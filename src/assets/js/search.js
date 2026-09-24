/* NETA 65 Grapevine / La Viña — search kit + /search/ page.
   ------------------------------------------------------------------
   GV.searchKit: small helpers around MiniSearch (vendored at
   /assets/vendor/minisearch.js), shared with /assets/js/library.js:
     - accent- and case-insensitive terms ("viña" = "vina", "catálogo" = "catalogo")
     - English + Spanish stop words ignored (so "libro de trabajo" works)
     - prefix + light fuzzy matching; AND first, falls back to OR ("partial")
     - highlight(text, terms) → safe HTML with <mark> around matches
   The /search/ page part only runs when #site-search exists. Its search box sits in the
   block below the hero; #ss-layout (type filters + results) shows only while there is a
   query, #ss-start (tips + browse) only while there is none.
   No build step, no dependencies besides MiniSearch. */
(function () {
  "use strict";
  var GV = (window.GV = window.GV || {});

  /* ================================================================ */
  /*  Search kit                                                        */
  /* ================================================================ */
  var STOP = {};
  ("a an and are as at be by for from in is it of on or the to with " +
    "al con de del el en es la las lo los o para por que se su sus un una uno y e")
    .split(" ").forEach(function (w) { STOP[w] = 1; });

  var MARKS = /\p{M}/gu;
  var WORDCH = /[\p{L}\p{N}]/u;

  function norm(s) { return String(s == null ? "" : s).normalize("NFD").replace(MARKS, "").toLowerCase(); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function processTerm(term) { var t = norm(term); return !t || STOP[t] ? null : t; }
  function processTermKeep(term) { var t = norm(term); return t || null; }

  var kit = {
    norm: norm,
    esc: esc,
    processTerm: processTerm,

    /* Create a MiniSearch index. fields: ["t","o",…]; boost: {t: 3, …} */
    create: function (fields, boost, extra) {
      if (typeof window.MiniSearch !== "function") throw new Error("MiniSearch not loaded");
      return new window.MiniSearch(Object.assign({
        idField: "id",
        fields: fields,
        storeFields: [],
        processTerm: processTerm,
        searchOptions: {
          boost: boost || {},
          combineWith: "AND",
          prefix: function (term) { return term.length > 1; },
          fuzzy: function (term) { return term.length > 4 ? 0.2 : false; },
        },
      }, extra || {}));
    },

    /* Run a query. Returns {hits:[{id,score,terms}], partial:bool}. */
    search: function (ms, q, opts) {
      q = String(q || "").trim();
      if (!q) return { hits: [], partial: false };
      opts = opts || {};
      // Query made only of stop words ("la", "the") → search them literally.
      var keep = !String(q).split(/[\s\p{P}]+/u).some(function (w) { return processTerm(w); });
      var base = keep ? { processTerm: processTermKeep } : {};
      var hits = ms.search(q, Object.assign({}, opts, base));
      var partial = false;
      if (!hits.length && /\s/.test(q)) {
        hits = ms.search(q, Object.assign({}, opts, base, { combineWith: "OR" }));
        partial = hits.length > 0;
      }
      return { hits: hits, partial: partial };
    },

    /* "Did you mean …?" — best fuzzy suggestion that differs from the query. */
    suggest: function (ms, q) {
      try {
        var s = ms.autoSuggest(q, { fuzzy: 0.34, prefix: false, combineWith: "AND" });
        var nq = norm(q).trim();
        for (var i = 0; i < s.length && i < 5; i++) if (norm(s[i].suggestion) !== nq) return s[i].suggestion;
      } catch (e) {}
      return "";
    },

    /* Escape text and wrap every word that STARTS with one of `terms`
       (already normalized index terms) in <mark>. Accent-insensitive. */
    highlight: function (text, terms) {
      text = String(text == null ? "" : text);
      if (!text || !terms || !terms.length) return esc(text);
      var ns = "", st = [], en = [], pos = 0;
      for (var ch of text) {
        var n = norm(ch);
        for (var k = 0; k < n.length; k++) { ns += n[k]; st.push(pos); en.push(pos + ch.length); }
        pos += ch.length;
      }
      var ranges = [];
      terms.forEach(function (t) {
        if (!t || t.length < 1) return;
        var from = 0, idx;
        while ((idx = ns.indexOf(t, from)) !== -1) {
          if (idx === 0 || !WORDCH.test(ns[idx - 1])) ranges.push([st[idx], en[idx + t.length - 1]]);
          from = idx + t.length;
        }
      });
      if (!ranges.length) return esc(text);
      ranges.sort(function (a, b) { return a[0] - b[0]; });
      var out = "", last = 0, merged = [];
      ranges.forEach(function (r) {
        var m = merged[merged.length - 1];
        if (m && r[0] <= m[1]) m[1] = Math.max(m[1], r[1]); else merged.push([r[0], r[1]]);
      });
      merged.forEach(function (r) { out += esc(text.slice(last, r[0])) + "<mark>" + esc(text.slice(r[0], r[1])) + "</mark>"; last = r[1]; });
      return out + esc(text.slice(last));
    },

    /* "2026-09-01" → "September 2026" / "Sep 1, 2026" in the page language. */
    fmtYmd: function (ymd, style) {
      if (!ymd) return "";
      var d = new Date(String(ymd).slice(0, 10) + "T12:00:00Z");
      if (isNaN(d)) return "";
      var opts = style === "month" ? { month: "long", year: "numeric" } : style === "long" ? { month: "long", day: "numeric", year: "numeric" } : { month: "short", day: "numeric", year: "numeric" };
      var s = GV.fmtDate ? GV.fmtDate(d, opts) : d.toDateString();
      return GV.lang === "es" ? s.charAt(0).toUpperCase() + s.slice(1) : s;
    },

    /* Base-path-safe URL for internal paths ("/es/library/"); web links unchanged;
       anything else (javascript:, data:, …) becomes "#". */
    href: function (u) {
      u = String(u || "");
      if (/^\/(?!\/)/.test(u)) return GV.url ? GV.url(u) : u;
      return /^https?:\/\//i.test(u) ? u : "#";
    },

    icons: function (templateId) {
      var map = {}, tpl = document.getElementById(templateId);
      if (tpl && tpl.content) [].forEach.call(tpl.content.querySelectorAll("[data-icon]"), function (el) { map[el.getAttribute("data-icon")] = el.innerHTML.trim(); });
      return function (name, cls) {
        var svg = map[name] || map["file-text"] || "";
        return cls ? svg.replace(/class="icon [^"]*"/, 'class="icon ' + cls + '"') : svg;
      };
    },

    debounce: function (fn, ms) {
      var t;
      return function () { var a = arguments, self = this; clearTimeout(t); t = setTimeout(function () { fn.apply(self, a); }, ms); };
    },

    fill: function (tpl, vars) { return String(tpl || "").replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] != null ? vars[k] : m; }); },

    /* "/" focuses the given input unless the user is typing somewhere. */
    slashFocus: function (input) {
      document.addEventListener("keydown", function (e) {
        if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
        var el = document.activeElement, tag = el && el.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (el && el.isContentEditable)) return;
        e.preventDefault();
        input.focus();
        input.select();
      });
    },
  };
  GV.searchKit = kit;

  /* ================================================================ */
  /*  /search/ page                                                     */
  /* ================================================================ */
  function initSearchPage() {
    var root = document.getElementById("site-search");
    var cfgEl = document.getElementById("ss-config");
    if (!root || !cfgEl) return;
    var CFG = JSON.parse(cfgEl.textContent);
    var S = CFG.s;
    var $ = function (id) { return document.getElementById(id); };
    var input = $("ss-q"), form = $("ss-form"), clearBtn = $("ss-clear");
    var statusEl = $("ss-status"), list = $("ss-results"), moreBtn = $("ss-more");
    var jumpWrap = $("ss-jump-wrap"), jumpEl = $("ss-jump"), kindsWrap = $("ss-kinds-wrap");
    var startEl = $("ss-start"), noneEl = $("ss-none"), partialEl = $("ss-partial"), errorEl = $("ss-error"), loadingEl = $("ss-loading");
    var didEl = $("ss-did"), noneKindEl = $("ss-none-kind"), layoutEl = $("ss-layout");
    var chips = [].slice.call(root.querySelectorAll("[data-kind]"));
    var icon = kit.icons("ss-icons");
    var numFmt = null;
    try { numFmt = new Intl.NumberFormat(CFG.locale || (CFG.lang === "es" ? "es-US" : "en-US")); } catch (e) {}
    function num(n) { return numFmt ? numFmt.format(Number(n) || 0) : String(n); }

    var GROUP = { page: "pages", article: "articles", topic: "articles", pdf: "docs", document: "docs", slides: "docs", form: "docs",
      episode: "podcasts", video: "videos", video_file: "videos", post: "instagram",
      album: "committee", photo: "committee", announcement: "committee", event: "committee", meeting: "committee" };
    var KIND_ICON = { article: "book-open", topic: "pen-line", pdf: "file-text", document: "file-text", slides: "presentation", form: "file-pen-line",
      episode: "headphones", video: "circle-play", video_file: "circle-play", post: "instagram", album: "images", announcement: "megaphone",
      event: "calendar-days", meeting: "video", page: "file-text" };
    var PAGE = 20;

    var state = { q: "", kind: "all", shown: PAGE };
    var items = null, ms = null, loading = false, lastHits = [], lastView = [];

    function tone(e) {
      if (e.src === "lv") return "lv";
      if (e.src === "neta") return "neta";
      if (e.k === "episode" || e.k === "video" || e.k === "post") return "grape";
      return "gv";
    }

    /* ---------- URL state ---------- */
    function readUrl() {
      var p = new URLSearchParams(location.search);
      state.q = p.get("q") || "";
      var k = p.get("type") || "all";
      state.kind = chips.some(function (c) { return c.getAttribute("data-kind") === k; }) ? k : "all";
    }
    function writeUrl() {
      var p = new URLSearchParams();
      if (state.q.trim()) p.set("q", state.q.trim());
      if (state.kind !== "all") p.set("type", state.kind);
      var qs = p.toString();
      try { history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash); } catch (e) {}
    }

    /* ---------- data ---------- */
    function load() {
      if (loading || items) return;
      loading = true;
      errorEl.hidden = true;
      if (state.q.trim()) loadingEl.hidden = false;
      fetch(kit.href(CFG.index), { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (json) {
          items = (json.items || []).map(function (e, i) { e._i = i; return e; });
          // a = a story's byline (writer · hometown): searching a writer or a city finds their stories.
          ms = kit.create(["t", "o", "x", "s", "a"], { t: 3, o: 2, x: 1.3, s: 0.8, a: 1.3 }, {
            storeFields: ["k", "d", "z"],
            searchOptions: {
              boost: { t: 3, o: 2, x: 1.3, s: 0.8, a: 1.3 },
              combineWith: "AND",
              prefix: function (term) { return term.length > 1; },
              fuzzy: function (term) { return term.length > 4 ? 0.2 : false; },
              // Site pages first; then a gentle boost for recent content. PDFs no page
              // links to any more and past events ("z") rank lower.
              boostDocument: function (id, term, stored) {
                if (!stored) return 1;
                if (stored.k === "page") return 1.6;
                var z = stored.z ? 0.6 : 1;
                if (!stored.d) return z;
                var age = (Date.now() - new Date(stored.d + "T12:00:00Z").getTime()) / 864e5;
                return z * (age < 0 ? 1.05 : 1 + 0.25 * Math.max(0, 1 - age / 730));
              },
            },
          });
          ms.addAll(items.map(function (e) { return { id: e._i, t: e.t, o: e.o || "", x: e.x || "", s: e.s || "", a: e.a || "", k: e.k, d: e.d || "", z: e.z ? 1 : 0 }; }));
          loading = false;
          loadingEl.hidden = true;
          run();
          revealChip();
        })
        .catch(function () {
          loading = false;
          loadingEl.hidden = true;
          errorEl.hidden = false;
        });
    }

    /* ---------- rendering ---------- */
    function resultHtml(h) {
      var e = items[h.id], terms = h.terms;
      var ext = !(e.u && e.u.charAt(0) === "/");
      var t = tone(e);
      var img = e.yt ? "https://i.ytimg.com/vi/" + encodeURIComponent(e.yt) + "/mqdefault.jpg" : e.im ? kit.href(e.im) : "";
      var kindLabel = CFG.kinds[e.k] || e.k;
      var ic = e.k === "page" && e.ic ? e.ic : KIND_ICON[e.k] || "file-text";
      var meta = '<span class="ss-kind">' + icon(ic, "size-3.5") + esc(kindLabel) + "</span>";
      if (e.src && e.src !== "site" && CFG.src[e.src] && CFG.src[e.src] !== kindLabel) meta += "<span>" + esc(CFG.src[e.src]) + "</span>";
      if (e.d && e.k !== "page") meta += '<time datetime="' + esc(e.d) + '">' + esc(kit.fmtYmd(e.d, e.dp === "m" ? "month" : "medium")) + "</time>";
      if (e.l && e.l !== CFG.lang && e.l !== "und") meta += '<span class="badge-muted uppercase" title="' + esc(CFG.langTitle[e.l] || "") + '">' + esc(e.l) + "</span>";
      if (e.n) meta += '<span class="badge-new">' + esc(S.isNew) + "</span>";
      if (e.pw) meta += '<span class="badge-muted" title="' + esc(S.subscriberTitle) + '">' + esc(S.subscriber) + "</span>";
      var host = "";
      if (ext) { try { host = new URL(e.u).hostname.replace(/^www\./, ""); } catch (x) {} }
      return '<li class="ss-result"><a data-nav href="' + esc(kit.href(e.u)) + '"' + (ext ? ' target="_blank" rel="noopener"' : "") + ">" +
        '<span class="ss-thumb' + (img ? " has-img" : "") + '" data-tone="' + t + '" aria-hidden="true">' + icon(ic, "size-5") +
        (img ? '<img src="' + esc(img) + '" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.parentNode.classList.remove(\'has-img\');this.remove()">' : "") + "</span>" +
        '<span class="ss-body" data-tone="' + t + '"><span class="ss-meta">' + meta + "</span>" +
        // Languages: e.tl = the shown title's, when it is an untranslated original in another
        // language; e.ol || e.l = the original title's (e.l alone is the item's own language).
        '<span class="ss-title"' + (e.tl ? ' lang="' + esc(e.tl) + '"' : "") + ">" + kit.highlight(e.t, terms) + "</span>" +
        (e.o ? '<span class="ss-orig"' + (e.ol || e.l ? ' lang="' + esc(e.ol || e.l) + '"' : "") + ">" + kit.highlight(e.o, terms) + "</span>" : "") +
        (e.s ? '<span class="ss-snippet">' + kit.highlight(e.s, terms) + "</span>" : "") +
        (e.m || host || e.a ? '<span class="ss-foot">' +
          (e.a ? '<span class="ss-by">' + icon("pen-line", "size-3") + '<span class="sr-only">' + esc(S.byline) + " </span><span>" + kit.highlight(e.a, terms) + "</span></span>" : "") +
          (host ? '<span class="ss-host">' + esc(host) + " " + icon("arrow-up-right", "size-3") + '<span class="sr-only"> (' + esc(S.external) + ")</span></span>" : "") +
          (e.m ? '<span class="auto-note" title="' + esc(S.autoHelp) + '">' + icon("languages", "size-3") + " " + esc(S.auto) + "</span>" : "") +
          "</span>" : "") +
        "</span></a></li>";
    }

    function jumpHtml(h) {
      var e = items[h.id];
      return '<a data-nav href="' + esc(kit.href(e.u)) + '"><span class="ss-jump-icon" aria-hidden="true">' + icon(e.ic || "file-text", "size-[1.1rem]") + "</span>" +
        '<span class="min-w-0"><span class="ss-jump-t">' + kit.highlight(e.t, h.terms) + "</span>" + (e.s ? '<span class="ss-jump-d">' + esc(e.s) + "</span>" : "") + "</span></a>";
    }

    function setStatus(txt) { if (statusEl.textContent !== txt) statusEl.textContent = txt; }

    function run(keepShown) {
      var q = state.q.trim();
      writeUrl();
      clearBtn.hidden = !state.q;
      chips.forEach(function (c) { c.setAttribute("aria-pressed", String(c.getAttribute("data-kind") === state.kind)); });
      if (!q) {
        startEl.hidden = false;
        if (layoutEl) layoutEl.hidden = true;
        [kindsWrap, jumpWrap, noneEl, noneKindEl, partialEl, moreBtn].forEach(function (el) { el.hidden = true; });
        list.innerHTML = "";
        setStatus("");
        document.title = CFG.title;
        return;
      }
      startEl.hidden = true;
      if (layoutEl) layoutEl.hidden = false;
      document.title = "“" + q + "” · " + CFG.title;
      if (!ms) { load(); loadingEl.hidden = !loading; return; } // index still downloading
      if (!keepShown) state.shown = PAGE;

      var res = kit.search(ms, q);
      lastHits = res.hits;
      var counts = { all: 0 };
      res.hits.forEach(function (h) { var g = GROUP[items[h.id].k] || "committee"; counts[g] = (counts[g] || 0) + 1; counts.all++; });
      chips.forEach(function (c) {
        var k = c.getAttribute("data-kind"), n = counts[k] || 0;
        var el = c.querySelector("[data-n]"); if (el) el.textContent = num(n);
        c.toggleAttribute("data-zero", !n);
      });

      var pages = res.hits.filter(function (h) { return items[h.id].k === "page"; });
      var view = state.kind === "all"
        ? res.hits.filter(function (h) { return items[h.id].k !== "page"; })
        : res.hits.filter(function (h) { return GROUP[items[h.id].k] === state.kind; });
      lastView = view;

      kindsWrap.hidden = !res.hits.length;
      partialEl.hidden = !res.partial;
      jumpWrap.hidden = !(state.kind === "all" && pages.length);
      jumpEl.innerHTML = state.kind === "all" ? pages.slice(0, 4).map(jumpHtml).join("") : "";

      if (!res.hits.length) {
        noneEl.hidden = false;
        noneKindEl.hidden = true;
        $("ss-none-title").textContent = kit.fill(S.noneTitle, { q: q });
        var sug = kit.suggest(ms, q);
        didEl.hidden = !sug;
        if (sug) { var b = didEl.querySelector("button"); b.textContent = sug; b.setAttribute("data-q", sug); }
        list.innerHTML = "";
        moreBtn.hidden = true;
        setStatus(kit.fill(S.noneTitle, { q: q }));
        statusEl.classList.add("sr-only");
        return;
      }
      noneEl.hidden = true;
      statusEl.classList.remove("sr-only");
      noneKindEl.hidden = !(state.kind !== "all" && !view.length);
      if (!noneKindEl.hidden) $("ss-none-kind-btn").textContent = kit.fill(S.noneKind, { kind: CFG.groups[state.kind] || state.kind });
      list.innerHTML = view.slice(0, state.shown).map(resultHtml).join("");
      moreBtn.hidden = view.length <= state.shown;

      var n = res.hits.length;
      if (state.kind === "all") setStatus(kit.fill(n === 1 ? S.countOne : S.count, { n: num(n), q: q }));
      else setStatus(kit.fill(S.countIn, { n: num(view.length), total: num(n), q: q, kind: CFG.groups[state.kind] || state.kind }));
    }

    /* ---------- events ---------- */
    var onType = kit.debounce(function () { state.q = input.value; run(); }, 140);
    input.addEventListener("input", function () { clearBtn.hidden = !input.value; if (!items) load(); onType(); });
    input.addEventListener("focus", load, { once: true });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      state.q = input.value; run();
      var first = root.querySelector("[data-nav]");
      if (first && window.matchMedia("(pointer: coarse)").matches) input.blur();
    });
    clearBtn.addEventListener("click", function () { input.value = ""; state.q = ""; run(); input.focus(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && input.value) { e.preventDefault(); input.value = ""; state.q = ""; run(); }
      else if (e.key === "ArrowDown") { var f = root.querySelector("[data-nav]"); if (f) { e.preventDefault(); f.focus(); } }
    });
    // Arrow keys move between results (jump links + result links).
    root.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      var links = [].slice.call(root.querySelectorAll("[data-nav]")).filter(function (a) { return a.offsetParent !== null; });
      var i = links.indexOf(document.activeElement);
      if (i === -1) return;
      e.preventDefault();
      if (e.key === "ArrowDown" && i < links.length - 1) links[i + 1].focus();
      else if (e.key === "ArrowUp") (i > 0 ? links[i - 1] : input).focus();
    });
    chips.forEach(function (c) {
      c.addEventListener("click", function () { state.kind = c.getAttribute("data-kind"); run(); });
    });
    moreBtn.addEventListener("click", function () {
      var before = state.shown;
      state.shown += PAGE;
      list.insertAdjacentHTML("beforeend", lastView.slice(before, state.shown).map(resultHtml).join(""));
      moreBtn.hidden = lastView.length <= state.shown;
      var next = list.children[before] && list.children[before].querySelector("a");
      if (next) next.focus();
    });
    root.addEventListener("click", function (e) {
      var b = e.target.closest("[data-q]");
      if (b) { e.preventDefault(); input.value = b.getAttribute("data-q"); state.q = input.value; state.kind = "all"; run(); input.focus(); }
      var all = e.target.closest("[data-show-all]");
      if (all) { e.preventDefault(); state.kind = "all"; run(); }
    });
    $("ss-retry").addEventListener("click", function () { load(); });
    kit.slashFocus(input);
    window.addEventListener("popstate", function () { readUrl(); input.value = state.q; run(); revealChip(); });

    // On phones the type chips scroll sideways: bring the selected one into view.
    function revealChip() {
      var c = root.querySelector('[data-kind][aria-pressed="true"]'), row = c && c.parentNode;
      if (row && row.scrollWidth > row.clientWidth) row.scrollLeft += c.getBoundingClientRect().left - row.getBoundingClientRect().left - 24;
    }

    /* ---------- start ---------- */
    readUrl();
    input.value = state.q;
    run();
    if (state.q) load();
    else if ("requestIdleCallback" in window) requestIdleCallback(load, { timeout: 2500 }); else setTimeout(load, 1200);
    if (!state.q && window.matchMedia("(pointer: fine)").matches) input.focus({ preventScroll: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initSearchPage); else initSearchPage();
})();
