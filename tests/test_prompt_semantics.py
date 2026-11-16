"""Conformance checks for the prompt engine shipped by this plugin."""
from __future__ import annotations

from fractions import Fraction

import pytest

from northmere_instruments.dynamic_prompt_parser import LanguageError, enumerate_outcomes, parse
from northmere_instruments.dynamic_prompt_sampler import PromptLanguageError, expand_prompt


def test_exact_mass_and_tagged_conditions() -> None:
    source = '{day => daylight | night => moonlight} ?day{warm}?night{cool}'
    outcomes = enumerate_outcomes(parse(source))
    assert {text: mass for text, _, mass in outcomes} == {
        'daylight warm': Fraction(1, 2), 'moonlight cool': Fraction(1, 2),
    }


def test_shared_decision_has_one_selection_in_both_modes() -> None:
    source = '---\nchoose style = {simple @2 | ornate}\n---\n?style.simple{A}?style.ornate{B}/?style.simple{C}?style.ornate{D}'
    for mode in ('exact', 'approximate'):
        for seed in range(25):
            result, trace = expand_prompt(source, seed, mode=mode, with_trace=True)
            assert result in ('A/C', 'B/D')
            assert sum(f.startswith('style.') for f in trace['facts']) == 1


def test_fresh_template_draws_not_shared() -> None:
    source = '---\nlet gaze = {left | right}\n---\n$gaze and $gaze'
    outcomes = enumerate_outcomes(parse(source))
    assert {text for text, _, _ in outcomes} == {
        'left and left', 'left and right', 'right and left', 'right and right',
    }


def test_multiline_header_comments_are_not_rendered() -> None:
    source = '---\n# Comment\nlet pose = {one |\n# Comment inside a declaration\ntwo}\n---\n$pose'
    assert {expand_prompt(source, seed) for seed in range(20)} == {'one', 'two'}


def test_windows_line_endings_supported() -> None:
    source = '---\r\nchoose style = {a | b}\r\n---\r\n?style.a{A}?style.b{B}'
    assert {expand_prompt(source, seed) for seed in range(20)} == {'A', 'B'}


def test_whole_line_comments_are_discarded() -> None:
    source = '// comment\nPortrait\n // another comment\n{sun | moon}'
    assert {expand_prompt(source, seed) for seed in range(30)} == {'Portrait\nsun', 'Portrait\nmoon'}


def test_hard_rule_enforced_in_exact_and_approximate() -> None:
    source = '---\nrequire window -> !flash\n---\n{window => W | room => R}{flash => F | soft => S}'
    for mode in ('exact', 'approximate'):
        seen = {expand_prompt(source, seed, mode=mode) for seed in range(50)}
        assert 'WF' not in seen
        assert seen <= {'WS', 'RF', 'RS'}


def test_preference_changes_exact_probabilities() -> None:
    source = '---\nprefer room & sun * 3\n---\n{room => R | open => O}{sun => S | gray => G}'
    mass = {text: chance for text, _, chance in enumerate_outcomes(parse(source))}
    assert mass == {'RS': Fraction(1, 2), 'RG': Fraction(1, 6),
                    'OS': Fraction(1, 6), 'OG': Fraction(1, 6)}


def test_large_independent_prompt_uses_bounded_search() -> None:
    source = ' '.join('{a | b}' for _ in range(15))
    result, trace = expand_prompt(source, 19, with_trace=True)
    assert trace['mode'] == 'approximate'
    assert len(result.split()) == 15
    assert set(result.split()) <= {'a', 'b'}
    assert expand_prompt(source, 19) == result


def test_unsatisfiable_prompt_rejected_in_both_modes() -> None:
    source = '---\nrequire a -> !a\n---\n{a => A | a => B}'
    for mode in ('exact', 'approximate'):
        with pytest.raises(PromptLanguageError):
            expand_prompt(source, 2, mode=mode)


@pytest.mark.parametrize('source', [
    '{one}', '{one | two', '{a => one | two}',
    '---\nlet x = $x\n---\n$x',
    '---\nlet x = $missing\n---\n$x',
    '---\nchoose x = {a | a}\n---\n?x.a{A}',
    '---\nrequire missing -> !missing\n---\ntext',
    '---\nlet x = {a | b}\nlet x = {c | d}\n---\n$x',
    '---\nchoose x = {a | b}\n---\n?missing{no}',
    '---\nchoose x = {a | b}\n---\n?x.a{{fact => yes | _ => no}}',
    '{red @0 | blue}',
])
def test_malformed_and_invalid_prompts_are_rejected(source: str) -> None:
    with pytest.raises((PromptLanguageError, LanguageError)):
        expand_prompt(source)


@pytest.mark.parametrize('seed', [-1, 2**64, True, '5'])
def test_invalid_seeds_rejected(seed: object) -> None:
    with pytest.raises(PromptLanguageError):
        expand_prompt('{red | blue}', seed)  # type: ignore[arg-type]


def test_explicit_exact_budget_fails_instead_of_approximating() -> None:
    source = ' '.join('{a | b}' for _ in range(15))
    with pytest.raises(PromptLanguageError, match='budget exceeded'):
        expand_prompt(source, 1, mode='exact')


def test_rendering_keeps_literal_whitespace() -> None:
    source = 'A  {left | right}\nB'
    assert {expand_prompt(source, seed) for seed in range(20)} == {
        'A  left\nB', 'A  right\nB',
    }
