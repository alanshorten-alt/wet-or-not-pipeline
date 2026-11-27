"""Ground stations engine: Met Éireann CAMP rain gauges to ground.json.

The gauge file is rewritten about hourly and runs 30-90 minutes behind, so it cannot say
"raining now". Radar owns that. Gauges give the nearest station's name, 24 hour rain and a
wetness reading, always with the reading's age.

Source: https://opendata2.met.ie/opendata2/campstn/pluvA.zip (about 33 MB, 24 hourly zips of
1-minute gauge files). The server allows HTTP Range requests, so only the central directory and
the two zips needed for the last 24 hours (about 3 MB) are downloaded.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

BASE = "https://opendata2.met.ie/opendata2"
PLUV_URL = f"{BASE}/campstn/pluvA.zip"
LOCATIONS_URL = f"{BASE}/CAMP_docs/campStationLocations.geojson"
WINDOW_H = 24
MIN_COVERAGE = 0.9  # share of the window's minutes a gauge must report
MAX_MINUTE_MM = 5.0  # a larger one-minute step is a glitch, not rain
NEIGHBOUR_KM = 10.0
RAIN_NOW_MAX_AGE_MIN = 30  # older than this, "raining now" would be a guess


class HttpFile(io.RawIOBase):
    """Seekable read-only view of a URL using Range requests, so zipfile can open a big archive cheaply."""

    def __init__(self, url: str):
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
            self.last_modified = parsedate_to_datetime(r.headers["Last-Modified"]).astimezone(timezone.utc)
        self.url, self.pos = url, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def read(self, n: int = -1) -> bytes:
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        if self.pos >= end:
            return b""
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end - 1}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        self.pos += len(data)
        return data

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def parse_cmp(text: str) -> list[tuple[datetime, float]]:
    """(UTC time, mm in that minute) from one TOA5 gauge file. Column PluvioAccNRT is the per-minute step."""
    out = []
    for line in text.splitlines()[4:]:
        try:
            row = next(csv.reader([line]))
            t = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            out.append((t, float(row[5])))
        except (ValueError, IndexError, StopIteration):
            continue
    return out


def read_zip_readings(outer: zipfile.ZipFile, member: str) -> dict[int, dict[datetime, float]]:
    """station number -> {minute: mm} from one hourly zip inside the archive."""
    inner = zipfile.ZipFile(io.BytesIO(outer.read(member)))
    out: dict[int, dict[datetime, float]] = {}
    for name in inner.namelist():
        m = re.search(r"Pluv_A_(\d+)_", name)
        if not m:
            continue
        dest = out.setdefault(int(m.group(1)), {})
        for t, mm in parse_cmp(inner.read(name).decode("latin-1")):
            dest[t] = mm
    return out


def pick_members(names: list[str]) -> list[str]:
    """Newest hourly zip (today so far) plus the one that closes the previous UTC day, when listed."""
    names = sorted(n for n in names if re.fullmatch(r"pluvA\d{10}\.zip", n))
    if not names:
        return []
    newest = names[-1]
    day = datetime.strptime(newest[5:13], "%Y%m%d")
    prev = f"pluvA{(day - timedelta(days=1)).strftime('%Y%m%d')}23.zip"
    return [prev, newest] if prev in names else [newest]


def station_rain(minutes: dict[datetime, float], end: datetime) -> dict | None:
    """24 hour total and last reading for one gauge, or None when the gauge looks unusable."""
    start = end - timedelta(hours=WINDOW_H)
    window = {t: mm for t, mm in minutes.items() if start < t <= end}
    if len(window) < MIN_COVERAGE * WINDOW_H * 60:
        return None
    # Negative steps are the bucket being emptied; a gauge with large ones is not trustworthy.
    if sum(-mm for mm in window.values() if mm < 0) > 1.0:
        return None
    total = sum(mm for mm in window.values() if 0 < mm <= MAX_MINUTE_MM)
    last = max(window)
    recent = sum(mm for t, mm in window.items() if t > last - timedelta(minutes=10) and 0 < mm <= MAX_MINUTE_MM)
    return {"rain24h": round(total, 2), "last": last, "recent10": recent}


def wetness(mm: float) -> str:
    if mm < 0.2:
        return "dry"
    if mm < 2:
        return "damp"
    if mm < 10:
        return "soft"
    return "waterlogged"


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def build_regions(stats: dict[int, dict], sites: dict[int, dict], now: datetime) -> list[dict]:
    regions = []
    for stno, s in sorted(stats.items()):
        site = sites.get(stno)
        if not site:
            continue
        near = [
            o["rain24h"]
            for n, o in stats.items()
            if n in sites and haversine_km((site["lat"], site["lng"]), (sites[n]["lat"], sites[n]["lng"])) <= NEIGHBOUR_KM
        ]
        near.sort()
        median = near[len(near) // 2] if len(near) % 2 else (near[len(near) // 2 - 1] + near[len(near) // 2]) / 2
        age = max(0.0, (now - s["last"]).total_seconds() / 60.0)
        if age > RAIN_NOW_MAX_AGE_MIN:
            rain_now = "unsure"
        else:
            rain_now = "yes" if s["recent10"] > 0 else "no"
        regions.append(
            {
                "stationId": str(stno),
                "name": site["name"],
                "lat": site["lat"],
                "lng": site["lng"],
                "rainNow": rain_now,
                "rain24hMm": {"median": round(median, 2), "low": round(near[0], 2), "high": round(near[-1], 2)},
                "stationCount": len(near),
                "wetness": wetness(median),
                "readingAgeMin": round(age, 1),
            }
        )
    return regions


def tidy_name(raw: str) -> str:
    """CAMP names are upper case. Title-case them, keeping vowel-free initials like JFK upper case."""
    return " ".join(w.upper() if re.fullmatch(r"[A-Za-z]{2,4}", w) and not re.search(r"[aeiouAEIOU]", w) else w.title() for w in raw.split())


def load_sites(geojson: dict) -> dict[int, dict]:
    out = {}
    for f in geojson.get("features", []):
        p = f.get("properties", {})
        if p.get("close_date") or "stno" not in p:
            continue
        lng, lat = f["geometry"]["coordinates"][:2]
        out[int(p["stno"])] = {"name": tidy_name(str(p["location"])), "lat": float(lat), "lng": float(lng)}
    return out


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def carry_forward(prev: dict, now: datetime) -> dict:
    """Same readings, with each reading's age advanced to now."""
    elapsed = max(0.0, (now - datetime.fromisoformat(prev["generatedAt"].replace("Z", "+00:00"))).total_seconds() / 60.0)
    regions = [{**r, "readingAgeMin": round(r["readingAgeMin"] + elapsed, 1)} for r in prev["regions"]]
    return {**prev, "regions": regions}


def build_ground(now: datetime, prev: dict | None = None) -> dict:
    """ground.json content. Reuses the previous file when the source has not changed since it was built."""
    src = HttpFile(PLUV_URL)
    if prev and prev.get("regions") and datetime.fromisoformat(prev["generatedAt"].replace("Z", "+00:00")) >= src.last_modified:
        return carry_forward(prev, now)
    outer = zipfile.ZipFile(src)
    readings: dict[int, dict[datetime, float]] = {}
    for member in pick_members(outer.namelist()):
        for stno, mins in read_zip_readings(outer, member).items():
            readings.setdefault(stno, {}).update(mins)
    if not readings:
        raise RuntimeError("no gauge readings in pluvA.zip")
    end = max(t for mins in readings.values() for t in mins)
    stats = {n: s for n, m in readings.items() if (s := station_rain(m, end))}
    regions = build_regions(stats, load_sites(get_json(LOCATIONS_URL)), now)
    return {"generatedAt": iso(now), "sourceTime": iso(end), "regions": regions}
