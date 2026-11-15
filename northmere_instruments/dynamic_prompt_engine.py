"""Deterministic dynamic prompt expansion with nested choices and tag constraints.

Grammar:
    prompt    := (literal | tag | choice)* [rules_block]
    choice    := '{' prompt ('|' prompt)+ '}'
    tag       := '[[#' identifier ']]'
    rule      := identifier '->' ['!'] identifier

Backslash escapes syntax characters (braces, pipes, brackets, and backslash).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from functools import lru_cache

MAX_CHOICES = 4096
MAX_DEPTH = 64
MAX_SEARCH_STATES = 200_000
IDENT = r"[A-Za-z][A-Za-z0-9_]*"
TAG_RE = re.compile(rf"\[\[#({IDENT})\]\]")
RULE_RE = re.compile(rf"({IDENT})\s*->\s*(!?)({IDENT})")
RULES_START = "[[RULES]]"
RULES_END = "[[/RULES]]"
_ESCAPABLE = frozenset(r"{}|\[]")


class PromptSyntaxError(ValueError):
    """Invalid prompt grammar, rules, or an impossible expansion."""


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
        result, stop = self._sequence(0, root=True)
        assert stop is None
        return result

    def _sequence(self, depth: int, *, root: bool = False) -> tuple[tuple[object, ...], str | None]:
        if depth > MAX_DEPTH:
            raise PromptSyntaxError(f"Brace nesting exceeds {MAX_DEPTH} levels at offset {self.pos}")
        nodes: list[object] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                nodes.append(Literal("".join(buf)))
                buf.clear()

        while self.pos < len(self.text):
            start = self.pos
            ch = self.text[start]
            if ch == "\\" and start + 1 < len(self.text) and self.text[start + 1] in _ESCAPABLE:
                buf.append(self.text[start + 1])
                self.pos += 2
                continue
            if ch == "\\" and start + 1 == len(self.text):
                buf.append(ch)
                self.pos += 1
                continue
            if self.text.startswith("[[#", start):
                match = TAG_RE.match(self.text, start)
                if not match:
                    raise PromptSyntaxError(f"Malformed tag at offset {start}; expected [[#name]]")
                flush()
                nodes.append(Tag(match.group(1)))
                self.pos = match.end()
                continue
            if ch == "{":
                flush()
                self.pos += 1
                alternatives: list[tuple[object, ...]] = []
                while True:
                    alternative, stop = self._sequence(depth + 1)
                    alternatives.append(alternative)
                    if stop == "}":
                        break
                    if stop is None:
                        raise PromptSyntaxError(f"Unclosed '{{' at offset {start}")
                if len(alternatives) < 2:
                    raise PromptSyntaxError(f"Choice at offset {start} needs a '|' separator")
                self.count += 1
                if self.count > MAX_CHOICES:
                    raise PromptSyntaxError(f"Too many choice groups (>{MAX_CHOICES})")
                nodes.append(Choice(tuple(alternatives)))
                continue
            if ch == "}":
                if root:
                    raise PromptSyntaxError(f"Unexpected '}}' at offset {start}")
                flush()
                self.pos += 1
                return tuple(nodes), ch
            if ch == "|" and not root:
                flush()
                self.pos += 1
                return tuple(nodes), ch
            buf.append(ch)
            self.pos += 1
        flush()
        return tuple(nodes), None


def split_inline_rules(template: str) -> tuple[str, str]:
    """Extract a standalone, final rules block (with CRLF support)."""
    lines = template.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == RULES_START]
    ends = [i for i, line in enumerate(lines) if line.strip() == RULES_END]
    if not starts and not ends:
        return template, ""
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise PromptSyntaxError("Expected exactly one [[RULES]] / [[/RULES]] pair")
    a, b = starts[0], ends[0]
    if any(line.strip() for line in lines[b + 1:]):
        raise PromptSyntaxError("Rules block must be at the end of the template")
    prompt = "".join(lines[:a]).rstrip("\r\n")
    rules = "".join(lines[a + 1:b])
    return prompt, rules


def parse_rules(text: str, known_tags: set[str]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = RULE_RE.fullmatch(line)
        if not match:
            raise PromptSyntaxError(f"Invalid rule on line {number}: {line!r}; use 'a -> b' or 'a -> !b'")
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


@lru_cache(maxsize=32_768)
def _all_tags(nodes: tuple[object, ...]) -> frozenset[str]:
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            found.add(node.name)
        elif isinstance(node, Choice):
            for alternative in node.alternatives:
                found.update(_all_tags(alternative))
    return frozenset(found)


@lru_cache(maxsize=32_768)
def _required_tags(nodes: tuple[object, ...]) -> frozenset[str]:
    required: set[str] = set()
    for node in nodes:
        if isinstance(node, Tag):
            required.add(node.name)
        elif isinstance(node, Choice):
            required.update(set.intersection(*[set(_required_tags(alt)) for alt in node.alternatives]))
    return frozenset(required)


def _impossible(nodes: tuple[object, ...], rules: tuple[Rule, ...]) -> bool:
    guaranteed = _required_tags(nodes)
    possible = _all_tags(nodes)
    for rule in rules:
        if rule.trigger not in possible:
            continue
        if rule.required and rule.trigger in guaranteed and rule.target not in possible:
            return True
        if not rule.required and rule.trigger in guaranteed and rule.target in guaranteed:
            return True
    return False


def _render(nodes: tuple[object, ...]) -> str:
    return "".join(node.value for node in nodes if isinstance(node, Literal))


def _choose_frontier(nodes: tuple[object, ...], drivers: frozenset[str], targets: frozenset[str]) -> int:
    # Trigger choices first; constraint propagation then reduces dependent branches.
    best_index = -1
    best_score = -1
    for i, node in enumerate(nodes):
        if isinstance(node, Choice):
            tags = _all_tags((node,))
            score = 2 if tags & drivers else (1 if tags & targets else 0)
            if score > best_score:
                best_index, best_score = i, score
    return best_index


def expand_prompt(template: str, seed: int = 0) -> str:
    """Expand a prompt deterministically, searching feasible branches without random retries.

    Without constraints, each choice is sampled uniformly. With constraints, branches
    are visited in seeded random order and pruned when provably impossible.
    """
    prompt_text, rules_text = split_inline_rules(template)
    tree = Parser(prompt_text).parse()
    known_tags = set(_all_tags(tree))
    rules = parse_rules(rules_text, known_tags)
    rng = random.Random(seed)
    drivers = frozenset(rule.trigger for rule in rules)
    targets = frozenset(rule.target for rule in rules)

    if not rules:
        # Linear iterative expansion; avoids recursion-limit failures on wide prompts.
        nodes = tree
        while True:
            index = _choose_frontier(nodes, drivers, targets)
            if index < 0:
                return _render(nodes)
            chosen = rng.choice(nodes[index].alternatives)
            nodes = nodes[:index] + chosen + nodes[index + 1:]

    stack = [tree]
    states = 0
    while stack:
        nodes = stack.pop()
        states += 1
        if states > MAX_SEARCH_STATES:
            raise PromptSyntaxError(
                f"Constraint search exceeded {MAX_SEARCH_STATES} states; simplify the rules or choices"
            )
        if _impossible(nodes, rules):
            continue
        index = _choose_frontier(nodes, drivers, targets)
        if index < 0:
            return _render(nodes)
        alternatives = list(nodes[index].alternatives)
        rng.shuffle(alternatives)
        for alt in reversed(alternatives):
            stack.append(nodes[:index] + alt + nodes[index + 1:])
    raise PromptSyntaxError("No rule-consistent expansion exists; check contradictory or unreachable tags")
