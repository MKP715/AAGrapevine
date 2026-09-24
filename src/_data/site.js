// Loads config/site.yml and exposes it as `site` in every template.
import fs from "node:fs";
import * as yaml from "js-yaml";

export default function () {
  const cfg = yaml.load(fs.readFileSync("config/site.yml", "utf8"));
  const s = cfg.site || {};
  return {
    ...s,
    // The GitHub Action exports SITE_URL from actions/configure-pages (custom domains, renames)
    url: String(process.env.SITE_URL || s.url || "").replace(/\/+$/, ""),
    meeting: cfg.meeting || {},
    // The monthly series (CityWide booth …): /monthly/ works out months past events.json's
    // `months_ahead` dates from these rules (eleventy/filters/monthly.js).
    recurring_events: Array.isArray(cfg.recurring_events) ? cfg.recurring_events : [],
    drive: cfg.drive || {},
    sources: cfg.sources || {},
    links: cfg.links || {},
    digest: cfg.digest || {},
    // Build timestamp (UTC ISO) — shown as "Last updated" in the footer.
    built: new Date().toISOString(),
    // No link to the Drive ROOT on purpose: it can hold private files (e.g. sign-up response sheets).
    // Pages link the current Panel folder instead (eleventy/filters/committee.js → driveInfo).
  };
}
