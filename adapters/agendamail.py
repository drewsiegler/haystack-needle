"""Agenda emails: bodies whose agenda sites turn bots away, read from the agendas they email.

BoardDocs, Diligent Community and PrimeGov disallow every bot but search engines
in robots.txt, Monte Sereno's CivicWeb does too, and Los Gatos's Municode portal
refuses scripts. Their bodies email agendas to anyone who asks (Gov. Code
54954.1), so a mailbox used only for that is on their lists (docs/sources.md),
and each run reads it.

- The mailbox is at Fastmail and read over JMAP, its API, with a read-only Email
  token kept in the FASTMAIL_API_TOKEN secret. One request lists the last
  LOOKBACK_DAYS of mail with each message's sender and subject; each new message
  from a sender being read is then downloaded whole and parsed here. Mail in Junk
  or Trash is skipped.
- SENDERS tells the body by the sender's address or domain, or the Reply-To's
  when the sender is a mailing service many towns share. The subject names
  the committee when it can ("October 22, 2026, City Schools Liaison Committee
  Meeting"). Mail from a sender not on the list, or a shelved one, is left
  unread, so adding a sender later picks up its recent emails. Replies to the
  inbox's written requests and lists' sign-up messages are left unread too.
- Each email becomes a meeting with one item: the subject as its title, the
  email's text, and any attached PDF, which the matcher reads like any agenda.
  The meeting's date is the first date in the subject, else the first in the
  text from the day the email came on, else the day it came. An email that lists
  several meetings ("Monday, October 19, 2026, City Council Regular Meeting at
  5:30 pm ...") becomes one meeting for each, from a sender whose line says so.
- A sender whose emails only say an agenda is up (Palo Alto and Los Gatos, so
  far) gets a line in the morning digest for each meeting, to check by hand.
- Links in an email are never followed: most point to sites whose robots.txt
  says no, or through a mailing service's click tracking. The alert links to the
  first one on a host the sender's line lists, never an unsubscribe link, else to
  the body's agenda page. Links are taken out of the text that's matched.
- So far (Oct 15, 2026) Palo Alto and Los Gatos send only notices that agendas
  are up, with no item text and no direct link (docs/sources.md). The Open Space
  Authority attaches each agenda as a PDF with a text layer (Oct 16).
- Votes from alerts' Useful and Noise links come to the same inbox, at its +votes
  address (needle/votes.py). A message to that address, or with a vote's subject,
  is never read as an agenda: collected_votes hands the run its subject, date and
  a hash of the sender keyed with a secret derived from the token, and the cache
  keeps only that it was seen, so no vote is counted twice. No address is kept.
- The run's cache keeps what each message gave (not its text), so a message is
  downloaded once. Later runs rebuild its meeting from the cache, and the
  matcher's saved snippets stand in for its text. If the run that downloaded it
  ran out of time before matching it, item_text downloads it again.
"""

from __future__ import annotations

import email
import hashlib
import hmac
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email import policy
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx

from needle import votes
from needle.fetch import USER_AGENT
from needle.models import Attachment, Body, Item, Meeting, Source

DAYS_BACK = 2
DAYS_AHEAD = 60
LOOKBACK_DAYS = 45  # how far back each run lists the inbox; older cache entries go
LIMIT = 500  # messages listed per run
TEXT_TTL = None  # an email doesn't change: read it once
PACIFIC = ZoneInfo("America/Los_Angeles")
SESSION_URL = "https://api.fastmail.com/jmap/session"
MAIL = "urn:ietf:params:jmap:mail"
USING = ["urn:ietf:params:jmap:core", MAIL]


@dataclass(frozen=True)
class Sender:
    match: str  # an address ("listserv@civicplus.com") or a whole domain ("pausd.org")
    client: str
    name: str  # what organizers call it
    body: str  # the body, when the subject doesn't name one
    page: str  # where people find the agendas, for health notices
    hosts: tuple[str, ...]  # hosts an alert may link to from the email; never fetched
    read: bool = True  # False: shelved, its emails wait unread in the inbox
    # Its emails only say an agenda is up: each goes in the digest's list to check by hand.
    notice: bool = False
    lists: bool = False  # one email lists several meetings, each its own meeting here


# Who sends agendas, one line each. read=False shelves a line: its emails arrive
# and wait. The Palo Alto, Los Gatos, Open Space Authority and Palo Alto Unified
# lines are confirmed by their first emails; the others use each body's own
# domain until its first email confirms the sender.
# Mail from a domain not listed is named in the run's log and left unread.
SENDERS = [
    Sender("paloalto.gov", "paloalto", "Palo Alto", "City Council",
           "https://cityofpaloalto.primegov.com/public/portal",
           ("cityofpaloalto.primegov.com", "www.paloalto.gov"), notice=True, lists=True),
    # Its clerk attaches each agenda; the only link is the signature's, to the
    # home page, so alerts link the agendas portal instead.
    Sender("openspaceauthority.org", "openspaceauthority", "Open Space Authority", "Board of Directors",
           "https://openspaceauthority.primegov.com/public/portal", ("openspaceauthority.primegov.com",)),
    # Los Gatos's Notify Me lists send from noreply@civicplus.com, which every
    # CivicPlus town shares, with the town's Reply-To; the subject names the body.
    Sender("losgatosca.gov", "losgatos", "Los Gatos", "Town Council",
           "https://losgatos-ca.municodemeetings.com/",
           ("losgatos-ca.municodemeetings.com", "meetings.municode.com",
            "mccmeetings.blob.core.usgovcloudapi.net"), notice=True),
    Sender("cityofmontesereno.org", "montesereno", "Monte Sereno", "City Council",
           "https://montesereno.civicweb.net/portal/",
           ("montesereno.civicweb.net", "www.cityofmontesereno.org")),
    Sender("sccoe.org", "sccoe", "Santa Clara County", "Board of Education",
           "https://www.sccoe.org/", ("go.boarddocs.com", "www.sccoe.org")),
    # School boards, read since Oct 27, 2026 (docs/sources.md, "School boards").
    # BoardDocs and Diligent Community say no to bots, so an email that only links
    # the agenda there goes in the digest's list to check by hand.
    Sender("pausd.org", "pausd", "Palo Alto Unified", "Board of Education",
           "https://go.boarddocs.com/ca/pausd/Board.nsf/Public", ("go.boarddocs.com",), notice=True),
    Sender("eesd.org", "eesd", "Evergreen SD", "Board of Trustees",
           "https://go.boarddocs.com/ca/eesd/Board.nsf/Public", ("go.boarddocs.com",), notice=True),
    Sender("wvm.edu", "wvm", "West Valley-Mission CCD", "Board of Trustees",
           "https://go.boarddocs.com/ca/wvm/Board.nsf/Public", ("go.boarddocs.com",), notice=True),
    Sender("sjeccd.edu", "sjeccd", "San José-Evergreen CCD", "Board of Trustees",
           "https://go.boarddocs.com/ca/sjeccd/Board.nsf/Public", ("go.boarddocs.com",), notice=True),
    Sender("gavilan.edu", "gavilan", "Gavilan CCD", "Board of Trustees",
           "https://go.boarddocs.com/ca/gjccd/Board.nsf/Public", ("go.boarddocs.com",), notice=True),
    Sender("esuhsd.org", "esuhsd", "East Side Union HSD", "Board of Trustees",
           "https://esuhsd.community.highbond.com/Portal/", ("esuhsd.community.highbond.com",), notice=True),
    Sender("cuhsd.org", "cuhsd", "Campbell Union HSD", "Board of Trustees",
           "https://cuhsd.community.highbond.com/Portal/", ("cuhsd.community.highbond.com",), notice=True),
]

SOURCES = {s.client: Source("agendamail", s.client, s.name) for s in SENDERS if s.read}

_MONTH = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DATE = re.compile(r"\b(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?"
                   r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
                   r"(\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)(\d{4})\b", re.I)  # "October 21,2026" too
_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
# A run of capitalized words ending in what a body is called.
_BODY = re.compile(r"(?:[A-Z][\w'&.-]*\s+(?:(?:and|of|on|for|the|&)\s+)*)*?"
                   r"(?:Committee|Commission|Council|Authority|Board(?:\s+of\s+(?:Education|Trustees|Directors))?)\b")
_LEAD = {"agenda", "meeting", "notice", "regular", "special", "adjourned", "revised", "amended",
         "updated", "posted", "draft", "final", "the", "of", "for", "and", "on"}
# Mail about getting on a list, not agendas: replies to the inbox's own written
# requests ("RE: Request for Los Gatos Town Council agendas by email (Gov. Code §
# 54954.1)") and lists' sign-up messages ("Confirm your email address").
_NOT_AGENDA = re.compile(r"(?i)agendas by email|54954\.1|confirm your email|confirmation needed"
                         r"|you have been subscribed|information on your subscriptions")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_HREF = re.compile(r"""href=["'](https?://[^"']+)""", re.I)
# Gmail's plain text puts each link's target, and phone links, in angle brackets.
_BRACKETED = re.compile(r"<(?:https?://|mailto:|\()[^>]*>")
_SCRIPT = re.compile(r"(?is)<(style|script)\b.*?</\1>")
_BREAK = re.compile(r"(?i)<(?:br|/p|/div|/li|/tr|/h\d)\b[^>]*>")
_TAGS = re.compile(r"<[^>]*>")


class InboxError(httpx.HTTPError):
    """The inbox couldn't be read: no token, or JMAP answered with an error."""


class Inbox:
    """The agenda inbox, read over JMAP (RFC 8620 and 8621) with a read-only token."""

    def __init__(self, token: str, client: httpx.Client | None = None, interval: float = 1.0):
        self.client = client or httpx.Client(timeout=60)
        self.client.headers.update({"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
        self.interval, self.last = interval, 0.0
        # Hashes voters' addresses: secret, so a guessed address can't be checked against the state.
        self._voter_key = hmac.new(token.encode(), b"haystack-needle votes", hashlib.sha256).digest()
        session = self._get(SESSION_URL).json()
        self.api, self.download_url = session["apiUrl"], session["downloadUrl"]
        self.account = session["primaryAccounts"][MAIL]

    def _wait(self) -> None:
        pause = self.last + self.interval - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        self.last = time.monotonic()

    def _get(self, url: str) -> httpx.Response:
        self._wait()
        response = self.client.get(url)
        response.raise_for_status()
        return response

    def call(self, *calls: list) -> dict[str, dict]:
        """Makes the method calls in one request; returns each answer by its tag."""
        self._wait()
        response = self.client.post(self.api, json={"using": USING, "methodCalls": list(calls)})
        response.raise_for_status()
        answers = {}
        for name, args, tag in response.json()["methodResponses"]:
            if name == "error":
                raise InboxError(f"JMAP answered {args.get('type', 'with an error')}")
            answers[tag] = args
        return answers

    def recent(self, since: datetime) -> list[dict]:
        """Messages since then, newest first, with sender and subject; not Junk or Trash."""
        account = self.account
        answers = self.call(
            ["Mailbox/get", {"accountId": account, "properties": ["role"]}, "boxes"],
            ["Email/query", {"accountId": account,
                             "filter": {"after": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                             "sort": [{"property": "receivedAt", "isAscending": False}], "limit": LIMIT}, "query"],
            ["Email/get", {"accountId": account,
                           "#ids": {"resultOf": "query", "name": "Email/query", "path": "/ids"},
                           "properties": ["blobId", "from", "to", "replyTo", "subject", "receivedAt",
                                          "mailboxIds"]}, "emails"])
        skip = {box["id"] for box in answers["boxes"]["list"] if box.get("role") in ("junk", "trash")}
        return [e for e in answers["emails"]["list"] if not skip & set(e.get("mailboxIds") or {})]

    def voter(self, address: str) -> str:
        return hmac.new(self._voter_key, address.strip().lower().encode(), hashlib.sha256).hexdigest()

    def raw(self, blob: str) -> bytes:
        """The whole message, as sent."""
        url = self.download_url
        for name, value in (("accountId", self.account), ("blobId", blob), ("name", "message.eml"),
                            ("type", "message/rfc822")):
            url = url.replace("{" + name + "}", quote(value, safe=""))
        return self._get(url).content


def connect() -> Inbox:
    token = os.environ.get("FASTMAIL_API_TOKEN")
    if not token:
        raise InboxError("its token, FASTMAIL_API_TOKEN, isn't set")
    return Inbox(token)


@dataclass
class Mail:
    text: str
    links: list[str]
    pdfs: list[tuple[str, bytes]]  # (file name, file)


def _content(part) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError):  # an unknown or wrong charset
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def read(raw: bytes) -> Mail:
    """The email's text (plain if it has it, else its HTML as text), links and PDFs."""
    message = email.message_from_bytes(raw, policy=policy.default)
    part = message.get_body(("plain", "html"))
    text = _content(part).replace("\r\n", "\n") if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        links = [html.unescape(u) for u in _HREF.findall(text)]
        text = html.unescape(_TAGS.sub("", _BREAK.sub("\n", _SCRIPT.sub("", text))))
    else:
        links = [u.rstrip(".,;:") for u in _URL.findall(text)]
    # Links aren't agenda text, and an unsubscribe link can carry the inbox's address.
    text = _URL.sub("", _BRACKETED.sub("", text))
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\xa0]+\n", "\n", text)).strip()
    pdfs = [(p.get_filename() or "Agenda.pdf", p.get_payload(decode=True) or b"")
            for p in message.walk()
            if not p.is_multipart() and (p.get_content_type() == "application/pdf"
                                         or (p.get_filename() or "").lower().endswith(".pdf"))]
    return Mail(text, links, pdfs)


def agenda_link(links: list[str], sender: Sender) -> str | None:
    """The first link on a host the sender's line lists that isn't an unsubscribe
    link or one carrying an address (the inbox's, in a list's own links)."""
    return next((u for u in links if urlsplit(u).hostname in sender.hosts
                 and not re.search(r"(?i)unsubscribe|@|%40", u)), None)


def sender_for(address: str) -> Sender | None:
    address = address.lower()
    domain = address.rpartition("@")[2]
    return (next((s for s in SENDERS if s.match == address), None)
            or next((s for s in SENDERS if s.match == domain), None))


def dates_in(text: str) -> list[date]:
    found = []
    for m in _DATE.finditer(text):
        found.append((m.start(), int(m[3]), _MONTH.index(m[1][:3].lower()) + 1, int(m[2])))
    for m in _NUMERIC.finditer(text):
        found.append((m.start(), int(m[3]), int(m[1]), int(m[2])))
    days = []
    for _, year, month, day in sorted(found):
        try:
            days.append(date(year, month, day))
        except ValueError:
            pass
    return days


def meeting_date(subject: str, text: str, sent: date) -> date | None:
    """The first date in the subject, else the first in the text from the day it
    came on. Dates more than a year out are someone's typo."""
    for d in dates_in(subject):
        if sent - timedelta(days=30) <= d <= sent + timedelta(days=365):
            return d
    for d in dates_in(text):
        if sent <= d <= sent + timedelta(days=365):
            return d
    return None


def body_name(subject: str, default: str) -> str:
    """The body the subject names ("City Schools Liaison Committee"), or the sender's."""
    m = _BODY.search(_DATE.sub(" ", subject))
    if not m:
        return default
    words = m[0].split()
    while words and words[0].lower() in _LEAD:
        words.pop(0)
    return " ".join(words)[:80] or default


# A meeting in a list of them, after its date: "Monday, October 19, 2026, City
# Council Regular Meeting at 5:30 pm Item 6: Supplemental Report Added Agenda Packet".
_ROW = re.compile(r",?\s+(" + _BODY.pattern + r")\s+(?:(?:Regular|Special|Joint|Adjourned)\s+)?Meeting"
                  r"(?:\s+at\s+\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)?")
_CANCELED = re.compile(r"\s*-?\s*Cancel", re.I)
ROW_CHARS = 300  # a listed meeting's text; what follows the last one is the newsletter's news


def listed(text: str) -> list[tuple[date, str, str, str]]:
    """The meetings an email lists, as (date, body, title, its text); canceled ones left out."""
    heads = [(m, row) for m in _DATE.finditer(text) if (row := _ROW.match(text, m.end()))]
    found = []
    for n, (m, row) in enumerate(heads):
        days = dates_in(m[0])
        if not days or _CANCELED.match(text, row.end()):
            continue
        end = min(heads[n + 1][0].start() if n + 1 < len(heads) else len(text), m.start() + ROW_CHARS)
        found.append((days[0], row[1], " ".join(row[0].lstrip(",").split()), text[m.start():end].strip()))
    return found


def _day(d: date) -> str:
    return f"{d:%a %b} {d.day}"  # as alerts write it: "Mon Oct 19"


def rows_for(mail: Mail, subject: str, sender: Sender, sent: date) -> list[dict]:
    """The meetings an email is about, each with its body, date, title and text, and
    the line for the digest's list to check by hand when the agenda isn't in it."""
    notice = sender.notice and not mail.pdfs
    if sender.lists and not mail.pdfs:
        rows = [{"body": body, "date": day.isoformat(), "title": title, "text": text,
                 "notice": f"{_day(day)}: {title}" if notice else ""}
                for day, body, title, text in listed(mail.text)]
        if rows:
            return rows
    day, title = meeting_date(subject, mail.text, sent), subject or "Agenda email"
    line = f"{_day(day)}: {title}" if day else f"{title} (sent {_day(sent)}; it gives no meeting date)"
    return [{"body": body_name(subject, sender.body), "date": (day or sent).isoformat(), "title": title,
             "text": mail.text, "notice": line if notice else ""}]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _meetings(ident: str, entry: dict, texts: list[str] | None = None, pdfs: list | None = None) -> list[Meeting]:
    received = datetime.fromisoformat(entry["received"])
    files = pdfs if pdfs is not None else [(name, None) for name in entry["files"]]
    rows = entry["meetings"]
    meetings = []
    for n, row in enumerate(rows):
        mid = ident if len(rows) == 1 else f"{ident}#{n + 1}"
        # An emailed PDF has no link for people; the alert links the email's agenda link instead.
        attachments = [Attachment(f"{ident}/{k}", name, "", received, data=data)
                       for k, (name, data) in enumerate(files, 1)] if n == 0 else []
        item = Item(mid, row["title"], texts[n] if texts else "", attachments=attachments, modified=received)
        meetings.append(Meeting(Body(SOURCES[entry["client"]], _slug(row["body"]), row["body"]), mid,
                                date.fromisoformat(row["date"]), url=entry["url"] or calendar_url(entry["client"]),
                                items=[item], modified=received, notice=row["notice"]))
    return meetings


# Set by collect for item_text: the run's connection, and the meetings it rebuilt from the cache;
# and for the run, the votes it read.
_inbox: Inbox | None = None
_rebuilt: dict[str, tuple[dict, int]] = {}
_votes: list[dict] = []


def is_vote(message: dict) -> bool:
    to = {(r.get("email") or "").lower() for r in message.get("to") or []}
    return votes.ADDRESS in to or votes.parse(message.get("subject")) is not None


def collected_votes() -> list[dict]:
    """The votes the last collect read, for the run to count: {"subject", "voter", "date"}."""
    return _votes


def collect(fetcher=None, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """New emails from senders being read, and those read before whose meeting
    falls from days_back days ago to days_ahead days ahead.

    fetcher and with_text are unused: the inbox is read over JMAP, not the web,
    and an email's text comes with it. cache holds what each message gave.

    Returns the meetings and, for each source that failed, what went wrong.
    """
    global _inbox, _rebuilt, _votes
    _inbox, _rebuilt, _votes = None, {}, []
    today = today or datetime.now(PACIFIC).date()
    cache = {} if cache is None else cache
    for ident in [i for i, entry in cache.items() if "meetings" not in entry]:
        del cache[ident]  # kept by the first version (Oct 15): read it again
    since = datetime.combine(today - timedelta(days=LOOKBACK_DAYS), datetime.min.time(), PACIFIC)
    try:
        _inbox = inbox = connect()
        listed_mail = inbox.recent(since)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        reason = str(error) if isinstance(error, InboxError) else type(error).__name__
        print(f"Couldn't read the agenda inbox: {reason}", file=sys.stderr)
        return [], {c: f"Couldn't read the agenda inbox: {reason}." for c in clients}

    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    unknown, fresh = set(), set()
    for message in listed_mail:
        ident = message["id"]
        if ident in cache:
            continue
        if is_vote(message):
            received = datetime.fromisoformat(message["receivedAt"].replace("Z", "+00:00"))
            voter = ((message.get("from") or [{}])[0].get("email") or "").strip()
            _votes.append({"subject": message.get("subject") or "", "voter": inbox.voter(voter) if voter else None,
                           "date": received.astimezone(PACIFIC).date().isoformat()})
            cache[ident] = {"received": received.isoformat(), "client": "votes", "meetings": []}
            continue
        address = ((message.get("from") or [{}])[0].get("email") or "")
        replies = [r.get("email") or "" for r in message.get("replyTo") or []]
        sender = next(filter(None, map(sender_for, [address, *replies])), None)
        if sender is None:
            unknown.add(address.rpartition("@")[2] or "no sender")
            continue
        if not sender.read or sender.client not in clients:
            continue  # shelved: left unread until its line says read=True
        subject = " ".join((message.get("subject") or "").split())[:200]
        if _NOT_AGENDA.search(subject):
            continue
        try:
            mail = read(inbox.raw(message["blobId"]))
        except httpx.HTTPError as error:
            failures[sender.client] = f"Couldn't download an agenda email: {type(error).__name__}."
            continue  # not cached, so it's tried again next run
        received = datetime.fromisoformat(message["receivedAt"].replace("Z", "+00:00"))
        rows = rows_for(mail, subject, sender, received.astimezone(PACIFIC).date())
        cache[ident] = {
            "received": received.isoformat(), "blob": message["blobId"], "client": sender.client,
            "subject": subject, "url": agenda_link(mail.links, sender), "files": [name for name, _ in mail.pdfs],
            "meetings": [{k: v for k, v in row.items() if k != "text"} for row in rows]}
        meetings += _meetings(ident, cache[ident], [row["text"] for row in rows], mail.pdfs)
        fresh.add(ident)
    if unknown:
        print(f"Left agenda emails unread from senders not in SENDERS: {', '.join(sorted(unknown))}. "
              "If one is a body's agendas, add it in adapters/agendamail.py.", file=sys.stderr)

    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    for ident, entry in list(cache.items()):
        if datetime.fromisoformat(entry["received"]) < since:
            del cache[ident]
        elif ident not in fresh and entry["client"] in clients and entry["client"] in SOURCES:
            for n, meeting in enumerate(_meetings(ident, entry)):
                if start <= meeting.date <= end:
                    meetings.append(meeting)
                    _rebuilt[meeting.id] = (entry, n)
    return meetings, failures


def item_text(fetcher, client: str, item: Item) -> str:
    """Nothing for an email read this run: its text came with it. For a meeting
    rebuilt from the cache whose text was never matched (the run that read it ran
    out of time), the email again, with its PDFs."""
    ref = _rebuilt.get(item.id)
    if item.text or ref is None or _inbox is None:
        return ""
    entry, n = ref
    mail = read(_inbox.raw(entry["blob"]))
    sender = next(s for s in SENDERS if s.client == entry["client"])
    sent = datetime.fromisoformat(entry["received"]).astimezone(PACIFIC).date()
    rows = rows_for(mail, entry["subject"], sender, sent)
    for attachment, (_, data) in zip(item.attachments, mail.pdfs):
        attachment.data = data
    return rows[n]["text"] if n < len(rows) else ""


def calendar_url(client: str) -> str:
    return next(s.page for s in SENDERS if s.client == client)
