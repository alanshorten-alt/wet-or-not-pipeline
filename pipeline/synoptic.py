"""Synoptic engine: Met Éireann "Latest Observations" CSV -> ground.json `synoptic` list.

The CSV (https://www.met.ie/latest-reports/observations/download) has about 25 synoptic stations,
one row each for the latest hour: temperature, weather, wind and gust in knots, wind direction,
humidity, hourly rain and pressure. It carries no coordinates and no observation time, so:
- coordinates come from the table below (Met Éireann's "Weather Observing Stations" page, in
  degrees/minutes/seconds; the five airport stations are not on that page and use approximate
  airport reference positions);
- observedAt is assumed to be the start of the latest whole hour before the fetch, and the app
  must show the age that implies rather than "live".
A station name we do not recognise is skipped with a warning, never guessed.
"""

import csv
import io
import re
import urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://www.met.ie/latest-reports/observations/download"
KT_TO_KMH = 1.852


def dms(d, m, s, hemi):
    v = d + m / 60 + s / 3600
    return -v if hemi in "SW" else v


# key = lower-case letters only, value = (display name, lat, lng)
STATIONS = {
    "athenry": ("Athenry", dms(53, 17, 21, "N"), dms(8, 47, 8, "W")),
    "ballyhaise": ("Ballyhaise", dms(54, 3, 5, "N"), dms(7, 18, 35, "W")),
    "belmullet": ("Belmullet", dms(54, 13, 39, "N"), dms(10, 0, 25, "W")),
    "claremorris": ("Claremorris", dms(53, 42, 39, "N"), dms(8, 59, 33, "W")),
    "dunsany": ("Dunsany", dms(53, 30, 57, "N"), dms(6, 39, 36, "W")),
    "finner": ("Finner", dms(54, 29, 38, "N"), dms(8, 14, 35, "W")),
    "gurteen": ("Gurteen", dms(53, 3, 11, "N"), dms(8, 0, 31, "W")),
    "johnstowncastle": ("Johnstown Castle", dms(52, 17, 52, "N"), dms(6, 29, 48, "W")),
    "macehead": ("Mace Head", dms(53, 19, 33, "N"), dms(9, 54, 3, "W")),
    "malinhead": ("Malin Head", dms(55, 22, 20, "N"), dms(7, 20, 20, "W")),
    "markreecastle": ("Markree Castle", dms(54, 10, 30, "N"), dms(8, 27, 20, "W")),
    "moorepark": ("Moore Park", dms(52, 9, 50, "N"), dms(8, 15, 50, "W")),
    "mtdillon": ("Mount Dillon", dms(53, 43, 37, "N"), dms(7, 58, 51, "W")),
    "mountdillon": ("Mount Dillon", dms(53, 43, 37, "N"), dms(7, 58, 51, "W")),
    "mullingar": ("Mullingar", dms(53, 32, 14, "N"), dms(7, 21, 44, "W")),
    "newportmayo": ("Newport", dms(53, 55, 20, "N"), dms(9, 34, 20, "W")),
    "newport": ("Newport", dms(53, 55, 20, "N"), dms(9, 34, 20, "W")),
    "oakpark": ("Oak Park", dms(52, 51, 40, "N"), dms(6, 54, 55, "W")),
    "phoenixpark": ("Phoenix Park", dms(53, 21, 50, "N"), dms(6, 20, 0, "W")),
    "rochespoint": ("Roche's Point", dms(51, 47, 35, "N"), dms(8, 14, 40, "W")),
    "sherkinisland": ("Sherkin Island", dms(51, 28, 35, "N"), dms(9, 25, 40, "W")),
    "valentia": ("Valentia", dms(51, 56, 23, "N"), dms(10, 14, 40, "W")),
    # Airport stations: approximate airport reference positions.
    "casement": ("Casement", 53.3025, -6.4378),
    "casementaerodrome": ("Casement", 53.3025, -6.4378),
    "dublin": ("Dublin Airport", 53.4272, -6.2412),
    "dublinairport": ("Dublin Airport", 53.4272, -6.2412),
    "cork": ("Cork Airport", 51.8472, -8.4861),
    "corkairport": ("Cork Airport", 51.8472, -8.4861),
    "knock": ("Knock Airport", 53.9100, -8.8184),
    "knockairport": ("Knock Airport", 53.9100, -8.8184),
    "shannon": ("Shannon Airport", 52.6910, -8.9180),
    "shannonairport": ("Shannon Airport", 52.6910, -8.9180),
}


def _key(name):
    return re.sub(r"[^a-z]", "", name.lower())


def _num(s):
    s = (s or "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _text(s):
    s = (s or "").strip()
    return None if s in ("", "-") else s


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_observations(text, now):
    """Return the contract's synoptic list. `now` is the timezone-aware fetch time."""
    observed = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    age = round((now - observed).total_seconds() / 60.0)
    out, seen = [], set()
    for row in csv.reader(io.StringIO(text.lstrip("﻿"))):
        if len(row) < 9 or row[0].strip().lower() == "station":
            continue
        hit = STATIONS.get(_key(row[0]))
        if not hit:
            print(f"synoptic: unknown station {row[0]!r}, skipped")
            continue
        name, lat, lng = hit
        if name in seen:
            continue
        seen.add(name)
        wind, gust = _num(row[3]), _num(row[4])
        rain = _num(row[7])
        out.append({
            "stationId": _slug(name),
            "name": name,
            "lat": round(lat, 4),
            "lng": round(lng, 4),
            "tempC": _num(row[1]),
            "weather": _text(row[2]),
            "windKmh": None if wind is None else round(wind * KT_TO_KMH),
            "gustKmh": None if gust is None else round(gust * KT_TO_KMH),
            "windFrom": _text(row[5]),
            "humidityPct": _num(row[6]),
            "rainHourMm": None if rain is None else max(rain, 0.0),
            "pressureHpa": _num(row[8]),
            "observedAt": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "readingAgeMin": age,
        })
    return out


def fetch_observations(now):
    req = urllib.request.Request(URL, headers={"User-Agent": "wet-or-not-pipeline"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
    rows = parse_observations(text, now)
    if not rows:
        raise RuntimeError("observations CSV had no recognised stations")
    return rows
