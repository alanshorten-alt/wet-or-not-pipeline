import io
import sys
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "pipeline")
import stations as st  # noqa: E402

T0 = datetime(2026, 11, 27, 8, 35, tzinfo=timezone.utc)


def cmp_text(rows):
    head = '"TOA5"\n"TIMESTAMP"\n"TS"\n"x"\n'
    body = "\n".join(f'"{t:%Y-%m-%d %H:%M:%S}",1,285,0,0,{mm},0,0,0,0,0,0' for t, mm in rows)
    return head + body


def minutes(n, mm=0.0, end=T0):
    return {end - timedelta(minutes=i): mm for i in range(n)}


def test_parse_cmp_reads_minute_and_step():
    rows = [(T0, 0.0), (T0 + timedelta(minutes=1), 0.2)]
    assert [(t, mm) for t, mm in st.parse_cmp(cmp_text(rows))] == rows


def test_pick_members_adds_previous_day_close():
    names = ["pluvA2026112607.zip", "pluvA2026112623.zip", "pluvA2026112708.zip", "readme.txt"]
    assert st.pick_members(names) == ["pluvA2026112623.zip", "pluvA2026112708.zip"]
    assert st.pick_members(["pluvA2026112708.zip"]) == ["pluvA2026112708.zip"]


def test_station_rain_sums_rain_and_ignores_glitches():
    m = minutes(24 * 60)
    m[T0] = 0.5
    m[T0 - timedelta(minutes=500)] = 0.25
    m[T0 - timedelta(minutes=600)] = 50.0  # glitch
    s = st.station_rain(m, T0)
    assert s["rain24h"] == 0.75
    assert s["recent10"] == 0.5
    assert s["last"] == T0


def test_station_rain_drops_short_or_emptied_gauges():
    assert st.station_rain(minutes(600), T0) is None
    m = minutes(24 * 60)
    m[T0 - timedelta(minutes=30)] = -80.0  # bucket emptied
    assert st.station_rain(m, T0) is None


def test_wetness_bands():
    assert [st.wetness(x) for x in (0, 0.5, 5, 12)] == ["dry", "damp", "soft", "waterlogged"]


def test_regions_use_neighbours_and_hide_stale_rain_now():
    sites = {
        1: {"name": "A", "lat": 53.0, "lng": -6.0},
        2: {"name": "B", "lat": 53.05, "lng": -6.0},
        3: {"name": "Far", "lat": 51.0, "lng": -9.0},
    }
    stats = {
        1: {"rain24h": 2.0, "last": T0, "recent10": 0.0},
        2: {"rain24h": 4.0, "last": T0, "recent10": 0.3},
        3: {"rain24h": 20.0, "last": T0, "recent10": 0.0},
    }
    r = {x["stationId"]: x for x in st.build_regions(stats, sites, T0 + timedelta(minutes=20))}
    assert r["1"]["stationCount"] == 2 and r["1"]["rain24hMm"] == {"median": 3.0, "low": 2.0, "high": 4.0}
    assert r["1"]["rainNow"] == "no" and r["2"]["rainNow"] == "yes"
    assert r["3"]["stationCount"] == 1 and r["3"]["wetness"] == "waterlogged"
    late = {x["stationId"]: x for x in st.build_regions(stats, sites, T0 + timedelta(minutes=45))}
    assert late["2"]["rainNow"] == "unsure"


def test_names_and_carry_forward():
    assert st.tidy_name("JFK PARK") == "JFK Park"
    assert st.tidy_name("ARDEE (BOHERNAMOE)") == "Ardee (Bohernamoe)"
    prev = {"generatedAt": "2026-11-27T09:00:00Z", "sourceTime": "2026-11-27T08:35:00Z", "regions": [{"readingAgeMin": 25.0}]}
    out = st.carry_forward(prev, datetime(2026, 11, 27, 9, 10, tzinfo=timezone.utc))
    assert out["regions"][0]["readingAgeMin"] == 35.0


def test_read_zip_readings_groups_by_station():
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("202611270003_01_Pluv_A_0285_M.CMP", cmp_text([(T0, 0.1)]))
        z.writestr("202611270033_01_Pluv_A_0485_M.CMP", cmp_text([(T0, 0.2)]))
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("pluvA2026112708.zip", inner.getvalue())
    got = st.read_zip_readings(zipfile.ZipFile(outer), "pluvA2026112708.zip")
    assert got == {285: {T0: 0.1}, 485: {T0: 0.2}}
