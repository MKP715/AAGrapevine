# Data contract (sync pipeline ⇄ site templates)

```
 GitHub Action (daily)                                     Eleventy build
 ─────────────────────                                     ──────────────
 scripts/sync/<source>.py ──► data/raw/<source>.json ─┐
                                                      ├─► scripts/sync/build_data.py ──► data/site/<file>.json ──► src/_data/db.js
 scripts/sync/translate.py ◄── (all raw titles) ──────┘         (adds i18n, whatsnew, status)                  (templates: db.<file>)
        └──► data/translations/cache.json  (+ overrides.yml wins)
```

* `data/raw/*.json` — written ONLY by the matching sync module. Cumulative: items are
  never dropped just because a source hiccuped; they get `last_seen` updates and may be
  marked `"status": "gone"` when confirmed deleted.
* `data/site/*.json` — written ONLY by `build_data.py`. Templates read ONLY these.
* All timestamps are ISO-8601 strings. Dates without time: `YYYY-MM-DD`.
  Datetimes: UTC `YYYY-MM-DDTHH:MM:SSZ`.

## 1. Raw file envelope — `data/raw/<source>.json`

```json
{
  "source": "youtube",
  "updated": "2026-09-23T10:17:00Z",   // last successful run of this module
  "attempted": "2026-09-23T10:17:00Z", // last run, successful or not
  "first_harvest": "2026-09-01T10:17:00Z", // first successful read of this source; never moves
  "ok": true,                          // false if this run failed (items kept from before)
  "error": null,                       // short message when ok=false
  "stats": { "fetched": 15, "new": 2 },// free-form counters shown on /status/
  "items": [ Item, ... ]
}
```

`first_harvest` is written by `common.save_raw()` (seeded from the oldest `first_seen` when an older
file has none). `build_data.py` uses it to tell the launch-day back catalog from real news (see
*is_new / What's New* in §3); the oldest `first_seen` cannot do that because it moves forward when a
source drops old items.

Source file names: `drive.json`, `youtube.json`, `podcasts.json`, `instagram.json`,
`articles.json`, `pdfs.json`, `events_external.json`, `editorial.json`,
`weekly_open.json`, `announcements.json`, `shop.json`, `meetings.json`, `quote.json`, `audio_project.json`.

## 2. Item (common shape for every piece of content)

```json
{
  "id": "yt:dQw4w9WgXcQ",          // globally unique, prefixed: yt: pod: ig: gv: lv: pdf: drive: ann: ev:
  "source": "youtube",             // youtube | podcast | instagram | grapevine | lavina | crawl | drive | committee | calendar
  "kind": "video",                 // video | episode | post | article | pdf | photo | document | slides | form | announcement | event | video_file
  "url": "https://...",            // where the button/link goes (external canonical)
  "title": "Original title",
  "summary": "Plain-text teaser, ≤ 400 chars, may be empty",
  "lang": "en",                    // en | es | fr | und   (language of title/summary)
  "date": "2026-09-16T14:00:00Z",  // best-known PUBLISH date (or YYYY-MM-DD); null if unknown
  "first_seen": "2026-09-23T10:17:00Z",
  "last_seen": "2026-09-23T10:17:00Z",
  "image": "https://... or /assets/... or null",
  "tags": ["season-11"],
  "category": "gvr",               // optional grouping key (see per-source below)
  "status": "ok",                  // ok | gone
  "extra": { }                     // per-source fields below
}
```

### Per-source `extra` and `category`

| source | kind | category values | extra |
|---|---|---|---|
| podcast | episode | show key (`gv` AA Grapevine's Podcast, `wo` Grapevine Weekly Open AA Meeting — `sources.podcasts[].key`) | `audio_url`, `duration_sec`, `season`, `episode`, `show`, `show_name`, `link` |
| youtube | video | `gv` / `lv` (by language) | `video_id`, `channel_id`, `duration_sec`, `playlists` [names] , `is_short` |
| grapevine / lavina | audio_project | `audio_project` | items `audio:gv` / `audio:lv` (scripts/sync/audio_project.py): see *audio_project.json* in §3 |
| quote | quote | — | `pub` (gv/lv), `text`, `attribution`, `source`, `source_lang`, `signup_url`, `date_label`, `date_from_heading`, `block` (teaser/embed/anchor), `node` — items `quote:<pub>:<date>` (scripts/sync/quote.py): see *quote.json* in §3 |
| instagram | post | account key `gv` / `lv` | `shortcode`, `account`, `username`, `media_type` (image/video/carousel), `thumb` (local path or null), `embed_url` |
| grapevine / lavina | article | publication `gv` / `lv` | `publication`, `issue_label` ("October 2026" / "Septiembre / Octubre 2026"), `issue_key` ("2026-10" / "2026-09"), `topic`, `section`, `author`, `free` (bool\|null) |
| crawl | pdf | `gvr` `rlv` `catalog` `flyer` `postcard` `news` `guidelines` `order-form` `workbook` `service` `literature` `other` | `host`, `file_url`, `size_bytes`, `pages`, `thumb` (site-relative path or null), `referrers` [{`url`,`title`}], `upload_month` ("2026-02"), `link_texts` [..] |
| drive | document / slides / photo / video_file / form | `reports` `notes` `slides` `flyers` `photos` `workshops` `announcements` `forms` `other` | `file_id`, `mime`, `panel` (77), `panel_label`, `path` ["photos","WhatsApp"], `album` (sub-folder name or null), `view_url`, `preview_url`, `download_url`, `thumb_url`, `image_url`, `is_image`, `is_video`, `is_pdf` |
| committee / drive / calendar | event | `committee` `recurring` `flyer` `gv-calendar` `lv-calendar` `manual` `ics` `neta65` | `start` (ISO datetime or date), `end` (for an all-day event: the LAST day, inclusive), `all_day`, `location`, `online_url`, `flyer_url`, `flyer_thumb`, `city`, `state`, `tentative` (present, `true`, only on an event whose details are not final) (+ `recurring`, `series`, `rule`, `recurrence_label` — see §5; manual: `own_i18n`; .ics feeds: `feed`, `uid`) |
| committee / drive | announcement | `manual` / `drive` | `body_md` (original-language Markdown), `expires` (date|null), `pinned` (bool); content/announcements: `own_i18n` (below) |

`editorial.json` items (kind `topic`): `extra` = `publication`, `issue_label`, `deadline` (date\|null), `theme`.
`extra.own_i18n` (content/events and content/announcements files only, when the header has them):
the author's own words in the other language — `title_es` / `summary_es` (or `title_en` / `summary_en`
for a file written in Spanish) → `{"title": {"es": …}, "summary": {"es": …}, "body_md": {"es": …}}`
(the summary is also that language's `body_md`, the text the pages show). build_data puts them in
`i18n` instead of a machine translation; only a language or field left out is machine-translated
(→ `machine`). Events may also have `{"location": {"es": …}}` from `location_es` (`location_en` in a
Spanish file): the place in the other language — a place is never machine-translated.
`extra.tentative` (content/events `tentative: true` / `yes` / `sí`; an .ics feed's `STATUS:TENTATIVE`):
the details are not final — the pages show "Details to be confirmed" / "Detalles por confirmar" and the
calendar feeds write `STATUS:TENTATIVE` (every other event `STATUS:CONFIRMED`). Absent otherwise.
`weekly_open.json`: items of kind `meeting` with `extra` = `zoom_id`, `passcode`, `day`, `time`, `url`
(+ structured `weekday`, `start_local`, `timezone`, `next_start` — see §5):
1. id `weekly_open` — the **Grapevine Weekly Open** (Wednesdays), read from aagrapevine.org/grapevine-weekly-open.
   Always the FIRST item of `data/site/weekly_open.json`: templates read `db.weekly_open.items[0]`.
2. id `weekly_open_lv` — **La Viña's weekly open meeting** in Spanish (Thursdays, from Nov 5, 2026), written
   from `config/site.yml` → `lavina_weekly_open` (an official La Viña flyer; there is no web page to read), on
   every run — also when the Grapevine page cannot be read. `lang` "es", `source` "lavina", `url` "" until
   La Viña publishes a page (config `url`). Same `extra` fields as the Grapevine item (no `player_url`,
   `sentence`) plus `starts` (first meeting, "2026-11-05") and `source_note`; `next_start` is never before
   `starts`. Title and summary are the config's own words in both languages (`extra.own_i18n` → `i18n.title`,
   `i18n.summary`, never machine-translated); `i18n.day/time/time_central/when/sentence` are written by rules as
   for the Grapevine item ("Thursdays at 11:00 AM Central" / "Jueves a las 11:00 a. m. (hora del Centro)").
   Delete the config block or set `enabled: false` to take it off the site. A `starts` date that is not on
   the configured weekday is replaced by the first real meeting (with a warning in the log). The title has no
   "New": the /monthly/ poster adds a "New" badge in the first month only.
The raw file may list them in another order (save_raw sorts by date); build_data puts Grapevine first.
Every module adds more `extra` fields than listed here; §5 lists all of them as built.

## 3. Site files — `data/site/*.json` (what templates read)

Every item in a site file = raw Item (**minus** `last_seen`, which only the sync modules use;
items with `status: "gone"` are left out) **plus**:

```json
"i18n": {
  "title":   { "en": "…", "es": "…" },
  "summary": { "en": "…", "es": "…" },
  "body_md": { "en": "…", "es": "…" },         // announcements + manual events (render with | md)
  "location": { "en": "…", "es": "…" }         // events only, and only when the place differs by language:
                                               // content/events location_es / location_en, or a place not known
                                               // yet ("Venue to be announced" → "Lugar por anunciarse")
  // more per kind — see §5 (section, topic, issue_label, album, day, time, when …)
},
"machine": ["es"],     // which languages were machine-translated (show a small "auto-translated" note)
"is_new": true         // see "is_new / What's New" below
```

`i18n.<field>.<original lang>` is always the original text; the other language is the
translation, or the original again when no translation exists (yet). Fields written by rules
(issue labels, Weekly Open day/time, committee meetings) never mark `machine`.

**is_new / What's New.** The "news date" of an item is its publish `date`; for a future-dated item
(next month's magazine issue) or an undated one it is the day the item was first found — but
only if that was more than 2 days after the source's *first* harvest (so launch day is not a
flood of the back catalog). Undated PDFs are never news by themselves (the crawler already dates
PDFs that appear on a page it knew). `is_new` = news date within 14 days. Never new: kind
`topic` (editorial themes; their date is a deadline), kind `meeting` (Weekly Open), committee
meetings, recurring events (category `recurring`, from `recurring_events:` in the config) and **back-catalog magazine stories** (articles of an issue that was never the current
issue on a magazine hub while we watched — the archive backfill found them; their issue is not in
the raw `issues` map). Back-catalog stories are real and appear on /read/ and in the spotlight, but
never in What's New. `whatsnew.json` = the newest 150 by news date (`wn_date`), same exclusions; events
appear there for 30 days after they were first announced; several Drive photos of one album on
one day become one group item (`extra.is_group`, `count`, `thumbs`). Committee meetings and recurring
events never appear in What's New (they come round every month; `SCHEDULED_EVENT_CATEGORIES`).

Files: `episodes.json`, `videos.json`, `instagram.json`, `articles.json`, `pdfs.json`,
`drive.json`, `events.json` (committee meetings auto-generated for 12 months + recurring events from
`recurring_events:` in the config + Drive flyers + TX calendar events + manual + the optional
`sources.ics_feeds` calendars — each real event ONCE, see *Events from several places* in §5), `announcements.json`, `editorial.json`, `weekly_open.json`,
`whatsnew.json` (newest 150 across all sources, sorted by date desc),
`spotlight.json` (published-writers spotlight, below), `status.json` (below),
`shop.json` (official store data, below — no `items`), `meetings.json` (Grapevine meetings, below),
`quote.json` (the Grapevine / La Viña daily quote, below), `audio_project.json` (the magazines' story lines, below).

Each site file is `{ "updated": "...", "fixture": false, <extra top-level keys>, "items": [...] }`,
items sorted newest first (events: soonest first). Extra top-level keys: `instagram.profiles`,
`videos.playlists`, `episodes.shows`, `articles.issues` (§5). `drive.json` never contains a Google
Form whose `extra.form_closed` is `true`. Output is deterministic: a re-run with the same raw data
changes only `status.json` (and `updated` stamps).

### pdfs.json — the Library (official documents, each once)

`build_data.py` passes the crawler's documents through `scripts/sync/pdf_curate.py`, so every consumer
(/library/, search, What's New, the digest, the home counters, /gvr/ and /shop/) sees the same list:

1. **Official sources only.** A document is kept only when its *file* is on a host listed in
   `config/site.yml` → `library.official_hosts` (default `aagrapevine.org`, `aalavina.org`, `aa.org`,
   `aaws.widen.net`; subdomains such as `www.` included). Local event flyers that a Grapevine event page
   links on other sites are left out (logged with their hosts), and the crawler no longer records them
   (`crawl_rules.pdf_url_from_href`, `OFFICIAL_DOC_HOSTS`).
2. **The same file twice** — the same address (the two magazine sites share one files directory; case and
   %-encoding ignored), or the same `size_bytes` + `pages` and the same title / the same file name apart
   from Drupal's `_0` suffix / an identical first-page thumbnail. Copies in the same document language:
   one stays (a live one; the magazine whose language is the document's; a real title over a file name;
   the newest upload; a rep-kit copy) and takes over the others' `referrers` (max 5) and kits;
   `extra.duplicates` lists the other addresses. Copies filed under different languages by the two sites
   (a bilingual file, e.g. the joint catalog) become one entry with `extra.same_file: true` (rule 4).
3. **Superseded versions** — the same original title, document language, category and type (e.g. a 2019
   "YouTube Channel" postcard and its 2026 replacement): only the newest stays. The title is compared without
   a leading publication name ("Grapevine", "AA Grapevine", "AAGV" — not "La Viña"), a trailing edition code
   ("EE", "Rev 2", "v2") and language markers: the 2013 "Copyright and Reprints Policy" gives way to the 2024
   "Grapevine Copyright and Reprints Policy", the 2023 price-increase release "(English)" to its "EE"
   re-issue. The publication does not count (the two sites share one files directory).
4. **Language editions** — an English and a Spanish (French…) edition of one document (a compatible type
   and a different `doc_lang`, plus one of: the same English title once "(English)" / "(Spa.)" markers and
   edition codes are removed — word order and small words ignored — and dates within 45 days, file names
   that differ only by the language word, or the GVR-kit / RLV-kit counterparts with the same type and page
   count; or, when the titles differ, file names that differ only by the language word AND dates within 45
   days — a French flyer whose French title was never translated —, or dates within 45 days + the same page
   count + a "found on" page in common + one title's words inside the other's once publication names are
   dropped, the title and every link text tried — "Privacy Policy" ⊆ "Privacy and Security Policy") become
   ONE item (chains join: FR ↔ ES ↔ EN): the English edition (else the Spanish one) is the item and
   `extra.versions` lists every edition. A French title the language detector took for English
   (`lang: "en"`, `doc_lang: "fr"`) is not used as the English title:

```json
"extra": { "versions": [
  { "lang": "en", "id": "pdf:98c45b8072b5", "url": "https://www.aagrapevine.org/…/Audio_download_GV_2026.pdf.pdf",
    "title": "Audio Downloads", "title_lang": "en", "i18n_title": { "en": "…", "es": "…" }, "machine": ["es"],
    "source": "gv", "date": "2026-01-08", "first_seen": "…", "category": "gvr", "tags": ["postcard"], "is_new": false,
    "host": "…", "file_url": "…", "filename": "…", "size_bytes": 97595, "pages": 1, "thumb": "…",
    "upload_month": "2026-01", "referrers": [ … ] },
  { "lang": "es", "id": "pdf:12db76ab9b4b", "url": "https://www.aalavina.org/…/Descarga_de_audios_2026.pdf", … }
], "kits": ["gvr", "rlv"] }
```

   `lang` = the edition's document language; order en, es, fr. The item's `i18n.title.<lang>` is the
   title of that language's edition (its original title when written in that language), language markers
   and edition codes removed; `machine` / `is_new` follow. `extra.kits` = every rep kit the document is in when that is more
   than its own `category` says. The Library shows one card per item: the edition in the page language
   (else the item's own), with links to every edition ("English · Español"; none for `same_file`), and the
   language filter counts every edition's language. /gvr/ and /shop/ (eleventy/filters/read.js
   `pdfEditions`) list each edition in its own kit. Every collapse is logged by the build.

### spotlight.json — published writers (home page + /published/)
Grapevine and La Viña stories published in the last 60/90 days, with where each writer is from.
Settings: `config/site.yml` → `spotlight:` (`home_days`, `list_days`, `default_scope`,
`neta65_counties`).
```json
{
  "updated": "2026-09-23T23:40:00Z", "fixture": false,
  "today": "2026-09-23",              // build day (America/Chicago) the windows were counted from
  "home_days": 60,                     // home page window
  "list_days": [60, 90],               // windows offered on the list page (first = default)
  "default_scope": "neta65",           // list page default: neta65 | texas | all
  "counts": {                          // stories per window and scope; "texas" INCLUDES neta65
    "60": { "neta65": 2, "texas": 8, "all": 100 },
    "90": { "neta65": 4, "texas": 15, "all": 156 }
  },
  "items": [ Article, … ]
}
```
* `items` = every **story with a byline** (`extra.author` or `extra.author_location`; "In Every Issue"
  departments such as Letter from the Editor / Dear Grapevine / Cartas del lector are left out) whose
  `extra.pub_date` lies within the longest window (`today − max(list_days)` … `today`, inclusive),
  from **any** place — so the page can offer an "everyone" view.
* Sorted: scope (`neta65`, `texas`, `other`, `unknown`), then `extra.pub_date` newest first, then title.
* Each item is the complete article item of `articles.json` (same fields, `i18n`, `machine`, `is_new`)
  — including `extra.geo` and `extra.pub_date`. To show a window of N days:
  `extra.pub_date >= today − N days`; for "Texas": scope `neta65` or `texas`.
* With no Area 65 writer in the window, `counts[..].neta65` is 0 and the page must say so gracefully.

### shop.json — Book of the Month, bulk discounts, subscription prices
Read daily from the official stores by `scripts/sync/shop.py` (URLs: `config/site.yml` → `sources.grapevine` /
`sources.lavina` → `botm`, `subscriptions`, `subscription_regions`; ~12 page requests a day through the shared
polite session, + a product page per subscription type once a month, + each product image once), then
`build_data.py`. Grapevine and La Viña are published by AA Grapevine, Inc.: every `url` is an official store
page — purchases always happen there. **ONE canonical home** for these prices and dates: templates never
hard-code a price, percent or date; other pages show at most a compact teaser that links to the shop page.
```json
{
  "updated": "2026-09-24T15:43:29Z", "fixture": false,
  "botm": [                                   // 0–2 entries, Grapevine first
    { "id": "botm:gv", "pub": "gv", "lang": "en",
      "title": "No Matter What: Dealing With Adversity in Sobriety",
      "url": "https://www.aagrapevine.org/store/no-matter-what-dealing-adversity-sobriety",   // buy here
      "page_url": "https://www.aagrapevine.org/BOTM",                                         // the offer page
      "image": "/assets/cache/shop/657477448ef71903.webp",    // ≤480 px WebP (cover), or null
      "price": 14.99, "sale_price": 11.99, "discount_pct": 20, "currency": "USD", "sku": "GV31",
      "starts": "2026-09-15", "ends": "2026-10-14",           // either may be null (dates not readable)
      "month_label": "October",                               // in the item's language ("Octubre" for LV)
      "blurb": "All recovering alcoholics have had to deal with adversity …",
      "i18n": { "title": {"en","es"}, "blurb": {"en","es"}, "month_label": {"en","es"} },
      "machine": ["es"] } ],                                  // languages machine-translated (title/blurb)
  "bulk_discounts": {                         // physical books only (see note); per-book discount in USD
    "source_url": "https://www.aagrapevine.org/store/no-matter-what-dealing-adversity-sobriety",
    "tiers": [ {"min":1,"max":4,"off":0}, {"min":5,"max":9,"off":0.5}, {"min":10,"max":19,"off":1.0},
               {"min":20,"max":29,"off":2.0}, {"min":30,"max":null,"off":3.0} ],
    "note": { "en": "These discounts apply exclusively to physical books. …", "es": "Esta oferta se aplica …" } },
  "subscriptions": [                          // one entry per publication × region that has plans; gv first, us/ca/intl
    { "pub": "gv", "region": "us", "url": "https://www.aagrapevine.org/store/us-subscriptions",
      "plans": [                              // the store's own order
        { "type": "print", "term_months": 12, "title": "Grapevine Print Subscriptions: 1-Year",
          "price": 36.0, "currency": "USD", "sku": "GVUS1",
          "url": "https://www.aagrapevine.org/store/grapevine-print-subscriptions-1-year",
          "image": "/assets/cache/shop/b863047b363d0518.webp",
          "volume": [ {"min":2,"max":19,"price":35.5}, {"min":20,"max":39,"price":35.0}, {"min":40,"max":null,"price":34.0} ] } ] } ],
  "types": {                                  // short official descriptions (first feature of a product page); may be {}
    "gv": { "print": {"en","es"}, "digital": {"en","es"}, "complete": {"en","es"} }, "lv": { … } }
}
```
* `type` is `print` | `digital` | `complete` | `other`, from the title (English or Spanish: "Print",
  "impresa", "Digital", "Complete"/"completa"); `term_months` 1 / 12 / 24 / 36 (or null) from "1-Month",
  "2-Years", "1 año", "3 años". `volume` is [] when the store shows no volume pricing (digital/complete).
  Plan titles are the store's own (Spanish for La Viña) — label plans from `type` + `term_months` in the page's
  language. Prices are in USD as the stores list them ("subject to Canadian or International conversion rates").
* `sale_price` = round(`price` × (1 − `discount_pct`/100), 2); the percent is read from the offer page.
* An offer whose `ends` is before today (site time zone) is left out even if the official page still shows it;
  a page with no offer gives no entry. Years are inferred around the sync date ("SEPT. 15 thru OCt. 14"): the
  latest window that has already started (or starts within 31 days), so an old offer left on the page reads as
  past, never as next year's. An offer line with one date ("through October 14") gives `starts: null`.
* The Book of the Month page must look like one (the content block + "Book of the Month" / "Libro del mes" in
  its title or heading): an unrelated 200 page (maintenance, a redirect home) is an error (previous offer kept,
  `ok: false`), never "no offer today".
* `currency` is read from the price ("CA$ 30" → "CAD", else "USD"); `types_checked` is stamped only when every
  publication's type descriptions were read (else the next run tries again). The print plans' card text is always
  the site's own (`shop.desc_print_*`): the store's sentence counts one year's copies.
* A book is always shown under the title it is sold under (`title`, in `lang`) — on /shop/, the home teaser,
  the posters and the digest (web, text, e-mail); `i18n.title` of the other language is only a small subtitle.
* `month_label` i18n is written by rule (never machine-translated); `title`/`blurb`/`types` texts are
  machine-translated into the other language (cached, like every other text).
* A part that cannot be fetched or parsed (GV offer, LV offer, GV subscriptions, LV subscriptions — or one
  region) keeps its previous data; the raw envelope gets `ok: false` with the reason, so /status/ shows
  "Book of the Month & subscription prices" as failing and the 7-day "not updating" report catches it.
* Without data (never synced) the file is `{updated: null, botm: [], bulk_discounts: {source_url: null,
  tiers: []}, subscriptions: [], types: {}}` — pages must show a graceful "see the official store" state.

### meetings.json — Grapevine meetings in our Area and nearby (the Meetings page)
Read once a day by `scripts/sync/meetings.py` from the public "12 Step Meeting List" lists of the offices in
`config/site.yml` → `meetings.feeds` (the same eight lists the Rowlett Group's meetings.html combines: Dallas,
Fort Worth, Tyler, the Spanish-speaking Dallas office, District 71 — and next to our Area the Arkansas Central
Office, OKC Intergroup and NWTA 66), then `build_data.py` (`meetings.build_site`). Only meetings whose types
include `meetings.type` ("GR") and that are not inactive. **ONE canonical home**: other pages link to the
Meetings page (at most a compact teaser); each meeting links to its page on the office's own site, which has
the joining details (Zoom links, phones and contacts are never copied).
```json
{
  "updated": "2026-09-24T22:38:35Z", "fixture": false, "type": "GR",
  "sources": [                                   // every office in config order (no keys, no feed addresses)
    { "id": "aadallas", "name": "Dallas Intergroup", "url": "https://www.aadallas.org", "in_area": true,
      "area_label": { "en": "Dallas area", "es": "Zona de Dallas" },   // config region_label
      "ok": true, "updated": "2026-09-24T22:37:36Z", "count": 5, "error": null } ],   // ok null = never read
  "groups": [                                    // our Area first, then nearby regions (config order); only groups with meetings
    { "id": "neta65", "in_area": true, "label": { "en": "Our Area (NETA 65)", "es": "Nuestra Área (NETA 65)" }, "count": 13 },
    { "id": "okcintergroup", "in_area": false, "label": { "en": "Oklahoma City area", "es": "Zona de Oklahoma City" }, "count": 8 } ],
  "items": [                                     // sorted by day, then time, then name
    { "id": "mtg:24f82f418540", "kind": "meeting", "name": "Richardson Group",
      "day": 3,                                  // 0 = Sunday … 6 = Saturday
      "time": "20:00", "end_time": null,         // local (Central) time as the office gives it; end may be null
      "location": "1144 N Plano Road, Suite 246 (Bus Route Access)",   // the place's own name; null when it only repeats the name/address
      "address": "1144 N Plano Rd, Richardson, TX 75081", "city": "Richardson", "county": "Dallas", "state": "TX",
      "lat": 32.961562, "lng": -96.699295, "approximate": false,       // approximate = the office only gives a city
      "region": "Richardson", "district": null,  // the office's own region / district names (may be null)
      "types": ["C", "D", "GR"],                 // codes → type_labels
      "attendance": "in_person",                 // in_person | online | hybrid
      "lang": "en",                              // "es" for type S or a Spanish-speaking office
      "url": "https://www.aadallas.org/meetings/richardson-group-15/", // the meeting's page on the office's site
      "sources": ["aadallas"],                   // every office that lists it (config order) — shown ONCE
      "directions_url": "https://www.google.com/maps/dir/?api=1&destination=32.961562,-96.699295",   // null: online / approximate
      "in_area": true,                           // city in an Area 65 county (spotlight.neta65_counties)
      "nearby": null,                            // out of our Area: { "id": office id, "label": {en, es} } = its group
      "i18n": {} } ],                            // meeting names are proper names: never translated
  "type_labels": { "C": { "en": "Closed", "es": "Cerrada" }, "GR": { "en": "Grapevine", "es": "Grapevine" } }  // codes in use only
}
```
* `in_area`: the city of the address is matched to its county with `scripts/sync/geo.py`
  (`data/geo/texas_places.json`; the office's region name when the city is not in the gazetteer — "Brazos Bend",
  region "Granbury" → Hood). A Texas city that cannot be matched counts as ours only when its office says
  `in_area: true`. Everything else is "nearby", grouped under its office's `region_label`.
* The same meeting in two lists (same day + time + street address — "Road"/"Rd", suite numbers ignored — or
  ≤ 60 m apart) is ONE item; `sources` names both offices and the id does not depend on which one answered.
  Two records of ONE office with their own meeting pages are never merged (two groups in two rooms of one
  club). `id` = hash of day | time | street address — else the meeting's own page (online meetings), else the
  name; two meetings of one office at one address and time also add their page, so ids are unique.
* `location` = the place's own name ("Serenity Club"); none when it only repeats the meeting's name or the
  address, and a place written as the street again plus a detail keeps only the detail
  ("1144 N Plano Road, Suite 246 (Bus Route Access)" → "Suite 246 (Bus Route Access)" — build_site applies
  this again, so older raw data is fixed without a new sync). A place name with a phone number or an e-mail
  address is dropped, and such a part of a meeting name is cut off.
* How each office is read (`methods`, in order): `feed` = the JSON list (`…/admin-ajax.php?action=meetings`);
  `page` = its public meeting-list page filtered to GR (classic page: `var locations` + table; "TSML UI" page:
  its public `tsml-cache-….json`). robots.txt is obeyed (tyler-aa.org: page only).
* Keys (Dallas, Fort Worth), tried in this order (`key_candidates`): env `TSML_KEY_AADALLAS` /
  `TSML_KEY_FORTWORTHAA` (GitHub secrets, optional) → `feed_obf` in config/site.yml (the full address with
  its key, reversed + base64 exactly like RowlettAA's meetings.html — obfuscation, not encryption;
  regenerate with `python -m scripts.sync.meetings --obfuscate "<address>"`) → RowlettAA's meetings.html
  (`key_source`, constant `key_const`, read only when needed). A key the office refuses (HTTP 401 / 403 →
  `KeyRejected`) moves on to the next source; `feeds[].key_from` names the one that worked and
  `stats.warnings` the refused one. A key is only sent to its own office's host: the keyed request follows
  redirects by hand, on that host only and when robots.txt allows (otherwise the list is not read), and
  robots.txt is checked against the full address with its query. A key is never written in plain text: not
  in data/raw, data/site, status.json or the logs (`redact()` on every address and message).
* An office that cannot be read keeps its previous meetings (`sources[].ok: false`, a note in the run
  summary); the source fails on /status/ only when no list at all could be read. An empty full list (feed or
  TSML UI cache) and a classic page with a meeting table but no `var locations` count as "cannot be read".
* `build_site` follows the settings at once (no new sync needed): `meetings.enabled: false` → the empty file;
  meetings of an office that is switched off (`enabled: false`) or removed are left out.
* Without data the file is `{updated: null, fixture: false, type: "GR", sources: [], groups: [], items: [],
  type_labels: {}}` — the page must show a graceful "see the intergroup websites" state.
* Where it shows: `/meetings/#grapevine-meetings` (`cmGvMeetings` → `gvMeetings()` in
  `eleventy/filters/committee.js`; each meeting's anchor is `#mtg-<hash>`). Elsewhere only pointers: one line
  on the home page (counts) and the site search — one `meeting` entry per group and place (`meeting:<id>`,
  a group that meets several times a week is one result), linking to its row.

### quote.json — the daily quote (home page)
Grapevine's "Daily Quote" and La Viña's "Cita Diaria", read by `scripts/sync/quote.py` from the block
`#quote-of-the-day` of each magazine's home page (`sources.<grapevine|lavina>.quote_page`, default `/`): one
request per site, in the full daily run (10:17 UTC) and again in the quick 12:07 UTC run (always after 6 AM
Texas time, when the new quote is out) — none when an earlier module of the same run already read that home page
(the shared session's page memo). `build_data.py` → `quote.build_site()`; nothing is translated.
```json
{ "updated": "2026-09-25T12:09:40Z", "fixture": false,
  "items": [                                   // the newest quote of each publication: Grapevine, then La Viña
    { "id": "quote:gv:2026-09-25", "pub": "gv", "lang": "en",
      "date": "2026-09-25",                    // the quote's day (Central): from the heading, else the day it was read
      "date_label": "September 25",            // the same day in the quote's language ("25 de septiembre")
      "heading": "Grapevine Daily Quote September 25",
      "text": "During his first AA years …",   // as published: whitespace and the outer quotation marks cleaned
      "attribution": "AA Co-Founder, Bill W., September 1945",
      "source": "“’Rules’ Dangerous but Unity Vital”, The Language of the Heart",   // after "From:" / "De"
      "source_lang": "en",                     // a Spanish quote can come from an English book
      "url": "https://www.aagrapevine.org/#quote-of-the-day",
      "signup_url": "https://visitor.r20.constantcontact.com/…" } ] }  // the publication's own e-mail sign-up
```
* The raw file also keeps `history` (the last 14 days per publication — see *Raw envelope extras*): it is only
  the guard that keeps a newer quote when a page shows an older one, so it is not copied into the site file.
* A page that cannot be fetched or read keeps that publication's previous quote (item + history); the raw
  envelope gets `ok: false` with the reason ("Daily quote" on /status/). The sign-up link is the teaser's
  own link field; the embed near the top of both pages links Grapevine's list, so a fallback only takes a
  link whose text names the publication's quote ("Sign up … Daily Quote" / "Regístrate … Cita").
* Where it shows: the home page only (`homeDailyQuotes` in `eleventy/filters/home.js` — the page language's
  magazine first, a quote older than 2 days left out; `home.js` adds "Today" / "Yesterday" in Central time
  and names the link "Today's quote on …" when the quote shown is not today's; on phones only the page
  language's quote shows, the other behind a button). No other page repeats it.
* Without data the file is `{updated: null, items: []}` and the home page leaves the card out.

### audio_project.json — record your story by phone (/contribute/#record)
Read daily by `scripts/sync/audio_project.py` from the official pages (`config/site.yml` → `sources.grapevine.audio_project`
= aagrapevine.org/audio-portal; `sources.lavina.record_story` + `record_instructions` = aalavina.org/graba-tu-historia and
its instructions page; `record_tips`, `record_topics`, `sample_audio` are only linked) — 3 requests a day through the
shared polite session, then `build_data.py` (nothing is translated). **ONE canonical home**: /contribute/#record;
Listen and Watch only link to it. The steps on the page are the site's own words (`community.rec.*`, hand-written EN/ES)
around the values below — never the official sentences machine-translated.
```json
{
  "updated": "2026-09-25T14:09:42Z", "fixture": false,
  "checked": "2026-09-25T14:09:27Z",          // the older of the two parts' last good reading
  "gv": {                                     // null when unknown (first run, or never read)
    "page_url": "https://www.aagrapevine.org/audio-portal",
    "phone": "(559) 726-1216", "tel": "+15597261216",     // shown (one style for both lines) / for the tel: link
    "minutes_min": 6, "minutes_max": 8,
    "keys": { "record": "1", "finish": "#", "save": "1", "permission": "2" },   // one of 0-9 # *
    "email": "webcoord@aagrapevine.org",      // Cloudflare-protected on the page: decoded (first byte = XOR key)
    "formats": ["WAV", "MP3"], "no_speakers": true,        // "does not collect recordings from speakers"
    "channel_url": "https://www.youtube.com/@AAGRAPEVINE",
    "playlists": [ { "title": "Sponsorship", "url": "https://www.youtube.com/watch?v=…&list=…" } ],
    "checked": "2026-09-25T14:09:27Z" },
  "lv": {
    "page_url": "https://www.aalavina.org/graba-tu-historia",
    "instructions_url": "…/instrucciones-graba-tu-historia", "tips_url": "…/consejos-de-grabacion",
    "topics_url": "…/temas-sugeridos", "sample_url": "…/audio-de-muestra",
    "phone": "(559) 670-1601", "tel": "+15596701601", "minutes_max": 7,
    "keys": { "record": "1" },
    "permission_text": "Otorgo a La Viña los derechos de autor de la grabación …",   // said on the call, quoted as is (es)
    "long_distance": true, "email": "lveditorial@aagrapevine.org", "formats": ["WAV", "MP3"],
    "no_speakers": true, "checked": "2026-09-25T14:09:42Z" }
}
```
* Raw items `audio:gv` / `audio:lv` (kind `audio_project`) carry the same fields in `extra`, plus `steps_text`
  (the official steps as written, for checking only).
* Each part is independent. A page that cannot be fetched, or on which a number, a key, the length or the
  e-mail address is not found (a layout or process change), keeps the previous data of that part and the raw
  envelope gets `ok: false` with the reason ("Record your story by phone" on /status/). When only La Viña's
  instructions page fails, the keys and the sentence come from the previous run and the source still counts as
  updated (`ok: true`; the problem is a note in `stats.warnings`, shown under "Notes" in the run summary).
* `phone` in the site file is written from `tel` in one style, "(559) 726-1216", whatever way each official
  page writes it (the raw `extra.phone` keeps the page's own spelling).
* `build_data` checks every value again: a part without a dialable `tel` (`+1` and 10 digits) is null; keys
  that are not one digit / `#` / `*` and an address that is not an e-mail address are dropped. The page shows a
  part's steps only when all its keys are there, and "see the official page" when the part is null.

### status.json
```json
{
  "generated": "2026-09-23T10:40:00Z", "updated": "…", "fixture": false,
  "sources": [
    { "source": "youtube", "label": "YouTube videos", "label_es": "Videos de YouTube",
      "ok": true,            // true = last run fine · false = last run failed (older data kept) · null = never ran
      "updated": "…",        // last SUCCESSFUL run
      "attempted": "…",      // last run, successful or not
      "count": 528, "new_7d": 3, "error": null, "stats": { /* the module's own counters */ } }
  ],
  "crawl": { "known_pages": 3162, "crawled_pages": 52, "never_crawled": 3110, "never_crawled_events": 2939,
             "queue_remaining": 3110, "est_days_to_full": 9.6, "last_run_pages": 0, "page_errors": 0, "new": 0,
             "pdfs": 92,               // the Library's entries (curated: official, each once) — as sources[pdfs].count (its new_7d counts the same entries)
             "pdfs_gone": 0, "pdfs_with_details": 14,
             "pdfs_with_thumbs": 92,   // Library entries with a first-page preview
             "updated": "…", "attempted": "…" },   // the crawler's page counters: for the run summary / maintainers
  "translations": { "cached": 1620, "engine": "argos1.0-ct2/v5", "model_enabled": true,
                    "translated_this_run": 0, "from_cache": 1620, "pending": 0, "rejected_by_guard": 0,
                    "seconds": 0.1, "model_seconds": 0.0, "texts_per_second": null, "glossary_entries": 162 },
  "counts": { "videos": 528, "episodes": 295, "…": 0, "whatsnew": 150 },
  "spotlight": { "today": "2026-09-23", "home_days": 60, "list_days": [60, 90],
                 "counts": { "60": { "neta65": 2, "texas": 8, "all": 100 }, "90": { … } }, "items": 156 },
  "problems": { },         // raw files that were missing/unreadable ("missing" = module never ran), and settings
                           // build_data could not use: "meeting" (also a skip_dates value that is not a meeting
                           // day), "recurring_events", "ics_feeds" (text says what); and
                           // "content_events": slips in content/events files that were worked around (a
                           // `location_es` still saying "Lugar por anunciarse" next to a real `location` …)
  "feeds": [               // the optional outside calendars (config sources.ics_feeds) — NOT content sources:
    { "key": "neta-65-workshops", "url": "https://neta65.org/events/category/workshop/list/?ical=1",
      "label": "NETA 65 workshops", "label_es": "Talleres de NETA 65",
      "category": "neta65", "group": "neta",       // where its events show on /events/ (neta | calendar)
      "state": "blocked",      // ok · blocked (the site's bot protection: HTTP 401/403/429, Cloudflare check) ·
                               // error (no answer, 404/500, not a calendar file) · never (not asked yet)
      "http_status": 403, "error": "HTTP 403: the site's bot protection (Cloudflare …) turned the robot away",
      "last_success": null,    // when a calendar file was last read (its copy is used while the feed fails)
      "last_attempt": "…",     // the last request (at most once a day, whether it worked or not)
      "checked_this_run": true, "from_copy": false,
      "events_count": 0,       // events the feed gave this run (from the copy when it failed)
      "duplicates": 0,         // of those, already on the calendar (content/events, a flyer, the committee
                               // meeting, a recurring_events date) — shown once
      "notes": [] }            // for the chair: what the feed says that a content/events file does not
                               // (its event page on another date, another start time, a venue the file
                               // still calls "to be announced") — listed in the Actions run summary
  ]
}
```
`feeds` is kept apart from `sources` on purpose: the /status/ page explains each feed in plain words
("Other calendars we read"), and the Actions run summary lists them as information only, so a feed that
a site's bot protection blocks never counts as failed and never opens the "A content source has stopped
updating" issue.
`pending` > 0 means the translation time budget ran out; the rest is translated on the next run.
`rejected_by_guard` counts sentences whose machine translation was refused (repeated words,
> 2.5× longer, changed numbers, HTML entities) — those keep their original text.

### The monthly toolkit (`/monthly/`) — no data file of its own
`eleventy/filters/monthly.js` builds one model per month at build time (America/Chicago): this month + the
next 12 (`monthlyPages` → `/monthly/YYYY-MM/` × en/es; `mpMonths` / `mpMonth` filters) from `db.editorial`
(themes, story deadlines, La Viña's suggested topics), `db.articles.issues`, `config/carry.yml` (the 10 ways,
the "put it to work" tips), `db.events` (+ the committee meeting from `site.meeting` and recurring dates from
`site.recurring_events` for months past `events.json`), `db.weekly_open` and `db.shop.botm`. The hub (`/monthly/`)
is the canonical home of the 10 ways; each month page of that month's toolkit and poster (PNG 1080 × 1350,
share, print on one Letter page). The 3 months before this one keep small redirect pages to `/monthly/`
(`monthlyPastPages`, `src/pages/monthly-past.njk`), so a printed poster's QR code never lands on a 404.
`MONTHLY_NOW=2026-12-15` fixes "today" for testing.

### The district report (`/monthly/#report`) — no data file of its own
`eleventy/filters/report.js` (`rpModel`) writes the current month's report in English AND Spanish as 12 sections
`{id, title, text}` (plain text; a line starting with "•" is a list item): `header` (blanks `[##]`, name, role,
home group), `committee` (`meeting.next`, `site.meeting`), `issues` (the month model: GV theme, LV issue, the
`config/carry.yml` tips, a newer issue already out), `deadlines` (next 3 Grapevine deadlines in `db.editorial`, La
Viña's rotating topics, `db.audio_project` phone lines), `shop` (`db.shop` Book of the Month and the lowest U.S.
subscription prices, Carry the Message), `events` (`upcomingEvents`: the next 45 days, a monthly series once),
`writers` (`db.spotlight`, Area 65, 60 days), `meetings` (`gvMeetings`; plus `meetings.options`: one option per
county of our Area and per nearby region, with its lines, for the county picker), `weekly` (`db.weekly_open`),
`resources` (`db.pdfs` of the last 45 days, `site.links` GVR/RLV sign-up, contact), `asks`, `notes` (empty). The page
embeds it as JSON (`rpJson`); `src/assets/js/report.js` is the editor; without JavaScript `rpText` shows it as text.
Kept per visitor only (optional): `localStorage["gv-report:YYYY-MM:en|es"]` = the changes to that month's report
`{ order, off, text, titles, custom, meet }` (only what differs from the data; drafts older than 3 months are
removed), `["gv-report:profile"]` = `{ district, name, role, group }`, `["gv-report:lang"]` = the report language.

### GVR / RLV 101 (`/orientation/`) — `config/orientation.yml`
Hand-written lessons for new GVRs / RLVs, loaded by `src/_data/orientation.js` as `orientation`
(`panel`, `lessons[]`, `pages` → `/orientation/<id>/` × en/es, `totalMinutes`, `totalChecks`). Each lesson:
`id` (the page address — keep it), `icon`, `minutes`, `example` (`issue` · `meeting` · `tip` · `botm` ·
`deadline` · `poster`: which live example `src/_includes/macros/orientation.njk` builds from `db.*`, `carry` and
`meeting` — nothing in the file itself goes stale), `title` / `summary` / `goal` / `try` / `discuss` `{en, es}`,
`points[]` `{title, text}`, `links[]` (`href` a page of this site · `link` a `site.links` key · `url`, with
`pub: gv|lv` for the La Viña-first order on `/es/`) and `check[]` (3 questions × 3 options, `answer` 1–3, `why`).
Placeholders `{rule_lc}` `{time}` `{panel}` `{panel_start}` are filled from `site.meeting` and `panel`. The build
fails on a missing language or a malformed question (`I18N_STRICT=1`); `tests/test_orientation.py` checks the
same plus lengths and wording. Filters: `eleventy/filters/orientation.js` (`o101Text`, `o101Vars`, `o101Links`,
`o101Deck` — the slide plan). The search index lists each lesson (`eleventy/filters/library.js`). The only thing
kept per visitor is `localStorage["gv-orientation-v1"]` = `{ v: 1, done: [lesson ids] }` (optional).

## 4. Template helpers (Eleventy filters)

* `{{ "nav.home" | t(lang) }}` — UI string from `src/_i18n/*.json`
* `{{ item | tx("title", lang) }}` — item field in the page language (falls back to original)
* `{{ item.date | fmtDate(lang, "long") }}` — `short` / `long` / `month` / `iso` / `relative` / `time`
* `{{ "/library/" | lurl(lang) }}` — language-prefixed URL (`/es/library/` for Spanish)
* `{{ page.url | altLangUrl(lang) }}` — the same page in the other language
* `{{ items | where("kind", "pdf") }}`, `| limit(6)`, `| sortByDate`, `| upcoming`, `| past`, `| groupBy("category")`, `| isRecent(14)`
* `{% icon "calendar", "size-5" %}` — inline Lucide SVG icon

## 5. Extended fields (as built)

Scanned from the real `data/raw/*.json` and `data/site/*.json` (2026-09-23). "→ i18n" = the
field also gets `i18n.<name> = {en, es}` in the site file.

### Raw envelope extras (besides `source updated attempted ok error stats items`)

| raw file | extra top-level keys |
|---|---|
| `articles.json` | `issues` {"gv:2026-10": {`publication`, `key`, `label`, `theme`, `description`, `url`, `image`, `cover` (local WebP), `hub`, `seen`}} — only issues seen as the CURRENT issue on a magazine hub; `detail_state` (module bookkeeping: retry state; `byline_at` = the article page was read and has no author/place, do not ask again); `archive_state` (below) |
| `podcasts.json` | `shows` [{`key`, `name`, `title`, `feed`, `description`, `image`, `language`, `web`, `apple`, `spotify`, `amazon`, `episodes`}], `discovery` (weekly feed discovery) |
| `youtube.json` | `playlists` [{`id`, `title`, `lang` (en/es/und), `count`, `channel_id`, `url`}], `backfilled_at`, `detail_fails`, `channel_ids` |
| `instagram.json` | `profiles` {gv/lv: {`username`, `name`, `full_name`, `url`, `followers`, `posts`, `owner_id`, `checked`, `avatar`}} |
| `pdfs.json` | `crawl` {`known_pages`, `crawled_pages`, `pdfs`, `last_run_pages`}; crawler counters are in `stats` |
| `events_external.json` | `cache`, `sitemap` (module bookkeeping — not used by the site) |
| `meetings.json` | `feeds` [{`id`, `name`, `url`, `lang`, `ok`, `count`, `method` (feed/page), `key_from` (secret/config/key_source — never the key), `error`, `note`, `updated`, `attempted`}], `type_labels` {code: {en, es}}. Items: `mtg:<hash>` (kind `meeting`, `title` = name, `url` = the meeting's page; `extra` = `day`, `time`, `end_time`, `location`, `address`, `street`, `zip`, `city`, `county`, `state`, `lat`, `lng`, `approximate`, `region`, `district`, `types`, `attendance`, `in_area`, `sources`) |
| `quote.json` | `history` {gv/lv: [{`pub`, `lang`, `date`, `heading`, `text`, `attribution`, `source`, `source_lang`, `url`, `signup_url`}]} — the last 14 days, newest first, one per day (raw only: the guard against a page going back to an older quote; never shown). Items: `quote:<pub>:<date>` (kind `quote`, `title` = the official heading, `url` = the page anchor; `extra` = `pub`, `text`, `attribution`, `source`, `source_lang`, `signup_url`, `date_label`, `date_from_heading`, `block` (teaser/embed/anchor), `node`) |
| `shop.json` | `bulk_discounts` {`source_url`, `tiers`, `note` {en?, es?}}, `types` {gv/lv: {print/digital/complete: {`text`, `lang`, `url`}}}, `listings` [{`pub`, `region`, `url`}], `types_checked` (ISO; type descriptions are re-read every 30 days). Items: `botm:gv` / `botm:lv` (kind `botm`; `extra` = `pub`, `page_url`, `price`, `sale_price`, `discount_pct`, `currency`, `sku`, `starts`, `ends`, `month`, `month_label`, `offer_text`, `product_name`, `image_src`) and `sub:<pub>:<region>:<sku>` (kind `subscription`; `extra` = `pub`, `region`, `listing_url`, `type`, `term_months`, `price`, `currency`, `sku`, `volume`, `position`, `image_src`) |

`articles.json` → `archive_state` — the archive listings (aagrapevine.org/archive, aalavina.org/archivo):
```json
{ "gv": { "backfilled": "2026-09-23T23:31:00Z",   // first full walk back finished (absent until then)
          "backfill_days": 120,                    // how far back it went (--backfill-days)
          "resume_page": 14,                        // only while an interrupted backfill is pending
          "backfill_stopped_at": 40,                // only when the backfill was given up (see below)
          "last_run": "…", "pages_last_run": 1, "new_last_run": 0,
          "oldest_seen": "2026-05",                 // oldest issue on the pages read last run
          "error": "page 0: no answer" },           // only when the last run had a problem
  "lv": { … } }
```
A daily run after the backfill reads page 0 and stops at the first page whose stories are all known.
Delete `archive_state` (or raise `--backfill-days`) to walk back again.
Safety stops for a site whose page links break: a page that still offers "Next" but lists only stories
already read earlier in the same run ends the walk (`error` "page N repeats stories of earlier pages
(pager broken?)", and the source shows as failing on /status/); an unfinished backfill that would have to
resume deeper than 6 pages per month of `--backfill-days` (at least 2 × `--archive-pages`; 40 for 120
days) is given up: it is marked `backfilled`, with `backfill_stopped_at` and an `error`, so it stops
costing requests.

`detail_state` holds only records whose article page still has something to tell. A Grapevine
"Online Exclusive" page prints no section, so for it title + paywall flag (`free`) count as complete and
it is read once.

### Site file top-level keys

| site file | key | shape |
|---|---|---|
| `instagram.json` | `profiles` | {gv: {username, name, full_name, url, followers, posts, owner_id, checked, avatar}, lv: {…}} — config order; names are brands (no i18n) |
| `videos.json` | `playlists` | [{id, title, lang (en/es — `und` is detected), count, channel_id, url, `i18n.title`, `machine`}] in the channel's order |
| `episodes.json` | `shows` | [{key, name, title, feed, description (plain text), image, language, web, apple, spotify, amazon, episodes, lang, `i18n.title`, `i18n.description`, `machine`}] in config order |
| `articles.json` | `issues` | [{id "gv:2026-10", publication, key, label, theme, description, url, image, cover, hub, lang, `i18n.theme`, `i18n.description`, `i18n.label`, `machine`}] newest `key` first. `i18n.label` is written by rules: GV monthly "October 2026" / "Octubre 2026"; LV bimonthly (odd key month) "September / October 2026" / "Septiembre / Octubre 2026" |
| `whatsnew.json` | (per item) `wn_date` | the news date used for sorting (ISO UTC) |

### Item `extra` fields per kind (site files carry the same `extra` as raw)

| kind (file) | extra fields | extra i18n |
|---|---|---|
| episode (`episodes`) | `audio_url`, `audio_type`, `audio_bytes`, `duration_sec`, `season`, `episode`, `episode_type`, `show`, `show_name`, `show_web`, `link`, `player_url`, `apple`, `spotify`, `amazon` | — |
| video (`videos`) | `video_id`, `channel_id`, `duration_sec`, `playlists` [names], `is_short`, `views`, `is_live_recording`, `date_approx`, `season`, `episode` (podcast videos only) | — |
| post (`instagram`) | `shortcode`, `account`, `username`, `media_type`, `thumb`, `embed_url`, `permalink`, `is_reel`, `manual`, `strategy`, `caption_known`, `embed_checked` | — |
| article (`articles`, `spotlight`) | `publication`, `issue_key`, `issue_label`, `issue_date` (cover date), `issue_theme`, `issue_url`, `topic`, `section`, `author`, `author_location`, `subtitle`, `teaser`, `free`, `online_exclusive`, `department` (bool); written by build_data: `geo`, `pub_date` (below) | `section`, `topic`, `issue_theme` (machine); `issue_label`, `author_location` (rules — from `geo.label_en/label_es`, only when a place is known) |
| pdf (`pdfs`) | `host`, `file_url`, `filename`, `size_bytes`, `pages`, `thumb`, `referrers` [{url, title}], `upload_month`, `link_texts`, `event_date`, `doc_lang`, `multilingual` (`true` when one file holds several languages — two or more page languages, language-only links for 2+ languages, or a heading such as "Catalog • Catálogo • Catalogue"; otherwise `null`: such a file takes the host site's language and gets no "(Spanish)" title suffix), `section` (heading on the referring page), `external`, `orphan`; after the Library rules (§3 *pdfs.json*): `duplicates` (other addresses of the same file), `kits` (every rep kit it is in), `versions` (language editions), `same_file` | `versions[].i18n_title` |
| topic (`editorial`) | `publication`, `theme`, `evergreen`, and for dated GV themes `issue_key`, `issue_label`, `deadline`, `due_text`, `pdf_url`, `submit_url`, `guidelines_url` | `issue_label` (rules); `theme` when it differs from the title |
| meeting (`weekly_open`) | `zoom_id`, `zoom_url`, `passcode`, `day`, `time`, `time_central`, `sentence`, `weekday`, `start_local`, `timezone`, `next_start`, `url`, `player_url`; La Viña item (`weekly_open_lv`, §2): no `sentence`/`player_url`, plus `starts`, `source_note`, `own_i18n` | written by rules from weekday/start_local/timezone: `day` ("Wednesdays"/"Miércoles"), `time` ("Noon Eastern"/"mediodía (hora del Este)"), `time_central` ("11:00 AM Central"/"11:00 a. m. (hora del Centro)"), `when` ("Wednesdays at 11:00 AM Central"/"Miércoles a las 11:00 a. m. (hora del Centro)"), `sentence` (join line with Zoom ID + passcode). Machine-translated only if those fields are missing |
| event (`events`) | common: `start`, `end`, `all_day`, `location`, `online_url`, `flyer_url`, `flyer_thumb`, `city`, `state`, `past`; `tentative` (`true` only: details to be confirmed), `location_tba` (`true` only: the place is not known yet — "Venue to be announced"; decided by the item's own `location`, by `location_es` / `location_en` only when there is no `location`). Committee: `meeting_id`, `passcode`, `recurring`. Recurring (category `recurring`, below): `recurring` (`true`), `series`, `rule`, `recurrence_label`. External calendar: `platform`, `online`, `scope`, `site`, `country`, `website`, `organizer`, `date_text`. Drive flyer: `drive_id`, `is_pdf`, `is_image`. Manual: `body_md`, `slug`, `file`, `own_i18n`; `also_in_feed` (the key of an .ics feed that lists the same event — also on a flyer, committee or recurring event) + `feed_match` (`url` / `title`). .ics feed (category `neta65` / `ics` / `gv-calendar` / `lv-calendar`, source `calendar`): `feed` (the feed's key), `uid` | committee meetings and recurring events: fixed human `title`/`summary` in both languages; recurring: `recurrence_label` (rules); manual: `body_md` (+ the file's own `title_es` / `summary_es`, never machine-translated); `location` (the file's `location_es` / `location_en`, or the site's own words for a place not known yet — never machine-translated) |
| document / slides / photo / video_file / form (`drive`) | `file_id`, `mime`, `name`, `panel`, `panel_label`, `path`, `album`, `view_url`, `preview_url`, `download_url`, `thumb_url`, `image_url`, `is_image`, `is_video`, `is_pdf`, `file_type`, `folder_id`, `folder_url`, `folder_chain`, `modified_text`, `size_bytes`, `duration_sec`, `shortcut_id`, `generic_name`, flyers: `event_date`, `event_title`, `event_time`, `event_end_time`, `event_location` / `event_month`, forms: `form_closed`, `form_signin_required` | `album` (photos in a sub-folder) |
| announcement (`announcements`) | `body_md`, `expires`, `pinned`, `slug`, `file`, `link`, `own_i18n` | `body_md` (+ the file's own `title_es` / `summary_es`) |

#### Recurring events (category `recurring`; build_data.recurring_events)
One item per date of each `recurring_events:` entry in `config/site.yml` — the next `months_ahead` (default 6;
/events/ and the calendar feed list only these — the /monthly/ posters work out later months from the same rule,
`site.recurring_events`, so the booth is on every month's poster without 13 cards on /events/)
dates plus the dates of the last 90 days (those have `extra.past: true`; the pages never list them, the
calendar feed keeps them for subscribers):
```json
{ "id": "ev:recurring:citywide-dallas:2026-10-10",      // key + local date: stable (calendar UID, card anchor)
  "source": "committee", "kind": "event", "category": "recurring",
  "url": "https://citywidedallasaa.org",                // the entry's `url`, or "/events/" without one
  "title": "GV/LV booth at CityWide Dallas", "lang": "en", "date": "2026-10-10T22:00:00Z",
  "first_seen": null, "tags": ["recurring"],
  "extra": { "start": "2026-10-10T22:00:00Z",           // 17:00 CDT; "2026-11-14T23:00:00Z" = 17:00 CST
             "end": "2026-10-11T01:00:00Z", "all_day": false,
             "location": "Lover's Lane United Methodist Church, 9200 Inwood Road, Dallas, TX 75220",
             "city": "Dallas", "state": "TX",              // read from the location (or the entry's city/state)
             "online_url": null, "flyer_url": null, "flyer_thumb": null,
             "recurring": true, "series": "citywide-dallas",
             "rule": {"week_of_month": 2, "weekday": "saturday", "start": "17:00", "end": "20:00"},
             "recurrence_label": "Every second Saturday of the month · 5:00 – 8:00 PM", "past": false },
  "i18n": { "title": {"en": "GV/LV booth at CityWide Dallas", "es": "Mesa de GV/LV en CityWide Dallas"},
            "summary": {"en": "…", "es": "…"},
            "recurrence_label": {"en": "Every second Saturday of the month · 5:00 – 8:00 PM",
                                 "es": "Cada segundo sábado del mes · 5:00–8:00 p. m."} },   // thin spaces around " – ", no-break in "p. m."
  "machine": [], "is_new": false }
```
* `title`/`summary` are the committee's own words (`title_es`, `summary_es`); a language left out is
  machine-translated and listed in `machine`. `recurrence_label` is written by rule, never translated,
  with the committee meeting's words (src/_i18n/committee.json `committee.rule` / `committee.ord.*`: "Every
  third Wednesday of the month"). `rule` is the entry's rule in the shape of `meeting:` (the end as used:
  a missing one is start + 1 hour); the web pages build the line from it with the meeting's own helpers
  (committee.js `recurrenceText`: same words, the browser's clock format) and fall back to `recurrence_label`.
* Never `is_new`, never in `whatsnew.json`, never in the /events/ "Past events" list. Home, search, the
  digest pages and the e-mail show only the next date of each `series`.
* Entries with a mistake are skipped; `status.json` → `problems.recurring_events` says which and why.

#### Events from several places; several days (`events.json`)
* **Several days.** An all-day event's `extra.start` / `extra.end` are dates and `end` is the LAST day
  (content/events `start: 2027-03-19`, `end: 2027-03-21`; an .ics feed's exclusive `DTEND;VALUE=DATE:20270322`
  is turned into `2027-03-21`). Its end counts from 23:59 Central on its last day (`event_end_ts`), and like
  every event it keeps `past: false` for one more day after that (`build_events` cutoff = now − 24 h: an
  assembly ending Sunday Mar 21 is `past: true` from 23:59 on Monday Mar 22). The pages do not wait for
  that: they hide it at midnight after its last day (`chicagoDayEndMs`). The pages show a date range; the
  calendar feeds write `DTSTART;VALUE=DATE:20270319` + `DTEND;VALUE=DATE:20270322`.
* **Outside calendars** (`sources.ics_feeds`): one item per VEVENT (RRULE expanded; CANCELLED left out), id
  `ev:ics:<hash of UID + start>`, `source: "calendar"`, `category` = the feed's `category:` (`neta65` or `ics`
  → shown with the NETA 65 events; `gv-calendar` / `lv-calendar` → with the GV/LV calendars), `url` = the
  VEVENT's `URL`, `extra.flyer_url` / `flyer_thumb` = its `ATTACH` (an image), `tags` = its `CATEGORIES`,
  `extra.tentative` = `STATUS:TENTATIVE`, `extra.location` without ", United States". The last good copy of
  each feed and its last answer are in `data/state/ics_feeds.json`
  (`{url: {"fetched": last success, "ics": text, "attempted", "state", "http_status", "error"}}`).
* **One real event, one item.** A feed event that is the same event as one already on the calendar — a
  content/events file, a dated Drive flyer, the committee meeting or a `recurring_events:` date — is left
  out. Both must **start the same local day**, and then either link the same event page (URL compared
  without scheme, `www.`, trailing slash, `?query`, `#fragment` — `neta65.org/event/<slug>`), or have the
  same shape (both all-day, or both timed and starting at most 2 hours apart; never one over several days
  against one on a single day — a file without `end` may take the feed's), not two different cities, and
  titles that name the same event (`similar_titles`: the words that tell events apart — the kind of event
  included, "workshop", "booth", "assembly" — shared at least 75 %, the shared city and the year ignored;
  word for word when a city is not known). So a workshop or a booth *at* an assembly, on its first day, is
  a separate event. The same event page on another date is another date (a series, a page used again,
  or a date that changed): the feed event is kept. The hand-written item wins and keeps its `own_i18n`;
  the feed only fills what it leaves out — `flyer_url`, `flyer_thumb`, `online_url` and the event-page
  `url` (when the file has none) only on a sure match (the same page, or the same start); a missing
  `location`; a `location` that is "to be announced" (on a sure match: the feed's venue replaces it and
  a TBA `location_es` is dropped); a missing `end` of the same kind — and the item is named in
  `extra.also_in_feed` / `extra.feed_match` (`url` / `title`). Nothing is copied onto the committee meeting
  or a recurring date. Where the feed says something the file does not — its event page on another date
  (for an upcoming file), another start time, a venue the file still calls "to be announced" —
  `status.json` `feeds[].notes` says so, for the chair to update the file. The same event in two feeds is
  kept once.

#### Articles: `extra.geo` and `extra.pub_date` (site files only; build_data.py)
`extra.geo` = where the writer is from, read from `extra.author_location` by `scripts/sync/geo.py`:
```json
"geo": { "scope": "neta65",                         // neta65 | texas | other | unknown
         "city": "Grand Prairie",                   // as the writer gave it (tidied), or null
         "county": "Dallas",                        // principal county, or null (unknown / not Texas)
         "counties": ["Dallas", "Ellis", "Tarrant"],// every Texas county the place lies in ([] outside Texas)
         "state": "TX",                             // US/Canada postal code, Mexican state name, or null
         "country": "US",                           // ISO code, or null when unknown
         "label_en": "Grand Prairie, Texas",        // display label (null when no place)
         "label_es": "Grand Prairie, Texas" }       // ("Nueva Jersey" / "New Jersey", "Condado de Houston, Texas" …)
```
* `neta65` — the place lies in an Area 65 county (`config/site.yml` `spotlight.neta65_counties`); a place
  in several counties counts if ANY of them is in Area 65. Cities are matched to counties with
  `data/geo/texas_places.json` (U.S. Census 2020 place-by-county table; see `data/geo/README.md`).
* `texas` — elsewhere in Texas, or "Texas" with no usable city. `other` — anywhere else, including a bare
  city name without a state that is not on geo.py's short list of unambiguous Texas cities ("Paris"
  alone is Paris, France; "Dallas" alone is Dallas, Texas). `unknown` — no place given (or a note printed
  where the place goes, e.g. a reprint's "Excerpt. Original title: …, August 1948").
* "Houston, Texas" is Harris County (`texas`); only "Houston County, Texas" is Area 65.
* A region counts only when it stands alone: "West Texas" / "West TX" is the region (`texas`), "West,
  Texas" is the town of West (McLennan County, `neta65`); "Panhandle, Texas" is the town of Panhandle.
* Also read as Texas: "Denton (Texas)", "Dallas, Texas USA", "Fort Worth, TX, 76102", "Tyler, Texas,
  District 42", "Texarkana, TX-AR"; words around the place are left out ("near Tyler", "Tyler area",
  "cerca de Tyler", "somewhere in East Texas" → East Texas); "N. Richland Hills", "De Soto", "Mc Kinney",
  "North Dallas", "Hurst-Euless-Bedford", Dallas / Fort Worth neighborhoods ("Oak Cliff" → Dallas County)
  and Spanish town names ("Palestina" → Palestine, English label "Palestine, Texas") are matched to
  their county. The list of rules is in the docstring of `scripts/sync/geo.py`.
* Mexican state abbreviations are written out ("Guadalajara, Jal." → Jalisco). "N.L.", "B.C.", "Mich."
  and "Col." are Mexican states in La Viña bylines (Nuevo León, Baja California, Michoacán, Colima)
  unless the country or a well-known city says otherwise ("Vancouver, B.C."); in Grapevine bylines they
  are Newfoundland and Labrador, British Columbia, Michigan, Colorado unless the country or city says
  otherwise ("Tijuana, B.C."). `label_en` is written in English only for a Spanish town name; otherwise
  both labels keep the writer's spelling of the city.

`extra.pub_date` (`YYYY-MM-DD`) = the day the story counts as published for the 60/90-day windows and the
monthly digest (last month's writers): the EARLIER of the first day of its issue (La Viña's bimonthly issues: the first month) and
the day the story was first seen online (`first_seen`, in America/Chicago) — never later than today. It
does not move: an October issue seen online on September 16 counts from September 16, also after
October 1 (so the digest lists it once); a back-catalog story found by the archive backfill counts
from its issue's first day (its `first_seen` is the later backfill day).

### Crawler state — `data/state/crawl-state.json`
Written only by `scripts/sync/crawl.py` (never edit it by hand; deleting it starts the crawl over).
Top level: `version`, `updated`, `sitemaps` {url: {`fetched`, `status`}}, `runs` (last runs' counters),
`pages` {url: page record}, `pdfs` {normalized url: PDF record}. `data/raw/pdfs.json` is rebuilt from it
on every run. A PDF record:

| field | meaning |
|---|---|
| `url`, `first_seen`, `last_seen_on_page`, `refs` [{`url`, `title`, `section`, `texts`, `alts`, `seen`}], `external` | where the file is and which pages link it |
| `status` | `ok` · `gone` (left out of the site files) · `not-pdf` (the link turned out to be a web page) |
| `gone_strike_at` | the FIRST failing check (404/410, or an HTML page where the file was). The PDF only becomes `gone` when a second check at least 24 h later fails too (`GONE_CONFIRM_H`); a good answer clears it |
| `gone_since` | when it became `gone`. A gone PDF that a page still links is checked again every 7 days during its first 60 days as gone, then every 30 days (`GONE_RECHECK_DAYS`); a good answer brings it back (`status: ok`, both fields removed) |
| `vanished_at` / `recheck` | no page links it any more (→ one check: deleted?) / a gone PDF is linked again (→ check: back?) |
| `hint`, `fresh` | the link is not a `*.pdf` address (the check must confirm it is a PDF) / it appeared on a page crawled before, so `first_seen` ≈ its publish date |
| `head` | last check: `status`, `size`, `type`, `last_modified`, `final_url`, `checked_at`; after no answer at all also `error: "unreachable"`, `fails`, `next_try` (2, 4, 8 … ≤ 60 days) and `unreachable_since` (first of those failures). A PDF on another site (`external`) whose host has not answered for 30+ days after 4+ tries becomes `gone` (`UNREACHABLE_GONE`); files on the two magazine sites never do |
| `details` | from the one-time download: `checked_at`, `title` (PDF metadata; the author is never read), `pages`, `chars`, `text_lang`, `page_langs` (language of each of the first 2 pages: `en`/`es`/`fr`, or `null` when a page has < 200 characters of text or no clear language — two different languages make the file `multilingual`), `heading` (a title-like line from the top of page 1), `thumb`; on failure `error`, `final` (do not retry), `attempts`, `next_try` |

### Translation rules that affect what you see
* Brand names are never translated (glossary `keep`: Grapevine, La Viña, Dear Grapevine, AA Grapevine,
  GVR, RLV, NETA 65, Grapevine Weekly Open …).
* Written by rules, not by the model: "[Season 11, Episode 12]" ⇄ "[Temporada 11, Episodio 12]"
  (typos such as "[Seaon 3. Episdode 1]" are normalized in the original title too), dates
  ("July 22, 2026" ⇄ "22 de julio de 2026"), prices, ordinals ("13th" → "13.º", "9th Step" → "Noveno Paso"),
  clock times ("7 PM" → "7 p. m."). Hashtag walls (3+ hashtags) are removed from teasers.
* A machine translation that repeats words, grows > 2.5×, changes/loses a number or contains an
  HTML entity is rejected; that sentence keeps its original text (counted in `status.translations.rejected_by_guard`).
