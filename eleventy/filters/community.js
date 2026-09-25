// Filters for the "community" pages: What's New, Monthly Digest, Share Kit (QR), Status, RSS feeds
// and sitemap. (The district report on /monthly/#report has its own file: report.js.)
//
// Everything here is pure data shaping (no network), so the pages keep
// working with empty data and the build never fails because a source was
// down. Plain-text messages (WhatsApp / e-mail / district report) are built
// here rather than in Nunjucks because exact line breaks matter in them.
import QRCode from "qrcode";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { safeUrl } from "../../eleventy.config.js";
import { ownLangs, chicagoDayEndMs, digestShop, gvMeetings } from "./committee.js";
// The monthly digest reuses the /monthly/ month model (issue theme, "put it to work" tips, weekly open
// meetings, La Viña topics) and the shop's "from $X a month" rule, so every page says the same.
// (monthly.js imports qrSvg from this file: the cycle is safe, both only call each other's functions.)
import { monthModel, nowDate, addMonths, monthLabel } from "./monthly.js";
import { shopFromMonthly, money } from "./shop.js";

const TZ = "America/Chicago";
const LOCALES = { en: "en-US", es: "es-US" };
const DAY = 864e5;

/* ------------------------------------------------------------------ */
/*  Content groups (one per What's New filter chip / digest section)   */
/* ------------------------------------------------------------------ */
// `page` = where "see all" links go; `emoji` = WhatsApp bullet.
export const GROUPS = {
  announcement: { icon: "megaphone", tone: "vine", page: "/announcements/", emoji: "📣" },
  article: { icon: "newspaper", tone: "gv", page: "/read/", emoji: "📰" },
  episode: { icon: "headphones", tone: "grape", page: "/listen/", emoji: "🎧" },
  video: { icon: "circle-play", tone: "grape", page: "/watch/", emoji: "🎬" },
  post: { icon: "instagram", tone: "grape", page: "/instagram/", emoji: "📸" },
  pdf: { icon: "file-text", tone: "gv", page: "/library/", emoji: "📄" },
  drive: { icon: "folder-open", tone: "vine", page: "/portfolio/", emoji: "📁" },
  event: { icon: "calendar-days", tone: "vine", page: "/events/", emoji: "📅" },
  topic: { icon: "pen-line", tone: "lv", page: "/contribute/", emoji: "✍️" },
  other: { icon: "sparkles", tone: "muted", page: "/whats-new/", emoji: "•" },
};
// Filter-chip order on What's New.
export const CHIP_ORDER = ["article", "pdf", "episode", "video", "post", "drive", "announcement", "event", "topic", "other"];

const DRIVE_ICONS = { photo: "image", slides: "presentation", document: "file", video_file: "film", form: "clipboard-list" };

/* ------------------------------------------------------------------ */
/*  Small helpers                                                      */
/* ------------------------------------------------------------------ */
function toDate(v) {
  if (!v) return null;
  if (v instanceof Date) return isNaN(v) ? null : v;
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return new Date(v + "T12:00:00Z");
  const d = new Date(v);
  return isNaN(d) ? null : d;
}
const ms = (v) => { const d = toDate(v); return d ? d.getTime() : 0; };
const isDateOnly = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);

/** YYYY-MM-DD of a moment in Central time (used for day grouping). */
export function ymdChicago(v) {
  if (isDateOnly(v)) return v;
  const d = toDate(v);
  if (!d) return "";
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

function fmt(v, lang, opts) {
  const d = toDate(v);
  if (!d) return "";
  let s = new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { timeZone: TZ, ...opts }).format(d);
  if (lang === "es") s = esMeridiem(s.charAt(0).toUpperCase() + s.slice(1));
  return s;
}
/** Intl's Spanish "7:00 p.m." → "7:00 p. m.", the style the rest of the site uses
 *  (build_data's Weekly Open time, committee.js). */
export const esMeridiem = (s) => String(s).replace(/\b([ap])\.\s?m\./g, "$1. m.").replace(/(\d) (?=[ap]\. m\.)/g, "$1 ");
const fmtShortDay = (v, lang) => fmt(v, lang, { weekday: "short", month: "short", day: "numeric" });
const fmtShortDayYear = (v, lang) => fmt(v, lang, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
// Inside a sentence ("fecha límite: jue, 1 de oct"): Spanish keeps the weekday lower-case.
const fmtShortDayMid = (v, lang) => { const s = fmtShortDay(v, lang); return lang === "es" ? s.charAt(0).toLowerCase() + s.slice(1) : s; };
const fmtDay = (v, lang) => fmt(v, lang, { month: "short", day: "numeric", year: "numeric" });
const fmtTime = (v, lang) => fmt(v, lang, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });

/** "Oct 17" – "Oct 23, 2026" style range. */
function fmtRange(a, b, lang) {
  const sameYear = toDate(a)?.getUTCFullYear() === toDate(b)?.getUTCFullYear();
  const first = fmt(a, lang, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
  return `${first} – ${fmtDay(b, lang)}`;
}

/**
 * Localize a magazine issue label: "October 2026" ⇄ "Octubre 2026",
 * "Septiembre-Octubre 2026" ⇄ "September / October 2026" (same style as the
 * pipeline's rule-built labels). Labels that don't look like
 * "<month>[-/<month>] <year>" are returned unchanged.
 */
const MONTHS = {
  en: ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"],
  es: ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
};
export function issueLabel(label, lang) {
  const s = clean(label);
  const m = s.match(/^([A-Za-zÁÉÍÓÚáéíóúñÑ]+)(?:\s*[-–\/]\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+))?\s+(?:de\s+)?(\d{4})$/);
  if (!m) return s;
  const idx = (w) => {
    const x = w.toLowerCase().replace("setiembre", "septiembre");
    const i = MONTHS.en.indexOf(x);
    return i !== -1 ? i : MONTHS.es.indexOf(x);
  };
  const a = idx(m[1]);
  const b = m[2] ? idx(m[2]) : null;
  if (a === -1 || b === -1) return s;
  const name = (i) => { const n = (MONTHS[lang] || MONTHS.en)[i]; return n.charAt(0).toUpperCase() + n.slice(1); };
  return `${name(a)}${b !== null ? " / " + name(b) : ""} ${m[3]}`;
}

/**
 * Issue label of an article / editorial theme in the page language. The data
 * pipeline writes `i18n.issue_label` by rule ("October 2026" ⇄ "Octubre 2026",
 * "September / October 2026" ⇄ "Septiembre / Octubre 2026") — that wins; the
 * local rule above is the fallback for older data.
 */
export function issueLabelOf(item, lang) {
  const i = item?.i18n?.issue_label;
  if (i && i[lang]) return clean(i[lang]);
  return issueLabel(item?.extra?.issue_label || "", lang);
}

/**
 * The same label inside a sentence: Spanish months are lower-case there and
 * take "de" before the year ("la edición de octubre de 2026").
 */
export function issueInSentence(label, lang) {
  const s = clean(label);
  if (lang !== "es") return s;
  const m = s.match(/^(.*?)\s+(?:de\s+)?(\d{4})$/);
  if (!m) return s;
  const months = m[1].split(/\s*\/\s*/).map((w) => (MONTHS.es.includes(w.toLowerCase()) ? w.toLowerCase() : w));
  return `${months.join("/")} de ${m[2]}`;
}

function absUrl(url, site) {
  if (!url) return "";
  if (/^(https?:|mailto:|tel:)/.test(url)) return url;
  const b = String(site?.url || "").replace(/\/$/, "");
  return b + (url.startsWith("/") ? url : "/" + url);
}
const langPath = (url, lang) => (lang && lang !== "en" && url.startsWith("/") ? `/${lang}${url}` : url);
const clean = (s) => String(s ?? "").replace(/\s+/g, " ").trim();

/**
 * The moment an item became "new" for timeline purposes.
 * Items from whatsnew.json carry `wn_date`, computed by build_data.py with the
 * full rules (launch-day back catalog is not news, undated PDFs are not news,
 * future-dated magazine issues count from the day they appeared …) — that
 * always wins. The fallback below is only for items from other files:
 * magazine articles carry their ISSUE date (e.g. Oct 1) but appear on the web
 * weeks earlier, and events carry their event date — so a date in the future
 * falls back to when our robot first saw the item.
 */
export function whenOf(item, now = Date.now()) {
  if (!item) return null;
  if (item.wn_date) return item.wn_date;
  if (item.kind === "event") return item.first_seen || item.date || null;
  const d = ms(item.date);
  if (d && d <= now + DAY) return item.date;
  if (item.first_seen && ms(item.first_seen) <= now + DAY) return item.first_seen;
  return item.date || item.first_seen || null;
}

/** Which What's New group (filter chip) an item belongs to. */
export function groupOf(item) {
  const k = item?.kind;
  if (k === "announcement" || k === "event" || k === "article" || k === "episode" || k === "post" || k === "topic") return k;
  if (k === "video") return item.source === "drive" ? "drive" : "video";
  if (k === "pdf") return item.source === "drive" ? "drive" : "pdf";
  if (item?.source === "drive" || ["document", "slides", "photo", "video_file", "form"].includes(k)) return "drive";
  return "other";
}

/** Color family for an item's icon bubble: gv | lv | grape | vine | muted. */
function toneOf(item, group) {
  const host = item?.extra?.host || "";
  if (item?.source === "lavina" || item?.category === "lv" || host.includes("lavina") || item?.extra?.publication === "lv") {
    if (group === "article" || group === "pdf" || group === "topic") return "lv";
  }
  return (GROUPS[group] || GROUPS.other).tone;
}

/**
 * Teaser without the title repeated: Instagram titles are the caption's first
 * sentence and the summary is the whole caption, so the same words would show
 * twice. Returns the rest of the summary ("" when nothing is left).
 */
export function teaser(summary, title) {
  const s = clean(summary);
  const truncated = /(\.\.\.|…)$/.test(clean(title));
  const t = clean(title).replace(/\s*(\.\.\.|…)$/, "");
  if (!s || !t) return s;
  if (s.toLowerCase() === t.toLowerCase()) return "";
  if (t.length < 12 || !s.toLowerCase().startsWith(t.toLowerCase())) return s;
  const rest = s.slice(t.length).replace(/^[\s,;:]+/, "");
  if (!rest) return "";
  return truncated ? "…" + rest : rest;
}

/**
 * Image for a SMALL list thumbnail (What's New: 48–128 px wide boxes).
 *  - YouTube's 480×360 "hqdefault" (4:3 with black bars) → the 320×180 "mqdefault" (16:9).
 *  - A cached image "/assets/cache/<dir>/<name>.webp" → "<name>.sm.webp" next to it when the
 *    daily sync wrote one (a ~160 px wide copy); otherwise the 480 px original, unchanged.
 */
const smallThumbSeen = new Map();
export function listThumb(src) {
  const u = String(src || "");
  if (!u) return "";
  const yt = u.match(/^(https:\/\/i\d?\.ytimg\.com\/vi(?:_webp)?\/[\w-]+\/)(?:hq|sd|maxres)default(\.jpg|\.webp)$/);
  if (yt) return `${yt[1]}mqdefault${yt[2]}`;
  const m = u.match(/^\/assets\/cache\/([\w-]+\/[\w-]+)\.webp$/);
  if (!m) return u;
  if (!smallThumbSeen.has(m[1])) smallThumbSeen.set(m[1], fs.existsSync(path.join("src", "assets", "cache", `${m[1]}.sm.webp`)));
  return smallThumbSeen.get(m[1]) ? `/assets/cache/${m[1]}.sm.webp` : u;
}

/** Link target for an item (internal paths get the language prefix). */
export function hrefOf(item, lang) {
  const u = item?.url || "";
  if (!u) return "";
  return u.startsWith("/") ? langPath(u, lang) : u;
}

const isMediaItem = (item) => item?.kind === "episode" || item?.kind === "video";

function pickLang(item, field, lang) {
  if (!item) return "";
  const i = item.i18n && item.i18n[field];
  if (i && (i[lang] || i[lang] === "")) return i[lang] || i[item.lang] || item[field] || "";
  if (i && i.en) return i.en;
  return item[field] ?? "";
}

/** Shallow copy of an item plus the display helpers templates need. */
function prep(item, now = Date.now()) {
  const group = groupOf(item);
  const ex = item.extra || {};
  const isGroup = !!ex.is_group;
  return {
    ...item,
    _when: whenOf(item, now),
    _group: group,
    _tone: toneOf(item, group),
    _icon: isGroup ? "images" : group === "drive" ? DRIVE_ICONS[item.kind] || "folder-open" : (GROUPS[group] || GROUPS.other).icon,
    _ext: /^https?:/.test(item.url || ""),
    // YouTube dates that are only approximate (old uploads) show month + year, never a time.
    _approx: !!ex.date_approx,
    _hasTime: !!item.date && !isDateOnly(item.date) && !ex.date_approx && item.kind !== "event" && ms(item.date) <= now + DAY,
    // A PDF's `lang` is the language of its TITLE; the document itself may differ.
    _docLang: item.kind === "pdf" && ex.doc_lang ? ex.doc_lang : item.lang,
    // Languages the committee wrote it in by hand too (content/events title_es …): no "Original in …" pill there.
    _own: ownLangs(item),
    _isGroup: isGroup,
  };
}

function eventStart(ev) {
  return ev?.extra?.start || ev?.date || null;
}

/** A date of a monthly event from config/site.yml `recurring_events:` (e.g. the booth at CityWide Dallas). */
const isRecurring = (ev) => ev?.category === "recurring";

/** Only the first (soonest) date of each recurring event: one line for the booth, not one per month. */
function nextOfEachSeries(events) {
  const seen = new Set();
  return events.filter((e) => {
    if (!isRecurring(e)) return true;
    const k = String(e.extra?.series || e.id);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/** The calendar day an event ends on (Central time): its `end` day, else its start day. */
function eventLastDay(ev) {
  const x = ev?.extra || {};
  const s = eventStart(ev);
  const last = x.end ? ymdChicago(isDateOnly(x.end) ? x.end : new Date(ms(x.end) - 1)) : ymdChicago(s);
  const first = ymdChicago(s);
  return last && last > first ? last : first;
}

/** An event over several days (an Area assembly, Fri–Sun) — not a timed one that only runs past midnight. */
function isMultiDay(ev) {
  const x = ev?.extra || {};
  const s = eventStart(ev);
  if (!s || eventLastDay(ev) === ymdChicago(s)) return false;
  return !!(x.all_day || isDateOnly(s)) || ms(x.end) - ms(s) > 18 * 3600e3;
}

/** When an event is over: midnight Central after its last day for an all-day event (it stays
 *  "coming up" all of that day); a timed event at its end (no end: 6 hours after it starts). */
function eventEndMs(ev) {
  const x = ev?.extra || {};
  const s = eventStart(ev);
  if (x.end && !isDateOnly(x.end)) return ms(x.end);
  if (x.end || x.all_day || isDateOnly(s)) return chicagoDayEndMs(eventLastDay(ev));
  return ms(s) + 6 * 3600e3;
}

/** Does an event's date need its year? When it ends in another year than `now`, or more than about
 *  six months ahead ("Fri, Sep 17 – Sun, Sep 19, 2027" next to this September's dates on What's New). */
const YEAR_AFTER_DAYS = 183;
function needsYear(lastYmd, now) {
  if (!lastYmd) return false;
  return lastYmd.slice(0, 4) !== ymdChicago(new Date(now)).slice(0, 4) || ms(lastYmd) - now > YEAR_AFTER_DAYS * DAY;
}

/** "Sat, Oct 17", "Wed, Oct 21 · 7:00 PM CDT" — or, over several days, "Fri, Mar 19 – Sun, Mar 21".
 *  With the year when it is another year or far ahead: "Fri, Sep 17 – Sun, Sep 19, 2027" /
 *  "Vie, 17 de sept – dom, 19 de sept de 2027". */
export function eventWhen(ev, lang, now = Date.now()) {
  const s = eventStart(ev);
  if (!s) return "";
  const allDay = ev?.extra?.all_day || isDateOnly(s);
  const multi = isMultiDay(ev);
  const firstYmd = ymdChicago(s);
  const lastYmd = multi ? eventLastDay(ev) : firstYmd;
  const withYear = needsYear(lastYmd, now);
  // the first day carries the year only when the range crosses into another year (Dec 31 – Jan 2)
  const day1 = withYear && (!multi || firstYmd.slice(0, 4) !== lastYmd.slice(0, 4)) ? fmtShortDayYear(s, lang) : fmtShortDay(s, lang);
  const first = allDay ? day1 : `${day1} · ${fmtTime(s, lang)}`;
  if (!multi) return first;
  const last = withYear ? fmtShortDayYear(lastYmd, lang) : fmtShortDay(lastYmd, lang);
  return `${first} – ${lang === "es" ? last.charAt(0).toLowerCase() + last.slice(1) : last}`;
}

/** The event's place in the page language (content/events `location_es` → i18n.location), else as written. */
export function eventWhere(ev, lang) {
  const x = ev?.extra || {};
  return clean((ev?.i18n?.location && ev.i18n.location[lang]) || x.location || x.city || "");
}

/** The day(s) on an event's small date box: "17", or "19–21" over several days. */
function eventDayBox(ev, lang) {
  const s = eventStart(ev);
  if (!s) return "";
  const day = fmt(isDateOnly(s) ? s : ymdChicago(s), lang, { day: "numeric" });
  return isMultiDay(ev) ? `${day}–${fmt(eventLastDay(ev), lang, { day: "numeric" })}` : day;
}

/** The month line of that date box: "Sep", or "Apr–May" when the event ends in another month
 *  (the tiles on /events/, home and announcements do the same — homeEventInfo). */
function eventMonthBox(ev, lang) {
  const s = eventStart(ev);
  if (!s) return "";
  const mon = (v) => new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { timeZone: TZ, month: "short" }).format(toDate(v)).replace(/\./g, "");
  const first = mon(ymdChicago(s));
  if (!isMultiDay(ev)) return first;
  const last = mon(eventLastDay(ev));
  return last !== first ? `${first}–${last}` : first;
}

/* ------------------------------------------------------------------ */
/*  QR code → inline SVG (synchronous; qrcode.create is the public API) */
/* ------------------------------------------------------------------ */
export function qrSvg(text, { label = "", cls = "", margin = 2, ecl = "M" } = {}) {
  if (!text) return "";
  let qr;
  try {
    qr = QRCode.create(String(text), { errorCorrectionLevel: ecl });
  } catch (e) {
    console.warn(`[community] QR failed for ${text}: ${e.message}`);
    return "";
  }
  const size = qr.modules.size;
  const full = size + margin * 2;
  // One path of horizontal runs keeps the SVG small (~3–6 KB) and crisp.
  let d = "";
  for (let r = 0; r < size; r++) {
    let c = 0;
    while (c < size) {
      if (qr.modules.get(r, c)) {
        let run = 1;
        while (c + run < size && qr.modules.get(r, c + run)) run++;
        d += `M${c + margin} ${r + margin}h${run}v1h-${run}z`;
        c += run;
      } else c++;
    }
  }
  const a11y = label ? `role="img" aria-label="${escapeXml(label)}"` : `aria-hidden="true"`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${full} ${full}" width="${full * 8}" height="${full * 8}" shape-rendering="crispEdges" class="${cls}" ${a11y}><rect width="${full}" height="${full}" fill="#ffffff"/><path fill="#000000" d="${d}"/></svg>`;
}

/** Same QR as a reusable <symbol> (referenced with <use href="#id">) — keeps
 *  pages that show one code many times (poster previews) small. */
export function qrSymbol(text, id, margin = 2) {
  const svg = qrSvg(text, { margin });
  const m = svg.match(/viewBox="([^"]+)"[^>]*>(.*)<\/svg>$/s);
  return m ? `<symbol id="${id}" viewBox="${m[1]}">${m[2]}</symbol>` : "";
}

export function escapeXml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&apos;")
    // strip characters that are illegal in XML 1.0
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, "");
}

/* ------------------------------------------------------------------ */
/*  What's New                                                         */
/* ------------------------------------------------------------------ */
/** Items ready for the timeline / feed: prepared, "gone" removed, newest first. */
export function prepareWhatsNew(items, now = Date.now()) {
  return (items || [])
    .filter((i) => i && i.status !== "gone")
    .map((i) => prep(i, now))
    .sort((a, b) => ms(b._when) - ms(a._when));
}

/**
 * A podcast episode and its YouTube upload come out on the same day with the same title
 * ("Gated Communities [Season 11, Episode 12]") or the same season/episode numbers. On the
 * What's New page they are ONE entry: the episode, carrying the video as `_twin` (both badges,
 * "Listen · Watch" links, the video's thumbnail). The day, chip and hero counts are computed
 * from this merged list, so they match what is shown. (The RSS feed keeps both items.)
 */
export function mergeMediaTwins(prepared) {
  const list = prepared || [];
  const norm = (s) => clean(s).toLowerCase();
  const se = (i) => (i.extra && i.extra.season != null && i.extra.episode != null ? `${i.extra.season}|${i.extra.episode}` : "");
  const eps = list.filter((i) => i.kind === "episode");
  if (!eps.length) return list;
  const twinOf = new Map(); // video → episode
  const taken = new Set();
  for (const v of list) {
    if (v.kind !== "video" || v.source === "drive") continue;
    const day = ymdChicago(v._when);
    const t = norm(v.title), k = se(v);
    const ep = eps.find((e) => !taken.has(e) && ymdChicago(e._when) === day && ((t && norm(e.title) === t) || (k && se(e) === k)));
    if (ep) { twinOf.set(v, ep); taken.add(ep); }
  }
  if (!twinOf.size) return list;
  const merged = new Map([...twinOf].map(([v, e]) => [e, { ...e, _twin: v }]));
  return list.filter((i) => !twinOf.has(i)).map((i) => merged.get(i) || i);
}

/** Group prepared items by Central-time day, with per-kind counts and ranks. */
export function whatsNewDays(prepared, lang) {
  const days = new Map();
  for (const it of prepared || []) {
    const ymd = ymdChicago(it._when) || "undated";
    if (!days.has(ymd)) days.set(ymd, []);
    days.get(ymd).push(it);
  }
  return [...days.entries()].map(([ymd, items]) => {
    const entries = bundleIssues(items);
    // counts/ranks are per ENTRY (a bundle is one entry) — the page script
    // uses them for filtering and "show N more"; itemTotal is for display.
    const counts = {};
    const ranked = entries.map((it, i) => {
      const r = counts[it._group] || 0;
      counts[it._group] = r + 1;
      return { ...it, _rankAll: i, _rankKind: r };
    });
    const itemCounts = {};
    for (const it of items) itemCounts[it._group] = (itemCounts[it._group] || 0) + 1;
    return {
      ymd,
      label: ymd === "undated" ? "" : fmt(ymd, lang, { weekday: "long", month: "long", day: "numeric", year: "numeric" }),
      items: ranked,
      counts,
      itemCounts,
      total: entries.length,
      itemTotal: items.length,
    };
  });
}

/**
 * When a magazine issue lands, dozens of articles appear on the same day.
 * Fold 4+ articles of the same publication + issue into ONE timeline entry
 * ("27 new stories in the October 2026 issue") placed where the first was.
 */
const BUNDLE_MIN = 4;
function bundleIssues(items) {
  const keyOf = (it) => (it.kind === "article" && it.extra?.issue_key ? `${it.extra.publication || it.source}|${it.extra.issue_key}` : null);
  const groups = new Map();
  for (const it of items) { const k = keyOf(it); if (k) { if (!groups.has(k)) groups.set(k, []); groups.get(k).push(it); } }
  const out = [];
  const done = new Set();
  for (const it of items) {
    const k = keyOf(it);
    const g = k && groups.get(k);
    if (!g || g.length < BUNDLE_MIN) { out.push(it); continue; }
    if (done.has(k)) continue;
    done.add(k);
    out.push({
      ...g[0],
      id: `bundle:${k}`,
      _bundle: true,
      _items: g,
    });
  }
  return out;
}

function chipList(prepared) {
  const counts = {};
  for (const it of prepared || []) counts[it._group] = (counts[it._group] || 0) + 1;
  return CHIP_ORDER.filter((k) => counts[k]).map((k) => ({ key: k, count: counts[k], icon: GROUPS[k].icon }));
}

function countRecent(prepared, days, now = Date.now()) {
  return (prepared || []).filter((i) => { const t = ms(i._when); return t && now - t <= days * DAY && t <= now + DAY; }).length;
}

/* ------------------------------------------------------------------ */
/*  Published writers (spotlight) — Area 65 first, then the rest of Texas */
/* ------------------------------------------------------------------ */
// data/site/spotlight.json (docs/DATA_SCHEMA.md → "spotlight.json"): Grapevine / La Viña
// stories with a byline, each with extra.geo.scope (neta65 | texas | other | unknown) and
// extra.pub_date (the day the story counts as published). Templates get it as db.spotlight;
// while src/_data/db.js does not list "spotlight" yet, it is read here with the same link
// cleaning as db.js (safeUrl on every link).
const YMD = /^\d{4}-\d{2}-\d{2}$/;
const ANONYMOUS = /^\s*(anonymous|an[oó]nim[oa]|anon\.?)\s*$/i;
let spotlightFile; // undefined = not read yet in this build (reset on "eleventy.before")

function readSpotlightFile() {
  try {
    const data = JSON.parse(fs.readFileSync(path.join("data", "site", "spotlight.json"), "utf8"));
    if (!data || !Array.isArray(data.items)) return null;
    for (const it of data.items) {
      if (!it || typeof it !== "object") continue;
      it.url = safeUrl(it.url);
      if (typeof it.image === "string") it.image = safeUrl(it.image);
      if (it.extra && typeof it.extra.issue_url === "string") it.extra.issue_url = safeUrl(it.extra.issue_url);
    }
    return data;
  } catch {
    return null;
  }
}

export function spotlightOf(db) {
  const s = db?.spotlight;
  if (s && Array.isArray(s.items)) return s;
  if (spotlightFile === undefined) spotlightFile = readSpotlightFile();
  return spotlightFile || { items: [] };
}

/** "2026-09-23" minus 60 days → "2026-07-25" (calendar days, no time-zone drift). */
function minusDays(ymd, n) {
  const d = new Date(ymd + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

/** The home page's window (spotlight.home_days, 60 by default) — /published/ opens with it. */
export function spotlightHomeDays(spot) {
  const n = Number(spot?.home_days);
  return Number.isInteger(n) && n > 0 ? n : 60;
}

/**
 * Stories by writers from Area 65 (`neta65`) and from the rest of Texas (`texas`) published
 * in the last `days` days: extra.pub_date ≥ today (Central time) − days, the same rule as the
 * home page and /published/. Each list newest first (then by title).
 * opts.exclusiveStart: leave out the day `days` days back, so the window is exactly `days`
 * calendar days (today and the days − 1 before it). `since` is the first day included.
 * opts.since / opts.until ("YYYY-MM-DD", both included): an exact range of days instead — the
 * monthly digest passes the previous calendar month, so two editions never list the same story.
 */
export function writersPick(spot, days, now = Date.now(), opts = {}) {
  const today = ymdChicago(new Date(now));
  const since = YMD.test(opts.since || "") ? opts.since : minusDays(today, opts.exclusiveStart ? days - 1 : days);
  const until = YMD.test(opts.until || "") ? opts.until : "";
  const out = { days, since, until, today, allDays: spotlightHomeDays(spot), neta65: [], texas: [], total: 0 };
  const seen = new Set();
  for (const it of spot?.items || []) {
    if (!it || typeof it !== "object" || it.status === "gone" || it.kind !== "article" || !it.url) continue;
    const scope = it.extra?.geo?.scope;
    if (scope !== "neta65" && scope !== "texas") continue;
    const pd = it.extra?.pub_date;
    if (!YMD.test(pd || "") || pd < since || (until && pd > until)) continue;
    const key = it.id || it.url;
    if (seen.has(key)) continue;
    seen.add(key);
    out[scope].push(it);
  }
  const order = (a, b) => (a.extra.pub_date < b.extra.pub_date ? 1 : a.extra.pub_date > b.extra.pub_date ? -1 : 0) || clean(a.title).localeCompare(clean(b.title));
  out.neta65.sort(order);
  out.texas.sort(order);
  out.total = out.neta65.length + out.texas.length;
  return out;
}

/** "Victor R." — or "Anonymous" / "Anónimo" when the magazine printed no name. */
export function writerName(item, lang, t) {
  const a = clean(item?.extra?.author);
  return !a || ANONYMOUS.test(a) ? t("community.writers.anonymous", lang) : a;
}

/** "Grand Prairie, Texas" in the page language (written by rules in build_data, never machine-translated). */
export function writerPlace(item, lang) {
  const g = item?.extra?.geo || {};
  return clean(lang === "es" ? g.label_es : g.label_en) || clean(g.label_en) || clean(pickLang(item, "author_location", lang)) || clean(item?.extra?.author_location);
}

const pubName = (item) => (item?.extra?.publication === "lv" || item?.source === "lavina" || item?.category === "lv" ? "La Viña" : "Grapevine");

/* ------------------------------------------------------------------ */
/*  Monthly digest (/digest/ — its e-mail twin is scripts/notify/send_digest.py) */
/* ------------------------------------------------------------------ */
// One EDITION per calendar month (America/Chicago). Edition "2026-10" goes out on October 1 and
// stays on /digest/ all of October:
//   * what was new in the PREVIOUS month (September): the What's New entries whose news date
//     (wn_date) falls in it, completed from the full episode / video / document / announcement
//     lists, because whatsnew.json only keeps its newest 150 entries (about one busy month);
//     a YouTube upload of a podcast episode is folded into the episode (mergeMediaTwins, as on
//     What's New); Instagram is one pointer line (its feed only keeps the newest posts);
//   * this month's magazine issues: count, a few highlights (free to read first, then members'
//     stories, Area 65 and Texas writers first) and the "put it to work" tips of the /monthly/ page;
//   * stories by writers from Area 65 / Texas published last month (spotlight, extra.pub_date);
//   * what is coming up THIS month (events not over yet, the weekly open meetings, the Grapevine
//     meetings count), story deadlines through the end of NEXT month, the phone story lines,
//     Book of the Month, the cheapest subscription and a pointer to the daily quote.
// send_digest.py applies the same rules: keep the two in step (docs/OPERATIONS.md → monthly-digest.yml).

const pad2 = (n) => String(n).padStart(2, "0");
/** "2026-09" → "2026-09-30" */
const monthLastDay = (key) => { const [y, m] = key.split("-").map(Number); return `${key}-${pad2(new Date(Date.UTC(y, m, 0)).getUTCDate())}`; };
/** "September" / "septiembre" (the month alone; Spanish months are lower-case inside a sentence) */
export function monthWord(key, lang) {
  const [y, m] = String(key).split("-").map(Number);
  if (!y || !m) return "";
  return new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, 15)));
}

/**
 * The edition of `now` (its Central-time month) or of an explicit "YYYY-MM": the month itself
 * (key, first, last), the previous month the news comes from (prev, prevFirst, prevLast) and the
 * next month (next, nextLast — story deadlines run through it).
 */
export function digestEdition(now = nowDate(), edition = "") {
  const key = /^\d{4}-(0[1-9]|1[0-2])$/.test(edition || "") ? edition : ymdChicago(new Date(now)).slice(0, 7);
  const prev = addMonths(key, -1);
  const next = addMonths(key, 1);
  return {
    key, first: `${key}-01`, last: monthLastDay(key),
    prev, prevFirst: `${prev}-01`, prevLast: monthLastDay(prev),
    next, nextLast: monthLastDay(next),
  };
}

// What the edition counts as news (What's New groups); Instagram and events have their own lines.
const MONTH_NEWS = ["announcement", "article", "episode", "video", "pdf", "drive"];
// Full source lists read on top of whatsnew.json (a dated item only: its date is its news date,
// the same rule as build_data's effective_ts; undated or future-dated items count only from What's New).
const MONTH_SOURCES = ["episodes", "videos", "pdfs", "announcements"];

/** The previous month's news, newest first, podcast/video twins folded ({ key: [items] }). */
export function monthNews(db, ed, now = Date.now()) {
  const found = new Map();
  const add = (raw, fromWhatsNew) => {
    if (!raw || !raw.id || raw.status === "gone" || found.has(raw.id)) return;
    if (!fromWhatsNew && !(isDateOnly(raw.date) || (typeof raw.date === "string" && ms(raw.date)))) return;
    const it = fromWhatsNew ? prep(raw, now) : { ...prep(raw, now), _when: raw.date };
    if (!MONTH_NEWS.includes(it._group)) return;
    const t = ms(it._when);
    if (!t || t > now + DAY) return;
    const ymd = ymdChicago(it._when);
    if (ymd < ed.prevFirst || ymd > ed.prevLast) return;
    if (it.kind === "announcement" && it.extra?.expires && ms(it.extra.expires) + DAY < now) return;
    found.set(raw.id, it);
  };
  for (const i of db?.whatsnew?.items || []) add(i, true);
  for (const name of MONTH_SOURCES) for (const i of db?.[name]?.items || []) add(i, false);
  const list = mergeMediaTwins([...found.values()].sort((a, b) => ms(b._when) - ms(a._when) || String(a.id).localeCompare(String(b.id))));
  const out = {};
  for (const k of MONTH_NEWS) out[k] = list.filter((i) => i._group === k);
  // pinned announcements first
  out.announcement.sort((a, b) => (b.extra?.pinned === true) - (a.extra?.pinned === true));
  return out;
}

const EVERY_ISSUE_RE = /in every issue|en cada (?:edici[oó]n|n[uú]mero)/i;
const isDepartment = (a) => a?.extra?.department === true || EVERY_ISSUE_RE.test(String(a?.extra?.section || ""));
const scopeRank = (a) => ({ neta65: 0, texas: 1 })[a?.extra?.geo?.scope] ?? 2;

/**
 * The magazine issues on the stands in the edition's month: Grapevine's issue of that month and
 * La Viña's bimonthly issue (key = the month, or the month before) — each from articles.json
 * (the latest synced issue gets its cover and official page from `issues`; the theme of this
 * month's Grapevine issue comes from the editorial calendar, like /monthly/).
 * highlights: `n` stories — free to read first, then members' stories (not "In Every Issue"),
 * Area 65 and Texas writers first, then in the magazine's own order.
 */
export function monthIssues(db, ed, mm = {}, n = 3) {
  const arts = (db?.articles?.items || []).filter((a) => a && a.kind === "article" && a.status !== "gone" && a.url && a.extra?.issue_key);
  const pubOf = (a) => a.extra.publication || a.category;
  const out = [];
  for (const pub of ["gv", "lv"]) {
    const key = [ed.key, addMonths(ed.key, -1)].find((k) => arts.some((a) => pubOf(a) === pub && a.extra.issue_key === k));
    if (!key) continue;
    const list = arts.filter((a) => pubOf(a) === pub && a.extra.issue_key === key);
    const meta = (db?.articles?.issues || []).find((i) => i && i.publication === pub && i.key === key) || null;
    const first = list[0];
    const pick = (l) => {
      const fromCalendar = pub === "gv" && key === ed.key ? clean(mm[l]?.gv?.theme) : "";
      return fromCalendar || clean(meta?.i18n?.theme?.[l] || first.i18n?.issue_theme?.[l] || meta?.theme || first.extra.issue_theme || first.extra.topic);
    };
    const label = (l) => clean(meta?.i18n?.label?.[l] || first.i18n?.issue_label?.[l]) || issueLabel(first.extra.issue_label || meta?.label || key, l);
    const ranked = list.map((a, i) => ({ a, i }))
      .sort((x, y) => (x.a.extra.free === true ? 0 : 1) - (y.a.extra.free === true ? 0 : 1)
        || isDepartment(x.a) - isDepartment(y.a) || scopeRank(x.a) - scopeRank(y.a) || x.i - y.i)
      .map((x) => x.a);
    out.push({
      pub, key, isLv: pub === "lv", name: pub === "lv" ? "La Viña" : "Grapevine",
      label: { en: label("en"), es: label("es") },
      theme: { en: pick("en"), es: pick("es") },
      themeMachine: pub === "gv" && key === ed.key ? !!mm.es?.gv?.machine : false,
      url: meta?.url || first.extra.issue_url || "",
      cover: meta?.cover || "",
      count: list.length,
      free: list.filter((a) => a.extra.free === true).length,
      highlights: ranked.slice(0, n),
    });
  }
  return out;
}

/** Events of the edition's month that are not over yet (the committee meeting has its own box;
 *  a monthly series — the CityWide booth — once), soonest first. */
function monthEvents(db, ed, now) {
  return nextOfEachSeries((db?.events?.items || [])
    .filter((e) => e && e.status !== "gone" && e.category !== "committee" && eventStart(e))
    .filter((e) => { const first = ymdChicago(eventStart(e)); return first <= ed.last && eventLastDay(e) >= ed.first && eventEndMs(e) >= now; })
    .sort((a, b) => ms(eventStart(a)) - ms(eventStart(b))))
    .map((e) => ({ ...prep(e, now), _recurring: isRecurring(e), _tentative: e.extra?.tentative === true }));
}

/**
 * Everything the monthly edition shows (the /digest/ page, its WhatsApp / e-mail texts).
 * opts: carry (config/carry.yml), site, now, edition ("YYYY-MM"), highlights, perSection.
 */
export function buildMonthlyDigest(db, meeting, opts = {}) {
  const now = opts.now instanceof Date ? opts.now.getTime() : Number(opts.now) || nowDate().getTime();
  const site = opts.site || {};
  const cfg = site.digest || {};
  const intOr = (v, d) => (Number.isInteger(Number(v)) && Number(v) > 0 ? Number(v) : d);
  const highlights = intOr(opts.highlights ?? cfg.highlights, 3);
  const perSection = intOr(opts.perSection ?? cfg.per_section, 5);
  const ed = digestEdition(now, opts.edition);
  const today = ymdChicago(new Date(now));
  const mm = {
    en: monthModel(ed.key, db || {}, opts.carry || {}, site, "en", new Date(now)),
    es: monthModel(ed.key, db || {}, opts.carry || {}, site, "es", new Date(now)),
  };

  const news = monthNews(db, ed, now);
  const counts = Object.fromEntries(MONTH_NEWS.map((k) => [k, news[k].length]));
  const total = MONTH_NEWS.reduce((n, k) => n + counts[k], 0);

  const deadlines = (db?.editorial?.items || [])
    .filter((e) => e && e.status !== "gone" && e.extra?.deadline && e.extra.deadline >= today && e.extra.deadline <= ed.nextLast)
    .sort((a, b) => a.extra.deadline.localeCompare(b.extra.deadline) || clean(a.title).localeCompare(clean(b.title)));

  const gvm = (L) => { const g = gvMeetings(db?.meetings, L, site); return { inArea: g.inArea, nearby: g.nearby }; };
  const ap = db?.audio_project || {};
  const line = (d) => (d && d.phone && d.tel ? { phone: String(d.phone), tel: String(d.tel) } : null);
  const igProfiles = db?.instagram?.profiles || {};
  const ig = ["gv", "lv"].map((k) => igProfiles[k]).filter((p) => p && (p.username || p.url))
    .map((p) => ({ username: String(p.username || "").replace(/^@/, ""), url: p.url || (p.username ? `https://www.instagram.com/${String(p.username).replace(/^@/, "")}/` : "") }));
  // What's New entries since the edition came out (the page's "since" pointer, never in the texts)
  const since = (db?.whatsnew?.items || []).filter((i) => i && i.status !== "gone" && i.wn_date && ymdChicago(i.wn_date) >= ed.first && ms(i.wn_date) <= now + DAY).length;
  const from = shopFromMonthly(db?.shop);

  return {
    edition: ed,
    until: new Date(now).toISOString(),
    today,
    highlights,
    perSection,
    news,
    counts,
    total,
    issues: monthIssues(db, ed, mm, highlights),
    // "put it to work": the tips of this month's Grapevine issue (config/carry.yml), at most 3
    tips: { en: (mm.en.tips || []).slice(0, 3), es: (mm.es.tips || []).slice(0, 3) },
    toolkit: { path: `/monthly/${ed.key}/`, label: { en: monthLabel(ed.key, "en"), es: monthLabel(ed.key, "es") } },
    writers: writersPick(spotlightOf(db), 0, now, { since: ed.prevFirst, until: ed.prevLast }),
    events: monthEvents(db, ed, now),
    weekly: { en: mm.en.weekly || [], es: mm.es.weekly || [] },
    gvm: { en: gvm("en"), es: gvm("es") },
    deadlines,
    lvTopics: { en: mm.en.lvTopics || [], es: mm.es.lvTopics || [] },
    audio: { gv: line(ap.gv), lv: line(ap.lv) },
    shop: { en: digestShop(db?.shop, "en", new Date(now)), es: digestShop(db?.shop, "es", new Date(now)) },
    subsFrom: Number.isFinite(from) && from > 0 ? from : null,
    quote: (db?.quote?.items || []).some((q) => q && q.text),
    instagram: ig,
    since,
    next: meeting?.next || null,
  };
}

/** "13 meetings every week in our Area, plus 10 in nearby areas" (md.gvm[lang]). */
export function gvmText(g, lang, t) {
  if (!g || !g.inArea) return "";
  const ours = t(g.inArea === 1 ? "community.digest.n_meetings_one" : "community.digest.n_meetings", lang, { n: g.inArea });
  return t(g.nearby ? "community.digest.gvm_text" : "community.digest.gvm_text_area", lang, { ours, nearby: g.nearby });
}

/** "9 podcast episodes, 2 videos and 8 documents" (zero counts left out), in `lang`. */
export function digestCountList(md, lang, t) {
  const parts = ["article", "episode", "video", "pdf", "drive", "announcement"]
    .filter((k) => md.counts[k] > 0)
    .map((k) => t(`community.digest.n_${k}${md.counts[k] === 1 ? "_one" : ""}`, lang, { n: md.counts[k] }));
  if (parts.length < 2) return parts[0] || "";
  return `${parts.slice(0, -1).join(", ")} ${t("community.digest.and", lang)} ${parts[parts.length - 1]}`;
}

/** The edition's one-sentence intro ("In September: …. Here's what's coming up in October."). */
export function digestIntro(md, lang, t) {
  const vars = { prev: monthWord(md.edition.prev, lang), month: monthWord(md.edition.key, lang) };
  const list = digestCountList(md, lang, t);
  return list ? t("community.digest.intro", lang, { ...vars, list }) : t("community.digest.intro_quiet", lang, vars);
}

/**
 * Plain-text monthly edition for WhatsApp ("whatsapp") or e-mail ("email").
 * `langs` = ["en"], ["es"] or ["en","es"] (bilingual: both languages in one message).
 * `media` = the shared podcast/video title helpers of eleventy/filters/media.js
 * ({ title, cleanTitle, videoKind } — see mediaHelpers below), so episodes and
 * videos read as on Home, Listen and Watch (no "[Season 11, Episode 12]" tail).
 */
export function monthlyDigestText(md, langs, style, site, t, media = {}) {
  const L = Array.isArray(langs) ? langs : [langs];
  const wa = style === "whatsapp";
  const main = L[0];
  const uniq = (arr) => arr.filter((v, i, a) => v && a.indexOf(v) === i);
  // vars: an object, or a function of the language (month names differ by language)
  const both = (key, vars) => uniq(L.map((l) => t(key, l, typeof vars === "function" ? vars(l) : vars))).join(" / ");
  const head = (s, emoji) => (wa ? `${emoji} *${s}*` : `${s.toUpperCase()}\n${"-".repeat(Math.min(s.length, 60))}`);
  const bullet = wa ? "•" : "-";
  const per = wa ? 3 : md.perSection;
  const url = (p) => absUrl(langPath(p, main), site);
  const ed = md.edition;
  const until = Date.parse(md.until) || Date.now();
  const titleOf = (item, l) => {
    const raw = clean(pickLang(item, "title", l));
    if (!isMediaItem(item) || !media.title) return raw;
    // A Weekly Open recording's display title drops the show name on the web pages, which show
    // the show next to it; a text message has no such label, so only the numbering tail goes.
    if (media.cleanTitle && media.videoKind && media.videoKind(item) === "weekly") return clean(media.cleanTitle(raw)) || raw;
    return clean(media.title(item, l)) || raw;
  };
  const titleLines = (item) => { const ts = uniq(L.map((l) => titleOf(item, l))); return ts.length ? ts : [clean(item.title)]; };
  const more = (n, page) => `${wa ? "➕" : "+"} ${both("community.digest.text_more", { n })} ${url(page)}`;
  const out = [];

  out.push(wa ? `*NETA 65 Grapevine / La Viña — ${both("community.digest.masthead")}*` : `NETA 65 Grapevine / La Viña — ${both("community.digest.masthead")}`);
  const edLine = `${both("community.digest.edition", (l) => ({ month: monthLabel(ed.key, l) }))} · ${both("community.digest.edition_sub", (l) => ({ prev: monthWord(ed.prev, l), month: monthWord(ed.key, l) }))}`;
  out.push(wa ? `_${edLine}_` : edLine);
  out.push("");
  for (const l of L) out.push(digestIntro(md, l, t));
  out.push("");

  // Next committee meeting
  if (md.next) {
    const when = uniq(L.map((l) => `${fmtShortDay(md.next.start, l)} · ${fmtTime(md.next.start, l)}`)).join(" / ");
    out.push(head(both("community.digest.next_meeting"), "🗓️"));
    out.push(`${when} — Zoom`);
    out.push(both("community.digest.text_all_welcome"));
    out.push(url("/meetings/"));
    out.push("");
  }

  // Announcements
  const ann = md.news.announcement;
  if (ann.length) {
    out.push(head(`${both("community.group.announcement")} (${ann.length})`, "📣"));
    for (const a of ann.slice(0, per)) {
      const [first, ...others] = titleLines(a);
      out.push(`${bullet} ${first}`);
      for (const r of others) out.push(`  ${r}`);
      out.push(`  ${absUrl(hrefOf(a, main), site)}`);
    }
    if (ann.length > per) out.push(more(ann.length - per, "/announcements/"));
    out.push("");
  }

  // This month in the magazines + put it to work
  const issues = [...md.issues].sort((a, b) => (a.isLv === (main === "es") ? -1 : 0) - (b.isLv === (main === "es") ? -1 : 0));
  if (issues.length) {
    out.push(head(both("community.digest.issues_title"), "📖"));
    for (const iss of issues) {
      const theme = uniq(L.map((l) => iss.theme[l])).map((x) => `“${x}”`).join(" / ");
      out.push(`${bullet} ${iss.name} — ${iss.label[main]}${theme ? `: ${theme}` : ""} (${both(iss.count === 1 ? "community.digest.n_stories_one" : "community.digest.n_stories", { n: iss.count })})`);
      for (const a of iss.highlights.slice(0, per)) {
        const [first, ...others] = titleLines(a);
        out.push(`  “${first}”${a.extra?.free === true ? ` — ${both("community.digest.free")}` : ""}`);
        for (const r of others) out.push(`  “${r}”`);
        out.push(`  ${a.url}`);
      }
      out.push(`  ${both("community.digest.issue_more", { n: iss.count })}: ${url("/read/")}`);
    }
    out.push("");
  }
  const tips = md.tips[main] || [];
  if (tips.length) {
    out.push(head(both("monthly.put_to_work"), "💡"));
    tips.forEach((tip, i) => {
      out.push(`${bullet} ${tip.title}: ${tip.text}`);
      for (const l of L.slice(1)) { const o = md.tips[l]?.[i]; if (o && o.text !== tip.text) out.push(`  ${o.title}: ${o.text}`); }
    });
  }
  out.push(`${wa ? "🖼️ " : ""}${both("community.digest.monthly", (l) => ({ month: md.toolkit.label[l] }))}: ${url(md.toolkit.path)}`);
  out.push("");

  // Writers from Area 65 (first) and the rest of Texas, published last month
  const W = md.writers;
  if (W && W.total) {
    out.push(head(`${both("community.writers.digest_title")} (${W.total})`, "⭐"));
    const pubUrl = url("/published/");
    for (const key of ["neta65", "texas"]) {
      const list = W[key] || [];
      if (!list.length) continue;
      out.push(`${both(`community.writers.group_${key}`)}:`);
      for (const item of list.slice(0, per)) {
        const [first, ...others] = titleLines(item);
        const place = writerPlace(item, main);
        out.push(`${bullet} "${first}" — ${writerName(item, main, t)}${place ? `, ${place}` : ""} (${pubName(item)}, ${issueInSentence(issueLabelOf(item, main), main)})`);
        for (const r of others) out.push(`  "${r}"`);
        out.push(`  ${absUrl(item.url, site)}`);
      }
      if (list.length > per) out.push(`${wa ? "➕" : "+"} ${both("community.digest.text_more", { n: list.length - per })} ${pubUrl}`);
    }
    out.push("");
  }

  // Listen & watch, the new documents and committee files
  const lists = [["episode", "🎧"], ["video", "🎬"], ["pdf", "📄"], ["drive", "📁"]];
  for (const [key, emoji] of lists) {
    const items = md.news[key];
    if (!items.length) continue;
    out.push(head(`${both(`community.group.${key}`)} (${items.length})`, emoji));
    for (const item of items.slice(0, per)) {
      const [first, ...others] = titleLines(item);
      out.push(`${bullet} ${first}`);
      for (const r of others) out.push(`  ${r}`);
      out.push(`  ${absUrl(hrefOf(item, main), site)}`);
    }
    if (items.length > per) out.push(more(items.length - per, GROUPS[key].page));
    out.push("");
  }
  if (md.instagram.length) {
    out.push(`${wa ? "📸 " : ""}${both("community.digest.ig_line")}: ${md.instagram.map((p) => `@${p.username}`).join(" · ")} — ${url("/instagram/")}`);
    out.push("");
  }
  if (!md.total && !(W && W.total)) {
    out.push(both("community.digest.text_quiet", (l) => ({ prev: monthWord(ed.prev, l) })));
    out.push("");
  }

  // Coming up this month
  out.push(head(both("community.digest.coming_month", (l) => ({ month: monthWord(ed.key, l) })), "📅"));
  if (md.events.length) {
    for (const ev of md.events) {
      const [first, ...rest] = titleLines(ev);
      const where = eventWhere(ev, main);
      const monthly = ev._recurring ? ` · ${both("community.digest.every_month")}` : "";
      const tbc = ev._tentative ? ` · ${both("committee.events.tentative")}` : "";
      out.push(`${bullet} ${eventWhen(ev, main, until)}${monthly}${tbc} — ${first}${where ? ` (${where})` : ""}`);
      for (const r of rest) out.push(`  ${r}`);
      if (ev.url) out.push(`  ${absUrl(hrefOf(ev, main), site)}`);
    }
  } else {
    out.push(both("community.digest.no_events", (l) => ({ month: monthWord(ed.key, l) })));
  }
  const weekly = md.weekly[main] || [];
  weekly.forEach((w, i) => {
    const starts = w.startsLabel ? ` (${t("monthly.weekly_from", main, { date: w.startsLabel })})` : "";
    out.push(`${bullet} ${both("community.digest.every_week")}: ${w.title} — ${w.when}${starts}`);
    for (const l of L.slice(1)) { const o = md.weekly[l]?.[i]; if (o && (o.title !== w.title || o.when !== w.when)) out.push(`  ${o.title} — ${o.when}`); }
  });
  if (weekly.length) out.push(`  ${url("/meetings/#weekly-open")}`);
  const g = md.gvm[main];
  if (g && g.inArea) {
    out.push(`${wa ? "📍" : bullet} ${both("community.digest.gvm_title")}: ${gvmText(g, main, t)}`);
    out.push(`  ${url("/meetings/#grapevine-meetings")}`);
  }
  out.push(`${both("community.digest.events_link")}: ${url("/events/")}`);
  out.push("");

  // Share your story: deadlines through next month, La Viña's open topics, the phone story lines
  const lvTopics = md.lvTopics[main] || [];
  const phones = [["Grapevine", md.audio.gv], ["La Viña", md.audio.lv]].filter(([, d]) => d);
  if (md.deadlines.length || lvTopics.length || phones.length) {
    out.push(head(both("community.digest.deadlines"), "✍️"));
    for (const d of md.deadlines) {
      const pub = d.extra?.publication === "lv" ? "La Viña" : "Grapevine";
      const theme = uniq(L.map((l) => clean(pickLang(d, "title", l)))).join(" / ");
      out.push(`${bullet} ${fmtShortDay(d.extra.deadline, main)} — "${theme}" (${pub}, ${issueInSentence(issueLabelOf(d, main), main)})`);
    }
    // La Viña publishes in Spanish: an English message gives the Spanish topic too
    if (lvTopics.length) out.push(`${bullet} ${both("community.digest.text_lv_anytime")} ${lvTopics.map((x) => `"${x.text}"${x.es && x.es !== x.text ? ` ("${x.es}")` : ""}`).join(", ")}`);
    if (phones.length) out.push(`${wa ? "🎙️" : bullet} ${both("community.rec.title")}: ${phones.map(([n, d]) => `${n} ${d.phone}`).join(" · ")}`);
    out.push(`  ${url("/contribute/")}`);
    out.push("");
  }

  // Book of the Month (the prices and dates have ONE home: /shop/#botm) + the cheapest subscription
  const sh = md.shop[main];
  if (sh && sh.offers.length) {
    const label = sh.pct ? both("community.digest.botm_title", { pct: sh.pct }) : both("community.digest.botm_title_plain");
    out.push(head(label, "📚"));
    for (const o of sh.offers) {
      // the title it is sold under first, then the translations as a second line
      const titles = uniq([o.raw.title, ...L.map((l) => o.raw.i18n?.title?.[l])]);
      const price = o.price ? t("community.digest.botm_price", main, { sale: o.sale, price: o.price }) : o.sale;
      const ends = o.endsLabel ? ` · ${t("community.digest.botm_ends", main, { date: o.endsLabel })}` : "";
      out.push(`${bullet} "${titles[0] || o.title}" (${o.pubName}) — ${price}${ends}`);
      for (const r of titles.slice(1)) out.push(`  "${r}"`);
      out.push(`  ${o.url}`);
    }
    out.push(`${both("community.digest.botm_more")}: ${url("/shop/")}#botm`);
  }
  if (md.subsFrom) out.push(`${wa ? "📬 " : ""}${both("shop.subs_from", (l) => ({ amount: money(md.subsFrom, l) }))}: ${url("/shop/")}#subscriptions`);
  if ((sh && sh.offers.length) || md.subsFrom) out.push("");

  if (md.quote) {
    out.push(`${wa ? "💬 " : ""}${both("community.digest.quote_line")}: ${url("/")}`);
    out.push("");
  }

  out.push(`${wa ? "🌐 " : ""}${both("community.digest.text_footer")}`);
  for (const l of L) out.push(absUrl(langPath("/whats-new/", l), site));
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
}

function uniqByTitle(items, lang) {
  const seen = new Set();
  return (items || []).filter((it) => {
    const k = clean(pickLang(it, "title", lang)).toLowerCase();
    if (!k || seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/* ------------------------------------------------------------------ */
/*  Status dashboard                                                   */
/* ------------------------------------------------------------------ */
// Sources whose count is legitimately 0 until the committee adds something
// get a friendly "how to fill this" hint instead of a bare 0.
const EMPTY_HINTS = { drive: "drive_empty", announcements: "ann_empty", manual_events: "events_empty", events_external: "ext_empty" };

const num = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));

export function statusView(status, now = Date.now()) {
  const sources = (status?.sources || []).map((s) => {
    const updated = s.updated || null;
    const age = updated ? now - ms(updated) : null;
    // ok: true = last run fine · false = last run failed (older data kept) · null/absent = never ran
    let state = "never";
    if (s.ok === false) state = "failed";
    else if (updated) state = "ok";
    const count = Number(s.count) || 0;
    const panel = Array.isArray(s.stats?.panels) && s.stats.panels.length ? String(s.stats.panels[0]) : "";
    return {
      ...s,
      state,
      count,
      stale: state === "ok" && age !== null && age > 3 * DAY,
      ageDays: age === null ? null : Math.floor(age / DAY),
      attempted: s.attempted || updated,
      hint: state === "ok" && count === 0 && EMPTY_HINTS[s.source] ? EMPTY_HINTS[s.source] : "",
      panel,
    };
  });
  const c = status?.crawl || {};
  const tr = status?.translations || {};
  // Optional outside calendars (config/site.yml sources.ics_feeds) — build_data's status.json `feeds`.
  // Kept apart from the content sources: a feed blocked by a site's bot protection is an extra that
  // does not work, not a source that "failed" (it is not in the counts above).
  const FEED_STATES = new Set(["ok", "blocked", "error", "never"]);
  const feeds = (Array.isArray(status?.feeds) ? status.feeds : []).filter((f) => f && f.url).map((f) => {
    let host = "";
    try { host = new URL(f.url).hostname.replace(/^www\./, ""); } catch { /* not a URL */ }
    const state = FEED_STATES.has(f.state) ? f.state : "never";
    return {
      ...f,
      state,
      host,
      events: Math.max(0, Number(f.events_count) || 0),
      dups: Math.max(0, Number(f.duplicates) || 0),
      http: f.http_status ? String(f.http_status) : "",
    };
  });
  const totalItems = sources.reduce((a, s) => a + s.count, 0);
  const found7d = sources.reduce((a, s) => a + (Number(s.new_7d) || 0), 0);
  return {
    generated: status?.generated || null,
    sources,
    feeds,
    okCount: sources.filter((s) => s.state === "ok").length,
    failedCount: sources.filter((s) => s.state === "failed").length,
    totalItems,
    found7d,
    // Everything tracked was first found in the last 7 days (the site's first week): the page
    // says so, so the big number is not read as "1,240 new things this week". From the second
    // week on this is false by itself — no date to update by hand.
    allFound7d: totalItems > 0 && found7d >= totalItems,
    // The Status page's library panel: document facts only (curated entries, build_data.py).
    crawl: {
      pdfs: num(c.pdfs) || 0,
      thumbs: num(c.pdfs_with_thumbs) || 0,
      updated: c.updated || null,
      hasData: (num(c.pdfs) || 0) > 0,
    },
    translations: {
      cached: num(tr.cached) || 0,
      pending: num(tr.pending) || 0,
      rejected: num(tr.rejected_by_guard) || 0,
      glossary: num(tr.glossary_entries) || 0,
    },
  };
}

/**
 * 0–100 → "1.6%" / "42%" / "99.9%" (es: "1,6 %"). Whole numbers from 10 up, but a value
 * short of 100 never rounds up to "100%" (it keeps one decimal, at most 99.9: 99.88 and
 * 99.97 both read "99.9%"), and a tiny non-zero value never shows as "0%".
 */
export function cmPct(n, lang) {
  let v = Number(n) || 0;
  if (v > 0 && v < 0.1) v = 0.1;
  let digits = v >= 10 ? 0 : 1;
  if (v < 100 && v >= 99.5) { digits = 1; v = Math.min(99.9, Math.round(v * 10) / 10); }
  return new Intl.NumberFormat(LOCALES[lang] || "en-US", { style: "percent", maximumFractionDigits: digits }).format(v / 100);
}

/* ------------------------------------------------------------------ */
/*  Icon sprite (What's New repeats the same few icons ~300 times)      */
/* ------------------------------------------------------------------ */
// Same icon sources as the shared {% icon %} shortcode: src/_includes/icons
// first, then Lucide. Each icon becomes ONE <symbol> per page; entries then
// reference it with a tiny <svg><use href="#cmi-name"/></svg>.
const require = createRequire(import.meta.url);
let LUCIDE_DIR = null;
try { LUCIDE_DIR = path.join(path.dirname(require.resolve("lucide-static/package.json")), "icons"); } catch { /* not installed */ }
const symbolCache = new Map();
const SYMBOL_ATTRS = ["viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"];

export function iconSymbol(name) {
  if (symbolCache.has(name)) return symbolCache.get(name);
  const local = path.join("src/_includes/icons", `${name}.svg`);
  const file = fs.existsSync(local) ? local : LUCIDE_DIR ? path.join(LUCIDE_DIR, `${name}.svg`) : "";
  let out = "";
  if (file && fs.existsSync(file)) {
    const svg = fs.readFileSync(file, "utf8").replace(/<!--.*?-->/gs, "");
    const open = (svg.match(/<svg\b[^>]*>/) || [""])[0];
    const attrs = SYMBOL_ATTRS.map((a) => { const m = open.match(new RegExp(`\\s${a}="([^"]*)"`)); return m ? ` ${a}="${m[1]}"` : ""; }).join("");
    const inner = svg.slice(svg.indexOf(open) + open.length, svg.lastIndexOf("</svg>")).replace(/>\s+</g, "><").replace(/\s+/g, " ").trim();
    out = `<symbol id="cmi-${name}"${attrs.includes("viewBox") ? "" : ' viewBox="0 0 24 24"'}${attrs}>${inner}</symbol>`;
  } else {
    console.warn(`[community] missing icon for sprite: ${name}`);
  }
  symbolCache.set(name, out);
  return out;
}

/** Hidden sprite with every icon in `names` (duplicates ignored). */
export function iconSprite(names) {
  const list = [...new Set((names || []).filter(Boolean))];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" class="absolute" aria-hidden="true" focusable="false"><defs>${list.map(iconSymbol).join("")}</defs></svg>`;
}

/** Reference to a sprite icon — same classes/a11y as the {% icon %} shortcode. */
export function iconUse(name, cls = "size-5") {
  return `<svg class="icon ${cls}" aria-hidden="true" focusable="false"><use href="#cmi-${name}"/></svg>`;
}

// Every icon the What's New timeline can reference.
export const WN_ICONS = [
  ...new Set([
    ...Object.values(GROUPS).map((g) => g.icon), ...Object.values(DRIVE_ICONS),
    "images", "book-open", "headphones", "youtube", "instagram", "users", "file-text", "grapes", "languages", "lock",
  ]),
];

/* ------------------------------------------------------------------ */
/*  Eleventy registration                                              */
/* ------------------------------------------------------------------ */
export default function (eleventyConfig, helpers) {
  const t = (key, lang, vars) => helpers.translateKey(key, lang, vars);
  // spotlight.json is read at most once per build (only while db.js does not provide it)
  eleventyConfig.on("eleventy.before", () => { spotlightFile = undefined; smallThumbSeen.clear(); });

  // Filters are prefixed "cm" (community) so they can never clash with another
  // area's filters; qrSvg keeps its plain name. QR code as inline SVG:
  //   {{ url | qrSvg("Label", "classes") | safe }}
  eleventyConfig.addFilter("qrSvg", (text, label = "", cls = "") => qrSvg(text, { label, cls }));
  eleventyConfig.addFilter("cmQrSymbol", (text, id) => qrSymbol(text, id));
  // Same SVG as a data: URI (download link). Encoded so it is safe in href="".
  eleventyConfig.addFilter("cmSvgDataUri", (svg) => "data:image/svg+xml;charset=utf-8," + encodeURIComponent(String(svg || "")));

  eleventyConfig.addFilter("cmWnPrepare", (items) => mergeMediaTwins(prepareWhatsNew(items)));
  eleventyConfig.addFilter("cmWnDays", (prepared, lang) => whatsNewDays(prepared, lang));
  eleventyConfig.addFilter("cmWnChips", (prepared) => chipList(prepared));
  eleventyConfig.addFilter("cmWnCountRecent", (prepared, days = 7) => countRecent(prepared, days));
  eleventyConfig.addFilter("cmHref", (item, lang) => hrefOf(item, lang));
  eleventyConfig.addFilter("cmTeaser", (summary, title) => teaser(summary, title));
  eleventyConfig.addFilter("cmListThumb", (src) => listThumb(src));
  eleventyConfig.addFilter("cmEventWhen", (ev, lang) => eventWhen(ev, lang));
  eleventyConfig.addFilter("cmEventWhere", (ev, lang) => eventWhere(ev, lang));
  eleventyConfig.addFilter("cmEventDayBox", (ev, lang) => eventDayBox(ev, lang));
  eleventyConfig.addFilter("cmEventMonthBox", (ev, lang) => eventMonthBox(ev, lang));
  eleventyConfig.addFilter("cmDateRange", (a, b, lang) => fmtRange(a, b, lang));
  eleventyConfig.addFilter("cmIssueLabel", (label, lang) => issueLabel(label, lang));

  // Monthly digest (/digest/): {% set md = db | cmMonthlyDigest(meeting, carry, site) %}. MONTHLY_NOW
  // (the /monthly/ test clock) also moves the edition: MONTHLY_NOW=2026-10-01 builds the October one.
  eleventyConfig.addFilter("cmMonthlyDigest", (db, meeting, carry, site) => buildMonthlyDigest(db, meeting, { carry, site }));
  eleventyConfig.addFilter("cmDigestIntro", (md, lang) => digestIntro(md, lang, t));
  eleventyConfig.addFilter("cmMonthWord", (key, lang) => monthWord(key, lang));
  eleventyConfig.addFilter("cmGvmText", (g, lang) => gvmText(g, lang, t));
  // The media filters (media.js) register after this file (alphabetical load order),
  // so they are looked up when the digest renders, not now. Missing → raw titles.
  const mediaHelpers = () => ({
    title: eleventyConfig.getFilter("mediaTitle"),
    cleanTitle: eleventyConfig.getFilter("mediaCleanTitle"),
    videoKind: eleventyConfig.getFilter("mediaVideoKind"),
  });
  eleventyConfig.addFilter("cmDigestText", (md, langs, style, site) => monthlyDigestText(md, langs, style, site, t, mediaHelpers()));

  // Published writers (Area 65 first, then the rest of Texas): the digest's list is md.writers;
  // these print one writer's byline the same way everywhere.
  eleventyConfig.addFilter("cmWriterName", (item, lang) => writerName(item, lang, t));
  eleventyConfig.addFilter("cmWriterPlace", (item, lang) => writerPlace(item, lang));


  eleventyConfig.addFilter("cmStatus", (status) => statusView(status));
  // Drop items whose title (in `lang`) repeats an earlier one — scraped section
  // pages sometimes share a generic title ("Read"), which looks broken in lists.
  eleventyConfig.addFilter("cmUniqTitle", (items, lang) => uniqByTitle(items, lang));
  // Articles grouped by magazine issue (publication + issue key — GV September
  // and LV September/October share the key "2026-09"), in first-seen order.
  eleventyConfig.addFilter("cmByIssue", (items) => {
    const m = new Map();
    for (const it of items || []) {
      const k = it?.extra?.issue_key ? `${it.extra.publication || it.source}|${it.extra.issue_key}` : "other";
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(it);
    }
    return [...m.entries()].map(([key, list]) => ({ key, items: list }));
  });

  // mailto: with subject AND body (the shared `mailto` filter only takes a subject)
  eleventyConfig.addFilter("cmMailto", (email, subject = "", body = "") => {
    const q = [subject && "subject=" + encodeURIComponent(subject), body && "body=" + encodeURIComponent(body)].filter(Boolean).join("&");
    return `mailto:${email || ""}${q ? "?" + q : ""}`;
  });
  // "https://x.github.io/Repo/es/" → "x.github.io/Repo/es"
  eleventyConfig.addFilter("cmShortUrl", (u) => String(u || "").replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, ""));
  // `cmWebcal` (https:// → webcal://) used by the community pages is registered in
  // committee.js; both files are auto-loaded, so it is available here too.
  eleventyConfig.addFilter("cmNum", (n, lang) => new Intl.NumberFormat(LOCALES[lang] || "en-US").format(Number(n) || 0));
  // 0–100 → "1.6%" / "1,6 %" (≥ 10 without decimals; a tiny non-zero value never shows as 0,
  // and anything short of 100 never rounds up to "100%": 99.9 → "99.9%", 99.97 → "99.9%")
  eleventyConfig.addFilter("cmPct", (n, lang) => cmPct(n, lang));

  // UI string with a fallback when the key does not exist (safe with I18N_STRICT=1):
  //   {{ ("community.status.src." + s.source) | cmTOr(lang, s.label) }}
  eleventyConfig.addFilter("cmTOr", (key, lang, fallback = "", vars) => {
    try {
      const v = helpers.translateKey(key, lang, vars);
      return v === key ? fallback : v;
    } catch {
      return fallback;
    }
  });

  // Issue label of an item in the page language ("Octubre 2026"), and the same
  // label for use inside a sentence ("octubre de 2026").
  eleventyConfig.addFilter("cmItemIssue", (item, lang) => issueLabelOf(item, lang));
  eleventyConfig.addFilter("cmIssueInSentence", (label, lang) => issueInSentence(label, lang));

  // Icon sprite: {{ names | cmIconSprite | safe }} once, then {{ "rss" | cmUse("size-4") | safe }}
  eleventyConfig.addFilter("cmIconSprite", (names) => iconSprite(names || WN_ICONS));
  eleventyConfig.addFilter("cmUse", (name, cls) => iconUse(name, cls));
  eleventyConfig.addGlobalData("cmWnIcons", WN_ICONS);
}
