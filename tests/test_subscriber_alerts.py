import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from needle import mailer, subscribers
from needle import run as needle_run
from needle import state as statefile
from test_run import AGENDA, COUNCIL, NOW, REPORT, FakeAdapter, FakeFetcher, meeting
from pdfs import make_pdf

KEY = "k" * 64
OWNER = "owner@example.org"
WATCHLISTS = """
surveillance: {priority: high, terms: [Flock, license plate reader]}
tenants: {priority: normal, terms: [rent stabilization]}
"""
PEOPLE = [{"email": "tenant@example.org", "watchlists": ["tenants"]},
          {"email": "flock@example.org", "terms": ["Flock"]},
          {"email": "gotham@example.org", "watchlists": ["surveillance"], "places": ["Gotham"]},
          {"email": "own@example.org", "custom": ["camera contract"]}]
ONE_AM = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)  # Thu Oct 15, 1 a.m. Pacific: no digest yet


class Mail:
    """Stands in for mailer.send: keeps each message, refusing the addresses in refuse."""

    def __init__(self):
        self.messages, self.refuse = [], set()

    def __call__(self, messages, settings):
        results = []
        for message in messages:
            if message["To"] in self.refuse:
                results.append("refused (550)")
            else:
                self.messages.append(message)
                results.append(None)
        return results

    def to(self, address):
        return [m["Subject"] for m in self.messages if m["To"] == address]

    def text(self, address):
        return [m.get_body(("plain",)).get_content() for m in self.messages if m["To"] == address]


@pytest.fixture
def setup(tmp_path):
    watchlists = tmp_path / "watchlists.yaml"
    watchlists.write_text(WATCHLISTS)
    adapter = FakeAdapter()
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), title="Flock camera contract"),
                        meeting(COUNCIL, date(2026, 10, 21), item_id="2", matter="200",
                                title="Rent stabilization update")]
    fetcher = FakeFetcher({AGENDA: make_pdf(["Email public comment to clerk@town.example."]),
                           REPORT: make_pdf(["Nothing here"])})
    state = statefile.empty()
    mail = Mail()
    people = list(PEOPLE)

    def go(now=NOW):
        outbox = needle_run.Outbox(False, mailer.Settings("user", "token", OWNER), send=mail)
        needle_run.run(state, fetcher, outbox, now, adapters={"fake": adapter}, watchlists_path=watchlists,
                       load_roster=lambda w: subscribers.load(json.dumps(people), KEY, w, OWNER))
        return outbox

    return adapter, state, mail, people, go


def test_each_person_gets_only_what_they_picked_one_message_each(setup):
    adapter, state, mail, people, go = setup
    go()

    alert = "Townsville City Council, Tue Oct 20, Item 7"
    assert mail.to(OWNER) == [f"[Haystack Needle: surveillance] {alert}",
                              "[Haystack Needle] Morning digest, Wed Oct 14: 1 item"]
    welcome = "[Haystack Needle] You're signed up for agenda alerts"
    digest = "[Haystack Needle] Morning digest, Wed Oct 14: 1 item"
    assert mail.to("tenant@example.org") == [welcome, digest]
    assert mail.to("flock@example.org") == [welcome, f"[Haystack Needle: surveillance] {alert}"]
    assert mail.to("gotham@example.org") == [welcome]  # nothing in Gotham
    assert mail.to("own@example.org") == [welcome, digest]  # their own terms wait for the digest
    for message in mail.messages:
        assert len(message.get_all("To")) == 1 and message["Cc"] is None and message["Bcc"] is None

    [welcome_text, own_digest] = mail.text("own@example.org")
    assert "Your own terms: camera contract" in welcome_text and "every city and agency" in welcome_text
    assert 'Matched: "camera contract" (your terms) in item title' in own_digest
    assert "Useful?" not in own_digest  # votes are for the watchlists
    [owner_alert, owner_digest] = mail.text(OWNER)
    assert 'Matched: "camera contract"' not in owner_alert  # subscribers' own terms are theirs
    assert '"camera contract" (1 person)' in owner_digest
    assert all(text.rstrip().endswith(needle_run.alerts.STOP_LINE)
               for m in mail.messages if m["To"] != OWNER for text in [m.get_body(("plain",)).get_content()])
    assert needle_run.alerts.STOP_LINE not in owner_alert

    # Nothing new: nobody hears again, and nobody is welcomed twice.
    sent = len(mail.messages)
    go(NOW + timedelta(hours=2))
    assert len(mail.messages) == sent


def test_an_update_goes_only_to_those_it_adds_something_for(setup):
    adapter, state, mail, people, go = setup
    go()
    adapter.meetings[0].items[0].title = "Flock camera contract and license plate reader policy"
    before = {p["email"]: len(mail.to(p["email"])) for p in PEOPLE}

    go(NOW + timedelta(hours=2))
    assert mail.to(OWNER)[-1].endswith("Item 7 (updated)")
    assert mail.to("gotham@example.org")[1:] == []
    assert {p["email"]: len(mail.to(p["email"])) for p in PEOPLE} == before  # Flock alone was already sent


def test_no_address_or_own_term_reaches_the_state_or_the_log(setup, capsys):
    adapter, state, mail, people, go = setup
    go(ONE_AM)  # before the digest, so alerts wait in the state

    saved = json.dumps(state)
    assert "example.org" not in saved and "example.org" not in "".join(capsys.readouterr())
    assert set(state["subscribers"]) == {key_of(p["email"]) for p in PEOPLE}
    # A waiting alert quotes the agenda, which may contain the term; nothing else names it.
    waiting = copy.deepcopy(state)
    for record in waiting["subscribers"].values():
        for alert in record["digest"] + record["held"]:
            alert["title"] = ""
            for m in alert["matches"]:
                m["snippet"] = ""
    assert "camera contract" not in json.dumps(waiting).lower()


def key_of(email):
    return subscribers.Roster(KEY).person(subscribers.Subscriber(0, email))


def test_past_the_send_cap_email_waits_for_the_next_run(setup, monkeypatch):
    adapter, state, mail, people, go = setup
    monkeypatch.setattr(needle_run, "SUBSCRIBER_SENDS", 3)
    go(ONE_AM)
    assert [len(mail.to(p["email"])) for p in PEOPLE] == [1, 2, 0, 0]  # welcome; welcome and alert
    assert state["sends"]["held"] == 2

    go(ONE_AM + timedelta(minutes=30))
    assert [len(mail.to(p["email"])) for p in PEOPLE] == [1, 2, 1, 1]
    go(ONE_AM + timedelta(hours=2))  # 3 a.m.: digests for those with alerts waiting
    assert mail.to("tenant@example.org")[-1] == "[Haystack Needle] Morning digest, Thu Oct 15: 1 item"
    assert mail.to("own@example.org")[-1] == "[Haystack Needle] Morning digest, Thu Oct 15: 1 item"
    assert "2 emails to subscribers had to wait" in mail.text(OWNER)[-1]
    assert state["sends"]["count"] == len(mail.messages) and state["sends"]["held"] == 0


def test_the_day_cap_counts_the_owners_email_too(setup, monkeypatch):
    adapter, state, mail, people, go = setup
    monkeypatch.setattr(needle_run, "DAY_SENDS", 4)
    go()
    assert len(mail.to(OWNER)) == 2 and len(mail.messages) == 6  # the owner's always go
    assert mail.to("gotham@example.org") == mail.to("own@example.org") == []
    go(NOW + timedelta(hours=1))
    assert len(mail.messages) == 6
    go(NOW + timedelta(days=1))  # a new day
    assert len(mail.to("gotham@example.org")) == len(mail.to("own@example.org")) - 1 == 1


def test_a_refused_address_is_tried_again_and_reported_by_number(setup, capsys):
    adapter, state, mail, people, go = setup
    mail.refuse.add("flock@example.org")
    go()
    record = state["subscribers"][key_of("flock@example.org")]
    assert mail.to("flock@example.org") == [] and state["sends"]["refused"] == [2]
    assert record["welcomed"] is None and len(record["held"]) == 1
    assert "example.org" not in "".join(capsys.readouterr())

    mail.refuse.clear()
    go(NOW + timedelta(hours=1))
    assert mail.to("flock@example.org") == ["[Haystack Needle] You're signed up for agenda alerts",
                                            "[Haystack Needle: surveillance] Townsville City Council, Tue Oct 20, Item 7"]
    go(NOW + timedelta(days=1))
    assert "subscriber 2 (their place in the SUBSCRIBERS list)" in mail.text(OWNER)[-1]
    assert state["sends"]["refused"] == []


def test_someone_taken_off_the_list_is_forgotten(setup):
    adapter, state, mail, people, go = setup
    go()
    people.pop(0)
    go(NOW + timedelta(hours=1))
    assert len(state["subscribers"]) == 3
