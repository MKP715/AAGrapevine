# data/geo — Texas place → county lookup

`texas_places.json` lets the site tell whether a magazine writer's town ("Grand Prairie, Texas")
is inside Area 65. It maps every Texas city, town, village and census-designated place to the
county or counties it lies in:

```json
"grand prairie":["Dallas","Ellis","Tarrant"],
"paris":["Lamar"],
```

* **Source:** U.S. Census Bureau, 2020 place-by-county reference table for Texas (public domain):
  <https://www2.census.gov/geo/docs/reference/codes2020/place_by_cou/st48_tx_place_by_county2020.txt>
* **Keys** are normalized names (lower case, no accents or apostrophes, "St."/"Ft."/"Mt." written
  out as "saint"/"fort"/"mount", no "city"/"town"/"CDP" suffix) — see `normalize_place()` in
  `scripts/sync/geo.py`. **Values** are county names without "County".
* **Which counties are Area 65** is NOT in this file — it is the `spotlight.neta65_counties` list
  in `config/site.yml`, so the committee can correct it without touching this data.
* **Rebuild** (only needed after a new census, or if the file is lost):
  `python -m scripts.dev.build_texas_gazetteer` — it downloads the Census table, writes the JSON
  (about 46 KB) and deletes the download again.

Used by `scripts/sync/geo.py` → `build_data.py` → `data/site/spotlight.json` (the "Published
writers" spotlight on the home page).
