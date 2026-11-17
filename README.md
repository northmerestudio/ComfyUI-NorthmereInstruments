# Northmere Instruments

Custom nodes for ComfyUI, developed by [Northmere Studio](https://github.com/NorthmereStudio).

## Dynamic Prompt

Dynamic Prompt generates a `STRING` from a prompt containing choices, conditions, and reusable definitions. Random choices are seeded, so the same seed can reproduce the same result.

A basic choice looks like this:

```text
{red | blue}
```

Choices can be nested, and individual options can be weighted:

```text
{small | {medium | large}}
{red @3 | blue @1}
```

The syntax also supports decisions that affect other parts of a prompt. This is useful when several details should agree with one another rather than being selected independently.

### Tags and conditions

A branch can assign a tag without including the tag's name in the output:

```text
{day => sunny | night => moonlit}
?day{warm light}
```

If the first choice assigns `day`, the condition adds `warm light`. A branch can assign multiple tags, while `_` assigns none:

```text
{compact, low_ceiling => small room | _ => ordinary room}
```

Conditions can also refer to named decisions, such as `?outfit.casual{shirt}`.

### Reusable definitions and shared decisions

There are two ways to define choices, depending on whether each reference should be evaluated separately or share one result:

```text
let gaze = {left | right}
choose outfit = {casual | formal}
```

Each use of `$gaze` expands independently. In contrast, `choose outfit` makes one decision for the entire generation. Its result can be checked wherever it is needed:

```text
?outfit.casual{in a shirt}
?outfit.formal{in a jacket}
```

Definitions made with `let` can use dotted names.

### Constraints and preferences

Constraints exclude combinations; preferences adjust their relative likelihood:

```text
require day -> !flash
prefer window & soft * 4
```

Here, `flash` is disallowed when `day` is present, while the combination of `window` and `soft` receives additional weight. Place these declarations before the prompt text.

### Constants, functions, and expressions

For more structured prompts, Dynamic Prompt supports typed constants, reusable functions, and expressions:

```text
const count: int = 3
fn scene.subject(name: text, tone: text = "natural") = $name with $tone lighting
$scene.subject("a model")
${count * 2}
```

Constants are immutable and support `text`, `int`, `float`, and `bool`. Functions may call other functions, and expressions support arithmetic, comparisons, Boolean logic, and built-in text functions.

Comments are supported on complete lines: use `#` for declaration lines and `//` in the prompt body. Inline comments are not supported.

### Complete example

```text
choose outfit = {casual | formal}
require day -> !flash
prefer window & soft * 4
// Scene
Portrait ?outfit.casual{in a shirt}?outfit.formal{in a jacket}.
{window => by a window | day => outside}, {soft => soft light | flash => flash}.
```

This example combines a shared outfit decision with tags, conditional text, a constraint, and a preference.

## LoRA Envelope

LoRA Envelope varies the strength of a model-only LoRA over the denoising process, rather than applying a fixed strength throughout.

Connect it between **Load Diffusion Model** and **KSampler**.

## Installation

Clone the repository into `ComfyUI/custom_nodes/`. If installing from an archive, it includes the `ComfyUI-NorthmereInstruments/` directory.

Restart ComfyUI and refresh the browser to load the nodes.

## License

MIT. See [LICENSE](LICENSE).
