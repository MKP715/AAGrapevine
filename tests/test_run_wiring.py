"""How the daily update is wired together (no network: every session is faked, data/raw is a temp folder).

  * PageMemo      — one run asks the magazines' server for each page ONCE, whichever modules need it
                    (common.PoliteSession page memo; the crawler reuses those copies).
  * Schedule      — the 12:07 UTC quick run for the daily quote: the cron string in update.yml, the
                    plan step and the commit step agree, and run_all runs `quote` in quick mode.
  * QuoteMain     — quote.main() keeps `history` in data/raw (and a crash keeps it too); --only.
  * AudioMain     — audio_project.main() asks for a page once; a warning is not a failed source.

    python -m unittest tests.test_run_wiring -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import audio_project as A  # noqa: E402
from scripts.sync import common  # noqa: E402
from scripts.sync import quote as Q  # noqa: E402
from scripts.sync import run_all  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
GV_HOME = "https://www.aagrapevine.org/"
LV_HOME = "https://www.aalavina.org/"


class FakeResp:
    def __init__(self, url: str, text: str, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.url, self.text, self.status_code = url, text, status
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": ctype, "ETag": '"v1"'}
        self.encoding = "utf-8"
        self.is_redirect = False

    def iter_content(self, chunk_size=65536):
        yield self.content

    def close(self):
        pass


def fake_session(pages: dict[str, str]):
    """A PoliteSession whose network is a dict; `.calls` counts the requests per URL."""
    s = common.PoliteSession(min_delay=0.0, respect_robots=False)
    calls: Counter = Counter()

    def request(method, url, **kw):
        calls[url] += 1
        if url in pages:
            return FakeResp(url, pages[url])
        return FakeResp(url, "", status=404)

    s.s = mock.Mock()
    s.s.request.side_effect = request
    s.calls = calls
    return s


class TempRaw(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-wiring-"))
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        p = mock.patch.object(common, "RAW_DIR", self.raw)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def env(self, source: str) -> dict:
        return json.loads((self.raw / f"{source}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- page memo
class PageMemo(unittest.TestCase):
    def test_a_magazine_page_is_requested_once_per_run(self):
        home = "<html><body><a href='/x.pdf'>X</a></body></html>"
        s = fake_session({GV_HOME: home})
        self.assertEqual(s.get_text(GV_HOME), home)
        # the same page written another way (no www, no trailing slash, http, a fragment) is the same page
        for u in ("https://aagrapevine.org", "http://www.aagrapevine.org/", GV_HOME + "#quote-of-the-day"):
            self.assertEqual(s.get_text(u), home)
        self.assertEqual(sum(s.calls.values()), 1)
        self.assertEqual(s.memo_hits, 3)
        self.assertEqual(s.remembered(GV_HOME)["headers"]["ETag"], '"v1"')
        s.get_text(GV_HOME, reuse=False)                      # asked for fresh on purpose
        self.assertEqual(s.calls[GV_HOME], 2)

    def test_other_hosts_and_failures_are_not_remembered(self):
        s = fake_session({"https://feeds.example.com/rss": "<rss/>"})
        s.get_text("https://feeds.example.com/rss")
        s.get_text("https://feeds.example.com/rss")
        self.assertEqual(s.calls["https://feeds.example.com/rss"], 2)
        self.assertIsNone(s.get_text("https://www.aalavina.org/missing"))
        self.assertIsNone(s.get_text("https://www.aalavina.org/missing"))
        self.assertEqual(s.calls["https://www.aalavina.org/missing"], 2)     # a failure is asked again
        self.assertIsNone(common.PoliteSession.memo_key("https://example.org/"))

    def test_the_crawler_reuses_a_page_read_earlier_in_the_run(self):
        from scripts.sync import crawl as C
        home = ("<html><head><title>AA Grapevine</title></head><body>"
                "<a href='/sites/default/files/2026-09/Flyer.pdf'>Our flyer</a>"
                "<a href='/magazine'>Magazine</a></body></html>")
        s = fake_session({GV_HOME: home})
        s.get_text(GV_HOME)                                   # e.g. quote.py, earlier in the run
        st = C.empty_state()
        st["pages"][GV_HOME] = {"src": "hub", "depth": 0, "crawled_at": None}
        with mock.patch.object(C, "shared_session", return_value=s):
            cr = C.Crawler(st, minutes=1, details_cap=0, recheck_days=30, max_mb=5, max_pages=None,
                           dry_run=True, only_urls=None, use_sitemap=False)
            cr.crawl_page(GV_HOME, st["pages"][GV_HOME])
        pg = st["pages"][GV_HOME]
        self.assertEqual(s.calls[GV_HOME], 1, "the crawl must not ask for the home page again")
        self.assertEqual((pg["status"], pg["etag"], pg["title"]), (200, '"v1"', "AA Grapevine"))
        self.assertEqual(cr.c["pages_reused"], 1)
        self.assertEqual(cr.c["pages_fetched"], 0)
        self.assertTrue(any(k.lower().endswith("flyer.pdf") for k in st["pdfs"]), st["pdfs"].keys())


# --------------------------------------------------------------------------- schedule wiring
class Schedule(unittest.TestCase):
    QUOTE_CRON = "7 12 * * *"
    DAILY_CRON = "17 10 * * *"

    def setUp(self):
        self.wf = yaml.safe_load((ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8"))
        # PyYAML reads the key `on:` as the boolean True
        self.on = self.wf.get("on", self.wf.get(True))
        self.steps = [st for job in self.wf["jobs"].values() for st in job.get("steps", [])]

    def step(self, name: str) -> str:
        found = [st.get("run") or "" for st in self.steps if st.get("name") == name]
        self.assertEqual(len(found), 1, f"one step named {name!r}")
        return found[0]

    def test_both_schedules(self):
        self.assertEqual({c["cron"] for c in self.on["schedule"]}, {self.DAILY_CRON, self.QUOTE_CRON})

    def test_plan_and_commit_steps_use_the_same_string(self):
        self.assertIn(f'QUOTE_CRON="{self.QUOTE_CRON}"', self.step("Decide what to sync"))
        self.assertIn(f'"${{SCHEDULE:-}}" = "{self.QUOTE_CRON}"', self.step("Commit refreshed data"))

    def test_run_all_runs_the_quote_in_quick_mode_before_the_crawl(self):
        self.assertIn("quote", run_all.QUICK_MODULES)
        self.assertLess(run_all.MODULES.index("quote"), run_all.MODULES.index("crawl"))
        self.assertEqual(run_all.MODULES[-1], "crawl")


# --------------------------------------------------------------------------- quote.main
class QuoteMain(TempRaw):
    TODAY = date(2026, 9, 25)

    def pages_dir(self) -> Path:
        d = self.tmp / "html"
        d.mkdir(exist_ok=True)
        for pub in ("gv", "lv"):
            shutil.copy(FIX / "quote" / f"{pub}_home.html", d / f"{pub}.html")
        return d

    def run_main(self, *args: str) -> None:
        with mock.patch.object(Q, "local_today", return_value=self.TODAY), \
                mock.patch.object(Q, "shared_session", return_value=fake_session({})):
            Q.main(["--html-dir", str(self.pages_dir()), *args])

    def test_history_is_saved_and_survives_a_crash(self):
        self.run_main()
        env = self.env("quote")
        self.assertTrue(env["ok"])
        self.assertEqual(sorted(env["history"]), ["gv", "lv"])
        self.assertEqual(env["history"]["gv"][0]["date"], "2026-09-25")
        self.assertEqual(env["stats"]["requests"], 0)

        def crash():
            raise RuntimeError("boom")
        with self.assertLogs("quote", level="ERROR"):         # (and keeps the traceback out of the test output)
            common.run_module("quote", crash)
        env2 = self.env("quote")
        self.assertFalse(env2["ok"])
        self.assertIn("boom", env2["error"])
        self.assertEqual(env2["history"], env["history"], "a crash keeps the history")
        self.assertEqual([i["id"] for i in env2["items"]], [i["id"] for i in env["items"]])

    def test_only_one_publication(self):
        self.run_main()
        before = {(i.get("extra") or {}).get("pub"): i for i in self.env("quote")["items"]}
        calls = []
        prev = self.env("quote")
        res = Q.collect(lambda u: calls.append(u) or None, prev, self.TODAY,
                        {"sources": {"grapevine": {}, "lavina": {}}}, only="lv")
        self.assertEqual(calls, [LV_HOME], "--only lv asks for La Viña's page only")
        by = {(i.get("extra") or {}).get("pub"): i for i in res["items"]}
        self.assertEqual(by["gv"]["id"], before["gv"]["id"], "Grapevine keeps its quote")
        self.assertEqual(by["lv"]["id"], before["lv"]["id"], "La Viña's page failed: its quote is kept")
        self.assertEqual(len(res["errors"]), 1)
        self.assertTrue(res["errors"][0].startswith("lv:"))


# --------------------------------------------------------------------------- audio_project.main
class AudioMain(TempRaw):
    def pages_dir(self) -> Path:
        d = self.tmp / "audio"
        d.mkdir(exist_ok=True)
        st = A.settings()
        for url, name in ((st["gv"]["page"], "gv_audio_portal.html"), (st["lv"]["page"], "lv_graba_tu_historia.html"),
                          (st["lv"]["instructions"], "lv_instrucciones.html")):
            shutil.copy(FIX / "audio_project" / name, d / A._file_key(url))
        return d

    def run_main(self, pages: Path, collect=None) -> None:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(A, "shared_session", return_value=fake_session({})))
            if collect:
                stack.enter_context(mock.patch.object(A, "collect", collect))
            A.main(["--html-dir", str(pages)])

    def test_each_page_is_asked_for_once(self):
        real, seen = A.collect, {}
        gv_page = A.settings()["gv"]["page"]

        def twice(fetch, prev, cfg=None):
            seen["pair"] = (fetch(gv_page) is not None, fetch(gv_page))
            return real(fetch, prev, cfg)
        self.run_main(self.pages_dir(), twice)
        self.assertEqual(seen["pair"], (True, None), "a page this run already asked for is not asked again")

    def test_a_warning_still_counts_as_updated(self):
        pages = self.pages_dir()
        self.run_main(pages)
        self.assertTrue(self.env("audio_project")["ok"])
        (pages / A._file_key(A.settings()["lv"]["instructions"])).unlink()      # only the steps page fails
        self.run_main(pages)
        env = self.env("audio_project")
        self.assertTrue(env["ok"], "both main pages were read: the source is up to date")
        self.assertIsNone(env.get("error"))
        self.assertEqual(len(env["stats"]["warnings"]), 1)
        self.assertIn("instructions", env["stats"]["warnings"][0])


if __name__ == "__main__":
    unittest.main()
