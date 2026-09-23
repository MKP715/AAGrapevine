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
  "ok": true,                          // false if this run failed (items kept from before)
  "error": null,                       // short message when ok=false
  "stats": { "fetched": 15, "new": 2 },// free-form counters shown on /status/
  "items": [ Item, ... ]
}
```

Source file names: `drive.json`, `youtube.json`, `podcasts.json`, `instagram.json`,
`articles.json`, `pdfs.json`, `events_external.json`, `editorial.json`,
`weekly_open.json`, `announcements.json`.

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
| instagram | post | account key `gv` / `lv` | `shortcode`, `account`, `username`, `media_type` (image/video/carousel), `thumb` (local path or null), `embed_url` |
| grapevine / lavina | article | publication `gv` / `lv` | `publication`, `issue_label` ("October 2026" / "Septiembre / Octubre 2026"), `issue_key` ("2026-10" / "2026-09"), `topic`, `section`, `author`, `free` (bool\|null) |
| crawl | pdf | `gvr` `rlv` `catalog` `flyer` `postcard` `news` `guidelines` `order-form` `workbook` `service` `literature` `other` | `host`, `file_url`, `size_bytes`, `pages`, `thumb` (site-relative path or null), `referrers` [{`url`,`title`}], `upload_month` ("2026-02"), `link_texts` [..] |
| drive | document / slides / photo / video_file / form | `reports` `notes` `slides` `flyers` `photos` `workshops` `announcements` `forms` `other` | `file_id`, `mime`, `panel` (77), `panel_label`, `path` ["photos","WhatsApp"], `album` (sub-folder name or null), `view_url`, `preview_url`, `download_url`, `thumb_url`, `image_url`, `is_image`, `is_video`, `is_pdf` |
| committee / drive / calendar | event | `committee` `flyer` `gv-calendar` `lv-calendar` `manual` `ics` | `start` (ISO datetime or date), `end`, `all_day`, `location`, `online_url`, `flyer_url`, `flyer_thumb`, `city`, `state` |
| committee / drive | announcement | `manual` / `drive` | `body_md` (original-language Markdown), `expires` (date|null), `pinned` (bool) |

`editorial.json` items (kind `topic`): `extra` = `publication`, `issue_label`, `deadline` (date\|null), `theme`.
`weekly_open.json`: single item (kind `meeting`) with `extra` = `zoom_id`, `passcode`, `day`, `time`, `url`
(+ structured `weekday`, `start_local`, `timezone`, `next_start` — see §5).
Every module adds more `extra` fields than listed here; §5 lists all of them as built.

## 3. Site files — `data/site/*.json` (what templates read)

Every item in a site file = raw Item (**minus** `last_seen`, which only the sync modules use;
items with `status: "gone"` are left out) **plus**:

```json
"i18n": {
  "title":   { "en": "…", "es": "…" },
  "summary": { "en": "…", "es": "…" },
  "body_md": { "en": "…", "es": "…" }          // announcements + manual events (render with | md)
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
`topic` (editorial themes; their date is a deadline), kind `meeting` (Weekly Open) and committee
meetings. `whatsnew.json` = the newest 150 by news date (`wn_date`), same exclusions; events
appear there for 30 days after they were first announced; several Drive photos of one album on
one day become one group item (`extra.is_group`, `count`, `thumbs`).

Files: `episodes.json`, `videos.json`, `instagram.json`, `articles.json`, `pdfs.json`,
`drive.json`, `events.json` (committee meetings auto-generated for 12 months + Drive flyers +
TX calendar events + manual), `announcements.json`, `editorial.json`, `weekly_open.json`,
`whatsnew.json` (newest 150 across all sources, sorted by date desc),
`status.json` (below), `districts.json` (from `content/districts.yml`).

Each site file is `{ "updated": "...", "fixture": false, <extra top-level keys>, "items": [...] }`,
items sorted newest first (events: soonest first). Extra top-level keys: `instagram.profiles`,
`videos.playlists`, `episodes.shows`, `articles.issues` (§5). `drive.json` never contains a Google
Form whose `extra.form_closed` is `true`. Output is deterministic: a re-run with the same raw data
changes only `status.json` (and `updated` stamps).

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
             "pdfs": 106, "pdfs_gone": 0, "pdfs_with_details": 14, "pdfs_with_thumbs": 14,
             "updated": "…", "attempted": "…" },
  "translations": { "cached": 1620, "engine": "argos1.0-ct2/v5", "model_enabled": true,
                    "translated_this_run": 0, "from_cache": 1620, "pending": 0, "rejected_by_guard": 0,
                    "seconds": 0.1, "model_seconds": 0.0, "texts_per_second": null, "glossary_entries": 162 },
  "counts": { "videos": 528, "episodes": 295, "…": 0, "whatsnew": 150, "districts": 2 },
  "problems": { }          // raw files that were missing/unreadable ("missing" = module never ran)
}
```
`pending` > 0 means the translation time budget ran out; the rest is translated on the next run.
`rejected_by_guard` counts sentences whose machine translation was refused (repeated words,
> 2.5× longer, changed numbers, HTML entities) — those keep their original text.

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
| `articles.json` | `issues` {"gv:2026-10": {`publication`, `key`, `label`, `theme`, `description`, `url`, `image`, `cover` (local WebP), `hub`, `seen`}}, `detail_state` (module bookkeeping) |
| `podcasts.json` | `shows` [{`key`, `name`, `title`, `feed`, `description`, `image`, `language`, `web`, `apple`, `spotify`, `amazon`, `episodes`}], `discovery` (weekly feed discovery) |
| `youtube.json` | `playlists` [{`id`, `title`, `lang` (en/es/und), `count`, `channel_id`, `url`}], `backfilled_at`, `detail_fails`, `channel_ids` |
| `instagram.json` | `profiles` {gv/lv: {`username`, `name`, `full_name`, `url`, `followers`, `posts`, `owner_id`, `checked`, `avatar`}} |
| `pdfs.json` | `crawl` {`known_pages`, `crawled_pages`, `pdfs`, `last_run_pages`}; crawler counters are in `stats` |
| `events_external.json` | `cache`, `sitemap` (module bookkeeping — not used by the site) |

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
| article (`articles`) | `publication`, `issue_key`, `issue_label`, `issue_date` (cover date), `issue_theme`, `issue_url`, `topic`, `section`, `author`, `author_location`, `subtitle`, `teaser`, `free`, `online_exclusive`, `department` (bool) | `section`, `topic`, `issue_theme` (machine); `issue_label` (rules) |
| pdf (`pdfs`) | `host`, `file_url`, `filename`, `size_bytes`, `pages`, `thumb`, `referrers` [{url, title}], `upload_month`, `link_texts`, `event_date`, `doc_lang`, `section` (heading on the referring page), `external`, `orphan` | — |
| topic (`editorial`) | `publication`, `theme`, `evergreen`, and for dated GV themes `issue_key`, `issue_label`, `deadline`, `due_text`, `pdf_url`, `submit_url`, `guidelines_url` | `issue_label` (rules); `theme` when it differs from the title |
| meeting (`weekly_open`) | `zoom_id`, `zoom_url`, `passcode`, `day`, `time`, `time_central`, `sentence`, `weekday`, `start_local`, `timezone`, `next_start`, `url`, `player_url` | written by rules from weekday/start_local/timezone: `day` ("Wednesdays"/"Miércoles"), `time` ("Noon Eastern"/"mediodía (hora del Este)"), `time_central` ("11:00 AM Central"/"11:00 a. m. (hora del Centro)"), `when` ("Wednesdays at 11:00 AM Central"/"Miércoles a las 11:00 a. m. (hora del Centro)"), `sentence` (join line with Zoom ID + passcode). Machine-translated only if those fields are missing |
| event (`events`) | common: `start`, `end`, `all_day`, `location`, `online_url`, `flyer_url`, `flyer_thumb`, `city`, `state`, `past`. Committee: `meeting_id`, `passcode`, `recurring`. External calendar: `platform`, `online`, `scope`, `site`, `country`, `website`, `organizer`, `date_text`. Drive flyer: `drive_id`, `is_pdf`, `is_image`. Manual: `body_md`, `slug`, `file` | committee meetings: fixed human `title`/`summary` in both languages; manual: `body_md` |
| document / slides / photo / video_file / form (`drive`) | `file_id`, `mime`, `name`, `panel`, `panel_label`, `path`, `album`, `view_url`, `preview_url`, `download_url`, `thumb_url`, `image_url`, `is_image`, `is_video`, `is_pdf`, `file_type`, `folder_id`, `folder_url`, `folder_chain`, `modified_text`, `size_bytes`, `duration_sec`, `shortcut_id`, `generic_name`, flyers: `event_date`, `event_title`, `event_time`, `event_end_time`, `event_location` / `event_month`, forms: `form_closed`, `form_signin_required` | `album` (photos in a sub-folder) |
| announcement (`announcements`) | `body_md`, `expires`, `pinned`, `slug`, `file`, `link` | `body_md` |
| district (`districts`) | top-level `number`, `name`, `language`, `website`, `gvr_contact`, `meets` (+ any extra YAML keys) | `name`, `meets` |

### Translation rules that affect what you see
* Brand names are never translated (glossary `keep`: Grapevine, La Viña, Dear Grapevine, AA Grapevine,
  GVR, RLV, NETA 65, Grapevine Weekly Open …).
* Written by rules, not by the model: "[Season 11, Episode 12]" ⇄ "[Temporada 11, Episodio 12]"
  (typos such as "[Seaon 3. Episdode 1]" are normalized in the original title too), dates
  ("July 22, 2026" ⇄ "22 de julio de 2026"), prices, ordinals ("13th" → "13.º", "9th Step" → "Noveno Paso"),
  clock times ("7 PM" → "7 p. m."). Hashtag walls (3+ hashtags) are removed from teasers.
* A machine translation that repeats words, grows > 2.5×, changes/loses a number or contains an
  HTML entity is rejected; that sentence keeps its original text (counted in `status.translations.rejected_by_guard`).
