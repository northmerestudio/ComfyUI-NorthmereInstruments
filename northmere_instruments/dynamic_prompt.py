"""Northmere Instruments dynamic prompt node, using the native ComfyUI V3 API."""

from __future__ import annotations

from comfy_api.latest import io

from .dynamic_prompt_engine import expand_prompt

CATEGORY = 'Northmere Instruments/Prompt'

class DynamicPromptNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id='NorthmereInstruments.DynamicPrompt',
            display_name='Dynamic Prompt (Rules)',
            category=CATEGORY,
            description='Seeded choices, reusable [[DEF]] sections, conditional [[WHEN]] text, and hard/soft rules.',
            search_aliases=['northmere', 'dynamic prompt', 'seeded prompt', 'prompt rules'],
            inputs=[
                io.String.Input(
                    'template', multiline=True, default='',
                    tooltip='Nested {one|two} choices, [[#tag]], [[DEF name]]/[[USE name]], '
                    '[[WHEN tag]], and a final [[RULES]] block (-> or ~>).',
                ),
                io.Int.Input('seed', default=0, min=0, max=0xFFFFFFFFFFFFFFFF,
                             control_after_generate=True,
                             tooltip='Deterministic expansion seed; independent of KSampler.'),
            ],
            outputs=[io.String.Output(display_name='prompt')],
        )
    @classmethod
    def execute(cls, template: str, seed: int) -> io.NodeOutput:
        return io.NodeOutput(expand_prompt(template, seed=seed))
