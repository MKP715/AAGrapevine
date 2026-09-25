// Area filters: access — the Accessibility page (src/pages/accessibility.njk).
//   axPhone(site, weekly, lang)  joining our Zoom meetings by phone (/accessibility/#phone)
//   axAudioTypes(shop, lang)     what the official stores' subscription descriptions say about audio
//   axAslPlaylist(videos, site, lang)  AA Grapevine's American Sign Language playlist on YouTube
// Nothing here copies meeting details: the IDs and passcodes come from `meeting:` (config/site.yml)
// and the weekly open meetings (db.weekly_open via cmWeeklyAll); the dial-in numbers and the
// callers' passcodes from `phone_access:` (config/site.yml).

const digitsOf = (s) => String(s ?? "").replace(/\D+/g, "");
const isDigits = (s) => /^\d+$/.test(String(s ?? "").trim());

/** "+1 346 248 7799" → "+13462487799" (U.S. numbers only; anything else → ""). */
export function telOf(number) {
  const d = digitsOf(number);
  if (/^1\d{10}$/.test(d)) return "+" + d;
  if (/^\d{10}$/.test(d)) return "+1" + d;
  return "";
}

/** Zoom's "one tap mobile" link: dial, then the meeting ID and # after a pause; with a passcode,
 *  ",,,,*<passcode>#" (the form Zoom prints in its invitations). */
export function oneTap(tel, meetingDigits, passcode) {
  if (!tel || !meetingDigits) return "";
  return `tel:${tel},,${meetingDigits}#` + (isDigits(passcode) ? `,,,,*${String(passcode).trim()}#` : "");
}

/** The passcode a caller types: the configured phone passcode, else the meeting passcode when it
 *  is numbers only (Zoom accepts it by phone as is), else "" (unknown: "ask the chair"). */
export function phonePasscode(configured, meetingPasscode) {
  if (isDigits(configured)) return String(configured).trim();
  if (isDigits(meetingPasscode)) return String(meetingPasscode).trim();
  return "";
}

/**
 * → { numbers: [{ display, tel, city }], meetings: [{ key, titles, id, digits, pass, askChair,
 *      details, call, callCity }], ok }
 * meetings: the committee meeting, then one entry per Zoom ROOM of the weekly open meetings (the
 * Grapevine Weekly Open and La Viña's open meeting share one room: one entry, both titles, the page
 * language's meeting first — cmWeeklyAll's order).
 */
export function axPhone(site, weekly, lang = "en", t = (k) => k) {
  const pa = (site && site.phone_access) || {};
  const numbers = (Array.isArray(pa.numbers) ? pa.numbers : [])
    .map((n) => ({ display: String(n?.number || "").trim(), tel: telOf(n?.number), city: String((lang === "es" && n?.city_es) || n?.city || "").trim() }))
    .filter((n) => n.tel);
  const first = numbers[0];
  const meetings = [];
  const m = (site && site.meeting) || {};
  const cDigits = digitsOf(m.meeting_id);
  if (cDigits) {
    const pass = phonePasscode(pa.committee?.phone_passcode, m.passcode);
    meetings.push({
      key: "committee", titles: [t("nav.meeting")], id: String(m.meeting_id), digits: cDigits, pass,
      askChair: !pass && !!String(m.passcode || "").trim(), details: "/meetings/#committee-meeting",
      call: first ? oneTap(first.tel, cDigits, pass) : "", callCity: first ? first.city : "",
    });
  }
  const rooms = new Map();
  for (const w of Array.isArray(weekly) ? weekly : []) {
    if (!w || !w.zoomDigits) continue;
    const k = w.zoomDigits + "|" + (w.passcode || "");
    if (!rooms.has(k)) {
      const pass = phonePasscode(pa.weekly_open?.phone_passcode, w.passcode);
      rooms.set(k, {
        key: "weekly-" + w.zoomDigits, titles: [], id: w.zoomId || w.zoomDigits, digits: w.zoomDigits, pass,
        askChair: false, askHost: !pass && !!w.passcode, details: "/meetings/#weekly-open",
        call: first ? oneTap(first.tel, w.zoomDigits, pass) : "", callCity: first ? first.city : "",
      });
    }
    if (w.title) rooms.get(k).titles.push(w.title);
  }
  meetings.push(...rooms.values());
  return { numbers, meetings, ok: numbers.length > 0 && meetings.length > 0 };
}

const AUDIO_RE = /\baudio|\bp[oó]dcast/i; // "audio", "audiobook(s)", "audiolibro(s)", "podcast", "pódcast"
/**
 * The official stores' descriptions of their subscription types (db.shop.types, from
 * scripts/sync/shop.py) that mention audio, in the page language:
 * → { types: n (how many descriptions there are), audio: [{ pub, type, text }] }.
 * The page says what they say — or that none of them mentions audio — and never more.
 */
export function axAudioTypes(shop, lang = "en") {
  const out = { types: 0, audio: [] };
  const types = (shop && shop.types) || {};
  for (const pub of ["gv", "lv"]) {
    for (const [type, desc] of Object.entries(types[pub] || {})) {
      const text = String((desc && (desc[lang] || desc.en || desc.es)) || "").trim();
      if (!text) continue;
      out.types++;
      if (AUDIO_RE.test(text)) out.audio.push({ pub, type, text });
    }
  }
  return out;
}

/** AA Grapevine's "American Sign Language" playlist (db.videos.playlists) → { url, count, title }
 *  with the verified link in config (links.asl_playlist) as the fallback. */
export function axAslPlaylist(videos, site, lang = "en") {
  const lists = (videos && Array.isArray(videos.playlists)) ? videos.playlists : [];
  const p = lists.find((x) => /american sign language|\bASL\b/i.test(String(x?.title || "")));
  const fallback = (site && site.links && site.links.asl_playlist) || "";
  if (!p) return fallback ? { url: fallback, count: 0, title: "" } : null;
  const title = (p.i18n && p.i18n.title && p.i18n.title[lang]) || p.title || "";
  return { url: p.url || fallback, count: Number(p.count) || 0, title };
}

export default function (eleventyConfig, helpers = {}) {
  const tr = (lang) => (k) => (helpers.translateKey ? helpers.translateKey(k, lang) : k);
  eleventyConfig.addFilter("axPhone", (site, weekly, lang) => axPhone(site, weekly, lang, tr(lang)));
  eleventyConfig.addFilter("axAudioTypes", (shop, lang) => axAudioTypes(shop, lang));
  eleventyConfig.addFilter("axAslPlaylist", (videos, site, lang) => axAslPlaylist(videos, site, lang));
}
