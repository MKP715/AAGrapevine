"""Record-your-story phone lines of Grapevine and La Viña → data/raw/audio_project.json

Both magazines take short recorded stories from AA members: Grapevine's Audio Project
(aagrapevine.org/audio-portal: a voicemail line, or a recording sent by e-mail; accepted stories go on
the AA Grapevine YouTube channel) and La Viña's "Graba tu historia" (aalavina.org/graba-tu-historia and
its instructions page: a voicemail line, or a recording sent by e-mail). This module only READS those
official pages so /contribute/ ("Record your story by phone") always shows the phone numbers, the keys
to press, the e-mail addresses, the length and the links the magazines give today. The steps are worded
by our site (src/_i18n/community.json → community.rec.*, hand-written English and Spanish) around the
values read here — never machine-translated.

Pages (config/site.yml → sources.grapevine.audio_project, sources.lavina.record_story /
record_instructions / record_tips / record_topics / sample_audio; defaults below). Cost: 3 page requests
a day (GV page, LV page, LV instructions), all through the shared polite session (5 s between requests).

Output (docs/DATA_SCHEMA.md → "audio_project"): items
    audio:gv   kind "audio_project"  extra: page_url, phone, tel, minutes_min, minutes_max, keys
               {record, finish, save, permission}, email, formats, no_speakers, channel_url,
               playlists [{title, url}], steps_text, checked
    audio:lv   kind "audio_project"  extra: page_url, instructions_url, tips_url, topics_url, sample_url,
               phone, tel, minutes_max, keys {record}, permission_text, long_distance, email, formats,
               no_speakers, steps_text, checked
build_data.py turns them into data/site/audio_project.json ({gv, lv, checked}).

Each publication is independent: a page that cannot be fetched or is not understood (a number, key or
e-mail address missing — a layout or process change) keeps the previous data of that part and the
envelope is marked ok=false with the reason (→ /status/), so the site never shows half-read steps.

Run:  python -m scripts.sync.audio_project [--dry-run] [--html-dir DIR] [--save-html DIR]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from .common import (clean_text, get_logger, load_config, load_raw, make_item, merge_items, now_iso, run_module,
                     save_raw, shared_session, slugify)

SOURCE = "audio_project"
log = get_logger(SOURCE)

# Defaults — config/site.yml sources.grapevine / sources.lavina override each of them.
DEFAULTS = {
    "gv": {"cfg": "grapevine", "base": "https://www.aagrapevine.org", "page": ("audio_project", "/audio-portal")},
    "lv": {"cfg": "lavina", "base": "https://www.aalavina.org", "page": ("record_story", "/graba-tu-historia"),
           "instructions": ("record_instructions", "/instrucciones-graba-tu-historia"),
           "tips": ("record_tips", "/consejos-de-grabacion"), "topics": ("record_topics", "/temas-sugeridos"),
           "sample": ("sample_audio", "/audio-de-muestra")},
}

# "559-726-1216", "(559) 670-1601", "+1 559.726.1216" (North American numbers only)
PHONE_RE = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-](\d{4})(?![\d-])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
FORMAT_RE = re.compile(r"\b(WAV|MP3|M4A|AAC|OGG|FLAC|WMA)\b", re.I)
KEY = r"([0-9#*])"
# Grapevine's voicemail steps (the "Audio Project Voicemail System" list item)
GV_KEYS = {
    "record": re.compile(rf"(?i)\bpress\s+{KEY}\s+to\s+(?:hear|begin|start|record)"),
    "finish": re.compile(rf"(?i)\bfinish(?:ed)?\b[^.]{{0,20}}?\bpress\s+{KEY}"),
    "save": re.compile(rf"(?i)\bpress\s+{KEY}\s+to\s+save"),
    "permission": re.compile(rf"(?i)\bpress\s+{KEY}\s+to\s+give\b[^.]{{0,60}}permission"),
}
LV_RECORD_KEY = re.compile(rf"(?i)\bpresion\w*\s+(?:el\s+)?{KEY}\s+para\s+grabar")
GV_MINUTES = re.compile(r"(?i)(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*min")
UPTO_MINUTES = re.compile(r"(?i)(?:hasta|up\s+to|no\s+more\s+than|m[áa]ximo\s+de)\s+(\d{1,2})\s*min")
GV_NO_SPEAKERS = re.compile(r"(?i)does\s+not\s+collect\s+recordings")
LV_NO_SPEAKERS = re.compile(r"(?i)no\s+recopila\s+grabaciones")
LONG_DISTANCE = re.compile(r"(?i)larga\s+distancia|long[\s-]distance")
QUOTE_RE = re.compile(r"[“\"«]\s*(.{40,700}?)\s*[”\"»]", re.S)
CF_PATH = "/cdn-cgi/l/email-protection"


class AudioParseError(ValueError):
    """The page was fetched but what we need is not on it (layout or process change)."""


# --------------------------------------------------------------------------- settings
def settings(cfg: dict | None = None) -> dict[str, dict]:
    """Absolute URLs per publication (config/site.yml sources.<grapevine|lavina> over the defaults)."""
    cfg = cfg if cfg is not None else load_config()
    src = cfg.get("sources") or {}
    out: dict[str, dict] = {}
    for pub, d in DEFAULTS.items():
        c = src.get(d["cfg"]) or {}
        base = str(c.get("base") or d["base"]).rstrip("/")
        urls = {}
        for field, spec in d.items():
            if field in ("cfg", "base"):
                continue
            key, default = spec
            v = str(c.get(key) or default)
            urls[field] = v if re.match(r"(?i)https?://", v) else base + "/" + v.lstrip("/")
        out[pub] = {"pub": pub, "base": base, **urls}
    return out


# --------------------------------------------------------------------------- small helpers
def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _content(soup: BeautifulSoup):
    """The page's own content block (Drupal), without menus, footers or the cart widget."""
    main = soup.find("main") or soup.body or soup
    block = main.select_one("#block-neatosub-content") or main
    for t in block.find_all(["script", "style", "noscript", "form", "nav"]):
        t.decompose()
    return block


def _text(el) -> str:
    """Text of an element with <br> as line breaks (lines cleaned; bullets and odd characters dropped)."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    lines = []
    for ln in el.get_text("").split("\n"):
        ln = clean_text(re.sub(r"^[\s•·●▪◦\-–�]+", "", ln))
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def decode_cfemail(hexstr: str) -> str | None:
    """Cloudflare's e-mail obfuscation: the first byte is the XOR key of the rest."""
    try:
        data = bytes.fromhex(hexstr.strip())
        if len(data) < 2:
            return None
        s = bytes(b ^ data[0] for b in data[1:]).decode("utf-8")
    except ValueError:
        return None
    return s if EMAIL_RE.fullmatch(s) else None


def emails(el) -> list[str]:
    """E-mail addresses in an element, in page order: mailto: links, Cloudflare-protected links and
    spans (decoded), then plain text."""
    out: list[str] = []

    def add(e: str | None) -> None:
        e = (e or "").strip().lower()
        if e and EMAIL_RE.fullmatch(e) and e not in out:
            out.append(e)

    for tag in el.find_all(True):
        href = tag.get("href") or ""
        if tag.name == "a" and href.lower().startswith("mailto:"):
            add(href[7:].split("?")[0])
        elif tag.name == "a" and CF_PATH in href and "#" in href:
            add(decode_cfemail(href.split("#", 1)[1]))
        if tag.get("data-cfemail"):
            add(decode_cfemail(tag["data-cfemail"]))
    for m in EMAIL_RE.finditer(el.get_text(" ")):
        add(m.group(0))
    return out


def phones(text: str) -> list[tuple[str, str]]:
    """[(as written, "+1NNNNNNNNNN"), …] in text order, each number once."""
    out, seen = [], set()
    for m in PHONE_RE.finditer(text or ""):
        tel = "+1" + "".join(m.groups())
        if tel not in seen:
            seen.add(tel)
            out.append((clean_text(m.group(0)), tel))
    return out


def formats(text: str) -> list[str]:
    out: list[str] = []
    for m in FORMAT_RE.finditer(text or ""):
        f = m.group(1).upper()
        if f not in out:
            out.append(f)
    return out


def _block_with(block, pattern: re.Pattern, names=("li", "p", "div")):
    """The smallest element (of `names`) whose text matches."""
    best = None
    for el in block.find_all(list(names)):
        if pattern.search(el.get_text(" ")):
            if best is None or len(el.get_text(" ")) < len(best.get_text(" ")):
                best = el
    return best


def _abs(page_url: str, href: str | None) -> str | None:
    href = (href or "").strip()
    return urljoin(page_url, href) if href else None


def _yt_title(a) -> str:
    """'Sponsorship, click here' → 'Sponsorship'; a bare 'click here' takes the heading before it."""
    t = clean_text(a.get_text(" "))
    t = re.sub(r"(?i)[,:\s–—-]*(?:click|haz\s+clic|pulsa)\s+(?:here|aqu[ií])[.!]*$", "", t).strip(" .,:–—-")
    if not t:
        prev = a.find_previous(["strong", "h2", "h3", "h4"])
        t = clean_text(prev.get_text(" ")) if prev else ""
    return t


# --------------------------------------------------------------------------- Grapevine
def parse_gv(html: str, page_url: str) -> dict:
    """Grapevine's Audio Project page → the fields in extra of audio:gv. Raises AudioParseError."""
    soup = _soup(html)
    block = _content(soup)
    text = block.get_text(" ")
    if not re.search(r"(?i)audio\s+project|audio\s+stor", text):
        raise AudioParseError("page does not look like the Audio Project page (maintenance? redirect?)")

    li = _block_with(block, re.compile(r"(?i)voicemail"), ("li",)) or _block_with(block, GV_KEYS["record"], ("li", "p"))
    if li is None:
        raise AudioParseError("no voicemail instructions found")
    steps = _text(li).split("\n")
    steps_text = [s for s in steps if len(s) > 3 and not re.fullmatch(r"(?i)the audio project voicemail system", s)]
    ph = phones(li.get_text(" ")) or phones(text)
    if not ph:
        raise AudioParseError("no phone number in the voicemail instructions")
    step_text = " ".join(steps)
    keys = {}
    for k, rx in GV_KEYS.items():
        m = rx.search(step_text)
        if not m:
            raise AudioParseError(f"voicemail step not understood: '{k}' key missing")
        keys[k] = m.group(1)

    mm = GV_MINUTES.search(text)
    up = UPTO_MINUTES.search(text)
    if mm:
        lo, hi = int(mm.group(1)), int(mm.group(2))
    elif up:
        lo, hi = None, int(up.group(1))
    else:
        raise AudioParseError("story length (minutes) not found")

    diy = _block_with(block, re.compile(r"(?i)do\s+it\s+yourself|recorder|WAV|MP3"), ("li",)) or block
    mail = emails(diy) or emails(block)
    if not mail:
        raise AudioParseError("no e-mail address for recordings")

    playlists, channel = [], None
    for a in block.find_all("a", href=True):
        u = _abs(page_url, a["href"]) or ""
        host = (urlsplit(u).hostname or "").lower()
        if not (host.endswith("youtube.com") or host == "youtu.be"):
            continue
        lst = (parse_qs(urlsplit(u).query).get("list") or [""])[0]
        u = re.sub(r"[?&]si=[^&#]*", "", u)             # the share-tracking parameter
        if lst:
            title = _yt_title(a)
            if title and all(p["url"] != u for p in playlists):
                playlists.append({"title": title, "url": u})
        elif channel is None and re.search(r"youtube\.com/(?:@|c/|channel/|user/)", u):
            channel = u

    return {
        "page_url": page_url, "phone": ph[0][0], "tel": ph[0][1],
        "minutes_min": lo, "minutes_max": hi, "keys": keys,
        "email": mail[0], "formats": formats(diy.get_text(" ")) or formats(text),
        "no_speakers": bool(GV_NO_SPEAKERS.search(text)),
        "channel_url": channel, "playlists": playlists[:8], "steps_text": steps_text[:8],
    }


# --------------------------------------------------------------------------- La Viña
def parse_lv(html: str, page_url: str) -> dict:
    """La Viña's "Graba tu historia" page → phone, length, e-mail, links. Raises AudioParseError."""
    soup = _soup(html)
    block = _content(soup)
    text = block.get_text(" ")
    if not re.search(r"(?i)graba|grabaci[oó]n", text):
        raise AudioParseError("page does not look like the Graba tu historia page (maintenance? redirect?)")
    call = _block_with(block, re.compile(r"(?i)llam[ae]"), ("p", "li")) or block
    ph = phones(call.get_text(" ")) or phones(text)
    if not ph:
        raise AudioParseError("no phone number")
    up = UPTO_MINUTES.search(text)
    if not up:
        raise AudioParseError("story length (minutes) not found")
    diy = _block_with(block, re.compile(r"(?i)WAV|MP3|grabadora"), ("p", "li", "div")) or block
    mail = emails(diy) or emails(block)
    if not mail:
        raise AudioParseError("no e-mail address for recordings")

    links: dict[str, str] = {}
    for a in block.find_all("a", href=True):
        u = _abs(page_url, a["href"]) or ""
        path = urlsplit(u).path.lower()
        for field, rx in (("instructions_url", r"instrucciones"), ("tips_url", r"consejos"),
                          ("topics_url", r"temas"), ("sample_url", r"muestra|modelo")):
            if field not in links and re.search(rx, path):
                links[field] = u
    return {
        "page_url": page_url, "phone": ph[0][0], "tel": ph[0][1], "minutes_max": int(up.group(1)),
        "email": mail[0], "formats": formats(diy.get_text(" ")) or formats(text),
        "no_speakers": bool(LV_NO_SPEAKERS.search(text)), **links,
    }


def parse_lv_instructions(html: str, page_url: str) -> dict:
    """La Viña's step-by-step page → record key, the copyright sentence to say, the steps as written."""
    soup = _soup(html)
    block = _content(soup)
    text = block.get_text(" ")
    m = LV_RECORD_KEY.search(text)
    if not m:
        raise AudioParseError("instructions: the key to press to record was not found")
    steps = []
    for el in block.find_all(["li", "p"]):
        if el.find(["li", "p"]):
            continue
        t = clean_text(el.get_text(" "))
        if re.match(r"(?i)paso\s+\d", t):
            steps.append(t)
    permission = ""
    holder = _block_with(block, re.compile(r"(?i)derechos\s+de\s+autor"), ("li", "p"))
    if holder is not None:
        em = holder.find(["em", "i", "blockquote"])
        cand = clean_text(em.get_text(" ")) if em else ""
        q = QUOTE_RE.search(cand) or QUOTE_RE.search(holder.get_text(" "))
        permission = clean_text(q.group(1)) if q else cand.strip(" “”\"«»")
        if not re.search(r"(?i)derechos", permission):
            permission = ""
    ph = phones(text)
    return {"keys": {"record": m.group(1)}, "permission_text": permission,
            "long_distance": bool(LONG_DISTANCE.search(text)), "steps_text": steps[:8],
            "instructions_phone": ph[0][1] if ph else None}


# --------------------------------------------------------------------------- collection
Fetch = Callable[[str], "str | None"]
LV_INSTRUCTION_FIELDS = ("keys", "permission_text", "long_distance", "steps_text")


def _item(pub: str, extra: dict) -> dict:
    return make_item(
        id=f"audio:{pub}", source="grapevine" if pub == "gv" else "lavina", kind="audio_project",
        url=extra["page_url"], title="Audio Project" if pub == "gv" else "Graba tu historia",
        lang="en" if pub == "gv" else "es", category="audio_project",
        extra={k: v for k, v in extra.items() if v not in (None, "", [], {})},
    )


def collect_gv(s: dict, fetch: Fetch) -> dict:
    html = fetch(s["page"])
    if not html:
        raise AudioParseError(f"could not fetch {s['page']}")
    return {**parse_gv(html, s["page"]), "checked": now_iso()}


def collect_lv(s: dict, fetch: Fetch, prev_extra: dict) -> tuple[dict, str | None]:
    """→ (extra, problem or None). The main page is required; when only the instructions page fails,
    its fields (keys, the sentence to say) come from the previous run and the problem is reported."""
    html = fetch(s["page"])
    if not html:
        raise AudioParseError(f"could not fetch {s['page']}")
    extra = parse_lv(html, s["page"])
    extra.setdefault("instructions_url", s["instructions"])
    extra.setdefault("tips_url", s["tips"])
    extra.setdefault("topics_url", s["topics"])
    extra.setdefault("sample_url", s["sample"])
    problem = None
    try:
        ihtml = fetch(extra["instructions_url"])
        if not ihtml:
            raise AudioParseError(f"could not fetch {extra['instructions_url']}")
        ins = parse_lv_instructions(ihtml, extra["instructions_url"])
        other = ins.pop("instructions_phone", None)
        if other and other != extra["tel"]:
            log.warning("lv: the instructions page gives another number (%s) than the main page (%s) — "
                        "using the main page's", other, extra["tel"])
        extra.update(ins)
    except Exception as e:  # noqa: BLE001 — keep the previous steps
        if not (prev_extra.get("keys") or {}).get("record"):
            raise AudioParseError(f"instructions: {e}") from e
        problem = f"instructions: {e}"
        extra.update({k: prev_extra[k] for k in LV_INSTRUCTION_FIELDS if k in prev_extra})
    extra["checked"] = now_iso()
    return extra, problem


def collect(fetch: Fetch, prev: dict, cfg: dict | None = None) -> dict:
    """Everything one run finds → {"items", "errors", "stats"}. A part that failed keeps its previous item."""
    st = settings(cfg)
    prev_items = {i["id"]: i for i in prev.get("items", []) if isinstance(i, dict) and i.get("id")}
    items: list[dict] = []
    errors: list[str] = []
    for pub in ("gv", "lv"):
        pid = f"audio:{pub}"
        prev_extra = (prev_items.get(pid) or {}).get("extra") or {}
        try:
            if pub == "gv":
                extra, problem = collect_gv(st[pub], fetch), None
            else:
                extra, problem = collect_lv(st[pub], fetch, prev_extra)
            items.append(_item(pub, extra))
            if problem:
                errors.append(f"{pub}: {problem}")
        except Exception as e:  # noqa: BLE001 — one part never stops the other
            errors.append(f"{pub}: {e}")
            log.warning("%s: %s — keeping the previous data", pub, e)
            if pid in prev_items:
                items.append(prev_items[pid])
    return {"items": items, "errors": errors,
            "stats": {"parts": len(items), "fresh": [p for p in ("gv", "lv") if not any(
                e.startswith(f"{p}:") for e in errors)]}}


# --------------------------------------------------------------------------- main
def _file_key(url: str) -> str:
    p = urlsplit(url)
    return slugify(f"{p.netloc}{p.path}", 120) + ".html"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print what was read, do not write data/raw")
    ap.add_argument("--html-dir", help="read saved pages from this folder instead of fetching (testing)")
    ap.add_argument("--save-html", help="also save every fetched page into this folder (fixtures)")
    args = ap.parse_args(argv)

    prev = load_raw(SOURCE)
    http = shared_session()
    before = http.requests_made
    asked: set[str] = set()

    def fetch(url: str) -> str | None:
        if url in asked:            # one request per page per run
            return None
        asked.add(url)
        if args.html_dir:
            p = Path(args.html_dir) / _file_key(url)
            return p.read_text(encoding="utf-8") if p.exists() else None
        html = http.get_text(url)
        if html and args.save_html:
            Path(args.save_html).mkdir(parents=True, exist_ok=True)
            (Path(args.save_html) / _file_key(url)).write_text(html, encoding="utf-8")
        return html

    res = collect(fetch, prev)
    stats = {**res["stats"], "requests": http.requests_made - before}
    if res["errors"]:
        stats["problems"] = res["errors"]
    if args.dry_run:
        print(json.dumps({**res, "stats": stats}, ensure_ascii=False, indent=1))
        return
    merged, _ = merge_items(prev.get("items") or [], res["items"], drop_missing=True, authoritative=True)
    ok = not res["errors"]
    save_raw(SOURCE, merged, ok=ok, error="; ".join(res["errors"])[:300] if not ok else None, stats=stats)
    log.info("audio project: %d part(s) read, %d request(s)%s", len(stats["fresh"]), stats["requests"],
             f" — problems: {res['errors']}" if res["errors"] else "")


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
