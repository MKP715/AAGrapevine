/* Home page behaviour — NETA 65 Grapevine / La Viña.
   Loaded (defer) after app.js and before Alpine, so components register on alpine:init.

   - homeMeeting: live countdown to the next committee meeting. If the last site build
     is older than the meeting (e.g. the daily build failed for a few days), it works out
     the next date itself from the meeting rule, so the card is never out of date.
   - homePlayer: the "More episodes" play buttons load into the featured audio player.
   - Keeps the hero art sized when the hero's height changes after fonts load. */
(function () {
  "use strict";
  var TZ = (window.SITE && window.SITE.tz) || "America/Chicago";

  function locale() {
    var lang = (window.GV && window.GV.lang) || document.documentElement.lang || "en";
    return lang === "es" ? "es-US" : "en-US";
  }

  // Same wording as the server-side filters (fmtDate "long" + homeTimeRange).
  function meetingLabels(start, end) {
    var loc = locale(), out = { date: "", time: "" };
    try {
      out.date = new Intl.DateTimeFormat(loc, { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: TZ }).format(start);
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

  /* The hero art sizes itself on window resize only. If the hero gets taller or shorter
     for another reason (web fonts arriving, Alpine filling in text), nudge it. */
  function watchHero() {
    var hero = document.getElementById("homeHero");
    if (!hero || !("ResizeObserver" in window)) return;
    var last = hero.offsetHeight, timer = null;
    new ResizeObserver(function () {
      var h = hero.offsetHeight;
      if (Math.abs(h - last) < 24) return;
      last = h;
      clearTimeout(timer);
      timer = setTimeout(function () { window.dispatchEvent(new Event("resize")); }, 200);
    }).observe(hero);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchHero); else watchHero();
})();
