"""
France split PoC: split the France pathfinding region into
  - france        (north, rows 96-108, wy 332-356) — unchanged path_id 33
  - south_france  (south, rows 83-95,  wy 306-330) — new path_id 85

Changes to pathfinding.esf:
  1. n_passable / n_listed: 85 → 86
  2. i2_ary: append regions.esf index for south_france (74 = repurposed central_italy slot)
  3. u2_ary: add south_france to sorted-pid list + update adjacency
  4. grid_cells: remap T_U2 85→86 (old impassable) and 33→85 (south france cells)

Run from repo root:
    python scripts/france_split.py
"""
import struct
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_writer import ESFWriter
from etwpc.io.esf_types import (
    ESFNode, ESFPrimitive,
    T_U2, T_U4, T_I2_ARY, T_U2_ARY, T_U1_ARY,
    T_RECORD_ARY,
)

# ─── Configuration ────────────────────────────────────────────────────────────

INPUT_PATH  = Path("data/gc/pathfinding.esf")
OUTPUT_PATH = Path("data/gc/pathfinding_france_split.esf")

# Pathfinding constants
FRANCE_PATH_ID_T_U2  = 33    # T_U2 value for france cells (0-indexed)
FRANCE_PATH_ID_1IDX  = 34    # 1-indexed path_id (= T_U2 + 1) for france

OLD_IMPASS_T_U2      = 85    # current impassable sentinel T_U2 value
NEW_IMPASS_T_U2      = 86    # new impassable sentinel T_U2 value (= n_passable_new)

SOUTH_FRANCE_T_U2    = 85    # T_U2 value for south_france (occupies old impassable slot)
SOUTH_FRANCE_PID     = 86    # 1-indexed path_id for south_france
SOUTH_FRANCE_ESF_ID  = 74    # index in regions.esf (74 = repurposed central_italy slot)

SPAIN_PID_1IDX       = 19    # spain 1-indexed path_id (for adjacency)

# Grid geometry
SPLIT_ROW = 96  # cells at row < 96 (wy < 332) = south france, row >= 96 = north france
                # row 83 = wy 306 (south), row 95 = wy 330, row 96 = wy 332 (north split)

# ─── Load ─────────────────────────────────────────────────────────────────────

print(f"Loading {INPUT_PATH} …")
reader    = ESFReader(INPUT_PATH)
root      = reader.read_root()
tag_names = reader.tag_names
timestamp = reader.timestamp

# Navigate to Europe grid_data
pf_areas  = root.children[0]                 # T_RECORD_ARY 'pathfinding_areas'
europe    = pf_areas.children[2]             # item[2] = Europe list
grid_data = europe[2]                        # T_RECORD 'grid_data'

FIXED       = 1 << 20
ORIGIN_Y    = grid_data.children[1].value / FIXED  # 140.0
CELL_SIZE   = grid_data.children[4].value / FIXED  # 2.0
COLS        = grid_data.children[5].value           # 310
ROWS        = grid_data.children[6].value           # 190

print(f"Grid: {COLS}×{ROWS}, origin_y={ORIGIN_Y}, cell_size={CELL_SIZE}")
print(f"Split row={SPLIT_ROW} (wy={ORIGIN_Y + SPLIT_ROW * CELL_SIZE})")

# ─── Helper ───────────────────────────────────────────────────────────────────

def cells_in_item(item: list) -> int:
    """Count grid cells represented by this grid_cells item (header + trailing)."""
    count = 1
    blobs = [c for c in item if isinstance(c, ESFPrimitive) and c.type_tag == T_U1_ARY]
    if len(blobs) > 1:
        # Trailing blob: 12 bytes per cell (8-byte traits + 4-byte T_U4 repeat)
        count += len(blobs[1].raw) // 12
    return count


def get_item_t_u2(item: list) -> ESFPrimitive | None:
    for c in item:
        if isinstance(c, ESFPrimitive) and c.type_tag == T_U2:
            return c
    return None


# ─── Modify u2_ary ────────────────────────────────────────────────────────────

def rebuild_u2_ary(old_vals: list[int]) -> list[int]:
    """Add south_france to the sorted-pid list and adjacency section."""
    n_regions = 85
    sorted_pids = list(old_vals[:n_regions])    # permutation of 1..85
    adj_raw     = list(old_vals[n_regions:])    # Sea(0) + adjacency entries

    # 1. Append south_france 1-indexed pid to sorted list
    sorted_pids.append(SOUTH_FRANCE_PID)   # now 86 entries

    # 2. Rebuild adjacency section: parse, patch france's entry, append south_france
    france_sorted_idx = sorted_pids.index(FRANCE_PATH_ID_1IDX)   # = 33
    france_entry_idx  = france_sorted_idx + 1  # +1 because Sea occupies entry 0

    pos       = 0
    entry_idx = 0
    new_adj   = []

    while pos < len(adj_raw):
        count = adj_raw[pos]
        adjs  = list(adj_raw[pos + 1 : pos + 1 + count])

        if entry_idx == france_entry_idx:
            # Add south_france to france's neighbour list
            adjs.append(SOUTH_FRANCE_PID)
            count = len(adjs)
            print(f"  france adjacency updated: {adjs}")

        new_adj.append(count)
        new_adj.extend(adjs)
        pos       += 1 + count
        entry_idx += 1

    # 3. Append south_france adjacency: adjacent to france + spain
    new_adj.append(2)                   # count = 2
    new_adj.append(FRANCE_PATH_ID_1IDX) # france (34)
    new_adj.append(SPAIN_PID_1IDX)      # spain  (19)
    print(f"  south_france adjacency appended: [2, {FRANCE_PATH_ID_1IDX}, {SPAIN_PID_1IDX}]")

    return sorted_pids + new_adj


# ─── Modify n_passable / n_listed ─────────────────────────────────────────────

n_passable_prim = grid_data.children[8]
n_listed_prim   = grid_data.children[9]
assert n_passable_prim.value == 85 and n_listed_prim.value == 85, "Unexpected n_passable/n_listed"

n_passable_prim.value = 86
n_listed_prim.value   = 86
print("n_passable/n_listed updated: 85 → 86")

# ─── Modify i2_ary (append south_france ESF region ID) ────────────────────────

i2_ary_prim = grid_data.children[10]
assert i2_ary_prim.type_tag == T_I2_ARY
old_i2 = list(i2_ary_prim.value)
assert len(old_i2) == 85, f"Expected 85, got {len(old_i2)}"

new_i2 = old_i2 + [SOUTH_FRANCE_ESF_ID]
i2_ary_prim.value = new_i2
i2_ary_prim.raw   = None   # force re-encode from value
print(f"i2_ary: appended south_france ESF ID {SOUTH_FRANCE_ESF_ID}, now {len(new_i2)} entries")

# ─── Modify u2_ary ────────────────────────────────────────────────────────────

u2_ary_prim = grid_data.children[11]
assert u2_ary_prim.type_tag == T_U2_ARY
old_u2 = list(u2_ary_prim.value)

new_u2 = rebuild_u2_ary(old_u2)
u2_ary_prim.value = new_u2
u2_ary_prim.raw   = None
print(f"u2_ary: {len(old_u2)} → {len(new_u2)} entries")

# ─── Modify grid_cells ────────────────────────────────────────────────────────

grid_cells = grid_data.children[12]   # T_RECORD_ARY

cell_pos    = 0
remapped_to_impass  = 0   # T_U2 85 → 86
remapped_to_sfrance = 0   # T_U2 33 → 85 (south france)
kept_france         = 0   # T_U2 33, but north (row >= SPLIT_ROW), kept

print("\nScanning grid_cells …")

for item in grid_cells.children:
    n_cells = cells_in_item(item)
    row0    = cell_pos // COLS

    t_u2 = get_item_t_u2(item)

    if t_u2 is not None:
        if t_u2.value == OLD_IMPASS_T_U2:
            # Old impassable → new impassable
            t_u2.value = NEW_IMPASS_T_U2
            remapped_to_impass += 1

        elif t_u2.value == FRANCE_PATH_ID_T_U2:
            # France cell — split on row
            if row0 < SPLIT_ROW:
                t_u2.value = SOUTH_FRANCE_T_U2
                remapped_to_sfrance += 1
            else:
                kept_france += 1

    cell_pos += n_cells

total_cells = cell_pos
assert total_cells == COLS * ROWS, f"Cell count mismatch: {total_cells} vs {COLS * ROWS}"
print(f"  Total cells verified: {total_cells}")
print(f"  Impassable remapped (85→86): {remapped_to_impass}")
print(f"  South-france remapped (33→85): {remapped_to_sfrance}")
print(f"  North-france kept  (33 unchanged): {kept_france}")

# ─── Write output ─────────────────────────────────────────────────────────────

print(f"\nWriting {OUTPUT_PATH} …")
writer = ESFWriter(root, tag_names, timestamp)
OUTPUT_PATH.write_bytes(writer.to_bytes())

size = OUTPUT_PATH.stat().st_size
print(f"Done. Output: {size:,} bytes (original: {INPUT_PATH.stat().st_size:,} bytes)")

# ─── Quick sanity: re-read and verify n_passable ──────────────────────────────

print("\nSanity re-read …")
reader2    = ESFReader(OUTPUT_PATH)
root2      = reader2.read_root()
europe2    = root2.children[0].children[2]
grid_data2 = europe2[2]
assert grid_data2.children[8].value == 86, "n_passable re-read mismatch"
assert grid_data2.children[9].value == 86, "n_listed re-read mismatch"
assert len(grid_data2.children[10].value) == 86, "i2_ary len re-read mismatch"
print("Sanity checks PASSED")
print(f"\nsouth_france cells (T_U2==85 in output): ", end="")

gcells2 = grid_data2.children[12]
sfrance_count = sum(
    1 for item in gcells2.children
    for c in item
    if isinstance(c, ESFPrimitive) and c.type_tag == T_U2 and c.value == 85
)
print(sfrance_count)
print(f"north-france cells (T_U2==33 in output): ", end="")
nfrance_count = sum(
    1 for item in gcells2.children
    for c in item
    if isinstance(c, ESFPrimitive) and c.type_tag == T_U2 and c.value == 33
)
print(nfrance_count)
print("\nAll done. Inspect pathfinding_france_split.esf for in-game testing.")
