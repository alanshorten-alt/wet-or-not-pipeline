# Wet or Not: pipeline

Public repo. Never commit secrets, tokens or personal details.

- `contracts/contracts.ts` is the source of truth for file formats; the private app repo keeps a synced copy. Change a schema and its tests together, and say in the PR that the app copy needs syncing.
- Heavy work (HDF5 decoding, nowcast) belongs here in `pipeline/`, never in a Worker or on Vercel.
- `sourceTime` is the radar scan time from the file, never the run time.
- Checks before a PR: `python -m pytest -q pipeline/tests` and `node --check pipeline/publish.mjs`.
- Avoid stacked PRs.
