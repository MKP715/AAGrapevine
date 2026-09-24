"""Monthly date rules: "the Nth <weekday> of every month, HH:MM–HH:MM" in the site's time zone.

One rule engine (upcoming_rule_dates) serves both
  * the committee meeting — config/site.yml `meeting:` (e.g. 3rd Wednesday 19:00–20:00 Central), and
  * every entry of `recurring_events:` (e.g. the GV/LV booth at CityWide Dallas, 2nd Saturday
    17:00–20:00) — see build_data.recurring_events().

Dates are counted on the local calendar and each start/end is turned into a real instant with the
time zone's own rules, so a daylight-saving change (or a month / year boundary) never moves an
event by an hour: 17:00 Central is 22:00 UTC in October (CDT) and 23:00 UTC in November (CST).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .common import load_config, to_iso

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4, "sabado": 5,
            "sábado": 5, "domingo": 6}
# "week_of_month" written as a word: second / 2nd / segundo / 2.º / last / último …
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1,
                  "primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercer": 3, "tercero": 3,
                  "tercera": 3, "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "ultimo": -1, "último": -1,
                  "ultima": -1, "última": -1}


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


def weekday_index(v: Any) -> int | None:
    """'saturday' / 'Saturday' / 'Saturdays' / 'sábado' / 'Sábados' → 5 (Monday = 0); None if not understood."""
    s = str(v or "").strip().lower().rstrip(".")
    if s in WEEKDAYS:
        return WEEKDAYS[s]
    if s.endswith("s") and s[:-1] in WEEKDAYS:        # plural: "saturdays", "sábados"
        return WEEKDAYS[s[:-1]]
    return None


def week_of_month_value(v: Any) -> int | None:
    """1–5, or -1 for "the last one" — from 2, "2", "2nd", "second", "segundo", "2.º", "last", "último".
    None when it cannot be understood."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v if v in (1, 2, 3, 4, 5, -1) else None
    s = str(v).strip().lower()
    if s in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[s]
    m = re.fullmatch(r"(-?\d)\s*(?:st|nd|rd|th|\.?\s*[ºoª°]|\.?\s*er|\.?\s*ra)?", s)
    if m and int(m[1]) in (1, 2, 3, 4, 5, -1):
        return int(m[1])
    return None


def ymd_text(v: Any) -> str | None:
    """A skip date from the settings → "YYYY-MM-DD" (YAML turns an unquoted 2027-01-09 into a date)."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v or "").strip()
    try:
        return date.fromisoformat(s).isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None
    except ValueError:                     # "2027-02-30"
        return None


@dataclass(frozen=True)
class MonthlyRule:
    """The Nth weekday of every month, from `start` to `end` (local wall-clock time)."""
    week_of_month: int                     # 1–5, or -1 = the last one of the month
    weekday: int                           # 0 = Monday … 6 = Sunday
    start: tuple[int, int]                 # (hour, minute)
    end: tuple[int, int]                   # (hour, minute); not after start → a one-hour event
    skip: frozenset[str] = frozenset()     # "YYYY-MM-DD" dates that do not happen

    def span(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """(start, end) as used for every date: an end that is missing or not after the start makes
        it a one-hour event (a 23:30 start ends at 23:59, the same day)."""
        (sh, sm), (eh, em) = self.start, self.end
        if (eh, em) <= (sh, sm):
            eh, em = min(sh + 1, 23), (sm if sh < 23 else 59)
        return (sh, sm), (eh, em)


def nth_weekday(y: int, m: int, weekday: int, n: int) -> date | None:
    """The `n`th `weekday` (0 = Monday) of month `m` of year `y` (n = -1: the last one); None when the
    month has no such day (a 5th Saturday)."""
    if n == -1:
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(y, m, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    return d if d.month == m else None


ORD_SHORT = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", -1: "last"}        # (settings messages)
DAY_NAMES_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def check_skip_dates(values: Any, weekday: int, week_of_month: int) -> tuple[set[str], list[str]]:
    """`skip_dates` from the settings (the committee meeting's or a recurring event's) → (the dates that
    really are one of the rule's days, notes for the chair). A value that is not a date, or not the rule's
    day of its month (the Sunday, the 1st Saturday, the wrong month …), would skip nothing: it is ignored
    and a note says which date to use instead."""
    ok: set[str] = set()
    notes: list[str] = []
    for s in values if isinstance(values, (list, tuple)) else [] if values in (None, "") else [values]:
        ymd = ymd_text(s)
        if not ymd:
            notes.append(f"skip date “{s}” is not a date like \"2027-01-09\" — ignored")
            continue
        d = date.fromisoformat(ymd)
        day = nth_weekday(d.year, d.month, weekday, week_of_month)
        if day != d:
            nth = f"{ORD_SHORT[week_of_month]} {DAY_NAMES_EN[weekday]}"
            notes.append(f"skip date “{ymd}” is not the {nth} of its month — ignored ("
                         + (f"that month's is {day.isoformat()})" if day else f"that month has no {nth})"))
            continue
        ok.add(ymd)
    return ok, notes


def upcoming_rule_dates(rule: MonthlyRule, count: int, tz: ZoneInfo, now: datetime | None = None,
                        include_recent_days: int = 0, horizon_months: int | None = None) -> list[dict]:
    """The next `count` dates of a monthly rule that are not over yet (end ≥ now − include_recent_days),
    soonest first: [{"ymd": "2026-10-10", "start": "2026-10-10T22:00:00Z", "end": "2026-10-11T01:00:00Z"}].

    Months are walked on the LOCAL calendar starting with the month before `now` (so an evening event on
    the last day of a month is still found while it is running, even though it is already the next month
    in UTC), for at most `horizon_months` months (default count + 15: a "5th Saturday" rule simply lists
    the months that have one). A date in `rule.skip` is left out and does not count."""
    if count <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=include_recent_days)
    (sh, sm), (eh, em) = rule.span()       # a missing / earlier end → a one-hour event
    local = since.astimezone(tz)
    y, m = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    out: list[dict] = []
    for _ in range(horizon_months if horizon_months is not None else count + 15):
        d = nth_weekday(y, m, rule.weekday, rule.week_of_month)
        if d and d.isoformat() not in rule.skip:
            start = datetime(d.year, d.month, d.day, sh, sm, tzinfo=tz)
            end = datetime(d.year, d.month, d.day, eh, em, tzinfo=tz)
            if end.astimezone(timezone.utc) >= since:
                out.append({"ymd": d.isoformat(), "start": to_iso(start), "end": to_iso(end)})
                if len(out) >= count:
                    break
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def meeting_rule(cfg: dict | None) -> MonthlyRule:
    """config/site.yml `meeting:` → its rule. Tolerant on purpose (the committee meeting must always
    show): anything that cannot be understood falls back to the 3rd Wednesday, 19:00–20:00.
    (The weekday lookup is deliberately NOT weekday_index(): the web pages compute the same meetings
    themselves — eleventy/filters/committee.js meetingDates() — and a more forgiving reading here, e.g.
    of "Wednesdays", would make the two disagree about the day.)"""
    cfg = cfg or {}
    wd = WEEKDAYS.get(str(cfg.get("weekday", "wednesday")).strip().lower(), 2)
    try:
        n = int(cfg.get("week_of_month", 3))
    except (TypeError, ValueError):
        n = 3
    if n not in (1, 2, 3, 4, 5, -1):
        n = 3
    sh, sm = parse_hhmm(cfg.get("start"), (19, 0))
    eh, em = parse_hhmm(cfg.get("end"), ((sh + 1) % 24, sm))
    # Only real meeting days are skipped; anything else is ignored (meeting_skip_notes() tells the chair).
    skip, _notes = check_skip_dates(cfg.get("skip_dates"), wd, n)
    return MonthlyRule(week_of_month=n, weekday=wd, start=(sh, sm), end=(eh, em), skip=frozenset(skip))


def meeting_skip_notes(cfg: dict | None) -> list[str]:
    """Notes about config/site.yml `meeting: skip_dates` — the same check as a recurring event's skip dates
    (build_data puts them in status.json problems.meeting → the Actions run summary)."""
    rule = meeting_rule(cfg)
    return check_skip_dates((cfg or {}).get("skip_dates"), rule.weekday, rule.week_of_month)[1]


def upcoming_meetings(count: int = 12, include_recent_days: int = 0) -> list[dict]:
    """The next `count` committee meetings (config/site.yml `meeting:`)."""
    cfg = load_config()
    tz = ZoneInfo((cfg.get("site", {}) or {}).get("timezone", "America/Chicago"))
    return upcoming_rule_dates(meeting_rule(cfg.get("meeting", {})), count, tz,
                               include_recent_days=include_recent_days)


if __name__ == "__main__":
    for x in upcoming_meetings(4):
        print(x)
