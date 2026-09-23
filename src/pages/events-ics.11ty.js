// Calendar feeds: /events.ics (English) and /es/events.ics (Spanish).
//
// Valid iCalendar (RFC 5545): CRLF line endings, lines folded at 75 octets,
// TEXT escaping, stable UIDs, DTSTAMP, UTC times for timed events and
// DATE values for all-day events (so no VTIMEZONE is needed).
//
// Contents: the monthly committee meetings (computed from config/site.yml,
// with the Zoom link), Drive flyer events, manual events, the Texas GV/LV
// calendar events and any extra .ics feeds — everything on /events/.
// Past events are kept for ~90 days so subscribers keep a little history.
import { normalizeEvents, buildIcs } from "../../eleventy/filters/committee.js";

export const data = {
  pagination: { data: "languages", size: 1, alias: "lang" },
  permalink: (data) => (data.lang === "en" ? "/events.ics" : `/${data.lang}/events.ics`),
  eleventyExcludeFromCollections: true,
  layout: null,
};

const KEEP_PAST_DAYS = 90;

export function render(data) {
  const lang = data.lang || "en";
  const t = (k, vars) => (typeof this.t === "function" ? this.t(k, lang, vars) : k);
  const site = data.site || {};
  const items = process.env.COMMITTEE_EMPTY ? [] : data.db?.events?.items || [];
  const now = new Date();
  const cutoff = now.getTime() - KEEP_PAST_DAYS * 864e5;
  const events = normalizeEvents(items, site, lang, { monthsBack: 3, monthsAhead: 12, now }).filter((e) => e.endMs >= cutoff);
  const base = String(site.url || "").replace(/\/$/, "");
  return buildIcs(events, {
    lang,
    now,
    name: t("committee.ics.name"),
    description: t("committee.ics.desc"),
    url: base + (lang === "en" ? "/events/" : `/${lang}/events/`),
    categoryLabel: (ev) => t(`committee.events.group.${ev.group}`),
  });
}
