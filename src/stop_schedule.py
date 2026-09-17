#!/usr/bin/env python3
"""Bus times at each origin stop, for writing map callouts by hand.

Task 5's callouts ("Last bus left 7:46pm") are hand-written in config.yml, and every
time in one has to come from the feed, not memory. This prints what the feed says for
each origin stop and each scenario: the first and last bus that day, and every departure
inside the scenario's departure window, grouped by route and direction.

Two things these facts do NOT tell you -- read before writing a callout:
  - They cover the origin stop only. The maps also count buses at any stop within the
    walk limit (about half a mile), so a stop with no buses can still have a map that
    reaches places. UNL East Campus on Saturday is the example: no buses at its stop,
    yet its map reaches ~10 sq mi.
  - Departures include both directions and every route. That's why they're grouped by
    direction: a callout should say "last bus left 7:46pm" or "a bus every hour", not
    a raw count.

Scenario dates, times and windows come from config.yml, and origins from
data/origins.csv, so the output follows any change to either. Rerun after the feed
updates.

Usage:
    python src/stop_schedule.py                    # every origin
    python src/stop_schedule.py --origin bryan-east
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import pandas as pd

from build_network import load_config
from compute_isochrones import read_origins, resolve_scenarios
from premise_check import gtfs_time_to_seconds


def clock(seconds: float) -> str:
    """12-hour clock time. GTFS times past midnight are marked as the next day."""
    hours, minutes = divmod(int(seconds) // 60, 60)
    suffix = " (next day)" if hours >= 24 else ""
    hours %= 24
    return f"{(hours - 1) % 12 + 1}:{minutes:02d}{'am' if hours < 12 else 'pm'}{suffix}"


def load_departures(gtfs: Path, stop_ids: set[str]) -> pd.DataFrame:
    """Every departure at the given stops, with route number and direction."""
    with zipfile.ZipFile(gtfs) as zf:
        def read(name: str) -> pd.DataFrame:
            with zf.open(name) as fh:
                return pd.read_csv(fh, dtype=str, encoding="utf-8-sig")
        stop_times, trips, routes = read("stop_times.txt"), read("trips.txt"), read("routes.txt")

    deps = stop_times[stop_times["stop_id"].isin(stop_ids)]
    trip_cols = ["trip_id", "route_id", "service_id"]
    trip_cols += [c for c in ("trip_headsign",) if c in trips.columns]
    deps = deps.merge(trips[trip_cols], on="trip_id")

    route_numbers = dict(zip(routes["route_id"], routes["route_short_name"]))
    deps["route"] = deps["route_id"].map(route_numbers).fillna(deps["route_id"])
    deps["dep_s"] = deps["departure_time"].map(gtfs_time_to_seconds)

    # Direction: a stop-level headsign wins over the trip's, when the feed gives one.
    toward = pd.Series(pd.NA, index=deps.index, dtype="object")
    for col in ("trip_headsign", "stop_headsign"):
        if col in deps.columns:
            present = deps[col].fillna("").str.strip() != ""
            toward = deps[col].where(present, toward)
    deps["toward"] = toward.fillna("(no direction given)")
    return deps


def route_sort_key(route: str) -> tuple:
    return (not route.isdigit(), int(route) if route.isdigit() else 0, route)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origin", help="show only this origin slug (see data/origins.csv)")
    args = ap.parse_args()

    cfg = load_config()
    gtfs = Path(cfg["paths"]["gtfs"])
    origins = read_origins(Path(cfg["paths"]["origins"]))
    if args.origin:
        chosen = origins[origins["slug"] == args.origin]
        if chosen.empty:
            sys.exit(f"ERROR: no origin '{args.origin}'. Choose from: "
                     + ", ".join(origins["slug"]))
        origins = chosen

    scenarios, problems = resolve_scenarios(cfg, gtfs)
    for p in problems:
        print(f"WARNING {p}")

    deps = load_departures(gtfs, set(origins["stop_id"]))

    for o in origins.itertuples(index=False):
        at_stop = deps[deps["stop_id"] == o.stop_id]
        routes = sorted(at_stop["route"].unique(), key=route_sort_key)
        print("=" * 74)
        print(f"{o.name}  [{o.slug}]")
        print(f"  stop {o.stop_id}, {o.stop_name}; routes {', '.join(routes) or 'none'}")
        print("=" * 74)

        for s in scenarios:
            services = set(s["active_service_ids"])
            print(f"\n  {s['label']}  [{s['id']}]  {s['departure']:%a %b %d}")
            if not services:
                print("    no StarTran service this day (its callout is added automatically)")
                continue
            day = at_stop[at_stop["service_id"].isin(services)]
            if day.empty:
                print("    no buses at this stop all day")
                continue
            print(f"    first bus {clock(day['dep_s'].min())}, last bus {clock(day['dep_s'].max())}"
                  f"  ({len(day)} departures, both directions)")

            start = s["departure"].hour * 3600 + s["departure"].minute * 60
            end = start + s["window_minutes"] * 60
            window = f"{clock(start)}-{clock(end)}"
            in_window = day[(day["dep_s"] >= start) & (day["dep_s"] < end)].sort_values("dep_s")
            if in_window.empty:
                before, after = day[day["dep_s"] < start], day[day["dep_s"] >= end]
                note = f"    no buses {window}"
                if len(before):
                    note += f"; the last one before left at {clock(before['dep_s'].max())}"
                if len(after):
                    note += f"; the next one leaves at {clock(after['dep_s'].min())}"
                print(note)
                continue
            print(f"    departures {window}:")
            for (route, toward), group in in_window.groupby(["route", "toward"], sort=False):
                times = ", ".join(clock(t) for t in group["dep_s"])
                print(f"      route {route} toward {toward}: {times}")
        print()

    print("Before writing a callout:")
    print("  - These are times at the origin stop only. The maps also use any stop within")
    print("    about half a mile, so a stop with no buses can still reach places.")
    print("  - Departures cover both directions and every route. Write last-bus times or")
    print("    frequency (\"a bus every hour\"), not raw counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
