// Validates a directory of data files against the contracts, then uploads it
// to Cloudflare R2 (S3-compatible API). Data files go first and manifest.json
// goes last, so the manifest never points at a file that is not there yet.
//
//   node pipeline/publish.mjs public/sample --prefix sample --dry-run
//   node pipeline/publish.mjs out --prefix live --prune   (also deletes radar frames the manifest no longer lists)
//
// Env (set as GitHub secrets, never committed or pasted in chat):
//   R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import { DeleteObjectsCommand, ListObjectsV2Command, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { GroundSchema, ManifestSchema, NowcastSchema, PitchIndexSchema, PitchTileSchema, RadarFrameSchema } from "../contracts/contracts.ts";

const args = process.argv.slice(2);
const dir = args.find((a) => !a.startsWith("--") && a !== flagValue("--prefix"));
const dryRun = args.includes("--dry-run");
const prune = args.includes("--prune");
// For files that live beside the live feed but are not part of it (pitch outlines): no manifest needed.
const noManifest = args.includes("--no-manifest");
const prefix = (flagValue("--prefix") ?? "").replace(/^\/+|\/+$/g, "");

function flagValue(name) {
  const i = args.indexOf(name);
  return i > -1 ? args[i + 1] : undefined;
}

if (!dir) {
  console.error("usage: node pipeline/publish.mjs <dir> [--prefix <key prefix>] [--dry-run]");
  process.exit(2);
}

function schemaFor(file) {
  if (file === "manifest.json") return ManifestSchema;
  if (file === "nowcast.json") return NowcastSchema;
  if (file === "ground.json") return GroundSchema;
  if (file.startsWith("radar/")) return RadarFrameSchema;
  if (file === "pitches/index.json") return PitchIndexSchema;
  if (file.startsWith("pitches/")) return PitchTileSchema;
  return null;
}

function listFiles(root, rel = "") {
  return readdirSync(join(root, rel), { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? listFiles(root, join(rel, e.name)) : [join(rel, e.name).replaceAll("\\", "/")],
  );
}

const files = listFiles(dir).filter((f) => f.endsWith(".json"));
if (!noManifest && !files.includes("manifest.json")) {
  console.error("manifest.json is required");
  process.exit(1);
}

// Validate everything before uploading anything.
const bodies = new Map();
for (const file of files) {
  const text = readFileSync(join(dir, file), "utf8");
  const schema = schemaFor(file);
  if (schema) {
    const result = schema.safeParse(JSON.parse(text));
    if (!result.success) {
      console.error(`${file} does not match its contract:`, result.error.issues.slice(0, 5));
      process.exit(1);
    }
  } else {
    JSON.parse(text);
  }
  bodies.set(file, text);
}

// manifest.json last; short cache so freshness shows quickly. Other files get 3 minutes,
// matching the edge cache rule on the custom domain.
// Pitch outlines change weekly at most: a day of caching is fine.
const ordered = noManifest ? [...files] : [...files.filter((f) => f !== "manifest.json"), "manifest.json"];
const cacheControl = (f) => (f === "manifest.json" ? "public, max-age=30" : f.startsWith("pitches/") ? "public, max-age=86400" : "public, max-age=180");
const key = (f) => (prefix ? `${prefix}/${f}` : f);

if (dryRun) {
  for (const f of ordered) console.log(`[dry run] would upload ${key(f)} (${bodies.get(f).length} bytes, ${cacheControl(f)})`);
  if (prune && !noManifest) console.log("[dry run] would prune radar frames not listed in the manifest");
  process.exit(0);
}

// Pasted secrets often carry a trailing space or newline, so trim before use.
const env = (n) => (process.env[n] ?? "").trim();
const R2_ACCOUNT_ID = env("R2_ACCOUNT_ID");
const R2_BUCKET = env("R2_BUCKET");
const R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID");
const R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY");
if (!R2_ACCOUNT_ID || !R2_BUCKET || !R2_ACCESS_KEY_ID || !R2_SECRET_ACCESS_KEY) {
  console.error("Missing R2 env vars (R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)");
  process.exit(1);
}
// A wrong account id makes the endpoint host name invalid, which shows up as an opaque TLS
// "handshake failure". Check the shape here so the error names the real problem.
if (!/^[0-9a-f]{32}$/i.test(R2_ACCOUNT_ID)) {
  console.error(
    `R2_ACCOUNT_ID must be exactly the 32-character account id (the part before .r2.cloudflarestorage.com), not a URL; got ${R2_ACCOUNT_ID.length} characters.`,
  );
  process.exit(1);
}
if (/[/:\s]/.test(R2_BUCKET)) {
  console.error("R2_BUCKET must be just the bucket name, for example wet-or-not-data.");
  process.exit(1);
}

const s3 = new S3Client({
  region: "auto",
  endpoint: `https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
  credentials: { accessKeyId: R2_ACCESS_KEY_ID, secretAccessKey: R2_SECRET_ACCESS_KEY },
});

for (const f of ordered) {
  await s3.send(
    new PutObjectCommand({
      Bucket: R2_BUCKET,
      Key: key(f),
      // Compressed here because R2's public URL doesn't compress: the nowcast is 1.4 MB as text and about 15 KB gzipped.
      // Browsers undo the gzip transparently.
      Body: gzipSync(bodies.get(f), { level: 9 }),
      ContentType: "application/json",
      ContentEncoding: "gzip",
      CacheControl: cacheControl(f),
    }),
  );
  console.log(`uploaded ${key(f)}`);
}

// Optional clean-up: only objects under <prefix>/radar/ that the manifest we just wrote does not list.
if (prune && !noManifest) {
  const keep = new Set(JSON.parse(bodies.get("manifest.json")).radarFiles.map(key));
  const listed = await s3.send(new ListObjectsV2Command({ Bucket: R2_BUCKET, Prefix: key("radar/") }));
  const stale = (listed.Contents ?? []).map((o) => o.Key).filter((k) => k && !keep.has(k));
  if (stale.length) {
    await s3.send(new DeleteObjectsCommand({ Bucket: R2_BUCKET, Delete: { Objects: stale.map((Key) => ({ Key })) } }));
  }
  console.log(`pruned ${stale.length} old radar frame(s)`);
}
