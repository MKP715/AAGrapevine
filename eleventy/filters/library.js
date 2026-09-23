// Library (all PDFs & documents) + site-wide Search — Eleventy filters.
//
// Auto-loaded by eleventy.config.js. Everything here runs at BUILD time:
//   * libDocs(db, lang)        → normalized, newest-first list of every library
//                                 document (crawled PDFs + committee Drive docs)
//   * libFacets(docs, lang)    → facet values + counts (source, language, type, year)
//   * libCollections(docs,lang)→ quick "collections" (GVR kit, RLV kit, catalogs …)
//   * libStats(docs)           → hero counters
//   * libIndexJson(db, lang)   → compact JSON for /library-index.json (client search)
//   * searchIndexJson(db, nav, lang, site) → compact JSON for /search-index.json
//   * libNum(n, lang)          → 3,100 (same locales as the rest of the site: en-US / es-US)
//   * jsonScript               → JSON safe to embed inside <script type="application/json">
//
// How crawled PDFs are described (scripts/sync/crawl.py + crawl_rules.py):
//   category = "gvr" / "rlv" when the PDF is on the official GVR / RLV resource page
//              (the rep "kits"), else the document type (postcard, order-form, news …);
//   tags     = for kit PDFs, the document type as well (["postcard"]);
//   lang     = language of the TITLE;  extra.doc_lang = language of the DOCUMENT;
//              (a document keeps both: `l` = document language, `tl` = title language,
//              so an untranslated title gets the right lang="" for screen readers);
//   date     = upload month ("2026-02-01", day unknown) or the exact day; may be null;
//   extra.orphan = no page links to the file any more (kept, but de-emphasized here).
//
// Dev/test switch: LIB_EMPTY=1 npx @11ty/eleventy …  builds the library and the
// search index as if no content had been synced yet (to check empty states).
import { openSync, readSync, closeSync } from "node:fs";
import path from "node:path";

const EMPTY = () => !!process.env.LIB_EMPTY;
// Only real web links (or site-relative paths) ever reach an href/src.
const SAFE_URL = /^(https?:\/\/|\/(?!\/))/i;
const DAY = 864e5;
const LOCALES = { en: "en-US", es: "es-US" }; // same as eleventy.config.js / app.js
const OFFICIAL_HOST = /(^|\.)(aagrapevine|aalavina)\.org$/i;
// A two-letter language code ("und" and junk → "").
const okLang = (v) => (typeof v === "string" && /^[a-z]{2}$/.test(v) ? v : "");
// First pages taller than this (height ÷ width) are shown whole instead of top-cropped:
// Letter (1.29) and A4 (1.41) covers crop well at the top (masthead + headline); booklets,
// posters and legal-size pages (1.55+) often center their title, so a top crop is a flat band.
const TALL_PAGE = 1.45;

/* ------------------------------------------------------------------ */
/*  Vocabulary                                                          */
/* ------------------------------------------------------------------ */

// Document types in display order + icon. Unknown types (e.g. a new Drive
// folder name) are listed alphabetically before "other" with a generic icon
// and a prettified label. "rep" is the type of kit PDFs the crawler could not
// sub-classify ("Rep resources"); the kits themselves are collections.
export const CATEGORIES = [
  ["news", "newspaper"], ["catalog", "book-open"], ["postcard", "mail"], ["flyer", "megaphone"],
  ["order-form", "clipboard-list"], ["guidelines", "list-checks"], ["workbook", "notebook-pen"],
  ["service", "hand-heart"], ["literature", "book-marked"], ["rep", "badge-check"],
  ["reports", "file-chart-column"], ["notes", "notepad-text"], ["slides", "presentation"],
  ["workshops", "graduation-cap"], ["forms", "file-pen-line"], ["other", "file-text"],
];
const CAT_ICON = Object.fromEntries(CATEGORIES);
const CAT_ORDER = { ...Object.fromEntries(CATEGORIES.map(([k], i) => [k, i])), other: 999 }; // "other" always last
// Drive folder names → library type keys (the Drive sync already maps
// English/Spanish folder names; this only merges near-duplicates).
const CAT_ALIASES = { flyers: "flyer", volantes: "flyer", postcards: "postcard", catalogs: "catalog", minutes: "notes", form: "forms", report: "reports", workshop: "workshops" };
const KITS = new Set(["gvr", "rlv"]);
// Tags that name a document type (kit PDFs carry their type as a tag).
const TYPE_TAGS = new Set(CATEGORIES.map(([k]) => k).filter((k) => k !== "rep" && k !== "other"));

export const SOURCES = [
  { key: "gv", icon: "grapes", tone: "gv" },
  { key: "lv", icon: "grapes", tone: "lv" },
  { key: "neta", icon: "users", tone: "vine" },
];
export const LANGS = ["en", "es", "fr"];

// Quick collections. `code` is a single letter stored per document in the JSON.
export const COLLECTIONS = [
  { key: "gvr-kit", code: "g", icon: "badge-check", tone: "gv" },
  { key: "rlv-kit", code: "r", icon: "badge-check", tone: "lv" },
  { key: "catalogs", code: "c", icon: "book-open", tone: "grape" },
  { key: "flyers", code: "f", icon: "megaphone", tone: "vine" },
  { key: "news", code: "n", icon: "newspaper", tone: "gv" },
  { key: "reports", code: "m", icon: "folder-open", tone: "vine" },
];

// Drive items that are NOT library documents.
const DRIVE_SKIP_KINDS = new Set(["photo", "video_file", "event", "announcement"]);
const DRIVE_SKIP_CATS = new Set(["photos", "fotos", "announcements", "anuncios"]);

/* ------------------------------------------------------------------ */
/*  Text helpers                                                        */
/* ------------------------------------------------------------------ */

// Decode %XX escapes that can leak into titles from file names ("La-Vin%CC%83a").
function safeDecode(s) {
  if (!s || s.indexOf("%") === -1) return s;
  try { return decodeURIComponent(s); } catch { /* fall through: decode valid runs only */ }
  return s.replace(/(?:%[0-9A-Fa-f]{2})+/g, (run) => { try { return decodeURIComponent(run); } catch { return run; } });
}

function squish(s) { return String(s || "").replace(/\s+/g, " ").trim(); }

function hostOf(url) { try { return new URL(url).hostname.toLowerCase(); } catch { return ""; } }

function fileNameOf(url) {
  try { return new URL(url).pathname.split("/").filter(Boolean).pop() || ""; } catch { return String(url || "").split(/[?#]/)[0].split("/").pop() || ""; }
}

// "Libro_de_trabajo-de-La-Vin%CC%83a (1).pdf" → "Libro de trabajo de La Viña"
function prettyFile(name) {
  let b = safeDecode(name || "").normalize("NFC");
  b = b.replace(/\.[A-Za-z0-9]{2,5}$/, "").replace(/\.[A-Za-z0-9]{2,5}$/, ""); // "x.pdf.pdf" happens
  b = b.replace(/\s*\(\d+\)\s*$/, "").replace(/[_+]+/g, " ");
  b = b.replace(/(?<!\d)-+|-+(?!\d)/g, " "); // keep hyphens only between digits (2026-27)
  return squish(b).replace(/^[\s.-]+|[\s.-]+$/g, "");
}

// Cheap guard: a title that is ONLY a generic link text ("Read more", "Descargar aquí").
// The crawler already avoids these; this only protects against old/odd records.
const GENERIC_RE = /^(?:(?:click|haga?\s+clic|pulse)\s+(?:here|aqu[ií])|read\s*more|lee(?:r)?\s*m[aá]s|learn\s*more|more\s*info|m[aá]s\s*info(?:rmaci[oó]n)?|download(?:\s+(?:here|pdf|now))?|descarga(?:r)?(?:\s+(?:aqu[ií]|pdf))?|here|aqu[ií]|pdf|view|ver|open|abrir|link|enlace|file|archivo)$/i;
function isGeneric(t) {
  const s = squish(t).replace(/[.:!…»>→]+$/g, "").trim();
  return !s || s.length < 3 || GENERIC_RE.test(s);
}

// Loose comparable form (accent/case/separator-insensitive).
function fold(s) { return String(s || "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[^a-z0-9]+/g, ""); }

function dehyphen(s) {
  return squish(String(s || "").replace(/[_+]+/g, " ").replace(/(?<!\d)-+|-+(?!\d)/g, " "));
}

function cleanTitle(raw, fileTitle) {
  let t = squish(safeDecode(String(raw || "")).normalize("NFC")).replace(/\.pdf$/i, "").trim();
  // A title that is really a file name ("Manual-RLV-2025", "GV_Catalog_2026") reads better de-hyphenated.
  if (t && fileTitle && fold(t) === fold(fileTitle)) t = fileTitle;
  else if (t && !/\s/.test(t) && /[_-]/.test(t)) t = dehyphen(t);
  // Title-cased ordinals from machine translation: "30Th" → "30th".
  return t.replace(/(\d)(St|Nd|Rd|Th)\b/g, (m, d, s) => d + s.toLowerCase());
}

// Cheap guard on top of the translation pipeline's own guard: a degenerate
// machine translation ("information-information-information…") is worse than the original.
function looksBroken(tr, orig) {
  if (!tr) return true;
  if (tr.length > orig.length * 3 + 20) return true;
  return /(^|[^\p{L}])(\p{L}{3,})(?:[\s\-_,.]+\2){2,}(?!\p{L})/iu.test(tr);
}

// Language markers in DRIVE file names ("Informe_ESPANOL.pdf"). Crawled PDFs carry extra.doc_lang.
function langHint(name) {
  const w = " " + String(name || "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[^a-z]+/g, " ") + " ";
  if (/ (frances|francais|french) /.test(w)) return "fr";
  if (/ (ingles|english) /.test(w)) return "en";
  if (/ (espanol|spanish) /.test(w)) return "es";
  return null;
}

// Calendar day in the Area's time zone (the rest of the site formats dates in Central time):
// an episode published 2026-09-21T04:15Z is "September 20" everywhere.
const DAY_FMT = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" });
function ymd(helpers, v) {
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return v; // already a plain date
  const d = helpers.toDate(v);
  if (!d) return "";
  try { return DAY_FMT.format(d); } catch { return d.toISOString().slice(0, 10); }
}

export function fmtNum(n, lang) {
  const v = Number(n);
  if (!Number.isFinite(v)) return String(n ?? "");
  try { return new Intl.NumberFormat(LOCALES[lang] || "en-US").format(v); } catch { return String(v); }
}

// translateKey for keys that may not exist (new Drive folder names, new nav
// pages, unexpected language codes) — "" instead of the raw key, and never
// throws (translateKey throws for missing keys when I18N_STRICT is set).
function tryKey(helpers, key, lang, vars) {
  try { const s = helpers.translateKey(key, lang, vars); return s === key ? "" : s; } catch { return ""; }
}

function catLabel(helpers, key, lang) {
  const s = tryKey(helpers, "library.cat." + key, lang);
  if (s) return s;
  // Unknown folder / type name → "Budget reports"
  const p = String(key || "").replace(/[-_]+/g, " ").trim();
  return p ? p.charAt(0).toUpperCase() + p.slice(1) : key;
}

/* ------------------------------------------------------------------ */
/*  Cached thumbnails (src/assets/cache/…)                              */
/* ------------------------------------------------------------------ */

// Pixel size from a WebP / PNG / JPEG header → {w, h}, or null (unknown format).
function imageSize(b) {
  const u24 = (i) => b[i] | (b[i + 1] << 8) | (b[i + 2] << 16);
  if (b.length >= 30 && b.toString("latin1", 0, 4) === "RIFF" && b.toString("latin1", 8, 12) === "WEBP") {
    const chunk = b.toString("latin1", 12, 16);
    if (chunk === "VP8 " && b[23] === 0x9d && b[24] === 0x01 && b[25] === 0x2a) return { w: b.readUInt16LE(26) & 0x3fff, h: b.readUInt16LE(28) & 0x3fff };
    if (chunk === "VP8L" && b[20] === 0x2f) { const v = b.readUInt32LE(21); return { w: (v & 0x3fff) + 1, h: ((v >>> 14) & 0x3fff) + 1 }; }
    if (chunk === "VP8X") return { w: u24(24) + 1, h: u24(27) + 1 };
    return null;
  }
  if (b.length >= 24 && b.readUInt32BE(0) === 0x89504e47 && b.toString("latin1", 12, 16) === "IHDR") return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
  if (b.length >= 4 && b[0] === 0xff && b[1] === 0xd8) {
    for (let i = 2; i + 9 < b.length;) {
      if (b[i] !== 0xff) { i++; continue; }
      const m = b[i + 1];
      if (m === 0xff) { i++; continue; }
      if (m === 0xd9 || m === 0xda) break; // end of image / start of scan: no frame header before it
      if (m === 0x01 || (m >= 0xd0 && m <= 0xd8)) { i += 2; continue; } // markers without a length
      if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) return { w: b.readUInt16BE(i + 7), h: b.readUInt16BE(i + 5) };
      i += 2 + b.readUInt16BE(i + 2);
    }
  }
  return null;
}

// Site-relative thumbnail ("/assets/cache/pdf/x.webp") → false when the file is missing
// (no <img> that would 404), else its {w, h} (null if the format is unknown).
// Web links → undefined (not checked). Reset with the document cache on every build.
const thumbSeen = new Map();
function localThumb(p) {
  const s = String(p || "");
  if (!/^\/assets\/[\w\-./]+$/.test(s) || s.includes("..")) return undefined;
  if (!thumbSeen.has(s)) {
    let r = false;
    try {
      const fd = openSync(path.join("src", s), "r");
      try {
        const b = Buffer.alloc(65536);
        const n = readSync(fd, b, 0, b.length, 0);
        r = n > 0 ? imageSize(b.subarray(0, n)) : false;
      } finally { closeSync(fd); }
    } catch { r = false; }
    thumbSeen.set(s, r);
  }
  return thumbSeen.get(s);
}

/* ------------------------------------------------------------------ */
/*  Library documents                                                   */
/* ------------------------------------------------------------------ */

/** Kit ("gvr" | "rlv" | "") and document type of a PDF / Drive item. */
export function docKitType(item) {
  let c = String(item?.category || "other").toLowerCase().trim() || "other";
  c = CAT_ALIASES[c] || c;
  if (!KITS.has(c)) return { kit: "", type: c };
  const tag = (Array.isArray(item.tags) ? item.tags : []).map((x) => CAT_ALIASES[String(x).toLowerCase()] || String(x).toLowerCase()).find((x) => TYPE_TAGS.has(x));
  return { kit: c, type: tag || "rep" };
}

function collectionsFor(doc) {
  let co = "";
  if (doc.kit === "gvr") co += "g";
  if (doc.kit === "rlv") co += "r";
  if (doc.c === "catalog") co += "c";
  if (doc.c === "flyer" || doc.c === "postcard") co += "f";
  if (doc.c === "news") co += "n";
  if (doc.s === "neta" && ["reports", "notes", "minutes", "informes", "actas"].includes(doc.c)) co += "m";
  return co;
}

function normalizeDoc(item, lang, helpers) {
  const ex = item.extra || {};
  const isDrive = item.source === "drive" || item.source === "committee";
  const openUrl = isDrive ? (ex.view_url || item.url) : (ex.file_url || item.url);
  if (!openUrl || !SAFE_URL.test(openUrl)) return null;
  const fileTitle = prettyFile(isDrive ? (item.title || "") : (ex.filename || fileNameOf(openUrl)));
  const refs = Array.isArray(ex.referrers) ? ex.referrers.filter((r) => r && SAFE_URL.test(r.url || "")) : [];

  // --- titles -------------------------------------------------------------
  let orig = cleanTitle(item.title || "", fileTitle);
  let t;
  let fromFile = false; // the title is the file name (its language is the document's, not item.lang)
  let machine = !!(item.machine && item.machine.includes(lang));
  if (isGeneric(orig)) {
    orig = fileTitle || orig; // same text in both languages — a translated file name means nothing
    t = orig;
    fromFile = !!fileTitle;
    machine = false;
  } else {
    t = cleanTitle(helpers.pickLang(item, "title", lang), fileTitle) || orig;
    if (isGeneric(t) || looksBroken(t, orig)) { t = orig; machine = false; }
  }
  if (!t) { t = orig = fileTitle || hostOf(openUrl) || "PDF"; fromFile = true; } // never an empty heading
  if (fold(t) === fold(orig)) machine = false; // "translation" identical to original → not worth a note

  // --- source: where the file was published --------------------------------
  // aa.org PDFs linked from aalavina.org count as La Viña (the page that shares them).
  const host = String(ex.host || hostOf(openUrl)).toLowerCase();
  let pubHost = host;
  if (!isDrive && !OFFICIAL_HOST.test(host)) pubHost = refs.map((r) => hostOf(r.url)).find((h) => OFFICIAL_HOST.test(h)) || host;
  const s = isDrive ? "neta" : /lavina/i.test(pubHost) ? "lv" : "gv";
  const { kit, type: c } = docKitType(item);

  // --- document language (extra.doc_lang; item.lang is the TITLE language) --
  const l = okLang(ex.doc_lang) || (isDrive ? langHint(item.title) : null) || okLang(item.lang) || (s === "lv" ? "es" : "en");
  // Language of the original title ("La Viña Subscription Form (Spanish)" is English
  // text about a Spanish document). Unknown ("und") → the document's language.
  const tl = (!fromFile && okLang(item.lang)) || l;

  // --- dates ----------------------------------------------------------------
  const date = helpers.toDate(item.date);
  const d = date ? ymd(helpers, item.date) : isDrive ? ymd(helpers, item.first_seen) : "";
  // Crawled PDFs often only know their upload month (…/files/2026-02/… → "2026-02-01"): show "February 2026".
  const dp = !isDrive && d && ex.upload_month && d === `${ex.upload_month}-01` ? "m" : "";
  const fs = (helpers.toDate(item.first_seen) || new Date(0)).getTime();

  const kind = item.kind || "pdf";
  const mime = String(ex.mime || "");
  const ft = kind === "slides" || /presentation/.test(mime) ? "slides"
    : kind === "form" || /form/.test(mime) ? "form"
    : /pdf/.test(mime) || kind === "pdf" || /\.pdf($|\?)/i.test(openUrl) ? "pdf"
    : /word|document/.test(mime) ? "doc" : /image/.test(mime) ? "image" : "file";
  let thumb = (isDrive ? (ex.thumb_url || item.image) : (ex.thumb || item.image)) || "";
  if (!SAFE_URL.test(thumb)) thumb = "";
  const shape = thumb ? localThumb(thumb) : undefined;
  if (shape === false) thumb = ""; // cached file missing: show the paper tile, not a broken request
  const ref = refs[0] || null;
  const ev = /^\d{4}-\d{2}-\d{2}$/.test(String(ex.event_date || "")) ? ex.event_date : "";

  const doc = {
    id: item.id,
    k: kind,
    ft,
    s,
    c,
    kit,
    l,
    tl,
    t,
    o: fold(orig) !== fold(t) ? orig : "", // set only when `t` is a translation (else t IS the original, in `tl`)
    m: machine,
    d,
    dp,
    y: d ? Number(d.slice(0, 4)) : null,
    ev,
    u: openUrl,
    dl: isDrive && SAFE_URL.test(ex.download_url || "") ? ex.download_url : "",
    pv: isDrive && SAFE_URL.test(ex.preview_url || "") ? ex.preview_url : "",
    th: thumb,
    // Tall first page → shown whole on a blurred copy of itself (see TALL_PAGE, library.css .lib-th-fit).
    tf: !!(shape && shape.w > 0 && shape.h / shape.w > TALL_PAGE),
    sz: Number(ex.size_bytes) || 0,
    pg: Number(ex.pages) || 0,
    r: ref ? { url: ref.url, title: squish(safeDecode(ref.title || "")) || hostOf(ref.url).replace(/^www\./, "") } : null,
    hx: !isDrive && host && !OFFICIAL_HOST.test(host) ? host.replace(/^www\./, "") : "", // opens on another site (aa.org)
    or: !isDrive && ex.orphan === true, // no page links to it any more
    n: !!item.is_new,
    fname: fileTitle,
    panel: ex.panel_label || "",
    // Undated crawled PDFs sort last ("first found today" says nothing about their age).
    ts: date ? date.getTime() : isDrive ? fs : 0,
    fs,
  };
  doc.co = collectionsFor(doc);
  return doc;
}

const cache = new Map(); // key: lang + items identity → docs (rebuilt every build)
let cacheGen = null;

export function libraryDocs(db, lang, helpers) {
  if (EMPTY() || !db) return [];
  const pdfs = (db.pdfs && db.pdfs.items) || [];
  const drive = (db.drive && db.drive.items) || [];
  if (cacheGen !== pdfs) { cache.clear(); thumbSeen.clear(); cacheGen = pdfs; }
  const ck = lang + ":" + drive.length + ":" + pdfs.length;
  if (cache.has(ck)) return cache.get(ck);

  const out = [];
  const seen = new Set();
  const add = (it) => {
    let d = null;
    try { d = normalizeDoc(it, lang, helpers); } catch (e) { console.warn(`[library] skipped ${it && it.id}: ${e.message}`); }
    if (d && d.id && !seen.has(d.id)) { seen.add(d.id); out.push(d); }
  };
  for (const it of pdfs) {
    if (!it || it.status === "gone") continue;
    add(it);
  }
  for (const it of drive) {
    if (!it || it.status === "gone") continue;
    const cat = String(it.category || "").toLowerCase();
    if (DRIVE_SKIP_KINDS.has(it.kind) || DRIVE_SKIP_CATS.has(cat)) continue;
    if (it.extra && (it.extra.is_image || it.extra.is_video || it.extra.form_closed === true)) continue;
    add(it);
  }
  // Newest first; files no page links to any more go last.
  out.sort((a, b) => (a.or - b.or) || b.ts - a.ts || b.fs - a.fs || a.t.localeCompare(b.t, lang));
  cache.set(ck, out);
  return out;
}

export function libraryFacets(docs, lang, helpers) {
  const count = (key) => { const m = new Map(); for (const d of docs) { const v = d[key]; if (v === null || v === "" || v === undefined) continue; m.set(v, (m.get(v) || 0) + 1); } return m; };
  const src = count("s"), lng = count("l"), cat = count("c"), yr = count("y");
  const langLabel = (k) => tryKey(helpers, "library.lang." + k, lang) || k.toUpperCase();
  return {
    src: SOURCES.filter((x) => src.get(x.key)).map((x) => ({ key: x.key, label: helpers.translateKey("library.src." + x.key, lang), count: src.get(x.key), tone: x.tone })),
    lang: [...LANGS.filter((k) => lng.get(k)), ...[...lng.keys()].filter((k) => !LANGS.includes(k)).sort()]
      .map((k) => ({ key: k, label: langLabel(k), count: lng.get(k) })),
    cat: [...cat.keys()].sort((a, b) => (CAT_ORDER[a] ?? 500) - (CAT_ORDER[b] ?? 500) || a.localeCompare(b))
      .map((k) => ({ key: k, label: catLabel(helpers, k, lang), count: cat.get(k), icon: CAT_ICON[k] || "file-text" })),
    year: [...yr.keys()].sort((a, b) => b - a).map((k) => ({ key: String(k), label: String(k), count: yr.get(k) })),
  };
}

export function libraryCollections(docs, lang, helpers) {
  return COLLECTIONS.map((c) => {
    const count = docs.filter((d) => d.co.includes(c.code)).length;
    return {
      ...c,
      label: helpers.translateKey(`library.col.${c.key}`, lang),
      // An empty collection says what will show up there (instead of just "0").
      desc: (!count && tryKey(helpers, `library.col.${c.key}_empty`, lang)) || helpers.translateKey(`library.col.${c.key}_desc`, lang),
      count,
    };
  });
}

export function libraryStats(docs) {
  const now = Date.now();
  const bySrc = { gv: 0, lv: 0, neta: 0 };
  let recent = 0, pdf = 0;
  for (const d of docs) {
    bySrc[d.s] = (bySrc[d.s] || 0) + 1;
    if (d.ft === "pdf") pdf++;
    if (d.d && !d.or && d.ts >= now - 31 * DAY && d.ts <= now + DAY) recent++;
  }
  return { total: docs.length, pdf, recent, bySrc, sources: Object.values(bySrc).filter(Boolean).length };
}

/* Compact client index: short keys, empty fields omitted, referrers de-duplicated. */
export function libraryIndex(db, lang, helpers) {
  const docs = libraryDocs(db, lang, helpers);
  const refs = [];
  const refIdx = new Map();
  const cats = {};
  const items = docs.map((d) => {
    const o = { id: d.id, t: d.t, s: d.s, c: d.c, l: d.l, u: d.u };
    if (d.tl !== d.l) o.tl = d.tl; // title language, when not the document's (library.js: d.tl || d.l)
    if (d.o) o.o = d.o;
    if (d.m) o.m = 1;
    if (d.d) o.d = d.d;
    if (d.dp) o.dp = d.dp;
    if (d.ev) o.ev = d.ev;
    if (d.th) o.th = d.th;
    if (d.th && d.tf) o.tf = 1;
    if (d.dl) o.dl = d.dl;
    if (d.pv) o.pv = d.pv;
    if (d.sz) o.sz = d.sz;
    if (d.pg) o.pg = d.pg;
    if (d.hx) o.hx = d.hx;
    if (d.or) o.or = 1;
    if (d.n) o.n = 1;
    if (d.k !== "pdf") o.k = d.k;
    if (d.ft !== "pdf") o.ft = d.ft;
    if (d.co) o.co = d.co;
    // Extra search words: the file name, when it adds words the titles don't have.
    if (d.fname && !fold(d.t + d.o).includes(fold(d.fname))) o.x = d.fname;
    if (d.r) {
      const key = d.r.url;
      if (!refIdx.has(key)) { refIdx.set(key, refs.length); refs.push([d.r.url, d.r.title]); }
      o.r = refIdx.get(key);
    }
    if (!(d.c in cats)) cats[d.c] = catLabel(helpers, d.c, lang);
    return o;
  });
  return {
    v: 2,
    lang,
    updated: [db?.pdfs?.updated, db?.drive?.updated].filter(Boolean).sort().pop() || null,
    count: items.length,
    cats,
    refs,
    items,
  };
}

/* ------------------------------------------------------------------ */
/*  Site-wide search index                                              */
/* ------------------------------------------------------------------ */

function snippet(s, n = 170) {
  s = squish(String(s || "").replace(/<[^>]*>/g, " ").replace(/https?:\/\/\S+/g, ""));
  if (s.length <= n) return s;
  return s.slice(0, n).replace(/\s+\S*$/, "").replace(/[,;:.\-–—\s]+$/, "") + "…";
}

// Anchors on the committee pages (#album-…, announcement / event cards) are made by
// eleventy/filters/committee.js. Reuse its helpers so search results land on the
// exact card; if that file ever changes or goes away, fall back to the same rules.
let committee = {};
try { committee = await import("./committee.js"); } catch { /* optional */ }
function slug(s, max = 60) {
  if (typeof committee.slugify === "function") { try { return committee.slugify(s, max); } catch { /* fall back */ } }
  return String(s || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, max).replace(/-+$/, "") || "item";
}

// How many past events /events/ lists (keep in sync with `past | limit(40)` in src/pages/events.njk).
const PAST_EVENTS_SHOWN = 40;

// Icons for site pages that have none in nav.js (footer links).
const PAGE_ICONS = { home: "house", about: "info", digest: "mail", share: "qr-code", search: "search", status: "activity" };

// Language-neutral internal path → path in the page language ("/events/#x" → "/es/events/#x").
const lurl = (u, lang) => (!u || /^(https?:|mailto:|tel:|#)/.test(u) ? u : (lang && lang !== "en" ? `/${lang}` : "") + (u.startsWith("/") ? u : "/" + u));
// Data items link to our own pages with language-neutral paths ("/announcements/#slug").
const isInternal = (u) => typeof u === "string" && /^\/(?!\/)/.test(u);

export function searchIndex(db, nav, lang, helpers, site) {
  const T = (k, vars) => helpers.translateKey(k, lang, vars);
  const P = (it, f) => squish(helpers.pickLang(it, f, lang));
  const both = (k) => [tryKey(helpers, k, "en"), tryKey(helpers, k, "es")].filter(Boolean).join(" ");
  // "October 2026 · Loneliness" — distinct phrases only (accent/case-insensitive).
  const uniq = (list, sep = " · ") => list.filter((v, i, a) => v && a.findIndex((w) => fold(w) === fold(v)) === i).join(sep);
  const out = [];
  const seenId = new Set();
  // `u`: absolute link, or a language-neutral internal path (prefixed here, once).
  // Languages (for lang="" in search.js): `l` = the item's language (badge); `ol` = language
  // of the original title `o` (default `l`); `tl` = language of the shown title `t` when
  // known. With `o` given, a `t` equal to it IS the untranslated original (language `ol`).
  const push = (e) => {
    if (!e.u || !SAFE_URL.test(e.u) || !e.id || seenId.has(e.id) || !squish(e.t)) return; // nothing to link to / no title
    seenId.add(e.id);
    if (e.im && !SAFE_URL.test(e.im)) e.im = "";
    const o = { id: e.id, k: e.k, t: squish(e.t), u: isInternal(e.u) ? lurl(e.u, lang) : e.u };
    const ol = okLang(e.ol) || okLang(e.l);
    let tl = okLang(e.tl);
    if (e.o && fold(e.o) !== fold(e.t)) {
      o.o = squish(e.o);
      if (ol && ol !== okLang(e.l)) o.ol = ol;
    } else if (e.o && !tl) tl = ol;
    if (tl && tl !== lang) o.tl = tl; // an untranslated title in another language
    if (e.s) o.s = e.s;
    if (e.x && e.x.trim()) o.x = squish(e.x);
    if (e.d) o.d = e.d;
    if (e.d && e.dp) o.dp = e.dp;
    if (e.l && e.l !== "und") o.l = e.l;
    if (e.src) o.src = e.src;
    if (e.im) o.im = e.im;
    if (e.yt) o.yt = e.yt;
    if (e.m) o.m = 1;
    if (e.n) o.n = 1;
    if (e.pw) o.pw = 1;
    if (e.z) o.z = 1;
    if (e.ic) o.ic = e.ic;
    out.push(o);
  };
  const mach = (it) => !!(it.machine && it.machine.includes(lang)) && fold(helpers.pickLang(it, "title", lang)) !== fold(it.title);
  const ok = (it) => it && it.status !== "gone";
  const safely = (label, fn) => { try { fn(); } catch (e) { console.warn(`[search-index] ${label}: ${e.message}`); } };

  /* ---- site pages (always present, even with no synced content) ---- */
  const pages = [{ key: "nav.home", url: "/", page: "home", icon: "house" }];
  const walk = (list) => { for (const it of list || []) { if (it.children) walk(it.children); else if (it.url) pages.push(it); } };
  walk(nav && nav.primary);
  walk(nav && nav.footer);
  const seenPage = new Set();
  for (const p of pages) {
    const pk = p.page || p.url.replace(/^\/|\/$/g, "") || "home";
    if (seenPage.has(p.url)) continue;
    seenPage.add(p.url);
    const desc = tryKey(helpers, p.descKey || `search.page_desc.${pk}`, lang) || tryKey(helpers, `search.page_desc.${pk}`, lang);
    push({
      id: "page:" + pk,
      k: "page",
      t: tryKey(helpers, p.key, lang) || p.url,
      tl: lang, // our own UI string, always in the page language
      o: tryKey(helpers, p.key, lang === "es" ? "en" : "es"),
      ol: lang === "es" ? "en" : "es",
      s: desc,
      x: both(`search.kw.${pk}`),
      u: p.url,
      src: "site",
      ic: p.icon || PAGE_ICONS[pk] || "file-text",
    });
  }
  if (EMPTY() || !db) return out;

  /* ---- articles (Grapevine & La Viña) ---- */
  safely("articles", () => {
    for (const it of db.articles?.items || []) {
      if (!ok(it)) continue;
      const ex = it.extra || {};
      const lv = ex.publication === "lv" || it.source === "lavina" || it.category === "lv";
      push({
        id: it.id, k: "article", t: P(it, "title"), o: it.title,
        s: snippet(P(it, "summary")),
        x: uniq([P(it, "issue_label"), P(it, "topic"), P(it, "section"), ...["issue_label", "topic", "section"].flatMap((f) => [it.i18n?.[f]?.en, it.i18n?.[f]?.es]), ex.issue_label, ex.topic, ex.author, lv ? "La Viña" : "Grapevine"].map(squish)),
        u: it.url, d: ymd(helpers, it.date), l: it.lang, src: lv ? "lv" : "gv",
        im: it.image || "", m: mach(it), n: it.is_new, pw: ex.free === false,
      });
    }
  });

  /* ---- editorial themes (calls for stories) — only the ones still open, like /contribute/ ---- */
  safely("editorial", () => {
    const today = new Date().toISOString().slice(0, 10);
    for (const it of db.editorial?.items || []) {
      if (!ok(it)) continue;
      const ex = it.extra || {};
      const deadline = ex.deadline ? ymd(helpers, ex.deadline) : "";
      if (deadline && deadline < today) continue; // closed
      if (!deadline && ex.evergreen !== true && ex.issue_key && String(ex.issue_key) < today.slice(0, 7)) continue; // past issue
      push({
        id: it.id, k: "topic", t: P(it, "title"), o: it.title,
        s: [P(it, "issue_label") || ex.issue_label, deadline ? T("search.deadline", { date: helpers.fmtDate(deadline, lang, "medium") }) : T("search.topic_open")].filter(Boolean).join(" · "),
        x: both("search.kw.contribute"),
        // No `d`: the deadline is in the snippet (a date in the meta line would read as a publish date).
        u: "/contribute/", l: it.lang, src: ex.publication === "lv" || it.category === "lv" ? "lv" : "gv", m: mach(it),
      });
    }
  });

  /* ---- PDFs + committee documents (same titles and types as the Library) ---- */
  safely("library", () => {
    for (const d of libraryDocs(db, lang, helpers)) {
      const kitWords = d.kit ? both(`library.col.${d.kit}-kit`) : "";
      push({
        // d.o is empty when d.t is the original title (language d.tl); d.l is the document's language.
        id: d.id, k: d.k === "pdf" ? "pdf" : d.k, t: d.t, o: d.o, ol: d.tl, tl: d.o ? lang : d.tl,
        s: uniq([d.ev ? T("library.event_on", { date: helpers.fmtDate(d.ev, lang, "medium") }) : "", catLabel(helpers, d.c, lang), d.r && d.r.title]),
        x: [d.fname && !fold(d.t + d.o).includes(fold(d.fname)) ? d.fname : "", kitWords].filter(Boolean).join(" "),
        u: d.u, d: d.d, dp: d.dp, l: d.l, src: d.s, im: d.th && (d.s === "neta" || d.th.startsWith("/")) ? d.th : "", m: d.m, n: d.n && !d.or, z: d.or,
      });
    }
  });

  /* ---- podcast episodes ---- */
  safely("episodes", () => {
    for (const it of db.episodes?.items || []) {
      if (!ok(it)) continue;
      const ex = it.extra || {};
      push({
        id: it.id, k: "episode", t: P(it, "title"), o: it.title,
        s: snippet(P(it, "summary"), 200),
        x: ex.show_name || "",
        u: it.url || ex.player_url || ex.link || "", d: ymd(helpers, it.date), l: it.lang, src: "podcast", m: mach(it), n: it.is_new,
      });
    }
  });

  /* ---- YouTube videos ---- */
  safely("videos", () => {
    for (const it of db.videos?.items || []) {
      if (!ok(it)) continue;
      const ex = it.extra || {};
      push({
        id: it.id, k: "video", t: P(it, "title"), o: it.title,
        s: snippet(P(it, "summary")),
        x: (ex.playlists || []).join(" · "),
        u: it.url, d: ymd(helpers, it.date), dp: ex.date_approx ? "m" : "", l: it.lang, src: it.category === "lv" ? "lv" : "youtube",
        yt: ex.video_id || "", im: ex.video_id ? "" : it.image || "", m: mach(it), n: it.is_new,
      });
    }
  });

  /* ---- Instagram posts (caption is the useful text) ---- */
  safely("instagram", () => {
    for (const it of db.instagram?.items || []) {
      if (!ok(it)) continue;
      const ex = it.extra || {};
      const cap = P(it, "summary");
      const first = cap.split(/(?<=[.!?¡¿])\s|\n/)[0] || "";
      const acct = ex.account === "lv" || it.category === "lv" ? "lv" : "gv";
      // The title is a caption line: in the page language if the caption was translated.
      const shownOrig = first ? fold(cap) === fold(it.summary) : fold(P(it, "title")) === fold(it.title);
      push({
        id: it.id, k: "post",
        t: first ? snippet(first, 90) : P(it, "title"),
        tl: shownOrig ? it.lang : lang,
        o: "",
        s: snippet(cap, 170),
        x: "@" + (ex.username || "") + " instagram",
        u: it.url, d: ymd(helpers, it.date), l: it.lang, src: acct, im: ex.thumb || "", m: mach(it), n: it.is_new,
      });
    }
  });

  /* ---- Committee: Drive photo albums (one entry per album, like /photos/) ---- */
  safely("albums", () => {
    let albums = null;
    if (typeof committee.photoAlbums === "function") {
      try { albums = committee.photoAlbums(db.drive?.items || [], lang); } catch { albums = null; }
    }
    if (albums) {
      for (const a of albums) {
        push({
          id: "album:" + a.slug, k: "album", t: a.title,
          s: [(a.count === 1 ? T("search.photos_one") : T("search.photos_n", { n: fmtNum(a.count, lang) })), a.panelLabel].filter(Boolean).join(" · "),
          x: both("nav.photos"),
          u: "/photos/#" + a.slug, d: ymd(helpers, a.newest), src: "neta",
          im: (a.cover && a.cover[0] && a.cover[0].thumb) || "", n: a.hasNew,
        });
      }
      return;
    }
    const fallback = new Map();
    for (const it of db.drive?.items || []) {
      if (!ok(it)) continue;
      const ex = it.extra || {};
      if (!(it.kind === "photo" || it.kind === "video_file" || ex.is_image || ex.is_video)) continue;
      const name = ex.album || ex.panel_label || "photos";
      if (!fallback.has(name)) fallback.set(name, { name, n: 0, cover: "", date: "", panel: ex.panel_label || "" });
      const a = fallback.get(name);
      a.n++;
      if (!a.cover) a.cover = ex.thumb_url || it.image || "";
      const dd = ymd(helpers, it.date);
      if (dd > a.date) a.date = dd;
    }
    for (const a of fallback.values()) {
      const sl = "album-" + slug(a.name, 50);
      push({
        id: "album:" + sl, k: "album", t: a.name === "photos" ? T("search.album_loose") : a.name,
        s: [(a.n === 1 ? T("search.photos_one") : T("search.photos_n", { n: fmtNum(a.n, lang) })), a.panel].filter(Boolean).join(" · "),
        x: both("nav.photos"),
        u: "/photos/#" + sl, d: a.date, src: "neta", im: a.cover,
      });
    }
  });

  /* ---- Committee: announcements (expired ones are hidden on the page, so skip them) ---- */
  safely("announcements", () => {
    const today = new Date().toISOString().slice(0, 10);
    const list = typeof committee.announcementList === "function"
      ? committee.announcementList(db.announcements?.items || [])
      : (db.announcements?.items || []).filter((it) => ok(it) && !(it.extra?.expires && String(it.extra.expires).slice(0, 10) < today));
    for (const it of list) {
      if (!ok(it)) continue;
      // The data gives "/announcements/#<slug>" (the card's id); older records: the committee page's own anchor.
      const u = isInternal(it.url) && it.url.includes("#") ? it.url
        : "/announcements/#" + (it._anchor || "ann-" + slug(String(it.id).replace(/^ann:/, ""), 70));
      push({
        id: it.id, k: "announcement", t: P(it, "title"), o: it.title,
        s: snippet(P(it, "summary") || P(it, "body_md")),
        u, d: ymd(helpers, it.date), l: it.lang, src: "neta", m: mach(it), n: it.is_new,
      });
    }
  });

  /* ---- Events (monthly committee meetings are covered by the Meeting page) ----
     Upcoming events link to their card on /events/. The "Past events" list there shows
     the newest PAST_EVENTS_SHOWN past events, each row with the same anchor as its card
     (a hand-written event's is its file-name slug), and the list opens by itself when a
     link points into it — so those link to /events/#<anchor> too. Older past events
     link to their own page elsewhere (e.g. the La Viña calendar) or are left out. */
  safely("events", () => {
    const now = Date.now();
    let evs = null;
    let anchored = false; // anchors known = the same ones /events/ renders
    if (typeof committee.normalizeEvents === "function") {
      try { evs = committee.normalizeEvents(db.events?.items || [], site || {}, lang); anchored = true; } catch { evs = null; }
    }
    if (!evs) {
      evs = (db.events?.items || []).filter((it) => ok(it) && it.kind === "event").map((it) => {
        const t0 = helpers.toDate(it.extra?.end || it.extra?.start || it.date);
        return {
          item: it, committee: it.category === "committee", anchor: "ev-" + slug(String(it.id).replace(/^ev:/, "")),
          past: it.extra?.past === true || (!!t0 && t0.getTime() < now - DAY), link: it.url || "", linkExternal: /^https?:/.test(it.url || ""),
        };
      });
    }
    // Same selection as events.njk: `all | where("past", true) | whereNot("committee", true) | reverse | limit(40)`
    // (normalizeEvents sorts soonest first, so the newest past events are the last ones).
    const pastRows = new Set(anchored ? evs.filter((e) => e.past === true && e.committee !== true).slice(-PAST_EVENTS_SHOWN).map((e) => e.id) : []);
    for (const ev of evs) {
      const it = ev.item;
      if (!it || !ok(it) || ev.committee || it.category === "committee") continue;
      const ex = it.extra || {};
      // The card's anchor on /events/. (Without committee.js the anchor is a guess, so the
      // data's own "/events/#<slug>" link wins then.)
      const card = !anchored && isInternal(it.url) && it.url.includes("#") ? it.url : "/events/#" + ev.anchor;
      let u = "";
      if (!ev.past || pastRows.has(ev.id)) u = card;
      else if (/^https?:\/\//.test(it.url || "")) u = it.url;
      if (!u) continue;
      const start = ex.start || it.date;
      push({
        id: it.id, k: "event", t: P(it, "title") || ev.title, o: it.title,
        s: [helpers.fmtDate(start, lang, "medium"), ex.location || [ex.city, ex.state].filter(Boolean).join(", ")].filter(Boolean).join(" · "),
        x: snippet(P(it, "summary"), 120),
        u, d: ymd(helpers, start), l: it.lang,
        src: it.source === "calendar" ? (it.category === "lv-calendar" ? "lv" : "gv") : "neta", m: mach(it), z: ev.past,
      });
    }
  });

  /* ---- Grapevine Weekly Open meeting → its section on our Meeting page (Zoom ID, passcode,
     day and time in both languages; the official page is linked from there) ---- */
  safely("weekly_open", () => {
    for (const it of db.weekly_open?.items || []) {
      if (!ok(it)) continue;
      const ex = it.extra || {};
      const when = P(it, "when") || [P(it, "day"), P(it, "time")].filter(Boolean).join(" · ");
      push({
        id: "weekly-open", k: "meeting", t: P(it, "title"), o: it.title,
        s: [when, ex.zoom_id ? "Zoom " + ex.zoom_id : ""].filter(Boolean).join(" · "),
        x: ["zoom", both("search.kw.weekly_open"), it.i18n?.when?.[lang === "es" ? "en" : "es"]].filter(Boolean).join(" "),
        u: "/meeting/#weekly-open", l: it.lang, src: "gv",
      });
    }
  });
  return out;
}

/* ------------------------------------------------------------------ */
export default function (eleventyConfig, helpers) {
  eleventyConfig.addFilter("libDocs", (db, lang) => libraryDocs(db, lang, helpers));
  eleventyConfig.addFilter("libFacets", (docs, lang) => libraryFacets(docs || [], lang, helpers));
  eleventyConfig.addFilter("libCollections", (docs, lang) => libraryCollections(docs || [], lang, helpers));
  eleventyConfig.addFilter("libStats", (docs) => libraryStats(docs || []));
  eleventyConfig.addFilter("libCatLabel", (key, lang) => catLabel(helpers, key, lang));
  eleventyConfig.addFilter("libCatIcon", (key) => CAT_ICON[key] || "file-text");
  eleventyConfig.addFilter("libCatIconMap", (cats) => Object.fromEntries((cats || []).map((c) => [c.key, c.icon])));
  eleventyConfig.addFilter("libColsConfig", (cols) => (cols || []).map((c) => ({ key: c.key, code: c.code, label: c.label, icon: c.icon, tone: c.tone })));
  eleventyConfig.addFilter("libNum", (n, lang) => fmtNum(n, lang));
  // JSON for <script type="application/json"> blocks: "<" escaped so data can never close the tag.
  eleventyConfig.addFilter("jsonScript", (v) => JSON.stringify(v ?? null).replace(/</g, "\\u003c"));
  eleventyConfig.addFilter("libIndexJson", (db, lang) => JSON.stringify(libraryIndex(db, lang, helpers)));
  const searchCache = new Map(); // one index per language per build (search.njk + search-index.json share it)
  const getSearch = (db, nav, lang, site) => {
    if (!searchCache.has(lang)) searchCache.set(lang, searchIndex(db, nav, lang, helpers, site));
    return searchCache.get(lang);
  };
  eleventyConfig.on("eleventy.before", () => searchCache.clear());
  eleventyConfig.addFilter("searchIndexJson", (db, nav, lang, site) => {
    const items = getSearch(db, nav, lang, site);
    return JSON.stringify({ v: 2, lang, built: new Date().toISOString(), count: items.length, items });
  });
  eleventyConfig.addFilter("searchKindCounts", (db, nav, lang, site) => {
    const counts = {};
    for (const e of getSearch(db, nav, lang, site)) counts[e.k] = (counts[e.k] || 0) + 1;
    return counts;
  });
}
