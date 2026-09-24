// /search-index.json and /es/search-index.json — everything on the site for the
// /search/ page (and nothing private: only what the site already shows).
// Entry fields: id, k (kind: page|article|topic|pdf|document|slides|form|episode|
//   video|post|album|announcement|event|meeting), t (title in page language),
//   o (other/original-language title), s (snippet), x (extra keywords), u (URL —
//   internal URLs start with "/" and already carry the /es prefix), d (YYYY-MM-DD),
//   dp ("m" = only the month is known), l (content language),
//   src (gv|lv|neta|podcast|youtube|site), im (image), yt (YouTube id → thumbnail),
//   ic (icon for site pages), m (machine-translated), n (new),
//   pw (subscriber story), z (de-emphasized: PDF no longer linked, past event),
//   a (a story's byline: writer · hometown, e.g. "Victor R. · Grand Prairie, Texas" —
//   searched and shown under the result). The Published Writers page entry is also
//   found by the names, cities and counties of the Texas writers it spotlights.
// Built by the searchIndexJson filter in eleventy/filters/library.js.
export default class SearchIndex {
  data() {
    return {
      pagination: { data: "languages", size: 1, alias: "lang" },
      permalink: (data) => (data.lang === "en" ? "/search-index.json" : `/${data.lang}/search-index.json`),
      eleventyExcludeFromCollections: true,
      layout: null,
    };
  }

  render(data) {
    return this.searchIndexJson(data.db, data.nav, data.lang, data.site);
  }
}
