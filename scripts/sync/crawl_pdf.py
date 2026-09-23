"""PDF inspection for the crawler (scripts/sync/crawl.py).

  * head_from_response() – status / size / type / Last-Modified from any HTTP response
  * download_pdf()       – streaming GET with a size cap and a time cap
  * analyze_pdf()        – pypdfium2: metadata title, page count, text of pages 1-2, and a small
                           WebP thumbnail of page 1 (Pillow)

Nothing here raises for a bad file: every function returns an error string instead.
Personal metadata (Author, Creator…) is deliberately NOT read — AA anonymity.
"""
from __future__ import annotations

import io
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

from .common import clean_text, now_iso, to_iso

THUMB_WIDTH = 360          # px — cards show ~180-240 px, so this is sharp on retina screens
THUMB_QUALITY = 65         # WebP quality → ~8-25 KB per thumbnail
THUMB_MAX_RATIO = 1.6      # very tall first pages are cropped to the top (height ≤ 1.6 × width)
TEXT_PAGES = 2             # pages of text sampled for language / title detection
TEXT_MAX_CHARS = 4000


def http_date_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return to_iso(parsedate_to_datetime(value))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def head_from_response(r) -> dict:
    """Compact record of an HTTP response for a PDF (works for HEAD and streamed GET)."""
    h = r.headers
    size = h.get("Content-Length")
    try:
        size = int(size) if size is not None else None
    except ValueError:
        size = None
    # Content-Length of a compressed response is not the file size
    if h.get("Content-Encoding", "").lower() not in ("", "identity"):
        size = None
    return {
        "status": r.status_code,
        "size": size,
        "type": (h.get("Content-Type") or "").split(";")[0].strip().lower() or None,
        "last_modified": http_date_iso(h.get("Last-Modified")),
        "final_url": r.url if getattr(r, "url", None) else None,
        "checked_at": now_iso(),
    }


def download_pdf(session, url: str, *, max_bytes: int, max_seconds: float = 90.0,
                 timeout=(10, 30)) -> tuple[dict, bytes | None, str | None, bool]:
    """GET a PDF with caps. Returns (head, data, error, final).

    `final` = True when retrying later is pointless (404/410, too large, not a PDF)."""
    r = session.get(url, stream=True, timeout=timeout,
                    headers={"Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.5"})
    if r is None:
        return {"status": None, "checked_at": now_iso()}, None, "unreachable", False
    head = head_from_response(r)
    try:
        if r.status_code in (404, 410):
            return head, None, f"http {r.status_code}", True
        if r.status_code != 200:
            return head, None, f"http {r.status_code}", False
        if head.get("type") and "html" in head["type"]:
            return head, None, "not-pdf", True
        if head.get("size") and head["size"] > max_bytes:
            return head, None, "too-large", True
        buf = io.BytesIO()
        t0 = time.monotonic()
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            buf.write(chunk)
            if buf.tell() > max_bytes:
                return head, None, "too-large", True
            if time.monotonic() - t0 > max_seconds:
                return head, None, "slow-download", False
        data = buf.getvalue()
    except Exception as e:  # connection reset mid-stream, decode errors…
        return head, None, f"download: {type(e).__name__}", False
    finally:
        try:
            r.close()
        except Exception:
            pass
    if b"%PDF" not in data[:1024]:
        return head, None, "not-pdf", True
    if not head.get("size"):
        head["size"] = len(data)
    return head, data, None, False


def analyze_pdf(data: bytes, thumb_file: Path | None) -> dict:
    """Read a PDF in memory. Returns details dict:
        {title, pages, text, error, final, thumb_written}
    `text` is the (not stored) text sample of the first pages; callers keep only derived values."""
    out: dict = {"title": None, "pages": None, "text": "", "error": None, "final": False, "thumb_written": False}
    try:
        import pypdfium2 as pdfium
    except Exception as e:  # pragma: no cover - dependency missing
        out.update(error=f"pypdfium2 missing: {e}", final=False)
        return out
    pdf = None
    try:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as e:
            msg = str(e).lower()
            out.update(error="encrypted" if "password" in msg else "invalid-pdf", final=True)
            return out
        try:
            meta = pdf.get_metadata_dict(skip_empty=True)
        except Exception:
            meta = {}
        out["title"] = clean_text(meta.get("Title") or "") or None
        out["pages"] = len(pdf)
        if not out["pages"]:
            out.update(error="no-pages", final=True)
            return out
        # --- text sample (first pages) ---------------------------------------------------
        parts = []
        for i in range(min(TEXT_PAGES, out["pages"])):
            page = textpage = None
            try:
                page = pdf[i]
                textpage = page.get_textpage()
                parts.append(textpage.get_text_range() or "")
            except Exception:
                pass
            finally:
                for obj in (textpage, page):
                    try:
                        obj and obj.close()
                    except Exception:
                        pass
        out["text"] = "\n".join(parts).replace("\r\n", "\n").replace("\r", "\n")[:TEXT_MAX_CHARS]
        # --- thumbnail of page 1 ---------------------------------------------------------
        if thumb_file is not None:
            page = bitmap = None
            try:
                page = pdf[0]
                w, h = page.get_size()
                scale = max(0.05, min(4.0, THUMB_WIDTH / max(w, 1)))
                bitmap = page.render(scale=scale)
                img = bitmap.to_pil()
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if img.height > img.width * THUMB_MAX_RATIO:
                    img = img.crop((0, 0, img.width, int(img.width * THUMB_MAX_RATIO)))
                thumb_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = thumb_file.with_name(thumb_file.name + ".tmp")   # *.tmp is git-ignored
                img.save(tmp, "WEBP", quality=THUMB_QUALITY, method=6)
                tmp.replace(thumb_file)
                out["thumb_written"] = True
            except Exception as e:
                out["error"] = f"thumb: {type(e).__name__}"
            finally:
                for obj in (bitmap, page):
                    try:
                        obj and obj.close()
                    except Exception:
                        pass
    except Exception as e:  # anything unexpected inside pdfium
        out.update(error=f"analyze: {type(e).__name__}: {e}"[:120], final=False)
    finally:
        try:
            pdf and pdf.close()
        except Exception:
            pass
    return out
