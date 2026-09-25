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

- Trae automáticamente los **artículos nuevos** de Grapevine y La Viña, **los documentos oficiales** (PDF) de
  aagrapevine.org y aalavina.org — **cada uno una sola vez**, con sus ediciones en inglés, español y francés
  en la misma tarjeta —, los episodios de **los dos podcasts** (el de AA Grapevine y la **Reunión Abierta
  Semanal de Grapevine**), los **videos de YouTube** y las **publicaciones de Instagram**, y lo
  **traduce todo** (inglés ⇄ español) con software libre.
- **Lo único que usted hace:** subir archivos a las carpetas del comité en **Google Drive**
  (dentro de la carpeta del Panel 77). Al día siguiente aparecen en el sitio: informes, notas, presentaciones y
  talleres en el **Portafolio** (`/es/portfolio/`; la antigua dirección `/es/documents/` lleva allí).
  - Un **volante** con la fecha al inicio del nombre se convierte en **evento** — con hora y lugar si
    los escribe: `2027-03-14 Asamblea de primavera 9am @ Tyler TX.pdf`
  - Un **Google Doc** en `anuncios` se convierte en **anuncio**; `(fijado)` lo deja arriba y
    `(hasta 2027-02-01)` lo oculta después de esa fecha.
  - Cada **subcarpeta** de `fotos` es un **álbum**. Por favor, solo fotos donde **no se reconozca la
    cara** de ningún miembro de AA.
- **Tienda** (`/es/shop/`): el **libro del mes** de Grapevine y La Viña y los **precios de suscripción** por
  región, leídos cada día de las tiendas oficiales (toda compra se hace allí). **Kit del mes**
  (`/es/monthly/`): un cartel para cada mes (descargar en PNG, compartir, imprimir) y las 10 maneras de poner
  una edición a trabajar. La **reunión abierta semanal de La Viña** (jueves, en español) está en
  `/es/meetings/#weekly-open`. La página **Reuniones** (`/es/meetings/`) también reúne las **reuniones de
  Grapevine** de los grupos de AA de nuestra Área y de las áreas cercanas (de las 8 listas de reuniones que usa
  la página del Grupo Rowlett). El **informe de GV/LV** para la reunión del distrito está en
  `/es/monthly/#report` (la antigua página *Distritos* ya no existe; su dirección lleva allí).
- **Ajustes** (reunión del comité, Zoom, correo, eventos de cada mes como la mesa en CityWide Dallas):
  archivo `config/site.yml`.
  **Corregir una traducción:** `data/translations/overrides.yml`.
- **Eventos sin volante** (talleres, asambleas): un archivo en `content/events`. Si todavía faltan
  detalles, `tentative: true` muestra "Detalles por confirmar"; el lugar en español va en `location_es`.
- **¿Funciona todo?** Página **/es/status/** del sitio, o la pestaña **Actions** en GitHub.
  **Actualizar ya:** GitHub → **Actions** → **Update & Deploy** → **Run workflow**.

Las instrucciones detalladas están abajo (en inglés); puede usar el traductor de su navegador.

---

## The short version

| Every morning (≈ 5 AM Central) the site automatically… | You only… |
|---|---|
| picks up new **Grapevine** and **La Viña** magazine stories | **upload files** to the committee's Google Drive folders (flyers, reports, notes, slides, photos) |
| searches **aagrapevine.org** and **aalavina.org** for every **PDF** (flyers, catalogs, GVR/RLV kits, order forms…) | *(optional)* change a setting in **`config/site.yml`** |
| adds new episodes of **both podcasts**, new **YouTube** videos and **Instagram** posts | |
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
4. [The monthly GV/LV report](#4-the-monthly-gvlv-report)
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
A short second run at about **7:07 AM Central** (6:07 AM in winter) picks up the Grapevine and La Viña
**daily quote**, which is out before 6 AM Texas time.
It also runs within a few minutes whenever someone saves a change to the settings or content.

| Source | What the site gets | Where it shows |
|---|---|---|
| **AA Grapevine** magazine (aagrapevine.org) | Each new issue's stories: title, author's first name + initial, the publisher's public teaser, link to read it | **Read** |
| **La Viña** magazine (aalavina.org) | Same, for each bimonthly issue | **Read** |
| **Both websites, searched page by page** | Every **official** PDF — only files on aagrapevine.org, aalavina.org, aa.org or aaws.widen.net (`library.official_hosts`) — flyers, catalogs, GVR / RLV kits, order forms, newsletters…, **each once** (`scripts/sync/pdf_curate.py`: copies of one file merged, older versions of one document dropped, English / Spanish / French editions on one card with language links), with page count and a preview picture | **Library** (catalogs and order forms also on **Shop**) |
| **AA Grapevine's Podcast** | Every episode, playable on the site | **Listen** |
| **Grapevine Weekly Open AA Meeting** (podcast) | Every recorded meeting, playable on the site | **Listen** |
| **YouTube** (@AAGrapevine — Grapevine *and* La Viña videos) | Every video, playable on the site | **Watch** |
| **Instagram** (@alcoholicsanonymous_gv, @alcoholicosanonimos_lv) | Newest posts — see [section 9](#9-instagram-how-the-site-reads-it-please-read) | **Instagram** |
| **Committee Google Drive** | Reports, notes, slides, workshops, forms, photo albums; dated flyers → events; docs in *announcements* → announcements | **Portfolio · Photos · Events · Announcements** |
| **Editorial calendar** (Grapevine) and suggested topics (La Viña) | Upcoming themes and story deadlines | **Contribute** |
| **Grapevine Weekly Open meeting** (web page) | Current day, time and Zoom details | **Meetings** |
| **La Viña's weekly open meeting** (from the settings, `lavina_weekly_open:` — an official La Viña flyer) | Thursdays in Spanish, first date, Zoom details | **Meetings** (one line on Home · Listen · Watch · monthly posters) |
| **Official stores** (aagrapevine.org / aalavina.org store pages) | **Book of the Month** (title, cover, percent, sale price, dates) and **subscription prices** per region (U.S. · Canada · International; print / digital / complete) | **Shop** (a short teaser on Home, the monthly posters and the weekly e-mail) |
| **Daily quote** (the home pages of aagrapevine.org and aalavina.org) | Grapevine's *Daily Quote* and La Viña's *Cita Diaria*: the quote as published (never translated), who said it, the book it comes from, the official e-mail sign-up | **Home** |
| **Record your story by phone** (aagrapevine.org/audio-portal, aalavina.org/graba-tu-historia) | Grapevine's *Audio Project* and La Viña's *Graba tu historia*: the phone number, the keys to press, the length, the e-mail address for recordings, Grapevine's story playlists | **Share your story** (`/contribute/#record`; one link on Listen · Watch) |
| **Committee meeting** (from the settings) | Next dates, countdown, "add to calendar" | **Meetings · Events** |
| **Local meeting lists** (the 8 intergroup / central office lists the Rowlett Group's meeting page uses — in or at our Area: Dallas Intergroup, Fort Worth Central Office, Tyler Central Service Office, the Spanish-speaking Dallas office, District 71 Abilene; nearby: Arkansas Central Office, OKC Intergroup, Northwest Texas Area 66) | Every meeting with the Grapevine ("GR") type: day, time, place, directions, link to the office's page. A meeting in two lists is shown once; our Area first (by county), nearby areas after | **Meetings** (one line on Home; each meeting is in the site search) |
| **Monthly events** (from the settings, e.g. our booth at CityWide Dallas) | The next dates, "add to calendar" | **Events · Home · Meetings · calendar feed · weekly e-mail** |
| **Other calendars** (optional, e.g. the NETA 65 workshop calendar on neta65.org) | Their events, each shown **once** even when it is also in `content/events` (yours wins). *neta65.org currently blocks robots — see [the NETA 65 workshop calendar](#the-neta-65-workshop-calendar)* | **Events** |
| **Translation** | Every title, teaser and announcement in both languages | Everywhere |

The site also offers, automatically: a **What's New** page (the newest items from every source),
an **RSS feed**, a **calendar file** your phone can subscribe to, a **share kit** for districts,
a **search** page, and a **status** page that shows the health of every source. **Shop** is the one
page for subscribing and buying (every purchase links to the official Grapevine / La Viña stores;
the old `/subscribe/` address redirects there, #anchors included).

What the newer pages do:

- **Book of the Month** (`/shop/#botm`): both magazines' books of the month with cover, percent off, sale and
  regular price, the offer's dates and a live "N days left" count; the heading and buttons switch to
  "Offer ended" by themselves on the last day, even before the next update. The home page shows a small teaser.
- **Subscriptions & prices** (`/shop/#subscriptions`): a switch for magazine × region (U.S., Canada,
  International) with every term's price as the stores list it, volume prices, gift subscriptions (Carry the
  Message), home-group order forms and catalogs. Links like `/shop/?pub=lv#subscriptions` open La Viña's prices.
- **Monthly toolkit** (`/monthly/`): the "10 ways to put an issue to work" guide
  (`config/carry.yml`), and a page for this month and each of the next 12 — the issue themes, tips, story
  deadlines, dates (committee meeting, the CityWide booth, workshops) and a **poster** in its own seasonal
  design to download as a PNG for WhatsApp / Instagram (1080 × 1350), share, or print on one letter page.
  Its QR code opens that month's page; the three previous months' addresses forward to `/monthly/`.
  The same page holds the **GV/LV report** for district meetings (`/monthly/#report`, see section 4); the old
  `/districts/` address forwards there.
- **Meetings** (`/meetings/`, renamed from *Committee meeting*; the old `/meeting/` address forwards there,
  `#weekly-open` included): the committee meeting (`#committee-meeting`), the **Grapevine meetings** of local
  groups (`#grapevine-meetings`: our Area first, then each nearby area — filters for place, day, in person /
  online and "include nearby areas"; each card links to the meeting's page on the office's site, which has the
  joining details and any changes), and the weekly open meetings (`#weekly-open`).
- **Districts** page: removed. Its one piece of its own, the GV/LV report, lives at `/monthly/#report`
  (found in the site search by "district" / "report", "distrito" / "informe"); `/districts/` forwards there.
- **La Viña's weekly open meeting**: `/meetings/#weekly-open` shows both public weekly meetings (Grapevine on
  Wednesdays in English, La Viña on Thursdays in Spanish) with day, time and Zoom details — the one place
  with those details; other pages link there.

**Menus** (`src/_data/nav.js`): What's New · Read · Listen · Watch · Library · Shop ·
**Get Involved** (monthly toolkit & GV/LV report, share your story, published writers, GVR / RLV
corner) · **Committee** (Meetings — committee meeting, Grapevine meetings & weekly open meetings —, events,
Portfolio — the committee's reports, notes, slides and workshop files, `/portfolio/` —, photos, announcements).
The Portfolio was called *Committee documents* (`/documents/`); that address forwards to `/portfolio/`, `#anchors` included.
Instagram, the weekly digest, the share kit, search and status are in the footer and the phone menu.
Each piece of information has one home page; other pages only link to it.

**Wording on the site** (both languages): visitors read "document(s)" / "documento(s)", never "PDF", and
"we keep it updated regularly" / "lo mantenemos actualizado con regularidad" — the pages do not describe how
the sites are searched (no "crawl", "checked every day on …"). Code, file names, data keys and these docs keep
the technical words.

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

Upload files into the right folder (reports, notes, slides, workshops and forms show on the **Portfolio**
page, `/portfolio/`). **They appear on the site the next morning**
(or a few minutes after you [run the update](#7-running-the-update-right-now)).
Folder names can be English or Spanish, any capitalization. Any other folder name also works —
it shows on the Portfolio page (`/portfolio/`) under its own name. Folders *outside* a Panel folder are ignored.

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
| Skip a meeting (holiday) | `meeting:` → `skip_dates: ["2027-12-15"]` (must be a meeting day — that month's 3rd Wednesday; any other date is ignored and the run summary says which date to use) |
| Something we do every month (a booth, a workshop) | `recurring_events:` — see [below](#add-a-recurring-event) |
| Skip one month of it | `recurring_events:` → that event's `skip_dates: ["2026-12-12"]` |
| Zoom link, meeting ID, passcode | `meeting:` → `zoom_url`, `meeting_id`, `passcode` |
| Contact e-mail | `site:` → `contact_email` and `meeting:` → `chair_email` |
| Which Drive panels are shown | `drive:` → `min_panel` |
| The offices whose Grapevine meetings are listed (our Area and nearby) | `meetings:` → `feeds` (each with `region_label`; `enabled: false` hides the list) |
| How long the daily PDF search runs | `sources:` → `crawler:` → `minutes_per_run` (default 40; **`0` pauses the PDF search** — everything else keeps updating) |
| Instagram: official method only | `sources:` → `instagram:` → `anonymous: false` (see [section 9](#9-instagram-how-the-site-reads-it-please-read)) |
| Another website's calendar on the Events page | `sources:` → `ics_feeds:` (instructions in the comments there) |
| Weekly e-mail day / length | `digest:` → `weekday`, `days` |
| The site's public address | `site:` → `url` (see [custom domain](#12-using-your-own-address-custom-domain)) |

If a change breaks the file (for example a missing space), the update shows a **red ✗** in the
Actions tab and **the website stays as it was**. Open the file's **History**, compare with the
previous version, and fix or undo the change.

### Add a recurring event

For something the committee does **every month** — like our Grapevine / La Viña booth at
**CityWide Dallas** (2nd Saturday, 5–8 PM) — add a block under `recurring_events:` in
`config/site.yml`. This is the one already there:

```yaml
recurring_events:
  - key: "citywide-dallas"          # short name: letters, numbers, dashes. Don't change it later.
    title: "GV/LV booth at CityWide Dallas"
    title_es: "Mesa de GV/LV en CityWide Dallas"
    summary: "Stop by our Grapevine and La Viña literature table at CityWide Dallas, …"
    summary_es: "Visita nuestra mesa de literatura de Grapevine y La Viña en CityWide Dallas, …"
    week_of_month: 2                # 1–5, or -1 for "the last one"
    weekday: "saturday"             # English or Spanish ("sábado")
    start: "17:00"                  # 24-hour clock, Central time
    end: "20:00"
    location: "Lover's Lane United Methodist Church, 9200 Inwood Road, Dallas, TX 75220"
    url: "https://citywidedallasaa.org"   # the "Event details" link (optional)
    months_ahead: 6                 # how many upcoming dates to list (Events page, calendar feed)
    skip_dates: []                  # a month without it: ["2026-12-12"] (must be that month's 2nd Saturday)
```

`months_ahead` only sets how many dates the **Events** page and the calendar feed list; the monthly posters
(`/monthly/`, 13 months) work out the later dates from the same rule, so there is no need to raise it.

To add another one, copy the whole block (from `- key:` down), paste it under the last one and
change the values. Every date then shows on the **Events** page (with an "Every month" badge); the
next one always keeps a place in the home page's **Upcoming events** row (the other places go to the
soonest workshops and other events, so a busy month never pushes the booth off the home page), and it
is on the **Meeting** page and in the **weekly e-mail**; all of them are in the **calendar feed** — in
both languages, with daylight-saving time handled. Write the Spanish
yourself (`title_es`, `summary_es`); if you leave it out, the site translates the English
automatically and marks it "auto-translated". A monthly event never shows as "New" and does not,
on its own, make the weekly e-mail go out. If a block has a mistake (for example
`weekday: "funday"`), only that event is left out: the site still updates, and the run summary in
the **Actions** tab shows a yellow **Settings problem** saying what to fix. The same happens for a
`skip_dates` date that is not one of the event's days (for example the Sunday, or the 1st Saturday):
that month still shows, and the note gives the date to use instead.

### La Viña's weekly open meeting

`lavina_weekly_open:` in `config/site.yml` holds La Viña's weekly open meeting (from its official flyer —
there is no web page to read yet): Spanish and English title and summary, `day`, `time` + `timezone` (the
meeting's own time zone; the site also shows Central time), `starts` (the first meeting, which must be on
that `day`), `zoom_id` and `passcode`. When La Viña publishes a page for it, put the address in `url`.
Set `enabled: false` (or delete the block) to take it off the site.

---

## 4. The monthly GV/LV report

Nothing to edit: **Monthly toolkit → Your monthly report** (`/monthly/#report`) is a two-minute report a
GVR / RLV can read aloud at the district meeting, in English or Spanish, with a copy button. It fills
itself in on every update — what is new, the current issues, published writers from our Area, story
deadlines, coming events and the next committee meeting (`eleventy/filters/community.js` → `reportText`).
Tips for a good report and the Area's published writers sit beside it. (The old *Districts* page and its
`content/districts.yml` list were retired; its news feed, calendar, digest and poster links live on
`/events/`, `/digest/`, `/share/` and in the footer.)

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
To write the other language yourself, add `title_es` and `summary_es` to a file written in English
(or `title_en` and `summary_en` to one written in Spanish): the other page then shows your words as
written, not an automatic translation.
Committee meetings are **not** added by hand; they come from the settings — and so do events that
happen every month ([`recurring_events:`](#add-a-recurring-event)).

**Events over several days** (the Area assemblies): write only dates, `start: 2027-03-19` and
`end: 2027-03-21` (the last day). The site shows the range ("Fri, Mar 19 – Sun, Mar 21, 2027") and
keeps the event listed until its last day is over.

**Details not final yet** (a date is set, the venue is not): add `tentative: true` (or `yes` / `sí`)
and, for the place, `location: "Venue to be announced"` with `location_es: "Lugar por anunciarse"`.
The event shows a **"Details to be confirmed"** badge everywhere and calendar apps mark it tentative.

### When an assembly's details are final

1. Open its file in [`content/events/`](content/events/) (for example
   `2027-06-25-neta65-summer-assembly.md`) and click the **pencil icon**.
2. Put the real venue and address in `location:` and **delete the `location_es:` line** (an address
   needs no translation).
3. Correct `start:` / `end:` if the dates changed, and update the description and `summary_es:`
   (the host districts, the format …). Add `url:` with the event's page on neta65.org if there is one.
4. **Delete the `tentative: true` line.**
5. **Commit changes.** About 10–20 minutes later the Events page, the home page, the digest and
   everyone's subscribed calendar show it as confirmed, with the new place.

(A date change only needs the file renamed if you want the name to match; the site reads the date
from `start:`.)

### The NETA 65 workshop calendar

`config/site.yml` → `sources:` → `ics_feeds:` lists the NETA 65 workshop calendar
(`https://neta65.org/events/category/workshop/list/?ical=1`). When the site can read it, its workshops
appear on the Events page by themselves, with the NETA 65 events. A workshop that is also in
`content/events` is shown **once**: your file wins (with your own Spanish) and the calendar only fills
in what the file leaves out, such as the flyer. The two are matched when they start the same day and
link the same neta65.org event page (`url:`), or have a similar title at about the same time — so a
workshop or a booth *at* an assembly stays its own event. When the calendar says something your file
does not (another date or time for the same event page, or the venue of an event your file still calls
"Venue to be announced"), the run summary on the **Actions** tab shows a **Check:** line naming the
file, so you can update it.

**Right now neta65.org blocks it.** The Area website is behind Cloudflare's "Just a moment…" bot
check, which turns away every robot, including ours (the site does not try to get around it). The
**Status** page says so under *Other calendars we read*. Until it is unblocked, a workshop or an
assembly shows on the Events page **only after someone on the committee adds it to `content/events`**
by hand — check <https://neta65.org/events/category/workshop/> now and then. The robot asks at most
once a day, and a blocked calendar never opens the *"A content source has stopped updating"* issue.

**To get it unblocked,** send this to the Area webmaster:

> Our committee website reads the public workshop calendar
> `https://neta65.org/events/category/workshop/list/?ical=1` once a day, but Cloudflare's bot check
> answers "403 Just a moment…". Could you add a Cloudflare WAF custom rule (Security → WAF →
> Custom rules) with the action **Skip** for requests whose URI query string contains `ical=1`, or
> whose User-Agent starts with `NETA65-GrapevineCommitteeBot`? ("Allow verified bots" alone is not
> enough: our small robot is not on Cloudflare's list.)

After that, nothing needs changing here: the next daily update reads it, and the Status page shows
*Working*.

---

## 7. Running the update right now

1. Open the repository on GitHub → **Actions** tab.
2. Click **Update & Deploy** in the left list.
3. Click **Run workflow** (right side) and choose:
   - **crawl_minutes** — how long to search aagrapevine.org / aalavina.org for PDFs.
     Leave it **empty** to use the daily setting (normally 40 minutes). Use up to `300` only for a
     big catch-up (see the [first-run checklist](#11-first-run-checklist)).
   - **skip_crawl** — tick it for a **quick refresh** (about 10–20 minutes): only **Google Drive**,
     **announcements**, the **podcasts** and the **daily quote** are updated. Videos, magazine stories, Instagram and
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
  when it last updated, how many items it has and any problem, plus a small **Document library** panel
  (how many documents have a preview, last update). How far the PDF search has got (pages known ·
  crawled) is in each run's summary on the **Actions** tab (the **PDF crawl** line) and in
  `data/site/status.json` → `crawl` — the public page does not describe the crawl.
- **The Actions tab** on GitHub — each run shows a green ✓ or a red ✗. Click a run to see two summary
  tables (every step of the update, and every source). A yellow ⚠ warning means one source had a
  bad day; the site still published (with that source's previous items). Below the tables,
  **Notes** lists small hiccups of sources that still updated (only worth a look if the same note
  repeats for a week), and **New podcast feeds found** appears when the Grapevine or La Viña site
  links a podcast the website does not show yet. Nothing is added by itself: to show it, add a
  `- key:` / `name:` / `feed:` entry like the two already under `podcasts:` in `config/site.yml`
  (or send the address to whoever helps with the website).
- **The badge** at the top of this page is green when the last update succeeded.
- **"Code check" runs:** when a settings, content or code file is saved, a **Code check** run also
  appears next to **Update & Deploy**. It builds a test copy of the site and runs the automatic tests;
  nothing is published. A red ✗ there means that change broke something: undo it from the file's
  **History** (or send the run to whoever helps with the website). The live site keeps working either way.
- **E-mail when a run fails:** GitHub → your picture → Settings → Notifications → *Actions* →
  "Only notify for failed workflows". **Good to know:** e-mails about the *daily* run go to the
  person who last switched the workflow on. To make sure they come to **you**: **Actions** →
  **Update & Deploy** → **⋯** (top right) → **Disable workflow**, then **Enable workflow**.
- **An issue when a source stops updating:** if the same source (for example Google Drive) has not
  updated for **7 days**, the site opens one issue titled **"A content source has stopped
  updating"** in this repository's **Issues** tab, explaining what to check. GitHub e-mails it to
  everyone who *watches* the repository (**Watch** button at the top → **All Activity**, or
  **Custom → Issues**). The issue closes by itself once the source works again. The optional
  **other calendars** (`ics_feeds:`) never open this issue: the Status page explains them under
  *Other calendars we read*, and the run summary lists them as information only.

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

### Meeting-list keys (Dallas, Fort Worth) — usually nothing to do

The Meetings page lists the Grapevine meetings of the intergroups' public meeting lists
(`meetings:` in `config/site.yml`). Dallas Intergroup and the Fort Worth Central Office answer
their full list only with a key. The key is already stored in the settings (`feed_obf`), hidden the
same way the Rowlett Group's meetings page hides it (written backwards and base64-encoded — this
only keeps it from casual reading, it is not encryption). Each run tries the keys in this order:

1. the optional GitHub secret **`TSML_KEY_AADALLAS`** / **`TSML_KEY_FORTWORTHAA`** (the key alone, or the whole
   list address with `&key=…`);
2. that office's `feed_obf` in `config/site.yml`;
3. the Rowlett Group's `meetings.html` (`meetings.key_source.url`, the constant named in `key_const`).

A key the office refuses (HTTP 401 / 403) is skipped and the next one is tried; a warning on the run (and
in `data/raw/meetings.json` → `stats.warnings`) names the refused source, so a stale secret or `feed_obf`
can be updated. A key is only ever sent to its own office's site (a redirect to another site is not
followed), and no key is written anywhere in plain text. If an office gives out a new key, either add it
as the secret, or run
`python -m scripts.sync.meetings --obfuscate "<the full list address with the new key>"` and paste the
printed value into that office's `feed_obf`. Without any working key, the office's public meeting page is
read instead, so the meetings keep showing. If a list cannot be read — or comes back empty, or its page
changed format — that office's previous meetings stay on the site until it works again.

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

The day and the number of days covered are set in `config/site.yml` → `digest:`. The meeting box uses
the same `meeting:` settings as the website, including your `note` (and `note_es`, if you add one). If
nothing is new that week, no e-mail is sent. To stop the digest, delete the `SMTP_PASSWORD` secret.

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
    after the first run, open the run's summary on the **Actions** tab — if its **PDF crawl** line shows
    only a small part of the known pages crawled, you may run it once more with `300`. (The committee's first PDF search was run
    ahead of time and saved, so normally `40` is right. Without it, the daily 40-minute search still
    reaches every page within about 10 days.)
- [ ] When the run shows a green ✓, open the website and its **Status** page.
- [ ] *(Recommended)* Turn on failure e-mails, and make sure they come to you (see
  [section 8](#8-is-everything-working)).

### What to expect on day 1

- **Magazine stories, both podcasts, videos and Instagram** appear on the first run.
- **The PDF Library** shows what the first PDF search found and keeps growing and re-checking daily.
- **Committee sections** (Portfolio, Photos, flyer events, Drive announcements) show a friendly
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
  **Code check** has built the website with the update and run the tests: **merge only if the pull request
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
| The PDF library looks small | Usually nothing is wrong: the PDF search has checked every page of both sites (about 3,450 pages) and found about 130 PDFs; the Library shows the official ones, each once (about 90 entries: copies, older versions and language editions are merged) | Open the last **Update & Deploy** run's summary → the **PDF crawl** line (or `data/site/status.json` → `crawl`). If (nearly) every known page is crawled, the library is complete and there is nothing to do. Only if few are (for example after the crawl's saved progress was deleted) run **Update & Deploy** once with `crawl_minutes = 300`. The run log's `pdf_curate` lines say which documents were merged and why. |
| Site shows "404 — There isn't a GitHub Pages site here" | Pages not switched to GitHub Actions | **Settings → Pages → Source: GitHub Actions**, then run **Update & Deploy**. |
| Run fails at "Read GitHub Pages settings" | Same as above | Same as above. |
| Run fails at "Commit refreshed data" with *permission denied* / *403* / *protected branch* | A rule on `main` stops the bot from saving its data | If `main` has branch protection or a ruleset, add **GitHub Actions** to its bypass list (**Settings → Rules** or **Settings → Branches**). The workflow already asks for write access itself; *Workflow permissions* does not need changing. |
| Status page: a calendar under *Other calendars we read* says "Blocked by the site's bot protection" | That website (neta65.org) turns robots away with Cloudflare | Until it is fixed, a workshop or assembly shows on the Events page only after someone adds it to `content/events` by hand ([how](#6-announcements-and-events-without-drive-optional)). To fix it, ask the site's webmaster to let calendar requests through ([what to send](#the-neta-65-workshop-calendar)). |
| An issue "A content source has stopped updating" appeared | One source has not updated for 7 days (the site keeps its older items) | Open the issue: it names the source, the error and what to check (for Google Drive: is the folder still shared "Anyone with the link"?). It closes itself when the source works again. |
| Yellow ⚠ "Translation models missing" or "Translation is not working" | The free translation models could not be downloaded (their website was down or moved) | New titles stay in their original language; nothing else is affected. If it lasts more than a few days, send the run's log to whoever helps with the website. |
| Run fails at "Publish to GitHub Pages" with *environment protection* | The `github-pages` environment only allows certain branches | **Settings → Environments → github-pages** → allow the `main` branch. |
| The weekly e-mail did not arrive | Secrets missing, wrong app password, not the configured weekday, or nothing new that week | Open the **Weekly e-mail digest** run: it says exactly which. Gmail needs an **app password**. |
| An issue "Broken links found by the weekly check" appeared | A link in the settings or in a `content/` file moved | Open the issue; fix the address in `config/site.yml` or the `content/` file. It closes itself when fixed. |

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

**Page design (for developers).** Every page is built from the same shared pieces, so the site
reads as one: the hero (`ui.pageHero`, with the grapevine art), section headers (`ui.sectionHead`
in eyebrow form), the in-page nav of long pages (`ui.pageNav`), "Updated … ago" (`ui.freshness`),
closing link cards (`ui.nextSteps`), the collapsed "For committee members" help (`ui.memberHelp`),
the page-level "nothing yet" card (`ui.pageEmpty`) and the in-list "no results" box
(`ui.emptyState`) — all in `src/_includes/macros/ui.njk`, each documented at the top of that file.
The matching CSS utilities (cards, buttons, chip rows, `sticky-aside`, `meta-row`, `tap-link`,
`step-num` …) and the rules for what may stick to the screen are documented at the top of
`src/assets/css/main.css`. Two rules to keep: nothing may cover content (sticky columns end above
the language banner and never grow taller than the window), and the one gap before the footer is
the footer's own margin.

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
