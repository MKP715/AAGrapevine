"""Committee meeting dates from config/site.yml `meeting` (e.g. 3rd Wednesday 19:00–20:00 Central)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .common import load_config, to_iso

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4, "sabado": 5,
            "sábado": 5, "domingo": 6}


def parse_hhmm(v, default: tuple[int, int]) -> tuple[int, int]:
    """A time from config/site.yml → (hour, minute); `default` when it cannot be understood.

    The file is edited by hand, so accept every way a time can arrive:
      "19:00" / "7:00 PM" / "7pm" / 19 (hour only) — and an UNQUOTED 19:00, which the YAML reader
      turns into the number 1140 (minutes, base 60)."""
    if isinstance(v, bool) or v is None or v == "":
        return default
    if isinstance(v, float):
        v = f"{v:.2f}"                     # unquoted 19.30 → "19.30"
    if isinstance(v, int):
        if 0 <= v <= 23:                   # "start: 19" → 19:00
            return v, 0
        if v >= 24 * 60:                   # unquoted 19:00:00 → 68400 (seconds)
            h, m = v // 3600, v % 3600 // 60
        else:                              # unquoted 19:00 → 1140 (minutes)
            h, m = divmod(v, 60)
        return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else default
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.h](\d{2})(?::\d{2})?)?\s*(?:([ap])\.?\s*m\.?)?\s*", str(v), re.I)
    if not m:
        return default
    h, mi = int(m[1]), int(m[2] or 0)
    if m[3]:
        if not 1 <= h <= 12:
            return default
        h = h % 12 + (12 if m[3].lower() == "p" else 0)
    return (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else default


def _nth_weekday(y: int, m: int, weekday: int, n: int) -> date | None:
    if n == -1:
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(y, m, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    return d if d.month == m else None


def upcoming_meetings(count: int = 12, include_recent_days: int = 0) -> list[dict]:
    cfg = load_config().get("meeting", {}) or {}
    tz = ZoneInfo(load_config().get("site", {}).get("timezone", "America/Chicago"))
    wd = WEEKDAYS.get(str(cfg.get("weekday", "wednesday")).strip().lower(), 2)
    try:
        n = int(cfg.get("week_of_month", 3))
    except (TypeError, ValueError):
        n = 3
    if n not in (1, 2, 3, 4, 5, -1):
        n = 3
    sh, sm = parse_hhmm(cfg.get("start"), (19, 0))
    eh, em = parse_hhmm(cfg.get("end"), ((sh + 1) % 24, sm))
    if (eh, em) <= (sh, sm):               # missing / earlier end → a one-hour meeting
        eh, em = min(sh + 1, 23), (sm if sh < 23 else 59)
    skip = set(str(s) for s in (cfg.get("skip_dates") or []))
    now = datetime.now(timezone.utc) - timedelta(days=include_recent_days)
    out = []
    y, m = now.year, now.month
    for _ in range(count + 14):
        d = _nth_weekday(y, m, wd, n)
        if d and d.isoformat() not in skip:
            start = datetime(d.year, d.month, d.day, sh, sm, tzinfo=tz)
            end = datetime(d.year, d.month, d.day, eh, em, tzinfo=tz)
            if end.astimezone(timezone.utc) >= now:
                out.append({"ymd": d.isoformat(), "start": to_iso(start), "end": to_iso(end)})
                if len(out) >= count:
                    break
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


if __name__ == "__main__":
    for x in upcoming_meetings(4):
        print(x)
