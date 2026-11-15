"""Seeded brace choices with explicit, one-way tag constraints.

No ComfyUI imports: the parser and sampler can be unit-tested independently.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
TAG_RE = re.compile(r"\[\[#([A-Za-z][A-Za-z0-9_]*)\]\]")
RULE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*->\s*(!?)([A-Za-z][A-Za-z0-9_]*)$")
MAX_CHOICES = 4096
MAX_DEPTH = 64
MAX_SEARCH_STATES = 100_000
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
class Choice:
    alternatives: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class Rule:
    trigger: str
    target: str
    required: bool

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

def parse_rules(text: str, known_tags: set[str]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = RULE_RE.fullmatch(line)
        if not match:
            raise PromptSyntaxError(
                f"Invalid rule on line {number}: {line!r}; "
                "use 'tag -> other' or 'tag -> !other'"
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

def _all_tags(nodes: tuple[object, ...]) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            found.add(node.name)
        elif isinstance(node, Choice):
            for alternative in node.alternatives:
                found.update(_all_tags(alternative))
    return found

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
    tags: set[str] = set()
    for node in nodes:
        if isinstance(node, Literal):
            text.append(node.value)
        elif isinstance(node, Tag):
            tags.add(node.name)
        else:
            raise AssertionError('Unresolved choice')
    return ''.join(text), tags

def expand_prompt(template: str, seed: int = 0) -> str:
    """Expand choices with a seeded, driver-first constrained search.

    With no rules, choices are uniform and retain their previous seeded order.
    With rules, alternatives are tried in random order, with backtracking when
    necessary. The resulting *valid outputs* are not globally uniform.
    """
    prompt_text, rules_text = split_inline_rules(template)
    tree = Parser(prompt_text).parse()
    rules = parse_rules(rules_text, _all_tags(tree))
    rng = random.Random(seed)

    if not rules:
        # Keep existing seed-to-output behavior for unconstrained prompts.
        nodes = tree
        while frontier := _frontier(nodes):
            index, choice = frontier[0]
            nodes = nodes[:index] + rng.choice(choice.alternatives) + nodes[index + 1:]
        return _render(nodes)[0]

    drivers = {rule.trigger for rule in rules}
    dependent = {rule.target for rule in rules}
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

        # Preserve the original preference for triggers over their dependents.
        def priority(entry: tuple[int, Choice]) -> int:
            possible = _tag_bounds((entry[1],), bounds_cache)[1]
            if possible & drivers:
                return 2
            if possible & dependent:
                return 1
            return 0

        index, choice = max(frontier, key=priority)
        options = list(choice.alternatives)
        rng.shuffle(options)
        # LIFO stack: the first shuffled alternative is explored first.
        for alternative in reversed(options):
            candidate = nodes[:index] + alternative + nodes[index + 1:]
            if _can_satisfy(candidate, rules, bounds_cache):
                stack.append(candidate)

    raise PromptSyntaxError('No rule-consistent expansion exists for this template.')
