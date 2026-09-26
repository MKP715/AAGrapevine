"""Event colours by kind, and the Published writers "see more" rule.

  * eventTone (eleventy/filters/event-tone.js) — the one rule every event list uses for its colours
    (/events/, the home page's "Upcoming events", the monthly toolkit, Announcements "Coming up"):
    committee meeting → booth → assembly → La Viña / Grapevine named in the title (the first one named
    wins) → the event's own Grapevine / La Viña calendar → other. Checked on data items, /events/
    events and monthly date rows, in English and Spanish, plus the kinds the repository's events use.
  * pwView (eleventy/filters/published.js) — each group shows its 12 most recent stories; the rest of
    the default view is marked `over` (listed without JavaScript, "see more" with it); when Area 65 is
    all shown and the longest period holds more of it, the page offers that period.

Runs the JavaScript with Node.js (tests/nodejs.py); skipped without Node.js / the npm packages.

    python -m unittest tests.test_event_tone -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodejs import run_js  # noqa: E402

TONE_JS = """
const { eventTone, eventToneSet, EVENT_TONES } = await imp("eleventy/filters/event-tone.js");
out({
  tones: input.cases.map((c) => eventTone(c)),
  set: eventToneSet(input.cases),
  order: EVENT_TONES,
  junk: [eventTone(null), eventTone(undefined), eventTone("x"), eventTone({})],
  filter: typeof filters.eventTone === "function" && typeof filters.eventToneSet === "function",
  data: [...new Set(JSON.parse(fs.readFileSync("data/site/events.json", "utf8")).items
    .filter((it) => it && it.kind === "event").map((it) => it.title + " => " + eventTone(it)))],
});
"""

# (event, expected kind)
CASES: list[tuple[dict, str]] = [
    # 1. the committee's own meeting — by flag / category / monthly kind / id, before any title rule
    ({"committee": True, "title": "NETA 65 Grapevine & La Viña Committee Meeting"}, "committee"),
    ({"category": "committee", "title": "Reunión del Comité de Grapevine y La Viña de NETA 65"}, "committee"),
    ({"kind": "committee", "title": ""}, "committee"),
    ({"id": "ev:committee:2026-10-22", "title": ""}, "committee"),
    ({"category": "manual", "title": "Special committee meeting — Grapevine"}, "committee"),
    ({"category": "manual", "title": "Reunión del comité de La Viña"}, "committee"),
    # 2. booth — before the GV / LV names it carries
    ({"category": "recurring", "title": "GV/LV booth at CityWide Dallas"}, "booth"),
    ({"kind": "recurring", "title": "Mesa de GV/LV en CityWide Dallas"}, "booth"),
    ({"title": "Grapevine literature table at the Round-Up"}, "booth"),
    ({"title": "Round table on sponsorship"}, "other"),
    ({"title": "Mesa redonda de servicio"}, "other"),
    # 3. assembly
    ({"category": "manual", "title": "NETA 65 Spring Assembly 2027"}, "assembly"),
    ({"title": "Asamblea de Otoño 2027 de NETA 65"}, "assembly"),
    ({"title": "District 12 Pre-Assembly"}, "assembly"),
    # 4. La Viña / Grapevine by title (accents and case ignored; the first one named wins)
    ({"title": "La Viña Writing Workshop (in Spanish) — Fort Worth"}, "lv"),
    ({"title": "Taller de escritura de LA VINA"}, "lv"),
    ({"title": "Grapevine Writing Workshop — Arlington"}, "gv"),
    ({"title": "Taller de Grapevine y La Viña"}, "gv"),
    ({"title": "La Viña & Grapevine night"}, "lv"),
    # the display title first, then the original and its translations
    ({"title": "Taller mensual", "item": {"title": "Monthly La Viña workshop"}}, "lv"),
    ({"title": "Monthly workshop", "i18n": {"title": {"es": "Taller de Grapevine"}}}, "gv"),
    # 5. an event from Grapevine's / La Viña's own calendar
    ({"category": "lv-calendar", "title": "Taller Mensual"}, "lv"),
    ({"category": "gv-calendar", "title": "Weekly Open"}, "gv"),
    ({"item": {"category": "lv-calendar", "title": "Taller"}, "title": "Workshop"}, "lv"),
    # 6. anything else
    ({"category": "flyer", "title": "Unity Day 2027"}, "other"),
    ({"title": "Timetable review"}, "other"),
]


class EventToneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = None

    def result(self):
        if EventToneTest.res is None:
            EventToneTest.res = run_js(self, TONE_JS, data={"cases": [c for c, _ in CASES]})
        return EventToneTest.res

    def test_rules(self):
        got = self.result()["tones"]
        for (case, want), have in zip(CASES, got):
            with self.subTest(case=case):
                self.assertEqual(have, want)

    def test_set_is_in_key_order_and_only_present_kinds(self):
        r = self.result()
        self.assertEqual(r["order"], ["committee", "gv", "lv", "booth", "assembly", "other"])
        self.assertEqual(r["set"], [k for k in r["order"] if k in {w for _, w in CASES}])

    def test_junk_is_other_and_filters_registered(self):
        r = self.result()
        self.assertEqual(r["junk"], ["other"] * 4)
        self.assertTrue(r["filter"])

    def test_repository_events(self):
        rows = self.result()["data"]
        for row in rows:
            title, kind = row.rsplit(" => ", 1)
            low = title.lower()
            with self.subTest(title=title):
                if "committee meeting" in low:
                    self.assertEqual(kind, "committee")
                elif "booth" in low:
                    self.assertEqual(kind, "booth")
                elif "assembly" in low:
                    self.assertEqual(kind, "assembly")
                elif "la viña" in low:
                    self.assertEqual(kind, "lv")
                elif "grapevine" in low:
                    self.assertEqual(kind, "gv")


PW_JS = """
const v = filters.pwView({ spotlight: input.spotlight }, "en");
out({
  limit: v.limit, defDays: v.defDays, defScope: v.defScope,
  groups: v.groups.map((g) => ({ key: g.key, matchCount: g.matchCount, shownCount: g.shownCount,
    hiddenByLimit: g.hiddenByLimit, widen: g.widen,
    visible: g.items.filter((i) => i.visible).map((i) => i.date),
    over: g.items.filter((i) => i.over).length })),
});
"""
TODAY = "2026-09-26"


def story(n: int, scope: str, date: str) -> dict:
    return {"id": f"s{n}", "kind": "article", "source": "grapevine", "category": "gv", "lang": "en",
            "url": f"https://www.aagrapevine.org/story/{n}", "title": f"Story {n}", "date": date,
            "extra": {"pub_date": date, "author": f"Writer {n}", "geo": {"scope": scope, "label_en": "Dallas, Texas"}}}


def spotlight(items: list[dict]) -> dict:
    return {"updated": TODAY + "T10:00:00Z", "list_days": [60, 90], "default_scope": "neta65", "items": items}


class PublishedLimitTest(unittest.TestCase):
    def view(self, items):
        return run_js(self, PW_JS, data={"spotlight": spotlight(items)}, env={"PW_TODAY": TODAY})

    def group(self, v, key):
        return next(g for g in v["groups"] if g["key"] == key)

    def test_area_highlight_is_the_12_most_recent(self):
        # 15 Area 65 stories in the default 60 days (Sep 1..15) + 20 from elsewhere
        items = [story(i, "neta65", f"2026-09-{i:02d}") for i in range(1, 16)]
        items += [story(100 + i, "other", f"2026-09-{i:02d}") for i in range(1, 21)]
        v = self.view(items)
        self.assertEqual(v["limit"], 12)
        area = self.group(v, "neta65")
        self.assertEqual((area["matchCount"], area["shownCount"], area["hiddenByLimit"], area["over"]), (15, 12, 3, 3))
        self.assertEqual(area["visible"], [f"2026-09-{i:02d}" for i in range(15, 3, -1)])  # newest first
        self.assertIsNone(area["widen"])
        # outside the default scope: nothing visible, nothing "over" (the no-JS page shows the default view)
        other = self.group(v, "other")
        self.assertEqual((other["matchCount"], other["over"], len(other["visible"])), (0, 0, 0))

    def test_area_all_shown_offers_the_longer_period(self):
        items = [story(1, "neta65", "2026-09-01"), story(2, "neta65", "2026-08-01"),
                 story(3, "neta65", "2026-07-01"), story(4, "neta65", "2026-07-02")]
        area = self.group(self.view(items), "neta65")
        self.assertEqual((area["matchCount"], area["hiddenByLimit"]), (2, 0))
        self.assertEqual(area["widen"], {"days": 90, "n": 4})

    def test_no_widen_when_the_longer_period_adds_nothing(self):
        area = self.group(self.view([story(1, "neta65", "2026-09-01")]), "neta65")
        self.assertIsNone(area["widen"])


if __name__ == "__main__":
    unittest.main()
