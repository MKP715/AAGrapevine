// Committee area filters — Meeting, Events (+ .ics feeds), Documents, Photos, Announcements.
//
// Everything here is PURE data shaping: it turns the synced data files
// (data/site/events.json, drive.json, announcements.json, weekly_open.json)
// into ready-to-render objects so the Nunjucks templates stay simple.
// The same functions also feed src/pages/events-ics.11ty.js, so the web
// page and the calendar feed can never disagree.
//
// Dev helper: COMMITTEE_EMPTY=1 npx @11ty/eleventy …  renders every committee
// page as if the Drive / events / announcements data were empty (launch state).

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
export const TZ = "America/Chicago";
const LOCALES = { en: "en-US", es: "es-US" };
const EMPTY = !!process.env.COMMITTEE_EMPTY;

// Helpers handed over by eleventy.config.js (translateKey, pickLang, fmtDate, toDate).
// They are set when the plugin loads; the fallbacks keep this module usable
// from a plain `node` script too.
let H = {
  translateKey: (k) => k,
  // Intl's Spanish "7:00 p.m." → "7:00 p. m." (no-break spaces); replaced by eleventy.config.js's esMeridiem.
  esMeridiem: (s) => String(s).replace(/\b([ap])\.\s?m\./g, "$1.\u00a0m.").replace(/(\d) (?=[ap]\.\u00a0m\.)/g, "$1\u00a0"),
  pickLang: (item, field, lang) => {
    if (!item) return "";
    const i = item.i18n && item.i18n[field];
    if (i && i[lang]) return i[lang];
    return item[field] ?? (item.extra && item.extra[field]) ?? "";
  },
  fmtDate: (v) => String(v || ""),
  toDate: (v) => (v ? new Date(v) : null),
};
const t = (key, lang, vars) => H.translateKey(key, lang, vars);

/* ------------------------------------------------------------------ */
/*  Small utilities                                                    */
/* ------------------------------------------------------------------ */
const isYmd = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);

export function slugify(s, max = 60) {
  return String(s || "")
    .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, max).replace(/-+$/, "") || "item";
}

// Offset (minutes) of America/Chicago from UTC at a given instant.
function chicagoOffsetMinutes(date) {
  try {
    const f = new Intl.DateTimeFormat("en-US", { timeZone: TZ, timeZoneName: "shortOffset" });
    const tz = f.formatToParts(date).find((p) => p.type === "timeZoneName")?.value || "GMT-6";
    const m = tz.match(/GMT([+-]\d+)(?::(\d+))?/);
    return m ? Number(m[1]) * 60 + Math.sign(Number(m[1])) * Number(m[2] || 0) : -360;
  } catch {
    return -360;
  }
}

// A wall-clock time in Chicago → the real instant (Date).
function atChicago(y, mo, d, hhmm = "00:00") {
  const [h, mi] = String(hhmm).split(":").map(Number);
  const guess = new Date(Date.UTC(y, mo, d, h || 0, mi || 0));
  return new Date(guess.getTime() - chicagoOffsetMinutes(guess) * 60000);
}

// "YYYY-MM-DD" of an instant, as seen in Chicago.
function chicagoYmd(date) {
  const p = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  return p; // en-CA formats as YYYY-MM-DD
}

function ymdAddDays(ymd, n) {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

// Midnight (start of day) in Chicago for a YYYY-MM-DD string.
function chicagoMidnight(ymd) {
  const [y, m, d] = ymd.split("-").map(Number);
  return atChicago(y, m - 1, d, "00:00");
}

// The moment a day ends in Chicago (midnight after it), as ms: an all-day event whose last day is
// `ymd` is over then — not at noon UTC (7 AM Central), which is what a bare "YYYY-MM-DD" parses to.
export function chicagoDayEndMs(ymd) {
  return isYmd(ymd) ? chicagoMidnight(ymdAddDays(ymd, 1)).getTime() : NaN;
}

// Any IANA time zone (the Grapevine Weekly Open is hosted in Eastern time).
const zoneFmts = new Map();
function zoneFmt(tz) {
  if (!zoneFmts.has(tz)) {
    let f = null;
    try {
      if (tz) f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" });
    } catch {
      f = null; // unknown zone name
    }
    zoneFmts.set(tz, f);
  }
  return zoneFmts.get(tz);
}
// The zone itself when it is a real IANA name, else Central.
const validZone = (tz) => (tz && zoneFmt(String(tz)) ? String(tz) : TZ);
// Wall-clock parts of an instant (ms) in a zone.
function zoneParts(ms, tz) {
  const p = {};
  for (const x of zoneFmt(tz).formatToParts(new Date(ms))) if (x.type !== "literal") p[x.type] = Number(x.value);
  return { y: p.year, mo: p.month - 1, d: p.day, h: p.hour % 24, mi: p.minute, s: p.second };
}
// A wall-clock date + time in a zone → the real instant (ms). Month/day may overflow (Date.UTC rolls them).
function zoneInstant(y, mo, d, h, mi, tz) {
  const guess = Date.UTC(y, mo, d, h, mi);
  const offsetAt = (ms) => {
    const p = zoneParts(ms, tz);
    return Date.UTC(p.y, p.mo, p.d, p.h, p.mi, p.s) - Math.floor(ms / 1000) * 1000;
  };
  return guess - offsetAt(guess - offsetAt(guess)); // 2nd pass: right even on a DST-change day
}

/**
 * A weekly meeting at a fixed local time (e.g. Noon Eastern): its first start at or after
 * `firstMs` that is not over yet (`liveMs` after it starts). Steps one calendar week at a
 * time in the host's own time zone, so a daylight-saving change never moves it by an hour.
 * `hhmm` = the local start time ("12:00"); default: the local time of `firstMs`.
 * (src/assets/js/committee.js does the same in the browser.)
 */
export function nextWeeklyStart(firstMs, nowMs, tz = TZ, hhmm = "", liveMs = 75 * 60000) {
  if (validZone(tz) !== tz) {
    tz = TZ;
    hhmm = ""; // a local time in an unknown zone means nothing in Central: keep firstMs's clock time
  }
  const p = zoneParts(firstMs, tz);
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || "").trim());
  const h = m ? Number(m[1]) : p.h, mi = m ? Number(m[2]) : p.mi;
  let ms = firstMs;
  for (let w = 1; ms + liveMs < nowMs && w < 5000; w++) ms = zoneInstant(p.y, p.mo, p.d + 7 * w, h, mi, tz);
  return ms;
}

function parseInstant(v) {
  if (!v) return null;
  if (v instanceof Date) return isNaN(v) ? null : v;
  if (isYmd(v)) return new Date(v + "T12:00:00Z"); // noon UTC: same calendar day everywhere in the Americas
  const d = new Date(v);
  return isNaN(d) ? null : d;
}

function fmt(date, lang, opts) {
  try {
    const s = new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { timeZone: TZ, ...opts }).format(date);
    return lang === "es" ? H.esMeridiem(s) : s;
  } catch {
    return "";
  }
}

function fmtRange(a, b, lang, opts) {
  try {
    const f = new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { timeZone: TZ, ...opts });
    const s = typeof f.formatRange === "function" ? f.formatRange(a, b) : `${f.format(a)} – ${f.format(b)}`;
    return lang === "es" ? H.esMeridiem(s) : s;
  } catch {
    return "";
  }
}

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

// UTC basic format for calendar URLs / ICS: 20261022T000000Z
const utcStamp = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
const ymdCompact = (ymd) => ymd.replace(/-/g, "");

// Drive "…/view" link → "…/preview" (embeddable). Leaves other URLs alone.
export function drivePreviewUrl(url) {
  if (!url) return "";
  const m = String(url).match(/drive\.google\.com\/file\/d\/([\w-]+)/);
  if (m) return `https://drive.google.com/file/d/${m[1]}/preview`;
  const o = String(url).match(/[?&]id=([\w-]+)/);
  if (o && /drive\.google\.com/.test(url)) return `https://drive.google.com/file/d/${o[1]}/preview`;
  return url;
}

function driveFileId(url) {
  const m = String(url || "").match(/\/d\/([\w-]{10,})/) || String(url || "").match(/[?&]id=([\w-]{10,})/);
  return m ? m[1] : null;
}

// Lucide / local SVG icon (same logic as the global {% icon %} shortcode,
// needed because shortcodes can't call other shortcodes).
const LUCIDE_DIR = path.join(path.dirname(require.resolve("lucide-static/package.json")), "icons");
const iconCache = new Map();
function icon(name, cls = "size-4") {
  let svg = iconCache.get(name);
  if (!svg) {
    const local = path.join("src/_includes/icons", `${name}.svg`);
    const file = fs.existsSync(local) ? local : path.join(LUCIDE_DIR, `${name}.svg`);
    if (!fs.existsSync(file)) return "";
    svg = fs.readFileSync(file, "utf8").replace(/<!--.*?-->/gs, "").trim();
    iconCache.set(name, svg);
  }
  return svg
    .replace(/<svg([^>]*?)class="[^"]*"/, "<svg$1")
    .replace("<svg", `<svg class="icon ${cls}" aria-hidden="true" focusable="false"`)
    .replace(/\s(width|height)="24"/g, "");
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/* ------------------------------------------------------------------ */
/*  Committee meeting                                                  */
/* ------------------------------------------------------------------ */
const WD = { sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6 };

function nthWeekday(year, month, weekday, n) {
  if (n === -1) {
    const last = new Date(Date.UTC(year, month + 1, 0));
    return last.getUTCDate() - ((last.getUTCDay() - weekday + 7) % 7);
  }
  const first = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const day = 1 + ((weekday - first + 7) % 7) + (n - 1) * 7;
  const dim = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return day <= dim ? day : null;
}

// Start / end ("HH:MM", Central) of the committee meeting from config/site.yml `meeting`.
// A missing end (or one that is not after the start) means a 1-hour meeting — the same
// rule as src/_data/meeting.js and scripts/sync/meeting.py, so the hero, the event
// cards, the calendar files and the live countdown always agree.
const hhmmMinutes = (s) => {
  const [h, m] = String(s || "").split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
};
function plusHour(hhmm) {
  const [h, m] = String(hhmm || "19:00").split(":").map(Number);
  return `${String(((h || 0) + 1) % 24).padStart(2, "0")}:${String(m || 0).padStart(2, "0")}`;
}
const meetingStart = (cfg = {}) => String(cfg.start || "19:00");
function meetingEnd(cfg = {}) {
  const start = meetingStart(cfg);
  return cfg.end && hhmmMinutes(cfg.end) > hhmmMinutes(start) ? String(cfg.end) : plusHour(start);
}
// Unquoted YAML dates (skip_dates: [2026-12-16]) arrive as Date objects → "YYYY-MM-DD".
const skipDates = (cfg = {}) => (cfg.skip_dates || []).map((d) => (d instanceof Date ? d.toISOString().slice(0, 10) : String(d)));

/**
 * Committee meeting dates between `monthsBack` months ago and `monthsAhead`
 * months ahead, from config/site.yml `meeting` (same rule as src/_data/meeting.js).
 */
export function meetingDates(cfg = {}, monthsBack = 3, monthsAhead = 12) {
  const weekday = WD[String(cfg.weekday || "wednesday").toLowerCase()] ?? 3;
  const n = Number(cfg.week_of_month || 3);
  const skip = new Set(skipDates(cfg));
  const now = new Date();
  const out = [];
  for (let i = -monthsBack; i <= monthsAhead; i++) {
    const base = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + i, 1));
    const y = base.getUTCFullYear(), mo = base.getUTCMonth();
    const d = nthWeekday(y, mo, weekday, n);
    if (!d) continue;
    const ymd = `${y}-${String(mo + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    if (skip.has(ymd)) continue;
    const start = atChicago(y, mo, d, meetingStart(cfg));
    const end = atChicago(y, mo, d, meetingEnd(cfg));
    out.push({ ymd, start: start.toISOString(), end: (end > start ? end : new Date(start.getTime() + 3600e3)).toISOString() });
  }
  return out;
}

// "Every 3rd Wednesday of the month" / "Cada tercer miércoles del mes"
function meetingRuleText(cfg = {}, lang = "en") {
  const n = Number(cfg.week_of_month || 3);
  const wd = WD[String(cfg.weekday || "wednesday").toLowerCase()] ?? 3;
  // 2023-01-01 was a Sunday → +wd days gives the right weekday name
  const weekday = fmt(new Date(Date.UTC(2023, 0, 1 + wd, 12)), lang, { weekday: "long", timeZone: "UTC" });
  const ord = t(`committee.ord.${n === -1 ? "last" : n}`, lang);
  return t("committee.rule", lang, { ord, weekday });
}

// "7:00 – 8:00 PM" (Central wall clock), from HH:MM strings.
function meetingTimeRange(cfg = {}, lang = "en") {
  const a = atChicago(2026, 0, 21, meetingStart(cfg));
  let b = atChicago(2026, 0, 21, meetingEnd(cfg));
  if (b <= a) b = new Date(a.getTime() + 3600e3); // e.g. 23:30 → 00:30 (next day)
  return fmtRange(a, b, lang, { hour: "numeric", minute: "2-digit" });
}

// The repeat line of a monthly event from config/site.yml `recurring_events:` (build_data writes its
// rule into extra.rule in the shape of `meeting:`): "Every second Saturday of the month · 5:00 – 8:00 PM" /
// "Cada segundo sábado del mes · 5:00–8:00 p. m." — the same words and clock format as the committee
// meeting's own line ("Every third Wednesday of the month · 7:00 – 8:00 PM"), which sits right above it
// on /meetings/. Empty when the rule cannot be read (the caller then uses the data's recurrence_label).
export function recurrenceText(rule, lang = "en") {
  if (!rule || typeof rule !== "object") return "";
  const n = Number(rule.week_of_month);
  const hhmm = (v) => /^\d{1,2}:\d{2}$/.test(String(v || ""));
  if (![1, 2, 3, 4, 5, -1].includes(n) || !(String(rule.weekday || "").toLowerCase() in WD) || !hhmm(rule.start)) return "";
  const cfg = { week_of_month: n, weekday: rule.weekday, start: rule.start, end: hhmm(rule.end) ? rule.end : "" };
  return `${meetingRuleText(cfg, lang)} · ${meetingTimeRange(cfg, lang)}`;
}

// The languages the committee wrote an item in BY HAND besides its original one: a monthly event from
// config/site.yml `recurring_events:` (title_es …) or a content/events · content/announcements file with
// title_es / summary_es (build_data puts them in i18n and never lists them in `machine`). Text in such a
// language is not a foreign-language original: no "EN" pill and no lang="en" on it.
export function ownLangs(it) {
  if (!it || it.source !== "committee" || !["manual", "recurring"].includes(it.category)) return [];
  const t = (it.i18n && it.i18n.title) || {};
  const machine = Array.isArray(it.machine) ? it.machine : [];
  return ["en", "es"].filter((l) => l !== it.lang && !!t[l] && t[l] !== (it.title || "") && !machine.includes(l));
}

// "NETA 65 Grapevine & La Viña Committee Meeting" / "Reunión del Comité de Grapevine y La Viña de NETA 65"
// Built from config/site.yml `site.committee(_es)` — the same words build_data.py
// uses for the committee meetings in data/site/events.json, so every page agrees.
export function meetingTitle(site, lang = "en") {
  const s = site || {};
  const name = (lang === "es" ? s.committee_es : s.committee) || t("committee.name", lang);
  return t("committee.meeting_title", lang, { committee: name });
}

// Plain-text description used in calendars (Google / Outlook / .ics).
function meetingDescription(site, lang, pageUrl) {
  const m = site.meeting || {};
  const lines = [t("committee.meeting.cal_desc", lang)];
  if (m.zoom_url) lines.push("", `${t("committee.meeting.cal_join", lang)}: ${m.zoom_url}`);
  if (m.meeting_id) lines.push(`${t("committee.meeting.id", lang)}: ${m.meeting_id}`);
  if (m.passcode) lines.push(`${t("committee.meeting.passcode", lang)}: ${m.passcode}`);
  if (pageUrl) lines.push("", `${t("committee.cal.details", lang)}: ${pageUrl}`);
  return lines.join("\n");
}

/* ------------------------------------------------------------------ */
/*  Events                                                             */
/* ------------------------------------------------------------------ */
// Filter-chip groups on /events/ ("recurring" = a monthly event from config/site.yml
// `recurring_events:`, e.g. the booth at CityWide Dallas — a NETA 65 event, not a committee meeting;
// "neta65" / "ics" = an outside calendar feed from config/site.yml `sources.ics_feeds:` that lists NETA 65
// events, e.g. the neta65.org workshop calendar — shown with the NETA 65 events, not the GV/LV calendars)
const GROUP_OF = { committee: "committee", recurring: "neta", flyer: "neta", manual: "neta", ics: "neta", neta65: "neta", "gv-calendar": "calendar", "lv-calendar": "calendar" };
function eventGroup(it) {
  if (GROUP_OF[it.category]) return GROUP_OF[it.category];
  if (it.source === "calendar") return "calendar";
  return "neta";
}

function siteAbs(site, url) {
  const b = String(site?.url || "").replace(/\/$/, "");
  return b + (String(url).startsWith("/") ? url : "/" + url);
}

function localPath(url, lang) {
  if (!url || /^(https?:|mailto:|tel:|#)/.test(url)) return url;
  const u = url.startsWith("/") ? url : "/" + url;
  if (/^\/(en|es)(\/|$)/.test(u)) return u; // already language-prefixed
  return lang && lang !== "en" ? `/${lang}${u}` : u;
}

// Element ids already used on the committee pages / layout. An announcement or
// manual event whose file name slug equals one of these gets a prefixed anchor
// instead, so a deep link never jumps to the wrong place.
const RESERVED_IDS = new Set([
  "main", "mobile-drawer", "subscribe", "how-docs", "how-to-post", "share-photos", "albums",
  "ev-upcoming-title", "ev-next-title", "cm-preview", "cm-preview-title", "cm-lb-i18n", "item",
]);
// Anchor for a hand-written item: its file-name slug (the data links to
// "/events/#<slug>" and "/announcements/#<slug>"), else a stable fallback.
function itemAnchor(slug, fallback) {
  const s = String(slug || "");
  if (/^[a-z0-9][a-z0-9-]{0,99}$/.test(s) && !RESERVED_IDS.has(s) && !/^(month-|docs-|cm-)/.test(s)) return s;
  return fallback;
}

// The id of an event's card on /events/ ("ev-recurring-citywide-dallas-2026-10-10"), so other
// pages (home, search, announcements) can link straight to it.
export function eventAnchor(it) {
  return itemAnchor(it?.extra?.slug, "ev-" + slugify(String(it?.id || "").replace(/^ev:/, "")));
}

// "Zoom", "Online", "En línea"… as a location really means "online on <platform>".
const ONLINE_PLACE = /^(zoom|online|virtual|en l[ií]nea|google meet|meet|microsoft teams|teams|webex|skype|facebook live|youtube( live)?)$/i;

function calendarLinks(ev) {
  const text = ev.title;
  const details = ev.calDescription || "";
  const location = ev.calLocation || "";
  let gDates, oStart, oEnd;
  if (ev.allDay) {
    gDates = `${ymdCompact(ev.startYmd)}/${ymdCompact(ymdAddDays(ev.endYmd, 1))}`;
    oStart = ev.startYmd;
    oEnd = ymdAddDays(ev.endYmd, 1);
  } else {
    gDates = `${utcStamp(new Date(ev.startMs))}/${utcStamp(new Date(ev.endMs))}`;
    oStart = new Date(ev.startMs).toISOString().replace(/\.\d{3}/, "");
    oEnd = new Date(ev.endMs).toISOString().replace(/\.\d{3}/, "");
  }
  const q = (o) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  return {
    gcal: "https://calendar.google.com/calendar/render?" + q({ action: "TEMPLATE", text, dates: gDates, details, location, ctz: TZ }),
    outlook: "https://outlook.live.com/calendar/0/action/compose?" + q({ rru: "addevent", subject: text, startdt: oStart, enddt: oEnd, allday: ev.allDay ? "true" : "false", body: details, location }),
  };
}

/**
 * Normalize db.events items + the computed committee meetings into one list
 * of display-ready events for a language.
 *
 * @param {object[]} items   db.events.items
 * @param {object}   site    `site` global (config)
 * @param {string}   lang    "en" | "es"
 * @param {object}   [opt]   { monthsBack: 3, monthsAhead: 12, now: Date }
 * @returns {object[]} sorted by start (soonest first)
 */
export function normalizeEvents(items, site, lang = "en", opt = {}) {
  const now = (opt.now || new Date()).getTime();
  const m = site?.meeting || {};
  const out = [];
  const seen = new Set();
  const meetingPage = siteAbs(site, localPath("/meetings/", lang));

  // 1) Committee meetings — always computed from config/site.yml, so the
  //    calendar is right even if the daily data sync failed.
  //    (Titles/summaries are fixed human text — never machine translation.)
  const mTitle = meetingTitle(site, lang);
  for (const d of meetingDates(m, opt.monthsBack ?? 3, opt.monthsAhead ?? 12)) {
    const id = `ev:committee:${d.ymd}`;
    seen.add(id);
    out.push(shapeEvent({
      id, source: "committee", kind: "event", category: "committee", lang: "en",
      url: "/meetings/", title: mTitle, summary: t("committee.meeting.cal_desc", lang),
      date: d.start, is_new: false, _i18nTitle: true,
      extra: { start: d.start, end: d.end, all_day: false, location: m.platform || "Zoom", online_url: m.zoom_url || null },
    }, site, lang, now, meetingDescription(site, lang, meetingPage)));
  }

  // 2) Everything else (Drive flyers, manual events, TX GV/LV calendar, .ics feeds)
  for (const it of items || []) {
    if (!it || it.status === "gone" || it.kind !== "event") continue;
    if (it.category === "committee") {
      // Already covered above unless it is outside the computed window.
      // Compare by the meeting's date in Central time (an evening meeting is
      // already "tomorrow" in UTC).
      const raw = it.extra?.start || it.date || "";
      const inst = parseInstant(raw);
      const ymd = isYmd(raw) ? raw : inst ? chicagoYmd(inst) : "";
      if (seen.has(`ev:committee:${ymd}`) || seen.has(it.id)) continue;
    }
    if (seen.has(it.id)) continue;
    seen.add(it.id);
    const ev = shapeEvent(it, site, lang, now);
    if (ev) out.push(ev);
  }
  return out.filter(Boolean).sort((a, b) => a.startMs - b.startMs || a.title.localeCompare(b.title));
}

function shapeEvent(it, site, lang, now, descOverride) {
  const x = it.extra || {};
  const rawStart = x.start || it.date;
  if (!rawStart) return null;
  const allDay = isYmd(rawStart) || x.all_day === true;
  let startMs, endMs, startYmd, endYmd;
  if (allDay) {
    startYmd = isYmd(rawStart) ? rawStart : chicagoYmd(parseInstant(rawStart));
    const rawEnd = x.end ? (isYmd(x.end) ? x.end : chicagoYmd(parseInstant(x.end))) : startYmd;
    endYmd = rawEnd < startYmd ? startYmd : rawEnd;
    startMs = chicagoMidnight(startYmd).getTime();
    endMs = chicagoMidnight(ymdAddDays(endYmd, 1)).getTime(); // exclusive end of last day
  } else {
    const s = parseInstant(rawStart);
    if (!s) return null;
    const e = parseInstant(x.end);
    startMs = s.getTime();
    endMs = e && e.getTime() > startMs ? e.getTime() : startMs + 3600e3; // no end → assume 1 hour
    startYmd = chicagoYmd(s);
    endYmd = chicagoYmd(new Date(endMs - 1));
  }
  const start = new Date(startMs), end = new Date(endMs);
  const startNoon = new Date(startYmd + "T12:00:00Z");
  // An event over several days (an Area assembly, Fri–Sun): a date RANGE on the card, its tile and in the
  // calendars. A timed event that only runs past midnight (7 PM – 1 AM) is not one.
  const multiDay = startYmd !== endYmd && (allDay || endMs - startMs > 18 * 3600e3);
  const nDays = Math.round((Date.parse(endYmd + "T12:00:00Z") - Date.parse(startYmd + "T12:00:00Z")) / 864e5) + 1;
  // content/events `tentative: true` (or STATUS:TENTATIVE in an outside calendar): details not final yet.
  const tentative = x.tentative === true;
  const group = eventGroup(it);
  const committee = it.category === "committee";
  // A date of a monthly event from config/site.yml `recurring_events:` (build_data.recurring_events):
  // its "every month" line is written from its rule (extra.rule) exactly like the committee meeting's;
  // the data's own recurrence_label (both languages) is the fallback.
  const recurring = it.category === "recurring";
  // An outside calendar (GV/LV websites, .ics feeds) that gives only a date did not list
  // a start time — and its own event page may not either (La Viña's "Taller Mensual"
  // page shows just the date and the Zoom link). So those say "Time not listed — see
  // event details", never "All day". Only Drive flyers and hand-written events are
  // really "All day".
  const timeNotListed = allDay && it.source === "calendar" && /^https?:\/\//.test(it.url || "");
  const title = it._i18nTitle ? it.title : (H.pickLang(it, "title", lang) || it.title || "");
  const summary = it._i18nTitle ? it.summary : (H.pickLang(it, "summary", lang) || "");
  const recurrence = recurring ? (recurrenceText(x.rule, lang) || String(H.pickLang(it, "recurrence_label", lang) || x.recurrence_label || "")) : "";
  // The committee wrote this event in both languages (config/site.yml title / title_es, or a
  // content/events file's title_es / summary_es): the other language's text is not a foreign-language
  // original, so no language pill and no lang="…" on it. (Anything machine-translated keeps them.)
  const ownWords = !it._i18nTitle && ownLangs(it).includes(lang);
  const past = x.past === true || endMs <= now;

  // Labels (all in Central time — the Area's time zone)
  const tileSrc = allDay ? startNoon : start;
  const tileOpts = allDay ? { timeZone: "UTC" } : {};
  const part = (d, o) => fmt(d, lang, { ...o, ...tileOpts }).replace(/\.$/, "");
  const tile = { mon: part(tileSrc, { month: "short" }), day: part(tileSrc, { day: "numeric" }), wd: part(tileSrc, { weekday: "short" }), range: false };
  if (multiDay) {
    // "MAR · 19–21 · Fri–Sun" (a range across two months: "MAR–APR · 30–2 · Tue–Fri")
    const endSrc = allDay ? new Date(endYmd + "T12:00:00Z") : new Date(endMs - 1);
    const mon2 = part(endSrc, { month: "short" });
    tile.mon = mon2 === tile.mon ? tile.mon : `${tile.mon}–${mon2}`;
    tile.day = `${tile.day}–${part(endSrc, { day: "numeric" })}`;
    tile.wd = `${tile.wd}–${part(endSrc, { weekday: "short" })}`;
    tile.range = true;
  }
  let dateLabel, timeLabel = "", rangeLabel = "";
  if (allDay) {
    const a = new Date(startYmd + "T12:00:00Z"), b = new Date(endYmd + "T12:00:00Z");
    dateLabel = startYmd === endYmd
      ? cap(fmt(a, lang, { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }))
      : cap(fmtRange(a, b, lang, { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }));
    // "Fri, Mar 19 – Sun, Mar 21, 2027" / "Vie, 19 de mar – dom, 21 de mar de 2027"
    if (multiDay) rangeLabel = cap(fmtRange(a, b, lang, { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }));
    timeLabel = timeNotListed ? t("committee.events.time_not_listed", lang)
      : multiDay ? t("committee.events.n_days", lang, { n: nDays }) : t("committee.events.all_day", lang);
  } else {
    dateLabel = cap(fmt(start, lang, { weekday: "long", month: "long", day: "numeric", year: "numeric" }));
    if (multiDay) {
      // "Fri, Mar 19, 6:00 PM CDT – Sun, Mar 21, 12:00 PM CDT": the times are in the range itself
      rangeLabel = cap(fmtRange(start, end, lang, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }));
      dateLabel = cap(fmtRange(start, end, lang, { weekday: "long", month: "long", day: "numeric", year: "numeric" }));
    } else timeLabel = fmtRange(start, end, lang, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  }
  const monthKey = startYmd.slice(0, 7);
  const monthLabel = cap(fmt(new Date(monthKey + "-15T12:00:00Z"), lang, { month: "long", year: "numeric", timeZone: "UTC" }));

  let link = it.url ? localPath(it.url, lang) : "";
  const flyerView = x.flyer_url || (it.source === "drive" && /drive\.google\.com/.test(it.url || "") ? it.url : null);
  const flyerId = driveFileId(flyerView);
  const flyerThumb = x.flyer_thumb || (flyerId ? `https://lh3.googleusercontent.com/d/${flyerId}=w600` : null);
  // The place in this language: content/events `location_es` / `location_en` → i18n.location (build_data
  // also writes "Lugar por anunciarse" for an English "Venue to be announced"); else as written.
  const locText = String(H.pickLang(it, "location", lang) || x.location || "").trim();
  let location = [locText, !locText && x.city ? [x.city, x.state].filter(Boolean).join(", ") : ""].filter(Boolean).join("");
  // A place that is not known yet: plain text on the card — no map pin, no address in the calendars.
  const locationTba = !!location && x.location_tba === true;
  const online = x.online_url || null;
  // "Zoom" as the location of an online event is the platform, not a place.
  let platform = committee ? "" : String(x.platform || "").trim();
  if (!committee && location && (ONLINE_PLACE.test(location.trim()) || (platform && location.trim().toLowerCase() === platform.toLowerCase()))) {
    if (!platform && !/^(online|virtual|en l[ií]nea)$/i.test(location.trim())) platform = location.trim();
    location = "";
  }
  const isOnline = !committee && !!(online || x.online === true || platform);
  // A hand-written event links to its own card on /events/ ("/events/#slug"):
  // no title link / "details" button for that — the calendar entry keeps it.
  const selfLink = /^\/(?:[a-z]{2}\/)?events\/?(?:#.*)?$/.test(link);
  const body = x.body_md ? String(H.pickLang(it, "body_md", lang) || x.body_md || "").trim() : "";

  // Text for calendars
  const anchor = eventAnchor(it);
  const detailsUrl = link && !selfLink
    ? (link.startsWith("/") ? siteAbs(site, link) : link)
    : siteAbs(site, localPath("/events/", lang)) + (committee ? "" : "#" + anchor);
  if (selfLink) link = "";
  const descLines = [];
  if (descOverride) descLines.push(descOverride);
  else {
    // Google / Outlook links cannot say "tentative" — the first line of the description does.
    if (tentative) descLines.push(`${t("committee.events.tentative", lang)}. ${t("committee.events.tentative_help", lang)}`, "");
    if (summary) descLines.push(summary);
    if (recurrence) descLines.push(recurrence);
    if (locationTba) descLines.push(location);
    if (online) descLines.push("", `${platform ? t("committee.events.online_on", lang, { platform }) : t("committee.events.online", lang)}: ${online}`);
    if (flyerView) descLines.push(`${t("committee.events.flyer", lang)}: ${flyerView}`);
    // Date-only outside event: the calendar shows it as all-day, so the note says the time was not listed.
    if (detailsUrl !== flyerView) descLines.push("", `${t(timeNotListed ? "committee.events.time_not_listed" : "committee.cal.details", lang)}: ${detailsUrl}`);
  }
  const calDescription = descLines.join("\n").trim();
  const place = locationTba ? "" : location;
  const calLocation = committee ? (online || location) : [place, !place && online ? online : ""].filter(Boolean).join("");

  const ev = {
    id: it.id,
    anchor,
    uid: slugify(String(it.id).replace(/:/g, "-"), 90),
    group, committee, category: it.category || "", source: it.source || "",
    // recurrence: "Every second Saturday of the month · 5:00 – 8:00 PM" (/meetings/, calendars, search);
    // the card, which already shows the time, uses only the day part: "Every second Saturday of the month".
    recurring, series: recurring ? String(x.series || "") : "", recurrence, recurrenceDay: recurrence.split(" · ")[0], ownWords,
    title, summary, body, item: it,
    platform, isOnline,
    allDay, startMs, endMs, startYmd, endYmd, multiDay, nDays,
    startIso: allDay ? startYmd : start.toISOString(),
    endIso: allDay ? endYmd : end.toISOString(),
    // stays listed through its last day (an all-day event until midnight Central after its last day)
    expireIso: end.toISOString(),
    tile, dateLabel, timeLabel, rangeLabel, monthKey, monthLabel,
    shareWhen: [dateLabel, timeLabel].filter(Boolean).join(" · "),
    tentative, locationTba,
    shortLabel: cap(fmt(tileSrc, lang, { month: "short", day: "numeric", ...tileOpts })).replace(/\.(?=\s|$)/, ""),
    // One line for the next meeting: "Wednesday, October 21 · 7:00 PM CDT" (committee.js keeps it current)
    whenLabel: allDay ? dateLabel : cap(fmt(start, lang, { weekday: "long", month: "long", day: "numeric" })) + " · " + fmt(start, lang, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }),
    location, online, link, linkExternal: /^https?:/.test(link || ""),
    detailsUrl,
    flyer: flyerView ? { view: flyerView, preview: drivePreviewUrl(flyerView), thumb: flyerThumb } : null,
    isNew: !!it.is_new && !committee,
    past,
    calDescription, calLocation,
    dtstampSrc: it.first_seen || null,
  };
  Object.assign(ev, calendarLinks(ev));
  // The "Add to calendar → .ics file" download (src/assets/js/committee.js CM.downloadIcs): all-day events
  // as DATE values, end = the last day (the file gets the exclusive DTEND, the day after).
  ev.icsData = { uid: ev.uid + (lang !== "en" ? "-" + lang : "") + "@neta65-gvlv", title: ev.title, start: ev.startIso, end: ev.endIso, allDay, tentative, description: calDescription, location: calLocation, url: ev.detailsUrl, filename: slugify(ev.title, 40) };
  return ev;
}

/* ------------------------------------------------------------------ */
/*  iCalendar (RFC 5545) writer                                        */
/* ------------------------------------------------------------------ */
// TEXT escaping (RFC 5545 §3.3.11)
export function icsEscape(s) {
  return String(s ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/\\/g, "\\\\")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,")
    .replace(/\n/g, "\\n");
}

// Fold a content line to ≤ 75 octets per physical line, never splitting a
// UTF-8 character (RFC 5545 §3.1). Continuation lines start with one space.
export function icsFold(line) {
  const enc = new TextEncoder();
  if (enc.encode(line).length <= 75) return line;
  const parts = [];
  let cur = "", curBytes = 0, limit = 75;
  for (const ch of line) {
    const b = enc.encode(ch).length;
    if (curBytes + b > limit) {
      parts.push(cur);
      cur = "";
      curBytes = 0;
      limit = 74; // continuation lines begin with a space (1 octet)
    }
    cur += ch;
    curBytes += b;
  }
  if (cur) parts.push(cur);
  return parts.join("\r\n ");
}

/**
 * Build a complete VCALENDAR string.
 * @param {object[]} events  output of normalizeEvents()
 * @param {object}   o       { name, description, lang, url, now }
 */
export function buildIcs(events, o = {}) {
  const now = o.now || new Date();
  const stamp = utcStamp(now);
  const L = [];
  const push = (s) => L.push(icsFold(s));
  push("BEGIN:VCALENDAR");
  push("VERSION:2.0");
  push("PRODID:-//NETA 65 Grapevine La Vina Committee//Events " + String(o.lang || "en").toUpperCase() + "//EN");
  push("CALSCALE:GREGORIAN");
  push("METHOD:PUBLISH");
  push("NAME:" + icsEscape(o.name));
  push("X-WR-CALNAME:" + icsEscape(o.name));
  if (o.description) {
    push("DESCRIPTION:" + icsEscape(o.description));
    push("X-WR-CALDESC:" + icsEscape(o.description));
  }
  push("X-WR-TIMEZONE:" + TZ);
  if (o.url) push("URL:" + o.url);
  push("REFRESH-INTERVAL;VALUE=DURATION:PT12H");
  push("X-PUBLISHED-TTL:PT12H");
  for (const ev of events) {
    push("BEGIN:VEVENT");
    push("UID:" + ev.uid + (o.lang && o.lang !== "en" ? "-" + o.lang : "") + "@neta65-gvlv");
    push("DTSTAMP:" + stamp);
    if (ev.allDay) {
      push("DTSTART;VALUE=DATE:" + ymdCompact(ev.startYmd));
      push("DTEND;VALUE=DATE:" + ymdCompact(ymdAddDays(ev.endYmd, 1)));
      push("TRANSP:TRANSPARENT");
    } else {
      push("DTSTART:" + utcStamp(new Date(ev.startMs)));
      push("DTEND:" + utcStamp(new Date(ev.endMs)));
      push("TRANSP:OPAQUE");
    }
    push("SUMMARY:" + icsEscape(ev.title));
    if (ev.calDescription) push("DESCRIPTION:" + icsEscape(ev.calDescription));
    if (ev.calLocation) push("LOCATION:" + icsEscape(ev.calLocation));
    if (ev.detailsUrl) push("URL:" + ev.detailsUrl);
    if (ev.flyer && ev.flyer.view) push("ATTACH:" + ev.flyer.view);
    push("CATEGORIES:" + icsEscape(o.categoryLabel ? o.categoryLabel(ev) : ev.group));
    // TENTATIVE: details not final yet (content/events `tentative: true`); every other event is CONFIRMED.
    push("STATUS:" + (ev.tentative ? "TENTATIVE" : "CONFIRMED"));
    push("SEQUENCE:0");
    push("END:VEVENT");
  }
  push("END:VCALENDAR");
  return L.join("\r\n") + "\r\n";
}

/* ------------------------------------------------------------------ */
/*  Drive: documents & photos                                          */
/* ------------------------------------------------------------------ */
// Category tabs on /documents/ (fixed order; unknown folders follow by name)
export const DOC_TABS = [
  { key: "reports", icon: "file-bar-chart" },
  { key: "notes", icon: "notebook-pen" },
  { key: "slides", icon: "presentation" },
  { key: "workshops", icon: "pen-line" },
  { key: "flyers", icon: "megaphone" },
  { key: "forms", icon: "clipboard-list" },
];
const DOC_KINDS = new Set(["document", "slides", "form", "video_file", "photo"]);
const PHOTO_KINDS = new Set(["photo", "video_file"]);

function isPhotoItem(it) {
  return PHOTO_KINDS.has(it.kind) && (it.category === "photos" || it.category === "other" || !it.category);
}
function isDocItem(it) {
  if (!DOC_KINDS.has(it.kind)) return false;
  if (it.category === "photos" || it.category === "announcements") return false;
  if (isPhotoItem(it)) return false;
  return true;
}

function fileType(it) {
  const mime = String(it.extra?.mime || "").toLowerCase();
  if (it.kind === "form" || mime.includes("google-apps.form")) return { key: "form", icon: "clipboard-list", tone: "vine" };
  if (it.kind === "slides" || /presentation|powerpoint/.test(mime)) return { key: "slides", icon: "presentation", tone: "grape" };
  if (it.kind === "video_file" || mime.startsWith("video/")) return { key: "video", icon: "file-video", tone: "grape" };
  if (it.kind === "photo" || mime.startsWith("image/")) return { key: "image", icon: "file-image", tone: "vine" };
  if (mime.includes("pdf") || it.extra?.is_pdf) return { key: "pdf", icon: "file-text", tone: "lv" };
  return { key: "doc", icon: "file-text", tone: "gv" };
}

function itemDate(it) {
  return it.date || null;
}
function sortKey(it) {
  const d = parseInstant(it.date) || parseInstant(it.first_seen);
  return d ? d.getTime() : 0;
}
const byNewest = (a, b) => sortKey(b) - sortKey(a) || String(a.title).localeCompare(String(b.title));

function panelOf(it) {
  const n = Number(it.extra?.panel) || 0;
  return { n, label: it.extra?.panel_label || (n ? `Panel ${n}` : "") };
}

function shapeDoc(it, lang) {
  const x = it.extra || {};
  const ft = fileType(it);
  const mime = String(x.mime || "");
  const googleNative = mime.startsWith("application/vnd.google-apps");
  const view = x.view_url || it.url || "";
  const preview = x.preview_url || drivePreviewUrl(view);
  // uc?export=download does not work for native Google Docs/Slides/Forms.
  const download = x.download_url && !(googleNative && /export=download/.test(x.download_url)) ? x.download_url : "";
  return {
    id: it.id,
    title: H.pickLang(it, "title", lang) || it.title || "",
    type: ft,
    date: itemDate(it),
    added: it.first_seen || null,
    panel: panelOf(it),
    view, preview: ft.key === "form" ? "" : preview, download: ft.key === "form" ? "" : download,
    thumb: x.thumb_url || it.image || "",
    isNew: !!it.is_new,
    item: it,
  };
}

/**
 * Documents grouped into category tabs (+ panels inside each tab).
 * Returns { tabs: [{key, label, icon, count, groups: [{panel, label, items}]}], total, multiPanel }
 */
export function documentTabs(items, lang = "en") {
  const docs = (items || []).filter((it) => it && it.status !== "gone" && it.source === "drive" && isDocItem(it));
  const tabs = new Map(DOC_TABS.map((d) => [d.key, { key: d.key, icon: d.icon, label: t(`committee.docs.cat.${d.key}`, lang), desc: t(`committee.docs.cat.${d.key}_desc`, lang), builtin: true, items: [] }]));
  for (const it of docs) {
    let key = it.category;
    if (!tabs.has(key) || !tabs.get(key).builtin) {
      if (it.kind === "form") key = "forms";
      else {
        const folder = (it.extra?.path && it.extra.path[0]) || t("committee.docs.cat.other", lang);
        key = "folder-" + slugify(folder, 40);
        if (!tabs.has(key)) tabs.set(key, { key, icon: "folder", label: folder, desc: "", builtin: false, items: [] });
      }
    }
    tabs.get(key).items.push(shapeDoc(it, lang));
  }
  const panels = new Set();
  for (const tab of tabs.values()) {
    tab.items.sort((a, b) => byNewest(a.item, b.item));
    tab.count = tab.items.length;
    const g = new Map();
    for (const d of tab.items) {
      panels.add(d.panel.n);
      if (!g.has(d.panel.n)) g.set(d.panel.n, { panel: d.panel.n, label: d.panel.label || t("committee.docs.no_panel", lang), items: [] });
      g.get(d.panel.n).items.push(d);
    }
    tab.groups = [...g.values()].sort((a, b) => b.panel - a.panel);
  }
  const list = [...tabs.values()];
  // Built-in tabs keep their order; extra folder tabs follow alphabetically.
  const builtin = list.filter((x) => x.builtin);
  const extra = list.filter((x) => !x.builtin).sort((a, b) => a.label.localeCompare(b.label));
  const panelLabels = [...new Set(docs.map((it) => panelOf(it).label).filter(Boolean))];
  return { tabs: [...builtin, ...extra], total: docs.length, multiPanel: panels.size > 1, panels: panelLabels };
}

/**
 * Photo albums: one per Drive sub-folder (extra.album), else per folder/panel.
 * Returns [{key, slug, title, count, photos, videos, cover:[…], newest, oldest, panelLabel, items:[…]}] newest first.
 */
export function photoAlbums(items, lang = "en") {
  const photos = (items || []).filter((it) => it && it.status !== "gone" && it.source === "drive" && isPhotoItem(it));
  const albums = new Map();
  for (const it of photos) {
    const x = it.extra || {};
    const folder = x.album || (it.category === "other" && x.path && x.path[0]) || null;
    const pl = x.panel_label || (x.panel ? `Panel ${x.panel}` : "");
    const key = folder ? `f:${folder}` : `p:${x.panel || 0}`;
    if (!albums.has(key)) {
      const albumI18n = it.i18n?.album?.[lang];
      albums.set(key, {
        key,
        // Same slug build_data.py gives a What's New photo group (extra.album_slug),
        // so "/photos/#<album_slug>" lands on this album.
        alias: itemAnchor(slugify(x.album || (x.path || []).join(" / "), 80), ""),
        slug: "album-" + slugify(folder || pl || "photos", 50),
        title: albumI18n || folder || (pl ? t("committee.photos.panel_album", lang, { panel: pl }) : t("committee.photos.untitled_album", lang)),
        panelLabel: pl,
        items: [],
      });
    }
    const isVideo = it.kind === "video_file" || x.is_video;
    const fid = x.file_id || driveFileId(it.url);
    const thumb = x.thumb_url || it.image || (fid ? `https://lh3.googleusercontent.com/d/${fid}=w600` : "");
    albums.get(key).items.push({
      id: it.id,
      title: H.pickLang(it, "title", lang) || it.title || "",
      date: it.date || null,
      thumb,
      full: isVideo ? (x.preview_url || drivePreviewUrl(it.url)) : (x.image_url || (fid ? `https://lh3.googleusercontent.com/d/${fid}=w1600` : thumb)),
      view: x.view_url || it.url,
      isVideo: !!isVideo,
      isNew: !!it.is_new,
      item: it,
    });
  }
  const out = [...albums.values()];
  const slugs = new Set();
  for (const a of out) {
    a.items.sort((p, q) => byNewest(p.item, q.item));
    a.count = a.items.length;
    a.videos = a.items.filter((p) => p.isVideo).length;
    a.photos = a.count - a.videos;
    a.cover = a.items.filter((p) => p.thumb).slice(0, 3);
    const times = a.items.map((p) => parseInstant(p.date)).filter(Boolean).map((d) => d.getTime());
    a.newest = times.length ? new Date(Math.max(...times)).toISOString() : null;
    a.oldest = times.length ? new Date(Math.min(...times)).toISOString() : null;
    a.sortMs = times.length ? Math.max(...times) : Math.max(0, ...a.items.map((p) => sortKey(p.item)));
    a.hasNew = a.items.some((p) => p.isNew);
    let s = a.slug, i = 2;
    while (slugs.has(s)) s = `${a.slug}-${i++}`;
    a.slug = s;
    slugs.add(s);
    if (a.alias && slugs.has(a.alias)) a.alias = "";
    else if (a.alias) slugs.add(a.alias);
  }
  return out.sort((a, b) => b.sortMs - a.sortMs || a.title.localeCompare(b.title));
}

/* ------------------------------------------------------------------ */
/*  Announcements                                                      */
/* ------------------------------------------------------------------ */
export function announcementList(items) {
  const today = chicagoYmd(new Date());
  return (items || [])
    .filter((it) => it && it.status !== "gone")
    .filter((it) => {
      const exp = it.extra?.expires;
      return !exp || String(exp).slice(0, 10) >= today;
    })
    .map((it) => ({
      ...it,
      // Data links point to "/announcements/#<slug>" (content/announcements/<slug>.md)
      _anchor: itemAnchor(it.extra?.slug, "ann-" + slugify(String(it.id).replace(/^ann:/, ""), 70)),
      _pinned: !!it.extra?.pinned,
    }))
    .sort((a, b) => (b._pinned - a._pinned) || sortKey(b) - sortKey(a));
}

/* ------------------------------------------------------------------ */
/*  Grapevine Weekly Open: "Wednesdays" / "11 AM Central" → Spanish     */
/* ------------------------------------------------------------------ */
const ES_DAYS = {
  monday: "lunes", tuesday: "martes", wednesday: "miércoles", thursday: "jueves", friday: "viernes", saturday: "sábado", sunday: "domingo",
};
export function whenText(s, lang = "en") {
  if (!s || lang !== "es") return s || "";
  let out = String(s);
  out = out.replace(/\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(s)?\b/gi, (m0, d, plural) => {
    const es = ES_DAYS[d.toLowerCase()];
    return plural ? `los ${es === "sábado" || es === "domingo" ? es + "s" : es}` : es;
  });
  out = out
    .replace(/\b(\d{1,2}(?::\d{2})?)\s*a\.?\s?m\b\.?/gi, "$1\u00a0a.\u00a0m.")
    .replace(/\b(\d{1,2}(?::\d{2})?)\s*p\.?\s?m\b\.?/gi, "$1\u00a0p.\u00a0m.")
    .replace(/\bnoon\b/gi, "mediodía")
    .replace(/\b(?:central(?: time)?|CT|CST|CDT)\b/gi, "(hora del Centro)")
    .replace(/\b(?:eastern(?: time)?|ET|EST|EDT)\b/gi, "(hora del Este)")
    .replace(/\b(?:pacific(?: time)?|PT|PST|PDT)\b/gi, "(hora del Pacífico)")
    .replace(/\bevery\b/gi, "cada")
    .replace(/\bat\b/gi, "a las")
    .replace(/\band\b/gi, "y")
    .replace(/\s+/g, " ")
    .trim();
  return cap(out);
}

/**
 * Grapevine Weekly Open (data/site/weekly_open.json item) → display object.
 * Times are shown in Central time (NETA 65's time zone); the host's own time
 * ("Noon Eastern") is kept as a secondary line. `next` is rolled forward week
 * by week from extra.next_start so it is right even if the data is a few days old
 * (committee.js rolls it forward again in the browser). The weeks are counted in the
 * host's time zone (extra.timezone, extra.start_local): Noon Eastern stays 11 AM Central
 * across daylight-saving changes.
 */
export function weeklyOpen(wo, lang = "en", now = new Date()) {
  if (!wo) return null;
  const x = wo.extra || {};
  const pick = (f) => {
    const v = wo.i18n && wo.i18n[f] && wo.i18n[f][lang];
    return v ? String(v) : "";
  };
  const when = pick("when") || [whenText(x.day, lang), whenText(x.time_central || x.time, lang)].filter(Boolean).join(" · ");
  const hostTime = pick("time") || whenText(x.time, lang);
  const digits = String(x.zoom_id || "").replace(/\D+/g, "");
  let next = null;
  const n0 = parseInstant(x.next_start);
  if (n0) {
    const tz = validZone(x.timezone);
    // start_local is the host's clock time, so it only counts together with its own zone.
    const at = tz === x.timezone && /^\d{1,2}:\d{2}$/.test(String(x.start_local || "").trim()) ? String(x.start_local).trim() : "";
    // "live" for ~an hour and a quarter after it starts
    const d = new Date(nextWeeklyStart(n0.getTime(), now.getTime(), tz, at, 75 * 60000));
    next = {
      iso: d.toISOString(),
      tz, at, // for the browser's own roll-forward (committee.js)
      date: cap(fmt(d, lang, { weekday: "long", month: "long", day: "numeric" })),
      time: fmt(d, lang, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }),
    };
  }
  // A meeting that has not started yet (La Viña's, from extra.starts = "2026-11-05"): its first
  // start, in the host's zone. Until then the page says "Starts Thursday, November 5, 2026".
  let starts = null;
  if (isYmd(x.starts) && next) {
    const [y, mo, dd] = x.starts.split("-").map(Number);
    const tz = next.tz;
    const at = /^(\d{1,2}):(\d{2})$/.exec(next.at || "");
    const p = zoneParts(Date.parse(next.iso), tz); // no start_local: the clock time of next_start
    const ms = zoneInstant(y, mo - 1, dd, at ? Number(at[1]) : p.h, at ? Number(at[2]) : p.mi, tz);
    if (Number.isFinite(ms) && now.getTime() < ms) {
      const d = new Date(ms);
      // "Thursday, November 5, 2026" / "jueves 5 de noviembre de 2026" (no comma after the weekday in Spanish)
      let long = fmt(d, lang, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
      if (lang === "es") long = long.replace(/^([^\d,]+),\s*/, "$1 ");
      starts = { iso: d.toISOString(), date: lang === "es" ? long : cap(long), time: fmt(d, lang, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }) };
    }
  }
  const isLv = wo.source === "lavina" || wo.id === "weekly_open_lv";
  return {
    id: wo.id || "",
    isLv,
    lang: wo.lang || (isLv ? "es" : "en"),
    title: pick("title") || String(wo.title || ""),
    summary: pick("summary") || String(wo.summary || ""),
    summaryMachine: Array.isArray(wo.machine) && wo.machine.includes(lang),
    day: pick("day") || whenText(x.day, lang),
    timeCentral: pick("time_central") || whenText(x.time_central, lang),
    when,
    hostTime: hostTime && !when.toLowerCase().includes(hostTime.toLowerCase()) ? hostTime : "",
    zoomId: x.zoom_id || "",
    zoomDigits: digits,
    passcode: x.passcode || "",
    zoomUrl: x.zoom_url || (digits ? `https://zoom.us/j/${digits}` : ""),
    detailsUrl: x.url || wo.url || "",
    playerUrl: x.player_url || "",
    next,
    starts,
  };
}

/**
 * Every weekly open meeting (data/site/weekly_open.json: the Grapevine Weekly Open, then La Viña's
 * Reunión Abierta) → display objects, the page language's meeting first (La Viña on /es/).
 */
export function weeklyOpenAll(items, lang = "en", now = new Date()) {
  const list = (items || []).filter((it) => it && it.kind === "meeting" && it.status !== "gone")
    .map((it) => weeklyOpen(it, lang, now)).filter(Boolean);
  const rank = (w) => (w.lang === lang ? 0 : 1);
  return list.map((w, i) => ({ w, i })).sort((a, b) => rank(a.w) - rank(b.w) || a.i - b.i).map((o) => o.w);
}

/* ------------------------------------------------------------------ */
/*  Weekly digest: Book of the Month teaser + this month's toolkit     */
/* ------------------------------------------------------------------ */
// The one canonical home for prices and dates is /shop/ (data/site/shop.json → db.shop): the digest
// only shows a compact teaser — title, sale price, end date — linking there and to the official store.
const moneyFmt = (v, lang) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  try {
    return new Intl.NumberFormat(LOCALES[lang] || "en-US", { style: "currency", currency: "USD" }).format(n);
  } catch {
    return `$${n.toFixed(2)}`;
  }
};
export function digestShop(shop, lang = "en", now = new Date()) {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  const offers = (shop?.botm || [])
    .filter((b) => b && b.url && Number.isFinite(Number(b.sale_price)) && (!isYmd(b.ends) || b.ends >= today))
    .map((b) => {
      const isLv = b.pub === "lv";
      let host = "";
      try { host = new URL(b.url).hostname.replace(/^www\./, ""); } catch { host = isLv ? "aalavina.org" : "aagrapevine.org"; }
      return {
        pub: b.pub, isLv, host, raw: b,
        pubName: isLv ? "La Viña" : "Grapevine",
        // The title the book is sold under (a Grapevine book in English, a La Viña book in Spanish),
        // never a translation; the translation is only a small gloss under it (like /shop/).
        title: b.title || (b.i18n?.title?.[lang]) || "",
        titleLang: b.lang || (isLv ? "es" : "en"),
        gloss: b.title && b.i18n?.title?.[lang] && b.i18n.title[lang] !== b.title ? b.i18n.title[lang] : "",
        machine: Array.isArray(b.machine) && b.machine.includes(lang),
        url: b.url,
        image: b.image || "",
        pct: Number(b.discount_pct) || null,
        sale: moneyFmt(b.sale_price, lang),
        price: Number(b.price) > Number(b.sale_price) ? moneyFmt(b.price, lang) : "",
        ends: isYmd(b.ends) ? b.ends : "",
        endsLabel: isYmd(b.ends) ? fmt(parseInstant(b.ends), lang, { month: "long", day: "numeric" }) : "", // "October 14" / "14 de octubre"
      };
    })
    // the page language's magazine first
    .sort((a, b) => ((a.isLv ? "es" : "en") === lang ? 0 : 1) - ((b.isLv ? "es" : "en") === lang ? 0 : 1));
  const pcts = [...new Set(offers.map((o) => o.pct).filter(Boolean))];
  // This month's poster & toolkit: /monthly/YYYY-MM/ (America/Chicago)
  const ym = today.slice(0, 7);
  const monthLabel = fmt(parseInstant(`${ym}-15`), lang, { month: "long", year: "numeric" }); // "September 2026" / "septiembre de 2026"
  return { offers, pct: pcts.length === 1 ? pcts[0] : null, month: { key: ym, path: `/monthly/${ym}/`, label: monthLabel } };
}

/**
 * The digest's plain text (community.js digestText) + the Book of the Month teaser and the
 * toolkit line, inserted before the closing "everything new" footer (the last paragraph).
 */
export function digestShopText(text, shop, langs, style, site, now = new Date()) {
  const L = Array.isArray(langs) ? langs : [langs];
  const main = L[0] || "en";
  const wa = style === "whatsapp";
  const base = String(site?.url || "").replace(/\/+$/, "");
  const abs = (p, l) => `${base}${l === "es" ? "/es" : ""}${p}`;
  // vars: an object, or a function of the language (a month name differs by language)
  const both = (key, vars) => L.map((l) => t(key, l, typeof vars === "function" ? vars(l) : vars)).filter((v, i, a) => a.indexOf(v) === i).join(" / ");
  const head = (s) => (wa ? `*${s}*` : `${s.toUpperCase()}\n${"-".repeat(Math.min(s.length, 60))}`);
  const dg = digestShop(shop, main, now);
  const out = [];
  if (dg.offers.length) {
    const label = dg.pct ? both("community.digest.botm_title", { pct: dg.pct }) : both("community.digest.botm_title_plain");
    out.push(wa ? `📚 ${head(label)}` : head(label));
    for (const o of dg.offers) {
      // the title it is sold under first, then the translations as a second line
      const titles = [o.raw.title, ...L.map((l) => o.raw.i18n?.title?.[l])].filter((v, i, a) => v && a.indexOf(v) === i);
      const price = o.price ? t("community.digest.botm_price", main, { sale: o.sale, price: o.price }) : o.sale;
      const ends = o.endsLabel ? ` · ${t("community.digest.botm_ends", main, { date: o.endsLabel })}` : "";
      out.push(`${wa ? "•" : "-"} "${titles[0] || o.title}" (${o.pubName}) — ${price}${ends}`);
      for (const r of titles.slice(1)) out.push(`  "${r}"`);
      out.push(`  ${o.url}`);
    }
    out.push(`${both("community.digest.botm_more")}: ${abs("/shop/", main)}#botm`);
    out.push("");
  }
  out.push(`${wa ? "🖼️ " : ""}${both("community.digest.monthly", (l) => ({ month: digestShop(null, l, now).month.label }))}: ${abs(dg.month.path, main)}`);
  const block = out.join("\n");
  const s = String(text || "").replace(/\s+$/, "");
  const cut = s.lastIndexOf("\n\n");
  return (cut > 0 ? `${s.slice(0, cut)}\n\n${block}\n\n${s.slice(cut + 2)}` : `${s}\n\n${block}`) + "\n";
}

/**
 * The committee's Google Drive, as the last sync saw it (data/site/status.json):
 * root + newest Panel folder (with a direct link), when it was last checked.
 * Used by the empty states so they can say exactly where a file goes.
 *
 * Only the current Panel folder is ever linked. The shared ROOT folder is never
 * linked from the site (it also holds old panels and loose files, such as event
 * sign-up sheets), so when the sync did not find a Panel folder, `url` is null
 * and every "Open Drive folder" button is hidden.
 */
export function driveInfo(status, site) {
  const src = (status?.sources || []).find((s) => s && s.source === "drive") || null;
  const st = (src && src.stats) || {};
  const panels = (st.panel_folders || []).filter((p) => p && p.id).sort((a, b) => (Number(b.panel) || 0) - (Number(a.panel) || 0));
  const p = panels[0] || null;
  const minPanel = Number(site?.drive?.min_panel) || null;
  const panel = p
    ? { number: Number(p.panel) || null, label: p.label || `Panel ${p.panel}`, name: p.name || p.label || `Panel ${p.panel}`, url: `https://drive.google.com/drive/folders/${p.id}` }
    : minPanel ? { number: minPanel, label: `Panel ${minPanel}`, name: `Panel ${minPanel}`, url: null } : null;
  return {
    rootName: st.root || "A65_GV",
    panel,
    // Where "Open Drive folder" goes: the current Panel folder, or nowhere (never the root).
    url: (panel && panel.url) || null,
    checked: src && src.ok !== null ? src.updated || null : null,
    ok: src ? src.ok : null,
    files: Number(st.files ?? src?.count ?? 0) || 0,
  };
}

/* ------------------------------------------------------------------ */
/*  Grapevine meetings (data/site/meetings.json → /meetings/#grapevine-meetings) */
/* ------------------------------------------------------------------ */
// "20:00" → "8:00 PM" / "8:00 p. m." (the site's Spanish spelling, no-break spaces). The feeds give
// local Central time as a wall-clock string, so no time zone math is needed (or wanted) here.
export function clock12(hhmm, lang = "en") {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(hhmm || ""));
  if (!m) return "";
  const h = Number(m[1]) % 24;
  const h12 = h % 12 || 12;
  if (lang === "es") return `${h12}:${m[2]} ${h < 12 ? "a. m." : "p. m."}`;
  return `${h12}:${m[2]} ${h < 12 ? "AM" : "PM"}`;
}

// Weekday names, 0 = Sunday ("Sunday" / "Domingo" — capitalised: they are headings and options)
export function weekdayName(d, lang = "en") {
  try {
    return cap(new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { weekday: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2023, 0, 1 + Number(d)))));
  } catch {
    return ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][Number(d)] || "";
  }
}

// Lower-case, accents removed: the same folding committee.js uses for the "City, county or group" box.
const foldText = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
const pickL = (o, lang) => (o && typeof o === "object" ? o[lang] || o.en || "" : String(o || ""));
// Type codes shown another way on the card (attendance line, language badge) or that repeat the section
const GVM_SKIP_TYPES = new Set(["ONL", "TC", "S", "EN", "INACTIVE"]);
const GVM_ACCESS_TYPES = new Set(["X", "XB"]);

/**
 * The Grapevine meetings of data/site/meetings.json, ready for /meetings/:
 * our Area first (grouped by weekday), then one group per nearby region, each by weekday.
 * Nothing here decides WHICH meetings exist — the data does; this only shapes and labels them.
 */
export function gvMeetings(data, lang = "en", site = {}) {
  const d = data || {};
  const L = lang === "es" ? "es" : "en";
  const labels = d.type_labels || {};
  const sources = Array.isArray(d.sources) ? d.sources : [];
  const srcById = new Map(sources.map((s) => [s.id, s]));
  const items = (EMPTY ? [] : Array.isArray(d.items) ? d.items : []).filter((it) => it && it.attendance !== "inactive" && Number.isInteger(Number(it.day)));
  const countyWord = (c) => (L === "es" ? `condado de ${c}` : `${c} County`);

  const card = (it) => {
    const types = (it.types || []).map(String);
    const shown = types.filter((c) => !GVM_SKIP_TYPES.has(c) && !GVM_ACCESS_TYPES.has(c) && labels[c]);
    // "Grapevine" first (the reason the meeting is here), then the office's other types
    const badges = shown.sort((a, b) => (a === "GR" ? -1 : b === "GR" ? 1 : 0))
      .map((c) => ({ code: c, label: pickL(labels[c], L), gv: c === "GR" }));
    const access = types.some((c) => GVM_ACCESS_TYPES.has(c));
    const srcs = (it.sources || []).map((id) => srcById.get(id)).filter(Boolean);
    // The details link goes to the office whose site the meeting's url is on
    const host = hostOf(it.url);
    const own = srcs.find((s) => hostOf(s.url) === host) || srcs[0] || null;
    const texas = !it.state || it.state === "TX";
    const place = [it.city, texas ? "" : it.state].filter(Boolean).join(", ");
    const placeLine = [place, it.county ? countyWord(it.county) : ""].filter(Boolean).join(" · ");
    const att = ["in_person", "online", "hybrid"].includes(it.attendance) ? it.attendance : "in_person";
    const groupLabel = it.in_area ? "" : pickL(it.nearby?.label, L);
    return {
      id: it.id,
      anchor: "mtg-" + String(it.id || "").replace(/^mtg:/, "").replace(/[^a-z0-9-]/gi, ""),
      name: it.name || "",
      day: Number(it.day),
      time: clock12(it.time, L),
      end: it.end_time ? clock12(it.end_time, L) : "",
      location: it.location || "",
      address: it.address || "",
      placeLine,
      approximate: !!it.approximate,
      attendance: att,
      spanish: it.lang === "es" || types.includes("S"),
      textLang: it.lang === "es" ? "es" : "en", // the office's own words (place notes)
      badges,
      access,
      directions: it.directions_url || "",
      url: it.url || "",
      siteHost: host,
      siteName: own ? own.name : "",
      listedBy: srcs.map((s) => s.name),
      inArea: !!it.in_area,
      // what the "City, county or group" box searches (accent- and case-insensitive)
      search: foldText([it.name, it.city, it.county, it.county ? countyWord(it.county) : "", it.region, it.district, it.state, it.address, it.location, groupLabel].filter(Boolean).join(" ")),
    };
  };

  const byDay = (cards) => {
    const days = [];
    for (let dd = 0; dd < 7; dd++) {
      const list = cards.filter((c) => c.day === dd);
      if (list.length) days.push({ day: dd, name: weekdayName(dd, L), count: list.length, items: list });
    }
    return days;
  };

  const all = items.map(card);
  const ours = all.filter((c) => c.inArea);
  const groups = [];
  const groupList = Array.isArray(d.groups) ? d.groups : [];
  const areaGroup = groupList.find((g) => g && g.in_area);
  groups.push({ id: areaGroup?.id || "neta65", inArea: true, label: pickL(areaGroup?.label, L) || pickL(site?.meetings?.area_label, L), count: ours.length, days: byDay(ours) });
  const nearbyCards = all.filter((c) => !c.inArea);
  const placed = new Set();
  for (const g of groupList) {
    if (!g || g.in_area) continue;
    const list = nearbyCards.filter((c) => items.find((it) => it.id === c.id)?.nearby?.id === g.id);
    if (!list.length) continue;
    list.forEach((c) => placed.add(c.id));
    groups.push({ id: g.id, inArea: false, label: pickL(g.label, L), count: list.length, days: byDay(list) });
  }
  // A nearby meeting whose group is missing from `groups` still shows (under its own label)
  const rest = nearbyCards.filter((c) => !placed.has(c.id));
  if (rest.length) {
    const lbl = pickL(items.find((it) => it.id === rest[0].id)?.nearby?.label, L) || t("committee.gvm.nearby_title", L);
    groups.push({ id: "nearby-other", inArea: false, label: lbl, count: rest.length, days: byDay(rest) });
  }

  const okSources = sources.filter((s) => s && s.ok === true).map((s) => ({ name: s.name, url: s.url, host: hostOf(s.url) }));
  const failed = sources.filter((s) => s && s.ok === false).map((s) => s.name);
  // Where to look without our list: the offices' own sites (never a feed address)
  const offices = (sources.length ? sources : (site?.meetings?.feeds || []).map((f) => ({ name: f.name, url: f.site })))
    .filter((s) => s && s.name && /^https:\/\//.test(s.url || "")).map((s) => ({ name: s.name, url: s.url, host: hostOf(s.url) }));
  const dayCounts = [0, 1, 2, 3, 4, 5, 6].map((dd) => ({ day: dd, name: weekdayName(dd, L), count: all.filter((c) => c.day === dd).length }));

  return {
    total: all.length,
    inArea: ours.length,
    nearby: nearbyCards.length,
    areaGroup: groups[0],
    nearbyGroups: groups.slice(1),
    updated: d.updated || null,
    okSources,
    failed,
    offices,
    dayCounts,
  };
}

/* ------------------------------------------------------------------ */
/*  Eleventy registration                                              */
/* ------------------------------------------------------------------ */
export default function (eleventyConfig, helpers) {
  if (helpers) H = { ...H, ...helpers };

  // Data guard: {{ db.drive.items | cmData }} → [] when COMMITTEE_EMPTY=1 (launch-state preview)
  eleventyConfig.addFilter("cmData", (items) => (EMPTY ? [] : items || []));

  eleventyConfig.addFilter("cmEvents", (items, site, lang) => normalizeEvents(EMPTY ? [] : items, site, lang));
  eleventyConfig.addFilter("cmDocTabs", (items, lang) => documentTabs(EMPTY ? [] : items, lang));
  eleventyConfig.addFilter("cmAlbums", (items, lang) => photoAlbums(EMPTY ? [] : items, lang));
  eleventyConfig.addFilter("cmAnnouncements", (items) => announcementList(EMPTY ? [] : items));
  eleventyConfig.addFilter("cmRule", (cfg, lang) => meetingRuleText(cfg, lang));
  eleventyConfig.addFilter("cmTimeRange", (cfg, lang) => meetingTimeRange(cfg, lang));
  eleventyConfig.addFilter("cmWhen", (s, lang) => whenText(s, lang));
  eleventyConfig.addFilter("cmSlug", (s) => slugify(s));
  eleventyConfig.addFilter("cmDigits", (s) => String(s || "").replace(/\D+/g, ""));
  eleventyConfig.addFilter("cmPreview", (u) => drivePreviewUrl(u));
  eleventyConfig.addFilter("cmWebcal", (u) => String(u || "").replace(/^https?:\/\//, "webcal://"));
  eleventyConfig.addFilter("cmWeekly", (wo, lang) => weeklyOpen(wo, lang));
  // Both weekly open meetings (Grapevine Weekly Open + La Viña), the page language's first — /meetings/#weekly-open
  eleventyConfig.addFilter("cmWeeklyAll", (items, lang) => weeklyOpenAll(EMPTY ? [] : items, lang));
  // Grapevine meetings in our Area and nearby (db.meetings) — /meetings/#grapevine-meetings
  eleventyConfig.addFilter("cmGvMeetings", (data, lang, site) => gvMeetings(data, lang, site));
  // Weekly digest (/digest/): Book of the Month teaser + this month's toolkit link, and the same in the copy text
  eleventyConfig.addFilter("cmDigestShop", (shop, lang) => digestShop(shop, lang));
  eleventyConfig.addFilter("cmDigestShopText", (text, shop, langs, style, site) => digestShopText(text, shop, langs, style, site));
  // Text for GLightbox's data-title / data-description. GLightbox puts those values into the
  // page with innerHTML, so plain autoescaping is not enough (the browser decodes the
  // attribute first). This returns HTML-escaped text as a normal string; autoescape then
  // escapes it a second time, and GLightbox ends up showing the name as plain text.
  // A leading "." is escaped too: GLightbox would treat such a description as a CSS selector.
  eleventyConfig.addFilter("cmLbText", (s) => esc(s).replace(/^\./, "&#46;"));
  eleventyConfig.addFilter("cmDriveInfo", (status, site) => driveInfo(status, site));

  // Where a file goes on Drive, as a breadcrumb:
  // {% cmDrivePath lang, di, "photos/2027 Spring Assembly", "booth.jpg" %}
  eleventyConfig.addShortcode("cmDrivePath", function (lang, di, folder, file = "") {
    const L = lang || "en";
    const d = di || {};
    const segs = [d.rootName || "A65_GV"];
    if (d.panel) segs.push(d.panel.name || d.panel.label);
    const target = String(folder || "").split("/").map((s) => s.trim()).filter(Boolean);
    // A <p> cannot carry an aria-label (screen readers ignore it), so the label is
    // visually hidden text at the start, and each chevron reads as "/".
    const sep = `<span class="cm-path-sep">${icon("chevron-right", "size-3.5")}<span class="sr-only"> / </span></span>`;
    const parts = segs.map((s) => `<span class="cm-path-seg">${icon("folder", "size-3.5")}<span>${esc(s)}</span></span>`);
    target.forEach((s, i) => parts.push(`<span class="cm-path-seg is-target">${icon(i === target.length - 1 && !file ? "folder-open" : "folder", "size-3.5")}<span>${esc(s)}</span></span>`));
    if (file) parts.push(`<span class="cm-path-file">${icon(/\.(jpe?g|png|heic|webp)$/i.test(file) ? "file-image" : "file-text", "size-3.5")}<span>${esc(file)}</span></span>`);
    return `<p class="cm-path"><span class="sr-only">${esc(t("committee.drive.path_aria", L))}: </span>${parts.join(sep)}</p>`;
  });

  // "Drive checked Sep 23, 2026 — nothing uploaded yet" (empty states only)
  eleventyConfig.addShortcode("cmDriveChecked", function (lang, di) {
    const L = lang || "en";
    const d = di || {};
    if (!d.checked) return "";
    const date = H.fmtDate(d.checked, L, "medium");
    const bad = d.ok === false;
    return `<p class="cm-checked${bad ? " is-warn" : ""}">${icon(bad ? "triangle-alert" : "circle-check", "size-4")}<span>${esc(t(bad ? "committee.drive.checked_problem" : "committee.drive.checked_empty", L, { date }))}</span></p>`;
  });

  // The next date of each monthly recurring event (config/site.yml `recurring_events:`), soonest
  // first — the "Also every month" box on /meetings/.
  eleventyConfig.addFilter("cmRecurringNext", (items, site, lang) => {
    const seen = new Set();
    return normalizeEvents(EMPTY ? [] : items, site, lang, { monthsBack: 0, monthsAhead: 0 })
      .filter((e) => e.recurring && !e.past && !seen.has(e.series) && seen.add(e.series));
  });

  // Next committee meeting as an event (for the pinned card & calendar buttons)
  eleventyConfig.addFilter("cmNextMeeting", (site, lang) => {
    const evs = normalizeEvents([], site, lang, { monthsBack: 0, monthsAhead: 14 });
    return evs.find((e) => e.committee && !e.past) || null;
  });

  // Rule for the client-side countdown (same shape as GV.nextMeeting expects)
  // (a missing end = a 1-hour meeting, like everywhere else — see meetingEnd)
  eleventyConfig.addFilter("cmRuleObj", (cfg) => {
    cfg = cfg || {};
    return {
      weekday: WD[String(cfg.weekday || "wednesday").toLowerCase()] ?? 3,
      n: Number(cfg.week_of_month || 3),
      start: meetingStart(cfg),
      end: meetingEnd(cfg),
      skip: skipDates(cfg),
    };
  });

  // Accessible preview modal for Drive files (documents, flyers):
  // any element with data-cm-preview="<embed url>" opens it (see committee.js).
  // Uses the native <dialog> element (focus trap, Esc to close, inert page).
  eleventyConfig.addShortcode("cmPreviewDialog", function (lang) {
    const L = lang || "en";
    return `
<dialog id="cm-preview" class="cm-dialog" aria-labelledby="cm-preview-title">
  <div class="cm-dialog-inner">
    <div class="flex items-center gap-2 border-b border-line px-3 py-2.5 sm:px-4">
      <span class="hidden size-9 shrink-0 place-items-center rounded-lg bg-gv-soft text-gv sm:grid">${icon("eye", "size-4")}</span>
      <h2 id="cm-preview-title" class="min-w-0 flex-1 truncate font-display text-base font-semibold text-ink sm:text-lg">${esc(t("common.preview", L))}</h2>
      <a class="btn-secondary btn-sm" data-cm-open href="#" target="_blank" rel="noopener">${icon("external-link", "size-4")}<span class="hidden sm:inline">${esc(t("common.open", L))}</span></a>
      <a class="btn-secondary btn-sm" data-cm-download href="#" rel="noopener" hidden>${icon("download", "size-4")}<span class="hidden sm:inline">${esc(t("common.download", L))}</span></a>
      <button type="button" class="grid size-10 shrink-0 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-ink" data-cm-close aria-label="${esc(t("common.close", L))}">${icon("x", "size-5")}</button>
    </div>
    <div class="relative min-h-0 flex-1 bg-surface-2">
      <p class="cm-dialog-loading">${icon("loader-circle", "size-5 animate-spin")} ${esc(t("common.loading", L))}</p>
      <iframe class="relative size-full border-0" title="${esc(t("common.preview", L))}" allow="autoplay; fullscreen" referrerpolicy="no-referrer"></iframe>
    </div>
    <p class="border-t border-line px-4 py-2 text-xs text-faint">${esc(t("committee.preview.note", L))}</p>
  </div>
</dialog>`;
  });

  // "Subscribe to the calendar" card (used on /meetings/ and /events/):
  // {% cmSubscribe lang, site, compact %}
  // The card lays itself out by its OWN width (container queries), not the screen's: two
  // columns (buttons | feed address) when it is at least 48rem wide, stacked in a narrow
  // column or sidebar. compact = no "how to set up" row.
  eleventyConfig.addShortcode("cmSubscribe", function (lang, site, compact = false) {
    const L = lang || "en";
    const other = L === "es" ? "en" : "es";
    const feed = siteAbs(site, localPath("/events.ics", L));
    const otherFeed = siteAbs(site, localPath("/events.ics", other));
    const webcal = feed.replace(/^https?:\/\//, "webcal://");
    const name = t("committee.ics.name", L);
    const gcal = "https://calendar.google.com/calendar/r?cid=" + encodeURIComponent(webcal);
    const outlook = "https://outlook.live.com/calendar/0/addfromweb?url=" + encodeURIComponent(feed) + "&name=" + encodeURIComponent(name);
    const btn = (href, ic, label, cls = "btn-secondary") =>
      `<a class="${cls}" href="${esc(href)}"${href.startsWith("http") ? ' target="_blank" rel="noopener"' : ""}>${icon(ic, "size-4")} ${esc(label)}</a>`;
    const howto = compact ? "" : `
      <div class="mt-6 grid gap-3 @3xl:grid-cols-3">
        ${["google", "apple", "outlook"].map((k) => `
        <details class="cm-howto">
          <summary>${icon(k === "apple" ? "smartphone" : "calendar", "size-4 text-gv")} <span>${esc(t(`committee.sub.howto_${k}`, L))}</span>${icon("chevron-down", "size-4 ml-auto opacity-60 cm-chev")}</summary>
          <p>${esc(t(`committee.sub.howto_${k}_text`, L))}</p>
        </details>`).join("")}
      </div>`;
    return `
<div class="card cm-subscribe @container relative h-full overflow-hidden card-pad">
  <div class="grid grid-cols-1 gap-6 @3xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] @3xl:items-center">
    <div>
      <p class="eyebrow flex items-center gap-2">${icon("calendar-sync", "size-4 text-gv")} ${esc(t("committee.sub.eyebrow", L))}</p>
      <h2 class="mt-2 font-display text-2xl font-semibold leading-tight text-ink @xl:text-3xl">${esc(t("committee.sub.title", L))}</h2>
      <p class="mt-2 max-w-xl leading-relaxed text-muted">${esc(t("committee.sub.text", L))}</p>
      <div class="mt-5 flex flex-wrap gap-2">
        ${btn(gcal, "calendar-plus", "Google Calendar", "btn-primary")}
        ${btn(webcal, "smartphone", t("committee.sub.apple", L))}
        ${btn(outlook, "calendar", "Outlook")}
      </div>
    </div>
    <div class="rounded-2xl border border-line bg-surface-2/60 p-4 sm:p-5">
      <label class="eyebrow" for="cm-feed-${L}${compact ? "-c" : ""}">${esc(t("committee.sub.url_label", L))}</label>
      <div class="mt-2 flex gap-2">
        <input id="cm-feed-${L}${compact ? "-c" : ""}" class="input min-w-0 flex-1 font-mono text-xs sm:text-sm" type="text" readonly value="${esc(feed)}" onfocus="this.select()">
        <button type="button" class="btn-secondary shrink-0" data-copy="${esc(feed)}" aria-label="${esc(t("committee.sub.copy_aria", L))}">${icon("copy", "size-4")}<span class="hidden sm:inline">${esc(t("common.copy", L))}</span></button>
      </div>
      <p class="mt-3 text-xs leading-relaxed text-muted">${esc(t("committee.sub.url_help", L))}</p>
      <p class="mt-2 text-xs text-muted">${esc(t("committee.sub.other_lang", L))} <a class="link" href="${esc(otherFeed)}" hreflang="${other}">${esc(otherFeed.replace(/^https?:\/\//, ""))}</a></p>
    </div>
  </div>${howto}
</div>`;
  });

  // Committee section sub-navigation: {% committeeNav lang, "events", db %}
  eleventyConfig.addShortcode("committeeNav", function (lang, current, db) {
    const L = lang || "en";
    const n = {
      events: EMPTY ? 0 : normalizeEvents(db?.events?.items || [], {}, L, { monthsBack: 0, monthsAhead: 0 }).filter((e) => !e.past && !e.committee).length,
      documents: documentTabs(EMPTY ? [] : db?.drive?.items, L).total,
      photos: photoAlbums(EMPTY ? [] : db?.drive?.items, L).reduce((s, a) => s + a.count, 0),
      announcements: announcementList(EMPTY ? [] : db?.announcements?.items).length,
    };
    const pages = [
      { key: "meetings", url: "/meetings/", icon: "calendar-clock" },
      { key: "events", url: "/events/", icon: "calendar-days" },
      { key: "documents", url: "/documents/", icon: "folder-open" },
      { key: "photos", url: "/photos/", icon: "images" },
      { key: "announcements", url: "/announcements/", icon: "megaphone" },
    ];
    const links = pages.map((p) => {
      const on = p.key === current;
      const count = n[p.key] ? `<span class="cm-subnav-count">${n[p.key]}</span>` : "";
      return `<a href="${localPath(p.url, L)}" class="cm-subnav-link${on ? " is-current" : ""}"${on ? ' aria-current="page"' : ""}>${icon(p.icon, "size-4")}<span>${esc(t("committee.subnav." + p.key, L))}</span>${count}</a>`;
    });
    // page-overlap (main.css): the pill bar tucks into the hero's faded bottom edge — the
    // shared "page start" for pages whose first block overlaps the hero.
    return `<nav class="container-page page-overlap" aria-label="${esc(t("committee.subnav.aria", L))}"><div class="cm-subnav no-scrollbar">${links.join("")}</div></nav>`;
  });
}
