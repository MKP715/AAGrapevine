"""Book of the Month / subscriptions (scripts/sync/shop.py → build_data → data/site/shop.json) and
La Viña's weekly open meeting (scripts/sync/weekly_open.py, second item).

Fixtures in tests/fixtures/shop/ are trimmed copies of the official store pages (September 2026).
Run:  python -m pytest -q tests/test_shop.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import build_data as B  # noqa: E402
from scripts.sync import shop as S  # noqa: E402
from scripts.sync import weekly_open as W  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "shop"
TODAY = date(2026, 9, 24)
GV, LV = "https://www.aagrapevine.org", "https://www.aalavina.org"
GV_PRODUCT = f"{GV}/store/no-matter-what-dealing-adversity-sobriety"
LV_PRODUCT = f"{LV}/tienda/frente-frente"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# Every page the module asks for, answered from the fixtures (anything else = a failed fetch).
PAGES = {
    f"{GV}/BOTM": "gv_botm.html", GV_PRODUCT: "gv_product.html",
    f"{LV}/libro-del-mes": "lv_botm.html", LV_PRODUCT: "lv_product.html",
    f"{GV}/store/grapevine-subscriptions": "gv_subscriptions.html",
    f"{LV}/tienda/suscripciones": "lv_subscriptions.html",
    f"{GV}/store/us-subscriptions": "gv_us_listing.html", f"{LV}/US-suscripciones": "lv_us_listing.html",
    f"{GV}/store/grapevine-complete-subscription-1-year": "gv_complete_product.html",
}


def fixture_fetch(url: str) -> str | None:
    name = PAGES.get(url)
    return fx(name) if name else None


class BookOfTheMonth(unittest.TestCase):
    def test_grapevine_offer(self):
        o = S.parse_botm(fx("gv_botm.html"), f"{GV}/BOTM", "en", TODAY)
        self.assertEqual(o["title"], "No Matter What: Dealing With Adversity in Sobriety")
        self.assertEqual(o["url"], GV_PRODUCT)
        self.assertEqual(o["discount_pct"], 20)
        self.assertEqual((o["month"], o["month_label"]), (10, "October"))
        self.assertEqual((o["starts"], o["ends"]), ("2026-09-15", "2026-10-14"))   # "SEPT. 15 thru OCt. 14"
        self.assertTrue(o["blurb"].startswith("All recovering alcoholics"))

    def test_la_vina_offer_in_spanish(self):
        o = S.parse_botm(fx("lv_botm.html"), f"{LV}/libro-del-mes", "es", TODAY)
        self.assertEqual(o["title"], "Frente a Frente: El apadrinamiento en acción")
        self.assertEqual(o["url"], LV_PRODUCT)
        self.assertEqual(o["month_label"], "Octubre")
        self.assertEqual((o["starts"], o["ends"]), ("2026-08-15", "2026-10-14"))   # "del 15 de AGOSTO al 14 de OCTUbre"
        self.assertTrue(o["blurb"].startswith("Detrás de cada historia"))

    def test_the_percent_comes_from_the_page(self):
        html = fx("gv_botm.html").replace("20% off", "25% off")
        o = S.parse_botm(html, f"{GV}/BOTM", "en", TODAY)
        self.assertEqual(o["discount_pct"], 25)
        self.assertEqual(S.sale_price(14.99, 25), 11.24)
        self.assertEqual(S.sale_price(14.99, 20), 11.99)

    def test_years_are_inferred_around_today(self):
        line = "Offer good for this title: DEC. 15 thru JAN. 14 Only."
        self.assertEqual(S.offer_dates(line, date(2027, 1, 5)), ("2026-12-15", "2027-01-14"))
        self.assertEqual(S.offer_dates(line, date(2026, 12, 20)), ("2026-12-15", "2027-01-14"))
        self.assertEqual(S.offer_dates("Oferta válida del 15 de diciembre al 14 de enero", date(2026, 12, 1)),
                         ("2026-12-15", "2027-01-14"))
        self.assertEqual(S.offer_dates("Offer good: Oct. 1, 2026 through Oct. 31, 2026", TODAY), ("2026-10-01", "2026-10-31"))

    def test_an_old_offer_left_on_the_page_is_past_not_next_year(self):
        self.assertEqual(S.offer_dates("APRIL 15 thru MAY 14", date(2026, 11, 20)), ("2026-04-15", "2026-05-14"))

    def test_no_offer_and_broken_offer(self):
        empty = ("<html><head><title>Book of the month! | AA Grapevine</title></head><body><main>"
                 "<div id='block-neatosub-content'><p>Check back soon!</p></div></main></body></html>")
        self.assertIsNone(S.parse_botm(empty, f"{GV}/BOTM", "en", TODAY))
        # an unrelated 200 page (maintenance, a redirect home) is an error, so the previous offer is kept
        maint = "<html><head><title>We'll be back soon</title></head><body><main><p>Maintenance</p></main></body></html>"
        with self.assertRaises(S.ShopParseError):
            S.parse_botm(maint, f"{GV}/BOTM", "en", TODAY)
        no_link = fx("gv_botm.html").replace("/store/no-matter-what-dealing-adversity-sobriety", "/somewhere")
        with self.assertRaises(S.ShopParseError):
            S.parse_botm(no_link, f"{GV}/BOTM", "en", TODAY)


class ProductPages(unittest.TestCase):
    TIERS = [{"min": 1, "max": 4, "off": 0.0}, {"min": 5, "max": 9, "off": 0.5}, {"min": 10, "max": 19, "off": 1.0},
             {"min": 20, "max": 29, "off": 2.0}, {"min": 30, "max": None, "off": 3.0}]

    def test_grapevine_book(self):
        p = S.parse_product(fx("gv_product.html"), GV_PRODUCT)
        self.assertEqual((p["price"], p["currency"], p["sku"]), (14.99, "USD", "GV31"))
        self.assertIn("/styles/product_feature_image/", p["image"])
        self.assertEqual(p["tiers"], self.TIERS)
        self.assertIn("physical books", p["tiers_note"])

    def test_la_vina_book(self):
        p = S.parse_product(fx("lv_product.html"), LV_PRODUCT)
        self.assertEqual((p["price"], p["sku"]), (14.99, "SGV04"))
        self.assertEqual(p["tiers"], self.TIERS)                      # "5 a 9 libros: $0.50 de descuento por libro."
        self.assertIn("libros físicos", p["tiers_note"])

    def test_visible_price_when_there_is_no_json_ld(self):
        html = fx("gv_product.html").replace("application/ld+json", "text/plain")
        p = S.parse_product(html, GV_PRODUCT)
        self.assertEqual((p["price"], p["sku"]), (14.99, "GV31"))

    def test_type_description_is_the_first_feature(self):
        p = S.parse_product(fx("gv_complete_product.html"), f"{GV}/store/grapevine-complete-subscription-1-year")
        self.assertTrue(S.short_description(p).startswith("Combines the Grapevine print magazine"))


class Subscriptions(unittest.TestCase):
    def test_region_links_from_the_category_pages(self):
        self.assertEqual(S.parse_categories(fx("gv_subscriptions.html"), f"{GV}/store/grapevine-subscriptions"), {
            "us": f"{GV}/store/us-subscriptions", "ca": f"{GV}/store/canada-subscriptions",
            "intl": f"{GV}/store/international-subscriptions"})
        self.assertEqual(S.parse_categories(fx("lv_subscriptions.html"), f"{LV}/tienda/suscripciones"), {
            "us": f"{LV}/US-suscripciones", "ca": f"{LV}/tienda/canada-suscripciones",
            "intl": f"{LV}/tienda/internacional-suscripciones"})

    def test_grapevine_us_listing(self):
        plans, nxt = S.parse_listing(fx("gv_us_listing.html"), f"{GV}/store/us-subscriptions")
        self.assertIsNone(nxt)
        self.assertEqual(len(plans), 11)
        first = plans[0]
        self.assertEqual((first["type"], first["term_months"], first["price"], first["sku"]), ("print", 12, 36.0, "GVUS1"))
        self.assertEqual(first["volume"], [{"min": 2, "max": 19, "price": 35.5}, {"min": 20, "max": 39, "price": 35.0},
                                           {"min": 40, "max": None, "price": 34.0}])
        self.assertEqual(first["url"], f"{GV}/store/grapevine-print-subscriptions-1-year")
        self.assertEqual({p["type"] for p in plans}, {"print", "digital", "complete"})
        self.assertEqual(sorted({p["term_months"] for p in plans}), [1, 12, 24, 36])

    def test_la_vina_us_listing_spanish_titles(self):
        plans, _ = S.parse_listing(fx("lv_us_listing.html"), f"{LV}/US-suscripciones")
        by_sku = {p["sku"]: p for p in plans}
        self.assertEqual((by_sku["LVUS1"]["type"], by_sku["LVUS1"]["term_months"]), ("print", 12))
        self.assertEqual((by_sku["LVV1M"]["type"], by_sku["LVV1M"]["term_months"]), ("complete", 1))
        self.assertEqual((by_sku["LO3A"]["type"], by_sku["LO3A"]["term_months"]), ("digital", 36))

    def test_type_and_term_words(self):
        self.assertEqual(S.plan_type("Suscripción revista impresa de La Viña: 2 años"), "print")
        self.assertEqual(S.plan_type("Grapevine Complete Subscription: 1-Month"), "complete")
        self.assertEqual(S.plan_type("Gift Certificate"), "other")
        self.assertEqual(S.term_months("Grapevine Print Subscriptions: 2-Years"), 24)
        self.assertEqual(S.term_months("Suscripción completa a La Viña: 1 mes"), 1)
        self.assertIsNone(S.term_months("Gift Certificate"))


class Collect(unittest.TestCase):
    def test_a_run_with_some_pages_missing_keeps_the_previous_plans(self):
        prev_ca = {"id": "sub:gv:ca:GVCAN1", "kind": "subscription", "title": "Canada: Grapevine Print Subscription: 1-Year",
                   "url": f"{GV}/store/x", "extra": {"pub": "gv", "region": "ca", "type": "print", "price": 36.0}}
        res = S.collect(fixture_fetch, lambda url: None, {"items": [prev_ca]}, TODAY, cfg={}, refresh_types=True)
        ids = {i["id"] for i in res["items"]}
        self.assertIn("botm:gv", ids)
        self.assertIn("botm:lv", ids)
        botm = next(i for i in res["items"] if i["id"] == "botm:gv")
        self.assertEqual((botm["extra"]["price"], botm["extra"]["sale_price"], botm["extra"]["sku"]), (14.99, 11.99, "GV31"))
        self.assertIn("sub:gv:us:GVUS1", ids)
        self.assertIn("sub:lv:us:LVUS1", ids)
        self.assertIn("sub:gv:ca:GVCAN1", ids, "the region that could not be read keeps its previous plans")
        self.assertTrue(any("region(s) ca, intl" in e for e in res["errors"]))
        self.assertEqual(res["bulk_discounts"]["tiers"][1], {"min": 5, "max": 9, "off": 0.5})
        self.assertEqual(set(res["bulk_discounts"]["note"]), {"en", "es"})
        self.assertIn("complete", res["types"]["gv"])

    def test_nothing_reachable_keeps_everything(self):
        prev = {"items": [{"id": "botm:gv", "kind": "botm", "title": "Old", "url": GV_PRODUCT, "extra": {}},
                          {"id": "sub:lv:us:LVUS1", "kind": "subscription", "title": "T", "url": f"{LV}/x",
                           "extra": {"pub": "lv", "region": "us"}}],
                "bulk_discounts": {"source_url": GV_PRODUCT, "tiers": [{"min": 1, "max": None, "off": 0}]},
                "types_checked": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        res = S.collect(lambda url: None, lambda url: None, prev, TODAY, cfg={})
        self.assertEqual({i["id"] for i in res["items"]}, {"botm:gv", "sub:lv:us:LVUS1"})
        self.assertEqual(len(res["errors"]), 4)
        self.assertEqual(res["bulk_discounts"]["source_url"], GV_PRODUCT)


class SiteShopJson(unittest.TestCase):
    def _ctx(self, items, **env):
        ctx = B.Ctx(offline=True)
        ctx.raw = {"shop": {"items": items, "updated": "2026-09-24T12:00:00Z", **env}}
        return ctx

    def _item(self, pub, ends, lang):
        return {"id": f"botm:{pub}", "kind": "botm", "title": f"Book {pub}", "summary": "About it.", "lang": lang,
                "url": f"{GV}/store/{pub}", "image": "/assets/cache/shop/x.webp",
                "extra": {"pub": pub, "page_url": f"{GV}/BOTM", "price": 14.99, "sale_price": 11.99, "discount_pct": 20,
                          "sku": "GV31", "starts": "2026-09-15", "ends": ends, "month": 10, "month_label": "October"}}

    def test_contract_order_expiry_and_labels(self):
        today = B.Ctx(offline=True).today_local
        past = date.fromordinal(today.toordinal() - 1).isoformat()
        future = date.fromordinal(today.toordinal() + 10).isoformat()
        plans = [{"id": f"sub:{pub}:{reg}:{sku}", "kind": "subscription", "title": sku, "url": f"{GV}/store/{sku}",
                  "extra": {"pub": pub, "region": reg, "type": "print", "term_months": 12, "price": 36, "sku": sku,
                            "position": pos, "volume": [{"min": 2, "max": None, "price": 35.5}]}}
                 for pub, reg, sku, pos in (("lv", "us", "LVUS1", 0), ("gv", "ca", "GVCAN1", 0), ("gv", "us", "GVUS2", 1),
                                            ("gv", "us", "GVUS1", 0))]
        ctx = self._ctx([self._item("gv", future, "en"), self._item("lv", past, "es"), *plans],
                        types={"gv": {"print": {"text": "Enjoy 12 issues.", "lang": "en"}}},
                        bulk_discounts={"source_url": GV_PRODUCT, "tiers": [{"min": 1, "max": 4, "off": 0}],
                                        "note": {"en": "Books only."}})
        i18n = B.I18n(None)
        doc, wanted = B.build_shop(ctx, i18n)
        B.finish_shop(doc, wanted, i18n)
        self.assertEqual([b["id"] for b in doc["botm"]], ["botm:gv"], "an offer past its end date is left out")
        b = doc["botm"][0]
        for key in ("id", "pub", "lang", "title", "url", "page_url", "image", "price", "sale_price", "discount_pct",
                    "currency", "sku", "starts", "ends", "month_label", "blurb", "i18n", "machine"):
            self.assertIn(key, b)
        self.assertEqual(b["i18n"]["month_label"], {"en": "October", "es": "Octubre"})
        self.assertEqual(b["i18n"]["title"], {"en": "Book gv", "es": "Book gv"})     # no model → original kept
        self.assertEqual([(s["pub"], s["region"]) for s in doc["subscriptions"]], [("gv", "us"), ("gv", "ca"), ("lv", "us")])
        self.assertEqual([p["sku"] for p in doc["subscriptions"][0]["plans"]], ["GVUS1", "GVUS2"])
        self.assertEqual(doc["subscriptions"][0]["plans"][0]["volume"], [{"min": 2, "max": None, "price": 35.5}])
        self.assertEqual(doc["types"]["gv"]["print"], {"en": "Enjoy 12 issues.", "es": "Enjoy 12 issues."})
        self.assertEqual(set(doc["bulk_discounts"]["note"]), {"en", "es"})
        self.assertEqual(B.shop_count(doc), 1 + 4)

    def test_no_raw_file_gives_an_empty_document(self):
        ctx = self._ctx([])
        doc, wanted = B.build_shop(ctx, B.I18n(None))
        self.assertEqual((doc["botm"], doc["subscriptions"], doc["types"]), ([], [], {}))
        self.assertEqual(doc["bulk_discounts"]["tiers"], [])


class LaVinaWeeklyOpen(unittest.TestCase):
    CFG = {"title_es": "Nueva Reunión Abierta de La Viña", "title_en": "La Viña's New Open Meeting",
           "day": "thursday", "time": "12:00", "timezone": "America/New_York", "starts": "2026-11-05",
           "zoom_id": "871 2036 8287", "passcode": "238047", "summary_es": "Dos miembros comparten.",
           "summary_en": "Two members share."}

    def test_item_fields_and_first_date(self):
        it = W.lavina_item(self.CFG, datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc))
        self.assertEqual((it["id"], it["lang"], it["kind"]), ("weekly_open_lv", "es", "meeting"))
        ex = it["extra"]
        for key in ("zoom_id", "passcode", "day", "time", "weekday", "start_local", "timezone", "time_central",
                    "next_start", "zoom_url", "starts"):
            self.assertIn(key, ex)
        self.assertEqual(ex["next_start"], "2026-11-05T17:00:00Z", "never before the first meeting")
        self.assertEqual((ex["weekday"], ex["start_local"], ex["time_central"]), ("thursday", "12:00", "11 AM Central"))
        self.assertEqual(ex["zoom_url"], "https://zoom.us/j/87120368287")
        self.assertEqual(ex["own_i18n"]["title"]["en"], "La Viña's New Open Meeting")

    def test_after_the_start_it_is_the_next_thursday(self):
        it = W.lavina_item(self.CFG, datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(it["extra"]["next_start"], "2026-11-12T17:00:00Z")

    def test_a_start_date_on_another_weekday_moves_to_the_first_meeting(self):
        it = W.lavina_item({**self.CFG, "starts": "2026-11-04"}, datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc))
        self.assertEqual((it["extra"]["starts"], it["extra"]["next_start"]), ("2026-11-05", "2026-11-05T17:00:00Z"))

    def test_disabled_or_broken_config(self):
        self.assertIsNone(W.lavina_item(None))
        self.assertIsNone(W.lavina_item({**self.CFG, "enabled": False}))
        self.assertIsNone(W.lavina_item({**self.CFG, "day": "someday"}))

    def test_grapevine_item_stays_first(self):
        gv = {"id": "weekly_open", "kind": "meeting", "title": "Grapevine Weekly Open AA Meeting", "extra": {}}
        items = W.with_lavina([gv], [], self.CFG)
        self.assertEqual([i["id"] for i in items], ["weekly_open", "weekly_open_lv"])
        self.assertEqual([i["id"] for i in W.with_lavina([gv], items, None)], ["weekly_open"])
        ctx = B.Ctx(offline=True)
        lv = W.lavina_item(self.CFG)
        lv["first_seen"] = "2026-09-25T00:00:00Z"            # newer than the Grapevine item …
        ctx.raw = {"weekly_open": {"items": [lv, {**gv, "url": "https://www.aagrapevine.org/grapevine-weekly-open",
                                                  "first_seen": "2026-09-01T00:00:00Z"}]}}
        self.assertEqual([i["id"] for i in B.weekly_open_items(ctx)], ["weekly_open", "weekly_open_lv"])   # … still second

    def test_bilingual_labels(self):
        lab = B.weekly_open_labels(W.lavina_item(self.CFG))
        self.assertEqual(lab["day"], {"en": "Thursdays", "es": "Jueves"})
        self.assertEqual(lab["when"]["en"], "Thursdays at 11:00 AM Central")
        self.assertIn("871 2036 8287", lab["sentence"]["es"])


if __name__ == "__main__":
    unittest.main()
