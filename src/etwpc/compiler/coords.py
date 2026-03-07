"""
Coordinate system transforms: TGA pixel ↔ world ↔ grid cell.

All constants are TBD — measure from game files per ETWPC-6.
Reference region for calibration: Brandenburg (simple rectangle, inland).
"""
from __future__ import annotations

# ── TGA pixel → world transform constants (ETWPC-6: fill from game files) ────
ORIGIN_X: float = 0.0   # TBD
ORIGIN_Y: float = 0.0   # TBD
SCALE_X:  float = 1.0   # TBD
SCALE_Y:  float = 1.0   # TBD

# ── World → grid transform constants (ETWPC-6: read from pathfinding.esf) ─────
GRID_ORIGIN_X: float = 0.0   # TBD
GRID_ORIGIN_Y: float = 0.0   # TBD
CELL_SIZE:     float = 1.0   # TBD

# Placeholder grid spec — replace with measured values
EUROPEAN_GRID = None  # ETWPC-6: populate from pathfinding.esf header


def pixel_to_world(px: int, py: int) -> tuple[float, float]:
    """Convert TGA pixel coordinates to ESF world coordinates."""
    # ETWPC-6: implement once ORIGIN_* and SCALE_* are known
    raise NotImplementedError("ETWPC-6: fill ORIGIN_* / SCALE_* from game files")


def world_to_cell(wx: float, wy: float, grid=None) -> tuple[int, int]:  # type: ignore[assignment]
    """Convert world coordinates to (row, col) grid cell index."""
    # ETWPC-6: implement once GRID_ORIGIN_* and CELL_SIZE are known
    raise NotImplementedError("ETWPC-6: fill GRID_ORIGIN_* / CELL_SIZE from pathfinding.esf")
