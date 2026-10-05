"""
Facts about vanilla pathfinding.esf that the region builders rely on (deep_dive 9.10-9.11).
These require extracted game files and are skipped if not present.

TO RUN:
    Populate data/gc/ with extracted pathfinding.esf and regions.esf
    pytest tests/integration/test_pathfinding_rules.py -v
"""
from pathlib import Path

import pytest

DATA_DIR    = Path(__file__).parent.parent.parent / "data" / "gc"
PATHFINDING = DATA_DIR / "pathfinding.esf"
REGIONS     = DATA_DIR / "regions.esf"


def requires_game_files(*paths):
    """Skip test if any of the required game files are absent."""
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"Game files not present: {', '.join(missing)}")


def europe_grid():
    from etwpc.compiler.footprint import AreaGrid
    from etwpc.io.esf_reader import ESFReader
    return AreaGrid(ESFReader(PATHFINDING).read_root().children[0].children[2])


def region_names():
    from etwpc.io.esf_reader import ESFReader
    from etwpc.io.esf_types import ESFPrimitive, T_UNICODE
    regs = ESFReader(REGIONS).read_root().children[3].children[3]
    return [next(c.value for c in it if isinstance(c, ESFPrimitive) and c.type_tag == T_UNICODE)
            for it in regs.children]


def test_region_of_reads_the_order_list():
    """A path id's region is i2[order[pid] - 1]; vanilla swaps make i2[pid] wrong."""
    requires_game_files(PATHFINDING, REGIONS)
    g, names = europe_grid(), region_names()
    assert names[g.region_of(19)] == "spain"            # i2[19] is gibraltar
    assert names[g.region_of(17)] == "gibraltar"
    assert names[g.region_of(18)] == "wilderness_khiva"
    assert names[g.region_of(5)] == "tripoli"           # i2[5] is palestine
    assert all(g.pid_of(g.region_of(p)) == p for p in range(len(g.i2)))


def test_field_rules_and_cell_invariants_hold_around_the_canaries_and_malta():
    """The decoded pass/unknown2 rules and the cell invariants the coastal carver keeps."""
    requires_game_files(PATHFINDING)
    from etwpc.compiler.coastal import CellView, check_cells, check_nodes, self_test
    v = CellView(europe_grid())
    cells = [(r, c) for r in range(26, 36) for c in range(26, 44)]       # the Canary Islands
    cells += [(r, c) for r in range(54, 62) for c in range(136, 145)]    # Malta
    assert self_test(v, cells) == []
    assert check_cells(v, cells) == []
    assert check_nodes(v, cells) == []
