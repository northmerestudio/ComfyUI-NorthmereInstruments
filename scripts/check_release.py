"""Verify an actual Northmere Instruments release ZIP after fresh extraction.

Usage:
    python scripts/check_release.py /path/to/ComfyUI-NorthmereInstruments-vX.zip
    python scripts/check_release.py /path/to/archive.zip --browser

Runs fast tests from the extracted archive; --browser adds the Chromium test.
This cannot certify compatibility with a real running ComfyUI/Krea 2 environment.
"""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

PACK_NAME = 'ComfyUI-NorthmereInstruments'
REQUIRED = {
    '__init__.py',
    'README.md',
    'LICENSE',
    'pyproject.toml',
    '.github/workflows/test.yml',
    'northmere_instruments/nodes.py',
    'northmere_instruments/engine.py',
    'northmere_instruments/curve.py',
    'web/curve.js',
    'web/curve.css',
}


def check_archive(archive: Path) -> None:
    with zipfile.ZipFile(archive) as zipped:
        invalid = zipped.testzip()
        if invalid is not None:
            raise ValueError(f'Corrupt ZIP member: {invalid}')
        relative = set()
        for member in zipped.infolist():
            name = PurePosixPath(member.filename)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != PACK_NAME:
                raise ValueError(f'Unexpected ZIP path: {member.filename!r}')
            if '.git' in name.parts or '__pycache__' in name.parts:
                raise ValueError(f'Build artifact in release ZIP: {member.filename!r}')
            if not member.is_dir():
                relative.add(name.relative_to(PACK_NAME).as_posix())
        missing = REQUIRED - relative
        if missing:
            raise ValueError(f'Missing required release files: {sorted(missing)}')


def run_checked(command: list[str], cwd: Path, *, input_data: str | None = None) -> None:
    process = subprocess.run(command, cwd=cwd, input=input_data, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(f'{command[0]} failed with code {process.returncode}: {command[1:]}')


def verify_release(archive: Path, *, browser: bool = False) -> None:
    check_archive(archive)
    with tempfile.TemporaryDirectory(prefix='northmere-release-') as directory:
        custom_nodes = Path(directory) / 'custom_nodes'
        custom_nodes.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(custom_nodes)
        root = custom_nodes / PACK_NAME
        run_checked([sys.executable, 'scripts/verify_install.py', str(custom_nodes)], root)
        run_checked([sys.executable, '-m', 'pytest', '-q'], root)
        run_checked([sys.executable, '-m', 'compileall', '-q', 'northmere_instruments'], root)
        run_checked(['node', 'tests/ui_smoke.cjs'], root)
        run_checked(['node', '--input-type=module', '--check'], root,
                    input_data=(root / 'web/curve.js').read_text(encoding='utf-8'))
        if browser:
            run_checked([sys.executable, 'tests/browser_smoke.py'], root)
        print(f'PASS: release archive {archive.name} verified after fresh extraction')
        if not browser:
            print('NOTE: pass --browser to also run the standalone Chromium editor check.')
        print('NOTE: live ComfyUI Nodes 2.0 / Krea 2 GPU integration is not tested.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    try:
        verify_release(args.archive, browser=args.browser)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
