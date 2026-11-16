"""Dynamic prompt node."""

from __future__ import annotations

from comfy_api.latest import io

from .dynamic_prompt_sampler import expand_prompt

CATEGORY = 'Northmere Instruments/Prompt'


class PromptTemplateInput(io.String.Input):
    """Text input with syntax highlighting."""

    def __init__(self) -> None:
        super().__init__(
            'template', multiline=True, default='',
            tooltip='Choices: {red | blue} or {red @3 | blue @1}. Tagged choices: '
                    '{day => sun | _ => cloud}. Use an optional --- header for let, choose, require and prefer.',
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
            description='Build seeded prompts with choices, weights, conditions, reusable text and constraints. Declare let, choose, require and prefer in a --- header.',
            search_aliases=['northmere prompt', 'dynamic prompt', 'random prompt', 'prompt template'],
            inputs=[
                PromptTemplateInput(),
                io.Int.Input('seed', default=0, min=0, max=0xFFFFFFFFFFFFFFFF,
                             control_after_generate=True,
                             tooltip='Controls repeatable choices; large constrained prompts use bounded sampling.'),
            ],
            outputs=[io.String.Output(display_name='prompt')],
        )
    @classmethod
    def execute(cls, template: str, seed: int) -> io.NodeOutput:
        return io.NodeOutput(expand_prompt(template, seed=seed))
