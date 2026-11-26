# Wet or Not: radar pipeline

Public repo for the 5-minute data feed behind Wet or Not (hyperlocal rain forecasts for Ireland). The app itself lives in a private repo and only reads the JSON files this pipeline publishes to Cloudflare R2.

- `pipeline/` ingests Met Éireann radar (Dublin and Shannon), applies a clutter mask, runs a nowcast, writes `radar/*.json`, `nowcast.json`, `ground.json`, `manifest.json`. See `pipeline/README.md` and `pipeline/VALIDATION.md`.
- `worker/` is a Cloudflare Worker cron that triggers `.github/workflows/radar.yml` every 5 minutes.
- `contracts/contracts.ts` defines the file formats (zod). The app keeps a synced copy.
- R2 credentials live only in this repo's Actions secrets: `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Never commit them.

Radar data: Met Éireann, www.met.ie, licensed CC BY 4.0. Contains modified Met Éireann data. Met Éireann does not accept liability for the content or for any use made of it.

No licence has been chosen for the code yet, so by default all rights are reserved.
