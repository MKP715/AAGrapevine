/* Area script — Read / Share Your Story / Subscribe / GVR-RLV Corner / About.
   Loaded (defer) after app.js and before Alpine, so components registered on
   "alpine:init" are ready when Alpine starts. No build step, no dependencies.
   All user-visible strings come from the page (data-* attributes rendered from
   src/_i18n/read.json), never from this file. */
(function () {
  "use strict";

  var TZ = (window.SITE && window.SITE.tz) || "America/Chicago";
  function store(key, val) {
    try {
      if (val === undefined) return JSON.parse(localStorage.getItem(key) || "null");
      localStorage.setItem(key, JSON.stringify(val));
    } catch (e) { return null; }
  }
  function fill(tpl, n) { return String(tpl || "").replace("{n}", n); }

  /* ------------------------------------------------------------------
     Deadlines: <span data-deadline="2026-10-15" data-t-days="{n} days left"
       data-t-today="…" data-t-tomorrow="…" data-t-past="…">
     The site is rebuilt daily, but a visitor may open a cached page — so the
     "days left" text is recomputed in the visitor's browser (Chicago dates). */
  function todayChicago() {
    try {
      var ymd = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
      return new Date(ymd + "T12:00:00Z").getTime();
    } catch (e) { var d = new Date(); return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), 12); }
  }
  function updateDeadlines() {
    var today = todayChicago();
    document.querySelectorAll("[data-deadline]").forEach(function (el) {
      var ymd = el.getAttribute("data-deadline");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(ymd)) return;
      var days = Math.round((new Date(ymd + "T12:00:00Z").getTime() - today) / 864e5);
      var numEl = el.querySelector("[data-days-num]");
      if (numEl) numEl.textContent = String(Math.max(0, days));
      var unitEl = el.querySelector("[data-days-unit]");
      if (unitEl) unitEl.textContent = el.getAttribute(days === 1 ? "data-t-unit1" : "data-t-unitn") || unitEl.textContent;
      var txt = days < 0 ? el.getAttribute("data-t-past") : days === 0 ? el.getAttribute("data-t-today")
        : days === 1 ? el.getAttribute("data-t-tomorrow") : fill(el.getAttribute("data-t-days"), days);
      var label = el.querySelector("[data-days-label]") || el;
      if (txt) label.textContent = txt;
      var row = el.closest("[data-deadline-row]");
      if (row) row.classList.toggle("is-past", days < 0);
    });
  }

  /* ------------------------------------------------------------------
     "See who from our Area got published" card (/read/ and /contribute/): the numbers are
     counted when the site is built, so recount the "last N days" window here with the
     visitor's date (Central time) — the same rule as the home page (home.js spotlight) and
     /published/: story day >= today − N days. Each number carries one date per writer
     (data-dates: that writer's newest story) and each name chip its writer's date (data-date).
     Writers can only leave the window, so this only ever hides or lowers what the server drew:
     no Area 65 writer left → the "none from our Area" text and the Texas list; no Texas writer
     left → the plain text and no numbers. */
  function ymdToday() {
    try {
      var s = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
      if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    } catch (e) { /* fall through */ }
    return new Date().toISOString().slice(0, 10);
  }
  function ymdMinus(ymd, days) {
    var p = ymd.split("-");
    return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] - days)).toISOString().slice(0, 10);
  }
  function recountSpot(root) {
    var days = parseInt(root.getAttribute("data-spot-days"), 10);
    if (!(days > 0)) return;
    var cutoff = ymdMinus(ymdToday(), days);
    var n = { area: 0, texas: 0 };
    root.querySelectorAll("[data-spot-n]").forEach(function (dd) {
      var key = dd.getAttribute("data-spot-n"), c = 0;
      (dd.getAttribute("data-dates") || "").split(" ").forEach(function (d) { if (d && d >= cutoff) c++; });
      n[key] = c;
      dd.textContent = String(c);
      var box = dd.parentNode, dt = box.querySelector("dt");
      if (dt) dt.textContent = dt.getAttribute(c === 1 ? "data-one" : "data-many") || dt.textContent;
      box.hidden = !c;
    });
    root.querySelectorAll("[data-spot-list] [data-date]").forEach(function (li) {
      li.hidden = li.getAttribute("data-date") < cutoff;
    });
    var list = n.area ? "area" : "texas";
    root.querySelectorAll("[data-spot-list]").forEach(function (el) {
      el.hidden = el.getAttribute("data-spot-list") !== list || !el.querySelector("[data-date]:not([hidden])");
    });
    var nums = root.querySelector("[data-spot-nums]");
    if (nums) nums.hidden = !n.texas;
    var mode = !n.texas ? "nodata" : !n.area ? "none_area" : "main";
    root.querySelectorAll("[data-spot-text]").forEach(function (el) { el.hidden = el.getAttribute("data-spot-text") !== mode; });
  }
  function recountSpots() {
    document.querySelectorAll("[data-spot-days]").forEach(function (root) {
      try { recountSpot(root); } catch (e) { /* keep the server-rendered card */ }
    });
  }

  /* ------------------------------------------------------------------
     Share your story › "Next workshops" ([data-ws-list]): each row carries the moment its workshop
     ends ([data-cm-expire="ISO"], the same attribute as /events/ — committee.js is not loaded here).
     The page is built once a day, so a workshop that has ended hides itself, and the list with it
     when none is left. */
  function expireWorkshops() {
    var now = Date.now();
    document.querySelectorAll("[data-ws-list]").forEach(function (list) {
      var left = 0;
      list.querySelectorAll("[data-cm-expire]").forEach(function (el) {
        var t = Date.parse(el.getAttribute("data-cm-expire"));
        if (t && t <= now) el.hidden = true; else left++;
      });
      list.hidden = !left;
    });
  }

  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    /* ---------------- Read page: publication filter + older issues ---------------- */
    Alpine.data("readPage", function (cfg) {
      cfg = cfg || {};
      return {
        pub: "all",
        older: [],
        loading: false,
        failed: false,
        remaining: Number(cfg.remaining) || 0,
        init: function () {
          var self = this;
          var saved = store("read-pub");
          // Remember the visitor's publication filter — only while the filter chips are on the page.
          if (cfg.chips && (saved === "gv" || saved === "lv")) this.pub = saved;
          // Arriving via "#gv-current" / "#lv-current": never hide the issue the visitor asked for.
          var h = (location.hash || "").replace("#", "");
          if (/^(gv|lv)-current$/.test(h) && !this.show(h.slice(0, 2))) this.pub = "all";
          // The same on the page: the hero's "This month's Grapevine" / "Current La Viña" buttons sit
          // outside this component, so a document-level listener catches them (and any other link to
          // #gv-current / #lv-current). When the saved filter hides that magazine, show both again,
          // then scroll to it once Alpine has shown it (x-show reveals on the next frame).
          document.addEventListener("click", function (e) {
            if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            var a = e.target && e.target.closest ? e.target.closest('a[href^="#"]') : null;
            var id = a ? a.getAttribute("href").slice(1) : "";
            if (!/^(gv|lv)-current$/.test(id) || self.show(id.slice(0, 2))) return;   // visible: the normal jump works
            e.preventDefault();
            if (location.hash !== "#" + id) { try { history.pushState(null, "", "#" + id); } catch (err) { /* file:// etc. */ } }
            self.reveal(id);
          });
          window.addEventListener("hashchange", function () {
            var id = (location.hash || "").replace("#", "");
            if (/^(gv|lv)-current$/.test(id) && !self.show(id.slice(0, 2))) self.reveal(id);
          });
        },
        reveal: function (id) {
          this.pub = "all";
          var tries = 0;
          (function go() {
            var el = document.getElementById(id);
            if (!el) return;
            if (!el.getClientRects().length && tries++ < 20) { requestAnimationFrame(go); return; }
            el.scrollIntoView({ block: "start" });
          })();
        },
        show: function (p) { return this.pub === "all" || this.pub === p; },
        setPub: function (p) { this.pub = p; store("read-pub", p); },
        // Site-relative asset path ("/assets/cache/…") → URL that respects the site's base path.
        assetUrl: function (p) { return p && window.GV && window.GV.url ? window.GV.url(p) : p; },
        visibleCount: function () {
          var self = this, n = 0;
          (cfg.counts || []).forEach(function (c) { if (self.show(c)) n++; });
          this.older.forEach(function (i) { if (self.show(i.pub)) n++; });
          return n;
        },
        loadOlder: function () {
          var self = this;
          if (self.loading || !cfg.json) return;   // a second tap while loading just waits for the same fetch
          self.loading = true; self.failed = false;
          fetch(window.GV ? window.GV.url(cfg.json) : cfg.json, { cache: "no-cache" })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (data) {
              self.older = (data && data.issues) || [];
              self.remaining = 0;
            })
            .catch(function () { self.failed = true; })
            .then(function () { self.loading = false; });
        },
      };
    });

    /* ---------------- GVR / RLV first-steps checklist (saved in this browser) ---------------- */
    Alpine.data("gvrChecklist", function (key, total) {
      return {
        key: key || "gvr-checklist-v1",
        total: Number(total) || 0,
        done: {},
        init: function () { this.done = store(this.key) || {}; },
        toggle: function (id) {
          var d = Object.assign({}, this.done);
          if (d[id]) delete d[id]; else d[id] = true;
          this.done = d;
          store(this.key, d);
        },
        isDone: function (id) { return !!this.done[id]; },
        count: function () { return Object.keys(this.done).length; },
        pct: function () { return this.total ? Math.round((this.count() / this.total) * 100) : 0; },
        reset: function () { this.done = {}; store(this.key, {}); },
      };
    });
  });

  function refresh() { updateDeadlines(); recountSpots(); expireWorkshops(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", refresh);
  else refresh();
  // A tab left open past midnight: recount when the page is shown again.
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible") refresh(); });
})();
