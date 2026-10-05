"""
Append a NEW region record (index 205, the 206th) by splitting areas off an existing
region - deep_dive section 9, milestone M1.

The new region is created DORMANT, like the eight void regions were in vanilla: a
regions.esf record with theatre flag -1 and its mesh areas, plus a CAI_WORLD_REGIONS
entry. No settlement, no DB row (dormant regions have none), no pathfinding change.
reactivate_region.py can then give it a settlement (M3).

    python scripts/add_region.py --regions-esf out/new_regions_unlocked/regions.esf \\
        --startpos-esf out/new_regions_unlocked/startpos.esf --out out/canaries_m1

What moves (vanilla `unexplorable` is a catch-all of 61 areas):
  unexplorable areas 1-4 -> new region `canary_islands`, areas 0-3
  unexplorable area 0    -> portugal (Madeira), appended as portugal's next area

Every reference to an area by (region, area index) is remapped. In regions.esf a
reference is packed `region << 16 | area`: query_info quadtree leaf defaults and
segment sides, and outline `connectivity` neighbours. In startpos each mesh area has
one CAI_WORLD_REGION_HLCIS record `[region ai id, area id, [area index], x, y]`, listed
in its region's CAI_REGION[1]; the moved HLCIs are retargeted, unexplorable's later
ones shift down. A region's AI adjacency is CAI_WORLD_REGION_BOUNDARIES
`[ai A, ai B, distance, 0]`, listed in both regions' CAI_REGION[7]; the new region
takes over unexplorable's boundary with each neighbour unexplorable no longer touches.
AI theatres list member regions in THEATRE[3].
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from etwpc.io.esf_reader import ESFReader  # noqa: E402
from etwpc.io.esf_writer import ESFWriter  # noqa: E402
from etwpc.io.esf_types import ESFNode, ESFPrimitive  # noqa: E402
from reactivate_region import (  # noqa: E402
    IdPool, all_ints, clear_bdi, deep_copy, find, set_bool, set_int, set_list, set_str,
    set_v2, roundtrip_ok,
)

SRC_REGION = "unexplorable"
NEW_REGION = "canary_islands"
NEW_AREAS = [1, 2, 3, 4]              # Canary Islands
GIVE_AREAS = {0: "portugal"}          # Madeira
DORMANT_TEMPLATE = "central_italy"    # a dormant land record with no boundaries
EUROPE_AI_THEATRE = 34                # CAI theatre of portugal, morocco, atlantic_ocean_e


def packed(region: int, area: int) -> int:
    return region << 16 | area


# ─── regions.esf ──────────────────────────────────────────────────────────────

def area_bbox(areas):
    xs0 = min(a[3].value[0] for a in areas)
    ys0 = min(a[3].value[1] for a in areas)
    xs1 = max(a[4].value[0] for a in areas)
    ys1 = max(a[4].value[1] for a in areas)
    return (xs0, ys0), (xs1, ys1)


def remap_regions_esf(root, remap: dict[int, int]) -> dict[str, int]:
    """Apply `remap` (old packed -> new packed) to every (region, area) reference."""
    counts = {"leaf_default": 0, "segment_side": 0, "connectivity": 0}
    regs = root.children[3].children[3].children
    for it in regs:
        for a in it[4].children:
            for ol in a[7].children:
                for con in ol[4].children:
                    v = con[0].value
                    if v in remap:
                        set_int(con[0], remap[v])
                        counts["connectivity"] += 1

    def walk(n):
        if not isinstance(n, ESFNode):
            return
        if n.tag == "cell":
            d = n.children[1]
            if d.value in remap:
                set_int(d, remap[d.value])
                counts["leaf_default"] += 1
            segs = list(n.children[2].value)
            hit = False
            for i in range(0, len(segs), 4):
                for k in (2, 3):
                    if segs[i + k] in remap:
                        segs[i + k] = remap[segs[i + k]]
                        counts["segment_side"] += 1
                        hit = True
            if hit:
                set_list(n.children[2], segs)
            return
        for c in n.children:
            walk(c)
    walk(root.children[6].children[2])
    return counts


def split_regions_esf(root) -> tuple[dict[int, int], dict]:
    regs_node = root.children[3].children[3]
    regs = regs_node.children
    names = [it[0].value for it in regs]
    si = names.index(SRC_REGION)
    new_idx = len(regs)
    src_areas = regs[si][4].children
    n_src = len(src_areas)

    remap: dict[int, int] = {}
    moved = set(NEW_AREAS) | set(GIVE_AREAS)
    keep = [a for a in range(n_src) if a not in moved]
    for new_a, old_a in enumerate(keep):
        if new_a != old_a:
            remap[packed(si, old_a)] = packed(si, new_a)
    for new_a, old_a in enumerate(NEW_AREAS):
        remap[packed(si, old_a)] = packed(new_idx, new_a)
    gifts = {}
    for old_a, target in GIVE_AREAS.items():
        ti = names.index(target)
        tgt_areas = regs[ti][4].children
        remap[packed(si, old_a)] = packed(ti, len(tgt_areas))
        gifts[old_a] = (ti, len(tgt_areas))
        tgt_areas.append(src_areas[old_a])
        lo, hi = area_bbox(tgt_areas)
        set_v2(regs[ti][2], lo)
        set_v2(regs[ti][3], hi)

    # the new record: a dormant land record (flag -1) holding the moved areas
    tpl = regs[names.index(DORMANT_TEMPLATE)]
    rec = deep_copy(tpl)
    set_str(rec[0], NEW_REGION)
    rec[4].children = [src_areas[a] for a in NEW_AREAS]
    lo, hi = area_bbox(rec[4].children)
    set_v2(rec[2], lo)
    set_v2(rec[3], hi)
    regs.append(rec)
    centre = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
    moved_boxes = {old_a: (src_areas[old_a][3].value, src_areas[old_a][4].value) for old_a in moved}

    regs[si][4].children = [src_areas[a] for a in keep]
    lo, hi = area_bbox(regs[si][4].children)
    set_v2(regs[si][2], lo)
    set_v2(regs[si][3], hi)

    counts = remap_regions_esf(root, remap)
    print(f"regions.esf: {NEW_REGION} appended at index {new_idx} with {len(NEW_AREAS)} areas; "
          f"{SRC_REGION} {n_src} -> {len(keep)} areas; gifts {gifts}; remapped refs {counts}")

    # neighbours, from outline connectivity, for the AI boundaries
    def neighbours(ri):
        out = set()
        for a in regs[ri][4].children:
            for ol in a[7].children:
                for con in ol[4].children:
                    out.add(con[0].value >> 16)
        out.discard(ri)
        return out
    info = {"src_idx": si, "new_idx": new_idx, "names": names + [NEW_REGION], "keep": keep, "gifts": gifts,
            "new_nb": neighbours(new_idx), "src_nb": neighbours(si),
            "centre": centre, "moved_boxes": moved_boxes,
            "new_bbox": (tuple(rec[2].value), tuple(rec[3].value))}
    return remap, info


# ─── startpos ─────────────────────────────────────────────────────────────────

def split_startpos(sp, info: dict, ids: IdPool) -> None:
    cwr = find(sp, "CAI_WORLD_REGIONS")
    items = cwr.children
    names = [find(it, "CAI_REGION").children[10].value for it in items]
    assert names == info["names"][:len(names)], "CAI_WORLD_REGIONS not index-aligned with regions.esf"
    si, ni = info["src_idx"], info["new_idx"]
    assert len(items) == ni, f"expected {ni} CAI regions, found {len(items)}"
    ai_of = [it[2].value for it in items]
    src_cr = find(items[si], "CAI_REGION")

    # new CAI_WORLD_REGIONS item from the dormant template, its BDI caches emptied
    item = deep_copy(items[names.index(DORMANT_TEMPLATE)])
    new_ai = ids.cai()
    set_int(item[2], new_ai)
    set_int(item[0].children[0], 0)                       # OWNED_INDIRECT
    clear_bdi(item)
    cr = find(item, "CAI_REGION")
    set_list(cr.children[0], [EUROPE_AI_THEATRE])
    set_int(cr.children[2], 0)
    set_list(cr.children[3], [])
    cr.children[4].value, cr.children[4].raw = info["centre"][0], b""
    cr.children[5].value, cr.children[5].raw = info["centre"][1], b""
    set_list(cr.children[7], [])
    set_str(cr.children[10], NEW_REGION)
    set_int(cr.children[11], 0)

    # HLCIs: one per mesh area, [region ai, area id, [area index], x, y]
    hl = {it[1].value: find(it, "CAI_REGION_HLCI") for it in find(sp, "CAI_WORLD_REGION_HLCIS").children}
    src_h = list(src_cr.children[1].value)
    assert len(src_h) == len(info["keep"]) + len(NEW_AREAS) + len(info["gifts"]), "HLCI count != area count"
    new_h = []
    for new_a, old_a in enumerate(NEW_AREAS):
        h = hl[src_h[old_a]]
        set_int(h.children[0], new_ai)
        set_list(h.children[2], [new_a])
        new_h.append(src_h[old_a])
    for old_a, (ti, t_area) in info["gifts"].items():
        h = hl[src_h[old_a]]
        set_int(h.children[0], ai_of[ti])
        set_list(h.children[2], [t_area])
        tcr = find(items[ti], "CAI_REGION")
        set_list(tcr.children[1], list(tcr.children[1].value) + [src_h[old_a]])
    for new_a, old_a in enumerate(info["keep"]):
        set_list(hl[src_h[old_a]].children[2], [new_a])
    set_list(src_cr.children[1], [src_h[a] for a in info["keep"]])
    set_list(cr.children[1], new_h)

    # boundaries: take over unexplorable's edge to each neighbour it no longer touches
    bounds = {it[1].value: find(it, "CAI_REGION_BOUNDARY") for it in find(sp, "CAI_WORLD_REGION_BOUNDARIES").children}
    src_b = list(src_cr.children[7].value)
    moved_b = []
    for bid in src_b:
        b = bounds[bid]
        a_, b_ = b.children[0].value, b.children[1].value
        other = b_ if a_ == ai_of[si] else a_
        oi = ai_of.index(other)
        if oi in info["new_nb"] and oi not in info["src_nb"]:
            set_int(b.children[0 if a_ == ai_of[si] else 1], new_ai)
            moved_b.append(bid)
    missing = [info["names"][oi] for oi in info["new_nb"]
               if not any(ai_of[oi] in (bounds[b].children[0].value, bounds[b].children[1].value) for b in moved_b)]
    assert not missing, f"no boundary to retarget for {missing}; would need a new one"
    set_list(src_cr.children[7], [b for b in src_b if b not in moved_b])
    set_list(cr.children[7], moved_b)
    set_bool(cr.children[8], bool(moved_b))

    items.append(item)
    add_border_patrol(sp, info, ai_of, new_ai, ids)
    add_region_memberships(sp, info, item, new_ai, DORMANT_TEMPLATE, ids)

    # AI theatre membership: THEATRE[3] lists the theatre's region ai ids
    for t in find(sp, "CAI_WORLD_THEATRES").children:
        if t[1].value in cr.children[0].value:
            th = find(t, "THEATRE")
            set_list(th.children[3], list(th.children[3].value) + [new_ai])
    print(f"startpos: CAI_WORLD_REGIONS[{ni}] {NEW_REGION} ai {new_ai}, HLCIs {new_h}, "
          f"boundaries {moved_b} (taken from {SRC_REGION}); {SRC_REGION} keeps {len(info['keep'])} HLCIs; "
          f"gift HLCIs {[src_h[a] for a in info['gifts']]}")


PF_GRID = 2                   # Europe pathfinding grid (pathfinding.esf area 2 / startpos grid 2)
PF_MARKERS = {1022, 1023}     # path ids that are edge markers, not region / sea / border ids


def relabel_pathfinding(pf_root, sp, info: dict) -> None:
    """Milestone M2: give the new region its own path id in the Europe grid.

    Path id space of a grid with n regions (deep_dive 9.4): 0..n-1 regions, n = sea,
    n+1.. = one id per border group, 1022/1023 markers. A region pid's regions.esf
    index is i2[order[pid] - 1], order = the first n entries of grid_data[11]
    (deep_dive 9.10), so the new pid gets the last i2 slot and the last order entry.
    Interior run / zero-record header cells carry a region pid or n; boundary records
    of every type use the same space (types 1, 4, 5 only ever n). Adding a region
    therefore appends i2 and shifts sea and every border id up by one. The cell
    structure does not change, so startpos obstacle node sequence ids stay valid;
    startpos keeps one bool per path id (grid item [8]), which gets an entry too."""
    from etwpc.compiler.footprint import AreaGrid
    from etwpc.io.esf_types import BoundaryEntry

    g = AreaGrid(pf_root.children[0].children[PF_GRID])
    n = len(g.i2)
    assert g.gd.children[8].value == g.gd.children[9].value == n
    src_pid = g.pid_of(info["src_idx"])
    new_pid = n
    gift_pid = {a: g.pid_of(ti) for a, (ti, _) in info["gifts"].items()}

    def shifted(pid):
        return pid + 1 if n <= pid < min(PF_MARKERS) else pid

    shifts = 0
    for it in g.items:
        if it.pid is not None and it.pid != shifted(it.pid):
            it.pid = shifted(it.pid)
            shifts += 1
        nb = []
        for a, b in it.bounds:
            be = BoundaryEntry.from_packed(a, b)
            if be.path_id != shifted(be.path_id):
                be.path_id = shifted(be.path_id)
                a, b = be.to_packed()
                shifts += 1
            nb.append((a, b))
        it.bounds = nb

    # relabel the moved areas' land records (all island cells are header cells)
    relabel = {"new": 0, "gift": 0}
    targets = [(a, new_pid, "new") for a in NEW_AREAS] + [(a, p, "gift") for a, p in gift_pid.items()]
    pad = g.cs
    for a, pid, kind in targets:
        (x0, y0), (x1, y1) = info["moved_boxes"][a]
        r0, c0 = g.cell_of(x0 - pad, y0 - pad)
        r1, c1 = g.cell_of(x1 + pad, y1 + pad)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                k, j = g.cell_item[r * g.cols + c]
                it = g.items[k]
                if j or not it.bounds:
                    assert it.pid != src_pid, f"run cell of {SRC_REGION} at ({r},{c}); needs an item split"
                    continue
                nb = []
                for x, y in it.bounds:
                    be = BoundaryEntry.from_packed(x, y)
                    if be.path_type == 0 and be.path_id == src_pid:
                        be.path_id = pid
                        x, y = be.to_packed()
                        relabel[kind] += 1
                    nb.append((x, y))
                it.bounds = nb
    assert relabel["new"], "no island records relabelled"

    ch = g.gd.children
    g.i2.append(info["new_idx"])
    set_list(ch[10], g.i2)
    for k in (8, 9):                                  # u16 counts
        ch[k].value, ch[k].raw = n + 1, b""
    # u2: per path id its 1-based i2 slot, then the border groups as [count, 1-based i2
    # slots...] (unchanged: the islands have no land border). The new pid maps to the new
    # last slot. Inserting it by southern edge (canaries_m2..m4) shifted every later pid
    # onto its predecessor's region: spain's cells -> wilderness_khiva, gibraltar's ->
    # malta, the islands -> iceland (deep_dive 9.10).
    u2 = list(ch[11].value)
    order, groups = u2[:n], u2[n:]
    order.append(n + 1)
    set_list(ch[11], order + groups)
    g.order = order
    assert g.region_of(new_pid) == info["new_idx"] and g.region_of(src_pid) == info["src_idx"]
    g.serialize()

    # startpos: obstacle cells keep their own copy of the cell's boundary records,
    # OBSTACLE_BOUNDARIES [n, (a, b) * n, cellid, 0] - same packing, same id space.
    # Unshifted, Gibraltar's fort cells kept sea = 85 = the new region: strait closed.
    from etwpc.compiler.obstacles import ObstacleSystem
    item = find(sp, "CAMPAIGN_PATHFINDER").children[0].children[PF_GRID]
    osys = ObstacleSystem(item)
    ob_shift = ob_src = 0
    for e in osys.entries:
        pairs = list(e.pairs)
        for i in range(0, len(pairs), 2):
            be = BoundaryEntry.from_packed(pairs[i], pairs[i + 1])
            if be.path_type == 0 and be.path_id == src_pid:
                ob_src += 1            # an obstacle cell on a moved area would need relabelling too
            if be.path_id != shifted(be.path_id):
                be.path_id = shifted(be.path_id)
                pairs[i], pairs[i + 1] = be.to_packed()
                ob_shift += 1
        e.pairs = pairs
    osys.flush()
    print(f"  startpos obstacle boundary records shifted: {ob_shift} (records on {SRC_REGION}'s pid: {ob_src})")

    # startpos: one bool per path id in the grid copy
    flags = next(c for c in item if isinstance(c, ESFPrimitive) and c.type_tag == 0x41)
    fl = list(flags.value)
    assert len(fl) >= n + 1, f"startpos path flags {len(fl)} < sea id {n}"
    fl.insert(new_pid, fl[src_pid])
    flags.value, flags.raw = fl, b""
    print(f"pathfinding grid {PF_GRID}: {NEW_REGION} path id {new_pid} (i2 slot {n}); sea {n}->{n + 1}, "
          f"border ids +1 ({shifts} cells/records); relabelled records {relabel}; startpos path flags {len(fl)}")


REGION_ANALYSIS_DESIRE = 11   # CAI_BDI_POOL desire that owns one border-patrol belief per region
FIXED = 1 << 20


def add_border_patrol(sp, info: dict, ai_of: list[int], new_ai: int, ids: IdPool) -> None:
    """Per-region CAI_BORDER_PATROL_ANALYSIS beliefs (crash Empire.exe+0x8ada40 without).

    CAI_INTERFACE/CAI_BDI_POOL desire 11 owns exactly one belief per region (vanilla ids
    7638..7842 in region order): BLOCK_OWNS entries [id, 11, 0.0, 0.0, 1], count [17],
    id list [19], CAI_ANALYSER [0] ids and [1] (region ai, belief id) pairs. At load the
    engine builds a region-ai -> analysis map from them (205 buckets in vanilla); a CAI
    region without one is looked up, not found, and dereferenced.
    Belief: [type 146, PROPERTY_SET, id, ..., [10] = [11, region index], ...,
    CAI_BORDER_PATROL_ANALYSIS[region ai, SPECIFIC_AREAS[(AREA_SPECIFIC[region ai, area
    index, PATROL_POINTS[(POINT[x, y, [neighbour region ai...]])], x, y])]]]."""
    bp = next(c for c in find(sp, "CAI_INTERFACE").children if isinstance(c, ESFNode) and c.tag == "CAI_BDI_POOL")
    beliefs = find(bp, "CAI_BDI_POOL_BELIEFS")
    by_id = {it[2].value: it for it in beliefs.children}
    desire = next(it for it in find(bp, "CAI_BDI_POOL_DESIRES").children if it[2].value == REGION_ANALYSIS_DESIRE)
    owns = next(c for c in desire if isinstance(c, ESFNode) and c.tag == "CAI_BDI_COMPONENT_BLOCK_OWNS")
    analyser = next(c for c in desire if isinstance(c, ESFNode) and c.tag == "CAI_ANALYSER")
    region_bel = list(analyser.children[0].value)
    assert len(region_bel) == info["new_idx"] == desire[17].value, "desire 11 is not one belief per region"
    si, ni = info["src_idx"], info["new_idx"]

    def analysis(bid):
        return find(by_id[bid], "CAI_BORDER_PATROL_ANALYSIS")

    new_id = max(region_bel) + 1 if max(region_bel) + 1 not in ids.used else ids.cai()
    ids.used.add(new_id)
    bel = deep_copy(by_id[region_bel[info["names"].index(DORMANT_TEMPLATE)]])
    set_int(bel[2], new_id)
    set_list(bel[10], [REGION_ANALYSIS_DESIRE, ni])
    an = find(bel, "CAI_BORDER_PATROL_ANALYSIS")
    set_int(an.children[0], new_ai)
    new_areas = find(an, "CAI_BORDER_PATROL_ANALYSIS_SPECIFIC_AREAS")
    new_areas.children = []

    # move / renumber the source region's area entries
    src_areas = find(analysis(region_bel[si]), "CAI_BORDER_PATROL_ANALYSIS_SPECIFIC_AREAS")
    keep_entries, moved = [], {"new": 0, "gift": 0, "renumbered": 0}
    for entry in src_areas.children:
        spec = entry[0]
        a = spec.children[1].value
        if a in NEW_AREAS:
            set_int(spec.children[0], new_ai)
            set_int(spec.children[1], NEW_AREAS.index(a))
            new_areas.children.append(entry)
            moved["new"] += 1
        elif a in info["gifts"]:
            ti, t_area = info["gifts"][a]
            set_int(spec.children[0], ai_of[ti])
            set_int(spec.children[1], t_area)
            find(analysis(region_bel[ti]), "CAI_BORDER_PATROL_ANALYSIS_SPECIFIC_AREAS").children.append(entry)
            moved["gift"] += 1
        else:
            new_a = info["keep"].index(a)
            if new_a != a:
                set_int(spec.children[1], new_a)
                moved["renumbered"] += 1
            keep_entries.append(entry)
    src_areas.children = keep_entries

    # neighbours' patrol points that face a moved area now face its new owner
    target_ai = {a: new_ai for a in NEW_AREAS} | {a: ai_of[t[0]] for a, t in info["gifts"].items()}
    pad = 3.0
    retargeted = 0
    for bid in region_bel:
        for pt in find_all_points(analysis(bid)):
            nb = list(pt.children[2].value)
            if ai_of[si] not in nb:
                continue
            x, y = pt.children[0].value / FIXED, pt.children[1].value / FIXED
            for a, ((x0, y0), (x1, y1)) in info["moved_boxes"].items():
                if x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad:
                    nb = [target_ai[a] if v == ai_of[si] else v for v in nb]
                    set_list(pt.children[2], nb)
                    retargeted += 1
                    break

    beliefs.children.append(bel)
    entry = deep_copy(owns.children[-1])
    set_int(entry[0], new_id)
    owns.children.append(entry)
    set_int(desire[17], desire[17].value + 1)
    set_list(desire[19], list(desire[19].value) + [new_id])
    set_list(analyser.children[0], region_bel + [new_id])
    set_list(analyser.children[1], list(analyser.children[1].value) + [new_ai, new_id])
    print(f"border patrol: belief {new_id} for {NEW_REGION} (desire {REGION_ANALYSIS_DESIRE} now "
          f"{desire[17].value}); area entries {moved}; neighbour patrol points retargeted {retargeted}")


REGION_GROUP_TYPE, OCCUPANCY_TYPE = 81, 159


def add_region_memberships(sp, info: dict, item, new_ai: int, tpl_name: str, ids: IdPool) -> list[int]:
    """The beliefs a CAI region lists in its wrapper [21]: CAI_BASIC_REGION_GROUP_ANALYSIS
    (type 81, owned by desire 5: every region is in >= 1 group; analyser [1] maps all 205
    region ai ids to a group) and CAI_REGION_OCCUPANCY_ANALYSIS (type 159, desire 20).
    A region owned by a faction but in no group makes the end turn rebuild every group
    with new ids while CAI_REGION_TARGET_PATHS_ANALYSIS keeps the old ones: erase(end)
    crash at Empire.exe+0x8df200 (deep_dive 9.5). The dormant template's own beliefs are
    cloned (a one-region group, like central_italy's 7400) and registered with their
    desire exactly like the border-patrol belief. Returns the new belief ids."""
    bp = next(c for c in find(sp, "CAI_INTERFACE").children if isinstance(c, ESFNode) and c.tag == "CAI_BDI_POOL")
    beliefs = find(bp, "CAI_BDI_POOL_BELIEFS")
    by_id = {it[2].value: it for it in beliefs.children}
    desires = {it[2].value: it for it in find(bp, "CAI_BDI_POOL_DESIRES").children}
    cwr = find(sp, "CAI_WORLD_REGIONS")
    tpl_item = next(it for it in cwr.children if find(it, "CAI_REGION").children[10].value == tpl_name)
    tpl_ai = tpl_item[2].value
    (x0, y0), (x1, y1) = info["new_bbox"]
    made = []
    for tb in tpl_item[21].value:
        t = by_id[tb]
        btype = t[0].value
        if btype not in (REGION_GROUP_TYPE, OCCUPANCY_TYPE):
            continue
        desire = desires[t[10].value[0]]
        owns = next(c for c in desire if isinstance(c, ESFNode) and c.tag == "CAI_BDI_COMPONENT_BLOCK_OWNS")
        analyser = next(c for c in desire if isinstance(c, ESFNode) and c.tag == "CAI_ANALYSER")
        nid = ids.cai()
        bel = deep_copy(t)
        set_int(bel[2], nid)
        set_list(bel[10], [desire[2].value, len(owns.children)])
        set_list(bel[20], [new_ai if v == tpl_ai else v for v in bel[20].value])
        set_list(bel[21], [])                      # template's per-manager ids: not ours to share
        rec = bel[-1]
        if btype == REGION_GROUP_TYPE:
            set_list(rec.children[0], [new_ai])
            set_list(rec.children[1], [])
            set_v2(rec.children[7], ((x0 + x1) / 2, (y0 + y1) / 2))
            for k, v in ((8, x1 - x0), (9, y1 - y0)):
                rec.children[k].value, rec.children[k].raw = float(v), b""
        else:
            set_int(rec.children[0], new_ai)
        beliefs.children.append(bel)
        entry = deep_copy(owns.children[-1])
        set_int(entry[0], nid)
        owns.children.append(entry)
        set_int(desire[17], desire[17].value + 1)
        set_list(desire[19], list(desire[19].value) + [nid])
        set_list(analyser.children[0], list(analyser.children[0].value) + [nid])
        set_list(analyser.children[1], list(analyser.children[1].value) + [new_ai, nid])
        made.append(nid)
        print(f"  belief {nid} type {btype} ({rec.tag}) registered with desire {desire[2].value} "
              f"(now {desire[17].value})")
    set_list(item[21], made)
    return made


def find_all_points(n):
    out, st = [], [n]
    while st:
        x = st.pop()
        if isinstance(x, ESFNode):
            if x.tag == "CAI_BORDER_PATROL_POINT":
                out.append(x)
            else:
                st.extend(x.children)
        elif isinstance(x, list):
            st.extend(x)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions-esf", type=Path, required=True)
    ap.add_argument("--startpos-esf", type=Path, required=True)
    ap.add_argument("--pathfinding-esf", type=Path,
                    help="give the new region its own path id (M2); without it pathfinding is untouched")
    ap.add_argument("--copy-from", type=Path, help="also copy *.pack (and pathfinding.esf without M2) from this build dir")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    rr_ = ESFReader(a.regions_esf)
    reg = rr_.read_root()
    _, info = split_regions_esf(reg)

    sr = ESFReader(a.startpos_esf)
    sp = sr.read_root()
    split_startpos(sp, info, IdPool(all_ints(sp)))

    outputs = [(a.out / "regions.esf", rr_, reg), (a.out / "startpos.esf", sr, sp)]
    if a.pathfinding_esf:
        pr = ESFReader(a.pathfinding_esf)
        pf = pr.read_root()
        relabel_pathfinding(pf, sp, info)
        outputs.append((a.out / "pathfinding.esf", pr, pf))
    for path, reader, root in outputs:
        path.write_bytes(ESFWriter(root, reader.tag_names, reader.timestamp).to_bytes())
        print(f"wrote {path} ({path.stat().st_size:,} B), round-trip {'OK' if roundtrip_ok(path) else 'MISMATCH'}")
    if a.copy_from:
        extra = list(a.copy_from.glob("*.pack")) + ([] if a.pathfinding_esf else [a.copy_from / "pathfinding.esf"])
        for f in extra:
            if f.exists():
                shutil.copy2(f, a.out / f.name)
                print(f"copied {f.name}")


if __name__ == "__main__":
    main()
