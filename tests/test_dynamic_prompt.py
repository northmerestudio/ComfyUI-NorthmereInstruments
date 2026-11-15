"""Run using pytest after placing this patch in the Northmere repository."""

import pytest

from northmere_instruments.dynamic_prompt_engine import PromptSyntaxError, expand_prompt


def test_nested_choices_reproducible():
    text = 'A {red|{green|blue}} hat and {small|large} bag'
    assert expand_prompt(text, seed=42) == expand_prompt(text, seed=42)
    for seed in range(50):
        output = expand_prompt(text, seed=seed)
        assert '{' not in output and '}' not in output

def test_negative_dependency_with_earlier_choice():
    text = 'person{, [[#glasses]]wearing glasses|, [[#glasses]]wearing glasses|}, {[[#blindfold]]blindfolded|not blindfolded}'
    for seed in range(500):
        output = expand_prompt(text + '\n[[RULES]]\nblindfold -> !glasses\n[[/RULES]]', seed)
        assert not (output.endswith(', blindfolded') and 'wearing glasses' in output)
        assert '[[#' not in output

def test_positive_dependency():
    text = '{[[#rain]]rain|sun}, {[[#umbrella]]umbrella|no accessory}'
    for seed in range(100):
        output = expand_prompt(text + '\n[[RULES]]\nrain -> umbrella\n[[/RULES]]', seed)
        assert not ('rain,' in output and 'umbrella' not in output)

def test_absent_trigger_does_not_force_target():
    text = '{[[#rain]]rain|sun}, {[[#umbrella]]umbrella|hat}'
    outputs = [expand_prompt(text + '\n[[RULES]]\nrain -> umbrella\n[[/RULES]]', seed) for seed in range(50)]
    assert any('sun' in output and 'hat' in output for output in outputs)


def test_literal_braces_and_empty_alternative():
    assert expand_prompt(r'\{literal\} {x|}', seed=4) in ('{literal} x', '{literal} ')

def test_broken_syntax_fails_loudly():
    for template in ('{a|b', '{a}', 'a}', '{a|{b|c}'):
        with pytest.raises(PromptSyntaxError):
            expand_prompt(template)


def test_contradiction_fails_loudly():
    with pytest.raises(PromptSyntaxError):
        expand_prompt('{[[#a]]a|} {[[#b]]b|}\n[[RULES]]\na -> b\na -> !b\n[[/RULES]]', seed=0)

def test_unknown_rule_tag_fails_loudly():
    with pytest.raises(PromptSyntaxError):
        expand_prompt('{[[#a]]a|}\n[[RULES]]\na -> unknown\n[[/RULES]]', seed=0)


def test_impossible_positive_dependency_fails_loudly():
    with pytest.raises(PromptSyntaxError):
        expand_prompt('[[#a]]{[[#b]]B|}\n[[RULES]]\na -> !b\na -> b\n[[/RULES]]', seed=1)

def test_inline_rules_expand_to_plain_string():
    template = (
        'A {warm|cool} portrait{, [[#glasses]]glasses|} '
        '{with [[#blindfold]]blindfold|without blindfold}\n'
        '[[RULES]]\n'
        'blindfold -> !glasses\n'
        '[[/RULES]]\n'
    )
    for seed in range(500):
        result = expand_prompt(template, seed=seed)
        assert '[[RULES]]' not in result and '[[/RULES]]' not in result
        assert '[[#' not in result and '{' not in result and '}' not in result
        assert not ('with blindfold' in result and 'glasses' in result)
        assert result == expand_prompt(template, seed=seed)

def test_inline_positive_rule():
    template = '{[[#rain]]rain|sun} {[[#umbrella]]umbrella|hat}\n[[RULES]]\nrain -> umbrella\n[[/RULES]]'
    for seed in range(100):
        result = expand_prompt(template, seed=seed)
        assert not (result.startswith('rain') and 'umbrella' not in result)


def test_plain_template_without_rules_still_expands():
    outputs = {expand_prompt('{a|b} {x|y}', seed=i) for i in range(50)}
    assert len(outputs) > 1
    assert all('{' not in item for item in outputs)

def test_invalid_inline_rules_placement():
    examples = [
        'A {a|b}\n[[RULES]]\nx -> y',
        'A {a|b}\n[[/RULES]]',
        'A {a|b}\n[[RULES]]\nx -> y\n[[/RULES]]\nextraneous',
        'A {a|b}\n[[RULES]]\na -> b\n[[/RULES]]\n[[RULES]]\n',
    ]
    for template in examples:
        with pytest.raises(PromptSyntaxError):
            expand_prompt(template, seed=0)


def test_node_signature_uses_only_template_and_seed():
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'northmere_instruments' / 'dynamic_prompt.py').read_text()
    tree = ast.parse(source)
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'DynamicPromptNode')
    execute = next(item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == 'execute')
    assert [argument.arg for argument in execute.args.args] == ['cls', 'template', 'seed']
    assert "io.String.Input('rules'" not in source

def test_malformed_inline_rules_are_not_emitted_as_prompt_text():
    with pytest.raises(PromptSyntaxError):
        expand_prompt('A {sun|rain}\n[[RULES]]\nbroken rule!\n[[/RULES]]', seed=0)

def test_expansion_is_the_string_output_not_unresolved_markup():
    template = '{a|b} {[[#warm]]warm|cold}\n[[RULES]]\nwarm -> !missing\n[[/RULES]]'
    with pytest.raises(PromptSyntaxError, match='Unknown target tag'):
        expand_prompt(template, seed=0)
    template = '{a|b} {[[#warm]]warm|cold}\n[[RULES]]\n# a comment\n[[/RULES]]'
    for seed in range(100):
        output = expand_prompt(template, seed=seed)
        assert not any(marker in output for marker in ('[[', '{', '}', '|'))
        assert output in ('a warm', 'a cold', 'b warm', 'b cold')

def test_nested_optional_forbidden_tag_does_not_remove_valid_parent_option():
    template = (
        '[[#blindfold]]blindfold {A|{[[#glasses]]glasses|clear}}'
        '\n[[RULES]]\nblindfold -> !glasses\n[[/RULES]]'
    )
    outputs = {expand_prompt(template, seed=seed) for seed in range(150)}
    assert 'blindfold A' in outputs
    assert 'blindfold clear' in outputs
    assert all('glasses' not in result for result in outputs)

def test_multiple_nested_levels_preserve_viable_branches():
    template = (
        '[[#driver]]D {outside|{inner|{[[#blocked]]bad|good}}}'
        '\n[[RULES]]\ndriver -> !blocked\n[[/RULES]]'
    )
    outputs = {expand_prompt(template, seed=seed) for seed in range(200)}
    assert 'D good' in outputs
    assert 'D inner' in outputs
    assert all('bad' not in result for result in outputs)


def test_rare_valid_positive_dependencies_are_found():
    # Previously 512 independent retries could fail even though a solution exists.
    choices = ' '.join(f'{{[[#b{i}]]yes{i}|no{i}}}' for i in range(12))
    rules = '\n'.join(f'a -> b{i}' for i in range(12))
    template = f'[[#a]] {choices}\n[[RULES]]\n{rules}\n[[/RULES]]'
    for seed in range(20):
        result = expand_prompt(template, seed)
        assert all(f'yes{i}' in result.split() for i in range(12))


def test_conflict_inside_one_alternative_is_pruned():
    template = '{[[#a]]A [[#b]]B|safe}\n[[RULES]]\na -> !b\n[[/RULES]]'
    assert all(expand_prompt(template, seed) == 'safe' for seed in range(30))


def test_impossible_indirect_dependencies_are_distinguished_from_search_limit():
    template = '[[#a]] {[[#b]]B|[[#c]]C}\n[[RULES]]\na -> b\na -> c\n[[/RULES]]'
    with pytest.raises(PromptSyntaxError, match='No rule-consistent expansion exists'):
        expand_prompt(template, seed=0)


def test_search_limit_has_its_own_error(monkeypatch):
    from northmere_instruments import dynamic_prompt_engine as engine
    monkeypatch.setattr(engine, 'MAX_SEARCH_STATES', 1)
    template = '{[[#a]]A|B} {[[#b]]X|Y}\n[[RULES]]\na -> b\n[[/RULES]]'
    with pytest.raises(PromptSyntaxError, match='search limit'):
        expand_prompt(template, seed=0)


def test_positive_and_negative_rules_hold_across_many_seeds():
    template = (
        '{[[#a]]A|other} '
        '{[[#b]]B|no-B} '
        '{[[#c]]C|no-C}\n'
        '[[RULES]]\na -> b\nb -> !c\n[[/RULES]]'
    )
    for seed in range(500):
        result = expand_prompt(template, seed)
        a, b, c = result.split()
        assert a != 'A' or b == 'B'
        assert b != 'B' or c != 'C'


def test_unconstrained_seed_mapping_stays_compatible():
    template = 'A {red|{green|blue}} hat and {small|large} bag'
    assert [expand_prompt(template, seed) for seed in (0, 1, 2, 42, 99)] == [
        'A blue hat and small bag',
        'A red hat and small bag',
        'A red hat and small bag',
        'A red hat and small bag',
        'A blue hat and small bag',
    ]

# Scene-aware prompt additions: definition reuse, conditional text, soft rules.

def test_definitions_expand_fresh_per_use_and_allow_forward_references():
    template = (
        '[[USE gaze]] / [[USE gaze]]\n'
        '[[DEF gaze]]\n{left|right}\n[[/DEF]]\n'
    )
    results = {expand_prompt(template, seed).strip() for seed in range(120)}
    assert results == {'left / left', 'left / right', 'right / left', 'right / right'}
    assert expand_prompt(template, 18) == expand_prompt(template, 18)


def test_definitions_do_not_leave_leading_or_trailing_blank_lines():
    template = '[[DEF note]]\nclear details\n[[/DEF]]\n\n[[USE note]]\n'
    assert expand_prompt(template) == 'clear details'


def test_nested_definitions_and_unused_tags_do_not_affect_constraints():
    template = (
        '[[DEF lighting]]\n[[USE direction]] light\n[[/DEF]]\n'
        '[[DEF direction]]\n{soft|bright}\n[[/DEF]]\n'
        '[[DEF unused]]\n[[#forbidden]]invalid\n[[/DEF]]\n'
        '[[#selected]]Scene: [[USE lighting]]\n'
        '[[RULES]]\nselected -> !forbidden\n[[/RULES]]'
    )
    # An unused definition cannot contribute a tag, so the rule must reject
    # a target that exists only inside that unused definition.
    with pytest.raises(PromptSyntaxError, match='Unknown target tag'):
        expand_prompt(template)
    assert {expand_prompt(template.split('[[RULES]]')[0], s).strip() for s in range(40)} == {
        'Scene: soft light', 'Scene: bright light',
    }


@pytest.mark.parametrize('source, message', [
    ('[[USE missing]]', 'Unknown definition'),
    ('[[DEF x]]\na\n[[/DEF]]\n[[DEF x]]\nb\n[[/DEF]]\n[[USE x]]', 'Duplicate definition'),
    ('[[DEF a]]\n[[USE b]]\n[[/DEF]]\n[[DEF b]]\n[[USE a]]\n[[/DEF]]\n[[USE a]]',
     'Recursive definition'),
    ('[[DEF a]] body [[/DEF]]', 'standalone'),
    ('[[USE bad name]]', 'Invalid'),
])
def test_reference_errors_are_explicit(source, message):
    with pytest.raises(PromptSyntaxError, match=message):
        expand_prompt(source)


def test_when_uses_final_tags_even_when_text_precedes_tag_and_is_nested():
    template = (
        'Photograph[[WHEN indoors]], soft ambient light[[/WHEN]]. '
        '{[[#indoors]]Inside|Outside}. '
        '{detail[[WHEN indoors]] indoors[[/WHEN]]|plain}'
    )
    outputs = {expand_prompt(template, seed) for seed in range(80)}
    assert any('Inside' in output and 'ambient light' in output for output in outputs)
    assert any('Outside' in output and 'ambient light' not in output for output in outputs)
    assert all(('Inside' in output) == ('ambient light' in output) for output in outputs)
    assert all(('Outside' not in output) == ('detail indoors' in output)
               for output in outputs if 'detail' in output)


def test_when_works_after_definition_substitution():
    text = (
        '[[DEF face_note]]\nHer face is out of frame.\n[[/DEF]]\n'
        '[[WHEN partner]][[USE face_note]][[/WHEN]] '
        '{[[#partner]]two people|one person}'
    )
    for seed in range(40):
        output = expand_prompt(text, seed)
        assert ('Her face' in output) == ('two people' in output)


@pytest.mark.parametrize('text, message', [
    ('[[WHEN missing]]text[[/WHEN]]', 'Unknown conditional tag'),
    ('[[WHEN x]]text', 'Unclosed'),
    ('[[/WHEN]]', 'Unexpected'),
    ('[[WHEN x]]{a|b}[[/WHEN]] [[#x]]', 'plain text'),
    ('[[WHEN x]][[#y]]bad[[/WHEN]] [[#x]] [[#y]]', 'plain text'),
    ('[[WHEN x]][[WHEN y]]nested[[/WHEN]][[/WHEN]] [[#x]] [[#y]]', 'plain text'),
])
def test_when_rejects_ambiguous_markup(text, message):
    with pytest.raises(PromptSyntaxError, match=message):
        expand_prompt(text)


def test_soft_rule_biases_target_without_biasing_source():
    # Target appears first in the template: source-first selection must still
    # make the context effective, but not make the source itself more common.
    template = (
        '{[[#light]]window light|flash}, {[[#room]]window room|plain backdrop}\n'
        '[[RULES]]\nroom ~> light @ 4\n[[/RULES]]'
    )
    outcomes = [expand_prompt(template, seed) for seed in range(4000)]
    room = [item for item in outcomes if 'window room' in item]
    backdrop = [item for item in outcomes if 'plain backdrop' in item]
    assert 1700 < len(room) < 2300  # P(source) ~= 1/2
    assert 0.75 < sum(item.startswith('window light') for item in room) / len(room) < 0.85
    assert 0.45 < sum(item.startswith('window light') for item in backdrop) / len(backdrop) < 0.55
    assert expand_prompt(template, 42) == expand_prompt(template, 42)


def test_soft_discouragement_keeps_unfavored_option_available():
    template = (
        '[[#small]]Small area, {[[#far]]long lens|normal lens}\n'
        '[[RULES]]\nsmall ~> far @ 0.1\n[[/RULES]]'
    )
    outputs = [expand_prompt(template, seed) for seed in range(1000)]
    count = sum('long lens' in item for item in outputs)
    assert 50 < count < 150


def test_hard_rules_take_precedence_over_soft_preferences():
    template = (
        '[[#small]]small room {[[#far]]far shot|[[#near]]near shot}\n'
        '[[RULES]]\nsmall -> !far\nsmall ~> far @ 1000\n[[/RULES]]'
    )
    assert all('near shot' in expand_prompt(template, seed) for seed in range(100))


def test_soft_rule_sources_are_chosen_in_dependency_order():
    template = (
        '{[[#c]]C|plain-c} {[[#b]]B|plain-b} {[[#a]]A|plain-a}\n'
        '[[RULES]]\na ~> b @ 100\nb ~> c @ 100\n[[/RULES]]'
    )
    outputs = [expand_prompt(template, seed) for seed in range(600)]
    chosen_a = [o for o in outputs if o.endswith(' A')]
    assert 230 < len(chosen_a) < 370
    assert sum(' B ' in o for o in chosen_a) / len(chosen_a) > 0.95
    assert sum(o.startswith('C ') for o in chosen_a) / len(chosen_a) > 0.9


@pytest.mark.parametrize('rule, match', [
    ('a ~> b @ 0', 'Invalid soft rule'),
    ('a ~> b @ -2', 'Invalid rule'),
    ('a ~> b @ nan', 'Invalid rule'),
    ('a ~> b @ 1e999', 'Invalid soft rule'),
    ('a ~> a @ 2', 'Invalid soft rule'),
    ('a ~> missing @ 2', 'Unknown tag'),
    ('a ~> b @ 2\na ~> b @ 3', 'Duplicate soft rule'),
    ('a ~> b @ 2\nb ~> a @ 3', 'Cyclic soft'),
])
def test_bad_soft_rules_fail_clearly(rule, match):
    template = f'[[#a]]A {{[[#b]]B|none}}\n[[RULES]]\n{rule}\n[[/RULES]]'
    with pytest.raises(PromptSyntaxError, match=match):
        expand_prompt(template)


def test_soft_rule_with_nested_target_works_when_target_becomes_guaranteed():
    template = (
        '[[#room]]room {neutral|{[[#soft]]soft light|hard light}}\n'
        '[[RULES]]\nroom ~> soft @ 20\n[[/RULES]]'
    )
    outputs = [expand_prompt(template, seed) for seed in range(1000)]
    assert sum('soft light' in o for o in outputs) > 400
    assert any('neutral' in o for o in outputs)


def test_expansion_size_limit(monkeypatch):
    from northmere_instruments import dynamic_prompt_engine as engine
    monkeypatch.setattr(engine, 'MAX_EXPANDED_CHARS', 8)
    with pytest.raises(PromptSyntaxError, match='Expanded prompt exceeds'):
        expand_prompt('[[DEF a]]\nhello!\n[[/DEF]]\n[[USE a]][[USE a]]')


def test_definitions_conditions_soft_and_hard_rules_compose():
    template = (
        '[[DEF illumination]]\n{[[#soft]]soft daylight|[[#flash]]flash}\n[[/DEF]]\n'
        '{[[#window]]window room|[[#studio]]studio} with [[USE illumination]]. '
        '[[WHEN window]]A window frame is visible. [[/WHEN]]'
        '[[WHEN soft]]Soft shadows are visible.[[/WHEN]]\n'
        '[[RULES]]\nwindow -> !flash\nwindow ~> soft @ 6\n[[/RULES]]'
    )
    for seed in range(300):
        result = expand_prompt(template, seed)
        assert '[[' not in result and '{' not in result
        if 'window room' in result:
            assert 'flash' not in result and 'soft daylight' in result
            assert 'A window frame is visible.' in result
        if 'studio' in result:
            assert 'A window frame is visible.' not in result
        assert ('Soft shadows are visible.' in result) == ('soft daylight' in result)
