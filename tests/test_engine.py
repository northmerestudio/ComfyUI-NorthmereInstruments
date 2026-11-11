"""Behavioral tests for scoped LoRA application and cleanup."""
from __future__ import annotations

import pytest
import torch

from northmere_instruments.envelope import DEFAULT_CURVE, LoRAEnvelope
from northmere_instruments.engine import Layer, Profile, _mask, install_wrapper, parse_layers, resolve_layers


class Sampling:
    def percent_to_sigma(self, p: float) -> float:
        return 10 * (1 - p)


class Diffusion(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.first = torch.nn.Linear(3, 4, bias=False)
        with torch.no_grad():
            self.first.weight.zero_()


class Base:
    def __init__(self) -> None:
        self.diffusion_model = Diffusion()
        self.model_sampling = Sampling()


class Patcher:
    def __init__(self) -> None:
        self.model = Base()
        self.model_options = {}
        self.wrapper = None

    def clone(self):
        new = Patcher.__new__(Patcher)
        new.model = self.model
        new.model_options = dict(self.model_options)
        new.wrapper = self.wrapper
        return new

    def set_model_unet_function_wrapper(self, wrapper) -> None:
        self.wrapper = wrapper
        self.model_options['model_function_wrapper'] = wrapper


def make_profile(patcher: Patcher) -> Profile:
    weights = Layer(torch.ones(4, 2), torch.ones(2, 3), 1.0)
    layers = {'diffusion_model.first.weight': weights}
    bound, skipped = resolve_layers(patcher.model.diffusion_model, layers)
    assert skipped == 0
    assert len(bound) == 1
    return Profile(layers, LoRAEnvelope.from_json(DEFAULT_CURVE), 'both',
                   patcher.model.model_sampling, bound)


def test_parse_lora() -> None:
    state = {'layer.lora_down.weight': torch.ones((2, 3)),
             'layer.lora_up.weight': torch.ones((4, 2)),
             'layer.alpha': torch.tensor(1.)}
    layers, missing = parse_layers(state, {'layer': 'diffusion_model.first.weight'})
    assert missing == 0
    assert layers['diffusion_model.first.weight'].alpha_scale == pytest.approx(.5)


def test_resolve_layers_has_no_side_effects() -> None:
    patcher = Patcher()
    module = patcher.model.diffusion_model.first
    assert len(module._forward_hooks) == 0
    make_profile(patcher)
    assert len(module._forward_hooks) == 0


def test_scoped_hook_bfloat16_and_cleanup() -> None:
    patcher = Patcher()
    module = patcher.model.diffusion_model.first
    modified = install_wrapper(patcher, make_profile(patcher))
    x = torch.ones(1, 3)
    assert torch.count_nonzero(module(x)) == 0
    for _ in range(3):
        result = modified.wrapper(lambda xx, ts, **kwargs: module(xx),
                                  {'input': x, 'timestep': torch.tensor([10.]), 'c': {}})
        assert torch.allclose(result, torch.full((1, 4), 3.0))
        assert len(module._forward_hooks) == 0
        assert torch.count_nonzero(module(x)) == 0
    module.to(torch.bfloat16)
    xb = x.to(torch.bfloat16)
    output = modified.wrapper(lambda xx, ts, **kwargs: module(xx),
                              {'input': xb, 'timestep': torch.tensor([10.]), 'c': {}})
    assert output.dtype == torch.bfloat16
    assert torch.all(output == torch.tensor(3., dtype=torch.bfloat16))
    assert len(module._forward_hooks) == 0


def test_hooks_removed_on_model_failure() -> None:
    patcher = Patcher()
    modified = install_wrapper(patcher, make_profile(patcher))
    module = patcher.model.diffusion_model.first
    def failing_forward(xx, ts, **kwargs):
        module(xx)
        raise RuntimeError('deliberate test failure')
    with pytest.raises(RuntimeError, match='deliberate'):
        modified.wrapper(failing_forward, {'input': torch.ones(1, 3),
                                          'timestep': torch.tensor([10.]), 'c': {}})
    assert len(module._forward_hooks) == 0


def test_chained_profiles_add_once_each() -> None:
    patcher = Patcher()
    profile = make_profile(patcher)
    first = install_wrapper(patcher, profile)
    second = install_wrapper(first, profile)
    module = patcher.model.diffusion_model.first
    result = second.wrapper(lambda xx, ts, **kwargs: module(xx),
                            {'input': torch.ones(1, 3), 'timestep': 10., 'c': {}})
    assert torch.allclose(result, torch.full((1, 4), 6.0))
    assert len(module._forward_hooks) == 0


def test_branch_mask_requires_valid_labels() -> None:
    with pytest.raises(ValueError, match='CFG branches'):
        _mask(2, None, 'positive', torch.device('cpu'), torch.float32)
    mask = _mask(4, (0, 1), 'positive', torch.device('cpu'), torch.float32)
    assert mask.tolist() == [1, 1, 0, 0]
