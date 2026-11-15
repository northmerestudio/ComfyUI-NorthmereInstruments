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
