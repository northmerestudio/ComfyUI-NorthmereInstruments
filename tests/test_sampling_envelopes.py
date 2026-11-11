"""Strength changes across model forwards, independently of sampler step count."""
from __future__ import annotations

import pytest
import torch

from northmere_instruments.curve import StrengthCurve
from northmere_instruments.engine import Layer, Profile, install_wrapper, parse_layers, resolve_layers


class _Model:
    def __init__(self) -> None:
        self.diffusion_model = torch.nn.Module()
        self.diffusion_model.first = torch.nn.Linear(3, 2, bias=False)
        torch.nn.init.zeros_(self.diffusion_model.first.weight)
        self.model_sampling = self

    def percent_to_sigma(self, progress: float) -> float:
        return 10.0 * (1.0 - progress)


class _Patcher:
    def __init__(self) -> None:
        self.model = _Model()
        self.model_options: dict = {}

    def clone(self):
        clone = _Patcher.__new__(_Patcher)
        clone.model = self.model
        clone.model_options = self.model_options.copy()
        return clone

    def set_model_unet_function_wrapper(self, wrapper):
        self.wrapper = wrapper
        self.model_options['model_function_wrapper'] = wrapper


@pytest.mark.parametrize('points', [
    [[0, 0], [1, 0]],
    [[0, 1], [1, 1]],
    [[0, 0], [1, 1]],
    [[0, 0], [0.5, 1], [1, 1]],
    [[0, -1], [1, 2]],
])
@pytest.mark.parametrize('sampler_steps', [8, 20, 40])
def test_envelope_matches_actual_progress_at_each_model_forward(points, sampler_steps):
    base = _Patcher()
    curve = StrengthCurve.from_json({'version': 1, 'interpolation': 'linear', 'points': points})
    weights = {'diffusion_model.first.weight': Layer(torch.ones(2, 2), torch.ones(2, 3), 1.)}
    bound, skipped = resolve_layers(base.model.diffusion_model, weights)
    assert skipped == 0
    wrapped = install_wrapper(base, Profile(weights, curve, 'both', base.model.model_sampling, bound))
    input_tensor = torch.ones(1, 3)
    for step in range(sampler_steps):
        progress = step / sampler_steps
        sigma = 10 * (1 - progress)
        actual = wrapped.wrapper(
            lambda x, t, **cond: base.model.diffusion_model.first(x),
            {'input': input_tensor, 'timestep': torch.tensor([sigma]), 'c': {}},
        )
        assert actual.detach().tolist()[0] == pytest.approx([6 * curve.at(progress)] * 2, abs=2e-5)
    assert len(base.model.diffusion_model.first._forward_hooks) == 0


def test_bad_adapter_alpha_is_counted_as_unsupported():
    state = {'x.lora_up.weight': torch.ones(2, 2), 'x.lora_down.weight': torch.ones(2, 3),
             'x.alpha': torch.tensor([1., 2.])}
    layers, rejected = parse_layers(state, {'x': 'diffusion_model.first.weight'})
    assert layers == {}
    assert rejected == 1
