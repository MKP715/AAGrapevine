/* "GVR / RLV 101" — /orientation/ (hub) and /orientation/<id>/ (a lesson). Plain JavaScript, no
   dependencies (loaded with `defer` after app.js). Everything here is an extra: without it the lessons,
   the self-check answers (a "See the answer" disclosure) and the printed handout all still work.

   1. Progress — "lessons done on this device": a list of lesson ids in localStorage (key below), and
      nothing else. Every read and write is wrapped in try/catch (private windows, blocked storage):
      the pages then simply show no progress. Drawn into [data-o101-card=<id>] (the Done badge, the
      card's call to action, the lesson list's check mark), [data-o101-progress] (count + bar + "Start
      over") and the hub's resume button [data-o101-resume] ("Continue: lesson 3").
   2. Self-check (lesson pages) — [data-o101-quiz]: feedback on the answer chosen (a click or tap, Space
      or Enter, or leaving the question — not each arrow-key move); when all three are right the lesson
      is saved as done. Nothing is sent anywhere.
   3. Slide show (hub) — #o101-deck-tpl is cloned into a full-screen dialog by "Present as slides"
      [data-o101-present] or on load at ?slides. #slide-N (resume) or #lesson-<id> picks the first slide.
      Keys: → ↓ Space PageDown next · ← ↑ Shift+Space PageUp back · Home / End · F full screen ·
      Esc exits. A slide taller than the screen (larger text, a phone, a zoomed browser) scrolls on
      its own: ↓ Space PageDown (↑ Shift+Space PageUp) scroll it first and turn the page at its end. Click or tap the right two thirds (next) or the left third (back); swipe on touch.
      The rest of the page is inert while it is open; a live region reads "Slide 3 of 45: <title>";
      focus returns to the button that opened it.
   4. Print — [data-o101-print] prints the page (the hub prints as the handout, see orientation.css). */
(function () {
  "use strict";
  var KEY = "gv-orientation-v1";

  /* ---------------- 1. Progress ---------------- */
  function loadDone() {
    try {
      var v = JSON.parse(localStorage.getItem(KEY) || "null");
      return v && Array.isArray(v.done) ? v.done.filter(function (x) { return typeof x === "string"; }) : [];
    } catch (e) { return []; }
  }
  function saveDone(list) {
    try {
      if (list.length) localStorage.setItem(KEY, JSON.stringify({ v: 1, done: list }));
      else localStorage.removeItem(KEY);
    } catch (e) { /* storage blocked: progress just isn't kept */ }
  }
  function markDone(id) {
    var d = loadDone();
    if (d.indexOf(id) < 0) { d.push(id); saveDone(d); }
    render();
  }

  function render() {
    var done = loadDone();
    var cards = document.querySelectorAll("[data-o101-card]");
    var ids = [];
    Array.prototype.forEach.call(cards, function (c) {
      var id = c.getAttribute("data-o101-card");
      if (ids.indexOf(id) < 0) ids.push(id);
      var on = done.indexOf(id) >= 0;
      c.classList.toggle("is-done", on);
      c.querySelectorAll("[data-o101-done], [data-o101-done-sr]").forEach(function (el) { el.hidden = !on; });
      var cta = c.querySelector("[data-o101-cta]");
      if (cta) {
        if (!cta.hasAttribute("data-t-start")) cta.setAttribute("data-t-start", cta.textContent);
        cta.textContent = on ? cta.getAttribute("data-t-review") : cta.getAttribute("data-t-start");
      }
    });
    var n = ids.filter(function (id) { return done.indexOf(id) >= 0; }).length;

    document.querySelectorAll("[data-o101-progress]").forEach(function (box) {
      var total = Number(box.getAttribute("data-total")) || ids.length;
      var all = total > 0 && n >= total;
      var count = box.querySelector("[data-o101-count]") || box;
      var txt = all ? box.getAttribute("data-t-all") : (box.getAttribute("data-t") || "").replace("{n}", n).replace("{total}", total);
      if (count.textContent !== txt) count.textContent = txt;
      var bar = box.querySelector("[data-o101-bar]");
      if (bar) bar.style.width = (total ? Math.round((n / total) * 100) : 0) + "%";
      var pb = box.querySelector("[role=progressbar]");
      if (pb) pb.setAttribute("aria-valuenow", String(n));
      var reset = box.querySelector("[data-o101-reset]");
      if (reset) reset.hidden = n === 0;
    });

    // Hub: "Start lesson 1" → "Continue: lesson 3" (the first lesson not done) → "Review lesson 1"
    var resume = document.querySelector("[data-o101-resume]");
    if (resume && cards.length) {
      var label = resume.querySelector("[data-o101-resume-label]");
      if (label && !label.hasAttribute("data-t-start")) label.setAttribute("data-t-start", label.textContent);
      if (!resume.hasAttribute("data-href-start")) resume.setAttribute("data-href-start", resume.getAttribute("href"));
      var next = null;
      Array.prototype.some.call(cards, function (c) { if (done.indexOf(c.getAttribute("data-o101-card")) < 0) { next = c; return true; } return false; });
      if (n === 0 || !label) {
        if (label) label.textContent = label.getAttribute("data-t-start");
        resume.setAttribute("href", resume.getAttribute("data-href-start"));
      } else if (next) {
        label.textContent = resume.getAttribute("data-t-continue").replace("{n}", next.getAttribute("data-n"));
        // the card's own link (the build gave it the site's path prefix; a data-* value would not have it)
        var link = next.querySelector("a[href]");
        if (link) resume.setAttribute("href", link.getAttribute("href"));
      } else {
        label.textContent = resume.getAttribute("data-t-review");
        resume.setAttribute("href", resume.getAttribute("data-href-start"));
      }
    }
  }

  document.addEventListener("click", function (e) {
    var reset = e.target.closest && e.target.closest("[data-o101-reset]");
    if (reset) {
      saveDone([]);
      render();
      var box = reset.closest("[data-o101-progress]");
      var focusTo = box && box.querySelector("[data-o101-count]");
      if (focusTo) { focusTo.setAttribute("tabindex", "-1"); focusTo.focus(); } // the button just disappeared
      return;
    }
    var pr = e.target.closest && e.target.closest("[data-o101-print]");
    if (pr) { window.print(); }
    // A lesson page on a phone: "All 6 lessons" unfolds the lesson list (orientation.css)
    var tb = e.target.closest && e.target.closest("[data-o101-toc-btn]");
    if (tb) {
      var box = tb.closest("[data-o101-toc]");
      var open = !(box && box.classList.contains("is-open"));
      if (box) box.classList.toggle("is-open", open);
      tb.setAttribute("aria-expanded", String(open));
    }
  });
  // Another tab finished a lesson: redraw.
  window.addEventListener("storage", function (e) { if (e.key === KEY || e.key === null) render(); });

  /* ---------------- 2. Self-check ---------------- */
  function icon(ok) {
    return ok
      ? '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>'
      : '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>';
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function initQuiz(quiz) {
    var lesson = quiz.getAttribute("data-lesson");
    var qs = quiz.querySelectorAll("[data-o101-q]");
    var result = quiz.querySelector("[data-o101-result]");
    var right = {};
    var wasDone = loadDone().indexOf(lesson) >= 0;

    function check() {
      var n = 0;
      Array.prototype.forEach.call(qs, function (q, i) { if (right[i]) n++; });
      if (n === qs.length && qs.length) {
        if (result) result.innerHTML = icon(true) + "<span>" + esc(quiz.getAttribute("data-t-done")) + "</span>";
        markDone(lesson);
      } else if (result && !wasDone) {
        result.textContent = "";
      }
    }

    /* An answer is graded when the visitor chooses it: a click or tap, Space or Enter on it, or
       leaving the question with it selected. Moving through the answers with the arrow keys (native
       radios select as they move) only selects — so keyboard and screen-reader users can hear every
       answer before choosing, without "Not quite" / "Right!" giving the answer away. */
    Array.prototype.forEach.call(qs, function (q, i) {
      var answer = q.getAttribute("data-answer");
      var fb = q.querySelector("[data-o101-fb]");
      var arrowed = false, graded = null;
      function clearMarks() {
        q.classList.remove("is-right");
        q.querySelectorAll(".o101-opt").forEach(function (lab) { lab.classList.remove("is-right", "is-wrong"); });
      }
      function grade(input) {
        if (!input || input.type !== "radio" || !input.checked) return;
        if (graded === input.value) return; // already said: don't read it out again
        graded = input.value;
        var ok = input.value === answer;
        right[i] = ok;
        clearMarks();
        q.classList.toggle("is-right", ok);
        var lab = input.closest(".o101-opt");
        if (lab) lab.classList.add(ok ? "is-right" : "is-wrong");
        if (fb) {
          fb.className = "o101-fb " + (ok ? "is-right" : "is-wrong");
          fb.innerHTML = icon(ok) + "<span><strong>" + esc(quiz.getAttribute(ok ? "data-t-right" : "data-t-wrong")) + "</strong>" +
            (ok ? " " + esc(q.getAttribute("data-why") || "") : "") + "</span>";
        }
        check();
      }
      q.addEventListener("keydown", function (e) {
        arrowed = /^(Arrow(Up|Down|Left|Right))$/.test(e.key);
      });
      q.addEventListener("pointerdown", function () { arrowed = false; });
      q.addEventListener("keyup", function (e) {
        if ((e.key === " " || e.key === "Enter") && e.target && e.target.type === "radio") grade(e.target);
      });
      q.addEventListener("change", function (e) {
        var input = e.target;
        if (!input || input.type !== "radio") return;
        if (arrowed) {
          // just moved here: selected, not graded yet (a previous grade no longer applies)
          arrowed = false;
          if (graded !== null && graded !== input.value) {
            graded = null; right[i] = false; clearMarks();
            if (fb) { fb.className = "o101-fb"; fb.textContent = ""; }
            check();
          }
          return;
        }
        grade(input);
      });
      q.addEventListener("focusout", function (e) {
        if (e.relatedTarget && q.contains(e.relatedTarget)) return;
        grade(q.querySelector("input[type=radio]:checked"));
      });
    });
  }

  /* ---------------- 3. Slide show ---------------- */
  var deck = null, slides = [], cur = 0, lastFocus = null, inerted = [], startUrl = "";

  function tplEl() { return document.getElementById("o101-deck-tpl"); }

  function indexFromHash(hash) {
    var m = /^#slide-(\d+)$/.exec(hash || "");
    if (m) return Math.max(0, Number(m[1]) - 1);
    m = /^#lesson-([a-z0-9-]+)$/.exec(hash || "");
    if (m) {
      for (var i = 0; i < slides.length; i++) if (slides[i].getAttribute("data-lesson") === m[1]) return i;
    }
    return 0;
  }

  function slideTitle(s) {
    var h = s && s.querySelector("h2");
    return h ? h.textContent.replace(/\s+/g, " ").trim() : "";
  }

  function setUrl(i) {
    try {
      var u = new URL(location.href);
      u.searchParams.set("slides", "");
      var q = u.search.replace(/slides=(&|$)/, "slides$1");
      history.replaceState(history.state, "", u.pathname + q + "#slide-" + (i + 1));
    } catch (e) {}
  }

  function go(i, dir) {
    if (!deck || !slides.length) return;
    i = Math.max(0, Math.min(slides.length - 1, i));
    var prev = slides[cur];
    var hadFocus = prev && prev.contains(document.activeElement);
    if (prev && prev !== slides[i]) { prev.hidden = true; prev.classList.remove("is-in", "is-in-back"); }
    var s = slides[i];
    s.hidden = false;
    if (s !== prev) s.scrollTop = 0; // a slide that scrolls (larger text, phones) starts at its top
    s.classList.remove("is-in", "is-in-back");
    if (dir) { void s.offsetWidth; s.classList.add(dir < 0 ? "is-in-back" : "is-in"); }
    cur = i;
    var bar = deck.querySelector("[data-o101-deck-bar]");
    if (bar) bar.style.width = ((i + 1) / slides.length) * 100 + "%";
    var num = deck.querySelector("[data-o101-n]");
    if (num) num.textContent = String(i + 1);
    var pb = deck.querySelector("[data-o101-prev]"), nb = deck.querySelector("[data-o101-next]");
    if (pb) pb.disabled = i === 0;
    if (nb) nb.disabled = i === slides.length - 1;
    // A disabled button can't hold focus: move it to the dialog so keys keep working.
    if (hadFocus || (document.activeElement && document.activeElement.disabled)) deck.focus({ preventScroll: true });
    var live = deck.querySelector("[data-o101-live]");
    if (live) live.textContent = (deck.getAttribute("data-t-live") || "").replace("{n}", i + 1).replace("{total}", slides.length).replace("{title}", slideTitle(s));
    setUrl(i);
  }

  function fsEl() { return document.fullscreenElement || document.webkitFullscreenElement || null; }
  function toggleFull() {
    if (!deck) return;
    try {
      if (fsEl()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      else (deck.requestFullscreen || deck.webkitRequestFullscreen).call(deck);
    } catch (e) {}
  }
  function syncFull() {
    if (!deck) return;
    var btn = deck.querySelector("[data-o101-full]");
    var lbl = btn && btn.querySelector("[data-o101-full-label]");
    if (lbl) lbl.textContent = deck.getAttribute(fsEl() ? "data-t-full-exit" : "data-t-full");
  }

  function reveal(btn) {
    var box = btn.closest("[data-o101-check]");
    if (!box) return;
    var open = btn.getAttribute("aria-expanded") !== "true";
    btn.setAttribute("aria-expanded", String(open));
    box.classList.toggle("is-revealed", open);
    box.querySelectorAll("[data-o101-why], [data-o101-ok]").forEach(function (el) { el.hidden = !open; });
    var lbl = btn.querySelector("[data-o101-reveal-label]");
    if (lbl) lbl.textContent = deck.getAttribute(open ? "data-t-hide" : "data-t-show");
  }

  function interactive(el) {
    return !!(el && el.closest && el.closest("a, button, input, select, textarea, label, summary, [contenteditable]"));
  }

  /* A slide that is taller than the stage and not yet at that end: scroll it (a few lines for an
     arrow, most of a screen for Space / Page keys) instead of turning the page. true = scrolled. */
  function scrollSlide(dir, small) {
    var s = slides[cur];
    if (!s || s.scrollHeight <= s.clientHeight + 2) return false;
    var atEnd = dir > 0 ? s.scrollTop + s.clientHeight >= s.scrollHeight - 2 : s.scrollTop <= 1;
    if (atEnd) return false;
    var step = small ? Math.max(40, s.clientHeight * 0.15) : s.clientHeight * 0.85;
    var reduce = window.GV && window.GV.reducedMotion ? window.GV.reducedMotion() : false;
    try { s.scrollBy({ top: dir * step, behavior: reduce ? "auto" : "smooth" }); } catch (err) { s.scrollTop += dir * step; }
    return true;
  }

  function onKey(e) {
    if (!deck) return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    var k = e.key;
    var onControl = interactive(e.target);
    if ((k === " " || k === "Enter") && onControl) return; // let buttons and links do their job
    var fwd = k === "ArrowDown" || k === "PageDown" || (k === " " && !e.shiftKey);
    var back = k === "ArrowUp" || k === "PageUp" || (k === " " && e.shiftKey);
    if ((fwd || back) && scrollSlide(fwd ? 1 : -1, k === "ArrowDown" || k === "ArrowUp")) { e.preventDefault(); return; }
    if (k === "ArrowRight" || fwd) { e.preventDefault(); go(cur + 1, 1); }
    else if (k === "ArrowLeft" || back) { e.preventDefault(); go(cur - 1, -1); }
    else if (k === "Home") { e.preventDefault(); go(0, -1); }
    else if (k === "End") { e.preventDefault(); go(slides.length - 1, 1); }
    else if (k === "f" || k === "F") { e.preventDefault(); toggleFull(); }
    else if (k === "Escape") { e.preventDefault(); if (fsEl()) toggleFull(); else closeDeck(); }
    else if (k === "Tab") {
      // Keep Tab inside the dialog (the page behind is inert, but the browser UI is not).
      var f = Array.prototype.filter.call(deck.querySelectorAll("a[href], button:not([disabled])"), function (x) { return !x.closest("[hidden]"); });
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === deck)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  var swipe = null, swallowClick = false;
  function onPointerDown(e) { if (e.pointerType !== "mouse") swipe = { x: e.clientX, y: e.clientY }; }
  function onPointerUp(e) {
    if (!swipe) return;
    var dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
    swipe = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.3) { swallowClick = true; go(cur + (dx < 0 ? 1 : -1), dx < 0 ? 1 : -1); }
  }
  function onStageClick(e) {
    if (swallowClick) { swallowClick = false; return; }
    if (interactive(e.target)) return;
    var sel = window.getSelection && window.getSelection();
    if (sel && String(sel).length) return;
    var r = e.currentTarget.getBoundingClientRect();
    if (e.clientX - r.left < r.width / 3) go(cur - 1, -1); else go(cur + 1, 1);
  }
  function onDeckClick(e) {
    var t = e.target;
    if (t.closest("[data-o101-next]")) go(cur + 1, 1);
    else if (t.closest("[data-o101-prev]")) go(cur - 1, -1);
    else if (t.closest("[data-o101-full]")) toggleFull();
    else if (t.closest("[data-o101-exit]")) closeDeck();
    else if (t.closest("[data-o101-reveal]")) reveal(t.closest("[data-o101-reveal]"));
  }
  function onHash() { if (deck) go(indexFromHash(location.hash), 0); }

  function openDeck(hash) {
    var tpl = tplEl();
    if (deck || !tpl) return;
    lastFocus = document.activeElement;
    if (!startUrl) startUrl = location.pathname + location.search.replace(/[?&]slides(=[^&]*)?/, "").replace(/^&/, "?") + (/^#(slide-|lesson-)/.test(location.hash) ? "" : location.hash);
    deck = tpl.content.firstElementChild.cloneNode(true);
    document.body.appendChild(deck);
    inerted = Array.prototype.filter.call(document.body.children, function (el) {
      return el !== deck && !/^(SCRIPT|TEMPLATE|STYLE)$/.test(el.tagName) && !el.hasAttribute("inert");
    });
    inerted.forEach(function (el) { el.setAttribute("inert", ""); });
    document.documentElement.classList.add("o101-presenting");
    slides = Array.prototype.slice.call(deck.querySelectorAll("[data-o101-slide]"));
    var fb = deck.querySelector("[data-o101-full]");
    if (fb && !(deck.requestFullscreen || deck.webkitRequestFullscreen)) fb.hidden = true;
    deck.addEventListener("click", onDeckClick);
    var stage = deck.querySelector("[data-o101-stage]");
    if (stage) {
      stage.addEventListener("click", onStageClick);
      stage.addEventListener("pointerdown", onPointerDown);
      stage.addEventListener("pointerup", onPointerUp);
      stage.addEventListener("pointercancel", function () { swipe = null; });
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", syncFull);
    document.addEventListener("webkitfullscreenchange", syncFull);
    window.addEventListener("hashchange", onHash);
    cur = 0;
    go(indexFromHash(hash), 0);
    deck.focus({ preventScroll: true });
    syncFull();
  }

  function closeDeck() {
    if (!deck) return;
    try { if (fsEl()) (document.exitFullscreen || document.webkitExitFullscreen).call(document); } catch (e) {}
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("fullscreenchange", syncFull);
    document.removeEventListener("webkitfullscreenchange", syncFull);
    window.removeEventListener("hashchange", onHash);
    deck.remove();
    deck = null; slides = [];
    inerted.forEach(function (el) { el.removeAttribute("inert"); });
    inerted = [];
    document.documentElement.classList.remove("o101-presenting");
    try { history.replaceState(history.state, "", startUrl || location.pathname); } catch (e) {}
    startUrl = "";
    var back = lastFocus && document.contains(lastFocus) && lastFocus !== document.body ? lastFocus : document.querySelector("[data-o101-present]");
    if (back && back.focus) back.focus({ preventScroll: true });
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("[data-o101-present]");
    if (!a || !tplEl()) return;
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; // new tab / window: let it load
    e.preventDefault();
    openDeck("");
  });

  /* ---------------- start ---------------- */
  function start() {
    render();
    // From now on a change of the count ("2 of 6 lessons done") is read out; the first draw is not.
    document.querySelectorAll("[data-o101-count]").forEach(function (el) { el.setAttribute("aria-live", "polite"); });
    document.querySelectorAll("[data-o101-quiz]").forEach(initQuiz);
    var hasSlides = false;
    try { hasSlides = new URLSearchParams(location.search).has("slides"); } catch (e) {}
    if (hasSlides && tplEl()) openDeck(location.hash);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
