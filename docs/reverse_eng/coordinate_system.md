# Coordinate System

**Status:** Not confirmed — fill in from ETWPC-6

## Reference Region: Brandenburg (Prussia)

The reference region used to establish the coordinate transforms.

| Coordinate space | Value |
|-----------------|-------|
| TGA pixel (centre) | TBD e.g. (512, 234) |
| World position | TBD e.g. (1234.5, 5678.9) |
| Grid cell | TBD e.g. row=45, col=82 |

## Transform: TGA pixel → World space

```python
# Fill in from Phase 0 findings
def pixel_to_world(px: int, py: int) -> tuple[float, float]:
    wx = ORIGIN_X + px * SCALE_X  # ORIGIN_X = TBD, SCALE_X = TBD
    wy = ORIGIN_Y + py * SCALE_Y
    return wx, wy
```

## Transform: World → Grid cell

```python
# Fill in from Phase 0 findings (grid origin and cell_size from pathfinding.esf)
def world_to_cell(wx: float, wy: float) -> tuple[int, int]:
    col = int((wx - GRID_ORIGIN_X) / CELL_SIZE)  # GRID_ORIGIN_X = TBD
    row = int((wy - GRID_ORIGIN_Y) / CELL_SIZE)  # CELL_SIZE = TBD
    return row, col
```
