"""
Patch startpos.esf: add CAI data for the new occitania region.

When a region is added to REGIONS_ARRAY, two sets of AI arrays must also
be extended — they are indexed by REGIONS_ARRAY position, not regions.esf:

  1. CAI_WORLD_SETTLEMENTS (1 array, 137 -> 138 items)
       Contains per-settlement AI state. Deep-copy France's item, update
       the path_id field to occitania's value (85 instead of 33).

  2. CAI_BDI_COMPONENT_BLOCK_OWNS (16 arrays, 137 -> 138 items each)
       Per-faction "block owns" BDI data for each region slot. Append a
       copy of the previous last item with a fresh unique ID.

Missing either causes an out-of-bounds read at the France campaign
loading bar.

Run from repo root:
    python scripts/fix_startpos_cai.py
"""

import copy, zlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_RECORD_ARY, T_U4_ARY

# ─── Config ───────────────────────────────────────────────────────────────────

INPUT_PATH  = Path("data/campaigns/main/startpos_occitania_v4.esf")
OUTPUT_PATH = Path("data/campaigns/main/startpos_occitania_v5.esf")

FRANCE_REGIONS_IDX   = 97    # France's index in REGIONS_ARRAY (and CAI arrays)
OCCITANIA_REGIONS_IDX = 137  # New occitania index (appended at end)

FRANCE_PATH_ID  = 33   # France's T_U2 pathfinding path_id
OCCITANIA_PATH_ID = 85  # Occitania's T_U2 pathfinding path_id


def new_id(seed: str) -> int:
    """Deterministic uint32 in 0x30000000 range — well above existing sequential IDs."""
    return 0x30000000 | (zlib.crc32(seed.encode()) & 0x0FFFFFFF)


# ─── ESF helpers ──────────────────────────────────────────────────────────────

def deep_copy_esf(node):
    if isinstance(node, ESFPrimitive):
        return ESFPrimitive(type_tag=node.type_tag,
                            value=copy.deepcopy(node.value),
                            raw=bytes(node.raw) if node.raw else b"")
    if isinstance(node, ESFNode):
        n = ESFNode(tag=node.tag, type_tag=node.type_tag, version=node.version)
        n.children = [deep_copy_esf(c) for c in node.children]
        return n
    if isinstance(node, list):
        return [deep_copy_esf(c) for c in node]
    return copy.deepcopy(node)


def find_node(node, tag):
    if isinstance(node, ESFNode):
        if node.tag == tag:
            return node
        for c in node.children:
            r = find_node(c, tag)
            if r:
                return r
    elif isinstance(node, list):
        for c in node:
            r = find_node(c, tag)
            if r:
                return r
    return None


def find_all_nodes(node, tag, results=None):
    if results is None:
        results = []
    if isinstance(node, ESFNode):
        if node.tag == tag:
            results.append(node)
        for c in node.children:
            find_all_nodes(c, tag, results)
    elif isinstance(node, list):
        for c in node:
            find_all_nodes(c, tag, results)
    return results


# ─── Load ─────────────────────────────────────────────────────────────────────

print(f"Loading {INPUT_PATH} ({INPUT_PATH.stat().st_size:,} bytes) ...")
reader    = ESFReader(INPUT_PATH)
root      = reader.read_root()
tag_names = reader.tag_names
timestamp = reader.timestamp

# ─── 1. CAI_WORLD_SETTLEMENTS ─────────────────────────────────────────────────

cws = find_node(root, "CAI_WORLD_SETTLEMENTS")
assert cws is not None, "CAI_WORLD_SETTLEMENTS not found"
assert len(cws.children) == 137, f"Expected 137, got {len(cws.children)}"
print(f"CAI_WORLD_SETTLEMENTS: {len(cws.children)} items")

france_cws_item = cws.children[FRANCE_REGIONS_IDX]
new_cws_item = deep_copy_esf(france_cws_item)

# Update OWNED_DIRECT[0] — unique object ID for this settlement node
owned_direct = new_cws_item[0]
old_od_id = owned_direct.children[0].value
owned_direct.children[0].value = new_id("cai_owned_direct:occitania")
owned_direct.children[0].raw   = None
print(f"  OWNED_DIRECT[0]: {old_od_id} -> {owned_direct.children[0].value}")

# Update CAI_SITUATED[3] — path_id list; change [33] -> [85]
cai_situated = new_cws_item[1]
old_pids = list(cai_situated.children[3].value)
new_pids = [OCCITANIA_PATH_ID if p == FRANCE_PATH_ID else p for p in old_pids]
cai_situated.children[3].value = new_pids
cai_situated.children[3].raw   = None
print(f"  CAI_SITUATED path_ids: {old_pids} -> {new_pids}")

# Update [3] — unique settlement node ID (sequential integers in vanilla; use CRC32)
old_node_id = new_cws_item[3].value
new_cws_item[3].value = new_id("cai_settlement_node:occitania")
new_cws_item[3].raw   = None
print(f"  Settlement node ID: {old_node_id} -> {new_cws_item[3].value}")

# Clear France-specific building slot reference arrays (items 10,11,14,15,22)
# These hold IDs of Paris's garrison slots — not valid for a new empty settlement.
for arr_idx in (10, 11, 14, 15, 22):
    prim = new_cws_item[arr_idx]
    if isinstance(prim, ESFPrimitive) and prim.type_tag == T_U4_ARY:
        old_len = len(prim.value) if prim.value else 0
        prim.value = []
        prim.raw   = None
        print(f"  Cleared array[{arr_idx}] ({old_len} entries -> 0)")

# item[13] = building count; reset to 0
new_cws_item[13].value = 0
new_cws_item[13].raw   = None

# CAI_SETTLEMENT[0] = slot ID list; reset to empty
cai_settlement = new_cws_item[28]
old_slots = list(cai_settlement.children[0].value)
cai_settlement.children[0].value = []
cai_settlement.children[0].raw   = None
print(f"  CAI_SETTLEMENT slot IDs cleared ({len(old_slots)} -> 0)")

# CAI_SETTLEMENT[2] — unique node ID
old_cs_id = cai_settlement.children[2].value
cai_settlement.children[2].value = new_id("cai_settlement_obj:occitania")
cai_settlement.children[2].raw   = None
print(f"  CAI_SETTLEMENT node ID: {old_cs_id} -> {cai_settlement.children[2].value}")

cws.children.append(new_cws_item)
print(f"CAI_WORLD_SETTLEMENTS: {len(cws.children)} items (occitania appended)")

# ─── 2. CAI_BDI_COMPONENT_BLOCK_OWNS (top-level, 137 items each) ──────────────

# Find all CAI_BDI_COMPONENT_BLOCK_OWNS T_RECORD_ARY nodes with exactly 137 children.
# (The ones embedded inside CAI_WORLD_SETTLEMENTS items have 0 children — skip those.)
all_block_owns = [
    n for n in find_all_nodes(root, "CAI_BDI_COMPONENT_BLOCK_OWNS")
    if n.type_tag == T_RECORD_ARY and len(n.children) == 137
]
print(f"\nCAI_BDI_COMPONENT_BLOCK_OWNS arrays to patch: {len(all_block_owns)}")

for i, bo in enumerate(all_block_owns):
    # Each item: [T_U4(id), T_U4(6), T_F4(0.0), T_F4(0.0), T_U4(1)]
    # Copy the last item and assign a fresh unique ID.
    template = deep_copy_esf(bo.children[-1])
    old_id = template[0].value
    template[0].value = new_id(f"cai_blockown:occitania:{i}")
    template[0].raw   = None
    bo.children.append(template)
    print(f"  array[{i}]: appended item (id {old_id} -> {template[0].value}), now {len(bo.children)}")

# ─── Write ────────────────────────────────────────────────────────────────────

print(f"\nWriting {OUTPUT_PATH} ...")
writer = ESFWriter(root, tag_names, timestamp)
OUTPUT_PATH.write_bytes(writer.to_bytes())
size = OUTPUT_PATH.stat().st_size
print(f"Done. {size:,} bytes (input: {INPUT_PATH.stat().st_size:,} bytes)")

# Sanity re-read
print("\nSanity re-read ...")
reader2 = ESFReader(OUTPUT_PATH)
root2   = reader2.read_root()
cws2    = find_node(root2, "CAI_WORLD_SETTLEMENTS")
assert len(cws2.children) == 138, f"Expected 138, got {len(cws2.children)}"
print(f"CAI_WORLD_SETTLEMENTS: {len(cws2.children)} items  PASS")
bos2 = [n for n in find_all_nodes(root2, "CAI_BDI_COMPONENT_BLOCK_OWNS")
        if n.type_tag == T_RECORD_ARY and len(n.children) == 138]
print(f"CAI_BDI_COMPONENT_BLOCK_OWNS (138 items): {len(bos2)} arrays  PASS")

# Round-trip
original = OUTPUT_PATH.read_bytes()
writer3  = ESFWriter(ESFReader(OUTPUT_PATH).read_root(), reader2.tag_names, reader2.timestamp)
if writer3.to_bytes() == original:
    print(f"Round-trip PASS ({len(original):,} bytes)")
else:
    for idx, (a, b) in enumerate(zip(original, writer3.to_bytes())):
        if a != b:
            print(f"Round-trip FAIL at 0x{idx:X}: 0x{a:02X} vs 0x{b:02X}")
            break

print(f"\nDeploy: copy {OUTPUT_PATH} -> game startpos.esf")
