"""Rules for the PDF crawler (scripts/sync/crawl.py).

Everything a maintainer may want to tune lives in the TABLES at the top of this file:
  * HUB_PATHS            – pages re-checked every day (resource pages, home pages, news…)
  * KIT_PAGES            – pages whose PDFs form the GVR / RLV kit (category gvr / rlv)
  * SKIP_PATH_PATTERNS   – site areas the crawler never visits (login, cart, paywalled articles…)
  * GENERIC_LINK_TEXT    – link texts that are useless as titles ("Download", "Aquí"…)
  * LANG_*               – how a PDF's language is recognised from its file name / title
  * DOC_TYPE_RULES       – keyword table → document type (catalog, postcard, order-form…)

The functions below the tables are small, pure (no network) and unit-testable:
  normalize_page_url, canon_pdf_url, is_event_path, should_crawl_path,
  clean_title, choose_title, doc_language, title_language, classify.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit

from .common import clean_text, detect_lang, pretty_filename

# =====================================================================================
#  TABLES — edit these
# =====================================================================================

# The two public hosts of the one Drupal install that serves AA Grapevine and La Viña.
DRUPAL_HOSTS = ("www.aagrapevine.org", "www.aalavina.org")
HOST_ALIASES = {"aagrapevine.org": "www.aagrapevine.org", "aalavina.org": "www.aalavina.org"}
SPANISH_HOSTS = ("www.aalavina.org",)
# Both hosts share ONE files directory (/sites/default/files/…): the same PDF is reachable on
# either host. Identity (id / thumbnail name) is therefore computed on this host.
CANON_FILES_HOST = "www.aagrapevine.org"

# Pages fetched EVERY day (cheap: ~40 pages ≈ 3.5 min at the 5 s crawl-delay).
HUB_PATHS: dict[str, list[str]] = {
    "www.aagrapevine.org": [
        "/", "/home", "/gvr-resources", "/catalog", "/magazine", "/archive", "/news-release",
        "/important-updates", "/anncmnt", "/announcement", "/carry-the-message", "/get-involved",
        "/get-involved/calendar", "/get-involved/become-grapevine-rep", "/about-us", "/store",
        "/contribute", "/podcasts", "/apps", "/BOTM", "/grapevine-weekly-open", "/share",
    ],
    "www.aalavina.org": [
        "/", "/principal", "/home", "/recursos", "/catalogo", "/la-revista", "/archivo",
        "/actualizaciones-importantes", "/anuncio", "/comunicado", "/lleva-el-mensaje", "/servicio",
        "/servicio/conviertete-en-rlv", "/tienda", "/calendario-de-eventos", "/libro-del-mes",
        "/aplicaciones", "/temas-sugeridos", "/comparte",
    ],
}

# Referrer page → category for the representative kits (these win over every other rule).
KIT_PAGES = {
    "https://www.aagrapevine.org/gvr-resources": "gvr",
    "https://www.aalavina.org/recursos": "rlv",
}

# Event detail pages: /get-involved/events/2026-10-03/some-event
EVENT_PATH_RE = re.compile(r"^/get-involved/events/(\d{4}-\d{2}-\d{2})(?:/|$)")

# Paths the crawler never fetches (matched against the URL path, case-insensitive).
SKIP_PATH_PATTERNS = [
    r"^/(user|users|usuario)(/|$)",                      # login / accounts
    r"(^|/)(login|logout|register|password|inicio-sesion)(/|$)",
    r"^/(cart|carrito|checkout|pago|pagar)(/|$)",        # shop cart / checkout
    r"^/(search|buscar|busqueda|site-search)(/|$)",      # search result pages
    r"^/(store|tienda)/(search|buscar)(/|$)",
    r"^/(admin|node/add|comment|filter|contextual|batch|system|quickedit|flag|entity-browser)(/|$)",
    r"^/node/\d+/(edit|delete|revisions)",
    r"(^|/)media/oembed", r"^/media/\d+/(edit|delete)",
    r"^/(cdn-cgi|core|modules|themes|profiles|libraries|sites|vendor)/",
    r"^/taxonomy/",
    r"^/(rss\.xml|feed|feeds|sitemap)", r"/(feed|rss)$",
    r"^/(print|printpdf|entityprint|print-pdf|printmail)/",
    r"^/magazine/\d{4}/",                                # paywalled Grapevine articles
    r"^/revista/[^/]+/[^/]+",                            # paywalled La Viña articles
    r"^/(magazine-issue|revista-edicion|issue)/",        # issue tables of contents (link to articles)
    r"^/podcasts?/.+",                                   # episodes come from the podcast RSS module
    r"^/index\.php",
]
_SKIP_RE = re.compile("|".join(f"(?:{p})" for p in SKIP_PATH_PATTERNS), re.I)
# File extensions that are never HTML pages.
_NON_HTML_EXT = re.compile(
    r"\.(jpe?g|png|gif|webp|svg|ico|bmp|tiff?|mp3|m4a|wav|ogg|mp4|m4v|mov|avi|webm|zip|rar|7z|gz|"
    r"docx?|xlsx?|pptx?|ppsx?|csv|txt|rtf|odt|ods|epub|mobi|css|js|json|xml|ics|vcf|woff2?|ttf|eot|exe|dmg|apk)$",
    re.I)

# Link texts that say nothing about the document (never used as a title).
GENERIC_LINK_TEXT = re.compile(r"""(?ix)^(
      download(\s+(here|now|pdf|the\s+pdf|file|the\s+file|flyer|form))?
    | descarg(a|ar|ue)(\s+(aqu[ií]|pdf|el\s+pdf|archivo|el\s+archivo|volante))?
    | (please\s+)?click(\s+here)? | (haga\s+)?clic(k)?(\s+aqu[ií])? | aqu[ií] | here | this | this\s+link
    | pdf | \(pdf\) | view | ver | open | abrir | print | imprimir | link | enlace | file | archivo
    | document | documento | flyer | volante | info | informaci[oó]n | details | detalles
    | (read|see|view|learn|find\s+out)(\s+more)? | (leer|lee|ver|conoce)(\s+m[aá]s)? | m[aá]s(\s+informaci[oó]n)?
    | more(\s+info(rmation)?)? | website | sitio\s+web | registration | registro
)[\s.!:…»›>-]*$""")
# Texts starting like this are "read more …"-style teasers.
GENERIC_PREFIX = re.compile(r"(?i)^(read\s+more|lee(r)?\s+m[aá]s|ver\s+m[aá]s|see\s+more|click\s+here|haga\s+clic|clic\s+aqu[ií])\b")
SIZE_TEXT = re.compile(r"(?i)^\s*[\(\[]?\s*\d+([.,]\d+)?\s*(kb|mb|gb|bytes?)\s*[\)\]]?\s*$")
# "Download App Poster" → "App Poster"; Spanish verbs only before an article ("Descarga el Poster…"),
# so noun phrases like "Descarga de audios" (= "Audio downloads") stay intact.
LEADING_VERB = re.compile(r"(?i)^(?:(?:download|view|read|learn|discover|see)\s+(?:the\s+|our\s+)?(?!of\b|de\b|more\b)"
                          r"|(?:descarga(?:r)?|descargue|ver|lee(?:r)?|conoce|descubre)\s+(?:el|la|los|las|nuestro|nuestra|tu|su)\s+)(?=\S)")
# "… here" / "… aquí" at the end of a link text ("Learn How It Works Here", "Archivo pdf aquí").
TRAILING_HERE = re.compile(r"(?i)[\s,:–-]+(?:click\s+)?(?:here|aqu[ií]|haga\s+clic\s+aqu[ií])[\s.!:…»›>]*$")
# If nothing but these words is left, the text says nothing about the document.
WEAK_WORDS = set("""a an the this our el la los las un una este esta nuestro nuestra con de del en in of for to para
file files archivo archivos pdf version versión versions document documento documents documentos copy copia
flyer flier volante download descarga descargar click clic haga learn more más conoce read lee leer see ver view
here aquí aqui info information información details detalles link enlace full completo completa printable
imprimible print imprimir form formulario""".split())
# Links that only name a language ("English", "Spanish version", "leer en Inglés"): the page title
# describes the document → "<page title> (Spanish)".
LANGUAGE_ONLY = re.compile(
    r"(?i)^(?:(?:read|view|download|leer|lee|ver|descarga(?:r)?)\s+(?:it\s+)?)?(?:in\s+|en\s+)?"
    r"(?:(?:the\s+)?(?:version|versi[oó]n)\s+(?:in\s+|en\s+)?)?"
    r"(english|spanish|french|ingl[eé]s|espa[nñ]ol|franc[eé]s|fran[cç]ais)"
    r"(?:\s+(?:version|versi[oó]n|translation|traducci[oó]n|pdf))?[\s.!:]*$")
LANGUAGE_NAMES = {
    "en": {"en": "English", "es": "inglés", "fr": "anglais"},
    "es": {"en": "Spanish", "es": "español", "fr": "espagnol"},
    "fr": {"en": "French", "es": "francés", "fr": "français"},
}
_LANG_WORD = {"english": "en", "ingles": "en", "spanish": "es", "espanol": "es",
              "french": "fr", "frances": "fr", "francais": "fr"}

# Language hints. Two-letter codes must be UPPERCASE in the file name (so the Spanish word "en"
# in "Mujeres-en-AA.pdf" is not mistaken for English); spelled-out names are case-insensitive.
LANG_FILENAME_HINTS = [
    ("es", re.compile(r"(?:^|[_\-\s.(])(?:SP|ES|ESP|SPA)(?:[_\-\s.)0-9]|$)")),
    ("es", re.compile(r"(?i)(?:^|[_\-\s.(])(?:spanish|espa[nñ]ol|espanol)(?:[_\-\s.)0-9]|$)")),
    ("es", re.compile(r"(?:^|[_\-\s])SF-\d")),                 # AAWS/Grapevine code SF-xxx = Spanish
    ("fr", re.compile(r"(?:^|[_\-\s.(])(?:FR|FRA)(?:[_\-\s.)0-9]|$)")),
    ("fr", re.compile(r"(?i)(?:^|[_\-\s.(])(?:french|fran[cç]ais|francais|franc[eé]s|la[_\-\s]?vigne)(?:[_\-\s.)0-9]|$)")),
    ("fr", re.compile(r"(?:^|[_\-\s])FF-\d")),                 # FF-xxx = French
    ("en", re.compile(r"(?:^|[_\-\s.(])(?:EN|ENG)(?:[_\-\s.)0-9]|$)")),
    ("en", re.compile(r"(?i)(?:^|[_\-\s.(])(?:english|ingl[eé]s)(?:[_\-\s.)0-9]|$)")),
]
# "(Spanish)", "(Spa.)", "(Francés)", "(Inglés)" … in link texts / titles describe the DOCUMENT.
LANG_TITLE_MARKERS = [
    ("es", re.compile(r"(?i)[(\[]\s*(spanish|spa\.?|espa[nñ]ol|espagnol|en\s+espa[nñ]ol)\s*[)\]]")),
    ("fr", re.compile(r"(?i)[(\[]\s*(french|fre\.?|fr\.|fran[cç]ais|franc[eé]s|en\s+franc[eé]s)\s*[)\]]")),
    ("en", re.compile(r"(?i)[(\[]\s*(english|eng\.?|ingl[eé]s|anglais|en\s+ingl[eé]s)\s*[)\]]")),
]

# Document type rules — FIRST MATCH WINS. `where`:
#   section  = heading the link sits under on the referring page ("Postcards", "Tarjetas postales")
#   referrer = URL of a page that links the PDF (news / announcement pages)
#   text     = title + link texts + image alts + file name (lower-case, accents removed)
DOC_TYPE_RULES: list[tuple[str, str, str]] = [
    ("postcard",   "section",  r"post ?cards?|tarjetas? postal"),
    ("order-form", "section",  r"subscription|suscripcion"),
    ("news",       "referrer", r"/(news-release|important-updates|actualizaciones-importantes|anncmnt|announcement|"
                               r"anuncio|comunicado)(/|$)"),
    ("postcard",   "text",     r"post ?card|tarjeta postal|(^|[_\- ])pc([_\- .]|\d|$)|marca ?libros?|bookmark"),
    ("catalog",    "text",     r"catalog"),
    ("news",       "text",     r"\bnews\b|noticias|what'?s new|novedades|newsletter|boletin|press release|\brelease\b|"
                               r"comunicado|announcement|anuncio|\bletter\b|\bcarta\b|price increase|aumento"),
    ("order-form", "text",     r"order[ -]?form|formulario|pedido|subscription|suscri|hoja de informacion|"
                               r"\bprices?\b|precios|back[ -]issue|by[ -]the[ -]month|ediciones (pasadas|anteriores)"),
    ("workbook",   "text",    r"workbook|libro de trabajo"),
    ("guidelines", "text",    r"guidelines?|pautas|\bguia\b|policy|politica|checklist|chequeo|editorial calendar|"
                              r"\btemas\b|instructions|instrucciones"),
    ("service",    "text",    r"handbook|manual|carry the message|lleva el mensaje|\bgvrs?\b|\brlvs?\b|"
                              r"representative|representante|self[ -]?support|automantenimiento|dependemos|"
                              r"workshop|taller|\btoday\b|\bhoy\b|12 ways|12 maneras|service|servicio"),
    ("flyer",      "text",    r"flyer|volante|poster|afiche|cartel|\bevent|evento|convention|convencion|"
                              r"conference|conferencia|roundup|round-up|assembly|asamblea|anniversary|aniversario"),
    ("literature", "text",    r"\bbooks?\b|libros?|pamphlet|folleto|\bplays?\b|audiobook|audiolibro|prayer|oracion"),
]
_DOC_TYPE_RULES = [(cat, where, re.compile(rx, re.I)) for cat, where, rx in DOC_TYPE_RULES]

# Lines on page 1 of a PDF that are never a good title (brand names printed on every flyer).
_BRAND_LINE = re.compile(r"(?i)^(aa\s+)?(the\s+)?(grapevine|la\s+vi[ñn]a|la\s+vigne|alcoholics\s+anonymous|"
                         r"alcoh[oó]licos\s+an[oó]nimos|aa\s+grapevine,?\s+inc\.?|www\.|https?:)")


# =====================================================================================
#  URL helpers
# =====================================================================================
def _norm_host(host: str) -> str:
    host = (host or "").lower().strip(".")
    host = re.sub(r":(80|443)$", "", host)
    return HOST_ALIASES.get(host, host)


def _norm_path(path: str) -> str:
    # Decode then re-encode so "%20" / " " / "%2520"-free variants all compare equal. No Unicode
    # normalisation: Drupal stores some names decomposed (Vin%CC%83a) and the server is exact.
    path = re.sub(r"/{2,}", "/", path or "/")
    try:
        decoded = unquote(path, errors="strict")
    except UnicodeDecodeError:        # e.g. Latin-1 "%E9": keep the server's exact bytes
        return quote(path, safe="/%") or "/"
    return quote(decoded, safe="/") or "/"


def is_drupal_host(host: str) -> bool:
    return _norm_host(host) in DRUPAL_HOSTS


def normalize_page_url(url: str, base: str | None = None) -> str | None:
    """Canonical https URL of an HTML page on one of the two Drupal hosts, or None if the
    link is off-site, has a query string, points to a file, or is otherwise not crawlable."""
    try:
        url = urljoin(base, url.strip()) if base else url.strip()
        p = urlsplit(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    host = _norm_host(p.hostname or "")
    if host not in DRUPAL_HOSTS:
        return None
    if p.query:               # ?page=2, ?f[0]=…, ?destination=… — duplicates / infinite spaces
        return None
    path = _norm_path(p.path)
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


def should_crawl_path(path: str) -> bool:
    """False for login/cart/search/paywalled-article/file paths (see SKIP_PATH_PATTERNS)."""
    if _SKIP_RE.search(path):
        return False
    if _NON_HTML_EXT.search(unquote(path)) or unquote(path).lower().endswith(".pdf"):
        return False
    return True


def event_date_of(url: str) -> str | None:
    m = EVENT_PATH_RE.match(urlsplit(url).path)
    return m.group(1) if m else None


def is_event_path(url: str) -> bool:
    return event_date_of(url) is not None


def hub_urls() -> list[str]:
    return [f"https://{h}{p}" for h, paths in HUB_PATHS.items() for p in paths]


_VIEWER_RE = re.compile(r"(?i)(viewer|pdfjs|pdf\.js|docs\.google\.com/(viewer|gview)|/view)")


def canon_pdf_url(url: str) -> str | None:
    """Normalise a PDF link: https + www host for the Drupal sites, clean %-encoding, no fragment,
    no cache-busting query on /sites/… files. Returns None for unusable URLs."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    host = _norm_host(p.hostname)
    scheme = "https" if host in DRUPAL_HOSTS else p.scheme
    path = _norm_path(p.path)
    query = p.query
    if host in DRUPAL_HOSTS and path.startswith("/sites/"):
        query = ""
    return urlunsplit((scheme, host, path, query, ""))


def pdf_identity(url: str) -> str:
    """Host-independent identity for the shared Drupal files directory (for ids / thumbnails)."""
    p = urlsplit(url)
    if p.hostname in DRUPAL_HOSTS and p.path.startswith("/sites/"):
        return urlunsplit(("https", CANON_FILES_HOST, p.path, "", ""))
    return url


def pdf_url_from_href(href: str, page_url: str, *, type_attr: str = "", classes: str = "",
                      text: str = "") -> str | None:
    """If an <a href>/<iframe src> points to a PDF return its canonical URL, else None.

    Recognises: *.pdf (any case, with ?query), PDF viewers (?file=/x.pdf, docs.google.com/viewer?url=),
    Drupal file links marked type="application/pdf" / class *application-pdf*, and
    /media/<id>/download or /file/<id> links whose text is a *.pdf file name."""
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    try:
        absu = urljoin(page_url, href)
        p = urlsplit(absu)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    path = unquote(p.path).lower()
    if path.endswith(".pdf"):
        return canon_pdf_url(absu)
    if p.query:
        for _k, v in parse_qsl(p.query, keep_blank_values=False):
            v = v.strip()
            if re.search(r"(?i)\.pdf$", unquote(urlsplit(v).path if "://" in v or v.startswith("/") else v)):
                if _VIEWER_RE.search(absu) or _k.lower() in ("file", "url", "src", "doc", "pdf"):
                    return canon_pdf_url(urljoin(absu, v))
    if "pdf" in (type_attr or "").lower() or re.search(r"application-pdf|file--pdf|mime-application-pdf", classes or ""):
        return canon_pdf_url(absu)
    if re.search(r"(?i)\.pdf\s*$", text or "") and re.search(r"^/(media/\d+/download|file/\d+|system/files/)", p.path):
        return canon_pdf_url(absu)
    return None


def upload_month_of(url: str) -> str | None:
    """'…/sites/default/files/2026-02/x.pdf' → '2026-02' (Drupal stores uploads by month)."""
    m = re.search(r"/files/(20\d{2}|19\d{2})-(0[1-9]|1[0-2])/", urlsplit(url).path)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def filename_of(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "document.pdf"


def strip_site_suffix(title: str) -> str:
    """'GVR Resources | AA Grapevine' → 'GVR Resources'."""
    t = clean_text(title)
    t = re.sub(r"\s*[|–—-]\s*(AA\s+)?(Grapevine|La\s+Vi[ñn]a)(,?\s+Inc\.?)?\s*$", "", t, flags=re.I)
    return t.strip()


# =====================================================================================
#  Titles
# =====================================================================================
def _fold(s: str) -> str:
    """lower-case + remove accents (for keyword matching)."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def looks_like_filename(s: str) -> bool:
    s = s.strip()
    if re.search(r"(?i)\.(pdf|docx?|pptx?|jpe?g|png|indd)$", s):
        return True
    # one "word" glued with _ or - (e.g. GV_Catalog_2026, Icon-Apps-Available-GV-Poster)
    return " " not in s and bool(re.search(r"[_]", s) or s.count("-") >= 2)


def clean_title(t: str) -> str:
    """Tidy a candidate title: drop '(PDF)', file sizes, stray punctuation and whitespace."""
    t = clean_text(t)
    if not t:
        return ""
    # "(PDF)", "[pdf]", "(1.2 MB)", "(PDF, 350 KB)" …
    t = re.sub(r"(?i)[(\[]\s*(?:pdf\s*[,;/|–-]?\s*)?(?:\d+(?:[.,]\d+)?\s*(?:kb|mb|gb|bytes?))?\s*[)\]]", " ", t)
    t = re.sub(r"(?i)[\s,;–—-]+\d+([.,]\d+)?\s*(kb|mb|gb)\b", " ", t)
    t = re.sub(r"(?i)\s*[-–—|:]\s*pdf\s*$", "", t)
    t = re.sub(r"(?i)\s+pdf$", "", t)
    t = re.sub(r"(?i)\.pdf$", "", t)
    t = re.sub(r"([!?])\1+", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = t.strip(" \t-–—|:;,·•»›>_")
    # unbalanced trailing "(" or leading ")"
    if t.count("(") > t.count(")"):
        t = t.rstrip("( ")
    if len(t) > 140:
        t = t[:140].rsplit(" ", 1)[0].rstrip(",;:-– ") + "…"
    return t


_LEARN_MORE_ABOUT = re.compile(r"(?i)^(?:learn|read|find\s+out|conoce|lee|leer|descubre)\s+(?:more|m[aá]s)\s+"
                               r"(?:about|on|sobre|acerca\s+de|de|del)\s+")


def _weak(t: str) -> bool:
    words = re.findall(r"[a-záéíóúüñ]+", t.lower())
    return not words or all(w in WEAK_WORDS for w in words)


def clean_link_text(t: str) -> str | None:
    """A link text usable as a title, or None if it is generic ('Download', 'Aquí', 'File here',
    'Conoce más aquí', '1.2 MB'…). Strips 'Download …', '… here', 'Learn more about …'."""
    t = clean_title(t)
    if not t or len(t) < 3 or not re.search(r"[A-Za-zÀ-ÿ]", t):
        return None
    if SIZE_TEXT.match(t) or re.match(r"(?i)^(https?://|www\.)", t):
        return None
    t = TRAILING_HERE.sub("", t).strip()
    t = _LEARN_MORE_ABOUT.sub("", t).strip()
    if not t or GENERIC_LINK_TEXT.match(t) or GENERIC_PREFIX.match(t) or _weak(t):
        return None
    stripped = LEADING_VERB.sub("", t).strip()
    if stripped and stripped != t:
        if GENERIC_LINK_TEXT.match(stripped) or len(stripped) < 3 or _weak(stripped):
            return None
        t = stripped
    if looks_like_filename(t):
        return None
    # a single short word ("Carta", "Info") is too vague — let a better source win
    if " " not in t and len(t) < 6:
        return None
    return t[0].upper() + t[1:]


def language_of_link(text: str) -> str | None:
    """'Spanish' / 'READ Spanish version' / 'leer en Inglés' → 'es' / 'es' / 'en'; else None."""
    m = LANGUAGE_ONLY.match(clean_text(text))
    return _LANG_WORD.get(_fold(m.group(1))) if m else None


def language_label(doc_lang: str, in_lang: str) -> str:
    """Name of `doc_lang` written in `in_lang`: ('es', 'en') → 'Spanish'."""
    return LANGUAGE_NAMES.get(doc_lang, {}).get(in_lang if in_lang in ("en", "es", "fr") else "en", doc_lang)


def page_title_is_generic(title: str, page_url: str) -> bool:
    """True for section/listing pages whose title just repeats the URL ('News Release' on
    /news-release, 'Servicio' on /servicio, 'Home'…) — such titles do not describe a PDF."""
    t = re.sub(r"[^a-z0-9]+", " ", _fold(title)).strip()
    if not t or t in ("home", "inicio", "principal", "accueil"):
        return True
    slug = urlsplit(page_url).path.rstrip("/").rsplit("/", 1)[-1]
    s = re.sub(r"[^a-z0-9]+", " ", _fold(unquote(slug))).strip()
    return t == s


# Language codes in file-name titles: UPPERCASE codes only (the Spanish words "en"/"es" must survive),
# spelled-out language names in any case.
_LANG_CODES = re.compile(r"(?:^|\s)(?:SP|SPAN|SPA|ESP|ES|ENG|EN|FRE|FR|Span|Spa|Esp|Eng|Fre)(?=\s|$)")
_LANG_WORDS = re.compile(r"(?i)(?:^|\s)(?:spanish|english|french|espa[nñ]ol|ingl[eé]s|franc[eé]s|fran[cç]ais)(?=\s|$)")


def strip_lang_tokens(title: str) -> str:
    """'SP Retrofit Completion Return to Office' → 'Retrofit Completion Return to Office'."""
    t = _LANG_WORDS.sub(" ", _LANG_CODES.sub(" ", title))
    t = re.sub(r"\s{2,}", " ", t).strip(" -–")
    return t or title


def clean_img_alt(t: str) -> str | None:
    """'Icon of Grapevine Workbook' → 'Grapevine Workbook'; 'GV Guidelines Img' → 'GV Guidelines'."""
    t = clean_text(t)
    t = re.sub(r"(?i)^(icon(o)?|image|imagen|img|logo|thumbnail|miniatura)(\s+(of|for|de|del|para))?[\s\-:_]+", "", t)
    t = re.sub(r"(?i)[\s\-_]+(icon|icono|img|image|imagen|cover|portada|thumbnail|logo)$", "", t)
    if looks_like_filename(t):
        t = pretty_filename(t.replace("-", "_") + ".pdf")
    return clean_link_text(t)


_BAD_META = re.compile(
    r"(?ix)microsoft\s+(word|powerpoint|excel|publisher)|\.(docx?|pptx?|xlsx?|indd|pdf|ai|psd|pub|qxp|txt|rtf|eps)\b"
    r"|^untitled|^sin\s+t[ií]tulo|^slide\s*\d|powerpoint\s+presentation|presentaci[oó]n\s+de\s+powerpoint"
    r"|^document\s*\d*$|^documento\s*\d*$|^adobe|^layout\s*\d|^print$|^title$|^pdf$|^\s*[\d\s._-]+$"
    r"|^[a-z]:\\|[/\\]|^\w+\d{3,}$|^(new\s+)?(blank|page)\s*\d*$|^canva|^flyer$|^volante$")


def meta_title_ok(t: str | None) -> str | None:
    """PDF metadata Title if it looks written by a human, else None."""
    raw = clean_text(t or "")
    if _BAD_META.search(raw) or "_" in raw:          # judged BEFORE cleaning strips ".pdf" etc.
        return None
    t = clean_title(raw)
    if not t or len(t) < 4 or len(t) > 150 or _BAD_META.search(t) or looks_like_filename(t):
        return None
    if not re.search(r"[A-Za-zÀ-ÿ]{3}", t):
        return None
    return t


_SMALL_WORDS = set("a an and as at but by for in of on or the to vs via y e o u de del la las el los en con por para al".split())


def smart_case(line: str) -> str:
    """Title-case an ALL-CAPS line: 'SPIRITUALITY AND GOD-TALK' → 'Spirituality and God-Talk'."""
    if line.upper() != line or not re.search(r"[A-Z]{3}", line):
        return line
    words = line.lower().split(" ")
    out = []
    for i, w in enumerate(words):
        if w in ("aa", "gv", "lv", "gvr", "rlv", "gso", "oss", "usa", "ii", "iii", "iv"):
            out.append(w.upper())
        elif i and w in _SMALL_WORDS:
            out.append(w)
        else:
            out.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(out)


def heading_from_text(text: str) -> str | None:
    """Guess a title from the first lines of page-1 text (skips brand lines, dates, URLs)."""
    for raw in (text or "").splitlines()[:25]:
        line = clean_text(raw)
        if len(line) < 6 or len(line) > 90 or _BRAND_LINE.match(line):
            continue
        letters = sum(ch.isalpha() for ch in line)
        if letters < 0.6 * len(line.replace(" ", "")) or len(line.split()) < 2:
            continue
        if re.search(r"(?i)\b(www\.|https?://|@|\.com|\.org|tel\.?|phone|p\.o\. box)\b", line):
            continue
        if re.fullmatch(r"(?i)[\w\s,.-]*\b(19|20)\d{2}\b[\w\s,.-]*", line) and letters < 12:
            continue
        # letter-spaced text ("G R A P E V I N E") is not a title
        if re.search(r"(?:\b\w\s){4,}", line):
            continue
        return clean_title(smart_case(line))
    return None


def title_from_filename(url: str) -> str:
    """'revSubscription-Order-Form_v52424.pdf' → 'Subscription Order Form'."""
    name = filename_of(url)
    name = re.sub(r"(?i)(\.pdf)+$", "", name)
    name = re.sub(r"_\d$", "", name)                            # Drupal duplicate suffix _0, _1
    name = re.sub(r"^rev(?=[A-Z])", "", name)                   # 'revGV_ORDER…' revision prefix
    name = re.sub(r"(?i)[_\-\s]+v\d{3,}$", "", name)             # version stamps _v52424
    name = re.sub(r"(?i)[_\-\s]+rev[_\-\s]*\d[\d_\-\s]*$", "", name)  # _rev101024, _rev_02-_6_24
    name = re.sub(r"(?i)[_\-\s]+(final|edited|web|new|upd\w*|jw|print)$", "", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", name)        # EditorialCalendar → Editorial Calendar
    if "_" not in name and " " not in name:                      # hyphens are the only separators
        name = name.replace("-", " ")
    name = re.sub(r"[\[\]{}]", " ", name)
    title = smart_case(pretty_filename(name + ".pdf"))
    title = re.sub(r"(?<=[A-Za-z])-(?=\d)|(?<=\d)-(?=[A-Za-z])", " ", title)   # October-2026 → October 2026
    title = re.sub(r"-{2,}", " ", title)
    title = re.sub(r"\s+-\s*|\s*-\s+", " - ", title)
    title = clean_title(title)
    return (title[:1].upper() + title[1:]) if title else "PDF document"


# On event pages a link called just "Flyer" / "Registration" is meaningful next to the event name.
EVENT_ROLE_TEXT = re.compile(
    r"(?i)^(?:(?:event|evento|the)\s+)?(flyer|flier|volante|registration(?:\s+form)?|registro|inscripci[oó]n|"
    r"form|formulario|program(?:me)?|programa|schedule|horario|agenda|brochure|folleto|poster|afiche|"
    r"information|informaci[oó]n|details|detalles|map|mapa)(?:\s+(?:here|aqu[ií]))?$")


def choose_title(*, link_texts: list[str], img_alts: list[str], meta_title: str | None,
                 text_heading: str | None, url: str, event_title: str | None = None,
                 page_title: str | None = None) -> tuple[str, str]:
    """Pick the best human title. Returns (title, source) where source ∈
    link | alt | meta | event | page | text | file. Candidates must be in referrer-priority order.
      event_title – the event's name, for PDFs linked only from event pages
      page_title  – title of a specific (non-listing) page that links only this one PDF"""
    ev = clean_title(event_title) if event_title else ""
    if ev:
        for t in link_texts:
            role = clean_title(t)
            if EVENT_ROLE_TEXT.match(role):
                return f"{ev} – {role[:1].upper()}{role[1:]}", "event"
    for t in link_texts:
        c = clean_link_text(t)
        if c:
            return c, "link"
    for t in img_alts:
        c = clean_img_alt(t)
        if c:
            return c, "alt"
    c = meta_title_ok(meta_title)
    if c:
        # metadata "GV News" vs file "GV News October 2026": the more specific file name wins
        f = strip_lang_tokens(title_from_filename(url))
        mw, fw = set(re.findall(r"\w+", _fold(c))), set(re.findall(r"\w+", _fold(f)))
        if mw and mw < fw:
            return f, "file"
        return c, "meta"
    if ev:                       # the event's name beats a guessed heading or a file name
        return ev, "event"
    if page_title and clean_title(page_title):
        return clean_title(page_title), "page"
    f = strip_lang_tokens(title_from_filename(url))
    # a heading guessed from page-1 text is noisy: only better than an uninformative file name
    if text_heading and _poor_filename_title(f):
        return text_heading, "text"
    return f, "file"


def _poor_filename_title(t: str) -> bool:
    """'Scan0001', 'Document 3', 'IMG 2231', '12345' … say nothing about the document."""
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", t)]
    return (len(words) < 2 or bool(re.match(r"(?i)^(scan|img|image|document|doc|file|untitled|flyer|volante|"
                                            r"pdf|new|copy|print)[\s\d_.-]*$", t)))


# =====================================================================================
#  Language
# =====================================================================================
def lang_from_markers(texts: list[str]) -> str | None:
    for t in texts:
        for lang, rx in LANG_TITLE_MARKERS:
            if rx.search(t or ""):
                return lang
    return None


def lang_from_filename(url: str) -> str | None:
    name = re.sub(r"(?i)(\.pdf)+$", "", filename_of(url))
    for lang, rx in LANG_FILENAME_HINTS:
        if rx.search(name):
            return lang
    return None


def doc_language(*, url: str, texts: list[str], text_sample: str | None, text_lang: str | None,
                 host_prior: str) -> str:
    """Language of the DOCUMENT: explicit markers > file-name hints > page-1 text > host prior."""
    return (lang_from_markers(texts) or lang_from_filename(url)
            or (text_lang if text_lang in ("en", "es", "fr") else None)
            or (detect_lang(text_sample) if text_sample and len(text_sample) > 200 else None)
            or host_prior)


_BRAND_NAMES = re.compile(r"(?i)\b(la\s+vi[ñn]a|la\s+vigne|grapevine|alcoholics\s+anonymous|"
                          r"alcoh[oó]licos\s+an[oó]nimos|aa|gv|lv|gvrs?|rlvs?)\b")


def title_language(title: str, doc_lang: str) -> str:
    """Language of the TITLE (what the translator must translate from). Brand names are ignored
    ("La Viña" does not make "La Viña 30 Anniversary" Spanish) and the document language is the
    prior, so neutral titles ('Catalog 2026') inherit it."""
    lang = detect_lang(_BRAND_NAMES.sub(" ", title), prior=doc_lang)
    return lang if lang in ("en", "es", "fr") else doc_lang


# =====================================================================================
#  Category / document type
# =====================================================================================
def doc_type(*, texts: list[str], sections: list[str], url: str, referrer_urls: list[str] | None = None) -> str | None:
    hay_for = {
        "text": _fold(" | ".join([*texts, filename_of(url).replace("_", " ")])),
        "section": _fold(" | ".join(sections)),
        "referrer": " | ".join(urlsplit(u).path.lower() for u in referrer_urls or []),
    }
    for cat, where, rx in _DOC_TYPE_RULES:
        hay = hay_for.get(where, "")
        if hay and rx.search(hay):
            return cat
    return None


def classify(*, referrer_urls: list[str], texts: list[str], sections: list[str], url: str) -> tuple[str, list[str]]:
    """Returns (category, tags). Kit pages → gvr/rlv (+ doc-type tag); PDFs linked only from event
    pages → flyer; otherwise the document type from DOC_TYPE_RULES, else 'other'."""
    dtype = doc_type(texts=texts, sections=sections, url=url, referrer_urls=referrer_urls)
    for ref in referrer_urls:
        if ref in KIT_PAGES:
            return KIT_PAGES[ref], [dtype] if dtype else []
    if referrer_urls and all(is_event_path(r) for r in referrer_urls):
        return "flyer", ["event"]
    return (dtype or "other"), []
