"""
Coordinate system transforms: TGA pixel ↔ world ↔ grid cell.

European grid constants confirmed from Phase 0 / grid_data-0003.xml.
TGA pixel ↔ world transform constants still TBD (ETWPC-6 follow-up).
"""
from __future__ import annotations

# ── European grid constants (confirmed from grid_data-0003.xml) ───────────────
EURO_GRID_ORIGIN_X: float = -180.0   # world x of grid left edge
EURO_GRID_ORIGIN_Y: float =  140.0   # world y of grid top edge
EURO_CELL_SIZE:     float =    2.0   # world units per cell
EURO_COLS:          int   =  310     # number of columns
EURO_ROWS:          int   =  190     # number of rows
# Bounding box: world (-180, 140) → (440, 520)

# ── TGA pixel → world transform constants (ETWPC-6: still TBD) ───────────────
# Calibrate from europe_lookup.tga pixel coordinates vs. known world positions
ORIGIN_X: float = 0.0   # TBD
ORIGIN_Y: float = 0.0   # TBD
SCALE_X:  float = 1.0   # TBD
SCALE_Y:  float = 1.0   # TBD

# Placeholder for the european grid spec (used by tests)
EUROPEAN_GRID = None  # set to a PathfindingGrid once loaded


def world_to_cell(wx: float, wy: float, grid=None) -> tuple[int, int]:
    """Convert world coordinates to (row, col) grid cell index."""
    ox = EURO_GRID_ORIGIN_X if grid is None else grid.origin[0]
    oy = EURO_GRID_ORIGIN_Y if grid is None else grid.origin[1]
    cs = EURO_CELL_SIZE     if grid is None else grid.cell_size
    col = int((wx - ox) / cs)
    row = int((wy - oy) / cs)
    return row, col


def cell_to_world(row: int, col: int, grid=None) -> tuple[float, float]:
    """Return world-space centre of grid cell (row, col)."""
    ox = EURO_GRID_ORIGIN_X if grid is None else grid.origin[0]
    oy = EURO_GRID_ORIGIN_Y if grid is None else grid.origin[1]
    cs = EURO_CELL_SIZE     if grid is None else grid.cell_size
    wx = ox + (col + 0.5) * cs
    wy = oy + (row + 0.5) * cs
    return wx, wy


def pixel_to_world(px: int, py: int) -> tuple[float, float]:
    """Convert TGA pixel coordinates to ESF world coordinates."""
    # ETWPC-6: calibrate ORIGIN_* / SCALE_* from europe_lookup.tga vs. known region centres
    raise NotImplementedError("ETWPC-6: calibrate TGA pixel→world using europe_lookup.tga")
