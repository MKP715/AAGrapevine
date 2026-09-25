// /sw.js — the service worker (offline use + the installable app). The logic lives in
// src/_includes/pwa/sw-core.js; this template puts this build's settings above it:
//   version   build.version (src/_data/build.js): a fingerprint of the site's code, so the worker —
//             and its app-shell cache — changes when the code does, not with each daily content sync
//   base      the site's base path ("/AAGrapevine/" on GitHub Pages, "/" on a custom domain)
//   shell     what is saved on install: styles, scripts, the two main fonts, the logo and app icons,
//             and the two offline pages (/offline/, /es/offline/)
//   save      the pages "Save key pages for offline" keeps, in the visitor's language: home, Meetings,
//             this month's Monthly toolkit page ({month}, worked out in the worker — the hub when that
//             page is missing), Contribute, Shop, and Accessibility / GVR 101 when those pages exist
// Registered by src/assets/js/pwa.js with scope = base. Served from the base path, so its scope
// can cover the whole site.
import fs from "node:fs";
import path from "node:path";

export const data = {
  permalink: "/sw.js",
  eleventyExcludeFromCollections: true,
  layout: false,
};

// Optional pages, picked up once they exist (first match wins).
const OPTIONAL = [
  ["accessibility/", "accesibilidad/"],
  ["gvr-101/", "gvr101/", "orientation/", "gvr/101/"],
];

export function render(data) {
  const prefix = String(process.env.PATH_PREFIX || "/").replace(/^\/+|\/+$/g, "");
  const base = prefix ? `/${prefix}/` : "/";
  const v = data.build.version;

  const urls = new Set();
  for (const p of data.collections?.all || []) {
    if (p.url) urls.add(p.url);
    for (const h of p.data?.pagination?.hrefs || []) urls.add(h);
  }
  const save = ["", "meetings/", "monthly/{month}/", "contribute/", "shop/"];
  for (const group of OPTIONAL) {
    const hit = group.find((p) => urls.has("/" + p));
    if (hit) save.push(hit);
  }

  const a = (p) => base + p;
  const offline = { en: a("offline/"), es: a("es/offline/") };
  const required = [a(`assets/css/main.css?v=${v}`), a(`assets/js/app.js?v=${v}`), a(`assets/js/pwa.js?v=${v}`), offline.en, offline.es];
  const shell = [
    ...required,
    a(`assets/js/hero-canvas.js?v=${v}`),
    a(`assets/vendor/alpine.min.js?v=${v}`),
    a("assets/fonts/inter-latin-wght-normal.woff2"),
    a("assets/fonts/fraunces-latin-opsz-normal.woff2"),
    a("assets/img/logo-wide.png"),
    a("assets/img/logo-32x32.png"),
    a("assets/img/app-icon-192.png"),
    a("favicon.ico"),
  ];

  const config = { version: v, base, shell, required, offline, save };
  const core = fs.readFileSync(path.join("src", "_includes", "pwa", "sw-core.js"), "utf8");
  return `/* NETA 65 Grapevine / La Viña — service worker, version ${v}. Generated from src/pages/sw.11ty.js. */\n` +
    `const CONFIG = ${JSON.stringify(config, null, 2)};\n\n${core}`;
}
