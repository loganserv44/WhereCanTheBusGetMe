#!/usr/bin/env python3
"""Task 3 -- compute a travel-time grid for every origin x scenario.

For each origin in data/origins.csv and each scenario in config.yml, route with R5 from
the origin to the centre of every grid cell, and record the 25th, 50th and 75th
percentile travel time across the departure window. The maps show the median (p50);
p25 and p75 are kept so the spread can be reported.

Why a window and a percentile rather than a single departure: leaving at 8:00 instead of
8:04 can cost a full headway if you just miss the bus. R5 routes a departure every
minute of the window and reports the distribution.

Before anything is routed, every scenario date is checked against the feed:
  - it falls on the weekday the scenario names
  - it lies inside the feed's calendar validity
  - it has no calendar_dates exception (the feed's way of marking holidays and other
    non-normal days)
The service running that day is then looked up. A scenario with no service at all
(Sunday) is not routed. It is written out as an explicit empty result flagged "no
service", because that is the finding, not a failure.

Outputs:
  output/grids/{origin}__{scenario}.parquet       id, p25, p50, p75 (minutes; NaN = unreachable)
  output/isochrones/{origin}__{scenario}.geojson  grid cells dissolved into travel-time bands
  output/run_manifest.json                        inputs, software, parameters, dates, results

Run inside the environment:
    micromamba run -n wherecanthebusgetme python src/compute_isochrones.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from build_network import load_config, section, sha256

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
OUT_GRIDS = Path("output/grids")
OUT_ISOCHRONES = Path("output/isochrones")
RUN_MANIFEST = Path("output/run_manifest.json")
EMPTY_GEOJSON = '{"type": "FeatureCollection", "features": []}\n'


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------
def read_origins(path: Path) -> pd.DataFrame:
    if not path.exists():
        sys.exit(f"ERROR: {path} not found -- origins are chosen in Task 4.")
    origins = pd.read_csv(path, dtype={"slug": str, "stop_id": str})
    missing = {"slug", "name", "lat", "lon"} - set(origins.columns)
    if missing:
        sys.exit(f"ERROR: {path} is missing column(s): {', '.join(sorted(missing))}")
    dupes = origins.loc[origins["slug"].duplicated(), "slug"].tolist()
    if dupes:
        sys.exit(f"ERROR: duplicate origin slug(s) in {path}: {', '.join(dupes)}")
    return origins


def _read_optional(zf: zipfile.ZipFile, name: str) -> pd.DataFrame | None:
    if name not in zf.namelist():
        return None
    with zf.open(name) as fh:
        return pd.read_csv(fh, dtype=str, encoding="utf-8-sig")


def service_on(gtfs: Path, date: dt.date) -> tuple[set[str], list[str], tuple[str, str] | None]:
    """Return (service_ids running on date, exceptions listed for date, calendar validity)."""
    ymd = date.strftime("%Y%m%d")
    with zipfile.ZipFile(gtfs) as zf:
        calendar = _read_optional(zf, "calendar.txt")
        cal_dates = _read_optional(zf, "calendar_dates.txt")

    active: set[str] = set()
    validity = None
    if calendar is not None and len(calendar):
        validity = (calendar["start_date"].min(), calendar["end_date"].max())
        day = WEEKDAYS[date.weekday()]
        running = calendar[(calendar[day] == "1")
                           & (calendar["start_date"] <= ymd) & (calendar["end_date"] >= ymd)]
        active |= set(running["service_id"])

    exceptions = []
    if cal_dates is not None and len(cal_dates):
        for _, row in cal_dates[cal_dates["date"] == ymd].iterrows():
            if row["exception_type"] == "1":
                active.add(row["service_id"])
                exceptions.append(f"service {row['service_id']} added")
            elif row["exception_type"] == "2":
                active.discard(row["service_id"])
                exceptions.append(f"service {row['service_id']} removed")
    return active, exceptions, validity


def resolve_scenarios(cfg: dict, gtfs: Path) -> tuple[list[dict], list[str]]:
    default_window = int(cfg["routing"]["departure_time_window_minutes"])
    resolved, problems = [], []
    for s in cfg["scenarios"]:
        date = dt.date.fromisoformat(str(s["date"]))
        hh, mm = (int(p) for p in str(s["time"]).split(":"))
        active, exceptions, validity = service_on(gtfs, date)

        if WEEKDAYS[date.weekday()] != str(s["weekday"]).lower():
            problems.append(f"{s['id']}: {date} is a {date:%A}, not a {s['weekday']}")
        if validity and not validity[0] <= date.strftime("%Y%m%d") <= validity[1]:
            problems.append(f"{s['id']}: {date} is outside the feed's calendar "
                            f"({validity[0]} to {validity[1]})")
        if exceptions:
            problems.append(f"{s['id']}: {date} has calendar exceptions "
                            f"({'; '.join(exceptions)}) -- choose a normal day")

        resolved.append({
            "id": s["id"],
            "label": s["label"],
            "weekday": s["weekday"],
            "date": date.isoformat(),
            "time": f"{hh:02d}:{mm:02d}",
            "departure": dt.datetime.combine(date, dt.time(hh, mm)),
            "window_minutes": int(s.get("departure_time_window_minutes", default_window)),
            "active_service_ids": sorted(active),
            "routed": bool(active),
        })
    return resolved, problems


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------
def percentile_name(column: str, percentiles: list[int]) -> str:
    """r5py names a single percentile 'travel_time' and several 'travel_time_p25' etc."""
    column = str(column)
    return f"p{percentiles[0]}" if column == "travel_time" else "p" + column.rsplit("_p", 1)[-1]


def write_bands(times: pd.DataFrame, cells, bands: list[int], path: Path) -> None:
    """Dissolve reachable cells into exclusive bands: 0-15 -> 15, 15-30 -> 30, ..."""
    med = times["p50"]
    reach = times[med.notna() & (med <= bands[-1])].copy()
    path.unlink(missing_ok=True)
    if reach.empty:
        path.write_text(EMPTY_GEOJSON, encoding="utf-8")
        return
    reach["band"] = np.asarray(bands)[np.searchsorted(bands, reach["p50"].to_numpy(), side="left")]
    polys = cells.merge(reach[["id", "band"]], on="id").dissolve(by="band").reset_index()
    polys = polys[["band", "geometry"]].to_crs("EPSG:4326")
    polys.to_file(path, driver="GeoJSON")


def route_all(cfg: dict, origins: pd.DataFrame, scenarios: list[dict]) -> tuple[list[dict], dict]:
    routing = cfg["routing"]
    # r5py reads --max-memory from sys.argv when it is first imported.
    sys.argv += ["--max-memory", str(cfg["network"]["jvm_max_memory"])]
    import geopandas as gpd
    import jpype
    import r5py
    from r5py.util.classpath import R5_CLASSPATH

    gtfs, osm = Path(cfg["paths"]["gtfs"]), Path(cfg["paths"]["osm_clipped"])
    grid = Path(cfg["paths"]["grid"])
    network = r5py.TransportNetwork(osm, [gtfs])
    centroids = gpd.read_file(grid, layer="centroids")[["id", "geometry"]]
    cells = gpd.read_file(grid, layer="cells")[["id", "geometry"]]
    cell_km2 = (float(cfg["grid"]["cell_size_m"]) / 1000) ** 2
    bands = [int(b) for b in routing["bands_minutes"]]
    percentiles = [int(p) for p in routing["percentiles"]]
    if 50 not in percentiles:
        sys.exit("ERROR: routing.percentiles must include 50 -- the maps show the median.")

    software = {
        "r5py": r5py.__version__,
        "r5_jar": Path(str(R5_CLASSPATH)).name,
        "java": str(jpype.java.lang.System.getProperty("java.version")),
    }
    print(f"  r5py {software['r5py']}, {software['r5_jar']}, Java {software['java']}")
    print(f"  {len(centroids):,} destination cells\n")

    OUT_GRIDS.mkdir(parents=True, exist_ok=True)
    OUT_ISOCHRONES.mkdir(parents=True, exist_ok=True)
    summary = []
    for origin in origins.itertuples(index=False):
        origin_gdf = gpd.GeoDataFrame(
            {"id": [origin.slug]},
            geometry=gpd.points_from_xy([origin.lon], [origin.lat]), crs="EPSG:4326",
        )
        for s in scenarios:
            stem = f"{origin.slug}__{s['id']}"
            started = time.perf_counter()
            times = pd.DataFrame({"id": centroids["id"]})

            if s["routed"]:
                matrix = r5py.TravelTimeMatrix(
                    network,
                    origins=origin_gdf,
                    destinations=centroids,
                    departure=s["departure"],
                    departure_time_window=dt.timedelta(minutes=s["window_minutes"]),
                    percentiles=percentiles,
                    transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
                    # r5py defaults are 3.6 km/h and a 10-min window -- always set both.
                    speed_walking=float(routing["speed_walking_kmh"]),
                    max_time_walking=dt.timedelta(minutes=float(routing["max_walk_minutes"])),
                    max_time=dt.timedelta(minutes=int(routing["max_time_minutes"])),
                )
                tt_cols = [c for c in matrix.columns if str(c).startswith("travel_time")]
                matrix = matrix.rename(columns={c: percentile_name(c, percentiles) for c in tt_cols})
                keep = ["to_id"] + [f"p{p}" for p in percentiles]
                times = times.merge(matrix[keep], left_on="id", right_on="to_id", how="left")
                times = times.drop(columns="to_id")
            else:
                for p in percentiles:
                    times[f"p{p}"] = np.nan

            for p in percentiles:
                times[f"p{p}"] = pd.to_numeric(times[f"p{p}"], errors="coerce").astype("float32")
            times.to_parquet(OUT_GRIDS / f"{stem}.parquet", index=False)
            write_bands(times, cells, bands, OUT_ISOCHRONES / f"{stem}.geojson")

            row = {"origin": origin.slug, "scenario": s["id"], "routed": s["routed"]}
            for b in bands:
                row[f"<={b}"] = int((times["p50"] <= b).sum())
            row["km2"] = round(row[f"<={bands[-1]}"] * cell_km2, 1)
            row["seconds"] = round(time.perf_counter() - started, 1)
            summary.append(row)
            status = f"{row['km2']:>6.1f} km^2 within {bands[-1]} min" if s["routed"] else "  no service -- not routed"
            print(f"  {stem:<32} {status}  ({row['seconds']:.1f} s)")
    return summary, software


# --------------------------------------------------------------------------
def main() -> int:
    cfg = load_config()
    gtfs = Path(cfg["paths"]["gtfs"])
    origins = read_origins(Path(cfg["paths"]["origins"]))

    section("SCENARIOS")
    scenarios, problems = resolve_scenarios(cfg, gtfs)
    for s in scenarios:
        service = ", ".join(s["active_service_ids"]) or "NONE (not routed)"
        print(f"  {s['id']:<10} {s['label']:<20} {s['departure']:%a %Y-%m-%d %H:%M}"
              f"  window {s['window_minutes']} min  service: {service}")
    if problems:
        print()
        for p in problems:
            print(f"  ERROR {p}")
        return 1
    print()

    section("ORIGINS")
    for o in origins.itertuples(index=False):
        print(f"  {o.slug:<22} {o.name}")
    print()

    section("ROUTING")
    r = cfg["routing"]
    print(f"  walk {r['speed_walking_kmh']} km/h, walk legs <= {r['max_walk_minutes']} min, "
          f"trips <= {r['max_time_minutes']} min, percentiles {r['percentiles']}")
    summary, software = route_all(cfg, origins, scenarios)
    print()

    section("RESULTS - cells reachable by median travel time")
    table = pd.DataFrame(summary)
    print(table.drop(columns=["routed", "seconds"]).to_string(index=False))

    # Sanity: the weekday-morning scenario should reach at least as far as any other.
    first = scenarios[0]["id"]
    print(f"\n  contrast check (no scenario should out-reach {first}):")
    anomalies = 0
    for origin, grp in table.groupby("origin", sort=False):
        base = float(grp.loc[grp["scenario"] == first, "km2"].iloc[0])
        worse = grp[(grp["scenario"] != first) & (grp["km2"] > base)]
        for _, w in worse.iterrows():
            anomalies += 1
            print(f"    CHECK {origin}: {w['scenario']} reaches {w['km2']} km^2 > {first} {base} km^2")
    if not anomalies:
        print("    ok")

    manifest = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "gtfs": {"path": str(gtfs), "sha256": sha256(gtfs)},
            "osm_clipped": {"path": cfg["paths"]["osm_clipped"],
                            "sha256": sha256(Path(cfg["paths"]["osm_clipped"]))},
            "grid": {"path": cfg["paths"]["grid"], "cell_size_m": cfg["grid"]["cell_size_m"],
                     "max_distance_to_stop_m": cfg["grid"]["max_distance_to_stop_m"]},
        },
        "software": software,
        "routing": {**cfg["routing"], "transport_modes": ["TRANSIT", "WALK"]},
        "scenarios": [{k: v for k, v in s.items() if k != "departure"} for s in scenarios],
        "origins": origins.to_dict(orient="records"),
        "results": summary,
    }
    RUN_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    RUN_MANIFEST.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\n  wrote {RUN_MANIFEST}, {len(summary)} grids in {OUT_GRIDS}, "
          f"band polygons in {OUT_ISOCHRONES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
