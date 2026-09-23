// RSS 2.0 feeds of everything new (db.whatsnew): /feed.xml (English) and
// /es/feed.xml (Spanish). Titles/summaries are in the feed's language
// (machine-translated where needed, and marked as such). Podcast episodes
// carry their audio as an <enclosure>, so podcast apps can use the feed too.
import { prepareWhatsNew, hrefOf, escapeXml as x, issueLabelOf } from "../../eleventy/filters/community.js";

const MAX_ITEMS = 100;
const LANG_TAG = { en: "en-us", es: "es-us" };
const AUDIO_TYPES = { mp3: "audio/mpeg", m4a: "audio/mp4", aac: "audio/aac", ogg: "audio/ogg", opus: "audio/ogg", wav: "audio/wav" };

export const data = {
  pagination: { data: "languages", size: 1, alias: "lang" },
  permalink: (data) => (data.lang === "en" ? "/feed.xml" : `/${data.lang}/feed.xml`),
  eleventyExcludeFromCollections: true,
  layout: false,
};

function abs(site, url) {
  if (!url) return "";
  if (/^https?:/.test(url)) return url;
  return String(site.url || "").replace(/\/$/, "") + (url.startsWith("/") ? url : "/" + url);
}
function rfc822(v) {
  const d = v ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(v) ? v + "T12:00:00Z" : v) : null;
  return d && !isNaN(d) ? d.toUTCString() : "";
}
function pick(item, field, lang) {
  const i = item?.i18n?.[field];
  if (i && (i[lang] || i[lang] === "")) return i[lang] || i[item.lang] || item[field] || "";
  return item?.[field] ?? "";
}
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function render(data) {
  const { site, db, lang } = data;
  const t = (key, vars) => this.t(key, lang, vars);
  const home = abs(site, lang === "en" ? "/" : `/${lang}/`);
  const self = abs(site, lang === "en" ? "/feed.xml" : `/${lang}/feed.xml`);
  const items = prepareWhatsNew(db?.whatsnew?.items || []).slice(0, MAX_ITEMS);
  const newest = items.find((i) => i._when)?._when || site.built;

  const out = [];
  out.push('<?xml version="1.0" encoding="UTF-8"?>');
  out.push('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">');
  out.push("<channel>");
  out.push(`<title>${x(t("feeds.rss_title"))}</title>`);
  out.push(`<link>${x(home)}</link>`);
  out.push(`<atom:link href="${x(self)}" rel="self" type="application/rss+xml"/>`);
  out.push(`<atom:link href="${x(abs(site, lang === "en" ? "/es/feed.xml" : "/feed.xml"))}" rel="alternate" type="application/rss+xml" hreflang="${lang === "en" ? "es" : "en"}"/>`);
  out.push(`<description>${x(t("site.description"))}</description>`);
  out.push(`<language>${LANG_TAG[lang] || lang}</language>`);
  out.push(`<lastBuildDate>${rfc822(site.built)}</lastBuildDate>`);
  out.push(`<pubDate>${rfc822(newest)}</pubDate>`);
  out.push("<ttl>720</ttl>");
  out.push("<generator>Eleventy — NETA 65 Grapevine / La Viña</generator>");
  if (site.contact_email) out.push(`<managingEditor>${x(site.contact_email)} (${x(t("site.tagline"))})</managingEditor>`);
  out.push("<image>");
  out.push(`<url>${x(abs(site, "/assets/img/logo-192x192.png"))}</url>`);
  out.push(`<title>${x(t("feeds.rss_title"))}</title>`);
  out.push(`<link>${x(home)}</link>`);
  out.push("</image>");

  for (const it of items) {
    const title = String(pick(it, "title", lang) || it.title || "").replace(/\s+/g, " ").trim();
    if (!title) continue;
    const link = abs(site, hrefOf(it, lang)) || home;
    const summary = String(pick(it, "summary", lang) || "").trim();
    const groupLabel = t(`community.group.${it._group}`);
    const source = sourceName(it);

    // Description: HTML (escaped) — summary, a context line, auto-translation note.
    const bits = [];
    if (summary) bits.push(`<p>${esc(summary)}</p>`);
    const ctx = [source, groupLabel];
    if (it.kind === "article" && it.extra?.issue_label) ctx.push(issueLabelOf(it, lang));
    if (it.kind === "article" && it.extra?.free === false) ctx.push(t("community.wn.subscriber"));
    if (it.kind === "episode" && it.extra?.show_name) ctx.push(it.extra.show_name);
    bits.push(`<p><em>${esc(ctx.filter(Boolean).join(" · "))}</em></p>`);
    if (Array.isArray(it.machine) && it.machine.includes(lang)) bits.push(`<p><small>${esc(t("common.auto_translated"))}</small></p>`);
    // Site-relative images (cached Instagram / PDF / article thumbnails) become absolute.
    const img = it.image || it.extra?.thumb || "";
    if (img && (/^https?:/.test(img) || img.startsWith("/"))) bits.push(`<p><img src="${esc(abs(site, img))}" alt="" width="480"/></p>`);

    out.push("<item>");
    out.push(`<title>${x(title)}</title>`);
    out.push(`<link>${x(link)}</link>`);
    out.push(`<guid isPermaLink="false">${x(it.id || link)}</guid>`);
    const pub = rfc822(it._when);
    if (pub) out.push(`<pubDate>${pub}</pubDate>`);
    out.push(`<description>${x(bits.join(""))}</description>`);
    out.push(`<category>${x(groupLabel)}</category>`);
    if (source && source !== groupLabel) out.push(`<category>${x(source)}</category>`);
    if (it.extra?.author && it.extra.author !== "Anonymous") out.push(`<dc:creator>${x(it.extra.author)}</dc:creator>`);
    const audio = it.kind === "episode" ? it.extra?.audio_url : null;
    if (audio && /^https?:/.test(audio)) {
      const ext = (audio.split("?")[0].match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase();
      // RSS 2.0 requires a length; the podcast feed gives it (audio_bytes). 0 is the accepted "unknown".
      const type = /^audio\//.test(it.extra?.audio_type || "") ? it.extra.audio_type : AUDIO_TYPES[ext] || "audio/mpeg";
      out.push(`<enclosure url="${x(audio)}" length="${Number(it.extra?.audio_bytes) || 0}" type="${x(type)}"/>`);
    }
    out.push("</item>");
  }
  out.push("</channel>");
  out.push("</rss>");
  return out.join("\n") + "\n";
}

function sourceName(it) {
  const host = it.extra?.host || "";
  if (it.source === "lavina" || it.category === "lv" || host.includes("lavina")) return "La Viña";
  if (it.source === "grapevine" || it.source === "crawl") return "AA Grapevine";
  if (it.source === "podcast") return it.extra?.show_name || "Podcast";
  if (it.source === "youtube") return "YouTube";
  if (it.source === "instagram") return "Instagram";
  if (it.source === "drive" || it.source === "committee") return "NETA 65";
  return "";
}
