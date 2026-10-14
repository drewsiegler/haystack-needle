# Haystack Needle

Haystack Needle reads every public meeting agenda in Santa Clara County as it's posted (the Board of Supervisors, city councils, school boards, VTA, Valley Water and their committees) and emails organizers when an item matches a campaign's watchlist: surveillance, policing contracts, immigration enforcement, tenant protections.

It's built for catching an item at its first public appearance, often a committee referral, a study session or a consent-calendar line, while there's still time to organize.

## Status

**Phase 1 of 6: research, no code yet.**

- [docs/scope.md](docs/scope.md): what the app does, v1 scope and build phases.
- [docs/sources.md](docs/sources.md): which platform each body posts its agendas on, and what each host's robots.txt allows.
- [backtest/cases.yaml](backtest/cases.yaml): 11 past items the app should have caught, with every agenda they appeared on. [backtest/README.md](backtest/README.md) has what a first titles-only check found.
- [watchlists.yaml](watchlists.yaml): the starting watchlists.

Phase 2 builds the Legistar adapter (San José, Sunnyvale, Mountain View, Santa Clara, Cupertino, Valley Water), the matcher and email alerts.

## How it will run

A scheduled GitHub Actions workflow checks every source every 2 hours, and hourly in the 72 hours before a meeting. It identifies itself by name in every request, keeps to each site's rate limits and honors robots.txt. Alerts go by email; nothing tracks who opens or clicks them. The subscriber list and email login live only in encrypted Actions secrets, never in this repo.

## License

[AGPL-3.0](LICENSE). Anyone who runs a modified copy as a service has to share their changes.
