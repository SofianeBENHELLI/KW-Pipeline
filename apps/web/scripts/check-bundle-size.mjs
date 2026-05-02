#!/usr/bin/env node
// Bundle-size budget enforcer for #125.
//
// Walks `dist/assets/*.js` after `vite build`, computes gzip size for
// each emitted chunk, matches each chunk against entries in
// `bundle-budgets.json`, and exits non-zero if any chunk exceeds its
// budget or any required pattern has no matching emitted asset.
//
// Run via `npm run bundle:check` from `apps/web/`.

import { readdir, readFile, stat } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = join(HERE, "..");
const ASSETS_DIR = join(WEB_ROOT, "dist", "assets");
const BUDGETS_PATH = join(WEB_ROOT, "bundle-budgets.json");

const KB = 1024;

async function loadBudgets() {
  const raw = await readFile(BUDGETS_PATH, "utf8");
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed.budgets)) {
    throw new Error(`bundle-budgets.json: 'budgets' must be an array.`);
  }
  return parsed.budgets.map((b) => ({
    pattern: new RegExp(b.pattern),
    rawPattern: b.pattern,
    label: b.label ?? b.pattern,
    maxGzipKB: Number(b.maxGzipKB),
    required: Boolean(b.required),
  }));
}

async function listJsAssets() {
  let entries;
  try {
    entries = await readdir(ASSETS_DIR);
  } catch (err) {
    if (err && err.code === "ENOENT") {
      console.error(
        `No build output at ${ASSETS_DIR}. Run \`npm run build\` from apps/web/ first.`,
      );
      process.exit(2);
    }
    throw err;
  }
  return entries.filter((name) => name.endsWith(".js"));
}

function pad(s, n) {
  s = String(s);
  return s.length >= n ? s : s + " ".repeat(n - s.length);
}

async function main() {
  const budgets = await loadBudgets();
  const names = await listJsAssets();

  const rows = [];
  const matchedPatterns = new Set();
  const failures = [];

  for (const name of names.sort()) {
    const path = join(ASSETS_DIR, name);
    const bytes = await readFile(path);
    const gzKB = gzipSync(bytes, { level: 9 }).length / KB;
    const matched = budgets.find((b) => b.pattern.test(name));

    if (matched) {
      matchedPatterns.add(matched.rawPattern);
      const ok = gzKB <= matched.maxGzipKB;
      rows.push({
        name,
        label: matched.label,
        gzKB,
        max: matched.maxGzipKB,
        status: ok ? "OK" : "FAIL",
      });
      if (!ok) {
        failures.push(
          `${name}: ${gzKB.toFixed(1)} KB gz exceeds budget ${matched.maxGzipKB} KB (${matched.label})`,
        );
      }
    } else {
      rows.push({ name, label: "(no budget)", gzKB, max: null, status: "—" });
    }
  }

  // Header + table.
  console.log(
    `\n${pad("Asset", 48)}${pad("Label", 36)}${pad("gz KB", 10)}${pad("Budget KB", 12)}Status`,
  );
  console.log("-".repeat(48 + 36 + 10 + 12 + 6));
  for (const r of rows) {
    console.log(
      `${pad(r.name, 48)}${pad(r.label, 36)}${pad(r.gzKB.toFixed(1), 10)}${pad(r.max ?? "—", 12)}${r.status}`,
    );
  }

  // Required budgets that didn't match anything emitted.
  for (const b of budgets) {
    if (b.required && !matchedPatterns.has(b.rawPattern)) {
      failures.push(
        `Required budget ${b.rawPattern} (${b.label}) had no matching emitted asset. ` +
          `Did the chunk get renamed?`,
      );
    }
  }

  if (failures.length) {
    console.error("\nBundle budget violations:");
    for (const f of failures) console.error(`  - ${f}`);
    console.error(
      "\nTo investigate, open apps/web/dist/stats.html (the rollup-plugin-visualizer treemap).",
    );
    process.exit(1);
  }

  console.log("\nAll bundle budgets satisfied.");
}

await main();
