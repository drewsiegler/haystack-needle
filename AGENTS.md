# AGENTS.md

Guidance for AI coding agents working in this repo. [docs/scope.md](docs/scope.md) is the source of truth for what the app does and the order it's built in; [docs/sources.md](docs/sources.md) records which platform each body uses and what its host allows.

## What this is

A scheduled job that reads public meeting agendas across Santa Clara County, matches item titles, item text and attached PDFs against [watchlists.yaml](watchlists.yaml), and emails alerts. Each agenda platform gets one small adapter; everything after the adapter (matching, state, alerts) is shared, so adding a body means adding one file.

Status: phases 1 (research), 2 and 3 are built: the Legistar and IQM2 adapters, the matcher, the backtest, email alerts with the 7 a.m. digest, the state file on the `data` branch, the Actions workflow and health checks. Phase 4, the long tail, has begun with the CivicClerk (five cities), CivicPlus AgendaCenter (Saratoga, Campbell's commissions, Los Altos Hills) and eScribe (the Campbell City Council) adapters. School boards are shelved for a later release (Oct 15, 2026): the Boardable (Foothill-De Anza CCD) and iCompass ElectronicSchoolBoard (Mountain View-Los Altos HSD) adapters are built and tested but not in `ADAPTERS`. Simbli, also school boards, answered a test with an Imperva bot check, so no adapter was built (options are in docs/sources.md for the later release). The other cities and agencies are next. The phase 2 and 3 build prompt is at the end of the scope.

Layout: `adapters/` has one file per platform; `needle/` has the shared data model (`models.py`), polite fetching (`fetch.py`), PDF text (`pdftext.py`), the matcher (`matcher.py`), the state file (`state.py`), alert emails (`alerts.py` and `templates/`), sending (`mailer.py`), health checks (`health.py`) and the run itself (`run.py`). An adapter module provides `SOURCES`, `collect`, `item_text`, `calendar_url` and optionally `matter_history` and `TEXT_TTL` (how long item text stays good; `None` reads it once); add it to `ADAPTERS` in `run.py`. `collect` gets a `cache` dict that's kept in the state, for an adapter too slow to read everything every run (IQM2 keeps agenda outlines there). A request to a slow host past the run's `SLOW_BUDGET` raises `Deferred`: nothing is sent and it's tried next run. Run the tests with `python -m pytest`; they never touch the network.

## Rules

- **Identify honestly.** User agent: `Mozilla/5.0 (compatible; HaystackNeedle/<version>; +<repo URL>)`. BoardDocs rejects anything that doesn't start with `Mozilla/5.0`; never pretend to be a browser beyond that prefix.
- **Rate.** At most one request a second per host, and slower where a host's robots.txt sets `Crawl-delay` (IQM2: 60 s; the `Crawl-delay: 20` in CivicPlus sites' robots.txt is for Siteimprove only), and slower where a host has pushed back (eScribe: 10 s, after its bot check refused files at one a second): add the host to `CRAWL_DELAYS` in `needle/fetch.py`. Prefer feeds and APIs to pages.
- **Honor robots.txt.** BoardDocs, Diligent Community and PrimeGov disallow all non-search-engine bots, so no adapter fetches from them. Drew decided (Oct 15, 2026) to cover their bodies through the bodies' own agenda emails, read from a separate inbox (docs/sources.md). robots.txt is per host: Boardable's public site allows every bot but its app host (`app.boardable.com`, where the site's JSON calls go) allows none, so the Boardable adapter reads only the site's pages and the S3 file links they carry.
- **Take a bot check as a no.** When a host answers with a bot check, `Fetcher` stops asking it for the rest of the run. It knows Cloudflare's (the `cf-mitigated: challenge` header), Vercel's (`x-vercel-mitigated`, in front of Boardable's public sites) and Imperva's, which comes as an ordinary HTML page with a 200 status (titled "Pardon Our Interruption", on Simbli, or the older "Request unsuccessful. Incapsula incident ID"); a new kind goes in `is_bot_check` in `needle/fetch.py`. Never try to get past one: no browser automation, no borrowed cookies, no solving the challenge.
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
