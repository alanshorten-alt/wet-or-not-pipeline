// Run with: node --test worker/reports
import assert from "node:assert/strict";
import test from "node:test";
import { FeedbackReportSchema } from "../../contracts/contracts.ts";
import worker, { originAllowed, reportKey } from "./index.js";
import { validateReport } from "./validate.js";

const good = () => ({
  schemaVersion: 1,
  generatedAt: new Date().toISOString(),
  sourceTime: new Date(Date.now() - 8 * 60_000).toISOString(),
  place: { lat: 53.29, lng: -6.45 },
  verdict: { state: "showers", text: "Showers nearby" },
  radar: { ageMin: 8, confidence: 0.4, motionKmh: { east: 20, north: 5 }, rainNowMmh: 0.3 },
  forecastMmh: [0.3, 0.2, 0, 0, 0],
  station: { name: "Casement", distanceKm: 4, rainHourMm: 0, windKmh: 13, readingAgeMin: 30 },
  rain24hMm: 1.2,
});

const mutations = {
  good: (r) => r,
  "no station, nulls": (r) => ({ ...r, station: null, rain24hMm: null, radar: { ...r.radar, rainNowMmh: null } }),
  "empty forecast": (r) => ({ ...r, forecastMmh: [] }),
  "window 2h with reportedAt": (r) => ({ ...r, window: "2h", reportedAt: new Date().toISOString() }),
  "window now": (r) => ({ ...r, window: "now" }),
  "bad window": (r) => ({ ...r, window: "today" }),
  "bad reportedAt": (r) => ({ ...r, reportedAt: "later" }),
  "extra top-level key": (r) => ({ ...r, userId: "x" }),
  "extra place key": (r) => ({ ...r, place: { ...r.place, name: "Home" } }),
  "missing key": ({ rain24hMm, ...r }) => r,
  "wrong version": (r) => ({ ...r, schemaVersion: 2 }),
  "bad time": (r) => ({ ...r, generatedAt: "yesterday" }),
  "offset time": (r) => ({ ...r, sourceTime: "2026-11-27T12:00:00+01:00" }),
  "outside Ireland": (r) => ({ ...r, place: { lat: 48.8, lng: 2.3 } }),
  "bad state": (r) => ({ ...r, verdict: { ...r.verdict, state: "storm" } }),
  "empty text": (r) => ({ ...r, verdict: { ...r.verdict, text: "" } }),
  "long text": (r) => ({ ...r, verdict: { ...r.verdict, text: "x".repeat(121) } }),
  "confidence > 1": (r) => ({ ...r, radar: { ...r.radar, confidence: 1.5 } }),
  "negative rain": (r) => ({ ...r, forecastMmh: [-1] }),
  "25 steps": (r) => ({ ...r, forecastMmh: Array(25).fill(0) }),
  "NaN in forecast": (r) => ({ ...r, forecastMmh: [Number.NaN] }),
  "string number": (r) => ({ ...r, rain24hMm: "1.2" }),
  "bad station": (r) => ({ ...r, station: { ...r.station, distanceKm: -1 } }),
  "array instead of object": () => [],
};

for (const [name, mutate] of Object.entries(mutations)) {
  test(`validator agrees with the contract: ${name}`, () => {
    const r = mutate(good());
    assert.equal(validateReport(r).ok, FeedbackReportSchema.safeParse(r).success);
  });
}

test("the good example passes both", () => {
  assert.equal(validateReport(good()).ok, true);
  assert.equal(FeedbackReportSchema.safeParse(good()).success, true);
});

class FakeBucket {
  files = new Map();
  async head(k) { return this.files.has(k) ? {} : null; }
  async put(k, v) { this.files.set(k, v); }
}
const env = () => ({ REPORTS: new FakeBucket(), ALLOWED_ORIGINS: "https://wet-or-not.vercel.app", PREVIEW_PREFIX: "https://wet-or-not-git-", PREVIEW_SUFFIX: "-simply-ev.vercel.app" });
const post = (body, origin = "https://wet-or-not.vercel.app") =>
  new Request("https://reports.example/", { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: typeof body === "string" ? body : JSON.stringify(body) });

test("origins: the live site and its previews only", () => {
  const e = env();
  assert.equal(originAllowed("https://wet-or-not.vercel.app", e), true);
  assert.equal(originAllowed("https://wet-or-not-git-saved-places-simply-ev.vercel.app", e), true);
  assert.equal(originAllowed("https://evil.example", e), false);
  assert.equal(originAllowed("https://wet-or-not-git-x-simply-ev.vercel.app.evil.example", e), false);
  assert.equal(originAllowed(null, e), false);
});

test("stores a good report once and rounds the pin", async () => {
  const e = env();
  const r = { ...good(), place: { lat: 53.2949, lng: -6.4549 } };
  const res = await worker.fetch(post(r), e);
  assert.equal(res.status, 200);
  assert.equal(e.REPORTS.files.size, 1);
  const [stored] = [...e.REPORTS.files.values()];
  assert.deepEqual(JSON.parse(stored).place, { lat: 53.29, lng: -6.45 });
  const again = await worker.fetch(post(r), e);
  assert.equal((await again.json()).duplicate, true);
  assert.equal(e.REPORTS.files.size, 1);
});

test("rejects bad origin, bad json, bad report, stale time, big body", async () => {
  const e = env();
  assert.equal((await worker.fetch(post(good(), "https://evil.example"), e)).status, 403);
  assert.equal((await worker.fetch(post("{nope"), e)).status, 400);
  assert.equal((await worker.fetch(post({ ...good(), userId: 1 }), e)).status, 400);
  assert.equal((await worker.fetch(post({ ...good(), generatedAt: new Date(Date.now() - 2 * 86400_000).toISOString() }), e)).status, 400);
  assert.equal((await worker.fetch(post("x".repeat(5000)), e)).status, 413);
  assert.equal(e.REPORTS.files.size, 0);
});

test("preflight is answered for allowed origins", async () => {
  const res = await worker.fetch(new Request("https://reports.example/", { method: "OPTIONS", headers: { Origin: "https://wet-or-not.vercel.app" } }), env());
  assert.equal(res.status, 204);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), "https://wet-or-not.vercel.app");
});

test("file names group by place, verdict and hour", async () => {
  const a = await reportKey(good());
  const b = await reportKey({ ...good(), sourceTime: "2026-01-01T00:00:00Z" });
  const c = await reportKey({ ...good(), verdict: { state: "wet", text: "Wet" } });
  assert.equal(a, b);
  assert.notEqual(a, c);
  assert.match(a, /^reports\/\d{4}-\d{2}-\d{2}\/[0-9a-f]{16}\.json$/);
});

test("a 'past 2 hours' report is kept apart from a 'now' one in the same hour", async () => {
  const a = await reportKey(good());
  const b = await reportKey({ ...good(), window: "2h" });
  assert.notEqual(a, b);
});

// ---- usage events (POST /event) ----
import { UsageEventSchema } from "../../contracts/contracts.ts";
import { validateEvent } from "./validate.js";

const goodEvent = () => ({
  schemaVersion: 1,
  generatedAt: new Date().toISOString(),
  type: "share",
  place: { lat: 53.29, lng: -6.45, name: "Clondalkin" },
});

const eventMutations = {
  good: (e) => e,
  "open, no name": (e) => ({ ...e, type: "open", place: { ...e.place, name: null } }),
  "bad type": (e) => ({ ...e, type: "view" }),
  "extra key": (e) => ({ ...e, userId: "x" }),
  "extra place key": (e) => ({ ...e, place: { ...e.place, accuracy: 5 } }),
  "missing name": (e) => ({ ...e, place: { lat: e.place.lat, lng: e.place.lng } }),
  "empty name": (e) => ({ ...e, place: { ...e.place, name: "" } }),
  "long name": (e) => ({ ...e, place: { ...e.place, name: "x".repeat(61) } }),
  "outside Ireland": (e) => ({ ...e, place: { ...e.place, lat: 48.8, lng: 2.3 } }),
  "bad time": (e) => ({ ...e, generatedAt: "soon" }),
  "wrong version": (e) => ({ ...e, schemaVersion: 2 }),
};
for (const [name, mutate] of Object.entries(eventMutations)) {
  test(`event validator agrees with the contract: ${name}`, () => {
    const e = mutate(goodEvent());
    assert.equal(validateEvent(e).ok, UsageEventSchema.safeParse(e).success);
  });
}

test("POST /event files a rounded event under a random key", async () => {
  const e = env();
  const body = JSON.stringify({ ...goodEvent(), place: { lat: 53.2917, lng: -6.4561, name: "Clondalkin" } });
  const res = await worker.fetch(
    new Request("https://w.example/event", { method: "POST", headers: { Origin: "https://wet-or-not.vercel.app" }, body }),
    e,
  );
  assert.equal(res.status, 200);
  const [[key, value]] = [...e.REPORTS.files];
  assert.match(key, /^events\/\d{4}-\d{2}-\d{2}\/share\/[0-9a-f-]{36}\.json$/);
  assert.deepEqual(JSON.parse(value).place, { lat: 53.29, lng: -6.46, name: "Clondalkin" });
});

test("POST /event refuses a bad event and an oversized one", async () => {
  const e = env();
  const post = (body) =>
    worker.fetch(new Request("https://w.example/event", { method: "POST", headers: { Origin: "https://wet-or-not.vercel.app" }, body }), e);
  assert.equal((await post(JSON.stringify({ ...goodEvent(), type: "view" }))).status, 400);
  assert.equal((await post("x".repeat(2000))).status, 413);
  assert.equal(e.REPORTS.files.size, 0);
});
