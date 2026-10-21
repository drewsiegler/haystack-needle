"""The "Ask for the records" link on surveillance alerts, for a records-request
tracker such as PRA Tracker (Drew's, which is private).

The link is a mailto to the tracker's inbox. It carries only what the agenda
says in public, a `name: value` line each (the alert's id, the platform, the
place, the body, the meeting date, the item number and title, and the term
that matched), and the tracker fills in the request on its side: which
agency, what to ask for, who signs. Nothing from the tracker comes back here.

- Only the owner's alerts get it, never a subscriber's, and only for an item
  that matched the surveillance watchlist.
- It's made as each email is written and never kept in the alert, since
  alerts waiting for the digest sit in the public state and the link names
  the tracker's address.
- The subject ends with a code, a keyed hash of the alert's id, so the
  tracker can tell the email came from an alert: "Draft: San José City
  Council, Tue Oct 27, item 6.1 [code]". The address and the key are the
  PRA_LINK_TO and PRA_LINK_KEY secrets; the tracker keeps the same key.
  Without both, no alert has the link.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from urllib.parse import quote

WATCHLIST = "surveillance"
TITLE_CHARS = 200
INTRO = ("Send this email as it is, and your records-request tracker drafts a request for this "
         "item on its next run. Don't change the lines below.")


@dataclass(frozen=True)
class Settings:
    to: str  # the tracker's address; never printed or kept in the state
    key: bytes


def settings_from_env(env=os.environ) -> Settings | None:
    to, key = (env.get("PRA_LINK_TO") or "").strip(), (env.get("PRA_LINK_KEY") or "").strip()
    return Settings(to, key.encode()) if "@" in to and key else None


def code(key: bytes, alert_id: str) -> str:
    """PRA Tracker's pra/links.py checks the same code."""
    return hmac.new(key, f"draft|{alert_id}".encode(), hashlib.sha256).hexdigest()[:12]


def link(alert: dict, settings: Settings | None) -> str | None:
    """The mailto link for one alert, or None."""
    if settings is None or WATCHLIST not in alert.get("watchlists", []):
        return None
    term = next((m["term"] for m in alert["matches"] if m["watchlist"] == WATCHLIST), "")
    number = alert.get("number") or ""
    subject = (f"Draft: {alert['body']}, {alert['day']}" + (f", item {number}" if number else "")
               + f" [{code(settings.key, alert['id'])}]")
    title = alert["title"]
    if len(title) > TITLE_CHARS:
        title = title[:TITLE_CHARS].rsplit(" ", 1)[0] + "…"
    fields = {"alert": alert["id"], "source": alert.get("source", ""), "place": alert.get("place", ""),
              "body": alert["body"], "meeting": alert["date"], "item": number, "title": title, "term": term}
    body = "\r\n".join([INTRO, ""] + [f"{k}: {v}" for k, v in fields.items()])  # CRLF, as RFC 6068 asks
    return f"mailto:{settings.to}?subject={quote(subject, safe='')}&body={quote(body, safe='')}"
