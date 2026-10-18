import copy
from datetime import date, datetime, timedelta, timezone

import pytest

from needle import run as needle_run
from needle import state as statefile
from needle.fetch import Deferred
from needle.models import Attachment, Body, Item, Meeting, Source
from pdfs import make_pdf

TOWN = Source("fake", "town", "Townsville")
COUNCIL = Body(TOWN, "1", "City Council")
COMMITTEE = Body(TOWN, "2", "Public Safety Committee")
NOW = datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc)  # Wed Oct 14, 10 a.m. Pacific
AGENDA = "https://town.example/agenda.pdf"
REPORT = "https://town.example/report.pdf"
WATCHLISTS = """
surveillance: {priority: high, terms: [Flock, license plate reader]}
tenants: {priority: normal, terms: [rent stabilization]}
"""


class FakeAdapter:
    SOURCES = {"town": TOWN}

    def __init__(self):
        self.meetings, self.texts, self.history = [], {}, {}
        self.fail = False
        self.text_reads = 0
        self.windows = []

    def collect(self, fetcher, today, with_text, days_back=2, days_ahead=30, cache=None):
        self.windows.append((days_back, days_ahead))
        if self.fail:
            return [], {"town": "ConnectError: the site is down"}
        start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
        return copy.deepcopy([m for m in self.meetings if start <= m.date <= end]), {}

    def item_text(self, fetcher, client, item):
        self.text_reads += 1
        return self.texts.get(item.matter_id, "")

    def matter_history(self, fetcher, client, matter_id):
        return self.history.get(matter_id, [])

    def calendar_url(self, client):
        return "https://town.example/calendar"


class FakeFetcher:
    def __init__(self, files):
        self.files, self.gets, self.slow = files, [], set()

    def get(self, url):
        if url in self.slow:
            raise Deferred("a slow site's time is up")
        self.gets.append(url)
        return self.files[url]


def meeting(body, day, item_id="1", title="Agreement for public safety technology", matter="100",
            attachments=(), meeting_id=None):
    item = Item(item_id, title, number="7", matter_id=matter, attachments=list(attachments))
    return Meeting(body, meeting_id or f"{body.id}-{day}", day, "6:00 PM", agenda_url=AGENDA, items=[item])


@pytest.fixture
def setup(tmp_path):
    watchlists = tmp_path / "watchlists.yaml"
    watchlists.write_text(WATCHLISTS)
    adapter = FakeAdapter()
    fetcher = FakeFetcher({
        AGENDA: make_pdf(["To submit public comment, email clerk@town.example by 5 p.m. Monday."]),
        REPORT: make_pdf(["Summary", "The contract covers 12 Flock cameras at city entrances."]),
    })
    state = statefile.empty()

    def go(now=NOW, **kwargs):
        outbox = needle_run.Outbox(dry_run=True)
        needle_run.run(state, fetcher, outbox, now, adapters={"fake": adapter},
                       watchlists_path=watchlists, **kwargs)
        return outbox.sent

    return adapter, fetcher, state, go


def test_alerts_once_and_skips_unchanged_items(setup, capsys):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), attachments=[Attachment("a1", "Staff Report", REPORT)])]

    assert go() == ["[Haystack Needle: surveillance] Townsville City Council, Tue Oct 20, Item 7"]
    printed = capsys.readouterr().out
    assert 'Matched: "Flock" (surveillance) in Staff Report, p. 2' in printed
    assert "clerk@town.example by 5 p.m. Monday" in printed

    assert go(NOW + timedelta(hours=2)) == []
    assert fetcher.gets.count(REPORT) == 1 and fetcher.gets.count(AGENDA) == 1
    assert adapter.text_reads == 1


def test_new_matching_attachment_sends_an_update(setup):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), attachments=[Attachment("a1", "Staff Report", REPORT)])]
    go()
    fetcher.files["https://town.example/memo.pdf"] = make_pdf(["Supplemental memo on the license plate reader"])
    adapter.meetings[0].items[0].attachments.append(Attachment("a2", "Memo", "https://town.example/memo.pdf"))

    [subject] = go(NOW + timedelta(hours=2))
    assert subject.endswith("Item 7 (updated)")


def test_same_matter_at_a_new_body_moved_forward(setup, capsys):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COMMITTEE, date(2026, 10, 15), title="Flock camera contract")]
    go()
    adapter.meetings.append(meeting(COUNCIL, date(2026, 10, 27), item_id="2", title="Flock camera contract"))
    capsys.readouterr()

    assert go(NOW + timedelta(hours=2)) == ["[Haystack Needle: surveillance] Townsville City Council, Tue Oct 27, Item 7"]
    assert "This moved forward: from Public Safety Committee (Thu Oct 15, 2026)" in capsys.readouterr().out


def test_normal_priority_waits_for_the_morning_digest(setup):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), title="Rent stabilization update")]
    one_am = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)

    assert go(one_am) == []
    assert len(state["digest"]) == 1
    # The first run after 2 a.m. sends it (the 2:47 try, or an hourly run), and later ones don't.
    assert go(one_am + timedelta(hours=1, minutes=47)) == ["[Haystack Needle] Morning digest, Thu Oct 15: 1 item"]
    assert state["digest"] == [] and go(one_am + timedelta(hours=5, minutes=47)) == []


def test_votes_from_the_adapter_are_counted_even_after_an_update(setup, capsys):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), attachments=[Attachment("a1", "Staff Report", REPORT)])]
    go()
    [entry] = state["alerts"].values()
    first = entry["id"]
    adapter.collected_votes = lambda: [{"subject": f"Noise: surveillance / Flock [{first}]", "voter": "v1",
                                        "date": "2026-10-14"}]
    go(NOW + timedelta(minutes=30))
    assert "Counted 1 vote." in capsys.readouterr().out

    fetcher.files["https://town.example/memo.pdf"] = make_pdf(["Supplemental memo on the license plate reader"])
    adapter.meetings[0].items[0].attachments.append(Attachment("a2", "Memo", "https://town.example/memo.pdf"))
    adapter.collected_votes = lambda: []
    go(NOW + timedelta(hours=2))
    [entry] = state["alerts"].values()
    assert entry["id"] != first and entry["earlier_ids"] == [first] and len(entry["voters"]) == 1

    # The same person on the updated alert: still one vote. Someone else: counted.
    adapter.collected_votes = lambda: [
        {"subject": f"Useful: surveillance / license plate reader [{entry['id']}]", "voter": v, "date": "2026-10-14"}
        for v in ("v1", "v2")]
    go(NOW + timedelta(hours=3))
    assert state["votes"]["terms"] == {"surveillance|Flock": {"useful": [], "noise": ["2026-10-14"]},
                                       "surveillance|license plate reader": {"useful": ["2026-10-14"], "noise": []}}


def test_the_digest_lists_votes_and_terms_to_review(setup, capsys):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), attachments=[Attachment("a1", "Staff Report", REPORT)])]
    go()
    [entry] = state["alerts"].values()
    adapter.collected_votes = lambda: [
        {"subject": f"Noise: surveillance / Flock [{entry['id']}]", "voter": v, "date": "2026-10-14"}
        for v in ("v1", "v2", "v3")] + [{"subject": "Noise: surveillance / Flock", "voter": "v4", "date": "2026-10-14"}]
    capsys.readouterr()

    # Votes alone send the digest.
    assert go(datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)) == [
        "[Haystack Needle] Morning digest, Thu Oct 15: 3 votes counted"]
    printed = capsys.readouterr().out
    assert "  - Noise: surveillance / Flock, on Townsville City Council, Tue Oct 20 (so far 0 useful, 3 noise)" in printed
    assert "1 email to the votes address didn't count" in printed
    assert "  - surveillance / Flock: 3 noise votes and no useful ones in the last 30 days" in printed
    assert state["votes"]["new"] == [] and state["votes"]["skipped"] == 0

    # A term to review alone doesn't send one.
    adapter.collected_votes = lambda: []
    assert go(datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc)) == []


def test_meetings_already_over_dont_alert(setup):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 13), title="Flock contract")]
    assert go() == []


def test_boilerplate_is_held_back_and_reported_in_the_digest(setup, capsys):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 15) + timedelta(days=7 * n), title="Consent calendar",
                                matter=None) for n in range(4)]
    adapter.texts = {}
    for m in adapter.meetings:
        m.items[0].text = "Report from the sheriff on Flock Safety audits"

    assert go() == ["[Haystack Needle] Morning digest, Wed Oct 14: nothing new, 1 phrase held back"]
    assert '"Flock" (surveillance) is on 4 of 4 Townsville City Council agendas' in capsys.readouterr().out


def test_agendas_emailed_only_as_a_notice_are_listed_once_in_the_digest(setup, capsys):
    adapter, fetcher, state, go = setup
    posted = meeting(COUNCIL, date(2026, 10, 19), title="City Council Regular Meeting", matter=None)
    posted.notice, posted.url = "Mon Oct 19: City Council Regular Meeting at 5:30 pm", "https://town.example/portal"
    adapter.meetings = [posted]

    assert go() == ["[Haystack Needle] Morning digest, Wed Oct 14: 1 agenda to check by hand"]
    printed = capsys.readouterr().out
    assert "Townsville: Mon Oct 19: City Council Regular Meeting at 5:30 pm" in printed
    assert "https://town.example/portal" in printed
    assert go(NOW + timedelta(days=1)) == []  # listed once


def test_a_notice_is_left_out_when_another_source_reads_that_agenda(setup):
    adapter, fetcher, state, go = setup
    posted = meeting(COUNCIL, date(2026, 10, 19), title="City Councils Regular Meeting", matter=None,
                     meeting_id="email-1")
    posted.notice = "Mon Oct 19: City Council Regular Meeting at 5:30 pm"
    adapter.meetings = [posted, meeting(COUNCIL, date(2026, 10, 19), title="Agenda", matter=None)]

    assert go() == [] and state["posted"] == {}


def test_a_source_failing_twice_warns_once(setup):
    adapter, fetcher, state, go = setup
    adapter.fail = True
    assert go(full=True) == []
    assert go(NOW + timedelta(hours=2), full=True) == ["[Haystack Needle] Can't read Townsville: failed 2 runs in a row"]
    assert go(NOW + timedelta(hours=4), full=True) == []
    adapter.fail = False
    go(NOW + timedelta(hours=6), full=True)
    assert state["sources"]["fake/town"] == {"fails": 0, "error": "", "warned": False}


def test_a_regular_body_gone_quiet_warns_once(setup):
    adapter, fetcher, state, go = setup
    state["bodies"]["fake/town/body/1"] = {"name": "Townsville City Council", "warned": False, "agendas": {},
                                           "dates": ["2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24"]}
    assert go() == ["[Haystack Needle] No meetings from Townsville City Council in 14 days"]
    assert go(NOW + timedelta(hours=2)) == []


def test_a_body_the_run_no_longer_reads_is_not_called_quiet(setup):
    adapter, fetcher, state, go = setup
    # A school board read before schools were shelved: its source isn't run any more.
    state["bodies"]["boardable/fhda/body/1"] = {"name": "Foothill-De Anza CCD Board of Trustees", "warned": False,
                                                "agendas": {}, "dates": ["2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24"]}
    assert go() == []


def test_between_full_runs_only_near_meetings_are_read(setup):
    adapter, fetcher, state, go = setup
    go()
    go(NOW + timedelta(hours=1))
    go(NOW + timedelta(hours=2))
    assert adapter.windows == [(2, 30), (0, 3), (2, 30)]


def test_a_new_term_rereads_everything(setup, tmp_path):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), attachments=[Attachment("a1", "Staff Report", REPORT)])]
    go()
    (tmp_path / "watchlists.yaml").write_text(WATCHLISTS + "cameras: {priority: high, terms: [city entrance]}\n")

    assert go(NOW + timedelta(hours=2)) == [
        "[Haystack Needle: surveillance, cameras] Townsville City Council, Tue Oct 20, Item 7 (updated)"]
    assert fetcher.gets.count(REPORT) == 2 and adapter.text_reads == 2


def test_taking_terms_out_rereads_nothing(setup, tmp_path):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), title="Rent stabilization and Flock cameras",
                                attachments=[Attachment("a1", "Staff Report", REPORT)])]
    go()
    # Flock goes, license plate reader turns case-sensitive, tenants turns high priority, a comment.
    (tmp_path / "watchlists.yaml").write_text(
        "# a comment\nsurveillance: {priority: high, terms: [license plate reader], case_sensitive: [license plate reader]}\n"
        "tenants: {priority: high, terms: [rent stabilization]}\n")
    adapter.meetings[0].items[0].attachments.append(Attachment("a2", "Memo", REPORT + "?memo"))
    fetcher.files[REPORT + "?memo"] = make_pdf(["Flock cameras again"])

    go(NOW + timedelta(hours=2))
    assert fetcher.gets.count(REPORT) == 1 and adapter.text_reads == 1
    assert state["terms"] == ["c|license plate reader", "i|rent stabilization"]

    # Making it match any case again counts as new.
    (tmp_path / "watchlists.yaml").write_text(WATCHLISTS)
    go(NOW + timedelta(hours=4))
    assert fetcher.gets.count(REPORT) == 2 and adapter.text_reads == 2


def test_a_state_from_before_the_term_list_keeps_its_cache(setup):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), attachments=[Attachment("a1", "Staff Report", REPORT)])]
    go()
    state["terms"], state["watchlists"] = None, "b0973a3d8d7d"

    go(NOW + timedelta(hours=2))
    assert fetcher.gets.count(REPORT) == 1 and "watchlists" not in state and len(state["terms"]) == 3


def test_state_survives_a_round_trip(setup, tmp_path):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), attachments=[Attachment("a1", "Staff Report", REPORT)])]
    go()
    statefile.save(state, tmp_path / "state.json")
    assert statefile.load(tmp_path / "state.json") == state
    assert b"%PDF" not in (tmp_path / "state.json").read_bytes()


def test_meetings_past_the_time_budget_wait_for_the_next_run(setup):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), title="Flock contract"),
                        meeting(COUNCIL, date(2026, 10, 27), title="Flock contract, part 2", matter="101", item_id="2")]
    ticks = iter([0, 0, 100, 100])  # the second meeting comes after the budget runs out
    assert len(go(budget=timedelta(seconds=50), clock=lambda: next(ticks))) == 1
    assert len(go(NOW + timedelta(hours=2))) == 1


def test_an_adapter_can_keep_item_text_until_it_changes(setup):
    adapter, fetcher, state, go = setup
    adapter.TEXT_TTL = None  # IQM2: its staff reports carry no modified time
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20))]
    go()
    go(NOW + timedelta(days=2))
    assert adapter.text_reads == 1


def test_downloads_from_a_slow_site_out_of_time_wait_quietly_for_the_next_run(setup, capsys):
    adapter, fetcher, state, go = setup
    adapter.meetings = [meeting(COUNCIL, date(2026, 10, 20), title="Agreement",
                                attachments=[Attachment("a1", "Staff Report", REPORT)])]
    fetcher.slow = {REPORT}
    assert go() == []
    assert "Couldn't" not in capsys.readouterr().err
    fetcher.slow = set()
    assert go(NOW + timedelta(hours=2)) == [
        "[Haystack Needle: surveillance] Townsville City Council, Tue Oct 20, Item 7"]


def test_only_one_adapter_is_for_dry_runs(capsys):
    with pytest.raises(SystemExit):
        needle_run.main(["--only", "iqm2"])
    assert "--only is for dry runs" in capsys.readouterr().err
