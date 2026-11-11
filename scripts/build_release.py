"""Create a reproducible source ZIP with a single ComfyUI custom-node root.

Usage: python scripts/build_release.py /path/to/output.zip
"""
from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = 'ComfyUI-NorthmereInstruments'
OMIT_DIRS = {
    '.git', '.venv', '.pytest_cache', '__pycache__', 'build', 'dist',
    '.mypy_cache', '.ruff_cache', 'playwright-report', 'test-results',
}
OMIT_FILES = {'.coverage', '.DS_Store'}


def publishable_files() -> list[Path]:
    files: list[Path] = []
    for candidate in ROOT.rglob('*'):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        relative = candidate.relative_to(ROOT)
        if any(part in OMIT_DIRS or part.endswith('.egg-info') for part in relative.parts[:-1]):
            continue
        if candidate.name in OMIT_FILES or candidate.suffix in ('.pyc', '.pyo'):
            continue
        files.append(candidate)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def build(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_relative_to(ROOT):
        raise ValueError('Release ZIP must be written outside the repository source tree')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in publishable_files():
            relative = source.relative_to(ROOT)
            info = zipfile.ZipInfo(f'{ARCHIVE_ROOT}/{relative.as_posix()}')
            info.date_time = (2026, 11, 11, 0, 0, 0)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(f'Wrote {destination} ({len(publishable_files())} files)')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.destination.resolve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
