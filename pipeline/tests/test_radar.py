import sys
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import (  # noqa: E402
    DBZ_CAP, DUBLIN, Grid, RadarSite, build_frame, dbz_to_rain, despeckle, load_clutter_mask, make_grid, merge_sites,
    range_azimuth, read_volume, sample_site,
)


def write_volume(path, lat=53.0, lon=-8.0, tilts=((0.5, None), (1.1, None))):
    """Tiny ODIM file. Each tilt is (elevation, fill) where fill maps (ray, bin) -> dBZ or None."""
    with h5py.File(path, "w") as f:
        f.create_group("what").attrs.update({"date": b"20261126", "time": b"160504", "object": b"PVOL"})
        f.create_group("where").attrs.update({"lat": lat, "lon": lon, "height": 50.0})
        for i, (elev, fill) in enumerate(tilts, start=1):
            g = f.create_group(f"dataset{i}")
            g.create_group("where").attrs.update(
                {"elangle": elev, "nbins": 250, "nrays": 360, "rscale": 1000.0, "rstart": 0.0, "a1gate": 0}
            )
            raw = np.zeros((360, 250), dtype=np.uint8)  # 0 = undetect
            if fill:
                dbz = np.full((360, 250), -np.inf)
                for ray in range(360):
                    for b in range(250):
                        v = fill(ray, b)
                        if v is not None:
                            dbz[ray, b] = v
                ok = np.isfinite(dbz)
                raw[ok] = ((dbz[ok] + 32.0) / 0.5).astype(np.uint8)
            d = g.create_group("data1")
            d.create_dataset("data", data=raw)
            d.create_group("what").attrs.update({"gain": 0.5, "offset": -32.0, "nodata": 255.0, "undetect": 0.0, "quantity": b"DBZH"})
            g.create_group("how").attrs.update(
                {"startazA": np.arange(360) + 0.0, "stopazA": np.arange(360) + 1.0}
            )


def test_marshall_palmer():
    assert abs(dbz_to_rain(np.array([30.0]))[0] - 2.73) < 0.02
    assert dbz_to_rain(np.array([-np.inf]))[0] == 0
    assert np.isnan(dbz_to_rain(np.array([np.nan]))[0])
    assert dbz_to_rain(np.array([80.0]))[0] == dbz_to_rain(np.array([DBZ_CAP]))[0]  # hail cap


def test_read_volume_scales_dbz(tmp_path):
    p = tmp_path / "v.h5"
    write_volume(p, tilts=((0.5, lambda r, b: 30.0),))
    vol = read_volume(str(p))
    assert vol.obs_time == datetime(2026, 11, 26, 16, 5, 4, tzinfo=timezone.utc)  # scan time, not run time
    assert abs(vol.tilts[0].dbz[10, 10] - 30.0) < 0.5


def test_range_azimuth_north_and_east():
    d, az = range_azimuth(53.0, -8.0, np.array([54.0]), np.array([-8.0]))
    assert abs(d[0] - 111.2) < 1 and az[0] < 0.1
    d, az = range_azimuth(53.0, -8.0, np.array([53.0]), np.array([-7.0]))
    assert abs(az[0] - 90) < 0.5


def test_echo_lands_in_the_right_direction(tmp_path):
    p = tmp_path / "v.h5"
    # echo only due east (ray 90), 50 to 100 km out
    write_volume(p, tilts=((0.5, lambda r, b: 30.0 if 85 <= r <= 95 and 50 <= b <= 100 else None),))
    vol = read_volume(str(p))
    site = RadarSite("T", 53.0, -8.0)
    east = sample_site(vol, site, np.array([[53.0]]), np.array([[-8.0 + 75 / (111.2 * np.cos(np.radians(53)))]]))[0]
    west = sample_site(vol, site, np.array([[53.0]]), np.array([[-8.0 - 75 / (111.2 * np.cos(np.radians(53)))]]))[0]
    assert east[0, 0] > 2 and west[0, 0] == 0


def test_low_tilt_exclusion_near_site(tmp_path):
    p = tmp_path / "v.h5"
    # clutter on the 0.5 degree tilt only; 1.1 degree tilt is clean
    write_volume(p, lat=DUBLIN.lat, lon=DUBLIN.lon, tilts=((0.5, lambda r, b: 40.0), (1.1, None)))
    vol = read_volume(str(p))
    lat = np.array([[DUBLIN.lat + 20 / 111.2], [DUBLIN.lat + 80 / 111.2]])
    lon = np.full((2, 1), DUBLIN.lon)
    near, _ = sample_site(vol, DUBLIN, lat, lon)
    far, _ = sample_site(vol, DUBLIN, lat, lon + 0)
    assert near[0, 0] == 0  # 20 km: 0.5 degree clutter ignored
    assert far[1, 0] > 5  # 80 km: lowest tilt is used


def test_despeckle_removes_lone_spike_but_keeps_a_storm_core():
    a = np.full((9, 9), 0.5, dtype=np.float32)
    a[2, 2] = 60.0
    out = despeckle(a)
    assert out[2, 2] < 1
    b = np.full((9, 9), 12.0, dtype=np.float32)
    b[4, 4] = 20.0
    assert despeckle(b)[4, 4] == 20.0


def test_merge_prefers_nearer_radar_not_max():
    a, b = np.array([1.0], dtype=np.float32), np.array([30.0], dtype=np.float32)
    out = merge_sites(a, np.array([50.0]), b, np.array([180.0]))
    assert out[0] == 1.0  # inside the near radar's clean range, far radar ignored
    mid = merge_sites(a, np.array([150.0]), b, np.array([155.0]))
    assert 1.0 < mid[0] < 30.0  # overlap zone blends
    only_b = merge_sites(np.array([np.nan], dtype=np.float32), np.array([50.0]), b, np.array([180.0]))
    assert only_b[0] == 30.0  # no coverage from the near radar falls back to the other


def test_build_frame_matches_contract_shape(tmp_path):
    p = tmp_path / "v.h5"
    write_volume(p, lat=53.4299, lon=-6.2443, tilts=((0.5, lambda r, b: 25.0), (1.1, lambda r, b: 25.0)))
    grid = make_grid()
    frame = build_frame([(read_volume(str(p)), DUBLIN)], grid, now=datetime(2026, 11, 26, 16, 20, tzinfo=timezone.utc))
    assert frame["sourceTime"] == "2026-11-26T16:05:04Z"  # from the file, not the run
    assert frame["generatedAt"] == "2026-11-26T16:20:00Z"
    assert len(frame["rainMmh"]) == grid.rows * grid.cols
    assert frame["grid"]["cols"] == grid.cols and min(frame["rainMmh"]) >= 0


def test_clutter_mask_zeroes_masked_cells_and_rejects_wrong_grid(tmp_path):
    import json

    p = tmp_path / "v.h5"
    write_volume(p, lat=53.4299, lon=-6.2443, tilts=((0.5, lambda r, b: 25.0), (1.1, lambda r, b: 25.0)))
    grid = make_grid()
    vol = read_volume(str(p))
    plain = build_frame([(vol, DUBLIN)], grid)["rainMmh"]
    wet_idx = [i for i, x in enumerate(plain) if x > 0][:5]
    assert wet_idx
    mf = tmp_path / "m.json"
    mf.write_text(json.dumps({"rows": grid.rows, "cols": grid.cols, "cells": wet_idx}))
    masked = build_frame([(vol, DUBLIN)], grid, clutter_mask=load_clutter_mask(str(mf), grid))["rainMmh"]
    assert all(masked[i] == 0 for i in wet_idx)
    assert sum(1 for x in masked if x > 0) == sum(1 for x in plain if x > 0) - len(wet_idx)
    mf.write_text(json.dumps({"rows": 3, "cols": 3, "cells": []}))
    try:
        load_clutter_mask(str(mf), grid)
        assert False, "should reject a mask for another grid"
    except ValueError:
        pass


def test_committed_clutter_mask_fits_default_grid():
    mask = load_clutter_mask(str(Path(__file__).resolve().parents[1] / "clutter_mask.json"), make_grid())
    assert 0 < mask.sum() < 0.05 * mask.size  # a small fraction of the grid, never most of it
