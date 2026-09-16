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

- 6 origin points, chosen deliberately, not "any stop" (see Task 4)
- 4 departure scenarios: Tue 8am, Tue 8pm, Sat 5pm, Sun noon (the evening times were
  moved an hour earlier so their departure windows sit inside service)
- Travel time bands: 15 / 30 / 45 / 60 min
- Static output. No server, no live routing.

### Decisions (previously open)

- **Output format**: static images, one figure per origin with a **2×2 grid** of panels
  (Tue 8am / Tue 8pm / Sat 5pm / Sun noon), built to read on a phone. The 24-panel
  contact sheet stays as an internal checking tool. GeoJSON is exported as a reusable
  intermediate but there is no web map in v1. (Changed 2026-09-15 from a 1×4 row, after
  reviewing the Task 3 preview.)
- **Units**: US customary everywhere a *reader* sees them: square miles, miles, feet
  and mph on maps, captions, tables and the published methodology. The computation stays
  metric (`config.yml`, the scripts, UTM meters, r5py's km/h), and one shared helper
  converts at display time. Decided 2026-09-15. Revised 2026-09-16: the earlier version
  also converted the config and code, which meant rewriting settings across four
  scripts and recomputing Tasks 2–3 for no difference a reader would see.
- **Origins**: the pipeline takes an arbitrary origin list. Candidates are proposed with
  rationale from the GTFS + city data (see Task 4), and the final six were approved before
  the expensive full run.
- **Overlays**: none. Bare isochrone bands + origin marker + a label-free tile basemap with our own landmark labels (see Task 5). "Can you
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
  exactly the methodology below. **Requires Java 22 or newer** — r5py's conda recipe
  claims `openjdk >=21`, but it passes `--enable-native-access=ALL-UNNAMED`, a flag that
  only exists from JDK 22, so on Java 21 the JVM refuses to start. `environment.yml`
  pins `openjdk=25` and keeps the JDK inside the environment, leaving the system Java
  untouched.

### Methodology (state these publicly, in methodology.md)

- Walking speed 4.7 km/h (1.31 m/s), the usual outdoor pace of healthy adults in a
  35-study meta-analysis (PubMed 33030707).
- Max walking time to/from stops: 10 min (~780 m at 4.7 km/h), applied to both the
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
Split into two halves with a commit between them. The risky unknown goes first.

*Part A — network (stopping point).*
1. Install `osmium-tool` (the `osmium extract` CLI). It is a separate package from
   `pyosmium`; hand-rolling a PBF clip in pyosmium risks dropping way nodes at the bbox
   edge, which breaks the street network without any error.
2. Derive the bbox from the GTFS stop extents plus a ~5 km buffer, so the clip is set
   by the data rather than a guessed city boundary.
3. `osmium extract --bbox ... --strategy complete_ways` → `data/processed/lincoln.osm.pbf`.
4. Build `r5py.TransportNetwork(lincoln.osm.pbf, [gtfs.zip])`. **This is the first time
   R5 does real work on Java 25**, which it isn't officially built for. If it fails,
   drop the pin to JDK 22/23 and retry.
5. Commit. **Checkpoint:** the clipped extract exists and R5 builds a Lincoln network
   from it without error.

   **Part A DONE (2026-09-14).** Clip: 100.3 MB → 11.0 MB in 3.5 s. R5 v7.5.1 built the
   network on Java 25.0.2 in 16.6 s using ~0.47 GB of heap (capped at 4 GB via
   `config.yml`). Two findings for Part B:
   - The built network's extent (`-96.911, 40.590, -96.464, 41.017`) is larger than the
     clip box, because `complete_ways` keeps whole every road crossing the edge. That
     is expected. **The grid must be built from the clip box, not `network.extent`.**
   - A 150 m grid over the box is ~31,700 cells, and most of the buffer is farmland no
     trip can reach. Consider limiting cells to those within walking distance of a stop.

*Part B — grid and verification.*
6. Generate a regular 150 m destination grid over the bbox in a metric CRS (UTM 14N,
   EPSG:32614), with cell polygons for rendering and WGS84 centroids for r5py →
   `data/processed/grid.gpkg`. Tunable in `config.yml`.
7. Verification (`src/verify_network.py`): trips with known right answers at Tue 08:00.
   Downtown → UNL City Campus is only ~850 m, so it checks that *walking* wins.
   Downtown → UNL East Campus (~3.5 km) checks that *transit* wins. One origin → the
   full grid times a Task 3–style run.
8. Commit.

   **Part B DONE (2026-09-15).** Grid: 31,922 cells of 150 m, 12.2 MB, built in 2 s.
   Verification passed: walking to the Nebraska Union takes 14.8 min and the earliest
   bus arrives at 17.8 min; East Campus takes 28.7 min on route 42 Bethany against 58.5
   min on foot. One origin → the full grid takes **2.0 s**. Decisions and findings:
   - **Full grid kept.** No reachable cell lies beyond 781 m from a stop, so trimming
     would lose nothing, but at 2 s per origin it would save nothing either.
   - **Compare itineraries door to door.** Adding up `DetailedItineraries` legs misses
     time spent at the origin before the first leg, and made a two-bus option look
     faster than walking. The first version of the check failed for exactly this reason.
   - **Carry into Task 3:** r5py defaults to walking at 3.6 km/h and a 10-minute
     departure window. The methodology says 4.8 km/h and 60 minutes, so set both
     explicitly on every call.
   - Task 3's compute is trivial: 16 origin × scenario runs at ~2 s each.

**Task 3 — Compute travel-time grids (`src/compute_isochrones.py`).**
For each (origin × scenario): `r5py.TravelTimeMatrixComputer` from the origin to all grid
centroids, `departure_time_window = 60 min`, `percentiles = [25, 50, 75]`,
`transport_modes = [TRANSIT, WALK]`, `max_time = 60 min`, walk speed 4.7 km/h, walk leg
cap ~800 m (verify the exact r5py kwarg against the installed version). Scenario datetimes
in America/Chicago: Tue 08:00, Tue 20:00, Sat 17:00, Sun 12:00, on Tue 2026-09-15, Sat
2026-09-19 and Sun 2026-09-20. The dates are set explicitly in `config.yml` and checked
in code against the feed (right weekday, inside its validity, no calendar exception). Output `output/grids/{origin}__{scenario}.parquet`
(cell_id, median_min) and dissolved-by-band polygons to `output/isochrones/*.geojson`.
Sunday: skip the routing call, emit an all-null grid with a blank-panel flag.

**Task 3 DONE (2026-09-15).** 6 origins × 4 scenarios = 24 grids in about 20 s, on a
grid trimmed to 9,994 cells within 1 km of a stop. Every scenario date is checked
against the feed before routing, and Sunday comes out as "no service" automatically.
Within 60 minutes, downtown reaches 111 km² at Tue 8am, 26 km² at Tue 8pm and 54 km²
at Sat 5pm. At Tue 8pm, Bryan East (1.4 km²) and Briarpark (1.3 km²) are walking only.
Full table in `methodology.md`. A preview contact sheet is at
`output/preview/task3_contact_sheet.png`, with its numbers in `task3_summary.csv`.
Decisions made during the task: walking at 4.7 km/h (a 35-study meta-analysis), evening
scenarios moved to 8pm/5pm, and six origins.

**Task 4 — Propose & finalize origins (`data/origins.csv`).**
Produce a candidate table (name, lat/lon, nearest stop, rationale). Basis: downtown
transfer hub / high-service node (best case); UNL City Campus edge (persona A); a
dense apartment area away from downtown (peripheral residential); a big-box retail /
grocery cluster; one low-frequency edge-of-network stop (worst case). **Done: six picked**
(downtown, UNL East Campus, N 27th & Superior, Bryan Health East Campus, S 40th &
Briarpark, 48th & R), with rationale in `data/origins.csv`. User picks 3–5
before the full compute run. `origins.csv` columns: `slug,name,lat,lon,note`.

**Units at display time (part of Task 5).** *Revised 2026-09-16.* The computation stays
metric, and nothing is recomputed. A small `src/units.py` holds the conversions (km² → sq
mi, km/h → mph, m → ft or mi), and every number a reader sees goes through it, so there
is one place a conversion could be wrong. What readers see:

| Computed (metric) | Shown to readers |
| --- | --- |
| Walking speed 4.7 km/h | 2.9 mph |
| Walk limit 10 min (~780 m) | 10 minutes, about half a mile |
| Grid square 150 m | about 490 ft |
| Grid trim 1 km from a stop | 0.62 mi |
| Clip buffer 5 km | 3.1 mi |
| Reach, e.g. downtown Tue 8am 111 km² | 43 sq mi |

`methodology.md` and `README.md` switch to US units in the same change as the final
figures. Metric stays in parentheses only when quoting a source (e.g. the 1.31 m/s
walking-speed study). `config.yml` and the scripts stay metric.

**Task 5 — Render the final figures (`src/render_panels.py`).** *Not started.* The design
was agreed 2026-09-15 after reviewing the Task 3 preview, which was hard to read: nothing
to orient by, postage-stamp panels, staircase edges, an abstract km² number, jargon in
the copy, and the key facts (like a last bus at 7:46pm) never stated on the map.

- **Layout:** one figure per origin, a 2×2 grid (Tue 8am, Tue 8pm / Sat 5pm, Sun noon),
  the same extent in all four panels, sized to read on a phone.
- **Basemap:** *revised 2026-09-16.* Label-free map tiles (CartoDB Positron "no labels"
  via `contextily`, cached in `data/processed/tiles/`) supply streets, water and parks
  with no filtering work. On top go about 8 landmark labels we place ourselves (Downtown,
  UNL City & East Campus, Airport, Gateway, SouthPointe…), listed with coordinates in
  `config.yml`, plus the city-limits outline from our OSM extract. The tiles' own labels
  were what turned to mush at panel size; this style has none. Requires the credit line
  "© OpenStreetMap contributors © CARTO". (Replaces a fully custom basemap, which would
  have meant filtering 921 ponds and 484 parks.)
- **Band shapes:** smoothed, contour-like shapes instead of blocky 150 m (≈490 ft) staircases, with no
  white seams between bands. Keep a raw-cell "honest pixels" mode in `config.yml` for
  checking, and note the few hundred feet of edge precision lost in the methodology.
- **Color:** the one-hue blue ordinal ramp validated for the preview (darkest = reached
  soonest). Re-run the palette validator if any step changes.
- **Headline per panel:** square miles reachable within an hour, plus the change from
  Tuesday 8am (e.g. "10 sq mi · down 77%"), so the collapse reads without math.
- **Copy:** a plain-language title and subtitle; method details move to a small footer
  and the methodology. Readable dates ("Tue, Sept 15"). Bigger text.
- **Callouts:** *hand-written, 2026-09-16.* Short notes stating the key fact on a panel,
  written by hand in `config.yml` rather than derived by code. At most one per panel,
  one short line (about 30 characters), always drawn in the same spot on the panel.
  Sunday panels get "No buses run on Sundays" automatically, because Task 3 already marks
  those scenarios as having no service. Format, keyed by origin slug
  (`data/origins.csv`) and scenario id (`config.yml`):

  ```yaml
  callouts:
    bryan-east:
      tue_2000: "Last bus left 7:46pm"
  ```

  Every time or count in a callout must come from the feed, not memory. The render
  script should refuse a callout whose origin slug or scenario id doesn't exist.
- **Origin:** a larger, labeled marker. Bus routes are faint context only.

Export two versions of each figure:
- `output/panels/{origin}.png` at ~200 dpi — the archive and print copy, kept in this repo
- `output/web/{origin}.webp` at ~2400 px wide — what the site serves. Basemap imagery
  compresses poorly, so a 200 dpi four-panel PNG can run 2–3 MB; the WebP should land
  around 300–600 KB, which keeps the whole page near 2–3 MB on a phone.

`render_panels.py` is built from `src/preview_isochrones.py`, which already handles
loading, routes, the shared extent, the legend and labels, rather than from scratch.
Pieces both scripts need move into shared functions, and the contact sheet stays as the
checking view. Load the `dataviz` skill before any color or layout change.

**Task 6 — Methodology write-up (`methodology.md`).**
Everything in the Methodology section above, filled in with actual values.

**Task 7 — Publish to GitHub Pages.** *Simplified 2026-09-16.* The finished figures go
into the existing personal site repo (`loganserv44/loganserv44.github.io`) as a
self-contained subfolder, matching `mail-in-ballot-search/` and `profsearch/`.

- `where-the-bus-goes/index.html` is **written by hand, once**: title, the six figures,
  a short plain-language explanation, the map credit line, and a link to the methodology
  in this repo. No template system. The few facts it states (feed version, dates,
  walking speed) get updated by hand if the analysis is ever rerun.
- A short `src/publish.py` copies `output/web/*.webp` into `where-the-bus-goes/panels/`
  and prints what changed. It stages and commits nothing; both repos are reviewed and
  committed by hand.
- The GeoJSON is **not** copied. The page doesn't use it, and anything committed to the
  site repo stays in its history for good.

Publish when the maps are final, not after every tweak, since each republished image set
stays in the site's history permanently.

Final URL: `https://loganserv44.github.io/where-the-bus-goes/`

### Project structure

```
config.yml                 # scenarios, bands, grid size, walk limit/speed, bbox
data/
  raw/          gtfs.zip, nebraska-latest.osm.pbf, MANIFEST.json   (gitignored)
  processed/    lincoln.osm.pbf (+ .json sidecar), grid.gpkg, tiles/ (basemap tile cache)  (gitignored)
                # R5's built network is cached by r5py in %LOCALAPPDATA%\r5py, not here
  origins.csv
src/
  premise_check.py         # Task 0: verify no Sunday service
  fetch_data.py
  build_network.py
  verify_network.py        # Task 2: check the network against known trips
  compute_isochrones.py
  preview_isochrones.py    # contact sheet of every result, for checking
  units.py                 # Task 5: metric -> US conversions for everything readers see
  render_panels.py         # Task 5: final per-origin figures (not started)
  publish.py               # Task 7: copy the web images into the Pages site repo
  common.py                # config load, slugify, paths, scenario-date derivation
output/
  grids/*.parquet
  isochrones/*.geojson
  panels/*.png             # 200 dpi archive copies
  web/*.webp               # web-sized copies published to the site
methodology.md
README.md
environment.yml            # conda env incl. openjdk 25
```

Git: `git init`, push to GitHub. Commit `src/`, `config.yml`, `data/origins.csv`,
`methodology.md`, `output/panels/`, `output/grids/`, `output/isochrones/`. Gitignore
`data/raw/` and `data/processed/` (regenerable). Orchestration: `run_all.py` (or a
Makefile) chaining fetch → build → compute → render.

### Verification

1. **Premise gate** (done): `src/premise_check.py` prints service spans per day type and
   fails if any Sunday service exists. Not yet cross-checked against a published
   StarTran PDF schedule.
2. **Network sanity** (done): `src/verify_network.py` checks trips with known answers
   (walking wins downtown → City Campus; the bus wins downtown → East Campus) and a
   one-origin run over the whole grid.
3. **Grid spot-checks**: for one origin/scenario, hand-verify 3–4 cells against Google
   Maps transit directions for the same date/time (rough agreement expected).
4. **Scenario contrast**: Tue 8pm reachable area visibly smaller than Tue 8am; Sat 5pm
   smaller still; Sunday blank. If Tue 8pm ≈ Tue 8am, the window or calendar handling is
   wrong.
5. **Panel review**: all four panels share one extent; legend, marker, footer metadata
   present; Sunday panel blank with caption.
6. Full run for all approved origins; visual review of the contact sheet.

### User Experience

**A** is a UNL student without a car. They know their nearest stop but can't picture what
it actually gets them. They see the reachable area shaded by travel time, and how it
shrinks after dark.

**B** works a shift ending at 9:30pm. The Bryan Health East Campus origin, where the last
weekday bus leaves at 7:46pm, shows whether the bus can still
get them home.

### Where this lives

- **Pipeline repo** (this one): `github.com/loganserv44/WhereCanTheBusGetMe`, public.
  The public pipeline is part of the argument — the Methodology section promises "here is
  exactly how this was computed," and the repo is what backs that up.
- **Published page**: `loganserv44.github.io/where-the-bus-goes/`, deployed from the
  separate site repo via Task 7. The two repos stay separate deliberately: the pipeline
  pulls a ~96 MB OSM extract, a 64 MB R5 jar, and a network cache, none of which belongs
  in a repo whose job is serving static files.

### Still open

- ~~Evening scenario times vs. the 60-minute window~~ **Resolved 2026-09-15:** the
  evening scenarios moved to Tue 8pm and Sat 5pm, so every 60-minute window sits inside
  service and all four panels measure the same thing. The 9:30pm shift-worker story is
  told by the Bryan Health East Campus origin instead of by a near-empty panel.
- A more meaningful panel headline, such as the share of Lincoln residents or jobs
  reachable, would need census data (a new source). Deferred on 2026-09-15 in favor of
  sq mi plus change from Tuesday 8am.
- Before publishing: confirm CARTO's basemap terms allow static map images on a personal
  site with the credit line (not yet checked).
- License for the repo (MIT for the code vs. CC BY for the maps, or both).
- Whether the site's root `index.html` — currently a "Coming Soon" placeholder — should
  start linking out to the project pages. Out of scope here, but this project makes it
  three unlinked pages.

### Out of scope for v1

- Interactive web map (MapLibre + the exported GeoJSON) — additive later.
- POI / destination overlays.
- Real-time / observed performance (a GTFS-RT feed exists).
