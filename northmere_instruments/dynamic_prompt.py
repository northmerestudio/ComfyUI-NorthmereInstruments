"""Dynamic prompt node."""

from __future__ import annotations

from comfy_api.latest import io

from .dynamic_prompt_engine import expand_prompt

CATEGORY = 'Northmere Instruments/Prompt'


class PromptTemplateInput(io.String.Input):
    """Text input with syntax highlighting."""

    def __init__(self) -> None:
        super().__init__(
            'template', multiline=True, default='',
            tooltip='Write a prompt with choices like {red|blue}, weighted choices like '
                    '{3::red|blue}, or @pick for named options.',
        )
        self.widget_type = 'NORTHMERE_INSTRUMENTS_PROMPT'
        self.socketless = True

class DynamicPromptNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id='NorthmereInstruments.DynamicPrompt',
            display_name='Northmere Dynamic Prompt',
            category=CATEGORY,
            description='Build prompts with random choices, weights, conditions and reusable text.',
            search_aliases=['northmere prompt', 'dynamic prompt', 'random prompt', 'prompt template'],
            inputs=[
                PromptTemplateInput(),
                io.Int.Input('seed', default=0, min=0, max=0xFFFFFFFFFFFFFFFF,
                             control_after_generate=True,
                             tooltip='Controls which options are chosen. The same prompt and seed give the same result.'),
            ],
            outputs=[io.String.Output(display_name='prompt')],
        )
    @classmethod
    def execute(cls, template: str, seed: int) -> io.NodeOutput:
        return io.NodeOutput(expand_prompt(template, seed=seed))
