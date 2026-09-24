"""Google Drive folder listing — used by scripts/sync/drive.py.

Two interchangeable listers return the same `Listing` of `Entry` objects:

* HtmlLister (default, no key needed) scrapes Drive's public "embedded folder view":
      https://drive.google.com/embeddedfolderview?id=<FOLDER_ID>
  Works for any folder shared as "Anyone with the link: Viewer". Each child is a
  `<div class="flip-entry" id="entry-<ID>">` with a link (folder or file), a small
  MIME-type icon, a title and a "last modified" text ("7:49 am", "Feb 20", "4/13/25").
  Shortcuts link to the *shortcut's* id; one HEAD request on /file/d/<id>/view
  (Drive answers 302 → the target) resolves the real file.

* ApiLister (only when the GOOGLE_API_KEY env var is set) uses the Drive API v3
  `files.list` for exact created/modified dates, sizes and shortcut targets. The key is
  sent in the X-Goog-Api-Key header (never in the URL, so it cannot leak into logs).
  Each folder is first confirmed with files.get (files.list answers "200, no files" for a
  folder the key cannot see). Any API error falls back to HtmlLister for that folder;
  after a few failures the API is switched off for the rest of the run.

A listing is only `ok` when Drive returned a real folder page. This matters because
drive.py deletes files that disappeared from a folder — it must never do that because
of a network error, a sign-in page, or a markup change on Google's side.
"""
from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from .common import MONTHS, PoliteSession, clean_text, get_logger, load_config

log = get_logger("drive")

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
EMBED_URL = "https://drive.google.com/embeddedfolderview?id={id}"
API_URL = "https://www.googleapis.com/drive/v3/files"
API_FIELDS = ("nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,size,"
              "imageMediaMetadata(time,width,height),videoMediaMetadata(durationMillis),"
              "shortcutDetails(targetId,targetMimeType))")

# Drive ids are URL-safe base64-ish strings (folders ~33 chars, Google Docs ~44 chars).
_ID = r"[A-Za-z0-9_-]{10,}"
_HREF_ID = re.compile(rf"/(?:folders|d)/({_ID})")
_ENTRY_ID = re.compile(rf"^entry-({_ID})$")

# Where docs.google.com links point → the native Google mime type.
_DOCS_KIND_MIME = {
    "document": "application/vnd.google-apps.document",
    "spreadsheets": "application/vnd.google-apps.spreadsheet",
    "presentation": "application/vnd.google-apps.presentation",
    "forms": "application/vnd.google-apps.form",
    "drawings": "application/vnd.google-apps.drawing",
}

# mimetypes on Windows/CI does not always know these.
_EXTRA_TYPES = {
    ".heic": "image/heic", ".heif": "image/heif", ".webp": "image/webp", ".md": "text/markdown",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".key": "application/vnd.apple.keynote", ".m4v": "video/x-m4v", ".mov": "video/quicktime",
}


def guess_mime(name: str) -> str | None:
    """Best-effort mime type from a file name's extension (None if unknown)."""
    m = re.search(r"(\.[A-Za-z0-9]{2,5})$", name or "")
    if not m:
        return None
    ext = m.group(1).lower()
    return _EXTRA_TYPES.get(ext) or mimetypes.guess_type("x" + ext)[0]


# --------------------------------------------------------------------------- data classes
@dataclass
class Entry:
    """One child of a Drive folder (file or folder), as reported by a lister."""
    id: str                          # file/folder id to use (shortcut *target* when resolved)
    name: str
    mime: str
    is_folder: bool = False
    href: str | None = None          # link from the listing (HTML mode)
    modified_text: str | None = None  # raw "last modified" text (HTML) or API modifiedTime
    modified: str | None = None      # ISO date / datetime
    created: str | None = None       # ISO datetime (API only)
    size: int | None = None          # bytes (API only)
    taken: str | None = None         # photo EXIF time, ISO datetime (API only)
    duration_sec: int | None = None  # video length (API only)
    shortcut_id: str | None = None   # id of the shortcut itself, when this entry was a shortcut
    unresolved_shortcut: bool = False


@dataclass
class Listing:
    ok: bool
    entries: list[Entry] = field(default_factory=list)
    title: str | None = None         # folder name (HTML mode: page <title>)
    error: str | None = None
    via: str = "html"


# --------------------------------------------------------------------------- "last modified" text
def local_today() -> date:
    """Today's date in the committee's time zone (Drive shows "7:49 am" for today's edits)."""
    tzname = (load_config().get("site") or {}).get("timezone") or "America/Chicago"
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tzname)).date()
    except Exception:  # tzdata missing → UTC is close enough for a day-level date
        return datetime.now(timezone.utc).date()


def parse_modified_text(text: str | None, today: date | None = None) -> str | None:
    """Turn Drive's relative "last modified" text into YYYY-MM-DD.

    "7:49 am" → today · "Feb 20" → this year (last year if that would be >1 day in the
    future) · "4/13/25" → 2025-04-13 · "Feb 20, 2024" / "20 feb 2024" also understood.
    """
    if not text:
        return None
    t = clean_text(text).lower().replace(".", "")
    today = today or local_today()
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}(\s*[ap]\s*m)?", t):
            return today.isoformat()
        if t in ("today", "hoy"):
            return today.isoformat()
        if t in ("yesterday", "ayer"):
            return (today - timedelta(days=1)).isoformat()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", t)
        if m:
            y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return date(y, int(m[1]), int(m[2])).isoformat()
        m = re.fullmatch(r"([a-záéíóú]+)\s+(\d{1,2})(?:,?\s+(\d{4}))?", t)          # "feb 20[, 2024]"
        if m and m[1] in MONTHS:
            mo, d = MONTHS[m[1]], int(m[2])
        else:
            m2 = re.fullmatch(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)(?:,?\s+(?:de\s+)?(\d{4}))?", t)  # "20 feb"
            if not (m2 and m2[2] in MONTHS):
                return None
            m, mo, d = m2, MONTHS[m2[2]], int(m2[1])
        if m[3]:
            return date(int(m[3]), mo, d).isoformat()
        cand = date(today.year, mo, d)
        if cand > today + timedelta(days=1):
            cand = date(today.year - 1, mo, d)
        return cand.isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------- HTML lister
class HtmlLister:
    """Lists a public folder by scraping the embedded folder view (no API key)."""

    via = "html"

    def __init__(self, session: PoliteSession | None = None, shortcut_cache: dict[str, str] | None = None):
        # Google hosts: browser UA, no robots (embeddedfolderview is a public embed endpoint),
        # still spaced out and retried by PoliteSession.
        self.http = session or PoliteSession(browser_ua=True, min_delay=0.5, respect_robots=False, timeout=30)
        # shortcut id → target file id, pre-filled from the previous run's items.
        self.shortcut_cache: dict[str, str] = dict(shortcut_cache or {})
        self.shortcuts_resolved = 0
        self.today = local_today()

    # public ---------------------------------------------------------------
    def list(self, folder_id: str) -> Listing:
        url = EMBED_URL.format(id=folder_id)
        r = self.http.get(url)
        if r is None:
            return Listing(False, error="network error (no response)")
        host = urlparse(r.url).netloc
        if "accounts.google." in host or "ServiceLogin" in r.url:
            return Listing(False, error="not shared publicly (Google asks to sign in)")
        if r.status_code in (401, 403, 404):
            return Listing(False, error=f"not found or not shared publicly (HTTP {r.status_code})")
        if r.status_code != 200:
            return Listing(False, error=f"HTTP {r.status_code}")
        r.encoding = "utf-8"
        return self.parse(r.text)

    def parse(self, html_text: str) -> Listing:
        soup = BeautifulSoup(html_text, "lxml")
        title = clean_text(soup.title.get_text()) if soup.title else None
        container = soup.find(class_="flip-entries")
        if container is None:
            # Not a folder page (sign-in / "request access" / captcha / Google changed the markup).
            hint = "request access page" if re.search(r"(?i)request access|need access|solicitar acceso", html_text) \
                else "unexpected page (not a public folder, or Drive changed its markup)"
            return Listing(False, title=title, error=hint)
        entries: list[Entry] = []
        for div in container.select("div.flip-entry"):
            try:
                e = self._parse_entry(div)
            except Exception as ex:  # one odd entry must not break the folder
                log.warning("could not parse a Drive entry: %s", ex)
                continue
            if e:
                entries.append(e)
        if not entries and container.find(True) is not None:
            # There ARE children but none looked like an entry → markup changed. Refuse, so
            # the caller does not mistake this for "the folder is now empty" and delete items.
            return Listing(False, title=title, error="folder page has content but no recognizable entries (markup changed?)")
        for e in entries:
            if e.mime == SHORTCUT_MIME and not e.is_folder:
                self._resolve_shortcut(e)
        return Listing(True, entries, title=title)

    # internals -------------------------------------------------------------
    def _parse_entry(self, div) -> Entry | None:
        a = div.find("a", href=True)
        href = a["href"].strip() if a else ""
        m = _HREF_ID.search(href)
        fid = m.group(1) if m else None
        if not fid:
            m = _ENTRY_ID.match(div.get("id") or "")
            fid = m.group(1) if m else None
        if not fid:
            return None
        t = div.select_one(".flip-entry-title")
        name = clean_text(t.get_text()) if t else ""
        lm = div.select_one(".flip-entry-last-modified")
        mod_text = clean_text(lm.get_text(" ")) if lm else None

        mime = None
        icon = div.select_one(".flip-entry-list-icon img[src]") or div.find("img", src=re.compile(r"/type/"))
        if icon and "/type/" in icon.get("src", ""):
            mime = unquote(icon["src"].split("/type/", 1)[1]).split("?")[0].strip("/")
        # Folders have no mime icon, just a sprite with aria-label="Folder".
        is_folder = "/drive/folders/" in href or (not mime and bool(div.select_one('[aria-label="Folder"]')))
        if is_folder:
            mime = FOLDER_MIME
        if not mime:
            pm = re.search(r"docs\.google\.com/(document|spreadsheets|presentation|forms|drawings)/", href)
            mime = _DOCS_KIND_MIME.get(pm.group(1)) if pm else None
        mime = mime or guess_mime(name) or "application/octet-stream"
        return Entry(id=fid, name=name, mime=mime, is_folder=is_folder, href=href or None,
                     modified_text=mod_text, modified=parse_modified_text(mod_text, self.today))

    def _resolve_shortcut(self, e: Entry) -> None:
        """Shortcut → target. Drive answers HEAD /file/d/<shortcut>/view with a 302 to the target."""
        sid = e.id
        e.shortcut_id = sid
        target, is_folder = self.shortcut_cache.get(sid), False
        if target == sid:
            self.shortcut_cache.pop(sid, None)
            target = None  # an old "could not resolve" entry — look it up again
        if not target:
            r = self.http.head(f"https://drive.google.com/file/d/{sid}/view", allow_redirects=False)
            loc = (r.headers.get("Location") or "") if r is not None else ""
            m = re.search(rf"/file/d/({_ID})", loc) or re.search(rf"/folders/({_ID})", loc)
            if m and m.group(1) != sid:
                target, is_folder = m.group(1), "/folders/" in loc
                self.shortcuts_resolved += 1
        if not target:
            # The view link by shortcut id still opens the file, but its thumbnail and direct
            # download do not — mark it so no broken picture is published; retried next run.
            e.unresolved_shortcut = True
            log.info("could not resolve shortcut %r (%s)", e.name, sid)
        else:
            e.id = target
        if is_folder:
            e.is_folder, e.mime = True, FOLDER_MIME
        else:
            # The listing only says "shortcut" — the name's extension tells us what it points at.
            e.mime = guess_mime(e.name) or SHORTCUT_MIME
            if target:
                self.shortcut_cache[sid] = target


# --------------------------------------------------------------------------- API lister
class ApiError(Exception):
    pass


class ApiLister:
    """Drive API v3 files.list with an API key (public folders only)."""

    via = "api"

    def __init__(self, key: str):
        self.key = key
        self.http = PoliteSession(browser_ua=True, min_delay=0.1, respect_robots=False, timeout=30)
        self.http.s.headers["X-Goog-Api-Key"] = key

    def check_folder(self, folder_id: str) -> str | None:
        """None when `folder_id` is a readable, non-trashed folder; otherwise the reason.

        Needed because files.list answers a query about a folder the key cannot see (made
        private, or a mistyped id) with HTTP 200 and an EMPTY list — which must never be read
        as "every file was deleted"."""
        r = self.http.get(f"{API_URL}/{folder_id}",
                          params={"fields": "id,mimeType,trashed", "supportsAllDrives": "true"})
        if r is None:
            return "API network error"
        if r.status_code in (401, 403, 404):
            return f"not found or not shared publicly (API HTTP {r.status_code})"
        if r.status_code != 200:
            return f"API HTTP {r.status_code}: {_api_message(r)}"
        try:
            meta = r.json()
        except ValueError:
            return "API returned non-JSON"
        if not isinstance(meta, dict) or meta.get("mimeType") != FOLDER_MIME:
            return "not a folder"
        if meta.get("trashed"):
            return "the folder is in the trash"
        return None

    def list(self, folder_id: str) -> Listing:
        problem = self.check_folder(folder_id)
        if problem:
            return Listing(False, error=problem, via="api")
        entries: list[Entry] = []
        token = None
        for _page in range(50):  # 50 × 1000 files is far beyond any committee folder
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": API_FIELDS, "pageSize": 1000, "orderBy": "folder,name",
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            r = self.http.get(API_URL, params=params)
            if r is None:
                return Listing(False, error="API network error", via="api")
            if r.status_code != 200:
                return Listing(False, error=f"API HTTP {r.status_code}: {_api_message(r)}", via="api")
            try:
                data = r.json()
            except ValueError:
                return Listing(False, error="API returned non-JSON", via="api")
            for f in data.get("files") or []:
                e = self._entry(f)
                if e:
                    entries.append(e)
            token = data.get("nextPageToken")
            if not token:
                break
        return Listing(True, entries, via="api")

    @staticmethod
    def _entry(f: dict) -> Entry | None:
        fid, name, mime = f.get("id"), clean_text(f.get("name")), f.get("mimeType") or ""
        if not fid:
            return None
        shortcut_id = None
        sd = f.get("shortcutDetails") or {}
        if mime == SHORTCUT_MIME:
            if sd.get("targetId"):
                shortcut_id, fid = fid, sd["targetId"]
                mime = sd.get("targetMimeType") or guess_mime(name) or SHORTCUT_MIME
            else:
                mime = guess_mime(name) or SHORTCUT_MIME
        imd = f.get("imageMediaMetadata") or {}
        vmd = f.get("videoMediaMetadata") or {}
        taken = None
        if imd.get("time"):  # EXIF "2026:02:19 18:33:16" (camera local time)
            m = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", imd["time"])
            if m and m[1] != "0000":
                taken = f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}"
        try:
            size = int(f["size"]) if f.get("size") else None
        except (TypeError, ValueError):
            size = None
        try:
            dur = int(int(vmd["durationMillis"]) / 1000) if vmd.get("durationMillis") else None
        except (TypeError, ValueError):
            dur = None
        return Entry(id=fid, name=name, mime=mime, is_folder=(mime == FOLDER_MIME),
                     modified_text=f.get("modifiedTime"), modified=_api_time(f.get("modifiedTime")),
                     created=_api_time(f.get("createdTime")), size=size, taken=taken,
                     duration_sec=dur, shortcut_id=shortcut_id)


def _api_time(s: str | None) -> str | None:
    """'2026-04-13T12:34:56.789Z' → '2026-04-13T12:34:56Z'."""
    if not s:
        return None
    return re.sub(r"\.\d+Z$", "Z", s)


def _api_message(r) -> str:
    try:
        msg = (r.json().get("error") or {}).get("message") or ""
    except ValueError:
        msg = r.text[:120]
    return clean_text(msg)[:160]


# --------------------------------------------------------------------------- combined lister
class DriveLister:
    """Uses the API when a key is configured, otherwise (or on any API error) the HTML view."""

    MAX_API_FAILURES = 3

    def __init__(self, *, use_api: bool = True, shortcut_cache: dict[str, str] | None = None):
        self.html = HtmlLister(shortcut_cache=shortcut_cache)
        key = (os.environ.get("GOOGLE_API_KEY") or "").strip()
        self.api = ApiLister(key) if (use_api and key) else None
        self._api_http = self.api.http if self.api else None
        self.api_failures = 0
        self.api_error: str | None = None
        self.listed_via: dict[str, int] = {"api": 0, "html": 0}

    @property
    def mode(self) -> str:
        return "api" if self.api else "html"

    @property
    def http(self) -> PoliteSession:
        """Session for Google hosts (Docs export / file downloads)."""
        return self.html.http

    def list(self, folder_id: str) -> Listing:
        if self.api is not None:
            res = self.api.list(folder_id)
            if res.ok:
                self.listed_via["api"] += 1
                return res
            self.api_failures += 1
            self.api_error = res.error
            log.warning("Drive API failed for %s (%s) — falling back to the public HTML view", folder_id, res.error)
            if self.api_failures >= self.MAX_API_FAILURES:
                log.warning("Drive API disabled for the rest of this run after %d failures", self.api_failures)
                self.api = None
        res = self.html.list(folder_id)
        if res.ok:
            self.listed_via["html"] += 1
        return res

    @property
    def requests_made(self) -> int:
        return self.html.http.requests_made + (self._api_http.requests_made if self._api_http else 0)
