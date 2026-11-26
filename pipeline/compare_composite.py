"""Check our decoded rain against Met Éireann's hourly composite accumulation.

    python pipeline/compare_composite.py 2026-11-26T15:00:00

Downloads the 12 five-minute Dublin and Shannon volumes for that hour, builds
our own accumulation on the output grid, and reports bias and correlation
against T_PASH21 (the Dublin-Shannon composite) for the hour ending at, and
the hour starting at, the stamped time (the product's convention is not
documented, so we test both). Manual tool: it needs network access.
"""
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

import h5py
import numpy as np
from pyproj import Proj

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from radar import DUBLIN, SHANNON, make_grid, merge_sites, despeckle, read_volume, sample_site  # noqa: E402

BASE = "https://opendata2.met.ie/radar"


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def listing(day: datetime) -> list[str]:
    html = get(f"{BASE}/{day:%Y/%m/%d}/").decode("latin-1")
    return sorted(set(re.findall(r'T_P[A-Z0-9]+_C_EIDB_\d{14}\.(?:h5|hdf)', html)))


def nearest(files: list[str], prefix: str, t: datetime, tol_s=150) -> str | None:
    best, best_dt = None, tol_s + 1
    for f in files:
        if not f.startswith(prefix):
            continue
        ft = datetime.strptime(re.search(r"_(\d{14})\.", f).group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        dt = abs((ft - t).total_seconds())
        if dt < best_dt:
            best, best_dt = f, dt
    return best


def accumulate(slots: list[datetime], grid, files: list[str], tmp: str) -> np.ndarray:
    lat, lon = grid.centers()
    total, used = np.zeros(lat.shape, dtype=np.float64), 0
    for t in slots:
        parts = []
        for prefix, site in (("T_PAGZ41", DUBLIN), ("T_PAGZ40", SHANNON)):
            f = nearest(files, prefix, t)
            if f is None:
                continue
            path = f"{tmp}/{f}"
            try:
                open(path, "rb").close()
            except FileNotFoundError:
                open(path, "wb").write(get(f"{BASE}/{t:%Y/%m/%d}/{f}"))
            r, d = sample_site(read_volume(path), site, lat, lon)
            parts.append((despeckle(r), d))
        if len(parts) < 2:
            continue
        rain = np.nan_to_num(merge_sites(parts[0][0], parts[0][1], parts[1][0], parts[1][1]), nan=0.0)
        total += rain * (5.0 / 60.0)
        used += 1
    print(f"  used {used} of {len(slots)} five-minute slots")
    return total * (len(slots) / max(used, 1))


def composite_on_grid(path: str, grid) -> np.ndarray:
    with h5py.File(path, "r") as f:
        w = f["where"].attrs
        data = f["dataset1/data1/data"][:].astype(np.float64)
        proj = Proj(w["projdef"].decode())
        x0, y0 = proj(float(w["UL_lon"]), float(w["UL_lat"]))
        xs, ys = float(w["xscale"]), float(w["yscale"])
    lat, lon = grid.centers()
    x, y = proj(lon, lat)
    col = np.floor((x - x0) / xs).astype(int)
    row = np.floor((y0 - y) / ys).astype(int)
    ok = (row >= 0) & (row < data.shape[0]) & (col >= 0) & (col < data.shape[1])
    out = np.full(lat.shape, np.nan)
    out[ok] = data[row[ok], col[ok]]
    out[out < 0] = np.nan
    return out


def stats(ours: np.ndarray, ref: np.ndarray) -> str:
    m = np.isfinite(ref) & np.isfinite(ours) & ((ref > 0.1) | (ours > 0.1))
    if m.sum() < 20:
        return "too few comparable cells"
    corr = np.corrcoef(ours[m], ref[m])[0, 1]
    return (f"cells {m.sum()}, ours/ref total {ours[m].sum() / ref[m].sum():.2f}, "
            f"mean ours {ours[m].mean():.2f} ref {ref[m].mean():.2f} mm, correlation {corr:.2f}")


def main() -> None:
    stamp = datetime.fromisoformat(sys.argv[1]).replace(tzinfo=timezone.utc)
    grid = make_grid()
    with tempfile.TemporaryDirectory() as tmp:
        comp = f"{tmp}/comp.hdf"
        name = f"T_PASH21_C_EIDB_{stamp:%Y%m%d%H%M%S}.hdf"
        open(comp, "wb").write(get(f"{BASE}/{stamp:%Y/%m/%d}/{name}"))
        ref = composite_on_grid(comp, grid)
        print(f"composite {name}: mean {np.nanmean(ref):.2f} mm over the grid")
        for label, start in (("hour ending at stamp", stamp - timedelta(hours=1)), ("hour starting at stamp", stamp)):
            slots = [start + timedelta(minutes=5 * i) for i in range(12)]
            files = sorted(set(listing(slots[0]) + listing(slots[-1])))
            print(label)
            print("  " + stats(accumulate(slots, grid, files, tmp), ref))


if __name__ == "__main__":
    main()
