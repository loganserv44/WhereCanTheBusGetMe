#!/usr/bin/env python3
"""Task 5 -- the final figures: one per origin, four panels each.

Each figure answers one question for one place: where can the bus get you in an hour,
and how does that change across Tuesday morning, Tuesday evening, Saturday evening and
Sunday. The four panels share an extent so the collapse is a matter of looking, not
arithmetic.

Built from src/preview_isochrones.py, which already worked out the colors, the routes
layer and the legend. What is new here:

  - a quiet basemap drawn from our own OSM extract (major roads, water, parks, city
    limits) with our own landmark labels, so streets never turn to mush at panel size.
    Tiles were tried first: CartoDB now requires an API key and serves watermarked
    tiles without one, and this avoids depending on any tile service at all.
  - smoothed band shapes instead of 150 m staircases: the travel-time surface is
    gaussian-blurred by about one cell, then contoured (set render.smoothing_sigma_cells
    to 0 in config.yml for raw squares)
  - US units for every reader-facing number, via src/units.py
  - a headline per panel: area within an hour plus its change from Tuesday morning
  - hand-written callouts from config.yml, stating the fact behind a panel
  - an extent fitted to each origin, with a scale bar, since reaches differ hugely

Run inside the environment, after compute_isochrones.py:
    micromamba run -n wherecanthebusgetme python src/render_panels.py
    micromamba run -n wherecanthebusgetme python src/render_panels.py --origin downtown
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from PIL import Image  # noqa: E402
from scipy.ndimage import gaussian_filter  # noqa: E402

from build_network import load_config  # noqa: E402
from preview_isochrones import BANDS, HAIRLINE, INK, INK2, MUTED, SURFACE  # noqa: E402
from units import MI_TO_M, fmt_change, fmt_mph, fmt_sq_mi, fmt_walk_distance, km2_to_sq_mi  # noqa: E402

warnings.filterwarnings("ignore", category=RuntimeWarning)

CRS = "EPSG:32614"
MANIFEST = Path("output/run_manifest.json")
GRIDS = Path("output/grids")
PANELS, WEB = Path("output/panels"), Path("output/web")
BASEMAP = Path("data/processed/basemap.gpkg")
BASEMAP_LAYERS = ("parks", "water", "roads_minor", "roads_major", "city_limits")
UNREACHABLE_MINUTES = 90.0  # above the last band, so contours close cleanly
CREDIT = "Map data © OpenStreetMap contributors"

# Basemap ink: everything a step or two off the surface, so the bands stay dominant.
PARK, WATER = "#eaf0e6", "#dfe9f1"
ROAD_MINOR, ROAD_MAJOR = "#e6e5df", "#d5d4cd"


def halo(size: float = 2.6):
    return [pe.withStroke(linewidth=size, foreground=SURFACE)]


# --------------------------------------------------------------------------
# basemap, drawn from our own OSM extract
# --------------------------------------------------------------------------
def build_basemap(cfg: dict, rebuild: bool = False) -> dict:
    """Load the basemap layers, extracting them from the OSM clip the first time.

    Cached to data/processed/basemap.gpkg because reading the .pbf takes a few seconds
    per layer and the result never changes between renders.
    """
    if BASEMAP.exists() and not rebuild:
        layers = {}
        for name in BASEMAP_LAYERS:
            try:
                layers[name] = gpd.read_file(BASEMAP, layer=name)
            except Exception:
                layers[name] = gpd.GeoDataFrame(geometry=[], crs=CRS)
        return layers

    osm = cfg["paths"]["osm_clipped"]
    min_water = float(cfg["basemap"]["min_water_hectares"]) * 10_000
    min_park = float(cfg["basemap"]["min_park_hectares"]) * 10_000
    print(f"  extracting basemap layers from {osm}")

    def read(layer: str, where: str, cols=("name",)) -> gpd.GeoDataFrame:
        found = gpd.read_file(osm, layer=layer, where=where, columns=list(cols))
        if found is None or found.empty:
            return gpd.GeoDataFrame(geometry=[], crs=CRS)
        return found.to_crs(CRS)

    water = read("multipolygons", "natural = 'water'")
    parks = read("multipolygons", "leisure = 'park'")
    layers = {
        "parks": parks[parks.area >= min_park] if len(parks) else parks,
        "water": water[water.area >= min_water] if len(water) else water,
        "roads_minor": read("lines", "highway = 'secondary'", ("highway",)),
        "roads_major": read("lines", "highway IN ('motorway','trunk','primary')", ("highway",)),
        "city_limits": read("multipolygons",
                            "boundary = 'administrative' AND name = 'Lincoln'"),
    }
    BASEMAP.unlink(missing_ok=True)
    for name, gdf in layers.items():
        if len(gdf):
            gdf[["geometry"]].to_file(BASEMAP, layer=name, driver="GPKG")
        print(f"    {name:<12} {len(gdf):>5}")
    return layers


def draw_basemap(ax, bm: dict) -> None:
    for name, color in (("parks", PARK), ("water", WATER)):
        if len(bm[name]):
            bm[name].plot(ax=ax, color=color, linewidth=0, zorder=1)
    for name, color, width in (("roads_minor", ROAD_MINOR, 0.5),
                               ("roads_major", ROAD_MAJOR, 1.0)):
        if len(bm[name]):
            bm[name].plot(ax=ax, color=color, linewidth=width, zorder=2)
    if len(bm["city_limits"]):
        bm["city_limits"].plot(ax=ax, facecolor="none", edgecolor=MUTED, linewidth=0.7,
                               linestyle=(0, (5, 3)), zorder=2)


# --------------------------------------------------------------------------
# the travel-time surface
# --------------------------------------------------------------------------
def lattice(cells: gpd.GeoDataFrame, cell_size_m: float) -> tuple:
    """Grid geometry: cell-centre coordinates for contouring, and index offsets."""
    ix0, iy0 = int(cells["ix"].min()), int(cells["iy"].min())
    nx = int(cells["ix"].max()) - ix0 + 1
    ny = int(cells["iy"].max()) - iy0 + 1
    x = (np.arange(nx) + ix0 + 0.5) * cell_size_m
    y = (np.arange(ny) + iy0 + 0.5) * cell_size_m
    return x, y, ix0, iy0, nx, ny


def travel_time_surface(stem: str, cells: gpd.GeoDataFrame, shape: tuple,
                        ix0: int, iy0: int) -> tuple[np.ndarray, gpd.GeoDataFrame]:
    """Median travel time per cell, as a 2-D array for contouring and as cells.

    Unreachable cells are NaN in the array and dropped from the returned cells.
    """
    times = pd.read_parquet(GRIDS / f"{stem}.parquet")[["id", "p50"]]
    joined = cells.merge(times, on="id", how="left")
    surface = np.full(shape, np.nan, dtype="float32")
    surface[joined["iy"].to_numpy() - iy0, joined["ix"].to_numpy() - ix0] = joined["p50"]
    return surface, joined.dropna(subset=["p50"])


def draw_bands(ax, surface: np.ndarray, reach: gpd.GeoDataFrame, x: np.ndarray,
               y: np.ndarray, sigma: float) -> None:
    """Fill the travel-time bands, smoothed by default."""
    bands = [b for b, _, _ in BANDS]
    colors = [c for _, c, _ in BANDS]
    if sigma <= 0:  # "honest pixels": the computed squares, exactly as they are
        lower = 0
        for band, color, _ in BANDS:
            part = reach[(reach["p50"] > lower) & (reach["p50"] <= band)]
            if len(part):
                part.plot(ax=ax, color=color, linewidth=0, zorder=3)
            lower = band
        return
    # Blur the surface, not the shapes: unreachable cells sit above the last band so the
    # 60-minute contour closes instead of running off to the grid edge.
    filled = np.where(np.isnan(surface), UNREACHABLE_MINUTES, surface)
    smoothed = gaussian_filter(filled, sigma=sigma, mode="nearest")
    ax.contourf(x, y, smoothed, levels=[0] + bands, colors=colors, zorder=3, antialiased=True)


# --------------------------------------------------------------------------
# panel furniture
# --------------------------------------------------------------------------
def scale_bar(ax, extent_m: float) -> None:
    """A scale bar, because each origin's figure is fitted to its own reach."""
    choices = [m for m in (1, 2, 5, 10) if m * MI_TO_M <= extent_m / 3] or [1]
    miles = choices[-1]
    length = miles * MI_TO_M
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    pad = (x1 - x0) * 0.05
    x_start = x1 - pad - length
    y = y0 + (y1 - y0) * 0.05
    ax.plot([x_start, x_start + length], [y, y], color=INK, lw=2.2, solid_capstyle="butt",
            zorder=7, path_effects=halo(3.4))
    ax.text(x_start + length / 2, y + (y1 - y0) * 0.014, f"{miles} mi", ha="center",
            va="bottom", color=INK, fontsize=8.5, zorder=7, path_effects=halo())


def draw_landmarks(ax, landmarks: gpd.GeoDataFrame, origin_point) -> None:
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    # A landmark sitting on top of the origin just collides with the origin's own label.
    too_close = max(900.0, (x1 - x0) * 0.05)
    for _, lm in landmarks.iterrows():
        px, py = lm.geometry.x, lm.geometry.y
        if not (x0 < px < x1 and y0 < py < y1):
            continue
        if origin_point.distance(lm.geometry) < too_close:
            continue
        ax.plot([px], [py], marker="o", markersize=2.6, color=INK2, zorder=6,
                path_effects=halo(2.0))
        ax.text(px, py + (y1 - y0) * 0.012, lm["name"], ha="center", va="bottom",
                color=INK2, fontsize=7.8, zorder=6, path_effects=halo(2.2))


def draw_origin(ax, point, label: str) -> None:
    ax.scatter([point.x], [point.y], s=95, marker="o", c=INK, edgecolors=SURFACE,
               linewidths=2.0, zorder=8)
    y0, y1 = ax.get_ylim()
    ax.text(point.x, point.y - (y1 - y0) * 0.022, label, ha="center", va="top", color=INK,
            fontsize=9, fontweight="bold", zorder=8, path_effects=halo(3.0))


# --------------------------------------------------------------------------
def render_origin(origin: dict, scenarios: list[dict], results: dict, cfg: dict,
                  cells: gpd.GeoDataFrame, routes: gpd.GeoSeries, landmarks: gpd.GeoDataFrame,
                  basemap: dict, city_sq_mi: float, feed_version: str) -> Path:
    render = cfg["render"]
    routing = cfg["routing"]
    cell_size_m = float(cfg["grid"]["cell_size_m"])
    callouts = (cfg.get("callouts") or {}).get(origin["slug"], {}) or {}
    x, y, ix0, iy0, nx, ny = lattice(cells, cell_size_m)

    surfaces, reaches, reach_bounds = {}, {}, []
    for s in scenarios:
        stem = f"{origin['slug']}__{s['id']}"
        surface, reach = travel_time_surface(stem, cells, (ny, nx), ix0, iy0)
        surfaces[s["id"]], reaches[s["id"]] = surface, reach
        rows, cols = np.where(~np.isnan(surface))
        if len(rows):
            reach_bounds.append((x[cols.min()], y[rows.min()], x[cols.max()], y[rows.max()]))

    origin_pt = gpd.GeoSeries(gpd.points_from_xy([origin["lon"]], [origin["lat"]]),
                              crs="EPSG:4326").to_crs(CRS).iloc[0]
    if reach_bounds:
        minx = min(b[0] for b in reach_bounds); miny = min(b[1] for b in reach_bounds)
        maxx = max(b[2] for b in reach_bounds); maxy = max(b[3] for b in reach_bounds)
    else:
        minx = maxx = origin_pt.x
        miny = maxy = origin_pt.y
    minx, maxx = min(minx, origin_pt.x), max(maxx, origin_pt.x)
    miny, maxy = min(miny, origin_pt.y), max(maxy, origin_pt.y)

    # One square extent for all four panels, never tighter than min_extent_miles.
    cx_, cy_ = (minx + maxx) / 2, (miny + maxy) / 2
    span = max(maxx - minx, maxy - miny) * 1.12
    span = max(span, float(render["min_extent_miles"]) * MI_TO_M)
    minx, maxx = cx_ - span / 2, cx_ + span / 2
    miny, maxy = cy_ - span / 2, cy_ + span / 2

    panel = 4.6
    left_in, top_in, bottom_in, right_in = 0.3, 2.5, 1.45, 0.3
    fig_w = left_in + 2 * panel + right_in
    fig_h = top_in + 2 * panel + bottom_in
    fig, axes = plt.subplots(2, 2, figsize=(fig_w, fig_h), facecolor=SURFACE)
    fig.subplots_adjust(left=left_in / fig_w, right=1 - right_in / fig_w,
                        top=1 - top_in / fig_h, bottom=bottom_in / fig_h,
                        wspace=0.03, hspace=0.12)

    baseline_km2 = results[(origin["slug"], scenarios[0]["id"])]["km2"]
    for idx, s in enumerate(scenarios):
        ax = axes[idx // 2][idx % 2]
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_aspect("equal")
        ax.set_facecolor(SURFACE)
        draw_basemap(ax, basemap)
        routes.plot(ax=ax, color=MUTED, linewidth=0.45, alpha=0.45, zorder=2)

        result = results[(origin["slug"], s["id"])]
        if result["routed"]:
            draw_bands(ax, surfaces[s["id"]], reaches[s["id"]], x, y,
                       float(render["smoothing_sigma_cells"]))
        draw_landmarks(ax, landmarks, origin_pt)
        draw_origin(ax, origin_pt, origin["name"])
        scale_bar(ax, maxx - minx)

        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(HAIRLINE); spine.set_linewidth(0.9)

        when = pd.Timestamp(s["date"]).strftime("%a, %b %d").replace(" 0", " ")
        ax.set_title(f"{s['label']}", color=INK, fontsize=13, fontweight="bold",
                     loc="left", pad=8)
        # Date inside the panel, so it can't collide with the title or the legend row.
        ax.text(0.98, 0.972, when, transform=ax.transAxes, ha="right", va="top",
                color=INK2, fontsize=9, zorder=9, path_effects=halo(2.6))

        headline = fmt_sq_mi(result["km2"]) if result["routed"] else "Nowhere"
        change = fmt_change(result["km2"], baseline_km2) if result["routed"] and idx else None
        ax.text(0.035, 0.965, headline, transform=ax.transAxes, ha="left", va="top",
                color=INK, fontsize=20, fontweight="bold", zorder=9, path_effects=halo(3.4))
        sub = "within an hour" if result["routed"] else "no buses run"
        if change:
            sub += f" · {change}"
        ax.text(0.035, 0.905, sub, transform=ax.transAxes, ha="left", va="top",
                color=INK2, fontsize=9.5, zorder=9, path_effects=halo(2.8))

        note = callouts.get(s["id"]) or (None if result["routed"] else "No buses run on Sundays")
        if note:
            ax.text(0.035, 0.035, note, transform=ax.transAxes, ha="left", va="bottom",
                    color=INK, fontsize=9.5, zorder=9,
                    bbox=dict(boxstyle="round,pad=0.4", fc=SURFACE, ec=HAIRLINE, lw=0.9))

    # --- titles, legend, footer ---
    left = left_in / fig_w
    # The title drops any parenthetical ("Bryan Health East Campus (48th & A)"), which
    # otherwise runs off the page; the full name still labels the marker on every panel.
    short_name = origin["name"].split(" (")[0]
    title = f"Where can the bus get you from {short_name}?"
    fig.text(left, 1 - 0.42 / fig_h, title, color=INK,
             fontsize=21 if len(title) <= 60 else 18, fontweight="bold", ha="left", va="top")
    fig.text(left, 1 - 0.95 / fig_h,
             "Shaded areas are everywhere you could reach within an hour on the StarTran bus,\n"
             "including the walk at each end and the wait in between. Same place, four different "
             "times of the week.",
             color=INK2, fontsize=11, ha="left", va="top", linespacing=1.5)
    handles = [Patch(facecolor=c, edgecolor="none", label=lab) for _, c, lab in BANDS]
    handles += [Line2D([], [], color=MUTED, lw=1.3, label="Bus routes"),
                Line2D([], [], marker="o", color="none", markerfacecolor=INK,
                       markeredgecolor=SURFACE, markersize=9, label="Starting point")]
    legend = fig.legend(handles=handles, loc="upper left",
                        bbox_to_anchor=(left, 1 - 1.78 / fig_h), ncol=len(handles),
                        frameon=False, fontsize=10, handlelength=1.5, columnspacing=1.7)
    for t in legend.get_texts():
        t.set_color(INK2)

    walk = fmt_walk_distance(float(routing["max_walk_minutes"]) / 60
                             * float(routing["speed_walking_kmh"]) * 1000)
    footer = "\n".join([
        f"Method: scheduled StarTran service, feed {feed_version} — not real-time performance.",
        f"Travel times are the median across a {routing['departure_time_window_minutes']}"
        f"-minute departure window, so they include a typical wait. Walking "
        f"{fmt_mph(float(routing['speed_walking_kmh']))}, up to {walk} to and from stops.",
        "Gaps inside a shaded area are places too far from any stop to walk to. Dashed "
        f"outline: Lincoln city limits, about {city_sq_mi:.0f} sq mi. Scale differs "
        "between origins.",
        f"{CREDIT} · Full method and code: github.com/loganserv44/WhereCanTheBusGetMe",
    ])
    fig.text(left, 0.3 / fig_h, footer, color=MUTED, fontsize=8.5, ha="left", va="bottom",
             linespacing=1.7)

    PANELS.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    png = PANELS / f"{origin['slug']}.png"
    fig.savefig(png, dpi=int(render["dpi"]), facecolor=SURFACE)
    plt.close(fig)

    with Image.open(png) as im:
        width = int(render["web_width_px"])
        web = im.convert("RGB").resize((width, round(im.height * width / im.width)),
                                       Image.LANCZOS)
        web.save(WEB / f"{origin['slug']}.webp", quality=82, method=6)
    webp = WEB / f"{origin['slug']}.webp"
    print(f"    {png} ({png.stat().st_size / 1e6:.1f} MB), "
          f"{webp} ({webp.stat().st_size / 1e6:.2f} MB)")
    return png


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origin", help="render only this origin slug")
    ap.add_argument("--rebuild-basemap", action="store_true",
                    help="re-extract the basemap layers from the OSM clip")
    args = ap.parse_args()

    cfg = load_config()
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scenarios, origins = man["scenarios"], man["origins"]
    results = {(r["origin"], r["scenario"]): r for r in man["results"]}
    if args.origin:
        origins = [o for o in origins if o["slug"] == args.origin]
        if not origins:
            sys.exit(f"ERROR: no origin '{args.origin}'")

    grid = cfg["paths"]["grid"]
    cells = gpd.read_file(grid, layer="cells")[["id", "ix", "iy", "geometry"]]
    basemap = build_basemap(cfg, rebuild=args.rebuild_basemap)
    city_sq_mi = km2_to_sq_mi(basemap["city_limits"].area.iloc[0] / 1e6) \
        if len(basemap["city_limits"]) else float("nan")

    import zipfile
    from shapely.geometry import LineString
    with zipfile.ZipFile(cfg["paths"]["gtfs"]) as zf:
        with zf.open("shapes.txt") as fh:
            shapes = pd.read_csv(fh, encoding="utf-8-sig").sort_values(
                ["shape_id", "shape_pt_sequence"])
        with zf.open("feed_info.txt") as fh:
            feed_version = str(pd.read_csv(fh, dtype=str, encoding="utf-8-sig")
                               ["feed_version"].iloc[0])
    routes = gpd.GeoSeries([LineString(g[["shape_pt_lon", "shape_pt_lat"]].to_numpy())
                            for _, g in shapes.groupby("shape_id") if len(g) > 1],
                           crs="EPSG:4326").to_crs(CRS)

    lm = pd.DataFrame(cfg["landmarks"])
    landmarks = gpd.GeoDataFrame(lm[["name"]], geometry=gpd.points_from_xy(lm["lon"], lm["lat"]),
                                 crs="EPSG:4326").to_crs(CRS)

    print(f"Rendering {len(origins)} figure(s), smoothing sigma "
          f"{cfg['render']['smoothing_sigma_cells']} cells")
    for o in origins:
        print(f"  {o['name']}")
        render_origin(o, scenarios, results, cfg, cells, routes, landmarks,
                      basemap, city_sq_mi, feed_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
