"""Scoped, model-only LoRA injection for supported linear PyTorch layers.

No hooks are registered at node construction time. During a model forward, hooks
are installed and removed in a ``finally`` block, including on failures.

This remains an experimental approach to ComfyUI's model execution lifecycle.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import logging
import math
from threading import RLock
from typing import Any, Callable, Protocol

import torch
import torch.nn.functional as F

from .curve import StrengthCurve, sigma_to_progress


class SamplingSchedule(Protocol):
    """Subset of ComfyUI model_sampling used to normalize denoising progress."""

    def percent_to_sigma(self, percent: float) -> float: ...

LOGGER = logging.getLogger(__name__)
# Serialize our temporary module-hook window. The model may be shared by cloned
# ModelPatchers, and simultaneous hook installation would double-apply adapters.
_FORWARD_LOCK = RLock()


@dataclass(frozen=True)
class Layer:
    up: torch.Tensor
    down: torch.Tensor
    alpha_scale: float


@dataclass(frozen=True)
class BoundLayer:
    module: torch.nn.Module
    weights: Layer


@dataclass(frozen=True, eq=False)
class Profile:
    layers: dict[str, Layer]
    schedule: StrengthCurve
    apply_to: str
    sampling: SamplingSchedule
    bound_layers: tuple[BoundLayer, ...] = ()


@dataclass(frozen=True)
class ActiveProfile:
    profile: Profile
    strength: float


def parse_layers(state: dict[str, Any], key_map: dict[str, str]) -> tuple[dict[str, Layer], int]:
    """Extract traditional linear LoRA A/B matrices from a ComfyUI key map.

    Unsupported or incomplete LoRA groups are counted rather than guessed.
    """
    groups: dict[str, dict[str, Any]] = {}
    tails = (
        ('.lora_down.weight', 'down'), ('.lora_up.weight', 'up'),
        ('.lora_A.weight', 'down'), ('.lora_B.weight', 'up'),
        ('.alpha', 'alpha'),
    )
    for key, value in state.items():
        for suffix, part in tails:
            if key.endswith(suffix):
                groups.setdefault(key[:-len(suffix)], {})[part] = value
                break

    result: dict[str, Layer] = {}
    unmatched = 0
    for prefix, group in groups.items():
        target = key_map.get(prefix)
        up, down = group.get('up'), group.get('down')
        if not target or not isinstance(up, torch.Tensor) or not isinstance(down, torch.Tensor):
            unmatched += 1
            continue
        if (up.ndim != 2 or down.ndim != 2 or down.shape[0] != up.shape[1]
                or down.shape[0] <= 0):
            unmatched += 1
            continue
        rank = down.shape[0]
        alpha = group.get('alpha', rank)
        try:
            alpha_value = float(alpha.item() if isinstance(alpha, torch.Tensor) else alpha)
        except (ValueError, TypeError, RuntimeError):
            unmatched += 1
            continue
        if not math.isfinite(alpha_value):
            unmatched += 1
            continue
        if target in result:
            raise ValueError(f'Duplicate LoRA target: {target}')
        result[target] = Layer(
            up=up.to(device='cpu', dtype=torch.float32),
            down=down.to(device='cpu', dtype=torch.float32),
            alpha_scale=alpha_value / rank,
        )
    return result, unmatched


def submodule(root: Any, path: str) -> Any | None:
    module = root
    for part in path.split('.'):
        if not part:
            continue
        if part.isdigit():
            try:
                module = module[int(part)]
            except (IndexError, TypeError, KeyError):
                return None
        else:
            module = getattr(module, part, None)
        if module is None:
            return None
    return module


def target_path(mapped_name: str) -> str:
    if mapped_name.startswith('diffusion_model.'):
        mapped_name = mapped_name[len('diffusion_model.'):]
    if mapped_name.endswith('.weight'):
        mapped_name = mapped_name[:-len('.weight')]
    return mapped_name


def resolve_layers(diffusion_model: torch.nn.Module, layers: dict[str, Layer]) -> tuple[tuple[BoundLayer, ...], int]:
    """Validate module targets without mutating model weights or attaching hooks."""
    bound: list[BoundLayer] = []
    missing = 0
    for name, weights in layers.items():
        module = submodule(diffusion_model, target_path(name))
        weight = getattr(module, 'weight', None) if module is not None else None
        if (not isinstance(module, torch.nn.Module) or not isinstance(weight, torch.Tensor)
                or weight.ndim != 2 or tuple(weight.shape) != (weights.up.shape[0], weights.down.shape[1])):
            missing += 1
            continue
        bound.append(BoundLayer(module, weights))
    return tuple(bound), missing


def _mask(batch: int, labels: tuple[int, ...] | None, mode: str,
          device: torch.device, dtype: torch.dtype) -> torch.Tensor | None:
    if mode == 'both':
        return None
    if mode not in ('positive', 'negative'):
        raise ValueError(f'Unsupported LoRA branch selection: {mode}')
    if not labels or batch % len(labels):
        raise ValueError(
            'Cannot identify ComfyUI CFG branches for conditional LoRA routing. '
            'Use apply_to=both or a sampler that supplies cond_or_uncond labels.'
        )
    chunk = batch // len(labels)
    values = [float((label == 0) if mode == 'positive' else (label != 0)) for label in labels]
    return torch.tensor(values, device=device, dtype=dtype).repeat_interleave(chunk)


def _make_hook(
    layer: Layer, strength: float, labels: tuple[int, ...] | None, apply_to: str,
) -> Callable[[torch.nn.Module, tuple[Any, ...], Any], Any]:
    """Return one per-call hook. No global activation/profile lookup is needed."""
    def hook(_module: torch.nn.Module, inputs: tuple[Any, ...], output: Any) -> Any:
        if (abs(strength) < 1e-10 or not inputs or not isinstance(inputs[0], torch.Tensor)
                or not isinstance(output, torch.Tensor)):
            return output
        x = inputs[0]
        if x.ndim < 2 or not x.is_floating_point():
            return output
        dtype = x.dtype if x.dtype in (torch.float16, torch.bfloat16, torch.float32) else torch.float32
        down = layer.down.to(device=x.device, dtype=dtype)
        up = layer.up.to(device=x.device, dtype=dtype)
        try:
            contribution = F.linear(F.linear(x.to(dtype), down), up)
        except RuntimeError as error:
            raise RuntimeError(
                f'Northmere linear LoRA dimension mismatch: input={tuple(x.shape)} '
                f'down={tuple(down.shape)} up={tuple(up.shape)}'
            ) from error
        mask = _mask(contribution.shape[0], labels, apply_to, contribution.device, contribution.dtype)
        if mask is not None:
            contribution = contribution * mask.view(-1, *([1] * (contribution.ndim - 1)))
        return output + (contribution * (layer.alpha_scale * strength)).to(output.dtype)

    return hook


def install_wrapper(model: Any, profile: Profile) -> Any:
    """Create a ModelPatcher clone whose hooks live only during model calls."""
    if not profile.bound_layers:
        raise ValueError('Cannot schedule LoRA without matching model linear layers')
    clone = model.clone()
    profiles = tuple(getattr(model, '_northmere_profiles', ())) + (profile,)
    clone._northmere_profiles = profiles

    existing = getattr(model, 'model_options', {}).get('model_function_wrapper')
    if existing is not None and not getattr(model, '_northmere_profiles', ()):
        raise RuntimeError('Existing model_function_wrapper cannot safely be replaced by Northmere Instruments')

    def forward_wrapper(apply_model: Callable[..., Any], args: dict[str, Any]) -> Any:
        timestep = args['timestep']
        sigma = float(timestep.reshape(-1)[0]) if isinstance(timestep, torch.Tensor) else float(timestep)
        cond = args.get('c') or {}
        transformer_options = cond.get('transformer_options') or {}
        raw_labels = transformer_options.get('cond_or_uncond')
        labels = tuple(int(label) for label in raw_labels) if raw_labels is not None else None
        active = (
            ActiveProfile(candidate, float(candidate.schedule.at(sigma_to_progress(candidate.sampling, sigma))))
            for candidate in profiles
        )
        # Keep all hook registrations within one locked forward call. Handles
        # nested calls via RLock and guarantees cleanup even if the model raises.
        with _FORWARD_LOCK:
            with ExitStack() as stack:
                for entry in active:
                    for bound in entry.profile.bound_layers:
                        handle = bound.module.register_forward_hook(
                            _make_hook(bound.weights, entry.strength, labels, entry.profile.apply_to)
                        )
                        stack.callback(handle.remove)
                return apply_model(args['input'], timestep, **cond)

    clone.set_model_unet_function_wrapper(forward_wrapper)
    return clone
