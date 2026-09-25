/* NETA 65 Grapevine / La Viña — service worker (offline use, installable app).
   Published as <site>/sw.js by src/pages/sw.11ty.js, which puts `const CONFIG = {…}` (this build's
   version, the site's base path, the app shell, the offline pages, the pages to save) above this file.
   Scope: the site's base path (/AAGrapevine/ on GitHub Pages). README → "Install the app, offline use".

   What it does with each request — only GET requests to OUR site; anything else is not touched:
   * never: other sites (YouTube, podcast audio, aagrapevine.org, aalavina.org, Drive…), POST, audio
     or video byte ranges, feeds and calendar files (.xml, .ics), the worker and the manifest.
   * pages (navigations): NETWORK FIRST, revalidated with the site (cache: "no-cache": not even the
     browser's HTTP cache can hand back an old page). Online, you always get the page from the site;
     the copy is kept (the last 80 pages, plus the ones saved with "Save key pages for offline"). The
     saved copy is used only when the network fails, answers with a server error, or takes more than 4 s —
     then the page is told (pwa.js shows "Slow connection — this is a saved copy"). A page that is not
     saved → the offline page, in the language of the address. GitHub Pages' "page not found" (404)
     is passed through and never kept.
   * CSS / JS / fonts / images of the site: STALE-WHILE-REVALIDATE from versioned caches (answer from
     the cache, refresh it in the background at most every 6 hours). Files with ?v=<version> (the
     CSS and JS links of base.njk) and fonts never change under the same address: cache first.
     Capped: 120 static files, 200 images (the oldest go first).
   * JSON indexes (search, media, library): NETWORK FIRST, the saved copy when offline or after 6 s.
   Caches: gvlv-shell-<version> (app shell) and gvlv-static-<version> are replaced by each new
   version; gvlv-pages-v1, gvlv-saved-v1, gvlv-img-v1 and gvlv-data-v1 are kept across versions, so a
   site update never deletes what a visitor saved. activate deletes every other gvlv-* cache.
   Updates: a new version installs in the background and WAITS; pwa.js shows "Updated — reload" and
   sends SKIP_WAITING when the visitor chooses it (otherwise it takes over once every tab is closed). */
"use strict";

const V = CONFIG.version;
const BASE = CONFIG.base;
const PREFIX = "gvlv-";
const CACHE = {
  shell: PREFIX + "shell-" + V,
  static: PREFIX + "static-" + V,
  pages: PREFIX + "pages-v1",
  saved: PREFIX + "saved-v1",
  img: PREFIX + "img-v1",
  data: PREFIX + "data-v1",
};
const LIMIT = { static: 120, pages: 80, img: 200, data: 24 };
const NAV_TIMEOUT = 4000;
const DATA_TIMEOUT = 6000;
const REVALIDATE_AFTER = 6 * 3600e3;

/* ------------------------------------------------------------------ install / activate */
self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE.shell);
    await Promise.all(CONFIG.shell.map(async (u) => {
      const required = CONFIG.required.includes(u);
      try {
        // Versioned files (?v=) and fonts are the same bytes the page just loaded: the browser's HTTP
        // cache may answer (no second download on a first visit). Everything else is checked with the
        // site ("no-cache": a tiny "not modified" when unchanged), so the shell is this deploy's.
        const same = /[?&]v=|\/assets\/fonts\//.test(u);
        const res = await fetch(new Request(u, { cache: same ? "default" : "no-cache", credentials: "same-origin" }));
        if (res.ok) await cache.put(u, CONFIG.offline.en === u || CONFIG.offline.es === u ? await stamp(res) : res);
        else if (required) throw new Error(u + " → HTTP " + res.status);
      } catch (e) {
        if (required) throw e; // the install fails and the browser tries again on the next visit
      }
    }));
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keep = new Set(Object.values(CACHE));
    for (const name of await caches.keys()) if (name.startsWith(PREFIX) && !keep.has(name)) await caches.delete(name);
    // No navigation preload: its request would use the browser's HTTP cache (up to 10 minutes old on
    // GitHub Pages); page() asks the site itself instead.
    if (self.registration.navigationPreload) { try { await self.registration.navigationPreload.disable(); } catch (e) { /* not supported */ } }
    await self.clients.claim(); // the first visit is looked after at once (offline works after one visit)
    // The page(s) open while the worker starts (a first visit) are kept too — usually straight from
    // the browser's HTTP cache, so this costs no extra data.
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    await Promise.all(wins.map(async (c) => {
      const key = pageKey(c.url);
      try { const res = await fetch(c.url, { credentials: "same-origin" }); if (keepable(res, key)) await keepPage(key, res); } catch (e) { /* offline */ }
    }));
  })());
});

/* ------------------------------------------------------------------ routing */
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith(BASE)) return; // never other sites
  if (req.headers.has("range")) return;
  if (req.mode === "navigate") { event.respondWith(page(event, url)); return; }
  const p = url.pathname;
  if (/\/sw\.js$|\.(webmanifest|xml|ics|txt)$/.test(p)) return;
  if (/\.json$/.test(p)) { event.respondWith(networkFirst(event)); return; }
  if (/\.(css|js|mjs|woff2?)$/.test(p)) { event.respondWith(staleWhileRevalidate(event, url, "static")); return; }
  if (/\.(png|jpe?g|webp|gif|svg|ico|avif)$/.test(p)) { event.respondWith(staleWhileRevalidate(event, url, "img")); return; }
  // anything else (documents, downloads): straight to the network, as without a worker
});

/* ------------------------------------------------------------------ pages */
const served = new Map(); // client id → when the saved copy we answered with was saved (pwa.js asks)

function pageKey(url) {
  const u = new URL(url);
  return u.origin + u.pathname.replace(/index\.html$/, "");
}
function isHtml(res) { return /text\/html/i.test(res.headers.get("content-type") || ""); }
function keepable(res, key) {
  return res && res.status === 200 && res.type === "basic" && isHtml(res) && !/\/404\.html$/.test(key) &&
    key !== self.location.origin + CONFIG.offline.en && key !== self.location.origin + CONFIG.offline.es;
}
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", "#x27": "'", nbsp: " " };
function decode(s) { return s.replace(/&(#39|#x27|amp|lt|gt|quot|nbsp);/g, (m, k) => ENT[k] || m); }

/* The copy we keep: the page's HTML + when it was saved + its title (the offline page lists both
   without reading every page again). Only safe headers are carried over. */
async function stamp(res) {
  const text = await res.text();
  const m = /<title>([^<]*)<\/title>/i.exec(text.slice(0, 12000));
  const h = new Headers({ "content-type": res.headers.get("content-type") || "text/html; charset=utf-8", "x-gvlv-saved": new Date().toISOString() });
  if (m) h.set("x-gvlv-title", encodeURIComponent(decode(m[1]).trim()));
  return new Response(text, { status: 200, statusText: "OK", headers: h });
}

async function keepPage(key, res) {
  const copy = await stamp(res);
  const pages = await caches.open(CACHE.pages);
  await pages.delete(key); // re-added at the end: the list stays in "last opened" order
  await pages.put(key, copy.clone());
  const saved = await caches.open(CACHE.saved);
  if (await saved.match(key)) await saved.put(key, copy); // a saved page stays fresh too
  await trim("pages");
}

async function savedCopy(key) {
  return (await (await caches.open(CACHE.saved)).match(key)) || (await (await caches.open(CACHE.pages)).match(key)) || null;
}

function answeredFromCopy(event, res) {
  const id = event.resultingClientId || event.clientId;
  if (id) {
    served.set(id, res.headers.get("x-gvlv-saved") || "");
    if (served.size > 30) served.delete(served.keys().next().value);
  }
  return res;
}

async function offlinePage(url) {
  const es = url.pathname.startsWith(BASE + "es/");
  const res = (await caches.match(es ? CONFIG.offline.es : CONFIG.offline.en)) || (await caches.match(CONFIG.offline.en));
  if (res) return res;
  const msg = es ? "Estás sin conexión. Intenta de nuevo cuando vuelvas a tener señal." : "You're offline. Try again when you have a signal.";
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline</title><p style="font:1.1rem/1.5 system-ui,sans-serif;margin:2rem">${msg}</p>`,
    { status: 503, headers: { "content-type": "text/html; charset=utf-8" } });
}

async function page(event, url) {
  const key = pageKey(url.href);
  const network = (async () => {
    // cache: "no-cache" — always ask the site (a changed page comes back in full, an unchanged one
    // as a tiny "not modified"), so a page shown online is never an old copy from the HTTP cache.
    const res = await fetch(new Request(event.request, { cache: "no-cache" }));
    if (keepable(res, key)) event.waitUntil(keepPage(key, res.clone()).catch(() => {}));
    return res;
  })();
  let timer = 0;
  const slow = new Promise((resolve) => { timer = setTimeout(resolve, NAV_TIMEOUT, null); });
  try {
    const res = await Promise.race([network, slow]);
    if (res) {
      clearTimeout(timer);
      if (res.status >= 500) { const copy = await savedCopy(key); if (copy) return answeredFromCopy(event, copy); }
      return res; // includes GitHub Pages' 404 page for a missing address
    }
    // No answer after 4 s (a weak signal): the saved copy if there is one; the page keeps loading
    // in the background and replaces the copy, so "Try again" gets the new one.
    const copy = await savedCopy(key);
    if (copy) { event.waitUntil(network.catch(() => {})); return answeredFromCopy(event, copy); }
    return await network;
  } catch (err) {
    clearTimeout(timer);
    const copy = await savedCopy(key);
    return copy ? answeredFromCopy(event, copy) : offlinePage(url);
  }
}

/* ------------------------------------------------------------------ static files */
function immutable(url) { return url.searchParams.has("v") || /\/assets\/fonts\//.test(url.pathname); }

async function staleWhileRevalidate(event, url, which) {
  const req = event.request;
  const hit = await caches.match(req);
  const refresh = () => fetch(req).then(async (res) => {
    if (res.ok && res.status === 200 && res.type === "basic") {
      const copy = res.clone();
      event.waitUntil((async () => { const c = await caches.open(CACHE[which]); await c.put(req, copy); await trim(which); })().catch(() => {}));
    }
    return res;
  });
  if (hit) {
    const age = Date.now() - (Date.parse(hit.headers.get("date") || "") || 0);
    if (!immutable(url) && age > REVALIDATE_AFTER) event.waitUntil(refresh().catch(() => {}));
    return hit;
  }
  try {
    return await refresh();
  } catch (err) {
    // Offline and not kept: any version of the same file (a saved page can ask for an older ?v=).
    const any = await caches.match(req, { ignoreSearch: true });
    if (any) return any;
    throw err;
  }
}

/* ------------------------------------------------------------------ JSON indexes */
async function networkFirst(event) {
  const req = event.request;
  const cache = await caches.open(CACHE.data);
  const hit = await cache.match(req, { ignoreSearch: true });
  const net = fetch(req).then((res) => {
    if (res.ok && res.status === 200 && res.type === "basic") {
      const copy = res.clone();
      event.waitUntil(cache.put(new URL(req.url).origin + new URL(req.url).pathname, copy).then(() => trim("data")).catch(() => {}));
    }
    return res;
  });
  if (!hit) return net;
  let timer = 0;
  const slow = new Promise((resolve) => { timer = setTimeout(resolve, DATA_TIMEOUT, null); });
  try {
    const res = await Promise.race([net, slow]);
    clearTimeout(timer);
    if (res && res.status < 500) return res;
    event.waitUntil(net.catch(() => {}));
    return hit;
  } catch (err) {
    clearTimeout(timer);
    return hit;
  }
}

/* ------------------------------------------------------------------ caps (oldest first) */
const trimming = {};
function trim(which) {
  const max = LIMIT[which];
  if (!max) return Promise.resolve();
  trimming[which] = (trimming[which] || Promise.resolve()).then(async () => {
    const c = await caches.open(CACHE[which]);
    const keys = await c.keys();
    for (let i = 0; i < keys.length - max; i++) await c.delete(keys[i]);
  }).catch(() => {});
  return trimming[which];
}

/* ------------------------------------------------------------------ "Save key pages for offline" */
function chicagoMonth() {
  try {
    const p = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", year: "numeric", month: "2-digit" }).formatToParts(new Date());
    return p.find((x) => x.type === "year").value + "-" + p.find((x) => x.type === "month").value;
  } catch (e) {
    return new Date().toISOString().slice(0, 7);
  }
}

function saveList(lang) {
  const pre = lang === "es" ? BASE + "es/" : BASE;
  return CONFIG.save.map((path) => {
    // "monthly/{month}/" = this month's toolkit page (the hub if that page isn't there)
    if (path.includes("{month}")) return [pre + path.replace("{month}", chicagoMonth()), pre + path.replace("{month}/", "")];
    return [pre + path];
  });
}

const ASSET_RE = /<(?:script|link)\b[^>]*?\b(?:src|href)="([^"]+)"[^>]*>/gi;
async function keepAssets(html, pageUrl) {
  const urls = new Set();
  let m;
  ASSET_RE.lastIndex = 0;
  while ((m = ASSET_RE.exec(html))) {
    const tag = m[0];
    if (/^<link/i.test(tag) && !/rel="(?:stylesheet|preload)"/i.test(tag)) continue;
    try {
      const u = new URL(m[1].replace(/&amp;/g, "&"), pageUrl);
      if (u.origin === self.location.origin && u.pathname.startsWith(BASE) && /\.(css|js|woff2)$/.test(u.pathname)) urls.add(u.href);
    } catch (e) { /* not a URL */ }
  }
  const cache = await caches.open(CACHE.static);
  await Promise.all([...urls].map(async (u) => {
    if (await caches.match(u)) return;
    try { const res = await fetch(u, { credentials: "same-origin" }); if (res.ok && res.type === "basic") await cache.put(u, res); } catch (e) { /* offline again */ }
  }));
}

async function savePages(lang, reply) {
  const list = saveList(lang);
  const saved = await caches.open(CACHE.saved);
  let done = 0, ok = 0;
  const failed = [];
  reply({ type: "SAVE_PROGRESS", done, total: list.length });
  for (const tries of list) {
    let good = false;
    for (const u of tries) {
      try {
        const res = await fetch(u, { credentials: "same-origin", cache: "no-cache" });
        if (!res.ok || res.type !== "basic" || !isHtml(res)) continue;
        const key = pageKey(res.url || u);
        const copy = await stamp(res);
        await keepAssets(await copy.clone().text(), key);
        await saved.put(key, copy);
        good = true;
        break;
      } catch (e) { /* try the next address, or give up on this page */ }
    }
    done += 1;
    if (good) ok += 1; else failed.push(tries[0]);
    reply({ type: "SAVE_PROGRESS", done, total: list.length });
  }
  reply({ type: "SAVE_DONE", saved: ok, total: list.length, failed });
}

/* ------------------------------------------------------------------ messages from pwa.js */
self.addEventListener("message", (event) => {
  const d = event.data || {};
  const port = event.ports && event.ports[0];
  const reply = (msg) => { try { if (port) port.postMessage(msg); else if (event.source) event.source.postMessage(msg); } catch (e) { /* page gone */ } };
  if (d.type === "SKIP_WAITING") self.skipWaiting();
  else if (d.type === "HOW_SERVED") {
    const id = event.source && event.source.id;
    const s = id && served.has(id) ? served.get(id) : null;
    if (id) served.delete(id);
    reply({ type: "HOW_SERVED", copy: s !== null, savedAt: s || null, version: V });
  } else if (d.type === "SAVE") event.waitUntil(savePages(d.lang === "es" ? "es" : "en", reply));
  else if (d.type === "VERSION") reply({ type: "VERSION", version: V });
});
