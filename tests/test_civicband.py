from datetime import date, datetime, timezone

import httpx
import pytest

from adapters import civicband
from conftest import FakeFetcher, load
from needle.matcher import compile_terms, match_meeting
from needle.run import Reader

AGENDAS = load("civicband/paloalto-agendas.json")
TODAY = date(2026, 10, 16)


def test_reads_each_agenda_as_a_meeting():
    fetcher = FakeFetcher({"/meetings/agendas.json": AGENDAS})
    meetings, failures = civicband.collect(fetcher, today=TODAY)
    assert failures == {}
    [(url, params)] = fetcher.calls
    assert url == "https://paloalto.ca.civic.band/meetings/agendas.json"
    assert (params["date__gte"], params["date__lte"]) == ("2026-10-14", "2026-11-15")
    assert params["_col"] == ["meeting", "date", "page", "text"]

    council, finance = sorted(meetings, key=lambda m: m.date)
    assert (council.body.source.name, council.body.name, council.date) == ("Palo Alto", "City Council",
                                                                          date(2026, 10, 19))
    assert finance.body.name == "Finance Committee"
    assert council.url == "https://cityofpaloalto.primegov.com/public/portal"
    assert council.agenda_url is None  # nothing to download
    [item] = council.items
    [agenda] = item.attachments
    assert item.title == "Agenda" and len(agenda.pages) == 3
    assert agenda.pages[1].startswith("CALL TO ORDER")
    assert agenda.url == "https://paloalto.ca.civic.band/meetings/agendas?meeting=CityCouncil&date=2026-10-19"


def test_follows_datasettes_next_token():
    first = {**AGENDAS, "rows": AGENDAS["rows"][:2], "next": "abc"}
    second = {**AGENDAS, "rows": AGENDAS["rows"][2:]}
    answers = [first, second]
    fetcher = FakeFetcher({"/meetings/agendas.json": lambda params: answers.pop(0)})
    meetings, _ = civicband.collect(fetcher, today=TODAY)
    assert len(fetcher.calls) == 2 and fetcher.calls[1][1]["_next"] == "abc"
    assert sorted(len(m.items[0].attachments[0].pages) for m in meetings) == [1, 3]


def test_reports_a_failure():
    def down(params):
        raise httpx.ConnectError("down")

    meetings, failures = civicband.collect(FakeFetcher({"/meetings/agendas.json": down}), today=TODAY)
    assert meetings == [] and failures == {"paloalto": "Couldn't read Civic Band: ConnectError"}


def test_a_match_says_which_page_its_on():
    meetings, _ = civicband.collect(FakeFetcher({"/meetings/agendas.json": AGENDAS}), today=TODAY)
    [council] = [m for m in meetings if m.body.name == "City Council"]

    class NoFetcher:
        def get(self, url, params=None):
            raise AssertionError("Civic Band's pages come as text; nothing is downloaded")

    terms = compile_terms({"housing": {"terms": ["oversized vehicles"]}})
    state = {"attachments": {}}
    reader = Reader(NoFetcher(), state, terms, datetime(2026, 10, 16, 15, tzinfo=timezone.utc), 0)
    pages = lambda a: reader.pages(council.body.source, a)  # noqa: E731
    assert [(m.location, m.page) for m in match_meeting(council, terms, pages)] == [("attachment", 2)]
    # Read again unchanged, it's the saved snippets; changed, it's read anew.
    assert [m.page for m in match_meeting(council, terms, pages)] == [2]
    council.items[0].attachments[0].pages[1] = "No parking items this week."
    assert match_meeting(council, terms, pages) == []


@pytest.mark.parametrize("meeting, name", [
    ("CityCouncil", "City Council"),
    ("ClimateActionandSustainabilityCommittee", "Climate Action and Sustainability Committee"),
    ("PolicyAndamp;ServicesCommittee", "Policy and Services Committee"),
    ("PlanningAndTransportationCommission", "Planning and Transportation Commission"),
    ("ParksandRecreationCommission", "Parks and Recreation Commission"),
    ("PlanningDirector'sHearing", "Planning Director's Hearing"),
])
def test_spaces_out_body_names(meeting, name):
    assert civicband.body_name(meeting) == name
