"""
High-level interface for pathfinding.esf.

Exposes the occupancy grid as a numpy ndarray of cell IDs.
Implement per ETWPC-13.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np


@dataclass
class PathfindingGrid:
    """The occupancy grid for one theatre (europe, americas, india, etc.)."""
    theatre_id: str
    origin: tuple[float, float]         # world-space (x, y) of grid corner
    cell_size: float                     # world units per cell
    cells: np.ndarray                    # shape=(rows, cols), dtype=uint32, values=cell_id
    boundaries: list = field(default_factory=list)   # raw boundary records

    @property
    def shape(self) -> tuple[int, int]:
        return self.cells.shape  # type: ignore[return-value]

    def world_to_cell(self, wx: float, wy: float) -> tuple[int, int]:
        col = int((wx - self.origin[0]) / self.cell_size)
        row = int((wy - self.origin[1]) / self.cell_size)
        return row, col

    def cell_to_world(self, row: int, col: int) -> tuple[float, float]:
        """Return world-space centre of cell (row, col)."""
        wx = self.origin[0] + (col + 0.5) * self.cell_size
        wy = self.origin[1] + (row + 0.5) * self.cell_size
        return wx, wy

    def get_region_cells(self, cell_id: int) -> list[tuple[int, int]]:
        """Return all (row, col) pairs with this cell_id."""
        rows, cols = np.where(self.cells == cell_id)
        return list(zip(rows.tolist(), cols.tolist()))


class PathfindingFile:
    """Wraps pathfinding.esf — load, modify, save."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.grids: dict[str, PathfindingGrid] = {}
        self._load()

    def _load(self) -> None:
        raise NotImplementedError("ETWPC-13: implement after ETWPC-12 (ESFWriter) done")

    def get_european_grid(self) -> PathfindingGrid:
        # Theatre ID TBD from Phase 1 findings
        key = next(k for k in self.grids if "europe" in k.lower())
        return self.grids[key]

    def save(self, path: Path) -> None:
        raise NotImplementedError("ETWPC-13: implement save")
