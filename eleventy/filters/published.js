// Area filters — Published Writers (/published/ and /es/published/).
// Auto-loaded by eleventy.config.js. Every filter name starts with "pw" so it never
// collides with another page area.
//
// Data: data/site/spotlight.json (docs/DATA_SCHEMA.md → "spotlight.json"): Grapevine and
// La Viña stories with a byline whose extra.pub_date lies in the longest window
// (spotlight.list_days, e.g. [60, 90]), each with extra.geo.scope = neta65 | texas | other |
// unknown. Templates read it as db.spotlight; until src/_data/db.js lists "spotlight" this
// module reads the file itself (same link cleaning as db.js: safeUrl on url/image).
//
// The windows are counted from TODAY in America/Chicago (the build day here; the browser
// recounts from its own today in /assets/js/published.js), so the server-rendered default
// view and the script always agree on the same day.
//
// Dev switches:
//   PW_TODAY=2026-11-30 npx @11ty/eleventy …   build as if it were that day (empty windows)
//   PW_EMPTY=1 npx @11ty/eleventy …           build as if no story had been found yet
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import * as yaml from "js-yaml";
import { safeUrl } from "../../eleventy.config.js";
import { scriptJson } from "../script-json.js";

const require = createRequire(import.meta.url);

const TZ = "America/Chicago";
const EMPTY = !!process.env.PW_EMPTY;
/** Where a writer is from, in display order. "texas" here = Texas OUTSIDE Area 65. */
export const PW_GROUPS = ["neta65", "texas", "other", "unknown"];
/** The scope filter: which groups each choice shows (Area 65 first, then Texas, then the rest). */
export const PW_SCOPES = { neta65: ["neta65"], texas: ["neta65", "texas"], all: ["neta65", "texas", "other", "unknown"] };
const PUBS = ["all", "gv", "lv"];
/** Cards shown per group before a "Show all N stories" button (divisible by 2, 3 and 4 columns). */
export const PW_GROUP_LIMIT = 24;
const ANONYMOUS = /^\s*(anonymous|an[oó]nim[oa]|anon\.?)\s*$/i;
const YMD = /^\d{4}-\d{2}-\d{2}$/;

/* ------------------------------------------------------------------ */
/*  small utils                                                        */
/* ------------------------------------------------------------------ */
const str = (v) => (v === null || v === undefined ? "" : String(v)).replace(/\s+/g, " ").trim();

/** Lower case, accents removed, punctuation → spaces: "Peñasco, Tejas" → "penasco tejas". */
export function pwNorm(s) {
  return str(s).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function todayChicago() {
  const env = process.env.PW_TODAY;
  if (env && YMD.test(env)) return env;
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

/** "2026-09-23" minus 60 days → "2026-07-25" (calendar days, no time-zone drift). */
export function pwMinusDays(ymd, n) {
  const d = new Date(ymd + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

function hostOf(u) {
  try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; }
}

function initialsOf(name) {
  const words = str(name).replace(/[^\p{L}\s.-]/gu, " ").split(/[\s.-]+/).filter(Boolean);
  if (!words.length) return "";
  return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
}

/* ------------------------------------------------------------------ */
/*  settings + data                                                    */
/* ------------------------------------------------------------------ */
let cfgCache = null;
function spotlightConfig() {
  if (cfgCache) return cfgCache;
  let s = {};
  try { s = (yaml.load(fs.readFileSync("config/site.yml", "utf8")) || {}).spotlight || {}; } catch { s = {}; }
  cfgCache = s && typeof s === "object" ? s : {};
  return cfgCache;
}

function readSpotlightFile() {
  try {
    const data = JSON.parse(fs.readFileSync("data/site/spotlight.json", "utf8"));
    if (!data || !Array.isArray(data.items)) return null;
    for (const it of data.items) {
      if (!it || typeof it !== "object") continue;
      it.url = safeUrl(it.url);
      if (it.image !== undefined && it.image !== null) it.image = safeUrl(it.image);
      if (it.extra && typeof it.extra.issue_url === "string") it.extra.issue_url = safeUrl(it.extra.issue_url);
    }
    return data;
  } catch {
    return null;
  }
}

/** The spotlight site file: db.spotlight when db.js provides it, else read here. */
function spotlightOf(db) {
  if (EMPTY) return { updated: null, items: [] };
  const s = db && db.spotlight;
  if (s && Array.isArray(s.items)) return s;
  return readSpotlightFile() || { updated: null, items: [] };
}

function listDaysOf(data) {
  const raw = Array.isArray(data.list_days) ? data.list_days : spotlightConfig().list_days;
  const days = (Array.isArray(raw) ? raw : []).map(Number).filter((n) => Number.isInteger(n) && n > 0 && n <= 3660);
  const uniq = [...new Set(days)];
  if (!uniq.length) return { list: [60, 90], def: 60 };
  // "first = default" (config/site.yml); the choices are shown shortest first.
  return { list: [...uniq].sort((a, b) => a - b), def: uniq[0] };
}

function defaultScopeOf(data) {
  const v = data.default_scope || spotlightConfig().default_scope;
  return PW_SCOPES[v] ? v : "neta65";
}

/* ------------------------------------------------------------------ */
/*  view model                                                         */
/* ------------------------------------------------------------------ */
function viewItem(it, lang, h) {
  const e = it.extra || {};
  const geo = e.geo && typeof e.geo === "object" ? e.geo : {};
  const scope = PW_GROUPS.includes(geo.scope) ? geo.scope : "unknown";
  const pub = (e.publication || it.category) === "lv" || it.source === "lavina" ? "lv" : "gv";
  const date = YMD.test(e.pub_date || "") ? e.pub_date : str(it.date).slice(0, 10);
  const origTitle = str(it.title);
  const title = str(h.pickLang(it, "title", lang)) || origTitle;
  const origLang = it.lang === "en" || it.lang === "es" ? it.lang : "";
  const translated = !!origLang && origLang !== lang && title !== origTitle;
  const machine = translated && Array.isArray(it.machine) && it.machine.includes(lang);

  const rawAuthor = str(e.author);
  const anonymous = !rawAuthor || ANONYMOUS.test(rawAuthor);
  const author = anonymous ? h.translateKey("published.anonymous", lang) : rawAuthor;

  let place = "";
  if (scope !== "unknown") {
    place = str(lang === "es" ? geo.label_es : geo.label_en) || str(geo.label_en) || str(h.pickLang(it, "author_location", lang)) || str(e.author_location);
  }
  const county = (scope === "neta65" || scope === "texas") && str(geo.county) && !/\b(county|condado)\b/i.test(place)
    ? h.translateKey("published.county", lang, { county: str(geo.county) }) : "";

  const issue = str(h.pickLang(it, "issue_label", lang)) || str(e.issue_label);
  const section = str(h.pickLang(it, "section", lang));
  const topic = str(h.pickLang(it, "topic", lang));
  const meta = [];
  for (const v of [section, topic]) if (v && !meta.some((m) => pwNorm(m) === pwNorm(v))) meta.push(v);

  const i18nTitle = (it.i18n && it.i18n.title) || {};
  // Writer, place (both languages, county) and title (both languages); each word once.
  const search = [...new Set(pwNorm([
    rawAuthor, anonymous ? author : "", geo.city, geo.county, geo.label_en, geo.label_es, e.author_location,
    origTitle, i18nTitle.en, i18nTitle.es,
  ].filter(Boolean).join(" ")).split(" ").filter(Boolean))].join(" ");

  return {
    id: str(it.id),
    scope, pub, date,
    url: it.url, host: hostOf(it.url),
    image: str(it.image),
    title, titleLang: translated ? lang : origLang, origTitle: translated ? origTitle : "", origLang, machine,
    author, anonymous, initials: anonymous ? "" : initialsOf(rawAuthor),
    place, county,
    issue, meta,
    free: e.free === true ? true : e.free === false ? false : null,
    exclusive: e.online_exclusive === true,
    isNew: it.is_new === true,
    search,
  };
}

function inScope(item, scope) { return PW_SCOPES[scope].includes(item.scope); }
function inPub(item, pub) { return pub === "all" || item.pub === pub; }

/**
 * db | pwView(lang) → everything the page needs, with the DEFAULT filters applied
 * (default scope + first list_days window, all magazines, no search) so the page works
 * without JavaScript and matches what published.js shows on the same day.
 */
function pwView(db, lang, h) {
  const data = spotlightOf(db);
  const { list: listDays, def: defDays } = listDaysOf(data);
  const defScope = defaultScopeOf(data);
  const today = todayChicago();
  const cutoffs = Object.fromEntries(listDays.map((d) => [d, pwMinusDays(today, d)]));

  const seen = new Set();
  const items = [];
  for (const it of data.items || []) {
    if (!it || typeof it !== "object" || it.status === "gone" || it.kind !== "article" || !it.url) continue;
    const v = viewItem(it, lang, h);
    if (!v.date || !v.title || seen.has(v.id)) continue;
    seen.add(v.id);
    items.push(v);
  }
  const rank = Object.fromEntries(PW_GROUPS.map((g, i) => [g, i]));
  items.sort((a, b) => rank[a.scope] - rank[b.scope] || (a.date < b.date ? 1 : a.date > b.date ? -1 : 0) || a.title.localeCompare(b.title, lang));

  // counts[days][scope][pub] — stories inside each window (the window is open-ended at the top:
  // a story counts from its pub_date on, whatever the clock says).
  const counts = {};
  for (const d of listDays) {
    counts[d] = {};
    for (const s of Object.keys(PW_SCOPES)) {
      counts[d][s] = {};
      for (const p of PUBS) counts[d][s][p] = items.filter((i) => i.date >= cutoffs[d] && inScope(i, s) && inPub(i, p)).length;
    }
  }

  const defCut = cutoffs[defDays];
  const groups = PW_GROUPS.map((key) => {
    const all = items.filter((i) => i.scope === key);
    let shown = 0;
    const rows = all.map((i) => {
      const match = i.date >= defCut && inScope(i, defScope);
      const visible = match && shown < PW_GROUP_LIMIT;
      if (match) shown++;
      return { ...i, visible };
    });
    const matchCount = rows.filter((i) => i.date >= defCut && inScope(i, defScope)).length;
    return { key, items: rows, total: all.length, matchCount, hiddenByLimit: Math.max(0, matchCount - PW_GROUP_LIMIT) };
  }).filter((g) => g.total > 0);

  const shownTotal = counts[defDays][defScope].all;
  // "Also in the last N days: X stories by writers elsewhere in Texas / outside Texas"
  const nextScope = defScope === "neta65" ? "texas" : defScope === "texas" ? "all" : "";
  const moreCount = nextScope ? counts[defDays][nextScope].all - shownTotal : 0;

  const cfg = spotlightConfig();
  const counties = (Array.isArray(cfg.neta65_counties) ? cfg.neta65_counties : []).map(str).filter(Boolean)
    .sort((a, b) => a.localeCompare(b, "en"));

  return {
    today, cutoffs, listDays, defDays, defScope, defPub: "all",
    items, groups, counts, shownTotal, nextScope, moreCount,
    counties, countyCount: counties.length,
    updated: data.updated || null,
    limit: PW_GROUP_LIMIT,
    total: items.length,
  };
}

/** mailto: link with a subject and a pre-filled body (CRLF line breaks, RFC 6068). */
function pwMailto(email, subject, body) {
  const e = str(email);
  if (!e) return "";
  const q = [];
  if (subject) q.push("subject=" + encodeURIComponent(str(subject)));
  if (body) q.push("body=" + encodeURIComponent(String(body).replace(/\r?\n/g, "\r\n")));
  return safeUrl("mailto:" + e + (q.length ? "?" + q.join("&") : ""));
}

/** UI strings published.js needs (raw templates: it fills {n} {days} {pub} {q} {stories} itself). */
const JS_KEYS = [
  "status.neta65.one", "status.neta65.other", "status.texas.one", "status.texas.other",
  "status.all.one", "status.all.other", "status.pub", "status.q",
  "n_stories.one", "n_stories.other", "show_all",
  "empty.neta65", "empty.texas", "empty.all", "empty.filtered",
  "more.texas", "more.all", "btn.texas", "btn.all",
];
function pwStrings(lang, h) {
  const out = {};
  for (const k of JS_KEYS) out[k] = h.translateKey("published." + k, lang);
  return out;
}

/* ------------------------------------------------------------------ */
/*  Icon sprite: the page repeats a few icons in every card (150+ cards), so they are  */
/*  defined once as <symbol>s and referenced with <use> — same Lucide / local art as   */
/*  the {% icon %} shortcode, a fraction of the bytes.                                 */
/* ------------------------------------------------------------------ */
const symbolCache = new Map();
function iconFile(name) {
  const local = path.join("src/_includes/icons", `${name}.svg`);
  if (fs.existsSync(local)) return local;
  try {
    const f = path.join(path.dirname(require.resolve("lucide-static/package.json")), "icons", `${name}.svg`);
    return fs.existsSync(f) ? f : "";
  } catch { return ""; }
}
function symbolOf(name) {
  if (symbolCache.has(name)) return symbolCache.get(name);
  let out = "";
  const f = /^[a-z0-9-]+$/.test(name) ? iconFile(name) : "";
  if (f) {
    const svg = fs.readFileSync(f, "utf8").replace(/<!--.*?-->/gs, "").trim();
    const m = svg.match(/<svg\b([^>]*)>([\s\S]*)<\/svg>/i);
    if (m) {
      const keep = ["viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"]
        .map((a) => { const v = m[1].match(new RegExp(`\\s${a}="([^"]*)"`)); return v ? ` ${a}="${v[1]}"` : ""; }).join("");
      out = `<symbol id="pw-i-${name}"${keep}>${m[2].replace(/>\s+</g, "><").trim()}</symbol>`;
    }
  } else {
    console.warn(`[published] missing icon: ${name}`);
  }
  symbolCache.set(name, out);
  return out;
}
/** "quote,lock" | pwSprite → one hidden <svg> holding those symbols (put it once on the page). */
function pwSprite(names) {
  const list = String(names || "").split(",").map((s) => s.trim()).filter(Boolean);
  return `<svg class="pw-sprite" aria-hidden="true" focusable="false" width="0" height="0">${list.map(symbolOf).join("")}</svg>`;
}
/** "lock" | pwIcon("size-3") → <svg class="icon size-3"><use href="#pw-i-lock"/></svg> (decorative) */
function pwIcon(name, cls = "size-4") {
  const n = String(name || "");
  if (!/^[a-z0-9-]+$/.test(n)) return "";
  return `<svg class="icon ${cls}" aria-hidden="true" focusable="false"><use href="#pw-i-${n}"/></svg>`;
}

/** JSON for an inline <script type="application/json"> (the shared serializer, eleventy/script-json.js). */
const pwJson = scriptJson;

export default function (eleventyConfig, helpers) {
  const h = helpers || {};
  eleventyConfig.on("eleventy.before", () => { cfgCache = null; });
  eleventyConfig.addFilter("pwView", (db, lang) => pwView(db, lang || "en", h));
  eleventyConfig.addFilter("pwStrings", (lang) => pwStrings(lang || "en", h));
  eleventyConfig.addFilter("pwJson", pwJson);
  eleventyConfig.addFilter("pwSprite", pwSprite);
  eleventyConfig.addFilter("pwIcon", pwIcon);
  eleventyConfig.addFilter("pwMailto", pwMailto);
  // "2 stories" / "1 historia" (published.n_stories.one|other)
  eleventyConfig.addFilter("pwStories", (n, lang) => h.translateKey(Number(n) === 1 ? "published.n_stories.one" : "published.n_stories.other", lang, { n }));
}
