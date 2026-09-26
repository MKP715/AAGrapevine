// The ONE serializer for data the pages embed in a <script type="application/json"> (or ld+json)
// block, used by the area filters (media, library, published, report — mediaJson, jsonScript, pwJson,
// rpJson). JSON.stringify, then "<", ">" and "&" as \u escapes (no text can close the <script>, open
// a comment or form an entity) and U+2028 / U+2029 (line separators that older JavaScript parsers
// reject inside strings). JSON.parse gives back exactly the same values.
export function scriptJson(value) {
  return JSON.stringify(value ?? null)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}
