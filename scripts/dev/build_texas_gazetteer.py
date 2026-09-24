"""(Re)build data/geo/texas_places.json — every Texas place → its county/counties.

    python -m scripts.dev.build_texas_gazetteer            # download the Census table, build, clean up
    python -m scripts.dev.build_texas_gazetteer --keep     # keep the downloaded table (in .tmp/, git-ignored)

Source: U.S. Census Bureau, 2020 "place by county" reference table for Texas (public domain):
    https://www2.census.gov/geo/docs/reference/codes2020/place_by_cou/st48_tx_place_by_county2020.txt
Pipe-delimited: STATE|STATEFP|COUNTYFP|COUNTYNAME|PLACEFP|PLACENS|PLACENAME|TYPE|CLASSFP|FUNCSTAT.
It lists every incorporated place (city / town / village) and census-designated place (CDP); a place
that spans several counties has one row per county.

Output (small, one place per line so git diffs stay readable):
    {"abbott": ["Hill"], …, "dallas": ["Collin", "Dallas", "Denton", "Kaufman", "Rockwall"], …}
Keys are scripts.sync.geo.normalize_place(name without its Census type word): lower case, no accents,
no apostrophes, punctuation → spaces, "St."/"Ft."/"Mt." → "saint"/"fort"/"mount". Values are county
names without " County", sorted A–Z. Two different places with the same name share one key (their
counties are merged) — e.g. "St. Paul" (Collin) and "St. Paul" (San Patricio).

The Census table only changes after a decennial census; re-run this script then (or if the file is
lost). The table is downloaded into .tmp/ (git-ignored, never published) and deleted afterwards —
also when the build fails — unless --keep is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sync.geo import GAZETTEER_PATH, normalize_place  # noqa: E402

CENSUS_URL = ("https://www2.census.gov/geo/docs/reference/codes2020/place_by_cou/"
              "st48_tx_place_by_county2020.txt")
STAGED = ROOT / ".tmp" / "census_tx_place_by_county2020.txt"   # .tmp/ is git-ignored
TYPE_WORDS = ("city", "town", "village", "CDP")
EXPECTED_HEADER = ["STATE", "STATEFP", "COUNTYFP", "COUNTYNAME", "PLACEFP", "PLACENS", "PLACENAME", "TYPE",
                   "CLASSFP", "FUNCSTAT"]


def download(dest: Path) -> None:
    import requests
    r = requests.get(CENSUS_URL, timeout=60, headers={"User-Agent": "NETA65-GrapevineCommitteeBot/2.0"})
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)


def parse(text: str) -> dict[str, list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0].split("|")
    if header[:len(EXPECTED_HEADER)] != EXPECTED_HEADER:
        raise ValueError(f"unexpected Census header: {header}")
    col = {h: i for i, h in enumerate(header)}
    places: dict[str, set[str]] = defaultdict(set)
    for ln in lines[1:]:
        f = ln.split("|")
        if len(f) < len(EXPECTED_HEADER) or f[col["STATE"]] != "TX":
            continue
        name = f[col["PLACENAME"]].strip()
        words = name.rsplit(" ", 1)
        if len(words) == 2 and words[1] in TYPE_WORDS:       # "Texas City city" → "Texas City"
            name = words[0]
        county = f[col["COUNTYNAME"]].strip()
        if county.endswith(" County"):
            county = county[:-len(" County")]
        key = normalize_place(name)
        if key and county:
            places[key].add(county)
    return {k: sorted(v) for k, v in sorted(places.items())}


def write(places: dict[str, list[str]], path: Path) -> int:
    body = ",\n".join(f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                      for k, v in places.items())
    text = "{\n" + body + "\n}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return len(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", action="store_true", help="keep the downloaded Census table")
    ap.add_argument("--source", help="use this local copy of the Census table instead of downloading")
    a = ap.parse_args(argv)
    src = Path(a.source) if a.source else STAGED
    try:
        if not src.exists():
            print(f"downloading {CENSUS_URL}")
            download(src)
        places = parse(src.read_text(encoding="utf-8"))
        size = write(places, GAZETTEER_PATH)
        counties = {c for v in places.values() for c in v}
        multi = sum(1 for v in places.values() if len(v) > 1)
        print(f"wrote {GAZETTEER_PATH.relative_to(ROOT)}: {len(places)} places, {len(counties)} counties, "
              f"{multi} places in 2+ counties, {size / 1024:.1f} KB")
        if len(counties) != 254:
            print(f"WARNING: expected 254 Texas counties, found {len(counties)}")
    finally:
        if not a.keep and src == STAGED and src.exists():
            src.unlink(missing_ok=True)
            print(f"removed {src.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
