// `build` in every template — the fingerprint of the site's CODE, used for:
//   * the ?v= on the CSS / JS links in layouts/base.njk (a new file name whenever the code changes,
//     so neither the browser nor the service worker can pair new pages with old styles or scripts);
//   * the service worker's version (src/pages/sw.11ty.js → /sw.js): its app-shell cache is named after
//     it, and a new value is what makes browsers install the new worker ("Updated — reload").
// It hashes what the CSS, the JS and the worker are built from — the templates and filters Tailwind
// reads for class names (src/_includes, src/pages, eleventy/), src/assets (styles, scripts, fonts,
// icons), eleventy.config.js and package-lock.json — and NOT the synced content (data/,
// src/assets/cache/) nor what only changes the HTML (settings in config/, strings in src/_i18n,
// src/_data): pages are network-first, so they are always fresh anyway. So neither the daily content
// update nor a settings edit changes it: returning visitors keep their cached styles and scripts
// (no re-download on a weak signal) and get no "Updated — reload" prompt; a code change does.
// (main.css imports Tailwind with source(none), so its classes come only from those folders.)
//   build.version  "c3f09a1b2d"  (10 hex characters)
//   build.commit   short git commit, when known ("" otherwise) — for the maintainer, not used in URLs
//   build.time     ISO time of this build
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const ROOTS = [path.join("src", "_includes"), path.join("src", "pages"), path.join("src", "assets"), "eleventy", "eleventy.config.js", "package-lock.json"];
const SKIP = new Set([path.join("src", "assets", "cache")]);

function files(p, out) {
  if (SKIP.has(path.normalize(p))) return out;
  let st;
  try { st = fs.statSync(p); } catch { return out; }
  if (st.isDirectory()) for (const name of fs.readdirSync(p).sort()) files(path.join(p, name), out);
  else if (st.isFile()) out.push(p);
  return out;
}

function commit() {
  if (process.env.GITHUB_SHA) return process.env.GITHUB_SHA.slice(0, 7);
  try {
    return execFileSync("git", ["rev-parse", "--short", "HEAD"], { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
  } catch {
    return "";
  }
}

export default function () {
  const h = crypto.createHash("sha256");
  for (const f of ROOTS.flatMap((r) => files(r, []))) {
    h.update(f.split(path.sep).join("/"));
    h.update("\0");
    h.update(fs.readFileSync(f));
    h.update("\0");
  }
  return { version: h.digest("hex").slice(0, 10), commit: commit(), time: new Date().toISOString() };
}
