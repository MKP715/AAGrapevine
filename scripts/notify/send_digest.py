"""Monthly bilingual (English + Spanish) e-mail digest for the districts.

One EDITION per calendar month (America/Chicago), sent on the 1st (.github/workflows/monthly-digest.yml).
Edition "2026-10" (sent October 1) holds — the same rules as the website's /digest/ page
(eleventy/filters/community.js → buildMonthlyDigest; keep the two in step):

  * the next committee meeting (Zoom link, ID, passcode, the chair's note)
  * what was new LAST month (September, Central time): announcements, podcast episodes (a YouTube
    upload of the same episode is folded into it), other videos, new documents, committee files —
    data/site/whatsnew.json entries whose news date (wn_date) falls in the month, completed from the
    full episodes / videos / pdfs / announcements files (whatsnew.json keeps only its newest 150)
  * this month's magazine issues (theme, number of stories, a few highlights — free to read first,
    members' stories, Area 65 and Texas writers first) + "put it to work" tips (config/carry.yml)
    + the link to this month's toolkit (/monthly/YYYY-MM/)
  * stories by writers from Area 65 and the rest of Texas published last month (spotlight.json)
  * coming up THIS month: events not over yet (a monthly series once), the weekly open meetings,
    the number of Grapevine meetings in our Area and nearby
  * story deadlines through the end of NEXT month, La Viña's open topics, the phone story lines
  * Book of the Month (compact; the prices live on /shop/#botm), the lowest month-to-month
    subscription price and a pointer to the daily quote on the home page
  * each section in English first, then in Spanish (titles are already translated)

Standard library only (smtplib + email.mime) so it runs anywhere without installing the sync
pipeline. PyYAML is used when available to read config/site.yml and config/carry.yml; a small
built-in reader is the fallback for site.yml (without PyYAML the "put it to work" tips are left out).

Usage (from the repo root):

    python -m scripts.notify.send_digest --dry-run                    # → .tmp/digest.html + .tmp/digest.txt
    python -m scripts.notify.send_digest --dry-run --month 2026-10    # preview another edition
    python -m scripts.notify.send_digest                              # sends (needs the SMTP_* env vars below)

Environment (GitHub secrets in .github/workflows/monthly-digest.yml):

    SMTP_SERVER     e.g. smtp.gmail.com                  (required to send)
    SMTP_PORT       587 (STARTTLS, default) or 465 (SSL). On any port but 465 the server MUST offer
                    STARTTLS: the password is never sent unencrypted (the run stops instead)
    SMTP_USERNAME   the mailbox login                    (required to send)
    SMTP_PASSWORD   an APP password, not your normal one (required to send)
    DIGEST_TO       one address (e.g. a Google Group) or several, comma-separated
                    (several addresses are sent as Bcc so nobody sees the others)
    DIGEST_FROM     optional "From" address (default: SMTP_USERNAME)
    DIGEST_REPLY_TO optional reply-to (default: site.contact_email from config)
    SITE_URL        optional public site address (overrides site.url from config)

Sending: connecting and logging in are tried up to 3 times; the message itself is handed over
ONCE — if the connection breaks at that point the run fails with "it MAY have been sent" instead of
trying again (a second try could e-mail every district twice).

Exit codes: 0 = sent / previewed / nothing to do, 1 = sending failed, 2 = not configured or a
--month that is not YYYY-MM.
"""
from __future__ import annotations

import argparse
import calendar
import html
import json
import os
import re
import smtplib
import socket
import ssl
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "site.yml"
CARRY_PATH = ROOT / "config" / "carry.yml"
SITE_DIR = ROOT / "data" / "site"
ASSET_DIR = ROOT / "src"   # the site's own files (/assets/… → src/assets/…)

# Copies of rules in scripts/sync/ and eleventy/filters/ (kept here so this script runs with the
# standard library alone, without importing the sync pipeline) — keep them equal
# (tests/test_digest_parity.py builds the same editions with community.js and compares them):
MULTI_DAY_MIN_HOURS = 18                             # build_data.MULTI_DAY_MIN_H: a timed event over several days
EVERY_ISSUE = re.compile(r"(?i)in every issue|en cada (?:edici[oó]n|n[uú]mero)")  # build_data._EVERY_ISSUE
SE_SUFFIX = re.compile(r"(?i)\s*[\[(]\s*(?:season|temporada)\s*\d+\s*[,;.·-]?\s*(?:episode|episodio|ep\.?)\s*\d+\s*[\])]\s*$")
ONE_SUFFIX = re.compile(r"(?i)\s*[\[(]\s*(?:season|temporada|episode|episodio)\s*\d+\s*[\])]\s*$")  # media.js
TIMED_NO_END_HOURS = 6                               # community.js eventEndMs: a timed event without an end
NEWS_GROUPS = ("announcement", "article", "episode", "video", "pdf", "drive")  # community.js MONTH_NEWS
NEWS_SOURCES = ("episodes", "videos", "pdfs", "announcements")                 # community.js MONTH_SOURCES

# Brand colors (same tokens as src/assets/css/main.css, light theme).
C = {
    "paper": "#fbf8f2", "surface": "#ffffff", "surface2": "#f4efe6", "ink": "#1d1a26",
    "muted": "#57526a", "faint": "#8a8599", "line": "#e6dfd2",
    "gv": "#0a5fa8", "gv_strong": "#07457c", "gv_soft": "#e5f0fa",
    "lv": "#b8430b", "lv_strong": "#8f3308", "lv_soft": "#fdeee3", "grape": "#5b2a86", "grape_soft": "#f1e8f8",
    "vine": "#2f6e2c", "vine_soft": "#e7f3e3",
}

# ---------------------------------------------------------------------------- words
# The website's wording (src/_i18n/community.json → community.digest.*), in the e-mail's own table.
T = {
    "en": {
        "lang_name": "English",
        "masthead": "Monthly Digest",
        "edition": "{month} edition",
        "edition_sub": "What's new in {prev} · Coming up in {month}",
        "intro": "In {prev}: {list}. And here's what's coming up in {month}.",
        "intro_quiet": "A quiet {prev} on the site. Here's what's coming up in {month}.",
        "and": "and",
        "n": {"article": ("{n} magazine stories", "1 magazine story"), "episode": ("{n} podcast episodes", "1 podcast episode"),
              "video": ("{n} videos", "1 video"), "pdf": ("{n} documents", "1 document"),
              "drive": ("{n} committee files", "1 committee file"), "announcement": ("{n} announcements", "1 announcement")},
        "next_meeting": "Next committee meeting",
        "join_zoom": "Join on Zoom",
        "meeting_id": "Meeting ID",
        "passcode": "Passcode",
        "meeting_note": "All AA members are welcome. No registration required.",
        "announcement": "Announcements",
        "issues": "This month in the magazines",
        "stories": ("{n} stories", "1 story"),
        "free_n": "{n} free to read",
        "free": "free to read",
        "subscriber": "subscriber story",
        "issue_more": "See all {n} stories",
        "tips": "Put it to work",
        "tips_sub": "Ways to carry the message with this month's Grapevine",
        "toolkit": "This month's toolkit ({month})",
        "writers": "Writers from Area 65 & Texas",
        "writers_sub": "Their stories came out in Grapevine or La Viña in {prev} — Area 65 writers first.",
        "group_neta65": "Area 65 (Northeast Texas)",
        "group_texas": "Elsewhere in Texas",
        "anonymous": "Anonymous",
        "episode": "Podcasts",
        "video": "Videos",
        "pdf": "Documents",
        "drive": "Committee uploads",
        "twin": "also on YouTube",
        "instagram": "Grapevine and La Viña on Instagram",
        "coming": "Coming up in {month}",
        "no_events": "No other events on the calendar for the rest of {month} yet.",
        "every_week": "Every week",
        "starting": "starting {date}",
        "gvm": "Grapevine meetings near you",
        "gvm_text": "{ours} every week in our Area, plus {nearby} in nearby areas",
        "gvm_text_area": "{ours} every week in our Area",
        "meetings_n": ("{n} meetings", "1 meeting"),
        "story": "Share your story — upcoming deadlines",
        "due": "Due {date}",
        "issue_of": "{issue} issue",
        "lv_anytime": "La Viña takes stories on its suggested themes anytime — no deadline. This month, why not:",
        "record": "Record your story by phone",
        "record_how": "How to record by phone",
        "story_cta": "How to send a story",
        "see_all": "See all",
        "details": "Details",
        "more": "and {n} more on the website",
        "album": "Photos: {name}",
        "new_photos": "{n} new photos",
        "new_photo": "1 new photo",
        "nothing": "A quiet month — nothing new was published in {prev}. The website still has hundreds of stories, podcasts and service resources.",
        "cta_site": "Open the website",
        "cta_new": "Everything new",
        "cta_events": "Events calendar",
        "machine": "Some titles were translated automatically.",
        "online": "Online",
        "monthly": "every month",
        "tentative": "details to be confirmed",
        "footer_why": "You are receiving this monthly summary from the {committee}.",
        "footer_unsub": "To stop receiving it, reply with \"unsubscribe\".",
        "footer_anon": "Feel free to forward it to your group or district — and please protect everyone's anonymity.",
        "pages": ("{n} pages", "1 page"),
        "min": "{n} min",
        "episode_se": "S{s} · E{e}",
        "botm_title": "Book of the Month — {pct}% off",
        "botm_title_plain": "Book of the Month",
        "botm_regular": "(regular {price})",
        "botm_until": "until {date}",
        "botm_more": "Book of the Month details on our shop page",
        "subs_from": "Month-to-month subscriptions from {amount}",
        "quote": "A daily quote from Grapevine and La Viña, on our home page",
        "quote_link": "Read today's quote",
        "full_calendar": "Full calendar",
        "in_other": "in Spanish",          # after something written in the other language
        "weekly_open": "Weekly Open",      # the pill of the Grapevine Weekly Open podcast
    },
    "es": {
        "lang_name": "Español",
        "masthead": "Resumen mensual",
        "edition": "Edición de {month}",
        "edition_sub": "Novedades de {prev} · Lo que viene en {month}",
        "intro": "En {prev}: {list}. Y esto es lo que viene en {month}.",
        "intro_quiet": "Un {prev} tranquilo en el sitio. Esto es lo que viene en {month}.",
        "and": "y",
        "n": {"article": ("{n} historias de las revistas", "1 historia de las revistas"),
              "episode": ("{n} episodios de podcast", "1 episodio de podcast"),
              "video": ("{n} videos", "1 video"), "pdf": ("{n} documentos", "1 documento"),
              "drive": ("{n} archivos del comité", "1 archivo del comité"), "announcement": ("{n} anuncios", "1 anuncio")},
        "next_meeting": "Próxima reunión del comité",
        "join_zoom": "Entrar por Zoom",
        "meeting_id": "ID de reunión",
        "passcode": "Código de acceso",
        "meeting_note": "Todos los miembros de AA son bienvenidos. No hace falta inscribirse.",
        "announcement": "Anuncios",
        "issues": "Este mes en las revistas",
        "stories": ("{n} historias", "1 historia"),
        "free_n": "{n} gratis para leer",
        "free": "gratis para leer",
        "subscriber": "historia para suscriptores",
        "issue_more": "Ver las {n} historias",
        "tips": "Ponla a trabajar",
        "tips_sub": "Maneras de llevar el mensaje con la Grapevine de este mes",
        "toolkit": "El kit de este mes ({month})",
        "writers": "Escritores del Área 65 y de Texas",
        "writers_sub": "Sus historias salieron en Grapevine o La Viña en {prev} — primero los del Área 65.",
        "group_neta65": "Área 65 (Noreste de Texas)",
        "group_texas": "En el resto de Texas",
        "anonymous": "Anónimo",
        "episode": "Podcasts",
        "video": "Videos",
        "pdf": "Documentos",
        "drive": "Archivos del comité",
        "twin": "también en YouTube",
        "instagram": "Grapevine y La Viña en Instagram",
        "coming": "Lo que viene en {month}",
        "no_events": "Todavía no hay otros eventos en el calendario para lo que queda de {month}.",
        "every_week": "Cada semana",
        "starting": "desde el {date}",
        "gvm": "Reuniones de Grapevine cerca de ti",
        "gvm_text": "{ours} cada semana en nuestra Área, y {nearby} en áreas cercanas",
        "gvm_text_area": "{ours} cada semana en nuestra Área",
        "meetings_n": ("{n} reuniones", "1 reunión"),
        "story": "Comparte tu historia — próximas fechas límite",
        "due": "Fecha límite: {date}",
        "issue_of": "Edición de {issue}",
        "lv_anytime": "La Viña recibe historias sobre sus temas sugeridos en cualquier momento, sin fecha límite. Ideas para este mes:",
        "record": "Graba tu historia por teléfono",
        "record_how": "Cómo grabar por teléfono",
        "story_cta": "Cómo enviar una historia",
        "see_all": "Ver todo",
        "details": "Detalles",
        "more": "y {n} más en el sitio web",
        "album": "Fotos: {name}",
        "new_photos": "{n} fotos nuevas",
        "new_photo": "1 foto nueva",
        "nothing": "Un mes tranquilo — no se publicó nada nuevo en {prev}. El sitio web tiene cientos de historias, podcasts y recursos de servicio.",
        "cta_site": "Abrir el sitio web",
        "cta_new": "Todas las novedades",
        "cta_events": "Calendario de eventos",
        "machine": "Algunos títulos se tradujeron automáticamente.",
        "online": "En línea",
        "monthly": "cada mes",
        "tentative": "detalles por confirmar",
        "footer_why": "Recibes este resumen mensual del {committee}.",
        "footer_unsub": "Para dejar de recibirlo, responde con \"cancelar\".",
        "footer_anon": "Puedes reenviarlo a tu grupo o distrito — y, por favor, protege el anonimato de todos.",
        "pages": ("{n} páginas", "1 página"),
        "min": "{n} min",
        "episode_se": "T{s} · E{e}",
        "botm_title": "Libro del mes — {pct}% de descuento",
        "botm_title_plain": "Libro del mes",
        "botm_regular": "(precio regular {price})",
        "botm_until": "hasta el {date}",
        "botm_more": "Detalles del libro del mes en nuestra página de la tienda",
        "subs_from": "Suscripciones mes a mes desde {amount}",
        "quote": "Una cita diaria de Grapevine y La Viña, en nuestra página de inicio",
        "quote_link": "Lee la cita de hoy",
        "full_calendar": "Calendario completo",
        "in_other": "en inglés",
        "weekly_open": "Reunión Abierta Semanal",
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

# The news lists of the edition, in order, with the site page "See all" points to.
GROUPS = [
    ("episode", "/listen/"),
    ("video", "/watch/"),
    ("pdf", "/library/"),
    ("drive", "/portfolio/"),
]


def log(msg: str) -> None:
    line = f"[digest] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:             # a console that is not UTF-8 (Windows cp1252): keep the log line
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


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


def load_carry() -> dict:
    """config/carry.yml ("put this issue to work"): {"ways": {id: way}, "tips": {"YYYY-MM": [tip]}}.
    Needs PyYAML (nested lists); without it, or with a broken file, the tips are simply left out."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(CARRY_PATH.read_text(encoding="utf-8")) or {}
    except ImportError:
        log("PyYAML is not installed — the \"put it to work\" tips are left out")
        return {"ways": {}, "tips": {}}
    except Exception as e:  # missing/broken file never stops the digest
        log(f"could not read config/carry.yml ({e}) — the tips are left out")
        return {"ways": {}, "tips": {}}
    ways = {str(w["id"]): w for w in (data.get("ways") or []) if isinstance(w, dict) and w.get("id")}
    tips = {str(k): v for k, v in (data.get("tips") or {}).items() if isinstance(v, list)}
    return {"ways": ways, "tips": tips}


def load_file(name: str) -> dict:
    """A whole data/site file ({} when missing or broken — a broken file never stops the digest)."""
    try:
        with open(SITE_DIR / f"{name}.json", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log(f"could not read data/site/{name}.json: {e}")
        return {}


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
    botm = load_file("shop").get("botm")
    return [b for b in (botm or []) if isinstance(b, dict)]


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


def central_day(v: Any) -> date | None:
    """The calendar day (America/Chicago) of an ISO timestamp; a date-only value is that day."""
    if is_date_only(v):
        return date.fromisoformat(v.strip())
    dt = parse_dt(v)
    return to_central(dt).date() if dt else None


def end_of_day(v: str | date) -> datetime:
    """Date-only 'YYYY-MM-DD' → the midnight after that day, Central time (community.js eventEndMs),
    so an all-day event stays in the digest for the whole of its last day."""
    d = v if isinstance(v, date) else date.fromisoformat(v.strip())
    nxt = d + timedelta(days=1)
    if TZ is not None:
        return datetime(nxt.year, nxt.month, nxt.day, 0, 0, tzinfo=TZ)
    off = to_central(datetime(nxt.year, nxt.month, nxt.day, 12, tzinfo=timezone.utc)).utcoffset() or timedelta(hours=-6)
    return datetime(nxt.year, nxt.month, nxt.day, 0, 0, tzinfo=timezone(off))


def month_add(key: str, n: int) -> str:
    """'2026-01' + (-1) → '2025-12'."""
    y, m = (int(x) for x in key.split("-"))
    i = y * 12 + (m - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def month_days(key: str) -> tuple[date, date]:
    """'2026-02' → (2026-02-01, 2026-02-28)."""
    y, m = (int(x) for x in key.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def edition_of(now: datetime, key: str | None = None) -> dict:
    """The edition of `now` (its Central-time month) or of an explicit 'YYYY-MM': the month itself,
    the PREVIOUS month the news comes from and the NEXT month (story deadlines run through it)."""
    k = key if key and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", key) else to_central(now).strftime("%Y-%m")
    prev, nxt = month_add(k, -1), month_add(k, 1)
    first, last = month_days(k)
    prev_first, prev_last = month_days(prev)
    return {"key": k, "first": first, "last": last, "prev": prev, "prev_first": prev_first,
            "prev_last": prev_last, "next": nxt, "next_last": month_days(nxt)[1]}


def month_word(key: str, lang: str) -> str:
    """'2026-09' → "September" / "septiembre"."""
    return MONTHS[lang][int(key[5:7]) - 1]


def month_label(key: str, lang: str) -> str:
    """'2026-09' → "September 2026" / "septiembre de 2026" (the /monthly/ page's label)."""
    return f"{month_word(key, lang)} de {key[:4]}" if lang == "es" else f"{month_word(key, lang)} {key[:4]}"


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
    """"7:00 PM CDT" / "7:00 p. m. (hora del Centro)": Central time. The Spanish half says it in
    words, like the rest of the Spanish site (CDT / CST are English abbreviations)."""
    d = to_central(dt)
    h = d.hour % 12 or 12
    if lang == "es":   # the site's spelling (no-break spaces: never split across lines)
        return f"{h}:{d.minute:02d} {'a.' if d.hour < 12 else 'p.'} m. (hora del Centro)"
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'} {d.tzname() or 'CT'}"


def fmt_month_day(ymd: str | date, lang: str) -> str:
    """'2026-10-14' → "October 14" / "14 de octubre"."""
    d = ymd if isinstance(ymd, date) else date.fromisoformat(ymd)
    return f"{d.day} de {MONTHS['es'][d.month - 1]}" if lang == "es" else f"{MONTHS['en'][d.month - 1]} {d.day}"


# ---------------------------------------------------------------------------- item helpers
def tx(item: dict, field: str, lang: str) -> str:
    """Field in the requested language (build_data's i18n block), else the original."""
    i18n = (item.get("i18n") or {}).get(field) or {}
    val = i18n.get(lang) or i18n.get(item.get("lang") or "") or item.get(field)
    if val is None and field == "body_md":
        val = (item.get("extra") or {}).get("body_md")
    return str(val or "").strip()


def tr(item: dict | None, field: str, lang: str) -> str:
    """The /monthly/ month model's rule (monthly.js tr): i18n[lang], else i18n.en, else the field
    (or extra[field]) as written."""
    if not item:
        return ""
    i = (item.get("i18n") or {}).get(field) or {}
    for k in (lang, "en"):
        if isinstance(i.get(k), str) and i[k].strip():
            return i[k].strip()
    return str(item.get(field) or (item.get("extra") or {}).get(field) or "").strip()


def tx_extra(item: dict, field: str, lang: str) -> str:
    """An `extra` field (issue_label, topic, section, album …) in the requested language: build_data's
    i18n copy when it has that language, else the original value."""
    val = ((item.get("i18n") or {}).get(field) or {}).get(lang) or (item.get("extra") or {}).get(field)
    return str(val or "").strip()


def is_machine(item: dict, lang: str) -> bool:
    return lang in (item.get("machine") or [])


def one_line(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


# JavaScript's a.localeCompare(b) (en-US, the Unicode root order) — how the website sorts ids and titles
# that tie — so the e-mail lists things in the same order: spaces and punctuation < digits < letters;
# accents, then case (lower-case first) only break ties. Curly quotes and the no-break space count as
# their plain forms ("variants"). Characters not listed here go after the letters.
_COLLATE = {c: i for i, c in enumerate(
    " _-\u2013\u2014,;:!\u00a1?\u00bf.\u2026\u00b7'\"\u00ab\u00bb()[]{}@*/\\&#%\u2022`^\u00b0\u00a9\u00ae+<=>|~$\u00a3\u20ac"
    "0123456789abcdefghijklmnopqrstuvwxyz")}
_VARIANT = {"\u00a0": (" ", 1), "\u2018": ("'", 1), "\u2019": ("'", 2), "\u201c": ('"', 1), "\u201d": ('"', 2)}


def js_order(v: Any) -> tuple:
    """A sort key: sorted(xs, key=js_order) == xs.sort((a, b) => a.localeCompare(b)) for ids and titles."""
    s = str(v or "")
    primary: list[int] = []
    accent: list[int] = []
    case: list[int] = []
    for c in unicodedata.normalize("NFD", s):
        if unicodedata.combining(c):
            if accent:
                accent[-1] = 1
            continue
        plain, variant = _VARIANT.get(c, (c, 1 if c.isupper() else 0))
        primary.append(_COLLATE.get(plain.lower(), 1000 + ord(plain.lower())))
        accent.append(0)
        case.append(variant)
    return tuple(primary), tuple(accent), tuple(case), s


def media_title(item: dict, lang: str) -> str:
    """A podcast episode / video title without its "[Season 11, Episode 12]" tail (the badge says it),
    like the website's media titles."""
    t = tx(item, "title", lang)
    s = ONE_SUFFIX.sub("", SE_SUFFIX.sub("", t)).strip()
    return s or t


def title_of(item: dict, lang: str) -> str:
    return media_title(item, lang) if item.get("kind") in ("episode", "video") else (tx(item, "title", lang) or str(item.get("title") or ""))


def group_of(item: dict) -> str:
    """Which What's New group an item belongs to (community.js groupOf)."""
    k = item.get("kind")
    if k in ("announcement", "event", "article", "episode", "post", "topic"):
        return k
    if k == "video":
        return "drive" if item.get("source") == "drive" else "video"
    if k == "pdf":
        return "drive" if item.get("source") == "drive" else "pdf"
    if item.get("source") == "drive" or k in ("document", "slides", "photo", "video_file", "form"):
        return "drive"
    return "other"


def is_department(item: dict) -> bool:
    """An "In Every Issue" page (AA News, Dear Grapevine, Discussion Topic …), not a member's story."""
    ex = item.get("extra") or {}
    return ex.get("department") is True or bool(EVERY_ISSUE.search(str(ex.get("section") or "")))


def scope_rank(item: dict) -> int:
    return {"neta65": 0, "texas": 1}.get(((item.get("extra") or {}).get("geo") or {}).get("scope"), 2)


def is_recurring(item: dict) -> bool:
    """A date of a monthly event from config/site.yml `recurring_events:` (build_data.recurring_events)."""
    return item.get("category") == "recurring"


def photo_count(item: dict) -> int:
    """How many photos a What's New item stands for (a same-day album group has extra.count)."""
    try:
        return max(1, int((item.get("extra") or {}).get("count") or 1))
    except (TypeError, ValueError):
        return 1


# build_data.committee_meetings writes the meeting's summary as "<this intro> <meeting.note>".
MEETING_INTRO = {"en": re.compile(r"^Our monthly committee meeting on [^.]*\.\s*"),
                 "es": re.compile(r"^Nuestra reunión mensual del comité por [^.]*\.\s*")}


def meeting_note(cfg: dict, meeting: dict, lang: str) -> str:
    """The line under the next meeting's date: the chair's own note from config/site.yml
    (meeting.note / note_es) — the same text the website shows for the meeting. Without note_es the
    Spanish comes from the meeting event, where the site's daily build already translated the note.
    An empty note shows nothing (like the site); unreadable settings → the standard sentence."""
    mt = cfg.get("meeting")
    if not isinstance(mt, dict):
        return T[lang]["meeting_note"]
    note = one_line(mt.get("note"))
    if lang == "en" or not note:
        return note
    es = one_line(mt.get("note_es"))
    if es:
        return es
    summary = (meeting.get("i18n") or {}).get("summary") or {}
    built_en = MEETING_INTRO["en"].sub("", one_line(summary.get("en")))
    built_es = MEETING_INTRO["es"].sub("", one_line(summary.get("es")))
    # only when the site was built from the same note (an edit made today is not built yet)
    return built_es if built_es and built_en == note else note


class Links:
    def __init__(self, site_url: str):
        self.base = site_url.rstrip("/")

    def page(self, path: str, lang: str) -> str:
        path = path if path.startswith("/") else "/" + path
        return self.base + ("/es" if lang == "es" else "") + path

    def asset(self, path: str) -> str:
        """A site file (never language-prefixed): '/assets/cache/…' → the full address."""
        return path if path.startswith(("http://", "https://")) else self.base + ("" if path.startswith("/") else "/") + path

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
        return (T[lang]["weekly_open"] if show == "wo" else "Podcast"), C["grape"], C["grape_soft"]
    if kind in ("video", "video_file") and src != "drive":
        return "Video", C["grape"], C["grape_soft"]
    if kind == "pdf" and src != "drive":
        host = (item.get("extra") or {}).get("host") or ""
        doc = "Documento" if lang == "es" else "Document"
        return (f"{doc} · La Viña", C["lv"], C["lv_soft"]) if "lavina" in host else (f"{doc} · Grapevine", C["gv"], C["gv_soft"])
    if kind == "announcement":
        return ("Anuncio" if lang == "es" else "Announcement"), C["vine"], C["vine_soft"]
    if src == "drive":
        en, es = DRIVE_CATEGORIES.get(item.get("category") or "other", DRIVE_CATEGORIES["other"])
        return (es if lang == "es" else en), C["vine"], C["vine_soft"]
    return "", C["muted"], C["surface2"]


def localize_months(label: str, lang: str) -> str:
    """'October 2026' ⇄ 'octubre 2026' so issue labels read naturally in each section (Spanish month
    names are lower-case; in_sentence adds the "de")."""
    src, dst = ("en", "es") if lang == "es" else ("es", "en")
    for a, b in zip(MONTHS[src], MONTHS[dst]):
        label = re.sub(rf"\b{a}\b", b if lang == "es" else b.capitalize(), label, flags=re.I)
    return label


def in_sentence(label: str, lang: str) -> str:
    """An issue label inside a line of text (community.js issueInSentence): Spanish months lower-case
    and "de" before the year — "Septiembre / Octubre 2026" → "septiembre/octubre de 2026"."""
    s = one_line(label)
    if lang != "es":
        return s
    m = re.fullmatch(r"(.*?)\s+(?:de\s+)?(\d{4})", s)
    if not m:
        return s
    months = [w.lower() if w.lower() in MONTHS["es"] else w for w in re.split(r"\s*/\s*", m.group(1))]
    return f"{'/'.join(months)} de {m.group(2)}"


def issue_label(item: dict, lang: str) -> str:
    """The item's issue as it reads inside a line: "October 2026" / "octubre de 2026"."""
    label = ((item.get("i18n") or {}).get("issue_label") or {}).get(lang)
    return in_sentence(str(label).strip() if label else localize_months(str((item.get("extra") or {}).get("issue_label") or ""), lang), lang)


def pub_of(item: dict) -> str:
    """'lv' for La Viña (in Spanish), 'gv' for Grapevine (in English)."""
    ex = item.get("extra") or {}
    return "lv" if ex.get("publication") == "lv" or item.get("source") == "lavina" or item.get("category") == "lv" else "gv"


def other_lang(pub: str, lang: str) -> str:
    """" (in Spanish)" after La Viña in the English half, " (en inglés)" after Grapevine in the
    Spanish half (like the website and the district report); "" when the magazine is in `lang`."""
    return f" ({T[lang]['in_other']})" if (pub == "lv") != (lang == "es") else ""


def lc_first(s: str) -> str:
    """The first letter lower-case: a schedule ("Los miércoles a las …") inside a sentence."""
    return s[:1].lower() + s[1:] if s else s


def item_meta(item: dict, lang: str) -> str:
    ex = item.get("extra") or {}
    kind = item.get("kind")
    parts: list[str] = []
    d = parse_dt(item.get("_when") or item.get("date"))
    if kind == "article":
        if ex.get("issue_label"):
            parts.append(issue_label(item, lang))
        topic_field = "topic" if ex.get("topic") else "section" if ex.get("section") else None
        if topic_field:
            parts.append(tx_extra(item, topic_field, lang))
    elif kind == "episode":
        if ex.get("season") and ex.get("episode"):
            parts.append(T[lang]["episode_se"].format(s=ex["season"], e=ex["episode"]))
        if ex.get("duration_sec"):
            parts.append(T[lang]["min"].format(n=max(1, round(int(ex["duration_sec"]) / 60))))
        if d:
            parts.append(fmt_day(d, lang, weekday=False))
    elif kind == "video":
        if d:
            parts.append(fmt_day(d, lang, weekday=False))
        if ex.get("duration_sec"):
            parts.append(T[lang]["min"].format(n=max(1, round(int(ex["duration_sec"]) / 60))))
    elif kind == "pdf" and item.get("source") != "drive":
        if ex.get("pages"):
            many, one = T[lang]["pages"]
            parts.append(one if str(ex["pages"]).strip() == "1" else many.format(n=ex["pages"]))
        host = str(ex.get("host") or "").replace("www.", "")
        if host:
            parts.append(host)
    else:
        if d:
            parts.append(fmt_day(d, lang, weekday=False))
    return " · ".join(p for p in parts if p)


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


# ---------------------------------------------------------------------------- collect
def news_date(item: dict, now: datetime, from_whatsnew: bool) -> str | None:
    """When an item became news. A What's New entry: its wn_date (build_data applied every rule);
    an entry without it: the community.js whenOf fallback. A full-list item (episodes, videos,
    documents, announcements): its own date only — undated or future-dated items count only
    from What's New (build_data.effective_ts)."""
    if not from_whatsnew:
        d = item.get("date")
        return d if isinstance(d, str) and parse_dt(d) else None
    if item.get("wn_date"):
        return item["wn_date"]
    hi = now + timedelta(days=1)
    d, fs = parse_dt(item.get("date")), parse_dt(item.get("first_seen"))
    if d and d <= hi:
        return item.get("date")
    if fs and fs <= hi:
        return item.get("first_seen")
    return item.get("date") or item.get("first_seen")


def merge_twins(items: list[dict]) -> list[dict]:
    """A podcast episode and its YouTube upload (same Central day, same title or same season/episode)
    are ONE entry: the episode, carrying the video as `_twin` (community.js mergeMediaTwins)."""
    def norm(s: Any) -> str:
        return one_line(s).lower()

    def se(i: dict) -> str:
        ex = i.get("extra") or {}
        return f"{ex['season']}|{ex['episode']}" if ex.get("season") is not None and ex.get("episode") is not None else ""

    eps = [i for i in items if i.get("kind") == "episode"]
    if not eps:
        return items
    twin_of: dict[int, dict] = {}
    taken: set[int] = set()
    for v in items:
        if v.get("kind") != "video" or v.get("source") == "drive":
            continue
        day, t, k = central_day(v["_when"]), norm(v.get("title")), se(v)
        for e in eps:
            if id(e) in taken or central_day(e["_when"]) != day:
                continue
            if (t and norm(e.get("title")) == t) or (k and se(e) == k):
                twin_of[id(v)] = e
                taken.add(id(e))
                break
    if not twin_of:
        return items
    videos = {id(e): v for v in items if id(v) in twin_of for e in [twin_of[id(v)]]}
    return [({**i, "_twin": videos[id(i)]} if id(i) in videos else i) for i in items if id(i) not in twin_of]


def month_news(now: datetime, ed: dict) -> dict[str, list[dict]]:
    """Last month's news by group, newest first (announcements: pinned first)."""
    today = to_central(now).date().isoformat()
    hi = now + timedelta(days=1)
    found: dict[str, dict] = {}

    def add(raw: dict, from_whatsnew: bool) -> None:
        iid = raw.get("id")
        if not iid or raw.get("status") == "gone" or iid in found or raw.get("kind") in ("topic", "meeting"):
            return
        g = group_of(raw)
        if g not in NEWS_GROUPS:
            return
        when = news_date(raw, now, from_whatsnew)
        t = parse_dt(when)
        if not t or t > hi:
            return
        day = central_day(when)
        if not day or not (ed["prev_first"] <= day <= ed["prev_last"]):
            return
        exp = (raw.get("extra") or {}).get("expires")
        if raw.get("kind") == "announcement" and exp and str(exp)[:10] < today:
            return
        found[iid] = {**raw, "_when": when, "_group": g}

    for it in load_items("whatsnew"):
        add(it, True)
    for name in NEWS_SOURCES:
        for it in load_items(name):
            add(it, False)
    items = sorted(found.values(), key=lambda i: js_order(i.get("id")))
    items.sort(key=lambda i: parse_dt(i["_when"]), reverse=True)
    items = merge_twins(items)
    out = {g: [i for i in items if i["_group"] == g] for g in NEWS_GROUPS}
    out["announcement"].sort(key=lambda i: not (i.get("extra") or {}).get("pinned"))
    return out


def gv_theme(ed: dict, lang: str, issue: dict | None = None) -> str:
    """This month's Grapevine theme — the /monthly/ month model's rule (monthly.js), so the e-mail,
    the website and the district report give the issue ONE name: the theme the issue itself carries
    once it is out (`issue`, its entry in articles.json issues[]), else the editorial calendar's."""
    own = tr(issue, "theme", lang)
    if own:
        return own
    themed = [i for i in load_items("editorial")
              if (i.get("extra") or {}).get("publication") == "gv" and (i.get("extra") or {}).get("issue_key") == ed["key"]]
    return " / ".join(t for t in (tr(i, "title", lang) for i in themed) if t)


def email_image(path: str) -> str:
    """A picture every mail program shows. Classic Outlook for Windows shows no WebP, so one of the
    site's WebP thumbnails (a magazine cover) goes out as the JPEG / PNG copy made next to it
    (scripts/sync/articles.py email_copy) — or not at all (""), never as a blank box."""
    if not path or not path.lower().endswith(".webp"):
        return path or ""
    if path.startswith(("http://", "https://")):
        return ""
    for ext in (".jpg", ".png"):
        alt = path[: -len(".webp")] + ext
        if (ASSET_DIR / alt.lstrip("/")).is_file():
            return alt
    return ""


def month_issues(ed: dict, n: int) -> list[dict]:
    """The magazine issues on the stands in the edition's month (community.js monthIssues)."""
    data = load_file("articles")
    arts = [a for a in (data.get("items") or []) if isinstance(a, dict) and a.get("kind") == "article"
            and a.get("status") != "gone" and a.get("url") and (a.get("extra") or {}).get("issue_key")]

    def pub_of(a: dict) -> str:
        return a["extra"].get("publication") or a.get("category") or ""

    out = []
    for pub in ("gv", "lv"):
        key = next((k for k in (ed["key"], month_add(ed["key"], -1))
                    if any(pub_of(a) == pub and a["extra"]["issue_key"] == k for a in arts)), None)
        if not key:
            continue
        lst = [a for a in arts if pub_of(a) == pub and a["extra"]["issue_key"] == key]
        meta = next((i for i in (data.get("issues") or []) if isinstance(i, dict)
                     and i.get("publication") == pub and i.get("key") == key), None) or {}
        first = lst[0]

        def theme(lang: str) -> str:
            cal = gv_theme(ed, lang, meta) if pub == "gv" and key == ed["key"] else ""
            return one_line(cal or ((meta.get("i18n") or {}).get("theme") or {}).get(lang)
                            or ((first.get("i18n") or {}).get("issue_theme") or {}).get(lang)
                            or meta.get("theme") or first["extra"].get("issue_theme") or first["extra"].get("topic"))

        def label(lang: str) -> str:
            return in_sentence(one_line(((meta.get("i18n") or {}).get("label") or {}).get(lang)) or issue_label(first, lang) or key, lang)

        ranked = sorted(enumerate(lst), key=lambda x: (x[1]["extra"].get("free") is not True, is_department(x[1]),
                                                       scope_rank(x[1]), x[0]))
        out.append({
            "pub": pub, "key": key, "is_lv": pub == "lv", "name": "La Viña" if pub == "lv" else "Grapevine",
            "label": {"en": label("en"), "es": label("es")}, "theme": {"en": theme("en"), "es": theme("es")},
            "url": meta.get("url") or first["extra"].get("issue_url") or "", "cover": meta.get("cover") or "",
            "count": len(lst), "free": sum(1 for a in lst if a["extra"].get("free") is True),
            "highlights": [a for _, a in ranked[:n]],
        })
    return out


def month_tips(ed: dict, carry: dict) -> dict[str, list[dict]]:
    """Up to 3 "put it to work" tips for this month's Grapevine issue (config/carry.yml, as on /monthly/)."""
    ways = carry.get("ways") or {}
    out: dict[str, list[dict]] = {"en": [], "es": []}
    for tip in (carry.get("tips") or {}).get(ed["key"]) or []:
        w = ways.get(str((tip or {}).get("way")))
        if not w:
            continue
        for lang in ("en", "es"):
            def pair(p: Any) -> str:
                return str((p or {}).get(lang) or (p or {}).get("en") or (p or {}).get("es") or "").strip() if isinstance(p, dict) else str(p or "")
            out[lang].append({"title": pair(w.get("title")), "text": pair(tip.get("text")), "icon": w.get("icon") or ""})
    return {k: v[:3] for k, v in out.items()}


def event_start(it: dict) -> str | None:
    return (it.get("extra") or {}).get("start") or it.get("date")


def event_last_day(it: dict) -> date | None:
    ex = it.get("extra") or {}
    s = event_start(it)
    first = central_day(s)
    end = ex.get("end")
    if end:
        last = date.fromisoformat(end.strip()) if is_date_only(end) else (
            to_central(parse_dt(end) - timedelta(microseconds=1)).date() if parse_dt(end) else None)
    else:
        last = first
    return last if last and first and last > first else first


def event_over_at(it: dict) -> datetime | None:
    """When an event is over (community.js eventEndMs): the midnight after its last day for an
    all-day event; a timed event at its end (no end: TIMED_NO_END_HOURS after it starts)."""
    ex = it.get("extra") or {}
    s = event_start(it)
    st = parse_dt(s)
    if not st:
        return None
    end = ex.get("end")
    if end and not is_date_only(end) and parse_dt(end):
        return parse_dt(end)
    if end or ex.get("all_day") or is_date_only(s):
        last = event_last_day(it)
        return end_of_day(last) if last else None
    return st + timedelta(hours=TIMED_NO_END_HOURS)


def month_events(now: datetime, ed: dict) -> list[dict]:
    """Events of the edition's month that are not over yet — the committee meeting has its own box,
    a monthly series (the CityWide booth) once — soonest first."""
    rows = []
    for it in load_items("events"):
        if it.get("status") == "gone" or it.get("category") == "committee":
            continue
        st, first, last, over = parse_dt(event_start(it)), central_day(event_start(it)), event_last_day(it), event_over_at(it)
        if not st or not first or not last or not over or over < now or first > ed["last"] or last < ed["first"]:
            continue
        rows.append((st, it))
    rows.sort(key=lambda x: x[0])
    out, series = [], set()
    for _, it in rows:
        if is_recurring(it):
            s = str((it.get("extra") or {}).get("series") or it.get("id"))
            if s in series:
                continue
            series.add(s)
        out.append(it)
    return out


def next_meeting(now: datetime) -> dict | None:
    """The next committee meeting that is not over (up to 60 days ahead)."""
    best = None
    for it in load_items("events"):
        if it.get("category") != "committee" or it.get("status") == "gone":
            continue
        st, over = parse_dt(event_start(it)), event_over_at(it)
        if st and over and over >= now and st <= now + timedelta(days=60) and (best is None or st < best[0]):
            best = (st, it)
    return best[1] if best else None


def short_date(d: date, lang: str) -> str:
    """"Nov 5" / "5 de noviembre" (monthly.js shortDate)."""
    return f"{d.day} de {MONTHS['es'][d.month - 1]}" if lang == "es" else f"{MONTHS_SHORT['en'][d.month - 1]} {d.day}"


def weekly_open(ed: dict) -> dict[str, list[dict]]:
    """The weekly open meetings (monthly.js: La Viña's only from its start date), the page
    language's magazine first."""
    out: dict[str, list[dict]] = {"en": [], "es": []}
    for w in load_items("weekly_open"):
        if w.get("status") == "gone":
            continue
        starts = str((w.get("extra") or {}).get("starts") or "")
        if is_date_only(starts) and date.fromisoformat(starts) > ed["last"]:
            continue
        pub = "lv" if w.get("source") == "lavina" else "gv"
        for lang in ("en", "es"):
            label = short_date(date.fromisoformat(starts), lang) if is_date_only(starts) and date.fromisoformat(starts) >= ed["first"] else ""
            title = tr(w, "title", lang)
            note = other_lang(pub, lang)          # "(en inglés)" / "(in Spanish)" unless the title says it
            if note and note.strip().lower() not in title.lower():
                title += note
            # the schedule follows other words here: "… — los miércoles a las 11:00 a. m. (hora del Centro)"
            when = tr(w, "when", lang) or tr(w, "day", lang)
            out[lang].append({"pub": pub, "title": title, "when": lc_first(when) if lang == "es" else when, "starts": label})
    for lang in out:
        mine = "lv" if lang == "es" else "gv"
        out[lang].sort(key=lambda r: r["pub"] != mine)
    return out


def lv_topics(ed: dict) -> dict[str, list[dict]]:
    """La Viña's suggested topics (no deadline): 3, rotating by month (monthly.js lvTopics)."""
    topics = sorted((i for i in load_items("editorial")
                     if (i.get("extra") or {}).get("publication") == "lv" and (i.get("extra") or {}).get("evergreen")),
                    key=lambda i: js_order(i.get("id")))
    picked: list[dict] = []
    if topics:
        idx = int(ed["key"][:4]) * 12 + int(ed["key"][5:7])
        for i in range(min(3, len(topics))):
            it = topics[(idx * 3 + i) % len(topics)]
            if it not in picked:
                picked.append(it)
    return {lang: [{"text": tr(i, "title", lang), "es": tr(i, "title", "es")} for i in picked] for lang in ("en", "es")}


def story_deadlines(now: datetime, ed: dict) -> list[dict]:
    """Story deadlines from today (Central) through the end of next month."""
    today = to_central(now).date().isoformat()
    last = ed["next_last"].isoformat()
    out = [i for i in load_items("editorial") if i.get("status") != "gone" and is_date_only((i.get("extra") or {}).get("deadline"))
           and today <= i["extra"]["deadline"] <= last]
    out.sort(key=lambda i: (i["extra"]["deadline"], js_order(one_line(i.get("title")))))
    return out


def month_writers(ed: dict) -> dict[str, list[dict]]:
    """Stories by writers from Area 65 / the rest of Texas published last month (spotlight.json,
    extra.pub_date in the previous calendar month) — community.js writersPick with since/until."""
    a, b = ed["prev_first"].isoformat(), ed["prev_last"].isoformat()
    out: dict[str, list[dict]] = {"neta65": [], "texas": []}
    seen: set[str] = set()
    for it in load_items("spotlight"):
        ex = it.get("extra") or {}
        scope = (ex.get("geo") or {}).get("scope")
        pd = ex.get("pub_date")
        key = it.get("id") or it.get("url")
        if (it.get("status") == "gone" or it.get("kind") != "article" or not it.get("url") or scope not in out
                or not is_date_only(pd) or not a <= pd <= b or key in seen):
            continue
        seen.add(key)
        out[scope].append(it)
    for lst in out.values():
        lst.sort(key=lambda i: js_order(one_line(i.get("title"))))
        lst.sort(key=lambda i: i["extra"]["pub_date"], reverse=True)
    return out


def gv_meetings() -> dict[str, int]:
    """How many Grapevine meetings meet every week in our Area and nearby (committee.js gvMeetings)."""
    ours = nearby = 0
    for it in load_items("meetings"):
        try:
            int(it.get("day"))
        except (TypeError, ValueError):
            continue
        if it.get("attendance") == "inactive":
            continue
        if it.get("in_area"):
            ours += 1
        else:
            nearby += 1
    return {"in_area": ours, "nearby": nearby}


def audio_lines() -> dict[str, dict]:
    """The magazines' record-your-story phone lines (data/site/audio_project.json)."""
    ap = load_file("audio_project")
    return {p: {"phone": str(d["phone"]), "tel": str(d["tel"])} for p in ("gv", "lv")
            if isinstance(d := ap.get(p), dict) and d.get("phone") and d.get("tel")}


def subs_from() -> float | None:
    """The lowest MONTH-TO-MONTH subscription price (shop.js shopFromMonthly: $2.99, digital), shown as
    exactly that ("Month-to-month subscriptions from $2.99") — never as "subscriptions from … a month":
    a yearly plan costs less per month. None without a month-to-month plan (the line is left out)."""
    plans = [p for s in (load_file("shop").get("subscriptions") or []) if isinstance(s, dict)
             for p in (s.get("plans") or []) if isinstance(p, dict) and isinstance(p.get("price"), (int, float)) and p["price"] > 0]
    monthly = [float(p["price"]) for p in plans if p.get("term_months") == 1]
    return min(monthly) if monthly else None


def collect(now: datetime, edition: str | None = None, max_per: int = 5, highlights: int = 3) -> dict:
    ed = edition_of(now, edition)
    today = to_central(now).date().isoformat()
    data: dict[str, Any] = {"edition": ed, "now": now, "month": ed["key"], "machine": {"en": False, "es": False}}
    data["groups"] = month_news(now, ed)
    data["issues"] = month_issues(ed, highlights)
    data["tips"] = month_tips(ed, load_carry())
    data["writers"] = month_writers(ed)
    data["meeting"] = next_meeting(now)
    data["events"] = month_events(now, ed)
    data["weekly"] = weekly_open(ed)
    data["gvm"] = gv_meetings()
    data["deadlines"] = story_deadlines(now, ed)
    data["lv_topics"] = lv_topics(ed)
    data["audio"] = audio_lines()
    # Book of the Month (a compact teaser: the prices and dates live on the site's /shop/#botm).
    # An offer whose last day has passed (Central time) is left out, like the website.
    data["botm"] = [b for b in load_botm()
                    if b.get("url") and isinstance(b.get("sale_price"), (int, float))
                    and not (is_date_only(b.get("ends")) and str(b["ends"]) < today)]
    data["subs_from"] = subs_from()
    data["quote"] = any(isinstance(q, dict) and q.get("text") for q in load_items("quote"))
    profiles = load_file("instagram").get("profiles") or {}
    data["instagram"] = [str(p["username"]).lstrip("@") for k in ("gv", "lv")
                         if isinstance(p := profiles.get(k), dict) and p.get("username")]
    # which languages carry machine translations (for the small footnote)
    # (Book of the Month titles are shown as sold, never translated, so they bring no footnote)
    shown = ([i for g in data["groups"].values() for i in g[:max_per]] + data["events"]
             + [a for iss in data["issues"] for a in iss["highlights"]]
             + [w for lst in data["writers"].values() for w in lst[:max_per]])
    for lang in ("en", "es"):
        data["machine"][lang] = any(is_machine(i, lang) for i in shown)
    return data


def total_count(data: dict) -> int:
    """How much the edition has to tell about last month — nothing means no e-mail. This month's
    issues, dates and deadlines come round every month (a monthly event like the booth too), so on
    their own they never turn a quiet month (or a site whose updates stopped) into an e-mail."""
    return sum(len(v) for v in data["groups"].values()) + sum(len(v) for v in data["writers"].values())


def news_total(data: dict) -> int:
    return sum(len(v) for v in data["groups"].values())


def count_list(data: dict, lang: str) -> str:
    """"9 podcast episodes, 2 videos and 8 documents" (community.js digestCountList)."""
    parts = []
    for g in ("article", "episode", "video", "pdf", "drive", "announcement"):
        n = len(data["groups"].get(g) or [])
        if n:
            many, one = T[lang]["n"][g]
            parts.append(one if n == 1 else many.format(n=n))
    if len(parts) < 2:
        return parts[0] if parts else ""
    return f"{', '.join(parts[:-1])} {T[lang]['and']} {parts[-1]}"


def intro(data: dict, lang: str) -> str:
    ed = data["edition"]
    v = {"prev": month_word(ed["prev"], lang), "month": month_word(ed["key"], lang)}
    lst = count_list(data, lang)
    return T[lang]["intro"].format(list=lst, **v) if lst else T[lang]["intro_quiet"].format(**v)


def gvm_text(g: dict, lang: str) -> str:
    if not g.get("in_area"):
        return ""
    many, one = T[lang]["meetings_n"]
    ours = one if g["in_area"] == 1 else many.format(n=g["in_area"])
    return (T[lang]["gvm_text"] if g.get("nearby") else T[lang]["gvm_text_area"]).format(ours=ours, nearby=g.get("nearby"))


# ---------------------------------------------------------------------------- rows (shared by HTML + text)
def build_rows(group: str, items: list[dict], lang: str, links: Links, page: str, max_per: int) -> tuple[list[dict], int]:
    """Turn items into display rows. Drive photos collapse to one row per album, so a big upload
    doesn't flood the e-mail; a podcast episode carries its YouTube twin's link."""
    t = T[lang]
    rows: list[dict] = []
    photos: dict[str, list[dict]] = {}
    for it in items:
        ex = it.get("extra") or {}
        if group == "drive" and (it.get("kind") == "photo" or ex.get("is_image")) and it.get("category") in (None, "", "photos", "other"):
            photos.setdefault(ex.get("album") or DRIVE_CATEGORIES["photos"][1 if lang == "es" else 0], []).append(it)
            continue
        label, fg, bg = item_label(it, lang)
        twin = it.get("_twin")
        rows.append({"label": label, "fg": fg, "bg": bg, "title": title_of(it, lang),
                     "url": links.item(it, lang, page), "meta": item_meta(it, lang),
                     "twin": links.item(twin, lang, "/watch/") if twin else ""})
    for album, its in photos.items():
        # What's New already merges one album's photos from one day into a single item
        # ("5 new photos in …", extra.count = 5): count photos, not items.
        n = sum(photo_count(it) for it in its)
        # the album name in this language (a photo group carries i18n.album), else as written in Drive
        names = [tx_extra(it, "album", lang) for it in its if (it.get("i18n") or {}).get("album")]
        name = next((v for v in names if v), album)
        rows.append({"label": DRIVE_CATEGORIES["photos"][1 if lang == "es" else 0], "fg": C["vine"], "bg": C["vine_soft"],
                     "title": t["album"].format(name=name), "url": links.page("/photos/", lang),
                     "meta": t["new_photo"] if n == 1 else t["new_photos"].format(n=n), "twin": ""})
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
                     "bg": C["lv_soft"] if lv else C["gv_soft"],
                     # the title the book is sold under (never a translation: no such edition exists)
                     "title": str(b.get("title") or "").strip() or tx(b, "title", lang),
                     "url": b["url"], "price": fmt_money(sale), "regular": regular,
                     "until": t["botm_until"].format(date=fmt_month_day(b["ends"], lang)) if is_date_only(b.get("ends")) else ""})
    ym = data.get("month") or to_central(data["now"]).strftime("%Y-%m")
    return {"title": t["botm_title"].format(pct=pcts.pop()) if len(pcts) == 1 else t["botm_title_plain"],
            "rows": rows, "more_url": links.page("/shop/", lang) + "#botm",
            "month_label": t["toolkit"].format(month=month_label(ym, lang)), "month_url": links.page(f"/monthly/{ym}/", lang)}


def ordered_issues(data: dict, lang: str) -> list[dict]:
    """La Viña first in the Spanish half, Grapevine first in the English half."""
    return sorted(data["issues"], key=lambda i: i["is_lv"] != (lang == "es"))


def writer_name(item: dict, lang: str) -> str:
    a = one_line((item.get("extra") or {}).get("author"))
    return T[lang]["anonymous"] if not a or re.fullmatch(r"(?i)anonymous|an[oó]nim[oa]|anon\.?", a) else a


def writer_place(item: dict, lang: str) -> str:
    g = (item.get("extra") or {}).get("geo") or {}
    return one_line(g.get("label_es") if lang == "es" else g.get("label_en")) or one_line(g.get("label_en")) \
        or one_line((item.get("extra") or {}).get("author_location"))


# ---------------------------------------------------------------------------- HTML
def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def subject_of(data: dict, cfg: dict) -> str:
    """"Grapevine / La Viña — October 2026 edition · Edición de octubre de 2026" — the edition's
    name in both languages (its news is from the month before, so never "Novedades de octubre")."""
    title = (cfg.get("site") or {}).get("title") or "Grapevine / La Viña"
    k = data["edition"]["key"]
    return (f"{title} — {T['en']['edition'].format(month=month_label(k, 'en'))}"
            f" · {T['es']['edition'].format(month=month_label(k, 'es'))}")


def render_html(data: dict, cfg: dict, links: Links, max_per: int, subject: str) -> str:
    site = cfg.get("site") or {}
    title = site.get("title") or "Grapevine / La Viña"
    logo = links.base + "/assets/img/logo-180x180.png"
    ed = data["edition"]
    teaser = [month_label(ed["key"], "en")]
    teaser += [f"{iss['name']} “{iss['theme']['en']}”" for iss in data["issues"] if iss["theme"]["en"]]
    teaser += [count_list(data, "en")] if news_total(data) else []
    preheader = shorten(" · ".join(x for x in teaser if x), 140)

    sections = [render_lang_html(lang, data, cfg, links, max_per) for lang in ("en", "es")]
    divider = f'<tr><td style="padding:0 32px;"><div style="border-top:2px dashed {C["line"]};height:1px;line-height:1px;">&nbsp;</div></td></tr>'
    committee = site.get("committee") or title
    committee_es = site.get("committee_es") or committee
    contact = site.get("contact_email") or ""
    footer = f"""
<tr><td style="padding:24px 32px 28px;background:{C['surface2']};border-radius:0 0 12px 12px;font-size:12px;line-height:1.6;color:{C['muted']};">
  <p style="margin:0 0 8px;">{_esc(T['en']['footer_why'].format(committee=committee))} {_esc(T['en']['footer_anon'])}</p>
  <p lang="es" style="margin:0 0 8px;">{_esc(T['es']['footer_why'].format(committee=committee_es))} {_esc(T['es']['footer_anon'])}</p>
  <p style="margin:0 0 8px;">{_esc(T['en']['footer_unsub'])} · <span lang="es">{_esc(T['es']['footer_unsub'])}</span></p>
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
      <div style="font-family:Georgia,'Times New Roman',serif;font-size:22px;line-height:1.2;color:#ffffff;font-weight:bold;">{_esc(title)} · {_esc(T['en']['masthead'])}</div>
      <div style="font-size:13px;color:#cfe3f6;margin-top:3px;">{_esc(committee)} · {_esc(month_label(ed['key'], 'en'))}</div>
    </td></tr></table>
</td></tr>
<tr><td style="padding:12px 32px;background:{C['gv_soft']};font-size:13px;color:{C['gv_strong']};">
  English first · <strong lang="es">Versión en español más abajo</strong>
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
    ed = data["edition"]
    meeting_cfg = cfg.get("meeting") or {}
    prev_w, month_w = month_word(ed["prev"], lang), month_word(ed["key"], lang)
    parts: list[str] = []
    h2 = f"font-family:Georgia,'Times New Roman',serif;font-size:26px;line-height:1.2;margin:0 0 4px;color:{C['ink']};"
    h3 = (f"font-family:Georgia,'Times New Roman',serif;font-size:18px;margin:0 0 10px;color:{C['ink']};"
          f"border-left:4px solid {{color}};padding-left:10px;")
    link_style = f"color:{C['ink']};text-decoration:none;font-weight:600;"
    small = f"font-size:12px;color:{C['faint']};margin-top:3px;"

    def pill(label: str, fg: str, bg: str) -> str:
        return (f'<span style="display:inline-block;font-size:11px;font-weight:bold;letter-spacing:.02em;color:{fg};'
                f'background:{bg};border-radius:999px;padding:2px 8px;margin-right:6px;vertical-align:1px;">{_esc(label)}</span>') if label else ""

    def table(rows: list[str]) -> str:
        return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table>'

    def row(inner: str) -> str:
        return f'<tr><td style="padding:8px 0;border-bottom:1px solid {C["line"]};font-size:15px;line-height:1.4;">{inner}</td></tr>'

    def section(heading: str, color: str, body: str, page: str | None = None, extra: int = 0, count: int | None = None,
                label: str = "") -> str:
        more = ""
        if page:
            more_txt = label or (t["more"].format(n=extra) if extra else t["see_all"])
            more = (f'<p style="margin:8px 0 0;font-size:13px;"><a href="{_esc(links.page(page, lang))}" '
                    f'style="color:{C["gv"]};">{_esc(more_txt)} →</a></p>')
        n = f' <span style="font-family:Arial,sans-serif;font-size:12px;color:{C["faint"]};font-weight:normal;">({count})</span>' if count else ""
        return f"""<tr><td style="padding:22px 32px 4px;">
  <h3 style="{h3.format(color=color)}">{_esc(heading)}{n}</h3>
  {body}{more}
</td></tr>"""

    # ---- headline
    parts.append(f"""<tr><td style="padding:28px 32px 6px;">
  <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{C['faint']};font-weight:bold;">{_esc(t['lang_name'])} · {_esc(t['masthead'])}</div>
  <h2 style="{h2}">{_esc(t['edition'].format(month=month_label(ed['key'], lang)))}</h2>
  <p style="margin:0 0 10px;font-size:13px;color:{C['gv']};font-weight:bold;">{_esc(t['edition_sub'].format(prev=prev_w, month=month_w))}</p>
  <p style="margin:0;font-size:15px;line-height:1.6;color:{C['ink']};">{_esc(intro(data, lang))}</p>
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
    <a href="{_esc(links.page('/meetings/', lang))}" style="font-size:13px;color:{C['gv']};margin-left:10px;">{_esc(t['details'])} →</a>
  </td></tr></table>
</td></tr>""")

    # ---- announcements (last month, still current)
    ann = data["groups"]["announcement"]
    if ann:
        body = []
        for a in ann[:max_per]:
            text = tx(a, "body_md", lang) or tx(a, "summary", lang)
            short = text if len(text) <= 700 else shorten(md_to_text(text), 600)
            body.append(f"""<div style="margin:0 0 14px;">
  <div style="font-size:16px;font-weight:bold;margin:0 0 4px;"><a href="{_esc(links.item(a, lang, '/announcements/'))}" style="{link_style}">{_esc(tx(a, 'title', lang))}</a></div>
  <div style="font-size:14px;line-height:1.6;color:{C['ink']};">{md_to_html(short, C['gv'])}</div>
</div>""")
        parts.append(section(t["announcement"], C["vine"], "".join(body), "/announcements/", max(0, len(ann) - max_per), len(ann)))

    # ---- this month in the magazines + put it to work + the toolkit
    bm = botm_block(data, lang, links)
    body = []
    for iss in ordered_issues(data, lang):
        color = C["lv"] if iss["is_lv"] else C["gv"]
        pic = email_image(iss["cover"])
        cover = (f'<td width="64" style="padding:0 14px 0 0;vertical-align:top;"><img src="{_esc(links.asset(pic))}" width="64" alt="" '
                 f'style="display:block;width:64px;height:auto;border:0;border-radius:6px;"></td>') if pic else ""
        many, one = t["stories"]
        count = one if iss["count"] == 1 else many.format(n=iss["count"])
        if iss["free"]:
            count += " · " + t["free_n"].format(n=iss["free"])
        theme = f'<div style="font-family:Georgia,serif;font-size:19px;font-weight:bold;margin:4px 0 2px;color:{C["ink"]};">“{_esc(iss["theme"][lang])}”</div>' if iss["theme"][lang] else ""
        hl = []
        for a in iss["highlights"][:max_per]:
            by = writer_name(a, lang) if (a.get("extra") or {}).get("author") else ""
            place = writer_place(a, lang) if by else ""
            meta = " · ".join(x for x in (by, place, t["free"] if (a.get("extra") or {}).get("free") is True else t["subscriber"]) if x)
            hl.append(f'<tr><td style="padding:5px 0 5px 12px;border-left:2px solid {color};font-size:14px;line-height:1.4;">'
                      f'<a href="{_esc(a["url"])}" style="{link_style}">{_esc(title_of(a, lang))}</a>'
                      f'<div style="{small}">{_esc(meta)}</div></td></tr>')
        body.append(f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 16px;"><tr>
  {cover}<td style="vertical-align:top;">
    {pill(iss['name'], color, C['lv_soft'] if iss['is_lv'] else C['gv_soft'])}<span style="font-size:13px;font-weight:bold;color:{C['ink']};">{_esc(iss['label'][lang])}</span>{f'<span style="font-size:12px;color:{C["muted"]};">{_esc(other_lang(iss["pub"], lang))}</span>' if other_lang(iss["pub"], lang) else ""}
    {theme}
    <div style="font-size:12px;color:{C['muted']};margin-bottom:8px;">{_esc(count)}</div>
    {table(hl)}
    <p style="margin:8px 0 0;font-size:13px;"><a href="{_esc(links.page('/read/', lang))}" style="color:{C['gv']};">{_esc(t['issue_more'].format(n=iss['count']))} →</a></p>
  </td></tr></table>""")
    tips = data["tips"].get(lang) or []
    tip_rows = "".join(
        f'<tr><td style="padding:6px 0;font-size:14px;line-height:1.5;"><strong style="color:{C["ink"]};">{_esc(tip["title"])}</strong>'
        f'<div style="color:{C["muted"]};">{_esc(tip["text"])}</div></td></tr>' for tip in tips)
    tips_html = (f'<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{C["lv_strong"]};font-weight:bold;">{_esc(t["tips"])}</div>'
                 f'<div style="font-size:12px;color:{C["muted"]};margin:2px 0 4px;">{_esc(t["tips_sub"])}</div>{table([tip_rows])}') if tips else ""
    body.append(f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{C['surface2']};border-radius:10px;">
  <tr><td style="padding:14px 16px;">{tips_html}
    <p style="margin:{10 if tips else 0}px 0 0;font-size:14px;"><a href="{_esc(bm['month_url'])}" style="color:{C['gv']};font-weight:bold;">{_esc(bm['month_label'])} →</a></p>
  </td></tr></table>""")
    parts.append(section(t["issues"], C["gv"], "".join(body)))

    # ---- writers from Area 65 / Texas, published last month
    W = data["writers"]
    n_writers = sum(len(v) for v in W.values())
    if n_writers:
        body = [f'<p style="margin:0 0 6px;font-size:13px;color:{C["muted"]};">{_esc(t["writers_sub"].format(prev=prev_w))}</p>']
        for key in ("neta65", "texas"):
            lst = W[key]
            if not lst:
                continue
            body.append(f'<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{C["faint"]};font-weight:bold;margin:10px 0 2px;">{_esc(t["group_" + key])}</div>')
            rows = []
            for it in lst[:max_per]:
                place = writer_place(it, lang)
                label, fg, bg = item_label(it, lang)
                issue = issue_label(it, lang)
                meta = " · ".join(x for x in (writer_name(it, lang) + (f", {place}" if place else ""),
                                              (issue + other_lang(pub_of(it), lang)).strip()) if x)
                rows.append(row(f'{pill(label, fg, bg)}<a href="{_esc(it["url"])}" style="{link_style}">{_esc(tx(it, "title", lang))}</a><div style="{small}">{_esc(meta)}</div>'))
            body.append(table(rows))
        parts.append(section(t["writers"], C["lv"], "".join(body), "/published/", 0, n_writers))

    # ---- podcasts, videos, documents, committee files (last month)
    colors = {"episode": C["grape"], "video": C["grape"], "pdf": C["gv"], "drive": C["vine"]}
    for g, page in GROUPS:
        items = data["groups"].get(g) or []
        if not items:
            continue
        rows_data, extra = build_rows(g, items, lang, links, page, max_per)
        rows = []
        for r in rows_data:
            twin = f' · <a href="{_esc(r["twin"])}" style="color:{C["gv"]};">{_esc(t["twin"])}</a>' if r.get("twin") else ""
            meta = f'<div style="{small}">{_esc(r["meta"])}{twin}</div>' if (r["meta"] or twin) else ""
            rows.append(row(f'{pill(r["label"], r["fg"], r["bg"])}<a href="{_esc(r["url"])}" style="{link_style}">{_esc(r["title"])}</a>{meta}'))
        parts.append(section(t[g], colors[g], table(rows), page, extra, len(items)))
    if data["instagram"]:
        handles = " · ".join(f"@{h}" for h in data["instagram"])
        parts.append(f'<tr><td style="padding:14px 32px 0;font-size:14px;"><a href="{_esc(links.page("/instagram/", lang))}" style="color:{C["gv"]};font-weight:bold;">{_esc(t["instagram"])} →</a> <span style="color:{C["muted"]};font-size:13px;">{_esc(handles)}</span></td></tr>')
    if not total_count(data):
        parts.append(f'<tr><td style="padding:16px 32px 0;font-size:14px;color:{C["muted"]};">{_esc(t["nothing"].format(prev=prev_w))}</td></tr>')

    # ---- coming up this month
    rows = []
    for ev in data["events"]:
        r = event_row(ev, lang, links)
        rows.append(row(f'<a href="{_esc(r["url"])}" style="{link_style}">{_esc(r["title"])}</a>'
                        f'<div style="font-size:13px;color:{C["muted"]};margin-top:2px;">{_esc(r["when"])}{(" · " + _esc(r["where"])) if r["where"] else ""}</div>'))
    if not rows:
        rows.append(row(f'<span style="font-size:14px;color:{C["muted"]};">{_esc(t["no_events"].format(month=month_w))}</span>'))
    for w in data["weekly"].get(lang) or []:
        starts = f' · {t["starting"].format(date=w["starts"])}' if w["starts"] else ""
        rows.append(row(f'<a href="{_esc(links.page("/meetings/", lang) + "#weekly-open")}" style="{link_style}">{_esc(w["title"])}</a>'
                        f'<div style="font-size:13px;color:{C["muted"]};margin-top:2px;"><strong style="color:{C["grape"]};">{_esc(t["every_week"])}</strong> · {_esc(w["when"])}{_esc(starts)}</div>'))
    gt = gvm_text(data["gvm"], lang)
    if gt:
        rows.append(row(f'<a href="{_esc(links.page("/meetings/", lang) + "#grapevine-meetings")}" style="{link_style}">{_esc(t["gvm"])}</a>'
                        f'<div style="font-size:13px;color:{C["muted"]};margin-top:2px;">{_esc(gt)}</div>'))
    body = table(rows) + f'<p style="margin:8px 0 0;font-size:13px;"><a href="{_esc(links.page("/events/", lang))}" style="color:{C["gv"]};">{_esc(t["full_calendar"])} →</a></p>'
    parts.append(section(t["coming"].format(month=month_w), C["vine"], body))

    # ---- share your story: deadlines through next month, La Viña's topics, the phone lines
    topics = data["lv_topics"].get(lang) or []
    if data["deadlines"] or topics or data["audio"]:
        rows = []
        for d in data["deadlines"]:
            lv = (d.get("extra") or {}).get("publication") == "lv"
            due = t["due"].format(date=fmt_month_day(d["extra"]["deadline"], lang))
            # "May 2027 issue · Due October 1" / "Edición de mayo de 2027 · Fecha límite: 1 de octubre"
            issue = issue_label(d, lang)
            meta_issue = f'<span style="color:{C["muted"]};">{_esc(t["issue_of"].format(issue=issue))}</span> · ' if issue else ""
            rows.append(row(f'{pill("La Viña" if lv else "Grapevine", C["lv"] if lv else C["gv"], C["lv_soft"] if lv else C["gv_soft"])}'
                            f'<strong>{_esc(tx(d, "title", lang))}</strong><div style="font-size:13px;margin-top:2px;">'
                            f'{meta_issue}<strong style="color:{C["lv_strong"]};">{_esc(due)}</strong></div>'))
        body = table(rows) if rows else ""
        if topics:
            items = "".join(f'<li style="margin:2px 0;">“{_esc(x["text"])}”' + (f' <span lang="es" style="color:{C["muted"]};font-style:italic;">— {_esc(x["es"])}</span>' if lang != "es" and x["es"] != x["text"] else "") + "</li>" for x in topics)
            body += (f'<div style="background:{C["lv_soft"]};border-radius:10px;padding:12px 14px;margin-top:10px;font-size:14px;">'
                     f'<div style="color:{C["ink"]};">{_esc(t["lv_anytime"])}</div><ul style="margin:6px 0 0;padding-left:18px;font-weight:bold;">{items}</ul></div>')
        if data["audio"]:
            pubs = ("lv", "gv") if lang == "es" else ("gv", "lv")
            phones = " · ".join(f'{"La Viña" if p == "lv" else "Grapevine"}: <a href="tel:{_esc(data["audio"][p]["tel"])}" style="color:{C["gv"]};font-weight:bold;">{_esc(data["audio"][p]["phone"])}</a>'
                                for p in pubs if p in data["audio"])
            body += (f'<div style="background:{C["surface2"]};border-radius:10px;padding:12px 14px;margin-top:10px;font-size:14px;">'
                     f'<strong>{_esc(t["record"])}</strong><div style="margin-top:4px;">{phones}</div>'
                     f'<div style="margin-top:4px;font-size:13px;"><a href="{_esc(links.page("/contribute/", lang) + "#record")}" style="color:{C["gv"]};">{_esc(t["record_how"])} →</a></div></div>')
        parts.append(section(t["story"], C["lv"], body, "/contribute/", label=t["story_cta"]))

    # ---- Book of the Month (compact) + the lowest month-to-month subscription price
    if bm["rows"]:
        rows = []
        for r in bm["rows"]:
            meta = " · ".join(x for x in (
                f'<strong style="color:{C["ink"]};">{_esc(r["price"])}</strong>' + (f' {_esc(r["regular"])}' if r["regular"] else ""),
                _esc(r["until"])) if x)
            rows.append(row(f'{pill(r["label"], r["fg"], r["bg"])}<a href="{_esc(r["url"])}" style="{link_style}">{_esc(r["title"])}</a>'
                            f'<div style="font-size:13px;color:{C["muted"]};margin-top:3px;">{meta}</div>'))
        body = table(rows) + f'<p style="margin:8px 0 0;font-size:13px;"><a href="{_esc(bm["more_url"])}" style="color:{C["gv"]};">{_esc(t["botm_more"])} →</a></p>'
        parts.append(section(bm["title"], C["grape"], body))
    lines = []
    if data["subs_from"]:
        lines.append(f'<a href="{_esc(links.page("/shop/", lang) + "#subscriptions")}" style="color:{C["gv"]};font-weight:bold;">{_esc(t["subs_from"].format(amount=fmt_money(data["subs_from"])))} →</a>')
    if data["quote"]:
        lines.append(f'{_esc(t["quote"])}: <a href="{_esc(links.page("/", lang))}" style="color:{C["gv"]};font-weight:bold;">{_esc(t["quote_link"])} →</a>')
    if lines:
        parts.append(f'<tr><td style="padding:16px 32px 0;font-size:14px;line-height:1.6;">{"<br>".join(lines)}</td></tr>')

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
    ed = data["edition"]
    out: list[str] = []
    title = site.get("title") or "Grapevine / La Viña"
    out += [f"{title} — {T['en']['masthead']} · {T['es']['masthead']}", site.get("committee") or "",
            "English first · Versión en español más abajo", ""]

    def head(s: str) -> list[str]:
        return [s.upper(), "-" * min(len(s), 60)]

    for lang in ("en", "es"):
        t = T[lang]
        prev_w, month_w = month_word(ed["prev"], lang), month_word(ed["key"], lang)
        top = f"{t['masthead']} — {t['edition'].format(month=month_label(ed['key'], lang))}"
        out += ["=" * len(top), top, "=" * len(top), t["edition_sub"].format(prev=prev_w, month=month_w), "", intro(data, lang), ""]
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
            out.append(f"  {links.page('/meetings/', lang)}")
            out.append("")
        ann = data["groups"]["announcement"]
        if ann:
            out += head(f"{t['announcement']} ({len(ann)})")
            for a in ann[:max_per]:
                out.append(f"* {tx(a, 'title', lang)}")
                body = md_to_text(tx(a, "body_md", lang) or tx(a, "summary", lang))
                if body:
                    out.append("  " + shorten(body, 600))
            out += [f"  → {links.page('/announcements/', lang)}", ""]
        # this month in the magazines
        bm = botm_block(data, lang, links)
        if data["issues"]:
            out += head(t["issues"])
            for iss in ordered_issues(data, lang):
                many, one = t["stories"]
                count = one if iss["count"] == 1 else many.format(n=iss["count"])
                if iss["free"]:
                    count += " · " + t["free_n"].format(n=iss["free"])
                theme = f": “{iss['theme'][lang]}”" if iss["theme"][lang] else ""
                out.append(f"* {iss['name']} — {iss['label'][lang]}{other_lang(iss['pub'], lang)}{theme} ({count})")
                for a in iss["highlights"][:max_per]:
                    free = f" — {t['free']}" if (a.get("extra") or {}).get("free") is True else ""
                    out.append(f"  - “{title_of(a, lang)}”{free}")
                    out.append(f"    {a['url']}")
                out.append(f"  → {t['issue_more'].format(n=iss['count'])}: {links.page('/read/', lang)}")
            out.append("")
        tips = data["tips"].get(lang) or []
        if tips:
            out += head(t["tips"])
            for tip in tips:
                out.append(f"* {tip['title']}: {tip['text']}")
        out += [f"{bm['month_label']}: {bm['month_url']}", ""]
        # writers
        W = data["writers"]
        n_writers = sum(len(v) for v in W.values())
        if n_writers:
            out += head(f"{t['writers']} ({n_writers})")
            for key in ("neta65", "texas"):
                if not W[key]:
                    continue
                out.append(f"{t['group_' + key]}:")
                for it in W[key][:max_per]:
                    place = writer_place(it, lang)
                    # "(La Viña, September / October 2026, in Spanish)"
                    where = ", ".join(x for x in (item_label(it, lang)[0], issue_label(it, lang),
                                                  T[lang]["in_other"] if other_lang(pub_of(it), lang) else "") if x)
                    out.append(f"* “{tx(it, 'title', lang)}” — {writer_name(it, lang)}{', ' + place if place else ''} ({where})")
                    out.append(f"  {it['url']}")
            out += [f"  → {links.page('/published/', lang)}", ""]
        # last month's lists
        for g, page in GROUPS:
            items = data["groups"].get(g) or []
            if not items:
                continue
            rows, extra = build_rows(g, items, lang, links, page, max_per)
            out += head(f"{t[g]} ({len(items)})")
            for r in rows:
                label = f"[{r['label']}] " if r["label"] else ""
                meta = f" ({r['meta']})" if r["meta"] else ""
                out.append(f"* {label}{r['title']}{meta}")
                out.append(f"  {r['url']}")
                if r.get("twin"):
                    out.append(f"  {t['twin']}: {r['twin']}")
            more = t["more"].format(n=extra) if extra else t["see_all"]
            out += [f"  → {more}: {links.page(page, lang)}", ""]
        if data["instagram"]:
            out += [f"{t['instagram']}: {' · '.join('@' + h for h in data['instagram'])} — {links.page('/instagram/', lang)}", ""]
        if not total_count(data):
            out += [t["nothing"].format(prev=prev_w), ""]
        # coming up this month
        out += head(t["coming"].format(month=month_w))
        if not data["events"]:
            out.append(t["no_events"].format(month=month_w))
        for ev in data["events"]:
            r = event_row(ev, lang, links)
            out.append(f"* {r['title']} — {r['when']}{(' · ' + r['where']) if r['where'] else ''}")
            out.append(f"  {r['url']}")
        for w in data["weekly"].get(lang) or []:
            starts = f" ({t['starting'].format(date=w['starts'])})" if w["starts"] else ""
            out.append(f"* {t['every_week']}: {w['title']} — {w['when']}{starts}")
            out.append(f"  {links.page('/meetings/', lang)}#weekly-open")
        gt = gvm_text(data["gvm"], lang)
        if gt:
            out.append(f"* {t['gvm']}: {gt}")
            out.append(f"  {links.page('/meetings/', lang)}#grapevine-meetings")
        out += [f"  → {t['full_calendar']}: {links.page('/events/', lang)}", ""]
        # share your story
        topics = data["lv_topics"].get(lang) or []
        if data["deadlines"] or topics or data["audio"]:
            out += head(t["story"])
            for d in data["deadlines"]:
                pub = "La Viña" if (d.get("extra") or {}).get("publication") == "lv" else "Grapevine"
                out.append(f"* {t['due'].format(date=fmt_month_day(d['extra']['deadline'], lang))} — “{tx(d, 'title', lang)}” ({pub}, {issue_label(d, lang)})")
            if topics:
                out.append(f"* {t['lv_anytime']} " + ", ".join(f"“{x['text']}”" + (f" (“{x['es']}”)" if lang != "es" and x["es"] != x["text"] else "") for x in topics))
            if data["audio"]:
                pubs = ("lv", "gv") if lang == "es" else ("gv", "lv")
                out.append(f"* {t['record']}: " + " · ".join(f"{'La Viña' if p == 'lv' else 'Grapevine'} {data['audio'][p]['phone']}" for p in pubs if p in data["audio"]))
                out.append(f"  {links.page('/contribute/', lang)}#record")
            out += [f"  → {t['story_cta']}: {links.page('/contribute/', lang)}", ""]
        # Book of the Month + subscriptions + the daily quote
        if bm["rows"]:
            out += head(bm["title"])
            for r in bm["rows"]:
                price = f"{r['price']} {r['regular']}".strip()
                out.append(f"* [{r['label']}] {r['title']} — {price}{(' · ' + r['until']) if r['until'] else ''}")
                out.append(f"  {r['url']}")
            out.append(f"  → {t['botm_more']}: {bm['more_url']}")
        if data["subs_from"]:
            out.append(f"{t['subs_from'].format(amount=fmt_money(data['subs_from']))} — {links.page('/shop/', lang)}#subscriptions")
        if data["quote"]:
            out.append(f"{t['quote']}: {links.page('/', lang)}")
        out.append("")
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


# Network trouble worth another try — but only while connecting and logging in, never once the
# message itself is on its way (see send()).
TRANSIENT = (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, ConnectionError,
             TimeoutError, socket.gaierror)
NO_STARTTLS = ("The mail server does not offer STARTTLS; refusing to send the password unencrypted. "
               "Use a server that supports STARTTLS on port 587, or SSL on port 465 (SMTP_PORT).")


def _close(conn: smtplib.SMTP) -> None:
    try:
        conn.quit()
    except (smtplib.SMTPException, OSError):
        try:
            conn.close()
        except OSError:
            pass


def connect(server: str, port: int, user: str, password: str, ctx: ssl.SSLContext) -> smtplib.SMTP:
    """An encrypted, logged-in connection. Port 465 is SSL from the start; any other port must
    switch to TLS with STARTTLS before the password is sent — a server (or anyone in between)
    that leaves STARTTLS out of its EHLO reply gets no password: RuntimeError."""
    if port == 465:
        conn: smtplib.SMTP = smtplib.SMTP_SSL(server, port, context=ctx, timeout=60)
    else:
        conn = smtplib.SMTP(server, port, timeout=60)
    try:
        conn.ehlo()
        if port != 465:
            if not conn.has_extn("starttls"):
                raise RuntimeError(NO_STARTTLS)
            conn.starttls(context=ctx)
            conn.ehlo()
        if user:
            conn.login(user, password)
    except BaseException:
        _close(conn)
        raise
    return conn


def send(msg: MIMEMultipart, from_addr: str, recipients: list[str]) -> None:
    """Connect and log in (up to 3 tries on network trouble), then hand the message over ONCE.
    A failure after that point is never retried: the server may already have accepted the e-mail,
    and a second try could send the whole district list a second copy."""
    server = os.environ.get("SMTP_SERVER", "").strip()
    port = int((os.environ.get("SMTP_PORT") or "587").strip() or 587)
    user = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    ctx = ssl.create_default_context()
    conn: smtplib.SMTP | None = None
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            conn = connect(server, port, user, password, ctx)
            break
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(
                "The mail server rejected the username/password. For Gmail you need an *App Password* "
                "(Google Account → Security → 2-Step Verification → App passwords), not your normal password."
            ) from e
        except TRANSIENT as e:
            last_err = e
            if attempt < 3:
                log(f"attempt {attempt}/3 failed ({type(e).__name__}: {e}); retrying…")
                time.sleep(10 * attempt)
    if conn is None:
        raise RuntimeError(f"Could not reach the mail server {server}:{port}: {last_err}")
    try:
        refused = conn.send_message(msg, from_addr=from_addr, to_addrs=recipients)
    except smtplib.SMTPRecipientsRefused as e:
        raise RuntimeError(f"All recipients were refused: {list(e.recipients)}") from e
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as e:
        raise RuntimeError(f"The mail server refused the e-mail ({type(e).__name__}: {e}); it was not sent.") from e
    except Exception as e:
        raise RuntimeError(
            f"The connection failed while the e-mail was being handed over ({type(e).__name__}: {e}). "
            "It MAY have been sent — check the mailbox or the group before re-running, "
            "so nobody gets it twice.") from e
    finally:
        _close(conn)
    if refused:
        log(f"WARNING: {len(refused)} recipient(s) were refused by the mail server")


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
def _positive(v: Any, default: int) -> int:
    try:
        n = int(v)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send the monthly bilingual e-mail digest.")
    ap.add_argument("--dry-run", action="store_true", help="don't send; write digest.html and digest.txt to --out-dir")
    ap.add_argument("--month", default=None, help="the edition YYYY-MM (default: this month, Central time)")
    ap.add_argument("--max-per-section", type=int, default=None, help="items listed per section (default: config digest.per_section or 5)")
    ap.add_argument("--to", default=None, help="override recipients (comma-separated)")
    ap.add_argument("--force", action="store_true", help="send even if nothing was new last month")
    ap.add_argument("--as-of", default=None, help="pretend today is YYYY-MM-DD (testing; the scheduled time, 15:05 UTC)")
    ap.add_argument("--out-dir", default=str(ROOT / ".tmp"), help="where --dry-run writes the preview")
    args = ap.parse_args(argv)

    cfg = load_config()
    site = cfg.get("site") or {}
    digest_cfg = cfg.get("digest") if isinstance(cfg.get("digest"), dict) else {}

    if args.as_of:
        d = date.fromisoformat(args.as_of)
        now = datetime(d.year, d.month, d.day, 15, 5, tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc).replace(microsecond=0)
    if args.month is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.month):
        log(f"--month must look like 2026-10 (got {args.month!r}) — nothing was built or sent")
        step_summary(["### E-mail digest", f"Stopped: the month {args.month!r} is not a YYYY-MM month like 2026-10. "
                      "Nothing was sent — run it again with the month written like that, or leave the box empty."])
        return 2

    max_per = args.max_per_section or _positive(digest_cfg.get("per_section"), 5)
    highlights = _positive(digest_cfg.get("highlights"), 3)
    site_url = (os.environ.get("SITE_URL") or site.get("url") or "").strip().rstrip("/")
    if not site_url:
        log("WARNING: no site URL (config site.url / SITE_URL) — links will be relative")
    links = Links(site_url)

    data = collect(now, args.month, max_per, highlights)
    ed = data["edition"]
    n = total_count(data)
    counts = {g: len(v) for g, v in data["groups"].items() if v}
    writers = sum(len(v) for v in data["writers"].values())
    log(f"edition {ed['key']} — news from {ed['prev_first']} to {ed['prev_last']}: {n} item(s) {counts} "
        f"writers={writers} · issues={len(data['issues'])} events={len(data['events'])} "
        f"deadlines={len(data['deadlines'])} meeting={'yes' if data['meeting'] else 'no'}")

    subject = subject_of(data, cfg)
    html_body = render_html(data, cfg, links, max_per, subject)
    text_body = render_text(data, cfg, links, max_per)

    if args.dry_run:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "digest.html").write_text(html_body, encoding="utf-8")
        (out / "digest.txt").write_text(text_body, encoding="utf-8")
        log(f"DRY RUN — subject: {subject}")
        log(f"preview written to {out / 'digest.html'} and {out / 'digest.txt'}")
        step_summary(["### E-mail digest preview (not sent)", f"**Subject:** {subject}", "",
                      f"{n} new item(s) last month: {counts}; writers {writers}; events this month "
                      f"{len(data['events'])}; deadlines {len(data['deadlines'])}", "",
                      "Download the `digest-preview` artifact to see it."])
        return 0

    if n == 0 and not args.force:
        log("nothing new last month — not sending (use --force to send anyway)")
        step_summary(["### E-mail digest", f"Nothing new in {month_label(ed['prev'], 'en')} — no e-mail sent."])
        return 0

    recipients = parse_recipients(args.to or os.environ.get("DIGEST_TO", ""))
    missing = [k for k in ("SMTP_SERVER", "SMTP_USERNAME", "SMTP_PASSWORD") if not os.environ.get(k, "").strip()]
    if missing or not recipients:
        log(f"e-mail is not configured (missing: {', '.join(missing + ([] if recipients else ['DIGEST_TO']))}). "
            "See README → 'Monthly e-mail digest'.")
        return 2

    user = os.environ.get("SMTP_USERNAME", "").strip()
    from_addr = parseaddr(os.environ.get("DIGEST_FROM", "").strip())[1] or (user if "@" in user else site.get("contact_email", ""))
    reply_to = parseaddr(os.environ.get("DIGEST_REPLY_TO", "").strip())[1] or site.get("contact_email", "") or from_addr
    from_name = site.get("committee") or site.get("title") or "Grapevine / La Viña"
    msg = build_message(subject, html_body, text_body, from_addr, from_name, recipients, reply_to)
    try:
        send(msg, from_addr, recipients)
    except Exception as e:
        log(f"ERROR: {e}")
        step_summary(["### E-mail digest", f"Sending FAILED: {e}"])
        return 1
    log(f"sent to {len(recipients)} recipient(s): {subject}")
    step_summary(["### E-mail digest sent", f"**Subject:** {subject}", "",
                  f"Recipients: {len(recipients)} · new items last month: {n} {counts}"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
