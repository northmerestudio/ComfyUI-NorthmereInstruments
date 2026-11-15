# Northmere Instruments

ComfyUI custom nodes by [Northmere Studio](https://github.com/NorthmereStudio).

## Dynamic Prompt

Expands nested `{a|b}` choices using a reproducible seed. Supports positive and negative rules through `[[#tag]]` markers and an inline `[[RULES]]` block. Outputs a fully expanded `STRING`.

## LoRA Envelope

Schedule a model-only LoRA's strength over denoising. Connect it between **Load Diffusion Model** and **KSampler**.

## Installation

Clone this repository into `ComfyUI/custom_nodes/`. The archive includes one `ComfyUI-NorthmereInstruments/` folder. Restart ComfyUI and refresh the browser.

MIT license. See [LICENSE](LICENSE).
