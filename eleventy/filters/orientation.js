// "GVR / RLV 101" (/orientation/ and /orientation/<id>/) — filters for the lessons in
// config/orientation.yml (loaded by src/_data/orientation.js as `orientation`). Auto-loaded by
// eleventy.config.js. Owned by src/pages/orientation.njk (hub, slides, handout),
// src/pages/orientation-lesson.njk and src/_includes/macros/orientation.njk.
//
//   o101Text(pair, lang, vars)   → the {en, es} text in the page language, with {placeholders} filled
//                                  from vars ({rule_lc}, {time}, {panel}, {panel_start}); unknown ones stay
//   o101Month("2027-01", lang)   → "January 2027" / "enero de 2027"
//   o101Lesson(lessons, id)      → one lesson (or null)
//   o101Links(links, lang, site) → [{ href, label, ext }] — site pages in the page language, site.links keys
//                                  resolved, links that can't be used left out; on /es/ La Viña's first
//   o101Deck(lessons)            → the slide plan: [{ kind: "title" | "agenda" | "lesson" | "points" |
//                                  "try" | "check" | "close", lesson, points, part, parts, q, qi }]
//   o101Letter(i)                → "A", "B", "C" … (0-based)
//   o101Vars(orientation, lang, rule, time) → the placeholder values ({rule_lc} …) for o101Text

const LOCALES = { en: "en-US", es: "es-US" };
// Key points per slide: never more than 3 (projector-size type), split as evenly as possible.
const PER_SLIDE = 3;

export function pickText(pair, lang = "en", vars = null) {
  if (!pair) return "";
  let s = typeof pair === "string" ? pair : pair[lang] || pair.en || "";
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined && vars[k] !== null && vars[k] !== "" ? String(vars[k]) : m));
  return s;
}

export function monthYear(key, lang = "en") {
  const m = /^(\d{4})-(\d{2})$/.exec(String(key || ""));
  if (!m) return "";
  return new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 15)));
}

export function deckPlan(lessons) {
  const out = [{ kind: "title" }, { kind: "agenda" }];
  for (const l of lessons || []) {
    out.push({ kind: "lesson", lesson: l });
    const pts = l.points || [];
    const parts = Math.max(1, Math.ceil(pts.length / PER_SLIDE));
    const size = Math.ceil(pts.length / parts);
    for (let p = 0; p < parts; p++) {
      out.push({ kind: "points", lesson: l, points: pts.slice(p * size, (p + 1) * size), start: p * size, part: p + 1, parts });
    }
    out.push({ kind: "try", lesson: l });
    (l.check || []).forEach((q, qi) => out.push({ kind: "check", lesson: l, q, qi }));
  }
  out.push({ kind: "close" });
  return out;
}

export default function (eleventyConfig) {
  eleventyConfig.addFilter("o101Text", (pair, lang, vars) => pickText(pair, lang, vars));
  eleventyConfig.addFilter("o101Month", (key, lang) => monthYear(key, lang));
  eleventyConfig.addFilter("o101Lesson", (lessons, id) => (lessons || []).find((l) => l.id === id) || null);
  eleventyConfig.addFilter("o101Letter", (i) => String.fromCharCode(65 + (Number(i) || 0)));
  // The placeholder values for o101Text: {{ orientation | o101Vars(lang, site.meeting | cmRule(lang), site.meeting | cmTimeRange(lang)) }}
  eleventyConfig.addFilter("o101Vars", (o, lang, rule = "", time = "") => ({
    rule,
    rule_lc: rule ? rule.charAt(0).toLocaleLowerCase(lang === "es" ? "es" : "en") + rule.slice(1) : "",
    time,
    panel: (o && o.panel && o.panel.number) || "",
    panel_start: monthYear(o && o.panel && o.panel.starts, lang),
  }));
  eleventyConfig.addFilter("o101Deck", (lessons) => deckPlan(lessons));
  eleventyConfig.addFilter("o101Links", (links, lang, site) => {
    const L = (site && site.links) || {};
    const out = [];
    for (const k of links || []) {
      let href = "";
      if (k.href) href = lang === "es" ? "/es" + k.href : k.href;
      else if (k.link) href = L[(lang === "es" && k.link_es) || k.link] || "";
      else if (k.url) href = (lang === "es" && k.url_es) || k.url;
      if (!href) continue;
      out.push({ href, label: pickText(k.label, lang), ext: /^https?:/.test(href), pub: k.pub || "" });
    }
    if (lang === "es") {
      // La Viña's link before Grapevine's (the rest keep their place): stable sort by publication
      const rank = (x) => (x.pub === "lv" ? 0 : x.pub === "gv" ? 1 : -1);
      const pubs = out.filter((x) => x.pub).sort((a, b) => rank(a) - rank(b));
      let i = 0;
      return out.map((x) => (x.pub ? pubs[i++] : x));
    }
    return out;
  });
}
