"""Preset-library contract: exact grid, semantics, smoothness, and backend compatibility.

Requires Node.js to evaluate the browser's own preset definitions, not a Python copy.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from northmere_instruments.envelope import LoRAEnvelope

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def presets():
    if not shutil.which('node'):
        pytest.skip('Node.js is required for front-end preset contract verification')
    js = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const defs = source.slice(source.indexOf('const DEFAULT_STRENGTHS'), source.indexOf('const MAX_POINTS'));
const context = vm.createContext({ Math, Number });
const result = vm.runInContext(defs + '\nJSON.stringify(PRESETS)', context);
process.stdout.write(result);
'''
    result = subprocess.run(['node', '-e', js, str(ROOT / 'web/curve.js')],
                            check=True, text=True, capture_output=True)
    return {id_: (label, points, interpolation if len(item) == 4 else 'linear')
            for item in json.loads(result.stdout)
            for id_, label, points, *rest in [item]
            for interpolation in [rest[0] if rest else 'linear']}


def test_count_and_unique_labels(presets):
    assert len(presets) == 12
    labels = [item[0] for item in presets.values()]
    assert len(set(labels)) == len(labels)
    assert len(set(presets)) == len(presets)


def test_exact_eighth_grid_and_valid_backend_curves(presets):
    for key, (_, points, interpolation) in presets.items():
        assert len(points) == 9, key
        assert [p[0] for p in points] == [i / 8 for i in range(9)], key
        assert all(0 <= p[1] <= 1 for p in points), key
        curve = LoRAEnvelope.from_json({'version': 1, 'points': points,
                                         'interpolation': interpolation})
        for i in range(1001):
            weight = curve.at(i / 1000)
            assert -1e-12 <= weight <= 1 + 1e-12, (key, i, weight)


def test_semantic_shapes_and_endpoints(presets):
    def curve(id_):
        _, points, interp = presets[id_]
        return LoRAEnvelope.from_json({'version': 1, 'points': points, 'interpolation': interp})

    assert all(p[1] == 0 for p in presets['off'][1])
    assert all(p[1] == 1 for p in presets['full'][1])
    assert all(x == y for x, y in presets['fade-in'][1])
    assert all(abs(y - (1 - x)) < 1e-12 for x, y in presets['fade-out'][1])
    assert all(abs(y - x*x) < 1e-12 for x, y in presets['ease-in'][1])
    assert presets['soft-rise'][1][0][1] == 0.25
    assert presets['quick-rise'][1][0][1] == 0.5
    assert presets['late-rise-capped'][1][-1][1] == 0.75
    assert all(y == 1 for x, y in presets['fast-rise'][1] if x >= 0.75)
    assert all(y == 1 for x, y in presets['early-fade-in'][1] if x >= 0.5)
    assert curve('late-on').at(0.249999) == 0
    assert curve('late-on').at(0.25) == 1
    assert curve('late-rise').at(0.5) == 0.3
    assert curve('late-rise-capped').at(0.5) == 0.225
    assert curve('ease-in').at(0.125) == 0.015625
    assert curve('fast-rise').at(0.125) == 0.372


def test_all_shape_profiles_are_distinct(presets):
    traces = []
    for id_, (_, points, interpolation) in presets.items():
        curve = LoRAEnvelope.from_json({'version': 1, 'points': points,
                                         'interpolation': interpolation})
        trace = tuple(round(curve.at(i/100), 8) for i in range(101))
        assert trace not in traces, f'duplicate curve: {id_}'
        traces.append(trace)
