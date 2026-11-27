import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from validate_hours import static_cells, wet_share  # noqa: E402


def test_wet_share_ignores_uncovered_cells():
    ref = np.array([[0.0, 0.5], [np.nan, 0.05]])
    assert wet_share(ref) == 1 / 3


def test_static_cell_needs_several_days():
    # Cell 0 echoes all the time on both days; cell 1 only on day one (a transient); cell 2 never.
    day1 = np.array([10.0, 10.0, 0.0])
    day2 = np.array([10.0, 0.0, 0.0])
    got = static_cells([day1, day2], [10, 10], persist=0.5, min_days=2)
    assert got.tolist() == [True, False, False]


def test_single_day_behaves_like_the_old_mask():
    got = static_cells([np.array([6.0, 4.0])], [10], persist=0.5, min_days=1)
    assert got.tolist() == [True, False]


def test_combine_round_trip(tmp_path):
    import json
    from types import SimpleNamespace

    from validate_hours import combine

    for day, cells in (("2026-11-28", {"0": 9, "1": 9}), ("2026-11-29", {"0": 8})):
        (tmp_path / f"{day}.json").write_text(json.dumps(
            {"day": day, "rows": 1, "cols": 3, "slots": 10, "dry_hours": ["06"], "echo_mm_h": 0.1, "cells": cells}))
    out = tmp_path / "mask.json"
    combine(SimpleNamespace(combine=str(tmp_path), persist=0.5, min_days=2, write_mask=str(out)))
    assert json.loads(out.read_text())["cells"] == [0]
