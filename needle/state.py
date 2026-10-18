"""The state file: what earlier runs saw and sent, kept on the `data` branch.

It stays small on purpose: no agenda text and no PDFs, only dates, hashes and
the short snippets that matched, so a run can skip anything that hasn't
changed since the last one and still rebuild the same matches.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from needle import votes

SCHEMA = 1
KEEP_DAYS = 400  # forget items, attachments, meetings and alerts older than this


def empty() -> dict:
    return {
        "schema": SCHEMA,
        # The terms matched ("i|Flock", or "c|ICE" for case-sensitive); a new one drops cached matches.
        # None: not recorded yet.
        "terms": None,
        "last_full_run": None,  # UTC, ISO format
        "last_digest": None,  # Pacific date of the last 7 a.m. digest
        "items": {},  # item key -> {"date", "modified", "read", "hits": [snippet, ...]}
        "attachments": {},  # attachment key -> {"seen", "modified", "sha256", "pages", "hits": {page: [snippet]}}
        "meetings": {},  # meeting key -> {"date", "modified", "comment", "comment_email"}
        # stage key -> {"id", "thread", "body", "date", "sigs", "sent", "priority"}, and once there are
        # any, "earlier_ids" (the alert's ids before an update) and "voters" (hashes, never addresses)
        "alerts": {},
        "digest": [],  # alerts waiting for the 7 a.m. digest, ready to render
        "bodies": {},  # body key -> {"name", "dates", "warned", "agendas": {meeting id: {"date", "hits"}}}
        "sources": {},  # adapter/client -> {"fails", "error", "warned"}
        "boilerplate": {},  # "body key|watchlist|term" -> {"note", "reported"}: held back as likely boilerplate
        "posted": {},  # meeting key -> {"date", "line", "url", "reported"}: agendas emailed as a notice only, to check by hand
        # Useful and Noise votes (needle/votes.py): "terms": {"watchlist|term": {"useful": [date],
        # "noise": [date]}}, "new": votes since the last digest, "skipped": vote emails not counted
        "votes": {"terms": {}, "new": [], "skipped": 0},
        # Other organizers (needle/subscribers.py), each known only by an HMAC of their address:
        # hash -> {"welcomed": Pacific date or None, "held": alerts the send cap held back,
        # "digest": alerts for their next digest, "digest_sent": Pacific date}
        "subscribers": {},
        # Subscribers' own terms already listed for the owner, by label (an HMAC), and when
        "custom": {"labels": [], "on": None},
        # Today's sends (Pacific), for the cap; since the owner's last digest, subscribers'
        # emails that had to wait and subscribers whose address was refused, by number
        "sends": {"day": None, "count": 0, "held": 0, "refused": []},
        "cache": {},  # adapter -> what it keeps between runs (IQM2: agenda outlines); the adapter prunes it
    }


def load(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return empty()
    state = json.loads(path.read_text())
    if state.get("schema") != SCHEMA:
        raise SystemExit(f"{path} has state schema {state.get('schema')}; this code reads {SCHEMA}.")
    for key, value in empty().items():
        state.setdefault(key, value)
    return state


def save(state: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(path)


def use_watchlists(state: dict, terms) -> None:
    """Drop cached matches when a term is added or made to match any case, so it's
    looked for everywhere.

    The saved snippets hold the text around each term's first match, and they're
    matched again with the current terms, so taking a term out, making one
    case-sensitive, moving one to another watchlist or changing a priority needs
    nothing read again.
    """
    now = {f"{'i' if t.pattern.flags & re.IGNORECASE else 'c'}|{t.text}" for t in terms}
    before = state["terms"]
    # Until Oct 18, 2026 the state kept only a hash of the file. The first run
    # after that takes the terms as they are: the one change since (RTC taken out
    # of tenants) added none.
    if before is not None and not all(k in before or f"i|{k[2:]}" in before for k in now):
        state["items"].clear()
        state["attachments"].clear()
    state["terms"] = sorted(now)
    state.pop("watchlists", None)  # the old hash


def prune(state: dict, today: date) -> None:
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for section, field in (("items", "date"), ("attachments", "seen"),
                           ("meetings", "date"), ("alerts", "date"), ("posted", "date")):
        for key in [k for k, v in state[section].items() if (v.get(field) or "") < cutoff]:
            del state[section][key]
    votes.prune(state, cutoff)
    for body in state["bodies"].values():
        agendas = body.get("agendas", {})
        for key in [k for k, v in agendas.items() if v["date"] < cutoff]:
            del agendas[key]
