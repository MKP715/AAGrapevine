# Operations runbook

Technical companion to the [README](../README.md). For the data contract between the sync
scripts and the templates see [DATA_SCHEMA.md](DATA_SCHEMA.md); for first-time setup see
[SETUP-GITHUB.md](SETUP-GITHUB.md).

- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Workflows](#workflows)
- [Sync modules](#sync-modules)
- [Recurring events](#recurring-events)
- [Events: several days, "to be confirmed", places, outside calendars](#events-several-days-to-be-confirmed-places-outside-calendars)
- [Crawl politeness](#crawl-politeness)
- [Failure handling (design guarantees)](#failure-handling-design-guarantees)
- [Repository size](#repository-size)
- [Running locally](#running-locally)
- [Resetting state](#resetting-state)
- [Adding a new source](#adding-a-new-source)
- [Environment variables and secrets](#environment-variables-and-secrets)

---

## Architecture

```
                         ┌──────────────────── .github/workflows/update.yml ────────────────────┐
  triggers:              │                                                                       │
   • cron 10:17 UTC      │  JOB 1  sync  (ubuntu, Python 3.12)                                    │
   • Run workflow        │  ┌────────────────────────────────────────────────────────────────┐  │
   • push to main        │  │ python -m scripts.sync.run_all [--crawl-minutes N | --quick]    │  │
     (config/content/    │  │                                                                │  │
      templates/code)    │  │  articles.py ──┐  aagrapevine.org /magazine, aalavina.org      │  │
   • cron 12:07 UTC      │  │  crawl.py ─────┤  both sites, sitemap + pages → every PDF      │  │
     (quick: the daily   │  │  podcasts.py ──┤  feeds.captivate.fm RSS (2 shows: gv, wo)     │  │
      quote + Drive …)   │  │  youtube.py ───┤  channel/playlist RSS + yt-dlp listing        │  │
                         │  │  instagram.py ─┤  Graph API w/ token, else public embed pages  │  │
                         │  │  drive.py ─────┤  public Drive folders (or Drive API w/ key)   │  │
                         │  │  editorial.py ─┤  /contribute, /temas-sugeridos                │  │
                         │  │  weekly_open.py┤  /grapevine-weekly-open                       │  │
                         │  │  announcements ┘  content/announcements, content/events (repo) │  │
                         │  │        │ each writes data/raw/<source>.json (cumulative)       │  │
                         │  │        ▼                                                       │  │
                         │  │  translate.py  EN⇄ES, offline CTranslate2 + Argos models      │  │
                         │  │        │       cache: data/translations/cache.json            │  │
                         │  │        ▼       (overrides.yml + glossary.yml win)             │  │
                         │  │  build_data.py → data/site/*.json (+ i18n, whatsnew, status)  │  │
                         │  └────────────────────────────────────────────────────────────────┘  │
                         │  git add -A data/{raw,site,state} cache.json src/assets/cache → push │
                         │                                                                       │
                         │  JOB 2  build-deploy  (runs even if JOB 1 failed → last good data)   │
                         │  checkout main → npm ci → configure-pages → PATH_PREFIX/SITE_URL      │
                         │  → eleventy (src/ + data/site/*.json) → tailwind → _site/             │
                         │  → upload-pages-artifact → deploy-pages                              │
                         │                                                                       │
                         │  JOB 3  report  (parallel to JOB 2) → one issue while a source has   │
                         │  not updated for 7+ days; closed automatically when it recovers      │
                         └───────────────────────────────────────────────────────────────────────┘

   monthly-digest.yml (the 1st, 15:05 UTC = 9:05 CST / 10:05 CDT) → scripts/notify/send_digest.py → SMTP
   link-check.yml     (Sundays) → build → lychee on _site + polite check of config links → one GitHub issue
   check.yml          (every pull request + every code/settings push to main) → strict eleventy build + checks;
                      offline Python tests; digest dry-run
```

The repository *is* the database: every run's results are committed, so the git history is also
the backup, and any past day can be inspected or restored.

## Repository layout

| Path | What | Written by |
|---|---|---|
| `config/site.yml` | All settings | people |
| `content/announcements/*.md`, `content/events/*.md` | Hand-written content (optional) | people |
| `data/translations/overrides.yml`, `glossary.yml` | Translation fixes | people |
| `data/raw/<source>.json` | Cumulative per-source items (envelope + Items) | the matching sync module only |
| `data/state/*.json` | Resumable state (`crawl-state.json`; `ics_feeds.json` = the last good copy + last answer of each outside calendar) | sync modules, `build_data.py` |
| `data/translations/cache.json` | Translation memory (one entry per line) | `translate.py` |
| `data/site/*.json` | What templates read | `build_data.py` only |
| `src/assets/cache/{pdf,ig,articles,pod}/` | Small WebP thumbnails (≤ 480 px): PDF covers, Instagram posts, story images, podcast covers | sync modules |
| `src/` | Eleventy templates, CSS, JS, images | people |
| `scripts/sync/` | The sync pipeline | — |
| `scripts/notify/send_digest.py` | Monthly e-mail | — |
| `.github/workflows/` | Automation | — |
| `.cache/models/` | Translation models (~175 MB; `en_es/`, `es_en/`) — **not committed**, cached by Actions | `translate.py` |

## Workflows

### `update.yml` — Update & Deploy

| Aspect | Behaviour |
|---|---|
| Schedule | `17 10 * * *` (UTC) = 5:17 AM CDT / 4:17 AM CST: the **full** daily run. Minute 17 avoids GitHub's top-of-hour congestion (scheduled runs can start 5–30 min late). Plus `7 12 * * *` (UTC) = 7:07 AM CDT / 6:07 AM CST, a **quick** run so the Grapevine / La Viña daily quote (out before 6 AM Texas time) is on the site every morning — the 10:17 run is too early for it in winter. *Decide what to sync* tells them apart by `github.event.schedule` (the exact cron string; keep the two equal) — `schedule` + `7 12 * * *` → quick, any other schedule → full. The data commit of the 12:07 run is "morning refresh with the daily quote". |
| Manual run | Inputs `crawl_minutes` (default empty = the config value `sources.crawler.minutes_per_run`; whole minutes, capped at 300) and `skip_crawl` (= quick run). |
| Push to `main` | Uses a `paths` filter: everything **except** `data/**` (but *including* `data/translations/overrides.yml` and `glossary.yml`), `src/assets/cache/**`, Markdown docs (but *including* `content/**` and `src/**`), `docs/**`, `tests/**`, other workflows. A push runs a **quick** sync and redeploys. |
| No loops | Bot commits (a) only touch excluded paths, (b) carry `[skip ci]`, and (c) are pushed with `GITHUB_TOKEN`, which never triggers workflows. |
| Concurrency | Group `update-deploy`, `cancel-in-progress: false`: a new run waits for the current one (GitHub keeps at most one pending run; a newer pending run replaces an older pending one). The sync job checks out `ref: ${{ github.ref }}` — the branch **tip** when the job starts, not the commit that queued the run (`github.sha`) — so a run that waited starts from the data the previous run just pushed instead of re-crawling from older state. |
| Sync command | Schedule 10:17 UTC: `python -m scripts.sync.run_all --crawl-minutes <sources.crawler.minutes_per_run>` (config; missing → 40; **`0` = no crawl**, the other sources still run). Manual: `--crawl-minutes <input>` (empty → config value). Push, `skip_crawl` or the 12:07 UTC schedule: `--quick` = only `drive`, `announcements`, `podcasts`, `quote` (`QUICK_MODULES` in `run_all.py`, podcasts with `--no-discover`; `quote` = 2 page requests to the magazine sites) + translation + `build_data`; YouTube, Instagram, articles, editorial, Weekly Open, shop (Book of the Month and prices), audio_project (the story lines), meetings, external events and the crawl wait for the next daily run. `run_all` exits non-zero only if `build_data` fails. |
| Sync env | Secrets `GOOGLE_API_KEY`, `IG_ACCESS_TOKEN`, `IG_BUSINESS_ID` (empty when unset = harmless); `GV_MT_THREADS=4` (runner vCPUs); `GV_TRANSLATE_MINUTES` (see Timeouts); `GV_MODELS_DIR=$GITHUB_WORKSPACE/.cache/models`; `PYTHONIOENCODING=utf-8`. |
| Timeouts | Job 360 min (GitHub's maximum). The *Decide what to sync* step computes: translation budget `T = clamp(345 − crawl − 35, 10, 40)` min and sync-step timeout `min(crawl + 30 + T + 20, 345)` — e.g. 130 min for a 40-min crawl, 345 for 300 (then T = 10). Setup takes ~5 min, so the step limit leaves ~10 min for the data commit even when the sync overruns (a step timeout is a failure, not a cancellation, so the commit and deploy still run). Build job: 30 min. |
| Model cache | `actions/cache/restore` + `actions/cache/save`, path `.cache/models` (= `GV_MODELS_DIR`), key `translation-models-v1-<OS>-urls-<sha256 of MODEL_URLS in translate.py>` — computed from the source with `ast`, so only a change of the model URLs forces a new ~175 MB download; any other edit of `translate.py` keeps the cache. Saved only after a fresh download and only when both `<pair>/model/model.bin` (> 1 MB) and `<pair>/sentencepiece.model` exist (same test as `model_ready()`), so a failed download is never cached. If fewer than 2 models are present after a successful sync step, the check step raises a `::warning` "Translation models missing" (the only download source is argos-net.com; translation then keeps new titles in their original language). Daily restores keep it from being evicted (7-day rule). Also: pip cache (setup-python), npm cache (setup-node). |
| Commit | `git add -A -- data/raw data/site data/state data/translations/cache.json src/assets/cache` (added, changed **and deleted** files; human-edited files such as `overrides.yml`, `glossary.yml`, `content/`, `config/` are never committed by the bot; `*.tmp` / `*.part` leftovers of a stopped run are git-ignored). Commit only if something changed; message `chore(data): daily content sync YYYY-MM-DD [skip ci]` (Central date). Push with up to 5 retries, `git pull --rebase --autostash -X theirs` between attempts (the bot's fresh generated files win a conflict; human edits to other files are kept). Runs even if the sync step failed, timed out **or the run was cancelled** (`always()`; GitHub gives `always()` steps about 5 minutes after a cancel, and the crawler saves its state on SIGTERM), so a cancelled 300-minute crawl keeps its progress. Deploy still skips cancelled runs. Simulated locally (shallow clone, concurrent human push, conflict in a generated file). **Token scope:** the sync job checks out with `persist-credentials: false`, so the write-access `GITHUB_TOKEN` is *not* in `.git/config` while `pip install` (floating versions) and the sync modules run; this step alone sets `http.https://github.com/.extraheader` from `GH_TOKEN` (covers `push` and `pull --rebase`) and unsets it on exit (`trap … EXIT`). |
| Summary | `run_all` writes a per-module table; a second step (`always()`) writes a per-source table from `data/site/status.json` (skipped while it is fixture data), turns each failing source into a yellow `::warning` (with the days since its last success), warns "Translation is not working" when texts are pending but none were translated, lists **Notes** (up to 2 `stats.warnings` per source that still updated, e.g. one YouTube feed answering 404), **Settings problems** (`status.json` → `problems.meeting` / `problems.recurring_events` / `problems.ics_feeds` / `problems.content_events`: a settings entry build_data skipped or corrected — e.g. a `meeting: skip_dates` value that is not a meeting day — also a yellow `::warning`), **Other calendars (optional, informational)** (`status.json` → `feeds`: each `sources.ics_feeds` entry's state, HTTP status, events and duplicates, and its `notes` as *Check:* lines — each also a `::notice`; a feed that a site's bot protection blocks is only a `::notice`, and feeds are **never** part of `health`, so they never open the "stopped updating" issue) and **New podcast feeds found** (`stats.discovered_feeds` of the podcasts source, plus a `::notice`), and passes the sources that are `ok: false` with no success for **7+ days** (or never) to the `report` job as the job output `health` (one line of JSON). |
| Report | Job `report` (needs `sync`, `!cancelled()`, only on `main`, `permissions: issues: write`, no checkout): keeps **one** issue *"A content source has stopped updating"* — opened when the first source crosses 7 days (GitHub e-mails the repository's watchers), body silently edited after every run, a comment only when a *new* source joins (hidden marker `<!-- failing-sources: … -->`), closed automatically when all recover. Error texts are put in code spans so `@handles` in them never notify GitHub users. `continue-on-error`: never fails the run (e.g. Issues disabled). |
| Deploy | Needs `sync`; runs when sync succeeded, **failed or timed out** (`!cancelled()`: not when a person cancels the run) and only on `main`, so the site always redeploys the last good committed data. Checks out `main` fresh (to include the data commit) → `npm ci` → `actions/configure-pages` → `PATH_PREFIX` = `base_path` with exactly one leading and trailing slash (`/AAGrapevine/` for a project site, `/` for a custom domain or a `user.github.io` repo) and `SITE_URL` = `base_url` without trailing slash → `I18N_STRICT=1 npx @11ty/eleventy` (Tailwind runs inside the build; a UI string missing from `src/_i18n/` fails the build, so Pages keeps the previous site instead of showing a raw key such as `home.spotlight.title`). A sanity step fails the deploy if `index.html`, `es/index.html` or the CSS is missing, and warns above 900 MB. |
| Permissions | Workflow default `contents: read`; `sync` gets `contents: write`; `build-deploy` gets `pages: write` + `id-token: write`; `report` gets `issues: write`. These job-level `permissions` work with the repository's default read-only *Workflow permissions* setting — it does not need to be changed. Every `actions/checkout` in every workflow uses `persist-credentials: false` (zizmor's `artipacked` audit is clean); only the data-commit step logs in, see *Commit*. |
| Pinned actions | `actions/checkout@v7`, `setup-python@v7`, `setup-node@v7`, `cache@v6` (restore/save), `configure-pages@v6`, `upload-pages-artifact@v5`, `deploy-pages@v5` (Dependabot keeps them current). |
| Linting | `actionlint` (with shellcheck) passes on all four workflows: `pip install actionlint-py shellcheck-py`, then `actionlint -shellcheck <path to shellcheck> .github/workflows/*.yml`. |

> **60-day rule.** GitHub disables scheduled workflows in public repos after 60 days without
> repository activity. The daily data commit is activity (the envelope timestamps change every
> run), so this does not happen while the job works. If the job had been failing for two months,
> re-enable it under **Actions → Update & Deploy → Enable workflow**.

### `monthly-digest.yml`

Runs on the 1st of every month at 15:05 UTC — 9:05 AM Central in winter (CST) and 10:05 AM in summer
(CDT), so always after 6 AM Texas time and after the morning updates of `update.yml` (10:17 and
12:07 UTC) — and exits immediately unless the four secrets `SMTP_SERVER`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `DIGEST_TO` exist. A manual run defaults to **preview** (`--dry-run`, uploaded as the
`digest-preview` artifact) and takes an optional *month* (`--month YYYY-MM`); unticking *Preview only*
sends immediately. The public address comes from the Pages API (`gh api repos/:repo/pages`), falling
back to `site.url`.

`scripts/notify/send_digest.py` (standard library only; PyYAML for `config/site.yml` — a small reader is
the fallback — and for the "put it to work" tips of `config/carry.yml`, which are left out without it)
builds the same **edition** as the `/digest/` page (`eleventy/filters/community.js` →
`buildMonthlyDigest`, `monthlyDigestText`) — keep their rules in step:

- **edition** = the Central-time month of the run (or `--month`); its **news window** is the whole
  previous calendar month in America/Chicago (the January edition looks back at December; a news
  date is compared as a Central calendar day, so 11:30 PM CDT on October 31 is October and 12:30 AM on
  November 1 — the day daylight saving ends — is November);
- **news** = the `whatsnew.json` entries whose `wn_date` falls in that month (build_data's rules: no
  launch-day back catalog, no undated documents, next month's magazine issue from the day it is first
  seen), completed from the full `episodes`, `videos`, `pdfs` and `announcements` files by their own
  `date` (`whatsnew.json` keeps only its newest `WHATSNEW_MAX` = 150 entries, about one busy month);
  expired announcements are left out; a YouTube upload of a podcast episode (same Central day, same
  title or season/episode) is folded into the episode as "also on YouTube" (`mergeMediaTwins`);
  magazine stories are only counted (the issue block shows them); Instagram is one pointer line (its
  feed keeps only the newest posts, so a monthly count would be wrong); events are in "coming up";
- **this month's issues**: Grapevine's issue of the month and La Viña's bimonthly issue (key = the
  month or the one before) from `articles.json` — count, free-to-read count, `digest.highlights`
  stories (free to read first, members' stories before "In Every Issue", Area 65 then Texas writers,
  then the magazine's order), the Grapevine theme from the editorial calendar (as on `/monthly/`), up to
  3 tips of `config/carry.yml` for the month and the link to `/monthly/YYYY-MM/`;
- **writers**: `spotlight.json` stories by Area 65 / Texas writers whose `extra.pub_date` is in the
  previous month (every story is in exactly one edition);
- **coming up**: the next committee meeting (up to 60 days ahead, with Zoom ID/passcode and the chair's
  `meeting.note` / `note_es` — without `note_es` the Spanish comes from the meeting event build_data
  translated; an empty note shows no line); the month's events that are not over yet (an all-day event
  until midnight after its last day, a timed one without an end 6 hours after its start, like
  `eventEndMs`; a monthly event from `recurring_events:` once, with "every month"); the weekly open
  meetings (La Viña's from its `starts` date); the number of Grapevine meetings in our Area and nearby;
- **share your story**: deadlines from today through the end of next month, La Viña's 3 open topics of
  the month (the `/monthly/` rotation), the phone story lines of `audio_project.json`;
- Book of the Month (compact; an offer past its last day is left out), the cheapest subscription
  (`shopFromMonthly`) and a pointer to the daily quote on the home page;
- English half then Spanish half (La Viña first there), from the `i18n` fields build_data produced;
  `digest.per_section` items per list + "and N more";
- subject `Grapevine / La Viña — October 2026 · Novedades de octubre`; multipart HTML + plain text,
  RFC 2047 headers, `List-Unsubscribe`, several recipients → Bcc;
- nothing new last month (no news and no writers) → nothing sent (exit 0): the issues, dates, deadlines
  and Book of the Month come round every month, so on their own they never send an e-mail (nor do they
  when the daily updates have stopped). Exit 1 = SMTP failure, 2 = not configured.

```bash
python -m scripts.notify.send_digest --dry-run                          # → .tmp/digest.html + .tmp/digest.txt
python -m scripts.notify.send_digest --dry-run --month 2026-10 --as-of 2026-10-01
MONTHLY_NOW=2026-10-01T15:05:00Z npx @11ty/eleventy                     # the /digest/ page of that edition
```

### `link-check.yml`

Sundays. Builds the site with `PATH_PREFIX=/`, runs **lychee** over `_site/**/*.html` with
`--root-dir _site` (internal links are checked on disk), `--host-concurrency 2`,
`--host-request-interval 1s`, a 3-day response cache, and accepts 403/429 (bot walls). High-volume
or bot-hostile hosts are excluded (aagrapevine.org, aalavina.org, YouTube, Instagram, Google,
Zoom, podcast platforms, social networks). Then it checks every URL under `links:` in the config
with the project's `PoliteSession` (robots.txt + 5 s Crawl-delay). Results: run summary + a single
issue *"Broken links found by the weekly check"* that is commented on while problems persist and
closed automatically when clean. It never fails the workflow.

Checked locally (2026-09-23): lychee 0.24.2 — the version `lychee-action@v2` installs — accepts every
flag used; `lychee --offline --root-dir <abs path>/_site '_site/**/*.html'` on a `PATH_PREFIX=/`
build resolved all internal links (0 errors), and a page with a deliberately broken link was flagged.
To reproduce on Windows, pass the root dir as `C:/…` (Git Bash: also `MSYS_NO_PATHCONV=1`).

### `check.yml` — Code check

On every `pull_request`, on every **push to `main`** that touches `scripts/**`, `tests/**`, `src/**`
(not `src/assets/cache/**`), `eleventy/**`, `eleventy.config.js`, `config/**`, `content/**`,
`data/translations/{overrides,glossary}.yml`, `package*.json`, `requirements.txt` or the workflow
itself (most work here is pushed straight to `main`, so without this the tests would never run), and
on `workflow_dispatch`. `permissions: contents: read`, nothing published; the bot's data commits
never trigger it (pushed with `GITHUB_TOKEN`). Job `build`: `npm ci` →
`PATH_PREFIX=/<repository name>/ I18N_STRICT=1 npx @11ty/eleventy` → the same sanity checks as
*Update & Deploy* (`index.html`, `es/index.html`, `assets/css/main.css`). Job `tests`: Python 3.12,
`pip install -r requirements.txt`, `python -m unittest discover -s tests -v` (no translation models
are downloaded, so model tests are skipped; the rest runs offline in a few seconds), then
`send_digest --dry-run` into the runner's temp folder (a smoke test of the optional e-mail). Dependabot
PRs therefore show a ✓/✗ before merging (the chair merges only on green), and a push that breaks the
tests shows a red ✗ on its commit. A push runs this alongside *Update & Deploy*, which still deploys:
a red ✗ here means "fix or revert that change", not "the site is down".

### `dependabot.yml`

Monthly grouped PRs for GitHub Actions and npm (minor/patch only), checked by `check.yml`. Python packages in
`requirements.txt` float within their current **major** version (`>=x,<next-major`), so each daily run picks up
minor/patch releases automatically; a new major version is only installed after someone raises the cap by hand.
`yt-dlp` is deliberately uncapped — it must keep up with YouTube changes.

## Sync modules

All modules follow the same conventions (see [DATA_SCHEMA.md](DATA_SCHEMA.md)): `main(argv)`,
`python -m scripts.sync.<name>`, output via `save_raw()`, cumulative `merge_items()` (preserves
`first_seen`, never drops items on a bad day), `ok=false` + error message on failure, time-boxed,
`--dry-run`. Each module's docstring is the detailed reference.

| Module | Source → output | Normal path | Fallbacks / safety | Useful flags |
|---|---|---|---|---|
| `articles.py` | aagrapevine.org `/magazine`, aalavina.org `/la-revista` → `articles.json` | Issue hub pages (titles, bylines, public teasers, card images); each article page fetched once for issue/topic/section/paywall flag | Home page / `/revista-2` if a hub fails; missing fields retried ≤ 3× a week apart. **Never stores article bodies.** | `--max-details 40`, `--max-seconds`, `--no-details`, `--only gv\|lv` |
| `crawl.py` (+ `crawl_rules.py`, `crawl_pdf.py`) | both sites → `pdfs.json`, `data/state/crawl-state.json`, `src/assets/cache/pdf/` | Sitemaps → priority queue (hubs daily, new pages, changed `<lastmod>`, events, re-checks every `recheck_days`); conditional GETs; ~30 % of time on PDF work (download ≤ `pdf_details_per_run` new PDFs ≤ `pdf_max_mb` for page count/title/thumbnail; HEAD others) | Resumes daily from state; a PDF becomes `gone` only after **two** failing checks at least 24 h apart (404/410, or an HTML page where the file was — `gone_strike_at` marks the first); a gone PDF that a page still links is re-checked after a week, later monthly, and comes back when it answers; a PDF on **another** site whose host has not answered at all for 30+ days (4+ tries, `head.unreachable_since`) is gone too — the magazine sites' own files are never retired that way; skip rules for login/cart/paywalled paths; PDF author metadata deliberately not read (anonymity) | `--minutes N` (0 = rebuild from state, no network), `--details`, `--url`, `--max-pages` |
| `podcasts.py` | shows in `sources.podcasts` (`gv` AA Grapevine's Podcast, `wo` Grapevine Weekly Open AA Meeting) → `podcasts.json` (category = show key) | RSS via requests + feedparser | Previous episodes kept on a bad feed; an episode that leaves the feed is `gone` only when its audio 404/410s; weekly discovery of new show feeds, never auto-added: listed under *New podcast feeds found* in the *Update & Deploy* run summary (with a `::notice`), and in `stats.discovered_feeds` of `podcasts.json` / `status.json` (the `/status/` page does not show them) | `--limit`, `--discover`, `--no-discover` |
| `youtube.py` | channel `UCI9uFLJ__aXT3-At0PlPWUQ` → `youtube.json` | Channel + uploads + playlist RSS every run | yt-dlp full listing weekly and a few dozen per-video detail fetches per run; if yt-dlp is blocked on CI IPs, RSS alone keeps the site current; deletions confirmed via oEmbed only after a complete listing | `--backfill`, `--no-backfill`, `--details`, `--backfill-minutes` |
| `instagram.py` | two accounts → `instagram.json`, `src/assets/cache/ig/` | Graph API Business Discovery if `IG_ACCESS_TOKEN`+`IG_BUSINESS_ID`; else the public profile **embed** page (a few requests/day) | web_profile_info JSON → profile HTML → optional RSSHub mirrors → post embeds; keeps last posts when all fail. `sources.instagram.anonymous: false` (or env `IG_ANONYMOUS=0`) disables every non-API request: only the API + `content/instagram.yml` | `--strategies`, `--account`, `--keep`, `--no-enrich`, `-v` |
| `drive.py` (+ `drive_listing.py`) | public Drive tree under `drive.root_folder_id` → `drive.json` (documents, photos, flyer events, announcements) | Public "embedded folder view" HTML (no key) | Drive API v3 when `GOOGLE_API_KEY` is set (falls back per folder); a folder only counts as read when Drive returned a real folder page, so a network error never deletes items; spreadsheets and `PRIVATE`/`(Responses)` names never published | `--no-api`, `--max-depth`, `--max-minutes`, `--include-loose` |
| `editorial.py` | `/contribute`, `/temas-sugeridos` → `editorial.json` | Grapevine editorial calendar (themes + deadlines); La Viña evergreen topics | A parsed page replaces that publication's topics; a failed page keeps the previous ones | `--only`, `--gv-html FILE` |
| `weekly_open.py` | `/grapevine-weekly-open` → `weekly_open.json` | Parses day/time/Zoom ID/passcode, converts to Central | Previous item kept on failure | `--html FILE` |
| `audio_project.py` | aagrapevine.org `/audio-portal`, aalavina.org `/graba-tu-historia` + `/instrucciones-graba-tu-historia` (`sources.grapevine.audio_project`, `sources.lavina.record_*`) → `audio_project.json` | The story lines: phone number, keys to press, length, the e-mail address for recordings (Cloudflare-protected → decoded), the no-speaker-recordings note, Grapevine's playlists, La Viña's copyright sentence. 3 requests a day | A page not fetched or not understood keeps that part's previous data (`ok: false`); when only La Viña's instructions page fails, its steps come from the previous run and the rest is fresh (`ok: true`, a note in `stats.warnings`) | `--dry-run`, `--html-dir DIR`, `--save-html DIR` |
| `announcements.py` | `content/announcements/*.md` → `announcements.json`; `content/events/*.md` → `manual_events.json` (hand-written `title_es` / `summary_es` — or `title_en` / `summary_en` — → `extra.own_i18n`, used by build_data instead of a machine translation) | Markdown + YAML front matter (no network) | The folder is the source of truth; a file with a formatting mistake is skipped and reported on `/status/` instead of breaking the run | `--dry-run` |
| `quote.py` | aagrapevine.org `/`, aalavina.org `/` (`sources.<pub>.quote_page`) → `data/raw/quote.json` (items + 14-day `history`, raw only) → `data/site/quote.json` (items) | The `#quote-of-the-day` teaser (`article.node--type-quote`): heading date (year inferred around today, Central), quote text (outer quotation marks cleaned, never translated), attribution / source split at "From:" / "De", the publication's own e-mail sign-up link. One request per site (none when an earlier module of the run read that page), in the full AND the quick run | Falls back to the view embed near the top of the page; a publication that fails keeps its previous quote (and last known sign-up link), `ok=false`; an older quote than the one known never replaces it | `--dry-run`, `--only gv\|lv`, `--html-dir DIR`, `--save-html DIR` |
| `translate.py` | all raw titles/summaries/bodies → `data/translations/cache.json` | CTranslate2 + Argos 1.0 models (auto-downloaded to `GV_MODELS_DIR`), sentence splitting with per-sentence ¿…? / ¡…! pairing, protected spans (URLs, handles, times, codes, sizes like 8.5 x 11, "Firstname X." names), glossary (+ built-in Step/Tradition ordinals, "[Season N, Episode M]" → "[Temporada N, Episodio M]"), sentence case for Spanish titles / Title Case for English titles, an output guard (rejects repeated-word loops, changed numbers, entity leaks → keeps the original) and a vocabulary guard (never outputs "coger" — vulgar in Latin-American Spanish) | Cache hits never re-translate; `overrides.yml` always wins; glossary edits re-translate only affected texts; bump `ENGINE_VERSION` to re-translate all | `"text" --to es`, `--download`, `--stats` |
| `build_data.py` | `data/raw/*` + content/ + config → `data/site/*.json` | Adds `i18n`, `machine`, `is_new`; builds events (12 months of committee meetings + `recurring_events:` from the config + flyers + manual + external + the optional `sources.ics_feeds` calendars, each real event once — see [Events](#events-several-days-to-be-confirmed-places-outside-calendars)), `whatsnew.json` (newest 150), `status.json` (+ `feeds`: the health of each outside calendar) | Only writer of `data/site/`; templates read nothing else. A bad `meeting:` / `recurring_events:` / `ics_feeds:` entry (or a `skip_dates` value that is not one of the rule's days) is skipped and listed in `status.json` → `problems` (and as a **Settings problem** in the run summary). Its only network request: each `.ics` feed, at most once a day (`--offline`: none) | `--offline`, `--no-translate` |
| `run_all.py` | orchestrator | Runs every module in turn, isolating failures, then translation + `build_data` | A crashing module is recorded as `ok=false` (see `run_module`) and the rest continue | `--crawl-minutes N`, `--quick` |
| `meeting.py` | config `meeting:` (+ the rule engine for `recurring_events:`) | `upcoming_rule_dates(MonthlyRule, count, tz)`: the Nth weekday (1–5, or −1 = last) of every month, start–end on the local clock → UTC instants (right across DST changes and month/year ends), `skip_dates`. Used for the committee meeting (3rd Wednesday 7–8 PM Central by default) and every recurring event | `check_skip_dates()`, shared by the meeting and every recurring event: a skip date that is not a date, or not the rule's day of its month, is ignored and noted ("skip date “2026-12-17” is not the 3rd Wednesday of its month — ignored (that month's is 2026-12-16)") → `problems.meeting` / `problems.recurring_events` | — |

## Recurring events

`config/site.yml` → `recurring_events:` lists what the committee does every month (the GV/LV booth
at CityWide Dallas: 2nd Saturday, 17:00–20:00 Central). Chair-facing instructions are in the
[README](../README.md#add-a-recurring-event); the data fields in [DATA_SCHEMA.md](DATA_SCHEMA.md).

| Where | What happens |
|---|---|
| `build_data.recurring_specs()` | Validates each entry. **Skipped** (reason in the log and in `status.json` → `problems.recurring_events`, shown as a *Settings problem* in the run summary): not a mapping, no title, a `week_of_month` other than 1–5 / −1 (also `"2nd"`, `"segundo"`, `"last"`, `"último"`), an unknown `weekday` (English or Spanish, plural and accents accepted), no readable `start` (`parse_hhmm`: `"17:00"`, `"5 PM"`, unquoted `17:00`), a duplicate `key`. **Noted, still shown**: an unreadable / not-later `end` (→ one hour), a `skip_dates` value that is not a date, or not one of the event's own days — e.g. the Sunday or the 1st Saturday for a 2nd-Saturday event — (ignored; the note names that month's real date), a `months_ahead` outside 1–24 (→ 6), a `url` / `online_url` that is not http(s) (left out; the event links to `/events/`). A missing `key` is made from the title; keys are slugified and cut to 32 characters so the card anchor keeps its date. An exception anywhere is caught in `build_events` (same as `meeting:`). |
| `build_data.recurring_events()` | `meeting.upcoming_rule_dates()` → the next `months_ahead` dates **plus** the dates of the last 90 days. One event per date: id `ev:recurring:<key>:<YYYY-MM-DD>`, source `committee`, category `recurring`, fixed i18n (the config's `title`/`title_es`, `summary`/`summary_es`; a missing language is machine-translated once and marked in `machine`) and a rule-written `recurrence_label` (the committee meeting's wording: "Every second Saturday of the month · 5:00 – 8:00 PM") plus `extra.rule`, from which the pages write the same line with the meeting's helpers. `build_events` marks the past ones `past: true` without counting them in `PAST_EVENTS_KEEP`. Never `is_new`, never in `whatsnew.json` (`SCHEDULED_EVENT_CATEGORIES`). |
| `/events/` (`normalizeEvents` in `eleventy/filters/committee.js`) | Every upcoming date as its own card in the **NETA 65 events** group (`GROUP_OF.recurring = "neta"`), with an "Every month" badge, the day rule, the place and the external "Event details" link; "add to calendar" per date. Past dates are left out of the *Past events* list (`whereNot("recurring", true)`). |
| Calendar feeds (`events-ics.11ty.js`) | One `VEVENT` per date — like the committee meetings — with a stable `UID` from the id (`ev-recurring-<key>-<date>@neta65-gvlv`, `-es` in the Spanish feed), UTC `DTSTART`/`DTEND`, `LOCATION`, `URL`, and the rule line in `DESCRIPTION`. Chosen over one `RRULE` series because UTC instants need no `VTIMEZONE` (an `RRULE` on a UTC start would drift an hour at every DST change; with `TZID` it needs a `VTIMEZONE` that Outlook.com handles unevenly), a skipped month is simply absent (no `EXDATE` quirks), and each date can change on its own. The feed carries the 90 past days (subscribers keep them, as for the committee meetings) and `months_ahead` dates ahead; `REFRESH-INTERVAL` 12 h rolls new dates in. |
| Home, search, digest, GV/LV report (`/monthly/#report`), e-mail | Only the **next** date of each series (`extra.series`): `homeEvents` (links to the card on `/events/`; the next date of every series **always keeps a place** in the home row of 4 — the other places go to the soonest one-off events, at least one of them when there is any, then at most one more committee meeting; shown by date), the search index (one `event` entry, found by "every month" / "cada mes" too), the monthly digest (`community.js`), the report (`report.js` → `upcomingEvents`), `send_digest.collect()`. In the e-mail a recurring event does not count toward "anything new?" (`total_count`). `/meetings/` shows an "Also every month" box (`cmRecurringNext`). |

## Events: several days, "to be confirmed", places, outside calendars

Chair-facing instructions: [content/events/README.md](../content/events/README.md) and the
[README](../README.md#6-announcements-and-events-without-drive-optional); the data fields:
[DATA_SCHEMA.md](DATA_SCHEMA.md) (§3, events).

| Topic | How it works |
|---|---|
| Several days (the Area assemblies, Fri–Sun) | A content/events file with `start: 2027-03-19` and `end: 2027-03-21` (dates only) is an all-day event whose `end` is the **last** day. `build_data.event_end_ts` puts its end at 23:59 Central on that day (like every event it keeps `past: false` one more day: the `build_events` cutoff is now − 24 h); on the pages `normalizeEvents` (`eleventy/filters/committee.js`) sets `multiDay`, a range tile ("MAR · 19–21 · Fri–Sun"), `rangeLabel` ("Fri, Mar 19 – Sun, Mar 21, 2027" / "Vie, 19 de mar – dom, 21 de mar de 2027") and `timeLabel` "3 days"; the card's `data-cm-expire` is midnight after the last day. `/events.ics`, `/es/events.ics`, the per-event ".ics file" button (`CM.downloadIcs`) and the Google / Outlook links use DATE values with the **exclusive** end (`DTEND;VALUE=DATE:20270322`). Home (`homeEventInfo`, `homeEvents` via `chicagoDayEndMs`), the digest page, the WhatsApp / e-mail text and the district report (`eventWhen`: "Fri, Mar 19 – Sun, Mar 21") and the monthly e-mail (`event_row`) show the range; the digest keeps an event that is still going on (`eventEndMs`). A timed event that only runs past midnight is not "several days" (it must last more than 18 hours). |
| Details to be confirmed | content/events `tentative: true` (also `yes`, `sí`) → `extra.tentative: true` (announcements.py); an outside calendar's `STATUS:TENTATIVE` does the same. Shown as the badge "Details to be confirmed" / "Detalles por confirmar" (`ui.tentativeBadge`, class `badge-tbc`: dashed outline; the explanation is its tooltip and screen-reader text) on `/events/` cards, the home row, the digest page, the "next event" card of `/announcements/` and search results (index flag `tb`); as " · Details to be confirmed" in the WhatsApp / e-mail text, the district report and the monthly e-mail. Calendar files: `STATUS:TENTATIVE` (every other event `STATUS:CONFIRMED`), and the first line of the description says it (Google / Outlook links cannot carry a status). Deleting the line makes the event confirmed on the next run. |
| The place in both languages | A place is **never** machine-translated. content/events `location_es` (in an English file) / `location_en` (in a Spanish one) → `extra.own_i18n.location` → build_data writes `i18n.location` (`location_pair`). A place that is not known yet ("Venue to be announced", "TBA", "Lugar por anunciarse", "Por confirmar" — `location_is_tba`) gets `extra.location_tba: true` and, when the other language was not written, the site's own words ("Lugar por anunciarse" / "Venue to be announced"). Such a place is shown as plain italic text with an hourglass (no map pin), is left out of `LOCATION` in the calendar files and of the Google / Outlook links (it goes into the description instead) and of the past-events list. Every page reads `i18n.location[lang]`, falling back to `extra.location`. |
| Outside calendars (`sources.ics_feeds`) | `build_data.ics_events()`: per feed ONE request (see [Crawl politeness](#crawl-politeness)); the answer is `ok` (a calendar file that parses), `blocked` (HTTP 401 / 403 / 429, or Cloudflare's "Just a moment…" check: `cf-mitigated: challenge`, `challenges.cloudflare.com`) or `error`. The last good copy of each feed is kept in `data/state/ics_feeds.json` (with `attempted`, `state`, `http_status`, `error`, and `fetched` = the last success) and used while the feed fails. `_parse_ics` reads The Events Calendar's export (VTIMEZONE + `DTSTART;TZID=America/Chicago`, `UID` `<post id>-<start>-<end>@neta65.org`, `URL` = the event page, `LOCATION` without ", United States", `ATTACH;FMTTYPE=image/…` = the flyer, `CATEGORIES` → tags; CANCELLED left out, RRULE expanded, an all-day `DTEND` is exclusive → last day). `category:` `neta65` (or `ics`) puts the events with the NETA 65 events; `gv-calendar` / `lv-calendar` with the GV/LV calendars. Health → `status.json` `feeds` → the "Other calendars we read" card on `/status/` and the informational block of the run summary. Feeds are not content sources: a blocked feed never counts as failed, never becomes a warning and never opens the "stopped updating" issue. |
| One event, several sources | `merge_feed_duplicates()` in `build_events`, against everything already on the calendar: content/events files, dated Drive flyers, the committee meeting and every `recurring_events:` date (so a feed that also lists the CityWide Dallas booth or the meeting never doubles them). Two events are the same only when they **start the same local day** and either (1) link the same event page — `event_url_key()` ignores the scheme, `www.`, the trailing slash, `?query` and `#fragment` (`neta65.org/event/<slug>`) — or (2) have the same shape (`_same_shape`: both all-day, or both timed and starting at most `TITLE_MATCH_MAX_GAP_H` = 2 hours apart; never one over several days against one on a single day), are not in two different cities, and have titles that name the same event (`similar_titles`: the telling words — the kind of event included: "workshop", "booth", "assembly" — shared / all ≥ 0.75, the shared city and the year ignored, `lv` / `gv` expanded, "grapevine", "la viña", "neta 65" ignored; word for word when a city is not known, e.g. a place "to be announced"). "LV Writing Workshop" ~ "La Viña Writing Workshop (in Spanish) — Fort Worth"; not ~ "LV Recording Workshop"; "Grapevine Workshop at the Spring Assembly" (7 PM on the assembly's Friday) is **not** the assembly. The same event page on another date is another date (a series, a page used again for a new workshop, or a date that changed): the feed event is kept. The hand-written event wins and keeps its own Spanish; the feed only fills what it leaves out — `flyer_url` / `flyer_thumb`, `online_url` and the event-page `url` only on a sure match (same page, or the same start); a missing place, or one "to be announced" (on a sure match: the feed's venue replaces it); a missing end of the same kind — recorded in `extra.also_in_feed` + `extra.feed_match` (`url` / `title`). Nothing is copied onto the meeting or a recurring date. What the feed says that a file does not (its event page on another date while the file is still upcoming, another start time, a venue the file calls "to be announced") → `feeds[].notes` → a *Check:* line and a `::notice` in the run summary, so the chair updates the file. The same event in two feeds is kept once. `feeds[].duplicates` counts them. Proven by `tests/test_events_feeds.py` with the six real workshop pages and the negative cases (a workshop / booth on an assembly's first day, with a known and with a TBA venue; the same page on another date; the booth and the meeting in a feed). |
| neta65.org is blocked | Since Sept 2026 every automated request to neta65.org (the iCal export, `webcal://…&ical=1`, `/wp-json/tribe/events/v1/events`) gets HTTP 403 "Just a moment…" from Cloudflare. The site does not try to get around it. The fix is on the Area's side: a Cloudflare WAF custom rule with the action **Skip** for requests whose query string contains `ical=1` (or whose User-Agent is the robot's); the next daily run then reads the feed by itself. Until then a NETA 65 workshop or assembly shows on the Events page only after the committee adds it to `content/events` by hand (the /status/ card says so and links content/events/README.md). |

## Crawl politeness

| Host | Rule we follow | Numbers |
|---|---|---|
| www.aagrapevine.org + www.aalavina.org | robots.txt obeyed; `Crawl-delay: 5` applied **across both hosts and all modules together** (one `shared_session()`); **each page at most once per run**: the session keeps every page it read for the rest of the run (`PoliteSession.get_text` / `remembered`), so what one module read — the home pages (quote, podcast discovery), `/BOTM` and `/libro-del-mes` (shop), `/grapevine-weekly-open`, the sitemap (external events) — is reused by the others and by the crawl; honest User-Agent with a contact URL; conditional GETs; skip login/cart/search/paywalled paths | 5 s between requests → 720 requests/hour max. Default 40 min/day ≈ 450 pages/day. **First coverage is complete** (Sept 2026): 3,445 pages known, 3,441 crawled, queue empty (the other 4 links return errors and are retried now and then), 130 PDFs. From now on the daily run only re-checks pages and picks up new ones — a 300-minute catch-up run is only needed after `crawl-state.json` is deleted. Pages are re-checked every `recheck_days` (21) unless the sitemap says they changed; hub pages daily. PDFs: ≤ `pdf_details_per_run` (40) downloads/run, ≤ `pdf_max_mb` (60 MB) each. |
| YouTube | Fixed set of feed URLs, ~1 s apart, short timeouts; yt-dlp time-boxed (6 min/run) | ~10–90 requests/day |
| Instagram | Public embed pages only (unless the official API token is set), ≤ a few requests per account per day, post-embed look-ups capped by `enrich_per_run`; no hammering on HTTP 429. Instagram's terms discourage automated collection — `anonymous: false` turns all of it off | ~2–30 requests/day |
| Google Drive | One request per folder per run (≤ 800 folders, 15 min budget) | tens of requests/day |
| Podcast feeds | One RSS request per show (2 shows); a few audio HEADs for vanished episodes | ~2–10 requests/day |
| Outside calendars (`sources.ics_feeds`, e.g. neta65.org's workshop calendar) | One plain GET per feed per run with the robot's own User-Agent (`sources.crawler.user_agent`), no retries, no robots.txt fetch (a published calendar file is meant for calendar programs); a feed is asked again only 20 hours later, whether it worked or not (`ICS_RETRY_HOURS_*` in `build_data.py`; the last good copy is used in between), so push-triggered and manual runs add nothing. **Never** get around a bot wall (no browser automation, proxies or faked headers) | ≤ 1 request/day per feed |
| Link check (weekly) | lychee: 2 concurrent / 1 s apart per host; config links via `PoliteSession` | a few hundred requests/week, none to the two magazine sites except ~20 config links at 5 s |

## Failure handling (design guarantees)

1. **Nothing disappears on a bad day.** `merge_items()` keeps every known item; a module that fails
   writes `ok=false` + `error` and keeps its previous items. Items are only marked `gone` after an
   explicit confirmation (404/410, oEmbed, a successful folder listing without the file). A PDF needs
   **two** failing checks at least 24 hours apart (404/410, or an HTML page where the file was), so one
   bad answer during a site update never hides it; a gone PDF that a page still links is checked again
   after a week, then monthly, and returns when it answers. A PDF on another website whose server has
   not answered at all for 30+ days (after 4+ tries) is gone too; files on aagrapevine.org /
   aalavina.org are never retired just because the site is down.
2. **One broken source never stops the others** (`run_module()` catches everything and records it).
3. **Atomic writes**: `write_json()` writes a temp file then `os.replace()`, so a killed run never
   leaves half a JSON file (`*.tmp` and half-downloaded `*.part` thumbnails are git-ignored, so the
   data commit never picks them up).
4. **Partial progress is kept**: the commit step runs even when the sync step failed, hit its
   time budget or the run was cancelled, so e.g. 35 minutes of crawling are not lost.
5. **The site always deploys** the latest committed data, even if today's sync failed.
6. **A bad settings edit cannot take the site down**: the build fails, GitHub Pages keeps serving
   the previous deployment.
7. **Health is visible**: `/status/` page, the run summary table, `::warning` annotations, the badge —
   and a source that has not updated for 7 days opens the issue *"A content source has stopped
   updating"* (the `report` job), because a run stays green while only one source fails.
   GitHub's failure e-mails for the *scheduled* run go to the user who last enabled the workflow
   (or last edited its `cron:` line): after a hand-over, the new chair disables and re-enables it.

## Repository size

Measured per item (JSON, indented one field per line): ~1.1–1.8 KB in `data/raw`, a bit more in
`data/site` (translations added). Thumbnails: WebP ≤ 480 px, ~16–23 KB each. The first full PDF
crawl is done, so these are real numbers, not estimates:

| Part | Measured (Sept 2026) | Growth |
|---|---|---|
| `data/raw` + `data/site` (130 PDFs, 528 videos, 295 episodes, 224 magazine stories, Instagram, events) | ~5 MB (1.6 + 3.3 MB) | a few MB a year (mostly new stories, episodes and videos) |
| `data/state/crawl-state.json` (3,445 pages) | ~1.4 MB | only when the two sites add pages |
| `data/translations/cache.json` | ~0.7 MB | grows with new titles |
| `src/assets/cache/` thumbnails (`pdf` 129 · `ig` 26 · `articles` 22 · `pod` 3) | ~3.5 MB | ~20 KB per new thumbnail |
| Git history growth (daily commits are small line-level changes, delta-compressed) | — | roughly 50–150 MB per year |

GitHub recommends repositories stay under 1 GB (hard warnings start around 5 GB); GitHub Pages
sites must stay under 1 GB (the deploy step warns at 900 MB). CI clones are shallow (depth 1), so
history size does not slow the daily run.

**If the repository ever gets too big** (years from now), squash the history. This rewrites history —
do it only if you understand it, with the scheduled workflow disabled:

```bash
git checkout --orphan fresh main          # same files, no history
git commit -m "Fresh start: history squashed on $(date +%F)"
git branch -M fresh main
git push --force origin main
```

## Running locally

Requirements: **Python 3.12+**, **Node 20+ (22 recommended)**, Git.

**Windows (PowerShell):**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm ci
$env:PYTHONIOENCODING = "utf-8"          # Windows console + non-ASCII text
python -m scripts.sync.run_all --crawl-minutes 5
npm start                                 # http://localhost:8080 (live reload)
```

**macOS / Linux:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm ci
python -m scripts.sync.run_all --crawl-minutes 5
npm start
```

Single modules and tools:

```bash
python -m scripts.sync.youtube --dry-run           # any module: fetch + print, write nothing
python -m scripts.sync.crawl --minutes 0           # rebuild pdfs.json from state, no network
python -m scripts.sync.translate "Dear Grapevine" --to es
python -m scripts.sync.translate --download        # fetch the models (~175 MB) once
python -m scripts.notify.send_digest --dry-run     # preview the e-mail in .tmp/
ONLY=library,search npx @11ty/eleventy             # build just some pages (fast)
```

Build exactly as GitHub Pages does for a project site:

```powershell
$env:PATH_PREFIX = "/AAGrapevine/"; npx @11ty/eleventy      # PowerShell
```
```bash
PATH_PREFIX=/AAGrapevine/ npx @11ty/eleventy                # macOS / Linux
MSYS_NO_PATHCONV=1 PATH_PREFIX=/AAGrapevine/ npx @11ty/eleventy   # Git Bash on Windows
```

(Git Bash rewrites values that look like paths — `/AAGrapevine/` becomes
`C:/Program Files/Git/AAGrapevine/` — unless `MSYS_NO_PATHCONV=1` is set.)

**Service worker (offline use).** The built site registers `/sw.js` (README → "Install the app,
offline use and Data saver"). It works on `http://localhost` too, and pages you open while testing are
kept in the browser. Styles and scripts are linked as `…?v=<build.version>` — a fingerprint of the code
(`src/_data/build.js`: `src/`, `eleventy/`, `config/`, `eleventy.config.js`, `package-lock.json`; not
`data/` or `src/assets/cache/`), which is also the worker's version. To start from a clean first visit:
DevTools → Application → Storage → **Clear site data**.

Please keep local test runs short (`--crawl-minutes 5`, `--dry-run`): the magazine sites ask for
5 seconds between requests and the daily job already visits them.

## Resetting state

| Goal | Do this |
|---|---|
| Redeploy without syncing much | Run *Update & Deploy* with **skip_crawl** ticked |
| Re-crawl the two sites from scratch | Delete `data/state/crawl-state.json` (commit), then run with `crawl_minutes = 300` |
| Re-read one source completely | Delete `data/raw/<source>.json` (loses `first_seen` dates → everything looks "new" for 14 days) |
| Re-translate one text | Add it to `data/translations/overrides.yml` (preferred) |
| Re-translate everything | Delete `data/translations/cache.json`, or bump `ENGINE_VERSION` in `translate.py` |
| Force a fresh model download | Delete the `translation-models-…` entry under **Actions → Caches** (the key changes by itself when `MODEL_URLS` in `translate.py` changes) |
| Undo a bad data commit | `git revert <sha>` (or GitHub → commit → *Revert*), then run the workflow |
| Roll the website back | **Actions** → an older successful run → *Re-run jobs* re-deploys what `main` has *now*; to publish old content, revert the commits first |

## Adding a new source

1. **Module:** create `scripts/sync/<name>.py` following an existing one (e.g. `podcasts.py`):
   `main(argv)` with argparse (`--dry-run`), build Items with `make_item()`, merge with
   `merge_items()`, write with `save_raw("<name>", …, ok=…, error=…, stats=…)`, and end with
   `if __name__ == "__main__": raise SystemExit(run_module("<name>", main))`.
   Use `shared_session()` for aagrapevine.org / aalavina.org; `PoliteSession()` elsewhere.
   Put any settings in `config/site.yml` under `sources:`.
2. **Contract:** add the source/kind/`extra` fields to [DATA_SCHEMA.md](DATA_SCHEMA.md).
3. **Pipeline:** register the module in `run_all.py` (decide whether it belongs in `--quick`), and
   map its raw file to a site file in `build_data.py` (plus a status label).
4. **Templates:** add the site file name to `FILES` in `src/_data/db.js`, then use
   `db.<file>.items` in a page; add UI strings to `src/_i18n/`.
5. **Test:** `python -m scripts.sync.<name> --dry-run`, then a real run, `npm start`, check `/status/`.
6. **Digest (optional):** add a group in `NEWS_GROUPS` / `GROUPS` (`scripts/notify/send_digest.py`) and in
   `MONTH_NEWS` (`eleventy/filters/community.js`), so the e-mail and the `/digest/` page stay the same.

## Environment variables and secrets

| Name | Where | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | secret → sync | Drive API (exact dates/sizes); optional |
| `IG_ACCESS_TOKEN`, `IG_BUSINESS_ID` | secret → sync | Instagram Graph API; optional |
| `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `DIGEST_TO`, `DIGEST_FROM`, `DIGEST_REPLY_TO` | secrets → digest | Monthly e-mail; optional |
| `PATH_PREFIX` | build | URL folder of the site (`/AAGrapevine/` or `/`); set automatically from Pages |
| `SITE_URL` | build, digest | Public address (from Pages); overrides `site.url` where supported |
| `GV_MODELS_DIR` | sync | Translation model folder (default `.cache/models`; the workflow sets it to the same folder it caches) |
| `GV_MT_THREADS` | sync | CPU threads for translation (workflow: 4) |
| `GV_TRANSLATE_MINUTES` | sync (`build_data`) | Time budget for new translations (default 40; the workflow lowers it on very long crawls) |
| `GV_CRAWL_MINUTES` | sync (`run_all`) | Crawl time box when `--crawl-minutes` is not given |
| `IG_ANONYMOUS` | sync | `0` = same as `sources.instagram.anonymous: false` |
| `IG_GRAPH_VERSION` | sync | Graph API version (default from config, `v21.0`) |
| `GV_LOG_LEVEL` | sync | `DEBUG` for verbose logs |
| `ONLY` | build (local) | Build only some `src/pages/*` files |
| `I18N_STRICT` | build | Fail on missing UI strings (always on in `update.yml` and `check.yml`; unset locally = the raw key is shown instead) |
