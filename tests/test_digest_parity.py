"""The monthly edition is written twice — by the website (/digest/, eleventy/filters/community.js →
buildMonthlyDigest) and by the e-mail (scripts/notify/send_digest.py, standard library only so the
e-mail job needs no Node.js). Their rules are copies of each other ("keep them equal"); this test
builds the same editions with BOTH and compares what each one picked, so a rule changed on one side
only fails here instead of drifting quietly:

  last month's news by group (and the podcast/YouTube twins), this month's issues (stories, free ones,
  highlights, theme), the Area 65 / Texas writers, this month's events, the story deadlines, the weekly
  open meetings, La Viña's topics, the "put it to work" tips, the phone lines, the Grapevine meeting
  counts, the subscription price, the daily quote and Instagram.

Two data sets: a small one made for the edge cases (an announcement that expired the day before, in
the small hours of the 1st; a monthly series; an assembly over several days …) and the repository's
own data for several editions. Skipped without Node.js and the site's npm packages (tests/nodejs.py).

    python -m unittest tests.test_digest_parity -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodejs import run_js  # noqa: E402
import test_send_digest as SD  # noqa: E402  (its fixture: a realistic October 2026 edition)

from scripts.notify import send_digest as D  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# What buildMonthlyDigest picked, in the same shape as summary() below.
PICK_JS = r"""
const C = await imp("eleventy/filters/community.js");
const summary = (md) => ({
  news: Object.fromEntries(Object.entries(md.news).map(([k, v]) => [k, v.map((i) => i.id)])),
  twins: md.news.episode.filter((i) => i._twin).map((i) => [i.id, i._twin.id]),
  issues: md.issues.map((i) => ({ pub: i.pub, key: i.key, count: i.count, free: i.free, highlights: i.highlights.map((a) => a.id),
                                  label_en: i.label.en, theme: i.theme })),
  writers: { neta65: md.writers.neta65.map((i) => i.id || i.url), texas: md.writers.texas.map((i) => i.id || i.url) },
  events: md.events.map((e) => e.id),
  deadlines: md.deadlines.map((d) => d.id),
  weekly: { en: md.weekly.en.map((w) => w.pub), es: md.weekly.es.map((w) => w.pub) },
  lv_topics: md.lvTopics.es.map((t) => t.es),
  tips: { en: md.tips.en.map((t) => t.title), es: md.tips.es.map((t) => t.title) },
  audio: Object.fromEntries(Object.entries(md.audio).filter(([, v]) => v).map(([k, v]) => [k, v.tel])),
  gvm: [md.gvm.en.inArea, md.gvm.en.nearby],
  subs_from: md.subsFrom,
  quote: md.quote,
  instagram: md.instagram.map((p) => p.username),
});
let db, site, carry;
if (input.dir) {
  const fs = await import("node:fs");
  db = {};
  for (const f of fs.readdirSync(input.dir)) if (f.endsWith(".json")) db[f.slice(0, -5)] = JSON.parse(fs.readFileSync(input.dir + "/" + f, "utf8"));
  if (!db.spotlight) db.spotlight = { items: [] };
  site = input.site;
  carry = input.carry;
} else {
  db = (await imp("src/_data/db.js")).default();
  site = (await imp("src/_data/site.js")).default();
  carry = (await imp("src/_data/carry.js")).default();
}
const res = {};
for (const [edition, now] of input.cases) {
  res[`${edition}@${now}`] = summary(C.buildMonthlyDigest(db, null, { carry, site, now: new Date(now), edition: edition || undefined }));
}
out(res);
"""


def summary(data: dict) -> dict:
    """What send_digest.collect picked, in the shape PICK_JS writes."""
    return {
        "news": {g: [i["id"] for i in v] for g, v in data["groups"].items()},
        "twins": [[i["id"], i["_twin"]["id"]] for i in data["groups"]["episode"] if i.get("_twin")],
        "issues": [{"pub": i["pub"], "key": i["key"], "count": i["count"], "free": i["free"],
                    "highlights": [a["id"] for a in i["highlights"]], "label_en": i["label"]["en"], "theme": i["theme"]}
                   for i in data["issues"]],
        "writers": {k: [i.get("id") or i.get("url") for i in v] for k, v in data["writers"].items()},
        "events": [e["id"] for e in data["events"]],
        "deadlines": [d["id"] for d in data["deadlines"]],
        "weekly": {lang: [w["pub"] for w in data["weekly"][lang]] for lang in ("en", "es")},
        "lv_topics": [t["es"] for t in data["lv_topics"]["es"]],
        "tips": {lang: [t["title"] for t in data["tips"][lang]] for lang in ("en", "es")},
        "audio": {k: v["tel"] for k, v in data["audio"].items()},
        "gvm": [data["gvm"]["in_area"], data["gvm"]["nearby"]],
        "subs_from": data["subs_from"],
        "quote": data["quote"],
        "instagram": data["instagram"],
    }


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Parity:
    maxDiff = None

    """Compare JS and Python for each (edition, now)."""

    def compare(self, js: dict, cases: list[tuple[str, datetime]], highlights: int, max_per: int) -> None:
        for edition, now in cases:
            key = f"{edition}@{iso(now)}"
            py = summary(D.collect(now, edition or None, max_per, highlights))
            with self.subTest(edition=key):
                self.assertIn(key, js)
                for field in py:
                    with self.subTest(field=field):
                        self.assertEqual(js[key][field], py[field], f"{key}: community.js and send_digest.py differ on {field}")


class FixtureParity(SD.DigestCase, Parity):
    def test_the_edge_cases(self):
        self.full_month()
        # an announcement that expired on the last day of the month: gone on the 1st in the small hours
        # too (Central time), like everywhere else on the site
        self.write("announcements", {"items": [
            SD.item("ann:1", "announcement", "committee", "2026-09-05", "New GVR orientation", url="/announcements/#new",
                    extra={"body_md": "Join us **Saturday**."}),
            SD.item("ann:sep30", "announcement", "committee", "2026-09-06", "Until the 30th", extra={"expires": "2026-09-30"}),
            SD.item("ann:oct1", "announcement", "committee", "2026-09-07", "Until the 1st", extra={"expires": "2026-10-01"}),
        ]})
        cfg = yaml.safe_load(SD.CONFIG)
        site = {**cfg["site"], "meeting": cfg.get("meeting") or {}, "digest": cfg.get("digest") or {}, "links": {},
                "recurring_events": []}
        carry_raw = yaml.safe_load(SD.CARRY)
        ways = {w["id"]: w for w in carry_raw["ways"]}
        carry = {"ways": carry_raw["ways"], "wayById": ways, "tips": carry_raw["tips"]}
        cases = [("", SD.OCT1),                                                   # the October edition goes out
                 ("", datetime(2026, 10, 1, 6, 30, tzinfo=timezone.utc)),         # 1:30 AM CDT on October 1
                 ("", datetime(2026, 10, 20, 15, 5, tzinfo=timezone.utc)),        # later in the month
                 ("2026-11", SD.OCT1)]                                            # a preview of November
        js = run_js(self, PICK_JS, data={"dir": str(self.site_dir), "site": site, "carry": carry,
                                         "cases": [[e, iso(n)] for e, n in cases]})
        highlights = int((cfg.get("digest") or {}).get("highlights") or 3)
        max_per = int((cfg.get("digest") or {}).get("per_section") or 5)
        self.compare(js, cases, highlights, max_per)
        # and the rule itself: the announcement that ended on September 30 is not in the October edition
        early = summary(D.collect(datetime(2026, 10, 1, 6, 30, tzinfo=timezone.utc), None, max_per, highlights))
        self.assertEqual(early["news"]["announcement"], ["ann:oct1", "ann:1"])          # newest first


class RealDataParity(unittest.TestCase, Parity):
    def test_the_repositorys_data(self):
        cfg = yaml.safe_load((ROOT / "config" / "site.yml").read_text(encoding="utf-8"))
        digest = cfg.get("digest") or {}
        highlights = D._positive(digest.get("highlights"), 3)
        max_per = D._positive(digest.get("per_section"), 5)
        today = datetime.now(timezone.utc).replace(microsecond=0)
        this = D.edition_of(today)["key"]
        cases = [("", today)] + [(D.month_add(this, n), datetime(*(int(x) for x in D.month_add(this, n).split("-")), 1, 15, 5,
                                                                 tzinfo=timezone.utc)) for n in (-1, 0, 1)]
        js = run_js(self, PICK_JS, data={"cases": [[e, iso(n)] for e, n in cases]})
        self.compare(js, cases, highlights, max_per)
        self.assertTrue(json.dumps(js))


class SortOrder(unittest.TestCase):
    def test_ties_are_broken_like_localeCompare(self):
        """Items with the same date are listed by id, titles by title — the website sorts them with
        JavaScript's localeCompare; send_digest.js_order must give the same order."""
        words = ["drive:1fC3vvsu0CG-XdUagGDb_fgiw", "drive:1Fs9t4jxeMXemu_FQP", "drive:1fc3", "drive:1FC3", "pod:abc",
                 "pdf:9372f4b13305", "yt:_abc", "yt:-abc", "yt:Abc", "yt:abc", "Área 65", "Arlington", "arlington", "Écrire",
                 "ecrire", "écrire", "Ecrire", "Zeta", "alpha beta", "alpha-beta", "alpha_beta", "alpha1", "Alpha", "ñandú",
                 "nube", "oso", "A Halloween to Remember", "At Wit's End", "At Wit’s End", "“Quoted”", "(Paren)", "10 ways",
                 "9 ways", "—dash", "¡Hola!", "¿Qué?", "…more", "’s", "‘x", "”q", "«gui»", "Mi\u00a0GPS", "Mi GPS", "Mi-GPS"]
        js = run_js(self, "out(input.slice().sort((a, b) => a.localeCompare(b, 'en-US')))", data=words, needs_modules=False)
        self.assertEqual(sorted(words, key=D.js_order), js)


if __name__ == "__main__":
    unittest.main()
