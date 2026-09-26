// Eleventy filters for the media area: /listen/ (podcasts), /watch/ (YouTube),
// /instagram/ and the JSON indexes written by src/pages/media-index.11ty.js.
//
// Everything here is defensive: the daily sync may deliver partial items
// (missing extra, dates, images…) and the pages must still build.
//
// What the pages read from data/site/*.json (see docs/DATA_SCHEMA.md §5):
//   episodes.json  items + `shows`     [{key, name, image, i18n.description, apple, spotify, …}]
//   videos.json    items + `playlists` [{id, title, lang, count, url, i18n.title}]
//   instagram.json items + `profiles`  {gv: {username, full_name, followers, posts, avatar}, lv: …}
//
// QA / development hooks (never set these in the GitHub workflow):
//   MEDIA_EMPTY=1          → media pages render as if nothing was synced yet
//   MEDIA_DATA_DIR=<dir>   → read <dir>/episodes.json, videos.json, instagram.json
//                            instead of data/site (for stress-testing big lists)
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { scriptJson } from "../script-json.js";

const require = createRequire(import.meta.url);

/* How many items the pages render on the server (no-JS / SEO). The rest is
   loaded from the JSON index by assets/js/media.js ("Load more"). Keep in sync
   with PAGE in media.js. */
export const MEDIA_SSR = 24;

/* Titles arrive clean from the sync ("Gated Communities [Season 11, Episode 12]" /
   "Comunidades cerradas [Temporada 11, Episodio 12]"); the numbering is shown as
   a localized badge, so the bracket is removed from the display title. */
const SE_SUFFIX = /\s*[\[(]\s*(?:season|temporada)\s*\d+\s*[,;.·-]?\s*(?:episode|episodio|ep\.?)\s*\d+\s*[\])]\s*$/i;
const ONE_SUFFIX = /\s*[\[(]\s*(?:season|temporada|episode|episodio)\s*\d+\s*[\])]\s*$/i;
const PODCAST_TITLE = /[\[(]\s*(?:season|temporada)\s*(\d+)\s*[,;.·-]?\s*(?:episode|episodio)\s*(\d+)\s*[\])]/i;
const WEEKLY_OPEN = /weekly\s+open|reuni[oó]n\s+abierta\s+semanal|open\s+aa\s+meeting/i;
/* "Grapevine Weekly Open AA Meeting, September 16, 2026" → "Meeting of September 16, 2026"
   (the show is already shown as a badge next to the title). */
const WO_PREFIXES = ["Grapevine Weekly Open AA Meeting", "Grapevine Weekly Open Meeting", "Grapevine Weekly Open"];
const IG_GENERIC_TITLE = /^\s*(?:aa\s+grapevine|la\s+vi[ñn]a)\s*[—–-]\s*instagram\s*$/i;
const LOCALES = { en: "en-US", es: "es-US" };

function toTime(v) {
  if (!v) return 0;
  const s = typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v + "T12:00:00Z" : v;
  const t = new Date(s).getTime();
  return Number.isFinite(t) ? t : 0;
}

/* MEDIA_DATA_DIR: the whole site file (items + envelope keys) or null. */
function readOverride(name) {
  const dir = process.env.MEDIA_DATA_DIR;
  if (!dir) return null;
  try {
    const data = JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), "utf8"));
    return data && typeof data === "object" ? data : null;
  } catch (e) {
    console.warn(`[media] MEDIA_DATA_DIR: could not read ${name}.json: ${e.message}`);
    return null;
  }
}
function fileOf(dbFile, name) {
  if (process.env.MEDIA_EMPTY) return {};
  return readOverride(name) || (dbFile && typeof dbFile === "object" ? dbFile : {});
}

/** Published items of a db file, newest first, with deleted ("gone") items and
    duplicates removed (videos by YouTube id — list keys in media.js must be unique). */
function mediaItems(dbFile, name) {
  const f = fileOf(dbFile, name);
  const raw = Array.isArray(f.items) ? f.items : [];
  const seen = new Set();
  return raw
    .filter((i) => i && typeof i === "object" && i.status !== "gone" && i.id)
    .map((i) => ({ ...i, extra: i.extra || {} }))
    // newest publish date first; items with an unknown date go last (their
    // first_seen is just "when the bot noticed them", not a publish date)
    .sort((a, b) => toTime(b.date) - toTime(a.date) || toTime(b.first_seen) - toTime(a.first_seen))
    .filter((i) => {
      const key = name === "videos" ? i.extra.video_id || String(i.id).replace(/^yt:/, "") : i.id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function num(v) {
  const n = parseInt(String(v ?? "").replace(/[^\d]/g, ""), 10);
  return Number.isFinite(n) ? n : null;
}

/** Season/episode numbers: from extra (podcasts, podcast videos) or the ORIGINAL title. */
function seOf(item) {
  const x = (item && item.extra) || {};
  let se = num(x.season), ep = num(x.episode);
  if (se === null && ep === null) {
    const m = PODCAST_TITLE.exec(String((item && item.title) || ""));
    if (m) { se = num(m[1]); ep = num(m[2]); }
  }
  return { se, ep };
}

/** Remove the numbering bracket. Defensive fallback for a numbered item whose
    (translated) title ends in some other bracket that carries the SAME numbers,
    e.g. a mangled "[septiembre 11, Episodio 12]" — never strips "(Part 2)" of E5. */
function stripNumbering(t, se = null, ep = null) {
  const orig = String(t || "").trim();
  let s = orig.replace(SE_SUFFIX, "").replace(ONE_SUFFIX, "");
  if (s === orig && (se !== null || ep !== null)) {
    const m = /\s*[\[(]([^\[\]()]{1,60})[\])]\s*$/.exec(orig);
    if (m) {
      const nums = (m[1].match(/\d+/g) || []).map(Number);
      if (nums.length <= 3 && (ep === null || nums.includes(ep)) && (se === null || nums.includes(se))) s = orig.slice(0, m.index);
    }
  }
  s = s.trim();
  return s || orig;
}

function escapeRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

/* ------------------------------------------------------------------ ALL-CAPS
   A few channel playlists/titles are written in capitals ("UN DIA A LA VEZ",
   "STAYING SOBER"). Shown as "Un dia a la vez" / "Staying Sober"; acronyms and
   brand names keep their spelling. Only multi-word, all-capital strings change. */
const KEEP_UPPER = new Set(["AA", "ASL", "GVR", "RLV", "CTM", "ICYPAA", "EURYPAA", "YPAA", "NETA", "USA", "EE.UU", "TV", "PDF", "SOS", "II", "III", "IV"]);
const SMALL_EN = new Set(["a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with"]);
const BRANDS = [[/\bgrapevine\b/gi, "Grapevine"], [/\bla viña\b/gi, "La Viña"], [/\bla vina\b/gi, "La Viña"], [/\bzoom\b/gi, "Zoom"]];
function cap(w) { return w.replace(/^([^\p{L}]*)(\p{L})/u, (m, a, b) => a + b.toLocaleUpperCase()); }
function softCaps(s, lang = "en") {
  s = String(s || "");
  const letters = s.replace(/[^\p{L}]/gu, "");
  const words = s.split(/\s+/).filter((w) => w.replace(/[^\p{L}]/gu, "").length >= 2);
  if (letters.length < 6 || words.length < 2 || letters !== letters.toLocaleUpperCase() || letters === letters.toLocaleLowerCase()) return s;
  const loc = lang === "es" ? "es" : "en";
  let first = true, afterColon = false;
  const out = s.split(/(\s+)/).map((w) => {
    if (!w || /^\s+$/.test(w)) return w;
    const core = w.replace(/[^\p{L}.]/gu, "").replace(/\.$/, "");
    const poss = /^(.*?)([’']S)([^\p{L}]*)$/u.exec(w);
    let r;
    if (KEEP_UPPER.has(core) || /\d/.test(w)) r = w;
    else if (poss && KEEP_UPPER.has(poss[1].replace(/[^\p{L}]/gu, ""))) r = poss[1] + poss[2].toLowerCase() + poss[3];
    else {
      const lower = w.toLocaleLowerCase(loc);
      if (first || afterColon) r = cap(lower);
      else if (loc === "es") r = lower;
      else r = SMALL_EN.has(lower.replace(/[^\p{L}]/gu, "")) ? lower : cap(lower);
    }
    first = false;
    afterColon = /[:\-–—]$/.test(w);
    return r;
  }).join("");
  return BRANDS.reduce((acc, [re, v]) => acc.replace(re, v), out);
}

/** Video "type" used by the Watch filters. */
function videoKind(item) {
  const x = (item && item.extra) || {};
  if (x.is_short) return "short";
  const title = String(item.title || "");
  const lists = (x.playlists || []).join(" | ");
  if (WEEKLY_OPEN.test(title) || (!PODCAST_TITLE.test(title) && WEEKLY_OPEN.test(lists))) return "weekly";
  if (PODCAST_TITLE.test(title) || /podcast/i.test(lists)) return "podcast";
  return "video";
}
/** A playlist that is the same thing as a type chip (Weekly Open / Podcast). */
function isKindPlaylist(title) { return WEEKLY_OPEN.test(String(title || "")) || /podcast/i.test(String(title || "")); }

/** Video language bucket: prefer the category from the sync (gv/lv), else the detected language. */
function videoLang(item) {
  if (item.category === "lv" || item.category === "gv") return item.category;
  return item.lang === "es" ? "lv" : "gv";
}

/** Human duration: "32 min", "1 h 05 min". Empty when unknown. */
function minutes(sec) {
  sec = Math.round(Number(sec) || 0);
  if (sec <= 0) return "";
  const m = Math.max(1, Math.round(sec / 60));
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

function safeUrl(u, fallback = "#") {
  const s = String(u || "").trim();
  return /^(https?:\/\/|\/(?!\/))/i.test(s) ? s : fallback;
}

/* ------------------------------------------------------------------ icons
   Icons repeated in every list row/card (24+ times per page) come from ONE
   inline SVG sprite per page ({% mediaSprite %}) and are referenced with
   {% micon "play", "size-4" %} → <svg><use href="#mi-play"/></svg>.
   Saves ~100 KB of HTML per page vs. inlining each SVG. Other icons keep
   using the shared {% icon %} shortcode. */
const SPRITE_ICONS = ["play", "pause", "loader-circle", "clock-3", "check", "chevron-down", "languages", "headphones",
  "grapes", "users", "smartphone", "list-video", "arrow-up-right", "square-play", "x", "clapperboard", "gallery-horizontal-end", "instagram"];
let spriteCache = null;
function sprite() {
  if (spriteCache) return spriteCache;
  let lucideDir = "";
  try { lucideDir = path.join(path.dirname(require.resolve("lucide-static/package.json")), "icons"); } catch (e) { /* not installed */ }
  const parts = [];
  for (const name of SPRITE_ICONS) {
    const local = path.join("src/_includes/icons", `${name}.svg`);
    const file = fs.existsSync(local) ? local : path.join(lucideDir, `${name}.svg`);
    if (!fs.existsSync(file)) { console.warn(`[media] sprite: missing icon ${name}`); continue; }
    const svg = fs.readFileSync(file, "utf8").replace(/<!--.*?-->/gs, "");
    const inner = (svg.match(/<svg[^>]*>([\s\S]*)<\/svg>/) || [])[1] || "";
    const sw = (svg.match(/stroke-width="([\d.]+)"/) || [])[1] || "2";
    parts.push(`<symbol id="mi-${name}" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${inner.trim()}</g></symbol>`);
  }
  spriteCache = `<svg xmlns="http://www.w3.org/2000/svg" style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true" focusable="false">${parts.join("")}</svg>`;
  return spriteCache;
}
function micon(name, cls = "size-5", label = "") {
  if (!SPRITE_ICONS.includes(name)) console.warn(`[media] micon: "${name}" is not in SPRITE_ICONS`);
  const a11y = label ? `role="img" aria-label="${String(label).replace(/"/g, "&quot;")}"` : `aria-hidden="true" focusable="false"`;
  return `<svg class="icon ${cls}" ${a11y}><use href="#mi-${name}"/></svg>`;
}

/** JSON that is safe inside <script type="application/json"> and HTML attributes (the shared serializer). */
const safeJson = scriptJson;

export default function (eleventyConfig, helpers) {
  const { translateKey, pickLang } = helpers;
  const tx = (item, field, lang) => String(pickLang(item, field, lang) || "");
  const machine = (item, lang) => !!(item && Array.isArray(item.machine) && item.machine.includes(lang));

  /* ------------------------------------------------------------ titles */

  /** "Grapevine Weekly Open AA Meeting, September 16, 2026" → "Meeting of September 16, 2026". */
  function stripShowPrefix(t, prefixes, lang) {
    for (const p of prefixes) {
      if (!p || p.length < 6) continue;
      const m = new RegExp("^\\s*" + escapeRe(p) + "\\s*[,:–—-]\\s*", "i").exec(t);
      if (!m) continue;
      const rest = t.slice(m[0].length).trim();
      if (rest.length < 3) return t;
      if (WEEKLY_OPEN.test(p) && /\b(?:19|20)\d{2}\b/.test(rest) && rest.length <= 40) return translateKey("media.meeting_of", lang, { date: rest });
      return rest;
    }
    return t;
  }

  /** Display title in the page language: numbering bracket and show-name prefix removed. */
  function displayTitle(item, lang) {
    if (!item) return "";
    const { se, ep } = seOf(item);
    const x = item.extra || {};
    let t = stripNumbering(tx(item, "title", lang), se, ep);
    const weekly = item.kind === "episode" ? WEEKLY_OPEN.test(String(x.show_name || "")) : videoKind(item) === "weekly";
    if (weekly) t = stripShowPrefix(t, [x.show_name, ...WO_PREFIXES], lang);
    return softCaps(t, lang);
  }

  /* ------------------------------------------------------------ podcasts */

  function showKeyOf(i) { return (i && i.extra && i.extra.show) || (i && i.category) || "gv"; }

  /** Short label for a show badge: "Weekly Open" for the meeting show, else its name. */
  function showShort(key, name, lang) {
    if (key === "wo" || WEEKLY_OPEN.test(String(name || ""))) return translateKey("media.show_short_weekly", lang);
    return String(name || key || "");
  }

  /** Shows in config order, merged from episodes.json `shows`, config sources.podcasts
      and the items themselves. Only shows that have at least one episode. */
  function showList(epFile, cfgShows, items, lang) {
    const f = fileOf(epFile, "episodes");
    const env = Array.isArray(f.shows) ? f.shows : f.shows && typeof f.shows === "object" ? Object.values(f.shows) : [];
    const byKey = new Map();
    // envelope first (has i18n + artwork), config fills whatever the envelope lacks
    const add = (s) => {
      if (!s || !s.key) return;
      const cur = byKey.get(s.key) || {};
      for (const [k, v] of Object.entries(s)) if (cur[k] === undefined || cur[k] === null || cur[k] === "") cur[k] = v;
      byKey.set(s.key, cur);
    };
    env.forEach(add);
    (cfgShows || []).forEach(add);
    const groups = new Map();
    for (const i of items || []) {
      const k = showKeyOf(i);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(i);
      if (!byKey.has(k)) byKey.set(k, { key: k, name: (i.extra && i.extra.show_name) || k });
    }
    const out = [];
    for (const [key, s] of byKey) {
      const eps = groups.get(key) || [];
      if (!eps.length) continue;
      const latest = eps[0];
      const seasons = new Map();
      for (const e of eps) { const n = num(e.extra && e.extra.season); if (n !== null) seasons.set(n, (seasons.get(n) || 0) + 1); }
      const x = latest.extra || {};
      const name = s.name || s.title || x.show_name || key;
      const image = safeUrl(s.thumb, "") || safeUrl(s.image, "") || imageFor(eps);
      out.push({
        key,
        name,
        short: showShort(key, name, lang),
        weekly: key === "wo" || WEEKLY_OPEN.test(name),
        desc: s.i18n ? tx(s, "description", lang) : String(s.description || ""),
        descMachine: machine(s, lang),
        image,
        web: safeUrl(s.web || x.show_web, ""),
        apple: safeUrl(s.apple || x.apple, ""),
        spotify: safeUrl(s.spotify || x.spotify, ""),
        amazon: safeUrl(s.amazon || x.amazon, ""),
        feed: safeUrl(s.feed, ""),
        count: eps.length,
        seasons: [...seasons.entries()].sort((a, b) => b[0] - a[0]).map(([k, count]) => ({ key: String(k), count })),
        latest,
      });
    }
    return out;
  }

  /** Most common artwork of a list of episodes. */
  function imageFor(eps) {
    const c = new Map();
    for (const e of eps) { const a = (e.extra && e.extra.thumb) || e.image; if (a) c.set(a, (c.get(a) || 0) + 1); }
    const best = [...c.entries()].sort((a, b) => b[1] - a[1])[0];
    return best ? safeUrl(best[0], "") : "";
  }

  /** {gv: {name, short, i}} for media.js (mini-player subtitle, row badges, artwork fallback). */
  function showsMeta(shows) {
    const out = {};
    for (const s of shows) out[s.key] = { name: s.name, short: s.short, i: s.image || null };
    return out;
  }

  /** Episode page (Captivate player page) — extra.link is only the show's generic page. */
  function episodePage(item) {
    const x = item.extra || {};
    return safeUrl(item.url, "") || safeUrl(x.player_url, "") || safeUrl(x.link, "") || safeUrl(x.show_web, "");
  }

  function epCompact(item, lang, shows) {
    const x = item.extra || {};
    const show = showKeyOf(item);
    const { se, ep } = seOf(item);
    const meta = shows.find((s) => s.key === show);
    const o = {
      id: item.id,
      t: displayTitle(item, lang),
      s: tx(item, "summary", lang),
      d: item.date || item.first_seen || null,
      du: Math.round(Number(x.duration_sec) || 0) || null,
      se,
      ep,
      a: safeUrl(x.audio_url, null),
      sh: show,
      l: item.lang || "und",
      u: episodePage(item) || null,
    };
    const art = (item.extra && item.extra.thumb) || item.image;
    if (art && (!meta || art !== meta.image)) o.i = safeUrl(art, null);
    if (Number(x.audio_bytes) > 0) o.b = Math.round(Number(x.audio_bytes)); // audio file size — shown while Data saver is on (pwa.js)
    if (item.is_new) o.n = 1;
    if (machine(item, lang)) o.m = 1;
    return o;
  }

  /* ------------------------------------------------------------ videos */

  /** Channel playlists (videos.json `playlists`, channel order) with display titles in
      the page language + item counts. Names used by items but missing from the
      envelope are appended so every item's playlist resolves. */
  function playlistList(vidFile, items, lang) {
    const f = fileOf(vidFile, "videos");
    const env = Array.isArray(f.playlists) ? f.playlists : [];
    const counts = new Map();
    for (const i of items || []) for (const p of (i.extra && i.extra.playlists) || []) if (p) counts.set(p, (counts.get(p) || 0) + 1);
    const out = [], seen = new Set();
    const push = (orig, p) => {
      if (!orig || seen.has(orig)) return;
      seen.add(orig);
      const plang = p && (p.lang === "es" || p.lang === "en") ? p.lang : "en";
      const title = softCaps((p && p.i18n ? tx(p, "title", lang) : "") || orig, lang);
      out.push({
        i: out.length,
        orig,
        title,
        lang: plang,
        count: counts.get(orig) || 0,
        url: safeUrl(p && p.url, ""),
        machine: !!(p && machine(p, lang)),
        kind: isKindPlaylist(orig),
      });
    };
    env.forEach((p) => push(p && p.title, p));
    for (const name of counts.keys()) push(name, null);
    return out.filter((p) => p.count > 0).map((p, i) => ({ ...p, i }));
  }

  /** The playlist name to show on a card: the first one that isn't a type (Weekly Open/Podcast). */
  function cardPlaylist(item, pls) {
    for (const name of (item.extra && item.extra.playlists) || []) {
      const p = pls.find((q) => q.orig === name);
      if (p && !p.kind) return p;
    }
    return null;
  }

  function videoCompact(item, lang, pls) {
    const x = item.extra || {};
    const { se, ep } = seOf(item);
    const o = {
      id: x.video_id || String(item.id || "").replace(/^yt:/, ""),
      t: displayTitle(item, lang),
      d: item.date || item.first_seen || null,
      du: Math.round(Number(x.duration_sec) || 0) || null,
      c: videoLang(item),
      k: videoKind(item),
      l: item.lang || "und",
    };
    if (x.date_approx) o.a = 1;
    if (se !== null) o.se = se;
    if (ep !== null) o.ep = ep;
    // playlist indexes; type playlists (Weekly Open / Podcast) are covered by the type chips
    const pl = ((x.playlists || []).map((n) => pls.find((p) => p.orig === n)).filter((p) => p && !p.kind)).map((p) => p.i);
    if (pl.length) o.p = pl;
    if (item.image && !/i\.ytimg\.com\/vi\//.test(item.image)) o.i = safeUrl(item.image, null);
    if (item.is_new) o.n = 1;
    if (machine(item, lang)) o.m = 1;
    return o;
  }

  /* ------------------------------------------------------------ icons */
  eleventyConfig.addShortcode("mediaSprite", () => sprite());
  eleventyConfig.addShortcode("micon", (name, cls, label) => micon(name, cls, label));

  /* ------------------------------------------------------------ filters */

  eleventyConfig.addFilter("mediaItems", mediaItems);
  eleventyConfig.addFilter("mediaCleanTitle", (s) => stripNumbering(s));
  eleventyConfig.addFilter("mediaSoftCaps", (s, lang) => softCaps(s, lang));
  /** Item title in the page language, numbering/show prefix removed: {{ item | mediaTitle(lang) }} */
  eleventyConfig.addFilter("mediaTitle", (item, lang) => displayTitle(item, lang));
  eleventyConfig.addFilter("mediaVideoKind", videoKind);
  eleventyConfig.addFilter("mediaVideoLang", videoLang);
  eleventyConfig.addFilter("mediaMinutes", minutes);
  eleventyConfig.addFilter("mediaNum", num);
  eleventyConfig.addFilter("mediaEpisodePage", (item) => (item ? episodePage(item) : ""));

  /** Shows with their episodes' stats: {{ db.episodes | mediaShowList(site.sources.podcasts, eps, lang) }} */
  eleventyConfig.addFilter("mediaShowList", (epFile, cfgShows, items, lang) => showList(epFile, cfgShows, items, lang));
  /** Short label of an episode's show ("Weekly Open"), for row badges. */
  eleventyConfig.addFilter("mediaShowOf", (item, shows) => (shows || []).find((s) => s.key === showKeyOf(item)) || null);

  /** Season chips over all episodes: [{key:"11", count}] newest season first. */
  eleventyConfig.addFilter("mediaSeasons", (items) => {
    const c = new Map();
    for (const i of items || []) { const s = num(i.extra && i.extra.season); if (s !== null) c.set(s, (c.get(s) || 0) + 1); }
    return [...c.entries()].sort((a, b) => b[0] - a[0]).map(([key, count]) => ({ key: String(key), count }));
  });

  /** Counts by an arbitrary accessor name: "lang" | "videoLang" | "videoKind". */
  eleventyConfig.addFilter("mediaCount", (items, by) => {
    const out = {};
    for (const i of items || []) {
      const k = by === "videoLang" ? videoLang(i) : by === "videoKind" ? videoKind(i) : (i[by] ?? "other");
      out[k] = (out[k] || 0) + 1;
    }
    return out;
  });

  eleventyConfig.addFilter("mediaSumDuration", (items) => (items || []).reduce((s, i) => s + (Number(i.extra && i.extra.duration_sec) || 0), 0));

  /** Newest video that is not a Short (for the featured player). */
  eleventyConfig.addFilter("mediaFeaturedVideo", (items) => (items || []).find((i) => videoKind(i) !== "short") || (items || [])[0] || null);

  /** The list without one item (Watch: the grid skips the featured video shown right above it). */
  eleventyConfig.addFilter("mediaWithout", (items, item) => (items || []).filter((i) => !item || i !== item && i.id !== item.id));

  /** Playlists for the Watch filters: {{ db.videos | mediaPlaylists(vids, lang) }} */
  eleventyConfig.addFilter("mediaPlaylists", (vidFile, items, lang) => playlistList(vidFile, items, lang));
  /** Chips: up to n non-type playlists, the page's language first, biggest first. */
  eleventyConfig.addFilter("mediaPlaylistChips", (pls, lang, n = 4) => {
    const want = lang === "es" ? "es" : "en";
    return (pls || []).filter((p) => !p.kind && p.count >= 3)
      .sort((a, b) => (a.lang === want ? 0 : 1) - (b.lang === want ? 0 : 1) || b.count - a.count || a.i - b.i)
      .slice(0, n);
  });
  /** Playlists grouped by language for the <select>: [{lang, items}] page language first. */
  eleventyConfig.addFilter("mediaPlaylistGroups", (pls, lang) => {
    const want = lang === "es" ? "es" : "en";
    const groups = [want, want === "es" ? "en" : "es"].map((l) => ({ lang: l, items: (pls || []).filter((p) => !p.kind && p.lang === l) }));
    return groups.filter((g) => g.items.length);
  });
  /** The playlist to show on a video card (not the Weekly Open / Podcast one), or null. */
  eleventyConfig.addFilter("mediaCardPlaylist", (item, pls) => cardPlaylist(item, pls || []));

  /** "S11 · E12" / "T11 · E12" (empty when unknown). */
  eleventyConfig.addFilter("mediaSE", (item, lang) => {
    const { se: s, ep: e } = seOf(item);
    if (s !== null && e !== null) return translateKey("media.se_short", lang, { s, e });
    if (e !== null) return translateKey("media.ep_short", lang, { e });
    if (s !== null) return translateKey("media.season_n", lang, { n: s });
    return "";
  });

  /** Publish date as text; YouTube dates marked approximate show month + year only. */
  eleventyConfig.addFilter("mediaVideoDate", (item, lang, style = "short") => {
    if (!item || !item.date) return "";
    return helpers.fmtDate(item.date, lang, item.extra && item.extra.date_approx ? "month" : style);
  });

  /* ------------------------------------------------------------ instagram */

  /** Instagram caption: the summary, else a meaningful title (not the generic "AA Grapevine — Instagram"). */
  eleventyConfig.addFilter("mediaIgCaption", (item, lang) => {
    const s = tx(item, "summary", lang).trim();
    if (s) return s;
    const t = tx(item, "title", lang).trim();
    return IG_GENERIC_TITLE.test(t) || IG_GENERIC_TITLE.test(String(item.title || "")) ? "" : t;
  });

  /** Local cached thumbnail for an Instagram post (item.image or extra.thumb), else "". */
  eleventyConfig.addFilter("mediaIgImage", (item) => safeUrl((item && (item.image || (item.extra && item.extra.thumb))) || "", ""));

  /** Posts of one Instagram account (by category, extra.account or username). */
  eleventyConfig.addFilter("mediaIgFor", (items, acc) => (items || []).filter((i) => {
    const x = i.extra || {};
    return i.category === acc.key || x.account === acc.key || (acc.username && x.username === acc.username);
  }));

  /** Profile of an account from instagram.json `profiles` (avatar, followers …) merged over config. */
  eleventyConfig.addFilter("mediaIgProfile", (igFile, acc) => {
    const f = fileOf(igFile, "instagram");
    const p = (f.profiles && acc && f.profiles[acc.key]) || {};
    const n = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null);
    return {
      username: p.username || (acc && acc.username) || "",
      name: (acc && acc.name) || p.name || "",
      fullName: p.full_name && p.full_name !== ((acc && acc.name) || p.name) ? String(p.full_name) : "",
      url: safeUrl(p.url, "") || `https://www.instagram.com/${(acc && acc.username) || p.username || ""}/`,
      avatar: safeUrl(p.avatar, ""),
      followers: n(p.followers),
      posts: n(p.posts),
    };
  });

  /** ["AA Grapevine's Podcast", "Grapevine Weekly Open AA Meeting"] → "… and …" / "… y …". */
  eleventyConfig.addFilter("mediaListNames", (list, lang, attr = "name") => {
    const names = (list || []).map((x) => (attr && x && typeof x === "object" ? x[attr] : x)).filter(Boolean).map(String);
    try { return new Intl.ListFormat(LOCALES[lang] || "en-US", { type: "conjunction" }).format(names); } catch (e) { return names.join(", "); }
  });

  /** 16867 → "16.9K" (compact), 3896 → "3,896". */
  eleventyConfig.addFilter("mediaNumber", (v, lang, compact = false) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "";
    const opts = compact && n >= 10000 ? { notation: "compact", maximumFractionDigits: 1 } : {};
    try { return new Intl.NumberFormat(LOCALES[lang] || "en-US", opts).format(n); } catch (e) { return String(n); }
  });

  /** Official embed URL for a post — only ever instagram.com (never trust arbitrary URLs in an iframe). */
  eleventyConfig.addFilter("mediaIgEmbed", (item) => {
    const x = (item && item.extra) || {};
    const u = String(x.embed_url || "");
    if (/^https:\/\/www\.instagram\.com\/(?:p|reel|tv)\/[\w-]+\/embed\/?(?:captioned\/?)?$/.test(u)) return u;
    if (/^[\w-]{5,}$/.test(String(x.shortcode || ""))) return `https://www.instagram.com/p/${x.shortcode}/embed/captioned/`;
    return "";
  });

  /** Plain text → HTML with http(s) links made clickable (shown as "host/…").
      Everything is HTML-escaped first, so it's safe to output with `| safe`. */
  eleventyConfig.addFilter("mediaLinkify", (text) => {
    const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    return esc(text || "").replace(/https?:\/\/[^\s<>"']+/g, (m) => {
      const url = m.replace(/[).,;:!?\]]+$/, "");
      const tail = m.slice(url.length);
      let label = url;
      try { const u = new URL(url.replace(/&amp;/g, "&")); label = u.hostname.replace(/^www\./, "") + (u.pathname.length > 1 ? "/…" : ""); } catch (e) { /* keep raw */ }
      return `<a class="link break-all" href="${url}" target="_blank" rel="noopener">${label}</a>${tail}`;
    });
  });

  /** Only http(s) or site-relative URLs make it into href/src attributes. */
  eleventyConfig.addFilter("mediaSafeUrl", (u, fallback = "#") => safeUrl(u, fallback));

  /** Script-safe JSON (use inside <script type="application/json"> or attributes). */
  eleventyConfig.addFilter("mediaJson", safeJson);

  /** JSON for the first N rows rendered on the server (same format as the index).
      episodes: extra = shows list (mediaShowList); videos: extra = playlists (mediaPlaylists). */
  eleventyConfig.addFilter("mediaInitialJson", (items, kind, lang, extra) => {
    items = items || [];
    if (kind === "episodes") {
      const shows = extra || [];
      const latest = {};
      for (const s of shows) latest[s.key] = epCompact(s.latest, lang, shows);
      return safeJson({ shows: showsMeta(shows), latest, items: items.slice(0, MEDIA_SSR).map((i) => epCompact(i, lang, shows)) });
    }
    const pls = extra || [];
    return safeJson({ playlists: pls.map((p) => p.title), items: items.slice(0, MEDIA_SSR).map((i) => videoCompact(i, lang, pls)) });
  });

  /** Full JSON index file body: /episodes-index.json, /videos-index.json (+ /es/…). */
  eleventyConfig.addFilter("mediaIndexJson", (db, kind, lang, cfgShows) => {
    const updated = new Date().toISOString();
    if (kind === "episodes") {
      const items = mediaItems(db && db.episodes, "episodes");
      const shows = showList(db && db.episodes, cfgShows, items, lang);
      return JSON.stringify({ v: 2, kind, lang, updated, total: items.length, shows: showsMeta(shows), items: items.map((i) => epCompact(i, lang, shows)) });
    }
    const items = mediaItems(db && db.videos, "videos");
    const pls = playlistList(db && db.videos, items, lang);
    return JSON.stringify({ v: 2, kind, lang, updated, total: items.length, playlists: pls.map((p) => p.title), items: items.map((i) => videoCompact(i, lang, pls)) });
  });

  /** A small dictionary of UI strings for media.js in the page language:
      ["media.play", "common.show_more"] → {"play": "…", "show_more": "…"} (last key segment). */
  eleventyConfig.addFilter("mediaI18n", (keys, lang) => {
    const o = {};
    for (const k of keys || []) o[k.replace(/^.*\./, "")] = translateKey(k, lang);
    return safeJson(o);
  });

  /** The weekly open meetings as ONE short line for /listen/ and /watch/, which only point to their
      canonical home (/meetings/#weekly-open — Zoom details live there only):
      db.weekly_open.items → "Wednesdays (English) and Thursdays (Spanish, from November 5)" /
      "miércoles (inglés) y jueves (español, desde el 5 de noviembre)". Weekday order; a meeting
      that has not started yet (extra.starts after today, Central time) says from when.
      source ("grapevine" | "lavina"): only that meeting, as its bare day ("Wednesdays") — for a
      card about one show (the Grapevine Weekly Open podcast on /listen/). */
  eleventyConfig.addFilter("mediaWeeklyDays", (items, lang = "en", source = "") => {
    const WD = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    const parts = (items || [])
      .filter((it) => it && it.kind === "meeting" && it.status !== "gone" && (!source || it.source === source))
      .map((it, i) => ({ it, i, wd: WD.indexOf(String(it.extra?.weekday || "").toLowerCase()) }))
      .sort((a, b) => (a.wd < 0 ? 9 : a.wd) - (b.wd < 0 ? 9 : b.wd) || a.i - b.i)
      .map(({ it }) => {
        let day = tx(it, "day", lang);
        if (!day) return "";
        if (lang === "es") day = day.toLowerCase(); // "Miércoles" inside a sentence
        if (source) return day;
        const language = translateKey(`media.wo_lang_${it.lang === "es" ? "es" : "en"}`, lang);
        const starts = String(it.extra?.starts || "");
        if (/^\d{4}-\d{2}-\d{2}$/.test(starts) && starts > today) {
          const date = new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { month: "long", day: "numeric", timeZone: "UTC" }).format(new Date(starts + "T12:00:00Z"));
          return translateKey("media.wo_day_lang_from", lang, { day, lang: language, date });
        }
        return translateKey("media.wo_day_lang", lang, { day, lang: language });
      })
      .filter(Boolean);
    if (parts.length < 2) return parts[0] || "";
    return translateKey("media.wo_days_two", lang, { a: parts.slice(0, -1).join(", "), b: parts[parts.length - 1] });
  });

}
