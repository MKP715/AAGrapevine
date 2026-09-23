// Area filters — Read / Share Your Story / Subscribe / GVR-RLV Corner / About.
// Auto-loaded by eleventy.config.js. All filter names are prefixed with "read"
// so they never collide with other page areas.
//
// Everything here is defensive on purpose: the synced data changes every day
// and may contain odd titles ("Read more"), missing issue keys, missing cover
// files, etc. These helpers turn whatever arrives into clean, presentable view
// models so the templates stay simple. Data shapes: docs/DATA_SCHEMA.md §5.
//
// Dev switch: READ_EMPTY=1 npx @11ty/eleventy … builds these pages as if no
// data had been synced yet (to check the empty states).

import fs from "node:fs";
import path from "node:path";

const EMPTY = !!process.env.READ_EMPTY;
const TZ = "America/Chicago";
const LOCALES = { en: "en-US", es: "es-US" };

/* Titles that are really link labels, not titles ("Read", "Leer más"…). */
const GENERIC_TITLE = /^\s*(read|read more|read it|read now|more|more\.{0,3}|continue reading|click here|here|download|leer|leer m[aá]s|lee m[aá]s|m[aá]s|seguir leyendo|descargar|aqu[ií]|haga clic aqu[ií])\s*[.!…»>]*\s*$/i;
/* Link labels that merely *contain* "read more" ("lee más NOTICIAS aquí"). */
const LINKISH_TITLE = /\b(read more|lee m[aá]s|leer m[aá]s|click here|haga clic)\b/i;
/* Bylines that add nothing when repeated on every story. */
const EMPTY_AUTHOR = /^\s*(anonymous|an[oó]nimo|an[oó]nima|anon\.?|n\/a|none|staff|editor(s)?|—|-)?\s*$/i;
/* Magazine sections: the issue's theme section comes first, "In every issue" departments last. */
const FEATURED_SECTION = /featured|special section|secci[oó]n especial|destacad/i;
const EVERY_ISSUE_SECTION = /in every issue|en cada (edici[oó]n|n[uú]mero)/i;
/* Editorial-calendar entries that are reprint issues, not a call for stories. */
const CLASSIC_ISSUE = /\bclassic grapevine\b|grapevine story archive|archivo de historias/i;

/* ------------------------------------------------------------------ */
/*  small utils                                                        */
/* ------------------------------------------------------------------ */
const arr = (v) => (EMPTY ? [] : Array.isArray(v) ? v : []);
const live = (items) => arr(items).filter((i) => i && i.status !== "gone");
/** Accepts a whole site file ({items, issues…}) or a bare items array. */
const itemsOf = (v) => live(Array.isArray(v) ? v : v && v.items);

function safeDecode(s) {
  let out = String(s || "");
  for (let i = 0; i < 2 && /%[0-9a-f]{2}/i.test(out); i++) {
    try { out = decodeURIComponent(out); } catch { out = out.replace(/%20/g, " "); break; }
  }
  return out.normalize("NFC");
}

function basename(url) {
  try { return new URL(url).pathname.split("/").filter(Boolean).pop() || ""; } catch { return String(url || "").split(/[?#]/)[0].split("/").pop() || ""; }
}

function capFirst(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
const hasMachine = (obj, lang) => !!obj && Array.isArray(obj.machine) && obj.machine.includes(lang);
const squash = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[^\p{L}\d]+/gu, "");
/* Degenerate machine translation ("information information information …"). */
const degenerate = (s) => /\b(\p{L}+)(\s+\1\b){2,}/iu.test(String(s || ""));

/* Site-relative cached asset ("/assets/cache/…") → the same path if the file exists. */
const assetSeen = new Map();
function localAsset(p) {
  const s = String(p || "");
  if (!/^\/assets\/[\w\-./]+$/.test(s) || s.includes("..")) return "";
  if (!assetSeen.has(s)) {
    let ok = false;
    try { ok = fs.statSync(path.join("src", s)).size > 0; } catch { ok = false; }
    assetSeen.set(s, ok);
  }
  return assetSeen.get(s) ? s : "";
}

/** "revGV_ORDER_FORM_GIFT--v52424.pdf" → "GV ORDER FORM GIFT"; "La-Vin%CC%83a-Form%5B30%5D" → "La Viña Form" */
export function cleanFileTitle(s) {
  let t = safeDecode(s);
  t = t.replace(/\.(pdf|docx?|pptx?|jpe?g|png)(\.pdf)?$/i, "");
  t = t.replace(/^rev(?=[A-Z])/, "");                 // "revSubscription…" (revision prefix)
  t = t.replace(/\s*\[\d+\]\s*$/, "");                 // "[30]" upload counters
  t = t.replace(/[_]+/g, " ");
  t = t.replace(/(?<=[\p{L}\d])-{1,2}(?=[\p{L}\d])/gu, " ");
  t = t.replace(/(?<=\p{Ll}{2})(?=\p{Lu}\p{Ll})/gu, " ").replace(/\bYou Tube\b/g, "YouTube"); // "EditorialCalendar" → "Editorial Calendar"
  t = t.replace(/\s+v?\d{5,6}$/i, "");                 // " v52424", " 082026" revision codes
  t = t.replace(/\s+0$/, "");                          // Drupal duplicate suffix "_0"
  t = t.replace(/\s{2,}/g, " ").trim().replace(/^[-–.\s]+|[-–.\s]+$/g, "");
  return capFirst(t);
}

/** "GV_Catalog_2026.pdf", "Temas-de-LV", "La-Vin%CC%83a" — a file name rather than a real title. */
const looksLikeFileName = (s) => /_|%[0-9a-f]{2}|\.(pdf|docx?|pptx?)$/i.test(String(s || "")) || !/\s/.test(String(s || "").trim());

/** Slug → readable title:".../sense-belonging" → "Sense belonging" (last-resort fallback only). */
function slugTitle(url) {
  const b = safeDecode(basename(url)).replace(/\.[a-z0-9]{2,5}$/i, "");
  return capFirst(b.replace(/[-_]+/g, " ").replace(/\s{2,}/g, " ").trim());
}

function pick(item, field, lang, helpers) {
  try { return String(helpers.pickLang(item, field, lang) || ""); } catch { return String(item?.[field] || ""); }
}

/** A translated field of any object with {i18n, lang, machine}: text + the language it is really in. */
function tr(obj, field, lang, original) {
  const orig = String(original ?? obj?.[field] ?? "").trim();
  const t = String(obj?.i18n?.[field]?.[lang] ?? "").trim();
  const srcLang = obj?.lang || "";
  if (!t || t === orig || degenerate(t)) return { text: orig, lang: srcLang || lang, machine: false, original: "" };
  const machine = srcLang !== lang && hasMachine(obj, lang);
  return { text: t, lang, machine, original: srcLang && srcLang !== lang ? orig : "" };
}

/* Today's date in Chicago as a UTC-noon Date (so day math never shifts). */
function todayChicagoNoon() {
  const ymd = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  return new Date(ymd + "T12:00:00Z");
}
function ymdNoon(v) {
  const m = String(v || "").match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? new Date(m[1] + "T12:00:00Z") : null;
}

/* Which publication an item belongs to: "gv" (Grapevine) or "lv" (La Viña). */
export function pubOf(item) {
  if (!item) return "gv";
  const e = item.extra || {};
  const p = e.publication || item.publication || item.category;
  if (p === "gv" || p === "lv") return p;
  if (p === "rlv") return "lv";
  if (item.source === "lavina") return "lv";
  if (item.source === "grapevine") return "gv";
  const refs = (e.referrers || []).map((r) => r && r.url).join(" ");
  const host = e.host === "www.aa.org" ? refs : e.host || item.url || "";
  return /lavina/i.test(host) ? "lv" : "gv";
}

/** Language the DOCUMENT is written in (not the title): extra.doc_lang, else item.lang. */
export function docLang(item) {
  const d = (item && item.extra && item.extra.doc_lang) || (item && item.lang) || "";
  return d === "und" ? "" : d;
}

/* ------------------------------------------------------------------ */
/*  Articles & issues                                                  */
/* ------------------------------------------------------------------ */

/** View model for one magazine article in the page language. */
export function articleView(item, lang, helpers) {
  const orig = String(item.title || "").trim();
  const e = item.extra || {};
  let title, titleLang, original = "", machine = false, fallback = false;
  if (!orig || GENERIC_TITLE.test(orig)) {
    // Safety net: the sync caught a "Read" link label instead of the headline.
    title = slugTitle(item.url) || orig;
    titleLang = item.lang || "en";
    fallback = true;
  } else {
    const t = pick(item, "title", lang, helpers).trim() || orig;
    const ok = t !== orig && !degenerate(t);
    title = ok ? t : orig;
    machine = ok && hasMachine(item, lang);
    titleLang = ok ? lang : item.lang || lang;
    if (ok && item.lang && item.lang !== lang) original = orig;
  }
  let summary = pick(item, "summary", lang, helpers).trim();
  if (GENERIC_TITLE.test(summary) || degenerate(summary)) summary = "";
  const sumMachine = !!summary && summary !== String(item.summary || "").trim() && hasMachine(item, lang);
  const sectionOrig = String(e.section || "").trim();
  const sec = tr(item, "section", lang, sectionOrig);
  const author = String(e.author || "").trim();
  const dept = e.department === true || EVERY_ISSUE_SECTION.test(sectionOrig);
  return {
    id: item.id,
    url: item.url,
    pub: pubOf(item),
    title,
    titleLang,
    original,
    origLang: item.lang || "",
    summary,
    summaryLang: sumMachine ? lang : item.lang || lang,
    machine: machine || sumMachine,
    fallback,
    section: sec.text,
    sectionOrig,
    sectionLang: sec.lang,
    rank: dept ? 2 : FEATURED_SECTION.test(sectionOrig) ? 0 : 1,
    author: EMPTY_AUTHOR.test(author) ? "" : author,
    authorLocation: EMPTY_AUTHOR.test(author) ? "" : String(e.author_location || "").trim(),
    free: e.free === true ? true : e.free === false ? false : null,
    isNew: !!item.is_new,
    date: item.date || null,
  };
}

function ymOf(v) {
  const m = String(v || "").match(/^(\d{4})-(\d{2})/);
  return m ? `${m[1]}-${m[2]}` : "";
}

/** Issue metadata (cover, theme, description…) from articles.json `issues` (list, or the raw {id: {...}} map). */
function issueMetaList(file) {
  if (EMPTY || !file || Array.isArray(file)) return [];
  const raw = file.issues;
  const list = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? Object.values(raw) : [];
  return list.filter((m) => m && typeof m === "object");
}

/** Group articles into issues (newest first) and attach the issue metadata.
 *  `file` = the whole articles.json object (or a bare items array). pub = "gv" | "lv" | "" (both). */
export function groupIssues(file, pub) {
  const map = new Map();
  const ensure = (p, key) => {
    const id = `${p}-${key}`;
    if (!map.has(id)) map.set(id, { id, pub: p, key, label: "", topic: "", date: null, items: [], urls: new Set(), newCount: 0, meta: null });
    return map.get(id);
  };
  for (const it of itemsOf(file)) {
    if (it.kind && it.kind !== "article") continue;
    const p = pubOf(it);
    if (pub && p !== pub) continue;
    const e = it.extra || {};
    const key = /^\d{4}-\d{2}$/.test(e.issue_key || "") ? e.issue_key : ymOf(e.issue_key) || ymOf(e.issue_date) || ymOf(it.date) || "undated";
    const g = ensure(p, key);
    if (g.urls.has(it.url)) continue;          // same story listed twice on the source
    g.urls.add(it.url);
    g.items.push(it);
    if (!g.date && it.date) g.date = it.date;
    if (!g.label && e.issue_label) g.label = String(e.issue_label);
    if (!g.topic && (e.issue_theme || e.topic)) g.topic = String(e.issue_theme || e.topic);
    if (it.is_new) g.newCount++;
  }
  // Issues known from the magazine pages (cover + theme) even before their stories are listed.
  for (const m of issueMetaList(file)) {
    const p = m.publication === "lv" || m.publication === "gv" ? m.publication : pubOf(m);
    if (pub && p !== pub) continue;
    const key = /^\d{4}-\d{2}$/.test(m.key || "") ? m.key : ymOf(m.key) || ymOf(String(m.id || "").split(":")[1]);
    if (!key) continue;
    const g = ensure(p, key);
    if (!g.meta) g.meta = m;
    if (!g.label && m.label) g.label = String(m.label);
    if (!g.topic && m.theme) g.topic = String(m.theme);
  }
  const out = [...map.values()].map(({ urls, ...g }) => ({ ...g, count: g.items.length }));
  out.sort((a, b) => (a.key === "undated") - (b.key === "undated") || (a.key < b.key ? 1 : a.key > b.key ? -1 : a.pub.localeCompare(b.pub)));
  return out;
}

const monthName = (y, m, lang, withYear) =>
  new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { month: "long", ...(withYear ? { year: "numeric" } : {}), timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, 15)));

/** Every issue that has stories, except the newest one of each publication (those are "current"). */
export function archiveIssues(file) {
  const all = groupIssues(file, "");
  const current = new Set();
  for (const p of ["gv", "lv"]) { const c = all.find((i) => i.pub === p); if (c) current.add(c.id); }
  return all.filter((i) => !current.has(i.id) && i.count > 0);
}

/** Localized issue label computed from the key: GV "October 2026"; LV "September–October 2026". */
export function issueLabel(issue, lang) {
  if (!issue) return "";
  const m = String(issue.key || "").match(/^(\d{4})-(\d{2})$/);
  if (!m) return issue.label || "";
  const y = Number(m[1]), mo = Number(m[2]);
  const lbl = issue.label || "";
  const bimonthly = issue.pub === "lv" ? !lbl || /[-–—\/]|\b(y|and)\b/i.test(lbl) : /[-–—\/]/.test(lbl);
  if (!bimonthly) return capFirst(monthName(y, mo, lang, true));
  const y2 = mo === 12 ? y + 1 : y, mo2 = mo === 12 ? 1 : mo + 1;
  const a = capFirst(monthName(y, mo, lang, false)), b = capFirst(monthName(y2, mo2, lang, false));
  return y2 === y ? `${a}–${b} ${y}` : `${a} ${y}–${b} ${y2}`;
}

/** Issue name for use inside a sentence ("For the {issue} issue"): lower-case months in Spanish. */
function issueInSentence(key, pub, rawLabel, lang) {
  const s = issueLabel({ key, pub, label: rawLabel || (pub === "lv" ? "a-b" : "single") }, lang);
  return lang === "es" ? s.toLowerCase() : s;
}

/* Month names (EN + ES, with common abbreviations) → month number. */
const MONTHS = {
  january: 1, jan: 1, enero: 1, ene: 1, february: 2, feb: 2, febrero: 2, march: 3, mar: 3, marzo: 3,
  april: 4, apr: 4, abril: 4, abr: 4, may: 5, mayo: 5, june: 6, jun: 6, junio: 6, july: 7, jul: 7, julio: 7,
  august: 8, aug: 8, agosto: 8, ago: 8, september: 9, sept: 9, sep: 9, septiembre: 9, setiembre: 9,
  october: 10, oct: 10, octubre: 10, november: 11, nov: 11, noviembre: 11, december: 12, dec: 12, diciembre: 12, dic: 12,
};
/** "January 2027" / "Enero-Febrero 2027" → issue key "2027-01" (+ whether it spans two months). */
function parseIssueLabel(label) {
  const s = String(label || "").trim();
  const m = s.match(/^([A-Za-zÀ-ÿ]+)\.?(?:\s*(?:[-–—\/]|y|and|&)\s*([A-Za-zÀ-ÿ]+)\.?)?\s*(?:de\s+)?(\d{4})$/i);
  if (!m) return null;
  const m1 = MONTHS[m[1].toLowerCase()];
  const m2 = m[2] ? MONTHS[m[2].toLowerCase()] : null;
  if (!m1 || (m[2] && !m2)) return null;
  return { key: `${m[3]}-${String(m1).padStart(2, "0")}`, two: !!m2 };
}
/** "January 2027" / "Enero-Febrero 2027" → the same label in the page language (unchanged if unparseable). */
export function localizeIssueLabel(label, lang, pub) {
  const p = parseIssueLabel(label);
  if (!p) return String(label || "").trim();
  return issueLabel({ key: p.key, pub: pub || (p.two ? "lv" : "gv"), label: p.two ? "a-b" : "single" }, lang);
}

/** Full view model of one issue for a language (cover, theme, description, sorted table of contents). */
export function issueView(issue, lang, helpers) {
  if (!issue) return null;
  const meta = issue.meta || null;
  const first = issue.items[0] || null;
  const views = issue.items.map((i) => articleView(i, lang, helpers));
  // Table of contents: theme section first, then the other sections (A–Z), "In every issue" last.
  const coll = new Intl.Collator(LOCALES[lang] || "en-US", { sensitivity: "base" });
  views.sort((a, b) => a.rank - b.rank || coll.compare(a.sectionOrig, b.sectionOrig) || coll.compare(a.title, b.title));

  // Label: rule-written i18n label from the issue metadata or the articles; else computed from the key.
  const label = (meta && meta.i18n && meta.i18n.label && meta.i18n.label[lang])
    || (first && first.i18n && first.i18n.issue_label && first.i18n.issue_label[lang])
    || issueLabel(issue, lang);
  // Theme: issue metadata first, then the articles' issue_theme / topic.
  let theme = { text: "", lang: "", machine: false, original: "" };
  if (meta && meta.theme) theme = tr(meta, "theme", lang);
  else if (first && ((first.extra || {}).issue_theme || (first.extra || {}).topic)) {
    const e = first.extra;
    theme = e.issue_theme ? tr(first, "issue_theme", lang, e.issue_theme) : tr(first, "topic", lang, e.topic);
  }
  const desc = meta && meta.description ? tr(meta, "description", lang) : { text: "", lang: "", machine: false };
  const firstExtra = (first && first.extra) || {};
  const cover = meta ? localAsset(meta.cover) : "";
  return {
    id: issue.id, pub: issue.pub, key: issue.key,
    label, rawLabel: issue.label,
    topic: theme.text, topicLang: theme.lang || (issue.pub === "lv" ? "es" : "en"), topicMachine: theme.machine,
    description: desc.text, descriptionLang: desc.lang, descriptionMachine: desc.machine,
    cover,
    url: (meta && meta.url) || firstExtra.issue_url || "",
    hub: (meta && meta.hub) || "",
    count: issue.count, newCount: issue.newCount, date: issue.date,
    isNewIssue: issue.count > 0 && issue.newCount === issue.count,
    articles: views,
    featured: views.filter((v) => v.rank === 0),
    others: views.filter((v) => v.rank !== 0),
    featuredSection: (views.find((v) => v.rank === 0) || {}).section || "",
    featuredSectionLang: (views.find((v) => v.rank === 0) || {}).sectionLang || "",
    machineCount: views.filter((v) => v.machine).length,
    freeCount: views.filter((v) => v.free === true).length,
    subscriberCount: views.filter((v) => v.free === false).length,
    featuredCount: views.filter((v) => v.rank === 0).length,
  };
}

/** "2026-09" of the oldest story we saved (when the archive started), or "". */
export function archiveSince(file) {
  let min = "";
  for (const it of itemsOf(file)) {
    const ym = ymOf(it.first_seen);
    if (ym && (!min || ym < min)) min = ym;
  }
  return min;
}

/* ------------------------------------------------------------------ */
/*  PDFs: kits, catalogs, forms, related links                         */
/* ------------------------------------------------------------------ */
function pdfText(item) {
  const e = item.extra || {};
  const bits = [item.title, basename(e.file_url || item.url), ...(e.link_texts || []), item.category, ...(item.tags || [])];
  return safeDecode(bits.filter(Boolean).join(" "))
    .replace(/(?<=\p{Ll})(?=\p{Lu})/gu, " ")   // "EditorialCalendar" → "Editorial Calendar"
    .replace(/[_\-.%]+/g, " ");
}
const tagsOf = (item) => [...(Array.isArray(item.tags) ? item.tags : []), item.category].filter(Boolean).map(String);
const hasTag = (item, t) => tagsOf(item).includes(t);

/** Clean, human title for a PDF.
 *  The title stays in its ORIGINAL language (it is the document's real name, and
 *  the document itself is in that language). When a machine translation exists
 *  for the page language it is returned separately as a small `gloss`. */
export function pdfTitleInfo(item, lang, helpers) {
  const e = item.extra || {};
  const orig = String(item.title || "").trim();
  const fileTitle = cleanFileTitle(basename(e.file_url || item.url));
  const bad = !orig || GENERIC_TITLE.test(orig) || LINKISH_TITLE.test(orig);
  // Real titles keep their punctuation ("By-The-Month Order Form"); only file-name-like ones are cleaned.
  const tidy = (s) => (looksLikeFileName(s) ? cleanFileTitle(s) : safeDecode(s).replace(/\s{2,}/g, " ").trim());
  const title = bad ? fileTitle || orig : tidy(orig) || orig;
  const tLang = (item.lang && item.lang !== "und" ? item.lang : "") || docLang(item);
  let gloss = "";
  if (!bad && tLang && tLang !== lang && hasMachine(item, lang)) {
    const t = tidy(pick(item, "title", lang, helpers).trim());
    if (t && squash(t) !== squash(title) && !degenerate(t) && t.length <= title.length * 2.5 + 12) gloss = t;
  }
  return { title, lang: tLang, gloss, glossLang: lang, machine: !!gloss };
}
export function pdfTitle(item, lang, helpers) { return pdfTitleInfo(item, lang, helpers).title; }

function refMatches(item, re) {
  const refs = (item.extra && item.extra.referrers) || [];
  return refs.some((r) => r && re.test(String(r.url || "")));
}
const byDateDesc = (a, b) => String(b.date || b.extra?.upload_month || "").localeCompare(String(a.date || a.extra?.upload_month || ""));

/** PDFs of the official GVR (Grapevine) or RLV (La Viña) resource kit, newest first. */
export function kitItems(pdfs, which) {
  const re = which === "rlv" ? /\/recursos(\/|$|[?#])/i : /\/gvr-resources(\/|$|[?#])/i;
  const seen = new Set();
  const out = [];
  for (const p of itemsOf(pdfs)) {
    if (!(p.category === which || refMatches(p, re))) continue;
    const key = p.url || p.id;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(p);
  }
  return out.sort(byDateDesc);
}

const KIT_GROUPS = ["news", "guides", "flyers", "forms", "other"];
/* Crawler sub-types (tags / category) → kit group. */
const TAG_GROUP = {
  news: "news", catalog: "news",
  guidelines: "guides", workbook: "guides", service: "guides",
  postcard: "flyers", flyer: "flyers",
  "order-form": "forms",
  literature: "other",
};
/** Sub-type of a kit PDF: the crawler's tag first, keywords in the title/file name as a fallback. */
export function kitGroup(item) {
  for (const t of tagsOf(item)) if (TAG_GROUP[t]) return TAG_GROUP[t];
  const t = pdfText(item);
  if (/\b(order|pedido|form|formulario|pagador|prices?|precios?|back issue|ediciones anteriores|by the month|por ejemplar)\b|subscri|suscri/i.test(t)) return "forms";
  if (/\b(postcard|postal|pc|flyer|volante|poster|cartel|bookmark|marcalibros?|marcador)\b/i.test(t)) return "flyers";
  if (/\b(news|noticias|newsletter|bolet[ií]n)\b|cat[aá]logo?\b/i.test(t)) return "news";
  if (/\b(play|plays|skit|obra|sketch|carol)\b|victor e|man in the bed/i.test(t)) return "other";
  if (/\b(handbook|manual|workbook|libro de trabajo|guides?|gu[ií]as?|guidelines|pautas|checklist|chequeo|today|hoy|self support|automantenimiento|autonom[ií]a|workshop|taller(es)?|pol[ií]tica|history|historia|editorial|calendar|temas|traditions?|tradiciones|12 ways|12 maneras)\b/i.test(t)) return "guides";
  if (/\b(new|nuevos?|book|libros?|audio|audiobook|audiolibro|descarga|download|app|apps|aplicaciones|ctm|carry the message|lleva el mensaje|instagram|youtube|podcast)\b/i.test(t)) return "flyers";
  return "other";
}

export function kitGroups(items) {
  const m = new Map(KIT_GROUPS.map((k) => [k, []]));
  for (const i of items || []) m.get(kitGroup(i)).push(i);
  return KIT_GROUPS.map((key) => ({ key, items: m.get(key) })).filter((g) => g.items.length);
}

/** Magazine catalogs (tag/category "catalog"; or "catalog" in the name — but never postcards or forms). */
export function catalogItems(pdfs) {
  const seen = new Set();
  const out = [];
  for (const p of itemsOf(pdfs)) {
    if (hasTag(p, "postcard") || hasTag(p, "order-form") || hasTag(p, "flyer")) continue;
    const t = pdfText(p);
    const isCat = hasTag(p, "catalog") || (/cat[aá]logo?s?\b|catalogue/i.test(t) && !/\b(postcard|postal|pc|order|pedido)\b/i.test(t));
    if (!isCat || seen.has(p.url)) continue;
    seen.add(p.url);
    out.push(p);
  }
  return out.sort(byDateDesc);
}

/** Official order & subscription forms, split by publication: { gv: [...], lv: [...] } (newest first). */
export function formItems(pdfs) {
  const out = { gv: [], lv: [] };
  const seen = new Set();
  for (const p of itemsOf(pdfs).slice().sort(byDateDesc)) {
    if (!(hasTag(p, "order-form") || (kitGroup(p) === "forms" && (p.category === "gvr" || p.category === "rlv")))) continue;
    const k = squash(cleanFileTitle(p.title || basename(p.url)));
    if (seen.has(p.url) || seen.has(k)) continue;
    seen.add(p.url); seen.add(k);
    out[p.category === "rlv" ? "lv" : p.category === "gvr" ? "gv" : pubOf(p)].push(p);
  }
  return out;
}

/** Newest PDFs whose name matches a keyword regex (for "related downloads" boxes). */
export function pdfsMatching(pdfs, pattern, n = 4) {
  let re;
  try { re = new RegExp(pattern, "i"); } catch { return []; }
  const seen = new Set();
  const out = [];
  const sorted = itemsOf(pdfs).slice().sort(byDateDesc);
  for (const p of sorted) {
    if (!re.test(pdfText(p))) continue;
    const k = squash(cleanFileTitle(p.title || basename(p.url)));
    if (seen.has(p.url) || seen.has(k)) continue;
    seen.add(p.url); seen.add(k);
    out.push(p);
    if (out.length >= n) break;
  }
  return out;
}

/** Writer's toolkit for the Share-your-story page: one PDF per need, in this order. */
const WRITER_KIT = [
  /writing workshop|taller(es)? de escritura/i,
  /record your story guidelines|gu[ií]a.*graba/i,
  /guidelines for contributing|contributing to (the )?(gv|grapevine)/i,
  /pautas para colaborar|gu[ií]a para (las )?contribuciones/i,
];
export function writerKit(pdfs, n = 4) {
  const all = itemsOf(pdfs).slice().sort(byDateDesc);
  const out = [];
  const seen = new Set();
  for (const re of WRITER_KIT) {
    const p = all.find((x) => !seen.has(x.url) && re.test(pdfText(x)) && !hasTag(x, "postcard"));
    if (p) { seen.add(p.url); out.push(p); }
    if (out.length >= n) break;
  }
  return out;
}

/* ------------------------------------------------------------------ */
/*  Editorial themes & deadlines                                       */
/* ------------------------------------------------------------------ */
function themeView(it, pub, lang, helpers) {
  const e = it.extra || {};
  const origTheme = String(it.title || e.theme || "").trim();
  const t = pick(it, "title", lang, helpers).trim();
  const ok = t && t !== origTheme && !degenerate(t);
  const theme = ok ? t : origTheme;
  const machine = ok && hasMachine(it, lang);
  let summary = pick(it, "summary", lang, helpers).trim();
  if (degenerate(summary)) summary = String(it.summary || "").trim();
  const sumMachine = !!summary && summary !== String(it.summary || "").trim() && hasMachine(it, lang);
  const key = /^\d{4}-\d{2}$/.test(e.issue_key || "") ? e.issue_key : (parseIssueLabel(e.issue_label) || {}).key || "";
  return {
    item: it, theme,
    themeLang: ok ? lang : it.lang || lang,
    original: ok && it.lang && it.lang !== lang ? origTheme : "",
    origLang: it.lang || "",
    machine,
    summary, summaryLang: sumMachine ? lang : it.lang || lang, summaryMachine: sumMachine,
    issueKey: key,
    issueLabel: key ? issueInSentence(key, pub, e.issue_label, lang) : localizeIssueLabel(e.issue_label, lang, pub),
    url: it.url,
  };
}

/** One publication's editorial calendar for a language:
 *  { upcoming (dated, soonest first; [0].isNext), evergreen (suggested topics with no deadline),
 *    past (recently closed), total, calendarPdf, guidelinesUrl }. */
export function editorialFor(items, pub, helpers, lang, pastN = 3) {
  const today = todayChicagoNoon();
  const thisMonth = today.toISOString().slice(0, 7);
  const rows = [];
  const evergreen = [];
  let calendarPdf = "", guidelinesUrl = "";
  const seen = new Set();
  for (const it of itemsOf(items)) {
    if (pubOf(it) !== pub) continue;
    const e = it.extra || {};
    if (!calendarPdf && /^https?:\/\//.test(e.pdf_url || "")) calendarPdf = e.pdf_url;
    if (!guidelinesUrl && /^https?:\/\//.test(e.guidelines_url || "")) guidelinesUrl = e.guidelines_url;
    const v = themeView(it, pub, lang, helpers);
    const dl = ymdNoon(e.deadline);
    const dedupe = `${squash(v.theme)}|${e.deadline || ""}|${v.issueKey}`;
    if (seen.has(dedupe)) continue;
    seen.add(dedupe);
    if (e.evergreen === true || (!dl && !v.issueKey)) { evergreen.push(v); continue; }
    if (!dl) {
      // Calendar entries without a deadline: reprint ("Classic") issues are not a call
      // for stories, and past issues are over. Keep only future ones.
      if (CLASSIC_ISSUE.test(`${it.title || ""} ${it.summary || ""}`) || (v.issueKey && v.issueKey < thisMonth)) continue;
      rows.push({ ...v, deadline: null, days: null, past: false });
      continue;
    }
    const days = Math.round((dl - today) / 864e5);
    rows.push({ ...v, deadline: String(e.deadline).slice(0, 10), days, past: days < 0 });
  }
  const upcoming = rows.filter((r) => !r.past).sort((a, b) =>
    (a.deadline ? 0 : 1) - (b.deadline ? 0 : 1) || String(a.deadline || a.issueKey).localeCompare(String(b.deadline || b.issueKey)) || String(a.issueKey).localeCompare(String(b.issueKey)));
  const past = rows.filter((r) => r.past).sort((a, b) => String(b.deadline).localeCompare(String(a.deadline))).slice(0, pastN);
  if (upcoming[0] && upcoming[0].deadline) upcoming[0].isNext = true;
  const coll = new Intl.Collator(LOCALES[lang] || "en-US", { sensitivity: "base" });
  evergreen.sort((a, b) => coll.compare(a.theme, b.theme));
  return { upcoming, evergreen, past, total: upcoming.length + evergreen.length + past.length, calendarPdf, guidelinesUrl };
}

/* ------------------------------------------------------------------ */
export default function (eleventyConfig, helpers) {
  const h = helpers || {};
  eleventyConfig.addFilter("readPub", (item) => pubOf(item));
  eleventyConfig.addFilter("readDocLang", (item) => docLang(item));
  eleventyConfig.addFilter("readIssues", (file, pub) => groupIssues(file, pub || ""));
  eleventyConfig.addFilter("readArchive", (file) => archiveIssues(file));
  eleventyConfig.addFilter("readArchiveSince", (file) => archiveSince(file));
  eleventyConfig.addFilter("readIssueView", (issue, lang) => issueView(issue, lang, h));
  eleventyConfig.addFilter("readIssueLabel", (issue, lang) => issueLabel(issue, lang));
  eleventyConfig.addFilter("readArticle", (item, lang) => articleView(item, lang, h));
  eleventyConfig.addFilter("readPdfTitle", (item, lang) => pdfTitle(item, lang, h));
  eleventyConfig.addFilter("readPdfTitleInfo", (item, lang) => pdfTitleInfo(item, lang, h));
  eleventyConfig.addFilter("readKit", (pdfs, which) => kitItems(pdfs, which));
  eleventyConfig.addFilter("readKitGroups", (items) => kitGroups(items));
  eleventyConfig.addFilter("readCatalogs", (pdfs) => catalogItems(pdfs));
  eleventyConfig.addFilter("readForms", (pdfs) => formItems(pdfs));
  eleventyConfig.addFilter("readWriterKit", (pdfs, n) => writerKit(pdfs, n || 4));
  eleventyConfig.addFilter("readPdfsMatching", (pdfs, pattern, n) => pdfsMatching(pdfs, pattern, n));
  eleventyConfig.addFilter("readEditorial", (items, pub, lang) => editorialFor(items, pub, h, lang));
  eleventyConfig.addFilter("readItems", (items) => itemsOf(items));      // honours READ_EMPTY
  eleventyConfig.addFilter("readFileTitle", (s) => cleanFileTitle(s));
  eleventyConfig.addFilter("readAsset", (p) => localAsset(p));
  // "(800) 631-6025" → "tel:+18006316025"; "+1 (570) 567-0437" → "tel:+15705670437"
  eleventyConfig.addFilter("readTel", (phone) => {
    const s = String(phone || "").trim();
    const digits = s.replace(/[^\d]/g, "");
    if (!digits) return "";
    return "tel:" + (s.startsWith("+") ? "+" + digits : digits.length === 10 ? "+1" + digits : digits);
  });
  eleventyConfig.addFilter("readMonth", (ym, lang, style) => {
    // "2026-02" → "Feb 2026" / "feb 2026"; style "long" → "February 2026" / "febrero de 2026"
    const m = String(ym || "").match(/^(\d{4})-(\d{2})/);
    if (!m) return "";
    return new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { month: style === "long" ? "long" : "short", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(+m[1], +m[2] - 1, 15)));
  });
  eleventyConfig.addFilter("readSourceCount", (status, name) => {
    const s = ((status && status.sources) || []).find((x) => x.source === name);
    return s && !EMPTY ? Number(s.count) || 0 : 0;
  });
}
