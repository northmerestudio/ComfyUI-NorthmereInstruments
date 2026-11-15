# Northmere Instruments

ComfyUI custom nodes by [Northmere Studio](https://github.com/NorthmereStudio).

## Dynamic Prompt

Expands seeded `{a|b}` choices into a `STRING`. Supported syntax:

- **Choices:** `{red|blue}`; nesting works too: `{small|{medium|large}}`.
- **Tags:** `[[#day]]` labels a choice without appearing in the output.
- **Rules:** Put a `[[RULES]]` / `[[/RULES]]` block at the **end** of the prompt. `day -> sun` requires `sun` when `day` is selected; `day -> !night` excludes `night`; `day ~> sun @ 3` favors `sun` (soft preference).
- **Reuse:** Define a standalone `[[DEF name]]` ... `[[/DEF]]` block, then insert it with `[[USE name]]`.
- **Conditional text:** `[[WHEN day]]bright light[[/WHEN]]` appears only if the `day` tag is selected; its contents must be plain text.

## LoRA Envelope

Schedule a model-only LoRA's strength over denoising. Connect it between **Load Diffusion Model** and **KSampler**.

## Installation

Clone this repository into `ComfyUI/custom_nodes/`. The archive includes one `ComfyUI-NorthmereInstruments/` folder. Restart ComfyUI and refresh the browser.

MIT license. See [LICENSE](LICENSE).
