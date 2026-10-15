# Haystack Needle — Build Scope

Oct 14, 2026 · @Drew S

## What it is

Haystack Needle checks every public agenda in Santa Clara County each day, matches new items against organizer watchlists like Flock, sheriff contract, ICE and rent, and alerts the right people within hours of posting.

**The problem.** Roughly 60 bodies post agendas here: the Board of Supervisors and its committees, 15 city councils plus their commissions, 30-some K-12 boards, 4 community college boards, and special districts like VTA and Valley Water. No organizer can read all of them, and the items that decide fights often surface first in a committee, a study session or a consent calendar.

**Why speed matters.** The Brown Act only requires a regular-meeting agenda to post 72 hours ahead (24 hours for special meetings). So "days earlier" comes from two things: catching an item the hour it posts, and catching its earliest appearance, such as a committee referral, study session, RFP award or tentative agenda, before it reaches the full council.

**Who it's for.** v1 alerts go to Drew only. SURJ and partner organizers come next, then other groups, the way District Lookup opened up.

**What success looks like.**

- An alert lands within 6 hours of an agenda posting.
- Backtested on past fights (the Saratoga policing proposal, local Flock contracts), it flags each one at its first public appearance.
- Fewer than 1 in 3 alerts are noise after the first month of tuning.

## Where the agendas live

Two adapters cover most of the county on day one. San José, Sunnyvale, Mountain View, Santa Clara, Cupertino and Valley Water all publish through Legistar, whose Web API answered without a key when tested. The Board of Supervisors and VTA both run on Granicus IQM2, so one scraper covers the Board, its committees and VTA.

The long tail is smaller towns and school boards, spread across four or five other platforms. Two sites (Saratoga and Palo Alto) refused automated fetches during this check, which matters for how the collector identifies itself.

| Body | Platform | How the app reads it | Checked |
| --- | --- | --- | --- |
| [San José](https://webapi.legistar.com/v1/sanjose/bodies) Council, Rules, Public Safety (PSFSS) and other committees | Legistar | Open Web API, JSON | Yes |
| [Sunnyvale](https://webapi.legistar.com/v1/sunnyvaleca/bodies) | Legistar | Open Web API | Yes |
| [Mountain View](https://webapi.legistar.com/v1/mountainview/bodies), incl. Rental Housing Committee, Public Safety Advisory Board | Legistar | Open Web API | Yes |
| [Santa Clara (city)](https://webapi.legistar.com/v1/santaclara/bodies) | Legistar | Open Web API | Yes |
| [Cupertino](https://webapi.legistar.com/v1/cupertino/bodies), incl. Public Safety Commission | Legistar | Open Web API | Yes |
| [Valley Water](https://webapi.legistar.com/v1/scvwd/bodies) board and committees | Legistar | Open Web API | Yes |
| [Board of Supervisors](https://d3.santaclaracounty.gov/view-meetings-and-agendas), its committees, county boards and commissions | Granicus IQM2 (sccgov.iqm2.com); a PrimeGov portal was started, then paused | Scrape HTML and PDFs | Yes |
| [VTA](https://vta.gov/sites/default/files/2023-01/board_correspondence_0127.pdf) board and committees | Granicus IQM2 (santaclaravta.iqm2.com) | Scrape | Yes, as of 2023 |
| [Palo Alto](https://www.paloalto.gov/Departments/City-Clerk/City-Meeting-Groups) | PrimeGov | Portal blocked my fetch; council packets post 11 days ahead | Partly |
| [Milpitas](https://sanjosespotlight.com/how-to-participate-in-milpitas-city-council-meetings/) | CivicClerk | Portal scrape | Reported, not tested |
| [Los Gatos](https://www.losgatosca.gov/AgendaCenter/Library-Board-17) commissions | CivicPlus AgendaCenter | Built-in RSS and Notify Me | Commissions yes; confirm Town Council |
| [East Side Union HSD](https://www.esuhsd.org/documents/Board%20-%20Admin/Student%20Governing%20Board/SGB%20Agenda%202021-03-15.pdf) | BoardDocs | Scrape | Yes |
| Saratoga | Unknown | Site refused automated fetch | Check by hand |
| Campbell, Morgan Hill, Gilroy, Los Altos, Los Altos Hills, Monte Sereno | Unknown | — | Check by hand |
| Other K-12, community college and open space boards | Likely BoardDocs or Simbli (assumption) | — | Check by hand |

The first build task is finishing this table: one afternoon of clicking through each body's agenda page and noting the platform.

## What already exists

Nothing does this for organizers in this county, but the name is taken and one open-source library is worth reading before writing scrapers.

| Tool | What it does | Why it doesn't cover this |
| --- | --- | --- |
| [Agenda Watch](https://agendawatch.org) (Big Local News at Stanford, with RJI) | [Harvests agendas and minutes daily and sends alerts](https://newspapers.org/stories/agenda-watch-platform-will-help-journalists-keep-watch-on-local-government,4163083) | Login is limited to MuckRock users at verified organizations, i.e. newsrooms. Coverage started with CivicPlus sites. |
| [civic-scraper](https://github.com/biglocalnews/civic-scraper) | Python library for downloading agendas and minutes, the engine behind Agenda Watch | Marked as no longer maintained; useful as a reference for scraper design, risky as a dependency |
| Platform alerts (Legistar, CivicPlus Notify Me, BoardDocs) | Email or RSS when a body posts an agenda | One site at a time, and they announce that an agenda exists, not what's inside it |
| Govly, Starbridge | Paid meeting summaries | Built for vendors chasing contracts, not for organizers |

With the project now called Haystack Needle, the names no longer clash. It's still worth emailing Big Local News to ask whether Santa Clara County is covered and whether a community-group login exists; if it is, this app can focus on what theirs lacks (watchlists by campaign, district tagging, alerts to non-journalists).

## v1 scope

v1 runs through phase 4: the Legistar and IQM2 bodies, school boards, and the other long-tail platforms. It reads every item title and attached staff report and emails matches to you. Everything else waits until v1 has caught a real item.

**v1 does**

- Checks every source every 2 hours, and hourly from 72 hours before a meeting through meeting day, to catch amended agendas and late memos.
- Reads agenda item titles, item text and attached PDFs (staff reports, contracts, resolutions), not just the agenda cover.
- Matches against watchlists, one per campaign: surveillance, policing contracts, immigration enforcement, tenant protections.
- Emails you right away for high-priority watchlists, and in a 7 a.m. digest for the rest.
- Tracks an item as it moves. When Legistar shows the same matter going from committee to council, the alert says "this moved forward" instead of looking new.
- Includes a "useful / noise" link in every alert so watchlists can be tuned.

**v1 doesn't**

- Alert anyone but you.
- Read minutes, video or transcripts.
- Offer public sign-up, accounts or a web dashboard.
- Use AI triage (planned for after v1).

## Matching

Watchlists match phrases, not bare words, and score where the match landed. "ICE" and "rent" as bare words would flood the inbox ("rental car", "Ice Centre", "current"), and a Flock contract often hides behind a title like "Approve agreement with vendor for public safety technology."

**Rules**

- Whole-word, case-aware matching. "ICE" matches only in capitals; "Immigration and Customs Enforcement" matches any case.
- The last word of a term may be plural: "license plate reader" matches "License Plate Readers". (Added Oct 14 after the backtest dry run.)
- A hyphen, with or without spaces around it, counts the same as a space, because PDF text breaks hyphenated names unpredictably: "Los Gatos-Monte Sereno" matches "Los Gatos - Monte Sereno" and "Los Gatos Monte Sereno". (Added Oct 14.)
- A straight apostrophe and a curly one count the same, because PDF text turns most apostrophes curly: "Sheriff's Office" matches "Sheriff’s Office". (Added Oct 14.)
- Each watchlist has terms, exclusions, a priority and optional bodies to limit it to.
- Score by location: a match in the item title is high; in the staff report, medium; only deep in a 300-page packet, low and digest-only.
- Skip boilerplate. Some bodies print the same legal paragraph on every agenda; a phrase that appears in more than half of a body's agendas gets flagged for review instead of alerting.
- Extract PDF text with a text layer first and fall back to OCR only for scanned pages.
- Alert once per item per stage. A changed or amended item alerts again, labeled "updated."

**Starting watchlist file** (Drew edits this; the app reads it. The current version, with terms added after the backtest dry run, is [watchlists.yaml](../watchlists.yaml).)

```yaml
surveillance:
  priority: high
  terms: [Flock, Flock Safety, ALPR, automated license plate, license plate reader,
          real-time crime center, ShotSpotter, SoundThinking, drone as first responder,
          surveillance use policy, surveillance technology]
policing-contracts:
  priority: high
  terms: [sheriff, law enforcement services agreement, police services contract,
          contract for police services, Los Gatos-Monte Sereno]
immigration:
  priority: high
  terms: [ICE, Immigration and Customs Enforcement, 287(g), immigration detainer,
          SB 54, sanctuary]
  case_sensitive: [ICE]
tenants:
  priority: normal
  terms: [rent stabilization, rent control, just cause eviction, tenant protection,
          anti-displacement, mobilehome rent, rent registry]
```

## Alerts

Email first, plus an RSS feed per watchlist; Signal later. Email reaches everyone, and RSS lets people follow without handing over any contact info.

**What an alert says**

```
Subject: [Haystack Needle: policing-contracts] Saratoga City Council, Wed Oct 21, Item 7

Item 7: Agreement for law enforcement services (first appearance)
Matched: "law enforcement services agreement" in staff report, p. 3
  "...staff recommends issuing an RFP for law enforcement services..."

Agenda:        <link>
Staff report:  <link>
Comment by:    Tue Oct 20, 5 p.m. to clerk@<city> (from agenda)
Prior history: none found

Useful? <yes link>  Noise? <no link>
```

The comment deadline and where to send comments come straight from the agenda, because that's what turns an alert into action.

**Privacy**

- The subscriber list is an organizer roster. Keep it out of the code repo, in an encrypted secret or a private config.
- No tracking pixels or click tracking. The useful/noise links record a vote, not who clicked.
- Send from a dedicated address. Check whether your Proton plan allows sending from an app; otherwise use a transactional email service and accept that it sees recipient addresses.
- Signal (via a bot account) is the channel organizers actually read, but bots are fragile and need a phone number. Phase 5.

## Architecture

Each portal gets a small adapter and everything after it is shared, so adding a body means adding one file. It runs as a scheduled job, so unlike District Lookup there's nothing for anyone to install.

&#91;embedded content: Pipeline · 3 adapter groups feeding one matcher and 3 outputs\]

- **Where it runs.** A scheduled GitHub Actions workflow in a public repo costs $0, and the agenda data it handles is public. GitHub can delay scheduled runs at busy times and pauses schedules in repos that go quiet, so pair it with a dead-man's-switch service such as Healthchecks.io that emails you when runs stop arriving.
- **State.** A small file of seen items, attachment hashes and sent alerts, committed to a `data` branch. PDFs are re-downloaded when needed, never stored in git.
- **Secrets.** SMTP login and the subscriber list live in encrypted Actions secrets, never in the repo.
- **District Lookup tie-in.** Each body gets tagged with its boundary from District Lookup's GeoPackage. Alerts can then name the district affected, and later someone can subscribe by address and get every body that governs it.
- **Surveillance by District tie-in.** A surveillance-watchlist match can link to that city's license plate reader count.

## Risks

The biggest risk is silent failure: a portal changes, the scraper returns nothing, and no alert looks exactly like no news.

| Risk | What it looks like | Mitigation |
| --- | --- | --- |
| Portal changes | The county already moved the Board to PrimeGov and back to IQM2 | Health check per source: if a body that meets weekly shows no new meeting in 14 days, email Drew |
| Sites that block bots | Saratoga and Palo Alto refused automated fetches in testing | Identify the app honestly in its user agent, fetch slowly, honor robots.txt, fall back to the platform's own email or RSS subscription |
| Late changes | Amended agendas and supplemental memos land after the 72-hour posting | Hourly re-checks through meeting day; "updated" alerts |
| Vague item titles | The word Flock only appears inside the contract PDF | Always read attachments, not just titles |
| Scanned PDFs | No text layer, so nothing matches | OCR fallback, and log which bodies post scans |
| Noise | Too many alerts and people stop reading | Phrase-level terms, location scoring, useful/noise votes, monthly tuning |
| One maintainer | Adapters break while Drew is busy | Keep adapters small and tested against saved sample pages; document how to add a body |

## Build phases

Start with a backtest, not code: a list of past items the app should have caught tells Claude Code what "working" means.

1. **Backtest set and source check.** List 5 to 10 past items (the Saratoga policing proposal, local Flock contracts, rent items) with the body and date each first appeared. Finish the platform table above, Saratoga first.
2. **Legistar + email.** One adapter for the six Legistar bodies, the watchlist file, the matcher, and email alerts. If Saratoga's platform turns out to be simple, add it here.
3. **IQM2.** Board of Supervisors, its committees and VTA, plus per-source health checks.
4. **Long tail.** BoardDocs (school boards), CivicClerk (Milpitas), CivicPlus (Los Gatos and others), PrimeGov (Palo Alto). This completes v1.
5. **Reach.** RSS feeds, a simple public page of recent matches, District Lookup tagging, Signal.
6. **Open it up.** Let partner groups bring their own watchlists.

**Prompt to paste into Claude Code for phases 2 and 3**

```markdown
# Build: Haystack Needle, agenda alerts for Santa Clara County

## Goal
Every 2 hours (hourly in the 72 hours before a meeting), pull new and amended
public meeting agendas, read item titles and attached PDFs, match them against
watchlists.yaml, and email alerts. Scope doc: https://claude.ai/code/artifact/27cf0105-c512-41ed-812e-084c64af42e5

## Stack
- Python 3.12, httpx, pypdf for text (ocrmypdf only for pages with no text layer),
  PyYAML, Jinja2 email templates, SMTP.
- GitHub Actions scheduled workflow in a public repo. Commit a small state file
  (seen items, attachment hashes, sent alerts) to a `data` branch each run.
  Never commit PDFs. SMTP credentials and the alert address live only in
  Actions secrets.

## Data model
Source > Body > Meeting > Item > Attachment.
Match(item, watchlist, term, location: title|item_text|attachment, page, score).
AlertLog(item, stage, sent_at). Keep Legistar MatterId so the same item can be
followed from committee to council.

## Tasks
1. adapters/legistar.py for clients: sanjose, sunnyvaleca, mountainview,
   santaclara, cupertino, scvwd (https://webapi.legistar.com/v1/{client}/...).
   Fetch events from 2 days ago to 30 days ahead, their items, and attachments.
   Handle paging.
2. Normalize into the data model; skip attachments whose hash hasn't changed.
3. Matcher: whole-word matching; terms listed under case_sensitive match only in
   that case; the last word of a term may be plural; a hyphen with or without
   spaces around it counts as a space; score title > item text > attachment;
   flag phrases that appear in
   more than half of a body's agendas as boilerplate instead of alerting.
4. Alerts: immediate email for priority: high, 7 a.m. Pacific digest for the rest.
   Each alert shows body, date, item, matched term with a short snippet, links,
   the public comment deadline/address if the agenda states one, and
   useful/noise links. Alert again only if the item changes or moves to a new body.
5. Health check: email the maintainer if a body that meets regularly shows no
   meetings for 14 days, or an adapter fails twice in a row.
6. Tests: saved fixtures per adapter, plus backtest/run.py that replays
   backtest/cases.yaml and reports which past items were caught and how early.
7. Then (phase 3): adapters/iqm2.py for sccgov.iqm2.com (Board of Supervisors,
   committees, boards and commissions) and santaclaravta.iqm2.com (VTA).

## Constraints
- User-Agent names the project and a contact address; max 1 request/second
  per host; honor robots.txt.
- No tracking pixels or click tracking. Never log the alert address.
- License: AGPL-3.0, with the LICENSE file at the repo root.
- Each adapter is one small file with its own tests, so a new body or
  platform can be added without touching the rest.
```

## Decisions

All six are settled, and the Claude Code prompt above reflects them.

- [x] **Name.** Haystack Needle (Agenda Watch was taken).
- [x] **Who gets v1 alerts.** Just you for now; SURJ and partners later.
- [x] **Hosting.** GitHub: a public repo with scheduled Actions, $0, with credentials in Actions secrets.
- [x] **School boards in v1.** Yes, in phase 4. BoardDocs and Diligent Community boards are covered by their own agenda emails, since those platforms' robots.txt turns bots away (decided Oct 15, 2026; see [sources.md](sources.md)).
- [x] **AI triage later.** Yes, after v1. It would summarize matches and drop false positives for a small per-run cost.
- [x] **License.** AGPL-3.0, so anyone running a modified copy as a service has to share their changes.

One more worth doing this week: email Big Local News about county coverage and community-group access before building the long-tail scrapers.
