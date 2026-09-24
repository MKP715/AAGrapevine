"""Offline tests for the published-writers spotlight: where a writer is from (scripts/sync/geo.py +
data/geo/texas_places.json), the magazine archive backfill (scripts/sync/articles.py) and
data/site/spotlight.json (scripts/sync/build_data.py). No network.

    python -m unittest tests.test_spotlight -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import common  # noqa: E402
from scripts.sync import geo  # noqa: E402
from scripts.sync.geo import classify_location, normalize_place  # noqa: E402

PROBE = ROOT / ".tmp" / "probe2"        # pages saved from the live sites (optional, not in git)


def scope(text, lang=None):
    return classify_location(text, lang)["scope"]


# =========================================================================== gazetteer + normalization
class Normalize(unittest.TestCase):
    def test_accents_case_punctuation(self):
        self.assertEqual(normalize_place("San José"), "san jose")
        self.assertEqual(normalize_place("  DALLAS  "), "dallas")
        self.assertEqual(normalize_place("Morgan's Point"), "morgans point")
        self.assertEqual(normalize_place("Morgan’s Point"), "morgans point")
        self.assertEqual(normalize_place("Little River-Academy"), "little river academy")

    def test_saint_fort_mount(self):
        for a, b in (("St. Jo", "Saint Jo"), ("St Jo", "saint jo"), ("Ft. Worth", "Fort Worth"),
                     ("Ft Worth", "fort worth"), ("Mt. Pleasant", "Mount Pleasant")):
            self.assertEqual(normalize_place(a), normalize_place(b), (a, b))

    def test_type_word_only_stripped_on_request(self):
        self.assertEqual(normalize_place("Texas City city", strip_type=True), "texas city")
        self.assertEqual(normalize_place("Texas City"), "texas city")
        self.assertEqual(normalize_place("Neches CDP", strip_type=True), "neches")
        self.assertEqual(normalize_place("Jersey Village city", strip_type=True), "jersey village")


class Gazetteer(unittest.TestCase):
    def test_file_is_small_and_complete(self):
        path = ROOT / "data" / "geo" / "texas_places.json"
        self.assertLess(path.stat().st_size, 80_000)
        places = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreater(len(places), 1700)
        counties = {c for v in places.values() for c in v}
        self.assertEqual(len(counties), 254)
        self.assertEqual(places["paris"], ["Lamar"])
        self.assertEqual(places["arlington"], ["Tarrant"])
        self.assertIn("Harris", places["houston"])
        self.assertNotIn("Houston", places["houston"])      # Houston city is not in Houston County
        for k in places:
            self.assertEqual(k, normalize_place(k), "keys are stored normalized")

    def test_every_area65_county_is_a_texas_county(self):
        known = set(geo.texas_counties())
        area = geo.neta65_counties()
        self.assertEqual(len(area), 75)
        self.assertEqual(area - known, set(), "a county in config spotlight.neta65_counties is misspelled")

    def test_builder_parses_census_rows(self):
        from scripts.dev import build_texas_gazetteer as B
        text = ("STATE|STATEFP|COUNTYFP|COUNTYNAME|PLACEFP|PLACENS|PLACENAME|TYPE|CLASSFP|FUNCSTAT\n"
                "TX|48|113|Dallas County|19000|1|Dallas city|INCORPORATED PLACE|C1|A\n"
                "TX|48|085|Collin County|19000|1|Dallas city|INCORPORATED PLACE|C1|A\n"
                "TX|48|167|Galveston County|72392|1|Texas City city|INCORPORATED PLACE|C1|A\n"
                "TX|48|029|Bexar County|1|1|St. Hedwig town|INCORPORATED PLACE|C1|A\n"
                "TX|48|001|Anderson County|50544|1|Neches CDP|CENSUS DESIGNATED PLACE|U1|S\n")
        self.assertEqual(B.parse(text), {"dallas": ["Collin", "Dallas"], "neches": ["Anderson"],
                                         "saint hedwig": ["Bexar"], "texas city": ["Galveston"]})
        with self.assertRaises(ValueError):
            B.parse("NOT|A|CENSUS|FILE\n")


# =========================================================================== classify_location
class ClassifyLocation(unittest.TestCase):
    def test_real_bylines_from_the_magazines(self):
        cases = {
            "Grand Prairie, Texas": "neta65", "San Antonio, Texas": "texas", "Round Rock, Texas": "texas",
            "San José, California": "other", "Maine": "other", "Oslo": "other", "Devonport": "other",
            "Nueva Jersey": "other", "Veracruz": "other", "Tegucigalpa": "other",
            "Scarborough, Ontario": "other", "Lake County, British Columbia": "other",
            "Cheyenne, Wyoming": "other", "New York, New York": "other", "Barcelona": "other",
            "San Juan": "other", "Florida": "other",
        }
        for text, want in cases.items():
            self.assertEqual(scope(text), want, text)

    def test_texas_spellings(self):
        for text in ("Dallas, TX", "Dallas, Texas", "Dallas, Tejas", "Dallas, Texas, EE. UU.",
                     "Dallas, Texas, USA", "Dallas TX", "Dallas tx", "Dallas, Tex.", "DALLAS, TEXAS",
                     "Dallas, Texas 75201", "Dallas, TX 75201-1234", "Dallas, Texas."):
            r = classify_location(text)
            self.assertEqual(r["scope"], "neta65", text)
            self.assertEqual(r["city"], "Dallas", text)
            self.assertEqual(r["state"], "TX", text)
            self.assertEqual(r["label_en"], "Dallas, Texas", text)
        self.assertEqual(scope("Tyler, Tx."), "neta65")
        self.assertEqual(scope("Tyler Texas"), "neta65")
        self.assertEqual(scope("Fort Worth, Tejas"), "neta65")
        r = classify_location("Ft. Worth, Texas")
        self.assertEqual((r["scope"], r["city"], r["county"]), ("neta65", "Fort Worth", "Tarrant"))

    def test_texas_without_a_city(self):
        for text in ("Texas", "TX", "Tejas", "Texas, USA"):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["city"], r["county"]), ("texas", None, None), text)
            self.assertEqual(r["label_en"], "Texas")

    def test_houston_city_is_not_houston_county(self):
        r = classify_location("Houston, Texas")
        self.assertEqual((r["scope"], r["county"]), ("texas", "Harris"))
        r = classify_location("Houston County, Texas")
        self.assertEqual((r["scope"], r["county"], r["city"]), ("neta65", "Houston", None))
        self.assertEqual(r["label_es"], "Condado de Houston, Texas")
        self.assertEqual(scope("Condado de Houston, Texas"), "neta65")
        self.assertEqual(scope("Crockett, Texas"), "neta65")         # the Houston County seat
        self.assertEqual(scope("Houston County"), "other")            # no state: AL, GA, MN, TN …

    def test_same_name_elsewhere(self):
        self.assertEqual(scope("Paris, Texas"), "neta65")
        self.assertEqual(classify_location("Paris, Texas")["county"], "Lamar")
        self.assertEqual(scope("Paris"), "other")
        self.assertEqual(scope("Paris, France"), "other")
        self.assertEqual(classify_location("Paris, France")["country"], "FR")
        self.assertEqual(scope("Arlington, Texas"), "neta65")
        self.assertEqual(scope("Arlington, Virginia"), "other")
        self.assertEqual(scope("Arlington"), "other")
        self.assertEqual(scope("Athens, TX"), "neta65")
        self.assertEqual(classify_location("Athens, TX")["county"], "Henderson")
        self.assertEqual(scope("Athens"), "other")
        self.assertEqual(scope("Dallas, Georgia"), "other")
        self.assertEqual(scope("Texarkana, Arkansas"), "other")
        self.assertEqual(scope("Texarkana, Texas"), "neta65")

    def test_multi_county_place_counts_if_any_county_is_area65(self):
        r = classify_location("Grand Prairie, Texas")
        self.assertEqual(r["counties"], ["Dallas", "Ellis", "Tarrant"])
        self.assertEqual(r["county"], "Dallas")
        r = classify_location("Abilene, Texas")                        # Jones + Taylor
        self.assertEqual((r["scope"], r["county"]), ("neta65", "Taylor"))
        r = classify_location("Mabank, Texas")                         # Henderson + Kaufman
        self.assertEqual(r["scope"], "neta65")
        r = classify_location("Round Rock, Texas")                     # Travis + Williamson
        self.assertEqual((r["scope"], r["county"]), ("texas", "Williamson"))
        r = classify_location("Pecan Gap, Texas")                      # no principal county known
        self.assertEqual((r["scope"], r["county"]), ("neta65", None))
        self.assertEqual(r["counties"], ["Delta", "Fannin"])

    def test_bare_city_allowlist(self):
        for city in ("Dallas", "Fort Worth", "Tyler", "Texarkana", "Abilene", "Waco", "Plano",
                     "Wichita Falls", "McKinney", "Frisco", "Garland", "Irving", "Grand Prairie",
                     "Richardson", "Sherman", "Nacogdoches", "Corsicana", "Ft. Worth"):
            self.assertEqual(scope(city), "neta65", city)
        for city in ("Houston", "El Paso", "Lubbock", "Amarillo", "Texas City"):
            self.assertEqual(scope(city), "texas", city)
        for city in ("Arlington", "Paris", "Athens", "Jacksonville", "Palestine", "Marshall", "Denison",
                     "Longview", "Denton", "Mansfield", "Mesquite", "San Antonio", "Austin", "Laredo",
                     "Grapevine", "Henderson"):
            self.assertEqual(scope(city), "other", city)
        self.assertEqual(scope("Dallas, USA"), "neta65")
        self.assertEqual(scope("Dallas, EE. UU."), "neta65")

    def test_regions_and_neighborhoods(self):
        self.assertEqual(scope("North Texas"), "neta65")
        self.assertEqual(scope("Norte de Texas"), "neta65")
        self.assertEqual(scope("Northeast Texas"), "neta65")
        self.assertEqual(scope("DFW"), "neta65")
        self.assertEqual(scope("Dallas/Fort Worth, Texas"), "neta65")
        self.assertEqual(scope("East Texas"), "texas")
        self.assertEqual(classify_location("East Texas")["label_es"], "Este de Texas")
        self.assertEqual(scope("West Texas"), "texas")
        r = classify_location("Oak Cliff, Dallas, Texas")
        self.assertEqual((r["scope"], r["city"]), ("neta65", "Dallas"))
        r = classify_location("Kingwood, Texas")                        # not a Census place
        self.assertEqual((r["scope"], r["city"], r["county"]), ("texas", "Kingwood", None))
        self.assertEqual(scope("Área 65"), "neta65")
        self.assertEqual(scope("Smith Co., Texas"), "neta65")
        self.assertEqual(scope("Harris County, TX"), "texas")

    def test_saint_mount_and_suffixes(self):
        self.assertEqual(scope("Mt. Pleasant, Texas"), "neta65")
        self.assertEqual(scope("Mount Pleasant, TX"), "neta65")
        self.assertEqual(scope("St. Jo, Texas"), "neta65")
        self.assertEqual(scope("Saint Jo, Texas"), "neta65")
        r = classify_location("Rowlett city, Texas")
        self.assertEqual((r["scope"], r["city"], r["county"]), ("neta65", "Rowlett", "Dallas"))

    def test_other_places_labels(self):
        r = classify_location("Nueva Jersey")
        self.assertEqual((r["state"], r["country"], r["label_en"], r["label_es"]),
                         ("NJ", "US", "New Jersey", "Nueva Jersey"))
        r = classify_location("New York, New York")
        self.assertEqual((r["label_en"], r["label_es"]), ("New York, New York", "Nueva York, Nueva York"))
        r = classify_location("Lake County, British Columbia")
        self.assertEqual((r["country"], r["label_es"]), ("CA", "Lake County, Columbia Británica"))
        r = classify_location("Veracruz")
        self.assertEqual((r["country"], r["label_en"]), ("MX", "Veracruz"))
        r = classify_location("Oslo")
        self.assertEqual((r["city"], r["country"], r["label_en"], r["label_es"]), ("Oslo", None, "Oslo", "Oslo"))
        r = classify_location("Portland OR")
        self.assertEqual((r["state"], r["label_es"]), ("OR", "Portland, Oregón"))

    def test_ap_style_abbreviations_seen_in_bylines(self):
        for text, state, label in (("Memphis, Tenn.", "TN", "Memphis, Tennessee"),
                                   ("Youngsville, LA", "LA", "Youngsville, Louisiana"),
                                   ("Fresno, Calif.", "CA", "Fresno, California"),
                                   ("Victoria, B.C.", "BC", "Victoria, British Columbia"),
                                   ("Toronto, Ont.", "ON", "Toronto, Ontario")):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["state"], r["label_en"]), ("other", state, label), text)
        r = classify_location("San Juan, P.R.")
        self.assertEqual((r["scope"], r["country"], r["label_es"]), ("other", "PR", "San Juan, Puerto Rico"))
        self.assertEqual(scope("Del Rio, Texas"), "texas")               # "Del" (Delaware) only as a whole part

    def test_notes_printed_where_the_place_goes_are_not_places(self):
        for text in ('Excerpt. Original title: “Editorial: On the 9th Tradition, ” Grapevine, August 1948',
                     "Reprinted from the June 1962 Grapevine", "Título original: “Algo”", "x" * 90):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["label_en"]), ("unknown", None), text)
        self.assertEqual(scope("Grapevine, Texas"), "neta65")           # the city named Grapevine is real

    def test_unknown_and_never_raises(self):
        for text in (None, "", "   ", "Anonymous", "Anónimo", "N/A"):
            r = classify_location(text)
            self.assertEqual(r["scope"], "unknown", repr(text))
            self.assertIsNone(r["label_en"])
        for weird in ("|||", "12345", "(Area)", ",,,", "Dallas,,Texas", 42, "–"):
            self.assertIn(classify_location(weird)["scope"], geo.SCOPES)
        self.assertEqual(scope("Dallas,,Texas"), "neta65")

    def test_result_shape_and_language_independence(self):
        keys = {"scope", "city", "county", "counties", "state", "country", "label_en", "label_es"}
        for text in ("Dallas, Texas", "Oslo", None, "Texas", "Maine"):
            self.assertEqual(set(classify_location(text)), keys)
            self.assertEqual(classify_location(text, "es"), classify_location(text, "en"))

    # --- review 2026-09 (rv-spotlight): bylines that lost the Area 65 priority or printed a wrong place
    def test_town_of_west_is_not_the_region_west_texas(self):
        for text in ("West, Texas", "West, TX", "West, Tejas", "West, Texas, USA", "West, Tejas, EE. UU."):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["city"], r["county"], r["label_en"], r["label_es"]),
                             ("neta65", "West", "McLennan", "West, Texas", "West, Texas"), text)
        for text in ("West Texas", "West TX", "W. Texas", "Oeste de Texas", "West Texas, USA"):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["county"], r["label_en"], r["label_es"]),
                             ("texas", None, "West Texas", "Oeste de Texas"), text)
        r = classify_location("Panhandle, Texas")                        # the town (Carson County) …
        self.assertEqual((r["scope"], r["county"], r["label_en"]), ("texas", "Carson", "Panhandle, Texas"))
        for text in ("Texas Panhandle", "the Panhandle"):                  # … and the region
            self.assertEqual(classify_location(text)["label_en"], "Texas Panhandle", text)
        for text, label in (("East TX", "East Texas"), ("S. Texas", "South Texas"), ("N. TX", "North Texas"),
                            ("Central TX", "Central Texas")):
            self.assertEqual(classify_location(text)["label_en"], label, text)
        self.assertEqual(scope("N. TX"), "neta65")
        self.assertEqual(scope("Dallas, Fort Worth"), "neta65")           # two cities, no state: DFW

    def test_texas_written_in_parentheses_after_a_country_zip_or_extra_part(self):
        for text, county in (("Denton (Texas)", "Denton"), ("Arlington (Tejas)", "Tarrant"),
                             ("Longview (TX)", "Gregg"), ("Tyler (Texas), USA", "Smith"),
                             ("Dallas (TX, USA)", "Dallas"), ("Tyler, Texas (District 42)", "Smith"),
                             ("Dallas, Texas USA", "Dallas"), ("Dallas Texas USA", "Dallas"),
                             ("Dallas, Texas EE. UU.", "Dallas"), ("Dallas TX U.S.A.", "Dallas"),
                             ("Grand Prairie, Tejas Estados Unidos", "Dallas"), ("Fort Worth, TX, 76102", "Tarrant"),
                             ("Fort Worth, TX, 76102-1234", "Tarrant"), ("Tyler, Texas, District 42", "Smith"),
                             ("Tyler Texas, District 42", "Smith"), ("Tyler, TX, Smith County", "Smith"),
                             ("Texarkana, TX-AR", "Bowie"), ("Texarkana, Texas/Arkansas", "Bowie")):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["county"], r["state"]), ("neta65", county, "TX"), text)
            self.assertNotIn("District", r["label_en"], text)
        self.assertEqual(classify_location("Tyler, Texas, District 42")["label_en"], "Tyler, Texas")
        r = classify_location("Texas, District 42")
        self.assertEqual((r["scope"], r["label_en"]), ("texas", "Texas"))
        # a country word is only split off when a state stays in front of it
        for text, state in (("New Mexico", "NM"), ("Nuevo México", "NM"), ("Albuquerque New Mexico", "NM"),
                            ("Albuquerque, New Mexico USA", "NM"), ("Arlington (Virginia)", "VA"),
                            ("Texarkana, AR", "AR")):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["state"]), ("other", state), text)
        self.assertEqual(classify_location("New England")["label_en"], "New England")
        self.assertEqual(classify_location("Ciudad de México")["state"], "Ciudad de México")

    def test_words_around_the_place_neighborhoods_and_other_spellings(self):
        cases = {
            "near Tyler, Texas": ("Smith", "Tyler, Texas"), "outside Tyler, Texas": ("Smith", "Tyler, Texas"),
            "just outside of Tyler, TX": ("Smith", "Tyler, Texas"), "Tyler area, Texas": ("Smith", "Tyler, Texas"),
            "Tyler-area, Texas": ("Smith", "Tyler, Texas"), "living in Tyler, Texas": ("Smith", "Tyler, Texas"),
            "a small town near Tyler, Texas": ("Smith", "Tyler, Texas"),
            "cerca de Tyler, Tejas": ("Smith", "Tyler, Texas"), "un pequeño pueblo cerca de Tyler, Tejas":
            ("Smith", "Tyler, Texas"), "Tyler y alrededores, Texas": ("Smith", "Tyler, Texas"),
            "a las afueras de Dallas": ("Dallas", "Dallas, Texas"), "near Dallas": ("Dallas", "Dallas, Texas"),
            "Dallas metro area": ("Dallas", "Dallas, Texas"),
            "Oak Cliff, Texas": ("Dallas", "Oak Cliff, Texas"), "Deep Ellum, TX": ("Dallas", "Deep Ellum, Texas"),
            "Lake Highlands, Texas": ("Dallas", "Lake Highlands, Texas"),
            "Arlington Heights, Texas": ("Tarrant", "Arlington Heights, Texas"),
            "Oak Cliff (Dallas), Texas": ("Dallas", "Oak Cliff, Texas"),
            "N. Richland Hills, Texas": ("Tarrant", "N. Richland Hills, Texas"),
            "Hurst-Euless-Bedford, Texas": ("Tarrant", "Hurst-Euless-Bedford, Texas"),
            "HEB, Texas": ("Tarrant", "HEB, Texas"),
            "Sherman-Denison, Texas": ("Grayson", "Sherman-Denison, Texas"),
            "De Soto, Texas": ("Dallas", "De Soto, Texas"), "Mc Kinney, Texas": ("Collin", "Mc Kinney, Texas"),
            "North Dallas, Texas": ("Dallas", "North Dallas, Texas"),
            "downtown Fort Worth, TX": ("Tarrant", "downtown Fort Worth, Texas"),
            "norte de Dallas, Tejas": ("Dallas", "norte de Dallas, Texas"),
            "Town of Addison, Texas": ("Dallas", "Town of Addison, Texas"),
            "Palestina, Tejas": ("Anderson", "Palestine, Texas"),
        }
        for text, (county, label) in cases.items():
            r = classify_location(text)
            self.assertEqual((r["scope"], r["county"], r["label_en"]), ("neta65", county, label), text)
        self.assertEqual(classify_location("Palestina, Tejas")["label_es"], "Palestina, Texas")
        r = classify_location("Mid-Cities, Texas")
        self.assertEqual((r["scope"], r["label_en"]), ("neta65", "Mid-Cities, Texas"))
        self.assertEqual(classify_location("Nueva Braunfels, Texas")["county"], "Comal")    # texas, not Area 65
        # the labels the review found garbled
        for text, en, es in (("somewhere in East Texas", "East Texas", "Este de Texas"),
                             ("rural East Texas", "East Texas", "Este de Texas"),
                             ("a small town in Texas", "Texas", "Texas"), ("Somewhere in Texas", "Texas", "Texas"),
                             ("North Texas area", "North Texas", "Norte de Texas")):
            r = classify_location(text)
            self.assertEqual((r["label_en"], r["label_es"]), (en, es), text)
        # real places that start or end with such a word are not cut; places elsewhere keep their label
        for text, want in (("Justin, Texas", ("neta65", "Justin, Texas")),
                           ("The Colony, Texas", ("neta65", "The Colony, Texas")),
                           ("Lake Dallas, Texas", ("neta65", "Lake Dallas, Texas")),
                           ("North Richland Hills, TX", ("neta65", "North Richland Hills, Texas")),
                           ("South Houston, Texas", ("texas", "South Houston, Texas")),
                           ("Kingwood, Texas", ("texas", "Kingwood, Texas")),
                           ("near Oslo, Norway", ("other", "near Oslo, Norway")),
                           ("San Francisco Bay Area, California", ("other", "San Francisco Bay Area, California")),
                           ("City of Industry, California", ("other", "City of Industry, California")),
                           ("Rancho Cucamonga, California", ("other", "Rancho Cucamonga, California"))):
            r = classify_location(text)
            self.assertEqual((r["scope"], r["label_en"]), want, text)
        self.assertEqual(scope("near Paris"), "other")                     # a bare name stays ambiguous
        self.assertEqual(scope("W."), "other")                             # a lone letter is not West

    def test_mexican_state_abbreviations(self):
        for text, state in (("Guadalajara, Jal.", "Jalisco"), ("Guadalajara Jal.", "Jalisco"),
                            ("Ciudad Juárez, Chih.", "Chihuahua"), ("Saltillo, Coah.", "Coahuila"),
                            ("Reynosa, Tamps.", "Tamaulipas"), ("La Paz, B.C.S.", "Baja California Sur"),
                            ("Toluca, Edo. de México", "Estado de México"), ("Toluca, Edomex", "Estado de México"),
                            ("Querétaro, Qro.", "Querétaro"), ("Hermosillo, Son.", "Sonora"),
                            ("Monterrey, N.L.", "Nuevo León"), ("Monterrey, NL", "Nuevo León"),
                            ("Monterrey NL", "Nuevo León"), ("Tijuana, B.C.", "Baja California"),
                            ("Mexicali, B.C.", "Baja California"), ("Morelia, Mich.", "Michoacán"),
                            ("Colima, Col.", "Colima"), ("Monterrey Nuevo León México", "Nuevo León")):
            for lang in (None, "en", "es"):
                r = classify_location(text, lang)
                self.assertEqual((r["scope"], r["state"], r["country"]), ("other", state, "MX"), (text, lang))
        self.assertEqual(classify_location("Monterrey, N.L.")["label_es"], "Monterrey, Nuevo León")
        # the same abbreviations for the US / Canada: the city, then the country, then the language decide
        for text, lang, state, label in (("Victoria, B.C.", None, "BC", "Victoria, British Columbia"),
                                         ("Vancouver, B.C.", "es", "BC", "Vancouver, British Columbia"),
                                         ("Detroit, Mich.", "es", "MI", "Detroit, Michigan"),
                                         ("Denver, Col.", "es", "CO", "Denver, Colorado"),
                                         ("Agassiz, B.C.", "en", "BC", "Agassiz, British Columbia"),
                                         ("Somewhere, B.C., Canada", "es", "BC", "Somewhere, British Columbia"),
                                         ("St. John's, NL", "es", "NL", "St. John's, Newfoundland and Labrador")):
            r = classify_location(text, lang)
            self.assertEqual((r["state"], r["label_en"]), (state, label), (text, lang))
        # an unknown city: a Spanish (La Viña) byline means the Mexican state, an English one does not
        self.assertEqual(classify_location("Rosita, B.C.", "es")["state"], "Baja California")
        self.assertEqual(classify_location("Rosita, B.C.", "en")["state"], "BC")
        self.assertEqual(classify_location("Rosita, B.C., México", "en")["state"], "Baja California")
        # abbreviations that are also words only count as their own part
        for text in ("Boot Camp", "Son", "Hermosillo Son", "Col.", "Jal."):
            self.assertIsNone(classify_location(text)["state"], text)

    def test_missing_gazetteer_degrades_to_texas(self):
        with mock.patch.object(geo, "GAZETTEER_PATH", ROOT / "does-not-exist.json"):
            geo.reset_caches()
            try:
                r = classify_location("Tyler, Texas")
                self.assertEqual((r["scope"], r["city"]), ("texas", "Tyler"))
                self.assertEqual(scope("Maine"), "other")
            finally:
                geo.reset_caches()
        self.assertEqual(scope("Tyler, Texas"), "neta65")


# =========================================================================== archive parsing
def _row(pub: str, key: str, slug: str, title: str, byline: str = "", subtitle: str = "") -> str:
    y, m = key.split("-")
    if pub == "gv":
        mon = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"][int(m) - 1]
        url = f"https://www.aagrapevine.org/magazine/{y}/{mon}/{slug}"
        label = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                 "October", "November", "December"][int(m) - 1] + f" {y}"
    else:
        es = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
              "octubre", "noviembre", "diciembre"]
        a, b = es[int(m) - 1], es[int(m) % 12]
        url = f"https://www.aalavina.org/revista/{a}-{b}-{y}/{slug}"
        label = f"{a.capitalize()} / {b.capitalize()} {y}"
    by = f'<div class="article-author">{"Por" if pub == "lv" else "By"}: \n  {byline}</div>' if byline else ""
    sub = (f'<div class="article-subtitle"><div class="field field--name-field-subtitle">{subtitle}<br></div>'
           f'</div>') if subtitle else '<div class="article-subtitle"></div>'
    return (f'<div class="views-row"><div class="layout--article view-mode-search_result '
            f'node--type-article"><div class="main-region"><h3 class="audible"><a class="article-node" '
            f'href="{url}">{title}</a></h3><div class="article-publication-date">{label}\n   |  Topic</div>'
            f'{by}{sub}<div class="node_view"></div></div></div></div>')


def _page(rows: list[str], next_page: int | None) -> str:
    pager = (f'<nav class="pager"><ul class="pager__items"><li class="pager__item pager__item--next">'
             f'<a href="?page={next_page}" rel="next">Next</a></li></ul></nav>') if next_page else ""
    return f'<html><body><main><div class="view-content">{"".join(rows)}</div>{pager}</main></body></html>'


class ArchiveParsing(unittest.TestCase):
    def test_rows_bylines_departments_and_pager(self):
        from scripts.sync import articles as AR
        html = _page([
            _row("gv", "2026-08", "letter-editor-august-2026", "Letter from the Editor"),
            _row("gv", "2026-08", "my-story", "My Story", "Jake B. \n | Tyler, Texas", "A subtitle"),
            _row("gv", "2026-08", "humor-in-sobriety", "Humor in Sobriety", "Ann"),
            _row("gv", "2026-08", "humor", "Humor"),
        ], 3)
        rows, has_next = AR.parse_archive(html, "https://www.aagrapevine.org/archive?page=2", "gv")
        self.assertTrue(has_next)
        self.assertEqual([r["slug"] for r in rows], ["letter-editor-august-2026", "my-story", "humor-in-sobriety", "humor"])
        self.assertEqual([r["department"] for r in rows], [True, False, False, True])
        r = rows[1]
        self.assertEqual((r["issue_key"], r["issue_label"], r["topic"]), ("2026-08", "August 2026", "Topic"))
        self.assertEqual((r["author"], r["author_location"], r["subtitle"]), ("Jake B.", "Tyler, Texas", "A subtitle"))
        self.assertEqual(r["url"], "https://www.aagrapevine.org/magazine/2026/aug/my-story")
        rows, has_next = AR.parse_archive(_page([_row("lv", "2026-07", "detenido", "Detenido", "Gus | Dallas, Tejas")], None),
                                          "https://www.aalavina.org/archivo", "lv")
        self.assertFalse(has_next)
        self.assertEqual((rows[0]["issue_key"], rows[0]["issue_label"]), ("2026-07", "Julio / Agosto 2026"))
        self.assertEqual(rows[0]["author_location"], "Dallas, Tejas")
        # links to the other magazine are not ours
        self.assertEqual(AR.parse_archive(html, "https://www.aalavina.org/archivo", "lv")[0], [])

    def test_lower_case_issue_label_is_tidied(self):
        # the live archive printed "june 2026" for most of the June 2026 stories
        from scripts.sync import articles as AR
        self.assertEqual(AR.tidy_label("june 2026"), "June 2026")
        self.assertEqual(AR.tidy_label("septiembre / octubre 2026"), "Septiembre / Octubre 2026")
        self.assertEqual(AR.tidy_label("June 2026"), "June 2026")
        self.assertEqual(AR.tidy_label("Julio / Agosto 2026"), "Julio / Agosto 2026")
        self.assertIsNone(AR.tidy_label(None))
        gv = _row("gv", "2026-06", "my-story", "My Story", "Ann | Tyler, Texas").replace("June 2026", "june 2026")
        lv = _row("lv", "2026-09", "detenido", "Detenido", "Gus | Dallas, Tejas").replace(
            "Septiembre / Octubre 2026", "septiembre / octubre 2026")
        rows, _ = AR.parse_archive(_page([gv], None), "https://www.aagrapevine.org/archive", "gv")
        self.assertEqual((rows[0]["issue_key"], rows[0]["issue_label"]), ("2026-06", "June 2026"))
        rows, _ = AR.parse_archive(_page([lv], None), "https://www.aalavina.org/archivo", "lv")
        self.assertEqual((rows[0]["issue_key"], rows[0]["issue_label"]), ("2026-09", "Septiembre / Octubre 2026"))

    @unittest.skipUnless((PROBE / "www.aagrapevine.org_archive.html").exists(), "saved archive pages not present")
    def test_saved_live_pages(self):
        from scripts.sync import articles as AR
        gv = (PROBE / "www.aagrapevine.org_archive.html").read_text(encoding="utf-8")
        rows, has_next = AR.parse_archive(gv, "https://www.aagrapevine.org/archive", "gv")
        self.assertTrue(has_next)
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(r["issue_key"] == "2026-10" and r["issue_label"] == "October 2026" for r in rows))
        sense = next(r for r in rows if r["slug"] == "sense-belonging")
        self.assertEqual((sense["author"], sense["author_location"]), ("Jake B.", "Cheyenne, Wyoming"))
        self.assertEqual({r["slug"] for r in rows if r["department"]},
                         {"letter-editor-october-2026", "dear-grapevine-october-2026", "aa-news-october-2026",
                          "discussion-topic"})
        lv = (PROBE / "www.aalavina.org_archivo.html").read_text(encoding="utf-8")
        rows, has_next = AR.parse_archive(lv, "https://www.aalavina.org/archivo", "lv")
        self.assertTrue(has_next)
        self.assertEqual(len(rows), 10)
        victor = next(r for r in rows if r["slug"] == "el-despertar-del-espiritu")
        self.assertEqual((victor["issue_key"], victor["author"], victor["author_location"]),
                         ("2026-09", "Victor R.", "Grand Prairie, Texas"))
        self.assertEqual(victor["title"], "El despertar del espíritu")
        self.assertEqual(scope(victor["author_location"]), "neta65")


# =========================================================================== archive walk (backfill / daily)
def _months_back(n: int) -> str:
    d = date.today().replace(day=1)
    y, m = d.year, d.month - n
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"


class ArchiveWalk(unittest.TestCase):
    """A fake GV archive: issues from next month back 7 months, 12 stories each, 10 per page."""

    def setUp(self):
        from scripts.sync import articles as AR
        self.AR = AR
        self.keys = [_months_back(n) for n in range(-1, 8)]          # newest first
        self.rows = [(k, f"story-{k}-{i}") for k in self.keys for i in range(12)]
        self.calls: list[str] = []

    def http(self, rows=None):
        rows = rows if rows is not None else self.rows
        test = self

        class Http:
            requests_made = 0

            def get_text(self, url, **kw):
                test.calls.append(url)
                page = int(url.split("page=")[1]) if "page=" in url else 0
                chunk = rows[page * 10:(page + 1) * 10]
                nxt = page + 1 if (page + 1) * 10 < len(rows) else None
                return _page([_row("gv", k, s, s.replace("-", " "), "Ann | Tyler, Texas") for k, s in chunk], nxt)

            def get(self, url, **kw):
                raise AssertionError("no article pages in this test")
        return Http()

    def walk(self, recs, state, pages=20, days=120, seconds=480, rows=None):
        url_to_id = {r["url"]: i for i, r in recs.items()}
        touched, listed, errors = set(), set(), []
        args = Namespace(backfill_days=days, archive_pages=pages, max_seconds=seconds)
        st = self.AR.walk_archive(self.http(rows), "gv", recs, url_to_id, touched, listed, state, args,
                                  time.monotonic(), errors)
        return st, touched, errors

    def test_initial_backfill_stops_at_the_window_then_daily_costs_one_page(self):
        recs: dict = {}
        st, touched, errors = self.walk(recs, {})
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=120)).isoformat()
        want = {s for k, s in self.rows if f"{k}-01" >= cutoff}
        got = {r["slug"] for r in recs.values()}
        self.assertEqual(got, want, "every story inside the window, nothing older")
        self.assertEqual(errors, [])
        self.assertTrue(st["backfilled"])
        self.assertEqual(st["backfill_days"], 120)
        self.assertNotIn("resume_page", st)
        last_needed = max(i for i, (k, s) in enumerate(self.rows) if f"{k}-01" >= cutoff)
        self.assertEqual(len(self.calls), last_needed // 10 + 1 + (1 if (last_needed + 1) % 10 == 0 else 0))
        r = next(iter(recs.values()))
        self.assertEqual((r["author"], r["author_location"], r["publication"]), ("Ann", "Tyler, Texas", "gv"))
        self.assertTrue(r["issue_key"] and r["issue_label"] and r["topic"] == "Topic")
        # next day: nothing new → one request
        self.calls.clear()
        st2, touched2, _ = self.walk(recs, st)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(touched2, set())
        self.assertEqual(st2["new_last_run"], 0)
        self.assertEqual(st2["backfilled"], st["backfilled"])

    def test_daily_run_reads_until_a_page_is_all_known(self):
        recs: dict = {}
        st, _, _ = self.walk(recs, {})
        newer = [(_months_back(-2), f"fresh-{i}") for i in range(15)]      # a new issue: 15 stories
        self.calls.clear()
        st2, touched, _ = self.walk(recs, st, rows=newer + self.rows)
        self.assertEqual(st2["new_last_run"], 15)
        self.assertEqual(len(self.calls), 3, "page 0 + 1 have new stories, page 2 is all known → stop")
        self.assertEqual({recs[i]["slug"] for i in touched}, {s for _, s in newer})

    def test_page_cap_resumes_next_run(self):
        recs: dict = {}
        st, _, _ = self.walk(recs, {}, pages=2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(st["resume_page"], 2)
        self.assertNotIn("backfilled", st)
        self.calls.clear()
        st2, _, _ = self.walk(recs, st)
        self.assertTrue(self.calls[0].endswith("?page=2"))
        self.assertTrue(st2["backfilled"])
        self.assertNotIn("resume_page", st2)

    def test_deeper_backfill_request_walks_again(self):
        recs: dict = {}
        st, _, _ = self.walk(recs, {}, days=40)
        n40 = len(recs)
        self.calls.clear()
        st2, _, _ = self.walk(recs, st, days=200)
        self.assertGreater(len(recs), n40)
        self.assertEqual(st2["backfill_days"], 200)

    def test_known_record_only_gets_gaps_filled(self):
        k, s = self.rows[0]
        mon = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"][int(k[5:]) - 1]
        url = f"https://www.aagrapevine.org/magazine/{k[:4]}/{mon}/{s}"
        iid = f"gv:{k}:{s}"
        recs = {iid: {"id": iid, "url": url, "publication": "gv", "issue_key": k, "title": "Hub Title",
                      "author": "Jake B.", "section": "Featured Section", "free": True}}
        self.walk(recs, {"backfilled": "2026-01-01T00:00:00Z", "backfill_days": 120})
        r = recs[iid]
        self.assertEqual((r["title"], r["author"], r["author_location"]), ("Hub Title", "Jake B.", "Tyler, Texas"))

    def test_layout_change_is_reported(self):
        class Empty:
            requests_made = 0

            def get_text(self, url, **kw):
                return "<html><body>Nothing here</body></html>"
        args = Namespace(backfill_days=120, archive_pages=20, max_seconds=480)
        errors: list = []
        st = self.AR.walk_archive(Empty(), "gv", {}, {}, set(), set(), {}, args, time.monotonic(), errors)
        self.assertTrue(errors)
        self.assertIn("error", st)
        self.assertNotIn("backfilled", st)

    def test_broken_pager_costs_two_requests_not_the_page_cap(self):
        # a site that ignores ?page= (always the same 10 stories, always a "Next" link) — review 2026-09
        stuck = [(_months_back(0), f"s-{i}") for i in range(10)]

        class Stuck:
            requests_made = 0

            def get_text(inner, url, **kw):
                self.calls.append(url)
                return _page([_row("gv", k, s, s, "Ann | Tyler, Texas") for k, s in stuck], 99)
        args = Namespace(backfill_days=120, archive_pages=20, max_seconds=480)
        recs: dict = {}
        state: dict = {}
        for day in range(3):
            self.calls.clear()
            errors: list = []
            state = self.AR.walk_archive(Stuck(), "gv", recs, {r["url"]: i for i, r in recs.items()}, set(), set(),
                                         state, args, time.monotonic(), errors)
            self.assertEqual(len(self.calls), 2, f"day {day}")
            self.assertIn("repeats", state["error"])
            self.assertTrue(errors and "page links" in errors[0])
            self.assertEqual(state["resume_page"], day + 1)
            self.assertNotIn("backfilled", state)
        self.assertEqual(len(recs), 10)

    def test_backfill_resuming_too_deep_is_given_up(self):
        self.assertEqual(self.AR.backfill_page_limit(Namespace(backfill_days=120, archive_pages=20)), 40)
        self.assertEqual(self.AR.backfill_page_limit(Namespace(backfill_days=365, archive_pages=20)), 73)
        self.assertEqual(self.AR.backfill_page_limit(Namespace(backfill_days=30, archive_pages=5)), 10)
        recs: dict = {}
        self.walk(recs, {})                                   # the stories are known already
        self.calls.clear()
        st, _, errors = self.walk(recs, {"resume_page": 40})  # an unfinished backfill 40 pages deep
        self.assertEqual(self.calls, [])
        self.assertTrue(st["backfilled"])
        self.assertEqual((st["backfill_days"], st["backfill_stopped_at"]), (120, 40))
        self.assertNotIn("resume_page", st)
        self.assertIn("given up", st["error"])
        self.assertTrue(errors)
        # from then on a daily run: one page, no error
        st2, _, errors2 = self.walk(recs, st)
        self.assertEqual((len(self.calls), errors2), (1, []))
        self.assertNotIn("error", st2)

    def test_last_page_repeating_a_pushed_down_story_is_the_end_not_an_error(self):
        # a story published between two requests pushes the previous page's last story onto the last page
        pages = [self.rows[0:10], [self.rows[9]]]      # the last page = only the pushed-down story

        class Shifted:
            requests_made = 0

            def get_text(inner, url, **kw):
                self.calls.append(url)
                n = int(url.split("page=")[1]) if "page=" in url else 0
                return _page([_row("gv", k, s, s, "Ann | Tyler, Texas") for k, s in pages[n]], 1 if n == 0 else None)
        errors: list = []
        st = self.AR.walk_archive(Shifted(), "gv", {}, {}, set(), set(), {}, Namespace(
            backfill_days=400, archive_pages=20, max_seconds=480), time.monotonic(), errors)
        self.assertEqual((len(self.calls), errors), (2, []))
        self.assertNotIn("error", st)
        self.assertTrue(st["backfilled"])

    def test_fetch_failure_is_not_an_error(self):
        class Down:
            requests_made = 0

            def get_text(self, url, **kw):
                return None
        args = Namespace(backfill_days=120, archive_pages=20, max_seconds=480)
        errors: list = []
        st = self.AR.walk_archive(Down(), "gv", {}, {}, set(), set(), {}, args, time.monotonic(), errors)
        self.assertEqual(errors, [])
        self.assertEqual(st["resume_page"], 0)
        self.assertIn("no answer", st["error"])


class BylineNeeds(unittest.TestCase):
    def test_rules(self):
        from scripts.sync import articles as AR
        today = datetime.now(timezone.utc).date()
        window = (today - timedelta(days=120)).isoformat()
        key = today.strftime("%Y-%m")
        old = (today - timedelta(days=200)).strftime("%Y-%m")
        story = {"issue_key": key, "author": "Ann", "author_location": None, "department": False}
        self.assertTrue(AR._needs_byline(None, story, window))
        self.assertFalse(AR._needs_byline(None, {**story, "author_location": "Tyler, Texas"}, window))
        self.assertFalse(AR._needs_byline(None, {**story, "issue_key": old}, window))
        self.assertFalse(AR._needs_byline(None, {**story, "department": True}, window))
        self.assertFalse(AR._needs_byline(None, {**story, "section": "En cada edición"}, window))
        self.assertFalse(AR._needs_byline({"ok": True, "byline_at": "x"}, story, window))
        self.assertFalse(AR._needs_byline({"ok": False, "tries": 3, "at": "2000-01-01T00:00:00Z"}, story, window))
        self.assertTrue(AR._needs_byline({"ok": False, "tries": 1, "at": "2000-01-01T00:00:00Z"}, story, window))
        self.assertFalse(AR._needs_byline({"ok": False, "tries": 1, "at": common.now_iso()}, story, window))

    def test_online_exclusive_without_a_section_is_complete(self):
        # Grapevine "Online Exclusive" pages print no section: 16 of 16 read so far — review 2026-09
        from scripts.sync import articles as AR
        now = datetime.now(timezone.utc)
        long_ago = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        exclusive = {"title": "T", "free": False, "online_exclusive": True, "section": None}
        self.assertTrue(AR._is_complete(exclusive))
        self.assertFalse(AR._needs_detail({"ok": True, "tries": 1, "at": long_ago}, exclusive, now))
        self.assertFalse(AR._needs_detail(None, exclusive, now))
        self.assertFalse(AR._is_complete({**exclusive, "free": None}))       # listed, page never read
        self.assertTrue(AR._needs_detail(None, {**exclusive, "free": None}, now))
        regular = {"title": "T", "free": True, "online_exclusive": False, "section": None}
        self.assertFalse(AR._is_complete(regular))                           # still retried (≤ 3×)
        self.assertTrue(AR._needs_detail({"ok": True, "tries": 1, "at": long_ago}, regular, now))
        self.assertTrue(AR._is_complete({**regular, "section": "Our Stories"}))

    def test_back_catalog_thumbnails_only_for_texas_writers(self):
        from scripts.sync import articles as AR
        issues = {"gv:2026-10": {}}
        self.assertTrue(AR._wants_thumb({"publication": "gv", "issue_key": "2026-10"}, issues))
        self.assertFalse(AR._wants_thumb({"publication": "gv", "issue_key": "2026-07",
                                          "author_location": "Maine"}, issues))
        self.assertTrue(AR._wants_thumb({"publication": "gv", "issue_key": "2026-07",
                                         "author_location": "Houston, Texas"}, issues))


class ArticlesModule(unittest.TestCase):
    """The whole module offline: hub + archive + a byline detail fetch, twice (backfill, then daily)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-spot-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        from scripts.sync import articles as AR
        self.AR = AR
        for p in (mock.patch.object(common, "RAW_DIR", self.tmp), mock.patch.object(AR, "THUMB_DIR", self.tmp / "t")):
            p.start()
            self.addCleanup(p.stop)

    def run_module(self, archive_rows, max_details=0, date_line=None):
        cur = _months_back(0)
        hub = ('<html><body><div class="main-region"><div class="large-eyebrow">{label}</div><h1>Theme</h1></div>'
               '<div class="node--type-article view-mode-teaser"><h3><a href="{url}">Hub Story</a></h3>'
               '<div class="author">By: Jake B. | Tyler, Texas</div></div></body></html>')
        first = _row("gv", cur, "hub-story", "x")
        url = first.split('href="')[1].split('"')[0]
        label = first.split('article-publication-date">')[1].split("\n")[0]
        calls: list[str] = []
        line = (date_line or "{label} | Topic | Our Stories").format(label=label)

        class Resp:
            status_code, encoding, headers = 200, "utf-8", {}
            text = ('<html><body><article class="node--type-article"><h1>Page Title</h1>'
                    f'<div class="article-publication-date">{line}</div>'
                    '<div class="author">By: Ann</div>' + "<p>" + "word " * 30 + "</p>" * 5 + "</article></body></html>")

        class Http:
            requests_made = 0

            def get_text(self, u, **kw):
                calls.append(u)
                if u.endswith("/magazine"):
                    return hub.format(label=label, url=url)
                if "/archive" in u:
                    page = int(u.split("page=")[1]) if "page=" in u else 0
                    chunk = archive_rows[page * 10:(page + 1) * 10]
                    return _page(chunk, page + 1 if (page + 1) * 10 < len(archive_rows) else None)
                return None

            def get(self, u, **kw):
                calls.append(u)
                return Resp()

        with mock.patch.object(self.AR, "shared_session", lambda: Http()):
            self.AR.main(["--only", "gv", "--max-details", str(max_details)])
        return calls, json.loads((self.tmp / "articles.json").read_text(encoding="utf-8"))

    def test_texas_writers_article_pages_come_first(self):
        # Two detail fetches allowed: the hub story (newest) and the OLDER Texas writer's story — not the
        # newer Maine story — so the spotlight's cards get their section and thumbnail first.
        cur, prev1, prev2 = _months_back(0), _months_back(1), _months_back(2)
        rows = [_row("gv", cur, "hub-story", "Hub Story", "Jake B. | Tyler, Texas"),
                _row("gv", prev1, "maine-story", "Maine Story", "Ann | Portland, Maine"),
                _row("gv", prev2, "texas-story", "Texas Story", "Bo | Paris, Texas")]
        calls, env = self.run_module(rows, max_details=2)
        pages = [c for c in calls if not c.endswith("/magazine") and "/archive" not in c]
        self.assertEqual([p.rsplit("/", 1)[-1] for p in pages], ["hub-story", "texas-story"])
        self.assertEqual(env["stats"]["details_left"], 1)

    def test_lower_case_labels_saved_earlier_are_repaired(self):
        cur, prev1 = _months_back(0), _months_back(1)
        rows = [_row("gv", cur, "hub-story", "Hub Story", "Jake B. | Tyler, Texas"),
                _row("gv", prev1, "older-story", "Older Story", "Ann | Paris, Texas")]
        self.run_module(rows)
        path = self.tmp / "articles.json"
        env = json.loads(path.read_text(encoding="utf-8"))
        older = next(i for i in env["items"] if i["id"].endswith("older-story"))
        good = older["extra"]["issue_label"]
        older["extra"]["issue_label"] = good.lower()          # as an earlier run saved "june 2026"
        path.write_text(json.dumps(env), encoding="utf-8")
        _calls, env2 = self.run_module(rows)                   # a daily run: the story is not re-listed
        older2 = next(i for i in env2["items"] if i["id"].endswith("older-story"))
        self.assertEqual(older2["extra"]["issue_label"], good)
        self.assertEqual(older2["first_seen"], older["first_seen"])
        self.assertEqual(older2["date"], older["date"])

    def test_online_exclusive_page_is_read_once(self):
        cur = _months_back(0)
        rows = [_row("gv", cur, "hub-story", "Hub Story", "Jake B. | Tyler, Texas")]
        calls, env = self.run_module(rows, max_details=5, date_line="{label} | Online Exclusive | Topic")
        self.assertEqual([c for c in calls if c.endswith("/hub-story")], [calls[-1]])
        item = env["items"][0]
        self.assertTrue(item["extra"]["online_exclusive"])
        self.assertIsNone(item["extra"]["section"])
        self.assertNotIn(item["id"], env["detail_state"], "complete → no retry bookkeeping")
        # as the live data had it before the fix: read once, ok, 10 days ago → still not asked again
        env["detail_state"][item["id"]] = {"ok": True, "tries": 1, "at": "2000-01-01T00:00:00Z"}
        (self.tmp / "articles.json").write_text(json.dumps(env), encoding="utf-8")
        calls2, env2 = self.run_module(rows, max_details=5, date_line="{label} | Online Exclusive | Topic")
        self.assertEqual([c for c in calls2 if c.endswith("/hub-story")], [])
        self.assertEqual(env2["stats"]["details_pending"], 0)

    def test_backfill_then_daily(self):
        cur, prev1 = _months_back(0), _months_back(1)
        rows = [_row("gv", cur, "hub-story", "Hub Story", "Jake B. | Tyler, Texas"),
                _row("gv", prev1, "older-story", "Older Story", "Ann | Paris, Texas"),
                _row("gv", prev1, "no-place", "No Place", "Bo")]
        calls, env = self.run_module(rows)
        ids = {i["id"] for i in env["items"]}
        self.assertEqual(ids, {f"gv:{cur}:hub-story", f"gv:{prev1}:older-story", f"gv:{prev1}:no-place"})
        older = next(i for i in env["items"] if i["id"].endswith("older-story"))
        self.assertEqual(older["date"], f"{prev1}-01")
        self.assertEqual(older["extra"]["author_location"], "Paris, Texas")
        self.assertEqual(older["extra"]["issue_label"][-4:], prev1[:4])
        self.assertTrue(env["archive_state"]["gv"]["backfilled"])
        self.assertEqual(env["stats"]["archive_new"], 2)
        # "No Place" lacks a place → its page is read once even with --max-details 0 …
        self.assertEqual([c for c in calls if c.endswith("/no-place")], [calls[-1]])
        st = env["detail_state"][f"gv:{prev1}:no-place"]
        self.assertTrue(st["ok"] and st["byline_at"])
        # … and never again; the daily run costs hub + ONE archive page
        calls2, env2 = self.run_module(rows)
        self.assertEqual(len(calls2), 2, calls2)
        self.assertEqual(len(env2["items"]), 3)


# =========================================================================== build_data: pub_date + spotlight
class Spotlight(unittest.TestCase):
    def ctx(self, today: str = "2026-09-23", hub=("gv:2026-10", "lv:2026-09")):
        from scripts.sync import build_data as B
        c = B.Ctx(offline=True)
        c.today_local = date.fromisoformat(today)
        c.now = datetime.fromisoformat(today + "T17:00:00+00:00")
        c.now_ts = c.now.timestamp()
        c.hub_issues = set(hub)
        return c

    @staticmethod
    def art(iid, key, loc="Tyler, Texas", author="Ann", first_seen="2026-09-23T12:00:00Z", pub=None, **ex):
        pub = pub or iid.split(":")[0]
        return {"id": iid, "kind": "article", "source": "grapevine" if pub == "gv" else "lavina",
                "title": ex.pop("title", iid), "date": None, "first_seen": first_seen, "lang": "en",
                "category": pub, "status": "ok",
                "extra": {"publication": pub, "issue_key": key, "author": author, "author_location": loc, **ex}}

    def test_pub_date_rules(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        self.assertEqual(B.article_pub_date(c, self.art("gv:a", "2026-08")), "2026-08-01")
        self.assertEqual(B.article_pub_date(c, self.art("lv:a", "2026-09")), "2026-09-01")   # bimonthly: 1st month
        # October issue online in September: the day we first saw it (in Chicago time)
        self.assertEqual(B.article_pub_date(c, self.art("gv:a", "2026-10", first_seen="2026-09-16T03:00:00Z")),
                         "2026-09-15")
        self.assertEqual(B.article_pub_date(c, self.art("gv:a", "2026-10", first_seen=None)), "2026-09-23")
        self.assertEqual(B.article_pub_date(c, self.art("gv:a", None, issue_date="2026-07-01")), "2026-07-01")
        # the day stays put once October has begun (review 2026-09: it used to jump to October 1, and
        # the weekly digest then listed the same stories a second time)
        self.assertEqual(B.article_pub_date(self.ctx("2026-10-02"), self.art("gv:a", "2026-10")), "2026-09-23")
        # an issue first seen after its first day counts from its first day; a back-catalog story found
        # by the archive backfill (first_seen = the backfill day) too
        self.assertEqual(B.article_pub_date(c, self.art("lv:a", "2026-09", first_seen="2026-09-05T15:00:00Z")),
                         "2026-09-01")
        self.assertEqual(B.article_pub_date(c, self.art("gv:a", "2026-07", first_seen="2026-09-23T23:07:00Z")),
                         "2026-07-01")
        # a first_seen in the future (clock skew) never makes the day later than today
        self.assertEqual(B.article_pub_date(c, self.art("gv:a", "2026-11", first_seen="2026-12-01T12:00:00Z")),
                         "2026-09-23")

    def test_pub_date_does_not_move_when_the_cover_month_starts(self):
        # the live record gv:2026-10:halloween-remember (first seen 2026-09-23T16:34Z) on four build days
        from scripts.sync import build_data as B
        it = self.art("gv:2026-10:halloween-remember", "2026-10", first_seen="2026-09-23T16:34:47Z")
        days = {d: B.article_pub_date(self.ctx(d), it)
                for d in ("2026-09-23", "2026-09-30", "2026-10-01", "2026-10-05")}
        self.assertEqual(set(days.values()), {"2026-09-23"}, days)

    def test_byline_language_follows_the_magazine(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        lv = self.art("lv:2026-09:mty", "2026-09", "Monterrey, N.L.")
        lv["lang"] = "en"                                  # a title detected as English does not matter
        gv = self.art("gv:2026-10:vic", "2026-10", "Agassiz, B.C.")
        B.enrich_articles(c, [lv, gv])
        self.assertEqual(B.byline_lang(lv), "es")
        self.assertEqual(B.byline_lang(gv), "en")
        self.assertEqual(lv["extra"]["geo"]["label_es"], "Monterrey, Nuevo León")
        self.assertEqual(gv["extra"]["geo"]["label_en"], "Agassiz, British Columbia")
        self.assertEqual(B.byline_lang({"lang": "es", "extra": {}}), "es")

    def sample(self):
        A = self.art
        return [
            A("gv:2026-10:tyler", "2026-10", "Tyler, Texas", title="Zeta"),              # neta65, 09-23
            A("lv:2026-09:gp", "2026-09", "Grand Prairie, Texas", title="Beta"),          # neta65, 09-01
            A("gv:2026-08:paris", "2026-08", "Paris, Texas", title="Alpha"),              # neta65, 08-01
            A("gv:2026-07:dallas", "2026-07", "Dallas, TX", title="Old"),                 # neta65, 07-01 (90 only)
            A("gv:2026-10:houston", "2026-10", "Houston, Texas", title="Hou"),            # texas
            A("lv:2026-09:sa", "2026-09", "San Antonio, Texas", title="Sa"),              # texas
            A("gv:2026-10:maine", "2026-10", "Maine", title="Me"),                        # other
            A("gv:2026-10:anon", "2026-10", None, author="Anonymous", title="Anon"),      # unknown
            A("gv:2026-10:dept", "2026-10", None, author=None, title="Dear Grapevine", department=True),
            A("lv:2026-09:cartas", "2026-09", None, author=None, title="Cartas", section="En cada edición"),
            A("gv:2026-05:ancient", "2026-05", "Tyler, Texas", title="Ancient"),          # outside 90 days
        ]

    def test_counts_sorting_and_window(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        items = self.sample()
        B.enrich_articles(c, items)
        got, counts = B.plan_spotlight(c, items)
        self.assertEqual(counts, {"60": {"neta65": 3, "texas": 5, "all": 7},
                                  "90": {"neta65": 4, "texas": 6, "all": 8}})
        self.assertEqual([i["id"] for i in got], [
            "gv:2026-10:tyler", "lv:2026-09:gp", "gv:2026-08:paris", "gv:2026-07:dallas",
            "gv:2026-10:houston", "lv:2026-09:sa", "gv:2026-10:maine", "gv:2026-10:anon"])
        tyler = got[0]["extra"]
        self.assertEqual((tyler["pub_date"], tyler["geo"]["scope"], tyler["geo"]["county"]),
                         ("2026-09-23", "neta65", "Smith"))

    def test_same_scope_and_date_sorted_by_title(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        items = [self.art("gv:2026-09:b", "2026-09", title="Bravo"), self.art("gv:2026-09:a", "2026-09", title="Álamo"),
                 self.art("gv:2026-09:c", "2026-09", title="charlie")]
        B.enrich_articles(c, items)
        got, _ = B.plan_spotlight(c, items)
        self.assertEqual([i["title"] for i in got], ["Álamo", "Bravo", "charlie"])

    def test_no_area65_writers_is_fine(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        items = [self.art("gv:2026-10:me", "2026-10", "Maine")]
        B.enrich_articles(c, items)
        got, counts = B.plan_spotlight(c, items)
        self.assertEqual(counts["60"], {"neta65": 0, "texas": 0, "all": 1})
        doc = B.build_spotlight(c, got, counts, "2026-09-23T17:00:00Z")
        self.assertEqual(list(doc), ["updated", "fixture", "today", "home_days", "list_days", "default_scope",
                                     "counts", "items"])
        self.assertEqual((doc["today"], doc["home_days"], doc["list_days"], doc["default_scope"]),
                         ("2026-09-23", 60, [60, 90], "neta65"))
        empty = B.build_spotlight(c, [], {"60": {"neta65": 0, "texas": 0, "all": 0}}, "x")
        self.assertEqual(empty["items"], [])

    def test_settings_are_sanity_checked(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        c.cfg = {"spotlight": {"home_days": "abc", "list_days": [90, "30", 90, -5, 9999], "default_scope": "Mars"}}
        self.assertEqual(B.spotlight_settings(c), (60, [90, 30], "neta65"))
        c.cfg = {}
        self.assertEqual(B.spotlight_settings(c), (60, [60, 90], "neta65"))
        c.cfg = {"spotlight": {"home_days": 30, "list_days": [60, 90], "default_scope": "all"}}
        items = self.sample()
        B.enrich_articles(c, items)
        _, counts = B.plan_spotlight(c, items)
        self.assertEqual(sorted(counts, key=int), ["30", "60", "90"])

    def test_location_label_i18n_is_written_by_rules(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        it = self.art("lv:2026-09:nj", "2026-09", "Nueva Jersey")
        B.enrich_articles(c, [it])
        self.assertEqual(B.local_fields(it)["author_location"], {"en": "New Jersey", "es": "Nueva Jersey"})
        none = self.art("gv:2026-10:x", "2026-10", None)
        B.enrich_articles(c, [none])
        self.assertNotIn("author_location", B.local_fields(none))

    def test_back_catalog_stays_out_of_whats_new(self):
        from scripts.sync import build_data as B
        c = self.ctx()
        cur = B.prep(self.art("gv:2026-10:cur", "2026-10"))
        cur["date"] = "2026-09-23"
        old = B.prep(self.art("gv:2026-09:old", "2026-09"))
        old["date"] = "2026-09-01"
        self.assertFalse(c.back_catalog(cur))
        self.assertTrue(c.back_catalog(old))
        self.assertFalse(c.is_new(old))
        plan = B.plan_whatsnew(c, {"articles": [cur, old]})
        self.assertEqual([it["id"] for _, it in plan], ["gv:2026-10:cur"])
        c.hub_issues = set()                                       # no issue map → old behaviour
        self.assertFalse(c.back_catalog(old))


if __name__ == "__main__":
    unittest.main()
