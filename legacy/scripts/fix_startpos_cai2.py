"""
Patch startpos_occitania_v5.esf: fix remaining 137-indexed CAI arrays.

Two categories remain after fix_startpos_cai.py:

  1. CAI_BORDER_PATROL_ANALYSIS_AREA_SPECIFIC_PATROL_POINTS (1 array, 137 -> 138)
       Deep-copy France's item (index 97), update position to Toulouse fixed-point.
       Toulouse: wx=1.4, wy=311.0 -> fixed = int(coord * (1<<20))

  2. T_U4_ARY primitives with exactly 137 elements
       Found inside CAI faction-specific INTERFACE_MANAGER nodes.
       These are candidate object ID lists. Append 0 to each.

Run from repo root:
    python scripts/fix_startpos_cai2.py
"""

import copy, zlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_RECORD_ARY, T_U4_ARY

# ─── Config ───────────────────────────────────────────────────────────────────

INPUT_PATH  = Path("data/campaigns/main/startpos_occitania_v5.esf")
OUTPUT_PATH = Path("data/campaigns/main/startpos_occitania_v6.esf")

FRANCE_REGIONS_IDX    = 97   # France's index in REGIONS_ARRAY (and CAI arrays)

# Toulouse in fixed-point (int(coord * (1 << 20)))
TOULOUSE_WX_FIXED = int(1.4   * (1 << 20))   # 1467904
TOULOUSE_WY_FIXED = int(311.0 * (1 << 20))   # 326057856


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


def find_all_u4_ary_137(node, results=None):
    """Find all T_U4_ARY ESFPrimitive nodes with exactly 137 elements."""
    if results is None:
        results = []
    if isinstance(node, ESFPrimitive):
        if node.type_tag == T_U4_ARY and node.value is not None and len(node.value) == 137:
            results.append(node)
    elif isinstance(node, ESFNode):
        for c in node.children:
            find_all_u4_ary_137(c, results)
    elif isinstance(node, list):
        for c in node:
            find_all_u4_ary_137(c, results)
    return results


# ─── Load ─────────────────────────────────────────────────────────────────────

print(f"Loading {INPUT_PATH} ({INPUT_PATH.stat().st_size:,} bytes) ...")
reader    = ESFReader(INPUT_PATH)
root      = reader.read_root()
tag_names = reader.tag_names
timestamp = reader.timestamp

# ─── 1. CAI_BORDER_PATROL_ANALYSIS_AREA_SPECIFIC_PATROL_POINTS ────────────────
# There are multiple such nodes (one per faction/context). Target only those
# with exactly 137 items (= vanilla REGIONS_ARRAY count, i.e. region-indexed).

all_bpaasp = [
    n for n in find_all_nodes(root, "CAI_BORDER_PATROL_ANALYSIS_AREA_SPECIFIC_PATROL_POINTS")
    if n.type_tag == T_RECORD_ARY and len(n.children) == 137
]
print(f"\nCAI_BORDER_PATROL_ANALYSIS_AREA_SPECIFIC_PATROL_POINTS (137 items): {len(all_bpaasp)} found")

for bpaasp in all_bpaasp:
    france_patrol = bpaasp.children[FRANCE_REGIONS_IDX]
    new_patrol = deep_copy_esf(france_patrol)

    # Each item is a T_RECORD containing a CAI_BORDER_PATROL_POINT node with:
    #   children[0] = T_I4 wx_fixed
    #   children[1] = T_I4 wy_fixed
    #   children[2] = T_U4_ARY adj_ids
    patrol_point = find_node(new_patrol, "CAI_BORDER_PATROL_POINT")
    if patrol_point is None and isinstance(new_patrol, ESFNode) and new_patrol.tag == "CAI_BORDER_PATROL_POINT":
        patrol_point = new_patrol

    if patrol_point is not None:
        old_wx = patrol_point.children[0].value
        old_wy = patrol_point.children[1].value
        patrol_point.children[0].value = TOULOUSE_WX_FIXED
        patrol_point.children[0].raw   = None
        patrol_point.children[1].value = TOULOUSE_WY_FIXED
        patrol_point.children[1].raw   = None
        print(f"  patrol pos: ({old_wx}, {old_wy}) -> ({TOULOUSE_WX_FIXED}, {TOULOUSE_WY_FIXED})")
        print(f"  adj_ids kept: {list(patrol_point.children[2].value)}")
    else:
        print("  WARNING: CAI_BORDER_PATROL_POINT not found — appending raw copy")

    bpaasp.children.append(new_patrol)
    print(f"  -> now {len(bpaasp.children)} items")

# ─── 2. T_U4_ARY primitives with 137 elements ─────────────────────────────────

u4_ary_137 = find_all_u4_ary_137(root)
print(f"\nT_U4_ARY primitives with 137 elements: {len(u4_ary_137)} found")
for i, prim in enumerate(u4_ary_137):
    old_len = len(prim.value)
    prim.value = list(prim.value) + [0]
    prim.raw   = None
    print(f"  [{i}]: {old_len} -> {len(prim.value)} elements (appended 0)")

# ─── Write ────────────────────────────────────────────────────────────────────

print(f"\nWriting {OUTPUT_PATH} ...")
writer = ESFWriter(root, tag_names, timestamp)
OUTPUT_PATH.write_bytes(writer.to_bytes())
size = OUTPUT_PATH.stat().st_size
print(f"Done. {size:,} bytes (input: {INPUT_PATH.stat().st_size:,} bytes)")

# ─── Sanity re-read ───────────────────────────────────────────────────────────

print("\nSanity re-read ...")
reader2 = ESFReader(OUTPUT_PATH)
root2   = reader2.read_root()

all_bpaasp2 = [
    n for n in find_all_nodes(root2, "CAI_BORDER_PATROL_ANALYSIS_AREA_SPECIFIC_PATROL_POINTS")
    if n.type_tag == T_RECORD_ARY and len(n.children) == 138
]
print(f"CAI_BORDER_PATROL_ANALYSIS_AREA_SPECIFIC_PATROL_POINTS (138 items): {len(all_bpaasp2)}  {'PASS' if len(all_bpaasp2) == len(all_bpaasp) else 'FAIL'}")

u4_ary_remaining = find_all_u4_ary_137(root2)
print(f"T_U4_ARY with 137 elements remaining: {len(u4_ary_remaining)} (expect 0)  {'PASS' if len(u4_ary_remaining) == 0 else 'FAIL'}")

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
