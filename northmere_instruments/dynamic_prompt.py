"""Northmere Instruments dynamic prompt node, using the native ComfyUI V3 API."""

from __future__ import annotations

from comfy_api.latest import io

from .dynamic_prompt_engine import expand_prompt

CATEGORY = 'Northmere Instruments/Prompt'


class PromptTemplateInput(io.String.Input):
    """Native STRING data with the same custom-widget contract as LoRA Envelope."""

    def __init__(self) -> None:
        super().__init__(
            'template', multiline=True, default='',
            tooltip='Use {one|two}, {3::one|two}, @pick{day: sunny|night: moonlit}, '
                    '@if(day){warm light}, @define/@use and @rules/@end.',
        )
        self.widget_type = 'NORTHMERE_INSTRUMENTS_PROMPT'
        self.socketless = True

class DynamicPromptNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id='NorthmereInstruments.DynamicPrompt',
            display_name='Northmere Prompt v3',
            category=CATEGORY,
            description='Seeded choices, labelled @pick alternatives, definitions, conditions and rule-aware sampling.',
            search_aliases=['northmere', 'dynamic prompt', 'seeded prompt', 'prompt rules'],
            inputs=[
                PromptTemplateInput(),
                io.Int.Input('seed', default=0, min=0, max=0xFFFFFFFFFFFFFFFF,
                             control_after_generate=True,
                             tooltip='Deterministic expansion seed; independent of KSampler.'),
            ],
            outputs=[io.String.Output(display_name='prompt')],
        )
    @classmethod
    def execute(cls, template: str, seed: int) -> io.NodeOutput:
        return io.NodeOutput(expand_prompt(template, seed=seed))
