/* NETA 65 Grapevine / La Viña — Published Writers page (/published/, /es/published/).
   ------------------------------------------------------------------
   src/pages/published.njk renders EVERY story of the longest window (spotlight.list_days)
   as a card with data-scope / data-pub / data-date / data-s (normalized search text), and
   shows the default view (Area 65 + first window) without JavaScript. This script filters
   those same cards in place:
     - where writers are from: Area 65 | All of Texas (Area 65 first) | Everyone
     - period: the list_days windows (60 | 90), recounted from TODAY in America/Chicago
       with each card's data-date (extra.pub_date), so a page built yesterday is still exact
     - magazine: All | Grapevine | La Viña, and a search box (writer, city, county, title)
     - live counts on every choice, an aria-live result line, empty states with ways to
       widen the list, a "more Texas writers" nudge, "Show all N stories" per group
     - shareable URLs: ?scope=texas&days=90&pub=lv&q=dallas (defaults are left out)
   Strings and settings come from <script id="pw-config"> (eleventy/filters/published.js). */
(function () {
  "use strict";
  var cfgEl = document.getElementById("pw-config");
  var form = document.getElementById("pw-form");
  var results = document.getElementById("pw-results");
  if (!cfgEl || !form || !results) return;
  var C;
  try { C = JSON.parse(cfgEl.textContent); } catch (e) { return; }
  var S = C.s || {};
  var LIST = (C.listDays || [60, 90]).map(Number);
  var LIMIT = Number(C.limit) || 24;
  var SCOPES = { neta65: ["neta65"], texas: ["neta65", "texas"], all: ["neta65", "texas", "other", "unknown"] };
  var ALIAS = { area65: "neta65", "area-65": "neta65", neta: "neta65", tx: "texas", everyone: "all", todos: "all" };
  var PUBS = ["all", "gv", "lv"];
  var DEF = { scope: SCOPES[C.defScope] ? C.defScope : "neta65", days: LIST.indexOf(Number(C.defDays)) !== -1 ? Number(C.defDays) : LIST[0], pub: "all", q: "" };
  var $ = function (id) { return document.getElementById(id); };
  var qInput = $("pw-q"), qClear = $("pw-q-clear"), statusEl = $("pw-status"), resetBtn = $("pw-reset");
  var emptyEl = $("pw-empty"), emptyTitle = $("pw-empty-title"), nudge = $("pw-nudge"), nudgeText = $("pw-nudge-text");

  var numFmt = null;
  try { numFmt = new Intl.NumberFormat(C.lang === "es" ? "es-US" : "en-US"); } catch (e) {}
  function num(n) { return numFmt ? numFmt.format(n) : String(n); }
  function fill(tpl, vars) {
    return String(tpl || "").replace(/\{(\w+)\}/g, function (m, k) { return vars[k] !== undefined && vars[k] !== null ? String(vars[k]) : m; });
  }
  function stories(n) { return fill(n === 1 ? S["n_stories.one"] : S["n_stories.other"], { n: num(n) }); }

  /* Same normalization as pwNorm() in eleventy/filters/published.js */
  var NON_WORD;
  try { NON_WORD = new RegExp("[^\\p{L}\\p{N}]+", "gu"); } catch (e) { NON_WORD = /[^a-z0-9\u00c0-\u024f]+/g; }
  function norm(s) {
    var t = String(s || "").toLowerCase();
    if (t.normalize) t = t.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return t.replace(NON_WORD, " ").trim();
  }

  /* ---------- dates: today in America/Chicago, windows as YYYY-MM-DD strings ---------- */
  function todayYmd() {
    var build = /^\d{4}-\d{2}-\d{2}$/.test(C.today || "") ? C.today : "";
    try {
      var parts = new Intl.DateTimeFormat("en-US", { timeZone: C.tz || "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
      var get = function (t) { for (var i = 0; i < parts.length; i++) if (parts[i].type === t) return parts[i].value; return ""; };
      var ymd = get("year") + "-" + get("month") + "-" + get("day");
      // A device clock set to a day BEFORE the build cannot be right: the build day wins then.
      if (/^\d{4}-\d{2}-\d{2}$/.test(ymd)) return build && build > ymd ? build : ymd;
    } catch (e) {}
    return build;
  }
  function minusDays(ymd, n) {
    var d = new Date(ymd + "T12:00:00Z");
    d.setUTCDate(d.getUTCDate() - n);
    return d.toISOString().slice(0, 10);
  }
  var cutoff = {};
  function recountWindows() {
    var t = todayYmd();
    LIST.forEach(function (d) { cutoff[d] = t ? minusDays(t, d) : "0000-00-00"; });
  }

  /* ---------- the cards (server-rendered) ---------- */
  var groups = [];
  Array.prototype.forEach.call(results.querySelectorAll("[data-group]"), function (sec) {
    var g = {
      key: sec.getAttribute("data-group"), el: sec,
      n: sec.querySelector("[data-group-n]"),
      more: sec.querySelector(".pw-showall"),
      moreBtn: sec.querySelector("[data-pw-showall]"),
      cards: [],
    };
    Array.prototype.forEach.call(sec.querySelectorAll(".pw-card"), function (el) {
      g.cards.push({ el: el, scope: el.getAttribute("data-scope"), pub: el.getAttribute("data-pub"), date: el.getAttribute("data-date") || "", s: " " + (el.getAttribute("data-s") || "") + " " });
    });
    groups.push(g);
  });
  var allCards = [];
  groups.forEach(function (g) { allCards = allCards.concat(g.cards); });

  /* ---------- state ---------- */
  var state = { scope: DEF.scope, days: DEF.days, pub: DEF.pub, q: "" };
  var expanded = {};

  function tokens(q) { return norm(q).split(" ").filter(Boolean); }
  function matches(c, st, toks) {
    if (SCOPES[st.scope].indexOf(c.scope) === -1) return false;
    if (c.date < cutoff[st.days]) return false;
    if (st.pub !== "all" && c.pub !== st.pub) return false;
    for (var i = 0; i < toks.length; i++) {
      // every word must appear; a word matches at the start of a word ("dal" → Dallas)
      if (c.s.indexOf(" " + toks[i]) === -1) return false;
    }
    return true;
  }
  function count(over) {
    var st = { scope: state.scope, days: state.days, pub: state.pub, q: state.q };
    for (var k in over) st[k] = over[k];
    var toks = tokens(st.q), n = 0;
    for (var i = 0; i < allCards.length; i++) if (matches(allCards[i], st, toks)) n++;
    return n;
  }

  /* ---------- URL state ---------- */
  function readUrl() {
    var p;
    try { p = new URLSearchParams(location.search); } catch (e) { return; }
    var sc = String(p.get("scope") || "").toLowerCase();
    sc = ALIAS[sc] || sc;
    if (SCOPES[sc]) state.scope = sc;
    var d = parseInt(p.get("days"), 10);
    if (LIST.indexOf(d) !== -1) state.days = d;
    var pub = String(p.get("pub") || "").toLowerCase();
    if (PUBS.indexOf(pub) !== -1) state.pub = pub;
    var q = p.get("q");
    if (q) state.q = q.slice(0, 100);
  }
  function writeUrl() {
    if (!window.history || !history.replaceState) return;
    var p;
    try { p = new URLSearchParams(location.search); } catch (e) { return; }
    var set = function (k, v, def) { if (v !== def && v !== "" && v !== null) p.set(k, v); else p.delete(k); };
    set("scope", state.scope, DEF.scope);
    set("days", String(state.days), String(DEF.days));
    set("pub", state.pub, "all");
    set("q", state.q.trim(), "");
    var qs = p.toString();
    try { history.replaceState(history.state, "", location.pathname + (qs ? "?" + qs : "") + location.hash); } catch (e) {}
  }

  /* ---------- render ---------- */
  function setCount(el, n) {
    if (!el) return;
    var nEl = el.querySelector("[data-n]");
    if (nEl) nEl.textContent = num(n);
  }
  function syncControls() {
    Array.prototype.forEach.call(form.querySelectorAll("input[type=radio]"), function (r) {
      var v = r.name === "days" ? Number(r.value) : r.value;
      r.checked = state[r.name] === v;
      var n = count(r.name === "days" ? { days: Number(r.value) } : r.name === "scope" ? { scope: r.value } : { pub: r.value });
      var chip = r.closest(".pw-chip");
      setCount(chip, n);
      if (chip) chip.classList.toggle("is-zero", n === 0);
    });
    if (qInput && qInput.value !== state.q) qInput.value = state.q;
    if (qClear) qClear.hidden = !state.q;
  }

  function statusText(total) {
    if (!total) return emptyTitleText();
    var t = fill(S["status." + state.scope + (total === 1 ? ".one" : ".other")], { n: num(total), days: state.days });
    if (state.pub !== "all") t += fill(S["status.pub"], { pub: (C.pubs || {})[state.pub] || state.pub });
    if (state.q.trim()) t += fill(S["status.q"], { q: state.q.trim() });
    return t;
  }
  function emptyTitleText() {
    if (state.pub !== "all" || state.q.trim()) return S["empty.filtered"];
    return fill(S["empty." + state.scope], { days: state.days });
  }

  function render(opts) {
    opts = opts || {};
    var toks = tokens(state.q), total = 0;
    groups.forEach(function (g) {
      var shown = 0, matched = 0;
      g.cards.forEach(function (c) {
        var ok = matches(c, state, toks);
        if (ok) matched++;
        var vis = ok && (expanded[g.key] || shown < LIMIT);
        if (vis) shown++;
        c.el.hidden = !vis;
      });
      total += matched;
      g.el.hidden = matched === 0;
      if (g.n) g.n.textContent = stories(matched);
      var extra = !expanded[g.key] && matched > LIMIT;
      if (g.more) g.more.hidden = !extra;
      if (extra && g.moreBtn) {
        var lab = g.moreBtn.querySelector("[data-label]");
        if (lab) lab.textContent = fill(S.show_all, { n: num(matched) });
      }
    });

    syncControls();

    // empty state + ways to widen the list
    if (emptyEl) {
      emptyEl.hidden = total > 0;
      if (!total) {
        if (emptyTitle) emptyTitle.textContent = emptyTitleText();
        Array.prototype.forEach.call(emptyEl.querySelectorAll("[data-pw-set]"), function (b) {
          var key = b.getAttribute("data-pw-set"), v = b.getAttribute("data-v"), n = 0, show = false;
          if (key === "days") { v = Number(v); show = v > state.days; if (show) n = count({ days: v }); }
          else if (v === "texas") { show = state.scope === "neta65"; if (show) n = count({ scope: "texas" }); }
          else if (v === "all") { show = state.scope !== "all"; if (show) n = count({ scope: "all" }); }
          b.hidden = !show || n === 0;
          setCount(b, n);
        });
        var clr = emptyEl.querySelector("[data-pw-clear]");
        if (clr) clr.hidden = !(state.pub !== "all" || state.q.trim());
      }
    }

    // "Also in the last 60 days: 6 stories by writers from other parts of Texas"
    if (nudge) {
      var next = state.scope === "neta65" ? "texas" : state.scope === "texas" ? "all" : "";
      var more = next && total ? count({ scope: next }) - total : 0;
      nudge.hidden = !(more > 0);
      if (more > 0) {
        if (nudgeText) nudgeText.textContent = fill(S["more." + next], { days: state.days, stories: stories(more) });
        var nb = nudge.querySelector("[data-pw-nudge]");
        if (nb) {
          nb.setAttribute("data-v", next);
          var nl = nb.querySelector("[data-label]");
          if (nl) nl.textContent = S["btn." + next];
        }
      }
    }

    if (resetBtn) resetBtn.hidden = state.scope === DEF.scope && state.days === DEF.days && state.pub === DEF.pub && !state.q.trim();
    var text = statusText(total);
    if (statusEl && statusEl.textContent !== text) statusEl.textContent = text;
    if (!opts.keepUrl) writeUrl();
  }

  /* ---------- events ---------- */
  function focusChoice(name) {
    var r = form.querySelector('input[name="' + name + '"]:checked');
    if (r) r.focus({ preventScroll: false });
  }
  function change(over, focusName) {
    for (var k in over) state[k] = over[k];
    expanded = {};
    render();
    if (focusName) focusChoice(focusName);
  }

  form.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || t.type !== "radio") return;
    var v = t.name === "days" ? Number(t.value) : t.value;
    if (t.name === "scope" && SCOPES[v]) change({ scope: v });
    else if (t.name === "days" && LIST.indexOf(v) !== -1) change({ days: v });
    else if (t.name === "pub" && PUBS.indexOf(v) !== -1) change({ pub: v });
  });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (qInput) { clearTimeout(typing); change({ q: qInput.value.slice(0, 100) }); }
  });

  var typing = null;
  if (qInput) {
    qInput.addEventListener("input", function () {
      if (qClear) qClear.hidden = !qInput.value;
      clearTimeout(typing);
      typing = setTimeout(function () { change({ q: qInput.value.slice(0, 100) }); }, 300);
    });
    qInput.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && qInput.value) { e.preventDefault(); clearTimeout(typing); qInput.value = ""; change({ q: "" }); }
    });
  }
  if (qClear) qClear.addEventListener("click", function () {
    clearTimeout(typing);
    if (qInput) qInput.value = "";
    change({ q: "" });
    if (qInput) qInput.focus();
  });
  if (resetBtn) resetBtn.addEventListener("click", function () {
    clearTimeout(typing);
    if (qInput) qInput.value = "";
    change({ scope: DEF.scope, days: DEF.days, pub: DEF.pub, q: "" }, "scope");
  });

  results.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("button") : null;
    if (!b) return;
    if (b.hasAttribute("data-pw-set")) {
      var key = b.getAttribute("data-pw-set"), v = b.getAttribute("data-v");
      if (key === "days") change({ days: Number(v) }, "days");
      else if (SCOPES[v]) change({ scope: v }, "scope");
    } else if (b.hasAttribute("data-pw-clear")) {
      clearTimeout(typing);
      if (qInput) qInput.value = "";
      change({ pub: "all", q: "" });
      if (qInput) qInput.focus();
    } else if (b.hasAttribute("data-pw-nudge")) {
      var next = b.getAttribute("data-v");
      if (SCOPES[next]) change({ scope: next }, "scope");
    } else if (b.hasAttribute("data-pw-showall")) {
      var key2 = b.getAttribute("data-pw-showall");
      var g = null;
      for (var i = 0; i < groups.length; i++) if (groups[i].key === key2) g = groups[i];
      expanded[key2] = true;
      render({ keepUrl: true });
      // keep keyboard users in place: move to the first card that just appeared
      if (g) {
        var vis = g.cards.filter(function (c) { return !c.el.hidden; });
        var first = vis[LIMIT] && vis[LIMIT].el.querySelector("a");
        if (first) first.focus();
      }
    }
  });

  /* ---------- start ---------- */
  recountWindows();
  readUrl();
  // the controls start disabled (useless without this script); switch them on
  Array.prototype.forEach.call(form.querySelectorAll("[data-pw-ctl]"), function (el) { el.disabled = false; });
  render();
  // A tab left open past midnight: recount the windows when the page is shown again.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    var before = JSON.stringify(cutoff);
    recountWindows();
    if (JSON.stringify(cutoff) !== before) render({ keepUrl: true });
  });
})();
