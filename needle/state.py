"""The state file: what earlier runs saw and sent, kept on the `data` branch.

It stays small on purpose: no agenda text and no PDFs, only dates, hashes and
the short snippets that matched, so a run can skip anything that hasn't
changed since the last one and still rebuild the same matches.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

SCHEMA = 1
KEEP_DAYS = 400  # forget items, attachments, meetings and alerts older than this


def empty() -> dict:
    return {
        "schema": SCHEMA,
        "watchlists": "",  # hash of watchlists.yaml; when it changes, cached matches are dropped
        "last_full_run": None,  # UTC, ISO format
        "last_digest": None,  # Pacific date of the last 7 a.m. digest
        "items": {},  # item key -> {"date", "modified", "read", "hits": [snippet, ...]}
        "attachments": {},  # attachment key -> {"seen", "modified", "sha256", "pages", "hits": {page: [snippet]}}
        "meetings": {},  # meeting key -> {"date", "modified", "comment", "comment_email"}
        "alerts": {},  # stage key -> {"id", "thread", "body", "date", "sigs", "sent", "priority"}
        "digest": [],  # alerts waiting for the 7 a.m. digest, ready to render
        "bodies": {},  # body key -> {"name", "dates", "warned", "agendas": {meeting id: {"date", "hits"}}}
        "sources": {},  # adapter/client -> {"fails", "error", "warned"}
        "boilerplate": {},  # "body key|watchlist|term" -> {"note", "reported"}: held back as likely boilerplate
        "posted": {},  # meeting key -> {"date", "line", "url", "reported"}: agendas emailed as a notice only, to check by hand
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


def use_watchlists(state: dict, digest: str) -> None:
    """Drop cached matches when the watchlists change, so new terms are looked for everywhere."""
    if state["watchlists"] != digest:
        state["items"].clear()
        state["attachments"].clear()
        state["watchlists"] = digest


def prune(state: dict, today: date) -> None:
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for section, field in (("items", "date"), ("attachments", "seen"),
                           ("meetings", "date"), ("alerts", "date"), ("posted", "date")):
        for key in [k for k, v in state[section].items() if (v.get(field) or "") < cutoff]:
            del state[section][key]
    for body in state["bodies"].values():
        agendas = body.get("agendas", {})
        for key in [k for k, v in agendas.items() if v["date"] < cutoff]:
            del agendas[key]
