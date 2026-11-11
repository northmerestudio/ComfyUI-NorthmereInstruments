"""Regression tests for error handling, signed strengths, and wrapper lifetimes."""
from __future__ import annotations

import pytest
import torch

from northmere_instruments.envelope import DEFAULT_CURVE, LoRAEnvelope
from northmere_instruments.engine import Layer, Profile, resolve_layers, install_wrapper, parse_layers


class LinearSampling:
    def percent_to_sigma(self, p: float) -> float:
        return 10.0 * (1.0 - p)


class ToyDiffusion(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.first = torch.nn.Linear(3, 2, bias=False)
        torch.nn.init.zeros_(self.first.weight)


class Model:
    def __init__(self) -> None:
        self.diffusion_model = ToyDiffusion()
        self.model_sampling = LinearSampling()


class Patcher:
    def __init__(self) -> None:
        self.model = Model()
        self.model_options: dict = {}
        self.wrapper = None

    def clone(self):
        result = Patcher.__new__(Patcher)
        result.model = self.model
        result.model_options = dict(self.model_options)
        return result

    def set_model_unet_function_wrapper(self, callback):
        self.wrapper = callback


def test_negative_and_amplified_curves():
    curve = LoRAEnvelope.from_json({"version": 1, "interpolation": "linear", "points": [[0, -1.5], [1, 2.5]]})
    assert curve.at(0) == -1.5
    assert curve.at(0.5) == 0.5
    assert curve.at(1) == 2.5


@pytest.mark.parametrize("payload", [
    {"version": 1, "points": [[0, 1], [1, float("nan")]]},
    {"version": 1, "points": [[0, 1], [0.5, 2], [0.5, 3], [1, 4]]},
    {"version": 1, "points": [[0, 1], [1, 21]]},
    {"version": 2, "points": [[0, 1], [1, 2]]},
])
def test_invalid_curve_rejected(payload):
    with pytest.raises(ValueError):
        LoRAEnvelope.from_json(payload)


def test_unmatched_layer_not_silently_accepted():
    weights = {"unmatched.lora_down.weight": torch.ones(2, 3),
               "unmatched.lora_up.weight": torch.ones(2, 2)}
    layers, skipped = parse_layers(weights, {})
    assert not layers and skipped == 1


def test_duplicate_weight_mapping_rejected():
    weights = {"a.lora_down.weight": torch.ones(2, 3), "a.lora_up.weight": torch.ones(2, 2),
               "b.lora_down.weight": torch.ones(2, 3), "b.lora_up.weight": torch.ones(2, 2)}
    with pytest.raises(ValueError, match="Duplicate"):
        parse_layers(weights, {"a": "diffusion_model.first.weight", "b": "diffusion_model.first.weight"})


def test_profile_is_inactive_outside_wrapper():
    patcher = Patcher()
    layer = Layer(torch.ones(2, 2), torch.ones(2, 3), 1.0)
    profile = Profile({"diffusion_model.first.weight": layer}, LoRAEnvelope.from_json(DEFAULT_CURVE),
                      "both", patcher.model.model_sampling)
    bound, skipped = resolve_layers(patcher.model.diffusion_model, profile.layers)
    assert len(bound) == 1 and skipped == 0
    profile = Profile(profile.layers, profile.schedule, profile.apply_to, profile.sampling, bound)
    modified = install_wrapper(patcher, profile)
    x = torch.ones(1, 3)
    assert torch.allclose(patcher.model.diffusion_model.first(x), torch.zeros(1, 2))
    output = modified.wrapper(lambda input, ts, **kwargs: patcher.model.diffusion_model.first(input),
                              {"input": x, "timestep": torch.tensor([10]), "c": {}})
    assert torch.allclose(output, torch.full((1, 2), 3.0))
    assert torch.allclose(patcher.model.diffusion_model.first(x), torch.zeros(1, 2))


def test_unrelated_model_wrapper_is_not_overwritten():
    patcher = Patcher()
    patcher.model_options["model_function_wrapper"] = object()
    profile = Profile({}, LoRAEnvelope.from_json(DEFAULT_CURVE), "both", patcher.model.model_sampling,
                      (object(),))
    with pytest.raises(RuntimeError, match="Existing model_function_wrapper"):
        install_wrapper(patcher, profile)
