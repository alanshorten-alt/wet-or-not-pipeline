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

export const GroundSchema = z.object({
  generatedAt: isoTime,
  sourceTime: isoTime,
  regions: z.array(GroundRegionSchema),
});

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
export type Ground = z.infer<typeof GroundSchema>;
export type Manifest = z.infer<typeof ManifestSchema>;
