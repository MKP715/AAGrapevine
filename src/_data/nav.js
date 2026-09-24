// Site navigation. `key` = i18n key (src/_i18n/common.json), `url` is
// language-neutral (templates prefix /es/ via the lurl filter).
// Each page lives in ONE place: Shop is top level (subscriptions, Book of the Month,
// catalogs, order forms); "Get Involved" (like aagrapevine.org's own section) holds the
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
      key: "nav.get_involved", icon: "hand-heart", children: [
        { key: "nav.monthly", url: "/monthly/", icon: "calendar-heart", page: "monthly", descKey: "nav.monthly_desc" },
        { key: "nav.contribute", url: "/contribute/", icon: "pen-line", page: "contribute", descKey: "nav.contribute_desc" },
        { key: "nav.published", url: "/published/", icon: "award", page: "published", descKey: "nav.published_desc" },
        { key: "nav.gvr", url: "/gvr/", icon: "badge-check", page: "gvr", descKey: "nav.gvr_desc" },
        { key: "nav.districts", url: "/districts/", icon: "map", page: "districts", descKey: "nav.districts_desc" },
      ],
    },
    {
      key: "nav.committee", icon: "users", children: [
        { key: "nav.meeting", url: "/meeting/", icon: "video", page: "meeting", descKey: "nav.meeting_desc" },
        { key: "nav.events", url: "/events/", icon: "calendar-days", page: "events", descKey: "nav.events_desc" },
        { key: "nav.documents", url: "/documents/", icon: "folder-open", page: "documents", descKey: "nav.documents_desc" },
        { key: "nav.photos", url: "/photos/", icon: "images", page: "photos", descKey: "nav.photos_desc" },
        { key: "nav.announcements", url: "/announcements/", icon: "megaphone", page: "announcements", descKey: "nav.announcements_desc" },
      ],
    },
  ],
  footer: [
    { key: "nav.about", url: "/about/", page: "about" },
    { key: "nav.digest", url: "/digest/", page: "digest" },
    // A media feed (like Listen / Watch), not a way to take part: footer + drawer "More", and linked
    // from the home page and /photos/.
    { key: "nav.instagram", url: "/instagram/", page: "instagram" },
    { key: "nav.share", url: "/share/", page: "share" },
    { key: "nav.search", url: "/search/", page: "search" },
    { key: "nav.status", url: "/status/", page: "status" },
  ],
};

// A dropdown group lists the pageKeys it holds, so the header can mark the
// section of the current page ("you are here") on the closed Get Involved/Committee button.
for (const item of nav.primary) if (item.children) item.pages = item.children.map((c) => c.page);

export default nav;
