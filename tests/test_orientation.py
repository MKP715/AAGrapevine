"""GVR / RLV 101 (config/orientation.yml → /orientation/): the lesson file stays complete and safe.

The site build (src/_data/orientation.js, I18N_STRICT=1) stops on the same problems; these tests say
which lesson is wrong without running the build, and add the site's wording rules.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "config" / "orientation.yml"
I18N = ROOT / "src" / "_i18n" / "orientation.json"
PLACEHOLDERS = {"rule_lc", "time", "panel", "panel_start"}
EXAMPLES = {"issue", "meeting", "tip", "botm", "deadline", "poster"}
# The site's wording rules for visitors (docs: "document(s)", never "PDF"; no talk of how data is gathered).
BANNED = re.compile(r"\b(pdf|crawl\w*|scrap\w*|robot|bot|automatically|autom[aá]ticamente)\b", re.I)
# AA shares experience rather than teaching: the orientation is "sessions" led by a "facilitator", with
# "review questions" — never classroom words; and GVR / RLV service is a "position", never a job.
CLASSROOM = re.compile(r"\b(lessons?|lecci[oó]n(es)?|trainers?|training|quiz\w*|course|curso|curriculum|class(es)?|"
                       r"clases?|self-check|teach\w*|enseñ\w*|capacitaci[oó]n|jobs?)\b", re.I)


def pairs(node, where=""):
    """Every {en, es} text pair in the lesson data, with a readable location."""
    if isinstance(node, dict):
        if set(node) >= {"en", "es"} and all(isinstance(node[k], str) for k in ("en", "es")):
            yield where, node
            return
        for k, v in node.items():
            yield from pairs(v, f"{where}.{k}" if where else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from pairs(v, f"{where}[{i}]")


class OrientationFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = yaml.safe_load(FILE.read_text(encoding="utf-8"))
        cls.lessons = cls.cfg["lessons"]
        site = yaml.safe_load((ROOT / "config" / "site.yml").read_text(encoding="utf-8"))
        cls.site_links = site.get("links") or {}

    def test_panel(self):
        self.assertIsInstance(self.cfg["panel"]["number"], int)
        self.assertRegex(str(self.cfg["panel"]["starts"]), r"^\d{4}-\d{2}$")

    def test_six_short_lessons(self):
        self.assertEqual(len(self.lessons), 6)
        ids = [l["id"] for l in self.lessons]
        self.assertEqual(len(ids), len(set(ids)), "lesson ids must be unique (they are page addresses)")
        for l in self.lessons:
            with self.subTest(lesson=l["id"]):
                self.assertRegex(l["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                self.assertTrue(5 <= l["minutes"] <= 8, "each lesson is read in 5–8 minutes")
                self.assertIn(l["example"], EXAMPLES)
                self.assertTrue(3 <= len(l["points"]) <= 6, "3 to 6 key points")
                for key in ("title", "summary", "goal", "try", "discuss"):
                    self.assertIn(key, l)

    def test_every_text_in_both_languages(self):
        found = list(pairs(self.lessons, "lessons"))
        self.assertGreater(len(found), 100)
        for where, p in found:
            with self.subTest(where=where):
                self.assertTrue(p["en"].strip(), "missing English")
                self.assertTrue(p["es"].strip(), "missing Spanish")

    def test_lengths_fit_heroes_and_slides(self):
        for l in self.lessons:
            with self.subTest(lesson=l["id"]):
                # hero subtitle budget (ui.njk): 110 characters EN / 135 ES; a 2-line title on a phone
                self.assertLessEqual(len(l["summary"]["en"]), 110)
                self.assertLessEqual(len(l["summary"]["es"]), 135)
                self.assertLessEqual(len(l["title"]["en"]), 32)
                self.assertLessEqual(len(l["title"]["es"]), 32)
                for p in l["points"]:
                    for lang in ("en", "es"):
                        self.assertLessEqual(len(p["text"][lang]), 240, p["title"][lang])

    def test_self_check(self):
        for l in self.lessons:
            answers = []
            for i, q in enumerate(l["check"], 1):
                with self.subTest(lesson=l["id"], question=i):
                    self.assertEqual(len(q["options"]), 3)
                    self.assertIn(q["answer"], (1, 2, 3))
                    self.assertTrue(q["why"]["en"] and q["why"]["es"])
                    opts = [o["en"].lower() for o in q["options"]]
                    self.assertEqual(len(opts), len(set(opts)), "options must differ")
                    answers.append(q["answer"])
            with self.subTest(lesson=l["id"]):
                self.assertEqual(len(l["check"]), 3)
                self.assertGreater(len(set(answers)), 1, "the right answer should not always be in the same place")

    def test_placeholders_are_known(self):
        for where, p in pairs(self.lessons, "lessons"):
            for lang in ("en", "es"):
                for name in re.findall(r"\{(\w+)\}", p[lang]):
                    with self.subTest(where=where, lang=lang):
                        self.assertIn(name, PLACEHOLDERS)

    def test_wording_rules(self):
        for where, p in pairs(self.lessons, "lessons"):
            for lang in ("en", "es"):
                with self.subTest(where=where, lang=lang):
                    self.assertIsNone(BANNED.search(p[lang]), p[lang])

    def test_links_resolve(self):
        pages = {p.stem for p in (ROOT / "src" / "pages").glob("*.njk")}
        for l in self.lessons:
            self.assertTrue(l.get("links"), l["id"])
            for k in l["links"]:
                with self.subTest(lesson=l["id"], link=k.get("href") or k.get("link") or k.get("url")):
                    self.assertTrue(k["label"]["en"] and k["label"]["es"])
                    if k.get("href"):
                        seg = k["href"].strip("/").split("/")[0].split("#")[0]
                        self.assertIn(seg, pages, "href must be a page of this site")
                    elif k.get("link"):
                        self.assertIn(k["link"], self.site_links)
                        if k.get("link_es"):
                            self.assertIn(k["link_es"], self.site_links)
                    else:
                        self.assertRegex(k["url"], r"^https://www\.(aagrapevine|aalavina)\.org/")
                    if k.get("pub"):
                        self.assertIn(k["pub"], ("gv", "lv"))


class OrientationFactsTest(unittest.TestCase):
    """Facts the lessons teach, as the official pages give them (checked September 2026)."""

    @classmethod
    def setUpClass(cls):
        cls.lessons = {l["id"]: l for l in yaml.safe_load(FILE.read_text(encoding="utf-8"))["lessons"]}

    def text(self, lesson: str, lang: str) -> str:
        l = self.lessons[lesson]
        parts = [p["text"][lang] for p in l["points"]] + [l["try"][lang]]
        parts += [q["why"][lang] for q in l["check"]] + [o[lang] for q in l["check"] for o in q["options"]]
        return " ".join(parts)

    def test_how_the_magazines_are_supported(self):
        # aalavina.org/Historia-de-LV: La Viña is published by AA Grapevine "con el apoyo de la Junta de
        # Servicios Generales como un servicio a la comunidad" — the General Service Board is funded by
        # the groups' contributions, so the lesson never says the magazines take none
        self.assertIn("General Service Board", self.text("magazines", "en"))
        self.assertIn("Junta de Servicios Generales", self.text("magazines", "es"))
        self.assertNotRegex(self.text("magazines", "en"), r"(?i)(don't|do not) take group contributions|fully self-supporting")
        self.assertNotRegex(self.text("magazines", "es"), r"(?i)no reciben contribuciones")

    def test_meeting_in_print(self):
        # aagrapevine.org/history-aa-grapevine: members in the armed services overseas called it their
        # "meeting in print" — the slide shows the text without its title, so the text says it
        first = self.lessons["magazines"]["points"][0]["text"]
        self.assertIn("meeting in print", first["en"])
        self.assertIn("reunión impresa", first["es"])
        self.assertIn("armed forces", first["en"])

    def test_wording_that_stays_true_for_the_whole_panel(self):
        role = self.text("role", "en") + " " + self.text("role", "es")
        self.assertNotRegex(role, r"starts in \{panel_start\}|empieza en \{panel_start\}")

    def test_anonymity_is_about_the_public_level(self):
        # Tradition Eleven: press, radio, films (today: anything public) — not members' own chats
        self.assertNotIn("WhatsApp", self.text("traditions", "en"))
        self.assertIn("outside AA", self.text("traditions", "en"))
        self.assertIn("fuera de AA", self.text("traditions", "es"))


class OrientationStringsTest(unittest.TestCase):
    def test_ui_strings_in_both_languages(self):
        data = json.loads(I18N.read_text(encoding="utf-8"))
        self.assertGreater(len(data), 50)
        for key, v in data.items():
            with self.subTest(key=key):
                self.assertTrue(v.get("en", "").strip() and v.get("es", "").strip())
                self.assertEqual(set(re.findall(r"\{(\w+)\}", v["en"])), set(re.findall(r"\{(\w+)\}", v["es"])),
                                 "both languages use the same {placeholders}")
                self.assertIsNone(BANNED.search(v["en"]) or BANNED.search(v["es"]))

    def test_aa_wording(self):
        """Every visitor-facing text of GVR / RLV 101 (its strings and its session data), the other strings
        that name it, and the GVR / RLV corner's own strings."""
        texts = [(f"orientation.json {k}", v[lang]) for k, v in json.loads(I18N.read_text(encoding="utf-8")).items()
                 for lang in ("en", "es")]
        texts += [(f"orientation.yml {w}", p[lang]) for w, p in pairs(yaml.safe_load(FILE.read_text(encoding="utf-8")))
                  for lang in ("en", "es")]
        others = {}
        for name in ("common", "access", "pwa", "read"):
            others.update(json.loads((ROOT / "src" / "_i18n" / f"{name}.json").read_text(encoding="utf-8")))
        texts += [(k, v[lang]) for k, v in others.items() for lang in ("en", "es")
                  if k.startswith(("read.gvr.", "nav.orientation")) or "101" in v["en"]]
        self.assertGreater(len(texts), 400)
        for where, s in texts:
            with self.subTest(where=where):
                self.assertIsNone(CLASSROOM.search(s), s)


if __name__ == "__main__":
    unittest.main()
