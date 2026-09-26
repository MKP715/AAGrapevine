"""Grapevine meetings in our Area and next to it → data/raw/meetings.json (→ data/site/meetings.json)

Where the meetings come from
----------------------------
The intergroups / central offices in and around Area 65 publish their meeting lists with the
"12 Step Meeting List" WordPress plugin (TSML) — the same eight lists the Rowlett Group's meetings page
combines (github.com/NETA65/RowlettAA → meetings.html): Dallas, Fort Worth, Tyler, the Spanish-speaking
Dallas office, District 71 (Abilene), and next to our Area the Arkansas Central Office, OKC Intergroup
and Northwest Texas Area 66. config/site.yml `meetings:` lists them. For each office, once a day, ONE of
these is read (`methods`, in order — the next one only when one fails):

  feed   the plugin's JSON list, <site>/wp-admin/admin-ajax.php?action=meetings[&key=…]: every meeting of
         the office. Dallas and Fort Worth answer it only with a key (see "Keys").
  page   the office's public meeting-list page (`page`, default <site>/meetings/), filtered to one type
         (?tsml-day=any&tsml-type=GR). The classic plugin page embeds that list with its locations
         (`var locations = {…}`) next to a table (24-hour time, region, attendance). The newer "TSML UI"
         page names a public cache file instead (data-src="/wp-content/tsml-cache-….json", the same JSON
         as the feed), read next. tyler-aa.org's robots.txt does not allow /wp-admin/, so only its page
         is read (methods: ["page"]).

What is kept
------------
  * every meeting whose types include `meetings.type` ("GR" = Grapevine) and that is not "inactive"
    (`add_types` / `region_types` of an office add types first, as on the Rowlett page — e.g. "S" for
    the Spanish-speaking office);
  * `in_area`: the city of the formatted address is matched to its county (scripts/sync/geo.py,
    data/geo/texas_places.json; the office's region name when the city itself is unknown) — true when
    that is one of config spotlight.neta65_counties. A Texas city that cannot be matched counts as in our
    Area only when its office says `in_area: true` (logged). Every other meeting is "nearby": it is shown
    under its office's `region_label` (build_site → groups);
  * ONLY public fields (to_record): name, day, time, place, address, map position, region, district,
    types, attendance and the meeting's own page. Phones, e-mails, contact names, Zoom links / phone
    numbers / passwords, notes, payment links and edit links are never copied: online and hybrid meetings
    link to their page on the office's site, which has the joining details. The free-text place name and
    the meeting name are also checked: a place name with a phone number or an e-mail address is dropped,
    and such a part of a meeting name is cut off;
  * the same meeting in two lists (same day + time + street address, or ≤ 60 m apart) is ONE item that
    names both offices; the richer record wins and the other fills its gaps. Two records of ONE office
    with their own meeting pages are never merged (two groups in two rooms of one club).

Keys (Dallas Intergroup, Fort Worth Central Office)
---------------------------------------------------
Found per office in this order: (1) the environment variable named in `key_env` (a GitHub secret);
(2) `feed_obf` in config/site.yml — the full feed address WITH its key, written backwards and then
base64-encoded, exactly the way RowlettAA's meetings.html stores it (it only keeps the key from casual
reading: it is NOT encryption); (3) RowlettAA's meetings.html itself (`key_source.url`, one request): the
JavaScript constant named in `key_const`, decoded the same way. When the office refuses a key (HTTP 401 /
403, e.g. after it changed its key), the next source is tried (a warning names the refused one). A key is
only ever sent to the site it belongs to: the decoded address must be on the feed's own host, and a
redirect of the keyed request is followed only on that same host (and when robots.txt allows it) —
otherwise the list is not read. No key is ever written anywhere in plain
text — not in data/raw, data/site or status.json, not in the logs: every address and message that could
carry one goes through redact(), also inside the shared HTTP logger. Without a usable key, the office's
public page is read instead.

    python -m scripts.sync.meetings --obfuscate "https://…/admin-ajax.php?action=meetings&key=…"

prints the `feed_obf` value for a new address (e.g. after an office changes its key).

Politeness: one request per office (+1 for a TSML UI cache file, +1 for the key source when it is
needed), robots.txt and its Crawl-delay obeyed (common.PoliteSession; checked against the full address
with its query), a clear User-Agent, timeouts, retries. If an office's list cannot be read — or its full
list comes back empty, or its page has a meeting table without the location data — its previous meetings
are kept (feeds[].ok = false, a note in stats.warnings); the envelope is ok=false only when no list at all
could be read.

Items: kind "meeting", source "meetings", id "mtg:<hash of day|time|place>" (place = the street address, else
the meeting's own page), url = the meeting's page on
the office's site, title = the meeting's name; extra = day (0 = Sunday), time, end_time, location,
address, street, zip, city, county, state, lat, lng, approximate, region, district, types, attendance,
in_area, sources. Envelope extras: `feeds` (per office: id, name, url, lang, ok, count, method,
key_from, error, note, updated, attempted) and `type_labels` ({code: {en, es}} for the codes in use).
build_site() turns the envelope into data/site/meetings.json (build_data.py; docs/DATA_SCHEMA.md →
"meetings.json").

Run:  python -m scripts.sync.meetings [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import binascii
import html as htmllib
import json
import logging
import math
import os
import re
import traceback
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .common import (PoliteSession, clean_text, get_logger, load_config, load_raw, make_item, merge_items, now_iso,
                     run_module, save_raw, short_hash)
from .geo import classify_location, fold, neta65_counties, normalize_place

SOURCE = "meetings"
log = get_logger(SOURCE)

DEFAULT_TYPE = "GR"
TIMEOUT = 60                       # seconds — the Dallas list is ~2 MB
MAX_BYTES = 25_000_000
NEAR_METERS = 60                   # two records this close, same day and time = one meeting
ATTENDANCE = ("in_person", "online", "hybrid")
METHODS = ("feed", "page")

# The 12 Step Meeting List plugin's standard meeting types (AA), English + Spanish.
TYPE_LABELS: dict[str, dict[str, str]] = {
    "11": {"en": "11th Step Meditation", "es": "Meditación del Paso 11"},
    "12x12": {"en": "12 Steps & 12 Traditions", "es": "Doce Pasos y Doce Tradiciones"},
    "A": {"en": "Secular", "es": "Secular"},
    "ABSI": {"en": "As Bill Sees It", "es": "Como lo ve Bill"},
    "AL": {"en": "Concurrent with Alateen", "es": "Al mismo tiempo que Alateen"},
    "AL-AN": {"en": "Concurrent with Al-Anon", "es": "Al mismo tiempo que Al-Anon"},
    "ASL": {"en": "American Sign Language", "es": "Lengua de señas americana"},
    "B": {"en": "Big Book", "es": "Libro Grande"},
    "BA": {"en": "Babysitting Available", "es": "Cuidado de niños disponible"},
    "BE": {"en": "Newcomer", "es": "Para recién llegados"},
    "BI": {"en": "Bisexual", "es": "Bisexual"},
    "BRK": {"en": "Breakfast", "es": "Desayuno"},
    "C": {"en": "Closed", "es": "Cerrada"},
    "CAN": {"en": "Candlelight", "es": "A la luz de las velas"},
    "CF": {"en": "Child-Friendly", "es": "Se admiten niños"},
    "D": {"en": "Discussion", "es": "De discusión"},
    "DA": {"en": "Danish", "es": "En danés"},
    "DB": {"en": "Digital Basket", "es": "Canasta digital"},
    "DD": {"en": "Dual Diagnosis", "es": "Diagnóstico dual"},
    "DE": {"en": "German", "es": "En alemán"},
    "DR": {"en": "Daily Reflections", "es": "Reflexiones diarias"},
    "EN": {"en": "English", "es": "En inglés"},
    "FF": {"en": "Fragrance Free", "es": "Sin fragancias"},
    "FR": {"en": "French", "es": "En francés"},
    "G": {"en": "Gay", "es": "Gay"},
    "GR": {"en": "Grapevine", "es": "Grapevine"},
    "H": {"en": "Birthday", "es": "Cumpleaños"},
    "HE": {"en": "Hebrew", "es": "En hebreo"},
    "ITA": {"en": "Italian", "es": "En italiano"},
    "JA": {"en": "Japanese", "es": "En japonés"},
    "KOR": {"en": "Korean", "es": "En coreano"},
    "L": {"en": "Lesbian", "es": "Lesbianas"},
    "LGBTQ": {"en": "LGBTQ", "es": "LGBTQ"},
    "LIT": {"en": "Literature", "es": "Literatura"},
    "LS": {"en": "Living Sober", "es": "Viviendo sobrio"},
    "M": {"en": "Men", "es": "Hombres"},
    "MED": {"en": "Meditation", "es": "Meditación"},
    "N": {"en": "Native American", "es": "Nativos americanos"},
    "NDG": {"en": "Indigenous", "es": "Indígenas"},
    "O": {"en": "Open", "es": "Abierta"},
    "ONL": {"en": "Online", "es": "En línea"},
    "OUT": {"en": "Outdoor", "es": "Al aire libre"},
    "P": {"en": "Professionals", "es": "Profesionales"},
    "POC": {"en": "People of Color", "es": "Personas de color"},
    "POL": {"en": "Polish", "es": "En polaco"},
    "POR": {"en": "Portuguese", "es": "En portugués"},
    "PUN": {"en": "Punjabi", "es": "En panyabí"},
    "RUS": {"en": "Russian", "es": "En ruso"},
    "S": {"en": "Spanish", "es": "En español"},
    "SEN": {"en": "Seniors", "es": "Personas mayores"},
    "SM": {"en": "Smoking Permitted", "es": "Se permite fumar"},
    "SP": {"en": "Speaker", "es": "Con orador"},
    "ST": {"en": "Step Study", "es": "Estudio de los Pasos"},
    "T": {"en": "Transgender", "es": "Transgénero"},
    "TC": {"en": "Location Temporarily Closed", "es": "Lugar cerrado temporalmente"},
    "TR": {"en": "Tradition Study", "es": "Estudio de las Tradiciones"},
    "W": {"en": "Women", "es": "Mujeres"},
    "X": {"en": "Wheelchair Access", "es": "Acceso para silla de ruedas"},
    "XB": {"en": "Wheelchair-Accessible Bathroom", "es": "Baño accesible para silla de ruedas"},
    "XT": {"en": "Cross Talk Permitted", "es": "Se permiten comentarios cruzados"},
    "Y": {"en": "Young People", "es": "Jóvenes"},
}


class FeedError(Exception):
    """One office's list could not be read (the message is shown on /status/ — always redacted)."""


class KeyRejected(FeedError):
    """The office refused the key (HTTP 401 / 403): the next key source is tried."""


# =========================================================================== keys & redaction
_SECRETS: set[str] = set()          # every key resolved this run: also scrubbed from any text
_KEY_PARAM = re.compile(r"(?i)(\bkey(?:=|%3D))[^&#\s'\"<>)\]]+")


def redact(text: Any) -> str:
    """'…?action=meetings&key=abc123' → '…?action=meetings&key=[redacted]' (and any known key value)."""
    s = _KEY_PARAM.sub(r"\1[redacted]", str(text if text is not None else ""))
    for k in _SECRETS:
        if k:
            s = s.replace(k, "[redacted]")
    return s


class RedactFilter(logging.Filter):
    """Scrubs keys from a log record (message, arguments and traceback) before any handler sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — a broken record is left as it is
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
        return True


_FILTER = RedactFilter()


def install_redaction() -> None:
    """On this module's logger, the shared HTTP logger (PoliteSession prints failed addresses) and
    urllib3's (it can print a retried address)."""
    for lg in (log, get_logger("http"), logging.getLogger("urllib3.connectionpool")):
        if _FILTER not in lg.filters:
            lg.addFilter(_FILTER)


install_redaction()


def obfuscate(url: str) -> str:
    """The `feed_obf` form of an address: written backwards, then base64 (RowlettAA's meetings.html)."""
    return base64.b64encode(url.strip()[::-1].encode("utf-8")).decode("ascii")


def deobfuscate(s: Any) -> str | None:
    """feed_obf / a RowlettAA constant → the address (None when it does not decode to one). Also accepts
    the base64 of an address that was not reversed."""
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", str(s or "")), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    for cand in (raw[::-1], raw):
        if re.match(r"https?://", cand):
            return cand.strip()
    return None


# const FEED_URL = (function(){var _r='…' + '…';return atob(_r).split('').reverse().join('')})();
_JS_CONST = (r"\b(?:const|let|var)\s+{name}\s*=\s*\(\s*function\s*\(\s*\)\s*\{{\s*(?:var|let|const)\s+\w+\s*=\s*"
             r"(?P<parts>(?:\s*(?:'[^'\n]*'|\"[^\"\n]*\")\s*\+?)+)\s*;\s*return\s+atob\(")


def js_const_url(page: str, name: str) -> str | None:
    """The address a RowlettAA-style obfuscated JavaScript constant decodes to (None if not found)."""
    if not page or not name:
        return None
    m = re.search(_JS_CONST.format(name=re.escape(name)), page)
    if not m:
        return None
    joined = "".join(a or b for a, b in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", m.group("parts")))
    return deobfuscate(joined)


def _bare_host(url: Any) -> str:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def key_in(address: str | None, feed_url: str) -> str | None:
    """The key= value of an address — only when the address is on the feed's own site."""
    if not address or _bare_host(address) != _bare_host(feed_url):
        return None
    k = ((parse_qs(urlsplit(address).query).get("key") or [""])[0]).strip()
    return k if re.fullmatch(r"[A-Za-z0-9_.~-]{8,200}", k) else None


def key_candidates(feed: dict, env: Mapping[str, str], key_page: Callable[[], str | None]):
    """Every usable key in order — (key, "secret" | "config" | "key_source") — each key once. Lazy: the
    key source page is only read when the keys before it are missing or refused."""
    seen: set[str] = set()
    name = str(feed.get("key_env") or "").strip()
    val = str(env.get(name) or "").strip() if name else ""
    if val:
        if "key=" in val:                  # the whole feed address was pasted into the secret
            val = key_in(val, feed["feed"]) or ""
        if re.fullmatch(r"[A-Za-z0-9_.~-]{8,200}", val):
            seen.add(val)
            yield val, "secret"
        else:
            log.warning("%s: the %s secret is not a usable key — ignored", feed["id"], name)
    if feed.get("feed_obf"):
        k = key_in(deobfuscate(feed["feed_obf"]), feed["feed"])
        if k and k not in seen:
            seen.add(k)
            yield k, "config"
        elif not k:
            log.warning("%s: feed_obf in config/site.yml does not decode to a %s address with a key — ignored",
                        feed["id"], _bare_host(feed["feed"]))
    if feed.get("key_const"):
        page = key_page()
        k = key_in(js_const_url(page or "", str(feed["key_const"])), feed["feed"]) if page else None
        if k and k not in seen:
            seen.add(k)
            yield k, "key_source"
        elif page and not k:
            log.warning("%s: %s was not found in the key source page (or is for another site)",
                        feed["id"], feed["key_const"])


def resolve_key(feed: dict, env: Mapping[str, str], key_page: Callable[[], str | None]) -> tuple[str | None, str | None]:
    """(key, where it came from: "secret" | "config" | "key_source") — (None, None) when there is none."""
    return next(key_candidates(feed, env, key_page), (None, None))


# =========================================================================== settings
def settings(cfg: dict | None = None) -> dict:
    """config/site.yml `meetings:` with defaults; offices without an id or a usable method are skipped."""
    cfg = load_config() if cfg is None else cfg
    m = cfg.get("meetings") if isinstance(cfg, dict) and isinstance(cfg.get("meetings"), dict) else {}
    ks = m.get("key_source") if isinstance(m.get("key_source"), dict) else {}
    feeds = []
    for f in m.get("feeds") or []:
        if not isinstance(f, dict) or not str(f.get("id") or "").strip():
            continue
        feed = {
            "id": str(f["id"]).strip(), "name": clean_text(f.get("name")) or str(f["id"]),
            "url": str(f.get("site") or f.get("url") or "").strip().rstrip("/"), "feed": str(f.get("feed") or "").strip(),
            "page": str(f.get("page") or "").strip(), "lang": "es" if str(f.get("lang") or "").lower() == "es" else "en",
            "in_area": bool(f.get("in_area")), "key_env": str(f.get("key_env") or "").strip(),
            "key_const": str(f.get("key_const") or "").strip(), "feed_obf": str(f.get("feed_obf") or "").strip(),
            "region_label": _label(f.get("region_label")) or {"en": clean_text(f.get("name")) or str(f["id"]),
                                                              "es": clean_text(f.get("name")) or str(f["id"])},
            "add_types": _types(f.get("add_types")),
            "region_types": {fold(k): _types(v) for k, v in (f.get("region_types") or {}).items()}
            if isinstance(f.get("region_types"), dict) else {},
        }
        if not feed["url"] and (feed["feed"] or feed["page"]):
            feed["url"] = f"https://{urlsplit(feed['feed'] or feed['page']).netloc}"
        if not feed["page"] and feed["url"]:
            feed["page"] = feed["url"] + "/meetings/"          # the plugin's default meeting-list page
        methods = f.get("methods") or ["feed", "page"]
        feed["methods"] = [x for x in (str(v).strip().lower() for v in methods)
                           if x in METHODS and feed["feed" if x == "feed" else "page"]]
        if f.get("enabled", True) is False or not feed["methods"]:
            continue
        feeds.append(feed)
    return {"enabled": m.get("enabled", True) is not False, "type": str(m.get("type") or DEFAULT_TYPE).strip(),
            "area_label": _label(m.get("area_label")) or {"en": "Our Area (NETA 65)", "es": "Nuestra Área (NETA 65)"},
            "key_source": {"url": str(ks.get("url") or "").strip()}, "feeds": feeds}


def _label(v: Any) -> dict[str, str] | None:
    """{en, es} from config ({en: …, es: …} or one plain text for both)."""
    if isinstance(v, dict):
        en, es = clean_text(v.get("en")), clean_text(v.get("es"))
        return {"en": en or es, "es": es or en} if en or es else None
    t = clean_text(v)
    return {"en": t, "es": t} if t else None


# =========================================================================== reading one office
Fetch = Callable[[str, "dict | None"], "tuple[str, str]"]      # (url, params) → (text, final url); raises FeedError


def parse_feed(text: str) -> list[dict]:
    """The plugin's JSON (feed or TSML UI cache file) → its meetings."""
    try:
        data = json.loads(text)
    except ValueError:
        raise FeedError("the answer is not a meeting list (not JSON)") from None
    if isinstance(data, dict) and isinstance(data.get("meetings"), list):
        data = data["meetings"]
    if not isinstance(data, list):
        raise FeedError("the answer is not a meeting list")
    return [m for m in data if isinstance(m, dict)]


def clock24(v: Any) -> str | None:
    """'18:00' / '18:00:00' / '6:00 pm' / 'Noon' / 'Midnight' → 'HH:MM' (None if not a time)."""
    t = clean_text(v).lower().replace(".", "")
    if t in ("noon", "mediodía", "mediodia"):
        return "12:00"
    if t in ("midnight", "medianoche"):
        return "00:00"
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*([ap])?\s*m?", t)
    if not m or (m[3] is None and m[2] is None):
        return None
    h, mi = int(m[1]), int(m[2] or 0)
    if m[3]:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if m[3] == "p" else 0)
    return f"{h:02d}:{mi:02d}" if 0 <= h <= 23 and 0 <= mi <= 59 else None


_OUR_PARAMS = ("tsml-day", "tsml-type", "key")


def _no_query(url: Any) -> str:
    """A meeting's page address without the list filters we asked for (and never a key); its own query
    stays ("https://district71.org/?tsml_meeting=sunshine-4")."""
    p = urlsplit(str(url or "").strip())
    if not p.scheme:
        return ""
    query = "&".join(q for q in p.query.split("&") if q and q.split("=", 1)[0].lower() not in _OUR_PARAMS)
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def _table_rows(page: str) -> dict[str, dict]:
    """The classic page's table: meeting page (no query) → day, time (24 h), region, attendance, types."""
    soup = BeautifulSoup(page, "html.parser")
    body = soup.find(id="meetings_tbody")
    rows: dict[str, dict] = {}
    for tr in body.find_all("tr") if body else []:
        a = tr.select_one("td.name a[href]")
        if not a:
            continue
        cls = tr.get("class") or []
        row: dict[str, Any] = {
            "types": [c[5:].upper() if c[5:].lower() != "12x12" else "12x12" for c in cls if c.startswith("type-")],
            "attendance": next((c[11:] for c in cls if c.startswith("attendance-")), None),
        }
        td = tr.select_one("td.time")
        m = re.match(r"(\d)-(\d{1,2}:\d{2})", (td.get("data-sort") or "") if td else "")
        if m:
            row["day"], row["time"] = int(m[1]), clock24(m[2])
        reg = tr.select_one("td.region")
        row["region"] = clean_text(reg.get_text(" ")) if reg else None
        rows[_no_query(htmllib.unescape(a["href"]))] = row
    return rows


def parse_page(page: str, page_url: str) -> tuple[list[dict] | None, str | None]:
    """An office's public meeting-list page → (meetings in the feed's shape, None) for the classic page,
    or (None, cache file address) for a TSML UI page."""
    m = re.search(r"\bvar\s+locations\s*=\s*", page)
    if m:
        try:
            locs, _end = json.JSONDecoder().raw_decode(page, m.end())
        except ValueError:
            raise FeedError("the meeting list on the page could not be read") from None
        rows = _table_rows(page)
        out = []
        for loc in (locs.values() if isinstance(locs, dict) else []):
            if not isinstance(loc, dict):
                continue
            for mt in loc.get("meetings") or []:
                if not isinstance(mt, dict):
                    continue
                url = _no_query(mt.get("url"))
                row = rows.get(url, {})
                out.append({
                    "name": mt.get("name"), "url": url, "day": row.get("day", mt.get("day")),
                    "time": row.get("time") or clock24(mt.get("time")), "end_time": None,
                    "types": mt.get("types") or row.get("types") or [], "location": loc.get("name"),
                    "formatted_address": loc.get("formatted_address"), "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"), "region": row.get("region"),
                    "attendance_option": row.get("attendance") or "in_person",
                })
        return out, None
    m = re.search(r"""<div[^>]*\bid=['"]tsml-ui['"][^>]*\bdata-src=['"]([^'"]+)['"]""", page) or \
        re.search(r"""\bdata-src=['"]([^'"]*tsml-cache[^'"]*\.json[^'"]*)['"]""", page)
    if m:
        return None, urljoin(page_url, htmllib.unescape(m.group(1)))
    if re.search(r"""\bid=['"](?:meetings_tbody|tsml)['"]""", page):
        if _table_rows(page):              # meetings listed, but not their places: the page changed
            raise FeedError("the meetings page changed format (a table but no location data) — not read")
        return [], None                    # the classic page, with no meeting of this type today
    raise FeedError("no meeting list was found on the office's meetings page")


def read_office(feed: dict, fetch: Fetch, env: Mapping[str, str], key_page: Callable[[], str | None],
                type_code: str) -> tuple[list[dict], str, str | None, list[str]]:
    """(meetings, method used, where the key came from, problems of the methods that failed first).
    Raises FeedError (redacted) when no method worked."""
    problems: list[str] = []
    key_from = None
    for method in feed["methods"]:
        try:
            if method == "feed":
                if feed["key_env"] or feed["key_const"] or feed["feed_obf"]:
                    text, key_from = None, None
                    refused: list[str] = []
                    for key, src in key_candidates(feed, env, key_page):
                        _SECRETS.add(key)
                        try:
                            text, _final = fetch(feed["feed"], {"key": key})
                        except KeyRejected:
                            refused.append(src)
                            log.warning("%s: the key from %s was not accepted — trying the next one", feed["id"], src)
                            continue
                        key_from = src
                        break
                    if refused:
                        problems.append("the key from " + " and ".join(refused) + " was not accepted"
                                        + (f" (the key from {key_from} worked)" if key_from else ""))
                    if text is None:
                        if refused:
                            raise FeedError("no key was accepted for its meeting list (update the "
                                            f"{feed['key_env'] or 'feed_obf'} value — see config/site.yml)")
                        raise FeedError("no key for its meeting list (set the "
                                        f"{feed['key_env'] or 'feed_obf'} value — see config/site.yml)")
                else:
                    text, _final = fetch(feed["feed"], None)
                meetings = parse_feed(text)
                if not meetings:           # an office's whole list is never empty
                    raise FeedError("the meeting list is empty")
                return meetings, method, key_from, problems
            page_url = feed["page"]
            text, final = fetch(page_url, {"tsml-day": "any", "tsml-type": type_code})
            meetings, cache_url = parse_page(text, final or page_url)
            if cache_url is not None:
                if _bare_host(cache_url) != _bare_host(page_url):
                    raise FeedError("the page's meeting data is on another site — not read")
                text, _final = fetch(cache_url, None)
                meetings = parse_feed(text)
                if not meetings:           # the cache file holds the office's whole list
                    raise FeedError("the meeting list is empty")
            return meetings or [], method, None, problems
        except FeedError as e:
            problems.append(f"{method}: {redact(e)}")
            if method != feed["methods"][-1]:
                log.info("%s: %s — trying the next way", feed["id"], redact(e))
    raise FeedError("; ".join(problems) or "nothing to read")


# =========================================================================== one meeting
_ADDR = re.compile(r"^(?:(?P<street>.*?),\s*)?(?P<city>[^,]+?),\s*(?P<state>[A-Z]{2})(?:\s+(?P<zip>\d{5})(?:-\d{4})?)?$")


def split_address(formatted: Any) -> dict:
    """'1144 N Plano Rd, Richardson, TX 75081, USA' → {address (no country), street, city, state, zip}."""
    a = clean_text(formatted).strip(" ,")
    a = re.sub(r",\s*(?:USA|US|United States(?: of America)?|EE\.? ?UU\.?)$", "", a, flags=re.I).strip(" ,")
    m = _ADDR.match(a)
    if not m:
        return {"address": a or None, "street": None, "city": None, "state": None, "zip": None}
    return {"address": a, "street": clean_text(m["street"]) or None, "city": clean_text(m["city"]),
            "state": m["state"], "zip": m["zip"]}


def area_of(city: str | None, state: str | None, feed: dict, region: str | None = None) -> tuple[bool, dict, str]:
    """(in our Area?, geo result, note) — module docstring, "What is kept". The office's region name
    ("Granbury") is tried when the city itself is not in the gazetteer ("Brazos Bend")."""
    if state and state != "TX":
        return False, {}, f"in {state}"
    if not city and not region:
        return feed["in_area"], {}, "no city in the address"
    g = classify_location(f"{city or region}, Texas")
    if g.get("scope") == "neta65":
        return True, g, ""
    if region and city and g.get("scope") == "texas" and not g.get("counties"):
        by_region = classify_location(f"{region}, Texas")
        if by_region.get("counties"):
            g = {**by_region, "city": city}
            if g.get("scope") == "neta65":
                return True, g, ""
    if g.get("scope") == "texas" and not g.get("counties"):
        return feed["in_area"], g, f"city {city or region!r} not matched to a county"
    return False, g, f"{', '.join(g.get('counties') or []) or 'another'} County, outside Area 65"


def _float(v: Any, lo: float, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 6) if lo <= f <= hi and f != 0 else None


def _county(g: dict) -> str | None:
    if g.get("county"):
        return g["county"]
    neta = neta65_counties()
    counties = g.get("counties") or []
    return next((c for c in counties if normalize_place(c) in neta), counties[0] if counties else None)


_PERSONAL_RE = re.compile(r"@|\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\+\d[\d\s().-]{8,}")


def _house_number(v: Any) -> str | None:
    m = re.match(r"\s*(\d+)\b", str(v or ""))
    return m.group(1) if m else None


def _location_name(loc: Any, name: str, parts: dict) -> str | None:
    """The place's own name ("Serenity Club", "Two story building with Blue Awning") — None when it only
    repeats the meeting's name or the address, or when it carries a phone number or an e-mail address.
    A place written as the street again plus a detail keeps only the detail
    ("1144 N Plano Road, Suite 246 (Bus Route Access)" → "Suite 246 (Bus Route Access)")."""
    s = clean_text(loc).strip(" ,")
    if not s or fold(s) == fold(name) or _PERSONAL_RE.search(s):
        return None
    if parts.get("address") and re.sub(r",\s*usa$", "", fold(s)) == fold(parts["address"]):
        return None
    num = _house_number(s)
    if num and num in (_house_number(parts.get("street")), _house_number(parts.get("address"))):
        rest = s[re.match(r"\s*\d+[^,(]*", s).end():].strip(" ,")
        if parts.get("city"):              # "…, Suite 5, Richardson, TX" → "Suite 5"; "…, Rockwall, TX" → ""
            rest = re.split(rf"(?i)(?:^|,)\s*{re.escape(parts['city'])}\b", rest)[0].strip(" ,")
        if rest.startswith("(") and rest.endswith(")") and rest.count("(") == 1:
            rest = rest[1:-1].strip()      # "6101 Watauga Rd (Strip Center)" → "Strip Center"
        return rest[:120] or None
    if parts.get("city") and re.match(r"\d", s) and re.search(rf",\s*{re.escape(fold(parts['city']))}\b", fold(s)):
        return None                        # "190 Shennendoah ln, Rockwall, TX" = the address again
    return s[:120]


def _clean_name(v: Any) -> str:
    """A meeting name without a phone number or an e-mail address someone typed into it."""
    t = clean_text(v)
    if _PERSONAL_RE.search(t):
        t = re.sub(r"\S*@\S+", "", t)
        t = _PERSONAL_RE.sub("", t)
        t = re.sub(r"\s{2,}", " ", re.sub(r"[\s,;:–—-]*(?:call|text|tel|phone|contact|email|e-mail)?[\s,;:–—-]*$", "",
                                          t, flags=re.I)).strip(" ,;:-–—()")
    return t


def _types(v: Any) -> list[str]:
    out = []
    for t in v if isinstance(v, list) else []:
        t = clean_text(t)
        if t and len(t) <= 12 and t not in out:
            out.append(t)
    return sorted(out, key=str.upper)


def to_record(m: dict, feed: dict, type_code: str) -> tuple[dict | None, str]:
    """A meeting of a list → (the public record, "") or (None, why it is left out: "type" | "inactive" |
    "incomplete"). Only the fields named here are read — nothing personal is copied."""
    region = clean_text(m.get("region")) or None
    types = _types(list(m.get("types") or []) + feed["add_types"] + feed["region_types"].get(fold(region or ""), []))
    if type_code not in types:
        return None, "type"
    att = clean_text(m.get("attendance_option")).lower().replace("-", "_").replace(" ", "_") or "in_person"
    if att == "inactive":
        return None, "inactive"
    att = att if att in ATTENDANCE else "in_person"
    try:
        day = int(str(m.get("day")).strip())
    except (TypeError, ValueError):
        day = -1
    start = clock24(m.get("time"))
    name = _clean_name(m.get("name")) or _clean_name(m.get("group"))
    if not 0 <= day <= 6 or not start or not name:
        return None, "incomplete"
    end = clock24(m.get("end_time"))
    if end and end <= start:               # "00:00" = not given
        end = None
    parts = split_address(m.get("formatted_address"))
    in_area, g, note = area_of(parts["city"], parts["state"], feed, region)
    if note and (in_area or feed["in_area"]):
        log.info("%s: %r — %s -> %s", feed["id"], name, note, "our Area" if in_area else "nearby")
    lat, lng = _float(m.get("latitude"), -90, 90), _float(m.get("longitude"), -180, 180)
    url = _no_query(m.get("url"))
    if url.startswith("http://"):
        url = "https://" + url[7:]
    if _bare_host(url) != _bare_host(feed["url"] or feed["feed"] or feed["page"]):
        url = feed["page"] or feed["url"]
    district = clean_text(m.get("district")) or None
    return {
        "name": name[:160], "day": day, "time": start, "end_time": end,
        "location": _location_name(m.get("location"), name, parts),
        "address": parts["address"], "street": parts["street"], "zip": parts["zip"],
        "city": parts["city"] or g.get("city"), "county": _county(g) if g else None,
        "state": parts["state"] or ("TX" if g else None),
        "lat": lat, "lng": lng, "approximate": str(m.get("approximate") or "").lower() == "yes",
        "region": region[:80] if region else None, "district": district[:80] if district else None,
        "types": types, "attendance": att, "in_area": in_area,
        "lang": "es" if "S" in types or feed["lang"] == "es" else "en", "url": url, "sources": [feed["id"]],
    }, ""


# =========================================================================== one item per meeting
_STREET_WORDS = {"street": "st", "road": "rd", "avenue": "ave", "av": "ave", "drive": "dr", "lane": "ln",
                 "boulevard": "blvd", "parkway": "pkwy", "highway": "hwy", "freeway": "fwy", "court": "ct",
                 "circle": "cir", "place": "pl", "trail": "trl", "north": "n", "south": "s", "east": "e",
                 "west": "w", "suite": "ste", "interstate": "i"}


def address_key(rec: dict) -> str:
    """'1144 N Plano Road, Suite 246' + 75081 → '1144 n plano rd|75081' (suite / unit numbers ignored)."""
    street = fold(rec.get("street") or "")
    street = re.split(r"\s*(?:#|\bste\b|\bsuite\b|\bunit\b|\bapt\b|\broom\b|\brm\b)", street)[0]
    words = [_STREET_WORDS.get(w, w) for w in re.findall(r"[a-z0-9]+", street)]
    if not words:
        return ""
    return " ".join(words) + "|" + (rec.get("zip") or fold(rec.get("city") or ""))


def _meters(a: dict, b: dict) -> float:
    if None in (a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng")):
        return math.inf
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lng"] - a["lng"])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6_371_000 * 2 * math.asin(min(1.0, math.sqrt(x)))


def same_meeting(a: dict, b: dict) -> bool:
    if (a["day"], a["time"]) != (b["day"], b["time"]):
        return False
    # one office never lists one meeting twice under two pages (two groups in two rooms of one club)
    if set(a["sources"]) & set(b["sources"]) and a.get("url") and b.get("url") and a["url"] != b["url"]:
        return False
    ka, kb = address_key(a), address_key(b)
    if ka and ka == kb:
        return True
    return not (a.get("approximate") or b.get("approximate")) and _meters(a, b) <= NEAR_METERS


def richness(rec: dict) -> float:
    return sum(1 for k in ("end_time", "location", "region", "street", "lat", "url") if rec.get(k)) + 0.1 * len(rec["types"])


def record_id(rec: dict) -> str:
    """Stable: day, time and the street address — else the meeting's own page (online meetings with the
    same name, day and time stay apart), else the name."""
    place = address_key(rec) or _no_query(rec.get("url")) or fold(rec.get("name") or "")
    return "mtg:" + short_hash(f"{rec['day']}|{rec['time']}|{place}")


def dedupe(records: list[dict], order: list[str]) -> tuple[list[dict], int]:
    """One record per meeting: the richest of each group (a fresh record before a kept one, then the
    config order of its office), its empty fields filled from the others, `sources` = every office."""
    rank = {fid: i for i, fid in enumerate(order)}

    def pref(r: dict) -> tuple:
        return (-richness(r), bool(r.get("_kept")), min(rank.get(s, 99) for s in r["sources"]))

    groups: list[list[dict]] = []
    for rec in records:
        for g in groups:
            if any(same_meeting(rec, other) for other in g):
                g.append(rec)
                break
        else:
            groups.append([rec])
    out, merged = [], 0
    for g in groups:
        g.sort(key=pref)
        base = dict(g[0])
        for other in g[1:]:
            merged += 1
            for k, v in other.items():
                if base.get(k) in (None, "", []) and v not in (None, "", []):
                    base[k] = v
            base["sources"] = base["sources"] + [s for s in other["sources"] if s not in base["sources"]]
        base["sources"] = sorted(base["sources"], key=lambda s: rank.get(s, 99))
        base.pop("_kept", None)
        base["id"] = record_id(base)
        out.append(base)
    ids: dict[str, int] = {}
    for r in out:
        ids[r["id"]] = ids.get(r["id"], 0) + 1
    for r in out:                          # two meetings of one office at one address, day and time
        if ids[r["id"]] > 1 and r.get("url"):
            r["id"] = "mtg:" + short_hash(f"{r['day']}|{r['time']}|{address_key(r)}|{_no_query(r['url'])}")
    out.sort(key=lambda r: (r["day"], r["time"], fold(r["name"])))
    return out, merged


EXTRA_KEYS = ("day", "time", "end_time", "location", "address", "street", "zip", "city", "county", "state", "lat",
              "lng", "approximate", "region", "district", "types", "attendance", "in_area", "sources")


def to_item(rec: dict) -> dict:
    return make_item(id=rec["id"], source=SOURCE, kind="meeting", url=rec["url"], title=rec["name"], lang=rec["lang"],
                     tags=list(rec["types"]), extra={k: rec.get(k) for k in EXTRA_KEYS})


def from_item(it: dict) -> dict | None:
    """A kept (previous) item → a record again (to merge it with this run's records)."""
    ex = it.get("extra") or {}
    if not isinstance(ex.get("day"), int) or not ex.get("time") or not it.get("title"):
        return None
    rec = {k: ex.get(k) for k in EXTRA_KEYS}
    rec.update(name=it["title"], url=it.get("url"), lang=it.get("lang") or "en",
               types=list(ex.get("types") or []), sources=list(ex.get("sources") or []))
    return rec


# =========================================================================== one run
def collect(fetch: Fetch, prev: dict, cfg: dict | None = None, env: Mapping[str, str] | None = None) -> dict:
    """Everything one run finds → {"items", "feeds", "errors", "warnings", "stats", "type_labels", "ok"}.
    An office whose list could not be read keeps its previous meetings."""
    st = settings(cfg)
    env = os.environ if env is None else env
    now = now_iso()
    prev_items = [i for i in prev.get("items") or [] if isinstance(i, dict) and i.get("id")]
    prev_feeds = {f.get("id"): f for f in prev.get("feeds") or [] if isinstance(f, dict)}
    stats = {"offices": len(st["feeds"]), "offices_ok": 0, "listed": 0, "grapevine": 0, "meetings": 0, "in_area": 0,
             "nearby": 0, "inactive": 0, "incomplete": 0, "duplicates_merged": 0, "kept_from_before": 0}
    if not st["enabled"]:
        return {"items": [], "feeds": [], "errors": [], "warnings": [], "stats": {**stats, "disabled": True},
                "type_labels": {}, "ok": True}
    key_cache: dict[str, str | None] = {}

    def key_page() -> str | None:
        """RowlettAA's meetings.html — fetched at most once per run, only when a key is still missing."""
        url = st["key_source"]["url"]
        if url and url not in key_cache:
            key_cache[url] = None
            try:
                key_cache[url] = fetch(url, None)[0]
            except Exception as e:  # noqa: BLE001 — without it, the office's public page is read
                log.warning("key source %s: %s", url, redact(e))
        return key_cache.get(url)

    records: list[dict] = []
    feeds_out: list[dict] = []
    errors: list[str] = []
    notes: list[str] = []              # warnings of offices that were read (a refused key)
    failed: set[str] = set()
    for feed in st["feeds"]:
        before = prev_feeds.get(feed["id"]) or {}
        row = {"id": feed["id"], "name": feed["name"], "url": feed["url"], "lang": feed["lang"], "ok": False,
               "count": 0, "method": None, "key_from": None, "error": None,
               "updated": before.get("updated"), "attempted": now}
        try:
            meetings, method, key_from, problems = read_office(feed, fetch, env, key_page, st["type"])
            got = []
            for m in meetings:
                rec, why = to_record(m, feed, st["type"])
                if rec:
                    got.append(rec)
                elif why != "type":
                    stats[why] += 1
            stats["listed"] += len(meetings)
            stats["grapevine"] += len(got)
            records += got
            row.update(ok=True, count=len(got), method=method, key_from=key_from, updated=now,
                       note=redact("; ".join(problems))[:300] or None)
            refused = [p for p in problems if "was not accepted" in p]
            if refused:                    # shown on /status/: the secret or feed_obf needs updating
                notes.append(redact(f"{feed['name']}: {refused[0]}")[:300])
            stats["offices_ok"] += 1
            log.info("%s: %d %s meeting(s) of %d listed, %d in our Area (%s%s)", feed["id"], len(got), st["type"],
                     len(meetings), sum(1 for r in got if r["in_area"]), method,
                     f", key from {key_from}" if key_from else "")
        except FeedError as e:
            msg = redact(e)[:300]
            row["error"] = msg
            errors.append(f"{feed['name']}: {msg}")
            failed.add(feed["id"])
            log.warning("%s: %s — its previous meetings are kept", feed["id"], msg)
        except Exception as e:  # noqa: BLE001 — one office never stops the others
            msg = redact(f"{type(e).__name__}: {e}")[:300]
            row["error"] = msg
            errors.append(f"{feed['name']}: {msg}")
            failed.add(feed["id"])
            log.warning("%s: %s — its previous meetings are kept", feed["id"], msg)
        feeds_out.append(row)

    # An office that could not be read keeps its previous meetings (named only by the failed offices;
    # an office that answered today adds itself back when it still lists the meeting).
    for it in prev_items:
        rec = from_item(it)
        if not rec:
            continue
        rec["sources"] = [s for s in rec["sources"] if s in failed]
        if rec["sources"]:
            rec["_kept"] = True
            records.append(rec)
            stats["kept_from_before"] += 1
    items, merged = dedupe(records, [f["id"] for f in st["feeds"]])
    stats["duplicates_merged"] = merged
    stats["meetings"] = len(items)
    stats["in_area"] = sum(1 for r in items if r["in_area"])
    stats["nearby"] = len(items) - stats["in_area"]
    for row in feeds_out:              # meetings per office after merging (a shared one counts for both)
        row["count"] = sum(1 for r in items if row["id"] in r["sources"])
    codes = sorted({c for r in items for c in r["types"]})
    unknown = [c for c in codes if c not in TYPE_LABELS]
    if unknown:
        log.warning("meeting type code(s) without a label: %s — add them to TYPE_LABELS", unknown)
    type_labels = {c: dict(TYPE_LABELS.get(c) or {"en": c, "es": c}) for c in codes}
    ok = bool(st["feeds"]) and stats["offices_ok"] > 0
    if not st["feeds"]:
        errors.append("no meeting lists are set up (config/site.yml meetings.feeds)")
    return {"items": items, "feeds": feeds_out, "errors": errors, "warnings": (errors if ok else []) + notes,
            "stats": stats, "type_labels": type_labels, "ok": ok}


# =========================================================================== data/site/meetings.json
SITE_KEYS = ("id", "kind", "name", "day", "time", "end_time", "location", "address", "city", "county", "state",
             "lat", "lng", "approximate", "region", "district", "types", "attendance", "lang", "url", "sources",
             "directions_url", "in_area", "nearby", "i18n")


def empty_site(updated: str | None = None, type_code: str = DEFAULT_TYPE) -> dict:
    return {"updated": updated, "fixture": False, "type": type_code, "sources": [], "groups": [], "items": [],
            "type_labels": {}}


def directions_url(rec: dict) -> str | None:
    """Google Maps directions to the meeting's door — none for an online-only meeting or a place the
    office only gives approximately (a city, for an online meeting)."""
    if rec.get("attendance") == "online" or rec.get("approximate"):
        return None
    if rec.get("lat") is not None and rec.get("lng") is not None:
        return f"https://www.google.com/maps/dir/?api=1&destination={rec['lat']},{rec['lng']}"
    if rec.get("street") and rec.get("address"):
        return "https://www.google.com/maps/dir/?api=1&destination=" + quote_plus(rec["address"])
    return None


def build_site(env: dict, cfg: dict | None = None) -> dict:
    """data/raw/meetings.json → data/site/meetings.json (docs/DATA_SCHEMA.md → "meetings.json"):
    `items` sorted by day and time, each with `in_area` and — for a meeting outside our Area — `nearby`
    {id, label} (its office's region_label); `groups` = our Area first, then each nearby region in the
    config order, with their counts (only groups that have meetings); `sources` = every configured office
    with its health; `type_labels` for the codes in use. Meeting names are proper names: nothing is
    translated (i18n stays empty). The labels come from config/site.yml, so a new region_label shows
    without a new sync."""
    st = settings(cfg)
    if not isinstance(env, dict):
        env = {}
    if not st["enabled"]:                  # config meetings.enabled: false → no meetings on the site at once
        return empty_site(env.get("updated"), st["type"])
    feeds = {f["id"]: f for f in st["feeds"]}
    order = {fid: i for i, fid in enumerate(feeds)}
    out: list[dict] = []
    for it in env.get("items") or []:
        if not isinstance(it, dict) or it.get("kind") != "meeting" or it.get("status", "ok") == "gone":
            continue
        rec = from_item(it)
        if not rec or not rec.get("url"):
            continue
        if not any(x in feeds for x in rec["sources"]):
            continue                       # its office was switched off or removed in config/site.yml
        # the place name is checked again with today's rules (no new sync needed)
        rec["location"] = _location_name(rec.get("location"), rec["name"],
                                         {"address": rec.get("address"), "street": rec.get("street"),
                                          "city": rec.get("city")})
        srcs = sorted([x for x in rec["sources"] if x in feeds], key=lambda x: order.get(x, 99))
        in_area = bool(rec.get("in_area"))
        home = next((x for x in srcs if x in feeds), None)
        row = {**rec, "id": it["id"], "kind": "meeting", "sources": srcs, "in_area": in_area,
               "types": list(rec.get("types") or []), "directions_url": directions_url(rec),
               "nearby": None if in_area else {"id": home or (srcs[0] if srcs else "other"),
                                               "label": dict(feeds[home]["region_label"]) if home else
                                               {"en": "Nearby", "es": "Cercanas"}},
               "i18n": {}}
        out.append({k: row.get(k) for k in SITE_KEYS})
    out.sort(key=lambda r: (r["day"], r["time"], fold(r["name"])))

    groups = []
    n_area = sum(1 for r in out if r["in_area"])
    if n_area:
        groups.append({"id": "neta65", "in_area": True, "label": dict(st["area_label"]), "count": n_area})
    near: dict[str, dict] = {}
    for r in out:
        if r["nearby"]:
            g = near.setdefault(r["nearby"]["id"], {"id": r["nearby"]["id"], "in_area": False,
                                                    "label": dict(r["nearby"]["label"]), "count": 0})
            g["count"] += 1
    groups += sorted(near.values(), key=lambda g: order.get(g["id"], 99))

    health = {f.get("id"): f for f in env.get("feeds") or [] if isinstance(f, dict)}
    sources = []
    for fid, f in feeds.items():
        h = health.get(fid) or {}
        sources.append({"id": fid, "name": f["name"], "url": f["url"], "in_area": f["in_area"],
                        "area_label": dict(f["region_label"]), "ok": h.get("ok") if h else None,
                        "updated": h.get("updated"), "count": sum(1 for r in out if fid in r["sources"]),
                        "error": redact(h["error"])[:200] if h.get("error") else None})
    raw_labels = env.get("type_labels") if isinstance(env.get("type_labels"), dict) else {}
    codes = sorted({c for r in out for c in r["types"]}, key=str.upper)
    labels = {c: dict(TYPE_LABELS.get(c) or raw_labels.get(c) or {"en": c, "es": c}) for c in codes}
    return {**empty_site(env.get("updated"), st["type"]), "sources": sources, "groups": groups, "items": out,
            "type_labels": labels}


def http_fetch(http: PoliteSession) -> Fetch:
    """fetch(url, params) for collect(): the text of a 200 answer and the final address (redacted), or
    FeedError with a plain, redacted reason. The key travels in `params`, so PoliteSession's own log
    lines (which print the address without them) never show it."""
    def fetch(url: str, params: dict | None) -> tuple[str, str]:
        # robots.txt is asked about the address that is really requested, with its query (never logged)
        full = requests.Request("GET", url, params=params or None).prepare().url
        if not http.allowed(full):
            raise FeedError(f"{_bare_host(url)}'s robots.txt does not allow reading {urlsplit(url).path}")
        keyed = bool(params and params.get("key"))
        if keyed:
            # A key is only sent to its own site: redirects are followed by hand, on the same host only.
            r = http.get(url, params=params, allow_redirects=False)
            for _hop in range(3):
                if r is None or not getattr(r, "is_redirect", False):
                    break
                loc = urljoin(r.url or full, r.headers.get("Location") or "")
                if _bare_host(loc) != _bare_host(url) or not http.allowed(loc):
                    raise FeedError(f"{_bare_host(url)} redirected the list to another site — the key was not sent")
                r = http.get(loc, allow_redirects=False)      # the Location carries its own query
            else:
                if r is not None and getattr(r, "is_redirect", False):
                    raise FeedError(f"{_bare_host(url)} redirected the list too many times")
        else:
            r = http.get(url, params=params)
        if r is None:
            raise FeedError(f"{_bare_host(url)} did not answer")
        if r.status_code in (401, 403):
            if keyed:
                raise KeyRejected(f"{_bare_host(url)} refused the request (HTTP {r.status_code}) — the key was "
                                  "not accepted")
            raise FeedError(f"{_bare_host(url)} refused the request (HTTP {r.status_code})")
        if r.status_code != 200:
            raise FeedError(f"{_bare_host(url)} answered HTTP {r.status_code}")
        if len(r.content) > MAX_BYTES:
            raise FeedError(f"{_bare_host(url)} sent more than {MAX_BYTES // 1_000_000} MB")
        return r.content.decode("utf-8", errors="replace"), redact(r.url)
    return fetch


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print what was found; do not write data/raw")
    ap.add_argument("--obfuscate", metavar="URL",
                    help="print the feed_obf value of a feed address (for config/site.yml) and stop")
    args = ap.parse_args(argv)
    if args.obfuscate:
        print(obfuscate(args.obfuscate))
        return
    prev = load_raw(SOURCE)
    http = PoliteSession(min_delay=2.0, respect_robots=True, timeout=TIMEOUT, retries=2)
    res = collect(http_fetch(http), prev)
    stats = {**res["stats"], "requests": http.requests_made}
    if res["warnings"]:
        stats["warnings"] = [redact(w)[:200] for w in res["warnings"]]
    if args.dry_run:
        print(redact(json.dumps({"feeds": res["feeds"], "stats": stats, "type_labels": res["type_labels"],
                                 "items": res["items"]}, ensure_ascii=False, indent=1)))
        return
    new_items = [to_item(r) for r in res["items"]]
    merged, _ = merge_items(prev.get("items") or [], new_items, drop_missing=True, authoritative=True)
    err = redact("; ".join(res["errors"]))[:300] if not res["ok"] else None
    save_raw(SOURCE, merged, ok=res["ok"], error=err, stats=stats,
             extra={"feeds": res["feeds"], "type_labels": res["type_labels"]})
    log.info("meetings: %d Grapevine meeting(s) (%d in our Area, %d nearby) from %d of %d list(s), %d request(s)%s",
             len(new_items), stats["in_area"], stats["nearby"], stats["offices_ok"], stats["offices"],
             stats["requests"], f" — problems: {redact(res['errors'])}" if res["errors"] else "")


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
