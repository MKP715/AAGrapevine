"""Record your story by phone (scripts/sync/audio_project.py → build_data → data/site/audio_project.json).

Fixtures in tests/fixtures/audio_project/ are trimmed copies of the official pages (September 2026):
aagrapevine.org/audio-portal, aalavina.org/graba-tu-historia and aalavina.org/instrucciones-graba-tu-historia
(their content block only; a menu and a footer with other addresses were added around it).
Run:  python -m unittest tests.test_audio_project -v   (CI: python -m unittest discover -s tests)
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import audio_project as A  # noqa: E402
from scripts.sync import build_data as B  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "audio_project"
GV, LV = "https://www.aagrapevine.org", "https://www.aalavina.org"
GV_PAGE, LV_PAGE, LV_STEPS = f"{GV}/audio-portal", f"{LV}/graba-tu-historia", f"{LV}/instrucciones-graba-tu-historia"
CFG = {"sources": {"grapevine": {"base": GV}, "lavina": {"base": LV}}}


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


PAGES = {GV_PAGE: "gv_audio_portal.html", LV_PAGE: "lv_graba_tu_historia.html", LV_STEPS: "lv_instrucciones.html"}


def fetcher(overrides: dict | None = None):
    """Fixture pages by URL; overrides {url: html or None} replace or fail single pages. Records the calls."""
    calls: list[str] = []

    def fetch(url: str) -> str | None:
        calls.append(url)
        if overrides and url in overrides:
            return overrides[url]
        name = PAGES.get(url)
        return fx(name) if name else None
    fetch.calls = calls
    return fetch


class Helpers(unittest.TestCase):
    def test_cloudflare_email_is_decoded(self):
        # the first byte is the key: 0x42 ^ each following byte
        hexstr = "42" + "".join(f"{ord(c) ^ 0x42:02x}" for c in "webcoord@aagrapevine.org")
        self.assertEqual(A.decode_cfemail(hexstr), "webcoord@aagrapevine.org")
        self.assertIsNone(A.decode_cfemail("zz"))
        self.assertIsNone(A.decode_cfemail("42" + "".join(f"{ord(c) ^ 0x42:02x}" for c in "not an address")))

    def test_phone_numbers(self):
        self.assertEqual(A.phones("Call 559-726-1216 now"), [("559-726-1216", "+15597261216")])
        self.assertEqual(A.phones("al (559) 670-1601."), [("(559) 670-1601", "+15596701601")])
        self.assertEqual(A.phones("+1 559.726.1216 or 559-726-1216")[0][1], "+15597261216")
        self.assertEqual(len(A.phones("+1 559.726.1216 or 559-726-1216")), 1)   # the same number once
        self.assertEqual(A.phones("order 2024-559-7261216 / zip 75088"), [])

    def test_settings_follow_config(self):
        st = A.settings({"sources": {"grapevine": {"base": "https://gv.example", "audio_project": "/new-audio"},
                                     "lavina": {"record_story": "https://lv.example/graba"}}})
        self.assertEqual(st["gv"]["page"], "https://gv.example/new-audio")
        self.assertEqual(st["lv"]["page"], "https://lv.example/graba")
        self.assertEqual(st["lv"]["instructions"], f"{LV}/instrucciones-graba-tu-historia")


class Grapevine(unittest.TestCase):
    def setUp(self):
        self.d = A.parse_gv(fx("gv_audio_portal.html"), GV_PAGE)

    def test_phone_keys_and_length(self):
        d = self.d
        self.assertEqual((d["phone"], d["tel"]), ("559-726-1216", "+15597261216"))
        self.assertEqual(d["keys"], {"record": "1", "finish": "#", "save": "1", "permission": "2"})
        self.assertEqual((d["minutes_min"], d["minutes_max"]), (6, 8))
        self.assertEqual(len(d["steps_text"]), 4)
        self.assertTrue(d["steps_text"][0].startswith("Call the voicemail system"))

    def test_do_it_yourself_email_and_notes(self):
        # the address is Cloudflare-protected on the page; the menu's mailto: (outside <main>) is ignored
        self.assertEqual(self.d["email"], "webcoord@aagrapevine.org")
        self.assertEqual(self.d["formats"], ["WAV", "MP3"])
        self.assertTrue(self.d["no_speakers"])

    def test_playlists_and_channel(self):
        titles = [p["title"] for p in self.d["playlists"]]
        self.assertEqual(titles, ["Sponsorship", "Voices of Women", "From Relapse to Recovery", "YPAA World Tour"])
        self.assertTrue(all("list=" in p["url"] and "si=" not in p["url"] for p in self.d["playlists"]))
        self.assertEqual(self.d["channel_url"], "https://www.youtube.com/@AAGRAPEVINE")

    def test_changed_process_is_an_error(self):
        html = fx("gv_audio_portal.html").replace("Press 2 to give AA Grapevine permission", "Say that you agree")
        with self.assertRaisesRegex(A.AudioParseError, "permission"):
            A.parse_gv(html, GV_PAGE)

    def test_unrelated_page_is_an_error(self):
        with self.assertRaises(A.AudioParseError):
            A.parse_gv("<html><body><main><h1>Site maintenance</h1></main></body></html>", GV_PAGE)


class LaVina(unittest.TestCase):
    def test_main_page(self):
        d = A.parse_lv(fx("lv_graba_tu_historia.html"), LV_PAGE)
        self.assertEqual((d["phone"], d["tel"]), ("(559) 670-1601", "+15596701601"))
        self.assertEqual(d["minutes_max"], 7)
        self.assertEqual(d["email"], "lveditorial@aagrapevine.org")
        self.assertEqual(d["formats"], ["WAV", "MP3"])
        self.assertTrue(d["no_speakers"])
        self.assertEqual(d["instructions_url"], LV_STEPS)
        self.assertEqual(d["tips_url"], f"{LV}/consejos-de-grabacion")
        self.assertEqual(d["topics_url"], f"{LV}/temas-sugeridos")
        self.assertNotIn("sample_url", d)                 # not linked there: comes from config/site.yml

    def test_instructions(self):
        d = A.parse_lv_instructions(fx("lv_instrucciones.html"), LV_STEPS)
        self.assertEqual(d["keys"], {"record": "1"})
        self.assertTrue(d["permission_text"].startswith("Otorgo a La Viña los derechos de autor"))
        self.assertFalse(d["permission_text"].startswith("“"))
        self.assertTrue(d["long_distance"])
        self.assertEqual(len(d["steps_text"]), 4)
        self.assertEqual(d["instructions_phone"], "+15596701601")


class Collect(unittest.TestCase):
    def test_both_parts(self):
        f = fetcher()
        res = A.collect(f, {"items": []}, CFG)
        self.assertEqual(res["errors"], [])
        by = {i["id"]: i for i in res["items"]}
        self.assertEqual(set(by), {"audio:gv", "audio:lv"})
        self.assertEqual(by["audio:lv"]["extra"]["sample_url"], f"{LV}/audio-de-muestra")
        self.assertEqual(by["audio:lv"]["extra"]["keys"], {"record": "1"})
        self.assertEqual((by["audio:gv"]["lang"], by["audio:lv"]["lang"]), ("en", "es"))
        self.assertEqual(sorted(f.calls), sorted(PAGES))     # 3 requests, each page once

    def test_failed_part_keeps_previous_data(self):
        prev = {"items": A.collect(fetcher(), {"items": []}, CFG)["items"]}
        old = copy.deepcopy(next(i for i in prev["items"] if i["id"] == "audio:gv"))
        broken = fx("gv_audio_portal.html").replace("559-726-1216", "five five nine")
        res = A.collect(fetcher({GV_PAGE: broken}), prev, CFG)
        self.assertEqual(len(res["errors"]), 1)
        self.assertTrue(res["errors"][0].startswith("gv:"))
        by = {i["id"]: i for i in res["items"]}
        self.assertEqual(by["audio:gv"], old)                # unchanged, incl. its `checked`
        self.assertIn("audio:lv", by)
        self.assertEqual(res["stats"]["fresh"], ["lv"])

    def test_page_down_without_previous_data(self):
        res = A.collect(fetcher({GV_PAGE: None, LV_PAGE: None}), {"items": []}, CFG)
        self.assertEqual(res["items"], [])
        self.assertEqual(len(res["errors"]), 2)

    def test_instructions_down_uses_previous_steps(self):
        prev = {"items": A.collect(fetcher(), {"items": []}, CFG)["items"]}
        res = A.collect(fetcher({LV_STEPS: None}), prev, CFG)
        # both main pages were read today: a note (warning), not a failed source
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["warnings"]), 1)
        self.assertTrue(res["warnings"][0].startswith("lv: instructions"))
        self.assertEqual(res["stats"]["fresh"], ["gv", "lv"])
        lv = next(i for i in res["items"] if i["id"] == "audio:lv")["extra"]
        self.assertEqual(lv["keys"], {"record": "1"})
        self.assertTrue(lv["permission_text"].startswith("Otorgo"))
        self.assertEqual(lv["tel"], "+15596701601")          # the main page is still read today

    def test_instructions_down_first_time_is_an_error(self):
        res = A.collect(fetcher({LV_STEPS: None}), {"items": []}, CFG)
        self.assertNotIn("audio:lv", {i["id"] for i in res["items"]})
        self.assertTrue(any(e.startswith("lv:") for e in res["errors"]))


class FakeCtx:
    def __init__(self, env: dict):
        self.raw = {"audio_project": env}

    def items(self, name: str) -> list[dict]:
        return B.live(self.raw.get(name, {}).get("items", []))


class SiteFile(unittest.TestCase):
    def env(self) -> dict:
        items = A.collect(fetcher(), {"items": []}, CFG)["items"]
        return {"updated": "2026-09-25T14:09:42Z", "ok": True, "items": items}

    def test_site_doc(self):
        doc = B.build_audio_project(FakeCtx(self.env()))
        self.assertEqual(doc["updated"], "2026-09-25T14:09:42Z")
        self.assertEqual(doc["gv"]["tel"], "+15597261216")
        # both numbers in the site's one style, however each official page writes it
        self.assertEqual((doc["gv"]["phone"], doc["lv"]["phone"]), ("(559) 726-1216", "(559) 670-1601"))
        self.assertEqual(doc["gv"]["keys"]["finish"], "#")
        self.assertEqual(len(doc["gv"]["playlists"]), 4)
        self.assertNotIn("steps_text", doc["gv"])          # the page words the steps itself
        self.assertEqual(doc["lv"]["minutes_max"], 7)
        self.assertTrue(doc["lv"]["permission_text"].startswith("Otorgo"))
        self.assertEqual(doc["checked"], min(doc["gv"]["checked"], doc["lv"]["checked"]))

    def test_unusable_values_are_dropped(self):
        env = self.env()
        for it in env["items"]:
            if it["id"] == "audio:gv":
                it["extra"].update(tel="call us", keys={"record": "1", "save": "<b>"}, email="webcoord at aagrapevine")
            else:
                it["extra"]["keys"] = {"record": "12"}
        doc = B.build_audio_project(FakeCtx(env))
        self.assertIsNone(doc["gv"])                        # no number the page can dial → no card data
        self.assertEqual(doc["lv"]["keys"], {})
        env2 = self.env()
        next(i for i in env2["items"] if i["id"] == "audio:gv")["extra"]["email"] = "webcoord at aagrapevine"
        self.assertIsNone(B.build_audio_project(FakeCtx(env2))["gv"]["email"])

    def test_nothing_synced_yet(self):
        doc = B.build_audio_project(FakeCtx({}))
        self.assertEqual((doc["gv"], doc["lv"], doc["checked"]), (None, None, None))

    def test_listed_on_status(self):
        self.assertIn("audio_project", [s[0] for s in B.SOURCES])


if __name__ == "__main__":
    unittest.main()
