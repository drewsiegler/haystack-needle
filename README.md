# Haystack Needle

Haystack Needle reads every public meeting agenda in Santa Clara County as it's posted (the Board of Supervisors, city councils, school boards, VTA, Valley Water and their committees) and emails organizers when an item matches a campaign's watchlist: surveillance, policing contracts, immigration enforcement, tenant protections. Each organizer gets only the watchlists, terms and places they picked.

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

It needs five Actions secrets (Settings → Secrets and variables → Actions), which never go in this repo, two more to email other organizers (below), and two for the "Ask for the records" link (below that):

| Secret | What it is |
| --- | --- |
| `SMTP_USERNAME` | The Proton address mail is sent from, `alerts@andrewsiegler.com` |
| `SMTP_PASSWORD` | A Proton SMTP token for that address (not the account password) |
| `ALERT_TO` | Where alerts and health warnings go |
| `FASTMAIL_API_TOKEN` | A read-only Fastmail API token for the agenda inbox, which gets the agenda emails and the alerts' votes |
| `HEALTHCHECKS_URL` | The ping URL of a Healthchecks.io check, which emails if runs stop arriving |
| `SUBSCRIBERS` | The other organizers who get alerts, and what each picked (below) |
| `SUBSCRIBERS_KEY` | A random key that turns each address into a code for the public state file |
| `PRA_LINK_TO` | Where "Ask for the records" links go: your records-request tracker's inbox (optional) |
| `PRA_LINK_KEY` | A random key the tracker shares, so it can tell a link came from an alert (optional) |

The workflow runs hourly, plus three tries at 2:47, 4:47 and 6:47 a.m. California time for the digest; the first run after 2 a.m. each day sends it. GitHub often starts scheduled runs hours late and drops many of them (only 5 to 7 of the 24 hourly runs fired each day on Oct 15-17, 2026), so the digest usually comes around 6 a.m., and on a day GitHub isn't behind it can come before 3. Set the Healthchecks.io check to a period of 1 hour with a 6-hour grace time, so it emails you only when no run has finished for about 7 hours. If it does, run the workflow by hand (Actions → Check agendas → Run workflow).

To check the mail setup, run the workflow by hand (Actions → Check agendas → Run workflow) with "Send one sample alert" ticked.

## Asking for the records

Drew's own surveillance alerts end with an **Ask for the records** link, for PRA Tracker, Drew's private records-request tracker. It opens an email to the tracker's inbox with what the agenda says about the item (the body, the meeting date, the item's number and title, and the term that matched); send it as it is, and the tracker drafts a public records request for the item's agreement and related records on its next run. The request is filled in on the tracker's side, and nothing from the tracker comes back here. Other organizers' alerts never have the link, the public state never holds it, and without both secrets no alert has it.

To turn it on, set two secrets here: `PRA_LINK_TO`, the tracker's address for these emails, and `PRA_LINK_KEY`, a random key the tracker keeps too. The tracker's own README has a command that makes the key and gives it to both repos without showing it on screen.

## Other organizers

Organizers sign up on a CryptPad form (end-to-end encrypted), and Drew approves each one by hand. The form asks for an email address, which watchlists they want, single terms from the watchlists, any terms of their own, and which cities or agencies (all of them if left blank). It asks for no name. `python -m needle.subscribers places` lists the city and agency names the form should offer.

Each person gets alerts only for what they picked: high-priority matches right away, the rest in their own morning digest, one email each, never a CC or BCC. Their own terms are matched for them alone and always wait for the digest. Their first email says what they picked. Every email they get ends with how to stop or change it: reply to it, and Drew updates the list. They hear about matches found after they sign up, not earlier ones.

Drew's own address (`ALERT_TO`) still gets everything on the watchlists, plus the health warnings, the boilerplate notes and the vote counts. Once a week the morning digest lists the terms organizers added themselves, with how many people chose each, to consider for watchlists.yaml.

### The Subscribers page

The **Haystack Admin** app in your Applications folder is your own tool for running Haystack Needle; organizers never see it. It opens a Subscribers page in your browser, where you approve sign-ups and send the list to GitHub without the terminal.

1. In CryptPad, open the form's responses and export them as CSV.
2. Open Haystack Admin (double-click it, or click it in the Dock). Under "Read the CryptPad export", pick the file and press **Read it**.
3. The page lists who's new and whose picks changed since you last looked. For each, choose Approve, Decline or Decide later, then press **Save these decisions**. If one address has several answers, the last one counts.
4. Press **Update GitHub**. It sends the list to the `SUBSCRIBERS` secret, straight from the page and never on screen. The next run emails anyone new and forgets anyone taken off.

Each person on the list has **Change** and **Take off** buttons, and **Add someone** adds a person by hand (when someone writes to you, say). Answers you decline, and people you take off, stay set aside, so the next export doesn't ask about them again; it asks only if they fill in the form again with different picks.

The page keeps your copy of the list in `~/Library/Application Support/Haystack Needle/subscribers.json`, readable by you alone and outside every repo. It keeps your decisions, not the export, so you can delete the CSV once you've read it in. Like PRA Tracker's form, the page is at an address only your Mac can reach (http://127.0.0.1:8738/), answers only to that address, and loads nothing from anywhere else. It reaches GitHub through GitHub's command-line tool, `gh`, signed in as you.

**The first time,** the page doesn't have the list GitHub has (that came from the terminal on Oct 18), and Update GitHub replaces GitHub's list with the page's. So read your latest export in first and approve everyone who's on the list now. If nobody has signed up yet, tick "Nobody has signed up yet" and press Update GitHub instead: GitHub's list becomes empty too, and from then on the two match. macOS also asks once whether Haystack Admin may use your Documents folder: click Allow, since that's where this folder is.

To stop the page, right-click the Dock icon and choose Quit. To make the app, or make it again (if this folder moves, or after a change in `packaging/macos/`), quit it and double-click **Make the Haystack Admin app.command** in this folder in Finder. It's built on your Mac rather than downloaded, so macOS doesn't ask about apps from the internet. `.venv/bin/python -m needle.signups` opens the same page from the terminal.

### From the terminal

1. Export the form's answers as CSV and save it **outside this repo**. Delete the rows you haven't approved; to remove someone or change what they get, delete or edit their row. If one address has several rows, the last one counts.
2. Turn it into the secret. This sends the list straight to GitHub and never shows it on screen:

   ```bash
   python -m needle.subscribers import ~/Documents/responses.csv | gh secret set SUBSCRIBERS
   ```

   It says how many people it read and which rows it left out and why, by row number.
3. Once only, make the key (keep it the same from then on: a new key welcomes everyone again and re-reads every agenda once):

   ```bash
   python -m needle.subscribers key | gh secret set SUBSCRIBERS_KEY
   ```

Done this way, the CSV export is your only copy of the list, since GitHub won't show a secret again. The next run emails anyone new and forgets anyone taken off. Either way, a new term someone adds means every agenda is read again once, over a few runs, just like a term added to watchlists.yaml.

### What's public

The repo, its Actions logs and the `data` branch are public. None of them holds an address or a name; the Subscribers page's copy of the list stays on your Mac. The state file knows each person only by a code made from their address with `SUBSCRIBERS_KEY`, which no one without the key can match to an address, and their own terms by codes made the same way. Alerts waiting for someone's digest sit in the state under their code until the digest goes out, quoting the agenda as every alert does. The logs give only a count of emails sent to organizers, not their subjects. If Proton refuses an address, Drew's digest names the subscriber by number (their place in the list), not by address.

### Sending limits

Proton sends this mail through its SMTP service, which needs a paid plan with a custom domain. Proton doesn't publish its paid plans' sending limits (they depend on the account's reputation); its free plan allows 50 emails an hour and 150 a day, and Proton says it isn't built for bulk mail. So each run sends at most 20 emails to organizers, and each day at most 140 in all (`SUBSCRIBER_SENDS` and `DAY_SENDS` in `needle/run.py`). Drew's own alerts always go. An organizer's email past the cap waits for the next run, and the morning digest says how many had to wait.

Each organizer costs about one digest a day, plus an email for each high-priority match. Move to a sending service such as Amazon SES or Postmark when any of these happens:

- the list passes about 25 people;
- the digest says emails had to wait on more than a couple of days a week;
- Proton warns about a sending limit ("Daily external limit reached") or holds the account for review.

Both take mail over SMTP with STARTTLS on port 587, as Proton does, so moving means changing `HOST` in `needle/mailer.py` and the `SMTP_USERNAME` and `SMTP_PASSWORD` secrets, then raising the caps to the new service's limits. Unlike Proton, a sending service sees who each email goes to.

## License

[AGPL-3.0](LICENSE). Anyone who runs a modified copy as a service has to share their changes.
