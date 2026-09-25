/* Community pages: What's New, Weekly Digest, the GV/LV report (/monthly/#report), Share Kit, Status, 404.
   Loaded (defer) before Alpine, so components register on "alpine:init".
   Everything here is a progressive enhancement: pages are fully readable
   without JavaScript. User-visible strings come from data-* attributes
   rendered by the templates (never hard-coded here). */
(function () {
  "use strict";
  var GV = window.GV || {};
  var TZ = (window.SITE && window.SITE.tz) || "America/Chicago";

  /* YYYY-MM-DD for a Date in Central time (matches the build-time grouping). */
  function ymd(d) {
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
    } catch (e) {
      return d.toISOString().slice(0, 10);
    }
  }
  function fill(tpl, n) { return String(tpl || "").replace("{n}", n); }

  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    /* ---------------- What's New timeline ----------------
       Days: <li data-day="2026-09-23" data-counts='{"pdf":3}' data-total="5">
       Entries: <li data-kind="pdf" data-rank="0" data-rank-all="2">          */
    Alpine.data("whatsNew", function (opts) {
      opts = opts || {};
      var initialDays = opts.days || 10;
      return {
        filter: "",
        limit: initialDays,
        cap: opts.cap || 6,
        open: {},
        days: [],
        status: "",
        init: function () {
          var self = this;
          // counts/total = timeline ENTRIES (a magazine issue is one entry);
          // icounts/itotal = underlying ITEMS (what the chips and status report).
          var json = function (el, a) { try { return JSON.parse(el.getAttribute(a) || "{}"); } catch (e) { return {}; } };
          this.days = Array.prototype.map.call(this.$el.querySelectorAll("[data-day]"), function (el) {
            return {
              ymd: el.getAttribute("data-day"),
              counts: json(el, "data-counts"), total: Number(el.getAttribute("data-total")) || 0,
              icounts: json(el, "data-icounts"), itotal: Number(el.getAttribute("data-itotal")) || 0,
            };
          });
          // Shareable filtered view: /whats-new/?type=pdf
          var m = /[?&]type=([a-z]+)/.exec(location.search);
          if (m && this.days.some(function (d) { return d.counts[m[1]]; })) this.filter = m[1];
          this.updateStatus();
        },
        count: function (d) { return this.filter ? d.counts[this.filter] || 0 : d.total; },
        /* Day heading "7 updates": the day's ITEMS of the chosen type (a magazine issue
           counts its stories, like the chips and the status line), all items without a filter. */
        dayLabel: function (day) {
          var d = this.days.find(function (x) { return x.ymd === day; });
          var n = d ? (this.filter ? d.icounts[this.filter] || 0 : d.itotal) : 0;
          return fill(this.$root.getAttribute(n === 1 ? "data-day-one" : "data-day-many"), n);
        },
        matchingDays: function () { var self = this; return this.days.filter(function (d) { return self.count(d) > 0; }); },
        dayShown: function (day) {
          var list = this.matchingDays().slice(0, this.limit);
          for (var i = 0; i < list.length; i++) if (list[i].ymd === day) return true;
          return false;
        },
        entryShown: function (el) {
          var kind = el.getAttribute("data-kind");
          if (this.filter && kind !== this.filter) return false;
          var dayEl = el.closest("[data-day]");
          if (dayEl && this.open[dayEl.getAttribute("data-day")]) return true;
          return Number(el.getAttribute(this.filter ? "data-rank" : "data-rank-all")) < this.cap;
        },
        hiddenIn: function (day) {
          if (this.open[day]) return 0;
          var d = this.days.find(function (x) { return x.ymd === day; });
          return d ? Math.max(0, this.count(d) - this.cap) : 0;
        },
        moreLabel: function (day) { return fill(this.$root.getAttribute("data-more"), this.hiddenIn(day)); },
        /* The button hides itself once the day is open, so keyboard / screen-reader focus
           moves to the first entry it revealed (never lost to <body>). */
        openDay: function (day) {
          var self = this;
          var rankAttr = this.filter ? "data-rank" : "data-rank-all";
          this.open[day] = true;
          this.$nextTick(function () {
            var dayEl = self.$root.querySelector('[data-day="' + day + '"]');
            if (!dayEl) return;
            var first = Array.prototype.find.call(dayEl.querySelectorAll(".cm-entry"), function (el) {
              return (!self.filter || el.getAttribute("data-kind") === self.filter) && Number(el.getAttribute(rankAttr)) >= self.cap;
            });
            var target = first && first.querySelector(".cm-entry-link");
            if (!target && first) { target = first.querySelector("h3"); if (target) target.setAttribute("tabindex", "-1"); }
            if (!target) { target = dayEl.querySelector(".cm-day-head"); if (target) target.setAttribute("tabindex", "-1"); }
            if (target) target.focus();
          });
        },
        moreDays: function () { return this.matchingDays().length > this.limit; },
        showOlder: function () { this.limit += initialDays; },
        filteredTotal: function () {
          var f = this.filter;
          return this.days.reduce(function (a, d) { return a + (f ? d.icounts[f] || 0 : d.itotal); }, 0);
        },
        setFilter: function (k) {
          this.filter = k;
          this.limit = initialDays;
          this.updateStatus();
          try {
            var u = new URL(location.href);
            if (k) u.searchParams.set("type", k); else u.searchParams.delete("type");
            history.replaceState(null, "", u.toString());
          } catch (e) { /* old browser: filter still works */ }
        },
        updateStatus: function () { this.status = fill(this.$root.getAttribute("data-showing"), this.filteredTotal()); },
      };
    });

    /* ---------------- Weekly digest ---------------- */
    Alpine.data("digestPage", function () {
      return {
        bi: false,
        init: function () {
          try { this.bi = localStorage.getItem("gv-digest-bi") === "1"; } catch (e) { /* ignore */ }
          this.$watch("bi", function (v) { try { localStorage.setItem("gv-digest-bi", v ? "1" : "0"); } catch (e) { /* ignore */ } });
        },
        src: function (kind) { return "#digest-" + kind + "-" + (this.bi ? "bi" : "one"); },
      };
    });

    /* ---------------- GV/LV report on /monthly/ (language toggle) ---------------- */
    Alpine.data("reportBox", function (lang) {
      return { rl: lang || "en" };
    });

    /* ---------------- Share kit ---------------- */
    Alpine.data("sharePage", function (lang) {
      return {
        qrLang: lang || "en",
        posterLang: lang || "en",
        init: function () {
          window.addEventListener("afterprint", function () { document.documentElement.removeAttribute("data-print"); });
        },
        printVariant: function (which) {
          document.documentElement.setAttribute("data-print", which);
          // Let the attribute apply before the print dialog snapshots the page.
          setTimeout(function () { window.print(); }, 60);
        },
      };
    });
  });

  /* ---------------- enhancements (no Alpine needed) ---------------- */
  function enhance() {
    // "Today" / "Yesterday" prefixes on What's New day headings (computed in
    // the browser so a page built yesterday still reads correctly today).
    var wrap = document.querySelector("[data-today]");
    if (wrap) {
      var now = new Date();
      var today = ymd(now), yest = ymd(new Date(now.getTime() - 864e5));
      document.querySelectorAll("[data-rel-day]").forEach(function (el) {
        var d = el.getAttribute("data-rel-day");
        var label = d === today ? wrap.getAttribute("data-today") : d === yest ? wrap.getAttribute("data-yesterday") : "";
        if (label) el.textContent = label + " · ";
      });
    }

    // Copy the text of another element: <button data-copy-from="#id">
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-copy-from]");
      if (!b) return;
      e.preventDefault();
      var src = document.querySelector(b.getAttribute("data-copy-from"));
      if (src && GV.copy) GV.copy("value" in src && src.tagName === "TEXTAREA" ? src.value : src.textContent, b);
    });

    // Download a QR <svg> as PNG: <button data-qr-png="#wrapper" data-filename="x.png">
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-qr-png]");
      if (!b) return;
      e.preventDefault();
      var holder = document.querySelector(b.getAttribute("data-qr-png"));
      var svg = holder && holder.querySelector("svg");
      if (!svg) return;
      // Whole pixels per QR module, otherwise rows get hairline seams.
      var vb = (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width) || 0;
      var size = vb ? vb * Math.max(1, Math.floor(1200 / vb)) : 1200;
      svgToPng(svg, size, b.getAttribute("data-filename") || "qr-code.png");
    });

    // 404: show the missing address and pre-fill the search box with words
    // from it ("/library/sponsorship-flyer.pdf" → "library sponsorship flyer").
    var nf = document.querySelector("[data-nf]");
    if (nf) {
      var base = (window.SITE && window.SITE.base) || "/";
      var path = location.pathname;
      try { path = decodeURIComponent(path); } catch (e) { /* malformed %-escape: use as-is */ }
      if (path.indexOf(base) === 0) path = path.slice(base.length);
      var words = path.replace(/^es\//, "").replace(/\.[a-z0-9]{2,5}$/i, "")  // any file extension (.html, .php, .pdf …)
        .split(/[\/\-_.+]+/).filter(function (w) { return w && w.length > 2 && !/^(index|grapevine|es|en|www|\d+)$/i.test(w); });
      var q = words.slice(-4).join(" ");
      if (q) document.querySelectorAll("[data-nf-query]").forEach(function (i) { if (!i.value) i.value = q; });
      if (!/404\.html$/.test(location.pathname)) {
        document.querySelectorAll("[data-nf-path]").forEach(function (el) { el.textContent = location.pathname; });
      }
    }
  }

  function svgToPng(svg, size, filename) {
    var clone = svg.cloneNode(true);
    clone.setAttribute("width", size);
    clone.setAttribute("height", size);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    var xml = new XMLSerializer().serializeToString(clone);
    var img = new Image();
    img.onload = function () {
      var c = document.createElement("canvas");
      c.width = c.height = size;
      var ctx = c.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, size, size);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(img, 0, 0, size, size);
      c.toBlob(function (blob) {
        if (!blob) return;
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
      }, "image/png");
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", enhance); else enhance();
})();
