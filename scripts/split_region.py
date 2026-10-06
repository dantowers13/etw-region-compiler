"""
Split a mainland region in two along a border line, appending the new region DORMANT
(deep_dive section 10). First case: France -> France + Occitania.

    python scripts/split_region.py --split occitania --regions-esf out/canaries_base5/regions.esf \\
        --startpos-esf out/canaries_base5/startpos.esf --out out/occitania_s1

regions.esf (rules in compiler/mesh.py, deep_dive 10.2-10.3):
  * the parent's main area is cut by the border line: the child gets the part on its side,
    minus any inland river the line runs along (no river polygon may straddle two land
    regions); the border becomes new vertices shared by both rings, and the two coast
    edges it lands on get one new vertex each, in every outline that has them;
  * river banks (open outlines) go to the side they lie in; sub-areas move by spec;
  * connectivity is regenerated for every touched outline (after checking it regenerates
    the stored runs exactly beforehand); quadtree edges are split / added / re-tagged by
    edge ownership and leaf defaults recomputed from their points;
  * faces are re-triangulated for the landmass groups that changed.
startpos: the child's CAI_WORLD_REGIONS entry (dormant template), HLCIs, boundaries (new
parent-child edge with its length; the parent's edge to a neighbour it no longer touches is
handed over, others are cloned), border patrol areas and points, region group / occupancy
beliefs, theatre membership. pathfinding.esf is not touched (milestone S2).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from etwpc.compiler.mesh import NONE, Mesh, packed
from etwpc.io.esf_reader import ESFReader
from etwpc.io.esf_types import ESFNode, ESFPrimitive
from etwpc.io.esf_writer import ESFWriter
from reactivate_region import (  # noqa: E402
    IdPool, all_ints, clear_bdi, deep_copy, find, roundtrip_ok, set_bool, set_int, set_list, set_str, set_v2,
)
from add_region import (  # noqa: E402
    REGION_ANALYSIS_DESIRE, add_region_memberships, find_all_points,
)

FIXED = 1 << 20
DORMANT_TEMPLATE = "central_italy"
RIVER_REGION = "all"
RIVER_MARGIN = 0.35        # child border keeps this far from a river the line runs along
REGION_GROW = 0.05         # region polygons are grown by this before cutting

SPLITS = {
    # ~1700 limit of Guyenne-et-Gascogne + Languedoc, Bay of Biscay -> Mediterranean, in map
    # coordinates (deep_dive 10.1; the Vivarais tip trimmed to ~45N). Child = south side.
    "occitania": dict(
        parent="france", child="occitania",
        cut=[(-17.713, 325.246), (-9.435, 325.46), (-6.187, 324.022), (-2.735, 323.768), (1.279, 324.91),
             (4.826, 324.6), (8.851, 324.0), (10.49, 322.452), (11.895, 321.37), (14.845, 320.608),
             (17.788, 319.823), (21.218, 321.123), (23.443, 321.728), (25.999, 322.642), (29.574, 321.373),
             (32.787, 320.233), (34.214, 319.632), (33.505, 315.822), (33.813, 312.947), (32.99, 310.841),
             (32.519, 309.054), (33.064, 301.249)],
        child_side=(10.6, 311.5),          # a point on the child's side (Toulouse)
        theatre=34,                        # CAI theatre (France's)
    ),
    # Batch 1 (approved on the review map, deep_dive 10.1): polygons in map coordinates,
    # applied in this order to what is left of the parent; borders follow ~1700
    # gouvernements, traced on the game's own (distorted) map in the north.
    "brittany": dict(parent="france", child="brittany", child_side=(-11.0, 342.8), region=[
        # the Duchy with the Pays de Retz south of the Loire, so the line meets the coast once
        (-14.6, 346.9), (-10.5, 344.3), (-7.5, 341.5), (-7.5, 337.5), (-9.4, 335.6), (-10.5, 333.5), (-12.0, 332.2),
        (-20, 330.0), (-46, 330.0), (-46, 352), (-14.6, 352)]),
    "normandy": dict(parent="france", child="normandy", child_side=(0.0, 348.0), region=[
        (-14.6, 346.9), (-14.6, 357.5), (3.8, 357.5), (3.8, 356.0), (8.0, 352.5), (12.8, 350.2), (14.0, 348.9),
        (14.0, 347.9), (12.8, 347.2), (10.5, 346.5), (9.0, 344.3), (-10.5, 344.3)]),
    "provence": dict(parent="france", child="provence", child_side=(39.3, 309.9), region=[
        (32.519, 309.054), (32.99, 310.841), (33.813, 312.947), (33.505, 315.822), (38.896, 316.078), (45.193, 316.116),
        (49.39, 314.908), (51.849, 309.373), (54.298, 301.567), (30.94, 303.61)]),
    "lyonnais": dict(parent="france", child="lyonnais", child_side=(35.8, 324.0), region=[
        (30.692, 329.484), (34.481, 328.135), (41.032, 325.425), (44.549, 323.323), (50.901, 319.441), (49.949, 316.203),
        (49.39, 314.908), (45.193, 316.116), (38.896, 316.078), (33.505, 315.822), (34.214, 319.632), (32.787, 320.233),
        (29.574, 321.373), (27.432, 323.944), (27.026, 327.402), (28.67, 329.313)]),
    "burgundy": dict(parent="france", child="burgundy", child_side=(35.2, 338.1), region=[
        (22.0, 343.5), (28.5, 344.5), (40.0, 345.0), (53.0, 342.0), (53.0, 323.944), (27.632, 323.944),
        (27.026, 327.402), (23.5, 334.0), (21.5, 338.0)]),
    # the walkable valley between the Pyrenees mountain zones around Spain's Andorra slot
    "andorra": dict(parent="spain", child="andorra", child_side=(9.12, 303.26), region=[
        (6.0, 301.4), (12.6, 301.4), (12.6, 305.8), (6.0, 305.8)]),
}
BATCH1 = ["brittany", "normandy", "provence", "lyonnais", "burgundy", "andorra"]


# ─── regions.esf ──────────────────────────────────────────────────────────────

def side_polygon(cut, inside_xy, far=400.0):
    """The half-plane-like polygon on `inside_xy`'s side of an open cut line."""
    line = LineString(cut)
    (x0, y0), (x1, y1) = cut[0], cut[-1]
    for sign in (1, -1):
        # close the line with a big loop on one side
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy)
        nx, ny = -dy / n * sign, dx / n * sign
        loop = [(x1 + nx * far + dx / n * far, y1 + ny * far + dy / n * far),
                (x0 + nx * far - dx / n * far, y0 + ny * far - dy / n * far)]
        poly = Polygon(list(line.coords) + loop).buffer(0)
        if poly.contains(Point(inside_xy)):
            return poly
    raise RuntimeError("could not orient the cut line")


def split_regions_esf(root, spec) -> dict:
    m = Mesh(root)
    names = [m.name(i) for i in range(len(m.regs))]
    pi = names.index(spec["parent"])
    ci = len(m.regs)
    riv = names.index(RIVER_REGION)
    p_areas = m.areas(pi)
    main = p_areas[0]
    assert main[2].value, "parent area 0 is not its main area"
    rings = [ol for ol in main[7].children if ol[0].value]
    banks = [ol for ol in main[7].children if not ol[0].value]
    assert len(rings) == 1, "main area must have exactly one closed ring"
    R = list(rings[0][3].value)
    ring_poly = Polygon([m.V(i) for i in R])
    own0 = m.owner_map()
    # every outline we will regenerate must regenerate exactly before we touch anything
    old_parent0 = {packed(pi, k) for k in range(len(p_areas))}
    odd = [(m.name(ri), ai) for ri, ai, ol in m.outlines()
           if (ri == pi or any(r[0] in old_parent0 for r in m.stored_runs(ol)))
           and m.runs(list(ol[3].value), ol[0].value, own0) != m.stored_runs(ol)]
    # vanilla's few outlines that do not regenerate exactly keep their stored runs; only the
    # neighbour values are patched (renumbered areas, stretches that now face the child)
    odd = {id(ol) for ri, ai, ol in m.outlines()
           if (ri == pi or any(r[0] in old_parent0 for r in m.stored_runs(ol)))
           and m.runs(list(ol[3].value), ol[0].value, own0) != m.stored_runs(ol)}

    # rivers inside the ring (their banks are the parent's open outlines)
    rivers = {}
    for ai in range(len(m.areas(riv))):
        rp = m.area_polygon(riv, ai)
        if not rp.is_empty and ring_poly.intersection(rp).area > 0.5 * rp.area:
            rivers[ai] = rp

    if "region" in spec:
        # a polygon on the map; an inland river it would cut goes wholly to the side that
        # holds most of it (vanilla: no river polygon straddles two land regions)
        # grown a little, so an edge drawn along an earlier child's border lies inside that
        # (already split off) territory instead of on the border itself
        side = Polygon(spec["region"]).buffer(REGION_GROW, join_style=2)
        crossed, to_child = [], []
        for ai, rp in rivers.items():
            f = side.intersection(rp).area / rp.area
            if 1e-6 < f < 1 - 1e-6:
                (to_child if f >= 0.5 else crossed).append(ai)
        if to_child:
            # wider than RIVER_MARGIN, so the corridor earlier splits left around the river
            # (river plus margins, still the parent's) is taken whole, with no slivers
            side = side.union(unary_union([rivers[ai] for ai in to_child]).buffer(RIVER_MARGIN + 0.1, join_style=2))
        if crossed:
            side = side.difference(unary_union([rivers[ai] for ai in crossed]).buffer(RIVER_MARGIN, join_style=2))
        child = ring_poly.intersection(side)
    else:
        # a cut line; rivers it runs along stay with the parent (Occitania, tested in game)
        cut = LineString(spec["cut"])
        side = side_polygon(spec["cut"], spec["child_side"])
        child = ring_poly.intersection(side)
        crossed = [ai for ai, rp in rivers.items() if rp.intersects(cut)]
        if crossed:
            child = child.difference(unary_union([rivers[ai] for ai in crossed]).buffer(RIVER_MARGIN, join_style=2))
            side = side.difference(unary_union([rivers[ai] for ai in crossed]).buffer(RIVER_MARGIN, join_style=2))
    parts = sorted([g for g in getattr(child, "geoms", [child]) if g.geom_type == "Polygon"], key=lambda g: -g.area)
    child = parts[0]
    assert not child.interiors, "child piece has a hole"
    for ai, rp in rivers.items():
        f = child.intersection(rp).area / rp.area
        assert f < 1e-6 or f > 1 - 1e-6, f"river {ai} straddles the new border ({f:.2f})"

    # walk the child ring: R vertices keep their index, everything else is new
    rset = {(round(m.V(i)[0], 4), round(m.V(i)[1], 4)): i for i in R}
    cc = list(child.exterior.coords)[:-1]
    on_r = [rset.get((round(x, 4), round(y, 4))) for x, y in cc]
    n = len(cc)
    # rotate so the walk starts on R right after the new run
    new_idx = [k for k in range(n) if on_r[k] is None]
    assert new_idx, "cut did not create a border"
    start = next(k for k in range(n) if on_r[k] is not None and on_r[(k - 1) % n] is None)
    cc = cc[start:] + cc[:start]
    on_r = on_r[start:] + on_r[:start]
    first_new = next(k for k in range(n) if on_r[k] is None)
    arc_child = on_r[:first_new]                               # R vertices on the child side
    border = cc[first_new:]                                    # new points, ends on R-edges
    if not all(v is None for v in on_r[first_new:]):
        runs, k = [], 0
        while k < n:
            if on_r[k] is None:
                j = k
                while j < n and on_r[j] is None:
                    j += 1
                runs.append((tuple(round(v, 2) for v in cc[k]), tuple(round(v, 2) for v in cc[j - 1]), j - k))
                k = j
            else:
                k += 1
        raise AssertionError(f"{spec['child']}: the border meets the parent's coast more than twice; "
                             f"new stretches (start, end, points): {runs}")
    assert len(border) >= 2

    def edge_of(pt):
        p = Point(pt)
        best = min(range(len(R)), key=lambda k: LineString([m.V(R[k]), m.V(R[(k + 1) % len(R)])]).distance(p))
        return best

    # the border's two end points lie on R edges (coast); insert them there
    a_pt, b_pt = border[0], border[-1]
    ka, kb = edge_of(a_pt), edge_of(b_pt)
    # simplify the interior of the border (buffer arcs) but keep the ends
    mid = LineString(border).simplify(0.05).coords if len(border) > 3 else border
    mid = [tuple(p) for p in mid]
    va = m.add_vertex(*a_pt)
    vb = m.add_vertex(*b_pt)
    inner = [m.add_vertex(*p) for p in mid[1:-1]]
    border_v = [va] + inner + [vb]

    # insert va / vb into every outline that has the coast edge (either direction)
    def insert_on_edge(e, v):
        hit = 0
        for ri, ai, ol in m.outlines():
            vs = list(ol[3].value)
            for i, (x, y) in enumerate(Mesh.edges(vs, ol[0].value)):
                if (x, y) == e or (y, x) == e:
                    vs.insert(i + 1, v)
                    m.set_outline(ol, vs)
                    hit += 1
                    break
        return hit
    ea = (R[ka], R[(ka + 1) % len(R)])
    eb = (R[kb], R[(kb + 1) % len(R)])
    ha, hb = insert_on_edge(ea, va), insert_on_edge(eb, vb)
    R = list(rings[0][3].value)                                # parent ring, now with va, vb

    # rings: the child keeps R's direction along its arc and closes along the border
    ia, ib = R.index(va), R.index(vb)

    def arc(i, j):                       # R from position i to j inclusive, cyclic
        out = [R[i]]
        while i != j:
            i = (i + 1) % len(R)
            out.append(R[i])
        return out
    arc1, arc2 = arc(ia, ib), arc(ib, ia)                     # va..vb and vb..va
    probe = lambda a: Point(m.V(a[len(a) // 2]))
    child_arc, parent_arc = (arc1, arc2) if child.buffer(1e-3).contains(probe(arc1)) else (arc2, arc1)
    if child_arc is arc1:            # va .. vb, then back along the border vb -> va
        child_ring = child_arc + border_v[-2:0:-1]
        parent_ring = parent_arc + border_v[1:-1]
    else:                            # vb .. va, then va -> vb
        child_ring = child_arc + border_v[1:-1]
        parent_ring = parent_arc + border_v[-2:0:-1]
    for ring in (child_ring, parent_ring):
        assert Polygon([m.V(i) for i in ring]).is_valid, "new ring is not a valid polygon"

    # areas: the child's main area is a copy of the parent's
    c_main = deep_copy(main)
    c_ring_ol = c_main[7].children[[k for k, ol in enumerate(main[7].children) if ol[0].value][0]]
    m.set_outline(c_ring_ol, child_ring)
    m.set_outline(rings[0], parent_ring)
    cpoly = Polygon([m.V(i) for i in child_ring])
    c_banks, p_banks = [], []
    for ol in banks:
        pts = [m.V(i) for i in ol[3].value]
        inside = cpoly.contains(Point(pts[len(pts) // 2]))
        (c_banks if inside else p_banks).append(ol)
    c_main[7].children = [c_ring_ol] + [deep_copy(ol) for ol in c_banks]
    main[7].children = [rings[0]] + p_banks
    m.set_area_bbox(c_main)
    m.set_area_bbox(main)

    # sub-areas (mountain zones beside the main ring, not inside it): to the nearer piece
    ppoly = Polygon([m.V(i) for i in parent_ring])
    moved = [k for k in range(1, len(p_areas))
             if cpoly.distance(m.area_polygon(pi, k)) < ppoly.distance(m.area_polygon(pi, k))]
    keep = [0] + [k for k in range(1, len(p_areas)) if k not in moved]
    remap = {}
    for new_a, old_a in enumerate(keep):
        if new_a != old_a:
            remap[packed(pi, old_a)] = packed(pi, new_a)
    for new_a, old_a in enumerate(moved, start=1):
        remap[packed(pi, old_a)] = packed(ci, new_a)

    # the new record (dormant template) and the parent's area list
    names_l = list(names)
    tpl = m.regs[names_l.index(DORMANT_TEMPLATE)]
    rec = deep_copy(tpl)
    set_str(rec[0], spec["child"])
    old_areas = list(p_areas)
    area_node = lambda r: next(c for c in r if isinstance(c, ESFNode) and c.tag == "areas")
    area_node(rec).children = [c_main] + [old_areas[k] for k in moved]
    area_node(m.regs[pi]).children = [old_areas[k] for k in keep]
    m.regs.append(rec)
    m.set_region_bbox(pi)
    m.set_region_bbox(ci)

    # ── connectivity: regenerate every outline that touches the parent's old areas ──
    old_parent = {packed(pi, k) for k in range(len(old_areas))}
    own1 = m.owner_map()
    regen = fallback = 0
    for ri, ai, ol in m.outlines():
        vs = list(ol[3].value)
        mine = packed(ri, ai)
        stored = m.stored_runs(ol)
        touches = ri in (pi, ci) or any(r[0] in old_parent for r in stored) or va in vs or vb in vs
        if not touches:
            continue
        if id(ol) in odd:
            assert va not in vs and vb not in vs, "an outline the split cuts does not regenerate exactly"
            edges = Mesh.edges(vs, ol[0].value)
            patched = []
            for nb, a0, a1 in stored:
                if nb in remap:
                    nb = remap[nb]
                elif nb in old_parent:
                    owners = [own1.get((y, x)) for x, y in edges[a0:a1 + 1] if own1.get((y, x)) is not None]
                    if owners:
                        nb = max(set(owners), key=owners.count)
                patched.append([nb, a0, a1])
            m.write_runs(ol, patched)
            fallback += 1
            continue
        new_runs = m.runs(vs, ol[0].value, own1)
        m.write_runs(ol, new_runs)
        regen += 1
    print(f"regions.esf: {spec['parent']} area 0 cut; {spec['child']} appended at index {ci} with "
          f"{1 + len(moved)} areas (sub-areas {moved}); {spec['parent']} keeps {keep}; border {len(border_v)} "
          f"vertices; coast edge inserts {ha}+{hb}; river banks {len(c_banks)} to child, {len(p_banks)} kept; "
          f"rivers along the line {crossed}; connectivity regenerated on {regen} outlines, patched on {fallback}")

    # ── quadtree ──
    border_edges = list(zip(border_v, border_v[1:]))
    split_edges = {ea: [(ea[0], va), (va, ea[1])], eb: [(eb[0], vb), (vb, eb[1])]}
    child_areas_poly = {0: cpoly} | {i: m.area_polygon(ci, i) for i in range(1, 1 + len(moved))}
    parent_areas_poly = {i: m.area_polygon(pi, i) for i in range(len(keep))}

    def locate_old(pt, old_tag):
        """New tag for a point that belonged to one of the parent's old areas."""
        if old_tag in remap:
            return remap[old_tag]
        if old_tag == packed(pi, 0):
            return packed(ci, 0) if cpoly.contains(Point(pt)) else packed(pi, 0)
        return old_tag

    st = {"split": 0, "added": 0, "retagged": 0, "defaults": 0}
    for lo, hi, cell in m.leaves():
        segs = list(cell.children[2].value)
        out = []
        for i in range(0, len(segs), 4):
            a, b, A, B = segs[i:i + 4]
            pieces = split_edges.get((a, b)) or ([(y, x) for x, y in reversed(split_edges[(b, a)])] if (b, a) in split_edges else None)
            if pieces:
                st["split"] += 1
                for x, y in pieces:
                    if m.overlaps(x, y, lo, hi):
                        out.append([x, y, A, B])
            else:
                out.append([a, b, A, B])
        for x, y in border_edges:
            if m.overlaps(x, y, lo, hi):
                out.append([x, y, NONE, NONE])
                st["added"] += 1
        flat = []
        for a, b, A, B in out:
            nA, nB = own1.get((b, a)), own1.get((a, b))
            mid = ((m.V(a)[0] + m.V(b)[0]) / 2, (m.V(a)[1] + m.V(b)[1]) / 2)
            nA = nA if nA is not None else locate_old(mid, A)
            nB = nB if nB is not None else locate_old(mid, B)
            st["retagged"] += (nA, nB) != (A, B)
            flat += [a, b, nA, nB]
        if flat != segs:
            set_list(cell.children[2], flat)
        d = cell.children[1].value
        if d in old_parent:
            nd = locate_old(cell.children[0].value, d)
            if d == packed(pi, 0) or d in remap:
                if nd != d:
                    set_int(cell.children[1], nd)
                    st["defaults"] += 1
    # other references to moved sub-areas: none outside connectivity / quadtree (deep_dive 9.1)
    print(f"  quadtree: {st}")

    # ── faces: re-triangulate the landmass groups that changed ──
    def group_faces(ri, idxs, holes):
        u = unary_union([m.area_polygon(ri, k) for k in idxs])
        polys = [g for g in getattr(u, "geoms", [u]) if g.geom_type == "Polygon"]
        assert len(polys) == 1, f"region {ri} areas {idxs} are not one landmass"
        poly = polys[0]
        if holes:
            poly = poly.difference(unary_union(holes))
        tri = m.triangulate(poly)
        tri_area = sum(Polygon([m.V(tri[i]), m.V(tri[i + 1]), m.V(tri[i + 2])]).area for i in range(0, len(tri), 3))
        assert abs(tri_area - poly.area) < 1e-3 * poly.area, f"triangulation covers {tri_area:.2f} of {poly.area:.2f}"
        for k in idxs:
            set_list(m.areas(ri)[k][6].children[0], tri)
        return len(tri) // 3

    old_face = {k: tuple(old_areas[k][6].children[0].value) for k in range(len(old_areas))}
    main_group = [k for k in range(len(old_areas)) if old_face[k] == old_face[0]]
    c_group = [0] + [moved.index(k) + 1 for k in main_group if k in moved]
    p_group = [keep.index(k) for k in main_group if k in keep]
    c_rivers = [rp for ai, rp in rivers.items() if cpoly.contains(rp.representative_point())]
    p_rivers = [rp for ai, rp in rivers.items() if ai not in crossed and not cpoly.contains(rp.representative_point())] \
        + [rivers[ai] for ai in crossed]
    tc = group_faces(ci, c_group, c_rivers)
    tp = group_faces(pi, p_group, p_rivers)
    print(f"  faces: {spec['child']} areas {c_group} {tc} triangles; {spec['parent']} areas {p_group} {tp} triangles")
    m.flush_vertices()

    # neighbours for the AI boundaries, with shared lengths
    def neighbour_lengths(ri):
        out = {}
        for k, a in enumerate(m.areas(ri)):
            for ol in a[7].children:
                vs = list(ol[3].value)
                for (x, y) in Mesh.edges(vs, ol[0].value):
                    o = own1.get((y, x))
                    if o is not None and (o >> 16) != ri:
                        out[o >> 16] = out.get(o >> 16, 0.0) + math.dist(m.V(x), m.V(y))
        return out
    cbox = rec[2].value, rec[3].value
    return {"pi": pi, "ci": ci, "names": names + [spec["child"]], "moved": moved, "keep": keep,
            "child_poly": cpoly, "border": [m.V(v) for v in border_v],
            "child_nb": neighbour_lengths(ci), "parent_nb": neighbour_lengths(pi),
            "child_bbox": cbox, "child_areas_poly": child_areas_poly, "parent_areas_poly": parent_areas_poly,
            "side": side,
            "class_ids": [a[5].value for a in m.areas(ci)]}


# ─── pathfinding.esf (milestone S2) ───────────────────────────────────────────

PF_GRID = 2
PF_MARKERS = 1022
STRIP_HALF = 0.5           # vanilla border strips: ~1.0 wide, centred on the regions.esf border
LAND_TYPES = (0, 6, 7, 8, 9, 11)   # record / obstacle-part types that carry a land region id
TOUCH = 1e-4               # two polygons touch when their boundaries share this much length


def _touching(polys_a, polys_b, a_pid, b_pid):
    """(i, j) for each polygon of pid a_pid in polys_a whose boundary shares a stretch with
    one of pid b_pid in polys_b; items are (pid, type, polygon)."""
    out = []
    for i, (pa, ta, A) in enumerate(polys_a):
        if pa != a_pid or ta not in LAND_TYPES:
            continue
        for j, (pb, tb, B) in enumerate(polys_b):
            if pb == b_pid and tb in LAND_TYPES and A.boundary.intersection(B.boundary).length > TOUCH:
                out.append((i, j))
    return out


def separate_records(g, view, cells, fpid, cpid, new_bid) -> dict:
    """Vanilla never lets two regions' land touch (10 contacts on the whole map): a land
    border is a strip of border-id records. Cells under obstacles cannot be cut, so the
    strip has gaps there, and the batch 1 build hung the game (the pathfinder's funnel
    looped at a Lyonnais / Burgundy contact, deep_dive 10.8). Close each remaining contact
    by giving one of the two records the border id: the parent's if it can, else the
    smaller, never a slot outline (type 7); geometry is unchanged. Run cells are never relabelled (that would
    re-split the run); a contact with only run cells / outlines is reported."""
    from shapely.affinity import translate
    from shapely.geometry import box
    from etwpc.io.esf_types import BoundaryEntry
    done, left = 0, set()
    while True:
        view.cache.clear()
        recs = {}
        for rc in cells:
            v = view.get(*rc)
            ox, oy = view.origin(*rc)
            recs[rc] = ([(v[1], 0, box(ox, oy, ox + g.cs, oy + g.cs))] if isinstance(v, tuple) else
                        [(q.be.path_id, q.t, translate(q.poly, ox, oy)) for q in v])
        victims = set()
        for rc, lst in recs.items():
            for nb in (rc, (rc[0], rc[1] + 1), (rc[0] + 1, rc[1])):
                if nb not in recs:
                    continue
                for a_pid, b_pid in ((fpid, cpid), (cpid, fpid)):
                    for i, j in _touching(lst, recs[nb], a_pid, b_pid):
                        cand = [(x, k) for x, k in ((rc, i), (nb, j))
                                if not isinstance(view.get(*x), tuple) and recs[x][k][1] != 7]
                        if not cand:
                            left.add(rc)
                            continue
                        # the parent's record first: S3 places the child's new slots on the
                        # child's land (a Lyonnais piece made border blocked Montbrison)
                        x, k = min(cand, key=lambda xk: (recs[xk[0]][xk[1]][0] != fpid, recs[xk[0]][xk[1]][2].area))
                        victims.add((x, k))
        if not victims:
            break
        for (r, c), k in victims:
            it = g.items[g.cell_item[r * g.cols + c][0]]
            bounds = list(it.bounds)
            be = BoundaryEntry.from_packed(*bounds[k])
            be.path_id = new_bid
            bounds[k] = be.to_packed()
            it.bounds = bounds
        done += len(victims)
    view.cache.clear()
    return {"relabelled": done, "unresolved_cells": sorted(left)}


def regroup_strips(g, view, cells, fpid, cpid, split_ids) -> int:
    """A strip of one of the parent's border groups that now touches only the child's land
    (none of the parent's) takes the child's copy of that group (split_ids: parent group id ->
    child group id). The side-of-line rule misses strip records that lie along the new
    border: Lyonnais land sat against France/Occitania strips south of Montbrison (vanilla
    has 19 land / foreign-strip contacts on grid 2, batch 1 had 28; deep_dive 10.9)."""
    from shapely.affinity import translate
    from shapely.geometry import box
    from etwpc.io.esf_types import BoundaryEntry
    if not split_ids:
        return 0
    def recs(rc):
        v = view.get(*rc)
        ox, oy = view.origin(*rc)
        return ([(v[1], 0, box(ox, oy, ox + g.cs, oy + g.cs))] if isinstance(v, tuple) else
                [(q.be.path_id, q.t, translate(q.poly, ox, oy)) for q in v])
    view.cache.clear()
    done = 0
    for r, c in cells:
        v = view.get(r, c)
        if isinstance(v, tuple) or not any(q.be.path_id in split_ids for q in v):
            continue
        near = [x for d in ((0, 0), (0, 1), (1, 0), (0, -1), (-1, 0))
                if 0 <= r + d[0] < g.rows and 0 <= c + d[1] < g.cols for x in recs((r + d[0], c + d[1]))]
        mine = recs((r, c))
        it = g.items[g.cell_item[r * g.cols + c][0]]
        bounds = list(it.bounds)
        for k, (pid, t, P) in enumerate(mine):
            if pid not in split_ids or t not in LAND_TYPES:
                continue
            touch = {q for q, tq, Q in near if q in (fpid, cpid) and tq in LAND_TYPES and Q is not P
                     and P.boundary.intersection(Q.boundary).length > TOUCH}
            if touch == {cpid}:
                be = BoundaryEntry.from_packed(*bounds[k])
                be.path_id = split_ids[pid]
                bounds[k] = be.to_packed()
                done += 1
        it.bounds = bounds
    view.cache.clear()
    return done


def regroup_parts(g, view, osys, store, reshaped, fpid, cpid, split_ids) -> int:
    """regroup_strips for the obstacle parts of reshaped cells (their own entry's parts and
    the neighbouring cells' records)."""
    from shapely.affinity import translate
    from shapely.geometry import box
    from etwpc.io.esf_types import BoundaryEntry
    from etwpc.compiler.obstacles import part_polygon
    if not split_ids:
        return 0
    def recs(rc):
        v = view.get(*rc)
        ox, oy = view.origin(*rc)
        return ([(v[1], 0, box(ox, oy, ox + g.cs, oy + g.cs))] if isinstance(v, tuple) else
                [(q.be.path_id, q.t, translate(q.poly, ox, oy)) for q in v])
    done = 0
    for e in osys.entries:
        if e.cell not in reshaped:
            continue
        r, c = e.cell
        bes = [BoundaryEntry.from_packed(e.pairs[i], e.pairs[i + 1]) for i in range(0, len(e.pairs), 2)]
        if not any(be.path_id in split_ids for be in bes):
            continue
        polys = [part_polygon(g, store, be, e.cell) for be in bes]
        nbs = [x for d in ((0, 1), (1, 0), (0, -1), (-1, 0))
               if 0 <= r + d[0] < g.rows and 0 <= c + d[1] < g.cols for x in recs((r + d[0], c + d[1]))]
        own = [(be.path_id, be.path_type, p) for be, p in zip(bes, polys) if p is not None]
        changed = False
        for k, (be, P) in enumerate(zip(bes, polys)):
            if P is None or be.path_id not in split_ids or be.path_type not in LAND_TYPES:
                continue
            touch = {q for q, tq, Q in own + nbs if q in (fpid, cpid) and tq in LAND_TYPES and Q is not P
                     and P.boundary.intersection(Q.boundary).length > TOUCH}
            if touch == {cpid}:
                be.path_id = split_ids[be.path_id]
                done += 1
                changed = True
        if changed:
            e.pairs = [x for be in bes for x in be.to_packed()]
    return done


def separate_parts(g, view, osys, store, reshaped, fpid, cpid, new_bid) -> dict:
    """The same rule for the obstacle parts that replace a reshaped cell at runtime: a part
    touching the other region inside its own entry or in a 4-neighbour cell's records gets
    the border id (inside an entry, the smaller of the two)."""
    from shapely.affinity import translate
    from shapely.geometry import box
    from etwpc.io.esf_types import BoundaryEntry
    from etwpc.compiler.obstacles import part_polygon

    def nb_records(rc):
        v = view.get(*rc)
        ox, oy = view.origin(*rc)
        return ([(v[1], 0, box(ox, oy, ox + g.cs, oy + g.cs))] if isinstance(v, tuple) else
                [(q.be.path_id, q.t, translate(q.poly, ox, oy)) for q in v])

    done, left, undecoded = 0, 0, 0
    for e in osys.entries:
        if e.cell not in reshaped:
            continue                     # copy entries are re-copied from their cell
        r, c = e.cell
        bes = [BoundaryEntry.from_packed(e.pairs[i], e.pairs[i + 1]) for i in range(0, len(e.pairs), 2)]
        polys = [part_polygon(g, store, be, e.cell) for be in bes]
        undecoded += sum(p is None for p in polys)
        nbs = [x for d in ((0, 1), (1, 0), (0, -1), (-1, 0))
               if 0 <= r + d[0] < g.rows and 0 <= c + d[1] < g.cols for x in nb_records((r + d[0], c + d[1]))]
        changed = False
        while True:
            parts = [(be.path_id, be.path_type, p) for be, p in zip(bes, polys) if p is not None]
            idx = [i for i, p in enumerate(polys) if p is not None]
            victims = set()
            for a_pid, b_pid in ((fpid, cpid), (cpid, fpid)):
                victims |= {idx[i] for i, _ in _touching(parts, nbs, a_pid, b_pid) if parts[i][1] != 7}
                for i, j in _touching(parts, parts, a_pid, b_pid):
                    cand = [k for k in (i, j) if parts[k][1] != 7]
                    if cand:
                        victims.add(idx[min(cand, key=lambda k: parts[k][2].area)])
            if not victims:
                break
            for k in victims:
                bes[k].path_id = new_bid
            done += len(victims)
            changed = True
        left += bool(_touching([(be.path_id, be.path_type, p) for be, p in zip(bes, polys) if p is not None],
                               nbs, fpid, cpid) or
                     _touching([(be.path_id, be.path_type, p) for be, p in zip(bes, polys) if p is not None],
                               nbs, cpid, fpid))
        if changed:
            e.pairs = [x for be in bes for x in be.to_packed()]
    return {"relabelled": done, "entries_still_touching": left, "undecoded": undecoded}


def split_pathfinding(pf_root, sp, spec, info) -> None:
    """Give the child its own path id in the grid and a border strip with the parent.

    Vanilla (deep_dive 10.4): a land border is a ~1-unit strip of type-0 polygons labelled
    with the pair's border-group id, centred on the regions.esf border, in header cells. So:
    shift sea / border ids up by one and append the child's pid (as add_region M2); carve
    the strip out of the parent's land along the new border; relabel the parent's records
    and run cells on the child's side; give the parent's border groups that now lie wholly
    on the child's side (Spain) to the child, and append a parent / child group. Cells that
    carry a startpos obstacle node are never re-cut (their startpos copies would go stale):
    there records are only relabelled whole, by where they lie, and the same relabel is
    applied to the copies. startpos node sequence ids are renumbered afterwards."""
    from etwpc.compiler.coastal import CellView, commit_plan, self_test, check_cells, check_nodes, CELL, ring_of
    from etwpc.compiler.footprint import AreaGrid, CellSpec
    from etwpc.compiler.obstacles import ObstacleSystem, part_polygon, store_polygons
    from etwpc.io.esf_types import BoundaryEntry
    from shapely.affinity import translate
    from shapely.geometry.polygon import orient
    from reactivate_region import renumber_startpos_nodes

    g = AreaGrid(pf_root.children[0].children[PF_GRID])
    n = len(g.i2)
    pi, ci = info["pi"], info["ci"]
    fpid = g.pid_of(pi)
    cpid = n                                       # the child's new pid
    u2 = list(g.gd.children[11].value)
    order, tail = u2[:n], u2[n:]
    groups, k = [], 0
    while k < len(tail):
        groups.append(tail[k + 1:k + 1 + tail[k]])
        k += 1 + tail[k]
    p_slot = g.i2.index(pi) + 1                    # 1-based i2 slots in the border groups
    c_slot = n + 1

    # geometry: the strip and the child's half of the plane
    border = LineString(info["border"])
    if "region" in spec:
        child_half = info["side"]
    else:
        cut = spec["cut"]
        ext = list(border.coords)
        if Point(ext[0]).distance(Point(cut[0])) > Point(ext[-1]).distance(Point(cut[0])):
            ext.reverse()
        ext = [cut[0]] + ext + [cut[-1]]
        child_half = side_polygon(ext, spec["child_side"])
    strip = border.buffer(STRIP_HALF)
    item = find(sp, "CAMPAIGN_PATHFINDER").children[0].children[PF_GRID]
    osys = ObstacleSystem(item)
    ob_cells = {(p >> 16, p & 0xFFFF) for p, _ in osys.pairs}
    # Obstacle copies (deep_dive 10.6): in a cell wholly inside an obstacle's zone every
    # entry is an exact copy of the cell's records (a run cell's: one full-cell record),
    # and the node's [1] is the cell's record count (9,862 of 9,862). Such cells may be
    # cut; their copies are refreshed afterwards. A cell holding a reshaped entry (the
    # zone's edge crosses it) is only relabelled.
    from etwpc.compiler.obstacles import _mobs
    node_of = {(p >> 16, p & 0xFFFF): i for p, i in osys.pairs}
    copy_entries: dict[tuple[int, int], list[int]] = {}
    reshaped = set()
    for rc, ni in node_of.items():
        k, j = g.cell_item[rc[0] * g.cols + rc[1]]
        it = g.items[k]
        cell = None if (j or not it.bounds) else [x for ab in it.bounds for x in ab]
        for e in _mobs(osys.nodes.children[ni])[0].children:
            ei = e[0].value
            pairs = osys.entries[ei].pairs
            if cell is not None:
                ok = pairs == cell
            else:
                bes = [BoundaryEntry.from_packed(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]
                ok = len(bes) == 1 and bes[0].path_type == 0 and bes[0].path_id == it.pid
            if ok:
                copy_entries.setdefault(rc, []).append(ei)
            else:
                reshaped.add(rc)

    # 1. shift sea and border ids (cells, records, startpos copies)
    shifted = lambda p: p + 1 if n <= p < PF_MARKERS else p
    for it in g.items:
        if it.pid is not None:
            it.pid = shifted(it.pid)
        nb = []
        for a, b in it.bounds:
            be = BoundaryEntry.from_packed(a, b)
            be.path_id = shifted(be.path_id)
            nb.append(be.to_packed())
        it.bounds = nb
    for e in osys.entries:
        pairs = list(e.pairs)
        for i in range(0, len(pairs), 2):
            be = BoundaryEntry.from_packed(pairs[i], pairs[i + 1])
            if be.path_id != shifted(be.path_id):
                be.path_id = shifted(be.path_id)
                pairs[i], pairs[i + 1] = be.to_packed()
        e.pairs = pairs
    bid = lambda gi: n + 1 + gi                    # border group id after the shift (group 0 = sea)

    # 2. the parent's border groups: which side are their strips on?
    side_of_group = {}
    view = CellView(g)
    for r in range(g.rows):
        for c in range(g.cols):
            recs = view.get(r, c)
            if isinstance(recs, tuple):
                continue
            ox, oy = view.origin(r, c)
            for q in recs:
                gi = q.be.path_id - (n + 1)
                if 0 < gi < len(groups) and p_slot in groups[gi]:
                    inside = child_half.contains(translate(q.poly, ox, oy).representative_point())
                    side_of_group.setdefault(gi, set()).add(inside)
    # a group wholly on the child's side becomes the child's; one on both sides is split
    # (the child's part gets its own group, e.g. Savoy's border shared by Lyonnais and Provence)
    regroup, split_to = [], {}
    for gi, sides in sorted(side_of_group.items()):
        if sides == {True}:
            groups[gi] = [c_slot if x == p_slot else x for x in groups[gi]]
            regroup.append(gi)
        elif sides == {True, False}:
            split_to[gi] = len(groups)
            groups.append([c_slot if x == p_slot else x for x in groups[gi]])
    split_ids = {bid(gi): bid(ng) for gi, ng in split_to.items()}
    new_gid = len(groups)                          # group index of the parent / child border
    new_bid = bid(new_gid)

    # 3. cells: relabel / carve. Planning is pure (relabels are recorded in the plan), so
    # it can be redone when a cut must be blocked: a cut that would put new vertices on the
    # edge of a cell holding a reshaped obstacle entry (which must not change) is undone
    # there; other header neighbours get those vertices inserted (vanilla shares every
    # cell-edge vertex with the neighbour).
    x0, y0, x1, y1 = unary_union([info["child_poly"], strip]).buffer(4).bounds
    r0, c0 = g.cell_of(x0, y0)
    r1, c1 = g.cell_of(x1, y1)

    def plan_cells(blocked):
        plan, run_specs = {}, {}
        stats = {"run_relabel": 0, "run_carved": 0, "rec_relabel": 0, "rec_carved": 0, "blocked_cells": 0,
                 "blocked_run_on_strip": 0}
        for r in range(max(0, r0), min(g.rows, r1 + 1)):
            for c in range(max(0, c0), min(g.cols, c1 + 1)):
                ox, oy = view.origin(r, c)
                sq = Polygon([(ox, oy), (ox + 2, oy), (ox + 2, oy + 2), (ox, oy + 2)])
                recs = view.get(r, c)
                hits = sq.intersection(strip).area > 1e-6
                cuts = hits and (r, c) not in blocked
                stats["blocked_cells"] += hits and (r, c) in blocked
                if isinstance(recs, tuple):
                    if recs[1] != fpid:
                        continue
                    if not cuts:
                        stats["blocked_run_on_strip"] += hits
                        if child_half.contains(sq.centroid):
                            spc = g.spec(r, c)
                            run_specs[r * g.cols + c] = CellSpec(spc.hdr, [], spc.word, cpid)
                            stats["run_relabel"] += 1
                        continue
                    out = []
                    for t, pid, geo in ((0, fpid, sq.difference(child_half).difference(strip)),
                                        (0, cpid, sq.intersection(child_half).difference(strip)),
                                        (0, new_bid, sq.intersection(strip))):
                        for pp in [x for x in getattr(geo, "geoms", [geo]) if x.geom_type == "Polygon" and x.area > 1e-6]:
                            out.append((t, pid, orient(translate(pp, -ox, -oy), 1.0), None))
                    plan[(r, c)] = out
                    stats["run_carved"] += 1
                    continue
                out, touched = [], False
                for q in recs:
                    pid0 = q.be.path_id
                    wp = translate(q.poly, ox, oy)
                    if pid0 in split_ids and child_half.contains(wp.representative_point()):
                        out.append((q.t, split_ids[pid0], q.poly, q))
                        touched = True
                        continue
                    if pid0 != fpid or q.t not in (0, 6, 7):
                        out.append((q.t, pid0, q.poly, q))
                        continue
                    if cuts and q.t != 7 and wp.intersection(strip).area > 1e-6:
                        for t, pid, geo in ((q.t, fpid, wp.difference(child_half).difference(strip)),
                                            (q.t, cpid, wp.intersection(child_half).difference(strip)),
                                            (0, new_bid, wp.intersection(strip))):
                            for pp in [x for x in getattr(geo, "geoms", [geo]) if x.geom_type == "Polygon" and x.area > 1e-6]:
                                out.append((t, pid, orient(translate(pp, -ox, -oy), 1.0), None))
                        touched = True
                        stats["rec_carved"] += 1
                    elif child_half.contains(wp.representative_point()):
                        out.append((q.t, cpid, q.poly, q))
                        touched = True
                        stats["rec_relabel"] += 1
                    else:
                        out.append((q.t, pid0, q.poly, q))
                if touched:
                    plan[(r, c)] = out
        return plan, run_specs, stats

    blocked = set(reshaped)
    while True:
        plan, run_specs, stats = plan_cells(blocked)
        extra, conflict = {}, set()
        cut_cells = {rc for rc, out in plan.items() if any(o is None for *_, o in out)}
        for (r, c), out in plan.items():
            for t, pid, poly, orig in out:
                if orig is not None:
                    continue
                for x, y in list(poly.exterior.coords)[:-1]:
                    on = [(0, -1) if abs(x) < 1e-6 else (0, 1) if abs(x - 2) < 1e-6 else None,
                          (-1, 0) if abs(y) < 1e-6 else (1, 0) if abs(y - 2) < 1e-6 else None]
                    on = [d for d in on if d is not None]
                    if len(on) != 1:                       # interior point or a cell corner
                        continue
                    dr, dc = on[0]
                    nb = (r + dr, c + dc)
                    if nb in cut_cells or isinstance(view.get(*nb), tuple):
                        continue
                    if nb in reshaped:
                        conflict.add((r, c))
                    else:
                        extra.setdefault(nb, []).append((x - 2 * dc, y - 2 * dr))
        if not conflict:
            break
        blocked |= conflict
    for nb in extra:
        plan.setdefault(nb, [(q.t, q.be.path_id, q.poly, q) for q in view.get(*nb)])
    # every carved polygon must be valid and the cell still partitioned
    for (r, c), out in plan.items():
        assert abs(sum(p.area for _, _, p, _ in out) - 4.0) < 1e-4, f"cell {(r, c)} not partitioned"
        for t, pid, p, orig in out:
            assert orig is not None or (p.is_valid and not p.interiors), f"cell {(r, c)}: bad polygon"
    around = sorted(set(plan) | ring_of(plan))
    bad = self_test(view, [rc for rc in around if (rc[0], rc[1]) not in plan])
    assert not bad, f"field rules do not reproduce cells beside the border: {bad[:3]}"
    if run_specs:
        g._apply(run_specs)
        view.cache.clear()
        # the plan's original Rec objects came from the old cache: re-read nothing, they stay valid
    res = commit_plan(view, plan, extra)
    view.cache.clear()
    bad = check_cells(view, around) + check_nodes(view, [rc for rc, out in plan.items() if any(o is None for *_, o in out)])
    assert not bad, f"carved cells break a vanilla invariant: {bad[:3]}"
    sep = separate_records(g, view, [(r, c) for r in range(max(0, r0), min(g.rows, r1 + 1))
                                     for c in range(max(0, c0), min(g.cols, c1 + 1))], fpid, cpid, new_bid)
    regrouped = regroup_strips(g, view, [(r, c) for r in range(max(0, r0), min(g.rows, r1 + 1))
                                         for c in range(max(0, c0), min(g.cols, c1 + 1))], fpid, cpid, split_ids)

    # 4. tables: i2 / order / counts / groups
    ch = g.gd.children
    g.i2.append(ci)
    set_list(ch[10], g.i2)
    for k in (8, 9):
        ch[k].value, ch[k].raw = n + 1, b""
    order.append(n + 1)
    groups.append([p_slot, c_slot])
    tail = []
    for gr in groups:
        tail += [len(gr)] + list(gr)
    set_list(ch[11], order + tail)
    g.order = order
    assert g.region_of(cpid) == ci and g.region_of(fpid) == pi
    g.serialize()

    # 5. startpos: per-pid flags, obstacle copies on the child's side, node sequence ids
    flags = next(c for c in item if isinstance(c, ESFPrimitive) and c.type_tag == 0x41)
    fl = list(flags.value)
    fl.insert(cpid, fl[fpid])
    for gi, ng in sorted(split_to.items(), key=lambda kv: kv[1]):
        fl.append(fl[bid(gi)])
    fl.append(False)
    flags.value, flags.raw = fl, b""
    # an obstacle copy follows the cell record it copies (same vertex list); a copy the
    # obstacle reshaped follows the cell when the cell's parent-side land all went one way
    view = CellView(g)
    store = store_polygons(item)
    relab = unresolved = 0
    for e in osys.entries:
        r, c = e.cell
        ox, oy = g.ox + c * g.cs, g.oy + r * g.cs
        recs = view.get(r, c)
        by_vi = {} if isinstance(recs, tuple) else {q.be.vertex_index: q.be.path_id for q in recs}
        land_pids = {recs[1]} if isinstance(recs, tuple) else {q.be.path_id for q in recs if q.t in (0, 6, 7)}
        pairs = list(e.pairs)
        for i in range(0, len(pairs), 2):
            be = BoundaryEntry.from_packed(pairs[i], pairs[i + 1])
            if be.path_id in split_ids:
                # a copy of a split border group's strip: same rule as the parent's records
                tgt = split_ids[be.path_id]
                cell_pids = set(by_vi.values())
                if be.vertex_index in by_vi:
                    newp = by_vi[be.vertex_index]
                elif be.path_id not in cell_pids or tgt not in cell_pids:
                    newp = tgt if tgt in cell_pids else be.path_id
                else:
                    newp = tgt if child_half.contains(Point(ox + 1, oy + 1)) else be.path_id
                if newp != be.path_id:
                    be.path_id = newp
                    pairs[i], pairs[i + 1] = be.to_packed()
                    relab += 1
                continue
            if be.path_id != fpid:
                continue
            poly = part_polygon(g, store, be, (r, c))
            if be.vertex_index in by_vi:
                newp = by_vi[be.vertex_index] if by_vi[be.vertex_index] in (cpid, new_bid) else fpid
            elif fpid not in land_pids or cpid not in land_pids:
                newp = cpid if fpid not in land_pids and cpid in land_pids else fpid
            elif poly is not None:
                newp = cpid if child_half.contains(poly.representative_point()) else fpid
            else:
                unresolved += 1
                newp = cpid if child_half.contains(Point(ox + 1, oy + 1)) else fpid
            if newp != fpid:
                be.path_id = newp
                pairs[i], pairs[i + 1] = be.to_packed()
                relab += 1
        e.pairs = pairs
    sep_parts = separate_parts(g, view, osys, store, reshaped, fpid, cpid, new_bid)
    regrouped_parts = regroup_parts(g, view, osys, store, reshaped, fpid, cpid, split_ids)
    # refresh the copies of every cell that changed (cut, relabelled or new fields)
    refreshed = 0
    for rc, eis in copy_entries.items():
        if rc in reshaped:
            continue
        k, j = g.cell_item[rc[0] * g.cols + rc[1]]
        it = g.items[k]
        if j or not it.bounds:
            for ei in eis:
                pairs = list(osys.entries[ei].pairs)
                be = BoundaryEntry.from_packed(pairs[0], pairs[1])
                if be.path_id != it.pid:
                    be.path_id = it.pid
                    pairs[0], pairs[1] = be.to_packed()
                    osys.entries[ei].pairs = pairs
                    refreshed += 1
            nrec = 1
        else:
            cell = [x for ab in it.bounds for x in ab]
            for ei in eis:
                if osys.entries[ei].pairs != cell:
                    osys.entries[ei].pairs = list(cell)
                    refreshed += 1
            nrec = len(it.bounds)
        set_int(osys.nodes.children[node_of[rc]][1], nrec)
    osys.flush()
    nren = renumber_startpos_nodes(sp, PF_GRID, g)
    print(f"pathfinding grid {PF_GRID}: {spec['child']} path id {cpid}; sea {n}->{n + 1}, border ids +1; new border "
          f"id {new_bid} [{spec['parent']}/{spec['child']}]; groups moved to the child {[groups[gi] for gi in regroup]}, "
          f"split {[groups[ng] for ng in split_to.values()]}; "
          f"{stats}; {res['rewritten_cells']} cells rewritten, {res['vertices_added']} vertices added; startpos flags "
          f"{len(fl)}, obstacle copies relabelled {relab} ({unresolved} undecoded), refreshed {refreshed}, node "
          f"sequence ids renumbered {nren}; direct {spec['parent']}/{spec['child']} contacts closed with the "
          f"border id: records {sep}, obstacle parts {sep_parts}; strips moved to the child's group: records "
          f"{regrouped}, obstacle parts {regrouped_parts}")


# ─── startpos ─────────────────────────────────────────────────────────────────

def clone_item(container, proto_item, new_id):
    it = deep_copy(proto_item)
    set_int(it[1], new_id)
    container.children.append(it)
    return it


def split_startpos(sp, spec, info, ids: IdPool) -> None:
    cwr = find(sp, "CAI_WORLD_REGIONS")
    items = cwr.children
    names = [find(it, "CAI_REGION").children[10].value for it in items]
    pi, ci = info["pi"], info["ci"]
    assert names == info["names"][:len(names)] and len(items) == ci, "CAI_WORLD_REGIONS not aligned with regions.esf"
    ai_of = [it[2].value for it in items]
    p_ai = ai_of[pi]
    p_cr = find(items[pi], "CAI_REGION")

    item = deep_copy(items[names.index(DORMANT_TEMPLATE)])
    new_ai = ids.cai()
    set_int(item[2], new_ai)
    set_int(item[0].children[0], 0)
    clear_bdi(item)
    cr = find(item, "CAI_REGION")
    c = info["child_poly"].representative_point()
    set_list(cr.children[0], [spec["theatre"]] if "theatre" in spec else list(p_cr.children[0].value))
    set_int(cr.children[2], 0)
    set_list(cr.children[3], [])
    cr.children[4].value, cr.children[4].raw = float(spec["child_side"][0]), b""
    cr.children[5].value, cr.children[5].raw = float(spec["child_side"][1]), b""
    set_list(cr.children[7], [])
    set_str(cr.children[10], spec["child"])
    set_int(cr.children[11], 0)

    # HLCIs: one per area [region ai, class id, [area index], x, y]
    hl_node = find(sp, "CAI_WORLD_REGION_HLCIS")
    hl = {it[1].value: it for it in hl_node.children}
    p_h = list(p_cr.children[1].value)
    assert len(p_h) == len(info["keep"]) + len(info["moved"]), "HLCI count != area count"
    new_main = clone_item(hl_node, hl[p_h[0]], ids.cai())
    h = find(new_main, "CAI_REGION_HLCI")
    set_int(h.children[0], new_ai)
    set_list(h.children[2], [0])
    set_int(h.children[3], int(round(spec["child_side"][0] * FIXED)))
    set_int(h.children[4], int(round(spec["child_side"][1] * FIXED)))
    c_h = [new_main[1].value]
    for new_a, old_a in enumerate(info["moved"], start=1):
        hh = find(hl[p_h[old_a]], "CAI_REGION_HLCI")
        set_int(hh.children[0], new_ai)
        set_list(hh.children[2], [new_a])
        c_h.append(p_h[old_a])
    for new_a, old_a in enumerate(info["keep"]):
        set_list(find(hl[p_h[old_a]], "CAI_REGION_HLCI").children[2], [new_a])
    set_list(p_cr.children[1], [p_h[a] for a in info["keep"]])
    set_list(cr.children[1], c_h)

    # boundaries [ai A, ai B, shared length, 0]
    b_node = find(sp, "CAI_WORLD_REGION_BOUNDARIES")
    bmap = {it[1].value: it for it in b_node.children}
    p_b = list(p_cr.children[7].value)
    other_of = {}
    for bid in p_b:
        bb = find(bmap[bid], "CAI_REGION_BOUNDARY")
        other_of[bid] = bb.children[1].value if bb.children[0].value == p_ai else bb.children[0].value
    c_b, handed, cloned = [], [], []
    for oi, length in sorted(info["child_nb"].items()):
        if oi >= len(ai_of) or oi == pi:
            continue
        o_ai = ai_of[oi]
        bid = next((b for b in p_b if other_of[b] == o_ai), None)
        if bid is None:
            continue                                   # the parent never had an AI edge there (rivers)
        if oi not in info["parent_nb"]:
            bb = find(bmap[bid], "CAI_REGION_BOUNDARY")
            set_int(bb.children[0 if bb.children[0].value == p_ai else 1], new_ai)
            bb.children[2].value, bb.children[2].raw = float(length), b""
            p_b.remove(bid)
            c_b.append(bid)
            handed.append(info["names"][oi])
        else:
            nb_it = clone_item(b_node, bmap[bid], ids.cai())
            bb = find(nb_it, "CAI_REGION_BOUNDARY")
            set_int(bb.children[0 if bb.children[0].value == p_ai else 1], new_ai)
            bb.children[2].value, bb.children[2].raw = float(length), b""
            c_b.append(nb_it[1].value)
            nb_cr = find(items[oi], "CAI_REGION")
            set_list(nb_cr.children[7], list(nb_cr.children[7].value) + [nb_it[1].value])
            set_bool(nb_cr.children[8], True)
            cloned.append(info["names"][oi])
    # the new parent-child edge
    pc = clone_item(b_node, bmap[p_b[0]], ids.cai())
    bb = find(pc, "CAI_REGION_BOUNDARY")
    set_int(bb.children[0], p_ai)
    set_int(bb.children[1], new_ai)
    blen = LineString(info["border"]).length
    bb.children[2].value, bb.children[2].raw = float(blen), b""
    set_int(bb.children[3], 0)
    p_b.append(pc[1].value)
    c_b.append(pc[1].value)
    set_list(p_cr.children[7], p_b)
    set_list(cr.children[7], c_b)
    set_bool(cr.children[8], True)

    items.append(item)
    patrol(sp, spec, info, ai_of, new_ai, ids)
    add_region_memberships(sp, {"new_bbox": info["child_bbox"]}, item, new_ai, DORMANT_TEMPLATE, ids)
    for t in find(sp, "CAI_WORLD_THEATRES").children:
        if t[1].value in cr.children[0].value:
            th = find(t, "THEATRE")
            set_list(th.children[3], list(th.children[3].value) + [new_ai])
    print(f"startpos: CAI_WORLD_REGIONS[{ci}] {spec['child']} ai {new_ai}; HLCIs {c_h}; boundaries handed over "
          f"{handed}, cloned {cloned}, new {spec['parent']}-{spec['child']} edge {pc[1].value} length {blen:.1f}")


def patrol(sp, spec, info, ai_of, new_ai, ids: IdPool) -> None:
    """Border-patrol belief for the child (desire 11, one per region; deep_dive 9.3). The
    parent's main-area entry is split by location, moved sub-area entries follow, the
    neighbours' points facing the child are re-targeted, and both sides get points along the
    new border facing each other."""
    pi, ci = info["pi"], info["ci"]
    bp = next(c for c in find(sp, "CAI_INTERFACE").children if isinstance(c, ESFNode) and c.tag == "CAI_BDI_POOL")
    beliefs = find(bp, "CAI_BDI_POOL_BELIEFS")
    by_id = {it[2].value: it for it in beliefs.children}
    desire = next(it for it in find(bp, "CAI_BDI_POOL_DESIRES").children if it[2].value == REGION_ANALYSIS_DESIRE)
    owns = next(c for c in desire if isinstance(c, ESFNode) and c.tag == "CAI_BDI_COMPONENT_BLOCK_OWNS")
    analyser = next(c for c in desire if isinstance(c, ESFNode) and c.tag == "CAI_ANALYSER")
    region_bel = list(analyser.children[0].value)
    assert len(region_bel) == ci == desire[17].value, "desire 11 is not one belief per region"
    areas_of = lambda bid: find(find(by_id[bid], "CAI_BORDER_PATROL_ANALYSIS"), "CAI_BORDER_PATROL_ANALYSIS_SPECIFIC_AREAS")
    p_ai = ai_of[pi]

    new_id = ids.cai()
    bel = deep_copy(by_id[region_bel[info["names"].index(DORMANT_TEMPLATE)]])
    set_int(bel[2], new_id)
    set_list(bel[10], [REGION_ANALYSIS_DESIRE, ci])
    an = find(bel, "CAI_BORDER_PATROL_ANALYSIS")
    set_int(an.children[0], new_ai)
    c_areas = find(an, "CAI_BORDER_PATROL_ANALYSIS_SPECIFIC_AREAS")
    c_areas.children = []
    p_areas = areas_of(region_bel[pi])
    cpoly = info["child_poly"]
    keep_entries = []
    moved_pts = 0
    for entry in p_areas.children:
        spec_n = entry[0]
        a = spec_n.children[1].value
        if a == 0:
            c_entry = deep_copy(entry)
            cs = c_entry[0]
            set_int(cs.children[0], new_ai)
            set_int(cs.children[1], 0)
            # points live in a list node; split by location
            p_list = next(n for n in spec_n.children if isinstance(n, ESFNode))
            c_list = next(n for n in cs.children if isinstance(n, ESFNode))
            keep_p, take_p = [], []
            for pt in p_list.children:
                node = pt[0] if isinstance(pt, list) else pt
                x, y = node.children[0].value / FIXED, node.children[1].value / FIXED
                (take_p if cpoly.buffer(0.8).contains(Point(x, y)) and not info["parent_areas_poly"][0].contains(Point(x, y)) else keep_p).append(pt)
            p_list.children = keep_p
            c_list.children = [deep_copy(p) for p in take_p]
            moved_pts += len(take_p)
            # the entry's reference point
            prims = [c for c in cs.children if isinstance(c, ESFPrimitive)]
            set_int(prims[2], int(round(spec["child_side"][0] * FIXED)))
            set_int(prims[3], int(round(spec["child_side"][1] * FIXED)))
            # points along the new border, one per ~3 units, facing the other side
            border = LineString(info["border"])
            proto = (keep_p or take_p)[0]
            for k in range(1, int(border.length // 3)):
                q = border.interpolate(k * 3.0)
                for lst, face, poly in ((c_list, p_ai, cpoly), (p_list, new_ai, info["parent_areas_poly"][0])):
                    off = None
                    for dx, dy in ((0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6)):
                        if poly.contains(Point(q.x + dx, q.y + dy)):
                            off = (q.x + dx, q.y + dy)
                            break
                    if off is None:
                        continue
                    np_ = deep_copy(proto)
                    node = np_[0] if isinstance(np_, list) else np_
                    set_int(node.children[0], int(round(off[0] * FIXED)))
                    set_int(node.children[1], int(round(off[1] * FIXED)))
                    set_list(node.children[2], [face])
                    lst.children.append(np_)
            c_areas.children.append(c_entry)
            keep_entries.append(entry)
        elif a in info["moved"]:
            set_int(spec_n.children[0], new_ai)
            set_int(spec_n.children[1], info["moved"].index(a) + 1)
            c_areas.children.append(entry)
        else:
            set_int(spec_n.children[1], info["keep"].index(a))
            keep_entries.append(entry)
    p_areas.children = keep_entries

    # neighbours' points that face the parent near the child now face the child
    retargeted = 0
    near = cpoly.buffer(3.0)
    for bid in region_bel:
        for pt in find_all_points(find(by_id[bid], "CAI_BORDER_PATROL_ANALYSIS")):
            nb = list(pt.children[2].value)
            if p_ai in nb and near.contains(Point(pt.children[0].value / FIXED, pt.children[1].value / FIXED)) \
                    and not info["parent_areas_poly"][0].buffer(0.5).contains(Point(pt.children[0].value / FIXED, pt.children[1].value / FIXED)):
                set_list(pt.children[2], [new_ai if v == p_ai else v for v in nb])
                retargeted += 1

    beliefs.children.append(bel)
    entry = deep_copy(owns.children[-1])
    set_int(entry[0], new_id)
    owns.children.append(entry)
    set_int(desire[17], desire[17].value + 1)
    set_list(desire[19], list(desire[19].value) + [new_id])
    set_list(analyser.children[0], region_bel + [new_id])
    set_list(analyser.children[1], list(analyser.children[1].value) + [new_ai, new_id])
    print(f"border patrol: belief {new_id}; {moved_pts} of the parent's main-area points moved to the child; "
          f"neighbour points re-targeted {retargeted}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, nargs="+", choices=sorted(SPLITS) + ["batch1"],
                    help="splits to apply in order (batch1 = brittany normandy provence lyonnais burgundy andorra)")
    ap.add_argument("--regions-esf", type=Path, required=True)
    ap.add_argument("--startpos-esf", type=Path, required=True)
    ap.add_argument("--pathfinding-esf", type=Path,
                    help="give the child its own path id and border strip (S2); without it pathfinding is untouched")
    ap.add_argument("--copy-from", type=Path, help="copy pathfinding.esf (without S2) / *.pack from this build dir")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    names = [x for n in a.split for x in (BATCH1 if n == "batch1" else [n])]
    a.out.mkdir(parents=True, exist_ok=True)
    rr = ESFReader(a.regions_esf)
    reg = rr.read_root()
    sr = ESFReader(a.startpos_esf)
    sp = sr.read_root()
    ids = IdPool(all_ints(sp))
    pr = pf = None
    if a.pathfinding_esf:
        pr = ESFReader(a.pathfinding_esf)
        pf = pr.read_root()
    for name in names:
        spec = SPLITS[name]
        print()
        print(f"== split {spec['parent']} -> {spec['child']} ==")
        info = split_regions_esf(reg, spec)
        split_startpos(sp, spec, info, ids)
        if pf is not None:
            split_pathfinding(pf, sp, spec, info)
    outputs = [(a.out / "regions.esf", rr, reg), (a.out / "startpos.esf", sr, sp)]
    if pf is not None:
        outputs.append((a.out / "pathfinding.esf", pr, pf))
    for path, reader, root in outputs:
        path.write_bytes(ESFWriter(root, reader.tag_names, reader.timestamp).to_bytes())
        print(f"wrote {path} ({path.stat().st_size:,} B), round-trip {'OK' if roundtrip_ok(path) else 'MISMATCH'}")
    if a.copy_from:
        import shutil
        for f in ([] if a.pathfinding_esf else [a.copy_from / "pathfinding.esf"]) + list(a.copy_from.glob("*.pack")):
            if f.exists():
                shutil.copy2(f, a.out / f.name)
                print(f"copied {f.name}")


if __name__ == "__main__":
    main()
