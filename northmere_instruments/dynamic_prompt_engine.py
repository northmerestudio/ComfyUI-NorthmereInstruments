"""Northmere Prompt v3. Deliberately incompatible with the old [[...]] DSL.

Grammar overview:
    {red|blue}                   unlabelled choice
    {3::red|1::blue}             positive relative alternative weights
    @pick{day: sunny|_: neutral|night, outdoor: moonlit}  facts; _ has none
    @if(day){bright light}       conditional rendering (no new facts)
    @use(name)                   fresh macro expansion
    @define name / @end         line-oriented reusable text
    @rules / @end               line-oriented constraints and preferences

Rule forms: `day -> night`, `day -> !night`, `day ~> sun @ 3`.
Small constrained prompts sample valid complete selections exactly. Large
templates use bounded driver-first search: hard rules are enforced but soft
preference probabilities are approximate.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass

MAX_DEPTH = 64
MAX_NODES = 8192
MAX_DEFINITIONS = 256
MAX_REFERENCE_DEPTH = 32
MAX_EXPANDED_CHARS = 1_000_000
MAX_VALID_STATES = 200_000
NAME = r'[A-Za-z][A-Za-z0-9_.-]*'
NAME_RE = re.compile(NAME + r'\Z')
BLOCK_START = re.compile(r'^\s*@(define|rules)\b(.*)$')
END_BLOCK = re.compile(r'^\s*@end\s*$')
USE = re.compile(r'@use\s*\(\s*(' + NAME + r')\s*\)')
HARD = re.compile(r'(' + NAME + r')\s*->\s*(!?)\s*(' + NAME + r')\Z')
SOFT = re.compile(r'(' + NAME + r')\s*~>\s*(' + NAME + r')\s*@\s*([0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:\s*)\Z')
WEIGHT = re.compile(r'\s*([0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*::')
LABEL = re.compile(r'\s*(' + NAME + r'(?:\s*,\s*' + NAME + r')*|_)\s*:')
ESCAPE = frozenset('\\{}|@:')

class PromptSyntaxError(ValueError):
    """Syntax, semantic or bounded-execution error in a Northmere prompt."""

@dataclass(frozen=True)
class Text:
    value: str

@dataclass(frozen=True)
class Choice:
    options: tuple['Option', ...]

@dataclass(frozen=True)
class Option:
    nodes: tuple[object, ...]
    weight: float = 1.0
    facts: tuple[str, ...] = ()

@dataclass(frozen=True)
class If:
    fact: str
    nodes: tuple[object, ...]

@dataclass(frozen=True)
class Rule:
    source: str
    target: str
    positive: bool

@dataclass(frozen=True)
class Preference:
    source: str
    target: str
    factor: float


def _extract(template: str) -> tuple[str, dict[str, str], str]:
    """Remove line-oriented blocks with strict nesting and end checks."""
    definitions: dict[str, str] = {}
    rule_lines: list[str] = []
    output: list[str] = []
    lines = template.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        match = BLOCK_START.fullmatch(lines[i].rstrip('\r\n'))
        if not match:
            if END_BLOCK.fullmatch(lines[i].rstrip('\r\n')):
                raise PromptSyntaxError(f'Unexpected @end on line {i+1}')
            output.append(lines[i]); i += 1; continue
        kind, arg = match.groups()
        name = arg.strip()
        if kind == 'define' and not NAME_RE.fullmatch(name):
            raise PromptSyntaxError(f'Invalid definition name on line {i+1}')
        if kind == 'rules' and name:
            raise PromptSyntaxError(f'@rules takes no arguments (line {i+1})')
        if kind == 'rules' and rule_lines:
            raise PromptSyntaxError('Only one @rules block is supported')
        i += 1
        body: list[str] = []
        while i < len(lines) and not END_BLOCK.fullmatch(lines[i].rstrip('\r\n')):
            if BLOCK_START.fullmatch(lines[i].rstrip('\r\n')):
                raise PromptSyntaxError('Nested @define/@rules blocks are not allowed')
            body.append(lines[i]); i += 1
        if i == len(lines):
            raise PromptSyntaxError(f'Unclosed @{kind} block')
        i += 1
        if kind == 'define':
            if name in definitions: raise PromptSyntaxError(f'Duplicate definition: {name}')
            definitions[name] = ''.join(body).strip('\r\n')
            if len(definitions) > MAX_DEFINITIONS: raise PromptSyntaxError('Too many definitions')
        else:
            if rule_lines: raise PromptSyntaxError('Only one @rules block is supported')
            rule_lines = body or ['\n']
    return ''.join(output).strip('\r\n'), definitions, ''.join(rule_lines)


def _resolve(text: str, definitions: dict[str, str], stack: tuple[str, ...] = ()) -> str:
    # A scanner ensures escaped @use() remains literal; no regex replacement on escaped text.
    out: list[str] = []
    i = 0; size = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i+1] == '@':
            piece = text[i:i+2]; i += 2
        elif text.startswith('@use', i):
            m = USE.match(text, i)
            if not m: raise PromptSyntaxError(f'Malformed @use at offset {i}')
            name = m.group(1)
            if name not in definitions: raise PromptSyntaxError(f'Unknown definition: {name}')
            if name in stack: raise PromptSyntaxError('Recursive definition: ' + ' -> '.join((*stack, name)))
            if len(stack) >= MAX_REFERENCE_DEPTH: raise PromptSyntaxError('Definition reference depth exceeded')
            piece = _resolve(definitions[name], definitions, (*stack, name))
            i = m.end()
        else:
            piece = text[i]; i += 1
        out.append(piece); size += len(piece)
        if size > MAX_EXPANDED_CHARS: raise PromptSyntaxError('Expanded prompt exceeds size limit')
    return ''.join(out)


class Parser:
    def __init__(self, text: str):
        self.text = text
        self.index = 0
        self.node_count = 0

    def parse(self) -> tuple[object, ...]:
        nodes, closing = self.sequence(0, None)
        if closing is not None: raise PromptSyntaxError('Unexpected delimiter')
        return nodes

    def sequence(self, depth: int, context: str | None) -> tuple[tuple[object, ...], str | None]:
        if depth > MAX_DEPTH: raise PromptSyntaxError('Maximum nesting depth exceeded')
        nodes: list[object] = []
        buf: list[str] = []
        def flush():
            if buf:
                nodes.append(Text(''.join(buf))); buf.clear()
        while self.index < len(self.text):
            c = self.text[self.index]
            if c == '\\' and self.index + 1 < len(self.text) and self.text[self.index+1] in ESCAPE:
                buf.append(self.text[self.index+1]); self.index += 2; continue
            if c == '@':
                if self.text.startswith('@pick', self.index):
                    m = re.match(r'@pick\s*\{', self.text[self.index:])
                    if not m: raise PromptSyntaxError(f'Malformed @pick at offset {self.index}')
                    flush(); self.index += m.end()
                    nodes.append(self.choice(depth+1, labelled=True)); continue
                if self.text.startswith('@if', self.index):
                    m = re.match(r'@if\s*\(\s*(' + NAME + r')\s*\)\s*\{', self.text[self.index:])
                    if not m: raise PromptSyntaxError(f'Malformed @if at offset {self.index}')
                    flush(); self.index += m.end()
                    body, stop = self.sequence(depth+1, 'if')
                    if stop != '}': raise PromptSyntaxError('Unclosed @if body')
                    nodes.append(If(m.group(1), body)); continue
                if re.match(r'@(define|rules|end|use)\b', self.text[self.index:]):
                    raise PromptSyntaxError(f'Misplaced directive at offset {self.index}')
            if c == '{':
                flush(); self.index += 1; nodes.append(self.choice(depth+1, labelled=False)); continue
            if c == '}':
                if context is None: raise PromptSyntaxError(f'Unexpected }} at offset {self.index}')
                flush(); self.index += 1; return tuple(nodes), '}'
            if c == '|' and context in ('choice', 'pick'):
                flush(); self.index += 1; return tuple(nodes), '|'
            buf.append(c); self.index += 1
        flush()
        return tuple(nodes), None

    def choice(self, depth: int, labelled: bool) -> Choice:
        options = []
        while True:
            facts = ()
            if labelled:
                m = LABEL.match(self.text, self.index)
                if m is None: raise PromptSyntaxError(f'Expected named alternative in @pick near offset {self.index}')
                facts = () if m.group(1) == '_' else tuple(x.strip() for x in m.group(1).split(','))
                if len(facts) != len(set(facts)):
                    raise PromptSyntaxError('Duplicate labels in alternative')
                self.index = m.end()
            m = WEIGHT.match(self.text, self.index)
            weight = float(m.group(1)) if m else 1.0
            if m: self.index = m.end()
            if not math.isfinite(weight) or weight <= 0:
                raise PromptSyntaxError('Choice weight must be positive and finite')
            body, stop = self.sequence(depth, 'pick' if labelled else 'choice')
            body = _trim(body)
            options.append(Option(body, weight, facts))
            if stop == '}': break
            if stop is None: raise PromptSyntaxError('Unclosed choice')
        if len(options) < 2: raise PromptSyntaxError('Choices require at least two alternatives')
        self.node_count += 1
        if self.node_count > MAX_NODES: raise PromptSyntaxError('Too many choices')
        return Choice(tuple(options))


def _trim(nodes: tuple[object, ...]) -> tuple[object, ...]:
    items = list(nodes)
    if items and isinstance(items[0], Text): items[0] = Text(items[0].value.lstrip())
    if items and isinstance(items[-1], Text): items[-1] = Text(items[-1].value.rstrip())
    return tuple(x for x in items if not (isinstance(x, Text) and not x.value))


def _facts(nodes: tuple[object, ...]) -> set[str]:
    result = set()
    for node in nodes:
        if isinstance(node, Choice):
            for opt in node.options:
                result.update(opt.facts)
                result.update(_facts(opt.nodes))
        if isinstance(node, If):
            if _facts(node.nodes):
                raise PromptSyntaxError('Labels inside @if are forbidden (conditional facts would be ambiguous)')
    return result


def _rules(text: str, facts: set[str]) -> tuple[tuple[Rule, ...], tuple[Preference, ...]]:
    hard = []; soft = []
    for index, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'): continue
        m = HARD.fullmatch(line)
        if m:
            a, neg, b = m.groups()
            rule = Rule(a, b, not bool(neg))
            if a == b and neg: raise PromptSyntaxError(f'Self-excluding rule at line {index}')
            if Rule(a, b, not rule.positive) in hard: raise PromptSyntaxError('Contradictory hard rules')
            if rule not in hard: hard.append(rule)
        else:
            m = SOFT.fullmatch(line)
            if not m: raise PromptSyntaxError(f'Invalid rule on @rules line {index}: {line}')
            a, b, value = m.groups()
            factor = float(value)
            if not math.isfinite(factor) or factor <= 0: raise PromptSyntaxError('Invalid soft weight')
            rule = Preference(a, b, factor)
            if rule in soft: raise PromptSyntaxError('Duplicate preference')
            soft.append(rule)
        if a not in facts or b not in facts:
            raise PromptSyntaxError(f'Unknown label in rule on line {index}: {a} / {b}')
    return tuple(hard), tuple(soft)


def _validate_conditions(nodes: tuple[object, ...], facts: set[str]) -> None:
    for node in nodes:
        if isinstance(node, If):
            if node.fact not in facts: raise PromptSyntaxError(f'Unknown label in @if: {node.fact}')
            _validate_conditions(node.nodes, facts)
        elif isinstance(node, Choice):
            for opt in node.options: _validate_conditions(opt.nodes, facts)


def _contains_fact(nodes: tuple[object, ...]) -> bool:
    for node in nodes:
        if isinstance(node, Choice):
            if any(option.facts or _contains_fact(option.nodes) for option in node.options):
                return True
        elif isinstance(node, If) and _contains_fact(node.nodes):
            return True
    return False


def _render(nodes: tuple[object, ...], facts: set[str], rng: random.Random) -> str:
    out = []
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.value)
        elif isinstance(node, If):
            out.append(_render(node.nodes, facts, rng) if node.fact in facts else '\u0000')
        elif isinstance(node, Choice):
            if _contains_fact((node,)):
                raise AssertionError('Unresolved labeled choice')
            choice = rng.choices(node.options, weights=[o.weight for o in node.options], k=1)[0]
            out.append(_render(choice.nodes, facts, rng))
        else:
            raise AssertionError('Unknown AST node')
    text = ''.join(out)
    # Remove gaps caused only by suppressed conditionals; literal spaces and
    # line breaks elsewhere remain untouched.
    return re.sub(r'[ \t]*\x00[ \t]*', lambda m: ' ' if m.group().startswith((' ', '\t')) and m.group().endswith((' ', '\t')) else '', text)


def expand_prompt(template: str, seed: int = 0) -> str:
    if not isinstance(template, str): raise PromptSyntaxError('Prompt must be a string')
    if '[[' in template or ']]' in template:
        raise PromptSyntaxError('Legacy [[...]] syntax is removed; use @pick, @if, @define and @rules')
    source, definitions, rules_text = _extract(template)
    source = _resolve(source, definitions)
    if re.search(r'(?<!\\)__(?:[A-Za-z0-9_./-]+)__', source):
        raise PromptSyntaxError('File-backed __wildcards__ are not supported; expand them upstream in a wildcard node')
    tree = Parser(source).parse()
    facts = _facts(tree)
    _validate_conditions(tree, facts)
    hard, preferences = _rules(rules_text, facts)
    rng = random.Random(seed)

    def valid(selected: set[str]) -> bool:
        return all(r.source not in selected or ((r.target in selected) == r.positive) for r in hard)

    # Without constraints, choose in linear time, including nested alternatives.
    if not hard and not preferences:
        def choose(nodes):
            output = []
            selected = set()
            for n in nodes:
                if isinstance(n, Choice):
                    option = rng.choices(n.options, weights=[o.weight for o in n.options], k=1)[0]
                    branch, branch_facts = choose(option.nodes)
                    output.extend(branch)
                    selected.update(branch_facts)
                    selected.update(option.facts)
                else: output.append(n)
            return tuple(output), selected
        resolved, selected = choose(tree)
        rendered = _render(resolved, selected, rng).strip()
        if len(rendered) > MAX_EXPANDED_CHARS: raise PromptSyntaxError('Rendered prompt exceeds size limit')
        return rendered

    # Exact globally weighted sample over all complete valid selections using
    # weighted reservoir sampling (streaming, no storage of exponentially many strings).
    total = 0.0
    selected_result = None
    explored = 0

    def walk(nodes, selected, product):
        nonlocal total, selected_result, explored
        first = next((i for i, n in enumerate(nodes) if isinstance(n, Choice) and _contains_fact((n,))), None)
        if first is not None:
            node = nodes[first]
            option_total = sum(o.weight for o in node.options)
            for option in node.options:
                next_selected = selected | set(option.facts)
                # Keep nested choices in their original slot.
                walk(nodes[:first] + option.nodes + nodes[first+1:], next_selected, product * (option.weight / option_total))
            return
        explored += 1
        if explored > MAX_VALID_STATES:
            raise PromptSyntaxError(f'Exact constraint sampling limit exceeded ({MAX_VALID_STATES} selections)')
        if not valid(selected): return
        factor = product
        for pref in preferences:
            if pref.source in selected and pref.target in selected:
                factor *= pref.factor
        if not math.isfinite(factor): raise PromptSyntaxError('Selection weights overflow; reduce weights')
        if factor <= 0: return
        total += factor
        if not math.isfinite(total): raise PromptSyntaxError('Total weights overflow; reduce weights')
        if rng.random() * total < factor:
            selected_result = (nodes, selected)

    def upper_bound(nodes):
        value = 1
        for node in nodes:
            if isinstance(node, Choice):
                if _contains_fact((node,)):
                    value *= sum(upper_bound(opt.nodes) for opt in node.options)
            elif isinstance(node, If):
                value *= upper_bound(node.nodes)
            if value > MAX_VALID_STATES:
                return value
        return value

    try:
        if upper_bound(tree) > MAX_VALID_STATES:
            raise PromptSyntaxError(f'Exact constraint sampling limit exceeded ({MAX_VALID_STATES} selections)')
        walk(tree, set(), 1.0)
    except PromptSyntaxError as exc:
        if MAX_VALID_STATES != 200_000 or 'Exact constraint sampling limit exceeded' not in str(exc):
            raise
        # Large templates: randomized, bounded depth-first search with
        # sound hard-rule pruning. This is NOT exact weighted sampling.
        # Do not return a rule-invalid selection or silently ignore rules.
        budget = [200_000]
        hard_sources = {r.source for r in hard}
        soft_sources = {r.source for r in preferences}
        targets = {r.target for r in hard} | {r.target for r in preferences}

        def possible(nodes):
            return _facts(nodes)

        def choice_priority(node):
            labels = possible((node,))
            if labels & soft_sources:
                return 3
            if labels & hard_sources:
                return 2
            if labels & targets:
                return 1
            return 0

        def search(nodes, selected):
            budget[0] -= 1
            if budget[0] < 0:
                raise PromptSyntaxError('Constrained search limit exceeded (200000 branches)')
            future = possible(nodes)
            for rule in hard:
                if rule.source in selected:
                    if rule.positive and rule.target not in selected and rule.target not in future:
                        return None
                    if not rule.positive and rule.target in selected:
                        return None
            candidates = [(i, n) for i, n in enumerate(nodes)
                          if isinstance(n, Choice) and _contains_fact((n,))]
            if not candidates:
                return (nodes, selected) if valid(selected) else None
            # Resolve constraint drivers before their targets. Otherwise a
            # choice like {nude|dressed} restricts a later scene choice and
            # badly overrepresents rare scene categories in large templates.
            first, node = max(candidates, key=lambda item: choice_priority(item[1]))
            remaining = list(node.options)
            while remaining:
                # Prefer alternatives that satisfy a soft rule at this step.
                # This is a local heuristic in bounded mode, not exact global sampling.
                weights = []
                for opt in remaining:
                    after = selected | set(opt.facts)
                    weight = opt.weight
                    for pref in preferences:
                        if (pref.source in after and pref.target in after
                                and not (pref.source in selected and pref.target in selected)):
                            weight *= pref.factor
                    weights.append(weight)
                if not all(math.isfinite(w) and w > 0 for w in weights):
                    raise PromptSyntaxError('Selection weights overflow; reduce weights')
                index = rng.choices(range(len(remaining)), weights=weights, k=1)[0]
                option = remaining.pop(index)
                result = search(nodes[:first] + option.nodes + nodes[first+1:], selected | set(option.facts))
                if result is not None:
                    return result
            return None
        selected_result = search(tree, set())
    if selected_result is None: raise PromptSyntaxError('No rule-consistent expansion exists')
    result = _render(*selected_result, rng).strip()
    if len(result) > MAX_EXPANDED_CHARS: raise PromptSyntaxError('Rendered prompt exceeds size limit')
    return result
