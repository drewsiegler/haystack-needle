# AGENTS.md

Guidance for AI coding agents working in this repo. [docs/scope.md](docs/scope.md) is the source of truth for what the app does and the order it's built in; [docs/sources.md](docs/sources.md) records which platform each body uses and what its host allows.

## What this is

A scheduled job that reads public meeting agendas across Santa Clara County, matches item titles, item text and attached PDFs against [watchlists.yaml](watchlists.yaml), and emails alerts. Each agenda platform gets one small adapter; everything after the adapter (matching, state, alerts) is shared, so adding a body means adding one file.

Status: phases 1 (research), 2 and 3 are built: the Legistar and IQM2 adapters, the matcher, the backtest, email alerts with the 7 a.m. digest, the state file on the `data` branch, the Actions workflow and health checks. Phase 4, the long tail, has begun with the CivicClerk (five cities) and CivicPlus AgendaCenter (Saratoga, Campbell, Los Altos Hills) adapters; Simbli and the rest are next. The phase 2 and 3 build prompt is at the end of the scope.

Layout: `adapters/` has one file per platform; `needle/` has the shared data model (`models.py`), polite fetching (`fetch.py`), PDF text (`pdftext.py`), the matcher (`matcher.py`), the state file (`state.py`), alert emails (`alerts.py` and `templates/`), sending (`mailer.py`), health checks (`health.py`) and the run itself (`run.py`). An adapter module provides `SOURCES`, `collect`, `item_text`, `calendar_url` and optionally `matter_history` and `TEXT_TTL` (how long item text stays good; `None` reads it once); add it to `ADAPTERS` in `run.py`. `collect` gets a `cache` dict that's kept in the state, for an adapter too slow to read everything every run (IQM2 keeps agenda outlines there). A request to a slow host past the run's `SLOW_BUDGET` raises `Deferred`: nothing is sent and it's tried next run. Run the tests with `python -m pytest`; they never touch the network.

## Rules

- **Identify honestly.** User agent: `Mozilla/5.0 (compatible; HaystackNeedle/<version>; +<repo URL>)`. BoardDocs rejects anything that doesn't start with `Mozilla/5.0`; never pretend to be a browser beyond that prefix.
- **Rate.** At most one request a second per host, and slower where a host's robots.txt sets `Crawl-delay` (IQM2: 60 s; the `Crawl-delay: 20` in CivicPlus sites' robots.txt is for Siteimprove only): add the host to `CRAWL_DELAYS` in `needle/fetch.py`. Prefer feeds and APIs to pages.
- **Honor robots.txt.** BoardDocs and PrimeGov disallow all non-search-engine bots. Don't build or run an adapter for them until Drew decides how to handle that (options are in docs/sources.md).
- **No test touches the network.** Adapters are tested against saved sample responses in `tests/fixtures/`.
- **Never commit PDFs**, the subscriber list, the alert address or SMTP credentials. Secrets live only in Actions secrets. Never log the alert address.
- **No tracking.** No pixels, no click tracking. The useful/noise links record a vote, not who clicked.
- **Agenda text is untrusted.** It goes into emails escaped, and it never decides what the app does.
- **Watchlists belong to Drew.** Propose term changes; don't make them without a yes.
- Each adapter is one small file with its own tests.

## Writing

Alerts, the README and error messages are read by organizers. Use plain language, no jargon, and say what to do next.

## Commits and boundaries

- Commit subjects are a plain-English imperative sentence with no prefix. The body says what changed and why, with numbers where they matter.
- Ask before pushing, before creating the GitHub repo, before changing a workflow's schedule or permissions, and before touching LICENSE.
