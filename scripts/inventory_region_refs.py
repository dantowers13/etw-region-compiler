"""
Cross-file inventory of every structure that encodes "a region" in ETW.

This is the ground truth the deeper compiler has to satisfy.  It reads the
three campaign files and prints, for one named region, every place the
engine keeps region-shaped data:

  pathfinding.esf   grid_cells item layout, interior runs vs boundary cells,
                    boundary-record path_type histogram, adjacency lists
  regions.esf       mesh (shared vertex table, per-area faces/outlines),
                    theatre flag, quadtree spatial index size
  startpos.esf      every array sized to a region count (205 / 137 / 85),
                    CAI_WORLD_REGIONS record, CAI region boundaries (adjacency)

Usage (from repo root, any venv with numpy is fine, only stdlib is used):

    python scripts/inventory_region_refs.py --game-dir "X:/Games/Steam/steamapps/common/Empire Total War"
    python scripts/inventory_region_refs.py --pathfinding p.esf --regions r.esf --startpos s.esf --region denmark

See docs/reverse_eng/deep_dive.md for what the numbers mean.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from etwpc.io.esf_reader import ESFReader  # noqa: E402
from etwpc.io.esf_types import (  # noqa: E402
    ESFNode, ESFPrimitive, BoundaryEntry,
    T_U1_ARY, T_U2, T_U4, T_RECORD_ARY,
)

EUROPE_AREA = 2          # pathfinding_areas[2] is the European grid
EUROPE_THEATRE_FLAG = 2  # regions[i][5] == theatre index for active regions


# ─── generic helpers ──────────────────────────────────────────────────────────

def find(node, tag):
    if isinstance(node, ESFNode):
        if node.tag == tag:
            return node
        for c in node.children:
            r = find(c, tag)
            if r:
                return r
    elif isinstance(node, list):
        for c in node:
            r = find(c, tag)
            if r:
                return r
    return None


def walk(node, fn, path=()):
    """Depth-first visit; fn(node, path) is called for every ESFNode and ESFPrimitive."""
    if isinstance(node, ESFNode):
        fn(node, path)
        for c in node.children:
            walk(c, fn, path + (node.tag,))
    elif isinstance(node, list):
        for c in node:
            walk(c, fn, path)
    elif isinstance(node, ESFPrimitive):
        fn(node, path)


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# ─── pathfinding.esf ──────────────────────────────────────────────────────────

def inventory_pathfinding(path: Path, region: str, regions_names: list[str]):
    section(f"[1] pathfinding.esf - {path}")
    root = ESFReader(path).read_root()
    area = root.children[0].children[EUROPE_AREA]
    verts, vlist, gd = area[0], area[1], area[2]
    print(f"vertices: {len(verts.children):,}   boundary vertex-index list: {len(vlist.value):,} u32")

    fixed = 1 << 20
    ch = gd.children
    cols, rows = ch[5].value, ch[6].value
    print(f"grid: origin=({ch[0].value / fixed}, {ch[1].value / fixed}) cell={ch[4].value / fixed} "
          f"{cols}x{rows}  n_passable={ch[8].value} n_listed={ch[9].value} "
          f"header_count={ch[7].value}")

    i2 = list(ch[10].value)              # path_id (0-based) -> regions.esf index
    u2 = list(ch[11].value)              # sorted 1-based pids, then adjacency lists
    n = ch[8].value
    pid_of = {regions_names[r]: p for p, r in enumerate(i2) if 0 <= r < len(regions_names)}
    if region not in pid_of:
        print(f"!! region {region!r} has no path_id in the European grid")
        return
    pid = pid_of[region]
    print(f"{region}: path_id={pid} (0-based; adjacency lists use {pid + 1})  regions.esf index={i2[pid]}")

    sorted_pids, adj = u2[:n], u2[n:]
    entries, pos = [], 0
    while pos < len(adj):
        k = adj[pos]
        entries.append(tuple(adj[pos + 1:pos + 1 + k]))
        pos += 1 + k
    names_1b = {p + 1: regions_names[r] for p, r in enumerate(i2)}
    mine = [e for e in entries if pid + 1 in e]
    print(f"u2_ary tail = {len(entries)} border groups (sizes {dict(Counter(len(e) for e in entries))}),"
          f" NOT per-region neighbour lists")
    print(f"  groups containing {region}: {[tuple(names_1b.get(x, '?') for x in e) for e in mine]}")
    print("  -> france_split.py indexes this section as if entry[i] belonged to sorted_pids[i-1];"
          " it appended a pid to an unrelated pair and duplicated (spain, france)")

    gc = ch[12]
    shapes = Counter()
    btype = Counter()
    run_cells = 0
    run_items = 0
    bnd_items_own = 0            # items with a type-0 boundary carrying this region's pid
    total_cells = 0
    run_pid_cells = Counter()
    for it in gc.children:
        blobs = [c for c in it if isinstance(c, ESFPrimitive) and c.type_tag == T_U1_ARY]
        n_run = len(blobs[1].raw) // 12 if len(blobs) > 1 else 0
        t_u2 = next((c.value for c in it if isinstance(c, ESFPrimitive) and c.type_tag == T_U2), None)
        bnode = next(c for c in it if isinstance(c, ESFNode))
        bs = [BoundaryEntry.from_packed(b[0].value, b[1].value) for b in bnode.children]
        shapes["header+boundaries" + ("+run" if t_u2 is not None else "")] += 1
        for b in bs:
            btype[b.path_type] += 1
        if t_u2 is not None:
            run_pid_cells[t_u2] += n_run
            if t_u2 == pid:
                run_cells += n_run
                run_items += 1
        if any(b.path_type == 0 and b.path_id == pid for b in bs):
            bnd_items_own += 1
        total_cells += 1 + n_run
    print(f"grid_cells items: {len(gc.children):,}  cells accounted: {total_cells:,} "
          f"(expect {cols * rows:,})")
    for k, v in shapes.most_common():
        print(f"  item shape {k:<28} {v:6,}")
    print("boundary records by path_type:", dict(sorted(btype.items())),
          "  (0=region edge, 1=sea, 2/3=transition, 4-7=variants - semantics unresolved)")
    print(f"{region}: interior run cells (T_U2=={pid}) = {run_cells} in {run_items} items;"
          f" items with a type-0 boundary tagged {pid} = {bnd_items_own}")
    print("  -> a split that only rewrites T_U2 touches interior runs and NONE of the boundary cells")
    print("largest interior-run owners (pid: cells):",
          [(p, names_1b.get(p + 1, 'sea/transition'), c) for p, c in run_pid_cells.most_common(6)])


# ─── regions.esf ──────────────────────────────────────────────────────────────

def inventory_regions(path: Path, region: str) -> list[str]:
    section(f"[2] regions.esf - {path}")
    root = ESFReader(path).read_root()
    print("root:", [c.tag for c in root.children])
    rd = root.children[3]
    vraw = rd.children[0].children[0].raw
    nv = len(vraw) // 8
    print(f"global vertex table: {nv:,} v2 (world coords), bbox {rd.children[1].value} .. {rd.children[2].value}")

    regs = rd.children[3]
    names = [str(it[0].value) for it in regs.children]
    types = [str(it[1].value) for it in regs.children]
    flags = [it[5].value for it in regs.children]
    print(f"regions: {len(names)}  by type {dict(Counter(types))}  by theatre-flag {dict(Counter(flags))}")
    print(f"  with settlement_and_slots: {sum(len(it) >= 7 for it in regs.children)} "
          f"(== startpos REGIONS_ARRAY count)")
    dormant = [n for n, t, f in zip(names, types, flags) if f == -1 and t == "land"]
    print(f"  dormant land slots (flag -1): {len(dormant)} e.g. {dormant[:8]}")

    owner: dict[int, set] = defaultdict(set)
    for nm, it in zip(names, regs.children):
        for a in it[4].children:
            for ol in a[7].children:
                for v in ol[3].value:
                    owner[v].add(nm)
    mult = Counter(len(s) for s in owner.values())
    print(f"outline vertex sharing (how many regions reference each vertex): {dict(sorted(mult.items()))}")
    print("  -> vertices are SHARED along borders; the 'vertices are exclusive' note in "
          "regions_repurpose_slot74.py is wrong")

    if region in names:
        it = regs.children[names.index(region)]
        print(f"{region}: index={names.index(region)} type={it[1].value} theatre-flag={it[5].value} "
              f"bbox={it[2].value}..{it[3].value} areas={len(it[4].children)}")
        for i, a in enumerate(it[4].children):
            faces = len(a[6].children[0].value) // 3
            ols = [len(o[3].value) for o in a[7].children]
            print(f"  area {i}: passable={a[2].value} adj_area_id={a[5].value} faces={faces} "
                  f"outlines(vertex counts)={ols} u2[8]={a[8].value} u2[9]={a[9].value}")
        neigh = Counter(n for v, s in owner.items() if region in s for n in s if n != region)
        print(f"  shares border vertices with: {dict(neigh)}")
        sas = it[6] if len(it) >= 7 else None
        if sas is not None:
            print(f"  settlement at {sas.children[0].value}, slots={len(sas.children[2].children)}")

    qi = root.children[6]
    ncell = [0]
    nseg = [0]

    def count(nd, _p):
        if isinstance(nd, ESFNode) and nd.tag == "cell":
            ncell[0] += 1
            for c in nd.children:
                if isinstance(c, ESFPrimitive) and isinstance(c.value, list):
                    nseg[0] += len(c.value) // 4
    walk(qi, count)
    print(f"query_info quadtree: quads={qi.children[0].value} cells={qi.children[1].value} "
          f"(counted {ncell[0]}) line segments={nseg[0]:,} [v1, v2, packed region/area, packed region/area]")
    print("  -> this is the spatial index the engine uses to answer 'which region is point P in';"
          " none of the scripts rebuild it")
    return names


# ─── startpos.esf ─────────────────────────────────────────────────────────────

def inventory_startpos(path: Path, region: str, n_regions_esf: int):
    section(f"[3] startpos.esf - {path}")
    root = ESFReader(path).read_root()
    ra = find(root, "REGIONS_ARRAY")
    n_sp = len(ra.children)
    sp_names = [it[0].children[0].value for it in ra.children]
    print(f"REGIONS_ARRAY: {n_sp}   CAI_WORLD_REGIONS: {len(find(root, 'CAI_WORLD_REGIONS').children)} "
          f"(== regions.esf {n_regions_esf})")
    targets = {n_regions_esf, n_sp, 85}
    hits: dict[int, Counter] = defaultdict(Counter)

    def scan(nd, p):
        tail = "/".join(p[-3:] + ((nd.tag,) if isinstance(nd, ESFNode) else ()))
        if isinstance(nd, ESFNode) and nd.type_tag == T_RECORD_ARY and len(nd.children) in targets:
            hits[len(nd.children)]["ARY  " + tail] += 1
        elif isinstance(nd, ESFPrimitive) and isinstance(nd.value, list) and len(nd.value) in targets:
            hits[len(nd.value)][f"PRIM {tail}"] += 1
    walk(root, scan)
    for k in sorted(hits):
        label = {n_regions_esf: "regions.esf-indexed", n_sp: "REGIONS_ARRAY-indexed", 85: "pathfinding-pid-indexed?"}[k]
        print(f"arrays of length {k} ({label}):")
        for tag, c in hits[k].most_common(40):
            print(f"   {c:4d}  {tag}")

    cwr = find(root, "CAI_WORLD_REGIONS")
    for item in cwr.children:
        cr = find(item, "CAI_REGION")
        if cr is not None and cr.children[10].value == region:
            print(f"CAI_REGION[{region}]: theatre_ids={cr.children[0].value} hlcis={len(cr.children[1].value)} "
                  f"(one per regions.esf area) settlement_ai={cr.children[2].value} "
                  f"slots={len(cr.children[3].value)} boundary_ids={cr.children[7].value} "
                  f"region_id={cr.children[11].value}")
    nb = len(find(root, "CAI_WORLD_REGION_BOUNDARIES").children)
    hl = len(find(root, "CAI_WORLD_REGION_HLCIS").children)
    ta = len(find(root, "CAI_WORLD_TRANSITION_AREAS").children)
    print(f"CAI_WORLD_REGION_BOUNDARIES (AI adjacency graph with distances): {nb}   "
          f"CAI_WORLD_REGION_HLCIS (one per region area): {hl}   CAI_WORLD_TRANSITION_AREAS: {ta}")
    cp = find(root, "CAMPAIGN_PATHFINDER")
    if cp is not None:
        grids = cp.children[0]
        print(f"CAMPAIGN_PATHFINDER: {len(grids.children)} PATHFINDING_GRID copies inside startpos "
              f"(grid[{EUROPE_AREA}] grid_paths={grids.children[EUROPE_AREA][0].value})")
    print("  -> none of the scripts touch CAI_WORLD_REGIONS, CAI_REGION_BOUNDARY, HLCIS or CAMPAIGN_PATHFINDER")
    if region in sp_names:
        print(f"{region}: REGIONS_ARRAY index {sp_names.index(region)}")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", type=Path, help="ETW install dir (uses data/campaign_maps + data/campaigns/main)")
    ap.add_argument("--pathfinding", type=Path)
    ap.add_argument("--regions", type=Path)
    ap.add_argument("--startpos", type=Path)
    ap.add_argument("--region", default="france")
    a = ap.parse_args()
    if a.game_dir:
        a.pathfinding = a.pathfinding or a.game_dir / "data/campaign_maps/global_map/pathfinding.esf"
        a.regions = a.regions or a.game_dir / "data/campaign_maps/global_map/regions.esf"
        a.startpos = a.startpos or a.game_dir / "data/campaigns/main/startpos.esf"
    for p in (a.pathfinding, a.regions, a.startpos):
        if p is None or not p.exists():
            sys.exit(f"missing file: {p}")
    names = inventory_regions(a.regions, a.region)
    inventory_pathfinding(a.pathfinding, a.region, names)
    inventory_startpos(a.startpos, a.region, len(names))


if __name__ == "__main__":
    main()
