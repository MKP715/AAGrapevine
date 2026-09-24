"""Tests for the translation engine and the committee content parser.

    python -m unittest tests.test_translate -v        (or: python -m pytest tests)

Model-dependent tests are skipped automatically when the models are not installed
(run `python -m scripts.sync.translate --download` first to include them).
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync import translate as T  # noqa: E402
from scripts.sync.announcements import markdown_to_text, parse_announcement, parse_event  # noqa: E402

MODELS = T.model_ready("en_es") and T.model_ready("es_en")


class TextHelpers(unittest.TestCase):
    def test_sentence_split_abbreviations(self):
        s = T.split_sentences("Meet Dr. Smith at St. Paul at 7 p.m. tonight. Read A.A. Grapevine. See No. 5 now!")
        self.assertEqual([x for x, _ in s],
                         ["Meet Dr. Smith at St. Paul at 7 p.m. tonight.", "Read A.A. Grapevine.", "See No. 5 now!"])
        es = T.split_sentences("Hola Sra. López. ¿Cómo está? ¡Bienvenida!")
        self.assertEqual(len(es), 3)
        self.assertEqual(len(T.split_sentences("Price is 3.50 today. Thanks.")), 2)

    def test_units_roundtrip(self):
        for text in ["One. Two!\n\n- bullet one\n- bullet two | extra\n  Indented [Season 1, Episode 2]",
                     "  leading and trailing  ", "Title | Sub · Other", "x" * 900 + ", " + "y" * 500]:
            self.assertEqual("".join(p for _, p in T.units(text)), text)

    def test_needs_translation(self):
        for s in ["", "   ", "2026", "https://www.aagrapevine.org/x", "grapevine@neta65.org", "— · —"]:
            self.assertFalse(T.needs_translation(s), s)
        self.assertTrue(T.needs_translation("Dear Grapevine"))

    def test_protect_and_restore(self):
        p = T.Protector(T.Glossary({"keep": ["Grapevine", "AA"], "terms": [{"en": "home group", "es": "grupo base"}]}))
        m = p.mask("Write to grapevine@neta65.org 🎉 or visit https://x.org/a-b, AA home group at 7 PM", "en", "es")
        self.assertNotIn("@", m.text)
        self.assertNotIn("http", m.text)
        self.assertNotIn("🎉", m.text)
        self.assertIn("grupo base", m.slots)
        back = T.Protector.restore(m.text, m)
        self.assertIn("https://x.org/a-b", back)
        self.assertIn("grapevine@neta65.org", back)
        self.assertIn("🎉", back)
        # a lost placeholder is detected
        self.assertIsNone(T.Protector.restore(re.sub(r"XQ1\b", "", m.text), m))

    def test_glossary_accent_and_case(self):
        g = T.Glossary({"keep": ["La Viña", "GVR"], "terms": [{"en": "Season", "es": "Temporada", "exact": True}]})
        pat, ents = g.matcher("en", "es")
        self.assertTrue(pat.search("read la vina today"))
        self.assertFalse(pat.search("gvr lowercase"))            # ALL-CAPS entries are case-sensitive
        self.assertTrue(pat.search("the GVR"))
        self.assertFalse(pat.search("holiday season"))           # exact: true
        self.assertTrue(pat.search("Season 11"))

    def test_spanish_article_agreement(self):
        g = T.Glossary({"terms": [{"en": "Checklist", "es": "Lista de verificación"}]})
        m = T.Protector(g).mask("The Checklist", "en", "es")
        self.assertEqual(T.Protector.restore("El XQ1", m, "es"), "La Lista de verificación")

    def test_titlecase_detection(self):
        self.assertTrue(T.is_title_case("Crutches and Casts"))
        self.assertFalse(T.is_title_case("She found sobriety at a young age"))
        self.assertTrue(T.is_all_caps("POLÍTICA EDITORIAL"))
        self.assertEqual(T.decase("Hi Marissa, It's Jack XQ1"), "hi marissa, it's jack XQ1")
        self.assertEqual(T.recase_names("hola marissa, soy jack", "Hi Marissa, It's Jack"), "hola Marissa, soy Jack")

    def test_postprocess(self):
        self.assertEqual(T.postprocess("Is it ready?", "está listo?", "es"), "¿Está listo?")
        self.assertEqual(T.postprocess("New Freedom", "Nueva libertad.", "es"), "Nueva libertad")
        self.assertEqual(T.postprocess("🍇 New issue!", "🍇 Nuevo número!", "es"), "🍇 ¡Nuevo número!")

    def test_looks_like(self):
        self.assertTrue(T.looks_like("Esta reunión es para todos los miembros de la comunidad.", "es"))
        self.assertFalse(T.looks_like("Dear Grapevine", "es"))


class LocalizedSpans(unittest.TestCase):
    """Rules that never go through the model (no models needed)."""

    def slots(self, text, src="en", tgt="es"):
        m = T.Protector(T.Glossary({})).mask(text, src, tgt)
        return m.slots

    def test_season_episode(self):
        self.assertEqual(T.fix_season_episode("X [Seaon 3. Episdode 1]"), "X [Season 3, Episode 1]")
        self.assertEqual(T.fix_season_episode("X [Session 3, Episode 1]", "es"), "X [Temporada 3, Episodio 1]")
        self.assertIn("[Temporada 11, Episodio 12]", self.slots("Gated Communities [Season 11, Episode 12]"))
        self.assertIn("[Season 2, Episode 4]", self.slots("Algo [Temporada 2, Episodio 4]", "es", "en"))
        self.assertIn("Temporada 3", self.slots("Season 3 starts now"))

    def test_dates(self):
        self.assertIn("22 de julio de 2026", self.slots("Meeting, July 22, 2026"))
        self.assertIn("el 14 de marzo de 2027", self.slots("Join us on March 14, 2027"))
        self.assertIn("octubre de 2026", self.slots("GV News October 2026"))
        self.assertIn("September / October 2026", self.slots("Septiembre / Octubre 2026", "es", "en"))
        self.assertIn("on September 24, 2026", self.slots("Nos vemos el 24 de septiembre de 2026", "es", "en"))
        self.assertIn("September 24", self.slots("desde el 24 de septiembre", "es", "en"))
        self.assertIn("Jan-Feb '19", self.slots("Una silla - Ene-Fe '19", "es", "en"))
        self.assertNotIn("may", " ".join(self.slots("you may 5 times")).lower())   # "may" the verb

    def test_prices_ordinals_times(self):
        self.assertIn("$29.99", self.slots("por $29,99 al año", "es", "en"))
        self.assertIn("75th", self.slots("el 75º aniversario", "es", "en"))
        self.assertIn("7 p. m.", self.slots("Meet at 7 PM"))
        self.assertEqual(T.spanish_time("7:30 pm"), "7:30 p. m.")

    def test_codes_and_hashtag_runs(self):
        s = self.slots("GV ORDER FORM v52424 and Panel77 in the 9th Step of the 1990s at 7pm")
        self.assertIn("v52424", s)
        self.assertIn("Panel77", s)
        self.assertNotIn("9th", s)
        self.assertNotIn("1990s", s)
        run = self.slots("Great talk. #alcoholicsanonymous #aa #12stepprogram #sober…")
        self.assertEqual(run, ["#alcoholicsanonymous #aa #12stepprogram #sober…"])   # ONE placeholder
        self.assertFalse(T.needs_translation("v52424"))
        self.assertFalse(T.needs_translation("GVLV2027"))

    def test_ordinals_and_articles(self):
        self.assertIn("13.º", self.slots("her 13th sober anniversary"))
        m = T.Protector(T.Glossary({})).mask("the 5th edition", "en", "es")
        self.assertEqual(T.Protector.restore("la edición XQ1", m, "es"), "la edición 5.ª")
        self.assertEqual(T.Protector.restore("la XQ1 edición", m, "es"), "la 5.ª edición")
        self.assertEqual(T.Protector.restore("el XQ1 ICYPAA", T.Protector(T.Glossary({})).mask("54th", "en", "es"),
                                             "es"), "el 54.º ICYPAA")
        self.assertIn("Noveno Paso", self.slots("his 9th Step work"))
        self.assertIn("Tercera Tradición", self.slots("the 3rd Tradition"))
        self.assertEqual(T.Protector.restore("XQ1 aniversario", T.Protector(T.Glossary({})).mask("13th", "en", "es"),
                                             "es"), "13.º aniversario")
        g = T.Glossary({"terms": [{"en": "Higher Power", "es": "Poder Superior"}]})
        m = T.Protector(g).mask("un Poder Superior", "es", "en")
        self.assertEqual(T.Protector.restore("an XQ1", m, "en"), "a Higher Power")
        self.assertEqual(T.decase("From Dennis R., GRAPEVINE PHOTO CONTEST"), "from dennis R., grapevine photo contest")

    def test_name_before_dash_kept(self):
        units = T.units("Anselmo M. - Mi mejor amigo")
        self.assertIn((False, "Anselmo M."), units)
        self.assertIn((True, "Mi mejor amigo"), units)
        long = "word " * 30 + "- more"    # long lines are not split at dashes
        self.assertEqual(sum(1 for f, _ in T.units(long) if f), 1)


class OutputGuard(unittest.TestCase):
    def test_rejects_garbage(self):
        P = T.output_problem
        self.assertIsNone(P("Gated Communities", "Comunidades cerradas"))
        self.assertIn("repeats", P("Information Form", "información-información-información-información"))
        self.assertIn("longer", P("Hi there", "Hola " * 12))
        self.assertIn("numbers", P("GV ORDER FORM v52424", "Formulario v524"))
        self.assertIn("numbers", P("$29,99 al año", "$2.99 a year"))
        self.assertIn("numbers", P("Hola", "Hello 12"))
        self.assertIn("HTML entity", P("Love, Coffee & Hot Donuts", "Amor, café &quot; Donuts calientes"))

    def test_allows_spelled_numbers(self):
        P = T.output_problem
        self.assertIsNone(P("working the 9th Step", "trabajando el noveno paso"))
        self.assertIsNone(P("the twelve steps", "los 12 pasos"))
        self.assertIsNone(P("3,500 members", "3.500 miembros"))
        self.assertIsNone(P("no, no, no", "no, no, no"))


class CacheAndOverrides(unittest.TestCase):
    def test_overrides(self):
        o = T.Overrides({"Gated Communities": {"es": "Comunidades privadas"}})
        self.assertEqual(o.get("gated  communities", "es"), "Comunidades privadas")
        self.assertIsNone(o.get("Gated Communities", "en"))

    def test_cache_roundtrip_and_glossary_invalidation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.json"
            c = T.TranslationCache(path)
            g1 = T.Glossary({"terms": [{"en": "home group", "es": "grupo base"}]})
            c.sync_glossary(g1)
            c.put("en", "es", "My home group", "Mi grupo base")
            c.put("en", "es", "Hello", "Hola")
            c.save()
            self.assertTrue(json.loads(path.read_text(encoding="utf-8")))
            c2 = T.TranslationCache(path)
            self.assertEqual(c2.get("en", "es", "Hello"), "Hola")
            g2 = T.Glossary({"terms": [{"en": "home group", "es": "grupo hogar"}]})
            self.assertEqual(c2.sync_glossary(g2), 1)           # only the entry with the changed phrase
            self.assertIsNone(c2.get("en", "es", "My home group"))
            self.assertEqual(c2.get("en", "es", "Hello"), "Hola")


class QualityRules(unittest.TestCase):
    """Fixes for defects found in the real data (no models needed)."""

    def slots(self, text, src="en", tgt="es", glossary=None, names=True):
        return T.Protector(glossary or T.Glossary({})).mask(text, src, tgt, names=names).slots

    # 1. single words are translated; only real codes are skipped
    def test_single_words_are_not_codes(self):
        for word in ("Loneliness", "Hope", "Publisher", "loneliness", "Writing_Workshop_Guidelines", "9th"):
            self.assertTrue(T.needs_translation(word), word)
            self.assertFalse(T.is_code_token(word), word)
        for code in ("v52424", "Panel77", "GV_LV", "A65_GV", "GVLV2027", "2026-27"):
            self.assertTrue(T.is_code_token(code), code)
            self.assertFalse(T.needs_translation(code), code)

    def test_single_word_also_tried_lower_cased(self):
        tr = T.Translator(cache=False, use_model=False, glossary_path=ROOT / "nonexistent.yml",
                          overrides_path=ROOT / "nonexistent.yml")
        self.assertTrue(T.is_single_word("Loneliness"))
        self.assertFalse(T.is_single_word("loneliness"))
        self.assertEqual(tr._plan("Loneliness", "en", "es", 0).inputs, ["Loneliness", "loneliness"])
        self.assertTrue(T._roundtrip_ok("loneliness", "Loneliness"))
        self.assertTrue(T._roundtrip_ok("resentments", "resentment"))
        self.assertFalse(T._roundtrip_ok("gripevine", "flu"))     # a pun is not a word: kept as written

    def test_quoted_single_word(self):
        m = T._QUOTED.search("October’s special section is about “Loneliness.” AA members share")
        self.assertEqual(m.group(2), "Loneliness")
        self.assertIsNone(T._QUOTED.search('the phrase "Stump the Thumper" again'))   # only ONE word
        p = T.Protector(T.Glossary({}))
        p.quote_labels = {"Loneliness": "Soledad"}
        self.assertIn("Soledad", p.mask("It is about “Loneliness.” Share", "en", "es").slots)

    # 2. "coger" is vulgar in Mexico: never in the Spanish
    def test_catch_is_rewritten_before_the_model(self):
        self.assertEqual(T.pre_edit("Catch the meeting on our new podcast and on YouTube.", "en", "es"),
                         "Don't miss the meeting on our new podcast and on YouTube.")
        self.assertEqual(T.pre_edit("You can catch every episode of XQ1.", "en", "es"),
                         "You can listen to every episode of XQ1.")
        self.assertEqual(T.pre_edit("Watch live and catch the replay later.", "en", "es"),
                         "Watch live and listen to the replay later.")
        self.assertEqual(T.pre_edit("Catch up on past episodes.", "en", "es"), "Get caught up on past episodes.")
        self.assertEqual(T.pre_edit("Missed it? Catch it on YouTube.", "en", "es"), "Missed it? Find it on YouTube.")
        self.assertEqual(T.pre_edit("Catch the meeting", "es", "en"), "Catch the meeting")   # EN→ES only

    def test_coger_guard(self):
        F = T.fix_vulgar_es
        self.assertEqual(F("Coge la reunión en nuestro podcast.", "Catch the meeting"), "Agarra la reunión en nuestro podcast.")
        self.assertEqual(F("Cogió el autobús.", "He caught the bus."), "Tomó el autobús.")
        self.assertEqual(F("cógelo ahora y cogerla después", "catch it"), "agárralo ahora y agarrarla después")
        self.assertEqual(F("cogiendo fuerza; los cogí", "catching"), "agarrando fuerza; los agarré")
        self.assertEqual(F("COGER", "catch"), "AGARRAR")
        for ok in ("recoger un bolígrafo", "escoger", "acoger a los recién llegados", "encoger", "un cojín", "cogollo"):
            self.assertEqual(F(ok, "catch"), ok)
        self.assertEqual(F("una excusa coja", "a lame excuse"), "una excusa coja")   # adjective "lame"
        self.assertEqual(T.postprocess("Catch it!", "¡Cógelo!", "es"), "¡Agárralo!")

    # 3. ¿…? and ¡…! per sentence
    def test_spanish_marks_per_sentence(self):
        M = T.spanish_marks
        self.assertEqual(M("Para unirse use la contraseña 238047 Miércoles no es un buen momento?"),
                         "Para unirse use la contraseña 238047 ¿Miércoles no es un buen momento?")
        self.assertEqual(M("Bienvenidos. Cómo estás? Bien."), "Bienvenidos. ¿Cómo estás? Bien.")
        self.assertEqual(M("¿Cuáles son las herramientas que usas."), "¿Cuáles son las herramientas que usas?")
        self.assertEqual(M("Si no puedes venir, por qué no escuchas el pódcast?"),
                         "Si no puedes venir, ¿por qué no escuchas el pódcast?")
        self.assertEqual(M("20% de descuento!"), "¡20% de descuento!")
        self.assertEqual(M("¡Hola! ¿Qué tal?"), "¡Hola! ¿Qué tal?")
        self.assertEqual(M("Visita https://x.org/?a=1 hoy."), "Visita https://x.org/?a=1 hoy.")

    def test_runon_question_is_split_in_the_source(self):
        s = T.split_sentences("To join live, use Zoom code 871 2036 8287 with password 238047 "
                              "Wednesday's not a good time? Catch the meeting on our podcast.")
        self.assertEqual([x for x, _ in s], ["To join live, use Zoom code 871 2036 8287 with password 238047",
                                             "Wednesday's not a good time?", "Catch the meeting on our podcast."])
        self.assertEqual(len(T.split_sentences("Did you attend the 2026 Spring Assembly in Tyler?")), 1)
        self.assertEqual("".join(p for _, p in T.units("Use 238047 Wednesday's not a good time? Yes.")),
                         "Use 238047 Wednesday's not a good time? Yes.")

    # 4./5. names, Traditions, anniversaries, percentages, sizes
    def test_names_with_an_initial_are_protected(self):
        self.assertIn("Ginger S.", self.slots("Photo Credit: Ginger S."))
        self.assertIn("Victor E.", self.slots("Victor E. is back"))
        self.assertIn("Laura R.", self.slots("De Laura R., en Milwaukee", "es", "en"))
        self.assertNotIn("De Laura R.", self.slots("De Laura R., en Milwaukee", "es", "en"))
        self.assertEqual(self.slots("Read A.A. Grapevine"), [])
        self.assertEqual(self.slots("Study Tradition X. today"), [])
        self.assertEqual(self.slots("Victor E. is back", names=False), [])
        self.assertTrue(T._lost_names("Photo: Ginger S.", "Foto: jengibre S."))
        self.assertFalse(T._lost_names("Photo: Ginger S.", "Foto: Ginger S."))

    def test_traditions_areas_anniversaries_percentages(self):
        self.assertIn("Décima Tradición", self.slots("Tradition Ten"))
        self.assertIn("Décima Tradición", self.slots("the Tenth Tradition"))
        self.assertIn("Primera Tradición", self.slots("Tradition One says"))
        self.assertIn("Asamblea del Área 51", self.slots("Straight from the Area 51 Assembly"))
        self.assertIn("Área 51", self.slots("Secretary for Area 51"))
        self.assertEqual(self.slots("October 20% off"), [])                   # not "20 de octubre% …"
        self.assertIn("20 de octubre", self.slots("See you October 20 in Tyler"))
        self.assertIn("30th", self.slots("30 Aniversario de La Viña", "es", "en"))
        self.assertIn("8.5 x 11", self.slots("Print the 8.5 x 11 poster"))
        self.assertEqual(T.postprocess("30 Aniversario de La Viña", "30th Anniversary of La Viña", "en"),
                         "30th Anniversary of La Viña")                          # never "30Th"
        self.assertEqual(T.postprocess("It is about “Loneliness.”", "Es sobre “Soledad”", "es"), "Es sobre “Soledad”.")
        self.assertEqual(T.postprocess("Es sobre “Soledad.”", "It is about “Loneliness”", "en"), "It is about “Loneliness.”")
        self.assertEqual(T._gender_number("lemas"), ("m", "p"))
        self.assertEqual(T._gender_number("mano"), ("f", "s"))
        self.assertEqual(T._indefinite_en("unique Fellowship"), "a")
        self.assertEqual(T._indefinite_en("hour"), "an")

    # 6. casing
    def test_spanish_sentence_case(self):
        S = T.sentence_case_es
        self.assertEqual(S("Riendo Nuestro camino a la cárcel", "Laughing Our Way to Jail"), "Riendo nuestro camino a la cárcel")
        self.assertEqual(S("Tercer Aniversario Especial", "Third Anniversary Special"), "Tercer aniversario especial")
        self.assertEqual(S("Un viaje a Vancouver con Marissa", "A Trip to Vancouver with Marissa"),
                         "Un viaje a Vancouver con Marissa")
        self.assertEqual(S("El Dios de Nuestra XQ1", "The God of Our XQ1"), "El Dios de nuestra XQ1")
        self.assertEqual(S("Viaje a Nueva York", "Trip to New York"), "Viaje a Nueva York")
        hint = {"Dimensión": "common", "Yukón": None}.get
        self.assertEqual(S("La cuarta Dimensión", "The Fourth Dimension", hint), "La cuarta dimensión")
        self.assertEqual(S("De Yukón a Texas", "From Yukon to Texas", hint), "De Yukón a Texas")

    def test_english_title_case(self):
        C = T.title_case_en
        self.assertEqual(C("The emptiness behind the party"), "The Emptiness Behind the Party")
        self.assertEqual(C("Holidays in sobriety (choose one)"), "Holidays in Sobriety (Choose One)")
        self.assertEqual(C("A unique Fellowship"), "A Unique Fellowship")
        self.assertEqual(C("Poster of the app (8.5 x 11)"), "Poster of the App (8.5 x 11)")
        self.assertEqual(C("How to buy and/or exchange a self-help book"), "How to Buy and/or Exchange a Self-Help Book")
        self.assertEqual(C("30th anniversary of La Viña"), "30th Anniversary of La Viña")
        long = "This is a sentence. And another one"
        self.assertEqual(C(long), long)                                       # not a title: unchanged

    def test_conjunction_between_names(self):
        self.assertEqual(T._translate_conjunctions("XQ1 - XQ2 y XQ3", "es", "en"), "XQ1 - XQ2 and XQ3")
        self.assertEqual(T._translate_conjunctions("XQ1 AND XQ2", "en", "es"), "XQ1 Y XQ2")

    def test_post_edits(self):
        self.assertEqual(T.post_edit_es("durante las vacaciones", "during the holidays"), "durante las fiestas")
        self.assertEqual(T.post_edit_es("vacaciones sobrias", "sober vacations"), "vacaciones sobrias")
        self.assertEqual(T.post_edit_es("De el Foro y a el grupo", ""), "Del Foro y al grupo")
        self.assertEqual(T.post_edit_es("de El Paso", ""), "de El Paso")


class RealGlossary(unittest.TestCase):
    """The committee's glossary.yml / overrides.yml: only their STRUCTURE is checked here. The chair edits
    the wording on github.com, and a better translation must never turn a test (and with it every
    Dependabot pull request) red. How terms are applied is tested with the small glossary below."""

    def load(self, name):
        import yaml
        with open(ROOT / "data" / "translations" / name, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_glossary_is_well_formed(self):
        data = self.load("glossary.yml")
        self.assertIsInstance(data, dict)
        keep, terms = data.get("keep") or [], data.get("terms") or []
        self.assertTrue(keep and terms)
        for k in keep:
            self.assertTrue(isinstance(k, str) and k.strip(), f"keep: {k!r}")
        for t in terms:
            self.assertIsInstance(t, dict, t)
            for lang in ("en", "es"):
                self.assertTrue(isinstance(t.get(lang), str) and t[lang].strip(), f"term without {lang}: {t}")
            self.assertIn(t.get("only"), (None, "en", "es"), t)
            self.assertIn(t.get("exact", False), (True, False), t)
            self.assertLessEqual(set(t), {"en", "es", "only", "exact"}, t)
        self.assertTrue(T.Glossary.load().entries("en", "es"))

    def test_overrides_are_well_formed(self):
        data = self.load("overrides.yml")
        self.assertIsInstance(data, dict)
        seen: dict[str, dict] = {}
        for k, v in data.items():
            self.assertTrue(isinstance(k, str) and k.strip(), k)
            self.assertIsInstance(v, dict, k)
            self.assertTrue(v and set(v) <= {"en", "es"}, k)
            for lang, text in v.items():
                self.assertTrue(isinstance(text, str) and text.strip(), f"{k!r} → {lang}")
            folded = T.fold(T._norm_key(k)).lower()
            self.assertEqual(seen.setdefault(folded, v), v, f"two different fixes for {k!r}")
        self.assertEqual(len(T.Overrides(data).exact), len({T._norm_key(k) for k in data}), "an unusable entry")


class GlossaryRules(unittest.TestCase):
    """How glossary terms and overrides are applied (a small glossary written here)."""

    @classmethod
    def setUpClass(cls):
        cls.g = T.Glossary({
            "keep": ["Grapevine", "La Viña", "AA"],
            "terms": [
                {"en": "Publisher", "es": "editor", "only": "es"},
                {"en": "Grapevine Publisher", "es": "editor de Grapevine", "only": "es"},
                {"en": "American Sign Language (ASL)", "es": "Lengua de señas americana (ASL)"},
                {"en": "American Sign Language", "es": "Lengua de señas americana (ASL)", "only": "es"},
                {"en": "Sober Holidays", "es": "fiestas sobrias", "only": "es"},
                {"en": "Carry the Message Project", "es": "Proyecto Lleva el Mensaje"},
                {"en": "Carry the Message Project", "es": "Proyecto Lleve el Mensaje", "only": "en"},
                {"en": "reach out", "es": "tender la mano", "only": "en"},
                {"en": "by reaching out", "es": "al tender la mano", "only": "en"},
                {"en": "We're not a glum lot", "es": "No somos un grupo sombrío", "only": "es"},
            ]})
        cls.o = T.Overrides({"Tocaron Fondo": {"en": "Hitting Bottom"}, "Coming in": {"es": "Llegando a AA"},
                             "Cómo rezo": {"en": "How I Pray"}})

    def slots(self, text, src="en", tgt="es"):
        return T.Protector(self.g).mask(text, src, tgt).slots

    def test_longest_phrase_direction_and_accents(self):
        self.assertIn("editor de Grapevine", self.slots("New Grapevine Publisher"))     # longest phrase wins
        self.assertIn("editor", self.slots("Letter from our Publisher"))
        self.assertNotIn("editor", " ".join(self.slots("the publisher", "es", "en")))  # `only: es`
        self.assertIn("fiestas sobrias", self.slots("Sober Holidays!"))                  # any capitalization
        self.assertIn("No somos un grupo sombrío", self.slots("We’re not a glum lot."))  # ’ matches '
        self.assertEqual(self.slots("American Sign Language (ASL)").count("Lengua de señas americana (ASL)"), 1)
        self.assertIn("Carry the Message Project", self.slots("PROYECTO LLEVE EL MENSAJE", "es", "en"))
        self.assertIn("by reaching out", self.slots("Porque al tender la mano", "es", "en"))
        self.assertIn("La Viña", self.slots("Lea La Vina", "es", "en"))                   # accent-insensitive

    def test_overrides(self):
        self.assertEqual(self.o.get("Tocaron Fondo", "en"), "Hitting Bottom")
        self.assertEqual(self.o.get("Coming in", "es"), "Llegando a AA")
        self.assertEqual(self.o.get("Como rezo", "en"), "How I Pray")                     # accent-insensitive
        self.assertIsNone(self.o.get("Coming in", "en"))

    def test_whole_title_override_wins_over_the_dash_split(self):
        # a short title is cut at " — " before translation; an override for the WHOLE title (with or
        # without a "[Season …]" tail) must still win, and the tail is still translated on its own
        with tempfile.TemporaryDirectory() as d:
            ov = Path(d) / "o.yml"
            ov.write_text('"Widening the Doorway — The Plain Language Big Book": { es: "Ampliar la puerta" }\n'
                          '"(English)": { es: "(inglés)" }\n', encoding="utf-8")
            tr = T.Translator(cache=False, use_model=False, glossary_path=Path(d) / "none.yml", overrides_path=ov)
            title = "Widening the Doorway — The Plain Language Big Book"
            for text in (title, title + " [Season 10, Episode 20]"):
                plan = tr._units(T.fix_season_episode(text, "en"), "es")
                self.assertEqual("".join(p for _, p in plan), T.fix_season_episode(text, "en"))
                self.assertIn((True, title), plan)
            # (no model here: _translate_new is what translate() calls for a text not in the cache)
            self.assertEqual(tr._translate_new([title + " (English)"], "en", "es"), ["Ampliar la puerta (inglés)"])
            self.assertEqual(tr._units("New Publisher (English)", "es")[-1], (True, "(English)"))
            # without an override the title is still cut at the dash
            self.assertEqual([p for f, p in tr._units("Eloy E. - De la oscuridad", "en") if f], ["De la oscuridad"])

    def test_override_fixes_a_cached_longer_text(self):
        # An override for a title must also fix the cached translation of a text that contains it
        # ("Bottle to Throttle [Season 5, Episode 8]"): the cache entry is dropped so it is redone.
        with tempfile.TemporaryDirectory() as d:
            c = T.TranslationCache(Path(d) / "c.json")
            c.put("en", "es", "Bottle to Throttle [Season 5, Episode 8]", "Botella para hervidor [Temporada 5, Episodio 8]")
            c.put("en", "es", "Something else", "Otra cosa")
            c.put("es", "en", "Bottle to Throttle", "unchanged direction")
            self.assertEqual(c.sync_overrides(T.Overrides({"Bottle to Throttle": {"es": "De la botella al volante"}})), 1)
            self.assertIsNone(c.get("en", "es", "Bottle to Throttle [Season 5, Episode 8]"))
            self.assertEqual(c.get("en", "es", "Something else"), "Otra cosa")
            self.assertEqual(c.get("es", "en", "Bottle to Throttle"), "unchanged direction")
            c.put("en", "es", "Bottle to Throttle [Season 5, Episode 8]", "De la botella al volante [Temporada 5, Episodio 8]")
            # unchanged overrides next time → nothing is redone
            self.assertEqual(c.sync_overrides(T.Overrides({"Bottle to Throttle": {"es": "De la botella al volante"}})), 0)
            self.assertIsNotNone(c.get("en", "es", "Bottle to Throttle [Season 5, Episode 8]"))


# A small glossary for the end-to-end tests, so they do not depend on the chair's wording.
E2E_GLOSSARY = """
keep: [AA Grapevine, A.A. Grapevine, Grapevine, La Viña, Dear Grapevine, Grapevine Weekly Open AA Meeting,
       Grapevine Weekly Open, AA Grapevine Podcast, AA, A.A., NETA 65, GVR, RLV, Zoom, YouTube]
terms:
  - { en: "Big Book", es: "Libro Grande" }
  - { en: "home group", es: "grupo base" }
  - { en: "sponsor", es: "padrino" }
  - { en: "sponsor", es: "madrina", only: en }
  - { en: "Twelve Steps", es: "Doce Pasos" }
  - { en: "DCM", es: "MCD" }
  - { en: "Season", es: "Temporada", exact: true }
  - { en: "Episode", es: "Episodio", exact: true }
  - { en: "Noon Eastern", es: "mediodía (hora del Este)", only: es }
"""


@unittest.skipUnless(MODELS, "translation models not installed")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        (tmp / "glossary.yml").write_text(E2E_GLOSSARY, encoding="utf-8")
        (tmp / "overrides.yml").write_text("{}\n", encoding="utf-8")
        cls.tr = T.Translator(cache_path=tmp / "c.json", glossary_path=tmp / "glossary.yml",
                              overrides_path=tmp / "overrides.yml", download=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def es(self, text):
        return self.tr.translate([text], "en", "es")[0][0]

    def en(self, text):
        return self.tr.translate([text], "es", "en")[0][0]

    def test_brands_kept(self):
        self.assertEqual(self.es("Dear Grapevine"), "Dear Grapevine")
        self.assertIn("La Viña", self.es("Read La Viña every month."))

    def test_no_dropped_sentence(self):
        out = self.es("Join us at the Spring Assembly. Volunteers welcome!")
        self.assertIn("Asamblea", out)
        self.assertIn("voluntarios", out.lower())

    def test_season_episode(self):
        self.assertTrue(self.es("Gated Communities [Season 11, Episode 12]").endswith("[Temporada 11, Episodio 12]"))
        out = self.es("The Junkyard [Season 5. Episode 10] - AA Grapevine Podcast")
        self.assertIn("[Temporada 5, Episodio 10] - AA Grapevine Podcast", out)
        self.assertTrue(self.en("Algo [Temporada 2, Episodio 4]").endswith("[Season 2, Episode 4]"))

    def test_brands_everywhere(self):
        out = self.es("Read Dear Grapevine in AA Grapevine and La Viña; ask your GVR or RLV at NETA 65 "
                      "about the Grapevine Weekly Open AA Meeting.")
        for w in ("Dear Grapevine", "AA Grapevine", "La Viña", "GVR", "RLV", "NETA 65", "Grapevine Weekly Open"):
            self.assertIn(w, out)

    def test_ampersand_and_issue_month(self):
        self.assertEqual(self.es("Love, Coffee & Hot Donuts").count("&"), 0)     # no "&quot;" leak
        self.assertIn("número de junio de 2026", self.es("Her story appears in the June 2026 issue of Grapevine."))

    def test_numbers_never_change(self):
        for text in ("GV ORDER FORM GIFT v52424", "Grapevine Weekly Open AA Meeting, July 22, 2026 [Season 2, Episode 4]",
                     "Join us on March 14, 2027 at 9 AM for the Spring Assembly."):
            self.assertIsNone(T.output_problem(text, self.es(text)), text)

    def test_glossary_terms(self):
        out = self.es("Ask your DCM how to become a GVR, and bring the Big Book to your home group.")
        for w in ("MCD", "GVR", "Libro Grande", "grupo base"):
            self.assertIn(w, out)
        self.assertIn("sponsor", self.en("Mi madrina me enseñó los Doce Pasos.").lower())

    def test_urls_emails_emoji_intact(self):
        out = self.es("🍇 Write to grapevine@neta65.org or visit https://www.aagrapevine.org/gvr-resources today!")
        for w in ("🍇", "grapevine@neta65.org", "https://www.aagrapevine.org/gvr-resources"):
            self.assertIn(w, out)

    def test_markdown_structure(self):
        md = "## Welcome!\n\n- Read the [Grapevine](https://www.aagrapevine.org) every **month**.\n\n```\ncode\n```"
        out, machine = self.tr.translate_markdown(md, "en", "es")
        self.assertTrue(machine)
        self.assertTrue(out.startswith("## "))
        self.assertIn("](https://www.aagrapevine.org)", out)
        self.assertIn("\n```\ncode\n```", out)
        self.assertEqual(out.count("**"), 2)

    def test_passthrough(self):
        self.assertEqual(self.tr.translate(["https://x.org", "2026", ""], "en", "es"),
                         [("https://x.org", False), ("2026", False), ("", False)])

    def test_single_word_translated(self):
        self.assertEqual(self.es("Loneliness"), "Soledad")                   # was left in English
        out = self.es("October’s special section is about “Loneliness.” AA members share touching stories.")
        self.assertIn("Soledad", out)
        self.assertNotIn("Lonabilidad", out)
        self.assertEqual(self.es("Gripevine"), "Gripevine")                  # a pun, not "Gripe" (flu)

    def test_no_coger_and_marks_per_sentence(self):
        out = self.es("To join the meeting live on Wednesdays at Noon Eastern, use Zoom code 871 2036 8287 with "
                      "password 238047 Wednesday's not a good time? Catch the meeting on our new podcast and on YouTube.")
        self.assertNotRegex(out, r"(?i)\bc[oó][gj]")
        self.assertFalse(out.startswith("¿"))
        self.assertRegex(out, r"238047 ¿[^?]+\?")
        self.assertRegex(out, r"No te pierdas|Escucha")
        self.assertNotRegex(self.es("He caught the bus."), r"(?i)\bc[oó][gj]")

    def test_names_traditions_titles(self):
        self.assertEqual(self.es("Tradition Ten"), "Décima Tradición")
        self.assertIn("Ginger S.", self.es("He received so much more. Photo Credit: Ginger S."))
        self.assertTrue(self.es("Victor E. is back").startswith("Victor E. "))
        self.assertEqual(self.es("Third Anniversary Special"), "Tercer aniversario especial")
        self.assertEqual(self.en("30 Aniversario de La Viña"), "30th Anniversary of La Viña")
        self.assertEqual(self.es("October 20% off"), "Octubre 20% de descuento")


class CommitteeContent(unittest.TestCase):
    def test_front_matter(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "2027-01-10-welcome.md"
            p.write_text("---\ntitle: Welcome!\nexpires: 2027-03-31\npinned: yes\n---\nHello **all** "
                         "[link](https://x.org).\n", encoding="utf-8")
            it = parse_announcement(p)
            self.assertEqual((it["id"], it["date"], it["extra"]["expires"], it["extra"]["pinned"]),
                             ("ann:2027-01-10-welcome", "2027-01-10", "2027-03-31", True))
            self.assertEqual(it["summary"], "Hello all link.")
            bad = Path(d) / "bad.md"
            bad.write_text('---\ntitle: "unclosed\n---\nx', encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_announcement(bad)

    def test_event_times(self):
        from zoneinfo import ZoneInfo
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "booth.md"
            p.write_text("---\ntitle: Booth\nstart: 2027-09-18 09:00\nlocation: Tyler, TX\n---\n", encoding="utf-8")
            it = parse_event(p, ZoneInfo("America/Chicago"))
            self.assertEqual(it["extra"]["start"], "2027-09-18T14:00:00Z")
            self.assertEqual((it["extra"]["city"], it["extra"]["state"]), ("Tyler", "TX"))

    def test_markdown_to_text(self):
        self.assertEqual(markdown_to_text("## Hi\n- **Bold** [x](http://a) `c`"), "Hi Bold x c")


class BuildRules(unittest.TestCase):
    """build_data.py rules that need no network and no models."""

    @classmethod
    def setUpClass(cls):
        from scripts.sync import build_data as B
        cls.B = B

    def test_issue_labels(self):
        L = self.B.issue_label
        self.assertEqual(L("gv", "2026-10", "October 2026"), {"en": "October 2026", "es": "Octubre 2026"})
        self.assertEqual(L("lv", "2026-09", "Septiembre / Octubre 2026"),
                         {"en": "September / October 2026", "es": "Septiembre / Octubre 2026"})
        self.assertEqual(L("lv", "2026-11", None)["en"], "November / December 2026")
        self.assertIsNone(L("gv", "2026-13", None))

    def test_weekly_open_labels(self):
        it = {"kind": "meeting", "extra": {"weekday": "wednesday", "start_local": "12:00",
                                           "timezone": "America/New_York", "next_start": "2026-09-30T16:00:00Z",
                                           "zoom_id": "871 2036 8287", "passcode": "238047"}}
        lab = self.B.weekly_open_labels(it)
        self.assertEqual(lab["when"], {"en": "Wednesdays at 11:00 AM Central",
                                       "es": "Miércoles a las 11:00 a. m. (hora del Centro)"})
        self.assertEqual(lab["time"]["en"], "Noon Eastern")
        self.assertIn("871 2036 8287", lab["sentence"]["es"])
        self.assertEqual(self.B.weekly_open_labels({"kind": "meeting", "extra": {}}), {})

    def test_hashtag_wall_and_last_seen(self):
        it = self.B.prep({"id": "x", "title": "T [Seaon 1, Episode 2]", "lang": "en", "last_seen": "2026-01-01",
                          "summary": "Great talk. #aa #sober #podcast #twelvesteps…"})
        self.assertEqual(it["summary"], "Great talk.")
        self.assertNotIn("last_seen", it)
        self.assertEqual(it["title"], "T [Season 1, Episode 2]")

    def test_never_new_and_closed_forms(self):
        ctx = self.B.Ctx(offline=True)
        now = ctx.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertFalse(ctx.is_new({"kind": "topic", "date": now, "first_seen": now}, "editorial"))
        self.assertFalse(ctx.is_new({"kind": "meeting", "date": now, "first_seen": now}, "weekly_open"))
        self.assertTrue(ctx.is_new({"kind": "video", "date": now, "first_seen": now}, "youtube"))
        self.assertTrue(self.B.closed_form({"kind": "form", "extra": {"form_closed": True}}))
        self.assertFalse(self.B.closed_form({"kind": "form", "extra": {"form_closed": False}}))

    def test_photo_group_links_to_its_album(self):
        B = self.B
        self.assertEqual(B.album_slug("Panel 76 / Assembly Photos"), "panel-76-assembly-photos")
        self.assertEqual(B.album_slug("Asamblea de Área — Fotos"), "asamblea-de-area-fotos")
        self.assertIsNone(B.album_slug(""))                  # no album → the page has no such anchor
        self.assertIsNone(B.album_slug("Albums"))            # an id the page already uses
        ctx = B.Ctx(offline=True)
        found = ctx.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        photos = [{"id": f"drive:p{i}", "source": "drive", "kind": "photo", "title": f"p{i}", "date": None,
                   "first_seen": found, "image": None, "extra": {"album": "Fall Assembly 2026"}} for i in range(3)]
        groups = [it for _, it in B.plan_whatsnew(ctx, {"drive": photos}) if it.get("extra", {}).get("is_group")]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["url"], "/photos/#fall-assembly-2026")
        self.assertEqual(groups[0]["extra"]["album_slug"], "fall-assembly-2026")

    def test_spanish_titles_get_english_title_case(self):
        pair = {"en": "The emptiness behind the party", "es": "El vacío detrás de la fiesta"}
        self.assertEqual(self.B.en_title_case(dict(pair), "es")["en"], "The Emptiness Behind the Party")
        self.assertEqual(self.B.en_title_case(dict(pair), "en")["en"], pair["en"])     # English originals untouched

    def test_first_harvest_is_not_news(self):
        ctx = self.B.Ctx(offline=True)
        ctx.births["pdfs"] = ctx.births["youtube"] = ctx.now_ts - 86400        # first harvest yesterday
        found = (ctx.now.replace(microsecond=0)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = {"kind": "video", "date": "2019-05-01", "first_seen": found}
        undated = {"kind": "video", "date": None, "first_seen": found}
        self.assertFalse(ctx.is_new(old, "youtube"))
        self.assertFalse(ctx.is_new(undated, "youtube"))             # found in the first harvest
        self.assertIsNone(ctx.effective_ts({"kind": "pdf", "date": None, "first_seen": found}, "pdfs"))
        ctx.births["youtube"] = ctx.now_ts - 10 * 86400              # a week after the first harvest
        self.assertTrue(ctx.is_new(undated, "youtube"))


class RunAllFlags(unittest.TestCase):
    def test_help_has_workflow_flags(self):
        import contextlib
        import io
        from scripts.sync import run_all
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            run_all.main(["--help"])
        self.assertIn("--crawl-minutes", buf.getvalue())
        self.assertIn("--quick", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
