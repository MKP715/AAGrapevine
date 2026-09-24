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
