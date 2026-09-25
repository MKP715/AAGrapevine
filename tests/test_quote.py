"""scripts/sync/quote.py — Grapevine's Daily Quote and La Viña's Cita Diaria (home pages → data/raw/quote.json →
data/site/quote.json). Fixtures: tests/fixtures/quote/ (the two "quote of the day" views of each home page,
trimmed from the live pages of 2026-09-25). No network: pages are passed in as strings."""
from __future__ import annotations

import re
import unittest
from datetime import date
from pathlib import Path

from scripts.sync import quote as Q

FIX = Path(__file__).parent / "fixtures" / "quote"
TODAY = date(2026, 9, 25)
CFG = {"sources": {"grapevine": {"base": "https://www.aagrapevine.org"}, "lavina": {"base": "https://www.aalavina.org"}}}


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def fetcher(pages: dict[str, str | None]):
    """fetch(url) for collect(): the fixture of the publication whose page is asked for; counts requests."""
    conf = Q.settings(CFG)
    by_url = {conf[p]["page"]: html for p, html in pages.items()}
    calls: list[str] = []

    def fetch(url: str) -> str | None:
        calls.append(url)
        return by_url.get(url)
    fetch.calls = calls
    return fetch


class ParseTest(unittest.TestCase):
    def parse(self, pub: str, html: str, today: date = TODAY) -> dict | None:
        s = Q.settings(CFG)[pub]
        return Q.parse_quote(html, s["page"], s["lang"], today, s["signup_re"])

    def test_grapevine(self):
        q = self.parse("gv", page("gv_home.html"))
        self.assertEqual(q["block"], "teaser")
        self.assertEqual(q["heading"], "Grapevine Daily Quote September 25")
        self.assertEqual(q["date"], "2026-09-25")
        self.assertTrue(q["text"].startswith("During his first AA years every AA has had plenty"))
        self.assertTrue(q["text"].endswith("And gratitude as well."))
        self.assertNotRegex(q["text"], r"^[“\"]|[”\"]$")           # the outer quotation marks are gone
        self.assertNotIn("  ", q["text"])
        self.assertEqual(q["attribution"], "AA Co-Founder, Bill W., September 1945")
        self.assertEqual(q["source"], "“’Rules’ Dangerous but Unity Vital”, The Language of the Heart")
        self.assertEqual(q["source_lang"], "en")
        self.assertTrue(q["signup_url"].startswith("https://visitor.r20.constantcontact.com/manage/optin?v="))

    def test_la_vina(self):
        q = self.parse("lv", page("lv_home.html"))
        self.assertEqual(q["heading"], "Cita Diaria con La Viña Septiembre 25")
        self.assertEqual(q["date"], "2026-09-25")
        self.assertTrue(q["text"].startswith("Mi nuevo amigo me preguntó"))
        self.assertTrue(q["text"].endswith("una forma de vivir sin la bebida."))   # “…bebida”. → …bebida.
        self.assertEqual(q["attribution"], "“La novia de nadie”. MORENO VALLEY, CALIFORNIA, DICIEMBRE DE 1992")
        self.assertEqual(q["source"], "I Am Responsible")
        self.assertEqual(q["source_lang"], "en")           # an English book title inside a Spanish quote
        # La Viña's own list — not Grapevine's, which the embed near the top of the page links on both sites
        self.assertIn("/d.jsp?", q["signup_url"])
        self.assertNotIn("manage/optin", q["signup_url"])

    def test_embed_fallback_without_teaser(self):
        html = re.sub(r"(?s)<article .*?</article>", "", page("lv_home.html"))
        q = self.parse("lv", html)
        self.assertEqual(q["block"], "embed")
        self.assertEqual(q["date"], "2026-09-25")
        self.assertTrue(q["text"].startswith("Mi nuevo amigo"))
        self.assertEqual(q["source"], "I Am Responsible")
        self.assertIsNone(q["signup_url"])                 # only Grapevine's link is left: never taken for La Viña

    def test_no_quote_block(self):
        self.assertIsNone(self.parse("gv", "<html><body><main><h1>Site maintenance</h1></main></body></html>"))

    def test_empty_quote_raises(self):
        html = re.sub(r"(?s)<div class=\"clearfix text-formatted field field--name-body.*?</div>", "", page("gv_home.html"))
        html = re.sub(r"(?s)<div class=\"quote-container\">.*?</p>\s*</div>", "", html)
        with self.assertRaises(Q.QuoteParseError):
            self.parse("gv", html)

    def test_heading_without_date_uses_fetch_day(self):
        html = page("gv_home.html").replace("Grapevine Daily Quote September 25", "Grapevine Daily Quote")
        q = self.parse("gv", html, date(2026, 9, 26))
        self.assertEqual(q["date"], "2026-09-26")
        self.assertFalse(q["date_from_heading"])


class TextTest(unittest.TestCase):
    def test_strip_outer_quotes(self):
        self.assertEqual(Q.strip_outer_quotes("  “Keep it  simple.”  "), "Keep it simple.")
        self.assertEqual(Q.strip_outer_quotes("“Vivir sin la bebida”."), "Vivir sin la bebida.")
        self.assertEqual(Q.strip_outer_quotes('"Easy does it."'), "Easy does it.")
        # two quotations with words between them are not one quoted text: left as published
        self.assertEqual(Q.strip_outer_quotes("“First,” he said, “things first.”"), "“First,” he said, “things first.”")
        self.assertEqual(Q.strip_outer_quotes("No marks at all."), "No marks at all.")

    def test_split_attribution(self):
        self.assertEqual(Q.split_attribution("Jim S., Del Rio, Texas, March 2010, From: “Title”, Grapevine"),
                         ("Jim S., Del Rio, Texas, March 2010", "“Title”, Grapevine"))
        self.assertEqual(Q.split_attribution("“Título”. CIUDAD DE MÉXICO, MÉXICO. De: Lo mejor de La Viña"),
                         ("“Título”. CIUDAD DE MÉXICO, MÉXICO", "Lo mejor de La Viña"))
        self.assertEqual(Q.split_attribution("Jim S., De Soto, Texas"), ("Jim S., De Soto, Texas", ""))
        self.assertEqual(Q.split_attribution("From: The Language of the Heart"), ("", "The Language of the Heart"))

    def test_date_label(self):
        self.assertEqual(Q.date_label("2026-09-25", "en"), "September 25")
        self.assertEqual(Q.date_label("2026-09-25", "es"), "25 de septiembre")
        self.assertEqual(Q.date_label("2027-01-01T05:00:00Z", "es"), "1 de enero")
        self.assertEqual(Q.date_label(None, "en"), "")

    def test_year_is_inferred_around_today(self):
        self.assertEqual(Q.heading_date("Grapevine Daily Quote December 31", date(2027, 1, 1)), date(2026, 12, 31))
        self.assertEqual(Q.heading_date("Cita Diaria con La Viña Enero 1", date(2026, 12, 31)), date(2027, 1, 1))
        self.assertEqual(Q.heading_date("Cita Diaria: 25 de septiembre", TODAY), date(2026, 9, 25))
        self.assertIsNone(Q.heading_date("Grapevine Daily Quote February 30", TODAY))
        self.assertIsNone(Q.heading_date("Grapevine Daily Quote", TODAY))


class CollectTest(unittest.TestCase):
    def test_both_publications_one_request_each(self):
        f = fetcher({"gv": page("gv_home.html"), "lv": page("lv_home.html")})
        res = Q.collect(f, {}, TODAY, CFG)
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(f.calls), 2)
        self.assertEqual(len(set(f.calls)), 2)
        self.assertEqual([i["id"] for i in res["items"]], ["quote:gv:2026-09-25", "quote:lv:2026-09-25"])
        gv, lv = res["items"]
        self.assertEqual((gv["lang"], lv["lang"]), ("en", "es"))
        self.assertEqual(gv["url"], "https://www.aagrapevine.org/#quote-of-the-day")
        self.assertEqual(lv["url"], "https://www.aalavina.org/#quote-of-the-day")
        self.assertEqual(lv["extra"]["date_label"], "25 de septiembre")
        self.assertEqual([h["date"] for h in res["history"]["gv"]], ["2026-09-25"])

    def test_failure_keeps_previous_quote(self):
        prev = Q.collect(fetcher({"gv": page("gv_home.html"), "lv": page("lv_home.html")}), {}, TODAY, CFG)
        prev_env = {"items": prev["items"], "history": prev["history"]}
        tomorrow = date(2026, 9, 26)
        gv_next = page("gv_home.html").replace("September 25", "September 26")
        res = Q.collect(fetcher({"gv": gv_next, "lv": None}), prev_env, tomorrow, CFG)
        self.assertEqual(len(res["errors"]), 1)
        self.assertTrue(res["errors"][0].startswith("lv: page unavailable"))
        ids = [i["id"] for i in res["items"]]
        self.assertEqual(ids, ["quote:gv:2026-09-26", "quote:lv:2026-09-25"])     # La Viña: yesterday's, kept
        self.assertEqual(res["items"][1]["extra"]["text"], prev["items"][1]["extra"]["text"])
        self.assertEqual([h["date"] for h in res["history"]["gv"]], ["2026-09-26", "2026-09-25"])
        self.assertEqual([h["date"] for h in res["history"]["lv"]], ["2026-09-25"])

    def test_unreadable_page_keeps_previous_and_signup(self):
        prev = Q.collect(fetcher({"gv": page("gv_home.html"), "lv": page("lv_home.html")}), {}, TODAY, CFG)
        prev_env = {"items": prev["items"], "history": prev["history"]}
        maintenance = "<html><body><h1>Down for maintenance</h1></body></html>"
        lv_embed_only = re.sub(r"(?s)<article .*?</article>", "", page("lv_home.html"))
        res = Q.collect(fetcher({"gv": maintenance, "lv": lv_embed_only}), prev_env, TODAY, CFG)
        self.assertEqual(res["errors"], ["gv: no quote of the day on https://www.aagrapevine.org/"])
        self.assertEqual(res["items"][0]["id"], "quote:gv:2026-09-25")
        # the embed has no La Viña sign-up link: the last known one is kept
        self.assertIn("/d.jsp?", res["items"][1]["extra"]["signup_url"])

    def test_history_cap_and_same_day_replaced(self):
        hist = []
        start = date(2026, 9, 1)
        for n in range(25):
            d = date.fromordinal(start.toordinal() + n)
            hist = Q.add_history(hist, {"date": d.isoformat(), "text": f"quote {n}"}, d)
        self.assertEqual(len(hist), Q.HISTORY_DAYS)
        self.assertEqual(hist[0]["date"], "2026-09-25")
        self.assertEqual(hist[-1]["date"], "2026-09-12")                 # 14 days, newest first
        hist = Q.add_history(hist, {"date": "2026-09-25", "text": "corrected"}, TODAY)
        self.assertEqual(len(hist), Q.HISTORY_DAYS)
        self.assertEqual(hist[0]["text"], "corrected")
        # days pass without a new quote: old entries still age out
        self.assertEqual(len(Q.add_history(hist, None, date(2026, 10, 5))), 4)

    def test_older_quote_on_page_does_not_replace_newer(self):
        prev = Q.collect(fetcher({"gv": page("gv_home.html"), "lv": page("lv_home.html")}), {}, TODAY, CFG)
        older = page("gv_home.html").replace("September 25", "September 24")
        with self.assertLogs("quote", "WARNING"):
            res = Q.collect(fetcher({"gv": older, "lv": page("lv_home.html")}), prev, TODAY, CFG)
        self.assertEqual(res["items"][0]["id"], "quote:gv:2026-09-25")
        self.assertEqual([h["date"] for h in res["history"]["gv"]], ["2026-09-25", "2026-09-24"])


class SiteFileTest(unittest.TestCase):
    def test_build_site(self):
        res = Q.collect(fetcher({"gv": page("gv_home.html"), "lv": page("lv_home.html")}), {}, TODAY, CFG)
        # raw order is not guaranteed (save_raw sorts by date): the site file is always Grapevine, La Viña
        env = {"updated": "2026-09-25T11:00:00Z", "items": list(reversed(res["items"])), "history": res["history"]}
        doc = Q.build_site(env)
        self.assertEqual(doc["updated"], "2026-09-25T11:00:00Z")
        self.assertEqual([i["pub"] for i in doc["items"]], ["gv", "lv"])
        gv = doc["items"][0]
        self.assertEqual(set(gv), set(Q.SITE_KEYS))
        self.assertEqual((gv["date"], gv["date_label"], gv["lang"]), ("2026-09-25", "September 25", "en"))
        self.assertEqual(doc["items"][1]["date_label"], "25 de septiembre")
        # the raw history is a guard for collect(), never shown: it stays out of the site file
        self.assertNotIn("history", doc)
        self.assertEqual(set(doc), {"updated", "fixture", "items"})

    def test_build_site_skips_bad_rows(self):
        env = {"items": [{"id": "quote:gv:x", "kind": "quote", "url": "https://www.aagrapevine.org/#quote-of-the-day",
                          "date": "2026-09-25", "lang": "en", "extra": {"pub": "gv", "text": "  "}},
                         {"id": "other", "kind": "botm", "extra": {"pub": "gv", "text": "x"}}]}
        self.assertEqual(Q.build_site(env)["items"], [])
        self.assertEqual(Q.build_site({}), Q.empty_site())


if __name__ == "__main__":
    unittest.main()
