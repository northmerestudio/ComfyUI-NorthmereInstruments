"""Validated, step-independent LoRA envelopes.

X represents denoising progress (0 start/noise, 1 finish), NOT a sample index.
Y is the actual signed model LoRA multiplier (no remapping).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from bisect import bisect_right

MAX_POINTS = 64
MAX_ABS_STRENGTH = 20.0  # validation safety bound, not a UI hard clamp
DEFAULT_STRENGTHS = (0.5, 0.8, 0.92, 0.968, 0.9872, 0.99488, 0.997952, 0.9991808, 1.0)
DEFAULT_POINTS = [[index / (len(DEFAULT_STRENGTHS) - 1), strength]
                  for index, strength in enumerate(DEFAULT_STRENGTHS)]
DEFAULT_CURVE = json.dumps({"version": 1, "interpolation": "linear", "points": DEFAULT_POINTS}, separators=(",", ":"))


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def sigma_to_progress(model_sampling: object, sigma: float) -> float:
    """Invert the model's noise-percent mapping; independent of KSampler step count."""
    start = float(model_sampling.percent_to_sigma(0.0))
    end = float(model_sampling.percent_to_sigma(1.0))
    if not all(map(math.isfinite, (start, end, sigma))) or start <= end:
        raise ValueError("Invalid model sigma schedule (cannot normalize denoising progress)")
    if sigma >= start:
        return 0.0
    if sigma <= end:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) * 0.5
        if float(model_sampling.percent_to_sigma(mid)) > sigma:
            lo = mid
        else:
            hi = mid
    return (lo + hi) * 0.5


def _slopes(xs: tuple[float, ...], ys: tuple[float, ...]) -> list[float]:
    """PCHIP/Fritsch–Carlson derivatives: monotone intervals never overshoot."""
    n = len(xs)
    h = [xs[i+1]-xs[i] for i in range(n-1)]
    d = [(ys[i+1]-ys[i])/h[i] for i in range(n-1)]
    if n == 2:
        return [d[0], d[0]]
    out = [0.0]*n
    for i in range(1, n-1):
        if d[i-1] == 0 or d[i] == 0 or d[i-1]*d[i] < 0:
            out[i] = 0.0
        else:
            w1, w2 = 2*h[i]+h[i-1], h[i]+2*h[i-1]
            out[i] = (w1+w2)/(w1/d[i-1]+w2/d[i])
    for idx, delta, a, b in ((0, d[0], h[0], h[1]), (n-1, d[-1], h[-1], h[-2])):
        other = d[1] if idx == 0 else d[-2]
        v = ((2*a+b)*delta - a*other)/(a+b)
        if v*delta <= 0:
            v = 0.0
        elif delta*other < 0 and abs(v) > abs(3*delta):
            v = 3*delta
        out[idx] = v
    return out


@dataclass(frozen=True)
class LoRAEnvelope:
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    interpolation: str
    slopes: tuple[float, ...]

    @classmethod
    def from_json(cls, payload: str | dict) -> 'LoRAEnvelope':
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError as exc:
            raise ValueError('Invalid LoRA envelope JSON') from exc
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ValueError('Envelope must be a version 1 object')
        interp = data.get('interpolation', 'linear')
        if interp not in ('linear', 'smooth', 'hold'):
            raise ValueError('Interpolation must be linear, smooth or hold')
        raw = data.get('points')
        if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_POINTS:
            raise ValueError(f'Envelope needs 2–{MAX_POINTS} points')
        pairs = []
        for p in raw:
            if not isinstance(p, (list, tuple)) or len(p) != 2 or any(type(v) not in (int, float) for v in p):
                raise ValueError('Each point must contain numeric [progress, strength]')
            x, y = float(p[0]), float(p[1])
            if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 1 or abs(y) > MAX_ABS_STRENGTH:
                raise ValueError('X must be 0..1 and strength must be finite, within ±20')
            pairs.append((x, y))
        if pairs[0][0] != 0.0 or pairs[-1][0] != 1.0:
            raise ValueError('Envelope requires fixed endpoints at X=0 and X=1')
        if any(a[0] >= b[0] for a, b in zip(pairs, pairs[1:])):
            raise ValueError('X positions must be strictly increasing')
        xs, ys = zip(*pairs)
        slopes = _slopes(xs, ys) if interp == 'smooth' else [0.0]*len(xs)
        return cls(xs, ys, interp, tuple(slopes))

    def at(self, progress: float) -> float:
        if not math.isfinite(progress):
            raise ValueError('Non-finite sampling progress')
        if progress <= 0:
            return self.ys[0]
        if progress >= 1:
            return self.ys[-1]
        i = bisect_right(self.xs, progress)-1
        width = self.xs[i+1]-self.xs[i]
        t = (progress-self.xs[i])/width
        if self.interpolation == 'hold':
            return self.ys[i]
        if self.interpolation == 'linear':
            return self.ys[i]+t*(self.ys[i+1]-self.ys[i])
        a, b = self.ys[i], self.ys[i+1]
        m0, m1 = self.slopes[i], self.slopes[i+1]
        result = (2*t**3-3*t*t+1)*a + (t**3-2*t*t+t)*width*m0 + (-2*t**3+3*t*t)*b + (t**3-t*t)*width*m1
        # Guard floating-point noise, not an intentional overshoot.
        return min(max(result, min(a, b)), max(a, b))
