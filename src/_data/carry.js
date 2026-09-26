// Loads config/carry.yml — "Put this issue to work": Grapevine & La Viña as a Twelfth Step tool —
// and exposes it as `carry` in every template:
//   carry.intro / carry.source / carry.story_note   {en, es}
//   carry.guides                                    {en, es}  (a pointer to the GVR / RLV guides)
//   carry.ways        [{id, icon, title: {en, es}, text: {en, es}}]   (display order)
//   carry.wayById     {<id>: way}                                     (lookup for tips)
//   carry.tips        {"YYYY-MM": [{way, text: {en, es}}]}            (Grapevine issue month)
//   carry.issueKeys   ["YYYY-MM", …] sorted ascending
// Theme names are not stored here: pages join carry.tips[key] with the issue's own theme once it is
// out (db.articles issues[]), else the editorial calendar (db.editorial items, extra.issue_key).
// Checks: a tip whose `way` is not a known id is dropped; a missing en/es text is filled from the
// other language. Both are logged, and with I18N_STRICT=1 (CI) they fail the build instead.
import fs from "node:fs";
import * as yaml from "js-yaml";

const FILE = "config/carry.yml";
const EMPTY = { intro: null, source: null, story_note: null, guides: null, ways: [], wayById: {}, tips: {}, issueKeys: [] };

export default function () {
  if (!fs.existsSync(FILE)) return EMPTY;
  const cfg = yaml.load(fs.readFileSync(FILE, "utf8")) || {};
  const problems = [];

  // A {en, es} pair: both languages present, or filled from the other one (and reported).
  const pair = (v, where) => {
    if (!v || typeof v !== "object") { problems.push(`${where}: missing`); return v ? { en: String(v), es: String(v) } : null; }
    for (const [a, b] of [["en", "es"], ["es", "en"]]) {
      if (typeof v[a] !== "string" || !v[a].trim()) { problems.push(`${where}: missing "${a}"`); v[a] = v[b] || ""; }
    }
    return v;
  };

  const ways = (Array.isArray(cfg.ways) ? cfg.ways : []).filter((w) => w && w.id);
  const wayById = {};
  for (const w of ways) {
    w.id = String(w.id);
    if (wayById[w.id]) problems.push(`ways: duplicate id "${w.id}"`);
    wayById[w.id] = w;
    w.title = pair(w.title, `ways ${w.id} title`);
    w.text = pair(w.text, `ways ${w.id} text`);
  }

  const tips = {};
  for (const [key, list] of Object.entries(cfg.tips || {})) {
    if (!/^\d{4}-\d{2}$/.test(key)) { problems.push(`tips: key "${key}" is not "YYYY-MM"`); continue; }
    tips[key] = (Array.isArray(list) ? list : []).filter((tip, i) => {
      if (!tip || !wayById[tip.way]) { problems.push(`tips ${key} #${i + 1}: unknown way "${tip && tip.way}"`); return false; }
      tip.text = pair(tip.text, `tips ${key} #${i + 1} text`);
      return true;
    });
  }

  const intro = pair(cfg.intro, "intro");
  const source = pair(cfg.source, "source");
  const story_note = pair(cfg.story_note, "story_note");
  const guides = cfg.guides ? pair(cfg.guides, "guides") : null;

  if (problems.length) {
    const msg = `[carry] ${FILE}: ${problems.join("; ")}`;
    if (process.env.I18N_STRICT) throw new Error(msg);
    console.warn(msg);
  }

  return {
    intro,
    source,
    story_note,
    guides,
    ways,
    wayById,
    tips,
    issueKeys: Object.keys(tips).sort(),
  };
}
