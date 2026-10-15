# Haystack Needle

Haystack Needle reads every public meeting agenda in Santa Clara County as it's posted (the Board of Supervisors, city councils, school boards, VTA, Valley Water and their committees) and emails organizers when an item matches a campaign's watchlist: surveillance, policing contracts, immigration enforcement, tenant protections.

It's built for catching an item at its first public appearance, often a committee referral, a study session or a consent-calendar line, while there's still time to organize.

## Status

**Phase 2 of 6: the Legistar adapter, the matcher, the backtest, email alerts and health checks are built. IQM2 (the county and VTA) is next.**

- [docs/scope.md](docs/scope.md): what the app does, v1 scope and build phases.
- [docs/sources.md](docs/sources.md): which platform each body posts its agendas on, and what each host's robots.txt allows.
- [backtest/cases.yaml](backtest/cases.yaml): 11 past items the app should have caught, with every agenda they appeared on. [backtest/README.md](backtest/README.md) has what a first titles-only check found.
- [watchlists.yaml](watchlists.yaml): the starting watchlists.

Phase 2 covers the six agencies on Legistar (San José, Sunnyvale, Mountain View, Santa Clara, Cupertino, Valley Water). High-priority matches are emailed right away; the rest go in a 7 a.m. digest.

## Trying it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python backtest/run.py
.venv/bin/python -m needle.run --dry-run
```

The tests never touch the network. `needle.run --dry-run` reads the live agendas and prints the emails it would send, without sending anything or saving state. `backtest/run.py` replays the past cases against the live Legistar API, one request a second, and caches what it downloads in `.cache/` so a second run is quick.

## How it runs

[A GitHub Actions workflow](.github/workflows/check-agendas.yml) checks every source every 2 hours, and hourly from 72 hours before a meeting through meeting day. It identifies itself by name in every request, keeps to each site's rate limits and honors robots.txt. What it has seen and sent is kept in `state.json` on the `data` branch: dates, hashes and short snippets, never PDFs. Alerts go by email; nothing tracks who opens or clicks them, and the useful/noise links open an email you choose to send.

It needs four Actions secrets (Settings → Secrets and variables → Actions), which never go in this repo:

| Secret | What it is |
| --- | --- |
| `SMTP_USERNAME` | The Proton address mail is sent from, `alerts@andrewsiegler.com` |
| `SMTP_PASSWORD` | A Proton SMTP token for that address (not the account password) |
| `ALERT_TO` | Where alerts and health warnings go |
| `HEALTHCHECKS_URL` | The ping URL of a Healthchecks.io check, which emails if runs stop arriving |

To check the mail setup, run the workflow by hand (Actions → Check agendas → Run workflow) with "Send one sample alert" ticked.

## License

[AGPL-3.0](LICENSE). Anyone who runs a modified copy as a service has to share their changes.
