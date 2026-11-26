// Cloudflare Worker: fires the radar workflow every 5 minutes on time.
// GitHub's own schedule drifts 10-20 minutes; a Worker cron does not. The Worker
// does no radar work (free plan CPU is 10 ms): it only calls GitHub's
// workflow_dispatch API.
//
// Secret (never committed): GITHUB_TOKEN, a fine-grained token limited to the
// wet-or-not repo with "Actions: Read and write".
// Vars (wrangler.toml): REPO, WORKFLOW, REF.
const worker = {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // Open the Worker URL to check the last dispatch by hand; it does not trigger a run.
  async fetch() {
    return new Response("wet-or-not radar trigger: runs on a */5 cron\n");
  },
};

export default worker;

async function dispatch(env) {
  const url = `https://api.github.com/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "wet-or-not-cron",
    },
    body: JSON.stringify({ ref: env.REF }),
  });
  // 204 means queued. Anything else shows up in the Worker's logs.
  if (res.status !== 204) console.error(`dispatch failed: HTTP ${res.status} ${await res.text()}`);
}
