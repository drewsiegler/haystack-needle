"""Votes: the Useful and Noise links in alerts, and counting them.

A vote is an email someone chose to send from an alert's link, to the agenda
inbox's +votes address, with the subject "Useful: {watchlist} / {term} [{alert
id}]" or "Noise: ...". Nothing loads when an alert is opened and nothing is
counted on a server.
"""

from __future__ import annotations

# The agenda inbox, which the agenda-email adapter reads; "+votes" keeps votes
# apart from agendas.
ADDRESS = "haystackneedle.agendas+votes@fastmail.com"
KINDS = ("Useful", "Noise")


def subject(vote: str, watchlist: str, term: str, alert_id: str) -> str:
    return f"{vote}: {watchlist} / {term} [{alert_id}]"
