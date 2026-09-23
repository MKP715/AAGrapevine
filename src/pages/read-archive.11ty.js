// /read-archive.json and /es/read-archive.json — older magazine issues for the
// Read page. The page server-renders the newest issues; everything older is
// fetched from this file only when a visitor taps "Load older issues", so the
// page stays light even after years of daily syncing.
// Short keys keep the file small:
//   issue: id, pub, key, label, topic, tl=topic lang, c=cover (site-relative), u=issue url, h=its host, count, items
//   story: t=title, tl=title lang, o=original title, ol=original lang, u=url, s=section,
//          a=author, f=1 free / 0 subscribers, m=machine-translated, n=new
const SSR_ARCHIVE = 8; // keep in sync with SSR_ARCHIVE in read.njk
const HUBS = { gv: "https://www.aagrapevine.org/magazine", lv: "https://www.aalavina.org/la-revista" };

const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };

export default class {
  data() {
    return {
      pagination: { data: "languages", size: 1, alias: "lang" },
      permalink: (data) => (data.lang === "en" ? "/read-archive.json" : `/${data.lang}/read-archive.json`),
      eleventyExcludeFromCollections: true,
    };
  }

  render(data) {
    const lang = data.lang;
    const src = data.site && data.site.sources ? data.site.sources : {};
    const hubs = {
      gv: src.grapevine ? src.grapevine.base + src.grapevine.magazine_hub : HUBS.gv,
      lv: src.lavina ? src.lavina.base + src.lavina.magazine_hub : HUBS.lv,
    };
    const older = (this.readArchive(data.db.articles) || []).slice(SSR_ARCHIVE);
    const issues = older.map((is) => {
      const v = this.readIssueView(is, lang);
      const u = v.url || hubs[v.pub] || hubs.gv;
      const out = {
        id: v.id,
        pub: v.pub,
        key: v.key,
        label: v.label || this.t("read.undated", lang),
        topic: v.topic || "",
        tl: v.topicLang || "",
        u,
        h: host(u),
        count: v.count,
        items: v.articles.map((a) => {
          const o = { t: a.title, tl: a.titleLang, u: a.url };
          if (a.original) { o.o = a.original; o.ol = a.origLang; }
          if (a.section) o.s = a.section;
          if (a.author) o.a = a.author;
          if (a.free === true) o.f = 1;
          else if (a.free === false) o.f = 0;
          if (a.machine) o.m = 1;
          if (a.isNew) o.n = 1;
          return o;
        }),
      };
      if (v.cover) out.c = v.cover;
      return out;
    });
    return JSON.stringify({ lang, generated: data.site.built, count: issues.length, issues });
  }
}
