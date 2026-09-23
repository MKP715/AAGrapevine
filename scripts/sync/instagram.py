"""Instagram → data/raw/instagram.json  (+ small WebP thumbnails in src/assets/cache/ig/)

Keeps the newest posts of the two official accounts listed in config/site.yml
(`sources.instagram.accounts`: @alcoholicsanonymous_gv = "gv", @alcoholicosanonimos_lv = "lv").

Run from the repo root:
    python -m scripts.sync.instagram                 # normal daily run
    python -m scripts.sync.instagram --dry-run -v    # fetch + print, write nothing
    python -m scripts.sync.instagram --strategies web_profile_info,profile_html   # test one path

-----------------------------------------------------------------------------
HOW IT FINDS POSTS  (per account, the first strategy that returns posts wins)
-----------------------------------------------------------------------------
  1. graph_api        Official Instagram Graph API "Business Discovery".
                      Only runs when the GitHub secrets IG_ACCESS_TOKEN and
                      IG_BUSINESS_ID exist (see "OPTIONAL: OFFICIAL API" below).
                      Most reliable; returns the newest 30 posts.
  2. profile_embed    The public profile *embed* page https://www.instagram.com/<user>/embed/
                      (the widget Instagram offers websites). No key needed.
                      Returns the newest ~6 posts with caption, date and image —
                      plenty for a daily run. (Verified working Sept 2026.)
  3. web_profile_info Instagram's anonymous web JSON endpoint (i.instagram.com and
                      www.instagram.com). Often answers HTTP 429 to data-center IPs;
                      tried once per host, never hammered.
  4. profile_html     The public profile page. Served to our honest bot User-Agent it
                      embeds the newest ~12 posts (incl. pinned) as JSON; browsers get a
                      login wall. Also used as a "gap fill" after profile_embed when those
                      6 posts don't reach back to the newest post we already had (first
                      run, or the daily job was down for a few days).
  5. rsshub           Optional RSSHub mirrors from config `sources.instagram.rsshub_instances`.
  6. embed_hovercard  Last resort: the embed of the newest post we already know lists
                      the account's 2 newest posts.
  +  manual           content/instagram.yml — always merged in (see that file).

Then every known post that still lacks a caption / image / type is "enriched" from its
public post embed https://www.instagram.com/p/<code>/embed/captioned/ (capped per run,
2.5–4 s apart). The publish date never needs the network: Instagram shortcodes encode
the media id, whose top bits are the creation time in milliseconds.

The file is cumulative: known posts are never lost when Instagram blocks us for a day
(ok=false + error is written instead, so /status/ shows it). The newest
`keep_per_account` (default 60) posts per account are kept; older posts and their
thumbnails are pruned (manual posts are never pruned — remove them from the YAML).

A NOTE ON INSTAGRAM'S RULES
  instagram.com/robots.txt disallows generic crawlers and Meta's terms restrict automated
  collection. This module does not crawl: it requests two public embed pages a day (plus a
  few post embeds), identifies itself honestly with the committee bot User-Agent, and only
  shows short teasers that link back to Instagram. The fully sanctioned path is the official
  API (strategy 1). To use ONLY the official API + the manual list, set in config/site.yml:
      sources: { instagram: { anonymous: false } }        (or env IG_ANONYMOUS=0)

-----------------------------------------------------------------------------
OPTIONAL: OFFICIAL API  (IG_ACCESS_TOKEN + IG_BUSINESS_ID) — free, ~30 minutes, one time
-----------------------------------------------------------------------------
Business Discovery lets ANY Instagram *professional* account read the public posts of other
professional accounts (AA Grapevine's accounts are professional accounts).

  1. The committee needs its own Instagram account switched to "Professional"
     (Instagram app → Settings → Account type and tools → Switch to professional → Business;
     free) and linked to a Facebook Page the committee manages (Page → Settings →
     Linked accounts → Instagram).
  2. At https://developers.facebook.com → My Apps → Create app → type "Business".
     Add the product "Instagram" (Instagram API with Facebook Login).
  3. Get a token that does not expire (recommended):
       business.facebook.com → Settings → Users → System users → Add (Admin) →
       "Assign assets": the Facebook Page (+ the app) → "Generate new token" for your app,
       with permissions: instagram_basic, pages_show_list, pages_read_engagement,
       business_management.  Token expiry: "Never".
     (Alternative: Graph API Explorer → User token with the same permissions → exchange for a
      60-day long-lived token:  GET https://graph.facebook.com/v21.0/oauth/access_token?
      grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=TOKEN
      — this one must be renewed every 60 days; /status/ shows an error when it expires.)
  4. Find IG_BUSINESS_ID (the committee's Instagram *business account id*, a long number):
       https://graph.facebook.com/v21.0/me/accounts?fields=name,instagram_business_account{id,username}&access_token=TOKEN
     → use instagram_business_account.id  (NOT the Page id).
  5. GitHub repo → Settings → Secrets and variables → Actions → "New repository secret":
       IG_ACCESS_TOKEN = the token,   IG_BUSINESS_ID = the id from step 4.
  Optional: env IG_GRAPH_VERSION (default v21.0; Meta forwards retired versions automatically).
  The token is sent only to graph.facebook.com and is never logged.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests
import yaml

from .common import (
    CACHE_ASSETS,
    CONTENT_DIR,
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
    strip_html,
    to_iso,
    truncate,
)

SOURCE = "instagram"
log = get_logger(SOURCE)

THUMB_DIR = CACHE_ASSETS / "ig"
THUMB_URL = "/assets/cache/ig/"
MANUAL_FILE = CONTENT_DIR / "instagram.yml"
AVATAR_PREFIX = "_avatar_"

DEFAULT_KEEP_PER_ACCOUNT = 60
DEFAULT_ENRICH_PER_RUN = 25
DEFAULT_MAX_MINUTES = 8.0
ENRICH_RETRY_DAYS = 3          # re-try a post whose embed was unusable after N days
THUMB_MAX_W, THUMB_MAX_H, THUMB_QUALITY = 480, 720, 70
AVATAR_MAX, AVATAR_REFRESH_DAYS = 160, 7
IMAGE_MAX_BYTES = 12 * 1024 * 1024

ALL_STRATEGIES = ("graph_api", "profile_embed", "web_profile_info", "profile_html", "rsshub", "embed_hovercard")
ANONYMOUS_STRATEGIES = {"profile_embed", "web_profile_info", "profile_html", "embed_hovercard"}

# Instagram's public web app id (sent by instagram.com itself on every XHR).
IG_APP_ID = "936619743392459"
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1")
# Headers a browser sends when it loads an embed inside an <iframe> on another site.
IFRAME_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Sec-Fetch-Dest": "iframe",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
}

MEDIA_TYPES = {  # every spelling Instagram uses → schema value
    "graphimage": "image", "image": "image", "xdtgraphimage": "image", "1": "image",
    "graphvideo": "video", "video": "video", "xdtgraphvideo": "video", "2": "video", "reels": "video",
    "graphsidecar": "carousel", "carousel_album": "carousel", "sidecar": "carousel",
    "xdtgraphsidecar": "carousel", "8": "carousel", "carousel": "carousel",
    # logged-out profile page ("Polaris" web app) type names
    "xigpolarisimagemedia": "image", "xigpolarisvideomedia": "video",
    "xigpolariscarouselmedia": "carousel", "xigpolarissidecarmedia": "carousel",
}

# --------------------------------------------------------------------------- shortcodes
_SC_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_SC_INDEX = {c: i for i, c in enumerate(_SC_ALPHABET)}
_IG_EPOCH_MS = 1314220021721          # Instagram's id epoch (2011-08-24 21:07:01.721 UTC)
SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
POST_URL_RE = re.compile(
    r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]{8,40})", re.I)


def shortcode_to_id(sc: str) -> int | None:
    """'DXEcB00AFOo' → 3874344850174268328 (public shortcodes are 11 chars; longer ones
    carry a private suffix, only the first 11 chars encode the id)."""
    sc = (sc or "")[:11]
    n = 0
    for ch in sc:
        if ch not in _SC_INDEX:
            return None
        n = n * 64 + _SC_INDEX[ch]
    return n or None


def id_to_shortcode(media_id: int | str) -> str | None:
    try:
        n = int(str(media_id).split("_")[0])   # "<media>_<owner>" ids exist too
    except (TypeError, ValueError):
        return None
    out = ""
    while n > 0:
        n, r = divmod(n, 64)
        out = _SC_ALPHABET[r] + out
    return out or None


def date_from_media_id(media_id: int | None) -> str | None:
    """Creation time is encoded in the top 41 bits of the media id (ms since IG epoch)."""
    if not media_id:
        return None
    try:
        ts = ((int(media_id) >> 23) + _IG_EPOCH_MS) / 1000.0
        dt = datetime.fromtimestamp(ts, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    if dt.year < 2010 or dt > datetime.now(timezone.utc) + timedelta(days=2):
        return None
    return to_iso(dt)


def date_from_shortcode(sc: str) -> str | None:
    return date_from_media_id(shortcode_to_id(sc))


def parse_post_url(url: str) -> tuple[str | None, bool]:
    """Instagram post/reel URL → (shortcode, is_reel)."""
    m = POST_URL_RE.search(url or "")
    if not m:
        return None, False
    return m.group(2), m.group(1).lower() in ("reel", "reels")


def post_url(sc: str, is_reel: bool = False) -> str:
    return f"https://www.instagram.com/{'reel' if is_reel else 'p'}/{sc}/"


def embed_url(sc: str) -> str:
    return f"https://www.instagram.com/p/{sc}/embed/captioned/"


def ts_to_iso(value: Any) -> str | None:
    """Unix seconds / ISO string ('2026-09-23T12:00:14+0000') → UTC ISO 'Z'."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return to_iso(datetime.fromtimestamp(float(value), timezone.utc))
        s = str(value).strip()
        s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s).replace("Z", "+00:00")
        return to_iso(datetime.fromisoformat(s))
    except (ValueError, OverflowError, OSError):
        return None


def norm_media_type(value: Any, is_video: bool | None = None) -> str | None:
    if value not in (None, ""):
        mt = MEDIA_TYPES.get(str(value).lower().replace(" ", ""))
        if mt:
            return mt
    if is_video is True:
        return "video"
    if is_video is False:
        return "image"
    return None


# --------------------------------------------------------------------------- captions
_HASHTAG_RE = re.compile(r"(?<![\w&])#([0-9A-Za-zÀ-ÖØ-öø-ÿ_]{2,40})")


def clean_caption(caption: str | None) -> str:
    """Normalize a caption: unify newlines, drop '.'/'-' spacer lines, drop trailing
    hashtag-only lines (they are kept separately as tags)."""
    if not caption:
        return ""
    text = str(caption).replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if not re.fullmatch(r"[.\-_•·⠀\s]*", ln) or ln == ""]
    while lines and (not lines[-1] or re.fullmatch(r"(?:[#@][\w.À-ÿ]+[\s,.]*)+", lines[-1])):
        lines.pop()
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def hashtags(caption: str | None, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for tag in _HASHTAG_RE.findall(caption or ""):
        t = tag.lower()
        if t not in seen:
            seen.append(t)
        if len(seen) >= limit:
            break
    return seen


def title_from_caption(caption: str, fallback: str, limit: int = 90) -> str:
    """First meaningful line of the caption, ≤ ~90 chars, cut at a word boundary."""
    for raw in (caption or "").split("\n"):
        line = clean_text(raw)
        line = re.sub(r"^(?:[#@][\w.À-ÿ]+\s*)+$", "", line)       # hashtag/mention-only lines
        line = line.rstrip(":;,-–— ")                             # "October 20% off:" → "… off"
        if len(re.sub(r"[\W_]+", "", line)) >= 3:
            if len(line) <= limit:
                return line
            cut = line[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.-–— ")
            return (cut or line[: limit - 1]) + "…"
    return fallback


def summary_from_caption(caption: str) -> str:
    return truncate(re.sub(r"\s*\n\s*", " ", caption or ""), 400)


# --------------------------------------------------------------------------- post records
def new_post(shortcode: str, account: str | None, strategy: str, **kw) -> dict:
    """Normalized in-memory record, independent of where it came from."""
    rec = {
        "shortcode": shortcode, "account": account, "strategy": strategy,
        "username": None, "caption": None,          # None = unknown, "" = known to be empty
        "date": None, "media_type": None, "image_url": None, "is_reel": False,
        "manual": False, "verify": False,           # verify=True → keep only if its embed confirms it
        "media_id": None,
    }
    rec.update({k: v for k, v in kw.items() if v is not None})
    return rec


def merge_post(a: dict, b: dict) -> dict:
    """Combine two records of the same shortcode; known values win over unknown ones."""
    out = dict(a)
    for k, v in b.items():
        if k in ("manual", "is_reel"):
            out[k] = bool(out.get(k)) or bool(v)
        elif k == "verify":
            out[k] = bool(out.get(k)) and bool(v)
        elif out.get(k) in (None, "") and v not in (None, ""):
            out[k] = v
    return out


def _caption_of(node: dict) -> str | None:
    edges = ((node.get("edge_media_to_caption") or {}).get("edges")) or []
    if edges:
        return ((edges[0] or {}).get("node") or {}).get("text") or ""
    cap = node.get("caption")
    if isinstance(cap, dict):
        return cap.get("text") or ""
    if isinstance(cap, str):
        return cap
    return "" if "edge_media_to_caption" in node else None


def _best_image(node: dict) -> str | None:
    """Smallest rendition that is still ≥ our thumbnail width (saves bandwidth)."""
    cands = []
    for r in node.get("display_resources") or []:
        if isinstance(r, dict) and r.get("src"):
            cands.append((int(r.get("config_width") or 0), r["src"]))
    for r in ((node.get("image_versions2") or {}).get("candidates")) or []:
        if isinstance(r, dict) and r.get("url"):
            cands.append((int(r.get("width") or 0), r["url"]))
    big = sorted(c for c in cands if c[0] >= THUMB_MAX_W)
    if big:
        return big[0][1]
    return node.get("display_url") or node.get("thumbnail_src") or (max(cands)[1] if cands else None)


def post_from_graphql(node: dict, account: str, strategy: str, username: str | None = None) -> dict | None:
    """Node from the profile embed (`shortcode_media`) or web_profile_info (`edges[].node`)."""
    if not isinstance(node, dict):
        return None
    sc = node.get("shortcode") or node.get("code") or id_to_shortcode(node.get("id") or node.get("pk") or 0)
    if not sc or not SHORTCODE_RE.match(sc):
        return None
    owner = node.get("owner") or node.get("user") or {}
    media_type = (norm_media_type(node.get("__typename")) or norm_media_type(node.get("media_type"))
                  or norm_media_type(None, node.get("is_video")))
    return new_post(
        sc, account, strategy,
        username=(owner.get("username") if isinstance(owner, dict) else None) or username,
        caption=_caption_of(node),
        date=ts_to_iso(node.get("taken_at_timestamp") or node.get("taken_at")) or date_from_shortcode(sc),
        media_type=media_type,
        image_url=_best_image(node) or node.get("display_uri"),
        is_reel=(node.get("product_type") == "clips"),
        media_id=str(node.get("pk") or node.get("id") or "") or None,
    )


_CONTEXT_JSON_RE = re.compile(r'"contextJSON"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_context_json(page: str) -> dict | None:
    """The embed pages carry their data as a JSON *string* inside a JS array:
    ... "contextJSON":"{\\"context\\":{...}}" ...  → decode twice."""
    for m in _CONTEXT_JSON_RE.finditer(page or ""):
        try:
            data = json.loads(json.loads('"' + m.group(1) + '"'))
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("context"), dict):
            return data["context"]
    return None


def parse_post_embed(page: str) -> dict | None:
    """Public post embed (/p/<code>/embed/captioned/) → fields, or None when the page is the
    generic app shell (post deleted/private, or we were served a login wall)."""
    from bs4 import BeautifulSoup   # local import: only needed when enriching

    if not page or 'class="Embed' not in page:
        return None
    soup = BeautifulSoup(page, "lxml")
    emb = soup.select_one(".Embed[data-media-id], .Embed")
    if emb is None:
        return None
    out: dict[str, Any] = {
        "media_id": emb.get("data-media-id") or None,
        "owner_id": emb.get("data-owner-id") or None,
        "media_type": norm_media_type(emb.get("data-media-type")),
        "permalink": (emb.get("data-permalink") or "").split("?")[0] or None,
    }
    user = soup.select_one(".UsernameText") or soup.select_one(".CaptionUsername")
    out["username"] = clean_text(user.get_text()) if user else None

    cap = soup.select_one(".Caption")
    if cap is not None:
        for junk in cap.select(".CaptionUsername, .CaptionComments"):
            junk.decompose()
        for br in cap.find_all("br"):
            br.replace_with("\n")
        out["caption"] = cap.get_text().strip()
    else:
        out["caption"] = ""      # captionless post: the embed has no .Caption block

    img = soup.select_one("img.EmbeddedMediaImage") or soup.select_one(".EmbeddedMedia img")
    poster = soup.select_one("video[poster]")
    out["image_url"] = (img.get("src") if img else None) or (poster.get("poster") if poster else None)

    # The hover card shows the owner's newest posts; their CDN URLs carry
    # ig_cache_key=<base64(media id)>.
    hover: list[str] = []
    for im in soup.select(".HoverCardPhotos img"):
        for key in re.findall(r"ig_cache_key=([A-Za-z0-9%=+/_-]+)", (im.get("src") or "") + " " + (im.get("srcset") or "")):
            try:
                raw = unquote(key).split(".")[0]
                mid = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
            except (ValueError, UnicodeDecodeError):
                continue
            sc = id_to_shortcode(mid) if mid.isdigit() else None
            if sc and sc not in hover:
                hover.append(sc)
    out["hover"] = hover
    return out


def _walk_dicts(obj: Any):
    """Yield every dict inside a JSON structure (iteratively deep, bounded)."""
    stack = [(obj, 0)]
    while stack:
        cur, d = stack.pop()
        if d > 60:
            continue
        if isinstance(cur, dict):
            yield cur
            stack.extend((v, d + 1) for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend((v, d + 1) for v in cur if isinstance(v, (dict, list)))


_JSON_SCRIPT_RE = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)


def parse_profile_html(page: str, account: str, username: str) -> list[dict]:
    """Posts from the Relay JSON blocks of a profile page. Only media whose owner is the
    account itself are kept (the page can also mention other users' posts)."""
    found: dict[str, dict] = {}
    for m in _JSON_SCRIPT_RE.finditer(page or ""):
        body = m.group(1)
        if '"code"' not in body:
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        for d in _walk_dicts(data):
            code = d.get("code")
            if not (isinstance(code, str) and SHORTCODE_RE.match(code) and ("pk" in d or "id" in d)
                    and any(k in d for k in ("caption", "display_uri", "media_type", "image_versions2"))):
                continue
            owner = d.get("user") or d.get("owner") or {}
            owner_name = (owner.get("username") if isinstance(owner, dict) else "") or ""
            if owner_name.lower() != username.lower() or code in found:
                continue
            p = post_from_graphql(d, account, "profile_html", username)
            if p:
                found[code] = p
    return sorted(found.values(), key=lambda p: p.get("date") or "", reverse=True)


def post_from_graph_api(m: dict, account: str, username: str) -> dict | None:
    """One `business_discovery.media.data[]` entry → record."""
    sc, is_reel = parse_post_url(m.get("permalink") or "")
    if not sc:
        return None
    mt = norm_media_type(m.get("media_type"))
    image = m.get("thumbnail_url") or (m.get("media_url") if mt == "image" else None)
    if not image:
        for ch in ((m.get("children") or {}).get("data")) or []:
            if str(ch.get("media_type", "")).upper() == "IMAGE" and ch.get("media_url"):
                image = ch["media_url"]
                break
    return new_post(sc, account, "graph_api", username=username, caption=m.get("caption") or "",
                    date=ts_to_iso(m.get("timestamp")) or date_from_shortcode(sc), media_type=mt,
                    image_url=image, is_reel=is_reel or "/reel/" in (m.get("permalink") or ""),
                    media_id=str(m.get("id") or "") or None)


# --------------------------------------------------------------------------- HTTP / strategies
class StrategyError(Exception):
    """A strategy could not produce posts (message is shown in stats / on /status/)."""


class Skip(StrategyError):
    """Strategy not applicable (not configured / disabled) — not an error."""


class Fetcher:
    """All network access for this module: one polite session for instagram.com (never
    faster than one request per ~2.5–4 s), one for the image CDN, a run deadline, and
    'circuit breakers' so a 429 on one endpoint family stops further calls to it."""

    def __init__(self, cfg: dict, deadline: float, dry_run: bool = False):
        self.cfg = cfg
        self.deadline = deadline
        self.dry_run = dry_run
        crawler = (load_config().get("sources", {}) or {}).get("crawler", {}) or {}
        self.bot_ua = crawler.get("user_agent") or "NETA65-GrapevineCommitteeBot/2.0"
        # robots.txt of instagram.com disallows every generic agent, so PoliteSession's robots
        # check would refuse everything; we deliberately limit ourselves to a handful of
        # public embed URLs instead (see module docstring). retries=1: never retry a 429.
        self.ig = PoliteSession(user_agent=self.bot_ua, min_delay=2.5, respect_robots=False,
                                timeout=20, retries=1)
        self.cdn = PoliteSession(user_agent=self.bot_ua, min_delay=0.3, respect_robots=False,
                                 timeout=25, retries=2)
        self.web = PoliteSession(user_agent=self.bot_ua, min_delay=1.0, respect_robots=True,
                                 timeout=25, retries=2)
        self.blocked: dict[str, str] = {}      # family → reason ("embed", "api", "html")
        self.requests = 0

    def time_left(self) -> float:
        return self.deadline - time.monotonic()

    def ig_get(self, url: str, family: str, headers: dict | None = None) -> requests.Response:
        """GET an instagram.com URL or raise StrategyError. `family` groups endpoints that
        share a rate limit; after a 429 the whole family is skipped for the rest of the run."""
        if family in self.blocked:
            raise StrategyError(f"skipped ({family} {self.blocked[family]} earlier this run)")
        if self.time_left() < 15:
            raise StrategyError("run time budget used up")
        time.sleep(random.uniform(0.0, 1.5))          # jitter on top of the 2.5 s minimum gap
        h = {"User-Agent": self.bot_ua, **IFRAME_HEADERS}
        h.update(headers or {})
        self.requests += 1
        r = self.ig.get(url, headers=h)
        if r is None:
            raise StrategyError("network error")
        if r.status_code == 429:
            self.blocked[family] = "HTTP 429 (rate-limited)"
            raise StrategyError("HTTP 429 (rate-limited)")
        if r.status_code in (401, 403):
            raise StrategyError(f"HTTP {r.status_code} (login required / blocked)")
        if r.status_code != 200:
            raise StrategyError(f"HTTP {r.status_code}")
        if "/accounts/login" in (r.url or ""):
            raise StrategyError("redirected to login")
        return r

    # ------------------------------------------------------------------ 1. Graph API
    def graph_api(self, acct: dict, profile: dict) -> list[dict]:
        token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
        bid = os.environ.get("IG_BUSINESS_ID", "").strip()
        if not token or not bid:
            raise Skip("not configured (optional GitHub secrets IG_ACCESS_TOKEN + IG_BUSINESS_ID)")
        ver = (os.environ.get("IG_GRAPH_VERSION") or self.cfg.get("graph_version") or "v21.0").strip()
        user = acct["username"]
        fields = (f"business_discovery.username({user}){{username,name,followers_count,media_count,"
                  f"profile_picture_url,media.limit(30){{id,caption,media_type,media_url,thumbnail_url,"
                  f"permalink,timestamp,children{{media_url,media_type}}}}}}")
        self.requests += 1
        try:
            r = requests.get(f"https://graph.facebook.com/{ver}/{bid}",
                             params={"fields": fields, "access_token": token},
                             headers={"User-Agent": self.bot_ua}, timeout=30)
        except requests.RequestException as e:   # message may contain the URL → scrub token
            raise StrategyError(_scrub(f"network error: {type(e).__name__}", token))
        try:
            data = r.json()
        except ValueError:
            raise StrategyError(f"HTTP {r.status_code} (not JSON)")
        if "error" in data:
            err = data.get("error") or {}
            code = err.get("code")
            hint = {190: "IG_ACCESS_TOKEN expired or invalid — create a new token (see scripts/sync/instagram.py)",
                    10: "token lacks permission (instagram_basic, pages_read_engagement)",
                    200: "token lacks permission (instagram_basic, pages_read_engagement)",
                    100: "bad IG_BUSINESS_ID or the account is not a professional account",
                    4: "API rate limit", 17: "API rate limit", 32: "API rate limit", 613: "API rate limit",
                    110: "user not visible"}.get(code, "")
            msg = _scrub(str(err.get("message") or "")[:160], token)
            raise StrategyError(f"Graph API error {code}: {hint or msg}")
        bd = data.get("business_discovery") or {}
        profile.update({k: v for k, v in {
            "full_name": bd.get("name"), "followers": bd.get("followers_count"),
            "posts": bd.get("media_count"), "avatar_url": bd.get("profile_picture_url"),
        }.items() if v not in (None, "")})
        media = ((bd.get("media") or {}).get("data")) or []
        posts = [p for p in (post_from_graph_api(m, acct["key"], user) for m in media) if p]
        if not posts:
            raise StrategyError("no media returned")
        return posts

    # ------------------------------------------------------------------ 2. profile embed
    def profile_embed(self, acct: dict, profile: dict) -> list[dict]:
        user = acct["username"]
        r = self.ig_get(f"https://www.instagram.com/{user}/embed/", "embed",
                        {"Referer": _referer()})
        ctx = parse_context_json(r.text)
        if not ctx:
            raise StrategyError("no data in embed page (login wall?)")
        if str(ctx.get("username") or "").lower() != user.lower():
            raise StrategyError(f"embed returned another profile ({ctx.get('username')!r})")
        _profile_from(profile, ctx.get("full_name"), ctx.get("followers_count"), ctx.get("posts_count"),
                      ctx.get("profile_pic_url"), ctx.get("owner_id"))
        posts = []
        for g in ctx.get("graphql_media") or []:
            node = (g or {}).get("shortcode_media") or g
            p = post_from_graphql(node, acct["key"], "profile_embed", user)
            if p:
                posts.append(p)
        if not posts:
            raise StrategyError("embed page lists no posts")
        return posts

    # ------------------------------------------------------------------ 3. web_profile_info
    def web_profile_info(self, acct: dict, profile: dict) -> list[dict]:
        user = acct["username"]
        notes = []
        # This endpoint only answers requests that look like instagram.com's own web app
        # (mobile Safari for i.instagram.com, desktop Chrome for www), so these headers mimic it.
        for host, ua, site in (("i.instagram.com", MOBILE_UA, "same-site"),
                               ("www.instagram.com", DESKTOP_UA, "same-origin")):
            headers = {"User-Agent": ua, "x-ig-app-id": IG_APP_ID, "Accept": "*/*",
                       "Referer": f"https://www.instagram.com/{user}/", "X-Requested-With": "XMLHttpRequest",
                       "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": site}
            try:
                r = self.ig_get(f"https://{host}/api/v1/users/web_profile_info/?username={user}",
                                "api:" + host, headers)
                data = r.json()
            except StrategyError as e:
                notes.append(f"{host}: {e}")
                continue
            except ValueError:
                notes.append(f"{host}: not JSON (login wall?)")
                continue
            u = ((data or {}).get("data") or {}).get("user") or {}
            if not u:
                notes.append(f"{host}: no user in response")
                continue
            _profile_from(profile, u.get("full_name"), (u.get("edge_followed_by") or {}).get("count"),
                          (u.get("edge_owner_to_timeline_media") or {}).get("count"),
                          u.get("profile_pic_url_hd") or u.get("profile_pic_url"), u.get("id"))
            edges = ((u.get("edge_owner_to_timeline_media") or {}).get("edges")) or []
            posts = [p for p in (post_from_graphql((e or {}).get("node") or {}, acct["key"],
                                                   "web_profile_info", user) for e in edges) if p]
            if posts:
                return posts
            notes.append(f"{host}: no posts (private or empty?)")
        raise StrategyError("; ".join(notes) or "no response")

    # ------------------------------------------------------------------ 4. profile HTML
    def profile_html(self, acct: dict, profile: dict) -> list[dict]:
        """The public profile page. Served to our (non-browser) bot User-Agent it embeds the
        newest ~12 posts (incl. pinned ones) as JSON — verified Sept 2026. Served to a browser
        it is an empty login-wall shell."""
        user = acct["username"]
        r = self.ig_get(f"https://www.instagram.com/{user}/", "html",
                        {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Dest": "document",
                         "Sec-Fetch-Site": "none"})
        posts = parse_profile_html(r.text, acct["key"], user)
        if not posts:
            raise StrategyError("no posts in page (login wall)")
        return posts[:30]

    # ------------------------------------------------------------------ 5. RSSHub
    def rsshub(self, acct: dict, profile: dict) -> list[dict]:
        import feedparser

        instances = [str(i).strip() for i in (self.cfg.get("rsshub_instances") or []) if str(i).strip()]
        if not instances:
            raise Skip("no rsshub_instances configured")
        user = acct["username"]
        notes = []
        for inst in instances:
            url = inst.replace("{username}", user) if "{username}" in inst \
                else inst.rstrip("/") + f"/instagram/user/{user}"
            if self.time_left() < 15:
                notes.append("time budget used up")
                break
            self.requests += 1
            r = self.web.get(url)
            if r is None or r.status_code != 200:
                notes.append(f"{urlparse(url).netloc}: {'error' if r is None else 'HTTP %s' % r.status_code}")
                continue
            feed = feedparser.parse(r.content)
            posts = []
            for e in feed.entries or []:
                sc, is_reel = parse_post_url(e.get("link") or e.get("id") or "")
                if not sc:
                    continue
                desc = e.get("summary") or e.get("description") or ""
                img = re.search(r'<img[^>]+src="([^"]+)"', desc)
                media = (e.get("media_content") or [{}])[0].get("url") if e.get("media_content") else None
                enc = next((x.get("href") for x in e.get("enclosures") or []
                            if str(x.get("type", "")).startswith("image")), None)
                published = e.get("published_parsed") or e.get("updated_parsed")
                posts.append(new_post(
                    sc, acct["key"], "rsshub", username=user,
                    caption=strip_html(desc) or clean_text(e.get("title")) or None,
                    date=(to_iso(datetime(*published[:6], tzinfo=timezone.utc)) if published else None)
                    or date_from_shortcode(sc),
                    image_url=(img.group(1).replace("&amp;", "&") if img else None) or media or enc,
                    is_reel=is_reel))
            if posts:
                return posts
            notes.append(f"{urlparse(url).netloc}: feed has no posts")
        raise StrategyError("; ".join(notes))

    # ------------------------------------------------------------------ 6. hover card
    def embed_hovercard(self, acct: dict, profile: dict, seed: str | None = None) -> list[dict]:
        if not seed:
            raise Skip("no known post to start from")
        r = self.ig_get(embed_url(seed), "embed", {"Referer": _referer()})
        data = parse_post_embed(r.text)
        if not data:
            raise StrategyError("seed post embed unusable")
        if data.get("username") and data["username"].lower() != acct["username"].lower():
            raise StrategyError("seed post belongs to another account")
        posts = [new_post(sc, acct["key"], "embed_hovercard", username=acct["username"],
                          date=date_from_shortcode(sc), verify=True)
                 for sc in data.get("hover") or [] if date_from_shortcode(sc)]
        if not posts:
            raise StrategyError("hover card lists no posts")
        return posts

    # ------------------------------------------------------------------ post embed (enrichment)
    def post_embed(self, sc: str) -> dict | None:
        """Returns parsed embed fields, None if the post has no usable embed, raises
        StrategyError on network trouble / rate limit."""
        r = self.ig_get(embed_url(sc), "embed", {"Referer": _referer()})
        return parse_post_embed(r.text)


def _scrub(msg: str, secret: str) -> str:
    return msg.replace(secret, "***") if secret else msg


def _referer() -> str:
    url = (load_config().get("site", {}) or {}).get("url") or "https://github.com/"
    return url.rstrip("/") + "/"


def _profile_from(profile: dict, full_name, followers, posts, avatar_url, owner_id) -> None:
    for k, v in (("full_name", full_name), ("followers", followers), ("posts", posts),
                 ("avatar_url", avatar_url), ("owner_id", owner_id)):
        if v not in (None, ""):
            profile[k] = str(v) if k == "owner_id" else v


# --------------------------------------------------------------------------- thumbnails
def thumb_rel(sc: str) -> str:
    return f"{THUMB_URL}{sc}.webp"


def thumb_file(rel: str | None) -> Path | None:
    """'/assets/cache/ig/X.webp' → absolute path (only for files inside our cache folder)."""
    if not rel or not str(rel).startswith(THUMB_URL):
        return None
    name = str(rel)[len(THUMB_URL):]
    if "/" in name or "\\" in name or not name.endswith(".webp"):
        return None
    return THUMB_DIR / name


def thumb_exists(rel: str | None) -> bool:
    p = thumb_file(rel)
    return bool(p and p.exists() and p.stat().st_size > 0)


def download_image(fx: Fetcher, url: str, dest: Path, max_w: int, max_h: int,
                   quality: int = THUMB_QUALITY) -> bool:
    """Download an (expiring) CDN image right away and store a small WebP copy."""
    from PIL import Image, ImageOps

    if fx.dry_run or not url or not url.startswith(("http://", "https://")):
        return False
    if fx.time_left() < 10:
        return False
    r = fx.cdn.get(url, headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                                 "Referer": "https://www.instagram.com/"})
    if r is None or r.status_code != 200:
        log.info("image download failed (%s) for %s", "error" if r is None else r.status_code, dest.name)
        return False
    ctype = r.headers.get("Content-Type", "")
    if not ctype.startswith("image/") or len(r.content) > IMAGE_MAX_BYTES:
        log.info("not an image / too big (%s, %d bytes) for %s", ctype, len(r.content), dest.name)
        return False
    try:
        with Image.open(io.BytesIO(r.content)) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            im.save(tmp, "WEBP", quality=quality, method=6)
        os.replace(tmp, dest)
        return True
    except Exception as e:   # corrupt image, unsupported format…
        log.warning("could not convert image for %s: %s", dest.name, e)
        return False


def ensure_thumb(fx: Fetcher, sc: str, image_url: str | None, stats: dict) -> str | None:
    """Local thumbnail path if we have (or can now make) one."""
    rel = thumb_rel(sc)
    if thumb_exists(rel):
        return rel
    if image_url and download_image(fx, image_url, THUMB_DIR / f"{sc}.webp", THUMB_MAX_W, THUMB_MAX_H):
        stats["thumbs_downloaded"] = stats.get("thumbs_downloaded", 0) + 1
        return rel
    return None


def ensure_avatar(fx: Fetcher, key: str, profile: dict) -> None:
    """Small profile picture for the follow cards (refreshed weekly).

    Freshness comes from the download time saved in the envelope (profile["_avatar_checked"]), not
    from the file's modification time: a fresh git checkout (every CI run) resets file times."""
    rel = f"{THUMB_URL}{AVATAR_PREFIX}{key}.webp"
    dest = THUMB_DIR / f"{AVATAR_PREFIX}{key}.webp"
    checked = parse_iso(profile.get("_avatar_checked"))
    fresh = (dest.exists() and checked is not None
             and (datetime.now(timezone.utc) - checked).total_seconds() < AVATAR_REFRESH_DAYS * 86400)
    if not fresh and profile.get("avatar_url"):
        if download_image(fx, profile["avatar_url"], dest, AVATAR_MAX, AVATAR_MAX, quality=80):
            profile["_avatar_checked"] = now_iso()
    if dest.exists():
        profile["avatar"] = rel


# --------------------------------------------------------------------------- manual list
def load_manual(accounts: list[dict], path: Path = MANUAL_FILE) -> tuple[list[dict], list[str]]:
    """content/instagram.yml → records (+ list of problems to report, never fatal)."""
    problems: list[str] = []
    if not path.exists():
        return [], problems
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return [], [f"{path.name}: YAML error — {str(e).splitlines()[0][:120]}"]
    entries = data.get("posts") if isinstance(data, dict) else data
    keys = {a["key"] for a in accounts}
    by_user = {a["username"].lower(): a["key"] for a in accounts}
    out: list[dict] = []
    for n, e in enumerate(entries or [], 1):
        if isinstance(e, str):
            e = {"url": e}
        if not isinstance(e, dict):
            problems.append(f"{path.name} entry {n}: not understood")
            continue
        url = str(e.get("url") or e.get("link") or "").strip()
        sc, is_reel = parse_post_url(url)
        sc = sc or (str(e.get("shortcode") or "").strip() or None)
        if not sc or not SHORTCODE_RE.match(sc):
            problems.append(f"{path.name} entry {n}: no Instagram post link found in {url[:60]!r}")
            continue
        acct = str(e.get("account") or "").strip().lower().lstrip("@") or None
        acct = by_user.get(acct or "", acct)
        if acct and acct not in keys:
            problems.append(f"{path.name} entry {n}: unknown account {acct!r} (use gv or lv)")
            acct = None
        cap = e.get("caption")
        out.append(new_post(sc, acct, "manual", caption=(str(cap).strip() if cap else None),
                            date=ts_to_iso(e.get("date")) or date_from_shortcode(sc),
                            is_reel=is_reel, manual=True))
    return out, problems


# --------------------------------------------------------------------------- items
def build_item(rec: dict, accounts_by_key: dict, prev: dict | None) -> dict:
    """Record (+ previously saved item) → schema Item."""
    key = rec.get("account") or ((prev or {}).get("extra") or {}).get("account") or "gv"
    acct = accounts_by_key.get(key) or {}
    prior = "es" if key == "lv" else "en"
    name = acct.get("name") or "Instagram"
    fallback = f"{name} en Instagram" if prior == "es" else f"{name} on Instagram"

    caption = rec.get("caption")               # None = this record doesn't know the caption
    pe = (prev or {}).get("extra") or {}
    cap_clean = clean_caption(caption)
    sc = rec["shortcode"]
    is_reel = bool(rec.get("is_reel") or pe.get("is_reel"))
    thumb = rec.get("thumb") or (pe.get("thumb") if thumb_exists(pe.get("thumb")) else None)
    username = rec.get("username") or pe.get("username") or acct.get("username")
    permalink = post_url(sc, is_reel)

    if caption is None and prev and pe.get("caption_known"):
        # keep what an earlier run learned instead of falling back to a generic title
        title, summary, lang, tags = prev.get("title"), prev.get("summary"), prev.get("lang"), prev.get("tags")
    else:
        title = title_from_caption(cap_clean, fallback)
        summary = summary_from_caption(cap_clean)
        lang = detect_lang(cap_clean, prior) if cap_clean else prior
        tags = hashtags(caption)

    item = make_item(
        id=f"ig:{sc}", source=SOURCE, kind="post", url=permalink,
        title=title or fallback, summary=summary or "", lang=lang or prior,
        date=rec.get("date") or (prev or {}).get("date") or date_from_shortcode(sc),
        image=thumb, tags=tags or [], category=key,
        extra={
            "shortcode": sc, "account": key, "username": username,
            "media_type": rec.get("media_type") or pe.get("media_type"),
            "thumb": thumb, "embed_url": embed_url(sc), "permalink": permalink,
            "is_reel": is_reel, "manual": bool(rec.get("manual")),
            "strategy": rec.get("strategy") or pe.get("strategy"),
            "caption_known": caption is not None or bool(pe.get("caption_known")),
            "embed_checked": rec.get("embed_checked") or pe.get("embed_checked"),
        },
    )
    return item


def fix_missing_thumbs(items: list[dict]) -> None:
    """merge_items() restores an old image path when the new record has none — drop paths
    whose file no longer exists so the site never shows a broken image."""
    for it in items:
        ex = it.setdefault("extra", {})
        if it.get("image") and not thumb_exists(it["image"]):
            it["image"] = None
        if ex.get("thumb") and not thumb_exists(ex["thumb"]):
            ex["thumb"] = None
        if not it.get("image") and ex.get("thumb"):
            it["image"] = ex["thumb"]


# --------------------------------------------------------------------------- enrichment
def _days_since(iso: str | None) -> float:
    dt = parse_iso(iso) if iso else None
    if not dt:
        return 1e9
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def needs_enrichment(rec: dict, prev: dict | None) -> bool:
    if rec.get("verify"):
        return True
    pe = (prev or {}).get("extra") or {}
    missing = (
        (rec.get("caption") is None and not pe.get("caption_known"))
        or (not rec.get("image_url") and not thumb_exists(thumb_rel(rec["shortcode"])))
        or not (rec.get("media_type") or pe.get("media_type"))
    )
    if not missing:
        return False
    return _days_since(rec.get("embed_checked") or pe.get("embed_checked")) >= ENRICH_RETRY_DAYS


def enrich(fx: Fetcher, records: dict[str, dict], prev_by_sc: dict[str, dict], cap: int,
           accounts: list[dict], stats: dict) -> None:
    """Fill caption / image URL / type from each post's public embed page (in place).
    Records flagged verify=True are removed unless their embed confirms them."""
    key_by_user = {a["username"].lower(): a["key"] for a in accounts}
    todo = [r for r in records.values() if needs_enrichment(r, prev_by_sc.get(r["shortcode"]))]
    todo.sort(key=lambda r: (not r.get("verify"), not r.get("manual"), _neg_date(r.get("date"))))
    done = 0
    for rec in todo:
        sc = rec["shortcode"]
        if done >= cap:
            break
        try:
            data = fx.post_embed(sc)
        except StrategyError as e:
            stats["enrich_failed"] = stats.get("enrich_failed", 0) + 1
            log.info("embed %s: %s", sc, e)
            if "embed" in fx.blocked or fx.time_left() < 15:
                stats.setdefault("warnings", []).append(f"post embeds: {e}")
                break
            continue
        done += 1
        rec["embed_checked"] = now_iso()
        rec["checked_now"] = True
        if not data:
            stats["enrich_failed"] = stats.get("enrich_failed", 0) + 1
            log.info("embed %s: no usable embed (deleted, private or login wall)", sc)
            if rec.get("verify"):
                records.pop(sc, None)
            continue
        user = (data.get("username") or "").lower()
        if rec.get("verify") and user and user not in key_by_user and not rec.get("manual"):
            log.info("embed %s: belongs to @%s, not one of our accounts — skipped", sc, user)
            records.pop(sc, None)
            continue
        if not rec.get("account") and user in key_by_user:
            rec["account"] = key_by_user[user]
        if rec.get("caption") is None or (rec.get("manual") and not rec.get("caption")):
            rec["caption"] = data.get("caption", "")
        for k in ("username", "media_type", "image_url", "media_id"):
            if not rec.get(k) and data.get(k):
                rec[k] = data[k]
        if not rec.get("date"):
            rec["date"] = date_from_media_id(data.get("media_id")) or date_from_shortcode(sc)
        rec["verify"] = False
        stats["enriched"] = stats.get("enriched", 0) + 1
    # anything still unverified (cap / time budget reached) waits for the next run
    for sc in [s for s, r in records.items() if r.get("verify")]:
        records.pop(sc, None)


def _neg_date(d: str | None) -> float:
    dt = parse_iso(d) if d else None
    return -(dt.timestamp()) if dt else 0.0


# --------------------------------------------------------------------------- pruning
def prune(items: list[dict], keep: int) -> tuple[list[dict], list[dict]]:
    """Keep the newest `keep` automatic posts per account (+ every manual post)."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it.get("category") or "?", []).append(it)
    kept, removed = [], []
    for lst in groups.values():
        auto = sorted((i for i in lst if not (i.get("extra") or {}).get("manual")),
                      key=lambda i: str(i.get("date") or i.get("first_seen") or ""), reverse=True)
        kept += auto[:keep] + [i for i in lst if (i.get("extra") or {}).get("manual")]
        removed += auto[keep:]
    return kept, removed


def cleanup_thumbs(kept: list[dict], removed: list[dict], dry_run: bool) -> int:
    """Delete thumbnails of pruned posts, plus orphans no kept post uses (never avatars).

    Orphans are found by comparing with the kept posts only — not by file age, because a fresh git
    checkout (every CI run) gives every file the same new modification time."""
    if dry_run or not THUMB_DIR.exists():
        return 0
    used: set[str] = set()
    for i in kept:
        ex = i.get("extra") or {}
        for ref in (ex.get("thumb"), i.get("image")):
            p = thumb_file(ref)
            if p:
                used.add(p.name)
        if ex.get("shortcode"):
            used.add(f"{ex['shortcode']}.webp")     # the post's own thumbnail, even if unlinked today
    n = 0
    for it in removed:
        p = thumb_file((it.get("extra") or {}).get("thumb") or it.get("image"))
        if p and p.name not in used and p.exists():
            p.unlink()
            n += 1
    if kept:   # safety: never sweep the folder when we somehow have no items at all
        for p in THUMB_DIR.glob("*.webp"):
            if p.name.startswith(AVATAR_PREFIX) or p.name in used:
                continue
            p.unlink()
            n += 1
    for p in THUMB_DIR.glob("*.part"):
        p.unlink(missing_ok=True)
    return n


# --------------------------------------------------------------------------- per-account fetch
def fetch_account(fx: Fetcher, acct: dict, profile: dict, order: list[str], anonymous: bool,
                  prev_items: list[dict], records: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Try the strategies in order; the first that returns posts wins.
    Returns (posts, human-readable log of every attempt for /status/)."""
    key, user = acct["key"], acct["username"]
    attempts: list[str] = []
    got: list[dict] = []
    for strat in order:
        if strat in ANONYMOUS_STRATEGIES and not anonymous:
            attempts.append(f"{strat}: off (anonymous: false)")
            continue
        try:
            if strat == "embed_hovercard":
                got = fx.embed_hovercard(acct, profile, _newest_known(prev_items, records, key))
            else:
                got = getattr(fx, strat)(acct, profile)
        except Skip as e:
            attempts.append(f"{strat}: skipped — {e}")
            continue
        except StrategyError as e:
            attempts.append(f"{strat}: {e}")
            log.info("@%s %s: %s", user, strat, e)
            continue
        except Exception as e:   # a parsing bug in one strategy must not stop the others
            attempts.append(f"{strat}: crashed ({type(e).__name__}: {str(e)[:80]})")
            log.exception("@%s %s crashed", user, strat)
            continue
        attempts.append(f"{strat}: {len(got)} posts")
        log.info("@%s: %d posts via %s", user, len(got), strat)
        break

    # Gap fill: the embed only lists ~6 posts. If they don't reach back to the newest post we
    # already had (first run, or the job didn't run for a few days), also read the profile
    # page, which lists ~12.
    if (got and len(got) < 12 and anonymous and "profile_html" in order
            and got[0]["strategy"] != "profile_html" and _has_gap(prev_items, key, got)):
        try:
            more = fx.profile_html(acct, profile)
            have = {g["shortcode"] for g in got}
            got += [p for p in more if p["shortcode"] not in have]
            attempts.append(f"profile_html (gap fill): {len(more)} posts")
        except StrategyError as e:
            attempts.append(f"profile_html (gap fill): {e}")
        except Exception as e:
            attempts.append(f"profile_html (gap fill): crashed ({type(e).__name__})")
            log.exception("@%s gap fill crashed", user)
    return got, attempts


# --------------------------------------------------------------------------- main
def _anonymous_allowed(cfg: dict) -> bool:
    env = os.environ.get("IG_ANONYMOUS", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return cfg.get("anonymous", True) is not False


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m scripts.sync.instagram", description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and print, write nothing")
    ap.add_argument("--account", action="append", help="only check this account key/username (repeatable)")
    ap.add_argument("--strategies", help=f"comma list overriding the order ({','.join(ALL_STRATEGIES)})")
    ap.add_argument("--enrich-cap", type=int, default=None, help="max post embeds to fetch (default 25)")
    ap.add_argument("--no-enrich", action="store_true", help="skip post-embed enrichment")
    ap.add_argument("--keep", type=int, default=None, help="posts kept per account (default 60)")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES, help="time budget")
    ap.add_argument("--manual-file", type=Path, default=MANUAL_FILE, help=argparse.SUPPRESS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.verbose:
        log.setLevel("DEBUG")

    cfg = ((load_config().get("sources") or {}).get("instagram")) or {}
    accounts = [a for a in (cfg.get("accounts") or []) if isinstance(a, dict) and a.get("username") and a.get("key")]
    accounts_by_key = {a["key"]: a for a in accounts}
    keep = args.keep or int(cfg.get("keep_per_account") or DEFAULT_KEEP_PER_ACCOUNT)
    enrich_cap = 0 if args.no_enrich else (args.enrich_cap if args.enrich_cap is not None
                                           else int(cfg.get("enrich_per_run") or DEFAULT_ENRICH_PER_RUN))
    order = [s.strip() for s in (args.strategies or ",".join(ALL_STRATEGIES)).split(",") if s.strip()]
    bad = [s for s in order if s not in ALL_STRATEGIES]
    if bad:
        ap.error(f"unknown strategies: {bad}")
    anonymous = _anonymous_allowed(cfg)
    selected = [a for a in accounts if not args.account
                or a["key"] in args.account or a["username"] in args.account]

    prev_env = load_raw(SOURCE)
    prev_items = [i for i in prev_env.get("items", []) if i.get("id", "").startswith("ig:")]
    prev_by_sc = {(i.get("extra") or {}).get("shortcode") or i["id"][3:]: i for i in prev_items}
    profiles: dict[str, dict] = {k: dict(v) for k, v in (prev_env.get("profiles") or {}).items()
                                 if isinstance(v, dict)}
    stats: dict[str, Any] = {"strategy": {}, "attempts": {}, "warnings": []}
    fx = Fetcher(cfg, deadline=time.monotonic() + args.max_minutes * 60, dry_run=args.dry_run)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 1. automatic strategies, per account -----------------------------------------
    records: dict[str, dict] = {}
    failures: list[tuple[str, list[str]]] = []
    for acct in selected:
        key, user = acct["key"], acct["username"]
        profile = profiles.setdefault(key, {})
        profile.update({"username": user, "name": acct.get("name") or user,
                        "url": f"https://www.instagram.com/{user}/"})
        got, attempts = fetch_account(fx, acct, profile, order, anonymous, prev_items, records)
        stats["attempts"][key] = attempts
        # a configured-but-broken API token (e.g. expired) must be visible on /status/ even
        # when an anonymous fallback saved the day
        stats["warnings"] += [f"@{user} {a}" for a in attempts
                              if a.startswith("graph_api:") and "skipped" not in a and not a.endswith(" posts")]
        if got:
            stats["strategy"][key] = got[0]["strategy"]
            profile["checked"] = now_iso()
            for rec in got:
                sc = rec["shortcode"]
                records[sc] = merge_post(records[sc], rec) if sc in records else rec
        else:
            stats["strategy"][key] = None
            failures.append((user, attempts))
    stats["fetched"] = len(records)

    # ---- 2. manual list (always) ------------------------------------------------------
    manual, problems = load_manual(accounts, args.manual_file)
    stats["warnings"] += problems
    manual_scs = {m["shortcode"] for m in manual}
    for rec in manual:
        sc = rec["shortcode"]
        records[sc] = merge_post(records[sc], rec) if sc in records else rec
    stats["manual"] = len(manual)
    # posts that were only on the manual list and have been removed from it disappear
    dropped_manual = [i for i in prev_items if (i.get("extra") or {}).get("manual")
                      and (i.get("extra") or {}).get("shortcode") not in manual_scs
                      and (i.get("extra") or {}).get("shortcode") not in records]
    drop_ids = {i["id"] for i in dropped_manual}

    # ---- 3. known posts that still miss something get a chance at enrichment ----------
    for sc, it in prev_by_sc.items():
        if sc in records or it["id"] in drop_ids:
            continue
        ex = it.get("extra") or {}
        stub = new_post(sc, ex.get("account") or it.get("category"), ex.get("strategy") or "known",
                        username=ex.get("username"), date=it.get("date"),
                        media_type=ex.get("media_type"), is_reel=ex.get("is_reel"),
                        embed_checked=ex.get("embed_checked"), stub=True)
        if needs_enrichment(stub, it):
            records[sc] = stub
    if enrich_cap > 0 and anonymous:      # post embeds are an anonymous request too
        enrich(fx, records, prev_by_sc, enrich_cap, accounts, stats)
    for sc in [s for s, r in records.items()
               if r.get("verify") or (r.get("stub") and not r.get("checked_now"))]:
        records.pop(sc, None)             # unconfirmed / untouched → nothing to update

    # ---- 4. thumbnails (CDN links expire — download now) + items ----------------------
    new_items = []
    for sc, rec in sorted(records.items(), key=lambda kv: _neg_date(kv[1].get("date"))):
        if not rec.get("account"):
            rec["account"] = "gv"
        rec["thumb"] = ensure_thumb(fx, sc, rec.get("image_url"), stats)
        new_items.append(build_item(rec, accounts_by_key, prev_by_sc.get(sc)))

    base = [i for i in prev_items if i["id"] not in drop_ids]
    merged, added = merge_items(base, new_items)
    fix_missing_thumbs(merged)
    kept, removed = prune(merged, keep)
    stats["pruned"] = len(removed) + len(dropped_manual)
    stats["thumbs_deleted"] = cleanup_thumbs(kept, removed + dropped_manual, args.dry_run)

    for key, profile in profiles.items():
        if key in accounts_by_key:
            ensure_avatar(fx, key, profile)
        profile.pop("avatar_url", None)     # CDN avatar URLs expire; never store them

    stats.update({
        "new": added, "total": len(kept),
        "per_account": {k: sum(1 for i in kept if i.get("category") == k) for k in accounts_by_key},
        "requests": fx.requests, "anonymous": anonymous,
        "graph_api": bool(os.environ.get("IG_ACCESS_TOKEN") and os.environ.get("IG_BUSINESS_ID")),
    })
    if not stats["warnings"]:
        stats.pop("warnings")
    ok = not failures
    error = None if ok else _failure_message(failures, anonymous, stats["graph_api"])

    if args.dry_run:
        _print_dry_run(kept, stats, error, profiles)
        return
    save_raw(SOURCE, kept, ok=ok, error=error, stats=stats, extra={"profiles": profiles})
    log.info("saved %d posts (%d new) — %s", len(kept), added,
             ", ".join(f"{k}: {v or 'FAILED'}" for k, v in stats["strategy"].items()))
    if error:
        log.warning(error)


def _failure_message(failures: list[tuple[str, list[str]]], anonymous: bool, graph: bool) -> str:
    """Short, actionable text for /status/ (full details stay in stats.attempts)."""
    users = ", ".join("@" + u for u, _ in failures)
    if not anonymous and not graph:
        return (f"No posts fetched for {users}: the automatic check is switched off "
                "(sources.instagram.anonymous: false) and IG_ACCESS_TOKEN / IG_BUSINESS_ID are not set.")
    reasons: list[str] = []
    for _, attempts in failures:
        for a in attempts:
            if "skipped" in a or "off (" in a or "earlier this run" in a:
                continue
            r = a.rsplit(": ", 1)[-1]
            if r not in reasons:
                reasons.append(r)
    why = "; ".join(reasons)[:140] or "no method available"
    return (f"No new posts for {users} ({why}). Posts already on the site are kept. Usually temporary; "
            "if it lasts, see the optional API setup in scripts/sync/instagram.py.")[:300]


def _has_gap(prev_items: list[dict], key: str, got: list[dict]) -> bool:
    """True when the oldest freshly fetched post is newer than every post we already had."""
    known = [parse_iso(i.get("date")) for i in prev_items
             if i.get("category") == key and not (i.get("extra") or {}).get("manual")]
    known = [d for d in known if d]
    fresh = [d for d in (parse_iso(p.get("date")) for p in got) if d]
    if not known:
        return True
    return bool(fresh) and min(fresh) > max(known)


def _newest_known(prev_items: list[dict], records: dict[str, dict], key: str) -> str | None:
    cands = [(i.get("date") or "", (i.get("extra") or {}).get("shortcode")) for i in prev_items
             if i.get("category") == key and i.get("status", "ok") == "ok"]
    cands += [(r.get("date") or "", r["shortcode"]) for r in records.values() if r.get("account") == key]
    cands = [c for c in cands if c[1]]
    return max(cands)[1] if cands else None


def _print_dry_run(items: list[dict], stats: dict, error: str | None, profiles: dict) -> None:
    print(json.dumps({"ok": not error, "error": error, "stats": stats, "profiles": profiles},
                     ensure_ascii=False, indent=1))
    for it in items[:12]:
        ex = it.get("extra") or {}
        print(f"- {it['date']} [{it['category']}/{ex.get('media_type')}/{it['lang']}] {it['id']} "
              f"via {ex.get('strategy')}: {it['title'][:70]!r} img={bool(it.get('image'))}")


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
