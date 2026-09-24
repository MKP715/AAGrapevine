"""Library curation: each official document ONCE in data/site/pdfs.json (called by build_data.py).

The crawler records every document linked from aagrapevine.org / aalavina.org. Before the site files
are written these rules run, so every consumer (Library, search, What's New, digest, home stats,
the GVR and Shop pages) sees the same list:

  a) official sources only (official_only, BEFORE translation): a document whose FILE is not on an
     official AA host — config/site.yml `library.official_hosts`, default crawl_rules.OFFICIAL_DOC_HOSTS
     (aagrapevine.org, aalavina.org, aa.org, aaws.widen.net; subdomains included) — is left out;
  b) the same file twice: the same address (the two magazine sites share ONE files directory;
     case / %-encoding ignored), or the same size in bytes + page count and either the same title,
     the same file name apart from Drupal's "_0" rename suffix, or an identical first-page thumbnail.
     Copies in the same document language → one is kept (a live one; the magazine whose language is
     the document's; the newest upload; a rep-kit copy; the most "found on" pages) and takes over the
     others' "found on" pages and kits. Copies that the two sites file under DIFFERENT languages
     (a bilingual file such as the joint catalog) become language versions of one entry (rule d);
  c) superseded versions: the same original title, publication, document language, category and
     type (e.g. a 2019 "YouTube Channel" postcard and its 2026 replacement) → only the newest stays;
  d) language versions: an English and a Spanish (French…) edition of one document — the same
     English title once "(English)" / "(Spa.)" markers are removed, a compatible type, a different
     document language, and dates within PAIR_DAYS, file names that differ only by the language
     word, or the GVR-kit / RLV-kit counterparts of each other (same type and page count) — become
     ONE entry: the English edition (else the Spanish one) is the item, `extra.versions` lists every
     edition, and the item's i18n title for each page language is that language's edition's title.

Every collapse is logged. Pure functions (no network); build_data.py passes a thumbnail reader.
Contract: docs/DATA_SCHEMA.md (pdfs).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit

from . import crawl_rules as R
from .common import ROOT, get_logger

log = get_logger("pdf_curate")

LANG_ORDER = ("en", "es", "fr")
PAIR_DAYS = 45                         # an English and a Spanish edition published this close = a pair
MAX_REFERRERS = 5                      # like the crawler's "found on" list
KITS = ("gvr", "rlv")
DRUPAL_DOMAINS = ("aagrapevine.org", "aalavina.org")   # one Drupal install, one files directory
# Document types (the crawler's tags on kit documents; else the category).
TYPE_TAGS = ("news", "catalog", "postcard", "flyer", "order-form", "guidelines", "workbook", "service",
             "literature")

_LANG_WORDS = (r"english|eng|ingl[eé]s|spanish|span|spa|sp|espa[nñ]ol|esp|french|fre|fr|"
               r"franc[eé]s|fran[cç]ais|fra")
# "(English)", "[Spa.]", "(español)" at the end of a title — or " - English"
_MARKER_RE = re.compile(rf"(?i)\s*(?:[(\[]\s*(?:{_LANG_WORDS})\.?\s*[)\]]|[-–—:]\s*(?:english|spanish|french|"
                        rf"ingl[eé]s|espa[nñ]ol|franc[eé]s|fran[cç]ais))\s*$")
# language words inside a FILE name ("New_Publisher-SPANISH-Anncmnt", "…_Release_FRE")
_FILE_LANG_RE = re.compile(rf"(?i)(?<![a-z])(?:{_LANG_WORDS})(?![a-z])")
_STOP = frozenset("the of and a an for to in on with y de del la el los las en et le les du des".split())


# =========================================================================== small helpers
def fold(s: Any) -> str:
    t = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def strip_lang_marker(title: str) -> str:
    """'Sold-out book is Back in stock! (English)' → 'Sold-out book is Back in stock!'."""
    t = str(title or "")
    for _ in range(2):
        s = _MARKER_RE.sub("", t).strip()
        if not s or s == t:
            break
        t = s
    return t or str(title or "")


def title_key(title: str) -> str:
    """Word set of a title for comparisons: accents, case, punctuation, word order, small words,
    ordinal endings and language markers ignored ('2026 Catalog (Postcard)' = '2026 Catalog Postcard',
    'Grapevine and La Viña Apps' = 'La Viña and Grapevine Apps')."""
    words = re.findall(r"[a-z0-9]+", fold(strip_lang_marker(title)))
    words = [re.sub(r"^(\d+)(?:st|nd|rd|th|o|a|er|e)$", r"\1", w) for w in words]
    return " ".join(sorted({w for w in words if w not in _STOP}))


def strict_title(title: str) -> str:
    """Accent/case/punctuation-insensitive, word order kept ('Youtube Channel' = 'YouTube Channel')."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(title)))


def extra(it: dict) -> dict:
    ex = it.get("extra")
    return ex if isinstance(ex, dict) else {}


def file_url(it: dict) -> str:
    return str(extra(it).get("file_url") or it.get("url") or "")


def file_host(it: dict) -> str:
    try:
        return (urlsplit(file_url(it)).hostname or "").lower() or str(extra(it).get("host") or "").lower()
    except ValueError:
        return str(extra(it).get("host") or "").lower()


def _domain(host: str) -> str:
    h = (host or "").lower()
    for d in DRUPAL_DOMAINS:
        if h == d or h.endswith("." + d):
            return d
    return h[4:] if h.startswith("www.") else h


def publication(it: dict) -> str:
    """'lv' for a file on aalavina.org (or an aa.org file shared by La Viña), else 'gv'."""
    d = _domain(file_host(it))
    if d not in DRUPAL_DOMAINS:
        for r in extra(it).get("referrers") or []:
            try:
                rd = _domain(urlsplit(str((r or {}).get("url") or "")).hostname or "")
            except ValueError:
                continue
            if rd in DRUPAL_DOMAINS:
                d = rd
                break
    return "lv" if d == "aalavina.org" else "gv"


def doc_lang(it: dict) -> str:
    v = str(extra(it).get("doc_lang") or it.get("lang") or "")
    return v if re.fullmatch(r"[a-z]{2}", v) else "und"


def en_title(it: dict) -> str:
    """The English title: the original when it is English, else its (machine) translation."""
    if it.get("lang") == "en":
        return str(it.get("title") or "")
    tr = ((it.get("i18n") or {}).get("title") or {}).get("en")
    return str(tr or it.get("title") or "")


def doc_type(it: dict) -> str:
    """Kit documents: their type tag ('postcard', or 'rep' when none); others: the category."""
    cat = str(it.get("category") or "other").lower()
    if cat not in KITS:
        return cat
    return next((t for t in (str(x).lower() for x in it.get("tags") or []) if t in TYPE_TAGS), "rep")


def types_compatible(a: dict, b: dict) -> bool:
    ta, tb = doc_type(a), doc_type(b)
    return ta == tb or "rep" in (ta, tb)


def kits_of(it: dict) -> set[str]:
    ks = {str(k) for k in extra(it).get("kits") or [] if k in KITS}
    if it.get("category") in KITS:
        ks.add(it["category"])
    return ks


def size_of(it: dict) -> int:
    try:
        return int(extra(it).get("size_bytes") or 0)
    except (TypeError, ValueError):
        return 0


def pages_of(it: dict) -> int:
    try:
        return int(extra(it).get("pages") or 0)
    except (TypeError, ValueError):
        return 0


def month_of(it: dict) -> str:
    m = str(extra(it).get("upload_month") or "")
    if re.fullmatch(r"\d{4}-\d{2}", m):
        return m
    d = str(it.get("date") or "")
    return d[:7] if re.match(r"\d{4}-\d{2}", d) else ""


def day_of(it: dict) -> date | None:
    d = str(it.get("date") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        m = month_of(it)
        d = f"{m}-01" if m else ""
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def file_key(it: dict) -> str:
    """The file's identity: the magazine sites share one files directory, so the host only counts
    for other sites; the path is compared decoded, NFC and lower case."""
    try:
        p = urlsplit(file_url(it))
    except ValueError:
        return ""
    path = unicodedata.normalize("NFC", unquote(re.sub(r"/{2,}", "/", p.path or ""))).lower()
    dom = _domain(p.hostname or "")
    return ("drupal" if dom in DRUPAL_DOMAINS else dom) + path if path else ""


def file_stem(it: dict, drop_lang: bool = False) -> str:
    """'…/2021-01/SF-202-Espiritualidad_ESPANOL_0.PDF' → 'sf 202 espiritualidad espanol' (Drupal's
    '_0' rename suffix and the extension dropped; drop_lang also drops language words)."""
    name = unquote(file_url(it).split("?", 1)[0].rsplit("/", 1)[-1])
    name = re.sub(r"(?i)(\.pdf)+$", "", unicodedata.normalize("NFC", name))
    name = re.sub(r"_\d{1,2}$", "", name)
    if drop_lang:
        name = _FILE_LANG_RE.sub(" ", re.sub(r"[_\-.]+", " ", name))
    return " ".join(re.findall(r"[a-z0-9]+", fold(name)))


def lang_rank(lang: str) -> int:
    return LANG_ORDER.index(lang) if lang in LANG_ORDER else len(LANG_ORDER)


def _thumb_digest_default(thumb: str) -> str | None:
    """md5 of a cached first-page thumbnail ('/assets/cache/pdf/x.webp' under src/), or None."""
    t = str(thumb or "")
    if not re.fullmatch(r"/assets/[\w\-./]+", t) or ".." in t:
        return None
    p = ROOT / "src" / t.lstrip("/")
    try:
        return hashlib.md5(p.read_bytes()).hexdigest()
    except OSError:
        return None


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.p)):
            out.setdefault(self.find(i), []).append(i)
        return [g for g in out.values()]


def _label(it: dict) -> str:
    return f"{it.get('id')} “{it.get('title')}” [{doc_lang(it)}] {file_url(it).rsplit('/', 1)[-1]}"


# =========================================================================== a) official sources
def official_hosts(cfg: dict | None = None) -> tuple[str, ...]:
    """config/site.yml `library.official_hosts`, else crawl_rules.OFFICIAL_DOC_HOSTS."""
    return R.official_doc_hosts(cfg)


def official_only(items: Iterable[dict], hosts: Iterable[str] | None = None) -> list[dict]:
    """Drop documents whose file is not on an official AA host (logs how many, and which hosts)."""
    hosts = tuple(hosts) if hosts is not None else R.official_doc_hosts()
    kept, dropped = [], {}
    for it in items:
        h = file_host(it)
        if R.is_official_doc_host(h, hosts):
            kept.append(it)
        else:
            dropped[h or "?"] = dropped.get(h or "?", 0) + 1
    if dropped:
        log.info("library: %d document(s) from non-official hosts left out: %s", sum(dropped.values()),
                 ", ".join(f"{h} ({n})" for h, n in sorted(dropped.items())))
    return kept


# =========================================================================== b) same file
def _same_file(a: dict, b: dict, digest: Callable[[str], str | None]) -> str | None:
    """Why a and b are one file (a reason string), or None."""
    ka, kb = file_key(a), file_key(b)
    if ka and ka == kb:
        return "same address"
    sa, sb = size_of(a), size_of(b)
    if not sa or sa != sb or pages_of(a) != pages_of(b):
        return None
    ta, tb = title_key(en_title(a)), title_key(en_title(b))
    if ta and ta == tb:
        return "same size, pages and title"
    if file_stem(a) and file_stem(a) == file_stem(b):
        return "same size and file name"
    da, db = digest(extra(a).get("thumb") or ""), digest(extra(b).get("thumb") or "")
    if da and da == db:
        return "same size, pages and first page"
    return None


def _filename_title(t: str) -> bool:
    """'Politica-Editorial-La_Vina' is a file name used as a title; 'Política Editorial' is a title."""
    t = str(t or "").strip()
    return not t or "_" in t or (" " not in t and "-" in t) or len(re.findall(r"[^\W\d_]-[^\W\d_]", t)) >= 2


def _keeper_key(it: dict) -> tuple:
    """Which copy of one file stays: a live one; the magazine whose language is the document's (its
    title is then the original); a real title over a file name; the newest upload; a rep-kit copy;
    the one found on the most pages."""
    home = (publication(it) == "gv" and doc_lang(it) == "en") or (publication(it) == "lv" and doc_lang(it) == "es")
    m = month_of(it)
    return (extra(it).get("orphan") is True, not home, _filename_title(it.get("title")),
            -int(m.replace("-", "")) if m else 0, not kits_of(it), -len(extra(it).get("referrers") or []),
            str(it.get("id")))


def _absorb(keep: dict, other: dict) -> None:
    """keep takes over other's 'found on' pages, kits and address (as a duplicate)."""
    ex = keep.setdefault("extra", {})
    refs = list(ex.get("referrers") or [])
    have = {str((r or {}).get("url")) for r in refs}
    for r in extra(other).get("referrers") or []:
        if r and r.get("url") and str(r["url"]) not in have and len(refs) < MAX_REFERRERS:
            refs.append(r)
            have.add(str(r["url"]))
    ex["referrers"] = refs
    kits = kits_of(keep) | kits_of(other)
    if kits - ({keep.get("category")} if keep.get("category") in KITS else set()):
        ex["kits"] = sorted(kits)
    dup = [u for u in ex.get("duplicates") or []]
    for u in [file_url(other), *(extra(other).get("duplicates") or [])]:
        if u and u != file_url(keep) and u not in dup:
            dup.append(u)
    ex["duplicates"] = sorted(dup)
    if keep.get("is_new") is False and other.get("is_new"):
        keep["is_new"] = True


# =========================================================================== c) superseded
def _supersede_key(it: dict) -> tuple | None:
    t = strict_title(it.get("title") or "")
    if not t:
        return None
    return (publication(it), doc_lang(it), t, str(it.get("category") or ""))


# =========================================================================== d) language versions
def _near(a: dict, b: dict, days: int = PAIR_DAYS) -> bool:
    da, db = day_of(a), day_of(b)
    return bool(da and db and abs((da - db).days) <= days)


def _kit_counterparts(a: dict, b: dict) -> bool:
    """The GVR kit's edition and the RLV kit's edition of one document (same type and page count)."""
    ka, kb = a.get("category"), b.get("category")
    return (ka in KITS and kb in KITS and ka != kb and doc_type(a) == doc_type(b)
            and pages_of(a) > 0 and pages_of(a) == pages_of(b))


def _pairable(a: dict, b: dict, days: int = PAIR_DAYS) -> str | None:
    if doc_lang(a) == doc_lang(b) or "und" in (doc_lang(a), doc_lang(b)) or not types_compatible(a, b):
        return None
    ta, tb = title_key(en_title(a)), title_key(en_title(b))
    if not ta or ta != tb:
        return None
    if _near(a, b, days):
        return "same title, published together"
    sa, sb = file_stem(a, drop_lang=True), file_stem(b, drop_lang=True)
    if sa and sa == sb:
        return "same title and file name"
    if _kit_counterparts(a, b):
        return "same title, GVR and RLV kit editions"
    return None


def _shown_title(v: dict, lang: str) -> tuple[str, bool]:
    """(title of edition v on a page in `lang`, machine-translated?)"""
    if v.get("title_lang") == lang:
        return v["title"], False
    tr = (v.get("i18n_title") or {}).get(lang)
    if tr:
        return tr, lang in (v.get("machine") or [])
    return v["title"], False


def version_record(it: dict) -> dict:
    ex = extra(it)
    i18n_t = dict(((it.get("i18n") or {}).get("title") or {}))
    rec = {
        "lang": doc_lang(it), "id": it.get("id"), "url": it.get("url"), "title": it.get("title") or "",
        "title_lang": it.get("lang"), "i18n_title": i18n_t,
        "machine": sorted(set(it.get("machine") or [])), "source": publication(it),
        "date": it.get("date"), "first_seen": it.get("first_seen"), "category": it.get("category"),
        "tags": list(it.get("tags") or []), "is_new": bool(it.get("is_new")),
    }
    for k in ("host", "file_url", "filename", "size_bytes", "pages", "thumb", "upload_month", "referrers",
              "event_date", "orphan", "duplicates"):
        if ex.get(k) not in (None, "", []):
            rec[k] = ex[k]
    return rec


def _merge_versions(members: list[dict], same_file: bool) -> dict:
    """One entry for the language editions `members` (distinct document languages)."""
    members = sorted(members, key=lambda it: (lang_rank(doc_lang(it)), str(it.get("id"))))
    primary = members[0]
    versions = [version_record(it) for it in members]
    for v in versions:                     # the language links say it: no "(English)" in the titles
        v["title"] = strip_lang_marker(v["title"])
        v["i18n_title"] = {k: strip_lang_marker(t) for k, t in v["i18n_title"].items()}
    by_lang = {v["lang"]: v for v in versions}
    pv = versions[0]
    ex = primary.setdefault("extra", {})
    ex["versions"] = versions
    kits = set().union(*(kits_of(it) for it in members))
    if kits - ({primary.get("category")} if primary.get("category") in KITS else set()):
        ex["kits"] = sorted(kits)
    if same_file:
        ex["same_file"] = True
    primary["title"] = pv["title"]
    i18n = primary.setdefault("i18n", {})
    titles, machine = {}, set(m for m in (primary.get("machine") or []) if m not in ("en", "es"))
    for lang in ("en", "es"):
        t, m = _shown_title(by_lang.get(lang) or pv, lang)
        titles[lang] = t
        if m:
            machine.add(lang)
    i18n["title"] = titles
    primary["machine"] = sorted(machine)
    primary["is_new"] = any(v["is_new"] for v in versions)
    return primary


# =========================================================================== main entry
def curate(items: list[dict], *, thumb_digest: Callable[[str], str | None] | None = None,
           pair_days: int = PAIR_DAYS) -> tuple[list[dict], dict[int, dict]]:
    """Rules b–d on the translated pdf items (newest first). → (items, {id(dropped item): kept item})
    so What's New can point a dropped item's entry at the entry that absorbed it."""
    digest = thumb_digest or _thumb_digest_default
    swaps: dict[int, dict] = {}
    items = [it for it in items if isinstance(it, dict)]
    order = {id(it): i for i, it in enumerate(items)}
    n = len(items)

    # ---- b) the same file under several addresses
    uf = _UF(n)
    reasons: dict[tuple[int, int], str] = {}
    buckets: dict[Any, list[int]] = {}
    for i, it in enumerate(items):
        if file_key(it):
            buckets.setdefault(("f", file_key(it)), []).append(i)
        if size_of(it):
            buckets.setdefault(("s", size_of(it)), []).append(i)
    for idx in buckets.values():
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = idx[x], idx[y]
                why = _same_file(items[i], items[j], digest)
                if why:
                    uf.union(i, j)
                    reasons.setdefault((i, j), why)
    siblings: list[list[dict]] = []           # same file, filed under different languages
    dropped: set[int] = set()
    for g in uf.groups():
        if len(g) < 2:
            continue
        by_lang: dict[str, list[int]] = {}
        for i in g:
            by_lang.setdefault(doc_lang(items[i]), []).append(i)
        keepers = []
        for idx in by_lang.values():
            idx.sort(key=lambda i: _keeper_key(items[i]))
            keep = items[idx[0]]
            for i in idx[1:]:
                _absorb(keep, items[i])
                dropped.add(i)
                swaps[id(items[i])] = keep
                why = next((r for (a, b), r in reasons.items() if i in (a, b)), "same file")
                log.info("library: same file (%s) — kept %s, left out %s", why, _label(keep), _label(items[i]))
            keepers.append(keep)
        if len(keepers) > 1:
            siblings.append(keepers)
    items = [it for i, it in enumerate(items) if i not in dropped]

    # ---- c) superseded versions (same title, publication, language, category and type)
    sib_ids = {id(it) for grp in siblings for it in grp}
    groups: dict[tuple, list[dict]] = {}
    for it in items:
        k = _supersede_key(it)
        if k and id(it) not in sib_ids:
            groups.setdefault(k, []).append(it)
    gone: set[int] = set()
    for k, grp in groups.items():
        if len(grp) < 2:
            continue
        grp.sort(key=lambda it: (extra(it).get("orphan") is True, -(day_of(it) or date.min).toordinal(),
                                 order[id(it)]))
        keep = grp[0]
        for old in grp[1:]:
            if not types_compatible(keep, old):
                continue
            gone.add(id(old))
            swaps[id(old)] = keep
            log.info("library: superseded — kept %s (%s), left out %s (%s)", _label(keep), keep.get("date"),
                     _label(old), old.get("date"))
    items = [it for it in items if id(it) not in gone]

    # ---- d) language editions of one document
    pos = {id(it): i for i, it in enumerate(items)}
    uf = _UF(len(items))
    langs: dict[int, set[str]] = {i: {doc_lang(it)} for i, it in enumerate(items)}

    def join(i: int, j: int) -> str:
        ri, rj = uf.find(i), uf.find(j)
        if ri == rj:
            return "already"
        if langs[ri] & langs[rj]:
            return "conflict"             # one edition per language
        uf.union(ri, rj)
        r = uf.find(ri)
        langs[r] = langs[ri] | langs[rj]
        return "joined"

    for grp in siblings:
        live = [pos[id(it)] for it in grp if id(it) in pos]
        for x in live[1:]:
            join(live[0], x)
    cands = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            why = _pairable(items[i], items[j], pair_days)
            if why:
                da, db = day_of(items[i]), day_of(items[j])
                gap = abs((da - db).days) if da and db else 10 ** 6
                cands.append((gap, i, j, why))
    for gap, i, j, why in sorted(cands):
        res = join(i, j)
        if res == "joined":
            log.info("library: language editions (%s) — %s + %s", why, _label(items[i]), _label(items[j]))
        elif res == "conflict":
            log.info("library: not paired (a %s edition is already paired) — %s + %s", doc_lang(items[j]),
                     _label(items[i]), _label(items[j]))
    out: list[dict] = []
    merged: dict[int, dict] = {}
    for g in uf.groups():
        if len(g) < 2:
            continue
        root = uf.find(g[0])
        members = [items[i] for i in g]
        # one bilingual file that the two sites file under their own languages (no separate editions)
        same = any(all(any(m is s for s in grp) for m in members) for grp in siblings)
        keep = _merge_versions(members, same)
        for m in members:
            if m is not keep:
                swaps[id(m)] = keep
        merged[root] = keep
    for i, it in enumerate(items):
        r = uf.find(i)
        if r in merged:
            if merged[r] is it:
                out.append(it)
        else:
            out.append(it)
    out.sort(key=lambda it: order[id(it)])
    # a chain (a → b, b → c) resolves to the final entry
    for k, v in list(swaps.items()):
        seen = set()
        while id(v) in swaps and id(v) not in seen:
            seen.add(id(v))
            v = swaps[id(v)]
        swaps[k] = v
    log.info("library: %d document(s) in, %d entries out (%d same file, %d superseded, %d merged as language editions)",
             n, len(out), len(dropped), len(gone), n - len(dropped) - len(gone) - len(out))
    return out, swaps


def remap_plan(plan: list[tuple[float, dict]], swaps: dict[int, dict]) -> list[tuple[float, dict]]:
    """What's New plan after curate(): an entry of a left-out document points at the entry that took
    it over; each entry once (its newest news date)."""
    best: dict[int, tuple[float, dict]] = {}
    order: list[int] = []
    for wn, it in plan:
        it = swaps.get(id(it), it)
        k = id(it)
        if k not in best:
            order.append(k)
            best[k] = (wn, it)
        elif wn > best[k][0]:
            best[k] = (wn, it)
    out = [best[k] for k in order]
    out.sort(key=lambda x: (-x[0], str(x[1].get("id"))))
    return out
