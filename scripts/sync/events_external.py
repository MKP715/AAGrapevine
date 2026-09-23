"""Texas + online Spanish-language events from the Grapevine / La Viña event calendars
→ data/raw/events_external.json

Where the events come from
--------------------------
AA Grapevine, Inc. runs one event calendar for both sites. Every event has a public page whose URL
carries its start date:
    https://www.aagrapevine.org/get-involved/events/2026-11-20/37th-mchenrys-soberfest
    https://www.aalavina.org/get-involved/events/2026-09-24/taller-mensual
All of them (~3,000, back to 2011) are listed in https://www.aagrapevine.org/sitemap.xml (an index of
sitemap.xml?page=1, ?page=2). Each event page has JSON-LD (name, startDate, endDate, location) plus
Drupal fields for the date range, location text, website URL and a free-text description.

What this module does (politely — 5 s between requests, see common.shared_session)
-----------------------------------------------------------------------------------
1. Read the sitemap index; re-download a sitemap page only when its <lastmod> changed. If the
   sitemap is unavailable, fall back to the two calendar pages (their embedded FullCalendar JSON).
   On such a partial day, upcoming events already known from the cache stay listed — only a
   complete sitemap can tell that an event was removed.
2. Keep event URLs dated from today-2 days to +18 months.
3. Fetch each event page we have never seen (cap --max-fetch, default 60/run, time-boxed) and cache
   the parsed result per URL in the envelope (`cache`), so a page is fetched once; upcoming events
   are re-checked every 14 days (a few per run) to catch changes/cancellations.
4. Output only events that are
     * in Texas (state TX/Texas/Tejas, or an unambiguous Texas city)           → extra.scope "texas"
     * or online/virtual AND Spanish-language (La Viña calendar or Spanish text) → extra.scope "online"
   Past events are dropped from the output (the cache remembers them until they age out).

Items: kind "event", source "calendar", category "gv-calendar" | "lv-calendar" (by site), id
"ev:gvcal:<hash>" / "ev:lvcal:<hash>". extra = start, end, all_day, location, city, state, country,
online_url, website, organizer, flyer_url, flyer_thumb, scope, platform, site.
All-day events use dates ("2026-11-20"; `end` is the inclusive last day, null for one-day events);
timed events use UTC datetimes.

PRIVACY: event descriptions often hold personal names/phones/e-mails of contacts. We keep only a
short description with e-mails, phone numbers and URLs removed; people can click through to the
official event page for contact details.

Run:  python -m scripts.sync.events_external [--max-fetch 60] [--max-seconds 480] [--dry-run]
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .common import (clean_text, date_from_text, detect_lang, get_logger, load_config, load_raw, make_item, merge_items,
                     now_iso, run_module, save_raw, shared_session, short_hash, to_iso, truncate)

SOURCE = "events_external"
log = get_logger(SOURCE)

EVENT_URL_RE = re.compile(
    r"^https?://(?:www\.)?(aagrapevine|aalavina)\.org/get-involved/events/(\d{4}-\d{2}-\d{2})/([^/?#\s]+)/?$", re.I)
WINDOW_PAST_DAYS = 2
WINDOW_FUTURE_DAYS = 548            # ~18 months
REFRESH_DAYS = 14                   # re-check a cached upcoming event this often
REFRESH_PER_RUN = 10
MAX_FAIL_TRIES = 5
CACHE_KEEP_PAST_DAYS = 7            # forget cache entries for events older than this

# --------------------------------------------------------------------------- geography
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico",
}
_STATE_ALIASES = {
    "tejas": "TX", "tex": "TX", "tex.": "TX", "nuevo mexico": "NM", "nueva york": "NY", "nueva jersey": "NJ",
    "carolina del norte": "NC", "carolina del sur": "SC", "dakota del norte": "ND", "dakota del sur": "SD",
    "luisiana": "LA", "pensilvania": "PA", "misuri": "MO", "misisipi": "MS", "hawai": "HI",
    "virginia occidental": "WV", "oregon": "OR", "michigan": "MI", "washington dc": "DC", "d.c.": "DC",
}
CA_PROVINCES = {"british columbia", "bc", "alberta", "ab", "saskatchewan", "manitoba", "ontario", "on", "quebec",
                "qc", "nova scotia", "new brunswick", "prince edward island", "newfoundland", "yukon",
                "northwest territories", "nunavut", "colombia britanica"}
COUNTRIES = {
    "usa", "united states", "estados unidos", "eeuu", "ee.uu.", "canada", "mexico", "guatemala", "el salvador",
    "honduras", "nicaragua", "costa rica", "panama", "colombia", "venezuela", "ecuador", "peru", "bolivia",
    "chile", "argentina", "uruguay", "paraguay", "brasil", "brazil", "cuba", "republica dominicana",
    "dominican republic", "puerto rico", "espana", "spain", "portugal", "france", "francia", "italy", "italia",
    "germany", "alemania", "ireland", "irlanda", "england", "inglaterra", "scotland", "wales", "united kingdom",
    "uk", "reino unido", "australia", "new zealand", "nueva zelanda", "japan", "japon", "thailand", "tailandia",
    "india", "philippines", "filipinas", "south africa", "israel", "netherlands", "belgium", "sweden", "norway",
    "denmark", "finland", "iceland", "poland", "russia", "china", "korea", "singapore", "bahamas", "jamaica",
    "bermuda", "belize", "cdmx", "ciudad de mexico",
}
MX_STATES = {"chihuahua", "coahuila", "nuevo leon", "tamaulipas", "sonora", "baja california", "jalisco",
             "michoacan", "guanajuato", "queretaro", "puebla", "veracruz", "oaxaca", "guerrero", "morelos",
             "hidalgo", "zacatecas", "durango", "sinaloa", "nayarit", "yucatan", "quintana roo", "chiapas",
             "tabasco", "campeche", "san luis potosi", "aguascalientes", "colima", "tlaxcala", "estado de mexico"}
# Texas cities that are safe to recognize without a state (ambiguous names like Paris, Athens,
# Arlington, Jacksonville, Pasadena, Huntsville, Lancaster, Palestine, Marshall are NOT here).
TX_CITIES = {
    "dallas", "fort worth", "ft worth", "ft. worth", "houston", "san antonio", "el paso", "corpus christi",
    "laredo", "lubbock", "amarillo", "brownsville", "mcallen", "killeen", "waco", "beaumont", "abilene",
    "odessa", "midland", "round rock", "college station", "galveston", "harlingen", "edinburg", "pharr",
    "san marcos", "new braunfels", "temple", "pearland", "sugar land", "conroe", "the woodlands", "katy",
    "league city", "baytown", "wichita falls", "san angelo", "cedar park", "pflugerville", "del rio",
    "eagle pass", "weslaco", "port arthur", "texas city", "south padre island", "kerrville", "fredericksburg",
    "granbury", "weatherford", "burleson", "mansfield", "cleburne", "stephenville", "plano", "irving",
    "grand prairie", "mesquite", "mckinney", "frisco", "denton", "lewisville", "richardson", "rowlett",
    "rockwall", "wylie", "sachse", "garland", "carrollton", "duncanville", "desoto", "cedar hill",
    "lancaster tx", "grapevine tx", "southlake", "keller", "euless", "bedford", "hurst", "north richland hills",
    "haltom city", "coppell", "flower mound", "the colony", "little elm", "prosper", "celina", "midlothian",
    "waxahachie", "ennis", "corsicana", "terrell", "forney", "kaufman", "royse city", "greenville",
    "sulphur springs", "mount pleasant", "mt pleasant", "texarkana", "tyler", "longview", "kilgore",
    "nacogdoches", "lufkin", "sherman", "denison", "bonham", "gainesville", "mineola", "lindale",
    "whitehouse", "gladewater", "gilmer", "daingerfield", "pittsburg tx", "jefferson tx", "canton tx",
    "athens tx", "palestine tx", "paris tx", "marshall tx", "henderson tx", "jacksonville tx", "huntsville tx",
    "arlington tx", "pasadena tx", "addison tx", "allen", "balch springs", "seagoville", "red oak",
    "heath", "fate", "quinlan", "emory", "commerce tx", "farmersville", "princeton tx", "anna tx",
    "van alstyne", "howe", "whitesboro", "decatur tx", "bridgeport tx", "mineral wells", "brownwood",
    "big spring", "alice", "kingsville", "victoria tx", "bay city", "lake jackson", "angleton", "friendswood",
    "missouri city", "richmond tx", "rosenberg", "spring tx", "humble", "kingwood", "tomball", "cypress tx",
    "copperas cove", "harker heights", "belton", "georgetown tx", "leander", "kyle", "buda", "lockhart",
    "seguin", "boerne", "uvalde", "hondo", "rio grande city", "mission tx", "san benito", "mercedes tx",
}
# Place names that are also everyday words/surnames ("Sierra Nevada", "Juan Guerrero", "montaña"):
# never treat them alone as proof that an event is outside Texas.
_COMMON_WORDS = {"montana", "nevada", "florida", "colorado", "guerrero", "hidalgo", "morelos", "victoria",
                 "georgia", "virginia", "india", "china", "chile", "jamaica", "israel", "washington", "maine",
                 "indiana", "uk", "korea", "cuba", "panama", "peru", "durango", "colima", "sonora"}
TEXAS_RE = re.compile(r"(?i)\b(?:texas|tejas)\b|\bTX\b|\bTex\.")
ONLINE_WORD_RE = re.compile(
    r"(?i)\b(zoom|virtual|online|on-line|en\s+l[ií]nea|google\s+meet|meet\.google|microsoft\s+teams|webex|"
    r"skype|facebook\s+live|youtube\s+live|livestream|webinar|teleconferencia|videollamada)\b")
PLATFORM_HOSTS = {"zoom.us": "Zoom", "meet.google.com": "Google Meet", "teams.microsoft.com": "Microsoft Teams",
                  "teams.live.com": "Microsoft Teams", "webex.com": "Webex", "gotomeeting.com": "GoToMeeting",
                  "skype.com": "Skype", "whereby.com": "Whereby", "jitsi": "Jitsi"}
URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\[email(?:&#160;|\s| )?protected\]", re.I)
PHONE_RE = re.compile(r"(?<![\w/])\+?\(?\d[\d\s().-]{6,}\d(?![\w/])")
ORGANIZER_RE = re.compile(
    r"(?i)\b(?:hosted by|host(?:ed)? by|organized by|presented by|sponsored by|organizad[oa] por|"
    r"auspiciad[oa] por|patrocinad[oa] por|anfitri[oó]n(?:es)?\s*:)\s*:?\s*([^.\n|;]{3,90})")
GENERIC_IMAGE_RE = re.compile(r"(?i)aa-logo-clipart|Grapevine_Logo|LaVina_Logo")
# Link labels that event submitters paste into descriptions.
BOILERPLATE_RE = re.compile(
    r"(?i)\bback to (?:the )?events? calendar\b|\bclick here\b|\bhaga clic aqu[ií]\b|\bclic aqu[ií]\b|"
    r"\bregresar al calendario\b|\bmore info(?:rmation)? here\b|\bm[aá]s informaci[oó]n aqu[ií]\b|\bread more\b")


# --------------------------------------------------------------------------- small helpers
def _fold(s: str) -> str:
    """lower-case, accents removed, single spaces — for matching place names."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.\s]", " ", s.lower())).strip()


def _today() -> date:
    tz = load_config().get("site", {}).get("timezone", "America/Chicago")
    return datetime.now(ZoneInfo(tz)).date()


def norm_event_url(u: str) -> str | None:
    """Canonical https://www.<site>.org/get-involved/events/<date>/<slug> or None."""
    u = htmllib.unescape((u or "").strip())
    m = EVENT_URL_RE.match(u)
    if not m:
        return None
    return f"https://www.{m[1].lower()}.org/get-involved/events/{m[2]}/{m[3]}"


def url_date(u: str) -> date | None:
    m = EVENT_URL_RE.match(u)
    try:
        return date.fromisoformat(m[2]) if m else None
    except ValueError:
        return None


def site_of(u: str) -> str:
    return "lavina" if "aalavina.org" in u else "grapevine"


def _parse_ld_dt(s: str | None) -> datetime | None:
    """'2026-11-20T06:00:00-0600' → aware datetime."""
    if not s:
        return None
    s = s.strip()
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("America/Chicago"))


def platform_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    for h, name in PLATFORM_HOSTS.items():
        if h in host:
            return name
    return None


def _scrub(text: str) -> str:
    """Remove e-mails, URLs, phone numbers (and the labels that introduced them) and markdown stars."""
    t = EMAIL_RE.sub(" ", text or "")
    t = URL_IN_TEXT_RE.sub(" ", t)
    t = PHONE_RE.sub(" ", t)
    t = re.sub(r"(?i)\b(?:tel|tel[ée]fono|phone|cel|cell|email|e-mail|correo|contact(?:o)?|whatsapp)\s*[:.]?\s*(?=[\s,;|]|$)",
               " ", t)
    t = BOILERPLATE_RE.sub(" ", t)
    t = re.sub(r"[*_]{2,}|(?<!\w)\*|\*(?!\w)", " ", t)
    t = re.sub(r"\s*[|•·]\s*", " · ", t)
    return clean_text(re.sub(r"(\s*·\s*){2,}", " · ", t)).strip(" ·,;:-")


def safe_summary(text: str) -> str:
    """Description without e-mails, phone numbers or URLs; empty if little is left."""
    t = _scrub(text)
    if len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", t)) < 5:   # e.g. just a contact's name — not a description
        return ""
    return truncate(t, 300)


# --------------------------------------------------------------------------- location
def parse_location(loc: str, title: str = "", website: str | None = None) -> dict:
    """→ {display, city, state, country, online_url, platform}. `state` is a 2-letter US code."""
    loc = clean_text(re.sub(r"[*_]{1,3}", " ", loc or ""))      # "*Zoom ID: …*" markdown leftovers
    out: dict = {"display": loc or None, "city": None, "state": None, "country": None,
                 "online_url": None, "platform": None}
    urls = URL_IN_TEXT_RE.findall(loc)
    for u in urls + ([website] if website else []):
        if platform_of(u):
            out["online_url"] = u.rstrip(".,;")
            out["platform"] = platform_of(u)
            break
    if urls and not URL_IN_TEXT_RE.sub("", loc).strip(" ,;*"):
        # the location is only a link (typical for Zoom events)
        out["display"] = out["platform"] or "Online"
        if not out["online_url"]:
            out["online_url"] = urls[0]
        return out
    if not out["platform"] and re.search(r"(?i)\bzoom\b", loc):
        out["platform"] = "Zoom"
    text = URL_IN_TEXT_RE.sub(" ", loc).strip()
    # "Dallas TX 75247" (no comma) → "Dallas, TX 75247"
    text = re.sub(r"(?<=[a-z.])\s+([A-Z]{2})(\s+\d{5}(?:-\d{4})?)?$", r", \1\2", text)
    parts = [p.strip(" *") for p in re.split(r"[,\n]| - ", text) if p.strip(" *")]
    state_idx = None
    for i in range(len(parts) - 1, -1, -1):
        raw = parts[i]
        f = _fold(re.sub(r"\b\d{5}(?:-\d{4})?\b", "", raw))
        m = re.fullmatch(r"([A-Za-z]{2})\.?", re.sub(r"\s*\d{5}(?:-\d{4})?\s*$", "", raw).strip())
        if m and m[1].upper() in US_STATES and (m[1].isupper() or m[1].upper() == "TX"):
            out["state"], state_idx = m[1].upper(), i
            break
        code = next((k for k, v in US_STATES.items() if _fold(v) == f), None) or _STATE_ALIASES.get(f)
        if code:
            out["state"], state_idx = code, i
            break
        if f in COUNTRIES or f in CA_PROVINCES or f in MX_STATES:
            if f in ("usa", "united states", "estados unidos", "eeuu", "ee.uu."):
                out["country"] = "USA"
                continue           # keep looking for the state before the country
            out["country"] = "Mexico" if f in MX_STATES else "Canada" if f in CA_PROVINCES else raw.strip()
            if f in MX_STATES or f in CA_PROVINCES:
                out["region"] = raw.strip()
            elif i > 0 and _fold(parts[i - 1]) in (CA_PROVINCES | MX_STATES):
                out["region"] = parts[i - 1]     # "Powell River, British Columbia, Canada"
                i -= 1
            state_idx = i          # the city (if any) sits just before it
            break
    if out["state"]:
        out["country"] = "USA"
    # City: the part just before the state, if it looks like a place (no street numbers).
    if state_idx is not None and state_idx > 0:
        cand = parts[state_idx - 1]
        if not re.search(r"\d", cand) and len(cand.split()) <= 4:
            out["city"] = cand
        else:
            fc = _fold(cand)
            for c in sorted(TX_CITIES, key=len, reverse=True):
                c0 = re.sub(r" tx$", "", c)
                if fc.endswith(" " + c0) or fc == c0:
                    out["city"] = c0.title()
                    break
    elif len(parts) == 1 and not re.search(r"\d", parts[0]) and len(parts[0].split()) <= 4 \
            and not out["country"] and not ONLINE_WORD_RE.search(parts[0]):
        out["city"] = parts[0]
    return out


def texas_decision(loc: dict, title: str) -> bool:
    """True when the event is in Texas. A parsed state wins; then explicit 'Texas'/'Tejas'/'TX' in the
    location or title; any other state/country mentioned → False; finally an unambiguous Texas city."""
    if loc.get("state"):
        return loc["state"] == "TX"
    hay = f"{loc.get('display') or ''} | {title}"
    if TEXAS_RE.search(hay):
        return True
    fh = f" {_fold(hay)} "
    others = [v for k, v in US_STATES.items() if k != "TX"] + list(_STATE_ALIASES)
    if loc.get("country") and _fold(loc["country"]) not in ("usa",):
        return False
    for name in others + list(MX_STATES) + [c for c in COUNTRIES if c not in ("usa", "united states")]:
        n = _fold(name)
        if n and n not in ("tex", "tejas") and n not in _COMMON_WORDS and f" {n} " in fh:
            return False
    city = _fold(loc.get("city") or "")
    return bool(city) and city in TX_CITIES      # ambiguous names (Paris, Athens…) aren't in the list


# --------------------------------------------------------------------------- event page parsing
def _ld_event(soup: BeautifulSoup) -> dict:
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or sc.get_text() or "{}")
        except Exception:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data if isinstance(data, list) else []
        for n in nodes:
            if isinstance(n, dict) and "Event" in str(n.get("@type", "")):
                return n
    return {}


def _field(soup: BeautifulSoup, name: str):
    return soup.select_one(f".field--name-{name} .field__item") or soup.select_one(f".field--name-{name}")


def parse_event(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    ld = _ld_event(soup)
    ev: dict = {}
    title = clean_text(ld.get("name") or "")
    if not title:
        h1 = soup.find("h1")
        og = soup.find("meta", attrs={"property": "og:title"})
        title = clean_text(h1.get_text(" ") if h1 else (og.get("content") if og else ""))
        if not title and soup.title:
            title = clean_text(soup.title.get_text()).split(" | ")[0]
    ev["title"] = title

    # dates -------------------------------------------------------------
    sd, ed = _parse_ld_dt(ld.get("startDate")), _parse_ld_dt(ld.get("endDate"))
    date_el = _field(soup, "field-daterange")
    date_text = clean_text(date_el.get_text(" ")) if date_el else ""
    ev["date_text"] = date_text or None
    has_clock = bool(re.search(r"\d{1,2}:\d{2}|\b\d{1,2}\s*[ap]\.?m\b", date_text, re.I))
    if sd:
        # Drupal stores date-only ranges at 12:00 UTC; anything else is a real time.
        all_day = sd.astimezone(timezone.utc).strftime("%H:%M") == "12:00" and not has_clock
        if all_day:
            ev["start"] = sd.date().isoformat()
            if ed and ed.date() > sd.date():
                ev["end"] = ed.date().isoformat()
        else:
            ev["start"] = to_iso(sd)
            if ed and ed > sd:
                ev["end"] = to_iso(ed)
        ev["all_day"] = all_day
    else:
        pieces = re.split(r"\s+[-–]\s+", date_text) if date_text else []
        s_iso = date_from_text(pieces[0])[0] if pieces else None
        e_iso = date_from_text(pieces[-1])[0] if len(pieces) > 1 else None
        d0 = url_date(url)
        ev["start"] = s_iso or (d0.isoformat() if d0 else None)
        if e_iso and ev["start"] and e_iso > ev["start"]:
            ev["end"] = e_iso
        ev["all_day"] = True

    # location / links --------------------------------------------------
    loc_el = _field(soup, "field-event-location")
    loc_text = clean_text(loc_el.get_text(" ")) if loc_el else ""
    if not loc_text:
        ln = (ld.get("location") or {}).get("name") if isinstance(ld.get("location"), dict) else None
        loc_text = ", ".join(ln) if isinstance(ln, list) else clean_text(ln or "")
    web_el = _field(soup, "field-website-url")
    website = None
    if web_el is not None:
        a = web_el.find("a", href=True) if hasattr(web_el, "find") else None
        website = (a["href"] if a else clean_text(web_el.get_text(" "))) or None
        if website and not website.lower().startswith("http"):
            website = None
    ev["location_raw"] = loc_text or None
    ev["website"] = website
    body_el = _field(soup, "body")
    body = ""
    if body_el is not None:
        for br in body_el.find_all("br"):
            br.replace_with("\n")
        body = clean_text(body_el.get_text(" "))
    if not body:
        body = clean_text(ld.get("description") or "")
    # online links sometimes only appear in the description
    if not website:
        for u in URL_IN_TEXT_RE.findall(body):
            if platform_of(u):
                website = u
                break
    ev["summary"] = safe_summary(body)
    om = ORGANIZER_RE.search(body)
    if om:
        org = _scrub(om[1])
        if len(org) >= 3:
            ev["organizer"] = truncate(org, 90)
    img = soup.select_one(".image-region img[src], .field--name-field-image img[src]")
    if img is not None:
        src = urljoin(url, img["src"])
        if not GENERIC_IMAGE_RE.search(src):
            ev["image"] = src
    ev["lang"] = detect_lang(f"{title} {ev['summary']}", prior="es" if "aalavina.org" in url else "en")
    return decide(ev)


def decide(ev: dict) -> dict:
    """(Re)compute location fields + Texas/online flags from the raw page fields kept in the cache.
    Run on every build, so improvements to these rules apply to cached events without refetching."""
    title, loc_text, website = ev.get("title") or "", ev.get("location_raw") or "", ev.get("website")
    loc = parse_location(loc_text, title, website)
    ev = dict(ev)
    ev.update({"location": loc["display"], "city": loc["city"], "state": loc["state"], "country": loc["country"],
               "region": loc.get("region"),
               "online_url": loc["online_url"] or (website if platform_of(website) else None),
               "platform": loc["platform"] or platform_of(website)})
    ev["online"] = bool(ev["online_url"] or ONLINE_WORD_RE.search(f"{loc_text} {title}"))
    ev["texas"] = texas_decision(loc, title)
    return {k: v for k, v in ev.items() if v not in (None, "")}


# --------------------------------------------------------------------------- discovery
def _sitemap_entries(xml: str) -> tuple[str, list[tuple[str, str | None]]]:
    """→ ('index'|'urlset', [(loc, lastmod)])."""
    kind = "index" if re.search(r"<sitemapindex\b", xml) else "urlset"
    tag = "sitemap" if kind == "index" else "url"
    out = []
    for block in re.findall(rf"<{tag}\b.*?</{tag}>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S)
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S)
        if loc:
            out.append((htmllib.unescape(loc[1]), lm[1] if lm else None))
    return kind, out


def discover_from_sitemap(http, base: str, sm_state: dict, keep_from: date) -> tuple[set[str] | None, str | None]:
    """Event URLs (dated >= keep_from) from the sitemap. Returns (urls, error) — urls None on failure."""
    idx_url = base.rstrip("/") + "/sitemap.xml"
    xml = http.get_text(idx_url)
    if not xml or not re.search(r"<(?:sitemapindex|urlset)\b", xml):
        return None, f"sitemap unavailable or not XML ({idx_url})"
    kind, entries = _sitemap_entries(xml)
    pages = entries if kind == "index" else [(idx_url, None)]
    if not pages:
        return None, "sitemap index lists no pages"
    urls: set[str] = set()
    failed = 0
    events_total = 0          # all event URLs (any date) — 0 means something is wrong, not "no events"
    pages_state = sm_state.setdefault("pages", {})
    for loc, lastmod in pages[:20]:
        cached = pages_state.get(loc)
        if kind == "index" and cached and lastmod and cached.get("lastmod") == lastmod:
            urls |= {u for u in cached.get("events", []) if (url_date(u) or date.min) >= keep_from}
            events_total += cached.get("events_total", len(cached.get("events", [])))
            continue
        body = xml if kind == "urlset" else http.get_text(loc)
        if not body or not re.search(r"<urlset\b", body):
            failed += 1
            if cached:  # use yesterday's list for this page
                urls |= {u for u in cached.get("events", []) if (url_date(u) or date.min) >= keep_from}
                events_total += cached.get("events_total", 0)
            continue
        _, locs = _sitemap_entries(body)
        all_events = [n for n in (norm_event_url(u) for u, _ in locs) if n]
        evs = sorted({n for n in all_events if (url_date(n) or date.min) >= keep_from})
        pages_state[loc] = {"lastmod": lastmod, "events": evs, "fetched": now_iso(), "urls_total": len(locs),
                            "events_total": len(all_events)}
        events_total += len(all_events)
        urls |= set(evs)
    # forget pages that are no longer in the index
    for k in list(pages_state):
        if k not in {p for p, _ in pages}:
            pages_state.pop(k, None)
    if events_total == 0:
        # The calendar has thousands of (past) events; an empty list means a broken sitemap.
        return None, "sitemap has no event URLs" + (f" ({failed} page(s) failed)" if failed else "")
    return urls, (f"{failed} sitemap page(s) failed" if failed else None)


def discover_from_calendars(http, keep_from: date) -> tuple[set[str] | None, str | None]:
    """Fallback: the FullCalendar JSON embedded in the two calendar pages."""
    cfg = load_config().get("sources", {}) or {}
    pages = [(cfg.get("grapevine", {}).get("base") or "https://www.aagrapevine.org") + "/get-involved/calendar",
             (cfg.get("lavina", {}).get("base") or "https://www.aalavina.org") + "/calendario-de-eventos"]
    urls: set[str] = set()
    ok = 0
    for page in pages:
        html = http.get_text(page)
        if not html:
            continue
        m = re.search(r'data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            continue
        try:
            settings = json.loads(m[1])
            for view in settings.get("fullCalendarView", []) or []:
                opts = json.loads(view.get("calendar_options") or "{}")
                for e in opts.get("events", []) or []:
                    n = norm_event_url(urljoin(page, e.get("url", "")))
                    if n and (url_date(n) or date.min) >= keep_from:
                        urls.add(n)
            ok += 1
        except Exception as e:
            log.warning("calendar JSON parse failed on %s: %s", page, e)
    return (urls if ok else None), (None if ok else "calendar pages unavailable")


# --------------------------------------------------------------------------- items
def build_item(url: str, ev: dict) -> dict:
    site = site_of(url)
    scope = "texas" if ev.get("texas") else "online"
    start = ev.get("start")
    extra = {
        "start": start,
        "end": ev.get("end"),
        "all_day": ev.get("all_day", True),
        "location": ev.get("location"),
        "city": ev.get("city"),
        "state": ev.get("state"),
        "country": ev.get("country"),
        "region": ev.get("region"),
        "online_url": ev.get("online_url"),
        "platform": ev.get("platform"),
        "website": ev.get("website"),
        "organizer": ev.get("organizer"),
        "flyer_url": ev.get("image"),
        "flyer_thumb": None,
        "scope": scope,
        "online": bool(ev.get("online")),
        "site": site,
        "date_text": ev.get("date_text"),
    }
    return make_item(
        id=f"ev:{'lvcal' if site == 'lavina' else 'gvcal'}:{short_hash(urlparse(url).path, 12)}",
        source="calendar", kind="event", url=url, title=ev.get("title") or "Event",
        summary=ev.get("summary") or "", lang=ev.get("lang") or ("es" if site == "lavina" else "en"),
        date=start, image=ev.get("image"), category="lv-calendar" if site == "lavina" else "gv-calendar",
        tags=[scope], extra=extra,
    )


def relevant(url: str, ev: dict) -> bool:
    """Texas events (any language) + online events in Spanish (La Viña calendar or Spanish text)."""
    if ev.get("texas"):
        return True
    return bool(ev.get("online")) and (site_of(url) == "lavina" or ev.get("lang") == "es")


def not_past(ev: dict, today: date) -> bool:
    last = ev.get("end") or ev.get("start")
    if not last:
        return False
    try:
        if "T" in last:
            d = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Chicago")).date()
        else:
            d = date.fromisoformat(last[:10])
    except ValueError:
        return False
    return d >= today


def dedupe(items: list[dict]) -> list[dict]:
    """The same event is sometimes posted twice (e.g. once per site, or re-submitted). Same start date
    + very similar title → keep the entry with the most information."""
    def key_title(t: str) -> str:
        return re.sub(r"\b(the|de|del|la|el|los|las|aa|of|and|y)\b", " ", _fold(t)).strip()

    def richness(i: dict) -> int:
        return sum(1 for v in (i.get("extra") or {}).values() if v not in (None, "", False)) + len(i.get("summary") or "") // 50

    kept: list[dict] = []
    for it in sorted(items, key=richness, reverse=True):
        s = str((it.get("extra") or {}).get("start") or "")[:10]
        kt = key_title(it.get("title") or "")
        dup = next((k for k in kept if str(k["extra"].get("start") or "")[:10] == s
                    and SequenceMatcher(None, kt, key_title(k.get("title") or "")).ratio() >= 0.85), None)
        if dup is None:
            kept.append(it)
    return kept


# --------------------------------------------------------------------------- main
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Texas / online-Spanish events from the GV & LV calendars")
    ap.add_argument("--max-fetch", type=int, default=60, help="max event pages to fetch this run (default 60)")
    ap.add_argument("--max-seconds", type=int, default=480, help="stop fetching after N seconds (default 480)")
    ap.add_argument("--dry-run", action="store_true", help="do not write data/raw/events_external.json")
    args = ap.parse_args(argv)

    prev = load_raw(SOURCE)
    cache: dict = dict(prev.get("cache") or {})
    sm_state: dict = dict(prev.get("sitemap") or {})
    try:
        _run(args, prev, cache, sm_state)
    except Exception as e:
        # Never lose the per-URL cache because of a surprise: save yesterday's items (minus past ones).
        log.exception("events_external failed")
        today = _today()
        items = [i for i in prev.get("items", []) if not_past(i.get("extra") or {}, today)]
        if not args.dry_run:
            save_raw(SOURCE, items, ok=False, error=f"{type(e).__name__}: {e}"[:300], stats=prev.get("stats"),
                     extra={"cache": cache, "sitemap": sm_state})


def _run(args, prev: dict, cache: dict, sm_state: dict) -> None:
    t0 = time.monotonic()
    http = shared_session()
    today = _today()
    win_from = today - timedelta(days=WINDOW_PAST_DAYS)
    win_to = today + timedelta(days=WINDOW_FUTURE_DAYS)
    cfg = load_config().get("sources", {}) or {}
    base = (cfg.get("grapevine", {}).get("base") or "https://www.aagrapevine.org")
    errors: list[str] = []

    # ---- 1. discover
    urls, err = discover_from_sitemap(http, base, sm_state, today - timedelta(days=CACHE_KEEP_PAST_DAYS))
    discovery = "sitemap"
    # A sitemap read without errors lists every event → an event missing from it was removed.
    discovery_complete = urls is not None and not err
    if urls is None:
        errors.append(err or "sitemap failed")
        urls, err2 = discover_from_calendars(http, today - timedelta(days=CACHE_KEEP_PAST_DAYS))
        discovery = "calendar"
        if urls is None:
            errors.append(err2 or "calendar failed")
    elif err:
        log.warning(err)
    if urls is None:
        # Nothing discovered today: keep yesterday's upcoming items, keep the cache.
        items = [i for i in prev.get("items", []) if not_past(i.get("extra") or {}, today)]
        if not args.dry_run:
            save_raw(SOURCE, items, ok=False, error="; ".join(errors), stats=prev.get("stats"),
                     extra={"cache": cache, "sitemap": sm_state})
        log.warning("no discovery source worked: %s", errors)
        return
    candidates = sorted((u for u in urls if win_from <= (url_date(u) or date.min) <= win_to), key=lambda u: (url_date(u), u))
    log.info("%d event URLs dated %s … %s (discovery: %s)", len(candidates), win_from, win_to, discovery)

    # ---- 2. fetch new pages first (soonest first), then a few stale refreshes
    now = datetime.now(timezone.utc)

    def stale(entry: dict) -> bool:
        try:
            return (now - datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))).days >= REFRESH_DAYS
        except Exception:
            return True

    new_urls = [u for u in candidates if u not in cache or
                (not cache[u].get("ev") and not cache[u].get("gone") and cache[u].get("fails", 0) < MAX_FAIL_TRIES)]
    refresh = [u for u in candidates if u in cache and cache[u].get("ev") and stale(cache[u])][:REFRESH_PER_RUN]
    fetched = refreshed = failed = 0
    for u in new_urls + refresh:
        if fetched + refreshed + failed >= args.max_fetch or time.monotonic() - t0 > args.max_seconds:
            break
        r = http.get(u)
        entry = dict(cache.get(u) or {})
        entry["at"] = now_iso()
        if r is None or r.status_code != 200:
            code = r.status_code if r is not None else None
            if code in (404, 410):
                entry.update(gone=True, http=code)
                entry.pop("ev", None)
            else:
                entry["fails"] = entry.get("fails", 0) + 1
                entry["http"] = code
            cache[u] = entry
            failed += 1
            continue
        try:
            r.encoding = r.encoding or "utf-8"
            ev = parse_event(r.text, u)
        except Exception as e:
            log.warning("parse failed %s: %s", u, e)
            entry["fails"] = entry.get("fails", 0) + 1
            cache[u] = entry
            failed += 1
            continue
        was_known = bool(entry.get("ev"))
        entry.update(ev=ev, http=200, fails=0)
        entry.pop("gone", None)
        cache[u] = entry
        if was_known:
            refreshed += 1
        else:
            fetched += 1
    pending = sum(1 for u in candidates if u not in cache or
                  (not cache[u].get("ev") and not cache[u].get("gone") and cache[u].get("fails", 0) < MAX_FAIL_TRIES))

    # ---- 3. build output
    pool = list(candidates)
    kept_known = 0
    if not discovery_complete:
        # Partial discovery today (calendar fallback, or a sitemap page failed with no saved copy):
        # events we already know stay listed; only past ones and pages confirmed deleted (404/410,
        # cache "gone") drop out. Otherwise they would vanish today and return as "new" tomorrow.
        listed = set(candidates)
        known = sorted(u for u, e in cache.items()
                       if u not in listed and (e or {}).get("ev") and not (e or {}).get("gone")
                       and win_from <= (url_date(u) or date.min) <= win_to)
        pool += known
        kept_known = len(known)
    new_items = []
    for u in pool:
        ev = (cache.get(u) or {}).get("ev")
        if not ev:
            continue
        ev = decide(ev)
        if not not_past(ev, today) or not relevant(u, ev):
            continue
        try:
            new_items.append(build_item(u, ev))
        except Exception as e:
            log.warning("skipping %s: %s", u, e)
    new_items = dedupe(new_items)
    # Events that vanished from the calendar or are now past are dropped (drop_missing); first_seen kept.
    # Items are rebuilt from the per-URL cache every day, so the cache is the whole truth: a value the
    # event page no longer has (e.g. an online link) must disappear too (authoritative).
    merged, added = merge_items(prev.get("items", []), new_items, drop_missing=True, authoritative=True)

    # ---- 4. prune cache: keep entries for events not older than a week
    cutoff = today - timedelta(days=CACHE_KEEP_PAST_DAYS)
    cache = {u: v for u, v in cache.items() if (url_date(u) or date.min) >= cutoff}

    stats = {
        "discovery": discovery,
        "discovery_complete": discovery_complete,
        "upcoming_urls": len(candidates),
        "kept_from_cache": kept_known,
        "fetched": fetched, "refreshed": refreshed, "failed": failed, "pending": pending,
        "cached": len(cache),
        "texas": sum(1 for i in merged if i["extra"].get("scope") == "texas"),
        "online_es": sum(1 for i in merged if i["extra"].get("scope") == "online"),
        "items": len(merged), "new": added, "requests": http.requests_made,
    }
    log.info("events: %s", stats)
    if args.dry_run:
        print(json.dumps({"stats": stats, "items": merged}, ensure_ascii=False, indent=1))
        return
    save_raw(SOURCE, merged, ok=not errors, error="; ".join(errors) if errors else None, stats=stats,
             extra={"cache": cache, "sitemap": sm_state})


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
