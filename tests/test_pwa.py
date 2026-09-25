"""The installable app / offline use / Data saver ("works with a weak signal") — static checks that
need no browser (the browser checks are in the QA scripts; README → "Install the app, offline use
and Data saver").

  * Icons      — every manifest icon exists at its size; "any" icons have transparent corners;
                 maskable + iPhone icons are opaque and keep the mark inside the 80 % safe circle.
  * Strings    — src/_i18n/pwa.json: every key in English AND Spanish, within the length budgets of
                 the design spec; every pwa.* key the offline page uses exists.
  * Pages      — the offline page is out of the sitemap/collections and noindex; base.njk links the
                 manifest, loads pwa.js and versions styles/scripts with build.version.
  * Worker     — sw-core.js only handles GET requests to our own origin, revalidates navigations,
                 never skips waiting on its own, and keeps visitors' saved pages across versions.

    python -m unittest tests.test_pwa -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "src" / "assets" / "img"


def read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class Icons(unittest.TestCase):
    ANY = {"app-icon-192.png": 192, "app-icon-512.png": 512}
    FULL = {"app-icon-maskable-192.png": 192, "app-icon-maskable-512.png": 512, "apple-touch-icon-180.png": 180}

    def test_sizes(self):
        for name, size in {**self.ANY, **self.FULL}.items():
            with self.subTest(name=name), Image.open(IMG / name) as im:
                self.assertEqual(im.size, (size, size))

    def test_any_icons_have_transparent_corners(self):
        for name, size in self.ANY.items():
            with Image.open(IMG / name) as f:
                im = f.convert("RGBA")
            with self.subTest(name=name):
                self.assertEqual(im.getpixel((0, 0))[3], 0)
                self.assertEqual(im.getpixel((size - 1, size - 1))[3], 0)
                self.assertEqual(im.getpixel((size // 2, size // 2))[3], 255)

    def test_full_bleed_icons_are_opaque_and_mark_is_in_safe_zone(self):
        for name, size in self.FULL.items():
            with Image.open(IMG / name) as f:
                im = f.convert("RGBA")
            with self.subTest(name=name):
                self.assertEqual(im.getchannel("A").getextrema(), (255, 255))
                # the mark (gold grapes, green leaf) never leaves the maskable safe circle (radius 40 %)
                c = (size - 1) / 2
                far = 0.0
                px = im.load()
                for y in range(size):
                    for x in range(size):
                        r, g, b, _ = px[x, y]
                        if r > 180 or g > 170:
                            far = max(far, math.hypot(x - c, y - c))
                self.assertGreater(far, 0.15 * size, "the mark is missing")
                self.assertLess(far, 0.40 * size)

    def test_manifest_template_lists_the_icons(self):
        src = read("src", "pages", "manifest.11ty.js")
        for name in [*self.ANY, "app-icon-maskable-192.png", "app-icon-maskable-512.png"]:
            self.assertIn(name, src)
        self.assertIn('"maskable"', src)
        self.assertIn('display: "standalone"', src)
        self.assertIn('short_name: "GV/LV 65"', src)
        self.assertIn('name: "Grapevine / La Viña — NETA 65"', src)


class Strings(unittest.TestCase):
    def setUp(self):
        self.pwa = json.loads(read("src", "_i18n", "pwa.json"))

    def test_every_key_in_both_languages(self):
        for key, v in self.pwa.items():
            with self.subTest(key=key):
                self.assertTrue(key.startswith("pwa."))
                self.assertTrue((v.get("en") or "").strip())
                self.assertTrue((v.get("es") or "").strip())

    def test_length_budgets(self):
        # design spec §1.2 / §1.18: hero subtitle 110 / 135, eyebrow 32 / 38 characters
        for key in ("pwa.offline.sub", "pwa.offline.sub_online"):
            self.assertLessEqual(len(self.pwa[key]["en"]), 110, key)
            self.assertLessEqual(len(self.pwa[key]["es"]), 135, key)
        self.assertLessEqual(len(self.pwa["pwa.offline.eyebrow"]["en"]), 32)
        self.assertLessEqual(len(self.pwa["pwa.offline.eyebrow"]["es"]), 38)

    def test_offline_page_keys_exist(self):
        used = set(re.findall(r"['\"](pwa\.[a-z0-9_.]+)['\"]", read("src", "pages", "offline.njk")))
        self.assertTrue(used)
        self.assertEqual(used - set(self.pwa), set())

    def test_no_pdf_wording(self):
        for key, v in self.pwa.items():
            for lang in ("en", "es"):
                self.assertNotRegex(v[lang], r"\bPDF\b", f"{key}.{lang}")


class Pages(unittest.TestCase):
    def test_offline_page_is_hidden_from_sitemap_and_search_engines(self):
        src = read("src", "pages", "offline.njk")
        self.assertRegex(src, r"(?m)^sitemap: false$")
        self.assertRegex(src, r"(?m)^eleventyExcludeFromCollections: true$")
        self.assertRegex(src, r"(?m)^pageKey: offline$")
        base = read("src", "_includes", "layouts", "base.njk")
        self.assertIn('pageKey == "offline"', base)

    def test_base_layout_wiring(self):
        base = read("src", "_includes", "layouts", "base.njk")
        self.assertIn("'/manifest.webmanifest' | lurl(L)", base)
        self.assertIn('/assets/js/pwa.js?v={{ build.version }}', base)
        self.assertIn('/assets/css/main.css?v={{ build.version }}', base)
        self.assertEqual(len(re.findall(r'<meta name="theme-color"[^>]*media="\(prefers-color-scheme: (?:light|dark)\)"', base)), 2)
        self.assertIn('rel="apple-touch-icon" href="/assets/img/apple-touch-icon-180.png"', base)
        self.assertIn('@import "./areas/pwa.css";', read("src", "assets", "css", "main.css"))

    def test_build_version_ignores_synced_content(self):
        src = read("src", "_data", "build.js")
        roots = re.search(r"const ROOTS = \[([^\]]+)\]", src).group(1)
        self.assertNotIn('"data"', roots)
        self.assertIn('path.join("src", "assets", "cache")', src)


class Worker(unittest.TestCase):
    def setUp(self):
        self.core = read("src", "_includes", "pwa", "sw-core.js")
        self.tpl = read("src", "pages", "sw.11ty.js")

    def test_only_our_own_get_requests(self):
        self.assertIn('if (req.method !== "GET") return;', self.core)
        self.assertIn("if (url.origin !== self.location.origin || !url.pathname.startsWith(BASE)) return;", self.core)
        self.assertIn('if (req.headers.has("range")) return;', self.core)

    def test_navigations_are_network_first_and_revalidated(self):
        self.assertIn('fetch(new Request(event.request, { cache: "no-cache" }))', self.core)
        self.assertRegex(self.core, r"const NAV_TIMEOUT = 4000;")

    def test_update_waits_for_the_visitor(self):
        install = self.core[self.core.index('addEventListener("install"'):self.core.index('addEventListener("activate"')]
        self.assertNotIn("skipWaiting", install)
        self.assertIn('if (d.type === "SKIP_WAITING") self.skipWaiting();', self.core)

    def test_saved_pages_survive_new_versions(self):
        self.assertIn('pages: PREFIX + "pages-v1"', self.core)
        self.assertIn('saved: PREFIX + "saved-v1"', self.core)
        self.assertIn('shell: PREFIX + "shell-" + V', self.core)
        self.assertRegex(self.core, r"LIMIT = \{ static: \d+, pages: 80, img: 200")

    def test_template_config(self):
        for needle in ('permalink: "/sw.js"', "offline.en, offline.es", '"monthly/{month}/"', '"meetings/"', '"contribute/"', '"shop/"'):
            self.assertIn(needle, self.tpl)


if __name__ == "__main__":
    unittest.main()
