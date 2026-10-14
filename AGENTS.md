# AGENTS.md

Guidance for AI coding agents working in this repo. [docs/scope.md](docs/scope.md) is the source of truth for what the app does and the order it's built in; [docs/sources.md](docs/sources.md) records which platform each body uses and what its host allows.

## What this is

A scheduled job that reads public meeting agendas across Santa Clara County, matches item titles, item text and attached PDFs against [watchlists.yaml](watchlists.yaml), and emails alerts. Each agenda platform gets one small adapter; everything after the adapter (matching, state, alerts) is shared, so adding a body means adding one file.

Status: phase 1 (research) is done; phase 2 (Legistar adapter, matcher, email) is next. The phase 2 and 3 build prompt is at the end of the scope.

## Rules

- **Identify honestly.** User agent: `Mozilla/5.0 (compatible; HaystackNeedle/<version>; +<repo URL>)`. BoardDocs rejects anything that doesn't start with `Mozilla/5.0`; never pretend to be a browser beyond that prefix.
- **Rate.** At most one request a second per host, and slower where a host's robots.txt sets `Crawl-delay` (IQM2: 60 s, Campbell: 20 s). Prefer feeds and APIs to pages.
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
