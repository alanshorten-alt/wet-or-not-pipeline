// Builds the single file to paste into the Cloudflare dashboard (no imports, no exports).
//   node worker/reports/build-single.mjs worker.js
import { readFileSync, writeFileSync } from "node:fs";

const here = (f) => new URL(f, import.meta.url);
const validate = readFileSync(here("validate.js"), "utf8").replace(/^export /gm, "");
const index = readFileSync(here("index.js"), "utf8")
  .replace(/^import .*\n/m, "")
  .replace(/^export default worker;\s*$/m, "")
  .replace(/^export /gm, "");
const out = process.argv[2];
if (!out) {
  console.error("usage: node worker/reports/build-single.mjs <output file>");
  process.exit(2);
}
writeFileSync(
  out,
  `// wet-or-not-reports: single-file copy for pasting into the Cloudflare dashboard.\n// Source of truth: worker/reports/index.js + validate.js in the pipeline repo (built by build-single.mjs).\n\n${validate}\n${index}\nexport default worker;\n`,
);
console.log(`wrote ${out}`);
