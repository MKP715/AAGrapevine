/* Shop page (/shop/) + the home page's Book of the Month teaser.
   Loaded with `defer` after app.js and BEFORE Alpine, so the Alpine component below is registered
   in time (alpine:init). No dependencies besides Alpine (the countdown is plain JS).

   1. Offer countdown — any element with data-shop-offer data-ends="YYYY-MM-DD" (the last day of the
      offer, in the site's time zone). Inside it:
        [data-shop-days]   text redrawn from its templates: data-t-many="{n} days left",
                           data-t-tomorrow="Ends tomorrow", data-t-last="Last day!"
        [data-shop-live]   shown while the offer runs
        [data-shop-ended]  shown (hidden attribute removed) once the offer is over
      The build renders the same text for the build day, so without JavaScript the page still reads right
      (the site is rebuilt daily, and the sync drops an offer once it has ended).
   2. shopSubs — the subscription price switch (publication × region radio groups). Without JavaScript
      the switch stays hidden (x-cloak) and every publication's U.S. prices show. ?pub=lv preselects. */
(function () {
  "use strict";
  var TZ = (window.SITE && window.SITE.tz) || "America/Chicago";

  function todayYmd() {
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    } catch (e) {
      return new Date().toISOString().slice(0, 10);
    }
  }
  function dayDiff(a, b) {
    var pa = a.split("-").map(Number), pb = b.split("-").map(Number);
    return Math.round((Date.UTC(pb[0], pb[1] - 1, pb[2]) - Date.UTC(pa[0], pa[1] - 1, pa[2])) / 864e5);
  }

  function updateOffers() {
    var today = todayYmd();
    var offers = document.querySelectorAll("[data-shop-offer][data-ends]");
    for (var i = 0; i < offers.length; i++) {
      var el = offers[i];
      var ends = el.getAttribute("data-ends");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(ends)) continue;
      var n = dayDiff(today, ends);
      var ended = n < 0;
      el.classList.toggle("is-ended", ended);
      var live = el.querySelectorAll("[data-shop-live]");
      for (var j = 0; j < live.length; j++) live[j].hidden = ended;
      var gone = el.querySelectorAll("[data-shop-ended]");
      for (var k = 0; k < gone.length; k++) gone[k].hidden = !ended;
      if (ended) continue;
      var days = el.querySelectorAll("[data-shop-days]");
      for (var d = 0; d < days.length; d++) {
        var s = days[d];
        var txt = n === 0 ? s.getAttribute("data-t-last") : n === 1 ? s.getAttribute("data-t-tomorrow") : (s.getAttribute("data-t-many") || "").replace("{n}", n);
        if (txt && s.textContent !== txt) s.textContent = txt;
        s.classList.toggle("is-urgent", n <= 3);
      }
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", updateOffers);
  else updateOffers();
  // A page left open past midnight (or brought back from the background) catches up.
  setInterval(updateOffers, 60 * 1000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) updateOffers(); });

  document.addEventListener("alpine:init", function () {
    /* x-data="shopSubs({ first: 'gv', avail: { gv: ['us','ca','intl'], lv: [...] } })" */
    window.Alpine.data("shopSubs", function (cfg) {
      cfg = cfg || {};
      var avail = cfg.avail || {};
      return {
        pub: cfg.first || "gv",
        region: "us",
        init: function () {
          try {
            var want = new URLSearchParams(window.location.search).get("pub");
            if (want && avail[want]) this.pub = want;
          } catch (e) {}
          this.fixRegion();
          var self = this;
          this.$watch("pub", function () { self.fixRegion(); });
        },
        has: function (r) { return (avail[this.pub] || []).indexOf(r) !== -1; },
        fixRegion: function () {
          var list = avail[this.pub] || [];
          if (list.length && list.indexOf(this.region) === -1) this.region = list.indexOf("us") !== -1 ? "us" : list[0];
        },
        show: function (p, r) { return this.pub === p && this.region === r; },
      };
    });
  });
})();
