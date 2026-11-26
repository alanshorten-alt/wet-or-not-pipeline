import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nowcast import STEPS, advect, build_nowcast, estimate_motion  # noqa: E402

T0 = datetime(2026, 11, 26, 16, 5, tzinfo=timezone.utc)
GRID = {"bounds": {"south": 51.0, "west": -10.0, "north": 52.0, "east": -9.0}, "cols": 80, "rows": 80, "cellKm": 2.5}


def blob(cx, cy, size=80):
    y, x = np.mgrid[0:size, 0:size]
    return (6.0 * np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * 6.0**2)))).astype(np.float32)


def frames(vx, vy):
    return [(T0 + timedelta(minutes=5 * i), blob(30 + vx * i, 40 + vy * i)) for i in range(-2, 1)]


def test_motion_east_and_south():
    dx, dy, conf = estimate_motion(frames(1.0, 0.0))
    assert 0.8 < dx < 1.2 and abs(dy) < 0.2 and conf > 0.5
    dx, dy, _ = estimate_motion(frames(0.0, 1.0))
    assert abs(dx) < 0.2 and 0.8 < dy < 1.2


def test_advection_moves_the_blob_by_steps():
    fs = frames(1.0, 0.0)
    out = advect(fs[-1][1], 1.0, 0.0)
    assert len(out) == STEPS
    peak = np.unravel_index(np.argmax(out[9]), out[9].shape)  # +50 min
    assert abs(peak[1] - (30 + 10)) <= 1 and abs(peak[0] - 40) <= 1


def test_build_nowcast_units_and_source_time():
    fs = frames(1.0, 0.0)
    nc = build_nowcast(fs, GRID, source_time=T0, now=T0 + timedelta(minutes=12))
    assert nc["sourceTime"] == "2026-11-26T16:05:00Z" and nc["generatedAt"] == "2026-11-26T16:17:00Z"
    assert [s["minutes"] for s in nc["steps"]] == list(range(5, 125, 5))
    assert 25 < nc["motionKmh"]["east"] < 35 and abs(nc["motionKmh"]["north"]) < 6  # 1 cell/5 min = 30 km/h
    assert all(len(s["rainMmh"]) == 80 * 80 for s in nc["steps"])


def test_dry_and_single_frame_are_safe():
    dry = [(T0 + timedelta(minutes=5 * i), np.zeros((80, 80), dtype=np.float32)) for i in range(-2, 1)]
    nc = build_nowcast(dry, GRID, source_time=T0)
    assert nc["motionKmh"] == {"east": 0.0, "north": 0.0} and nc["confidence"] == 0.5
    one = build_nowcast(dry[-1:], GRID, source_time=T0)
    assert one["confidence"] == 0 and len(one["steps"]) == STEPS
