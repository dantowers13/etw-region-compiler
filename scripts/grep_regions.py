#!/usr/bin/env python3
"""
Quick utility: list all region names and cell IDs from a dumped gc_pathfinding esf.xml.
Useful for Phase 0 exploration before the full ESFReader is implemented.

Usage:
    python scripts/grep_regions.py out/gc_pathfinding/esf.xml
    python scripts/grep_regions.py out/gc_pathfinding/esf.xml --search "pommerania"
    python scripts/grep_regions.py out/gc_pathfinding/esf.xml --list-regions --cell-ids
"""
import sys
import re
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="List regions from ESF XML dump")
    parser.add_argument("xml_file", type=Path)
    parser.add_argument("--search", default=None, help="Search for a specific term (case-insensitive)")
    parser.add_argument("--list-regions", action="store_true", help="List all region name entries")
    parser.add_argument("--cell-ids", action="store_true", help="Also show cell_id values found")
    args = parser.parse_args()

    if not args.xml_file.exists():
        print(f"ERROR: {args.xml_file} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {args.xml_file} ({args.xml_file.stat().st_size / 1024 / 1024:.1f} MB)...")

    region_names = []
    cell_ids = set()

    # Stream-parse — file may be very large
    with open(args.xml_file, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if 'name="' in line or "region" in line.lower():
                match = re.search(r'name="([^"]+)"', line)
                if match:
                    name = match.group(1)
                    if args.search is None or args.search.lower() in name.lower():
                        region_names.append((i + 1, name))

            if args.cell_ids and "cell_id" in line:
                for m in re.finditer(r'cell_id="?(\d+)"?', line):
                    cell_ids.add(int(m.group(1)))

    print(f"\nFound {len(region_names)} matching entries:")
    for lineno, name in region_names[:100]:
        print(f"  line {lineno:6d}: {name}")
    if len(region_names) > 100:
        print(f"  ... and {len(region_names) - 100} more")

    if args.cell_ids and cell_ids:
        sorted_ids = sorted(cell_ids)
        print(f"\nCell IDs found: {sorted_ids[:20]}")
        print(f"Total unique cell_ids: {len(cell_ids)}")
        print(f"Max cell_id: {max(cell_ids)}")


if __name__ == "__main__":
    main()
