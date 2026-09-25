/* The district-meeting report editor on /monthly/#report (src/pages/monthly.njk).
   The report's sections come from the build (eleventy/filters/report.js → <script id="rp-data">, both
   languages); the editor's own words from <script id="rp-ui"> (page language). Each GVR / RLV can
   switch sections on and off, reorder them, edit any text, add their own sections and pick the
   counties near their district for the Grapevine meetings; the preview updates as they type.
   Output: plain text (copy / WhatsApp / e-mail / .txt), formatted text (copy → e-mail or Word), a
   Word file (.docx, written here: a small stored ZIP, no library) and print (print CSS in
   areas/report.css prints only the report).
   Drafts: localStorage, per month and report language ("gv-report:YYYY-MM:en"), plus the
   "Your details" fields ("gv-report:profile") and the report language ("gv-report:lang").
   Only the CHANGES are stored, so a section nobody edited always shows the latest data. Storage
   may be missing (private windows): every access is wrapped, and the page works without it.
   Without JavaScript the page shows the whole report as text (.rp-nojs). */
(function () {
  "use strict";
  var GV = window.GV || {};
  var PREFIX = "gv-report:";
  var WA_MAX = 6000;     // longest wa.me link we open (longer: copy + paste instead)
  var MAIL_MAX = 1800;   // longest mailto: link (Windows / Outlook stop near 2,000)
  var WPM = 130;         // reading aloud
  var BOM = String.fromCharCode(0xfeff);
  var NBSP = String.fromCharCode(0xa0);
  var WJ = String.fromCharCode(0x2060);   // word joiner: no line break there

  /* ---------------- small helpers ---------------- */
  function load(k) { try { var s = localStorage.getItem(k); return s ? JSON.parse(s) : null; } catch (e) { return null; } }
  function store(k, v) {
    try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; }
  }
  // "k in o" (not hasOwnProperty): Alpine's reactive proxies track `in`, so a section's first edit
  // updates the preview. Keys are section ids, never inherited names.
  function has(o, k) { return !!o && k in o && o[k] !== undefined; }
  function str(v, max) { return typeof v === "string" ? v.slice(0, max || 20000) : ""; }
  function fmt(tpl, vars) {
    return String(tpl || "").replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] != null ? String(vars[k]) : m; });
  }
  function clean(s) { return String(s == null ? "" : s).replace(/\s+/g, " ").trim(); }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function slug(s) { return clean(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24); }
  // XML 1.0 can't hold most control characters: drop them (tabs and line breaks stay).
  function xmlText(s) {
    var out = "";
    s = String(s);
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= 32 || c === 9 || c === 10 || c === 13) out += s.charAt(i); }
    return out.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* ---------------- the report: text and blocks ---------------- */
  /* The sections joined as the report — the same rules as composeText in eleventy/filters/report.js:
     the header as it is, then "1. Title" + text; an empty section is left out. */
  function compose(sections) {
    var out = [], n = 0;
    sections.forEach(function (s) {
      var body = String(s.text || "").replace(/\r\n?/g, "\n").replace(/\s+$/, "").replace(/^\n+/, "");
      if (!body.trim()) return;
      if (s.id === "header") out.push(body);
      else out.push((++n) + ". " + clean(s.title) + "\n" + body);
    });
    return out.join("\n\n") + "\n";
  }

  /* The same report as blocks for the preview, the formatted copy and the Word file: the header's
     first line is the title, its other lines the byline; a line starting with • - * or · is a list
     item (an indented line after it continues it); every other line is a paragraph. */
  function blocks(sections) {
    var out = [], n = 0;
    sections.forEach(function (s) {
      var body = String(s.text || "").replace(/\r\n?/g, "\n");
      if (!body.trim()) return;
      if (s.id === "header") {
        var lines = body.split("\n").map(clean).filter(Boolean);
        out.push({ t: "title", text: lines[0], sec: s.id });
        lines.slice(1).forEach(function (l) { out.push({ t: "meta", text: l, sec: s.id }); });
        return;
      }
      n++;
      out.push({ t: "h", text: n + ". " + clean(s.title), sec: s.id });
      var list = null;
      body.split("\n").forEach(function (line) {
        if (!line.trim()) { list = null; return; }
        var m = /^\s*[•·*-]\s+(.*)$/.exec(line);
        if (m) {
          if (!list) { list = { t: "ul", items: [], sec: s.id }; out.push(list); }
          list.items.push(m[1].trim());
          return;
        }
        if (list && /^\s{2,}\S/.test(line)) { list.items[list.items.length - 1] += "\n" + line.trim(); return; }
        list = null;
        out.push({ t: "p", text: line.trim(), sec: s.id });
      });
    });
    return out;
  }

  // Links and e-mail addresses inside a line (a trailing period or bracket is not part of a link).
  var LINK = /(https?:\/\/[^\s<>"]*[^\s<>".,;:!?)\]’”])|([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;
  function splitLinks(text) {
    var parts = [], last = 0, m;
    LINK.lastIndex = 0;
    while ((m = LINK.exec(text))) {
      if (m.index > last) parts.push({ text: text.slice(last, m.index) });
      parts.push({ text: m[0], href: m[1] ? m[1] : "mailto:" + m[2] });
      last = m.index + m[0].length;
    }
    if (last < text.length) parts.push({ text: text.slice(last) });
    return parts;
  }
  // HTML of one line: links; in the preview, [blanks] are marked.
  function inline(text, o) {
    return splitLinks(text).map(function (p) {
      if (p.href) {
        var ext = /^https?:/.test(p.href) && o.newTab;
        return '<a href="' + esc(p.href) + '"' + (o.link ? ' style="' + o.link + '"' : "") + (ext ? ' target="_blank" rel="noopener"' : "") + ">" + esc(p.text) + "</a>";
      }
      // never "La / Viña" or "7– / 8 PM" at a line end
      var h = esc(p.text).replace(/La Viña/g, "La" + NBSP + "Viña").replace(/(\d)–(\d)/g, "$1–" + WJ + "$2");
      return o.marks ? h.replace(/\[([^\]\n]{1,80})\]/g, '<mark class="rp-blank">[$1]</mark>') : h;
    }).join("").replace(/\n/g, "<br>");
  }

  /* The on-screen preview: one wrapper per section (data-sec) so the section being edited can be
     shown and highlighted. Headings are h4 (the page: h2 section → h3 "Your report"). */
  function previewHtml(bl, active) {
    var h = "", cur = null;
    var o = { marks: true, newTab: true };
    bl.forEach(function (b) {
      if (b.sec !== cur) {
        if (cur !== null) h += "</div>";
        cur = b.sec;
        h += '<div class="rp-doc-sec' + (b.sec === active ? " is-active" : "") + '" data-sec="' + esc(b.sec) + '">';
      }
      if (b.t === "title") h += '<p class="rp-doc-title">' + inline(b.text, o) + "</p>";
      else if (b.t === "meta") h += '<p class="rp-doc-meta">' + inline(b.text, o) + "</p>";
      else if (b.t === "h") h += '<h4 class="rp-doc-h">' + inline(b.text, o) + "</h4>";
      else if (b.t === "p") h += "<p>" + inline(b.text, o) + "</p>";
      else if (b.t === "ul") h += "<ul>" + b.items.map(function (i) { return "<li>" + inline(i, o) + "</li>"; }).join("") + "</ul>";
    });
    if (cur !== null) h += "</div>";
    return h;
  }

  /* Formatted copy for e-mail and word processors: inline styles only (mail apps drop <style>). */
  function emailHtml(bl, lang) {
    var o = { link: "color:#0b57a4" };
    var h = '<div lang="' + lang + '" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#1a1a1a">';
    bl.forEach(function (b) {
      if (b.t === "title") h += '<h1 style="font-size:20px;line-height:1.3;margin:0 0 4px">' + inline(b.text, o) + "</h1>";
      else if (b.t === "meta") h += '<p style="margin:0 0 2px;color:#555555">' + inline(b.text, o) + "</p>";
      else if (b.t === "h") h += '<h2 style="font-size:16px;line-height:1.3;margin:18px 0 6px">' + inline(b.text, o) + "</h2>";
      else if (b.t === "p") h += '<p style="margin:0 0 6px">' + inline(b.text, o) + "</p>";
      else if (b.t === "ul") h += '<ul style="margin:0 0 8px;padding-left:22px">' + b.items.map(function (i) { return '<li style="margin:0 0 4px">' + inline(i, o) + "</li>"; }).join("") + "</ul>";
    });
    return h + "</div>";
  }

  /* ---------------- Word (.docx): WordprocessingML in a stored ZIP ---------------- */
  var CRC = null;
  function crc32(u8) {
    if (!CRC) {
      CRC = new Uint32Array(256);
      for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; CRC[n] = c >>> 0; }
    }
    var crc = 0xffffffff;
    for (var i = 0; i < u8.length; i++) crc = CRC[(crc ^ u8[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }
  function zipBlob(files, type) {
    var enc = new TextEncoder(), parts = [], central = [], offset = 0, now = new Date();
    var time = ((now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1)) & 0xffff;
    var date = (((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate()) & 0xffff;
    files.forEach(function (f) {
      var name = enc.encode(f.name), data = enc.encode(f.text), crc = crc32(data);
      var lh = new DataView(new ArrayBuffer(30));
      lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true); lh.setUint16(8, 0, true);
      lh.setUint16(10, time, true); lh.setUint16(12, date, true); lh.setUint32(14, crc, true);
      lh.setUint32(18, data.length, true); lh.setUint32(22, data.length, true); lh.setUint16(26, name.length, true); lh.setUint16(28, 0, true);
      parts.push(new Uint8Array(lh.buffer), name, data);
      var ch = new DataView(new ArrayBuffer(46));
      ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint16(8, 0x0800, true); ch.setUint16(10, 0, true);
      ch.setUint16(12, time, true); ch.setUint16(14, date, true); ch.setUint32(16, crc, true);
      ch.setUint32(20, data.length, true); ch.setUint32(24, data.length, true); ch.setUint16(28, name.length, true);
      ch.setUint16(30, 0, true); ch.setUint16(32, 0, true); ch.setUint16(34, 0, true); ch.setUint16(36, 0, true); ch.setUint32(38, 0, true); ch.setUint32(42, offset, true);
      central.push(new Uint8Array(ch.buffer), name);
      offset += 30 + name.length + data.length;
    });
    var size = 0;
    central.forEach(function (p) { size += p.length; });
    var end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
    end.setUint32(12, size, true); end.setUint32(16, offset, true);
    return new Blob(parts.concat(central, [new Uint8Array(end.buffer)]), { type: type });
  }

  var W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
  var RELS = "http://schemas.openxmlformats.org/package/2006/relationships";
  var OREL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  var XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';

  function docx(bl, lang, title) {
    var rels = [];
    function runs(text) {
      return splitLinks(text).map(function (p) {
        var t = p.text.split("\n").map(function (seg) { return '<w:t xml:space="preserve">' + xmlText(seg) + "</w:t>"; }).join("<w:br/>");
        if (!p.href) return "<w:r>" + t + "</w:r>";
        var id = "rId" + (rels.length + 10);
        rels.push('<Relationship Id="' + id + '" Type="' + OREL + '/hyperlink" Target="' + xmlText(p.href) + '" TargetMode="External"/>');
        return '<w:hyperlink r:id="' + id + '" w:history="1"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>' + t + "</w:r></w:hyperlink>";
      }).join("");
    }
    function para(style, inner) { return "<w:p><w:pPr><w:pStyle w:val=\"" + style + "\"/></w:pPr>" + inner + "</w:p>"; }
    var body = bl.map(function (b) {
      if (b.t === "title") return para("Title", runs(b.text));
      if (b.t === "meta") return para("Subtitle", runs(b.text));
      if (b.t === "h") return para("Heading1", runs(b.text));
      if (b.t === "p") return para("Normal", runs(b.text));
      return b.items.map(function (i) { return para("ListBullet", '<w:r><w:t xml:space="preserve">•</w:t></w:r><w:r><w:tab/></w:r>' + runs(i)); }).join("");
    }).join("");
    var wlang = lang === "es" ? "es-419" : "en-US";
    var document = XML + '<w:document xmlns:w="' + W + '" xmlns:r="' + OREL + '"><w:body>' + body +
      '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>';
    var styles = XML + '<w:styles xmlns:w="' + W + '">' +
      '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="' + wlang + '"/></w:rPr></w:rPrDefault>' +
      '<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>' +
      '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>' +
      '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="1F3864"/><w:sz w:val="34"/><w:szCs w:val="34"/></w:rPr></w:style>' +
      '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="40"/></w:pPr><w:rPr><w:color w:val="555555"/></w:rPr></w:style>' +
      '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="80"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="1F3864"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>' +
      '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="40"/><w:ind w:left="360" w:hanging="360"/></w:pPr></w:style>' +
      '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>' +
      "</w:styles>";
    var docRels = XML + '<Relationships xmlns="' + RELS + '"><Relationship Id="rId1" Type="' + OREL + '/styles" Target="styles.xml"/>' + rels.join("") + "</Relationships>";
    var core = XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">' +
      "<dc:title>" + xmlText(title) + "</dc:title><dc:language>" + wlang + '</dc:language><dcterms:created xsi:type="dcterms:W3CDTF">' + new Date().toISOString().replace(/\.\d+Z$/, "Z") + "</dcterms:created></cp:coreProperties>";
    var types = XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
      '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>' +
      '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>';
    var rootRels = XML + '<Relationships xmlns="' + RELS + '"><Relationship Id="rId1" Type="' + OREL + '/officeDocument" Target="word/document.xml"/>' +
      '<Relationship Id="rId2" Type="' + RELS + '/metadata/core-properties" Target="docProps/core.xml"/></Relationships>';
    return zipBlob([
      { name: "[Content_Types].xml", text: types },
      { name: "_rels/.rels", text: rootRels },
      { name: "docProps/core.xml", text: core },
      { name: "word/document.xml", text: document },
      { name: "word/styles.xml", text: styles },
      { name: "word/_rels/document.xml.rels", text: docRels },
    ], "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  }

  /* ---------------- clipboard, files, links ---------------- */
  // The copy event lets us put plain text AND html on the clipboard where the async API is missing.
  function legacyCopy(text, html) {
    var ok = false;
    function onCopy(e) {
      try { e.clipboardData.setData("text/plain", text); if (html) e.clipboardData.setData("text/html", html); e.preventDefault(); ok = true; } catch (x) { /* no clipboardData */ }
    }
    document.addEventListener("copy", onCopy);
    try { document.execCommand("copy"); } catch (e) { /* not allowed */ }
    document.removeEventListener("copy", onCopy);
    if (ok || html) return ok;
    // Last resort: select the text in a hidden textarea (focus goes back afterwards).
    var back = document.activeElement, ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0"; ta.style.top = "0";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy") !== false; } catch (e) { ok = false; }
    document.body.removeChild(ta);
    if (back && back.focus) back.focus({ preventScroll: true });
    return ok;
  }
  function writeText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(function () { return legacyCopy(text) ? true : Promise.reject(new Error("copy")); });
    }
    return legacyCopy(text) ? Promise.resolve(true) : Promise.reject(new Error("copy"));
  }
  function saveBlob(blob, name) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  }
  // Opens a link the way a click on it would (a new tab for web links; mailto: in place).
  function openLink(href, newTab) {
    var a = document.createElement("a");
    a.href = href;
    if (newTab) { a.target = "_blank"; a.rel = "noopener"; }
    a.setAttribute("data-rp-open", "");
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { a.remove(); }, 0);
  }

  /* ---------------- the Alpine component ---------------- */
  document.addEventListener("alpine:init", function () {
    window.Alpine.data("rpEditor", function (pageLang) {
      return {
        L: pageLang || "en",
        rl: pageLang || "en",       // the report's language (its own EN / ES switch)
        D: null,                    // the model (both languages)
        ui: {},
        prof: { district: "", name: "", role: "", group: "" },
        d: null,                    // this month + language's changes
        open: {},                   // sections whose editor is open
        active: "",                 // the section being edited (highlighted in the preview)
        saveState: "idle",          // idle · saved · failed
        savedAt: "",
        msg: "",
        waLong: false,
        full: false,                // phones: the preview unfolded
        canShare: !!navigator.share,
        _saveT: 0, _msgT: 0, _pending: false,

        init: function () {
          try {
            this.D = JSON.parse(document.getElementById("rp-data").textContent);
            this.ui = JSON.parse(document.getElementById("rp-ui").textContent) || {};
          } catch (e) { return; }
          var p = load(PREFIX + "profile");
          if (p && typeof p === "object") {
            for (var k in this.prof) if (has(p, k)) this.prof[k] = str(p[k], 60);
            if (typeof this.D.langs.en.roles[this.prof.role] !== "string") this.prof.role = "";
          }
          var lg = load(PREFIX + "lang");
          if (lg === "en" || lg === "es") this.rl = lg;
          this.prune();
          this.d = this.loadDraft(this.rl);
          var self = this;
          // Leaving the page within the save delay still keeps the last keystrokes.
          var flush = function () { if (self._pending) self.saveNow(); };
          window.addEventListener("pagehide", flush);
          document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(); });
          // An edit in another tab (same device) shows up here too.
          window.addEventListener("storage", function (e) {
            if (e.key === self.key(self.rl)) self.d = self.loadDraft(self.rl);
          });
        },

        /* ---- storage ---- */
        key: function (lang) { return PREFIX + this.D.month + ":" + lang; },
        ids: function () { return this.D.langs[this.rl].sections.map(function (s) { return s.id; }); },
        blank: function () { return { order: this.ids(), off: {}, text: {}, titles: {}, custom: [], meet: [] }; },
        loadDraft: function (lang) {
          var b = { order: this.D.langs[lang].sections.map(function (s) { return s.id; }), off: {}, text: {}, titles: {}, custom: [], meet: [] };
          var x = load(this.key(lang));
          if (!x || typeof x !== "object") return b;
          var defaults = b.order.slice();
          (Array.isArray(x.custom) ? x.custom : []).forEach(function (c) {
            if (c && /^custom-\d{1,4}$/.test(c.id) && !b.custom.some(function (o) { return o.id === c.id; })) b.custom.push({ id: c.id, title: str(c.title, 120), text: str(c.text) });
          });
          var valid = defaults.concat(b.custom.map(function (c) { return c.id; }));
          var order = (Array.isArray(x.order) ? x.order : []).filter(function (id, i, a) { return valid.indexOf(id) !== -1 && a.indexOf(id) === i; });
          valid.forEach(function (id) { if (order.indexOf(id) === -1) order.push(id); });  // a section new since the draft
          order.splice(order.indexOf("header"), 1);
          b.order = ["header"].concat(order);
          ["off", "text", "titles"].forEach(function (f) {
            var src = x[f] && typeof x[f] === "object" ? x[f] : {};
            Object.keys(src).forEach(function (id) {
              if (defaults.indexOf(id) === -1) return;
              if (f === "off" && src[id] === true) b.off[id] = true;
              if (f !== "off" && typeof src[id] === "string") b[f][id] = str(src[id], f === "titles" ? 120 : 20000);
            });
          });
          var opts = this.D.meetings.options.map(function (o) { return o.id; });
          b.meet = (Array.isArray(x.meet) ? x.meet : []).filter(function (id) { return opts.indexOf(id) !== -1; });
          return b;
        },
        // Drafts older than three months are removed.
        prune: function () {
          try {
            var cut = this.D.month.split("-").map(Number);
            var min = cut[0] * 12 + cut[1] - 3;
            for (var i = localStorage.length - 1; i >= 0; i--) {
              var k = localStorage.key(i), m = k && /^gv-report:(\d{4})-(\d{2}):/.exec(k);
              if (m && Number(m[1]) * 12 + Number(m[2]) < min) localStorage.removeItem(k);
            }
          } catch (e) { /* no storage */ }
        },
        touch: function () {
          var self = this;
          this._pending = true;
          clearTimeout(this._saveT);
          this._saveT = setTimeout(function () { self.saveNow(); }, 350);
        },
        saveNow: function () {
          clearTimeout(this._saveT);
          this._pending = false;
          var ok = store(this.key(this.rl), this.d);
          this.saveState = ok ? "saved" : "failed";
          if (ok) {
            try { this.savedAt = new Intl.DateTimeFormat(this.L === "es" ? "es-US" : "en-US", { hour: "numeric", minute: "2-digit" }).format(new Date()); } catch (e) { this.savedAt = ""; }
            if (GV.esMeridiem) this.savedAt = GV.esMeridiem(this.savedAt);
          }
        },
        setProf: function (k, v) {
          this.prof[k] = str(v, 60);
          this.saveState = store(PREFIX + "profile", this.prof) ? "saved" : "failed";
          if (this.saveState === "saved") this.saveNow();
        },
        say: function (k, title) { return fmt(this.ui[k], { title: title }); },
        savedText: function () { return this.saveState === "failed" ? this.ui.not_saved : this.saveState === "saved" ? this.ui.saved : this.ui.saved_idle; },

        /* ---- the sections ---- */
        lm: function () { return this.D.langs[this.rl]; },
        def: function (id) { var s = this.lm().sections; for (var i = 0; i < s.length; i++) if (s[i].id === id) return s[i]; return null; },
        cust: function (id) { for (var i = 0; i < this.d.custom.length; i++) if (this.d.custom[i].id === id) return this.d.custom[i]; return null; },
        isCustom: function (id) { return /^custom-/.test(id); },
        defaultText: function (id) {
          if (id === "meetings" && this.d.meet.length) return this.meetText();
          var s = this.def(id);
          return s ? s.text : "";
        },
        textOf: function (id) {
          if (this.isCustom(id)) { var c = this.cust(id); return c ? c.text : ""; }
          return has(this.d.text, id) ? this.d.text[id] : this.defaultText(id);
        },
        titleOf: function (id) {
          if (this.isCustom(id)) { var c = this.cust(id); return (c && clean(c.title)) || this.lm().custom; }
          if (has(this.d.titles, id) && clean(this.d.titles[id])) return this.d.titles[id];
          var s = this.def(id);
          return s ? s.title : id;
        },
        titleValue: function (id) {
          if (this.isCustom(id)) { var c = this.cust(id); return c ? c.title : ""; }
          return has(this.d.titles, id) ? this.d.titles[id] : this.titleOf(id);
        },
        setText: function (id, v) {
          v = str(v);
          if (this.isCustom(id)) { var c = this.cust(id); if (c) c.text = v; }
          else if (v === this.defaultText(id)) delete this.d.text[id];
          else this.d.text[id] = v;
          this.touch();
        },
        setTitle: function (id, v) {
          v = str(v, 120);
          if (this.isCustom(id)) { var c = this.cust(id); if (c) c.title = v; }
          else if (v === (this.def(id) || {}).title) delete this.d.titles[id];
          else this.d.titles[id] = v;
          this.touch();
        },
        isOn: function (id) { return !this.d.off[id]; },
        edited: function (id) { return !this.isCustom(id) && (has(this.d.text, id) || has(this.d.titles, id)); },
        // The list the editor shows: the report's order, each with its number in the report.
        list: function () {
          var self = this, n = 0, total = this.d.order.length;
          return this.d.order.map(function (id, i) {
            var on = self.isOn(id), empty = !String(self.textOf(id)).trim();
            var num = on && !empty && id !== "header" ? ++n : 0;
            return { id: id, i: i, title: self.titleOf(id), on: on, empty: empty, num: num, header: id === "header", custom: self.isCustom(id), edited: self.edited(id), first: i <= 1, last: i === total - 1 };
          });
        },
        toggle: function (id) {
          if (this.d.off[id]) delete this.d.off[id]; else this.d.off[id] = true;
          this.touch();
        },
        canMove: function (id, dir) {
          var i = this.d.order.indexOf(id), j = i + dir;
          return i > 0 && j > 0 && j < this.d.order.length;
        },
        move: function (id, dir) {
          if (!this.canMove(id, dir)) return;
          var o = this.d.order, i = o.indexOf(id);
          o.splice(i, 1);
          o.splice(i + dir, 0, id);
          this.touch();
          GV.announce && GV.announce(fmt(this.ui.moved, { title: this.titleOf(id), n: i + dir, total: o.length - 1 }));
          // Keep the keyboard on the moved section: the same arrow, or the other one at an end.
          var self = this;
          this.$nextTick(function () {
            var want = dir < 0 ? "up" : "down", other = dir < 0 ? "down" : "up";
            var b = self.$root.querySelector('[data-rp-move="' + (self.canMove(id, dir) ? want : other) + '"][data-rp-id="' + id + '"]');
            if (b) b.focus();
          });
        },
        toggleOpen: function (id) {
          var self = this;
          this.open[id] = !this.open[id];
          if (this.open[id]) this.focusSec(id);
          this.$nextTick(function () { self.growAll(); });
        },
        closeEditor: function (id) {
          this.open[id] = false;
          var b = this.$root.querySelector('[data-rp-edit="' + id + '"]');
          if (b) b.focus();
        },
        addCustom: function () {
          var max = 0;
          this.d.custom.forEach(function (c) { max = Math.max(max, Number(c.id.split("-")[1]) || 0); });
          var id = "custom-" + (max + 1);
          this.d.custom.push({ id: id, title: "", text: "" });
          this.d.order.push(id);
          this.open[id] = true;
          this.touch();
          GV.announce && GV.announce(this.ui.added);
          var self = this;
          this.$nextTick(function () {
            var f = document.getElementById("rp-title-" + id);
            if (f) f.focus();
            self.focusSec(id);
          });
        },
        removeCustom: function (id) {
          this.d.custom = this.d.custom.filter(function (c) { return c.id !== id; });
          this.d.order = this.d.order.filter(function (x) { return x !== id; });
          delete this.open[id];
          this.touch();
          GV.announce && GV.announce(this.ui.removed);
          var b = this.$refs.add;
          this.$nextTick(function () { if (b) b.focus(); });
        },
        resetSection: function (id) {
          delete this.d.text[id];
          delete this.d.titles[id];
          this.touch();
          GV.announce && GV.announce(fmt(this.ui.reset_done, { title: this.titleOf(id) }));
          this.$nextTick(function () { var t = document.getElementById("rp-text-" + id); if (t) t.focus(); });
        },
        resetAll: function () {
          if (!window.confirm(this.ui.reset_all_confirm)) return;
          clearTimeout(this._saveT);
          this._pending = false;
          this.d = this.blank();
          this.open = {};
          this.active = "";
          store(this.key(this.rl), null);
          this.saveState = "idle";
          GV.announce && GV.announce(this.ui.reset_all_done);
          this.flash(this.ui.reset_all_done);
        },
        setLang: function (l) {
          if (l === this.rl || !this.D.langs[l]) return;
          if (this._pending) this.saveNow();   // the old language's last changes
          this.rl = l;
          store(PREFIX + "lang", l);
          this.d = this.loadDraft(l);
          this.open = {};
          this.active = "";
          this.waLong = false;
          GV.announce && GV.announce(fmt(this.ui.lang_done, { lang: this.ui["lang_" + l] }));
        },

        /* ---- Grapevine meetings: the county picker ---- */
        meetOpts: function (group) { return this.D.meetings.options.filter(function (o) { return o.group === group; }); },
        picked: function (id) { return this.d.meet.indexOf(id) !== -1; },
        togglePick: function (id) {
          var i = this.d.meet.indexOf(id);
          if (i === -1) this.d.meet.push(id); else this.d.meet.splice(i, 1);
          delete this.d.text.meetings;  // picking writes the section again
          this.touch();
        },
        clearPicks: function () { this.d.meet = []; delete this.d.text.meetings; this.touch(); },
        meetText: function () {
          var rl = this.rl, M = this.lm().meet, picks = this.d.meet;
          var chosen = this.D.meetings.options.filter(function (o) { return picks.indexOf(o.id) !== -1; });
          var lines = [];
          chosen.forEach(function (o) { lines = lines.concat(o.lines[rl] || []); });
          var out = [fmt(M.pick, { where: chosen.map(function (o) { return o.name[rl]; }).join(", ") })];
          out = out.concat(lines.length ? lines : [M.none]);
          out.push(M.more);
          return out.join("\n");
        },

        /* ---- the output ---- */
        filled: function (text) {
          var tk = this.lm().tokens, p = this.prof, lm = this.lm();
          [[tk.district, clean(p.district)], [tk.name, clean(p.name)], [tk.group, clean(p.group)], [tk.role, typeof lm.roles[p.role] === "string" ? lm.roles[p.role] : ""]]
            .forEach(function (m) { if (m[1]) text = text.split(m[0]).join(m[1]); });
          return text;
        },
        outSections: function () {
          var self = this;
          return this.d.order.filter(function (id) { return self.isOn(id); })
            .map(function (id) { return { id: id, title: self.filled(self.titleOf(id)), text: self.filled(self.textOf(id)) }; });
        },
        plain: function () { return compose(this.outSections()); },
        titleLine: function () {
          var h = this.isOn("header") ? clean(this.filled(this.textOf("header")).split("\n")[0]) : "";
          return h || clean(this.filled(this.def("header").text.split("\n")[0]));
        },
        preview: function () { return this.d ? previewHtml(blocks(this.outSections()), this.active) : ""; },
        blanks: function () { var m = this.plain().match(/\[[^\]\n]{1,80}\]/g); return m ? m.length : 0; },
        blanksText: function () { var n = this.blanks(); return n === 0 ? this.ui.blanks_none : n === 1 ? this.ui.blanks_one : fmt(this.ui.blanks, { n: n }); },
        readText: function () {
          var words = this.plain().replace(/https?:\/\/\S+/g, "").split(/\s+/).filter(Boolean).length;
          var n = Math.max(1, Math.round(words / WPM));
          return n === 1 ? this.ui.read_time_one : fmt(this.ui.read_time, { n: n });
        },
        fileBase: function () {
          var d = slug(this.prof.district);
          return "grapevine-la-vina-report-" + (d ? "district-" + d + "-" : "") + this.D.month + "-" + this.rl;
        },
        flash: function (m, btn) {
          var self = this;
          this.msg = m;
          if (GV.announce) GV.announce(m);
          clearTimeout(this._msgT);
          this._msgT = setTimeout(function () { self.msg = ""; }, 6000);
          if (btn) { btn.classList.add("is-done"); setTimeout(function () { btn.classList.remove("is-done"); }, 1600); }
        },
        copyText: function (btn) {
          var self = this;
          writeText(this.plain()).then(function () { self.flash(self.ui.copied_text, btn); }, function () { self.flash(self.ui.copy_failed); });
        },
        copyHtml: function (btn) {
          var self = this, text = this.plain(), html = emailHtml(blocks(this.outSections()), this.rl);
          var fallback = function () {
            if (legacyCopy(text, html)) return self.flash(self.ui.copied_html, btn);
            writeText(text).then(function () { self.flash(self.ui.copied_plain, btn); }, function () { self.flash(self.ui.copy_failed); });
          };
          if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write && window.isSecureContext) {
            try {
              var item = new window.ClipboardItem({ "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([text], { type: "text/plain" }) });
              navigator.clipboard.write([item]).then(function () { self.flash(self.ui.copied_html, btn); }, fallback);
              return;
            } catch (e) { /* older ClipboardItem */ }
          }
          fallback();
        },
        download: function (kind) {
          var name = this.fileBase() + (kind === "txt" ? ".txt" : ".docx");
          var blob = kind === "txt"
            ? new Blob([BOM + this.plain().replace(/\n/g, "\r\n")], { type: "text/plain;charset=utf-8" })
            : docx(blocks(this.outSections()), this.rl, this.titleLine());
          saveBlob(blob, name);
          this.flash(fmt(this.ui.downloaded, { file: name }));
        },
        print: function () {
          var was = document.title;
          document.title = this.titleLine();   // the name "Save as PDF" suggests
          var restore = function () { document.title = was; window.removeEventListener("afterprint", restore); };
          window.addEventListener("afterprint", restore);
          this.$nextTick(function () { window.print(); if (!("onafterprint" in window)) restore(); });
        },
        whatsapp: function () {
          var url = "https://wa.me/?text=" + encodeURIComponent(this.plain());
          if (url.length > WA_MAX) {
            this.waLong = true;
            this.$nextTick(function () { var h = document.getElementById("rp-wa-title"); if (h) h.focus(); });
            return;
          }
          this.waLong = false;
          openLink(url, true);
          this.flash(fmt(this.ui.opening, { app: "WhatsApp" }));
        },
        closeWa: function () {
          this.waLong = false;
          this.$nextTick(function () { var b = document.querySelector("[data-rp-wa]"); if (b) b.focus(); });
        },
        waOpen: function () { openLink("https://wa.me/", true); },
        shareNative: function () {
          if (!navigator.share) return;
          navigator.share({ title: this.titleLine(), text: this.plain() }).catch(function () { /* cancelled */ });
        },
        email: function () {
          var self = this, subject = this.titleLine(), text = this.plain();
          var url = "mailto:?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(text);
          if (url.length <= MAIL_MAX) { openLink(url, false); return; }
          var short = "mailto:?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(this.lm().paste);
          writeText(text).then(function () { self.flash(self.ui.mail_long); }, function () { self.flash(self.ui.copy_failed); })
            .then(function () { openLink(short, false); });
        },

        /* ---- editing comfort ---- */
        // The preview follows the section being edited (only when the preview scrolls by itself).
        focusSec: function (id) {
          this.active = id;
          var box = this.$refs.doc;
          this.$nextTick(function () {
            if (!box) return;
            var el = box.querySelector('[data-sec="' + id + '"]');
            var oy = getComputedStyle(box).overflowY;
            if (!el || (oy !== "auto" && oy !== "scroll") || box.scrollHeight <= box.clientHeight + 4) return;
            var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            box.scrollTo({ top: Math.max(0, el.offsetTop - 16), behavior: reduce ? "auto" : "smooth" });
          });
        },
        grow: function (el) {
          if (!el || (window.CSS && CSS.supports && CSS.supports("field-sizing", "content"))) return;
          el.style.height = "auto";
          el.style.height = el.scrollHeight + 2 + "px";
        },
        growAll: function () {
          var self = this;
          this.$root.querySelectorAll("textarea.rp-text").forEach(function (t) { if (t.offsetParent) self.grow(t); });
        },
      };
    });
  });
})();
