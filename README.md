# Grapevine / La Viña — NETA 65 Committee website

[![Update & Deploy](https://github.com/MKP715/AAGrapevine/actions/workflows/update.yml/badge.svg)](https://github.com/MKP715/AAGrapevine/actions/workflows/update.yml)

**Website:** <https://mkp715.github.io/AAGrapevine/> · **En español:** <https://mkp715.github.io/AAGrapevine/es/>

The website of the **Northeast Texas Area 65 (NETA 65) Grapevine & La Viña Committee**.
It **updates itself every morning** and is **fully bilingual (English + Spanish)**. Nobody has
to hand-edit pages, retype flyers or translate anything any more.

> **Setting it up for the first time?** Use the [first-run checklist](#11-first-run-checklist)
> (details in [docs/SETUP-GITHUB.md](docs/SETUP-GITHUB.md), about 15 minutes).

## Resumen en español

Sitio web del **Comité de Grapevine y La Viña del Área 65 del Noreste de Texas**.
**Se actualiza solo todas las mañanas** (≈ 5 a. m., hora del Centro), en **inglés y español**.

- Trae automáticamente los **artículos nuevos** de Grapevine y La Viña, **todos los PDF** de aagrapevine.org y
  aalavina.org, los episodios de **los dos podcasts** (el de AA Grapevine y la **Reunión Abierta
  Semanal de Grapevine**), los **videos de YouTube** y las **publicaciones de Instagram**, y lo
  **traduce todo** (inglés ⇄ español) con software libre.
- **Lo único que usted hace:** subir archivos a las carpetas del comité en **Google Drive**
  (dentro de la carpeta del Panel 77). Al día siguiente aparecen en el sitio.
  - Un **volante** con la fecha al inicio del nombre se convierte en **evento** — con hora y lugar si
    los escribe: `2027-03-14 Asamblea de primavera 9am @ Tyler TX.pdf`
  - Un **Google Doc** en `anuncios` se convierte en **anuncio**; `(fijado)` lo deja arriba y
    `(hasta 2027-02-01)` lo oculta después de esa fecha.
  - Cada **subcarpeta** de `fotos` es un **álbum**. Por favor, solo fotos donde **no se reconozca la
    cara** de ningún miembro de AA.
- **Ajustes** (reunión del comité, Zoom, correo): archivo `config/site.yml`.
  **Corregir una traducción:** `data/translations/overrides.yml`.
- **¿Funciona todo?** Página **/es/status/** del sitio, o la pestaña **Actions** en GitHub.
  **Actualizar ya:** GitHub → **Actions** → **Update & Deploy** → **Run workflow**.

Las instrucciones detalladas están abajo (en inglés); puede usar el traductor de su navegador.

---

## The short version

| Every morning (≈ 5 AM Central) the site automatically… | You only… |
|---|---|
| picks up new **Grapevine** and **La Viña** magazine stories | **upload files** to the committee's Google Drive folders (flyers, reports, notes, slides, photos) |
| searches **aagrapevine.org** and **aalavina.org** for every **PDF** (flyers, catalogs, GVR/RLV kits, order forms…) | *(optional)* change a setting in **`config/site.yml`** |
| adds new episodes of **both podcasts**, new **YouTube** videos and **Instagram** posts | *(optional)* add your **districts** in **`content/districts.yml`** |
| turns dated **flyers** into **events** and Drive docs into **announcements** | *(rarely)* fix a translation in **`data/translations/overrides.yml`** |
| **translates everything** English ⇄ Spanish (free, open-source, no account needed) | |
| rebuilds and publishes the website — and keeps the last good version if a source is down | |

Everything runs for free on GitHub (GitHub Actions + GitHub Pages) using open-source software.
No paid services, no passwords or API keys required.

---

## Contents

1. [What updates automatically](#1-what-updates-automatically)
2. [Your part: uploading to Google Drive](#2-your-part-uploading-to-google-drive)
3. [Changing settings (`config/site.yml`)](#3-changing-settings-configsiteyml)
4. [Adding districts](#4-adding-districts)
5. [Fixing a translation](#5-fixing-a-translation)
6. [Announcements and events without Drive (optional)](#6-announcements-and-events-without-drive-optional)
7. [Running the update right now](#7-running-the-update-right-now)
8. [Is everything working?](#8-is-everything-working)
9. [Instagram: how the site reads it (please read)](#9-instagram-how-the-site-reads-it-please-read)
10. [Optional upgrades](#10-optional-upgrades) (Google API key · Instagram token · weekly e-mail)
11. [First-run checklist](#11-first-run-checklist) (and what to expect on day 1)
12. [Using your own address (custom domain)](#12-using-your-own-address-custom-domain)
13. [Replacing the old site](#13-replacing-the-old-site)
14. [Housekeeping](#14-housekeeping)
15. [Troubleshooting](#15-troubleshooting)
16. [How it works](#16-how-it-works)
17. [Credits and licenses](#17-credits-and-licenses)

---

## 1. What updates automatically

Every day at about **5:17 AM Central** (4:17 AM in winter) GitHub runs the **Update & Deploy** job.
It also runs within a few minutes whenever someone saves a change to the settings or content.

| Source | What the site gets | Where it shows |
|---|---|---|
| **AA Grapevine** magazine (aagrapevine.org) | Each new issue's stories: title, author's first name + initial, the publisher's public teaser, link to read it | **Read** |
| **La Viña** magazine (aalavina.org) | Same, for each bimonthly issue | **Read** |
| **Both websites, searched page by page** | Every PDF: flyers, catalogs, GVR / RLV kits, order forms, newsletters… with page count and a preview picture | **Library** |
| **AA Grapevine's Podcast** | Every episode, playable on the site | **Listen** |
| **Grapevine Weekly Open AA Meeting** (podcast) | Every recorded meeting, playable on the site | **Listen** |
| **YouTube** (@AAGrapevine — Grapevine *and* La Viña videos) | Every video, playable on the site | **Watch** |
| **Instagram** (@alcoholicsanonymous_gv, @alcoholicosanonimos_lv) | Newest posts — see [section 9](#9-instagram-how-the-site-reads-it-please-read) | **Instagram** |
| **Committee Google Drive** | Reports, notes, slides, workshops, forms, photo albums; dated flyers → events; docs in *announcements* → announcements | **Documents · Photos · Events · Announcements** |
| **Editorial calendar** (Grapevine) and suggested topics (La Viña) | Upcoming themes and story deadlines | **Contribute** |
| **Grapevine Weekly Open meeting** (web page) | Current day, time and Zoom details | **Meeting** |
| **Committee meeting** (from the settings) | Next dates, countdown, "add to calendar" | **Meeting · Events** |
| **Translation** | Every title, teaser and announcement in both languages | Everywhere |

The site also offers, automatically: a **What's New** page (the newest items from every source),
an **RSS feed**, a **calendar file** your phone can subscribe to, a **share kit** for districts,
a **search** page, and a **status** page that shows the health of every source.

**Respecting AA Grapevine, Inc.:** the site shows titles and the publishers' own public teasers and
links back to the official pages. It never copies magazine articles. **Respecting anonymity:** it
only shows what the official sources publish (first names and last initials).

---

## 2. Your part: uploading to Google Drive

The committee's shared folder is **A65_GV** (the one in `config/site.yml` → `drive.root_folder_id`).
It must be shared as **"Anyone with the link — Viewer"**. Inside it, each panel has its own folder:

```
A65_GV/
└── 2027-2028_Panel77_GVLV/          ← a panel folder (any name containing "Panel 77")
    ├── reports/        (or informes)
    ├── notes/          (or notas, minutes, actas)
    ├── slides/         (or presentaciones)
    ├── flyers/         (or volantes)         → dated flyers become EVENTS
    ├── workshops/      (or talleres)
    ├── forms/          (or formularios)
    ├── announcements/  (or anuncios)         → Google Docs become ANNOUNCEMENTS
    └── photos/         (or fotos)
        ├── Spring Assembly 2027/             → each sub-folder is an ALBUM
        └── Writing Workshop/
```

Upload files into the right folder. **They appear on the site the next morning**
(or a few minutes after you [run the update](#7-running-the-update-right-now)).
Folder names can be English or Spanish, any capitalization. Any other folder name also works —
it shows in the Documents page under its own name. Folders *outside* a Panel folder are ignored.

### Naming files (optional, but it makes the site smarter)

**Flyers → events.** Put the flyer in *flyers* and start its name with the date. Add a time and a
place if you like — the place goes after an `@`:

| File name | Result |
|---|---|
| `2027-03-14 Spring Assembly GV booth.pdf` | Event on Mar 14, 2027 (all day), flyer attached |
| `2027-03-14 Spring Assembly booth 9am @ Tyler Civic Center.pdf` | Mar 14, 2027 at 9:00 AM, at "Tyler Civic Center" |
| `2027-05-02 Writing workshop 10-12pm @ Ross Ave Group.pdf` | 10:00 AM – 12:00 PM |

**Announcements.** Put a Google Doc (or a .txt / .docx file) in *announcements*. The file name is
the headline and the document's text is the body:

| File name | Result |
|---|---|
| `2027-01-10 Welcome new GVRs` | Headline "Welcome new GVRs", dated Jan 10, 2027 |
| `2027-01-10 Welcome new GVRs (pinned)` — or `(fijado)` | Stays at the top |
| `Summer schedule (until 2027-08-31)` — or `(hasta 2027-08-31)` | Disappears by itself after Aug 31 |

**Documents.** A date at the start of any file name sets its date: `2027-03-14 Area report.pdf`.
Dates can also be written `03-14-2027`, `March 14, 2027` or `14 de marzo de 2027`.

**Photos.** Create a sub-folder inside *photos* for each album (e.g. `Fall Assembly 2027`).
**Please protect anonymity:** only upload pictures where **no AA member's face can be recognized**
(booths, literature tables, venues, banners) — or where everyone pictured agreed to share.

### Good to know

- **A new panel** (e.g. Panel 79): create `2029-2030_Panel79_GVLV` with the same sub-folders. The site picks it up
  automatically. Panels older than `drive.min_panel` in the settings are ignored.
- **Never published:** spreadsheets (form responses can contain personal information) and anything
  whose name contains `PRIVATE`, `PRIVADO`, `(Responses)` or `(Respuestas)`.
- **Deleting or moving** a file in Drive removes it from the site on the next update.
- Empty folders are fine — the site shows a friendly "nothing here yet" message.

---

## 3. Changing settings (`config/site.yml`)

All settings live in **one file**: [`config/site.yml`](config/site.yml). To edit it:

1. Open the file on GitHub and click the **pencil icon** (✎ "Edit this file").
2. Change the value after the colon. **Keep the spaces at the start of each line exactly as they are**
   (YAML uses indentation), and keep quotes around text that has them.
3. Click **Commit changes…** → **Commit changes**.
4. The site rebuilds automatically in about **10–20 minutes** (watch it in the **Actions** tab).

Common changes:

| To change… | Edit… |
|---|---|
| Committee meeting day/time | `meeting:` → `week_of_month`, `weekday`, `start`, `end` (24-hour, Central time) |
| Skip a meeting (holiday) | `meeting:` → `skip_dates: ["2027-12-15"]` |
| Zoom link, meeting ID, passcode | `meeting:` → `zoom_url`, `meeting_id`, `passcode` |
| Contact e-mail | `site:` → `contact_email` and `meeting:` → `chair_email` |
| Which Drive panels are shown | `drive:` → `min_panel` |
| How long the daily PDF search runs | `sources:` → `crawler:` → `minutes_per_run` (default 40; **`0` pauses the PDF search** — everything else keeps updating) |
| Instagram: official method only | `sources:` → `instagram:` → `anonymous: false` (see [section 9](#9-instagram-how-the-site-reads-it-please-read)) |
| Weekly e-mail day / length | `digest:` → `weekday`, `days` |
| The site's public address | `site:` → `url` (see [custom domain](#12-using-your-own-address-custom-domain)) |

If a change breaks the file (for example a missing space), the update shows a **red ✗** in the
Actions tab and **the website stays as it was**. Open the file's **History**, compare with the
previous version, and fix or undo the change.

---

## 4. Adding districts

Edit [`content/districts.yml`](content/districts.yml) the same way (pencil icon → commit). Only the number is required:

```yaml
districts:
  - number: 54
    name: "Garland – Mesquite"
    language: en                 # en | es | both
    website: "https://www.example-district54.org"
    gvr_contact: "gvr@example-district54.org"
    meets: "2nd Sunday, 3 PM"
  - number: 89
    language: es
```

Write names in English **or** Spanish — they are translated automatically.

---

## 5. Fixing a translation

Machine translation is good but not perfect. Two ways to correct it:

**A. Fix one specific title or phrase** — [`data/translations/overrides.yml`](data/translations/overrides.yml).
Add one line per fix at the bottom of the file (no `#` in front): the exact original text, then the
translation you want:

```yaml
"Carry the Message Project": { es: "Proyecto Lleva el Mensaje" }
"Casados, sobrios y felices": { en: "Married, Sober and Happy" }
```

**B. Fix a word everywhere** — [`data/translations/glossary.yml`](data/translations/glossary.yml).
Add names that must never be translated under `keep:` (for example a group's name) and AA terms
that always need the same translation under `terms:`. Instructions are at the top of that file.

Saving either file rebuilds the site in about 10–20 minutes.

---

## 6. Announcements and events without Drive (optional)

Drive is the easy way. If you prefer GitHub, you can also add a small text file:

- **Announcement:** a Markdown file in [`content/announcements/`](content/announcements/README.md), e.g. `2027-01-10-welcome-gvrs.md`.
- **Event without a flyer:** a Markdown file in [`content/events/`](content/events/README.md).

Each folder's README shows a copy-and-paste example. English or Spanish — it is translated automatically.
Committee meetings are **not** added by hand; they come from the settings.

---

## 7. Running the update right now

1. Open the repository on GitHub → **Actions** tab.
2. Click **Update & Deploy** in the left list.
3. Click **Run workflow** (right side) and choose:
   - **crawl_minutes** — how long to search aagrapevine.org / aalavina.org for PDFs.
     Leave it **empty** to use the daily setting (normally 40 minutes). Use up to `300` only for a
     big catch-up (see the [first-run checklist](#11-first-run-checklist)).
   - **skip_crawl** — tick it for a **quick refresh** (about 10–20 minutes): only **Google Drive**,
     **announcements** and the **podcasts** are updated. Videos, magazine stories, Instagram and
     PDFs wait for the next daily run.
4. Click the green **Run workflow** button. A normal run takes about an hour in total (the PDF
   search waits 5 seconds between pages, as the sites ask); a 300-minute catch-up about 6 hours.
   You can close the page — it runs on GitHub's computers.

Saving any settings or content file starts a quick update automatically — no need to do this by hand.

**Stopping a run:** open it and click **Cancel workflow**. Everything it fetched so far is still
saved (nothing has to be fetched again), but the website is only republished by the next run.

---

## 8. Is everything working?

- **The site's Status page** — <https://mkp715.github.io/AAGrapevine/status/> shows, for every source,
  when it last updated, how many items it has and any problem, plus how far the PDF search has got.
- **The Actions tab** on GitHub — each run shows a green ✓ or a red ✗. Click a run to see two summary
  tables (every step of the update, and every source). A yellow ⚠ warning means one source had a
  bad day; the site still published (with that source's previous items).
- **The badge** at the top of this page is green when the last update succeeded.
- **E-mail when a run fails:** GitHub → your picture → Settings → Notifications → *Actions* →
  "Only notify for failed workflows". **Good to know:** e-mails about the *daily* run go to the
  person who last switched the workflow on. To make sure they come to **you**: **Actions** →
  **Update & Deploy** → **⋯** (top right) → **Disable workflow**, then **Enable workflow**.
- **An issue when a source stops updating:** if the same source (for example Google Drive) has not
  updated for **7 days**, the site opens one issue titled **"A content source has stopped
  updating"** in this repository's **Issues** tab, explaining what to check. GitHub e-mails it to
  everyone who *watches* the repository (**Watch** button at the top → **All Activity**, or
  **Custom → Issues**). The issue closes by itself once the source works again.

It is normal for **one** source to fail now and then (Instagram in particular sometimes refuses
robots). Nothing is lost: the previous items stay on the site and the next run tries again.
Only worry if the same source fails for more than a week — that is when the issue above appears.
See [Troubleshooting](#15-troubleshooting).

---

## 9. Instagram: how the site reads it (please read)

**By default** the site looks at the two official accounts' **public embed pages** — the small
"widget" Instagram offers to websites — a few requests a day, and shows each post's picture, date
and the start of its caption, linking back to Instagram.

**Please know:** Instagram's terms of use **discourage automated collection** of its pages, even
public ones and even at this tiny volume. The committee can choose how strict to be:

| Choice | How | Result |
|---|---|---|
| **Default** — public embed pages | Nothing to do (`anonymous: true`) | Works most days; Instagram sometimes refuses for a day |
| **Official and compliant** — Instagram's own API | Add the free **Instagram token** ([section 10 b](#b-instagram-token-the-official-way)) | Most reliable, and the method Instagram's terms allow |
| **Official only, no token** — manual list | In `config/site.yml` set `sources:` → `instagram:` → `anonymous: false`, then list posts by hand in [`content/instagram.yml`](content/instagram.yml) | No automated visits to Instagram at all |

With `anonymous: false` and no token, only the posts you list in `content/instagram.yml` appear
(paste each post's link; add a `caption:` line, because the site will not visit Instagram to fetch
the caption or picture). Instructions are at the top of that file.

---

## 10. Optional upgrades

None of these are needed. Each one is a **GitHub secret** — a private value only the workflows can
read. To add one: repository **Settings** → **Secrets and variables** → **Actions** →
**New repository secret** → enter the **Name** exactly as shown and the **Secret** → **Add secret**.

### a) Google API key: exact dates for Drive files

Without it, Drive file dates come from the file name or the day the site first saw the file.

1. Go to <https://console.cloud.google.com/> (sign in with the committee Google account).
2. Top bar → project list → **New project** → name it `grapevine-site` → **Create**.
3. Menu → **APIs & Services** → **Library** → search **Google Drive API** → **Enable**.
4. **APIs & Services** → **Credentials** → **Create credentials** → **API key**. Copy it.
5. Click the new key → **API restrictions** → **Restrict key** → tick **Google Drive API** → **Save**.
6. In GitHub add the secret **`GOOGLE_API_KEY`** with that key. Done — the next run uses it.

The key is free for this amount of use and only reads folders that are already public.

### b) Instagram token: the official way

Instagram's *Business Discovery* API lets one Instagram **professional** account read the public
posts of other professional accounts (AA Grapevine's accounts are professional accounts). It is free
and takes about 30 minutes once. Someone comfortable with Facebook/Meta settings should do it:

1. The committee's own Instagram account → **Settings → Account type and tools → Switch to
   professional → Business** (free), linked to a **Facebook Page** the committee manages.
2. At <https://developers.facebook.com/> → **My Apps → Create app** → type **Business** → add the
   product **Instagram** (Instagram API with Facebook Login).
3. Get a token that never expires: **business.facebook.com → Settings → Users → System users** →
   add one (Admin) → **Assign assets**: the Facebook Page and the app → **Generate new token** with the
   permissions `instagram_basic`, `pages_show_list`, `pages_read_engagement`, `business_management`,
   expiry **Never**.
4. Find the committee's **Instagram business account ID** (a long number): open
   `https://graph.facebook.com/v21.0/me/accounts?fields=name,instagram_business_account{id,username}&access_token=TOKEN`
   (put your token in place of `TOKEN`) and copy `instagram_business_account` → `id` (not the Page id).
5. Add the GitHub secrets **`IG_ACCESS_TOKEN`** (the token) and **`IG_BUSINESS_ID`** (the id).

The next run uses the API automatically. Full technical notes are at the top of
`scripts/sync/instagram.py`. If the token ever stops working, the Status page shows an Instagram
error; generate a new token and replace the secret.

### c) Weekly e-mail digest: keep every district informed

Once a week (Monday by default) the site can e-mail a clean **English + Spanish** summary: the next
committee meeting with its Zoom link, new announcements, upcoming events, and everything new on the
site. Send it to one **Google Group** that includes all DCMs / GVRs / RLVs, and the districts get it
without anyone lifting a finger.

**With a Gmail account** (for example the committee's):

1. Turn on **2-Step Verification** for that Google account.
2. Go to <https://myaccount.google.com/apppasswords>, create an app password named
   `grapevine digest`, and copy the 16-letter password.
3. Add these GitHub secrets:

| Secret | Value |
|---|---|
| `SMTP_SERVER` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` *(optional — 587 is the default; use 465 if your provider says "SSL")* |
| `SMTP_USERNAME` | the full Gmail address |
| `SMTP_PASSWORD` | the 16-letter **app password** (not the normal password) |
| `DIGEST_TO` | where to send it — ideally one Google Group address; several addresses can be separated by commas (they are sent as **Bcc**, so nobody sees the others) |
| `DIGEST_FROM` | *(optional)* the "From" address, if different from `SMTP_USERNAME` |
| `DIGEST_REPLY_TO` | *(optional)* where replies go (default: `contact_email` in the settings) |

4. **Preview first:** Actions → **Weekly e-mail digest** → **Run workflow** (leave *Preview only* ticked) →
   open the finished run → **Artifacts** → download **digest-preview** → open `digest.html`.
5. To send one right away, run it again with *Preview only* **unticked**.

The day and the number of days covered are set in `config/site.yml` → `digest:`. If nothing is new
that week, no e-mail is sent. To stop the digest, delete the `SMTP_PASSWORD` secret.

---

## 11. First-run checklist

Already done for the current site. For a new copy (or after moving the repository) — click-by-click
details are in **[docs/SETUP-GITHUB.md](docs/SETUP-GITHUB.md)**:

- [ ] **Settings → Pages → Build and deployment → Source: GitHub Actions**
- [ ] **Settings → Actions → General → Actions permissions: Allow all actions** (leave *Workflow
  permissions* at its default — the workflows ask for exactly what they need)
- [ ] Check `site:` → `url:` and the meeting details in `config/site.yml`
- [ ] **Actions → Update & Deploy → Run workflow** once, with **crawl_minutes** left empty (the
  daily setting, 40 minutes).
  - Use **`300`** only if the big first PDF search has *not* been saved in the repository yet:
    after the first run, open the site's **Status** page — if the PDF search shows only a small
    percentage done, you may run it once more with `300`. (The committee's first PDF search was run
    ahead of time and saved, so normally `40` is right. Without it, the daily 40-minute search still
    reaches every page within about 10 days.)
- [ ] When the run shows a green ✓, open the website and its **Status** page.
- [ ] *(Recommended)* Turn on failure e-mails, and make sure they come to you (see
  [section 8](#8-is-everything-working)).

### What to expect on day 1

- **Magazine stories, both podcasts, videos and Instagram** appear on the first run.
- **The PDF Library** shows what the first PDF search found and keeps growing and re-checking daily.
- **Committee sections** (Documents, Photos, flyer events, Drive announcements) show a friendly
  **"nothing here yet"** message: the Panel 77 folders in Drive are still empty. They fill in the
  morning after the first uploads. The **committee meeting** dates show right away (from the settings).
- **Translations:** the first run translates hundreds of titles (up to 40 minutes a day); anything not
  done yet shows in its original language and is finished on the next runs.
- **Weekly e-mail** stays off until its secrets are added; the **link check** first runs on Sunday.

---

## 12. Using your own address (custom domain)

For example **grapevine.neta65.org** instead of mkp715.github.io/AAGrapevine:

1. **DNS** (whoever manages neta65.org — the Area webmaster): add a **CNAME** record
   `grapevine` → `mkp715.github.io` (use the GitHub account name that owns this repository).
2. **GitHub:** repository **Settings → Pages → Custom domain** → type `grapevine.neta65.org` →
   **Save**. Wait for the green "DNS check successful" (minutes to a few hours), then tick
   **Enforce HTTPS**.
3. **Settings file:** change `site:` → `url:` in `config/site.yml` to `https://grapevine.neta65.org`.
4. Run **Update & Deploy** once. Links, feeds and share buttons now use the new address.

No `CNAME` file is needed: sites published by GitHub Actions take the domain from the Pages
setting, and the workflow reads the address from there by itself.

---

## 13. Replacing the old site

The old site at **https://neta65.github.io/Grapevine/** was edited by hand. None of its content
(old flyers, announcements, events, photo lists) is carried over — only the logo and the grapevine
artwork live on in the new design. Pick **one** of these:

**Option A — the new site takes over the old address (best, if you can manage the `neta65` GitHub account)**

1. On github.com/neta65/Grapevine: **Settings → General** → rename it to `Grapevine-old`
   (or archive/delete it once you're sure).
2. On this repository: **Settings → General → Danger Zone → Transfer ownership** → to `neta65`.
3. After the transfer: **Settings → General** → rename `AAGrapevine` to `Grapevine`.
4. Re-check **Settings → Pages** (Source: GitHub Actions), the **Actions** permissions, and any
   secrets (see [docs/SETUP-GITHUB.md](docs/SETUP-GITHUB.md)). Then switch **Update & Deploy** off
   and on again (**Actions → Update & Deploy → ⋯ → Disable workflow**, then **Enable workflow**)
   so the failure e-mails go to the new owner.
5. In `config/site.yml` set `url: "https://neta65.github.io/Grapevine"` and update the address in
   `sources: → crawler: → user_agent`.
6. Run **Update & Deploy**. The site is live at the old address; bookmarks keep working.

The workflow detects the new address and folder name by itself, so nothing else needs changing.

**Option B — keep the new address and forward visitors from the old one**

1. Open github.com/neta65/Grapevine. Delete the old files (or leave them — only `index.html` matters).
2. **Add file → Upload files:** upload [`docs/redirect-old-site/index.html`](docs/redirect-old-site/index.html)
   as `index.html`, and a second copy renamed **`404.html`** (so old deep links forward too). Commit.
3. If the new site's address is not `https://mkp715.github.io/AAGrapevine/`, open the uploaded file
   with the pencil icon and change `NEW_SITE` at the very top — plus the same address in the three
   places marked *NO-JAVASCRIPT FALLBACK*.

Visitors of the old page land on the matching new page — for example `…/Grapevine/#events` opens
the new **Events** page, in Spanish if they had chosen Spanish on the old site.

---

## 14. Housekeeping

- **Keep the repository public.** GitHub Actions is free and unlimited for public repositories.
  A private repository gets 2,000 free minutes a month, which the daily update would use up.
- **The 60-day rule.** GitHub pauses scheduled workflows in a public repository after 60 days
  without activity. The daily data commit counts as activity, so this should never happen. If it
  ever does: **Actions → Update & Deploy → Enable workflow**.
- **Dependabot pull requests.** Once a month GitHub may open a pull request titled
  `chore(actions)…` or `chore(deps)…` that updates the building blocks. A few minutes later the
  **Pull request check** has built the website with the update: **merge only if the pull request
  shows a green ✓**. If it shows a red ✗, leave it open (or close it) — the live site is not
  affected. After merging, glance at the next **Update & Deploy** run; if it is red, open the merged
  pull request and click **Revert**.
- **Who gets the failure e-mails.** E-mails about the daily run go to the person who last switched
  the workflow on (or last changed its schedule). When a new chair takes over, or after moving the
  repository, the person who should receive them opens **Actions → Update & Deploy → ⋯ →
  Disable workflow**, then **Enable workflow** (and turns on the e-mails, see
  [section 8](#8-is-everything-working)).
- **Repository size** grows slowly (data files and small preview pictures). That is expected; see
  [docs/OPERATIONS.md](docs/OPERATIONS.md#repository-size) if it ever passes about 1 GB.
- **Daily data commits** by `github-actions[bot]` ("chore(data): daily content sync …") are normal.

---

## 15. Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| The site did not change today | The run failed, is still running, or the schedule was paused | **Actions** tab: open the latest **Update & Deploy** run. If the workflow shows "disabled", click **Enable workflow**. Then **Run workflow**. |
| Red ✗ right after editing a settings file | A typo in the YAML (usually indentation or a missing quote) | Open the failed run → the red step shows the line. Fix the file, or undo your change from the file's **History**. The live site is unaffected. |
| A Drive file does not appear | Wrong folder, folder not public, name contains `PRIVATE`, it is a spreadsheet, or the update hasn't run yet | Check the file is inside the current Panel folder and the root folder is shared "Anyone with the link". Wait for the next run or run it manually. |
| A flyer did not become an event | No date at the start of the name, or it is not in *flyers* | Rename it like `2027-03-14 Title 9am @ Place.pdf`. |
| An announcement did not appear | Not in *announcements*, or its `(until …)` date passed | Move/rename it; it must be a Google Doc, .txt, .md or .docx. |
| A translation is wrong | Machine translation | Add a fix to `data/translations/overrides.yml` ([section 5](#5-fixing-a-translation)). |
| Instagram stopped updating | Instagram is refusing robots for a while (or `anonymous: false` without a token) | The last posts stay and it usually recovers. For a permanent fix add the [Instagram token](#b-instagram-token-the-official-way). |
| The PDF library is small | The PDF search is still in progress (~3,200 pages at 5 seconds each) | Check the Status page; it grows daily. Optionally run once with `crawl_minutes = 300`. |
| Site shows "404 — There isn't a GitHub Pages site here" | Pages not switched to GitHub Actions | **Settings → Pages → Source: GitHub Actions**, then run **Update & Deploy**. |
| Run fails at "Read GitHub Pages settings" | Same as above | Same as above. |
| Run fails at "Commit refreshed data" with *permission denied* / *403* / *protected branch* | A rule on `main` stops the bot from saving its data | If `main` has branch protection or a ruleset, add **GitHub Actions** to its bypass list (**Settings → Rules** or **Settings → Branches**). The workflow already asks for write access itself; *Workflow permissions* does not need changing. |
| An issue "A content source has stopped updating" appeared | One source has not updated for 7 days (the site keeps its older items) | Open the issue: it names the source, the error and what to check (for Google Drive: is the folder still shared "Anyone with the link"?). It closes itself when the source works again. |
| Yellow ⚠ "Translation models missing" or "Translation is not working" | The free translation models could not be downloaded (their website was down or moved) | New titles stay in their original language; nothing else is affected. If it lasts more than a few days, send the run's log to whoever helps with the website. |
| Run fails at "Publish to GitHub Pages" with *environment protection* | The `github-pages` environment only allows certain branches | **Settings → Environments → github-pages** → allow the `main` branch. |
| The weekly e-mail did not arrive | Secrets missing, wrong app password, not the configured weekday, or nothing new that week | Open the **Weekly e-mail digest** run: it says exactly which. Gmail needs an **app password**. |
| An issue "Broken links found by the weekly check" appeared | A link in the settings or a district website moved | Open the issue; fix the address in `config/site.yml` or `content/districts.yml`. It closes itself when fixed. |

Still stuck? Open the failed run, click the red step, copy the last 20 lines, and send them to
whoever helps with the website (or open an **Issue** in this repository).

---

## 16. How it works

```
 every morning (GitHub Actions)                                     GitHub Pages
 ─────────────────────────────                                      ────────────
  aagrapevine.org ─┐
  aalavina.org  ───┤  Python sync scripts    data/raw/*.json     Eleventy +      website
  2 podcast feeds ─┤  (scripts/sync/*.py) ──► + translation   ──► Tailwind    ──► in English
  YouTube ─────────┤  polite: robots.txt,     (offline, open      (src/)          and Spanish
  Instagram ───────┤  5-second crawl delay    source models)
  Google Drive ────┘                          data/site/*.json
                                              committed to this repository (history = backup)
```

Technical details, how to run it on your own computer, and how to add a new source:
**[docs/OPERATIONS.md](docs/OPERATIONS.md)**.

---

## 17. Credits and licenses

This project is free software under the **GNU GPL v3** (see [LICENSE](LICENSE)). It is built
entirely from open-source tools — thank you to their authors:

**Website**

| Tool | Used for | License |
|---|---|---|
| [Eleventy](https://www.11ty.dev/) | Builds the pages | MIT |
| [Tailwind CSS](https://tailwindcss.com/) | Design system / styles | MIT |
| [Alpine.js](https://alpinejs.dev/) | Small interactive parts (menus, filters, countdown) | MIT |
| [MiniSearch](https://lucaong.github.io/minisearch/) | Site search | MIT |
| [GLightbox](https://biati-digital.github.io/glightbox/) | Photo albums | MIT |
| [lite-youtube-embed](https://github.com/paulirish/lite-youtube-embed) | Fast, privacy-friendly video players | Apache-2.0 |
| [Lucide](https://lucide.dev/) | Icons | ISC |
| [Inter](https://rsms.me/inter/) and [Fraunces](https://github.com/undercasetype/Fraunces) via [Fontsource](https://fontsource.org/) | Fonts (self-hosted) | SIL OFL 1.1 |
| [markdown-it](https://github.com/markdown-it/markdown-it), [js-yaml](https://github.com/nodeca/js-yaml), [node-qrcode](https://github.com/soldair/node-qrcode) | Text formatting, settings, QR codes | MIT |

**Daily content sync**

| Tool | Used for | License |
|---|---|---|
| [Requests](https://requests.readthedocs.io/) | Fetching pages | Apache-2.0 |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) + [lxml](https://lxml.de/) | Reading web pages | MIT / BSD-3 |
| [Protego](https://github.com/scrapy/protego) | Obeying robots.txt and crawl delays | BSD-3 |
| [feedparser](https://github.com/kurtmckee/feedparser) | Podcast and YouTube feeds | BSD-2 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Complete YouTube video lists (no API key) | Unlicense |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (PDFium) | PDF page counts and preview pictures | Apache-2.0 / BSD-3 |
| [Pillow](https://python-pillow.org/) | Small WebP thumbnails | MIT-CMU |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) + [SentencePiece](https://github.com/google/sentencepiece) | Offline English ⇄ Spanish translation | MIT / Apache-2.0 |
| [Argos Translate](https://github.com/argosopentech/argos-translate) models (trained on [OPUS](https://opus.nlpl.eu/) data) | The translation models | CC-BY 4.0 (models) |
| [python-dateutil](https://github.com/dateutil/dateutil), [PyYAML](https://pyyaml.org/), [icalendar](https://github.com/collective/icalendar) | Dates, settings, calendars | Apache-2.0/BSD, MIT, BSD-2 |

**Automation:** [GitHub Actions](https://docs.github.com/actions) (`actions/checkout`, `setup-python`,
`setup-node`, `cache`, `configure-pages`, `upload-pages-artifact`, `deploy-pages`, `upload-artifact` — MIT),
[lychee](https://github.com/lycheeverse/lychee) link checker (MIT / Apache-2.0), Dependabot.

*AA Grapevine®, La Viña® and their content are the property of AA Grapevine, Inc. This committee
website links to the official sources and is not affiliated with or endorsed by AA Grapevine, Inc.
or Alcoholics Anonymous World Services, Inc.*
