import json
from datetime import date

from needle import state as statefile
from needle import votes
from needle.matcher import compile_terms

TODAY = date(2026, 10, 18)
TERMS = compile_terms({"surveillance": {"terms": ["Flock", "ALPR"]}})


def with_alert():
    state = statefile.empty()
    state["alerts"]["town/matter/100@m1"] = {
        "id": "a1b2c3d4e5", "earlier_ids": ["0f0f0f0f0f"], "body_name": "City Council", "date": "2026-10-21",
        "sigs": ["surveillance|Flock|attachment|a1", "surveillance|ALPR|title|"]}
    return state


def mail(subject, voter="v1", day="2026-10-18"):
    return {"subject": subject, "voter": voter, "date": day}


def test_counts_a_vote_on_an_alert_the_state_remembers():
    state = with_alert()

    assert votes.count(state, [mail("Noise: surveillance / Flock [a1b2c3d4e5]")]) == 1
    assert state["votes"]["terms"] == {"surveillance|Flock": {"useful": [], "noise": ["2026-10-18"]}}
    assert state["votes"]["new"] == [{"vote": "noise", "watchlist": "surveillance", "term": "Flock",
                                      "body": "City Council", "date": "2026-10-21"}]
    assert votes.since_digest(state) == [
        "Noise: surveillance / Flock, on City Council, Wed Oct 21 (so far 0 useful, 1 noise)"]


def test_counts_one_vote_per_person_per_alert():
    state = with_alert()
    votes.count(state, [mail("Noise: surveillance / Flock [a1b2c3d4e5]")])

    # Again, on another of the alert's terms, or on the alert before its update: left out.
    assert votes.count(state, [mail("Noise: surveillance / Flock [a1b2c3d4e5]"),
                               mail("Useful: surveillance / ALPR [a1b2c3d4e5]"),
                               mail("Useful: surveillance / Flock [0f0f0f0f0f]")]) == 0
    assert votes.count(state, [mail("Useful: surveillance / Flock [a1b2c3d4e5]", voter="v2")]) == 1
    assert state["votes"]["terms"]["surveillance|Flock"] == {"useful": ["2026-10-18"], "noise": ["2026-10-18"]}
    assert state["votes"]["skipped"] == 0


def test_counts_only_the_exact_format_for_a_known_alert_and_term():
    state = with_alert()
    subjects = ["Re: Noise: surveillance / Flock [a1b2c3d4e5]",  # not the format
                "noise: surveillance / Flock [a1b2c3d4e5]",
                "Noise: surveillance / Flock [ffffffffff]",  # no such alert
                "Noise: surveillance / Axon [a1b2c3d4e5]",  # not a term that alert matched
                "Noise: tenants / Flock [a1b2c3d4e5]",
                "What do these links do?"]

    assert votes.count(state, [mail(s) for s in subjects] + [mail("Noise: surveillance / Flock [a1b2c3d4e5]",
                                                                  voter=None)]) == 0
    assert state["votes"]["skipped"] == 7 and state["votes"]["terms"] == {} and state["votes"]["new"] == []


def test_the_state_keeps_no_address_and_no_subject():
    state = with_alert()
    votes.count(state, [mail("Noise: surveillance / Flock [a1b2c3d4e5]", voter="keyed-hash-of-sender")])

    saved = json.dumps(state)
    assert "keyed-hash-of-sender" not in saved and "[a1b2c3d4e5]" not in saved
    # The voter is kept per alert, so one person's votes on two alerts can't be linked.
    assert len(state["alerts"]["town/matter/100@m1"]["voters"][0]) == 16


def test_a_term_with_noise_and_no_useful_votes_in_30_days_is_one_to_review():
    state = with_alert()
    state["votes"]["terms"] = {"surveillance|Flock": {"useful": ["2026-09-01"], "noise": ["2026-10-01", "2026-10-10"]},
                               "surveillance|ALPR": {"useful": ["2026-10-02"], "noise": ["2026-10-03"] * 3},
                               "surveillance|Axon": {"useful": [], "noise": ["2026-10-03"] * 4}}
    assert votes.to_review(state, TODAY, TERMS) == []

    votes.count(state, [mail("Noise: surveillance / Flock [a1b2c3d4e5]")])
    # Axon has the votes but is no longer in the watchlists.
    assert votes.to_review(state, TODAY, TERMS) == [
        "surveillance / Flock: 3 noise votes and no useful ones in the last 30 days"]


def test_old_votes_are_forgotten():
    state = with_alert()
    state["votes"]["terms"] = {"surveillance|Flock": {"useful": ["2025-01-01"], "noise": ["2025-01-01", "2026-10-01"]},
                               "surveillance|ALPR": {"useful": ["2025-01-01"], "noise": []}}
    statefile.prune(state, TODAY)
    assert state["votes"]["terms"] == {"surveillance|Flock": {"useful": [], "noise": ["2026-10-01"]}}
