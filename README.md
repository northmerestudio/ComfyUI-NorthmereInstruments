# Northmere Instruments

ComfyUI custom nodes by [Northmere Studio](https://github.com/NorthmereStudio).

## Dynamic Prompt

Expands seeded `{a|b}` choices into a `STRING`. Supported syntax:

- **Choices:** `{red|blue}`; nesting works too: `{small|{medium|large}}`. Use `{3::red|1::blue}` for relative weights.
- **Named choices:** `@pick{day: sunny|night: moonlit}` labels the selected alternative without putting its label in the output. Labels are Boolean facts for the current expansion; the same label may occur in multiple named choices. One alternative may declare several facts: `@pick{compact, low_ceiling: small room|_: ordinary room}`. Use `_:` when an alternative has **no label**; `_` never becomes a fact.
- **Rules:** Put an `@rules` ... `@end` block on its own lines. `day -> sun` requires `sun` when `day` is selected; `day -> !night` excludes `night`; `day ~> sun @ 3` multiplies the weight of complete selections containing both labels by 3. Rule labels must be declared in named choices.
- **Reuse:** Define a standalone `@define name` ... `@end` block, then insert it with `@use(name)`. Each use expands independently.
- **Conditional text:** `@if(day){bright light}` appears only if `day` was selected. It can contain ordinary nested choices, but not new named choices.

Example:

```text
@pick{day: sunny landscape|night: moonlit landscape},
@pick{soft: soft light|flash: studio flash}.
@if(day){ Warm {golden|gentle} colors.}

@rules
  day -> !flash
@end
```

Structural whitespace around choices, labels, directives, and rule operators is tolerated. Literal prompt whitespace is generally preserved. Escape syntax characters with a backslash, such as `\{` and `\|`.

**Breaking syntax change:** The old `[[...]]` tags, definitions, conditions, and rules are **not supported**. Existing templates must be rewritten. The node identifier remains unchanged.

**Wildcards:** Inline choices and weights resemble ComfyUI Wildcards syntax, but file-backed `__name__` wildcards are **not** resolved here. Expand those in an upstream wildcard node. Northmere constraints apply only to Northmere's own named choices.

Hard rules are enforced during seeded selection. Smaller constrained prompts use exact weighted sampling; large prompts use bounded randomized backtracking, which preserves hard rules and favors soft rules locally but does not guarantee exact soft-rule probabilities. Search exhaustion is reported as an error. See `examples/migrated_prompt_v3.txt` for a complete converted template.

## LoRA Envelope

Schedule a model-only LoRA's strength over denoising. Connect it between **Load Diffusion Model** and **KSampler**.

## Installation

Clone this repository into `ComfyUI/custom_nodes/`. The archive includes one `ComfyUI-NorthmereInstruments/` folder. Restart ComfyUI and refresh the browser.

MIT license. See [LICENSE](LICENSE).
