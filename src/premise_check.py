#!/usr/bin/env python3
"""Task 0 -- verify the premise before anything gets built on it.

The project's argument rests on one factual claim: StarTran runs no Sunday
service, so the Sunday panel is blank. If that is wrong, the scope has to be
revised before any pipeline code is written. This script is the gate.

It checks, against the published GTFS feed:

  1. Feed identity -- publisher, version, validity window.
  2. The Sunday claim, two independent ways:
       a. no service_id has sunday=1 in calendar.txt
       b. no calendar_dates.txt exception ADDS service on a date that is a Sunday
     (b) is the one that is easy to miss: a feed can show no Sunday service in
     the weekly bitmask and still add a holiday or game-day service by date.
  3. Service spans per day type -- first departure and last arrival.
  4. Route and stop counts.
  5. Per-route spans, because "service until 9:55pm" is the last bus anywhere
     on the system and says nothing about whether most routes quit far earlier.

Exit code is 0 if the premise holds and 1 if it does not.

Usage:
    python src/premise_check.py [--gtfs data/raw/gtfs.zip]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
import zipfile
from pathlib import Path

import pandas as pd

DEFAULT_GTFS = Path("data/raw/gtfs.zip")
FEED_URL = "https://startran.connexionz.net/rtt/public/resource/gtfs.zip"

DAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# GTFS exception_type values (calendar_dates.txt)
SERVICE_ADDED = 1
SERVICE_REMOVED = 2


# --------------------------------------------------------------------------
# GTFS time handling
# --------------------------------------------------------------------------
def gtfs_time_to_seconds(value: str) -> float:
    """Convert a GTFS HH:MM:SS to seconds after midnight of the service day.

    GTFS permits hours >= 24 for trips running past midnight: 25:15:00 is
    1:15am, still belonging to the previous service day. Parsing these as wall
    clock times would silently truncate the evening span and make the network
    look like it shuts down earlier than it does.
    """
    if not isinstance(value, str) or not value.strip():
        return float("nan")
    h, m, s = (int(p) for p in value.strip().split(":"))
    return h * 3600 + m * 60 + s


def seconds_to_label(total: float) -> str:
    """Render seconds-after-midnight, keeping hours >= 24 visible as such."""
    if pd.isna(total):
        return "--"
    total = int(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    suffix = "  (next day)" if h >= 24 else ""
    return f"{h:02d}:{m:02d}:{s:02d}{suffix}"


def service_day_label(row: pd.Series) -> str:
    """Name a service pattern from its weekly bitmask, e.g. 'Mon-Fri'."""
    active = [d for d in DAY_COLS if int(row[d]) == 1]
    if not active:
        return "(no regular days)"
    short = {d: d[:3].capitalize() for d in DAY_COLS}
    if active == DAY_COLS[:5]:
        return "Mon-Fri"
    if len(active) == 1:
        return short[active[0]]
    return ", ".join(short[d] for d in active)


def read_gtfs_table(zf: zipfile.ZipFile, name: str) -> pd.DataFrame | None:
    """Read one GTFS table, or None when the (optional) file is absent."""
    if name not in zf.namelist():
        return None
    with zf.open(name) as fh:
        return pd.read_csv(fh, dtype=str, encoding="utf-8-sig")


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def report_feed_identity(zf: zipfile.ZipFile, gtfs_path: Path) -> None:
    print("=" * 74)
    print("FEED IDENTITY")
    print("=" * 74)

    digest = hashlib.sha256(gtfs_path.read_bytes()).hexdigest()
    stat = gtfs_path.stat()
    print(f"  source        {FEED_URL}")
    print(f"  local file    {gtfs_path}")
    print(f"  size          {stat.st_size:,} bytes")
    print(f"  sha256        {digest}")
    print(f"  file mtime    {dt.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()}")

    agency = read_gtfs_table(zf, "agency.txt")
    if agency is not None and len(agency):
        print(f"  agency        {agency.iloc[0]['agency_name']}")
        print(f"  timezone      {agency.iloc[0]['agency_timezone']}")

    info = read_gtfs_table(zf, "feed_info.txt")
    if info is not None and len(info):
        row = info.iloc[0]
        print(f"  publisher     {row.get('feed_publisher_name', '?')}")
        print(f"  feed_version  {row.get('feed_version', '?')}")
        print(f"  declared span {row.get('feed_start_date', '?')} -> {row.get('feed_end_date', '?')}")
    print()


def check_sunday_service(
    calendar: pd.DataFrame | None,
    cal_dates: pd.DataFrame | None,
) -> tuple[bool, list[str]]:
    """Return (premise_holds, failure_reasons) for the no-Sunday-service claim."""
    print("=" * 74)
    print("THE SUNDAY CLAIM")
    print("=" * 74)

    failures: list[str] = []

    # -- Check (a): the weekly bitmask ------------------------------------
    if calendar is None or calendar.empty:
        print("  calendar.txt   ABSENT -- feed defines service by date only.")
        print("                 Cannot check a weekly bitmask; relying on (b).")
    else:
        print("  (a) calendar.txt weekly patterns")
        for _, row in calendar.iterrows():
            days = service_day_label(row)
            sun = int(row["sunday"])
            flag = "  <-- RUNS SUNDAY" if sun else ""
            print(
                f"        service_id {row['service_id']:<4} {days:<24}"
                f" sunday={sun}  {row['start_date']}..{row['end_date']}{flag}"
            )
        sunday_services = calendar[calendar["sunday"].astype(int) == 1]
        if len(sunday_services):
            ids = ", ".join(sunday_services["service_id"])
            failures.append(f"calendar.txt has Sunday service: service_id(s) {ids}")
            print(f"\n      FAIL: {len(sunday_services)} service_id(s) run on Sundays.")
        else:
            print("\n      PASS: no service_id has sunday=1.")

    # -- Check (b): date exceptions that ADD service ----------------------
    print("\n  (b) calendar_dates.txt exceptions")
    if cal_dates is None or cal_dates.empty:
        print("        (none) -- no date exceptions in the feed.")
        print("\n      PASS: no exception can add Sunday service.")
    else:
        added_on_sunday = []
        for _, row in cal_dates.iterrows():
            date = dt.datetime.strptime(row["date"], "%Y%m%d").date()
            etype = int(row["exception_type"])
            kind = "ADDED" if etype == SERVICE_ADDED else "REMOVED"
            weekday = date.strftime("%A")
            marker = ""
            if etype == SERVICE_ADDED and date.weekday() == 6:  # 6 == Sunday
                marker = "  <-- ADDS SUNDAY SERVICE"
                added_on_sunday.append((row["service_id"], date))
            print(
                f"        service_id {row['service_id']:<4} {date.isoformat()}"
                f"  {weekday:<10} {kind}{marker}"
            )

        n_added = (cal_dates["exception_type"].astype(int) == SERVICE_ADDED).sum()
        if added_on_sunday:
            for sid, date in added_on_sunday:
                failures.append(f"calendar_dates.txt adds service {sid} on Sunday {date}")
            print(f"\n      FAIL: {len(added_on_sunday)} exception(s) add Sunday service.")
        else:
            print(
                f"\n      PASS: {n_added} service-adding exception(s); none fall on a Sunday."
            )
    print()
    return (not failures), failures


def report_service_spans(
    zf: zipfile.ZipFile, calendar: pd.DataFrame | None
) -> pd.DataFrame:
    """Print first/last service times per service pattern. Returns trips+times."""
    print("=" * 74)
    print("SERVICE SPANS")
    print("=" * 74)

    trips = read_gtfs_table(zf, "trips.txt")
    stop_times = read_gtfs_table(zf, "stop_times.txt")

    stop_times["dep_s"] = stop_times["departure_time"].map(gtfs_time_to_seconds)
    stop_times["arr_s"] = stop_times["arrival_time"].map(gtfs_time_to_seconds)

    merged = stop_times.merge(trips[["trip_id", "route_id", "service_id"]], on="trip_id")

    labels = {}
    if calendar is not None:
        labels = {r["service_id"]: service_day_label(r) for _, r in calendar.iterrows()}

    print(f"  {'service':<10} {'days':<12} {'trips':>7} {'first departure':<16} {'last arrival':<22}")
    print(f"  {'-'*10} {'-'*12} {'-'*7} {'-'*16} {'-'*22}")
    for sid, grp in merged.groupby("service_id"):
        print(
            f"  {sid:<10} {labels.get(sid, '?'):<12} {grp['trip_id'].nunique():>7}"
            f" {seconds_to_label(grp['dep_s'].min()):<16}"
            f" {seconds_to_label(grp['arr_s'].max()):<22}"
        )

    # Sunday gets no row above because no service runs; state it explicitly.
    print(f"  {'(none)':<10} {'Sun':<12} {0:>7} {'--':<16} {'--':<22}")
    print()
    return merged


def report_per_route_spans(merged: pd.DataFrame, routes: pd.DataFrame,
                           calendar: pd.DataFrame | None) -> None:
    """Per-route spans -- the network-wide span hides how early most routes stop."""
    print("=" * 74)
    print("PER-ROUTE SPANS")
    print("=" * 74)

    labels = {}
    if calendar is not None:
        labels = {r["service_id"]: service_day_label(r) for _, r in calendar.iterrows()}

    name_by_id = {
        r["route_id"]: (r.get("route_short_name") or r.get("route_long_name") or r["route_id"])
        for _, r in routes.iterrows()
    }
    long_by_id = {r["route_id"]: (r.get("route_long_name") or "") for _, r in routes.iterrows()}

    for sid, sgrp in merged.groupby("service_id"):
        print(f"\n  --- service_id {sid} ({labels.get(sid, '?')}) ---")
        print(f"  {'route':<8} {'name':<34} {'first':<10} {'last':<20}")
        print(f"  {'-'*8} {'-'*34} {'-'*10} {'-'*20}")
        rows = []
        for rid, rgrp in sgrp.groupby("route_id"):
            rows.append((rid, rgrp["dep_s"].min(), rgrp["arr_s"].max()))
        for rid, first, last in sorted(rows, key=lambda x: x[2]):
            label = str(name_by_id.get(rid, rid))
            long = str(long_by_id.get(rid, ""))[:32]
            print(
                f"  {label:<8} {long:<34}"
                f" {seconds_to_label(first).split()[0]:<10} {seconds_to_label(last):<20}"
            )


def report_counts(zf: zipfile.ZipFile) -> tuple[pd.DataFrame, int]:
    print("=" * 74)
    print("COUNTS")
    print("=" * 74)
    routes = read_gtfs_table(zf, "routes.txt")
    stops = read_gtfs_table(zf, "stops.txt")
    trips = read_gtfs_table(zf, "trips.txt")
    print(f"  routes        {len(routes)}")
    print(f"  stops         {len(stops)}")
    print(f"  trips         {len(trips)}")
    print()
    return routes, len(stops)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gtfs", type=Path, default=DEFAULT_GTFS,
                    help=f"path to the GTFS zip (default: {DEFAULT_GTFS})")
    args = ap.parse_args()

    if not args.gtfs.exists():
        print(f"ERROR: {args.gtfs} not found. Download it from:\n  {FEED_URL}",
              file=sys.stderr)
        return 2

    with zipfile.ZipFile(args.gtfs) as zf:
        report_feed_identity(zf, args.gtfs)
        calendar = read_gtfs_table(zf, "calendar.txt")
        cal_dates = read_gtfs_table(zf, "calendar_dates.txt")

        holds, failures = check_sunday_service(calendar, cal_dates)
        routes, _ = report_counts(zf)
        merged = report_service_spans(zf, calendar)
        report_per_route_spans(merged, routes, calendar)

    print()
    print("=" * 74)
    print("VERDICT")
    print("=" * 74)
    if holds:
        print("  PASS -- no Sunday service. The premise holds; the blank Sunday")
        print("          panel is justified. Clear to proceed to Task 1.")
        return 0
    print("  FAIL -- the premise does NOT hold:")
    for f in failures:
        print(f"          - {f}")
    print("\n  STOP. Revise the project scope before writing pipeline code.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
