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
    drive: cfg.drive || {},
    sources: cfg.sources || {},
    links: cfg.links || {},
    digest: cfg.digest || {},
    // Build timestamp (UTC ISO) — shown as "Last updated" in the footer.
    built: new Date().toISOString(),
    // Drive folder link for the committee upload area
    driveRootUrl: cfg.drive?.root_folder_id ? `https://drive.google.com/drive/folders/${cfg.drive.root_folder_id}` : null,
  };
}
