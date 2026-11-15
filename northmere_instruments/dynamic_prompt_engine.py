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
MAX_ATTEMPTS = 512
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


def _required_tags(nodes: tuple[object, ...]) -> set[str]:
    """Tags guaranteed by the selected nodes, not merely possible in subchoices."""
    required: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            required.add(node.name)
        elif isinstance(node, Choice):
            required.update(set.intersection(*(
                _required_tags(alternative) for alternative in node.alternatives
            )))
    return required


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
    """Resolve a template, preferring rule drivers before their dependent choices.

    The sampler retains equal-probability options when no rule applies. For
    constrained prompts it samples feasible branches; conditional results are
    not asserted to have identical global weights to unconstrained samples.
    """
    prompt_text, rules_text = split_inline_rules(template)
    tree = Parser(prompt_text).parse()
    known_tags = _all_tags(tree)
    rules = parse_rules(rules_text, known_tags)
    drivers = {rule.trigger for rule in rules}
    dependent = {rule.target for rule in rules}
    rng = random.Random(seed)

    for _attempt in range(MAX_ATTEMPTS):
        nodes = tree
        for _step in range(MAX_CHOICES + 1):
            frontier = _frontier(nodes)
            if not frontier:
                result, tags = _render(nodes)
                if not _violated(tags, rules):
                    return result
                break

            # Resolve possible triggers first even if their group occurs later in
            # the text, so 'blindfold -> !glasses' is not dominated by earlier
            # glasses choices. This also applies to trigger tags nested in options.
            def priority(entry: tuple[int, Choice]) -> int:
                possible = _all_tags((entry[1],))
                if possible & drivers:
                    return 2
                if possible & dependent:
                    return 1
                return 0

            index, choice = max(frontier, key=priority)
            current_tags = {node.name for node in nodes if isinstance(node, Tag)}
            options: list[tuple[object, ...]] = []
            for alternative in choice.alternatives:
                candidate = current_tags | _required_tags(alternative)
                # Reject only guaranteed conflicts. A tag in an unselected
                # nested option must not disqualify its entire parent.
                if any(
                    rule.trigger in current_tags and not rule.required and rule.target in candidate
                    for rule in rules
                ):
                    continue
                if any(
                    not rule.required and rule.trigger in candidate and rule.target in current_tags
                    for rule in rules
                ):
                    continue
                options.append(alternative)
            if not options:
                break
            selected = rng.choice(options)
            nodes = nodes[:index] + selected + nodes[index + 1:]
        else:
            raise PromptSyntaxError('Too many expansion steps')

    raise PromptSyntaxError(
        f'No rule-consistent expansion found after {MAX_ATTEMPTS} attempts. '
        'Check mutually exclusive or unreachable required tags.'
    )
