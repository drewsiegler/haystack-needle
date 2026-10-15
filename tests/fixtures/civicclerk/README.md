Real CivicClerk API responses saved on Oct 14, 2026 (fetched with the project's user agent, one request a second).

- `*_events_*.json`: `/v1/Events/GetEventsList(...)` answers, saved on Oct 15, 2026 after the plain `/v1/Events` list stopped answering. The `_page1`/`_page2` pair is Los Altos Hills for Oct 12 to Nov 13, 2026 (`skip=0` and `skip=15`) cut to three events each; page 1 keeps `hasMore: true`.
- `*_meeting_*.json`: `/v1/Meetings/{agendaId}` answers. The big agendas keep every section heading but only the items the tests use. `milpitasca_meeting_2129_unpublished.json` is whole: an agenda that isn't published yet comes back with no items.
- Signed blob links (`pdfVersionFullPath`, `pdfMediaFullPath`) have their query string replaced with `?REDACTED`. The adapter doesn't use them; they carry a week-long signature.
