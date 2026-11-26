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
import { DeleteObjectsCommand, ListObjectsV2Command, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { GroundSchema, ManifestSchema, NowcastSchema, RadarFrameSchema } from "../contracts/contracts.ts";

const args = process.argv.slice(2);
const dir = args.find((a) => !a.startsWith("--") && a !== flagValue("--prefix"));
const dryRun = args.includes("--dry-run");
const prune = args.includes("--prune");
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
  return null;
}

function listFiles(root, rel = "") {
  return readdirSync(join(root, rel), { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? listFiles(root, join(rel, e.name)) : [join(rel, e.name).replaceAll("\\", "/")],
  );
}

const files = listFiles(dir).filter((f) => f.endsWith(".json"));
if (!files.includes("manifest.json")) {
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
const ordered = [...files.filter((f) => f !== "manifest.json"), "manifest.json"];
const cacheControl = (f) => (f === "manifest.json" ? "public, max-age=30" : "public, max-age=180");
const key = (f) => (prefix ? `${prefix}/${f}` : f);

if (dryRun) {
  for (const f of ordered) console.log(`[dry run] would upload ${key(f)} (${bodies.get(f).length} bytes, ${cacheControl(f)})`);
  if (prune) console.log("[dry run] would prune radar frames not listed in the manifest");
  process.exit(0);
}

const { R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY } = process.env;
if (!R2_ACCOUNT_ID || !R2_BUCKET || !R2_ACCESS_KEY_ID || !R2_SECRET_ACCESS_KEY) {
  console.error("Missing R2 env vars (R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)");
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
      Body: bodies.get(f),
      ContentType: "application/json",
      CacheControl: cacheControl(f),
    }),
  );
  console.log(`uploaded ${key(f)}`);
}

// Optional clean-up: only objects under <prefix>/radar/ that the manifest we just wrote does not list.
if (prune) {
  const keep = new Set(JSON.parse(bodies.get("manifest.json")).radarFiles.map(key));
  const listed = await s3.send(new ListObjectsV2Command({ Bucket: R2_BUCKET, Prefix: key("radar/") }));
  const stale = (listed.Contents ?? []).map((o) => o.Key).filter((k) => k && !keep.has(k));
  if (stale.length) {
    await s3.send(new DeleteObjectsCommand({ Bucket: R2_BUCKET, Delete: { Objects: stale.map((Key) => ({ Key })) } }));
  }
  console.log(`pruned ${stale.length} old radar frame(s)`);
}
