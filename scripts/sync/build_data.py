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
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import yaml

from . import translate as T
from .common import (CONTENT_DIR, RAW_DIR, SITE_DIR, STATE_DIR, clean_text, get_logger, load_config, now_iso,
                     parse_iso, read_json, short_hash, slugify, strip_html, to_iso, truncate, write_json)
from .geo import SCOPES, classify_location, fold
from .meeting import upcoming_meetings

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
            firsts = [t for t in (ts(i.get("first_seen")) for i in env["items"]) if t]
            if firsts:
                self.births[name] = min(firsts)
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
            if it.get("category") == "committee" or (it.get("extra") or {}).get("past"):
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


def ics_events(ctx: Ctx) -> list[dict]:
    """Optional public .ics feeds from config sources.ics_feeds (last good copy kept in data/state)."""
    feeds = (ctx.cfg.get("sources", {}) or {}).get("ics_feeds") or []
    if not feeds:
        return []
    state_path = STATE_DIR / "ics_feeds.json"
    state = read_json(state_path, {}) or {}
    out: list[dict] = []
    for feed in feeds:
        spec = feed if isinstance(feed, dict) else {"url": str(feed)}
        url = spec.get("url")
        if not url:
            continue
        text = None
        if not ctx.offline:
            try:
                import requests
                r = requests.get(url, timeout=25, headers={"User-Agent": "NETA65-GrapevineCommitteeBot/2.0"})
                if r.status_code == 200 and "BEGIN:VCALENDAR" in r.text[:2000]:
                    text = r.text
                else:
                    log.warning("ics feed %s → HTTP %s", url, r.status_code)
            except Exception as e:
                log.warning("ics feed %s failed: %s", url, e)
        if text:
            state[url] = {"fetched": now_iso(), "ics": text}
        else:
            text = (state.get(url) or {}).get("ics")
        if text:
            try:
                out += _parse_ics(ctx, {"_ics": text, "_spec": spec})
            except Exception as e:  # a broken feed never blocks the build
                log.warning("ics feed %s could not be parsed: %s", url, e)
    if not ctx.offline:
        try:
            write_json(state_path, state)
        except Exception as e:
            log.warning("could not save ics state: %s", e)
    return out


def _parse_ics(ctx: Ctx, x: dict) -> list[dict]:
    from icalendar import Calendar
    from dateutil.rrule import rrulestr

    spec = x["_spec"]
    cal = Calendar.from_ical(x["_ics"])
    horizon_lo = ctx.now - timedelta(days=60)
    horizon_hi = ctx.now + timedelta(days=366)
    out = []
    for comp in cal.walk("VEVENT"):
        try:
            title = clean_text(str(comp.get("SUMMARY") or ""))
            if not title or str(comp.get("STATUS") or "").upper() == "CANCELLED":
                continue
            start = comp.decoded("DTSTART")
            end = comp.decoded("DTEND") if comp.get("DTEND") else None
            all_day = not isinstance(start, datetime)
            desc = strip_html(str(comp.get("DESCRIPTION") or ""))
            loc = clean_text(str(comp.get("LOCATION") or ""))
            link = clean_text(str(comp.get("URL") or "")) or spec.get("link") or "/events/"
            online = next(iter(re.findall(r"https?://[^\s<>\"]*(?:zoom\.us|meet\.google|teams\.microsoft)[^\s<>\"]*",
                                          f"{loc} {desc}")), None)
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
                    e = e - timedelta(days=1)        # DTEND of all-day events is exclusive
                e_iso = (e.isoformat() if all_day else to_iso(e if e.tzinfo else e.replace(tzinfo=ctx.tz))) if e else None
                city, state = city_state(loc)
                out.append({
                    "id": f"ev:ics:{short_hash(uid + '|' + s_iso)}", "source": "calendar", "kind": "event",
                    "url": link, "title": title, "summary": truncate(desc, 400),
                    "lang": T.detect_language(f"{title}. {desc}", "en"), "date": s_iso,
                    "first_seen": None, "image": None, "tags": [],
                    "category": spec.get("category") or "ics", "status": "ok",
                    "extra": {"start": s_iso, "end": e_iso, "all_day": all_day, "location": loc or None,
                              "online_url": online, "flyer_url": None, "flyer_thumb": None,
                              "city": city, "state": state, "feed": spec.get("name") or spec.get("url")},
                })
        except Exception as e:
            log.warning("ics event skipped: %s", e)
    return out


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
    try:
        committee = committee_meetings(ctx)
    except Exception as e:  # a bad `meeting:` edit in config/site.yml must not stop the daily update
        log.error("committee meetings skipped — config/site.yml `meeting:` problem: %s: %s", type(e).__name__, e)
        ctx.raw_problems["meeting"] = f"config/site.yml meeting: {type(e).__name__}: {e}"[:200]
        committee = []
    groups = [("committee", committee), ("flyer", flyer_events(ctx)),
              ("external", safe_each([i for i in ctx.items("events_external") if i.get("kind") == "event"], prep, "event")),
              ("manual", safe_each([i for i in ctx.items("manual_events") if i.get("kind") == "event"], prep, "event")),
              ("ics", ics_events(ctx))]
    for _label, items in groups:
        for ev in items:
            ev.setdefault("extra", {})
            if not ev["extra"].get("start"):
                ev["extra"]["start"] = ev.get("date")
            if not ev["extra"].get("start"):
                continue
            evs.setdefault(ev["id"], ev)
    cutoff = ctx.now_ts - 86400
    upcoming = [e for e in evs.values() if event_end_ts(ctx, e) >= cutoff]
    past = [e for e in evs.values() if event_end_ts(ctx, e) < cutoff and e.get("category") != "committee"]
    upcoming.sort(key=lambda e: (event_start_ts(e), e["id"]))
    past.sort(key=lambda e: (-event_start_ts(e), e["id"]))
    for e in upcoming:
        e["extra"]["past"] = False
    for e in past[:PAST_EVENTS_KEEP]:
        e["extra"]["past"] = True
    return upcoming + past[:PAST_EVENTS_KEEP]


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

    def pair(self, text: str, src: str | None, md: bool = False) -> tuple[dict, bool]:
        """→ ({'en': …, 'es': …}, machine_translated?)"""
        if src not in LANGS or not text:
            return {"en": text, "es": text}, False
        out, machine = self.get(text, src, md)
        tgt = other(src)
        val = {src: text, tgt: out if out is not None else text}
        return {"en": val["en"], "es": val["es"]}, bool(out is not None and machine and out != text)

    def apply(self, it: dict) -> None:
        if it.pop("_fixed_i18n", False):
            return
        src = source_lang(it)
        i18n, machine = {}, set()
        for field, text, md, fsrc in text_fields(it):
            s = fsrc or src
            i18n[field], m = self.pair(text, s, md)
            if m:
                machine.add(other(s))  # type: ignore[arg-type]
                if field in TITLE_FIELDS and it.get("kind") != "post":    # (Instagram "titles" are captions)
                    en_title_case(i18n[field], s)
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
            for field, text, md, fsrc in text_fields(it):
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
        if it.get("category") == "committee" or it["extra"].get("past"):
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
        "items": [],
    }


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
            "weekly_open": simple(ctx, "weekly_open"),
            "announcements": build_announcements(ctx),
            "events": build_events(ctx),
        }
        enrich_articles(ctx, cols["articles"])
        meta, meta_wanted = build_meta(ctx, i18n)
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
        counts.update({"whatsnew": len(whatsnew), "districts": len(districts)})
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
