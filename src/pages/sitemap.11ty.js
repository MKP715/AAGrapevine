// /sitemap.xml — every HTML page in both languages, each with hreflang
// alternates (en / es / x-default) so search engines pair the translations.
// Built from collections.all, so new pages are picked up automatically.
// A page opts out with `sitemap: false` in its front matter.
export const data = {
  permalink: "/sitemap.xml",
  eleventyExcludeFromCollections: true,
  layout: false,
};

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function render(data) {
  const base = String(data.site?.url || "").replace(/\/$/, "");
  const abs = (u) => base + u;
  const lastmod = String(data.site?.built || new Date().toISOString()).slice(0, 10);

  // Collect HTML page URLs (skip feeds, JSON, .ics, 404 and opted-out pages).
  // Eleventy only puts the FIRST page of a paginated template in collections
  // (our pages paginate over languages), so also add every pagination href.
  const urls = new Set();
  const isPage = (u) => typeof u === "string" && (u.endsWith("/") || u.endsWith(".html")) && !/\/404(\.html)?$/.test(u);
  for (const p of data.collections?.all || []) {
    if (p.data?.sitemap === false) continue;
    if (isPage(p.url)) urls.add(p.url);
    for (const h of p.data?.pagination?.hrefs || []) if (isPage(h)) urls.add(h);
  }

  // "/es/x/" ⇄ "/x/"
  const enOf = (u) => (u.startsWith("/es/") ? u.slice(3) : u === "/es" ? "/" : u);
  const esOf = (u) => "/es" + enOf(u);

  const isEs = (u) => (u.startsWith("/es/") ? 1 : 0);
  const sorted = [...urls].sort((a, b) => enOf(a).localeCompare(enOf(b)) || isEs(a) - isEs(b));
  const out = ['<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'];
  for (const u of sorted) {
    const en = enOf(u), es = esOf(u);
    const hasEn = urls.has(en), hasEs = urls.has(es);
    const depth = en.split("/").filter(Boolean).length;
    out.push("  <url>");
    out.push(`    <loc>${esc(abs(u))}</loc>`);
    out.push(`    <lastmod>${lastmod}</lastmod>`);
    out.push("    <changefreq>daily</changefreq>");
    out.push(`    <priority>${en === "/" ? "1.0" : depth <= 1 ? "0.8" : "0.5"}</priority>`);
    if (hasEn && hasEs) {
      out.push(`    <xhtml:link rel="alternate" hreflang="en" href="${esc(abs(en))}"/>`);
      out.push(`    <xhtml:link rel="alternate" hreflang="es" href="${esc(abs(es))}"/>`);
      out.push(`    <xhtml:link rel="alternate" hreflang="x-default" href="${esc(abs(en))}"/>`);
    }
    out.push("  </url>");
  }
  out.push("</urlset>");
  return out.join("\n") + "\n";
}
