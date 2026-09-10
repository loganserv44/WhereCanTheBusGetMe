# Methodology

> **Status: in progress.** Task 0 (premise verification) is complete and recorded
> below. The routing, grid, and rendering sections are filled in as those tasks run.
> See [PLAN.md](PLAN.md) for the task breakdown.

## Data provenance

| | |
| --- | --- |
| Feed | StarTran static GTFS |
| Source | `https://startran.connexionz.net/rtt/public/resource/gtfs.zip` |
| Publisher | Connexionz Ltd (agency-hosted; no account or API key required) |
| `feed_version` | `20260828` |
| Server `Last-Modified` | 2026-08-28 14:42:58 UTC |
| Downloaded | 2026-09-10 |
| SHA-256 | `289625b154248c981b534ff007e2a788b07fd23944873b0c3a1ef8fac2354d4c` |
| Size | 395,765 bytes |
| Agency timezone | `America/Chicago` |
| Declared validity | 2026-08-27 → 2027-12-31 |

All findings below come from this exact feed version. Re-running
`python src/premise_check.py` reproduces them.

## Task 0 — premise verification

The project's central claim is that StarTran runs no Sunday service, which is why the
Sunday panel is blank. That claim was verified before any pipeline code was written.

### Sunday service: confirmed absent

Checked two independent ways, because either alone is insufficient:

**(a) Weekly service patterns (`calendar.txt`).** The feed defines exactly two service
patterns, and neither runs on Sunday:

| `service_id` | Days | `sunday` | Valid |
| --- | --- | --- | --- |
| `1` | Mon–Fri | `0` | 2026-08-27 → 2027-12-31 |
| `2` | Saturday | `0` | 2026-08-27 → 2027-12-31 |

**(b) Date exceptions (`calendar_dates.txt`).** A feed can show no Sunday service in the
weekly bitmask and still add service on specific Sunday dates — a holiday shuttle, a
game-day route. Checking only the bitmask would miss that. This feed contains exactly
one exception:

| `service_id` | Date | Weekday | Type |
| --- | --- | --- | --- |
| `1` | 2026-09-07 | Monday | **removed** (Labor Day) |

There are **zero service-adding exceptions** in the feed, so no Sunday service can enter
by that route either.

**Conclusion: the premise holds.** The blank Sunday panel is factually justified.

### Network size

| | Premise said | Actual |
| --- | --- | --- |
| Routes | ~18 | **18** |
| Stops | ~1,022 (per Transitland listing) | **811** |
| Trips | — | 1,396 |

The Transitland stop count appears to be from an older feed version; 811 is what this
feed contains.

### Service spans

Network-wide, across all routes. Times are first departure and last arrival anywhere on
the system. GTFS times beyond `24:00:00` were handled as belonging to the prior service
day; this feed contains none, so no trips run past midnight.

| Day type | Trips | First departure | Last arrival |
| --- | --- | --- | --- |
| Weekday (Mon–Fri) | 1,101 | 05:40 | 21:50 |
| Saturday | 295 | 06:40 | 19:35 |
| **Sunday** | **0** | — | — |

**These differ from the figures originally assumed in the plan** (weekday
~5:15am–9:55pm, Saturday ~5:55am–7:05pm). The corrected numbers are above. The
differences are modest and do not affect the project's argument — service still
collapses in the evening, shrinks further on Saturday, and is absent Sunday.

### Per-route spans — the network-wide figure is misleading

"Service until 9:50pm" describes the last bus *anywhere on the system*, not what a rider
can actually use. Counting routes still running at a given hour:

| Weekday | Routes still operating (of 18) |
| --- | --- |
| after 21:00 | **8** — Vine, Heart Hospital, Havelock, University Place, Holdrege, South 13th, North 27th, O Street |
| after 21:30 | **3** — South 13th, North 27th, O Street |

| Saturday | Routes still operating (of 14 running that day) |
| --- | --- |
| after 18:30 | **7** |
| after 19:00 | **2** — University Place, Heart Hospital |

Only 14 of the 18 routes run on Saturday at all. The four weekday-only routes are NIC
City, Vine, Holdrege, and Downtown Trolley.

### Consequences for scenario selection

Two findings here constrain Task 3 and are recorded now so the choice is deliberate:

**1. The evening departure windows extend past the end of service.** The methodology
computes median travel time across a 60-minute departure window. For the planned
scenarios:

- **Tue 9:00pm** → window runs 21:00–22:00, but the last bus arrives anywhere at 21:50.
  Most of the window has no service at all.
- **Sat 6:00pm** → window runs 18:00–19:00, but only two routes operate past 19:00.

A median across a window that is half-empty is not wrong, but it reports something
different from what it reports at 8am, where the window is uniformly served. This needs
an explicit decision before Task 3 — see PLAN.md's open items.

**2. The feed's holiday coverage is unreliable beyond the near term.** The declared
validity window runs 16 months (to 2027-12-31), yet contains a single holiday exception
(Labor Day 2026). Thanksgiving, Christmas, and July 4th are absent. Either StarTran runs
normal service on those days, or — more likely — the feed only carries near-term
exceptions and will be revised. **Scenario dates should therefore be chosen close to the
feed's start date and screened for holidays manually**, rather than trusting
`calendar_dates.txt` to flag them.

## Routing parameters

*Filled in during Task 3.*

## Grid and rendering

*Filled in during Tasks 2 and 5.*

## Limitations

- **Scheduled service only.** All travel times come from the published timetable, not
  observed vehicle performance. A GTFS-RT feed exists but is not used. Real trips are
  affected by traffic, weather, breakdowns, and missed connections; treat these maps as
  the optimistic case.
- Analysis reflects feed version `20260828` and will drift as StarTran revises schedules.
