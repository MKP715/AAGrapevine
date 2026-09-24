"""Weekly e-mail digest (scripts/notify/send_digest.py): the Book of the Month teaser and the
"this month's poster & toolkit" line, rendered in the --dry-run preview (HTML + plain text, both
languages) from a small data/site folder written for each test.

Run:  python -m unittest tests.test_send_digest -v   (CI: python -m unittest discover -s tests)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.notify import send_digest as D  # noqa: E402

SITE = "https://example.org/site"
GV_PRODUCT = "https://www.aagrapevine.org/store/no-matter-what-dealing-adversity-sobriety"
LV_PRODUCT = "https://www.aalavina.org/tienda/frente-frente"


def offer(pub: str, **kw) -> dict:
    gv = pub == "gv"
    title_en = "No Matter What: Dealing With Adversity & Sobriety" if gv else "Face to Face: Sponsorship in Action"
    title_es = "No importa qué: la adversidad y la sobriedad" if gv else "Frente a Frente: El apadrinamiento en acción"
    b = {
        "id": f"botm:{pub}", "pub": pub, "lang": "en" if gv else "es",
        "title": title_en if gv else title_es,
        "url": GV_PRODUCT if gv else LV_PRODUCT,
        "page_url": "https://www.aagrapevine.org/BOTM" if gv else "https://www.aalavina.org/libro-del-mes",
        "image": None, "price": 14.99, "sale_price": 11.99, "discount_pct": 20, "currency": "USD",
        "starts": "2026-09-15", "ends": "2026-10-14", "month_label": "October" if gv else "Octubre",
        "i18n": {"title": {"en": title_en, "es": title_es}},
        "machine": ["es"] if gv else ["en"],
    }
    b.update(kw)
    return b


def write_shop(d: Path, botm: list[dict]) -> None:
    (d / "shop.json").write_text(json.dumps({"updated": "2026-09-24T15:43:29Z", "botm": botm}), encoding="utf-8")


def preview(tmp_path: Path, as_of: str = "2026-09-28") -> tuple[str, str]:
    out = tmp_path / "out"
    assert D.main(["--dry-run", "--as-of", as_of, "--out-dir", str(out)]) == 0
    return (out / "digest.html").read_text(encoding="utf-8"), (out / "digest.txt").read_text(encoding="utf-8")


def halves(text: str) -> tuple[str, str]:
    """The plain text's English half and Spanish half."""
    i = text.index("Novedades de la semana")
    return text[:i], text[i:]


class SendDigestTests(unittest.TestCase):
    """Each test gets its own empty data/site folder (whatsnew/events/announcements) and SITE_URL."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)
        self.site_dir = self.tmp_path / "site"
        self.site_dir.mkdir()
        for name in ("whatsnew", "events", "announcements"):
            (self.site_dir / f"{name}.json").write_text(json.dumps({"items": []}), encoding="utf-8")
        for p in (mock.patch.object(D, "SITE_DIR", self.site_dir), mock.patch.dict(os.environ, {"SITE_URL": SITE})):
            p.start()
            self.addCleanup(p.stop)

    def test_botm_teaser_in_both_languages(self):
        write_shop(self.site_dir, [offer("gv"), offer("lv")])
        html, text = preview(self.tmp_path)
        en, es = halves(text)

        # English half: heading with the percent from the data, GV first, sale + regular price, last day, links
        assert "BOOK OF THE MONTH — 20% OFF" in en
        assert "* [Grapevine] No Matter What: Dealing With Adversity & Sobriety — $11.99 (regular $14.99) · until October 14" in en
        assert en.index("[Grapevine]") < en.index("[La Viña] Frente a Frente")   # the title it is sold under
        assert GV_PRODUCT in en and LV_PRODUCT in en
        assert f"→ Book of the Month details on our shop page: {SITE}/shop/#botm" in en
        assert f"This month's poster & toolkit (September 2026): {SITE}/monthly/2026-09/" in en

        # Spanish half: La Viña first, Spanish dates/links; each book keeps its own title
        assert "LIBRO DEL MES — 20% DE DESCUENTO" in es
        assert "* [La Viña] Frente a Frente: El apadrinamiento en acción — $11.99 (precio regular $14.99) · hasta el 14 de octubre" in es
        assert es.index("[La Viña]") < es.index("[Grapevine] No Matter What")
        assert f"{SITE}/es/shop/#botm" in es
        assert f"El cartel y el kit de este mes (septiembre de 2026): {SITE}/es/monthly/2026-09/" in es

        # HTML: the same in both halves, titles escaped, official store + our shop page linked
        assert html.count("Book of the Month — 20% off") == 1 and html.count("Libro del mes — 20% de descuento") == 1
        assert "Dealing With Adversity &amp; Sobriety" in html and "Adversity & Sobriety" not in html
        assert f'href="{GV_PRODUCT}"' in html and f'href="{LV_PRODUCT}"' in html
        assert f'href="{SITE}/shop/#botm"' in html and f'href="{SITE}/es/shop/#botm"' in html
        assert f'href="{SITE}/monthly/2026-09/"' in html and f'href="{SITE}/es/monthly/2026-09/"' in html
        assert "<strong" in html and "$11.99</strong> (regular $14.99) · until October 14" in html
        # Book titles are never machine-translated, so they bring no "translated automatically" footnote
        assert "Some titles were translated automatically." not in en
        assert "Algunos títulos se tradujeron automáticamente." not in es


    def test_ended_offer_is_left_out(self):
        write_shop(self.site_dir, [offer("gv", ends="2026-09-27"), offer("lv", discount_pct=15, sale_price=12.74)])
        html, text = preview(self.tmp_path)          # as of Sep 28: the GV offer ended the day before
        assert GV_PRODUCT not in text and GV_PRODUCT not in html
        assert "BOOK OF THE MONTH — 15% OFF" in text and "$12.74 (regular $14.99)" in text


    def test_different_percents_use_the_plain_heading(self):
        write_shop(self.site_dir, [offer("gv"), offer("lv", discount_pct=15, sale_price=12.74)])
        _, text = preview(self.tmp_path)
        en, es = halves(text)
        assert "BOOK OF THE MONTH\n-----------------\n" in en and "% OFF" not in en
        assert "LIBRO DEL MES\n-------------\n" in es


    def test_no_shop_data_keeps_the_toolkit_line(self):
        html, text = preview(self.tmp_path)          # no shop.json at all
        assert "BOOK OF THE MONTH" not in text and "Book of the Month" not in html
        assert f"{SITE}/monthly/2026-09/" in text and f"{SITE}/es/monthly/2026-09/" in text
        assert f'href="{SITE}/monthly/2026-09/"' in html

        (self.site_dir / "shop.json").write_text("{not json", encoding="utf-8")   # a broken file never stops the digest
        _, text = preview(self.tmp_path)
        assert "BOOK OF THE MONTH" not in text and "/monthly/2026-09/" in text


    def test_botm_alone_is_not_news(self):
        write_shop(self.site_dir, [offer("gv"), offer("lv")])
        data = D.collect(datetime(2026, 9, 28, 23, tzinfo=timezone.utc), 7, 30, 6)
        assert len(data["botm"]) == 2
        assert D.total_count(data) == 0         # no e-mail for an offer alone


    def test_month_is_counted_in_central_time(self):
        # 03:00 UTC on October 1 is still the evening of September 30 in Texas
        data = D.collect(datetime(2026, 10, 1, 3, tzinfo=timezone.utc), 7, 30, 6)
        assert data["month"] == "2026-09"
        links = D.Links(SITE)
        assert D.botm_block(data, "es", links)["month_url"] == f"{SITE}/es/monthly/2026-09/"
        assert D.collect(datetime(2026, 10, 1, 6, tzinfo=timezone.utc), 7, 30, 6)["month"] == "2026-10"


if __name__ == "__main__":
    unittest.main()
