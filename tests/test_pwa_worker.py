"""The service worker's BEHAVIOUR ("works with a weak signal"): /sw.js is built from src/pages/sw.11ty.js
+ src/_includes/pwa/sw-core.js and run in Node.js against a pretend network and pretend caches.

  * install    — the app shell and both offline pages are kept; a missing required file (the CSS, the
                 scripts, an offline page) makes the install fail so the browser tries again; a missing
                 optional file does not.
  * activate   — old versions' gvlv-* caches go; visitors' pages, saved pages and other sites' caches stay;
                 the page open during a first visit is kept WITH its own styles and scripts.
  * pages      — online: the page from the site (kept for later); offline, a server error or no answer
                 in time: the kept copy (and pwa.js is told it is a copy); no copy: the offline page in
                 the address's language; GitHub Pages' 404 passes through and is never kept.
  * files      — the site's own GET requests only (other sites, POST, byte ranges, the worker and the
                 manifest are never touched); styles/images/JSON work offline once seen.
  * Save       — "Save key pages for offline" keeps the visitor's pages in their language, this month's
                 toolkit page when there is one, and reports progress and what failed.

Skipped without Node.js (tests/nodejs.py). The page-level checks (the panel, the banner) are in the
browser QA scripts; the static contract checks are in tests/test_pwa.py.

    python -m unittest tests.test_pwa_worker -v        (or: python -m unittest discover -s tests)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodejs import run_js  # noqa: E402

WORLD_JS = r"""
import vm from "node:vm";
const SW = await imp("src/pages/sw.11ty.js");
const ORIGIN = "https://example.test";
const B = "/AAGrapevine/";
const code = SW.render({
  build: { version: "t1" },
  collections: { all: ["/", "/meetings/", "/monthly/", "/contribute/", "/shop/", "/accessibility/", "/orientation/",
                       "/orientation/magazines/"].map((url) => ({ url })) },
  orientation: { lessons: [{ id: "magazines" }] },
});

const html = (title, extra = "") => `<!doctype html><html><head><title>${title}</title>` +
  `<link rel="stylesheet" href="${B}assets/css/main.css?v=t1"><script src="${B}assets/js/app.js?v=t1"></script></head><body>${extra}</body></html>`;

function world() {
  const store = new Map();
  const net = new Map();          // absolute URL → { status, body, ct } | "offline" | "hang"
  const fetched = [];
  let fast = false;
  const handlers = {};
  const make = (url, status, body, headers) => {
    const res = new Response(body, { status, headers });
    Object.defineProperty(res, "type", { value: "basic" });
    Object.defineProperty(res, "url", { value: url });
    return res;
  };
  class FakeCache {
    constructor() { this.m = new Map(); }
    key(r) { return typeof r === "string" ? new URL(r, ORIGIN).href : r.url; }
    async put(r, res) { const k = this.key(r); const body = await res.arrayBuffer(); this.m.delete(k); this.m.set(k, { body, status: res.status, headers: [...res.headers] }); }
    async match(r, o = {}) {
      const k = this.key(r);
      if (this.m.has(k)) { const e = this.m.get(k); return make(k, e.status, e.body, e.headers); }
      if (o.ignoreSearch) for (const [kk, e] of this.m) if (kk.split("?")[0] === k.split("?")[0]) return make(kk, e.status, e.body, e.headers);
      return undefined;
    }
    async delete(r) { return this.m.delete(this.key(r)); }
    async keys() { return [...this.m.keys()].map((u) => new Request(u)); }
  }
  const caches = {
    async open(n) { if (!store.has(n)) store.set(n, new FakeCache()); return store.get(n); },
    async keys() { return [...store.keys()]; },
    async delete(n) { return store.delete(n); },
    async has(n) { return store.has(n); },
    async match(r, o) { for (const c of store.values()) { const hit = await c.match(r, o); if (hit) return hit; } return undefined; },
  };
  async function fetch(input) {
    const url = new URL(typeof input === "string" ? input : input.url, ORIGIN).href;
    fetched.push(url);
    const spec = net.has(url) ? net.get(url) : "offline";
    if (spec === "offline") throw new TypeError("Failed to fetch");
    if (spec === "hang") return new Promise(() => {});
    return make(url, spec.status || 200, spec.body ?? "", { "content-type": spec.ct || "text/html; charset=utf-8", date: new Date().toUTCString() });
  }
  let skipped = false;
  const wins = [];                // the tabs open while the worker starts (a first visit)
  const self = {
    addEventListener: (t, fn) => { handlers[t] = fn; },
    location: { origin: ORIGIN },
    registration: {},
    clients: { claim: async () => {}, matchAll: async () => wins.slice() },
    skipWaiting: () => { skipped = true; },
  };
  // In a worker a relative address is read against the worker's own; Node's Request needs it whole.
  class WorkerRequest extends Request {
    constructor(input, init) { super(typeof input === "string" ? new URL(input, ORIGIN).href : input, init); }
  }
  const ctx = vm.createContext({
    self, caches, fetch, Request: WorkerRequest, Response, Headers, URL, console,
    setTimeout: (fn, ms, ...a) => (fast ? (queueMicrotask(() => fn(...a)), 0) : setTimeout(fn, ms, ...a)),
    clearTimeout: (t) => { if (t) clearTimeout(t); },
  });
  vm.runInContext(code, ctx);
  const CONFIG = vm.runInContext("CONFIG", ctx);
  const until = async (waits) => { let n = -1; while (n !== waits.length) { n = waits.length; await Promise.allSettled(waits.slice()); } };

  return {
    CONFIG, store, net, fetched, handlers, wins,
    set fast(v) { fast = v; },
    get skipped() { return skipped; },
    page: (path, spec) => net.set(ORIGIN + path, spec),
    async lifecycle(type) {
      const waits = [];
      handlers[type]({ waitUntil: (p) => waits.push(p) });
      await until(waits);
      return (await Promise.allSettled(waits)).map((x) => x.status);
    },
    async request(path, { navigate = false, method = "GET", headers = {}, settle = true } = {}) {
      const req = new Request(new URL(path, ORIGIN).href, { method, headers });
      if (navigate) Object.defineProperty(req, "mode", { value: "navigate" });
      const waits = [];
      let answer = null;
      handlers.fetch({ request: req, clientId: "", resultingClientId: "tab-1", respondWith: (p) => { answer = p; }, waitUntil: (p) => waits.push(p) });
      if (!answer) return null;
      let res;
      try { res = await answer; } catch (e) { return { error: String(e && e.message || e) }; }
      if (settle) await until(waits);
      return { status: res.status, body: await res.text(), saved: res.headers.get("x-gvlv-saved"), title: res.headers.get("x-gvlv-title") };
    },
    async message(data) {
      const replies = [];
      const waits = [];
      handlers.message({ data, ports: [{ postMessage: (m) => replies.push(m) }], source: { id: "tab-1", postMessage: (m) => replies.push(m) },
                         waitUntil: (p) => waits.push(p) });
      await until(waits);
      return replies;
    },
    async keys(name) { return store.has(name) ? [...store.get(name).m.keys()] : null; },
  };
}

function shellOnline(w) {
  for (const u of w.CONFIG.shell) {
    const offline = u === w.CONFIG.offline.en || u === w.CONFIG.offline.es;
    w.net.set(ORIGIN + u, offline ? { body: html(u.includes("/es/") ? "Sin conexión" : "Offline", u.includes("/es/") ? "OFFLINE-ES" : "OFFLINE-EN") }
                                  : { body: "/* " + u + " */", ct: /\.css/.test(u) ? "text/css" : /\.js/.test(u) ? "text/javascript" : "application/octet-stream" });
  }
}
const R = {};

/* ---------------- install / activate ---------------- */
{
  const w = world();
  shellOnline(w);
  w.net.set(ORIGIN + B + "assets/fonts/fraunces-latin-opsz-normal.woff2", { status: 404, body: "" });   // an optional file is missing
  R.config = { base: w.CONFIG.base, save: w.CONFIG.save, required: w.CONFIG.required, offline: w.CONFIG.offline };
  R.install = await w.lifecycle("install");
  R.shell = await w.keys("gvlv-shell-t1");
  const off = await w.store.get("gvlv-shell-t1").match(ORIGIN + w.CONFIG.offline.es);
  R.offlineStamped = !!(off && off.headers.get("x-gvlv-saved"));
  R.skippedOnInstall = w.skipped;

  const bad = world();
  shellOnline(bad);
  bad.net.set(ORIGIN + bad.CONFIG.required[0], { status: 404, body: "" });                              // the CSS is missing
  R.installBad = await bad.lifecycle("install");

  for (const n of ["gvlv-shell-t0", "gvlv-static-t0", "gvlv-pages-v1", "gvlv-saved-v1", "gvlv-img-v1", "someone-else"]) await w.store.set(n, new (w.store.get("gvlv-shell-t1").constructor)());
  R.activate = await w.lifecycle("activate");
  R.cachesAfter = [...w.store.keys()].sort();

  /* ---------------- pages ---------------- */
  w.page(B + "meetings/", { body: html("Meetings &amp; more", "ONLINE") });
  R.online = await w.request(B + "meetings/", { navigate: true });
  R.kept = await w.keys("gvlv-pages-v1");
  const copy = await w.store.get("gvlv-pages-v1").match(ORIGIN + B + "meetings/");
  R.keptTitle = copy && decodeURIComponent(copy.headers.get("x-gvlv-title") || "");
  R.howServedOnline = (await w.message({ type: "HOW_SERVED" }))[0];

  w.net.delete(ORIGIN + B + "meetings/");                                                                // the signal is gone
  R.offlineCopy = await w.request(B + "meetings/", { navigate: true });
  R.howServedCopy = (await w.message({ type: "HOW_SERVED" }))[0];
  R.offlineEs = await w.request(B + "es/shop/", { navigate: true });
  R.offlineEn = await w.request(B + "shop/", { navigate: true });

  w.page(B + "meetings/", { status: 503, body: "Server trouble" });
  R.serverError = await w.request(B + "meetings/", { navigate: true });
  w.page(B + "nope/", { status: 404, body: html("Page not found", "404") });
  R.notFound = await w.request(B + "nope/", { navigate: true });
  R.keptAfter404 = await w.keys("gvlv-pages-v1");

  w.page(B + "meetings/", "hang");                                                                        // a weak signal: no answer in time
  w.fast = true;
  R.slow = await w.request(B + "meetings/", { navigate: true, settle: false });
  w.fast = false;

  /* ---------------- other requests ---------------- */
  R.untouched = {
    otherSite: await w.request("https://www.aagrapevine.org/store", { navigate: true }),
    otherProject: await w.request("/another-project/", { navigate: true }),
    post: await w.request(B + "search.json", { method: "POST" }),
    range: await w.request(B + "assets/audio/x.mp3", { headers: { range: "bytes=0-" } }),
    worker: await w.request(B + "sw.js"),
    manifest: await w.request(B + "manifest.webmanifest"),
    feed: await w.request(B + "feed.xml"),
  };
  R.cssOffline = await w.request(w.CONFIG.required[0]);                                                  // from the app shell
  w.page(B + "assets/img/cover.webp", { body: "IMG", ct: "image/webp" });
  R.imgOnline = await w.request(B + "assets/img/cover.webp");
  w.net.delete(ORIGIN + B + "assets/img/cover.webp");
  R.imgOffline = await w.request(B + "assets/img/cover.webp");
  R.imgNever = await w.request(B + "assets/img/never-seen.webp");
  w.page(B + "search-index.json?x=1", { body: '{"v":1}', ct: "application/json" });
  R.jsonOnline = await w.request(B + "search-index.json?x=1");
  w.net.delete(ORIGIN + B + "search-index.json?x=1");
  R.jsonOffline = await w.request(B + "search-index.json?x=2");

  /* ---------------- a first visit: the open page is kept with its own files; then the signal goes ---------------- */
  const f = world();
  shellOnline(f);
  await f.lifecycle("install");
  const own = [B + "assets/js/home.js?v=t1", B + "assets/vendor/lite-yt-embed.css?v=t1"];
  f.page(B, { body: html("Home", `<script src="${own[0]}" defer></script><link rel="stylesheet" href="${own[1]}">FIRST`) });
  f.page(own[0], { body: "/* home */", ct: "text/javascript" });
  f.page(own[1], { body: "/* lyt */", ct: "text/css" });
  f.wins.push({ url: ORIGIN + B });
  R.firstActivate = await f.lifecycle("activate");
  for (const u of [B, ...own]) f.net.delete(ORIGIN + u);
  R.firstVisit = { page: await f.request(B, { navigate: true }), js: await f.request(own[0]), css: await f.request(own[1]) };

  /* ---------------- "Save key pages for offline" (Spanish) ---------------- */
  const s = world();
  shellOnline(s);
  await s.lifecycle("install");
  const pages = s.CONFIG.save.filter((p) => !p.includes("{month}"));
  for (const p of pages) s.page(B + "es/" + p, { body: html("ES " + (p || "inicio")) });
  s.page(B + "es/contribute/", "offline");                                                               // one page can't be reached
  const before = s.fetched.length;
  R.saveReplies = await s.message({ type: "SAVE", lang: "es" });
  R.saved = (await s.keys("gvlv-saved-v1")) || [];
  R.savePages = pages;
  R.saveFetchedEnglish = s.fetched.slice(before).some((u) => u.startsWith(ORIGIN + B) && !u.startsWith(ORIGIN + B + "es/") && !u.includes("/assets/"));
}
out(R);
"""

ORIGIN = "https://example.test"
B = "/AAGrapevine/"


class Worker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = None

    def setUp(self):
        if Worker.r is None:
            Worker.r = run_js(self, WORLD_JS, needs_modules=False, env={"PATH_PREFIX": B}, timeout=120)
        self.r = Worker.r

    def test_the_build_settings(self):
        c = self.r["config"]
        self.assertEqual(c["base"], B)
        self.assertEqual(c["offline"], {"en": B + "offline/", "es": B + "es/offline/"})
        for p in ("", "meetings/", "monthly/", "monthly/{month}/", "contribute/", "shop/", "accessibility/", "orientation/",
                  "orientation/magazines/"):
            self.assertIn(p, c["save"])
        self.assertEqual(len(c["save"]), len(set(c["save"])))
        self.assertTrue(all(u.startswith(B) for u in c["required"]))

    def test_install(self):
        self.assertEqual(self.r["install"], ["fulfilled"])                   # an optional font missing is fine
        for u in self.r["config"]["required"]:
            self.assertIn(ORIGIN + u, self.r["shell"])
        self.assertTrue(self.r["offlineStamped"])
        self.assertFalse(self.r["skippedOnInstall"])                         # a new version waits for the visitor
        self.assertEqual(self.r["installBad"], ["rejected"])                 # the CSS missing: try again later

    def test_activate_keeps_what_visitors_saved(self):
        self.assertEqual(self.r["activate"], ["fulfilled"])
        after = self.r["cachesAfter"]
        for gone in ("gvlv-shell-t0", "gvlv-static-t0"):
            self.assertNotIn(gone, after)
        for kept in ("gvlv-shell-t1", "gvlv-pages-v1", "gvlv-saved-v1", "gvlv-img-v1", "someone-else"):
            self.assertIn(kept, after)

    def test_a_first_visit_works_offline(self):
        # the page open while the worker starts is kept, and so are the scripts and styles it asked for
        # (they loaded before the worker was in charge): offline, it opens whole, with its own scripts
        r = self.r
        self.assertEqual(r["firstActivate"], ["fulfilled"])
        self.assertIn("FIRST", r["firstVisit"]["page"]["body"])
        self.assertEqual(r["firstVisit"]["js"]["body"], "/* home */")
        self.assertEqual(r["firstVisit"]["css"]["body"], "/* lyt */")

    def test_pages_online_offline_and_slow(self):
        r = self.r
        self.assertEqual((r["online"]["status"], r["online"]["saved"]), (200, None))
        self.assertIn("ONLINE", r["online"]["body"])
        self.assertIn(ORIGIN + B + "meetings/", r["kept"])
        self.assertEqual(r["keptTitle"], "Meetings & more")
        self.assertFalse(r["howServedOnline"]["copy"])
        # offline: the kept copy, and the page is told so
        self.assertIn("ONLINE", r["offlineCopy"]["body"])
        self.assertTrue(r["offlineCopy"]["saved"])
        self.assertTrue(r["howServedCopy"]["copy"])
        self.assertTrue(r["howServedCopy"]["savedAt"])
        # never seen: the offline page in the address's language
        self.assertIn("OFFLINE-ES", r["offlineEs"]["body"])
        self.assertIn("OFFLINE-EN", r["offlineEn"]["body"])
        # a server error → the copy; GitHub Pages' 404 → as it is, never kept
        self.assertIn("ONLINE", r["serverError"]["body"])
        self.assertEqual(r["notFound"]["status"], 404)
        self.assertNotIn(ORIGIN + B + "nope/", r["keptAfter404"])
        # no answer in time → the copy
        self.assertIn("ONLINE", r["slow"]["body"])

    def test_only_the_sites_own_get_requests(self):
        for name, res in self.r["untouched"].items():
            with self.subTest(request=name):
                self.assertIsNone(res)

    def test_files_work_offline_once_seen(self):
        r = self.r
        self.assertEqual(r["cssOffline"]["status"], 200)
        self.assertEqual(r["imgOnline"]["body"], "IMG")
        self.assertEqual(r["imgOffline"]["body"], "IMG")
        self.assertIn("error", r["imgNever"])                                 # offline and never seen: a network error
        self.assertEqual(r["jsonOnline"]["body"], '{"v":1}')
        self.assertEqual(r["jsonOffline"]["body"], '{"v":1}')                 # any ?query: the same index

    def test_save_key_pages(self):
        r = self.r
        replies = r["saveReplies"]
        self.assertEqual(replies[0]["type"], "SAVE_PROGRESS")
        done = replies[-1]
        self.assertEqual(done["type"], "SAVE_DONE")
        self.assertEqual(done["failed"], [B + "es/contribute/"])            # the page that could not be reached
        self.assertEqual(done["saved"], len(r["savePages"]) - 1)
        self.assertEqual(done["total"], len(r["savePages"]))                 # no page for this month yet: not counted
        for p in r["savePages"]:
            if p != "contribute/":
                self.assertIn(ORIGIN + B + "es/" + p, r["saved"])
        self.assertFalse(r["saveFetchedEnglish"])                            # only the visitor's language


if __name__ == "__main__":
    unittest.main()
