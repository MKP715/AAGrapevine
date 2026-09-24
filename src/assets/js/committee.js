/* Committee pages (Meeting, Events, Documents, Photos, Announcements).
   Loaded with `defer` after app.js and BEFORE Alpine, so the Alpine
   components below are registered in time (alpine:init).
   No build step, no dependencies besides window.GV (app.js), Alpine and —
   on /photos/ only — GLightbox (self-hosted, MIT). */
(function () {
  "use strict";
  var GV = window.GV || {};
  var LANG = GV.lang || document.documentElement.lang || "en";
  var LOCALE = LANG === "es" ? "es-US" : "en-US";
  var TZ = (window.SITE && window.SITE.tz) || "America/Chicago";
  var REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var CM = (window.CM = window.CM || {});

  document.documentElement.classList.add("cm-js");

  /* ---------------- formatting helpers ---------------- */
  function fmt(d, opts, tz) {
    try { return new Intl.DateTimeFormat(LOCALE, Object.assign({ timeZone: tz || TZ }, opts)).format(d); } catch (e) { return ""; }
  }
  function range(a, b, opts, tz) {
    try {
      var f = new Intl.DateTimeFormat(LOCALE, Object.assign({ timeZone: tz || TZ }, opts));
      return f.formatRange ? f.formatRange(a, b) : f.format(a) + " – " + f.format(b);
    } catch (e) { return ""; }
  }
  function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function utcStamp(d) { return new Date(d).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, ""); }
  function ymdCompact(s) { return String(s).slice(0, 10).replace(/-/g, ""); }
  function ymdAdd(s, n) {
    var p = String(s).slice(0, 10).split("-").map(Number);
    return new Date(Date.UTC(p[0], p[1] - 1, p[2] + n)).toISOString().slice(0, 10);
  }
  function chicagoYmd(d) {
    try { return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(d); } catch (e) { return new Date(d).toISOString().slice(0, 10); }
  }
  // Live-region count: data-count-label="Showing {n} events" + data-count-label-one="Showing 1 event"
  function countLabels(root) {
    var many = root.getAttribute("data-count-label") || "{n}";
    return { many: many, one: root.getAttribute("data-count-label-one") || many };
  }
  function countText(labels, n) { return (n === 1 ? labels.one : labels.many).replace("{n}", n); }

  /* ---------------- calendar links + .ics download ---------------- */
  // ev: {title, description, location, url, start, end, allDay, uid, filename}
  CM.calLinks = function (ev) {
    var q = function (o) { return Object.keys(o).map(function (k) { return k + "=" + encodeURIComponent(o[k]); }).join("&"); };
    var gd, os, oe;
    if (ev.allDay) {
      gd = ymdCompact(ev.start) + "/" + ymdCompact(ymdAdd(ev.end || ev.start, 1));
      os = String(ev.start).slice(0, 10); oe = ymdAdd(ev.end || ev.start, 1);
    } else {
      gd = utcStamp(ev.start) + "/" + utcStamp(ev.end || ev.start);
      os = new Date(ev.start).toISOString().replace(/\.\d{3}/, ""); oe = new Date(ev.end || ev.start).toISOString().replace(/\.\d{3}/, "");
    }
    return {
      gcal: "https://calendar.google.com/calendar/render?" + q({ action: "TEMPLATE", text: ev.title || "", dates: gd, details: ev.description || "", location: ev.location || "", ctz: TZ }),
      outlook: "https://outlook.live.com/calendar/0/action/compose?" + q({ rru: "addevent", subject: ev.title || "", startdt: os, enddt: oe, allday: ev.allDay ? "true" : "false", body: ev.description || "", location: ev.location || "" }),
    };
  };

  // RFC 5545 TEXT escaping + 75-octet line folding (UTF-8 safe)
  function icsEsc(s) { return String(s == null ? "" : s).replace(/\r\n?/g, "\n").replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n"); }
  function icsFold(line) {
    var enc = window.TextEncoder ? new TextEncoder() : null;
    var bytes = function (s) { return enc ? enc.encode(s).length : unescape(encodeURIComponent(s)).length; };
    if (bytes(line) <= 75) return line;
    var out = [], cur = "", n = 0, limit = 75;
    Array.from(line).forEach(function (ch) {
      var b = bytes(ch);
      if (n + b > limit) { out.push(cur); cur = ""; n = 0; limit = 74; }
      cur += ch; n += b;
    });
    if (cur) out.push(cur);
    return out.join("\r\n ");
  }

  CM.downloadIcs = function (ev) {
    var L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//NETA 65 Grapevine La Vina Committee//Event//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
      "UID:" + (ev.uid || utcStamp(ev.start) + "@neta65-gvlv"), "DTSTAMP:" + utcStamp(new Date())];
    if (ev.allDay) {
      L.push("DTSTART;VALUE=DATE:" + ymdCompact(ev.start), "DTEND;VALUE=DATE:" + ymdCompact(ymdAdd(ev.end || ev.start, 1)), "TRANSP:TRANSPARENT");
    } else {
      L.push("DTSTART:" + utcStamp(ev.start), "DTEND:" + utcStamp(ev.end || ev.start));
    }
    L.push("SUMMARY:" + icsEsc(ev.title));
    if (ev.description) L.push("DESCRIPTION:" + icsEsc(ev.description));
    if (ev.location) L.push("LOCATION:" + icsEsc(ev.location));
    if (ev.url) L.push("URL:" + ev.url);
    L.push("END:VEVENT", "END:VCALENDAR");
    var text = L.map(icsFold).join("\r\n") + "\r\n";
    var blob = new Blob([text], { type: "text/calendar;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (ev.filename || "event") + ".ics";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
  };

  /* ---------------- Alpine components ---------------- */
  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    /* Next committee meeting: live countdown, "live" state (15 min before → end),
       labels in Central time + the visitor's own time, calendar links.
       x-data="cmMeeting({rule, start, end, title, description, location, url, i18n})" */
    Alpine.data("cmMeeting", function (cfg) {
      cfg = cfg || {};
      var i18n = cfg.i18n || {};
      return {
        phase: "upcoming", d: 0, h: 0, m: 0, s: 0,
        dateLabel: "", timeLabel: "", whenLabel: "", localLabel: "", joinText: i18n.join || "",
        tile: { mon: "", day: "", wd: "" }, gcal: "", outlook: "",
        _start: null, _end: null, _ymd: "",
        init: function () {
          this.compute();
          this.tick();
          var self = this;
          this._t = setInterval(function () { self.tick(); }, 1000);
        },
        destroy: function () { clearInterval(this._t); },
        compute: function () {
          var nx = null;
          try { if (cfg.rule && GV.nextMeeting) nx = GV.nextMeeting(cfg.rule); } catch (e) { nx = null; }
          var start = nx ? nx.start : new Date(cfg.start), end = nx ? nx.end : new Date(cfg.end || cfg.start);
          if (isNaN(start)) return;
          this._start = start; this._end = end; this._ymd = nx ? nx.ymd : chicagoYmd(start);
          this.dateLabel = cap(fmt(start, { weekday: "long", month: "long", day: "numeric", year: "numeric" }));
          this.timeLabel = range(start, end, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
          // One line for the hero: "Wednesday, October 21 · 7:00 PM CDT" (same as the build's whenLabel)
          this.whenLabel = cap(fmt(start, { weekday: "long", month: "long", day: "numeric" })) + " · " + fmt(start, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
          this.tile = {
            mon: fmt(start, { month: "short" }).replace(/\.$/, ""),
            day: fmt(start, { day: "numeric" }),
            wd: fmt(start, { weekday: "short" }).replace(/\.$/, ""),
          };
          // Visitor in another time zone? Show "Your time: …" too.
          this.localLabel = "";
          try {
            var local = Intl.DateTimeFormat().resolvedOptions().timeZone;
            var probe = { hour: "numeric", minute: "numeric", day: "numeric" };
            if (local && fmt(start, probe, local) !== fmt(start, probe, TZ)) {
              var lr = range(start, end, { weekday: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short" }, local);
              this.localLabel = String(i18n.yourTime || "{time}").replace("{time}", lr);
            }
          } catch (e) {}
          var links = CM.calLinks({ title: cfg.title, description: cfg.description, location: cfg.location, start: start, end: end });
          this.gcal = links.gcal; this.outlook = links.outlook;
        },
        tick: function () {
          if (!this._start) return;
          var now = Date.now();
          if (this._end && now >= this._end.getTime()) this.compute(); // meeting over → roll to the next one
          var st = this._start.getTime(), diff = st - now;
          this.phase = now >= st ? "live" : diff <= 15 * 60000 ? "soon" : "upcoming";
          this.joinText = this.phase === "live" ? i18n.live : this.phase === "soon" ? i18n.soon : i18n.join;
          diff = Math.max(0, diff);
          this.d = Math.floor(diff / 864e5); this.h = Math.floor((diff % 864e5) / 36e5);
          this.m = Math.floor((diff % 36e5) / 6e4); this.s = Math.floor((diff % 6e4) / 1e3);
        },
        pad: function (n) { return String(n).padStart(2, "0"); },
        downloadIcs: function () {
          if (!this._start) return;
          CM.downloadIcs({
            uid: "ev-committee-" + this._ymd + (LANG !== "en" ? "-" + LANG : "") + "@neta65-gvlv",
            title: cfg.title, description: cfg.description, location: cfg.location, url: cfg.url,
            start: this._start, end: this._end, allDay: false, filename: "committee-meeting-" + this._ymd,
          });
          closeMenus();
        },
      };
    });

    /* /events/ filter chips + "show every monthly meeting" toggle */
    Alpine.data("cmEvents", function () {
      return {
        filter: "all", showAll: false, labels: { many: "{n}", one: "{n}" },
        init: function () {
          this.labels = countLabels(this.$root);
          try {
            var p = new URLSearchParams(location.search).get("filter");
            if (p && ["all", "committee", "neta", "calendar"].indexOf(p) !== -1) this.filter = p;
          } catch (e) {}
          // Deep link to a (hidden by default) committee meeting → reveal it;
          // to a past event → open the "Past events" list.
          var id = "";
          try { id = location.hash ? decodeURIComponent(location.hash.slice(1)) : ""; } catch (e) {}
          var el = id && document.getElementById(id);
          if (el && el.getAttribute("data-committee") === "1") {
            this.showAll = true;
            this.$nextTick(function () { el.scrollIntoView({ block: "start" }); });
          } else if (el && el.closest && el.closest("details[data-cm-past]")) {
            el.closest("details[data-cm-past]").open = true;
            el.classList.add("cm-target");
            this.$nextTick(function () { el.scrollIntoView({ block: "start" }); });
          }
        },
        set: function (f) { this.filter = f; },
        show: function (el) {
          if (el.hasAttribute("data-cm-expired")) return false;
          var c = el.getAttribute("data-committee") === "1";
          if (this.filter === "all") return !c || this.showAll;
          return el.getAttribute("data-group") === this.filter;
        },
        monthVisible: function (el) {
          var self = this;
          return Array.prototype.some.call(el.querySelectorAll("li[data-group]"), function (li) { return self.show(li); });
        },
        get visibleCount() {
          var self = this;
          return Array.prototype.filter.call(this.$root.querySelectorAll("li[data-group]"), function (li) { return self.show(li); }).length;
        },
        countText: function () { return countText(this.labels, this.visibleCount); },
      };
    });

    /* /photos/ album: "Show all" reveals the photos the CSS hides (committee.css, photo grid)
       and moves keyboard focus to the first photo that was hidden — the button disappears,
       so focus would otherwise be lost. x-data="cmAlbum()" on the album <section>. */
    Alpine.data("cmAlbum", function () {
      return {
        all: false,
        showAll: function () {
          var first = null;
          var items = this.$root.querySelectorAll(".cm-photo-grid > li");
          for (var i = 0; i < items.length; i++) {
            if (getComputedStyle(items[i]).display === "none") { first = items[i]; break; }
          }
          this.all = true;
          this.$nextTick(function () {
            var a = first && first.querySelector("a");
            if (a) a.focus();
          });
        },
      };
    });

    /* /documents/ category toggles + quick search */
    Alpine.data("cmDocs", function () {
      return {
        tab: "all", q: "", labels: { many: "{n}", one: "{n}" },
        init: function () {
          this.labels = countLabels(this.$root);
          var m = location.hash.match(/^#docs-(.+)$/);
          if (m && document.getElementById("docs-" + m[1])) this.tab = m[1];
        },
        set: function (k) {
          this.tab = k;
          try { history.replaceState(null, "", k === "all" ? location.pathname + location.search : "#docs-" + k); } catch (e) {}
        },
        terms: function () { return this.q.toLowerCase().trim().split(/\s+/).filter(Boolean); },
        match: function (el) {
          var t = this.terms();
          if (!t.length) return true;
          var txt = el.getAttribute("data-text") || "";
          for (var i = 0; i < t.length; i++) if (txt.indexOf(t[i]) === -1) return false;
          return true;
        },
        sectionVisible: function (key, count, el) {
          if (this.tab !== "all") return this.tab === key;
          if (!count) return false;
          if (!this.q) return true;
          var self = this;
          return Array.prototype.some.call(el.querySelectorAll("li[data-text]"), function (li) { return self.match(li); });
        },
        get visibleCount() {
          var self = this, tab = this.tab;
          return Array.prototype.filter.call(this.$root.querySelectorAll("li[data-text]"), function (li) {
            var sec = li.closest("section");
            if (tab !== "all" && sec && sec.id !== "docs-" + tab) return false;
            return self.match(li);
          }).length;
        },
        countText: function () { return countText(this.labels, this.visibleCount); },
      };
    });
  });

  /* ---------------- dropdown menus (<details class="cm-menu">) ---------------- */
  function closeMenus(except) {
    document.querySelectorAll("details.cm-menu[open]").forEach(function (d) { if (d !== except) d.removeAttribute("open"); });
  }
  document.addEventListener("click", function (e) {
    var menu = e.target.closest && e.target.closest("details.cm-menu");
    closeMenus(menu);
    if (menu && e.target.closest(".cm-menu-panel a")) setTimeout(function () { menu.removeAttribute("open"); }, 0);
  });
  // Keep an opened menu on the screen: it opens under its button, aligned left; if that
  // would run past the right edge (a button far right on a phone), align it right, and if
  // it then starts off the left edge, pin it 8px from the edge. "toggle" does not bubble,
  // so it is caught in the capture phase.
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d || !d.matches || !d.matches("details.cm-menu") || !d.open) return;
    var panel = d.querySelector(".cm-menu-panel");
    if (!panel) return;
    panel.style.left = ""; panel.style.right = "";
    var M = 8, vw = document.documentElement.clientWidth, r = panel.getBoundingClientRect();
    if (r.right > vw - M) { panel.style.left = "auto"; panel.style.right = "0"; r = panel.getBoundingClientRect(); }
    if (r.left < M) { panel.style.right = "auto"; panel.style.left = (M - d.getBoundingClientRect().left) + "px"; }
  }, true);
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var open = document.querySelector("details.cm-menu[open]");
    if (open) { open.removeAttribute("open"); var s = open.querySelector("summary"); if (s) s.focus(); }
  });

  /* ---------------- per-event .ics download ----------------
     Bound on each button (NOT delegated to document): Chromium silently drops
     the synthetic <a download>.click() when it is fired from a document-level
     click listener, so delegation would break the download. */
  function bindIcsButtons() {
    document.querySelectorAll("[data-cm-ics]").forEach(function (b) {
      if (b._cmBound) return;
      b._cmBound = true;
      b.addEventListener("click", function (e) {
        e.preventDefault();
        try { CM.downloadIcs(JSON.parse(b.getAttribute("data-cm-ics"))); } catch (err) { /* malformed data: ignore */ }
        closeMenus();
      });
    });
  }

  /* ---------------- Drive preview dialog ---------------- */
  var lastTrigger = null;
  function dialogEl() { return document.getElementById("cm-preview"); }
  CM.preview = function (src, title, openUrl, downloadUrl) {
    var dlg = dialogEl();
    if (!dlg || typeof dlg.showModal !== "function" || !src) return false;
    var frame = dlg.querySelector("iframe");
    var h = dlg.querySelector("#cm-preview-title");
    var o = dlg.querySelector("[data-cm-open]");
    var dl = dlg.querySelector("[data-cm-download]");
    if (h) h.textContent = title || h.textContent;
    if (o) { o.href = openUrl || src; }
    if (dl) { if (downloadUrl) { dl.href = downloadUrl; dl.hidden = false; } else { dl.hidden = true; dl.removeAttribute("href"); } }
    dlg.classList.remove("is-loaded");
    frame.setAttribute("title", title || "");
    frame.onload = function () { dlg.classList.add("is-loaded"); };
    frame.src = src;
    dlg.showModal();
    document.documentElement.style.overflow = "hidden";
    return true;
  };
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-cm-preview]");
    if (!t || e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0) return;
    lastTrigger = t;
    if (CM.preview(t.getAttribute("data-cm-preview"), t.getAttribute("data-cm-title"), t.getAttribute("data-cm-open"), t.getAttribute("data-cm-download"))) e.preventDefault();
  });
  function wireDialog() {
    var dlg = dialogEl();
    if (!dlg) return;
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg || (e.target.closest && e.target.closest("[data-cm-close]"))) dlg.close();
    });
    dlg.addEventListener("close", function () {
      var f = dlg.querySelector("iframe");
      if (f) f.src = "about:blank";
      document.documentElement.style.overflow = "";
      if (lastTrigger && document.contains(lastTrigger)) lastTrigger.focus();
    });
  }

  /* ---------------- hide things whose time has passed ----------------
     Pages are rebuilt daily; this keeps them right between builds.
     [data-cm-expire="ISO"] → hidden once that moment passes.
     [data-cm-max="6"] on a list → only the first N non-expired children show. */
  function expire() {
    var now = Date.now();
    document.querySelectorAll("[data-cm-expire]").forEach(function (el) {
      var t = Date.parse(el.getAttribute("data-cm-expire"));
      if (t && t <= now && !el.hasAttribute("data-cm-expired")) { el.setAttribute("data-cm-expired", ""); el.hidden = true; }
    });
    document.querySelectorAll("[data-cm-max]").forEach(function (list) {
      var max = Number(list.getAttribute("data-cm-max")) || 6, i = 0;
      Array.prototype.forEach.call(list.children, function (li) {
        if (li.hasAttribute("data-cm-expired")) return;
        li.classList.toggle("hidden", i >= max);
        li.classList.toggle("is-next", i === 0);
        i++;
      });
    });
  }

  /* ---------------- Grapevine Weekly Open: next date ----------------
     The page is built daily, but the meeting is weekly: roll the date forward
     in the browser, say "Live now" during the meeting, and add the visitor's
     own time when they are outside Central time.
     <p data-cm-weekly="ISO">…<span data-cm-weekly-label data-live="…">…
     <span data-cm-weekly-date>…<span data-cm-weekly-local data-tpl="Your time: {time}" hidden> */
  function weekly() {
    document.querySelectorAll("[data-cm-weekly]").forEach(function (box) {
      var t = Date.parse(box.getAttribute("data-cm-weekly"));
      if (!t) return;
      var now = Date.now(), LIVE = 75 * 60000, WEEK = 7 * 864e5;
      while (t + LIVE < now) t += WEEK;
      var d = new Date(t), live = now >= t;
      var lab = box.querySelector("[data-cm-weekly-label]");
      if (lab) {
        if (!lab.hasAttribute("data-next")) lab.setAttribute("data-next", lab.textContent);
        lab.textContent = live ? (lab.getAttribute("data-live") || lab.getAttribute("data-next")) : lab.getAttribute("data-next");
      }
      var dt = box.querySelector("[data-cm-weekly-date]");
      if (dt) dt.textContent = cap(fmt(d, { weekday: "long", month: "long", day: "numeric" })) + " · " + fmt(d, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
      var loc = box.querySelector("[data-cm-weekly-local]");
      if (loc) {
        try {
          var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
          var probe = { hour: "numeric", minute: "numeric", day: "numeric" };
          if (tz && fmt(d, probe, tz) !== fmt(d, probe, TZ)) {
            loc.textContent = String(loc.getAttribute("data-tpl") || "{time}").replace("{time}", fmt(d, { weekday: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short" }, tz));
            loc.hidden = false;
          } else loc.hidden = true;
        } catch (e) { loc.hidden = true; }
      }
    });
  }

  /* ---------------- photo lightbox (GLightbox) ---------------- */
  function initLightbox() {
    if (typeof window.GLightbox !== "function" || !document.querySelector(".glightbox-cm")) return;
    var lab = document.getElementById("cm-lb-i18n");
    var L = function (k, d) { return (lab && lab.getAttribute("data-" + k)) || d; };
    var html = '<div id="glightbox-body" class="glightbox-container cm-lightbox" tabindex="-1" role="dialog" aria-modal="true" aria-label="' + L("label", "Photos") + '">' +
      '<div class="gloader visible"></div><div class="goverlay"></div><div class="gcontainer">' +
      '<div id="glightbox-slider" class="gslider"></div>' +
      '<button class="gclose gbtn" aria-label="' + L("close", "Close") + '" data-taborder="3">{closeSVG}</button>' +
      '<button class="gprev gbtn" aria-label="' + L("prev", "Previous") + '" data-taborder="2">{prevSVG}</button>' +
      '<button class="gnext gbtn" aria-label="' + L("next", "Next") + '" data-taborder="1">{nextSVG}</button>' +
      "</div></div>";
    var lb = window.GLightbox({
      selector: ".glightbox-cm",
      touchNavigation: true,
      keyboardNavigation: true,
      loop: true,
      zoomable: true,
      draggable: true,
      closeOnOutsideClick: true,
      descPosition: "bottom",
      moreLength: 0,
      openEffect: REDUCED ? "none" : "zoom",
      closeEffect: REDUCED ? "none" : "zoom",
      slideEffect: REDUCED ? "none" : "slide",
      lightboxHTML: html,
    });
    CM.lightbox = lb;

    // If a full-size Drive image can't load (file not public yet, Google hiccup),
    // GLightbox would spin forever. Fall back to the thumbnail, then to a
    // neutral placeholder, so the slide always finishes loading.
    var thumbs = {};
    document.querySelectorAll(".glightbox-cm").forEach(function (a) {
      var img = a.querySelector("img");
      if (img && img.getAttribute("src")) thumbs[a.href] = img.getAttribute("src");
    });
    var PLACEHOLDER = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600"><rect width="800" height="600" fill="#2a2638"/>' +
      '<g fill="none" stroke="#8a8599" stroke-width="10" stroke-linecap="round" stroke-linejoin="round" transform="translate(340 240)"><rect x="0" y="0" width="120" height="100" rx="12"/><circle cx="38" cy="34" r="12"/><path d="M120 70 88 40 20 100"/></g></svg>');
    document.addEventListener("error", function (e) {
      var img = e.target;
      if (!img || img.tagName !== "IMG" || !img.closest || !img.closest(".gslide")) return;
      var src = img.getAttribute("src") || "";
      var alt = thumbs[src];
      if (alt && !img.hasAttribute("data-cm-fallback")) { img.setAttribute("data-cm-fallback", "thumb"); img.src = alt; }
      else if (img.getAttribute("data-cm-fallback") !== "placeholder") { img.setAttribute("data-cm-fallback", "placeholder"); img.src = PLACEHOLDER; }
    }, true);
    // "Full screen" button: open the album's first photo (bound per button —
    // see bindIcsButtons for why this is not delegated).
    document.querySelectorAll("[data-cm-open-gallery]").forEach(function (b) {
      b.addEventListener("click", function () {
        var first = document.querySelector('.glightbox-cm[data-gallery="' + b.getAttribute("data-cm-open-gallery") + '"]');
        if (first) first.click();
      });
    });
  }

  /* ---------------- committee sub-nav: show "you are here" ----------------
     On phones the pill bar (Meeting · Events · Documents · Photos · Announcements)
     scrolls sideways and the current tab can start off-screen. Center it inside the
     bar. Only the bar's own scrollLeft changes (scrollIntoView would also move the page). */
  function centerSubnav() {
    var nav = document.querySelector(".cm-subnav");
    var cur = nav && nav.querySelector(".is-current");
    if (!cur || nav.scrollWidth <= nav.clientWidth + 1) return;
    var n = nav.getBoundingClientRect(), c = cur.getBoundingClientRect();
    var x = nav.scrollLeft + (c.left - n.left - nav.clientLeft) - (nav.clientWidth - c.width) / 2;
    nav.scrollLeft = Math.max(0, Math.min(x, nav.scrollWidth - nav.clientWidth));
  }

  /* ---------------- boot ---------------- */
  // Deferred script: the DOM is parsed already; Alpine starts right after us.
  expire();
  weekly();
  setInterval(function () { expire(); weekly(); }, 60000);
  function ready() { centerSubnav(); bindIcsButtons(); wireDialog(); initLightbox(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready); else ready();
})();
