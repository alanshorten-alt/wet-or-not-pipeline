import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pitches import cell_of, clean_ring, export  # noqa: E402

# pitches_sample.osm, Dublin: one GAA pitch, one soccer pitch in the next cell over, a closed way that is not a pitch,
# an open way tagged as a pitch, and a speck.
SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pitches_sample.osm")


def test_cell_of_is_stable_at_edges():
    assert cell_of(-11.0, 51.0) == (0, 0)
    assert cell_of(-6.5, 53.3) == (18, 9)


def test_clean_ring_rounds_dedupes_and_rejects_open_or_tiny():
    ring = [(-6.5000001, 53.3), (-6.5000001, 53.3), (-6.498, 53.3), (-6.498, 53.301), (-6.5000001, 53.3)]
    got = clean_ring(ring)
    assert got is not None and got[0] == got[-1] == [-6.5, 53.3] and len(got) == 4
    assert clean_ring([(-6.5, 53.3), (-6.498, 53.3), (-6.498, 53.301)]) is None  # not closed
    assert clean_ring([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.0, 0.0)]) is None  # outside Ireland


def test_export_keeps_only_real_pitches_and_matches_the_contract_shape(tmp_path):
    out = tmp_path / "pitches"
    counts = export(SAMPLE, str(out))
    assert counts == {"18_9": 1, "19_10": 1, "10_4": 1}
    tile = json.loads((out / "18_9.json").read_text())
    assert tile["schemaVersion"] == 1 and (tile["ix"], tile["iy"]) == (18, 9)
    p = tile["pitches"][0]
    assert p["kind"] == "pitch" and p["sport"] == "gaelic_games" and p["ring"][0] == p["ring"][-1]
    index = json.loads((out / "index.json").read_text())
    assert sorted(index["cells"]) == ["10_4", "18_9", "19_10"]
    track = json.loads((out / "10_4.json").read_text())["pitches"][0]
    assert track["kind"] == "track" and track["sport"] == "running" and track["ring"][0] == track["ring"][-1]


def test_export_replaces_old_tiles(tmp_path):
    out = tmp_path / "pitches"
    out.mkdir()
    (out / "0_0.json").write_text("{}")
    export(SAMPLE, str(out))
    assert not (out / "0_0.json").exists()
