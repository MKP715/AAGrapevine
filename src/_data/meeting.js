// Computes upcoming committee meeting dates from config/site.yml `meeting`
// (e.g. 3rd Wednesday, 19:00–20:00 Central). Client JS recomputes live.
import fs from "node:fs";
import * as yaml from "js-yaml";

const WD = { sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6 };

// Offset (minutes) of America/Chicago from UTC for a given UTC date.
function chicagoOffsetMinutes(date) {
  const f = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", timeZoneName: "shortOffset" });
  const tz = f.formatToParts(date).find((p) => p.type === "timeZoneName")?.value || "GMT-6";
  const m = tz.match(/GMT([+-]\d+)(?::(\d+))?/);
  return m ? Number(m[1]) * 60 + Math.sign(Number(m[1])) * Number(m[2] || 0) : -360;
}

function nthWeekday(year, month, weekday, n) {
  if (n === -1) {
    const last = new Date(Date.UTC(year, month + 1, 0));
    const diff = (last.getUTCDay() - weekday + 7) % 7;
    return last.getUTCDate() - diff;
  }
  const first = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const day = 1 + ((weekday - first + 7) % 7) + (n - 1) * 7;
  const dim = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return day <= dim ? day : null;
}

function atChicago(y, mo, d, hhmm) {
  const [h, mi] = String(hhmm || "19:00").split(":").map(Number);
  const guess = new Date(Date.UTC(y, mo, d, h, mi));
  const off = chicagoOffsetMinutes(guess);
  return new Date(guess.getTime() - off * 60000);
}

function plusHour(hhmm) {
  const [h, m] = String(hhmm || "19:00").split(":").map(Number);
  return `${String((h + 1) % 24).padStart(2, "0")}:${String(m || 0).padStart(2, "0")}`;
}

export default function () {
  const cfg = yaml.load(fs.readFileSync("config/site.yml", "utf8")).meeting || {};
  const weekday = WD[String(cfg.weekday || "wednesday").toLowerCase()] ?? 3;
  const n = Number(cfg.week_of_month || 3);
  // Unquoted YAML dates arrive as Date objects — normalize everything to "YYYY-MM-DD".
  const skip = new Set((cfg.skip_dates || []).map((d) => (d instanceof Date ? d.toISOString().slice(0, 10) : String(d))));
  const now = new Date();
  const dates = [];
  for (let i = -1; i < 14 && dates.length < 13; i++) {
    const base = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + i, 1));
    const y = base.getUTCFullYear(), mo = base.getUTCMonth();
    const d = nthWeekday(y, mo, weekday, n);
    if (!d) continue;
    const ymd = `${y}-${String(mo + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    if (skip.has(ymd)) continue;
    const start = atChicago(y, mo, d, cfg.start);
    const end = cfg.end ? atChicago(y, mo, d, cfg.end) : new Date(start.getTime() + 3600e3); // default: 1 hour
    if (end.getTime() < now.getTime()) continue;
    dates.push({ ymd, start: start.toISOString(), end: end.toISOString() });
  }
  return {
    // Client countdown uses the same rule; a missing end means a 1-hour meeting.
    rule: { weekday, n, start: cfg.start, end: cfg.end || plusHour(cfg.start), skip: [...skip] },
    next: dates[0] || null,
    upcoming: dates.slice(0, 12),
  };
}
