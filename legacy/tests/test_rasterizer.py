"""
Unit tests for the polygon rasterizer.
Use synthetic grids — no game files required.
"""
import pytest
import numpy as np
from shapely.geometry import box as shapely_box

PARENT_ID = 10
NEW_ID    = 99
DEFAULT   = 0


def make_test_grid(rows=20, cols=20, fill_id=PARENT_ID):
    """Create a synthetic all-same-id grid."""
    from etwpc.io.pathfinding_io import PathfindingGrid
    return PathfindingGrid(
        theatre_id="test",
        origin=(0.0, 0.0),
        cell_size=1.0,
        cells=np.full((rows, cols), fill_id, dtype=np.uint32),
    )


@pytest.mark.skip(reason="Implement after ETWPC-16")
def test_rasterize_square_polygon():
    """A square polygon covering a known cell range should reassign exactly those cells."""
    from etwpc.compiler.rasterizer import rasterize_polygon
    grid = make_test_grid()
    polygon = shapely_box(5.0, 5.0, 10.0, 10.0)
    result = rasterize_polygon(grid, polygon, PARENT_ID, NEW_ID)
    assert result[5, 5]  == NEW_ID
    assert result[9, 9]  == NEW_ID
    assert result[4, 4]  == PARENT_ID
    assert result[0, 0]  == PARENT_ID
    assert np.sum(result == NEW_ID) == 25
    assert np.sum(result == PARENT_ID) == 20*20 - 25


@pytest.mark.skip(reason="Implement after ETWPC-16")
def test_rasterize_does_not_touch_other_region_cells():
    """Cells with a different region ID must never be reassigned."""
    from etwpc.compiler.rasterizer import rasterize_polygon
    grid = make_test_grid()
    OTHER_ID = 50
    grid.cells[0:5, 0:5] = OTHER_ID
    polygon = shapely_box(0.0, 0.0, 10.0, 10.0)
    result = rasterize_polygon(grid, polygon, PARENT_ID, NEW_ID)
    assert np.all(result[0:5, 0:5] == OTHER_ID)
