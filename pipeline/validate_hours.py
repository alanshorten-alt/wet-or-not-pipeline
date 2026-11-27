"""Multi-regime radar validation and clutter-mask builder.

    python pipeline/validate_hours.py --cache DIR --day 2026-11-26 \
        --dry 06 08 09 --wet 11 12 14 16 [--write-mask pipeline/clutter_mask.json]

For each hour the argument names the END of the hour (the composite stamp
T_PASH21_..HH0000 is the hour ending at HH, as compare_composite.py found).
It rebuilds our 5-minute rain on the output grid for the 12 slots in that
hour, accumulates, and compares with the composite at pixel level and on a 3x3
neighbourhood mean. Dry hours also feed a persistent-echo (clutter) mask.

Several days at once: pass --days D1 D2 ... --auto-dry instead of --day and --dry. Dry hours are
then picked from the hourly composite itself (hours where almost nothing is raining anywhere),
and a cell only enters the mask if it echoes in at least --persist of the dry slots AND does so on
at least --min-days separate days, so a one-off transient echo cannot be mistaken for clutter.

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


def wet_share(ref: np.ndarray, thresh: float = 0.1) -> float:
    """Share of covered composite cells with more than `thresh` mm in the hour."""
    ok = np.isfinite(ref)
    return float((ref[ok] > thresh).mean()) if ok.any() else float("nan")


def static_cells(echo_by_day: list[np.ndarray], slots_by_day: list[int], persist: float, min_days: int) -> np.ndarray:
    """Cells echoing in >= `persist` of all dry slots and in >= `persist` of the dry slots of >= `min_days` days."""
    total = sum(echo_by_day) / max(sum(slots_by_day), 1)
    days_hit = sum((e / max(n, 1) >= persist).astype(int) for e, n in zip(echo_by_day, slots_by_day) if n > 0)
    return (total >= persist) & (days_hit >= min_days)


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


def save_echo(directory: str, day: str, echo_slots: np.ndarray, n_slots: int, hours: list[str], grid, echo: float) -> None:
    """One small file per day: how many dry slots each echoing cell echoed in."""
    os.makedirs(directory, exist_ok=True)
    flat = echo_slots.ravel()
    cells = {str(i): int(flat[i]) for i in np.flatnonzero(flat > 0)}
    with open(os.path.join(directory, f"{day}.json"), "w") as fh:
        json.dump({"day": day, "rows": grid.rows, "cols": grid.cols, "slots": n_slots, "dry_hours": hours,
                   "echo_mm_h": echo, "cells": cells}, fh, separators=(",", ":"))


def combine(args) -> None:
    """Build the mask from saved per-day echo files: static = persistent in the dry slots on several days."""
    files = sorted(f for f in os.listdir(args.combine) if f.endswith(".json"))
    if not files:
        sys.exit(f"no echo files in {args.combine}")
    days = [json.load(open(os.path.join(args.combine, f))) for f in files]
    rows, cols = days[0]["rows"], days[0]["cols"]
    if any((d["rows"], d["cols"]) != (rows, cols) for d in days):
        sys.exit("echo files were built for different grids")
    echo_by_day = []
    for d in days:
        a = np.zeros(rows * cols)
        for i, c in d["cells"].items():
            a[int(i)] = c
        echo_by_day.append(a)
    slots = [d["slots"] for d in days]
    min_days = args.min_days if len(days) > 1 else 1
    mask = static_cells(echo_by_day, slots, args.persist, min_days)
    idx = np.flatnonzero(mask).tolist()
    print(f"clutter mask: {len(idx)} cells from {len(days)} day(s), {sum(slots)} dry slots, persistent on >= {min_days} day(s)")
    if args.write_mask:
        with open(args.write_mask, "w") as fh:
            json.dump({"rows": rows, "cols": cols, "cells": idx,
                       "built_from": f"{sum(slots)} dry five-minute slots on {len(days)} days ({days[0]['day']} to {days[-1]['day']})",
                       "echo_mm_h": days[0]["echo_mm_h"], "persist": args.persist, "min_days": min_days}, fh)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache")
    ap.add_argument("--day", help="one day, with --dry/--wet hours")
    ap.add_argument("--days", nargs="*", default=[], help="several days; use with --auto-dry")
    ap.add_argument("--dry", nargs="*", default=[])
    ap.add_argument("--wet", nargs="*", default=[])
    ap.add_argument("--auto-dry", action="store_true", help="pick the dry hours of each --days day from the composite")
    ap.add_argument("--dry-max-share", type=float, default=0.02, help="largest wet share of cells for an hour to count as dry")
    ap.add_argument("--max-dry-hours", type=int, default=6, help="most dry hours used per day, spread across the day")
    ap.add_argument("--min-days", type=int, default=2, help="days a cell must be persistent on (multi-day runs)")
    ap.add_argument("--write-mask")
    ap.add_argument("--save-echo", help="directory to write one small echo-count file per day (input for --combine)")
    ap.add_argument("--combine", help="build the mask from the echo-count files in this directory; no downloads")
    ap.add_argument("--save", help="directory to keep each hour's ours/ref grids as .npz for offline analysis")
    ap.add_argument("--persist", type=float, default=0.5, help="share of dry slots a cell must be echoing in")
    ap.add_argument("--echo", type=float, default=0.1, help="mm/h counted as an echo in a dry slot")
    args = ap.parse_args()
    if args.combine:
        combine(args)
        return
    if bool(args.day) == bool(args.days):
        ap.error("give either --day (with --dry/--wet) or --days (with --auto-dry)")
    if args.days and not args.auto_dry:
        ap.error("--days needs --auto-dry")

    if not args.cache:
        ap.error("--cache is required unless --combine is used")
    os.makedirs(args.cache, exist_ok=True)
    grid = make_grid()
    lat, lon = grid.centers()
    d_dub, _ = range_azimuth(DUBLIN.lat, DUBLIN.lon, lat, lon)
    d_sha, _ = range_azimuth(SHANNON.lat, SHANNON.lon, lat, lon)
    far = np.minimum(d_dub, d_sha) > 250.0
    print(f"cells beyond 250 km of both radars: {int(far.sum())} of {far.size}")

    echo_by_day: list[np.ndarray] = []
    slots_by_day: list[int] = []
    for day_s in (args.days or [args.day]):
        day = datetime.fromisoformat(day_s).replace(tzinfo=timezone.utc)
        files = cc.listing(day)
        dry_hours, wet_hours = list(args.dry), list(args.wet)
        refs: dict[str, np.ndarray] = {}
        if args.auto_dry:
            candidates = []
            for h in range(1, 24):
                end = day.replace(hour=h)
                name = f"T_PASH21_C_EIDB_{end:%Y%m%d%H%M%S}.hdf"
                if name not in files:
                    continue
                refs[f"{h:02d}"] = cc.composite_on_grid(fetch(args.cache, end, name), grid)
                share = wet_share(refs[f"{h:02d}"])
                print(f"  {day_s} {h:02d}:00 wet share {share:.1%}")
                if share <= args.dry_max_share:
                    candidates.append(h)
            step = max(len(candidates) / args.max_dry_hours, 1.0)
            dry_hours = [f"{candidates[int(i * step)]:02d}" for i in range(min(len(candidates), args.max_dry_hours))]
            print(f"{day_s}: {len(candidates)} dry hours (wet share <= {args.dry_max_share:.1%}), using {dry_hours}")

        echo_slots, n_slots = np.zeros(lat.shape), 0
        for kind, hours in (("dry", dry_hours), ("wet", wet_hours)):
            for h in hours:
                end = day.replace(hour=int(h))
                ref = refs.get(h)
                if ref is None:
                    comp_name = f"T_PASH21_C_EIDB_{end:%Y%m%d%H%M%S}.hdf"
                    ref = cc.composite_on_grid(fetch(args.cache, end, comp_name), grid)
                rain = slot_rain(hour_slots(end), grid, files, args.cache, end)
                have = ~np.isnan(rain[:, 0, 0])
                ours = np.nansum(rain, axis=0) * (5.0 / 60.0) * (12 / max(have.sum(), 1))
                if args.save:
                    os.makedirs(args.save, exist_ok=True)
                    np.savez_compressed(os.path.join(args.save, f"{day_s}_{kind}_{h}.npz"), ours=ours, ref=ref, lat=lat,
                                        lon=lon, d_dub=d_dub, d_sha=d_sha)
                res = compare(ours, ref)
                res["slots"] = int(have.sum())
                res["max_rain_beyond_250km"] = float(rain[have][:, far].max()) if far.any() and have.any() else 0.0
                print(day_s, kind, f"{h}:00", json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in res.items()}))
                if kind == "dry":
                    echo_slots += (rain[have] > args.echo).sum(axis=0)
                    n_slots += int(have.sum())
        echo_by_day.append(echo_slots)
        slots_by_day.append(n_slots)
        if args.save_echo and n_slots:
            save_echo(args.save_echo, day_s, echo_slots, n_slots, dry_hours, grid, args.echo)

    n_total = sum(slots_by_day)
    if n_total and args.write_mask:
        min_days = args.min_days if len(echo_by_day) > 1 else 1
        mask = static_cells(echo_by_day, slots_by_day, args.persist, min_days)
        share = sum(echo_by_day) / n_total
        idx = np.flatnonzero(mask.ravel()).tolist()
        print(f"clutter mask: {len(idx)} cells echo in >= {args.persist:.0%} of {n_total} dry slots on >= {min_days} day(s)")
        for i in idx[:40]:
            r, c = divmod(i, lat.shape[1])
            print(f"  cell {r},{c} lat {lat[r, c]:.2f} lon {lon[r, c]:.2f} share {share[r, c]:.2f}")
        days_used = args.days or [args.day]
        with open(args.write_mask, "w") as fh:
            json.dump({"rows": int(lat.shape[0]), "cols": int(lat.shape[1]), "cells": idx,
                       "built_from": f"{n_total} dry five-minute slots on {', '.join(days_used)}", "echo_mm_h": args.echo,
                       "persist": args.persist, "min_days": min_days}, fh)


if __name__ == "__main__":
    main()
