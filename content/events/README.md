# Manual events (optional)

Committee meetings are generated automatically from `config/site.yml`, and any
flyer in the Drive `flyers` folder whose file name starts with a date becomes an
event. Use this folder only for events without a flyer. One Markdown file each:

```markdown
---
title: GV/LV booth — Fall Assembly
title_es: Mesa de GV/LV — Asamblea de Otoño      # optional — your own Spanish title
start: 2027-09-18T09:00:00-05:00
end: 2027-09-18T16:00:00-05:00
location: Tyler, TX
url: https://neta65.org
lang: en                                          # the language of the title and the text below
summary_es: "Visita nuestra mesa en la asamblea." # optional — your own Spanish description
---
Optional description (English or Spanish).
```

**Your own translation (optional).** The site translates the title and the
description into the other language automatically, and marks them
"auto-translated". To write them yourself, add `title_es` and `summary_es` to a
file written in English (or `title_en` and `summary_en` to one written in
Spanish). The other-language page then shows your words exactly as written, and
without the "auto-translated" note. `summary_es` is shown **instead of** the
whole description below the header, so write the complete Spanish text there.
Anything you leave out is still translated automatically. Put a value in quotes
when it contains `: ` (for example `title_es: "Taller: Fort Worth"`).

## The place in Spanish: `location_es`

A place is never machine-translated (addresses and group names must stay exactly
as they are). When the place itself needs words in the other language, write them
with `location_es` (or `location_en` in a file written in Spanish):

```markdown
location: "Venue to be announced"
location_es: "Lugar por anunciarse"
```

A place that is not known yet ("Venue to be announced", "TBA", "Lugar por
anunciarse") is shown as plain text, without a map pin, and is left out of the
address field of the calendar files. Without `location_es`, the Spanish page says
"Lugar por anunciarse" by itself. A real address needs no `location_es`.

## Events over several days (assemblies)

Give only dates — no times — and `end` is the **last** day:

```markdown
start: 2027-03-19
end: 2027-03-21
```

The Events page shows the range ("Fri, Mar 19 – Sun, Mar 21, 2027"), the event
stays listed until the end of its last day, and calendars show it on all three days.

## Details not final yet: `tentative: true`

When the date is set but the venue, the host districts or the times are not
confirmed, add:

```markdown
tentative: true            # also works: yes, sí
```

The event then shows a **"Details to be confirmed" / "Detalles por confirmar"**
badge on the Events page, the home page, the monthly digest and the search, and
people's calendar apps mark it as *tentative*.

**When the details are final**, edit the file: put the real place in `location`
(and delete `location_es`, unless the Spanish needs other words), fix the dates
or times if they changed, update the description (and `summary_es`), and
**delete the `tentative: true` line**. The next update (10–20 minutes after you
save) shows it as confirmed everywhere, including in subscribed calendars.

## The same event on the NETA 65 calendar

The site can also read the NETA 65 workshop calendar (`config/site.yml` →
`sources:` → `ics_feeds:`). An event that is both there and in this folder is shown
**once**: this file wins (with your own Spanish), and the calendar only fills in
what the file leaves out (the flyer, the event page link, a venue your file still
gives as "Venue to be announced"). Both must start **the same day**; then they are
matched by their neta65.org event page (`url:`), or by a similar title at about the
same time — so keep `url:` pointing to the event's page on neta65.org when there is
one. A workshop or a booth *at* an assembly is never mixed up with the assembly.

When the calendar says something this file does not — its event page on another
date, another start time, or a real venue while the file says "Venue to be
announced" — the run summary (GitHub → **Actions** → the latest run) shows a
**Check:** line with the file's name. Update the file; nothing else is needed.

While neta65.org blocks our robot (the **Status** page says so), the calendar is
not read at all: a workshop or assembly shows on the Events page only when it has a
file here.
