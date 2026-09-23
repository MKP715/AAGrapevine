/* NETA 65 Grapevine / La Viña — Library page (/library/).
   ------------------------------------------------------------------
   The page is server-rendered with the newest documents (works without JS).
   This script loads /library-index.json (every PDF + committee document,
   see src/pages/library-index.11ty.js) and adds:
     - instant search (MiniSearch via GV.searchKit: accent-insensitive, prefix, fuzzy)
     - facets with live counts (source / language / category / year, multi-select)
     - quick collections, sort, card/list view, "Load more" paging
     - shareable URLs: ?q=&src=&lang=&cat=&year=&col=&sort=&view=
       (?cat=gvr / ?cat=rlv - the crawler's kit categories, used by links on
       other pages - open the GVR / RLV kit collections)
     - copy / share buttons and the Google Drive preview dialog
   Card markup mirrors the docCard macro in src/pages/library.njk. */
(function () {
  "use strict";
  var GV = window.GV || {};
  var kit = GV.searchKit;
  var cfgEl = document.getElementById("lib-config");
  if (!cfgEl || !kit) return;
  var CFG = JSON.parse(cfgEl.textContent);
  var S = CFG.s;
  var LANG = CFG.lang;
  var esc = kit.esc;
  var icon = kit.icons("lib-icons");
  var $ = function (id) { return document.getElementById(id); };
  var numFmt = null;
  try { numFmt = new Intl.NumberFormat(CFG.locale || (LANG === "es" ? "es-US" : "en-US")); } catch (e) {}
  function num(n) { return numFmt ? numFmt.format(Number(n) || 0) : String(n); }

  /* ---------- screen-reader announcements ---------- */
  var live = document.createElement("div");
  live.className = "sr-only";
  live.setAttribute("aria-live", "polite");
  document.body.appendChild(live);
  function announce(msg) { live.textContent = ""; setTimeout(function () { live.textContent = msg; }, 30); }

  /* ================================================================ */
  /*  Card actions (work on server-rendered and JS-rendered cards)      */
  /* ================================================================ */
  function flash(btn) {
    if (!btn || btn._flash) return;
    var html = btn.innerHTML, title = btn.getAttribute("title");
    btn._flash = true;
    btn.classList.add("is-done");
    btn.innerHTML = icon("check", "size-4") + '<span class="sr-only">' + esc(S.copied) + "</span>";
    btn.setAttribute("title", S.copied);
    announce(S.copied);
    setTimeout(function () { btn.innerHTML = html; btn.classList.remove("is-done"); if (title) btn.setAttribute("title", title); btn._flash = false; }, 1600);
  }

  var dialog = $("lib-preview"), frame = $("lib-preview-frame"), lastFocus = null;
  function openPreview(btn) {
    var url = btn.getAttribute("data-lib-preview"), open = btn.getAttribute("data-open") || url;
    if (!dialog || typeof dialog.showModal !== "function") { window.open(open, "_blank", "noopener"); return; }
    lastFocus = btn;
    $("lib-preview-title").textContent = btn.getAttribute("data-title") || "";
    ["lib-preview-open", "lib-preview-open-m"].forEach(function (id) { var a = $(id); if (a) a.href = open; });
    var dl = $("lib-preview-dl"), dlUrl = btn.getAttribute("data-dl");
    if (dl) { dl.href = dlUrl || open; dl.hidden = !dlUrl; }
    frame.src = url;
    dialog.showModal();
    var close = dialog.querySelector("[data-lib-close]");
    if (close) close.focus();
  }
  if (dialog) {
    dialog.addEventListener("close", function () {
      frame.src = "about:blank";
      if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    });
    // Click on the dimmed backdrop closes the dialog.
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
  }

  document.addEventListener("click", function (e) {
    var b;
    if ((b = e.target.closest("[data-lib-copy]"))) {
      e.preventDefault();
      GV.copy && GV.copy(b.getAttribute("data-lib-copy"));
      flash(b);
    } else if ((b = e.target.closest("[data-lib-share]"))) {
      e.preventDefault();
      var data = { title: b.getAttribute("data-title") || document.title, url: b.getAttribute("data-lib-share") };
      if (navigator.share) navigator.share(data).catch(function () {});
      else { GV.copy && GV.copy(data.url); flash(b); }
    } else if ((b = e.target.closest("[data-lib-preview]"))) {
      e.preventDefault();
      openPreview(b);
    } else if ((b = e.target.closest("[data-lib-close]"))) {
      e.preventDefault();
      if (dialog && dialog.open) dialog.close();
    }
  });

  /* ================================================================ */
  /*  Browse: search + facets + collections                            */
  /* ================================================================ */
  var results = $("lib-results");
  var input = $("lib-q"), form = $("lib-form");
  if (!results || !input) {
    // Empty library (nothing synced yet): the search box still works as a
    // shortcut to the site-wide search.
    if (form && input) form.addEventListener("submit", function (e) { e.preventDefault(); location.href = kit.href(CFG.search) + (input.value.trim() ? "?q=" + encodeURIComponent(input.value.trim()) : ""); });
    return;
  }

  var clearBtn = $("lib-clear"), kbd = $("lib-kbd"), statusEl = $("lib-status");
  var moreWrap = $("lib-more-wrap"), moreBtn = $("lib-more"), shownEl = $("lib-shown");
  var emptyEl = $("lib-empty"), partialEl = $("lib-partial"), errorEl = $("lib-error"), activeEl = $("lib-active");
  var sortSel = $("lib-sort"), facetsEl = $("lib-facets"), facetsToggle = $("lib-facets-toggle"), facetsN = $("lib-facets-n");
  var clearAllBtn = $("lib-clear-all"), siteSearch = $("lib-site-search");
  var chips = [].slice.call(document.querySelectorAll(".lib-chip"));
  var colBtns = [].slice.call(document.querySelectorAll(".lib-col"));
  var viewBtns = [].slice.call(document.querySelectorAll(".lib-view [data-view]"));
  var relOpt = sortSel && sortSel.querySelector('option[value="rel"]');

  var FACETS = ["src", "lang", "cat", "year"];
  var COLS = {};
  (CFG.cols || []).forEach(function (c) { COLS[c.key] = c; });

  var state = { q: "", src: new Set(), lang: new Set(), cat: new Set(), year: new Set(), col: "", sort: "", view: "grid" };
  var docs = null, refs = [], cats = {}, ms = null;
  var list = [], shown = CFG.pageSize, termsById = null, partial = false, hydrated = false;

  function val(f, d) { return f === "src" ? d.s : f === "lang" ? d.l : f === "cat" ? d.c : d.d ? d.d.slice(0, 4) : ""; }
  function filtered() { return !!(state.q.trim() || state.col || FACETS.some(function (f) { return state[f].size; })); }
  function effectiveSort() { return state.sort || (state.q.trim() ? "rel" : "new"); }

  /* ---------- URL <-> state ---------- */
  var LEGACY_CAT = { gvr: "gvr-kit", rlv: "rlv-kit" };
  function readUrl() {
    var p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    FACETS.forEach(function (f) { state[f] = new Set((p.get(f) || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean)); });
    state.col = COLS[p.get("col")] ? p.get("col") : "";
    // ?cat=gvr -> the GVR kit collection (links from other pages use the crawler's
    // categories; here kit PDFs are filed by document type and the kits are collections).
    if (!state.col && state.cat.size === 1) {
      var only = Array.from(state.cat)[0];
      var hasChip = chips.some(function (c) { return c.getAttribute("data-f") === "cat" && c.getAttribute("data-v") === only; });
      if (LEGACY_CAT[only] && COLS[LEGACY_CAT[only]] && !hasChip) { state.col = LEGACY_CAT[only]; state.cat.clear(); }
    }
    var s = p.get("sort");
    state.sort = ["new", "old", "az", "rel"].indexOf(s) !== -1 ? s : "";
    var v = p.get("view");
    if (!v) { try { v = localStorage.getItem("lib-view"); } catch (e) {} }
    if (!v) v = window.innerWidth < 640 ? "list" : "grid"; // phones default to the compact list
    state.view = v === "list" ? "list" : "grid";
  }
  function writeUrl() {
    var p = new URLSearchParams();
    if (state.q.trim()) p.set("q", state.q.trim());
    FACETS.forEach(function (f) { if (state[f].size) p.set(f, Array.from(state[f]).join(",")); });
    if (state.col) p.set("col", state.col);
    if (state.sort) p.set("sort", state.sort);
    if (state.view === "list") p.set("view", "list");
    var qs = p.toString();
    try { history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash); } catch (e) {}
    if (siteSearch) siteSearch.href = kit.href(CFG.search) + (state.q.trim() ? "?q=" + encodeURIComponent(state.q.trim()) : "");
  }

  /* ---------- data ---------- */
  function load() {
    errorEl.hidden = true;
    results.setAttribute("aria-busy", filtered() ? "true" : "false");
    fetch(kit.href(CFG.index), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (json) {
        refs = json.refs || [];
        cats = json.cats || {};
        docs = (json.items || []).map(function (d, i) { d._i = i; return d; });
        results.removeAttribute("aria-busy");
        update(true);
      })
      .catch(function () {
        results.removeAttribute("aria-busy");
        errorEl.hidden = false;
      });
  }

  // Built lazily on the first query (browsing with facets alone never needs it).
  function ensureIndex() {
    if (ms) return ms;
    ms = kit.create(["t", "o", "x", "r", "c"], { t: 3, o: 2, x: 1, r: 0.6, c: 0.8 });
    ms.addAll(docs.map(function (d) {
      var ref = d.r != null && refs[d.r] ? refs[d.r][1] : "";
      return {
        id: d._i, t: d.t, o: d.o || "", x: d.x || "", r: ref,
        c: [cats[d.c] || d.c, CFG.src[d.s], CFG.srcLong[d.s], CFG.langs[d.l], d.d ? d.d.slice(0, 4) : ""].join(" "),
      };
    }));
    return ms;
  }

  /* ---------- compute + render ---------- */
  function compute() {
    var q = state.q.trim(), base;
    termsById = null;
    partial = false;
    if (q) {
      var r = kit.search(ensureIndex(), q);
      partial = r.partial;
      termsById = {};
      // Files no page links to any more rank below live ones with a similar score.
      var hits = r.hits.map(function (h, i) { return { h: h, i: i, sc: h.score * (docs[h.id].or ? 0.5 : 1) }; });
      hits.sort(function (a, b) { return b.sc - a.sc || a.i - b.i; });
      base = hits.map(function (x) { termsById[x.h.id] = x.h.terms; return docs[x.h.id]; });
    } else base = docs;
    if (state.col) {
      var code = COLS[state.col].code;
      base = base.filter(function (d) { return (d.co || "").indexOf(code) !== -1; });
    }
    // Faceted counts: each facet counts over items matching every OTHER facet.
    var counts = { src: {}, lang: {}, cat: {}, year: {} }, out = [];
    base.forEach(function (d) {
      var pass = FACETS.map(function (f) { return !state[f].size || state[f].has(val(f, d)); });
      var all = pass[0] && pass[1] && pass[2] && pass[3];
      if (all) out.push(d);
      FACETS.forEach(function (f, i) {
        for (var j = 0; j < 4; j++) if (j !== i && !pass[j]) return;
        var v = val(f, d);
        counts[f][v] = (counts[f][v] || 0) + 1;
      });
    });
    var sort = effectiveSort();
    if (sort === "new") out.sort(function (a, b) { return a._i - b._i; });
    else if (sort === "old") out.sort(function (a, b) { return (a.or ? 1 : 0) - (b.or ? 1 : 0) || (a.d ? 0 : 1) - (b.d ? 0 : 1) || b._i - a._i; });
    else if (sort === "az") out.sort(function (a, b) { return a.t.localeCompare(b.t, LANG, { sensitivity: "base", numeric: true }); });
    list = out;
    return counts;
  }

  function srcBadge(s) {
    if (s === "lv") return '<span class="badge-lv">' + icon("grapes", "size-3") + " La Viña</span>";
    if (s === "neta") return '<span class="badge-vine">' + icon("users", "size-3") + " NETA 65</span>";
    return '<span class="badge-gv">' + icon("grapes", "size-3") + " Grapevine</span>";
  }

  function fileSize(b) {
    b = Number(b) || 0;
    if (!b) return "";
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(i ? 1 : 0) + " " + u[i];
  }

  function renderCard(d) {
    var terms = termsById && termsById[d._i];
    var t = d.t, title = kit.highlight(t, terms);
    // d.o only exists when d.t is a translation; else d.t IS the original title, in language tl.
    var tl = d.tl || d.l || "";
    var ft = d.ft || "pdf", ref = d.r != null ? refs[d.r] : null;
    var th = d.th ? esc(kit.href(d.th)) : "";
    var dl = kit.href(d.dl || d.u), open = esc(kit.href(d.u)), tA = { title: t };
    var meta = [];
    if (d.d) meta.push('<time datetime="' + esc(d.d) + '">' + esc(kit.fmtYmd(d.d, d.dp === "m" ? "month" : "long")) + "</time>");
    if (d.pg) meta.push("<span>" + esc(d.pg === 1 ? S.pageOne : kit.fill(S.pages, { n: num(d.pg) })) + "</span>");
    if (d.sz) meta.push("<span>" + esc(fileSize(d.sz)) + "</span>");
    if (d.hx) meta.push("<span>" + esc(kit.fill(S.opensOn, { host: d.hx })) + "</span>");

    var actions = d.pv
      ? '<button type="button" class="lib-btn lib-btn-main" data-lib-preview="' + esc(kit.href(d.pv)) + '" data-title="' + esc(t) + '" data-open="' + open + '" data-dl="' + esc(d.dl || "") + '" aria-label="' + esc(kit.fill(S.previewAria, tA)) + '">' + icon("eye", "size-4") + "<span>" + esc(S.preview) + "</span></button>" +
        '<a class="lib-btn lib-icon" href="' + open + '" target="_blank" rel="noopener" aria-label="' + esc(kit.fill(S.openAria, tA)) + '" title="' + esc(S.open) + '">' + icon("external-link", "size-4") + "</a>"
      : '<a class="lib-btn lib-btn-main" href="' + open + '" target="_blank" rel="noopener" aria-label="' + esc(kit.fill(S.openAria, tA)) + '">' + icon("external-link", "size-4") + "<span>" + esc(S.open) + "</span></a>";
    actions +=
      '<a class="lib-btn lib-icon" href="' + esc(dl) + '" download target="_blank" rel="noopener" aria-label="' + esc(kit.fill(S.downloadAria, tA)) + '" title="' + esc(S.download) + '">' + icon("download", "size-4") + "</a>" +
      (d.pv ? "" : '<button type="button" class="lib-btn lib-icon" data-lib-copy="' + open + '" aria-label="' + esc(kit.fill(S.copyAria, tA)) + '" title="' + esc(S.copyLink) + '">' + icon("link", "size-4") + "</button>") +
      '<button type="button" class="lib-btn lib-icon" data-lib-share="' + open + '" data-title="' + esc(t) + '" aria-label="' + esc(kit.fill(S.shareAria, tA)) + '" title="' + esc(S.share) + '">' + icon("share-2", "size-4") + "</button>";

    return '<article class="lib-card is-fresh' + (d.or ? " is-orphan" : "") + '" id="doc-' + esc(String(d.id).replace(/:/g, "-")) + '">' +
      '<div class="lib-media"><div class="lib-tile" data-tone="' + esc(d.s) + '" aria-hidden="true"><span class="lib-sheet"><span class="lib-sheet-icon">' +
      icon(CFG.catIcons[d.c] || "file-text", "size-6") + '</span><span class="lib-sheet-lines"></span><span class="lib-sheet-ft">' + esc(CFG.ft[ft] || ft) + "</span></span></div>" +
      (th && d.tf ? '<img class="lib-th-blur" src="' + th + '" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">' : "") +
      (th ? "<img" + (d.tf ? ' class="lib-th-fit"' : "") + ' src="' + th + '" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">' : "") +
      (d.n && !d.or ? '<span class="badge-new lib-new">' + esc(S.isNew) + "</span>" : "") + "</div>" +
      '<div class="lib-body"><div class="lib-badges">' + srcBadge(d.s) + '<span class="badge-muted">' + esc(cats[d.c] || d.c) + "</span>" +
      (d.l && d.l !== LANG ? '<span class="badge-muted uppercase" title="' + esc(CFG.langTitle[d.l] || "") + '">' + esc(d.l) + "</span>" : "") + "</div>" +
      '<h3 class="lib-title"' + (!d.o && tl && tl !== LANG ? ' lang="' + esc(tl) + '"' : "") + '><a href="' + open + '" target="_blank" rel="noopener">' + title + "</a></h3>" +
      (d.o ? '<p class="lib-orig"><span class="sr-only">' + esc(S.original) + ": </span><span" + (tl ? ' lang="' + esc(tl) + '"' : "") + ">" + kit.highlight(d.o, terms) + "</span></p>" : "") +
      (d.ev ? '<p class="lib-event">' + icon("calendar-days", "size-3.5") + '<time datetime="' + esc(d.ev) + '">' + esc(kit.fill(S.eventOn, { date: kit.fmtYmd(d.ev, "long") })) + "</time></p>" : "") +
      (meta.length ? '<p class="lib-meta">' + meta.join("") + "</p>" : "") +
      (ref ? '<p class="lib-ref">' + esc(S.foundOn) + ' <a href="' + esc(kit.href(ref[0])) + '" target="_blank" rel="noopener">' + esc(ref[1]) + "</a></p>" : "") +
      (d.or ? '<p class="lib-orphan">' + icon("unlink", "size-3.5") + "<span>" + esc(S.orphan) + "</span></p>" : "") +
      (d.m ? '<p class="lib-auto"><span class="auto-note" title="' + esc(S.autoHelp) + '">' + icon("languages", "size-3") + " " + esc(S.auto) + "</span></p>" : "") +
      '<div class="lib-actions">' + actions + "</div></div></article>";
  }

  function renderPills() {
    var html = "";
    function pill(key, label) {
      return '<button type="button" class="lib-pill" data-rm="' + esc(key) + '" aria-label="' + esc(kit.fill(S.removeFilter, { label: label })) + '">' + esc(label) + icon("x", "size-3.5") + "</button>";
    }
    if (state.q.trim()) html += pill("q", "“" + state.q.trim() + "”");
    if (state.col) html += pill("col", S.collection + ": " + COLS[state.col].label);
    FACETS.forEach(function (f) {
      state[f].forEach(function (v) {
        var chip = chips.filter(function (c) { return c.getAttribute("data-f") === f && c.getAttribute("data-v") === v; })[0];
        var label = chip ? chip.querySelector("span:not([data-n]):not(.lib-dot)").textContent : v;
        html += pill(f + ":" + v, label);
      });
    });
    if (html) html += '<button type="button" class="btn-ghost btn-sm" data-lib-reset>' + esc(clearAllBtn ? clearAllBtn.textContent.trim() : "×") + "</button>";
    activeEl.innerHTML = html;
    activeEl.hidden = !html;
  }

  function renderStatus() {
    var n = list.length;
    var txt = filtered()
      ? (n === 1 ? S.countMatchOne : kit.fill(S.countMatch, { n: num(n) }))
      : (n === 1 ? S.countOne : kit.fill(S.countAll, { n: num(n) }));
    if (statusEl.textContent !== txt) statusEl.textContent = txt;
    var s = Math.min(shown, n);
    shownEl.textContent = kit.fill(S.countShown, { shown: num(s), n: num(n) });
    moreWrap.hidden = n <= shown;
  }

  function renderEmpty() {
    var none = !list.length;
    emptyEl.hidden = !none;
    results.hidden = none;
    if (!none) return;
    var tEl = emptyEl.querySelector("[data-empty-title]"), xEl = emptyEl.querySelector("[data-empty-text]");
    var onlyCol = state.col && !state.q.trim() && !FACETS.some(function (f) { return state[f].size; });
    var reports = onlyCol && state.col === "reports";
    // An empty collection is "not yet", not "no match": show its own icon and color.
    var iconEl = emptyEl.querySelector("[data-empty-icon]"), col = onlyCol ? COLS[state.col] : null;
    if (iconEl) {
      iconEl.innerHTML = icon(col && col.icon ? col.icon : "search-x", "size-6");
      if (col && col.tone) iconEl.setAttribute("data-tone", col.tone); else iconEl.removeAttribute("data-tone");
    }
    if (onlyCol) {
      tEl.textContent = kit.fill(S.colEmptyTitle, { name: COLS[state.col].label });
      xEl.textContent = reports ? S.colEmptyReports : S.colEmptyText;
    } else {
      tEl.textContent = S.noneTitle;
      xEl.textContent = S.noneText;
    }
    // Committee reports come from the committee's Drive: point to the Documents page too.
    var docsLink = emptyEl.querySelector("[data-lib-docs]");
    if (reports && !docsLink && CFG.docsPage) {
      docsLink = document.createElement("a");
      docsLink.className = "btn-ghost btn-sm";
      docsLink.setAttribute("data-lib-docs", "");
      docsLink.href = kit.href(CFG.docsPage);
      docsLink.innerHTML = icon("folder-open", "size-4") + " " + esc(S.colEmptyReportsCta);
      emptyEl.querySelector(".flex").appendChild(docsLink);
    }
    if (docsLink) docsLink.hidden = !reports;
    if (siteSearch) siteSearch.hidden = reports;
  }

  function update(fromLoad) {
    if (!docs) { renderControls(); return; }
    var counts = compute();
    shown = CFG.pageSize;
    // First paint with default state: the server already rendered exactly these cards.
    var keepSsr = fromLoad && !hydrated && !filtered() && effectiveSort() === "new" && results.querySelectorAll(".lib-card").length === Math.min(CFG.pageSize, list.length);
    hydrated = true;
    if (!keepSsr) results.innerHTML = list.slice(0, shown).map(renderCard).join("");
    chips.forEach(function (c) {
      var f = c.getAttribute("data-f"), v = c.getAttribute("data-v"), n = counts[f][v] || 0;
      var el = c.querySelector("[data-n]");
      if (el) el.textContent = num(n);
      c.toggleAttribute("data-zero", !n);
    });
    partialEl.hidden = !partial;
    renderStatus();
    renderEmpty();
    renderControls();
    writeUrl();
  }

  function renderControls() {
    clearBtn.hidden = !input.value;
    if (kbd) kbd.hidden = !!input.value;
    chips.forEach(function (c) { c.setAttribute("aria-pressed", String(state[c.getAttribute("data-f")].has(c.getAttribute("data-v")))); });
    colBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-col") === state.col)); });
    viewBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-view") === state.view)); });
    results.setAttribute("data-view", state.view);
    if (relOpt) relOpt.hidden = !state.q.trim();
    if (sortSel) sortSel.value = effectiveSort();
    var nActive = FACETS.reduce(function (a, f) { return a + state[f].size; }, 0);
    if (facetsN) { facetsN.textContent = nActive; facetsN.hidden = !nActive; }
    if (clearAllBtn) clearAllBtn.hidden = !filtered();
    if (docs) renderPills();
  }

  function reset() {
    state.q = ""; input.value = ""; state.col = ""; state.sort = "";
    FACETS.forEach(function (f) { state[f].clear(); });
    update();
  }

  function revealResults() {
    var h = $("lib-results-h");
    if (!h) return;
    var top = h.getBoundingClientRect().top;
    if (top > window.innerHeight * 0.7 || top < 0) {
      var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      h.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    }
  }

  /* ---------- events ---------- */
  var onType = kit.debounce(function () { state.q = input.value; if (state.sort === "rel" && !state.q.trim()) state.sort = ""; update(); }, 150);
  input.addEventListener("input", function () { clearBtn.hidden = !input.value; if (kbd) kbd.hidden = !!input.value; onType(); });
  input.addEventListener("keydown", function (e) { if (e.key === "Escape" && input.value) { e.preventDefault(); input.value = ""; state.q = ""; update(); } });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    state.q = input.value;
    update();
    if (window.matchMedia("(pointer: coarse)").matches) input.blur();
    revealResults();
  });
  clearBtn.addEventListener("click", function () { input.value = ""; state.q = ""; if (state.sort === "rel") state.sort = ""; update(); input.focus(); });

  chips.forEach(function (c) {
    c.addEventListener("click", function () {
      var set = state[c.getAttribute("data-f")], v = c.getAttribute("data-v");
      if (set.has(v)) set.delete(v); else set.add(v);
      update();
    });
  });
  colBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      var k = b.getAttribute("data-col");
      state.col = state.col === k ? "" : k;
      update();
      if (state.col) revealResults();
    });
  });
  if (sortSel) sortSel.addEventListener("change", function () { state.sort = sortSel.value; update(); });
  viewBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      state.view = b.getAttribute("data-view");
      try { localStorage.setItem("lib-view", state.view); } catch (e) {}
      renderControls();
      writeUrl();
    });
  });
  if (facetsToggle) facetsToggle.addEventListener("click", function () {
    var open = facetsEl.classList.toggle("hidden") === false;
    facetsToggle.setAttribute("aria-expanded", String(open));
  });
  moreBtn.addEventListener("click", function () {
    if (!docs) return;
    var before = shown;
    shown += CFG.pageSize;
    results.insertAdjacentHTML("beforeend", list.slice(before, shown).map(renderCard).join(""));
    renderStatus();
    announce(shownEl.textContent);
  });
  document.addEventListener("click", function (e) {
    var rm = e.target.closest("[data-rm]"), rs = e.target.closest("[data-lib-reset]");
    if (rs || (e.target.closest("#lib-clear-all"))) { e.preventDefault(); reset(); return; }
    if (!rm) return;
    var k = rm.getAttribute("data-rm");
    if (k === "q") { state.q = ""; input.value = ""; if (state.sort === "rel") state.sort = ""; }
    else if (k === "col") state.col = "";
    else { var i = k.indexOf(":"); state[k.slice(0, i)].delete(k.slice(i + 1)); }
    update();
    input.focus({ preventScroll: true });
  });
  $("lib-retry").addEventListener("click", load);
  kit.slashFocus(input);
  window.addEventListener("popstate", function () { readUrl(); input.value = state.q; update(); });

  /* ---------- start ---------- */
  readUrl();
  input.value = state.q;
  renderControls();
  load();
})();
