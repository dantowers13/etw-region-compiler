"""
Build an ETW mod .pack file from a list of (pack_path, local_file) pairs.

Pack format: PFH0 (ETW / NTW old format)
  Header (24 bytes):
    uint32 magic        "PFH0"
    uint32 pack_type    3 = mod
    uint32 deps_count   0
    uint32 deps_size    0
    uint32 files_count
    uint32 index_size   (sum of 4 + len(path) + 1 for each file)
  File index (variable):
    for each file:
      uint32 file_size
      NUL-terminated ASCII path
  File data (variable):
    raw bytes of each file, concatenated

Usage:
    python scripts/build_pack.py

Edit the FILES and OUTPUT sections at the bottom for each mod build.
"""

from __future__ import annotations

import struct
from pathlib import Path


def build_pack(output_path: Path, files: list[tuple[str, Path]]) -> None:
    """Create a PFH0 mod pack at output_path.

    Args:
        output_path: destination .pack file path.
        files: list of (pack_internal_path, local_file_path) tuples.
               pack_internal_path uses forward or back slashes (ETW accepts either).
    """
    entries: list[tuple[str, bytes]] = []
    for pack_path, local_path in files:
        data = local_path.read_bytes()
        entries.append((pack_path, data))
        print(f"  [{len(data):>12,} B]  {pack_path}")

    # Build file index
    index_parts: list[bytes] = []
    for pack_path, data in entries:
        path_bytes = pack_path.encode("ascii") + b"\x00"
        index_parts.append(struct.pack("<I", len(data)) + path_bytes)

    index_bytes  = b"".join(index_parts)
    files_count  = len(entries)
    index_size   = len(index_bytes)

    header = struct.pack(
        "<4sIIIII",
        b"PFH0",
        3,             # mod pack type
        0,             # deps count
        0,             # deps size
        files_count,
        index_size,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        f.write(header)
        f.write(index_bytes)
        for _, data in entries:
            f.write(data)

    total = output_path.stat().st_size
    print(f"\nPack written: {output_path}")
    print(f"  {files_count} files, {total:,} bytes total")


# ─── CONFIG ───────────────────────────────────────────────────────────────────

OUTPUT_PACK = Path("out/packs/occitania_poc.pack")

# (internal pack path, local file path)
FILES = [
    (
        "campaign_maps/global_map/pathfinding.esf",
        Path("data/gc/pathfinding_france_split.esf"),
    ),
    (
        "campaign_maps/global_map/regions.esf",
        Path("data/gc/regions_occitania.esf"),
    ),
    (
        "campaign_maps/global_map/europe_lookup.tga",
        Path("data/gc/europe_lookup_occitania.tga"),
    ),
    (
        "db/regions_tables/regions",
        Path("data/gc/regions_tables_occitania.bin"),
    ),
    (
        "campaigns/main/startpos.esf",
        Path("data/campaigns/main/startpos_occitania.esf"),
    ),
]


if __name__ == "__main__":
    print(f"Building {OUTPUT_PACK} ...")
    build_pack(OUTPUT_PACK, FILES)
