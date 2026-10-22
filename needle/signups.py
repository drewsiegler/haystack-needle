"""The Subscribers page: approving sign-ups from the CryptPad form, and sending
the list to GitHub, in your browser instead of the terminal.

    python -m needle.signups               # opens it in your browser
    python -m needle.signups --no-browser  # prints its address instead

Export the form's answers from CryptPad as CSV and pick the file on the page.
It shows who's new and whose picks changed, and you approve or decline each.
The people you approve are kept in a file on this Mac, outside every repo
(LIST), so each export asks only about what's new; answers you decline, and
people you take off, stay set aside so the next export doesn't ask again.
You can also add, change or take off someone by hand.

"Update GitHub" sends the list to the SUBSCRIBERS secret with GitHub's own
command-line tool, gh, as `python -m needle.subscribers import FILE | gh
secret set SUBSCRIBERS` does: straight from this page to GitHub, never on
screen. The next run emails anyone new and forgets anyone taken off.

Like PRA Tracker's form, it listens on 127.0.0.1 only, which no other computer
can reach, answers only to its own address, needs a code from its own pages to
change anything, and loads nothing from elsewhere. Addresses appear on its
pages, in the list file and in the secret, and nowhere else: never in what it
prints, and never in the repo.

The Haystack Needle app (packaging/macos/) runs it with --app: see run_for_app.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import http.client
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from needle import subscribers
from needle.subscribers import SubscriberError, fold

ROOT = subscribers.ROOT
TEMPLATES = Path(__file__).parent / "templates" / "signups"
LIST = Path.home() / "Library" / "Application Support" / "Haystack Needle" / "subscribers.json"
NAME = "Haystack Needle subscribers page"  # its Server header, which tells it apart from other programs
PORT = 8738  # PRA Tracker's form has 8737
IDLE_SECONDS = 20  # a browser's spare connection that sends nothing is dropped after this
MAX_UPLOAD = 5_000_000  # bytes
FIELDS = ("watchlists", "terms", "custom", "places")
ASK_GITHUB_EVERY = 300  # seconds between asking GitHub when the secrets were last set
SENT_SLACK = timedelta(minutes=2)  # GitHub's date for a secret this page set can trail its own clock


def entry_of(person: dict) -> dict:
    """A person as the SUBSCRIBERS secret has them."""
    return {"email": person["email"], **{name: list(person.get(name) or []) for name in FIELDS}}


def fingerprint(person: dict) -> str:
    """Stands for one answer (an address and its picks) in the set-aside list."""
    return hashlib.sha256(json.dumps(entry_of(person), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def same_picks(a: dict, b: dict) -> bool:
    return all({fold(x) for x in a.get(name) or []} == {fold(x) for x in b.get(name) or []} for name in FIELDS)


def secret_text(people: list[dict]) -> str:
    """The SUBSCRIBERS secret: the same JSON `needle.subscribers import` makes."""
    return json.dumps([entry_of(p) for p in people], ensure_ascii=False, separators=(",", ":"))


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# The list on this Mac


@dataclass
class Saved:
    people: list[dict] = field(default_factory=list)  # entry_of(...) plus "approved", a date
    set_aside: set[str] = field(default_factory=set)  # fingerprints of answers declined or people taken off
    sent: dict | None = None  # {"digest": digest_of(what was sent), "at": when, ISO in UTC}


def sent_as_is(saved: Saved) -> bool:
    """Whether GitHub has the list as it is here, as far as this page knows."""
    return bool(saved.sent) and saved.sent["digest"] == digest_of(secret_text(saved.people))


def load_list(path: Path) -> Saved:
    if not Path(path).exists():
        return Saved()
    data = json.loads(Path(path).read_text())
    return Saved([dict(p) for p in data.get("people") or []], set(data.get("set_aside") or []), data.get("sent"))


def save_list(path: Path, saved: Saved) -> None:
    """Writes it readable by you alone, and whole or not at all."""
    path = Path(path)
    if path.resolve().is_relative_to(ROOT):
        raise SubscriberError("The list has to live outside the repo, so it's never committed.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"people": saved.people, "set_aside": sorted(saved.set_aside), "sent": saved.sent}
    temporary = path.with_name(path.name + ".new")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
        json.dump(data, file, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def check(people: list[dict], watchlists: dict) -> list[str]:
    """What the run would say about the list, by subscriber number: someone whose
    watchlist has since been renamed, say."""
    notes: list[str] = []
    if people:
        subscribers.load(secret_text(people), "0" * subscribers.MIN_KEY, watchlists, warn=notes.append)
    return notes


# Reading an export, and the decisions on it


@dataclass
class Review:
    new: list[dict] = field(default_factory=list)
    changed: list[tuple[dict, dict]] = field(default_factory=list)  # (on the list, from the form)
    set_aside: list[dict] = field(default_factory=list)  # answers declined or taken off before
    same: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[dict]:
        """The answers to decide on, in the order the page numbers them."""
        return self.new + [new for _, new in self.changed] + self.set_aside


def read_csv(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig", "replace"))))


def review(rows: list[list[str]], saved: Saved, watchlists: dict, places: list[str]) -> Review:
    """Sorts an export's answers against the list. SubscriberError if it isn't one."""
    entries, notes = subscribers.read_form(rows, watchlists, places)
    by_email = {p["email"]: p for p in saved.people}
    out = Review(notes=notes)
    for entry in entries:
        old = by_email.get(entry["email"])
        if old and same_picks(old, entry):
            out.same += 1
        elif fingerprint(entry) in saved.set_aside:
            out.set_aside.append(entry)
        elif old:
            out.changed.append((old, entry))
        else:
            out.new.append(entry)
    return out


def approve(saved: Saved, entry: dict, today: date, replacing: str | None = None) -> None:
    """Puts entry on the list in place of the same address (or of `replacing`,
    when a change gives someone a new one), keeping their number; a new person
    goes at the end. What they had is set aside, so an export showing their old
    answer doesn't ask again."""
    person = {**entry_of(entry), "approved": today.isoformat()}
    emails = {entry["email"], replacing} - {None}
    spot = next((n for n, p in enumerate(saved.people) if p["email"] in emails), len(saved.people))
    for old in saved.people:
        if old["email"] in emails and not same_picks(old, entry):
            saved.set_aside.add(fingerprint(old))
    saved.people = [p for p in saved.people if p["email"] not in emails]
    saved.people.insert(spot, person)
    saved.set_aside.discard(fingerprint(entry))


def decide(saved: Saved, pending: list[dict], choices: dict[int, str], today: date) -> tuple[int, int]:
    """Applies the review page's choices; (approved, declined)."""
    approved = declined = 0
    for n, entry in enumerate(pending):
        if choices.get(n) == "approve":
            approve(saved, entry, today)
            approved += 1
        elif choices.get(n) == "decline":
            saved.set_aside.add(fingerprint(entry))
            declined += 1
    return approved, declined


def take_off(saved: Saved, email: str) -> bool:
    """Takes someone off, setting their answer aside so an export doesn't bring it back."""
    for person in saved.people:
        if person["email"] == email:
            saved.people.remove(person)
            saved.set_aside.add(fingerprint(person))
            return True
    return False


def person_from(fields: dict, watchlists: dict, places: list[str]) -> tuple[dict | None, list[str]]:
    """The person the Add or Change form describes, checked the way an export
    is; or None and what to fix."""
    email = one(fields, "email")
    terms = "\n".join((fields.get("terms") or [""])[0].splitlines())
    rows = [["Email", "Watchlists", "Terms", "Places"],
            [email, ";".join(many(fields, "watchlists")), terms, ";".join(many(fields, "places"))]]
    entries, notes = subscribers.read_form(rows, watchlists, places)
    notes = [re.sub(r"^Row \d+: ", "", note) for note in notes]
    if not entries:
        if not subscribers._EMAIL.fullmatch(email.lower()):
            return None, ["Give their email address."]
        return None, ["Pick at least one watchlist or term."]
    return entries[0], notes


# GitHub


class GitHub:
    """The repo's secrets, through gh, GitHub's command-line tool, signed in as you.
    run is subprocess.run, or a stand-in for tests."""

    def __init__(self, root: Path = ROOT, run=subprocess.run):
        self.root, self.run = Path(root), run

    def gh(self) -> str | None:
        # An app opened from Finder has a short PATH, without Homebrew's folder.
        return shutil.which("gh", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin",
                                                        "/usr/local/bin"]))

    def _call(self, *args: str, text: str | None = None) -> subprocess.CompletedProcess | str:
        gh = self.gh()
        if gh is None:
            return ("GitHub's command-line tool, gh, isn't installed, so the page can't reach GitHub. "
                    "Install it (brew install gh), run gh auth login once, and try again.")
        try:
            return self.run([gh, *args], input=text, capture_output=True, text=True, timeout=60, cwd=self.root)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"Couldn't run gh: {error.__class__.__name__}."

    @staticmethod
    def said(result: subprocess.CompletedProcess) -> str:
        lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
        return lines[-1] if lines else f"gh stopped ({result.returncode})"

    def dates(self) -> dict[str, datetime] | str:
        """When each secret was last set, by name; or why it couldn't ask."""
        result = self._call("secret", "list", "--json", "name,updatedAt")
        if isinstance(result, str):
            return result
        if result.returncode:
            return f"Couldn't ask GitHub about the secrets: {self.said(result)}"
        try:
            return {s["name"]: datetime.fromisoformat(s["updatedAt"].replace("Z", "+00:00"))
                    for s in json.loads(result.stdout)}
        except (ValueError, KeyError, TypeError):
            return "Couldn't read GitHub's answer about the secrets."

    def set_secret(self, name: str, value: str) -> str | None:
        """None once set; else why not. The value goes to gh on its input, never as an argument."""
        result = self._call("secret", "set", name, text=value)
        if isinstance(result, str):
            return result
        return None if result.returncode == 0 else f"GitHub didn't take it: {self.said(result)}"


# The page


def one(fields: dict, name: str) -> str:
    """A field's value on one line, or ""."""
    return " ".join(str((fields.get(name) or [""])[0])[:2000].split())


def many(fields: dict, name: str) -> list[str]:
    return [" ".join(v.split()) for v in fields.get(name) or [] if v.strip()]


def when(moment: datetime | str | None) -> str:
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    if moment is None:
        return ""
    local = moment.astimezone()
    return f"{local:%a %b} {local.day} at {local:%-I:%M %p}".replace("AM", "a.m.").replace("PM", "p.m.")


_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(default=True),
                   trim_blocks=True, lstrip_blocks=True)
_env.filters["when"] = when


@dataclass
class App:
    list_path: Path
    github: GitHub
    watchlists: dict
    places: list[str]
    today: object = date.today  # a function giving today's date
    token: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    used: set = field(default_factory=set)  # each form's one-time code, once saved
    reviews: dict = field(default_factory=dict)  # an export's answers to decide on, by its code
    lock: object = field(default_factory=threading.RLock)  # one change to the list (or GitHub) at a time
    asked: tuple = (None, -ASK_GITHUB_EVERY)  # (GitHub's secrets' dates, or why not; when, by time.monotonic())

    def page(self, name: str, **context) -> str:
        return _env.get_template(name).render(token=self.token, once=secrets.token_urlsafe(12), who=self.who,
                                              watchlists=self.watchlists, places=self.places, **context)

    def who(self, email: str) -> str:
        """Stands for someone in a link, so no address goes in the browser's history."""
        return hmac.new(self.token.encode(), email.encode(), hashlib.sha256).hexdigest()[:16]

    def email_for(self, code: str) -> str:
        with self.lock:
            saved = load_list(self.list_path)
        return next((p["email"] for p in saved.people if hmac.compare_digest(self.who(p["email"]), code)), "")

    def github_dates(self, fresh: bool = False):
        if fresh or time.monotonic() - self.asked[1] >= ASK_GITHUB_EVERY:
            self.asked = (self.github.dates(), time.monotonic())
        return self.asked[0]

    def status(self, saved: Saved) -> dict:
        """Where the list stands against GitHub's copy, for the top of the page."""
        dates = self.github_dates()
        problem = dates if isinstance(dates, str) else None
        dates = {} if problem else dates
        github_set, sent_at = dates.get("SUBSCRIBERS"), saved.sent and datetime.fromisoformat(saved.sent["at"])
        return {"problem": problem, "github_set": github_set, "has_key": problem is not None or
                "SUBSCRIBERS_KEY" in dates, "sent_at": sent_at,
                "current": sent_as_is(saved),
                "elsewhere": bool(github_set) and (sent_at is None or github_set > sent_at + SENT_SLACK)}

    def home(self, notice: str | None = None, problems: list[str] | None = None) -> str:
        with self.lock:
            saved = load_list(self.list_path)
        return self.page("home.html.j2", saved=saved, status=self.status(saved), notice=notice,
                         problems=problems or [], checks=check(saved.people, self.watchlists),
                         list_path=self.list_path)

    def person_form(self, email: str = "", fields: dict | None = None, problems: list[str] | None = None) -> str:
        with self.lock:
            saved = load_list(self.list_path)
        person = next((p for p in saved.people if p["email"] == email), None)
        if email and person is None and fields is None:
            return self.home(f"{email} isn't on the list.")
        if fields is None and person:
            fields = {"email": [person["email"]], "watchlists": person["watchlists"],
                      "terms": ["\n".join(person["terms"] + person["custom"])], "places": person["places"]}
        return self.page("person.html.j2", was=email, fields=fields or {}, problems=problems or [],
                         one=one, many=many)

    def read_export(self, data: bytes | None) -> str:
        if not data:
            return self.home(problems=["Pick the CSV file you exported from CryptPad, then press Read it."])
        with self.lock:
            saved = load_list(self.list_path)
        try:
            found = review(read_csv(data), saved, self.watchlists, self.places)
        except SubscriberError as error:
            return self.home(problems=[f"That doesn't look like the form's export: {error}"])
        code = secrets.token_urlsafe(12)
        self.reviews = {code: found}  # the latest export only
        return self.page("review.html.j2", review=found, code=code)

    def change(self, action: str, fields: dict) -> str:
        """A save from one of the page's forms; the page to show next. Each form
        is saved once: sending it again (reloading after) changes nothing."""
        with self.lock:
            once = one(fields, "once")
            if not once or once in self.used:
                return self.home("That was saved already, so nothing changed.")
            saved = load_list(self.list_path)
            today = self.today()
            if action == "decide":
                found = self.reviews.get(one(fields, "review"))
                if found is None:
                    return self.home(problems=["That export was read before the page restarted. Pick the file again."])
                choices = {int(k.removeprefix("decide-")): v[0] for k, v in fields.items()
                           if re.fullmatch(r"decide-\d+", k) and v}
                approved, declined = decide(saved, found.pending, choices, today)
                notice = f"Approved {approved}, declined {declined}."
            elif action == "person":
                entry, problems = person_from(fields, self.watchlists, self.places)
                was = one(fields, "was").lower()
                if entry and entry["email"] != was and any(p["email"] == entry["email"] for p in saved.people):
                    entry, problems = None, [f"{entry['email']} is on the list already: change them there."]
                if entry is None:
                    return self.person_form(was, fields, problems)
                approve(saved, entry, today, replacing=was or None)
                notice = f"{'Changed' if was else 'Added'} {entry['email']}." + (" " + " ".join(problems) if problems else "")
            elif action == "remove":
                email = one(fields, "email").lower()
                if not take_off(saved, email):
                    return self.home(f"{email} isn't on the list.")
                notice = f"Took {email} off the list."
            elif action == "send":
                text = secret_text(saved.people)
                if not saved.people and one(fields, "everyone") != "yes":
                    return self.home(problems=["The list here is empty, so updating would take everyone off "
                                               "GitHub's list. Tick the box to say that's what you want."])
                problem = self.github.set_secret("SUBSCRIBERS", text)
                if problem:
                    return self.home(problems=[problem])
                saved.sent = {"digest": digest_of(text), "at": datetime.now(timezone.utc).isoformat()}
                self.asked = (None, -ASK_GITHUB_EVERY)  # ask GitHub again
                notice = (f"GitHub has the list: {len(saved.people)} "
                          f"{'person' if len(saved.people) == 1 else 'people'}. The next run emails anyone new.")
            elif action == "key":
                dates = self.github_dates(fresh=True)
                if isinstance(dates, str):
                    return self.home(problems=[dates])
                if "SUBSCRIBERS_KEY" in dates:
                    return self.home("GitHub has the key already, so nothing changed.")
                problem = self.github.set_secret("SUBSCRIBERS_KEY", secrets.token_hex(32))
                if problem:
                    return self.home(problems=[problem])
                self.asked = (None, -ASK_GITHUB_EVERY)
                notice = "Made the key and gave it to GitHub. Keep it from now on."
                self.used.add(once)
                return self.home(notice)
            else:
                return self.home()
            save_list(self.list_path, saved)
            self.used.add(once)
            if not sent_as_is(saved):
                notice += " Update GitHub when you're done."
        return self.home(notice)


def uploaded(content_type: str, body: bytes) -> tuple[dict, bytes | None]:
    """A multipart form's fields (as parse_qs gives them) and its file."""
    message = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    fields: dict = {}
    data = None
    if message.is_multipart():
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True) or b""
            if part.get_filename() is not None:
                data = payload or data
            elif name:
                fields.setdefault(name, []).append(payload.decode("utf-8", "replace"))
    return fields, data


def handler_for(app: App, port_of):
    class Handler(BaseHTTPRequestHandler):
        server_version = NAME
        timeout = IDLE_SECONDS

        def log_message(self, format, *args):  # keep the terminal quiet
            pass

        def allowed(self) -> bool:
            port = port_of()
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://{h}" for h in hosts}

        def send(self, status: int, body: str = "") -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            # Not no-referrer: then browsers send "Origin: null" with the page's own forms.
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.allowed():
                return self.send(403, "Open the page at its own address.")
            url = urlsplit(self.path)
            try:
                if url.path == "/":
                    self.send(200, app.home())
                elif url.path == "/person":
                    code = one(parse_qs(url.query), "who")
                    email = app.email_for(code) if code else ""
                    self.send(200, app.home("That person isn't on the list now.") if code and not email
                              else app.person_form(email))
                else:
                    self.send(404, "Not here.")
            except (OSError, ValueError, SubscriberError) as error:
                self.send(500, app.page("error.html.j2", problem=str(error)))

        def do_POST(self):
            if not self.allowed():
                return self.send(403, "Open the page at its own address.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                return self.send(413, "That file is too big to be the form's export.")
            body = self.rfile.read(length)
            kind = self.headers.get("Content-Type") or ""
            if kind.startswith("multipart/form-data"):
                fields, data = uploaded(kind, body)
            else:
                fields, data = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True), None
            if not secrets.compare_digest(one(fields, "token"), app.token):
                return self.send(403, "That page is from an earlier session. Reload it and try again.")
            path = urlsplit(self.path).path.strip("/")
            try:
                if path == "import":
                    self.send(200, app.read_export(data))
                elif path in ("decide", "person", "remove", "send", "key"):
                    self.send(200, app.change(path, fields))
                else:
                    self.send(404, "Not here.")
            except (OSError, ValueError, SubscriberError) as error:
                self.send(500, app.page("error.html.j2", problem=str(error)))

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True  # stopping doesn't wait for a browser's open connections


def serve(list_path: Path = LIST, github: GitHub | None = None, port: int = PORT, fallback: bool = True,
          watchlists: dict | None = None, places: list[str] | None = None) -> tuple[Server, App]:
    """The page's server, not yet serving. Port 0 picks a free one, and so
    does a port that's taken, unless fallback is off (then OSError)."""
    if watchlists is None:
        watchlists = yaml.safe_load((ROOT / "watchlists.yaml").read_text())
    app = App(Path(list_path), github or GitHub(), watchlists,
              places if places is not None else subscribers.place_names())
    holder: dict = {}
    try:
        server = Server(("127.0.0.1", port), handler_for(app, lambda: holder["port"]))
    except OSError:
        if not fallback:
            raise
        server = Server(("127.0.0.1", 0), handler_for(app, lambda: holder["port"]))
    holder["port"] = server.server_address[1]
    return server, app


def address(server: Server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/"


def running(port: int = PORT) -> bool:
    """Whether the page is already answering on the port, from the app or a
    terminal. It asks only for the server's name, so it changes nothing."""
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("HEAD", "/")
        name = connection.getresponse().getheader("Server") or ""
        connection.close()
    except (OSError, http.client.HTTPException):
        return False
    return name.startswith(NAME)


def _watch(pid: int, stop: threading.Event, every: float) -> None:
    """Sets stop once process pid is gone: the app quit, or was force-quit."""
    while not stop.wait(every):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            stop.set()
        except PermissionError:  # it's there, but another user's
            pass


def run_for_app(app_pid: int, list_path: Path = LIST, port: int = PORT, open_page=None,
                stop: threading.Event | None = None, every: float = 1.0, **kwargs) -> int:
    """What the Haystack Needle app runs (`python -m needle.signups --app PID`):
    the page, but only on port, so the app's Dock icon can always reopen it. If
    the page is already running there, it opens that one instead. It stops when
    the app quits (or process app_pid is gone), finishing a change under way
    first. What went wrong goes on a line starting "Problem: ", which the app
    shows in a message box. Returns the exit code."""
    import webbrowser

    open_page = open_page or webbrowser.open
    stop = stop or threading.Event()
    url = f"http://127.0.0.1:{port}/"
    if running(port):
        print("The page was already running, so the app opened it.", flush=True)
        open_page(url)
        return 0
    try:
        load_list(list_path)
    except (OSError, ValueError) as error:
        print(f"Problem: The subscriber list on this Mac ({list_path}) couldn't be read: "
              f"{error.__class__.__name__}. If you changed it by hand, undo that, then open Haystack Needle again.",
              flush=True)
        return 1
    try:
        server, app = serve(list_path, port=port, fallback=False, **kwargs)
    except OSError:
        print(f"Problem: Another program on this Mac is using port {port}, which the page needs. Quit that "
              "program (or restart the Mac), then open Haystack Needle again.", flush=True)
        return 1
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):  # the app's Quit sends SIGTERM
            signal.signal(signum, lambda *_: stop.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_watch, args=(app_pid, stop, every), daemon=True).start()
    print(f"The Subscribers page is at {url}", flush=True)
    open_page(url)
    stop.wait()
    with app.lock:  # a change under way finishes first, and none starts after
        server.shutdown()
        server.server_close()
    print("Stopped the page.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> None:
    import webbrowser

    parser = argparse.ArgumentParser(prog="python -m needle.signups", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", type=Path, default=LIST, help=f"the list on this Mac (default: {LIST})")
    parser.add_argument("--no-browser", action="store_true", help="print the address instead of opening it")
    parser.add_argument("--port", type=int, default=PORT, help=f"the port on 127.0.0.1 (default: {PORT})")
    parser.add_argument("--app", type=int, metavar="PID",
                        help="how the Haystack Needle app runs it: stop when process PID does (packaging/macos/)")
    args = parser.parse_args(argv)
    if args.list.resolve().is_relative_to(ROOT):
        sys.exit("Keep the list outside the repo, so it's never committed.")
    if args.app:
        sys.exit(run_for_app(args.app, args.list, port=args.port))
    url = f"http://127.0.0.1:{args.port}/"
    if running(args.port):
        print(f"The page is already running at {url} (in the Haystack Needle app or another terminal), so this opens it.")
        if not args.no_browser:
            webbrowser.open(url)
        return
    server, _ = serve(args.list, port=args.port)
    url = address(server)
    print(f"The Subscribers page is at {url} (only this computer can open it). Press Ctrl-C to stop it.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped the page.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
