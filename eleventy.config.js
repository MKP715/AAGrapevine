// Eleventy configuration — NETA 65 Grapevine / La Viña site.
// Shared filters live here; page-area specific filters can be added as
// separate files in ./eleventy/filters/*.js (auto-loaded, see bottom).
import { EleventyHtmlBasePlugin } from "@11ty/eleventy";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import markdownIt from "markdown-it";

const require = createRequire(import.meta.url);
const md = markdownIt({ html: false, linkify: true, breaks: true });
const TZ = "America/Chicago";
const LOCALES = { en: "en-US", es: "es-US" };
const LUCIDE_DIR = path.join(path.dirname(require.resolve("lucide-static/package.json")), "icons");
const iconCache = new Map();

/* ------------------------------------------------------------------ */
/*  i18n helpers                                                       */
/* ------------------------------------------------------------------ */
let I18N = null;
function loadI18n() {
  const dir = "src/_i18n";
  const out = {};
  for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".json")).sort()) {
    const data = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
    for (const [k, v] of Object.entries(data)) out[k] = v;
  }
  return out;
}

function interpolate(str, vars) {
  if (!vars || typeof str !== "string") return str;
  return str.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined && vars[k] !== null ? String(vars[k]) : m));
}

export function translateKey(key, lang = "en", vars) {
  if (!I18N) I18N = loadI18n();
  const entry = I18N[key];
  if (!entry) {
    if (process.env.I18N_STRICT) throw new Error(`Missing i18n key: ${key}`);
    return key;
  }
  const s = entry[lang] ?? entry.en ?? key;
  return interpolate(s, vars);
}

function pickLang(item, field, lang) {
  if (!item) return "";
  const i = item.i18n && item.i18n[field];
  if (i && (i[lang] || i[lang] === "")) return i[lang] || i[item.lang] || item[field] || "";
  if (i && i.en) return i.en;
  if (item[field + "_" + lang]) return item[field + "_" + lang];
  if (item.extra && item.extra[field] !== undefined) return item.extra[field];
  return item[field] ?? "";
}

/* ------------------------------------------------------------------ */
/*  Dates                                                              */
/* ------------------------------------------------------------------ */
function toDate(v) {
  if (!v) return null;
  if (v instanceof Date) return v;
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return new Date(v + "T12:00:00Z"); // date-only: noon UTC avoids TZ day shift
  const d = new Date(v);
  return isNaN(d) ? null : d;
}

function fmtDate(v, lang = "en", style = "long") {
  const d = toDate(v);
  if (!d) return "";
  const loc = LOCALES[lang] || "en-US";
  const opts = {
    short: { month: "short", day: "numeric", year: "numeric", timeZone: TZ },
    long: { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: TZ },
    medium: { month: "long", day: "numeric", year: "numeric", timeZone: TZ },
    month: { month: "long", year: "numeric", timeZone: TZ },
    monthShort: { month: "short", timeZone: TZ },
    day: { day: "numeric", timeZone: TZ },
    weekday: { weekday: "short", timeZone: TZ },
    time: { hour: "numeric", minute: "2-digit", timeZone: TZ, timeZoneName: "short" },
    datetime: { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZone: TZ, timeZoneName: "short" },
  }[style];
  if (style === "iso") return d.toISOString();
  if (style === "ymd") return d.toISOString().slice(0, 10);
  if (style === "relative") return relative(d, lang);
  let s = new Intl.DateTimeFormat(loc, opts || {}).format(d);
  // Spanish writes month names in lowercase ("octubre de 2026"). Only the "long" style, which
  // opens with the weekday and is used as a stand-alone line, gets a capital ("Miércoles, 21 …").
  if (lang === "es" && style === "long") s = s.charAt(0).toUpperCase() + s.slice(1);
  return s;
}

/* ------------------------------------------------------------------ */
/*  Links from data                                                    */
/* ------------------------------------------------------------------ */
// Every link that comes from synced data or hand-edited content (content/districts.yml,
// content/events/*.md, content/announcements/*.md → data/site/*.json) passes through safeUrl()
// before a template writes it into href/src:
//   * http(s)://…, mailto:…, tel:…, "#fragment" and site-relative "/path" are kept;
//   * "//host/…", "www.district5.org" or "zoom.us/j/123" (scheme forgotten) become "https://…";
//   * anything else ("javascript:…", "data:…", relative "foo/bar", a bare file name) becomes ""
//     so the template hides the link instead of printing a broken or unsafe one.
// src/_data/db.js applies it to all URL fields of data/site/*.json; the `extUrl` filter is the
// same function for templates.
const FILE_EXT_TLD = /\.(?:jpe?g|png|gif|webp|avif|svg|pdf|docx?|xlsx?|pptx?|txt|md|html?|php|aspx?|mp3|m4a|mp4|mov|zip)$/i;
const BARE_HOST = /^(?:[\p{L}\p{N}](?:[\p{L}\p{N}-]*[\p{L}\p{N}])?\.)+\p{L}{2,24}(?::\d{1,5})?(?=[/?#]|$)/u;
export function safeUrl(value) {
  if (value === null || value === undefined || typeof value === "object") return "";
  // Browsers ignore tabs/newlines inside URLs ("java\tscript:" still runs), so drop all controls first.
  const s = String(value).trim().replace(/[\u0000-\u001f\u007f]/g, "");
  if (!s) return "";
  if (/^https?:\/\/[^\s/\\?#]/i.test(s)) return s;
  if (/^(?:mailto|tel):[^\s]/i.test(s)) return s;
  if (s.startsWith("#")) return s;
  if (/^\/(?![/\\])/.test(s)) return s; // "/path" (not "//host" or "/\host", which leave the site)
  if (/^\/\/[^\s/\\?#]/.test(s)) return "https:" + s;
  const host = s.match(BARE_HOST);
  if (host && (/^www\./i.test(s) || !FILE_EXT_TLD.test(host[0].replace(/:\d+$/, "")))) return "https://" + s;
  return "";
}

function relative(d, lang) {
  const rtf = new Intl.RelativeTimeFormat(LOCALES[lang] || "en-US", { numeric: "auto" });
  const diff = (d.getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [u, s] of units) if (abs >= s || u === "minute") return rtf.format(Math.round(diff / s), u);
  return "";
}

function dateVal(item) {
  if (!item) return 0;
  const v = item.extra && item.extra.start ? item.extra.start : item.date || item.first_seen;
  const d = toDate(v);
  return d ? d.getTime() : 0;
}

/* ------------------------------------------------------------------ */
export default function (eleventyConfig) {
  const pathPrefix = process.env.PATH_PREFIX || "/";

  eleventyConfig.addPlugin(EleventyHtmlBasePlugin);

  // Dev helper: ONLY=library,search npx @11ty/eleventy  → builds just those src/pages/* files
  if (process.env.ONLY) {
    const keep = process.env.ONLY.split(",").map((s) => s.trim()).filter(Boolean);
    for (const f of fs.readdirSync("src/pages")) {
      if (!keep.some((k) => f.startsWith(k))) eleventyConfig.ignores.add(`src/pages/${f}`);
    }
  }
  // autoescape MUST stay true: titles/captions come from third-party sources (XSS safety). Use | safe only for trusted HTML.
  eleventyConfig.setNunjucksEnvironmentOptions({ autoescape: true, trimBlocks: true, lstripBlocks: true, throwOnUndefined: false });

  // Rebuild i18n when those files change in --serve mode
  eleventyConfig.addWatchTarget("src/_i18n/");
  eleventyConfig.addWatchTarget("data/site/");
  eleventyConfig.addWatchTarget("config/");
  eleventyConfig.on("eleventy.before", () => { I18N = loadI18n(); });

  /* ---------- passthrough ---------- */
  eleventyConfig.addPassthroughCopy({ "src/assets/img": "assets/img" });
  eleventyConfig.addPassthroughCopy({ "src/assets/js": "assets/js" });
  eleventyConfig.addPassthroughCopy({ "src/assets/cache": "assets/cache" });
  eleventyConfig.addPassthroughCopy({ "src/favicon.ico": "favicon.ico" });
  if (fs.existsSync("src/CNAME")) eleventyConfig.addPassthroughCopy({ "src/CNAME": "CNAME" });
  eleventyConfig.addPassthroughCopy({ "src/.nojekyll": ".nojekyll" });
  // Self-hosted open-source vendor libs (no CDN dependency)
  const nm = (p) => "node_modules/" + p;
  eleventyConfig.addPassthroughCopy({
    [nm("alpinejs/dist/cdn.min.js")]: "assets/vendor/alpine.min.js",
    [nm("minisearch/dist/umd/index.js")]: "assets/vendor/minisearch.js",
    [nm("glightbox/dist/js/glightbox.min.js")]: "assets/vendor/glightbox.min.js",
    [nm("glightbox/dist/css/glightbox.min.css")]: "assets/vendor/glightbox.min.css",
    [nm("lite-youtube-embed/src/lite-yt-embed.js")]: "assets/vendor/lite-yt-embed.js",
    [nm("lite-youtube-embed/src/lite-yt-embed.css")]: "assets/vendor/lite-yt-embed.css",
  });
  for (const f of ["inter-latin-wght-normal", "inter-latin-ext-wght-normal", "inter-latin-wght-italic"]) {
    eleventyConfig.addPassthroughCopy({ [nm(`@fontsource-variable/inter/files/${f}.woff2`)]: `assets/fonts/${f}.woff2` });
  }
  for (const f of ["fraunces-latin-full-normal", "fraunces-latin-ext-full-normal", "fraunces-latin-full-italic"]) {
    eleventyConfig.addPassthroughCopy({ [nm(`@fontsource-variable/fraunces/files/${f}.woff2`)]: `assets/fonts/${f}.woff2` });
  }

  /* ---------- i18n filters ---------- */
  eleventyConfig.addFilter("t", (key, lang, vars) => translateKey(key, lang, vars));
  eleventyConfig.addFilter("tx", (item, field, lang) => pickLang(item, field, lang));
  eleventyConfig.addFilter("machineFor", (item, lang) => !!(item && item.machine && item.machine.includes(lang)));
  eleventyConfig.addFilter("lurl", (url, lang) => {
    if (!url || /^(https?:|mailto:|tel:|#)/.test(url)) return url;
    const u = url.startsWith("/") ? url : "/" + url;
    return lang && lang !== "en" ? `/${lang}${u}` : u;
  });
  eleventyConfig.addFilter("altLangUrl", (url, lang) => {
    const u = url || "/";
    if (lang === "es") return u.replace(/^\/es(\/|$)/, "/") || "/";
    return "/es" + (u.startsWith("/") ? u : "/" + u);
  });
  eleventyConfig.addFilter("otherLang", (lang) => (lang === "es" ? "en" : "es"));
  eleventyConfig.addFilter("pick", (obj, lang, field = "") => {
    // pick(obj, lang, "title") → obj.title_es || obj.title  (for config/site.yml style fields)
    if (!obj) return "";
    if (lang !== "en" && obj[`${field}_${lang}`]) return obj[`${field}_${lang}`];
    return obj[field] ?? "";
  });

  /* ---------- dates ---------- */
  eleventyConfig.addFilter("fmtDate", fmtDate);
  eleventyConfig.addFilter("toDate", toDate);
  eleventyConfig.addFilter("isoDate", (v) => { const d = toDate(v); return d ? d.toISOString() : ""; });
  eleventyConfig.addFilter("rfc822", (v) => { const d = toDate(v); return d ? d.toUTCString() : ""; });
  eleventyConfig.addFilter("isRecent", (v, days = 14) => { const d = toDate(v); return !!d && Date.now() - d.getTime() < days * 864e5 && d.getTime() <= Date.now() + 864e5; });
  eleventyConfig.addFilter("sortByDate", (items, dir = "desc") => [...(items || [])].sort((a, b) => (dir === "asc" ? dateVal(a) - dateVal(b) : dateVal(b) - dateVal(a))));
  eleventyConfig.addFilter("upcoming", (items) => (items || []).filter((i) => dateVal(i) >= Date.now() - 6 * 3600e3).sort((a, b) => dateVal(a) - dateVal(b)));
  eleventyConfig.addFilter("past", (items) => (items || []).filter((i) => dateVal(i) < Date.now() - 6 * 3600e3).sort((a, b) => dateVal(b) - dateVal(a)));
  eleventyConfig.addFilter("withinDays", (items, days = 7) => (items || []).filter((i) => { const t = dateVal(i); return t && Date.now() - t <= days * 864e5 && t <= Date.now() + 864e5; }));
  eleventyConfig.addFilter("duration", (sec) => {
    sec = Math.round(Number(sec) || 0);
    if (!sec) return "";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  });
  eleventyConfig.addFilter("year", (v) => { const d = toDate(v); return d ? d.getUTCFullYear() : ""; });

  /* ---------- collections helpers ---------- */
  eleventyConfig.addFilter("where", (items, key, val) => (items || []).filter((i) => {
    const v = key.split(".").reduce((o, k) => (o == null ? o : o[k]), i);
    return Array.isArray(val) ? val.includes(v) : v === val;
  }));
  eleventyConfig.addFilter("whereNot", (items, key, val) => (items || []).filter((i) => {
    const v = key.split(".").reduce((o, k) => (o == null ? o : o[k]), i);
    return Array.isArray(val) ? !val.includes(v) : v !== val;
  }));
  eleventyConfig.addFilter("whereIncludes", (items, key, val) => (items || []).filter((i) => {
    const v = key.split(".").reduce((o, k) => (o == null ? o : o[k]), i);
    return Array.isArray(v) ? v.includes(val) : typeof v === "string" && v.includes(val);
  }));
  eleventyConfig.addFilter("limit", (arr, n) => (arr || []).slice(0, n));
  eleventyConfig.addFilter("offset", (arr, n) => (arr || []).slice(n));
  eleventyConfig.addFilter("groupBy", (items, key) => {
    const m = new Map();
    for (const i of items || []) {
      const v = key.split(".").reduce((o, k) => (o == null ? o : o[k]), i) ?? "other";
      if (!m.has(v)) m.set(v, []);
      m.get(v).push(i);
    }
    return [...m.entries()].map(([k, v]) => ({ key: k, items: v }));
  });
  eleventyConfig.addFilter("countBy", (items, key) => {
    const o = {};
    for (const i of items || []) { const v = key.split(".").reduce((x, k) => (x == null ? x : x[k]), i) ?? "other"; o[v] = (o[v] || 0) + 1; }
    return o;
  });
  eleventyConfig.addFilter("pluck", (items, key) => (items || []).map((i) => key.split(".").reduce((o, k) => (o == null ? o : o[k]), i)));
  eleventyConfig.addFilter("uniq", (arr) => [...new Set((arr || []).filter((x) => x !== undefined && x !== null && x !== ""))]);
  eleventyConfig.addFilter("keys", (o) => Object.keys(o || {}));
  eleventyConfig.addFilter("values", (o) => Object.values(o || {}));
  eleventyConfig.addFilter("length", (a) => (a ? (a.length ?? Object.keys(a).length) : 0));
  eleventyConfig.addFilter("shuffleSeed", (arr, seed = 1) => {
    // deterministic shuffle (build-time) so pages don't churn randomly
    const a = [...(arr || [])]; let s = seed;
    for (let i = a.length - 1; i > 0; i--) { s = (s * 9301 + 49297) % 233280; const j = Math.floor((s / 233280) * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
    return a;
  });

  /* ---------- text ---------- */
  eleventyConfig.addFilter("md", (s) => (s ? md.render(String(s)) : ""));
  eleventyConfig.addFilter("mdInline", (s) => (s ? md.renderInline(String(s)) : ""));
  eleventyConfig.addFilter("stripHtml", (s) => String(s || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim());
  eleventyConfig.addFilter("excerpt", (s, n = 160) => {
    s = String(s || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n).replace(/\s+\S*$/, "") + "…" : s;
  });
  eleventyConfig.addFilter("json", (v) => JSON.stringify(v));
  eleventyConfig.addFilter("fileSize", (b) => {
    b = Number(b) || 0; if (!b) return "";
    const u = ["B", "KB", "MB", "GB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
  });
  eleventyConfig.addFilter("absUrl", (url, base) => {
    try { return new URL(url, (base || "").replace(/\/?$/, "/")).toString(); } catch { return url; }
  });
  eleventyConfig.addFilter("siteUrl", (url, site) => {
    // Absolute URL on the public site (feeds, QR codes). url like "/es/library/"
    const b = String(site?.url || "").replace(/\/$/, "");
    return b + (url.startsWith("/") ? url : "/" + url);
  });
  // Data-driven href/src: {{ item.extra.website | extUrl }} → "" when the value is not a safe link.
  eleventyConfig.addFilter("extUrl", safeUrl);
  eleventyConfig.addFilter("hostname", (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } });
  eleventyConfig.addFilter("driveImg", (fileId, w = 800) => (fileId ? `https://lh3.googleusercontent.com/d/${fileId}=w${w}` : ""));
  eleventyConfig.addFilter("ytThumb", (id, q = "hqdefault") => (id ? `https://i.ytimg.com/vi/${id}/${q}.jpg` : ""));
  eleventyConfig.addFilter("mailto", (email, subject) => `mailto:${email}${subject ? "?subject=" + encodeURIComponent(subject) : ""}`);
  eleventyConfig.addFilter("urlencode", (s) => encodeURIComponent(String(s || "")));

  /* ---------- icons: {% icon "name", "extra classes" %} ---------- */
  eleventyConfig.addShortcode("icon", (name, cls = "size-5", label = "") => {
    let svg = iconCache.get(name);
    if (!svg) {
      // Local icons (brands, custom art) win over Lucide.
      const local = path.join("src/_includes/icons", `${name}.svg`);
      const file = fs.existsSync(local) ? local : path.join(LUCIDE_DIR, `${name}.svg`);
      if (!fs.existsSync(file)) { console.warn(`[icon] missing icon: ${name}`); return ""; }
      svg = fs.readFileSync(file, "utf8").replace(/<!--.*?-->/gs, "").trim();
      iconCache.set(name, svg);
    }
    const a11y = label ? `role="img" aria-label="${label}"` : `aria-hidden="true" focusable="false"`;
    return svg.replace(/<svg([^>]*?)class="[^"]*"/, "<svg$1").replace("<svg", `<svg class="icon ${cls}" ${a11y}`).replace(/\s(width|height)="24"/g, "");
  });

  /* ---------- Tailwind CSS (compiled after each build) ---------- */
  eleventyConfig.on("eleventy.after", ({ directories, dir }) => {
    // `directories.output` honors the CLI --output flag (`dir` is deprecated in Eleventy 3)
    const out = path.join((directories && directories.output) || dir.output, "assets/css/main.css");
    fs.mkdirSync(path.dirname(out), { recursive: true });
    const cli = path.join(path.dirname(require.resolve("@tailwindcss/cli/package.json")), "dist", "index.mjs");
    execFileSync(process.execPath, [cli, "-i", "src/assets/css/main.css", "-o", out, "--minify"], { stdio: "inherit" });
  });

  /* ---------- area-specific filters (auto-loaded) ---------- */
  const extraDir = path.resolve("eleventy/filters");
  if (fs.existsSync(extraDir)) {
    for (const f of fs.readdirSync(extraDir).filter((f) => f.endsWith(".js")).sort()) {
      eleventyConfig.addPlugin(async (cfg) => {
        const mod = await import("./eleventy/filters/" + f);
        await mod.default(cfg, { translateKey, pickLang, fmtDate, toDate });
      });
    }
  }

  return {
    pathPrefix,
    dir: { input: "src", includes: "_includes", data: "_data", output: "_site" },
    templateFormats: ["njk", "md", "html", "11ty.js"],
    htmlTemplateEngine: "njk",
    markdownTemplateEngine: "njk",
  };
}
