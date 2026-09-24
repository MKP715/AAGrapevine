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

2. ARCHIVE. Both sites list every story newest first, 10 per page, with the issue label, topic,
   byline ("By: Jake B. | Cheyenne, Wyoming" / "Por: Victor R. | Grand Prairie, Texas") and subtitle:
       https://www.aagrapevine.org/archive?page=N      https://www.aalavina.org/archivo?page=N
   The FIRST run walks back until a page's oldest story is older than today − --backfill-days
   (default 120) or --archive-pages (default 20 per publication) is reached; an interrupted backfill
   resumes where it stopped (envelope `archive_state`). After that a daily run reads until it is past
   the newest issue AND the one before it (a row of an older issue appears) and then stops at the first
   page whose stories were all known before this run — about 4-7 requests per publication. The archive
   is sorted by publish date and the next issue carries a future date (October stories are dated
   Oct 1 but go online mid-September), so an Online Exclusive posted after the next issue went online
   is listed BELOW that issue's whole block; stopping at the first known page would never reach it.
   If the site's page links
   break (a page offering "Next" lists only stories already read earlier in the same run) the walk stops
   with an error instead of reading the same page 20 times, and a backfill that would have to resume
   deeper than backfill_page_limit() (6 pages per month of --backfill-days) is given up. This fills the 60/90-day
   "published writers" spotlight (build_data.py → data/site/spotlight.json) with the issues before
   the current one. Stories older than the backfill window are not added. Back-catalog issues are
   not "news" (build_data keeps them out of What's New) and get no local thumbnail unless the writer
   is from Texas (spotlight card) — the repository stays small.

3. DETAILS. Each article page is fetched ONCE (cap --max-details, default 40/run, so a new issue is
   complete within a day or two) to read: the "October 2026 | Loneliness | Our Personal Stories"
   line (issue / topic / section), author, subtitle (editor's one-line summary), whether the full
   text is public (paywalled pages show "WANT TO CONTINUE READING? You must have an active online …
   subscription" / "¿desea continuar leyendo?") and an "Online Exclusive" marker. Pages that were
   fetched but are missing fields are retried at most 3 times, a week apart. An article counts as
   removed ("gone") only after two 404/410 answers at least a week apart, and never while the
   current issue hub (or the archive) still lists it.
   Stories inside the backfill window whose listing showed no author or no place (La Viña's listing
   sometimes has no byline) are fetched FIRST, under their own cap (--max-byline-details, default
   60/run), so the spotlight knows where every writer is from; a page that simply has no place
   ("By: Anonymous") is not asked again. Of the rest, stories by Texas writers come first (their
   spotlight cards want the section and thumbnail), then the newest issues.

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
site can show the current covers/themes (only issues seen on a hub — back-catalog issues found in the
archive have no entry), `detail_state` (retry bookkeeping) and `archive_state` (docs/DATA_SCHEMA.md).

Run:  python -m scripts.sync.articles [--max-details 40] [--max-byline-details 60] [--max-seconds 480]
                                      [--archive-pages 20] [--backfill-days 120] [--no-archive]
                                      [--no-details] [--only gv|lv] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .common import (CACHE_ASSETS, MONTHS, clean_text, detect_lang, get_logger, load_config, load_raw,
                     make_item, merge_items, now_iso, run_module, save_raw, shared_session, short_hash,
                     truncate)
from .geo import classify_location

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

# Archive listings (newest first, 10 stories per page; ?page=0 is the plain path).
ARCHIVE_PATHS = {"gv": "/archive", "lv": "/archivo"}
BACKFILL_DAYS = 120          # first run: walk back this far (covers the 60/90-day spotlight + a margin)
ARCHIVE_PAGES = 20           # max archive pages per publication per run
BYLINE_DETAILS = 60          # max article pages fetched per run only to learn a missing author/place
# "In Every Issue" departments by slug ("letter-editor-october-2026", "cartas-del-lector") — the archive
# has no "In Every Issue" box, so a new back-catalog record is flagged by its slug until its page is read.
DEPARTMENT_SLUG_RE = re.compile(
    r"^(?:letter-editor|letter-from-the-editor|dear-grapevine|aa-news|discussion-topic|at-wits-end|wits-end|"
    r"carta-de-bienvenida|cartas-del-lector|noticias|tema-de-discusion(?:-para-reuniones)?|humor|"
    r"acerca-del-alcoholismo|alcoholism-(?:at-)?large)(?:-[a-z]+(?:-[a-z]+)?-20\d\d)?(?:-\d+)?$")
EVERY_ISSUE_RE = re.compile(r"(?i)in every issue|en cada (?:edici[oó]n|n[uú]mero)")


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


def tidy_label(label: str | None) -> str | None:
    """Capitalize lower-case month names in a printed issue label — the archive listing sometimes prints
    "june 2026" where the magazine says "June 2026" ("septiembre / octubre 2026" → "Septiembre /
    Octubre 2026"). Everything else is kept exactly as printed."""
    if not label:
        return label
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize()
                  if m.group(0).islower() and m.group(0) in MONTHS else m.group(0), label)


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


# --------------------------------------------------------------------------- archive listing
def archive_url(base: str, pub: str, page: int) -> str:
    return f"{base}{ARCHIVE_PATHS[pub]}" + (f"?page={page}" if page else "")


def parse_archive(html: str, page_url: str, pub: str) -> tuple[list[dict], bool]:
    """One archive page → (rows, has_next_page). Row: url, slug, title, issue_key, issue_label (only
    when it agrees with the key), topic, author, author_location, subtitle, online_exclusive,
    department (slug looks like an "In Every Issue" department and there is no byline)."""
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    seen: set[str] = set()
    for node in soup.select(".views-row"):
        card = node.select_one("[class*='node--type-']") or node
        link = card.select_one("h3 a[href], h2 a[href]") or card.find("a", href=True)
        if not link:
            continue
        url = canonical(urljoin(page_url, link["href"]))
        if pub_of_url(url) != pub or url in seen:
            continue
        _p, key, slug = issue_from_url(url)
        label, topic, exclusive = None, None, False
        line = card.select_one(".article-publication-date")
        if line:
            rest = []
            for part in (clean_text(x) for x in line.get_text(" ").split("|")):
                if not part:
                    continue
                if EXCLUSIVE_RE.search(part):
                    exclusive = True
                elif label is None and issue_key_from_label(part):
                    label = tidy_label(part)
                else:
                    rest.append(part)
            topic = rest[0] if rest else None
        if not key and label:          # a story URL outside /magazine/<y>/<m>/ — trust the printed issue
            key = issue_key_from_label(label)
            slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
        if not key or not slug:
            continue
        by = card.select_one(".article-author, .author")
        author, loc = parse_byline(by.get_text(" ")) if by else (None, None)
        sub = card.select_one(".article-subtitle")
        seen.add(url)
        rows.append({
            "url": url, "slug": slug, "title": clean_text(link.get_text(" ")),
            "issue_key": key, "issue_label": label if label and issue_key_from_label(label) == key else None,
            "topic": topic, "author": author, "author_location": loc,
            "subtitle": inline_text(sub) if sub else "", "online_exclusive": exclusive,
            "department": bool(DEPARTMENT_SLUG_RE.match(slug)) and not author,
        })
    has_next = soup.select_one("a[rel='next'], .pager__item--next a[href]") is not None
    return rows, has_next


def merge_listing(rec: dict, row: dict, new: bool) -> bool:
    """Fold an archive row into a record. Known records only get their GAPS filled (the hub card and
    the article page are richer); a new record takes everything. → True if the record changed."""
    changed = False
    for k in ("title", "author", "author_location", "subtitle", "topic", "issue_label"):
        v = row.get(k)
        if v and not rec.get(k):
            rec[k] = v
            changed = True
    if row.get("online_exclusive") and not rec.get("online_exclusive"):
        rec["online_exclusive"] = True
        changed = True
    if new:
        rec.update({"issue_key": row["issue_key"], "slug": row["slug"], "department": row.get("department", False)})
        changed = True
    return changed


def _lacks_byline(rec: dict) -> bool:
    """A story (not a department) whose author or place we do not know yet."""
    if rec.get("department") or EVERY_ISSUE_RE.search(str(rec.get("section") or "")):
        return False
    return not rec.get("author") or not rec.get("author_location")


def _texas_writer(rec: dict) -> bool:
    """The writer's place is in Texas (Area 65 or elsewhere) — a card in the published-writers spotlight."""
    return classify_location(rec.get("author_location")).get("scope") in ("neta65", "texas")


def _wants_thumb(rec: dict, issues: dict) -> bool:
    """Local thumbnails (≈20 KB each, committed to git) for stories of issues seen on a hub — as
    before — but for back-catalog stories from the archive only when the writer is from Texas (they
    appear as cards in the spotlight); the other ~25 stories per old issue are text-only links."""
    if f"{rec.get('publication')}:{rec.get('issue_key')}" in issues:
        return True
    return _texas_writer(rec)


def _issue_day(rec: dict) -> str:
    key = rec.get("issue_key") or ""
    return f"{key}-01" if re.fullmatch(r"\d{4}-\d{2}", key) else ""


def _needs_byline(state: dict | None, rec: dict, window_day: str) -> bool:
    """Fetch the article page once to learn a missing author/place — only inside the backfill window
    (the spotlight shows at most 90 days), and never again after a successful read."""
    if not _lacks_byline(rec) or not _issue_day(rec) or _issue_day(rec) < window_day:
        return False
    if not state:
        return True
    if state.get("gone") or state.get("ok") or state.get("byline_at"):
        return False
    if state.get("tries", 0) >= MAX_DETAIL_TRIES:
        return False
    return _days_since(state.get("at"), datetime.now(timezone.utc)) >= RETRY_DAYS


def backfill_page_limit(args) -> int:
    """The deepest archive page an unfinished backfill may resume at before it is given up. The live
    archives hold 10 stories a page and at most ~40 new stories a month (Grapevine; La Viña fewer), so
    6 pages per month of --backfill-days is a wide margin (120 days → 24; GV needed 17, LV 7) — and
    never less than two runs' worth of --archive-pages."""
    return max(2 * int(args.archive_pages), math.ceil(int(args.backfill_days) / 30 * 6))


ISSUE_MONTHS = {"gv": 1, "lv": 2}   # Grapevine is monthly, La Viña bimonthly


def walk_archive(http, pub: str, recs: dict, url_to_id: dict, touched: set, listed: set, state: dict,
                 args, t0: float, errors: list, known: set | None = None) -> dict:
    """Read the archive of one publication (see module docstring §2). Mutates recs/url_to_id/touched/
    listed; returns the new archive_state entry for `pub`.
    Safety stops for a site whose pager stops working (it ignores ?page= and always shows the same
    stories with a "Next" link): a page whose stories were ALL listed on earlier pages of this same run
    ends the walk with an error (≈2 requests a day instead of the page cap), and a backfill that would
    have to resume deeper than backfill_page_limit() is given up (marked done, with an error) so it
    stops costing requests; delete archive_state or raise --backfill-days to try again.
    `known`: ids known BEFORE this run (before the hub step added today's stories). A daily walk
    counts "new" against it, so the day a new issue appears its stories do not look already known."""
    st = dict(state or {})
    st.pop("error", None)
    today = datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=args.backfill_days)).isoformat()
    initial = not st.get("backfilled") or int(st.get("backfill_days") or 0) < args.backfill_days
    page = int(st.get("resume_page") or 0) if initial else 0
    limit = backfill_page_limit(args)
    base = _pub_base(pub)
    fetched = new = filled = 0
    done = False                # reached the window's end, the archive's end or (daily) known stories
    gave_up = False             # (backfill) resumed too deep — the pager is most likely broken
    oldest = None
    seen_run: set[str] = set()  # story URLs listed on the pages read in THIS run
    known_ids = set(recs) if known is None else known
    newest_key = None           # (daily) newest issue on page 0
    while True:
        if initial and page >= limit:
            gave_up = True
            st["error"] = (f"backfill given up at page {page} (limit {limit} for {args.backfill_days} days) — "
                           f"the archive's pager may be broken")
            errors.append(f"{pub}: archive backfill given up at page {page} — the site's page links may be broken")
            log.warning("%s archive: %s", pub, st["error"])
            break
        if fetched >= args.archive_pages:
            log.info("%s archive: page cap (%d) reached — continuing next run at page %d", pub, fetched, page)
            break
        if time.monotonic() - t0 > args.max_seconds:
            log.info("%s archive: time budget reached — continuing next run at page %d", pub, page)
            break
        url = archive_url(base, pub, page)
        html = http.get_text(url)
        fetched += 1
        if not html:
            st["error"] = f"page {page}: no answer"
            log.warning("%s archive unavailable: %s", pub, url)
            break
        try:
            rows, has_next = parse_archive(html, url, pub)
        except Exception as e:
            log.exception("%s archive parse failed (%s)", pub, url)
            st["error"] = f"page {page}: parse error {type(e).__name__}"
            break
        if not rows:
            if page == 0:
                errors.append(f"{pub}: the archive page lists no stories (layout changed?)")
                st["error"] = "page 0: no stories found"
            else:
                done = True             # past the last page
            break
        page_urls = {r["url"] for r in rows}
        if seen_run and page_urls <= seen_run:
            if not has_next:
                done = True             # a last page holding only stories pushed down by a new one
                break
            # every story on this page was already on an earlier page of this run and the page still
            # offers "Next": the site ignores ?page= (or loops) — stop instead of reading the same page
            # up to the page cap; an unfinished backfill retries from this page next run
            st["error"] = f"page {page} repeats stories of earlier pages (pager broken?)"
            errors.append(f"{pub}: archive page {page} repeats earlier pages — the site's page links may be broken")
            log.warning("%s archive: %s — stopped", pub, st["error"])
            break
        seen_run |= page_urls
        page_new = 0                # records this page created
        page_unknown = 0            # stories not known before this run (the hub may have added them today)
        for row in rows:
            day = f"{row['issue_key']}-01"
            oldest = min(oldest or day, day)
            iid = url_to_id.get(row["url"]) or f"{pub}:{row['issue_key']}:{row['slug']}"
            is_new = iid not in recs          # no record yet → the row fills every field
            if is_new and day < cutoff:
                continue                # older than the backfill window
            rec = recs.setdefault(iid, {"id": iid, "url": row["url"], "publication": pub})
            changed = merge_listing(rec, row, is_new)
            url_to_id[row["url"]] = iid
            listed.add(iid)
            if rec.get("status") == "gone":        # the archive lists it again → it exists
                rec["status"] = "ok"
                changed = True
            if iid not in known_ids:
                page_unknown += 1
            if is_new:
                page_new += 1
            elif changed:
                filled += 1
            if changed:
                touched.add(iid)
        new += page_new
        if min(f"{r['issue_key']}-01" for r in rows) < cutoff or not has_next:
            done = True
            break
        if not initial:
            newest_key = newest_key or max(r["issue_key"] for r in rows)
            floor = _add_months(newest_key, -ISSUE_MONTHS.get(pub, 1))   # the issue before the newest
            if page_unknown == 0 and any(r["issue_key"] < floor for r in rows):
                done = True             # daily run: past the two newest issues and nothing new here
                break
        page += 1
    st.update({"last_run": now_iso(), "pages_last_run": fetched, "new_last_run": new})
    if oldest:
        st["oldest_seen"] = oldest[:7]
    if initial:
        if done or gave_up:
            st.update({"backfilled": now_iso(), "backfill_days": args.backfill_days})
            st.pop("resume_page", None)
            if gave_up:
                st["backfill_stopped_at"] = page
            else:
                st.pop("backfill_stopped_at", None)
        else:
            st["resume_page"] = page
    log.info("%s archive: %d page(s), %d new stor%s, %d gap(s) filled%s", pub, fetched, new,
             "y" if new == 1 else "ies", filled, " (initial backfill)" if initial else "")
    return st


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
    label = tidy_label(rec.get("issue_label") or issue.get("label")) or label_from_key(pub, key)
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
    """Everything the article page can tell us is known. Grapevine "Online Exclusive" pages print no
    section ("August 2026 | Online Exclusive | Topic"), so for them title + paywall flag is complete —
    otherwise each one would be fetched again after 7 and 14 days for a section that never comes.
    (`free` is only ever set from the article page, so an exclusive listed in the archive but never
    read is still incomplete.)"""
    if not rec.get("title") or rec.get("free") is None:
        return False
    return bool(rec.get("section") or rec.get("online_exclusive"))


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
    ap.add_argument("--max-byline-details", type=int, default=BYLINE_DETAILS,
                    help=f"extra article pages fetched only to learn a missing author/place (default {BYLINE_DETAILS})")
    ap.add_argument("--max-seconds", type=int, default=480, help="stop fetching after N seconds")
    ap.add_argument("--archive-pages", type=int, default=ARCHIVE_PAGES,
                    help=f"max archive pages per publication this run (default {ARCHIVE_PAGES})")
    ap.add_argument("--backfill-days", type=int, default=BACKFILL_DAYS,
                    help=f"how far back the archive is read (default {BACKFILL_DAYS} days)")
    ap.add_argument("--no-archive", action="store_true", help="skip the archive listing (hubs only)")
    ap.add_argument("--no-details", action="store_true", help="hubs + archive listing only (no article pages)")
    ap.add_argument("--only", choices=["gv", "lv"], help="process only one publication")
    ap.add_argument("--dry-run", action="store_true", help="do not write data/raw/articles.json")
    args = ap.parse_args(argv)
    try:
        _run(args)
    except Exception as e:
        # Keep yesterday's items AND the envelope extras (issues map, retry/archive state) on any surprise.
        log.exception("articles failed")
        if not args.dry_run:
            prev = load_raw(SOURCE)
            save_raw(SOURCE, prev.get("items", []), ok=False, error=f"{type(e).__name__}: {e}"[:300],
                     stats=prev.get("stats"),
                     extra={"issues": prev.get("issues") or {}, "detail_state": prev.get("detail_state") or {},
                            "archive_state": prev.get("archive_state") or {}})


def _run(args) -> None:
    t0 = time.monotonic()
    http = shared_session()
    prev = load_raw(SOURCE)
    prev_items = prev.get("items", [])
    prev_by_id = {i["id"]: i for i in prev_items if i.get("id")}
    detail_state: dict = dict(prev.get("detail_state") or {})
    issues: dict = dict(prev.get("issues") or {})
    archive_state: dict = dict(prev.get("archive_state") or {})
    errors: list[str] = []
    stats: dict = {"hub_articles": 0, "details_fetched": 0, "details_failed": 0, "thumbs": 0}

    # working records keyed by id, seeded from what we already know
    recs: dict[str, dict] = {}
    touched: set[str] = set()
    for it in prev_items:
        ex = it.get("extra") or {}
        pub = ex.get("publication") or it.get("category")
        if pub not in PUBS:
            continue
        rec = {k: v for k, v in ex.items() if v is not None}
        rec.update({"id": it["id"], "url": it["url"], "title": it.get("title"), "image": it.get("image"),
                    "publication": pub, "status": it.get("status")})
        if rec.get("issue_label") and tidy_label(rec["issue_label"]) != rec["issue_label"]:
            rec["issue_label"] = tidy_label(rec["issue_label"])     # "june 2026" saved by an earlier run
            touched.add(it["id"])
        if not rec.get("department") and EVERY_ISSUE_RE.search(str(rec.get("section") or "")):
            rec["department"] = True                                # section read by an earlier run
            touched.add(it["id"])
        recs[it["id"]] = rec
    on_hub: set[str] = set()        # listed on a magazine hub or the archive today → certainly not deleted
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

    # ---- 2. archive listing (backfill once, then only what is new)
    if not args.no_archive:
        stats.update({"archive_pages": 0, "archive_new": 0})
        for pub in PUBS:
            if args.only and args.only != pub:
                continue
            try:
                archive_state[pub] = walk_archive(http, pub, recs, url_to_id, touched, on_hub,
                                                  archive_state.get(pub) or {}, args, t0, errors,
                                                  known=set(prev_by_id))
            except Exception as e:    # the archive is extra — never lose the hub results over it
                log.exception("%s archive failed", pub)
                archive_state[pub] = {**(archive_state.get(pub) or {}), "error": f"{type(e).__name__}: {e}"[:200]}
                continue
            stats["archive_pages"] += archive_state[pub].get("pages_last_run", 0)
            stats["archive_new"] += archive_state[pub].get("new_last_run", 0)

    # ---- 3. article details: missing bylines inside the window first, then Texas writers (their cards
    #         lead the published-writers spotlight and want their section + thumbnail), then newest issues
    window_day = (datetime.now(timezone.utc).date() - timedelta(days=args.backfill_days)).isoformat()
    if not args.no_details:
        now = datetime.now(timezone.utc)
        mine = [r for r in recs.values() if not args.only or r["publication"] == args.only]
        by_line = [r for r in mine if _needs_byline(detail_state.get(r["id"]), r, window_day)]
        ids = {r["id"] for r in by_line}
        rest = [r for r in mine if r["id"] not in ids and _needs_detail(detail_state.get(r["id"]), r, now)]
        by_line.sort(key=lambda r: (r.get("issue_key") or ""), reverse=True)
        rest.sort(key=lambda r: (_texas_writer(r), r.get("issue_key") or ""), reverse=True)
        todo = by_line + rest
        stats["details_pending"] = len(todo)
        stats["byline_pending"] = len(by_line)
        used = {"byline": 0, "detail": 0}
        for r in todo:
            kind = "byline" if r["id"] in ids else "detail"
            if used[kind] >= (args.max_byline_details if kind == "byline" else args.max_details):
                continue
            if time.monotonic() - t0 > args.max_seconds:
                log.info("time budget reached; %d article pages left for tomorrow",
                         len(todo) - stats["details_fetched"] - stats["details_failed"])
                break
            used[kind] += 1
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
                    r["issue_label"] = tidy_label(d["issue_label"])
            for k in ("title", "topic", "section", "author", "author_location", "subtitle", "free", "online_exclusive"):
                if d.get(k) is not None and d.get(k) != "":
                    r[k] = d[k]
            if d.get("teaser") and not r.get("teaser"):
                r["teaser"] = d["teaser"]
            if EVERY_ISSUE_RE.search(str(d.get("section") or "")):
                r["department"] = True           # the page itself says "In Every Issue"
            if not r.get("image") and d.get("image") and _wants_thumb(r, issues):
                thumb = local_thumb(http, d["image"], short_hash(r["id"], 16))
                if thumb:
                    r["image"] = thumb[0]
                    stats["thumbs"] += 1
            st["ok"] = True
            if _lacks_byline(r):
                st["byline_at"] = st["at"]      # the page has no more to tell — do not ask again
            detail_state[r["id"]] = st
            touched.add(r["id"])
            stats["details_fetched"] += 1
        stats["details_left"] = max(0, len(todo) - stats["details_fetched"] - stats["details_failed"])

    # ---- 4. assemble + save
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
    # Keep detail state only where it still matters (incomplete or gone records that still exist, and
    # "no byline on the page" marks inside the backfill window), so the envelope doesn't grow by ~50
    # entries a month forever.
    by_id = {i["id"]: i for i in merged}

    def _rec_of(k: str) -> dict:
        return {**(by_id[k].get("extra") or {}), "title": by_id[k].get("title")}

    detail_state = {k: v for k, v in detail_state.items()
                    if k in by_id and (v.get("gone") or v.get("missing_since") or not _is_complete(_rec_of(k))
                                       or (v.get("byline_at") and _issue_day(_rec_of(k)) >= window_day))}

    if args.dry_run:
        print(json.dumps({"stats": stats, "errors": errors, "issues": issues, "archive_state": archive_state,
                          "sample": [i for i in merged if i["id"] in touched][:5]}, ensure_ascii=False, indent=1))
        return
    ok = not errors
    save_raw(SOURCE, merged, ok=ok, error="; ".join(errors) if errors else None, stats=stats,
             extra={"issues": issues, "detail_state": detail_state, "archive_state": archive_state})
    log.info("articles: %d total, %d new, %d details fetched%s", len(merged), added, stats["details_fetched"],
             f" — errors: {errors}" if errors else "")


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
