// File contracts: the only way engines talk to each other.
// Every file carries generatedAt and sourceTime (ISO 8601 UTC) so the phone
// can show "radar X min ago" and flag a stale run. Schemas are the source of
// truth; types are inferred from them. Pipeline jobs and the app both validate.
import { z } from "zod";

const isoTime = z.iso.datetime();

export const GridMetaSchema = z.object({
  /** Bounding box, degrees (WGS84). */
  bounds: z.object({
    south: z.number().min(-90).max(90),
    west: z.number().min(-180).max(180),
    north: z.number().min(-90).max(90),
    east: z.number().min(-180).max(180),
  }),
  cols: z.number().int().positive(),
  rows: z.number().int().positive(),
  /** Cell size in km (2.5 matches the Met Éireann model grid; keep it a setting). */
  cellKm: z.number().positive(),
});

/** Rain rate in mm/h, row-major from the north-west corner. */
const rainGrid = z.array(z.number().min(0).max(500));

function checkGrid(
  d: { grid: z.infer<typeof GridMetaSchema> },
  cells: number[],
  ctx: z.RefinementCtx,
  path: (string | number)[],
) {
  const { bounds, cols, rows } = d.grid;
  if (bounds.south >= bounds.north || bounds.west >= bounds.east) {
    ctx.addIssue({ code: "custom", message: "grid bounds are inverted", path: ["grid", "bounds"] });
  }
  if (cells.length !== cols * rows) {
    ctx.addIssue({ code: "custom", message: `expected ${cols * rows} cells, got ${cells.length}`, path });
  }
}

export const RadarFrameSchema = z
  .object({
    generatedAt: isoTime,
    sourceTime: isoTime,
    grid: GridMetaSchema,
    rainMmh: rainGrid,
  })
  .superRefine((d, ctx) => checkGrid(d, d.rainMmh, ctx, ["rainMmh"]));

export const NowcastStepSchema = z.object({
  /** Minutes ahead of sourceTime (5, 10, ... 120). */
  minutes: z.number().int().positive(),
  rainMmh: rainGrid,
});

export const NowcastSchema = z
  .object({
    generatedAt: isoTime,
    sourceTime: isoTime,
    grid: GridMetaSchema,
    steps: z.array(NowcastStepSchema).min(1),
    /** Rain motion, km/h east and north. */
    motionKmh: z.object({ east: z.number(), north: z.number() }),
    confidence: z.number().min(0).max(1),
  })
  .superRefine((d, ctx) => {
    d.steps.forEach((s, i) => checkGrid(d, s.rainMmh, ctx, ["steps", i, "rainMmh"]));
  });

export const RainNowSchema = z.enum(["yes", "no", "unsure"]);
export const WetnessSchema = z.enum(["dry", "damp", "soft", "waterlogged"]);

export const GroundRegionSchema = z.object({
  stationId: z.string().min(1),
  name: z.string().min(1),
  lat: z.number().min(-90).max(90),
  lng: z.number().min(-180).max(180),
  rainNow: RainNowSchema,
  rain24hMm: z
    .object({ median: z.number().min(0), low: z.number().min(0), high: z.number().min(0) })
    .refine((r) => r.low <= r.median && r.median <= r.high, "expected low <= median <= high"),
  stationCount: z.number().int().min(0),
  wetness: WetnessSchema,
  /** Age of the newest reading used, minutes. */
  readingAgeMin: z.number().min(0),
});

/** One Met Éireann synoptic station, from the Latest Observations CSV (latest hour). */
export const SynopticStationSchema = z.object({
  stationId: z.string().min(1),
  name: z.string().min(1),
  lat: z.number().min(-90).max(90),
  lng: z.number().min(-180).max(180),
  tempC: z.number().nullable(),
  weather: z.string().nullable(),
  /** Converted from knots. */
  windKmh: z.number().min(0).nullable(),
  gustKmh: z.number().min(0).nullable(),
  /** Compass point the wind blows from, e.g. "SW". */
  windFrom: z.string().nullable(),
  humidityPct: z.number().min(0).max(100).nullable(),
  /** Rain in the latest hour, mm. */
  rainHourMm: z.number().min(0).nullable(),
  pressureHpa: z.number().nullable(),
  /** The CSV has no timestamp: this is the start of the latest whole hour before the fetch (assumed). */
  observedAt: isoTime,
  readingAgeMin: z.number().min(0),
});

export const GroundSchema = z.object({
  generatedAt: isoTime,
  sourceTime: isoTime,
  regions: z.array(GroundRegionSchema),
  /** Synoptic stations. Empty until the synoptic engine has run. */
  synoptic: z.array(SynopticStationSchema).default([]),
});

/** "Got it wrong": one tap in the app sends this snapshot of what the phone showed at the pin.
 * generatedAt is when the person tapped; sourceTime is the radar scan the verdict used.
 * No name, account or device id. The pin is rounded to 2 decimals (about 1 km).
 * The Worker in worker/reports/ mirrors these limits by hand; worker/reports/validate.test.mjs keeps them in step. */
export const FeedbackReportSchema = z
  .object({
    schemaVersion: z.literal(1),
    generatedAt: isoTime,
    sourceTime: isoTime,
    place: z.object({ lat: z.number().min(51.2).max(55.6), lng: z.number().min(-10.9).max(-5.2) }).strict(),
    verdict: z
      .object({ state: z.enum(["dry", "showers", "wet"]), text: z.string().min(1).max(120) })
      .strict(),
    radar: z
      .object({
        ageMin: z.number().min(0).max(1440),
        confidence: z.number().min(0).max(1),
        motionKmh: z.object({ east: z.number().min(-300).max(300), north: z.number().min(-300).max(300) }).strict(),
        /** Radar rain rate at the pin in the latest frame, mm/h. */
        rainNowMmh: z.number().min(0).max(500).nullable(),
      })
      .strict(),
    /** Nowcast rain rate at the pin every 5 minutes ahead, mm/h (up to 24 steps = 2 hours). */
    forecastMmh: z.array(z.number().min(0).max(500)).max(24),
    /** Nearest synoptic station the app showed, if any. */
    station: z
      .object({
        name: z.string().min(1).max(60),
        distanceKm: z.number().min(0).max(500),
        rainHourMm: z.number().min(0).max(500).nullable(),
        windKmh: z.number().min(0).max(500).nullable(),
        readingAgeMin: z.number().min(0).max(10080),
      })
      .strict()
      .nullable(),
    /** Nearest CAMP gauge's 24 h rain, mm, when within range. */
    rain24hMm: z.number().min(0).max(1000).nullable(),
  })
  .strict();

/** Anonymous usage count from the app: a WhatsApp share, or an open from a shared link (`?src=wa`).
 * No id, no cookie, no device information. The place is rounded to 2 decimals (about 1 km) and carries
 * the label the app showed (a town, club or park), or null for "Your location".
 * The Worker in worker/reports/ mirrors these limits by hand; worker/reports/validate.test.mjs keeps them in step. */
export const UsageEventSchema = z
  .object({
    schemaVersion: z.literal(1),
    generatedAt: isoTime,
    type: z.enum(["share", "open"]),
    place: z
      .object({
        lat: z.number().min(51.2).max(55.6),
        lng: z.number().min(-10.9).max(-5.2),
        name: z.string().min(1).max(60).nullable(),
      })
      .strict(),
  })
  .strict();

const FreshnessEntry = z.object({ sourceTime: isoTime, stale: z.boolean() });

export const ManifestSchema = z.object({
  generatedAt: isoTime,
  /** Last 12 observed radar files, oldest first (1 hour back to now). */
  radarFiles: z.array(z.string()).max(12),
  nowcastFile: z.string().min(1),
  groundFile: z.string().min(1),
  /** Per-engine freshness so a late run is visible, not silent. */
  freshness: z.object({ ingest: FreshnessEntry, nowcast: FreshnessEntry, ground: FreshnessEntry }),
});

export type GridMeta = z.infer<typeof GridMetaSchema>;
export type RadarFrame = z.infer<typeof RadarFrameSchema>;
export type NowcastStep = z.infer<typeof NowcastStepSchema>;
export type Nowcast = z.infer<typeof NowcastSchema>;
export type RainNow = z.infer<typeof RainNowSchema>;
export type Wetness = z.infer<typeof WetnessSchema>;
export type GroundRegion = z.infer<typeof GroundRegionSchema>;
export type SynopticStation = z.infer<typeof SynopticStationSchema>;
export type Ground = z.infer<typeof GroundSchema>;
export type Manifest = z.infer<typeof ManifestSchema>;
export type FeedbackReport = z.infer<typeof FeedbackReportSchema>;
export type UsageEvent = z.infer<typeof UsageEventSchema>;
