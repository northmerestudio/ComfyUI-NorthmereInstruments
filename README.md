# Northmere Instruments

ComfyUI custom nodes by [Northmere Studio](https://github.com/NorthmereStudio).

## Dynamic Prompt

Expands seeded choices into a `STRING`. Supported syntax:

- **Choices:** `{red | blue}` and nested choices such as `{small | {medium | large}}`. Use `{red @3 | blue @1}` for relative weights.
- **Tagged choices:** `{day => sunny | night => moonlit}` asserts facts without printing their names. A branch can assert several facts: `{compact, low_ceiling => small room | _ => ordinary room}`. `_` asserts none.
- **Conditions:** `?day{warm light}` outputs text only when a fact is present. `?outfit.casual{shirt}` tests a shared decision.
- **Reuse:** Declare `let gaze = {left | right}` in a `---` header; `$gaze` expands independently each time. Dotted definition names are supported.
- **Shared decisions:** `choose outfit = {casual | formal}` selects once for the entire generation; refer to `?outfit.casual{...}` or `?outfit.formal{...}`.
- **Constraints:** `require day -> !flash` disallows conflicting choices; `prefer window & soft * 4` increases relative likelihood. Declare these in the header.
- **Comments:** Whole body lines starting with `//` and whole header lines starting with `#` are ignored. Inline comments are not recognized.

Example:

```text
---
choose outfit = {casual | formal}
require day -> !flash
prefer window & soft * 4
---
// Scene
Portrait ?outfit.casual{in a shirt}?outfit.formal{in a jacket}.
{window => by a window | day => outside}, {soft => soft light | flash => flash}.
```

## LoRA Envelope

Schedule a model-only LoRA's strength over denoising. Connect it between **Load Diffusion Model** and **KSampler**.

## Installation

Clone this repository into `ComfyUI/custom_nodes/`. The archive includes one `ComfyUI-NorthmereInstruments/` folder. Restart ComfyUI and refresh the browser.

MIT license. See [LICENSE](LICENSE).
