// Loads config/history.yml — the history of Grapevine & La Viña (the timeline on /about/#history) —
// and exposes it as `history` in every template:
//   history.items     [{ n, year, iso, label: {en, es}, decade, type, tone, title: {en, es}, desc: {en, es},
//                        rank: {all, gv, lv}, side: {all, gv, lv} }]   oldest first
//   history.decades   [{ decade: 1940, items: […], count: {all, gv, lv}, first: {all, gv, lv} }]
//   history.count     { all, gv, lv, both }   gv / lv count the "both" milestones too (the page's filters)
//   history.first / history.last   the first and last year · history.official {grapevine, lavina} (urls)
// type is grapevine | lavina | both; tone is its colour on the page: gv | lv | vine.
// rank.<filter> is the milestone's place in that filter (1-based; 0 = not in it) — the page's "Show all"
// collapses by it; first.<filter> is the smallest rank of a decade (0 = none in that filter).
// side.<filter> is "l" or "r": where the milestone sits in the two-sided desktop layout when that
// filter is on (left and right take turns inside each decade, so a filtered decade still alternates).
// history.years is how long ago the first milestone was, in whole decades (82 → 80: "more than 80 years").
// Class names for the page's CSS states (src/assets/css/areas/read.css, "History"; the collapsed list
// shows the first LIMITS[0] milestones of the filter when narrow, LIMITS[1] when wide):
//   item.cls      hs-<f> (on the right, filter f) · hx7-<f> / hx12-<f> (past the first 7 / 12 of f)
//   decade.cls    hn-gv / hn-lv (nothing in that filter) · dx7-<f> / dx12-<f> (nothing in the first 7 / 12)
//   history.moreCls  bn7-<f> / bn12-<f> ("Show all" has nothing more to show for f)
// Labels: "June 1944" → { en: "June 1944", es: "Junio de 1944" }, iso "1944-06"; "Summer 1996" →
// "Verano de 1996", iso "1996"; "1948" stays "1948".
// Checks: a missing en/es text, an unknown type, a year that can't be read, a milestone out of order —
// logged, and with I18N_STRICT=1 (CI) they fail the build instead (the same rule as config/carry.yml).
import fs from "node:fs";
import * as yaml from "js-yaml";

const FILE = "config/history.yml";
const EMPTY = { items: [], decades: [], count: { all: 0, gv: 0, lv: 0, both: 0 }, moreCls: "", first: null, last: null, years: 0, official: {} };
const LIMITS = [7, 12];
const TYPES = { grapevine: "gv", lavina: "lv", both: "vine" };
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const SEASONS = { spring: "primavera", summer: "verano", fall: "otoño", autumn: "otoño", winter: "invierno" };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

/** "June 1944" → { year: 1944, month: 6, en, es, iso }; null when it can't be read. */
function parseYear(text) {
  const m = /^(?:([A-Za-z]+)\s+)?(\d{4})$/.exec(String(text || "").trim());
  if (!m) return null;
  const year = Number(m[2]);
  if (!m[1]) return { year, month: 0, en: String(year), es: String(year), iso: String(year) };
  const word = m[1].toLowerCase();
  const mi = MONTHS.indexOf(word);
  if (mi >= 0) return { year, month: mi + 1, en: `${cap(word)} ${year}`, es: `${cap(MESES[mi])} de ${year}`, iso: `${year}-${String(mi + 1).padStart(2, "0")}` };
  if (SEASONS[word]) return { year, month: 0, en: `${cap(word)} ${year}`, es: `${cap(SEASONS[word])} de ${year}`, iso: String(year) };
  return null;
}

/** Parse and check the file's content. */
function loadHistory(text) {
  const cfg = yaml.load(text) || {};
  const problems = [];
  const pair = (v, where) => {
    if (!v || typeof v !== "object") { problems.push(`${where}: missing`); return { en: String(v || ""), es: String(v || "") }; }
    for (const [a, b] of [["en", "es"], ["es", "en"]]) {
      if (typeof v[a] !== "string" || !v[a].trim()) { problems.push(`${where}: missing "${a}"`); v[a] = v[b] || ""; }
    }
    return { en: v.en.trim(), es: v.es.trim() };
  };

  const count = { all: 0, gv: 0, lv: 0, both: 0 };
  let prev = 0;
  const items = [];
  (Array.isArray(cfg.milestones) ? cfg.milestones : []).filter(Boolean).forEach((m, i) => {
    const where = `milestone ${i + 1} (${m.year})`;
    const when = parseYear(m.year);
    if (!when) { problems.push(`${where}: year must look like "June 1944", "Summer 1996" or "1948"`); return; }
    if (!TYPES[m.type]) { problems.push(`${where}: type "${m.type}" must be grapevine, lavina or both`); return; }
    if (when.year < prev) problems.push(`${where}: out of order (oldest first)`);
    prev = Math.max(prev, when.year);
    const inGv = m.type !== "lavina", inLv = m.type !== "grapevine";
    count.all += 1;
    if (inGv) count.gv += 1;
    if (inLv) count.lv += 1;
    if (m.type === "both") count.both += 1;
    items.push({
      n: items.length + 1,
      year: String(m.year).trim(), iso: when.iso, label: { en: when.en, es: when.es },
      decade: Math.floor(when.year / 10) * 10,
      type: m.type, tone: TYPES[m.type],
      title: pair(m.title, `${where} title`),
      desc: pair(m.desc, `${where} desc`),
      rank: { all: count.all, gv: inGv ? count.gv : 0, lv: inLv ? count.lv : 0 },
      side: {},
    });
  });

  // Decades, with each filter's count, first rank and left/right turns
  const decades = [];
  for (const it of items) {
    let d = decades[decades.length - 1];
    if (!d || d.decade !== it.decade) {
      d = { decade: it.decade, items: [], count: { all: 0, gv: 0, lv: 0 }, first: { all: 0, gv: 0, lv: 0 } };
      decades.push(d);
    }
    d.items.push(it);
    for (const f of ["all", "gv", "lv"]) {
      if (!it.rank[f]) continue;
      d.count[f] += 1;
      if (!d.first[f]) d.first[f] = it.rank[f];
      it.side[f] = d.count[f] % 2 ? "l" : "r";
    }
  }

  const official = {};
  for (const k of ["grapevine", "lavina"]) {
    const u = cfg.official && cfg.official[k];
    if (u && !/^https:\/\//.test(u)) problems.push(`official.${k}: must start with https://`);
    else if (u) official[k] = u;
  }

  // The CSS state classes (see the header)
  const F = ["all", "gv", "lv"];
  for (const it of items) {
    it.cls = F.flatMap((f) => [
      it.side[f] === "r" ? `hs-${f}` : "",
      ...LIMITS.map((n) => (it.rank[f] > n ? `hx${n}-${f}` : "")),
    ]).filter(Boolean).join(" ");
  }
  for (const d of decades) {
    d.cls = F.flatMap((f) => [
      !d.count[f] ? `hn-${f}` : "",
      ...LIMITS.map((n) => (d.first[f] > n ? `dx${n}-${f}` : "")),
    ]).filter(Boolean).join(" ");
  }
  const moreCls = F.flatMap((f) => LIMITS.map((n) => (count[f] <= n ? `bn${n}-${f}` : ""))).filter(Boolean).join(" ");

  const yearOf = (it) => (it ? Number(it.iso.slice(0, 4)) : null);
  const first = yearOf(items[0]);
  return {
    problems,
    data: {
      items, decades, count, moreCls, official,
      first, last: yearOf(items[items.length - 1]),
      years: first ? Math.floor((new Date().getFullYear() - first) / 10) * 10 : 0,
    },
  };
}

export default function () {
  if (!fs.existsSync(FILE)) return EMPTY;
  const { problems, data } = loadHistory(fs.readFileSync(FILE, "utf8"));
  if (problems.length) {
    const msg = `[history] ${FILE}: ${problems.join("; ")}`;
    if (process.env.I18N_STRICT) throw new Error(msg);
    console.warn(msg);
  }
  return data;
}
