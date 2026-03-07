"""
Unit tests for coordinate system transforms (ETWPC-6).
Implement once transforms are confirmed from Phase 0 findings.
"""
import pytest

# These values will be filled in from Phase 0 / ETWPC-6 findings.
# Brandenburg (Prussia) is the reference region.
BRANDON_TGA_PIXEL = (None, None)    # e.g. (512, 234)
BRANDON_WORLD_POS = (None, None)    # e.g. (1234.5, 5678.9)
BRANDON_GRID_CELL = (None, None)    # e.g. row=45, col=82


@pytest.mark.skip(reason="Fill in BRANDON_* constants from Phase 0 findings")
def test_pixel_to_world_known_region():
    """pixel_to_world should produce the known world position for Brandenburg's centre."""
    from etwpc.compiler.coords import pixel_to_world
    wx, wy = pixel_to_world(*BRANDON_TGA_PIXEL)
    assert abs(wx - BRANDON_WORLD_POS[0]) < 1.0
    assert abs(wy - BRANDON_WORLD_POS[1]) < 1.0


@pytest.mark.skip(reason="Fill in BRANDON_* constants from Phase 0 findings")
def test_world_to_cell_known_region():
    """world_to_cell should produce the known grid cell for Brandenburg's centre."""
    from etwpc.compiler.coords import world_to_cell, EUROPEAN_GRID
    row, col = world_to_cell(*BRANDON_WORLD_POS, EUROPEAN_GRID)
    assert row == BRANDON_GRID_CELL[0]
    assert col == BRANDON_GRID_CELL[1]
