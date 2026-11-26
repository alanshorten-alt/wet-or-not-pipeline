"""Short-range rain nowcast: track motion between recent frames, advect the latest one.

Pure functions, no network. Motion comes from dense optical flow (Farneback)
between consecutive 5-minute frames. The flow is reduced to one robust vector
because the nowcast contract carries a single national motion; the latest frame
is then shifted along it for 5, 10 ... 120 minutes. Rain is not grown or
decayed: this is extrapolation of what the radar already shows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

STEP_MIN = 5
STEPS = 24
MAX_CELLS_PER_STEP = 4.0  # 120 km/h on a 2.5 km grid; faster is a bad estimate
WET_MMH = 0.2  # cells above this count as echo when estimating motion
MIN_WET_CELLS = 40  # fewer than this and there is nothing reliable to track


def _to_u8(rain: np.ndarray) -> np.ndarray:
    return np.clip(np.log1p(rain) / np.log1p(50.0) * 255.0, 0, 255).astype(np.uint8)


def pair_motion(prev: np.ndarray, cur: np.ndarray, minutes: float) -> tuple[float, float, int]:
    """(dx, dy) in grid cells per 5 minutes (dx east, dy south) and the echo cell count."""
    flow = cv2.calcOpticalFlowFarneback(_to_u8(prev), _to_u8(cur), None, 0.5, 3, 15, 3, 5, 1.1, 0)
    wet = (prev > WET_MMH) | (cur > WET_MMH)
    n = int(wet.sum())
    if n < MIN_WET_CELLS:
        return 0.0, 0.0, n
    scale = STEP_MIN / max(minutes, 1.0)
    return float(np.median(flow[..., 0][wet])) * scale, float(np.median(flow[..., 1][wet])) * scale, n


def estimate_motion(frames: list[tuple[datetime, np.ndarray]]) -> tuple[float, float, float]:
    """Mean (dx, dy) cells per 5 min over consecutive pairs, plus a 0..1 confidence.

    Confidence is high when the pairs agree and there is plenty of echo; it is
    0.5 when there is too little rain to track (the dry forecast is then
    persistence and says nothing about new rain) and 0 with fewer than two frames.
    """
    if len(frames) < 2:
        return 0.0, 0.0, 0.0
    vecs = [pair_motion(a[1], b[1], (b[0] - a[0]).total_seconds() / 60.0) for a, b in zip(frames, frames[1:])]
    live = [v for v in vecs if v[2] >= MIN_WET_CELLS]
    if not live:
        return 0.0, 0.0, 0.5
    dx = float(np.mean([v[0] for v in live]))
    dy = float(np.mean([v[1] for v in live]))
    speed = float(np.hypot(dx, dy))
    if speed > MAX_CELLS_PER_STEP:
        dx, dy, speed = dx * MAX_CELLS_PER_STEP / speed, dy * MAX_CELLS_PER_STEP / speed, MAX_CELLS_PER_STEP
    spread = float(np.mean([np.hypot(v[0] - dx, v[1] - dy) for v in live])) if len(live) > 1 else 0.5
    agreement = float(np.exp(-spread / max(speed, 0.5)))
    coverage = min(1.0, min(v[2] for v in live) / 200.0)
    return dx, dy, round(float(np.clip(agreement * (0.5 + 0.5 * coverage), 0.0, 1.0)), 2)


def advect(rain: np.ndarray, dx: float, dy: float, steps: int = STEPS) -> list[np.ndarray]:
    """Latest frame shifted by k*(dx, dy) cells for k = 1..steps (backward bilinear lookup)."""
    h, w = rain.shape
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    src = rain.astype(np.float32)
    out = []
    for k in range(1, steps + 1):
        out.append(cv2.remap(src, gx - dx * k, gy - dy * k, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0))
    return out


def build_nowcast(frames: list[tuple[datetime, np.ndarray]], grid: dict, source_time: datetime,
                  now: datetime | None = None) -> dict:
    """Nowcast dict matching the contract. `frames` oldest first; the last is the latest radar frame.

    sourceTime is the radar scan time, carried through unchanged, so the phone's
    "radar X min ago" counts from the scan and not from when this job ran.
    """
    frames = sorted(frames, key=lambda f: f[0])[-3:]
    dx, dy, confidence = estimate_motion(frames)
    cell_km = grid["cellKm"]
    iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    steps = []
    for k, field in enumerate(advect(frames[-1][1], dx, dy), start=1):
        steps.append({"minutes": k * STEP_MIN, "rainMmh": [0 if x < 0.05 else round(float(x), 1) for x in field.ravel()]})
    return {
        "generatedAt": iso(now or datetime.now(timezone.utc)),
        "sourceTime": iso(source_time),
        "grid": grid,
        "steps": steps,
        # dy is rows southward, so north is -dy; 12 five-minute steps per hour.
        "motionKmh": {"east": round(dx * cell_km * 12, 1), "north": round(-dy * cell_km * 12, 1)},
        "confidence": confidence,
    }
