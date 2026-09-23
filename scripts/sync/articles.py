"""Magazine articles from AA Grapevine (English) and La Viña (Spanish) → data/raw/articles.json

How it works (every day, politely — both sites ask for 5 s between requests):

1. HUBS. Fetch each magazine's "current issue" page (config sources.<pub>.magazine_hub):
       https://www.aagrapevine.org/magazine      (monthly; article URLs /magazine/2026/oct/<slug>)
       https://www.aalavina.org/la-revista       (bimonthly; /revista/septiembre-octubre-2026/<slug>)
   The hub's table of contents already shows, for each story: title, "By: Jake B. | Cheyenne,
   Wyoming", a one/two-sentence public teaser and a 600×400 card image. The hub header gives the
   issue label ("October 2026"), the issue theme ("Loneliness"), its blurb and cover image. The
   "In Every Issue" box links the departments (Letter from the Editor, Dear Grapevine, …).
   If a hub fails or shows no articles, the home page (and /revista-2 for La Viña) is tried.

2. DETAILS. Each article page is fetched ONCE (cap --max-details, default 40/run, so a new issue is
   complete within a day or two) to read: the "October 2026 | Loneliness | Our Personal Stories"
   line (issue / topic / section), author, subtitle (editor's one-line summary), whether the full
   text is public (paywalled pages show "WANT TO CONTINUE READING? You must have an active online …
   subscription" / "¿desea continuar leyendo?") and an "Online Exclusive" marker. Pages that were
   fetched but are missing fields are retried at most 3 times, a week apart. An article counts as
   removed ("gone") only after two 404/410 answers at least a week apart, and never while the
   current issue hub still links it.

COPYRIGHT: we NEVER store article body text — only the title, the publisher's own public teaser /
subtitle, author byline and links back to the official page. Card images are hot-linked from the
official site (not copied); only when an article has no card-size image is a small local WebP
thumbnail (≤ 480 px) made in src/assets/cache/articles/.

Item (docs/DATA_SCHEMA.md): source grapevine|lavina, kind article, category gv|lv, id
"<pub>:<issue_key>:<slug>" (department slugs like "discussion-topic" repeat every month).
    extra = publication, issue_label, issue_key, issue_date, issue_theme, issue_url, topic, section,
            author, author_location, subtitle, teaser, free, online_exclusive, department
`date` = first day of the issue month — except that magazines go online BEFORE their cover month
(the October issue is online mid-September), so a future issue date is replaced by the day we first
saw the article (the best-known online publish date). extra.issue_date always holds the cover date.

The envelope also carries `issues`: {"gv:2026-10": {label, theme, description, image, url, …}} so the
site can show the current covers/themes.

Run:  python -m scripts.sync.articles [--max-details 40] [--max-seconds 480] [--no-details] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .common import (CACHE_ASSETS, MONTHS, clean_text, detect_lang, get_logger, load_config, load_raw,
                     make_item, merge_items, now_iso, run_module, save_raw, shared_session, short_hash,
                     truncate)

SOURCE = "articles"
log = get_logger(SOURCE)

PUBS = {
    "gv": {"source": "grapevine", "lang": "en", "cfg": "grapevine", "base": "https://www.aagrapevine.org",
           "hub": "/magazine", "fallbacks": ["/"]},
    "lv": {"source": "lavina", "lang": "es", "cfg": "lavina", "base": "https://www.aalavina.org",
           "hub": "/la-revista", "fallbacks": ["/revista-2", "/"]},
}
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]
ES_MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
             "Octubre", "Noviembre", "Diciembre"]

GV_PATH_RE = re.compile(r"^/magazine/(20\d\d)/([a-z]{3,9})/([a-z0-9][a-z0-9_-]*)$", re.I)
LV_PATH_RE = re.compile(r"^/revista/([a-z0-9-]*20\d\d[a-z0-9-]*)/([a-z0-9][a-z0-9_-]*)$", re.I)
GENERIC_IMAGE_RE = re.compile(r"(?i)Grapevine_Logo|LaVina_Logo|aa-logo-clipart|/logo[^/]*\.(png|jpe?g|svg)")
PAYWALL_RE = re.compile(r"(?i)want to continue reading|must have an active online|desea continuar leyendo|"
                        r"debes tener una suscripci")
EXCLUSIVE_RE = re.compile(r"(?i)online[\s-]+exclusive|web[\s-]+exclusive|exclusiv[oa]\s+(?:en\s+l[ií]nea|web|digital)")
BYLINE_PREFIX_RE = re.compile(r"(?i)^\s*(?:by|por|par)\s*:?\s*")
THUMB_DIR = CACHE_ASSETS / "articles"
MAX_DETAIL_TRIES = 3
RETRY_DAYS = 7


# --------------------------------------------------------------------------- small helpers
def _pub_base(pub: str) -> str:
    cfg = (load_config().get("sources", {}) or {}).get(PUBS[pub]["cfg"], {}) or {}
    return (cfg.get("base") or PUBS[pub]["base"]).rstrip("/")


def _hub_paths(pub: str) -> list[str]:
    cfg = (load_config().get("sources", {}) or {}).get(PUBS[pub]["cfg"], {}) or {}
    first = cfg.get("magazine_hub") or PUBS[pub]["hub"]
    return [first] + [p for p in PUBS[pub]["fallbacks"] if p != first]


def canonical(url: str) -> str:
    """https, lower-case host, no query/fragment/trailing slash."""
    p = urlparse(url.strip())
    host = p.netloc.lower()
    path = re.sub(r"/{2,}", "/", p.path).rstrip("/") or "/"
    return f"https://{host}{path}"


def pub_of_url(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if host.endswith("aagrapevine.org"):
        return "gv"
    if host.endswith("aalavina.org"):
        return "lv"
    return None


def issue_from_url(url: str) -> tuple[str | None, str | None, str | None]:
    """→ (pub, issue_key 'YYYY-MM', slug) for article URLs; (None, None, None) otherwise."""
    pub = pub_of_url(url)
    path = urlparse(url).path.rstrip("/")
    if pub == "gv":
        m = GV_PATH_RE.match(path)
        if m:
            mo = MONTHS.get(m[2].lower())
            return "gv", (f"{m[1]}-{mo:02d}" if mo else None), m[3].lower()
    elif pub == "lv":
        m = LV_PATH_RE.match(path)
        if m:
            return "lv", issue_key_from_label(m[1].replace("-", " ")), m[2].lower()
    return None, None, None


def issue_key_from_label(label: str | None) -> str | None:
    """'October 2026' → '2026-10'; 'Septiembre / Octubre 2026' / 'septiembre octubre 2026' → '2026-09'."""
    if not label:
        return None
    words = re.findall(r"[a-záéíóúñ]+|\d{4}", label.lower())
    months = [MONTHS[w] for w in words if w in MONTHS]
    years = [int(w) for w in words if w.isdigit() and 2000 <= int(w) <= 2100]
    if months and years:
        return f"{years[0]:04d}-{months[0]:02d}"
    return None


def label_from_key(pub: str, key: str | None) -> str | None:
    if not key:
        return None
    y, m = map(int, key.split("-"))
    if pub == "gv":
        return f"{EN_MONTHS[m - 1]} {y}"
    # La Viña is bimonthly (Jan/Feb, Mar/Apr, …): "Septiembre / Octubre 2026"
    if m % 2 == 1 and m < 12:
        return f"{ES_MONTHS[m - 1]} / {ES_MONTHS[m]} {y}"
    return f"{ES_MONTHS[m - 1]} {y}"


def _add_months(key: str, n: int) -> str:
    y, m = map(int, key.split("-"))
    m += n
    y += (m - 1) // 12
    return f"{y:04d}-{(m - 1) % 12 + 1:02d}"


def title_from_slug(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().capitalize()


def inline_text(el) -> str:
    """Text with inline elements glued ("<span>E</span>n" → "En") and blocks/<br> as spaces."""
    if el is None:
        return ""
    for br in el.find_all("br"):
        br.replace_with(" ")
    for b in el.find_all(["p", "li", "div", "h1", "h2", "h3", "h4", "blockquote"]):
        b.insert_after(" ")
    return clean_text(el.get_text(""))


def public_teaser(text: str, limit: int = 300) -> str:
    """The publisher's own public teaser, trimmed to whole sentences (≤ limit chars)."""
    t = clean_text(text)
    if not t:
        return ""
    if len(t) <= limit and re.search(r"[.!?…\"”’)]$", t):
        return t
    cut = t[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?…][\"”’)]?(?=\s|$)", cut)]
    ends = [e for e in ends if e >= 40]
    if ends:
        return cut[:ends[-1]]
    # Drupal cut the teaser mid-word ("…going home to an empt"): drop the partial word, add "…".
    return truncate(t, min(limit, max(40, len(t) - 1)))


def parse_byline(text: str) -> tuple[str | None, str | None]:
    """'By: Jake B. | Cheyenne, Wyoming' → ('Jake B.', 'Cheyenne, Wyoming'); 'By: Anonymous' → ('Anonymous', None)."""
    t = BYLINE_PREFIX_RE.sub("", clean_text(text))
    if not t:
        return None, None
    parts = [clean_text(p) for p in t.split("|")]
    author = parts[0] or None
    loc = ", ".join(p for p in parts[1:] if p) or None
    return author, loc


def _meta(soup: BeautifulSoup, key: str) -> str:
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return clean_text(tag.get("content")) if tag and tag.get("content") else ""


def _img_src(img, page_url: str) -> str | None:
    if img is None:
        return None
    src = img.get("src") or img.get("data-src") or ""
    if not src and img.get("srcset"):
        src = img["srcset"].split(",")[0].split()[0]
    if not src or src.startswith("data:"):
        return None
    url = urljoin(page_url, src)
    return None if GENERIC_IMAGE_RE.search(url) else url


# --------------------------------------------------------------------------- hub parsing
def parse_hub(html: str, page_url: str, pub: str) -> tuple[dict, list[dict]]:
    """→ (issue_info, cards). Each card: url, title, author, author_location, teaser, image, department."""
    soup = BeautifulSoup(html, "lxml")
    issue: dict = {}
    eyebrow = soup.select_one(".large-eyebrow")
    if eyebrow:
        issue["label"] = clean_text(eyebrow.get_text(" "))
        region = eyebrow.find_parent(class_="main-region") or eyebrow.parent
        t = region.select_one(".field--name-field-issue-title") or region.find("h1")
        if t:
            issue["theme"] = clean_text(t.get_text(" "))
        for p in region.select("p.subcopy"):
            if p.find("a", href=re.compile(r"login|inicio-sesion")):
                a = p.find("a", href=True)
                dest = parse_qs(urlparse(a["href"]).query).get("destination", [None])[0]
                if dest:
                    issue["url"] = canonical(urljoin(page_url, dest))
                continue
            txt = clean_text(p.get_text(" "))
            if txt and "description" not in issue:
                issue["description"] = truncate(txt, 500)
        block = region.find_parent(class_=re.compile(r"taxonomy-term|views-row")) or region.parent
        img = block.select_one(".image-region img") if block else None
        if img is not None:
            issue["image"] = urljoin(page_url, img.get("src", "")) or None
    issue["key"] = issue_key_from_label(issue.get("label"))

    cards: dict[str, dict] = {}

    def add(card: dict) -> None:
        """Teaser cards are added first and win; later plain links only fill gaps."""
        u = card["url"]
        old = cards.get(u)
        if not old:
            cards[u] = card
            return
        for k, v in card.items():
            if v and not old.get(k):
                old[k] = v

    # 1) teaser cards in the table of contents
    for node in soup.select(".node--type-article.view-mode-teaser, .layout--article-teaser"):
        link = node.select_one(".read-more a[href]") or node.select_one("h3 a[href], h2 a[href]") \
            or node.find("a", href=True)
        if not link:
            continue
        url = canonical(urljoin(page_url, link["href"]))
        if pub_of_url(url) != pub or re.search(r"/(user|usuario|store|tienda|cart)\b", url):
            continue
        h = node.find(["h3", "h2", "h4"])
        author, loc = parse_byline(node.select_one(".author").get_text(" ")) if node.select_one(".author") else (None, None)
        body = node.select_one(".field--name-body")
        add({
            "url": url,
            "title": clean_text(h.get_text(" ")) if h else "",
            "author": author, "author_location": loc,
            "teaser": public_teaser(inline_text(body)) if body else "",
            "image": _img_src(node.select_one(".image-region img, img"), page_url),
            "department": False,
        })
    # 2) any other article links (the "In Every Issue" departments, home-page promos, …).
    #    Links into much older issues (e.g. La Viña's 2020 "historia modelo" sample) are not news.
    if issue.get("key"):
        oldest = _add_months(issue["key"], -6)
    else:  # fallback pages (home) have no issue header — use the calendar
        oldest = _add_months(datetime.now(timezone.utc).strftime("%Y-%m"), -8)
    for a in soup.find_all("a", href=True):
        url = canonical(urljoin(page_url, a["href"]))
        p, key, slug = issue_from_url(url)
        if p != pub or not key or (oldest and key < oldest):
            continue
        text = clean_text(a.get_text(" "))
        in_every = bool(a.find_parent(class_=re.compile(r"in[-_]every[-_]issue")))
        add({"url": url, "title": "" if text.lower() in ("read", "leer", "") else text,
             "department": in_every})
    return issue, list(cards.values())


# --------------------------------------------------------------------------- article page parsing
def parse_article(html: str, page_url: str) -> dict:
    """Metadata only — never the body text."""
    soup = BeautifulSoup(html, "lxml")
    art = soup.select_one("article.node--type-article") or soup.find("article") or soup.find("main") or soup
    d: dict = {}
    h1 = art.find("h1")
    d["title"] = clean_text(h1.get_text(" ")) if h1 else _meta(soup, "og:title")
    line = art.select_one(".article-publication-date")
    if line:
        parts = [clean_text(x) for x in line.get_text(" ").split("|")]
        parts = [p for p in parts if p]
        rest = []
        for p in parts:
            if EXCLUSIVE_RE.search(p):
                d["online_exclusive"] = True
            elif issue_key_from_label(p) and "issue_label" not in d:
                d["issue_label"] = p
            else:
                rest.append(p)
        if rest:
            d["topic"] = rest[0]
        if len(rest) > 1:
            d["section"] = rest[1]
    by = art.select_one(".article-author, .author")
    if by:
        d["author"], d["author_location"] = parse_byline(by.get_text(" "))
    sub = art.select_one(".article-subtitle")
    if sub:
        d["subtitle"] = clean_text(sub.get_text(" "))
    desc = _meta(soup, "og:description") or _meta(soup, "description")
    if desc:
        d["teaser"] = public_teaser(desc)
    image = _img_src(soup.select_one(".article-main-container .field--name-field-image img, "
                                     "article .field--name-field-image img"), page_url)
    if not image:
        og = _meta(soup, "og:image")
        if og and not GENERIC_IMAGE_RE.search(og):
            image = urljoin(page_url, og)
    d["image"] = image
    # Paywall: logged-out visitors see the first paragraph, then a "want to continue reading?" block.
    # Count story paragraphs (not those inside site blocks such as that notice or customer service).
    # We only COUNT them — the text itself is never kept.
    wall = soup.select_one("#block-wanttocontinuereading") is not None or bool(PAYWALL_RE.search(soup.get_text(" ")))
    body_paras = sum(
        1 for p in art.find_all("p")
        if len(clean_text(p.get_text(" "))) > 40
        and not p.find_parent(class_=re.compile(r"block-block-content|article-publication-date|article-author|author"))
    )
    if wall:
        d["free"] = body_paras >= 4       # a free story would show many paragraphs despite the notice
    elif body_paras:
        d["free"] = True
    if EXCLUSIVE_RE.search(" ".join(clean_text(x.get_text(" ")) for x in art.select(
            ".article-publication-date, .breadcrumb, h1, .article-subtitle, .field--name-field-tags"))):
        d["online_exclusive"] = True
    return {k: v for k, v in d.items() if v not in (None, "")}


# --------------------------------------------------------------------------- thumbnails
def local_thumb(http, img_url: str, key: str) -> tuple[str, int, int] | None:
    """Download an image once and store a ≤480 px wide WebP in src/assets/cache/articles/ (only for
    articles that have no card-size image on the hub). Returns (site_path, width, height) or None."""
    try:
        from PIL import Image
    except Exception:
        return None
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / f"{key}.webp"
    site_path = f"/assets/cache/articles/{dest.name}"
    if dest.exists():
        try:
            with Image.open(dest) as im:
                return site_path, im.width, im.height
        except Exception:
            dest.unlink(missing_ok=True)
    r = http.get(img_url)
    if r is None or r.status_code != 200:
        return None
    if not r.headers.get("Content-Type", "").startswith("image/") or len(r.content) > 15 * 1024 * 1024:
        return None
    try:
        im = Image.open(BytesIO(r.content))
        im = im.convert("RGB")
        im.thumbnail((480, 480 * 3))
        im.save(dest, "WEBP", quality=70, method=6)
        return site_path, im.width, im.height
    except Exception as e:
        log.debug("thumbnail failed for %s: %s", img_url, e)
        return None


def thumb_size(site_path: str) -> tuple[int, int] | None:
    """(width, height) of a thumbnail we made earlier, or None."""
    try:
        from PIL import Image
        with Image.open(THUMB_DIR / site_path.rsplit("/", 1)[-1]) as im:
            return im.width, im.height
    except Exception:
        return None


# --------------------------------------------------------------------------- item assembly
def build_item(rec: dict, first_seen: str | None, issues: dict) -> dict:
    """rec = combined knowledge about one article (previous extra + hub card + detail page)."""
    pub = rec["publication"]
    key = rec.get("issue_key")
    issue = issues.get(f"{pub}:{key}", {}) if key else {}
    label = rec.get("issue_label") or issue.get("label") or label_from_key(pub, key)
    issue_date = f"{key}-01" if key else None
    seen_day = (first_seen or now_iso())[:10]
    date = issue_date if issue_date and issue_date <= seen_day else seen_day
    title = rec.get("title") or title_from_slug(rec.get("slug") or "")
    summary = rec.get("subtitle") or rec.get("teaser") or ""
    lang = detect_lang(f"{title} {summary}", prior=PUBS[pub]["lang"])
    if lang not in ("en", "es", "fr"):
        lang = PUBS[pub]["lang"]
    extra = {
        "publication": pub,
        "issue_label": label,
        "issue_key": key,
        "issue_date": issue_date,
        "issue_theme": rec.get("issue_theme") or issue.get("theme"),
        "issue_url": issue.get("url"),
        "topic": rec.get("topic"),
        "section": rec.get("section"),
        "author": rec.get("author"),
        "author_location": rec.get("author_location"),
        "subtitle": rec.get("subtitle"),
        "teaser": rec.get("teaser"),
        "free": rec.get("free"),
        "online_exclusive": bool(rec.get("online_exclusive")),
        "department": bool(rec.get("department")),
    }
    return make_item(
        id=rec["id"], source=PUBS[pub]["source"], kind="article", url=rec["url"], title=title,
        summary=summary, lang=lang, date=date, image=rec.get("image"), category=pub,
        extra=extra, status=rec.get("status") or "ok",
    )


def _is_complete(rec: dict) -> bool:
    return bool(rec.get("title") and rec.get("section") and rec.get("free") is not None)


def _days_since(iso: str | None, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds() / 86400
    except Exception:
        return float("inf")


def _needs_detail(state: dict | None, rec: dict, now: datetime) -> bool:
    """Fetch an article page once; retry (max 3×, a week apart) only while fields are missing.
    A page that answered 404/410 once is asked again after RETRY_DAYS before the article counts as
    gone. detail_state only keeps entries for incomplete/missing/gone records, so "no state" +
    complete = done."""
    if not state:
        return not _is_complete(rec)
    if state.get("gone"):
        return False
    if state.get("missing_since"):
        return _days_since(state.get("at"), now) >= RETRY_DAYS
    if state.get("ok") and _is_complete(rec):
        return False
    if state.get("tries", 0) >= MAX_DETAIL_TRIES:
        return False
    return _days_since(state.get("at"), now) >= RETRY_DAYS


# --------------------------------------------------------------------------- main
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Grapevine / La Viña magazine articles")
    ap.add_argument("--max-details", type=int, default=40, help="max article pages to fetch this run (default 40)")
    ap.add_argument("--max-seconds", type=int, default=480, help="stop fetching details after N seconds")
    ap.add_argument("--no-details", action="store_true", help="hubs only (no article pages)")
    ap.add_argument("--only", choices=["gv", "lv"], help="process only one publication")
    ap.add_argument("--dry-run", action="store_true", help="do not write data/raw/articles.json")
    args = ap.parse_args(argv)
    try:
        _run(args)
    except Exception as e:
        # Keep yesterday's items AND the envelope extras (issues map, retry state) on any surprise.
        log.exception("articles failed")
        if not args.dry_run:
            prev = load_raw(SOURCE)
            save_raw(SOURCE, prev.get("items", []), ok=False, error=f"{type(e).__name__}: {e}"[:300],
                     stats=prev.get("stats"),
                     extra={"issues": prev.get("issues") or {}, "detail_state": prev.get("detail_state") or {}})


def _run(args) -> None:
    t0 = time.monotonic()
    http = shared_session()
    prev = load_raw(SOURCE)
    prev_items = prev.get("items", [])
    prev_by_id = {i["id"]: i for i in prev_items if i.get("id")}
    detail_state: dict = dict(prev.get("detail_state") or {})
    issues: dict = dict(prev.get("issues") or {})
    errors: list[str] = []
    stats: dict = {"hub_articles": 0, "details_fetched": 0, "details_failed": 0, "thumbs": 0}

    # working records keyed by id, seeded from what we already know
    recs: dict[str, dict] = {}
    for it in prev_items:
        ex = it.get("extra") or {}
        pub = ex.get("publication") or it.get("category")
        if pub not in PUBS:
            continue
        rec = {k: v for k, v in ex.items() if v is not None}
        rec.update({"id": it["id"], "url": it["url"], "title": it.get("title"), "image": it.get("image"),
                    "publication": pub, "status": it.get("status")})
        recs[it["id"]] = rec
    touched: set[str] = set()
    on_hub: set[str] = set()        # listed on a current magazine hub today → certainly not deleted
    url_to_id = {r["url"]: i for i, r in recs.items()}

    # ---- 1. hubs
    for pub in PUBS:
        if args.only and args.only != pub:
            continue
        base = _pub_base(pub)
        got = 0
        for path in _hub_paths(pub):
            hub_url = base + path
            html = http.get_text(hub_url)
            if not html:
                log.warning("%s hub unavailable: %s", pub, hub_url)
                continue
            try:
                issue, cards = parse_hub(html, hub_url, pub)
            except Exception as e:
                log.exception("%s hub parse failed", pub)
                errors.append(f"{pub}: hub parse error {type(e).__name__}")
                continue
            if issue.get("key"):
                ik = f"{pub}:{issue['key']}"
                issues[ik] = {**issues.get(ik, {}), **{k: v for k, v in issue.items() if v},
                              "publication": pub, "hub": hub_url, "seen": now_iso()}
            for c in cards:
                p, key, slug = issue_from_url(c["url"])
                if not key and issue.get("key") and not c.get("department"):
                    key, slug = issue["key"], urlparse(c["url"]).path.rstrip("/").rsplit("/", 1)[-1]
                if not key:
                    continue
                iid = url_to_id.get(c["url"]) or f"{pub}:{key}:{slug}"
                rec = recs.setdefault(iid, {"id": iid, "url": c["url"], "publication": pub})
                have_page = _is_complete(rec)   # the article page's own title beats hub link text
                rec.update({k: v for k, v in c.items()
                            if v not in (None, "", False) and k != "url" and not (k == "title" and have_page)})
                rec.update({"issue_key": rec.get("issue_key") or key, "slug": slug, "publication": pub})
                if key == issue.get("key") and issue.get("theme"):
                    rec.setdefault("issue_theme", issue["theme"])
                url_to_id[c["url"]] = iid
                touched.add(iid)
                on_hub.add(iid)
                if rec.get("status") == "gone":     # the hub links it again → it exists
                    rec["status"] = "ok"
                if (detail_state.get(iid) or {}).get("gone"):
                    detail_state[iid] = {k: v for k, v in detail_state[iid].items() if k != "gone"}
                got += 1
            if got:
                log.info("%s: %d articles on %s (issue %s — %s)", pub, got, hub_url, issue.get("label"), issue.get("theme"))
                break
            log.warning("%s: no articles found on %s (layout changed?)", pub, hub_url)
        if not got:
            errors.append(f"{pub}: no articles found on the magazine hub (site down or layout changed)")
        stats["hub_articles"] += got

    # ---- 2. article details (newest issues first, then hub order)
    if not args.no_details:
        now = datetime.now(timezone.utc)
        todo = [r for r in recs.values()
                if (not args.only or r["publication"] == args.only) and _needs_detail(detail_state.get(r["id"]), r, now)]
        todo.sort(key=lambda r: (r.get("issue_key") or ""), reverse=True)
        stats["details_pending"] = len(todo)
        for r in todo:
            if stats["details_fetched"] + stats["details_failed"] >= args.max_details:
                break
            if time.monotonic() - t0 > args.max_seconds:
                log.info("time budget reached; %d article pages left for tomorrow",
                         len(todo) - stats["details_fetched"] - stats["details_failed"])
                break
            prev_st = detail_state.get(r["id"], {})
            st = {"tries": prev_st.get("tries", 0) + 1, "at": now_iso()}
            resp = http.get(r["url"])
            if resp is None or resp.status_code != 200:
                code = resp.status_code if resp is not None else None
                first_missing = prev_st.get("missing_since")
                if code in (404, 410) and r["id"] not in on_hub:
                    # Gone only after two "not found" answers at least RETRY_DAYS apart: one 404
                    # during a site deploy must not hide an article for good.
                    if first_missing and _days_since(first_missing, now) >= RETRY_DAYS:
                        st["gone"] = True
                        r["status"] = "gone"
                        touched.add(r["id"])
                    else:
                        st["missing_since"] = first_missing or st["at"]
                elif first_missing and code not in (404, 410):
                    st["missing_since"] = first_missing      # still unconfirmed (timeout, 5xx …)
                st["ok"] = False
                st["error"] = f"HTTP {code}" if code else "fetch failed"
                detail_state[r["id"]] = st
                stats["details_failed"] += 1
                continue
            try:
                resp.encoding = resp.encoding or "utf-8"
                d = parse_article(resp.text, r["url"])
            except Exception as e:
                log.warning("article parse failed %s: %s", r["url"], e)
                st.update(ok=False, error=f"parse {type(e).__name__}")
                detail_state[r["id"]] = st
                stats["details_failed"] += 1
                continue
            # The detail page is authoritative for the issue label/key it prints.
            if d.get("issue_label"):
                k2 = issue_key_from_label(d["issue_label"])
                if k2 and k2 == r.get("issue_key"):
                    r["issue_label"] = d["issue_label"]
            for k in ("title", "topic", "section", "author", "author_location", "subtitle", "free", "online_exclusive"):
                if d.get(k) is not None and d.get(k) != "":
                    r[k] = d[k]
            if d.get("teaser") and not r.get("teaser"):
                r["teaser"] = d["teaser"]
            if not r.get("image") and d.get("image"):
                thumb = local_thumb(http, d["image"], short_hash(r["id"], 16))
                if thumb:
                    r["image"] = thumb[0]
                    stats["thumbs"] += 1
            st["ok"] = True
            detail_state[r["id"]] = st
            touched.add(r["id"])
            stats["details_fetched"] += 1
        stats["details_left"] = max(0, len(todo) - stats["details_fetched"] - stats["details_failed"])

    # ---- 3. assemble + save
    # The editor's letter ("Letter from the Editor" / "Bienvenida") carries the portrait magazine
    # cover as its image; remember it as the issue's cover so the site can show it.
    for r in recs.values():
        ik = f"{r['publication']}:{r.get('issue_key')}"
        img = r.get("image") or ""
        if not (r.get("department") and img.startswith("/assets/cache/articles/")):
            continue
        if ik in issues and not issues[ik].get("cover"):
            dims = thumb_size(img)
            if dims and dims[1] > dims[0] * 1.2:
                issues[ik]["cover"] = img
    new_items = []
    for iid in touched:
        r = recs[iid]
        prev_it = prev_by_id.get(iid)
        try:
            new_items.append(build_item(r, prev_it.get("first_seen") if prev_it else None, issues))
        except Exception as e:  # one odd record must not sink the run
            log.warning("skipping %s: %s", iid, e)
    merged, added = merge_items(prev_items, new_items)
    stats["new"] = added
    stats["total"] = len(merged)
    stats["requests"] = http.requests_made
    # keep the issues map small: the 24 most recent
    issues = dict(sorted(issues.items(), key=lambda kv: kv[0].split(":", 1)[1], reverse=True)[:24])
    # Keep detail state only where it still matters (incomplete or gone records that still exist),
    # so the envelope doesn't grow by ~50 entries a month forever.
    by_id = {i["id"]: i for i in merged}
    detail_state = {k: v for k, v in detail_state.items()
                    if k in by_id and (v.get("gone") or v.get("missing_since")
                                       or not _is_complete({**(by_id[k].get("extra") or {}),
                                                            "title": by_id[k].get("title")}))}

    if args.dry_run:
        print(json.dumps({"stats": stats, "errors": errors, "issues": issues,
                          "sample": [i for i in merged if i["id"] in touched][:5]}, ensure_ascii=False, indent=1))
        return
    ok = not errors
    save_raw(SOURCE, merged, ok=ok, error="; ".join(errors) if errors else None, stats=stats,
             extra={"issues": issues, "detail_state": detail_state})
    log.info("articles: %d total, %d new, %d details fetched%s", len(merged), added, stats["details_fetched"],
             f" — errors: {errors}" if errors else "")


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
