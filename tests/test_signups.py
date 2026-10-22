import csv
import http.client
import io
import json
import os
import socket
import stat
import subprocess
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode

import pytest

from needle import signups, subscribers
from needle.signups import Saved, approve, decide, fingerprint, load_list, review, save_list, take_off

WATCHLISTS = {"surveillance": {"priority": "high", "terms": ["Flock", "license plate reader"]},
              "tenants": {"priority": "normal", "terms": ["rent stabilization"]},
              "immigration": {"priority": "high", "terms": ["ICE"], "case_sensitive": ["ICE"]}}
PLACES = ["Campbell", "San José", "Santa Clara County"]
TODAY = date(2026, 10, 22)
# The titles on Drew's CryptPad form, Oct 18, 2026 (tests/test_subscribers.py).
HEADER = ["Time", "Participant", "Please enter your email address", "Pick whole categories.",
          "Want just a few terms instead? Type them here.", "Cities & Agencies (leave blank if you want all of them)"]


def row(email, watchlists="", terms="", places=""):
    return ["2026-10-20", "Anonymous", email, watchlists, terms, places]


def person(email, watchlists=(), terms=(), custom=(), places=()):
    return {"email": email, "watchlists": list(watchlists), "terms": list(terms), "custom": list(custom),
            "places": list(places), "approved": "2026-10-18"}


def export(*rows) -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows([HEADER, *rows])
    return out.getvalue().encode("utf-8-sig")


# Sorting an export against the list

def test_an_export_is_sorted_against_the_list():
    saved = Saved([person("a@example.org", ["immigration"]), person("d@example.org", ["tenants"])])
    saved.set_aside.add(fingerprint({"email": "c@example.org", "watchlists": ["tenants"]}))
    found = review(signups.read_csv(export(row("A@example.org", "Immigration"), row("b@example.org", "", "Flock"),
                                           row("c@example.org", "tenants"), row("d@example.org", "surveillance"),
                                           row("not an address", "tenants"))), saved, WATCHLISTS, PLACES)
    assert found.same == 1
    assert [e["email"] for e in found.new] == ["b@example.org"]
    assert [(old["watchlists"], new["watchlists"]) for old, new in found.changed] == [(["tenants"], ["surveillance"])]
    assert [e["email"] for e in found.set_aside] == ["c@example.org"]
    assert found.notes == ["Row 6: no usable email address; left out."]
    assert [e["email"] for e in found.pending] == ["b@example.org", "d@example.org", "c@example.org"]


def test_what_isnt_the_forms_export_says_so():
    with pytest.raises(subscribers.SubscriberError, match="No email column"):
        review([["Name", "Phone"], ["x", "y"]], Saved(), WATCHLISTS, PLACES)


def test_decisions_change_the_list_and_declined_answers_stay_aside():
    saved = Saved()
    pending = [{"email": f"{x}@example.org", "watchlists": ["tenants"], "terms": [], "custom": [], "places": []}
               for x in "abc"]
    assert decide(saved, pending, {0: "approve", 1: "decline", 2: "later"}, TODAY) == (1, 1)
    assert [(p["email"], p["approved"]) for p in saved.people] == [("a@example.org", "2026-10-22")]
    again = review(signups.read_csv(export(*[row(e["email"], "tenants") for e in pending])), saved, WATCHLISTS, PLACES)
    assert again.same == 1 and [e["email"] for e in again.set_aside] == ["b@example.org"]
    assert [e["email"] for e in again.new] == ["c@example.org"]


def test_a_change_keeps_the_persons_number_and_sets_the_old_answer_aside():
    saved = Saved([person("a@example.org", ["tenants"]), person("b@example.org", ["tenants"]),
                   person("c@example.org", ["tenants"])])
    old = dict(saved.people[1])
    approve(saved, {"email": "b@example.org", "watchlists": ["surveillance"]}, TODAY)
    assert [p["email"] for p in saved.people] == ["a@example.org", "b@example.org", "c@example.org"]
    assert saved.people[1]["watchlists"] == ["surveillance"] and fingerprint(old) in saved.set_aside
    approve(saved, {"email": "new-b@example.org", "watchlists": ["tenants"]}, TODAY, replacing="b@example.org")
    assert [p["email"] for p in saved.people] == ["a@example.org", "new-b@example.org", "c@example.org"]


def test_someone_taken_off_stays_off_at_the_next_export():
    saved = Saved([person("a@example.org", ["tenants"])])
    assert take_off(saved, "a@example.org") and saved.people == []
    found = review(signups.read_csv(export(row("a@example.org", "tenants"))), saved, WATCHLISTS, PLACES)
    assert found.new == [] and [e["email"] for e in found.set_aside] == ["a@example.org"]
    assert not take_off(saved, "a@example.org")


def test_someone_by_hand_is_checked_like_an_export():
    entry, notes = signups.person_from({"email": ["B@Example.org"], "watchlists": ["tenants"],
                                        "terms": ["flock\nMeasure A"], "places": ["San José", "Gotham"]},
                                       WATCHLISTS, PLACES)
    assert entry == {"email": "b@example.org", "watchlists": ["tenants"], "terms": ["Flock"], "custom": ["Measure A"],
                     "places": ["San José"]}
    assert notes == ['"Gotham" isn\'t a city or agency the app reads; left out.']
    assert signups.person_from({"email": ["nope"], "watchlists": ["tenants"]}, WATCHLISTS, PLACES) == (
        None, ["Give their email address."])
    assert signups.person_from({"email": ["b@example.org"]}, WATCHLISTS, PLACES) == (
        None, ["Pick at least one watchlist or term."])


def test_the_secret_is_what_the_import_command_makes():
    entries, _ = subscribers.read_form([HEADER, row("a@example.org", "immigration", "Flock\nMeasure B")],
                                       WATCHLISTS, PLACES)
    text = signups.secret_text([{**entries[0], "approved": "2026-10-22"}])
    assert text == json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    roster = subscribers.load(text, "k" * 64, WATCHLISTS)
    assert [p.email for p in roster.people] == ["a@example.org"]


def test_the_list_is_checked_the_way_the_run_reads_it():
    people = [person("a@example.org", ["tenants"]), person("b@example.org", ["renamed"])]
    assert signups.check(people, WATCHLISTS) == [
        "Subscriber 2 picked renamed, not a watchlist now; left out.",
        "Subscriber 2 picked nothing the app can match; left out."]


def test_the_list_file_is_yours_alone_and_outside_the_repo(tmp_path):
    path = tmp_path / "Haystack Needle" / "subscribers.json"
    saved = Saved([person("a@example.org", ["tenants"])], {"abc"}, {"digest": "d", "at": "2026-10-22T22:00:00+00:00"})
    save_list(path, saved)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_list(path) == saved and load_list(tmp_path / "none.json") == Saved()
    with pytest.raises(subscribers.SubscriberError, match="outside the repo"):
        save_list(signups.ROOT / "subscribers.json", saved)
    assert not (signups.ROOT / "subscribers.json").exists()


# GitHub, through gh

class Recorder:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls, self.result = [], (returncode, stdout, stderr)

    def __call__(self, args, input=None, **kwargs):
        self.calls.append((args, input))
        return subprocess.CompletedProcess(args, self.result[0], self.result[1], self.result[2])


def test_the_list_goes_to_gh_on_its_input_never_as_an_argument(monkeypatch):
    run = Recorder()
    github = signups.GitHub(run=run)
    monkeypatch.setattr(github, "gh", lambda: "/opt/homebrew/bin/gh")
    assert github.set_secret("SUBSCRIBERS", '[{"email":"a@example.org"}]') is None
    [(args, given)] = run.calls
    assert args == ["/opt/homebrew/bin/gh", "secret", "set", "SUBSCRIBERS"] and "example.org" in given
    run.result = (1, "", "HTTP 403: Resource not accessible\n")
    assert github.set_secret("SUBSCRIBERS", "[]") == "GitHub didn't take it: HTTP 403: Resource not accessible"


def test_it_reads_when_the_secrets_were_set(monkeypatch):
    run = Recorder(stdout='[{"name":"SUBSCRIBERS","updatedAt":"2026-10-18T20:22:38Z"}]')
    github = signups.GitHub(run=run)
    monkeypatch.setattr(github, "gh", lambda: "/usr/local/bin/gh")
    assert github.dates() == {"SUBSCRIBERS": datetime(2026, 10, 18, 20, 22, 38, tzinfo=timezone.utc)}
    monkeypatch.setattr(github, "gh", lambda: None)
    assert "isn't installed" in github.dates()


# The page

class FakeGitHub(signups.GitHub):
    def __init__(self, dates=None):
        super().__init__(run=None)
        self.secrets = dict(dates or {})
        self.sent = {}

    def dates(self):
        return dict(self.secrets)

    def set_secret(self, name, value):
        self.sent[name] = value
        self.secrets[name] = datetime.now(timezone.utc)
        return None


@pytest.fixture
def page(tmp_path):
    github = FakeGitHub({"SUBSCRIBERS": datetime(2026, 10, 18, 20, 22, tzinfo=timezone.utc),
                         "SUBSCRIBERS_KEY": datetime(2026, 10, 18, 20, 22, tzinfo=timezone.utc)})
    path = tmp_path / "subscribers.json"
    srv, app = signups.serve(path, github, port=0, watchlists=WATCHLISTS, places=PLACES)
    app.today = lambda: TODAY
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, app, path, github
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, host=None, origin=None, kind=None):
    port = srv.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if origin:
        headers["Origin"] = origin
    if isinstance(body, dict):
        body, kind = urlencode(body, doseq=True), "application/x-www-form-urlencoded"
    if kind:
        headers["Content-Type"] = kind
    connection.request(method, path, body, headers)
    response = connection.getresponse()
    return response.status, response.read().decode(), dict(response.getheaders())


def multipart(fields: dict, data: bytes):
    boundary = "----haystack-test"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="export"; filename="responses.csv"\r\n'
                 f"Content-Type: text/csv\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def test_the_first_time_it_says_githubs_list_came_from_elsewhere(page):
    srv, _, _, _ = page
    status, html, headers = call(srv, "GET", "/")
    assert status == 200 and "from outside this page" in html and "Nobody yet" in html
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "same-origin"  # no-referrer makes browsers send "Origin: null"
    assert "Make the key" not in html  # GitHub has it
    # Nobody's signed up yet, so it offers to empty GitHub's list too, only with the box ticked.
    assert "If nobody has signed up yet" in html and "Nobody has signed up yet. Make GitHub" in html


def test_with_no_list_anywhere_theres_nothing_to_send(tmp_path):
    srv, _ = signups.serve(tmp_path / "s.json", FakeGitHub(), port=0, watchlists=WATCHLISTS, places=PLACES)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _, html, _ = call(srv, "GET", "/")
        assert "GitHub has no list yet" in html and "Update GitHub</button>" not in html
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_export_through_the_page_then_github(page, capsys):
    srv, app, path, github = page
    body, kind = multipart({"token": app.token}, export(row("a@example.org", "tenants", "Flock", "San Jose"),
                                                          row("b@example.org", "surveillance")))
    status, html, _ = call(srv, "POST", "/import", body, kind=kind)
    assert status == 200 and "2 new" in html and "San José" in html
    code = app.reviews and next(iter(app.reviews))
    status, html, _ = call(srv, "POST", "/decide", {"token": app.token, "once": "o1", "review": code,
                                                    "decide-0": "approve", "decide-1": "later"})
    assert "Approved 1, declined 0. Update GitHub when you&#39;re done." in html and "1. a@example.org" in html
    assert [p["email"] for p in load_list(path).people] == ["a@example.org"] and github.sent == {}

    status, html, _ = call(srv, "POST", "/send", {"token": app.token, "once": "o2"})
    assert "GitHub has the list: 1 person." in html and "GitHub has this list." in html
    assert json.loads(github.sent["SUBSCRIBERS"]) == [{"email": "a@example.org", "watchlists": ["tenants"],
                                                       "terms": ["Flock"], "custom": [], "places": ["San José"]}]
    assert "example.org" not in capsys.readouterr().out  # nothing about anyone in what it prints
    assert load_list(path).sent["digest"] == signups.digest_of(github.sent["SUBSCRIBERS"])


def test_links_carry_a_code_not_an_address(page):
    srv, app, path, _ = page
    save_list(path, Saved([person("a@example.org", ["tenants"])]))
    _, html, _ = call(srv, "GET", "/")
    assert "/person?who=" in html and "email=a%40" not in html and "who=a@" not in html
    _, html, _ = call(srv, "GET", f"/person?who={app.who('a@example.org')}")
    assert "Change a@example.org" in html and 'value="tenants" checked' in html
    _, html, _ = call(srv, "GET", "/person?who=0000000000000000")
    assert "isn&#39;t on the list now" in html


def test_changing_and_taking_off_by_hand(page):
    srv, app, path, _ = page
    save_list(path, Saved([person("a@example.org", ["tenants"]), person("b@example.org", ["tenants"])]))
    _, html, _ = call(srv, "POST", "/person", {"token": app.token, "once": "p1", "was": "a@example.org",
                                               "email": "a@example.org", "watchlists": ["immigration"], "terms": ""})
    assert "Changed a@example.org." in html and load_list(path).people[0]["watchlists"] == ["immigration"]
    _, html, _ = call(srv, "POST", "/person", {"token": app.token, "once": "p2", "was": "a@example.org",
                                               "email": "b@example.org", "watchlists": ["tenants"]})
    assert "is on the list already" in html  # the form again, with what to fix
    _, html, _ = call(srv, "POST", "/remove", {"token": app.token, "once": "p3", "email": "b@example.org"})
    assert "Took b@example.org off the list." in html
    assert [p["email"] for p in load_list(path).people] == ["a@example.org"]


def test_an_empty_list_goes_only_when_you_say_so(page):
    srv, app, _, github = page
    _, html, _ = call(srv, "POST", "/send", {"token": app.token, "once": "e1"})
    assert "take everyone off" in html and github.sent == {}
    _, html, _ = call(srv, "POST", "/send", {"token": app.token, "once": "e2", "everyone": "yes"})
    assert github.sent == {"SUBSCRIBERS": "[]"}
    # Now the two match, so the warning and the button are gone.
    assert "GitHub has this list." in html and "from outside this page" not in html
    assert "Update GitHub</button>" not in html


def test_each_form_saves_once(page):
    srv, app, path, _ = page
    form = {"token": app.token, "once": "same", "email": "c@example.org", "watchlists": ["tenants"]}
    call(srv, "POST", "/person", form)
    _, html, _ = call(srv, "POST", "/remove", {**form, "email": "c@example.org"})
    assert "saved already" in html and [p["email"] for p in load_list(path).people] == ["c@example.org"]


def test_the_key_is_made_only_when_github_has_none(page):
    srv, app, _, github = page
    _, html, _ = call(srv, "POST", "/key", {"token": app.token, "once": "k1"})
    assert "has the key already" in html and github.sent == {}
    del github.secrets["SUBSCRIBERS_KEY"]
    app.asked = (None, -signups.ASK_GITHUB_EVERY)
    _, html, _ = call(srv, "GET", "/")
    assert "Make the key" in html
    _, html, _ = call(srv, "POST", "/key", {"token": app.token, "once": "k2"})
    assert len(github.sent["SUBSCRIBERS_KEY"]) == 64 and "Make the key" not in html


def test_another_site_cant_change_anything(page):
    srv, app, path, _ = page
    good = {"token": app.token, "once": "z", "email": "c@example.org", "watchlists": ["tenants"]}
    assert call(srv, "POST", "/person", {**good, "token": "wrong"})[0] == 403
    assert call(srv, "POST", "/person", good, host="evil.example")[0] == 403  # DNS rebinding
    assert call(srv, "POST", "/person", good, origin="https://evil.example")[0] == 403
    assert call(srv, "GET", "/", host="evil.example")[0] == 403
    assert not path.exists()


# What the Haystack Needle app runs

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def other_program():
    srv = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_it_knows_the_page_from_other_programs(page, other_program):
    srv, _, _, _ = page
    assert signups.running(srv.server_address[1])
    assert not signups.running(other_program) and not signups.running(free_port())


def test_the_app_opens_the_page_thats_already_running(page, tmp_path, capsys):
    srv, _, _, _ = page
    opened = []
    assert signups.run_for_app(os.getpid(), tmp_path / "x.json", port=srv.server_address[1],
                               open_page=opened.append) == 0
    assert opened == [f"http://127.0.0.1:{srv.server_address[1]}/"] and "already running" in capsys.readouterr().out


def test_the_page_stops_when_the_app_quits(tmp_path):
    fake_app = subprocess.Popen(["sleep", "30"])
    port, opened, out = free_port(), [], {}
    thread = threading.Thread(target=lambda: out.setdefault("code", signups.run_for_app(
        fake_app.pid, tmp_path / "subscribers.json", port=port, open_page=opened.append, every=0.05,
        github=FakeGitHub(), watchlists=WATCHLISTS, places=PLACES)), daemon=True)
    thread.start()
    for _ in range(100):
        if signups.running(port):
            break
        time.sleep(0.05)
    assert signups.running(port) and opened == [f"http://127.0.0.1:{port}/"]
    fake_app.kill()
    fake_app.wait()
    thread.join(5)
    assert not thread.is_alive() and out["code"] == 0 and not signups.running(port)


def test_the_app_says_what_went_wrong(tmp_path, other_program, capsys):
    kwargs = {"github": FakeGitHub(), "watchlists": WATCHLISTS, "places": PLACES}
    assert signups.run_for_app(os.getpid(), tmp_path / "s.json", port=other_program, open_page=print, **kwargs) == 1
    assert capsys.readouterr().out.startswith(f"Problem: Another program on this Mac is using port {other_program}")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert signups.run_for_app(os.getpid(), broken, port=free_port(), open_page=print, **kwargs) == 1
    assert capsys.readouterr().out.startswith("Problem: The subscriber list on this Mac")
