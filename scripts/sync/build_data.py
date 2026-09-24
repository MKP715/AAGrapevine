"""Assemble the site data — data/site/*.json, the ONLY files the templates read — from the raw
source files (data/raw/*.json). Adds English ⇄ Spanish translations, "new" flags, the events
calendar, What's New, districts, the published-writers spotlight (spotlight.json: where each
Grapevine / La Viña writer is from, Area 65 first) and the /status/ page.
Contract: docs/DATA_SCHEMA.md §3 + §5.

    python -m scripts.sync.build_data                    # normal daily run
    python -m scripts.sync.build_data --out .tmp/site    # write somewhere else (testing)
    python -m scripts.sync.build_data --no-translate     # only cached translations (fast)

Robust by design: a missing/corrupt raw file or a bad item is logged and skipped, never fatal.
Output is deterministic (stable sort, no run timestamps inside items, no `last_seen`) so daily git
diffs stay small.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import yaml

from . import translate as T
from .common import (CONTENT_DIR, RAW_DIR, SITE_DIR, STATE_DIR, clean_text, get_logger, load_config, now_iso,
                     parse_iso, read_json, short_hash, slugify, strip_html, to_iso, truncate, write_json)
from .geo import SCOPES, classify_location, fold
from .meeting import (MonthlyRule, check_skip_dates, meeting_skip_notes, parse_hhmm, upcoming_meetings,
                      upcoming_rule_dates, week_of_month_value, weekday_index)

log = get_logger("build_data")

NEW_DAYS = 14                 # "New" badge
WHATSNEW_MAX = 150
PAST_EVENTS_KEEP = 12
RECENT_EVENT_DAYS = 30        # events announced within N days appear in What's New
LANGS = ("en", "es")
# Published-writers spotlight defaults (config/site.yml `spotlight:` overrides them)
SPOTLIGHT_HOME_DAYS = 60
SPOTLIGHT_LIST_DAYS = (60, 90)
SPOTLIGHT_SCOPES = ("neta65", "texas", "all")
_EVERY_ISSUE = re.compile(r"(?i)in every issue|en cada (?:edici[oó]n|n[uú]mero)")
NEVER_NEW_KINDS = ("topic", "meeting")   # editorial themes (date = a deadline) and the Weekly Open
# Events computed from config/site.yml (the monthly committee meeting, `recurring_events:`): a date that
# comes round every month is never "new", never in What's New and never in the "past events" list.
SCHEDULED_EVENT_CATEGORIES = ("committee", "recurring")
# recurring_events: (config/site.yml)
RECURRING_AHEAD = 6           # upcoming dates listed per event (`months_ahead`)
RECURRING_AHEAD_MAX = 24
RECURRING_KEEP_PAST_DAYS = 90  # dates of the last 90 days stay in events.json (past: true) so calendar
                               # subscribers keep them, like the committee meetings in the .ics feed
RECURRING_KEY_MAX = 32        # "ev-recurring-<key>-<date>" anchors stay within committee.js slugify()'s 60 characters

# raw source → labels on the /status/ page (order = order shown)
SOURCES: list[tuple[str, str, str]] = [
    ("drive", "Google Drive (committee uploads)", "Google Drive (archivos del comité)"),
    ("announcements", "Announcements (content/announcements)", "Anuncios (content/announcements)"),
    ("manual_events", "Events (content/events)", "Eventos (content/events)"),
    ("articles", "Grapevine & La Viña articles", "Artículos de Grapevine y La Viña"),
    ("editorial", "Editorial themes (upcoming issues)", "Temas editoriales (próximos números)"),
    ("pdfs", "PDF library (crawl of both sites)", "Biblioteca de PDF (rastreo de ambos sitios)"),
    ("youtube", "YouTube videos", "Videos de YouTube"),
    ("podcasts", "Podcasts", "Pódcasts"),
    ("instagram", "Instagram posts", "Publicaciones de Instagram"),
    ("weekly_open", "Grapevine Weekly Open meeting", "Reunión Grapevine Weekly Open"),
    ("events_external", "GV/LV event calendars", "Calendarios de eventos de GV/LV"),
    ("shop", "Book of the Month & subscription prices", "Libro del mes y precios de suscripción"),
]

# Canonical key order of a site item. `last_seen` is deliberately NOT here: it only serves the sync
# modules (gone-detection) and would make every item change in git once a week.
ITEM_KEYS = ("id", "source", "kind", "url", "title", "summary", "lang", "date", "first_seen",
             "image", "tags", "category", "status", "extra")
DROP_KEYS = ("last_seen",)

# Fixed (human) Spanish for the default meeting note in config/site.yml → no machine translation.
DEFAULT_NOTE_ES = {
    "All AA members are welcome to attend. No registration required.":
        "Todos los miembros de AA son bienvenidos. No se requiere inscripción.",
}

MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December")
MONTHS_ES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
             "Octubre", "Noviembre", "Diciembre")
_MONTH_WORDS = {m.lower() for m in MONTHS_EN + MONTHS_ES} | {"setiembre"}

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAYS_EN = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
WEEKDAYS_ES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábados", "Domingos")
TZ_NAMES = {   # IANA zone → (English, Spanish)
    "America/New_York": ("Eastern", "hora del Este"), "America/Chicago": ("Central", "hora del Centro"),
    "America/Denver": ("Mountain", "hora de la Montaña"), "America/Phoenix": ("Mountain", "hora de la Montaña"),
    "America/Los_Angeles": ("Pacific", "hora del Pacífico"),
}

# "#alcoholicsanonymous #aa #twelvesteps …" walls at the end of podcast/video descriptions
_HASHTAG_WALL = re.compile(r"(?:(?<=\s)|^)#\w+(?:[\s,]+#\w+){2,}\s*(?:…|\.\.\.)?")


# =========================================================================== small helpers
def ts(v: Any) -> float | None:
    """ISO date/datetime → POSIX seconds (date-only = noon UTC, like the templates)."""
    if not v:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        s += "-01"
    d = parse_iso(s)
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def fix_title(s: Any) -> str:
    """Decode leftover %XX escapes from file names ('La-Vin%CC%83a' → 'La-Viña'), NFC-normalize."""
    t = str(s or "")
    if re.search(r"%[0-9A-Fa-f]{2}", t):
        try:
            t = unquote(t)
        except Exception:
            pass
    return clean_text(unicodedata.normalize("NFC", t))


def strip_hashtag_wall(s: str) -> str:
    """Remove runs of 3+ hashtags (SEO lists in podcast/video descriptions) from a teaser."""
    if "#" not in (s or ""):
        return s or ""
    out = clean_text(_HASHTAG_WALL.sub(" ", s))
    return re.sub(r"\s+([.,;:!?…])", r"\1", out)


def prep(raw_item: dict) -> dict:
    """Copy a raw item into canonical key order (+ light clean-up); never mutates the raw data."""
    it = copy.deepcopy(raw_item)
    out = {k: it.get(k) for k in ITEM_KEYS}
    for k, v in it.items():            # keep any extra top-level fields a module added
        if k not in out and k not in DROP_KEYS:
            out[k] = v
    out["lang"] = out.get("lang") or "und"
    out["title"] = T.fix_season_episode(fix_title(out.get("title")), "es" if out["lang"] == "es" else "en")
    out["summary"] = strip_hashtag_wall(clean_text(out.get("summary") or ""))
    out["tags"] = list(out.get("tags") or [])
    out["status"] = out.get("status") or "ok"
    out["extra"] = out.get("extra") if isinstance(out.get("extra"), dict) else {}
    return out


def live(items: Iterable[dict]) -> list[dict]:
    return [i for i in items if isinstance(i, dict) and i.get("id") and i.get("status", "ok") != "gone"]


def sort_newest(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: (ts(i.get("date")) or ts(i.get("first_seen")) or 0.0, i["id"]), reverse=True)


def city_state(location: str | None) -> tuple[str | None, str | None]:
    m = re.search(r"([A-Za-zÀ-ÿ .'-]+),\s*(TX|Texas|[A-Z]{2})\b", location or "")
    if not m:
        return None, None
    return clean_text(m.group(1)), ("TX" if m.group(2) in ("TX", "Texas") else m.group(2))


def safe_each(items: Iterable[dict], fn: Callable[[dict], Any], what: str) -> list:
    """Apply fn to every item; one bad item is logged and skipped, never fatal."""
    out = []
    for it in items:
        try:
            r = fn(it)
        except Exception as e:
            log.warning("skipped %s %s: %s: %s", what, (it or {}).get("id"), type(e).__name__, e)
            continue
        if r is not None:
            out.append(r)
    return out


def issue_label(pub: str | None, key: str | None, raw_label: str | None = None) -> dict | None:
    """Magazine issue label in both languages, written locally (never machine-translated):
    Grapevine is monthly ("October 2026" / "Octubre 2026"); La Viña is bimonthly with the first
    month as key ("2026-09" → "September / October 2026" / "Septiembre / Octubre 2026")."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", str(key or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    named = [w for w in re.findall(r"[^\W\d_]+", (raw_label or "").lower()) if w in _MONTH_WORDS]
    bimonthly = len(named) >= 2 or (len(named) != 1 and pub == "lv" and mo % 2 == 1)
    if bimonthly and mo < 12:
        return {"en": f"{MONTHS_EN[mo - 1]} / {MONTHS_EN[mo]} {y}", "es": f"{MONTHS_ES[mo - 1]} / {MONTHS_ES[mo]} {y}"}
    return {"en": f"{MONTHS_EN[mo - 1]} {y}", "es": f"{MONTHS_ES[mo - 1]} {y}"}


class Ctx:
    """Everything one build needs (clock, config, raw data)."""

    def __init__(self, offline: bool = False):
        self.cfg = load_config()
        self.tz = ZoneInfo(self.cfg.get("site", {}).get("timezone", "America/Chicago"))
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.now_ts = self.now.timestamp()
        self.today_local = self.now.astimezone(self.tz).date()
        self.offline = offline
        self.raw: dict[str, dict] = {}
        self.raw_problems: dict[str, str] = {}
        self.births: dict[str, float] = {}
        self.hub_issues: set[str] = set()      # "gv:2026-10" — magazine issues seen on a hub (current issues)
        self.feeds: list[dict] = []            # health of each sources.ics_feeds entry (→ status.json `feeds`)
        self.feed_requests = 0                 # requests made to .ics feeds this run (one per feed at most)

    # ---------------------------------------------------------------- raw loading
    def load_raw(self) -> None:
        for name, *_ in SOURCES:
            p = RAW_DIR / f"{name}.json"
            env = {"source": name, "updated": None, "ok": False, "error": None, "stats": {}, "items": []}
            if not p.exists():
                self.raw_problems[name] = "missing"
            else:
                try:
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
                        raise ValueError("not a raw envelope")
                    env.update(data)
                    env["items"] = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("id")]
                except Exception as e:
                    self.raw_problems[name] = f"unreadable: {type(e).__name__}: {e}"[:200]
                    log.error("raw file %s is unreadable (%s) — building without it", p.name, e)
            self.raw[name] = env
            # The source's first harvest: the stable `first_harvest` stamp (common.save_raw); older
            # envelopes without it fall back to the oldest first_seen.
            firsts = [t for t in (ts(i.get("first_seen")) for i in env["items"]) if t]
            fh = ts(env.get("first_harvest"))
            cands = ([fh] if fh else []) + firsts
            if cands:
                self.births[name] = min(cands)
        iss = (self.raw.get("articles") or {}).get("issues")
        rows = iss.values() if isinstance(iss, dict) else iss if isinstance(iss, list) else []
        self.hub_issues = {f"{r.get('publication')}:{r.get('key')}" for r in rows
                           if isinstance(r, dict) and r.get("publication") and r.get("key")}
        loaded = {k: len(v["items"]) for k, v in self.raw.items() if v["items"]}
        log.info("raw items: %s%s", loaded, f"  (problems: {self.raw_problems})" if self.raw_problems else "")

    def items(self, name: str) -> list[dict]:
        return live(self.raw.get(name, {}).get("items", []))

    # ---------------------------------------------------------------- dates
    def effective_ts(self, it: dict, source: str | None = None) -> float | None:
        """The date an item became news: its publish date; for future-dated items (next month's
        magazine issue) and undated items the day we found it — but only if found after the
        source's first harvest (so launch day isn't 3,000 "new" items). The crawler already dates
        PDFs it saw appear on a known page, so an undated PDF is never news by itself."""
        d = ts(it.get("date"))
        if d is not None and d <= self.now_ts + 86400:
            return d
        if source == "pdfs":
            return None
        return self.found_ts(it, source)

    def found_ts(self, it: dict, source: str | None = None) -> float | None:
        """first_seen, but only when the item appeared AFTER its source's first harvest (the very
        first sync finds everything at once — that is not news). Committee-written items always count."""
        fs = ts(it.get("first_seen"))
        if not fs:
            return None
        birth = self.births.get(source or "")
        if it.get("source") == "committee" or birth is None or fs > birth + 2 * 86400:
            return min(fs, self.now_ts)
        return None

    def back_catalog(self, it: dict) -> bool:
        """A magazine story of an OLDER issue that articles.py found only in the site's archive
        (its issue was never the current one on a hub while we watched). Real stories, shown on
        /read/ and in the spotlight — but not "news": they stay out of What's New and get no
        "New" badge, so the first archive backfill does not flood the feed with months of stories."""
        if it.get("kind") != "article" or not self.hub_issues:
            return False
        ex = it.get("extra") or {}
        pub, key = ex.get("publication") or it.get("category"), ex.get("issue_key")
        return bool(pub and key) and f"{pub}:{key}" not in self.hub_issues

    def is_new(self, it: dict, source: str | None = None) -> bool:
        if it.get("kind") in NEVER_NEW_KINDS or self.back_catalog(it):
            return False
        if it.get("kind") == "event":               # "new" = newly announced, not "happening soon"
            if it.get("category") in SCHEDULED_EVENT_CATEGORIES or (it.get("extra") or {}).get("past"):
                return False
            f = self.found_ts(it, source)
            return bool(f and self.now_ts - f < NEW_DAYS * 86400)
        e = self.effective_ts(it, source)
        return bool(e is not None and self.now_ts - e < NEW_DAYS * 86400 and e <= self.now_ts + 86400)


# =========================================================================== collections
def simple(ctx: Ctx, source: str, kinds: tuple[str, ...] | None = None, exclude: tuple[str, ...] = (),
           skip: Callable[[dict], bool] | None = None) -> list[dict]:
    items = [i for i in ctx.items(source) if (not kinds or i.get("kind") in kinds) and i.get("kind") not in exclude]
    if skip is not None:
        before = len(items)
        items = [i for i in items if not skip(i)]
        if before != len(items):
            log.info("%s: %d item(s) left out on purpose", source, before - len(items))
    items = safe_each(items, prep, source)
    dropped = [i["id"] for i in items if not i["title"]]
    if dropped:
        log.warning("%s: %d item(s) without a title skipped: %s", source, len(dropped), dropped[:5])
    return sort_newest([i for i in items if i["title"]])


def closed_form(it: dict) -> bool:
    """A Google Form that no longer accepts answers — never promote a closed sign-up."""
    return it.get("kind") == "form" and (it.get("extra") or {}).get("form_closed") is True


def build_announcements(ctx: Ctx) -> list[dict]:
    raw = [i for i in ctx.items("announcements") if i.get("kind") == "announcement"]
    raw += [i for i in ctx.items("drive") if i.get("kind") == "announcement"]
    items = safe_each(raw, prep, "announcement")
    today = ctx.today_local.isoformat()
    kept = []
    for it in items:
        exp = it["extra"].get("expires")
        if exp and str(exp)[:10] < today:
            continue
        it["extra"].setdefault("body_md", it.get("summary") or "")
        it["extra"]["pinned"] = bool(it["extra"].get("pinned"))
        kept.append(it)
    kept.sort(key=lambda i: (0 if i["extra"]["pinned"] else 1,
                             -(ts(i.get("date")) or ts(i.get("first_seen")) or 0.0), i["id"]))
    return kept


# --------------------------------------------------------------------------- events
def _local_iso(day: str, hhmm: str | None, tz: ZoneInfo) -> str | None:
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
        d = date.fromisoformat(day[:10])
        return to_iso(datetime(d.year, d.month, d.day, h, m, tzinfo=tz))
    except Exception:
        return None


def committee_meetings(ctx: Ctx, count: int = 12) -> list[dict]:
    """The monthly committee meeting for the next 12 months — fixed human text in both languages."""
    site, mt = ctx.cfg.get("site", {}) or {}, ctx.cfg.get("meeting", {}) or {}
    platform = mt.get("platform") or "Zoom"
    committee = site.get("committee") or "Grapevine / La Viña Committee"
    committee_es = site.get("committee_es") or "Comité de Grapevine / La Viña"
    note = clean_text(mt.get("note"))
    note_es = clean_text(mt.get("note_es")) or DEFAULT_NOTE_ES.get(note, "")
    machine: list[str] = []
    if note and not note_es:   # the committee changed the note but gave no Spanish → translate it
        r = T.get_translator().translate([note], "en", "es")[0]
        note_es, machine = (r[0], ["es"]) if r[0] and r[1] else (note, [])
    title = {"en": f"{committee} Meeting", "es": f"Reunión del {committee_es}"}
    summary = {"en": clean_text(f"Our monthly committee meeting on {platform}. {note}"),
               "es": clean_text(f"Nuestra reunión mensual del comité por {platform}. {note_es}")}
    out = []
    for m in upcoming_meetings(count):
        out.append({
            "id": f"ev:committee:{m['ymd']}", "source": "committee", "kind": "event", "url": "/meeting/",
            "title": title["en"], "summary": summary["en"], "lang": "en", "date": m["start"],
            "first_seen": None, "image": None, "tags": ["committee"],
            "category": "committee", "status": "ok",
            "extra": {"start": m["start"], "end": m["end"], "all_day": False, "location": platform,
                      "online_url": mt.get("zoom_url"), "meeting_id": mt.get("meeting_id"),
                      "passcode": mt.get("passcode"), "flyer_url": None, "flyer_thumb": None,
                      "city": None, "state": None, "recurring": True},
            "i18n": {"title": dict(title), "summary": dict(summary)},
            "machine": list(machine), "is_new": False, "_fixed_i18n": True,
        })
    return out


# --------------------------------------------------------------------------- recurring events (config)
# The repeat line uses the SAME words as the committee meeting's line ("Every third Wednesday of the
# month"): src/_i18n/committee.json `committee.rule` and `committee.ord.*` (a unit test keeps the two
# in step). The web pages build the line themselves from `extra.rule`, with those strings and the
# browser's clock format (eleventy/filters/committee.js recurrenceText); this copy is the fallback.
_RULE = {"en": "Every {ord} {weekday} of the month", "es": "Cada {ord} {weekday} del mes"}
_ORD_WORDS = {"en": {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last"},
              "es": {1: "primer", 2: "segundo", 3: "tercer", 4: "cuarto", 5: "quinto", -1: "último"}}
_DAY_NAMES = {"en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
              "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")}
_NB = "\u00a0"      # Spanish "8:00 p. m.": no-break spaces (esMeridiem in eleventy.config.js)
_THIN = "\u2009"    # "7:00 – 8:00 PM": the browser's time ranges put thin spaces around the dash


def clock_range(start: tuple[int, int], end: tuple[int, int], lang: str) -> str:
    """The time range as the site's pages write it (Intl formatRange, en-US / es-US):
    (17, 0), (20, 0) → "5:00 – 8:00 PM" / "5:00–8:00 p. m."; when the half of the day changes,
    "11:00 AM – 1:00 PM" / "11:00 a. m. – 1:00 p. m." (thin spaces around the dash)."""
    def hm(h: int, m: int) -> str:
        return f"{(h % 12) or 12}:{m:02d}"

    dash = f"{_THIN}–{_THIN}"
    if lang == "es":
        a, b = (f"a.{_NB}m." if h < 12 else f"p.{_NB}m." for h in (start[0], end[0]))
        return f"{hm(*start)}–{hm(*end)}{_NB}{b}" if a == b else f"{hm(*start)}{_NB}{a}{dash}{hm(*end)}{_NB}{b}"
    a, b = ("AM" if h < 12 else "PM" for h in (start[0], end[0]))
    return f"{hm(*start)}{dash}{hm(*end)} {b}" if a == b else f"{hm(*start)} {a}{dash}{hm(*end)} {b}"


def recurrence_label(rule: MonthlyRule) -> dict[str, str]:
    """Hand-written, both languages (never machine-translated), worded like the committee meeting's line:
    "Every second Saturday of the month · 5:00 – 8:00 PM" / "Cada segundo sábado del mes · 5:00–8:00 p. m."."""
    start, end = rule.span()
    n, wd = rule.week_of_month, rule.weekday
    return {lang: _RULE[lang].format(ord=_ORD_WORDS[lang][n], weekday=_DAY_NAMES[lang][wd])
            + f" · {clock_range(start, end, lang)}" for lang in ("en", "es")}


def rule_fields(rule: MonthlyRule) -> dict[str, Any]:
    """`extra.rule` of a recurring event: the rule in the shape of config/site.yml `meeting:` (week_of_month,
    English weekday, start / end as "HH:MM" — the end as actually used), so the web pages write the repeat
    line with the committee meeting's own helpers (eleventy/filters/committee.js recurrenceText)."""
    (sh, sm), (eh, em) = rule.span()
    return {"week_of_month": rule.week_of_month, "weekday": WEEKDAYS[rule.weekday],
            "start": f"{sh:02d}:{sm:02d}", "end": f"{eh:02d}:{em:02d}"}


def _bilingual(en: str, es: str, title: bool = False) -> tuple[dict[str, str], list[str]]:
    """The committee's own words in both languages. When only one language was written, the other is
    machine-translated (→ its language in `machine`); if that is not possible right now, the original
    shows in both (and the next run tries again)."""
    if en and es or not (en or es):
        return {"en": en, "es": es}, []
    src, tgt = ("en", "es") if en else ("es", "en")
    text = en or es
    out, machine = text, []
    try:
        r = T.get_translator().translate([text], src, tgt)[0]
        if r[0]:
            out = T.title_case_en(r[0]) if (title and tgt == "en" and r[1]) else r[0]
            machine = [tgt] if r[1] and r[0] != text else []
    except Exception as e:           # translation trouble never stops the calendar
        log.warning("recurring event text not translated (%s: %s) — shown as written", type(e).__name__, e)
    return {src: text, tgt: out}, machine


def _recurring_url(v: Any) -> str | None:
    s = clean_text(v)
    if not s:
        return None
    if re.match(r"(?i)^www\.", s):
        s = "https://" + s
    return s if re.match(r"(?i)^https?://[^\s/]+\.[^\s]+$", s) else None


def recurring_specs(ctx: Ctx) -> tuple[list[dict], list[str]]:
    """config/site.yml `recurring_events:` → (valid event settings, problems). Hand-edited settings: an
    entry with a real mistake (no title, a weekday or week that cannot be understood, no start time …)
    is SKIPPED and named in the problems; small slips (an end time that cannot be read, a skip date that
    is not a date or not one of the event's days, a link that is not a web address) are noted but the
    event still shows."""
    raw = ctx.cfg.get("recurring_events")
    if raw is None or raw == "" or raw == []:
        return [], []
    if isinstance(raw, dict):              # a single event written without the leading "- "
        raw = [raw]
    if not isinstance(raw, list):
        return [], ["recurring_events: not understood — it must be a list, each event starting with “- key:”"]
    specs: list[dict] = []
    problems: list[str] = []
    seen: set[str] = set()
    for n, e in enumerate(raw, 1):
        if not isinstance(e, dict):
            problems.append(f"recurring_events entry {n}: not understood (its settings must be indented under "
                            f"“- key:”) — skipped")
            continue
        title_en, title_es = clean_text(e.get("title")), clean_text(e.get("title_es"))
        key = slugify(str(e.get("key") or ""), RECURRING_KEY_MAX).strip("-") if clean_text(e.get("key")) else ""
        if not key and (title_en or title_es):
            key = slugify(title_en or title_es, RECURRING_KEY_MAX).strip("-")
        name = f"recurring_events entry {n} ({key or title_en or title_es or 'no name'})"
        errors: list[str] = []
        notes: list[str] = []
        if not (title_en or title_es):
            errors.append("it needs a title")
        if key in seen:
            errors.append(f"the key “{key}” is used twice (each event needs its own)")
        def given(k: str) -> bool:
            return e.get(k) not in (None, "")

        wom = week_of_month_value(e.get("week_of_month"))
        if wom is None:
            errors.append(f"week_of_month “{e.get('week_of_month')}” must be 1, 2, 3, 4, 5 or -1 (the last one)"
                          if given("week_of_month") else "it needs week_of_month (1–5, or -1 for the last one)")
        wd = weekday_index(e.get("weekday"))
        if wd is None:
            errors.append(f"weekday “{e.get('weekday')}” is not a day of the week" if given("weekday")
                          else "it needs a weekday (like \"saturday\")")
        start = parse_hhmm(e.get("start"), (-1, -1))
        if start == (-1, -1):
            errors.append(f"start time “{e.get('start')}” is not a time like \"17:00\"" if given("start")
                          else "it needs a start time (like \"17:00\")")
        end = parse_hhmm(e.get("end"), (-1, -1))
        if given("end") and end == (-1, -1):
            notes.append(f"end time “{e.get('end')}” is not a time like \"20:00\" — shown as one hour long")
        elif end != (-1, -1) and start != (-1, -1) and end <= start:
            notes.append(f"end time “{e.get('end')}” is not after the start — shown as one hour long")
        if errors:
            problems.append(f"{name}: " + "; ".join(errors) + " — skipped")
            continue
        seen.add(key)
        # A slip like the Sunday, the 1st Saturday or the wrong month would skip nothing: say so
        # (the same check as the committee meeting's own skip_dates — meeting.check_skip_dates).
        skip, skip_notes = check_skip_dates(e.get("skip_dates"), wd, wom)
        notes += skip_notes
        ahead = RECURRING_AHEAD
        if e.get("months_ahead") not in (None, ""):
            try:
                ahead = int(e.get("months_ahead"))
                if not 1 <= ahead <= RECURRING_AHEAD_MAX:
                    raise ValueError
            except (TypeError, ValueError):
                ahead = RECURRING_AHEAD
                notes.append(f"months_ahead “{e.get('months_ahead')}” must be a number from 1 to "
                             f"{RECURRING_AHEAD_MAX} — {RECURRING_AHEAD} used")
        url = _recurring_url(e.get("url"))
        if e.get("url") and not url:
            notes.append(f"url “{e.get('url')}” is not a web address (https://…) — left out")
        online = _recurring_url(e.get("online_url"))
        if e.get("online_url") and not online:
            notes.append(f"online_url “{e.get('online_url')}” is not a web address (https://…) — left out")
        if notes:
            problems.append(f"{name}: " + "; ".join(notes))
        location = clean_text(e.get("location"))
        city, state = city_state(location)
        specs.append({
            "key": key, "title": (title_en, title_es),
            "summary": (clean_text(e.get("summary")), clean_text(e.get("summary_es"))),
            "rule": MonthlyRule(week_of_month=wom, weekday=wd, start=start, end=end if end != (-1, -1) else start,
                                skip=frozenset(skip)),
            "ahead": ahead, "location": location or None, "url": url, "online_url": online,
            "city": clean_text(e.get("city")) or city, "state": clean_text(e.get("state")) or state,
        })
    return specs, problems


def recurring_events(ctx: Ctx) -> list[dict]:
    """Every entry of config/site.yml `recurring_events:` (e.g. the GV/LV booth at CityWide Dallas on the
    2nd Saturday, 17:00–20:00 Central) → one event per date: the next `months_ahead` dates, plus the
    dates of the last RECURRING_KEEP_PAST_DAYS days (build_events marks them past; the web pages never
    list them, the calendar feed keeps them for subscribers). Titles and summaries are the committee's
    own words in both languages (fixed i18n); the recurrence line is written by rule. An entry with a
    mistake is skipped and reported (log + status.json `problems.recurring_events`), never fatal."""
    specs, problems = recurring_specs(ctx)
    for p in problems:
        log.warning("config/site.yml %s", p)
    if problems:
        ctx.raw_problems["recurring_events"] = ("config/site.yml " + " / ".join(problems))[:2000]
    out: list[dict] = []
    for sp in specs:
        rule = sp["rule"]
        recent = [d for d in upcoming_rule_dates(rule, 12, ctx.tz, ctx.now, include_recent_days=RECURRING_KEEP_PAST_DAYS)
                  if (ts(d["end"]) or 0) < ctx.now_ts]
        dates = recent + upcoming_rule_dates(rule, sp["ahead"], ctx.tz, ctx.now)
        if not dates:
            continue
        title, m1 = _bilingual(*sp["title"], title=True)
        summary, m2 = _bilingual(*sp["summary"])
        label = recurrence_label(rule)
        lang = "en" if sp["title"][0] else "es"
        machine = sorted(set(m1) | set(m2))
        for d in dates:
            out.append({
                "id": f"ev:recurring:{sp['key']}:{d['ymd']}", "source": "committee", "kind": "event",
                "url": sp["url"] or "/events/",
                "title": title[lang], "summary": summary[lang], "lang": lang, "date": d["start"],
                "first_seen": None, "image": None, "tags": ["recurring"],
                "category": "recurring", "status": "ok",
                "extra": {"start": d["start"], "end": d["end"], "all_day": False, "location": sp["location"],
                          "online_url": sp["online_url"], "flyer_url": None, "flyer_thumb": None,
                          "city": sp["city"], "state": sp["state"], "recurring": True, "series": sp["key"],
                          "rule": rule_fields(rule), "recurrence_label": label["en"]},
                "i18n": {"title": dict(title), "summary": dict(summary), "recurrence_label": dict(label)},
                "machine": list(machine), "is_new": False, "_fixed_i18n": True,
            })
    return out


def flyer_events(ctx: Ctx) -> list[dict]:
    """Drive flyers whose file name starts with a date (drive.py sets extra.event_date)."""
    out = []
    for d in ctx.items("drive"):
        ex = d.get("extra") or {}
        day = ex.get("event_date")
        if not day or d.get("kind") == "announcement":
            continue
        try:
            start = _local_iso(day, ex.get("event_time"), ctx.tz) if ex.get("event_time") else str(day)[:10]
            end = _local_iso(day, ex.get("event_end_time"), ctx.tz) if ex.get("event_end_time") else None
            loc = ex.get("event_location")
            city, state = city_state(loc)
            it = prep(d)
            it.update({
                "id": f"ev:flyer:{ex.get('file_id') or short_hash(d['id'])}", "kind": "event", "category": "flyer",
                "title": fix_title(ex.get("event_title") or d.get("title")), "date": start,
                "image": ex.get("thumb_url") or d.get("image"),
            })
            it["extra"] = {"start": start, "end": end, "all_day": not ex.get("event_time"), "location": loc,
                           "online_url": None, "flyer_url": ex.get("view_url") or d.get("url"),
                           "flyer_thumb": ex.get("thumb_url"), "city": city, "state": state,
                           "drive_id": d["id"], "is_pdf": ex.get("is_pdf"), "is_image": ex.get("is_image")}
            out.append(it)
        except Exception as e:
            log.warning("flyer %s skipped: %s", d.get("id"), e)
    return out


# --------------------------------------------------------------------------- outside calendars (.ics)
# config/site.yml `sources.ics_feeds:` — public calendar files (.ics) whose events are merged into Events.
# They are EXTRAS: a feed that cannot be read never stops the update and never counts as a content source
# that "stopped updating" (status.json keeps their health apart, under `feeds`). Politeness: ONE plain
# request per feed per run with the committee robot's own User-Agent (sources.crawler.user_agent), no
# retries, and a feed is asked again only ICS_RETRY_HOURS_* later — about once a day whether it worked
# or not (so reruns and the quick runs after a push never pile up requests; the /status/ page and the
# README promise the Area webmaster "at most once a day").
# The last good copy of each feed is kept in data/state/ics_feeds.json and used until a new one arrives.
ICS_STATE_FILE = "ics_feeds.json"
ICS_TIMEOUT = 25                        # seconds for the one request
ICS_RETRY_HOURS_AFTER_FAILURE = 20      # blocked / failing feed: at most one request a day
ICS_RETRY_HOURS_AFTER_SUCCESS = 20      # working feed: likewise (the saved copy is used in between)
ICS_MAX_BYTES = 3_000_000
# Feed categories the site knows (config `category:`) → the group each one is shown with on /events/.
ICS_CATEGORIES = {"neta65": "neta", "ics": "neta", "gv-calendar": "calendar", "lv-calendar": "calendar"}
# A place that is not known yet: never shown as an address (no map pin, no LOCATION in the calendar file).
_LOCATION_TBA = re.compile(
    r"(?i)^\s*(?:(?:venue|location|place|site|lugar|sede|sitio|local)\b\s*(?:(?:is|will\s+be|ser[áa])\s+)?[:\-–—]?\s*)?"
    r"(?:to\s+be\s+(?:announced|determined|confirmed)|tba|tbd|tbc|por\s+(?:anunciar(?:se)?|confirmar|definir|"
    r"determinar)|a\s+confirmar|pendiente|(?:se\s+)?anunciar[áa]\s+(?:pronto|m[áa]s\s+adelante))\s*[.!]?\s*$")
TBA_LOCATION = {"en": "Venue to be announced", "es": "Lugar por anunciarse"}


def location_is_tba(v: Any) -> bool:
    """'Venue to be announced' / 'Lugar por anunciarse' / 'TBA' …: a place that is not known yet."""
    return bool(clean_text(v)) and bool(_LOCATION_TBA.match(clean_text(v)))


def feed_specs(ctx: Ctx) -> list[dict]:
    """config/site.yml `sources.ics_feeds:` → [{key, url, label, label_es, category, group, host}]. A plain
    address (`- "https://…"`) works too; webcal:// is read as https://. An entry that cannot be used is
    skipped and named in status.json problems.ics_feeds (→ the Actions run summary)."""
    sources = ctx.cfg.get("sources") if isinstance(ctx.cfg.get("sources"), dict) else {}
    raw = sources.get("ics_feeds")
    if raw in (None, "", []):
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        ctx.raw_problems["ics_feeds"] = "config/site.yml sources.ics_feeds: not understood — it must be a list"
        return []
    out: list[dict] = []
    notes: list[str] = []
    keys: set[str] = set()
    for n, f in enumerate(raw, 1):
        spec = f if isinstance(f, dict) else {"url": f}
        url = re.sub(r"(?i)^webcal://", "https://", clean_text(spec.get("url")))
        if not re.match(r"(?i)^https?://[^\s/]+\.[^\s]+$", url):
            notes.append(f"ics_feeds entry {n}: “{spec.get('url')}” is not a calendar address (https://…) — skipped")
            continue
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        label = clean_text(spec.get("label") or spec.get("name")) or host
        key = slugify(clean_text(spec.get("key")) or label, 40).strip("-") or short_hash(url, 8)
        while key in keys:
            key += "-2"
        keys.add(key)
        category = clean_text(spec.get("category")).lower() or "ics"
        if category not in ICS_CATEGORIES:
            notes.append(f"ics_feeds entry {n} ({label}): category “{spec.get('category')}” is not one of "
                         f"{', '.join(sorted(ICS_CATEGORIES))} — “ics” used")
            category = "ics"
        out.append({"key": key, "url": url, "label": label, "label_es": clean_text(spec.get("label_es")) or label,
                    "category": category, "group": ICS_CATEGORIES[category], "host": host})
    if notes:
        for m in notes:
            log.warning("config/site.yml %s", m)
        ctx.raw_problems["ics_feeds"] = ("config/site.yml sources." + " / ".join(notes))[:2000]
    return out


def _challenge_page(resp: Any, head: str) -> bool:
    """Cloudflare's 'Just a moment…' check (or a similar bot wall) instead of the file."""
    h = {str(k).lower(): str(v).lower() for k, v in (getattr(resp, "headers", None) or {}).items()}
    return (h.get("cf-mitigated") == "challenge" or "just a moment" in head.lower()
            or "challenges.cloudflare.com" in head or "cf-chl" in head or "_cf_chl" in head)


def _response_text(resp: Any) -> str:
    """The answer as text. A calendar file is UTF-8 unless the server names another charset (RFC 5545);
    `requests` would read a "text/…" answer without a charset as ISO-8859-1 ("La ViÃ±a")."""
    headers = {str(k).lower(): str(v) for k, v in (getattr(resp, "headers", None) or {}).items()}
    if "charset=" in headers.get("content-type", "").lower():
        return resp.text
    return (resp.content or b"").decode("utf-8-sig", "replace")


def fetch_feed(url: str, user_agent: str) -> dict:
    """ONE plain request (no retries) → {"state": "ok" | "blocked" | "error", "http_status", "error", "text"}.
    "blocked" = the site's bot protection turned the robot away (Cloudflare's "Just a moment…" check, or
    HTTP 401 / 403 / 429); "error" = anything else (no answer, 404, 500, not a calendar file)."""
    import requests
    try:
        r = requests.get(url, timeout=ICS_TIMEOUT, allow_redirects=True, headers={
            "User-Agent": user_agent, "Accept": "text/calendar, text/plain;q=0.8, */*;q=0.1"})
    except Exception as e:      # no answer (timeout, DNS, TLS …) — never fatal, never retried this run
        return {"state": "error", "http_status": None, "error": f"no answer ({type(e).__name__})", "text": None}
    text = _response_text(r) if len(r.content or b"") <= ICS_MAX_BYTES else ""
    head = text[:4000]
    if r.status_code == 200 and "BEGIN:VCALENDAR" in head[:2000]:
        return {"state": "ok", "http_status": 200, "error": None, "text": text}
    if _challenge_page(r, head):
        return {"state": "blocked", "http_status": r.status_code, "text": None,
                "error": f"HTTP {r.status_code}: the site's bot protection (Cloudflare “Just a moment…” check) "
                         "turned the robot away"}
    if r.status_code in (401, 403, 429):
        return {"state": "blocked", "http_status": r.status_code, "text": None,
                "error": f"HTTP {r.status_code}: the site refused the robot"}
    if r.status_code != 200:
        return {"state": "error", "http_status": r.status_code, "text": None, "error": f"HTTP {r.status_code}"}
    return {"state": "error", "http_status": 200, "text": None,
            "error": "the answer is not a calendar file (.ics)" if text else "the calendar file is too large"}


def _feed_due(st: dict, now: datetime) -> bool:
    """May the feed be asked again? About once a day (ICS_RETRY_HOURS_*), whether it worked or not."""
    last = parse_iso(st.get("attempted")) if st.get("attempted") else None
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    wait = ICS_RETRY_HOURS_AFTER_SUCCESS if st.get("state") == "ok" else ICS_RETRY_HOURS_AFTER_FAILURE
    return (now - last).total_seconds() >= wait * 3600 - 300      # (5 min slack: the daily run drifts)


def ics_events(ctx: Ctx) -> list[dict]:
    """Events of the optional .ics feeds (config sources.ics_feeds) + each feed's health in ctx.feeds
    (→ status.json `feeds`, the /status/ page and the Actions run summary)."""
    specs = feed_specs(ctx)
    ctx.feeds = []
    if not specs:
        return []
    state_path = STATE_DIR / ICS_STATE_FILE
    state = read_json(state_path, {}) or {}
    if not isinstance(state, dict):
        state = {}
    ua = str(((ctx.cfg.get("sources") or {}).get("crawler") or {}).get("user_agent")
             or "NETA65-GrapevineCommitteeBot/2.0")
    out: list[dict] = []
    changed = False
    for spec in specs:
        url = spec["url"]
        st = dict(state.get(url)) if isinstance(state.get(url), dict) else {}
        health = {"key": spec["key"], "url": url, "label": spec["label"], "label_es": spec["label_es"],
                  "category": spec["category"], "group": spec["group"],
                  "state": st.get("state") or ("ok" if st.get("ics") else "never"),
                  "http_status": st.get("http_status"), "error": st.get("error"),
                  "last_success": st.get("fetched"), "last_attempt": st.get("attempted"),
                  "checked_this_run": False, "from_copy": False, "events_count": 0, "duplicates": 0, "notes": []}
        fresh = None
        if ctx.offline:
            log.info("ics feed %s: offline run — not asked (last copy used)", spec["key"])
        elif not _feed_due(st, ctx.now):
            log.info("ics feed %s: asked at %s (%s) — not again yet", spec["key"], st.get("attempted"), st.get("state"))
        else:
            res = fetch_feed(url, ua)
            ctx.feed_requests += 1
            st.update({"attempted": to_iso(ctx.now), "state": res["state"], "http_status": res["http_status"],
                       "error": res["error"]})
            if res["state"] == "ok":
                try:          # a copy is only kept when it can be read
                    fresh = _parse_ics(ctx, {"_ics": res["text"], "_spec": spec})
                    st.update({"fetched": st["attempted"], "ics": res["text"]})
                except Exception as e:
                    st.update({"state": "error", "error": f"the calendar file could not be read ({type(e).__name__})"})
            level = log.info if st["state"] == "ok" else log.warning
            level("ics feed %s → %s%s", spec["key"], st["state"], f" ({st['error']})" if st.get("error") else "")
            state[url] = st
            changed = True
            health.update({"state": st["state"], "http_status": st["http_status"], "error": st["error"],
                           "last_success": st.get("fetched"), "last_attempt": st["attempted"], "checked_this_run": True})
        evs = fresh
        if evs is None and st.get("ics"):
            try:
                evs = _parse_ics(ctx, {"_ics": st["ics"], "_spec": spec})
                health["from_copy"] = True
            except Exception as e:  # a broken copy never blocks the build
                log.warning("ics feed %s: the saved copy could not be read: %s", spec["key"], e)
        health["events_count"] = len(evs or [])
        out += evs or []
        ctx.feeds.append(health)
    if changed and not ctx.offline:
        try:
            write_json(state_path, state)
        except Exception as e:
            log.warning("could not save ics state: %s", e)
    return out


def _ics_text(v: Any) -> str:
    """An iCalendar value (vText, a list of them, CATEGORIES, bytes …) → plain text."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(t for t in (_ics_text(x) for x in v) if t)
    cats = getattr(v, "cats", None)                  # CATEGORIES
    if cats is not None:
        return ", ".join(str(c) for c in cats)
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return clean_text(str(v))


def _ics_list(v: Any) -> list:
    return list(v) if isinstance(v, (list, tuple)) else [] if v is None else [v]


def _parse_ics(ctx: Ctx, x: dict) -> list[dict]:
    """The events of one .ics text (The Events Calendar's export on neta65.org, Google Calendar …) in the
    site's event shape. Recurring events (RRULE) are expanded; CANCELLED events are left out; STATUS:
    TENTATIVE → extra.tentative; URL → the event's page; ATTACH → its flyer; CATEGORIES → tags."""
    from icalendar import Calendar
    from dateutil.rrule import rrulestr

    spec = x["_spec"]
    cal = Calendar.from_ical(x["_ics"])
    horizon_lo = ctx.now - timedelta(days=60)
    horizon_hi = ctx.now + timedelta(days=366)
    out = []
    for comp in cal.walk("VEVENT"):
        try:
            title = _ics_text(comp.get("SUMMARY"))
            status = _ics_text(comp.get("STATUS")).upper()
            if not title or status == "CANCELLED":
                continue
            start = comp.decoded("DTSTART")
            end = comp.decoded("DTEND") if comp.get("DTEND") else None
            all_day = not isinstance(start, datetime)
            desc = strip_html(_ics_text(comp.get("DESCRIPTION")))
            loc = re.sub(r"(?i),?\s*(?:united states(?: of america)?|usa|u\.s\.a?\.?|estados unidos)\s*$", "",
                         _ics_text(comp.get("LOCATION"))).strip(" ,")
            link = _ics_text(comp.get("URL"))
            link = link if re.match(r"(?i)^https?://", link) else (spec.get("link") or "/events/")
            online = next(iter(re.findall(r"https?://[^\s<>\"]*(?:zoom\.us|meet\.google|teams\.microsoft)[^\s<>\"]*",
                                          f"{loc} {desc}")), None)
            flyer = thumb = None
            for a in _ics_list(comp.get("ATTACH")):
                href = clean_text(str(a))
                if not re.match(r"(?i)^https?://", href):
                    continue
                kind = str(getattr(a, "params", {}).get("FMTTYPE") or "").lower()
                flyer = flyer or href
                if not thumb and (kind.startswith("image/") or re.search(r"(?i)\.(?:jpe?g|png|webp|gif)(?:\?|$)", href)):
                    thumb = href
            cats = [c for c in re.split(r"\s*,\s*", _ics_text(comp.get("CATEGORIES"))) if c]
            starts = [start]
            if comp.get("RRULE"):
                rule = comp.get("RRULE").to_ical().decode()
                base = start if isinstance(start, datetime) else datetime(start.year, start.month, start.day)
                naive = base.tzinfo is None
                lo = horizon_lo.replace(tzinfo=None) if naive else horizon_lo
                hi = horizon_hi.replace(tzinfo=None) if naive else horizon_hi
                starts = list(rrulestr(rule, dtstart=base).between(lo, hi, inc=True))[:60]
                if all_day:
                    starts = [s.date() for s in starts]
            dur = (end - start) if end is not None else None
            uid = str(comp.get("UID") or title)
            for s in starts:
                sdt = s if isinstance(s, datetime) else datetime(s.year, s.month, s.day, tzinfo=timezone.utc)
                if sdt.tzinfo is None:
                    sdt = sdt.replace(tzinfo=ctx.tz)
                if not (horizon_lo <= sdt <= horizon_hi):
                    continue
                s_iso = s.isoformat() if all_day else to_iso(s if s.tzinfo else s.replace(tzinfo=ctx.tz))
                e = (s + dur) if dur is not None else None
                if e is not None and all_day:
                    e = max(e - timedelta(days=1), s)        # DTEND of all-day events is exclusive
                e_iso = (e.isoformat() if all_day else to_iso(e if e.tzinfo else e.replace(tzinfo=ctx.tz))) if e else None
                city, state = city_state(loc)
                ex = {"start": s_iso, "end": e_iso, "all_day": all_day, "location": loc or None,
                      "online_url": online, "flyer_url": flyer, "flyer_thumb": thumb,
                      "city": city, "state": state, "feed": spec.get("key") or spec.get("url"), "uid": uid}
                if status == "TENTATIVE":
                    ex["tentative"] = True
                out.append({
                    "id": f"ev:ics:{short_hash(uid + '|' + s_iso)}", "source": "calendar", "kind": "event",
                    "url": link, "title": title, "summary": truncate(desc, 400),
                    "lang": T.detect_language(f"{title}. {desc}", "en"), "date": s_iso,
                    "first_seen": None, "image": thumb, "tags": [slugify(c, 40) for c in cats][:6],
                    "category": spec.get("category") or "ics", "status": "ok", "extra": ex,
                })
        except Exception as e:
            log.warning("ics event skipped: %s", e)
    return out


# --------------------------------------------------------------------------- one event, several sources
# The same real event can come from a feed AND from content/events, a dated Drive flyer or the committee's
# own events (the monthly meeting, config `recurring_events:` such as the CityWide Dallas booth): the
# neta65.org workshop feed lists the very workshops the chair wrote in content/events. It is shown ONCE.
# Two events are the same only when they START THE SAME LOCAL DAY and either
#   * link the same event page (normalized URL: scheme, "www.", trailing slash, ?query and #fragment
#     ignored — neta65.org/event/<slug>), or
#   * have the same shape (both all-day, or both at a time of day starting at most TITLE_MATCH_MAX_GAP_H
#     hours apart; never one over several days against one on a single day), are not in two different
#     cities, and have titles that name the same event (similar_titles — word for word when a city is not
#     known). So a workshop or a booth AT an assembly, on its first day, is never merged into the assembly.
# The hand-written event wins (it keeps its own Spanish); the feed only fills what it leaves out. Where
# the feed knows something the file does not say — the same event page on another date, another start
# time, the venue of an event whose file still says "Venue to be announced" — the chair gets a note
# (status.json feeds[].notes → the Actions run summary) to update the file.
_TITLE_STOP = {"the", "a", "an", "of", "and", "in", "at", "for", "on", "to", "with", "de", "la", "el", "los", "las",
               "del", "en", "y", "con", "para", "por", "un", "una", "al", "spanish", "espanol", "english", "ingles"}
_TITLE_ABBR = {"lv": ("la", "vina"), "gv": ("grapevine",), "gvlv": ("grapevine", "la", "vina"),
               "neta65": ("neta", "65")}
TITLE_MATCH_MAX_GAP_H = 2          # title route: two timed events start at most 2 hours apart
MULTI_DAY_MIN_H = 18               # a timed event is "over several days" only past 18 hours (as on the pages)
GENERATED_EVENTS = ("committee", "recurring")    # built from config/site.yml: a feed never changes them


def event_url_key(url: Any) -> str | None:
    """'https://www.neta65.org/event/lv-writing-workshop/?ical=1#x' → 'neta65.org/event/lv-writing-workshop'.
    None for an address that is not an event's own page (no path: a site's home page)."""
    s = clean_text(url)
    if not re.match(r"(?i)^(?:https?|webcal)://", s):
        return None
    p = urlsplit(s)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", unquote(p.path or "")).rstrip("/").lower()
    if not host or not path.strip("/"):
        return None
    return host + path


def title_tokens(s: Any) -> set[str]:
    """'LV Writing Workshop' → {'vina', 'writing', 'workshop'} (accents, case, little words ignored)."""
    words = re.findall(r"[a-z0-9]+", fold(clean_text(s)).replace("&", " and "))
    out: list[str] = []
    for w in words:
        out += _TITLE_ABBR.get(w, (w,))
    return {w for w in out if w not in _TITLE_STOP}


# Words every GV/LV event title shares — they say nothing about WHICH event it is. The KIND of event
# ("workshop", "booth", "assembly", "meeting") is NOT one of them: "GV/LV Booth at the Fall Assembly" is
# not the Fall Assembly.
_TITLE_GENERIC = {"grapevine", "vina", "neta", "65", "aa", "event", "evento", "area", "committee", "comite"}


def similar_titles(a: Any, b: Any, ignore: set[str] | frozenset[str] = frozenset(), exact: bool = False) -> bool:
    """Two titles of the same event, in any wording: the words that tell events apart ("writing workshop",
    "fall assembly", "taller de escritura") are the same, or nearly all shared (shared / all ≥ 0.75).
    `ignore`: words that say nothing here (the city both events are in, the year); `exact`: the telling
    words must be the same (used when a city is not known). 'LV Writing Workshop' ~ 'La Viña Writing
    Workshop (in Spanish) — Fort Worth' (city ignored); not ~ 'LV Recording Workshop'; 'Grapevine Workshop
    at the Spring Assembly' is not ~ 'NETA 65 Spring Assembly 2027'. Titles made only of common words
    must match word for word."""
    ta, tb = title_tokens(a) - set(ignore), title_tokens(b) - set(ignore)
    if not ta or not tb:
        return False
    da, db = ta - _TITLE_GENERIC, tb - _TITLE_GENERIC
    if not da or not db:
        return ta == tb
    if exact:
        return da == db
    return len(da & db) / len(da | db) >= 0.75


def _event_titles(ev: dict) -> list[str]:
    own = ((ev.get("extra") or {}).get("own_i18n") or {}).get("title") or {}
    return [t for t in [ev.get("title"), *own.values()] if clean_text(t)]


def _event_day(ctx: Ctx, ev: dict) -> str | None:
    return local_day(ctx, (ev.get("extra") or {}).get("start") or ev.get("date"))


def _event_city(ev: dict) -> str:
    ex = ev.get("extra") or {}
    if location_is_tba(ex.get("location")):
        return ""
    return fold(clean_text(ex.get("city") or city_state(ex.get("location"))[0] or ""))


def _is_all_day(ev: dict) -> bool:
    ex = ev.get("extra") or {}
    s = str(ex.get("start") or ev.get("date") or "").strip()
    return bool(ex.get("all_day")) or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))


def _event_last_day(ctx: Ctx, ev: dict) -> str | None:
    """The local day an event ends on (an end at midnight belongs to the day before); None without an end."""
    end = str((ev.get("extra") or {}).get("end") or "").strip()
    if not end:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        last = end
    else:
        t = ts(end)
        if t is None:
            return None
        last = datetime.fromtimestamp(t - 1, ctx.tz).date().isoformat()
    first = _event_day(ctx, ev)
    return max(last, first) if first else last


def _multi_day(ctx: Ctx, ev: dict) -> bool | None:
    """Over several days (an Area assembly)? None when the event gives no end."""
    first, last = _event_day(ctx, ev), _event_last_day(ctx, ev)
    if not first or not last:
        return None
    if last <= first:
        return False
    if _is_all_day(ev):
        return True
    ex = ev.get("extra") or {}
    s, e = ts(ex.get("start") or ev.get("date")), ts(ex.get("end"))
    return s is not None and e is not None and e - s > MULTI_DAY_MIN_H * 3600


def _same_shape(ctx: Ctx, keep: dict, other: dict) -> bool:
    """Both all-day or both timed (starting at most TITLE_MATCH_MAX_GAP_H apart); not one over several days
    and the other on one day (a file without an end may take the feed's end)."""
    if _is_all_day(keep) != _is_all_day(other):
        return False
    km, om = _multi_day(ctx, keep), _multi_day(ctx, other)
    if km and om is None:
        return False
    if km is not None and om is not None:
        if km != om or (km and _event_last_day(ctx, keep) != _event_last_day(ctx, other)):
            return False
    if not _is_all_day(keep):
        a = ts((keep.get("extra") or {}).get("start") or keep.get("date"))
        b = ts((other.get("extra") or {}).get("start") or other.get("date"))
        if a is None or b is None or abs(a - b) > TITLE_MATCH_MAX_GAP_H * 3600:
            return False
    return True


def same_event(ctx: Ctx, keep: dict, other: dict) -> str | None:
    """How `other` (a feed event) is the same real event as `keep`: "url", "title" or None. Both must start
    the same local day: an event page shared by two different dates is two dates (a series listed under
    one page, a page used again for a new workshop, or a date that changed — merge_feed_duplicates tells
    the chair about the last)."""
    kd, od = _event_day(ctx, keep), _event_day(ctx, other)
    if not kd or kd != od:
        return None
    ku, ou = event_url_key(keep.get("url")), event_url_key(other.get("url"))
    if ku and ku == ou:
        return "url"
    if not _same_shape(ctx, keep, other):
        return None
    kc, oc = _event_city(keep), _event_city(other)
    if kc and oc and kc != oc:
        return None
    ignore = {kd[:4]} | title_tokens(kc) | title_tokens(oc)
    exact = not (kc and oc)
    if any(similar_titles(a, b, ignore, exact) for a in _event_titles(keep) for b in _event_titles(other)):
        return "title"
    return None


def _written_in(ev: dict) -> str:
    """Where the chair wrote an event: 'content/events/<file>.md', or the Drive flyer's name."""
    ex = ev.get("extra") or {}
    if ex.get("file"):
        return str(ex["file"])
    if ev.get("category") == "flyer":
        return f"Drive flyer “{clean_text(ev.get('title'))}”"
    return f"“{clean_text(ev.get('title'))}”"


def _feed_name(ctx: Ctx, key: Any) -> str:
    label = next((h.get("label") for h in ctx.feeds if h.get("key") == key and h.get("label")), None)
    return f"the “{label}” calendar" if label else "the calendar"


def _local_clock(ctx: Ctx, v: Any) -> str:
    t = ts(v)
    if t is None:
        return str(v)
    d = datetime.fromtimestamp(t, ctx.tz)
    return f"{(d.hour % 12) or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def merge_feed_duplicates(ctx: Ctx, primary: list[dict], feed: list[dict]) -> list[dict]:
    """→ the feed events that are NOT on the calendar yet. A feed event that is the same real event as one
    already there (content/events, a dated Drive flyer, the committee meeting, a `recurring_events:` date)
    is dropped and only fills what a hand-written event leaves out; the same event in a second feed is
    dropped too. ctx.feeds gets the duplicates and the notes for the chair (`notes`: what the feed says
    that the file does not — see fill_from_feed — and an event page listed on another date)."""
    kept: list[dict] = []
    dups: dict[str, int] = {}
    notes: dict[str, list[str]] = {}

    def note(ev: dict, msg: str) -> None:
        key = (ev.get("extra") or {}).get("feed") or ""
        if msg not in notes.setdefault(key, []):
            notes[key].append(msg)

    for ev in feed:
        match = next(((p, how) for p in primary if (how := same_event(ctx, p, ev))), None)
        if match:
            for msg in fill_from_feed(ctx, match[0], match[1], ev):
                note(ev, msg)
            feed_key = ev["extra"].get("feed") or ""
            dups[feed_key] = dups.get(feed_key, 0) + 1
            continue
        if any(same_event(ctx, k, ev) == "url"
               or (k["extra"].get("uid") == ev["extra"].get("uid") and k["extra"].get("start") == ev["extra"].get("start"))
               for k in kept):
            continue                     # the same event in two feeds
        kept.append(ev)
    # The event page of an upcoming hand-written event, listed in a feed on OTHER dates only: a date that
    # changed on neta65.org (or a page used again for another workshop). Nothing is merged; the chair checks.
    for p in primary:
        key = event_url_key(p.get("url"))
        if not key or p.get("category") in GENERATED_EVENTS or event_end_ts(ctx, p) < ctx.now_ts:
            continue
        day = _event_day(ctx, p)
        on_page = [ev for ev in feed if event_url_key(ev.get("url")) == key]
        if not on_page or any(_event_day(ctx, ev) == day for ev in on_page):
            continue
        later = [ev for ev in on_page if event_end_ts(ctx, ev) >= ctx.now_ts]
        if later:
            days = ", ".join(sorted({d for d in (_event_day(ctx, ev) for ev in later) if d})[:3])
            shown = any(ev is k for ev in later for k in kept)
            note(later[0], f"{_written_in(p)}: {_feed_name(ctx, later[0]['extra'].get('feed'))} lists its event page "
                           f"({p.get('url')}) on {days}, but the file says {day}"
                           + (" (the calendar's date is on the Events page too)" if shown else "")
                           + ". If the date changed, correct start: and end: in the file; if it is another "
                             "event, give the file its own url:.")
    for h in ctx.feeds:
        h["duplicates"] = dups.get(h.get("key"), 0)
        h["notes"] = notes.get(h.get("key"), [])
    if dups:
        log.info("ics feeds: %d event(s) already on the calendar (content/events, a flyer, the committee's own "
                 "events) — shown once", sum(dups.values()))
    for msg in (m for ms in notes.values() for m in ms):
        log.warning("ics feeds: check %s", msg)
    return kept


def fill_from_feed(ctx: Ctx, keep: dict, how: str, dup: dict) -> list[str]:
    """The hand-written event wins; the feed only fills what it leaves out → notes for the chair where the
    feed says something the file does not. The event-page link, the flyer and the online link are copied
    only on a sure match (the same event page, or the same start); a place the file gives as "Venue to be
    announced" is replaced by the feed's real venue on a sure match too (the chair is told to update the
    file). Nothing is copied onto the committee's own events (the meeting, `recurring_events:`)."""
    kx, dx = keep.setdefault("extra", {}), dup.get("extra") or {}
    kx["also_in_feed"] = dx.get("feed")
    kx["feed_match"] = how
    if keep.get("category") in GENERATED_EVENTS:
        return []
    notes: list[str] = []
    cal = _feed_name(ctx, dx.get("feed"))
    same_start = _same_start(kx.get("start"), dx.get("start"))
    sure = how == "url" or same_start
    if sure:
        for k in ("flyer_url", "flyer_thumb", "online_url"):
            if not kx.get(k) and dx.get(k):
                kx[k] = dx[k]
        if not re.match(r"(?i)^https?://", str(keep.get("url") or "")) and re.match(r"(?i)^https?://", str(dup.get("url") or "")):
            keep["url"] = dup["url"]
    feed_loc = clean_text(dx.get("location"))
    mine = clean_text(kx.get("location"))
    if feed_loc and not location_is_tba(feed_loc):
        if not mine:
            kx["location"] = feed_loc
            kx["city"], kx["state"] = kx.get("city") or dx.get("city"), kx.get("state") or dx.get("state")
        elif location_is_tba(mine) and sure:
            kx["location"], kx["city"], kx["state"] = feed_loc, dx.get("city"), dx.get("state")
            own = kx.get("own_i18n") if isinstance(kx.get("own_i18n"), dict) else {}
            own_loc = {lang: v for lang, v in (own.get("location") or {}).items() if not location_is_tba(v)}
            if own_loc:
                own["location"] = own_loc
            else:
                own.pop("location", None)
            notes.append(f"{_written_in(keep)}: the file says the venue is not known yet; {cal} gives “{feed_loc}”, "
                         "which the site shows now. Put it in location: (and delete location_es:).")
        elif location_is_tba(mine):
            notes.append(f"{_written_in(keep)}: the file says the venue is not known yet; {cal} gives “{feed_loc}”. "
                         "If that is the venue, put it in location: (and delete location_es:).")
    if not kx.get("end") and dx.get("end") and bool(kx.get("all_day")) == bool(dx.get("all_day")) and same_start:
        kx["end"] = dx["end"]
    if not same_start and not _is_all_day(keep) and not _is_all_day(dup):
        notes.append(f"{_written_in(keep)}: {cal} says it starts at {_local_clock(ctx, dx.get('start'))}, "
                     f"the file says {_local_clock(ctx, kx.get('start'))} (Central time). If the time changed, "
                     "correct start: and end: in the file.")
    return notes


def _same_start(a: Any, b: Any) -> bool:
    return bool(a) and (a == b or (ts(a) is not None and ts(a) == ts(b)))


def event_end_ts(ctx: Ctx, ev: dict) -> float:
    ex = ev.get("extra") or {}
    v = ex.get("end") or ex.get("start") or ev.get("date")
    s = str(v or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):          # all-day: until the end of that local day
        d = date.fromisoformat(s)
        return datetime(d.year, d.month, d.day, 23, 59, tzinfo=ctx.tz).timestamp()
    return ts(s) or 0.0


def event_start_ts(ev: dict) -> float:
    return ts((ev.get("extra") or {}).get("start") or ev.get("date")) or 0.0


def build_events(ctx: Ctx) -> list[dict]:
    evs: dict[str, dict] = {}
    try:        # `meeting: skip_dates` that are not a meeting day: ignored, and the chair is told
        notes = meeting_skip_notes(ctx.cfg.get("meeting") if isinstance(ctx.cfg.get("meeting"), dict) else {})
    except Exception as e:
        notes = [f"skip_dates could not be read ({type(e).__name__})"]
    if notes:
        log.warning("config/site.yml meeting: %s", "; ".join(notes))
        ctx.raw_problems["meeting"] = ("config/site.yml meeting: " + "; ".join(notes))[:2000]
    try:
        committee = committee_meetings(ctx)
    except Exception as e:  # a bad `meeting:` edit in config/site.yml must not stop the daily update
        log.error("committee meetings skipped — config/site.yml `meeting:` problem: %s: %s", type(e).__name__, e)
        ctx.raw_problems["meeting"] = f"config/site.yml meeting: {type(e).__name__}: {e}"[:200]
        committee = []
    try:
        recurring = recurring_events(ctx)
    except Exception as e:  # likewise for `recurring_events:` (recurring_events() already skips bad entries)
        log.error("recurring events skipped — config/site.yml `recurring_events:` problem: %s: %s", type(e).__name__, e)
        ctx.raw_problems["recurring_events"] = f"config/site.yml recurring_events: {type(e).__name__}: {e}"[:200]
        recurring = []
    flyers = flyer_events(ctx)
    manual = safe_each([i for i in ctx.items("manual_events") if i.get("kind") == "event"], prep, "event")
    try:        # optional outside calendars: never allowed to stop the update
        feed = ics_events(ctx)
        # The same real event in a feed and on the calendar already (content/events, a dated flyer, the
        # committee meeting, a recurring_events date) is shown once: the hand-written one wins, the feed
        # only fills what it leaves out (never on the committee's own events).
        feed = merge_feed_duplicates(ctx, manual + flyers + recurring + committee, feed)
    except Exception as e:
        log.error("ics feeds skipped: %s: %s", type(e).__name__, e)
        feed = []
    groups = [("committee", committee), ("recurring", recurring), ("flyer", flyers),
              ("external", safe_each([i for i in ctx.items("events_external") if i.get("kind") == "event"], prep, "event")),
              ("manual", manual), ("ics", feed)]
    file_notes: list[str] = []          # slips in content/events files (→ status.json problems.content_events)
    for _label, items in groups:
        for ev in items:
            ev.setdefault("extra", {})
            ex = ev["extra"]
            if not ex.get("start"):
                ex["start"] = ev.get("date")
            if not ex.get("start"):
                continue
            # extra.tentative: present (true) only on an event whose details are not final yet
            if ex.get("tentative") is True or (ex.get("tentative") and str(ex["tentative"]).strip().lower()
                                               in ("1", "true", "yes", "y", "si", "sí", "on")):
                ex["tentative"] = True
            else:
                ex.pop("tentative", None)
            # a place that is not known yet ("Venue to be announced"): never shown as an address. The
            # file's own `location` decides; `location_es` / `location_en` only when there is no location.
            base_loc = clean_text(ex.get("location"))
            own_loc = {lang: clean_text(v) for lang, v in ((ex.get("own_i18n") or {}).get("location") or {}).items()
                       if clean_text(v)}
            if location_is_tba(base_loc) or (not base_loc and own_loc
                                             and all(location_is_tba(v) for v in own_loc.values())):
                ex["location_tba"] = True
            else:
                ex.pop("location_tba", None)
            if _label == "manual" and base_loc:
                stale = [f"location_{lang}" for lang, v in own_loc.items() if location_is_tba(v)]
                if stale and not location_is_tba(base_loc):
                    file_notes.append(f"{_written_in(ev)}: {' / '.join(stale)} says the venue is not known yet, but "
                                      f"location: gives “{base_loc}” — that place is shown in both languages. "
                                      f"Delete the {' / '.join(stale)} line.")
                real = [f"location_{lang}" for lang, v in own_loc.items() if not location_is_tba(v)]
                if real and location_is_tba(base_loc):
                    file_notes.append(f"{_written_in(ev)}: location: says the venue is not known yet, but "
                                      f"{' / '.join(real)} gives a place — put the place in location: too.")
            evs.setdefault(ev["id"], ev)
    if file_notes:
        for m in file_notes:
            log.warning("content/events: %s", m)
        ctx.raw_problems["content_events"] = " / ".join(file_notes)[:2000]
    cutoff = ctx.now_ts - 86400
    upcoming = [e for e in evs.values() if event_end_ts(ctx, e) >= cutoff]
    over = [e for e in evs.values() if event_end_ts(ctx, e) < cutoff and e.get("category") != "committee"]
    # Past dates of a recurring event (the last RECURRING_KEEP_PAST_DAYS days) are kept for the calendar
    # feed only — they do not use up the PAST_EVENTS_KEEP places of real past events.
    past = [e for e in over if e.get("category") != "recurring"]
    upcoming.sort(key=lambda e: (event_start_ts(e), e["id"]))
    past.sort(key=lambda e: (-event_start_ts(e), e["id"]))
    kept = past[:PAST_EVENTS_KEEP] + [e for e in over if e.get("category") == "recurring"]
    kept.sort(key=lambda e: (-event_start_ts(e), e["id"]))
    for e in upcoming:
        e["extra"]["past"] = False
    for e in kept:
        e["extra"]["past"] = True
    return upcoming + kept


# =========================================================================== translation
def other(lang: str) -> str:
    return "es" if lang == "en" else "en"


# Short labels inside `extra` that get their own i18n entry (item kind → extra keys).
LABEL_FIELDS = {"article": ("section", "topic", "issue_theme", "department")}
# Fields that are titles: their machine English (from Spanish) is written in Title Case, like the
# Grapevine's own titles ("Atados por la misma enfermedad" → "Bound by the Same Illness").
TITLE_FIELDS = ("title", "section", "topic", "issue_theme", "department", "album", "theme")


def en_title_case(pair: dict, src: str | None) -> dict:
    """Title Case for the machine-translated English of a Spanish title (in place)."""
    if src == "es" and isinstance(pair.get("en"), str):
        pair["en"] = T.title_case_en(pair["en"])
    return pair


def text_fields(it: dict) -> list[tuple[str, str, bool, str | None]]:
    """(field, original text, is_markdown, source language or None = the item's) that get an
    i18n entry by machine translation."""
    ex = it.get("extra") or {}
    kind = it.get("kind")
    fields: list[tuple[str, str, bool, str | None]] = [
        ("title", it.get("title") or "", False, None), ("summary", it.get("summary") or "", False, None)]
    if kind == "announcement" or (kind == "event" and ex.get("body_md")):
        fields.append(("body_md", str(ex.get("body_md") or ""), True, None))
    for k in LABEL_FIELDS.get(kind or "", ()):
        v = ex.get(k)
        if isinstance(v, str) and clean_text(v):          # (articles' "department" is sometimes a bool)
            fields.append((k, clean_text(v), False, None))
    album = ex.get("album")
    if it.get("source") == "drive" and isinstance(album, str) and clean_text(album):
        a = clean_text(album)
        fields.append(("album", a, False, T.detect_language(a, it.get("lang") if it.get("lang") in LANGS else "en")))
    if kind == "meeting" and not weekly_open_labels(it):  # structured fields missing → translate the text
        fields += [(k, clean_text(ex.get(k)), False, None) for k in ("day", "time", "time_central", "sentence")
                   if isinstance(ex.get(k), str) and ex.get(k)]
    if kind == "topic" and clean_text(ex.get("theme")) and clean_text(ex.get("theme")) != it.get("title"):
        fields.append(("theme", clean_text(ex.get("theme")), False, None))
    return fields


def own_words(it: dict) -> dict[str, dict[str, str]]:
    """The author's own translations of a hand-written item (content/events, content/announcements:
    `title_es`, `summary_es` … → announcements.py → `extra.own_i18n`): {field: {lang: text}}. They are
    used instead of a machine translation, and only a language left out is machine-translated."""
    own = (it.get("extra") or {}).get("own_i18n")
    if not isinstance(own, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for field, texts in own.items():
        if isinstance(texts, dict):
            kept = {lang: v.strip() if field == "body_md" else clean_text(v)
                    for lang, v in texts.items() if lang in LANGS and isinstance(v, str) and v.strip()}
            if kept:
                out[str(field)] = kept
    return out


def location_pair(it: dict, own: dict[str, dict[str, str]], src: str | None) -> dict[str, str] | None:
    """An event's place in both languages (`i18n.location`) — never machine-translated (addresses and
    group names stay as written). The file's own `location` is its language's text; `location_es`
    (`location_en` in a Spanish file) the other one (`extra.own_i18n.location`). A place that is not known
    yet ("Venue to be announced", "TBA") without the other language written gets the site's own words
    ("Lugar por anunciarse"). None when there is nothing language-specific: the pages then show
    `extra.location` as it is. The file's `location` decides whether the place is known: a
    `location_es: "Lugar por anunciarse"` left behind after the venue was confirmed never hides it
    (build_events notes the slip in problems.content_events)."""
    if it.get("kind") != "event":
        return None
    base = clean_text((it.get("extra") or {}).get("location"))
    mine = dict(own.get("location") or {})
    if base and not location_is_tba(base):
        mine = {lang: v for lang, v in mine.items() if not location_is_tba(v)}
    tba = location_is_tba(base) or (not base and bool(mine) and all(location_is_tba(v) for v in mine.values()))
    if not mine and not tba:
        return None
    lang0 = src if src in LANGS else "en"
    pair: dict[str, str] = {}
    for lang in LANGS:
        if lang == lang0 and base:
            pair[lang] = base
        elif mine.get(lang):
            pair[lang] = mine[lang]
        elif tba:
            pair[lang] = TBA_LOCATION[lang]
        else:
            pair[lang] = base or next(iter(mine.values()), "")
    return pair


def local_fields(it: dict) -> dict[str, dict]:
    """i18n entries written by rules (never machine-translated): issue labels, Weekly Open times,
    the writer's place ("Nueva Jersey" → "New Jersey"; from extra.geo, see scripts/sync/geo.py)."""
    ex = it.get("extra") or {}
    out: dict[str, dict] = {}
    if it.get("kind") in ("article", "topic") and (ex.get("issue_key") or ex.get("issue_label")):
        lab = issue_label(ex.get("publication") or it.get("category"), ex.get("issue_key"), ex.get("issue_label"))
        if lab:
            out["issue_label"] = lab
        elif ex.get("issue_label"):
            out["issue_label"] = {"en": clean_text(ex["issue_label"]), "es": clean_text(ex["issue_label"])}
    geo = ex.get("geo")
    if it.get("kind") == "article" and isinstance(geo, dict) and geo.get("label_en"):
        out["author_location"] = {"en": geo["label_en"], "es": geo.get("label_es") or geo["label_en"]}
    if it.get("kind") == "meeting":
        out.update(weekly_open_labels(it))
    return out


def _clock(h: int, m: int, lang: str) -> str:
    if (h, m) == (12, 0):
        return "Noon" if lang == "en" else "mediodía"
    if lang == "en":
        return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    return f"{(h % 12) or 12}:{m:02d} {'a. m.' if h < 12 else 'p. m.'}"


def weekly_open_labels(it: dict) -> dict[str, dict]:
    """Hand-written bilingual day/time strings for the Grapevine Weekly Open meeting, built from
    the structured fields weekly_open.py extracts (weekday, start_local, timezone, next_start)."""
    ex = it.get("extra") or {}
    wd = str(ex.get("weekday") or "").lower()
    if wd not in WEEKDAYS or not re.fullmatch(r"\d{1,2}:\d{2}", str(ex.get("start_local") or "")):
        return {}
    tzname = str(ex.get("timezone") or "America/New_York")
    try:
        own_tz, central = ZoneInfo(tzname), ZoneInfo("America/Chicago")
    except Exception:
        return {}
    h, m = (int(x) for x in str(ex["start_local"]).split(":"))
    ref = parse_iso(ex.get("next_start")) if ex.get("next_start") else None
    if ref is None:
        today = datetime.now(own_tz).date()
        ref = datetime(today.year, today.month, today.day, h, m, tzinfo=own_tz)
    c = ref.astimezone(central)
    i = WEEKDAYS.index(wd)
    ci = WEEKDAYS.index(WEEKDAYS[c.weekday()])        # (a meeting near midnight can change day)
    tz_en, tz_es = TZ_NAMES.get(tzname, (tzname.split("/")[-1].replace("_", " "), tzname.split("/")[-1].replace("_", " ")))
    own = {"en": f"{_clock(h, m, 'en')} {tz_en}", "es": f"{_clock(h, m, 'es')} ({tz_es})"}
    cen = {"en": f"{_clock(c.hour, c.minute, 'en')} Central", "es": f"{_clock(c.hour, c.minute, 'es')} (hora del Centro)"}
    at_es = "al" if (c.hour, c.minute) == (12, 0) else "a las" if c.hour % 12 != 1 else "a la"
    when = {"en": f"{WEEKDAYS_EN[ci]} at {cen['en']}", "es": f"{WEEKDAYS_ES[ci]} {at_es} {cen['es']}"}
    zid, pw = clean_text(ex.get("zoom_id")), clean_text(ex.get("passcode"))
    same_tz = tzname == "America/Chicago"
    en = f"Join live on {WEEKDAYS_EN[ci]} at {cen['en']}" + ("" if same_tz else f" ({own['en']})")
    es = f"Únete en vivo los {WEEKDAYS_ES[ci].lower()} {at_es} {cen['es'].replace(' (hora del Centro)', '')}, hora del Centro" + \
         ("" if same_tz else f" ({_clock(h, m, 'es')}, {tz_es})")
    if zid:
        en += f" on Zoom — meeting ID {zid}" + (f", passcode {pw}" if pw else "")
        es += f", por Zoom: ID de reunión {zid}" + (f", código de acceso {pw}" if pw else "")
    return {
        "day": {"en": WEEKDAYS_EN[i], "es": WEEKDAYS_ES[i]},
        "time": own,
        "time_central": cen, "when": when,
        "sentence": {"en": en + ".", "es": es + "."},
    }


def source_lang(it: dict) -> str | None:
    """Language to translate FROM (None = keep the original in both languages)."""
    lang = it.get("lang")
    if lang in LANGS:
        return lang
    if lang in (None, "", "und"):
        det = T.detect_language(f"{it.get('title') or ''}. {it.get('summary') or ''}", None)
        if det in LANGS:
            it["lang"] = det
            return det
    return None


def _letters_key(s: str) -> str:
    """'UN DIA A LA VEZ' and 'Un día a la vez' → the same key (accents, case and spacing ignored)."""
    return re.sub(r"\s+", " ", T.fold(unicodedata.normalize("NFC", str(s)))).strip().lower()


class I18n:
    """Collects every text to translate, runs the translator in priority order (so a time-boxed
    first run translates the newest/most visible things first), then fills i18n/machine."""

    def __init__(self, translator: T.Translator | None):
        self.tr = translator
        self.jobs: dict[tuple, tuple] = {}
        self.done: dict[tuple, tuple] = {}
        self.pending = 0
        self.seconds = 0.0

    def want(self, text: str, src: str | None, prio: tuple, md: bool = False) -> None:
        if not text or src not in LANGS:
            return
        key = (src, other(src), md, text)
        if key not in self.jobs or prio < self.jobs[key]:
            self.jobs[key] = prio

    def run(self, chunk: int = 200) -> None:
        t0 = time.monotonic()
        order = sorted(self.jobs, key=lambda k: (self.jobs[k], k))
        if self.tr is not None:
            for i in range(0, len(order), chunk):
                groups: dict[tuple, list] = {}
                for k in order[i:i + chunk]:
                    groups.setdefault(k[:3], []).append(k)
                for (src, tgt, md), keys in sorted(groups.items()):
                    try:
                        if md:
                            for k in keys:
                                self.done[k] = self.tr.translate_markdown(k[3], src, tgt)
                        else:
                            for k, r in zip(keys, self.tr.translate([k[3] for k in keys], src, tgt)):
                                self.done[k] = r
                    except Exception as e:     # translation trouble never breaks the build
                        log.error("translation batch failed (%s: %s) — keeping originals", type(e).__name__, e)
                done = min(i + chunk, len(order))
                if done % 1000 < chunk and done < len(order):
                    log.info("translated %d/%d texts (%.0fs)", done, len(order), time.monotonic() - t0)
        self.pending = sum(1 for k in self.jobs if self.done.get(k, (None,))[0] is None)
        self.seconds = time.monotonic() - t0

    def get(self, text: str, src: str, md: bool = False) -> tuple[str | None, bool]:
        return self.done.get((src, other(src), md, text), (None, False))

    def respell(self, text: str, src: str) -> str:
        """The text as shown in its OWN language. overrides.yml may give an entry in the text's own
        language to restore what the source lost — accents and capitals only ("UN DIA A LA VEZ":
        { es: "Un día a la vez" }). Any other same-language change is ignored: the original wording
        is never rewritten."""
        overrides = getattr(self.tr, "overrides", None)
        ov = overrides.get(text, src) if overrides is not None else None
        if ov and _letters_key(ov) == _letters_key(text) and ov != text:
            return ov
        return text

    def pair(self, text: str, src: str | None, md: bool = False) -> tuple[dict, bool]:
        """→ ({'en': …, 'es': …}, machine_translated?)"""
        if src not in LANGS or not text:
            return {"en": text, "es": text}, False
        out, machine = self.get(text, src, md)
        tgt = other(src)
        shown = text if md else self.respell(text, src)
        val = {src: shown, tgt: out if out is not None else shown}
        return {"en": val["en"], "es": val["es"]}, bool(out is not None and machine and out != text)

    def apply(self, it: dict) -> None:
        if it.pop("_fixed_i18n", False):
            return
        src = source_lang(it)
        i18n, machine = {}, set()
        own = own_words(it)
        for field, text, md, fsrc in text_fields(it):
            s = fsrc or src
            i18n[field], m = self.pair(text, s, md)
            mine = {lang: v for lang, v in (own.get(field) or {}).items() if lang != s}
            if mine:               # the author's own words in the other language: never "auto-translated"
                i18n[field].update(mine)
                m = False
            if m:
                machine.add(other(s))  # type: ignore[arg-type]
                if field in TITLE_FIELDS and it.get("kind") != "post":    # (Instagram "titles" are captions)
                    en_title_case(i18n[field], s)
        for field in ("body_md",):          # a hand-written description where the file itself has no text
            if field in own and field not in i18n:
                base = str((it.get("extra") or {}).get(field) or "")
                i18n[field] = {lang: own[field].get(lang) or base for lang in LANGS}
        loc = location_pair(it, own, src)
        if loc:
            i18n["location"] = loc
        i18n.update(local_fields(it))
        it["i18n"] = i18n
        it["machine"] = sorted(machine)


def plan_translations(ctx: Ctx, cols: dict[str, list[dict]], wn_refs: set[int], i18n: I18n) -> None:
    small = {"announcements", "events", "weekly_open", "editorial"}
    for name, items in cols.items():
        for it in items:
            if it.get("_fixed_i18n"):
                continue
            src = source_lang(it)
            when = -(ctx.effective_ts(it, raw_source(it)) or ts(it.get("date")) or ts(it.get("first_seen")) or 0.0)
            own = own_words(it)
            for field, text, md, fsrc in text_fields(it):
                s = fsrc or src
                if s in LANGS and other(s) in (own.get(field) or {}):  # type: ignore[arg-type]
                    continue                   # written by hand in the other language: nothing to translate
                if name in small:
                    tier = 0
                elif id(it) in wn_refs:
                    tier = 1 if field == "title" else 2
                elif field not in ("summary", "body_md"):
                    tier = 3               # titles and short labels
                else:
                    tier = 4
                i18n.want(text, fsrc or src, (tier, when), md)


def raw_source(it: dict) -> str:
    s, k = it.get("source"), it.get("kind")
    return {"youtube": "youtube", "podcast": "podcasts", "instagram": "instagram", "crawl": "pdfs",
            "drive": "drive", "calendar": "events_external"}.get(s) or (
        "editorial" if k == "topic" else "weekly_open" if k == "meeting" else
        "articles" if s in ("grapevine", "lavina") else
        "manual_events" if (s == "committee" and k == "event") else "announcements")


# =========================================================================== envelope metadata
def _pub_lang(pub: str | None, text: str) -> str:
    return "es" if pub == "lv" else "en" if pub == "gv" else T.detect_language(text, "en")


def build_meta(ctx: Ctx, i18n: I18n) -> tuple[dict[str, dict], list[tuple[dict, str, str, str]]]:
    """Top-level keys (besides items) of the site files, from the raw envelopes:
    instagram.profiles, videos.playlists, episodes.shows, articles.issues."""
    meta: dict[str, dict] = {"instagram": {}, "videos": {}, "episodes": {}, "articles": {}}
    wanted: list[tuple[dict, str, str, str]] = []           # (target dict, field, text, src)

    # Instagram account profiles (followers, avatar …): names are brand names → no translation.
    prof = (ctx.raw.get("instagram") or {}).get("profiles")
    if isinstance(prof, dict):
        order = [a.get("key") for a in ((ctx.cfg.get("sources") or {}).get("instagram") or {}).get("accounts") or []
                 if isinstance(a, dict)]
        keys = sorted(prof, key=lambda k: (order.index(k) if k in order else 99, str(k)))
        meta["instagram"]["profiles"] = {k: {kk: vv for kk, vv in prof[k].items() if not str(kk).startswith("_")}
                                         for k in keys if isinstance(prof[k], dict)}

    # YouTube playlists: [{id, title, lang, count, url, i18n.title}]
    pls = (ctx.raw.get("youtube") or {}).get("playlists")
    if isinstance(pls, list):
        rows = []
        for p in pls:
            if not isinstance(p, dict) or not clean_text(p.get("title")):
                continue
            r = {k: v for k, v in p.items() if not str(k).startswith("_")}
            r["title"] = clean_text(p["title"])
            src = p.get("lang") if p.get("lang") in LANGS else T.detect_language(r["title"], "en")
            r["_src"] = src if src in LANGS else "en"
            wanted.append((r, "title", r["title"], r["_src"]))
            rows.append(r)
        meta["videos"]["playlists"] = rows

    # Podcast shows: [{key, name, title, description, image, language, web, apple, spotify, amazon, episodes}]
    shows = (ctx.raw.get("podcasts") or {}).get("shows")
    if isinstance(shows, list):
        order = [s.get("key") for s in ((ctx.cfg.get("sources") or {}).get("podcasts") or []) if isinstance(s, dict)]
        rows = []
        for s in shows:
            if not isinstance(s, dict) or not s.get("key"):
                continue
            r = {k: v for k, v in s.items() if not str(k).startswith("_")}
            r["title"] = clean_text(s.get("title") or s.get("name"))
            r["description"] = clean_text(strip_html(s.get("description") or ""))
            lang = str(s.get("language") or "")[:2].lower()
            r["_src"] = lang if lang in LANGS else T.detect_language(f"{r['title']}. {r['description']}", "en")
            for f in ("title", "description"):
                wanted.append((r, f, r[f], r["_src"]))
            rows.append(r)
        rows.sort(key=lambda r: (order.index(r["key"]) if r["key"] in order else 99, str(r["key"])))
        meta["episodes"]["shows"] = rows

    # Magazine issues: {"gv:2026-10": {...}} or [...] → newest first, with local month labels
    iss = (ctx.raw.get("articles") or {}).get("issues")
    rows_in = list(iss.values()) if isinstance(iss, dict) else list(iss) if isinstance(iss, list) else []
    rows = []
    for s in rows_in:
        if not isinstance(s, dict):
            continue
        pub, key = s.get("publication"), str(s.get("key") or "")
        if not pub or not re.fullmatch(r"\d{4}-\d{2}", key):
            continue
        r = {"id": f"{pub}:{key}", "publication": pub, "key": key,
             "label": clean_text(s.get("label")), "theme": clean_text(s.get("theme")),
             "description": clean_text(s.get("description")), "url": s.get("url"), "image": s.get("image"),
             "cover": s.get("cover"), "hub": s.get("hub")}
        r["_src"] = _pub_lang(pub, f"{r['theme']}. {r['description']}")
        for f in ("theme", "description"):
            if r[f]:
                wanted.append((r, f, r[f], r["_src"]))
        rows.append(r)
    rows.sort(key=lambda r: (r["key"], r["publication"]), reverse=True)
    meta["articles"]["issues"] = rows

    for _r, _f, text, src in wanted:
        i18n.want(text, src, (0, 0.0))
    return meta, wanted


def finish_meta(meta: dict[str, dict], wanted: list[tuple[dict, str, str, str]], i18n: I18n) -> None:
    for r, field, text, src in wanted:
        pair, machine = i18n.pair(text, src)
        r.setdefault("i18n", {})[field] = pair
        r.setdefault("machine", set())
        if machine:
            r["machine"].add(other(src))
            if field in TITLE_FIELDS:
                en_title_case(pair, src)
    for r in meta["articles"].get("issues", []):
        lab = issue_label(r["publication"], r["key"], r.get("label"))
        r.setdefault("i18n", {})["label"] = lab or {"en": r["label"], "es": r["label"]}
        r.setdefault("machine", set())
    for rows in (meta["videos"].get("playlists", []), meta["episodes"].get("shows", []),
                 meta["articles"].get("issues", [])):
        for r in rows:
            r["lang"] = r.get("lang") if r.get("lang") in LANGS else r.pop("_src", "en")
            r.pop("_src", None)
            r["machine"] = sorted(r.get("machine") or [])


# =========================================================================== What's New
# Ids the site already uses on its pages (mirror of RESERVED_IDS in eleventy/filters/committee.js).
_RESERVED_ANCHORS = {"main", "mobile-drawer", "subscribe", "how-docs", "how-to-post", "share-photos", "albums",
                     "ev-upcoming-title", "ev-next-title", "cm-preview", "cm-preview-title", "cm-lb-i18n", "item"}


def album_slug(album: str | None) -> str | None:
    """The anchor the /photos/ page gives an album — the same computation as
    itemAnchor(slugify(album, 80), "") in eleventy/filters/committee.js — or None if it has none."""
    s = unicodedata.normalize("NFKD", str(album or ""))
    s = re.sub(r"[̀-ͯ]", "", s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:80].rstrip("-") or "item"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", s) or s in _RESERVED_ANCHORS \
            or re.match(r"(?:month-|docs-|cm-)", s):
        return None
    return s


def plan_whatsnew(ctx: Ctx, cols: dict[str, list[dict]]) -> list[tuple[float, dict]]:
    """[(news timestamp, item)] — originals by reference (copied after translation); photo
    groups are new dicts ('N new photos in <album>'). Editorial themes and the Weekly Open
    meeting are never "news" (their date is a deadline or nothing)."""
    out: list[tuple[float, dict]] = []
    for name in ("articles", "pdfs", "videos", "episodes", "instagram", "announcements"):
        for it in cols.get(name, []):
            if it.get("kind") in NEVER_NEW_KINDS or ctx.back_catalog(it):
                continue
            wn = ctx.effective_ts(it, raw_source(it))
            if wn is not None:
                out.append((wn, it))
    groups: dict[tuple[str, str], list[tuple[float, dict]]] = {}
    for it in cols.get("drive", []):
        wn = ctx.effective_ts(it, "drive")
        if wn is None:
            continue
        if it.get("kind") != "photo":
            if not it["extra"].get("event_date"):     # dated flyers appear as their event instead
                out.append((wn, it))
            continue
        ex = it["extra"]
        album = clean_text(ex.get("album") or " / ".join(ex.get("path") or [])) or "Photos"
        day = datetime.fromtimestamp(wn, ctx.tz).date().isoformat()
        groups.setdefault((album, day), []).append((wn, it))
    for (album, day), members in sorted(groups.items()):
        members.sort(key=lambda x: (-x[0], x[1]["id"]))
        if len(members) == 1:
            out.append(members[0])
            continue
        first = members[0][1]
        n = len(members)
        fx = first["extra"]
        slug = album_slug(fx.get("album") or " / ".join(fx.get("path") or []))
        g = {
            "id": f"drive:album:{short_hash(album + '|' + day)}", "source": "drive", "kind": "photo",
            # /photos/ gives every album an anchor with this slug (photoAlbums() in committee.js)
            "url": f"/photos/#{slug}" if slug else "/photos/",
            "title": f"{n} new photos in {album}", "summary": "", "lang": "en", "date": day,
            "first_seen": min((m[1].get("first_seen") or "") for m in members) or None,
            "image": first.get("image"), "tags": ["album"], "category": "photos", "status": "ok",
            "extra": {"album": album, "count": n, "is_group": True, "photo_ids": [m[1]["id"] for m in members][:24],
                      "thumbs": [m[1].get("image") or m[1]["extra"].get("thumb_url") for m in members[:4]],
                      "panel": first["extra"].get("panel"), "panel_label": first["extra"].get("panel_label"),
                      "album_slug": slug or slugify(album)},
            "_album": album,
        }
        out.append((max(m[0] for m in members), g))
    for it in cols.get("events", []):
        if it.get("category") in SCHEDULED_EVENT_CATEGORIES or it["extra"].get("past"):
            continue
        f = ctx.found_ts(it, raw_source(it) if it.get("category") != "flyer" else "drive")
        if f and ctx.now_ts - f <= RECENT_EVENT_DAYS * 86400:
            out.append((f, it))
    out.sort(key=lambda x: (-x[0], x[1]["id"]))
    seen, uniq = set(), []
    for wn, it in out:
        if it["id"] not in seen:
            seen.add(it["id"])
            uniq.append((wn, it))
    return uniq[:WHATSNEW_MAX]


def finish_group(ctx: Ctx, g: dict, i18n: I18n) -> None:
    album = g.pop("_album")
    src = T.detect_language(album, "en")
    names, machine = i18n.pair(album, src)
    if machine:
        en_title_case(names, src)
    n = g["extra"]["count"]
    g["i18n"] = {"title": {"en": f"{n} new photos in {names['en']}", "es": f"{n} fotos nuevas en {names['es']}"},
                 "summary": {"en": "", "es": ""}, "album": names}
    g["machine"] = [other(src)] if machine else []


def materialize_whatsnew(plan: list[tuple[float, dict]]) -> list[dict]:
    items = []
    for wn, it in plan:
        c = dict(it)
        c["wn_date"] = to_iso(datetime.fromtimestamp(wn, timezone.utc))
        items.append(c)
    return items


# =========================================================================== published-writers spotlight
def local_day(ctx: Ctx, v: Any) -> str | None:
    """ISO datetime/date → 'YYYY-MM-DD' in the site's time zone (a bare date is kept as is)."""
    s = str(v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    d = parse_iso(s) if s else None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(ctx.tz).date().isoformat()


def article_pub_date(ctx: Ctx, it: dict) -> str:
    """The day a magazine story counts as published, for the 60/90-day windows and the weekly digest:
    the EARLIER of the first day of its issue (La Viña's bimonthly "Septiembre / Octubre" issue →
    September 1) and the day we first saw it online (`first_seen`, site time zone) — never after today.
    It does not move: the October issue seen online on September 16 counts from September 16, also
    after October 1 (so the digest does not list it twice); a back-catalog story found by the archive
    backfill counts from its issue's first day (its first_seen is the later backfill day)."""
    ex = it.get("extra") or {}
    today = ctx.today_local.isoformat()
    key = str(ex.get("issue_key") or "")
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", key):
        start = f"{key}-01"
    else:
        start = local_day(ctx, ex.get("issue_date")) or local_day(ctx, it.get("date"))
    seen = local_day(ctx, it.get("first_seen"))
    days = [d for d in (start, seen) if d]
    return min(min(days), today) if days else today


def byline_lang(it: dict) -> str | None:
    """The language the byline's place is written in: La Viña prints Spanish bylines ("Monterrey, N.L."
    is Nuevo León there), the Grapevine English ones — whatever language the title was detected in."""
    pub = (it.get("extra") or {}).get("publication") or it.get("category")
    return "es" if pub == "lv" else "en" if pub == "gv" else it.get("lang")


def enrich_articles(ctx: Ctx, items: list[dict]) -> None:
    """extra.geo (where the writer is from — scripts/sync/geo.py) and extra.pub_date on every story."""
    for it in items:
        ex = it.setdefault("extra", {})
        if ex.get("department") is not True and _EVERY_ISSUE.search(str(ex.get("section") or "")):
            ex["department"] = True     # the page prints "In Every Issue" / "En cada edición"
        try:
            ex["geo"] = classify_location(ex.get("author_location"), byline_lang(it))
            ex["pub_date"] = article_pub_date(ctx, it)
        except Exception as e:      # never fatal: the story simply stays out of the spotlight
            log.warning("spotlight fields for %s skipped: %s: %s", it.get("id"), type(e).__name__, e)
            ex.setdefault("geo", classify_location(None))
            ex.setdefault("pub_date", None)


def spotlight_settings(ctx: Ctx) -> tuple[int, list[int], str]:
    """(home_days, list_days, default_scope) from config/site.yml `spotlight:`, sanity-checked."""
    sp = ctx.cfg.get("spotlight") if isinstance(ctx.cfg.get("spotlight"), dict) else {}

    def days(v: Any) -> int | None:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n if 1 <= n <= 366 else None

    home = days(sp.get("home_days")) or SPOTLIGHT_HOME_DAYS
    raw = sp.get("list_days") if isinstance(sp.get("list_days"), list) else list(SPOTLIGHT_LIST_DAYS)
    lst: list[int] = []
    for v in raw:
        n = days(v)
        if n and n not in lst:
            lst.append(n)
    lst = lst or list(SPOTLIGHT_LIST_DAYS)
    scope = str(sp.get("default_scope") or "neta65").strip().lower()
    return home, lst, scope if scope in SPOTLIGHT_SCOPES else "neta65"


def spotlight_candidate(it: dict) -> bool:
    """A story with a byline — not an "In Every Issue" department (Letter from the Editor, Dear
    Grapevine, Cartas del lector …), which has no single writer."""
    ex = it.get("extra") or {}
    if it.get("kind") != "article" or ex.get("department") is True or _EVERY_ISSUE.search(str(ex.get("section") or "")):
        return False
    return bool(clean_text(ex.get("author")) or clean_text(ex.get("author_location")))


def spotlight_scope(it: dict) -> str:
    s = ((it.get("extra") or {}).get("geo") or {}).get("scope")
    return s if s in SCOPES else "unknown"


def plan_spotlight(ctx: Ctx, articles: list[dict]) -> tuple[list[dict], dict]:
    """→ (the stories of the longest window, sorted: Area 65, rest of Texas, elsewhere, unknown; then
    newest pub_date first; then title) and the counts per window {"60": {"neta65", "texas", "all"}}
    ("texas" includes Area 65). Items are the article dicts themselves (copied when written)."""
    home, lst, _scope = spotlight_settings(ctx)
    windows = sorted(set(lst) | {home})
    today = ctx.today_local
    starts = {d: (today - timedelta(days=d)).isoformat() for d in windows}
    end = today.isoformat()
    cands = [it for it in articles if spotlight_candidate(it) and (it.get("extra") or {}).get("pub_date")]
    counts: dict[str, dict[str, int]] = {}
    for d in windows:
        inside = [it for it in cands if starts[d] <= it["extra"]["pub_date"] <= end]
        scopes = [spotlight_scope(it) for it in inside]
        counts[str(d)] = {"neta65": scopes.count("neta65"),
                          "texas": scopes.count("neta65") + scopes.count("texas"), "all": len(inside)}
    longest = max(windows)
    items = [it for it in cands if starts[longest] <= it["extra"]["pub_date"] <= end]
    items.sort(key=lambda it: (SCOPES.index(spotlight_scope(it)),
                               -date.fromisoformat(it["extra"]["pub_date"]).toordinal(),
                               fold(it.get("title")), it["id"]))
    return items, counts


def build_spotlight(ctx: Ctx, items: list[dict], counts: dict, now: str) -> dict:
    home, lst, scope = spotlight_settings(ctx)
    return {"updated": now, "fixture": False, "today": ctx.today_local.isoformat(),
            "home_days": home, "list_days": lst, "default_scope": scope, "counts": counts,
            "items": [clean_private(copy.deepcopy(i)) for i in items]}


# =========================================================================== districts
def json_safe(v: Any) -> Any:
    """Hand-written YAML values → JSON types (dates → 'YYYY-MM-DD', anything odd → text)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [json_safe(x) for x in v]
    return str(v)


def build_districts(ctx: Ctx, i18n: I18n) -> list[dict]:
    path = CONTENT_DIR / "districts.yml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception as e:
        log.error("content/districts.yml has a formatting problem (%s) — districts page left empty", e)
        ctx.raw_problems["districts"] = f"content/districts.yml: {e}"[:200]
        return []
    rows = (data or {}).get("districts") or [] if isinstance(data, dict) else []
    items = []
    for r in rows:
        if not isinstance(r, dict) or r.get("number") in (None, ""):
            continue
        num = r["number"]
        num = int(num) if str(num).strip().isdigit() else clean_text(num)
        it = {"id": f"district:{num}", "number": num, "name": clean_text(r.get("name")),
              "language": str(r.get("language") or "en").lower(), "website": clean_text(r.get("website")),
              "gvr_contact": clean_text(r.get("gvr_contact")), "meets": clean_text(r.get("meets"))}
        for k, v in r.items():
            if k not in it and v is not None:
                it[str(k)] = json_safe(v)      # e.g. "updated: 2026-09-01" is a date for YAML
        prior = "es" if it["language"] == "es" else "en"
        it["_langs"] = {"name": T.detect_language(it["name"], prior) if T.needs_translation(it["name"]) else None,
                        "meets": T.detect_language(it["meets"], prior) if T.needs_translation(it["meets"]) else None}
        for field in ("name", "meets"):
            i18n.want(it[field], it["_langs"][field], (0, 0.0))
        items.append(it)
    items.sort(key=lambda d: (0, d["number"], "") if isinstance(d["number"], int) else (1, 0, str(d["number"])))
    return items


def finish_district(it: dict, i18n: I18n) -> None:
    langs = it.pop("_langs", {})
    i18n_out, machine = {}, set()
    for field in ("name", "meets"):
        src = langs.get(field)
        i18n_out[field], m = i18n.pair(it[field], src)
        if m:
            machine.add(other(src))
    it["i18n"] = i18n_out
    it["machine"] = sorted(machine)


# =========================================================================== shop (official store data)
# data/raw/shop.json (scripts/sync/shop.py) → data/site/shop.json: the Book of the Month offers, the
# bulk-book discount tiers, the subscription prices per publication × region and short descriptions of
# the subscription types. Contract: docs/DATA_SCHEMA.md → "shop.json". Prices and dates always come
# from the synced data (never written into a template); purchases link to the official stores.
SHOP_PUBS = ("gv", "lv")
SHOP_REGIONS = ("us", "ca", "intl")
SHOP_TYPES = ("print", "digital", "complete")


def _num(v: Any) -> float | None:
    try:
        return None if v is None or isinstance(v, bool) else round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    try:
        return None if v is None or isinstance(v, bool) else int(v)
    except (TypeError, ValueError):
        return None


def empty_shop(updated: str | None = None) -> dict:
    return {"updated": updated, "fixture": False, "botm": [],
            "bulk_discounts": {"source_url": None, "tiers": []}, "subscriptions": [], "types": {}}


def build_shop(ctx: Ctx, i18n: I18n) -> tuple[dict, list[tuple[dict, str, str, str]]]:
    """shop.json without its translations (→ finish_shop); the texts to translate are registered
    with `i18n` (Book of the Month titles and blurbs, type descriptions, the bulk-discount note).
    An offer whose end date has passed is left out (the official page may still show it)."""
    env = ctx.raw.get("shop") or {}
    items = ctx.items("shop")
    today = ctx.today_local.isoformat()
    doc = empty_shop(env.get("updated"))
    wanted: list[tuple[dict, str, str, str]] = []

    for pub in SHOP_PUBS:
        it = next((i for i in items if i.get("id") == f"botm:{pub}" and i.get("kind") == "botm"), None)
        if not it:
            continue
        ex = it.get("extra") or {}
        if ex.get("ends") and str(ex["ends"]) < today:
            log.info("shop: the %s Book of the Month offer ended %s — left out", pub, ex["ends"])
            continue
        lang = it.get("lang") if it.get("lang") in LANGS else ("es" if pub == "lv" else "en")
        title, blurb = fix_title(it.get("title")), clean_text(it.get("summary"))
        if not title or not it.get("url"):
            continue
        row = {"id": it["id"], "pub": pub, "lang": lang, "title": title, "url": it["url"],
               "page_url": ex.get("page_url"), "image": it.get("image") or None,
               "price": _num(ex.get("price")), "sale_price": _num(ex.get("sale_price")),
               "discount_pct": ex.get("discount_pct"), "currency": ex.get("currency") or "USD",
               "sku": ex.get("sku"), "starts": ex.get("starts"), "ends": ex.get("ends"),
               "month_label": ex.get("month_label"), "blurb": blurb, "i18n": {}, "machine": set()}
        for f in ("title", "blurb"):
            if row[f]:
                wanted.append((row, f, row[f], lang))
        month = _int_or_none(ex.get("month"))
        if month and 1 <= month <= 12:          # written by rule, never machine-translated
            row["i18n"]["month_label"] = {"en": MONTHS_EN[month - 1], "es": MONTHS_ES[month - 1]}
        elif row["month_label"]:
            wanted.append((row, "month_label", row["month_label"], lang))
        doc["botm"].append(row)

    by_key: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        ex = it.get("extra") or {}
        if it.get("kind") == "subscription" and ex.get("pub") in SHOP_PUBS and ex.get("region") in SHOP_REGIONS:
            by_key.setdefault((ex["pub"], ex["region"]), []).append(it)
    listings = [x for x in (env.get("listings") or []) if isinstance(x, dict)]
    for pub in SHOP_PUBS:
        for region in SHOP_REGIONS:
            plans = sorted(by_key.get((pub, region), []),
                           key=lambda i: (_int_or_none((i.get("extra") or {}).get("position")) or 0, i["id"]))
            if not plans:
                continue
            url = next((x.get("url") for x in listings if x.get("pub") == pub and x.get("region") == region), None)
            rows = []
            for i in plans:
                ex = i.get("extra") or {}
                rows.append({
                    "type": ex.get("type") if ex.get("type") in (*SHOP_TYPES, "other") else "other",
                    "term_months": _int_or_none(ex.get("term_months")), "title": clean_text(i.get("title")),
                    "price": _num(ex.get("price")), "currency": ex.get("currency") or "USD", "sku": ex.get("sku"),
                    "url": i.get("url"), "image": i.get("image") or None,
                    "volume": [{"min": _int_or_none(v.get("min")), "max": _int_or_none(v.get("max")),
                                "price": _num(v.get("price"))}
                               for v in (ex.get("volume") or []) if isinstance(v, dict)],
                })
            doc["subscriptions"].append({"pub": pub, "region": region,
                                         "url": url or (plans[0].get("extra") or {}).get("listing_url"),
                                         "plans": rows})

    bulk = env.get("bulk_discounts") if isinstance(env.get("bulk_discounts"), dict) else {}
    tiers = [{"min": _int_or_none(t.get("min")), "max": _int_or_none(t.get("max")), "off": _num(t.get("off")) or 0.0}
             for t in (bulk.get("tiers") or []) if isinstance(t, dict) and _int_or_none(t.get("min")) is not None]
    doc["bulk_discounts"] = {"source_url": bulk.get("source_url"), "tiers": tiers}
    note = {k: clean_text(v) for k, v in (bulk.get("note") or {}).items() if k in LANGS and clean_text(v)}
    if note:
        cell = dict(note)
        doc["bulk_discounts"]["note"] = cell
        if len(note) == 1:                    # only one store printed it: translate it
            src, text = next(iter(note.items()))
            wanted.append((cell, "", text, src))

    raw_types = env.get("types") if isinstance(env.get("types"), dict) else {}
    for pub in SHOP_PUBS:
        for typ in SHOP_TYPES:
            t = (raw_types.get(pub) or {}).get(typ)
            text = clean_text(t.get("text")) if isinstance(t, dict) else ""
            if text:
                src = t.get("lang") if t.get("lang") in LANGS else ("es" if pub == "lv" else "en")
                cell = doc["types"].setdefault(pub, {}).setdefault(typ, {src: text})
                wanted.append((cell, "", text, src))

    for _t, _f, text, src in wanted:
        i18n.want(text, src, (0, 0.0))
    return doc, wanted


def finish_shop(doc: dict, wanted: list[tuple[dict, str, str, str]], i18n: I18n) -> None:
    for target, field, text, src in wanted:
        pair, machine = i18n.pair(text, src)
        if not field:                         # a plain {en, es} cell (type description, bulk note)
            target.update(pair)
            continue
        if machine and field in TITLE_FIELDS:
            en_title_case(pair, src)
        target["i18n"][field] = pair
        if machine:
            target["machine"].add(other(src))
    for row in doc["botm"]:
        row["i18n"] = {k: row["i18n"][k] for k in ("title", "blurb", "month_label") if k in row["i18n"]}
        row["machine"] = sorted(row.get("machine") or [])


def shop_count(doc: dict) -> int:
    return len(doc.get("botm") or []) + sum(len(s.get("plans") or []) for s in doc.get("subscriptions") or [])


# Weekly Open meetings: the Grapevine one (Wednesdays) first — templates read db.weekly_open.items[0] —
# then La Viña's (Thursdays, config/site.yml `lavina_weekly_open`).
WEEKLY_OPEN_ORDER = ("weekly_open", "weekly_open_lv")


def weekly_open_items(ctx: Ctx) -> list[dict]:
    items = simple(ctx, "weekly_open")
    n = len(WEEKLY_OPEN_ORDER)
    return sorted(items, key=lambda i: WEEKLY_OPEN_ORDER.index(i["id"]) if i["id"] in WEEKLY_OPEN_ORDER else n)


# =========================================================================== status
CRAWL_KEYS = ("known_pages", "crawled_pages", "never_crawled", "never_crawled_events", "queue_remaining",
              "est_days_to_full", "last_run_pages", "pdfs", "pdfs_gone", "pdfs_with_details", "pdfs_with_thumbs",
              "page_errors", "new")


def crawl_summary(ctx: Ctx) -> dict:
    env = ctx.raw.get("pdfs") or {}
    stats = env.get("stats") or {}
    crawl = dict(env.get("crawl") or {})
    for k in CRAWL_KEYS:
        if k in stats and stats[k] is not None:
            crawl[k] = stats[k]                   # the newest stats win over the summary block
    if not crawl.get("known_pages"):
        st = read_json(STATE_DIR / "crawl-state.json", {}) or {}
        pages = st.get("pages") if isinstance(st.get("pages"), dict) else {}
        crawl.setdefault("known_pages", len(pages))
        crawl.setdefault("crawled_pages", sum(1 for p in pages.values() if isinstance(p, dict) and p.get("crawled_at")))
    crawl["pdfs"] = len([i for i in ctx.items("pdfs") if i.get("kind") == "pdf"])
    crawl["pdfs_gone"] = crawl.get("pdfs_gone", sum(1 for i in env.get("items", []) if i.get("status") == "gone"))
    crawl["updated"] = env.get("updated")
    crawl["attempted"] = env.get("attempted")
    return {k: crawl[k] for k in sorted(crawl)}


def build_status(ctx: Ctx, translator: T.Translator | None, i18n: I18n, counts: dict[str, int],
                 translation_enabled: bool, tr_seconds: float) -> dict:
    week_ago = ctx.now_ts - 7 * 86400
    sources = []
    for name, label, label_es in SOURCES:
        env = ctx.raw.get(name) or {}
        items = ctx.items(name)
        problem = ctx.raw_problems.get(name)
        never_ran = problem == "missing"
        err = env.get("error") or (("not run yet" if never_ran else problem) if problem else None)
        sources.append({
            "source": name, "label": label, "label_es": label_es,
            # ok: true = last run fine · false = last run failed (older data kept) · null = never ran
            "ok": None if never_ran else (bool(env.get("ok")) and not problem),
            "updated": env.get("updated"), "attempted": env.get("attempted") or env.get("updated"),
            "count": len(items),
            "new_7d": sum(1 for i in items if (ts(i.get("first_seen")) or 0) >= week_ago),
            "error": err, "stats": env.get("stats") or {},
        })
    tr = translator.summary() if translator else {}
    n_tr = tr.get("translated", 0)
    return {
        "generated": now_iso(), "updated": now_iso(), "fixture": False,
        "sources": sources,
        "crawl": crawl_summary(ctx),
        "translations": {"cached": tr.get("cached", 0), "engine": T.ENGINE_VERSION,
                         "model_enabled": translation_enabled, "translated_this_run": n_tr,
                         "from_cache": tr.get("cache_hits", 0), "pending": i18n.pending,
                         "rejected_by_guard": tr.get("rejected", 0),
                         "seconds": round(tr_seconds, 1), "model_seconds": tr.get("model_seconds", 0),
                         "texts_per_second": round(n_tr / tr_seconds, 1) if n_tr and tr_seconds else None,
                         "glossary_entries": (len(translator.glossary.keep) + len(translator.glossary.terms))
                         if translator else 0},
        "counts": counts,
        "problems": dict(sorted(ctx.raw_problems.items())),
        # Optional outside calendars (config sources.ics_feeds): kept apart from `sources` on purpose —
        # a feed that a site's bot protection blocks is an extra that did not work, not a content source
        # that "stopped updating" (no weekly GitHub issue about it). Shown on /status/ in plain words.
        "feeds": [feed_status(h) for h in ctx.feeds],
        "items": [],
    }


def feed_status(h: dict) -> dict:
    """One feed's health for status.json: state ok | blocked | error | never, the HTTP status of the last
    request, when it last worked, how many events it gave (and how many of them were already on the
    calendar — content/events, a flyer, the committee's own events — shown once), and `notes`: what the
    feed says that a content/events file does not (another date or time, a venue now known), for the
    chair to check (merge_feed_duplicates → the Actions run summary)."""
    keys = ("key", "url", "label", "label_es", "category", "group", "state", "http_status", "error", "last_success",
            "last_attempt", "checked_this_run", "from_copy", "events_count", "duplicates")
    out = {k: h.get(k) for k in keys}
    out["notes"] = [str(n) for n in (h.get("notes") or [])]
    return out


# =========================================================================== main
SITE_FILES = ("videos", "episodes", "instagram", "articles", "pdfs", "drive", "editorial", "weekly_open",
              "announcements", "events")
SINGLE_SOURCE = {"videos": "youtube", "episodes": "podcasts", "instagram": "instagram", "articles": "articles",
                 "pdfs": "pdfs", "drive": "drive", "editorial": "editorial", "weekly_open": "weekly_open"}


def clean_private(it: dict) -> dict:
    for k in [k for k in it if k.startswith("_") or k in DROP_KEYS]:
        it.pop(k, None)
    return it


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.sync.build_data", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(SITE_DIR), help="output folder (default data/site)")
    ap.add_argument("--no-translate", action="store_true",
                    help="do not run the translation model (cached translations are still used)")
    ap.add_argument("--translate-minutes", type=float,
                    default=float(os.environ.get("GV_TRANSLATE_MINUTES") or 40),
                    help="time budget for NEW translations; the rest is done on the next run (default 40)")
    ap.add_argument("--offline", action="store_true", help="no network (skip .ics feeds; use last copy)")
    ap.add_argument("--no-prune", action="store_true", help="keep unused translation-cache entries")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = Ctx(offline=a.offline)
    ctx.load_raw()
    translator = T.Translator(budget_seconds=max(a.translate_minutes, 0.1) * 60, use_model=not a.no_translate)
    T._DEFAULT = translator          # committee_meetings() etc. share the same cache
    i18n = I18n(translator)
    completed = False
    try:
        cols: dict[str, list[dict]] = {
            "videos": simple(ctx, "youtube", ("video",)),
            "episodes": simple(ctx, "podcasts", ("episode",)),
            "instagram": simple(ctx, "instagram", ("post",)),
            "articles": simple(ctx, "articles", ("article",)),
            "pdfs": simple(ctx, "pdfs", ("pdf",)),
            "drive": simple(ctx, "drive", exclude=("announcement",), skip=closed_form),
            "editorial": simple(ctx, "editorial"),
            "weekly_open": weekly_open_items(ctx),
            "announcements": build_announcements(ctx),
            "events": build_events(ctx),
        }
        enrich_articles(ctx, cols["articles"])
        meta, meta_wanted = build_meta(ctx, i18n)
        try:
            shop, shop_wanted = build_shop(ctx, i18n)
        except Exception as e:  # the store data never breaks the build
            log.error("shop.json could not be built (%s: %s) — writing an empty one", type(e).__name__, e)
            shop, shop_wanted = empty_shop(), []
        wn_plan = plan_whatsnew(ctx, cols)
        spot_items, spot_counts = plan_spotlight(ctx, cols["articles"])
        districts = build_districts(ctx, i18n)
        # What's New and the spotlight (home page) are translated first
        plan_translations(ctx, cols, {id(it) for _, it in wn_plan} | {id(it) for it in spot_items}, i18n)
        for _, it in wn_plan:
            if it.get("_album"):
                i18n.want(it["_album"], T.detect_language(it["_album"], "en"), (0, 0.0))
        log.info("translation: %d texts needed (model %s, budget %.0f min)", len(i18n.jobs),
                 "off" if a.no_translate else "on", a.translate_minutes)
        i18n.run()

        for name, items in cols.items():
            kept = []
            for it in items:
                try:
                    i18n.apply(it)
                    it["is_new"] = ctx.is_new(it, raw_source(it))
                    kept.append(it)
                except Exception as e:
                    log.warning("skipped %s: %s: %s", it.get("id"), type(e).__name__, e)
            cols[name] = kept
        finish_meta(meta, meta_wanted, i18n)
        finish_shop(shop, shop_wanted, i18n)
        for _, it in wn_plan:
            if "_album" in it:
                finish_group(ctx, it, i18n)
                it["is_new"] = True
        for d in districts:
            finish_district(d, i18n)
        whatsnew = materialize_whatsnew([(wn, it) for wn, it in wn_plan])
        for it in whatsnew:
            it.setdefault("is_new", ctx.is_new(it, raw_source(it)))

        counts = {name: len(items) for name, items in cols.items()}
        counts.update({"whatsnew": len(whatsnew), "districts": len(districts), "shop": shop_count(shop)})
        now = now_iso()
        for name in SITE_FILES:
            src = SINGLE_SOURCE.get(name)
            updated = (ctx.raw.get(src) or {}).get("updated") if src else None
            doc = {"updated": updated or now, "fixture": False}
            doc.update(meta.get(name) or {})
            doc["items"] = [clean_private(i) for i in cols[name]]
            write_json(out_dir / f"{name}.json", doc)
        write_json(out_dir / "whatsnew.json", {"updated": now, "fixture": False,
                                              "items": [clean_private(i) for i in whatsnew]})
        write_json(out_dir / "districts.json", {"updated": now, "fixture": False,
                                               "items": [clean_private(d) for d in districts]})
        spot_items, spot_counts = plan_spotlight(ctx, cols["articles"])     # again: translated + kept items
        spotlight = build_spotlight(ctx, spot_items, spot_counts, now)
        write_json(out_dir / "spotlight.json", spotlight)
        write_json(out_dir / "shop.json", {**shop, "updated": shop.get("updated") or now})
        status = build_status(ctx, translator, i18n, counts, not a.no_translate, i18n.seconds)
        status["spotlight"] = {"today": spotlight["today"], "home_days": spotlight["home_days"],
                               "list_days": spotlight["list_days"], "counts": spotlight["counts"],
                               "items": len(spotlight["items"])}
        write_json(out_dir / "status.json", status)
        completed = True
    finally:
        # Always keep the translations done this run (they took minutes of model time), even when a
        # later step failed. Prune unused entries only after a complete, healthy run.
        prune = (completed and not a.no_prune and not a.no_translate and i18n.pending == 0
                 and not any(p != "missing" for p in ctx.raw_problems.values()))
        try:
            translator.save(prune_unused=prune)
        except Exception as e:
            if completed:
                raise
            log.error("could not save the translation cache: %s: %s", type(e).__name__, e)
    log.info("wrote %s → %s", counts, out_dir)
    log.info("translations: %s", {k: v for k, v in status["translations"].items()})
    log.info("build_data finished in %.1fs", time.monotonic() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
