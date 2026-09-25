"""Grapevine's Daily Quote and La Viña's Cita Diaria from the magazines' home pages → data/raw/quote.json

Both home pages (config/site.yml → sources.grapevine / sources.lavina `quote_page`, default "/") carry
a "quote of the day" block, anchored #quote-of-the-day and new before 6 AM Central every day:

    <article class="node--type-quote">                      (the teaser; preferred)
      <h3><span class="field--name-title">Grapevine Daily Quote September 25</span></h3>
      <div class="field--name-body"><p>“During his first AA years …”</p></div>
      <div class="field--name-field-attribution">AA Co-Founder, Bill W., September 1945, From: “…”, The Language of the Heart</div>
      <div class="field--name-field-links"><a href="https://visitor.r20.constantcontact.com/…">Sign up to receive GV's Daily Quote</a></div>
    </article>

La Viña: "Cita Diaria con La Viña Septiembre 25" / "“Mi nuevo amigo …”." / "“La novia de nadie”. MORENO VALLEY,
CALIFORNIA, DICIEMBRE DE 1992. De I Am Responsible" / "Regístrate para recibir la Cita Diaria de La Viña".
The same quote is also shown near the top of each page (a view "embed" with a <div class="quote-container">);
it is the fallback when the teaser is missing. (Its sign-up link is Grapevine's on BOTH sites, so the
fallback looks for the publication's own sign-up link by its text instead.)

What is kept: the quote exactly as published (only whitespace and the outer quotation marks are cleaned;
nothing is translated — each quote is shown in its own language), the attribution and the source book
split apart ("From:" / "De"), the day (from the heading, year inferred around today in Central time; the
fetch day when the heading has no date), the official page anchor and the publication's sign-up link.

Cost: ONE page request per publication (+ robots.txt), through the shared polite session (5 s between
requests to the magazines' server) — none when an earlier module of the same run already read that home
page (the session's page memo; the crawl, later, reuses these copies too). Runs in the daily update AND in
the quick one (run_all QUICK_MODULES): the 12:07 UTC scheduled run (.github/workflows/update.yml) is always
after 6 AM Texas time.

Output (docs/DATA_SCHEMA.md → "quote"): items `quote:<pub>:<date>` kind "quote" (the newest quote of each
publication), and the envelope key `history` = {"gv": [...], "lv": [...]}: the quotes of the last
HISTORY_DAYS days per publication, newest first, one per day. `history` stays in data/raw only: it is the
memory behind "a page that shows an older quote than one we already have keeps the newer one". When a page
cannot be fetched or read, that publication's previous quote is kept and the run is marked ok=false
(→ /status/). build_data.py turns it into data/site/quote.json (build_site below: the items only).

Run:  python -m scripts.sync.quote [--dry-run] [--only gv|lv] [--html-dir DIR] [--save-html DIR]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .common import (MONTHS, clean_text, detect_lang, get_logger, load_config, load_raw, make_item, merge_items,
                     run_module, save_raw, shared_session)

SOURCE = "quote"
log = get_logger(SOURCE)

ANCHOR = "quote-of-the-day"
HISTORY_DAYS = 14                # quotes kept per publication (one per day)
PUB_ORDER = ("gv", "lv")

# Defaults — config/site.yml sources.grapevine / sources.lavina (`base`, `quote_page`) override them.
PUBS: dict[str, dict] = {
    "gv": {"cfg": "grapevine", "lang": "en", "base": "https://www.aagrapevine.org", "page": "/",
           # "Sign up to receive GV's Daily Quote"
           "signup_re": re.compile(r"(?i)\b(?:sign\s*up|subscribe)\b.*\bdaily\s+quote\b")},
    "lv": {"cfg": "lavina", "lang": "es", "base": "https://www.aalavina.org", "page": "/",
           # "Regístrate para recibir la Cita Diaria de La Viña"
           "signup_re": re.compile(r"(?i)\b(?:reg[ií]strate|suscr[ií]bete|inscr[ií]bete)\b.*\bcita\b")},
}

MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December")
MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
             "octubre", "noviembre", "diciembre")
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
# "September 25" / "Septiembre 25" / "Sept. 25, 2026" / "25 de septiembre (de 2026)"
HEAD_DATE_RE = re.compile(
    rf"(?i)\b(?:({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?"
    rf"|(\d{{1,2}})\s+(?:de\s+)?({_MONTH_RE})\.?(?:,?\s+(?:de\s+|del\s+)?(20\d\d))?)\b")
# ", From: “Title”, Book" / ". De I Am Responsible" / "From: …" — the LAST one splits the attribution
# from the source (capitalised on purpose: "DICIEMBRE DE 1992" and "La novia de nadie" never match).
FROM_RE = re.compile(r"(?:(?:^|[,;]\s*|\.\s+|\s+[-–—]\s+)(?:From|FROM|Tomado de|Fuente)\s*:?"
                     r"|(?:^|\.\s+)(?:De|DE)\s*:|(?:^|\.\s+)De)\s+(?=\S)")


class QuoteParseError(ValueError):
    """The page was fetched but no quote could be read from it."""


# --------------------------------------------------------------------------- settings
def settings(cfg: dict | None = None) -> dict[str, dict]:
    """{pub: {pub, lang, page (absolute URL to fetch), url (the official page anchor), signup_re}}."""
    cfg = cfg if cfg is not None else load_config()
    src = cfg.get("sources") or {}
    out: dict[str, dict] = {}
    for pub, d in PUBS.items():
        c = src.get(d["cfg"]) or {}
        base = str(c.get("base") or d["base"]).rstrip("/")
        page = str(c.get("quote_page") or d["page"])
        page = page if re.match(r"(?i)https?://", page) else base + "/" + page.lstrip("/")
        out[pub] = {"pub": pub, "lang": d["lang"], "page": page, "url": page.split("#")[0] + "#" + ANCHOR,
                    "signup_re": d["signup_re"]}
    return out


def local_today(cfg: dict | None = None) -> date:
    cfg = cfg if cfg is not None else load_config()
    try:
        tz = ZoneInfo((cfg.get("site") or {}).get("timezone") or "America/Chicago")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("America/Chicago")
    return datetime.now(tz).date()


# --------------------------------------------------------------------------- text helpers
def _balanced(s: str) -> bool:
    """No curly quote closes before it opens, every one that opens closes, straight quotes pair up."""
    depth = 0
    for ch in s:
        if ch in "“«„":
            depth += 1
        elif ch in "”»":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and s.count('"') % 2 == 0


def strip_outer_quotes(s: str) -> str:
    """'“Text.”' → 'Text.'  ·  '“Texto”.' → 'Texto.'  ·  '“A,” he said, “B.”' is left alone (not one quotation)."""
    s = clean_text(s)
    m = re.fullmatch(r"[“«„\"]\s*(.+?)\s*[”»“\"]\s*([.!?…]?)", s, re.S)
    if not m or not _balanced(m[1]):
        return s
    inner, tail = m[1], m[2]
    if tail and not inner.endswith((".", "!", "?", "…")):
        inner += tail
    return inner


def split_attribution(s: str) -> tuple[str, str]:
    """'AA Co-Founder, Bill W., September 1945, From: “Title”, Book' → ('AA Co-Founder, Bill W., September 1945',
    '“Title”, Book')  ·  '“Historia”. LUGAR, MES DE 1992. De Book' → ('“Historia”. LUGAR, MES DE 1992', 'Book')."""
    s = clean_text(s)
    last = None
    for m in FROM_RE.finditer(s):
        last = m
    if not last:
        return s.rstrip(" ,;"), ""
    who, src = s[:last.start()].strip(" ,;"), s[last.end():].strip(" ,;")
    if not src:
        return s.rstrip(" ,;"), ""
    return who, src.rstrip(".") if src.count(".") == 1 and src.endswith(".") else src


def infer_date(month: int, day: int, year: int | None, today: date) -> date | None:
    """The heading gives no year: the candidate closest to today (Dec 31 read on Jan 1 → last year)."""
    years = [year] if year else [today.year - 1, today.year, today.year + 1]
    best = None
    for y in years:
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best


def heading_date(heading: str, today: date) -> date | None:
    m = HEAD_DATE_RE.search(heading or "")
    if not m:
        return None
    if m[1]:
        mo, d, y = MONTHS.get(m[1].lower().rstrip(".")), int(m[2]), m[3]
    else:
        mo, d, y = MONTHS.get(m[5].lower().rstrip(".")), int(m[4]), m[6]
    if not mo or not 1 <= d <= 31:
        return None
    return infer_date(mo, d, int(y) if y else None, today)


def date_label(iso: str | None, lang: str) -> str:
    """'2026-09-25' → 'September 25' (en) / '25 de septiembre' (es)."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return f"{d.day} de {MONTHS_ES[d.month - 1]}" if lang == "es" else f"{MONTHS_EN[d.month - 1]} {d.day}"


# --------------------------------------------------------------------------- parsing
def _text(el) -> str:
    return clean_text(el.get_text(" ")) if el is not None else ""


def _body_text(el) -> str:
    """The quote's paragraphs, joined by one space (a quote is short; the site shows it as one block)."""
    if el is None:
        return ""
    paras = [clean_text(p.get_text(" ")) for p in el.find_all("p")] or [clean_text(el.get_text(" "))]
    return clean_text(" ".join(p for p in paras if p))


def _block(soup: BeautifulSoup):
    """(block element, kind) — the quote teaser, else the view row near the top of the page."""
    art = soup.select_one("article.node--type-quote")
    if art is not None:
        return art, "teaser"
    for sel in (".view-quote-of-the-day .views-row", "[class*='view-id-quote_of_the_day'] .views-row",
                ".quote-container"):
        el = soup.select_one(sel)
        if el is not None:
            return (el.parent if sel == ".quote-container" else el), "embed"
    anchor = soup.select_one(f"a[name='{ANCHOR}'], #{ANCHOR}")
    if anchor is not None:
        box = anchor.find_parent(class_=re.compile(r"\bview\b")) or anchor.parent
        if box is not None:
            return box, "anchor"
    return None, ""


def parse_quote(html: str, page_url: str, lang: str, today: date, signup_re: re.Pattern | None = None) -> dict | None:
    """The day's quote on one home page, or None when the page has no quote block at all.
    Raises QuoteParseError when the block is there but holds no quote text."""
    soup = BeautifulSoup(html, "lxml")
    block, kind = _block(soup)
    if block is None:
        return None
    title_el = block.select_one(".field--name-title") or block.find(["h2", "h3", "h4"])
    heading = _text(title_el)
    body = block.select_one(".field--name-body") or block.select_one(".quote-container p")
    text = strip_outer_quotes(_body_text(body))
    if len(text) < 3:
        raise QuoteParseError(f"quote block ({kind}) has no text")
    attr_el = block.select_one(".field--name-field-attribution, .field--name-attribution")
    who, src = split_attribution(_text(attr_el))
    # The publication's own sign-up link: the teaser's link field; else any link on the page whose text
    # says so (the embed near the top links Grapevine's list on BOTH sites — never taken blindly).
    signup = None
    teaser_links = block.select(".field--name-field-links a[href]") if kind == "teaser" else []
    page_links = [a for a in soup.select("a[href]") if signup_re and signup_re.search(_text(a))]
    for a in teaser_links + page_links:
        href = (a.get("href") or "").strip()
        if href.startswith(("http://", "https://")):
            signup = href
            break
    day = heading_date(heading, today)
    return {
        "heading": heading,
        "date": (day or today).isoformat(),
        "date_from_heading": day is not None,
        "text": text,
        "attribution": who,
        "source": src,
        "source_lang": detect_lang(src, prior=lang) if src else None,
        "signup_url": signup,
        "node": (block.get("id") or "") if kind == "teaser" else "",
        "block": kind,
    }


# --------------------------------------------------------------------------- collecting
Fetch = Callable[[str], "str | None"]


def entry(pub: str, lang: str, q: dict, url: str) -> dict:
    """One quote as stored in `history` (and in an item's extra)."""
    return {"pub": pub, "lang": lang, "date": q["date"], "heading": q.get("heading") or "", "text": q["text"],
            "attribution": q.get("attribution") or "", "source": q.get("source") or "",
            "source_lang": q.get("source_lang"), "url": url, "signup_url": q.get("signup_url")}


def add_history(history: list[dict], new: dict | None, today: date, keep_days: int = HISTORY_DAYS) -> list[dict]:
    """One quote per day, newest first: `new` (if any) replaces an entry of the same day; entries older
    than `keep_days` days (and anything beyond `keep_days` entries) are dropped."""
    rows = [h for h in history if isinstance(h, dict) and h.get("date") and (not new or h.get("date") != new.get("date"))]
    if new:
        rows.append(new)
    cutoff = (today - timedelta(days=keep_days - 1)).isoformat()
    rows = [h for h in rows if str(h["date"]) >= cutoff]
    rows.sort(key=lambda h: str(h["date"]), reverse=True)
    return rows[:keep_days]


def collect(fetch: Fetch, prev: dict, today: date, cfg: dict | None = None, only: str | None = None) -> dict:
    """{items, history, errors, stats}. A publication that fails keeps its previous quote (item + history)."""
    conf = settings(cfg)
    prev_items = {((i.get("extra") or {}).get("pub")): i for i in prev.get("items") or [] if isinstance(i, dict)}
    prev_hist = prev.get("history") if isinstance(prev.get("history"), dict) else {}
    items, history, errors = [], {}, []
    stats = {"fetched": 0, "parsed": 0}
    for pub in PUB_ORDER:
        s = conf[pub]
        hist = [h for h in (prev_hist.get(pub) or []) if isinstance(h, dict)]
        q = None
        if only and pub != only:
            pass
        else:
            html = fetch(s["page"])
            if not html:
                errors.append(f"{pub}: page unavailable ({s['page']})")
            else:
                stats["fetched"] += 1
                try:
                    q = parse_quote(html, s["page"], s["lang"], today, s["signup_re"])
                    if q is None:
                        errors.append(f"{pub}: no quote of the day on {s['page']}")
                except QuoteParseError as e:
                    errors.append(f"{pub}: {e}")
                except Exception as e:  # noqa: BLE001 — one publication never breaks the other
                    log.exception("%s: quote parse failed", pub)
                    errors.append(f"{pub}: parse error {type(e).__name__}")
        old = prev_items.get(pub)
        if q:
            stats["parsed"] += 1
            old_sign = ((old or {}).get("extra") or {}).get("signup_url")
            if not q.get("signup_url") and old_sign:     # keep the last known sign-up link
                q["signup_url"] = old_sign
            e = entry(pub, s["lang"], q, s["url"])
            # The site went back to an older quote than one we already have (a cache glitch): keep the newer.
            newest = hist[0] if hist else None
            hist = add_history(hist, e, today)
            best = hist[0] if hist else e
            if newest and best is not e and str(newest.get("date")) > e["date"]:
                log.warning("%s: the page shows %s but %s is already known — kept the newer", pub, e["date"],
                            newest.get("date"))
            items.append(make_item(
                id=f"quote:{pub}:{best['date']}", source=SOURCE, kind="quote", url=best["url"],
                title=best.get("heading") or "", lang=s["lang"], date=best["date"],
                extra={"pub": pub, "text": best["text"], "attribution": best.get("attribution") or "",
                       "source": best.get("source") or "", "source_lang": best.get("source_lang"),
                       "signup_url": best.get("signup_url"), "date_label": date_label(best["date"], s["lang"]),
                       "date_from_heading": bool(q.get("date_from_heading")) if best is e else None,
                       "block": q.get("block") if best is e else None, "node": q.get("node") if best is e else None}))
        elif old:
            items.append(old)                                   # keep yesterday's quote
            hist = add_history(hist, None, today)
        if hist:
            history[pub] = hist
    return {"items": items, "history": history, "errors": errors, "stats": stats}


# --------------------------------------------------------------------------- site file (build_data.py)
SITE_KEYS = ("id", "pub", "lang", "date", "date_label", "heading", "text", "attribution", "source", "source_lang",
             "url", "signup_url")


def empty_site(updated: str | None = None) -> dict:
    return {"updated": updated, "fixture": False, "items": []}


def build_site(env: dict) -> dict:
    """data/raw/quote.json → data/site/quote.json: {updated, fixture, items (Grapevine then La Viña, the newest
    quote of each)}. Rows without text or link are left out. The raw `history` is not copied: nothing on the
    site shows past quotes (it only guards against a page going back to an older quote — collect())."""
    env = env if isinstance(env, dict) else {}
    doc = empty_site(env.get("updated"))
    by_pub: dict[str, dict] = {}
    for it in env.get("items") or []:
        if not isinstance(it, dict) or it.get("kind") != "quote" or it.get("status", "ok") == "gone":
            continue
        ex = it.get("extra") or {}
        pub = ex.get("pub")
        if pub not in PUBS or not clean_text(ex.get("text")) or not it.get("url") or not it.get("date"):
            continue
        row = {"id": it.get("id"), "pub": pub, "lang": it.get("lang") or PUBS[pub]["lang"],
               "date": str(it["date"])[:10],
               "date_label": ex.get("date_label") or date_label(it["date"], it.get("lang") or PUBS[pub]["lang"]),
               "heading": clean_text(it.get("title")), "text": clean_text(ex.get("text")),
               "attribution": clean_text(ex.get("attribution")), "source": clean_text(ex.get("source")),
               "source_lang": ex.get("source_lang"), "url": it["url"], "signup_url": ex.get("signup_url") or None}
        if pub not in by_pub or row["date"] > by_pub[pub]["date"]:
            by_pub[pub] = row
    doc["items"] = [by_pub[p] for p in PUB_ORDER if p in by_pub]
    return doc


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print what was read, do not write data/raw")
    ap.add_argument("--only", choices=PUB_ORDER, help="read one publication only (the other keeps its quote)")
    ap.add_argument("--html-dir", help="read saved pages <dir>/gv.html, <dir>/lv.html instead of fetching (testing)")
    ap.add_argument("--save-html", help="also save each fetched page into this folder (fixtures)")
    args = ap.parse_args(argv)

    prev = load_raw(SOURCE)
    http = shared_session()
    before = http.requests_made
    pages = {s["page"]: pub for pub, s in settings().items()}

    def fetch(url: str) -> str | None:
        pub = pages.get(url, "page")
        if args.html_dir:
            p = Path(args.html_dir) / f"{pub}.html"
            return p.read_text(encoding="utf-8") if p.exists() else None
        html = http.get_text(url)       # at most one request per page per run (the session's page memo; 5xx retried)
        if html and args.save_html:
            Path(args.save_html).mkdir(parents=True, exist_ok=True)
            (Path(args.save_html) / f"{pub}.html").write_text(html, encoding="utf-8")
        return html

    today = local_today()
    res = collect(fetch, prev, today, only=args.only)
    stats = {**res["stats"], "requests": http.requests_made - before, "today": today.isoformat()}
    if res["errors"]:
        stats["problems"] = res["errors"]
    if args.dry_run:
        print(json.dumps({**res, "stats": stats}, ensure_ascii=False, indent=1))
        return
    merged, new = merge_items(prev.get("items") or [], res["items"], drop_missing=True, authoritative=True)
    stats["new"] = new
    ok = not res["errors"]
    save_raw(SOURCE, merged, ok=ok, error="; ".join(res["errors"])[:300] if not ok else None, stats=stats,
             extra={"history": res["history"]})
    log.info("quote: %s, %d request(s)%s", ", ".join(f"{(i.get('extra') or {}).get('pub')} {i.get('date')}"
                                                     for i in merged) or "none", stats["requests"],
             f" — problems: {res['errors']}" if res["errors"] else "")


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
