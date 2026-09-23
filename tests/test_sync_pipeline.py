"""Offline tests for the sync pipeline's safety rules (no network: every session is faked,
data/raw and the cache folders are redirected to a temporary directory).

    python -m unittest tests.test_sync_pipeline -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import common  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


class TempRaw(unittest.TestCase):
    """Each test gets its own data/raw (load_raw/save_raw read common.RAW_DIR at call time)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-test-"))
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        p = mock.patch.object(common, "RAW_DIR", self.raw)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def env(self, source: str) -> dict:
        return json.loads((self.raw / f"{source}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- merge_items
class MergeItems(unittest.TestCase):
    def _pair(self):
        old = [{"id": "a", "title": "A", "image": "/old.png", "summary": "old", "date": "2026-01-01",
                "first_seen": "2026-01-01T00:00:00Z", "last_seen": iso(datetime.now(timezone.utc)),
                "extra": {"expires": "2026-09-01", "link": "https://old.example"}}]
        new = [{"id": "a", "title": "A", "image": None, "summary": "", "date": None,
                "first_seen": None, "last_seen": None, "extra": {"expires": None, "link": None}}]
        return old, new

    def test_default_mode_keeps_previous_values(self):
        old, new = self._pair()
        merged, added = common.merge_items(old, new)
        self.assertEqual(added, 0)
        self.assertEqual(merged[0]["image"], "/old.png")
        self.assertEqual(merged[0]["extra"]["expires"], "2026-09-01")

    def test_authoritative_mode_takes_the_new_item_as_is(self):
        old, new = self._pair()
        merged, _ = common.merge_items(old, new, authoritative=True)
        it = merged[0]
        self.assertEqual(it["first_seen"], "2026-01-01T00:00:00Z")      # history kept
        self.assertEqual(it["last_seen"], old[0]["last_seen"])          # weekly throttle kept
        self.assertIsNone(it["image"])
        self.assertIsNone(it["date"])
        self.assertIsNone(it["extra"]["expires"])
        self.assertIsNone(it["extra"]["link"])


# --------------------------------------------------------------------------- raw file I/O
class RawFiles(TempRaw):
    def test_corrupt_raw_file_is_kept_aside_and_reported(self):
        common.save_raw("demo", [{"id": "x", "title": "X", "first_seen": "2026-01-01T00:00:00Z"}])
        p = self.raw / "demo.json"
        p.write_text(p.read_text(encoding="utf-8")[:-5], encoding="utf-8")   # truncated file
        env = common.load_raw("demo")
        self.assertEqual(env["items"], [])
        backups = list(self.raw.glob("demo.json.corrupt-*"))
        self.assertEqual(len(backups), 1, "the unreadable file must be kept for recovery")
        self.assertFalse(p.exists())
        common.save_raw("demo", [{"id": "y", "title": "Y"}], ok=True)
        env = self.env("demo")
        self.assertFalse(env["ok"])
        self.assertIn("unreadable", env["error"])
        self.assertEqual([i["id"] for i in env["items"]], ["y"])
        common.save_raw("demo", [{"id": "y", "title": "Y"}], ok=True)     # reported once, then healthy
        self.assertTrue(self.env("demo")["ok"])

    def test_write_json_accepts_dates(self):
        common.write_json(self.raw / "d.json", {"when": date(2026, 9, 1), "tags": {"b", "a"}})
        self.assertEqual(json.loads((self.raw / "d.json").read_text(encoding="utf-8")),
                         {"when": "2026-09-01", "tags": ["a", "b"]})


# --------------------------------------------------------------------------- polite session
class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class CrossHostPacing(unittest.TestCase):
    def test_both_magazine_hosts_share_one_delay(self):
        clock = FakeClock()
        stamps = []

        class Resp:
            status_code, headers, text, encoding = 200, {}, "<html/>", "utf-8"

        def fake_request(_self, method, url, **kw):
            stamps.append((clock.t, url))
            return Resp()

        with mock.patch.object(common.time, "monotonic", clock.monotonic), \
                mock.patch.object(common.time, "sleep", clock.sleep), \
                mock.patch("requests.Session.request", fake_request):
            s = common.PoliteSession(min_delay=5.0, respect_robots=False)
            for u in ["https://www.aagrapevine.org/magazine", "https://www.aalavina.org/la-revista",
                      "https://aalavina.org/revista/a", "https://www.aagrapevine.org/magazine/b",
                      "https://feeds.example.com/rss"]:
                s.get(u)
        gaps = [b[0] - a[0] for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(gaps[:3], [5.0, 5.0, 5.0])
        self.assertEqual(gaps[3], 0.0, "an unrelated host is not delayed by the magazine sites")
        self.assertEqual(common.PoliteSession.pace_key("https://WWW.AALAVINA.ORG/x"),
                         common.PoliteSession.pace_key("https://www.aagrapevine.org/y"))


# --------------------------------------------------------------------------- announcements
class Announcements(TempRaw):
    def setUp(self):
        super().setUp()
        from scripts.sync import announcements as A
        self.A = A
        self.ann, self.evs = self.tmp / "announcements", self.tmp / "events"
        self.ann.mkdir()
        self.evs.mkdir()
        for name, val in (("ANN_DIR", self.ann), ("EVENTS_DIR", self.evs)):
            p = mock.patch.object(A, name, val)
            p.start()
            self.addCleanup(p.stop)

    def test_removed_header_lines_disappear(self):
        (self.ann / "welcome.md").write_text(
            "---\ntitle: Welcome\nexpires: 2026-09-01\nurl: https://old.example/link\nimage: /assets/img/old.png\n"
            "---\nBody text one.\n", encoding="utf-8")
        (self.evs / "2027-03-14-assembly.md").write_text(
            "---\ntitle: Assembly\nstart: 2027-03-14\nonline_url: https://zoom.us/j/111\nlocation: Tyler, TX\n"
            "---\nOld body.\n", encoding="utf-8")
        self.A.main([])
        first = self.env("announcements")["items"][0]["first_seen"]
        (self.ann / "welcome.md").write_text("---\ntitle: Welcome\n---\nBody text one.\n", encoding="utf-8")
        (self.evs / "2027-03-14-assembly.md").write_text("---\ntitle: Assembly\nstart: 2027-03-14\n---\n",
                                                          encoding="utf-8")
        self.A.main([])
        a = self.env("announcements")["items"][0]
        e = self.env("manual_events")["items"][0]
        self.assertEqual(a["first_seen"], first)
        self.assertIsNone(a["image"])
        self.assertIsNone(a["extra"]["expires"])
        self.assertIsNone(a["extra"]["link"])
        self.assertEqual(a["url"], "/announcements/#welcome")
        self.assertIsNone(e["extra"]["online_url"])
        self.assertIsNone(e["extra"]["location"])
        self.assertEqual(e["extra"]["body_md"], "")

    def test_extension_case_and_other_files(self):
        (self.ann / "Spring-Assembly.MD").write_text("---\ntitle: Spring\n---\nHi.\n", encoding="utf-8")
        (self.ann / "notes.txt").write_text("not markdown", encoding="utf-8")
        (self.ann / "README.md").write_text("# help", encoding="utf-8")
        (self.ann / ".gitkeep").write_text("", encoding="utf-8")
        self.assertEqual([p.name for p in self.A.content_files(self.ann)], ["Spring-Assembly.MD"])
        self.A.main([])
        env = self.env("announcements")
        self.assertEqual(len(env["items"]), 1)
        self.assertEqual(env["stats"]["problems"], 1)
        self.assertIn("notes.txt", env["stats"]["errors"][0])


# --------------------------------------------------------------------------- external events
class ExternalEvents(TempRaw):
    def _run(self, sitemap_ok: bool, listed: tuple[str, ...]):
        from scripts.sync import events_external as E
        d1 = (date.today() + timedelta(days=20)).isoformat()
        d2 = (date.today() + timedelta(days=200)).isoformat()
        urls = {"dallas": f"https://www.aagrapevine.org/get-involved/events/{d1}/dallas-roundup",
                "tyler": f"https://www.aagrapevine.org/get-involved/events/{d2}/tyler-assembly"}
        titles = {urls["dallas"]: ("Dallas Roundup", d1), urls["tyler"]: ("Tyler Assembly", d2)}
        index = ("<sitemapindex><sitemap><loc>https://www.aagrapevine.org/sitemap.xml?page=1</loc>"
                 f"<lastmod>{'-'.join(listed)}</lastmod></sitemap></sitemapindex>")
        page = "<urlset>" + "".join(f"<url><loc>{urls[k]}</loc></url>" for k in listed) + "</urlset>"
        cal = ('<script type="application/json" data-drupal-selector="drupal-settings-json">'
               + json.dumps({"fullCalendarView": [{"calendar_options": json.dumps(
                   {"events": [{"url": urls["dallas"]}]})}]}) + "</script>")

        class Resp:
            def __init__(self, text):
                self.status_code, self.text, self.encoding = 200, text, "utf-8"

        class Http:
            requests_made = 0

            def get_text(self, url, **kw):
                if url.endswith("/sitemap.xml"):
                    return index if sitemap_ok else None
                if "sitemap.xml?page=1" in url:
                    return page
                if "calendar" in url or "calendario" in url:
                    return cal
                return None

            def get(self, url, **kw):
                title, day = titles[url]
                ld = {"@type": "Event", "name": title, "startDate": f"{day}T12:00:00+0000"}
                return Resp(f'<script type="application/ld+json">{json.dumps(ld)}</script><h1>{title}</h1>'
                            '<div class="field--name-field-event-location"><div class="field__item">Dallas, TX'
                            '</div></div>')

        with mock.patch.object(E, "shared_session", lambda: Http()):
            E.main([])
        env = self.env("events_external")
        return {i["title"]: i["first_seen"] for i in env["items"]}

    def test_calendar_fallback_keeps_known_events(self):
        day1 = self._run(True, ("dallas", "tyler"))
        self.assertEqual(set(day1), {"Dallas Roundup", "Tyler Assembly"})
        day2 = self._run(False, ())                      # sitemap down → calendar lists only Dallas
        self.assertEqual(day2, day1, "known upcoming events stay, with their first_seen")
        day3 = self._run(True, ("dallas",))              # a complete sitemap without Tyler → removed
        self.assertEqual(set(day3), {"Dallas Roundup"})


# --------------------------------------------------------------------------- articles
HUB = """<html><body><div class="main-region"><div class="large-eyebrow">October 2026</div>
<h1>Loneliness</h1></div>
<div class="node--type-article view-mode-teaser"><h3><a href="/magazine/2026/oct/{slug}">My Story</a></h3>
<div class="author">By: Jake B. | Tyler, Texas</div><div class="field--name-body"><p>A teaser sentence that is
long enough to be kept here.</p></div><img src="/sites/default/files/card.jpg"></div></body></html>"""
ARTICLE = ("""<html><body><article class="node--type-article"><h1>My Story</h1>
<div class="article-publication-date">October 2026 | Loneliness | Our Personal Stories</div>
<div class="author">By: Jake B.</div><p>""" + "x " * 30 + "</p></article></body></html>")


class Articles(TempRaw):
    def setUp(self):
        super().setUp()
        from scripts.sync import articles as AR
        self.AR = AR
        p = mock.patch.object(AR, "THUMB_DIR", self.tmp / "thumbs")
        p.start()
        self.addCleanup(p.stop)

    def _run(self, hub_slug: str, article_code: int) -> list:
        calls = []

        class Resp:
            def __init__(self, code, text=""):
                self.status_code, self.text, self.encoding, self.headers, self.content = \
                    code, text, "utf-8", {}, text.encode()

        class Http:
            requests_made = 0

            def get_text(self, url, **kw):
                return HUB.replace("{slug}", hub_slug) if url.endswith("/magazine") else None

            def get(self, url, **kw):
                calls.append(url)
                return Resp(article_code, ARTICLE if article_code == 200 else "")

        with mock.patch.object(self.AR, "shared_session", lambda: Http()):
            self.AR.main(["--only", "gv"])
        return calls

    def _item(self, slug="my-story"):
        env = self.env("articles")
        it = next(i for i in env["items"] if i["url"].endswith(slug))
        return it, env["detail_state"].get(it["id"])

    def _age_state(self, iid, days):
        """Pretend the last check (and first 404) happened `days` ago."""
        env = self.env("articles")
        old = iso(datetime.now(timezone.utc) - timedelta(days=days))
        st = env["detail_state"][iid]
        st["at"] = old
        if "missing_since" in st:
            st["missing_since"] = old
        common.write_json(self.raw / "articles.json", env)

    def test_404_while_the_hub_links_it_does_not_hide_it(self):
        self._run("my-story", 404)
        it, st = self._item()
        self.assertEqual(it["status"], "ok")
        self.assertNotIn("gone", st)

    def test_hub_listing_revives_an_article_marked_gone(self):
        self._run("my-story", 200)
        env = self.env("articles")
        it = env["items"][0]
        it["status"] = "gone"                               # as the old code left it after one 404
        env["detail_state"] = {it["id"]: {"tries": 1, "at": it["first_seen"], "gone": True, "ok": False}}
        common.write_json(self.raw / "articles.json", env)
        self._run("my-story", 200)
        it, st = self._item()
        self.assertEqual(it["status"], "ok")
        self.assertFalse((st or {}).get("gone"))

    def test_gone_needs_two_404s_a_week_apart(self):
        self._run("my-story", 200)                            # known, but make it need a re-check
        env = self.env("articles")
        iid = env["items"][0]["id"]
        env["items"][0]["extra"]["section"] = None            # incomplete → retried
        env["detail_state"] = {iid: {"tries": 1, "at": env["items"][0]["first_seen"], "ok": False}}
        common.write_json(self.raw / "articles.json", env)
        self._age_state(iid, 8)
        self._run("next-story", 404)                          # hub moved on; first 404
        it, st = self._item()
        self.assertEqual(it["status"], "ok")
        self.assertIn("missing_since", st)
        self.assertEqual(self._run("next-story", 404), [], "no re-check before a week has passed")
        self._age_state(iid, 8)
        self._run("next-story", 404)                          # second 404, a week later
        it, st = self._item()
        self.assertEqual(it["status"], "gone")
        self.assertTrue(st["gone"])


# --------------------------------------------------------------------------- drive
class Drive(TempRaw):
    def test_private_names_never_stored_and_last_seen_stable(self):
        from scripts.sync import drive as D
        from scripts.sync.drive_listing import FOLDER_MIME, Entry, Listing
        tree = {
            "ROOT": [Entry("P77", "2027-2028_Panel77_GVLV", FOLDER_MIME, is_folder=True)],
            "P77": [Entry("NOTES", "notes", FOLDER_MIME, is_folder=True)],
            "NOTES": [Entry("f1", "2027-01-20 Minutes.pdf", "application/pdf", modified_text="Jan 20"),
                      Entry("f2", "PRIVATE - Maria G. sponsor phone list.docx",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                      Entry("f3", "GVR sign-up (Responses)", "application/vnd.google-apps.spreadsheet")],
        }

        class FakeLister:
            mode, api_error, requests_made, http = "html", None, 0, None

            class html:
                shortcuts_resolved = 0
                http = None

            def list(self, fid):
                return Listing(True, [Entry(**vars(e)) for e in tree[fid]])

        cfg = {"drive": {"root_folder_id": "ROOT", "min_panel": 77,
                         "exclude_name_contains": ["(Responses)", "PRIVATE"]}}
        with mock.patch.object(D, "DriveLister", lambda **kw: FakeLister()), \
                mock.patch.object(D, "load_config", lambda: cfg), \
                mock.patch.object(D, "check_forms", lambda items, http: {}):
            D.main([])
            text = (self.raw / "drive.json").read_text(encoding="utf-8")
            first = self.env("drive")
            with mock.patch.object(D, "now_iso", lambda: "2099-01-01T00:00:00Z"):
                D.main([])
        self.assertNotIn("Maria", text)
        self.assertNotIn("sign-up", text)
        self.assertEqual(first["stats"]["excluded"], 2)
        self.assertEqual(sum(first["stats"]["excluded_by_reason"].values()), 2)
        second = self.env("drive")
        self.assertEqual(first["items"][0]["last_seen"], second["items"][0]["last_seen"])


# --------------------------------------------------------------------------- meeting + build_data
class MeetingConfig(unittest.TestCase):
    def test_times_in_every_shape(self):
        import yaml
        from scripts.sync.meeting import parse_hhmm
        cases = {"19:00": (19, 0), '"19:00"': (19, 0), "7:30": (7, 30), '"7:00 PM"': (19, 0),
                 "19": (19, 0), "19:00:00": (19, 0), '"nonsense"': (19, 0), '"25:00"': (19, 0)}
        for raw, want in cases.items():
            self.assertEqual(parse_hhmm(yaml.safe_load(f"x: {raw}")["x"], (19, 0)), want, raw)

    def test_unquoted_time_in_config_works(self):
        import yaml
        from scripts.sync import meeting as M
        cfg = yaml.safe_load("site: {timezone: America/Chicago}\nmeeting:\n  weekday: wednesday\n"
                             "  week_of_month: 3\n  start: 19:00\n  end: 20:00\n")
        with mock.patch.object(M, "load_config", lambda: cfg):
            ms = M.upcoming_meetings(2)
        self.assertEqual(len(ms), 2)
        start = datetime.fromisoformat(ms[0]["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(ms[0]["end"].replace("Z", "+00:00"))
        self.assertEqual(end - start, timedelta(hours=1))
        self.assertIn(start.astimezone(timezone.utc).hour, (0, 1))   # 19:00 Central = 00:00/01:00 UTC


class BuildData(TempRaw):
    def test_district_extra_date_is_written_as_text(self):
        from scripts.sync import build_data as B
        content = self.tmp / "content"
        content.mkdir()
        (content / "districts.yml").write_text(
            "districts:\n  - number: 7\n    name: Tyler\n    updated: 2026-09-01\n", encoding="utf-8")

        class I18nStub:
            def want(self, *a, **k):
                pass

        with mock.patch.object(B, "CONTENT_DIR", content):
            ctx = B.Ctx(offline=True)
            rows = B.build_districts(ctx, I18nStub())
        self.assertEqual(rows[0]["updated"], "2026-09-01")
        common.write_json(self.tmp / "districts.json", {"items": rows})

    def test_bad_meeting_config_is_reported_not_fatal(self):
        from scripts.sync import build_data as B
        ctx = B.Ctx(offline=True)
        with mock.patch.object(B, "committee_meetings", side_effect=ValueError("bad time")), \
                mock.patch.object(B, "ics_events", lambda c: []):
            evs = B.build_events(ctx)
        self.assertIn("meeting", ctx.raw_problems)
        self.assertIsInstance(evs, list)


# --------------------------------------------------------------------------- weekly open, crawl
class WeeklyOpen(unittest.TestCase):
    def test_join_details_carried_other_fields_not(self):
        from scripts.sync.weekly_open import ITEM_ID, carry_join_details
        prev = [{"id": ITEM_ID, "extra": {"zoom_id": "1", "passcode": "p", "day": "Wednesday", "weekday": 2,
                                          "time": "Noon", "start_local": "12:00", "player_url": "https://old"}}]
        item = {"id": ITEM_ID, "extra": {"zoom_id": "2", "day": "Wednesday", "weekday": 2, "time": "Noon",
                                         "start_local": "12:00"}}
        self.assertEqual(carry_join_details(item, prev), ["passcode"])
        self.assertEqual(item["extra"]["passcode"], "p")
        self.assertEqual(item["extra"]["zoom_id"], "2")
        self.assertNotIn("player_url", item["extra"])


class CrawlMerge(unittest.TestCase):
    def test_fresh_fields_win_and_last_seen_is_throttled(self):
        from scripts.sync import crawl as C
        now = datetime.now(timezone.utc)
        old = [{"id": "pdf:1", "image": "/assets/cache/pdf/x.webp", "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": iso(now - timedelta(days=2)),
                "extra": {"thumb": "/assets/cache/pdf/x.webp", "referrers": [{"url": "u", "title": "t"}],
                          "event_date": "2026-05-01", "orphan": False, "size_bytes": 10}}]
        new = [{"id": "pdf:1", "image": None, "first_seen": "2026-01-01T00:00:00Z",
                "extra": {"thumb": None, "referrers": [], "event_date": None, "orphan": True, "size_bytes": None}}]
        merged, _ = common.merge_items(old, new)
        C.reapply_fresh_fields(merged, new)
        ex = merged[0]["extra"]
        self.assertIsNone(merged[0]["image"])
        self.assertEqual((ex["thumb"], ex["referrers"], ex["event_date"], ex["orphan"]), (None, [], None, True))
        self.assertEqual(ex["size_bytes"], 10, "a transient HEAD failure keeps the known size")
        cur = iso(now - timedelta(days=2))
        self.assertFalse(C._last_seen_changed(cur, iso(now)))                        # < a week newer
        self.assertTrue(C._last_seen_changed(cur, iso(now - timedelta(days=30))))    # moved back
        self.assertTrue(C._last_seen_changed(iso(now - timedelta(days=9)), iso(now)))


# --------------------------------------------------------------------------- podcasts, instagram
def png_bytes(size=(1200, 1200)) -> bytes:
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", size, (120, 40, 90)).save(buf, "PNG")
    return buf.getvalue()


class PodcastArt(TempRaw):
    def test_one_small_local_copy_per_artwork(self):
        from PIL import Image
        from scripts.sync import podcasts as P
        art = self.tmp / "pod"
        art.mkdir()
        (art / "stale.webp").write_bytes(b"x")
        data = png_bytes()
        calls = []

        class Resp:
            status_code, headers, content = 200, {"Content-Type": "image/png"}, data

        class Http:
            def get(self, url, **kw):
                calls.append(url)
                return Resp()

        items = [{"id": f"pod:gv:{n}", "image": "https://artwork.example/a.jpeg", "extra": {}} for n in range(3)]
        items.append({"id": "pod:wo:1", "image": "https://artwork.example/b.jpeg", "extra": {"thumb": "/old"}})
        shows = [{"key": "gv", "image": "https://artwork.example/a.jpeg"}]
        with mock.patch.object(P, "ART_DIR", art):
            stats = P.attach_art(Http(), items, shows, dry_run=False)
            self.assertEqual(sorted(calls), ["https://artwork.example/a.jpeg", "https://artwork.example/b.jpeg"])
            thumb = items[0]["extra"]["thumb"]
            self.assertTrue(thumb.startswith("/assets/cache/pod/") and thumb.endswith(".webp"))
            self.assertEqual(shows[0]["thumb"], thumb)
            self.assertNotEqual(items[3]["extra"]["thumb"], thumb)
            with Image.open(art / thumb.rsplit("/", 1)[-1]) as im:
                self.assertLessEqual(max(im.size), 480)
            self.assertFalse((art / "stale.webp").exists())
            self.assertEqual(stats["artwork"], 2)
            calls.clear()
            P.attach_art(Http(), items, shows, dry_run=False)
            self.assertEqual(calls, [], "artwork is downloaded once, then reused")


class InstagramAvatar(unittest.TestCase):
    def test_freshness_comes_from_the_envelope_not_file_time(self):
        from scripts.sync import instagram as I
        tmp = Path(tempfile.mkdtemp(prefix="gv-ig-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / f"{I.AVATAR_PREFIX}gv.webp").write_bytes(b"x")     # fresh mtime, like a new checkout
        calls = []

        def fake_download(fx, url, dest, *a, **k):
            calls.append(url)
            return True

        with mock.patch.object(I, "THUMB_DIR", tmp), mock.patch.object(I, "download_image", fake_download):
            prof = {"avatar_url": "https://cdn/a.jpg"}
            I.ensure_avatar(None, "gv", prof)                        # never checked → refresh
            self.assertEqual(len(calls), 1)
            self.assertIn("_avatar_checked", prof)
            I.ensure_avatar(None, "gv", dict(prof))                  # checked just now → no request
            self.assertEqual(len(calls), 1)
            old = dict(prof, _avatar_checked=iso(datetime.now(timezone.utc) - timedelta(days=8)))
            I.ensure_avatar(None, "gv", old)                         # a week later → refresh
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
