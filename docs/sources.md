# Where the agendas live

Phase 1 source check, done Oct 14, 2026. It replaces the platform table in [scope.md](scope.md). Each body was checked by fetching its agenda page or API with the project's user agent, one request a second or slower, after reading the host's robots.txt.

## The short version

Seven adapters cover about 50 bodies. Four of them read an official JSON API or a plain agenda page and need no scraping tricks. The hard part isn't the platforms, it's **robots.txt**: BoardDocs and PrimeGov tell every bot except search engines to stay out, and that covers 15 bodies, including the County Board of Education, all four community college districts and Palo Alto. That's a decision for phase 4 (see [Open question](#open-question-boarddocs-and-primegov)).

| Adapter | Bodies | How it reads | robots.txt | Phase |
| --- | --- | --- | --- | --- |
| Legistar | 6 | Official Web API, JSON | none (404) | 2 |
| Granicus IQM2 | 3 | HTML calendar + RSS feed, PDFs | `Crawl-delay: 60` | 3 |
| CivicClerk | 4 | Public OData API, JSON (agenda items and attachments) | none (404) | 4 |
| CivicPlus AgendaCenter | 4 | Agenda list + PDFs | `Disallow: /RSS.aspx`, Campbell adds `Crawl-delay: 20` | 4 (or 2) |
| Simbli (eBoardSolutions) | 18 (12 confirmed) | Meeting pages | none found | 4 |
| BoardDocs | 13 | — | **disallows all but search engines** | 4, blocked |
| PrimeGov | 2 | Public API works, JSON | **`Disallow: /`** | 4, blocked |
| One-offs | 8 | Municode Meetings, CivicWeb, iCompass, AgendaOnline, Granicus, Laserfiche, Los Altos's own site | varies | later |

## By body

### Legistar (phase 2)

All six answered `https://webapi.legistar.com/v1/{client}/events` with no key. Matters carry `MatterAgendaDate` (first agenda date) and `MatterId`, which is what lets an alert say "this moved forward."

| Body | Client | Notes |
| --- | --- | --- |
| San José: Council, Rules and Open Government, CED, PSFSS and others | `sanjose` | Future placeholder events exist (Dec 2026); filter by date |
| Sunnyvale | `sunnyvaleca` | Placeholder event dated 2027-12-31 |
| Mountain View, incl. Rental Housing Committee, Public Safety Advisory Board | `mountainview` | |
| Santa Clara (city), incl. Stadium Authority | `santaclara` | |
| Cupertino, incl. Public Safety Commission, TICC | `cupertino` | |
| Valley Water board and committees | `scvwd` | |

Tested and **not** on Legistar: `milpitas`, `paloalto`, `campbell`, `gilroy`, `morganhill`, `losaltos`, `losgatos`, `saratoga`, `sccgov`, `vta`.

### Granicus IQM2 (phase 3)

| Body | Portal |
| --- | --- |
| Board of Supervisors, its committees, county boards and commissions | sccgov.iqm2.com |
| VTA board and committees | santaclaravta.iqm2.com |
| Fremont Union High School District | fremontunionhighschoolca.iqm2.com |

IQM2's robots.txt asks for **one request a minute** (`Crawl-delay: 60`). Each portal has an RSS calendar (`/Citizens/RSS.aspx?Feed=Calendar`), so the adapter should poll the feed and fetch only meetings that are new or changed. At one request a minute, a two-hour run gets 120 requests per portal, enough for a few new meetings and their attachments, not for a full re-crawl. The county calendar page alone is 1.6 MB and lists about 800 meetings.

Gilroy's site still links an old `gilroyca.iqm2.com` portal; it has moved to CivicClerk.

### CivicClerk (phase 4, easy)

Each city has an open OData API at `https://{tenant}.api.civicclerk.com/v1/`. `Events` lists meetings; `Meetings/{agendaId}` returns the agenda as nested items with their attachments. Tested on the Los Altos Hills Jan 15, 2026 council agenda, which listed the Flock termination item by title.

| Body | Tenant |
| --- | --- |
| Milpitas | `milpitasca` |
| Morgan Hill | `morganhillca` |
| Gilroy | `gilroyca` |
| Los Altos Hills City Council (commissions use AgendaCenter) | `losaltoshillsca` |

### CivicPlus AgendaCenter (phase 4, or phase 2 for Saratoga)

| Body | Agenda list |
| --- | --- |
| Saratoga (council and commissions) | saratoga.ca.us/AgendaCenter |
| Campbell | campbellca.gov/AgendaCenter |
| Los Gatos commissions (Town Council is on Municode Meetings) | losgatosca.gov/AgendaCenter |
| Los Altos Hills commissions | losaltoshills.ca.gov/AgendaCenter |

**Saratoga answered normally** to an honest user agent: agenda list, search and PDFs. The refusal noted in the scope was probably aimed at a generic fetcher. Saratoga's council agendas are short PDFs (2 to 6 pages) that list each staff report and attachment by name, so title matching works well there. Its robots.txt disallows `/RSS.aspx` and `/Search`; AgendaCenter's own pages (`/AgendaCenter/...`) are allowed. Campbell asks for 20 seconds between requests.

Saratoga is simple enough to add in phase 2 if you want the policing-contract fight covered early.

### Simbli (phase 4)

Meeting pages seen at `simbli.eboardsolutions.com/SB_Meetings/...?S={district id}`:

Alum Rock Union, Los Altos SD, Los Gatos Union, Luther Burbank, Mountain View Whisman (older agendas on NovusAgenda), Sunnyvale SD, Union SD, Los Gatos-Saratoga Joint Union HSD, Morgan Hill Unified, Loma Prieta, Saratoga Union, Berryessa Union.

Only board **policies** seen on Simbli, so the meetings platform still needs a look: Cambrian, Campbell Union SD, Franklin-McKinley, Orchard, San José Unified, Santa Clara Unified.

### BoardDocs (phase 4, blocked by robots.txt)

Cupertino Union, Evergreen, Moreland, Mount Pleasant, Oak Grove, East Side Union HSD, Campbell Union HSD, Palo Alto Unified, Santa Clara County Office of Education (County Board of Education), Foothill-De Anza CCD, West Valley-Mission CCD, San José-Evergreen CCD, Gavilan CCD.

`go.boarddocs.com/robots.txt` disallows every user agent (`User-agent: * / Disallow: /`) and allows only Googlebot, Bingbot, msnbot and Slurp. Separately, it returns 403 to any user agent that doesn't start with `Mozilla/5.0`.

### PrimeGov (phase 4, blocked by robots.txt)

| Body | Portal |
| --- | --- |
| Palo Alto | cityofpaloalto.primegov.com |
| Santa Clara Valley Open Space Authority | openspaceauthority.primegov.com |

PrimeGov's public API (`/api/v2/PublicPortal/ListUpcomingMeetings`) returned Palo Alto's meetings as JSON, with publish dates for each document, even though paloalto.gov itself returns 403. But PrimeGov's robots.txt is `User-agent: * / Disallow: /`.

### One-offs

| Body | Platform | Status |
| --- | --- | --- |
| Los Gatos Town Council | Municode Meetings (losgatos-ca.municodemeetings.com) | Drops the connection for scripted clients; check by hand |
| Monte Sereno | iCompass CivicWeb (montesereno.civicweb.net) | Found, not yet tested |
| Mountain View-Los Altos HSD | iCompass / Diligent (mvla.ic-board.com, mvlapub.ic-board.com) | Found by search, not tested |
| Milpitas Unified | AgendaOnline (per its website) | Not tested |
| Gilroy Unified | AgendaOnline (gilroy.agendaonline.net); also links Simbli | Confirm which is current |
| Housing Authority of the County of Santa Clara | Granicus (hacsc.granicus.com) | Found, not tested |
| Midpeninsula Regional Open Space District | Laserfiche portal | Found, not tested |
| Los Altos (city) | Its own website (losaltosca.gov/citycouncil/...) | Returns 403 to any non-browser client; check by hand, fall back to its email notices |

Still unknown: Lakeside Joint SD, El Camino Healthcare District, Santa Clara County Central Fire.

## What this means for the collector

- **User agent.** Use `Mozilla/5.0 (compatible; HaystackNeedle/<version>; +<repo URL>)`. It's the standard honest-bot format, it names the project and where to reach it, and BoardDocs rejects anything else outright.
- **Rate.** One request a second is the floor, but honor each host's `Crawl-delay`: 60 s for IQM2, 20 s for Campbell. Feeds and APIs first, pages second.
- **robots.txt is per host, not per platform.** Simbli's robots.txt URL redirects to an error page; Legistar's and CivicClerk's API hosts have none.

## Open question: BoardDocs and PrimeGov

The scope says to honor robots.txt. BoardDocs and PrimeGov both say no to every bot that isn't a search engine. Together that's 15 bodies:

- **BoardDocs (13):** the County Board of Education, all four community college districts, East Side and Campbell high school districts, Palo Alto Unified and five elementary districts.
- **PrimeGov (2):** Palo Alto City Council and its commissions, and the Open Space Authority.

Options, none needed before phase 4:

1. **Leave them out of the scraper** and cover them by subscribing the alert address to each body's own email notices, then matching those emails. Slower (it announces that an agenda exists, not what's inside it) but within the rules.
2. **Ask.** Email PrimeGov/Palo Alto's clerk and the BoardDocs districts that matter most (County Board of Education first) for permission to fetch their agendas at a low rate. A yes in writing lets the adapter run.
3. **Fetch anyway at a low rate**, treating robots.txt as aimed at crawlers rather than a scheduled reader of public agendas. That would break the scope's own rule, so it shouldn't happen without deciding to change that rule.
