# Where the Bus Goes

**Isochrone maps of StarTran transit access in Lincoln, NE.**

How far can you actually get from a given point in Lincoln using the bus, walking, and
waiting — and how does that reach change depending on what time and day it is?

The deliverable is a set of side-by-side panels: same origin, four departure scenarios
(Tue 8am, Tue 8pm, Sat 5pm, Sun noon). The comparison is the point, not any single map.
StarTran runs no Sunday service, so the fourth panel is blank.

> **Status: maps done, not yet published.** Tasks 0–5 are complete: the premise is
> verified, the network is built and checked, six origins chosen, travel times computed,
> and the six final figures rendered to `output/panels/` (print) and `output/web/` (web).
> There is also an hourly explorer — `output/web/explorer/index.html`, openable straight
> from disk — with one map per hour for four origins, and the chosen origin, day and hour
> kept in the URL so one map can be linked to. What's left is the site page and
> publishing. See [PLAN.md](PLAN.md) for the task breakdown.

## Method (short version)

Scheduled-service isochrones computed with [r5py](https://r5py.readthedocs.io/)
(a Python wrapper for Conveyal R5) over StarTran's GTFS feed and an OpenStreetMap
street network. Travel times are the **median across a 60-minute departure window** —
leaving at 8:00 versus 8:04 can differ by 15 minutes if you just missed a bus, and a
single departure time would misrepresent that. Walking is 2.9 mph, with up to a
10-minute walk (about half a mile) to and from stops.

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

Requires **Python 3.12** and **Java 22 or newer** (r5py runs the R5 routing engine on
the JVM). A conda-forge environment supplies both and keeps the JDK isolated, so your
system Java installation is untouched:

```bash
micromamba create -f environment.yml
micromamba activate wherecanthebusgetme
```

`conda env create -f environment.yml` works identically if you have conda. This project
was set up with [micromamba](https://mamba.readthedocs.io/en/latest/user_guide/micromamba.html)
— a single ~11 MB binary, no installer — because the Miniforge installer aborts on
Windows here.

> **Java version gotcha.** r5py's package metadata declares `openjdk >=21`, but it
> unconditionally passes `--enable-native-access=ALL-UNNAMED` to the JVM, and that flag
> only exists from JDK 22. On Java 21 the environment solves cleanly and then fails at
> runtime with `Unable to start JVM`. `environment.yml` pins `openjdk=25` for this
> reason — don't "helpfully" relax it back to 21.

On first use r5py downloads the R5 routing engine jar (~64 MB) into its own cache.

## Running the pipeline

```bash
python src/fetch_data.py          # download GTFS + OSM
python src/premise_check.py       # verify the no-Sunday-service premise against the feed
python src/build_network.py       # clip OSM, build R5 network, generate destination grid
python src/verify_network.py      # check the network against trips with known answers
python src/compute_isochrones.py  # travel-time grids per origin x scenario
python src/preview_isochrones.py  # quick-look contact sheet of every result
python src/stop_schedule.py       # bus times at each origin stop, for writing callouts
python src/render_panels.py       # the comparison figures + hourly explorer images
                                  #   (--skip-explorer for just the figures)
python src/publish.py             # copy panels + page into the Pages site repo
```

## Published output

The finished maps are published to
**[loganserv44.github.io/where-the-bus-goes](https://loganserv44.github.io/where-the-bus-goes/)**,
deployed from the separate [`loganserv44.github.io`](https://github.com/loganserv44/loganserv44.github.io)
repo. `src/publish.py` copies the rendered panels across and regenerates the page so its
stated feed version and scenario dates always match the run that produced the images.

This repo holds the pipeline; that one holds the website. They're kept separate because
the pipeline pulls a ~96 MB OSM extract, a 64 MB R5 jar, and a network cache, which have
no business in a repo that serves static files.

## Repository layout

```
config.yml        scenarios, time bands, grid size, walk limits
data/origins.csv  the chosen origin points (curated, tracked)
src/              pipeline scripts + the published page template
notebooks/        exploratory analysis, premise verification
output/           grids, isochrone GeoJSON, rendered panels
methodology.md    the public methodology statement
PLAN.md           scope, decisions, and task breakdown
```

## License

TBD.
