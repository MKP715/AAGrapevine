// Filters for the "community" pages: What's New, Weekly Digest, Districts,
// Share Kit (QR), Status, RSS feeds and sitemap.
//
// Everything here is pure data shaping (no network), so the pages keep
// working with empty data and the build never fails because a source was
// down. Plain-text messages (WhatsApp / e-mail / district report) are built
// here rather than in Nunjucks because exact line breaks matter in them.
import QRCode from "qrcode";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

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
  drive: { icon: "folder-open", tone: "vine", page: "/documents/", emoji: "📁" },
  event: { icon: "calendar-days", tone: "vine", page: "/events/", emoji: "📅" },
  topic: { icon: "pen-line", tone: "lv", page: "/contribute/", emoji: "✍️" },
  other: { icon: "sparkles", tone: "muted", page: "/whats-new/", emoji: "•" },
};
// Filter-chip order on What's New.
export const CHIP_ORDER = ["article", "pdf", "episode", "video", "post", "drive", "announcement", "event", "topic", "other"];
// Section order in the digest (events/deadlines have their own sections).
export const DIGEST_ORDER = ["announcement", "article", "episode", "video", "post", "pdf", "drive"];

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
  if (lang === "es") s = s.charAt(0).toUpperCase() + s.slice(1);
  return s;
}
const fmtShortDay = (v, lang) => fmt(v, lang, { weekday: "short", month: "short", day: "numeric" });
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
    _isGroup: isGroup,
  };
}

function eventStart(ev) {
  return ev?.extra?.start || ev?.date || null;
}

/** "Sat, Oct 17" or "Wed, Oct 21 · 7:00 PM CDT" for an event. */
export function eventWhen(ev, lang) {
  const s = eventStart(ev);
  if (!s) return "";
  const allDay = ev?.extra?.all_day || isDateOnly(s);
  return allDay ? fmtShortDay(s, lang) : `${fmtShortDay(s, lang)} · ${fmtTime(s, lang)}`;
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
/*  Weekly digest                                                      */
/* ------------------------------------------------------------------ */
/**
 * Items that are "news" in the last `days` days. whatsnew.json is the one
 * source of truth for that (its `wn_date` applies all the pipeline's rules:
 * no launch-day back catalog, no undated PDFs, magazine issues dated to the
 * day they appeared …), so the digest, the district report and What's New
 * always agree. It holds the newest 150 items — far more than a month.
 */
function recentNews(db, days, now) {
  const since = now - days * DAY;
  return (db?.whatsnew?.items || [])
    .filter((i) => i && i.id && i.status !== "gone")
    .map((i) => prep(i, now))
    .filter((i) => i._group !== "event" && i._group !== "topic" && i._group !== "other")
    .filter((i) => { const t = ms(i._when); return t >= since && t <= now + DAY; })
    .filter((i) => !(i.kind === "announcement" && i.extra?.expires && ms(i.extra.expires) + DAY < now))
    .sort((a, b) => ms(b._when) - ms(a._when));
}

export function buildDigest(db, meeting, { days = 7, eventDays = 30, deadlineDays = 75, now = Date.now() } = {}) {
  const since = now - days * DAY;
  const recent = recentNews(db, days, now);
  const groups = DIGEST_ORDER.map((key) => ({ key, ...GROUPS[key], items: recent.filter((i) => i._group === key) })).filter((g) => g.items.length);

  const events = (db?.events?.items || [])
    .filter((e) => e && e.status !== "gone" && e.category !== "committee")
    .filter((e) => { const t = ms(eventStart(e)); return t && t >= now - 6 * 3600e3 && t <= now + eventDays * DAY; })
    .sort((a, b) => ms(eventStart(a)) - ms(eventStart(b)))
    .map((e) => prep(e, now));

  const todayYmd = ymdChicago(new Date(now));
  const deadlines = (db?.editorial?.items || [])
    .filter((e) => e?.extra?.deadline && e.extra.deadline >= todayYmd && ms(e.extra.deadline) <= now + deadlineDays * DAY)
    .sort((a, b) => ms(a.extra.deadline) - ms(b.extra.deadline));

  // La Viña's suggested themes have no deadline ("evergreen"): suggest two,
  // rotating every week so the digest doesn't repeat the same ones.
  const evergreen = (db?.editorial?.items || []).filter((e) => e?.extra?.evergreen && e.extra.publication === "lv");
  const week = Math.floor(now / (7 * DAY));
  const lvThemes = evergreen.length
    ? [...new Set([(week * 2) % evergreen.length, (week * 2 + 1) % evergreen.length])].map((i) => evergreen[i])
    : [];

  return {
    since: new Date(since).toISOString(),
    until: new Date(now).toISOString(),
    days,
    eventDays,
    groups,
    total: recent.length,
    events,
    deadlines,
    lvThemes,
    next: meeting?.next || null,
  };
}

/**
 * Plain-text digest for WhatsApp ("whatsapp") or e-mail ("email").
 * `langs` = ["en"], ["es"] or ["en","es"] (bilingual: both languages in one message).
 * `media` = the shared podcast/video title helpers of eleventy/filters/media.js
 * ({ title, cleanTitle, videoKind } — see mediaHelpers below), so episodes and
 * videos read as on Home, Listen and Watch (no "[Season 11, Episode 12]" tail).
 */
export function digestText(dg, langs, style, site, t, media = {}) {
  const L = Array.isArray(langs) ? langs : [langs];
  const wa = style === "whatsapp";
  const main = L[0];
  const both = (key, vars) => L.map((l) => t(key, l, vars)).filter((v, i, a) => a.indexOf(v) === i).join(" / ");
  const head = (s) => (wa ? `*${s}*` : `${s.toUpperCase()}\n${"-".repeat(Math.min(s.length, 60))}`);
  const bullet = wa ? "•" : "-";
  const perGroup = wa ? 5 : 8;
  const titleOf = (item, l) => {
    const raw = clean(pickLang(item, "title", l));
    if (!isMediaItem(item) || !media.title) return raw;
    // A Weekly Open recording's display title drops the show name ("Meeting of
    // September 16, 2026") because the web pages show the show next to it; a
    // text message has no such label, so there only the numbering tail goes.
    if (media.cleanTitle && media.videoKind && media.videoKind(item) === "weekly") return clean(media.cleanTitle(raw)) || raw;
    return clean(media.title(item, l)) || raw;
  };
  const titleLines = (item) => {
    const ts = L.map((l) => titleOf(item, l)).filter((v, i, a) => v && a.indexOf(v) === i);
    return ts.length ? ts : [clean(item.title)];
  };
  const out = [];

  const title = `NETA 65 Grapevine / La Viña — ${both("community.digest.masthead")}`;
  out.push(wa ? `*${title}*` : title);
  out.push(wa ? `_${L.map((l) => fmtRange(dg.since, dg.until, l)).filter((v, i, a) => a.indexOf(v) === i).join(" / ")}_` : L.map((l) => fmtRange(dg.since, dg.until, l)).filter((v, i, a) => a.indexOf(v) === i).join(" / "));
  out.push("");

  if (!dg.groups.length) {
    out.push(both("community.digest.text_quiet"));
    out.push("");
  }
  for (const g of dg.groups) {
    const label = both(`community.group.${g.key}`);
    out.push(wa ? `${g.emoji} ${head(label)} (${g.items.length})` : head(`${label} (${g.items.length})`));
    // A whole magazine issue that appeared this week is ONE line ("Grapevine —
    // October 2026: 27 new stories") instead of 27 titles.
    let rest = g.items;
    let shown = 0;
    if (g.key === "article") {
      rest = [];
      for (const b of bundleIssues(g.items)) {
        if (!b._bundle) { rest.push(b); continue; }
        const pub = b.extra?.publication === "lv" || b.source === "lavina" ? "La Viña" : "Grapevine";
        const line = L.map((l) => t("community.digest.text_issue", l, { pub, issue: issueLabelOf(b, l), n: b._items.length }))
          .filter((v, i, a) => a.indexOf(v) === i);
        out.push(`${bullet} ${line[0]}`);
        for (const r of line.slice(1)) out.push(`  ${r}`);
        out.push(`  ${absUrl(langPath("/read/", main), site)}`);
        shown += b._items.length;
      }
    }
    const singles = uniqByTitle(rest, main).slice(0, perGroup);
    for (const item of singles) {
      const [first, ...others] = titleLines(item);
      out.push(`${bullet} ${first}`);
      for (const r of others) out.push(`  ${r}`);
      out.push(`  ${absUrl(hrefOf(item, main), site)}`);
    }
    shown += singles.length + (rest.length - uniqByTitle(rest, main).length);
    if (g.items.length > shown) {
      out.push(`${wa ? "➕" : "+"} ${both("community.digest.text_more", { n: g.items.length - shown })} ${absUrl(langPath(g.page, main), site)}`);
    }
    out.push("");
  }

  if (dg.events.length) {
    const label = both("community.digest.coming_up");
    out.push(wa ? `📅 ${head(label)}` : head(label));
    for (const ev of dg.events.slice(0, 8)) {
      const [first, ...rest] = titleLines(ev);
      const where = ev.extra?.location || ev.extra?.city || "";
      out.push(`${bullet} ${eventWhen(ev, main)} — ${first}${where ? ` (${where})` : ""}`);
      for (const r of rest) out.push(`  ${r}`);
      if (ev.url) out.push(`  ${absUrl(hrefOf(ev, main), site)}`);
    }
    out.push("");
  }

  if (dg.next) {
    const label = both("community.digest.next_meeting");
    const when = L.map((l) => `${fmtShortDay(dg.next.start, l)} · ${fmtTime(dg.next.start, l)}`).filter((v, i, a) => a.indexOf(v) === i).join(" / ");
    out.push(wa ? `🗓️ ${head(label)}` : head(label));
    out.push(`${when} — Zoom`);
    out.push(both("community.digest.text_all_welcome"));
    out.push(absUrl(langPath("/meeting/", main), site));
    out.push("");
  }

  if (dg.deadlines.length || dg.lvThemes?.length) {
    const label = both("community.digest.deadlines");
    out.push(wa ? `✍️ ${head(label)}` : head(label));
    for (const d of dg.deadlines.slice(0, 6)) {
      const pub = d.extra?.publication === "lv" ? "La Viña" : "Grapevine";
      const theme = L.map((l) => clean(pickLang(d, "title", l))).filter((v, i, a) => v && a.indexOf(v) === i).join(" / ");
      out.push(`${bullet} ${fmtShortDay(d.extra.deadline, main)} — "${theme}" (${pub}, ${issueLabelOf(d, main)})`);
    }
    if (dg.lvThemes?.length) {
      const themes = dg.lvThemes.map((d) => `"${clean(pickLang(d, "title", main))}"`).join(", ");
      out.push(`${bullet} ${both("community.digest.text_lv_anytime")} ${themes}`);
    }
    out.push(`  ${absUrl(langPath("/contribute/", main), site)}`);
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
/*  District report template                                           */
/* ------------------------------------------------------------------ */
/**
 * Newest issue of a publication: articles.json `issues` (newest first, with
 * rule-built i18n labels) when present, else derived from the articles.
 * `theme` is quoted as published (GV in English, LV in Spanish).
 */
function latestIssue(articles, pub) {
  const iss = (articles?.issues || []).find((i) => i && i.publication === pub && i.key);
  if (iss) {
    const count = (articles.items || []).filter((a) => a?.extra?.publication === pub && a.extra.issue_key === iss.key).length;
    return { key: iss.key, label: iss.label || iss.key, lang: iss.lang, i18n: { issue_label: iss.i18n?.label, topic: iss.i18n?.theme }, topic: iss.theme || "", count };
  }
  const list = (articles?.items || []).filter((a) => a?.extra?.publication === pub && a.extra.issue_key);
  if (!list.length) return null;
  const key = list.map((a) => a.extra.issue_key).sort().pop();
  const inIssue = list.filter((a) => a.extra.issue_key === key);
  const topic = inIssue.map((a) => a.extra.issue_theme || a.extra.topic).find(Boolean) || "";
  return { key, label: inIssue[0].extra.issue_label || key, i18n: { issue_label: inIssue[0].i18n?.issue_label }, topic, count: inIssue.length };
}

export function reportData(db, meeting, now = Date.now()) {
  const news = recentNews(db, 30, now);
  const count = (g) => news.filter((i) => i._group === g).length;
  const dg = buildDigest(db, meeting, { days: 30, eventDays: 60, deadlineDays: 90, now });
  return {
    articles: count("article"),
    episodes: count("episode"),
    videos: count("video"),
    pdfs: count("pdf"),
    gv: latestIssue(db?.articles, "gv"),
    lv: latestIssue(db?.articles, "lv"),
    events: dg.events.slice(0, 4),
    deadlines: dg.deadlines.slice(0, 4),
    lvThemes: dg.lvThemes || [],
    next: meeting?.next || null,
  };
}

export function reportText(rd, lang, site, t) {
  const T = (k, v) => t(`community.report.${k}`, lang, v);
  const url = (p) => absUrl(langPath(p, lang), site);
  const out = [];
  out.push(`📋 ${T("t_title")}`);
  out.push(T("t_byline"));
  out.push("");
  let n = 0;
  const num = () => `${++n}.`;

  // "56 articles, 8 podcast episodes, 10 videos and 1 service PDF" — zero counts are left out.
  const counts = [["article", rd.articles], ["episode", rd.episodes], ["video", rd.videos], ["pdf", rd.pdfs]]
    .filter(([, n]) => n > 0)
    .map(([k, n]) => T(n === 1 ? `n_${k}_one` : `n_${k}`, { n }));
  const list = counts.length > 1 ? `${counts.slice(0, -1).join(", ")} ${T("and")} ${counts[counts.length - 1]}` : counts[0] || "";
  out.push(`${num()} ${list ? T("t_new", { list }) : T("t_new_none")}`);
  out.push(`   ${url("/whats-new/")}`);

  if (rd.gv || rd.lv) {
    const parts = [];
    // Issue themes come from the magazines themselves (GV in English, LV in
    // Spanish): shown in the report language when a translation exists,
    // otherwise quoted as published.
    for (const [name, iss] of [["Grapevine", rd.gv], ["La Viña", rd.lv]]) {
      if (!iss) continue;
      const theme = clean(iss.i18n?.topic?.[lang] || iss.topic);
      parts.push(`${name} — ${iss.i18n?.issue_label?.[lang] || issueLabel(iss.label, lang)}${theme ? ` ("${theme}")` : ""}`);
    }
    out.push(`${num()} ${T("t_issues")} ${parts.join("; ")}.`);
    out.push(`   ${url("/read/")}`);
  }

  out.push(`${num()} ${T("t_write")}`);
  if (rd.deadlines.length) {
    for (const d of rd.deadlines) {
      const pub = d.extra?.publication === "lv" ? "La Viña" : "Grapevine";
      out.push(`   • "${clean(pickLang(d, "title", lang))}" — ${pub} ${issueLabelOf(d, lang)} — ${T("t_due", { date: fmtShortDay(d.extra.deadline, lang) })}`);
    }
  } else out.push(`   • ${T("t_no_deadlines")}`);
  if (rd.lvThemes?.length) {
    out.push(`   • ${T("t_lv_anytime")} ${rd.lvThemes.map((d) => `"${clean(pickLang(d, "title", lang))}"`).join(", ")}`);
  }
  out.push(`   ${url("/contribute/")}`);

  out.push(`${num()} ${T("t_events")}`);
  if (rd.events.length) {
    for (const ev of rd.events) {
      const where = ev.extra?.location || ev.extra?.city || "";
      out.push(`   • ${eventWhen(ev, lang)} — ${clean(pickLang(ev, "title", lang))}${where ? ` (${where})` : ""}`);
    }
  } else out.push(`   • ${T("t_no_events")}`);
  out.push(`   ${url("/events/")}`);

  if (rd.next) {
    out.push(`${num()} ${T("t_meeting", { date: fmtShortDay(rd.next.start, lang), time: fmtTime(rd.next.start, lang) })}`);
    out.push(`   ${url("/meeting/")}`);
  }
  out.push(`${num()} ${T("t_ask")}`);
  out.push("");
  out.push(T("t_contact", { email: site?.contact_email || "" }));
  return out.join("\n") + "\n";
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
  const known = num(c.known_pages) || 0;
  const crawled = Math.min(num(c.crawled_pages) || 0, known || Infinity);
  const perRun = num(c.last_run_pages) || 0;
  const remaining = num(c.queue_remaining) ?? num(c.never_crawled) ?? Math.max(0, known - crawled);
  const est = num(c.est_days_to_full);
  let daysLeft = null;
  if (known > 0 && remaining === 0) daysLeft = 0;
  else if (est !== null && est > 0) daysLeft = Math.max(1, Math.ceil(est));
  else if (perRun > 0) daysLeft = Math.ceil(remaining / perRun);
  const tr = status?.translations || {};
  return {
    generated: status?.generated || null,
    sources,
    okCount: sources.filter((s) => s.state === "ok").length,
    failedCount: sources.filter((s) => s.state === "failed").length,
    totalItems: sources.reduce((a, s) => a + s.count, 0),
    found7d: sources.reduce((a, s) => a + (Number(s.new_7d) || 0), 0),
    crawl: {
      known,
      crawled: known ? crawled : num(c.crawled_pages) || 0,
      pct: known ? Math.round((crawled / known) * 1000) / 10 : 0,   // one decimal
      pdfs: num(c.pdfs) || 0,
      thumbs: num(c.pdfs_with_thumbs) || 0,
      perRun,
      remaining,
      daysLeft,
      errors: num(c.page_errors) || 0,
      updated: c.updated || null,
      hasData: known > 0 || crawled > 0,
    },
    translations: {
      cached: num(tr.cached) || 0,
      pending: num(tr.pending) || 0,
      rejected: num(tr.rejected_by_guard) || 0,
      glossary: num(tr.glossary_entries) || 0,
    },
  };
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

  // Filters are prefixed "cm" (community) so they can never clash with another
  // area's filters; qrSvg keeps its plain name. QR code as inline SVG:
  //   {{ url | qrSvg("Label", "classes") | safe }}
  eleventyConfig.addFilter("qrSvg", (text, label = "", cls = "") => qrSvg(text, { label, cls }));
  eleventyConfig.addFilter("cmQrSymbol", (text, id) => qrSymbol(text, id));
  // Same SVG as a data: URI (download link). Encoded so it is safe in href="".
  eleventyConfig.addFilter("cmSvgDataUri", (svg) => "data:image/svg+xml;charset=utf-8," + encodeURIComponent(String(svg || "")));

  eleventyConfig.addFilter("cmWnPrepare", (items) => prepareWhatsNew(items));
  eleventyConfig.addFilter("cmWnDays", (prepared, lang) => whatsNewDays(prepared, lang));
  eleventyConfig.addFilter("cmWnChips", (prepared) => chipList(prepared));
  eleventyConfig.addFilter("cmWnCountRecent", (prepared, days = 7) => countRecent(prepared, days));
  eleventyConfig.addFilter("cmHref", (item, lang) => hrefOf(item, lang));
  eleventyConfig.addFilter("cmTeaser", (summary, title) => teaser(summary, title));
  eleventyConfig.addFilter("cmEventWhen", (ev, lang) => eventWhen(ev, lang));
  eleventyConfig.addFilter("cmDateRange", (a, b, lang) => fmtRange(a, b, lang));
  eleventyConfig.addFilter("cmIssueLabel", (label, lang) => issueLabel(label, lang));

  eleventyConfig.addFilter("cmDigest", (db, meeting, days = 7, eventDays = 30) => buildDigest(db, meeting, { days, eventDays }));
  // The media filters (media.js) register after this file (alphabetical load order),
  // so they are looked up when the digest renders, not now. Missing → raw titles.
  const mediaHelpers = () => ({
    title: eleventyConfig.getFilter("mediaTitle"),
    cleanTitle: eleventyConfig.getFilter("mediaCleanTitle"),
    videoKind: eleventyConfig.getFilter("mediaVideoKind"),
  });
  eleventyConfig.addFilter("cmDigestText", (dg, langs, style, site) => digestText(dg, langs, style, site, t, mediaHelpers()));

  eleventyConfig.addFilter("cmReport", (db, meeting) => reportData(db, meeting));
  eleventyConfig.addFilter("cmReportText", (rd, lang, site) => reportText(rd, lang, site, t));

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

  // Districts sorted by number; entries without a number are dropped.
  eleventyConfig.addFilter("cmDistricts", (items) =>
    (items || []).filter((d) => d && d.number !== undefined && d.number !== null && d.number !== "")
      .sort((a, b) => (Number(a.number) || 0) - (Number(b.number) || 0) || String(a.number).localeCompare(String(b.number))));

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
  // 0–100 → "1.6%" / "1,6 %" (≥ 10 without decimals; a tiny non-zero value never shows as 0)
  eleventyConfig.addFilter("cmPct", (n, lang) => {
    const v = Number(n) || 0;
    const shown = v > 0 && v < 0.1 ? 0.1 : v;
    return new Intl.NumberFormat(LOCALES[lang] || "en-US", { style: "percent", maximumFractionDigits: shown >= 10 ? 0 : 1 }).format(shown / 100);
  });

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
