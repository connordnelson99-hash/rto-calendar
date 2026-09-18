#!/usr/bin/env node
/**
 * export_digest_md.js — produce the weekly digest markdown headlessly.
 *
 * The calendar app's "Download markdown" button calls
 * window.buildDigestMarkdown(), which lives in webcal-v2/data.js and is plain
 * JS over the same rto_events_with_docs.json this repo already builds. Nothing
 * about it needs a browser, so rather than reimplement the format in Python
 * (and let the two drift the first time the screener changes), this harness
 * loads data.js with a minimal window/fetch shim and calls the real function.
 *
 * The output is byte-identical to what the Download button produces for the
 * same week. If the app's export changes, this changes with it.
 *
 * Usage:
 *   node export_digest_md.js                      # current ISO week -> stdout
 *   node export_digest_md.js --week 2026-07-27    # a specific week (Monday, ISO)
 *   node export_digest_md.js --last-week --out digest.md
 *   node export_digest_md.js --list               # available week keys
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const HERE = __dirname;
const REPO = path.resolve(HERE, "..");
const DATA_JS = path.join(REPO, "webcal-v2", "data.js");

function parseArgs(argv) {
  const args = { week: null, lastWeek: false, list: false, out: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--week") args.week = argv[++i];
    else if (a === "--last-week") args.lastWeek = true;
    else if (a === "--list") args.list = true;
    else if (a === "--out") args.out = argv[++i];
    else if (a === "-h" || a === "--help") { console.log(require("fs").readFileSync(__filename, "utf8").split("*/")[0]); process.exit(0); }
    else { console.error(`unknown argument: ${a}`); process.exit(2); }
  }
  return args;
}

// data.js fetches "../rto-docs/<file>?t=<cachebuster>" relative to webcal-v2/.
// Resolve those to local reads so no server is involved.
function localFetch(url) {
  const clean = String(url).split("?")[0].replace(/^\.\.\//, "");
  const file = path.join(REPO, clean);
  if (!fs.existsSync(file)) {
    return Promise.resolve({ ok: false, status: 404, statusText: `no such file: ${file}` });
  }
  const text = fs.readFileSync(file, "utf8");
  return Promise.resolve({
    ok: true, status: 200, statusText: "OK",
    json: async () => JSON.parse(text),
    text: async () => text,
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (!fs.existsSync(DATA_JS)) {
    console.error(`Cannot find ${DATA_JS}. Run this from inside the RTO Calendar repo.`);
    process.exit(1);
  }

  // data.js is an IIFE that decorates `window`. Give it one, plus the handful
  // of globals it touches, and evaluate it in this context.
  const sandbox = { console, Date, JSON, Math, URL, TextEncoder, TextDecoder, setTimeout, clearTimeout };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.fetch = localFetch;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(DATA_JS, "utf8"), sandbox, { filename: DATA_JS });

  if (typeof sandbox.loadMarketsData !== "function" ||
      typeof sandbox.buildDigestMarkdown !== "function") {
    console.error("data.js did not expose loadMarketsData/buildDigestMarkdown. " +
                  "The app's export API changed; update this harness.");
    process.exit(1);
  }

  const data = await sandbox.loadMarketsData();
  const weeks = (data && data.weeks) || [];
  const keys = weeks.map(w => w.key);

  if (args.list) {
    for (const w of weeks) {
      const mark = w.key === data.currentWeekKey ? "  <- current" : "";
      console.log(`${w.key}  ${w.weekStart}..${w.weekEnd}  ${w.items.length} meetings${mark}`);
    }
    return;
  }

  let key = args.week || data.currentWeekKey;
  if (args.lastWeek) {
    const i = keys.indexOf(data.currentWeekKey);
    if (i <= 0) {
      console.error("No week before the current one in the dataset.");
      process.exit(1);
    }
    key = keys[i - 1];
  }

  if (!keys.includes(key)) {
    console.error(`Week ${key} is not in the dataset. Known weeks:\n  ${keys.join("\n  ")}`);
    process.exit(1);
  }

  const week = weeks.find(w => w.key === key);
  const md = sandbox.buildDigestMarkdown(data, key);

  if (args.out) {
    fs.writeFileSync(args.out, md, "utf8");
    console.error(`Wrote digest for week ${key} (${week.items.length} meetings) -> ${args.out}`);
  } else {
    process.stdout.write(md);
  }
}

main().catch(err => { console.error(err); process.exit(1); });
