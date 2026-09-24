// Shop filters (owned by the Shop page, /shop/; also used by the home page's Book of the Month teaser).
// Auto-loaded by eleventy.config.js. They read db.shop (data/site/shop.json, docs/DATA_SCHEMA.md → "shop.json")
// and never invent a price, percent or date: everything shown comes from the synced store data, so the
// Shop page stays the ONE canonical home for these numbers (other pages only show a compact teaser).
//
//   shopBotm(shop, lang)      → Book of the Month views, page-language publication first
//   shopSubs(shop, lang)      → subscription comparison: publications → regions → plan types → terms
//   shopBulk(shop, lang)      → bulk-book discount rows (tiers with a discount) + note + source
//   shopFromMonthly(shop)     → lowest monthly price (for "Subscriptions from $2.99/month"), or null
//   shopMoney(n, lang)        → "$11.99" (USD, the stores' currency)
//   shopToday()               → today's date (YYYY-MM-DD) in the site's time zone, at build time

const TZ = "America/Chicago";
const LOCALES = { en: "en-US", es: "es-US" };
const MAG = { gv: "Grapevine", lv: "La Viña" };
// The language each store writes its own texts in (plan-type descriptions, book titles).
const STORE_LANG = { gv: "en", lv: "es" };
const TYPE_ORDER = ["print", "digital", "complete", "other"];
const REGION_ORDER = ["us", "ca", "intl"];

const round2 = (n) => Math.round(n * 100) / 100;

function pubOrder(lang) {
  return lang === "es" ? ["lv", "gv"] : ["gv", "lv"];
}

export function todayYmd(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

// Whole days from a to b (both YYYY-MM-DD, calendar dates).
function dayDiff(a, b) {
  const pa = String(a).slice(0, 10).split("-").map(Number);
  const pb = String(b).slice(0, 10).split("-").map(Number);
  if (pa.length < 3 || pb.length < 3 || pa.some(isNaN) || pb.some(isNaN)) return null;
  return Math.round((Date.UTC(pb[0], pb[1] - 1, pb[2]) - Date.UTC(pa[0], pa[1] - 1, pa[2])) / 864e5);
}

export function money(n, lang = "en", currency = "USD") {
  const v = Number(n);
  if (n === null || n === undefined || n === "" || !isFinite(v)) return "";
  try {
    return new Intl.NumberFormat(LOCALES[lang] || "en-US", { style: "currency", currency: currency || "USD" }).format(v);
  } catch (e) {
    return "$" + v.toFixed(2);
  }
}

// "October 14" / "14 de octubre" (a calendar date, so formatted in UTC: no time-zone shift).
function dayMonth(ymd, lang) {
  if (!ymd || dayDiff(ymd, ymd) === null) return "";
  const p = String(ymd).slice(0, 10).split("-").map(Number);
  try {
    return new Intl.DateTimeFormat(LOCALES[lang] || "en-US", { month: "long", day: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(p[0], p[1] - 1, p[2])));
  } catch (e) {
    return String(ymd);
  }
}

function i18nField(item, field, lang) {
  const tr = item && item.i18n && item.i18n[field];
  return (tr && tr[lang]) || "";
}

/* ---------------- Book of the Month ---------------- */
function botmView(b, lang, today) {
  const pub = b.pub === "lv" ? "lv" : "gv";
  const itemLang = b.lang || STORE_LANG[pub];
  const machine = Array.isArray(b.machine) ? b.machine : [];
  // The book is sold under its own title: that is the main title; the page-language title is a subtitle.
  const title = b.title || i18nField(b, "title", itemLang) || i18nField(b, "title", lang);
  const trTitle = lang !== itemLang ? i18nField(b, "title", lang) : "";
  const subtitle = trTitle && trTitle.trim().toLowerCase() !== String(title).trim().toLowerCase() ? trTitle : "";
  const trBlurb = i18nField(b, "blurb", lang);
  const blurb = trBlurb || b.blurb || "";
  const blurbLang = trBlurb ? lang : itemLang;
  const price = Number(b.price);
  const sale = Number(b.sale_price);
  const hasPrices = isFinite(price) && price > 0 && isFinite(sale) && sale > 0 && sale < price;
  const days = b.ends ? dayDiff(today, b.ends) : null;
  return {
    id: b.id || "botm:" + pub,
    pub,
    isLv: pub === "lv",
    mag: MAG[pub],
    itemLang,
    title,
    subtitle,
    subtitleMachine: !!subtitle && machine.includes(lang),
    blurb,
    blurbLang,
    blurbMachine: blurbLang !== itemLang && machine.includes(lang),
    image: b.image || "",
    url: b.url || b.page_url || "",
    pageUrl: b.page_url || "",
    pct: Number(b.discount_pct) || (hasPrices ? Math.round((1 - sale / price) * 100) : 0),
    hasPrices,
    price: hasPrices ? money(price, lang, b.currency) : "",
    sale: hasPrices ? money(sale, lang, b.currency) : "",
    save: hasPrices ? money(round2(price - sale), lang, b.currency) : "",
    saleNum: hasPrices ? sale : null,
    ends: b.ends || "",
    endsLabel: b.ends ? dayMonth(b.ends, lang) : "",
    starts: b.starts || "",
    startsLabel: b.starts ? dayMonth(b.starts, lang) : "",
    monthLabel: i18nField(b, "month_label", lang) || b.month_label || "",
    days,
    ended: days !== null && days < 0,
  };
}

export function shopBotm(shop, lang = "en", today = todayYmd()) {
  const list = (shop && Array.isArray(shop.botm) ? shop.botm : []).filter((b) => b && (b.url || b.page_url));
  const order = pubOrder(lang);
  return list
    .map((b) => botmView(b, lang, today))
    .filter((v) => !v.ended)
    .sort((a, b) => order.indexOf(a.pub) - order.indexOf(b.pub));
}

/* ---------------- Subscriptions ---------------- */
function termKey(m) {
  if (m === 1) return { key: "shop.term_month" };
  if (m && m % 12 === 0) return m === 12 ? { key: "shop.term_year" } : { key: "shop.term_years", vars: { n: m / 12 } };
  if (m) return { key: "shop.term_months", vars: { n: m } };
  return null;
}

function typeView(pub, type, plans, shop, lang, t) {
  const cur = plans[0].currency || "USD";
  const sorted = plans.slice().sort((a, b) => (a.term_months || 999) - (b.term_months || 999));
  const withMonths = sorted.filter((p) => p.term_months && Number(p.price) > 0);
  // Baseline for "save": the shortest term's price per month (monthly plans, or the 1-year plan for print).
  const base = withMonths[0] || null;
  const baseRate = base ? Number(base.price) / base.term_months : null;
  let best = null;
  if (withMonths.length > 1) {
    for (const p of withMonths) if (!best || Number(p.price) / p.term_months < Number(best.price) / best.term_months - 1e-9) best = p;
  }
  const terms = sorted.map((p) => {
    const m = p.term_months || null;
    const price = Number(p.price);
    const tk = termKey(m);
    const perMonth = m && m > 1 && price > 0 ? price / m : null;
    let save = null;
    if (base && m && p !== base && baseRate) {
      const s = round2(baseRate * m - price);
      const key = base.term_months === 1 ? "shop.save_vs_monthly" : base.term_months === 12 ? "shop.save_vs_yearly" : "shop.save_plain";
      if (s >= 0.5) save = t(key, lang, { amount: money(s, lang, cur) });
    }
    return {
      months: m,
      label: tk ? t(tk.key, lang, tk.vars) : p.title || "",
      price: money(price, lang, cur),
      monthly: m === 1,
      perMonth: perMonth ? money(round2(perMonth), lang, cur) : "",
      save,
      best: !!best && p === best,
      url: p.url || "",
      sku: p.sku || "",
      title: p.title || "",
    };
  });
  // Group / volume pricing (print): rows = quantity tiers, columns = terms.
  let volume = null;
  const volPlans = sorted.filter((p) => Array.isArray(p.volume) && p.volume.length);
  if (volPlans.length) {
    const rowsByKey = new Map();
    volPlans.forEach((p, col) => {
      for (const v of p.volume) {
        const k = `${v.min}-${v.max}`;
        if (!rowsByKey.has(k)) rowsByKey.set(k, { min: v.min, max: v.max, prices: new Array(volPlans.length).fill("") });
        rowsByKey.get(k).prices[col] = money(v.price, lang, cur);
      }
    });
    const rows = [...rowsByKey.values()].sort((a, b) => a.min - b.min).map((r) => ({
      label: r.max ? t("shop.copies_range", lang, { a: r.min, b: r.max }) : t("shop.copies_plus", lang, { a: r.min }),
      prices: r.prices,
    }));
    volume = {
      cols: volPlans.map((p) => { const tk = termKey(p.term_months); return tk ? t(tk.key, lang, tk.vars) : p.title; }),
      rows,
    };
  }
  // Description: the store's own words when they are in the page's language (not a machine translation),
  // else our short i18n text.
  const official = shop.types && shop.types[pub] && shop.types[pub][type];
  // The card lists every term, so a description written for one term ("Un año de acceso…",
  // "One year of online access…") loses that lead-in. The print text is always ours: the store's
  // ("seis (6) ejemplares…") counts one year's copies and would read as the total on a 1–3 year card.
  const officialText = official && lang === STORE_LANG[pub] && type !== "print"
    ? String(official[lang] || "").replace(/^(un|1)\s+años?\s+de\s+|^(one|1)\s+years?\s+of\s+/i, "").replace(/^./, (c) => c.toUpperCase())
    : "";
  const fallbackKey = type === "print" ? "shop.desc_print_" + pub : type === "other" ? "" : "shop.desc_" + type;
  return {
    type,
    name: type === "other" ? (plans[0].title || "") : t("shop.type_" + type, lang),
    desc: officialText || (fallbackKey ? t(fallbackKey, lang) : ""),
    descLang: officialText ? lang : "",
    terms,
    volume,
    fromPerMonth: withMonths.length ? money(round2(Math.min(...withMonths.map((p) => Number(p.price) / p.term_months))), lang, cur) : "",
  };
}

export function shopSubs(shop, lang = "en", t = (k) => k) {
  const subs = shop && Array.isArray(shop.subscriptions) ? shop.subscriptions : [];
  const pubs = [];
  for (const pub of pubOrder(lang)) {
    const regions = [];
    for (const region of REGION_ORDER) {
      const entry = subs.find((s) => s && s.pub === pub && s.region === region);
      const plans = entry && Array.isArray(entry.plans) ? entry.plans.filter((p) => p && Number(p.price) > 0 && p.url) : [];
      if (!plans.length) continue;
      const types = [];
      for (const type of TYPE_ORDER) {
        const tp = plans.filter((p) => (TYPE_ORDER.includes(p.type) ? p.type : "other") === type);
        if (tp.length) types.push(typeView(pub, type, tp, shop, lang, t));
      }
      regions.push({ region, url: entry.url || "", types });
    }
    if (regions.length) pubs.push({ pub, isLv: pub === "lv", mag: MAG[pub], regions });
  }
  // Every region that any publication offers, in the fixed order (a region missing for the selected
  // publication is hidden by the page's switch).
  const regionList = REGION_ORDER.filter((r) => pubs.some((p) => p.regions.some((x) => x.region === r)));
  // Client-side switch config: which regions each publication offers, and the default (U.S. when offered).
  const avail = {};
  for (const p of pubs) avail[p.pub] = p.regions.map((r) => r.region);
  for (const p of pubs) p.defaultRegion = avail[p.pub].includes("us") ? "us" : avail[p.pub][0];
  return { pubs, regions: regionList, first: pubs.length ? pubs[0].pub : "", avail };
}

export function shopFromMonthly(shop) {
  const subs = shop && Array.isArray(shop.subscriptions) ? shop.subscriptions : [];
  const plans = subs.flatMap((s) => (s && Array.isArray(s.plans) ? s.plans : [])).filter((p) => p && Number(p.price) > 0);
  const monthly = plans.filter((p) => p.term_months === 1).map((p) => Number(p.price));
  if (monthly.length) return Math.min(...monthly);
  const rates = plans.filter((p) => p.term_months).map((p) => Number(p.price) / p.term_months);
  return rates.length ? round2(Math.min(...rates)) : null;
}

/* ---------------- Bulk-book discounts ---------------- */
export function shopBulk(shop, lang = "en", t = (k) => k) {
  const bd = shop && shop.bulk_discounts;
  const tiers = bd && Array.isArray(bd.tiers) ? bd.tiers : [];
  const rows = tiers
    .filter((x) => x && Number(x.off) > 0)
    .sort((a, b) => a.min - b.min)
    .map((x) => ({
      label: x.max ? t("shop.books_range", lang, { a: x.min, b: x.max }) : t("shop.books_plus", lang, { a: x.min }),
      off: money(x.off, lang),
    }));
  const note = bd && bd.note ? bd.note[lang] || "" : "";
  return { rows, note, source: (bd && bd.source_url) || "" };
}

export default function (eleventyConfig, helpers) {
  const t = helpers.translateKey;
  eleventyConfig.addFilter("shopBotm", (shop, lang) => shopBotm(shop, lang));
  eleventyConfig.addFilter("shopSubs", (shop, lang) => shopSubs(shop, lang, t));
  eleventyConfig.addFilter("shopBulk", (shop, lang) => shopBulk(shop, lang, t));
  eleventyConfig.addFilter("shopFromMonthly", (shop) => shopFromMonthly(shop));
  eleventyConfig.addFilter("shopMoney", (n, lang) => money(n, lang));
  eleventyConfig.addFilter("shopToday", () => todayYmd());
}
