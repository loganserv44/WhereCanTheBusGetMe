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

## Street network (Task 2)

| | |
| --- | --- |
| Source | Geofabrik Nebraska extract, SHA-256 `22919b17…f2f7` (see `data/raw/MANIFEST.json`) |
| Clip tool | osmium-tool 1.19.1, `extract --strategy complete_ways` |
| Clip box (W, S, E, N) | `-96.84854, 40.68099, -96.53426, 40.92480` |
| How the box was set | Extent of all 811 GTFS stops, padded by 5 km |
| Box size | ~26.5 × 27.0 km (714 km²) |
| Clipped extract | 11.0 MB: 1,410,694 nodes, 214,838 ways, 6,485 relations |
| Routing engine | R5 v7.5.1 via r5py 1.1.7, on OpenJDK 25.0.2 (Azul Zulu) |
| Network extent as built | `-96.91052, 40.59033, -96.46381, 41.01681` |

**Why the box comes from the stops.** A hand-drawn city boundary would have to be
maintained by hand. The stop extent follows the feed: if StarTran extends a route, the
next run's clip grows with it.

**Why a 5 km buffer.** Walk legs are capped at about 800 m, so a stop near the edge only
needs roughly a kilometre of surrounding streets. The extra margin costs almost nothing
(the clip takes seconds) and rules out a street network truncated at the boundary, which
would make edge trips look slower or unreachable without raising any error.

**Why `complete_ways`.** It keeps every road that crosses the box edge whole, instead of
cutting it at the boundary. That is why the network R5 built extends past the clip box.
This is expected, and it means the destination grid is laid out over the clip box, not
over the network's extent.

## Destination grid (Task 2)

Travel time is computed from each origin to the centre of every cell in a regular grid.

| | |
| --- | --- |
| Cell size | 150 m square |
| Projection | UTM zone 14N (EPSG:32614), which is metric and nearly distortion-free over Lincoln |
| Coverage | Every cell whose centre falls inside the clip box |
| Cells | 31,922 |
| Within 800 m of a stop | 8,765 (27%) |
| Within 1 km of a stop | 9,994 (31%) |
| Within 2 km of a stop | 13,895 (44%) |

**Stable cell ids.** A cell's id comes from its position on a fixed 150 m lattice
(`id = row × 100000 + column`), not from a running count. The same patch of ground keeps
its id if the box is later resized, so results from different runs can be joined.

**Distance to nearest stop.** Each cell records its straight-line distance to the nearest
stop. Walk legs are capped at about 800 m, so most of the box can't be reached by
transit. It is kept in the grid so it can be excluded by filtering later rather than by
rebuilding the grid.

## Network verification (Task 2)

A network that builds without errors can still route badly. A truncated street clip,
stops that fail to link to streets, or a timezone mix-up all produce bad travel times
with no error. So the network was checked against trips with known right answers
(`python src/verify_network.py`), departing Tuesday 2026-09-15 at 08:00, with walking at
4.8 km/h and walk legs capped at 10 minutes.

| Trip | Expected | Result |
| --- | --- | --- |
| Downtown (11th & L) → Nebraska Union, ~850 m | Walking is fastest | Walk 1,158 m, **14.8 min**. The earliest bus option (route 25 Vine) arrives at 17.8 min. |
| Downtown (11th & L) → East Campus (N 33rd & Holdrege), ~3.5 km | Transit is fastest | **28.7 min** door to door: walk 538 m, route 42 Bethany for 4.6 km, walk 446 m. Walking the whole way takes 58.5 min. |

The walking time checks out independently. The two points are 716 m apart north–south
and 469 m east–west, so walking downtown's street grid covers about 1.19 km, which is
14.8 minutes at 4.8 km/h.

**How trips were compared.** Each option was timed door to door, from the 08:00
departure to arrival. R5 returns options that start at different moments within the
departure window, and time spent at the origin before an option's first leg belongs to
no leg. A first version of this check added up the legs instead. That made a two-bus
option to the Nebraska Union look like 8 minutes, and the check "failed". The network
was right; the measurement was wrong.

**One origin to the whole grid.** With the settings Task 3 will use (a 60-minute
departure window, median travel time), routing from downtown to all 31,922 cells took
2.0 seconds. At Tuesday 08:00:

| Within | Cells | Area |
| --- | --- | --- |
| 15 min | 119 | ~3 km² |
| 30 min | 1,166 | ~26 km² |
| 45 min | 3,539 | ~80 km² |
| 60 min | 5,476 | ~123 km² |

The 15-minute figure matches walking alone: 15 minutes on a street grid covers a
diamond of about 3 km². That's expected, since the median over the window includes
waiting for a bus.

No reachable cell was more than 781 m from a stop, so every reachable cell fell within
the 800 m walk limit. Trimming the grid to cells near stops would therefore lose
nothing. It would also save nothing at 2 seconds per origin, so the full grid is kept.

## Routing parameters

*Filled in during Task 3.*

Two r5py defaults differ from this methodology and must be overridden on every routing
call, or travel times will be quietly wrong:

- **Walking speed** defaults to 3.6 km/h. The methodology specifies **4.8 km/h**
  (`speed_walking=4.8`).
- **Departure time window** defaults to 10 minutes. The methodology specifies **60
  minutes** (`departure_time_window=timedelta(minutes=60)`).

## Grid and rendering

*Filled in during Tasks 2 and 5.*

## Limitations

- **Scheduled service only.** All travel times come from the published timetable, not
  observed vehicle performance. A GTFS-RT feed exists but is not used. Real trips are
  affected by traffic, weather, breakdowns, and missed connections; treat these maps as
  the optimistic case.
- Analysis reflects feed version `20260828` and will drift as StarTran revises schedules.
