"""Grapevine Weekly Open AA Meeting (Zoom) → data/raw/weekly_open.json

Source page: https://www.aagrapevine.org/grapevine-weekly-open  (config: sources.grapevine.weekly_open)

The page currently says, in one sentence:
    "To join the meeting live on Wednesdays at Noon Eastern, use Zoom code 871 2036 8287
     with password 238047"
We extract the Zoom meeting ID, passcode, weekday and time *as written on the page*, and also
compute the Central-time equivalent (NETA 65 is in Central time) plus the next occurrence.

Output: Items of kind "meeting" — see docs/DATA_SCHEMA.md:
  1. id "weekly_open": the Grapevine Weekly Open (from the page above; always FIRST — templates read
     db.weekly_open.items[0]):
       extra = zoom_id, passcode, day, time, url  (+ weekday, time_central, start_local, timezone,
               next_start, zoom_url, player_url, sentence)
  2. id "weekly_open_lv": La Viña's weekly open meeting in Spanish, from config/site.yml
     `lavina_weekly_open` (an official La Viña flyer; there is no web page to read). Same extra fields
     (no player_url / sentence), plus `starts` (first meeting); next_start is never before `starts`.
     Written on every run, also when the Grapevine page cannot be read.

If the page can't be fetched or parsed, the previous Grapevine item is kept and the envelope gets ok=false.

Run:  python -m scripts.sync.weekly_open [--dry-run] [--html FILE]
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .common import (clean_text, get_logger, load_config, load_raw, make_item, merge_items, run_module,
                     save_raw, shared_session, to_iso, truncate)

SOURCE = "weekly_open"
ITEM_ID = "weekly_open"
LV_ITEM_ID = "weekly_open_lv"      # La Viña's weekly open meeting (config/site.yml lavina_weekly_open)
log = get_logger(SOURCE)

DEFAULT_URL = "https://www.aagrapevine.org/grapevine-weekly-open"

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
# First three letters → weekday number (English and Spanish, in case the page is ever in Spanish).
WEEKDAY_PREFIX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
                  "lun": 0, "mar": 1, "mié": 2, "mie": 2, "jue": 3, "vie": 4, "sáb": 5, "sab": 5, "dom": 6}

ZONES = {
    "eastern": "America/New_York", "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "central": "America/Chicago", "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago",
    "mountain": "America/Denver", "mt": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver",
    "pacific": "America/Los_Angeles", "pt": "America/Los_Angeles", "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "este": "America/New_York", "centro": "America/Chicago", "pacífico": "America/Los_Angeles",
}
_ZONE_WORDS = "|".join(sorted(ZONES, key=len, reverse=True))

DAY_RE = re.compile(r"(?i)\b(mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?|"
                    r"lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bados?|domingos?)\b")
TIME_RE = re.compile(
    rf"(?i)\b(noon|midnight|mediod[ií]a|\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)|\d{{1,2}}:\d{{2}})"
    rf"(?:\s*\(?\s*(?:(?:hora\s+del?\s+)?({_ZONE_WORDS})\b(?:\s+time)?)\)?)?")
# "Zoom code 871 2036 8287", "Meeting ID: 871 2036 8287", "zoom.us/j/87120368287"
ZOOM_ID_RE = re.compile(r"(?i)(?:zoom|meeting\s*id|id\s+de\s+(?:la\s+)?reuni[óo]n)\D{0,30}?"
                        r"(\d{3}[\s.-]?\d{3,4}[\s.-]?\d{3,5})(?!\d)")
ZOOM_URL_RE = re.compile(r"(?i)https?://[\w.-]*zoom\.us/j/(\d{9,11})(?:\?pwd=[\w.-]+)?")
PASS_RE = re.compile(r"(?i)\b(?:password|passcode|pass\s*code|pwd|contraseña|c[óo]digo\s+de\s+acceso)\b"
                     r"\s*(?:is|es)?\s*[:#-]?\s*([A-Za-z0-9]{3,20})\b")


# --------------------------------------------------------------------------- parsing helpers
def _format_zoom_id(raw: str) -> str:
    """'87120368287' → '871 2036 8287' (Zoom's own grouping); keeps other lengths readable."""
    d = re.sub(r"\D", "", raw)
    if len(d) == 11:
        return f"{d[:3]} {d[3:7]} {d[7:]}"
    if len(d) == 10:
        return f"{d[:3]} {d[3:6]} {d[6:]}"
    if len(d) == 9:
        return f"{d[:3]} {d[3:6]} {d[6:]}"
    return d


def _parse_clock(txt: str) -> tuple[int, int] | None:
    """'Noon' → (12,0); '11 AM' → (11,0); '7:30 p.m.' → (19,30); '19:00' → (19,0)."""
    t = txt.lower().replace(".", "").replace(" ", "")
    if t in ("noon", "mediodía", "mediodia"):
        return 12, 0
    if t == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", t)
    if not m:
        return None
    h, mi, ap = int(m[1]), int(m[2] or 0), m[3]
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    return h, mi


def _fmt_clock(h: int, m: int) -> str:
    """(11, 0) → '11 AM'; (19, 30) → '7:30 PM'."""
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def _next_occurrence(weekday: int, hh: int, mm: int, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    days = (weekday - now.weekday()) % 7
    cand = (now + timedelta(days=days)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def _block_lines(el) -> list[str]:
    """Text of `el` split on block elements and <br>, with inline elements kept together."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    for b in el.find_all(["p", "li", "div", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"]):
        b.insert_before("\n")
        b.insert_after("\n")
    return [ln for ln in (clean_text(x) for x in el.get_text("").split("\n")) if ln]


def parse_page(html: str, page_url: str) -> dict:
    """Return a dict of whatever could be found (keys may be missing)."""
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        out["title"] = clean_text(og_title["content"])
    elif soup.title:
        out["title"] = clean_text(soup.title.get_text()).split(" | ")[0]

    main = soup.find("main") or soup.body or soup
    for t in main.find_all(["script", "style", "noscript", "svg", "nav", "form"]):
        t.decompose()

    # Captivate (podcast) player embedded on the page — handy for the site's "listen" button.
    for fr in main.find_all("iframe", src=True):
        if "captivate" in fr["src"] or "podcast" in fr["src"]:
            out["player_url"] = fr["src"]
            break

    lines = _block_lines(main)
    text = " ".join(lines)

    # The sentence that carries the join details (fallback: whole page text).
    sentence = next((ln for ln in lines if re.search(r"(?i)zoom", ln) and re.search(r"\d{3}", ln)), "")
    hay = sentence or text
    if sentence:
        out["sentence"] = truncate(sentence, 300)

    m = ZOOM_URL_RE.search(html)
    if m:
        out["zoom_id"] = _format_zoom_id(m[1])
        out["zoom_url"] = f"https://zoom.us/j/{m[1]}"
    m = ZOOM_ID_RE.search(hay) or ZOOM_ID_RE.search(text)
    if m:
        digits = re.sub(r"\D", "", m[1])
        if 9 <= len(digits) <= 11:
            out["zoom_id"] = _format_zoom_id(digits)
            out.setdefault("zoom_url", f"https://zoom.us/j/{digits}")
    m = PASS_RE.search(hay) or PASS_RE.search(text)
    if m:
        out["passcode"] = m[1]

    day_m = DAY_RE.search(hay)
    if day_m:
        wd = WEEKDAY_PREFIX.get(day_m[1].lower()[:3])
        if wd is not None:
            out["weekday_num"] = wd
            out["weekday"] = list(WEEKDAYS)[wd]
            out["day"] = day_m[1][:1].upper() + day_m[1][1:]        # as written: "Wednesdays"
    # Prefer a time that follows the weekday ("Wednesdays at Noon Eastern").
    time_m = (TIME_RE.search(hay, day_m.end()) if day_m else None) or TIME_RE.search(hay)
    if time_m:
        clock = _parse_clock(time_m[1])
        zone_word = (time_m[2] or "").lower()
        out["time"] = clean_text(time_m[0]).strip("() ")          # as written: "Noon Eastern"
        if clock:
            out["clock"] = clock
            # Grapevine is based in New York; if the page omits the zone, assume Eastern.
            out["timezone"] = ZONES.get(zone_word, "America/New_York")

    # Short description: first substantial paragraph that is not the join sentence.
    for ln in lines:
        if len(ln) >= 60 and ln != sentence and not re.search(r"(?i)zoom code|password|@", ln):
            sents = re.split(r"(?<=[.!?])\s+", ln)
            out["summary"] = truncate(" ".join(sents[:2]), 300)
            break
    out["url"] = page_url
    return out


def build_item(p: dict, page_url: str) -> dict:
    extra: dict = {
        "zoom_id": p.get("zoom_id"),
        "passcode": p.get("passcode"),
        "day": p.get("day"),
        "time": p.get("time"),
        "url": page_url,
        "weekday": p.get("weekday"),
        "zoom_url": p.get("zoom_url"),
        "player_url": p.get("player_url"),
        "sentence": p.get("sentence"),
    }
    clock, wd, tzname = p.get("clock"), p.get("weekday_num"), p.get("timezone")
    if clock and tzname:
        hh, mm = clock
        extra["start_local"] = f"{hh:02d}:{mm:02d}"
        extra["timezone"] = tzname
        if wd is not None:
            nxt = _next_occurrence(wd, hh, mm, ZoneInfo(tzname))
            central = nxt.astimezone(ZoneInfo("America/Chicago"))
            extra["time_central"] = f"{_fmt_clock(central.hour, central.minute)} Central"
            extra["next_start"] = to_iso(nxt)          # UTC, computed at sync time (templates may recompute)
    return make_item(
        id=ITEM_ID, source="grapevine", kind="meeting", url=page_url,
        title=p.get("title") or "Grapevine Weekly Open AA Meeting",
        summary=p.get("summary") or "", lang="en", date=None, category=None,
        extra={k: v for k, v in extra.items() if v not in (None, "")},
    )


def lavina_item(cfg: dict | None, now: datetime | None = None) -> dict | None:
    """La Viña's weekly open meeting from config/site.yml `lavina_weekly_open` → an Item shaped like the
    Grapevine one (lang "es"), or None when the block is missing, disabled or incomplete."""
    c = cfg if isinstance(cfg, dict) else {}
    if not c or c.get("enabled") is False:
        return None
    wd = WEEKDAY_PREFIX.get(str(c.get("day") or "").strip().lower()[:3])
    clock = _parse_clock(str(c.get("time") or ""))
    tzname = str(c.get("timezone") or "America/New_York")
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        log.warning("lavina_weekly_open: unknown timezone %r — item left out", tzname)
        return None
    if wd is None or not clock:
        log.warning("lavina_weekly_open: day %r / time %r not understood — item left out", c.get("day"), c.get("time"))
        return None
    hh, mm = clock
    starts = str(c.get("starts") or "").strip()
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    ref = now
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", starts):
        first = datetime.fromisoformat(starts).replace(tzinfo=tz)       # midnight local on the first day
        if first > now:
            ref = first - timedelta(seconds=1)                          # → the first meeting itself
    else:
        starts = ""
    nxt = _next_occurrence(wd, hh, mm, tz, ref)
    if starts and datetime.fromisoformat(starts).weekday() != wd:
        # A start date on another weekday: the first meeting shown is the first real occurrence
        log.warning("lavina_weekly_open: starts %s is not a %s — using %s", starts, list(WEEKDAYS)[wd],
                    nxt.date().isoformat())
        starts = nxt.date().isoformat()
    central = nxt.astimezone(ZoneInfo("America/Chicago"))
    digits = re.sub(r"\D", "", str(c.get("zoom_id") or ""))
    weekday = list(WEEKDAYS)[wd]
    day_es = {"monday": "Lunes", "tuesday": "Martes", "wednesday": "Miércoles", "thursday": "Jueves",
              "friday": "Viernes", "saturday": "Sábados", "sunday": "Domingos"}[weekday]
    zone_es = {"America/New_York": "hora del Este", "America/Chicago": "hora del Centro",
               "America/Denver": "hora de la Montaña", "America/Los_Angeles": "hora del Pacífico"}.get(tzname, tzname)
    time_es = ("12 p. m." if (hh, mm) == (12, 0) else
               f"{hh % 12 or 12}{f':{mm:02d}' if mm else ''} {'a. m.' if hh < 12 else 'p. m.'}")
    title_es = clean_text(c.get("title_es")) or "Reunión Abierta de La Viña"
    title_en = clean_text(c.get("title_en"))
    summary_es, summary_en = clean_text(c.get("summary_es")), clean_text(c.get("summary_en"))
    own = {"title": {"es": title_es, **({"en": title_en} if title_en else {})},
           "summary": {**({"es": summary_es} if summary_es else {}), **({"en": summary_en} if summary_en else {})}}
    extra = {
        "zoom_id": _format_zoom_id(digits) if 9 <= len(digits) <= 11 else None,
        "passcode": clean_text(c.get("passcode")) or None,
        "day": day_es,                                   # as on the flyer ("Día: jueves")
        "time": f"{time_es} ({zone_es})",                # as on the flyer ("12 p. m. (hora del Este)")
        "weekday": weekday,
        "start_local": f"{hh:02d}:{mm:02d}",
        "timezone": tzname,
        "time_central": f"{_fmt_clock(central.hour, central.minute)} Central",
        "next_start": to_iso(nxt),
        "zoom_url": f"https://zoom.us/j/{digits}" if 9 <= len(digits) <= 11 else None,
        "starts": starts or None,
        "url": clean_text(c.get("url")) or None,
        "source_note": clean_text(c.get("source")) or None,
        "own_i18n": {k: v for k, v in own.items() if v},
    }
    return make_item(
        id=LV_ITEM_ID, source="lavina", kind="meeting", url=clean_text(c.get("url")),
        title=title_es, summary=summary_es, lang="es", date=None, category=None,
        extra={k: v for k, v in extra.items() if v not in (None, "", {})},
    )


def with_lavina(items: list[dict], prev_items: list[dict], cfg: dict | None) -> list[dict]:
    """`items` (the Grapevine item(s)) + La Viña's item from config, merged with its previous copy."""
    lv = lavina_item(cfg)
    rest = [i for i in items if i.get("id") != LV_ITEM_ID]
    if lv is None:
        return rest
    lv_merged, _ = merge_items([i for i in prev_items if i.get("id") == LV_ITEM_ID], [lv], authoritative=True)
    return rest + lv_merged


# Join details and the fields derived from them. When one is missing today (a partial parse after a
# layout change is likelier than the meeting losing its Zoom ID), yesterday's group is kept.
JOIN_GROUPS = {
    "zoom_id": ("zoom_id",),
    "passcode": ("passcode",),
    "day": ("day", "weekday"),
    "time": ("time", "start_local", "timezone", "time_central", "next_start"),
}


def carry_join_details(item: dict, prev_items: list[dict]) -> list[str]:
    """Fill missing join details of `item` from the previous item (in place); returns the names kept."""
    prev = next((i for i in prev_items if i.get("id") == item.get("id")), None)
    if not prev:
        return []
    pe, ne = prev.get("extra") or {}, item.setdefault("extra", {})
    carried = []
    for name, keys in JOIN_GROUPS.items():
        if not ne.get(name) and pe.get(name):
            for k in keys:
                if pe.get(k) not in (None, ""):
                    ne[k] = pe[k]
            carried.append(name)
    return carried


# --------------------------------------------------------------------------- main
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the item, do not write data/raw")
    ap.add_argument("--html", help="parse a saved HTML file instead of fetching (testing)")
    args = ap.parse_args(argv)

    full_cfg = load_config()
    cfg = full_cfg.get("sources", {}).get("grapevine", {}) or {}
    lv_cfg = full_cfg.get("lavina_weekly_open")
    page_url = (cfg.get("base") or "https://www.aagrapevine.org").rstrip("/") + (cfg.get("weekly_open") or "/grapevine-weekly-open")
    prev = load_raw(SOURCE)
    prev_items = prev.get("items", [])
    prev_gv = [i for i in prev_items if i.get("id") != LV_ITEM_ID]

    if args.html:
        with open(args.html, encoding="utf-8") as f:
            html = f.read()
    else:
        html = shared_session().get_text(page_url)
    if not html:
        log.warning("could not fetch %s — keeping previous item", page_url)
        if not args.dry_run:
            save_raw(SOURCE, with_lavina(prev_gv, prev_items, lv_cfg), ok=False, error=f"fetch failed: {page_url}",
                     stats=prev.get("stats"))
        return

    try:
        parsed = parse_page(html, page_url)
    except Exception as e:  # layout surprises must never crash the pipeline
        log.exception("parse error")
        parsed, err = {}, f"parse error: {type(e).__name__}: {e}"
    else:
        err = None

    found = [k for k in ("zoom_id", "passcode", "day", "time") if parsed.get(k)]
    missing = [k for k in ("zoom_id", "passcode", "day", "time") if not parsed.get(k)]
    log.info("found %s; missing %s", found, missing or "nothing")

    if not parsed.get("zoom_id") and not parsed.get("day"):
        # Nothing useful — the page layout probably changed. Keep yesterday's item.
        err = err or "could not find the Zoom ID or meeting day on the page (layout changed?)"
        log.warning(err)
        if not args.dry_run:
            save_raw(SOURCE, with_lavina(prev_gv, prev_items, lv_cfg), ok=False, error=err, stats={"missing": missing})
        return

    item = build_item(parsed, page_url)
    if args.dry_run:
        import json
        print(json.dumps([item, lavina_item(lv_cfg)], ensure_ascii=False, indent=1))
        return
    # Today's page is the truth (a link or note the page dropped disappears), except for the join
    # details: if the parser missed one today, yesterday's value is kept and listed in stats.
    carried = carry_join_details(item, prev_items)
    merged, _ = merge_items(prev_gv, [item], drop_missing=True, authoritative=True)
    merged = with_lavina(merged, prev_items, lv_cfg)       # Grapevine first, then La Viña
    stats = {"found": found, "missing": missing}
    if carried:
        stats["kept_from_previous"] = carried
    save_raw(SOURCE, merged, ok=True, stats=stats)
    log.info("weekly open: %s %s, Zoom %s", item["extra"].get("day"), item["extra"].get("time"),
             item["extra"].get("zoom_id"))


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
