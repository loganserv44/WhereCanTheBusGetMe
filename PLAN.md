# Where the Bus Goes (Working Title)
## Isochrone maps of StarTran transit access in Lincoln, NE

### Goal

Static isochrone maps showing how far you can travel from a given point in Lincoln using
StarTran, walking, and waiting — and how that reach changes depending on what time and
day it is.

The core deliverable is a set of side-by-side panels: same origin, four different
departure times. The comparison is the point, not any single map.

### The premise — VERIFIED 2026-09-10 (feed `20260828`)

Task 0 is complete. Run `python src/premise_check.py` to reproduce; full findings in
[methodology.md](methodology.md).

StarTran runs **18 routes**, and **no Sunday service** — confirmed two independent ways:
neither of the feed's two service patterns has `sunday=1`, and the feed contains zero
service-*adding* date exceptions, so none can be added by date either. Its single
exception removes weekday service on Labor Day.

| Day type | Trips | First departure | Last arrival |
| --- | --- | --- | --- |
| Weekday | 1,101 | 05:40 | 21:50 |
| Saturday | 295 (14 of 18 routes) | 06:40 | 19:35 |
| **Sunday** | **0** | — | — |

The originally assumed spans (weekday ~5:15am–9:55pm, Saturday ~5:55am–7:05pm) were
close but wrong; the verified figures are above. The argument is unaffected.

The reachable area from any point collapses at night, shrinks further Saturday evening,
and goes to zero on Sunday. The blank Sunday panel requires no computation and is the
strongest graphic in the project.

**The network-wide span overstates what a rider can use.** Of 18 weekday routes, only 8
still operate after 9:00pm and only 3 after 9:30pm. This shapes the evening panel and
the open question below.

### Scope (v1)

- 3–5 origin points, chosen deliberately, not "any stop"
- 4 departure scenarios: Tue 8am, Tue 9pm, Sat 6pm, Sun noon
- Travel time bands: 15 / 30 / 45 / 60 min
- Static output. No server, no live routing.

### Decisions (previously open)

- **Output format**: static PNG panels — matplotlib + muted basemap tiles. One 1×4
  comparison figure per origin, plus a combined contact sheet. GeoJSON is exported as a
  reusable intermediate but there is no web map in v1.
- **Origins**: the pipeline takes an arbitrary origin list. Candidates are proposed with
  rationale from the GTFS + city data (see Task 4), and the final 3–5 are approved before
  the expensive full run.
- **Overlays**: none. Bare isochrone bands + origin marker + street basemap. "Can you
  reach the grocery store" is answered by choosing origins near those trips, not by
  drawing pins.
- **Sunday panel**: truly blank — origin marker and a "No StarTran service" caption, no
  reachable area, no walk-only floor. Matches the premise and maximises the graphic.

### Resources

- **GTFS**: StarTran feed, served directly by the agency (Connexionz infrastructure) at
  `https://startran.connexionz.net/rtt/public/resource/gtfs.zip` — no account, no auth.
  This is the preferred source and resolves the Transitland 401.
  - Fallback: Mobility Database mirror for StarTran (no account required).
- **OSM street network** (for walking access/egress): Geofabrik Nebraska extract
  `https://download.geofabrik.de/north-america/us/nebraska-latest.osm.pbf`, clipped to
  Lincoln.
- **Routing engine**: `r5py` (Python wrapper for Conveyal R5). It natively computes
  travel time over a departure-time window and returns a chosen percentile (median) —
  exactly the methodology below. Requires Java 21 (documented in the README; a conda
  environment with conda-forge `openjdk` is the recommended setup).

### Methodology (state these publicly, in methodology.md)

- Max walking distance to/from stops: 800 m (~10 min at 4.8 km/h), applied to both the
  access and egress leg.
- Departure time computed across a 60-minute window, reporting the **median** travel time
  per destination (leaving at 8:00 vs 8:04 can differ by 15 min if you just missed a
  bus). The 25th/75th percentiles are also stored for an optional spread appendix.
- Feed version and download date (recorded in `data/raw/MANIFEST.json` with a checksum).
- Scenario calendar dates and why they were chosen (a normal week inside the feed's
  validity window, clear of reduced-service holidays).
- Grid resolution, walking speed, and R5/r5py version.
- Scheduled service only — not real-time performance.

### Pipeline / task breakdown

**Task 0 — Verify the premise (blocking gate).**
Load the feed and confirm: no `service_id` runs Sundays (weekly bitmask *and* no
`calendar_dates` exception adds Sunday service); earliest/latest service times per day
type roughly match the premise; route count ~18–20; note `feed_info` version and
`calendar` start/end dates. Write findings to `methodology.md`. If Sunday service exists
or spans differ materially, stop and revise scope.

**Task 1 — Data acquisition (`src/fetch_data.py`).**
Download the GTFS zip and the Nebraska OSM extract to `data/raw/`, record SHA-256 +
timestamp in `data/raw/MANIFEST.json`. Idempotent: skip when the file is present and the
hash matches, unless `--force`.

**Task 2 — Build routing inputs (`src/build_network.py`).**
Clip Nebraska OSM to a Lincoln bbox (city limits + ~5 km buffer) with `pyosmium` →
`data/processed/lincoln.osm.pbf`. Build `r5py.TransportNetwork(osm_pbf, [gtfs_zip])`
(r5py caches it). Generate a regular destination grid over the clipped area — default
150 m cells (tunable in `config.yml`), stored as `data/processed/grid.gpkg` with cell
centroids as destination points and cell polygons for rendering.

**Task 3 — Compute travel-time grids (`src/compute_isochrones.py`).**
For each (origin × scenario): `r5py.TravelTimeMatrixComputer` from the origin to all grid
centroids, `departure_time_window = 60 min`, `percentiles = [25, 50, 75]`,
`transport_modes = [TRANSIT, WALK]`, `max_time = 60 min`, walk speed 4.8 km/h, walk leg
cap ~800 m (verify the exact r5py kwarg against the installed version). Scenario datetimes
in America/Chicago: Tue 08:00, Tue 21:00, Sat 18:00, Sun 12:00 — concrete dates derived
in code from the feed's validity window. Output `output/grids/{origin}__{scenario}.parquet`
(cell_id, median_min) and dissolved-by-band polygons to `output/isochrones/*.geojson`.
Sunday: skip the routing call, emit an all-null grid with a blank-panel flag.

**Task 4 — Propose & finalize origins (`data/origins.csv`).**
Produce a candidate table (name, lat/lon, nearest stop, rationale). Basis: downtown
transfer hub / high-service node (best case); UNL City Campus edge (persona A); a
dense apartment area away from downtown (peripheral residential); a big-box retail /
grocery cluster; one low-frequency edge-of-network stop (worst case). User picks 3–5
before the full compute run. `origins.csv` columns: `slug,name,lat,lon,note`.

**Task 5 — Render panels (`src/render_panels.py`).**
Per origin, one 1×4 figure (Tue 8am / Tue 9pm / Sat 6pm / Sun noon). Grid cells shaded
by band with a colorblind-safe 4-step sequential ramp; unreachable cells unfilled;
optional dissolve-and-smooth to polygons with a raw-cell "honest pixels" mode available
via config. Origin marker; muted basemap (CartoDB Positron via `contextily`, tiles
cached); identical fixed extent across all four panels; Sunday panel = basemap + marker +
"No StarTran service" caption. Footer: feed version + date, 800 m walk limit, one-line
methodology. Export `output/panels/{origin}.png` at ~200 dpi + a contact sheet. Load the
`dataviz` skill before choosing the color ramp.

**Task 6 — Methodology write-up (`methodology.md`).**
Everything in the Methodology section above, filled in with actual values.

**Task 7 — Publish to GitHub Pages (`src/publish.py`).**
The finished panels are published to the existing personal site repo
(`loganserv44/loganserv44.github.io`) as a self-contained subfolder, matching the pattern
already used by `mail-in-ballot-search/` and `profsearch/`. `publish.py` takes the site
repo path (config or `--site-repo`) and:
- copies `output/panels/*.png` → `where-the-bus-goes/panels/`
- copies `output/isochrones/*.geojson` → `where-the-bus-goes/data/` (unused by the v1
  page, but it makes an interactive version additive later)
- renders `where-the-bus-goes/index.html` from a template, injecting the feed version,
  download date, scenario dates, and the methodology summary so the page cannot drift
  from what actually ran
- stages nothing and commits nothing — it prints what changed and leaves both repos for
  manual review and commit

Final URL: `https://loganserv44.github.io/where-the-bus-goes/`

### Project structure

```
config.yml                 # scenarios, bands, grid size, walk limit/speed, bbox
data/
  raw/          gtfs.zip, nebraska-latest.osm.pbf, MANIFEST.json   (gitignored)
  processed/    lincoln.osm.pbf, grid.gpkg, network cache, tiles/  (gitignored)
  origins.csv
src/
  fetch_data.py
  build_network.py
  compute_isochrones.py
  render_panels.py
  publish.py               # copy panels + render index.html into the Pages site repo
  common.py                # config load, slugify, paths, scenario-date derivation
  templates/
    page.html.j2           # the published project page
notebooks/
  premise_check.ipynb
output/
  grids/*.parquet
  isochrones/*.geojson
  panels/*.png
methodology.md
README.md
environment.yml            # conda env incl. openjdk 21
```

Git: `git init`, push to GitHub. Commit `src/`, `config.yml`, `data/origins.csv`,
`methodology.md`, `output/panels/`, `output/grids/`, `output/isochrones/`. Gitignore
`data/raw/` and `data/processed/` (regenerable). Orchestration: `run_all.py` (or a
Makefile) chaining fetch → build → compute → render.

### Verification

1. **Premise gate**: `notebooks/premise_check.ipynb` prints service spans per day type,
   asserts no Sunday service, cross-checked against one published StarTran PDF schedule.
2. **Network sanity**: one r5py trip (downtown hub → UNL, Tue 8am) returns a plausible
   ~10–20 min transit itinerary.
3. **Grid spot-checks**: for one origin/scenario, hand-verify 3–4 cells against Google
   Maps transit directions for the same date/time (rough agreement expected).
4. **Scenario contrast**: Tue 9pm reachable area visibly smaller than Tue 8am; Sat 6pm
   smaller still; Sunday blank. If Tue 9pm ≈ Tue 8am, the window or calendar handling is
   wrong.
5. **Panel review**: all four panels share one extent; legend, marker, footer metadata
   present; Sunday panel blank with caption.
6. Full run for all approved origins; visual review of the contact sheet.

### User Experience

**A** is a UNL student without a car. They know their nearest stop but can't picture what
it actually gets them. They see the reachable area shaded by travel time, and how it
shrinks after dark.

**B** works a shift ending at 9:30pm. The Tue 9pm panel shows whether the bus can still
get them home.

### Where this lives

- **Pipeline repo** (this one): `github.com/loganserv44/WhereCanTheBusGetMe`, public.
  The public pipeline is part of the argument — the Methodology section promises "here is
  exactly how this was computed," and the repo is what backs that up.
- **Published page**: `loganserv44.github.io/where-the-bus-goes/`, deployed from the
  separate site repo via Task 7. The two repos stay separate deliberately: the pipeline
  pulls a ~250 MB OSM extract and an R5 network cache, none of which belongs in a repo
  whose job is serving static files.

### Still open

- **Evening scenario times vs. the 60-minute window** (raised by Task 0, must be settled
  before Task 3). The methodology takes the median travel time across a 60-min departure
  window. At Tue 8am that window is uniformly served. At Tue 9pm it runs 21:00–22:00 when
  the last bus arrives at 21:50, and at Sat 6pm it runs 18:00–19:00 when only two routes
  operate past 19:00 — so both evening medians are computed across a largely empty
  window. Options: shift the evening scenarios earlier (e.g. Tue 8pm / Sat 5pm) so the
  windows sit inside service; keep the times and let the near-empty panels be the finding;
  or vary the window length by scenario (which breaks comparability across panels).
- License for the repo (MIT for the code vs. CC BY for the maps, or both).
- Whether the site's root `index.html` — currently a "Coming Soon" placeholder — should
  start linking out to the project pages. Out of scope here, but this project makes it
  three unlinked pages.

### Out of scope for v1

- Interactive web map (MapLibre + the exported GeoJSON) — additive later.
- POI / destination overlays.
- Real-time / observed performance (a GTFS-RT feed exists).
