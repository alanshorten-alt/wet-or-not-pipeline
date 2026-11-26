# Radar trigger (Cloudflare Worker)

Calls GitHub's `workflow_dispatch` for `radar.yml` every 5 minutes. No radar work happens here.

## Deploy (Alan, once)

1. GitHub: Settings > Developer settings > Fine-grained tokens > new token, repository access: only `wet-or-not-pipeline`, permission `Actions: Read and write`. Copy it once.
2. Cloudflare: Workers & Pages > Create > Worker, name `wet-or-not-cron`, paste `index.js`, deploy.
3. Worker > Settings > Variables and secrets: add `REPO`, `WORKFLOW`, `REF` (values in `wrangler.toml`) and a **secret** `GITHUB_TOKEN` with the token. Do not paste the token in chat or commit it.
4. Worker > Settings > Triggers > Cron: `*/5 * * * *`.
5. Check: the Actions tab should show a "Radar run" every 5 minutes; Worker > Logs shows `dispatch failed` lines if GitHub refuses.

## Things to know

- **Actions minutes.** A run every 5 minutes is about 8,600 runs a month. Public repositories get unlimited standard-runner minutes; private ones get 2,000 a month on the free plan, which this exceeds. Make the repo public (it holds no secrets) or reduce the cadence before turning the cron on.
- **Edge cache (3 minutes).** Cache Rules only apply to a custom domain on a Cloudflare zone, not to the `r2.dev` dev URL. Once the bucket has a custom domain: Caching > Cache Rules > new rule, "URI Path ends with `.json`", Eligible for cache, Edge TTL 3 minutes (ignore origin). Until then the objects carry `Cache-Control: max-age=180` and the browser honours it. `manifest.json` stays at 30 seconds on purpose so freshness shows quickly.
- The workflow's hourly `schedule` is a backstop in case the Worker stops.
