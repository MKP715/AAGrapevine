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

  /* Chip rows (chip-row / chip-row-nowrap, main.css) that scroll sideways: once scrolled, the
     left edge fades too (.is-scrolled), so a chip cut at the left never looks like a hard edge.
     (Element scroll events don't bubble — listen in the capture phase.) */
  document.addEventListener("scroll", function (e) {
    var t = e.target;
    if (!t || !t.classList || !(t.classList.contains("chip-row") || t.classList.contains("chip-row-nowrap"))) return;
    t.classList.toggle("is-scrolled", t.scrollLeft > 4);
  }, { capture: true, passive: true });

  /* One shared, visually hidden live region: status messages (e.g. "Copied") are read out by
     screen readers even when a button's own label does not change (WCAG 4.1.3). */
  var liveEl = null, liveTimer = 0;
  function liveRegion() {
    if (liveEl || !document.body) return liveEl;
    liveEl = document.createElement("div");
    liveEl.className = "sr-only";
    liveEl.setAttribute("role", "status");
    liveEl.setAttribute("aria-live", "polite");
    liveEl.setAttribute("data-gv-live", "");
    document.body.appendChild(liveEl);
    return liveEl;
  }
  GV.announce = function (msg) {
    var el = liveRegion(); if (!el) return;
    el.textContent = "";                       // clear first so the same message is read again
    clearTimeout(liveTimer);
    liveTimer = setTimeout(function () { el.textContent = msg; }, 60);
  };

  // With a button: it shows "Copied!" for a moment and screen readers hear the same through the
  // live region. Without one, the caller gives its own feedback (library.js announces itself).
  GV.copy = function (text, btn) {
    var done = function () {
      if (!btn) return;
      GV.announce(GV.t("Copied to clipboard", "Copiado al portapapeles"));
      var prev = btn.getAttribute("data-label") || btn.innerHTML;
      btn.setAttribute("data-label", prev);
      btn.innerHTML = GV.t("Copied!", "¡Copiado!");
      btn.classList.add("is-active");
      setTimeout(function () { btn.innerHTML = prev; btn.classList.remove("is-active"); }, 1600);
    };
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(done, fallback);
    fallback();
    function fallback() {
      var back = document.activeElement; // select() moves focus to the helper; give it back after
      var ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { if (document.execCommand("copy") !== false) done(); } catch (e) {}
      document.body.removeChild(ta);
      if (back && back !== document.body && back.focus) back.focus({ preventScroll: true });
    }
  };

  GV.share = function (data, btn) {
    if (navigator.share) return navigator.share(data).catch(function () {});
    GV.copy((data.title ? data.title + "\n" : "") + (data.text ? data.text + "\n" : "") + (data.url || location.href), btn);
  };

  /* Intl's Spanish "7:00 p.m." → "7:00 p. m.", the one spelling the whole site uses (the build
     twin is esMeridiem in eleventy.config.js). Both spaces are no-break spaces, so a narrow card
     never wraps "1:03 a." / "m.". No-op on English pages. */
  GV.esMeridiem = function (s) {
    s = s == null ? "" : String(s);
    return LANG === "es" ? s.replace(/\b([ap])\.\s?m\./g, "$1.\u00a0m.").replace(/(\d) (?=[ap]\.\u00a0m\.)/g, "$1\u00a0") : s;
  };

  GV.fmtDate = function (d, opts) {
    try { return GV.esMeridiem(new Intl.DateTimeFormat(LOCALE, Object.assign({ timeZone: TZ }, opts || {})).format(new Date(d))); } catch (e) { return ""; }
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

  /* Next occurrence of an "nth weekday of month" meeting, in America/Chicago.
     Same rule as the build (src/_data/meeting.js): a month without a 5th <weekday> is skipped,
     and the search starts one month back so an evening meeting on the last day of a month
     (already the next month in UTC) is still found while it is in progress. */
  GV.nextMeeting = function (rule) {
    // rule: {weekday:3, n:3, start:"19:00", end:"20:00", skip:["2026-12-16"]}  (n: 1-5, or -1 = last)
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
    var now = new Date(), skip = rule.skip || [], n = Number(rule.n);
    for (var i = -1; i < 15; i++) {
      var base = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + i, 1));
      var y = base.getUTCFullYear(), mo = base.getUTCMonth();
      var first = new Date(Date.UTC(y, mo, 1)).getUTCDay();
      var last = new Date(Date.UTC(y, mo + 1, 0));
      var day;
      if (n === -1) day = last.getUTCDate() - ((last.getUTCDay() - rule.weekday + 7) % 7);
      else day = 1 + ((rule.weekday - first + 7) % 7) + (n - 1) * 7;
      if (day > last.getUTCDate()) continue; // no 5th <weekday> this month
      var ymd = y + "-" + String(mo + 1).padStart(2, "0") + "-" + String(day).padStart(2, "0");
      if (skip.indexOf(ymd) !== -1) continue;
      var end = at(y, mo, day, rule.end || rule.start);
      if (end > now) return { start: at(y, mo, day, rule.start), end: end, ymd: ymd };
    }
    return null;
  };

  /* ---------------- Reading & display settings (GV.prefs) ----------------
     The "Aa" panel (partials/comfort-panel.njk) edits these; base.njk's first <head> script
     applied the saved ones before the first paint. localStorage "gvlv-prefs" (this device only;
     blocked storage just means nothing is remembered):
       { text: 100|115|130|150, spacing: "normal"|"relaxed", contrast: "normal"|"high",
         motion: "auto"|"reduce", saver: "off"|"on"|"auto" }
     → <html data-text data-spacing data-contrast data-motion data-saver>. data-saver is "on" for
     saver "on", or for "auto" (the default) while the browser asks to save data, the connection
     is 2G or the device is offline. Every change fires window event "gvlv:prefs" {detail: prefs}
     (also when another tab changes them, or an automatic data saver flips).
       GV.prefs.get() · .set({text: 130}) · .reset() · .isCustom() · .saverActive() · .defaults */
  var PREF_KEY = "gvlv-prefs";
  var PREF_DEFAULTS = { text: 100, spacing: "normal", contrast: "normal", motion: "auto", saver: "auto" };
  var PREF_VALUES = { text: [100, 115, 130, 150], spacing: ["normal", "relaxed"], contrast: ["normal", "high"], motion: ["auto", "reduce"], saver: ["off", "on", "auto"] };
  function cleanPrefs(p) {
    var o = {};
    p = p && typeof p === "object" ? p : {};
    Object.keys(PREF_DEFAULTS).forEach(function (k) {
      var v = k === "text" ? Number(p[k]) : p[k];
      o[k] = PREF_VALUES[k].indexOf(v) !== -1 ? v : PREF_DEFAULTS[k];
    });
    return o;
  }
  function readPrefs() {
    try { return cleanPrefs(JSON.parse(localStorage.getItem(PREF_KEY) || "{}")); } catch (e) { return cleanPrefs({}); }
  }
  var prefs = readPrefs();
  function saverActive(p) {
    p = p || prefs;
    if (p.saver === "on") return true;
    if (p.saver === "off") return false;
    var c = navigator.connection || {};
    return c.saveData === true || /^(slow-2g|2g)$/.test(c.effectiveType || "") || navigator.onLine === false;
  }
  function applyPrefs() {
    var d = document.documentElement;
    d.setAttribute("data-text", String(prefs.text));
    d.setAttribute("data-spacing", prefs.spacing);
    d.setAttribute("data-contrast", prefs.contrast);
    d.setAttribute("data-motion", prefs.motion);
    d.setAttribute("data-saver", saverActive() ? "on" : "off");
  }
  function firePrefs() {
    var detail = Object.assign({}, prefs);
    try { window.dispatchEvent(new CustomEvent("gvlv:prefs", { detail: detail })); } catch (e) { /* very old browser */ }
  }
  GV.prefs = {
    key: PREF_KEY,
    defaults: Object.assign({}, PREF_DEFAULTS),
    get: function () { return Object.assign({}, prefs); },
    set: function (part) {
      prefs = cleanPrefs(Object.assign({}, prefs, part || {}));
      try {
        if (GV.prefs.isCustom()) localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
        else localStorage.removeItem(PREF_KEY);
      } catch (e) { /* storage blocked: the choice still applies to this page */ }
      applyPrefs(); firePrefs();
      return GV.prefs.get();
    },
    reset: function () { return GV.prefs.set(PREF_DEFAULTS); },
    isCustom: function () {
      return Object.keys(PREF_DEFAULTS).some(function (k) { return prefs[k] !== PREF_DEFAULTS[k]; });
    },
    saverActive: function () { return saverActive(); },
  };
  // Another tab changed them → follow here too.
  window.addEventListener("storage", function (e) {
    if (e.key !== PREF_KEY && e.key !== null) return;
    prefs = readPrefs(); applyPrefs(); firePrefs();
  });
  // "Automatic" data saver follows the connection while the page is open.
  (function () {
    var onNet = function () {
      if (prefs.saver !== "auto") return;
      var was = document.documentElement.getAttribute("data-saver");
      applyPrefs();
      if (document.documentElement.getAttribute("data-saver") !== was) firePrefs();
    };
    if (navigator.connection && navigator.connection.addEventListener) navigator.connection.addEventListener("change", onNet);
    window.addEventListener("online", onNet);
    window.addEventListener("offline", onNet);
  })();

  /* Reduced motion: the OS setting OR the panel's "Reduce" (html[data-motion="reduce"]). Use it for
     scrollIntoView({behavior}) and anything else that moves on its own. */
  GV.reducedMotion = function () {
    if (document.documentElement.getAttribute("data-motion") === "reduce") return true;
    try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; }
  };

  /* ---------------- Read aloud (GV.tts) ----------------
     Reads the page's <main> with the browser's speech synthesis, in the page language (a voice
     whose lang matches "en" / "es", else the default voice with utterance.lang set; a block with
     its own lang="…" is read in that language). The text is split into chunks — one per heading,
     paragraph, list item, table cell … (long ones at sentence ends), so long pages work and
     Chrome's ~15 s utterance limit never cuts one short. Skipped: nav, footer, buttons and form
     fields, aria-hidden and visually hidden text, anything not shown (closed <details>, hidden
     tabs), [data-tts-skip]. The chunk being read gets .tts-current (areas/access.css) and is
     scrolled into view; the status names the section (the last heading read).
     Pause = cancel and remember the chunk; Resume starts that chunk again (speechSynthesis.pause()
     is unreliable on Android). Leaving the page stops it.
       GV.tts.supported · .play() · .pause() · .stop() · .toggle() · .state() · .on(fn) → fn(status)
       status = { supported, state: "idle"|"playing"|"paused", section, index, total, done, empty } */
  GV.tts = (function () {
    var synth = window.speechSynthesis;
    var supported = !!(synth && window.SpeechSynthesisUtterance);
    var BLOCK = "h1,h2,h3,h4,h5,h6,p,li,dt,dd,blockquote,figcaption,caption,td,th,summary,pre";
    var SKIP = "nav,footer,button,select,textarea,input,label,legend,script,style,template,noscript,svg,canvas,iframe,audio,video,dialog:not([open])," +
      "[aria-hidden='true'],[hidden],[inert],.sr-only,[role='button'],[role='tab'],[class*='btn'],.chip,[data-tts-skip],lite-youtube," +
      "details:not([open]) > :not(summary)"; // a closed <details> still has boxes in current Chrome
    // "has a letter or a digit" (not only punctuation or emoji). Built at run time: an older browser
    // without Unicode property escapes would reject a /\p{L}/u literal and stop this whole file.
    var WORDISH = (function () { try { return new RegExp("[\\p{L}\\p{N}]", "u"); } catch (e) { return /[A-Za-z0-9À-ɏ]/; } })();
    var chunks = [], idx = 0, state = "idle", token = 0, done = false, empty = false, cur = null, fns = [];
    var MAX = 240;

    function hiddenEl(el) {
      // visually hidden (sr-only and its responsive variants): a 1px clipped box
      for (var n = el; n && n.nodeType === 1 && n.tagName !== "MAIN"; n = n.parentElement) {
        if (n.offsetWidth <= 1 && n.offsetHeight <= 1 && getComputedStyle(n).position === "absolute") return true;
      }
      return false;
    }
    function blockOf(el, root) {
      var b = el.closest(BLOCK);
      if (b && root.contains(b)) return b;
      for (var n = el; n && n !== root; n = n.parentElement) {
        var d = getComputedStyle(n).display;
        if (d && d.indexOf("inline") !== 0 && d !== "contents") return n;
      }
      return root;
    }
    function split(text) {
      if (text.length <= MAX) return [text];
      var parts = text.match(/[^.!?¿¡…:;]+[.!?…:;]*["»”’)]*\s*/g) || [text], out = [], acc = "";
      parts.forEach(function (s) {
        if ((acc + s).length > MAX && acc) { out.push(acc.trim()); acc = ""; }
        while (s.length > MAX) { var cut = s.lastIndexOf(" ", MAX); if (cut < 40) cut = MAX; out.push(s.slice(0, cut).trim()); s = s.slice(cut); }
        acc += s;
      });
      if (acc.trim()) out.push(acc.trim());
      return out;
    }
    function collect() {
      var root = document.querySelector("main") || document.body, list = [], vis = new WeakMap();
      var pageLang = (document.documentElement.lang || "en").slice(0, 2);
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
          if (!/\S/.test(n.nodeValue)) return NodeFilter.FILTER_REJECT;
          var el = n.parentElement;
          if (!el || el.closest(SKIP)) return NodeFilter.FILTER_REJECT;
          var ok = vis.get(el);
          if (ok === undefined) {
            var cs = getComputedStyle(el);
            ok = !!el.getClientRects().length && cs.visibility !== "hidden" && !hiddenEl(el) &&
              // not rendered (inside a closed <details>, display: none, visibility: hidden …)
              !(el.checkVisibility && !el.checkVisibility({ visibilityProperty: true }));
            vis.set(el, ok);
          }
          return ok ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        },
      });
      var n, last = null;
      while ((n = walker.nextNode())) {
        var b = blockOf(n.parentElement, root);
        // the language of this piece of text: a Spanish title inside an English sentence is read
        // by the Spanish voice (the block is split where the language changes)
        var l = n.parentElement.closest("[lang]");
        var tl = ((l && l.getAttribute("lang")) || pageLang).slice(0, 2).toLowerCase();
        // (the next piece of the same block: a space between them unless one is there already, or the
        // piece starts with punctuation — "in print" + ", here" is "in print, here", not "print , here")
        if (last && last.el === b && last.lang === tl) {
          last.text += (/\s$/.test(last.text) || /^[\s,.;:!?%)\]}»”’]/.test(n.nodeValue) ? "" : " ") + n.nodeValue;
          continue;
        }
        // a new block (or the same block again after a nested one, or another language): a new
        // chunk, in document order
        last = { el: b, text: n.nodeValue, lang: tl, heading: /^H[1-6]$/.test(b.tagName) };
        list.push(last);
      }
      var out = [], section = "";
      list.forEach(function (c) {
        var t = c.text.replace(/\s+/g, " ").trim();
        if (!t || !WORDISH.test(t)) return;
        if (c.heading) section = t;
        split(t).forEach(function (piece) { out.push({ el: c.el, text: piece, lang: c.lang, section: section }); });
      });
      return out;
    }
    function voiceFor(lang) {
      var vs = [];
      try { vs = synth.getVoices() || []; } catch (e) { return null; }
      var want = lang === "es" ? ["es-us", "es-mx", "es-419", "es-es", "es"] : ["en-us", "en-gb", "en"];
      var norm = function (v) { return String(v.lang || "").replace("_", "-").toLowerCase(); };
      for (var i = 0; i < want.length; i++) {
        var hits = vs.filter(function (v) { var l = norm(v); return l === want[i] || (want[i].length === 2 && l.indexOf(want[i] + "-") === 0); });
        if (hits.length) return hits.filter(function (v) { return v.localService; })[0] || hits[0];
      }
      return null;
    }
    function status() {
      var c = chunks[idx];
      return { supported: supported, state: state, section: c ? c.section : "", index: idx, total: chunks.length, done: done, empty: empty };
    }
    function emit() { var s = status(); fns.forEach(function (f) { try { f(s); } catch (e) {} }); }
    function mark(c) {
      if (cur && cur !== (c && c.el)) cur.classList.remove("tts-current");
      cur = c ? c.el : null;
      if (!cur) return;
      cur.classList.add("tts-current");
      // (the bottom edge: above the read-aloud bar, the language banner and the player — scroll-padding-bottom)
      var rcs = getComputedStyle(document.documentElement), r = cur.getBoundingClientRect(), top = parseFloat(rcs.scrollPaddingTop) || 0;
      if (r.top < top || r.bottom > window.innerHeight - (parseFloat(rcs.scrollPaddingBottom) || 0)) {
        try { cur.scrollIntoView({ behavior: GV.reducedMotion() ? "auto" : "smooth", block: r.height > window.innerHeight * 0.6 ? "start" : "center" }); } catch (e) { cur.scrollIntoView(); }
      }
    }
    function speakAt(i) {
      var my = ++token;
      if (i >= chunks.length) { state = "idle"; done = true; idx = 0; mark(null); emit(); return; }
      idx = i; mark(chunks[i]); emit();
      var c = chunks[i], u = new SpeechSynthesisUtterance(c.text), v = voiceFor(c.lang);
      u.lang = c.lang === "es" ? "es-US" : c.lang === "en" ? "en-US" : c.lang;
      if (v) u.voice = v;
      u.onend = function () { if (my === token && state === "playing") speakAt(i + 1); };
      u.onerror = function (e) {
        if (my !== token || state !== "playing") return;
        if (e && (e.error === "interrupted" || e.error === "canceled")) return;
        speakAt(i + 1);
      };
      try { if (synth.paused) synth.resume(); synth.speak(u); } catch (e) { stop(); }
    }
    function play() {
      if (!supported) return;
      done = false; empty = false;
      if (state === "playing") return;
      if (state !== "paused") { chunks = collect(); idx = 0; }
      if (!chunks.length) { empty = true; state = "idle"; emit(); return; }
      state = "playing";
      token++;
      try { synth.cancel(); } catch (e) {}
      var at = idx;
      setTimeout(function () { if (state === "playing") speakAt(at); }, 60); // Chrome drops a speak() right after cancel()
      emit();
    }
    function pause() {
      if (state !== "playing") return;
      state = "paused"; token++;
      try { synth.cancel(); } catch (e) {}
      emit();
    }
    function stop() {
      var was = state;
      state = "idle"; token++; idx = 0; done = false; chunks = [];
      try { if (supported && (was !== "idle" || synth.speaking)) synth.cancel(); } catch (e) {}
      mark(null); emit();
    }
    if (supported) {
      window.addEventListener("pagehide", stop);
      window.addEventListener("beforeunload", stop);
      try { synth.getVoices(); } catch (e) {} // starts loading the voice list (Chrome loads it lazily)
      if (synth.addEventListener) synth.addEventListener("voiceschanged", function () {});
    }
    return {
      supported: supported,
      play: play, pause: pause, stop: stop,
      toggle: function () { if (state === "playing") pause(); else play(); },
      state: function () { return status(); },
      chunks: function () { return chunks.map(function (c) { return { text: c.text, lang: c.lang, section: c.section }; }); },
      collect: function () { return collect().map(function (c) { return { text: c.text, lang: c.lang, section: c.section }; }); },
      on: function (f) { if (typeof f === "function") fns.push(f); },
    };
  })();

  /* ---------------- Alpine components ---------------- */
  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    /* The "Aa" panel's shared state: open, who opened it (focus goes back there), whether any
       setting differs from the defaults (the dot on the "Aa" buttons) and whether the page is
       being read aloud. */
    Alpine.store("gvlv", {
      open: false, trigger: null, custom: GV.prefs.isCustom(), speaking: false,
      show: function (el) { this.trigger = el || null; this.open = true; },
      toggle: function (el) { if (this.open) this.open = false; else this.show(el); },
    });
    window.addEventListener("gvlv:prefs", function () { Alpine.store("gvlv").custom = GV.prefs.isCustom(); });
    GV.tts.on(function (s) { Alpine.store("gvlv").speaking = s.state !== "idle"; });

    /* The panel (partials/comfort-panel.njk): a non-modal dialog. Opening moves focus to the
       first control (the Pause button while reading aloud); Escape closes it and puts focus back
       on the button that opened it (or the Menu button / the visible "Aa" button); a click or
       Tab outside closes it without moving focus. */
    Alpine.data("gvlvPanel", function () {
      return {
        p: GV.prefs.get(),
        saverOn: GV.prefs.saverActive(),
        tts: GV.tts.state(),
        init: function () {
          var self = this;
          // Was the last press a pointer on an "Aa" button? (Clicking one moves focus onto it: that
          // must not close the panel before its own click toggles it. A keyboard Tab onto one must.)
          document.addEventListener("pointerdown", function (e) {
            self._ptrTrigger = !!(e.target && e.target.closest && e.target.closest('[aria-controls="gvlv-panel"]'));
          }, true);
          document.addEventListener("keydown", function () { self._ptrTrigger = false; }, true);
          window.addEventListener("gvlv:prefs", function (e) { self.p = Object.assign({}, e.detail); self.saverOn = GV.prefs.saverActive(); });
          GV.tts.on(function (s) { self.tts = s; });
          this.$watch("$store.gvlv.open", function (v) {
            if (!v) return;
            self.p = GV.prefs.get(); self.saverOn = GV.prefs.saverActive();
            self.$nextTick(function () {
              var first = self.tts.state !== "idle" ? self.$refs.ttsMain : self.$root.querySelector('input[name="gvlv-text"]:checked');
              (first || self.$root).focus({ preventScroll: true });
            });
          });
        },
        set: function (k, v) { var o = {}; o[k] = v; GV.prefs.set(o); },
        reset: function () {
          GV.prefs.reset();
          GV.announce(this.$root.getAttribute("data-reset-done"));
          // Reset is now disabled (nothing left to reset): keep focus in the panel
          var r = this.$root.querySelector('input[name="gvlv-text"][value="100"]');
          if (r) r.focus();
        },
        close: function (refocus) {
          var s = Alpine.store("gvlv"), a = document.activeElement;
          var inside = a && this.$root.contains(a);
          s.open = false;
          if (!(refocus || inside)) return;
          var t = s.trigger;
          if (!t || t.offsetParent === null) {
            t = [].slice.call(document.querySelectorAll('.gvlv-trigger, .site-menu-btn')).filter(function (b) { return b.offsetParent !== null; })[0];
          }
          if (t) t.focus({ preventScroll: true });
        },
        onEscape: function (e) {
          if (!Alpine.store("gvlv").open) return;
          var a = document.activeElement;
          if (a && a.closest && a.closest("#mobile-drawer")) return;
          e.preventDefault();
          this.close(!a || a === document.body || this.$root.contains(a));
        },
        onOutside: function (e) {
          if (!Alpine.store("gvlv").open) return;
          if (e.target && e.target.closest && e.target.closest('[aria-controls="gvlv-panel"]')) return;
          this.close(false);
        },
        onFocusOut: function (e) {
          var to = e.relatedTarget;
          if (!Alpine.store("gvlv").open || !to || this.$root.contains(to)) return;
          // Focus left the panel: close it, so it never hides the focused control (WCAG 2.4.11) — even
          // when that control is another "Aa" button reached with Tab (the Accessibility page has one
          // right after the panel). A pointer press on an "Aa" button is left to that button's click.
          if (this._ptrTrigger && to.closest && to.closest('[aria-controls="gvlv-panel"]')) return;
          this.close(false);
        },
        // Read aloud. On a phone the panel covers the page, so once reading starts it closes (focus back
        // on the button that opened it) and the read-aloud bar at the bottom (gvlvTtsBar) takes over.
        ttsToggle: function () {
          var starting = this.tts.state !== "playing";
          GV.tts.toggle();
          var phone = false;
          try { phone = window.matchMedia("(max-width: 39.99rem)").matches; } catch (e) {}
          if (starting && phone && GV.tts.state().state === "playing") this.close(true);
        },
        ttsStop: function () { GV.tts.stop(); var m = this.$refs.ttsMain; if (m) m.focus(); },
        statusText: function () {
          var s = this.tts, el = this.$root, sec = s.section || el.getAttribute("data-read-top");
          if (s.empty) return el.getAttribute("data-read-empty");
          if (s.state === "playing") return el.getAttribute("data-read-now").replace("{section}", sec);
          if (s.state === "paused") return el.getAttribute("data-read-paused").replace("{section}", sec);
          if (s.done) return el.getAttribute("data-read-done");
          return "";
        },
      };
    });

    /* The read-aloud bar (partials/comfort-panel.njk, areas/access.css .tts-bar): while the page is
       being read (or paused) and the panel is closed, a small bar at the bottom says what is being
       read and has Pause / Resume and Stop — so reading is never left running with no control in
       sight (phones close the panel when reading starts). While it is shown <html> gets
       .tts-bar-on and --tts-h (its height): the page keeps that room at its end and keyboard focus
       is never left under it (scroll-padding-bottom). */
    Alpine.data("gvlvTtsBar", function () {
      return {
        tts: GV.tts.state(),
        init: function () {
          var self = this;
          GV.tts.on(function (s) { self.tts = s; });
          if (window.ResizeObserver) new ResizeObserver(function () { self.measure(); }).observe(this.$root);
        },
        visible: function () { return this.tts.state !== "idle" && !Alpine.store("gvlv").open; },
        measure: function () {
          var root = document.documentElement, on = this.visible(), h = on ? Math.ceil(this.$root.getBoundingClientRect().height) : 0;
          root.classList.toggle("tts-bar-on", !!h);
          if (h) root.style.setProperty("--tts-h", h + "px"); else root.style.removeProperty("--tts-h");
        },
        sync: function () { var self = this; this.visible(); this.$nextTick(function () { self.measure(); }); },
        text: function () {
          var el = this.$root, sec = this.tts.section || el.getAttribute("data-read-top");
          return el.getAttribute(this.tts.state === "paused" ? "data-read-paused" : "data-read-now").replace("{section}", sec);
        },
        toggle: function () { GV.tts.toggle(); },
        stop: function () {
          GV.tts.stop();
          // the bar goes away with the button that had focus: put it on the visible "Aa" button
          var t = [].slice.call(document.querySelectorAll(".gvlv-trigger, .site-menu-btn")).filter(function (b) { return b.offsetParent !== null; })[0];
          if (t) t.focus({ preventScroll: true });
        },
      };
    });

    /* Header: transparent (white text) over the dark page hero, solid once scrolled.
       A page without a hero (no ui.pageHero) — or <body data-header="solid"> — gets the
       solid header from the start, so its white text never sits on the light page. */
    Alpine.data("siteHeader", function () {
      return {
        solid: false, drawer: false, comfortNext: false, theme: document.documentElement.getAttribute("data-theme") || "light",
        force: document.body.getAttribute("data-header") === "solid" || !document.querySelector("[data-gv-hero], .page-hero, #homeHero"),
        init: function () {
          var self = this;
          var onScroll = function () { self.solid = self.force || window.scrollY > 24; };
          onScroll();
          window.addEventListener("scroll", onScroll, { passive: true });
          /* The phone/tablet menu is a modal dialog (it is teleported to <body>, see header.njk):
             while it is open the page can't scroll, everything behind it is inert (no Tab, no
             screen-reader browsing), and focus starts on its Close button. On close, focus goes
             back to the Menu button. */
          var inerted = [];
          var setInert = function (on) {
            if (!on) { inerted.forEach(function (el) { el.inert = false; }); inerted = []; return; }
            var dlg = document.getElementById("mobile-drawer");
            Array.prototype.forEach.call(document.body.children, function (el) {
              if (el === dlg || (dlg && el.contains(dlg)) || el.tagName === "SCRIPT" || el.tagName === "TEMPLATE" || el.hasAttribute("data-gv-live") || el.inert) return;
              el.inert = true; inerted.push(el);
            });
          };
          this.$watch("drawer", function (v) {
            document.documentElement.style.overflow = v ? "hidden" : "";
            setInert(v);
            if (v) {
              self.$nextTick(function () { var c = self.$refs.drawerClose; if (c) c.focus({ preventScroll: true }); });
            } else {
              var b = self.$refs.menuBtn, a = document.activeElement;
              // Only when focus would otherwise be lost (it was in the drawer, now hidden) and the
              // Menu button is still shown (below xl).
              if (b && b.offsetParent !== null && (!a || a === document.body || a.closest("#mobile-drawer"))) b.focus({ preventScroll: true });
              if (self.comfortNext) { self.comfortNext = false; window.Alpine.store("gvlv").show(b); }
            }
          });
          /* The drawer is hidden by CSS once the desktop nav shows (xl, 1280px — later with larger
             text: areas/access.css). If the window grows past that while it is open (tablet
             rotation, a widened window) or the text gets smaller, close it so the page isn't left
             scroll-locked and inert behind a menu nobody can see. The Menu button is shown exactly
             while the drawer can be, so its visibility is the test. */
          var onWide = function () {
            var b = self.$refs.menuBtn;
            if (self.drawer && b && b.offsetParent === null) self.drawer = false;
          };
          window.addEventListener("resize", onWide, { passive: true });
          window.addEventListener("gvlv:prefs", function () { requestAnimationFrame(onWide); });
        },
        /* The drawer's "Aa" button: close the drawer, then open the settings panel; Escape in the
           panel brings focus back to the Menu button. */
        openComfort: function () {
          this.comfortNext = true; // the drawer watcher opens it once the page behind is no longer inert
          this.drawer = false;
        },
        toggleTheme: function () {
          this.theme = this.theme === "dark" ? "light" : "dark";
          document.documentElement.setAttribute("data-theme", this.theme);
          // "gvlv-theme": our own key (mkp715.github.io is one origin shared by every Pages project, and
          // a bare "theme" is a common key there). base.njk reads it before the first paint.
          try { localStorage.setItem("gvlv-theme", this.theme); } catch (e) {}
        },
      };
    });

    /* Desktop Committee / Service dropdowns (header.njk). Escape closes the menu and, when focus
       was inside it, puts focus back on its button; tabbing out of it closes it. A focus loss
       with no new target (a click on a blank spot, or Safari not focusing a clicked link) is
       left to @click.outside, so a mouse click on a menu link is never swallowed. */
    Alpine.data("navMenu", function () {
      return {
        open: false,
        onEscape: function () {
          if (!this.open) return;
          var inside = this.$root.contains(document.activeElement);
          this.open = false;
          if (inside && this.$refs.btn) this.$refs.btn.focus();
        },
        onFocusOut: function (e) {
          if (this.open && e.relatedTarget && !this.$root.contains(e.relatedTarget)) this.open = false;
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
    // The status live region exists from page load, so its first message is announced.
    liveRegion();
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
