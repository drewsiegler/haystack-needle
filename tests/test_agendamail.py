import json
from dataclasses import replace
from datetime import date, datetime, timezone
from email.message import EmailMessage

import httpx
import pytest

from adapters import agendamail
from conftest import FIXTURES, load
from needle.matcher import compile_terms, match_meeting
from needle.models import Source
from needle.run import Reader
from pdfs import make_pdf

EML = (FIXTURES / "agendamail" / "pausd-liaison.eml").read_bytes()
LOSGATOS = (FIXTURES / "agendamail" / "losgatos-council.eml").read_bytes()
PALOALTO = (FIXTURES / "agendamail" / "paloalto-weekly.eml").read_bytes()
SESSION = "https://api.fastmail.com/jmap/session"
API = "https://api.fastmail.com/jmap/api/"
DOWNLOAD = "https://www.fastmailusercontent.com/jmap/download/u1a2b3c4d/"
AGENDA = "https://go.boarddocs.com/ca/pausd/Board.nsf/goto?open&id=DYQ34D050895"
TODAY = date(2026, 10, 15)


class FakeJmap:
    """Answers the session, the listing (honoring its "after" filter) and message
    downloads, and records every request."""

    def __init__(self, raw: bytes = EML, listing: dict | None = None, raws: dict | None = None):
        self.raws, self.listing = raws or {"G-pausd": raw}, listing or load("agendamail/recent.json")
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        url = str(request.url)
        if url == SESSION:
            return httpx.Response(200, json=load("agendamail/session.json"))
        if url == API:
            calls = json.loads(request.content)["methodCalls"]
            after = calls[1][1]["filter"]["after"]
            answer = json.loads(json.dumps(self.listing))
            if answer["methodResponses"][-1][0] == "Email/get":
                emails = answer["methodResponses"][-1][1]
                emails["list"] = [e for e in emails["list"] if e["receivedAt"] >= after]
            return httpx.Response(200, json=answer)
        if url.startswith(DOWNLOAD):
            blob, rest = url.removeprefix(DOWNLOAD).split("/", 1)
            assert rest == "message.eml?type=message%2Frfc822"
            return httpx.Response(200, content=self.raws[blob])
        raise AssertionError(f"no fixture for {url}")

    def connect(self):
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return agendamail.Inbox("test-token", client, interval=0)

    @property
    def downloads(self) -> int:
        return sum(str(r.url).startswith(DOWNLOAD) for r in self.requests)


def listing(*emails):
    return {"methodResponses": [
        ["Mailbox/get", {"list": [{"id": "P-inbox", "role": "inbox"}]}, "boxes"],
        ["Email/query", {"ids": [e["id"] for e in emails]}, "query"],
        ["Email/get", {"list": list(emails)}, "emails"]]}


def message(ident, sender, subject, received, reply_to=None):
    return {"id": ident, "blobId": f"G-{ident}", "receivedAt": received, "subject": subject,
            "from": [{"name": None, "email": sender}],
            "replyTo": [{"name": None, "email": reply_to}] if reply_to else None,
            "mailboxIds": {"P-inbox": True}}


@pytest.fixture
def jmap(monkeypatch):
    fake = FakeJmap()
    monkeypatch.setattr(agendamail, "connect", fake.connect)
    return fake


@pytest.fixture
def pausd_on(monkeypatch):
    """Palo Alto Unified as it would be once school boards are read: one flag on its line."""
    monkeypatch.setattr(agendamail, "SENDERS", [replace(s, read=True) if s.client == "pausd" else s
                                                for s in agendamail.SENDERS])
    monkeypatch.setitem(agendamail.SOURCES, "pausd", Source("agendamail", "pausd", "Palo Alto Unified"))


def collect(cache, today=TODAY, **window):
    return agendamail.collect(clients=tuple(agendamail.SOURCES), today=today, cache=cache, **window)


def test_shelved_school_emails_stay_unread(jmap):
    cache = {}
    assert "pausd" not in agendamail.SOURCES
    assert collect(cache) == ([], {})
    assert cache == {} and jmap.downloads == 0


def test_reads_an_agenda_email(jmap, pausd_on, capsys):
    cache = {}
    meetings, failures = collect(cache)
    assert failures == {}
    [meeting] = meetings
    assert meeting.body.source.name == "Palo Alto Unified"
    assert meeting.body.name == "City Schools Liaison Committee"
    assert meeting.date == date(2026, 10, 22)
    assert meeting.url == AGENDA  # for people; never fetched
    assert meeting.agenda_url is None
    [item] = meeting.items
    assert item.title == "October 22, 2026, City Schools Liaison Committee Meeting"
    assert "7:00-8:30 pm at the District Office Aspen\nConference Room" in item.text
    assert "<https" not in item.text and item.attachments == []
    # One download: the junk-folder copy and the unknown sender are left alone,
    # and the log names only the unknown sender's domain.
    assert jmap.downloads == 1
    log = capsys.readouterr().err
    assert "example.org" in log and "news@" not in log
    assert cache["M-pausd"]["meetings"][0]["date"] == "2026-10-22" and "text" not in str(cache)
    # It only links the agenda, so it's also listed in the digest to check by hand.
    assert meeting.notice == "Thu Oct 22: October 22, 2026, City Schools Liaison Committee Meeting"


def test_reads_each_email_once(jmap, pausd_on):
    cache = {}
    collect(cache)
    [meeting], _ = collect(cache)
    assert jmap.downloads == 1
    assert meeting.items[0].text == ""  # the matcher's saved snippets stand in for it
    assert meeting.body.name == "City Schools Liaison Committee"
    # If the first run never matched it, the text is read again.
    assert "Aspen" in agendamail.item_text(None, "pausd", meeting.items[0])
    assert jmap.downloads == 2


def test_returns_read_emails_only_in_the_window(jmap, pausd_on):
    cache = {}
    collect(cache)
    assert collect(cache, today=date(2026, 10, 18), days_back=0, days_ahead=3)[0] == []
    assert len(collect(cache, today=date(2026, 10, 19), days_back=0, days_ahead=3)[0]) == 1
    collect(cache, today=date(2026, 12, 15))  # past the lookback: dropped
    assert cache == {}


def test_reads_an_attached_agenda_pdf(monkeypatch, pausd_on):
    message = EmailMessage()
    message["From"], message["Subject"] = "sender@pausd.org", "Board of Education Agenda"
    message.set_content("The agenda for Tuesday, October 20, 2026 is attached.")
    pdf = make_pdf(["Call to order", "Approve the license plate reader contract"])
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename="Agenda 10-20.pdf")
    listing = load("agendamail/recent.json")
    listing["methodResponses"][2][1]["list"][0]["subject"] = message["Subject"]
    fake = FakeJmap(raw=message.as_bytes(), listing=listing)
    monkeypatch.setattr(agendamail, "connect", fake.connect)

    [meeting], _ = collect({})
    assert (meeting.body.name, meeting.date) == ("Board of Education", date(2026, 10, 20))
    [attachment] = meeting.items[0].attachments
    assert (attachment.name, attachment.data, attachment.url) == ("Agenda 10-20.pdf", pdf, "")
    assert meeting.notice == ""  # the agenda came with it: nothing to check by hand

    class NoFetcher:
        def get(self, url, params=None):
            raise AssertionError("an emailed PDF is never downloaded")

    terms = compile_terms({"surveillance": {"terms": ["license plate reader"]}})
    state = {"attachments": {}}
    reader = Reader(NoFetcher(), state, terms, datetime(2026, 10, 15, 22, tzinfo=timezone.utc), 0)
    matches = match_meeting(meeting, terms, lambda a: reader.pages(meeting.body.source, a))
    assert [(m.location, m.page) for m in matches] == [("attachment", 2)]


def test_reads_a_los_gatos_notice_by_its_reply_to(monkeypatch):
    # From the address every CivicPlus town shares, so the town's Reply-To tells it.
    notice = message("lg", "noreply@civicplus.com", "Town Council Meeting Agendas", "2026-10-16T02:35:09Z",
                     reply_to="manager@losgatosca.gov")
    fake = FakeJmap(listing=listing(notice), raws={"G-lg": LOSGATOS})
    monkeypatch.setattr(agendamail, "connect", fake.connect)
    [meeting], failures = collect({})
    assert failures == {}
    assert (meeting.body.source.name, meeting.body.name) == ("Los Gatos", "Town Council")
    assert meeting.date == date(2026, 10, 15)  # no date in it: the day it came
    assert meeting.url == "https://losgatos-ca.municodemeetings.com/"  # not the unsubscribe link
    text = meeting.items[0].text
    assert "Town Council Agendas have been posted on the website" in text
    assert "http" not in text and "example.invalid" not in text  # links, and the inbox's address, out
    assert meeting.notice == "Town Council Meeting Agendas (sent Thu Oct 15; it gives no meeting date)"


def test_reads_palo_altos_weekly_notice(monkeypatch):
    notice = message("pa", "city.clerk@paloalto.gov", "Notice for City Council Meeting Agendas: October 15, 2026",
                     "2026-10-16T03:00:18Z")
    fake = FakeJmap(listing=listing(notice), raws={"G-pa": PALOALTO})
    monkeypatch.setattr(agendamail, "connect", fake.connect)
    cache = {}
    meetings, _ = collect(cache)
    # One meeting for each it lists; the canceled Economic Development Committee is left out.
    assert [(m.date, m.body.name, m.notice) for m in meetings] == [
        (date(2026, 10, 19), "City Council", "Mon Oct 19: City Council Regular Meeting at 5:30 pm"),
        (date(2026, 10, 26), "City Council", "Mon Oct 26: City Council Special Meeting at 5:30 pm"),
        (date(2026, 10, 20), "Finance Committee", "Tue Oct 20: Finance Committee Regular Meeting at 4:00 pm"),
        (date(2026, 10, 22), "City School Liaison Committee",
         "Thu Oct 22: City School Liaison Committee Regular Meeting at 7:00 pm")]
    first = meetings[0]
    assert first.id == "pa#1" and first.body.source.name == "Palo Alto"
    assert first.items[0].text == ("Monday, October 19, 2026, City Council Regular Meeting at 5:30 pm "
                                   "Item 6: Supplemental Report Added Agenda Packet")
    assert "Voter" not in meetings[-1].items[0].text  # the newsletter's news isn't agenda text
    # Its links all go through Constant Contact's click tracking: the alert links the portal.
    assert first.url == agendamail.calendar_url("paloalto")
    # Rebuilt from the cache on a later run, the same four.
    assert [m.id for m in collect(cache)[0]] == ["pa#1", "pa#2", "pa#3", "pa#4"]


def test_rereads_an_email_kept_by_the_first_version(jmap, pausd_on):
    cache = {"M-pausd": {"received": "2026-10-15T21:00:05+00:00", "blob": "G-pausd", "client": "pausd",
                         "body": "City Schools Liaison Committee", "subject": "...", "date": "2026-10-22",
                         "url": None, "files": []}}
    [meeting], _ = collect(cache)
    assert jmap.downloads == 1 and "meetings" in cache["M-pausd"]
    assert "Aspen" in meeting.items[0].text


def test_never_links_an_unsubscribe_link():
    [losgatos] = [s for s in agendamail.SENDERS if s.client == "losgatos"]
    agenda = "https://meetings.municode.com/adaHtmlDocument/index?cc=LOSGATOS&me=0123abcd"
    assert agendamail.agenda_link([
        "https://meetings.municode.com/list.aspx?mode=Unsubscribe&CID=145",
        "https://meetings.municode.com/prefs?Email=agendas%40example.invalid",
        "https://www.losgatosca.gov/list.aspx?MID=3326",
        agenda], losgatos) == agenda


def test_reports_a_missing_token(monkeypatch):
    monkeypatch.delenv("FASTMAIL_API_TOKEN", raising=False)
    meetings, failures = collect({})
    assert meetings == [] and set(failures) == set(agendamail.SOURCES)
    assert "FASTMAIL_API_TOKEN" in failures["paloalto"]


def test_reports_a_jmap_error(monkeypatch):
    fake = FakeJmap(listing={"methodResponses": [["error", {"type": "accountNotFound"}, "boxes"]]})
    monkeypatch.setattr(agendamail, "connect", fake.connect)
    meetings, failures = collect({})
    assert meetings == [] and "accountNotFound" in failures["sccoe"]


def test_reads_an_html_only_email():
    message = EmailMessage()
    message.set_content('<p>Agenda for the <b>Planning &amp; Transportation Commission</b>:'
                        '<br><a href="https://cityofpaloalto.primegov.com/a?x=1&amp;y=2">view</a></p>'
                        "<style>p {color: red}</style>", subtype="html")
    mail = agendamail.read(message.as_bytes())
    assert mail.text == "Agenda for the Planning & Transportation Commission:\nview"
    assert mail.links == ["https://cityofpaloalto.primegov.com/a?x=1&y=2"]


@pytest.mark.parametrize("subject, body, day", [
    ("October 22, 2026, City Schools Liaison Committee Meeting", "City Schools Liaison Committee", date(2026, 10, 22)),
    ("Agenda - Town Council", "Town Council", None),
    ("Special Meeting of the Board of Education, Tues. Oct. 20th, 2026", "Board of Education", date(2026, 10, 20)),
    ("Planning and Transportation Commission Agenda for 10/21/2026", "Planning and Transportation Commission",
     date(2026, 10, 21)),
    ("Your agenda is posted", "City Council", None),
])
def test_tells_the_body_and_date_from_the_subject(subject, body, day):
    assert agendamail.body_name(subject, "City Council") == body
    assert agendamail.meeting_date(subject, "", date(2026, 10, 15)) == day


def test_finds_the_date_in_the_text_from_the_day_it_came():
    text = "Minutes of September 1, 2026 are attached. The next meeting is November 3, 2026."
    assert agendamail.meeting_date("Agenda posted", text, date(2026, 10, 15)) == date(2026, 11, 3)


def test_tells_the_sender_by_address_or_domain():
    assert agendamail.sender_for("City.Clerk@PaloAlto.gov").client == "paloalto"
    assert agendamail.sender_for("clerk@pausd.org").client == "pausd"
    assert agendamail.sender_for("noreply@civicplus.com") is None
    assert agendamail.sender_for("clerk@mail.pausd.org") is None
