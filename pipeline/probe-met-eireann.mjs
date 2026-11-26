// One-off probe: polls Met Éireann endpoints and logs HTTP status, size,
// Last-Modified and age, so we learn field names and delivery lag before
// building the real ingest. No dependencies; run with Node 22.
//
//   node pipeline/probe-met-eireann.mjs            # one poll
//   node pipeline/probe-met-eireann.mjs --every 5  # poll every 5 min for 24 h
//
// Endpoints are env-configurable because the exact URLs must be confirmed
// against Met Éireann's open-data pages before relying on them.
import { appendFileSync } from "node:fs";

const targets = {
  camp: process.env.CAMP_URL ?? "https://opendata2.met.ie/opendata2/campstn/pluvA.zip",
  edr: process.env.EDR_URL ?? "",
};

const everyIdx = process.argv.indexOf("--every");
const everyMin = everyIdx > -1 ? Number(process.argv[everyIdx + 1]) : 0;
const hours = 24;
const logFile = process.env.PROBE_LOG ?? "probe-log.jsonl";

async function pollOnce() {
  for (const [name, url] of Object.entries(targets)) {
    if (!url) {
      console.warn(`${name}: set ${name.toUpperCase()}_URL to probe it`);
      continue;
    }
    const started = Date.now();
    const row = { at: new Date().toISOString(), name, url };
    try {
      const res = await fetch(url, { headers: { "user-agent": "wet-or-not-probe" } });
      const body = await res.arrayBuffer();
      const lastModified = res.headers.get("last-modified");
      Object.assign(row, {
        status: res.status,
        bytes: body.byteLength,
        ms: Date.now() - started,
        lastModified,
        ageMin: lastModified ? Math.round((Date.now() - new Date(lastModified).getTime()) / 60000) : null,
        contentType: res.headers.get("content-type"),
      });
    } catch (e) {
      row.error = String(e);
    }
    console.log(JSON.stringify(row));
    appendFileSync(logFile, JSON.stringify(row) + "\n");
  }
}

await pollOnce();
if (everyMin > 0) {
  const end = Date.now() + hours * 3_600_000;
  while (Date.now() < end) {
    await new Promise((r) => setTimeout(r, everyMin * 60_000));
    await pollOnce();
  }
}
