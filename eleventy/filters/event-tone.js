// Shared: the colour of an event, the same wherever events are listed (/events/, the home page's
// "Upcoming events", the monthly toolkit's dates, …). Auto-loaded by eleventy.config.js.
//
//   {{ ev | eventTone }}            → "committee" | "booth" | "assembly" | "lv" | "gv" | "other"
//   {{ events | eventToneSet }}     → the kinds present, in legend order (for a colour key)
//
// Markup: put class="ev-tone-<kind>" on the card (or on the tile / badge / dot itself); the colours
// are the ev-* rules in src/assets/css/areas/committee.css (tokens only, light / dark / high contrast).
// Colour is never the only signal: the card's badge (or its title) says the kind in words.
//
// Rules, first match wins (titles are compared without case or accents, in every language the item has):
//   1. committee  the committee's own meeting: category / kind "committee", an id "ev:committee:…", or a
//                 title that says "committee meeting" / "reunión del comité"
//   2. booth      a literature booth or table: "booth", "table" (not "round table"), "kiosk", "stall",
//                 "mesa" (not "mesa redonda / de trabajo / directiva"), "puesto"
//   3. assembly   "assembly" / "asamblea" (a pre-assembly too)
//   4. lv / gv    the title names La Viña ("La Viña", "La Vina", "LV") or Grapevine ("Grapevine", "GV");
//                 a title that names both gets the one it names first
//   5. an event from La Viña's or Grapevine's own calendar (category lv-calendar / gv-calendar)
//   6. other      anything else (neutral)
//
// It reads a raw data item (db.events.items), a /events/ event (cmEvents: .committee, .item) and a
// monthly toolkit date row (kind: committee | recurring | event).

/** The kinds, in the order a colour key lists them. */
export const EVENT_TONES = ["committee", "gv", "lv", "booth", "assembly", "other"];

const RE = {
  committee: /\bcommittee meetings?\b|\breunion(?:es)? del comite\b/,
  booth: /\b(?:booths?|kiosks?|stalls?|puestos?)\b|\b(?<!round )tables?\b|\bmesas?\b(?! (?:redonda|de trabajo|directiva))/,
  assembly: /\b(?:assembly|assemblies|asambleas?)\b/,
  lv: /\bla vina\b|\blv\b/,
  gv: /\bgrapevine\b|\bgv\b/,
};

/** "La Viña — Taller" → "la vina — taller" (lower case, accents removed). */
function norm(s) {
  return String(s == null ? "" : s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/** Every title the item has: the display title first, then the original and its translations. */
function titlesOf(x) {
  const out = [];
  const add = (v) => { if (typeof v === "string" && v.trim()) out.push(norm(v)); };
  const addI18n = (o) => { const t = o && o.i18n && o.i18n.title; if (t && typeof t === "object") { add(t.en); add(t.es); } };
  add(x.title);
  if (x.item && typeof x.item === "object") { add(x.item.title); addI18n(x.item); }
  addI18n(x);
  return out;
}

/** The first position of a pattern in a title (Infinity when it is not there). */
function firstAt(re, s) {
  const m = re.exec(s);
  return m ? m.index : Infinity;
}

export function eventTone(ev) {
  if (!ev || typeof ev !== "object") return "other";
  const category = String(ev.category || (ev.item && ev.item.category) || "");
  const id = String(ev.id || "");
  if (ev.committee === true || category === "committee" || ev.kind === "committee" || id.startsWith("ev:committee:")) return "committee";
  const titles = titlesOf(ev);
  if (titles.some((t) => RE.committee.test(t))) return "committee";
  if (titles.some((t) => RE.booth.test(t))) return "booth";
  if (titles.some((t) => RE.assembly.test(t))) return "assembly";
  for (const t of titles) {
    const lv = firstAt(RE.lv, t), gv = firstAt(RE.gv, t);
    if (lv !== Infinity || gv !== Infinity) return lv < gv ? "lv" : "gv";
  }
  if (category === "lv-calendar") return "lv";
  if (category === "gv-calendar") return "gv";
  return "other";
}

/** The kinds a list of events uses, in EVENT_TONES order (a colour key shows only these). */
export function eventToneSet(events) {
  const seen = new Set((Array.isArray(events) ? events : []).map(eventTone));
  return EVENT_TONES.filter((k) => seen.has(k));
}

export default function (eleventyConfig) {
  eleventyConfig.addFilter("eventTone", eventTone);
  eleventyConfig.addFilter("eventToneSet", eventToneSet);
}
