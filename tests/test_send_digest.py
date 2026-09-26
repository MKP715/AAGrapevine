"""Monthly e-mail digest (scripts/notify/send_digest.py): the edition's month window (Central time,
January ← December, daylight saving), what each section holds, the subject, "nothing new → no
e-mail", and the --dry-run preview (HTML + plain text, English and Spanish halves) from a small
data/site folder written for each test — plus one run of the real command on the repository's data.

Run:  python -m unittest tests.test_send_digest -v   (CI: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.notify import send_digest as D  # noqa: E402

SITE = "https://example.org/site"
GV_PRODUCT = "https://www.aagrapevine.org/store/no-matter-what-dealing-adversity-sobriety"
LV_PRODUCT = "https://www.aalavina.org/tienda/frente-frente"
OCT1 = datetime(2026, 10, 1, 15, 5, tzinfo=timezone.utc)      # the October edition goes out (10:05 AM CDT)

CONFIG = """site:
  title: "Grapevine / La Viña"
  committee: "NETA 65 Grapevine & La Viña Committee"
  committee_es: "Comité de Grapevine y La Viña de NETA 65"
  url: "https://example.org/site"
  contact_email: "chair@example.org"
meeting:
  zoom_url: "https://zoom.us/j/123"
  meeting_id: "123 456"
  passcode: "abc"
  note: "All AA members are welcome."
  note_es: "Todos los miembros de AA son bienvenidos."
digest:
  highlights: 2
  per_section: 3
"""

CARRY = """ways:
  - id: newcomer
    icon: hand-heart
    title: { en: "Give it to a newcomer", es: "Dale un ejemplar a un recién llegado" }
    text: { en: "x", es: "x" }
  - id: group-topic
    icon: messages-square
    title: { en: "Bring it to your group", es: "Llévala a tu grupo" }
    text: { en: "x", es: "x" }
tips:
  "2026-10":
    - way: newcomer
      text: { en: "So many newcomers feel isolated.", es: "Muchos recién llegados se sienten aislados." }
    - way: nobody-knows-this-way
      text: { en: "dropped", es: "dropped" }
    - way: group-topic
      text: { en: "Members rarely raise this topic.", es: "Los miembros rara vez sacan este tema." }
"""


def offer(pub: str, **kw) -> dict:
    gv = pub == "gv"
    title_en = "No Matter What: Dealing With Adversity & Sobriety" if gv else "Face to Face: Sponsorship in Action"
    title_es = "No importa qué: la adversidad y la sobriedad" if gv else "Frente a Frente: El apadrinamiento en acción"
    b = {
        "id": f"botm:{pub}", "pub": pub, "lang": "en" if gv else "es",
        "title": title_en if gv else title_es,
        "url": GV_PRODUCT if gv else LV_PRODUCT,
        "image": None, "price": 14.99, "sale_price": 11.99, "discount_pct": 20, "currency": "USD",
        "starts": "2026-09-15", "ends": "2026-10-14",
        "i18n": {"title": {"en": title_en, "es": title_es}},
        "machine": ["es"] if gv else ["en"],
    }
    b.update(kw)
    return b


def item(iid: str, kind: str, source: str, date_: str | None, title: str, **kw) -> dict:
    it = {"id": iid, "kind": kind, "source": source, "date": date_, "first_seen": kw.pop("first_seen", date_),
          "title": title, "url": kw.pop("url", f"https://example.com/{iid}"), "lang": kw.pop("lang", "en"),
          "status": "ok", "category": kw.pop("category", None), "extra": kw.pop("extra", {}),
          "i18n": kw.pop("i18n", {"title": {"en": title, "es": f"ES {title}"}}), "machine": kw.pop("machine", [])}
    it.update(kw)
    return it


def article(iid: str, pub: str, key: str, title: str, **ex) -> dict:
    return item(iid, "article", "grapevine" if pub == "gv" else "lavina", "2026-09-20", title, category=pub,
                url=f"https://example.com/{iid}", extra={"publication": pub, "issue_key": key,
                                                         "issue_label": {"gv": "October 2026", "lv": "Septiembre / Octubre 2026"}[pub], **ex},
                i18n={"title": {"en": title, "es": f"ES {title}"},
                      "issue_label": {"en": {"gv": "October 2026", "lv": "September / October 2026"}[pub],
                                      "es": {"gv": "Octubre 2026", "lv": "Septiembre / Octubre 2026"}[pub]},
                      "issue_theme": {"en": {"gv": "Loneliness", "lv": "Service in AA"}[pub],
                                      "es": {"gv": "Soledad", "lv": "Servicio en AA"}[pub]}})


def event(iid: str, start: str, end: str | None, title: str, category: str = "manual", **ex) -> dict:
    return item(iid, "event", "committee", start, title, category=category,
                extra={"start": start, "end": end, **ex})


class DigestCase(unittest.TestCase):
    """Each test gets its own data/site folder, config/site.yml and config/carry.yml, and SITE_URL."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)
        self.site_dir = self.tmp_path / "site"
        self.site_dir.mkdir()
        (self.tmp_path / "site.yml").write_text(CONFIG, encoding="utf-8")
        (self.tmp_path / "carry.yml").write_text(CARRY, encoding="utf-8")
        for name in ("whatsnew", "events", "announcements"):
            self.write(name, {"items": []})
        for p in (mock.patch.object(D, "SITE_DIR", self.site_dir), mock.patch.object(D, "ASSET_DIR", self.tmp_path / "src"),
                  mock.patch.object(D, "CONFIG_PATH", self.tmp_path / "site.yml"),
                  mock.patch.object(D, "CARRY_PATH", self.tmp_path / "carry.yml"), mock.patch.dict(os.environ, {"SITE_URL": SITE})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def write(self, name: str, data: dict) -> None:
        (self.site_dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def preview(self, as_of: str = "2026-10-01", *extra: str) -> tuple[str, str]:
        out = self.tmp_path / "out"
        self.assertEqual(D.main(["--dry-run", "--as-of", as_of, "--out-dir", str(out), *extra]), 0)
        return (out / "digest.html").read_text(encoding="utf-8"), (out / "digest.txt").read_text(encoding="utf-8")

    @staticmethod
    def halves(text: str) -> tuple[str, str]:
        """The plain text's English half and Spanish half."""
        i = text.index("Resumen mensual — Edición de")
        return text[:i], text[i:]

    def full_month(self) -> None:
        """A realistic October 2026 edition (news from September)."""
        self.write("whatsnew", {"items": [
            dict(article("gv:a1", "gv", "2026-10", "A Halloween to Remember"), wn_date="2026-09-23T16:34:47Z"),
            item("ig:1", "post", "instagram", "2026-09-20T15:00:00Z", "A post", wn_date="2026-09-20T15:00:00Z"),
            dict(event("ev:new", "2026-10-03T19:00:00Z", None, "Workshop"), wn_date="2026-09-10T12:00:00Z"),
            item("pdf:1", "pdf", "crawl", "2026-09-15", "GV News October 2026", wn_date="2026-09-15T12:00:00Z",
                 extra={"host": "www.aagrapevine.org", "pages": 4}),
            item("drive:album:x", "photo", "drive", "2026-09-12", "5 new photos in Booth", wn_date="2026-09-12T12:00:00Z",
                 category="photos", extra={"album": "Booth", "count": 5, "is_group": True}),
            item("pdf:old", "pdf", "crawl", "2026-08-31", "August document", wn_date="2026-08-31T12:00:00Z"),
        ]})
        # the full lists: an episode (not in What's New any more) and its YouTube upload the same evening
        self.write("episodes", {"items": [
            item("pod:1", "episode", "podcast", "2026-09-21T04:15:00Z", "Gated Communities [Season 11, Episode 12]",
                 category="gv", extra={"season": 11, "episode": 12, "duration_sec": 1920}),
            item("pod:old", "episode", "podcast", "2026-08-31T04:15:00Z", "Two Way Prayer [Season 11, Episode 9]"),
        ]})
        self.write("videos", {"items": [
            item("yt:1", "video", "youtube", "2026-09-21T04:33:00Z", "Gated Communities [Season 11, Episode 12]",
                 url="https://www.youtube.com/watch?v=abc", extra={"season": 11, "episode": 12}),
            item("yt:2", "video", "youtube", "2026-09-08T13:29:00Z", "Date with higher power"),
            item("yt:undated", "video", "youtube", None, "No date"),
        ]})
        self.write("announcements", {"items": [
            item("ann:1", "announcement", "committee", "2026-09-05", "New GVR orientation", url="/announcements/#new",
                 extra={"body_md": "Join us **Saturday**."}),
            item("ann:gone", "announcement", "committee", "2026-09-06", "Expired", extra={"expires": "2026-09-30"}),
        ]})
        self.write("articles", {"issues": [
            {"id": "gv:2026-10", "publication": "gv", "key": "2026-10", "label": "October 2026", "theme": "Loneliness",
             "url": "https://www.aagrapevine.org/magazine-issue/october-2026", "cover": "/assets/cache/articles/gv.webp",
             "i18n": {"theme": {"en": "Loneliness", "es": "Soledad"}, "label": {"en": "October 2026", "es": "Octubre 2026"}}},
        ], "items": [
            article("gv:a1", "gv", "2026-10", "A Halloween to Remember", free=False, geo={"scope": "texas"}, author="Aaron M."),
            article("gv:a2", "gv", "2026-10", "At Wit's End", free=True, department=True),
            article("gv:a3", "gv", "2026-10", "When Loneliness Comes", free=True),
            article("gv:a4", "gv", "2026-10", "Who's That Girl?", free=False, geo={"scope": "other"}),
            article("lv:a1", "lv", "2026-09", "El despertar del espíritu", free=False, geo={"scope": "neta65"}),
            article("lv:a2", "lv", "2026-09", "Detenido", free=False),
        ]})
        self.write("editorial", {"items": [
            item("ed:gv:2026-10", "topic", "grapevine", "2026-05-01", "Dealing with Loneliness",
                 extra={"publication": "gv", "issue_key": "2026-10", "deadline": "2026-05-01"},
                 i18n={"title": {"en": "Dealing with Loneliness", "es": "Tratar con la soledad"}}),
            item("ed:gv:2027-05", "topic", "grapevine", "2026-10-01", "Fun in Sobriety",
                 extra={"publication": "gv", "issue_key": "2027-05", "issue_label": "May 2027", "deadline": "2026-10-01"}),
            item("ed:gv:2027-06", "topic", "grapevine", "2026-11-30", "Emotional Sobriety",
                 extra={"publication": "gv", "issue_key": "2027-06", "issue_label": "June 2027", "deadline": "2026-11-30"}),
            item("ed:gv:2027-07", "topic", "grapevine", "2026-12-01", "Prison Issue",
                 extra={"publication": "gv", "issue_key": "2027-07", "issue_label": "July 2027", "deadline": "2026-12-01"}),
            item("ed:gv:2027-04", "topic", "grapevine", "2026-09-01", "Closed", extra={"publication": "gv", "deadline": "2026-09-01"}),
        ] + [item(f"ed:lv:t{i}", "topic", "lavina", None, f"Tema {i}", lang="es", extra={"publication": "lv", "evergreen": True},
                  i18n={"title": {"en": f"Topic {i}", "es": f"Tema {i}"}}) for i in range(5)]})
        self.write("spotlight", {"items": [
            item("sp:1", "article", "grapevine", "2026-09-23", "A Halloween to Remember", category="gv",
                 extra={"publication": "gv", "pub_date": "2026-09-23", "author": "Aaron M.", "issue_label": "October 2026",
                        "geo": {"scope": "texas", "label_en": "Round Rock, Texas", "label_es": "Round Rock, Texas"}}),
            item("sp:2", "article", "lavina", "2026-09-01", "El despertar", category="lv",
                 extra={"publication": "lv", "pub_date": "2026-09-01", "author": "Victor R.",
                        "geo": {"scope": "neta65", "label_en": "Grand Prairie, Texas"}}),
            item("sp:aug", "article", "grapevine", "2026-08-31", "August story", extra={"pub_date": "2026-08-31", "geo": {"scope": "neta65"}}),
            item("sp:far", "article", "grapevine", "2026-09-10", "Far away", extra={"pub_date": "2026-09-10", "geo": {"scope": "other"}}),
        ]})
        self.write("events", {"items": [
            event("ev:committee:2026-10-21", "2026-10-22T00:00:00Z", "2026-10-22T01:00:00Z", "Committee meeting", "committee",
                  online_url="https://zoom.us/j/123"),
            event("ev:done", "2026-10-01T13:00:00Z", "2026-10-01T14:00:00Z", "Earlier this morning"),
            event("ev:ws", "2026-10-03T19:00:00Z", "2026-10-03T22:00:00Z", "Grapevine Writing Workshop", location="Arlington"),
            event("ev:rec:2026-10-10", "2026-10-10T22:00:00Z", "2026-10-11T01:00:00Z", "Booth", "recurring", series="citywide"),
            event("ev:rec:2026-11-14", "2026-11-14T23:00:00Z", "2026-11-15T02:00:00Z", "Booth", "recurring", series="citywide"),
            event("ev:assembly", "2026-10-30", "2026-11-01", "Fall Assembly", all_day=True, tentative=True),
            event("ev:nov", "2026-11-07T01:00:00Z", None, "November workshop"),
        ]})
        self.write("weekly_open", {"items": [
            item("weekly_open", "meeting", "grapevine", None, "Grapevine Weekly Open AA Meeting",
                 i18n={"title": {"en": "Grapevine Weekly Open AA Meeting", "es": "Grapevine Weekly Open AA Meeting"},
                       "when": {"en": "Wednesdays at 11:00 AM Central", "es": "Los miércoles a las 11:00 a. m. (hora del Centro)"}}),
            item("weekly_open_lv", "meeting", "lavina", None, "Reunión Abierta de La Viña", extra={"starts": "2026-11-05"},
                 i18n={"title": {"en": "La Viña Open Meeting", "es": "Reunión Abierta de La Viña"}, "when": {"en": "Thursdays", "es": "Los jueves"}}),
        ]})
        self.write("meetings", {"items": [{"id": f"m{i}", "day": i % 7, "in_area": i < 3, "attendance": "in_person"} for i in range(5)]
                   + [{"id": "inactive", "day": 1, "in_area": True, "attendance": "inactive"}]})
        self.write("audio_project", {"gv": {"phone": "(559) 726-1216", "tel": "+15597261216"},
                                     "lv": {"phone": "(559) 670-1601", "tel": "+15596701601"}})
        self.write("quote", {"items": [{"id": "q", "text": "One day at a time."}]})
        self.write("instagram", {"profiles": {"gv": {"username": "alcoholicsanonymous_gv"}, "lv": {"username": "alcoholicosanonimos_lv"}}})
        self.write("shop", {"botm": [offer("gv"), offer("lv")], "subscriptions": [
            {"pub": "gv", "plans": [{"type": "print", "term_months": 12, "price": 24.0},     # $2 a month, but for a year
                                    {"type": "complete", "term_months": 1, "price": 6.0},
                                    {"type": "digital", "term_months": 1, "price": 2.99}]}]})


class EditionWindow(DigestCase):
    def test_the_window_is_the_previous_calendar_month(self):
        ed = D.edition_of(OCT1)
        self.assertEqual((ed["key"], ed["prev"], ed["next"]), ("2026-10", "2026-09", "2026-11"))
        self.assertEqual((ed["prev_first"], ed["prev_last"]), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual((ed["first"], ed["last"], ed["next_last"]), (date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 30)))

    def test_january_edition_looks_back_at_december(self):
        ed = D.edition_of(datetime(2027, 1, 1, 15, 5, tzinfo=timezone.utc))
        self.assertEqual((ed["key"], ed["prev"]), ("2027-01", "2026-12"))
        self.assertEqual((ed["prev_first"], ed["prev_last"]), (date(2026, 12, 1), date(2026, 12, 31)))
        self.assertEqual(ed["next_last"], date(2027, 2, 28))
        self.assertEqual(D.edition_of(OCT1, "2027-01")["prev"], "2026-12")      # --month 2027-01
        self.assertEqual(D.edition_of(OCT1, "2027-13")["key"], "2026-10")       # not a month → today's edition

    def test_the_month_is_counted_in_central_time(self):
        utc = timezone.utc
        # 03:00 UTC on October 1 is still the evening of September 30 in Texas (CDT)
        self.assertEqual(D.edition_of(datetime(2026, 10, 1, 3, tzinfo=utc))["key"], "2026-09")
        self.assertEqual(D.edition_of(datetime(2026, 10, 1, 6, tzinfo=utc))["key"], "2026-10")
        # November 1, 2026 is the day daylight saving time ends (2 AM): midnight is still CDT (UTC−5)
        self.assertEqual(D.edition_of(datetime(2026, 11, 1, 4, 30, tzinfo=utc))["key"], "2026-10")
        self.assertEqual(D.edition_of(datetime(2026, 11, 1, 5, 30, tzinfo=utc))["key"], "2026-11")
        # December 1 in CST (UTC−6): 05:30 UTC is still November 30
        self.assertEqual(D.edition_of(datetime(2026, 12, 1, 5, 30, tzinfo=utc))["key"], "2026-11")
        self.assertEqual(D.edition_of(datetime(2026, 12, 1, 6, 30, tzinfo=utc))["key"], "2026-12")
        # the 1st at the scheduled 15:05 UTC is the 1st in both CST (9:05 AM) and CDT (10:05 AM)
        for m in range(1, 13):
            self.assertEqual(D.edition_of(datetime(2027, m, 1, 15, 5, tzinfo=utc))["key"], f"2027-{m:02d}")

    def test_news_on_the_edges_of_the_month_across_daylight_saving(self):
        self.write("whatsnew", {"items": [
            item("a", "pdf", "crawl", None, "Oct 31, 11:30 PM CDT", wn_date="2026-11-01T04:30:00Z"),     # October
            item("b", "pdf", "crawl", None, "Nov 1, 12:30 AM CDT", wn_date="2026-11-01T05:30:00Z"),      # November
            item("c", "pdf", "crawl", None, "Nov 30, 11:30 PM CST", wn_date="2026-12-01T05:30:00Z"),     # November
            item("d", "pdf", "crawl", None, "Dec 1, 12:30 AM CST", wn_date="2026-12-01T06:30:00Z"),      # December
            item("e", "pdf", "crawl", None, "Dec 31, 11 PM CST", wn_date="2027-01-01T05:00:00Z"),        # December
            item("f", "pdf", "crawl", None, "a date-only value", wn_date="2026-12-31"),                  # December
        ]})
        ids = lambda now: [i["id"] for i in D.collect(now)["groups"]["pdf"]]  # noqa: E731
        self.assertEqual(ids(datetime(2026, 11, 1, 15, 5, tzinfo=timezone.utc)), ["a"])
        self.assertEqual(sorted(ids(datetime(2026, 12, 1, 15, 5, tzinfo=timezone.utc))), ["b", "c"])
        self.assertEqual(sorted(ids(datetime(2027, 1, 1, 15, 5, tzinfo=timezone.utc))), ["d", "e", "f"])


class Sections(DigestCase):
    def setUp(self):
        super().setUp()
        self.full_month()
        self.data = D.collect(OCT1, None, 3, 2)

    def test_last_months_news(self):
        g = self.data["groups"]
        self.assertEqual([i["id"] for i in g["article"]], ["gv:a1"])          # from What's New
        self.assertEqual([i["id"] for i in g["pdf"]], ["pdf:1"])              # not the August one
        self.assertEqual([i["id"] for i in g["drive"]], ["drive:album:x"])
        self.assertEqual([i["id"] for i in g["announcement"]], ["ann:1"])     # the expired one is left out
        # the episode comes from the full list (What's New no longer has it) with its YouTube upload folded in
        self.assertEqual([i["id"] for i in g["episode"]], ["pod:1"])
        self.assertEqual(g["episode"][0]["_twin"]["id"], "yt:1")
        self.assertEqual([i["id"] for i in g["video"]], ["yt:2"])             # undated → never news
        self.assertNotIn("post", g)                                           # Instagram: a pointer line only
        self.assertEqual(self.data["instagram"], ["alcoholicsanonymous_gv", "alcoholicosanonimos_lv"])
        self.assertEqual(D.total_count(self.data), 6 + 2)                     # 6 news + 2 writers
        self.assertEqual(D.count_list(self.data, "en"),
                         "1 magazine story, 1 podcast episode, 1 video, 1 document, 1 committee file and 1 announcement")
        self.assertEqual(D.count_list(self.data, "es"),
                         "1 historia de las revistas, 1 episodio de podcast, 1 video, 1 documento, 1 archivo del comité y 1 anuncio")

    def test_this_months_issues_highlights_and_tips(self):
        gv, lv = self.data["issues"]
        self.assertEqual((gv["key"], gv["count"], gv["free"], gv["cover"]), ("2026-10", 4, 2, "/assets/cache/articles/gv.webp"))
        # the theme of this month's Grapevine is the issue's own once it is out (like /monthly/, Read
        # and the district report) — not the editorial calendar's call for stories
        self.assertEqual(gv["theme"], {"en": "Loneliness", "es": "Soledad"})
        # free to read first, members' stories before "In Every Issue" pages, Texas writers first
        self.assertEqual([a["id"] for a in gv["highlights"]], ["gv:a3", "gv:a2"])
        # La Viña's bimonthly issue that began last month is still this month's
        self.assertEqual((lv["key"], lv["label"]["en"], lv["theme"]["es"]), ("2026-09", "September / October 2026", "Servicio en AA"))
        self.assertEqual([a["id"] for a in lv["highlights"]], ["lv:a1", "lv:a2"])   # Area 65 writer first
        self.assertEqual([t["title"] for t in self.data["tips"]["en"]], ["Give it to a newcomer", "Bring it to your group"])
        self.assertEqual(self.data["tips"]["es"][1]["text"], "Los miembros rara vez sacan este tema.")

    def test_writers_published_last_month(self):
        w = self.data["writers"]
        self.assertEqual([i["id"] for i in w["neta65"]], ["sp:2"])            # not the August story
        self.assertEqual([i["id"] for i in w["texas"]], ["sp:1"])

    def test_coming_up_this_month(self):
        # not over yet, this month only, the monthly booth once, the committee meeting in its own box
        self.assertEqual([e["id"] for e in self.data["events"]], ["ev:ws", "ev:rec:2026-10-10", "ev:assembly"])
        self.assertEqual(self.data["meeting"]["id"], "ev:committee:2026-10-21")
        self.assertEqual([w["title"] for w in self.data["weekly"]["en"]], ["Grapevine Weekly Open AA Meeting"])  # LV starts in November
        self.assertEqual(self.data["gvm"], {"in_area": 3, "nearby": 2})
        self.assertEqual(D.gvm_text(self.data["gvm"], "en"), "3 meetings every week in our Area, plus 2 in nearby areas")
        self.assertEqual(D.gvm_text({"in_area": 1, "nearby": 0}, "es"), "1 reunión cada semana en nuestra Área")

    def test_share_your_story_and_the_extras(self):
        # deadlines from today through the end of next month (Oct 1 – Nov 30)
        self.assertEqual([d["id"] for d in self.data["deadlines"]], ["ed:gv:2027-05", "ed:gv:2027-06"])
        self.assertEqual(len(self.data["lv_topics"]["en"]), 3)
        self.assertEqual(set(self.data["audio"]), {"gv", "lv"})
        self.assertEqual(self.data["subs_from"], 2.99)
        self.assertTrue(self.data["quote"])

    def test_subject(self):
        # the edition's name in both languages (its news is from the month before)
        self.assertEqual(D.subject_of(self.data, D.load_config()),
                         "Grapevine / La Viña — October 2026 edition · Edición de octubre de 2026")
        jan = D.collect(datetime(2027, 1, 1, 15, 5, tzinfo=timezone.utc))
        self.assertEqual(D.subject_of(jan, {}), "Grapevine / La Viña — January 2027 edition · Edición de enero de 2027")

    def test_dry_run_preview_in_both_languages(self):
        html, text = self.preview()
        en, es = self.halves(text)
        for line in ("Monthly Digest — October 2026 edition", "What's new in September · Coming up in October",
                     "In September: 1 magazine story, 1 podcast episode", "NEXT COMMITTEE MEETING: Wed, Oct 21, 2026 · 7:00 PM CDT",
                     "THIS MONTH IN THE MAGAZINES", "* Grapevine — October 2026: “Loneliness” (4 stories · 2 free to read)",
                     "PUT IT TO WORK", f"This month's toolkit (October 2026): {SITE}/monthly/2026-10/",
                     "WRITERS FROM AREA 65 & TEXAS (2)", "PODCASTS (1)", "* [Podcast] Gated Communities (S11 · E12 · 32 min · Sep 20)",
                     "also on YouTube: https://www.youtube.com/watch?v=abc", "COMMITTEE UPLOADS (1)", "Photos: Booth (5 new photos)",
                     "COMING UP IN OCTOBER", "* Every week: Grapevine Weekly Open AA Meeting — Wednesdays at 11:00 AM Central",
                     "* Grapevine meetings near you: 3 meetings every week in our Area, plus 2 in nearby areas",
                     "SHARE YOUR STORY — UPCOMING DEADLINES", "* Due October 1 — “Fun in Sobriety” (Grapevine, May 2027)",
                     "* Record your story by phone: Grapevine (559) 726-1216 · La Viña (559) 670-1601",
                     f"Month-to-month subscriptions from $2.99 — {SITE}/shop/#subscriptions",
                     "A daily quote from Grapevine and La Viña, on our home page",
                     "Grapevine and La Viña on Instagram: @alcoholicsanonymous_gv · @alcoholicosanonimos_lv"):
            self.assertIn(line, en)
        self.assertNotIn("Earlier this morning", text)
        self.assertNotIn("November workshop", text)
        self.assertNotIn("Two Way Prayer", text)
        # Spanish half: La Viña first, Spanish words, the Spanish meeting note, 12-hour time like the site
        for line in ("Resumen mensual — Edición de octubre de 2026", "Novedades de septiembre · Lo que viene en octubre",
                     "En septiembre: 1 historia de las revistas", "ESTE MES EN LAS REVISTAS", "PONLA A TRABAJAR",
                     f"El kit de este mes (octubre de 2026): {SITE}/es/monthly/2026-10/", "LO QUE VIENE EN OCTUBRE",
                     "Todos los miembros de AA son bienvenidos.", "7:00 p. m. (hora del Centro)", "Graba tu historia por teléfono",
                     "Suscripciones mes a mes desde $2.99", "* Fecha límite: 1 de octubre — “ES Fun in Sobriety” (Grapevine, mayo de 2027)",
                     "* Cada semana: Grapevine Weekly Open AA Meeting (en inglés) — los miércoles a las 11:00 a. m. (hora del Centro)",
                     "sin fecha límite. Ideas para este mes:"):
            self.assertIn(line, es)
        # Spanish months inside a line: lower-case, with "de"; the other magazine's language said once
        self.assertLess(es.index("* La Viña — septiembre/octubre de 2026: “Servicio en AA”"),
                        es.index("* Grapevine — octubre de 2026 (en inglés): “Soledad”"))
        self.assertLess(en.index("* Grapevine — October 2026: "), en.index("* La Viña — September / October 2026 (in Spanish): "))
        self.assertIn("(Grapevine, octubre de 2026, en inglés)", es)                  # a Texas writer's story
        self.assertIn("— Victor R., Grand Prairie, Texas (La Viña, in Spanish)", en)
        self.assertNotIn("CDT", es)
        self.assertNotRegex(es, r"(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre|Noviembre|Diciembre) \d{4}")
        # HTML: both halves, escaped, the Spanish cells marked lang="es", tap-to-call, absolute cover
        self.assertIn("<title>Grapevine / La Viña — October 2026 edition · Edición de octubre de 2026</title>", html)
        body = html.split("</head>", 1)[1]
        self.assertEqual(body.count("October 2026 edition"), 1)
        self.assertEqual(body.count("Edición de octubre de 2026"), 1)
        self.assertIn('lang="es"', html)
        self.assertIn('href="tel:+15597261216"', html)
        # classic Outlook for Windows shows no WebP: without a JPEG / PNG copy the cover is left out
        self.assertNotIn(".webp", html)
        self.assertIn("At Wit&#x27;s End", html)
        self.assertIn(f'href="{SITE}/es/meetings/#grapevine-meetings"', html)
        self.assertIn("May 2027 issue</span> · ", html)                                    # the deadline rows
        self.assertIn("Edición de mayo de 2027</span> · ", html)
        self.assertIn("(en inglés)", html)

    def test_covers_go_out_as_jpeg(self):
        # the JPEG copy made next to a cover (scripts/sync/articles.py email_copy) is what the e-mail shows
        jpg = self.tmp_path / "src" / "assets" / "cache" / "articles" / "gv.jpg"
        jpg.parent.mkdir(parents=True)
        jpg.write_bytes(b"\xff\xd8\xff\xd9")
        html, _ = self.preview()
        self.assertIn(f'src="{SITE}/assets/cache/articles/gv.jpg"', html)
        self.assertNotIn(".webp", html)
        self.assertEqual(D.email_image("https://example.org/x.webp"), "")
        self.assertEqual(D.email_image("/assets/img/logo.png"), "/assets/img/logo.png")

    def test_nothing_new_means_no_email(self):
        for name in ("whatsnew", "episodes", "videos", "announcements", "spotlight"):
            self.write(name, {"items": []})
        data = D.collect(OCT1)
        self.assertEqual(D.total_count(data), 0)     # the issues, dates and Book of the Month alone are not news
        self.assertTrue(data["issues"] and data["events"] and data["botm"])
        with mock.patch.dict(os.environ, {"SMTP_SERVER": "", "DIGEST_TO": ""}):
            self.assertEqual(D.main(["--as-of", "2026-10-01"]), 0)                # nothing to send
        self.full_month()
        with mock.patch.dict(os.environ, {"SMTP_SERVER": "", "DIGEST_TO": ""}):
            self.assertEqual(D.main(["--as-of", "2026-10-01"]), 2)                # news, but e-mail not set up
        _, text = self.preview("2026-10-01", "--month", "2026-11")
        self.assertIn("Monthly Digest — November 2026 edition", text)            # --month previews another edition
        self.assertIn("A quiet October on the site", text)


class BookOfTheMonth(DigestCase):
    def test_botm_teaser_in_both_languages(self):
        self.write("shop", {"botm": [offer("gv"), offer("lv")]})
        html, text = self.preview("2026-09-28")
        en, es = self.halves(text)
        self.assertIn("BOOK OF THE MONTH — 20% OFF", en)
        self.assertIn("* [Grapevine] No Matter What: Dealing With Adversity & Sobriety — $11.99 (regular $14.99) · until October 14", en)
        self.assertLess(en.index("[Grapevine] No Matter"), en.index("[La Viña] Frente a Frente"))   # the title it is sold under
        self.assertIn(f"→ Book of the Month details on our shop page: {SITE}/shop/#botm", en)
        self.assertIn(f"This month's toolkit (September 2026): {SITE}/monthly/2026-09/", en)
        self.assertIn("LIBRO DEL MES — 20% DE DESCUENTO", es)
        self.assertIn("* [La Viña] Frente a Frente: El apadrinamiento en acción — $11.99 (precio regular $14.99) · hasta el 14 de octubre", es)
        self.assertLess(es.index("[La Viña] Frente"), es.index("[Grapevine] No Matter What"))
        self.assertIn(f"El kit de este mes (septiembre de 2026): {SITE}/es/monthly/2026-09/", es)
        self.assertEqual(html.count("Book of the Month — 20% off"), 1)
        self.assertEqual(html.count("Libro del mes — 20% de descuento"), 1)
        self.assertIn("Dealing With Adversity &amp; Sobriety", html)
        self.assertNotIn("Adversity & Sobriety", html)
        self.assertIn(f'href="{GV_PRODUCT}"', html)
        self.assertIn("$11.99</strong> (regular $14.99) · until October 14", html)
        # Book titles are never machine-translated, so they bring no "translated automatically" footnote
        self.assertNotIn("Some titles were translated automatically.", en)

    def test_ended_offer_is_left_out_and_percents_can_differ(self):
        self.write("shop", {"botm": [offer("gv", ends="2026-09-27"), offer("lv", discount_pct=15, sale_price=12.74)]})
        html, text = self.preview("2026-09-28")          # the GV offer ended the day before
        self.assertNotIn(GV_PRODUCT, text + html)
        self.assertIn("BOOK OF THE MONTH — 15% OFF", text)
        self.write("shop", {"botm": [offer("gv"), offer("lv", discount_pct=15, sale_price=12.74)]})
        _, text = self.preview("2026-09-28")
        en, es = self.halves(text)
        self.assertIn("BOOK OF THE MONTH\n-----------------\n", en)
        self.assertNotIn("% OFF", en)
        self.assertIn("LIBRO DEL MES\n-------------\n", es)

    def test_no_or_broken_shop_data_keeps_the_toolkit_line(self):
        html, text = self.preview("2026-09-28")          # no shop.json at all
        self.assertNotIn("BOOK OF THE MONTH", text)
        self.assertIn(f"{SITE}/monthly/2026-09/", text)
        self.assertIn(f'href="{SITE}/es/monthly/2026-09/"', html)
        (self.site_dir / "shop.json").write_text("{not json", encoding="utf-8")   # a broken file never stops the digest
        _, text = self.preview("2026-09-28")
        self.assertIn("/monthly/2026-09/", text)

    def test_toolkit_month_is_the_editions_month(self):
        data = D.collect(datetime(2026, 10, 1, 3, tzinfo=timezone.utc))      # still September 30 in Texas
        self.assertEqual(data["month"], "2026-09")
        self.assertEqual(D.botm_block(data, "es", D.Links(SITE))["month_url"], f"{SITE}/es/monthly/2026-09/")


class Wording(unittest.TestCase):
    """Small wording rules of the e-mail (both halves)."""

    def test_page_counts(self):
        doc = lambda n: item("pdf:x", "pdf", "crawl", "2026-09-01", "Form", extra={"pages": n, "host": "www.aalavina.org"})  # noqa: E731
        self.assertEqual(D.item_meta(doc(1), "en"), "1 page · aalavina.org")
        self.assertEqual(D.item_meta(doc(1), "es"), "1 página · aalavina.org")
        self.assertEqual(D.item_meta(doc(4), "en"), "4 pages · aalavina.org")
        self.assertEqual(D.item_meta(doc(4), "es"), "4 páginas · aalavina.org")

    def test_spanish_months_inside_a_line(self):
        self.assertEqual(D.in_sentence("Septiembre / Octubre 2026", "es"), "septiembre/octubre de 2026")
        self.assertEqual(D.in_sentence("Octubre 2026", "es"), "octubre de 2026")
        self.assertEqual(D.in_sentence("octubre de 2026", "es"), "octubre de 2026")        # already right
        self.assertEqual(D.in_sentence("September / October 2026", "en"), "September / October 2026")
        self.assertEqual(D.in_sentence("Edición especial", "es"), "Edición especial")       # not a month label
        art = {"kind": "article", "extra": {"issue_label": "May 2027"}}
        self.assertEqual(D.issue_label(art, "es"), "mayo de 2027")                         # no i18n: the local rule
        self.assertEqual(D.issue_label(art, "en"), "May 2027")
        art["i18n"] = {"issue_label": {"en": "May 2027", "es": "Mayo 2027"}}
        self.assertEqual(D.issue_label(art, "es"), "mayo de 2027")

    def test_times_and_the_weekly_open_pill(self):
        t = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)                             # 7 PM CDT
        self.assertEqual(D.fmt_time(t, "en"), "7:00 PM CDT")
        self.assertEqual(D.fmt_time(t, "es"), "7:00 p. m. (hora del Centro)")
        self.assertEqual(D.fmt_time(datetime(2026, 12, 17, 1, 0, tzinfo=timezone.utc), "en"), "7:00 PM CST")
        wo = item("pod:wo", "episode", "podcast", "2026-08-27", "Grapevine Weekly Open AA Meeting", category="wo")
        self.assertEqual(D.item_label(wo, "en")[0], "Weekly Open")
        self.assertEqual(D.item_label(wo, "es")[0], "Reunión Abierta Semanal")
        self.assertEqual(D.item_label(dict(wo, category="gv"), "es")[0], "Podcast")


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL and records what the digest asks of the mail server.
    Each test makes its own subclass (Sending.make_server), so the settings and the log are its own."""
    offers_starttls = True
    connect_errors: list = []       # raised by the next connections, in order
    login_error: Exception | None = None
    send_error: Exception | None = None
    log: list = []

    def __init__(self, host, port, timeout=None, context=None):
        cls = type(self)
        if cls.connect_errors:
            raise cls.connect_errors.pop(0)
        cls.log.append(("connect", host, port))
        self.encrypted = bool(context)                     # only SMTP_SSL is given the TLS context here

    def ehlo(self):
        type(self).log.append(("ehlo",))

    def has_extn(self, name):
        return name.lower() == "starttls" and type(self).offers_starttls

    def starttls(self, context=None):
        self.encrypted = True
        type(self).log.append(("starttls",))

    def login(self, user, password):
        type(self).log.append(("login", "encrypted" if self.encrypted else "PLAIN TEXT"))
        if type(self).login_error:
            raise type(self).login_error

    def send_message(self, msg, from_addr=None, to_addrs=None):
        type(self).log.append(("send", tuple(to_addrs or ())))
        if type(self).send_error:
            raise type(self).send_error
        return {}

    def quit(self):
        type(self).log.append(("quit",))

    def close(self):
        pass


class Sending(unittest.TestCase):
    """send(): the password only over an encrypted connection; retries only before the message goes."""

    def setUp(self):
        env = {"SMTP_SERVER": "smtp.example.org", "SMTP_PORT": "587", "SMTP_USERNAME": "digest@example.org",
               "SMTP_PASSWORD": "app-password"}
        for p in (mock.patch.dict(os.environ, env), mock.patch.object(D.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.msg = D.build_message("Subject", "<p>x</p>", "x", "digest@example.org", "Committee",
                                   ["a@example.org", "b@example.org"], "chair@example.org")

    def make_server(self, **kw):
        cls = type("Server", (FakeSMTP,), {"log": [], "connect_errors": list(kw.pop("connect_errors", [])), **kw})
        for name in ("SMTP", "SMTP_SSL"):
            p = mock.patch.object(D.smtplib, name, cls)
            p.start()
            self.addCleanup(p.stop)
        return cls

    @staticmethod
    def steps(server) -> list[str]:
        return [e[0] for e in server.log]

    def test_starttls_before_the_password(self):
        server = self.make_server()
        D.send(self.msg, "digest@example.org", ["a@example.org", "b@example.org"])
        self.assertEqual(self.steps(server), ["connect", "ehlo", "starttls", "ehlo", "login", "send", "quit"])
        self.assertIn(("login", "encrypted"), server.log)
        self.assertIn(("send", ("a@example.org", "b@example.org")), server.log)

    def test_no_starttls_means_no_password(self):
        server = self.make_server(offers_starttls=False)
        with self.assertRaises(RuntimeError) as cm:
            D.send(self.msg, "digest@example.org", ["a@example.org"])
        self.assertIn("does not offer STARTTLS", str(cm.exception))
        self.assertNotIn("login", self.steps(server))                    # the password never left
        self.assertNotIn("send", self.steps(server))
        self.assertEqual(self.steps(server).count("connect"), 1)        # a missing STARTTLS is not retried

    def test_port_465_is_ssl_from_the_start(self):
        with mock.patch.dict(os.environ, {"SMTP_PORT": "465"}):
            server = self.make_server(offers_starttls=False)
            D.send(self.msg, "digest@example.org", ["a@example.org"])
        self.assertEqual(self.steps(server), ["connect", "ehlo", "login", "send", "quit"])
        self.assertIn(("login", "encrypted"), server.log)

    def test_connection_trouble_is_retried_before_sending(self):
        server = self.make_server(connect_errors=[ConnectionRefusedError("refused"), TimeoutError("slow")])
        D.send(self.msg, "digest@example.org", ["a@example.org"])
        self.assertEqual(self.steps(server).count("send"), 1)
        self.make_server(connect_errors=[ConnectionRefusedError("x")] * 3)
        with self.assertRaises(RuntimeError) as cm:
            D.send(self.msg, "digest@example.org", ["a@example.org"])
        self.assertIn("Could not reach the mail server smtp.example.org:587", str(cm.exception))

    def test_a_failure_while_sending_is_never_retried(self):
        for err in (smtplib.SMTPServerDisconnected("gone"), TimeoutError("timed out"), ConnectionResetError("reset")):
            with self.subTest(error=type(err).__name__):
                server = self.make_server(send_error=err)
                with self.assertRaises(RuntimeError) as cm:
                    D.send(self.msg, "digest@example.org", ["a@example.org", "b@example.org"])
                self.assertIn("MAY have been sent", str(cm.exception))
                self.assertEqual(self.steps(server).count("send"), 1)   # one try: nobody gets it twice
                self.assertEqual(self.steps(server).count("connect"), 1)

    def test_refusals_are_reported(self):
        self.make_server(login_error=smtplib.SMTPAuthenticationError(535, b"bad"))
        with self.assertRaisesRegex(RuntimeError, "App Password"):
            D.send(self.msg, "digest@example.org", ["a@example.org"])
        self.make_server(send_error=smtplib.SMTPRecipientsRefused({"a@example.org": (550, b"no")}))
        with self.assertRaisesRegex(RuntimeError, "All recipients were refused"):
            D.send(self.msg, "digest@example.org", ["a@example.org"])
        self.make_server(send_error=smtplib.SMTPDataError(554, b"spam"))
        with self.assertRaisesRegex(RuntimeError, "it was not sent"):
            D.send(self.msg, "digest@example.org", ["a@example.org"])


class MonthArgument(DigestCase):
    def test_a_mistyped_month_stops_before_anything_is_sent(self):
        self.full_month()
        env = {"SMTP_SERVER": "smtp.example.org", "SMTP_USERNAME": "u@example.org", "SMTP_PASSWORD": "p",
               "DIGEST_TO": "group@example.org"}
        out = self.tmp_path / "out"
        with mock.patch.dict(os.environ, env), mock.patch.object(D, "send") as send:
            for bad in ("2026-9", "Oct", "2026-13", "", "2026-10 "):
                with self.subTest(month=bad):
                    self.assertEqual(D.main(["--as-of", "2026-10-01", "--month", bad]), 2)
                    self.assertEqual(D.main(["--dry-run", "--as-of", "2026-10-01", "--month", bad, "--out-dir", str(out)]), 2)
            send.assert_not_called()
            self.assertFalse(out.exists())
            self.assertEqual(D.main(["--as-of", "2026-10-01", "--month", "2026-10"]), 0)
            send.assert_called_once()

    def test_the_workflow_passes_any_month_on(self):
        wf = (ROOT / ".github" / "workflows" / "monthly-digest.yml").read_text(encoding="utf-8")
        self.assertIn('if [ -n "$month" ]; then args+=(--month "$month"); fi', wf)
        self.assertNotIn("=~ ^[0-9]{4}", wf)          # no silent filter: the script rejects a bad month


class RealData(unittest.TestCase):
    """The command itself, on the repository's own data (like the check workflow): it must always build."""

    def test_the_command_builds_a_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "GITHUB_STEP_SUMMARY": ""}
            for extra in ([], ["--month", "2027-01"]):
                r = subprocess.run([sys.executable, "-m", "scripts.notify.send_digest", "--dry-run", "--out-dir", tmp, *extra],
                                   cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("DRY RUN — subject: Grapevine / La Viña — ", r.stdout)
                html = (Path(tmp) / "digest.html").read_text(encoding="utf-8")
                text = (Path(tmp) / "digest.txt").read_text(encoding="utf-8")
                self.assertIn("Versión en español más abajo", html)
                self.assertIn("Resumen mensual — Edición de", text)
            self.assertIn("January 2027 edition · Edición de enero de 2027", r.stdout)


if __name__ == "__main__":
    unittest.main()
