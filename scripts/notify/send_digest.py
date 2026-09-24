"""Weekly bilingual (English + Spanish) e-mail digest for the districts.

Reads what the daily sync already built — data/site/whatsnew.json, events.json and
announcements.json — and sends ONE clean e-mail (HTML + plain text) with:

  * the next committee meeting (Zoom link, ID, passcode)
  * new announcements
  * upcoming events (next 30 days; a monthly event from `recurring_events:` once, with its next date)
  * everything new in the last N days (config/site.yml → digest.days): magazine
    articles, podcast episodes, videos, Instagram, PDFs, committee uploads
  * a compact Book of the Month teaser (data/site/shop.json → botm: title, sale price, last
    day, the official store link + the site's /shop/#botm) and one line to this month's
    poster & toolkit (/monthly/YYYY-MM/) — never counted as news on their own
  * each section in English first, then in Spanish (titles are already translated)

Standard library only (smtplib + email.mime) so it runs anywhere without installing
the sync pipeline. PyYAML is used when available to read config/site.yml; a small
built-in reader is the fallback.

Usage (from the repo root):

    python -m scripts.notify.send_digest --dry-run          # writes .tmp/digest.html + .tmp/digest.txt
    python -m scripts.notify.send_digest                    # sends (needs the SMTP_* env vars below)
    python -m scripts.notify.send_digest --only-on-weekday  # used by the daily schedule: sends only on digest.weekday

Environment (GitHub secrets in .github/workflows/weekly-digest.yml):

    SMTP_SERVER     e.g. smtp.gmail.com                  (required to send)
    SMTP_PORT       587 (STARTTLS, default) or 465 (SSL)
    SMTP_USERNAME   the mailbox login                    (required to send)
    SMTP_PASSWORD   an APP password, not your normal one (required to send)
    DIGEST_TO       one address (e.g. a Google Group) or several, comma-separated
                    (several addresses are sent as Bcc so nobody sees the others)
    DIGEST_FROM     optional "From" address (default: SMTP_USERNAME)
    DIGEST_REPLY_TO optional reply-to (default: site.contact_email from config)
    SITE_URL        optional public site address (overrides site.url from config)

Exit codes: 0 = sent / previewed / nothing to do, 1 = sending failed, 2 = not configured.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import socket
import ssl
import time
from datetime import date, datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "site.yml"
SITE_DIR = ROOT / "data" / "site"

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Copies of three rules in scripts/sync/ (kept here so this script runs with the standard library
# alone, without importing the sync pipeline) — keep them equal:
NEW_DAYS = 14                                        # build_data.NEW_DAYS: the site's "New" badge
MULTI_DAY_MIN_HOURS = 18                             # build_data.MULTI_DAY_MIN_H: a timed event over several days
SCOPE_ORDER = ("neta65", "texas", "other", "unknown")  # geo.SCOPES: Area 65 writers first, then Texas
EVERY_ISSUE = re.compile(r"(?i)in every issue|en cada (?:edici[oó]n|n[uú]mero)")  # build_data._EVERY_ISSUE

# Brand colors (same tokens as src/assets/css/main.css, light theme).
C = {
    "paper": "#fbf8f2", "surface": "#ffffff", "surface2": "#f4efe6", "ink": "#1d1a26",
    "muted": "#57526a", "faint": "#8a8599", "line": "#e6dfd2",
    "gv": "#0a5fa8", "gv_strong": "#07457c", "gv_soft": "#e5f0fa",
    "lv": "#b8430b", "lv_soft": "#fdeee3", "grape": "#5b2a86", "grape_soft": "#f1e8f8",
    "vine": "#2f6e2c", "vine_soft": "#e7f3e3",
}

# ---------------------------------------------------------------------------- words
T = {
    "en": {
        "lang_name": "English",
        "heading": "What's new this week",
        "intro": "Here is everything new for Grapevine and La Viña from {range}, gathered automatically from "
                 "aagrapevine.org, aalavina.org, the podcast, YouTube, Instagram and our committee's Google Drive.",
        "next_meeting": "Next committee meeting",
        "join_zoom": "Join on Zoom",
        "meeting_id": "Meeting ID",
        "passcode": "Passcode",
        "meeting_note": "All AA members are welcome. No registration required.",
        "announcements": "Announcements",
        "events": "Coming up",
        "articles": "New in the magazines",
        "episodes": "Podcast episodes",
        "videos": "Videos",
        "instagram": "On Instagram",
        "pdfs": "New PDFs & service resources",
        "drive": "From the committee",
        "see_all": "See all",
        "details": "Details",
        "more": "and {n} more on the website",
        "read_more": "Read more",
        "new_posts": "{n} new posts",
        "new_post": "1 new post",
        "album": "Photos: {name}",
        "new_photos": "{n} new photos",
        "new_photo": "1 new photo",
        "nothing": "Nothing new this week — the website still has hundreds of stories, podcasts and service resources.",
        "cta_site": "Open the website",
        "cta_new": "Everything new",
        "cta_events": "Events calendar",
        "machine": "Some titles were translated automatically.",
        "online": "Online",
        "all_day": "All day",
        "monthly": "every month",
        "tentative": "details to be confirmed",
        "footer_why": "You are receiving this weekly summary from the {committee}.",
        "footer_unsub": "To stop receiving it, reply with \"unsubscribe\".",
        "footer_anon": "Feel free to forward it to your group or district — and please protect everyone's anonymity.",
        "pages": "{n} pages",
        "min": "{n} min",
        "episode": "S{s} · E{e}",
        "botm_title": "Book of the Month — {pct}% off",
        "botm_title_plain": "Book of the Month",
        "botm_regular": "(regular {price})",
        "botm_until": "until {date}",
        "botm_more": "Book of the Month details on our shop page",
        "toolkit": "This month's poster & toolkit ({month})",
    },
    "es": {
        "lang_name": "Español",
        "heading": "Novedades de la semana",
        "intro": "Aquí está todo lo nuevo de Grapevine y La Viña del {range}, recopilado automáticamente de "
                 "aagrapevine.org, aalavina.org, el podcast, YouTube, Instagram y el Google Drive de nuestro comité.",
        "next_meeting": "Próxima reunión del comité",
        "join_zoom": "Entrar por Zoom",
        "meeting_id": "ID de reunión",
        "passcode": "Código de acceso",
        "meeting_note": "Todos los miembros de AA son bienvenidos. No se requiere inscripción.",
        "announcements": "Anuncios",
        "events": "Próximos eventos",
        "articles": "Nuevo en las revistas",
        "episodes": "Episodios del podcast",
        "videos": "Videos",
        "instagram": "En Instagram",
        "pdfs": "Nuevos PDF y recursos de servicio",
        "drive": "Del comité",
        "see_all": "Ver todo",
        "details": "Detalles",
        "more": "y {n} más en el sitio web",
        "read_more": "Leer más",
        "new_posts": "{n} publicaciones nuevas",
        "new_post": "1 publicación nueva",
        "album": "Fotos: {name}",
        "new_photos": "{n} fotos nuevas",
        "new_photo": "1 foto nueva",
        "nothing": "No hay novedades esta semana — el sitio web tiene cientos de historias, podcasts y recursos de servicio.",
        "cta_site": "Abrir el sitio web",
        "cta_new": "Todas las novedades",
        "cta_events": "Calendario de eventos",
        "machine": "Algunos títulos se tradujeron automáticamente.",
        "online": "En línea",
        "all_day": "Todo el día",
        "monthly": "cada mes",
        "tentative": "detalles por confirmar",
        "footer_why": "Recibe este resumen semanal del {committee}.",
        "footer_unsub": "Para dejar de recibirlo, responda con \"cancelar\".",
        "footer_anon": "Puede reenviarlo a su grupo o distrito — y por favor proteja el anonimato de todos.",
        "pages": "{n} páginas",
        "min": "{n} min",
        "episode": "T{s} · E{e}",
        "botm_title": "Libro del mes — {pct}% de descuento",
        "botm_title_plain": "Libro del mes",
        "botm_regular": "(precio regular {price})",
        "botm_until": "hasta el {date}",
        "botm_more": "Detalles del libro del mes en nuestra página de la tienda",
        "toolkit": "El cartel y el kit de este mes ({month})",
    },
}

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
           "octubre", "noviembre", "diciembre"],
}
MONTHS_SHORT = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
}
DAYS_SHORT = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "es": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
}
DRIVE_CATEGORIES = {
    "reports": ("Reports", "Informes"), "notes": ("Notes", "Notas"), "slides": ("Slides", "Presentaciones"),
    "flyers": ("Flyers", "Volantes"), "photos": ("Photos", "Fotos"), "workshops": ("Workshops", "Talleres"),
    "announcements": ("Announcements", "Anuncios"), "forms": ("Forms", "Formularios"), "other": ("Files", "Archivos"),
}

# Section order + which site page "See all" points to.
GROUPS = [
    ("articles", "/read/"),
    ("episodes", "/listen/"),
    ("videos", "/watch/"),
    ("instagram", "/instagram/"),
    ("pdfs", "/library/"),
    ("drive", "/documents/"),
]


def log(msg: str) -> None:
    print(f"[digest] {msg}", flush=True)


# ---------------------------------------------------------------------------- config
def _mini_yaml(text: str) -> dict:
    """Fallback reader for config/site.yml when PyYAML is missing: understands the
    simple `section:` / `  key: scalar` shape used by the settings we need."""
    out: dict[str, Any] = {}
    section = None
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            section = m.group(1)
            out[section] = {} if not m.group(2) else _scalar(m.group(2))
            continue
        m = re.match(r"^  ([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and section and isinstance(out.get(section), dict) and m.group(2):
            out[section][m.group(1)] = _scalar(m.group(2))
    return out


def _scalar(v: str) -> Any:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def load_config() -> dict:
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError as e:
        log(f"could not read {CONFIG_PATH}: {e}")
        return {}
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        return _mini_yaml(text)
    except Exception as e:  # malformed YAML → use what the simple reader can get
        log(f"config/site.yml could not be parsed fully ({e}); using the simple reader")
        return _mini_yaml(text)


def load_items(name: str) -> list[dict]:
    p = SITE_DIR / f"{name}.json"
    shown = f"data/site/{name}.json"
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") if isinstance(data, dict) else None
        return [i for i in (items or []) if isinstance(i, dict)]
    except FileNotFoundError:
        log(f"{shown} not found — section will be empty")
    except Exception as e:  # corrupt JSON must never stop the digest
        log(f"could not read {shown}: {e}")
    return []


def load_botm() -> list[dict]:
    """The Book of the Month offers of data/site/shop.json (`botm`, 0–2 entries, Grapevine first —
    docs/DATA_SCHEMA.md → shop.json). That file has no `items`; a missing or broken file = no offers."""
    try:
        with open(SITE_DIR / "shop.json", encoding="utf-8") as f:
            data = json.load(f)
        botm = data.get("botm") if isinstance(data, dict) else None
        return [b for b in (botm or []) if isinstance(b, dict)]
    except FileNotFoundError:
        return []
    except Exception as e:  # corrupt JSON must never stop the digest
        log(f"could not read data/site/shop.json: {e}")
        return []


# ---------------------------------------------------------------------------- time
try:
    from zoneinfo import ZoneInfo

    TZ: Any = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover — no tz database (rare on Windows without tzdata)
    TZ = None


def to_central(dt: datetime) -> datetime:
    if TZ is not None:
        return dt.astimezone(TZ)
    # Fallback: US DST rule (2nd Sunday of March → 1st Sunday of November, 2 AM local).
    y = dt.year
    mar = date(y, 3, 8 + (6 - date(y, 3, 8).weekday()) % 7)
    nov = date(y, 11, 1 + (6 - date(y, 11, 1).weekday()) % 7)
    dst_start = datetime(y, 3, mar.day, 8, tzinfo=timezone.utc)
    dst_end = datetime(y, 11, nov.day, 7, tzinfo=timezone.utc)
    off = -5 if dst_start <= dt.astimezone(timezone.utc) < dst_end else -6
    return dt.astimezone(timezone(timedelta(hours=off), "CDT" if off == -5 else "CST"))


def parse_dt(v: Any) -> datetime | None:
    """ISO string / date-only → aware UTC datetime (date-only = noon UTC, like the site)."""
    if not v or not isinstance(v, str):
        return None
    s = v.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return datetime.fromisoformat(s + "T12:00:00+00:00")
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_date_only(v: Any) -> bool:
    return isinstance(v, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()))


def end_of_day(v: str) -> datetime:
    """Date-only 'YYYY-MM-DD' → 23:59 Central time that day (as build_data.event_end_ts does),
    so an all-day event stays in the digest for the whole of its last day."""
    d = date.fromisoformat(v.strip())
    if TZ is not None:
        return datetime(d.year, d.month, d.day, 23, 59, tzinfo=TZ)
    off = to_central(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)).utcoffset() or timedelta(hours=-6)
    return datetime(d.year, d.month, d.day, 23, 59, tzinfo=timezone(off))


def fmt_day(dt: datetime, lang: str, weekday: bool = True, year: bool = False) -> str:
    d = to_central(dt)
    if lang == "es":
        s = f"{d.day} de {MONTHS_SHORT['es'][d.month - 1]}."
        if weekday:
            s = f"{DAYS_SHORT['es'][d.weekday()]}., {s}"
        return s + (f" de {d.year}" if year else "")
    s = f"{MONTHS_SHORT['en'][d.month - 1]} {d.day}"
    if weekday:
        s = f"{DAYS_SHORT['en'][d.weekday()]}, {s}"
    return s + (f", {d.year}" if year else "")


def fmt_time(dt: datetime, lang: str) -> str:
    d = to_central(dt)
    tz = d.tzname() or "CT"
    if lang == "es":
        return f"{d.hour}:{d.minute:02d} {tz}"
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'} {tz}"


def fmt_range(start: datetime, end: datetime, lang: str) -> str:
    a, b = to_central(start), to_central(end)
    if lang == "es":
        if a.month == b.month and a.year == b.year:
            return f"{a.day} al {b.day} de {MONTHS['es'][b.month - 1]} de {b.year}"
        return f"{a.day} de {MONTHS['es'][a.month - 1]} al {b.day} de {MONTHS['es'][b.month - 1]} de {b.year}"
    if a.month == b.month and a.year == b.year:
        return f"{MONTHS['en'][a.month - 1]} {a.day}–{b.day}, {b.year}"
    return f"{MONTHS['en'][a.month - 1]} {a.day} – {MONTHS['en'][b.month - 1]} {b.day}, {b.year}"


# ---------------------------------------------------------------------------- item helpers
def tx(item: dict, field: str, lang: str) -> str:
    """Field in the requested language (build_data's i18n block), else the original."""
    i18n = (item.get("i18n") or {}).get(field) or {}
    val = i18n.get(lang) or i18n.get(item.get("lang") or "") or item.get(field)
    if val is None and field == "body_md":
        val = (item.get("extra") or {}).get("body_md")
    return str(val or "").strip()


def is_machine(item: dict, lang: str) -> bool:
    return lang in (item.get("machine") or [])


def effective_date(item: dict) -> datetime | None:
    """When did this item become *news*?  min(publish date, first seen):
       * brand-new items → their publish date
       * next month's magazine issue (future-dated) → the day we first saw it
       * very old PDFs discovered by the first big crawl → their old date (so they don't flood the digest)
    """
    ds = [d for d in (parse_dt(item.get("date")), parse_dt(item.get("first_seen"))) if d]
    return min(ds) if ds else None


def still_news(item: dict, now: datetime) -> bool:
    """Does the website still mark this item "New"? build_data's is_new flag decides (it already
    leaves out everything the very first harvest found); data without the flag falls back to the
    What's New date being less than NEW_DAYS old."""
    if "is_new" in item:
        return bool(item.get("is_new"))
    wn = parse_dt(item.get("wn_date")) or effective_date(item)
    return bool(wn and wn <= now and now - wn < timedelta(days=NEW_DAYS))


def is_department(item: dict) -> bool:
    """An "In Every Issue" page (AA News, Dear Grapevine, Discussion Topic …), not a member's story."""
    ex = item.get("extra") or {}
    return ex.get("department") is True or bool(EVERY_ISSUE.search(str(ex.get("section") or "")))


def story_order(item: dict, now: datetime) -> tuple:
    """Magazine stories: members' stories before "In Every Issue" pages, Area 65 writers first and
    then the rest of Texas (like the site's published-writers spotlight), then newest first."""
    scope = ((item.get("extra") or {}).get("geo") or {}).get("scope")
    rank = SCOPE_ORDER.index(scope) if scope in SCOPE_ORDER else len(SCOPE_ORDER) - 1
    return is_department(item), rank, -(effective_date(item) or now).timestamp()


# build_data.committee_meetings writes the meeting's summary as "<this intro> <meeting.note>".
MEETING_INTRO = {"en": re.compile(r"^Our monthly committee meeting on [^.]*\.\s*"),
                 "es": re.compile(r"^Nuestra reunión mensual del comité por [^.]*\.\s*")}


def _one_line(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def meeting_note(cfg: dict, meeting: dict, lang: str) -> str:
    """The line under the next meeting's date: the chair's own note from config/site.yml
    (meeting.note / note_es) — the same text the website shows for the meeting. Without note_es the
    Spanish comes from the meeting event, where the site's daily build already translated the note.
    An empty note shows nothing (like the site); unreadable settings → the standard sentence."""
    mt = cfg.get("meeting")
    if not isinstance(mt, dict):
        return T[lang]["meeting_note"]
    note = _one_line(mt.get("note"))
    if lang == "en" or not note:
        return note
    es = _one_line(mt.get("note_es"))
    if es:
        return es
    summary = (meeting.get("i18n") or {}).get("summary") or {}
    built_en = MEETING_INTRO["en"].sub("", _one_line(summary.get("en")))
    built_es = MEETING_INTRO["es"].sub("", _one_line(summary.get("es")))
    # only when the site was built from the same note (an edit made today is not built yet)
    return built_es if built_es and built_en == note else note


class Links:
    def __init__(self, site_url: str):
        self.base = site_url.rstrip("/")

    def page(self, path: str, lang: str) -> str:
        path = path if path.startswith("/") else "/" + path
        return self.base + ("/es" if lang == "es" else "") + path

    def item(self, item: dict, lang: str, fallback_page: str) -> str:
        ex = item.get("extra") or {}
        url = item.get("url") or ex.get("view_url") or ""
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("/"):
            return self.page(url, lang)
        return self.page(fallback_page, lang)


def item_label(item: dict, lang: str) -> tuple[str, str, str]:
    """(label, text color, background) for the little pill in front of an item."""
    src, kind = item.get("source"), item.get("kind")
    if kind == "article":
        return ("La Viña", C["lv"], C["lv_soft"]) if item.get("category") == "lv" or src == "lavina" \
            else ("Grapevine", C["gv"], C["gv_soft"])
    if kind == "episode":
        # Two shows (config sources.podcasts): the magazine's podcast ("gv") and the
        # Grapevine Weekly Open AA Meeting ("wo"), which gets its own pill.
        show = item.get("category") or (item.get("extra") or {}).get("show")
        return ("Weekly Open" if show == "wo" else "Podcast"), C["grape"], C["grape_soft"]
    if kind in ("video", "video_file"):
        return "Video", C["grape"], C["grape_soft"]
    if kind == "post":
        return ("La Viña", C["lv"], C["lv_soft"]) if item.get("category") == "lv" else ("Grapevine", C["gv"], C["gv_soft"])
    if kind == "pdf":
        host = (item.get("extra") or {}).get("host") or ""
        return ("PDF · La Viña", C["lv"], C["lv_soft"]) if "lavina" in host else ("PDF · Grapevine", C["gv"], C["gv_soft"])
    if src == "drive":
        en, es = DRIVE_CATEGORIES.get(item.get("category") or "other", DRIVE_CATEGORIES["other"])
        return (es if lang == "es" else en), C["vine"], C["vine_soft"]
    return "", C["muted"], C["surface2"]


def localize_months(label: str, lang: str) -> str:
    """'October 2026' ⇄ 'Octubre 2026' so issue labels read naturally in each section."""
    src, dst = ("en", "es") if lang == "es" else ("es", "en")
    for a, b in zip(MONTHS[src], MONTHS[dst]):
        label = re.sub(rf"\b{a}\b", b.capitalize(), label, flags=re.I)
    return label


def tx_extra(item: dict, field: str, lang: str) -> str:
    """An `extra` field (issue_label, topic, section, album …) in the requested language: build_data's
    i18n copy when it has that language, else the original value."""
    val = ((item.get("i18n") or {}).get(field) or {}).get(lang) or (item.get("extra") or {}).get(field)
    return str(val or "").strip()


def item_meta(item: dict, lang: str) -> str:
    ex = item.get("extra") or {}
    kind = item.get("kind")
    parts: list[str] = []
    d = parse_dt(item.get("date"))
    if kind == "article":
        if ex.get("issue_label"):
            label = ((item.get("i18n") or {}).get("issue_label") or {}).get(lang)
            parts.append(str(label).strip() if label else localize_months(str(ex["issue_label"]), lang))
        topic_field = "topic" if ex.get("topic") else "section" if ex.get("section") else None
        if topic_field:
            parts.append(tx_extra(item, topic_field, lang))
    elif kind == "episode":
        if ex.get("season") and ex.get("episode"):
            parts.append(T[lang]["episode"].format(s=ex["season"], e=ex["episode"]))
        if ex.get("duration_sec"):
            parts.append(T[lang]["min"].format(n=max(1, round(int(ex["duration_sec"]) / 60))))
        if d:
            parts.append(fmt_day(d, lang, weekday=False))
    elif kind == "video":
        if d:
            parts.append(fmt_day(d, lang, weekday=False))
        if ex.get("duration_sec"):
            parts.append(T[lang]["min"].format(n=max(1, round(int(ex["duration_sec"]) / 60))))
    elif kind == "pdf":
        if ex.get("pages"):
            parts.append(T[lang]["pages"].format(n=ex["pages"]))
        host = str(ex.get("host") or "").replace("www.", "")
        if host:
            parts.append(host)
    else:
        if d:
            parts.append(fmt_day(d, lang, weekday=False))
    return " · ".join(p for p in parts if p)


# ---------------------------------------------------------------------------- collect
def md_to_text(s: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\((\S+?)\)", r"\1 (\2)", s)
    s = re.sub(r"(\*\*|__|\*|_|`)", "", s)
    s = re.sub(r"^#+\s*", "", s, flags=re.M)
    return re.sub(r"[ \t]+", " ", s).strip()


def md_to_html(s: str, link_color: str) -> str:
    """Tiny, safe Markdown subset for announcement bodies: paragraphs, line breaks,
    **bold**, *italic*, [links](https://…). Everything else is escaped."""
    out = []
    for para in re.split(r"\n\s*\n", s.strip()):
        t = html.escape(para.strip())
        t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)",
                   lambda m: f'<a href="{m.group(2)}" style="color:{link_color};">{m.group(1)}</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
        t = re.sub(r"^#+\s*", "", t)
        out.append(t.replace("\n", "<br>"))
    return "".join(f'<p style="margin:0 0 10px;">{p}</p>' for p in out if p)


def shorten(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(",;:.- ") + "…"


def collect(now: datetime, days: int, event_days: int, max_per: int) -> dict:
    start = now - timedelta(days=days)
    data: dict[str, Any] = {"start": start, "end": now, "groups": {}, "announcements": [], "events": [],
                            "meeting": None, "machine": {"en": False, "es": False}}

    hi = now + timedelta(hours=1)

    def in_window(it: dict) -> bool:
        """New this week when EITHER
        * it became news in the window (min(date, first_seen), see effective_date), OR
        * we first found it in the window and the website still marks it "New" — something dated
          before the previous digest but only found after it (a PDF dated last month, a La Viña
          issue dated the 1st, an announcement whose name starts with an earlier date) would
          otherwise miss every digest.
        An item's first_seen falls in exactly one weekly window, so nothing is listed twice."""
        d = effective_date(it)
        if d and start <= d <= hi:
            return True
        fs = parse_dt(it.get("first_seen"))
        return bool(fs and start <= fs <= hi and still_news(it, now))

    # ---- new content (whatsnew = newest items across every source)
    seen: set[str] = set()
    buckets: dict[str, list[dict]] = {g: [] for g, _ in GROUPS}
    for it in load_items("whatsnew"):
        iid = it.get("id") or it.get("url")
        if not iid or iid in seen or it.get("status") == "gone" or not in_window(it):
            continue
        seen.add(iid)
        kind, src = it.get("kind"), it.get("source")
        if kind == "article":
            buckets["articles"].append(it)
        elif kind == "episode":
            buckets["episodes"].append(it)
        elif kind == "video":
            buckets["videos"].append(it)
        elif kind == "post":
            buckets["instagram"].append(it)
        elif kind == "pdf":
            buckets["pdfs"].append(it)
        elif src == "drive" and kind in ("document", "slides", "photo", "video_file", "form"):
            buckets["drive"].append(it)
        # announcements/events come from their own files below
    for g, items in buckets.items():
        if g == "articles":   # members' stories (Area 65, then Texas) before "In Every Issue" pages
            items.sort(key=lambda i: story_order(i, now))
        else:
            items.sort(key=lambda i: effective_date(i) or now, reverse=True)
        data["groups"][g] = items

    # ---- announcements (new in the window, not expired)
    today = to_central(now).date().isoformat()
    for it in load_items("announcements"):
        ex = it.get("extra") or {}
        if ex.get("expires") and str(ex["expires"])[:10] < today:
            continue
        if in_window(it):
            data["announcements"].append(it)
    data["announcements"].sort(key=lambda i: (not (i.get("extra") or {}).get("pinned"), -(effective_date(i) or now).timestamp()))

    # ---- events: next committee meeting (callout) + other upcoming events
    horizon = now + timedelta(days=event_days)
    meeting_horizon = now + timedelta(days=max(event_days, 45))  # monthly meeting: always show the next one
    upcoming = []
    for it in load_items("events"):
        ex = it.get("extra") or {}
        start_raw, end_raw = ex.get("start") or it.get("date"), ex.get("end")
        st = parse_dt(start_raw)
        en = parse_dt(end_raw) if end_raw else None
        if not st:
            continue
        # When the event is over. All-day (date-only) events stay until 23:59 Central on their
        # last day, like the website; a timed event without an end counts as 2 hours long.
        if en is not None:
            last = end_of_day(end_raw) if is_date_only(end_raw) else en
        elif is_date_only(start_raw):
            last = end_of_day(start_raw)
        else:
            last = st + timedelta(hours=2)
        if last < now or st > (meeting_horizon if it.get("category") == "committee" else horizon):
            continue
        upcoming.append((st, it))
    upcoming.sort(key=lambda x: x[0])
    series_seen: set[str] = set()
    for st, it in upcoming:
        if it.get("category") == "committee":  # auto-generated monthly committee meeting
            if data["meeting"] is None:
                data["meeting"] = it
            continue
        if is_recurring(it):  # a monthly event from config/site.yml recurring_events: only its next date
            series = str((it.get("extra") or {}).get("series") or it.get("id"))
            if series in series_seen:
                continue
            series_seen.add(series)
        data["events"].append(it)

    # ---- Book of the Month (a compact teaser: the prices and dates live on the site's /shop/#botm)
    # An offer whose last day has passed (Central time) is left out, like the website.
    data["botm"] = [b for b in load_botm()
                    if b.get("url") and isinstance(b.get("sale_price"), (int, float))
                    and not (is_date_only(b.get("ends")) and str(b["ends"]) < today)]
    # This month's poster & toolkit page: /monthly/YYYY-MM/ (Central time)
    data["month"] = to_central(now).strftime("%Y-%m")

    # which languages carry machine translations (for the small footnote)
    every = ([i for g in data["groups"].values() for i in g[:max_per]] + data["announcements"] + data["events"]
             + data["botm"])
    for lang in ("en", "es"):
        data["machine"][lang] = any(is_machine(i, lang) for i in every)
    return data


def is_recurring(item: dict) -> bool:
    """A date of a monthly event from config/site.yml `recurring_events:` (build_data.recurring_events)."""
    return item.get("category") == "recurring"


def total_count(data: dict) -> int:
    """How much the digest has to tell — nothing means no e-mail. A recurring event (the monthly booth)
    comes round every month, like the committee meeting, so it is listed but does not count: on its own
    it never turns a quiet week into an e-mail."""
    return (sum(len(v) for v in data["groups"].values()) + len(data["announcements"])
            + sum(1 for e in data["events"] if not is_recurring(e)))


# ---------------------------------------------------------------------------- rows (shared by HTML + text)
def photo_count(item: dict) -> int:
    """How many photos a What's New item stands for (a same-day album group has extra.count)."""
    try:
        return max(1, int((item.get("extra") or {}).get("count") or 1))
    except (TypeError, ValueError):
        return 1


def build_rows(group: str, items: list[dict], lang: str, links: Links, page: str, max_per: int) -> tuple[list[dict], int]:
    """Turn items into display rows. Instagram collapses to one row per account and
    Drive photos to one row per album, so a big upload doesn't flood the e-mail."""
    t = T[lang]
    rows: list[dict] = []
    if group == "instagram":
        by_acct: dict[str, list[dict]] = {}
        for it in items:
            ex = it.get("extra") or {}
            by_acct.setdefault(ex.get("username") or it.get("category") or "instagram", []).append(it)
        for user, its in by_acct.items():
            label, fg, bg = item_label(its[0], lang)
            n = len(its)
            rows.append({"label": label, "fg": fg, "bg": bg, "title": f"@{user}",
                         "url": links.page(page, lang),
                         "meta": t["new_post"] if n == 1 else t["new_posts"].format(n=n)})
        return rows, 0
    photos: dict[str, list[dict]] = {}
    for it in items:
        ex = it.get("extra") or {}
        if group == "drive" and (it.get("kind") == "photo" or ex.get("is_image")):
            photos.setdefault(ex.get("album") or DRIVE_CATEGORIES["photos"][1 if lang == "es" else 0], []).append(it)
            continue
        label, fg, bg = item_label(it, lang)
        rows.append({"label": label, "fg": fg, "bg": bg, "title": tx(it, "title", lang) or it.get("title") or "",
                     "url": links.item(it, lang, page), "meta": item_meta(it, lang)})
    for album, its in photos.items():
        # What's New already merges one album's photos from one day into a single item
        # ("5 new photos in …", extra.count = 5): count photos, not items.
        n = sum(photo_count(it) for it in its)
        # the album name in this language (a photo group carries i18n.album), else as written in Drive
        names = [tx_extra(it, "album", lang) for it in its if (it.get("i18n") or {}).get("album")]
        name = next((v for v in names if v), album)
        rows.append({"label": DRIVE_CATEGORIES["photos"][1 if lang == "es" else 0], "fg": C["vine"], "bg": C["vine_soft"],
                     "title": t["album"].format(name=name), "url": links.page("/photos/", lang),
                     "meta": t["new_photo"] if n == 1 else t["new_photos"].format(n=n)})
    extra = max(0, len(rows) - max_per)
    return rows[:max_per], extra


def event_row(it: dict, lang: str, links: Links) -> dict:
    t = T[lang]
    ex = it.get("extra") or {}
    raw_start, raw_end = ex.get("start") or it.get("date"), ex.get("end")
    st = parse_dt(raw_start)
    en = parse_dt(raw_end) if raw_end else None
    timed = bool(st) and not is_date_only(raw_start) and not ex.get("all_day")
    when = fmt_day(st, lang) if st else ""
    if timed:
        when += " · " + fmt_time(st, lang)
    # An event of several days (an Area assembly, Fri–Sun): "Fri, Mar 19 – Sun, Mar 21", like the website —
    # the same rule as the pages (committee.js multiDay, community.js isMultiDay): all-day over several
    # dates, or a timed event longer than 18 hours; a timed one that only runs past midnight is one day.
    if st and en:
        last = en if is_date_only(raw_end) else en - timedelta(microseconds=1)    # an end at 00:00 is the day before
        if to_central(last).date() > to_central(st).date() and (
                not timed or (en - st).total_seconds() > MULTI_DAY_MIN_HOURS * 3600):
            when += " – " + fmt_day(last, lang)
    if is_recurring(it):
        when += " · " + t["monthly"]
    if ex.get("tentative"):          # content/events `tentative: true`: not final yet
        when += " · " + t["tentative"]
    # The place in this language (content/events `location_es` → i18n.location), as written otherwise.
    where = tx_extra(it, "location", lang) or (t["online"] if ex.get("online_url") else "")
    url = it.get("url") or ex.get("flyer_url") or ex.get("online_url") or ""
    url = links.item({**it, "url": url}, lang, "/events/")
    return {"title": tx(it, "title", lang), "when": when, "where": where, "url": url}


def fmt_money(v: Any) -> str:
    """11.99 → "$11.99" (the stores list prices in USD; the website shows them the same way)."""
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def fmt_month_day(ymd: str, lang: str) -> str:
    """'2026-10-14' → "October 14" / "14 de octubre"."""
    d = date.fromisoformat(ymd)
    return f"{d.day} de {MONTHS['es'][d.month - 1]}" if lang == "es" else f"{MONTHS['en'][d.month - 1]} {d.day}"


def botm_block(data: dict, lang: str, links: Links) -> dict:
    """The Book of the Month teaser + this month's toolkit link, shared by the HTML and the text:
    {title, rows: [{label, fg, bg, title, url, price, regular, until}], more_url, month_label, month_url}.
    The magazine of the section's language comes first (La Viña in the Spanish half)."""
    t = T[lang]
    offers = sorted(data.get("botm") or [], key=lambda b: (b.get("pub") == "lv") != (lang == "es"))
    pcts = {b.get("discount_pct") for b in offers if b.get("discount_pct")}
    rows = []
    for b in offers:
        lv = b.get("pub") == "lv"
        price, sale = b.get("price"), b.get("sale_price")
        regular = t["botm_regular"].format(price=fmt_money(price)) if isinstance(price, (int, float)) and price > sale else ""
        rows.append({"label": "La Viña" if lv else "Grapevine", "fg": C["lv"] if lv else C["gv"],
                     "bg": C["lv_soft"] if lv else C["gv_soft"], "title": tx(b, "title", lang),
                     "url": b["url"], "price": fmt_money(sale), "regular": regular,
                     "until": t["botm_until"].format(date=fmt_month_day(b["ends"], lang)) if is_date_only(b.get("ends")) else ""})
    ym = data.get("month") or to_central(data["end"]).strftime("%Y-%m")
    y, m = (int(x) for x in ym.split("-"))
    month_label = f"{MONTHS['es'][m - 1]} de {y}" if lang == "es" else f"{MONTHS['en'][m - 1]} {y}"
    return {"title": t["botm_title"].format(pct=pcts.pop()) if len(pcts) == 1 else t["botm_title_plain"],
            "rows": rows, "more_url": links.page("/shop/", lang) + "#botm",
            "month_label": t["toolkit"].format(month=month_label), "month_url": links.page(f"/monthly/{ym}/", lang)}


# ---------------------------------------------------------------------------- HTML
def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def render_html(data: dict, cfg: dict, links: Links, max_per: int, subject: str) -> str:
    site = cfg.get("site") or {}
    title = site.get("title") or "Grapevine / La Viña"
    logo = links.base + "/assets/img/logo-180x180.png"
    short_names = {"articles": "magazine stories", "episodes": "podcast episodes", "videos": "videos",
                   "instagram": "Instagram posts", "pdfs": "PDFs", "drive": "committee files"}
    teaser = [tx(i, "title", "en") for i in data["announcements"][:1]]
    teaser += [f"{len(v)} {short_names[g]}" for g, v in data["groups"].items() if v]
    preheader = shorten(" · ".join(teaser), 140) or T["en"]["heading"]

    sections = []
    for lang in ("en", "es"):
        sections.append(render_lang_html(lang, data, cfg, links, max_per))

    divider = f'<tr><td style="padding:0 32px;"><div style="border-top:2px dashed {C["line"]};height:1px;line-height:1px;">&nbsp;</div></td></tr>'
    committee = site.get("committee") or title
    committee_es = site.get("committee_es") or committee
    contact = site.get("contact_email") or ""
    footer = f"""
<tr><td style="padding:24px 32px 28px;background:{C['surface2']};border-radius:0 0 12px 12px;font-size:12px;line-height:1.6;color:{C['muted']};">
  <p style="margin:0 0 8px;">{_esc(T['en']['footer_why'].format(committee=committee))} {_esc(T['en']['footer_anon'])}<br>
  {_esc(T['es']['footer_why'].format(committee=committee_es))} {_esc(T['es']['footer_anon'])}</p>
  <p style="margin:0 0 8px;">{_esc(T['en']['footer_unsub'])} · {_esc(T['es']['footer_unsub'])}</p>
  <p style="margin:0;"><a href="{_esc(links.page('/', 'en'))}" style="color:{C['gv']};">{_esc(links.base.split('://')[-1])}</a>
  {f' · <a href="mailto:{_esc(contact)}" style="color:{C["gv"]};">{_esc(contact)}</a>' if contact else ''}</p>
</td></tr>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:0;background:{C['paper']};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{_esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{C['paper']};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
  style="width:100%;max-width:600px;background:{C['surface']};border:1px solid {C['line']};border-radius:12px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:{C['ink']};">
<tr><td style="padding:22px 32px;background:{C['gv_strong']};border-radius:12px 12px 0 0;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="padding-right:14px;vertical-align:middle;"><img src="{_esc(logo)}" width="48" height="48" alt="" style="display:block;border:0;border-radius:10px;background:#ffffff;"></td>
    <td style="vertical-align:middle;">
      <div style="font-family:Georgia,'Times New Roman',serif;font-size:22px;line-height:1.2;color:#ffffff;font-weight:bold;">{_esc(title)}</div>
      <div style="font-size:13px;color:#cfe3f6;margin-top:3px;">{_esc(committee)}</div>
    </td></tr></table>
</td></tr>
<tr><td style="padding:12px 32px;background:{C['gv_soft']};font-size:13px;color:{C['gv_strong']};">
  English first · <strong>Versión en español más abajo</strong>
</td></tr>
{sections[0]}
{divider}
{sections[1]}
{footer}
</table>
</td></tr></table>
</body>
</html>
"""


def render_lang_html(lang: str, data: dict, cfg: dict, links: Links, max_per: int) -> str:
    t = T[lang]
    meeting_cfg = cfg.get("meeting") or {}
    rng = fmt_range(data["start"], data["end"], lang)
    parts: list[str] = []
    h2 = f"font-family:Georgia,'Times New Roman',serif;font-size:24px;line-height:1.25;margin:0 0 6px;color:{C['ink']};"
    h3 = (f"font-family:Georgia,'Times New Roman',serif;font-size:17px;margin:0 0 10px;color:{C['ink']};"
          f"border-left:4px solid {{color}};padding-left:10px;")
    parts.append(f"""<tr><td style="padding:28px 32px 8px;">
  <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{C['faint']};font-weight:bold;">{_esc(t['lang_name'])}</div>
  <h2 style="{h2}">{_esc(t['heading'])}</h2>
  <p style="margin:0;font-size:14px;line-height:1.6;color:{C['muted']};">{_esc(t['intro'].format(range=rng))}</p>
</td></tr>""")

    # ---- next committee meeting
    m = data.get("meeting")
    if m:
        ex = m.get("extra") or {}
        st = parse_dt(ex.get("start") or m.get("date"))
        zoom = ex.get("online_url") or meeting_cfg.get("zoom_url") or ""
        when = f"{fmt_day(st, lang, year=True)} · {fmt_time(st, lang)}" if st else ""
        details = []
        if meeting_cfg.get("meeting_id"):
            details.append(f"{t['meeting_id']}: <strong>{_esc(meeting_cfg['meeting_id'])}</strong>")
        if meeting_cfg.get("passcode"):
            details.append(f"{t['passcode']}: <strong>{_esc(meeting_cfg['passcode'])}</strong>")
        btn = (f'<a href="{_esc(zoom)}" style="display:inline-block;background:{C["gv"]};color:#ffffff;text-decoration:none;'
               f'font-weight:bold;font-size:14px;padding:10px 18px;border-radius:8px;">{_esc(t["join_zoom"])}</a>') if zoom else ""
        info = " · ".join(details)
        note = meeting_note(cfg, m, lang)
        if note:
            info = f"{info}<br>{_esc(note)}" if info else _esc(note)
        info_html = f'<div style="font-size:13px;color:{C["muted"]};margin-bottom:12px;">{info}</div>' if info \
            else '<div style="height:10px;line-height:10px;">&nbsp;</div>'
        parts.append(f"""<tr><td style="padding:16px 32px 4px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{C['gv_soft']};border-radius:10px;">
  <tr><td style="padding:16px 18px;">
    <div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{C['gv_strong']};font-weight:bold;">{_esc(t['next_meeting'])}</div>
    <div style="font-size:18px;font-weight:bold;margin:4px 0 2px;color:{C['ink']};">{_esc(when)}</div>
    {info_html}
    {btn}
    <a href="{_esc(links.page('/meeting/', lang))}" style="font-size:13px;color:{C['gv']};margin-left:10px;">{_esc(t['details'])} →</a>
  </td></tr></table>
</td></tr>""")

    link_style = f"color:{C['ink']};text-decoration:none;font-weight:600;"

    def section(key: str, color: str, body: str, page: str | None, extra: int = 0) -> str:
        more = ""
        if page:
            more_txt = t["more"].format(n=extra) if extra else t["see_all"]
            more = (f'<p style="margin:8px 0 0;font-size:13px;"><a href="{_esc(links.page(page, lang))}" '
                    f'style="color:{C["gv"]};">{_esc(more_txt)} →</a></p>')
        return f"""<tr><td style="padding:20px 32px 4px;">
  <h3 style="{h3.format(color=color)}">{_esc(t[key])}</h3>
  {body}{more}
</td></tr>"""

    # ---- announcements
    if data["announcements"]:
        body = []
        for a in data["announcements"]:
            text = tx(a, "body_md", lang) or tx(a, "summary", lang)
            short = text if len(text) <= 700 else shorten(md_to_text(text), 600)
            body.append(f"""<div style="margin:0 0 14px;">
  <div style="font-size:16px;font-weight:bold;margin:0 0 4px;">{_esc(tx(a, 'title', lang))}</div>
  <div style="font-size:14px;line-height:1.6;color:{C['ink']};">{md_to_html(short, C['gv'])}</div>
</div>""")
        parts.append(section("announcements", C["grape"], "".join(body), "/announcements/"))

    # ---- upcoming events
    if data["events"]:
        rows = []
        for ev in data["events"][:max_per]:
            r = event_row(ev, lang, links)
            rows.append(f"""<tr><td style="padding:8px 0;border-bottom:1px solid {C['line']};">
  <a href="{_esc(r['url'])}" style="{link_style}font-size:15px;">{_esc(r['title'])}</a>
  <div style="font-size:13px;color:{C['muted']};margin-top:2px;">{_esc(r['when'])}{(' · ' + _esc(r['where'])) if r['where'] else ''}</div>
</td></tr>""")
        body = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table>'
        parts.append(section("events", C["lv"], body, "/events/", max(0, len(data["events"]) - max_per)))

    # ---- new content groups
    colors = {"articles": C["gv"], "episodes": C["grape"], "videos": C["grape"], "instagram": C["lv"],
              "pdfs": C["gv"], "drive": C["vine"]}
    for g, page in GROUPS:
        items = data["groups"].get(g) or []
        if not items:
            continue
        rows_data, extra = build_rows(g, items, lang, links, page, max_per)
        rows = []
        for r in rows_data:
            pill = (f'<span style="display:inline-block;font-size:11px;font-weight:bold;letter-spacing:.02em;color:{r["fg"]};'
                    f'background:{r["bg"]};border-radius:999px;padding:2px 8px;margin-right:6px;vertical-align:1px;">'
                    f'{_esc(r["label"])}</span>') if r["label"] else ""
            meta = f'<div style="font-size:12px;color:{C["faint"]};margin-top:3px;">{_esc(r["meta"])}</div>' if r["meta"] else ""
            rows.append(f"""<tr><td style="padding:8px 0;border-bottom:1px solid {C['line']};font-size:15px;line-height:1.4;">
  {pill}<a href="{_esc(r['url'])}" style="{link_style}">{_esc(r['title'])}</a>{meta}
</td></tr>""")
        body = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table>'
        parts.append(section(g, colors[g], body, page, extra))

    if not total_count(data):     # (a monthly recurring event alone is not news)
        parts.append(f'<tr><td style="padding:16px 32px;font-size:14px;color:{C["muted"]};">{_esc(t["nothing"])}</td></tr>')

    # ---- Book of the Month (compact) + this month's poster & toolkit
    bm = botm_block(data, lang, links)
    month_line = (f'<p style="margin:{12 if bm["rows"] else 0}px 0 0;font-size:14px;">'
                  f'<a href="{_esc(bm["month_url"])}" style="color:{C["gv"]};font-weight:bold;">{_esc(bm["month_label"])} →</a></p>')
    if bm["rows"]:
        rows = []
        for r in bm["rows"]:
            pill = (f'<span style="display:inline-block;font-size:11px;font-weight:bold;letter-spacing:.02em;color:{r["fg"]};'
                    f'background:{r["bg"]};border-radius:999px;padding:2px 8px;margin-right:6px;vertical-align:1px;">'
                    f'{_esc(r["label"])}</span>')
            meta = " · ".join(x for x in (
                f'<strong style="color:{C["ink"]};">{_esc(r["price"])}</strong>' + (f' {_esc(r["regular"])}' if r["regular"] else ""),
                _esc(r["until"])) if x)
            rows.append(f"""<tr><td style="padding:8px 0;border-bottom:1px solid {C['line']};font-size:15px;line-height:1.4;">
  {pill}<a href="{_esc(r['url'])}" style="{link_style}">{_esc(r['title'])}</a>
  <div style="font-size:13px;color:{C['muted']};margin-top:3px;">{meta}</div>
</td></tr>""")
        parts.append(f"""<tr><td style="padding:20px 32px 4px;">
  <h3 style="{h3.format(color=C['grape'])}">{_esc(bm['title'])}</h3>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table>
  <p style="margin:8px 0 0;font-size:13px;"><a href="{_esc(bm['more_url'])}" style="color:{C['gv']};">{_esc(t['botm_more'])} →</a></p>
  {month_line}
</td></tr>""")
    else:
        parts.append(f'<tr><td style="padding:16px 32px 0;">{month_line}</td></tr>')

    # ---- call to action + machine translation note
    def btn(label: str, url: str, primary: bool) -> str:
        style = (f"background:{C['gv']};color:#ffffff;border:1px solid {C['gv']};" if primary
                 else f"background:#ffffff;color:{C['gv']};border:1px solid {C['gv']};")
        return (f'<a href="{_esc(url)}" style="display:inline-block;{style}text-decoration:none;font-weight:bold;'
                f'font-size:13px;padding:9px 14px;border-radius:8px;margin:0 6px 8px 0;">{_esc(label)}</a>')

    note = (f'<p style="margin:10px 0 0;font-size:12px;color:{C["faint"]};font-style:italic;">{_esc(t["machine"])}</p>'
            if data["machine"].get(lang) else "")
    parts.append(f"""<tr><td style="padding:22px 32px 26px;">
  {btn(t['cta_new'], links.page('/whats-new/', lang), True)}{btn(t['cta_events'], links.page('/events/', lang), False)}{btn(t['cta_site'], links.page('/', lang), False)}
  {note}
</td></tr>""")
    # lang on every cell so screen readers switch voice for the Spanish half
    return "\n".join(parts).replace("<tr><td style=", f'<tr><td lang="{lang}" style=')


# ---------------------------------------------------------------------------- plain text
def render_text(data: dict, cfg: dict, links: Links, max_per: int) -> str:
    site = cfg.get("site") or {}
    meeting_cfg = cfg.get("meeting") or {}
    out: list[str] = []
    title = site.get("title") or "Grapevine / La Viña"
    out += [title, site.get("committee") or "", "English first · Versión en español más abajo", ""]
    for lang in ("en", "es"):
        t = T[lang]
        head = f"{t['heading']} ({fmt_range(data['start'], data['end'], lang)})"
        out += ["=" * len(head), head, "=" * len(head), ""]
        m = data.get("meeting")
        if m:
            ex = m.get("extra") or {}
            st = parse_dt(ex.get("start") or m.get("date"))
            zoom = ex.get("online_url") or meeting_cfg.get("zoom_url") or ""
            out.append(f"{t['next_meeting'].upper()}: {fmt_day(st, lang, year=True)} · {fmt_time(st, lang)}" if st else t["next_meeting"].upper())
            if zoom:
                out.append(f"  {t['join_zoom']}: {zoom}")
            if meeting_cfg.get("meeting_id"):
                out.append(f"  {t['meeting_id']}: {meeting_cfg['meeting_id']}   {t['passcode']}: {meeting_cfg.get('passcode', '')}")
            note = meeting_note(cfg, m, lang)
            if note:
                out.append(f"  {note}")
            out.append("")
        if data["announcements"]:
            out += [t["announcements"].upper(), "-" * len(t["announcements"])]
            for a in data["announcements"]:
                out.append(f"* {tx(a, 'title', lang)}")
                body = md_to_text(tx(a, "body_md", lang) or tx(a, "summary", lang))
                if body:
                    out.append("  " + shorten(body, 600))
            out += [f"  → {links.page('/announcements/', lang)}", ""]
        if data["events"]:
            out += [t["events"].upper(), "-" * len(t["events"])]
            for ev in data["events"][:max_per]:
                r = event_row(ev, lang, links)
                out.append(f"* {r['title']} — {r['when']}{(' · ' + r['where']) if r['where'] else ''}")
                out.append(f"  {r['url']}")
            out += [f"  → {links.page('/events/', lang)}", ""]
        for g, page in GROUPS:
            items = data["groups"].get(g) or []
            if not items:
                continue
            rows, extra = build_rows(g, items, lang, links, page, max_per)
            out += [t[g].upper(), "-" * len(t[g])]
            for r in rows:
                label = f"[{r['label']}] " if r["label"] else ""
                meta = f" ({r['meta']})" if r["meta"] else ""
                out.append(f"* {label}{r['title']}{meta}")
                out.append(f"  {r['url']}")
            more = t["more"].format(n=extra) if extra else t["see_all"]
            out += [f"  → {more}: {links.page(page, lang)}", ""]
        if not total_count(data):
            out += [t["nothing"], ""]
        bm = botm_block(data, lang, links)
        if bm["rows"]:
            out += [bm["title"].upper(), "-" * len(bm["title"])]
            for r in bm["rows"]:
                price = f"{r['price']} {r['regular']}".strip()
                out.append(f"* [{r['label']}] {r['title']} — {price}{(' · ' + r['until']) if r['until'] else ''}")
                out.append(f"  {r['url']}")
            out.append(f"  → {t['botm_more']}: {bm['more_url']}")
        out += [f"{bm['month_label']}: {bm['month_url']}", ""]
        out.append(f"{t['cta_new']}: {links.page('/whats-new/', lang)}")
        if data["machine"].get(lang):
            out.append(t["machine"])
        out += ["", ""]
    committee = site.get("committee") or title
    out.append(T["en"]["footer_why"].format(committee=committee) + " " + T["en"]["footer_unsub"])
    out.append(T["es"]["footer_why"].format(committee=site.get("committee_es") or committee) + " " + T["es"]["footer_unsub"])
    out.append(links.page("/", "en"))
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------------------- e-mail
def parse_recipients(s: str) -> list[str]:
    out = []
    for part in re.split(r"[,;\n]+", s or ""):
        addr = parseaddr(part.strip())[1]
        if addr and "@" in addr:
            out.append(addr)
    return list(dict.fromkeys(out))


def build_message(subject: str, html_body: str, text_body: str, from_addr: str, from_name: str,
                  recipients: list[str], reply_to: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")           # RFC 2047 — the subject has ñ and dashes
    msg["From"] = formataddr((from_name, from_addr))    # encodes the non-ASCII display name
    # One address (e.g. a Google Group) → To. Several → Bcc (envelope only) so districts'
    # addresses are not shown to everyone.
    msg["To"] = recipients[0] if len(recipients) == 1 else formataddr((from_name, from_addr))
    if reply_to:
        msg["Reply-To"] = reply_to
        msg["List-Unsubscribe"] = f"<mailto:{reply_to}?subject=unsubscribe>"
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(from_addr.split("@", 1)[-1] or "localhost"))
    msg["Content-Language"] = "en, es"
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def send(msg: MIMEMultipart, from_addr: str, recipients: list[str]) -> None:
    server = os.environ.get("SMTP_SERVER", "").strip()
    port = int((os.environ.get("SMTP_PORT") or "587").strip() or 587)
    user = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    ctx = ssl.create_default_context()
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            if port == 465:
                conn: smtplib.SMTP = smtplib.SMTP_SSL(server, port, context=ctx, timeout=60)
            else:
                conn = smtplib.SMTP(server, port, timeout=60)
                conn.ehlo()
                if conn.has_extn("starttls"):
                    conn.starttls(context=ctx)
                    conn.ehlo()
            with conn:
                if user:
                    conn.login(user, password)
                refused = conn.send_message(msg, from_addr=from_addr, to_addrs=recipients)
            if refused:
                log(f"WARNING: {len(refused)} recipient(s) were refused by the mail server")
            return
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(
                "The mail server rejected the username/password. For Gmail you need an *App Password* "
                "(Google Account → Security → 2-Step Verification → App passwords), not your normal password."
            ) from e
        except smtplib.SMTPRecipientsRefused as e:
            raise RuntimeError(f"All recipients were refused: {list(e.recipients)}") from e
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, ConnectionError,
                TimeoutError, socket.gaierror) as e:
            last_err = e
            log(f"attempt {attempt}/3 failed ({type(e).__name__}: {e}); retrying…")
            time.sleep(10 * attempt)
    raise RuntimeError(f"Could not reach the mail server {server}:{port}: {last_err}")


def step_summary(lines: list[str]) -> None:
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if not p:
        return
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send the weekly bilingual e-mail digest.")
    ap.add_argument("--dry-run", action="store_true", help="don't send; write .tmp/digest.html and .tmp/digest.txt")
    ap.add_argument("--days", type=int, default=None, help="look back N days (default: config digest.days or 7)")
    ap.add_argument("--event-days", type=int, default=30, help="show events in the next N days (default 30)")
    ap.add_argument("--max-per-section", type=int, default=6, help="max items listed per section (default 6)")
    ap.add_argument("--to", default=None, help="override recipients (comma-separated)")
    ap.add_argument("--only-on-weekday", action="store_true",
                    help="do nothing unless today (Central time) is config digest.weekday")
    ap.add_argument("--force", action="store_true", help="send even if there is nothing new")
    ap.add_argument("--as-of", default=None, help="pretend today is YYYY-MM-DD (testing)")
    ap.add_argument("--out-dir", default=str(ROOT / ".tmp"), help="where --dry-run writes the preview")
    args = ap.parse_args(argv)

    cfg = load_config()
    site = cfg.get("site") or {}
    digest_cfg = cfg.get("digest") or {}

    if args.as_of:
        d = date.fromisoformat(args.as_of)
        now = datetime(d.year, d.month, d.day, 23, 0, tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc).replace(microsecond=0)

    if args.only_on_weekday:
        want = str(digest_cfg.get("weekday") or "monday").strip().lower()
        today = WEEKDAYS[to_central(now).weekday()]
        if want in WEEKDAYS and today != want:
            log(f"today is {today}; the digest goes out on {want} (config/site.yml → digest.weekday). Nothing to do.")
            return 0

    days = args.days or int(digest_cfg.get("days") or 7)
    site_url = (os.environ.get("SITE_URL") or site.get("url") or "").strip().rstrip("/")
    if not site_url:
        log("WARNING: no site URL (config site.url / SITE_URL) — links will be relative")
    links = Links(site_url)

    data = collect(now, days, args.event_days, args.max_per_section)
    n = total_count(data)
    counts = {g: len(v) for g, v in data["groups"].items() if v}
    log(f"window {data['start']:%Y-%m-%d} → {data['end']:%Y-%m-%d} ({days} days): {n} item(s) "
        f"{counts} · announcements={len(data['announcements'])} events={len(data['events'])} "
        f"meeting={'yes' if data['meeting'] else 'no'}")

    title = site.get("title") or "Grapevine / La Viña"
    subject = (f"{title} — What's new · Novedades "
               f"({fmt_range(data['start'], data['end'], 'en')})")
    html_body = render_html(data, cfg, links, args.max_per_section, subject)
    text_body = render_text(data, cfg, links, args.max_per_section)

    if args.dry_run:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "digest.html").write_text(html_body, encoding="utf-8")
        (out / "digest.txt").write_text(text_body, encoding="utf-8")
        log(f"DRY RUN — subject: {subject}")
        log(f"preview written to {out / 'digest.html'} and {out / 'digest.txt'}")
        step_summary(["### E-mail digest preview (not sent)", f"**Subject:** {subject}", "",
                      f"{n} new item(s): {counts}; announcements {len(data['announcements'])}; "
                      f"events {len(data['events'])}", "", "Download the `digest-preview` artifact to see it."])
        return 0

    if n == 0 and not args.force:
        log("nothing new in the window — not sending (use --force to send anyway)")
        step_summary(["### E-mail digest", "Nothing new this week — no e-mail sent."])
        return 0

    recipients = parse_recipients(args.to or os.environ.get("DIGEST_TO", ""))
    missing = [k for k in ("SMTP_SERVER", "SMTP_USERNAME", "SMTP_PASSWORD") if not os.environ.get(k, "").strip()]
    if missing or not recipients:
        log(f"e-mail is not configured (missing: {', '.join(missing + ([] if recipients else ['DIGEST_TO']))}). "
            "See README → 'Weekly e-mail digest'.")
        return 2

    user = os.environ.get("SMTP_USERNAME", "").strip()
    from_addr = parseaddr(os.environ.get("DIGEST_FROM", "").strip())[1] or (user if "@" in user else site.get("contact_email", ""))
    reply_to = parseaddr(os.environ.get("DIGEST_REPLY_TO", "").strip())[1] or site.get("contact_email", "") or from_addr
    from_name = site.get("committee") or title
    msg = build_message(subject, html_body, text_body, from_addr, from_name, recipients, reply_to)
    try:
        send(msg, from_addr, recipients)
    except Exception as e:
        log(f"ERROR: {e}")
        step_summary(["### E-mail digest", f"Sending FAILED: {e}"])
        return 1
    log(f"sent to {len(recipients)} recipient(s): {subject}")
    step_summary(["### E-mail digest sent", f"**Subject:** {subject}", "",
                  f"Recipients: {len(recipients)} · new items: {n} {counts}"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
