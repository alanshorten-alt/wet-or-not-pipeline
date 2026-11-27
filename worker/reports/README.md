# "Got it wrong" reports (Cloudflare Worker)

The app's "Got it wrong" button posts one JSON snapshot here (format: `FeedbackReportSchema` in `contracts/contracts.ts`). The Worker checks it and files it in a **private** R2 bucket as `reports/<day>/<hash>.json`. No account, name or device id is stored, and the pin is rounded to about 1 km.

## Limits it enforces
- Only the live site and its Vercel previews can post (CORS and an `Origin` check).
- 4 KB maximum, strict shape (unknown keys are refused), report time within the last 24 hours.
- One file per place (about 1 km), verdict and hour, so repeated taps overwrite nothing and add nothing.
- It does **not** rate-limit by IP: that needs a custom domain or Cloudflare's rate-limiting binding. The worst case is bounded by the file-per-place rule and R2's low cost, but set a spend alert.

## Deploy (Alan, once)
1. Cloudflare > R2 > the bucket `wet-or-not-reports` (private: do not turn on public access or a custom domain).
2. Workers & Pages > Create > Worker, name `wet-or-not-reports`, paste `index.js` and add `validate.js` (or deploy the folder with `wrangler deploy`).
3. Worker > Settings > Bindings: add an R2 bucket binding named `REPORTS` pointing at `wet-or-not-reports`.
4. Worker > Settings > Variables: `ALLOWED_ORIGINS`, `PREVIEW_PREFIX`, `PREVIEW_SUFFIX` (values in `wrangler.toml`).
5. Copy the Worker's URL (`https://wet-or-not-reports.<you>.workers.dev`) into the app's `NEXT_PUBLIC_REPORTS_URL`.

No secrets are involved. Reports are read from the R2 dashboard or by a later pipeline job.

## Tests
`node --test worker/reports/validate.test.mjs` checks the hand-written validator against the zod contract on the same examples.
