"""Native V3 node definitions. The frontend graph editor is optional for API calls."""
from __future__ import annotations
import logging
from typing import Any
import comfy.lora
import comfy.utils
import folder_paths
from comfy_api.latest import io
from .engine import Profile, resolve_layers, install_wrapper, parse_layers
from .curve import DEFAULT_CURVE, StrengthCurve

LOGGER = logging.getLogger(__name__)
CATEGORY = 'Northmere Instruments/LoRA'


class EnvelopeInput(io.String.Input):
    """STRING data with a renderer-specific widget, preserves API-friendly JSON."""
    def __init__(self) -> None:
        super().__init__(
            'curve', default=DEFAULT_CURVE,
            tooltip='Edit LoRA weight against denoising progress. X 0=first/noisy, X 1=finished; '
                    'Y is actual LoRA strength (negative, zero, or above 1 allowed). '
                    'Edit keyframes using the graph or numeric fields.'
        )
        self.widget_type = 'NORTHMERE_INSTRUMENTS_CURVE'
        self.socketless = True


def _apply(model: Any, lora_name: str, curve: StrengthCurve, apply_to: str) -> Any:
    path = folder_paths.get_full_path('loras', lora_name)
    if not path:
        raise FileNotFoundError(f'Cannot find LoRA: {lora_name}')
    if not hasattr(model.model, 'diffusion_model'):
        raise ValueError('Expected a ComfyUI diffusion MODEL')
    state = comfy.utils.load_torch_file(path, safe_load=True)
    mapping = comfy.lora.model_lora_keys_unet(model.model, {})
    layers, unmapped = parse_layers(state, mapping)
    if not layers:
        raise ValueError('No compatible linear LoRA layers mapped to this model')
    bound, skipped = resolve_layers(model.model.diffusion_model, layers)
    if not bound:
        raise ValueError('No compatible linear modules found; check LoRA architecture')
    if skipped or unmapped:
        raise ValueError(
            f'LoRA {lora_name!r} is only partially compatible: {len(bound)} linear layers matched, '
            f'{skipped} unsupported model layers, {unmapped} unmapped/incomplete adapter groups. '
            'Northmere does not silently apply a partial LoRA. Use a compatible model-only linear LoRA.'
        )
    profile = Profile(layers=layers, schedule=curve, apply_to=apply_to,
                      sampling=model.model.model_sampling, bound_layers=bound)
    LOGGER.info('Scheduled LoRA %s matched=%d layers', lora_name, len(bound))
    return install_wrapper(model, profile)


class LoRAEnvelopeNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id='NorthmereInstruments.LoRAStrengthCurve', display_name='LoRA Envelope', category=CATEGORY,
            description='Modulate model-only LoRA weight over normalized denoising progress. '
                        'Independent of sampler step count; supports linear, smooth, and hold interpolation. '
                        'Works with a standard KSampler (no special sampler required).',
            search_aliases=['northmere', 'instruments', 'lora envelope', 'lora curve', 'lora schedule', 'dynamic lora'],
            inputs=[
                io.Model.Input('model', tooltip='Diffusion model. Connect the output to KSampler MODEL.'),
                io.Combo.Input('lora_name', options=folder_paths.get_filename_list('loras'),
                               tooltip='Choose a compatible diffusion-model LoRA. Text encoder weights are not scheduled.'),
                EnvelopeInput(),
                io.Combo.Input('apply_to', options=['both', 'positive', 'negative'], default='both',
                               tooltip='CFG branch selection. Use both for Krea Turbo / CFG=1. '
                                       'Positive/negative only require separate CFG branches.', advanced=True),
            ],
            outputs=[io.Model.Output(display_name='MODEL', tooltip='Model with continuous LoRA modulation')],
        )

    @classmethod
    def execute(
        cls, model: Any, lora_name: str, curve: str, apply_to: str = 'both'
    ) -> io.NodeOutput:
        parsed = StrengthCurve.from_json(curve)
        return io.NodeOutput(_apply(model, lora_name, parsed, apply_to))
