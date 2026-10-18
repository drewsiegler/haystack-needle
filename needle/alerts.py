"""Turn matches into alert emails: one per item for high-priority watchlists, and
the morning digest for the rest.

An alert is a plain dict of strings, so a digest entry can wait in the state
file until morning. Agenda text is untrusted: the HTML templates escape it,
the text templates flatten it to one line, and only http(s) links get through.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from needle import votes
from needle.matcher import DEEP_PAGE, snippet
from needle.models import Match, Meeting

TEMPLATES = Path(__file__).parent / "templates"
TITLE_CHARS = 300  # agenda titles can run to a paragraph
WHERE = {"title": "item title", "item_text": "item text"}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_EMAIL = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_COMMENT = re.compile(r"comment", re.IGNORECASE)


def oneline(text: str | None, limit: int | None = None) -> str:
    """Agenda text on one line, without control characters, cut to limit."""
    text = " ".join(_CONTROL.sub(" ", text or "").split())
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def safe_url(url: str | None) -> str | None:
    url = (url or "").strip()
    return url if re.match(r"https?://[^\s<>\"']+$", url) else None


def day_text(d: date) -> str:
    return f"{d:%a %b} {d.day}"  # "Wed Oct 21"


_env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(enabled_extensions=("html.j2",), default=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_env.filters["oneline"] = oneline


def render(name: str, **context) -> str:
    return _env.get_template(name).render(**context)


def comment_info(pages: list[str]) -> tuple[str, str] | None:
    """Where and by when to send public comment, as the agenda says it.

    Finds the first email address with the word "comment" in the 300
    characters before it, and returns (the sentence-sized snippet around them,
    the address). None if the agenda doesn't say.
    """
    text = " ".join(" ".join(page.split()) for page in pages)
    for found in _EMAIL.finditer(text):
        before = text[max(0, found.start() - 300):found.start()]
        hits = list(_COMMENT.finditer(before))
        if hits:
            start = found.start() - len(before) + hits[-1].start()
            return snippet(text, start, found.end(), chars=120), found.group().rstrip(".")
    return None


def vote_links(alert_id: str, watchlist: str, term: str) -> dict:
    """mailto links that record a vote as an email to the agenda inbox's +votes address.

    Nothing loads when an alert is opened and nothing is counted on a server:
    a vote is an email someone chose to send, with the alert id, watchlist
    and term in the subject, which the run reads with the agendas and counts
    (needle/votes.py).
    """
    return {vote.lower(): f"mailto:{votes.ADDRESS}?subject={quote(votes.subject(vote, watchlist, term, alert_id), safe='')}"
            for vote in votes.KINDS}


def where(match: Match) -> str:
    if match.location != "attachment":
        return WHERE[match.location]
    name = oneline(match.attachment.name if match.attachment else "", 80) or "attachment"
    deep = " (deep in the packet)" if match.page and match.page > DEEP_PAGE else ""
    return f"{name}, p. {match.page}{deep}"


def build(meeting: Meeting, matches: list[Match], alert_id: str, priority: str,
          label: str = "", label_detail: str = "", history: list[str] | None = None,
          comment: tuple[str, str] | None = None) -> dict:
    """One alert for one item at one meeting, from that item's matches (best first)."""
    item = matches[0].item
    source = meeting.body.source
    body = oneline(f"{source.name} {meeting.body.name}", 120)
    watchlists = list(dict.fromkeys(m.watchlist for m in matches))
    number = oneline(item.number, 20).rstrip(".")  # Cupertino prints "1."
    links = [("Agenda", safe_url(meeting.agenda_url)), ("Meeting page", safe_url(meeting.url))]
    seen = set()
    for m in matches:
        # Skip an attachment that is the agenda itself (CivicPlus): it's linked already.
        if m.attachment and m.attachment.id not in seen and m.attachment.url != meeting.agenda_url:
            seen.add(m.attachment.id)
            links.append((oneline(m.attachment.name, 80) or "Attachment", safe_url(m.attachment.url)))
    subject = (f"[Haystack Needle: {', '.join(watchlists)}] {body}, {day_text(meeting.date)}, "
               f"{'Item ' + number if number else 'agenda item'}")
    if label == "Updated":
        subject += " (updated)"
    return {
        "id": alert_id,
        "priority": priority,
        "subject": oneline(subject, 200),
        "watchlists": watchlists,
        "label": label,
        "label_detail": oneline(label_detail, 200),
        "body": body,
        "date": meeting.date.isoformat(),
        "day": day_text(meeting.date),
        "time": oneline(meeting.time, 20),
        "number": number,
        "title": oneline(item.title, TITLE_CHARS),
        "matches": [{"term": m.term, "watchlist": m.watchlist, "where": where(m),
                     "snippet": oneline(m.snippet, 400)} for m in matches],
        "links": [{"label": text, "url": url} for text, url in links if url],
        "comment": oneline(comment[0], 400) if comment else "",
        "comment_email": comment[1] if comment else "",
        "history": [oneline(h, 200) for h in history or []],
        "votes": vote_links(alert_id, matches[0].watchlist, matches[0].term),
    }


def alert_email(alert: dict) -> tuple[str, str, str]:
    """(subject, text, html) for one alert sent right away."""
    return (alert["subject"], render("alert.txt.j2", a=alert), render("alert.html.j2", a=alert))


def digest_email(alerts: list[dict], boilerplate: list[str], day: date,
                 posted: list[dict] = (), voted: list[str] = (), review: list[str] = (),
                 skipped: int = 0) -> tuple[str, str, str]:
    """posted: agendas a body emailed only a notice of ({"line", "url"}), to check by hand.
    voted: a line for each vote since the last digest; review: terms with only noise
    votes lately; skipped: vote emails not counted (needle/votes.py)."""
    count, held, check = len(alerts), len(boilerplate), len(posted)
    if count:
        summary = f"{count} item{'s' if count != 1 else ''}"
    elif check:
        summary = f"{check} agenda{'s' if check != 1 else ''} to check by hand"
    elif voted:
        summary = f"{len(voted)} vote{'s' if len(voted) != 1 else ''} counted"
    else:
        summary = f"nothing new, {held} phrase{'s' if held != 1 else ''} held back"
    subject = f"[Haystack Needle] Morning digest, {day_text(day)}: {summary}"
    context = {"alerts": alerts, "boilerplate": [oneline(b, 300) for b in boilerplate],
               "posted": [{"line": oneline(p["line"], 200), "url": safe_url(p["url"])} for p in posted],
               "voted": [oneline(v, 300) for v in voted], "review": [oneline(r, 300) for r in review],
               "skipped": skipped}
    return subject, render("digest.txt.j2", **context), render("digest.html.j2", **context)
