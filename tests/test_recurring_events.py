"""Offline tests for monthly recurring events: the date rule shared by the committee meeting and
config/site.yml `recurring_events:` (scripts/sync/meeting.py), the events build_data makes of them
(scripts/sync/build_data.py) and the weekly e-mail's handling (scripts/notify/send_digest.py).

The settings are written here, not read from config/site.yml, so the chair can change the real
booth (or add others) without turning these tests red. No network, no translation model.

    python -m unittest tests.test_recurring_events -v      (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import build_data as B  # noqa: E402
from scripts.sync import meeting as M  # noqa: E402
from scripts.sync.meeting import MonthlyRule, upcoming_rule_dates  # noqa: E402

CHI = ZoneInfo("America/Chicago")
TODAY = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)          # Thu Sep 24, 2026, 10 AM CDT
BOOTH = MonthlyRule(week_of_month=2, weekday=5, start=(17, 0), end=(20, 0))   # 2nd Saturday 5–8 PM

CITYWIDE_YAML = """
site: {timezone: America/Chicago}
recurring_events:
  - key: "citywide-dallas"
    title: "GV/LV booth at CityWide Dallas"
    title_es: "Mesa de GV/LV en CityWide Dallas"
    summary: "Our literature table at CityWide Dallas."
    summary_es: "Nuestra mesa de literatura en CityWide Dallas."
    week_of_month: 2
    weekday: "saturday"
    start: 17:00
    end: "20:00"
    location: "Lover's Lane United Methodist Church, 9200 Inwood Road, Dallas, TX 75220"
    url: "https://citywidedallasaa.org"
    months_ahead: 6
    skip_dates: []
"""


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ctx_with(cfg_yaml: str | dict, now: datetime = TODAY) -> B.Ctx:
    """A build context with the given settings and a fixed clock (no raw data)."""
    ctx = B.Ctx(offline=True)
    ctx.cfg = yaml.safe_load(cfg_yaml) if isinstance(cfg_yaml, str) else cfg_yaml
    ctx.now = now
    ctx.now_ts = now.timestamp()
    ctx.today_local = now.astimezone(ctx.tz).date()
    return ctx


class NoTranslator:
    """Stands in for the translation model: tests never download or run it."""
    def translate(self, texts, src, tgt):
        return [(None, False) for _ in texts]


# --------------------------------------------------------------------------- the date rule
class MonthlyRuleDates(unittest.TestCase):
    def test_second_saturday_for_13_months_across_both_dst_changes(self):
        got = upcoming_rule_dates(BOOTH, 13, CHI, TODAY)
        self.assertEqual([d["ymd"] for d in got], [
            "2026-10-10", "2026-11-14", "2026-12-12", "2027-01-09", "2027-02-13", "2027-03-13", "2027-04-10",
            "2027-05-08", "2027-06-12", "2027-07-10", "2027-08-14", "2027-09-11", "2027-10-09"])
        starts = {d["ymd"]: d["start"] for d in got}
        ends = {d["ymd"]: d["end"] for d in got}
        # 17:00 Central = 22:00 UTC with daylight time (CDT, until Nov 1, 2026 / from Mar 14, 2027) …
        self.assertEqual(starts["2026-10-10"], "2026-10-10T22:00:00Z")
        self.assertEqual(ends["2026-10-10"], "2026-10-11T01:00:00Z")
        # … and 23:00 UTC with standard time (CST): after the Nov 1 switch, and on Mar 13 (DST starts Mar 14)
        self.assertEqual(starts["2026-11-14"], "2026-11-14T23:00:00Z")
        self.assertEqual(ends["2026-11-14"], "2026-11-15T02:00:00Z")
        self.assertEqual(starts["2027-03-13"], "2027-03-13T23:00:00Z")
        self.assertEqual(starts["2027-04-10"], "2027-04-10T22:00:00Z")
        self.assertEqual(starts["2027-10-09"], "2027-10-09T22:00:00Z")
        for d in got:            # always 5–8 PM on the local clock, whatever the season
            s, e = utc(d["start"]).astimezone(CHI), utc(d["end"]).astimezone(CHI)
            self.assertEqual((s.date().isoformat(), s.hour, s.minute, e.hour), (d["ymd"], 17, 0, 20))

    def test_on_the_day_the_clocks_change(self):
        first_sunday = MonthlyRule(week_of_month=1, weekday=6, start=(17, 0), end=(20, 0))
        nov = upcoming_rule_dates(first_sunday, 1, CHI, datetime(2026, 10, 20, tzinfo=timezone.utc))
        self.assertEqual(nov[0], {"ymd": "2026-11-01", "start": "2026-11-01T23:00:00Z", "end": "2026-11-02T02:00:00Z"})
        second_sunday = MonthlyRule(week_of_month=2, weekday=6, start=(17, 0), end=(20, 0))
        mar = upcoming_rule_dates(second_sunday, 1, CHI, datetime(2027, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(mar[0], {"ymd": "2027-03-14", "start": "2027-03-14T22:00:00Z", "end": "2027-03-15T01:00:00Z"})

    def test_skip_dates_leave_a_month_out_and_the_count_stays(self):
        rule = MonthlyRule(week_of_month=2, weekday=5, start=(17, 0), end=(20, 0),
                           skip=frozenset({"2026-12-12", "2027-01-09"}))
        got = [d["ymd"] for d in upcoming_rule_dates(rule, 4, CHI, TODAY)]
        self.assertEqual(got, ["2026-10-10", "2026-11-14", "2027-02-13", "2027-03-13"])

    def test_last_weekday_of_the_month(self):
        last_saturday = MonthlyRule(week_of_month=-1, weekday=5, start=(17, 0), end=(20, 0))
        got = [d["ymd"] for d in upcoming_rule_dates(last_saturday, 5, CHI, TODAY)]
        self.assertEqual(got, ["2026-09-26", "2026-10-31", "2026-11-28", "2026-12-26", "2027-01-30"])

    def test_fifth_weekday_only_in_months_that_have_one(self):
        fifth_saturday = MonthlyRule(week_of_month=5, weekday=5, start=(10, 0), end=(12, 0))
        got = [d["ymd"] for d in upcoming_rule_dates(fifth_saturday, 3, CHI, TODAY)]
        self.assertEqual(got, ["2026-10-31", "2027-01-30", "2027-05-29"])

    def test_year_boundary_and_an_event_still_running(self):
        self.assertEqual(upcoming_rule_dates(BOOTH, 1, CHI, datetime(2026, 12, 20, tzinfo=timezone.utc))[0]["ymd"],
                         "2027-01-09")
        # Last Wednesday of September 2026, 7–8 PM CDT = Oct 1, 00:00–01:00 UTC. At 00:30 UTC it is already
        # October in UTC, but the meeting is still going on: it is still the "next" one.
        last_wed = MonthlyRule(week_of_month=-1, weekday=2, start=(19, 0), end=(20, 0))
        got = upcoming_rule_dates(last_wed, 2, CHI, datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc))
        self.assertEqual([d["ymd"] for d in got], ["2026-09-30", "2026-10-28"])
        # … and once it has ended it is gone
        done = upcoming_rule_dates(BOOTH, 1, CHI, datetime(2026, 10, 11, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(done[0]["ymd"], "2026-11-14")

    def test_recent_dates_on_request(self):
        got = [d["ymd"] for d in upcoming_rule_dates(BOOTH, 5, CHI, TODAY, include_recent_days=90)]
        self.assertEqual(got, ["2026-07-11", "2026-08-08", "2026-09-12", "2026-10-10", "2026-11-14"])

    def test_missing_or_earlier_end_means_one_hour(self):
        rule = MonthlyRule(week_of_month=2, weekday=5, start=(17, 0), end=(9, 0))
        d = upcoming_rule_dates(rule, 1, CHI, TODAY)[0]
        self.assertEqual(utc(d["end"]) - utc(d["start"]), utc("2026-10-10T23:00:00Z") - utc("2026-10-10T22:00:00Z"))

    def test_setting_values_written_every_way(self):
        for v, want in [(2, 2), ("2", 2), ("2nd", 2), ("second", 2), ("Segundo", 2), ("2.º", 2), ("last", -1),
                        ("último", -1), (-1, -1), (0, None), (6, None), ("often", None), (None, None), (True, None)]:
            self.assertEqual(M.week_of_month_value(v), want, v)
        for v, want in [("saturday", 5), ("Saturday", 5), ("SATURDAYS", 5), ("sábado", 5), ("Sábados", 5),
                        ("sabado", 5), ("miércoles", 2), ("funday", None), ("", None), (None, None)]:
            self.assertEqual(M.weekday_index(v), want, v)
        self.assertEqual(M.ymd_text(date(2027, 1, 9)), "2027-01-09")
        self.assertEqual(M.ymd_text("2027-01-09"), "2027-01-09")
        self.assertIsNone(M.ymd_text("2027-02-30"))
        self.assertIsNone(M.ymd_text("next month"))

    def test_committee_meeting_rule_unchanged(self):
        cfg = {"site": {"timezone": "America/Chicago"},
               "meeting": {"weekday": "wednesday", "week_of_month": 3, "start": "19:00", "end": "20:00"}}
        rule = M.meeting_rule(cfg["meeting"])
        self.assertEqual(rule, MonthlyRule(3, 2, (19, 0), (20, 0), frozenset()))
        got = upcoming_rule_dates(rule, 3, CHI, TODAY)
        self.assertEqual(got[0], {"ymd": "2026-10-21", "start": "2026-10-22T00:00:00Z", "end": "2026-10-22T01:00:00Z"})
        self.assertEqual(got[1]["start"], "2026-11-19T01:00:00Z")          # CST from November
        # anything unreadable keeps the old defaults: 3rd Wednesday, 19:00–20:00
        self.assertEqual(M.meeting_rule({"weekday": "someday", "week_of_month": "x", "start": "late"}),
                         MonthlyRule(3, 2, (19, 0), (20, 0), frozenset()))
        with mock.patch.object(M, "load_config", lambda: cfg):
            self.assertEqual(len(M.upcoming_meetings(12)), 12)


# --------------------------------------------------------------------------- build_data
class RecurringEventsBuild(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(B.T, "get_translator", lambda **kw: NoTranslator())
        p.start()
        self.addCleanup(p.stop)

    def test_ids_times_and_texts(self):
        ctx = ctx_with(CITYWIDE_YAML)
        evs = B.recurring_events(ctx)
        self.assertEqual(ctx.raw_problems, {})
        by_id = {e["id"]: e for e in evs}
        upcoming = [e for e in evs if B.ts(e["extra"]["end"]) >= TODAY.timestamp()]
        self.assertEqual([e["id"] for e in upcoming], [f"ev:recurring:citywide-dallas:{d}" for d in (
            "2026-10-10", "2026-11-14", "2026-12-12", "2027-01-09", "2027-02-13", "2027-03-13")])
        oct_, nov = by_id["ev:recurring:citywide-dallas:2026-10-10"], by_id["ev:recurring:citywide-dallas:2026-11-14"]
        self.assertEqual((oct_["extra"]["start"], oct_["extra"]["end"]), ("2026-10-10T22:00:00Z", "2026-10-11T01:00:00Z"))
        self.assertEqual((nov["extra"]["start"], nov["extra"]["end"]), ("2026-11-14T23:00:00Z", "2026-11-15T02:00:00Z"))
        self.assertEqual(oct_["date"], oct_["extra"]["start"])
        # dates of the last 90 days are kept too (for calendar subscribers; build_events marks them past)
        self.assertIn("ev:recurring:citywide-dallas:2026-09-12", by_id)
        self.assertIn("ev:recurring:citywide-dallas:2026-07-11", by_id)
        self.assertNotIn("ev:recurring:citywide-dallas:2026-06-13", by_id)
        self.assertEqual((oct_["source"], oct_["kind"], oct_["category"]), ("committee", "event", "recurring"))
        self.assertEqual(oct_["url"], "https://citywidedallasaa.org")
        ex = oct_["extra"]
        self.assertEqual((ex["all_day"], ex["city"], ex["state"], ex["online_url"], ex["recurring"], ex["series"]),
                         (False, "Dallas", "TX", None, True, "citywide-dallas"))
        self.assertIsNone(ex["flyer_url"])
        self.assertIsNone(ex["flyer_thumb"])
        self.assertEqual(ex["location"], "Lover's Lane United Methodist Church, 9200 Inwood Road, Dallas, TX 75220")
        self.assertEqual(oct_["i18n"]["title"], {"en": "GV/LV booth at CityWide Dallas", "es": "Mesa de GV/LV en CityWide Dallas"})
        self.assertEqual(oct_["i18n"]["summary"]["es"], "Nuestra mesa de literatura en CityWide Dallas.")
        self.assertEqual(oct_["i18n"]["recurrence_label"], {
            "en": "Every second Saturday of the month · 5:00\u2009–\u20098:00 PM",
            "es": "Cada segundo sábado del mes · 5:00–8:00\u00a0p.\u00a0m."})
        # the rule itself, in the shape of `meeting:` — the web pages write the line from it with the
        # committee meeting's own helpers (eleventy/filters/committee.js recurrenceText)
        self.assertEqual(ex["rule"], {"week_of_month": 2, "weekday": "saturday", "start": "17:00", "end": "20:00"})
        self.assertEqual(oct_["machine"], [])
        self.assertIs(oct_["is_new"], False)

    def test_recurrence_labels(self):
        # worded like the committee meeting's line ("Every third Wednesday of the month · 7:00 – 8:00 PM"),
        # time ranges as the browser writes them (thin spaces around the dash; none in "5:00–8:00 p. m.")
        rule = MonthlyRule(week_of_month=-1, weekday=6, start=(11, 30), end=(13, 0))
        self.assertEqual(B.recurrence_label(rule), {
            "en": "Every last Sunday of the month · 11:30 AM\u2009–\u20091:00 PM",
            "es": "Cada último domingo del mes · 11:30\u00a0a.\u00a0m.\u2009–\u20091:00\u00a0p.\u00a0m."})
        self.assertEqual(B.recurrence_label(MonthlyRule(1, 0, (9, 0), (11, 0)))["es"],
                         "Cada primer lunes del mes · 9:00–11:00\u00a0a.\u00a0m.")
        self.assertEqual(B.recurrence_label(MonthlyRule(3, 2, (19, 0), (20, 0)))["en"],
                         "Every third Wednesday of the month · 7:00\u2009–\u20098:00 PM")
        # a 23:30 start with no end: the one-hour rule stops at 23:59 (the same day) — so does extra.rule
        late = MonthlyRule(2, 5, (23, 30), (23, 30))
        self.assertEqual(B.rule_fields(late), {"week_of_month": 2, "weekday": "saturday", "start": "23:30", "end": "23:59"})
        self.assertEqual(B.rule_fields(rule)["week_of_month"], -1)

    def test_words_match_the_committee_meeting_line(self):
        """The rule words are the site's own (src/_i18n/committee.json), so the booth's line and the
        committee meeting's line on /meeting/ can never be worded differently."""
        strings = json.loads((ROOT / "src" / "_i18n" / "committee.json").read_text(encoding="utf-8"))
        for lang in ("en", "es"):
            self.assertEqual(B._RULE[lang], strings["committee.rule"][lang], lang)
            for n, word in B._ORD_WORDS[lang].items():
                key = "committee.ord." + ("last" if n == -1 else str(n))
                self.assertEqual(word, strings[key][lang], (lang, key))

    def test_a_skip_date_that_is_not_the_events_day_is_reported(self):
        """A likely slip (the Sunday, the 1st Saturday, the wrong month) would skip nothing: it is ignored
        as before, but the chair is told (status.json problems.recurring_events → Actions summary)."""
        ctx = ctx_with(CITYWIDE_YAML.replace("    skip_dates: []",
                                             '    skip_dates: ["2026-11-14", 2027-01-09, "2027-02-30", "garbage", '
                                             '"2026-10-11", "2026-12-05"]'))
        evs = B.recurring_events(ctx)
        days = [e["extra"]["start"][:10] for e in evs if B.ts(e["extra"]["end"]) >= TODAY.timestamp()]
        self.assertEqual(days, ["2026-10-10", "2026-12-12", "2027-02-13", "2027-03-13", "2027-04-10", "2027-05-08"])
        report = ctx.raw_problems["recurring_events"]
        self.assertIn("skip date “2026-10-11” is not the 2nd Saturday of its month — ignored "
                      "(that month's is 2026-10-10)", report)
        self.assertIn("skip date “2026-12-05” is not the 2nd Saturday of its month — ignored "
                      "(that month's is 2026-12-12)", report)
        for words in ("“2027-02-30” is not a date", "“garbage” is not a date"):
            self.assertIn(words, report)
        for fine in ("2026-11-14", "2027-01-09"):             # real 2nd Saturdays (quoted or not): no note
            self.assertNotIn(f"“{fine}”", report)
        # a "5th Saturday" rule and a month that has none; the last Friday of the month
        _, problems = B.recurring_specs(ctx_with("""
recurring_events:
  - {title: "Fifth", week_of_month: 5, weekday: saturday, start: "10:00", skip_dates: ["2026-11-28", "2026-10-31"]}
  - {title: "Last", week_of_month: -1, weekday: friday, start: "10:00", skip_dates: ["2026-10-30", "2026-10-23"]}
"""))
        text = " / ".join(problems)
        self.assertIn("“2026-11-28” is not the 5th Saturday of its month — ignored (that month has no 5th Saturday)", text)
        self.assertIn("“2026-10-23” is not the last Friday of its month — ignored (that month's is 2026-10-30)", text)
        self.assertNotIn("“2026-10-31”", text)
        self.assertNotIn("“2026-10-30”", text)

    def test_never_new_never_in_whats_new_never_a_past_event(self):
        ctx = ctx_with(CITYWIDE_YAML)
        with mock.patch.object(B, "ics_events", lambda c: []):
            evs = B.build_events(ctx)
        rec = [e for e in evs if e["category"] == "recurring"]
        self.assertEqual(len(rec), 9)                       # 6 ahead + 3 in the last 90 days
        for e in rec:
            e["first_seen"] = "2026-09-24T10:00:00Z"        # even if the robot "found" it today
            self.assertFalse(ctx.is_new(e, B.raw_source(e)), e["id"])
        wn = B.plan_whatsnew(ctx, {"events": evs})
        self.assertFalse([it["id"] for _, it in wn if it.get("category") == "recurring"])
        past = [e["extra"]["start"][:10] for e in rec if e["extra"]["past"]]
        self.assertEqual(past, ["2026-09-12", "2026-08-08", "2026-07-11"])
        self.assertEqual(sum(1 for e in rec if not e["extra"]["past"]), 6)

    def test_past_dates_do_not_push_out_real_past_events(self):
        ctx = ctx_with(CITYWIDE_YAML)
        old = [{"id": f"ev:manual:old-{i}", "source": "committee", "kind": "event", "title": f"Old {i}", "lang": "en",
                "date": f"2026-0{1 + i % 8}-0{1 + i % 9}", "category": "manual", "status": "ok", "extra": {}}
               for i in range(15)]
        with mock.patch.object(B, "ics_events", lambda c: []), \
                mock.patch.object(B.Ctx, "items", lambda self, name: old if name == "manual_events" else []):
            evs = B.build_events(ctx)
        past_manual = [e for e in evs if e["category"] == "manual" and e["extra"]["past"]]
        self.assertEqual(len(past_manual), B.PAST_EVENTS_KEEP)
        self.assertEqual(sum(1 for e in evs if e["category"] == "recurring" and e["extra"]["past"]), 3)

    def test_bad_entries_are_skipped_and_reported(self):
        ctx = ctx_with("""
recurring_events:
  - key: "citywide-dallas"
    title: "Booth"
    week_of_month: 2
    weekday: "saturday"
    start: "17:00"
    end: "20:00"
  - "just a line of text"
  - title: "No such day"
    week_of_month: 2
    weekday: "funday"
    start: "17:00"
  - title: "Week seven"
    week_of_month: 7
    weekday: "saturday"
    start: "17:00"
  - key: "citywide-dallas"
    title: "Same key again"
    week_of_month: 1
    weekday: "friday"
    start: "18:00"
  - title: "No start"
    week_of_month: 1
    weekday: "friday"
  - week_of_month: 1
    weekday: "friday"
    start: "18:00"
  - key: "Second Friday Workshop!"
    title: "Workshop"
    week_of_month: "2nd"
    weekday: "Viernes"
    start: "6:30 PM"
    end: "6 PM"
    url: "not a link"
    months_ahead: 99
    skip_dates: ["2026-10-09", "someday"]
""")
        with mock.patch.object(B, "ics_events", lambda c: []):
            evs = B.build_events(ctx)             # never raises
        series = {e["extra"]["series"] for e in evs if e["category"] == "recurring"}
        self.assertEqual(series, {"citywide-dallas", "second-friday-workshop"})
        report = ctx.raw_problems["recurring_events"]
        for words in ("entry 2", "entry 3", "funday", "entry 4", "week_of_month “7”", "used twice", "entry 6",
                      "start time", "entry 7", "needs a title", "not after the start", "not a web address",
                      "months_ahead", "someday"):
            self.assertIn(words, report)
        self.assertNotIn("entry 1 ", report)
        ws = [e for e in evs if e["extra"].get("series") == "second-friday-workshop" and not e["extra"]["past"]]
        self.assertEqual(len(ws), B.RECURRING_AHEAD)                 # months_ahead 99 → the default
        self.assertNotIn("2026-10-09", [e["extra"]["start"][:10] for e in ws])      # skipped
        first = ws[0]
        self.assertEqual(first["extra"]["start"], "2026-11-14T00:30:00Z")          # Fri Nov 13, 6:30 PM CST
        self.assertEqual(B.ts(first["extra"]["end"]) - B.ts(first["extra"]["start"]), 3600)   # one hour
        self.assertEqual(first["url"], "/events/")                   # bad link left out → our own page

    def test_not_a_list_is_reported_not_fatal(self):
        for cfg in ("recurring_events: oops", "recurring_events: 5"):
            ctx = ctx_with(cfg)
            self.assertEqual(B.recurring_events(ctx), [])
            self.assertIn("recurring_events", ctx.raw_problems)
        ctx = ctx_with("recurring_events:")
        self.assertEqual(B.recurring_events(ctx), [])
        self.assertEqual(ctx.raw_problems, {})
        ctx = ctx_with("meeting: {}")
        self.assertEqual(B.recurring_events(ctx), [])

    def test_a_crash_is_contained(self):
        ctx = ctx_with(CITYWIDE_YAML)
        with mock.patch.object(B, "recurring_events", side_effect=RuntimeError("boom")), \
                mock.patch.object(B, "ics_events", lambda c: []):
            evs = B.build_events(ctx)
        self.assertIn("recurring_events", ctx.raw_problems)
        self.assertFalse([e for e in evs if e["category"] == "recurring"])

    def test_only_english_given_spanish_is_machine_translated(self):
        class Stub:
            def translate(self, texts, src, tgt):
                return [(f"[{tgt}] {t}", True) for t in texts]

        ctx = ctx_with(CITYWIDE_YAML.replace('    title_es: "Mesa de GV/LV en CityWide Dallas"\n', "")
                       .replace('    summary_es: "Nuestra mesa de literatura en CityWide Dallas."\n', ""))
        with mock.patch.object(B.T, "get_translator", lambda **kw: Stub()):
            ev = B.recurring_events(ctx)[0]
        self.assertEqual(ev["i18n"]["title"]["es"], "[es] GV/LV booth at CityWide Dallas")
        self.assertEqual(ev["i18n"]["summary"]["es"], "[es] Our literature table at CityWide Dallas.")
        self.assertEqual(ev["machine"], ["es"])
        self.assertEqual(ev["i18n"]["recurrence_label"]["es"], "Cada segundo sábado del mes · 5:00–8:00\u00a0p.\u00a0m.")
        # translation not possible right now: the English shows in both, nothing marked "auto-translated"
        ev = B.recurring_events(ctx_with(CITYWIDE_YAML.replace('    title_es: "Mesa de GV/LV en CityWide Dallas"\n', "")))[0]
        self.assertEqual(ev["i18n"]["title"]["es"], "GV/LV booth at CityWide Dallas")
        self.assertEqual(ev["machine"], [])


# --------------------------------------------------------------------------- weekly e-mail
class DigestEmail(unittest.TestCase):
    def test_booth_listed_once_and_alone_it_is_not_news(self):
        from scripts.notify import send_digest as D
        ctx = ctx_with(CITYWIDE_YAML)
        with mock.patch.object(B.T, "get_translator", lambda **kw: NoTranslator()), \
                mock.patch.object(B, "ics_events", lambda c: []):
            events = B.build_events(ctx)
        now = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
        with mock.patch.object(D, "load_items", lambda name: events if name == "events" else []):
            data = D.collect(now, 7, 60, 6)      # 60 days ahead: Oct 10 AND Nov 14 are in the window
        booth = [e for e in data["events"] if e["category"] == "recurring"]
        self.assertEqual([e["id"] for e in booth], ["ev:recurring:citywide-dallas:2026-10-10"])
        self.assertEqual(D.total_count(data), 0)            # nothing new → no e-mail that week
        row = D.event_row(booth[0], "es", D.Links("https://example.org"))
        self.assertIn("cada mes", row["when"])
        self.assertEqual(row["url"], "https://citywidedallasaa.org")


if __name__ == "__main__":
    unittest.main()
