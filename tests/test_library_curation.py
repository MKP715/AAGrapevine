"""Library curation (scripts/sync/pdf_curate.py + the crawler's official-host rule): official AA sources
only, each document once, language editions on one entry. Fixtures mirror real crawler records."""
from __future__ import annotations

import copy
import unittest

from scripts.sync import crawl_rules as R
from scripts.sync import pdf_curate as P

GV = "https://www.aagrapevine.org/sites/default/files/"
LV = "https://www.aalavina.org/sites/default/files/"
GVR_PAGE = {"url": "https://www.aagrapevine.org/gvr-resources", "title": "GVR Resources"}
RLV_PAGE = {"url": "https://www.aalavina.org/recursos", "title": "Recursos"}


def pdf(pid: str, url: str, title: str, *, lang: str = "en", doc_lang: str | None = None, en: str | None = None,
        es: str | None = None, date: str | None = "2026-01-08", category: str = "gvr", tags=("postcard",),
        size: int | None = 100_000, pages: int | None = 1, thumb: str | None = None, refs=None,
        orphan: bool = False, is_new: bool = False) -> dict:
    """A pdfs.json item as build_data has it after translation (i18n + machine filled in)."""
    host = url.split("/")[2]
    um = date[:7] if date and "/files/" in url and url.split("/files/")[1][:7] == date[:7] else None
    t_en = en if en is not None else (title if lang == "en" else title + " (en)")
    t_es = es if es is not None else (title if lang == "es" else title + " (es)")
    return {
        "id": pid, "source": "crawl", "kind": "pdf", "url": url, "title": title, "summary": "", "lang": lang,
        "date": date, "first_seen": "2026-09-20T00:00:00Z", "image": thumb, "tags": list(tags), "category": category,
        "status": "ok",
        "extra": {"host": host, "file_url": url, "filename": url.rsplit("/", 1)[-1], "size_bytes": size, "pages": pages,
                  "thumb": thumb, "referrers": list(refs if refs is not None else ([RLV_PAGE] if "aalavina" in host else [GVR_PAGE])),
                  "upload_month": um, "link_texts": [], "event_date": None, "doc_lang": doc_lang or lang,
                  "section": None, "external": "aagrapevine" not in host and "aalavina" not in host, "orphan": orphan},
        "i18n": {"title": {"en": t_en, "es": t_es}, "summary": {"en": "", "es": ""}},
        "machine": ["es"] if lang == "en" else ["en"],
        "is_new": is_new,
    }


def no_thumbs(_path: str):
    return None


def curate(items, **kw):
    kw.setdefault("thumb_digest", no_thumbs)
    return P.curate(copy.deepcopy(items), **kw)


def urls_of(entry: dict) -> list[str]:
    vs = entry["extra"].get("versions")
    return [v["url"] for v in vs] if vs else [entry["url"]]


class OfficialSources(unittest.TestCase):
    def test_official_hosts_and_subdomains(self):
        hosts = R.OFFICIAL_DOC_HOSTS
        for h in ("www.aagrapevine.org", "aagrapevine.org", "www.aalavina.org", "www.aa.org", "aa.org",
                  "aaws.widen.net", "WWW.AA.ORG"):
            self.assertTrue(R.is_official_doc_host(h, hosts), h)
        for h in ("www.aawv.org", "aa-montana.org", "aa-seta.org", "www.marylandaa.org", "www.aataiwan.com",
                  "notaa.org", "aa.org.evil.com", "", None):
            self.assertFalse(R.is_official_doc_host(h, hosts), h)

    def test_config_list_replaces_the_default(self):
        self.assertEqual(R.official_doc_hosts({"library": {"official_hosts": ["AA.org", " aagrapevine.org "]}}),
                         ("aa.org", "aagrapevine.org"))
        self.assertEqual(R.official_doc_hosts({"library": {"official_hosts": []}}), R.OFFICIAL_DOC_HOSTS)
        self.assertEqual(R.official_doc_hosts({}), R.OFFICIAL_DOC_HOSTS)
        self.assertEqual(P.official_hosts({"library": {"official_hosts": ["aa.org"]}}), ("aa.org",))

    def test_crawler_records_only_official_documents(self):
        page = "https://www.aagrapevine.org/get-involved/events/2025-07-25/area-73-convention"
        hosts = R.OFFICIAL_DOC_HOSTS
        self.assertIsNone(R.pdf_url_from_href("https://www.aawv.org/_files/ugd/69408f_7bfc.pdf", page, hosts=hosts))
        self.assertIsNone(R.pdf_url_from_href("http://aa-montana.org/pdf/2016-08Campout.pdf", page, hosts=hosts))
        self.assertEqual(R.pdf_url_from_href("/sites/default/files/2026-01/2026-CTM.pdf", page, hosts=hosts),
                         GV + "2026-01/2026-CTM.pdf")
        self.assertEqual(R.pdf_url_from_href("https://www.aa.org/sites/default/files/literature/x.pdf", page, hosts=hosts),
                         "https://www.aa.org/sites/default/files/literature/x.pdf")
        self.assertEqual(R.pdf_url_from_href("https://aaws.widen.net/s/abc/f-2.pdf", page, hosts=hosts),
                         "https://aaws.widen.net/s/abc/f-2.pdf")
        viewer = "https://docs.google.com/viewer?url=https://www.aalavina.org/sites/default/files/2026-01/x.pdf"
        self.assertEqual(R.pdf_url_from_href(viewer, page, hosts=hosts), LV + "2026-01/x.pdf")
        # a site that is not in the configured list
        self.assertIsNone(R.pdf_url_from_href("https://www.aa.org/x.pdf", page, hosts=("aagrapevine.org",)))

    def test_build_drops_documents_from_other_sites(self):
        items = [pdf("pdf:1", GV + "2026-01/2026-CTM.pdf", "2026 Carry The Message Project"),
                 pdf("pdf:2", "https://www.aa.org/sites/default/files/literature/Retrofit.pdf", "Letter", category="news", tags=()),
                 pdf("pdf:3", "https://www.aawv.org/_files/ugd/69408f.pdf", "Area 73 Convention", category="flyer", tags=("event",)),
                 pdf("pdf:4", "http://www.marylandaa.org/docs/fall_conf_2012.pdf", "Area 29 Fall Convention", category="flyer")]
        with self.assertLogs("pdf_curate", level="INFO") as logs:
            kept = P.official_only(items, R.OFFICIAL_DOC_HOSTS)
        self.assertEqual([i["id"] for i in kept], ["pdf:1", "pdf:2"])
        self.assertTrue(any("2 document(s) from non-official hosts" in m for m in logs.output))


class SameFile(unittest.TestCase):
    def test_two_names_for_one_file(self):
        """EditorialCalendar_GV_2026_27.pdf and Editorial_Calendar_GV_2026_27.pdf: 63,423 bytes, 2 pages."""
        kit = pdf("pdf:dc07", GV + "2026-02/EditorialCalendar_GV_2026_27.pdf", "Editorial Calendar", date="2026-02-18",
                  tags=("guidelines",), size=63423, pages=2)
        page = pdf("pdf:37b8", GV + "2026-02/Editorial_Calendar_GV_2026_27.pdf", "Editorial Calendar", date="2026-02-18",
                   category="guidelines", tags=(), size=63423, pages=2,
                   refs=[{"url": "https://www.aagrapevine.org/contribute", "title": "Contribute"}])
        out, swaps = curate([kit, page])
        self.assertEqual(len(out), 1)
        keep = out[0]
        self.assertEqual(keep["id"], "pdf:dc07", "the rep-kit copy stays")
        self.assertEqual([r["url"] for r in keep["extra"]["referrers"]],
                         [GVR_PAGE["url"], "https://www.aagrapevine.org/contribute"])
        self.assertEqual(keep["extra"]["duplicates"], [GV + "2026-02/Editorial_Calendar_GV_2026_27.pdf"])
        self.assertEqual(len(swaps), 1)

    def test_same_address_on_both_sites_and_drupal_rename(self):
        a = pdf("pdf:a", LV + "2021-01/SF-202-Espiritualidad_ESPANOL_0.PDF", "La Espiritualidad y la Mención de Dios",
                lang="es", category="rlv", tags=(), en="Spirituality and God-Talk", size=163215, pages=2, date="2021-01-21")
        b = pdf("pdf:b", GV + "2021-01/SF-202-Espiritualidad_ESPANOL.PDF", "Spirituality and God-Talk (Spa.)",
                lang="en", doc_lang="es", tags=(), size=163215, pages=2, date="2021-01-21")
        c = pdf("pdf:c", LV + "2021-01/sf-202-espiritualidad_espanol.pdf", "Espiritualidad", lang="es",
                category="rlv", tags=(), size=None, pages=None, date="2021-01-21")
        out, _ = curate([a, b, c])
        self.assertEqual([i["id"] for i in out], ["pdf:a"], "La Viña's copy of a Spanish document stays")
        self.assertEqual(out[0]["extra"]["kits"], ["gvr", "rlv"], "still in both rep kits")

    def test_bilingual_file_filed_under_each_sites_language(self):
        """GV_Catalog_2026.pdf = LV_Catalogo_2026.pdf (same bytes, same first page) → one entry, two editions."""
        gv = pdf("pdf:gv", GV + "2026-02/GV_Catalog_2026.pdf", "Catalog 2026", tags=("catalog",), size=18082337,
                 pages=32, thumb="/assets/cache/pdf/gv.webp", date="2026-02-02")
        lv = pdf("pdf:lv", LV + "2026-02/LV_Catalogo_2026.pdf", "Catálogo 2026", lang="es", category="rlv",
                 tags=("catalog",), en="2026 Catalog", size=18082337, pages=32, thumb="/assets/cache/pdf/lv.webp",
                 date="2026-02-02")
        out, _ = curate([gv, lv], thumb_digest=lambda p: "same-first-page" if p else None)
        self.assertEqual(len(out), 1)
        e = out[0]
        self.assertTrue(e["extra"]["same_file"])
        self.assertEqual([v["lang"] for v in e["extra"]["versions"]], ["en", "es"])
        self.assertEqual(e["i18n"]["title"], {"en": "Catalog 2026", "es": "Catálogo 2026"})
        self.assertEqual(e["machine"], [], "each page shows its own site's original title")

    def test_same_size_but_different_documents_stay(self):
        a = pdf("pdf:a", GV + "2026-01/Books_PC_GV_2026.pdf", "Grapevine Books", size=500, pages=1)
        b = pdf("pdf:b", GV + "2026-01/Bookmark.pdf", "Bookmark", size=500, pages=1)
        c = pdf("pdf:c", GV + "2026-01/Other.pdf", "Grapevine Books", size=501, pages=1, date="2026-01-09")
        out, _ = curate([a, b, c], thumb_digest=lambda p: None)
        self.assertEqual(len(out), 2, "same size alone is not enough; a newer same-title file supersedes")
        out2, _ = curate([a, b])
        self.assertEqual(len(out2), 2)


class Superseded(unittest.TestCase):
    def test_older_edition_of_the_same_postcard(self):
        new = pdf("pdf:new", GV + "2026-01/YOUTUBE_PC_GV.pdf", "Youtube Channel", size=229162)
        old = pdf("pdf:old", GV + "2020-01/Youtube%20LH_2019%20New.pdf", "YouTube Channel", tags=(), size=331012,
                  date="2020-01-01")
        with self.assertLogs("pdf_curate", level="INFO") as logs:
            out, swaps = curate([new, old])
        self.assertEqual([i["id"] for i in out], ["pdf:new"])
        self.assertTrue(any("superseded" in m and "pdf:old" in m for m in logs.output))
        self.assertEqual([k["id"] for k in swaps.values()], ["pdf:new"], "What's New points at the new one")

    def test_same_title_different_category_or_type_or_language_stays(self):
        a = pdf("pdf:a", GV + "2024-08/GV-Handbook.pdf", "Handbook", tags=("service",), size=1)
        b = pdf("pdf:b", GV + "2020-01/Handbook.pdf", "Handbook", category="guidelines", tags=(), size=2, date="2020-01-01")
        c = pdf("pdf:c", GV + "2020-01/Handbook-2.pdf", "Handbook", tags=("order-form",), size=3, date="2020-01-01")
        d = pdf("pdf:d", LV + "2020-01/Handbook.pdf", "Handbook", lang="en", doc_lang="fr", category="rlv",
                tags=("service",), size=4, date="2020-01-01")
        out, _ = curate([a, b, c, d])
        self.assertEqual(len(out), 3, "a/b differ in category, a/c in type; d is another language (an edition)")


class LanguageEditions(unittest.TestCase):
    def test_english_and_spanish_edition_become_one_entry(self):
        en = pdf("pdf:en", GV + "2026-01/Audio_download_GV_2026.pdf.pdf", "Audio Downloads", size=97595, is_new=False)
        es = pdf("pdf:es", LV + "2026-01/Descarga_de_audios_2026.pdf", "Descarga de Audios", lang="es", category="rlv",
                 en="Audio Downloads", size=89988, is_new=True)
        out, swaps = curate([es, en])
        self.assertEqual(len(out), 1)
        e = out[0]
        self.assertEqual(e["id"], "pdf:en", "the English edition is the entry")
        vs = e["extra"]["versions"]
        self.assertEqual([(v["lang"], v["id"], v["source"]) for v in vs], [("en", "pdf:en", "gv"), ("es", "pdf:es", "lv")])
        self.assertEqual(vs[1]["url"], LV + "2026-01/Descarga_de_audios_2026.pdf")
        self.assertEqual((vs[1]["size_bytes"], vs[1]["pages"]), (89988, 1))
        self.assertEqual(e["i18n"]["title"], {"en": "Audio Downloads", "es": "Descarga de Audios"})
        self.assertEqual(e["machine"], [])
        self.assertEqual(e["extra"]["kits"], ["gvr", "rlv"])
        self.assertTrue(e["is_new"], "new when any edition is new")
        self.assertEqual(len(swaps), 1)

    def test_three_languages_and_markers(self):
        base = GV + "2021-08/New_Publisher-{}-Anncmnt-Aug2021.pdf"
        items = [pdf(f"pdf:{l}", base.format(w), f"New Grapevine Publisher ({w.title()})", doc_lang=l,
                     category="news", tags=(), date="2021-08-04",
                     es=f"Nuevo editor de Grapevine ({ {'en': 'inglés', 'es': 'español', 'fr': 'francés'}[l]})")
                 for l, w in (("fr", "FRENCH"), ("es", "SPANISH"), ("en", "ENGLISH"))]
        out, _ = curate(items)
        self.assertEqual(len(out), 1)
        e = out[0]
        self.assertEqual([v["lang"] for v in e["extra"]["versions"]], ["en", "es", "fr"])
        self.assertEqual(e["title"], "New Grapevine Publisher", "the language marker goes: the links say it")
        self.assertEqual(e["i18n"]["title"]["en"], "New Grapevine Publisher")
        self.assertEqual(e["i18n"]["title"]["es"], "Nuevo editor de Grapevine", "the Spanish edition's title, marker removed")
        self.assertEqual(e["machine"], ["es"], "the Spanish edition's English title was translated")

    def test_kit_counterparts_published_years_apart(self):
        """'Prayer and Meditation' postcards: GVR kit 2021-12, RLV kit 2023-09 (same type, same page count)."""
        en = pdf("pdf:en", GV + "2021-12/GV%20NEW_PRAYER.pdf", "Prayer and Meditation", pages=2, date="2021-12-03")
        es = pdf("pdf:es", LV + "2023-09/Oracion_y_meditacion.pdf", "Oración y Meditación", lang="es",
                 category="rlv", en="Prayer and Meditation", pages=2, date="2023-09-05")
        out, _ = curate([es, en])
        self.assertEqual(len(out), 1)

    def test_not_paired(self):
        en = pdf("pdf:en", GV + "2021-12/Prayer.pdf", "Prayer and Meditation", category="news", tags=(),
                 date="2021-12-03", size=34603335)
        es = pdf("pdf:es", LV + "2023-09/Oracion.pdf", "Oración y Meditación", lang="es", category="news", tags=(),
                 en="Prayer and Meditation", date="2023-09-05", size=1537013)
        self.assertEqual(len(curate([en, es])[0]), 2, "same title but two years apart and not kit editions")
        other = pdf("pdf:x", LV + "2026-01/LV_Instagram--PC2026.pdf", "La Viña en Instagram", lang="es",
                    category="rlv", en="La Viña on Instagram")
        gv = pdf("pdf:y", GV + "2026-01/GV_Instagram--PC2026.pdf", "GV instagram Card")
        self.assertEqual(len(curate([other, gv])[0]), 2, "different titles are different documents")
        flyer = pdf("pdf:f", LV + "2026-01/Audio-form.pdf", "Descarga de Audios", lang="es", category="rlv",
                    tags=("order-form",), en="Audio Downloads")
        card = pdf("pdf:c", GV + "2026-01/Audio.pdf", "Audio Downloads", tags=("postcard",), size=5)
        self.assertEqual(len(curate([flyer, card])[0]), 2, "an order form and a postcard are not editions")

    def test_one_edition_per_language(self):
        en1 = pdf("pdf:e1", GV + "2022-08/Release_ENG.pdf", "Price Increase Release (English)", category="news",
                  tags=(), size=1, date="2022-08-29")
        en2 = pdf("pdf:e2", LV + "2022-08/Release_ENG_v2.pdf", "Price Increase Release (English)", category="news",
                  tags=(), size=2, date="2022-08-30", refs=[RLV_PAGE])
        es = pdf("pdf:s", GV + "2022-08/Release_SPAN.pdf", "Comunicado", lang="es", category="news", tags=(),
                 en="Price Increase Release (Spanish)", size=3, date="2022-08-29")
        out, _ = curate([en1, en2, es])
        self.assertEqual(len(out), 2)
        merged = [e for e in out if e["extra"].get("versions")]
        self.assertEqual(len(merged), 1)
        self.assertEqual(sorted(v["lang"] for v in merged[0]["extra"]["versions"]), ["en", "es"])
        self.assertEqual(sorted(u for e in out for u in urls_of(e)), sorted(i["url"] for i in (en1, en2, es)),
                         "nothing lost, no address twice")

    def test_whats_new_follows_the_merge(self):
        en = pdf("pdf:en", GV + "2026-01/Audio_download_GV_2026.pdf", "Audio Downloads")
        es = pdf("pdf:es", LV + "2026-01/Descarga_de_audios_2026.pdf", "Descarga de Audios", lang="es",
                 category="rlv", en="Audio Downloads")
        other = pdf("pdf:o", GV + "2026-01/Other.pdf", "Other", tags=("flyer",))
        items = [en, es, other]
        plan = [(300.0, es), (200.0, other), (100.0, en)]
        out, swaps = P.curate(items, thumb_digest=no_thumbs)
        new_plan = P.remap_plan(plan, swaps)
        self.assertEqual([(w, it["id"]) for w, it in new_plan], [(300.0, "pdf:en"), (200.0, "pdf:o")])


class Helpers(unittest.TestCase):
    def test_title_key_and_markers(self):
        self.assertEqual(P.strip_lang_marker("Sold-out book is Back in stock! (English)"), "Sold-out book is Back in stock!")
        self.assertEqual(P.strip_lang_marker("Spirituality and God-Talk (Spa.)"), "Spirituality and God-Talk")
        self.assertEqual(P.strip_lang_marker("La Espiritualidad y la Mención de Dios (Inglés)"),
                         "La Espiritualidad y la Mención de Dios")
        self.assertEqual(P.strip_lang_marker("English"), "English")
        self.assertEqual(P.title_key("2026 Catalog (Postcard)"), P.title_key("2026 Catalog Postcard"))
        self.assertEqual(P.title_key("Grapevine and La Viña Apps"), P.title_key("La Viña and Grapevine Apps"))
        self.assertEqual(P.title_key("30th Anniversary of La Viña"), P.title_key("La Viña 30 Anniversary"))
        self.assertNotEqual(P.title_key("Grapevine Books"), P.title_key("La Viña Books"))
        a = pdf("x", LV + "2021-01/A_0.PDF", "a")
        b = pdf("y", GV + "2021-01/a.pdf", "a")
        self.assertEqual(P.file_stem(a), P.file_stem(b))
        self.assertEqual(P.file_key(pdf("x", LV + "2020-08/POLI%CC%81TICA%20EDITORIAL.pdf", "a")),
                         P.file_key(pdf("y", GV + "2020-08/Política Editorial.PDF", "a")))


if __name__ == "__main__":
    unittest.main()
