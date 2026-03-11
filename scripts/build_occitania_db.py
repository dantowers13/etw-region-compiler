"""
Build occitania_db.pack with ONLY the new occitania delta records.

Root cause of previous crash: the pack contained full vanilla tables (142/310/424/159
records) which the engine merged with main.pack, doubling every vanilla entry.
This script produces a minimal pack (1/2/5/1 records) matching Lord mod's URR pattern.

Table formats (all confirmed from vanilla + Lord URR inspection):
  regions_tables:              str(name) str(continent) u32(R) u32(G) u32(B)
  campaign_map_settlements:    str(key) str(region) str(display) u32(tier)
  campaign_map_slots:          str(key) str(region) str(slot_type) str("") str("")
  campaign_map_towns_and_ports: str(key) str(slot_type) str(display)  [repeating, no u32]
"""

from __future__ import annotations
import struct
from pathlib import Path

# ─── helpers ──────────────────────────────────────────────────────────────────

def ws(s: str) -> bytes:
    enc = s.encode("utf-16-le")
    return struct.pack("<H", len(s)) + enc

def wu(v: int) -> bytes:
    return struct.pack("<I", v)

def make_db(records_bytes: list[bytes]) -> bytes:
    out = bytearray()
    out += b'\x01'
    out += struct.pack("<I", len(records_bytes))
    for r in records_bytes:
        out += r
    return bytes(out)

def build_pack(output_path: Path, files: list[tuple[str, bytes]]) -> None:
    index = b"".join(
        struct.pack("<I", len(data)) + path.encode("ascii") + b"\x00"
        for path, data in files
    )
    header = struct.pack("<4sIIIII", b"PFH0", 3, 0, 0, len(files), len(index))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        f.write(header)
        f.write(index)
        for _, data in files:
            f.write(data)
    print(f"Pack written: {output_path}  ({output_path.stat().st_size:,} bytes, {len(files)} files)")

# ─── occitania delta records ───────────────────────────────────────────────────

# regions_tables: 1 record
# RGB (220,140,60) must match TGA palette slot 254 and europe_lookup.tga repaint
regions_rec = ws("occitania") + ws("cont_europe") + wu(220) + wu(140) + wu(60)

# campaign_map_settlements: 1 record (settlement location, no slot-type suffix)
# tier=5 matches Paris-level major settlements; Toulouse hosts 7 major building slots
sett_rec = ws("settlement:occitania:toulouse") + ws("occitania") + ws("Toulouse") + wu(5)

# campaign_map_slots: 2 resource slots  (town/port types go in towns_and_ports only)
# Format: key + region + slot_type + "" + ""
def slot_rec(key: str, region: str, slot_type: str) -> bytes:
    return ws(key) + ws(region) + ws(slot_type) + ws("") + ws("")

slots_recs = [
    slot_rec("wine:occitania:bordeaux",       "occitania", "wine"),
    slot_rec("iron:occitania:massifcentral",   "occitania", "iron"),
]

# campaign_map_towns_and_ports: 5 records (key + slot_type + display_name)
# "town-textile" / "town-metal" / "town" / "port" are all valid slot_type values
def tap_rec(key: str, slot_type: str, display: str) -> bytes:
    return ws(key) + ws(slot_type) + ws(display)

tap_recs = [
    tap_rec("town:occitania:toulouse",        "town",  "Toulouse"),
    tap_rec("town:occitania:bordeaux",        "town",  "Bordeaux"),
    tap_rec("port:occitania:marseille",       "port",  "Marseille"),
    tap_rec("town:occitania:clermont-ferrand","town",  "Clermont-Ferrand"),
    tap_rec("town:occitania:lyons",           "town",  "Lyons"),
]

# ─── assemble ─────────────────────────────────────────────────────────────────

regions_db  = make_db([regions_rec])
sett_db     = make_db([sett_rec])
slots_db    = make_db(slots_recs)
tap_db      = make_db(tap_recs)

print("DB record counts:")
print(f"  regions_tables:              1 record  ({len(regions_db)} bytes)")
print(f"  campaign_map_settlements:    1 record  ({len(sett_db)} bytes)")
print(f"  campaign_map_slots:          2 records ({len(slots_db)} bytes)")
print(f"  campaign_map_towns_and_ports: 5 records ({len(tap_db)} bytes)")

OUTPUT = Path("out/packs/occitania_db_v2.pack")
build_pack(OUTPUT, [
    (r"db\regions_tables\occitania_regions",                           regions_db),
    (r"db\campaign_map_settlements_tables\occitania_settlements",      sett_db),
    (r"db\campaign_map_slots_tables\occitania_slots",                  slots_db),
    (r"db\campaign_map_towns_and_ports_tables\occitania_towns",        tap_db),
])
