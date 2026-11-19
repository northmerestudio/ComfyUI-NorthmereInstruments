#!/usr/bin/env python3
"""Generate 100 prompts with the Northmere Instruments prompt engine.

Run from the ComfyUI-NorthmereInstruments project directory:
    python generate_100_prompts.py examples/beauty_prompt.txt
    python generate_100_prompts.py examples/beauty_prompt.txt -o prompts.txt -n 100 --seed 42
"""

import argparse
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from northmere_instruments.dynamic_prompt_sampler import expand_prompt


def main():
    parser = argparse.ArgumentParser(description="Generate prompts using Northmere Instruments")
    parser.add_argument("template", type=Path, help="Input prompt template (.txt)")
    parser.add_argument("-o", "--output", type=Path, default=Path("generated_prompts.txt"))
    parser.add_argument("-n", "--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0, help="Starting seed")
    args = parser.parse_args()

    if args.count < 1 or args.seed < 0 or args.seed + args.count > 2**64:
        parser.error("Count must be positive and seeds must fit in unsigned 64-bit range")

    template = args.template.read_text(encoding="utf-8")

    prompts = [
        f"# Prompt {i + 1}\n{expand_prompt(template, seed=args.seed + i)}"
        for i in range(args.count)
    ]

    args.output.write_text(
        "\n\n".join(prompts) + "\n",
        encoding="utf-8"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(prompts) + "\n", encoding="utf-8")
    print(f"Saved {len(prompts)} prompts to {args.output}")


if __name__ == "__main__":
    main()
