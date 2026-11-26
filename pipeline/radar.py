"""Decode Met Éireann ODIM HDF5 polar volumes into a rain-rate grid.

Pure functions, no network. Output matches the RadarFrame contract in
src/lib/contracts.ts: an equirectangular lat/lng grid, row-major from the
north-west corner, rain rate in mm/h. The phone never needs a projection
library; the composite's own projection is only used by the comparison tool.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import h5py
import numpy as np

EARTH_KM = 6371.0

# Marshall-Palmer: Z = a * R^b, Z in mm^6/m^3, R in mm/h.
ZR_A = 200.0
ZR_B = 1.6
# Above this reflectivity is treated as hail contamination and capped.
DBZ_CAP = 53.0
# Below this is noise or drizzle that is not worth showing as rain.
DBZ_MIN = 5.0


@dataclass(frozen=True)
class Grid:
    south: float
    west: float
    north: float
    east: float
    cols: int
    rows: int
    cell_km: float

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        """Latitude and longitude of every cell centre, shape (rows, cols), row 0 = north."""
        lats = self.north - (np.arange(self.rows) + 0.5) * (self.north - self.south) / self.rows
        lons = self.west + (np.arange(self.cols) + 0.5) * (self.east - self.west) / self.cols
        lon_g, lat_g = np.meshgrid(lons, lats)
        return lat_g, lon_g

    def meta(self) -> dict:
        return {
            "bounds": {"south": self.south, "west": self.west, "north": self.north, "east": self.east},
            "cols": self.cols,
            "rows": self.rows,
            "cellKm": self.cell_km,
        }


def make_grid(south=51.2, west=-10.9, north=55.5, east=-5.2, cell_km=2.5) -> Grid:
    """Ireland at roughly cell_km spacing (2.5 km matches the Met Éireann model grid)."""
    mid_lat = math.radians((south + north) / 2)
    rows = round((north - south) * 111.19 / cell_km)
    cols = round((east - west) * 111.19 * math.cos(mid_lat) / cell_km)
    return Grid(south, west, north, east, cols, rows, cell_km)


@dataclass(frozen=True)
class RadarSite:
    name: str
    lat: float
    lon: float
    # (max_range_km, min_elevation_deg): inside this range only tilts at or above
    # the elevation are used, to avoid beam clipping hills (ground clutter).
    low_tilt_exclusions: tuple[tuple[float, float], ...] = ()


# Dublin: skip the 0.5 degree tilt within 40 km so Dublin and Wicklow hill
# echoes do not read as rain over south Dublin.
DUBLIN = RadarSite("Dublin", 53.4299, -6.2443, ((40.0, 1.0),))
SHANNON = RadarSite("Shannon", 52.692787, -8.919994)


@dataclass
class Tilt:
    elevation: float
    dbz: np.ndarray  # (nrays, nbins), NaN where there is no coverage, -inf where no echo
    ray_az: np.ndarray  # centre azimuth of each ray, degrees, ascending
    rscale_km: float
    rstart_km: float


@dataclass
class Volume:
    site_lat: float
    site_lon: float
    obs_time: datetime
    tilts: list[Tilt] = field(default_factory=list)


def _text(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def read_volume(path: str) -> Volume:
    with h5py.File(path, "r") as f:
        what = f["what"].attrs
        date = _text(what["date"])
        time = _text(what["time"])
        obs = datetime.strptime(date + time, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        where = f["where"].attrs
        vol = Volume(float(where["lat"]), float(where["lon"]), obs)
        names = sorted((n for n in f if n.startswith("dataset")), key=lambda n: int(n[7:]))
        for name in names:
            g = f[name]
            w = g["where"].attrs
            q = g["data1/what"].attrs
            raw = g["data1/data"][:]
            dbz = raw.astype(np.float32) * float(q["gain"]) + float(q["offset"])
            dbz[raw == int(q["undetect"])] = -np.inf  # measured, no echo
            dbz[raw == int(q["nodata"])] = np.nan  # not measured
            how = g["how"].attrs
            if "startazA" in how and "stopazA" in how:
                a0, a1 = np.asarray(how["startazA"]), np.asarray(how["stopazA"])
                az = (a0 + ((a1 - a0 + 180) % 360 - 180) / 2) % 360
            else:
                az = (np.arange(int(w["nrays"])) + 0.5) * 360.0 / int(w["nrays"])
            order = np.argsort(az)
            vol.tilts.append(
                Tilt(
                    float(w["elangle"]),
                    dbz[order],
                    az[order],
                    float(w["rscale"]) / 1000.0,
                    float(w["rstart"]) / 1000.0,
                )
            )
    vol.tilts.sort(key=lambda t: t.elevation)
    return vol


def dbz_to_rain(dbz: np.ndarray) -> np.ndarray:
    """Reflectivity (dBZ) to rain rate (mm/h). -inf (no echo) and below DBZ_MIN give 0; NaN stays NaN."""
    d = np.minimum(dbz, DBZ_CAP)
    with np.errstate(over="ignore", invalid="ignore"):
        r = (np.power(10.0, d / 10.0) / ZR_A) ** (1.0 / ZR_B)
    r = np.where(d < DBZ_MIN, 0.0, r)
    return np.where(np.isnan(dbz), np.nan, r)


def range_azimuth(site_lat: float, site_lon: float, lat: np.ndarray, lon: np.ndarray):
    """Great-circle distance (km) and initial bearing (degrees from north) from the site."""
    p1, p2 = math.radians(site_lat), np.radians(lat)
    dl = np.radians(lon - site_lon)
    a = np.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    dist = 2 * EARTH_KM * np.arcsin(np.sqrt(a))
    y = np.sin(dl) * np.cos(p2)
    x = math.cos(p1) * np.sin(p2) - math.sin(p1) * np.cos(p2) * np.cos(dl)
    return dist, (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def _usable_tilt_index(vol: Volume, site: RadarSite, dist_km: np.ndarray) -> np.ndarray:
    """For each cell, which tilt to sample: the lowest allowed at that range."""
    elevs = np.array([t.elevation for t in vol.tilts])
    min_elev = np.zeros_like(dist_km)
    for max_range, floor in site.low_tilt_exclusions:
        min_elev = np.where(dist_km < max_range, np.maximum(min_elev, floor), min_elev)
    # first tilt whose elevation is >= the floor (falls back to the highest tilt)
    idx = np.searchsorted(elevs, min_elev - 1e-6, side="left")
    return np.minimum(idx, len(elevs) - 1)


def sample_site(vol: Volume, site: RadarSite, lat: np.ndarray, lon: np.ndarray, max_range_km=250.0):
    """Rain rate (mm/h) at each grid cell from one radar; NaN outside its coverage. Also returns range."""
    dist, az = range_azimuth(vol.site_lat, vol.site_lon, lat, lon)
    tilt_idx = _usable_tilt_index(vol, site, dist)
    out = np.full(dist.shape, np.nan, dtype=np.float32)
    for i, tilt in enumerate(vol.tilts):
        m = (tilt_idx == i) & (dist < max_range_km)
        if not m.any():
            continue
        # nearest ray by azimuth (wrap-around aware), nearest bin by range
        ray = np.searchsorted(tilt.ray_az, az[m])
        lo = (ray - 1) % len(tilt.ray_az)
        hi = ray % len(tilt.ray_az)
        d_lo = np.abs((az[m] - tilt.ray_az[lo] + 180) % 360 - 180)
        d_hi = np.abs((az[m] - tilt.ray_az[hi] + 180) % 360 - 180)
        ray = np.where(d_lo <= d_hi, lo, hi)
        nbins = tilt.dbz.shape[1]
        b = np.floor((dist[m] - tilt.rstart_km) / tilt.rscale_km).astype(int)
        valid = (b >= 0) & (b < nbins)
        vals = np.full(b.shape, np.nan, dtype=np.float32)
        vals[valid] = dbz_to_rain(tilt.dbz[ray[valid], b[valid]])
        out[m] = vals
    return out, dist


def despeckle(rain: np.ndarray, ratio: float = 4.0, floor: float = 2.0) -> np.ndarray:
    """Replace isolated spikes (wind farms, interference, sun spikes) with the local median.

    A cell is a spike when it exceeds ratio times its 3x3 neighbourhood median
    plus floor mm/h. Real convective cores are wider than one 2.5 km cell, so
    their neighbours are high too and they are left alone.
    """
    nan = np.isnan(rain)
    filled = np.where(nan, 0.0, rain)
    pad = np.pad(filled, 1, mode="edge")
    h, w = filled.shape
    stack = np.stack([pad[i : i + h, j : j + w] for i in range(3) for j in range(3) if (i, j) != (1, 1)])
    med = np.median(stack, axis=0)
    spike = filled > ratio * med + floor
    return np.where(spike & ~nan, med, rain).astype(np.float32)


def merge_sites(a: np.ndarray, dist_a: np.ndarray, b: np.ndarray, dist_b: np.ndarray, clean_km=120.0, blend_km=200.0):
    """Prefer the nearer radar inside its clean range; blend by inverse-distance in the overlap.

    Never takes max(a, b): the farther beam is high and wide, and max would
    amplify its bright-band and overshoot artefacts.
    """
    near_a = dist_a <= dist_b
    nearer = np.where(near_a, a, b)
    farther = np.where(near_a, b, a)
    d_near = np.where(near_a, dist_a, dist_b)
    d_far = np.where(near_a, dist_b, dist_a)

    # Start from the nearer radar; if it has no coverage use the other one.
    out = np.where(np.isnan(nearer), farther, nearer)
    # Blend only where both radars see the cell and the nearer one is beyond its clean range.
    both = ~np.isnan(nearer) & ~np.isnan(farther) & (d_near > clean_km) & (d_far < blend_km + 50)
    w_near = (1.0 / np.maximum(d_near, 1.0) ** 2) / (1.0 / np.maximum(d_near, 1.0) ** 2 + 1.0 / np.maximum(d_far, 1.0) ** 2)
    blended = w_near * nearer + (1.0 - w_near) * farther
    return np.where(both, blended, out).astype(np.float32)


def load_clutter_mask(path: str, grid: Grid) -> np.ndarray:
    """Boolean (rows, cols) mask of persistent static echoes, built by validate_hours.py.

    Raises if it was built for a different grid, so a stale mask can never be
    applied to the wrong cells.
    """
    import json

    with open(path) as fh:
        m = json.load(fh)
    if (m["rows"], m["cols"]) != (grid.rows, grid.cols):
        raise ValueError(f"clutter mask is {m['rows']}x{m['cols']} but the grid is {grid.rows}x{grid.cols}")
    mask = np.zeros(grid.rows * grid.cols, dtype=bool)
    mask[m["cells"]] = True
    return mask.reshape(grid.rows, grid.cols)


def build_frame(volumes: list[tuple[Volume, RadarSite]], grid: Grid, now: datetime | None = None,
                clutter_mask: np.ndarray | None = None) -> dict:
    """RadarFrame dict. sourceTime is the scan time from the file, never the run time."""
    lat, lon = grid.centers()
    sampled = []
    for v, s in volumes:
        r, d = sample_site(v, s, lat, lon)
        sampled.append((despeckle(r), d))
    rain, dist = sampled[0]
    for r2, d2 in sampled[1:]:
        rain = merge_sites(rain, dist, r2, d2)
        dist = np.minimum(dist, d2)
    rain = np.nan_to_num(rain, nan=0.0)
    if clutter_mask is not None:
        rain = np.where(clutter_mask, 0.0, rain)
    source = min(v.obs_time for v, _ in volumes)
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    cells = [0 if x < 0.05 else round(float(x), 1) for x in rain.ravel()]
    return {"generatedAt": iso(generated), "sourceTime": iso(source), "grid": grid.meta(), "rainMmh": cells}
