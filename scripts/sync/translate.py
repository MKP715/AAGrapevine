"""Offline English ⇄ Spanish machine translation for the whole site.

Open source only, no API keys:
  * CTranslate2 (MIT) runs the models on the CPU,
  * SentencePiece (Apache-2.0) tokenizes,
  * the models are Argos Translate packages v1.0 (OPUS-MT derived, CC-BY 4.0),
    downloaded automatically on first use into GV_MODELS_DIR (default .cache/models).

What this module adds on top of the raw model (every item below fixes a real defect
observed with these models on our content):
  * sentence splitting (the model silently DROPS the 2nd sentence of multi-sentence input),
    aware of abbreviations (St., Dr., Sra., a.m., A.A., No. 5 …), lines, bullets, "|" separators;
  * protected spans → placeholder tokens (XQ1, XQ2 …; verified empirically to survive the model):
    URLs, e-mails, @handles, #hashtags, phone numbers, Zoom ids, clock times, emoji and any
    character the model cannot represent, plus glossary KEEP names ("Dear Grapevine" became
    "Querido abuelo" without this);
  * glossary TERMS (data/translations/glossary.yml) inserted through the same placeholders;
  * Title Case / ALL CAPS titles and single Capitalized words are also translated lower-cased and
    the better variant is kept (fewer untranslated source words wins, ties broken by the model's
    own score; the model copied "Loneliness" like a name). A single word only counts as translated
    if its translation translates back to it ("Gripevine" → "gripe" = flu is refused);
  * Spanish output of a Title Case source is put in sentence case ("Riendo Nuestro camino"), and
    build_data gives English titles translated from Spanish Title Case;
  * one quoted Capitalized word ("about “Loneliness.”") is translated on its own first (inside
    the sentence the model invented "Lonabilidad");
  * "Victor E."-style names (first name + initial) are re-translated protected if the model
    changed them ("jengibre S.", "vencedor E."); visible at first so Spanish gets the gender right;
  * source rewrites for phrasings the model gets wrong ("Catch the meeting" → "Don't miss …") and
    a guard that rewrites any form of "coger" (vulgar in Mexico) as "agarrar"/"tomar";
  * LOCALIZED spans are written by rules, never by the model: "[Season 11, Episode 12]" ⇄
    "[Temporada 11, Episodio 12]" (the model wrote "[septiembre 11, …]"), "Season 3", dates
    ("July 22, 2026" ⇄ "22 de julio de 2026", "Septiembre / Octubre 2026", "Ene-Feb '19"),
    prices ("$29,99" → "$29.99") and Spanish ordinals ("75º" → "75th");
  * runs of hashtags, and codes such as "v52424" / "Panel77", are protected as one span;
  * short "Name - Title" lines are translated piece by piece around the dash;
  * file-name-like titles ("Writing-Workshop-Guidelines") are de-hyphenated first;
  * Spanish ¿…? ¡…! are paired per sentence (a question glued to a statement by a missing period
    after a code is split off first); first-letter case and terminal punctuation match the source;
  * "Tradition Ten" / "Tenth Tradition" → "Décima Tradición", "Area 51 Assembly" → "Asamblea del
    Área 51", "30 Aniversario" → "30th Anniversary", sizes ("8.5 x 11") kept;
  * a GUARD rejects any model output with a repeated-word loop, > 2.5× length growth or
    changed numbers; that sentence keeps its original text (logged) — garbage is never shipped;
  * text already in the target language, URLs, numbers, codes … are passed through untouched.

Results are cached in data/translations/cache.json (one entry per line → small git diffs);
data/translations/overrides.yml always wins. Editing the glossary re-translates only the
cached texts that contain the changed phrases; bumping ENGINE_VERSION re-translates all.

API
    from scripts.sync.translate import translate_texts, translate_markdown, get_translator
    translate_texts(["Dear Grapevine"], "en", "es")            → ["Dear Grapevine"]
    get_translator().translate(texts, "en", "es")               → [(text|None, machine: bool), …]

CLI
    python -m scripts.sync.translate "Welcome, new GVRs!" --to es
    python -m scripts.sync.translate --download                 # just fetch/verify the models
    python -m scripts.sync.translate --stats
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from .common import MODELS_DIR, TRANSLATIONS_DIR, _EN_WORDS, _ES_WORDS, clean_text, detect_lang, get_logger

log = get_logger("translate")

# Bump to re-translate EVERYTHING (e.g. after changing the pipeline below or the models).
# v4: localized spans (season/episode, dates, prices), hashtag runs, codes, output guard.
# v5: single words/titles also lower-cased, quoted phrases on their own, ¿¡ per sentence, no
#     "coger", names with an initial kept, "Tradition Ten", Spanish sentence case, sizes.
ENGINE_VERSION = "argos1.0-ct2/v5"

LANGS = ("en", "es")
PAIRS = {("en", "es"): "en_es", ("es", "en"): "es_en"}
MODEL_URLS = {
    # Argos Translate package index (https://github.com/argosopentech/argospm-index).
    # Do NOT switch es_en to 1.9: it needs a different (BPE) tokenizer.
    "en_es": "https://argos-net.com/v1/translate-en_es-1_0.argosmodel",
    "es_en": "https://argos-net.com/v1/translate-es_en-1_0.argosmodel",
}
# SHA-256 of the packages as downloaded when this pipeline was built. A mismatch only logs a
# warning (the package is still validated structurally) so a harmless re-upload upstream cannot
# silently switch translation off.
MODEL_SHA256: dict[str, str] = {
    "en_es": "d698d0ef87ad70d5d184b7fa6965905bf4368f09a2bb9ffb165a79bac96af0c4",
    "es_en": "1b963aa0e0cb6e5ce874f0aa1a1949a19bc4d762e833532239a8834340f6b378",
}

CACHE_PATH = TRANSLATIONS_DIR / "cache.json"
GLOSSARY_PATH = TRANSLATIONS_DIR / "glossary.yml"
OVERRIDES_PATH = TRANSLATIONS_DIR / "overrides.yml"

BEAM_SIZE = 4
MAX_DECODING_LENGTH = 256
MAX_BATCH_SIZE = 32
LONG_SEGMENT_CHARS = 320          # longer sentences are split at ; , — before translation
SHORT_LINE_CHARS = 110            # lines up to this long (titles) are also split at " - "
MAX_GROWTH = 2.5                  # guard: an output this many times longer than its source is rejected


def _threads() -> int:
    """CPU threads for the model. GitHub runners have 2–4 cores; GV_MT_THREADS overrides."""
    env = os.environ.get("GV_MT_THREADS")
    if env and env.isdigit() and int(env) > 0:
        return int(env)
    return max(1, min(4, os.cpu_count() or 2))


# =========================================================================== models
def model_dir(pair: str, models_dir: Path | None = None) -> Path:
    return Path(models_dir or MODELS_DIR) / pair


def model_ready(pair: str, models_dir: Path | None = None) -> bool:
    d = model_dir(pair, models_dir)
    mb = d / "model" / "model.bin"
    return mb.is_file() and mb.stat().st_size > 1_000_000 and (d / "sentencepiece.model").is_file()


def ensure_models(pairs: Iterable[str] = ("en_es", "es_en"), models_dir: Path | None = None,
                  download: bool = True) -> dict[str, bool]:
    """Make sure the model folders exist; download + verify + unpack them if missing."""
    out = {}
    for pair in pairs:
        if model_ready(pair, models_dir):
            out[pair] = True
            continue
        if not download:
            out[pair] = False
            continue
        try:
            _download_pair(pair, Path(models_dir or MODELS_DIR))
            out[pair] = model_ready(pair, models_dir)
        except Exception as e:  # network down, bad zip … → site still builds, untranslated
            log.error("could not install translation model %s: %s", pair, e)
            out[pair] = False
    return out


def _download_pair(pair: str, models_dir: Path) -> None:
    import requests  # local import: only needed on a fresh machine / CI cache miss

    url = MODEL_URLS[pair]
    models_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".download-{pair}-", dir=models_dir))
    try:
        zpath = tmp / f"{pair}.argosmodel"
        digest = ""
        for attempt in range(1, 4):
            try:
                log.info("downloading translation model %s (≈90 MB) from %s", pair, url)
                h = hashlib.sha256()
                with requests.get(url, stream=True, timeout=(20, 120),
                                  headers={"User-Agent": "NETA65-GrapevineCommitteeBot/2.0"}) as r:
                    r.raise_for_status()
                    with open(zpath, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            h.update(chunk)
                digest = h.hexdigest()
                break
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                log.warning("download attempt %d failed (%s); retrying", attempt, e)
                time.sleep(10 * attempt)
        size = zpath.stat().st_size
        if size < 20_000_000:
            raise RuntimeError(f"downloaded package is too small ({size} bytes)")
        want = MODEL_SHA256.get(pair)
        if want and digest != want:
            log.warning("model %s sha256 %s differs from the pinned %s (validating structure instead)",
                        pair, digest, want)
        log.info("model %s: %.1f MB, sha256 %s", pair, size / 1e6, digest)

        unpack = tmp / "unpacked"
        unpack.mkdir()
        root = unpack.resolve()
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            prefix = f"{pair}/"
            if not any(n.startswith(prefix) for n in names):
                # tolerate a differently named top folder
                tops = {n.split("/", 1)[0] for n in names if "/" in n}
                if len(tops) != 1:
                    raise RuntimeError(f"unexpected package layout: {sorted(tops)[:5]}")
                prefix = tops.pop() + "/"
            for info in z.infolist():
                if info.is_dir() or not info.filename.startswith(prefix):
                    continue
                rel = info.filename[len(prefix):]
                if not rel or rel.startswith("stanza/"):   # stanza sentence splitter not needed
                    continue
                target = (unpack / rel).resolve()
                if root not in target.parents:              # zip-slip guard
                    raise RuntimeError(f"unsafe path in package: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        meta = json.loads((unpack / "metadata.json").read_text(encoding="utf-8"))
        want_from, want_to = pair.split("_")
        if meta.get("from_code") != want_from or meta.get("to_code") != want_to:
            raise RuntimeError(f"package is {meta.get('from_code')}→{meta.get('to_code')}, expected {pair}")
        import sentencepiece as spm
        spm.SentencePieceProcessor(model_file=str(unpack / "sentencepiece.model"))  # loads = valid
        if not (unpack / "model" / "model.bin").is_file():
            raise RuntimeError("model/model.bin missing from package")
        final = models_dir / pair
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        os.replace(unpack, final)
        log.info("installed translation model %s -> %s", pair, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# =========================================================================== glossary
_FOLD_CLASSES = {"a": "aáàâäã", "e": "eéèêë", "i": "iíìîï", "o": "oóòôöõ", "u": "uúùûü", "n": "nñ", "c": "cç"}


def fold(s: str) -> str:
    """'La Viña' → 'La Vina' (strip accents, keep case)."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _term_regex(term: str, case_sensitive: bool) -> str:
    """Accent-insensitive, whitespace/hyphen-tolerant regex for a glossary phrase."""
    out = []
    for ch in unicodedata.normalize("NFC", term.strip()):
        base = fold(ch)
        low = base.lower()
        if ch.isspace():
            out.append(r"[\s\-_]+")
        elif ch in "-‐–":
            out.append(r"[\s\-‐–_]*")
        elif ch in "'’":
            out.append("['’]")
        elif low in _FOLD_CLASSES:
            cls = _FOLD_CLASSES[low]
            if base.isupper():
                cls = cls.upper()
            out.append(f"[{cls}]")
        else:
            out.append(re.escape(ch))
    body = "".join(out)
    return body if case_sensitive else f"(?i:{body})"


@dataclass
class GlossEntry:
    src: str            # phrase as it appears in the source language
    tgt: str            # what to write in the target language
    keep: bool = False
    case_sensitive: bool = False


class Glossary:
    """data/translations/glossary.yml → per-direction matchers."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.keep: list[str] = [clean_text(k) for k in (data.get("keep") or []) if clean_text(k)]
        self.terms: list[dict] = [t for t in (data.get("terms") or []) if isinstance(t, dict)]
        self._compiled: dict[tuple[str, str], tuple[re.Pattern | None, list[GlossEntry]]] = {}

    @classmethod
    def load(cls, path: Path = GLOSSARY_PATH) -> "Glossary":
        try:
            with open(path, encoding="utf-8") as f:
                return cls(yaml.safe_load(f) or {})
        except FileNotFoundError:
            return cls({})
        except Exception as e:  # a typo in the YAML must not stop the site build
            log.error("glossary %s could not be read (%s) — translating without it", path, e)
            return cls({})

    def entries(self, src: str, tgt: str) -> list[GlossEntry]:
        out: list[GlossEntry] = []
        seen: set[str] = set()
        for k in self.keep:
            key = fold(k).lower()
            if key not in seen:
                seen.add(key)
                out.append(GlossEntry(k, k, keep=True, case_sensitive=k.isupper()))
        for t in self.terms:
            only = str(t.get("only") or "").strip().lower()
            if only and only != tgt:
                continue
            s, d = clean_text(t.get(src)), clean_text(t.get(tgt))
            if not s or not d:
                continue
            key = fold(s).lower()
            if key in seen:
                continue
            seen.add(key)
            cs = bool(t.get("exact")) or (s.isupper() and len(s) <= 6)
            out.append(GlossEntry(s, d, keep=False, case_sensitive=cs))
        out.sort(key=lambda e: len(e.src), reverse=True)   # longest phrase wins
        return out

    def matcher(self, src: str, tgt: str) -> tuple[re.Pattern | None, list[GlossEntry]]:
        key = (src, tgt)
        if key not in self._compiled:
            ents = self.entries(src, tgt)
            if ents:
                alts = "|".join(f"(?P<g{i}>{_term_regex(e.src, e.case_sensitive)})" for i, e in enumerate(ents))
                pat = re.compile(rf"(?<![\w@#/.])(?:{alts})(?![\w])")
            else:
                pat = None
            self._compiled[key] = (pat, ents)
        return self._compiled[key]

    def snapshot(self) -> dict[str, dict[str, str]]:
        """{'en>es': {folded source phrase: target}} — stored in the cache to detect edits."""
        snap = {}
        for (s, t) in PAIRS:
            snap[f"{s}>{t}"] = {fold(e.src).lower(): e.tgt for e in self.entries(s, t)}
        return snap


# =========================================================================== overrides
def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(s))).strip()


class Overrides:
    """data/translations/overrides.yml: {original text: {es: fixed, en: fixed}} — always wins."""

    def __init__(self, data: dict | None = None):
        self.exact: dict[str, dict[str, str]] = {}
        self.loose: dict[str, dict[str, str]] = {}
        for k, v in (data or {}).items():
            if not isinstance(v, dict):
                continue
            vals = {str(lang).lower(): str(t) for lang, t in v.items() if t is not None and str(lang).lower() in LANGS}
            if vals:
                self.exact[_norm_key(k)] = vals
                self.loose[fold(_norm_key(k)).lower()] = vals

    @classmethod
    def load(cls, path: Path = OVERRIDES_PATH) -> "Overrides":
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return cls(data if isinstance(data, dict) else {})
        except FileNotFoundError:
            return cls({})
        except Exception as e:
            log.error("overrides %s could not be read (%s) — ignoring it", path, e)
            return cls({})

    def get(self, text: str, tgt: str) -> str | None:
        k = _norm_key(text)
        hit = self.exact.get(k) or self.loose.get(fold(k).lower())
        return hit.get(tgt) if hit else None


# =========================================================================== cache
class TranslationCache:
    """data/translations/cache.json = {sha1(src|tgt|text): {"s", "t", "v", "d"}} (+ "_meta")."""

    def __init__(self, path: Path = CACHE_PATH, enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled
        self.entries: dict[str, dict] = {}
        self.meta: dict = {}
        self.used: set[str] = set()
        self.dirty = False
        if enabled:
            self._load()

    @staticmethod
    def key(src: str, tgt: str, text: str) -> str:
        return hashlib.sha1(f"{src}|{tgt}|{text}".encode("utf-8")).hexdigest()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:  # corrupted cache → start over rather than crash
            log.warning("translation cache unreadable (%s) — starting a new one", e)
            return
        self.meta = data.pop("_meta", {}) if isinstance(data, dict) else {}
        self.entries = {k: v for k, v in (data or {}).items() if isinstance(v, dict) and "t" in v}

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, src: str, tgt: str, text: str) -> str | None:
        if not self.enabled:
            return None
        k = self.key(src, tgt, text)
        e = self.entries.get(k)
        if e and e.get("v") == ENGINE_VERSION and e.get("s") == text:
            self.used.add(k)
            return e["t"]
        return None

    def put(self, src: str, tgt: str, text: str, translation: str) -> None:
        if not self.enabled:
            return
        k = self.key(src, tgt, text)
        self.entries[k] = {"s": text, "t": translation, "v": ENGINE_VERSION, "d": f"{src}>{tgt}"}
        self.used.add(k)
        self.dirty = True

    def sync_glossary(self, glossary: Glossary) -> int:
        """Drop cached translations whose source contains a glossary phrase that was added,
        removed or changed since the cache was written. Returns the number dropped."""
        snap = glossary.snapshot()
        old = self.meta.get("glossary")
        self.meta["glossary"] = snap
        if not isinstance(old, dict):
            if self.entries:
                self.dirty = True
            return 0
        dropped = 0
        for direction, cur in snap.items():
            prev = old.get(direction) or {}
            changed = [p for p in set(cur) | set(prev) if cur.get(p) != prev.get(p)]
            if not changed:
                continue
            rx = re.compile(r"(?<!\w)(?:" + "|".join(_term_regex(p, False) for p in
                                                      sorted(changed, key=len, reverse=True)) + r")(?!\w)")
            for k in [k for k, e in self.entries.items() if e.get("d", direction) == direction]:
                if rx.search(fold(self.entries[k].get("s", ""))):
                    del self.entries[k]
                    dropped += 1
        if dropped or old != snap:
            self.dirty = True
            if dropped:
                log.info("glossary changed -> %d cached translations will be redone", dropped)
        return dropped

    def sync_overrides(self, overrides: "Overrides") -> int:
        """Drop cached translations of texts that CONTAIN an override that was added or changed
        since the cache was written. An override for a title must also fix the cached whole text
        it is part of ("Bottle to Throttle [Season 3, Episode 7]", a summary sentence): an exact
        override wins before the cache, but a longer cached text would otherwise never be redone.
        The first time (no snapshot yet) every override counts as new. Returns the number dropped."""
        snap = {k: dict(sorted(v.items())) for k, v in sorted(overrides.exact.items())}
        old = self.meta.get("overrides")
        self.meta["overrides"] = snap
        prev = old if isinstance(old, dict) else {}
        dropped = 0
        for s, t in PAIRS:
            changed = [k for k in set(snap) | set(prev)
                       if (snap.get(k) or {}).get(t) != (prev.get(k) or {}).get(t)]
            if not changed:
                continue
            rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(fold(k).lower()) for k in
                                                      sorted(changed, key=len, reverse=True)) + r")(?!\w)")
            direction = f"{s}>{t}"
            for k in [k for k, e in self.entries.items() if e.get("d") == direction]:
                src_text = fold(_norm_key(self.entries[k].get("s", ""))).lower()
                if rx.search(src_text):
                    del self.entries[k]
                    dropped += 1
        if dropped or old != snap:
            self.dirty = True
            if dropped:
                log.info("overrides changed -> %d cached translations will be redone", dropped)
        return dropped

    def reapply(self, direction: str, fix) -> int:
        """Run a text fix-up (`fix(source, cached_translation) → translation`) over the cached
        translations of one direction, so a new built-in word rule also corrects texts translated
        before it existed — without re-running the model. Returns the number of entries changed."""
        changed = 0
        for e in self.entries.values():
            if e.get("d") != direction:
                continue
            new = fix(e.get("s", ""), e["t"])
            if new != e["t"]:
                e["t"] = new
                changed += 1
        if changed:
            self.dirty = True
            log.info("word rules corrected %d cached %s translations", changed, direction)
        return changed

    def save(self, prune_unused: bool = False) -> None:
        """Write one entry per line (sorted) so the daily git diff only shows real changes."""
        if not self.enabled:
            return
        if prune_unused:
            stale = [k for k in self.entries if k not in self.used]
            if stale:
                for k in stale:
                    del self.entries[k]
                self.dirty = True
                log.info("pruned %d unused cache entries", len(stale))
        if not self.dirty and self.path.exists():
            return
        self.meta["engine"] = ENGINE_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("{\n")
            f.write('"_meta": ' + json.dumps(self.meta, ensure_ascii=False, sort_keys=True))
            for k in sorted(self.entries):
                e = self.entries[k]
                row = {"s": e["s"], "t": e["t"], "v": e.get("v"), "d": e.get("d")}
                f.write(",\n" + json.dumps(k) + ": " + json.dumps(row, ensure_ascii=False))
            f.write("\n}\n")
        os.replace(tmp, self.path)
        self.dirty = False


# =========================================================================== text helpers
# Placeholder styles, in order of preference. Both were verified to pass through the model
# unchanged in 100% of a 26-sentence test in both directions ("{1}", "[1]", "⟦1⟧", "<1>" and
# plain numbers were lost or mangled). The 2nd style is used to retry a sentence whose
# placeholders did not survive the 1st.
_PH_STYLES = [
    (lambda i: f"XQ{i}", re.compile(r"(?i)\bX\s?Q\s?(\d{1,3})(?!\d)")),
    (lambda i: f"ZX{i}Z", re.compile(r"(?i)\bZ\s?X\s?(\d{1,3})\s?Z")),
]
_ANY_PH = re.compile(r"(?i)\b(?:XQ\d{1,3}|ZX\d{1,3}Z)\b")
_MD_MARK = re.compile("\x00\\d+\x00")   # inline Markdown constructs (see translate_markdown)

# Spans that are copied verbatim (order matters: e-mail before URL before bare domain).
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'\[\]]*[^\s<>\"'\[\].,;:!?)]")
_DOMAIN = re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:org|com|net|edu|gov|io|us|mx|info|app|fm)"
                     r"(?:/[^\s<>\"']*[^\s<>\"'.,;:!?)])?(?![\w.])")
_HASHTAG = r"#\w*[^\W\d_]\w{0,40}"
# Codes: a letter touching a digit ("v52424", "Panel77", "A65_GV", "GV2026"); ordinals, decades,
# clock times and sizes ("9th", "1990s", "7pm", "2x") are ordinary words — see _code_or_word().
_CODE = re.compile(r"(?<![\w@#/.\-])(?=[A-Za-z0-9_\-]*?(?:[A-Za-z][0-9]|[0-9][A-Za-z]))"
                   r"[A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)*(?![\w\-])")
_NOT_CODE = re.compile(r"(?i)\d+(?:st|nd|rd|th|s|am|pm|x|k|er|ra|ro|do|da|to|ta|vo|va|no|na|mo|ma|o|a|os|as)")
_TIME_PATTERN = re.compile(r"(?i)\b\d{1,2}(?::\d{2})?\s?(?:[ap]\.\s?m\.|[ap]m\b)")   # 7 PM / 7:00 p.m.
# A member's first name + last initial ("Ginger S.", "Victor E.", "Mary Ann K."): anonymity-style
# names are never translated ("Ginger S." became "jengibre S.", "Victor E." "vencedor E.").
# Not "Read A.A." (the initial must not be followed by another letter/period) and not structural
# words that take a letter ("Tradition X.", "Plan B.", "Group A.").
_NAME_INITIAL = re.compile(
    r"(?<![\w.])(?!(?:Tradition|Step|Concept|Chapter|Appendix|Part|Section|Class|Group|Plan|Vitamin|Type|Level|"
    r"Room|Table|Exhibit|Option|Tradición|Paso|Concepto|Capítulo|Apéndice|Parte|Sección|Clase|Grupo|Tipo|Nivel|"
    r"Sala|Mesa|Anexo|Opción)\s)"
    r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{1,20}(?:-[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{1,20})?\s[A-ZÁÉÍÓÚÑ]\.(?![\w.])")
# Sizes: "8.5 x 11", "11×17" (the model wrote "8,5 x 11").
_DIMENSIONS = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?\s?[x×]\s?\d+(?:[.,]\d+)?(?:\s?(?:in|cm|mm|″|\"))?(?![\w.,]?\d)")
_PROTECT_PATTERNS = [
    _MD_MARK,
    _EMAIL,                                                                                 # e-mail
    _URL,                                                                                   # URL
    _DOMAIN,                                                                                # bare domain
    re.compile(r"(?<![\w@])@[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?"),             # @handle
    re.compile(rf"(?<![\w#&]){_HASHTAG}(?:[\s,]+{_HASHTAG})+(?:…|\.\.\.)?"),                # #run #of #tags
    re.compile(rf"(?<![\w#&]){_HASHTAG}"),                                                  # #hashtag
    _NAME_INITIAL,                                                                          # Victor E.
    re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),                  # phone
    re.compile(r"\b\d{3,4}(?:[ -]\d{3,4}){1,3}\b"),                                         # Zoom id
    _DIMENSIONS,                                                                            # 8.5 x 11
    _TIME_PATTERN,
    re.compile(r"\b\w+&\w+\b"),                                                             # Q&A, AT&T
    _CODE,                                                                                  # v52424
]
_URLISH = (_EMAIL, _URL, _DOMAIN)
# ONE Capitalized word in quotes: 'about “Loneliness.”'. Inside a sentence the model takes it for a
# name and garbles it (“Lonabilidad”), so it is translated on its own first (longer quoted titles —
# "Stump the Thumper", "Wit's End" — came out worse that way and are left in their sentence).
_QUOTED = re.compile(r"((?:^|(?<=[\s(\[—–]))[“\"«])([A-ZÁÉÍÓÚÑ][^\W\d_]{2,})([.,!?]?[”\"»])(?!\w)")


def _code_or_word(m: re.Match, put) -> str:
    tok = m.group(0)
    return tok if _NOT_CODE.fullmatch(tok) else put(tok)


# --------------------------------------------------------------------------- localized spans
# Written by rules in the target language (the model only sees a placeholder), so they are
# always right: episode numbering, dates, prices, ordinals.
_EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December")
_ES_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
              "octubre", "noviembre", "diciembre")
_EN_ABBR = {m[:3].lower(): i + 1 for i, m in enumerate(_EN_MONTHS)}
_ES_ABBR = {"ene": 1, "feb": 2, "fe": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
            "sep": 9, "sept": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
_ES_FULL = {m: i + 1 for i, m in enumerate(_ES_MONTHS)} | {"setiembre": 9}
# Full English month names in any case; 3-letter abbreviations only Capitalized ("Mar." not "mar").
# ("May"/"March" are also verbs → only Capitalized or ALL CAPS)
_EN_MON_FULL = (r"(?:(?i:January|February|April|June|July|August|September|October|November|December)"
                r"|May|MAY|March|MARCH)")
_EN_MON = rf"(?:{_EN_MON_FULL}|(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?)"
_ES_MON = r"(?i:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
_ES_MON_ABBR = r"(?:Ene|Feb|Fe|Mar|Abr|May|Jun|Jul|Ago|Sept|Sep|Oct|Nov|Dic)\.?"
_ES_PREPS = {"desde", "hasta", "para", "antes", "después", "despues", "partir", "de", "del", "entre", "por", "a", "al"}


def spanish_time(s: str) -> str:
    """'7 PM' / '7:00 p.m.' → '7 p. m.' / '7:00 p. m.' (the RAE style used by La Viña)."""
    m = re.fullmatch(r"(?i)\s*(\d{1,2})(?::(\d{2}))?\s?([ap])\.?\s?m\.?\s*", s or "")
    if not m:
        return s
    return f"{int(m.group(1))}{':' + m.group(2) if m.group(2) else ''} {m.group(3).lower()}. m."

SEASON_EPISODE = re.compile(
    r"\[\s*(?:Season|Seaon|Sesaon|Seasson|Session|Temporada)\s*(\d{1,3})\s*[,.;:]?\s*"
    r"(?:Episode|Episdode|Epsiode|Episod|Episodio|Ep\.?)\s*(\d{1,4})\s*\]", re.I)
_SEASON_WORD = re.compile(r"(?i)\b(Season|Temporada)\s+(\d{1,3})\b")
_EPISODE_WORD = re.compile(r"(?i)\b(Episode|Episodio)\s+(\d{1,4})\b")


def season_episode(season: int | str, episode: int | str, lang: str) -> str:
    """'[Season 11, Episode 12]' / '[Temporada 11, Episodio 12]'."""
    if lang == "es":
        return f"[Temporada {int(season)}, Episodio {int(episode)}]"
    return f"[Season {int(season)}, Episode {int(episode)}]"


def fix_season_episode(title: str, lang: str = "en") -> str:
    """Normalize typos in the bracket ('[Seaon 3. Episdode 1]' → '[Season 3, Episode 1]')."""
    return SEASON_EPISODE.sub(lambda m: season_episode(m.group(1), m.group(2), lang), title or "")


def _en_month(name: str) -> int:
    return _EN_ABBR[name.rstrip(".").lower()[:3]]


def _es_month(name: str) -> int:
    n = name.rstrip(".").lower()
    return _ES_FULL.get(n) or _ES_ABBR[n]


def _same_case(word: str, like: str) -> str:
    return word[:1].upper() + word[1:] if like[:1].isupper() else word


_ORD_ES_M = ("Primer", "Segundo", "Tercer", "Cuarto", "Quinto", "Sexto", "Séptimo", "Octavo", "Noveno", "Décimo",
             "Undécimo", "Duodécimo")
_ORD_ES_F = ("Primera", "Segunda", "Tercera", "Cuarta", "Quinta", "Sexta", "Séptima", "Octava", "Novena", "Décima",
             "Undécima", "Duodécima")


def _spelled_ordinal(m: re.Match) -> str:
    """'9th Step' → 'Noveno Paso', '3rd Tradition' → 'Tercera Tradición', '12th Concept' → 'Duodécimo Concepto'."""
    n, what, plural = int(m.group(1)), m.group(2).lower(), bool(m.group(3))
    if what == "tradition":
        return f"{_ORD_ES_F[n - 1]} Tradición" + ("es" if plural else "")
    word = _ORD_ES_M[n - 1] + ("o" if n in (1, 3) and plural else "")
    return f"{word} {'Paso' if what == 'step' else 'Concepto'}" + ("s" if plural else "")


def _ordinal_en(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


_EN_NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
_EN_ORDINAL_WORDS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                     "eleventh", "twelfth")
_TRADITION_NUM = re.compile(r"(?i)\b(Tradition)s?\s+(" + "|".join(_EN_NUMBER_WORDS) + r")\b(?![-–]\w)")
_TRADITION_ORD = re.compile(r"(?i)\b(" + "|".join(_EN_ORDINAL_WORDS) + r")\s+(Tradition)(s?)\b")


_AREA_ROLES = {"Assembly": "Asamblea", "Committee": "Comité", "Convention": "Convención", "Delegate": "Delegado",
               "Chair": "Coordinador", "Treasurer": "Tesorero", "Secretary": "Secretario", "Newsletter": "Boletín"}


def _tradition_es(n: int, plural: bool = False) -> str:
    """'Tradition Ten' / 'Tenth Tradition' → 'Décima Tradición' (how AA's Spanish literature writes it;
    the model wrote 'Tradición 10')."""
    return f"{_ORD_ES_F[n - 1]} Tradición" + ("es" if plural else "")


def _localizers(src: str, tgt: str) -> list[tuple[re.Pattern, object, str]]:
    """[(pattern, match → localized text (or None = leave it), slot kind)] for one direction."""
    out: list[tuple[re.Pattern, object, str]] = [
        (SEASON_EPISODE, lambda m: season_episode(m.group(1), m.group(2), tgt), "p")]
    if src == "en" and tgt == "es":
        es = lambda m, g=1: _ES_MONTHS[_en_month(m.group(g)) - 1]  # noqa: E731
        on = lambda m: "el " if m.group("on") else ""  # noqa: E731   "on March 14" → "el 14 de marzo"
        out += [
            (re.compile(rf"\b({_EN_MON})\s*[/–—-]\s*({_EN_MON}),?\s+(\d{{4}})\b"),
             lambda m: f"{es(m)} / {es(m, 2)} de {m.group(3)}", "p"),
            (re.compile(rf"(?P<on>\b[Oo]n\s+)?\b({_EN_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b"),
             lambda m: f"{on(m)}{int(m.group(3))} de {es(m, 2)} de {m.group(4)}", "date"),
            # (not "October 20% off" → "20 de octubre% de descuento")
            (re.compile(rf"(?P<on>\b[Oo]n\s+)?\b({_EN_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?![:.,]?\d|\s?%|\s?(?i:percent)\b)"),
             lambda m: f"{on(m)}{int(m.group(3))} de {es(m, 2)}", "date"),
            (re.compile(rf"\b({_EN_MON_FULL}),?\s+(\d{{4}})\b"), lambda m: f"{es(m)} de {m.group(2)}", "month"),
            (_SEASON_WORD, lambda m: f"{_same_case('temporada', m.group(1))} {int(m.group(2))}", "p"),
            (_EPISODE_WORD, lambda m: f"{_same_case('episodio', m.group(1))} {int(m.group(2))}", "p"),
            (re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?"), lambda m: m.group(0), "p"),
            (re.compile(r"\b(1[0-2]|[1-9])(?:st|nd|rd|th)\s+(Step|Tradition|Concept)(s?)\b", re.I), _spelled_ordinal, "t"),
            (_TRADITION_NUM, lambda m: _tradition_es(_EN_NUMBER_WORDS.index(m.group(2).lower()) + 1), "t"),
            # "Area 51 Assembly" → "Asamblea del Área 51" (the model wrote "Zona 51")
            # (not "Northeast Texas Area 65", a glossary phrase)
            (re.compile(r"(?<!Texas )\bArea\s+(\d{1,3})\s+(" + "|".join(_AREA_ROLES) + r")\b"),
             lambda m: f"{_AREA_ROLES[m.group(2)]} del Área {m.group(1)}", "t"),
            (re.compile(r"(?<!Texas )\bArea\s+(\d{1,3})\b"), lambda m: f"Área {m.group(1)}", "t"),
            (_TRADITION_ORD, lambda m: _tradition_es(_EN_ORDINAL_WORDS.index(m.group(1).lower()) + 1,
                                                     bool(m.group(3))), "t"),
            (re.compile(r"\b(\d{1,4})(?:st|nd|rd|th)\b"), lambda m: f"{int(m.group(1))}.º", "ord"),
        ]
    elif src == "es" and tgt == "en":
        en = lambda m, g=1: _EN_MONTHS[_es_month(m.group(g)) - 1]  # noqa: E731
        ab = lambda m, g=1: _EN_MONTHS[_es_month(m.group(g)) - 1][:3]  # noqa: E731

        def on_(m: re.Match) -> str:
            """'el 24 de septiembre' → 'on September 24' (but 'desde el 24 …' → 'desde XQ1' → 'from …')."""
            if not m.group("el"):
                return ""
            before = re.findall(r"[^\W\d_]+", m.string[:m.start()].lower())[-1:]
            return "" if before and before[0] in _ES_PREPS else ("On " if m.group("el")[0] == "E" else "on ")
        out += [
            (re.compile(rf"\b({_ES_MON})\s*[/–—-]\s*({_ES_MON}),?\s+(?:del?\s+)?(\d{{4}})\b"),
             lambda m: f"{en(m)} / {en(m, 2)} {m.group(3)}", "p"),
            (re.compile(rf"(?P<el>\b[Ee]l\s+)?\b(\d{{1,2}})\s+de\s+({_ES_MON})(?:\s+del?|,)?\s+(\d{{4}})\b"),
             lambda m: f"{on_(m)}{en(m, 3)} {int(m.group(2))}, {m.group(4)}", "date"),
            (re.compile(rf"(?P<el>\b[Ee]l\s+)?\b({_ES_MON})\s+(\d{{1,2}}),?\s+(\d{{4}})\b"),
             lambda m: f"{on_(m)}{en(m, 2)} {int(m.group(3))}, {m.group(4)}", "date"),
            (re.compile(rf"(?P<el>\b[Ee]l\s+)?\b(\d{{1,2}})\s+de\s+({_ES_MON})\b"),
             lambda m: f"{on_(m)}{en(m, 3)} {int(m.group(2))}", "date"),
            (re.compile(rf"\b({_ES_MON}),?\s+(?:del?\s+)?(\d{{4}})\b"), lambda m: f"{en(m)} {m.group(2)}", "p"),
            # La Viña back issues: "Ene-Feb '19", "May-Jun '18"
            (re.compile(rf"\b({_ES_MON_ABBR})\s*[-–/]\s*({_ES_MON_ABBR})\s*(['’]\d{{2}}|\d{{4}})\b"),
             lambda m: f"{ab(m)}-{ab(m, 2)} {m.group(3)}", "p"),
            (_SEASON_WORD, lambda m: f"{_same_case('season', m.group(1))} {int(m.group(2))}", "p"),
            (_EPISODE_WORD, lambda m: f"{_same_case('episode', m.group(1))} {int(m.group(2))}", "p"),
            (re.compile(r"\$\s?(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?!\d)"),
             lambda m: "$" + m.group(1).replace(".", ",") + "." + m.group(2), "p"),
            (re.compile(r"\$\s?\d[\d.]*"), lambda m: m.group(0), "p"),
            # "30 Aniversario" / "30º aniversario" → "30th Anniversary" (the model wrote "30Th")
            (re.compile(r"\b(\d{1,3})\s?(?:\.?[º°ª])?(?=\s+(?i:aniversario)\b)"),
             lambda m: _ordinal_en(int(m.group(1))), "p"),
            (re.compile(r"\b(\d{1,3})\s?\.?[º°](?!\w)"), lambda m: _ordinal_en(int(m.group(1))), "p"),
        ]
    return out


_LOCALIZERS: dict[tuple[str, str], list] = {}


def localizers(src: str, tgt: str) -> list:
    if (src, tgt) not in _LOCALIZERS:
        _LOCALIZERS[(src, tgt)] = _localizers(src, tgt)
    return _LOCALIZERS[(src, tgt)]

_WORD = re.compile(r"[^\W\d_][\w'’\-]*")
_SMALL = {
    "en": set("a an the and or nor but of in on at to for with by from as into onto over per via vs is are be".split()),
    "es": set("a al el la los las un una unos unas y e o u ni de del en con por para sin sobre entre que es son su sus".split()),
}
_KEEP_CAPS = {"I", "God", "Dios"}

_ABBREV = {
    "st", "dr", "dra", "sr", "sra", "srta", "mr", "mrs", "ms", "jr", "sgt", "gen", "lt", "mt", "ft", "ave", "av",
    "blvd", "rd", "hwy", "inc", "co", "corp", "ltd", "dept", "depto", "apt", "e.g", "i.e", "vs", "approx",
    "a.m", "p.m", "a.a", "u.s", "ee.uu", "ud", "uds", "lic", "ing", "prof", "fig", "ej", "tel", "ext", "aprox", "cía",
}
_ABBREV_BEFORE_DIGIT = {"no", "nos", "núm", "num", "vol", "pág", "pag", "p", "pp", "ch", "cap", "art", "sec", "ed"}
_BOUNDARY = re.compile(r"([.!?…]+)([\"'”’»)\]]*)(\s+)")
_BULLET = re.compile(r"^(\s*(?:[-*•·▪►✓✔➤→]|\d{1,3}[.)]|[a-zA-Z][.)])\s+)")
_NAME_BEFORE_DASH = re.compile(r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{1,14}(?:\s+[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{1,14})?"
                               r"(?:\s+[A-ZÁÉÍÓÚÑÜ]\.)?")
_TRAILING_BRACKET = re.compile(r"^(.*?\S)(\s*)([\[(][^\[\]()]{1,60}[\])])\s*$")
_LETTER = re.compile(r"[^\W\d_]")


def _lang_votes(text: str) -> dict[str, float]:
    t = text.lower()
    words = re.findall(r"[a-záéíóúüñ']+", t)
    es = sum(1 for w in words if w in _ES_WORDS) + 2 * len(re.findall(r"[ñ¿¡]", t)) + len(re.findall(r"[áéíóú]", t))
    es += sum(1 for w in words if re.search(r"(ción|ciones|dad|dades|mente)$", w))
    en = sum(1 for w in words if w in _EN_WORDS)
    en += sum(1 for w in words if re.search(r"(ing|tion|ness|ship)$", w) and not w.endswith("ción"))
    return {"es": es, "en": en, "_n": len(words)}


def looks_like(text: str, lang: str) -> bool:
    """Strong evidence that `text` is ALREADY written in `lang` (used to skip translation)."""
    v = _lang_votes(text)
    other = "en" if lang == "es" else "es"
    return v["_n"] >= 4 and v[lang] >= 3 and v[lang] >= 2.5 * max(v[other], 0.5)


def is_code_token(token: str) -> bool:
    """True for ONE token that is a code, never a word: "v52424", "Panel77", "GVLV2027", "2026-27",
    "GV_LV", "A65_GV". Dictionary words ("Loneliness", "Hope"), ordinals/decades/times ("9th",
    "1990s", "7pm") and file-name-like phrases ("Writing_Workshop_Guidelines") are not codes."""
    t = (token or "").strip().strip(".,;:!?¿¡()[]{}\"'“”‘’«»")
    if not t or re.search(r"\s", t) or _NOT_CODE.fullmatch(t):
        return False
    if re.search(r"\d", t):
        return True
    if "_" in t:
        parts = [p for p in re.split(r"[_\-]+", t) if p]
        return bool(parts) and all(p.isupper() or len(p) <= 2 for p in parts)
    return False


def needs_translation(text: str) -> bool:
    """False for empty strings, URLs, numbers, codes — anything without real words."""
    if not text or not _LETTER.search(text):
        return False
    rest = text
    for p in _URLISH:
        rest = p.sub(" ", rest)
    rest = rest.strip()
    if rest and not re.search(r"\s", rest) and is_code_token(rest):
        return False          # one code-like token: "v52424", "2026-27", "GVLV2027", "GV_LV"
    rest = _CODE.sub(lambda m: m.group(0) if _NOT_CODE.fullmatch(m.group(0)) else " ", rest)
    return any(len(w) >= 2 for w in _WORD.findall(rest))


def split_sentences(line: str) -> list[tuple[str, str]]:
    """'One. Two!' → [('One.', ' '), ('Two!', '')] — abbreviation-aware."""
    out, start = [], 0
    for m in _BOUNDARY.finditer(line):
        nxt = line[m.end():m.end() + 1]
        if not nxt:
            continue
        if nxt.islower():
            continue
        if m.group(1) == ".":
            before = re.search(r"(\S+)$", line[start:m.start()])
            tok = before.group(1).lstrip("(\"'“‘[¿¡").lower() if before else ""
            if tok in _ABBREV or (len(tok) == 1 and tok.isalpha()):
                continue
            if tok in _ABBREV_BEFORE_DIGIT and nxt.isdigit():
                continue
            if re.fullmatch(r"(?:[a-z]\.)+[a-z]", tok):       # U.S.A. / A.A.
                continue
        out.append((line[start:m.end(2)], m.group(3)))
        start = m.end()
    if start < len(line):
        out.append((line[start:], ""))
    return [piece for sent, ws in out for piece in _split_runon(sent, ws)]


# A question/exclamation glued to the sentence before it by a missing period after a code:
# "… use Zoom code 871 2036 8287 with password 238047 Wednesday's not a good time?" became
# "¿Para unirse … 238047 Miércoles no es un buen momento?" (¿ in front of the statement).
_RUNON = re.compile(r"(?:(?<!\d)\d{5,}|\b\d{3,4}(?:[ -]\d{3,4}){1,3})(\s+)(?=[A-Z][a-z]+(?:['’][a-z]+)?\s+[a-z])")


def _split_runon(sent: str, ws: str) -> list[tuple[str, str]]:
    """Split a ?/! sentence at a code number followed by a new Capitalized sentence."""
    if not re.search(r"[?!][\"'”’»)\]]*\s*$", sent):
        return [(sent, ws)]
    cut = None
    for m in _RUNON.finditer(sent):
        before, after = sent[:m.start(1)], sent[m.end(1):]
        if len(before.split()) >= 4 and len(after.split()) >= 3:
            cut = m
    if cut is None:
        return [(sent, ws)]
    return [(sent[:cut.start(1)], cut.group(1)), (sent[cut.end(1):], ws)]


def _split_long(seg: str) -> list[tuple[str, str]]:
    """Very long sentences confuse the model: cut at ; — , closest to the middle."""
    if len(seg) <= LONG_SEGMENT_CHARS:
        return [(seg, "")]
    mid = len(seg) // 2
    cands = list(re.finditer(r"(?<=[;:—–,])\s+", seg)) or list(re.finditer(r"\s+", seg))
    if not cands:
        return [(seg, "")]
    m = min(cands, key=lambda m: abs(m.start() - mid))
    left, sep, right = seg[:m.start()], m.group(0), seg[m.end():]
    parts = _split_long(left)
    parts[-1] = (parts[-1][0], parts[-1][1] + sep)
    return parts + _split_long(right)


def units(text: str) -> list[tuple[bool, str]]:
    """Cut text into (translate?, piece) units; ''.join(pieces) == text."""
    out: list[tuple[bool, str]] = []

    def emit_segment(seg: str) -> None:
        for q, qsep in _split_long(seg):
            out.append((needs_translation(q), q))
            if qsep:
                out.append((False, qsep))

    for line in re.split(r"(\r?\n+)", text):
        if not line:
            continue
        if not line.strip():
            out.append((False, line))
            continue
        m = _BULLET.match(line)
        if m:
            out.append((False, m.group(1)))
            line = line[m.end():]
        lead = re.match(r"\s*", line).group(0)
        if lead:
            out.append((False, lead))
            line = line[len(lead):]
        trail = re.search(r"\s*$", line).group(0)
        if trail:
            line = line[: len(line) - len(trail)]
        # "Title | Subtitle"; short lines (titles) also at a spaced dash: "Eloy E. - De la oscuridad …"
        seps = r"(\s+\|\s+|\s+·\s+)" if len(line) > SHORT_LINE_CHARS else r"(\s+\|\s+|\s+·\s+|\s+[-–—]\s+)"
        parts = re.split(seps, line)
        for i, part in enumerate(parts):
            if i % 2:
                out.append((False, part))
                continue
            if i == 0 and len(parts) > 2 and _NAME_BEFORE_DASH.fullmatch(part) and parts[1].strip() in "-–—":
                out.append((False, part))      # a member's name: "Anselmo M." stays "Anselmo M."
                continue
            for sent, ws in split_sentences(part):
                tb = _TRAILING_BRACKET.match(sent)
                if tb:
                    emit_segment(tb.group(1))
                    if tb.group(2):
                        out.append((False, tb.group(2)))
                    emit_segment(tb.group(3))
                else:
                    emit_segment(sent)
                if ws:
                    out.append((False, ws))
        if trail:
            out.append((False, trail))
    return out


@dataclass
class Masked:
    text: str                                        # masked text sent to the model
    slots: list[str] = field(default_factory=list)   # placeholder i restores to slots[i-1]
    style: int = 0
    glossary_hits: int = 0
    kinds: list[str] = field(default_factory=list)   # per slot: p protected, k keep, t term, md link, time
    labels: list[str] = field(default_factory=list)  # per slot: text used for Spanish article agreement


class Protector:
    """Replaces protected spans + glossary phrases with numbered placeholders."""

    def __init__(self, glossary: Glossary):
        self.glossary = glossary
        self._known: dict[str, set[str]] = {}
        # translated Markdown link labels by marker (set by translate_markdown) → article agreement
        self.marker_labels: dict[str, str] = {}
        # short quoted phrases translated on their own ('about “Loneliness.”' → the model invented
        # “Lonabilidad”; set by Translator._translate_segments): {quoted source text: translation}
        self.quote_labels: dict[str, str] = {}

    def set_known_chars(self, src: str, chars: set[str]) -> None:
        self._known[src] = {c for c in chars if not c.isspace()}

    def mask(self, text: str, src: str, tgt: str, style: int = 0, names: bool = True) -> Masked:
        """names=False leaves "Victor E."-style names for the model to see (it needs them for
        the gender of the Spanish: "Kathy R. … estaba rodeada"); the Translator re-masks them when
        the model changed one ("jengibre S.")."""
        make = _PH_STYLES[style][0]
        slots: list[str] = []
        kinds: list[str] = []

        def put(value: str, kind: str = "p") -> str:
            slots.append(value)
            kinds.append(kind)
            return make(len(slots))

        out = text
        if self.quote_labels:
            gpat = self.glossary.matcher(src, tgt)[0]
            gspans = [g.span() for g in gpat.finditer(text)] if gpat is not None else []

            def quoted(m: re.Match) -> str:
                tr = self.quote_labels.get(m.group(2))
                if tr is None or any(a < m.end() and m.start() < b for a, b in gspans):
                    return m.group(0)       # a glossary phrase covers it: 'Proyecto "Lleva el Mensaje"'
                return m.group(1) + put(tr, "t") + m.group(3)
            out = _QUOTED.sub(quoted, out)
        for pat in _PROTECT_PATTERNS:
            if pat is _TIME_PATTERN and src != "en":
                continue      # es→en handles "a las 7 p. m." well on its own ("the XQ1s" if masked)
            if pat is _NAME_INITIAL and not names:
                continue
            kind = "time" if pat is _TIME_PATTERN else "p"
            if pat is _MD_MARK:
                out = pat.sub(lambda m: put(m.group(0), "md" if m.group(0) in self.marker_labels else "p"), out)
                continue
            if pat is _CODE:
                out = pat.sub(lambda m: m.group(0) if _ANY_PH.fullmatch(m.group(0)) else _code_or_word(m, put), out)
                continue
            if pat is _TIME_PATTERN and tgt == "es":
                out = pat.sub(lambda m: put(spanish_time(m.group(0)), "time"), out)
                continue
            out = pat.sub(lambda m, kind=kind: put(m.group(0), kind), out)
        # Localized spans: season/episode, dates, prices … written by rules in the target language.
        for pat, fn, kind in localizers(src, tgt):
            def loc(m: re.Match, fn=fn, kind=kind) -> str:
                if _ANY_PH.search(m.group(0)):
                    return m.group(0)
                value = fn(m)
                return m.group(0) if value is None else put(value, kind)
            out = pat.sub(loc, out)
        known = self._known.get(src, set())
        # Emoji/symbols and characters the model never saw come out as " ⁇ " or trigger
        # hallucinations ("🎉" became "inaceptable") → protect every such run.
        out = re.sub(r"[^\s\x00]+", lambda m: self._mask_unknown(m.group(0), known, put), out)
        pat, ents = self.glossary.matcher(src, tgt)
        hits = 0
        if pat is not None:
            def gl(m: re.Match) -> str:
                nonlocal hits
                hits += 1
                e = ents[int(m.lastgroup[1:])]
                return put(e.tgt, "k" if e.keep else "t")
            out = pat.sub(gl, out)
        labels = [self.marker_labels.get(v, "") if k == "md" else v for v, k in zip(slots, kinds)]
        return Masked(out, slots, style, hits, kinds, labels)

    @staticmethod
    def _safe_char(c: str, known: set[str]) -> bool:
        if unicodedata.category(c)[0] in "LNP" or c in "$%&+=<>|~^`'\"":
            return not known or c in known
        return False     # symbols (So/Sm/Sk), format chars (U+FE0F, ZWJ), private use …

    @classmethod
    def _mask_unknown(cls, token: str, known: set[str], put) -> str:
        if all(cls._safe_char(c, known) for c in token):
            return token
        res, run = [], []
        for c in token:
            if cls._safe_char(c, known):
                if run:
                    res.append(f" {put(''.join(run))} ")
                    run = []
                res.append(c)
            else:
                run.append(c)
        if run:
            res.append(f" {put(''.join(run))} ")
        return "".join(res).strip()

    @staticmethod
    def restore(text: str, masked: Masked, tgt: str = "") -> str | None:
        """Put the protected spans back; None if a placeholder was lost or duplicated."""
        rx = _PH_STYLES[masked.style][1]
        found = sorted(int(m.group(1)) for m in rx.finditer(text))
        if found != list(range(1, len(masked.slots) + 1)):
            return None
        if tgt == "es" and masked.slots:
            text = _fix_articles(text, masked)
        elif tgt == "en" and len(masked.slots) == 1 and masked.kinds[:1] == ["t"]:
            # Spanish titles use articles ("El padrinazgo"); English titles don't ("Sponsorship")
            text = re.sub(r"^\s*(?:The|the)\s+(?=" + rx.pattern.replace("(?i)", "") + r"\s*$)", "", text)
        slots = list(masked.slots)          # (local copy: one Masked serves several candidates)
        if tgt == "en" and masked.slots:
            def a_an(m: re.Match) -> str:
                i = int(re.search(r"\d+", m.group(3)).group(0)) - 1
                first = masked.slots[i][:1].lower() if i < len(masked.slots) else ""
                if not first.isalpha():
                    return m.group(0)
                art = _indefinite_en(masked.slots[i])
                return (art.capitalize() if m.group(1)[0].isupper() else art) + m.group(2) + m.group(3)
            text = re.sub(r"\b(an?|An?)(\s+)(" + rx.pattern.replace("(?i)", "") + ")", a_an, text)
        if tgt == "es" and "ord" in masked.kinds:
            # "13th anniversary" → "13.º aniversario", "5th Tradition" → "5.ª Tradición"
            # Gender from the article in front ("la 76.ª Conferencia", "el 54.º ICYPAA"), else from the
            # noun after it ("5.ª Tradición") or, when the model put the number last, before it.
            def word_at(tok: str) -> str:
                ph = rx.fullmatch(tok)
                if ph:
                    k = int(ph.group(1)) - 1
                    return (masked.labels[k] if k < len(masked.labels) else "") if masked.kinds[k] in ("t", "md") else ""
                return tok
            for m in rx.finditer(text):
                i = int(m.group(1)) - 1
                if i >= len(masked.kinds) or masked.kinds[i] != "ord":
                    continue
                art = re.search(r"\b(el|la|los|las|del|al|un|una|su|nuestra|nuestro)\s+$", text[:m.start()], re.I)
                if art:
                    fem = art.group(1).lower() in ("la", "las", "una", "nuestra")
                else:
                    after = re.match(r"\s+(\S+)", text[m.end():])
                    before = re.search(r"(\S+)\s+$", text[:m.start()])
                    noun = word_at(after.group(1)) if after else ""
                    if not noun or noun.lower() in _FUNCTION_WORDS_ES:
                        noun = word_at(before.group(1)) if before else ""
                    noun = (_TERM_WORD.findall(noun) or [""])[0]
                    gn = _gender_number(noun) if noun and not noun.isupper() else None
                    fem = bool(gn and gn[0] == "f")
                if fem:
                    slots[i] = slots[i].replace("º", "ª")
        if tgt == "en" and "date" in masked.kinds:
            # "el XQ1" (el 22 de julio) comes back as "the XQ1" → "July 22"
            def no_the(m: re.Match) -> str:
                i = int(re.search(r"\d+", m.group(2)).group(0)) - 1
                return m.group(2) if i < len(masked.kinds) and masked.kinds[i] == "date" else m.group(0)
            text = re.sub(r"\b(?:The|the)(\s+)(" + rx.pattern.replace("(?i)", "") + ")", no_the, text)
        return rx.sub(lambda m: slots[int(m.group(1)) - 1], text)


def _indefinite_en(phrase: str) -> str:
    """'a' or 'an' in front of this phrase: 'an hour', 'a unique Fellowship', 'a one-day event'."""
    w = phrase.strip().lower()
    if re.match(r"(?:uni|use|usu|uti|ura|uro|eu|ewe|one\b|once\b|u\b)", w):
        return "a"
    if re.match(r"(?:hour|honest|honor|honour|heir)", w):
        return "an"
    return "an" if w[:1] in "aeiou" else "a"


# Spanish article agreement in front of a glossary term: the model only sees "XQ1" and
# defaults to the masculine ("El XQ1" → "El Lista de verificación …"), so infer gender/number
# from the term's first word and fix the article; drop a duplicate article ("la La Viña").
_FUNCTION_WORDS_ES = {"de", "del", "en", "el", "la", "los", "las", "y", "o", "a", "al", "que", "con", "por", "para"}
_ARTICLES = {("m", "s"): "el", ("f", "s"): "la", ("m", "p"): "los", ("f", "p"): "las"}
_INDEF = {("m", "s"): "un", ("f", "s"): "una", ("m", "p"): "unos", ("f", "p"): "unas"}
_DETERMINERS = {"el", "la", "los", "las", "un", "una", "unos", "unas", "nuestro", "nuestra", "nuestros",
                "nuestras", "mi", "mis", "tu", "tus", "su", "sus"}
_NUMERALS = {"doce", "dos", "tres", "siete", "12", "7", "3", "2"}
_ART_RX = {i: re.compile(r"(?i)\b(el|la|los|las|un|una|unos|unas|del|al|varios|varias|muchos|muchas|algunos|algunas|otros|otras)(\s+)(" + pat.pattern.replace("(?i)", "") + ")")
           for i, (_mk, pat) in enumerate(_PH_STYLES)}
_TERM_WORD = re.compile(r"[^\W_]+")
_ISSUE_RX = {i: re.compile(r"(?i)\b(número|edición|ejemplar|revista|mes)(\s+)(" + pat.pattern.replace("(?i)", "") + ")")
             for i, (_mk, pat) in enumerate(_PH_STYLES)}
_QUANTIFIERS = {"vari", "much", "algun", "otr"}
_TIME_PREP_RX = {i: re.compile(r"(?i)\b(en|a)(\s+)(" + pat.pattern.replace("(?i)", "") + ")")
                 for i, (_mk, pat) in enumerate(_PH_STYLES)}


# Nouns whose gender the ending gets wrong ("el lema", "la mano").
_MASC_A = {"lema", "tema", "problema", "programa", "sistema", "idioma", "poema", "dilema", "drama", "clima",
           "esquema", "síntoma", "diploma", "emblema", "enigma", "fantasma", "panorama", "mapa", "día", "planeta",
           "sofá", "dogma", "carisma", "aroma", "cometa", "telegrama", "crucigrama", "pijama"}
_FEM_O = {"mano", "foto", "radio", "moto", "crisis", "tesis", "fe", "ley", "red", "sed", "paz", "luz", "voz", "vez",
          "cruz", "nariz", "raíz", "noche", "tarde", "gente", "mente", "muerte", "suerte", "parte", "clase", "llave",
          "calle", "fuente", "frase", "fiebre", "sangre", "carne", "leche", "nieve", "nube", "sal", "piel", "miel",
          "cárcel", "señal", "vocal", "labor", "flor", "sobriedad"}


def _gender_number(term: str) -> tuple[str, str] | None:
    words = _TERM_WORD.findall(term)
    while words and words[0].lower() in _NUMERALS:
        words = words[1:]
    if not words or words[0][:1] in "áÁ" or not words[0][:1].isalpha():
        return None
    w = words[0].lower()
    plural = len(w) > 3 and w.endswith("s") and w not in _FEM_O
    sing = w[:-2] if plural and w.endswith("es") and w[:-2] in _FEM_O else w[:-1] if plural else w
    if sing in _MASC_A:
        return ("m", "p" if plural else "s")
    if sing in _FEM_O:
        return ("f", "p" if plural else "s")
    fem = bool(re.search(r"(a|as|ión|iones|dad|dades|tad|tud|umbre|umbres)$", w))
    return ("f" if fem else "m", "p" if plural else "s")


def _fix_articles(text: str, masked: Masked) -> str:
    def fix(m: re.Match) -> str:
        art, ws, ph = m.group(1), m.group(2), m.group(3)
        idx = int(re.search(r"\d+", ph).group(0)) - 1
        if idx >= len(masked.slots):
            return m.group(0)
        value = masked.labels[idx] if idx < len(masked.labels) else masked.slots[idx]
        kind = masked.kinds[idx] if idx < len(masked.kinds) else "p"
        first = _TERM_WORD.findall(value)[:1]
        if first and first[0].lower() in _DETERMINERS:     # "la La Viña" → "La Viña"
            a = art.lower()
            if a in ("del", "al"):
                lead = "de" if a == "del" else "a"
                return (lead.capitalize() if art[0].isupper() else lead) + ws + ph
            return ph
        if kind not in ("t", "md"):
            return m.group(0)
        gn = _gender_number(value)
        if gn is None:
            return m.group(0)
        a = art.lower()
        if a[:-2] in _QUANTIFIERS:                       # "varias servidores" → "varios servidores"
            new = a[:-2] + ("as" if gn[0] == "f" else "os")
        elif a in ("un", "una", "unos", "unas"):
            new = _INDEF[gn]
        elif a == "del":
            new = "del" if gn == ("m", "s") else "de " + _ARTICLES[gn]
        elif a == "al":
            new = "al" if gn == ("m", "s") else "a " + _ARTICLES[gn]
        else:
            new = _ARTICLES[gn]
        if art[0].isupper():
            new = new[0].upper() + new[1:]
        return new + ws + ph

    def fix_time(m: re.Match) -> str:
        idx = int(re.search(r"\d+", m.group(3)).group(0)) - 1
        if idx < len(masked.kinds) and masked.kinds[idx] == "date":
            if masked.slots[idx].startswith("el "):     # "Únete a el 14 de marzo" → "Únete el 14 de marzo"
                return m.group(3)
            if m.group(1).lower() == "en":              # "en 22 de julio" → "el 22 de julio"
                return ("El" if m.group(1)[0].isupper() else "el") + m.group(2) + m.group(3)
            return m.group(0)
        if idx >= len(masked.kinds) or masked.kinds[idx] != "time":
            return m.group(0)
        hour = re.match(r"\d+", masked.slots[idx])
        art = "a la" if hour and hour.group(0) in ("1", "01") else "a las"
        return (art.capitalize() if m.group(1)[0].isupper() else art) + m.group(2) + m.group(3)
    def fix_month(m: re.Match) -> str:
        idx = int(re.search(r"\d+", m.group(3)).group(0)) - 1
        if idx < len(masked.kinds) and masked.kinds[idx] == "month":
            return m.group(1) + m.group(2) + "de " + m.group(3)
        return m.group(0)
    text = _TIME_PREP_RX[masked.style].sub(fix_time, text)
    text = _ISSUE_RX[masked.style].sub(fix_month, text)
    return _ART_RX[masked.style].sub(fix, text)


_CONJ = {("es", "en"): {"y": "and", "e": "and", "o": "or", "u": "or"}, ("en", "es"): {"and": "y", "or": "o"}}


def _translate_conjunctions(masked: str, src: str, tgt: str) -> str:
    """'XQ1 y XQ2' → 'XQ1 and XQ2' for texts made only of names (never sent to the model)."""
    table = _CONJ.get((src, tgt), {})

    def repl(m: re.Match) -> str:
        w = m.group(0)
        t = table.get(w.lower())
        if t is None:
            return w
        return t.upper() if w.isupper() and len(w) > 1 else t
    return re.sub(r"(?<=\s)\w+(?=\s)", repl, masked) if table else masked


def _plain_words(masked: str) -> list[str]:
    return [w for w in _WORD.findall(masked) if not _ANY_PH.fullmatch(w)]


def is_title_case(masked: str) -> bool:
    """'Crutches and Casts', 'Coming in' (one content word) — not 'Join us at the Spring Assembly'."""
    words = _plain_words(masked)
    if not 2 <= len(words) <= 16 or re.search(r"[.!?]\s+\S", masked):
        return False
    content = [w for w in words if w.lower() not in _SMALL["en"] and w.lower() not in _SMALL["es"]]
    if len(content) == 1:          # "Coming in", "Sober Up"
        return content[0][0].isupper() and len(words) <= 3
    return len(content) >= 2 and all(w[0].isupper() for w in content)


def is_single_word(masked: str) -> bool:
    """One capitalized word ('Loneliness', 'Publisher'): the model copies it like a name."""
    words = _plain_words(masked)
    return len(words) == 1 and not words[0].islower() and not re.search(r"[.!?]\s+\S", masked)


# EN→ES rewrites of the SOURCE before the model sees it: phrasings the model mistranslates.
# "Catch the meeting on our podcast" became "Coge/Atrapa la reunión" ("coger" is vulgar in Mexico).
_MEDIA_NOUNS = (r"(?:meetings?|episodes?|podcasts?|shows?|recordings?|replays?|talks?|shares?|speakers?|videos?|"
                r"livestreams?|streams?|broadcasts?|conversations?|panels?|workshops?|interviews?|sessions?|"
                r"segments?|stories|story|series|XQ\d{1,3}|ZX\d{1,3}Z)")
_PRE_EDITS: dict[tuple[str, str], list[tuple[re.Pattern, str]]] = {
    ("en", "es"): [
        (re.compile(r"\b([Cc])atch(?:ing)?\s+up\s+(on|with)\b"), r"get caught up \2"),
        (re.compile(r"\bCATCH\s+UP\s+(ON|WITH)\b"), r"GET CAUGHT UP \1"),
        (re.compile(r"\b([Cc])atch\s+(it|them)\s+(?=on|at|in)\b"), r"find \2 "),
        (re.compile(r"\b([Cc])atch(?=\s+(?:[\w'’]+\s+){0,3}?" + _MEDIA_NOUNS + r"\b)"), "\x01"),
    ],
}


_NOT_IMPERATIVE = {"can", "could", "will", "would", "may", "might", "should", "must", "to", "you", "we", "they", "i",
                   "he", "she", "also", "ll", "cannot", "can't", "won't", "and", "or"}


def _catch_media(m: re.Match) -> str:
    """'Catch the meeting …' → "Don't miss the meeting …" ("No te pierdas …"); after a modal or a
    subject ('you can catch every episode') → 'listen to' ("puedes escuchar …")."""
    prev = re.findall(r"[A-Za-z']+", m.string[:m.start()])[-1:]
    if prev and prev[0].lower().lstrip("'") in _NOT_IMPERATIVE:
        return "Listen to" if m.group(1) == "C" else "listen to"
    return "Don't miss" if m.group(1) == "C" else "don't miss"


def pre_edit(masked: str, src: str, tgt: str) -> str:
    out = masked
    for pat, repl in _PRE_EDITS.get((src, tgt), []):
        if repl == "\x01":
            out = pat.sub(_catch_media, out)
        else:
            out = pat.sub(lambda m, r=repl: (m.expand(r)[0].upper() + m.expand(r)[1:])
                          if m.group(0)[0].isupper() else m.expand(r), out)
    return out


def is_all_caps(masked: str) -> bool:
    words = _plain_words(masked)
    letters = "".join(words)
    return len(words) >= 2 and len(letters) >= 6 and letters.isupper()


def decase(masked: str) -> str:
    """'Crutches And Casts' → 'crutches and casts' (acronyms, placeholders, 'I', 'God' kept)."""
    caps = is_all_caps(masked)
    if not caps:
        masked = decase_caps_runs(masked)     # "… PHOTO CONTEST" inside a Title Case line

    def low(m: re.Match) -> str:
        w = m.group(0)
        if _ANY_PH.fullmatch(w) or w in _KEEP_CAPS:
            return w
        if not caps and w.isupper() and len(w) >= 2:
            return w                       # acronym (GV, PC, USA)
        if len(w) == 1 and w.isupper() and m.string[m.end():m.end() + 1] == ".":
            return w                       # an initial ("Dennis R.")
        return w.lower()
    return _WORD.sub(low, masked)


_CAPS_RUN = re.compile(r"\b[A-ZÁÉÍÓÚÑÜ]{3,}(?:[\s,&'’-]+(?:(?i:and|or|of|y|e|o|de)\s+)?[A-ZÁÉÍÓÚÑÜ]{2,})+\b")


def decase_caps_runs(masked: str) -> str:
    """'Send your photos! GRAPEVINE PHOTO CONTEST!' → '… photo contest!': ALL-CAPS runs inside
    ordinary text are left untranslated by the model unless lower-cased."""
    return _CAPS_RUN.sub(lambda m: m.group(0) if _ANY_PH.search(m.group(0)) else m.group(0).lower(), masked)


def defilename(text: str) -> str:
    """'Writing-Workshop-Guidelines' / 'GV_Catalog_2026' → words separated by spaces.
    Only for file-name-like strings (no spaces, no URL) — 'F-202 God-Talk' is left alone."""
    t = text.strip()
    if not t or " " in t or any(p.search(t) for p in _PROTECT_PATTERNS[1:4]):
        return text
    if "_" in t or t.count("-") >= 2:
        return re.sub(r"(?<=[^\W_])[-_]+(?=[^\W_])", " ", text)
    return text


def copied_words(src_masked: str, out: str, src_lang: str) -> int:
    """How many real source words were left untranslated in the output."""
    small = _SMALL.get(src_lang, set())
    caps = is_all_caps(src_masked)        # "ORDER FORM GIFT": every word counts, not just non-acronyms
    src_words = {w.lower() for w in _plain_words(src_masked)
                 if len(w) >= 3 and (caps or not w.isupper()) and w.lower() not in small}
    out_words = {w.lower() for w in _WORD.findall(out)}
    return sum(1 for w in src_words if w in out_words)


def recase_names(out: str, original_masked: str, reference: str | None = None) -> str:
    """After translating a lower-cased title, give copied names ('Marissa', 'Yukón') their capital
    back. `reference` = the translation of the title as written: a word it has in lower case is an
    ordinary word, not a name ('A Simple Meditation' → 'una meditación simple')."""
    names = {}
    # the first word is capitalized anyway; it is a name only if the reference has it capitalized
    # somewhere else than at its start ("Cecil's Vodcast" → "Vodcast de Cecil")
    ref_caps = {fold(m.group(0)).lower() for m in _WORD.finditer(reference or "")
                if m.start() > 0 and m.group(0)[0].isupper()}
    for i, w in enumerate(_plain_words(original_masked)):
        if (i > 0 or fold(w).lower().split("'")[0].split("’")[0] in ref_caps) and len(w) >= 3 and \
                w[0].isupper() and not w.isupper() and w.lower() not in _SMALL["en"] and w.lower() not in _SMALL["es"]:
            names[fold(re.split(r"['’]", w)[0]).lower()] = w
    if reference:
        lower_in_ref = {fold(w).lower() for w in _WORD.findall(reference) if w.islower()}
        names = {k: v for k, v in names.items() if k not in lower_in_ref}
    if not names:
        return out

    def fix(m: re.Match) -> str:
        w = m.group(0)
        if w.islower() and fold(w) in names:
            return w[0].upper() + w[1:]           # keep the model's spelling ("yukón" → "Yukón")
        return w
    return _WORD.sub(fix, out)


def _first_alpha(s: str) -> int:
    m = _LETTER.search(s)
    return m.start() if m else -1


def _first_word_letter(s: str) -> int:
    """Index of the first letter that STARTS a word ('30th Anniversary' → the 'A', not the 't')."""
    m = re.search(r"(?<![\w])[^\W\d_]", s)
    return m.start() if m else -1


# ---------------------------------------------------------------- Spanish ¿…? ¡…!
_CLOSERS = r"[\"'”’»)\]]*"
_ES_QWORDS = (r"(?:por\s+qué|para\s+qué|a\s+qué|de\s+qué|en\s+qué|con\s+qué|qué|cómo|cuándo|dónde|adónde|"
              r"quién|quiénes|cuál|cuáles|cuánto|cuánta|cuántos|cuántas)")
_ES_QWORD_START = re.compile(_ES_QWORDS + r"\b", re.I)
_CLAUSE_QUESTION = re.compile(r"[,;:]\s+(?=" + _ES_QWORDS + r"\b)", re.I)     # "Si no puedes, ¿por qué no …?"
_RUNON_OUT = re.compile(r"(?<=\d)\s+(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+\s+[a-záéíóúñü])")   # "… 238047 Miércoles no es …?"


def _mark_position(sent: str) -> int | None:
    """Where ¿ / ¡ goes in a sentence: at the start of the question itself."""
    m = re.search(r"[^\W_]", sent)
    if not m or re.search(r"https?://|@", sent[:m.start()]):
        return None
    k = m.start()
    if not _ES_QWORD_START.match(sent, k):
        clause = list(_CLAUSE_QUESTION.finditer(sent))
        if clause:
            return clause[-1].end()
    runon = list(_RUNON_OUT.finditer(sent, k))
    return runon[-1].end() if runon else k


def _pair_marks(sent: str) -> str:
    for opener, closer in (("¿", "?"), ("¡", "!")):
        end = re.search(r"([.?!…]*)(" + _CLOSERS + r")\s*$", sent)
        term = end.group(1) if end else ""
        if closer in term and opener not in sent:
            k = _mark_position(sent)
            if k is not None:
                sent = sent[:k] + opener + sent[k:]
        elif opener in sent and closer not in sent[sent.rindex(opener):] and term in (".", ""):
            # the model opened a question and closed it with "." (or not at all)
            core = sent[:end.start()] if end else sent
            sent = core + closer + sent[end.start(2):] if end else sent + closer
    return sent


def spanish_marks(text: str) -> str:
    """¿…? and ¡…! paired per SENTENCE: a sentence ending in ?/! opens with ¿/¡ (at the start of
    the question: "Si no puedes, ¿por qué …?"), one the model opened with ¿/¡ gets its ?/!."""
    return "".join(_pair_marks(sent) + ws for sent, ws in split_sentences(text))


# ---------------------------------------------------------------- vulgar "coger"
# "Catch the meeting …" came out as "Coge la reunión …": "coger" is vulgar in Mexico and most of
# Latin America (this site's Spanish-speaking audience). Every form of it is rewritten as
# "agarrar" (neutral everywhere), or "tomar" in front of a bus/train/plane ("tomar el autobús").
_COGER_ENDINGS = {
    # stem cog-
    "er": "ar", "e": "a", "es": "as", "emos": "amos", "éis": "áis", "en": "an", "ed": "ad",
    "í": "é", "iste": "aste", "ió": "ó", "imos": "amos", "isteis": "asteis", "ieron": "aron",
    "ía": "aba", "ías": "abas", "íamos": "ábamos", "íais": "abais", "ían": "aban",
    "erá": "ará", "eré": "aré", "erás": "arás", "eremos": "aremos", "eréis": "aréis", "erán": "arán",
    "ería": "aría", "erías": "arías", "eríamos": "aríamos", "eríais": "aríais", "erían": "arían",
    "iera": "ara", "ieras": "aras", "iéramos": "áramos", "ierais": "arais", "ieran": "aran",
    "iese": "ase", "ieses": "ases", "iésemos": "ásemos", "iesen": "asen",
    "ido": "ado", "ida": "ada", "idos": "ados", "idas": "adas", "iendo": "ando", "iéndo": "ándo",
}
_COJER_ENDINGS = {"o": "o", "a": "e", "as": "es", "amos": "emos", "áis": "éis", "an": "en"}   # stem coj-
_ENCLITIC = r"(?:(?:me|te|se|nos|os)?(?:lo|la|los|las|le|les)|me|te|se|nos|os)"
_COGER = re.compile(
    r"(?i)(?<![\w-])(c[oó])(?:g(" + "|".join(sorted(_COGER_ENDINGS, key=len, reverse=True)) + r")"
    r"|j(" + "|".join(sorted(_COJER_ENDINGS, key=len, reverse=True)) + r"))(" + _ENCLITIC + r")?(?![\w-])")
_TRANSPORT_ES = re.compile(r"(?i)^\s+(?:\w+\s+){0,2}?(?:autob[uú]s|bus|cami[oó]n|tren|avi[oó]n|taxi|metro|"
                           r"vuelo|ferry|barco|transporte|colectivo|micro)\b")
# "coja/cojo" are also the adjective "lame": only rewrite them when the source had such a verb.
_CATCH_EN = re.compile(r"(?i)\b(?:catch\w*|caught|grab\w*|take|takes|took|taking|taken|get|gets|got|pick\w*|seiz\w*)\b")


# Word choices fixed AFTER the model (a glossary placeholder in their place made the model's grammar
# worse: "Don y Sam compartir consejos … a través del XQ1"): (source must match, output → fix).
_POST_EDITS_ES: list[tuple[re.Pattern, re.Pattern, re.Pattern | None, str]] = [
    # "the holidays" (Thanksgiving → New Year) are "las fiestas", not "las vacaciones"
    (re.compile(r"(?i)\bholidays?\b"), re.compile(r"\b([Vv])acaciones\b"), re.compile(r"(?i)\bvacation"), "fiestas"),
    # a group's "business meeting" is a "reunión de trabajo" (the words the site's own pages use)
    (re.compile(r"(?i)\bbusiness meetings?\b"), re.compile(r"\breunión de negocios\b", re.I), None,
     "reunión de trabajo"),
    (re.compile(r"(?i)\bbusiness meetings?\b"), re.compile(r"\breuniones de negocios\b", re.I), None,
     "reuniones de trabajo"),
    # a "speaker meeting" has speakers ("oradores"), not loudspeakers ("altavoces")
    (re.compile(r"(?i)\bspeakers?\b"), re.compile(r"\baltavoces\b", re.I), re.compile(r"(?i)\bloud ?speakers?\b"),
     "oradores"),
    # "in / out of / back to the rooms" (of AA) = the meetings, not bedrooms
    (re.compile(r"(?i)\bthe rooms\b"), re.compile(r"\blas habitaciones\b", re.I),
     re.compile(r"(?i)\b(?:hotel|bed|guest|hospital|motel|hallway|living)\s*rooms?\b"), "las reuniones"),
    # "experience, strength and hope": AA Spanish says "fortaleza" (also the site's own pages)
    (re.compile(r"(?i)\bstrength,? and hope\b"), re.compile(r"\bfuerza y esperanza\b", re.I), None,
     "fortaleza y esperanza"),
]
# "reuniones AA" / "miembro AA" → "reuniones de AA" / "miembro de AA" (done here, not with glossary
# terms: a masked term hides its gender from the model — "nuestro reuniones de AA")
_DE_AA = re.compile(r"\b((?:[Rr]euni(?:ón|ones))|(?:[Mm]iembros?))\s+(AA|A\.A\.)(?=$|[\s.,;:!?)»”’\"'])")
_DE_AA_SRC = re.compile(r"(?i)\b(?:AA|A\.A\.)\s+(?:meetings?|members?)\b")


def post_edit_es(text: str, src: str) -> str:
    for need, pat, veto, repl in _POST_EDITS_ES:
        if need.search(src or "") and not (veto and veto.search(src or "")):
            text = pat.sub(lambda m: _same_case(repl, m.group(0)), text)
    if _DE_AA_SRC.search(src or ""):
        text = _DE_AA.sub(r"\1 de \2", text)
    # "De el Foro …" → "Del Foro …", "a el grupo" → "al grupo" (not "de El Paso": a name)
    return re.sub(r"\b([Dd]e|[Aa]) el\b", lambda m: ("D" if m.group(1)[0] == "D" else "d") + "el"
                  if m.group(1).lower() == "de" else m.group(1) + "l", text)


def fix_vulgar_es(text: str, src: str = "") -> str:
    """'Coge la reunión' → 'Agarra la reunión', 'cogió el autobús' → 'tomó el autobús'."""
    if not re.search(r"(?i)\bc[oó][gj]", text):
        return text

    def repl(m: re.Match) -> str:
        stem, end_g, end_j, clitic = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        if end_j is not None and not _CATCH_EN.search(src or ""):
            return m.group(0)
        verb = "tom" if _TRANSPORT_ES.match(text[m.end():]) else "agarr"
        ending = _COGER_ENDINGS[end_g.lower()] if end_g is not None else _COJER_ENDINGS[end_j.lower()]
        if stem.lower() == "có":           # "cógelo" → "agárralo" (keep the written accent)
            verb = "tóm" if verb == "tom" else "agárr"
        word = verb + ending + clitic.lower()
        if m.group(0).isupper():
            return word.upper()
        return word[0].upper() + word[1:] if m.group(0)[0].isupper() else word
    out = _COGER.sub(repl, text)
    if out != text:
        log.debug("rewrote vulgar 'coger': %r -> %r", text[:80], out[:80])
    return out


# ---------------------------------------------------------------- Spanish sentence case
# Title Case is English; Spanish titles are written in sentence case. After translating a Title
# Case source the model often keeps the capitals ("Riendo Nuestro camino", "Aniversario Especial").
_ES_KEEP_CAPS = {"Dios", "Paso", "Pasos", "Tradición", "Tradiciones", "Concepto", "Conceptos", "Poder", "Superior",
                 "Libro", "Grande", "Comunidad", "Oración", "Serenidad", "Asamblea", "Área", "Distrito", "Comité",
                 "Convención", "Conferencia", "Junta", "Intergrupo"}
_ES_PROPER_MULTI = re.compile(r"\b(?:Estados Unidos|Nueva York|Nuevo México|Nueva Jersey|Nueva Zelanda|Nueva Orleans|"
                              r"Reino Unido|Puerto Rico|Costa Rica|El Salvador|América Latina|Carolina del (?:Norte|Sur)|"
                              r"Dakota del (?:Norte|Sur)|Virginia Occidental|Columbia Británica)\b")


def sentence_case_es(out: str, src_masked: str, case_hint=None) -> str:
    """Lower-case the capitals the model copied from an English Title Case source. Kept: the first
    word (and one after . ! ? : or an opening quote/bracket), placeholders, acronyms, AA words
    written with a capital ('Paso', 'Tradición', 'Dios'), words copied letter for letter from the
    source ('Marissa', 'Vancouver') and proper names by `case_hint(word)` → 'proper' | 'common' |
    None (the model's vocabulary: 'Vancouver' is only known capitalized, 'dimensión' lower-case)."""
    src_exact = {w.lower() for w in _plain_words(src_masked)}
    src_folded = {fold(w).lower() for w in _plain_words(src_masked)}
    proper = [m.span() for m in _ES_PROPER_MULTI.finditer(out)]

    def fix(m: re.Match) -> str:
        w, i = m.group(0), m.start()
        if not w[:1].isupper() or w.isupper() or _ANY_PH.fullmatch(w) or w in _ES_KEEP_CAPS or w in _KEEP_CAPS:
            return w
        hint = case_hint(w) if case_hint else None
        if any(a <= i < b for a, b in proper) or w.lower() in src_exact or hint == "proper" \
                or (fold(w).lower() in src_folded and hint != "common"):
            return w
        before = out[:i].rstrip()
        if not before or before[-1] in ".!?:¿¡\"“«([—–-|·":
            return w
        if len(w) == 1 or re.match(r"[A-ZÁÉÍÓÚÑ]\.", out[i:i + 2]):
            return w                       # an initial
        return w[0].lower() + w[1:]
    return _WORD.sub(fix, out)


# ---------------------------------------------------------------- English Title Case
# English titles are written in Title Case (Grapevine does), Spanish ones in sentence case: a title
# translated ES→EN gets Title Case ("Atados por la misma enfermedad" → "Bound by the Same Illness").
# AP/APA style: minor words (articles, conjunctions, prepositions of up to 3 letters) stay lower
# case unless first or last or after a colon/dash/bracket.
_EN_MINOR = set("a an the and but or nor for so yet as at by in of off on per to up via vs vs.".split())


def title_case_en(text: str) -> str:
    """'The emptiness behind the party' → 'The Emptiness Behind the Party'. Only for short, title-like
    text (≤ 14 words, no sentence inside); acronyms, names with inner capitals, codes, URLs,
    e-mails, @handles and #hashtags are left as written."""
    t = text or ""
    words = _WORD.findall(t)
    if not words or len(words) > 14 or re.search(r"[.!?…]\s+\S", t) or any(p.search(t) for p in _URLISH):
        return t
    spans = [m for m in re.finditer(r"[^\s]+", t)]
    out, last = [], len(spans) - 1
    for n, m in enumerate(spans):
        tok = m.group(0)
        prev = spans[n - 1].group(0) if n else ""
        lead = re.match(r"^[\"'“‘«(\[¿¡]*", tok).group(0)
        core = tok[len(lead):]
        forced = n == 0 or n == last or bool(lead) or prev.endswith((":", "—", "–", "-")) or prev in ("-", "–", "—", "|")

        def cap_part(p: str, first: bool) -> str:
            if not p or not p[0].isalpha() or not p[0].islower():
                return p                           # "30th", "iPhone"-style are handled below
            if any(c.isupper() for c in p[1:]) or re.search(r"\d", p):
                return p
            if not first and re.sub(r"[^\w.]", "", p).lower() in _EN_MINOR:
                return p
            return p[0].upper() + p[1:]
        if not core or core[:1] in "@#" or re.search(r"[A-Z]", core[1:]) or re.search(r"\d", core[:1]) \
                or (len(re.sub(r"\W", "", core)) == 1 and n > 0):     # "8.5 x 11" keeps its "x"
            out.append(tok)
            continue
        parts = re.split(r"([-/])", core)         # "self-support" → "Self-Support", "and/or"
        core = "".join(cap_part(p, forced and i == 0) if i % 2 == 0 else p for i, p in enumerate(parts))
        out.append(lead + core)
    res, pos = [], 0
    for m, new in zip(spans, out):
        res.append(t[pos:m.start()])
        res.append(new)
        pos = m.end()
    res.append(t[pos:])
    return "".join(res)


def postprocess(src: str, out: str, tgt: str) -> str:
    """Match terminal punctuation and first-letter case of the source; Spanish ¿ ¡ (per sentence);
    never the vulgar "coger"."""
    out = re.sub(r"[ \t]{2,}", " ", out.replace("⁇", " ")).strip()
    if not out:
        return out
    s = src.rstrip()
    src_term = re.search(r"([.!?…:;)\]])" + _CLOSERS + r"$", s)
    if out.endswith(".") and not out.endswith("..") and not src_term:
        out = out[:-1].rstrip()
    elif src_term and src_term.group(1) == "." and re.search(r"[^\W_]" + _CLOSERS + r"$", out) \
            and not re.search(r"(?:^|\s)[A-ZÁÉÍÓÚÑ]\.$|\.\." + _CLOSERS + "$", s):
        # "… about “Loneliness.”" came back as "… sobre “Soledad”" (period lost): Spanish puts the
        # period after the closing quote (“Soledad”.), American English inside (“Loneliness.”)
        if tgt == "en":
            close = re.search(_CLOSERS + r"$", out).group(0)
            out = out[:len(out) - len(close)] + "." + close
        else:
            out += "."
    if tgt == "es":
        out = spanish_marks(post_edit_es(fix_vulgar_es(out, src), src))
    elif tgt == "en":
        out = out.replace("¿", "").replace("¡", "")
    i, j = _first_word_letter(src), _first_word_letter(out)
    if i >= 0 and j >= 0 and src[i].isupper() and out[j].islower():
        out = out[:j] + out[j].upper() + out[j + 1:]
    return out


# =========================================================================== engine
class Engine:
    """Lazy CTranslate2 + SentencePiece loader (one translator per direction)."""

    def __init__(self, models_dir: Path | None = None, threads: int | None = None, download: bool = True):
        self.models_dir = Path(models_dir or MODELS_DIR)
        self.threads = threads or _threads()
        self.download = download
        self._loaded: dict[str, tuple | None] = {}

    def load(self, src: str, tgt: str):
        pair = PAIRS.get((src, tgt))
        if pair is None:
            return None
        if pair in self._loaded:
            return self._loaded[pair]
        res = None
        try:
            if ensure_models([pair], self.models_dir, self.download).get(pair):
                import ctranslate2
                import sentencepiece as spm
                d = model_dir(pair, self.models_dir)
                kw = dict(device="cpu", inter_threads=1, intra_threads=self.threads)
                try:
                    tr = ctranslate2.Translator(str(d / "model"), compute_type="int8", **kw)
                except ValueError:   # CPU without int8 support
                    tr = ctranslate2.Translator(str(d / "model"), compute_type="default", **kw)
                sp = spm.SentencePieceProcessor(model_file=str(d / "sentencepiece.model"))
                chars: set[str] = set()
                for i in range(sp.get_piece_size()):
                    if not (sp.is_unknown(i) or sp.is_control(i)):
                        chars.update(sp.id_to_piece(i).replace("▁", ""))
                res = (tr, sp, chars)
                log.info("loaded model %s (%d threads)", pair, self.threads)
        except Exception as e:
            log.error("translation model %s unavailable: %s", pair, e)
            res = None
        self._loaded[pair] = res
        return res

    def case_hint(self, src: str, tgt: str):
        """word → 'proper' (the vocabulary only knows it Capitalized: 'Vancouver'), 'common' (its
        lower-case form is the more frequent one: 'dimensión', 'regional') or None (unknown)."""
        loaded = self._loaded.get(PAIRS.get((src, tgt), ""))
        if not loaded:
            return None
        sp = loaded[1]
        unk = sp.unk_id()
        cache: dict[str, str | None] = {}

        def hint(word: str) -> str | None:
            if word not in cache:
                lo, cap = sp.piece_to_id("▁" + word.lower()), sp.piece_to_id("▁" + word[:1].upper() + word[1:].lower())
                if lo == unk and cap == unk:
                    cache[word] = None
                elif lo == unk:
                    cache[word] = "proper"
                elif cap == unk or sp.get_score(lo) >= sp.get_score(cap):
                    cache[word] = "common"
                else:
                    cache[word] = None
            return cache[word]
        return hint

    def run(self, texts: Sequence[str], src: str, tgt: str, **opts) -> list[tuple[str, float]]:
        loaded = self.load(src, tgt)
        if loaded is None or not texts:
            return [("", -99.0)] * len(texts)
        tr, sp, _ = loaded
        toks = [sp.encode(t, out_type=str) or ["."] for t in texts]
        res = tr.translate_batch(toks, beam_size=BEAM_SIZE, max_decoding_length=MAX_DECODING_LENGTH,
                                 max_batch_size=MAX_BATCH_SIZE, return_scores=True, **opts)
        return [(sp.decode(r.hypotheses[0]), float(r.scores[0]) if r.scores else 0.0) for r in res]


# =========================================================================== output guard
# "information-information-information…", "de la de la de la …": the same 1–4 words 3+ times.
_REPEAT = re.compile(r"(?i)\b([^\W\d_][\w'’]*(?:\s+[^\W\d_][\w'’]*){0,3})(?:[\s\-–—,;/]+\1\b){2,}")
_NUM_WORDS: dict[int, tuple[str, ...]] = {
    1: ("one", "first", "uno", "una", "un", "primer", "primero", "primera"),
    2: ("two", "second", "dos", "segundo", "segunda"), 3: ("three", "third", "tres", "tercer", "tercero", "tercera"),
    4: ("four", "fourth", "cuatro", "cuarto", "cuarta"), 5: ("five", "fifth", "cinco", "quinto", "quinta"),
    6: ("six", "sixth", "seis", "sexto", "sexta"), 7: ("seven", "seventh", "siete", "séptimo", "séptima"),
    8: ("eight", "eighth", "ocho", "octavo", "octava"), 9: ("nine", "ninth", "nueve", "noveno", "novena"),
    10: ("ten", "tenth", "diez", "décimo", "décima"), 11: ("eleven", "eleventh", "once", "undécimo"),
    12: ("twelve", "twelfth", "doce", "duodécimo", "duodécima"), 13: ("thirteen", "trece"),
    14: ("fourteen", "catorce"), 15: ("fifteen", "quince"), 16: ("sixteen", "dieciséis"),
    17: ("seventeen", "diecisiete"), 18: ("eighteen", "dieciocho"), 19: ("nineteen", "diecinueve"),
    20: ("twenty", "veinte"), 30: ("thirty", "treinta"), 40: ("forty", "cuarenta"), 50: ("fifty", "cincuenta"),
    100: ("hundred", "cien", "ciento"),
}
_ORDINAL_DIGITS = re.compile(r"(?i)(\d+)(?:st|nd|rd|th|\.?[ºª°]|er|ra|ro|do|da|to|ta|vo|va|no|na|mo|ma)(?![^\W\d_])")


_HTML_ENTITY = re.compile(r"&(?:[a-zA-Z]{2,8}|#\d{2,5}|#x[0-9a-fA-F]{2,4});")


def _nums(s: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d{1,9}", s)]


def output_problem(src: str, out: str) -> str | None:
    """Why a translation must NOT be used (None = fine): empty, a repeated-word loop, more than
    2.5× longer than the source, or numbers that were dropped, changed or invented."""
    if not out or not out.strip():
        return "empty output"
    ent = _HTML_ENTITY.search(out)
    if ent and not _HTML_ENTITY.search(src):
        return f"HTML entity {ent.group(0)}"
    if len(out) > MAX_GROWTH * len(src) + 8:
        return f"{len(out) / max(len(src), 1):.1f}x longer"
    loop = _REPEAT.search(out)
    if loop and not _REPEAT.search(src):
        return f"repeats '{loop.group(1)}'"
    have, got = Counter(_nums(src)), Counter(_nums(out))
    spelled_ok = Counter(int(m.group(1)) for m in _ORDINAL_DIGITS.finditer(src))   # "9th" → "noveno"
    missing = [n for n, c in (have - got).items() if c > spelled_ok.get(n, 0)]
    low = fold(src).lower()
    extra = [n for n in (got - have) if not any(re.search(rf"\b{fold(w)}\b", low) for w in _NUM_WORDS.get(n, ()))]
    if missing or extra:
        return f"numbers changed (missing {sorted(missing)}, new {sorted(extra)})"
    return None


def _lost_names(src: str, out: str) -> bool:
    """True if a "Victor E."-style name of the source is not in the translation letter for letter."""
    return any(m.group(0) not in out for m in _NAME_INITIAL.finditer(src))


def _roundtrip_ok(word: str, back: str) -> bool:
    """'loneliness' → 'soledad' → 'loneliness' ✓; 'gripevine' → 'gripe' → 'flu' ✗."""
    a = fold(word).lower()
    return any(w == a or (len(a) >= 5 and w[:5] == a[:5]) for w in re.findall(r"[^\W\d_]+", fold(back).lower()))


@dataclass
class _Job:
    seg: str
    masked: Masked
    inputs: list[str]                 # [as written, lower-cased (titles only)]
    direct: str | None = None         # result that needs no model at all
    result: str | None = None
    why: str | None = None            # why a model output was rejected by the guard


# =========================================================================== translator
TResult = tuple  # (translated text or None if unavailable, machine_translated: bool)


class Translator:
    """Cache + overrides + glossary + model. Create one per run and call save() at the end."""

    def __init__(self, *, cache: bool = True, cache_path: Path = CACHE_PATH, glossary_path: Path = GLOSSARY_PATH,
                 overrides_path: Path = OVERRIDES_PATH, models_dir: Path | None = None, download: bool = True,
                 budget_seconds: float | None = None, threads: int | None = None, use_model: bool = True):
        self.use_model = use_model      # False → only cache/overrides/glossary (no model run)
        self.glossary = Glossary.load(glossary_path)
        self.overrides = Overrides.load(overrides_path)
        self.cache = TranslationCache(cache_path, enabled=cache)
        self.cache.sync_glossary(self.glossary)
        self.cache.sync_overrides(self.overrides)
        self.cache.reapply("en>es", lambda s, t: post_edit_es(t, s))
        self.engine = Engine(models_dir, threads, download)
        self.protector = Protector(self.glossary)
        self.deadline = (time.monotonic() + budget_seconds) if budget_seconds else None
        self.stats = {"requested": 0, "cache_hits": 0, "overrides": 0, "passthrough": 0, "translated": 0,
                      "failed": 0, "segments": 0, "model_inputs": 0, "fallbacks": 0, "rejected": 0,
                      "model_seconds": 0.0}
        # segments whose model output failed the guard and kept their original text: (why, source, output)
        self.rejected: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------ public
    def available(self, src: str, tgt: str) -> bool:
        if not self.use_model:
            return False
        loaded = self.engine.load(src, tgt)
        if loaded is not None:
            self.protector.set_known_chars(src, loaded[2])
        return loaded is not None

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def translate(self, texts: Sequence[str], src: str, tgt: str, chunk: int = 48) -> list[TResult]:
        """Translate plain texts. Returns [(text|None, machine)] — None = could not translate now
        (model missing or time budget used up); callers fall back to the original."""
        results: list[TResult | None] = [None] * len(texts)
        todo: dict[str, list[int]] = {}
        for i, raw in enumerate(texts):
            self.stats["requested"] += 1
            text = unicodedata.normalize("NFC", raw or "")
            quick = self._quick(text, src, tgt)
            if quick is not None:
                results[i] = quick
            else:
                todo.setdefault(text, []).append(i)
        if todo:
            pending = list(todo)
            done: dict[str, TResult] = {}
            if not self.available(src, tgt):
                done = {t: (None, False) for t in pending}
            else:
                for start in range(0, len(pending), chunk):
                    batch = pending[start:start + chunk]
                    if self.out_of_time():
                        done.update({t: (None, False) for t in batch})
                        continue
                    for t, o in zip(batch, self._translate_new(batch, src, tgt)):
                        if o is None:
                            done[t] = (None, False)
                        else:
                            self.cache.put(src, tgt, t, o)
                            done[t] = (o, o != t)
            for t, idxs in todo.items():
                r = done[t]
                self.stats["translated" if r[0] is not None else "failed"] += len(idxs)
                for i in idxs:
                    results[i] = r
        return results  # type: ignore[return-value]

    def translate_markdown(self, md: str, src: str, tgt: str) -> TResult:
        """Translate Markdown safely: link targets, images, code, URLs stay intact; link labels and
        **emphasis** are translated separately; headings/bullets/quotes keep their markers."""
        md = unicodedata.normalize("NFC", md or "")
        quick = self._quick(md, src, tgt)
        if quick is not None:
            return quick
        if self.out_of_time() or not self.available(src, tgt):
            return (None, False)
        marks: dict[str, tuple[str, object]] = {}

        def mark(kind: str, payload: object) -> str:
            key = f"\x00{len(marks) + 1}\x00"
            marks[key] = (kind, payload)
            return key

        plan: list[tuple[str, str, bool]] = []      # (prefix, content, translate?)
        in_code = False
        for line in md.split("\n"):
            st = line.strip()
            if st.startswith(("```", "~~~")):
                in_code = not in_code
                plan.append((line, "", False))
                continue
            if in_code or not st or st.startswith("<") or re.match(r"^\s*\|?\s*:?-{3,}", line) \
                    or re.match(r"^(?: {4}|\t)", line) or re.fullmatch(r"[-*_]{3,}", st):
                plan.append((line, "", False))
                continue
            m = re.match(r"^(\s*(?:#{1,6}\s+|>\s?)*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d{1,3}[.)]\s+)?)", line)
            prefix, content = m.group(1), line[m.end():]
            content = re.sub(r"`[^`\n]+`", lambda x: mark("keep", x.group(0)), content)
            content = re.sub(r"!\[[^\]]*\]\([^)]*\)", lambda x: mark("keep", x.group(0)), content)
            content = re.sub(r"<(?:https?://|mailto:)[^>]+>", lambda x: mark("keep", x.group(0)), content)
            content = re.sub(r"\[([^\]\n]+)\]\((\S+?)(\s+\"[^\"]*\")?\)",
                             lambda x: mark("link", (x.group(1), x.group(2) + (x.group(3) or ""))), content)
            content = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", lambda x: mark("em", (x.group(1), x.group(2))), content)
            content = re.sub(r"(?<![\w*])(\*|_)(?=[^\s*_])(.+?)(?<=[^\s*_])\1(?![\w*])",
                             lambda x: mark("em", (x.group(1), x.group(2))), content)
            content = re.sub(r"\|", lambda x: mark("keep", "|"), content) if st.startswith("|") else content
            plan.append((prefix, content, True))

        inner_keys = [k for k, (kind, _) in marks.items() if kind in ("link", "em")]
        inner_src = [marks[k][1][0] if marks[k][0] == "link" else marks[k][1][1] for k in inner_keys]  # type: ignore[index]
        outer_src = [c for _, c, t in plan if t]
        inner_out = self._translate_new(inner_src, src, tgt) if inner_src else []
        if any(o is None for o in inner_out):
            return (None, False)
        inner = dict(zip(inner_keys, inner_out))
        self.protector.marker_labels = {k: v for k, v in inner.items() if marks[k][0] == "link"}
        try:
            outer_out = self._translate_new(outer_src, src, tgt) if outer_src else []
        finally:
            self.protector.marker_labels = {}
        if any(o is None for o in outer_out):
            return (None, False)
        outer = iter(outer_out)

        def render(s: str, depth: int = 0) -> str:
            if depth > 4:
                return s.replace("\x00", "")

            def one(x: re.Match) -> str:
                kind, payload = marks.get(x.group(0), ("keep", ""))
                if kind == "keep":
                    return str(payload)
                if kind == "link":
                    return f"[{render(inner[x.group(0)], depth + 1)}]({payload[1]})"  # type: ignore[index]
                return f"{payload[0]}{render(inner[x.group(0)], depth + 1)}{payload[0]}"  # type: ignore[index]
            return _MD_MARK.sub(one, s)

        lines = [(p + render(next(outer))) if t else p for p, c, t in plan]
        out = "\n".join(lines)
        self.cache.put(src, tgt, md, out)
        self.stats["translated"] += 1
        return (out, out != md)

    def save(self, prune_unused: bool = False) -> None:
        self.cache.save(prune_unused=prune_unused)

    def summary(self) -> dict:
        s = dict(self.stats)
        s["model_seconds"] = round(s["model_seconds"], 1)
        s["segments_per_second"] = round(s["segments"] / s["model_seconds"], 1) if s["model_seconds"] else None
        s["cached"] = len(self.cache)
        s["engine"] = ENGINE_VERSION
        return s

    # ------------------------------------------------------------------ internals
    def _quick(self, text: str, src: str, tgt: str) -> TResult | None:
        """Answers that need no model: same language, nothing to translate, override, cache."""
        if src == tgt or (src, tgt) not in PAIRS or not needs_translation(text):
            self.stats["passthrough"] += 1
            return (text, False)
        ov = self.overrides.get(text, tgt)
        if ov is not None:
            self.stats["overrides"] += 1
            return (ov, False)
        cached = self.cache.get(src, tgt, text)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return (cached, cached != text)
        if self._already_in(text, src, tgt):
            self.stats["passthrough"] += 1
            return (text, False)
        return None

    def _already_in(self, text: str, src: str, tgt: str) -> bool:
        """True when the text is clearly written in the target language already (bilingual
        captions, mislabeled items). Names/URLs are masked first so "La Viña" is neutral."""
        masked = _ANY_PH.sub(" ", self.protector.mask(text, src, tgt).text)
        return looks_like(masked, tgt) and not looks_like(masked, src)

    def _translate_new(self, texts: Sequence[str], src: str, tgt: str) -> list[str | None]:
        # canonical "[Season 5, Episode 10]" first, so "[Season 5. Episode 10]" is not cut in two
        plans = [self._units(fix_season_episode(t, src), tgt) for t in texts]
        segs = list(dict.fromkeys(p for plan in plans for flag, p in plan if flag))
        seg_out = self._translate_segments(segs, src, tgt)
        out: list[str | None] = []
        for plan in plans:
            parts = []
            for flag, piece in plan:
                if not flag:
                    parts.append(piece)
                    continue
                o = seg_out.get(piece)
                if o is None:
                    parts = None
                    break
                parts.append(o)
            out.append("".join(parts) if parts is not None else None)
        return out

    def _units(self, text: str, tgt: str) -> list[tuple[bool, str]]:
        """units(), except that a one-line title with an override is kept in one piece (before its
        " [Season …]" / " (Spanish)" tail): otherwise it is cut at " — " / " | " first and the
        override for the whole title ("Widening the Doorway — The Plain Language Big Book") never
        matches."""
        if "\n" not in text:
            tb = _TRAILING_BRACKET.match(text)
            head, gap, tail = (tb.group(1), tb.group(2), tb.group(3)) if tb else (text, "", "")
            if head == head.strip() and head + gap + tail == text and self.overrides.get(head, tgt) is not None:
                return [(True, head)] + ([(False, gap)] if gap else []) + (units(tail) if tail else [])
        return units(text)

    def _plan(self, seg: str, src: str, tgt: str, style: int, names: bool = True) -> _Job:
        m = self.protector.mask(defilename(seg), src, tgt, style, names=names)
        # the model answers "&" with "&quot;"/"&amp;" (HTML in its training data): say "and"/"y"
        as_masked = m.text
        m.text = re.sub(r"(?<=\S)\s+&\s+(?=\S)", " and " if src == "en" else " y ", m.text)
        m.text = pre_edit(m.text, src, tgt)
        base = m.text
        rest = [w for w in _plain_words(base) if len(w) >= 2]
        if not rest or all(w.isupper() and len(w) <= 4 for w in rest):
            # nothing for the model: keep "&" as written ("GRAPEVINE & LA VIÑA" is not "… y …" in
            # English), but a lone conjunction between names is translated ("Grapevine y La Viña")
            direct = _translate_conjunctions(as_masked, src, tgt)
            restored = Protector.restore(direct, m, tgt) or seg
            return _Job(seg, m, [], direct=postprocess(seg, restored, tgt))
        inputs = [base]
        # Capitalized words look like names to the model, which then copies them ("Loneliness"
        # stayed English) → titles and single words are also translated lower-cased.
        if is_title_case(base) or is_all_caps(base) or is_single_word(base):
            low = decase(base)
        else:
            low = decase_caps_runs(base)
        if low != base:
            inputs.append(low)
        return _Job(seg, m, inputs)

    def _quoted_labels(self, segs: list[str], src: str, tgt: str) -> dict[str, str]:
        """Translate the short quoted phrases of these segments on their own (see _QUOTED)."""
        inners: list[str] = []
        for seg in segs:
            for m in _QUOTED.finditer(seg):
                if needs_translation(m.group(2)) and m.group(2) not in inners:
                    inners.append(m.group(2))
        if not inners:
            return {}
        done = self._translate_segments(inners, src, tgt, quotes=False)
        return {s: o for s, o in done.items() if o and o != s}

    def _translate_segments(self, segs: list[str], src: str, tgt: str, quotes: bool = True) -> dict[str, str | None]:
        labels = self._quoted_labels(segs, src, tgt) if quotes else {}
        prev = self.protector.quote_labels
        self.protector.quote_labels = labels
        try:
            return self._translate_segments_masked(segs, src, tgt)
        finally:
            self.protector.quote_labels = prev

    def _translate_segments_masked(self, segs: list[str], src: str, tgt: str) -> dict[str, str | None]:
        res: dict[str, str | None] = {}
        jobs: list[_Job] = []
        for seg in segs:
            self.stats["segments"] += 1
            ov = self.overrides.get(seg, tgt)
            if ov is not None:
                res[seg] = ov
            elif self._already_in(seg, src, tgt):
                res[seg] = seg
            else:
                # names ("Kathy R.") stay visible to the model at first: it needs them for gender
                job = self._plan(seg, src, tgt, 0, names=False)
                if job.direct is not None:
                    res[seg] = job.direct
                else:
                    jobs.append(job)
        self._run_jobs(jobs, src, tgt)
        # … but a segment with such a name is also translated with the names protected, and that
        # version wins when the model changed a name ("Ginger S." → "jengibre S.", "Victor E." →
        # "vencedor E.") or left clearly more English untranslated.
        named = [j for j in jobs if j.result is not None and _NAME_INITIAL.search(j.seg)]
        if named:
            jobs_n = [self._plan(j.seg, src, tgt, 0) for j in named]
            self._run_jobs([j for j in jobs_n if j.direct is None], src, tgt)
            for j, jn in zip(named, jobs_n):
                r = jn.direct if jn.direct is not None else jn.result
                if r is None or r == j.result:
                    continue
                if _lost_names(j.seg, j.result) or \
                        copied_words(j.seg, r, src) + 2 <= copied_words(j.seg, j.result, src):
                    j.result = r
        retry = [j for j in jobs if j.result is None]
        if retry:   # placeholders lost or output rejected → try the other placeholder style
            jobs2 = [self._plan(j.seg, src, tgt, 1) for j in retry]
            self._run_jobs([j for j in jobs2 if j.direct is None], src, tgt)
            last = []
            for j, j2 in zip(retry, jobs2):
                j.result = j2.direct if j2.direct is not None else j2.result
                if j.result is None:
                    last.append(j)
                    j.why = j.why or j2.why
            if last:    # last resort: plain model output without protection — still guarded
                self.stats["fallbacks"] += len(last)
                log.debug("placeholder fallback for %d segment(s): %s", len(last), [j.seg[:60] for j in last])
                for j, (o, _) in zip(last, self._mt([j.seg for j in last], src, tgt)):
                    out = postprocess(j.seg, o, tgt) if o else ""
                    why = output_problem(j.seg, out) or self._lost_protected(j.seg, out)
                    if why:
                        self._reject(j.seg, out, j.why or why)
                        out = j.seg          # never ship garbage: keep this sentence as written
                    j.result = out
        for j in jobs:
            res[j.seg] = j.result
        return res

    @staticmethod
    def _lost_protected(seg: str, out: str) -> str | None:
        """Unprotected fallback output must still contain every URL / e-mail it had."""
        for p in _URLISH:
            for m in p.finditer(seg):
                if m.group(0) not in out:
                    return f"lost '{m.group(0)[:40]}'"
        return None

    def _reject(self, seg: str, out: str, why: str) -> None:
        self.stats["rejected"] += 1
        if len(self.rejected) < 200:
            self.rejected.append((why, seg, out))
        if self.stats["rejected"] <= 40:
            log.info("guard: kept the original of %r (%s; model said %r)", seg[:90], why, (out or "")[:90])

    def _run_jobs(self, jobs: list[_Job], src: str, tgt: str) -> None:
        flat = [(ji, vi, inp) for ji, j in enumerate(jobs) for vi, inp in enumerate(j.inputs)]
        if not flat:
            return
        outs = self._mt([x[2] for x in flat], src, tgt)
        as_written = {ji: o for (ji, vi, _inp), (o, _s) in zip(flat, outs) if vi == 0 and o}
        lowered = {ji: recase_names(o, jobs[ji].inputs[0], as_written.get(ji))
                   for (ji, vi, _inp), (o, _s) in zip(flat, outs) if vi == 1 and o}
        hint = self.engine.case_hint(src, tgt) if tgt == "es" else None
        cands: dict[int, list[tuple]] = {}
        for (ji, vi, _inp), (o, score) in zip(flat, outs):
            j = jobs[ji]
            if not o:
                continue
            if vi == 1:
                o = lowered[ji]
            elif tgt == "es" and is_title_case(j.inputs[0]) and not is_all_caps(j.inputs[0]):
                o = sentence_case_es(o, j.inputs[0], hint)     # "Riendo Nuestro camino" → "… nuestro …"
            restored = Protector.restore(o, j.masked, tgt)
            if restored is None:
                continue
            final = postprocess(j.seg, restored, tgt)
            why = output_problem(j.seg, final)
            if why:                      # loop / blow-up / changed numbers → not a candidate
                j.why = j.why or f"{why}: {final[:80]!r}"
                continue
            copied = copied_words(j.inputs[-1], o, src)
            # the lower-cased variant must beat the as-written one by a small score margin
            cands.setdefault(ji, []).append((copied, -(score - (0.02 if vi else 0.0)), vi, final, o))
        best = {ji: min(c) for ji, c in cands.items()}
        # A single word the model only translated lower-cased must be a real word, not a name or
        # a pun ("Gripevine" → "gripe" = flu!): its translation must translate back to it.
        check = [ji for ji, b in best.items() if b[2] == 1 and is_single_word(jobs[ji].inputs[0])
                 and not _ANY_PH.search(b[4])]
        if check and self.engine.load(tgt, src) is not None:
            backs = self._mt([best[ji][4] for ji in check], tgt, src)
            for ji, (back, _s) in zip(check, backs):
                if not _roundtrip_ok(_plain_words(jobs[ji].inputs[0])[0], back):
                    as_written = [c for c in cands[ji] if c[2] == 0]
                    best[ji] = min(as_written) if as_written else best[ji]
                    if as_written:
                        log.debug("single word %r: kept as written (%r came back as %r)",
                                  jobs[ji].seg, cands[ji], back)
        for ji, b in best.items():
            jobs[ji].result = b[3]

    def _mt(self, inputs: list[str], src: str, tgt: str) -> list[tuple[str, float]]:
        t0 = time.monotonic()
        outs = self.engine.run(inputs, src, tgt)
        # rare degenerate output ("de la de la de la …") → decode again with a repetition penalty
        bad = [i for i, (o, _) in enumerate(outs)
               if (_REPEAT.search(o) and not _REPEAT.search(inputs[i])) or len(o) > MAX_GROWTH * len(inputs[i]) + 8]
        if bad:
            again = self.engine.run([inputs[i] for i in bad], src, tgt, repetition_penalty=1.3, no_repeat_ngram_size=3)
            for i, r in zip(bad, again):
                outs[i] = r
        self.stats["model_inputs"] += len(inputs) + len(bad)
        self.stats["model_seconds"] += time.monotonic() - t0
        return outs


# =========================================================================== module API
_DEFAULT: Translator | None = None
_NEUTRAL: Protector | None = None


def detect_language(text: str, prior: str | None = None) -> str:
    """common.detect_lang() after masking names/URLs from the glossary KEEP list, so that
    "Come visit the Grapevine & La Viña table" is English, not Spanish."""
    global _NEUTRAL
    if _NEUTRAL is None:
        _NEUTRAL = Protector(Glossary.load())
    try:
        masked = _NEUTRAL.mask(text or "", "en", "es").text
        # names, URLs and glossary phrases become placeholders, which are then dropped
        neutral = _ANY_PH.sub(" ", masked)
    except Exception:
        neutral = text or ""
    return detect_lang(neutral, prior)


def get_translator(**kw) -> Translator:
    """Shared Translator (keyword arguments only apply when it is first created)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Translator(**kw)
    return _DEFAULT


def translate_texts(texts: Sequence[str], src: str, tgt: str) -> list[str]:
    """Plain list in, plain list out. Falls back to the original text when translation is unavailable."""
    res = get_translator().translate(list(texts), src, tgt)
    return [r[0] if r[0] is not None else (orig or "") for r, orig in zip(res, texts)]


def translate_markdown(md: str, src: str, tgt: str) -> str:
    r = get_translator().translate_markdown(md, src, tgt)
    return r[0] if r[0] is not None else (md or "")


# =========================================================================== CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.sync.translate",
                                 description="Offline English ⇄ Spanish translation (Argos/OPUS-MT via CTranslate2).")
    ap.add_argument("text", nargs="*", help="text(s) to translate")
    ap.add_argument("--to", choices=LANGS, help="target language (default: the other one)")
    ap.add_argument("--from", dest="src", choices=LANGS, help="source language (default: detected)")
    ap.add_argument("--file", help="translate the contents of this file")
    ap.add_argument("--markdown", action="store_true", help="treat input as Markdown")
    ap.add_argument("--no-cache", action="store_true", help="ignore the cache (fresh translation)")
    ap.add_argument("--save", action="store_true", help="store new translations in the cache")
    ap.add_argument("--download", action="store_true", help="download/verify the models and exit")
    ap.add_argument("--stats", action="store_true", help="show cache statistics and exit")
    ap.add_argument("--models-dir", help="model folder (default GV_MODELS_DIR or .cache/models)")
    a = ap.parse_args(argv)

    models_dir = Path(a.models_dir) if a.models_dir else None
    if a.download:
        res = ensure_models(models_dir=models_dir)
        for pair, ok in res.items():
            print(f"{pair}: {'ready' if ok else 'MISSING'}  ({model_dir(pair, models_dir)})")
        return 0 if all(res.values()) else 1
    if a.stats:
        c = TranslationCache(CACHE_PATH)
        by = {}
        for e in c.entries.values():
            by[e.get("d")] = by.get(e.get("d"), 0) + 1
        print(json.dumps({"cached": len(c), "by_direction": by, "engine": ENGINE_VERSION,
                          "current_engine_entries": sum(1 for e in c.entries.values() if e.get("v") == ENGINE_VERSION)},
                         indent=1))
        return 0

    texts = list(a.text)
    if a.file:
        texts.append(Path(a.file).read_text(encoding="utf-8"))
    if not texts and not sys.stdin.isatty():
        texts.append(sys.stdin.read())
    if not texts:
        ap.print_help()
        return 2
    tr = Translator(cache=not a.no_cache, models_dir=models_dir)
    for text in texts:
        src = a.src or (detect_lang(text) if detect_lang(text) in LANGS else ("es" if a.to == "en" else "en"))
        tgt = a.to or ("en" if src == "es" else "es")
        t0 = time.monotonic()
        out, machine = tr.translate_markdown(text, src, tgt) if a.markdown else tr.translate([text], src, tgt)[0]
        ms = (time.monotonic() - t0) * 1000
        print(f"[{src}→{tgt}{'' if machine else ', unchanged/override'}, {ms:.0f} ms] {out if out is not None else '(translation unavailable)'}")
    if a.save:
        tr.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
