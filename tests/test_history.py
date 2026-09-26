"""The history of Grapevine & La Viña (config/history.yml → src/_data/history.js → /about/#history).

  * the file: every milestone complete in both languages, a known type, a readable year, oldest first,
    the copy's source noted, the site's wording rules (documents, not "PDF"; typographic quotes);
  * the data file (Node.js): labels and <time> values, decades, the filter counts, the left/right turns
    and the collapse classes the page's CSS uses — and, with I18N_STRICT=1, a broken file stops the build;
  * the pages' sources: the timeline's i18n keys exist in both languages, and "Built with open-source
    software" lives on /status/ only (its keys under community.status.*, none left under read.about.*).

    python -m unittest tests.test_history -v
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodejs import run_js  # noqa: E402

FILE = ROOT / "config" / "history.yml"
TYPES = {"grapevine", "lavina", "both"}
YEAR = re.compile(r"^(?:(January|February|March|April|May|June|July|August|September|October|November|December|"
                  r"Spring|Summer|Fall|Autumn|Winter) )?(\d{4})$")
BANNED = re.compile(r"\b(pdfs?|crawl\w*|scrap\w*|robot|bot|automatically|autom[aá]ticamente)\b", re.I)


def load_i18n(name: str) -> dict:
    return json.loads((ROOT / "src" / "_i18n" / name).read_text(encoding="utf-8"))


class HistoryFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FILE.read_text(encoding="utf-8")
        cls.cfg = yaml.safe_load(cls.text)
        cls.ms = cls.cfg["milestones"]

    def test_source_noted(self):
        head = self.text.split("\nofficial:", 1)[0]
        self.assertIn("https://neta65.github.io/Grapevine/#about", head)
        self.assertIn("https://neta65.github.io/Grapevine/data/timeline.csv", head)
        self.assertRegex(head, r"copied on \d{4}-\d{2}-\d{2}")

    def test_complete_in_both_languages(self):
        self.assertEqual(len(self.ms), 40, "the old site's timeline has 40 rows — all of them are kept")
        for i, m in enumerate(self.ms):
            with self.subTest(i=i, year=m.get("year")):
                self.assertIn(m["type"], TYPES)
                self.assertRegex(str(m["year"]), YEAR)
                for part in ("title", "desc"):
                    for lang in ("en", "es"):
                        v = m[part][lang]
                        self.assertIsInstance(v, str)
                        self.assertTrue(v.strip(), f"{part}.{lang} is empty")
                        self.assertEqual(v, v.strip())

    def test_oldest_first_and_all_types(self):
        years = [int(YEAR.match(str(m["year"])).group(2)) for m in self.ms]
        self.assertEqual(years, sorted(years))
        self.assertEqual({m["type"] for m in self.ms}, TYPES)

    def test_wording(self):
        for i, m in enumerate(self.ms):
            for part in ("title", "desc"):
                for lang in ("en", "es"):
                    v = m[part][lang]
                    with self.subTest(i=i, part=part, lang=lang):
                        self.assertIsNone(BANNED.search(v))
                        # typographic quotes: “ ” ’ in English, « » in Spanish — never straight ones
                        self.assertNotRegex(v, r"[\"']")
                        if lang == "es":
                            self.assertNotRegex(v, r"[“”]")
                        if part == "title":
                            self.assertNotRegex(v, r"[a-z]{4,}\.$", "headings have no full stop")

    def test_official_links(self):
        off = self.cfg["official"]
        self.assertEqual(set(off), {"grapevine", "lavina"})
        for u in off.values():
            self.assertTrue(u.startswith("https://www.aagrapevine.org/"), u)


DATA_JS = """
const mod = await imp("src/_data/history.js");
const h = mod.default();
let broken = null;
if (input.tmp) {
  process.chdir(input.tmp);
  try { mod.default(); broken = "no error"; } catch (e) { broken = String(e.message || e); }
}
out({ h, broken });
"""


class HistoryDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = yaml.safe_load(FILE.read_text(encoding="utf-8"))["milestones"]

    def run_data(self, broken_yaml: str | None = None):
        if broken_yaml is None:
            return run_js(self, DATA_JS, data={"tmp": None})
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config").mkdir()
            (Path(tmp) / "config" / "history.yml").write_text(broken_yaml, encoding="utf-8")
            return run_js(self, DATA_JS, data={"tmp": tmp})

    def test_items_and_labels(self):
        h = self.run_data()["h"]
        items = h["items"]
        self.assertEqual(len(items), len(self.ms))
        by_year = {it["year"]: it for it in items}
        self.assertEqual(by_year["June 1944"]["label"], {"en": "June 1944", "es": "Junio de 1944"})
        self.assertEqual(by_year["June 1944"]["iso"], "1944-06")
        self.assertEqual(by_year["Summer 1996"]["label"], {"en": "Summer 1996", "es": "Verano de 1996"})
        self.assertEqual(by_year["Summer 1996"]["iso"], "1996")
        self.assertEqual(by_year["1948"]["label"], {"en": "1948", "es": "1948"})
        self.assertEqual(by_year["September 2023"]["iso"], "2023-09")
        for it, m in zip(items, self.ms):
            self.assertEqual(it["title"], {k: m["title"][k].strip() for k in ("en", "es")})
            self.assertEqual(it["tone"], {"grapevine": "gv", "lavina": "lv", "both": "vine"}[m["type"]])
            self.assertRegex(it["iso"], r"^\d{4}(-\d{2})?$")
        self.assertEqual(h["first"], 1944)
        self.assertEqual(h["last"], int(items[-1]["iso"][:4]))
        self.assertEqual(h["years"] % 10, 0)
        self.assertGreaterEqual(h["years"], 80)

    def test_counts_decades_and_turns(self):
        h = self.run_data()["h"]
        types = [m["type"] for m in self.ms]
        want = {"all": len(types), "gv": sum(t != "lavina" for t in types),
                "lv": sum(t != "grapevine" for t in types), "both": types.count("both")}
        self.assertEqual(h["count"], want)
        decs = [d["decade"] for d in h["decades"]]
        self.assertEqual(decs, sorted(set(decs)))
        self.assertEqual(sum(len(d["items"]) for d in h["decades"]), want["all"])
        for f in ("all", "gv", "lv"):
            ranks = [it["rank"][f] for it in h["items"] if it["rank"][f]]
            self.assertEqual(ranks, list(range(1, want[f] + 1)), f)
            for d in h["decades"]:
                inside = [it for it in d["items"] if it["rank"][f]]
                self.assertEqual(d["count"][f], len(inside))
                # left and right take turns inside the decade, starting on the left
                self.assertEqual([it["side"][f] for it in inside], ["l" if i % 2 == 0 else "r" for i in range(len(inside))])
                self.assertEqual(d["first"][f], inside[0]["rank"][f] if inside else 0)
                self.assertEqual(f"hn-{f}" in d["cls"].split(), not inside)

    def test_collapse_classes(self):
        h = self.run_data()["h"]
        for it in h["items"]:
            cls = it["cls"].split()
            for f in ("all", "gv", "lv"):
                for n in (7, 12):
                    self.assertEqual(f"hx{n}-{f}" in cls, it["rank"][f] > n, (it["year"], f, n))
                self.assertEqual(f"hs-{f}" in cls, it["side"].get(f) == "r")
        more = h["moreCls"].split()
        for f in ("all", "gv", "lv"):
            for n in (7, 12):
                self.assertEqual(f"bn{n}-{f}" in more, h["count"][f] <= n)

    def test_broken_file_stops_the_strict_build(self):
        bad = ("milestones:\n"
               "  - year: \"1950\"\n    type: grapevine\n    title: { en: \"A\", es: \"A\" }\n    desc: { en: \"B\" }\n"
               "  - year: \"Someday 1949\"\n    type: lavina\n    title: { en: \"C\", es: \"C\" }\n    desc: { en: \"D\", es: \"D\" }\n"
               "  - year: \"1940\"\n    type: magazine\n    title: { en: \"E\", es: \"E\" }\n    desc: { en: \"F\", es: \"F\" }\n"
               "  - year: \"1945\"\n    type: both\n    title: { en: \"G\", es: \"G\" }\n    desc: { en: \"H\", es: \"H\" }\n")
        msg = self.run_data(bad)["broken"]
        self.assertIn("[history]", msg)
        self.assertIn('missing "es"', msg)
        self.assertIn("year must look like", msg)
        self.assertIn('type "magazine"', msg)
        self.assertIn("out of order", msg)


class PagesTest(unittest.TestCase):
    def test_timeline_keys(self):
        read = load_i18n("read.json")
        about = (ROOT / "src" / "pages" / "about.njk").read_text(encoding="utf-8")
        keys = set(re.findall(r'"(read\.about\.hist_\w+)"', about))
        self.assertGreaterEqual(len(keys), 8)
        for k in keys:
            with self.subTest(key=k):
                self.assertIn(k, read)
                self.assertTrue(read[k]["en"].strip() and read[k]["es"].strip())

    def test_credits_live_on_status_only(self):
        about = (ROOT / "src" / "pages" / "about.njk").read_text(encoding="utf-8")
        status = (ROOT / "src" / "pages" / "status.njk").read_text(encoding="utf-8")
        read, community = load_i18n("read.json"), load_i18n("community.json")
        self.assertNotIn("credits", about.split("-#}", 1)[1])
        self.assertEqual([k for k in read if k.startswith(("read.about.cr_", "read.about.credits"))], [])
        self.assertIn('id="credits"', status)
        slugs = re.findall(r'\["[^"]+", "https://[^"]+", "[^"]+", "(\w+)"\]', status)
        self.assertGreaterEqual(len(slugs), 13)
        for k in ["community.status.credits_title", "community.status.credits_sub", "community.status.credits_eyebrow"] + \
                 [f"community.status.cr_{s}" for s in slugs]:
            with self.subTest(key=k):
                self.assertIn(k, community)
                self.assertTrue(community[k]["en"].strip() and community[k]["es"].strip())


if __name__ == "__main__":
    unittest.main()
