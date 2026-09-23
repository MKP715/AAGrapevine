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
  // Lower-case and drop accents so "catalogo" finds "Catálogo" and "vina" finds "Viña".
  function fold(s) {
    s = String(s || "").toLowerCase();
    try { return s.normalize("NFD").replace(/[̀-ͯ]/g, ""); } catch (e) { return s; }
  }

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
          var saved = store("read-pub");
          // Remember the visitor's publication filter — only while the filter chips are on the page.
          if (cfg.chips && (saved === "gv" || saved === "lv")) this.pub = saved;
          // Arriving via "#gv-current" / "#lv-current": never hide the issue the visitor asked for.
          var h = (location.hash || "").replace("#", "");
          if (/^(gv|lv)-current$/.test(h) && !this.show(h.slice(0, 2))) this.pub = "all";
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

    /* ---------------- Resource-kit filter (kit chips + text search) ---------------- */
    Alpine.data("kitFilter", function (initialKit) {
      return {
        kit: initialKit || "all",
        q: "",
        shown: 0,
        init: function () {
          var self = this;
          this.$nextTick(function () { self.recount(); });
          this.$watch("q", function () { self.recount(); });
          this.$watch("kit", function () { self.recount(); });
        },
        terms: function () { return fold(this.q).trim().split(/\s+/).filter(Boolean); },
        match: function (el) {
          if (this.kit !== "all" && el.getAttribute("data-kit") !== this.kit) return false;
          var t = this.terms();
          if (!t.length) return true;
          var hay = el._hay || (el._hay = fold(el.getAttribute("data-text") || el.textContent || ""));
          for (var i = 0; i < t.length; i++) if (hay.indexOf(t[i]) === -1) return false;
          return true;
        },
        kitVisible: function (k) { return this.kit === "all" || this.kit === k; },
        recount: function () {
          var self = this, n = 0;
          this.$root.querySelectorAll("[data-kit-item]").forEach(function (el) { if (self.match(el)) n++; });
          this.shown = n;
        },
        groupHas: function (el) {
          var self = this, items = el.querySelectorAll("[data-kit-item]");
          for (var i = 0; i < items.length; i++) if (self.match(items[i])) return true;
          return false;
        },
      };
    });
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", updateDeadlines);
  else updateDeadlines();
})();
