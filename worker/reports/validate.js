// Hand-written check of a "Got it wrong" report. Mirrors FeedbackReportSchema in
// contracts/contracts.ts (the Worker is pasted into the dashboard, so it carries no dependencies).
// validate.test.mjs runs both on the same examples so they cannot drift apart.

const isObj = (v) => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v, lo, hi) => typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
const numOrNull = (v, lo, hi) => v === null || num(v, lo, hi);
const keysAre = (o, keys) => Object.keys(o).length === keys.length && keys.every((k) => k in o);
// Same shape as zod's z.iso.datetime(): UTC, "Z" suffix, optional fractional seconds.
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;
const isoTime = (v) => typeof v === "string" && ISO.test(v) && !Number.isNaN(Date.parse(v));
const str = (v, min, max) => typeof v === "string" && v.length >= min && v.length <= max;

/** Returns { ok: true } or { ok: false, error }. */
export function validateReport(r) {
  const fail = (error) => ({ ok: false, error });
  if (!isObj(r) || !keysAre(r, ["schemaVersion", "generatedAt", "sourceTime", "place", "verdict", "radar", "forecastMmh", "station", "rain24hMm"])) return fail("shape");
  if (r.schemaVersion !== 1) return fail("schemaVersion");
  if (!isoTime(r.generatedAt) || !isoTime(r.sourceTime)) return fail("time");
  const { place, verdict, radar, station } = r;
  if (!isObj(place) || !keysAre(place, ["lat", "lng"]) || !num(place.lat, 51.2, 55.6) || !num(place.lng, -10.9, -5.2)) return fail("place");
  if (!isObj(verdict) || !keysAre(verdict, ["state", "text"]) || !["dry", "showers", "wet"].includes(verdict.state) || !str(verdict.text, 1, 120)) return fail("verdict");
  if (!isObj(radar) || !keysAre(radar, ["ageMin", "confidence", "motionKmh", "rainNowMmh"])) return fail("radar");
  const m = radar.motionKmh;
  if (!num(radar.ageMin, 0, 1440) || !num(radar.confidence, 0, 1) || !numOrNull(radar.rainNowMmh, 0, 500)) return fail("radar");
  if (!isObj(m) || !keysAre(m, ["east", "north"]) || !num(m.east, -300, 300) || !num(m.north, -300, 300)) return fail("motion");
  if (!Array.isArray(r.forecastMmh) || r.forecastMmh.length > 24 || !r.forecastMmh.every((v) => num(v, 0, 500))) return fail("forecast");
  if (station !== null) {
    if (!isObj(station) || !keysAre(station, ["name", "distanceKm", "rainHourMm", "windKmh", "readingAgeMin"])) return fail("station");
    if (!str(station.name, 1, 60) || !num(station.distanceKm, 0, 500) || !numOrNull(station.rainHourMm, 0, 500) || !numOrNull(station.windKmh, 0, 500) || !num(station.readingAgeMin, 0, 10080)) return fail("station");
  }
  if (!numOrNull(r.rain24hMm, 0, 1000)) return fail("rain24h");
  return { ok: true };
}
