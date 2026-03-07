"""
Polygon rasterizer: the core compiler algorithm.

Takes a shapely.Polygon in world coordinates and relabels grid cells
inside it from parent_cell_id to new_cell_id.

Implement per ETWPC-16.
"""
from __future__ import annotations
import numpy as np
from shapely.geometry import Polygon

from etwpc.io.pathfinding_io import PathfindingGrid


def rasterize_polygon(
    grid: PathfindingGrid,
    polygon: Polygon,
    parent_cell_id: int,
    new_cell_id: int,
) -> np.ndarray:
    """
    Return a modified copy of grid.cells where cells inside `polygon`
    that previously held `parent_cell_id` now hold `new_cell_id`.

    Uses vectorised shapely.contains for performance.
    Only cells currently holding parent_cell_id are modified.
    """
    raise NotImplementedError("ETWPC-16: implement vectorised rasterizer")
