// /library-index.json and /es/library-index.json — the full Library for
// client-side search & filtering (fetched by /assets/js/library.js).
// Compact on purpose (it can hold 3,000+ documents): short keys, empty fields
// omitted, "found on" pages de-duplicated into `refs`. Field meanings:
//   id, t (title in page language), o (original title if different), s (gv|lv|neta),
//   c (document type), l (document language), d (YYYY-MM-DD), dp ("m" = month precision),
//   ev (event date of a flyer), th (thumbnail), u (open URL), dl (download URL if different),
//   pv (Drive preview URL), sz (bytes), pg (pages), hx (file lives on another site, e.g. aa.org),
//   r (index into refs), n (new), m (title machine-translated), or (no page links to it any more),
//   k (kind if not pdf), ft (file type if not pdf), co (collection codes), x (extra search words)
// Built by the libIndexJson filter in eleventy/filters/library.js.
export default class LibraryIndex {
  data() {
    return {
      pagination: { data: "languages", size: 1, alias: "lang" },
      permalink: (data) => (data.lang === "en" ? "/library-index.json" : `/${data.lang}/library-index.json`),
      eleventyExcludeFromCollections: true,
      layout: null,
    };
  }

  render(data) {
    return this.libIndexJson(data.db, data.lang);
  }
}
