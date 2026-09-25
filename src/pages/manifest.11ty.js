// /manifest.webmanifest (English) and /es/manifest.webmanifest (Spanish) — what makes the site
// installable ("Install app" / "Add to Home Screen"). base.njk links the one of the page's language.
// Both describe the SAME app (same id and scope: the site's base path), so it is installed once;
// the Spanish one opens on /es/ and has Spanish shortcuts.
// Colours come from the design tokens (main.css): theme = --c-gv-strong (light), background =
// --c-paper (light). Icons: src/assets/img/app-icon-*.png, drawn by scripts/dev/make_app_icons.py
// ("any": rounded square; "maskable": full bleed with the mark inside the 80 % safe circle).
// The HTML base plugin only rewrites HTML, so the base path is written into every URL here.
export const data = {
  pagination: { data: "languages", size: 1, alias: "lang" },
  permalink: (data) => (data.lang === "en" ? "/manifest.webmanifest" : `/${data.lang}/manifest.webmanifest`),
  eleventyExcludeFromCollections: true,
  layout: false,
};

export function render(data) {
  const { lang } = data;
  const t = (key) => this.t(key, lang);
  const prefix = String(process.env.PATH_PREFIX || "/").replace(/^\/+|\/+$/g, "");
  const base = prefix ? `/${prefix}/` : "/";
  const home = lang === "en" ? base : `${base}${lang}/`;
  const icon = (file, size, purpose) => ({ src: `${base}assets/img/${file}`, sizes: `${size}x${size}`, type: "image/png", purpose });
  const manifest = {
    id: base,
    name: "Grapevine / La Viña — NETA 65",
    short_name: "GV/LV 65",
    description: t("pwa.manifest.description"),
    lang: lang === "es" ? "es-US" : "en-US",
    dir: "ltr",
    start_url: home,
    scope: base,
    display: "standalone",
    theme_color: "#07457c",
    background_color: "#fbf8f2",
    categories: ["lifestyle", "news", "education"],
    icons: [
      icon("app-icon-192.png", 192, "any"),
      icon("app-icon-512.png", 512, "any"),
      icon("app-icon-maskable-192.png", 192, "maskable"),
      icon("app-icon-maskable-512.png", 512, "maskable"),
    ],
    shortcuts: [
      { name: t("nav.meetings"), url: `${home}meetings/` },
      { name: t("nav.monthly"), url: `${home}monthly/` },
      { name: t("nav.listen"), url: `${home}listen/` },
    ].map((s) => ({ ...s, icons: [icon("app-icon-192.png", 192, "any")] })),
    prefer_related_applications: false,
  };
  return JSON.stringify(manifest, null, 2) + "\n";
}
