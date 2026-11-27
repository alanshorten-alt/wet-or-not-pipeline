# Pipeline

Scheduled jobs that fetch, decode and publish the data files. They run on GitHub Actions (no CPU limit), triggered every 5 minutes by a Cloudflare Worker cron via `workflow_dispatch`, because Actions' own cron starts late and is disabled after 60 days without repo activity. They never run on Vercel.

Jobs write files that match `contracts/contracts.ts`; nothing else is shared between engines.

| Job | Status |
| --- | --- |
| `probe-met-eireann.mjs` | Checks Met Éireann CAMP and EDR field names and delivery lag before the real ingest is built |
| `publish.mjs` | Validates a folder of data files against the contracts, then uploads to R2 (manifest last). Use `--dry-run` to check without uploading |
| `radar.py`, `ingest_radar.py` | Decodes Dublin and Shannon polar volumes (ODIM HDF5) into a `RadarFrame` JSON on a 2.5 km lat/lng grid (about 191 x 151 cells). Prototype, not scheduled yet |
| `compare_composite.py` | Manual check of our decoded rain against the hourly Dublin-Shannon composite |
| `stations.py` | Stations engine: CAMP rain gauges (`pluvA.zip`, fetched with HTTP Range, about 3 MB a run) to `ground.json`: station name, 24 h rain (median, low, high of gauges within 10 km), wetness and reading age. Called from `run_radar.py` |

Sources for v1 are Met Éireann only (CC BY 4.0). TII, Dublin City, Fingal, WOW-IE and Netatmo or private networks are excluded.

## Publishing to R2

Set these as GitHub repository secrets (Settings, Secrets and variables, Actions): `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Create the key under R2, Manage R2 API Tokens, with Object Read & Write limited to the one bucket. Never commit or paste these.

The manual workflow `Publish sample data to R2` uploads `public/sample` under the `sample/` prefix, so `NEXT_PUBLIC_DATA_URL` becomes `<public r2 url>/sample`.

## Radar ingest prototype

```
pip install -r pipeline/requirements.txt
python pipeline/ingest_radar.py --dublin T_PAGZ41_..._.h5 --shannon T_PAGZ40_..._.h5 --out radar.json
python pipeline/compare_composite.py 2026-11-26T15:00:00
python -m pytest pipeline/tests
```

Choices (see `radar.py`):
- `sourceTime` is the scan time inside the file, not the run time. Files appear about 10 to 16 minutes after the scan.
- Marshall-Palmer Z = 200 R^1.6, dBZ capped at 53 (hail) and cut below 5 dBZ.
- Dublin skips tilts below 1.0 degrees within 40 km to avoid hill clutter. Not yet checked against real clutter maps.
- Lone spikes (more than 4 times the 3x3 median plus 2 mm/h) are replaced by the local median.
- Radar merge: nearer radar inside 120 km, inverse-distance-squared blend beyond, never max.
- Output is an equirectangular grid, so the phone needs no projection library. The composite's own projection is only used in the comparison tool.

First comparison (hour stamped 15:00 on 26 Nov 2026): the composite stamp is the hour **ending** at that time (correlation 0.44, against 0.16 for the hour starting). Our total was 0.86 times the composite's, so about 14% low. One hour is not enough to justify a correction factor; collect more hours before applying one.

## Live run (every 5 minutes)

`run_radar.py` ingests the newest Dublin+Shannon pair (clutter mask applied), reads the previous
manifest and frames back from the public R2 URL, runs `nowcast.py` (optical flow on the last three
frames, 24 five-minute steps) and writes `radar/<scan>.json`, `nowcast.json`, `ground.json` and
`manifest.json` into `--out`. `publish.mjs --prune` uploads them and deletes frames the manifest no
longer lists. `.github/workflows/radar.yml` runs it; `worker/` triggers it on time.

`ground.json` comes from `stations.py`. The CAMP file is rewritten about hourly and runs 30-90 minutes
behind, so the engine never claims rain "now" from it: `rainNow` is `unsure` once a reading is older than
30 minutes, and every region carries `readingAgeMin`. The previous file is reused until the source changes,
with ages advanced. If the engine fails, the last `ground.json` is kept and the radar run still publishes.
Gauges with under 90% coverage of the last 24 hours or with large negative steps (bucket emptying) are dropped.
