"""Contract-level ComfyUI V3 fixture and synthetic sampling integration.

This intentionally does not claim to replace running a real ComfyUI server. It
checks the plugin entrypoint, schema shape and node execution without a GPU.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]


class _Input:
    def __init__(self, id: str, **kwargs: object) -> None:
        self.id = id
        self.__dict__.update(kwargs)


class _Output(_Input):
    def __init__(self, id: str | None = None, **kwargs: object) -> None:
        super().__init__(id or "", **kwargs)


class _Combo(_Input):
    def __init__(self, id: str, options: list[str], **kwargs: object) -> None:
        super().__init__(id, options=options, **kwargs)


class _Schema:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _NodeOutput:
    def __init__(self, model: object) -> None:
        self.model = model


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    comfy = ModuleType('comfy')
    comfy.__path__ = []
    lora = ModuleType('comfy.lora')
    utils = ModuleType('comfy.utils')
    comfy.lora, comfy.utils = lora, utils
    monkeypatch.setitem(sys.modules, 'comfy', comfy)
    monkeypatch.setitem(sys.modules, 'comfy.lora', lora)
    monkeypatch.setitem(sys.modules, 'comfy.utils', utils)

    folder_paths = ModuleType('folder_paths')
    folder_paths.get_filename_list = lambda folder: ['supported.safetensors', 'partial.safetensors']
    folder_paths.get_full_path = lambda folder, name: str(tmp_path / name)
    monkeypatch.setitem(sys.modules, 'folder_paths', folder_paths)

    io = SimpleNamespace(
        ComfyNode=type('ComfyNode', (), {}),
        Schema=_Schema,
        Model=SimpleNamespace(Input=_Input, Output=_Output),
        String=SimpleNamespace(Input=_Input),
        Combo=SimpleNamespace(Input=_Combo),
        NodeOutput=_NodeOutput,
    )
    latest = ModuleType('comfy_api.latest')
    latest.io = io
    latest.ComfyExtension = type('ComfyExtension', (), {})
    api = ModuleType('comfy_api')
    api.__path__ = []
    monkeypatch.setitem(sys.modules, 'comfy_api', api)
    monkeypatch.setitem(sys.modules, 'comfy_api.latest', latest)

    pkg_name = 'northmere_test_extension'
    spec = importlib.util.spec_from_file_location(
        pkg_name, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)]
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = package
    spec.loader.exec_module(package)
    extension = asyncio.run(package.comfy_entrypoint())
    node_class, = asyncio.run(extension.get_node_list())
    yield node_class, lora, utils
    for key in list(sys.modules):
        if key.startswith(pkg_name):
            sys.modules.pop(key, None)


class _Model:
    def __init__(self) -> None:
        self.diffusion_model = torch.nn.Module()
        self.diffusion_model.first = torch.nn.Linear(3, 2, bias=False)
        torch.nn.init.zeros_(self.diffusion_model.first.weight)
        self.model_sampling = SimpleNamespace(percent_to_sigma=lambda p: 10 * (1 - p))


class _Patcher:
    def __init__(self) -> None:
        self.model = _Model()
        self.model_options = {}

    def clone(self):
        new = _Patcher.__new__(_Patcher)
        new.model = self.model
        new.model_options = dict(self.model_options)
        return new

    def set_model_unet_function_wrapper(self, wrapper):
        self.model_options['model_function_wrapper'] = wrapper
        self.wrapper = wrapper


def test_v3_schema_and_native_widget_contract(plugin) -> None:
    node, _lora, _utils = plugin
    schema = node.define_schema()
    assert schema.node_id == 'NorthmereInstruments.LoRAEnvelope'
    assert schema.display_name == 'LoRA Envelope'
    assert [item.id for item in schema.inputs] == ['model', 'lora_name', 'curve', 'apply_to']
    curve = schema.inputs[2]
    assert curve.widget_type == 'NORTHMERE_INSTRUMENTS_CURVE'
    assert curve.socketless is True
    from northmere_instruments.envelope import LoRAEnvelope
    assert len(LoRAEnvelope.from_json(curve.default).xs) == 9
    assert [out.display_name for out in schema.outputs] == ['MODEL']


def test_execute_applies_complete_supported_adapter(plugin) -> None:
    node, lora, utils = plugin
    model = _Patcher()
    lora.model_lora_keys_unet = lambda base, mapping: {'linear': 'diffusion_model.first.weight'}
    utils.load_torch_file = lambda path, safe_load: {
        'linear.lora_up.weight': torch.ones(2, 2),
        'linear.lora_down.weight': torch.ones(2, 3),
    }
    from northmere_instruments.envelope import DEFAULT_CURVE
    out = node.execute(model, 'supported.safetensors', DEFAULT_CURVE, 'both').model
    x = torch.ones(1, 3)
    actual = out.wrapper(
        lambda input, t, **kwargs: model.model.diffusion_model.first(input),
        {'input': x, 'timestep': torch.tensor([10.]), 'c': {}},
    )
    assert torch.allclose(actual, torch.full((1, 2), 3.0))
    assert not model.model.diffusion_model.first._forward_hooks


def test_execute_rejects_partially_mapped_adapter(plugin) -> None:
    node, lora, utils = plugin
    model = _Patcher()
    lora.model_lora_keys_unet = lambda base, mapping: {'linear': 'diffusion_model.first.weight'}
    utils.load_torch_file = lambda path, safe_load: {
        'linear.lora_up.weight': torch.ones(2, 2),
        'linear.lora_down.weight': torch.ones(2, 3),
        'unknown.lora_up.weight': torch.ones(2, 2),
        'unknown.lora_down.weight': torch.ones(2, 3),
    }
    from northmere_instruments.envelope import DEFAULT_CURVE
    with pytest.raises(ValueError, match='partially compatible'):
        node.execute(model, 'partial.safetensors', DEFAULT_CURVE, 'both')
