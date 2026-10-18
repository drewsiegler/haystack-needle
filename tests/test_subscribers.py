import csv
import io
import json
import sys

import pytest

from needle import subscribers
from needle.subscribers import CUSTOM, ROOT, Roster, SubscriberError, load, read_form

KEY = "k" * 64
WATCHLISTS = {"surveillance": {"priority": "high", "terms": ["Flock", "license plate reader"]},
              "tenants": {"priority": "normal", "terms": ["rent stabilization"]},
              "immigration": {"priority": "high", "terms": ["ICE"], "case_sensitive": ["ICE"]}}
PLACES = ["Campbell", "San José", "Santa Clara County"]


def roster(*entries, key=KEY, **kwargs):
    return load(json.dumps(list(entries)), key, WATCHLISTS, **kwargs)


def test_no_list_means_no_subscribers():
    assert load(None, None, WATCHLISTS) is None and load("  ", KEY, WATCHLISTS) is None


def test_a_list_needs_its_key_and_errors_never_quote_it():
    with pytest.raises(SubscriberError, match="SUBSCRIBERS_KEY"):
        load('[{"email": "a@example.org"}]', None, WATCHLISTS)
    with pytest.raises(SubscriberError) as error:
        load('[{"email": "a@example.org",}]', KEY, WATCHLISTS)
    assert "line 1" in str(error.value) and "example.org" not in str(error.value)


def test_who_wants_what():
    r = roster({"email": "A@Example.org", "watchlists": ["tenants"], "terms": ["flock"],
                "custom": ["Measure A"], "places": ["San Jose"]})
    [a] = r.people
    assert a.email == "a@example.org" and a.terms == {"Flock"}
    assert a.wants("tenants", "rent stabilization", "San José")
    assert a.wants("surveillance", "Flock", "San José")
    assert not a.wants("surveillance", "license plate reader", "San José")
    assert a.wants(CUSTOM, r.label("measure  a"), "San José")
    assert not a.wants(CUSTOM, r.label("Measure B"), "San José")
    assert not a.wants("tenants", "rent stabilization", "Campbell")


def test_own_terms_already_on_a_watchlist_are_matched_as_that_term():
    [a] = roster({"email": "a@example.org", "custom": ["License Plate Reader"]}).people
    assert a.terms == {"license plate reader"} and a.custom == ()
    entries, _ = read_form([["Email", "Your own terms"], ["a@example.org", "Rent & Stabilization"]],
                           {"tenants": {"terms": ["rent and stabilization"]}}, PLACES)
    assert entries[0]["terms"] == ["rent and stabilization"]  # "&" reads as "and"


def test_own_terms_match_once_each_under_a_keyed_label():
    r = roster({"email": "a@example.org", "custom": ["Measure A"]},
               {"email": "b@example.org", "custom": ["measure a", "Alviso Tenants Union"]})
    terms = r.terms()
    assert len(terms) == 2 and {t.watchlist for t in terms} == {CUSTOM}
    assert all(t.text.startswith("#") and "easure" not in t.text for t in terms)
    assert terms[0].pattern.search("an item on MEASURE A funding")
    assert Roster("x" * 64).label("Measure A") != r.label("Measure A")  # another key, another label


def test_a_person_is_known_by_a_keyed_hash_that_stays_the_same():
    a = roster({"email": "a@example.org", "watchlists": ["tenants"]})
    b = roster({"email": " A@example.org", "watchlists": ["immigration"]})
    other = roster({"email": "a@example.org", "watchlists": ["tenants"]}, key="x" * 64)
    assert a.person(a.people[0]) == b.person(b.people[0]) != other.person(other.people[0])
    assert "example" not in a.person(a.people[0]) + repr(a) + repr(a.people[0]) + str(a.people[0])


def test_bad_entries_are_left_out_by_number():
    warnings = []
    r = load(json.dumps([{"email": "not-an-address"}, {"email": "b@example.org", "watchlists": ["gone"]},
                         {"email": "owner@example.org", "watchlists": ["tenants"]},
                         {"email": "c@example.org", "watchlists": ["tenants"]}]),
             KEY, WATCHLISTS, owner="Owner@example.org", warn=warnings.append)
    assert [p.number for p in r.people] == [4]  # the owner gets everything already
    assert warnings == ["Subscriber 1 has no usable email address; left out.",
                        "Subscriber 2 picked gone, not a watchlist now; left out.",
                        "Subscriber 2 picked nothing the app can match; left out."]


# CryptPad's export: time, participant, then one column per question, several picks comma-joined.
FORM = [["Time", "Participant", "Email address", "Categories", "Single terms from the watchlists",
         "New terms", "Cities or agencies"],
        ["2026-10-18T10:00:00Z", "Anonymous", "a@example.org", "tenants,Surveillance", "", "Measure A", ""],
        ["2026-10-18T11:00:00Z", "Anonymous", "b@example.org", "", "ICE", "", "San Jose,Gotham"],
        ["2026-10-18T12:00:00Z", "Anonymous", "", "tenants", "", "", ""],
        ["2026-10-18T13:00:00Z", "Anonymous", "a@example.org", "immigration", "", "flock; Measure B",
         "All cities and agencies"]]


def test_the_form_export_becomes_the_list():
    entries, notes = read_form(FORM, WATCHLISTS, PLACES)
    assert entries == [
        {"email": "a@example.org", "watchlists": ["immigration"], "terms": ["Flock"], "custom": ["Measure B"],
         "places": []},
        {"email": "b@example.org", "watchlists": [], "terms": ["ICE"], "custom": [], "places": ["San José"]}]
    assert notes == ['Row 3: "Gotham" isn\'t a city or agency the app reads; left out.',
                     "Row 4: no usable email address; left out.",
                     "Row 5 replaces row 2, from the same address."]


def test_questions_are_found_by_whole_words_in_their_titles():
    # The titles on Drew's form, Oct 18, 2026: the categories question says "emailed".
    header = ["Time", "Participant", "Please enter your email address",
              "Pick whole categories. Matches for surveillance, policing contracts and immigration are "
              "emailed right away; the rest come in a morning digest.",
              "Want just a few terms instead, or something that’s not on a list? Type them here, one per "
              "line or separated by commas. Terms from the watchlists, like Flock, work too.",
              "Cities & Agencies (leave blank if you want all of them)"]
    assert subscribers.columns(header) == {"email": 2, "watchlists": 3, "terms": 4, "places": 5}
    entries, _ = read_form([header, ["", "Anonymous", "a@example.org", "tenants,Surveillance",
                                     "flock\nMeasure A", "San Jose"]], WATCHLISTS, PLACES)
    assert entries == [{"email": "a@example.org", "watchlists": ["tenants", "surveillance"], "terms": ["Flock"],
                        "custom": ["Measure A"], "places": ["San José"]}]


class Screen(io.StringIO):
    def isatty(self):
        return True


def test_the_list_never_goes_on_screen_or_into_the_repo(tmp_path, monkeypatch, capsys):
    form = tmp_path / "responses.csv"
    with form.open("w", newline="") as file:
        csv.writer(file).writerows(FORM)
    monkeypatch.setattr(subscribers, "place_names", lambda: PLACES)

    screen = Screen()
    monkeypatch.setattr(sys, "stdout", screen)
    with pytest.raises(SystemExit, match="gh secret set SUBSCRIBERS"):
        subscribers.main(["import", str(form)])
    assert screen.getvalue() == ""
    with pytest.raises(SystemExit, match="outside the repo"):
        subscribers.main(["import", str(form), "--out", str(ROOT / "subscribers.json")])
    assert not (ROOT / "subscribers.json").exists()
    monkeypatch.undo()

    subscribers.main(["import", str(form), "--out", str(tmp_path / "list.json")])
    assert [e["email"] for e in json.loads((tmp_path / "list.json").read_text())] == ["a@example.org", "b@example.org"]
    out, err = capsys.readouterr()
    assert out == "" and "2 subscribers from 4 rows." in err and "example.org" not in err
    assert "Their own terms, to consider for watchlists.yaml: Measure B" in err
