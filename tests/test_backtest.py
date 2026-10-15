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
