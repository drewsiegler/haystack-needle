# Where the agendas live

Phase 1 source check, done Oct 14, 2026. It replaces the platform table in [scope.md](scope.md). Each body was checked by fetching its agenda page or API with the project's user agent, one request a second or slower, after reading the host's robots.txt.

## The short version

Seven adapters cover about 51 bodies. Four of them read an official JSON API or a plain agenda page and need no scraping tricks. The hard part isn't the platforms, it's **robots.txt**: BoardDocs and PrimeGov tell every bot except search engines to stay out, and that covers 15 bodies, including the County Board of Education, all four community college districts and Palo Alto. That's a decision for phase 4 (see [Open question](#open-question-boarddocs-and-primegov)).

| Adapter | Bodies | How it reads | robots.txt | Phase |
| --- | --- | --- | --- | --- |
| Legistar | 6 | Official Web API, JSON | none (404) | 2 |
| Granicus IQM2 | 3 | HTML calendar + RSS feed, PDFs | `Crawl-delay: 60` | 3 |
| CivicClerk | 5 | Public OData API, JSON (agenda items and attachments) | none (404) | 4, built |
| CivicPlus AgendaCenter | 3 | Agenda search page + PDFs | `Disallow: /Search`, `/RSS.aspx` (not `/AgendaCenter/`) | 4, built |
| Simbli (eBoardSolutions) | 18 (12 confirmed) | Meeting pages | none found | 4 |
| BoardDocs | 13 | — | **disallows all but search engines** | 4, blocked |
| PrimeGov | 2 | Public API works, JSON | **`Disallow: /`** | 4, blocked |
| One-offs | 8 | Municode Meetings, CivicWeb, iCompass, AgendaOnline, Granicus, Laserfiche, eScribe | varies | later |

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

IQM2's robots.txt asks for **one request a minute** (`Crawl-delay: 60`). The RSS calendar is at `/Services/RSS.aspx?Feed=Calendar` (`/Citizens/RSS.aspx` is an error page); on Oct 14, 2026 it worked for VTA but showed the error page for the county, and it lists only meetings with published documents. So the adapter reads `/Citizens/Calendar.aspx?From=MM/DD/YYYY&To=MM/DD/YYYY` instead: one request per portal per run, about 215 KB for the county's 32-day window (without the dates it's the whole year, 1.6 MB and about 800 meetings). Each meeting's `Detail_Meeting.aspx?ID=` page has the whole agenda in one request: items, their "possible action" notes, legislative file links and attachments. The adapter keeps those outlines in the state and reads a page again only when its calendar row changes, or after 110 minutes in the 3 days before the meeting and a day before that.

A run is one thread, so every minute waiting on IQM2 holds up the rest. A run may spend its first 25 minutes waiting on IQM2; after that, an IQM2 request that would have to wait is left for the next run, while one whose minute passed during other work still goes out. That leaves Legistar at least 15 of the run's 40 minutes. Measured on Oct 14, 2026, a first run with an empty state made 31 IQM2 requests in 24 minutes (both calendars, all 20 posted agendas, then staff reports and attachments, soonest meetings first) and left 343 for later runs, about a day's worth. After that a run reads only what's new. For scale, the January 27, 2026 Board agenda had 81 legislative files and 148 attachments.

Gilroy's site still links an old `gilroyca.iqm2.com` portal; it has moved to CivicClerk.

### CivicClerk (phase 4, built)

Los Altos moved its current meetings to CivicClerk (found by hand on Oct 14; losaltosca.gov itself returns 403 to scripts). Its older agendas stay in a CivicPlus AgendaCenter archive with RSS and Notify Me, which the app doesn't need. Each city has an open OData API at `https://{tenant}.api.civicclerk.com/v1/`. `Events` lists meetings; `Meetings/{agendaId}` returns the agenda as nested items with their attachments. Tested on the Los Altos Hills Jan 15, 2026 council agenda, which listed the Flock termination item by title.

All five API hosts return 404 for `/robots.txt`, so the usual one request a second applies. What the adapter relies on (checked Oct 14, 2026):

- `Events?$filter=startDateTime ge ... and startDateTime lt ...` lists meetings. The answer is paged: Los Altos Hills gave 15 rows and an `@odata.nextLink` for a 32-day window, and the adapter follows it (only to the same host). Times are local, written with a `Z`.
- `Meetings/{agendaId}` is the whole agenda in one request (130 to 300 KB for a council): items nested under their section headings, each with a description (the only item text beyond the title) and attachments. An agenda that isn't published yet answers with no items (`agendaIsPublish: false`), so the adapter lists the meeting and reads the items on a later run. An item's `modifiedDate` is the time of the request, so it can't tell what changed; the agenda's posting time stands in.
- Agenda PDFs are at `Meetings/GetMeetingFileStream(fileId=,plainText=false)` and attachments at `Meetings/GetAttachmentFile(fileId=)`. Both answer with the PDF and no key. The attachment records also carry a signed blob link (`pdfVersionFullPath`) that expires in a week; the adapter doesn't use it, and the saved fixtures have its signature removed.
- Titles can hold HTML, tables and `&nbsp;`; the adapter reduces them to plain text and cuts a title over 300 characters, keeping the whole of it in the item's text. Some attachments are Word files, which are left out.
- No matter history: an item's number (like `2025-876`) is its matter ID, so "moved forward" comes from past alerts, as on IQM2.
- The meeting page link is `https://{tenant}.portal.civicclerk.com/event/{eventId}/overview`; it opened that meeting's page in a browser on Oct 14, 2026.

| Body | Tenant |
| --- | --- |
| Milpitas | `milpitasca` |
| Morgan Hill | `morganhillca` |
| Gilroy | `gilroyca` |
| Los Altos Hills City Council (commissions use AgendaCenter) | `losaltoshillsca` |
| Los Altos City Council and commissions | `losaltosca` |

### CivicPlus AgendaCenter (phase 4, built)

**Saratoga answered normally** to an honest user agent: agenda list, search and PDFs. The refusal noted in the scope was probably aimed at a generic fetcher. All the AgendaCenter hosts serve the same CivicPlus robots.txt: it disallows `/Search`, `/RSS.aspx` and a few admin paths, but not `/AgendaCenter/`. Its `Crawl-delay: 20` sits under the Siteimprove user agents only (phase 1 read it as Campbell's), so the usual one request a second applies.

What the adapter relies on (checked Oct 14, 2026):

- `/AgendaCenter/Search/?term=&CIDs=all&startDate=MM/DD/YYYY&endDate=MM/DD/YYYY` lists every category's agendas in a date window, on one page, so a run makes one request per city and a December window crosses into January without trouble. The main `/AgendaCenter` page shows only each category's current year, and other years load by POST.
- Each row has the meeting date, the agenda's title and ID, and "Posted" or "Amended" with a Pacific date and time. The adapter uses that time to know when to read the PDF again. No time of day for the meeting itself.
- `/AgendaCenter/ViewFile/Agenda/_MMDDYYYY-NNNN` is the agenda PDF, and the only text there is: no item list, no HTML agenda, no separate packet link on any of the four sites. So each meeting is one item whose attachment is the PDF. Saratoga's council agendas are 2 to 6 pages that name each staff report (the names are links to the reports, which the adapter doesn't follow yet). Campbell's "agenda" is the whole packet: the Oct 13, 2026 Planning Commission file was 138 MB and over 500 pages. A match past page 10 scores low and waits for the digest.
- `/AgendaCenter/{Category-Name}-{id}/?#_MMDDYYYY-NNNN` opens the body's agenda list at that row; it's the alert's meeting page link.

| Body | Agenda list | Notes |
| --- | --- | --- |
| Saratoga (council and commissions) | saratoga.ca.us/AgendaCenter | |
| Campbell commissions | campbellca.gov/AgendaCenter | The City Council category stops at Oct 7, 2025 because the council moved to eScribe (see [One-offs](#one-offs)) |
| Los Altos Hills committees | losaltoshills.ca.gov/AgendaCenter | The City Council category (id 15) is skipped: CivicClerk reads that council item by item |
| Los Gatos commissions | losgatosca.gov/AgendaCenter | **Not read**: nothing posted after October 2024. The commissions probably moved to Municode Meetings with the Town Council (not checked) |

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
| Los Gatos Town Council | Municode Meetings (losgatos-ca.municodemeetings.com) | Agendas and packets load fine for a script, as HTML (meetings.municode.com/adaHtmlDocument) and PDF (Azure blob storage). The meeting list doesn't: the portal drops scripted connections, and there's no RSS. Phase 4 needs another way to learn new meeting ids: the portal's own data calls (look in browser dev tools) or the town's Notify Me emails |
| Monte Sereno | iCompass CivicWeb (montesereno.civicweb.net) | Found, not yet tested |
| Mountain View-Los Altos HSD | iCompass / Diligent (mvla.ic-board.com, mvlapub.ic-board.com) | Found by search, not tested |
| Milpitas Unified | AgendaOnline (per its website) | Not tested |
| Gilroy Unified | AgendaOnline (gilroy.agendaonline.net); also links Simbli | Confirm which is current |
| Housing Authority of the County of Santa Clara | Granicus (hacsc.granicus.com) | Found, not tested |
| Midpeninsula Regional Open Space District | Laserfiche portal | Found, not tested |
| Campbell City Council | eScribe (pub-campbell.escribemeetings.com) | Found and tested Oct 14, 2026; no adapter yet. Details below |

Still unknown: Lakeside Joint SD, El Camino Healthcare District, Santa Clara County Central Fire.

#### Campbell City Council on eScribe

Campbell's council agendas have been on eScribe since October 2025, at `https://pub-campbell.escribemeetings.com`. The city's AgendaCenter page shows that portal in a frame above the old category list, but the council's own page (`/189/City-Council`) still links its "Most Recent Agenda" to the AgendaCenter category, which gives the Oct 7, 2025 packet. That packet was made by eScribe and names the portal on its first page, which is how it was found. The commissions stay on AgendaCenter; the portal has only council meetings.

The host returns 404 for `/robots.txt`, so the usual one request a second applies. What an adapter could rely on (checked Oct 14, 2026):

- `POST /MeetingsCalendarView.aspx/GetCalendarMeetings` with the JSON body `{"calendarStartDate": "2025-09-01", "calendarEndDate": "2026-12-01"}` lists the meetings in that window as JSON (`{"d": [...]}`; 50 meetings and 225 KB for those 15 months). It's the call the portal's own calendar makes. Each meeting has an `ID` (a GUID), `MeetingName` (regular session, executive session, study session or special meeting), `StartDate` in local time (`2026/10/06 19:00:30`), `Location`, `HasAgenda` and `MeetingDocumentLink`: the agenda PDF, the full packet as PDF and as HTML, minutes and video. `Fetcher` only does GET, so an adapter needs a small POST helper there.
- `/Meeting.aspx?Id={ID}&Agenda=Agenda&lang=English` is the full agenda as HTML, item by item: number, title, attachments and recommended action. The Oct 6, 2026 regular meeting had 24 items and 52 attachment links in 205 KB. That's real item text, unlike Campbell's AgendaCenter packets.
- Attachments and agenda PDFs are at `/FileStream.ashx?DocumentId=`.
- The first meeting listed is Oct 7, 2025, the same meeting as the last one in the AgendaCenter category. The latest is Oct 6, 2026. The next regular meeting is Oct 20, 2026, and agendas go up the Friday before.

## What this means for the collector

- **User agent.** Use `Mozilla/5.0 (compatible; HaystackNeedle/<version>; +<repo URL>)`. It's the standard honest-bot format, it names the project and where to reach it, and BoardDocs rejects anything else outright.
- **Rate.** One request a second is the floor, but honor each host's `Crawl-delay`: 60 s for IQM2. Feeds and APIs first, pages second.
- **robots.txt is per host, not per platform.** Simbli's robots.txt URL redirects to an error page; Legistar's and CivicClerk's API hosts have none.

## Open question: BoardDocs and PrimeGov

The scope says to honor robots.txt. BoardDocs and PrimeGov both say no to every bot that isn't a search engine. Together that's 15 bodies:

- **BoardDocs (13):** the County Board of Education, all four community college districts, East Side and Campbell high school districts, Palo Alto Unified and five elementary districts.
- **PrimeGov (2):** Palo Alto City Council and its commissions, and the Open Space Authority.

Options, none needed before phase 4:

1. **Leave them out of the scraper** and cover them by subscribing the alert address to each body's own email notices, then matching those emails. Slower (it announces that an agenda exists, not what's inside it) but within the rules.
2. **Ask.** Email PrimeGov/Palo Alto's clerk and the BoardDocs districts that matter most (County Board of Education first) for permission to fetch their agendas at a low rate. A yes in writing lets the adapter run.
3. **Fetch anyway at a low rate**, treating robots.txt as aimed at crawlers rather than a scheduled reader of public agendas. That would break the scope's own rule, so it shouldn't happen without deciding to change that rule.
