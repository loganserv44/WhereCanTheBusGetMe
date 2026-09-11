#!/usr/bin/env python3
"""Task 1 -- acquire the two datasets the whole pipeline is built on.

Downloads the StarTran GTFS feed and the Geofabrik Nebraska OSM extract into
data/raw/, and records provenance in data/raw/MANIFEST.json: source URL, SHA-256,
byte size, download timestamp, and the server's Last-Modified header.

That manifest is not bookkeeping for its own sake. The published methodology has to
state which feed version produced the maps, and a reader has to be able to tell
whether a rerun used the same inputs. Transit schedules change every few months; a
map with no recorded feed version cannot be checked or reproduced.

Both files are gitignored -- they are large and regenerable. The manifest is what
gets committed.

The script is idempotent. A file already present with a hash matching the manifest
is left alone, so rerunning costs nothing and the ~96 MB OSM extract is fetched
once. Where the publisher provides a checksum (Geofabrik does), the local file is
verified against it rather than only against our own record.

Usage:
    python src/fetch_data.py              # fetch anything missing
    python src/fetch_data.py --check      # report if the remote has changed, download nothing
    python src/fetch_data.py --force      # re-download everything
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import requests

RAW_DIR = Path("data/raw")
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
CHUNK = 1 << 20  # 1 MiB

SOURCES = [
    {
        "name": "gtfs.zip",
        "url": "https://startran.connexionz.net/rtt/public/resource/gtfs.zip",
        "description": "StarTran static GTFS feed (agency-hosted via Connexionz)",
        "md5_url": None,  # publisher provides no checksum
    },
    {
        "name": "nebraska-latest.osm.pbf",
        "url": "https://download.geofabrik.de/north-america/us/nebraska-latest.osm.pbf",
        "description": "Geofabrik Nebraska OSM extract (street network for walk legs)",
        "md5_url": "https://download.geofabrik.de/north-america/us/nebraska-latest.osm.pbf.md5",
    },
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def hash_file(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"generated_utc": None, "files": {}}


def save_manifest(manifest: dict) -> None:
    manifest["generated_utc"] = utc_now()
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def remote_head(url: str) -> dict:
    """Cheap metadata probe: size and Last-Modified without downloading the body."""
    try:
        r = requests.head(url, timeout=30, allow_redirects=True)
        r.raise_for_status()
        return {
            "bytes": int(r.headers["Content-Length"]) if "Content-Length" in r.headers else None,
            "last_modified": r.headers.get("Last-Modified"),
        }
    except requests.RequestException as exc:
        return {"error": str(exc)}


def download(url: str, dest: Path) -> dict:
    """Stream a URL to disk, reporting progress. Returns response metadata."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=(30, 300)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None
        last_modified = r.headers.get("Last-Modified")

        written = 0
        next_report = 0
        with tmp.open("wb") as fh:
            for block in r.iter_content(CHUNK):
                fh.write(block)
                written += len(block)
                # Report every ~10% for known sizes, else every 25 MiB.
                if total:
                    pct = written / total * 100
                    if pct >= next_report:
                        print(f"      {pct:5.1f}%  {human(written)} / {human(total)}")
                        next_report += 10
                elif written >= next_report:
                    print(f"      {human(written)}")
                    next_report += 25 * (1 << 20)

    # Only move into place once the download completed, so an interrupted run
    # never leaves a truncated file that looks valid.
    tmp.replace(dest)
    return {"bytes": written, "last_modified": last_modified}


def verify_publisher_md5(source: dict, path: Path) -> bool | None:
    """Check the file against the publisher's own .md5, if they publish one.

    Returns True/False, or None when no checksum is available. This catches a
    corrupted or truncated download that our own SHA-256 would happily record as
    correct, because our hash only proves the file matches itself.
    """
    if not source.get("md5_url"):
        return None
    try:
        r = requests.get(source["md5_url"], timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"      could not fetch publisher checksum: {exc}")
        return None
    expected = r.text.split()[0].strip().lower()
    actual = hash_file(path, "md5")
    ok = expected == actual
    print(f"      publisher md5 {'OK' if ok else 'MISMATCH'}  ({expected[:16]}...)")
    return ok


# --------------------------------------------------------------------------
# main flow
# --------------------------------------------------------------------------
def check_only() -> int:
    print("Comparing local files against the remote sources.\n")
    manifest = load_manifest()
    stale = 0
    for src in SOURCES:
        path = RAW_DIR / src["name"]
        entry = manifest["files"].get(src["name"], {})
        print(f"  {src['name']}")
        if not path.exists():
            print("      local:  MISSING")
            stale += 1
        else:
            print(f"      local:  {human(path.stat().st_size)}"
                  f"  last-modified recorded: {entry.get('remote_last_modified', '?')}")
        info = remote_head(src["url"])
        if "error" in info:
            print(f"      remote: unreachable ({info['error']})")
            continue
        print(f"      remote: {human(info['bytes']) if info['bytes'] else '?'}"
              f"  last-modified: {info.get('last_modified', '?')}")
        if path.exists() and info.get("last_modified") and \
                entry.get("remote_last_modified") and \
                info["last_modified"] != entry["remote_last_modified"]:
            print("      >>> remote has been updated since this file was downloaded")
            stale += 1
        print()
    if stale:
        print(f"{stale} source(s) missing or out of date. Run without --check to fetch.")
    else:
        print("Everything is current.")
    return 0


def fetch_all(force: bool) -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    failures = 0

    for src in SOURCES:
        name = src["name"]
        path = RAW_DIR / name
        entry = manifest["files"].get(name)
        print(f"=== {name} ===")
        print(f"    {src['description']}")

        recorded_hash = (entry or {}).get("sha256")
        if path.exists() and not force:
            actual = hash_file(path)
            if recorded_hash == actual:
                print(f"    present and matches manifest, skipping "
                      f"({human(path.stat().st_size)})")
                print(f"    sha256 {actual}\n")
                continue
            if recorded_hash is None:
                print("    present but not in the manifest -- recording it")
            else:
                print("    present but hash differs from manifest -- re-recording")
            manifest["files"][name] = {
                "url": src["url"],
                "sha256": actual,
                "bytes": path.stat().st_size,
                "downloaded_utc": (entry or {}).get("downloaded_utc"),
                "remote_last_modified": (entry or {}).get("remote_last_modified"),
                "note": "hash computed from existing local file, not a fresh download",
            }
            verify_publisher_md5(src, path)
            print(f"    sha256 {actual}\n")
            continue

        action = "re-downloading (--force)" if path.exists() else "downloading"
        print(f"    {action} from {src['url']}")
        try:
            meta = download(src["url"], path)
        except requests.RequestException as exc:
            print(f"    FAILED: {exc}\n")
            failures += 1
            continue

        digest = hash_file(path)
        if verify_publisher_md5(src, path) is False:
            print("    FAILED: publisher checksum mismatch -- download is corrupt\n")
            failures += 1
            continue

        manifest["files"][name] = {
            "url": src["url"],
            "sha256": digest,
            "bytes": meta["bytes"],
            "downloaded_utc": utc_now(),
            "remote_last_modified": meta["last_modified"],
        }
        print(f"    done: {human(meta['bytes'])}")
        print(f"    sha256 {digest}\n")

    save_manifest(manifest)
    print(f"Manifest written to {MANIFEST_PATH}")

    if failures:
        print(f"\n{failures} source(s) failed. Fix before proceeding to Task 2.")
        return 1
    print("\nAll sources present. Clear to proceed to Task 2 (build_network.py).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--force", action="store_true",
                   help="re-download everything, ignoring what is already present")
    g.add_argument("--check", action="store_true",
                   help="report whether the remotes have changed; download nothing")
    args = ap.parse_args()

    return check_only() if args.check else fetch_all(args.force)


if __name__ == "__main__":
    sys.exit(main())
