"""Reading comfort ("Aa" panel) and the Accessibility page — static checks that need no browser (the
browser checks — keyboard, read aloud with a mocked voice, larger text at 320–1920 px, axe in high
contrast — are in the QA scripts; README → "Reading & display settings and the Accessibility page").

  * Strings   — every comfort.* (src/_i18n/common.json) and access.* (src/_i18n/access.json) key in
                English AND Spanish, within the design spec's length budgets, and following the wording
                rules ("document", never "PDF"; nothing about how the site updates itself).
  * Contract  — base.njk applies the saved choices (localStorage "gvlv-prefs") to <html> before the
                first paint (data-text / data-spacing / data-contrast / data-motion / data-saver); the
                panel is one non-modal dialog with the #pwa-slot the offline-app script fills.
  * Phone     — config/site.yml `phone_access:` numbers are U.S. dial-in numbers and the callers'
                passcodes are empty or numbers only (Zoom phone passcodes are digits).
  * Page      — /accessibility/ and /es/accessibility/ exist, are in the footer (nav.js) and so in the
                search index, and the Meetings page points to its #phone section.

    python -m unittest tests.test_accessibility -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def strings() -> dict:
    common = json.loads(read("src", "_i18n", "common.json"))
    access = json.loads(read("src", "_i18n", "access.json"))
    out = {k: v for k, v in common.items() if k.startswith("comfort.") or k == "nav.accessibility"}
    out.update(access)
    return out


class Strings(unittest.TestCase):
    def test_every_key_in_both_languages(self):
        s = strings()
        self.assertGreater(len(s), 150)
        for key, val in s.items():
            with self.subTest(key=key):
                self.assertIsInstance(val.get("en"), str)
                self.assertIsInstance(val.get("es"), str)
                self.assertTrue(val["en"].strip() and val["es"].strip())

    def test_every_key_used_by_the_templates_exists(self):
        s = strings()
        src = read("src", "_includes", "partials", "comfort-panel.njk") + read("src", "_includes", "partials", "header.njk") \
            + read("src", "pages", "accessibility.njk") + read("src", "pages", "meetings.njk")
        used = set(re.findall(r'"((?:comfort|access)\.[a-z0-9_]+)"(?!\s*\+)', src))
        # keys built in the template from a prefix ("access.f_" + name + "_t", "access.cap_" + c + n …)
        prefixes = set(re.findall(r'"((?:comfort|access)\.[a-z0-9_]+)"\s*\+', src))
        self.assertTrue(used)
        for key in sorted(used):
            with self.subTest(key=key):
                self.assertTrue(key in s, key)
        for p in prefixes:
            with self.subTest(prefix=p):
                self.assertTrue(any(k.startswith(p) for k in s), p)

    def test_length_budgets(self):
        s = strings()
        budgets = {  # design spec 1.18: hero subtitle 110/135, buttons 24/28, chips 16/20
            "access.hero_sub": (110, 135),
            **{k: (24, 28) for k in ("access.open_settings", "access.open_short", "access.cap_watch", "access.asl_gv_btn",
                                     "access.ph_ask_btn", "access.fb_btn", "access.pr_posters_link", "access.aud_listen_cta",
                                     "access.aud_weekly_cta", "access.aud_record_cta", "access.aud_aa_cta", "comfort.read_play",
                                     "comfort.reset", "access.join_by_phone")},
            **{k: (16, 20) for k in s if k.startswith("access.nav_")},
        }
        for key, (en, es) in budgets.items():
            with self.subTest(key=key):
                self.assertLessEqual(len(s[key]["en"]), en)
                self.assertLessEqual(len(s[key]["es"]), es)

    def test_wording_rules(self):
        banned = re.compile(r"\bPDF\b|\bcrawl|\brobot|\bbot\b|automatically|itself every|every (day|morning)|GitHub Action|Cloudflare", re.I)
        for key, val in strings().items():
            for lang in ("en", "es"):
                with self.subTest(key=key, lang=lang):
                    self.assertIsNone(banned.search(val[lang]), val[lang])


class Contract(unittest.TestCase):
    def test_head_script_applies_the_saved_choices_before_paint(self):
        base = read("src", "_includes", "layouts", "base.njk")
        head = base.split("<title>")[0]
        self.assertIn('localStorage.getItem("gvlv-prefs")', head)
        for attr in ("data-text", "data-spacing", "data-contrast", "data-motion", "data-saver"):
            self.assertIn(f'setAttribute("{attr}"', head, attr)
        self.assertIn("try {", head)
        self.assertRegex(head, r"saveData === true")
        self.assertRegex(head, r"slow-2g\|2g")

    def test_panel_markup(self):
        panel = read("src", "_includes", "partials", "comfort-panel.njk")
        header = read("src", "_includes", "partials", "header.njk")
        self.assertIn('{% include "partials/comfort-panel.njk" %}', header)
        self.assertEqual(panel.count('<div id="pwa-slot"></div>'), 1)
        self.assertIn('role="dialog"', panel)
        self.assertIn('aria-modal="false"', panel)
        self.assertIn('aria-labelledby="gvlv-title"', panel)
        for group in ("text", "spacing", "contrast", "motion", "saver"):
            self.assertIn(f'seg("{group}"', panel, group)
        # the Aa buttons: header (from 640 px) and the drawer, all pointing at the one panel
        self.assertGreaterEqual(header.count('aria-controls="gvlv-panel"'), 3)
        self.assertIn('"comfort.button"', header)
        # the order the offline-app contract expects: data saver, then #pwa-slot, then Reset
        self.assertLess(panel.index('seg("saver"'), panel.index('id="pwa-slot"'))
        self.assertLess(panel.index('id="pwa-slot"'), panel.index('"comfort.reset"'))

    def test_app_js_prefs_api(self):
        app = read("src", "assets", "js", "app.js")
        self.assertIn('var PREF_KEY = "gvlv-prefs";', app)
        self.assertIn('"gvlv:prefs"', app)
        self.assertRegex(app, r"text: \[100, 115, 130, 150\]")
        self.assertRegex(app, r'saver: \["off", "on", "auto"\]')
        # every storage access is guarded
        for m in re.finditer(r"localStorage\.(getItem|setItem|removeItem)", app):
            window = app[max(0, m.start() - 200):m.start()]
            self.assertIn("try", window, app[m.start() - 80:m.end() + 40])


class Phone(unittest.TestCase):
    def setUp(self):
        self.cfg = yaml.safe_load(read("config", "site.yml"))

    def test_dial_in_numbers(self):
        nums = self.cfg["phone_access"]["numbers"]
        self.assertGreaterEqual(len(nums), 3)
        self.assertTrue(any("346" in n["number"] for n in nums), "Houston first/among the numbers")
        for n in nums:
            with self.subTest(number=n["number"]):
                digits = re.sub(r"\D", "", n["number"])
                self.assertRegex(digits, r"^1[2-9]\d{2}[2-9]\d{6}$")
                self.assertTrue(n.get("city"))

    def test_phone_passcodes_are_numbers_or_empty(self):
        pa = self.cfg["phone_access"]
        for part in ("committee", "weekly_open"):
            with self.subTest(part=part):
                self.assertRegex(str(pa[part].get("phone_passcode") or ""), r"^\d*$")

    def test_meeting_ids(self):
        self.assertRegex(re.sub(r"\D", "", str(self.cfg["meeting"]["meeting_id"])), r"^\d{9,11}$")
        self.assertRegex(re.sub(r"\D", "", str(self.cfg["lavina_weekly_open"]["zoom_id"])), r"^\d{9,11}$")

    def test_accessibility_links(self):
        links = self.cfg["links"]
        self.assertRegex(links["asl_playlist"], r"^https://www\.youtube\.com/playlist\?list=PL[\w-]+$")
        for k in ("aa_big_book", "aa_twelve_and_twelve", "aa_accessibility_resources"):
            self.assertTrue(links[k].startswith("https://www.aa.org/"), k)
        self.assertRegex(links["aa_access_email"], r"^[\w.+-]+@aa\.org$")


class Page(unittest.TestCase):
    def test_page_and_footer_link(self):
        page = read("src", "pages", "accessibility.njk")
        self.assertIn("accessibility/index.html", page)
        self.assertIn("pagination: { data: languages", page)
        for anchor in ("settings", "captions", "asl", "audio", "phone", "print", "feedback"):
            self.assertIn(f'id="{anchor}"', page, anchor)
        nav = read("src", "_data", "nav.js")
        self.assertRegex(nav, r'key: "nav\.accessibility", url: "/accessibility/"')

    def test_meetings_page_points_to_phone_section(self):
        meetings = read("src", "pages", "meetings.njk")
        self.assertGreaterEqual(meetings.count("#phone"), 3)
        self.assertIn('"access.join_by_phone"', meetings)


if __name__ == "__main__":
    unittest.main()
