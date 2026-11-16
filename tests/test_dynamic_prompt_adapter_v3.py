"""Offline ComfyUI V3 adapter smoke test for the breaking prompt language."""
import importlib
import sys
from types import ModuleType, SimpleNamespace


def test_dynamic_prompt_schema_and_execution(monkeypatch):
    class Input:
        def __init__(self, name=None, **kwargs):
            self.name, self.kwargs = name, kwargs

    class Schema:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Output:
        def __init__(self, value):
            self.value = value

    latest = ModuleType('comfy_api.latest')
    latest.io = SimpleNamespace(ComfyNode=object, Schema=Schema,
                                String=SimpleNamespace(Input=Input, Output=Input),
                                Int=SimpleNamespace(Input=Input), NodeOutput=Output)
    monkeypatch.setitem(sys.modules, 'comfy_api.latest', latest)
    from northmere_instruments import dynamic_prompt
    importlib.reload(dynamic_prompt)
    schema = dynamic_prompt.DynamicPromptNode.define_schema()
    assert schema.node_id == 'NorthmereInstruments.DynamicPrompt'
    assert [x.name for x in schema.inputs] == ['template', 'seed']
    assert schema.inputs[0].widget_type == 'NORTHMERE_INSTRUMENTS_PROMPT'
    assert schema.inputs[0].socketless is True
    assert schema.inputs[0].kwargs['multiline'] is True
    assert schema.outputs[0].kwargs['display_name'] == 'prompt'
    result = dynamic_prompt.DynamicPromptNode.execute('{day => sun | night => moon}', seed=4)
    assert result.value in ('sun', 'moon')
