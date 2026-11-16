"""Dynamic prompt node."""

from __future__ import annotations

from comfy_api.latest import io

from .dynamic_prompt_sampler import expand_prompt

CATEGORY = 'Northmere Instruments/Prompt'


class PromptTemplateInput(io.String.Input):
    """STRING input with the dedicated prompt editor widget."""

    def __init__(self) -> None:
        super().__init__(
            'template', multiline=True, default='',
            tooltip='Use {red | blue} for alternatives, @3 for a weight, and => for facts. '
                    'Optional --- header: choose, let, require, prefer. '
                    'Use $name for reusable text and ?fact{text} for conditions.',
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
            description='Expand seeded text choices with weights, shared decisions, reusable templates, '
                        'conditional text and hard/soft constraints. Optional --- header defines rules.',
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
