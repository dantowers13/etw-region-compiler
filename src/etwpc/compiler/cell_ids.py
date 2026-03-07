"""
cell_id allocator.

Finds the next available cell_id not already used in the grid.
Implement per ETWPC-19.
"""
from __future__ import annotations
import numpy as np


def allocate_cell_id(cells: np.ndarray) -> int:
    """Return the lowest positive integer not already used as a cell_id in `cells`."""
    used = set(int(x) for x in np.unique(cells))
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate
