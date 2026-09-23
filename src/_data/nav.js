// Site navigation. `key` = i18n key (src/_i18n/common.json), `url` is
// language-neutral (templates prefix /es/ via the lurl filter).
export default {
  primary: [
    { key: "nav.whats_new", url: "/whats-new/", icon: "sparkles", page: "whats-new" },
    { key: "nav.read", url: "/read/", icon: "book-open", page: "read" },
    { key: "nav.listen", url: "/listen/", icon: "headphones", page: "listen" },
    { key: "nav.watch", url: "/watch/", icon: "circle-play", page: "watch" },
    { key: "nav.library", url: "/library/", icon: "library", page: "library" },
    {
      key: "nav.committee", icon: "users", children: [
        { key: "nav.meeting", url: "/meeting/", icon: "video", page: "meeting", descKey: "nav.meeting_desc" },
        { key: "nav.events", url: "/events/", icon: "calendar-days", page: "events", descKey: "nav.events_desc" },
        { key: "nav.documents", url: "/documents/", icon: "folder-open", page: "documents", descKey: "nav.documents_desc" },
        { key: "nav.photos", url: "/photos/", icon: "images", page: "photos", descKey: "nav.photos_desc" },
        { key: "nav.announcements", url: "/announcements/", icon: "megaphone", page: "announcements", descKey: "nav.announcements_desc" },
      ],
    },
    {
      key: "nav.service", icon: "hand-heart", children: [
        { key: "nav.gvr", url: "/gvr/", icon: "badge-check", page: "gvr", descKey: "nav.gvr_desc" },
        { key: "nav.districts", url: "/districts/", icon: "map", page: "districts", descKey: "nav.districts_desc" },
        { key: "nav.contribute", url: "/contribute/", icon: "pen-line", page: "contribute", descKey: "nav.contribute_desc" },
        { key: "nav.subscribe", url: "/subscribe/", icon: "mail-open", page: "subscribe", descKey: "nav.subscribe_desc" },
        { key: "nav.instagram", url: "/instagram/", icon: "instagram", page: "instagram", descKey: "nav.instagram_desc" },
      ],
    },
  ],
  footer: [
    { key: "nav.about", url: "/about/" },
    { key: "nav.digest", url: "/digest/" },
    { key: "nav.share", url: "/share/" },
    { key: "nav.search", url: "/search/" },
    { key: "nav.status", url: "/status/" },
  ],
};
