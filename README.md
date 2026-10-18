# Haystack Needle

Haystack Needle reads every public meeting agenda in Santa Clara County as it's posted (the Board of Supervisors, city councils, school boards, VTA, Valley Water and their committees) and emails organizers when an item matches a campaign's watchlist: surveillance, policing contracts, immigration enforcement, tenant protections.

It's built for catching an item at its first public appearance, often a committee referral, a study session or a consent-calendar line, while there's still time to organize.

## Status

**Phase 3 of 6 is built, and phase 4 has begun: the Legistar, IQM2, CivicClerk, CivicPlus, eScribe and Midpen adapters, the matcher, the backtest, email alerts and health checks. The rest of the long tail (the other smaller cities and agencies) is next. School boards wait for a later release.**

- [docs/scope.md](docs/scope.md): what the app does, v1 scope and build phases.
- [docs/sources.md](docs/sources.md): which platform each body posts its agendas on, and what each host's robots.txt allows.
- [backtest/cases.yaml](backtest/cases.yaml): 11 past items the app should have caught, with every agenda they appeared on. [backtest/README.md](backtest/README.md) has what a first titles-only check found.
- [watchlists.yaml](watchlists.yaml): the starting watchlists.

It covers the six agencies on Legistar (San José, Sunnyvale, Mountain View, Santa Clara, Cupertino, Valley Water) and the two on IQM2: Santa Clara County (the Board of Supervisors, its committees, boards and commissions) and VTA, the five cities on CivicClerk (Milpitas, Morgan Hill, Gilroy, Los Altos Hills and Los Altos), and the three on CivicPlus AgendaCenter: Saratoga (council and commissions), Campbell's commissions and Los Altos Hills' committees, the Campbell City Council on eScribe, and the Midpeninsula Regional Open Space District (Midpen) board and committees, read from Midpen's own website. School district boards (K-12 districts and the community colleges) are left for a later release: so many of them turn bots away that they'd take most of the work. The County Board of Education stays in, read from its agenda emails once that part is built. On CivicPlus the agenda is a PDF with no item list, so the whole agenda is matched, and an alert points to the page where the match is. In a test, Campbell's eScribe portal started a bot check after 15 file downloads a second apart, so Haystack Needle now waits 10 seconds between requests there. It doesn't get around bot checks: if the check comes back, a new Campbell council agenda's staff reports may take a few hourly runs to read. Its item titles and recommended actions are read right away. High-priority matches are emailed right away; the rest go in a morning digest, usually by 7 a.m.

IQM2 asks for a minute between requests, so a new county agenda is read over several runs: item titles and notes first, then staff reports and attachments, soonest meetings first. A Board of Supervisors agenda with 80 items and 150 attachments takes most of a day to read in full.

## Trying it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python backtest/run.py
.venv/bin/python -m needle.run --dry-run
.venv/bin/python -m needle.run --dry-run --only iqm2 --cache .cache/dry
```

The tests never touch the network. `needle.run --dry-run` reads the live agendas and prints the emails it would send, without sending anything or saving state. `--only` runs one adapter; `--cache` keeps what a dry run downloads, so the next one picks up where it stopped (a dry run saves no state, and IQM2's minute between requests makes a first county read take about 25 minutes). `backtest/run.py` replays the past cases against the live Legistar API, CivicClerk and CivicPlus (one request a second) and IQM2 (one a minute), and `--watchlists` tries term changes on a copy of the watchlists, and caches what it downloads in `.cache/` so a second run is quick.

## How it runs

[A GitHub Actions workflow](.github/workflows/check-agendas.yml) checks every source every 2 hours, and hourly from 72 hours before a meeting through meeting day. It identifies itself by name in every request, keeps to each site's rate limits and honors robots.txt. What it has seen and sent is kept in `state.json` on the `data` branch: dates, hashes and short snippets, never PDFs. Alerts go by email; nothing tracks who opens or clicks them.

Each alert ends with Useful and Noise links. Each opens an email for you to send, to the agenda inbox's votes address, with the alert's watchlist, term and id in the subject; don't change the subject, or the vote won't count. Each run counts the votes: one per person per alert. The state is public, so it keeps no email address, only a scrambled code made with a secret key, which no one can match to an address. The morning digest lists the votes since the last digest, and names a term to review once it has 3 noise votes and no useful ones in 30 days. The app never changes the watchlists: say which terms to change, and Drew decides.

It needs five Actions secrets (Settings → Secrets and variables → Actions), which never go in this repo:

| Secret | What it is |
| --- | --- |
| `SMTP_USERNAME` | The Proton address mail is sent from, `alerts@andrewsiegler.com` |
| `SMTP_PASSWORD` | A Proton SMTP token for that address (not the account password) |
| `ALERT_TO` | Where alerts and health warnings go |
| `FASTMAIL_API_TOKEN` | A read-only Fastmail API token for the agenda inbox, which gets the agenda emails and the alerts' votes |
| `HEALTHCHECKS_URL` | The ping URL of a Healthchecks.io check, which emails if runs stop arriving |

The workflow runs hourly, plus three tries at 2:47, 4:47 and 6:47 a.m. California time for the digest; the first run after 2 a.m. each day sends it. GitHub often starts scheduled runs hours late and drops many of them (only 5 to 7 of the 24 hourly runs fired each day on Oct 15-17, 2026), so the digest usually comes around 6 a.m., and on a day GitHub isn't behind it can come before 3. Set the Healthchecks.io check to a period of 1 hour with a 6-hour grace time, so it emails you only when no run has finished for about 7 hours. If it does, run the workflow by hand (Actions → Check agendas → Run workflow).

To check the mail setup, run the workflow by hand (Actions → Check agendas → Run workflow) with "Send one sample alert" ticked.

## License

[AGPL-3.0](LICENSE). Anyone who runs a modified copy as a service has to share their changes.
