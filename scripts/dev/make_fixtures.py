"""DEV ONLY: build realistic data/site/*.json fixtures from probe files in .tmp/probe/
so page templates can be developed before the real sync runs.
The real pipeline (scripts/sync/run_all.py) overwrites every file written here.

    python -m scripts.dev.make_fixtures
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
from bs4 import BeautifulSoup

from scripts.sync.common import (MODELS_DIR, ROOT, SITE_DIR, clean_text, date_from_text, detect_lang, now_iso,
                                 pretty_filename, short_hash, strip_html, truncate, write_json)

PROBE = ROOT / ".tmp" / "probe"
NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------- tiny translator
_tr = {}


def translate(texts, src, tgt):
    if not texts or src == tgt or src not in ("en", "es"):
        return list(texts)
    try:
        import ctranslate2
        import sentencepiece as spm
    except Exception:
        return list(texts)
    key = f"{src}_{tgt}"
    if key not in _tr:
        d = MODELS_DIR / key
        if not d.exists():
            return list(texts)
        _tr[key] = (ctranslate2.Translator(str(d / "model"), device="cpu", compute_type="int8"),
                    spm.SentencePieceProcessor(model_file=str(d / "sentencepiece.model")))
    t, sp = _tr[key]
    out = t.translate_batch([sp.encode(x or ".", out_type=str) for x in texts], beam_size=2, max_decoding_length=200)
    return [sp.decode(r.hypotheses[0]).strip() if x else "" for r, x in zip(out, texts)]


def add_i18n(items, fields=("title", "summary")):
    for lang in ("en", "es"):
        other = "es" if lang == "en" else "en"
        group = [i for i in items if i.get("lang", "en") == lang]
        for f in fields:
            vals = [i.get(f, "") or "" for i in group]
            tr = translate(vals, lang, other)
            for i, v, t in zip(group, vals, tr):
                i.setdefault("i18n", {})[f] = {lang: v, other: t}
    for i in items:
        if i.get("lang") in ("en", "es"):
            i["machine"] = ["es" if i["lang"] == "en" else "en"]
        else:
            i["machine"] = []
            for f in fields:
                i.setdefault("i18n", {})[f] = {"en": i.get(f, ""), "es": i.get(f, "")}
        d = i.get("date")
        try:
            dd = datetime.fromisoformat(str(d).replace("Z", "+00:00")) if d and "T" in str(d) else (datetime.fromisoformat(d + "T12:00:00+00:00") if d else None)
        except Exception:
            dd = None
        i["is_new"] = bool(dd and (NOW - dd).days < 14 and dd <= NOW + timedelta(days=1))
    return items


def item(**kw):
    base = dict(id="", source="", kind="", url="", title="", summary="", lang="en", date=None, first_seen=now_iso(),
                last_seen=now_iso(), image=None, tags=[], category=None, status="ok", extra={})
    base.update(kw)
    base["summary"] = truncate(base.get("summary") or "", 400)
    return base


def envelope(items):
    return {"updated": now_iso(), "fixture": True, "items": items}


# ---------------------------------------------------------------- builders
def videos():
    f = feedparser.parse(str(PROBE / "yt_gv.xml"))
    out = []
    for e in f.entries:
        vid = e.get("yt_videoid")
        lang = detect_lang(e.title, "en")
        out.append(item(id=f"yt:{vid}", source="youtube", kind="video", url=e.link, title=e.title,
                        summary=strip_html(e.get("summary", "")), lang=lang, date=e.published.replace("+00:00", "Z"),
                        image=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", category="lv" if lang == "es" else "gv",
                        extra={"video_id": vid, "channel_id": "UCI9uFLJ__aXT3-At0PlPWUQ", "playlists": [], "is_short": "/shorts/" in e.link}))
    return out


def episodes():
    f = feedparser.parse(str(PROBE / "pod_gv.xml"))
    out = []
    for e in f.entries[:60]:
        audio = next((l.href for l in e.get("links", []) if l.get("rel") == "enclosure"), None)
        dur = e.get("itunes_duration", "0")
        secs = sum(int(x) * 60 ** i for i, x in enumerate(reversed(str(dur).split(":")))) if dur and re.fullmatch(r"[\d:]+", str(dur)) else 0
        img = (e.get("image") or {}).get("href") or (f.feed.get("image") or {}).get("href")
        d = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(item(id=f"pod:{short_hash(e.get('id', e.link))}", source="podcast", kind="episode", url=e.get("link") or "",
                        title=e.title, summary=strip_html(e.get("summary", "")), lang=detect_lang(e.title + " " + strip_html(e.get("summary", ""))[:200], "en"),
                        date=d, image=img, category="gv",
                        extra={"audio_url": audio, "duration_sec": secs, "season": e.get("itunes_season"), "episode": e.get("itunes_episode"),
                               "show": "gv", "show_name": "AA Grapevine's Podcast", "link": e.get("link")}))
    return out


def articles():
    out = []
    soup = BeautifulSoup((PROBE / "gv_magazine.html").read_text(encoding="utf-8", errors="replace"), "lxml")
    seen = set()
    for a in soup.select('a[href*="/magazine/2026/"]'):
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        title = clean_text(a.get_text()) or slug.replace("-", " ").title()
        if len(title) < 4:
            title = slug.replace("-", " ").title()
        out.append(item(id=f"gv:{slug}", source="grapevine", kind="article", url=href, title=title,
                        summary="Many of my friends know that my wife is a beekeeper." if "village" in slug else "",
                        lang="en", date="2026-10-01", image=None, category="gv",
                        extra={"publication": "gv", "issue_label": "October 2026", "issue_key": "2026-10", "topic": "Loneliness",
                               "section": "Our Personal Stories", "author": "Anonymous", "free": None}))
    soup = BeautifulSoup((PROBE / "lv_home.html").read_text(encoding="utf-8", errors="replace"), "lxml")
    for a in soup.select('a[href*="/revista/septiembre-octubre-2026/"]'):
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        title = clean_text(a.get_text()) or slug.replace("-", " ").capitalize()
        if len(title) < 4:
            title = slug.replace("-", " ").capitalize()
        out.append(item(id=f"lv:{slug}", source="lavina", kind="article", url=href, title=title, summary="", lang="es",
                        date="2026-09-01", category="lv",
                        extra={"publication": "lv", "issue_label": "Septiembre-Octubre 2026", "issue_key": "2026-09", "topic": "",
                               "section": "", "author": "", "free": None}))
    return out


def pdfs():
    out, seen = [], set()
    for fname, host, ref_title, cat in [("gv_gvr.html", "www.aagrapevine.org", "GVR Resources", "gvr"),
                                         ("lv_recursos.html", "www.aalavina.org", "Recursos para los representantes", "rlv")]:
        soup = BeautifulSoup((PROBE / fname).read_text(encoding="utf-8", errors="replace"), "lxml")
        for a in soup.select('a[href$=".pdf"]'):
            url = a["href"]
            if url.startswith("/"):
                url = f"https://{host}{url}"
            if url in seen:
                continue
            seen.add(url)
            fn = url.rsplit("/", 1)[-1]
            txt = clean_text(a.get_text())
            title = txt if txt and txt.lower() not in ("download", "descargar", "here", "aquí", "pdf") and len(txt) > 3 else pretty_filename(re.sub(r"%20", " ", fn))
            m = re.search(r"/files/(\d{4}-\d{2})/", url)
            lang = detect_lang(title, "es" if "lavina" in host else "en")
            out.append(item(id=f"pdf:{short_hash(url)}", source="crawl", kind="pdf", url=url, title=title, lang=lang,
                            date=(m.group(1) + "-01") if m else None, category=cat,
                            extra={"host": host, "file_url": url, "size_bytes": None, "pages": None, "thumb": None,
                                   "referrers": [{"url": f"https://{host}/{'gvr-resources' if cat == 'gvr' else 'recursos'}", "title": ref_title}],
                                   "upload_month": m.group(1) if m else None, "link_texts": [txt] if txt else []}))
    return out


def instagram():
    out = []
    rows = list(csv.DictReader(open(PROBE / "instagram.csv", encoding="utf-8")))[:18]
    for n, r in enumerate(rows):
        sc = r["id"]
        acct = "lv" if n % 3 == 0 else "gv"
        out.append(item(id=f"ig:{sc}", source="instagram", kind="post", url=f"https://www.instagram.com/p/{sc}/",
                        title=("La Viña" if acct == "lv" else "AA Grapevine") + " — Instagram", summary="",
                        lang="es" if acct == "lv" else "en", date=(NOW - timedelta(days=n * 4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        category=acct, extra={"shortcode": sc, "account": acct, "username": "alcoholicosanonimos_lv" if acct == "lv" else "alcoholicsanonymous_gv",
                                              "media_type": "image", "thumb": None, "embed_url": f"https://www.instagram.com/p/{sc}/embed/captioned/"}))
    return out


def drive():
    # Uses the (previous-panel) public A65_GV folders only to have realistic dev photos/docs.
    out = []
    photos = [("1-u4mNb4oaIw7_mAmUi8SK26pVtab3K9H", "GV July.jpg", "Published authors 2025 - GV"),
              ("1-5I-XTwrnd3p3neihwxXct7V7LNCeRD6", "GV October .jpg", "Published authors 2025 - GV"),
              ("1KWtnzLwq9OfB1mF9UMFghgrZBfubJG63", "January GV.jpg", "Published authors 2025 - GV"),
              ("1pg9en5Cs79lmIFFoRwKC9RE7TTi_VvEi", "July - A Vision for Me.jpg", "Published authors 2025 - GV"),
              ("18TwRMAXQs0zdkBk48l513eZnvKe-7hmX", "Promotional material.jpeg", "Email"),
              ("1UhVkJxInz-HD8eE7LwtWxWs4jfkn28Ej", "Promotional material 2.jpeg", "Email"),
              ("1ck5UDziQGRvEo5CYSpxgQyC8vYILy-E_", "Apps.jpeg", "Email"),
              ("185kvpNHr3gvE0IsJw94L_FdM0ezxizBw", "Audiobook.jpg", "Email"),
              ("1ypYEuCxXh7PhR5nIQPqlPoq6P3tA9QM8", "Podcast.jpeg", "Email"),
              ("1ihUWpTpoSQhLWJUNqepUq7oA2zDgXcnX", "Youtube.jpeg", "Email")]
    for fid, name, album in photos:
        out.append(item(id=f"drive:{fid}", source="drive", kind="photo", url=f"https://drive.google.com/file/d/{fid}/view",
                        title=pretty_filename(name), lang="en", date="2026-02-20", category="photos",
                        image=f"https://lh3.googleusercontent.com/d/{fid}=w600",
                        extra={"file_id": fid, "mime": "image/jpeg", "panel": 77, "panel_label": "Panel 77 (2027–2028)", "path": ["photos", album],
                               "album": album, "view_url": f"https://drive.google.com/file/d/{fid}/view",
                               "preview_url": f"https://drive.google.com/file/d/{fid}/preview",
                               "download_url": f"https://drive.google.com/uc?export=download&id={fid}",
                               "thumb_url": f"https://lh3.googleusercontent.com/d/{fid}=w600", "image_url": f"https://lh3.googleusercontent.com/d/{fid}=w1600",
                               "is_image": True, "is_video": False, "is_pdf": False}))
    docs = [("1rmbylJ2k2CSlQzOSCnir8KkLenDuOnyF", "2025.02.16.Winter ACM.pdf", "reports", "application/pdf"),
            ("1Sex1VhDkRaMkccXYnAI2GcJzIMzObed0", "2025.04.06.Spring ACM.pdf", "reports", "application/pdf"),
            ("1MuROKsmtpelPAuvmSrxPscMA2Drjfstw", "March 2026 NETA65 GV_LV Committee Meeting 2026.pptx", "slides", "application/vnd.google-apps.shortcut"),
            ("1sowhe9wTzBuOXlD-QrXfzAfYRt2aO2fB", "Taller de Escritura Grupo 31 de Octubre - GV-LV Committee.pptx", "workshops", "application/vnd.openxmlformats-officedocument.presentationml.presentation")]
    for fid, name, cat, mime in docs:
        d, rest = date_from_text(name)
        title = pretty_filename(rest)
        out.append(item(id=f"drive:{fid}", source="drive", kind="slides" if cat in ("slides", "workshops") else "document",
                        url=f"https://drive.google.com/file/d/{fid}/view", title=title, lang=detect_lang(title, "en"), date=d, category=cat,
                        image=f"https://lh3.googleusercontent.com/d/{fid}=w600",
                        extra={"file_id": fid, "mime": mime, "panel": 77, "panel_label": "Panel 77 (2027–2028)", "path": [cat], "album": None,
                               "view_url": f"https://drive.google.com/file/d/{fid}/view", "preview_url": f"https://drive.google.com/file/d/{fid}/preview",
                               "download_url": f"https://drive.google.com/uc?export=download&id={fid}", "thumb_url": f"https://lh3.googleusercontent.com/d/{fid}=w600",
                               "image_url": None, "is_image": False, "is_video": False, "is_pdf": name.endswith(".pdf")}))
    return out


def events(drive_items):
    out = []
    # committee meetings are computed in templates too; include next 3 here like build_data will
    from scripts.sync.meeting import upcoming_meetings
    for m in upcoming_meetings(6):
        out.append(item(id=f"ev:committee:{m['ymd']}", source="committee", kind="event", url="/meeting/", title="Grapevine / La Viña Committee Meeting (Zoom)",
                        summary="Monthly NETA 65 GV/LV committee meeting. All AA members are welcome.", lang="en", date=m["start"], category="committee",
                        extra={"start": m["start"], "end": m["end"], "all_day": False, "location": "Zoom", "online_url": "https://zoom.us/j/9494767497?pwd=neta65",
                               "flyer_url": None, "flyer_thumb": None}))
    out.append(item(id="ev:flyer:sample1", source="drive", kind="event", url="https://drive.google.com/file/d/1rmbylJ2k2CSlQzOSCnir8KkLenDuOnyF/view",
                    title="GV/LV Writing Workshop — Ross Avenue Group", summary="Bring a story idea! Hands-on writing workshop for Grapevine and La Viña.",
                    lang="en", date=(NOW + timedelta(days=24)).strftime("%Y-%m-%d"), category="flyer",
                    extra={"start": (NOW + timedelta(days=24)).strftime("%Y-%m-%d"), "end": None, "all_day": True, "location": "Dallas, TX", "online_url": None,
                           "flyer_url": "https://drive.google.com/file/d/1rmbylJ2k2CSlQzOSCnir8KkLenDuOnyF/view",
                           "flyer_thumb": "https://lh3.googleusercontent.com/d/1rmbylJ2k2CSlQzOSCnir8KkLenDuOnyF=w600"}))
    out.append(item(id="ev:lvcal:sample", source="calendar", kind="event", url="https://www.aalavina.org/get-involved/events/2026-10-30/iii-convencion-hispana-del-estado-de-alabama",
                    title="Taller de escritura de La Viña — Distritos Hispanos", summary="Taller para compartir tu historia en español.", lang="es",
                    date=(NOW + timedelta(days=40)).strftime("%Y-%m-%d"), category="lv-calendar",
                    extra={"start": (NOW + timedelta(days=40)).strftime("%Y-%m-%d"), "end": None, "all_day": True, "location": "Dallas, TX",
                           "online_url": None, "flyer_url": None, "flyer_thumb": None, "city": "Dallas", "state": "TX"}))
    out.append(item(id="ev:past:sample", source="drive", kind="event", url="", title="Fall Assembly — GV/LV Booth", summary="", lang="en",
                    date=(NOW - timedelta(days=12)).strftime("%Y-%m-%d"), category="flyer",
                    extra={"start": (NOW - timedelta(days=12)).strftime("%Y-%m-%d"), "end": None, "all_day": True, "location": "Tyler, TX", "online_url": None,
                           "flyer_url": None, "flyer_thumb": None}))
    return sorted(out, key=lambda i: i["extra"]["start"])


def announcements():
    body = ("Welcome to the new, self-updating Grapevine / La Viña website! New articles, PDFs, podcasts, videos and Instagram posts now appear "
            "automatically every day — in English and Spanish. Share it with your group and district.")
    it = item(id="ann:fixture-welcome", source="committee", kind="announcement", url="/announcements/", title="Our new website updates itself every day",
              summary=body, lang="en", date=NOW.strftime("%Y-%m-%d"), category="manual",
              extra={"body_md": body + "\n\nQuestions? Write to [grapevine@neta65.org](mailto:grapevine@neta65.org).", "expires": None, "pinned": True})
    add_i18n([it], ("title", "summary"))
    it["i18n"]["body_md"] = {"en": it["extra"]["body_md"], "es": translate([body], "en", "es")[0] + "\n\n¿Preguntas? Escribe a [grapevine@neta65.org](mailto:grapevine@neta65.org)."}
    return [it]


def editorial():
    rows = [("gv", "January 2027", "Sponsorship", "2026-09-30"), ("gv", "February 2027", "Emotional Sobriety", "2026-10-31"),
            ("gv", "March 2027", "Carrying the Message", "2026-11-30"), ("lv", "Enero-Febrero 2027", "El padrinazgo", "2026-10-15"),
            ("lv", "Marzo-Abril 2027", "La unidad", "2026-12-15")]
    return [item(id=f"ed:{p}:{short_hash(t)}", source="grapevine" if p == "gv" else "lavina", kind="topic",
                 url="https://www.aagrapevine.org/contribute" if p == "gv" else "https://www.aalavina.org/temas-sugeridos",
                 title=t, lang="en" if p == "gv" else "es", date=dl, category=p,
                 extra={"publication": p, "issue_label": iss, "deadline": dl, "theme": t}) for p, iss, t, dl in rows]


def main():
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    v, e, a, p, ig, dr = videos(), episodes(), articles(), pdfs(), instagram(), drive()
    for coll in (v, e, a, p, ig, dr):
        add_i18n(coll)
    ev = add_i18n(events(dr))
    ann = announcements()
    ed = add_i18n(editorial(), ("title",))
    write_json(SITE_DIR / "videos.json", envelope(v))
    write_json(SITE_DIR / "episodes.json", envelope(e))
    write_json(SITE_DIR / "articles.json", envelope(a))
    write_json(SITE_DIR / "pdfs.json", envelope(p))
    write_json(SITE_DIR / "instagram.json", envelope(ig))
    write_json(SITE_DIR / "drive.json", envelope(dr))
    write_json(SITE_DIR / "events.json", envelope(ev))
    write_json(SITE_DIR / "announcements.json", envelope(ann))
    write_json(SITE_DIR / "editorial.json", envelope(ed))
    wo = add_i18n([item(id="weekly_open", source="grapevine", kind="meeting", url="https://www.aagrapevine.org/grapevine-weekly-open",
                        title="Grapevine Weekly Open AA Meeting", summary="Join the Grapevine Weekly Open AA meeting on Zoom.", lang="en",
                        extra={"zoom_id": "871 2036 8287", "passcode": "238047", "day": "Wednesdays", "time": "11 AM Central",
                               "url": "https://www.aagrapevine.org/grapevine-weekly-open"})])
    write_json(SITE_DIR / "weekly_open.json", envelope(wo))
    allitems = [i for c in (v, e, a, p, ig, dr, ann) for i in c if i.get("date")]
    allitems.sort(key=lambda i: str(i["date"]), reverse=True)
    write_json(SITE_DIR / "whatsnew.json", envelope(allitems[:150]))
    write_json(SITE_DIR / "districts.json", envelope([{"number": 89, "language": "es", "name": "", "website": "", "gvr_contact": "", "meets": ""},
                                                       {"number": 90, "language": "es", "name": "", "website": "", "gvr_contact": "", "meets": ""}]))
    srcs = [("drive", "Google Drive", len(dr)), ("articles", "Grapevine & La Viña articles", len(a)), ("pdfs", "PDF library (site crawl)", len(p)),
            ("youtube", "YouTube", len(v)), ("podcasts", "Podcasts", len(e)), ("instagram", "Instagram", len(ig)),
            ("editorial", "Editorial themes", len(ed)), ("events_external", "GV/LV calendars (Texas)", 1), ("announcements", "Announcements", 1)]
    write_json(SITE_DIR / "status.json", {"generated": now_iso(), "fixture": True,
                                          "sources": [{"source": s, "label": l, "ok": True, "updated": now_iso(), "count": c, "new_7d": 2, "error": None, "stats": {}} for s, l, c in srcs],
                                          "crawl": {"known_pages": 3100, "crawled_pages": 480, "pdfs": len(p), "last_run_pages": 470},
                                          "translations": {"cached": 1234}, "items": []})
    print("fixtures written:", {k: len(x) for k, x in dict(videos=v, episodes=e, articles=a, pdfs=p, instagram=ig, drive=dr, events=ev).items()})


if __name__ == "__main__":
    main()
