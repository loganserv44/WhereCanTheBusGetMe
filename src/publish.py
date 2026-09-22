#!/usr/bin/env python3
"""Task 7 -- copy the finished page and its images into the GitHub Pages site repo.

This repo holds the pipeline; a separate repo (loganserv44.github.io) serves the site.
They are kept apart because the pipeline pulls a ~96 MB OSM extract, a 64 MB R5 jar and
a network cache, none of which belong in a repo that serves static files.

What gets copied into <site>/where-the-bus-goes/:

    output/web/explorer/index.html      the hourly explorer
    output/web/explorer/compare.html    the six 2x2 figures, side by side
    output/web/explorer/style.css       shared by both pages
    output/web/explorer/common.js       the legend and footer, shared by both pages
    output/web/explorer/data.js|json    what the pages read
    output/web/explorer/*.webp          one map per origin x hour
    output/web/*.webp                   the six 2x2 comparison figures

Nothing is generated here. The page is hand-written and tracked in this repo, so what
the site serves is exactly what was reviewed, not a template filled in at publish time.

The checks below matter more than the copying. A page whose stated feed version does not
match the images beside it is worse than no page at all, and the failure is silent: the
maps still render, they are just from a different run than the text claims. So this
refuses to publish when the page and the run disagree, when an image the page asks for
is missing, or when the renders are older than the routing results they came from.

Usage:
    python src/publish.py                 # check, then copy
    python src/publish.py --check         # check only, copy nothing
    python src/publish.py --site <path>   # a site repo somewhere else
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

WEB = Path("output/web")
EXPLORER = WEB / "explorer"
MANIFEST = Path("output/run_manifest.json")
DEFAULT_SITE = Path.home() / "Projects" / "loganserv44.github.io"
SUBDIR = "where-the-bus-goes"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"


def check_feed(page_feed: str | None, manifest: dict) -> list[str]:
    """Confirm the feed version the page prints is the feed the maps were routed from."""
    run_sha = (manifest.get("inputs", {}).get("gtfs") or {}).get("sha256")
    if not run_sha:
        return ["output/run_manifest.json records no GTFS sha256; cannot verify the feed"]

    raw_manifest = Path("data/raw/MANIFEST.json")
    if raw_manifest.exists():
        raw = json.loads(raw_manifest.read_text(encoding="utf-8"))
        disk_sha = (raw.get("files", {}).get("gtfs.zip") or {}).get("sha256")
        if disk_sha and disk_sha != run_sha:
            return [f"the maps were routed from GTFS {run_sha[:12]}..., but the feed now "
                    f"on disk is {disk_sha[:12]}... -- rerun the pipeline, or publish the "
                    f"maps that match the feed"]

    gtfs = Path("data/raw/gtfs.zip")
    if not gtfs.exists():
        # The zip is gitignored, so a fresh clone will not have it. Say so rather than
        # passing silently, which is what this check exists to prevent.
        print(f"  note: {gtfs} not present, so the page's feed version "
              f"({page_feed}) could not be read back from the feed itself")
        return []

    import csv
    import io
    import zipfile

    with zipfile.ZipFile(gtfs) as zf:
        if "feed_info.txt" not in zf.namelist():
            return ["the feed has no feed_info.txt, so its version cannot be checked"]
        with zf.open("feed_info.txt") as fh:
            rows = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))
    actual = rows[0].get("feed_version") if rows else None
    if actual != page_feed:
        return [f"the page says feed {page_feed}, but data/raw/gtfs.zip calls itself "
                f"{actual}"]
    print(f"  feed {actual} confirmed against data/raw/gtfs.zip")
    return []


def collect() -> tuple[list[Path], list[str]]:
    """Every file to publish, plus any reason not to.

    Returns (files, problems). A non-empty problems list means do not copy.
    """
    problems: list[str] = []

    data_json = EXPLORER / "data.json"
    pages = [EXPLORER / "index.html", EXPLORER / "compare.html"]
    assets = [EXPLORER / "style.css", EXPLORER / "common.js",
              EXPLORER / "data.js", data_json]
    for required in (*pages, *assets, MANIFEST):
        if not required.exists():
            problems.append(f"missing {required}")
    if problems:
        return [], problems

    data = json.loads(data_json.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # Both pages are searched for image references, so a figure that only compare.html
    # shows still counts as referenced.
    html = "\n".join(p.read_text(encoding="utf-8") for p in pages)

    # The page prints a feed version in its footer, from data.js. Checking it is the
    # whole reason this script exists, because the failure is silent: a page can state
    # one feed version while showing maps routed from another, and nothing looks wrong.
    #
    # run_manifest.json identifies the feed by SHA-256 rather than by version string, so
    # the check runs in two steps: the run used the feed that is on disk, and that feed
    # calls itself what the page says it does.
    problems += check_feed(data.get("feed_version"), manifest)

    files = [*pages, *assets]

    # Every hourly map the page can ask for.
    for panel in data["panels"]:
        img = EXPLORER / panel["image"]
        if not img.exists():
            problems.append(f"data.json references a missing image: {panel['image']}")
        else:
            files.append(img)

    # The six comparison figures, which the page references by name in its HTML rather
    # than through data.json -- so they are checked against the HTML itself.
    for fig in sorted(WEB.glob("*.webp")):
        if f'src="{fig.name}"' not in html:
            problems.append(f"{fig.name} is not shown by either page")
        files.append(fig)

    for name in sorted({f'src="{n}"' for n in html.split('src="')[1:]}):
        ref = name[len('src="'):].split('"')[0]
        if ref.endswith(".webp") and not (EXPLORER / ref).exists() and not (WEB / ref).exists():
            problems.append(f"the page references a missing image: {ref}")

    # Renders older than the routing results mean the maps on disk are not from the run
    # the manifest describes.
    newest_result = MANIFEST.stat().st_mtime
    stale = [f.name for f in files if f.suffix == ".webp" and f.stat().st_mtime < newest_result]
    if stale:
        problems.append(f"{len(stale)} image(s) older than output/run_manifest.json "
                        f"(e.g. {stale[0]}) -- rerun render_panels.py")

    return files, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", type=Path, default=DEFAULT_SITE,
                    help=f"path to the Pages site repo (default: {DEFAULT_SITE})")
    ap.add_argument("--check", action="store_true",
                    help="run the checks and report; copy nothing")
    args = ap.parse_args()

    print("Checking what would be published...\n")
    files, problems = collect()

    if problems:
        print("REFUSING TO PUBLISH:")
        for p in problems:
            print(f"  - {p}")
        return 1

    total = sum(f.stat().st_size for f in files)
    images = [f for f in files if f.suffix == ".webp"]
    print(f"  {len(files)} files, {human(total)}")
    print(f"  {len(images)} maps + {len(files) - len(images)} page/asset files")

    if args.check:
        print("\n--check: nothing copied.")
        return 0

    dest_root = args.site / SUBDIR
    if not args.site.exists():
        print(f"\nERROR: no site repo at {args.site}")
        print("Clone it first, or pass --site.")
        return 1

    dest_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in files:
        dest = dest_root / f.name
        # Copy only what changed, so `git status` in the site repo shows the real diff
        # rather than every file restamped.
        if dest.exists() and dest.stat().st_size == f.stat().st_size and \
                dest.read_bytes() == f.read_bytes():
            continue
        shutil.copy2(f, dest)
        copied += 1

    print(f"\n  copied {copied} changed file(s) into {dest_root}")
    if copied == 0:
        print("  (the site was already up to date)")

    print(f"\nNext, in {args.site}:")
    print("  git add -A")
    print('  git commit -m "Publish Where the Bus Goes"')
    print("  git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
