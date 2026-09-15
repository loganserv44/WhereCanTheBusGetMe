#!/usr/bin/env python3
"""Task 2 -- build the routing inputs.

  1. Derive a bounding box from the GTFS stop extents plus a buffer.
  2. Clip the Nebraska OSM extract to it with osmium-tool.
  3. Have R5 build a TransportNetwork from the clip and the GTFS feed.
  4. Lay a regular destination grid over the bounding box.

The grid is laid over the clip box, not over the extent R5 reports for the built
network. The clip keeps whole every road crossing the box edge, so the network's
extent runs kilometres past the box into countryside no trip reaches.

The bounding box comes from where the stops actually are rather than a city boundary
typed in by hand, so it follows the feed if StarTran extends a route.

The clip uses osmium-tool's `extract --strategy complete_ways`, which keeps every way
that crosses the box edge whole. A naive clip drops the nodes outside the box, leaving
streets that end at the boundary. R5 raises no error for that; routes near the edge
just come out slower or unreachable.

R5 caches the built network in %LOCALAPPDATA%\\r5py, keyed by a hash of the input
files, so a rerun with unchanged inputs loads in seconds. r5py purges that cache
after two weeks.

Run inside the environment:
    micromamba run -n wherecanthebusgetme python src/build_network.py

Options:
    --force-clip          re-clip the OSM extract even if an up-to-date clip exists
    --allow-gtfs-errors   let R5 build despite GTFS validation errors (off by default:
                          R5's objections should be read before they are overridden)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import yaml

CONFIG_PATH = Path("config.yml")
ENV_NAME = "wherecanthebusgetme"

KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_AT_EQUATOR = 111.320


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def mb(path: Path) -> str:
    return f"{path.stat().st_size / 1e6:,.1f} MB"


def section(title: str) -> None:
    print("=" * 74)
    print(title)
    print("=" * 74)


# --------------------------------------------------------------------------
# step 1: bounding box
# --------------------------------------------------------------------------
def read_stops(gtfs_zip: Path) -> pd.DataFrame:
    with zipfile.ZipFile(gtfs_zip) as zf, zf.open("stops.txt") as fh:
        stops = pd.read_csv(fh, usecols=["stop_id", "stop_lat", "stop_lon"],
                            dtype={"stop_id": str}, encoding="utf-8-sig")
    stops["stop_lat"] = stops["stop_lat"].astype(float)
    stops["stop_lon"] = stops["stop_lon"].astype(float)
    return stops


def stop_extent_bbox(gtfs_zip: Path, buffer_km: float) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) around every stop, padded by buffer_km.

    The longitude buffer is scaled by the cosine of the median stop latitude: at
    Lincoln's ~40.8 N a degree of longitude is ~84 km, not 111.
    """
    stops = read_stops(gtfs_zip)
    lat = stops["stop_lat"]
    lon = stops["stop_lon"]

    ref_lat = lat.median()
    dlat = buffer_km / KM_PER_DEG_LAT
    dlon = buffer_km / (KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(ref_lat)))

    return (
        round(lon.min() - dlon, 5),
        round(lat.min() - dlat, 5),
        round(lon.max() + dlon, 5),
        round(lat.max() + dlat, 5),
    )


def bbox_dimensions_km(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    west, south, east, north = bbox
    mid_lat = (south + north) / 2
    width = (east - west) * KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(mid_lat))
    height = (north - south) * KM_PER_DEG_LAT
    return width, height


# --------------------------------------------------------------------------
# step 2: clip
# --------------------------------------------------------------------------
def find_osmium() -> str:
    exe = shutil.which("osmium")
    if exe is None:
        sys.exit(
            "ERROR: osmium-tool not found on PATH. Run this script inside the "
            f"environment:\n  micromamba run -n {ENV_NAME} python src/build_network.py"
        )
    return exe


def run_osmium(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run osmium-tool with argv[0] set to plain "osmium".

    osmium picks its subcommand from the name it was invoked under, so it can also run
    as e.g. "osmium-extract". On Windows shutil.which returns "...\\osmium.EXE", which
    osmium does not recognise as its own name and rejects as an unknown command. So
    launch the resolved path via `executable` but present the name as "osmium".
    """
    return subprocess.run(["osmium", *args], executable=find_osmium(), check=True, **kwargs)


def clip_osm(src: Path, dst: Path, bbox: tuple[float, ...], force: bool) -> None:
    """Clip src to bbox, skipping the work if dst is already this exact clip.

    A sidecar JSON records the bbox and the source hash the clip was made from. Checking
    only that dst exists would silently reuse a stale clip after the buffer changes or a
    new OSM extract is downloaded.
    """
    sidecar = dst.with_name(dst.name + ".json")
    wanted = {
        "bbox_wsen": list(bbox),
        "source": str(src).replace("\\", "/"),
        "source_sha256": sha256(src),
        "strategy": "complete_ways",
    }

    if dst.exists() and sidecar.exists() and not force:
        if json.loads(sidecar.read_text(encoding="utf-8")) == wanted:
            print(f"  up-to-date clip present, skipping ({mb(dst)})")
            return
        print("  existing clip was made from a different bbox or source -- re-clipping")

    dst.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "extract",
        "--bbox", ",".join(str(v) for v in bbox),
        "--strategy", "complete_ways",
        "--set-bounds",
        "--overwrite",
        "--output", str(dst),
        str(src),
    ]
    print("  osmium " + " ".join(args))
    started = time.perf_counter()
    run_osmium(args)
    # Written only after osmium succeeds, so a failed clip is never mistaken for a good one.
    sidecar.write_text(json.dumps(wanted, indent=2) + "\n", encoding="utf-8")
    print(f"  clipped {mb(src)} -> {mb(dst)} in {time.perf_counter() - started:.1f} s")


def report_osm_counts(path: Path) -> None:
    try:
        out = run_osmium(["fileinfo", "--extended", "--json", str(path)],
                         capture_output=True, text=True).stdout
        count = json.loads(out)["data"]["count"]
        print(f"  nodes {count['nodes']:>12,}")
        print(f"  ways  {count['ways']:>12,}")
        print(f"  relations {count['relations']:>8,}")
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as exc:
        print(f"  (could not read element counts: {exc})")


# --------------------------------------------------------------------------
# step 3: R5 network
# --------------------------------------------------------------------------
def build_network(osm: Path, gtfs: Path, max_memory: str, allow_gtfs_errors: bool) -> int:
    # r5py reads --max-memory from sys.argv when it is first imported (configargparse,
    # parse_known_args), so the cap has to be in place before that import.
    sys.argv += ["--max-memory", max_memory]

    import jpype
    import r5py
    from r5py.util.config import Config
    from r5py.util.exceptions import GtfsFileError

    system = jpype.java.lang.System
    runtime = jpype.java.lang.Runtime.getRuntime()
    print(f"  r5py {r5py.__version__} on Java {system.getProperty('java.version')}"
          f" ({system.getProperty('java.vendor')})")
    print(f"  JVM max heap {runtime.maxMemory() / 1024**3:.1f} GB (requested {max_memory})")

    started = time.perf_counter()
    try:
        network = r5py.TransportNetwork(osm, [gtfs], allow_errors=allow_gtfs_errors)
    except GtfsFileError as exc:
        print("\n  R5 rejected the GTFS feed. Its objections:\n")
        print("  " + str(exc).replace("\n", "\n  "))
        print("\n  Read these before overriding. If they are acceptable, rerun with "
              "--allow-gtfs-errors.")
        return 1
    except Exception as exc:
        print(f"\n  R5 failed: {type(exc).__name__}: {exc}")
        print("  If this is a Java compatibility error, try pinning openjdk to 22 or 23 "
              "in environment.yml.")
        raise
    elapsed = time.perf_counter() - started

    used_gb = (runtime.totalMemory() - runtime.freeMemory()) / 1024**3
    west, south, east, north = network.extent.bounds
    print(f"  built (or loaded from cache) in {elapsed:.1f} s")
    print(f"  JVM heap in use afterwards ~{used_gb:.2f} GB")
    print(f"  street network extent W,S,E,N: {west:.5f},{south:.5f},{east:.5f},{north:.5f}")
    print(f"  timezone {network.timezone}")

    cached = sorted(Config().CACHE_DIR.glob("*.transport_network"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    if cached:
        print(f"  cache {cached[0]} ({mb(cached[0])})")
    return 0


# --------------------------------------------------------------------------
# step 4: destination grid
# --------------------------------------------------------------------------
def build_grid(bbox: tuple[float, float, float, float], gtfs: Path,
               cell_size_m: float, crs: str, out_path: Path) -> None:
    """Write a regular grid of square cells covering the clip box to a GeoPackage.

    Two layers:
      cells      polygons in the metric CRS, for rendering
      centroids  points in WGS84, the destinations handed to r5py

    Cell ids come from the cell's absolute position on a lattice anchored at the
    projection origin (id = row * 100000 + col), not from a running count. So a given
    patch of ground keeps its id if the box is later resized, and results from
    different runs join cleanly.

    Every cell also records its straight-line distance to the nearest stop. Walk legs
    are capped (~800 m), so cells far from any stop can't be reached by transit.
    Keeping the distance lets a later step drop those cells by filtering, rather than
    by rebuilding the grid.
    """
    import geopandas as gpd
    import numpy as np
    import shapely

    started = time.perf_counter()
    clip_box = gpd.GeoSeries([shapely.box(*bbox)], crs="EPSG:4326").to_crs(crs).iloc[0]
    minx, miny, maxx, maxy = clip_box.bounds

    ix0, iy0 = math.floor(minx / cell_size_m), math.floor(miny / cell_size_m)
    ix1, iy1 = math.ceil(maxx / cell_size_m), math.ceil(maxy / cell_size_m)
    ix, iy = np.meshgrid(np.arange(ix0, ix1), np.arange(iy0, iy1))
    ix, iy = ix.ravel(), iy.ravel()
    xmin, ymin = ix * cell_size_m, iy * cell_size_m
    cx, cy = xmin + cell_size_m / 2, ymin + cell_size_m / 2

    # The box is a rectangle in lon/lat but slightly rotated in UTM, so keep only the
    # cells whose centroid falls inside it rather than everything in its bounds.
    inside = shapely.contains_xy(clip_box, cx, cy)
    ix, iy, xmin, ymin, cx, cy = (a[inside] for a in (ix, iy, xmin, ymin, cx, cy))
    ids = iy.astype("int64") * 100_000 + ix.astype("int64")

    stops = read_stops(gtfs)
    stops_gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]), crs="EPSG:4326"
    ).to_crs(crs)
    centroids = gpd.GeoDataFrame({"id": ids}, geometry=gpd.points_from_xy(cx, cy), crs=crs)
    nearest = gpd.sjoin_nearest(centroids, stops_gdf, how="left", distance_col="near_stop_m")
    near_stop_m = nearest.groupby("id")["near_stop_m"].min().reindex(ids).round(1).to_numpy()

    cells = gpd.GeoDataFrame(
        {"id": ids, "ix": ix, "iy": iy, "near_stop_m": near_stop_m},
        geometry=shapely.box(xmin, ymin, xmin + cell_size_m, ymin + cell_size_m),
        crs=crs,
    )
    centroids["near_stop_m"] = near_stop_m

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)  # a stale file would keep old layers alongside new
    cells.to_file(out_path, layer="cells", driver="GPKG")
    centroids.to_crs("EPSG:4326").to_file(out_path, layer="centroids", driver="GPKG")

    print(f"  {len(cells):,} cells of {cell_size_m:g} m in {crs}")
    for limit in (800, 1000, 2000):
        n = int((near_stop_m <= limit).sum())
        print(f"    within {limit:>5,} m of a stop: {n:>7,} ({n / len(cells):.0%})")
    print(f"  wrote {out_path} ({mb(out_path)}) in {time.perf_counter() - started:.1f} s")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--force-clip", action="store_true",
                    help="re-clip the OSM extract even if an up-to-date clip exists")
    ap.add_argument("--allow-gtfs-errors", action="store_true",
                    help="let R5 build despite GTFS validation errors")
    # parse_known_args: r5py parses the same sys.argv later for its own options.
    args, _ = ap.parse_known_args()

    cfg = load_config()
    gtfs = Path(cfg["paths"]["gtfs"])
    osm_source = Path(cfg["paths"]["osm_source"])
    osm_clipped = Path(cfg["paths"]["osm_clipped"])

    missing = [p for p in (gtfs, osm_source) if not p.exists()]
    if missing:
        sys.exit("ERROR: missing input(s): " + ", ".join(map(str, missing))
                 + "\nRun: python src/fetch_data.py")

    section("STEP 1 - BOUNDING BOX FROM GTFS STOP EXTENTS")
    buffer_km = float(cfg["network"]["bbox_buffer_km"])
    bbox = stop_extent_bbox(gtfs, buffer_km)
    width, height = bbox_dimensions_km(bbox)
    print(f"  buffer {buffer_km:g} km")
    print(f"  W,S,E,N {','.join(str(v) for v in bbox)}")
    print(f"  ~{width:.1f} km x {height:.1f} km = {width * height:,.0f} km^2\n")

    section("STEP 2 - CLIP OSM EXTRACT")
    clip_osm(osm_source, osm_clipped, bbox, args.force_clip)
    report_osm_counts(osm_clipped)
    print()

    section("STEP 3 - BUILD R5 TRANSPORT NETWORK")
    status = build_network(osm_clipped, gtfs, str(cfg["network"]["jvm_max_memory"]),
                           args.allow_gtfs_errors)
    print()
    if status != 0:
        return status

    section("STEP 4 - DESTINATION GRID")
    build_grid(bbox, gtfs, float(cfg["grid"]["cell_size_m"]), str(cfg["grid"]["crs"]),
               Path(cfg["paths"]["grid"]))
    print()
    print("Routing inputs built: clipped extract, R5 network, destination grid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
