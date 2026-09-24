// All synced content (written by scripts/sync/build_data.py into data/site/*.json).
// Templates use e.g. db.videos.items, db.status.sources …
//
// Link safety: every URL field is passed through safeUrl() (eleventy.config.js) here, once, so
// no page, filter or JSON index can print a "javascript:" link or a broken "www.example.org"
// one. Hand-edited content is the realistic source (content/districts.yml `website`,
// content/events/*.md `url` / `online_url` / `flyer`, announcements `url` / `image`).
// "www.x.org" and "zoom.us/j/1" are repaired to https://…; unusable values become "" (the
// templates hide empty links). An item whose own `url` is unusable points to its anchor on our
// page (committee event / announcement) or is left out. Every repaired or dropped value is
// written to the build log and listed in db.status.link_problems [{where, field, value, fixed}].
import fs from "node:fs";
import path from "node:path";
import { safeUrl } from "../../eleventy.config.js";

// "spotlight" = the published-writers file (Grapevine / La Viña stories by Texas writers, Area 65
// first — docs/DATA_SCHEMA.md → "spotlight.json"), read by the home page, /published/, /read/,
// /contribute/ and the search index as db.spotlight. Its links (url, image, extra.issue_url) get the
// same cleaning as every other file here. Without the file, db.spotlight is { updated: null, items: [] }.
const FILES = [
  "episodes", "videos", "instagram", "articles", "pdfs", "drive", "events",
  "announcements", "editorial", "weekly_open", "whatsnew", "status", "districts",
  "spotlight",
];

// Field names that hold a link or an image address: url, image, extra.online_url, extra.thumbs[],
// referrers[].url, shows[].apple, issues[].cover, profiles.gv.avatar, district website …
// Only string values are touched (extra.is_image is a boolean); extra.host (a bare host name),
// extra.file (a repository path) and extra.link_texts do not match.
const URL_KEY = /(?:^|_)(?:url|link|image|website|thumbs?|cover|avatar|feed|web|hub|apple|spotify|amazon|permalink)$/i;

function fixOne(value, field, where, problems) {
  const fixed = safeUrl(value);
  if (fixed !== value.trim()) problems.push({ where, field, value: value.slice(0, 160), fixed });
  return fixed;
}

function cleanLinks(node, key, file, where, problems) {
  if (Array.isArray(node)) {
    node.forEach((v, i) => {
      if (typeof v === "string") { if (URL_KEY.test(key)) node[i] = fixOne(v, key, where, problems); }
      else if (v && typeof v === "object") cleanLinks(v, key, file, v.id ? `${file} ${v.id}` : where, problems);
    });
    return;
  }
  for (const [k, v] of Object.entries(node)) {
    if (k === "i18n") continue; // translated text only
    if (typeof v === "string") { if (URL_KEY.test(k)) node[k] = fixOne(v, k, where, problems); }
    else if (v && typeof v === "object") cleanLinks(v, k, file, !Array.isArray(v) && v.id ? `${file} ${v.id}` : where, problems);
  }
}

export default function () {
  const out = {};
  const problems = [];
  for (const name of FILES) {
    const p = path.join("data", "site", `${name}.json`);
    let data = { updated: null, items: [] };
    try {
      if (fs.existsSync(p)) data = JSON.parse(fs.readFileSync(p, "utf8"));
    } catch (e) {
      console.warn(`[content] could not read ${p}: ${e.message}`);
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) data = { updated: null, items: [] };
    if (!Array.isArray(data.items)) data.items = [];
    const hadUrl = new Set(data.items.filter((it) => it && typeof it.url === "string" && it.url.trim()));
    cleanLinks(data, "", `${name}.json`, `${name}.json`, problems);
    // An item whose own link was unusable: a committee event / announcement falls back to its
    // anchor on our page (as if no url had been given); anything else would be a card that goes
    // nowhere, so it is left out (it is listed in the log and in link_problems).
    data.items = data.items.filter((it) => {
      if (!hadUrl.has(it) || it.url) return true;
      const slug = it.extra && typeof it.extra.slug === "string" ? encodeURIComponent(it.extra.slug) : "";
      if (slug && it.kind === "event") { it.url = `/events/#${slug}`; return true; }
      if (slug && it.kind === "announcement") { it.url = `/announcements/#${slug}`; return true; }
      problems.push({ where: `${name}.json ${it.id || ""}`.trim(), field: "item", value: "(left out: no usable link)", fixed: "" });
      return false;
    });
    out[name] = data;
  }
  if (problems.length) console.warn(`[links] ${problems.length} link value(s) in data/site repaired or hidden:`);
  // The same item is often in two files (events.json + whatsnew.json): log each value once.
  const seen = new Set();
  for (const pr of problems) {
    const k = pr.field === "item" ? pr.where : pr.field + "\u0000" + pr.value;
    if (seen.has(k)) continue;
    seen.add(k);
    if (seen.size > 25) { console.warn("[links] … more in db.status.link_problems"); break; }
    console.warn(pr.field === "item" ? `[links] ${pr.where}: left out — its link is not usable`
      : pr.fixed ? `[links] ${pr.where}: ${pr.field} ${JSON.stringify(pr.value)} → ${pr.fixed}`
      : `[links] ${pr.where}: ${pr.field} ${JSON.stringify(pr.value)} is not a usable link — hidden`);
  }
  out.status.link_problems = problems;
  return out;
}
