"""Run the site's JavaScript (eleventy/filters/*.js, src/_data/*.js) from a unittest, with Node.js.

    from nodejs import run_js            # (tests/ is on sys.path under "unittest discover -s tests")
    out = run_js(self, "out(filters.axPhone(input.site, [], 'en'))", data={"site": {...}})

The script is an ES module run from the repository root. Before it runs, the harness does what
eleventy.config.js does at build time: it registers every filter (the area files in eleventy/filters/
get the same helpers — translateKey, pickLang, fmtDate …), so a filter behaves exactly as on the pages.
In the script:
    filters        {name: function} — every Eleventy filter
    imp(path)      import a repository file (e.g. imp("eleventy/filters/report.js"))
    input          the JSON `data` passed from Python (null without it)
    out(value)     hand a JSON value back to Python (the last call wins)
I18N_STRICT=1 is set, so a missing i18n key throws — as in the CI build.

A test is skipped when Node.js is missing, and — for scripts that need the site's npm packages
(needs_modules=True, the default) — when node_modules is missing ("npm ci"). CI installs both for
the tests (.github/workflows/check.yml).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r"""
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
const imp = (p) => import(pathToFileURL(path.resolve(p)).href);
const input = JSON.parse(fs.readFileSync(0, "utf8") || "null");
let __out = null;
const out = (v) => { __out = v; };
const filters = {};
if (globalThis.__NEEDS_MODULES__) {
  const plugins = [];
  const cfg = new Proxy({}, {
    get(_t, name) {
      if (name === "addFilter" || name === "addNunjucksFilter") return (n, fn) => { filters[n] = fn; };
      if (name === "addPlugin") return (fn) => { plugins.push(fn); };
      return () => ({ add() {} });
    },
  });
  const conf = await imp("eleventy.config.js");
  conf.default(cfg);
  // the area filters (anonymous plugins); Eleventy's own plugins are not needed here
  for (const fn of plugins) if (typeof fn === "function" && !fn.name) await fn(cfg);
}
"""
EPILOGUE = r"""
process.stdout.write("\n@@JSON@@" + JSON.stringify(__out) + "\n");
"""


def node_path() -> str | None:
    return shutil.which("node")


def run_js(case: unittest.TestCase, script: str, data: Any = None, needs_modules: bool = True,
           env: dict[str, str] | None = None, timeout: int = 180) -> Any:
    node = node_path()
    if not node:
        case.skipTest("Node.js is not installed")
    if needs_modules and not (ROOT / "node_modules" / "@11ty" / "eleventy").is_dir():
        case.skipTest("the site's npm packages are not installed (npm ci)")
    code = f"globalThis.__NEEDS_MODULES__ = {str(bool(needs_modules)).lower()};\n" + PRELUDE + script + EPILOGUE
    r = subprocess.run([node, "--input-type=module", "-e", code], cwd=ROOT, input=json.dumps(data),
                       capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                       env={**os.environ, "I18N_STRICT": "1", "NODE_NO_WARNINGS": "1", **(env or {})})
    if r.returncode != 0:
        case.fail(f"node exited with {r.returncode}:\n{r.stderr[-4000:]}")
    marker = r.stdout.rfind("@@JSON@@")
    if marker < 0:
        case.fail(f"no result from node:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return json.loads(r.stdout[marker + len("@@JSON@@"):].strip())
