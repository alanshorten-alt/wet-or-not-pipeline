import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from synoptic import parse_observations  # noqa: E402

CSV = '''Station,"Temperature (ºC)",Weather,"Wind Speed (Kts)","Wind Gust (Kts)","Wind Direction","Humidity (%)","Rainfall (mm)","Pressure (hPa)"
Casement,4,Fair,05,,SW,99,0.01,1014
Dublin,5,Mist,04,,W,99,0.0,1013
"Malin Head",6,"Recent Rain",10,28,SW,79,0.01,1011
"Markree Castle",-1,-,-,,-,98,0.0,1013
"Mt Dillon",2,-,03,,S,99,0.0,1013
Nowhere,1,Fair,01,,N,90,0.0,1000
'''
NOW = datetime(2026, 11, 27, 9, 51, tzinfo=timezone.utc)


def test_parse():
    rows = {r["name"]: r for r in parse_observations(CSV, NOW)}
    assert set(rows) == {"Casement", "Dublin Airport", "Malin Head", "Markree Castle", "Mount Dillon"}
    c = rows["Casement"]
    assert c["windKmh"] == 9 and c["gustKmh"] is None and c["windFrom"] == "SW"
    assert c["observedAt"] == "2026-11-27T09:00:00Z" and c["readingAgeMin"] == 51
    assert rows["Malin Head"]["gustKmh"] == 52  # 28 kt
    m = rows["Markree Castle"]
    assert m["windKmh"] is None and m["weather"] is None and m["tempC"] == -1


def test_unknown_station_skipped():
    assert all(r["name"] != "Nowhere" for r in parse_observations(CSV, NOW))
