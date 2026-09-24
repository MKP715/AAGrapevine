"""Offline tests for events that come from several places, and the new event fields:

  * the optional .ics feeds (config/site.yml `sources.ics_feeds:`) — The Events Calendar's export on
    neta65.org: parsing, ONE polite request per run, the health each feed reports (neta65.org sits behind
    Cloudflare's "Just a moment…" check: HTTP 403), the last good copy, a calendar sent without a charset
    (UTF-8), and the de-duplication against content/events (the six real Grapevine / La Viña workshop
    pages), the committee meeting and the recurring booth — with the cases that must NOT merge (a workshop
    or booth on an assembly's first day, the same event page on another date) and the notes the chair gets;
  * content/events `tentative: true` and `location_es` / `location_en`, "Venue to be announced";
  * events over several days (the Area assemblies, Fri–Sun);
  * the committee meeting's own `skip_dates` check;
  * the Actions run summary: a blocked optional feed is a notice, never a "stopped updating" source.

No network: every request is faked (tests/fixtures/neta65-workshops.ics mirrors the plugin's format:
VTIMEZONE, DTSTART;TZID=America/Chicago, UID "<post id>-<start>-<end>@neta65.org", URL, LOCATION with
", United States", CATEGORIES:Grapevine-LaViña,Workshop, ATTACH;FMTTYPE=image/… for the flyer).

    python -m unittest tests.test_events_feeds -v      (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import announcements as A  # noqa: E402
from scripts.sync import build_data as B  # noqa: E402
from scripts.sync import meeting as M  # noqa: E402

CHI = ZoneInfo("America/Chicago")
TODAY = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)          # Thu Sep 24, 2026, 10 AM CDT
FEED_URL = "https://neta65.org/events/category/workshop/list/?ical=1"
FIXTURE = (ROOT / "tests" / "fixtures" / "neta65-workshops.ics").read_text(encoding="utf-8").replace("\n", "\r\n")
CFG = {
    "site": {"timezone": "America/Chicago"},
    "meeting": {"weekday": "wednesday", "week_of_month": 3, "start": "19:00", "end": "20:00", "skip_dates": []},
    "sources": {
        "crawler": {"user_agent": "NETA65-GrapevineCommitteeBot/2.0 (+https://github.com/MKP715/AAGrapevine)"},
        "ics_feeds": [{"url": FEED_URL, "label": "NETA 65 workshops", "label_es": "Talleres de NETA 65",
                       "category": "neta65"}],
    },
}

# The six workshops as the chair wrote them in content/events (headers as in the real files).
WORKSHOPS = {
    "2026-09-26-lv-writing-workshop-fort-worth.md": (
        'title: "La Viña Writing Workshop (in Spanish) — Fort Worth"\n'
        'title_es: "Taller de Escritura de La Viña — Fort Worth"\n'
        "start: 2026-09-26T19:00:00-05:00\nend: 2026-09-26T21:00:00-05:00\n"
        'location: "Grupo Nueva Esperanza, 3401 E Belknap, Fort Worth, TX 76111"\n'
        'url: "https://neta65.org/event/lv-writing-workshop/"\n'
        'flyer: "https://neta65.org/wp-content/uploads/2026/09/New-Writing-Workshop-La-Nueva-Esperanza.jpg"\n'
        "lang: en\nsummary_es: \"Taller en español para aprender a escribir tu historia para La Viña.\"\n"),
    "2026-10-03-gv-writing-workshop-arlington.md": (
        'title: "Grapevine Writing Workshop — Arlington"\n'
        'title_es: "Taller de Escritura de Grapevine (en inglés) — Arlington"\n'
        "start: 2026-10-03T14:00:00-05:00\nend: 2026-10-03T17:00:00-05:00\n"
        'location: "Primary Purpose Group – Arlington, 1802 West Division Street, Arlington, TX 76012"\n'
        'url: "https://neta65.org/event/grapevine-writing-workshop-6/"\nlang: en\n'),
    "2026-10-07-lv-writing-workshop-mansfield.md": (
        'title: "La Viña Writing Workshop (in Spanish) — Mansfield"\n'
        'title_es: "Taller de Escritura de La Viña — Mansfield"\n'
        "start: 2026-10-07T20:00:00-05:00\nend: 2026-10-07T22:00:00-05:00\n"
        'location: "Grupo 7 Defectos y 7 Virtudes, 287 Frontage Road, Mansfield, TX 76063"\n'
        'url: "https://neta65.org/event/la-vina-writing-workshop-3/"\nlang: en\n'),
    "2026-10-17-lv-recording-workshop-duncanville.md": (
        'title: "La Viña Recording Workshop (in Spanish) — Duncanville"\n'
        'title_es: "Taller de Grabación de La Viña — Duncanville"\n'
        "start: 2026-10-17T19:00:00-05:00\nend: 2026-10-17T21:00:00-05:00\n"
        'location: "Grupo Progresso Latino, 101 East Camp Wisdom Rd., Duncanville, TX 75116"\n'
        'url: "https://neta65.org/event/lv-recording-workshop/"\nlang: en\n'),
    "2026-10-26-lv-writing-workshop-tyler.md": (
        'title: "La Viña Writing Workshop (in Spanish) — Tyler"\n'
        'title_es: "Taller de Escritura de La Viña — Tyler"\n'
        "start: 2026-10-26T19:00:00-05:00\nend: 2026-10-26T21:00:00-05:00\n"
        'location: "Grupo Libro Grande, 623 West Bow, Tyler, TX 75702"\n'
        'url: "https://neta65.org/event/la-vina-writing-workshop-4/"\nlang: en\n'),
    "2026-11-07-lv-information-workshop-longview.md": (
        'title: "La Viña Information Workshop (in Spanish) — Longview"\n'
        'title_es: "Taller de Información de La Viña — Longview"\n'
        "start: 2026-11-07T19:00:00-06:00\nend: 2026-11-07T21:00:00-06:00\n"
        'location: "Grupo Solo por Hoy, 2035 South High, Longview, TX 75602"\n'
        'url: "https://neta65.org/event/la-vina-information-workshop/"\nlang: en\n'),
    # no event page of its own: found in the feed by its day + title
    "2027-09-18-gvlv-booth-fall-assembly.md": (
        'title: "GV/LV booth — Fall Assembly"\ntitle_es: "Mesa de GV/LV — Asamblea de Otoño"\n'
        "start: 2027-09-18T09:00:00-05:00\nend: 2027-09-18T16:00:00-05:00\nlocation: Tyler, TX\nlang: en\n"),
    # the Spring assembly (Fri–Sun), no event page and no end in the file: the feed fills them in
    "2027-03-19-neta65-spring-assembly.md": (
        'title: "NETA 65 Spring Assembly 2027"\ntitle_es: "Asamblea de Primavera 2027 de NETA 65"\n'
        "start: 2027-03-19\n"
        'location: "DoubleTree by Hilton Hotel Dallas Near the Galleria, 4099 Valley View Ln, Dallas, TX 75244"\n'
        "lang: en\n"),
}
# The three 2027 Area assemblies as the chair wrote them (two not final yet: venue to be announced).
ASSEMBLIES = {
    "2027-03-19-neta65-spring-assembly.md": (
        'title: "NETA 65 Spring Assembly 2027"\ntitle_es: "Asamblea de Primavera 2027 de NETA 65"\n'
        "start: 2027-03-19\nend: 2027-03-21\n"
        'location: "DoubleTree by Hilton Hotel Dallas Near the Galleria, 4099 Valley View Ln, Dallas, TX 75244"\n'
        "lang: en\n"),
    "2027-06-25-neta65-summer-assembly.md": (
        'title: "NETA 65 Summer Assembly 2027"\ntitle_es: "Asamblea de Verano 2027 de NETA 65"\n'
        'start: 2027-06-25\nend: 2027-06-27\nlocation: "Venue to be announced"\nlocation_es: "Lugar por anunciarse"\n'
        "tentative: true\nlang: en\n"),
    "2027-09-17-neta65-fall-assembly.md": (
        'title: "NETA 65 Fall Assembly 2027"\ntitle_es: "Asamblea de Otoño 2027 de NETA 65"\n'
        'start: 2027-09-17\nend: 2027-09-19\nlocation: "Venue to be announced"\nlocation_es: "Lugar por anunciarse"\n'
        "tentative: true\nlang: en\n"),
}
VTIMEZONE = FIXTURE[FIXTURE.index("BEGIN:VTIMEZONE"):FIXTURE.index("END:VTIMEZONE") + len("END:VTIMEZONE")]


def ics(*vevents: str) -> str:
    """A small calendar in The Events Calendar's shape (VTIMEZONE + TZID=America/Chicago)."""
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//NETA65 - ECPv6.15.1//NONSGML v1.0//EN\r\n" + VTIMEZONE + "\r\n"
            + "".join(vevents) + "END:VCALENDAR\r\n")


def vevent(uid: str, summary: str, start: str, end: str, url: str, location: str = "", flyer: str = "") -> str:
    """start / end: '20270319T190000' (Central time) or '20270319' (all-day; end = the day AFTER, as in .ics)."""
    kind = "VALUE=DATE" if len(start) == 8 else "TZID=America/Chicago"
    lines = ["BEGIN:VEVENT", f"DTSTART;{kind}:{start}", f"DTEND;{kind}:{end}", f"UID:{uid}@neta65.org",
             f"SUMMARY:{summary}", f"URL:{url}"]
    if location:
        lines.append("LOCATION:" + location.replace(",", "\\,"))
    if flyer:
        lines.append(f"ATTACH;FMTTYPE=image/jpeg:{flyer}")
    return "\r\n".join(lines + ["END:VEVENT"]) + "\r\n"


REAL_URLS = ["https://neta65.org/event/lv-writing-workshop/", "https://neta65.org/event/grapevine-writing-workshop-6/",
             "https://neta65.org/event/la-vina-writing-workshop-3/", "https://neta65.org/event/lv-recording-workshop/",
             "https://neta65.org/event/la-vina-writing-workshop-4/", "https://neta65.org/event/la-vina-information-workshop/"]


def ctx_with(cfg: dict | None = None, now: datetime = TODAY, offline: bool = False) -> B.Ctx:
    ctx = B.Ctx(offline=True)
    ctx.offline = offline
    ctx.cfg = json.loads(json.dumps(cfg or CFG))
    ctx.now = now
    ctx.now_ts = now.timestamp()
    ctx.today_local = now.astimezone(ctx.tz).date()
    return ctx


class NoTranslator:
    def translate(self, texts, src, tgt):
        return [(None, False) for _ in texts]


class FakeResp:
    def __init__(self, status: int, text: str, headers: dict | None = None):
        self.status_code, self.text = status, text
        self.content = text.encode("utf-8")
        self.headers = headers or {}


CLOUDFLARE_403 = FakeResp(403, '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
                               '<script src="https://challenges.cloudflare.com/x.js"></script></head></html>',
                          {"Server": "cloudflare", "Cf-Mitigated": "challenge", "Content-Type": "text/html"})


class TempState(unittest.TestCase):
    """data/state (the feeds' last good copy) in a temporary folder; no translation model."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-feed-test-"))
        p = mock.patch.object(B, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(B.T, "get_translator", lambda **kw: NoTranslator())
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def state(self) -> dict:
        return json.loads((self.tmp / B.ICS_STATE_FILE).read_text(encoding="utf-8"))

    def manual_events(self, files: dict[str, str] | None = None) -> list[dict]:
        """content/events files (name → header lines) → their events, as announcements.py reads them."""
        folder = Path(tempfile.mkdtemp(dir=self.tmp, prefix="events-"))
        for name, header in (WORKSHOPS if files is None else files).items():
            (folder / name).write_text(f"---\n{header}---\nA hand-written description.\n", encoding="utf-8")
        return [A.parse_event(p, CHI) for p in A.content_files(folder)]


# --------------------------------------------------------------------------- reading a feed
class FeedParsing(TempState):
    def test_the_events_calendar_export(self):
        ctx = ctx_with()
        evs = B._parse_ics(ctx, {"_ics": FIXTURE, "_spec": B.feed_specs(ctx)[0]})
        by_url = {e["url"]: e for e in evs}
        self.assertEqual(len(evs), 11)                              # 12 VEVENTs, the CANCELLED one left out
        self.assertNotIn("https://neta65.org/event/cancelled-workshop/", by_url)
        fw = by_url["https://neta65.org/event/lv-writing-workshop/"]
        self.assertEqual((fw["extra"]["start"], fw["extra"]["end"]), ("2026-09-27T00:00:00Z", "2026-09-27T02:00:00Z"))
        self.assertEqual(fw["extra"]["location"], "Grupo Nueva Esperanza, 3401 E Belknap, Fort Worth, TX, 76111")
        self.assertEqual(fw["extra"]["city"], "Fort Worth")
        self.assertEqual(fw["extra"]["flyer_url"],
                         "https://neta65.org/wp-content/uploads/2026/09/New-Writing-Workshop-La-Nueva-Esperanza.jpg")
        self.assertEqual(fw["extra"]["flyer_thumb"], fw["extra"]["flyer_url"])
        self.assertEqual(fw["tags"], ["grapevine-lavina", "workshop"])
        self.assertEqual((fw["source"], fw["category"], fw["extra"]["feed"]), ("calendar", "neta65", "neta-65-workshops"))
        self.assertTrue(fw["extra"]["uid"].endswith("@neta65.org"))
        self.assertNotIn("tentative", fw["extra"])
        # daylight saving: 19:00 on Nov 7 is CST (UTC-6)
        lv = by_url["https://neta65.org/event/la-vina-information-workshop/"]
        self.assertEqual(lv["extra"]["start"], "2026-11-08T01:00:00Z")
        # HTML entities in titles (the plugin writes "&#8211;")
        self.assertEqual(by_url["https://neta65.org/event/grapevine-writing-workshop-7/"]["title"],
                         "Grapevine Writing Workshop – Denton")
        # STATUS:TENTATIVE → extra.tentative
        self.assertIs(by_url["https://neta65.org/event/taller-grabacion-garland/"]["extra"]["tentative"], True)
        # an all-day event over three days: the EXCLUSIVE DTEND (Mar 22) → the last day, Mar 21
        spring = by_url["https://neta65.org/event/spring-assembly-2027/"]
        self.assertEqual((spring["extra"]["start"], spring["extra"]["end"], spring["extra"]["all_day"]),
                         ("2027-03-19", "2027-03-21", True))
        # an escaped line break inside DESCRIPTION (\n), as the plugin writes it
        self.assertTrue(fw["summary"].endswith("strength and hope. All AA members are welcome."), fw["summary"])

    def test_the_fixture_is_valid_icalendar(self):
        """Every line of the fixture is a real content line (nothing silently dropped by the parser)."""
        from icalendar import Calendar
        cal = Calendar.from_ical(FIXTURE)
        errors = [e for comp in cal.walk() for e in (getattr(comp, "errors", None) or [])]
        self.assertEqual(errors, [])
        self.assertEqual(len(list(cal.walk("VEVENT"))), 12)

    def test_settings(self):
        ctx = ctx_with({"sources": {"ics_feeds": [
            {"url": "webcal://neta65.org/?post_type=tribe_events&tribe_events_cat=workshop&ical=1&eventDisplay=list",
             "label": "Workshops"},
            {"url": "not a link", "label": "Broken"},
            {"url": "https://example.org/cal.ics", "category": "somewhere"},
            "https://calendar.example.org/basic.ics"]}})
        specs = B.feed_specs(ctx)
        self.assertEqual([s["url"] for s in specs], [
            "https://neta65.org/?post_type=tribe_events&tribe_events_cat=workshop&ical=1&eventDisplay=list",
            "https://example.org/cal.ics", "https://calendar.example.org/basic.ics"])
        self.assertEqual([s["category"] for s in specs], ["ics", "ics", "ics"])
        self.assertEqual({s["group"] for s in specs}, {"neta"})
        self.assertEqual(len({s["key"] for s in specs}), 3)
        report = ctx.raw_problems["ics_feeds"]
        self.assertIn("“not a link” is not a calendar address", report)
        self.assertIn("category “somewhere”", report)
        self.assertEqual(B.feed_specs(ctx_with({"sources": {"ics_feeds": []}})), [])


# --------------------------------------------------------------------------- one polite request, health
class FeedHealth(TempState):
    def run_feed(self, ctx, resp):
        calls = []

        def fake_get(url, **kw):
            calls.append((url, kw))
            if isinstance(resp, Exception):
                raise resp
            return resp
        with mock.patch("requests.get", fake_get):
            evs = B.ics_events(ctx)
        return evs, calls

    def test_cloudflare_block_is_reported_plainly_and_asked_once_a_day(self):
        ctx = ctx_with()
        evs, calls = self.run_feed(ctx, CLOUDFLARE_403)
        self.assertEqual(evs, [])
        self.assertEqual(len(calls), 1)                                           # ONE request, no retries
        self.assertEqual(calls[0][0], FEED_URL)
        self.assertEqual(calls[0][1]["headers"]["User-Agent"], CFG["sources"]["crawler"]["user_agent"])  # our own name
        self.assertEqual(ctx.feed_requests, 1)
        h = ctx.feeds[0]
        self.assertEqual((h["state"], h["http_status"], h["events_count"], h["last_success"], h["checked_this_run"]),
                         ("blocked", 403, 0, None, True))
        self.assertIn("Cloudflare", h["error"])
        st = self.state()[FEED_URL]
        self.assertEqual((st["state"], st["http_status"], st["attempted"]), ("blocked", 403, "2026-09-24T15:00:00Z"))
        self.assertNotIn("ics", st)
        # a rerun the same day (a settings change, "Run workflow"): the site is NOT asked again
        for hours in (1, 12, 19):
            ctx2 = ctx_with(now=TODAY + timedelta(hours=hours))
            _, calls2 = self.run_feed(ctx2, AssertionError("must not be called"))
            self.assertEqual(calls2, [])
            self.assertEqual((ctx2.feeds[0]["state"], ctx2.feeds[0]["checked_this_run"]), ("blocked", False))
        # the next daily run asks again (once)
        ctx3 = ctx_with(now=TODAY + timedelta(days=1))
        _, calls3 = self.run_feed(ctx3, CLOUDFLARE_403)
        self.assertEqual(len(calls3), 1)

    def test_other_answers(self):
        cases = [(FakeResp(429, "slow down"), "blocked", 429), (FakeResp(404, "not found"), "error", 404),
                 (FakeResp(200, "<html>a web page</html>"), "error", 200),
                 (requests.ConnectionError("no route"), "error", None), (TimeoutError("slow"), "error", None)]
        for resp, state, http in cases:
            (self.tmp / B.ICS_STATE_FILE).unlink(missing_ok=True)
            ctx = ctx_with()
            self.run_feed(ctx, resp)
            self.assertEqual((ctx.feeds[0]["state"], ctx.feeds[0]["http_status"]), (state, http), resp)

    def test_a_good_copy_is_kept_and_used_while_blocked(self):
        ctx = ctx_with()
        evs, _ = self.run_feed(ctx, FakeResp(200, FIXTURE, {"Content-Type": "text/calendar"}))
        self.assertEqual(len(evs), 11)
        h = ctx.feeds[0]
        self.assertEqual((h["state"], h["http_status"], h["last_success"], h["from_copy"]),
                         ("ok", 200, "2026-09-24T15:00:00Z", False))
        self.assertEqual(self.state()[FEED_URL]["ics"], FIXTURE)
        # a working feed is not asked again the same day either ("at most once a day", as /status/ promises) …
        for hours in (1, 2, 12, 19):
            ctx1 = ctx_with(now=TODAY + timedelta(hours=hours))
            evs1, calls = self.run_feed(ctx1, AssertionError("must not be called"))
            self.assertEqual(calls, [])
            self.assertEqual((len(evs1), ctx1.feeds[0]["from_copy"]), (11, True))
        # … the next day the site blocks the robot: the copy is used, the date it worked is kept
        ctx2 = ctx_with(now=TODAY + timedelta(days=1))
        evs2, _ = self.run_feed(ctx2, CLOUDFLARE_403)
        self.assertEqual(len(evs2), 11)
        h2 = ctx2.feeds[0]
        self.assertEqual((h2["state"], h2["from_copy"], h2["last_success"], h2["events_count"]),
                         ("blocked", True, "2026-09-24T15:00:00Z", 11))
        # a new answer that cannot be read never replaces the good copy
        ctx3 = ctx_with(now=TODAY + timedelta(days=2))
        self.run_feed(ctx3, FakeResp(200, "BEGIN:VCALENDAR\r\nthis is not iCalendar"))
        self.assertEqual(self.state()[FEED_URL]["ics"], FIXTURE)

    def test_a_calendar_without_a_charset_is_read_as_utf8(self):
        """`requests` reads "text/calendar" without a charset as ISO-8859-1 ("La ViÃ±a"); RFC 5545 says UTF-8."""
        body = FIXTURE.encode("utf-8")
        for headers in ({"Content-Type": "text/calendar"}, {}, {"content-type": "text/calendar; charset=UTF-8"}):
            resp = FakeResp(200, "", headers)
            resp.content = body
            has_charset = "charset" in str(headers).lower()
            resp.text = body.decode("utf-8") if has_charset else body.decode("iso-8859-1")   # what requests gives
            (self.tmp / B.ICS_STATE_FILE).unlink(missing_ok=True)
            ctx = ctx_with()
            evs, _ = self.run_feed(ctx, resp)
            titles = {e["title"] for e in evs}
            self.assertIn("La Viña Writing Workshop", titles, headers)
            self.assertFalse(any("Ã" in t for t in titles), headers)

    def test_offline_build_never_asks(self):
        ctx = ctx_with(offline=True)
        _, calls = self.run_feed(ctx, AssertionError("must not be called"))
        self.assertEqual(calls, [])
        self.assertEqual(ctx.feeds[0]["state"], "never")
        self.assertFalse((self.tmp / B.ICS_STATE_FILE).exists())

    def test_status_json_keeps_feeds_apart_from_sources(self):
        ctx = ctx_with()
        self.run_feed(ctx, CLOUDFLARE_403)
        status = B.build_status(ctx, None, B.I18n(None), {}, False, 0.0)
        self.assertEqual([s["source"] for s in status["sources"]], [s[0] for s in B.SOURCES])
        self.assertNotIn("ics_feeds", status["problems"])          # a blocked feed is not a settings problem
        f = status["feeds"][0]
        self.assertEqual({k: f[k] for k in ("key", "label", "label_es", "state", "http_status", "events_count",
                                            "last_success", "group")},
                         {"key": "neta-65-workshops", "label": "NETA 65 workshops", "label_es": "Talleres de NETA 65",
                          "state": "blocked", "http_status": 403, "events_count": 0, "last_success": None,
                          "group": "neta"})


# --------------------------------------------------------------------------- the same event twice
class FeedDuplicates(TempState):
    def build(self, files=None, feed_text=FIXTURE, cfg=None):
        ctx = ctx_with(cfg)
        manual = self.manual_events(files)
        feed_items = B._parse_ics(ctx, {"_ics": feed_text, "_spec": B.feed_specs(ctx)[0]})
        ctx.feeds = [{"key": "neta-65-workshops", "label": "NETA 65 workshops"}]
        with mock.patch.object(B, "ics_events", lambda c: feed_items), \
                mock.patch.object(B.Ctx, "items", lambda self, name: manual if name == "manual_events" else []):
            evs = B.build_events(ctx)
        return ctx, evs

    def test_the_six_workshops_appear_once_and_keep_their_own_spanish(self):
        ctx, evs = self.build()
        for url in REAL_URLS:
            key = B.event_url_key(url)
            same = [e for e in evs if B.event_url_key(e.get("url")) == key]
            self.assertEqual(len(same), 1, url)
            self.assertEqual(same[0]["category"], "manual", url)            # the hand-written one wins
            self.assertEqual(same[0]["extra"]["also_in_feed"], "neta-65-workshops")
            self.assertEqual(same[0]["extra"]["feed_match"], "url")
            self.assertIn("es", same[0]["extra"]["own_i18n"]["title"])
        fw = next(e for e in evs if e["id"] == "ev:manual:2026-09-26-lv-writing-workshop-fort-worth")
        self.assertEqual(fw["extra"]["own_i18n"]["title"]["es"], "Taller de Escritura de La Viña — Fort Worth")
        self.assertEqual(fw["extra"]["location"], "Grupo Nueva Esperanza, 3401 E Belknap, Fort Worth, TX 76111")
        # the feed only fills what the file leaves out: Arlington's file has no flyer
        arl = next(e for e in evs if e["id"] == "ev:manual:2026-10-03-gv-writing-workshop-arlington")
        self.assertEqual(arl["extra"]["flyer_url"], "https://neta65.org/wp-content/uploads/2026/06/GV-LV-writing-workshop-10-3.png")
        self.assertEqual(arl["title"], "Grapevine Writing Workshop — Arlington")
        # found by day + similar title (no shared event page): the booth; the assembly gets its page and end
        booth = [e for e in evs if B.local_day(ctx, e["extra"]["start"]) == "2027-09-18"]
        self.assertEqual([(e["category"], e["extra"].get("feed_match")) for e in booth], [("manual", "title")])
        self.assertEqual(booth[0]["url"], "https://neta65.org/event/gvlv-booth-fall-assembly/")
        spring = next(e for e in evs if e["id"] == "ev:manual:2027-03-19-neta65-spring-assembly")
        self.assertEqual((spring["extra"]["start"], spring["extra"]["end"], spring["url"]),
                         ("2027-03-19", "2027-03-21", "https://neta65.org/event/spring-assembly-2027/"))
        # events only in the feed are shown, with the NETA 65 events
        feed_only = sorted(e["url"] for e in evs if e["category"] == "neta65")
        self.assertEqual(feed_only, ["https://neta65.org/event/grapevine-writing-workshop-7/",
                                     "https://neta65.org/event/la-vina-writing-workshop-dallas/",
                                     "https://neta65.org/event/taller-grabacion-garland/"])
        self.assertEqual(ctx.feeds[0]["duplicates"], 8)
        self.assertEqual(ctx.feeds[0]["notes"], [])          # the feed and the files agree
        # nothing else was lost or doubled
        self.assertEqual(len([e for e in evs if e["category"] in ("manual", "neta65")]), len(WORKSHOPS) + 3)

    def test_url_normalization(self):
        k = B.event_url_key
        self.assertEqual(k("https://neta65.org/event/lv-writing-workshop/"), "neta65.org/event/lv-writing-workshop")
        for v in ("http://www.neta65.org/event/lv-writing-workshop", "HTTPS://NETA65.ORG/event/lv-writing-workshop//",
                  "https://neta65.org/event/lv-writing-workshop/?ical=1#details"):
            self.assertEqual(k(v), "neta65.org/event/lv-writing-workshop", v)
        for v in ("https://neta65.org", "https://neta65.org/", "/events/#2027-03-19-x", "", None, "mailto:a@b.org"):
            self.assertIsNone(k(v), v)          # not an event's own page: never used to match

    def test_similar_titles(self):
        fort_worth, tyler = B.title_tokens("Fort Worth"), B.title_tokens("Tyler")
        # the city both events are in (and the year) say nothing: they are ignored
        self.assertTrue(B.similar_titles("LV Writing Workshop", "La Viña Writing Workshop (in Spanish) — Fort Worth",
                                         fort_worth))
        self.assertTrue(B.similar_titles("Taller de Escritura de La Viña", "Taller de Escritura de La Viña — Tyler", tyler))
        self.assertTrue(B.similar_titles("GV/LV booth — Fall Assembly", "Grapevine / La Viña Booth at the Fall Assembly"))
        self.assertTrue(B.similar_titles("NETA 65 Spring Assembly 2027", "Spring Assembly", {"2027"}, exact=True))
        self.assertFalse(B.similar_titles("LV Writing Workshop", "LV Recording Workshop"))
        self.assertFalse(B.similar_titles("Workshop", "Spring Assembly"))
        # an event AT an assembly is not the assembly (the kind of event counts; one-way "subset" is not enough)
        self.assertFalse(B.similar_titles("Grapevine Workshop at the Spring Assembly", "NETA 65 Spring Assembly 2027",
                                          {"2027"}))
        self.assertFalse(B.similar_titles("La Viña Booth at the Fall Assembly", "NETA 65 Fall Assembly 2027", {"2027"}))
        self.assertFalse(B.similar_titles("GV/LV Booth at the Spring Assembly", "NETA 65 Spring Assembly 2027", {"2027"}))
        # without the city on both sides (a place to be announced), every telling word must match
        self.assertFalse(B.similar_titles("LV Writing Workshop", "La Viña Writing Workshop Grupo Hispano", exact=True))
        self.assertTrue(B.similar_titles("LV Writing Workshop", "La Viña Writing Workshop (in Spanish)", exact=True))

    def test_a_workshop_or_booth_at_an_assembly_is_its_own_event(self):
        """On an assembly's first day, at the same hotel (known venue) or in the city of an assembly whose
        venue is still to be announced: never merged into the assembly, which keeps its own link and flyer."""
        feed = ics(
            vevent("20001", "Grapevine Workshop at the Spring Assembly", "20270319T190000", "20270319T210000",
                   "https://neta65.org/event/gv-workshop-spring-assembly/",
                   "DoubleTree by Hilton Hotel Dallas Near the Galleria, 4099 Valley View Ln, Dallas, TX, 75244, United States",
                   "https://neta65.org/wp-content/uploads/2027/03/gv-workshop.jpg"),
            vevent("20002", "La Viña Booth at the Fall Assembly", "20270917T090000", "20270917T160000",
                   "https://neta65.org/event/lv-booth-fall-assembly/", "Tyler Convention Center, Tyler, TX, United States",
                   "https://neta65.org/wp-content/uploads/2027/09/lv-booth.jpg"),
            # all-day on the assembly's first day only (a one-day event against a three-day one)
            vevent("20003", "Spring Assembly", "20270319", "20270320", "https://neta65.org/event/spring-hospitality/",
                   "Dallas, TX, United States"))
        ctx, evs = self.build(ASSEMBLIES, feed)
        by_id = {e["id"]: e for e in evs}
        spring, fall = by_id["ev:manual:2027-03-19-neta65-spring-assembly"], by_id["ev:manual:2027-09-17-neta65-fall-assembly"]
        for a in (spring, fall):
            self.assertNotIn("feed_match", a["extra"])
            self.assertTrue(a["url"].startswith("/events/#"), a["url"])          # its own card, not the workshop's page
            self.assertIsNone(a["extra"]["flyer_url"])
        self.assertEqual(sorted(e["url"] for e in evs if e["category"] == "neta65"), [
            "https://neta65.org/event/gv-workshop-spring-assembly/", "https://neta65.org/event/lv-booth-fall-assembly/",
            "https://neta65.org/event/spring-hospitality/"])
        self.assertEqual(ctx.feeds[0]["duplicates"], 0)

    def test_the_same_page_on_another_date(self):
        """A shared event page counts only on the same day. A past file never swallows a new date; an upcoming
        file whose page shows another date is reported to the chair (the calendar's date is shown too)."""
        feed = ics(
            # the Fort Worth file (Sep 26, 2026) — its page used again for a new workshop in January
            vevent("30001", "LV Writing Workshop", "20270115T190000", "20270115T210000",
                   "https://neta65.org/event/lv-writing-workshop/", "Grupo Hispano, 4800 Ross Ave, Dallas, TX, 75204"),
            # the Arlington file says Oct 3; neta65.org now says Oct 10
            vevent("30002", "Grapevine Writing Workshop", "20261010T140000", "20261010T170000",
                   "https://neta65.org/event/grapevine-writing-workshop-6/",
                   "Primary Purpose Group – Arlington, 1802 West Division Street, Arlington, TX, 76012"))
        ctx, evs = self.build(WORKSHOPS, feed)
        feed_only = sorted(B.local_day(ctx, e["extra"]["start"]) for e in evs if e["category"] == "neta65")
        self.assertEqual(feed_only, ["2026-10-10", "2027-01-15"])               # neither date is lost
        self.assertEqual(ctx.feeds[0]["duplicates"], 0)
        notes = ctx.feeds[0]["notes"]
        self.assertEqual(len(notes), 2)
        arl = next(n for n in notes if "arlington" in n)
        self.assertIn("content/events/2026-10-03-gv-writing-workshop-arlington.md", arl)
        self.assertIn("on 2026-10-10, but the file says 2026-10-03", arl)
        self.assertIn("“NETA 65 workshops” calendar", arl)
        # the Fort Worth workshop is still upcoming on Sep 24 — the chair is asked too; once it is over, not
        fw_ctx = ctx_with(now=datetime(2026, 10, 1, 12, tzinfo=CHI).astimezone(timezone.utc))
        fw_ctx.feeds = [{"key": "neta-65-workshops"}]
        manual = self.manual_events({k: v for k, v in WORKSHOPS.items() if "fort-worth" in k})
        items = B._parse_ics(fw_ctx, {"_ics": feed, "_spec": B.feed_specs(fw_ctx)[0]})
        kept = B.merge_feed_duplicates(fw_ctx, manual, items)
        self.assertEqual(len(kept), 2)
        self.assertEqual(fw_ctx.feeds[0]["notes"], [])

    def test_another_start_time_is_reported(self):
        feed = ics(vevent("30003", "Grapevine Writing Workshop", "20261003T150000", "20261003T180000",
                          "https://neta65.org/event/grapevine-writing-workshop-6/"))
        ctx, evs = self.build({k: v for k, v in WORKSHOPS.items() if "arlington" in k}, feed)
        arl = next(e for e in evs if e["id"] == "ev:manual:2026-10-03-gv-writing-workshop-arlington")
        self.assertEqual((arl["extra"]["feed_match"], arl["extra"]["start"]), ("url", "2026-10-03T19:00:00Z"))   # file wins
        self.assertEqual(ctx.feeds[0]["notes"], [
            "content/events/2026-10-03-gv-writing-workshop-arlington.md: the “NETA 65 workshops” calendar says it "
            "starts at 3:00 PM, the file says 2:00 PM (Central time). If the time changed, correct start: and end: "
            "in the file."])

    def test_a_venue_now_known_fills_the_tba_place_and_the_chair_is_told(self):
        feed = ics(vevent("30004", "NETA 65 Summer Assembly 2027", "20270625", "20270628",
                          "https://neta65.org/event/summer-assembly-2027/", "Harvey Hotel, 2 Main St, Tyler, TX, 75701"))
        ctx, evs = self.build(ASSEMBLIES, feed)
        summer = next(e for e in evs if e["id"] == "ev:manual:2027-06-25-neta65-summer-assembly")
        self.assertEqual(summer["extra"]["feed_match"], "title")
        self.assertEqual((summer["extra"]["location"], summer["extra"]["city"]), ("Harvey Hotel, 2 Main St, Tyler, TX, 75701", "Tyler"))
        self.assertNotIn("location_tba", summer["extra"])
        self.assertNotIn("location", summer["extra"]["own_i18n"])        # the stale "Lugar por anunciarse" is gone
        self.assertIs(summer["extra"]["tentative"], True)                # still the chair's call
        self.assertEqual(summer["url"], "https://neta65.org/event/summer-assembly-2027/")
        B.I18n(NoTranslator()).apply(summer)
        self.assertNotIn("location", summer["i18n"])                     # the address, as written, in both languages
        self.assertEqual(ctx.feeds[0]["notes"], [
            "content/events/2027-06-25-neta65-summer-assembly.md: the file says the venue is not known yet; the "
            "“NETA 65 workshops” calendar gives “Harvey Hotel, 2 Main St, Tyler, TX, 75701”, which the site shows "
            "now. Put it in location: (and delete location_es:)."])
        self.assertEqual(len([e for e in evs if e["category"] in ("manual", "neta65")]), 3)

    def test_the_booth_and_the_meeting_in_a_feed_show_once(self):
        """config recurring_events (the CityWide Dallas booth) and the committee meeting are on the calendar
        too: a feed that lists them (the whole neta65.org calendar, a GV/LV calendar) never doubles them."""
        cfg = json.loads(json.dumps(CFG))
        cfg["recurring_events"] = [{
            "key": "citywide-dallas", "title": "GV/LV booth at CityWide Dallas", "title_es": "Mesa de GV/LV en CityWide Dallas",
            "week_of_month": 2, "weekday": "saturday", "start": "17:00", "end": "20:00",
            "location": "Lover's Lane United Methodist Church, 9200 Inwood Road, Dallas, TX 75220",
            "url": "https://citywidedallasaa.org"}]
        meeting = B.committee_meetings(ctx_with(cfg))[0]
        m_start = datetime.fromisoformat(meeting["extra"]["start"].replace("Z", "+00:00")).astimezone(CHI)
        m_end = datetime.fromisoformat(meeting["extra"]["end"].replace("Z", "+00:00")).astimezone(CHI)
        feed = ics(
            vevent("40001", "Grapevine/La Viña Booth at CityWide Dallas", "20261010T170000", "20261010T200000",
                   "https://neta65.org/event/gvlv-booth-citywide/",
                   "Lover's Lane United Methodist Church, 9200 Inwood Road, Dallas, TX, 75220",
                   "https://neta65.org/wp-content/uploads/2026/10/booth.jpg"),
            vevent("40002", "GV/LV Committee Meeting", f"{m_start:%Y%m%dT%H%M%S}", f"{m_end:%Y%m%dT%H%M%S}",
                   "https://neta65.org/event/gvlv-committee-meeting/", "Online"))
        ctx, evs = self.build({}, feed, cfg)
        self.assertEqual([e["id"] for e in evs if e["category"] == "neta65"], [])
        self.assertEqual(ctx.feeds[0]["duplicates"], 2)
        booth = next(e for e in evs if e["id"] == "ev:recurring:citywide-dallas:2026-10-10")
        self.assertEqual((booth["url"], booth["extra"]["flyer_url"]), ("https://citywidedallasaa.org", None))  # untouched
        self.assertEqual(booth["extra"]["feed_match"], "title")
        m = next(e for e in evs if e["id"] == meeting["id"])
        self.assertEqual((m["url"], m["extra"]["feed_match"]), ("/meetings/", "title"))
        self.assertEqual(ctx.feeds[0]["notes"], [])

    def test_the_same_page_for_every_date_of_a_series(self):
        """A feed may list every date of a series under one page: only the date written by hand is dropped."""
        ctx = ctx_with()
        manual = self.manual_events({k: v for k, v in WORKSHOPS.items() if "tyler" in k})
        one = next(e for e in B._parse_ics(ctx, {"_ics": FIXTURE, "_spec": B.feed_specs(ctx)[0]})
                   if e["url"].endswith("la-vina-writing-workshop-4/"))
        later = json.loads(json.dumps(one))
        later["id"], later["extra"]["start"], later["extra"]["end"] = "ev:ics:later", "2026-11-23T01:00:00Z", None
        kept = B.merge_feed_duplicates(ctx, manual, [one, later])
        self.assertEqual([e["id"] for e in kept], ["ev:ics:later"])

    def test_same_title_same_day_other_city_is_another_event(self):
        ctx = ctx_with()
        manual = self.manual_events({k: v for k, v in WORKSHOPS.items() if "tyler" in k})
        feed = B._parse_ics(ctx, {"_ics": FIXTURE, "_spec": B.feed_specs(ctx)[0]})
        dallas = [e for e in feed if e["url"].endswith("-dallas/")]
        self.assertEqual(B.merge_feed_duplicates(ctx, manual, dallas), dallas)


# --------------------------------------------------------------------------- tentative, place in Spanish
class TentativeAndPlace(TempState):
    def test_tentative_values(self):
        files = {f"2027-0{i}-01-x{i}.md": f"title: X{i}\nstart: 2027-0{i}-01\ntentative: {v}\n"
                 for i, v in enumerate(["true", "yes", "sí", "Sí", "no", "false", '""'], 1)}
        got = {e["extra"]["slug"]: e["extra"].get("tentative") for e in self.manual_events(files)}
        self.assertEqual(got, {"2027-01-01-x1": True, "2027-02-01-x2": True, "2027-03-01-x3": True,
                               "2027-04-01-x4": True, "2027-05-01-x5": None, "2027-06-01-x6": None,
                               "2027-07-01-x7": None})

    def summer(self):
        return self.manual_events({"2027-06-25-neta65-summer-assembly.md": (
            'title: "NETA 65 Summer Assembly 2027"\ntitle_es: "Asamblea de Verano 2027 de NETA 65"\n'
            'start: 2027-06-25\nend: 2027-06-27\nlocation: "Venue to be announced"\n'
            'location_es: "Lugar por anunciarse"\ntentative: true\nlang: en\n')})[0]

    def test_location_in_spanish_and_venue_to_be_announced(self):
        ev = self.summer()
        self.assertEqual(ev["extra"]["own_i18n"]["location"], {"es": "Lugar por anunciarse"})
        self.assertIs(ev["extra"]["tentative"], True)
        ctx = ctx_with()
        with mock.patch.object(B, "ics_events", lambda c: []), \
                mock.patch.object(B.Ctx, "items", lambda self, name: [ev] if name == "manual_events" else []):
            evs = B.build_events(ctx)
        e = next(x for x in evs if x["id"] == ev["id"])
        self.assertIs(e["extra"]["location_tba"], True)
        self.assertIs(e["extra"]["tentative"], True)
        B.I18n(NoTranslator()).apply(e)
        self.assertEqual(e["i18n"]["location"], {"en": "Venue to be announced", "es": "Lugar por anunciarse"})

    def test_a_real_address_is_never_translated_and_tba_gets_the_sites_own_words(self):
        spring = self.manual_events({"a.md": 'title: Spring\nstart: 2027-03-19\nlocation: "DoubleTree, 4099 Valley '
                                              'View Ln, Dallas, TX 75244"\nlang: en\n'})[0]
        B.I18n(NoTranslator()).apply(spring)
        self.assertNotIn("location", spring["i18n"])                 # the page shows extra.location as it is
        tba = self.manual_events({"b.md": "title: Fall\nstart: 2027-09-17\nlocation: TBA\nlang: en\n"})[0]
        B.I18n(NoTranslator()).apply(tba)
        self.assertEqual(tba["i18n"]["location"], {"en": "TBA", "es": "Lugar por anunciarse"})
        spanish = self.manual_events({"c.md": 'title: Taller\nstart: 2027-01-09\nlocation: "Lugar por anunciarse"\n'
                                              'location_en: "Venue to be announced soon"\nlang: es\n'})[0]
        B.I18n(NoTranslator()).apply(spanish)
        self.assertEqual(spanish["i18n"]["location"], {"es": "Lugar por anunciarse", "en": "Venue to be announced soon"})
        for v in ("Venue to be announced", "Lugar por anunciarse", "TBA", "TBD.", "Location: to be determined",
                  "Por confirmar", "Sede: por definir"):
            self.assertTrue(B.location_is_tba(v), v)
        for v in ("Tyler, TX", "TBA Hall, 12 Main St", "", None, "Grupo Por Confirmar, Dallas"):
            self.assertFalse(B.location_is_tba(v), v)

    def test_a_location_es_left_behind_never_hides_the_confirmed_address(self):
        """The venue was confirmed (`location` has the address) but `location_es: "Lugar por anunciarse"` was
        not deleted: the address is shown in both languages (no hourglass, in the calendar files), and the
        chair is told which line to delete."""
        ev = self.manual_events({"2027-06-25-neta65-summer-assembly.md": (
            'title: "NETA 65 Summer Assembly 2027"\nstart: 2027-06-25\nend: 2027-06-27\n'
            'location: "Hilton Tyler, 1 Main St, Tyler, TX 75701"\nlocation_es: "Lugar por anunciarse"\nlang: en\n')})[0]
        ctx = ctx_with()
        with mock.patch.object(B, "ics_events", lambda c: []), \
                mock.patch.object(B.Ctx, "items", lambda self, name: [ev] if name == "manual_events" else []):
            evs = B.build_events(ctx)
        e = next(x for x in evs if x["id"] == ev["id"])
        self.assertNotIn("location_tba", e["extra"])
        B.I18n(NoTranslator()).apply(e)
        self.assertNotIn("location", e["i18n"])            # the pages show extra.location, in both languages
        self.assertEqual(ctx.raw_problems["content_events"], (
            "content/events/2027-06-25-neta65-summer-assembly.md: location_es says the venue is not known yet, but "
            "location: gives “Hilton Tyler, 1 Main St, Tyler, TX 75701” — that place is shown in both languages. "
            "Delete the location_es line."))
        # a status.json problem that is not a content source: manual_events itself did not fail
        status = B.build_status(ctx, None, B.I18n(None), {}, False, 0.0)
        self.assertNotEqual(next(s for s in status["sources"] if s["source"] == "manual_events").get("error"),
                            ctx.raw_problems["content_events"])


# --------------------------------------------------------------------------- several days
class MultiDay(TempState):
    def test_upcoming_through_the_last_day(self):
        ev = self.manual_events({"2027-03-19-spring.md": "title: Spring Assembly\nstart: 2027-03-19\nend: 2027-03-21\n"})[0]
        self.assertEqual((ev["extra"]["start"], ev["extra"]["end"], ev["extra"]["all_day"]), ("2027-03-19", "2027-03-21", True))
        # (like every event, the data keeps it one more day — cutoff = now − 24 h; the pages hide it at
        # midnight after its last day by themselves)
        for when, past in ((datetime(2027, 3, 20, 12, tzinfo=CHI), False),     # Saturday
                           (datetime(2027, 3, 21, 23, 0, tzinfo=CHI), False),   # Sunday night, the last day
                           (datetime(2027, 3, 22, 23, 0, tzinfo=CHI), False),   # the one-day grace
                           (datetime(2027, 3, 23, 0, 30, tzinfo=CHI), True),
                           (datetime(2027, 3, 23, 12, tzinfo=CHI), True)):
            ctx = ctx_with(now=when.astimezone(timezone.utc))
            with mock.patch.object(B, "ics_events", lambda c: []), \
                    mock.patch.object(B.Ctx, "items", lambda self, name: [ev] if name == "manual_events" else []):
                evs = B.build_events(ctx)
            got = next(e for e in evs if e["id"] == ev["id"])
            self.assertIs(got["extra"]["past"], past, when)

    def test_weekly_email_shows_the_range_the_place_and_the_note(self):
        from scripts.notify import send_digest as D
        ev = self.manual_events({"2027-06-25-summer.md": (
            'title: "NETA 65 Summer Assembly 2027"\ntitle_es: "Asamblea de Verano 2027 de NETA 65"\nstart: 2027-06-25\n'
            'end: 2027-06-27\nlocation: "Venue to be announced"\nlocation_es: "Lugar por anunciarse"\ntentative: yes\n'
            'lang: en\n')})[0]
        B.I18n(NoTranslator()).apply(ev)
        en, es = D.event_row(ev, "en", D.Links("https://example.org")), D.event_row(ev, "es", D.Links("https://example.org"))
        self.assertEqual(en["when"], "Fri, Jun 25 – Sun, Jun 27 · details to be confirmed")
        self.assertEqual(es["when"], "vie., 25 de jun. – dom., 27 de jun. · detalles por confirmar")
        self.assertEqual((en["where"], es["where"]), ("Venue to be announced", "Lugar por anunciarse"))
        # … and it stays in the e-mail through Sunday
        with mock.patch.object(D, "load_items", lambda name: [ev] if name == "events" else []):
            data = D.collect(datetime(2027, 6, 27, 20, 0, tzinfo=CHI).astimezone(timezone.utc), 7, 30, 6)
        self.assertEqual([e["id"] for e in data["events"]], [ev["id"]])

    def test_weekly_email_uses_the_sites_multi_day_rule(self):
        """A timed event that only runs past midnight is ONE day (as on the website: more than 18 hours, or
        all-day over several dates); an end at midnight belongs to the day before."""
        from scripts.notify import send_digest as D
        links = D.Links("https://example.org")

        def when(start, end, all_day=False):
            return D.event_row({"kind": "event", "title": "X", "extra": {"start": start, "end": end, "all_day": all_day}},
                               "en", links)["when"]
        self.assertEqual(when("2026-10-03T19:00:00-05:00", "2026-10-04T01:00:00-05:00"), "Sat, Oct 3 · 7:00 PM CDT")
        self.assertEqual(when("2026-10-03T19:00:00-05:00", "2026-10-04T00:00:00-05:00"), "Sat, Oct 3 · 7:00 PM CDT")
        self.assertEqual(when("2026-10-03T09:00:00-05:00", "2026-10-04T16:00:00-05:00"),
                         "Sat, Oct 3 · 9:00 AM CDT – Sun, Oct 4")
        self.assertEqual(when("2027-03-19", "2027-03-21", True), "Fri, Mar 19 – Sun, Mar 21")
        self.assertEqual(when("2027-03-19", "2027-03-19", True), "Fri, Mar 19")


# --------------------------------------------------------------------------- the committee meeting's skip dates
class MeetingSkipDates(unittest.TestCase):
    def test_a_date_that_is_not_a_meeting_day_is_reported_and_ignored(self):
        cfg = {"weekday": "wednesday", "week_of_month": 3,
               "skip_dates": ["2026-12-16", "2026-12-17", "2027-01-13", "garbage", "2027-02-30"]}
        notes = M.meeting_skip_notes(cfg)
        self.assertEqual(notes, [
            "skip date “2026-12-17” is not the 3rd Wednesday of its month — ignored (that month's is 2026-12-16)",
            "skip date “2027-01-13” is not the 3rd Wednesday of its month — ignored (that month's is 2027-01-20)",
            "skip date “garbage” is not a date like \"2027-01-09\" — ignored",
            "skip date “2027-02-30” is not a date like \"2027-01-09\" — ignored"])
        self.assertEqual(M.meeting_rule(cfg).skip, frozenset({"2026-12-16"}))
        self.assertEqual(M.meeting_skip_notes({"skip_dates": []}), [])
        self.assertEqual(M.meeting_skip_notes({}), [])

    def test_the_note_reaches_status_json(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["meeting"]["skip_dates"] = ["2026-12-17"]
        ctx = ctx_with(cfg)
        with mock.patch.object(B, "ics_events", lambda c: []), mock.patch.object(B, "committee_meetings", lambda c: []):
            B.build_events(ctx)
        self.assertEqual(ctx.raw_problems["meeting"], "config/site.yml meeting: skip date “2026-12-17” is not the "
                                                      "3rd Wednesday of its month — ignored (that month's is 2026-12-16)")


# --------------------------------------------------------------------------- the Actions run summary
class RunSummary(unittest.TestCase):
    """The "Write run summary" step of .github/workflows/update.yml, run on a status.json with a blocked
    feed: it is listed as information, and it is NOT a source that stopped updating (no weekly issue)."""

    def test_blocked_feed_is_informational(self):
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8"))
        step = next(s for s in wf["jobs"]["sync"]["steps"] if s.get("name") == "Write run summary")
        code = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        tmp = Path(tempfile.mkdtemp(prefix="gv-summary-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data" / "site").mkdir(parents=True)
        week_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = {"fixture": False, "sources": [
            {"source": "manual_events", "label": "Events (content/events)", "ok": True, "updated": week_ago, "count": 9}],
            "feeds": [{"key": "neta-65-workshops", "label": "NETA 65 workshops", "url": FEED_URL, "state": "blocked",
                       "http_status": 403, "events_count": 11, "duplicates": 8, "last_success": "2026-09-20T15:00:00Z",
                       "checked_this_run": True, "notes": [
                           "content/events/2026-10-03-gv-writing-workshop-arlington.md: the “NETA 65 workshops” "
                           "calendar lists its event page (https://neta65.org/event/grapevine-writing-workshop-6/) on "
                           "2026-10-10, but the file says 2026-10-03. If the date changed, correct start: and end: in "
                           "the file; if it is another event, give the file its own url:."]}],
            "problems": {"content_events": "content/events/2027-06-25-x.md: location_es says the venue is not known "
                                           "yet, but location: gives “Hilton Tyler” — that place is shown in both "
                                           "languages. Delete the location_es line."}}
        (tmp / "data" / "site" / "status.json").write_text(json.dumps(status), encoding="utf-8")
        (tmp / "script.py").write_text(code, encoding="utf-8")
        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(tmp / "summary.md"), GITHUB_OUTPUT=str(tmp / "out.txt"),
                   PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, str(tmp / "script.py")], cwd=tmp, env=env, capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("::notice title=Calendar feed blocked (NETA 65 workshops)::", r.stdout)
        self.assertNotIn("Nothing is missing", r.stdout)           # it only shows what someone added by hand
        self.assertIn("only after someone on the committee adds it by hand", r.stdout)
        # the feed's notes: a notice + a "Check:" line each (never a warning)
        self.assertIn("::notice title=Check a content/events file (NETA 65 workshops)::content/events/2026-10-03-", r.stdout)
        feed_warnings = [ln for ln in r.stdout.splitlines() if ln.startswith("::warning") and "content_events" not in ln]
        self.assertEqual(feed_warnings, [])
        summary = (tmp / "summary.md").read_text(encoding="utf-8")
        self.assertIn("NETA 65 workshops: blocked by the site's bot protection · HTTP 403 · 11 event(s), 8 already on "
                      "the Events page (shown once) · last read 2026-09-20 (that copy is still used)", summary)
        self.assertIn("  - Check: content/events/2026-10-03-gv-writing-workshop-arlington.md: the “NETA 65 workshops” "
                      "calendar lists its event page", summary)
        # a slip in a content/events file is a Settings problem (the chair fixes the file)
        self.assertIn("::warning title=Settings problem (content_events)::content/events/2027-06-25-x.md", r.stdout)
        self.assertIn("- content/events/2027-06-25-x.md: location_es says", summary)
        health = json.loads((tmp / "out.txt").read_text(encoding="utf-8").split("health=", 1)[1])
        self.assertEqual(health["stale"], [])                    # → no "stopped updating" issue


if __name__ == "__main__":
    unittest.main()
