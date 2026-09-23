"""Committee-written content that lives in the repository (edited on github.com):

  content/announcements/*.md  → data/raw/announcements.json   (kind "announcement")
  content/events/*.md         → data/raw/manual_events.json   (kind "event")

Each file is Markdown with a small YAML header ("front matter"), e.g.

    ---
    title: Welcome, new GVRs and RLVs!
    date: 2027-01-10
    expires: 2027-03-31     # optional — hidden after this date
    pinned: false           # optional — keep at the top
    ---
    Write in English **or** Spanish — the site translates automatically.

Events use `title, start, end, location, url` (+ optional `online_url`, `flyer`, `image`).
Files whose name starts with "_" or "README" are ignored; other files that do not end in .md
(any capitalization) are skipped and listed on /status/. The folder is the source of truth:
deleting a file removes the item, and deleting a header line removes that value. A file with a formatting mistake is skipped and reported
on the /status/ page instead of breaking the daily update.

    python -m scripts.sync.announcements [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .common import (CONTENT_DIR, clean_text, date_from_text, get_logger, load_config, load_raw, make_item,
                     merge_items, run_module, save_raw, slugify, to_iso, truncate)
from .translate import detect_language

log = get_logger("announcements")

ANN_DIR = CONTENT_DIR / "announcements"
EVENTS_DIR = CONTENT_DIR / "events"
_FM = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.S)


# --------------------------------------------------------------------------- parsing helpers
MD_SUFFIXES = (".md", ".markdown")


def _skipped_name(name: str) -> bool:
    """Help files and hidden files are never content: _template.md, README.md, .gitkeep …"""
    return name.startswith(("_", ".")) or name.upper().startswith("README")


def content_files(folder: Path) -> list[Path]:
    """The Markdown files of a content folder. The extension is matched case-insensitively
    ('Spring.MD' works) — the same on Windows and on the Linux machine of the daily update."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in MD_SUFFIXES and not _skipped_name(p.name))


def ignored_files(folder: Path) -> list[Path]:
    """Other files in a content folder (e.g. 'Assembly.txt'): not read, reported on /status/."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() not in MD_SUFFIXES and not _skipped_name(p.name))


def read_front_matter(path: Path) -> tuple[dict, str]:
    """Return (front matter dict, Markdown body). Raises ValueError with a friendly message."""
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    m = _FM.match(text)
    if not m:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark else ""
        raise ValueError(f"the header between the --- lines is not valid{where}: {getattr(e, 'problem', e)}")
    if not isinstance(meta, dict):
        raise ValueError("the header between the --- lines must be 'name: value' lines")
    return meta, m.group(2).strip()


def markdown_to_text(md: str) -> str:
    """Plain-text teaser from Markdown (links → their label, formatting marks removed)."""
    t = re.sub(r"```.*?```", " ", md or "", flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                     # images
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                  # links → label
    t = re.sub(r"<(https?://[^>]+)>", r"\1", t)
    t = re.sub(r"(?m)^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+", "", t)   # headings, quotes, bullets
    t = re.sub(r"(\*\*|__|\*|_|~~|`)(?=\S)(.+?)(?<=\S)\1", r"\2", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return clean_text(t)


def as_date(v) -> str | None:
    """YAML date / 'YYYY-MM-DD' / 'March 5, 2027' → 'YYYY-MM-DD'."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            return None
    d, _ = date_from_text(s)
    return d


def as_when(v, tz: ZoneInfo) -> tuple[str | None, bool]:
    """Event start/end → (ISO UTC datetime 'Z' or 'YYYY-MM-DD', all_day)."""
    if v is None or v == "":
        return None, False
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=tz)
        return to_iso(dt), False
    if isinstance(v, date):
        return v.isoformat(), True
    s = str(v).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return date.fromisoformat(s).isoformat(), True
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
        return to_iso(dt if dt.tzinfo else dt.replace(tzinfo=tz)), False
    except ValueError:
        pass
    try:  # "March 14, 2027 9:00 AM"
        from dateutil import parser as dparser
        dt = dparser.parse(s)
        return to_iso(dt if dt.tzinfo else dt.replace(tzinfo=tz)), False
    except Exception:
        d = as_date(s)
        return (d, True) if d else (None, False)


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "y", "si", "sí", "on")


def pick_lang(meta: dict, text: str) -> str:
    lang = str(meta.get("lang") or meta.get("language") or "").strip().lower()[:2]
    return lang if lang in ("en", "es") else detect_language(text, "en")


def as_tags(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = re.split(r"[,;]", v)
    return [clean_text(t) for t in v if clean_text(t)]


def city_state(location: str) -> tuple[str | None, str | None]:
    m = re.search(r"([A-Za-zÀ-ÿ .'-]+),\s*(TX|Texas|[A-Z]{2})\b", location or "")
    if not m:
        return None, None
    st = "TX" if m.group(2) in ("TX", "Texas") else m.group(2)
    return clean_text(m.group(1).split(",")[-1]), st


# --------------------------------------------------------------------------- builders
def parse_announcement(path: Path) -> dict:
    meta, body = read_front_matter(path)
    stem = path.stem
    file_date, rest = date_from_text(stem)
    title = clean_text(meta.get("title"))
    if not title:
        h = re.search(r"(?m)^#{1,3}\s+(.+)$", body)
        title = clean_text(h.group(1)) if h else clean_text(re.sub(r"[-_]+", " ", rest or stem)).capitalize()
        if h:
            body = (body[:h.start()] + body[h.end():]).strip()
    if not title:
        raise ValueError("it has no title (add a line 'title: …' to the header)")
    when = as_date(meta.get("date")) or file_date
    if meta.get("date") and not as_date(meta.get("date")):
        raise ValueError(f"the date '{meta.get('date')}' is not a date (use YYYY-MM-DD)")
    expires = as_date(meta.get("expires"))
    slug = slugify(stem)
    text = markdown_to_text(body)
    summary = clean_text(meta.get("summary")) or text
    image = clean_text(meta.get("image")) or None
    link = clean_text(meta.get("url") or meta.get("link")) or None
    return make_item(
        id=f"ann:{slug}", source="committee", kind="announcement", url=link or f"/announcements/#{slug}",
        title=title, summary=truncate(summary, 400), lang=pick_lang(meta, f"{title}. {text}"), date=when,
        image=image, tags=as_tags(meta.get("tags")), category="manual",
        extra={"body_md": body, "expires": expires, "pinned": as_bool(meta.get("pinned")), "slug": slug,
               "file": f"content/announcements/{path.name}", "link": link},
    )


def parse_event(path: Path, tz: ZoneInfo) -> dict:
    meta, body = read_front_matter(path)
    stem = path.stem
    file_date, rest = date_from_text(stem)
    title = clean_text(meta.get("title")) or clean_text(re.sub(r"[-_]+", " ", rest or stem)).capitalize()
    start, all_day = as_when(meta.get("start") or meta.get("date"), tz)
    if not start and file_date:
        start, all_day = file_date, True
    if not start:
        raise ValueError("it has no start date (add a line 'start: 2027-03-14' or "
                         "'start: 2027-03-14T09:00:00-05:00' to the header)")
    end, _ = as_when(meta.get("end"), tz)
    location = clean_text(meta.get("location"))
    city, state = city_state(location)
    slug = slugify(stem)
    text = markdown_to_text(body)
    online = clean_text(meta.get("online_url") or meta.get("zoom")) or None
    flyer = clean_text(meta.get("flyer") or meta.get("flyer_url")) or None
    image = clean_text(meta.get("image") or meta.get("flyer_thumb")) or None
    return make_item(
        id=f"ev:manual:{slug}", source="committee", kind="event",
        url=clean_text(meta.get("url")) or f"/events/#{slug}", title=title, summary=truncate(text, 400),
        lang=pick_lang(meta, f"{title}. {text}"), date=start, image=image, tags=as_tags(meta.get("tags")),
        category="manual",
        extra={"start": start, "end": end, "all_day": all_day, "location": location or None, "online_url": online,
               "flyer_url": flyer, "flyer_thumb": image, "city": city, "state": state, "body_md": body,
               "slug": slug, "file": f"content/events/{path.name}"},
    )


def collect(folder: Path, parser, label: str) -> tuple[list[dict], list[str]]:
    items, errors = [], []
    seen: set[str] = set()
    for path in content_files(folder):
        try:
            it = parser(path)
        except Exception as e:  # one bad file never blocks the others
            msg = f"{folder.name}/{path.name}: {e}"
            log.warning("skipped %s", msg)
            errors.append(msg[:240])
            continue
        if it["id"] in seen:
            errors.append(f"{folder.name}/{path.name}: duplicate name")
            continue
        seen.add(it["id"])
        items.append(it)
    for path in ignored_files(folder):
        msg = f"{folder.name}/{path.name}: ignored — only files ending in .md are read (rename it to end in .md)"
        log.warning("%s", msg)
        errors.append(msg[:240])
    log.info("%s: %d file(s), %d problem(s)", label, len(items), len(errors))
    return items, errors


def finalize(prev: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """The folder is the source of truth → drop items whose file was deleted, and take every field
    from today's file (a line removed from the header really disappears — authoritative merge; only
    first_seen is remembered). Items without a date get the day they first appeared on the site."""
    merged, added = merge_items(prev, new, drop_missing=True, authoritative=True)
    for it in merged:
        if not it.get("date") and it.get("first_seen"):
            it["date"] = it["first_seen"][:10]
    return merged, added


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the items, do not write data/raw")
    a = ap.parse_args(argv)
    tz = ZoneInfo(load_config().get("site", {}).get("timezone", "America/Chicago"))

    anns, ann_err = collect(ANN_DIR, parse_announcement, "announcements")
    events, ev_err = collect(EVENTS_DIR, lambda p: parse_event(p, tz), "events")

    if a.dry_run:
        print(json.dumps({"announcements": anns, "events": events, "errors": ann_err + ev_err},
                         ensure_ascii=False, indent=1, default=str))
        return

    today = datetime.now(timezone.utc).date().isoformat()
    ann_items, ann_new = finalize(load_raw("announcements").get("items", []), anns)
    save_raw("announcements", ann_items, ok=True, stats={
        "files": len(anns), "new": ann_new, "problems": len(ann_err), "errors": ann_err,
        "active": sum(1 for i in ann_items if not (i["extra"].get("expires") and i["extra"]["expires"] < today)),
    })
    ev_items, ev_new = finalize(load_raw("manual_events").get("items", []), events)
    save_raw("manual_events", ev_items, ok=True, stats={
        "files": len(events), "new": ev_new, "problems": len(ev_err), "errors": ev_err,
    })


if __name__ == "__main__":
    raise SystemExit(run_module("announcements", main))
