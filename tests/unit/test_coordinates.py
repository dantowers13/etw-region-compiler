"""
Unit tests for coordinate system transforms.
World→grid constants confirmed from Phase 0 / grid_data-0003.xml.
"""
import pytest

# West Pommerania reference — confirmed from Phase 0
# regions.esf world centre: (94.76274, 374.42502)
WEST_POMM_WORLD = (94.76274, 374.42502)
WEST_POMM_CELL  = (117, 137)   # (row, col)

# TGA pixel calibration still TBD (ETWPC-6)
BRANDON_TGA_PIXEL = (None, None)
BRANDON_WORLD_POS = (None, None)


def test_world_to_cell_west_pommerania():
    """world_to_cell must place west_pommerania at the expected grid cell."""
    from etwpc.compiler.coords import world_to_cell
    row, col = world_to_cell(*WEST_POMM_WORLD)
    assert row == WEST_POMM_CELL[0]
    assert col == WEST_POMM_CELL[1]


def test_world_to_cell_grid_origin():
    """Grid origin (−180, 140) maps to cell (0, 0)."""
    from etwpc.compiler.coords import world_to_cell
    row, col = world_to_cell(-180.0, 140.0)
    assert row == 0
    assert col == 0


def test_cell_to_world_roundtrip():
    """cell_to_world followed by world_to_cell should return the original cell."""
    from etwpc.compiler.coords import world_to_cell, cell_to_world
    for r, c in [(0, 0), (100, 150), (189, 309), (117, 137)]:
        wx, wy = cell_to_world(r, c)
        row2, col2 = world_to_cell(wx, wy)
        assert row2 == r
        assert col2 == c


@pytest.mark.skip(reason="TGA pixel calibration TBD — fill from europe_lookup.tga (ETWPC-6)")
def test_pixel_to_world_known_region():
    """pixel_to_world should produce the known world position for a reference region."""
    from etwpc.compiler.coords import pixel_to_world
    wx, wy = pixel_to_world(*BRANDON_TGA_PIXEL)
    assert abs(wx - BRANDON_WORLD_POS[0]) < 1.0
    assert abs(wy - BRANDON_WORLD_POS[1]) < 1.0
