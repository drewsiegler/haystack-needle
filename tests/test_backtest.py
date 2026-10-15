import importlib.util
from datetime import date
from pathlib import Path

from conftest import FakeFetcher, load
from needle.matcher import compile_terms

spec = importlib.util.spec_from_file_location("backtest_run", Path(__file__).parents[1] / "backtest" / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

TERMS = compile_terms({"surveillance": {"terms": ["license plate reader", "Flock"]}})


def fetcher(event_date=None):
    events = load("legistar/mountainview_events.json")
    if event_date:
        events[0]["EventDate"] = event_date
    return FakeFetcher({
        "/events": events,
        "/events/3354/eventitems": load("legistar/mountainview_eventitems_3354.json"),
        "/matters/10357/versions": [],
    })


def test_replay_stage_finds_the_matter_and_matches_its_title():
    stage = {"date": date(2026, 2, 24), "matter": 10357}
    result = run.replay_stage(fetcher(), "mountainview", stage, TERMS, lambda a: ["Flock Safety"])
    assert result["item"].title == "Automated License Plate Reader Contract"
    assert result["late"] == 0
    assert run.where(result["matches"]) == ('title: "license plate reader"; '
                                            'attachment (Council Report p. 1): "Flock"')


def test_replay_stage_leaves_out_attachments_changed_after_the_meeting():
    stage = {"date": date(2026, 2, 19), "matter": 10357}  # a meeting the day before the upload
    result = run.replay_stage(fetcher("2026-02-19T00:00:00"), "mountainview", stage, TERMS, lambda a: ["Flock"])
    assert result["late"] == 1
    assert [m.location for m in result["matches"]] == ["title"]


def test_replay_stage_reports_a_matter_not_on_the_agenda():
    stage = {"date": date(2026, 2, 24), "matter": 1}
    result = run.replay_stage(fetcher(), "mountainview", stage, TERMS, None)
    assert result["item"] is None and result["matches"] == []


class PageFetcher:
    def __init__(self, pages):
        self.pages, self.gets = pages, []

    def get(self, url):
        self.gets.append(url)
        return next(page for part, page in self.pages.items() if part in url).encode()


def test_replay_iqm2_stage_finds_the_item_by_meeting_and_title():
    fixtures = Path(__file__).parent / "fixtures" / "iqm2"
    fetcher = PageFetcher({"Detail_Meeting.aspx?ID=17578": (fixtures / "sccgov_meeting_17578.html").read_text(),
                           "Detail_LegiFile.aspx?ID=127331": "<html>no report sections</html>"})
    stage = {"date": date(2026, 1, 27), "body": "Board of Supervisors", "iqm2_meeting_id": 17578,
             "item": "Item 56: Consider recommendations relating to the Surveillance Use Policy for the "
                     "Automated License Plate Readers ... (Office of the Sheriff)"}
    result = run.replay_stage(fetcher, "sccgov", stage, TERMS, None)
    assert result["item"].number == "56" and result["item"].matter_id == "127331"
    assert run.where(result["matches"]) == 'title: "license plate reader"; item text: "license plate reader"'
    assert run.stage_label(stage) == "meeting 17578"


def test_replay_civicclerk_stage_finds_the_item_by_agenda_and_title():
    base = "https://losaltoshillsca.api.civicclerk.com/v1"
    january = load("civicclerk/losaltoshillsca_events_2026-01-10_2026-01-20.json")
    stage = {"date": date(2026, 1, 15), "body": "City Council", "civicclerk_agenda_id": 4957,
             "item": "Discussion and Possible Direction on Terminating Agreement with FLOCK Safety for the "
                     "Town's Automatic License Plate Reader System"}
    result = run.replay_stage(FakeFetcher({f"{base}/Events": january,
                                           f"{base}/Meetings/4957": load("civicclerk/losaltoshillsca_meeting_4957.json")}),
                              "losaltoshillsca", stage, TERMS, None)

    assert result["meeting"].id == "4354" and result["item"].number == "6.A"
    assert run.where(result["matches"]) == 'title: "license plate reader", "Flock"'
    assert run.stage_label(stage) == "agenda 4957"


def test_replay_civicclerk_stage_is_not_found_when_no_meeting_has_that_agenda():
    base = "https://losaltoshillsca.api.civicclerk.com/v1"
    stage = {"date": date(2026, 1, 15), "body": "City Council", "civicclerk_agenda_id": 9999, "item": "Anything"}
    result = run.replay_stage(FakeFetcher({f"{base}/Events": load("civicclerk/losaltoshillsca_events_2026-01-10_2026-01-20.json")}),
                              "losaltoshillsca", stage, TERMS, None)

    assert result["item"] is None and result["matches"] == []


def test_replay_civicplus_stage_reads_the_agenda_pdf_found_by_its_id():
    january = (Path(__file__).parent / "fixtures" / "civicplus" / "saratoga_search_2026-01-01_2026-01-31.html").read_bytes()
    stage = {"date": date(2026, 1, 21), "body": "City Council",
             "url": "https://www.saratoga.ca.us/AgendaCenter/ViewFile/Agenda/_01212026-1401"}
    terms = compile_terms({"policing-contracts": {"terms": ["Sheriff's Office contract"]}})
    pages = ["2.1 Multi-Jurisdictional Sheriff’s Office Contract Negotiations Ad Hoc Committee"]
    result = run.replay_stage(PageFetcher({"/AgendaCenter/Search/": january.decode()}), "saratoga", stage, terms,
                              lambda a: pages if a.url == stage["url"] else [])
    assert result["meeting"].body.name == "City Council" and result["late"] == 0
    assert run.stage_label(stage) == "agenda 1401"
    assert run.where(result["matches"]) == 'attachment (Agenda p. 1): "Sheriff\'s Office contract"'


def test_replay_civicplus_stage_is_not_found_when_that_day_has_no_such_agenda():
    january = (Path(__file__).parent / "fixtures" / "civicplus" / "saratoga_search_2026-01-01_2026-01-31.html").read_text()
    stage = {"date": date(2026, 1, 21), "url": "https://www.saratoga.ca.us/AgendaCenter/ViewFile/Agenda/_01212026-9999"}
    result = run.replay_stage(PageFetcher({"/AgendaCenter/Search/": january}), "saratoga", stage, TERMS, None)
    assert result["item"] is None


def test_replay_civicplus_stage_reads_the_version_that_was_up_on_the_meeting_day():
    # The Oct 7, 2026 agenda was amended on Oct 8; its Previous Versions page lists the
    # current row too, so it stands in for the search page as well.
    page = (Path(__file__).parent / "fixtures" / "civicplus" / "saratoga_previousversions_10072026-1482.html").read_text()
    stage = {"date": date(2026, 10, 7), "url": "https://www.saratoga.ca.us/AgendaCenter/ViewFile/Agenda/_10072026-1482"}
    fetcher = PageFetcher({"/AgendaCenter/Search/": page, "/AgendaCenter/PreviousVersions/_10072026-1482": page})
    result = run.replay_stage(fetcher, "saratoga", stage, TERMS, None)
    [agenda] = result["item"].attachments
    assert agenda.url == "https://www.saratoga.ca.us/AgendaCenter/ViewFile/ArchivedAgenda/_10072026-1327"
    assert result["late"] == 0
