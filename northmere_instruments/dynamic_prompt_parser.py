"""Prompt language parsing, validation, and bounded exact semantics."""
from __future__ import annotations

import ast
import hashlib
import itertools
import operator
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
class Computed:
    expression: str

@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[str, ...]

@dataclass(frozen=True)
class Parameter:
    name: str
    kind: str
    default: str | None

@dataclass(frozen=True)
class Function:
    params: tuple[Parameter, ...]
    body: tuple[Node, ...]

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


Node: TypeAlias = Text | Ref | Call | Computed | Conditional | Choice
Expression: TypeAlias = (
    tuple[Literal['name'], str]
    | tuple[Literal['not'], 'Expression']
    | tuple[Literal['and', 'or'], 'Expression', 'Expression']
)
Fragment: TypeAlias = str | tuple[Literal['if'], str, tuple['Fragment', ...]]
Derivation: TypeAlias = tuple[tuple[Fragment, ...], frozenset[str], Fraction]
Outcome: TypeAlias = tuple[str, frozenset[str], Fraction]

def split_args(source: str) -> tuple[str, ...]:
    """Split a call or signature without splitting quoted or nested commas."""
    if not source.strip():
        return ()
    args = []
    start = 0
    depth = 0
    quote = ''
    escaped = False
    for i, char in enumerate(source):
        if escaped:
            escaped = False
            continue
        if char == '\\' and quote:
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ''
            continue
        if char in '\"\'':
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise LanguageError('unbalanced function arguments')
        elif char == ',' and depth == 0:
            args.append(source[start:i].strip())
            start = i + 1
    if quote or depth:
        raise LanguageError('unclosed function arguments')
    args.append(source[start:].strip())
    if not all(args):
        raise LanguageError('empty function argument')
    return tuple(args)


def read_call(source: str, start: int) -> tuple[tuple[str, ...], int]:
    """Read the balanced parentheses of a call, preserving argument source."""
    depth = 0
    quote = ''
    escaped = False
    for i in range(start, len(source)):
        char = source[i]
        if escaped:
            escaped = False
        elif char == '\\' and quote:
            escaped = True
        elif quote:
            if char == quote:
                quote = ''
        elif char in '\"\'':
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return split_args(source[start + 1:i]), i + 1
    raise LanguageError('unclosed function call')


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
                if self.data.startswith('${', self.pos):
                    end = self.data.find('}', self.pos + 2)
                    if end < 0:
                        raise LanguageError('unclosed computed expression')
                    flush()
                    parts.append(Computed(self.data[self.pos + 2:end].strip()))
                    self.pos = end + 1
                    continue
                m = re.match(PATH, self.data[self.pos + 1:])
                if not m:
                    raise LanguageError('invalid template reference')
                flush()
                name = m.group()
                self.pos += 1 + len(name)
                call_start = self.pos
                while call_start < len(self.data) and self.data[call_start] in ' \t':
                    call_start += 1
                if call_start < len(self.data) and self.data[call_start] == '(':
                    args, self.pos = read_call(self.data, call_start)
                    parts.append(Call(name, args))
                else:
                    parts.append(Ref(name))
                continue
            if c == '?':
                m = re.match(PATH, self.data[self.pos + 1:])
                if m and re.match(r'[ \t]*\{', self.data[self.pos + 1 + len(m.group()):]):
                    flush()
                    self.pos += len(m.group()) + 1
                    while self.data[self.pos] in ' \t':
                        self.pos += 1
                    self.pos += 1
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
    functions: dict[str, Function] | None = None
    constants: dict[str, object] | None = None

def parse(source: str) -> Program:
    """Parse and validate an entire prompt, including its leading declarations."""
    templates: dict[str, tuple[Node, ...]] = {}
    decisions: dict[str, list[tuple[str, Fraction]]] = {}
    requirements: list[tuple[Expression, Expression]] = []
    preferences: list[tuple[Expression, Fraction]] = []
    functions: dict[str, Function] = {}
    constants: dict[str, object] = {}
    body = source
    # Declarations form the leading program section; the first ordinary
    # content line starts the prompt body. No delimiter is required.
    lines = source.splitlines(keepends=True)
    offset = 0
    started = False
    depth = 0
    for line in lines:
        stripped = line.strip().lstrip('\ufeff').strip()
        if not started and (not stripped or stripped.startswith('#')):
            offset += len(line)
            continue
        if depth == 0 and stripped and not stripped.startswith('#') and not re.match(
                r'(?:choose|let|const|fn|require|prefer)\s+', stripped):
            break
        started = True
        if not stripped.startswith('#'):
            depth += line.count('{') - line.count('}')
            if depth < 0:
                raise LanguageError('malformed declaration brace')
        offset += len(line)
    if started:
        if depth:
            raise LanguageError('unclosed declaration')
        header = source[:offset].lstrip('\ufeff')
        body = source[offset:]
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
                raise LanguageError('malformed declaration brace')
            if depth == 0:
                statements.append(cur.strip())
                cur = ''
        if cur or depth:
            raise LanguageError('unclosed declaration statement')
        for st in statements:
            m = re.fullmatch(f'const\\s+({PATH})\\s*:\\s*(text|int|float|bool)\\s*=\\s*(.+)', st, re.S)
            if m:
                name, kind, value = m.groups()
                if name in templates or name in functions or name in decisions or name in constants:
                    raise LanguageError('duplicate declaration')
                constants[name] = read_value(value, kind)
                continue
            m = re.fullmatch(f'fn\\s+({PATH})\\s*\\((.*?)\\)\\s*=\\s*(.*)', st, re.S)
            if m:
                name, signature, definition = m.groups()
                if name in templates or name in decisions or name in functions or name in constants or name.split('.')[0] in decisions:
                    raise LanguageError('duplicate declaration')
                params = []
                for raw in split_args(signature):
                    item = re.fullmatch(f'({ID})\\s*:\\s*(text|int|float|bool)(?:\\s*=\\s*(.+))?', raw, re.S)
                    if not item:
                        raise LanguageError('invalid function parameter')
                    param, kind, default = item.groups()
                    if param in (p.name for p in params):
                        raise LanguageError('duplicate function parameter')
                    if default is not None:
                        read_value(default, kind)
                    params.append(Parameter(param, kind, default))
                functions[name] = Function(tuple(params), Reader(definition).parse())
                continue
            m = re.fullmatch(f'let\\s+({PATH})\\s*=\\s*(.*)', st, re.S)
            if m:
                name, definition = m.groups()
                if name in templates or name in decisions or name in functions or name in constants or name.split('.')[0] in decisions:
                    raise LanguageError('duplicate declaration')
                templates[name] = Reader(definition).parse()
                continue
            m = re.fullmatch(f'choose\\s+({ID})\\s*=\\s*\\{{(.*)\\}}', st, re.S)
            if m:
                name, parts = m.groups()
                if name in templates or name in decisions or name in functions or name in constants or any((k.split('.')[0] == name for k in templates)):
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
    p = Program(parsed, templates, decisions, requirements, preferences, functions, constants)
    compile_functions(p)
    validate(p)
    return p


def read_value(source: str, kind: str) -> object:
    """Only literal data is executable as a function argument."""
    try:
        value = ast.literal_eval(source)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as e:
        raise LanguageError('function arguments must be typed literals or $references') from e
    if kind == 'text' and type(value) is str:
        return value
    if kind == 'int' and type(value) is int:
        return value
    if kind == 'float' and type(value) in (float, int):
        return value
    if kind == 'bool' and type(value) is bool:
        return value
    raise LanguageError(f'function argument must have type {kind}')


def evaluate(source: str, variables: dict[str, object]) -> object:
    """Evaluate bounded, side-effect-free expressions with no Python object access."""
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
    compare = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
               ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
    builtins = {'upper': str.upper, 'lower': str.lower, 'title': str.title,
                'len': len, 'str': str, 'int': int, 'float': float,
                'abs': abs, 'round': round, 'min': min, 'max': max}
    try:
        tree = ast.parse(source, mode='eval')
    except SyntaxError as e:
        raise LanguageError('invalid computed expression') from e
    count = [0]

    def visit(node: ast.AST) -> object:
        count[0] += 1
        if count[0] > 300:
            raise LanguageError('computed expression too complex')
        if isinstance(node, ast.Constant) and type(node.value) in (str, int, float, bool):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise LanguageError(f'unknown expression variable: {node.id}')
            return variables[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            value = visit(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            if type(value) not in (int, float):
                raise LanguageError('unary arithmetic requires a number')
            return -value if isinstance(node.op, ast.USub) else +value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = visit(node.left), visit(node.right)
            if type(left) not in (str, int, float) or type(right) not in (str, int, float):
                raise LanguageError('invalid computed operands')
            if len(str(left)) + len(str(right)) > 10000:
                raise LanguageError('computed expression too large')
            if isinstance(node.op, ast.Mult) and ((type(left) is str and type(right) is int) or
                                                  (type(right) is str and type(left) is int)):
                if max(left if type(left) is int else right, 0) * len(left if type(left) is str else right) > 100000:
                    raise LanguageError('computed expression too large')
            try:
                return binary[type(node.op)](left, right)
            except (ValueError, ZeroDivisionError, OverflowError, TypeError) as e:
                raise LanguageError('invalid computed arithmetic') from e
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [bool(visit(v)) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            for op, right_node in zip(node.ops, node.comparators):
                right = visit(right_node)
                if type(op) not in compare:
                    raise LanguageError('unsupported comparison')
                try:
                    valid = compare[type(op)](left, right)
                except TypeError as e:
                    raise LanguageError('invalid comparison operands') from e
                if not valid:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return visit(node.body if visit(node.test) else node.orelse)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            fn = builtins.get(node.func.id)
            if fn is None:
                raise LanguageError('unknown computed function')
            args = [visit(a) for a in node.args]
            try:
                return fn(*args)
            except (ValueError, TypeError, OverflowError, ZeroDivisionError) as e:
                raise LanguageError('invalid computed function arguments') from e
        raise LanguageError('unsupported computed expression')

    result = visit(tree.body)
    if type(result) not in (str, int, float, bool) or len(str(result)) > 100000:
        raise LanguageError('computed expression result too large')
    return result



def compile_functions(p: Program) -> None:
    """Expand typed, hygienic function calls into the established prompt AST.

    Compilation is pure: no Python execution, imports, filesystem, or side effects.
    Each invocation has independent choice draws, as with existing templates.
    """
    functions = p.functions or {}
    budget = [0]
    constants = p.constants or {}

    def compile_arg(value: str, kind: str, env: dict[str, tuple[Node, ...]], stack: tuple[str, ...]) -> tuple[Node, ...]:
        if value.startswith('$'):
            parsed = Reader(value).parse()
            if len(parsed) != 1 or not isinstance(parsed[0], (Ref, Call)):
                raise LanguageError('invalid function reference argument')
            if kind != 'text':
                raise LanguageError('reference arguments require type text')
            return compile_nodes(parsed, env, stack, {})
        item = read_value(value, kind)
        return (Text(str(item) if not isinstance(item, bool) else str(item).lower()),)

    def compile_nodes(
        nodes: tuple[Node, ...], env: dict[str, tuple[Node, ...]],
        stack: tuple[str, ...], values: dict[str, object],
    ) -> tuple[Node, ...]:
        result: list[Node] = []
        for node in nodes:
            budget[0] += 1
            if budget[0] > 30000:
                raise LanguageError('function expansion exceeds node budget')
            if isinstance(node, Ref) and node.name in env:
                result.extend(env[node.name])
            elif isinstance(node, Ref) and node.name in constants:
                value = constants[node.name]
                result.append(Text(str(value).lower() if isinstance(value, bool) else str(value)))
            elif isinstance(node, Computed):
                value = evaluate(node.expression, constants | values)
                result.append(Text(str(value).lower() if isinstance(value, bool) else str(value)))
            elif isinstance(node, Call):
                function = functions.get(node.name)
                if function is None:
                    raise LanguageError(f'unknown function: {node.name}')
                if node.name in stack:
                    raise LanguageError('recursive function')
                if len(node.args) > len(function.params):
                    raise LanguageError('too many function arguments')
                local = {}
                local_values: dict[str, object] = {}
                for i, param in enumerate(function.params):
                    arg = node.args[i] if i < len(node.args) else param.default
                    if arg is None:
                        raise LanguageError(f'missing function argument: {param.name}')
                    local[param.name] = compile_arg(arg, param.kind, env, stack)
                    if not arg.startswith('$'):
                        local_values[param.name] = read_value(arg, param.kind)
                result.extend(compile_nodes(function.body, local, stack + (node.name,), local_values))
            elif isinstance(node, Choice):
                branches = tuple(Branch(compile_nodes(b.children, env, stack, values), b.facts, b.weight)
                                 for b in node.branches)
                result.append(Choice(branches))
            elif isinstance(node, Conditional):
                result.append(Conditional(node.condition, compile_nodes(node.children, env, stack, values)))
            else:
                result.append(node)
        return tuple(result)

    p.body = compile_nodes(p.body, {}, (), {})
    p.templates = {name: compile_nodes(nodes, {}, (), {}) for name, nodes in p.templates.items()}


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
