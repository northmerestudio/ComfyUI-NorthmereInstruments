"""Prompt language parsing, validation, and bounded exact semantics."""
from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass
from fractions import Fraction
from functools import reduce
from math import lcm
from typing import Iterator, Literal, TypeAlias

ID = '[A-Za-z][A-Za-z0-9_]*'
PATH = f'{ID}(?:\\.{ID})*'


class LanguageError(ValueError):
    """Invalid prompt syntax, semantics, or exact sampling budget."""


@dataclass(frozen=True)
class Text:
    value: str

@dataclass(frozen=True)
class Ref:
    name: str

@dataclass(frozen=True)
class Conditional:
    condition: str
    children: tuple[Node, ...]

@dataclass(frozen=True)
class Branch:
    children: tuple[Node, ...]
    facts: frozenset[str]
    weight: Fraction

@dataclass(frozen=True)
class Choice:
    branches: tuple[Branch, ...]


Node: TypeAlias = Text | Ref | Conditional | Choice
Expression: TypeAlias = (
    tuple[Literal['name'], str]
    | tuple[Literal['not'], 'Expression']
    | tuple[Literal['and', 'or'], 'Expression', 'Expression']
)
Fragment: TypeAlias = str | tuple[Literal['if'], str, tuple['Fragment', ...]]
Derivation: TypeAlias = tuple[tuple[Fragment, ...], frozenset[str], Fraction]
Outcome: TypeAlias = tuple[str, frozenset[str], Fraction]

class Reader:
    """Read nested prompt expressions into immutable syntax nodes."""

    def __init__(self, data: str) -> None:
        self.data = data
        self.pos = 0

    def parse(self, stop: str = '') -> tuple[Node, ...]:
        parts: list[Node] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                parts.append(Text(''.join(buf)))
                buf.clear()
        while self.pos < len(self.data) and self.data[self.pos] not in stop:
            c = self.data[self.pos]
            if c == '\\':
                if self.pos + 1 == len(self.data):
                    raise LanguageError('trailing escape')
                nxt = self.data[self.pos + 1]
                if nxt not in '{}|$?@/=\\':
                    raise LanguageError('invalid escape')
                buf.append(nxt)
                self.pos += 2
                continue
            if c == '{':
                flush()
                self.pos += 1
                parts.append(self.choice())
                continue
            if c == '$':
                m = re.match(PATH, self.data[self.pos + 1:])
                if not m:
                    raise LanguageError('invalid template reference')
                flush()
                parts.append(Ref(m.group()))
                self.pos += 1 + len(m.group())
                continue
            if c == '?':
                m = re.match(PATH, self.data[self.pos + 1:])
                if m and self.data[self.pos + 1 + len(m.group()):].startswith('{'):
                    flush()
                    self.pos += len(m.group()) + 2
                    inner = self.parse('}')
                    if self.pos >= len(self.data) or self.data[self.pos] != '}':
                        raise LanguageError('unclosed conditional')
                    self.pos += 1
                    parts.append(Conditional(m.group(), inner))
                    continue
            if c == '}':
                raise LanguageError('unexpected closing brace')
            buf.append(c)
            self.pos += 1
        flush()
        return tuple(parts)

    def choice(self) -> Choice:
        # Scan nested choices and escapes before parsing each alternative.
        start = self.pos
        depth = 0
        escaped = False
        sections = []
        while self.pos < len(self.data):
            c = self.data[self.pos]
            if escaped:
                escaped = False
                self.pos += 1
                continue
            if c == '\\':
                escaped = True
                self.pos += 1
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                if depth == 0:
                    sections.append(self.data[start:self.pos])
                    self.pos += 1
                    break
                depth -= 1
            elif c == '|' and depth == 0:
                sections.append(self.data[start:self.pos])
                start = self.pos + 1
            self.pos += 1
        else:
            raise LanguageError('unclosed choice')
        if len(sections) < 2:
            raise LanguageError('a choice needs at least two branches')
        branches = []
        has_tag = []
        for section in sections:
            # Trim syntax-margin whitespace, not internal text whitespace.
            section = section.strip()
            w = Fraction(1)
            if re.search('\\s+@-[0-9]+(?:\\.[0-9]+)?$', section):
                raise LanguageError('negative weight')
            wm = re.search('\\s+@([0-9]+(?:\\.[0-9]+)?)$', section)
            if wm:
                w = Fraction(wm.group(1))
                section = section[:wm.start()]
            if w <= 0:
                raise LanguageError('weights must be positive')
            # Fact tags are recognized only at the start of a branch.
            tag = re.match(f'^((?:{ID})(?:\\s*,\\s*{ID})*|_)\\s*=>\\s*', section)
            facts = frozenset()
            if tag:
                if tag.group(1) != '_':
                    facts = frozenset((x.strip() for x in tag.group(1).split(',')))
                section = section[tag.end():]
                has_tag.append(True)
            else:
                has_tag.append(False)
            child_reader = Reader(section)
            nodes = child_reader.parse()
            if child_reader.pos < len(section):
                raise LanguageError('unparsed content')
            branches.append(Branch(nodes, facts, w))
        if any(has_tag) and (not all(has_tag)):
            raise LanguageError('mixed fact tags and plain branches')
        return Choice(tuple(branches))

def parse_expr(expr: str) -> Expression:
    tokens = re.findall(f'{PATH}|[!&|()]', expr)
    if ''.join(tokens) != re.sub('\\s+', '', expr):
        raise LanguageError('invalid Boolean expression')
    pos = 0

    def atom() -> Expression:
        nonlocal pos
        if pos == len(tokens):
            raise LanguageError('incomplete expression')
        t = tokens[pos]
        pos += 1
        if t == '!':
            return ('not', atom())
        if t == '(':
            e = ors()
            if pos == len(tokens) or tokens[pos] != ')':
                raise LanguageError('unbalanced expression')
            pos += 1
            return e
        if re.fullmatch(PATH, t):
            return ('name', t)
        raise LanguageError('invalid expression token')

    def ands() -> Expression:
        nonlocal pos
        v = atom()
        while pos < len(tokens) and tokens[pos] == '&':
            pos += 1
            v = ('and', v, atom())
        return v

    def ors() -> Expression:
        nonlocal pos
        v = ands()
        while pos < len(tokens) and tokens[pos] == '|':
            pos += 1
            v = ('or', v, ands())
        return v
    result = ors()
    if pos != len(tokens):
        raise LanguageError('trailing expression tokens')
    return result

def eval_expr(e: Expression, facts: frozenset[str] | set[str]) -> bool:
    kind = e[0]
    if kind == 'name':
        return e[1] in facts
    if kind == 'not':
        return not eval_expr(e[1], facts)
    if kind == 'and':
        return eval_expr(e[1], facts) and eval_expr(e[2], facts)
    if kind == 'or':
        return eval_expr(e[1], facts) or eval_expr(e[2], facts)
    raise AssertionError(kind)

@dataclass
class Program:
    body: tuple[Node, ...]
    templates: dict[str, tuple[Node, ...]]
    decisions: dict[str, list[tuple[str, Fraction]]]
    requirements: list[tuple[Expression, Expression]]
    preferences: list[tuple[Expression, Fraction]]

def parse(source: str) -> Program:
    """Parse and validate an entire prompt, including its optional header."""
    templates: dict[str, tuple[Node, ...]] = {}
    decisions: dict[str, list[tuple[str, Fraction]]] = {}
    requirements: list[tuple[Expression, Expression]] = []
    preferences: list[tuple[Expression, Fraction]] = []
    body = source
    # Tolerate paste-related whitespace/BOM before a header, but do not
    # change whitespace in ordinary prompts that have no header.
    opening = re.match(r'\A\ufeff?[ \t\r\n]*---[ \t]*\r?\n', source)
    if opening:
        header_start = opening.end()
        match = re.search(r'^---[ \t]*\r?$', source[header_start:], re.M)
        if not match:
            raise LanguageError('unclosed header')
        header = source[header_start:header_start + match.start()]
        body = source[header_start + match.end():]
        body = body.removeprefix('\r\n') if body.startswith('\r\n') else body.removeprefix('\n')
        statements = []
        cur = ''
        depth = 0
        for line in header.splitlines():
            if line.lstrip().startswith('#') or (not cur and not line.strip()):
                continue
            cur += ('\n' if cur else '') + line
            depth += line.count('{') - line.count('}')
            if depth < 0:
                raise LanguageError('malformed header brace')
            if depth == 0:
                statements.append(cur.strip())
                cur = ''
        if cur or depth:
            raise LanguageError('unclosed header statement')
        for st in statements:
            m = re.fullmatch(f'let\\s+({PATH})\\s*=\\s*(.*)', st, re.S)
            if m:
                name, definition = m.groups()
                if name in templates or name in decisions or name.split('.')[0] in decisions:
                    raise LanguageError('duplicate declaration')
                templates[name] = Reader(definition).parse()
                continue
            m = re.fullmatch(f'choose\\s+({ID})\\s*=\\s*\\{{(.*)\\}}', st, re.S)
            if m:
                name, parts = m.groups()
                if name in templates or name in decisions or any((k.split('.')[0] == name for k in templates)):
                    raise LanguageError('duplicate declaration')
                variants = []
                for raw in parts.split('|'):
                    choice = re.fullmatch(f'\\s*({ID})(?:\\s+@([0-9]+(?:\\.[0-9]+)?))?\\s*', raw)
                    if not choice:
                        raise LanguageError('bad decision option')
                    weight = Fraction(choice.group(2) or '1')
                    if weight <= 0:
                        raise LanguageError('bad decision weight')
                    variants.append((choice.group(1), weight))
                if len(variants) < 2 or len(set((n for n, _ in variants))) != len(variants):
                    raise LanguageError('invalid decision variants')
                decisions[name] = variants
                continue
            m = re.fullmatch('require\\s+(.+?)\\s*->\\s*(.+)', st)
            if m:
                requirements.append((parse_expr(m.group(1)), parse_expr(m.group(2))))
                continue
            m = re.fullmatch('prefer\\s+(.+)\\s+\\*\\s+([0-9]+(?:\\.[0-9]+)?)', st)
            if m:
                factor = Fraction(m.group(2))
                if factor <= 0:
                    raise LanguageError('invalid preference weight')
                preferences.append((parse_expr(m.group(1)), factor))
                continue
            raise LanguageError(f'unknown header statement: {st[:45]}')
    body = ''.join((line for line in body.splitlines(keepends=True) if not line.lstrip().startswith('//')))
    parsed = Reader(body).parse()
    p = Program(parsed, templates, decisions, requirements, preferences)
    validate(p)
    return p


def traverse(
    nodes: tuple[Node, ...], templates: dict[str, tuple[Node, ...]],
    stack: tuple[str, ...] = (),
) -> 'Iterator[Node]':
    for node in nodes:
        yield node
        if isinstance(node, Ref):
            if node.name not in templates:
                raise LanguageError('unknown template')
            if node.name in stack:
                raise LanguageError('recursive template')
            yield from traverse(templates[node.name], templates, stack + (node.name,))
        if isinstance(node, Choice):
            for branch in node.branches:
                yield from traverse(branch.children, templates, stack)
        if isinstance(node, Conditional):
            yield from traverse(node.children, templates, stack)

def validate(p: Program) -> None:
    """Reject missing references, recursive templates, and unknown facts."""
    for key in p.templates:
        list(traverse(p.templates[key], p.templates, (key,)))
    allnodes = list(traverse(p.body, p.templates))
    for key in p.templates:
        allnodes += list(traverse(p.templates[key], p.templates, (key,)))
    facts = set()
    for node in allnodes:
        if isinstance(node, Choice):
            for b in node.branches:
                facts.update(b.facts)
    selectors = {f'{d}.{option}' for d, variants in p.decisions.items() for option, _ in variants}
    names = facts | selectors
    for node in allnodes:
        if isinstance(node, Conditional):
            if node.condition not in names:
                raise LanguageError('unknown condition')
            if any((isinstance(n, Choice) and any((b.facts for b in n.branches)) for n in traverse(node.children, p.templates))):
                raise LanguageError('fact-emitting choice inside conditional')

    def refs(expr: Expression) -> set[str]:
        if expr[0] == 'name':
            return {expr[1]}
        if expr[0] == 'not':
            return refs(expr[1])
        return refs(expr[1]) | refs(expr[2])
    for lhs, rhs in p.requirements:
        if (refs(lhs) | refs(rhs)) - names:
            raise LanguageError('unknown rule symbol')
    for expr, _ in p.preferences:
        if refs(expr) - names:
            raise LanguageError('unknown preference symbol')

def enumerate_outcomes(p: Program, max_states: int = 30000) -> list[Outcome]:
    """Exact bounded semantic oracle; returns (output, facts, Fraction probability).

    A derivation includes all active independent choices and template expansions.
    Conditional content may contain choices but never fact-producing choices.
    """

    def expand(nodes: tuple[Node, ...]) -> list[Derivation]:
        acc = [((), frozenset(), Fraction(1))]
        for node in nodes:
            if isinstance(node, Text):
                opts = [((node.value,), frozenset(), Fraction(1))]
            elif isinstance(node, Ref):
                opts = expand(p.templates[node.name])
            elif isinstance(node, Conditional):
                opts = [((('if', node.condition, frags),), facts, mass) for frags, facts, mass in expand(node.children)]
            elif isinstance(node, Choice):
                total = sum((b.weight for b in node.branches), Fraction(0))
                opts = []
                for branch in node.branches:
                    opts.extend(((frag, facts | branch.facts, mass * branch.weight / total) for frag, facts, mass in expand(branch.children)))
            else:
                raise AssertionError(node)
            updated = []
            for frags1, facts1, mass1 in acc:
                for frags2, facts2, mass2 in opts:
                    updated.append((frags1 + frags2, facts1 | facts2, mass1 * mass2))
                    if len(updated) > max_states:
                        raise LanguageError('exact reference budget exceeded')
            acc = updated
        return acc

    def render(frags: tuple[Fragment, ...], facts: frozenset[str]) -> str:
        result = []
        for frag in frags:
            if isinstance(frag, str):
                result.append(frag)
            elif frag[0] == 'if':
                if frag[1] in facts:
                    result.append(render(frag[2], facts))
            else:
                raise AssertionError(frag)
        return ''.join(result)
    states = []
    variant_lists = []
    for name, variants in p.decisions.items():
        total = sum((w for _, w in variants), Fraction(0))
        variant_lists.append([(f'{name}.{v}', w / total) for v, w in variants])
    for selections in itertools.product(*variant_lists):
        decision_facts = frozenset((name for name, _ in selections))
        decision_mass = reduce(lambda a, b: a * b, (w for _, w in selections), Fraction(1))
        for frags, facts, mass in expand(p.body):
            allfacts = facts | decision_facts
            if not all((not eval_expr(a, allfacts) or eval_expr(b, allfacts) for a, b in p.requirements)):
                continue
            pref = reduce(lambda a, b: a * b, (factor for pred, factor in p.preferences if eval_expr(pred, allfacts)), Fraction(1))
            states.append((render(frags, allfacts), allfacts, mass * decision_mass * pref))
            if len(states) > max_states:
                raise LanguageError('exact reference budget exceeded')
    if not states:
        raise LanguageError('no valid derivations')
    total = sum((mass for _, _, mass in states), Fraction(0))
    return [(out, facts, mass / total) for out, facts, mass in states]


def exact_sample(
    p: Program, seed: int, outcomes: list[Outcome] | None = None,
) -> tuple[str, frozenset[str]]:
    """Portable exact-reference profile: SHA-256 counter bits + rejection.

    The ordering of derivations is decisions in declaration order, alternatives
    in source order, recursively depth-first. This implementation keeps that
    ordering via enumerate_outcomes.
    """
    if not isinstance(seed, int) or isinstance(seed, bool) or (not -2 ** 63 <= seed < 2 ** 63):
        raise LanguageError('seed must be a signed 64-bit integer')
    if outcomes is None:
        outcomes = enumerate_outcomes(p)
    d = lcm(*(mass.denominator for _, _, mass in outcomes))
    weights = [int(mass * d) for _, _, mass in outcomes]
    total = sum(weights)
    prefix = b'Northmere-v4\x00' + str(seed).encode('ascii') + b'\x00'
    counter = 0
    pool = 0
    available = 0

    def bit() -> int:
        nonlocal counter, pool, available
        if not available:
            if counter >= 2 ** 64:
                raise LanguageError('random stream exhausted')
            pool = int.from_bytes(hashlib.sha256(prefix + counter.to_bytes(8, 'big')).digest(), 'big')
            counter += 1
            available = 256
        available -= 1
        return pool >> available & 1
    k = (total - 1).bit_length()
    while True:
        candidate = 0
        for _ in range(k):
            candidate = candidate << 1 | bit()
        if candidate < total:
            break
    for out, facts, mass in outcomes:
        bucket = int(mass * d)
        if candidate < bucket:
            return (out, facts)
        candidate -= bucket
    raise AssertionError('unreachable')
