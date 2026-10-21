import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

from needle import alerts, records
from needle import run as needle_run
from needle import state as statefile
from needle.models import Attachment, Body, Item, Match, Meeting, Source
from pdfs import make_pdf

CITY = Source("legistar", "sanjose", "San José")
TRACKER = records.Settings("tracker+drafts@example.org", b"a shared test key")


def alert(watchlist="surveillance", term="Flock", number="6.1"):
    meeting = Meeting(Body(CITY, "1", "City Council"), "9", date(2026, 10, 27), "1:30 PM",
                      agenda_url="https://sanjose.example/agenda.pdf")
    item = Item("1", "Approve agreement with Flock Group Inc.\nfor ALPR cameras", number=number)
    match = Match(meeting, item, watchlist, term, "title", 30, "…agreement with Flock Group Inc…")
    return alerts.build(meeting, [match], "3f9a2c1d0b", "high")


def fields(url):
    query = parse_qs(urlsplit(url).query)
    lines = query["body"][0].split("\r\n")
    return query["subject"][0], dict(line.split(": ", 1) for line in lines if ": " in line)


def test_the_link_carries_only_the_agenda_facts():
    url = records.link(alert(), TRACKER)
    assert url.startswith("mailto:tracker+drafts@example.org?subject=")
    subject, body = fields(url)
    assert subject == "Draft: San José City Council, Tue Oct 27, item 6.1 [113c2a6b35a2]"
    assert body == {"alert": "3f9a2c1d0b", "source": "legistar/sanjose", "place": "San José",
                    "body": "San José City Council", "meeting": "2026-10-27", "item": "6.1",
                    "title": "Approve agreement with Flock Group Inc. for ALPR cameras", "term": "Flock"}


def test_the_code_matches_pra_trackers():
    # PRA Tracker's tests/test_links.py checks the same code for the same key and alert.
    assert records.code(b"a shared test key", "3f9a2c1d0b") == "113c2a6b35a2"


def test_only_the_owners_surveillance_alerts_get_it():
    a = alert()
    _, text, html = alerts.alert_email(a, records_to=TRACKER)
    assert "Ask for the records: mailto:tracker+drafts@example.org?subject=Draft" in text
    assert '<a href="mailto:tracker+drafts@example.org?subject=Draft' in html
    _, text, html = alerts.alert_email(a, subscriber=True, records_to=TRACKER)
    assert "Ask for the records" not in text + html and "tracker+drafts" not in text + html
    _, text, _ = alerts.alert_email(alert("tenants", "rent stabilization"), records_to=TRACKER)
    assert "Ask for the records" not in text
    _, text, _ = alerts.alert_email(a)
    assert "Ask for the records" not in text  # no secrets, no link
    assert "records" not in a  # the alert itself never holds the link


def test_settings_need_both_secrets():
    assert records.settings_from_env({"PRA_LINK_TO": "t@example.org", "PRA_LINK_KEY": " k "}) == \
        records.Settings("t@example.org", b"k")
    assert records.settings_from_env({"PRA_LINK_TO": "t@example.org"}) is None
    assert records.settings_from_env({"PRA_LINK_KEY": "k"}) is None


def test_the_digest_has_the_link_and_the_public_state_doesnt(tmp_path):
    town = Source("fake", "town", "Townsville")

    class Adapter:
        SOURCES = {"town": town}

        def collect(self, fetcher, today, with_text, days_back=2, days_ahead=30, cache=None):
            item = Item("1", "Agreement for public safety technology", number="7", matter_id="100",
                        attachments=[Attachment("a1", "Staff Report", "https://town.example/r.pdf")])
            return [Meeting(Body(town, "1", "City Council"), "m1", date(2026, 10, 20), items=[item],
                            agenda_url="https://town.example/agenda.pdf")], {}

        def item_text(self, fetcher, client, item):
            return ""

        def calendar_url(self, client):
            return "https://town.example/calendar"

    class Fetcher:
        files = {"https://town.example/agenda.pdf": make_pdf(["Agenda"]),
                 "https://town.example/r.pdf": make_pdf(["Summary", "12 Flock cameras."])}

        def get(self, url):
            return self.files[url]

    watchlists = tmp_path / "watchlists.yaml"
    watchlists.write_text("surveillance: {priority: normal, terms: [Flock]}\n")  # waits for the digest
    state, outbox = statefile.empty(), needle_run.Outbox(dry_run=True)
    sent = []
    outbox.send = lambda subject, text, html=None, to=None, on_fail=None: sent.append((subject, text))
    now = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)  # 1 a.m. Pacific: before the digest
    needle_run.run(state, Fetcher(), outbox, now, adapters={"fake": Adapter()}, watchlists_path=watchlists,
                   records_to=TRACKER)
    assert state["digest"] and "tracker+drafts" not in json.dumps(state)
    needle_run.run(state, Fetcher(), outbox, now + timedelta(hours=2), adapters={"fake": Adapter()},
                   watchlists_path=watchlists, records_to=TRACKER)
    [(subject, text)] = [s for s in sent if "Morning digest" in s[0]]
    assert "Ask for the records: mailto:tracker+drafts@example.org" in text
    assert "tracker+drafts" not in json.dumps(state)
