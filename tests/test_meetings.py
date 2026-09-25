"""Grapevine meetings (scripts/sync/meetings.py → build_site → data/site/meetings.json).

Fixtures in tests/fixtures/meetings/ are trimmed copies of the offices' public meeting lists (September
2026): a few Grapevine ("GR") meetings and a few others per office. Every personal field (e-mail, phone,
Zoom link, contact name, notes, payment link…) holds a FAKE value there, so the tests can prove none of
them is copied. Two synthetic Grapevine meetings (Houston, TX and Ardmore, OK) test the Area rule. No
real key is used anywhere: the key tests use a made-up one.
Run:  python -m unittest tests.test_meetings -v   (CI: python -m unittest discover -s tests)
"""
from __future__ import annotations

import base64
import json
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import meetings as M  # noqa: E402
from scripts.sync.common import load_config  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "meetings"
DALLAS_FEED = "https://www.aadallas.org/wp-admin/admin-ajax.php?action=meetings"
FW_FEED = "https://www.fortworthaa.org/wp-admin/admin-ajax.php?action=meetings"
D71_FEED = "https://district71.org/wp-admin/admin-ajax.php?action=meetings"
FAKE_KEY = "synthetic0123456789abcdef"          # made up — not a real key
FAKE_KEY_FW = "anotherfake9876543210"
KEY_PAGE = "https://raw.githubusercontent.com/NETA65/RowlettAA/main/meetings.html"
PERSONAL_VALUES = ("someone@example.org", "office@example.org", "feedback@example.org", "pat@example.org",
                   "(555) 010", "+1555", "zoom.us", "Passcode: FAKE", "Pat X.", "PO Box 0", "@fake", "$fake",
                   "Call (555)", "Park in back", "wp-admin/post.php")


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def js_const(name: str, url: str) -> str:
    """A RowlettAA-style constant: base64 of the reversed address, split into quoted parts."""
    b = base64.b64encode(url[::-1].encode()).decode()
    parts = [b[i:i + 20] for i in range(0, len(b), 20)]
    return ("const " + name + " = (function(){var _r='" + "'+'".join(parts)
            + "';return atob(_r).split('').reverse().join('')})();")


# A fake key page: the Fort Worth constant comes FIRST, so a sloppy match of "FEED_URL" would take it.
KEY_PAGE_HTML = "\n".join([
    "<script>", js_const("FEED_URL_FW", f"{FW_FEED}&key={FAKE_KEY_FW}"),
    js_const("FEED_URL", f"{DALLAS_FEED}&key={FAKE_KEY}"),
    js_const("FEED_URL_OTHER", f"https://evil.example.com/wp-admin/admin-ajax.php?action=meetings&key={FAKE_KEY}"),
    "</script>"])


def feeds_cfg(**over) -> dict:
    feeds = [
        {"id": "aadallas", "name": "Dallas Intergroup", "site": "https://www.aadallas.org", "feed": DALLAS_FEED,
         "key_env": "TSML_KEY_AADALLAS", "key_const": "FEED_URL", "lang": "en", "in_area": True,
         "region_label": {"en": "Dallas area", "es": "Zona de Dallas"}},
        {"id": "fortworthaa", "name": "Fort Worth AA Central Office", "site": "https://fortworthaa.org", "feed": FW_FEED,
         "key_env": "TSML_KEY_FORTWORTHAA", "key_const": "FEED_URL_FW", "lang": "en", "in_area": True,
         "region_label": {"en": "Fort Worth area", "es": "Zona de Fort Worth"}},
        {"id": "tyleraa", "name": "AA Central Service Office, Tyler", "site": "https://www.tyler-aa.org",
         "page": "https://www.tyler-aa.org/meetings/", "methods": ["page"], "in_area": True,
         "region_label": {"en": "East Texas (Tyler)", "es": "Este de Texas (Tyler)"}},
        {"id": "district71", "name": "District 71 AA", "site": "https://district71.org", "feed": D71_FEED,
         "in_area": True, "region_label": {"en": "Abilene", "es": "Abilene"}},
    ]
    cfg = {"spotlight": load_config().get("spotlight"),
           "meetings": {"type": "GR", "area_label": {"en": "Our Area (NETA 65)", "es": "Nuestra Área (NETA 65)"},
                        "key_source": {"url": KEY_PAGE}, "feeds": feeds}}
    cfg["meetings"].update(over)
    return cfg


class FakeWeb:
    """fetch(url, params) for collect(): answers from the fixtures; records every request."""

    def __init__(self, fail=(), keys=None):
        self.fail = set(fail)
        self.keys = keys or {DALLAS_FEED: FAKE_KEY, FW_FEED: FAKE_KEY_FW}
        self.calls: list[tuple[str, dict]] = []
        self.pages = {
            DALLAS_FEED: "aadallas_feed.json", FW_FEED: "fortworthaa_feed.json", D71_FEED: "district71_feed.json",
            "https://www.tyler-aa.org/meetings/": "tyleraa_page.html",
            "https://fortworthaa.org/meetings/": "fortworthaa_page.html",
            "https://fortworthaa.org/wp-content/tsml-cache-0123456789.json?1790026838": "fortworthaa_feed.json",
        }

    def __call__(self, url, params):
        self.calls.append((url, dict(params or {})))
        if url in self.fail:
            raise M.FeedError(f"{M._bare_host(url)} answered HTTP 503")
        if url == KEY_PAGE:
            return KEY_PAGE_HTML, url
        if url in self.keys and (params or {}).get("key") != self.keys[url]:
            raise M.FeedError(f"{M._bare_host(url)} refused the request (HTTP 401)")
        if url not in self.pages:
            raise M.FeedError(f"{M._bare_host(url)} did not answer")
        return fx(self.pages[url]), url


# =========================================================================== keys
class Keys(unittest.TestCase):
    def test_obfuscation_round_trip_like_rowlett(self):
        url = f"{DALLAS_FEED}&key={FAKE_KEY}"
        obf = M.obfuscate(url)
        self.assertNotIn(FAKE_KEY, obf)
        self.assertEqual(base64.b64decode(obf).decode()[::-1], url)       # exactly RowlettAA's scheme
        self.assertEqual(M.deobfuscate(obf), url)
        self.assertIsNone(M.deobfuscate("not base64!"))

    def test_js_constant_is_matched_by_its_exact_name(self):
        self.assertEqual(M.js_const_url(KEY_PAGE_HTML, "FEED_URL"), f"{DALLAS_FEED}&key={FAKE_KEY}")
        self.assertEqual(M.js_const_url(KEY_PAGE_HTML, "FEED_URL_FW"), f"{FW_FEED}&key={FAKE_KEY_FW}")
        self.assertIsNone(M.js_const_url(KEY_PAGE_HTML, "FEED_URL_ES"))

    def test_a_key_is_only_used_for_its_own_site(self):
        self.assertEqual(M.key_in(f"{DALLAS_FEED}&key={FAKE_KEY}", DALLAS_FEED), FAKE_KEY)
        self.assertEqual(M.key_in(f"https://aadallas.org/x?key={FAKE_KEY}", DALLAS_FEED), FAKE_KEY)   # www. or not
        evil = M.js_const_url(KEY_PAGE_HTML, "FEED_URL_OTHER")
        self.assertIsNone(M.key_in(evil, DALLAS_FEED))
        self.assertIsNone(M.key_in(f"{DALLAS_FEED}&key=x", DALLAS_FEED))      # too short to be a key

    def test_order_secret_then_config_then_key_page(self):
        feed = M.settings(feeds_cfg())["feeds"][0]
        pages = []

        def page():
            pages.append(1)
            return KEY_PAGE_HTML
        self.assertEqual(M.resolve_key(feed, {"TSML_KEY_AADALLAS": "fromsecret12345"}, page), ("fromsecret12345", "secret"))
        self.assertEqual(M.resolve_key(feed, {"TSML_KEY_AADALLAS": f"{DALLAS_FEED}&key=pastedwhole123"}, page),
                         ("pastedwhole123", "secret"))
        self.assertEqual(pages, [], "the key page is not read while a secret works")
        with_obf = dict(feed, feed_obf=M.obfuscate(f"{DALLAS_FEED}&key=fromconfig12345"))
        self.assertEqual(M.resolve_key(with_obf, {}, page), ("fromconfig12345", "config"))
        self.assertEqual(pages, [])
        self.assertEqual(M.resolve_key(feed, {}, page), (FAKE_KEY, "key_source"))
        self.assertEqual(M.resolve_key(feed, {}, lambda: None), (None, None))

    def test_config_keys_are_stored_obfuscated_for_their_own_sites(self):
        """config/site.yml: the two keyed offices carry feed_obf, and it decodes to their own list."""
        st = M.settings()
        keyed = {f["id"]: f for f in st["feeds"] if f["feed_obf"]}
        self.assertEqual(set(keyed), {"aadallas", "fortworthaa"})
        text = (ROOT / "config" / "site.yml").read_text(encoding="utf-8")
        for f in keyed.values():
            key = M.key_in(M.deobfuscate(f["feed_obf"]), f["feed"])
            self.assertTrue(key, f["id"])
            self.assertNotIn(key, text, "the key must not appear in plain text")


class Redaction(unittest.TestCase):
    def test_redact_addresses_and_known_keys(self):
        self.assertEqual(M.redact(f"GET {DALLAS_FEED}&key={FAKE_KEY} failed"),
                         f"GET {DALLAS_FEED}&key=[redacted] failed")
        self.assertIn("key%3D[redacted]", M.redact(f"url=x%26key%3D{FAKE_KEY}"))
        M._SECRETS.add(FAKE_KEY_FW)
        try:
            self.assertEqual(M.redact(f"odd message {FAKE_KEY_FW}"), "odd message [redacted]")
        finally:
            M._SECRETS.discard(FAKE_KEY_FW)

    def test_log_records_are_scrubbed(self):
        seen = []

        class Keep(logging.Handler):
            def emit(self, record):
                seen.append(self.format(record))
        lg = M.get_logger("http")                    # PoliteSession's logger carries the filter too
        h = Keep()
        saved, lg.handlers = lg.handlers, [h]         # (quiet: only our handler sees the test lines)
        try:
            lg.warning("%s %s failed: %s", "GET", "x", f"Max retries exceeded with url: /a?action=meetings&key={FAKE_KEY}")
            try:
                raise RuntimeError(f"boom {DALLAS_FEED}&key={FAKE_KEY}")
            except RuntimeError:
                lg.exception("crash")
        finally:
            lg.handlers = saved
        self.assertTrue(seen)
        self.assertFalse(any(FAKE_KEY in s for s in seen), seen)


# =========================================================================== parsing
class Parsing(unittest.TestCase):
    def test_clock(self):
        self.assertEqual([M.clock24(x) for x in ("18:00", "18:00:00", "6:00 pm", "12:15 pm", "Noon", "12:00 am",
                                                 "Midnight", "7 pm", "", "soon")],
                         ["18:00", "18:00", "18:00", "12:15", "12:00", "00:00", "00:00", "19:00", None, None])

    def test_address(self):
        a = M.split_address("1144 N Plano Rd, Richardson, TX 75081, USA")
        self.assertEqual((a["address"], a["street"], a["city"], a["state"], a["zip"]),
                         ("1144 N Plano Rd, Richardson, TX 75081", "1144 N Plano Rd", "Richardson", "TX", "75081"))
        self.assertEqual(M.split_address("Dallas, TX, USA")["city"], "Dallas")

    def test_classic_page(self):
        meetings, cache = M.parse_page(fx("tyleraa_page.html"), "https://www.tyler-aa.org/meetings/")
        self.assertIsNone(cache)
        self.assertEqual(len(meetings), 3)
        by = {(m["day"], m["time"]): m for m in meetings}
        m = by[(0, "17:30")]                   # "5:30 pm" in the list; 24 h from the table
        self.assertEqual((m["name"], m["region"], m["attendance_option"]), ("Any Lengths Group", "Texarkana", "in_person"))
        self.assertEqual(m["formatted_address"], "2013 S Ann St, Texarkana, TX 75501, USA")
        self.assertEqual(m["url"], "https://www.tyler-aa.org/meetings/any-lengths-group-7/")   # our filters dropped
        self.assertEqual(by[(3, "12:00")]["types"], ["C", "GR", "X"])

    def test_tsml_ui_page_names_its_cache_file(self):
        meetings, cache = M.parse_page(fx("fortworthaa_page.html"), "https://fortworthaa.org/meetings/?type=grapevine")
        self.assertIsNone(meetings)
        self.assertEqual(cache, "https://fortworthaa.org/wp-content/tsml-cache-0123456789.json?1790026838")

    def test_not_a_list(self):
        with self.assertRaises(M.FeedError):
            M.parse_feed("<html>error</html>")
        with self.assertRaises(M.FeedError):
            M.parse_page("<html><body>Nothing here</body></html>", "https://x.org/meetings/")

    def test_a_table_without_location_data_is_an_error(self):
        page = fx("tyleraa_page.html").replace("var locations", "var places")
        with self.assertRaises(M.FeedError):
            M.parse_page(page, "https://www.tyler-aa.org/meetings/")
        empty = '<table><tbody id="meetings_tbody"></tbody></table>'
        self.assertEqual(M.parse_page(empty, "https://x.org/meetings/"), ([], None), "no meeting of this type today")

    def test_meeting_page_keeps_its_own_query(self):
        self.assertEqual(M._no_query("https://district71.org/?tsml_meeting=sunshine-4"),
                         "https://district71.org/?tsml_meeting=sunshine-4")
        self.assertEqual(M._no_query("https://x.org/meetings/a/?tsml-day=any&tsml-type=GR"), "https://x.org/meetings/a/")


# =========================================================================== one meeting
class Records(unittest.TestCase):
    def setUp(self):
        self.st = M.settings(feeds_cfg())
        self.feed = {f["id"]: f for f in self.st["feeds"]}

    def rec(self, fid, name):
        rows = json.loads(fx(f"{fid}_feed.json"))
        return M.to_record(next(m for m in rows if m["name"] == name), self.feed[fid], "GR")

    def test_only_public_fields_are_kept(self):
        rec, why = self.rec("aadallas", "Richardson Group")
        self.assertEqual(why, "")
        text = json.dumps(rec)
        for v in PERSONAL_VALUES:
            self.assertNotIn(v, text)
        self.assertEqual(rec["url"], "https://www.aadallas.org/meetings/richardson-group-15/")
        self.assertEqual((rec["day"], rec["time"], rec["city"], rec["county"], rec["in_area"]),
                         (3, "20:00", "Richardson", "Dallas", True))
        self.assertEqual(rec["location"], "Suite 246 (Bus Route Access)")   # the street again is dropped, the suite stays

    def test_location_that_repeats_the_address_or_the_name_is_dropped(self):
        self.assertIsNone(self.rec("aadallas", "Big Book (Rockwall) Group")[0]["location"])
        self.assertIsNone(self.rec("fortworthaa", "Mid-Cities")[0]["location"])

    def test_location_that_starts_with_the_street_keeps_only_the_detail(self):
        loc = M._location_name
        rich = {"address": "1144 N Plano Rd, Richardson, TX 75081", "street": "1144 N Plano Rd", "city": "Richardson"}
        self.assertEqual(loc("1144 N Plano Road, Suite 246 (Bus Route Access)", "Richardson Group", rich),
                         "Suite 246 (Bus Route Access)")
        self.assertEqual(loc("1144 N Plano Road, Suite 246, Richardson, TX", "Richardson Group", rich), "Suite 246")
        gp = {"address": "921 W Pioneer Pkwy, Grand Prairie, TX 75052", "street": "921 W Pioneer Pkwy",
              "city": "Grand Prairie"}
        self.assertEqual(loc("921 W Pioneer Pkwy, Suite O", "Grand Prairie Group", gp), "Suite O")
        wat = {"address": "6101 Watauga Rd e, Watauga, TX 76148", "street": "6101 Watauga Rd e", "city": "Watauga"}
        self.assertEqual(loc("6101 Watauga Rd (Strip Center)", "Serenity", wat), "Strip Center")
        self.assertIsNone(loc("6101 Watauga Road", "Serenity", wat), "nothing left but the street")
        self.assertEqual(loc("Serenity Club", "Any Lengths Group", wat), "Serenity Club")
        self.assertEqual(loc("12 Steps Hall", "X", wat), "12 Steps Hall", "another number: a name, kept")

    def test_phone_or_email_in_a_place_or_a_name_is_not_copied(self):
        parts = {"address": "1 Main St, Tyler, TX 75702", "street": "1 Main St", "city": "Tyler"}
        self.assertIsNone(M._location_name("Private home – call Bob (555) 010-0004", "G", parts))
        self.assertIsNone(M._location_name("Ask pat@example.org for the gate code", "G", parts))
        self.assertIsNone(M._location_name("Home group 903-555-0100", "G", parts))
        self.assertEqual(M._clean_name("Tuesday Grapevine – call (555) 010-0004"), "Tuesday Grapevine")
        self.assertEqual(M._clean_name("Sunday GV pat@example.org"), "Sunday GV")
        self.assertEqual(M._clean_name("Big Book Group 12 & 12"), "Big Book Group 12 & 12")
        rows = json.loads(fx("aadallas_feed.json"))
        m = dict(next(r for r in rows if r["name"] == "Richardson Group"), location="Call (555) 010-0004",
                 name="Richardson Group (555) 010-0004")
        rec, _ = M.to_record(m, self.feed["aadallas"], "GR")
        self.assertEqual(rec["name"], "Richardson Group")
        self.assertIsNone(rec["location"])
        self.assertNotIn("555", json.dumps(rec))

    def test_non_grapevine_and_inactive_meetings_are_left_out(self):
        self.assertEqual(self.rec("aadallas", "ODAAT Group"), (None, "type"))
        rows = json.loads(fx("aadallas_feed.json"))
        gone = dict(rows[0], attendance_option="inactive")
        self.assertEqual(M.to_record(gone, self.feed["aadallas"], "GR"), (None, "inactive"))

    def test_bogus_end_time_is_dropped(self):
        self.assertIsNone(self.rec("fortworthaa", "Grand Prairie")[0]["end_time"])      # "00:00"
        self.assertEqual(self.rec("fortworthaa", "Mid-Cities")[0]["end_time"], "19:00")

    def test_area_rule(self):
        gran = self.rec("fortworthaa", "Granbury Serenity")[0]          # Brazos Bend: found by its region
        self.assertEqual((gran["city"], gran["county"], gran["in_area"]), ("Brazos Bend", "Hood", True))
        hou = self.rec("fortworthaa", "Houston Test Group")[0]
        self.assertEqual((hou["county"], hou["in_area"]), ("Harris", False))
        ok = self.rec("fortworthaa", "Oklahoma Test Group")[0]
        self.assertEqual((ok["state"], ok["county"], ok["in_area"]), ("OK", None, False))
        abi = self.rec("district71", "Sunshine")[0]
        self.assertEqual((abi["county"], abi["in_area"], abi["attendance"]), ("Taylor", True, "hybrid"))
        self.assertEqual(abi["url"], "https://district71.org/?tsml_meeting=sunshine-4")
        unknown = M.area_of("Nowhereville", "TX", dict(self.feed["aadallas"], in_area=True))
        self.assertTrue(unknown[0])
        self.assertFalse(M.area_of("Nowhereville", "TX", dict(self.feed["aadallas"], in_area=False))[0])

    def test_office_types(self):
        feed = dict(self.feed["aadallas"], add_types=["S"], region_types={"richardson": ["W"]})
        rows = json.loads(fx("aadallas_feed.json"))
        rec, _ = M.to_record(next(m for m in rows if m["name"] == "Richardson Group"), feed, "GR")
        self.assertIn("S", rec["types"])
        self.assertIn("W", rec["types"])
        self.assertEqual(rec["lang"], "es")


# =========================================================================== one run
class Collect(unittest.TestCase):
    def test_all_offices_with_keys_from_the_key_page(self):
        web = FakeWeb()
        res = M.collect(web, {}, feeds_cfg(), env={})
        self.assertTrue(res["ok"])
        self.assertEqual([u for u, _ in web.calls].count(KEY_PAGE), 1, "the key page is read once")
        self.assertIn((DALLAS_FEED, {"key": FAKE_KEY}), web.calls)
        self.assertIn((FW_FEED, {"key": FAKE_KEY_FW}), web.calls)
        self.assertIn(("https://www.tyler-aa.org/meetings/", {"tsml-day": "any", "tsml-type": "GR"}), web.calls)
        names = [r["name"] for r in res["items"]]
        # 3 Dallas + 5 Fort Worth (Grand Prairie shared) + 3 Tyler + 1 Abilene
        self.assertEqual(len(res["items"]), 11)
        gp = [r for r in res["items"] if r["city"] == "Grand Prairie"]
        self.assertEqual(len(gp), 1)
        self.assertEqual(gp[0]["sources"], ["aadallas", "fortworthaa"])
        self.assertEqual(gp[0]["name"], "Grand Prairie Group")
        self.assertEqual(res["stats"]["duplicates_merged"], 1)
        self.assertEqual((res["stats"]["in_area"], res["stats"]["nearby"]), (9, 2))
        self.assertEqual([(r["day"], r["time"]) for r in res["items"]],
                         sorted((r["day"], r["time"]) for r in res["items"]))
        self.assertIn("Houston Test Group", names)
        self.assertEqual({f["id"]: f["key_from"] for f in res["feeds"]}["aadallas"], "key_source")
        self.assertEqual(set(res["type_labels"]), {c for r in res["items"] for c in r["types"]})
        self.assertEqual(res["type_labels"]["GR"], {"en": "Grapevine", "es": "Grapevine"})
        dump = json.dumps(res)
        self.assertNotIn(FAKE_KEY, dump)
        self.assertNotIn(FAKE_KEY_FW, dump)
        for v in PERSONAL_VALUES:
            self.assertNotIn(v, dump)

    def test_ids_do_not_depend_on_which_office_answered(self):
        a = {r["id"] for r in M.collect(FakeWeb(), {}, feeds_cfg(), env={})["items"]}
        b = {r["id"] for r in M.collect(FakeWeb(fail={DALLAS_FEED}), {}, feeds_cfg(), env={})["items"]}
        gp = next(r["id"] for r in M.collect(FakeWeb(), {}, feeds_cfg(), env={})["items"] if r["city"] == "Grand Prairie")
        self.assertIn(gp, b)
        self.assertTrue(b <= a)

    def test_without_a_key_the_public_page_is_read(self):
        web = FakeWeb(fail={KEY_PAGE, FW_FEED})
        res = M.collect(web, {}, feeds_cfg(), env={})
        fw = next(f for f in res["feeds"] if f["id"] == "fortworthaa")
        self.assertEqual((fw["ok"], fw["method"]), (True, "page"))
        self.assertIn("fortworthaa.org/wp-content/tsml-cache-0123456789.json?1790026838", " ".join(u for u, _ in web.calls))
        self.assertIn("TSML_KEY_FORTWORTHAA", fw["note"])
        self.assertNotIn((FW_FEED, {}), web.calls, "a keyed list is never asked without its key")

    def test_a_failed_office_keeps_its_previous_meetings(self):
        first = M.collect(FakeWeb(), {}, feeds_cfg(), env={})
        prev = {"items": [M.to_item(r) for r in first["items"]]}
        # Tyler's page is down today: its 3 meetings stay; Dallas and Fort Worth are fresh.
        web = FakeWeb(fail={"https://www.tyler-aa.org/meetings/"})
        res = M.collect(web, prev, feeds_cfg(), env={})
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["items"]), len(first["items"]))
        tyler = next(f for f in res["feeds"] if f["id"] == "tyleraa")
        self.assertEqual((tyler["ok"], tyler["count"]), (False, 3))
        self.assertIn("HTTP 503", tyler["error"])
        self.assertEqual(res["stats"]["kept_from_before"], 3)
        self.assertTrue(res["warnings"])
        # a shared meeting whose other office still lists it is not duplicated
        web = FakeWeb(fail={FW_FEED, "https://fortworthaa.org/meetings/"})
        res = M.collect(web, prev, feeds_cfg(), env={})
        gp = [r for r in res["items"] if r["city"] == "Grand Prairie"]
        self.assertEqual(len(gp), 1)
        self.assertEqual(gp[0]["sources"], ["aadallas", "fortworthaa"])

    def test_an_empty_list_or_a_changed_page_keeps_the_previous_meetings(self):
        first = M.collect(FakeWeb(), {}, feeds_cfg(), env={})
        prev = {"items": [M.to_item(r) for r in first["items"]]}

        class Broken(FakeWeb):
            def __call__(self, url, params):
                if url == D71_FEED:
                    self.calls.append((url, dict(params or {})))
                    return "[]", url
                if url == "https://www.tyler-aa.org/meetings/":
                    self.calls.append((url, dict(params or {})))
                    return fx("tyleraa_page.html").replace("var locations", "var places"), url
                if url == "https://district71.org/meetings/":
                    raise M.FeedError("district71.org did not answer")
                return super().__call__(url, params)
        res = M.collect(Broken(), prev, feeds_cfg(), env={})
        by = {f["id"]: f for f in res["feeds"]}
        self.assertFalse(by["tyleraa"]["ok"])
        self.assertIn("changed format", by["tyleraa"]["error"])
        self.assertFalse(by["district71"]["ok"])
        self.assertIn("empty", by["district71"]["error"])
        self.assertEqual((by["tyleraa"]["count"], by["district71"]["count"]), (3, 1), "their meetings are kept")
        self.assertEqual(len(res["items"]), len(first["items"]))

    def test_a_refused_key_tries_the_next_source(self):
        """A stale secret (HTTP 401) → the key from the key page is tried, and a warning says so."""
        class Strict(FakeWeb):
            def __call__(self, url, params):
                if url in self.keys and (params or {}).get("key") and params["key"] != self.keys[url]:
                    self.calls.append((url, dict(params)))
                    raise M.KeyRejected(f"{M._bare_host(url)} refused the request (HTTP 401) — the key was not accepted")
                return super().__call__(url, params)
        web = Strict()
        res = M.collect(web, {}, feeds_cfg(), env={"TSML_KEY_AADALLAS": "stalesecret12345"})
        dal = next(f for f in res["feeds"] if f["id"] == "aadallas")
        self.assertEqual((dal["ok"], dal["method"], dal["key_from"]), (True, "feed", "key_source"))
        self.assertIn((DALLAS_FEED, {"key": "stalesecret12345"}), web.calls)
        self.assertIn((DALLAS_FEED, {"key": FAKE_KEY}), web.calls)
        self.assertTrue(any("key from secret was not accepted" in w for w in res["warnings"]), res["warnings"])
        dump = json.dumps(res)
        self.assertNotIn("stalesecret12345", dump)
        self.assertNotIn(FAKE_KEY, dump)

    def test_nothing_readable_is_a_failed_run(self):
        web = FakeWeb(fail={DALLAS_FEED, FW_FEED, D71_FEED, KEY_PAGE, "https://www.tyler-aa.org/meetings/",
                            "https://fortworthaa.org/meetings/", "https://www.aadallas.org/meetings/",
                            "https://district71.org/meetings/"})
        res = M.collect(web, {}, feeds_cfg(), env={})
        self.assertFalse(res["ok"])
        self.assertEqual(res["items"], [])
        self.assertEqual(len(res["errors"]), 4)

    def test_disabled(self):
        res = M.collect(FakeWeb(), {}, feeds_cfg(enabled=False), env={})
        self.assertEqual((res["ok"], res["items"]), (True, []))


class Dedupe(unittest.TestCase):
    def base(self, **kw):
        r = {"name": "G", "day": 6, "time": "11:00", "end_time": None, "location": None, "address": "a",
             "street": "921 W Pioneer Pkwy", "zip": "75052", "city": "Grand Prairie", "lat": 32.7089486,
             "lng": -97.0177139, "approximate": False, "region": None, "types": ["GR"], "url": "u", "sources": ["x"]}
        r.update(kw)
        return r

    def test_same_street_written_differently_or_a_few_meters_apart(self):
        a = self.base(street="921 West Pioneer Parkway, Suite O", sources=["x"])
        b = self.base(street="921 W Pioneer Pkwy", sources=["y"], end_time="12:00")
        c = self.base(street="920 W Pioneer Pkwy", lat=32.70897, sources=["z"])
        items, merged = M.dedupe([a, b, c], ["x", "y", "z"])
        self.assertEqual((len(items), merged), (1, 2))
        self.assertEqual(items[0]["sources"], ["x", "y", "z"])
        self.assertEqual(items[0]["end_time"], "12:00")

    def test_two_meetings_of_one_office_at_one_address_stay_apart(self):
        a = self.base(url="https://x.org/meetings/a/", sources=["x"], name="Room 1 Group")
        b = self.base(url="https://x.org/meetings/b/", sources=["x"], name="Room 2 Group")
        items, merged = M.dedupe([a, b], ["x"])
        self.assertEqual((len(items), merged), (2, 0))
        self.assertNotEqual(items[0]["id"], items[1]["id"])

    def test_online_meetings_without_a_street_get_their_own_ids(self):
        a = self.base(street=None, zip=None, city=None, lat=None, lng=None, attendance="online", name="Online GV",
                      url="https://x.org/meetings/online-gv/", sources=["x"])
        b = self.base(street=None, zip=None, city=None, lat=None, lng=None, attendance="online", name="Online GV",
                      url="https://y.org/meetings/online-gv/", sources=["y"])
        items, _ = M.dedupe([a, b], ["x", "y"])
        self.assertEqual(len({i["id"] for i in items}), 2)
        self.assertEqual(M.record_id(a), M.record_id(dict(a, name="Renamed")), "stable: the page, not the name")

    def test_other_time_or_place_stays_apart(self):
        items, _ = M.dedupe([self.base(), self.base(time="12:00", sources=["y"]),
                             self.base(street="1 Main St", lat=33.0, lng=-96.0, sources=["z"])], ["x", "y", "z"])
        self.assertEqual(len(items), 3)


# =========================================================================== data/site/meetings.json
class SiteJson(unittest.TestCase):
    def setUp(self):
        res = M.collect(FakeWeb(), {}, feeds_cfg(), env={})
        self.env = {"updated": "2026-09-24T12:00:00Z", "items": [M.to_item(r) for r in res["items"]],
                    "feeds": res["feeds"], "type_labels": res["type_labels"]}
        self.site = M.build_site(self.env, feeds_cfg())

    def test_contract(self):
        s = self.site
        self.assertEqual(set(s), {"updated", "fixture", "type", "sources", "groups", "items", "type_labels"})
        self.assertEqual((s["updated"], s["fixture"], s["type"]), ("2026-09-24T12:00:00Z", False, "GR"))
        it = s["items"][0]
        self.assertEqual(set(it), set(M.SITE_KEYS))
        self.assertTrue(all(i["id"].startswith("mtg:") and i["kind"] == "meeting" for i in s["items"]))
        self.assertEqual([(i["day"], i["time"]) for i in s["items"]], sorted((i["day"], i["time"]) for i in s["items"]))
        self.assertEqual([x["id"] for x in s["sources"]], ["aadallas", "fortworthaa", "tyleraa", "district71"])
        self.assertEqual(set(s["sources"][0]), {"id", "name", "url", "in_area", "area_label", "ok", "updated", "count", "error"})

    def test_groups_and_nearby(self):
        s = self.site
        self.assertEqual(s["groups"][0], {"id": "neta65", "in_area": True,
                                          "label": {"en": "Our Area (NETA 65)", "es": "Nuestra Área (NETA 65)"},
                                          "count": 9})
        self.assertEqual([(g["id"], g["count"]) for g in s["groups"][1:]], [("fortworthaa", 2)])
        hou = next(i for i in s["items"] if i["name"] == "Houston Test Group")
        self.assertEqual(hou["nearby"], {"id": "fortworthaa", "label": {"en": "Fort Worth area", "es": "Zona de Fort Worth"}})
        rich = next(i for i in s["items"] if i["name"] == "Richardson Group")
        self.assertIsNone(rich["nearby"])
        self.assertEqual(rich["directions_url"], "https://www.google.com/maps/dir/?api=1&destination=32.961562,-96.699295")

    def test_no_directions_for_online_or_approximate(self):
        self.assertIsNone(M.directions_url({"attendance": "online", "lat": 1.0, "lng": 1.0}))
        self.assertIsNone(M.directions_url({"attendance": "in_person", "approximate": True, "lat": 1.0, "lng": 1.0}))

    def test_nothing_secret_or_personal(self):
        dump = json.dumps(self.site)
        for v in (FAKE_KEY, FAKE_KEY_FW, "key=", *PERSONAL_VALUES):
            self.assertNotIn(v, dump)

    def test_switched_off_meetings_or_office_leave_the_site_at_once(self):
        self.assertEqual(M.build_site(self.env, feeds_cfg(enabled=False))["items"], [])
        cfg = feeds_cfg()
        for f in cfg["meetings"]["feeds"]:
            if f["id"] == "tyleraa":
                f["enabled"] = False
        s = M.build_site(self.env, cfg)
        self.assertFalse(any("tyleraa" in i["sources"] for i in s["items"]))
        self.assertEqual(len(s["items"]), len(self.site["items"]) - 3)
        self.assertNotIn("tyleraa", [x["id"] for x in s["sources"]])

    def test_empty_and_broken_input(self):
        self.assertEqual(M.build_site({}, feeds_cfg())["items"], [])
        self.assertEqual(M.build_site(None, feeds_cfg())["groups"], [])
        e = M.empty_site()
        self.assertEqual((e["items"], e["groups"], e["sources"]), ([], [], []))


class HttpFetch(unittest.TestCase):
    """http_fetch(): robots.txt is asked about the real address; a keyed request never leaves its site."""

    class Resp:
        def __init__(self, url, status=200, location=None, body=b"[]"):
            self.url, self.status_code, self.content = url, status, body
            self.headers = {"Location": location} if location else {}
            self.is_redirect = bool(location) and status in (301, 302, 303, 307, 308)

    class Http:
        def __init__(self, answers, disallow=()):
            self.answers, self.disallow = list(answers), disallow
            self.asked, self.gets = [], []

        def allowed(self, url):
            self.asked.append(url)
            return not any(d in url for d in self.disallow)

        def get(self, url, **kw):
            self.gets.append((url, kw))
            return self.answers.pop(0)

    def test_robots_is_checked_with_the_query(self):
        http = self.Http([], disallow=("tsml-type=",))
        fetch = M.http_fetch(http)
        with self.assertRaises(M.FeedError) as cm:
            fetch("https://x.org/meetings/", {"tsml-day": "any", "tsml-type": "GR"})
        self.assertIn("tsml-type=GR", http.asked[0])
        self.assertNotIn("tsml-type", str(cm.exception))
        self.assertEqual(http.gets, [])

    def test_a_redirect_to_another_site_is_not_followed_with_the_key(self):
        http = self.Http([self.Resp(DALLAS_FEED + "&key=" + FAKE_KEY, 302,
                                    "https://evil.example.com/wp-admin/admin-ajax.php?action=meetings&key=" + FAKE_KEY)])
        fetch = M.http_fetch(http)
        with self.assertRaises(M.FeedError) as cm:
            fetch(DALLAS_FEED, {"key": FAKE_KEY})
        self.assertIn("another site", str(cm.exception))
        self.assertEqual(len(http.gets), 1, "evil.example.com is never asked")
        self.assertIs(http.gets[0][1]["allow_redirects"], False)

    def test_a_redirect_on_the_same_site_is_followed(self):
        http = self.Http([self.Resp(DALLAS_FEED, 301, "https://aadallas.org/wp-admin/admin-ajax.php?action=meetings&key=" + FAKE_KEY),
                          self.Resp("https://aadallas.org/wp-admin/admin-ajax.php?action=meetings&key=" + FAKE_KEY, 200,
                                    body=b'[{"name": "x"}]')])
        text, final = M.http_fetch(http)(DALLAS_FEED, {"key": FAKE_KEY})
        self.assertEqual(json.loads(text), [{"name": "x"}])
        self.assertNotIn(FAKE_KEY, final)
        self.assertEqual(len(http.gets), 2)

    def test_a_refused_key_is_its_own_error(self):
        http = self.Http([self.Resp(DALLAS_FEED, 401)])
        with self.assertRaises(M.KeyRejected):
            M.http_fetch(http)(DALLAS_FEED, {"key": FAKE_KEY})
        http = self.Http([self.Resp("https://x.org/meetings/", 403)])
        with self.assertRaises(M.FeedError) as cm:
            M.http_fetch(http)("https://x.org/meetings/", None)
        self.assertNotIsInstance(cm.exception, M.KeyRejected)


class Settings(unittest.TestCase):
    def test_defaults(self):
        st = M.settings(feeds_cfg())
        by = {f["id"]: f for f in st["feeds"]}
        self.assertEqual(by["aadallas"]["page"], "https://www.aadallas.org/meetings/")
        self.assertEqual(by["aadallas"]["methods"], ["feed", "page"])
        self.assertEqual(by["tyleraa"]["methods"], ["page"])
        self.assertEqual(by["fortworthaa"]["url"], "https://fortworthaa.org")

    def test_the_real_config(self):
        st = M.settings()
        self.assertEqual(st["type"], "GR")
        by = {f["id"]: f for f in st["feeds"]}
        self.assertEqual(by["tyleraa"]["methods"], ["page"], "tyler-aa.org's robots.txt does not allow /wp-admin/")
        self.assertTrue(all(f["region_label"]["en"] and f["region_label"]["es"] for f in st["feeds"]))


if __name__ == "__main__":
    unittest.main()
