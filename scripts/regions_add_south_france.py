"""
Add occitania to regions.esf as a new land region at index 205.

Changes:
  1. Append occitania to the regions T_RECORD_ARY (at index 205)
  2. Add occitania to Europe theatres_and_region_keys (region_keys)

occitania is the southern portion of France, capital: Toulouse.
World coordinates: ~(1.0, 311.0) for Toulouse.

Run from repo root:
    python scripts/regions_add_occitania.py
"""

import copy
import struct
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_ASCII, T_UNICODE, T_U1_ARY

INPUT_PATH  = Path("data/gc/regions.esf")
OUTPUT_PATH = Path("data/gc/regions_occitania.esf")

# Toulouse world coordinates (wx≈1.4°E → wx≈11.2, wy≈311 from latitude interpolation)
# Using a position clearly in southern France
TOULOUSE_WX = 1.4
TOULOUSE_WY = 311.0

# South France bounding box (covers rows 83-95, wy 306-330)
# wx = origin_x + col * cell_size: cols 83-113 → wx = -180 + 83*2 to -180 + 113*2 = -14 to 46
# But France only spans cols ~83-113, let's use France's southern sub-region
SF_BBOX_MIN_X = -34.0
SF_BBOX_MIN_Y = 300.0
SF_BBOX_MAX_X =  53.0
SF_BBOX_MAX_Y = 332.0

print(f"Loading {INPUT_PATH} ...")
reader    = ESFReader(INPUT_PATH)
root      = reader.read_root()
tag_names = reader.tag_names
timestamp = reader.timestamp

# ─── Find the regions array ──────────────────────────────────────────────────

def find_node_by_tag(node, tag, type_tag=None):
    if isinstance(node, ESFNode):
        if node.tag == tag and (type_tag is None or node.type_tag == type_tag):
            return node
        for c in node.children:
            r = find_node_by_tag(c, tag, type_tag)
            if r:
                return r
    return None

region_data = root.children[3]   # region_data T_RECORD
regions_ary = find_node_by_tag(region_data, 'regions')
assert regions_ary is not None, "regions array not found"
print(f"regions: {len(regions_ary.children)} items (france at 70, next new = 205)")
assert len(regions_ary.children) == 205, f"Expected 205, got {len(regions_ary.children)}"

# ─── Deep-copy France (index 70) and modify ──────────────────────────────────

france_item = regions_ary.children[70]

def deep_copy_esf(node):
    """Deep-copy an ESFNode or ESFPrimitive."""
    if isinstance(node, ESFPrimitive):
        raw = bytes(node.raw) if node.raw else b""
        p = ESFPrimitive(
            type_tag=node.type_tag,
            value=copy.deepcopy(node.value),
            raw=raw,
        )
        return p
    elif isinstance(node, ESFNode):
        n = ESFNode(tag=node.tag, type_tag=node.type_tag, version=node.version)
        n.children = [deep_copy_esf(c) for c in node.children]
        return n
    elif isinstance(node, list):
        return [deep_copy_esf(c) for c in node]
    else:
        return copy.deepcopy(node)


def get_str(prim: ESFPrimitive) -> str:
    """Return string value of an ASCII/Unicode primitive."""
    return str(prim.value)


def set_str(prim: ESFPrimitive, new_val: str) -> None:
    """Update string value (preserve type_tag, clear raw so writer re-encodes)."""
    prim.value = new_val
    prim.raw   = None


print("Deep-copying France record ...")
sf_item = deep_copy_esf(france_item)   # deep copy of list

# [0] name: 'france' → 'occitania'
set_str(sf_item[0], 'occitania')
print(f"  [0] name: 'france' → 'occitania'")

# [1] type: 'land' (keep as-is)
# [2] bbox min: use south France bounds
sf_item[2].value = (SF_BBOX_MIN_X, SF_BBOX_MIN_Y)
sf_item[2].raw   = None
print(f"  [2] bbox_min: {sf_item[2].value}")

# [3] bbox max: use south France bounds
sf_item[3].value = (SF_BBOX_MAX_X, SF_BBOX_MAX_Y)
sf_item[3].raw   = None
print(f"  [3] bbox_max: {sf_item[3].value}")

# [4] areas: keep as-is (copy of France — visual shape will be wrong but won't crash)
# (For a proper implementation, rasterize the occitania polygon and compute areas)
print(f"  [4] areas: copied from France ({len(sf_item[4].children)} items)")

# [5] Prim (int value=2): keep as-is
# [6] settlement_and_slots: modify capital position + slot keys
sas = sf_item[6]   # settlement_and_slots T_RECORD

# [6][0] capital position (T_V2)
sas.children[0].value = (TOULOUSE_WX, TOULOUSE_WY)
sas.children[0].raw   = None
print(f"  [6][0] capital pos: ({TOULOUSE_WX}, {TOULOUSE_WY})")

# [6][2] slot_descriptions T_RECORD_ARY
slot_descs = sas.children[2]
print(f"  [6][2] slot_descriptions: {len(slot_descs.children)} slots")

# Build occitania slots:
# Keep only the settlement slots (0-6: paris) and rename them → toulouse
# Plus a few southern resource slots
# For PoC: rename all 'france:paris' → 'occitania:toulouse' in settlement slots
#          and remove northern resource slots (keep southern ones)

SOUTH_FRANCE_SLOTS = {
    # original slot key → new key
    'settlement:france:paris:settlement_army-admin'  : 'settlement:occitania:toulouse:settlement_army-admin',
    'settlement:france:paris:settlement_government'  : 'settlement:occitania:toulouse:settlement_government',
    'settlement:france:paris:settlement_culture'     : 'settlement:occitania:toulouse:settlement_culture',
    'settlement:france:paris:settlement_navy-admin'  : 'settlement:occitania:toulouse:settlement_navy-admin',
    'settlement:france:paris:settlement_ordnance'    : 'settlement:occitania:toulouse:settlement_ordnance',
    'settlement:france:paris:settlement_road'        : 'settlement:occitania:toulouse:settlement_road',
    'settlement:france:paris:settlement_fortification': 'settlement:occitania:toulouse:settlement_fortification',
    # Southern resources
    'wine:france:bordeaux'         : 'wine:occitania:bordeaux',
    'iron:france:massifcentral'    : 'iron:occitania:massifcentral',
    'port:france:marseille'        : 'port:occitania:marseille',
    'town:france:toulouse'         : 'town:occitania:toulouse',
    'town:france:bordeaux'         : 'town:occitania:bordeaux',
    'town:france:lyons'            : 'town:occitania:lyons',
    'town:france:clermont-ferrand' : 'town:occitania:clermont-ferrand',
}

new_slot_items = []
for slot_item in slot_descs.children:
    # slot_item is a list; slot_item[0] = key (T_ASCII)
    key_prim = slot_item[0]
    orig_key = get_str(key_prim)
    if orig_key in SOUTH_FRANCE_SLOTS:
        new_key = SOUTH_FRANCE_SLOTS[orig_key]
        new_slot = deep_copy_esf(slot_item)
        set_str(new_slot[0], new_key)
        # Update slot type (index 1) if needed (types stay the same)
        # Update position to Toulouse area for settlement slots
        if 'settlement:occitania:toulouse' in new_key:
            # Move settlement position to Toulouse
            new_slot[2].value = (TOULOUSE_WX + 0.5, TOULOUSE_WY + 0.5)
            new_slot[2].raw   = None
            new_slot[3].value = (TOULOUSE_WX + 0.5, TOULOUSE_WY + 0.5)
            new_slot[3].raw   = None
        new_slot_items.append(new_slot)
        print(f"    slot: {orig_key!r} → {new_key!r}")

slot_descs.children = new_slot_items
print(f"  occitania has {len(new_slot_items)} slots")

# Also remove slots from France that we're moving to occitania
# (Keep France intact for the PoC — geographic inaccuracy is acceptable)
# We DON'T modify France's slots — both regions get some overlap for simplicity

# ─── Append occitania to regions array ─────────────────────────────────────

regions_ary.children.append(sf_item)
print(f"\nregions: {len(regions_ary.children)} items (occitania at 205)")

# ─── Add occitania to Europe region_keys ───────────────────────────────────

# Europe theatre is theatres_and_region_keys item[2]
tar = root.children[0]   # T_RECORD_ARY theatres_and_region_keys
europe_tar = tar.children[2][0]   # theatre T_RECORD
rk_europe  = europe_tar.children[4]  # region_keys T_RECORD_ARY

# Create new region_key item: [T_ASCII 'occitania', T_V2 (wx, wy)]
# Copy structure from an existing item
existing_rk = deep_copy_esf(rk_europe.children[1])   # copy france's rk item
set_str(existing_rk[0], 'occitania')
existing_rk[1].value = (TOULOUSE_WX, TOULOUSE_WY)
existing_rk[1].raw   = None

rk_europe.children.append(existing_rk)
print(f"region_keys: added 'occitania' ({TOULOUSE_WX}, {TOULOUSE_WY}), now {len(rk_europe.children)} items")

# ─── Write ────────────────────────────────────────────────────────────────────

print(f"\nWriting {OUTPUT_PATH} ...")
writer = ESFWriter(root, tag_names, timestamp)
OUTPUT_PATH.write_bytes(writer.to_bytes())
size = OUTPUT_PATH.stat().st_size
print(f"Done. Output: {size:,} bytes (original: {INPUT_PATH.stat().st_size:,} bytes)")

# ─── Sanity re-read ───────────────────────────────────────────────────────────

print("\nSanity re-read ...")
reader2    = ESFReader(OUTPUT_PATH)
root2      = reader2.read_root()
regions2   = find_node_by_tag(root2.children[3], 'regions')
assert len(regions2.children) == 206, f"Expected 206, got {len(regions2.children)}"
sf = regions2.children[205]
assert sf[0].value == 'occitania', f"Name mismatch: {sf[0].value!r}"
print(f"regions[205].name = {sf[0].value!r}  PASS")

# Check round-trip of the modified file
original = OUTPUT_PATH.read_bytes()
root3    = ESFReader(OUTPUT_PATH).read_root()
writer3  = ESFWriter(root3, reader2.tag_names, reader2.timestamp)
output3  = writer3.to_bytes()
if output3 == original:
    print(f"Round-trip PASS ({len(original):,} bytes)")
else:
    for i, (a, b) in enumerate(zip(original, output3)):
        if a != b:
            print(f"Round-trip FAIL at 0x{i:X}: orig=0x{a:02X} out=0x{b:02X}")
            break

print("\nAll done. Inspect regions_occitania.esf for in-game testing.")
