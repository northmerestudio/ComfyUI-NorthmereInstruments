"""Verify on-disk installation layout; safe to run without starting ComfyUI.

Usage: python scripts/verify_install.py /path/to/ComfyUI/custom_nodes
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PACK_NAME = 'ComfyUI-NorthmereInstruments'
REQUIRED = (
    '__init__.py',
    'northmere_instruments/__init__.py',
    'northmere_instruments/nodes.py',
    'northmere_instruments/engine.py',
    'northmere_instruments/curve.py',
    'web/curve.js',
    'web/curve.css',
)


def verify(custom_nodes: Path) -> list[str]:
    target = custom_nodes / PACK_NAME
    errors = []
    if not target.is_dir():
        errors.append(f'Package directory missing: {target}')
        return errors
    for item in REQUIRED:
        if not (target / item).is_file():
            errors.append(f'Missing {target / item}')
    if errors:
        nested = target / PACK_NAME / '__init__.py'
        if nested.exists():
            errors.append(f'Detected double nesting: {nested}. Move its parent contents one directory up.')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('custom_nodes', type=Path)
    args = parser.parse_args()
    errors = verify(args.custom_nodes)
    if errors:
        print('FAIL: Northmere Instruments install layout is invalid:', file=sys.stderr)
        for error in errors:
            print(f'  - {error}', file=sys.stderr)
        return 1
    print(f'PASS: {args.custom_nodes / PACK_NAME} contains all required installation files')
    print('NOTE: layout verification does not validate the ComfyUI API or GPU sampling.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
