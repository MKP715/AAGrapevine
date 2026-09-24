// /monthly/ — "Carry the message this month": one month model per month for a rolling window
// (the current month in America/Chicago at build time, plus the next 12). Auto-loaded by
// eleventy.config.js. Owned by src/pages/monthly.njk (hub) and src/pages/monthly-month.njk
// (one poster page per month × language).
//
// Everything here is read from data the site already has — nothing is typed in:
//   db.editorial   Grapevine themes per issue (extra.issue_key), story deadlines (extra.deadline),
//                  La Viña's evergreen suggested topics (extra.evergreen)
//   db.articles    issues[] — the Grapevine issue on the stands, La Viña's bimonthly issue
//   carry          config/carry.yml — the 10 ways and the "put it to work" tips per GV issue
//   db.events      committee meetings, the monthly recurring events (CityWide Dallas booth …),
//                  workshops and assemblies
//   db.weekly_open the Weekly Open meetings (La Viña's from its extra.starts date)
//   db.shop.botm   Book of the Month offers (starts / ends window)
//   site.meeting   the committee meeting rule (used when a month's meeting is not in events.json:
//                  the current month once its meeting is over, or the 13th month)
//
// Globals:   monthlyPages  [{ key: "YYYY-MM", lang }]  → pagination for the per-month pages
//            monthlyKeys   ["YYYY-MM", …]              (13 keys, current month first)
//            monthlyPastPages [{ key, lang, label }]   the 3 months before: redirect stubs to /monthly/
// Filters:   mpMonths(db, carry, site, lang)            → [model, …] for the whole window
//            mpMonth(key, db, carry, site, lang)        → one model
//            mpQr(url, label)                           → QR code SVG (qrSvg from community.js)
// Dev/test:  MONTHLY_NOW=2026-12-15 fixes "today" (the window and the "past" checks).

import { qrSvg } from "./community.js";

const TZ = "America/Chicago";
const LOC = { en: "en-US", es: "es-US" };
const WINDOW = 13;
const PAST_MONTHS = 3;
const WD = { sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6 };

// 12 poster designs keyed by calendar month; 6 structurally different layouts, each used twice
// with its own seasonal palette and motif (src/assets/css/areas/monthly.css).
export const DESIGNS = [
  { id: "frost", layout: "editorial" },   // Jan — winter frost, snowflakes
  { id: "rose", layout: "ticket" },       // Feb — rose & wine, a ticket with a coupon corner
  { id: "sprout", layout: "split" },      // Mar — spring green, calendar strip
  { id: "rain", layout: "cork" },         // Apr — spring showers, pinned notes
  { id: "bloom", layout: "cover" },       // May — flowers, magazine cover
  { id: "notebook", layout: "notebook" }, // Jun — ruled notebook page (a nod to the member's poster)
  { id: "sun", layout: "editorial" },     // Jul — summer sun
  { id: "shore", layout: "ticket" },      // Aug — late-summer sea & sand
  { id: "chalk", layout: "notebook" },    // Sep — chalkboard, back to school
  { id: "autumn", layout: "cork" },       // Oct — autumn corkboard, falling leaves
  { id: "harvest", layout: "split" },     // Nov — harvest plum & gold
  { id: "holiday", layout: "cover" },     // Dec — winter holidays, pine & stars
];

/* ------------------------------------------------------------------ */
/*  Date helpers (Central time)                                        */
/* ------------------------------------------------------------------ */
const pad = (n) => String(n).padStart(2, "0");
const ymdFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });

/** "YYYY-MM-DD" in Central time; a date-only string is returned as is. */
export function chicagoYmd(v) {
  if (!v) return "";
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
  const d = v instanceof Date ? v : new Date(v);
  return isNaN(d) ? "" : ymdFmt.format(d);
}
export function nowDate() {
  const fixed = process.env.MONTHLY_NOW;
  if (fixed && /^\d{4}-\d{2}-\d{2}/.test(fixed)) return new Date(fixed.length === 10 ? fixed + "T12:00:00-05:00" : fixed);
  return new Date();
}
export function addMonths(key, n) {
  const [y, m] = key.split("-").map(Number);
  const i = y * 12 + (m - 1) + n;
  return `${Math.floor(i / 12)}-${pad((i % 12) + 1)}`;
}
const lastDay = (key) => { const [y, m] = key.split("-").map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate(); };
/** 13 month keys: the current Central-time month first. */
export function windowKeys(now = nowDate(), n = WINDOW) {
  const first = chicagoYmd(now).slice(0, 7);
  return Array.from({ length: n }, (_, i) => addMonths(first, i));
}

const utcNoon = (ymd) => new Date(ymd + "T12:00:00Z");
function monthName(key, lang, style = "long") {
  const [y, m] = key.split("-").map(Number);
  return new Intl.DateTimeFormat(LOC[lang] || "en-US", { month: style, timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, 15)));
}
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
/** "May 2027" / "mayo de 2027" */
export function monthLabel(key, lang) {
  const y = key.slice(0, 4);
  return lang === "es" ? `${monthName(key, "es")} de ${y}` : `${monthName(key, "en")} ${y}`;
}
/** "Oct 1" / "1 de octubre" (+ year when it is not `year`) */
export function shortDate(ymd, lang, year) {
  if (!ymd) return "";
  const [y, m, d] = ymd.split("-").map(Number);
  const key = `${y}-${pad(m)}`;
  const withYear = year && String(y) !== String(year);
  if (lang === "es") return `${d} de ${monthName(key, "es")}${withYear ? ` de ${y}` : ""}`;
  return `${monthName(key, "en", "short")} ${d}${withYear ? `, ${y}` : ""}`;
}
/** "Jun 25–27" / "25–27 de junio" (same month), else "Jun 30–Jul 2" / "30 de junio–2 de julio" */
function dayRange(a, b, lang) {
  if (a.slice(0, 7) !== b.slice(0, 7)) return `${shortDate(a, lang)}–${shortDate(b, lang)}`;
  const d1 = Number(a.slice(8)), d2 = Number(b.slice(8));
  return lang === "es" ? `${d1}–${d2} de ${monthName(a.slice(0, 7), "es")}` : `${monthName(a.slice(0, 7), "en", "short")} ${d1}–${d2}`;
}
/** Calendar chip for a Central-time date: { day: "21", mon: "Oct", wd: "Wed" } */
function chip(ymd, lang) {
  const d = utcNoon(ymd);
  const loc = LOC[lang] || "en-US";
  const f = (o) => new Intl.DateTimeFormat(loc, { ...o, timeZone: "UTC" }).format(d).replace(/\.$/, "");
  return { day: String(d.getUTCDate()), mon: cap(f({ month: "short" })), wd: cap(f({ weekday: "short" })), ymd };
}
/** "Wednesday, October 21" / "miércoles 21 de octubre" */
function longDay(ymd, lang) {
  const d = utcNoon(ymd);
  if (lang === "es") {
    const f = (o) => new Intl.DateTimeFormat("es-US", { ...o, timeZone: "UTC" }).format(d);
    return `${f({ weekday: "long" })} ${d.getUTCDate()} de ${f({ month: "long" })}`;
  }
  return new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" }).format(d);
}
/** Central-time clock parts of an instant: { h, mi } */
function chicagoClock(v) {
  const d = new Date(v);
  if (isNaN(d)) return null;
  const p = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit", hourCycle: "h23" }).formatToParts(d);
  return { h: Number(p.find((x) => x.type === "hour").value), mi: Number(p.find((x) => x.type === "minute").value) };
}
/** "5–8 PM" / "5–8 p. m." / "7 PM" (Central time; the zone word is added by the template) */
export function timeRange(start, end, lang) {
  const a = chicagoClock(start);
  if (!a) return "";
  const b = end ? chicagoClock(end) : null;
  const mer = (h) => (lang === "es" ? (h < 12 ? "a. m." : "p. m.") : h < 12 ? "AM" : "PM");
  const hm = (c) => `${c.h % 12 || 12}${c.mi ? ":" + pad(c.mi) : ""}`;
  if (!b || (b.h === a.h && b.mi === a.mi)) return `${hm(a)} ${mer(a.h)}`;
  if ((a.h < 12) === (b.h < 12)) return `${hm(a)}–${hm(b)} ${mer(b.h)}`;
  return `${hm(a)} ${mer(a.h)}–${hm(b)} ${mer(b.h)}`;
}

/* ------------------------------------------------------------------ */
/*  Committee meeting rule (fallback when events.json has no date)     */
/* ------------------------------------------------------------------ */
function nthWeekday(y, m0, weekday, n) {
  if (n === -1) {
    const last = new Date(Date.UTC(y, m0 + 1, 0));
    return last.getUTCDate() - ((last.getUTCDay() - weekday + 7) % 7);
  }
  const first = new Date(Date.UTC(y, m0, 1)).getUTCDay();
  const day = 1 + ((weekday - first + 7) % 7) + (n - 1) * 7;
  return day <= new Date(Date.UTC(y, m0 + 1, 0)).getUTCDate() ? day : null;
}
function chicagoInstant(ymd, hhmm) {
  const [h, mi] = String(hhmm || "19:00").split(":").map(Number);
  const [y, m, d] = ymd.split("-").map(Number);
  const guess = new Date(Date.UTC(y, m - 1, d, h, mi || 0));
  const tz = new Intl.DateTimeFormat("en-US", { timeZone: TZ, timeZoneName: "shortOffset" }).formatToParts(guess).find((p) => p.type === "timeZoneName")?.value || "GMT-6";
  const off = Number((tz.match(/GMT([+-]\d+)/) || [0, -6])[1]);
  return new Date(guess.getTime() - off * 3600e3).toISOString();
}
export function meetingByRule(key, meeting = {}) {
  const wd = WD[String(meeting.weekday || "wednesday").toLowerCase()] ?? 3;
  const n = Number(meeting.week_of_month || 3);
  const [y, m] = key.split("-").map(Number);
  const d = nthWeekday(y, m - 1, wd, n);
  if (!d) return null;
  const ymd = `${key}-${pad(d)}`;
  const skip = new Set((meeting.skip_dates || []).map((s) => (s instanceof Date ? s.toISOString().slice(0, 10) : String(s))));
  if (skip.has(ymd)) return null;
  return { ymd, start: chicagoInstant(ymd, meeting.start || "19:00"), end: chicagoInstant(ymd, meeting.end || "20:00") };
}

/* ------------------------------------------------------------------ */
/*  Text helpers                                                       */
/* ------------------------------------------------------------------ */
const tr = (item, field, lang) => {
  if (!item) return "";
  const i = item.i18n && item.i18n[field];
  if (i && typeof i[lang] === "string" && i[lang].trim()) return i[lang];
  if (i && typeof i.en === "string" && i.en.trim()) return i.en;
  return item[field] || (item.extra && item.extra[field]) || "";
};
const pair = (p, lang) => (p ? p[lang] || p.en || p.es || "" : "");
/** A poster hook: the first question of the summary (short), else its first sentence. */
export function hook(summary, max = 110) {
  const s = String(summary || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  const parts = s.match(/[^.?!]+[.?!]+/g) || [s];
  const q = parts.map((p) => p.trim()).find((p) => p.endsWith("?") && p.length <= max);
  const pick = q || parts[0].trim();
  return pick.length > max ? pick.slice(0, max).replace(/\s+\S*$/, "") + "…" : pick;
}
const slug = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/* ------------------------------------------------------------------ */
/*  The month model                                                    */
/* ------------------------------------------------------------------ */
function eventDays(ev) {
  const ex = ev.extra || {};
  const start = chicagoYmd(ex.start || ev.date);
  let end = ex.end ? chicagoYmd(ex.end) : start;
  // An end at midnight (timed events that finish at 00:00) belongs to the day before; keep ≥ start.
  if (end && end > start && typeof ex.end === "string" && !/^\d{4}-\d{2}-\d{2}$/.test(ex.end)) {
    const c = chicagoClock(ex.end);
    if (c && c.h === 0 && c.mi === 0) end = chicagoYmd(new Date(Date.parse(ex.end) - 1));
  }
  if (!end || end < start) end = start;
  return { start, end };
}

/**
 * One month's model.
 * @param {string} key   "YYYY-MM"
 * @param {object} db    { editorial, articles, events, weekly_open, shop }
 * @param {object} carry the `carry` global (config/carry.yml)
 * @param {object} site  the `site` global (meeting, links, url)
 * @param {string} lang  "en" | "es"
 * @param {Date}   now
 */
export function monthModel(key, db = {}, carry = {}, site = {}, lang = "en", now = nowDate()) {
  const L = lang === "es" ? "es" : "en";
  const year = key.slice(0, 4);
  const first = `${key}-01`;
  const last = `${key}-${pad(lastDay(key))}`;
  const nextFirst = `${addMonths(key, 1)}-01`;
  const today = chicagoYmd(now);
  const mNum = Number(key.slice(5, 7));
  const design = DESIGNS[mNum - 1];
  const editorial = (db.editorial && db.editorial.items) || [];
  const gvEd = editorial.filter((i) => i && i.extra && i.extra.publication === "gv");
  const issues = (db.articles && db.articles.issues) || [];

  /* Grapevine issue on the stands (theme from the editorial calendar; else the synced issue) */
  const gvIssue = issues.find((i) => i && i.publication === "gv" && i.key === key) || null;
  const themed = gvEd.filter((i) => i.extra.issue_key === key);
  const themes = themed.map((i) => tr(i, "title", L)).filter(Boolean);
  const theme = themes.length ? themes.join(" / ") : gvIssue ? tr(gvIssue, "theme", L) : "";
  const gv = theme ? {
    key, theme, themes, label: monthLabel(key, L),
    summary: themed.length ? tr(themed[0], "summary", L) : tr(gvIssue, "description", L),
    url: gvIssue ? gvIssue.url : "", cover: gvIssue ? gvIssue.cover || "" : "",
    machine: L === "es" && themed.some((i) => (i.machine || []).includes("es")),
  } : null;

  /* La Viña's bimonthly issue, when the synced one covers this month */
  const lvIssue = issues.find((i) => i && i.publication === "lv" && i.key && (i.key === key || addMonths(i.key, 1) === key)) || null;
  const lv = lvIssue ? { key: lvIssue.key, theme: tr(lvIssue, "theme", L), label: tr(lvIssue, "label", L), url: lvIssue.url || "", cover: lvIssue.cover || "" } : null;

  /* "Put it to work" tips for this month's Grapevine issue */
  const wayById = carry.wayById || {};
  const tips = ((carry.tips || {})[key] || []).filter((t) => wayById[t.way]).map((t) => ({
    way: t.way, icon: wayById[t.way].icon || "circle", title: pair(wayById[t.way].title, L), text: pair(t.text, L),
  }));

  /* Story deadlines from the 1st of this month through the 1st of next month (still open) */
  const deadlines = gvEd
    .filter((i) => i.extra.deadline && i.extra.deadline >= first && i.extra.deadline <= nextFirst && i.extra.deadline >= today)
    .sort((a, b) => a.extra.deadline.localeCompare(b.extra.deadline) || String(a.title).localeCompare(String(b.title)))
    .map((i) => ({
      id: i.id, theme: tr(i, "title", L), themeEn: i.title, hook: L === "en" ? hook(i.summary) : "",
      summary: tr(i, "summary", L),
      issueKey: i.extra.issue_key, issueLabel: i.extra.issue_key ? monthLabel(i.extra.issue_key, L) : tr(i, "issue_label", L),
      due: i.extra.deadline, dueLabel: shortDate(i.extra.deadline, L, year), dueLong: shortDate(i.extra.deadline, L, "0"),
      submitUrl: i.extra.submit_url || "", guidelinesUrl: i.extra.guidelines_url || "",
      machine: L === "es" && (i.machine || []).includes("es"),
    }));

  /* La Viña suggested topics — 3, rotating by month */
  const lvTopics = editorial.filter((i) => i && i.extra && i.extra.publication === "lv" && i.extra.evergreen)
    .sort((a, b) => String(a.id).localeCompare(String(b.id)));
  const topics = [];
  if (lvTopics.length) {
    const idx = Number(year) * 12 + mNum;
    const n = Math.min(3, lvTopics.length);
    for (let i = 0; i < n; i++) {
      const it = lvTopics[(idx * 3 + i) % lvTopics.length];
      if (!topics.includes(it)) topics.push(it);
    }
  }
  const lvTopicList = topics.map((i) => ({ id: i.id, es: tr(i, "title", "es"), text: tr(i, "title", L) }));

  /* Book of the Month offers whose window overlaps the month */
  const botm = (((db.shop && db.shop.botm) || []).filter((b) => b && b.ends && (!b.starts || b.starts <= last) && b.ends >= first))
    .sort((a, b) => (a.pub === (L === "es" ? "lv" : "gv") ? -1 : 1) - (b.pub === (L === "es" ? "lv" : "gv") ? -1 : 1))
    .map((b) => ({
      // The book's own title (a Grapevine book is in English, a La Viña book in Spanish), never a translation.
      id: b.id, pub: b.pub, title: b.title || tr(b, "title", L), lang: b.lang || (b.pub === "lv" ? "es" : "en"), discount: b.discount_pct || null,
      ends: b.ends, endsLabel: shortDate(b.ends, L, year), starts: b.starts, past: b.ends < today,
    }));

  /* The shared offer when every book has the same discount and end date (the poster says it once) */
  const botmOffer = botm.length > 1 && botm.every((b) => b.discount === botm[0].discount && b.ends === botm[0].ends)
    ? { discount: botm[0].discount, endsLabel: botm[0].endsLabel } : null;

  /* Dates: committee meeting, the monthly recurring events, other events */
  const events = ((db.events && db.events.items) || []).filter((e) => e && e.kind === "event");
  const inMonth = (e) => { const d = eventDays(e); return d.start && d.start <= last && d.end >= first; };
  const zone = L === "es" ? "(hora del Centro)" : "Central";
  const evView = (e, extra = {}) => {
    const d = eventDays(e);
    const ex = e.extra || {};
    const timed = !ex.all_day && ex.start && !/^\d{4}-\d{2}-\d{2}$/.test(ex.start);
    const shownDay = d.start < first ? first : d.start;
    return {
      id: e.id, title: tr(e, "title", L), url: e.url || "",
      ymd: d.start, endYmd: d.end, chip: chip(shownDay, L), dayLabel: shortDate(shownDay, L, year), day: longDay(d.start, L),
      endDay: d.end !== d.start ? longDay(d.end, L) : "",
      range: d.end !== d.start ? dayRange(d.start, d.end, L) : "",
      time: timed ? `${timeRange(ex.start, ex.end, L)}` : "", zone: timed ? zone : "",
      city: ex.city || "", online: !!ex.online || /zoom/i.test(ex.location || ""),
      tentative: !!ex.tentative, past: d.end < today, ...extra,
    };
  };
  let meetingEv = events.find((e) => e.id && e.id.startsWith(`ev:committee:${key}-`));
  let committee = null;
  if (meetingEv) committee = evView(meetingEv, { kind: "committee" });
  else {
    const r = meetingByRule(key, site.meeting || {});
    if (r) committee = evView({ id: `ev:committee:${r.ymd}`, kind: "event", url: "/meeting/", title: "", extra: { start: r.start, end: r.end, location: "Zoom", online: true } }, { kind: "committee" });
  }
  if (committee) {
    committee.title = "";
    committee.platform = (site.meeting && site.meeting.platform) || "Zoom";
  }
  /* The recurring series (config/site.yml recurring_events:) — events.json lists only the next
     `months_ahead` dates, so a later month's date is worked out from the same rule (like the
     committee meeting from site.meeting), with the series' own title and repeat line. */
  const recEvents = events.filter((e) => e.extra && e.extra.recurring && e.extra.series);
  const recIn = recEvents.filter(inMonth);
  for (const spec of Array.isArray(site.recurring_events) ? site.recurring_events : []) {
    if (!spec || !spec.key || spec.enabled === false) continue;
    const series = recEvents.filter((e) => e.extra.series === spec.key);
    const lastListed = series.reduce((mx, e) => (eventDays(e).start > mx ? eventDays(e).start : mx), "");
    if (!series.length || series.some(inMonth) || lastListed >= first) continue;
    const r = meetingByRule(key, { week_of_month: spec.week_of_month, weekday: spec.weekday, start: spec.start, end: spec.end, skip_dates: spec.skip_dates || [] });
    if (!r) continue;
    const tpl = series[series.length - 1];
    recIn.push({ ...tpl, id: `ev:recurring:${spec.key}:${r.ymd}`, date: r.start, extra: { ...tpl.extra, start: r.start, end: r.end } });
  }
  const recurring = recIn
    .sort((a, b) => eventDays(a).start.localeCompare(eventDays(b).start))
    .map((e) => evView(e, { kind: "recurring", series: e.extra.series, label: tr(e, "recurrence_label", L) }));
  const other = events.filter((e) => e.source !== "calendar" && !(e.extra && e.extra.recurring) && !String(e.id).startsWith("ev:committee:") && inMonth(e))
    .sort((a, b) => eventDays(a).start.localeCompare(eventDays(b).start))
    .map((e) => evView(e, { kind: "event" }));
  const dates = [committee, ...recurring, ...other].filter(Boolean).sort((a, b) => a.chip.ymd.localeCompare(b.chip.ymd));

  /* Weekly open meetings (La Viña's only from its start date) */
  const weekly = ((db.weekly_open && db.weekly_open.items) || []).filter((w) => {
    if (!w || w.status === "gone") return false;
    const starts = w.extra && w.extra.starts;
    return !starts || starts <= last;
  }).map((w) => {
    const starts = (w.extra && w.extra.starts) || "";
    return {
      id: w.id, pub: w.source === "lavina" ? "lv" : "gv", title: tr(w, "title", L), when: tr(w, "when", L) || tr(w, "day", L),
      startsLabel: starts && starts >= first ? shortDate(starts, L) : "", new: !!(starts && starts >= first && starts <= last),
    };
  }).sort((a, b) => (a.pub === (L === "es" ? "lv" : "gv") ? -1 : 1) - (b.pub === (L === "es" ? "lv" : "gv") ? -1 : 1));

  /* Mini calendar (split layout): leading blanks + days with marks */
  const [yy, mm] = key.split("-").map(Number);
  const offset = new Date(Date.UTC(yy, mm - 1, 1)).getUTCDay();
  const marks = {};
  const mark = (ymd, type) => { if (ymd && ymd.startsWith(key)) { const d = Number(ymd.slice(8)); (marks[d] = marks[d] || []).includes(type) || marks[d].push(type); } };
  for (const d of dates) {
    for (let c = d.chip.ymd; c <= (d.endYmd || d.chip.ymd) && c <= last; c = chicagoYmd(new Date(utcNoon(c).getTime() + 864e5))) mark(c, d.kind);
  }
  for (const d of deadlines) mark(d.due, "deadline");
  const weekdays = Array.from({ length: 7 }, (_, i) => new Intl.DateTimeFormat(LOC[L], { weekday: "narrow", timeZone: "UTC" }).format(new Date(Date.UTC(2026, 1, 1 + i))));
  const calendar = { offset, days: lastDay(key), marks, weekdays };

  /* Density: how much the poster holds (the CSS tightens type for busy months) */
  const load = deadlines.length * 3 + Math.min(tips.length, 3) * 2 + botm.length * 1.5 + dates.length * 1.2 + weekly.length + (L === "es" ? 2 : 0);
  const density = load > 20 ? "dense" : load > 15 ? "snug" : "roomy";

  const path = `/monthly/${key}/`;
  const url = (L === "es" ? "/es" : "") + path;
  return {
    key, lang: L, year, month: mNum, first, last,
    name: cap(monthName(key, L)), monthName: monthName(key, L), label: monthLabel(key, L), title: cap(monthLabel(key, L)),
    design: design.id, layout: design.layout, density,
    isCurrent: key === today.slice(0, 7),
    gv, lv, tips, deadlines, lvTopics: lvTopicList, botm, botmOffer,
    committee, recurring, events: other, dates, weekly, calendar,
    url, absUrl: String(site.url || "").replace(/\/$/, "") + url,
    prev: addMonths(key, -1), next: addMonths(key, 1),
    fileName: `neta65-grapevine-${key}-${L}.png`,
    slug: slug(key),
  };
}

export default function (eleventyConfig) {
  eleventyConfig.addGlobalData("monthlyKeys", () => windowKeys());
  eleventyConfig.addGlobalData("monthlyPages", () => {
    const keys = windowKeys();
    return ["en", "es"].flatMap((lang) => keys.map((key) => ({ key, lang })));
  });
  // The last PAST_MONTHS months keep a small redirect page (src/pages/monthly-past.njk → /monthly/),
  // so printed posters' QR codes and shared links never land on "page not found".
  eleventyConfig.addGlobalData("monthlyPastPages", () => {
    const cur = windowKeys(nowDate(), 1)[0];
    const keys = Array.from({ length: PAST_MONTHS }, (_, i) => addMonths(cur, -(i + 1)));
    return ["en", "es"].flatMap((lang) => keys.map((key) => ({ key, lang, label: monthLabel(key, lang) })));
  });

  // The window's models are built once per language per build (the hub and 26 pages share them).
  let cache = new Map();
  eleventyConfig.on("eleventy.before", () => { cache = new Map(); });
  const months = (db, carry, site, lang) => {
    const k = lang;
    if (!cache.has(k)) {
      const now = nowDate();
      const keys = windowKeys(now);
      cache.set(k, keys.map((key, i) => {
        const m = monthModel(key, db || {}, carry || {}, site || {}, lang, now);
        m.index = i;
        m.hasPrev = i > 0;
        m.hasNext = i < keys.length - 1;
        return m;
      }));
    }
    return cache.get(k);
  };
  eleventyConfig.addFilter("mpMonths", (db, carry, site, lang) => months(db, carry, site, lang));
  eleventyConfig.addFilter("mpMonth", (key, db, carry, site, lang) => months(db, carry, site, lang).find((m) => m.key === key) || monthModel(key, db || {}, carry || {}, site || {}, lang));
  eleventyConfig.addFilter("mpQr", (url, label = "") => qrSvg(url, { label, cls: "mp-qr-svg", margin: 2 }));
}
