"""
Remove the saved unit zones (CHARACTER_OBSTACLEs) that cover regions about to be split,
so split_region.py can cut every border cell (deep_dive 10.11).

A character obstacle is the game's saved zone for one army or agent. Its cells are
"reshaped" in startpos, and split_region.py could only relabel them; the zone outlines the
game then built over those relabelled cells hung or crashed it (selecting units near Lyon,
a rake, AI moves on end turn: deep_dive 10.8-10.10). The game recomputes a unit's zone when
the unit is selected or moves.

    python scripts/clear_zones.py --base out/canaries_base5 --regions france spain --out out/zoneless_base

Copies regions.esf, pathfinding.esf and *.pack from --base; writes startpos.esf.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etwpc.compiler.footprint import AreaGrid  # noqa: E402
from etwpc.compiler.obstacles import ObstacleSystem, _find, _mobs, remove_obstacles, verify_obstacles  # noqa: E402
from etwpc.io.esf_reader import ESFReader  # noqa: E402
from etwpc.io.esf_types import BoundaryEntry  # noqa: E402
from etwpc.io.esf_writer import ESFWriter  # noqa: E402
from reactivate_region import find, region_name, roundtrip_ok  # noqa: E402

GRID = 2


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", type=Path, required=True, help="build dir with regions/pathfinding/startpos.esf")
    ap.add_argument("--regions", nargs="+", required=True, help="regions whose cells the zones may not touch")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    reg = ESFReader(a.base / "regions.esf").read_root()
    names = [region_name(r) for r in reg.children[3].children[3].children]
    g = AreaGrid(ESFReader(a.base / "pathfinding.esf").read_root().children[0].children[GRID])
    pids = {g.pid_of(names.index(n)) for n in a.regions}

    def cell_pids(r, c):
        k, j = g.cell_item[r * g.cols + c]
        it = g.items[k]
        if j or not it.bounds:
            return {it.pid}
        return {BoundaryEntry.from_packed(x, y).path_id for x, y in it.bounds}

    sr = ESFReader(a.base / "startpos.esf")
    sp = sr.read_root()
    item = find(sp, "CAMPAIGN_PATHFINDER").children[0].children[GRID]
    osys = ObstacleSystem(item)
    lists = _find(item, "OBSTACLE_LISTS")
    char_ids = set(lists.children[2].value)
    cells_of: dict[int, set] = {}
    for packed, ni in osys.pairs:
        rc = (packed >> 16, packed & 0xFFFF)
        for e in _mobs(osys.nodes.children[ni])[0].children:
            for bid in list(e[3].value)[0::2]:
                if bid - 2 in char_ids:
                    cells_of.setdefault(bid - 2, set()).add(rc)
    seed = {cid for cid, cells in cells_of.items() if any(cell_pids(*rc) & pids for rc in cells)}
    # A node stores pre-merged geometry per combination of the obstacles covering its cell,
    # and campaign load looks up the combination still active there. Removing one zone from
    # a shared cell leaves a combination vanilla never stored (crash Empire.exe+0x75a4ab,
    # cell (112, 89): deep_dive 10.11). So remove whole groups: every character zone that
    # shares a cell with a removed one, repeated. Forts stay (their own entries exist).
    chars_at: dict[tuple, set] = {}
    for cid, cells in cells_of.items():
        for rc in cells:
            chars_at.setdefault(rc, set()).add(cid)
    drop = set(seed)
    while True:
        grow = {c for s in chars_at.values() if s & drop for c in s} - drop
        if not grow:
            break
        drop |= grow
    drop = sorted(drop)
    print(f"grid {GRID}: {len(char_ids)} character obstacles; {len(seed)} touch {a.regions}; "
          f"with every zone sharing a cell with them: {len(drop)}")
    def missing_full_set(o):
        n = 0
        for node in o.nodes.children:
            lists = [list(e[3].value)[0::2] for e in _mobs(node)[0].children]
            full = {i for l in lists for i in l}
            n += not any(set(l) == full for l in lists)
        return n
    before = missing_full_set(osys)
    st = remove_obstacles(osys, char_ids=drop)
    osys.flush()
    print(f"removed: {st}")
    print(f"nodes without an entry for their full obstacle set: {before} before, {missing_full_set(osys)} after")
    bad = verify_obstacles(ObstacleSystem(item), g.sequence_index(), g.cols)
    print(f"verify_obstacles grid {GRID}: {len(bad)} problems {bad[:4]}")
    assert not bad

    p = a.out / "startpos.esf"
    p.write_bytes(ESFWriter(sp, sr.tag_names, sr.timestamp).to_bytes())
    print(f"wrote {p} ({p.stat().st_size:,} B), round-trip {'OK' if roundtrip_ok(p) else 'MISMATCH'}")
    for f in [a.base / "regions.esf", a.base / "pathfinding.esf"] + list(a.base.glob("*.pack")):
        shutil.copy2(f, a.out / f.name)
        print(f"copied {f.name}")


if __name__ == "__main__":
    main()
