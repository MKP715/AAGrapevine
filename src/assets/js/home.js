/* Home page behaviour — NETA 65 Grapevine / La Viña.
   Loaded (defer) after app.js and before Alpine, so components register on alpine:init.

   - homeMeeting: live countdown to the next committee meeting (the card in the hero from
     1024px and the one under the hero on smaller screens — one of them is displayed). If the
     last site build is older than the meeting (e.g. the daily build failed for a few days),
     it works out the next date itself from the meeting rule, so the card is never out of date.
   - homePlayer: the "More episodes" play buttons load into the featured audio player.
   - Published-writers spotlight: recounts the "last 60 days" window with the visitor's own
     date (plain JS, no Alpine needed), so stories drop out on time between daily builds,
     and re-sizes the "see the full list" tile that closes the grid's last row.
   The hero art (hero-canvas.js, loaded by base.njk) sizes itself; nothing to do here. */
(function () {
  "use strict";
  var TZ = (window.SITE && window.SITE.tz) || "America/Chicago";

  function locale() {
    var lang = (window.GV && window.GV.lang) || document.documentElement.lang || "en";
    return lang === "es" ? "es-US" : "en-US";
  }

  // Same wording as the server-side filters (homeMeetingDate + homeTimeRange).
  function meetingLabels(start, end) {
    var loc = locale(), out = { date: "", time: "" };
    try {
      out.date = new Intl.DateTimeFormat(loc, { weekday: "long", month: "long", day: "numeric", timeZone: TZ }).format(start);
      if (loc === "es-US") out.date = out.date.charAt(0).toUpperCase() + out.date.slice(1);
      var tf = new Intl.DateTimeFormat(loc, { hour: "numeric", minute: "2-digit", timeZone: TZ, timeZoneName: "short" });
      out.time = end && end > start && tf.formatRange ? tf.formatRange(start, end) : tf.format(start);
    } catch (e) { /* very old browser: keep the server text */ }
    return out;
  }

  // "2026-10-21" — the meeting's calendar date in Central time (a 7 PM meeting is
  // already the next day in UTC, which made the file name look a day off).
  function localYmd(ms) {
    try {
      var p = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: TZ }).formatToParts(new Date(ms));
      var get = function (t) { for (var i = 0; i < p.length; i++) if (p[i].type === t) return p[i].value; return ""; };
      var s = get("year") + "-" + get("month") + "-" + get("day");
      if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    } catch (e) { /* fall through */ }
    return new Date(ms).toISOString().slice(0, 10);
  }

  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    Alpine.data("homeMeeting", function () {
      return {
        start: 0, end: 0, d: 0, h: 0, m: 0, s: 0,
        live: false, ready: false, rule: null, dateLabel: "", timeLabel: "",

        init: function () {
          var ds = this.$el.dataset;
          try { this.rule = JSON.parse(ds.rule || "null"); } catch (e) { this.rule = null; }
          this.start = Date.parse(ds.start) || 0;
          this.end = Date.parse(ds.end) || (this.start ? this.start + 3600e3 : 0);
          // Start from the server-rendered text so nothing flickers.
          this.dateLabel = this.$refs.date ? this.$refs.date.textContent.trim() : "";
          this.timeLabel = this.$refs.time ? this.$refs.time.textContent.trim() : "";
          this.tick();
          var self = this;
          this._timer = setInterval(function () { self.tick(); }, 1000);
          this.ready = true;
        },
        destroy: function () { clearInterval(this._timer); },

        // Move on to the next meeting once this one has ended.
        advance: function () {
          if (!this.rule || !window.GV || !window.GV.nextMeeting) return;
          var n = window.GV.nextMeeting(this.rule);
          if (!n || n.end.getTime() <= Date.now()) return;
          this.start = n.start.getTime();
          this.end = n.end.getTime();
          var l = meetingLabels(n.start, n.end);
          if (l.date) this.dateLabel = l.date;
          if (l.time) this.timeLabel = l.time;
        },

        tick: function () {
          var now = Date.now();
          if (this.end && now >= this.end) this.advance();
          this.live = !!this.start && now >= this.start && now < this.end;
          var diff = Math.max(0, this.start - now);
          this.d = Math.floor(diff / 864e5);
          this.h = Math.floor((diff % 864e5) / 36e5);
          this.m = Math.floor((diff % 36e5) / 6e4);
          this.s = Math.floor((diff % 6e4) / 1e3);
        },

        pad: function (n) { return n < 10 ? "0" + n : String(n); },

        addToCalendar: function () {
          if (!window.GV || !window.GV.ics || !this.start) return;
          var ds = this.$root.dataset; // $el would be the clicked button here
          window.GV.ics({
            title: ds.calTitle, description: ds.calDesc, location: ds.calLoc, url: ds.calLoc,
            start: new Date(this.start), end: new Date(this.end),
            // Same UID as the Events page / calendar feed use for this meeting, so adding it
            // twice updates the entry instead of duplicating it.
            uid: "ev-committee-" + localYmd(this.start) + (locale() === "es-US" ? "-es" : "") + "@neta65-gvlv",
            filename: "neta65-grapevine-committee-" + localYmd(this.start),
          });
        },
      };
    });

    Alpine.data("homePlayer", function () {
      return {
        current: "", playing: false, title: "", meta: "",

        init: function () {
          this.title = this.$refs.title ? this.$refs.title.textContent.trim() : "";
          this.meta = this.$refs.meta ? this.$refs.meta.textContent.trim() : "";
          var a = this.$refs.audio, self = this;
          if (!a) return;
          a.addEventListener("play", function () { self.playing = true; });
          a.addEventListener("pause", function () { self.playing = false; });
          a.addEventListener("ended", function () { self.playing = false; });
        },

        // el = the play link of an episode in the list (href = mp3, works without JS too)
        play: function (el) {
          var a = this.$refs.audio;
          if (!a) { window.open(el.href, "_blank", "noopener"); return; }
          var id = el.dataset.id;
          if (this.current === id) { if (a.paused) a.play().catch(function () {}); else a.pause(); return; }
          this.current = id;
          this.title = el.dataset.title || this.title;
          this.meta = el.dataset.meta || "";
          if (this.$refs.art && el.dataset.img) { this.$refs.art.style.visibility = ""; this.$refs.art.src = el.dataset.img; }
          a.src = el.dataset.src;
          a.play().catch(function () {});
        },

        isPlaying: function (id) { return this.current === id && this.playing; },
      };
    });
  });

  /* ---------- Published-writers spotlight: keep the "last N days" window current ----------
     The page is built once a day; this recounts with the visitor's date (Central time, like
     the build — eleventy/filters/home.js homeSpotlight: pub_date >= today − N days) and hides
     the stories that have left the window since, then updates the counts, the notes
     ("no Area 65 story" / "no Texas story") and the "see the full list" tile that closes the
     grid's last row (fillGrid). Stories can only leave the window, never join it, so it only
     ever hides what the server rendered. The column count per screen width is CSS
     (home.css), so nothing needs redoing on resize. */
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
  /* How the grid closes its last row at `cols` columns: `a` Area 65 cards two columns wide (or
     the two-column "no Area 65 story" note when a = 0 and t > 0), then `t` Texas cards, placed
     like CSS `grid-auto-flow: row dense`. → { span: empty columns at the end of the last row
     (the "see the full list" tile fills them; 0 = no tile), full: Area 65 cards would leave gaps
     higher up, so each takes a whole row }. Same function as spotFill in
     eleventy/filters/home.js (which renders the first version): keep the two in step. */
  function spotFill(a, t, cols) {
    function run(featSpan) {
      var rows = [];
      function put(span) {
        for (var r = 0; ; r++) {
          if (!rows[r]) { rows[r] = []; for (var z = 0; z < cols; z++) rows[r].push(false); }
          for (var c = 0; c + span <= cols; c++) {
            var ok = true;
            for (var k = c; k < c + span; k++) if (rows[r][k]) { ok = false; break; }
            if (ok) { for (k = c; k < c + span; k++) rows[r][k] = true; return; }
          }
        }
      }
      var i;
      if (a > 0) for (i = 0; i < a; i++) put(featSpan);
      else if (t > 0) put(Math.min(2, cols));
      for (i = 0; i < t; i++) put(1);
      var free = 0, trailing = 0, last = rows[rows.length - 1] || [];
      for (i = 0; i < rows.length; i++) for (var j = 0; j < cols; j++) if (!rows[i][j]) free++;
      for (var n = last.length - 1; n >= 0 && !last[n]; n--) trailing++;
      return { free: free, trailing: trailing };
    }
    if (!a && !t) return { span: 0, full: false };
    var res = run(Math.min(2, cols)), full = false;
    if (res.free !== res.trailing) { full = true; res = run(cols); }
    return { span: res.trailing, full: full };
  }
  // Write the tile spans (data-f2…data-f5) and the whole-row columns (data-full) that home.css reads.
  function fillGrid(root, a, t) {
    var grid = root.querySelector("[data-spot-grid]"), tile = root.querySelector("[data-spot-fill]");
    if (!grid || !tile) return;
    var full = [];
    for (var cols = 2; cols <= 5; cols++) {
      var r = spotFill(a, t, cols);
      tile.setAttribute("data-f" + cols, String(r.span));
      if (r.full) full.push(cols);
    }
    if (full.length) grid.setAttribute("data-full", full.join(" ")); else grid.removeAttribute("data-full");
  }

  function spotlight(root) {
    var days = parseInt(root.getAttribute("data-days"), 10);
    if (!(days > 0)) return;
    var cutoff = ymdMinus(ymdToday(), days);
    var counts = { neta65: 0, texas: 0 };
    var items = root.querySelectorAll("[data-pub]");
    for (var i = 0; i < items.length; i++) {
      var li = items[i], pub = li.getAttribute("data-pub") || "";
      if (/^\d{4}-\d{2}-\d{2}$/.test(pub) && pub < cutoff) li.hidden = true;
      if (!li.hidden) counts[li.getAttribute("data-scope")] = (counts[li.getAttribute("data-scope")] || 0) + 1;
    }
    var total = counts.neta65 + counts.texas;
    var lists = root.querySelectorAll("[data-spot-list]");
    for (var j = 0; j < lists.length; j++) lists[j].hidden = !lists[j].querySelector("[data-pub]:not([hidden])");
    var toggle = function (sel, hide) { var el = root.querySelector(sel); if (el) el.hidden = hide; };
    toggle("[data-spot-grid]", !total);
    toggle("[data-spot-empty]", !!total);
    toggle("[data-spot-counts]", !total);
    toggle("[data-spot-none-a65]", counts.neta65 > 0 || counts.texas === 0);
    fillGrid(root, counts.neta65, counts.texas);
    var nf = null;
    try { nf = new Intl.NumberFormat(locale()); } catch (e) { nf = null; }
    var chips = root.querySelectorAll("[data-spot-count]");
    for (var k = 0; k < chips.length; k++) {
      var chip = chips[k], n = counts[chip.getAttribute("data-spot-count")] || 0;
      chip.hidden = !n;
      var tpl = n === 1 ? chip.getAttribute("data-one") : chip.getAttribute("data-many");
      var txt = chip.querySelector("[data-text]");
      if (txt && tpl) txt.textContent = tpl.replace("{n}", nf ? nf.format(n) : String(n));
    }
  }
  function initSpotlights() {
    var roots = document.querySelectorAll("[data-home-spot]");
    for (var i = 0; i < roots.length; i++) {
      try { spotlight(roots[i]); } catch (e) { /* keep the server-rendered list */ }
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initSpotlights); else initSpotlights();
})();
