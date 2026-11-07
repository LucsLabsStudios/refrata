#!/usr/bin/env node
// Brand image generator for Refrata (LucsLabs Studios).
// Reuses the API keys of the Remotion studio (.env) through its env loader; no key is copied here.
//
//   node tools/brand/generate.mjs --plan tools/brand/plan.json --out-dir build/brand/raw [--only id1,id2]
//
// plan.json = [{ "id", "prompt", "size"?: "1024x1024", "background"?: "transparent"|"opaque", "quality"? }]
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const REMOTION_ENV = process.env.EDA_ENV_FILE || "D:/Remotion/Lab/.env";
process.env.EDA_ENV_FILE = REMOTION_ENV;
const { requireKey } = await import(pathToFileURL("D:/Remotion/Lab/tools/lib/env.mjs").href);

const flags = {};
const a = process.argv.slice(2);
for (let i = 0; i < a.length; i++) if (a[i].startsWith("--")) flags[a[i].slice(2)] = a[i + 1]?.startsWith("--") ? true : a[++i];

const key = requireKey("OPENAI_API_KEY");
const model = flags.model || "gpt-image-2.5-sunburst";
const plan = JSON.parse(readFileSync(resolve(flags.plan), "utf8"));
const outDir = resolve(flags["out-dir"] || "build/brand/raw");
const only = flags.only ? new Set(String(flags.only).split(",")) : null;
mkdirSync(outDir, { recursive: true });

async function one(job) {
  const out = join(outDir, `${job.id}.png`);
  if (existsSync(out) && !flags.force) return console.log(`= ${job.id} (exists)`);
  const body = {
    model, prompt: job.prompt, n: 1,
    size: job.size || "1024x1024",
    quality: job.quality || flags.quality || "high",
    ...(job.background ? { background: job.background, output_format: "png" } : {}),
  };
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch("https://api.openai.com/v1/images/generations", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify(body),
    });
    const txt = await res.text();
    if (res.ok) {
      const item = JSON.parse(txt).data?.[0];
      const buf = item?.b64_json ? Buffer.from(item.b64_json, "base64") : Buffer.from(await (await fetch(item.url)).arrayBuffer());
      writeFileSync(out, buf);
      return console.log(`✓ ${job.id} → ${out}`);
    }
    console.warn(`  ${job.id} attempt ${attempt + 1}: HTTP ${res.status} ${txt.slice(0, 300)}`);
    await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
  }
  throw new Error(`failed: ${job.id}`);
}

const jobs = plan.filter((j) => !only || only.has(j.id));
const results = await Promise.allSettled(jobs.map(one));
const bad = results.filter((r) => r.status === "rejected");
console.log(`done ${jobs.length - bad.length}/${jobs.length}`);
process.exit(bad.length ? 1 : 0);
