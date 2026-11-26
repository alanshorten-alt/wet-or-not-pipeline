"""Multi-regime radar validation and clutter-mask builder.

    python pipeline/validate_hours.py --cache DIR --day 2026-11-26 \
        --dry 06 08 09 --wet 11 12 14 16 [--write-mask pipeline/clutter_mask.json]

For each hour the argument names the END of the hour (the composite stamp
T_PASH21_..HH0000 is the hour ending at HH, as compare_composite.py found).
It rebuilds our 5-minute rain on the output grid for the 12 slots in that
hour, accumulates, and compares with the composite at pixel level and on a 3x3
neighbourhood mean. Dry hours also feed a persistent-echo (clutter) mask.

Manual tool: it needs network access. Downloads are cached in --cache.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_composite as cc  # noqa: E402
from radar import DUBLIN, SHANNON, despeckle, make_grid, merge_sites, range_azimuth, read_volume, sample_site  # noqa: E402


def fetch(cache: str, day: datetime, name: str) -> str:
    path = os.path.join(cache, name)
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(cc.get(f"{cc.BASE}/{day:%Y/%m/%d}/{name}"))
    return path


def hour_slots(end: datetime) -> list[datetime]:
    return [end - timedelta(hours=1) + timedelta(minutes=5 * i) for i in range(12)]


def slot_rain(slots, grid, files, cache, day) -> np.ndarray:
    """(n, rows, cols) merged rain rate, mm/h, zeros where no data; missing slots are NaN."""
    lat, lon = grid.centers()
    out = np.full((len(slots),) + lat.shape, np.nan, dtype=np.float32)
    for i, t in enumerate(slots):
        parts = []
        for prefix, site in (("T_PAGZ41", DUBLIN), ("T_PAGZ40", SHANNON)):
            f = cc.nearest(files, prefix, t)
            if f is None:
                break
            r, d = sample_site(read_volume(fetch(cache, t, f)), site, lat, lon)
            parts.append((despeckle(r), d))
        if len(parts) == 2:
            out[i] = np.nan_to_num(merge_sites(parts[0][0], parts[0][1], parts[1][0], parts[1][1]), nan=0.0)
    return out


def box3(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1, mode="edge")
    return sum(p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)) / 9.0


def corr(a, b, m) -> float:
    return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 20 else float("nan")


def compare(ours: np.ndarray, ref: np.ndarray) -> dict:
    ok = np.isfinite(ref)
    m = ok & ((ref > 0.1) | (ours > 0.1))
    ref0 = np.where(ok, ref, 0.0)
    ours3, ref3 = box3(ours), box3(ref0)
    m3 = ok & ((ref3 > 0.1) | (ours3 > 0.1))
    return {
        "cells_wet": int(m.sum()),
        "ours_over_ref_total": float(ours[m].sum() / ref[m].sum()) if m.any() and ref[m].sum() > 0 else float("nan"),
        "corr_pixel": corr(ours, ref0, m),
        "corr_3x3": corr(ours3, ref3, m3),
        "ours_mean_all": float(ours[ok].mean()),
        "ref_mean_all": float(ref[ok].mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--day", required=True)
    ap.add_argument("--dry", nargs="*", default=[])
    ap.add_argument("--wet", nargs="*", default=[])
    ap.add_argument("--write-mask")
    ap.add_argument("--save", help="directory to keep each hour's ours/ref grids as .npz for offline analysis")
    ap.add_argument("--persist", type=float, default=0.5, help="share of dry slots a cell must be echoing in")
    ap.add_argument("--echo", type=float, default=0.1, help="mm/h counted as an echo in a dry slot")
    args = ap.parse_args()

    os.makedirs(args.cache, exist_ok=True)
    day = datetime.fromisoformat(args.day).replace(tzinfo=timezone.utc)
    grid = make_grid()
    files = cc.listing(day)
    lat, lon = grid.centers()
    d_dub, _ = range_azimuth(DUBLIN.lat, DUBLIN.lon, lat, lon)
    d_sha, _ = range_azimuth(SHANNON.lat, SHANNON.lon, lat, lon)
    far = np.minimum(d_dub, d_sha) > 250.0
    print(f"cells beyond 250 km of both radars: {int(far.sum())} of {far.size}")

    echo_slots, n_slots = np.zeros(lat.shape), 0
    for kind, hours in (("dry", args.dry), ("wet", args.wet)):
        for h in hours:
            end = day.replace(hour=int(h))
            comp_name = f"T_PASH21_C_EIDB_{end:%Y%m%d%H%M%S}.hdf"
            ref = cc.composite_on_grid(fetch(args.cache, end, comp_name), grid)
            rain = slot_rain(hour_slots(end), grid, files, args.cache, end)
            have = ~np.isnan(rain[:, 0, 0])
            ours = np.nansum(rain, axis=0) * (5.0 / 60.0) * (12 / max(have.sum(), 1))
            if args.save:
                os.makedirs(args.save, exist_ok=True)
                np.savez_compressed(os.path.join(args.save, f"{kind}_{h}.npz"), ours=ours, ref=ref, lat=lat, lon=lon,
                                    d_dub=d_dub, d_sha=d_sha)
            res = compare(ours, ref)
            res["slots"] = int(have.sum())
            res["max_rain_beyond_250km"] = float(rain[have][:, far].max()) if far.any() and have.any() else 0.0
            print(kind, f"{h}:00", json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in res.items()}))
            if kind == "dry":
                echo_slots += (rain[have] > args.echo).sum(axis=0)
                n_slots += int(have.sum())
    if n_slots and args.write_mask:
        share = echo_slots / n_slots
        mask = share >= args.persist
        idx = np.flatnonzero(mask.ravel()).tolist()
        print(f"clutter mask: {len(idx)} cells echo in >= {args.persist:.0%} of {n_slots} dry slots")
        for i in idx[:40]:
            r, c = divmod(i, lat.shape[1])
            print(f"  cell {r},{c} lat {lat[r, c]:.2f} lon {lon[r, c]:.2f} share {share[r, c]:.2f}")
        with open(args.write_mask, "w") as fh:
            json.dump({"rows": int(lat.shape[0]), "cols": int(lat.shape[1]), "cells": idx,
                       "built_from": f"{n_slots} dry five-minute slots on {args.day}", "echo_mm_h": args.echo,
                       "persist": args.persist}, fh)


if __name__ == "__main__":
    main()
