#!/usr/bin/env python3
"""Task 2 verification -- does the network actually route?

Building the network without an error proves R5 accepted the inputs. It does not prove
that trips come out sensibly: a bad street clip, a timezone mix-up, or stops that failed
to link to streets can all leave trips that are unreachable or absurdly slow, with no
error at all. So this checks real trips against what a Lincoln rider would expect.

  1. Downtown (11th & L) -> Nebraska Union (14th & R), ~850 m apart.
     City Campus borders downtown, so the right answer is "walk". This checks the
     walking network. If a bus beats walking here, something is wrong.

  2. Downtown (11th & L) -> UNL East Campus (N 33rd & Holdrege), ~3.5 km.
     Too far to walk quickly. The right answer uses transit.

  3. One origin -> every grid cell, with the Task 3 settings (60-min departure window,
     median). Times a real run, and reports whether any reachable cell lies more than
     800 m from a stop -- which decides whether trimming the grid would lose anything.

Trips are compared by door-to-door time from the departure, not by adding up legs;
see door_to_door_minutes() for why that distinction matters.

Stops are named by GTFS stop_id rather than by coordinates, so the checks follow the
feed. The departure is Tuesday 2026-09-15 08:00: a normal weekday inside the feed's
validity window. The formal scenario dates are chosen in Task 3.

Run inside the environment:
    micromamba run -n wherecanthebusgetme python src/verify_network.py
"""

from __future__ import annotations

import datetime as dt
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd

from build_network import load_config, read_stops, section

DEPARTURE = dt.datetime(2026, 9, 15, 8, 0)  # naive: r5py reads it in the feed's timezone

ORIGIN_STOP = "1121"  # L Street & South 11th Street, Stop 1 -- busiest downtown cluster
TRIPS = [
    # (label, destination stop_id, expectation)
    ("Downtown -> Nebraska Union (City Campus)", "466", "walk"),
    ("Downtown -> N 33rd & Holdrege (East Campus)", "140", "transit"),
]

# Methodology values. r5py's defaults differ and must be overridden on every call:
# walking speed defaults to 3.6 km/h (methodology: 4.8), and the departure time window
# defaults to 10 minutes (methodology: 60).
SPEED_WALKING_KMH = 4.8
MAX_WALK = dt.timedelta(minutes=10)  # ~800 m at 4.8 km/h
WALK_LIMIT_M = 800

WALK_MODES = {"WALK"}


def mode_name(value) -> str:
    return str(getattr(value, "name", value)).upper()


def read_route_names(gtfs: Path) -> dict[str, str]:
    with zipfile.ZipFile(gtfs) as zf, zf.open("routes.txt") as fh:
        routes = pd.read_csv(fh, dtype=str, encoding="utf-8-sig")
    return dict(zip(routes["route_id"],
                    routes["route_short_name"].fillna("") + " " + routes["route_long_name"].fillna("")))


def door_to_door_minutes(legs: pd.DataFrame) -> float:
    """Minutes from DEPARTURE until the rider arrives, following the legs in order.

    Do NOT add up the legs' travel_time and wait_time. R5 returns options that start at
    different moments within the departure window, and time spent at the origin before
    an option's first leg belongs to no leg at all. Adding up legs made a two-bus option
    to the Nebraska Union look like 8 minutes, when it actually arrived later than walking.

    A leg's departure_time is when it begins (for a bus leg, boarding, after its wait),
    so the clock jumps forward to it. Walk-only options have no departure_time (NaT) and
    start at DEPARTURE.
    """
    clock = pd.Timestamp(DEPARTURE)
    for _, leg in legs.sort_values("segment").iterrows():
        if pd.notna(leg["departure_time"]):
            clock = max(clock, pd.Timestamp(leg["departure_time"]))
        clock += leg["travel_time"]
    return (clock - pd.Timestamp(DEPARTURE)).total_seconds() / 60


def point_gdf(ids: list[str], stops, stop_ids: list[str]):
    import geopandas as gpd

    rows = stops.set_index("stop_id").loc[stop_ids]
    return gpd.GeoDataFrame(
        {"id": ids},
        geometry=gpd.points_from_xy(rows["stop_lon"], rows["stop_lat"]),
        crs="EPSG:4326",
    )


def check_itineraries(network, r5py, stops, route_names: dict[str, str]) -> bool:
    section("CHECKS 1-2 - DETAILED ITINERARIES")
    origins = point_gdf([f"o{i}" for i in range(len(TRIPS))], stops,
                        [ORIGIN_STOP] * len(TRIPS))
    destinations = point_gdf([f"d{i}" for i in range(len(TRIPS))], stops,
                             [stop for _, stop, _ in TRIPS])

    started = time.perf_counter()
    itineraries = r5py.DetailedItineraries(
        network,
        origins=origins,
        destinations=destinations,
        departure=DEPARTURE,
        transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
        speed_walking=SPEED_WALKING_KMH,
        max_time_walking=MAX_WALK,
        max_time=dt.timedelta(minutes=90),
    )
    print(f"  departure {DEPARTURE:%a %Y-%m-%d %H:%M}, computed in "
          f"{time.perf_counter() - started:.1f} s\n")

    all_ok = True
    for i, (label, _, expect) in enumerate(TRIPS):
        trip = itineraries[itineraries["to_id"] == f"d{i}"]
        print(f"  {label}  (expect: {expect})")
        if trip.empty:
            print("    NO ITINERARY FOUND -- destination unreachable within 90 min")
            print("    CHECK FAILED\n")
            all_ok = False
            continue

        options = []
        for _, legs in trip.groupby("option"):
            legs = legs.sort_values("segment")
            modes = [mode_name(m) for m in legs["transport_mode"]]
            routes = [route_names.get(str(r), str(r)) for r in legs["route_id"]
                      if pd.notna(r) and str(r) not in ("", "nan", "None")]
            options.append((door_to_door_minutes(legs), " + ".join(modes),
                            list(dict.fromkeys(routes)), set(modes)))
        options.sort(key=lambda o: o[0])

        print(f"    {len(options)} options; earliest arrivals, clock starting {DEPARTURE:%H:%M}:")
        for total, desc, routes, _ in options[:5]:
            print(f"      {total:5.1f} min  {desc}" + (f"  (route {', '.join(routes)})" if routes else ""))
        walk_only = [o for o in options if o[3] <= WALK_MODES]
        if walk_only:
            print(f"    walking the whole way: {walk_only[0][0]:.1f} min")

        best_total, _, _, best_modes = options[0]
        uses_transit = bool(best_modes - WALK_MODES)
        if expect == "walk":
            ok = not uses_transit and best_total <= 20
            reason = "walking is the fastest way there, under 20 min"
        else:
            ok = uses_transit and 10 <= best_total <= 45
            reason = "the fastest way there uses transit and takes 10-45 min"
        print(f"    {'PASS' if ok else 'CHECK FAILED'}: {reason} "
              f"(got {best_total:.1f} min, {'transit' if uses_transit else 'walk only'})\n")
        all_ok &= ok
    return all_ok


def check_grid_matrix(network, r5py, stops, grid_path: Path) -> bool:
    import geopandas as gpd

    section("CHECK 3 - ONE ORIGIN TO EVERY GRID CELL (TASK 3 SETTINGS)")
    centroids = gpd.read_file(grid_path, layer="centroids")
    origin = point_gdf(["downtown"], stops, [ORIGIN_STOP])

    started = time.perf_counter()
    matrix = r5py.TravelTimeMatrix(
        network,
        origins=origin,
        destinations=centroids[["id", "geometry"]],
        departure=DEPARTURE,
        departure_time_window=dt.timedelta(minutes=60),
        percentiles=[50],
        transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
        speed_walking=SPEED_WALKING_KMH,
        max_time_walking=MAX_WALK,
        max_time=dt.timedelta(minutes=60),
    )
    elapsed = time.perf_counter() - started

    tt_col = next(c for c in matrix.columns if str(c).startswith("travel_time"))
    merged = centroids.merge(matrix[["to_id", tt_col]], left_on="id", right_on="to_id")
    reachable = merged[merged[tt_col].notna()]

    print(f"  {len(centroids):,} destinations, computed in {elapsed:.1f} s")
    print(f"  reachable within 60 min (median): {len(reachable):,} "
          f"({len(reachable) / len(centroids):.0%})")
    for band in (15, 30, 45, 60):
        n = int((reachable[tt_col] <= band).sum())
        print(f"    <= {band} min: {n:>6,} cells  (~{n * 0.0225:,.0f} km^2)")

    beyond = reachable[reachable["near_stop_m"] > WALK_LIMIT_M]
    print(f"\n  reachable cells more than {WALK_LIMIT_M} m from any stop: {len(beyond):,}")
    if len(reachable):
        print(f"  farthest reachable cell from a stop: {reachable['near_stop_m'].max():,.0f} m")
    if len(beyond):
        print("  -> trimming the grid to cells near stops WOULD lose reachable cells")
    else:
        print("  -> trimming the grid to cells near stops would lose nothing")

    ok = len(reachable) > 0
    print(f"\n  {'PASS' if ok else 'CHECK FAILED'}: at least one cell is reachable")
    return ok


def main() -> int:
    cfg = load_config()
    # r5py reads --max-memory from sys.argv when it is first imported.
    sys.argv += ["--max-memory", str(cfg["network"]["jvm_max_memory"])]
    import r5py

    gtfs = Path(cfg["paths"]["gtfs"])
    stops = read_stops(gtfs)
    network = r5py.TransportNetwork(Path(cfg["paths"]["osm_clipped"]), [gtfs])

    ok_trips = check_itineraries(network, r5py, stops, read_route_names(gtfs))
    ok_grid = check_grid_matrix(network, r5py, stops, Path(cfg["paths"]["grid"]))

    print()
    section("VERDICT")
    if ok_trips and ok_grid:
        print("  PASS -- the network routes plausibly. Task 2 is complete.")
        return 0
    print("  FAIL -- see the checks above before building on this network.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
