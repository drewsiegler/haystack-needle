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
