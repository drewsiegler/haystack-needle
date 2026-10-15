# Where the agendas live

Phase 1 source check, done Oct 14, 2026. It replaces the platform table in [scope.md](scope.md). Each body was checked by fetching its agenda page or API with the project's user agent, one request a second or slower, after reading the host's robots.txt.

## The short version

**School boards are shelved for a later release** (decided Oct 15, 2026): v1 doesn't read K-12 districts or the community college districts, since so many of them sit behind robots.txt or bot checks. The County Board of Education is the exception: it stays in v1, read from its agenda emails. That leaves Simbli, BoardDocs, Diligent Community, Boardable and ElectronicSchoolBoard out of v1; what was learned about them stays below for the later release.

Ten adapters were built for about 53 bodies, and v1 runs six of them (Legistar, IQM2, CivicClerk, CivicPlus, eScribe and Midpen's own website). Four of them read an official JSON API or a plain agenda page and need no scraping tricks. The hard part isn't the platforms, it's **robots.txt**: BoardDocs, Diligent Community and PrimeGov tell every bot except search engines to stay out. On Oct 15, 2026 that covered 11 bodies, including the County Board of Education, three community college districts and Palo Alto, and Drew decided to cover them through their own agenda emails instead of scraping (see [Agenda emails](#decided-agenda-emails-for-boarddocs-diligent-community-and-primegov)).

| Adapter | Bodies | How it reads | robots.txt | Phase |
| --- | --- | --- | --- | --- |
| Legistar | 6 | Official Web API, JSON | none (404) | 2 |
| Granicus IQM2 | 3 (2 in v1) | HTML calendar + RSS feed, PDFs | `Crawl-delay: 60` | 3 |
| CivicClerk | 5 | Public OData API, JSON (agenda items and attachments) | none (404) | 4, built |
| CivicPlus AgendaCenter | 3 | Agenda search page + PDFs | `Disallow: /Search`, `/RSS.aspx` (not `/AgendaCenter/`) | 4, built |
| eScribe | 1 | Meeting calendar (JSON POST) + HTML agenda, PDFs | none (404); a Cloudflare bot check on files | 4, built |
| Simbli (eBoardSolutions) | 21 (15 confirmed) | Meeting list (HTML) + agenda JSON, one request per item | none (the URL is an error page); **an Imperva bot check** | later release (schools); stopped by a bot check |
| BoardDocs | 6 (1 in v1) | Their own agenda emails | **disallows all but search engines** | County Board of Education in v1 by email; the rest later (schools); emails requested |
| Diligent Community | 3 | Their own agenda emails | **disallows all** | later release (schools); emails requested |
| PrimeGov | 2 | Their own agenda emails (the public API works, but robots.txt says no) | **`Disallow: /`** | 4, by email (planned) |
| iCompass ElectronicSchoolBoard | 1 | RSS feed + agenda and item pages, PDFs | none (404) | built; later release (schools) |
| Boardable | 1 (4 of its groups) | Public site pages, which carry their data, + PDFs from S3 | the site allows every bot; **the app host and its JSON API disallow all** | built; later release (schools) |
| Midpen's website (Drupal) | 1 (its board and committees) | Public meetings table + meeting pages, PDFs | Drupal's stock file; meeting pages and files allowed | 4, built |
| One-offs | 5 | Municode Meetings, CivicWeb, AgendaOnline, Granicus | varies | later |

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
| Fremont Union High School District (not read: shelved with the schools) | fremontunionhighschoolca.iqm2.com |

IQM2's robots.txt asks for **one request a minute** (`Crawl-delay: 60`). The RSS calendar is at `/Services/RSS.aspx?Feed=Calendar` (`/Citizens/RSS.aspx` is an error page); on Oct 14, 2026 it worked for VTA but showed the error page for the county, and it lists only meetings with published documents. So the adapter reads `/Citizens/Calendar.aspx?From=MM/DD/YYYY&To=MM/DD/YYYY` instead: one request per portal per run, about 215 KB for the county's 32-day window (without the dates it's the whole year, 1.6 MB and about 800 meetings). Each meeting's `Detail_Meeting.aspx?ID=` page has the whole agenda in one request: items, their "possible action" notes, legislative file links and attachments. The adapter keeps those outlines in the state and reads a page again only when its calendar row changes, or after 110 minutes in the 3 days before the meeting and a day before that.

A run is one thread, so every minute waiting on IQM2 holds up the rest. A run may spend its first 25 minutes waiting on IQM2; after that, an IQM2 request that would have to wait is left for the next run, while one whose minute passed during other work still goes out. That leaves Legistar at least 15 of the run's 40 minutes. Measured on Oct 14, 2026, a first run with an empty state made 31 IQM2 requests in 24 minutes (both calendars, all 20 posted agendas, then staff reports and attachments, soonest meetings first) and left 343 for later runs, about a day's worth. After that a run reads only what's new. For scale, the January 27, 2026 Board agenda had 81 legislative files and 148 attachments.

Gilroy's site still links an old `gilroyca.iqm2.com` portal; it has moved to CivicClerk.

### CivicClerk (phase 4, built)

Los Altos moved its current meetings to CivicClerk (found by hand on Oct 14; losaltosca.gov itself returns 403 to scripts). Its older agendas stay in a CivicPlus AgendaCenter archive with RSS and Notify Me, which the app doesn't need. Each city has an open OData API at `https://{tenant}.api.civicclerk.com/v1/`. `Events/GetEventsList(...)` lists meetings; `Meetings/{agendaId}` returns the agenda as nested items with their attachments. Tested on the Los Altos Hills Jan 15, 2026 council agenda, which listed the Flock termination item by title.

All five API hosts return 404 for `/robots.txt`, so the usual one request a second applies. What the adapter relies on (checked Oct 14, 2026):

- `Events/GetEventsList(fromDate='YYYY-MM-DD',toDate='YYYY-MM-DD',categoryIds=[],skip=0,sortDescending=false,cursorId=0)` lists the published meetings, oldest first. It's what the public portal asks for. `toDate` itself is left out, so the adapter asks for the day after its window. The answer is paged: Los Altos Hills gave 15 rows and `hasMore: true` for a 32-day window, and `skip=15` gave the other 7. Times are local, written with a `Z`. The records are the old ones cut down: same ids, times, agenda ids and published files, with `categoryId` for the body (the same number as the old `eventCategoryId`). **Changed Oct 15, 2026:** the plain `Events?$filter=...` list the adapter used at first started answering 500 for all five cities (first seen in the 06:07 UTC run), while the portal kept working; `Meetings/{agendaId}` was unaffected.
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
| Campbell commissions | campbellca.gov/AgendaCenter | The City Council category stops at Oct 7, 2025 because the council moved to eScribe (see [eScribe](#escribe-phase-4-built)) |
| Los Altos Hills committees | losaltoshills.ca.gov/AgendaCenter | The City Council category (id 15) is skipped: CivicClerk reads that council item by item |
| Los Gatos commissions | losgatosca.gov/AgendaCenter | **Not read**: nothing posted after October 2024. The commissions moved to Municode Meetings with the Town Council (confirmed Oct 15; see One-offs) |

### eScribe (phase 4, built)

Campbell's City Council has posted its agendas on eScribe since October 2025, at `https://pub-campbell.escribemeetings.com`. The city's AgendaCenter page shows that portal in a frame above the old category list, but the council's own page (`/189/City-Council`) still links its "Most Recent Agenda" to the AgendaCenter category, which gives the Oct 7, 2025 packet. That packet was made by eScribe and names the portal on its first page, which is how it was found. The commissions stay on AgendaCenter; the portal has only council meetings, starting with that same Oct 7, 2025 meeting.

The host returns 404 for `/robots.txt`, but requests to it are 10 seconds apart because of the bot check on its files (below). What the adapter relies on (checked Oct 14, 2026):

- `POST /MeetingsCalendarView.aspx/GetCalendarMeetings` with the JSON body `{"calendarStartDate": "2025-09-01", "calendarEndDate": "2026-12-01"}` lists the meetings in that window as JSON (`{"d": [...]}`; 50 meetings and 225 KB for those 15 months). It's the call the portal's own calendar makes; `Fetcher.post_json` makes it. Each meeting has an `ID` (a GUID), `MeetingType` (regular session, executive session, study session or special meeting, all filed under the City Council), `StartDate` in local time (`2026/10/06 19:00:30`), `HasAgenda` and `MeetingDocumentLink`: the short agenda PDF, the full packet as PDF and as HTML, minutes and video. Nothing says when an agenda was posted, and a meeting isn't listed until its agenda is: on Oct 14 the window from Oct 12 to Nov 13 was empty, though the next regular meeting is Oct 20 (agendas go up the Friday before).
- `/Meeting.aspx?Id={ID}&Agenda=Agenda&lang=English` is the full agenda as HTML, item by item: number, title, description, recommended action and attachments. The Oct 6, 2026 regular meeting has 24 items on the page; without the four headings that have items under them, that's 20 items with 26 attachments. That's real item text, unlike Campbell's AgendaCenter packets. An item keeps its ID when the agenda is revised.
- A revised agenda is listed as `Merged` (`Agenda=Merged`) and replaces the first one; its attachments get new document IDs. An addendum (`Agenda=Addendum`) lists only the items it adds or changes and is read along with the agenda. One meeting of the 50 had each.
- Attachments and agenda PDFs are at `/FileStream.ashx?DocumentId=`. A replaced file gets a new ID, so each file is read once.

**A Cloudflare bot check guards the files.** On Oct 14, 2026 a test run read the calendar, three agenda pages and 15 attachment PDFs at one request a second. From the 16th file on, every download got a 403 "Verifying your browser" page (header `cf-mitigated: challenge`) that needs JavaScript and cookies, including one that had downloaded minutes earlier. Two minutes later the portal's pages loaded but its files were still refused. How long the check lasts, and whether GitHub Actions' addresses meet it sooner, isn't known. Haystack Needle doesn't try to get past a bot check: `Fetcher` leaves the host alone for the rest of the run and the files wait for the next one. Item titles, descriptions and recommended actions come from the agenda pages, so they're matched either way; the staff reports are what's at risk.

**Decided Oct 14, 2026: ask the portal less often.** Requests to `escribemeetings.com` are 10 seconds apart (`CRAWL_DELAYS` in `needle/fetch.py`), so a new agenda's 25 or so files take about four minutes, well inside the run's 25 minutes for slow hosts. Whether that keeps the check away isn't known yet; if it doesn't, each run reads files until the check refuses one. Still open: asking Campbell's clerk (or eScribe) to let the project's user agent download the files, or matching the agenda pages only and not downloading the files.

### Midpen's website (phase 4, built)

The Midpeninsula Regional Open Space District (Midpen) posts its agendas on its own Drupal website, `www.openspace.org`. Its robots.txt (checked Oct 15, 2026) is Drupal's stock file: it shuts out `/admin/`, `/search/`, `/user/` and the like, not the meeting pages or `/sites/default/files/`, and sets no crawl delay, so the usual one request a second applies. There's no feed. The Laserfiche portal (`portal.laserfiche.com`, linked from the meetings page) holds older records and wasn't needed. What the adapter relies on:

- `/get-involved/public-meetings` lists this year's meetings in one table, upcoming first (47 rows on Oct 15): the meeting's name ("Board Meeting", "Special Board Meeting", "Action Plan & Budget Committee Meeting"), date, time and page. The body comes from the name, not the page's slug, which can be wrong: `bod-20260929` is a Board Appointee Evaluation Committee meeting, and `bod-20261018` meets on Oct 28. This year's list had the board and five committees: Action Plan & Budget, Board Appointee Evaluation, Bond Oversight, Legislative, Funding & Public Affairs, and Planning & Natural Resources. The page says it lists "this year", so around New Year a meeting on the other side of Jan 1 may be missing for a few days; not checked yet.
- `/about-us/meetings/{slug}` is the meeting's page. Once the agenda is posted (72 hours ahead, 24 for a special meeting) it links the agenda PDF and the agenda packet, one PDF per item, named for the item: `Item 4 AwardofContractforSensitiveSpeciesProtectionProgram_R-26-149.pdf`. Before that the page has only the date.

Each meeting gets an "Agenda" item with the agenda PDF and one item per packet file, titled from the file's name with spaces put back and the staff report number ("Award of Contract for Sensitive Species Protection Program (R-26-149)"). The agenda PDF has the full titles and recommendations, but reading titles from it would be fragile; the matcher reads it anyway. A few titles come out run together ("Myotis Trailat BCR"): "and", "at", "in" and "on" aren't split off because they end too many words (Grassland, Habitat, Mountain). A replaced file gets a new name (`_REVISED`, `_1`), so it's read again; the item keeps its id, which is the meeting and item number.

A test on Oct 15, 2026 (`--dry-run --only midpen --full`) read the list, four meeting pages and 12 PDFs: the Oct 14 board meeting's agenda and 11 packet files; the three meetings ahead had no agenda yet. Nothing matched the watchlists; with test terms, item titles and PDF pages both matched.

### Boardable (built, shelved with the schools)

The Foothill-De Anza Community College District left BoardDocs for Boardable. Its public site is `https://site.boardable.com/foothill-de-anza-community-college-district`. Checked Oct 15, 2026 with the project's `Fetcher`, one request a second.

**Two hosts, two answers.**

- `site.boardable.com/robots.txt` is `User-Agent: * / Disallow:`, which allows every bot. The site runs on Vercel.
- `app.boardable.com/robots.txt` is `User-agent: * / Disallow: /`, allowing only `/login` and `/start-trial`. That's where the public site's own JSON calls go (`/api/public-sites/meetings?count=&page=&upcoming=&group=`, `/api/public-sites/meetings/{slug}/agenda`, with a `SiteId: 277` header), and the site's links to documents (`/{organization}/public-site-data/meetings/{slug}/documents/{id}/view`). So the adapter never requests anything from that host. Alerts link to the document viewer there, for people to click.
- `boardable-production.s3.amazonaws.com/robots.txt` answers 403, which means no rules. The attachments are there.

**What the adapter relies on.** The site is a Nuxt app rendered on the server: each page carries the data it was built from in a `<script id="__NUXT_DATA__">` block, in Nuxt's flat "devalue" form (`unflatten` in the adapter unpacks it). That's the same data the JSON calls return, so nothing is scraped from the page's HTML.

- `/{site}/meetings` lists the next 10 upcoming meetings, soonest first. Each one has its slug, its time as shown ("Thu, Oct 15, 2026 2:00 PM PDT") and its groups (Boardable's name for a board or committee). The paging details give the count of all upcoming meetings. The page ignores query strings, so later pages and the "Past" tab come only from the JSON call. On Oct 15 it listed one meeting, a student council meeting that day. Meetings seem to appear on the site when their materials are published: the Oct 5 board meeting's were published Oct 2.
- `/{site}`, the home page, lists the 4 most recent past meetings, with their times in UTC but without their groups.
- `/{site}/meetings/{slug}` is one meeting, about 430 KB. It has the meeting's groups, its time, whether its materials are published, and its whole agenda as a tree. The Oct 5, 2026 regular meeting has 15 sections, 34 items and 26 PDF attachments. Each item has its title and its text (recommended action, background, who submitted it, whether there's backup). Each item has an `id` that changes with each agenda version (that agenda was version 3) and a `logical_id` that doesn't, so the adapter uses `logical_id`. The page also names the meetings just before and after it (`previous_slug`, `next_slug`, of any group). The adapter uses those to reach upcoming meetings past the first 10, or past meetings older than the last 4. `/{site}/meetings/{slug}/agenda` loads its agenda in the browser, from the JSON call, so it isn't used.
- Each attachment has a `preSignedUrl`: a link to a PDF of the file on S3, good for 20 minutes (`X-Amz-Expires=1200`). One was downloaded with `Fetcher` without trouble. The run downloads that link, and an alert links the attachment's viewer. The run reads every adapter's meetings before it downloads anything. If it gets to a Boardable file after the link has expired, S3 refuses it, and the next run tries again with a fresh link. An attachment keeps its stored file name (`file`) across agenda versions, so a file is read once.
- There's no agenda PDF to link to (its download is on the app host), so an alert links the meeting's page instead. It also has no public comment line, which comes from the agenda PDF.

A run asks for the two list pages, then one page per meeting in the window. That's each meeting of a group it reads, plus any past meeting in the window, because the home page doesn't name its groups. `Fetcher` takes Vercel's `x-vercel-mitigated` header (a challenge or a block) as a bot check and leaves the site alone for the rest of the run. None came up in the 23 requests made while building the adapter. On Oct 14, a dry run's window (Oct 12 to Nov 13) held no meeting of a group it reads, because the next regular board meeting, Nov 9, wasn't on the site yet. With the window reaching back 10 days, it read the Oct 5 board meeting's 34 items and 26 attachments in 7 requests.

**Which groups.** The site has ten groups. The adapter reads four: the Board of Trustees (Boardable calls it "Main Governing Board"), its Audit and Finance Committee, the Citizens' Bond Oversight Committee (Measure G spending) and the Financing Corporation Board of Directors. It leaves out six:

- The Foothill-De Anza Foundation Board of Directors, the colleges' fundraising nonprofit, which isn't a district body.
- The Associated Students of Foothill College and De Anza Student Government.
- The De Anza College Academic Senate, the Foothill College Academic Senate and the Foothill College Curriculum Committee.

Those five meet about weekly during terms, and their business is mostly student events, club funding, courses and faculty matters. Anything that becomes district policy or a contract goes to the Board. To read one, add its name to `GROUPS` in `adapters/boardable.py`.

### iCompass ElectronicSchoolBoard (built, shelved with the schools)

Mountain View-Los Altos HSD's Board of Trustees, at mvlapub.ic-board.com (the public site; mvla.ic-board.com is the staff side). Neither host has a robots.txt (404), so the usual one request a second applies. Its board policies are on Simbli, which isn't read. What the adapter relies on (checked Oct 15, 2026):

- `/Reference_Library/ESB_RSS/rss.xml` lists the published agendas of the last three months: "Oct 12 2026 7:00PM Agenda", the meeting type, and the meeting id in `default.aspx?rssid=975`. An agenda shows up once it's published (Oct 12's was up by Oct 7); meetings without one aren't listed. Every entry carries the same `pubDate`, the feed's own build time, so it doesn't say when an agenda changed.
- `/public_agendaview.aspx?mtgId=975` is the agenda: each item's number, title, type (Action, Info) and a "revised" mark, linking to `/public_itemview.aspx?ItemId=...&mtgId=...`. The item ids are encrypted strings, but the same ones come back on every load.
- An item page has labelled fields (Summary, Funding, Recommendation, Enclosure, and any others the district filled in) and its PDF attachments at `/Attachments/{guid}.pdf`. The printable agenda (`/com/agenda_print.aspx`) has titles only, so the item pages are the only place for text and files.
- An agenda has about 40 items, one request each (about 40 seconds). The adapter keeps each item's fields and file list in the run's state and reads the page again only when the item's title or revised mark changes, or once a day; a second read the same day is two requests (the feed and the agenda).

### Simbli (shelved with the schools; stopped by a bot check)

Simbli is eBoardSolutions' board portal, `https://simbli.eboardsolutions.com`, with one site per district, named by a site id (`?S=`). Checked Oct 14, 2026 with the project's `Fetcher`.

**There's no robots.txt.** `/robots.txt` redirects (302) to `/Pages/ErrorPage.aspx?msg=District robots.txt not found in state GS`: the portal reads the path as a district's short name, finds none, and shows its error page with a 200. The page has no rules in it, so nothing is disallowed. The same happens on `assistive.eboardsolutions.com` (the portal's accessible copy). `app1.eboardsolutions.com`, its document service, answers 404. The company's own site, `www.eboardsolutions.com/robots.txt`, allows every bot but seven SEO crawlers.

**An Imperva bot check guards the portal, and it said no.** Every page carries Imperva's scripts (`/_Incapsula_Resource?SWJIYLWA=...` and a script at a path of random words). On Oct 14, 2026 the Fetcher made about 36 requests to the portal over about ten minutes, one a second or slower, and one meeting page was opened in a browser. Then, reading the 12 districts' meeting lists one a second, the 4th through the 12th all came back as Imperva's "Pardon Our Interruption" page: "something about your browser made us think you were a bot", with a request to turn on JavaScript and cookies. It came with an ordinary 200 status and no `cf-mitigated` header, so `Fetcher` didn't see a bot check. That's why 8 more requests went out before the survey was stopped by hand. Nothing was sent to the host after that. Haystack Needle doesn't try to get past a bot check, so no adapter was built. How long the block lasts, and how many requests Imperva allows a reader that runs no JavaScript, isn't known.

What an adapter would read. All of it worked before the check, with the project's user agent and no sign-in:

- `/SB_Meetings/SB_MeetingListing.aspx?S={site id}` is the district's meeting list as HTML: the newest 50 meetings, each with date and time, title, meeting type and its meeting id (`MID`, in the title's `ViewMeeting("{S}","{MID}","1",...)` link). Berryessa's 50 go back to June 2023, and a meeting is listed only once the district creates it (its Nov 18 meeting wasn't there yet). The list's "load more" call, `POST /Services/api/GetMeetingListing`, needs a bearer token from a cookie and an encrypted connection string from the page. 50 rows already cover the app's window, so the HTML is the simpler read.
- `/SB_Meetings/ViewMeeting.aspx?S={S}&MID={MID}` is the meeting page. An Angular app draws the agenda in the browser from JSON calls, and the page holds the encrypted ids those calls need (`sToken`, `enDID`, `enMeetingID`, `encrTZ`). They were the same in every session.
- `/Services/api/MeetingView/GetItemsTreeDTO/?sct=&endid=&enmid=&enuid=&v=` is the agenda outline as JSON: each item's number (`bulletLabel`), title, numeric `AgendaID`, encrypted `ID`, whether it has attachments, and its sub-items. Berryessa's Oct 14, 2026 regular meeting has 92 items, 64 with attachments.
- `/Services/api/GetMeeting/?endid=&enmid=&entz=` gives the meeting's title, date, place and `IsPublished`.
- `/Services/api/GetItemContents/?sct=&endid=&enmid=&enaid={item's encrypted ID}&enuid=&entz=&view=` is one item's text as JSON fields (Recommended Motion, Rationale, Financial Impact, Quick Summary / Abstract) and its attachments (`AttachmentID`, file name, type). It takes one request per item. The portal's own page also loads items one at a time (`GetMeetingItemDetailsModel`). No call returned a whole agenda's contents: the packet builder on app1 (`GetMeetingPacketTree`) answers `null` without a sign-in, and Print Agenda builds a PDF.
- `/Meetings/Attachment.aspx?S={S}&AID={AttachmentID}&MID={MID}` is the file itself, a PDF, with no session needed. The `ViewAgendaMinuteDocument.aspx?aid=` link in the app's code answered "File not found."

So a new agenda takes about 3 requests plus one per item before any file: about 95 for Berryessa's Oct 14 meeting. That's more than the portal allowed in total before the check.

**Site ids.** `POST /CoreServices/api/Search/GetSitesByStateAbbrs?sid=36030528` with the body `["CA"]` lists Simbli's 806 California sites (Berryessa isn't among them). The ids for the districts seen with meetings:

| District | Site id | Meetings checked Oct 14 |
| --- | --- | --- |
| Alum Rock Union | 36030683 | Yes: 47 listed, newest Oct 15, 2026 |
| Berryessa Union | 36030528 | Yes: 50 listed, newest Oct 14, 2026 |
| Los Altos SD | 36030305 | Yes: 50 listed, newest Sept 28, 2026 |
| Los Gatos Union | 36030309 | Bot check |
| Luther Burbank | 36030816 | Bot check |
| Mountain View Whisman (older agendas on NovusAgenda) | 36031895 | Bot check |
| Sunnyvale SD | 36030203 | Bot check |
| Union SD | 36030252 | Bot check |
| Los Gatos-Saratoga Joint Union HSD | 36030307 | Bot check |
| Morgan Hill Unified | 36030336 | Bot check |
| Loma Prieta Joint Union | 36030511 | Bot check |
| Saratoga Union | 36030439 | Bot check |
| Cupertino Union | 36031664 | Not read on Simbli. On Oct 15 the district's board page linked its "GAMUT Meetings" to `index.aspx?S=36031664` |
| Mount Pleasant | 36031934 | Not read on Simbli. On Oct 15 the district's "Access Agendas Here" linked to `simbli.eboardsolutions.com/csba/mountpleasant/`; its Aug 12, 2026 minutes are there |
| Oak Grove | 36030929 | Not read on Simbli. On Oct 15 the district said it's "transitioning from BoardDocs to GAMUT Meetings", and `ogsd.cc/boardmeetings` redirected to `SB_MeetingListing.aspx?S=36030929` |

The other six each have a Simbli site, but whether they post meetings there is still **not confirmed**: the check came before their meeting lists were read. Cambrian 36031579, Campbell Union SD 36030716, Franklin-McKinley 36030745, Orchard 36030181, San José Unified 36030421, Santa Clara Unified 36030433. GAMUT Meetings, which Cupertino Union and Oak Grove link to, is the California School Boards Association's meetings service, which runs on Simbli. The last three rows were found from the districts' own sites, with no requests to Simbli. Other county districts with Simbli sites, not checked: Campbell Union HSD 36031021, Evergreen 36031535, Fremont Union HSD 36030747, Gilroy Unified 36030249, Milpitas Unified 36030333, Mountain View-Los Altos HSD 36030803 and Lakeside Joint 36030711. Most of them post meetings elsewhere (per this page), so their sites may hold only board policies. But Cupertino Union, Mount Pleasant and Oak Grove looked that way too, so each is worth checking from the district's own site.

Options, for Drew:

1. **Ask.** Email eBoardSolutions (or a district that matters most) to let the project's user agent through Imperva at a low rate. Without that, the hourly run can't count on reading Simbli at all.
2. **Read much less, much slower.** A long crawl delay for the host, item contents read once per item and kept in the state (as IQM2's outlines are), and a cap on requests per run. Whether Imperva counts requests per minute or per day isn't known, and finding out means meeting the check again.
3. **Titles only.** The meeting list and agenda outline, about 3 requests per meeting, with no item text and no files. It may still meet the check.
4. **Leave Simbli out**, as with BoardDocs, and cover these boards another way (their notification emails).

Whichever it is, `Fetcher` now recognizes Imperva's page as a bot check (since Oct 14, 2026), so an adapter won't read the block page as a district with no meetings. It looks at the start of HTML answers only, for a page titled "Pardon Our Interruption" or Imperva's older "Request unsuccessful. Incapsula incident ID" page, raises `BotCheck` and sends nothing more to the host that run. The `_Incapsula_Resource` script alone isn't a sign: every normal page has it.

### BoardDocs (by email: the County Board of Education in v1, the others shelved with the schools)

Checked again Oct 15, 2026 from each body's own website, six still post their agendas on BoardDocs: the Santa Clara County Office of Education (County Board of Education), Palo Alto Unified, Evergreen, West Valley-Mission CCD, San José-Evergreen CCD and Gavilan CCD. They're covered by their own agenda emails (see [Agenda emails](#decided-agenda-emails-for-boarddocs-diligent-community-and-primegov)).

Seven of the 13 found in phase 1 have moved, and BoardDocs keeps their older meetings:

- East Side Union HSD (since July 1, 2026), Campbell Union HSD (since Aug 21, 2025) and Moreland: Diligent Community, below.
- Cupertino Union, Mount Pleasant and Oak Grove: Simbli, above.
- Foothill-De Anza CCD: Boardable, at `site.boardable.com/foothill-de-anza-community-college-district`, where its Oct 5, 2026 board meeting is listed. Read by the Boardable adapter, below.

`go.boarddocs.com/robots.txt` disallows every user agent (`User-agent: * / Disallow: /`) and allows only Googlebot, Bingbot, msnbot and Slurp. Separately, it returns 403 to any user agent that doesn't start with `Mozilla/5.0`.

### Diligent Community (shelved with the schools; agenda emails requested)

| Body | Portal |
| --- | --- |
| East Side Union HSD | esuhsd.community.highbond.com |
| Campbell Union HSD | cuhsd.community.highbond.com |
| Moreland SD | moreland.community.highbond.com |

Diligent makes BoardDocs too. The robots.txt on the East Side and Campbell portals (checked Oct 15, 2026) opens with `User-agent: * / Disallow: /` and then names scores of bots, so these boards are covered by their own agenda emails too.

### PrimeGov (phase 4, by email)

| Body | Portal |
| --- | --- |
| Palo Alto | cityofpaloalto.primegov.com |
| Santa Clara Valley Open Space Authority | openspaceauthority.primegov.com |

PrimeGov's public API (`/api/v2/PublicPortal/ListUpcomingMeetings`) returned Palo Alto's meetings as JSON, with publish dates for each document, even though paloalto.gov itself returns 403. But PrimeGov's robots.txt is `User-agent: * / Disallow: /`.

### One-offs

| Body | Platform | Status |
| --- | --- | --- |
| Los Gatos Town Council and commissions | Municode Meetings (losgatos-ca.municodemeetings.com) | **Can't be read as things stand** (checked Oct 15, 2026). The portal lists every Los Gatos body's meetings, but it drops a scripted connection before answering, even for robots.txt, which the app takes as a no. Each agenda is on hosts that allow scripts: an HTML agenda at `meetings.municode.com/adaHtmlDocument/index?cc=LOSGATOS&me={meeting id}` (no robots.txt) and PDFs at `mccmeetings.blob.core.usgovcloudapi.net/losgatos-pubu/MEET-Agenda-{id}.pdf` and `MEET-Packet-{id}.pdf`. But the meeting id is a random 32-character code, and nothing outside the portal lists them: Municode's `PublishPage` needs a page id the town doesn't publish (without one it answers 500), and the town calendar links only to the portal's front page. Ways in: the agenda-email route, or asking the town or Municode to let the app's user agent through, or for the PublishPage id. **Agenda emails chosen** (Oct 15): the Town Clerk answered the written request by pointing to the town's Notify Me lists, which have one "Agenda - …" list per body (see the agenda-email table). Whether those notices carry the meeting id is unknown until one arrives, and it looks unlikely: the lists keep no archive, and the town's weekly newsletter, sent with the same tool, links agendas only to the portal's front page. The portal itself offers no sign-up. The agenda inbox is on the lists (Oct 15) |
| Monte Sereno | iCompass CivicWeb (montesereno.civicweb.net) | **Blocked by robots.txt** (checked Oct 15, 2026): `User-agent: *` / `Disallow: /`, with only search engines allowed (at a 120-second crawl delay). Its calendar service (`/Services/MeetingsService.svc/meetings?from=&to=`) answers JSON with each meeting's id, type and date. It and the meeting type list were each asked once on Oct 15, in the same batch as robots.txt and before it was read; nothing since. A candidate for the agenda-email route, like BoardDocs and PrimeGov |
| Milpitas Unified | AgendaOnline (per its website) | Not tested; shelved with the schools |
| Gilroy Unified | AgendaOnline (gilroy.agendaonline.net); also links Simbli | Not tested; shelved with the schools |
| Housing Authority of the County of Santa Clara | Granicus (hacsc.granicus.com) | **Later version** (Drew, Oct 15, 2026): an appointed board. Its robots.txt allows only search engines (checked Oct 15), so it would need the agenda-email route |
| Midpeninsula Regional Open Space District | Its own website (www.openspace.org) | **Read** since Oct 15, 2026; see [Midpen's website](#midpens-website-phase-4-built). The Laserfiche portal holds older records |

Left for a later version (Drew, Oct 15, 2026), platform not yet found: El Camino Healthcare District (an elected board) and Santa Clara County Central Fire (governed by the Board of Supervisors sitting as its board; its agendas may come through the county's IQM2 site, not checked). Lakeside Joint SD is shelved with the schools.

## What this means for the collector

- **User agent.** Use `Mozilla/5.0 (compatible; HaystackNeedle/<version>; +<repo URL>)`. It's the standard honest-bot format, it names the project and where to reach it, and BoardDocs rejects anything else outright.
- **Rate.** One request a second is the floor, but honor each host's `Crawl-delay`: 60 s for IQM2. Feeds and APIs first, pages second.
- **A bot check is a no.** When a host answers with one (eScribe's files sit behind Cloudflare's, Simbli behind Imperva's), stop asking it for the rest of the run, and never try to get past it. Not every bot check says so in a header: Imperva's comes as an ordinary page with a 200 status. `Fetcher` knows both (since Oct 14, 2026 for Imperva's, by the page's title or its "Incapsula incident ID" line).
- **robots.txt is per host, not per platform.** Simbli's robots.txt URL redirects to an error page (the portal reads `robots.txt` as a district name), so it has none; Legistar's and CivicClerk's API hosts have none either. Boardable's public site allows every bot while its app host, where the site's own JSON calls go, allows none, so the adapter reads the site's pages instead.

## Decided: agenda emails for BoardDocs, Diligent Community and PrimeGov

BoardDocs, Diligent Community and PrimeGov say no to every bot that isn't a search engine, and the scope says to honor robots.txt. On Oct 15, 2026 Drew decided to leave them out of the scraper and cover their bodies through the bodies' own agenda emails, matched like any agenda. It's slower and thinner (an email may only say that an agenda is up, with a link the app can't follow), but it's within the rules. The other options were asking each body for permission to fetch, or fetching anyway, which would break the scope's rule.

Later on Oct 15 Drew shelved school boards for a later release. Eight of the thirteen bodies below are school districts; their requests had gone out, so their emails will arrive, and v1 reads only the other five: Palo Alto, the Open Space Authority, Los Gatos, Monte Sereno and the County Board of Education (kept in v1 at Drew's request). The school emails are kept for the later release.

Also on Oct 15 Drew added two towns on the same terms: Monte Sereno, whose CivicWeb robots.txt also says no, and Los Gatos, whose Municode portal refuses scripts (see [One-offs](#one-offs)). If a Los Gatos agenda email carries the meeting's Municode id, the app can read the agenda itself from meetings.municode.com, which allows scripts; the town's Notify Me notices may not (see the table).

**Where the emails go.** A mailbox used only for these emails, at Fastmail (US region). Each run reads it over JMAP, Fastmail's API, with a read-only Email token kept only in the `FASTMAIL_API_TOKEN` Actions secret: the token can read mail but not send, move or delete it, and it's never logged. The Proton alert address won't do: Proton offers IMAP only through its Bridge app, which runs on your own computer.

**How to get on each list.** California's open meeting law lets anyone ask in writing to be sent a body's agendas, and since 2022 (SB 274) a body with a website has to email the agenda, or a link to it, when asked to (Gov. Code §54954.1). The request lasts for the calendar year and has to be renewed after January 1. A body may charge for mailing, no more than its cost. Three bodies also have a sign-up form (Los Gatos's found after its clerk answered). Found Oct 15, 2026:

| Body | Platform | How to sign up |
| --- | --- | --- |
| Palo Alto City Council and committees | PrimeGov | Form: "Sign Up for City Council and Committee Agendas Mailing List" on paloalto.gov's City Council page (a Constant Contact list). **Subscribed Oct 15**: the agenda inbox is on the list |
| Moreland SD | Diligent Community | Form: "Sign-up to receive notification of each Board Meeting" on its Board Meetings page (a Google Form) |
| County Board of Education | BoardDocs | Written request; no sign-up found |
| Palo Alto Unified | BoardDocs | Written request; no sign-up found |
| Evergreen SD | BoardDocs | Written request; no sign-up found |
| West Valley-Mission CCD | BoardDocs | Written request to the Executive Assistant to the Board; no sign-up found |
| San José-Evergreen CCD | BoardDocs | Written request to the Board Clerk; no sign-up found |
| Gavilan CCD | BoardDocs | Written request; no sign-up found |
| East Side Union HSD | Diligent Community | Written request; no sign-up found |
| Campbell Union HSD | Diligent Community | Written request; no sign-up found |
| Open Space Authority | PrimeGov | Written request to the Clerk of the Board; its newsletter is general news, not agendas |
| Los Gatos Town Council and commissions | Municode Meetings (the portal refuses scripts) | Form: the town's Notify Me page (losgatosca.gov/list.aspx, CivicPlus), as the Town Clerk advised in answer to the written request. It has 16 lists, each "Agenda - " and a body: Town Council, Planning Commission, Arts and Culture, Community Health and Senior Services, Complete Streets and Transportation, Diversity Equity and Inclusion, Finance, Parks and Sustainability, and Youth Commissions, Conceptual Development Advisory, Council Policy, Development Review, Historic Preservation, and Town Pension and OPEB Trusts Oversight Committees, Library Board and Personnel Board. Each says it sends notice that a new agenda (or packet) "has been posted on the website"; staff send them by hand, since the AgendaCenter (empty since Oct 2024) has no lists of its own. **Subscribed Oct 15**: signing up needs a Notify Me account, and the list page kept asking Drew to sign in though the account was signed in, so town staff added the agenda inbox to the lists by hand. Changes to the subscription may need staff too. Every list sends from listserv@civicplus.com, so the adapter tells the body by the subject or list name, not the sender. If the notices carry no meeting id (no `me=` link or `MEET-Agenda-{id}.pdf` link), ask the clerk to include the agenda's direct link |
| Monte Sereno City Council and commissions | iCompass CivicWeb | Written request to the City Clerk; no sign-up checked |

**Steps.**

1. Drew sets up the mailbox.
2. Sign up with the forms (Los Gatos: the 16 Notify Me agenda lists), and send the other clerks a written request under §54954.1 to email each agenda to that address (a copy of the agenda rather than only a link, where they'll do it). Renew every January.
3. Build an email adapter: read new messages over JMAP, tell the body by its sender, and match the email's text and any attached agenda PDF. **Built Oct 15** (`adapters/agendamail.py`, read on every run since then), from the first email to arrive: Palo Alto Unified's notice of its Oct 22 City Schools Liaison Committee agenda, sent by a board office staff member from a pausd.org address. It was a short note with a go.boarddocs.com link and no attachment, and the meeting's date was only in the subject. The adapter never follows an email's links (BoardDocs, PrimeGov and the rest say no to bots); the alert links to one on a host the sender's line lists. Palo Alto Unified's line is shelved with the schools, so its emails wait unread.
4. Check each v1 body's first email against the adapter. `SENDERS` has one line per sender; until a body's first email arrives, its line uses the body's own domain (paloalto.gov, openspaceauthority.org, cityofmontesereno.org, sccoe.org; Los Gatos's lists send from listserv@civicplus.com). Mail from a sender not on the list is left unread and its domain named in the run's log; add or fix the line, and the email is read on the next run if it came in the last 45 days. Every list formats its emails differently, so check the body name and meeting date the adapter takes from it, and add the email, trimmed of personal details, to `tests/fixtures/agendamail/`.
