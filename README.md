# Where the Bus Goes

**Isochrone maps of StarTran transit access in Lincoln, NE.**

How far can you actually get from a given point in Lincoln using the bus, walking, and
waiting — and how does that reach change depending on what time and day it is?

The deliverable is a set of side-by-side panels: same origin, four departure scenarios
(Tue 8am, Tue 9pm, Sat 6pm, Sun noon). The comparison is the point, not any single map.
StarTran runs no Sunday service, so the fourth panel is blank.

> **Status: planning / not yet implemented.** Nothing in `src/` exists yet. See
> [PLAN.md](PLAN.md) for the full scope, methodology, and task breakdown.

## Method (short version)

Scheduled-service isochrones computed with [r5py](https://r5py.readthedocs.io/)
(a Python wrapper for Conveyal R5) over StarTran's GTFS feed and an OpenStreetMap
street network. Travel times are the **median across a 60-minute departure window** —
leaving at 8:00 versus 8:04 can differ by 15 minutes if you just missed a bus, and a
single departure time would misrepresent that. Maximum 800 m walk to and from stops.

This reflects the **published schedule**, not real-time performance.

Full methodology, including feed version and scenario dates, will live in
`methodology.md` once the pipeline runs.

## Data sources

| Source | Used for | URL |
| --- | --- | --- |
| StarTran GTFS | Transit schedule | `https://startran.connexionz.net/rtt/public/resource/gtfs.zip` |
| Geofabrik Nebraska extract | Walking network | `https://download.geofabrik.de/north-america/us/nebraska-latest.osm.pbf` |

Neither is committed to this repo — both are fetched by `src/fetch_data.py` and
recorded with checksums in `data/raw/MANIFEST.json`.

## Setup

Requires **Python 3.11+** and **Java 21** (r5py runs the R5 routing engine on the JVM).
A conda environment is the least painful way to get both:

```bash
conda env create -f environment.yml
conda activate wherecanthebusgetme
```

## Running the pipeline

```bash
python src/fetch_data.py        # download GTFS + OSM
python src/build_network.py     # clip OSM, build R5 network, generate destination grid
python src/compute_isochrones.py  # travel-time grids per origin x scenario
python src/render_panels.py     # the comparison figures
```

## Repository layout

```
config.yml      scenarios, time bands, grid size, walk limits
data/origins.csv  the chosen origin points (curated, tracked)
src/            pipeline scripts
notebooks/      exploratory analysis, premise verification
output/         grids, isochrone GeoJSON, rendered panels
methodology.md  the public methodology statement
PLAN.md         scope, decisions, and task breakdown
```

## License

TBD.
