"""Votes: the Useful and Noise links in alerts, and counting them.

A vote is an email someone chose to send from an alert's link, to the agenda
inbox's +votes address, with the subject "Useful: {watchlist} / {term} [{alert
id}]" or "Noise: ...". Nothing loads when an alert is opened and nothing is
counted on a server. The agenda-email adapter reads votes with the agendas and
never reads one as an agenda; the run counts them here.

- Only a subject in that exact format counts, and only when its alert id is one
  the state remembers and its watchlist and term are ones that alert matched.
  The subject is untrusted: nothing from it but the vote goes into the state or
  an email. The watchlist, term, body and date come from the state.
- One vote per person per alert (an updated alert is the same one); later ones
  are left out. The state is public (the `data` branch), so it never holds an
  address. The adapter hands over a hash of the sender keyed with a secret, so
  nobody can check a guessed address against it, and the state keeps a hash of
  that and the alert, so one person's votes on different alerts can't be linked.
- Each term keeps the dates of its votes. The morning digest lists the votes
  since the last one and names a term to review once it has REVIEW_NOISE noise
  votes and no useful ones in REVIEW_DAYS days. The app never changes the
  watchlists: Drew decides.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

# The agenda inbox, which the agenda-email adapter reads; "+votes" keeps votes
# apart from agendas.
ADDRESS = "haystackneedle.agendas+votes@fastmail.com"
KINDS = ("Useful", "Noise")
SUBJECT = re.compile(r"(Useful|Noise): (.+) \[([0-9a-f]{10})\]")
REVIEW_NOISE = 3
REVIEW_DAYS = 30


def subject(vote: str, watchlist: str, term: str, alert_id: str) -> str:
    return f"{vote}: {watchlist} / {term} [{alert_id}]"


def parse(text: str | None) -> tuple[str, str, str] | None:
    """("useful" or "noise", "watchlist / term", alert id) from a vote's subject, else None."""
    m = SUBJECT.fullmatch(" ".join((text or "").split()))
    return (m[1].lower(), m[2], m[3]) if m else None


def _day(d: str) -> str:
    d = date.fromisoformat(d)
    return f"{d:%a %b} {d.day}"  # as alerts write it: "Wed Oct 21"


def count(state: dict, mail: list[dict]) -> int:
    """Counts the votes the adapter read: {"subject", "voter" (keyed hash or None),
    "date" (ISO, Pacific)}. Returns how many counted."""
    tally = state["votes"]
    by_id = {}
    for stage, entry in state["alerts"].items():
        for ident in [entry["id"], *entry.get("earlier_ids", [])]:
            by_id[ident] = (stage, entry)
    counted = 0
    for vote in mail:
        parsed = parse(vote["subject"])
        stage, entry = by_id.get(parsed[2], (None, None)) if parsed else (None, None)
        pairs = [sig.split("|", 2)[:2] for sig in entry["sigs"]] if entry else []
        pair = next((p for p in pairs if " ".join(f"{p[0]} / {p[1]}".split()) == parsed[1]), None)
        if not pair or not vote["voter"]:
            tally["skipped"] += 1
            continue
        voter = hashlib.sha256(f"{vote['voter']}|{stage}".encode()).hexdigest()[:16]
        if voter in entry.setdefault("voters", []):
            continue  # this person voted on this alert already
        entry["voters"].append(voter)
        (watchlist, term), kind = pair, parsed[0]
        tally["terms"].setdefault(f"{watchlist}|{term}", {"useful": [], "noise": []})[kind].append(vote["date"])
        body = state["bodies"].get(entry.get("body"), {}).get("name") or entry.get("body_name", "")
        tally["new"].append({"vote": kind, "watchlist": watchlist, "term": term, "body": body,
                             "date": entry["date"]})
        counted += 1
    return counted


def since_digest(state: dict) -> list[str]:
    """One line per vote since the last digest, with the term's tally so far."""
    lines = []
    for v in state["votes"]["new"]:
        dates = state["votes"]["terms"].get(f"{v['watchlist']}|{v['term']}", {})
        useful, noise = len(dates.get("useful", [])), len(dates.get("noise", []))
        lines.append(f"{v['vote'].capitalize()}: {v['watchlist']} / {v['term']}, on {v['body']}, "
                     f"{_day(v['date'])} (so far {useful} useful, {noise} noise)")
    return lines


def to_review(state: dict, today: date, terms) -> list[str]:
    """Terms still in the watchlists with REVIEW_NOISE noise votes and no useful
    ones in the last REVIEW_DAYS days. terms: the run's compiled terms."""
    current = {f"{t.watchlist}|{t.text}" for t in terms}
    since = (today - timedelta(days=REVIEW_DAYS)).isoformat()
    lines = []
    for name, dates in sorted(state["votes"]["terms"].items()):
        noise = [d for d in dates["noise"] if d >= since]
        if name in current and len(noise) >= REVIEW_NOISE and not any(d >= since for d in dates["useful"]):
            watchlist, term = name.split("|", 1)
            lines.append(f"{watchlist} / {term}: {len(noise)} noise votes and no useful ones "
                         f"in the last {REVIEW_DAYS} days")
    return lines


def prune(state: dict, cutoff: str) -> None:
    terms = state["votes"]["terms"]
    for name, dates in list(terms.items()):
        for kind in dates:
            dates[kind] = [d for d in dates[kind] if d >= cutoff]
        if not any(dates.values()):
            del terms[name]
