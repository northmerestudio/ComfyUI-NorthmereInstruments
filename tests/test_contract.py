"""Static contracts that protect existing Northmere workflow files."""
from __future__ import annotations

import json
from pathlib import Path

from northmere_instruments.curve import DEFAULT_CURVE

ROOT = Path(__file__).resolve().parents[1]


def test_envelope_starts_with_nine_keyframes() -> None:
    data = json.loads(DEFAULT_CURVE)
    assert len(data["points"]) == 9
    assert data["points"][0] == [0.0, 0.5]
    assert data["points"][-1] == [1.0, 1.0]
    for index, point in enumerate(data["points"]):
        assert abs(point[0] - index / 8) < 1e-12


def test_node_id_is_stable_but_display_name_is_clear() -> None:
    source = (ROOT / "northmere_instruments" / "nodes.py").read_text()
    assert "node_id='NorthmereInstruments.LoRAStrengthCurve'" in source
    assert "display_name='LoRA Envelope'" in source
    assert "class LoRAEnvelopeNode(io.ComfyNode):" in source


def test_frontend_preserves_widget_serialization_type() -> None:
    source = (ROOT / "web" / "curve.js").read_text()
    assert "NORTHMERE_INSTRUMENTS_CURVE" in source
    assert "getValue: () => invalidSerializedValue ?? JSON.stringify(data)" in source
    assert "setValue: (value) =>" in source


def test_enabled_switch_removed_from_node_interface() -> None:
    source = (ROOT / "northmere_instruments" / "nodes.py").read_text()
    assert "io.Boolean.Input('enabled'" not in source
    assert "def execute(" in source
    assert "apply_to: str = 'both'" in source
