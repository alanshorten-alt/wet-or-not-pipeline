// Cloudflare Worker: receives "Got it wrong" reports from the app and files them in a private R2 bucket.
// Does no heavy work: validate, name the file, store it.
//
// Bindings (wrangler.toml): REPORTS (R2 bucket, private). Vars: ALLOWED_ORIGINS (comma list),
// PREVIEW_PREFIX / PREVIEW_SUFFIX (Vercel preview URLs of the app).
//
// One file per place (about 1 km), verdict and hour, so repeated taps cannot pile up files.
// POST /event files an anonymous usage count (share or open) as events/<day>/<type>/<random>.json.
import { validateEvent, validateReport } from "./validate.js";

const MAX_BYTES = 4096;
const MAX_EVENT_BYTES = 1024;

export function originAllowed(origin, env) {
  if (!origin) return false;
  const list = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (list.includes(origin)) return true;
  const pre = env.PREVIEW_PREFIX;
  const suf = env.PREVIEW_SUFFIX;
  return Boolean(pre && suf && origin.startsWith(pre) && origin.endsWith(suf) && /^[a-z0-9.-]+$/.test(origin.slice("https://".length)));
}

const cors = (origin) => ({
  "Access-Control-Allow-Origin": origin,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
  Vary: "Origin",
});

const reply = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

async function sha256Hex(text) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Where a report is filed: reports/<day>/<hash>.json. Exported for the tests. */
export async function reportKey(r) {
  const hour = r.generatedAt.slice(0, 13);
  const hash = (await sha256Hex(`${r.place.lat.toFixed(2)}|${r.place.lng.toFixed(2)}|${r.verdict.state}|${hour}`)).slice(0, 16);
  return `reports/${r.generatedAt.slice(0, 10)}/${hash}.json`;
}

async function handleEvent(request, env, headers) {
  const declared = Number(request.headers.get("Content-Length") || 0);
  if (declared > MAX_EVENT_BYTES) return reply(413, { ok: false, error: "too large" }, headers);
  const text = await request.text();
  if (text.length > MAX_EVENT_BYTES) return reply(413, { ok: false, error: "too large" }, headers);
  let event;
  try {
    event = JSON.parse(text);
  } catch {
    return reply(400, { ok: false, error: "json" }, headers);
  }
  const check = validateEvent(event);
  if (!check.ok) return reply(400, { ok: false, error: check.error }, headers);
  const age = Date.now() - Date.parse(event.generatedAt);
  if (age > 24 * 3600_000 || age < -5 * 60_000) return reply(400, { ok: false, error: "time" }, headers);
  // Round again here: never trust the phone to have done it. The key is random, so nothing links two events.
  event.place = { lat: Math.round(event.place.lat * 100) / 100, lng: Math.round(event.place.lng * 100) / 100, name: event.place.name };
  const key = eventKey(event, crypto.randomUUID());
  await env.REPORTS.put(key, JSON.stringify(event), { httpMetadata: { contentType: "application/json" } });
  return reply(200, { ok: true }, headers);
}

/** Where an event is filed: events/<day>/<type>/<id>.json. Exported for the tests. */
export function eventKey(e, id) {
  return `events/${e.generatedAt.slice(0, 10)}/${e.type}/${id}.json`;
}

const worker = {
  async fetch(request, env) {
    const origin = request.headers.get("Origin");
    if (!originAllowed(origin, env)) return reply(403, { ok: false, error: "origin" });
    const headers = cors(origin);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });
    if (request.method !== "POST") return reply(405, { ok: false, error: "method" }, headers);

    const path = new URL(request.url).pathname.replace(/\/+$/, "");
    if (path === "/event") return handleEvent(request, env, headers);

    const declared = Number(request.headers.get("Content-Length") || 0);
    if (declared > MAX_BYTES) return reply(413, { ok: false, error: "too large" }, headers);
    const text = await request.text();
    if (text.length > MAX_BYTES) return reply(413, { ok: false, error: "too large" }, headers);

    let report;
    try {
      report = JSON.parse(text);
    } catch {
      return reply(400, { ok: false, error: "json" }, headers);
    }
    const check = validateReport(report);
    if (!check.ok) return reply(400, { ok: false, error: check.error }, headers);
    // Reports must be about now: not from the future, not more than a day old.
    const age = Date.now() - Date.parse(report.generatedAt);
    if (age > 24 * 3600_000 || age < -5 * 60_000) return reply(400, { ok: false, error: "time" }, headers);

    // Round the pin again here: never trust the phone to have done it.
    report.place = { lat: Math.round(report.place.lat * 100) / 100, lng: Math.round(report.place.lng * 100) / 100 };
    const key = await reportKey(report);
    if (await env.REPORTS.head(key)) return reply(200, { ok: true, duplicate: true }, headers);
    await env.REPORTS.put(key, JSON.stringify(report), { httpMetadata: { contentType: "application/json" } });
    return reply(200, { ok: true }, headers);
  },
};

export default worker;
