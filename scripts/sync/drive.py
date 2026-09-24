"""Google Drive → data/raw/drive.json  (the committee's upload area).

How the committee uses it (see config/site.yml → drive):

    A65_GV/                              ← drive.root_folder_id ("Anyone with the link")
      2027-2028_Panel77_GVLV/            ← a PANEL folder (name matches "Panel 77")
        reports/   notes/   slides/      ← category folders (English or Spanish names)
        flyers/    workshops/   announcements/   forms/
        photos/Spring Assembly/          ← each sub-folder of photos = an album
      2029-2030_Panel79_GVLV/            ← picked up automatically (panel >= min_panel)
      flyers/ notes/ …                   ← "loose" folders outside a panel: ignored unless
                                           drive.include_loose_folders is true

Every file becomes one Item (docs/DATA_SCHEMA.md, source "drive"):
  kind  photo | video_file | form | slides | document | announcement
  category  reports notes slides flyers photos workshops announcements forms other

Naming conventions the committee can use (all optional):
  * A date anywhere in the name sets the item date: "2027-03-14 Spring Assembly.pdf".
  * flyers/: a dated flyer becomes an event ("2027-03-14 Spring Assembly GV booth 9am @ Tyler TX.pdf").
  * announcements/: a Google Doc, .txt, .md or .docx; the file name is the headline and the
    text is the body. Add "(pinned)" / "(fijado)" to pin it, "(until 2027-02-01)" /
    "(hasta 2027-02-01)" to hide it after that date.

No API key needed: folders are read from Drive's public "embedded folder view"
(scripts/sync/drive_listing.py). If the GOOGLE_API_KEY secret exists, the Drive API is
used instead for exact dates/sizes (falls back to the HTML view on any error).

Drive is the source of truth: a file deleted/moved out of the tree disappears from the
site — but only when its folder was actually read successfully this run (a network
error or a folder that suddenly isn't public never deletes anything).

Run:  python -m scripts.sync.drive [--include-loose] [--root ID] [--dry-run] [--no-api]
"""
from __future__ import annotations

import argparse
import html
import io
import json
import re
import time
import unicodedata
import zipfile
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .common import (
    MONTHS, date_from_text, detect_lang, get_logger, load_config, load_raw, now_iso, parse_iso, run_module,
    save_raw, make_item, sort_items, truncate,
)
from .drive_listing import SHORTCUT_MIME, DriveLister, Entry, local_today

SOURCE = "drive"
log = get_logger(SOURCE)

MAX_BODY_CHARS = 12_000        # announcement body cap
MAX_TEXT_DOWNLOAD = 3_000_000  # bytes — announcement source files (.txt/.md/.docx)
MAX_ANNOUNCEMENT_FETCHES = 40  # per run (bodies are reused while a file is unchanged)
BIG_FOLDER_WARN = 500          # the HTML view may not list more than this many files

# --------------------------------------------------------------------------- categories
# First folder under the panel root → category. Matching is accent/case-insensitive and
# on whole words, so "Meeting Notes", "Fotos 2027" or "Informes-Reports" work too.
CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "reports": ["report", "reports", "informe", "informes", "reporte", "reportes"],
    "notes": ["note", "notes", "nota", "notas", "minutes", "minuta", "minutas", "acta", "actas"],
    "slides": ["slide", "slides", "presentation", "presentations", "presentacion", "presentaciones",
               "diapositiva", "diapositivas", "powerpoint", "deck", "decks"],
    "flyers": ["flyer", "flyers", "flier", "fliers", "volante", "volantes", "folleto", "folletos"],
    "photos": ["photo", "photos", "foto", "fotos", "picture", "pictures", "pics", "image", "images",
               "imagen", "imagenes", "gallery", "galeria"],
    "workshops": ["workshop", "workshops", "taller", "talleres"],
    "announcements": ["announcement", "announcements", "anuncio", "anuncios", "aviso", "avisos", "news", "noticias"],
    "forms": ["form", "forms", "formulario", "formularios", "sign up", "sign ups", "signup", "signups",
              "inscripcion", "inscripciones"],
}
_SPANISH_HINTS = {"informe", "informes", "reporte", "reportes", "nota", "notas", "minuta", "minutas", "acta",
                  "actas", "presentacion", "presentaciones", "diapositiva", "diapositivas", "volante", "volantes",
                  "folleto", "folletos", "foto", "fotos", "imagen", "imagenes", "galeria", "taller", "talleres",
                  "anuncio", "anuncios", "aviso", "avisos", "noticias", "formulario", "formularios",
                  "inscripcion", "inscripciones", "la vina", "lavina", "espanol"}

# Never published, whatever the config says: spreadsheets (form responses = personal
# data), scripts, and junk files that sync clients leave behind.
_ALWAYS_EXCLUDE_MIME = ("spreadsheet", "ms-excel", "text/csv", "tab-separated-values",
                        "google-apps.script", "google-apps.site", "google-apps.fusiontable")
_ALWAYS_EXCLUDE_NAME = re.compile(
    r"(?i)(\.(xlsx?|xlsm|csv|tsv|ods|numbers|tmp|lnk|ini|db|ds_store)$|^~\$|^\.|^thumbs\.db$|^desktop\.ini$)")

# Native Google types: url segment + export suffix for a downloadable copy.
_NATIVE = {
    "application/vnd.google-apps.document": ("document", "export?format=pdf", "Google Doc"),
    "application/vnd.google-apps.presentation": ("presentation", "export/pdf", "Google Slides"),
    "application/vnd.google-apps.drawing": ("drawings", "export/png", "Google Drawing"),
    "application/vnd.google-apps.form": ("forms", None, "Google Form"),
}
_KNOWN_EXT = ("pdf|docx?|pptx?|ppsx?|odp|odt|rtf|txt|md|key|pages|jpe?g|png|gif|webp|heic|heif|tiff?|bmp|svg|"
              "mp4|m4v|mov|avi|wmv|webm|mkv|3gp|mp3|m4a|wav|ogg|zip|epub")
_EXT_RE = re.compile(rf"(?i)\.({_KNOWN_EXT})$")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def category_for(folder_name: str) -> str | None:
    """'Meeting Notes' → notes, 'Fotos 2027' → photos, 'Misc' → None."""
    n = f" {_norm(folder_name)} "
    best: tuple[int, int, str] | None = None
    for order, (cat, syns) in enumerate(CATEGORY_SYNONYMS.items()):
        for syn in syns:
            m = re.search(rf"(?<![a-z0-9]){re.escape(syn)}(?![a-z0-9])", n)
            if m and (best is None or (m.start(), order) < best[:2]):
                best = (m.start(), order, cat)
    return best[2] if best else None


def kind_for(mime: str, name: str) -> str:
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "photo"
    if m.startswith("video/"):
        return "video_file"
    if m == "application/vnd.google-apps.form":
        return "form"
    if any(s in m for s in ("presentation", "powerpoint", "keynote")) or re.search(r"(?i)\.(pptx?|ppsx?|odp|key)$", name):
        return "slides"
    return "document"


def exclusion_reason(e: Entry, cfg: dict) -> str | None:
    mime = (e.mime or "").lower()
    for s in list(cfg.get("exclude_mime_contains") or []) + list(_ALWAYS_EXCLUDE_MIME):
        if s and str(s).lower() in mime:
            return f"type {mime}"
    low = (e.name or "").lower()
    for s in cfg.get("exclude_name_contains") or []:
        if s and str(s).lower() in low:
            return f"name contains {s!r}"
    if not e.is_folder and _ALWAYS_EXCLUDE_NAME.search(e.name or ""):
        return "file type never published"
    return None


# --------------------------------------------------------------------------- titles & dates
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_MONTH_YEAR_ONLY = re.compile(rf"(?i)(?<!\d )(?<!\d de )\b({_MONTH_ALT})\.?,?\s+(?:de\s+|del\s+)?(20\d{{2}})\b")
_COPY_SUFFIX = re.compile(r"(?i)\s*(\(\d+\)|\bcopy\b|\bcopia\b)\s*$")
_COPY_PREFIX = re.compile(r"(?i)^(copy of|copia de)\s+")
_PINNED = re.compile(r"(?i)\s*[\[(](pinned|fijado|fijo|pin)[\])]\s*|📌")
_UNTIL = re.compile(r"(?i)\s*[\[(]\s*(?:until|expires?|hasta|vence)\s*:?\s*([^)\]]+)[\])]\s*")


def strip_ext(name: str) -> str:
    return _EXT_RE.sub("", name or "").strip()


def tidy(text: str) -> str:
    """Underscores/%20 → spaces, 'GV_LV' → 'GV/LV', collapse spaces, trim separators."""
    t = text.replace("%20", " ")
    t = re.sub(r"(?i)\bGV[\s_-]+LV\b", "GV/LV", t)
    t = re.sub(r"_+", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -–—_.,·|:;")


def name_date(text: str) -> tuple[str | None, str, bool]:
    """(iso_date, text_without_date, has_explicit_day). 'March 2026 …' → ('2026-03-01', '…', False)."""
    iso, rest = date_from_text(text)
    if not iso:
        return None, text, False
    explicit = True
    if iso.endswith("-01"):
        # Was it only "Month YYYY"? Remove month-year phrases and see if a date is still found.
        iso2, _ = date_from_text(_MONTH_YEAR_ONLY.sub(" ", text))
        explicit = iso2 == iso
    return iso, rest, explicit


# Photo/video names that say nothing ("IMG_1234", "WhatsApp Image … at 6.33.16 PM", uuids).
_GENERIC_WORDS = {"img", "image", "images", "imagen", "dsc", "dscn", "dscf", "pxl", "mvimg", "vid", "video", "pano",
                  "screenshot", "screen", "shot", "captura", "de", "pantalla", "whatsapp", "photo", "foto", "picture",
                  "pic", "scan", "wa", "copy", "of", "pm", "am", "at", "a", "las", "edited", "burst", "cover",
                  "portrait", "hdr", "original", "camera", "snapchat", "signal", "telegram", "fb", "received",
                  "inbound", "unnamed", "download", "file"}


def is_generic_media_name(stem: str) -> bool:
    words = _norm(stem).split()
    # numbers, WhatsApp/camera counters and hex/uuid chunks ("85fadc60 22cf 4c00 …") carry no meaning
    rest = [w for w in words if not re.fullmatch(r"\d+|(?=[a-f]*\d)[0-9a-f]{4,}|wa\d+|img\d+|dsc[nf]?\d+|pxl\d*|p\d{6,}", w)]
    return all(w in _GENERIC_WORDS for w in rest)


def _drop_repeated_year(title: str) -> str:
    """'March 2026 Committee Meeting 2026' → 'March 2026 Committee Meeting'."""
    m = re.search(r"\s(20\d{2})$", title)
    return title[: m.start()].rstrip(" -–_.,") if m and m[1] in title[: m.start()] else title


# --------------------------------------------------------------------------- flyer events
# "9-11am", "9am-12pm", "7pm-9pm", "9 am to 3 pm", "9:30am - 11:30am", "9 a.m. - 1 p.m.", "7 a 9 pm".
# Groups: 1 h1, 2 m1, 3 am/pm of the start (optional), 4 h2, 5 m2, 6 am/pm of the end.
_TIME_RANGE = re.compile(
    r"(?i)(?<![\d:.])(\d{1,2})(?:[:.](\d{2}))?\s*(?:([ap])\.?\s*m\.?(?![a-z]))?\s*(?:[-–—]|(?<![a-z])(?:to|a|hasta)(?![a-z]))\s*"
    r"(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s*m\.?(?![a-z])")
_TIME_12 = re.compile(r"(?i)(?<![\d:.])(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s*m\.?(?![a-z])")
# "10:00-12:00", "18h00 a 20h00" (24-hour clock).
_TIME_24_RANGE = re.compile(
    r"(?i)(?<![\d:.])([01]?\d|2[0-3])[:h]([0-5]\d)\s*(?:hrs?\b|h\b)?\s*(?:[-–—]|(?<![a-z])(?:to|a|hasta)(?![a-z]))\s*"
    r"([01]?\d|2[0-3])[:h]([0-5]\d)(?!\d)(?:\s*(?:hrs?|h)\b)?")
_TIME_24 = re.compile(r"(?<![\d:.])([01]?\d|2[0-3])[:h]([0-5]\d)(?!\d)(?:\s*(?:hrs?|h)\b)?")
_PLACE = re.compile(
    r"(?i)\b(tx|texas|church|iglesia|hall|cent(?:er|re)|centro|club|clubhouse|room|hotel|inn|library|biblioteca|"
    r"park|parque|school|escuela|zoom|online|virtual|en l[ií]nea|sal[oó]n|capilla|chapel|street|st|avenue|ave|"
    r"road|rd|blvd|boulevard|hwy|highway|suite|ste|building|auditorium|convention|fellowship|alano|campus|"
    r"ranch|lodge|pavilion|pabell[oó]n|casa|district|distrito)\b")


def _hhmm(h: int, m: int, ap: str | None) -> str | None:
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap.lower() == "p" else 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


_TIME_LEAD = re.compile(r"(?i)\s*(?<![\w])(?:from|at|de|desde|a\s+las?)\s*$")


def _cut(text: str, m: re.Match) -> str:
    """Text with the matched time removed, and a dangling 'from' / 'de' / 'a las' before it."""
    return _TIME_LEAD.sub("", text[: m.start()]) + " " + text[m.end():]


def extract_time(text: str) -> tuple[str | None, str | None, str]:
    """'Assembly 9-11am' → ('09:00', '11:00', 'Assembly'). Returns (start, end, text_without_time).

    Each side of a range may carry its own am/pm ('9am-12pm'); without one the start takes
    the end's ('9-11am'), and '10-2pm' starts in the morning."""
    m = _TIME_RANGE.search(text)
    if m:
        ap_start, ap_end = m[3], m[6]
        start = _hhmm(int(m[1]), int(m[2] or 0), ap_start or ap_end)
        end = _hhmm(int(m[4]), int(m[5] or 0), ap_end)
        if not ap_start and start and end and start > end:
            start = _hhmm(int(m[1]), int(m[2] or 0), "a")
        if start:
            return start, end, _cut(text, m)
    m = _TIME_24_RANGE.search(text)
    if m:
        start, end = _hhmm(int(m[1]), int(m[2]), None), _hhmm(int(m[3]), int(m[4]), None)
        if start:
            return start, end, _cut(text, m)
    m = _TIME_12.search(text)
    if m:
        start = _hhmm(int(m[1]), int(m[2] or 0), m[3])
        if start:
            return start, None, _cut(text, m)
    m = _TIME_24.search(text)
    if m:
        start = _hhmm(int(m[1]), int(m[2]), None)
        if start:
            return start, None, _cut(text, m)
    return None, None, text


def extract_location(text: str) -> tuple[str | None, str]:
    """'Booth @ Tyler Civic Center' / 'Assembly - Tyler, TX' → (location, rest). Best effort."""
    if "@" in text:
        before, after = text.split("@", 1)
        loc = tidy(after)
        if loc:
            return loc, before
    parts = re.split(r"\s+[-–—]\s+", text)
    if len(parts) >= 2:
        last = parts[-1].strip()
        if _PLACE.search(last) or re.match(r"^\d{2,6}\s+[A-Za-z]", last):
            return tidy(last), " - ".join(parts[:-1])
    return None, text


# --------------------------------------------------------------------------- crawl
@dataclass
class Panel:
    number: int | None      # None = loose folder outside any panel
    label: str | None
    folder_id: str | None
    folder_name: str | None


LOOSE = Panel(None, None, None, None)


@dataclass
class Found:
    entry: Entry
    panel: Panel
    path: list[str]          # folder names below the panel root (loose: incl. the top folder)
    chain: list[str]         # folder ids from the root down to the parent folder
    seq: int = 0             # 1-based position among media files in the same folder


@dataclass
class Crawl:
    root_ok: bool = False
    root_error: str | None = None
    root_title: str | None = None
    found: list[Found] = field(default_factory=list)
    listed_ok: set[str] = field(default_factory=set)
    uncertain: set[str] = field(default_factory=set)   # folders we could not (fully) read
    unreadable: list[str] = field(default_factory=list)
    panels: list[dict] = field(default_factory=list)
    skipped_panels: list[str] = field(default_factory=list)
    loose_skipped: list[str] = field(default_factory=list)
    # Only the REASON is recorded for excluded files, never their names: a file marked PRIVATE
    # may carry a member's name, and data/raw + status.json are published in the public repo.
    excluded: list[str] = field(default_factory=list)
    depth_limited: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False


def panel_label(folder_name: str, number: int) -> str:
    """'2027-2028_Panel77_GVLV' → 'Panel 77 (2027–2028)'. AA panel N serves (1950+N)–(1951+N)."""
    m = re.search(r"(20\d{2})\s*[-–_/ ]\s*(20\d{2})", folder_name)
    y1, y2 = (int(m[1]), int(m[2])) if m else (1950 + number, 1951 + number)
    return f"Panel {number} ({y1}–{y2})"


def crawl(lister: DriveLister, dcfg: dict, *, root_id: str, include_loose: bool, max_depth: int,
          deadline: float, max_folders: int) -> Crawl:
    c = Crawl()
    pat = re.compile(str(dcfg.get("panel_folder_pattern") or r"Panel\s*(\d+)"), re.I)
    min_panel = int(dcfg.get("min_panel") or 0)

    root = lister.list(root_id)
    c.root_title = root.title
    if not root.ok:
        c.root_error = root.error
        return c
    c.root_ok = True
    c.listed_ok.add(root_id)

    queue: deque[tuple[str, list[str], Panel, list[str]]] = deque()
    root_files: list[Entry] = []
    for e in root.entries:
        why = exclusion_reason(e, dcfg)
        if why:
            c.excluded.append(why)
            continue
        if e.is_folder:
            m = pat.search(e.name)
            if m and m.groups() and m.group(1) and m.group(1).isdigit():
                n = int(m.group(1))
                if n >= min_panel:
                    p = Panel(n, panel_label(e.name, n), e.id, e.name)
                    c.panels.append({"panel": n, "label": p.label, "name": e.name, "id": e.id})
                    queue.append((e.id, [], p, [root_id, e.id]))
                else:
                    c.skipped_panels.append(e.name)
                continue
            if include_loose:
                queue.append((e.id, [e.name], LOOSE, [root_id, e.id]))
            else:
                c.loose_skipped.append(e.name)
        elif include_loose:
            root_files.append(e)
        else:
            c.loose_skipped.append(e.name)
    _add_files(c, root_files, LOOSE, [], [root_id])

    visited = {root_id}
    t_listed = 0
    while queue:
        if time.monotonic() > deadline or t_listed >= max_folders:
            c.truncated = True
            c.uncertain.update(q[0] for q in queue)
            c.warnings.append(f"stopped early (time/folder budget); {len(queue)} folder(s) left for the next run")
            break
        fid, path, panel, chain = queue.popleft()
        if fid in visited:               # shortcut loops / the same folder linked twice
            continue
        visited.add(fid)
        where = "/".join(([panel.folder_name] if panel.folder_name else []) + path) or fid
        res = lister.list(fid)
        t_listed += 1
        if not res.ok:
            c.uncertain.add(fid)
            c.unreadable.append(f"{where}: {res.error}")
            log.warning("folder %s unreadable: %s", where, res.error)
            continue
        c.listed_ok.add(fid)
        if len(res.entries) >= BIG_FOLDER_WARN and lister.mode == "html":
            c.warnings.append(f"{where} has {len(res.entries)} entries — add a GOOGLE_API_KEY secret "
                              "(or split the folder) so none are missed")
        files: list[Entry] = []
        for e in res.entries:
            why = exclusion_reason(e, dcfg)
            if why:
                c.excluded.append(why)
                continue
            if e.is_folder:
                if len(path) + 1 > max_depth:
                    c.depth_limited.append(f"{where}/{e.name}")
                    continue
                if e.id not in visited and e.id not in chain:
                    queue.append((e.id, path + [e.name], panel, chain + [e.id]))
            else:
                files.append(e)
        _add_files(c, files, panel, path, chain)
    return c


def _add_files(c: Crawl, files: list[Entry], panel: Panel, path: list[str], chain: list[str]) -> None:
    media = sorted((e for e in files if kind_for(e.mime, e.name) in ("photo", "video_file")),
                   key=lambda e: (e.name.lower(), e.id))
    seq = {e.id: i for i, e in enumerate(media, 1)}
    for e in files:
        c.found.append(Found(e, panel, list(path), list(chain), seq.get(e.id, 0)))


# --------------------------------------------------------------------------- items
def urls_for(fid: str, mime: str, *, has_thumb: bool = True) -> dict:
    native = _NATIVE.get(mime)
    if native:
        seg, export, _ = native
        base = f"https://docs.google.com/{seg}/d/{fid}"
        if seg == "forms":
            view = f"{base}/viewform"
            urls = {"view_url": view, "preview_url": f"{view}?embedded=true", "download_url": None}
        else:
            urls = {"view_url": f"{base}/edit?usp=sharing", "preview_url": f"{base}/preview",
                    "download_url": f"{base}/{export}"}
    else:
        urls = {"view_url": f"https://drive.google.com/file/d/{fid}/view",
                "preview_url": f"https://drive.google.com/file/d/{fid}/preview",
                "download_url": f"https://drive.google.com/uc?export=download&id={fid}"}
    urls["thumb_url"] = f"https://lh3.googleusercontent.com/d/{fid}=w600" if has_thumb else None
    urls["image_url"] = f"https://lh3.googleusercontent.com/d/{fid}=w1600" if (has_thumb and mime.startswith("image/")) else None
    return urls


def file_type_label(mime: str, name: str) -> str:
    if mime in _NATIVE:
        return _NATIVE[mime][2]
    m = re.search(r"\.([A-Za-z0-9]{2,5})$", name or "")
    if m:
        return m.group(1).upper().replace("JPG", "JPEG")
    if mime == SHORTCUT_MIME:
        return "Shortcut"
    return (mime.split("/")[-1].split(".")[-1] or "File").upper()[:12]


def lang_prior(path: list[str], name: str) -> str:
    n = f" {_norm(' '.join(path + [name]))} "
    return "es" if any(f" {h} " in n for h in _SPANISH_HINTS) else "en"


def build_item(f: Found, dcfg: dict) -> dict:
    e, panel, path = f.entry, f.panel, f.path
    kind = kind_for(e.mime, e.name)
    first = path[0] if path else None
    category = category_for(first) if first else None
    if category is None:
        if first:
            category = "other"
        else:  # file sitting directly in the panel (or root) folder
            category = {"form": "forms", "photo": "photos", "video_file": "photos", "slides": "slides"}.get(kind, "other")
    if category == "announcements":
        kind = "announcement"
    album = " / ".join(path[1:]) if (category == "photos" and len(path) > 1) else None

    # ---- title / date from the file name
    stem = _COPY_PREFIX.sub("", strip_ext(e.name))
    stem = _COPY_SUFFIX.sub("", stem)
    pinned = bool(_PINNED.search(stem))
    stem = _PINNED.sub(" ", stem)
    expires = None
    mu = _UNTIL.search(stem)
    if mu:
        expires = date_from_text(mu.group(1))[0]
        stem = stem[: mu.start()] + " " + stem[mu.end():]
    ndate, rest, explicit_day = name_date(stem)
    if ndate and not explicit_day:
        # Only "Month YYYY" — that IS the distinguishing part ("March 2026 Committee Meeting",
        # "November December 2025"), so it stays in the title; the date still sorts the item.
        title = _drop_repeated_year(tidy(stem))
    else:
        title = tidy(rest) or tidy(stem)

    generic = kind in ("photo", "video_file") and is_generic_media_name(rest)
    if generic:
        label = album or (path[-1] if path else None) or ("Video" if kind == "video_file" else "Photo")
        label = label[:1].upper() + label[1:]
        title = f"{label} #{f.seq}" if f.seq else label

    extra: dict = {
        "file_id": e.id, "mime": e.mime, "name": e.name,
        "panel": panel.number, "panel_label": panel.label,
        "path": list(path), "album": album,
        **urls_for(e.id, e.mime, has_thumb=not e.unresolved_shortcut),
        "is_image": e.mime.startswith("image/"), "is_video": e.mime.startswith("video/"),
        "is_pdf": e.mime == "application/pdf" or e.name.lower().endswith(".pdf"),
        "file_type": file_type_label(e.mime, e.name),
        "folder_id": f.chain[-1] if f.chain else None,
        "folder_url": f"https://drive.google.com/drive/folders/{f.chain[-1]}" if f.chain else None,
        "folder_chain": list(f.chain),
        "modified_text": e.modified_text,
        "size_bytes": e.size, "duration_sec": e.duration_sec,
        "shortcut_id": e.shortcut_id, "generic_name": generic or None,
    }

    # ---- date: name date > photo taken time > created (API) > modified (listing) > first_seen (merge)
    listing_date = e.created or e.modified
    if category == "flyers":
        date = listing_date or None     # a flyer's name date is the EVENT date, not when it was posted
        if ndate and explicit_day:
            t_start, t_end, no_time = extract_time(rest)
            loc, no_loc = extract_location(no_time)
            extra.update({
                "event_date": ndate, "event_title": tidy(no_loc) or title,
                "event_time": t_start, "event_end_time": t_end, "event_location": loc,
            })
        elif ndate:
            extra["event_month"] = ndate[:7]
    elif kind == "photo":
        date = ndate or (e.taken[:10] if e.taken else None) or listing_date
    else:
        date = ndate or listing_date

    if kind == "announcement":
        extra.update({"body_md": "", "expires": expires, "pinned": pinned})
    elif pinned:
        extra["pinned"] = True

    tags = []
    if panel.number:
        tags.append(f"panel-{panel.number}")
    tags.append(re.sub(r"[^a-z0-9]+", "-", extra["file_type"].lower()).strip("-"))

    url = extra["view_url"]
    image = extra["thumb_url"]
    if kind == "announcement" and not (extra["is_image"] or extra["is_pdf"]):
        image = None  # text announcements: a screenshot of a doc page is not a useful picture
    prior = lang_prior(path, e.name)
    item = make_item(
        id=f"drive:{e.id}", source=SOURCE, kind=kind, url=url, title=title,
        lang=detect_lang(title, prior) if not generic else prior,
        date=date, image=image, tags=tags, category=category,
        extra={k: v for k, v in extra.items() if v is not None or k in (
            "panel", "panel_label", "album", "download_url", "thumb_url", "image_url")},
    )
    return item


# --------------------------------------------------------------------------- announcement bodies
def _decode(data: bytes) -> str:
    """Bytes of a .txt/.md upload → str (UTF-8 with/without BOM, UTF-16 with BOM, or Windows-1252)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def docx_to_text(data: bytes) -> str:
    """Tiny .docx reader (paragraph text only) — no extra dependency."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    paras = []
    for p in re.findall(r"(?s)<w:p[ >].*?</w:p>", xml):
        p = re.sub(r"<w:tab/>", "\t", p)
        p = re.sub(r"<w:br[^>]*/>", "\n", p)
        bullet = "* " if "<w:numPr>" in p else ""
        text = html.unescape("".join(re.findall(r"(?s)<w:t(?:\s[^>]*)?>(.*?)</w:t>", p)))
        paras.append(bullet + text if text.strip() else "")
    return "\n\n".join(paras)


def normalize_body(text: str, title: str) -> str:
    """Plain text → tidy Markdown-ish body: no BOM, LF newlines, ≤1 blank line between paragraphs."""
    t = (text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    t = t.replace("​", "")
    lines = [re.sub(r"[ \t]+$", "", ln) for ln in t.split("\n")]
    out: list[str] = []
    for ln in lines:
        if not ln.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(re.sub(r"(?<=\S)[ \t]{2,}", " ", ln))
    while out and out[-1] == "":
        out.pop()
    # Google Docs bodies often repeat the headline as the first line.
    if out and _norm(out[0].lstrip("# ")) == _norm(title):
        out = out[1:]
        while out and out[0] == "":
            out.pop(0)
    body = "\n".join(out).strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS].rsplit("\n", 1)[0] + "\n\n…"
    return body


def body_url(fid: str, mime: str, name: str) -> str | None:
    """Where to download an announcement's text; None for PDFs/images (the file itself is the message)."""
    low = name.lower()
    if mime == "application/vnd.google-apps.document":
        return f"https://docs.google.com/document/d/{fid}/export?format=txt"
    if mime.startswith("text/") or low.endswith((".txt", ".md", ".markdown", ".docx")) or "wordprocessingml" in mime:
        return f"https://drive.google.com/uc?export=download&id={fid}"
    return None


def fetch_body(http, fid: str, mime: str, name: str) -> str | None:
    """Text of an announcement file, or None when it cannot be read (then the old body is kept)."""
    url = body_url(fid, mime, name)
    if url is None:
        return ""
    low = name.lower()
    r = http.get(url)
    if r is None or r.status_code != 200:
        log.warning("announcement %r: could not download text (%s)", name, getattr(r, "status_code", "no response"))
        return None
    data = r.content[:MAX_TEXT_DOWNLOAD]
    ctype = r.headers.get("Content-Type", "")
    if "text/html" in ctype and not mime.startswith("text/html"):
        # A sign-in / virus-scan page instead of the file.
        log.warning("announcement %r: Drive returned an HTML page instead of the file", name)
        return None
    try:
        if "wordprocessingml" in mime or low.endswith(".docx"):
            return docx_to_text(data)
        return _decode(data)
    except Exception as ex:
        log.warning("announcement %r: could not read text: %s", name, ex)
        return None


def fill_announcements(items: list[dict], prev_by_id: dict[str, dict], http) -> dict:
    """Download bodies for announcement items (reusing unchanged ones from the previous run)."""
    stats = Counter()
    for it in items:
        if it["kind"] != "announcement":
            continue
        ex = it["extra"]
        old = (prev_by_id.get(it["id"]) or {}).get("extra") or {}
        unchanged = old.get("body_md") and old.get("modified_text") and old.get("modified_text") == ex.get("modified_text")
        if body_url(ex["file_id"], ex.get("mime", ""), ex.get("name", "")) is None:
            ex["body_md"] = ""
            stats["file_only"] += 1
        elif unchanged:
            ex["body_md"] = old["body_md"]
            stats["reused"] += 1
        elif stats["fetched"] + stats["failed"] >= MAX_ANNOUNCEMENT_FETCHES:
            ex["body_md"] = old.get("body_md") or ""
            stats["deferred"] += 1
        else:
            text = fetch_body(http, ex["file_id"], ex.get("mime", ""), ex.get("name", ""))
            if text is None:
                ex["body_md"] = old.get("body_md") or ""
                stats["failed"] += 1
            else:
                ex["body_md"] = normalize_body(text, it["title"])
                stats["fetched"] += 1
        if ex["body_md"]:
            plain = re.sub(r"(?m)^\s*(#+|\*|-|\d+\.)\s+", "", ex["body_md"])
            it["summary"] = truncate(plain, 400)
            it["lang"] = detect_lang(it["title"] + " " + plain[:600], it["lang"] if it["lang"] != "und" else "en")
    return dict(stats)


def check_forms(items: list[dict], http) -> dict:
    """Mark Google Forms that stopped accepting responses (extra.form_closed = True), so the site
    does not advertise a closed sign-up. One HEAD per form: Google redirects closed forms to
    /closedform and members-only forms to a sign-in page."""
    stats = Counter()
    for it in items:
        if it["kind"] != "form":
            continue
        r = http.head(it["url"], allow_redirects=False)
        loc = (r.headers.get("Location") or "") if r is not None else ""
        if r is None:
            stats["unknown"] += 1
            continue
        if "closedform" in loc:
            it["extra"]["form_closed"] = True
            stats["closed"] += 1
        elif "accounts.google." in loc or "ServiceLogin" in loc:
            it["extra"]["form_closed"] = False
            it["extra"]["form_signin_required"] = True
            stats["signin_required"] += 1
        elif r.status_code == 200 or "viewform" in loc:
            it["extra"]["form_closed"] = False
            stats["open"] += 1
        else:
            stats["unknown"] += 1
    return dict(stats)


# --------------------------------------------------------------------------- merge
def merge(prev_items: list[dict], new_items: list[dict], uncertain: set[str]) -> tuple[list[dict], dict]:
    """Drive is the source of truth: keep only what we saw, except items whose folder (or an
    ancestor folder) could not be read this run — those are kept untouched."""
    now = now_iso()
    today = local_today().isoformat()
    old = {i["id"]: i for i in prev_items if i.get("id")}
    out: dict[str, dict] = {}
    added = 0
    for it in new_items:
        p = old.get(it["id"])
        if p:
            it["first_seen"] = p.get("first_seen") or now
            if not it.get("date"):
                it["date"] = p.get("date") or it["first_seen"][:10]
            pe, ne = p.get("extra") or {}, it["extra"]
            for k in ("size_bytes", "duration_sec"):     # exact values known from an API run
                if ne.get(k) is None and pe.get(k) is not None:
                    ne[k] = pe[k]
            # A form whose check could not decide today (network hiccup) keeps yesterday's answer,
            # so a closed sign-up is not advertised again for a day.
            if it.get("kind") == "form" and "form_closed" not in ne and "form_closed" in pe:
                for k in ("form_closed", "form_signin_required"):
                    if k in pe:
                        ne[k] = pe[k]
        else:
            it["first_seen"] = now
            it["date"] = it.get("date") or today
            added += 1
        # Refresh last_seen at most weekly (like merge_items) so unchanged files don't churn git.
        prev_seen = parse_iso(p.get("last_seen")) if p else None
        fresh_seen = prev_seen is not None and datetime.now(timezone.utc) - prev_seen < timedelta(days=7)
        it["last_seen"] = p["last_seen"] if fresh_seen else now
        it["status"] = "ok"
        out[it["id"]] = it
    kept = dropped = 0
    for iid, p in old.items():
        if iid in out:
            continue
        chain = (p.get("extra") or {}).get("folder_chain") or []
        if any(fid in uncertain for fid in chain):
            out[iid] = p        # its folder could not be read → cannot tell if it was deleted
            kept += 1
        else:
            dropped += 1
    return sort_items(list(out.values())), {"new": added, "removed": dropped, "kept_unverified": kept}


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m scripts.sync.drive", description=__doc__.split("\n")[0])
    ap.add_argument("--include-loose", action="store_true",
                    help="also include folders/files directly in the root (outside Panel folders)")
    ap.add_argument("--root", help="root folder id (default: config drive.root_folder_id)")
    ap.add_argument("--max-depth", type=int, default=6, help="sub-folder depth below a panel (default 6)")
    ap.add_argument("--max-minutes", type=float, default=15.0, help="time budget for listing folders")
    ap.add_argument("--max-folders", type=int, default=800, help="max folders listed per run")
    ap.add_argument("--no-api", action="store_true", help="ignore GOOGLE_API_KEY, use the public HTML view")
    ap.add_argument("--dry-run", action="store_true", help="print a summary, do not write data/raw/drive.json")
    args = ap.parse_args(argv)

    dcfg = load_config().get("drive") or {}
    root_id = (args.root or str(dcfg.get("root_folder_id") or "")).strip()
    include_loose = bool(args.include_loose or dcfg.get("include_loose_folders"))
    prev = load_raw(SOURCE)
    prev_items = prev.get("items") or []
    prev_by_id = {i["id"]: i for i in prev_items if i.get("id")}

    if not root_id:
        save_raw(SOURCE, prev_items, ok=False, error="drive.root_folder_id is not set in config/site.yml",
                 stats=prev.get("stats"))
        return

    # Shortcut ids really resolved last time → no extra request needed. A shortcut that could not
    # be resolved is stored with file_id == shortcut_id; it is NOT cached, so it is retried.
    sc_cache = {}
    for i in prev_items:
        ex = i.get("extra") or {}
        sid, fid = ex.get("shortcut_id"), ex.get("file_id")
        if sid and fid and fid != sid:
            sc_cache[sid] = fid
    lister = DriveLister(use_api=not args.no_api, shortcut_cache=sc_cache)
    log.info("listing Drive folder %s via %s (include_loose=%s)", root_id, lister.mode, include_loose)

    c = crawl(lister, dcfg, root_id=root_id, include_loose=include_loose, max_depth=args.max_depth,
              deadline=time.monotonic() + args.max_minutes * 60, max_folders=args.max_folders)

    if not c.root_ok:
        err = f"root folder unreadable: {c.root_error} — is it shared as 'Anyone with the link'?"
        log.error(err)
        if args.dry_run:
            print(err)
            return
        save_raw(SOURCE, prev_items, ok=False, error=err[:300],
                 stats={**(prev.get("stats") or {}), "requests": lister.requests_made})
        return

    items: list[dict] = []
    seen: dict[str, int] = {}
    for f in c.found:
        try:
            it = build_item(f, dcfg)
        except Exception as ex:  # one odd file must not break the run
            log.warning("skipping %r: %s: %s", f.entry.name, type(ex).__name__, ex)
            continue
        if it["id"] in seen:  # same file reachable twice (e.g. original + shortcut) → keep the original
            j = seen[it["id"]]
            if items[j]["extra"].get("shortcut_id") and not it["extra"].get("shortcut_id"):
                items[j] = it
            continue
        seen[it["id"]] = len(items)
        items.append(it)

    ann_stats = fill_announcements(items, prev_by_id, lister.http)
    form_stats = check_forms(items, lister.http)
    merged, mstats = merge(prev_items, items, c.uncertain)

    live = [i for i in merged if i.get("status") == "ok"]
    albums = Counter(i["extra"].get("album") for i in live if i["extra"].get("album"))
    warnings = list(c.warnings)
    if c.unreadable:
        warnings.append(f"{len(c.unreadable)} folder(s) could not be read — check their sharing settings")
    if not c.panels:
        warnings.append(f"no Panel folder >= {dcfg.get('min_panel')} found in the root folder")
    if lister.api_error:
        warnings.append(f"Drive API error (used the public view instead): {lister.api_error}")
    stats = {
        "mode": lister.mode if not lister.api_error else "html (api failed)",
        "root": c.root_title,
        "panels": [p["label"] for p in c.panels],
        "panel_folders": c.panels,
        "include_loose": include_loose,
        "loose_skipped": c.loose_skipped[:30],
        "skipped_panels": c.skipped_panels,
        "folders": len(c.listed_ok),
        "files": len(live),
        "fetched": len(items),
        **mstats,
        "by_category": dict(Counter(i.get("category") for i in live).most_common()),
        "by_kind": dict(Counter(i.get("kind") for i in live).most_common()),
        "albums": dict(albums.most_common(40)),
        "events_from_flyers": sum(1 for i in live if i["extra"].get("event_date")),
        "excluded": len(c.excluded),
        "excluded_by_reason": dict(Counter(c.excluded).most_common()),
        "unreadable_folders": c.unreadable[:30],
        "depth_limited": c.depth_limited[:20],
        "shortcuts_resolved": lister.html.shortcuts_resolved,
        "announcements": ann_stats,
        "forms": form_stats,
        "requests": lister.requests_made,
        "truncated": c.truncated,
        "warnings": warnings,
    }
    log.info("drive: %d items (%d new, %d removed, %d kept from unreadable folders) from %d folders; %s",
             len(live), mstats["new"], mstats["removed"], mstats["kept_unverified"], len(c.listed_ok),
             stats["by_category"])
    for w in warnings:
        log.warning(w)

    if args.dry_run:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
        for it in merged[:3]:
            print(json.dumps(it, ensure_ascii=False, indent=1))
        return
    save_raw(SOURCE, merged, ok=True, stats=stats)


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
