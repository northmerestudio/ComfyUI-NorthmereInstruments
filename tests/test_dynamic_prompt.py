"""Northmere v3 language regression and statistical tests (no legacy syntax)."""
import pytest

from northmere_instruments.dynamic_prompt_engine import PromptSyntaxError, expand_prompt


def test_plain_choices_nested_and_reproducible():
    text = 'A {small|{medium|large}} {red|blue} hat'
    assert expand_prompt(text, 42) == expand_prompt(text, 42)
    assert len({expand_prompt(text, i) for i in range(40)}) >= 4
    assert all('{' not in expand_prompt(text, i) for i in range(30))


def test_weighted_choice_probability():
    outputs = [expand_prompt('{3::red|1::blue}', i) for i in range(1200)]
    assert 0.69 < outputs.count('red') / len(outputs) < 0.81


def test_labelled_choice_and_whitespace():
    text = '@pick { day :   sunny day  | night:    moonlit night  }'
    assert {expand_prompt(text, i) for i in range(50)} == {'sunny day', 'moonlit night'}


def test_if_resolves_after_labels_and_chooses_nested_plain_alternatives():
    text = 'Portrait @if(day){{golden|warm} light} @pick{night:moon|day:sun}'
    outputs = [expand_prompt(text, i) for i in range(80)]
    assert all((' light' in x) == x.endswith('sun') for x in outputs)
    assert any('golden' in x for x in outputs)
    assert any('warm' in x for x in outputs)
    assert all('  ' not in x for x in outputs)


def test_rules_positive_negative_and_complete_search():
    text = '@pick{day:day|night:night} @pick{warm:warm|cold:cold}\n@rules\nday -> warm\nnight -> !warm\n@end'
    outputs = {expand_prompt(text, s) for s in range(40)}
    assert outputs == {'day warm', 'night cold'}


def test_rare_solution_is_found_instead_of_retrying():
    text = ' '.join(f'@pick{{yes{i}:ok{i}|no{i}:bad{i}}}' for i in range(12))
    text += '\n@rules\n' + '\n'.join(f'yes0 -> yes{i}' for i in range(1,12)) + '\n@end'
    for seed in range(3):
        assert expand_prompt(text, seed)


def test_exact_soft_rule_global_probability():
    text = '@pick{room:room|outside:outside} @pick{sun:sun|cloud:cloud}\n@rules\nroom ~> sun @ 4\n@end'
    outcomes = [expand_prompt(text, s) for s in range(1400)]
    # 4:1 relative weight only when both room and sun are selected.
    room = [x for x in outcomes if x.startswith('room')]
    assert 0.75 < sum(x.endswith('sun') for x in room) / len(room) < 0.85
    assert 0.62 < len(room) / len(outcomes) < 0.76


def test_hard_rule_overrides_soft_rule():
    t = '@pick{room:room|outside:outside} @pick{sun:sun|cloud:cloud}\n@rules\nroom -> !sun\nroom ~> sun @ 100\n@end'
    assert all(x != 'room sun' for x in (expand_prompt(t, i) for i in range(60)))


def test_definitions_forward_reference_fresh_and_recursive():
    t = '@use(gaze) and @use(gaze)\n@define gaze\n{left|right}\n@end'
    assert {expand_prompt(t, i) for i in range(100)} == {'left and left','left and right','right and left','right and right'}
    with pytest.raises(PromptSyntaxError, match='Recursive definition'):
        expand_prompt('@use(a)\n@define a\n@use(b)\n@end\n@define b\n@use(a)\n@end')


def test_no_legacy_and_no_implicit_file_wildcards():
    with pytest.raises(PromptSyntaxError, match='Legacy'):
        expand_prompt('[[#day]] {sun|moon}')
    with pytest.raises(PromptSyntaxError, match='wildcards'):
        expand_prompt('__colors__')


@pytest.mark.parametrize('bad, error', [
    ('{a}', 'at least two'),
    ('{a|}', 'Empty'),
    ('{a|b', 'Unclosed'),
    ('{a|b}}', 'Unexpected'),
    ('@pick{a: x|a: y}', 'Duplicate alternative'),
    ('@pick{x|y}', 'Expected named alternative'),
    ('@pick{a: x|b: y} @if(missing){hello}', 'Unknown label'),
    ('@if(a){@pick{a:a|b:b}}', 'Labels inside'),
    ('@use(unknown)', 'Unknown definition'),
    ('@define x\nhi', 'Unclosed'),
    ('@end', 'Unexpected'),
    ('@rules\na -> b\n@end', 'Unknown label'),
    ('@pick{a:a|b:b}\n@rules\na -> b\na -> !b\n@end', 'Contradictory'),
    ('@pick{a:a|b:b}\n@rules\na ~> b @ 0\n@end', 'Invalid soft weight'),
    ('{0::red|blue}', 'positive'),
])
def test_errors(bad, error):
    if error is None:
        assert expand_prompt(bad)
    else:
        with pytest.raises(PromptSyntaxError, match=error):
            expand_prompt(bad)


def test_impossible_rules():
    t = '@pick{a:A|b:B}\n@rules\na -> b\nb -> a\n@end'
    # b cannot co-occur with a, but both are legal independently (implications
    # reject each one), therefore there is no valid solution.
    with pytest.raises(PromptSyntaxError, match='No rule-consistent'):
        expand_prompt(t)


def test_limit_is_reported_and_not_mistaken_for_unsatisfiable(monkeypatch):
    import northmere_instruments.dynamic_prompt_engine as engine
    monkeypatch.setattr(engine, 'MAX_VALID_STATES', 1)
    t = '@pick{a:a|b:b}\n@rules\na -> !b\n@end'
    with pytest.raises(PromptSyntaxError, match='sampling limit'):
        expand_prompt(t)


def test_literal_escaping_and_clip_weighting():
    assert expand_prompt(r'\{literal\} (portrait:1.2) \@pick') == '{literal} (portrait:1.2) @pick'


def test_independent_unlabelled_choices_do_not_blow_up_constraint_space():
    t = '@pick{day:day|night:night} ' + ' '.join('{red|blue}' for _ in range(25))
    t += '\n@rules\nday -> !night\n@end'
    assert len(expand_prompt(t, 0).split()) == 26
