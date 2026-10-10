"""
Patch startpos_occitania_v7.esf -> v8.esf

Fixes two wrong IDs in the occitania CAI_WORLD_SETTLEMENTS item that
were set to CRC32 values instead of proper engine-expected values:

  1. OWNED_DIRECT[0]: the CAI type code.
       Vanilla has 42 distinct values, range 1265-1430, multiples of 3.
       These are NOT unique per region — they are type/category codes.
       The fix script incorrectly set this to a CRC32 (~970M), which would
       crash if the engine uses it as an index into a type table.
       Fix: use France's value (1274) since occitania is copied from France.

  2. CAI_SETTLEMENT[2]: the settlement object node ID.
       Vanilla values are sequential-ish, range ~585M-587M (median gap 12288).
       The fix script set it to a CRC32 (~974M), outside the vanilla range.
       Fix: max_vanilla + median_gap = 587440304.

Run from repo root:
    python scripts/fix_startpos_cai_v8.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import ESFNode, ESFPrimitive

INPUT_PATH  = Path("data/campaigns/main/startpos_occitania_v7.esf")
OUTPUT_PATH = Path("data/campaigns/main/startpos_occitania_v8.esf")

FRANCE_OD_TYPE_CODE  = 1274        # France's OWNED_DIRECT[0] — type code, not instance ID
NEXT_CS_ID           = 587440304   # max_vanilla(587428016) + median_gap(12288)

def find_node(node, tag):
    if isinstance(node, ESFNode):
        if node.tag == tag: return node
        for c in node.children:
            r = find_node(c, tag)
            if r: return r
    elif isinstance(node, list):
        for c in node:
            r = find_node(c, tag)
            if r: return r
    return None

print(f"Loading {INPUT_PATH} ({INPUT_PATH.stat().st_size:,} bytes) ...")
reader    = ESFReader(INPUT_PATH)
root      = reader.read_root()
tag_names = reader.tag_names
timestamp = reader.timestamp

cws = find_node(root, "CAI_WORLD_SETTLEMENTS")
assert cws is not None
assert len(cws.children) == 138, f"Expected 138, got {len(cws.children)}"

occ_item = cws.children[-1]   # occitania = last item

# 1. OWNED_DIRECT[0]: restore to France's type code
old_od = occ_item[0].children[0].value
occ_item[0].children[0].value = FRANCE_OD_TYPE_CODE
occ_item[0].children[0].raw   = None
print(f"OWNED_DIRECT[0]: {old_od} -> {FRANCE_OD_TYPE_CODE}")

# 2. CAI_SETTLEMENT[2]: set to proper sequential value
old_cs = occ_item[28].children[2].value
occ_item[28].children[2].value = NEXT_CS_ID
occ_item[28].children[2].raw   = None
print(f"CAI_SETTLEMENT[2]: {old_cs} -> {NEXT_CS_ID}")

# Confirm the values we previously fixed are still correct
print(f"CAI_SITUATED[2] (should be 1264): {occ_item[1].children[2].value}")
print(f"item[3] (should be 3595):         {occ_item[3].value}")
print(f"CAI_SITUATED x (should be 1.400): {occ_item[1].children[0].value / (1<<20):.3f}")
print(f"CAI_SITUATED y (should be 311.0): {occ_item[1].children[1].value / (1<<20):.3f}")

print(f"\nWriting {OUTPUT_PATH} ...")
writer = ESFWriter(root, tag_names, timestamp)
OUTPUT_PATH.write_bytes(writer.to_bytes())
print(f"Done. {OUTPUT_PATH.stat().st_size:,} bytes")

# Sanity re-read
print("\nSanity re-read ...")
reader2 = ESFReader(OUTPUT_PATH)
root2   = reader2.read_root()
cws2    = find_node(root2, "CAI_WORLD_SETTLEMENTS")
assert len(cws2.children) == 138
occ2 = cws2.children[-1]
assert occ2[0].children[0].value == FRANCE_OD_TYPE_CODE
assert occ2[28].children[2].value == NEXT_CS_ID
print("PASS")

original = OUTPUT_PATH.read_bytes()
writer3  = ESFWriter(ESFReader(OUTPUT_PATH).read_root(), reader2.tag_names, reader2.timestamp)
if writer3.to_bytes() == original:
    print(f"Round-trip PASS ({len(original):,} bytes)")
else:
    for i, (a, b) in enumerate(zip(original, writer3.to_bytes())):
        if a != b:
            print(f"Round-trip FAIL at 0x{i:X}: 0x{a:02X} vs 0x{b:02X}")
            break
