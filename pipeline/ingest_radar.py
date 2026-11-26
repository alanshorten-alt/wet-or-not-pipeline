"""Turn the newest Dublin and Shannon polar volumes into a RadarFrame JSON file.

    python pipeline/ingest_radar.py --dublin D.h5 --shannon S.h5 --out radar.json

Run `node pipeline/publish.mjs <dir>` afterwards to validate against the
contract and upload. Downloading is deliberately separate so this stays testable.
"""
import argparse
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from radar import DUBLIN, SHANNON, build_frame, load_clutter_mask, make_grid, read_volume  # noqa: E402

MASK = __file__.rsplit("/", 1)[0] + "/clutter_mask.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dublin", required=True)
    p.add_argument("--shannon")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    vols = [(read_volume(a.dublin), DUBLIN)]
    if a.shannon:
        vols.append((read_volume(a.shannon), SHANNON))
    grid = make_grid()
    frame = build_frame(vols, grid, clutter_mask=load_clutter_mask(MASK, grid))
    with open(a.out, "w") as f:
        json.dump(frame, f, separators=(",", ":"))
    wet = sum(1 for x in frame["rainMmh"] if x >= 0.5)
    print(f"wrote {a.out}: sourceTime {frame['sourceTime']}, {len(frame['rainMmh'])} cells, {wet} wet (>=0.5 mm/h)")


if __name__ == "__main__":
    main()
