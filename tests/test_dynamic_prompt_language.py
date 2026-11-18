"""Current prompt syntax, exact semantics, and constrained search tests."""
from pathlib import Path
from fractions import Fraction
import pytest

from northmere_instruments.dynamic_prompt_sampler import expand_prompt, PromptLanguageError
from northmere_instruments.dynamic_prompt_parser import parse, enumerate_outcomes, eval_expr

ROOT=Path(__file__).resolve().parents[1]


def test_small_exact_reference_weights():
    source='prefer room & sun * 4\n{room => R | outside => O}{sun => S | cloud => C}'
    p=parse(source)
    outcomes=enumerate_outcomes(p)
    assert {x:mass for x,_,mass in outcomes}=={'RS':Fraction(4,7),'RC':Fraction(1,7),'OS':Fraction(1,7),'OC':Fraction(1,7)}
    for seed in (0,1,42):
        text,trace=expand_prompt(source,seed,with_trace=True)
        assert text in ('RS','RC','OS','OC') and trace['mode']=='exact'


def test_shared_decision_consistent():
    source='choose style = {minimal | ornate}\n?style.minimal{simple}?style.ornate{rich}, ?style.minimal{plain}?style.ornate{decorated}'
    assert {expand_prompt(source,s) for s in range(16)} <= {'simple, plain','rich, decorated'}


def test_fresh_template_expansion():
    p=parse('let gaze = {left | right}\n$gaze/$gaze')
    assert len({x for x,_,_ in enumerate_outcomes(p)})==4


def test_approximate_hard_rules_even_with_boolean_expressions():
    source='require (a | b) -> !c\n{a => A | b => B | _ => N}{c => C | _ => X}'
    for s in range(30):
        out,trace=expand_prompt(source,s,mode='approximate',with_trace=True)
        assert out in {'AX','BX','NC','NX'}
        assert trace['mode']=='approximate'


def test_errors_are_not_silent():
    with pytest.raises(PromptLanguageError):expand_prompt('{bad => a | b}',seed=0)
    with pytest.raises(PromptLanguageError, match='Legacy v3'):expand_prompt('@pick{day: sun | night: moon}',seed=0)
    with pytest.raises(PromptLanguageError):expand_prompt('{a | b}',seed=-1)
    with pytest.raises(PromptLanguageError):expand_prompt('require a -> !a\n{a => A | a => B}',seed=1)


def test_typed_functions_and_scoped_arguments():
    source = ('fn scene.pose(subject: text, count: int = 2) = '
              '$subject ${count + 1} {left | right}\n'
              '$scene.pose("model", 3) / $scene.pose("actor")')
    outcomes = enumerate_outcomes(parse(source))
    assert {text for text, _, _ in outcomes} == {
        f'model 4 {a} / actor 3 {b}' for a in ('left', 'right') for b in ('left', 'right')
    }
    for mode in ('exact', 'approximate'):
        assert expand_prompt(source, 9, mode=mode).startswith('model 4 ')


def test_constants_computed_expressions_and_namespaced_functions():
    source = ('const length: int = 4\n'
              'fn text.decorate(word: text, multiplier: int) = '
              '${upper(word)} ${length * multiplier} ${"yes" if multiplier > 1 else "no"}\n'
              '$text.decorate("hello", 3)')
    assert expand_prompt(source) == 'HELLO 12 yes'


@pytest.mark.parametrize('source', [
    'fn f(n: int) = $n\n$f("wrong")',
    'fn f(n: int) = $n\n$f()',
    'fn f(n: text) = $n\n$f("a", "b")',
    'fn f(n: text) = $f("again")\n$f("hi")',
    'fn f(n: text, n: int) = hi\n$f("hi", 1)',
    'fn f(n: int = "no") = $n\n$f()',
    '${__import__("os")}', '${(1).__class__}', '${2 ** 100000}',
    'const x: bool = 1\n$x',
])
def test_extended_language_rejects_invalid_or_unsafe_programs(source):
    with pytest.raises(PromptLanguageError):
        expand_prompt(source)


def test_existing_prompt_attachment_is_still_valid():
    source = ROOT / 'examples' / 'beauty_prompt.txt'
    for mode in ('auto', 'approximate'):
        text, trace = expand_prompt(source.read_text(), 123, mode=mode, with_trace=True)
        assert 'photorealistic editorial beauty photograph' in text
        assert '?' not in text
        assert '$model' not in text
        assert trace['facts']


def test_headerless_declarations_with_flexible_whitespace():
    source = ('\ufeff  \n# definitions\n'
              'const  label : text = "portrait"\n'
              'fn scene.label ( count : int = 2 ) = ${upper(label)} ${count + 1}\n'
              'choose look = { soft | bold }\n'
              'require look.soft -> !look.bold\n'
              '\n$scene.label ( 3 ) ?look.soft {soft}?look.bold{bold}')
    assert {expand_prompt(source, seed) for seed in range(12)} == {
        'PORTRAIT 4 soft', 'PORTRAIT 4 bold',
    }


def test_declarations_without_separator():
    source = ('choose style = { natural | glossy }\n'
              'let detail = { gentle | vivid }\n'
              'fn decor.name(value: text) = ${upper(value)}\n'
              '?style.natural {soft}?style.glossy{bright} $detail $decor.name ("tone")')
    assert len({expand_prompt(source, seed) for seed in range(100)}) >= 2


def test_declarations_with_crlf():
    source = ('const name: text = "model"\r\n'
              'fn describe ( subject : text ) = $subject\r\n'
              '$describe ( $name )')
    assert expand_prompt(source) == 'model'


def test_plain_prompt_whitespace_stays_literal():
    source = '  A  { x | y }  B\n'
    assert {expand_prompt(source, seed) for seed in range(20)} == {
        '  A  x  B\n', '  A  y  B\n',
    }


def test_prompt_syntax_errors_report_source_position() -> None:
    from northmere_instruments.dynamic_prompt_sampler import PromptLanguageError, expand_prompt

    cases = [
        ('first\n{red | blue', 'Line 2, column 1: unclosed choice'),
        ('first\n\\z', 'Line 2, column 1: invalid escape'),
        ('// comment\nfirst\n{red | blue', 'Line 3, column 1: unclosed choice'),
        ('let color = {red | blue\nbody', 'Line 1, column 13: unclosed declaration'),
        ('choose color = {red | }\nbody', 'Line 1, column 1: bad decision option'),
    ]
    for source, expected in cases:
        try:
            expand_prompt(source)
        except PromptLanguageError as error:
            assert expected in str(error), str(error)
        else:
            raise AssertionError(f'Expected syntax failure for {source!r}')
