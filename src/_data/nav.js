// Site navigation. `key` = i18n key (src/_i18n/common.json), `url` is
// language-neutral (templates prefix /es/ via the lurl filter).
// Each page lives in ONE place: Shop is top level (subscriptions, Book of the Month,
// catalogs, order forms); "Get involved" (like aagrapevine.org's own section) holds the
// service pages; "Committee" holds the committee's own pages.
const nav = {
  primary: [
    { key: "nav.whats_new", url: "/whats-new/", icon: "sparkles", page: "whats-new" },
    { key: "nav.read", url: "/read/", icon: "book-open", page: "read" },
    { key: "nav.listen", url: "/listen/", icon: "headphones", page: "listen" },
    { key: "nav.watch", url: "/watch/", icon: "circle-play", page: "watch" },
    { key: "nav.library", url: "/library/", icon: "library", page: "library" },
    { key: "nav.shop", url: "/shop/", icon: "shopping-bag", page: "shop", descKey: "nav.shop_desc" },
    {
      // The representatives' pages first (the corner, the course for new ones, the monthly tools:
      // the toolkit and the digest that passes the month's news on), then the pages for any member
      // (share a story, read who got published). The header menu, the phone drawer and the footer
      // all follow this order.
      key: "nav.get_involved", icon: "hand-heart", children: [
        { key: "nav.gvr", url: "/gvr/", icon: "badge-check", page: "gvr", descKey: "nav.gvr_desc" },
        // GVR / RLV 101: six short sessions for new representatives (config/orientation.yml), with slides and a handout.
        { key: "nav.orientation", url: "/orientation/", icon: "sprout", page: "orientation", descKey: "nav.orientation_desc" },
        { key: "nav.monthly", url: "/monthly/", icon: "calendar-heart", page: "monthly", descKey: "nav.monthly_desc" },
        // Monthly digest: last month's news and this month's dates, to paste into a WhatsApp group or
        // send by e-mail (it was in the footer's "Stay updated" column).
        { key: "nav.digest", url: "/digest/", icon: "newspaper", page: "digest", descKey: "nav.digest_desc" },
        { key: "nav.contribute", url: "/contribute/", icon: "pen-line", page: "contribute", descKey: "nav.contribute_desc" },
        { key: "nav.published", url: "/published/", icon: "award", page: "published", descKey: "nav.published_desc" },
      ],
    },
    {
      key: "nav.committee", icon: "users", children: [
        // Meetings: the committee's monthly meeting, Grapevine meetings in and near our Area, and the
        // weekly open meetings. It stays under Committee: it is the first tab of the committee sub-nav.
        { key: "nav.meetings", url: "/meetings/", icon: "calendar-clock", page: "meetings", descKey: "nav.meetings_desc" },
        { key: "nav.events", url: "/events/", icon: "calendar-days", page: "events", descKey: "nav.events_desc" },
        // Portfolio: the committee's own files (reports, notes, slides, workshops) from its Google Drive.
        // (It was /documents/ — documents-redirect.njk keeps that address working.)
        { key: "nav.portfolio", url: "/portfolio/", icon: "folder-open", page: "portfolio", descKey: "nav.portfolio_desc" },
        { key: "nav.photos", url: "/photos/", icon: "images", page: "photos", descKey: "nav.photos_desc" },
        { key: "nav.announcements", url: "/announcements/", icon: "megaphone", page: "announcements", descKey: "nav.announcements_desc" },
      ],
    },
  ],
  // Pages outside the header menus. group "stay" → the footer's "Stay updated" column (after the
  // official aagrapevine.org / aalavina.org sites, before the RSS and calendar feeds); group "site" →
  // the footer's bottom bar (right side, with "Last updated"), except About, which is a button beside
  // the e-mail and neta65.org ones under the footer's about blurb. The phone drawer lists them all
  // under "More" (except Search, which has its own button there). `icon`: a Lucide name, or a local
  // icon from src/_includes/icons (instagram).
  footer: [
    // A media feed (like Listen / Watch), not a way to take part: footer + drawer "More", and linked
    // from the home page and /photos/.
    { key: "nav.instagram", url: "/instagram/", page: "instagram", icon: "instagram", group: "stay" },
    { key: "nav.about", url: "/about/", page: "about", icon: "info", group: "site" },
    // Accessibility: the reading settings explained, captions, ASL, audio, joining meetings by phone,
    // printing (src/pages/accessibility.njk). Also linked from the "Aa" panel.
    { key: "nav.accessibility", url: "/accessibility/", page: "accessibility", icon: "accessibility", group: "site" },
    // Saved pages & app (/offline/): what is saved on this device, "Save key pages", installing the site.
    { key: "nav.offline", url: "/offline/", page: "offline", icon: "hard-drive-download", group: "site" },
    { key: "nav.share", url: "/share/", page: "share", icon: "qr-code", group: "site" },
    { key: "nav.search", url: "/search/", page: "search", icon: "search", group: "site" },
    { key: "nav.status", url: "/status/", page: "status", icon: "activity", group: "site" },
  ],
};

// A dropdown group lists the pageKeys it holds, so the header can mark the
// section of the current page ("you are here") on the closed Get Involved/Committee button.
for (const item of nav.primary) if (item.children) item.pages = item.children.map((c) => c.page);

export default nav;
