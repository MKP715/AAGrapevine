"""The district report (/monthly/#report): eleventy/filters/report.js writes it, src/_i18n/report.json
words it, src/assets/js/report.js lets each GVR / RLV edit it.

  * Strings — every report.* text in English AND Spanish, with the same {placeholders} in both, and
              every key the code asks for exists (the CI build is strict about it too).
  * Model   — reportModel() on a small data set, fixed "today": the 12 sections in order, in both
              languages, what each one says, and the fallback sentence of each section when there
              is nothing to report (no meeting date, no deadlines, no events …); the plain text the
              page shows without JavaScript; the editor's own strings; the events rule.
  * Real    — the model built from the repository's own data: complete, both languages, no
              placeholder left unfilled.

The Model and Real checks run the JavaScript with Node.js (tests/nodejs.py) and are skipped without
Node.js or the site's npm packages.

    python -m unittest tests.test_report -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodejs import run_js  # noqa: E402

STRINGS = ROOT / "src" / "_i18n" / "report.json"
SECTION_IDS = ["header", "committee", "issues", "deadlines", "shop", "events", "writers", "meetings", "weekly",
               "resources", "asks", "notes"]
SITE_URL = "https://example.org/site"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
NOW = "2026-10-05T17:00:00Z"            # Monday, October 5, 2026, noon in Texas


def strings() -> dict:
    return json.loads(STRINGS.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ a small data set (October 2026)
def item(iid: str, kind: str, **kw) -> dict:
    it = {"id": iid, "kind": kind, "status": "ok", "title": kw.pop("title", iid), "url": kw.pop("url", f"https://example.com/{iid}"),
          "date": kw.pop("date", None), "lang": kw.pop("lang", "en"), "category": kw.pop("category", None),
          "source": kw.pop("source", "committee"), "extra": kw.pop("extra", {}), "i18n": kw.pop("i18n", {}), "machine": []}
    it.update(kw)
    return it


def event(iid: str, start: str, end: str | None = None, **kw) -> dict:
    extra = {"start": start, "end": end, **kw.pop("extra", {})}
    return item(iid, "event", date=start, extra=extra, **kw)


def full_db() -> dict:
    return {
        "events": {"items": [
            event("ev:committee", "2026-10-22T00:00:00Z", "2026-10-22T01:00:00Z", title="Committee meeting", category="committee"),
            event("ev:done", "2026-10-03T19:00:00Z", "2026-10-03T21:00:00Z", title="Already over"),
            event("ev:ws", "2026-10-17T15:00:00Z", "2026-10-17T18:00:00Z", title="Grapevine Writing Workshop",
                  i18n={"title": {"en": "Grapevine Writing Workshop", "es": "Taller de escritura de Grapevine"}},
                  extra={"location": "Arlington, TX"}),
            event("ev:rec:2026-10-10", "2026-10-10T22:00:00Z", "2026-10-11T01:00:00Z", title="CityWide booth", category="recurring",
                  extra={"series": "citywide"}),
            event("ev:rec:2026-11-14", "2026-11-14T23:00:00Z", "2026-11-15T02:00:00Z", title="CityWide booth", category="recurring",
                  extra={"series": "citywide"}),
            event("ev:assembly", "2026-10-30", "2026-11-01", title="Fall Assembly", extra={"all_day": True, "tentative": True}),
            event("ev:far", "2026-12-12T16:00:00Z", None, title="Too far ahead"),
        ]},
        "editorial": {"items": [
            item("ed:gv:2027-05", "topic", title="Fun in Sobriety", source="grapevine",
                 extra={"publication": "gv", "issue_key": "2027-05", "deadline": "2026-10-15"},
                 i18n={"title": {"en": "Fun in Sobriety", "es": "Diversión en sobriedad"}}),
            item("ed:gv:old", "topic", title="Closed", source="grapevine", extra={"publication": "gv", "issue_key": "2027-03", "deadline": "2026-09-01"}),
        ] + [item(f"ed:lv:t{i}", "topic", title=f"Tema {i}", lang="es", source="lavina", extra={"publication": "lv", "evergreen": True},
                  i18n={"title": {"en": f"Topic {i}", "es": f"Tema {i}"}}) for i in range(4)]},
        "articles": {"items": [], "issues": [
            {"id": "gv:2026-10", "publication": "gv", "key": "2026-10", "label": "October 2026", "theme": "Loneliness",
             "url": "https://www.aagrapevine.org/magazine-issue/october-2026",
             "i18n": {"theme": {"en": "Loneliness", "es": "Soledad"}, "label": {"en": "October 2026", "es": "Octubre 2026"}}},
            {"id": "lv:2026-09", "publication": "lv", "key": "2026-09", "label": "Septiembre / Octubre 2026", "theme": "Servicio en AA",
             "url": "https://www.aalavina.org/revista/septiembre-octubre-2026",
             "i18n": {"theme": {"en": "Service in AA", "es": "Servicio en AA"},
                      "label": {"en": "September / October 2026", "es": "Septiembre / Octubre 2026"}}},
        ]},
        "shop": {"botm": [], "subscriptions": [
            {"pub": "gv", "region": "us", "plans": [{"type": "print", "term_months": 12, "price": 36.0},
                                                   {"type": "digital", "term_months": 1, "price": 2.99},
                                                   {"type": "digital", "term_months": 12, "price": 29.99}]},
            {"pub": "lv", "region": "us", "plans": [{"type": "print", "term_months": 12, "price": 18.0},
                                                   {"type": "digital", "term_months": 12, "price": 14.99}]},
        ]},
        "meetings": {"items": []},
        "weekly_open": {"items": [
            item("weekly_open", "meeting", title="Grapevine Weekly Open AA Meeting", source="grapevine",
                 extra={"zoom_id": "871 2036 8287", "passcode": "238047", "weekday": "wednesday", "start_local": "12:00",
                        "timezone": "America/New_York", "next_start": "2026-10-07T16:00:00Z"},
                 i18n={"title": {"en": "Grapevine Weekly Open AA Meeting", "es": "Grapevine Weekly Open AA Meeting"},
                       "when": {"en": "Wednesdays at 11:00 AM Central", "es": "Los miércoles a las 11:00 a. m. (hora del Centro)"}}),
        ]},
        "pdfs": {"items": [
            item("pdf:1", "pdf", title="GV News October 2026", date="2026-09-28", url="https://www.aagrapevine.org/gv-news.pdf",
                 i18n={"title": {"en": "GV News October 2026", "es": "Noticias de GV, octubre de 2026"}}),
        ]},
        "spotlight": {"items": [
            item("sp:1", "article", title="My spiritual GPS", source="grapevine", category="gv",
                 extra={"publication": "gv", "pub_date": "2026-09-20", "author": "Jim B.", "issue_label": "October 2026",
                        "geo": {"scope": "neta65", "city": "Pottsboro", "label_en": "Pottsboro, Texas"}},
                 i18n={"title": {"en": "My spiritual GPS", "es": "Mi GPS espiritual"},
                       "issue_label": {"en": "October 2026", "es": "Octubre 2026"}}),
        ]},
        "audio_project": {"gv": {"phone": "(559) 726-1216", "tel": "+15597261216", "minutes_min": 6, "minutes_max": 8},
                          "lv": {"phone": "(559) 670-1601", "tel": "+15596701601", "minutes_max": 7}},
    }


MEETING = {"next": {"ymd": "2026-10-21", "start": "2026-10-22T00:00:00Z", "end": "2026-10-22T01:00:00Z"},
           "rule": {"weekday": 3, "n": 3, "start": "19:00", "end": "20:00"}}

EMPTY_DB = {"events": {"items": []}, "editorial": {"items": []}, "articles": {"items": [], "issues": []},
            "shop": {}, "meetings": {"items": []}, "weekly_open": {"items": []}, "pdfs": {"items": []},
            "spotlight": {"items": []}, "audio_project": {}}

# Build the model(s) the way the page does (the site's own settings and config/carry.yml).
MODEL_JS = """
const R = await imp("eleventy/filters/report.js");
const site = (await imp("src/_data/site.js")).default();
const carry = (await imp("src/_data/carry.js")).default();
const now = new Date(input.now);
const build = (db, meeting) => {
  const model = R.reportModel(db, meeting, carry, site, now);
  return { model, text: { en: R.reportText(model, "en"), es: R.reportText(model, "es") } };
};
out({
  ids: R.SECTION_IDS,
  full: build(input.full, input.meeting),
  empty: build(input.empty, {}),
  ui: { en: R.uiStrings("en"), es: R.uiStrings("es") },
  events: R.upcomingEvents(input.full, now).map((e) => [e.id, e._recurring, e._tentative]),
  safe: R.safeJson({ a: "</script><!-- x" }),
});
"""


class Strings(unittest.TestCase):
    def test_both_languages_and_the_same_placeholders(self):
        s = strings()
        self.assertGreater(len(s), 150)
        for key, v in s.items():
            with self.subTest(key=key):
                self.assertTrue(key.startswith("report."))
                self.assertTrue(isinstance(v.get("en"), str) and v["en"].strip(), "missing English")
                self.assertTrue(isinstance(v.get("es"), str) and v["es"].strip(), "missing Spanish")
                self.assertEqual(sorted(set(PLACEHOLDER.findall(v["en"]))), sorted(set(PLACEHOLDER.findall(v["es"]))),
                                 "both languages use the same {placeholders}")

    def test_every_key_the_code_asks_for_exists(self):
        s = strings()
        code = (ROOT / "eleventy" / "filters" / "report.js").read_text(encoding="utf-8")
        used = set(re.findall(r'\bT\("([a-z0-9_.]+)"', code)) | {k[len("report."):] for k in re.findall(r'"(report\.[a-z0-9_.]+)"', code)}
        self.assertGreater(len(used), 50)
        for k in sorted(used):
            with self.subTest(key=k):
                self.assertIn(f"report.{k}", s)
        for sid in SECTION_IDS + ["custom"]:
            self.assertIn(f"report.s.{sid}", s)
        ui = re.search(r"const UI_KEYS = \[(.*?)\];", code, re.S)
        self.assertIsNotNone(ui)
        for k in re.findall(r'"([a-z0-9_]+)"', ui.group(1)):
            with self.subTest(ui=k):
                self.assertIn(f"report.{k}", s)
        # the page and the editor
        page = (ROOT / "src" / "pages" / "monthly.njk").read_text(encoding="utf-8") \
            + (ROOT / "src" / "assets" / "js" / "report.js").read_text(encoding="utf-8")
        for k in sorted(set(re.findall(r'"(report\.[a-z0-9_.]+)"', page))):
            with self.subTest(page_key=k):
                self.assertIn(k, s)


class Model(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = None

    def setUp(self):
        if Model.res is None:
            Model.res = run_js(self, MODEL_JS, data={"now": NOW, "full": full_db(), "meeting": MEETING, "empty": EMPTY_DB},
                               env={"SITE_URL": SITE_URL})
        self.r = Model.res
        self.s = strings()

    def sections(self, which: str, lang: str) -> dict[str, dict]:
        return {x["id"]: x for x in self.r[which]["model"]["langs"][lang]["sections"]}

    def tr(self, key: str, lang: str, **v) -> str:
        return PLACEHOLDER.sub(lambda m: str(v.get(m.group(1), m.group(0))), self.s[key][lang])

    def test_twelve_sections_in_order_in_both_languages(self):
        self.assertEqual(self.r["ids"], SECTION_IDS)
        m = self.r["full"]["model"]
        self.assertEqual((m["v"], m["month"]), (1, "2026-10"))
        for lang in ("en", "es"):
            with self.subTest(lang=lang):
                secs = m["langs"][lang]["sections"]
                self.assertEqual([x["id"] for x in secs], SECTION_IDS)
                self.assertEqual([x["title"] for x in secs], [self.s[f"report.s.{i}"][lang] for i in SECTION_IDS])
                for x in secs:
                    self.assertNotRegex(x["text"], r"\{[a-z_]+\}", f"{x['id']}: a placeholder was left unfilled")
                    self.assertNotIn("report.", x["text"])
                    self.assertNotIn("undefined", x["text"])
        self.assertEqual(m["langs"]["en"]["month"], "October 2026")
        self.assertEqual(m["langs"]["es"]["month"], "octubre de 2026")
        en, es = self.sections("full", "en"), self.sections("full", "es")
        for sid in SECTION_IDS:
            with self.subTest(section=sid):
                self.assertEqual(bool(en[sid]["text"].strip()), bool(es[sid]["text"].strip()), "EN and ES say something alike")

    def test_what_the_sections_say(self):
        for lang in ("en", "es"):
            S = {k: v["text"] for k, v in self.sections("full", lang).items()}
            base = SITE_URL + ("/es" if lang == "es" else "")
            with self.subTest(lang=lang, section="header"):
                self.assertTrue(S["header"].startswith(self.tr("report.h_title", lang, district=self.s["report.tok_district"][lang],
                                                               month="October 2026" if lang == "en" else "octubre de 2026")))
            with self.subTest(lang=lang, section="committee"):
                self.assertIn("October 21" if lang == "en" else "21 de octubre", S["committee"])
                self.assertIn(self.tr("report.c_join", lang, url=f"{base}/meetings/"), S["committee"])
                self.assertNotIn(self.s["report.c_none"][lang], S["committee"])
            with self.subTest(lang=lang, section="issues"):
                self.assertIn("Loneliness" if lang == "en" else "Soledad", S["issues"])
                self.assertIn(f"{base}/monthly/2026-10/", S["issues"])
                # La Viña first on the Spanish report
                gv, lv = S["issues"].find("Grapevine"), S["issues"].find("La Viña")
                self.assertTrue(gv >= 0 and lv >= 0)
                self.assertEqual(lv < gv, lang == "es")
            with self.subTest(lang=lang, section="deadlines"):
                self.assertIn("“Fun in Sobriety”" if lang == "en" else "“Diversión en sobriedad” (“Fun in Sobriety”)", S["deadlines"])
                self.assertNotIn("Closed", S["deadlines"])                     # its deadline has passed
                self.assertIn("(559) 726-1216", S["deadlines"])
                self.assertIn("(559) 670-1601", S["deadlines"])
                self.assertIn("“Tema", S["deadlines"])
            with self.subTest(lang=lang, section="shop"):
                self.assertIn(self.s["report.s_title"][lang], S["shop"])
                self.assertIn(f"{base}/shop/", S["shop"])
            with self.subTest(lang=lang, section="events"):
                self.assertIn(self.tr("report.e_intro", lang, n=45), S["events"])
                self.assertIn("Grapevine Writing Workshop" if lang == "en" else "Taller de escritura de Grapevine", S["events"])
                self.assertEqual(S["events"].count("CityWide booth"), 1)        # a monthly series once …
                self.assertIn(self.s["report.e_monthly"][lang], S["events"])   # … marked "every month"
                self.assertIn(self.s["report.e_tbc"][lang], S["events"])       # the tentative assembly
                for gone in ("Already over", "Too far ahead", "Committee meeting"):
                    self.assertNotIn(gone, S["events"])
                # the time zone as the committee line says it: "CDT" in English, "(hora del Centro)" in Spanish
                if lang == "es":
                    self.assertRegex(S["events"], r"m\. \(hora del Centro\) —")
                    self.assertNotRegex(S["events"], r"\b(?:CDT|CST)\b")
                else:
                    self.assertIn("CDT", S["events"])
            with self.subTest(lang=lang, section="writers"):
                self.assertIn("Jim B., Pottsboro", S["writers"])
                self.assertIn("My spiritual GPS" if lang == "en" else "Mi GPS espiritual", S["writers"])
            with self.subTest(lang=lang, section="meetings"):
                self.assertEqual(S["meetings"], self.tr("report.m_empty", lang, url=f"{base}/meetings/#grapevine-meetings"))
            with self.subTest(lang=lang, section="weekly"):
                self.assertIn("Grapevine Weekly Open AA Meeting", S["weekly"])
                self.assertIn(f"{base}/meetings/#weekly-open", S["weekly"])
            with self.subTest(lang=lang, section="resources"):
                self.assertIn("https://www.aagrapevine.org/gv-news.pdf", S["resources"])
                self.assertIn(f"{base}/gvr/", S["resources"])
            with self.subTest(lang=lang, section="asks/notes"):
                self.assertEqual(S["asks"], self.s["report.a_default"][lang])
                self.assertEqual(S["notes"], "")

    def test_nothing_to_report_falls_back_to_a_sentence(self):
        for lang in ("en", "es"):
            S = {k: v["text"] for k, v in self.sections("empty", lang).items()}
            base = SITE_URL + ("/es" if lang == "es" else "")
            with self.subTest(lang=lang):
                self.assertTrue(S["committee"].startswith(self.s["report.c_none"][lang]))
                self.assertIn(self.tr("report.i_none", lang, url=f"{base}/read/"), S["issues"])
                self.assertIn(self.s["report.d_none"][lang], S["deadlines"])
                self.assertIn(self.tr("report.e_none", lang, n=45), S["events"])
                self.assertIn(self.s["report.w_none"][lang].replace("{n}", "60"), S["writers"])
                self.assertEqual(S["meetings"], self.tr("report.m_empty", lang, url=f"{base}/meetings/#grapevine-meetings"))
                self.assertEqual(S["weekly"], "")                                # nothing to say: left out
                self.assertIn(self.tr("report.r_library", lang, url=f"{base}/library/"), S["resources"])
                self.assertNotIn(self.s["report.b_title_plain"][lang], S["shop"])  # no Book of the Month offer

    def test_the_plain_text(self):
        for which in ("full", "empty"):
            for lang in ("en", "es"):
                with self.subTest(which=which, lang=lang):
                    text = self.r[which]["text"][lang]
                    secs = self.sections(which, lang)
                    self.assertTrue(text.startswith(secs["header"]["text"] + "\n\n1. "))
                    self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))
                    shown = [sid for sid in SECTION_IDS[1:] if secs[sid]["text"].strip()]
                    for n, sid in enumerate(shown, 1):                       # numbered, empty sections left out
                        self.assertIn(f"\n{n}. {secs[sid]['title']}\n", text)
                    self.assertNotIn(f"{len(shown) + 1}. ", text.split("\n\n")[-1][:5])
                    self.assertNotIn(secs["notes"]["title"], text)

    def test_editor_strings_and_json(self):
        for lang in ("en", "es"):
            with self.subTest(lang=lang):
                ui = self.r["ui"][lang]
                self.assertGreaterEqual(len(ui), 30)
                for k, v in ui.items():
                    self.assertTrue(isinstance(v, str) and v.strip() and not v.startswith("report."), k)
        self.assertNotIn("</script", self.r["safe"])
        self.assertNotIn("<!--", self.r["safe"])
        self.assertEqual(json.loads(self.r["safe"]), {"a": "</script><!-- x"})

    def test_upcoming_events_rule(self):
        self.assertEqual(self.r["events"], [["ev:rec:2026-10-10", True, False], ["ev:ws", False, False],
                                            ["ev:assembly", False, True]])


class RealData(unittest.TestCase):
    def test_the_report_builds_from_the_repositorys_data(self):
        res = run_js(self, """
            const R = await imp("eleventy/filters/report.js");
            const db = (await imp("src/_data/db.js")).default();
            const site = (await imp("src/_data/site.js")).default();
            const carry = (await imp("src/_data/carry.js")).default();
            const meeting = (await imp("src/_data/meeting.js")).default();
            const model = R.reportModel(db, meeting, carry, site);
            out({ model, en: R.reportText(model, "en"), es: R.reportText(model, "es") });
        """, env={"SITE_URL": SITE_URL})
        m = res["model"]
        for lang in ("en", "es"):
            with self.subTest(lang=lang):
                secs = m["langs"][lang]["sections"]
                self.assertEqual([x["id"] for x in secs], SECTION_IDS)
                for x in secs:
                    self.assertNotRegex(x["text"], r"\{[a-z_]+\}", x["id"])
                    self.assertNotIn("undefined", x["text"])
                    self.assertNotIn("NaN", x["text"])
                self.assertGreater(len(res[lang]), 800)
                self.assertIn("1. ", res[lang])
        self.assertIsInstance(m["meetings"]["options"], list)
        for opt in m["meetings"]["options"]:
            self.assertTrue(opt["label"]["en"] and opt["label"]["es"])


if __name__ == "__main__":
    unittest.main()
