# Announcements

Two ways to post an announcement (both are auto-translated EN ⇄ ES):

1. **Google Drive (easiest):** put a Google Doc (or a `.txt` file) in the
   `announcements` folder inside the current Panel folder. The file name is the
   headline. Start the name with a date to control the date shown, e.g.
   `2027-01-10 Welcome new GVRs`. The document text is the body.
2. **GitHub:** add a Markdown file to this folder, e.g.
   `2027-01-10-welcome-gvrs.md`:

```markdown
---
title: Welcome, new GVRs and RLVs!
date: 2027-01-10
expires: 2027-03-31     # optional — hidden after this date
pinned: false           # optional — keep at the top
title_es: "¡Bienvenidos, nuevos GVR y RLV!"   # optional — your own Spanish
summary_es: "Texto completo del anuncio en español."
---
Write in English **or** Spanish. Links like [aagrapevine.org](https://www.aagrapevine.org) work.
```

**Your own translation (optional).** Add `title_es` and `summary_es` to a file
written in English (or `title_en` and `summary_en` to one written in Spanish)
and the other-language page shows your words instead of an automatic
translation, without the "auto-translated" note. `summary_es` replaces the whole
text below the header on the Spanish page, so write the complete text there.
Whatever you leave out is still translated automatically.

Files whose name starts with `_` or `README` are ignored.
