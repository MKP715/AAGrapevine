"""The daily update: run every content-sync module, then assemble the site data.

    python -m scripts.sync.run_all                       # everything (what GitHub Actions runs daily)
    python -m scripts.sync.run_all --crawl-minutes 40    # crawl budget (or env GV_CRAWL_MINUTES)
    python -m scripts.sync.run_all --crawl-minutes 0     # everything except the PDF crawl
    python -m scripts.sync.run_all --quick               # push/edit refresh: drive, announcements,
                                                         #   podcasts (cheap) + build_data; no crawl
    python -m scripts.sync.run_all --only youtube,podcasts
    python -m scripts.sync.run_all --skip crawl --no-translate

Order: drive, announcements, podcasts, youtube, instagram, articles, editorial, weekly_open,
events_external, crawl (last, time-boxed), then build_data (which translates).

Each module runs in this same process (so the polite crawl delay for aagrapevine.org /
aalavina.org is shared) and is isolated: if one fails — or is missing — it is logged and the
others still run; its previous data is kept. The exit code is 0 even when a module fails
(a soft failure, shown on /status/); it is non-zero ONLY if build_data fails (then the site
would not update, so the GitHub Action should go red).
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
import time
import traceback
from pathlib import Path

from .common import RAW_DIR, get_logger, load_raw, run_module

log = get_logger("run_all")

MODULES = ["drive", "announcements", "podcasts", "youtube", "instagram", "articles", "editorial",
           "weekly_open", "events_external", "crawl"]
RAW_NAME = {"crawl": "pdfs"}            # module → data/raw/<name>.json it writes (default: same name)

# --quick (a settings/content edit was pushed): only the sources that are cheap and do not touch
# aagrapevine.org / aalavina.org (5 s crawl delay), then build_data. The daily run does the rest.
QUICK_MODULES = ("drive", "announcements", "podcasts")
# Flags for the slow, optional parts of a module under --quick (also with --only … --quick).
# They are only passed if the module supports them.
QUICK_ARGS = {
    "youtube": ["--no-backfill"],
    "podcasts": ["--no-discover"],
    "articles": ["--no-details"],
    "instagram": ["--no-enrich"],
}


def _supports(mod, flag: str) -> bool:
    try:
        return f'"{flag}"' in inspect.getsource(mod) or f"'{flag}'" in inspect.getsource(mod)
    except (OSError, TypeError):
        return False


def _raw_summary(name: str) -> dict:
    raw = load_raw(RAW_NAME.get(name, name))
    items = raw.get("items") or []
    stats = raw.get("stats") or {}
    new = stats.get("new")
    return {"ok": raw.get("ok"), "items": sum(1 for i in items if i.get("status", "ok") != "gone"),
            "new": new if isinstance(new, int) else None, "error": raw.get("error"),
            "exists": (RAW_DIR / f"{RAW_NAME.get(name, name)}.json").exists()}


def run_source(name: str, argv: list[str]) -> dict:
    """Import scripts.sync.<name> lazily and run its main(argv) through run_module()."""
    t0 = time.monotonic()
    row = {"module": name, "status": "ok", "seconds": 0.0, "items": None, "new": None, "note": ""}
    try:
        mod = importlib.import_module(f"scripts.sync.{name}")
    except ModuleNotFoundError as e:
        row.update(status="missing" if e.name == f"scripts.sync.{name}" else "import error",
                   note=f"{type(e).__name__}: {e}"[:160])
        log.error("%s: %s — skipped", name, row["note"])
        return row
    except Exception as e:
        row.update(status="import error", note=f"{type(e).__name__}: {e}"[:160])
        log.error("%s could not be imported — skipped\n%s", name, traceback.format_exc())
        return row
    fn = getattr(mod, "main", None)
    if not callable(fn):
        row.update(status="missing", note="no main()")
        return row
    args = list(argv)
    dropped = [a for a in args if a.startswith("--") and not _supports(mod, a)]
    if dropped:
        log.warning("%s does not support %s — ignored", name, dropped)
        args = [a for a in args if a not in dropped]

    def call() -> None:
        try:
            fn(args)
        except SystemExit as e:        # argparse error / explicit exit inside the module
            if e.code not in (0, None):
                raise RuntimeError(f"exited with code {e.code}") from None

    log.info("──── %s %s", name, " ".join(args))
    before = _raw_summary(name)
    run_module(RAW_NAME.get(name, name), call)     # never raises; marks the raw file ok=false on crash
    after = _raw_summary(name)
    row["seconds"] = round(time.monotonic() - t0, 1)
    row["items"] = after["items"]
    row["new"] = after["new"] if after["new"] is not None else max(0, after["items"] - before["items"])
    if not after["exists"]:
        row.update(status="failed", note="wrote no data")
    elif after["ok"] is False:
        row.update(status="failed", note=str(after["error"] or "")[:160])
    return row


def run_build(no_translate: bool, out: str | None = None, translate_minutes: float | None = None) -> dict:
    t0 = time.monotonic()
    row = {"module": "build_data", "status": "ok", "seconds": 0.0, "items": None, "new": None, "note": ""}
    try:
        from . import build_data
        args = (["--no-translate"] if no_translate else []) + (["--out", out] if out else [])
        if translate_minutes is not None:
            args += ["--translate-minutes", f"{translate_minutes:g}"]
        rc = build_data.main(args)
        if rc not in (0, None):
            row.update(status="failed", note=f"exit code {rc}")
    except SystemExit as e:
        if e.code not in (0, None):
            row.update(status="failed", note=f"exit code {e.code}")
    except Exception as e:
        row.update(status="failed", note=f"{type(e).__name__}: {e}"[:200])
        log.error("build_data failed:\n%s", traceback.format_exc())
    row["seconds"] = round(time.monotonic() - t0, 1)
    try:
        st = json.loads(((Path(out) if out else RAW_DIR.parent / "site") / "status.json").read_text(encoding="utf-8"))
        tr = st.get("translations") or {}
        counts = st.get("counts") or {}
        row["items"] = sum(v for k, v in counts.items() if isinstance(v, int) and k not in ("whatsnew", "districts"))
        row["note"] = row["note"] or (f"translated {tr.get('translated_this_run', 0)} new in {tr.get('seconds', 0)}s, "
                                      f"{tr.get('pending', 0)} pending, {tr.get('rejected_by_guard', 0)} kept original")
    except Exception:
        pass
    return row


def print_table(rows: list[dict], total_seconds: float | None = None) -> None:
    head = f"{'module':<16}{'status':<14}{'secs':>7}{'items':>8}{'new':>6}  note"
    lines = [head, "─" * len(head)]
    for r in rows:
        items = "" if r["items"] is None else str(r["items"])
        new = "" if r["new"] is None else str(r["new"])
        lines.append(f"{r['module']:<16}{r['status']:<14}{r['seconds']:>7.1f}{items:>8}{new:>6}  {r['note']}")
    failed = [r["module"] for r in rows if r["status"] not in ("ok", "skipped")]
    if total_seconds is not None:
        lines += ["─" * len(head), f"{'total':<16}{'':<14}{total_seconds:>7.1f}{'':>8}{'':>6}  "
                  + (f"problems: {', '.join(failed)}" if failed else "all ok")]
    print("\n" + "\n".join(lines) + "\n", flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:                        # nice table on the GitHub Actions run page
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("### Daily content update\n\n| module | status | seconds | items | new | note |\n"
                        "|---|---|---:|---:|---:|---|\n")
                for r in rows:
                    icon = {"ok": "✅", "skipped": "⏭️"}.get(r["status"], "⚠️")
                    f.write(f"| {r['module']} | {icon} {r['status']} | {r['seconds']} | {r['items'] if r['items'] is not None else ''}"
                            f" | {r['new'] if r['new'] is not None else ''} | {str(r['note']).replace('|', '/')} |\n")
                if total_seconds is not None:
                    f.write(f"| **total** | | {round(total_seconds, 1)} | | | {', '.join(failed) or 'all ok'} |\n")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.sync.run_all", description="Run the daily content update.")
    ap.add_argument("--only", help=f"comma-separated modules to run (of: {', '.join(MODULES)})")
    ap.add_argument("--skip", help="comma-separated modules to skip")
    ap.add_argument("--crawl-minutes", type=float, default=None, metavar="N",
                    help="time box for the PDF crawl in minutes; 0 = no crawl "
                         "(default: config sources.crawler.minutes_per_run / env GV_CRAWL_MINUTES)")
    ap.add_argument("--quick", action="store_true",
                    help=f"fast refresh: only {', '.join(QUICK_MODULES)} (cheap options) + build_data; "
                         "no crawl unless --crawl-minutes N (N > 0) is also given")
    ap.add_argument("--no-translate", action="store_true", help="build without running the translation model")
    ap.add_argument("--translate-minutes", type=float, default=None, metavar="N",
                    help="time box for new translations in build_data (default 40 / env GV_TRANSLATE_MINUTES)")
    ap.add_argument("--no-build", action="store_true", help="only run the sync modules")
    ap.add_argument("--out", help="write the site data here instead of data/site (testing)")
    a = ap.parse_args(argv)

    only = [m.strip() for m in (a.only or "").split(",") if m.strip()]
    skip = {m.strip() for m in (a.skip or "").split(",") if m.strip()}
    unknown = [m for m in list(only) + list(skip) if m not in MODULES and m != "build_data"]
    if unknown:
        log.warning("unknown module name(s) ignored: %s", unknown)
    crawl_minutes = a.crawl_minutes
    if crawl_minutes is None and os.environ.get("GV_CRAWL_MINUTES"):
        try:
            crawl_minutes = float(os.environ["GV_CRAWL_MINUTES"])
        except ValueError:
            log.warning("GV_CRAWL_MINUTES=%r is not a number — ignored", os.environ["GV_CRAWL_MINUTES"])
    if crawl_minutes is not None and crawl_minutes < 0:
        log.warning("--crawl-minutes %g is negative — treated as 0 (no crawl)", crawl_minutes)
        crawl_minutes = 0.0

    def skipped(name: str, why: str) -> dict:
        return {"module": name, "status": "skipped", "seconds": 0.0, "items": None, "new": None, "note": why}

    t0 = time.monotonic()
    rows = []
    for name in MODULES:
        if (only and name not in only) or name in skip:
            continue
        args: list[str] = []
        if name == "crawl":
            if crawl_minutes is not None and crawl_minutes <= 0:
                rows.append(skipped(name, "--crawl-minutes 0"))
                continue
            if a.quick and a.crawl_minutes is None:          # (an env default never forces a crawl)
                rows.append(skipped(name, "--quick"))
                continue
            if crawl_minutes is not None:
                args += ["--minutes", f"{crawl_minutes:g}"]
        elif a.quick and not only and name not in QUICK_MODULES:
            rows.append(skipped(name, "--quick"))
            continue
        if a.quick:
            args += QUICK_ARGS.get(name, [])
        rows.append(run_source(name, args))

    build_ok = True
    if not a.no_build and "build_data" not in skip:      # --only X still rebuilds the site data
        row = run_build(a.no_translate, a.out, a.translate_minutes)
        rows.append(row)
        build_ok = row["status"] == "ok"
    print_table(rows, time.monotonic() - t0)
    log.info("total %.1f min", (time.monotonic() - t0) / 60)
    return 0 if build_ok else 1


if __name__ == "__main__":
    sys.exit(main())
