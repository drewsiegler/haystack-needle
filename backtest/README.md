# Backtest

[cases.yaml](cases.yaml) lists past fights and the agendas each one appeared on, oldest first. It's how the app proves it works: `backtest/run.py` (built in phase 2) replays each case's agendas through the matcher and reports which items were caught, at which stage and from where.

## Titles-only dry run, Oct 14, 2026

Before any code, each case's first-appearance title was checked against the watchlist with a throwaway script. It checked titles only: no item text and no attachments, so a miss here could still be caught by a staff report.

| Case | Watchlist | First appearance | Starting watchlist | After Oct 14 changes |
| --- | --- | --- | --- | --- |
| Saratoga Sheriff vs. Los Gatos police | policing-contracts | Jan 21, 2026, City Council | Yes | Yes |
| County surveillance policy (Flock) | surveillance | Jan 27, 2026, Board of Supervisors | Yes | Yes |
| Los Altos Hills ends Flock | surveillance | Jan 15, 2026, City Council | Yes | Yes |
| Mountain View ends Flock | surveillance | Feb 24, 2026, City Council | Yes | Yes |
| Cupertino ALPR program | surveillance | Mar 1, 2023, TICC | No: "License Plate Recognition" | Yes |
| Santa Clara Flock purchase | surveillance | Dec 3, 2024, consent calendar | Yes | Yes |
| San José ALPR rules | surveillance | Mar 10, 2026, City Council | Yes | Yes |
| San José immigration enforcement | immigration | Oct 28, 2025, City Council | No: "Civil Immigration Enforcement" | Yes |
| Sunnyvale immigration enforcement | immigration | Mar 10, 2026, colleagues' memo | No | Yes |
| Sunnyvale tenant protections | tenants | Apr 13, 2026, Planning Commission | No: "Tenant Protections" (plural) | Yes |
| Mountain View tenant anti-harassment | tenants | Mar 27, 2025, Rental Housing Committee | No | Yes |

The starting watchlist caught 6 of the 11 cases by title, and every miss had the same cause: the watchlist's phrase wasn't the phrase local staff use. After the changes below, all 11 match by title. The San José Mayor's Sept 30 memo, "Internal Controls for Automatic License Plate Readers", now matches too.

Two items still match nothing by title: Saratoga's "Law Enforcement Town Hall" and its Oct 7 decision, "Law Enforcement Discussion and Agreement Determination". The Oct 7 item text does match: "LGMSPD", "Los Gatos Monte Sereno" and "Sheriff's". Item text has to be read, not just titles.

## Changes made Oct 14, 2026

**Matcher** (in [docs/scope.md](../docs/scope.md), Matching rules and build prompt task 3)
- The last word of a term may be plural: "license plate reader" matches "License Plate Readers". Terms are written in the singular, since a plural term doesn't match the singular.
- A hyphen, with or without spaces around it, counts as a space. PDF text gives "Los Gatos - Monte Sereno", "Los Gatos -Monte Sereno" and "Los Gatos Monte Sereno".

**Terms** (in [watchlists.yaml](../watchlists.yaml))
- surveillance: added `automatic license plate`, `license plate recognition`.
- policing-contracts: added `law enforcement service` and `LGMSPD`; dropped `law enforcement services agreement`, which never appears in Saratoga's agendas and is covered by `law enforcement service`.
- immigration: added `immigration enforcement`, the phrase in every 2025–26 city policy here (San José, Sunnyvale, Mountain View, Santa Clara). The case-sensitive `ICE` rule holds up: 624 San José titles since Jan 2025 contain the letters "ice", and none has the word "ICE".
- tenants: added `tenant anti-harassment`, `tenant anti-retaliation`, `tenant relocation`, `eviction prevention`.

## Still open

- `sheriff` catches Saratoga at every stage but will also fire on most Board of Supervisors agendas. One fix is to limit that term to city bodies, which needs per-term body limits the watchlist format doesn't have yet. Decide after phase 3 shows how noisy it is.
- Check whether the county's ALPR policy for contract cities was on a 2025 agenda (the IQM2 calendar read starts in Jan 2026).
- Look for an earlier Los Altos Hills Flock item (Jan 15 was listed under Ongoing Business).
- Confirm Saratoga's Oct 7, 2026 vote in the minutes.
- Add a case for a vague title where only the attachment names Flock, if one turns up.
