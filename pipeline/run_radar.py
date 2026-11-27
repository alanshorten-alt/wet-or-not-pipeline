"""One 5-minute run: ingest the newest radar pair, update history, nowcast, write files.

    python pipeline/run_radar.py --out out --base-url https://<r2 public url>/live

Stateless: the previous manifest and frames are read back from the published
base URL, so a fresh runner needs no cache. Writes only what changed (the new
radar frame, nowcast.json, manifest.json) into --out; publish.mjs uploads it.
Exits 0 with no files written when there is no new scan yet, and prints
`changed=true|false` for the workflow.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nowcast import build_nowcast  # noqa: E402
from stations import build_ground  # noqa: E402
from synoptic import fetch_observations  # noqa: E402
from radar import DUBLIN, SHANNON, build_frame, load_clutter_mask, make_grid, read_volume  # noqa: E402

BASE = "https://opendata2.met.ie/radar"
MASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clutter_mask.json")
HISTORY = 12
RADAR_STALE_MIN = 15  # keep in step with src/lib/data.ts
NEVER = "1970-01-01T00:00:00Z"


def get(url: str, timeout: int = 60) -> bytes:
    # Published files are stored gzipped (publish.mjs), and urllib does not undo Content-Encoding itself.
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return body


def stamp(name: str) -> datetime:
    return datetime.strptime(re.search(r"_(\d{14})\.", name).group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def pairs(listing: list[str], tol_s: int = 150) -> list[tuple[datetime, str, str]]:
    """(time, dublin file, shannon file), newest first, where both radars scanned within tol_s."""
    dub = sorted(f for f in listing if f.startswith("T_PAGZ41"))
    sha = sorted(f for f in listing if f.startswith("T_PAGZ40"))
    out = []
    for d in reversed(dub):
        s = min(sha, key=lambda f: abs((stamp(f) - stamp(d)).total_seconds()), default=None)
        if s and abs((stamp(s) - stamp(d)).total_seconds()) <= tol_s:
            out.append((stamp(d), d, s))
    return out


def frame_name(source_time: str) -> str:
    return "radar/" + datetime.fromisoformat(source_time.replace("Z", "+00:00")).strftime("%Y%m%dT%H%MZ") + ".json"


def build(pair, grid, mask, tmp) -> dict:
    _, d, s = pair
    paths = []
    for f in (d, s):
        p = os.path.join(tmp, f)
        with open(p, "wb") as fh:
            fh.write(get(f"{BASE}/latest/{f}"))
        paths.append(p)
    vols = [(read_volume(paths[0]), DUBLIN), (read_volume(paths[1]), SHANNON)]
    return build_frame(vols, grid, clutter_mask=mask)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fetch_json(url: str):
    try:
        return json.loads(get(url, 30))
    except Exception:  # missing or unreachable history is normal on the first run
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-url", required=True, help="public URL of the published folder, no trailing slash")
    ap.add_argument("--backfill", type=int, default=HISTORY - 1, help="max older frames to rebuild when history is missing")
    a = ap.parse_args()
    base_url = a.base_url.rstrip("/")

    listing = sorted(set(re.findall(r"T_PAGZ4[01]_C_EIDB_\d{14}\.h5", get(f"{BASE}/latest/").decode("latin-1"))))
    avail = pairs(listing)
    if not avail:
        sys.exit("no Dublin+Shannon pair in latest/")
    grid = make_grid()
    mask = load_clutter_mask(MASK, grid)
    prev = fetch_json(f"{base_url}/manifest.json") or {}
    have = list(prev.get("radarFiles", []))

    newest = avail[0]
    os.makedirs(os.path.join(a.out, "radar"), exist_ok=True)
    frames: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        new = build(newest, grid, mask, tmp)
        name = frame_name(new["sourceTime"])
        if name in have:
            print("no new scan since the last run")
            print("changed=false")
            return
        frames[name] = new
        # History: published frames first, then rebuild missing recent slots from latest/.
        for n in have[-(HISTORY - 1):]:
            fr = fetch_json(f"{base_url}/{n}")
            if fr and fr.get("grid") == new["grid"]:
                frames[n] = fr

        def covered(t: datetime) -> bool:
            return any(abs((datetime.fromisoformat(f["sourceTime"].replace("Z", "+00:00")) - t).total_seconds()) <= 150
                       for f in frames.values())

        rebuilt = 0
        for p in avail[1:]:
            if rebuilt >= a.backfill or len(frames) >= HISTORY:
                break
            if newest[0] - p[0] > timedelta(minutes=5 * HISTORY) or covered(p[0]):
                continue
            fr = build(p, grid, mask, tmp)
            fname = frame_name(fr["sourceTime"])
            frames[fname] = fr
            with open(os.path.join(a.out, fname), "w") as fh:
                json.dump(fr, fh, separators=(",", ":"))
            rebuilt += 1

    with open(os.path.join(a.out, name), "w") as fh:
        json.dump(new, fh, separators=(",", ":"))
    ordered = sorted(frames)[-HISTORY:]
    series = [(datetime.fromisoformat(frames[n]["sourceTime"].replace("Z", "+00:00")),
               np.array(frames[n]["rainMmh"], dtype=np.float32).reshape(grid.rows, grid.cols)) for n in ordered]
    now = datetime.now(timezone.utc)
    nowcast = build_nowcast(series, new["grid"], series[-1][0], now=now)
    with open(os.path.join(a.out, "nowcast.json"), "w") as fh:
        json.dump(nowcast, fh, separators=(",", ":"))

    # Stations engine: gauge file is hourly and lags 30-90 minutes. If it fails, keep the last ground
    # file (its readings age on screen) instead of failing the radar run.
    ground = fetch_json(f"{base_url}/ground.json")
    prev_synoptic = (ground or {}).get("synoptic", [])
    try:
        ground = build_ground(now_utc(), ground)
    except Exception as e:  # noqa: BLE001
        print(f"stations engine failed, keeping the previous ground file: {e}")
        if not ground:
            ground = {"generatedAt": nowcast["generatedAt"], "sourceTime": NEVER, "regions": []}
    # Synoptic engine: hourly observations CSV. On failure keep the previous list (readings age on screen).
    try:
        ground["synoptic"] = fetch_observations(now_utc())
    except Exception as e:  # noqa: BLE001
        print(f"synoptic engine failed, keeping the previous list: {e}")
        ground["synoptic"] = prev_synoptic
    with open(os.path.join(a.out, "ground.json"), "w") as fh:
        json.dump(ground, fh, separators=(",", ":"))

    age_min = (now - series[-1][0]).total_seconds() / 60.0
    ground_age = (now - datetime.fromisoformat(ground["sourceTime"].replace("Z", "+00:00"))).total_seconds() / 60.0
    manifest = {
        "generatedAt": nowcast["generatedAt"],
        "radarFiles": ordered,
        "nowcastFile": "nowcast.json",
        "groundFile": "ground.json",
        "freshness": {
            "ingest": {"sourceTime": new["sourceTime"], "stale": age_min > RADAR_STALE_MIN},
            "nowcast": {"sourceTime": nowcast["sourceTime"], "stale": age_min > RADAR_STALE_MIN},
            "ground": {"sourceTime": ground["sourceTime"], "stale": ground_age > 60},
        },
    }
    with open(os.path.join(a.out, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, separators=(",", ":"))
    print(f"frame {name}, {len(ordered)} in history, motion {nowcast['motionKmh']} km/h, confidence {nowcast['confidence']}")
    print("changed=true")


if __name__ == "__main__":
    main()
