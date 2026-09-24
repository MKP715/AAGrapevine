"""Shared helpers for the content-sync pipeline.

Every sync module (drive.py, youtube.py, …) uses:
  * CONFIG / load_config()          – config/site.yml
  * PoliteSession                   – requests with UA, robots.txt, per-server crawl-delay, retries
  * load_raw() / save_raw()         – data/raw/<source>.json envelope (see docs/DATA_SCHEMA.md)
  * merge_items()                   – cumulative merge that preserves first_seen and never drops items
                                      (authoritative=True for sources that are their own full truth)
  * detect_lang(), clean_text(), date helpers
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
import yaml

try:
    from protego import Protego
except Exception:  # pragma: no cover
    Protego = None

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "site.yml"
RAW_DIR = ROOT / "data" / "raw"
SITE_DIR = ROOT / "data" / "site"
STATE_DIR = ROOT / "data" / "state"
TRANSLATIONS_DIR = ROOT / "data" / "translations"
CACHE_ASSETS = ROOT / "src" / "assets" / "cache"  # committed thumbnails (pdf/, ig/)
CONTENT_DIR = ROOT / "content"
MODELS_DIR = Path(os.environ.get("GV_MODELS_DIR", ROOT / ".cache" / "models"))

for _d in (RAW_DIR, SITE_DIR, STATE_DIR, TRANSLATIONS_DIR, CACHE_ASSETS):
    _d.mkdir(parents=True, exist_ok=True)

# Host names served by ONE server. PoliteSession spaces requests per server, so the magazine sites'
# Crawl-delay (5 s) holds across both of them for every module sharing shared_session().
SAME_SERVER_HOSTS = {h: "aagrapevine.org+aalavina.org" for h in (
    "www.aagrapevine.org", "aagrapevine.org", "www.aalavina.org", "aalavina.org")}

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)


# --------------------------------------------------------------------------- logging
def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s %(name)-12s %(levelname)-7s %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(os.environ.get("GV_LOG_LEVEL", "INFO"))
        log.propagate = False
    return log


# --------------------------------------------------------------------------- config
_CONFIG: dict | None = None


def load_config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _CONFIG = yaml.safe_load(f) or {}
    return _CONFIG


# --------------------------------------------------------------------------- time
def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def to_iso(dt: datetime | date | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.isoformat()


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return datetime.fromisoformat(s + "T12:00:00+00:00")
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


MONTHS = {
    # English
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
    "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    # Spanish
    "enero": 1, "ene": 1, "febrero": 2, "marzo": 3, "abril": 4, "abr": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "ago": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12, "dic": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


def date_from_text(text: str) -> tuple[str | None, str]:
    """Find a date inside a file name / title.

    Returns (iso_date_or_None, text_without_the_date). Recognizes:
      2026-10-05, 2026.10.05, 2026_10_05, 20261005, 10-05-2026, 10/05/2026,
      "Oct 5 2026", "October 5, 2026", "5 de octubre de 2026", "March 2026" (→ 2026-03-01).
    """
    t = text
    pats = [
        (r"(?<!\d)(20\d{2})[-._ ](\d{1,2})[-._ ](\d{1,2})(?!\d)", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        (r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        (r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})(?!\d)", lambda m: (int(m[3]), int(m[1]), int(m[2]))),
        (rf"(?i)\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b", lambda m: (int(m[3]), MONTHS[m[1].lower()], int(m[2]))),
        (rf"(?i)\b(\d{{1,2}})\s+(?:de\s+)?({_MONTH_RE})\.?,?\s+(?:de\s+|del\s+)?(20\d{{2}})\b", lambda m: (int(m[3]), MONTHS[m[2].lower()], int(m[1]))),
        (rf"(?i)\b({_MONTH_RE})\.?,?\s+(?:de\s+|del\s+)?(20\d{{2}})\b", lambda m: (int(m[2]), MONTHS[m[1].lower()], 1)),
    ]
    for pat, fn in pats:
        m = re.search(pat, t)
        if not m:
            continue
        try:
            y, mo, d = fn(m)
            dt = date(y, mo, d)
        except (ValueError, KeyError):
            continue
        rest = (t[: m.start()] + " " + t[m.end():]).strip(" -_.,·|")
        return dt.isoformat(), re.sub(r"\s{2,}", " ", rest).strip()
    return None, text


# --------------------------------------------------------------------------- text
_WS = re.compile(r"\s+")


def clean_text(s: Any) -> str:
    if s is None:
        return ""
    s = html.unescape(str(s))
    s = s.replace(" ", " ").replace(" ", " ").replace("​", "")
    return _WS.sub(" ", s).strip()


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return clean_text(s)


def truncate(s: str, n: int = 400) -> str:
    s = clean_text(s)
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:.- ") + "…"


def pretty_filename(name: str) -> str:
    """'GV_Catalog_2026-final (1).pdf' → 'GV Catalog 2026 final'."""
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name)
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)
    base = re.sub(r"[_]+", " ", base)
    base = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", base)
    base = re.sub(r"%20", " ", base)
    base = re.sub(r"\s{2,}", " ", base).strip(" -.")
    return base


def slugify(s: str, maxlen: int = 80) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen] or "item"


def short_hash(s: str, n: int = 12) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


# --------------------------------------------------------------------------- language detection
_ES_WORDS = set("""el la los las de del que y en un una por para con es su sus al lo como más pero le ya o este sí porque
esta entre cuando muy sin sobre también me hasta hay donde quien desde todo nos durante todos uno les ni contra otros ese eso
ante ellos esto mí antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes nada muchos cual poco ella estar
estas algunas algo nosotros mi mis tú te ti tu tus somos soy fue era mujer hombre vida día días año años grupo grupos reunión
reuniones sobriedad sobrio sobria historia historias distrito taller escritura revista servicio comité alcohólicos anónimos
padrino madrina paso pasos tradición tradiciones mensaje viña amor humildad fuerza""".split())
_EN_WORDS = set("""the and of to in is you that it he was for on are as with his they i at be this have from or one had by
but not what all were we when your can said there an each which she do how their if will up other about out many then them
these so some her would make like him into time has look two more write go see my our me sober sobriety meeting meetings story
stories group groups service committee alcoholics anonymous sponsor step steps tradition traditions message love life day
days year years new free book books podcast episode season""".split())
_FR_WORDS = set("""le les des est et une pour dans avec sur pas qui ce ne au du vous nous leur sont été être vigne
réunion sobriété mouvement groupe histoire""".split())


def detect_lang(text: str, prior: str | None = None) -> str:
    """Tiny, dependency-free EN/ES/FR detector tuned for short titles.

    `prior` (e.g. 'es' for aalavina.org) wins when the text is inconclusive.
    """
    t = clean_text(text).lower()
    if not t:
        return prior or "und"
    words = re.findall(r"[a-záéíóúüñàâçèêëîïôûœ']+", t)
    es = sum(1 for w in words if w in _ES_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    fr = sum(1 for w in words if w in _FR_WORDS)
    es += 2 * len(re.findall(r"[ñ¿¡]", t)) + len(re.findall(r"[áíóú]", t))
    es += sum(1 for w in words if re.search(r"(ción|ciones|dad|dades|mente|amos|aron)$", w))
    en += sum(1 for w in words if re.search(r"(ing|tion|ness|ship|ly)$", w) and not w.endswith("ción"))
    fr += 2 * len(re.findall(r"[àâçèêëîïôûœ]", t))
    best = max((es, "es"), (en, "en"), (fr, "fr"))
    runner = sorted([es, en, fr])[-2]
    if best[0] == 0 or best[0] - runner < 1:
        return prior or (best[1] if best[0] else "und")
    return best[1]


# --------------------------------------------------------------------------- raw data I/O
def raw_path(source: str) -> Path:
    return RAW_DIR / f"{source}.json"


# source → message, for raw files found unreadable this run (reported by the next save_raw()).
_CORRUPT_NOTES: dict[str, str] = {}


def load_raw(source: str) -> dict:
    """The raw envelope of a source (an empty one if the file does not exist yet).

    A file that exists but cannot be parsed (a bad hand edit, an interrupted restore) is never
    silently overwritten: it is renamed to `<source>.json.corrupt-<UTC time>` (kept for recovery
    from the git history), the module rebuilds the source from scratch, and the next save_raw()
    marks the run ok=false with an explanation so the problem shows on /status/."""
    p = raw_path(source)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
                raise ValueError("not a raw envelope (expected an object with an 'items' list)")
            data.setdefault("items", [])
            return data
        except Exception as e:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = p.with_name(f"{p.name}.corrupt-{stamp}")
            try:
                os.replace(p, backup)
            except OSError as e2:   # cannot move it aside → stop rather than overwrite it
                raise RuntimeError(f"{p.name} is unreadable ({type(e).__name__}) and could not be "
                                   f"renamed: {e2}") from e
            _CORRUPT_NOTES[source] = (
                f"data/raw/{p.name} was unreadable ({type(e).__name__}: {str(e)[:80]}); it was saved as "
                f"{backup.name} and this source was rebuilt from scratch — restore the file from the git "
                f"history to keep older items and first-seen dates")
            get_logger("common").error("%s", _CORRUPT_NOTES[source])
    return {"source": source, "updated": None, "ok": False, "error": None, "stats": {}, "items": []}


def _json_default(o: Any) -> Any:
    """Last-resort conversion so an unexpected value (e.g. a YAML date) never crashes a write."""
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    return str(o)


def write_json(path: Path, data: Any, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=False, default=_json_default)
        else:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False, default=_json_default)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_raw(source: str, items: list[dict], ok: bool = True, error: str | None = None,
             stats: dict | None = None, extra: dict | None = None) -> None:
    """Write data/raw/<source>.json. If ok=False and items is empty, previous items are kept.

    `first_harvest` records when the source was first read successfully (even with 0 items) and
    never moves afterwards: build_data uses it to tell the launch-day back catalog from real news.
    (The oldest first_seen cannot be used for that — it moves forward whenever a source drops old
    items, e.g. past events.) When first written it is seeded from the oldest first_seen known."""
    prev = load_raw(source)
    note = _CORRUPT_NOTES.pop(source, None)
    if note:   # the previous file was unreadable (see load_raw): make it visible on /status/ once
        ok, error = False, (f"{note}; {error}" if error else note)
    if not ok and not items:
        items = prev.get("items", [])
    items = sort_items(items)
    first = prev.get("first_harvest")
    if not first and ok:
        seen = sorted(str(i["first_seen"]) for i in [*(prev.get("items") or []), *items]
                      if isinstance(i, dict) and i.get("first_seen"))
        first = seen[0] if seen else now_iso()
    env = {
        "source": source,
        "updated": now_iso() if ok else prev.get("updated"),
        "attempted": now_iso(),
        "ok": ok,
        "error": (error or None) if not ok else None,
        "stats": stats or {},
        "items": items,
    }
    if first:
        env["first_harvest"] = first
    if extra:
        env.update(extra)
    write_json(raw_path(source), env)


def sort_items(items: list[dict]) -> list[dict]:
    def key(i):
        d = (i.get("extra") or {}).get("start") or i.get("date") or i.get("first_seen") or ""
        return str(d)
    return sorted(items, key=key, reverse=True)


def make_item(*, id: str, source: str, kind: str, url: str, title: str, summary: str = "",
              lang: str = "und", date: str | None = None, image: str | None = None,
              tags: Iterable[str] | None = None, category: str | None = None,
              extra: dict | None = None, status: str = "ok") -> dict:
    """Build an Item following docs/DATA_SCHEMA.md (first_seen/last_seen set by merge_items)."""
    return {
        "id": id, "source": source, "kind": kind, "url": url,
        "title": clean_text(title), "summary": truncate(summary or "", 400),
        "lang": lang or "und", "date": date, "first_seen": None, "last_seen": None,
        "image": image, "tags": list(tags or []), "category": category, "status": status,
        "extra": extra or {},
    }


def merge_items(old: list[dict], new: list[dict], *, drop_missing: bool = False,
                mark_gone_missing: bool = False, authoritative: bool = False) -> tuple[list[dict], int]:
    """Cumulative merge by id.

    * keeps `first_seen` of existing items, sets `last_seen` = now for items in `new`
      (refreshed at most weekly)
    * by default a field that is empty in the new item (date/image/summary, or an `extra` key that is
      None/""/[]/{}) keeps its previous value — a scraped page that lacks something today does not wipe it
    * authoritative=True: the new item is the whole truth (hand-written files, items rebuilt from
      their own saved state or cache) → only first_seen/last_seen come from the old item, so a field
      that was removed really disappears
    * items only in `old` are kept (unless drop_missing) — optionally marked status="gone"
    * returns (merged, count_of_new_ids)
    """
    now = now_iso()
    by_id = {i["id"]: i for i in old if i.get("id")}
    added = 0
    seen = set()
    for it in new:
        iid = it.get("id")
        if not iid:
            continue
        seen.add(iid)
        prev = by_id.get(iid)
        it = dict(it)
        if prev and authoritative:
            it["first_seen"] = prev.get("first_seen") or now
        elif prev:
            it["first_seen"] = prev.get("first_seen") or now
            # keep previously-known values when the new fetch lacks them
            for k in ("date", "image", "summary"):
                if not it.get(k) and prev.get(k):
                    it[k] = prev[k]
            pe, ne = prev.get("extra") or {}, it.get("extra") or {}
            it["extra"] = {**pe, **{k: v for k, v in ne.items() if v not in (None, "", [], {})}}
        else:
            it["first_seen"] = it.get("first_seen") or now
            added += 1
        # Refresh last_seen at most weekly so unchanged items don't churn the daily git diff.
        prev_seen = parse_iso(prev.get("last_seen")) if prev else None
        if prev_seen and (datetime.now(timezone.utc) - prev_seen).days < 7:
            it["last_seen"] = prev.get("last_seen")
        else:
            it["last_seen"] = now
        it.setdefault("status", "ok")
        by_id[iid] = it
    if drop_missing:
        by_id = {k: v for k, v in by_id.items() if k in seen}
    elif mark_gone_missing:
        for k, v in by_id.items():
            if k not in seen:
                v["status"] = "gone"
    return sort_items(list(by_id.values())), added


# --------------------------------------------------------------------------- HTTP
class PoliteSession:
    """requests.Session wrapper: identifies itself, obeys robots.txt + Crawl-delay per server
    (host names of one server share one delay slot — SAME_SERVER_HOSTS), retries transient errors
    with backoff, and never hammers a host.

        http = PoliteSession(min_delay=1.0)           # generic
        r = http.get(url)                             # returns Response or None (robots-disallowed / failed)
    """

    def __init__(self, user_agent: str | None = None, min_delay: float = 1.0, respect_robots: bool = True,
                 timeout: float = 30.0, retries: int = 3, browser_ua: bool = False):
        cfg = load_config()
        self.ua = user_agent or (BROWSER_UA if browser_ua else (cfg.get("sources", {}).get("crawler", {}) or {}).get(
            "user_agent", "NETA65-GrapevineCommitteeBot/2.0"))
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": self.ua, "Accept-Language": "en-US,en;q=0.8,es;q=0.7"})
        self.min_delay = min_delay
        self.respect_robots = respect_robots
        self.timeout = timeout
        self.retries = retries
        self._robots: dict[str, Any] = {}
        self._last: dict[str, float] = {}          # pace key (see pace_key) → time of the last request
        self._delay: dict[str, float] = {}         # pace key → strictest delay seen for that group
        self.requests_made = 0
        self.log = get_logger("http")

    # pacing ---------------------------------------------------------------
    @staticmethod
    def pace_key(url: str) -> str:
        """Requests are spaced per server, not per host name: www.aagrapevine.org and www.aalavina.org
        (and their bare-domain aliases) are one Drupal server and share one Crawl-delay slot."""
        host = (urlparse(url).hostname or "").lower()
        return SAME_SERVER_HOSTS.get(host, host)

    def _pause(self, key: str, delay: float) -> None:
        last = self._last.get(key)
        if last is not None:
            gap = time.monotonic() - last
            if gap < delay:
                time.sleep(delay - gap)

    # robots ---------------------------------------------------------------
    def _robots_for(self, url: str):
        host = urlparse(url).netloc
        if host in self._robots:
            return self._robots[host]
        rp = None
        if self.respect_robots and Protego is not None:
            key = self.pace_key(url)
            # robots.txt is a request to the same server too (its own delay is not known yet)
            self._pause(key, max(self.min_delay, self._delay.get(key, 0.0)))
            try:
                r = self.s.get(f"{urlparse(url).scheme}://{host}/robots.txt", timeout=self.timeout)
                if r.status_code == 200:
                    rp = Protego.parse(r.text)
            except Exception as e:  # robots unreachable → assume allowed
                self.log.debug("robots fetch failed for %s: %s", host, e)
            finally:
                self._last[key] = time.monotonic()
        self._robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(url, self.ua)
        except Exception:
            return True

    def delay_for(self, url: str) -> float:
        rp = self._robots_for(url)
        d = self.min_delay
        if rp is not None:
            try:
                cd = rp.crawl_delay(self.ua)
                if cd:
                    d = max(d, float(cd))
            except Exception:
                pass
        key = self.pace_key(url)
        d = max(d, self._delay.get(key, 0.0))      # hosts on one server: the strictest delay wins
        self._delay[key] = d
        return d

    def _wait(self, url: str) -> None:
        d = self.delay_for(url)
        self._pause(self.pace_key(url), d)

    # requests -------------------------------------------------------------
    MAX_REDIRECTS = 5

    def request(self, method: str, url: str, **kw) -> requests.Response | None:
        """One polite request. Redirects of the magazine server (SAME_SERVER_HOSTS) are followed
        by hand, so every hop also waits the Crawl-delay and is checked against robots.txt (requests
        itself would follow them at once, inside the same call)."""
        follow = kw.pop("allow_redirects", True)
        if not follow or (urlparse(url).hostname or "").lower() not in SAME_SERVER_HOSTS:
            return self._send(method, url, allow_redirects=follow, **kw)
        r = None
        for _hop in range(self.MAX_REDIRECTS + 1):
            r = self._send(method, url, allow_redirects=False, **kw)
            loc = r.headers.get("Location") if r is not None and getattr(r, "is_redirect", False) else None
            if not loc:
                return r
            nxt = urljoin(url, loc)
            try:
                r.close()
            except Exception:  # noqa: BLE001 — closing a finished redirect response never matters
                pass
            if not self.allowed(nxt):
                self.log.info("robots.txt disallows %s (redirected from %s)", nxt, url)
                return None
            url = nxt
        self.log.warning("%s %s: too many redirects", method, url)
        return None

    def _send(self, method: str, url: str, **kw) -> requests.Response | None:
        if not self.allowed(url):
            self.log.info("robots.txt disallows %s", url)
            return None
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("allow_redirects", True)
        key = self.pace_key(url)
        for attempt in range(1, self.retries + 1):
            self._wait(url)
            try:
                r = self.s.request(method, url, **kw)
                self._last[key] = time.monotonic()
                self.requests_made += 1
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    ra = r.headers.get("Retry-After")
                    backoff = float(ra) if ra and ra.isdigit() else 5.0 * attempt
                    self.log.warning("%s %s → %s, retry in %.0fs", method, url, r.status_code, backoff)
                    time.sleep(min(backoff, 60))
                    continue
                return r
            except requests.RequestException as e:
                self._last[key] = time.monotonic()
                if attempt >= self.retries:
                    self.log.warning("%s %s failed: %s", method, url, e)
                    return None
                time.sleep(3 * attempt)
        return None

    def get(self, url: str, **kw) -> requests.Response | None:
        return self.request("GET", url, **kw)

    def head(self, url: str, **kw) -> requests.Response | None:
        return self.request("HEAD", url, **kw)

    def get_text(self, url: str, **kw) -> str | None:
        r = self.get(url, **kw)
        if r is None or r.status_code != 200:
            return None
        r.encoding = r.encoding or "utf-8"
        if r.encoding.lower() in ("iso-8859-1", "latin-1") and "charset" not in r.headers.get("Content-Type", ""):
            r.encoding = "utf-8"
        return r.text


_SHARED: PoliteSession | None = None


def shared_session() -> PoliteSession:
    """ONE bot session for aagrapevine.org / aalavina.org shared by every module in a run,
    so robots.txt Crawl-delay (5 s) is respected across modules and across BOTH hosts (one server,
    see SAME_SERVER_HOSTS), not just within one module or one host name."""
    global _SHARED
    if _SHARED is None:
        _SHARED = PoliteSession(min_delay=5.0, respect_robots=True)
    return _SHARED


def absolute(base: str, href: str) -> str:
    return urljoin(base, href.strip())


def run_module(name: str, fn) -> int:
    """Standard CLI wrapper: `python -m scripts.sync.<name>` → exit code 0 even on soft failure
    (the module is expected to have written ok=false itself)."""
    log = get_logger(name)
    t0 = time.time()
    try:
        fn()
        log.info("done in %.1fs", time.time() - t0)
        return 0
    except KeyboardInterrupt:
        raise
    except Exception as e:  # never fail the whole pipeline because one source broke
        log.exception("module crashed: %s", e)
        try:
            prev = load_raw(name)
            keep = {k: v for k, v in prev.items() if k not in ("source", "updated", "attempted", "ok", "error", "stats", "items")}
            save_raw(name, prev.get("items", []), ok=False, error=f"{type(e).__name__}: {e}"[:300], stats=prev.get("stats"), extra=keep)
        except Exception:
            pass
        return 0
