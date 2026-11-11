"""Regression tests for the archive layout that ComfyUI actually imports."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def test_required_files_exist() -> None:
    from scripts.verify_install import REQUIRED
    assert all((ROOT / item).is_file() for item in REQUIRED)


def test_loader_entrypoint_importable() -> None:
    spec = importlib.util.spec_from_file_location('northmere_installed_smoke', ROOT / '__init__.py',
                                                   submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert callable(module.comfy_entrypoint)
        assert module.WEB_DIRECTORY == './web'
    finally:
        sys.modules.pop(spec.name, None)


def test_installed_layout_from_archive() -> None:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)
        archive = path / 'release.zip'
        custom_nodes = path / 'custom_nodes'
        custom_nodes.mkdir()
        with zipfile.ZipFile(archive, 'w') as output:
            for item in sorted(ROOT.rglob('*')):
                if item.is_file() and not any(part.startswith('.') or part == '__pycache__'
                                              for part in item.relative_to(ROOT).parts):
                    output.write(item, arcname=f'{ROOT.name}/{item.relative_to(ROOT).as_posix()}')
        with zipfile.ZipFile(archive) as source:
            source.extractall(custom_nodes)
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'verify_install.py'), str(custom_nodes)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        installed_root = custom_nodes / ROOT.name
        spec = importlib.util.spec_from_file_location('northmere_zip_smoke', installed_root / '__init__.py',
                                                       submodule_search_locations=[str(installed_root)])
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            assert callable(module.comfy_entrypoint)
        finally:
            sys.modules.pop(spec.name, None)
