/* /monthly/ — poster scaling fallback, "Download image (PNG)" and "Share" (src/pages/monthly-month.njk).
   Progressive enhancement: the Download and Share buttons are `hidden` in the HTML and shown here;
   Print works on its own (onclick="window.print()"; the print stylesheet prints only the poster).
   The PNG is made from the unscaled poster node (1080 × 1350) with html-to-image, self-hosted at
   assets/vendor/html-to-image.js and loaded on first use. It waits for document.fonts.ready and
   html-to-image embeds the site's self-hosted fonts, so the image looks like the page.
   Share: Web Share API level 2 with the PNG file where the browser can share files (phones →
   WhatsApp …); anywhere else it copies the page link. */
(function () {
  "use strict";
  var GV = window.GV || {};
  var announce = function (msg) { if (GV.announce) GV.announce(msg); };

  /* Scale-to-fit fallback: the CSS uses tan(atan2(100cqw, 1080px)); older browsers get --mp-s here. */
  var cssScale = window.CSS && CSS.supports && CSS.supports("width", "calc(tan(atan2(1px, 2px)) * 1px)");
  if (!cssScale && "ResizeObserver" in window) {
    var ro = new ResizeObserver(function (entries) {
      entries.forEach(function (e) {
        var p = e.target.querySelector(".mp-poster");
        if (p) p.style.setProperty("--mp-s", String(e.contentRect.width / 1080));
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll(".mp-fit"), function (f) { ro.observe(f); });
  }

  /* Safety net for unusual months (many events, long titles): if a block does not fit its column,
     the poster's type is stepped down until everything fits. The PNG and the print use the same DOM. */
  function overflowing(p) {
    var pr = p.getBoundingClientRect();
    var blocks = p.querySelectorAll(".mp-b, .mp-foot, .mp-head, .mp-row, .mp-col, .mp-body, .mp-board, .mp-cover, .mp-strip, .mp-page");
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i], br = b.getBoundingClientRect(), par = b.parentElement.getBoundingClientRect();
      var tol = 6 * (pr.width / 1080);
      if (br.bottom > par.bottom + tol || br.bottom > pr.bottom + tol) return true;
    }
    return false;
  }
  function fit(p) {
    var size = parseFloat(getComputedStyle(p).fontSize) || 23;
    for (var n = 0; n < 16 && size > 16 && overflowing(p); n++) { size -= 0.5; p.style.fontSize = size + "px"; }
  }
  var fitAll = function () { Array.prototype.forEach.call(document.querySelectorAll("[data-mp-poster]"), fit); };
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll); else fitAll();

  var poster = document.querySelector(".mp-print-root [data-mp-poster]");
  var dl = document.querySelector("[data-mp-download]");
  var sh = document.querySelector("[data-mp-share]");
  if (!poster || !dl) return;

  var base = ((window.SITE && window.SITE.base) || "/").replace(/\/?$/, "/");
  var libPromise = null;
  function loadLib() {
    if (window.htmlToImage) return Promise.resolve(window.htmlToImage);
    if (!libPromise) {
      libPromise = new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = base + "assets/vendor/html-to-image.js";
        s.onload = function () { window.htmlToImage ? resolve(window.htmlToImage) : reject(new Error("html-to-image")); };
        s.onerror = function () { libPromise = null; reject(new Error("html-to-image failed to load")); };
        document.head.appendChild(s);
      });
    }
    return libPromise;
  }

  var blobPromise = null;
  function render() {
    if (blobPromise) return blobPromise;
    var fontsReady = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    blobPromise = Promise.all([loadLib(), fontsReady]).then(function (r) {
      var h2i = r[0];
      var opts = {
        width: 1080, height: 1350, canvasWidth: 1080, canvasHeight: 1350, pixelRatio: 1, cacheBust: false,
        backgroundColor: getComputedStyle(poster).backgroundColor || "#ffffff",
        // The on-screen poster is scaled down with a transform: the image is taken unscaled.
        style: { transform: "none", position: "relative", top: "0", left: "0", margin: "0" },
      };
      // First pass warms the image/font caches (Safari drops fonts on the very first draw).
      return h2i.toBlob(poster, opts).then(function () { return h2i.toBlob(poster, opts); });
    }).then(function (blob) {
      if (!blob) throw new Error("empty image");
      return blob;
    }, function (err) { blobPromise = null; throw err; });
    return blobPromise;
  }

  function busy(btn, on) {
    if (on) {
      btn.setAttribute("aria-busy", "true");
      btn.disabled = true;
      announce(dl.getAttribute("data-working"));
    } else {
      btn.removeAttribute("aria-busy");
      btn.disabled = false;
    }
  }

  function save(blob, name) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
  }

  dl.hidden = false;
  dl.addEventListener("click", function () {
    busy(dl, true);
    render().then(function (blob) {
      save(blob, dl.getAttribute("data-file"));
      announce(dl.getAttribute("data-done"));
    }).catch(function () {
      announce(dl.getAttribute("data-failed"));
      window.alert(dl.getAttribute("data-failed"));
    }).then(function () { busy(dl, false); });
  });

  if (!sh) return;
  sh.hidden = false;
  var url = sh.getAttribute("data-url") || location.href;
  var copyLink = function () {
    if (GV.copy) GV.copy(url, sh);
    announce(sh.getAttribute("data-copied"));
  };
  var canShareFiles = function (file) {
    try { return !!(navigator.canShare && navigator.share && navigator.canShare({ files: [file] })); } catch (e) { return false; }
  };
  // Phones: make the image while the finger is still on the button, so share() runs within the
  // click's user activation.
  if (navigator.canShare) {
    ["pointerdown", "focus"].forEach(function (ev) { sh.addEventListener(ev, function () { render().catch(function () {}); }, { once: true }); });
  }
  sh.addEventListener("click", function () {
    if (!navigator.canShare || !navigator.share) { copyLink(); return; }
    busy(sh, true);
    render().then(function (blob) {
      var file = new File([blob], sh.getAttribute("data-file"), { type: "image/png" });
      if (!canShareFiles(file)) { copyLink(); return; }
      return navigator.share({ files: [file], title: sh.getAttribute("data-title") || document.title, text: sh.getAttribute("data-text") || url })
        .catch(function (err) { if (!err || err.name !== "AbortError") copyLink(); });
    }).catch(copyLink).then(function () { busy(sh, false); });
  });
})();
