"""
Patch pathfinding_france_split.esf: change i2_ary[85] from 205 -> 74.

The original france_split.py appended occitania with ESF index 205 (would be a
206th entry in regions.esf, which crashes). We repurposed regions.esf slot [74]
('central_italy') as 'occitania' instead, so the pathfinding reference must
point to index 74.

Run from repo root:
    python scripts/fix_pathfinding_esf_id.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import T_I2_ARY

INPUT_PATH  = Path("data/gc/pathfinding_france_split.esf")
OUTPUT_PATH = Path("data/gc/pathfinding_france_split_v2.esf")

OLD_ESF_ID = 205
NEW_ESF_ID = 74    # regions.esf slot repurposed for occitania

print(f"Loading {INPUT_PATH} ...")
reader    = ESFReader(INPUT_PATH)
root      = reader.read_root()
tag_names = reader.tag_names
timestamp = reader.timestamp

europe    = root.children[0].children[2]
grid_data = europe[2]

i2_ary_prim = grid_data.children[10]
assert i2_ary_prim.type_tag == T_I2_ARY
vals = list(i2_ary_prim.value)
assert len(vals) == 86, f"Expected 86 i2_ary entries (85 vanilla + occitania), got {len(vals)}"
assert vals[85] == OLD_ESF_ID, f"Expected i2_ary[85]={OLD_ESF_ID}, got {vals[85]}"

vals[85] = NEW_ESF_ID
i2_ary_prim.value = vals
i2_ary_prim.raw   = None

print(f"i2_ary[85]: {OLD_ESF_ID} -> {NEW_ESF_ID}")

print(f"\nWriting {OUTPUT_PATH} ...")
writer = ESFWriter(root, tag_names, timestamp)
OUTPUT_PATH.write_bytes(writer.to_bytes())
size = OUTPUT_PATH.stat().st_size
print(f"Done. {size:,} bytes")

# Verify
reader2   = ESFReader(OUTPUT_PATH)
root2     = reader2.read_root()
grid_data2 = root2.children[0].children[2][2]
vals2 = list(grid_data2.children[10].value)
assert vals2[85] == NEW_ESF_ID, f"Verify failed: {vals2[85]}"
print(f"Verify i2_ary[85]={vals2[85]}  PASS")

# Round-trip
original = OUTPUT_PATH.read_bytes()
writer3  = ESFWriter(ESFReader(OUTPUT_PATH).read_root(), reader2.tag_names, reader2.timestamp)
output3  = writer3.to_bytes()
if output3 == original:
    print(f"Round-trip PASS ({len(original):,} bytes)")
else:
    for i, (a, b) in enumerate(zip(original, output3)):
        if a != b:
            print(f"Round-trip FAIL at 0x{i:X}: orig=0x{a:02X} out=0x{b:02X}")
            break

print(f"\nAll done. Deploy {OUTPUT_PATH} -> game pathfinding.esf")
