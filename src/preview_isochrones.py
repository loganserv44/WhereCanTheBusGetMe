#!/usr/bin/env python3
"""Task 3 preview -- every origin x scenario on one contact sheet, for checking results.

This is a checking tool, not the Task 5 design: there is no basemap. Bus routes from
the GTFS shapes give enough context to see whether reach follows the network.

Design choices (from the dataviz method):
  - Travel-time bands are ordered, so they take one hue: a blue ramp, darkest = reached
    soonest. Steps 700/550/400/250; the lightest stops at 250 so the 45-60 min band still
    stands out against the background. Validated as an ordinal ramp (all checks pass).
  - Every panel shares one extent, so area compares directly across the grid.
  - One headline number per panel (km^2 within 60 min); the full numbers go to a CSV
    beside the image, so nothing is readable only as color.

Run inside the environment, after compute_isochrones.py:
    micromamba run -n wherecanthebusgetme python src/preview_isochrones.py
"""

from __future__ import annotations

import json
import textwrap
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from shapely.geometry import LineString  # noqa: E402

from build_network import load_config  # noqa: E402

warnings.filterwarnings("ignore", category=RuntimeWarning)

CRS = "EPSG:32614"
OUT_DIR = Path("output/preview")
MANIFEST = Path("output/run_manifest.json")

# Chart chrome, reference palette light mode.
SURFACE, INK, INK2, MUTED, HAIRLINE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
# Ordinal ramp (blue 700 / 550 / 400 / 250): darkest = reachable soonest.
BANDS = [(15, "#0d366b", "0–15 min"), (30, "#1c5cab", "15–30 min"),
         (45, "#3987e5", "30–45 min"), (60, "#86b6ef", "45–60 min")]

plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]


def area_label(km2: float) -> str:
    # One decimal for small areas: "1.4 km²" and "1.3 km²" are the story; "1 km²" hides it.
    return f"{km2:.1f} km²" if km2 < 10 else f"{km2:.0f} km²"


def corner_label(ax, text: str, color: str, weight: str = "bold") -> None:
    ax.text(0.04, 0.04, text, transform=ax.transAxes, ha="left", va="bottom", color=color,
            fontsize=9, fontweight=weight, zorder=6,
            bbox=dict(boxstyle="round,pad=0.25", fc=SURFACE, ec="none", alpha=0.9))


def main() -> int:
    cfg = load_config()
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scenarios, origins = man["scenarios"], man["origins"]
    results = {(r["origin"], r["scenario"]): r for r in man["results"]}
    routing = man["routing"]

    with zipfile.ZipFile(cfg["paths"]["gtfs"]) as zf, zf.open("shapes.txt") as fh:
        shapes = pd.read_csv(fh, encoding="utf-8-sig").sort_values(["shape_id", "shape_pt_sequence"])
    routes = gpd.GeoSeries(
        [LineString(g[["shape_pt_lon", "shape_pt_lat"]].to_numpy())
         for _, g in shapes.groupby("shape_id") if len(g) > 1],
        crs="EPSG:4326").to_crs(CRS)

    iso = {}
    for o in origins:
        for s in scenarios:
            path = Path("output/isochrones") / f"{o['slug']}__{s['id']}.geojson"
            try:
                g = gpd.read_file(path)
                iso[(o["slug"], s["id"])] = g.to_crs(CRS) if len(g) else None
            except Exception:  # an empty FeatureCollection may not parse as a layer
                iso[(o["slug"], s["id"])] = None

    pts = gpd.GeoSeries(gpd.points_from_xy([o["lon"] for o in origins], [o["lat"] for o in origins]),
                        crs="EPSG:4326").to_crs(CRS)
    bounds = [g.total_bounds for g in iso.values() if g is not None] + [pts.total_bounds]
    pad = 1200
    minx, miny = min(b[0] for b in bounds) - pad, min(b[1] for b in bounds) - pad
    maxx, maxy = max(b[2] for b in bounds) + pad, max(b[3] for b in bounds) + pad

    nrow, ncol = len(origins), len(scenarios)
    panel_w = 3.0
    panel_h = panel_w * (maxy - miny) / (maxx - minx)
    left_in, top_in, bottom_in, right_in = 1.9, 2.25, 0.55, 0.25
    fig_w = left_in + ncol * panel_w + right_in
    fig_h = top_in + nrow * panel_h + bottom_in + 0.08 * nrow
    fig, axes = plt.subplots(nrow, ncol, figsize=(fig_w, fig_h), facecolor=SURFACE, squeeze=False)
    fig.subplots_adjust(left=left_in / fig_w, right=1 - right_in / fig_w,
                        top=1 - top_in / fig_h, bottom=bottom_in / fig_h, wspace=0.04, hspace=0.06)

    for i, o in enumerate(origins):
        origin_pt = pts.iloc[i]
        for j, s in enumerate(scenarios):
            ax = axes[i, j]
            ax.set_facecolor(SURFACE)
            g = iso[(o["slug"], s["id"])]
            if g is not None:
                for band, color, _ in BANDS:
                    part = g[g["band"] == band]
                    if len(part):
                        part.plot(ax=ax, color=color, edgecolor=SURFACE, linewidth=0.25, zorder=2)
            routes.plot(ax=ax, color=MUTED, linewidth=0.3, alpha=0.55, zorder=3)
            ax.scatter([origin_pt.x], [origin_pt.y], s=30, c=INK, edgecolors=SURFACE,
                       linewidths=1.3, zorder=5)

            r = results[(o["slug"], s["id"])]
            # Same corner for both, so the label never sits on top of the origin marker.
            if r["routed"]:
                corner_label(ax, area_label(r["km2"]), INK)
            else:
                corner_label(ax, "No StarTran service", INK2, weight="normal")

            ax.set_xlim(minx, maxx)
            ax.set_ylim(miny, maxy)
            ax.set_aspect("equal")
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_color(HAIRLINE)
                spine.set_linewidth(0.8)
            if i == 0:
                ax.set_title(s["label"], color=INK, fontsize=11, fontweight="bold", pad=6)
            if j == 0:
                ax.text(-0.06, 0.5, "\n".join(textwrap.wrap(o["name"], 22)), transform=ax.transAxes,
                        ha="right", va="center", color=INK, fontsize=10, fontweight="bold")

    left = left_in / fig_w
    dates = ", ".join(sorted({s["date"] for s in scenarios}))
    fig.text(left, 1 - 0.38 / fig_h, "Where can the bus get you in an hour?", color=INK,
             fontsize=17, fontweight="bold", ha="left", va="top")
    fig.text(left, 1 - 0.85 / fig_h,
             f"Median door-to-door travel time by StarTran bus and walking, from {nrow} Lincoln origins "
             f"at {ncol} times of the week.\n"
             f"{routing['departure_time_window_minutes']}-minute departure window · walking "
             f"{routing['speed_walking_kmh']} km/h · walks capped at {routing['max_walk_minutes']} min · "
             f"dates {dates}",
             color=INK2, fontsize=9.5, ha="left", va="top", linespacing=1.45)
    handles = [Patch(facecolor=c, edgecolor="none", label=lab) for _, c, lab in BANDS]
    handles += [Line2D([], [], color=MUTED, lw=1.2, label="Bus route"),
                Line2D([], [], marker="o", color="none", markerfacecolor=INK,
                       markeredgecolor=SURFACE, markersize=7, label="Origin")]
    legend = fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(left, 1 - 1.55 / fig_h),
                        ncol=len(handles), frameon=False, fontsize=9.5, handlelength=1.4,
                        columnspacing=1.6)
    for t in legend.get_texts():
        t.set_color(INK2)
    fig.text(left, 0.22 / fig_h,
             "Preview for checking Task 3 results — no basemap; the final design comes in Task 5. "
             f"Numbers: {OUT_DIR.as_posix()}/task3_summary.csv",
             color=MUTED, fontsize=8.5, ha="left", va="bottom")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png = OUT_DIR / "task3_contact_sheet.png"
    fig.savefig(png, dpi=150, facecolor=SURFACE)
    pd.DataFrame(man["results"]).to_csv(OUT_DIR / "task3_summary.csv", index=False)
    print(f"wrote {png} ({fig_w:.1f} x {fig_h:.1f} in at 150 dpi) and task3_summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
