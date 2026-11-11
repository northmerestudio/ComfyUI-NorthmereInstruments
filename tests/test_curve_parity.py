"""Cross-language parity for Python sampling versus the displayed JS curve."""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

import pytest

from northmere_instruments.envelope import LoRAEnvelope

ROOT = Path(__file__).resolve().parents[1]


def test_backend_and_frontend_match_for_diverse_curves():
    rng = random.Random(72413)
    batches = []
    expected = []
    for interpolation in ('linear', 'smooth', 'hold'):
        for count in (2, 3, 9, 25):
            for _ in range(4):
                middle = sorted(rng.uniform(0.00001, 0.99999) for _ in range(count - 2))
                positions = [0.0, *middle, 1.0]
                points = [[x, rng.uniform(-3, 3)] for x in positions]
                samples = [0, 1, *[rng.random() for _ in range(80)]]
                payload = {'version': 1, 'interpolation': interpolation, 'points': points}
                curve = LoRAEnvelope.from_json(payload)
                batches.append({'interpolation': interpolation, 'points': points, 'positions': samples})
                expected.append([curve.at(x) for x in samples])
    run = subprocess.run(
        ['node', str(ROOT / 'tests' / 'curve_parity.cjs')],
        input=json.dumps(batches), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True,
    )
    results = json.loads(run.stdout)
    for backend, frontend in zip(expected, results):
        assert frontend == pytest.approx(backend, abs=1e-8)
