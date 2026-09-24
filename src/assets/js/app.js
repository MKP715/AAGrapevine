/* NETA 65 Grapevine / La Viña — shared client script (loaded on every page).
   Registers Alpine components before Alpine starts (this file is loaded first). */
(function () {
  "use strict";
  var LANG = (window.SITE && window.SITE.lang) || document.documentElement.lang || "en";
  var LOCALE = LANG === "es" ? "es-US" : "en-US";
  var TZ = (window.SITE && window.SITE.tz) || "America/Chicago";

  /* ---------------- tiny utilities (window.GV) ---------------- */
  var GV = (window.GV = window.GV || {});
  GV.lang = LANG;
  GV.base = (window.SITE && window.SITE.base) || "/";
  GV.url = function (p) { return GV.base.replace(/\/$/, "") + (p.charAt(0) === "/" ? p : "/" + p); };
  GV.t = function (en, es) { return LANG === "es" ? es : en; };

  GV.copy = function (text, btn) {
    var done = function () {
      if (!btn) return;
      var prev = btn.getAttribute("data-label") || btn.innerHTML;
      btn.setAttribute("data-label", prev);
      btn.innerHTML = GV.t("Copied!", "¡Copiado!");
      btn.classList.add("is-active");
      setTimeout(function () { btn.innerHTML = prev; btn.classList.remove("is-active"); }, 1600);
    };
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(done, fallback);
    fallback();
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) {}
      document.body.removeChild(ta);
    }
  };

  GV.share = function (data, btn) {
    if (navigator.share) return navigator.share(data).catch(function () {});
    GV.copy((data.title ? data.title + "\n" : "") + (data.text ? data.text + "\n" : "") + (data.url || location.href), btn);
  };

  GV.fmtDate = function (d, opts) {
    try { return new Intl.DateTimeFormat(LOCALE, Object.assign({ timeZone: TZ }, opts || {})).format(new Date(d)); } catch (e) { return ""; }
  };

  GV.relative = function (d) {
    var rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });
    var diff = (new Date(d).getTime() - Date.now()) / 1000, abs = Math.abs(diff);
    var units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
    for (var i = 0; i < units.length; i++) if (abs >= units[i][1] || units[i][0] === "minute") return rtf.format(Math.round(diff / units[i][1]), units[i][0]);
    return "";
  };

  /* ---------------- calendar files (.ics, RFC 5545) ----------------
     The one .ics writer for the browser. GV.ics({title, description, location, url, start, end,
     allDay, uid, filename}) downloads a single event. start/end: Date or ISO string; with allDay,
     "YYYY-MM-DD" (end = last day, inclusive). GV.icsText(ev) returns the file text. */
  // TEXT escaping: CRLF/CR → LF first, then \ ; , and newlines.
  GV.icsEsc = function (s) {
    return String(s == null ? "" : s).replace(/\r\n?/g, "\n").replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n");
  };
  // Content lines longer than 75 octets are folded (CRLF + one space), never inside a UTF-8 character.
  GV.icsFold = function (line) {
    var enc = window.TextEncoder ? new TextEncoder() : null;
    var bytes = function (s) { return enc ? enc.encode(s).length : unescape(encodeURIComponent(s)).length; };
    if (bytes(line) <= 75) return line;
    var out = [], cur = "", n = 0, limit = 75;
    Array.from(line).forEach(function (ch) {
      var b = bytes(ch);
      if (n + b > limit) { out.push(cur); cur = ""; n = 0; limit = 74; } // continuation lines start with a space
      cur += ch; n += b;
    });
    if (cur) out.push(cur);
    return out.join("\r\n ");
  };
  GV.icsText = function (ev) {
    function stamp(d) { return new Date(d).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, ""); }
    function day(d) { return (typeof d === "string" ? d : new Date(d).toISOString()).slice(0, 10); }
    function ymd(d) { return day(d).replace(/-/g, ""); }
    function nextDay(d) { var t = new Date(day(d) + "T12:00:00Z"); t.setUTCDate(t.getUTCDate() + 1); return t.toISOString().slice(0, 10); }
    var L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//NETA 65 Grapevine La Vina Committee//Event//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
      "UID:" + String(ev.uid || stamp(ev.start) + "@neta65-gvlv").replace(/[\r\n]/g, ""), "DTSTAMP:" + stamp(new Date())];
    if (ev.allDay) L.push("DTSTART;VALUE=DATE:" + ymd(ev.start), "DTEND;VALUE=DATE:" + ymd(nextDay(ev.end || ev.start)), "TRANSP:TRANSPARENT");
    else L.push("DTSTART:" + stamp(ev.start), "DTEND:" + stamp(ev.end || ev.start));
    L.push("SUMMARY:" + GV.icsEsc(ev.title));
    if (ev.description) L.push("DESCRIPTION:" + GV.icsEsc(ev.description));
    if (ev.location) L.push("LOCATION:" + GV.icsEsc(ev.location));
    if (ev.url) L.push("URL:" + String(ev.url).replace(/[\s]/g, ""));
    L.push("END:VEVENT", "END:VCALENDAR");
    return L.map(GV.icsFold).join("\r\n") + "\r\n";
  };
  GV.ics = function (ev) {
    var blob = new Blob([GV.icsText(ev)], { type: "text/calendar;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = (ev.filename || "event") + ".ics";
    document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
  };

  /* Next occurrence of an "nth weekday of month" meeting, in America/Chicago. */
  GV.nextMeeting = function (rule) {
    // rule: {weekday:3, n:3, start:"19:00", end:"20:00", skip:["2026-12-16"]}
    function chicagoOffset(d) {
      try {
        var p = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", timeZoneName: "shortOffset" }).formatToParts(d);
        var tz = (p.find(function (x) { return x.type === "timeZoneName"; }) || {}).value || "GMT-6";
        var m = tz.match(/GMT([+-]\d+)/); return m ? Number(m[1]) * 60 : -360;
      } catch (e) { return -360; }
    }
    function at(y, mo, day, hhmm) {
      var hm = String(hhmm || "19:00").split(":");
      var g = new Date(Date.UTC(y, mo, day, Number(hm[0]), Number(hm[1] || 0)));
      return new Date(g.getTime() - chicagoOffset(g) * 60000);
    }
    var now = new Date(), skip = rule.skip || [];
    for (var i = 0; i < 15; i++) {
      var base = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + i, 1));
      var y = base.getUTCFullYear(), mo = base.getUTCMonth();
      var first = new Date(Date.UTC(y, mo, 1)).getUTCDay();
      var day;
      if (rule.n === -1) { var last = new Date(Date.UTC(y, mo + 1, 0)); day = last.getUTCDate() - ((last.getUTCDay() - rule.weekday + 7) % 7); }
      else day = 1 + ((rule.weekday - first + 7) % 7) + (rule.n - 1) * 7;
      var ymd = y + "-" + String(mo + 1).padStart(2, "0") + "-" + String(day).padStart(2, "0");
      if (skip.indexOf(ymd) !== -1) continue;
      var end = at(y, mo, day, rule.end || rule.start);
      if (end > now) return { start: at(y, mo, day, rule.start), end: end, ymd: ymd };
    }
    return null;
  };

  /* ---------------- Alpine components ---------------- */
  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    /* Header: transparent (white text) over the dark page hero, solid once scrolled.
       A page without a hero (no ui.pageHero) — or <body data-header="solid"> — gets the
       solid header from the start, so its white text never sits on the light page. */
    Alpine.data("siteHeader", function () {
      return {
        solid: false, drawer: false, theme: document.documentElement.getAttribute("data-theme") || "light",
        force: document.body.getAttribute("data-header") === "solid" || !document.querySelector("[data-gv-hero], .page-hero, #homeHero"),
        init: function () {
          var self = this;
          var onScroll = function () { self.solid = self.force || window.scrollY > 24; };
          onScroll();
          window.addEventListener("scroll", onScroll, { passive: true });
          this.$watch("drawer", function (v) { document.documentElement.style.overflow = v ? "hidden" : ""; });
        },
        toggleTheme: function () {
          this.theme = this.theme === "dark" ? "light" : "dark";
          document.documentElement.setAttribute("data-theme", this.theme);
          try { localStorage.setItem("theme", this.theme); } catch (e) {}
        },
      };
    });

    /* Live countdown: x-data="countdown('2026-10-21T00:00:00Z')" */
    Alpine.data("countdown", function (iso) {
      return {
        target: new Date(iso).getTime(), d: 0, h: 0, m: 0, s: 0, live: false, done: false,
        init: function () { this.tick(); var self = this; this._t = setInterval(function () { self.tick(); }, 1000); },
        destroy: function () { clearInterval(this._t); },
        tick: function () {
          var diff = Math.max(0, this.target - Date.now());
          this.done = diff === 0;
          this.d = Math.floor(diff / 864e5); this.h = Math.floor((diff % 864e5) / 36e5);
          this.m = Math.floor((diff % 36e5) / 6e4); this.s = Math.floor((diff % 6e4) / 1e3);
        },
        pad: function (n) { return String(n).padStart(2, "0"); },
      };
    });

    /* Generic client-side filter for lists rendered at build time:
       <div x-data="filterList()"> <button @click="set('kind','pdf')">…  <li x-show="match($el)" data-kind="pdf" data-text="…"> */
    Alpine.data("filterList", function (initial) {
      return {
        q: "", f: Object.assign({}, initial || {}),
        set: function (k, v) { this.f[k] = this.f[k] === v ? "" : v; },
        is: function (k, v) { return (this.f[k] || "") === v; },
        match: function (el) {
          for (var k in this.f) { if (this.f[k] && (el.dataset[k] || "").split(" ").indexOf(this.f[k]) === -1) return false; }
          if (this.q) { var t = (el.dataset.text || el.textContent || "").toLowerCase(); var terms = this.q.toLowerCase().split(/\s+/); for (var i = 0; i < terms.length; i++) if (t.indexOf(terms[i]) === -1) return false; }
          return true;
        },
      };
    });
  });

  /* ---------------- progressive enhancements ---------------- */
  function enhance() {
    // Show build/update times in the visitor's local time
    document.querySelectorAll("time[data-local-time]").forEach(function (el) {
      var d = el.getAttribute("datetime"); if (!d) return;
      el.textContent = GV.fmtDate(d, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZone: undefined });
      el.title = GV.relative(d);
    });
    document.querySelectorAll("time[data-relative]").forEach(function (el) {
      var d = el.getAttribute("datetime"); if (d) el.textContent = GV.relative(d);
    });
    // Copy buttons: <button data-copy="text">
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-copy]");
      if (b) { e.preventDefault(); GV.copy(b.getAttribute("data-copy"), b); }
      var s = e.target.closest("[data-share]");
      if (s) { e.preventDefault(); GV.share({ title: s.getAttribute("data-share-title") || document.title, text: s.getAttribute("data-share-text") || "", url: s.getAttribute("data-share") || location.href }, s); }
    });
    // Language switch keeps the #hash and ?query
    document.querySelectorAll("[data-lang-switch]").forEach(function (a) {
      a.addEventListener("click", function () { if (location.hash || location.search) a.href = a.href.split(/[?#]/)[0] + location.search + location.hash; });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", enhance); else enhance();
})();
