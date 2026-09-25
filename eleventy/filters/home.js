// Home-page filters (owned by the home page). Auto-loaded by eleventy.config.js.
// Every filter is defensive: the synced data may be empty, partial or odd
// (e.g. a crawled link titled just "Read"), and the home page must still look good.
//
// Dev tip: HOME_EMPTY=1 npx @11ty/eleventy …  renders the home page as if every
// data file were empty (to check the empty states). It only affects templates that
// read data through `db | homeData`, i.e. the home page.
//
// Podcast / video titles on the home page use the shared `mediaTitle` filter
// (eleventy/filters/media.js), so they read exactly like /listen/ and /watch/.

import fs from "node:fs";
import path from "node:path";
// The Library's own rules (which documents, which kit / type, which collections),
// so the home page's quick links show the same numbers as /library/.
import { libraryDocs, libraryCollections, docKitType, CATEGORIES, COLLECTIONS } from "./library.js";
// The id of an event's card on /events/ (a monthly recurring event links there) and the end of a
// Central-time day (an all-day event is upcoming through its last day).
import { eventAnchor, chicagoDayEndMs } from "./committee.js";

const TZ = "America/Chicago";
const LOCALES = { en: "en-US", es: "es-US" };
const DAY = 864e5;

// Link texts that say nothing about the item ("Read", "Leer más", "Download" …).
const JUNK_TITLE = /^(read|read more|read here|more|learn more|click here|here|download|download here|pdf|view|view pdf|open|link|leer|leer m[aá]s|m[aá]s|aqu[ií]|haga clic aqu[ií]|haz clic aqu[ií]|descargar|desc[aá]rgalo|ver|ver pdf|abrir|enlace|untitled|sin t[ií]tulo)$/i;

// Link texts like "Read more news here" / "Lee más noticias aquí".
const JUNK_PREFIX = /^(read more|learn more|click here|lee m[aá]s|leer m[aá]s|haz clic|haga clic)(\s.{0,20})?$/i;

// Podcast/video title tail "[Season 11, Episode 12]" / "[Temporada 11, Episodio 12]" —
// ignored when matching a podcast episode with its YouTube upload (titleKey below).
// The pattern stays broad on purpose (any trailing bracket mentioning a season/episode).
const SEASON_TAIL = /\s*[[(][^\])]*(season|temporada|episod)[^\])]*[\])]\s*$/i;

// Kinds that never belong in the "fresh" strip (they have their own sections / no page).
// Announcements are skipped too: they have their own section right below the strip.
const FRESH_SKIP = new Set(["event", "topic", "meeting", "announcement"]);

// Magazine sections that carry the issue's theme ("Featured Section", "Sección Especial" …).
const FEATURED_SECTION = /featured|special|especial|destacad/i;

// "2026-09-23" for a moment in time, as a calendar day in Central time (the site's time zone).
function ymdCentral(ms) {
  try {
    const s = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  } catch { /* fall through */ }
  return new Date(ms).toISOString().slice(0, 10);
}
// The day a spotlight story counts as published: extra.pub_date (build_data.py), else its date.
function spotPubDate(i) {
  const p = String((i && i.extra && i.extra.pub_date) || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(p) ? p : String((i && i.date) || "").slice(0, 10);
}
// "2026-09-23" minus 60 days → "2026-07-25"
function ymdMinus(ymd, days) {
  const [y, m, d] = String(ymd).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - days)).toISOString().slice(0, 10);
}

/* Published-writers grid: how to close its last row at `cols` columns (home.css: 2 from 640px,
   3 from 1024px, 4 from 1360px, 5 from 1800px). Replays the grid's `row dense` auto-placement:
   `a` Area 65 cards two columns wide (or, when a = 0 and t > 0, the two-column "no Area 65
   story" note), then `t` Texas cards one column wide.
   → { span, full }: `span` = empty columns at the end of the last row, which the "see the full
   list" tile fills (0 = the rows are full: no tile); `full` = true when Area 65 cards would
   leave gaps higher up (too few Texas cards to sit beside them) — then every Area 65 card
   takes a whole row at that width, which leaves no gap except at the end of the last row.
   The SAME function is in src/assets/js/home.js (spotFill): keep the two in step. */
function spotFill(a, t, cols) {
  const run = (featSpan) => {
    const rows = [];
    const put = (span) => {
      for (let r = 0; ; r++) {
        if (!rows[r]) rows[r] = new Array(cols).fill(false);
        for (let c = 0; c + span <= cols; c++) {
          let ok = true;
          for (let k = c; k < c + span; k++) if (rows[r][k]) { ok = false; break; }
          if (ok) { for (let k = c; k < c + span; k++) rows[r][k] = true; return; }
        }
      }
    };
    if (a > 0) for (let i = 0; i < a; i++) put(featSpan);
    else if (t > 0) put(Math.min(2, cols));
    for (let i = 0; i < t; i++) put(1);
    let free = 0, trailing = 0;
    for (const row of rows) for (const used of row) if (!used) free++;
    const last = rows[rows.length - 1] || [];
    for (let k = last.length - 1; k >= 0 && !last[k]; k--) trailing++;
    return { free, trailing };
  };
  a = Math.max(0, Math.floor(Number(a) || 0));
  t = Math.max(0, Math.floor(Number(t) || 0));
  cols = Math.max(1, Math.floor(Number(cols) || 1));
  if (!a && !t) return { span: 0, full: false };
  let r = run(Math.min(2, cols));
  let full = false;
  if (r.free !== r.trailing) { full = true; r = run(cols); }
  return { span: r.trailing, full };
}

/* data/site/spotlight.json read straight from disk — only used while src/_data/db.js does not
   load it yet (db.spotlight undefined). Links get the same treatment db.js gives every data
   file: a story link must be http(s) and a picture http(s) or a site path, else it is dropped. */
function readSpotlightFile() {
  const p = path.join("data", "site", "spotlight.json");
  let data = null;
  try {
    if (fs.existsSync(p)) data = JSON.parse(fs.readFileSync(p, "utf8"));
  } catch (e) {
    console.warn(`[home] could not read ${p}: ${e.message}`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return { items: [] };
  const web = (v) => (typeof v === "string" && /^https?:\/\/[^\s/\\?#]/i.test(v.trim()) ? v.trim() : "");
  const pic = (v) => (typeof v === "string" && /^\/(?![/\\])/.test(v.trim()) ? v.trim() : web(v));
  data.items = (Array.isArray(data.items) ? data.items : [])
    .filter((i) => i && typeof i === "object")
    .map((i) => ({ ...i, url: web(i.url), image: pic(i.image) }))
    .filter((i) => i.url);
  return data;
}

const MONTHS = {
  // lower-case month names (en + es) → 0-based month index
  january: 0, february: 1, march: 2, april: 3, may: 4, june: 5, july: 6, august: 7, september: 8, october: 9, november: 10, december: 11,
  enero: 0, febrero: 1, marzo: 2, abril: 3, mayo: 4, junio: 5, julio: 6, agosto: 7, septiembre: 8, setiembre: 8, octubre: 9, noviembre: 10, diciembre: 11,
};

export default function (eleventyConfig, helpers) {
  const { toDate, pickLang, translateKey } = helpers;

  const time = (v) => { const d = toDate(v); return d ? d.getTime() : 0; };
  const itemTime = (i) => (i ? time((i.extra && i.extra.start) || i.date || i.first_seen) : 0);
  // "News date": What's New items carry `wn_date` (the date the sync sorted them by).
  const newsTime = (i) => (i ? time(i.wn_date || i.date || i.first_seen) : 0);
  const byNewest = (a, b) => itemTime(b) - itemTime(a);
  const alive = (i) => i && typeof i === "object" && i.status !== "gone";
  // Lists from the data files: anything that isn't an array counts as empty.
  const arr = (x) => (Array.isArray(x) ? x : []);
  const pubOf = (i) => {
    const x = (i && i.extra) || {};
    if (x.publication === "lv" || x.publication === "gv") return x.publication;
    if (i.source === "lavina" || i.category === "lv") return "lv";
    if (i.source === "crawl") return /lavina/i.test(String(x.host || i.url || "")) ? "lv" : "gv";
    return "gv";
  };

  function goodTitle(item) {
    const t = String((item && item.title) || "").replace(/[\s.…:!»«"'“”→>-]+$/g, "").replace(/^[\s«"'“]+/, "").trim();
    return t.length >= 3 && !JUNK_TITLE.test(t) && !JUNK_PREFIX.test(t);
  }

  // Normalized title without the season/episode tail (to spot a podcast episode and its YouTube upload).
  const titleKey = (i) => String((i && i.title) || "").toLowerCase().replace(SEASON_TAIL, "").replace(/[^\p{L}\p{N}]+/gu, " ").trim();

  // Best picture for an item (local cached thumb wins over remote URLs that expire).
  function imageOf(i) {
    if (!i) return "";
    const x = i.extra || {};
    return x.thumb || i.image || x.thumb_url || x.flyer_thumb || (Array.isArray(x.thumbs) && x.thumbs.find(Boolean)) || "";
  }

  // How good a card an item makes (ties inside one day / one issue).
  function richness(i) {
    const x = i.extra || {};
    let s = (imageOf(i) ? 1 : 0) + (String(i.summary || "").length > 60 ? 1 : 0);
    if (i.kind === "article") s += (x.department ? 0 : 3) + (FEATURED_SECTION.test(String(x.section || "")) && !x.department ? 2 : 0);
    return s;
  }

  // Monday-based week number — used to rotate evergreen lists once a week
  // (stable within a week, so daily builds don't reshuffle the page).
  const weekSeed = () => Math.floor((Date.now() + 3 * DAY) / (7 * DAY));

  /* All data the home page reads goes through this, so HOME_EMPTY=1 can blank it. */
  eleventyConfig.addFilter("homeData", (db) => {
    if (!process.env.HOME_EMPTY) return db || {};
    const out = {};
    for (const k of Object.keys(db || {})) out[k] = { updated: null, items: [] };
    out.status = { generated: null, sources: [], items: [] };
    out.spotlight = { updated: null, items: [] }; // (also when db.js does not load it yet)
    return out;
  });

  /* PUBLISHED WRITERS spotlight: every Grapevine / La Viña story published in the last
     `home_days` days (spotlight.json, default 60) whose writer is from Texas —
     { days, listDays, maxDays, today, cutoff, a65: [Area 65 writers], tx: [rest of Texas], total }.
     Area 65 (geo.scope "neta65") and the rest of Texas ("texas") are separate lists, each
     newest first (extra.pub_date), then by title. The window is counted from TODAY in Central
     time (pub_date >= today − days, inclusive), so a rebuild is always current; home.js
     recounts it in the visitor's browser with the same rule, so the page stays right between
     daily builds (stories drop out of the window as days pass — none can come in).
     Reads db.spotlight; while src/_data/db.js does not load that file, reads it from disk. */
  eleventyConfig.addFilter("homeSpotlight", (db, fallbackDays = 60, fallbackListDays = null) => {
    const raw = db && db.spotlight !== undefined ? db.spotlight : readSpotlightFile();
    const sp = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const days = Math.max(1, Math.round(Number(sp.home_days) || Number(fallbackDays) || 60));
    let listDays = (Array.isArray(sp.list_days) ? sp.list_days : Array.isArray(fallbackListDays) ? fallbackListDays : [])
      .map(Number).filter((d) => d > 0);
    if (!listDays.length) listDays = [60, 90];
    const today = ymdCentral(Date.now());
    const cutoff = ymdMinus(today, days);
    const pubOfItem = spotPubDate;
    const scopeOf = (i) => String((i.extra && i.extra.geo && i.extra.geo.scope) || "");
    const seen = new Set();
    const list = arr(sp.items).filter((i) => {
      if (!alive(i) || !i.extra || !i.url || i.kind === "topic") return false;
      const p = pubOfItem(i);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(p) || p < cutoff) return false;
      const key = i.id || i.url;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const order = (a, b) => pubOfItem(b).localeCompare(pubOfItem(a)) || String(a.title || "").localeCompare(String(b.title || ""));
    const a65 = list.filter((i) => scopeOf(i) === "neta65").sort(order);
    const tx = list.filter((i) => scopeOf(i) === "texas").sort(order);
    // maxDays: the longest window on /published/ (named by the "see the full list" tile)
    return { days, listDays, maxDays: Math.max(...listDays), today, cutoff, a65, tx, total: a65.length + tx.length };
  });

  /* The day a spotlight story counts as published (YYYY-MM-DD): extra.pub_date, else its
     date — the same rule as the /published/ page (eleventy/filters/published.js). */
  eleventyConfig.addFilter("homePubDate", spotPubDate);

  /* How the Published-writers grid closes its last row at each column count (see spotFill):
     spot (homeSpotlight) → { f2, f3, f4, f5: tile span at 2 / 3 / 4 / 5 columns (0 = no tile),
     full: "3 5" — the column counts at which Area 65 cards take a whole row ("" = none) }.
     index.njk writes these as data-f2…data-f5 / data-full; home.css reads them;
     home.js recomputes them when stories leave the window in the visitor's browser. */
  eleventyConfig.addFilter("homeSpotFill", (spot) => {
    const a = spot && Array.isArray(spot.a65) ? spot.a65.length : 0;
    const t = spot && Array.isArray(spot.tx) ? spot.tx.length : 0;
    const out = { full: "" };
    const full = [];
    for (const cols of [2, 3, 4, 5]) {
      const r = spotFill(a, t, cols);
      out["f" + cols] = r.span;
      if (r.full) full.push(cols);
    }
    out.full = full.join(" ");
    return out;
  });

  /* Initials for a writer's monogram, from the name as printed:
     "Victor R." → "VR", "J.G." → "JG", "R. O." → "RO", "Mary Ann K." → "MK"; "" when none. */
  eleventyConfig.addFilter("homeInitials", (name) => {
    const parts = String(name || "").normalize("NFC").split(/[\s.·,-]+/).filter((p) => /\p{L}/u.test(p));
    if (!parts.length) return "";
    const first = (p) => p.match(/\p{L}/u)[0];
    return (first(parts[0]) + (parts.length > 1 ? first(parts[parts.length - 1]) : "")).toLocaleUpperCase();
  });

  /* Next-meeting date for the compact meeting card, without the year (the next meeting is
     at most a few weeks away): "Wednesday, October 21" / "Miércoles, 21 de octubre".
     home.js writes the same format when it moves on to the following meeting. */
  eleventyConfig.addFilter("homeMeetingDate", (v, lang = "en") => {
    const d = toDate(v);
    if (!d) return "";
    let s = new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { weekday: "long", month: "long", day: "numeric", timeZone: TZ }).format(d);
    if (lang === "es") s = s.charAt(0).toUpperCase() + s.slice(1);
    return s;
  });

  /* "Fresh" strip: the newest items across all sources, mixed round-robin by source
     (GV stories, LV stories, posts, videos, episodes, PDFs, committee files) so one busy
     source — a new magazine issue with 30 stories — can't take over the row.
     `shown` (extra arguments: single items or arrays) = everything the home page already shows in
     its own sections (Listen & watch, New on YouTube, the magazines, published writers, newest
     documents, Instagram, committee uploads): those are left out, and so is a podcast episode's
     YouTube copy (same title), so no item appears twice on the page. */
  eleventyConfig.addFilter("homeFresh", (items, n = 8, ...shown) => {
    const now = Date.now();
    const shownItems = shown.flat(2).filter(Boolean);
    const shownIds = new Set(shownItems.map((i) => i.id).filter(Boolean));
    const shownUrls = new Set(shownItems.map((i) => i.url).filter(Boolean));
    const shownMedia = new Set(shownItems.filter((i) => i.kind === "episode" || i.kind === "video").map(titleKey).filter(Boolean));
    const isShown = (i) => shownIds.has(i.id) || (i.url && shownUrls.has(i.url))
      || ((i.kind === "episode" || i.kind === "video") && shownMedia.has(titleKey(i)));
    const list = [...arr(items)].filter((i) => alive(i) && !isShown(i)).sort((a, b) => newsTime(b) - newsTime(a));
    // A podcast episode and its YouTube upload share a title: keep the episode
    // (the podcast player page), so the video slot goes to a different video.
    const epKeys = new Set(list.filter((i) => i.kind === "episode").map(titleKey).filter(Boolean));
    const seen = new Set();
    const pool = list.filter((i) => {
      if (FRESH_SKIP.has(i.kind) || !goodTitle(i)) return false;
      const t = newsTime(i);
      // Magazine issues are dated ahead (the October issue is out in September);
      // anything further out than ~6 weeks is a bad date, not "fresh".
      if (!t || t > now + 45 * DAY) return false;
      // Instagram posts without a picture make a poor preview (they have their own strip).
      if (i.kind === "post" && !imageOf(i)) return false;
      if (i.kind === "video" && epKeys.has(titleKey(i))) return false;
      const key = i.id || i.url;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    // Prefer the last two months; older items only when there is too little.
    const recent = pool.filter((i) => now - newsTime(i) <= 60 * DAY);
    const src = recent.length >= Math.min(n, 4) ? recent : pool;
    const groupOf = (i) => (i.kind === "article" ? "article:" + pubOf(i) : ["photo", "document", "slides", "form", "video_file"].includes(i.kind) ? "drive" : i.kind);
    const groups = new Map();
    for (const it of src) {
      const g = groupOf(it);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(it);
    }
    const dayOf = (i) => Math.floor(newsTime(i) / DAY);
    for (const g of groups.values()) g.sort((a, b) => dayOf(b) - dayOf(a) || richness(b) - richness(a) || newsTime(b) - newsTime(a));
    const out = [];
    for (let round = 0; out.length < n; round++) {
      const heads = [...groups.values()].filter((a) => a.length > round).map((a) => a[round]);
      if (!heads.length) break;
      heads.sort((a, b) => newsTime(b) - newsTime(a));
      for (const h of heads) { if (out.length >= n) break; out.push(h); }
    }
    return out.sort((a, b) => newsTime(b) - newsTime(a) || richness(b) - richness(a));
  });

  /* Items whose news date is within the last `days` days (not in the future beyond a day). */
  eleventyConfig.addFilter("homeRecentCount", (items, days = 7) => {
    const now = Date.now();
    return arr(items).filter((i) => {
      if (!alive(i) || FRESH_SKIP.has(i.kind)) return false;
      const t = newsTime(i);
      return t && now - t <= days * DAY && t <= now + DAY;
    }).length;
  });

  /* Latest issue of a publication ("gv" | "lv") for the page language:
     { key, label, topic, topicLang, cover, url, total, items[] }.
     `issues` = articles.json `issues` (cover, theme + its translation, issue page URL).
     Stories: the issue's featured section first, then other stories, departments
     ("Letter from the Editor", "Dear Grapevine" …) last. */
  eleventyConfig.addFilter("homeLatestIssue", (articles, pub, n = 5, issues = [], lang = "en") => {
    const list = arr(articles).filter((a) => alive(a) && a.kind !== "topic" && pubOf(a) === pub);
    const metas = (Array.isArray(issues) ? issues : []).filter((m) => m && (m.publication === pub || String(m.id || "").startsWith(pub + ":")));
    const keyOf = (a) => (a.extra && a.extra.issue_key) || String(a.date || "").slice(0, 7);
    const keys = [...list.map(keyOf), ...metas.map((m) => m.key)].filter((k) => /^\d{4}-\d{2}/.test(String(k || ""))).sort();
    const key = keys.pop() || "";
    const meta = metas.find((m) => m.key === key) || null;
    const issue = list.filter((a) => keyOf(a) === key);
    const first = issue[0] || null;
    if (!meta && !issue.length) return { key: "", label: "", topic: "", topicLang: "", cover: "", url: "", total: 0, items: [] };

    const good = issue.filter(goodTitle);
    const featured = good.filter((a) => !(a.extra && a.extra.department) && FEATURED_SECTION.test(String((a.extra && a.extra.section) || "")));
    const regular = good.filter((a) => !(a.extra && a.extra.department) && !featured.includes(a));
    const depts = good.filter((a) => a.extra && a.extra.department);
    const withTeaser = (xs) => [...xs.filter((a) => a.summary), ...xs.filter((a) => !a.summary)];
    // Up to three featured stories, then the rest (a mix of sections reads better than 5 of one kind).
    const ranked = [...withTeaser(featured).slice(0, 3), ...withTeaser(regular), ...withTeaser(featured).slice(3), ...withTeaser(depts)];
    const items = ranked.slice(0, n);
    if (items.length < Math.min(2, n)) {
      // A brand-new issue with hardly any stories yet: pad with the previous issue.
      for (const a of list.filter((x) => keyOf(x) !== key && goodTitle(x)).sort(byNewest)) {
        if (items.length >= n) break;
        items.push(a);
      }
    }

    // Theme: from the issue record (with its translation); else only if the stories agree.
    let topic = "", topicOrig = "", topicLang = "";
    if (meta && meta.theme) {
      topicOrig = String(meta.theme);
      topic = String(pickLang(meta, "theme", lang) || topicOrig);
    } else {
      const topics = [...new Set(issue.map((a) => a.extra && a.extra.topic).filter(Boolean))];
      if (topics.length === 1) {
        topicOrig = String(topics[0]);
        topic = String((first && pickLang(first, "topic", lang)) || topicOrig);
      }
    }
    const origLang = (meta && meta.lang) || (first && first.lang) || (pub === "lv" ? "es" : "en");
    // Not translated (yet): mark the original language so screen readers pronounce it right.
    if (topic && topic === topicOrig && origLang !== lang) topicLang = origLang;

    const rawLabel = (meta && meta.label) || (first && first.extra && first.extra.issue_label) || "";
    return {
      key,
      label: rawLabel,
      topic,
      topicLang,
      cover: (meta && (meta.cover || meta.image)) || "",
      url: (meta && meta.url) || (first && first.extra && first.extra.issue_url) || "",
      total: issue.length,
      items,
    };
  });

  /* Localize an issue label like "October 2026" or "Septiembre / Octubre 2026":
     en "October 2026" · "September–October 2026"; es "octubre de 2026" · "septiembre–octubre de 2026"
     (lower case: the label is used inside sentences such as "Edición de …"). */
  eleventyConfig.addFilter("homeIssueLabel", (label, lang = "en") => {
    const s = String(label || "");
    const found = [];
    for (const m of s.toLowerCase().matchAll(/[a-záéíóú]+/g)) if (MONTHS[m[0]] !== undefined) found.push(MONTHS[m[0]]);
    const year = (s.match(/\b(19|20)\d{2}\b/) || [])[0];
    if (!found.length || !year) return s;
    const loc = LOCALES[lang] || "en-US";
    const name = (mi) => {
      const t = new Intl.DateTimeFormat(loc, { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2020, mi, 15)));
      return t.charAt(0).toUpperCase() + t.slice(1);
    };
    const months = [...new Set(found)].slice(0, 2).map(name).join("–");
    return lang === "es" ? `${months.toLowerCase()} de ${year}` : `${months} ${year}`;
  });

  /* Upcoming events for the home page, shown by date. The very next committee meeting is already
     in the hero, so it is skipped. The NEXT date of every monthly recurring event (config/site.yml
     `recurring_events:`, e.g. the booth at CityWide Dallas) always keeps a place — even when several
     one-off events come sooner — and only its next date (one series never fills several places).
     The other places go to the soonest one-off events (workshops, assemblies …); a place still free
     takes at most ONE more committee meeting (a row of identical monthly meetings says little). */
  const isYmd = (v) => /^\d{4}-\d{2}-\d{2}$/.test(String(v || ""));
  const evStart = (e) => time((e.extra && e.extra.start) || e.date);
  // When an event is over: an all-day event at midnight Central after its LAST day (an assembly
  // Fri–Sun stays upcoming all Sunday); a timed one at its end (no end: 2 hours after the start).
  const evEnd = (e) => {
    const x = e.extra || {};
    const s = x.start || e.date;
    const last = x.end || (isYmd(s) || x.all_day ? String(s).slice(0, 10) : "");
    if (isYmd(last)) return chicagoDayEndMs(last);
    if (x.end) return time(x.end);
    return evStart(e) + 2 * 3600e3;
  };
  eleventyConfig.addFilter("homeEvents", (events, next, n = 4) => {
    const now = Date.now();
    const nextT = next && next.start ? time(next.start) : 0;
    const series = new Set();
    const up = arr(events).filter((e) => alive(e) && evStart(e) && evEnd(e) >= now)
      .filter((e) => !(e.category === "committee" && nextT && Math.abs(evStart(e) - nextT) < 36 * 3600e3))
      .sort((a, b) => evStart(a) - evStart(b))
      .filter((e) => {
        if (e.category !== "recurring") return true;
        const k = String((e.extra && e.extra.series) || e.id);
        if (series.has(k)) return false;
        series.add(k);
        return true;
      });
    const monthly = up.filter((e) => e.category === "recurring");                        // next date of each series
    const oneOff = up.filter((e) => e.category !== "recurring" && e.category !== "committee");
    // Every series keeps a place, but one place always stays for the soonest one-off event.
    const reserved = monthly.slice(0, Math.max(0, n - (oneOff.length ? 1 : 0)));
    const pick = [...reserved, ...oneOff.slice(0, n - reserved.length)];
    const committee = up.find((e) => e.category === "committee");
    if (pick.length < n && committee) pick.push(committee);
    return pick.sort((a, b) => evStart(a) - evStart(b));
  });

  /* How a home-page event card shows its date, time and place (the same rules as /events/):
     { tile: {mon, day, wd, range}, srDate, when, location, tba, tentative, multiDay }.
     An event over several days (an Area assembly Fri–Sun) gets a date RANGE — on the tile
     ("MAR · 19–21 · Fri–Sun") and as its "when" line ("Fri, Mar 19 – Sun, Mar 21, 2027");
     the place is in the page language (content/events `location_es` → i18n.location). */
  eleventyConfig.addFilter("homeEventInfo", (e, lang = "en") => {
    const x = (e && e.extra) || {};
    const loc = LOCALES[lang] || "en-US";
    const s = x.start || (e && e.date) || "";
    const dateOnly = !!x.all_day || isYmd(s);
    const ymdOf = (v) => (isYmd(v) ? String(v) : v ? ymdCentral(time(v)) : "");
    const startYmd = ymdOf(s);
    let endYmd = x.end ? (isYmd(x.end) ? String(x.end) : ymdCentral(time(x.end) - 1)) : startYmd;
    if (!endYmd || endYmd < startYmd) endYmd = startYmd;
    const multiDay = !!startYmd && endYmd !== startYmd && (dateOnly || time(x.end) - time(s) > 18 * 3600e3);
    // date-only values are read at noon UTC: the same calendar day in Central time
    const at = (ymdOrIso) => (isYmd(ymdOrIso) ? new Date(ymdOrIso + "T12:00:00Z") : new Date(time(ymdOrIso)));
    const f = (d, o) => {
      try {
        const out = new Intl.DateTimeFormat(loc, { timeZone: TZ, ...o }).format(d);
        return lang === "es" && helpers.esMeridiem ? helpers.esMeridiem(out) : out;
      } catch { return ""; }
    };
    const fr = (a, b, o) => {
      try {
        const out = new Intl.DateTimeFormat(loc, { timeZone: TZ, ...o }).formatRange(a, b);
        return lang === "es" && helpers.esMeridiem ? helpers.esMeridiem(out) : out;
      } catch { return ""; }
    };
    const capital = (v) => (v ? v.charAt(0).toUpperCase() + v.slice(1) : v);
    const a = at(dateOnly ? startYmd : s);
    const b = multiDay ? (dateOnly ? at(endYmd) : new Date(time(x.end) - 1)) : a;
    const part = (d, o) => f(d, o).replace(/\./g, "");
    const tile = { mon: part(a, { month: "short" }), day: part(a, { day: "numeric" }), wd: part(a, { weekday: "short" }), range: multiDay };
    if (multiDay) {
      const mon2 = part(b, { month: "short" });
      if (mon2 !== tile.mon) tile.mon += "–" + mon2;
      tile.day += "–" + part(b, { day: "numeric" });
      tile.wd += "–" + part(b, { weekday: "short" });
    }
    let when;
    if (multiDay) {
      when = dateOnly
        ? capital(fr(a, b, { weekday: "short", month: "short", day: "numeric", year: "numeric" }))
        : capital(fr(time(s), time(x.end), { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }));
    } else if (dateOnly) {
      // An outside calendar that only gives a date: the time isn't listed (as on /events/), not "all day".
      when = translateKey(e && e.source === "calendar" ? "home.time_not_listed" : "home.all_day", lang);
    } else {
      const st = time(s), en = time(x.end);
      when = en > st ? fr(st, en, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : f(st, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
    }
    const srDate = multiDay
      ? capital(fr(a, b, { weekday: "long", month: "long", day: "numeric", year: "numeric" }))
      : capital(f(a, { weekday: "long", month: "long", day: "numeric", year: "numeric" }));
    const place = String(pickLang(e, "location", lang) || x.location || x.city || "").trim();
    return {
      tile, when, srDate, multiDay,
      location: place,
      tba: !!place && x.location_tba === true,
      tentative: x.tentative === true,
    };
  });

  /* The id of an event's card on /events/ ("/events/#" + this): a monthly recurring event's card
     there carries its "every month" line, the add-to-calendar menu and the organizers' link. */
  eleventyConfig.addFilter("homeEventAnchor", (e) => (e ? eventAnchor(e) : ""));

  /* Announcements: not expired, pinned first, then newest. */
  eleventyConfig.addFilter("homeAnnouncements", (items) => {
    const today = ymdCentral(Date.now()); // Central-time day, like /announcements/
    return arr(items).filter((a) => {
      if (!alive(a)) return false;
      const exp = a.extra && a.extra.expires;
      return !exp || String(exp).slice(0, 10) >= today;
    }).sort((a, b) => {
      const pa = a.extra && a.extra.pinned ? 1 : 0, pb = b.extra && b.extra.pinned ? 1 : 0;
      return pb - pa || byNewest(a, b);
    });
  });

  /* "Write for the magazines": Grapevine themes with an upcoming deadline (soonest first)
     + La Viña's evergreen topics (no deadline; a different pair each week).
     Half and half when both exist, so both magazines are always invited. */
  eleventyConfig.addFilter("homeThemes", (items, n = 4) => {
    const today = ymdCentral(Date.now()); // Central-time day, like /contribute/
    const live = arr(items).filter((t) => alive(t) && t.extra && goodTitle(t));
    const dated = live.filter((t) => t.extra.deadline && String(t.extra.deadline).slice(0, 10) >= today)
      .sort((a, b) => String(a.extra.deadline).localeCompare(String(b.extra.deadline)));
    const evergreen = live.filter((t) => t.extra.evergreen === true && !t.extra.deadline);
    let rot = [];
    if (evergreen.length) {
      const s = weekSeed() % evergreen.length;
      rot = [...evergreen.slice(s), ...evergreen.slice(0, s)];
    }
    let nd = Math.min(dated.length, Math.ceil(n / 2));
    const ne = Math.min(rot.length, n - nd);
    nd = Math.min(dated.length, n - ne);
    return [...dated.slice(0, nd), ...rot.slice(0, ne)];
  });

  /* Newest PDFs: dated ones only (an undated PDF isn't "new"), newest first, with a mix:
     one per kind of document and magazine first (GV News, an LV order form, a workbook …),
     preferring PDFs that have a thumbnail. */
  eleventyConfig.addFilter("homeNewestPdfs", (items, n = 6) => {
    const now = Date.now();
    const list = arr(items).filter((i) => alive(i) && goodTitle(i) && time(i.date) && time(i.date) <= now + 2 * DAY)
      .sort((a, b) => time(b.date) - time(a.date) || (imageOf(b) ? 1 : 0) - (imageOf(a) ? 1 : 0));
    const typeOf = (p) => ((Array.isArray(p.tags) && p.tags[0]) || p.category || "other") + ":" + pubOf(p);
    const out = [];
    const used = new Map();
    const passes = [
      (p) => imageOf(p) && !used.has(typeOf(p)),
      (p) => imageOf(p) && (used.get(typeOf(p)) || 0) < 2,
      (p) => !used.has(typeOf(p)),
      () => true,
    ];
    for (const ok of passes) {
      for (const p of list) {
        if (out.length >= n) break;
        if (out.includes(p) || !ok(p)) continue;
        out.push(p);
        used.set(typeOf(p), (used.get(typeOf(p)) || 0) + 1);
      }
    }
    return out.sort((a, b) => time(b.date) - time(a.date));
  });

  /* Library quick links, counted exactly like /library/ (same documents — crawled PDFs +
     committee Drive files — and the same rules, from library.js):
     1. the Library's own quick collections (GVR kit, RLV kit, catalogs, flyers & postcards,
        news …) → /library/?col=<key>;
     2. if no collection has anything yet: the kits and the most common document types.
        A kit PDF counts in its kit AND in its own type (docKitType), like the Library's
        filters → ?col=gvr-kit / ?col=rlv-kit / ?cat=<type>. */
  const CAT_ICON = Object.fromEntries(CATEGORIES);
  // UI string, or "" when the key does not exist (safe with I18N_STRICT=1)
  const tryT = (key, lang) => { try { const v = translateKey(key, lang); return v === key ? "" : v; } catch { return ""; } };
  const typeLabel = (type, lang) => tryT("library.cat." + type, lang)
    || String(type).replace(/[-_]+/g, " ").replace(/^\p{Ll}/u, (m) => m.toUpperCase());
  eleventyConfig.addFilter("homeLibChips", (db, lang = "en", n = 6) => {
    let docs = null;
    try { docs = libraryDocs(db, lang, helpers); } catch (e) { console.warn(`[home] library chips: ${e.message}`); }
    if (docs) {
      try {
        const cols = libraryCollections(docs, lang, helpers).filter((c) => c && c.count > 0).slice(0, n);
        if (cols.length) return cols.map((c) => ({ href: "/library/?col=" + encodeURIComponent(c.key), label: c.label, count: c.count, icon: c.icon || "folder-open" }));
      } catch (e) { console.warn(`[home] library collections: ${e.message}`); }
    }
    const rows = docs ? docs.map((d) => ({ kit: d.kit, type: d.c })) : arr(db && db.pdfs && db.pdfs.items).filter(alive).map(docKitType);
    const kits = new Map(), types = new Map();
    for (const r of rows) {
      if (r.kit) kits.set(r.kit, (kits.get(r.kit) || 0) + 1);
      if (r.type && r.type !== "other") types.set(r.type, (types.get(r.type) || 0) + 1);
    }
    const chips = [];
    for (const col of COLLECTIONS) {
      const kit = col.key.replace(/-kit$/, "");
      if (col.key.endsWith("-kit") && kits.get(kit)) {
        chips.push({ href: "/library/?col=" + encodeURIComponent(col.key), label: tryT("library.col." + col.key, lang) || kit.toUpperCase(), count: kits.get(kit), icon: col.icon });
      }
    }
    for (const [type, count] of [...types].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
      chips.push({ href: "/library/?cat=" + encodeURIComponent(type), label: typeLabel(type, lang), count, icon: CAT_ICON[type] || "file-text" });
    }
    return chips.slice(0, n);
  });

  /* Podcast episodes for "Listen & watch": { featured, more[] }.
     Featured = newest episode of the first show in config (AA Grapevine's Podcast),
     unless it is over a month older than the newest episode of any show.
     More = the next newest, but every show gets a row: a show missing from the list (the
     other one published several in a row) replaces the oldest row of a show that has two. */
  eleventyConfig.addFilter("homeEpisodes", (items, shows = [], nMore = 3) => {
    const list = arr(items).filter((e) => alive(e) && (e.extra && (e.extra.audio_url || e.url))).sort(byNewest);
    if (!list.length) return { featured: null, more: [] };
    const showOf = (e) => String((e.extra && e.extra.show) || e.category || "");
    const mainKey = (Array.isArray(shows) && shows[0] && shows[0].key) || "gv";
    const main = list.find((e) => showOf(e) === mainKey);
    const featured = main && itemTime(list[0]) - itemTime(main) <= 30 * DAY ? main : list[0];
    const more = list.filter((e) => e !== featured).slice(0, nMore);
    const rows = (k) => (showOf(featured) === k ? 1 : 0) + more.filter((e) => showOf(e) === k).length;
    for (const e of list) {
      const k = showOf(e);
      if (!k || rows(k)) continue;
      let i = more.length - 1;
      while (i >= 0 && rows(showOf(more[i])) < 2) i--;
      if (i < 0) break;
      more.splice(i, 1, e); // e = the newest episode of the missing show
    }
    return { featured, more: more.sort(byNewest) };
  });

  /* The show record for an episode from a list of shows (episodes.json `shows`
     or config sources.podcasts), matched by key; {} when not found. */
  eleventyConfig.addFilter("homeShow", (shows, ep) => {
    const key = ep && ((ep.extra && ep.extra.show) || ep.category);
    return (key && Array.isArray(shows) && shows.find((s) => s && s.key === key)) || {};
  });

  /* Videos for the home page: regular videos (not Shorts), newest first, skipping the
     YouTube copies of the podcast episodes already shown just above (pass them as extra
     arguments: single items or arrays); at least one La Viña video when there is a recent one. */
  eleventyConfig.addFilter("homeVideos", (items, n = 4, ...shown) => {
    const now = Date.now();
    const skip = new Set(shown.flat().filter(Boolean).map(titleKey));
    const list = arr(items).filter((v) => alive(v) && v.extra && v.extra.video_id).sort(byNewest);
    let pool = list.filter((v) => !v.extra.is_short && !skip.has(titleKey(v)));
    if (pool.length < n) pool = [...pool, ...list.filter((v) => !pool.includes(v))];
    const out = pool.slice(0, n);
    const isLv = (v) => v.category === "lv" || v.lang === "es";
    if (n > 1 && !out.some(isLv)) {
      const lv = pool.find((v) => isLv(v) && !v.extra.is_short && now - itemTime(v) <= 365 * DAY);
      if (lv) out[out.length - 1] = lv;
    }
    return out.sort(byNewest);
  });

  /* Instagram posts: newest first, alternating the two accounts so both always show. */
  eleventyConfig.addFilter("homeInstagram", (items, n = 6) => {
    const list = arr(items).filter(alive).sort(byNewest);
    const byAcc = new Map();
    for (const p of list) {
      const k = (p.extra && p.extra.account) || p.category || "gv";
      if (!byAcc.has(k)) byAcc.set(k, []);
      byAcc.get(k).push(p);
    }
    const queues = [...byAcc.values()];
    const out = [];
    for (let i = 0; out.length < n && queues.some((q) => q.length > i); i++) {
      for (const q of queues) if (q[i] && out.length < n) out.push(q[i]);
    }
    return out.sort(byNewest);
  });

  /* Committee Drive uploads (documents/photos/slides…), newest first. */
  eleventyConfig.addFilter("homeDrive", (items, n = 6) => arr(items)
    .filter((i) => alive(i) && i.kind !== "event" && i.kind !== "announcement" && !(i.extra && i.extra.form_closed))
    .sort((a, b) => time(b.date || b.first_seen) - time(a.date || a.first_seen))
    .slice(0, n));

  /* 1234 → "1,234" (en) / "1234" … "12 345" (es) */
  eleventyConfig.addFilter("homeNum", (n, lang = "en") => {
    const v = Number(n) || 0;
    return new Intl.NumberFormat(LOCALES[lang] || "en-US").format(v);
  });

  /* "7:00 – 8:00 PM CDT" in Central time. */
  eleventyConfig.addFilter("homeTimeRange", (start, end, lang = "en") => {
    const s = toDate(start), e = toDate(end);
    if (!s) return "";
    const f = new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { hour: "numeric", minute: "2-digit", timeZone: TZ, timeZoneName: "short" });
    let out;
    try { out = e && e > s ? f.formatRange(s, e) : f.format(s); } catch { out = f.format(s); }
    return lang === "es" && helpers && helpers.esMeridiem ? helpers.esMeridiem(out) : out; // "p. m." like the rest of the site
  });

  /* Weekday name (0 = Sunday) in the page language: "Wednesday" / "miércoles". */
  eleventyConfig.addFilter("homeWeekday", (idx, lang = "en") => {
    const d = new Date(Date.UTC(2023, 0, 1 + (Number(idx) || 0), 12)); // 2023-01-01 was a Sunday
    return new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { weekday: "long", timeZone: "UTC" }).format(d);
  });

  /* Grapevine Weekly Open schedule line. The sync writes it by rule in both languages
     (i18n.when: "Wednesdays at 11:00 AM Central" / "Miércoles a las 11:00 a. m. (hora del Centro)").
     Fallback when that is missing: build it from extra.day / extra.time. */
  eleventyConfig.addFilter("homeWeeklyOpen", (item, lang = "en") => {
    const i = item || {};
    const when = i.i18n && i.i18n.when && i.i18n.when[lang];
    if (when) return String(when);
    const x = i.extra || {};
    const day = String(x.day || "").trim(), tm = String(x.time_central || x.time || "").trim();
    if (lang !== "es") return [day, tm].filter(Boolean).join(" · ");
    const days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
    const di = days.findIndex((d) => day.toLowerCase().startsWith(d));
    const dayEs = di >= 0
      ? "Los " + new Intl.DateTimeFormat("es-US", { weekday: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2023, 0, 1 + di, 12))).replace(/s?$/, "s") // lunes…viernes are already plural; sábado → sábados
      : day;
    const m = tm.match(/(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?/i);
    let timeEs = tm;
    if (m) {
      let h = Number(m[1]) % 12; if (m[3].toLowerCase() === "p") h += 12;
      timeEs = new Intl.DateTimeFormat("es-US", { hour: "numeric", minute: "2-digit", timeZone: "UTC" }).format(new Date(Date.UTC(2023, 0, 1, h, Number(m[2] || 0))));
      if (helpers && helpers.esMeridiem) timeEs = helpers.esMeridiem(timeEs);
      if (/central|ct\b|cst|cdt/i.test(tm)) timeEs += " (hora del Centro)";
    }
    return [dayEs, timeEs].filter(Boolean).join(" · ");
  });

  /* The original language when EVERY item is a machine translation from that same
     language for this page (one note per list instead of a note on every row); else "". */
  eleventyConfig.addFilter("homeAllMachine", (items, lang = "en") => {
    const list = arr(items);
    if (!list.length || !list[0]) return "";
    const src = list[0].lang;
    const all = list.every((i) => i && i.lang === src && i.lang !== lang && Array.isArray(i.machine) && i.machine.includes(lang));
    return all ? src : "";
  });

  /* Wrap a caption in curly quotes — without doubling quotes it already has
     ("“Faith is…”" stays as is instead of becoming "““Faith is…””"). */
  eleventyConfig.addFilter("homeQuote", (s) => {
    const t = String(s || "").trim();
    if (!t) return "";
    if (/^["“«]/.test(t)) return t;
    return "“" + t.replace(/["”»]+$/, "") + "”";
  });

  /* Lucide icon name for an item kind. */
  eleventyConfig.addFilter("homeKindIcon", (kind) => ({
    article: "book-open-text", pdf: "file-text", video: "circle-play", episode: "headphones", post: "instagram",
    photo: "image", document: "file-text", slides: "presentation", form: "clipboard-list", announcement: "megaphone",
    event: "calendar-days", video_file: "film", topic: "pen-line",
  }[kind] || "sparkles"));

  /* Colour family for an item: gv (blue) | lv (amber) | grape (media) | vine (committee). */
  eleventyConfig.addFilter("homeTone", (i) => {
    if (!i) return "gv";
    const x = i.extra || {};
    if (i.source === "lavina" || x.publication === "lv") return "lv";
    if (i.source === "grapevine") return "gv";
    if (i.source === "crawl") return String(x.host || "").includes("lavina") ? "lv" : "gv";
    if (i.source === "drive" || i.source === "committee") return "vine";
    if (i.source === "instagram") return i.category === "lv" ? "lv" : "grape";
    if (i.source === "youtube") return i.category === "lv" ? "lv" : "grape";
    return "grape";
  });

  /* Best picture for an item (see imageOf). */
  eleventyConfig.addFilter("homeImage", imageOf);

  /* Language of the content behind a card: a PDF's `lang` is its TITLE's language,
     `extra.doc_lang` is the document's. "" for unknown. */
  eleventyConfig.addFilter("homeDocLang", (i) => {
    if (!i) return "";
    const l = (i.kind === "pdf" && i.extra && i.extra.doc_lang) || i.lang || "";
    return l === "und" ? "" : l;
  });

  /* Title in the page language, tidied when it is really a file name
     ("La-Vin%CC%83a-Subscription-Form_2026" → "La Viña Subscription Form 2026").
     (Episodes and videos use `mediaTitle` — the same titles as /listen/ and /watch/.) */
  eleventyConfig.addFilter("homeTitle", (i, lang) => {
    let t = String(pickLang(i, "title", lang) || "");
    if (/%[0-9a-f]{2}/i.test(t)) { try { t = decodeURIComponent(t); } catch { /* keep as is */ } }
    t = t.normalize("NFC");
    // File-name style ("Back-Issue-30-Packs-Form_082026"): 2+ hyphen/underscore joins, or no spaces at all.
    if ((t.match(/[\p{L}\p{N}][-_]+[\p{L}\p{N}]/gu) || []).length >= 2 || (!/\s/.test(t) && /[-_]/.test(t))) t = t.replace(/\.(pdf|docx?|pptx?|jpe?g|png)$/i, "").replace(/[-_]+/g, " ");
    // Podcast/video titles (should one ever come here): drop the "[Season 11, Episode 12]" tail.
    if (i && (i.kind === "episode" || i.kind === "video")) t = t.replace(SEASON_TAIL, "");
    return t.trim();
  });
}
