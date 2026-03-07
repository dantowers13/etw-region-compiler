# ETW pathfinding.esf Binary Format

**Status:** Skeleton only — fill in from ETWPC-7, ETWPC-8, ETWPC-9

Reference: http://t-a-w.blogspot.com/2012/03/esf-empire-total-war-object.html

## ABCE Header (16 bytes)

| Offset | Size | Type | Value |
|--------|------|------|-------|
| 0 | 4 | uint32 LE | 0x0000ABCE (magic) |
| 4 | 4 | uint32 LE | 0x00000000 (always zero) |
| 8 | 4 | uint32 LE | Unix timestamp |
| 12 | 4 | uint32 LE | Footer offset |

## cell_id_coords Encoding

From taw's xml2esf (`on_text_node_cell_id_coords`):

```
Each entry = two uint32 LE values:
  u32_1 = (row << 16) | col   — grid position
  u32_2 = cell_id              — region ID

Sparse representation: [TBD — confirm from ETWPC-7 whether interior cells listed]
Default cell_id for unlisted cells: [TBD]
```

## boundaries Encoding

From taw's xml2esf (`on_empty_node_boundaries`):

```
Each entry = two uint32 LE values:
  u32_1 = (passable_part << 24) | (unknown2 << 4) | path_type
  u32_2 = (path_id << 22) | vertex_index

Semantics: [TBD from ETWPC-8]
Required for pathfinding: [TBD — ETWPC-8 experiment]
```

## Coordinate System

**TBD from ETWPC-6**

```
TGA pixel (px, py) → world (wx, wy):
  wx = origin_x + px * scale_x
  wy = origin_y + py * scale_y
  origin_x = TBD, scale_x = TBD

World (wx, wy) → grid (row, col):
  col = int((wx - grid_origin_x) / cell_size)
  row = int((wy - grid_origin_y) / cell_size)
  grid_origin_x = TBD, cell_size = TBD
```
