"""Seeded brace choices, reusable text, and tag-guided scene selection.

No ComfyUI imports: the parser and sampler can be unit-tested independently.
"""

from __future__ import annotations

import math
import random
import re
from collections import deque
from dataclasses import dataclass

TAG_RE = re.compile(r"\[\[#([A-Za-z][A-Za-z0-9_]*)\]\]")
RULE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*->\s*(!?)([A-Za-z][A-Za-z0-9_]*)$")
SOFT_RULE_RE = re.compile(
    r'^([A-Za-z][A-Za-z0-9_]*)\s*~>\s*([A-Za-z][A-Za-z0-9_]*)\s*@\s*'
    r'((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)$'
)
DEF_RE = re.compile(
    r'(?m)^[ \t]*\[\[DEF (?P<name>[A-Za-z][A-Za-z0-9_.]*)\]\][ \t]*\r?\n'
    r'(?P<body>.*?)'
    r'^[ \t]*\[\[/DEF\]\][ \t]*(?:\r?\n|\Z)',
    re.DOTALL | re.MULTILINE,
)
USE_RE = re.compile(r'\[\[USE ([A-Za-z][A-Za-z0-9_.]*)\]\]')
WHEN_RE = re.compile(r'\[\[WHEN ([A-Za-z][A-Za-z0-9_]*)\]\]')
MAX_CHOICES = 4096
MAX_DEPTH = 64
MAX_SEARCH_STATES = 100_000
MAX_DEFINITIONS = 256
MAX_REFERENCE_DEPTH = 32
MAX_EXPANDED_CHARS = 1_000_000
RULES_START = '[[RULES]]'
RULES_END = '[[/RULES]]'
RULES_BLOCK_RE = re.compile(
    r'(?m)^[ \t]*\[\[RULES\]\][ \t]*\r?\n'
    r'(?P<rules>.*?)'
    r'^[ \t]*\[\[/RULES\]\][ \t]*(?:\r?\n|\Z)',
    re.DOTALL | re.MULTILINE,
)
class PromptSyntaxError(ValueError):
    """Invalid prompt markup or dependency rules."""


@dataclass(frozen=True)
class Literal:
    value: str


@dataclass(frozen=True)
class Tag:
    name: str


@dataclass(frozen=True)
class When:
    tag: str
    text: str


@dataclass(frozen=True)
class Choice:
    alternatives: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class Rule:
    trigger: str
    target: str
    required: bool


@dataclass(frozen=True)
class SoftRule:
    trigger: str
    target: str
    weight: float


class Parser:
    def __init__(self, template: str) -> None:
        self.text = template
        self.pos = 0
        self.count = 0

    def parse(self) -> tuple[object, ...]:
        output, delimiter = self._sequence(0, root=True)
        assert delimiter is None
        return output
    def _sequence(self, depth: int, *, root: bool = False) -> tuple[tuple[object, ...], str | None]:
        if depth > MAX_DEPTH:
            raise PromptSyntaxError(f"Brace nesting exceeds {MAX_DEPTH} levels")
        parts: list[object] = []
        buf: list[str] = []
        def flush() -> None:
            if buf:
                raw = ''.join(buf)
                cursor = 0
                for match in TAG_RE.finditer(raw):
                    if match.start() > cursor:
                        parts.append(Literal(raw[cursor:match.start()]))
                    parts.append(Tag(match.group(1)))
                    cursor = match.end()
                if cursor < len(raw):
                    parts.append(Literal(raw[cursor:]))
                buf.clear()
        while self.pos < len(self.text):
            if self.text.startswith('[[WHEN', self.pos):
                opening = WHEN_RE.match(self.text, self.pos)
                if opening is None:
                    raise PromptSyntaxError(f'Invalid [[WHEN tag]] at offset {self.pos}')
                ending = self.text.find('[[/WHEN]]', opening.end())
                if ending < 0:
                    raise PromptSyntaxError(f'Unclosed [[WHEN]] at offset {self.pos}')
                content = self.text[opening.end():ending]
                # Conditional text cannot introduce tags or further choices: it
                # is rendered only after all scene tags have been decided.
                if '[[' in content or '{' in content or '}' in content:
                    raise PromptSyntaxError('[[WHEN]] must contain plain text only')
                flush()
                parts.append(When(opening.group(1), content))
                self.pos = ending + len('[[/WHEN]]')
                continue
            if self.text.startswith('[[/WHEN', self.pos):
                raise PromptSyntaxError(f'Unexpected [[/WHEN]] at offset {self.pos}')
            char = self.text[self.pos]
            if char == '\\' and self.pos + 1 < len(self.text) and self.text[self.pos + 1] in r'{}|\\':
                buf.append(self.text[self.pos + 1])
                self.pos += 2
                continue
            if char == '{':
                flush()
                opening = self.pos
                self.pos += 1
                options: list[tuple[object, ...]] = []
                while True:
                    option, stop = self._sequence(depth + 1)
                    options.append(option)
                    if stop == '}':
                        break
                    if stop is None:
                        raise PromptSyntaxError(f"Unclosed '{{' at offset {opening}")
                if len(options) < 2:
                    raise PromptSyntaxError(f"Choice at offset {opening} needs a '|' separator")
                self.count += 1
                if self.count > MAX_CHOICES:
                    raise PromptSyntaxError(f"Too many choice groups (>{MAX_CHOICES})")
                parts.append(Choice(tuple(options)))
                continue
            if char == '}' or (char == '|' and not root):
                if root:
                    raise PromptSyntaxError(f"Unexpected '}}' at offset {self.pos}")
                flush()
                self.pos += 1
                return tuple(parts), char
            buf.append(char)
            self.pos += 1
        if not root:
            flush()
            return tuple(parts), None
        flush()
        return tuple(parts), None


def expand_definitions(template: str) -> str:
    """Expand explicitly used definitions before parsing choices or tag rules.

    Every USE inserts a fresh copy of its definition. Unused definitions are
    inert, including any tag markers they contain.
    """
    definitions: dict[str, str] = {}

    def collect(match: re.Match[str]) -> str:
        name = match.group('name')
        if name in definitions:
            raise PromptSyntaxError(f'Duplicate definition {name!r}')
        body = match.group('body')
        if '[[DEF' in body or '[[/DEF' in body:
            raise PromptSyntaxError('Nested definitions are not supported')
        definitions[name] = body.rstrip('\r\n')
        if len(definitions) > MAX_DEFINITIONS:
            raise PromptSyntaxError(f'Too many definitions (>{MAX_DEFINITIONS})')
        return ''

    prompt = DEF_RE.sub(collect, template)
    if '[[DEF' in prompt or '[[/DEF' in prompt:
        raise PromptSyntaxError('Definitions need standalone [[DEF name]] / [[/DEF]] blocks')
    if definitions:
        # Remove blank lines left at the edges by definition-only metadata.
        prompt = prompt.strip('\r\n')

    def resolve(text: str, stack: tuple[str, ...]) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in definitions:
                raise PromptSyntaxError(f'Unknown definition {name!r}')
            if name in stack:
                raise PromptSyntaxError(f'Recursive definition: {" -> ".join((*stack, name))}')
            if len(stack) >= MAX_REFERENCE_DEPTH:
                raise PromptSyntaxError(f'Reference depth exceeds {MAX_REFERENCE_DEPTH}')
            return resolve(definitions[name], (*stack, name))

        expanded = USE_RE.sub(replace, text)
        if '[[USE' in expanded:
            raise PromptSyntaxError('Invalid [[USE name]] reference')
        if len(expanded) > MAX_EXPANDED_CHARS:
            raise PromptSyntaxError(f'Expanded prompt exceeds {MAX_EXPANDED_CHARS} characters')
        return expanded

    return resolve(prompt, ())

def split_inline_rules(template: str) -> tuple[str, str]:
    """Read one optional rules block at the very end of the template.

    The block is metadata, never rendered as part of the expanded prompt.
    """
    starts = template.count(RULES_START)
    ends = template.count(RULES_END)
    if starts == ends == 0:
        return template, ''
    if starts != 1 or ends != 1:
        raise PromptSyntaxError('Expected exactly one [[RULES]] / [[/RULES]] pair')
    match = RULES_BLOCK_RE.search(template)
    if not match or template[match.end():].strip():
        raise PromptSyntaxError(
            'Rules must form a standalone [[RULES]] ... [[/RULES]] block at the end of the template'
        )
    # The final newline separates the content from the metadata marker.
    prompt = template[:match.start()].rstrip('\r\n')
    return prompt, match.group('rules')

def parse_rules(text: str, known_tags: set[str]) -> tuple[Rule | SoftRule, ...]:
    rules: list[Rule | SoftRule] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        soft = SOFT_RULE_RE.fullmatch(line)
        if soft:
            trigger, target, number_text = soft.groups()
            if trigger not in known_tags or target not in known_tags:
                raise PromptSyntaxError(f'Unknown tag in soft rule on line {number}: {line!r}')
            weight = float(number_text)
            if not math.isfinite(weight) or weight <= 0 or trigger == target:
                raise PromptSyntaxError(f'Invalid soft rule on line {number}: {line!r}')
            if any(isinstance(rule, SoftRule) and (rule.trigger, rule.target) == (trigger, target)
                   for rule in rules):
                raise PromptSyntaxError(f'Duplicate soft rule on line {number}: {line!r}')
            rules.append(SoftRule(trigger, target, weight))
            continue
        match = RULE_RE.fullmatch(line)
        if not match:
            raise PromptSyntaxError(
                f"Invalid rule on line {number}: {line!r}; "
                "use 'tag -> other', 'tag -> !other', or 'tag ~> other @ 3'"
            )
        trigger, negation, target = match.groups()
        if trigger not in known_tags:
            raise PromptSyntaxError(f"Unknown trigger tag {trigger!r} on line {number}")
        if target not in known_tags:
            raise PromptSyntaxError(f"Unknown target tag {target!r} on line {number}")
        if trigger == target and negation:
            raise PromptSyntaxError(f"Self-excluding rule on line {number}: {line!r}")
        rule = Rule(trigger, target, not bool(negation))
        if Rule(trigger, target, not rule.required) in rules:
            raise PromptSyntaxError(f"Contradictory rules for {trigger!r} and {target!r}")
        if rule not in rules:
            rules.append(rule)
    return tuple(rules)


def _soft_levels(rules: tuple[SoftRule, ...]) -> dict[str, int]:
    """Visit soft-rule sources before their targets, independently of text order."""
    graph: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    for rule in rules:
        graph.setdefault(rule.trigger, set())
        indegree.setdefault(rule.trigger, 0)
        indegree.setdefault(rule.target, 0)
        if rule.target not in graph[rule.trigger]:
            graph[rule.trigger].add(rule.target)
            indegree[rule.target] += 1
    queue = deque(tag for tag, degree in indegree.items() if degree == 0)
    order = []
    while queue:
        tag = queue.popleft()
        order.append(tag)
        for target in graph.get(tag, ()):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(indegree):
        raise PromptSyntaxError('Cyclic soft preferences are ambiguous')
    levels: dict[str, int] = {}
    for tag in reversed(order):
        levels[tag] = max((levels[target] + 1 for target in graph.get(tag, ())), default=0)
    return levels

def _all_tags(nodes: tuple[object, ...]) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            found.add(node.name)
        elif isinstance(node, Choice):
            for alternative in node.alternatives:
                found.update(_all_tags(alternative))
    return found


def _walk_nodes(nodes: tuple[object, ...]):
    for node in nodes:
        yield node
        if isinstance(node, Choice):
            for option in node.alternatives:
                yield from _walk_nodes(option)

def _tag_bounds(
    nodes: tuple[object, ...], cache: dict[int, tuple[set[str], set[str]]]
) -> tuple[set[str], set[str]]:
    """Return guaranteed and possible tags, including unresolved nested choices.

    Cache subtree bounds by Choice identity; choices are immutable during expansion.
    """
    required: set[str] = set()
    possible: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            required.add(node.name)
            possible.add(node.name)
        elif isinstance(node, Choice):
            key = id(node)
            if key not in cache:
                alternatives = [_tag_bounds(option, cache) for option in node.alternatives]
                forced = set.intersection(*(bounds[0] for bounds in alternatives))
                available = set.union(*(bounds[1] for bounds in alternatives))
                cache[key] = forced, available
            forced, available = cache[key]
            required.update(forced)
            possible.update(available)
    return required, possible


def _can_satisfy(
    nodes: tuple[object, ...], rules: tuple[Rule, ...],
    cache: dict[int, tuple[set[str], set[str]]],
) -> bool:
    """Prune only when a rule is already impossible to satisfy."""
    required, possible = _tag_bounds(nodes, cache)
    return all(
        rule.trigger not in required or (
            rule.target in possible if rule.required else rule.target not in required
        )
        for rule in rules
    )


def _frontier(nodes: tuple[object, ...]) -> list[tuple[int, Choice]]:
    return [(index, node) for index, node in enumerate(nodes) if isinstance(node, Choice)]


def _violated(tags: set[str], rules: tuple[Rule, ...]) -> bool:
    return any(
        rule.trigger in tags and (rule.target in tags) != rule.required
        for rule in rules
    )

def _render(nodes: tuple[object, ...]) -> tuple[str, set[str]]:
    text: list[str] = []
    tags = {node.name for node in nodes if isinstance(node, Tag)}
    for node in nodes:
        if isinstance(node, Literal):
            text.append(node.value)
        elif isinstance(node, When):
            if node.tag in tags:
                text.append(node.text)
        elif isinstance(node, Tag):
            pass
        else:
            raise AssertionError('Unresolved choice')
    return ''.join(text), tags

def expand_prompt(template: str, seed: int = 0) -> str:
    """Expand choices with a seeded, driver-first constrained search.

    With no rules, choices are uniform. Hard rules reject invalid branches;
    soft rules multiply local odds of eligible downstream choices. Valid
    outputs are not guaranteed to be globally uniform.
    """
    prompt_text, rules_text = split_inline_rules(template)
    prompt_text = expand_definitions(prompt_text)
    tree = Parser(prompt_text).parse()
    known_tags = _all_tags(tree)
    for node in _walk_nodes(tree):
        if isinstance(node, When) and node.tag not in known_tags:
            raise PromptSyntaxError(f'Unknown conditional tag {node.tag!r}')
    parsed_rules = parse_rules(rules_text, known_tags)
    rules = tuple(rule for rule in parsed_rules if isinstance(rule, Rule))
    soft_rules = tuple(rule for rule in parsed_rules if isinstance(rule, SoftRule))
    soft_levels = _soft_levels(soft_rules)
    rng = random.Random(seed)

    if not rules and not soft_rules:
        # Keep existing seed-to-output behavior for unconstrained prompts.
        nodes = tree
        while frontier := _frontier(nodes):
            index, choice = frontier[0]
            nodes = nodes[:index] + rng.choice(choice.alternatives) + nodes[index + 1:]
        return _render(nodes)[0]

    drivers = {rule.trigger for rule in rules}
    dependent = {rule.target for rule in parsed_rules}
    soft_sources = {rule.trigger for rule in soft_rules}
    bounds_cache: dict[int, tuple[set[str], set[str]]] = {}
    stack = [tree]
    explored = 0

    while stack:
        if explored >= MAX_SEARCH_STATES:
            raise PromptSyntaxError(
                f'Constraint search limit ({MAX_SEARCH_STATES} states) exceeded; '
                'simplify the rules or reduce the number of choices.'
            )
        nodes = stack.pop()
        explored += 1
        if not _can_satisfy(nodes, rules, bounds_cache):
            continue

        frontier = _frontier(nodes)
        if not frontier:
            result, tags = _render(nodes)
            if not _violated(tags, rules):
                return result
            continue

        # Soft sources are selected before targets, even if they appear later
        # in the text; hard-rule drivers retain priority over other choices.
        def priority(entry: tuple[int, Choice]) -> tuple[int, int]:
            possible = _tag_bounds((entry[1],), bounds_cache)[1]
            sources = possible & soft_sources
            if sources:
                return 3, max(soft_levels[tag] for tag in sources)
            if possible & drivers:
                return 2, 0
            if possible & dependent:
                return 1, 0
            return 0, 0

        index, choice = max(frontier, key=priority)
        if not soft_rules:
            # Preserve existing hard-only choice order and seed behavior.
            options = list(choice.alternatives)
            rng.shuffle(options)
        else:
            forced = _tag_bounds(nodes, bounds_cache)[0]
            candidates = [option for option in choice.alternatives if _can_satisfy(
                nodes[:index] + option + nodes[index + 1:], rules, bounds_cache
            )]
            active = [rule for rule in soft_rules if rule.trigger in forced and rule.target not in forced]
            if active:
                # Gumbel top-k samples a weighted ordering without replacement;
                # log-weights avoid overflow when preferences are multiplied.
                def score(option: tuple[object, ...]) -> float:
                    candidate = nodes[:index] + option + nodes[index + 1:]
                    guaranteed = _tag_bounds(candidate, bounds_cache)[0]
                    log_weight = sum(math.log(rule.weight) for rule in active if rule.target in guaranteed)
                    u = min(1 - 1e-15, max(1e-15, rng.random()))
                    return -math.log(-math.log(u)) + log_weight

                options = sorted(candidates, key=score, reverse=True)
            else:
                options = candidates
                rng.shuffle(options)
        # LIFO stack: the first shuffled alternative is explored first.
        for alternative in reversed(options):
            candidate = nodes[:index] + alternative + nodes[index + 1:]
            if not soft_rules and not _can_satisfy(candidate, rules, bounds_cache):
                continue
            stack.append(candidate)

    raise PromptSyntaxError('No rule-consistent expansion exists for this template.')
