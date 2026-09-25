/* NETA 65 Grapevine / La Viña — "Works with a weak signal": the installable app, offline use and
   what Data saver does. Loaded on every page by layouts/base.njk (right after app.js, before
   Alpine). Everything here is an extra: without JavaScript, without service workers or with
   storage blocked the site works as before. README → "Install the app, offline use".

   1. Service worker (/sw.js, src/pages/sw.11ty.js): registered after the page has loaded, scope =
      the site's base path. A new version waits until the visitor chooses "Reload" in the small
      "Updated" toast (it then takes over and the page reloads), or until every tab is closed.
   2. The "Aa" panel's #pwa-slot (partials/comfort-panel.njk) — and [data-pwa-slot] anywhere —
      gets: the connection / saved-pages status, "Install app" (Chrome, Edge, Android: the browser's
      own prompt; iPhone/iPad and Safari on a Mac: the steps), "Save key pages for offline" (with
      progress), "See saved pages" and, while images are hidden, "Show images on this page".
      [data-pwa-install] gets the install part only, [data-pwa-save] the save part only.
   3. Offline: a small, dismissible notice under the header ("You're offline — showing saved
      pages"), also when the worker had to answer with a saved copy (slow connection). While
      offline the Data saver effects are on, without changing the visitor's choice.
   4. Data saver — whatever turns <html data-saver="on"> on (the panel's switch, app.js GV.prefs):
      no hero animation (hero-canvas.js), pictures are not fetched (areas/pwa.css hides them and
      images Alpine fills in later get loading="lazy", so the browser never asks for them),
      YouTube previews show a "Load video (uses data)" button instead of a thumbnail and don't
      warm up connections, episode sizes show before playing (.pwa-saver-only). "Show images" undoes
      it for the page being viewed. Every change applies at once (html[data-saver] is watched).
   5. The offline page (/offline/): lists the pages saved on this device (read from the caches).
   Browser storage: only sessionStorage "gvlv-pwa-hide" (notices closed in this tab session). */
(function () {
  "use strict";
  var GV = window.GV || (window.GV = {});
  var root = document.documentElement;
  var LANG = GV.lang || (window.SITE && window.SITE.lang) || root.lang || "en";
  var T = function (en, es) { return LANG === "es" ? es : en; };
  var BASE = GV.base || (window.SITE && window.SITE.base) || "/";
  var at = function (p) { return BASE.replace(/\/$/, "") + p; };
  var HOME = at(LANG === "es" ? "/es/" : "/");
  var OFFLINE_URL = at(LANG === "es" ? "/es/offline/" : "/offline/");
  var SAVED_CACHE = "gvlv-saved-v1", PAGES_CACHE = "gvlv-pages-v1";
  var canSW = !!(navigator.serviceWorker && window.isSecureContext && window.caches);
  var announce = function (m) { if (GV.announce) GV.announce(m); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };

  /* Episode sizes (Listen): 73517462 → "70.1 MB" — the same units as the build's fileSize filter. */
  GV.fileSize = function (b) {
    b = Number(b) || 0; if (!b) return "";
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(i ? 1 : 0) + " " + u[i];
  };

  /* Lucide icons used in the notices and panel (ISC licence, same as the site's {% icon %}). */
  var ICONS = {
    "wifi-off": '<path d="M12 20h.01"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/><path d="M5 12.859a10 10 0 0 1 5.17-2.69"/><path d="M19 12.859a10 10 0 0 0-2.007-1.523"/><path d="M2 8.82a15 15 0 0 1 4.177-2.643"/><path d="M22 8.82a15 15 0 0 0-11.288-3.764"/><path d="m2 2 20 20"/>',
    wifi: '<path d="M12 20h.01"/><path d="M2 8.82a15 15 0 0 1 20 0"/><path d="M5 12.859a10 10 0 0 1 14 0"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/>',
    download: '<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/>',
    share: '<path d="M12 2v13"/><path d="m16 6-4-4-4 4"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>',
    "square-plus": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M8 12h8"/><path d="M12 8v8"/>',
    save: '<path d="M12 2v8"/><path d="m16 6-4 4-4-4"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 18h.01"/><path d="M10 18h.01"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    play: '<path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"/>',
    app: '<path d="M12 13V7"/><path d="m15 10-3 3-3-3"/><rect width="20" height="14" x="2" y="3" rx="2"/><path d="M12 17v4"/><path d="M8 21h8"/>',
  };
  function icon(name, cls) {
    return '<svg class="icon ' + (cls || "size-4") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + ICONS[name] + "</svg>";
  }

  /* Notices the visitor closed in this tab session (sessionStorage; blocked → shown again). */
  var hidden = {};
  try { hidden = JSON.parse(sessionStorage.getItem("gvlv-pwa-hide") || "{}") || {}; } catch (e) { hidden = {}; }
  function hideForSession(kind, off) {
    if (off) delete hidden[kind]; else hidden[kind] = 1;
    try { sessionStorage.setItem("gvlv-pwa-hide", JSON.stringify(hidden)); } catch (e) { /* storage blocked */ }
  }

  var state = {
    online: navigator.onLine !== false,
    copyFrom: null,         // this page is a saved copy (the network was too slow): when it was saved
    waiting: null,          // a new service worker waiting for "Reload"
    installEvt: null,       // Chrome / Edge / Android: the deferred install prompt
    installed: false,
    saving: null,           // { done, total } while "Save key pages" runs
    saved: null,            // pages saved for offline in this language (null = unknown)
    lastSave: "",           // the result line after saving
    imgs: false,            // "Show images" for this page view
    swReady: false,
  };
  var isOfflinePage = !!document.querySelector("[data-pwa-saved]");
  var isFallback = isOfflinePage && location.pathname.replace(/index\.html$/, "") !== new URL(OFFLINE_URL, location.href).pathname;

  /* ================================================================= Data saver ================= */
  function saverOn() { return root.getAttribute("data-saver") === "on"; }
  function prefsSaver() {
    // What the visitor's own choice says right now (app.js GV.prefs; the same rule as base.njk's head script).
    if (GV.prefs && GV.prefs.saverActive) { try { return !!GV.prefs.saverActive(); } catch (e) { /* fall through */ } }
    var p = {};
    try { p = JSON.parse(localStorage.getItem("gvlv-prefs") || "{}") || {}; } catch (e) { p = {}; }
    var c = navigator.connection || {};
    return p.saver === "on" || (p.saver !== "off" && (c.saveData === true || /^(slow-2g|2g)$/.test(c.effectiveType || "")));
  }
  // Offline: the effects are on whatever the choice (the stored preference is not changed); back
  // online, the visitor's own choice returns (only if we were the ones who turned it on).
  var forcing = false, forcedByUs = false;
  function enforceOffline() {
    var want = !state.online ? "on" : (prefsSaver() ? "on" : "off");
    if (root.getAttribute("data-saver") !== want && (!state.online || forcedByUs)) { forcing = true; root.setAttribute("data-saver", want); forcing = false; }
    forcedByUs = !state.online;
  }

  var HIDE = 'img:not([src^="data:"]):not([src^="blob:"]):not([src*="/assets/img/"]):not([data-saver-keep])';
  function imagesHidden() { return saverOn() && !state.imgs; }

  /* Images Alpine fills in later (:src) — and those in <template>s it clones — get loading="lazy":
     hidden by areas/pwa.css, a lazy image is never requested. (Runs before Alpine starts.) */
  function lazyLater(scope) {
    (scope || document).querySelectorAll("img:not([loading])").forEach(function (img) { if (!img.getAttribute("src")) img.setAttribute("loading", "lazy"); });
    (scope || document).querySelectorAll("template").forEach(function (t) { if (t.content) { t.content.querySelectorAll("img:not([loading])").forEach(function (img) { img.setAttribute("loading", "lazy"); }); lazyLater(t.content); } });
  }

  /* The frame a hidden picture leaves: a quiet tile, with an "image off" mark in the middle unless
     something else already fills it (a play overlay, a fallback icon). Only frames with their own
     size and no visible text are marked (screen-reader-only text doesn't count). Read, then write. */
  function visibleText(el) {
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) { if (n.nodeValue.trim()) return true; continue; }
      if (n.nodeType !== 1 || n.tagName === "IMG" || n.tagName.toLowerCase() === "svg") continue;
      if (n.classList.contains("sr-only") || n.classList.contains("lyt-visually-hidden") || n.getAttribute("aria-hidden") === "true") continue;
      if (visibleText(n)) return true;
    }
    return false;
  }
  function markFrames() {
    var on = imagesHidden();
    if (!on) {
      document.querySelectorAll(".pwa-ph").forEach(function (el) { el.classList.remove("pwa-ph", "pwa-ph-icon"); });
      document.querySelectorAll(".pwa-alt").forEach(function (el) { el.remove(); });
      return;
    }
    var todo = [], alts = [];
    document.querySelectorAll(HIDE).forEach(function (img) {
      if (img.closest(".glightbox-container, [data-saver-keep]")) return;
      // A hidden picture leaves the accessibility tree too: its text alternative stays as
      // screen-reader text (a cover that is a link keeps its name).
      var alt = (img.getAttribute("alt") || "").trim();
      var next = img.nextElementSibling;
      if (alt && !(next && next.classList.contains("pwa-alt"))) alts.push([img, alt]);
      var p = img.parentElement;
      if (!p || p.classList.contains("pwa-ph")) return;
      var w = p.offsetWidth, h = p.offsetHeight;
      if (w < 24 || h < 24 || visibleText(p)) return;
      var filled = Array.prototype.some.call(p.children, function (c) { return c.tagName !== "IMG" && c.offsetWidth > w / 2 && c.offsetHeight > h / 2; });
      todo.push([p, !filled]);
    });
    todo.forEach(function (x) { x[0].classList.add("pwa-ph"); if (x[1]) x[0].classList.add("pwa-ph-icon"); });
    alts.forEach(function (x) {
      var sr = document.createElement("span");
      sr.className = "pwa-alt sr-only";
      sr.textContent = x[1];
      x[0].insertAdjacentElement("afterend", sr);
    });
  }
  function countHidden() {
    if (!imagesHidden()) return 0;
    var n = 0;
    document.querySelectorAll(HIDE).forEach(function (img) { if (!img.closest(".glightbox-container, [data-saver-keep]")) n++; });
    return n + document.querySelectorAll("lite-youtube:not(.lyt-activated)").length;
  }

  /* YouTube previews (lite-youtube): no thumbnail (pwa.css), a visible "Load video (uses data)"
     button, and no early connections to YouTube / Google on hover. */
  var LOAD_VIDEO = T("Load video (uses data)", "Cargar video (usa datos)");
  function videoButtons() {
    var on = imagesHidden();
    document.querySelectorAll("lite-youtube").forEach(function (el) {
      var btn = el.querySelector(".lyt-playbtn");
      if (!btn) return;
      var lab = btn.querySelector(".pwa-lyt-label");
      if (on) {
        if (!lab) {
          lab = document.createElement("span");
          lab.className = "pwa-lyt-label";
          lab.setAttribute("aria-hidden", "true");
          lab.innerHTML = icon("play", "size-4") + "<span>" + esc(LOAD_VIDEO) + "</span>";
          btn.appendChild(lab);
        }
        if (!btn.hasAttribute("data-pwa-name")) btn.setAttribute("data-pwa-name", btn.getAttribute("aria-label") || "");
        var title = (btn.querySelector(".lyt-visually-hidden") || {}).textContent || el.getAttribute("playlabel") || "";
        btn.setAttribute("aria-label", LOAD_VIDEO + (title ? " — " + title.trim() : ""));
      } else if (lab) {
        lab.remove();
        var old = btn.getAttribute("data-pwa-name");
        if (old) btn.setAttribute("aria-label", old); else btn.removeAttribute("aria-label");
        btn.removeAttribute("data-pwa-name");
      }
    });
    var LYT = window.customElements && window.customElements.get("lite-youtube");
    if (LYT) {
      if (on && !LYT.preconnected) { LYT.preconnected = true; LYT.pwaBlocked = true; }
      else if (!on && LYT.pwaBlocked) { LYT.preconnected = false; LYT.pwaBlocked = false; }
    }
  }

  function syncSaver() {
    root.classList.toggle("pwa-imgs", state.imgs);
    if (saverOn()) lazyLater();
    markFrames();
    videoButtons();
    renderNotice();
    renderSlots();
  }
  function showImages() {
    state.imgs = true;
    syncSaver();
    announce(T("Images are shown on this page.", "Las imágenes se muestran en esta página."));
  }

  // Whoever changes data-saver (the panel, app.js, the browser's own data saver) → apply at once.
  new MutationObserver(function () {
    if (forcing) return;
    if (!state.online && !saverOn()) { forcing = true; root.setAttribute("data-saver", "on"); forcing = false; forcedByUs = true; }
    if (!saverOn()) state.imgs = false; // "Show images" lasts until Data saver goes off
    syncSaver();
  }).observe(root, { attributes: true, attributeFilter: ["data-saver"] });

  // Lists Alpine renders later (episodes, videos, search results …): new pictures → frames, labels;
  // a panel slot that appears later → filled. Text-only changes (a countdown, the player's clock) are ignored.
  var moTimer = 0;
  var WATCH = "img, lite-youtube, #pwa-slot, [data-pwa-slot], [data-pwa-install], [data-pwa-save]";
  function relevantNode(n) { return n.nodeType === 1 && (n.matches(WATCH) || !!n.querySelector(WATCH)); }
  new MutationObserver(function (muts) {
    if (moTimer) return;
    var relevant = muts.some(function (m) { return Array.prototype.some.call(m.addedNodes, relevantNode); });
    if (!relevant) return;
    moTimer = requestAnimationFrame(function () {
      moTimer = 0;
      fillSlots();
      if (imagesHidden()) { markFrames(); videoButtons(); }
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  /* The address-bar colour follows the site's theme (the two media-query metas in base.njk only
     know the OS setting; the header's theme button can differ). */
  function themeColor() {
    var dark = root.getAttribute("data-theme") === "dark";
    document.querySelectorAll('meta[name="theme-color"]').forEach(function (m) { m.setAttribute("content", dark ? "#121019" : "#07457c"); });
  }
  new MutationObserver(themeColor).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  themeColor();

  /* ================================================================= Notices ==================== */
  /* The top notice (offline / saved copy): fixed right under the header; while it is shown
     html.pwa-bar-on adds its height to --header-h (hero, anchors, sticky columns, the Aa panel
     follow). The toast (a new version / images are off): at the bottom, above the podcast player
     and the language banner, and the page keeps room for it at the end (pwa.css). */
  var bar = null, toast = null, barRO = null, toastRO = null;
  function barKind() {
    if (!state.online) return hidden.offline ? "" : "offline";
    if (state.copyFrom !== null && !hidden.copy) return "copy";
    return "";
  }
  function toastKind() {
    if (state.waiting) return "update";
    if (!state.online || barKind()) return "";
    if (imagesHidden() && !hidden.saver && countHidden() > 0) return "saver";
    return "";
  }
  function measure(el, prop, cls) {
    var h = el && !el.hidden ? Math.ceil(el.getBoundingClientRect().height) : 0;
    root.classList.toggle(cls, !!h);
    if (h) root.style.setProperty(prop, h + "px"); else root.style.removeProperty(prop);
  }
  function ago(iso) {
    if (!iso || !GV.relative) return "";
    return Date.now() - Date.parse(iso) < 60e3 ? T("just now", "hace un momento") : GV.relative(iso);
  }

  function renderNotice() {
    renderBar();
    renderToast();
  }

  function renderBar() {
    var kind = barKind();
    if (!kind) { if (bar) { bar.hidden = true; measure(bar, "--pwa-bar-h", "pwa-bar-on"); } return; }
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "pwa-bar";
      bar.setAttribute("role", "region");
      bar.setAttribute("aria-label", T("Connection", "Conexión"));
      bar.setAttribute("data-tts-skip", "");
      document.body.appendChild(bar);
      if (window.ResizeObserver) { barRO = new ResizeObserver(function () { measure(bar, "--pwa-bar-h", "pwa-bar-on"); }); barRO.observe(bar); }
    }
    var html;
    // The links sit in the sentence (inline links: no 44px box needed, and the notice stays one line on most phones).
    if (kind === "offline") {
      html = '<span class="pwa-bar-icon">' + icon("wifi-off", "size-4") + "</span>" +
        '<p class="pwa-bar-text"><strong>' + esc(T("You're offline", "Estás sin conexión")) + "</strong> — " +
        esc(isFallback ? T("this page isn't saved on this device.", "esta página no está guardada en este dispositivo.") : T("showing saved pages.", "se muestran las páginas guardadas.")) +
        (isOfflinePage ? "" : ' <a class="pwa-bar-link" href="' + esc(OFFLINE_URL) + '">' + esc(T("See saved pages", "Ver las páginas guardadas")) + "</a>") + "</p>";
    } else {
      var when = ago(state.copyFrom);
      html = '<span class="pwa-bar-icon">' + icon("wifi-off", "size-4") + "</span>" +
        '<p class="pwa-bar-text"><strong>' + esc(T("Slow connection", "Conexión lenta")) + "</strong> — " +
        esc(when ? T("this is the copy saved " + when + ".", "esta es la copia guardada " + when + ".") : T("this is a saved copy.", "esta es una copia guardada.")) +
        ' <a class="pwa-bar-link" href="' + esc(location.pathname + location.search) + '">' + esc(T("Try again", "Intentar de nuevo")) + "</a></p>";
    }
    html += '<button type="button" class="pwa-bar-close" data-pwa-act="close-bar" data-kind="' + kind + '" aria-label="' + esc(T("Close this notice", "Cerrar este aviso")) + '">' + icon("x", "size-4") + "</button>";
    if (bar.getAttribute("data-kind") !== kind || bar.hidden) {
      bar.innerHTML = '<div class="pwa-bar-inner container-page">' + html + "</div>";
      bar.setAttribute("data-kind", kind);
    }
    bar.hidden = false;
    measure(bar, "--pwa-bar-h", "pwa-bar-on");
  }

  function renderToast() {
    var kind = toastKind();
    if (!kind) { if (toast) { toast.hidden = true; measure(toast, "--pwa-toast-h", "pwa-toast-on"); } return; }
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "pwa-toast";
      toast.setAttribute("role", "region");
      toast.setAttribute("aria-label", T("Site notice", "Aviso del sitio"));
      toast.setAttribute("data-tts-skip", "");
      document.body.appendChild(toast);
      if (window.ResizeObserver) { toastRO = new ResizeObserver(function () { measure(toast, "--pwa-toast-h", "pwa-toast-on"); }); toastRO.observe(toast); }
    }
    if (toast.getAttribute("data-kind") !== kind || toast.hidden) {
      var html = kind === "update"
        ? '<span class="pwa-toast-icon">' + icon("refresh", "size-5") + '</span><p class="pwa-toast-text"><strong>' + esc(T("Updated", "Actualizado")) + "</strong> " +
          esc(T("A new version of the site is ready.", "Hay una versión nueva del sitio.")) + "</p>" +
          '<button type="button" class="btn-primary btn-sm shrink-0" data-pwa-act="update">' + esc(T("Reload", "Recargar")) + "</button>"
        : '<span class="pwa-toast-icon">' + icon("image", "size-5") + '</span><p class="pwa-toast-text"><strong>' + esc(T("Data saver is on", "Ahorro de datos activado")) + "</strong> " +
          esc(T("Images and video previews are off.", "Las imágenes y las vistas previas de video están apagadas.")) + "</p>" +
          '<button type="button" class="btn-secondary btn-sm shrink-0" data-pwa-act="show-images">' + esc(T("Show images", "Mostrar imágenes")) + "</button>";
      html += '<button type="button" class="pwa-toast-close" data-pwa-act="close-toast" data-kind="' + kind + '" aria-label="' + esc(T("Close this notice", "Cerrar este aviso")) + '">' + icon("x", "size-5") + "</button>";
      toast.innerHTML = html;
      toast.setAttribute("data-kind", kind);
    }
    toast.hidden = false;
    measure(toast, "--pwa-toast-h", "pwa-toast-on");
  }

  /* ================================================================= Install ==================== */
  function standalone() {
    try {
      if (navigator.standalone === true) return true;
      return ["standalone", "minimal-ui", "fullscreen", "window-controls-overlay"].some(function (m) { return window.matchMedia("(display-mode: " + m + ")").matches; });
    } catch (e) { return false; }
  }
  state.installed = standalone();
  var ua = navigator.userAgent || "";
  var isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var isMacSafari = !isIOS && /Macintosh/.test(ua) && /Safari\//.test(ua) && !/Chrome|Chromium|CriOS|FxiOS|Edg|OPR|Firefox/.test(ua);
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault(); // our "Install app" button instead of the browser's mini bar
    state.installEvt = e;
    renderSlots();
  });
  window.addEventListener("appinstalled", function () {
    state.installEvt = null;
    state.installed = true;
    renderSlots();
    announce(T("Installed. Look for “GV/LV 65” on your home screen or among your apps.", "Instalado. Busca “GV/LV 65” en tu pantalla de inicio o entre tus apps."));
  });
  function install() {
    var e = state.installEvt;
    if (!e) return;
    state.installEvt = null;
    try {
      e.prompt();
      (e.userChoice || Promise.resolve({})).then(function (r) { if (r && r.outcome === "accepted") state.installed = true; renderSlots(); });
    } catch (err) { renderSlots(); }
  }
  function installHtml() {
    if (state.installed) return "";
    if (state.installEvt) {
      return '<button type="button" class="btn-primary btn-sm pwa-wide" data-pwa-act="install">' + icon("app", "size-4") + esc(T("Install app", "Instalar la app")) + "</button>" +
        '<p class="pwa-hint">' + esc(T("It opens in its own window and works with a weak signal.", "Se abre en su propia ventana y funciona con poca señal.")) + "</p>";
    }
    if (isIOS) {
      return '<p class="pwa-steps">' + icon("share", "size-4 shrink-0") + "<span>" +
        esc(T("On iPhone or iPad: tap Share, then “Add to Home Screen”.", "En iPhone o iPad: toca Compartir y luego “Agregar a inicio”.")) + "</span></p>";
    }
    if (isMacSafari) {
      return '<p class="pwa-steps">' + icon("square-plus", "size-4 shrink-0") + "<span>" +
        esc(T("In Safari on a Mac: File → Add to Dock.", "En Safari en una Mac: Archivo → Agregar al Dock.")) + "</span></p>";
    }
    return "";
  }

  /* ================================================================= Save for offline =========== */
  function isThisLang(u) {
    var p = new URL(u, location.href).pathname;
    var es = p.indexOf(at("/es/")) === 0;
    return LANG === "es" ? es : !es;
  }
  function countSaved() {
    if (!canSW) return Promise.resolve(null);
    return caches.has(SAVED_CACHE).then(function (has) {
      if (!has) return 0;
      return caches.open(SAVED_CACHE).then(function (c) { return c.keys(); }).then(function (keys) { return keys.filter(function (k) { return isThisLang(k.url); }).length; });
    }).catch(function () { return null; });
  }
  function refreshCount() { countSaved().then(function (n) { state.saved = n; renderSlots(); }); }

  function savePages() {
    if (!canSW || state.saving) return;
    if (!state.online) { state.lastSave = T("You're offline: connect to save pages.", "Estás sin conexión: conéctate para guardar páginas."); renderSlots(); return; }
    state.saving = { done: 0, total: 0 };
    state.lastSave = "";
    renderSlots();
    announce(T("Saving pages for offline use…", "Guardando páginas para usar sin conexión…"));
    var gaveUp = setTimeout(function () { finish(null); }, 60000);
    function finish(d) {
      clearTimeout(gaveUp);
      if (!state.saving) return;
      state.saving = null;
      if (d && d.saved) {
        state.lastSave = d.saved === d.total
          ? T("Saved " + d.saved + " pages. They open without a connection.", "Se guardaron " + d.saved + " páginas. Se abren sin conexión.")
          : T("Saved " + d.saved + " of " + d.total + " pages.", "Se guardaron " + d.saved + " de " + d.total + " páginas.");
      } else {
        state.lastSave = T("Couldn't save the pages. Try again with a better signal.", "No se pudieron guardar las páginas. Intenta de nuevo con mejor señal.");
      }
      announce(state.lastSave);
      refreshCount();
    }
    navigator.serviceWorker.ready.then(function (reg) {
      if (!reg.active) { finish(null); return; }
      var ch = new MessageChannel();
      ch.port1.onmessage = function (ev) {
        var d = ev.data || {};
        if (d.type === "SAVE_PROGRESS") { state.saving = { done: d.done, total: d.total }; renderProgress(); }
        else if (d.type === "SAVE_DONE") finish(d);
      };
      reg.active.postMessage({ type: "SAVE", lang: LANG }, [ch.port2]);
    }, function () { finish(null); });
  }
  function renderProgress() {
    document.querySelectorAll("[data-pwa-progress]").forEach(function (box) {
      var s = state.saving;
      var bar = box.querySelector("progress"), txt = box.querySelector("span");
      if (!s) return;
      if (s.total) { bar.max = s.total; bar.value = s.done; } else bar.removeAttribute("value");
      txt.textContent = s.total ? T("Saving " + s.done + " of " + s.total + "…", "Guardando " + s.done + " de " + s.total + "…") : T("Saving…", "Guardando…");
    });
  }
  function saveHtml(inCard) {
    if (!canSW) return '<p class="pwa-hint">' + esc(T("This browser can't keep pages for use without a connection.", "Este navegador no puede guardar páginas para usarlas sin conexión.")) + "</p>";
    var s = state.saving;
    // While saving, the button stays focusable (aria-disabled) so keyboard focus isn't lost; offline it is disabled.
    var h = '<button type="button" class="btn-secondary btn-sm pwa-wide" data-pwa-act="save"' + (!state.online ? " disabled" : s ? ' aria-disabled="true"' : "") + ">" + icon("save", "size-4") +
      esc(state.saved ? T("Update saved pages", "Actualizar las páginas guardadas") : T("Save key pages for offline", "Guardar páginas clave")) + "</button>";
    if (s) h += '<p class="pwa-progress" data-pwa-progress><progress max="1"></progress><span></span></p>';
    else if (state.lastSave) h += '<p class="pwa-status">' + esc(state.lastSave) + "</p>";
    else if (!inCard) h += '<p class="pwa-hint">' + esc(T("Meetings, this month's toolkit, Share your story, the Shop and more, in your language.", "Reuniones, el kit de este mes, Comparte tu historia, la Tienda y más, en tu idioma.")) + "</p>"; // (a card says it already)
    return h;
  }

  /* ================================================================= The panel slots ============ */
  function statusHtml() {
    var n = state.saved;
    var saved = n ? T(n === 1 ? "1 page saved on this device" : n + " pages saved on this device", n === 1 ? "1 página guardada en este dispositivo" : n + " páginas guardadas en este dispositivo") : "";
    return '<p class="pwa-line ' + (state.online ? "is-online" : "is-offline") + '">' + icon(state.online ? "wifi" : "wifi-off", "size-4 shrink-0") + "<span>" +
      esc(state.online ? T("You're online", "Tienes conexión") : T("You're offline", "Estás sin conexión")) + (saved ? " · " + esc(saved) : "") + "</span></p>";
  }
  function slotHtml(kind) {
    if (kind === "install") return installHtml();
    if (kind === "save") return saveHtml(true);
    var parts = ['<p class="pwa-slot-h">' + icon("save", "size-4 shrink-0") + "<span>" + esc(T("Offline & app", "Sin conexión y app")) + "</span></p>", statusHtml()];
    var inst = installHtml();
    if (inst) parts.push('<div class="pwa-row">' + inst + "</div>");
    parts.push('<div class="pwa-row">' + saveHtml() + "</div>");
    var links = [];
    if (canSW && !isOfflinePage) links.push('<a class="pwa-link" href="' + esc(OFFLINE_URL) + '">' + esc(T("See saved pages", "Ver las páginas guardadas")) + "</a>");
    if (imagesHidden() && countHidden() > 0) links.push('<button type="button" class="pwa-link" data-pwa-act="show-images">' + icon("image", "size-4 shrink-0") + esc(T("Show images on this page", "Mostrar imágenes en esta página")) + "</button>");
    if (links.length) parts.push('<p class="pwa-links">' + links.join("") + "</p>");
    return parts.join("");
  }
  function slots() {
    var out = [];
    document.querySelectorAll("#pwa-slot, [data-pwa-slot]").forEach(function (el) { out.push([el, "full"]); });
    document.querySelectorAll("[data-pwa-install]").forEach(function (el) { out.push([el, "install"]); });
    document.querySelectorAll("[data-pwa-save]").forEach(function (el) { out.push([el, "save"]); });
    return out;
  }
  var drawn = new WeakMap();
  function renderSlots() {
    slots().forEach(function (x) {
      var el = x[0], html = slotHtml(x[1]);
      el.setAttribute("data-pwa-ready", "");
      if (drawn.get(el) === html) return;                                // unchanged: keep focus where it is
      var focused = el.contains(document.activeElement) ? document.activeElement.getAttribute("data-pwa-act") : null;
      el.innerHTML = html;
      drawn.set(el, html);
      el.classList.toggle("pwa-slot", x[1] === "full" && !!html);
      if (focused) {
        var again = el.querySelector('[data-pwa-act="' + focused + '"]:not([disabled])') || el.querySelector("button:not([disabled]), a");
        if (again) again.focus({ preventScroll: true });
      }
    });
    renderProgress();
  }
  function fillSlots() { if (document.querySelector("#pwa-slot:not([data-pwa-ready]), [data-pwa-slot]:not([data-pwa-ready]), [data-pwa-install]:not([data-pwa-ready]), [data-pwa-save]:not([data-pwa-ready])")) renderSlots(); }

  /* ================================================================= Clicks ===================== */
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-pwa-act], [data-pwa-retry]");
    if (!b) return;
    if (b.hasAttribute("data-pwa-retry")) {
      if (!isOfflinePage || (state.online && !isFallback)) return; // online on /offline/: a plain link home
      e.preventDefault();
      location.reload();
      return;
    }
    var act = b.getAttribute("data-pwa-act");
    if (act === "install") install();
    else if (act === "save") savePages();
    else if (act === "show-images") showImages();
    else if (act === "update" && state.waiting) { wantReload = true; state.waiting.postMessage({ type: "SKIP_WAITING" }); b.disabled = true; }
    else if (act === "close-bar") { hideForSession(b.getAttribute("data-kind")); renderBar(); }
    else if (act === "close-toast") {
      var k = b.getAttribute("data-kind");
      if (k === "update") state.waiting = null; else hideForSession(k);
      renderToast();
    }
  });

  /* ================================================================= Online / offline ========== */
  function onNet() {
    var was = state.online;
    state.online = navigator.onLine !== false;
    enforceOffline();
    if (was !== state.online) {
      announce(state.online ? T("You're back online.", "Volviste a tener conexión.") : T("You're offline. Saved pages still open.", "Estás sin conexión. Las páginas guardadas se siguen abriendo."));
      if (state.online) { hideForSession("offline", true); state.copyFrom = null; }
      // The offline page shown in place of another page: that page, now that we can.
      if (state.online && isFallback) { location.reload(); return; }
    }
    syncSaver();
    offlinePageCopy();
  }
  window.addEventListener("online", onNet);
  window.addEventListener("offline", onNet);

  /* ================================================================= Service worker ============ */
  var wantReload = false;
  function offerUpdate(w) {
    state.waiting = w;
    renderToast();
    announce(T("A new version of the site is ready. Reload to use it.", "Hay una versión nueva del sitio. Recarga para usarla."));
  }
  function register() {
    navigator.serviceWorker.register(at("/sw.js"), { scope: BASE }).then(function (reg) {
      state.swReady = true;
      if (reg.waiting && navigator.serviceWorker.controller) offerUpdate(reg.waiting);
      reg.addEventListener("updatefound", function () {
        var w = reg.installing;
        if (!w) return;
        w.addEventListener("statechange", function () { if (w.state === "installed" && navigator.serviceWorker.controller) offerUpdate(w); });
      });
      // A tab (or the installed app) left open for hours checks for a new version when it comes back
      // — at most once an hour, so a weak signal isn't spent on it.
      var last = Date.now();
      document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible" && Date.now() - last > 3600e3) { last = Date.now(); reg.update().catch(function () {}); }
      });
      refreshCount();
    }).catch(function () { /* blocked (private mode, policy): the site simply works online */ });
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (wantReload) { wantReload = false; location.reload(); }
    });
    // Did the worker answer this page with a saved copy (slow connection)?
    var ctl = navigator.serviceWorker.controller;
    if (ctl && window.MessageChannel) {
      var ch = new MessageChannel();
      ch.port1.onmessage = function (ev) {
        var d = ev.data || {};
        if (d.copy && !isOfflinePage) { state.copyFrom = d.savedAt || ""; renderNotice(); }
      };
      try { ctl.postMessage({ type: "HOW_SERVED" }, [ch.port2]); } catch (e) { /* worker gone */ }
    }
  }

  /* ================================================================= The offline page ========== */
  function offlinePageCopy() {
    var box = document.querySelector("[data-pwa-offline-copy]");
    if (!box || isFallback || !state.online) return;
    // Opened on purpose while online: "Pages saved on this device", and the button goes home.
    var h1 = document.querySelector(".gv-hero-title > span:last-child"), sub = document.querySelector(".gv-hero-sub");
    if (h1 && box.getAttribute("data-title-online")) h1.textContent = box.getAttribute("data-title-online");
    if (sub && box.getAttribute("data-sub-online")) sub.textContent = box.getAttribute("data-sub-online");
    var btn = box.querySelector("[data-pwa-retry]");
    if (btn) {
      btn.setAttribute("href", HOME);
      btn.innerHTML = '<svg class="icon size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg> <span>' + esc(box.getAttribute("data-home-label") || "") + "</span>";
    }
  }

  function readCache(name) {
    return caches.has(name).then(function (has) {
      if (!has) return [];
      return caches.open(name).then(function (c) {
        return c.keys().then(function (keys) {
          return Promise.all(keys.map(function (k) {
            return c.match(k).then(function (res) {
              var t = "";
              try { t = decodeURIComponent((res && res.headers.get("x-gvlv-title")) || ""); } catch (e) { t = ""; }
              return { url: k.url, title: t, saved: (res && res.headers.get("x-gvlv-saved")) || "" };
            });
          }));
        });
      });
    });
  }
  function niceTitle(e) {
    var p = new URL(e.url).pathname;
    if (p === at("/") || p === at("/es/")) return p === at("/es/") ? "Inicio" : "Home";
    var t = (e.title || "").split(" · ")[0].trim();
    return t || decodeURIComponent(p.replace(BASE, "/"));
  }
  function listHtml(title, items) {
    if (!items.length) return "";
    var MAX = 8;
    var rows = items.map(function (e, i) {
      var p = new URL(e.url).pathname;
      var other = !isThisLang(e.url);
      var lang = p.indexOf(at("/es/")) === 0 ? "es" : "en";
      var when = e.saved ? ago(e.saved) : "";
      if (when) when = T("Saved ", "Guardada ") + when;
      return "<li" + (i >= MAX ? ' class="pwa-more" hidden' : "") + '><a class="pwa-saved-link" href="' + esc(p) + '"' + (other ? ' hreflang="' + lang + '" lang="' + lang + '"' : "") + ">" +
        '<span class="pwa-saved-title">' + esc(niceTitle(e)) + "</span>" + (other ? '<span class="badge-muted uppercase">' + lang + "</span>" : "") +
        (when ? '<span class="pwa-saved-when">' + esc(when) + "</span>" : "") + "</a></li>";
    }).join("");
    var more = items.length > MAX ? '<button type="button" class="btn-ghost btn-sm mt-2" data-pwa-more>' + esc(T("Show all " + items.length, "Mostrar las " + items.length)) + "</button>" : "";
    return '<div class="pwa-saved-group"><h3 class="pwa-saved-h">' + esc(title) + " <span>(" + items.length + ')</span></h3><ul class="pwa-saved-list" role="list">' + rows + "</ul>" + more + "</div>";
  }
  function offlineList() {
    var box = document.querySelector("[data-pwa-saved]");
    if (!box) return;
    var status = box.querySelector("[data-pwa-saved-status]"), list = box.querySelector("[data-pwa-saved-list]");
    var empty = box.querySelector("[data-pwa-saved-empty]"), unsup = box.querySelector("[data-pwa-saved-unsupported]");
    if (!canSW) { if (status) status.hidden = true; if (unsup) unsup.hidden = false; return; }
    Promise.all([readCache(SAVED_CACHE), readCache(PAGES_CACHE)]).then(function (r) {
      var mine = function (a) { return a.filter(function (e) { return isThisLang(e.url); }).concat(a.filter(function (e) { return !isThisLang(e.url); })); };
      var savedUrls = {};
      r[0].forEach(function (e) { savedUrls[e.url] = 1; });
      var saved = mine(r[0]);
      var recent = mine(r[1].filter(function (e) { return !savedUrls[e.url]; }).reverse()); // newest first
      if (!saved.length && !recent.length) { if (status) status.hidden = true; if (empty) empty.hidden = false; return; }
      var n = saved.length + recent.length;
      if (status) status.textContent = T(n === 1 ? "1 page opens without a connection." : n + " pages open without a connection.", n === 1 ? "1 página se abre sin conexión." : n + " páginas se abren sin conexión.");
      list.innerHTML = listHtml(box.getAttribute("data-t-saved") || "", saved) + listHtml(box.getAttribute("data-t-recent") || "", recent);
    }).catch(function () { if (status) status.hidden = true; if (unsup) unsup.hidden = false; });
    box.addEventListener("click", function (e) {
      var b = e.target.closest("[data-pwa-more]");
      if (!b) return;
      var group = b.closest(".pwa-saved-group");
      var first = group.querySelector(".pwa-more");
      group.querySelectorAll(".pwa-more").forEach(function (li) { li.hidden = false; });
      b.remove();
      if (first) { var a = first.querySelector("a"); if (a) a.focus(); }
    });
  }

  /* ================================================================= Start ====================== */
  enforceOffline();
  if (saverOn()) lazyLater();
  function start() {
    if (canSW) refreshCount();
    syncSaver();
    offlinePageCopy();
    offlineList();
    if (canSW) {
      if (document.readyState === "complete") setTimeout(register, 0);
      else window.addEventListener("load", function () { setTimeout(register, 0); });
    } else renderSlots();
  }
  // Start at DOMContentLoaded: it fires after every deferred script (lite-youtube is defined and its
  // players set up, Alpine has made its first pass). While this deferred file runs, readyState is
  // already "interactive", so readyState alone can't tell — "complete" means we came late.
  var started = false;
  function go() { if (!started) { started = true; start(); } }
  document.addEventListener("DOMContentLoaded", go);
  if (document.readyState === "complete") go();
  // YouTube previews defined later than that (or never on this page): label them once they are.
  if (window.customElements && window.customElements.whenDefined) window.customElements.whenDefined("lite-youtube").then(function () { if (started) videoButtons(); });
  window.addEventListener("gvlv:prefs", function () { setTimeout(function () { enforceOffline(); syncSaver(); }, 0); });
})();
