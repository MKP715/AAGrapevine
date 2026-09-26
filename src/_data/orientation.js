// Loads config/orientation.yml — "GVR / RLV 101", the orientation for new Grapevine and La Viña
// representatives — and exposes it as `orientation` in every template:
//   orientation.panel        { number: 77, starts: "2027-01" }
//   orientation.lessons      [{ id, n, icon, minutes, example, title, summary, goal, points: [{title, text}],
//                               try, discuss, links: [{href|link|url, url_es, link_es, pub, label}],
//                               check: [{q, options: [{en, es}], answer (1-based), why}],
//                               prev, next (ids or "") }]   every text is {en, es}
//   orientation.pages        [{ lang, id }]  → pagination for the lesson pages (orientation-lesson.njk)
//   orientation.totalMinutes / totalChecks
// Pages: src/pages/orientation.njk (hub, slides, handout) and src/pages/orientation-lesson.njk.
// Filters (placeholders, links, the slide plan): eleventy/filters/orientation.js.
// Checks: a missing en/es text, a lesson without 3–6 points, a question without 3 options or with an
// `answer` outside them, a duplicate or unsafe id — logged, and with I18N_STRICT=1 (CI) they fail the
// build instead (the same rule as config/carry.yml).
import fs from "node:fs";
import * as yaml from "js-yaml";

const FILE = "config/orientation.yml";
const EXAMPLES = new Set(["issue", "meeting", "tip", "botm", "deadline", "poster"]);
const EMPTY = { panel: { number: null, starts: "" }, lessons: [], pages: [], totalMinutes: 0, totalChecks: 0 };

/** Parse and check the file's content. */
function loadOrientation(text) {
  const cfg = yaml.load(text) || {};
  const problems = [];

  // A {en, es} pair: both languages present, or filled from the other one (and reported).
  const pair = (v, where) => {
    if (!v || typeof v !== "object") { problems.push(`${where}: missing`); return { en: String(v || ""), es: String(v || "") }; }
    for (const [a, b] of [["en", "es"], ["es", "en"]]) {
      if (typeof v[a] !== "string" || !v[a].trim()) { problems.push(`${where}: missing "${a}"`); v[a] = v[b] || ""; }
    }
    return { en: v.en.trim(), es: v.es.trim() };
  };

  const seen = new Set();
  const lessons = (Array.isArray(cfg.lessons) ? cfg.lessons : []).filter(Boolean).map((l, i) => {
    const where = `lesson ${i + 1}`;
    const id = String(l.id || "");
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) problems.push(`${where}: id "${id}" must be lowercase letters, digits and dashes`);
    if (seen.has(id)) problems.push(`${where}: duplicate id "${id}"`);
    seen.add(id);
    const minutes = Number(l.minutes);
    if (!Number.isInteger(minutes) || minutes < 3 || minutes > 12) problems.push(`${where}: minutes must be a whole number from 3 to 12`);
    if (!EXAMPLES.has(l.example)) problems.push(`${where}: example "${l.example}" is not one of ${[...EXAMPLES].join(", ")}`);
    const points = (Array.isArray(l.points) ? l.points : []).map((p, j) => ({
      title: pair(p && p.title, `${where} point ${j + 1} title`),
      text: pair(p && p.text, `${where} point ${j + 1} text`),
    }));
    if (points.length < 3 || points.length > 6) problems.push(`${where}: needs 3 to 6 points (has ${points.length})`);
    const check = (Array.isArray(l.check) ? l.check : []).map((c, j) => {
      const w = `${where} question ${j + 1}`;
      const options = (Array.isArray(c && c.options) ? c.options : []).map((o, k) => pair(o, `${w} option ${k + 1}`));
      const answer = Number(c && c.answer);
      if (options.length !== 3) problems.push(`${w}: needs 3 options (has ${options.length})`);
      if (!Number.isInteger(answer) || answer < 1 || answer > options.length) problems.push(`${w}: answer must be 1–${options.length}`);
      return { q: pair(c && c.q, `${w} text`), options, answer, why: pair(c && c.why, `${w} why`) };
    });
    if (check.length !== 3) problems.push(`${where}: needs 3 questions (has ${check.length})`);
    const links = (Array.isArray(l.links) ? l.links : []).filter(Boolean).map((k, j) => {
      const w = `${where} link ${j + 1}`;
      if (!k.href && !k.link && !k.url) problems.push(`${w}: needs href, link or url`);
      if (k.href && !/^\/(?!\/)/.test(k.href)) problems.push(`${w}: href must be a path on this site, like /monthly/#ways`);
      if (k.url && !/^https:\/\//.test(k.url)) problems.push(`${w}: url must start with https://`);
      return { href: k.href || "", link: k.link || "", link_es: k.link_es || "", url: k.url || "", url_es: k.url_es || "", pub: k.pub || "", label: pair(k.label, `${w} label`) };
    });
    return {
      id, n: i + 1, icon: String(l.icon || "graduation-cap"), minutes, example: l.example,
      title: pair(l.title, `${where} title`),
      summary: pair(l.summary, `${where} summary`),
      goal: pair(l.goal, `${where} goal`),
      points,
      try: pair(l.try, `${where} try`),
      discuss: pair(l.discuss, `${where} discuss`),
      links,
      check,
    };
  });
  lessons.forEach((l, i) => {
    l.prev = i > 0 ? lessons[i - 1].id : "";
    l.next = i < lessons.length - 1 ? lessons[i + 1].id : "";
  });

  const p = cfg.panel || {};
  const panel = { number: Number(p.number) || null, starts: String(p.starts || "") };
  if (!panel.number) problems.push("panel: number missing");
  if (!/^\d{4}-\d{2}$/.test(panel.starts)) problems.push('panel: starts must be "YYYY-MM"');

  return {
    problems,
    data: {
      panel,
      lessons,
      pages: ["en", "es"].flatMap((lang) => lessons.map((l) => ({ lang, id: l.id }))),
      totalMinutes: lessons.reduce((s, l) => s + (l.minutes || 0), 0),
      totalChecks: lessons.reduce((s, l) => s + l.check.length, 0),
    },
  };
}

export default function () {
  if (!fs.existsSync(FILE)) return EMPTY;
  const { problems, data } = loadOrientation(fs.readFileSync(FILE, "utf8"));
  if (problems.length) {
    const msg = `[orientation] ${FILE}: ${problems.join("; ")}`;
    if (process.env.I18N_STRICT) throw new Error(msg);
    console.warn(msg);
  }
  return data;
}
