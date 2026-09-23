"""YouTube → data/raw/youtube.json  (AA Grapevine & La Viña videos — no API key needed)

Where the videos come from (cheapest and most reliable first):

  1. RSS/Atom feeds — EVERY run. The channel feed, the channel's automatic
     "uploads" playlists (UULF… = long-form, UUSH… = shorts, UULV… = past live
     streams) and every public playlist of the channel. Each feed returns its
     newest 15 videos with the exact publish date, description and view count.
  2. yt-dlp flat listing — weekly (or whenever few videos are stored). The
     complete list of videos / shorts / live streams, the channel's playlists and
     the full membership of every playlist (→ extra.playlists; the Spanish /
     La Viña playlists are a strong language hint). Flat listings give duration,
     views and only an *approximate* date ("3 years ago") which we store as a
     plain date with extra.date_approx = true.
  3. yt-dlp per-video details — a few dozen per run, newest first — for videos
     that still lack an exact date or a duration. Over a week or two every video
     ends up with its exact date and description.
  4. "Gone" check — only after a COMPLETE listing: stored videos that are no
     longer listed are checked with YouTube's public oEmbed endpoint; 401/403/404
     (private / deleted) marks them status "gone" (capped per run).

yt-dlp is optional: when it is missing, slow or blocked (YouTube sometimes blocks
cloud/CI IP addresses) steps 2–4 are skipped or cut short and the RSS feeds alone
keep the site current. This module never fails the pipeline because of yt-dlp.

A note on robots.txt: youtube.com's robots.txt asks *crawlers* not to index
/feeds/videos.xml. We are not crawling. We fetch a small, fixed set of feed URLs
from the committee's settings about once a day, which is what a feed reader
does for a subscriber (Google's own Feedfetcher ignores robots.txt for the same
reason). So the YouTube requests here skip the robots.txt check but keep a
1-second delay between requests, a clear User-Agent and short time-outs.

Run:  python -m scripts.sync.youtube [--backfill] [--no-backfill] [--details 60]
                                    [--backfill-minutes 6] [--gone-checks 20] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import feedparser

from .common import (
    PoliteSession,
    clean_text,
    detect_lang,
    get_logger,
    load_config,
    load_raw,
    make_item,
    merge_items,
    now_iso,
    parse_iso,
    run_module,
    save_raw,
    to_iso,
)

SOURCE = "youtube"
log = get_logger(SOURCE)

RSS_CHANNEL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
RSS_PLAYLIST = "https://www.youtube.com/feeds/videos.xml?playlist_id={}"
WATCH_URL = "https://www.youtube.com/watch?v={}"
SHORTS_URL = "https://www.youtube.com/shorts/{}"
THUMB_URL = "https://i.ytimg.com/vi/{}/hqdefault.jpg"
OEMBED_URL = "https://www.youtube.com/oembed?url=https://www.youtube.com/watch%3Fv%3D{}&format=json"

BACKFILL_EVERY_DAYS = 7      # full yt-dlp listing at most this often …
SMALL_LIST = 100             # … unless fewer than this many videos are stored
MAX_DETAIL_FAILS = 3         # stop retrying per-video details after this many failures
SHORTS_CHECKS_PER_RUN = 40   # HEAD /shorts/<id> probes for new videos of unknown type
RSS_PLAYLIST_BUDGET_S = 300  # stop fetching playlist feeds after 5 minutes (normally ~40 s)
# oEmbed answers for a video that is no longer watchable: 401/403 = private, 404 = deleted
GONE_CODES = (401, 403, 404)

_VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SEASON_RE = re.compile(r"\[\s*(?:Season|Temporada)\s*(\d+)\s*[,.]\s*(?:Episode|Episodio)\s*(\d+)\s*\]", re.I)
_LIVE_RE = re.compile(r"\bweekly\s+open\b|\blive\b|\ben\s+vivo\b|\bdirecto\b", re.I)
# Brand names that would otherwise skew the tiny EN/ES word-count detector
# ("La Viña" makes an English title look Spanish; "Grapevine"/"AA" are neutral).
_BRANDS_RE = re.compile(
    r"(?i)\bla\s+vi[ñn]a\b|\bgrapevine(?:'s)?\b|\bicypaa\b|\beurypaa\b|\bypaa\b|\bgvrs?\b|\brlvs?\b|"
    r"\basl\b|\bzoom\b|\bctm\b|(?<![\w.])a\.?\s?a\.?(?![\w.])"
)


# --------------------------------------------------------------------------- small helpers
def _int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _iso_from_rss(s: str | None) -> str | None:
    """'2026-09-21T16:00:07+00:00' → '2026-09-21T16:00:07Z'."""
    if not s:
        return None
    try:
        return to_iso(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


def _iso_from_ts(ts: Any) -> str | None:
    t = _int(ts)
    if not t or t <= 0:
        return None
    return to_iso(datetime.fromtimestamp(t, tz=timezone.utc))


def _date_from_ts(ts: Any) -> str | None:
    t = _int(ts)
    if not t or t <= 0:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()


def _lang_text(s: str) -> str:
    """Remove brand names before language detection (see _BRANDS_RE)."""
    return _BRANDS_RE.sub(" ", s or "")


def playlist_lang(title: str) -> str:
    """Language of a playlist from its title: 'es' | 'en' | 'und'."""
    lang = detect_lang(_lang_text(title), None)
    if lang == "und" and re.search(r"(?i)vi[ñn]a", title or ""):
        return "es"  # a playlist just called "La Viña" is Spanish content
    return lang if lang in ("en", "es") else "und"


def _timeboxed(fn: Callable[[], Any], seconds: float) -> Any:
    """Run fn() in a daemon thread and give up after `seconds`.

    yt-dlp calls are blocking and can hang on a slow/blocked connection; a daemon
    thread cannot keep the process alive, so a stuck call never blocks the pipeline.
    """
    if seconds <= 0:
        raise TimeoutError("no time left")
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["r"] = fn()
        except BaseException as e:  # noqa: BLE001 — report anything to the caller
            box["e"] = e

    t = threading.Thread(target=run, daemon=True, name="yt-dlp")
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"timed out after {seconds:.0f}s")
    if "e" in box:
        raise box["e"]
    return box.get("r")


# --------------------------------------------------------------------------- per-video accumulator
class Video:
    """Everything learned about one video during this run (merged with the stored item later)."""

    __slots__ = ("vid", "channel_id", "title", "desc", "date", "approx_ts", "duration", "views",
                 "is_short", "live", "playlists", "yt_lang", "details_ok")

    def __init__(self, vid: str):
        self.vid = vid
        self.channel_id: str | None = None
        self.title: str | None = None
        self.desc: str | None = None
        self.date: str | None = None          # exact publish datetime (ISO Z)
        self.approx_ts: int | None = None     # yt-dlp "3 years ago" estimate
        self.duration: int | None = None
        self.views: int | None = None
        self.is_short: bool | None = None
        self.live: bool | None = None
        self.playlists: set[str] = set()      # playlist ids
        self.yt_lang: str | None = None
        self.details_ok = False


class Collector:
    def __init__(self) -> None:
        self.videos: dict[str, Video] = {}

    def get(self, vid: str) -> Video:
        v = self.videos.get(vid)
        if v is None:
            v = self.videos[vid] = Video(vid)
        return v

    # RSS entry (feedparser) -------------------------------------------------
    def add_rss(self, e: Any, channel_id: str | None, *, playlist: str | None = None,
                is_short: bool | None = None, live: bool | None = None) -> str | None:
        vid = e.get("yt_videoid") or ""
        if not _VID_RE.match(vid):
            return None
        v = self.get(vid)
        v.channel_id = e.get("yt_channelid") or v.channel_id or channel_id
        title = clean_text(e.get("title"))
        if title:
            v.title = title
        desc = e.get("summary")
        if desc is not None and (v.desc is None or len(desc) > len(v.desc)):
            v.desc = desc
        d = _iso_from_rss(e.get("published"))
        if d:
            v.date = d
        stats = e.get("media_statistics") or {}
        views = _int(stats.get("views")) if isinstance(stats, dict) else None
        if views is not None:
            v.views = views
        if is_short is not None:
            v.is_short = is_short
        if live:
            v.live = True
        if playlist:
            v.playlists.add(playlist)
        return vid

    # yt-dlp flat entry ------------------------------------------------------
    def add_flat(self, e: dict, channel_id: str | None, *, playlist: str | None = None,
                 is_short: bool | None = None, live: bool | None = None) -> str | None:
        vid = (e or {}).get("id") or ""
        if not _VID_RE.match(vid):
            return None
        title = clean_text(e.get("title"))
        # playlists keep slots for private/deleted videos (no title, or "[Private video]") —
        # those are not videos anyone can watch, so they must not count as "listed"
        if not title or re.fullmatch(r"\[(private|deleted) video\]", title, re.I) or e.get("availability") in (
                "private", "needs_auth", "subscriber_only", "premium_only"):
            return None
        v = self.get(vid)
        v.channel_id = v.channel_id or e.get("channel_id") or channel_id
        if title:
            v.title = v.title or title
        if e.get("timestamp") and not v.approx_ts:
            v.approx_ts = _int(e.get("timestamp"))
        if e.get("duration"):
            v.duration = _int(e.get("duration"))
        if e.get("view_count") is not None and v.views is None:
            v.views = _int(e.get("view_count"))
        if is_short is not None:
            v.is_short = is_short
        elif "/shorts/" in str(e.get("url") or ""):
            v.is_short = True
        if live or e.get("live_status") in ("was_live", "post_live", "is_live"):
            v.live = True
        if playlist:
            v.playlists.add(playlist)
        return vid

    # yt-dlp full info -------------------------------------------------------
    def add_details(self, info: dict) -> bool:
        vid = info.get("id") or ""
        if not _VID_RE.match(vid):
            return False
        v = self.get(vid)
        v.channel_id = info.get("channel_id") or v.channel_id
        title = clean_text(info.get("title"))
        if title:
            v.title = title
        if info.get("description") is not None:
            v.desc = info.get("description")
        exact = _iso_from_ts(info.get("timestamp")) or _iso_from_ts(info.get("release_timestamp"))
        if not exact and re.fullmatch(r"\d{8}", str(info.get("upload_date") or "")):
            u = info["upload_date"]
            exact = f"{u[:4]}-{u[4:6]}-{u[6:]}"
        if exact and not v.date:   # an RSS date (same value) wins if we already have one
            v.date = exact
        if info.get("duration"):
            v.duration = _int(info.get("duration"))
        if info.get("view_count") is not None:
            v.views = _int(info.get("view_count"))
        if info.get("was_live") or info.get("live_status") in ("was_live", "post_live", "is_live"):
            v.live = True
        lang = str(info.get("language") or "").lower()[:2]
        if lang in ("en", "es", "fr"):
            v.yt_lang = lang
        v.details_ok = bool(exact)
        return v.details_ok


# --------------------------------------------------------------------------- RSS
def fetch_feed(http: PoliteSession, url: str) -> tuple[list, int | None]:
    """→ (entries, http_status). entries == [] with status 404 means "no such feed"."""
    r = http.get(url, headers={"Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.5"})
    if r is None:
        return [], None
    if r.status_code != 200:
        return [], r.status_code
    f = feedparser.parse(r.content)
    return list(f.entries or []), 200


# --------------------------------------------------------------------------- yt-dlp
class _YtLogger:
    """Routes yt-dlp's console output into our log (quietly) and remembers the last error."""

    def __init__(self) -> None:
        self.last_error = ""

    def debug(self, msg: str) -> None:  # yt-dlp sends progress/info lines here
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        self.last_error = str(msg)
        log.debug("yt-dlp error: %s", msg)


def _ytdlp():
    try:
        import yt_dlp  # noqa: WPS433 — optional dependency
        return yt_dlp
    except Exception as e:  # pragma: no cover
        log.warning("yt-dlp not available (%s) — RSS only", e)
        return None


def _flat_opts(logger: _YtLogger) -> dict:
    return {
        "quiet": True, "no_warnings": True, "noprogress": True, "ignoreerrors": True,
        "skip_download": True, "extract_flat": "in_playlist", "socket_timeout": 20,
        "retries": 2, "extractor_retries": 2, "logger": logger,
        # estimate upload dates from "3 years ago" labels (marked date_approx)
        "extractor_args": {"youtubetab": {"approximate_date": [""]}},
    }


def _flat_list(yt_dlp, url: str, seconds: float) -> tuple[dict | None, str]:
    """Flat-extract a channel tab / playlist. → (info or None, error text)."""
    logger = _YtLogger()

    def go():
        with yt_dlp.YoutubeDL(_flat_opts(logger)) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and info.get("entries") is not None:
                info["entries"] = [e for e in info["entries"] if e]  # materialize generators
            return info

    try:
        info = _timeboxed(go, seconds)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    return info, logger.last_error


def backfill_channel(yt_dlp, channel_id: str, col: Collector, deadline: float) -> dict:
    """Complete listing of one channel with yt-dlp (time-boxed by `deadline`).

    Returns {"videos_ok", "shorts_ok", "listed": set(ids), "playlists": [..], "membership_ok", "errors"}.
    """
    base = f"https://www.youtube.com/channel/{channel_id}"
    res: dict[str, Any] = {"videos_ok": False, "shorts_ok": False, "listed": set(), "playlists": [],
                           "playlists_ok": False, "membership_ok": False, "errors": []}

    def left() -> float:
        return deadline - time.monotonic()

    for tab, kw in (("videos", {"is_short": False}), ("shorts", {"is_short": True}), ("streams", {"live": True})):
        info, err = _flat_list(yt_dlp, f"{base}/{tab}", min(left(), 180))
        if info is None:
            # a channel without a "streams"/"shorts" tab is normal, not an error
            if not re.search(r"does not have a \w+ tab", err or ""):
                res["errors"].append(f"{tab}: {err[:120]}")
                log.warning("yt-dlp %s listing failed: %s", tab, err[:200])
            elif tab == "shorts":
                res["shorts_ok"] = True
            continue
        entries = info.get("entries") or []
        for e in entries:
            vid = col.add_flat(e, channel_id, **kw)
            if vid:
                res["listed"].add(vid)
        res[f"{tab}_ok"] = True
        log.info("yt-dlp %-8s %4d entries", tab, len(entries))

    info, err = _flat_list(yt_dlp, f"{base}/playlists", min(left(), 120))
    if info is None:
        res["errors"].append(f"playlists: {err[:120]}")
        return res
    res["playlists_ok"] = True
    pls = []
    for e in info.get("entries") or []:
        pid = str(e.get("id") or "")
        if not pid.startswith(("PL", "OL", "FL")) or not e.get("title"):
            continue
        pls.append({"id": pid, "title": clean_text(e.get("title")), "lang": playlist_lang(e.get("title"))})
    log.info("yt-dlp playlists %3d", len(pls))

    complete = True
    for pl in pls:
        if left() < 5:
            complete = False
            res["errors"].append("playlist membership cut short (time budget)")
            break
        info, err = _flat_list(yt_dlp, f"https://www.youtube.com/playlist?list={pl['id']}", min(left(), 60))
        if info is None:
            complete = False
            res["errors"].append(f"playlist {pl['id']}: {err[:80]}")
            continue
        n = 0
        for e in info.get("entries") or []:
            vid = col.add_flat(e, channel_id, playlist=pl["id"])
            if vid:
                n += 1
                res["listed"].add(vid)
        pl["count"] = n
    res["playlists"] = pls
    res["membership_ok"] = complete
    return res


class DetailFetcher:
    """Per-video details with ONE reused yt-dlp instance (keeps its player cache warm)."""

    BOT_CHECK = re.compile(r"(?i)sign in to confirm|not a bot|429|too many requests")
    GONE = re.compile(r"(?i)private video|video unavailable|has been removed|account .* terminated|"
                      r"no longer available|does not exist")

    def __init__(self, yt_dlp) -> None:
        self.logger = _YtLogger()
        self.ydl = yt_dlp.YoutubeDL({
            "quiet": True, "no_warnings": True, "noprogress": True, "ignoreerrors": True,
            "skip_download": True, "socket_timeout": 20, "retries": 1, "extractor_retries": 1,
            "logger": self.logger,
        })

    def fetch(self, vid: str, seconds: float) -> tuple[dict | None, str]:
        self.logger.last_error = ""

        def go():
            # process=False: metadata only, no format selection / downloads
            return self.ydl.extract_info(WATCH_URL.format(vid), download=False, process=False)

        try:
            info = _timeboxed(go, seconds)
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}: {e}"
        return info, self.logger.last_error

    def close(self) -> None:
        try:
            self.ydl.close()
        except Exception:
            pass


def resolve_channel_id(yt_dlp, handle: str, seconds: float) -> str | None:
    """'@AAGrapevine' → 'UC…' (only needed if config lists a handle without an id)."""
    h = handle if handle.startswith("@") else "@" + handle
    info, _ = _flat_list(yt_dlp, f"https://www.youtube.com/{h}/videos", seconds)
    cid = (info or {}).get("channel_id")
    return cid if cid and str(cid).startswith("UC") else None


# --------------------------------------------------------------------------- checks
def oembed_status(http: PoliteSession, vid: str) -> int | None:
    r = http.get(OEMBED_URL.format(vid))
    return None if r is None else r.status_code


def probe_is_short(http: PoliteSession, vid: str) -> bool | None:
    """/shorts/<id> answers 200 for a Short and redirects (303) to /watch for normal videos."""
    r = http.head(SHORTS_URL.format(vid), allow_redirects=False)
    if r is None:
        return None
    if r.status_code == 200:
        return True
    if 300 <= r.status_code < 400:
        return False
    return None


# --------------------------------------------------------------------------- item building
def build_item(v: Video, prev: dict | None, pl_by_id: dict[str, dict], membership_complete: bool,
               default_channel: str | None) -> dict | None:
    pe = (prev or {}).get("extra") or {}
    title = v.title or (prev or {}).get("title") or ""
    if not title:
        return None  # nothing presentable yet (e.g. a "[Private video]" playlist slot)

    desc = v.desc if v.desc is not None else ((prev or {}).get("summary") or "")
    desc = clean_text(desc)

    # --- publish date: exact beats approximate; a stored date is kept stable
    date_approx = False
    if v.date:
        date = v.date
    elif prev and prev.get("date") and not pe.get("date_approx"):
        date = prev["date"]
    elif prev and prev.get("date"):
        date, date_approx = prev["date"], True
    elif v.approx_ts:
        date, date_approx = _date_from_ts(v.approx_ts), True
    else:
        date = None

    # --- playlists (names, in the channel's playlist order)
    ids = set(v.playlists)
    if not membership_complete:
        # keep what earlier (complete) runs knew; RSS only shows each playlist's newest 15
        prev_titles = set(pe.get("playlists") or [])
        ids |= {pid for pid, p in pl_by_id.items() if p["title"] in prev_titles}
    order = {pid: i for i, pid in enumerate(pl_by_id)}
    pl_ids = sorted((i for i in ids if i in pl_by_id), key=lambda i: order[i])
    pl_titles = [pl_by_id[i]["title"] for i in pl_ids]
    if not membership_complete:  # titles of playlists we no longer list (renamed/removed) stay too
        pl_titles += [t for t in (pe.get("playlists") or []) if t not in pl_titles]

    # --- language: YouTube's own audio-language tag > playlist majority > English
    pl_langs = [pl_by_id[i]["lang"] for i in pl_ids]
    yt_lang = v.yt_lang or pe.get("yt_lang")
    if yt_lang in ("en", "es"):
        prior = yt_lang
    elif pl_langs.count("es") > pl_langs.count("en"):
        prior = "es"
    else:
        prior = "en"
    lang = detect_lang(_lang_text(title) + " " + _lang_text(desc[:200]), prior)
    if lang not in ("en", "es", "fr"):
        lang = prior

    is_short = v.is_short if v.is_short is not None else pe.get("is_short")
    is_short = bool(is_short)
    live = bool(v.live or pe.get("is_live_recording") or _LIVE_RE.search(title)
                or re.search(r"(?i)\blive recording\b|\bgrabaci[oó]n en vivo\b", desc[:300]))
    duration = v.duration or _int(pe.get("duration_sec"))
    views = v.views if v.views is not None else _int(pe.get("views"))

    tags: list[str] = []
    m = _SEASON_RE.search(title)
    season = episode = None
    if m:
        season, episode = int(m[1]), int(m[2])
        tags.append(f"season-{season}")
    if re.search(r"(?i)weekly\s+open", title):
        tags.append("weekly-open")
    if is_short:
        tags.append("short")
    if live:
        tags.append("live")
    if re.search(r"(?i)\bASL\b|sign language|lenguaje de señas", title + " " + " ".join(pl_titles)):
        tags.append("asl")

    extra = {
        "video_id": v.vid,
        "channel_id": v.channel_id or pe.get("channel_id") or default_channel,
        "duration_sec": duration,
        "playlists": pl_titles,
        "is_short": is_short,
        "views": views,
        "is_live_recording": live,
        "date_approx": date_approx,
        "season": season,
        "episode": episode,
    }
    if yt_lang:
        extra["yt_lang"] = yt_lang
    for k in ("season", "episode", "views"):   # optional fields: omit rather than store null
        if extra[k] is None:
            del extra[k]

    it = make_item(
        id=f"yt:{v.vid}", source="youtube", kind="video",
        url=(SHORTS_URL if is_short else WATCH_URL).format(v.vid),
        title=title, summary=desc, lang=lang, date=date,
        image=THUMB_URL.format(v.vid), tags=tags,
        category="lv" if lang == "es" else "gv", extra=extra,
    )
    return it


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Sync YouTube videos → data/raw/youtube.json")
    ap.add_argument("--backfill", action="store_true", help="force the full yt-dlp listing now")
    ap.add_argument("--no-backfill", action="store_true", help="skip the yt-dlp listing (RSS only)")
    ap.add_argument("--backfill-minutes", type=float, default=6.0,
                    help="time budget for ALL yt-dlp work this run (listing + details), default 6")
    ap.add_argument("--details", type=int, default=60,
                    help="max videos to fetch exact date/description for this run (0 = off), default 60")
    ap.add_argument("--gone-checks", type=int, default=20, help="max oEmbed 'is it deleted?' checks per run")
    ap.add_argument("--max-playlist-feeds", type=int, default=80, help="cap on playlist RSS feeds per run")
    ap.add_argument("--dry-run", action="store_true", help="do everything but do not write the JSON")
    # parse_known_args: when a runner imports this module and calls main() without argv,
    # its own command-line flags must not crash us.
    args, unknown = ap.parse_known_args(argv)
    if unknown:
        log.debug("ignoring unknown arguments: %s", unknown)

    t0 = time.monotonic()
    cfg = (load_config().get("sources", {}) or {}).get("youtube", {}) or {}
    channels = [c for c in (cfg.get("channels") or []) if isinstance(c, dict)]

    prev = load_raw(SOURCE)
    prev_items: list[dict] = prev.get("items", []) or []
    prev_by_vid: dict[str, dict] = {}
    for it in prev_items:
        iid = str(it.get("id") or "")
        if iid.startswith("yt:"):
            prev_by_vid[iid[3:]] = it
    stored_playlists: list[dict] = [p for p in (prev.get("playlists") or []) if isinstance(p, dict) and p.get("id")]
    backfilled_at = prev.get("backfilled_at")
    detail_fails: dict[str, int] = {k: int(v) for k, v in (prev.get("detail_fails") or {}).items()}
    known_channels: dict[str, str] = dict(prev.get("channel_ids") or {})  # handle → id cache

    http = PoliteSession(min_delay=1.0, respect_robots=False, timeout=20, retries=2)
    col = Collector()
    errors: list[str] = []
    stats: dict[str, Any] = {"channels": len(channels), "rss_feeds": 0, "rss_failed": 0}
    rss_ok = False
    yt_dlp = None if args.no_backfill and args.details <= 0 else _ytdlp()
    ytdlp_deadline: float | None = None

    def ytdlp_left() -> float:
        nonlocal ytdlp_deadline
        if ytdlp_deadline is None:  # the budget starts with the first yt-dlp call
            ytdlp_deadline = time.monotonic() + args.backfill_minutes * 60
        return ytdlp_deadline - time.monotonic()

    # ---- resolve channel ids ------------------------------------------------
    channel_ids: list[str] = []
    for c in channels:
        cid = str(c.get("id") or "").strip()
        handle = str(c.get("handle") or "").strip()
        if not cid and handle:
            cid = known_channels.get(handle) or ""
            if not cid and yt_dlp is not None:
                cid = resolve_channel_id(yt_dlp, handle, min(ytdlp_left(), 60)) or ""
            if cid:
                known_channels[handle] = cid
        if cid.startswith("UC"):
            channel_ids.append(cid)
        else:
            errors.append(f"channel {handle or c}: no channel id")
    if not channel_ids:
        save_raw(SOURCE, prev_items, ok=False, error="no YouTube channel configured/resolved", stats=stats,
                 extra={"backfilled_at": backfilled_at, "playlists": stored_playlists,
                        "detail_fails": detail_fails, "channel_ids": known_channels})
        return
    default_channel = channel_ids[0]

    # ---- 1. channel RSS + automatic uploads playlists (always) --------------
    for cid in channel_ids:
        suffix = cid[2:]
        feeds = [
            (RSS_CHANNEL.format(cid), {}),
            (RSS_PLAYLIST.format("UULF" + suffix), {"is_short": False}),
            (RSS_PLAYLIST.format("UUSH" + suffix), {"is_short": True}),
            (RSS_PLAYLIST.format("UULV" + suffix), {"live": True}),
        ]
        for i, (url, kw) in enumerate(feeds):
            entries, status = fetch_feed(http, url)
            stats["rss_feeds"] += 1
            if status == 200:
                for e in entries:
                    col.add_rss(e, cid, **kw)
                rss_ok = True
            elif status == 404 and i > 0:
                pass  # e.g. no live streams → UULV feed does not exist
            else:
                stats["rss_failed"] += 1
                if i == 0:
                    why = f"HTTP {status}" if status else "network error"
                    errors.append(f"channel RSS {cid}: {why}")
                    log.warning("channel RSS failed for %s (%s)", cid, why)
    log.info("RSS: %d videos from channel feeds", len(col.videos))

    # ---- 2. yt-dlp complete listing (weekly / when small / forced) ----------
    last_bf = parse_iso(backfilled_at)
    # also run it when the feeds failed today — the listing is then the only fresh source
    due = (args.backfill or last_bf is None or len(prev_by_vid) < SMALL_LIST or not rss_ok
           or datetime.now(timezone.utc) - last_bf >= timedelta(days=BACKFILL_EVERY_DAYS))
    listed: set[str] = set()
    listing_complete = False
    membership_complete = False
    new_playlists: list[dict] | None = None
    if args.no_backfill:
        stats["backfill"] = "skipped (--no-backfill)"
    elif not due:
        stats["backfill"] = f"not due (last {backfilled_at})"
    elif yt_dlp is None:
        stats["backfill"] = "skipped (yt-dlp unavailable)"
    else:
        tb = time.monotonic()
        all_ok_videos = all_ok_shorts = all_ok_playlists = all_membership = True
        pls: list[dict] = []
        for cid in channel_ids:
            try:
                res = backfill_channel(yt_dlp, cid, col, time.monotonic() + max(0.0, ytdlp_left()))
            except Exception as e:  # noqa: BLE001 — never fail the module because of yt-dlp
                log.warning("yt-dlp backfill crashed for %s: %s", cid, e)
                res = {"videos_ok": False, "shorts_ok": False, "listed": set(), "playlists": [],
                       "playlists_ok": False, "membership_ok": False, "errors": [f"crash: {e}"[:120]]}
            listed |= res["listed"]
            all_ok_videos &= res["videos_ok"]
            all_ok_shorts &= res["shorts_ok"]
            all_ok_playlists &= res["playlists_ok"]
            all_membership &= res["playlists_ok"] and res["membership_ok"]
            for p in res["playlists"]:
                p["channel_id"] = cid
            pls += res["playlists"]
            errors += [f"yt-dlp {e}" for e in res["errors"]]
        secs = round(time.monotonic() - tb, 1)
        stats["backfill_seconds"] = secs
        stats["listed"] = len(listed)
        if all_ok_playlists:
            new_playlists = pls  # otherwise keep the stored list of playlists
        # Sanity check before trusting the listing for "gone" detection: a partial
        # answer from YouTube must not make us mark real videos as deleted.
        known_ok = sum(1 for it in prev_by_vid.values() if it.get("status", "ok") == "ok")
        listing_complete = all_ok_videos and all_ok_shorts and len(listed) >= 0.9 * known_ok
        membership_complete = all_ok_videos and all_membership
        if all_ok_videos:
            backfilled_at = now_iso()
            stats["backfill"] = f"ok ({len(listed)} listed in {secs}s)"
        else:
            stats["backfill"] = f"failed after {secs}s — will retry next run"
        log.info("backfill: %s", stats["backfill"])

    playlists = new_playlists if new_playlists is not None else stored_playlists
    for p in playlists:                       # re-derive language (cheap; picks up heuristic fixes)
        p["lang"] = playlist_lang(p.get("title", ""))
    pl_by_id = {p["id"]: p for p in playlists}

    # ---- 3. playlist RSS feeds (daily: newest 15 of each playlist) ----------
    feeds_done = fails_in_row = 0
    rss_deadline = time.monotonic() + RSS_PLAYLIST_BUDGET_S
    for p in playlists[: max(0, args.max_playlist_feeds)]:
        if fails_in_row >= 5 or time.monotonic() > rss_deadline:
            errors.append("playlist feeds stopped early (YouTube not answering)")
            log.warning("playlist feeds: stopping early (%d failures in a row)", fails_in_row)
            break
        entries, status = fetch_feed(http, RSS_PLAYLIST.format(p["id"]))
        stats["rss_feeds"] += 1
        feeds_done += 1
        if status == 200:
            for e in entries:
                col.add_rss(e, p.get("channel_id") or default_channel, playlist=p["id"])
            rss_ok = True
            fails_in_row = 0
        elif status == 404:
            fails_in_row = 0  # playlist deleted/private since the last listing
        else:
            stats["rss_failed"] += 1
            fails_in_row += 1
    stats["playlist_feeds"] = feeds_done
    log.info("RSS: %d videos after %d playlist feeds", len(col.videos), feeds_done)

    # ---- 4. is it a Short? (cheap HEAD probe for new videos of unknown type) --
    probes = 0
    for vid, v in col.videos.items():
        if probes >= SHORTS_CHECKS_PER_RUN:
            break
        known = v.is_short if v.is_short is not None else (prev_by_vid.get(vid, {}).get("extra") or {}).get("is_short")
        if known is None and (v.title or vid in prev_by_vid):
            v.is_short = probe_is_short(http, vid)
            probes += 1
    stats["shorts_probes"] = probes

    # ---- 5. per-video details (exact date / description / duration) --------
    gone: set[str] = set()
    detailed = detail_failed = 0
    if args.details > 0 and yt_dlp is not None and ytdlp_left() > 10:
        def needs(vid: str) -> bool:
            if detail_fails.get(vid, 0) >= MAX_DETAIL_FAILS:
                return False
            v = col.videos.get(vid)
            p = prev_by_vid.get(vid) or {}
            pe = p.get("extra") or {}
            if p.get("status") == "gone" and v is None:
                return False
            exact = (v is not None and v.date) or (p.get("date") and not pe.get("date_approx"))
            duration = (v is not None and v.duration) or pe.get("duration_sec")
            return not (exact and duration)

        def sort_key(vid: str) -> str:
            v = col.videos.get(vid)
            p = prev_by_vid.get(vid) or {}
            return str((v and (v.date or _date_from_ts(v.approx_ts))) or p.get("date") or p.get("first_seen") or "")

        todo = sorted((vid for vid in set(col.videos) | set(prev_by_vid) if needs(vid)), key=sort_key, reverse=True)
        stats["details_pending"] = len(todo)
        fetcher = DetailFetcher(yt_dlp)
        consecutive_fail = 0
        try:
            for vid in todo[: args.details]:
                left = ytdlp_left()
                if left < 5:
                    log.info("details: time budget used up")
                    break
                info, err = fetcher.fetch(vid, min(left, 60))
                if info and col.add_details(info):
                    detailed += 1
                    consecutive_fail = 0
                    detail_fails.pop(vid, None)
                    continue
                detail_failed += 1
                consecutive_fail += 1
                detail_fails[vid] = detail_fails.get(vid, 0) + 1
                log.debug("details failed for %s: %s", vid, err[:160])
                if DetailFetcher.GONE.search(err or ""):
                    # confirm with oEmbed before believing it
                    if oembed_status(http, vid) in GONE_CODES:
                        gone.add(vid)
                if DetailFetcher.BOT_CHECK.search(err or "") or "Timeout" in (err or "") or consecutive_fail >= 5:
                    errors.append(f"details stopped: {err[:100]}")
                    log.warning("details: stopping early (%s)", err[:160])
                    break
        finally:
            fetcher.close()
    stats["details_fetched"] = detailed
    stats["details_failed"] = detail_failed

    # ---- 6. confirm deleted/private videos ----------------------------------
    checks = 0
    if listing_complete and args.gone_checks > 0:
        seen_now = listed | set(col.videos)
        missing = [vid for vid, it in prev_by_vid.items()
                   if vid not in seen_now and it.get("status", "ok") != "gone" and vid not in gone]
        missing.sort(key=lambda vid: str(prev_by_vid[vid].get("last_seen") or ""))
        for vid in missing[: args.gone_checks]:
            code = oembed_status(http, vid)
            checks += 1
            if code in GONE_CODES:
                gone.add(vid)
        stats["missing_from_listing"] = len(missing)
    stats["gone_checks"] = checks
    stats["gone_marked"] = len(gone)

    # ---- 7. build + merge ---------------------------------------------------
    new_items = []
    for vid, v in col.videos.items():
        if vid in gone:
            continue
        try:
            it = build_item(v, prev_by_vid.get(vid), pl_by_id, membership_complete, default_channel)
        except Exception as e:  # noqa: BLE001 — one bad record must not stop the run
            log.warning("could not build item for %s: %s", vid, e)
            continue
        if it:
            new_items.append(it)
    merged, added = merge_items(prev_items, new_items)

    # merge_items keeps old values when the new ones are empty; for fields where
    # "empty" is the truth this run (e.g. removed from all playlists), re-apply.
    fresh = {it["id"]: it for it in new_items}
    for it in merged:
        f = fresh.get(it["id"])
        if f is not None:
            it["extra"]["playlists"] = f["extra"]["playlists"]
            it["extra"]["date_approx"] = f["extra"]["date_approx"]
            it["status"] = "ok"
        elif it["id"][3:] in gone:
            it["status"] = "gone"
        for k in ("season", "episode", "views"):   # optional: omit rather than store null
            if (it.get("extra") or {}).get(k, 0) is None:
                it["extra"].pop(k, None)

    ok_items = [i for i in merged if i.get("status") != "gone"]
    stats.update({
        "fetched": len(new_items),
        "new": added,
        "total": len(merged),
        "active": len(ok_items),
        "gone": len(merged) - len(ok_items),
        "spanish": sum(1 for i in ok_items if i.get("lang") == "es"),
        "shorts": sum(1 for i in ok_items if (i.get("extra") or {}).get("is_short")),
        "approx_dates": sum(1 for i in ok_items if (i.get("extra") or {}).get("date_approx")),
        "playlists": len(playlists),
        "seconds": round(time.monotonic() - t0, 1),
    })
    if errors:
        stats["warnings"] = [e[:160] for e in errors[:8]]

    ok = rss_ok or bool(listed)
    error = None if ok else ("; ".join(errors) or "no data from RSS or yt-dlp")[:300]
    # forget failure counters of videos that no longer need details
    detail_fails = {k: v for k, v in detail_fails.items() if k in prev_by_vid or k in col.videos}
    log.info("videos: %d total (%d new, %d Spanish, %d shorts, %d approx dates, %d gone)",
             stats["total"], added, stats["spanish"], stats["shorts"], stats["approx_dates"], stats["gone"])
    if args.dry_run:
        log.info("dry run — not writing data/raw/%s.json; stats=%s", SOURCE, stats)
        return
    save_raw(SOURCE, merged, ok=ok, error=error, stats=stats, extra={
        "backfilled_at": backfilled_at,
        "playlists": [{"id": p["id"], "title": p.get("title"), "lang": p.get("lang"),
                       "count": p.get("count"), "channel_id": p.get("channel_id"),
                       "url": f"https://www.youtube.com/playlist?list={p['id']}"} for p in playlists],
        "detail_fails": detail_fails,
        "channel_ids": known_channels,
    })


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
