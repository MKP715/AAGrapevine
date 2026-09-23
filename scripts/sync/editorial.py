"""Upcoming editorial themes & submission deadlines → data/raw/editorial.json

Sources (config: sources.grapevine.contribute / sources.lavina.contribute):

* Grapevine — https://www.aagrapevine.org/contribute ("Editorial Calendar")
  The page lists, per calendar year, every monthly issue with its theme and deadline:
      Grapevine Editorial Calendar 2027
        JANUARY
        Spiritual Awakenings (stories due June 1, 2026)
        Share your personal journey with Step Two. …
  One issue can carry two themes (December: "Remote Communities" + "Sober Holidays!"), and
  some have no deadline ("Classic Grapevine"). A PDF version is linked from the page.

* La Viña — https://www.aalavina.org/temas-sugeridos ("Temas sugeridos")
  La Viña does not publish an issue-by-issue calendar; this page is a list of evergreen
  story suggestions. They are stored as topics with extra.evergreen = true and no issue/deadline.
  (If a suggestion ever carries a "fecha límite …" date, it is picked up as the deadline.)

Items (kind "topic", see docs/DATA_SCHEMA.md):
    title = theme, summary = the editors' prompt text, date = deadline (YYYY-MM-DD) or null
    extra = publication, issue_label, issue_key, deadline, theme, evergreen, pdf_url, submit_url

Only the current/future issues plus the most recent past one are kept. The editorial calendar is
authoritative, so when a page parses successfully its list REPLACES that publication's previous
topics (first_seen is still preserved). When a page can't be fetched/parsed, the previous topics
for that publication are kept and the envelope reports ok=false with a clear error.

Run:  python -m scripts.sync.editorial [--dry-run] [--gv-html FILE] [--lv-html FILE]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .common import (MONTHS, clean_text, date_from_text, detect_lang, get_logger, load_config, load_raw,
                     make_item, merge_items, run_module, save_raw, shared_session, slugify, truncate)

SOURCE = "editorial"
log = get_logger(SOURCE)

EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]
ES_MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
             "Octubre", "Noviembre", "Diciembre"]
_FULL_MONTH_RE = re.compile(r"(?i)^(" + "|".join(EN_MONTHS + ES_MONTHS) + r")\s*:?$")
_CAL_YEAR_RE = re.compile(r"(?i)(?:editorial\s+calendar|calendario\s+editorial)\D{0,20}(20\d\d)")
# "Spiritual Awakenings (stories due June 1, 2026) optional description…"
_THEME_RE = re.compile(
    r"^(?P<theme>.+?)\s*\(\s*(?:stories|articles|submissions|historias|art[íi]culos)?\s*"
    r"(?:due|deadline|fecha\s+l[íi]mite)\s*(?:by|on|:)?\s*(?P<due>[^)]*)\)\s*(?P<rest>.*)$", re.I)
_BLOCK_TAGS = ["p", "li", "div", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
               "article"]


# --------------------------------------------------------------------------- helpers
def _today_central() -> date:
    tz = load_config().get("site", {}).get("timezone", "America/Chicago")
    return datetime.now(ZoneInfo(tz)).date()


def _add_months(key: str, n: int) -> str:
    y, m = map(int, key.split("-"))
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _block_lines(el) -> list[str]:
    """Visible text split on block elements / <br>, keeping inline elements (em, strong, a) together."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    for b in el.find_all(_BLOCK_TAGS):
        b.insert_before("\n")
        b.insert_after("\n")
    return [ln for ln in (clean_text(x) for x in el.get_text("").split("\n")) if ln]


def _main(soup: BeautifulSoup):
    main = soup.find("main") or soup.body or soup
    for t in main.find_all(["script", "style", "noscript", "svg", "nav", "form", "iframe"]):
        t.decompose()
    return main


def parse_deadline(text: str, issue_year: int, issue_month: int) -> str | None:
    """'June 1, 2026' / 'Sept. 1, 2026' / 'Jan 1 2027' → ISO date. When the year is missing
    ('June 1') pick the latest such date before the issue month (deadlines precede issues)."""
    iso, _ = date_from_text(text)   # needs a 20xx year; "Month YYYY" alone → 1st of that month
    if iso:
        return iso
    m = re.search(r"(?i)\b([a-záéíóú]+)\.?\s+(\d{1,2})\b", text)
    if m and m[1].lower() in MONTHS:
        mo, d = MONTHS[m[1].lower()], int(m[2])
        for y in (issue_year, issue_year - 1):
            try:
                cand = date(y, mo, d)
            except ValueError:
                continue
            if cand < date(issue_year, issue_month, 1):
                return cand.isoformat()
    return None


# --------------------------------------------------------------------------- Grapevine
def parse_gv(html: str, page_url: str) -> tuple[list[dict], dict]:
    """Return (rows, meta). rows: dicts with issue_year, issue_month, theme, deadline, description."""
    soup = BeautifulSoup(html, "lxml")
    main = _main(soup)
    meta: dict = {}
    for a in main.find_all("a", href=True):
        href, label = a["href"], clean_text(a.get_text(" "))
        if href.lower().endswith(".pdf") and not meta.get("pdf_url"):
            meta["pdf_url"] = urljoin(page_url, href)
        elif re.search(r"(?i)submit", href + " " + label) and not meta.get("submit_url"):
            meta["submit_url"] = urljoin(page_url, href)
        elif re.search(r"(?i)guideline", href) and not meta.get("guidelines_url"):
            meta["guidelines_url"] = urljoin(page_url, href)
    m = re.search(r"(?i)no later than\s+(\w+)\s+months?\s+before", main.get_text(" "))
    if m:
        meta["lead_time"] = clean_text(m[0])

    rows: list[dict] = []
    year = month = None
    cur: dict | None = None
    just_saw_month = False
    for line in _block_lines(main):
        ym = _CAL_YEAR_RE.search(line)
        if ym:
            year, month, cur, just_saw_month = int(ym[1]), None, None, False
            continue
        if year is None:
            continue
        mm = _FULL_MONTH_RE.match(line)
        if mm:
            month = MONTHS[mm[1].lower()]
            cur, just_saw_month = None, True
            continue
        if month is None:
            continue
        tm = _THEME_RE.match(line)
        if tm and len(tm["theme"]) <= 120:
            cur = {"issue_year": year, "issue_month": month, "theme": clean_text(tm["theme"]).strip(" -–—:"),
                   "deadline": parse_deadline(tm["due"], year, month), "due_text": clean_text(tm["due"]),
                   "description": clean_text(tm["rest"])}
            rows.append(cur)
            just_saw_month = False
            continue
        if just_saw_month and len(line) <= 80 and not re.search(r"[.?!]$", line):
            # A theme with no deadline, e.g. "Classic Grapevine".
            cur = {"issue_year": year, "issue_month": month, "theme": line, "deadline": None,
                   "due_text": "", "description": ""}
            rows.append(cur)
            just_saw_month = False
            continue
        just_saw_month = False
        if cur is not None and len(cur["description"]) < 600:
            cur["description"] = clean_text(cur["description"] + " " + line)
    return rows, meta


# --------------------------------------------------------------------------- La Viña
def parse_lv(html: str, page_url: str) -> tuple[list[dict], dict]:
    """Evergreen suggestions = the bulleted (<li>) items of the page's main content. No bullets → no
    rows (the caller then keeps yesterday's list instead of publishing random page text)."""
    soup = BeautifulSoup(html, "lxml")
    main = _main(soup)
    meta: dict = {}
    intro = main.find(["p", "h1", "h2"])
    if intro:
        meta["intro"] = truncate(clean_text(intro.get_text(" ")), 300)
    content = main.select_one(".node--type-page, article") or main
    cands = [clean_text(li.get_text(" ")) for li in content.find_all("li")
             if not li.find_parent(["nav", "header", "footer"]) and not li.find("a", href=re.compile(r"^(?!#)"))]
    rows, seen = [], set()
    for c in cands:
        if not c or len(c) > 200 or re.search(r"(?i)regresar|p[áa]gina anterior|iniciar sesi[óo]n", c):
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        deadline = None
        issue_year = issue_month = None
        if re.search(r"(?i)fecha\s+l[íi]mite|antes del|hasta el", c):
            deadline, _ = date_from_text(c)
        theme = c.rstrip(" .")
        rows.append({"issue_year": issue_year, "issue_month": issue_month, "theme": theme,
                     "deadline": deadline, "due_text": "", "description": ""})
    return rows, meta


# --------------------------------------------------------------------------- items
def rows_to_items(rows: list[dict], pub: str, page_url: str, meta: dict) -> list[dict]:
    source = "grapevine" if pub == "gv" else "lavina"
    items = []
    for r in rows:
        y, mo = r.get("issue_year"), r.get("issue_month")
        issue_key = f"{y:04d}-{mo:02d}" if y and mo else None
        if issue_key:
            issue_label = f"{EN_MONTHS[mo - 1]} {y}" if pub == "gv" else f"{ES_MONTHS[mo - 1]} {y}"
        else:
            issue_label = None
        theme = r["theme"]
        tid = f"ed:{pub}:{issue_key or 'any'}:{slugify(theme, 48)}"
        extra = {
            "publication": pub,
            "issue_label": issue_label,
            "issue_key": issue_key,
            "deadline": r.get("deadline"),
            "theme": theme,
            "evergreen": issue_key is None,
            "due_text": r.get("due_text") or None,
            "pdf_url": meta.get("pdf_url"),
            "submit_url": meta.get("submit_url"),
            "guidelines_url": meta.get("guidelines_url"),
        }
        items.append(make_item(
            id=tid, source=source, kind="topic", url=page_url, title=theme,
            summary=r.get("description") or "",
            lang=detect_lang(f"{theme} {r.get('description') or ''}", prior="en" if pub == "gv" else "es"),
            date=r.get("deadline"), category=pub,
            extra={k: v for k, v in extra.items() if v is not None},
        ))
    return items


def keep_window(items: list[dict], today: date) -> list[dict]:
    """Keep evergreen topics, plus dated ones from the issue on sale now, later issues, and the one before.

    Magazines are dated ahead: in late September the October issue is on sale. So the "current" issue is
    the latest issue_key <= next month; we also keep the issue just before it."""
    dated = sorted({i["extra"]["issue_key"] for i in items if i["extra"].get("issue_key")})
    if not dated:
        return items
    horizon = _add_months(f"{today.year:04d}-{today.month:02d}", 1)
    past_or_now = [k for k in dated if k <= horizon]
    if past_or_now:
        current = past_or_now[-1]
        idx = dated.index(current)
        start = dated[idx - 1] if idx > 0 else current
    else:
        start = dated[0]
    return [i for i in items if not i["extra"].get("issue_key") or i["extra"]["issue_key"] >= start]


# --------------------------------------------------------------------------- main
def _fetch(url: str, html_file: str | None) -> str | None:
    if html_file:
        with open(html_file, encoding="utf-8") as f:
            return f.read()
    return shared_session().get_text(url)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Editorial themes & deadlines (Grapevine / La Viña)")
    ap.add_argument("--dry-run", action="store_true", help="print items, do not write data/raw")
    ap.add_argument("--gv-html", help="parse this saved Grapevine /contribute HTML instead of fetching")
    ap.add_argument("--lv-html", help="parse this saved La Viña /temas-sugeridos HTML instead of fetching")
    ap.add_argument("--only", choices=["gv", "lv"], help="process only one publication")
    ap.add_argument("--force", action="store_true", help="accept a much shorter list than yesterday's")
    args = ap.parse_args(argv)

    src = load_config().get("sources", {}) or {}
    pages = {
        "gv": (src.get("grapevine", {}).get("base") or "https://www.aagrapevine.org").rstrip("/")
        + (src.get("grapevine", {}).get("contribute") or "/contribute"),
        "lv": (src.get("lavina", {}).get("base") or "https://www.aalavina.org").rstrip("/")
        + (src.get("lavina", {}).get("contribute") or "/temas-sugeridos"),
    }
    prev = load_raw(SOURCE)
    prev_items = prev.get("items", [])
    today = _today_central()

    fresh: list[dict] = []          # items from pages that parsed OK
    kept: list[dict] = []           # previous items of publications that failed today
    errors: list[str] = []
    stats: dict = {}
    for pub in ("gv", "lv"):
        prev_pub = [i for i in prev_items if (i.get("extra") or {}).get("publication") == pub]
        if args.only and args.only != pub:
            kept += prev_pub
            continue
        url = pages[pub]
        html = _fetch(url, args.gv_html if pub == "gv" else args.lv_html)
        if not html:
            errors.append(f"{pub}: could not fetch {url}")
            kept += prev_pub
            continue
        try:
            rows, meta = (parse_gv if pub == "gv" else parse_lv)(html, url)
        except Exception as e:
            log.exception("%s parse error", pub)
            errors.append(f"{pub}: parse error {type(e).__name__}: {e}"[:200])
            kept += prev_pub
            continue
        if not rows:
            errors.append(f"{pub}: no themes found on {url} (page layout changed?)")
            kept += prev_pub
            continue
        items = rows_to_items(rows, pub, url, meta)
        # Duplicate ids can happen if the same theme is listed twice for one issue — keep the first.
        uniq = {}
        for it in items:
            uniq.setdefault(it["id"], it)
        items = list(uniq.values())
        window = keep_window(items, today)
        # Sanity check: a half-rendered or maintenance page can "parse" into a much shorter list.
        # Losing most topics overnight is far more likely a site glitch than an editorial decision.
        if len(prev_pub) >= 6 and len(window) < len(prev_pub) * 0.4 and not args.force:
            errors.append(f"{pub}: only {len(window)} themes found (had {len(prev_pub)}) — kept the previous "
                          f"list; run with --force to accept")
            kept += prev_pub
            continue
        stats[pub] = {"parsed": len(items), "kept": len(window),
                      "with_deadline": sum(1 for i in window if i["extra"].get("deadline")),
                      "open": sum(1 for i in window if (i["extra"].get("deadline") or "") >= today.isoformat())}
        log.info("%s: %d themes parsed, %d in window", pub, len(items), len(window))
        fresh += window

    if args.dry_run:
        print(json.dumps(fresh, ensure_ascii=False, indent=1))
        print("errors:", errors)
        return

    # Previous items for the publications refreshed today are replaced (drop_missing on that subset);
    # items of failed publications are carried over unchanged.
    refreshed_pubs = {i["extra"]["publication"] for i in fresh}
    old_for_refreshed = [i for i in prev_items if (i.get("extra") or {}).get("publication") in refreshed_pubs]
    merged, added = merge_items(old_for_refreshed, fresh, drop_missing=True)
    stats["new"] = added
    all_items = merged + kept
    ok = not errors
    save_raw(SOURCE, all_items, ok=ok, error="; ".join(errors) if errors else None, stats=stats)
    log.info("editorial: %d topics (%d new)%s", len(all_items), added, f" — errors: {errors}" if errors else "")


if __name__ == "__main__":
    raise SystemExit(run_module(SOURCE, main))
