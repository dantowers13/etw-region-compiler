"""
Repurpose regions.esf slot [74] 'central_italy' as 'occitania'.

This avoids the 205-entry hard limit: instead of appending a 206th entry,
we rename an existing unused land slot that is:
  - NOT in startpos REGIONS_ARRAY
  - NOT referenced by pathfinding i2_ary (safe to repurpose content)
  - IS in CAI_WORLD_REGIONS[74] (CAI data naturally associates)

Changes:
  1. regions[74]: rename 'central_italy' -> 'occitania', update bbox + capital + slots
  2. theatres_and_region_keys: rename 'central_italy' rk entry -> 'occitania' (if found)

Run from repo root:
    python scripts/regions_repurpose_slot74.py
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
OUTPUT_PATH = Path("data/gc/regions_occitania_v2.esf")

SLOT_INDEX  = 74        # central_italy slot to repurpose
OLD_NAME    = "central_italy"
NEW_NAME    = "occitania"

# Toulouse world coordinates
TOULOUSE_WX = 1.4
TOULOUSE_WY = 311.0

# Southern France bounding box (covers occitania cells rows 83-95)
SF_BBOX_MIN_X = -34.0
SF_BBOX_MIN_Y = 300.0
SF_BBOX_MAX_X =  53.0
SF_BBOX_MAX_Y = 332.0

# Slot renames: these must match entries in campaign_map_settlements_tables /
# campaign_map_slots_tables (already added to occitania_db.pack).
SLOT_RENAMES = {
    # Settlement slots (from the paris settlement of france)
    "settlement:france:paris:settlement_army-admin"   : "settlement:occitania:toulouse:settlement_army-admin",
    "settlement:france:paris:settlement_government"   : "settlement:occitania:toulouse:settlement_government",
    "settlement:france:paris:settlement_culture"      : "settlement:occitania:toulouse:settlement_culture",
    "settlement:france:paris:settlement_navy-admin"   : "settlement:occitania:toulouse:settlement_navy-admin",
    "settlement:france:paris:settlement_ordnance"     : "settlement:occitania:toulouse:settlement_ordnance",
    "settlement:france:paris:settlement_road"         : "settlement:occitania:toulouse:settlement_road",
    "settlement:france:paris:settlement_fortification": "settlement:occitania:toulouse:settlement_fortification",
    # Resource slots
    "wine:france:bordeaux"          : "wine:occitania:bordeaux",
    "iron:france:massifcentral"     : "iron:occitania:massifcentral",
    "port:france:marseille"         : "port:occitania:marseille",
    "town:france:toulouse"          : "town:occitania:toulouse",
    "town:france:bordeaux"          : "town:occitania:bordeaux",
    "town:france:lyons"             : "town:occitania:lyons",
    "town:france:clermont-ferrand"  : "town:occitania:clermont-ferrand",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def find_node_by_tag(node, tag, type_tag=None):
    if isinstance(node, ESFNode):
        if node.tag == tag and (type_tag is None or node.type_tag == type_tag):
            return node
        for c in node.children:
            r = find_node_by_tag(c, tag, type_tag)
            if r:
                return r
    return None


def deep_copy_esf(node):
    if isinstance(node, ESFPrimitive):
        return ESFPrimitive(type_tag=node.type_tag, value=copy.deepcopy(node.value),
                            raw=bytes(node.raw) if node.raw else b"")
    elif isinstance(node, ESFNode):
        n = ESFNode(tag=node.tag, type_tag=node.type_tag, version=node.version)
        n.children = [deep_copy_esf(c) for c in node.children]
        return n
    elif isinstance(node, list):
        return [deep_copy_esf(c) for c in node]
    else:
        return copy.deepcopy(node)


def get_str(prim: ESFPrimitive) -> str:
    return str(prim.value)


def set_str(prim: ESFPrimitive, new_val: str) -> None:
    prim.value = new_val
    prim.raw   = None


# ─── Load ─────────────────────────────────────────────────────────────────────

print(f"Loading {INPUT_PATH} ...")
reader    = ESFReader(INPUT_PATH)
root      = reader.read_root()
tag_names = reader.tag_names
timestamp = reader.timestamp

region_data = root.children[3]
regions_ary = find_node_by_tag(region_data, "regions")
assert regions_ary is not None, "regions array not found"
assert len(regions_ary.children) == 205, f"Expected 205, got {len(regions_ary.children)}"

slot = regions_ary.children[SLOT_INDEX]
slot_name = get_str(slot[0])
print(f"Slot [{SLOT_INDEX}] name = {slot_name!r}  (type = {get_str(slot[1])!r})")
assert slot_name == OLD_NAME, f"Expected {OLD_NAME!r}, got {slot_name!r}"

# ─── 1. Rename the slot ────────────────────────────────────────────────────────

set_str(slot[0], NEW_NAME)
print(f"  [0] name: {OLD_NAME!r} -> {NEW_NAME!r}")

# [1] type = 'land' — keep as-is
# [2] bbox min
slot[2].value = (SF_BBOX_MIN_X, SF_BBOX_MIN_Y)
slot[2].raw   = None
print(f"  [2] bbox_min: {slot[2].value}")

# [3] bbox max
slot[3].value = (SF_BBOX_MAX_X, SF_BBOX_MAX_Y)
slot[3].raw   = None
print(f"  [3] bbox_max: {slot[3].value}")

# [4] areas: keep existing (wrong geometry, but won't crash — cosmetic)
print(f"  [4] areas: kept as-is ({len(slot[4].children)} items)")

# [5] int prim: -1 for unused regions; update to 2 (same as France) to indicate active region
slot[5].value = 2
slot[5].raw   = None
print(f"  [5] int: -1 -> 2 (mark as active region)")

# [6] settlement_and_slots T_RECORD: central_italy doesn't have one — copy from France[70]
france_item = regions_ary.children[70]
france_sas_orig = france_item[6]  # settlement_and_slots T_RECORD from France
sas = deep_copy_esf(france_sas_orig)
slot.append(sas)
print(f"  [6] settlement_and_slots: copied from France[70]")

# [6][0] capital position (T_V2)
sas.children[0].value = (TOULOUSE_WX, TOULOUSE_WY)
sas.children[0].raw   = None
print(f"  [6][0] capital pos: ({TOULOUSE_WX}, {TOULOUSE_WY})")

# [6][2] slot_descriptions T_RECORD_ARY
slot_descs = sas.children[2]
print(f"  [6][2] slot_descriptions: {len(slot_descs.children)} France slots (will filter)")

france_slots = slot_descs   # already a deep copy from France

new_slot_items = []
for slot_item in france_slots.children:
    key_prim = slot_item[0]
    orig_key = get_str(key_prim)
    if orig_key in SLOT_RENAMES:
        new_key  = SLOT_RENAMES[orig_key]
        # slot_item is already a deep copy (we deep-copied sas from France)
        new_slot = slot_item
        set_str(new_slot[0], new_key)
        if "settlement:occitania:toulouse" in new_key:
            new_slot[2].value = (TOULOUSE_WX + 0.5, TOULOUSE_WY + 0.5)
            new_slot[2].raw   = None
            new_slot[3].value = (TOULOUSE_WX + 0.5, TOULOUSE_WY + 0.5)
            new_slot[3].raw   = None
        new_slot_items.append(new_slot)
        print(f"    {orig_key!r} -> {new_key!r}")

slot_descs.children = new_slot_items
print(f"  occitania has {len(new_slot_items)} slots")

# ─── 2. Update theatres_and_region_keys ────────────────────────────────────────

tar = root.children[0]          # T_RECORD_ARY theatres_and_region_keys
europe_tar  = tar.children[2][0]  # europe theatre T_RECORD
rk_europe   = europe_tar.children[4]  # region_keys T_RECORD_ARY

renamed_rk = False
for rk_item in rk_europe.children:
    if get_str(rk_item[0]) == OLD_NAME:
        set_str(rk_item[0], NEW_NAME)
        rk_item[1].value = (TOULOUSE_WX, TOULOUSE_WY)
        rk_item[1].raw   = None
        print(f"theatres_and_region_keys: renamed {OLD_NAME!r} -> {NEW_NAME!r} at ({TOULOUSE_WX}, {TOULOUSE_WY})")
        renamed_rk = True
        break

if not renamed_rk:
    # Not found — add new entry from France's rk item
    print(f"  '{OLD_NAME}' not in region_keys; appending new '{NEW_NAME}' entry")
    france_rk = None
    for rk_item in rk_europe.children:
        if get_str(rk_item[0]) == "france":
            france_rk = rk_item
            break
    if france_rk:
        new_rk = deep_copy_esf(france_rk)
        set_str(new_rk[0], NEW_NAME)
        new_rk[1].value = (TOULOUSE_WX, TOULOUSE_WY)
        new_rk[1].raw   = None
        rk_europe.children.append(new_rk)
        print(f"  Appended '{NEW_NAME}' to region_keys ({len(rk_europe.children)} total)")

# ─── Write ─────────────────────────────────────────────────────────────────────

print(f"\nWriting {OUTPUT_PATH} ...")
writer = ESFWriter(root, tag_names, timestamp)
OUTPUT_PATH.write_bytes(writer.to_bytes())
size = OUTPUT_PATH.stat().st_size
orig_size = INPUT_PATH.stat().st_size
print(f"Done. {size:,} bytes (original: {orig_size:,} bytes)")

# ─── Sanity re-read ─────────────────────────────────────────────────────────────

print("\nSanity re-read ...")
reader2  = ESFReader(OUTPUT_PATH)
root2    = reader2.read_root()
regions2 = find_node_by_tag(root2.children[3], "regions")
assert len(regions2.children) == 205, f"Still 205 entries: PASS (got {len(regions2.children)})"
slot2    = regions2.children[SLOT_INDEX]
assert get_str(slot2[0]) == NEW_NAME, f"Name mismatch: {get_str(slot2[0])!r}"
print(f"regions[{SLOT_INDEX}].name = {get_str(slot2[0])!r}  PASS")

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

print("\nAll done. Deploy regions_occitania_v2.esf -> game regions.esf")
