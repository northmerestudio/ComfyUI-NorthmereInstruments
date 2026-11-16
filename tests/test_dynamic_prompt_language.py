"""Current prompt syntax, exact semantics, and constrained search tests."""
from pathlib import Path
from fractions import Fraction
import pytest

from northmere_instruments.dynamic_prompt_sampler import expand_prompt, PromptLanguageError
from northmere_instruments.dynamic_prompt_parser import parse, enumerate_outcomes, eval_expr

ROOT=Path(__file__).resolve().parents[1]


def test_small_exact_reference_weights():
    source='---\nprefer room & sun * 4\n---\n{room => R | outside => O}{sun => S | cloud => C}'
    p=parse(source)
    outcomes=enumerate_outcomes(p)
    assert {x:mass for x,_,mass in outcomes}=={'RS':Fraction(4,7),'RC':Fraction(1,7),'OS':Fraction(1,7),'OC':Fraction(1,7)}
    for seed in (0,1,42):
        text,trace=expand_prompt(source,seed,with_trace=True)
        assert text in ('RS','RC','OS','OC') and trace['mode']=='exact'


def test_shared_decision_consistent():
    source='---\nchoose style = {minimal | ornate}\n---\n?style.minimal{simple}?style.ornate{rich}, ?style.minimal{plain}?style.ornate{decorated}'
    assert {expand_prompt(source,s) for s in range(16)} <= {'simple, plain','rich, decorated'}


def test_fresh_template_expansion():
    p=parse('---\nlet gaze = {left | right}\n---\n$gaze/$gaze')
    assert len({x for x,_,_ in enumerate_outcomes(p)})==4


def test_approximate_hard_rules_even_with_boolean_expressions():
    source='---\nrequire (a | b) -> !c\n---\n{a => A | b => B | _ => N}{c => C | _ => X}'
    for s in range(30):
        out,trace=expand_prompt(source,s,mode='approximate',with_trace=True)
        assert out in {'AX','BX','NC','NX'}
        assert trace['mode']=='approximate'


def test_errors_are_not_silent():
    with pytest.raises(PromptLanguageError):expand_prompt('{bad => a | b}',seed=0)
    with pytest.raises(PromptLanguageError, match='Legacy v3'):expand_prompt('@pick{day: sun | night: moon}',seed=0)
    with pytest.raises(PromptLanguageError):expand_prompt('{a | b}',seed=-1)
    with pytest.raises(PromptLanguageError):expand_prompt('---\nrequire a -> !a\n---\n{a => A | a => B}',seed=1)
