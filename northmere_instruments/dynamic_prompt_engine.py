"""Northmere dynamic prompts: whitespace-tolerant, constraint-aware expansion.

Structural whitespace inside [[...]] directives and around choice alternatives is
ignored. Whitespace in ordinary literal text (including WHEN bodies) is preserved.
"""
from __future__ import annotations

import math
import random
import re
from collections import deque
from dataclasses import dataclass
from functools import lru_cache

MAX_CHOICES = 4096
MAX_DEPTH = 64
MAX_SEARCH_STATES = 200_000
MAX_DEFINITIONS = 256
MAX_REFERENCE_DEPTH = 32
MAX_EXPANDED_CHARS = 1_000_000
IDENT = r'[A-Za-z][A-Za-z0-9_]*'
DEF_IDENT = r'[A-Za-z][A-Za-z0-9_.]*'
TAG_RE = re.compile(r'\[\[\s*#\s*(' + IDENT + r')\s*\]\]')
WHEN_RE = re.compile(r'\[\[\s*WHEN\s+(' + IDENT + r')\s*\]\]')
WHEN_END_RE = re.compile(r'\[\[\s*/\s*WHEN\s*\]\]')
USE_RE = re.compile(r'(?<!\\)\[\[\s*USE\s+(' + DEF_IDENT + r')\s*\]\]')
DEF_START_RE = re.compile(r'\[\[\s*DEF\s+(' + DEF_IDENT + r')\s*\]\]')
DEF_END_RE = re.compile(r'\[\[\s*/\s*DEF\s*\]\]')
RULES_START_RE = re.compile(r'\[\[\s*RULES\s*\]\]')
RULES_END_RE = re.compile(r'\[\[\s*/\s*RULES\s*\]\]')
RULE_RE = re.compile(r'(' + IDENT + r')\s*->\s*(!?)\s*(' + IDENT + r')')
SOFT_RULE_RE = re.compile(r'(' + IDENT + r')\s*~>\s*(' + IDENT + r')\s*@\s*' +
                          r'((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)')
ESCAPABLE = frozenset(r'{}|\[]')


class PromptSyntaxError(ValueError):
    """Invalid markup, unknown references, unsatisfiable constraints or safety limit."""


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


def _directive(line: str, pattern: re.Pattern[str]) -> re.Match[str] | None:
    return pattern.fullmatch(line.strip())


def split_inline_rules(template: str) -> tuple[str, str]:
    """Accept indented RULES markers with optional internal whitespace and CRLF."""
    lines = template.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if _directive(line, RULES_START_RE)]
    ends = [i for i, line in enumerate(lines) if _directive(line, RULES_END_RE)]
    if not starts and not ends:
        # Catch malformed standalone directives instead of rendering them.
        if re.search(r'(?m)^\s*\[\[\s*/?\s*RULES\b', template):
            raise PromptSyntaxError('Malformed or unpaired [[RULES]] block')
        return template, ''
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise PromptSyntaxError('Expected one [[RULES]] / [[/RULES]] pair')
    a, b = starts[0], ends[0]
    if any(line.strip() for line in lines[b + 1:]):
        raise PromptSyntaxError('Rules block must be the final nonblank section')
    return ''.join(lines[:a]).rstrip('\r\n'), ''.join(lines[a + 1:b])


def expand_definitions(template: str) -> str:
    """Expand standalone definitions; referenced copies are parsed independently."""
    definitions: dict[str, str] = {}
    lines = template.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = _directive(lines[i], DEF_START_RE)
        if match:
            name = match.group(1)
            if name in definitions:
                raise PromptSyntaxError(f'Duplicate definition {name!r}')
            i += 1
            body: list[str] = []
            while i < len(lines) and not _directive(lines[i], DEF_END_RE):
                if _directive(lines[i], DEF_START_RE):
                    raise PromptSyntaxError('Nested definitions are not supported')
                body.append(lines[i])
                i += 1
            if i == len(lines):
                raise PromptSyntaxError(f'Unclosed [[DEF {name}]] block')
            definitions[name] = ''.join(body).rstrip('\r\n')
            if len(definitions) > MAX_DEFINITIONS:
                raise PromptSyntaxError('Too many definitions')
            i += 1
        else:
            # Detect malformed standalone definition constructs early.
            if re.match(r'^\s*\[\[\s*/?\s*DEF\b', lines[i]):
                raise PromptSyntaxError(f'Malformed or misplaced definition on line {i + 1}')
            out.append(lines[i])
            i += 1
    prompt = ''.join(out)
    if definitions:
        prompt = prompt.strip('\r\n')

    def resolve(value: str, stack: tuple[str, ...]) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in definitions:
                raise PromptSyntaxError(f'Unknown definition {name!r}')
            if name in stack:
                raise PromptSyntaxError('Recursive definition: ' + ' -> '.join((*stack, name)))
            if len(stack) >= MAX_REFERENCE_DEPTH:
                raise PromptSyntaxError('Definition reference depth exceeded')
            return resolve(definitions[name], (*stack, name))
        result = USE_RE.sub(replace, value)
        if re.search(r'(?<!\\)\[\[\s*USE\b', result):
            raise PromptSyntaxError('Malformed [[USE name]] reference')
        if len(result) > MAX_EXPANDED_CHARS:
            raise PromptSyntaxError('Expanded definitions exceed character limit')
        return result
    return resolve(prompt, ())


class Parser:
    def __init__(self, template: str) -> None:
        self.text = template
        self.pos = 0
        self.count = 0

    def parse(self) -> tuple[object, ...]:
        tree, stop = self._sequence(0, root=True)
        if stop is not None:
            raise PromptSyntaxError('Unexpected parser delimiter')
        return tree

    def _sequence(self, depth: int, *, root: bool = False) -> tuple[tuple[object, ...], str | None]:
        if depth > MAX_DEPTH:
            raise PromptSyntaxError(f'Brace nesting exceeds {MAX_DEPTH} levels')
        nodes: list[object] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                nodes.append(Literal(''.join(buf)))
                buf.clear()

        while self.pos < len(self.text):
            start = self.pos
            ch = self.text[start]
            if ch == '\\' and start + 1 < len(self.text) and self.text[start + 1] in ESCAPABLE:
                buf.append(self.text[start + 1])
                self.pos += 2
                continue
            if self.text.startswith('[[', start):
                tag = TAG_RE.match(self.text, start)
                opening = WHEN_RE.match(self.text, start)
                if tag:
                    flush()
                    nodes.append(Tag(tag.group(1)))
                    self.pos = tag.end()
                    continue
                if opening:
                    closing = WHEN_END_RE.search(self.text, opening.end())
                    if closing is None:
                        raise PromptSyntaxError(f'Unclosed [[WHEN]] at offset {start}')
                    content = self.text[opening.end():closing.start()]
                    # Contents are literal, never conditional syntax of their own.
                    if '[[' in content or '{' in content or '}' in content:
                        raise PromptSyntaxError('[[WHEN]] must contain plain text only')
                    flush()
                    nodes.append(When(opening.group(1), content))
                    self.pos = closing.end()
                    continue
                if re.match(r'\[\[\s*(?:#|/?\s*WHEN\b|USE\b|DEF\b|/?\s*RULES\b)', self.text[start:]):
                    raise PromptSyntaxError(f'Malformed or misplaced directive at offset {start}')
            if ch == '{':
                flush()
                self.pos += 1
                options: list[tuple[object, ...]] = []
                while True:
                    option, stop = self._sequence(depth + 1)
                    options.append(_trim_alternative(option))
                    if stop == '}':
                        break
                    if stop is None:
                        raise PromptSyntaxError(f"Unclosed '{{' at offset {start}")
                if len(options) < 2:
                    raise PromptSyntaxError(f'Choice at offset {start} needs a | separator')
                self.count += 1
                if self.count > MAX_CHOICES:
                    raise PromptSyntaxError('Too many choices')
                nodes.append(Choice(tuple(options)))
                continue
            if ch == '}':
                if root:
                    raise PromptSyntaxError(f"Unexpected '}}' at offset {start}")
                flush()
                self.pos += 1
                return tuple(nodes), '}'
            if ch == '|' and not root:
                flush()
                self.pos += 1
                return tuple(nodes), '|'
            buf.append(ch)
            self.pos += 1
        flush()
        return tuple(nodes), None


def _trim_alternative(nodes: tuple[object, ...]) -> tuple[object, ...]:
    """Trim branch-edge whitespace, including whitespace adjacent to leading tags.

    Tags emit no text; spaces after a tag at a branch edge are formatting,
    not an intentional leading space. Literal interior spacing is untouched.
    """
    items = list(nodes)
    for direction in (1, -1):
        indices = range(len(items)) if direction == 1 else range(len(items) - 1, -1, -1)
        for i in indices:
            node = items[i]
            if isinstance(node, Tag):
                continue
            if isinstance(node, Literal):
                trimmed = node.value.lstrip() if direction == 1 else node.value.rstrip()
                items[i] = Literal(trimmed)
                if not trimmed:
                    continue
            break
    return tuple(n for n in items if not (isinstance(n, Literal) and not n.value))


def parse_rules(text: str, known_tags: set[str]) -> tuple[Rule | SoftRule, ...]:
    rules: list[Rule | SoftRule] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        soft = SOFT_RULE_RE.fullmatch(line)
        if soft:
            trigger, target, weight_text = soft.groups()
            weight = float(weight_text)
            if not math.isfinite(weight) or weight <= 0 or trigger == target:
                raise PromptSyntaxError(f'Invalid soft weight on line {number}')
            rule: Rule | SoftRule = SoftRule(trigger, target, weight)
            if any(isinstance(x, SoftRule) and (x.trigger, x.target) == (trigger, target) for x in rules):
                raise PromptSyntaxError(f'Duplicate soft rule on line {number}')
        else:
            hard = RULE_RE.fullmatch(line)
            if hard is None:
                raise PromptSyntaxError(f'Invalid rule on line {number}: {line!r}')
            trigger, negation, target = hard.groups()
            if trigger == target and negation:
                raise PromptSyntaxError(f'Self-excluding rule on line {number}')
            rule = Rule(trigger, target, not bool(negation))
            if Rule(trigger, target, not rule.required) in rules:
                raise PromptSyntaxError(f'Contradictory rules on line {number}')
        if rule.trigger not in known_tags or rule.target not in known_tags:
            raise PromptSyntaxError(f'Unknown tag in rule on line {number}: {line!r}')
        if rule not in rules:
            rules.append(rule)
    return tuple(rules)


def _all_tags(nodes: tuple[object, ...]) -> frozenset[str]:
    result: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            result.add(node.name)
        elif isinstance(node, Choice):
            for alt in node.alternatives:
                result.update(_all_tags(alt))
    return frozenset(result)


@lru_cache(maxsize=32768)
def _bounds(nodes: tuple[object, ...]) -> tuple[frozenset[str], frozenset[str]]:
    required: set[str] = set()
    possible: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            required.add(node.name)
            possible.add(node.name)
        elif isinstance(node, Choice):
            subs = [_bounds(alt) for alt in node.alternatives]
            required.update(set.intersection(*(set(x[0]) for x in subs)))
            possible.update(set.union(*(set(x[1]) for x in subs)))
    return frozenset(required), frozenset(possible)


def _satisfiable(nodes: tuple[object, ...], rules: tuple[Rule, ...]) -> bool:
    forced, possible = _bounds(nodes)
    return all(rule.trigger not in forced or
               (rule.target in possible if rule.required else rule.target not in forced)
               for rule in rules)


def _soft_levels(rules: tuple[SoftRule, ...]) -> dict[str, int]:
    graph: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    for r in rules:
        graph.setdefault(r.trigger, set())
        indegree.setdefault(r.trigger, 0)
        indegree.setdefault(r.target, 0)
        if r.target not in graph[r.trigger]:
            graph[r.trigger].add(r.target)
            indegree[r.target] += 1
    queue = deque(k for k, v in indegree.items() if v == 0)
    order: list[str] = []
    while queue:
        name = queue.popleft()
        order.append(name)
        for target in sorted(graph.get(name, ())):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(indegree):
        raise PromptSyntaxError('Cyclic soft preferences are ambiguous')
    levels: dict[str, int] = {}
    for name in reversed(order):
        levels[name] = max((levels[t] + 1 for t in graph.get(name, ())), default=0)
    return levels


def _render(nodes: tuple[object, ...]) -> tuple[str, set[str]]:
    tags = {n.name for n in nodes if isinstance(n, Tag)}
    out: list[str] = []
    total = 0
    for node in nodes:
        if isinstance(node, Literal):
            part = node.value
        elif isinstance(node, When):
            part = node.text if node.tag in tags else ''
        elif isinstance(node, Tag):
            continue
        else:
            raise PromptSyntaxError('Unresolved choice during rendering')
        total += len(part)
        if total > MAX_EXPANDED_CHARS:
            raise PromptSyntaxError('Rendered prompt exceeds character limit')
        out.append(part)
    return ''.join(out), tags


def expand_prompt(template: str, seed: int = 0) -> str:
    """Expand and validate a seeded prompt, preserving all supported constructs."""
    if not isinstance(template, str):
        raise PromptSyntaxError('Prompt must be a string')
    prompt, rules_text = split_inline_rules(template)
    prompt = expand_definitions(prompt)
    if len(prompt) > MAX_EXPANDED_CHARS:
        raise PromptSyntaxError('Prompt exceeds character limit')
    tree = Parser(prompt).parse()
    known = _all_tags(tree)

    def check_whens(nodes: tuple[object, ...]) -> None:
        for n in nodes:
            if isinstance(n, When) and n.tag not in known:
                raise PromptSyntaxError(f'Unknown conditional tag {n.tag!r}')
            if isinstance(n, Choice):
                for alt in n.alternatives:
                    check_whens(alt)
    check_whens(tree)
    parsed = parse_rules(rules_text, set(known))
    hard = tuple(x for x in parsed if isinstance(x, Rule))
    soft = tuple(x for x in parsed if isinstance(x, SoftRule))
    levels = _soft_levels(soft)
    rng = random.Random(seed)
    drivers = {r.trigger for r in hard}
    soft_sources = {r.trigger for r in soft}
    targets = {r.target for r in parsed}

    if not parsed:
        nodes = tree
        while True:
            found = next(((i, n) for i, n in enumerate(nodes) if isinstance(n, Choice)), None)
            if found is None:
                return _render(nodes)[0]
            i, choice = found
            nodes = nodes[:i] + rng.choice(choice.alternatives) + nodes[i + 1:]

    stack = [tree]
    explored = 0
    while stack:
        explored += 1
        if explored > MAX_SEARCH_STATES:
            raise PromptSyntaxError(f'Constraint search exceeded {MAX_SEARCH_STATES} states')
        nodes = stack.pop()
        if not _satisfiable(nodes, hard):
            continue
        frontier = [(i, n) for i, n in enumerate(nodes) if isinstance(n, Choice)]
        if not frontier:
            output, tags = _render(nodes)
            if all(r.trigger not in tags or ((r.target in tags) == r.required) for r in hard):
                return output
            continue

        def priority(item: tuple[int, Choice]) -> tuple[int, int]:
            possible = _bounds((item[1],))[1]
            sources = possible & soft_sources
            if sources:
                return 3, max(levels[x] for x in sources)
            if possible & drivers:
                return 2, 0
            if possible & targets:
                return 1, 0
            return 0, 0

        i, choice = max(frontier, key=priority)
        candidates = [alt for alt in choice.alternatives if _satisfiable(nodes[:i] + alt + nodes[i + 1:], hard)]
        forced = _bounds(nodes)[0]
        active = [r for r in soft if r.trigger in forced and r.target not in forced]
        if active:
            def score(alt: tuple[object, ...]) -> float:
                guaranteed = _bounds(nodes[:i] + alt + nodes[i + 1:])[0]
                weight = sum(math.log(r.weight) for r in active if r.target in guaranteed)
                u = max(1e-15, min(1 - 1e-15, rng.random()))
                return -math.log(-math.log(u)) + weight
            candidates.sort(key=score, reverse=True)
        else:
            rng.shuffle(candidates)
        for alt in reversed(candidates):
            stack.append(nodes[:i] + alt + nodes[i + 1:])
    raise PromptSyntaxError('No rule-consistent expansion exists for this template')
