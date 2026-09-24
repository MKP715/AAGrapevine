/* Media pages — Listen (podcast), Watch (YouTube), Instagram.
   NETA 65 Grapevine / La Viña.

   Loaded with `defer` BEFORE Alpine (see layouts/base.njk), so everything is
   registered on `alpine:init`. No build step, no dependencies besides Alpine
   (and the vendored lite-youtube element on /watch/).

   Pieces:
   • Alpine.store("audio")  one shared <audio preload="none"> for the page: the
     featured player and the sticky mini-player are two views of it. Remembers
     the position of every episode (localStorage, this device only), playback
     speed, and the last episode for "Continue listening". Media Session API
     metadata + lock-screen / headset controls.
   • podcastPage / videoPage  list components: the server renders the newest 24
     items; the full list comes from /episodes-index.json or /videos-index.json
     (fetched on first filter/search/"Load more", or when the list end nears).
     /listen/?show=wo (&season=2) opens pre-filtered; the address follows the show /
     season chips (history.replaceState), so a filtered view can be shared.
   • miniPlayer, igPage  small helpers.
   • Instagram embeds: resize official embed iframes from their postMessage.

   Page-chrome contract (with the language banner, community area): while the
   sticky mini-player is visible, <html> gets class "has-player" and the CSS
   variable --player-h (its height in px, safe-area padding excluded), so the
   banner can sit above it: bottom: calc(var(--player-h, 0px) + env(safe-area-inset-bottom) + 0.75rem). */
(function () {
  "use strict";

  var GV = window.GV || {};
  var LANG = GV.lang || document.documentElement.lang || "en";
  var PAGE = 24; // keep in sync with MEDIA_SSR in eleventy/filters/media.js
  var RATES = [1, 1.25, 1.5, 2];
  var K_POS = "gv:audio:pos"; // { episodeId: [seconds (-1 = finished), savedAt] }
  var K_LAST = "gv:audio:last"; // last episode played (for "Continue listening")
  var K_RATE = "gv:audio:rate";
  var MAX_POS = 150; // positions remembered per device
  var FETCH_TIMEOUT = 20000; // ms before the full-list download counts as failed

  /* ------------------------------------------------------------ helpers */

  function readJson(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }
  var INITIAL = readJson("media-initial") || {};
  var T = readJson("media-i18n") || {};
  var SHOWS = INITIAL.shows || {};
  var LATEST = INITIAL.latest || {}; // newest episode of each show (show cards: "Play the latest")
  var SHOW_KEYS = Object.keys(SHOWS);
  var PRIMARY = SHOW_KEYS[0] || "";

  function fmt(s, vars) {
    return String(s || "").replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] != null ? vars[k] : m; });
  }
  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode / quota */ } },
    json: function (k, dflt) { try { var v = JSON.parse(this.get(k)); return v == null ? dflt : v; } catch (e) { return dflt; } },
  };
  function pad(n) { return String(n).padStart(2, "0"); }
  /* 1938 → "32:18", 4000 → "1:06:40" */
  function clock(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }
  /* 1938 → "32 min", 4000 → "1 h 07 min" (same as the mediaMinutes filter) */
  function mins(sec) {
    sec = Math.round(Number(sec) || 0);
    if (sec <= 0) return "";
    var m = Math.max(1, Math.round(sec / 60));
    return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + pad(m % 60) + " min";
  }
  /* lower-case, accents removed: "Reunión" → "reunion" */
  function norm(s) {
    s = String(s || "").toLowerCase();
    return s.normalize ? s.normalize("NFD").replace(/[\u0300-\u036f]/g, "") : s;
  }
  function day(d, monthOnly) {
    if (!d) return "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(d)) d += "T12:00:00Z"; // date-only: noon UTC (like the server)
    if (!GV.fmtDate) return String(d).slice(0, monthOnly ? 7 : 10);
    // approximate dates (some old YouTube uploads) show month + year only
    var s = GV.fmtDate(d, monthOnly ? { month: "long", year: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
    return monthOnly && LANG === "es" ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }
  function safeUrl(u) { u = String(u || ""); return /^(https?:\/\/|\/(?!\/))/i.test(u) ? u : ""; }
  /* Site-relative asset path → URL under the site base (/AAGrapevine/ on GitHub Pages).
     Idempotent: a path that already carries the base (the player's cur.art, or a
     "Continue listening" entry saved by an older version with the base added two or
     more times) is stripped back to the bare path first, so the base is added once. */
  function assetUrl(u) {
    u = safeUrl(u);
    if (!u || u.charAt(0) !== "/" || !GV.url) return u;
    var b = String(GV.base || "/").replace(/\/+$/, "");
    if (b) while (u.indexOf(b + "/") === 0) u = u.slice(b.length);
    return GV.url(u);
  }
  function artFor(ep) {
    if (!ep) return "";
    var s = SHOWS[ep.sh];
    return assetUrl(ep.art || ep.i || (s && s.i) || "");
  }
  function showName(ep) { var s = ep && SHOWS[ep.sh]; return (s && s.name) || ""; }
  function searchText(parts) { return norm(parts.filter(Boolean).join(" ")); }
  /* Object.assign would *call* getters; copy property descriptors instead. */
  function mix(target, src) { Object.defineProperties(target, Object.getOwnPropertyDescriptors(src)); return target; }

  /* ------------------------------------------------------------ positions */

  var posMap = null; // non-reactive mirror of K_POS
  function positions() {
    if (!posMap) {
      var o = store.json(K_POS, {});
      posMap = o && typeof o === "object" && !Array.isArray(o) ? o : {};
    }
    return posMap;
  }
  function writePositions() {
    var map = positions(), ids = Object.keys(map);
    if (ids.length > MAX_POS) {
      ids.sort(function (a, b) { return (map[b][1] || 0) - (map[a][1] || 0); });
      ids.slice(MAX_POS).forEach(function (id) { delete map[id]; });
    }
    store.set(K_POS, JSON.stringify(map));
  }

  /* ------------------------------------------------------------ audio element */

  var audio = null; // the one HTMLAudioElement (created on first use)
  var pendingSeek = null; // seconds to jump to once metadata is available
  var lastSave = 0;
  function S() { return window.Alpine.store("audio"); }
  function audioDuration() { return audio && isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0; }

  /* Save the current episode's position (and "last played"). t overrides currentTime. */
  function persist(t) {
    var s = S();
    if (!s || !s.cur) return;
    var cur = s.cur;
    var time = t != null ? Number(t) : audio ? audio.currentTime || 0 : 0;
    var dur = audioDuration() || cur.du || 0;
    var val = null;
    if (dur && time > 0 && dur - time < 20) val = -1; // finished
    else if (time >= 5) val = Math.floor(time);
    if (val !== null) {
      positions()[cur.id] = [val, Date.now()];
      writePositions();
      s.saved[cur.id] = val;
    }
    store.set(K_LAST, JSON.stringify({ id: cur.id, t: cur.t, a: cur.a, art: cur.art, du: cur.du, sh: cur.sh, u: cur.u, at: Date.now() }));
  }

  function ensureAudio() {
    if (audio) return audio;
    audio = document.createElement("audio");
    audio.setAttribute("preload", "none");
    audio.preload = "none";
    audio.id = "gv-audio";
    audio.hidden = true;
    document.body.appendChild(audio);
    var on = function (ev, fn) { audio.addEventListener(ev, fn); };
    on("play", function () { var s = S(); s.playing = true; s.error = false; session("playing"); });
    on("playing", function () { var s = S(); s.loading = false; s.playing = true; });
    on("pause", function () { var s = S(); s.playing = false; s.loading = false; session("paused"); persist(); });
    on("waiting", function () { if (!audio.paused) S().loading = true; });
    on("canplay", function () { S().loading = false; });
    on("loadedmetadata", function () {
      var s = S();
      if (audioDuration()) s.dur = audioDuration();
      if (pendingSeek != null) {
        try { audio.currentTime = Math.min(pendingSeek, Math.max(0, (audioDuration() || Infinity) - 1)); } catch (e) {}
        pendingSeek = null;
      }
    });
    on("durationchange", function () { if (audioDuration()) S().dur = audioDuration(); });
    on("timeupdate", function () {
      var s = S();
      if (!s.scrubbing) s.time = audio.currentTime || 0;
      if (Date.now() - lastSave > 5000) { lastSave = Date.now(); persist(); positionState(); }
    });
    on("ended", function () { var s = S(); s.playing = false; persist(audioDuration() || s.dur); });
    on("error", function () {
      if (!audio.getAttribute("src")) return;
      var s = S(); s.error = true; s.loading = false; s.playing = false;
    });
    setupMediaSession();
    window.addEventListener("pagehide", function () { persist(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) persist(); });
    return audio;
  }

  /* ------------------------------------------------------------ Media Session */

  function session(state) {
    try { if ("mediaSession" in navigator) navigator.mediaSession.playbackState = state; } catch (e) {}
  }
  function setMeta(cur) {
    if (!("mediaSession" in navigator) || !cur || typeof window.MediaMetadata !== "function") return;
    try {
      var art = cur.art ? new URL(cur.art, location.href).href : "";
      navigator.mediaSession.metadata = new window.MediaMetadata({
        title: cur.t || "",
        artist: showName(cur) || "AA Grapevine",
        album: "AA Grapevine",
        artwork: art ? [{ src: art, sizes: "512x512" }] : [],
      });
    } catch (e) {}
  }
  function setupMediaSession() {
    if (!("mediaSession" in navigator)) return;
    var h = function (action, fn) { try { navigator.mediaSession.setActionHandler(action, fn); } catch (e) {} };
    h("play", function () { S().play(); });
    h("pause", function () { S().pause(); });
    h("stop", function () { S().pause(); });
    h("seekbackward", function (d) { S().skip(-((d && d.seekOffset) || 15)); });
    h("seekforward", function (d) { S().skip((d && d.seekOffset) || 30); });
    h("seekto", function (d) {
      if (!d || d.seekTime == null || !audio) return;
      try { audio.currentTime = d.seekTime; } catch (e) {}
      S().time = d.seekTime;
    });
  }
  function positionState() {
    try {
      var d = audioDuration();
      if (d && navigator.mediaSession && navigator.mediaSession.setPositionState) {
        navigator.mediaSession.setPositionState({ duration: d, playbackRate: audio.playbackRate || 1, position: Math.min(audio.currentTime || 0, d) });
      }
    } catch (e) {}
  }

  /* "Continue listening": the last episode, if it was left part-way (and recently). */
  function lastResume() {
    var last = store.json(K_LAST, null);
    if (!last || !last.id || !safeUrl(last.a)) return null;
    var p = positions()[last.id];
    if (!p || !(p[0] > 30)) return null;
    if (last.at && Date.now() - last.at > 60 * 864e5) return null;
    return last;
  }

  /* ------------------------------------------------------------ page chrome
     html.has-player + --player-h while the mini-player card is on screen
     (ResizeObserver: also fires when x-show hides it → size 0 → cleared). */
  function watchPlayerChrome() {
    var card = document.querySelector(".media-mini-card");
    if (!card) return;
    var root = document.documentElement;
    var GAP = 8; // px between the card and the bottom edge (see .media-mini padding)
    function apply() {
      var h = card.getBoundingClientRect().height;
      var on = h > 0 && card.offsetParent !== null;
      root.classList.toggle("has-player", on);
      if (on) root.style.setProperty("--player-h", Math.ceil(h + GAP) + "px");
      else root.style.removeProperty("--player-h");
    }
    if ("ResizeObserver" in window) new ResizeObserver(apply).observe(card);
    else window.addEventListener("resize", apply);
    apply();
  }

  /* ------------------------------------------------------------ Alpine */

  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    /* ===== shared audio player ===== */
    Alpine.store("audio", {
      cur: null, // {id, t, a, art, du, sh, u}
      playing: false,
      loading: false,
      error: false,
      time: 0,
      dur: 0,
      rate: 1,
      scrubbing: false,
      dismissed: false,
      featuredId: null,
      featuredVisible: false,
      saved: {}, // id → seconds (-1 = finished); reactive copy of positions()

      init: function () {
        var map = positions(), sv = {};
        for (var id in map) if (Array.isArray(map[id])) sv[id] = map[id][0];
        this.saved = sv;
        var r = Number(store.get(K_RATE));
        if (RATES.indexOf(r) !== -1) this.rate = r;
      },

      /* the sticky mini-player shows when something is loaded, not dismissed,
         and the featured card isn't already on screen showing that episode */
      get mini() {
        return !!this.cur && !this.dismissed && !(this.featuredVisible && this.cur.id === this.featuredId);
      },
      isCurrent: function (id) { return !!this.cur && !!id && this.cur.id === id; },
      isPlaying: function (id) { return this.isCurrent(id) && this.playing; },
      isDone: function (id) { return this.saved[id] === -1 && !this.isCurrent(id); },
      resumeAt: function (ep) { var v = ep && this.saved[ep.id]; return v > 0 ? v : 0; },
      timeFor: function (ep) { return ep && this.isCurrent(ep.id) ? this.time : this.resumeAt(ep); },
      durFor: function (ep) { return ep && this.isCurrent(ep.id) && this.dur ? this.dur : (ep && ep.du) || 0; },
      pctLive: function (ep) { var d = this.durFor(ep); return d ? Math.min(100, (this.timeFor(ep) / d) * 100) : 0; },
      pct: function (ep) { return ep && this.isDone(ep.id) ? 100 : this.pctLive(ep); },

      /* Load an episode (compact index object) into the player. */
      load: function (ep, autoplay, start) {
        if (!ep || !ep.id || !safeUrl(ep.a)) { this.error = true; return; }
        var a = ensureAudio();
        if (this.cur && this.cur.id !== ep.id) persist();
        this.cur = { id: ep.id, t: ep.t || "", a: safeUrl(ep.a), art: artFor(ep), du: ep.du || 0, sh: ep.sh || "", u: safeUrl(ep.u) };
        this.error = false;
        this.dismissed = false;
        this.playing = false;
        this.dur = ep.du || 0;
        var from = start != null ? Math.max(0, Number(start) || 0) : this.resumeAt(ep);
        this.time = from;
        pendingSeek = from > 0 ? from : null;
        a.src = this.cur.a;
        a.defaultPlaybackRate = this.rate;
        a.playbackRate = this.rate;
        setMeta(this.cur);
        persist(from);
        if (autoplay !== false) this.play();
      },
      play: function () {
        if (!this.cur) return;
        var a = ensureAudio(), self = this;
        if (!a.getAttribute("src")) a.src = this.cur.a;
        this.dismissed = false;
        this.loading = a.readyState < 3;
        var p;
        try { p = a.play(); } catch (e) { self.error = true; self.loading = false; return; }
        if (p && typeof p.catch === "function") {
          p.catch(function (err) {
            self.loading = false;
            if (err && err.name === "AbortError") return; // a newer load() interrupted this one
            self.playing = false;
            if (err && err.name === "NotAllowedError") return; // autoplay policy: wait for a tap
            self.error = true;
          });
        }
      },
      pause: function () { if (audio) audio.pause(); },
      toggle: function () {
        if (!this.cur) return;
        if (!audio || audio.paused) this.play(); else this.pause();
      },
      /* play/pause button of any episode */
      toggleEp: function (ep) {
        if (!ep || !ep.id) return;
        if (!safeUrl(ep.a)) { // no playable file (feed hiccup): open the episode page instead
          if (safeUrl(ep.u)) window.open(safeUrl(ep.u), "_blank", "noopener");
          return;
        }
        if (this.isCurrent(ep.id)) { this.dismissed = false; this.toggle(); }
        else this.load(ep, true);
      },
      skip: function (delta) {
        if (!this.cur) return;
        ensureAudio();
        var max = audioDuration() || this.dur || Infinity;
        var base = audio.readyState > 0 ? audio.currentTime || 0 : pendingSeek != null ? pendingSeek : this.time;
        var t = Math.max(0, Math.min(max - 1, base + delta));
        if (audio.readyState > 0) { try { audio.currentTime = t; } catch (e) {} } else pendingSeek = t;
        this.time = t;
        persist(t);
      },
      skipFor: function (ep, delta) {
        if (!ep || !ep.id) return;
        if (this.isCurrent(ep.id)) this.skip(delta);
        else this.load(ep, true, Math.max(0, this.resumeAt(ep) + delta));
      },
      /* range input: `input` while dragging, `change` when released (also keyboard) */
      scrub: function (ep, v) {
        v = Number(v) || 0;
        if (!ep || !ep.id) return;
        if (!this.isCurrent(ep.id)) this.load(ep, false, v);
        this.scrubbing = true;
        this.time = v;
      },
      seekTo: function (ep, v) {
        v = Number(v) || 0;
        if (!ep || !ep.id) return;
        if (!this.isCurrent(ep.id)) this.load(ep, false, v);
        this.scrubbing = false;
        this.time = v;
        if (audio && audio.readyState > 0) { try { audio.currentTime = v; } catch (e) {} } else pendingSeek = v;
        persist(v);
      },
      cycleRate: function () {
        var i = RATES.indexOf(this.rate);
        this.rate = RATES[(i + 1) % RATES.length];
        if (audio) { audio.defaultPlaybackRate = this.rate; audio.playbackRate = this.rate; }
        store.set(K_RATE, String(this.rate));
      },
      dismiss: function () { this.pause(); this.dismissed = true; },
      retry: function () {
        if (!this.cur) return;
        var a = ensureAudio(), t = this.time;
        this.error = false;
        pendingSeek = t > 0 ? t : null;
        a.src = this.cur.a;
        a.load();
        this.play();
      },
      /* Keyboard shortcuts inside a player: K / space = play-pause, J = −15 s, L = +30 s */
      onKey: function (e, ep) {
        var el = e.target || {}, tag = (el.tagName || "").toLowerCase();
        if (e.altKey || e.ctrlKey || e.metaKey) return;
        if ((tag === "input" && el.type !== "range") || tag === "select" || tag === "textarea") return;
        var k = (e.key || "").toLowerCase();
        if (k === "k" || (k === " " && tag !== "button" && tag !== "a")) { e.preventDefault(); this.toggleEp(ep); }
        else if (k === "j") { e.preventDefault(); this.skipFor(ep, -15); }
        else if (k === "l") { e.preventDefault(); this.skipFor(ep, 30); }
      },
      /* The featured player's controls report whether they're on screen: while
         at least half visible, the mini-player stays hidden for that episode. */
      watchFeatured: function (el, ep) {
        var self = this;
        this.featuredId = ep && ep.id;
        if (!("IntersectionObserver" in window) || !el) return;
        new IntersectionObserver(function (entries) {
          var e = entries[entries.length - 1];
          self.featuredVisible = e.isIntersecting && e.intersectionRatio >= 0.5;
        }, { threshold: [0, 0.5, 1] }).observe(el);
      },
    });

    /* ===== shared list behaviour (episodes + videos) ===== */
    /* prep(item, source) adds the search text; source = the index/initial JSON */
    function listBase(initial, prep) {
      var inflight = null; // fetch promise lives outside Alpine's reactive proxy
      initial.forEach(function (it) { prep(it, INITIAL); });
      return {
        T: T,
        LANG: LANG,
        initial: initial,
        all: initial,
        total: initial.length,
        indexUrl: "",
        ready: false,
        loading: false,
        failed: false,
        q: "",
        shown: PAGE,

        get client() { return this.ready && (this.filtering || this.shown > PAGE); },
        get visible() { return this.filtered.slice(0, this.shown); },
        get hasMore() { return this.client ? this.filtered.length > this.shown : this.total > this.initial.length; },
        get statusText() {
          if (this.client) { var n = this.filtered.length; return fmt(T.showing, { n: Math.min(this.shown, n), total: n }); }
          return fmt(T.showing, { n: this.initial.length, total: this.total });
        },
        setupList: function (watchKeys) {
          var self = this;
          this.indexUrl = this.$el.dataset.index || "";
          this.total = Number(this.$el.dataset.total) || this.initial.length;
          watchKeys.forEach(function (k) {
            self.$watch(k, function () { self.shown = PAGE; if (self.filtering) self.fetchIndex(); });
          });
          // Prefetch the full index when the end of the list comes near.
          var sentinel = this.$refs.more;
          if (sentinel && "IntersectionObserver" in window && this.total > this.initial.length) {
            var io = new IntersectionObserver(function (entries) {
              if (entries.some(function (e) { return e.isIntersecting; })) { io.disconnect(); self.fetchIndex(); }
            }, { rootMargin: "600px 0px" });
            io.observe(sentinel);
          }
        },
        fetchIndex: function () {
          var self = this;
          if (this.ready) return Promise.resolve();
          if (inflight) return inflight;
          if (!this.indexUrl) { this.ready = true; return Promise.resolve(); }
          this.loading = true;
          this.failed = false;
          var url = GV.url ? GV.url(this.indexUrl) : this.indexUrl;
          // A stalled connection must not leave "Loading…" forever: give up after
          // FETCH_TIMEOUT (→ "Couldn't load the full list · Retry", filters keep working).
          var ctrl = typeof window.AbortController === "function" ? new window.AbortController() : null;
          var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, FETCH_TIMEOUT) : 0;
          inflight = fetch(url, ctrl ? { credentials: "same-origin", signal: ctrl.signal } : { credentials: "same-origin" })
            .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function (data) {
              var items = data && Array.isArray(data.items) ? data.items : [];
              if (!items.length) throw new Error("empty index");
              if (data.shows) for (var k in data.shows) SHOWS[k] = data.shows[k];
              if (data.playlists) self.playlists = data.playlists;
              items.forEach(function (it) { prep(it, data); });
              self.all = items;
              self.total = items.length;
            })
            .catch(function (e) {
              if (window.console) console.warn("[media] index:", e && e.message);
              self.failed = true;
              self.all = self.initial; // filters still work on what we have
            })
            .then(function () { clearTimeout(timer); self.ready = true; self.loading = false; inflight = null; });
          return inflight;
        },
        retryIndex: function () { this.ready = false; this.failed = false; this.fetchIndex(); },
        more: function () {
          var self = this;
          this.fetchIndex().then(function () { self.shown += PAGE; });
        },
        date: function (d, monthOnly) { return day(d, monthOnly); },
        clock: clock,
        mins: mins,
        /* "S11 · E12" / "T11 · E12" */
        seLabel: function (it) {
          if (!it) return "";
          if (it.se != null && it.ep != null) return fmt(T.se_short, { s: it.se, e: it.ep });
          if (it.ep != null) return fmt(T.ep_short, { e: it.ep });
          return "";
        },
      };
    }

    /* ===== LISTEN ===== */
    Alpine.data("podcastPage", function () {
      var initial = Array.isArray(INITIAL.items) ? INITIAL.items.slice() : [];
      var prep = function (ep) {
        var sh = SHOWS[ep.sh] || {};
        ep._h = searchText([ep.t, ep.s, sh.name, sh.short, ep.se != null ? "s" + ep.se : "", ep.ep != null ? "e" + ep.ep : ""]);
      };
      return mix(listBase(initial, prep), {
        shows: SHOWS,
        show: "",
        season: "",
        elang: "",
        sort: "new",
        resumeEp: null,
        mixed: false,

        init: function () {
          var self = this;
          this.mixed = this.$el.dataset.mixed === "true";
          var fromUrl = this.readUrl();
          this.setupList(["q", "show", "season", "elang", "sort"]);
          if (this.filtering) this.fetchIndex(); // opened with ?show=… — the watchers only see later changes
          if (fromUrl) { this.syncUrl(); this.revealChips(); } // drop values that don't apply (unknown show, …)
          ["show", "season"].forEach(function (k) { self.$watch(k, function () { self.syncUrl(); }); });
          this.resumeEp = lastResume();
          this.$nextTick(watchPlayerChrome);
        },
        /* Filters in the address: /listen/?show=wo (the Meeting page's "Listen to past
           meetings" button) and ?show=wo&season=2. Picking a show / season updates the
           address (replaceState: no extra Back steps), so the view can be shared. */
        readUrl: function () {
          var p;
          try { p = new URLSearchParams(window.location.search); } catch (e) { return false; }
          var sh = p.get("show") || "", se = p.get("season") || "";
          if (!sh && !se) return false;
          if (sh && SHOW_KEYS.length > 1 && SHOW_KEYS.indexOf(sh) !== -1) this.show = sh;
          // a season number only means something within one show
          if (/^\d{1,4}$/.test(se) && (this.show || SHOW_KEYS.length === 1)) this.season = String(Number(se));
          return true;
        },
        syncUrl: function () {
          try {
            var loc = window.location, p = new URLSearchParams(loc.search);
            if (this.show) p.set("show", this.show); else p.delete("show");
            if (this.season) p.set("season", this.season); else p.delete("season");
            var qs = p.toString(), url = loc.pathname + (qs ? "?" + qs : "") + loc.hash;
            if (url !== loc.pathname + loc.search + loc.hash) window.history.replaceState(window.history.state, "", url);
          } catch (e) { /* old browser / sandboxed frame: the filter still works */ }
        },
        /* On a phone the chip rows scroll sideways: bring a chip pressed by the address into view. */
        revealChips: function () {
          var root = this.$el;
          this.$nextTick(function () {
            root.querySelectorAll(".media-chips").forEach(function (row) {
              var b = row.querySelector('[aria-pressed="true"]');
              if (!b || row.scrollWidth <= row.clientWidth) return;
              var r = b.getBoundingClientRect(), box = row.getBoundingClientRect();
              if (r.left < box.left || r.right > box.right) row.scrollLeft += r.left - box.left - 16;
            });
          });
        },
        epAt: function (i) { return this.initial[i] || { id: "" }; },
        /* newest episode of a show (from the page JSON, so it works before the index loads) */
        latestOf: function (key) { return LATEST[key] || { id: "" }; },
        get filtering() { return !!(this.q.trim() || this.show || this.season || this.elang || this.sort !== "new"); },
        /* the current filters as a test for one episode */
        matcher: function () {
          var terms = norm(this.q.trim()).split(/\s+/).filter(Boolean), sh = this.show, se = this.season, el = this.elang;
          return function (ep) {
            if (sh && ep.sh !== sh) return false;
            if (se && String(ep.se) !== se) return false;
            if (el && ep.l !== el) return false;
            for (var i = 0; i < terms.length; i++) if ((ep._h || "").indexOf(terms[i]) === -1) return false;
            return true;
          };
        },
        get filtered() {
          var out = this.all.filter(this.matcher());
          return this.sort === "old" ? out.reverse() : out;
        },
        /* While the full list downloads, the server-rendered newest episodes that match
           stay visible: newest first they are exactly the top of the final list. */
        keep: function (ep) { return !this.filtering || (this.sort === "new" && this.matcher()(ep)); },
        get kept() {
          if (!this.filtering) return this.initial.length;
          return this.sort === "new" ? this.initial.filter(this.matcher()).length : 0;
        },
        get statusText() {
          if (this.client) { var n = this.filtered.length; return fmt(T.showing, { n: Math.min(this.shown, n), total: n }); }
          if (this.filtering) return ""; // total not known until the full list is here (the "Loading…" note shows)
          return fmt(T.showing, { n: this.initial.length, total: this.total });
        },
        clear: function () { this.q = ""; this.show = ""; this.season = ""; this.elang = ""; this.sort = "new"; this.shown = PAGE; },
        /* seasons belong to a show: switching show resets the season */
        setShow: function (key) { if (key !== this.show) this.season = ""; this.show = key; },
        /* show card "See episodes": filter the list to that show and scroll to it */
        pickShow: function (key) {
          this.setShow(key);
          var el = document.getElementById("episodes");
          if (!el) return;
          var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
        },
        /* show card "Play the latest": plays here; modifier-clicks keep the link (episode page) */
        playLatest: function (ep, ev) {
          if (ev && (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button > 0)) return;
          if (!ep || !ep.id || !safeUrl(ep.a)) return; // nothing playable → follow the link
          if (ev) ev.preventDefault();
          this.$store.audio.toggleEp(ep);
        },
        /* badge on rows of the non-primary shows ("Weekly Open"), only when there are several shows */
        showBadge: function (ep) {
          if (!ep || SHOW_KEYS.length < 2 || !ep.sh || ep.sh === PRIMARY) return "";
          var s = SHOWS[ep.sh];
          return (s && (s.short || s.name)) || "";
        },
        showName: showName,

        art: artFor,
        /* short helpers keep the 24+ server-rendered rows light */
        isCur: function (ep) { return this.$store.audio.isCurrent(ep && ep.id); },
        isOn: function (ep) { return this.$store.audio.isPlaying(ep && ep.id); },
        isBusy: function (ep) { var s = this.$store.audio; return s.loading && s.isCurrent(ep && ep.id); },
        isDone: function (ep) { return this.$store.audio.isDone(ep && ep.id); },
        pct: function (ep) { return this.$store.audio.pct(ep); },
        tog: function (ep) { this.$store.audio.toggleEp(ep); },
        rowCls: function (ep) { return (this.isCur(ep) ? "is-current " : "") + (this.isOn(ep) ? "is-playing" : ""); },
        long: function (ep) { return !!(ep && ep.s && ep.s.length > 150); },
        /* language pill only when the list mixes languages (today every episode is in English) */
        foreign: function (ep) { return this.mixed && !!(ep && ep.l && ep.l !== LANG && ep.l !== "und"); },
        langTitle: function (l) { return T[l] || l; },
        playLabel: function (ep) {
          var s = this.$store.audio;
          if (!ep) return T.play;
          var verb = s.isPlaying(ep.id) ? T.pause : s.resumeAt(ep) > 0 ? T.resume : T.play;
          return verb + ": " + (ep.t || "");
        },
        /* show card button: the visible words ("Play the latest" / "Pause") start the
           accessible name, then the episode title (WCAG 2.5.3 Label in Name) */
        latestText: function (ep) { return this.isOn(ep) ? T.pause : T.play_latest; },
        latestLabel: function (ep) { return this.latestText(ep) + (ep && ep.t ? ": " + ep.t : ""); },
        pillText: function (ep) {
          var s = this.$store.audio;
          if (!ep) return "";
          if (s.isPlaying(ep.id)) return T.playing;
          var at = s.timeFor(ep), du = s.durFor(ep);
          if (at > 0 && du > at && !s.isDone(ep.id)) return fmt(T.left, { t: mins(du - at) });
          return mins(ep.du) || T.play;
        },
        leftText: function (ep) {
          if (!ep) return "";
          var at = this.$store.audio.resumeAt(ep);
          return ep.du && at ? fmt(T.left, { t: mins(ep.du - at) }) : clock(at);
        },
        valueText: function (ep) {
          var s = this.$store.audio;
          return fmt(T.time_of, { a: clock(s.timeFor(ep)), b: clock(s.durFor(ep)) });
        },
      });
    });

    /* the sticky mini-player's `ep` is whatever is loaded */
    Alpine.data("miniPlayer", function () {
      return { get ep() { return this.$store.audio.cur || { id: "" }; } };
    });

    /* ===== WATCH ===== */
    Alpine.data("videoPage", function () {
      var initial = Array.isArray(INITIAL.items) ? INITIAL.items.slice() : [];
      var prep = function (v, src) {
        var names = (src && src.playlists) || [];
        v._h = searchText([v.t].concat((v.p || []).map(function (i) { return names[i]; })));
      };
      return mix(listBase(initial, prep), {
        playlists: Array.isArray(INITIAL.playlists) ? INITIAL.playlists : [],
        vlang: "",
        coll: "",
        cur: null,
        opener: null,

        init: function () { this.setupList(["q", "vlang", "coll"]); },
        vidAt: function (i) { return this.initial[i] || { id: "" }; },
        get filtering() { return !!(this.q.trim() || this.vlang || this.coll); },
        get filtered() {
          var terms = norm(this.q.trim()).split(/\s+/).filter(Boolean), lg = this.vlang, c = this.coll;
          var pl = c.indexOf("pl:") === 0 ? Number(c.slice(3)) : null;
          return this.all.filter(function (v) {
            if (lg && v.c !== lg) return false;
            if (pl !== null) { if (!v.p || v.p.indexOf(pl) === -1) return false; }
            else if (c && v.k !== c) return false;
            for (var i = 0; i < terms.length; i++) if ((v._h || "").indexOf(terms[i]) === -1) return false;
            return true;
          });
        },
        setColl: function (c) { this.coll = this.coll === c ? "" : c; },
        clear: function () { this.q = ""; this.vlang = ""; this.coll = ""; this.shown = PAGE; },

        thumb: function (v) {
          if (!v || !v.id) return "";
          return v.i ? assetUrl(v.i) : "https://i.ytimg.com/vi/" + encodeURIComponent(v.id) + "/hqdefault.jpg";
        },
        ytUrl: function (v) {
          if (!v || !v.id) return "#";
          var id = encodeURIComponent(v.id);
          return v.k === "short" ? "https://www.youtube.com/shorts/" + id : "https://www.youtube.com/watch?v=" + id;
        },
        plName: function (v) { return v && v.p && v.p.length ? this.playlists[v.p[0]] || "" : ""; },
        dur: function (v) { return v && v.du ? clock(v.du) : ""; },

        /* Play a video in the modal. Modifier-clicks keep the normal link behaviour (new tab). */
        open: function (v, ev) {
          if (ev && (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button > 0)) return;
          var dlg = this.$refs.dlg, frame = this.$refs.frame;
          if (!v || !v.id || !dlg || typeof dlg.showModal !== "function" || !frame) return; // follow the link
          if (ev) ev.preventDefault();
          this.opener = ev && ev.currentTarget ? ev.currentTarget : document.activeElement;
          this.pauseFeatured();
          this.cur = v;
          var f = document.createElement("iframe");
          f.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(v.id) + "?autoplay=1&playsinline=1&rel=0&hl=" + LANG;
          f.title = v.t || "YouTube";
          f.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
          f.allowFullscreen = true;
          f.referrerPolicy = "strict-origin-when-cross-origin";
          f.className = "absolute inset-0 size-full border-0";
          frame.replaceChildren(f);
          dlg.showModal();
          var closeBtn = this.$refs.closeBtn;
          if (closeBtn) closeBtn.focus();
        },
        close: function () { if (this.$refs.dlg && this.$refs.dlg.open) this.$refs.dlg.close(); },
        /* <dialog> "close" event (Esc, button, backdrop): remove the iframe = stop playback */
        onClose: function () {
          if (this.$refs.frame) this.$refs.frame.replaceChildren();
          this.cur = null;
          var o = this.opener;
          this.opener = null;
          if (o && typeof o.focus === "function" && document.contains(o)) o.focus();
        },
        /* Pause the featured <lite-youtube> player (it is created with enablejsapi=1). */
        pauseFeatured: function () {
          document.querySelectorAll("lite-youtube iframe").forEach(function (f) {
            try { f.contentWindow.postMessage(JSON.stringify({ event: "command", func: "pauseVideo", args: [] }), "*"); } catch (e) {}
          });
        },
      });
    });

    /* ===== INSTAGRAM ===== */
    Alpine.data("igPage", function () {
      return {
        tab: "gv",
        shown: { gv: 12, lv: 12 },
        more: function (k) { this.shown[k] = (this.shown[k] || 12) + 12; },
      };
    });
  });

  /* lite-youtube switches to the full YouTube IFrame API (youtube.com, not
     youtube-nocookie.com) on phones and Safari. Keep every player in
     privacy-enhanced mode instead; at worst the visitor taps play twice. */
  document.querySelectorAll("lite-youtube").forEach(function (el) { el.needsYTApi = false; });

  /* Official Instagram embeds report their height with postMessage
     ({type:"MEASURE", details:{height}}); size the matching iframe. */
  window.addEventListener("message", function (e) {
    if (e.origin !== "https://www.instagram.com") return;
    var d = e.data;
    if (typeof d === "string") { try { d = JSON.parse(d); } catch (x) { return; } }
    if (!d || d.type !== "MEASURE" || !d.details) return;
    var h = Number(d.details.height);
    if (!(h > 120)) return;
    document.querySelectorAll("iframe[data-ig-embed]").forEach(function (f) {
      if (f.contentWindow === e.source) f.style.height = Math.min(Math.ceil(h), 1800) + "px";
    });
  });
})();
