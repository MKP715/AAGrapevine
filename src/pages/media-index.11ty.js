// Compact JSON indexes for the media pages. The pages server-render only the
// newest ~24 items; assets/js/media.js fetches these files for "Load more",
// search and filters.
//
//   /episodes-index.json     /es/episodes-index.json   (podcast episodes)
//   /videos-index.json       /es/videos-index.json     (YouTube videos)
//
// Titles/summaries are already in the page language (machine translations
// come from data/site/*.json). The JSON body is built by the `mediaIndexJson`
// filter in eleventy/filters/media.js so the format lives in one place.
export default class MediaIndex {
  data() {
    return {
      mediaIndexPages: [
        { kind: "episodes", lang: "en" },
        { kind: "episodes", lang: "es" },
        { kind: "videos", lang: "en" },
        { kind: "videos", lang: "es" },
      ],
      pagination: { data: "mediaIndexPages", size: 1, alias: "ix" },
      permalink: (data) => `${data.ix.lang === "en" ? "" : "/" + data.ix.lang}/${data.ix.kind}-index.json`,
      eleventyExcludeFromCollections: true,
      layout: false,
    };
  }

  render(data) {
    const shows = (data.site && data.site.sources && data.site.sources.podcasts) || [];
    return this.mediaIndexJson(data.db, data.ix.kind, data.ix.lang, shows);
  }
}
