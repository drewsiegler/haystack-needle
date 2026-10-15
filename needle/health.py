"""Health checks, because no alert looks exactly like no news.

- A source that fails two runs in a row gets one email until it recovers.
- A body that meets regularly but shows no meeting for 14 days gets one email
  until a meeting shows up again.
- Each successful run pings Healthchecks.io, which emails when the pings stop
  (GitHub can delay or pause scheduled runs).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from statistics import median

import httpx

from needle.alerts import day_text, oneline

FAILS_TO_WARN = 2
QUIET_DAYS = 14
REGULAR_GAP_DAYS = 14  # a body "meets regularly" if its usual gap between meetings is this or less
MIN_DATES = 3  # meeting dates needed before judging whether a body meets regularly
KEEP_DATES = 12


def record_source(state: dict, key: str, name: str, error: str | None,
                  calendar: str) -> tuple[str, str] | None:
    """Count consecutive failures; return a notice (subject, text) the run it reaches FAILS_TO_WARN."""
    entry = state["sources"].setdefault(key, {"fails": 0, "error": "", "warned": False})
    if error is None:
        entry.update(fails=0, error="", warned=False)
        return None
    entry["fails"] += 1
    entry["error"] = oneline(error, 300)
    if entry["fails"] < FAILS_TO_WARN or entry["warned"]:
        return None
    entry["warned"] = True
    return (f"[Haystack Needle] Can't read {name}: failed {entry['fails']} runs in a row",
            f"The last {entry['fails']} runs couldn't read {name}'s agendas, so no alerts can "
            f"come from {name} until this is fixed.\n\n"
            f"Last error: {entry['error']}\n\n"
            f"What to do: open {calendar} in a browser. If meetings show there, the adapter "
            "needs a fix; if the site is down, it will likely recover on its own. You'll get "
            "this email once per outage.\n")


def record_meetings(state: dict, body_key: str, name: str, dates: list[date]) -> None:
    entry = state["bodies"].setdefault(body_key, {"name": name, "dates": [], "warned": False, "agendas": {}})
    entry["name"] = name
    entry["dates"] = sorted(set(entry["dates"]) | {d.isoformat() for d in dates})[-KEEP_DATES:]


def quiet_bodies(state: dict, today: date, skip: set[str], calendars: dict[str, str]) -> list[tuple[str, str]]:
    """Notices for bodies that meet regularly but show no meeting in QUIET_DAYS days.

    skip holds source keys that failed this run; they get their own notice.
    calendars maps a source key to its public calendar.
    """
    notices = []
    for body_key, entry in state["bodies"].items():
        source_key = body_key.rsplit("/body/", 1)[0]
        dates = [date.fromisoformat(d) for d in entry["dates"]]
        if source_key in skip or len(dates) < MIN_DATES:
            continue
        gap = median((b - a).days for a, b in zip(dates, dates[1:]))
        quiet = max(dates) < today - timedelta(days=QUIET_DAYS)
        if not quiet:
            entry["warned"] = False
            continue
        if gap > REGULAR_GAP_DAYS or entry["warned"]:
            continue
        entry["warned"] = True
        name = oneline(entry["name"], 120)
        notices.append((
            f"[Haystack Needle] No meetings from {name} in {QUIET_DAYS} days",
            f"{name} usually meets about every {gap:g} days, but its last meeting was "
            f"{day_text(max(dates))}, {max(dates).year}, and none are posted ahead. It may be "
            "on a break, or the agenda site may have changed how it lists meetings.\n\n"
            f"What to do: check {calendars.get(source_key, 'its agenda site')}. If meetings "
            "are listed there, Haystack Needle is missing them and the adapter needs a fix. "
            "You'll get this email once until a meeting shows up again.\n",
        ))
    return notices


def ping(url: str, fail: bool = False) -> None:
    """Tell Healthchecks.io the run finished (or failed). The URL is a secret, so it's never printed."""
    try:
        httpx.get(url.rstrip("/") + ("/fail" if fail else ""), timeout=15)
    except httpx.HTTPError as error:
        print(f"Couldn't ping Healthchecks.io ({type(error).__name__}).", file=sys.stderr)
