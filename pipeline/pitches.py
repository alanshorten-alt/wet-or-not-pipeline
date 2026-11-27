"""Export pitch outlines from an OpenStreetMap extract into small tile files.

    python pipeline/pitches.py ireland.osm.pbf out/pitches

Reads closed ways tagged leisure=pitch, stadium or track and writes `<ix>_<iy>.json` (0.25 degree cells,
see PitchTileSchema in contracts/contracts.ts) plus `index.json` listing the cells that exist.
Data (c) OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone

import osmium

WEST, SOUTH, SIZE = -11.0, 51.0, 0.25
LNG_RANGE = (-10.9, -5.2)
LAT_RANGE = (51.2, 55.6)
KINDS = ("pitch", "stadium", "track")
MIN_EXTENT_DEG = 0.00004  # about 4 m: smaller than that is a mapping scrap, not a pitch
MAX_RING_POINTS = 300


def cell_of(lng: float, lat: float) -> tuple[int, int]:
    return math.floor((lng - WEST) / SIZE), math.floor((lat - SOUTH) / SIZE)


def clean_ring(coords: list[tuple[float, float]]) -> list[list[float]] | None:
    """Round to 5 decimals, drop repeated points, keep it closed. None when it is not usable."""
    pts: list[list[float]] = []
    for lng, lat in coords:
        p = [round(lng, 5), round(lat, 5)]
        if not pts or p != pts[-1]:
            pts.append(p)
    if len(pts) < 4 or pts[0] != pts[-1] or len(pts) > MAX_RING_POINTS:
        return None
    lngs = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    if max(lngs) - min(lngs) < MIN_EXTENT_DEG and max(lats) - min(lats) < MIN_EXTENT_DEG:
        return None
    if not (LNG_RANGE[0] <= min(lngs) and max(lngs) <= LNG_RANGE[1] and LAT_RANGE[0] <= min(lats) and max(lats) <= LAT_RANGE[1]):
        return None
    return pts


def sport_of(tags) -> str | None:
    raw = tags.get("sport")
    if not raw:
        return None
    first = raw.split(";")[0].strip()
    return first[:30] or None


def read_pitches(pbf: str) -> list[dict]:
    out = []
    fp = osmium.FileProcessor(pbf).with_locations().with_filter(osmium.filter.KeyFilter("leisure"))
    for obj in fp:
        if not obj.is_way() or not obj.is_closed():
            continue
        kind = obj.tags.get("leisure")
        if kind not in KINDS:
            continue
        try:
            coords = [(n.lon, n.lat) for n in obj.nodes]
        except osmium.InvalidLocationError:
            continue
        ring = clean_ring(coords)
        if ring:
            out.append({"kind": kind, "sport": sport_of(obj.tags), "ring": ring})
    return out


def export(pbf: str, out_dir: str, now: datetime | None = None) -> dict[str, int]:
    """Writes the tile files and index.json; returns {cell: pitch count}."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cells: dict[tuple[int, int], list[dict]] = {}
    for p in read_pitches(pbf):
        lng = sum(pt[0] for pt in p["ring"]) / len(p["ring"])
        lat = sum(pt[1] for pt in p["ring"]) / len(p["ring"])
        cells.setdefault(cell_of(lng, lat), []).append(p)
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        if old.endswith(".json"):
            os.remove(os.path.join(out_dir, old))
    counts: dict[str, int] = {}
    for (ix, iy), pitches in sorted(cells.items()):
        name = f"{ix}_{iy}"
        counts[name] = len(pitches)
        tile = {"schemaVersion": 1, "generatedAt": stamp, "ix": ix, "iy": iy, "pitches": pitches[:5000]}
        with open(os.path.join(out_dir, f"{name}.json"), "w") as f:
            json.dump(tile, f, separators=(",", ":"))
    index = {"schemaVersion": 1, "generatedAt": stamp, "cells": list(counts)}
    with open(os.path.join(out_dir, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("out_dir")
    a = ap.parse_args()
    counts = export(a.pbf, a.out_dir)
    print(f"{sum(counts.values())} pitches in {len(counts)} cells")
