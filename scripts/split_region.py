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

import shapely
from shapely.geometry import LinearRing, LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree
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
    # Batch 2, the Ottoman lands (approved 2026-10-08 on the review map): polygons in map
    # coordinates, projected from c.1700 eyalet lines through the game's own city positions
    # (out/split_planning_ottoman). Crete and Cyprus are island areas moved by add_region.py.
    "albania": dict(parent="greece", child="albania", sub_areas="cover", child_side=(140.4, 293.6), region=[(130.00, 300.00), (148.00, 300.00), (148.00, 290.00), (146.20, 287.30), (144.50, 286.60), (130.00, 286.00)]),
    "aleppo": dict(parent="syria", child="aleppo", sub_areas="cover", child_side=(277.1, 257.9), region=[(243.71, 249.24), (243.73, 252.27), (243.90, 254.48), (244.28, 257.31), (245.29, 263.21), (245.63, 266.28), (245.76, 269.92), (245.59, 275.59), (253.85, 275.43), (267.83, 275.51), (272.41, 275.39), (280.70, 275.00), (284.73, 274.96), (290.60, 275.25), (301.91, 276.31), (302.14, 270.77), (302.19, 267.19), (302.09, 263.42), (301.85, 259.44), (298.79, 256.08), (294.04, 250.62), (291.47, 250.24), (288.06, 249.97), (277.72, 249.43), (261.98, 248.86), (251.39, 249.20)]),
    "macedonia": dict(parent="rumelia", child="macedonia", sub_areas="cover", child_side=(152.3, 297.6), region=[(140.00, 312.00), (167.30, 312.00), (167.30, 289.80), (169.50, 289.00), (177.00, 286.00), (177.00, 278.00), (140.00, 278.00)]),
    # everything north of the Danube: the river areas of `all` plus seeds (Bucharest, Craiova)
    "wallachia": dict(parent="bulgaria", child="wallachia", sub_areas="cover", child_side=(186.6, 317.2), river_areas=[94, 95, 98],
                      seeds=[(186.6, 317.2), (169.5, 316.5)]),
    "hudavendigar": dict(parent="anatolia", child="hudavendigar", sub_areas="cover", child_side=(206.8, 285.7), region=[(179.66, 280.39), (180.43, 284.51), (183.04, 296.48), (186.22, 296.09), (187.36, 296.07), (188.58, 296.16), (190.65, 296.62), (196.02, 298.33), (198.09, 298.90), (200.43, 299.36), (202.50, 299.55), (204.33, 299.53), (207.12, 299.32), (221.03, 297.71), (221.13, 292.76), (221.62, 284.01), (218.18, 278.87), (214.94, 278.90), (205.59, 278.78), (191.95, 279.05), (188.89, 279.22), (186.06, 279.49)]),
    "aydin": dict(parent="anatolia", child="aydin", sub_areas="cover", child_side=(190.5, 272.6), region=[(180.94, 255.36), (179.65, 266.01), (179.19, 271.56), (179.14, 274.07), (179.18, 275.86), (179.36, 278.15), (179.66, 280.39), (186.06, 279.49), (188.89, 279.22), (191.95, 279.05), (205.59, 278.78), (214.94, 278.90), (218.18, 278.87), (217.72, 275.57), (216.95, 271.79), (213.16, 265.71), (211.30, 260.62), (209.36, 254.73), (202.54, 254.66), (197.25, 254.72)]),
    "karaman": dict(parent="anatolia", child="karaman", sub_areas="cover", child_side=(230.7, 268.7), region=[(204.00, 248.00), (205.50, 257.50), (212.50, 271.50), (217.00, 272.60), (226.25, 275.75), (243.25, 274.50), (251.80, 275.60), (252.20, 274.30), (248.40, 265.20), (247.20, 261.00), (247.40, 248.00)]),
    "adana": dict(parent="anatolia", child="adana", sub_areas="cover", child_side=(251.9, 262.9), region=[(237.94, 254.76), (238.35, 256.74), (239.00, 259.32), (240.56, 264.23), (245.13, 265.35), (249.38, 266.27), (262.66, 268.81), (271.13, 264.68), (271.79, 262.96), (272.24, 261.16), (272.37, 259.89), (272.31, 258.56), (272.01, 257.13), (271.22, 254.84), (254.94, 255.15)]),
    # the Pontic coast, cut off from the interior by Anatolia's mountain sub-area (found by a
    # point in it), closed at its west end by one cut
    "trebizond": dict(parent="anatolia", child="trebizond", sub_areas="cover", child_side=(279.7, 290.0), barrier_points=[(278.79, 286.59)],
                      seeds=[(279.7, 290.0)], cuts=[[(266.5, 297.0), (266.5, 285.5)]]),
    "erzurum": dict(parent="anatolia", child="erzurum", sub_areas="cover", child_side=(293.2, 282.8), region=[(266.00, 306.00), (266.00, 290.00), (270.00, 283.00), (276.00, 276.00), (281.00, 270.00), (283.00, 258.00), (340.00, 258.00), (340.00, 306.00)]),
    "mosul": dict(parent="mesopotamia", child="mosul", sub_areas="cover", child_side=(305.4, 256.6), region=[(289.35, 245.16), (290.08, 248.92), (290.41, 251.91), (290.49, 254.12), (290.45, 255.57), (290.13, 258.44), (289.52, 261.25), (288.65, 264.01), (287.26, 267.41), (285.33, 271.46), (290.02, 271.40), (294.34, 271.66), (298.96, 272.22), (306.78, 273.47), (309.64, 273.86), (312.57, 274.11), (315.54, 274.17), (318.52, 274.03), (322.27, 273.62), (339.18, 270.99), (339.32, 253.71), (339.52, 248.75), (339.84, 244.74), (333.88, 244.91), (328.62, 244.73), (324.14, 244.31), (316.17, 243.15), (313.72, 243.00), (311.49, 243.22), (306.92, 244.33), (304.54, 244.65), (302.33, 244.74), (295.01, 244.72), (292.46, 244.86)]),
    "basra": dict(parent="mesopotamia", child="basra", sub_areas="cover", child_side=(343.6, 211.5), region=[(318.46, 197.31), (322.69, 211.94), (325.73, 221.80), (328.80, 223.59), (331.77, 225.18), (334.57, 226.54), (348.92, 223.20), (354.86, 222.00), (360.80, 220.92), (360.66, 209.83), (360.06, 193.18), (332.79, 196.19), (325.61, 196.83)]),
}
BATCH1 = ["brittany", "normandy", "provence", "lyonnais", "burgundy", "andorra"]
BATCH2 = ['albania', 'aleppo', 'macedonia', 'wallachia', 'hudavendigar', 'aydin', 'karaman', 'adana', 'trebizond', 'erzurum', 'mosul', 'basra']


# ─── regions.esf ──────────────────────────────────────────────────────────────

SLIVER_W = 0.6             # land narrower than 2 x this between the drawn line and a river ...
SLIVER_LEN = 0.8           # ... and at least this long goes to the region on its bank
INNER_MARGIN = 0.3         # a cut keeps this far off a sub-area inside the main ring
GRID = 1e-4                # overlay snap-rounding grid: vertices are float32 (~3e-5 apart at y 335),
                           # and a river ring running along the coast leaves zero-width spikes


def _areal(g):
    """The polygonal part of an overlay result (snap-rounding can leave collapsed lines)."""
    ps = [p for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
    return shapely.union_all(ps) if ps else Polygon()


def plan_split(ring_poly, rivers: dict, near_rivers, side, sliver_w: float = SLIVER_W):
    """The child's and the parent's land, with every river the border follows on its bank
    (deep_dive 10.15). `rivers`: {area: polygon} of the river areas inside the parent's ring
    (interior rivers); `near_rivers`: every river polygon around (border rivers too); `side`:
    the child's side of the drawn line. A river area the side straddles becomes a border
    river, in neither region (vanilla: a river on a border lies outside both rings); land
    left between the drawn line and a river, narrower than 2 x SLIVER_W, goes to the region
    on that bank (`sliver_w`: a spec's `snap` widens it where a line is drawn further off a
    river, as along the Danube). Returns (child, parent, converted river areas)."""
    conv = sorted(ai for ai, rp in rivers.items() if 1e-6 < side.intersection(rp).area / rp.area < 1 - 1e-6)
    rv = shapely.union_all([rivers[ai] for ai in conv], grid_size=GRID) if conv else Polygon()
    land = _areal(ring_poly.difference(rv, grid_size=GRID))
    child = _areal(land.intersection(side, grid_size=GRID))
    parent = _areal(land.difference(side, grid_size=GRID))
    assert child.area > 1e-6 and parent.area > 1e-6,         f"the drawn side takes {'none' if child.area <= 1e-6 else 'all'} of the parent's main area"
    wet = unary_union([near_rivers, rv])

    def polys(g):
        return sorted([p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and p.area > 1e-9],
                      key=lambda p: -p.area)

    def slivers(x, other):
        """Pieces of x cut off by a river, or thin strips of x between a river and `other`."""
        parts = polys(x)
        out = list(parts[1:])                          # cut off from x's main body
        main = parts[0]
        thin = main.difference(main.buffer(-sliver_w, join_style=2).buffer(sliver_w, join_style=2))
        for t in polys(thin):
            if (t.length / 2 >= SLIVER_LEN and t.distance(wet) < 1e-6
                    and t.boundary.intersection(other.buffer(1e-6)).length > 0.5):
                # a neck, not a sliver: moving it would cut x in two (Normandy's land around
                # the end of the Seine joins its two banks)
                rest = polys(main.difference(t))
                if len(rest) > 1 and sum(r.area for r in rest[1:]) > t.area:
                    continue
                out.append(t)
        return [s for s in out if s.boundary.intersection(other.buffer(1e-6)).length > 1e-3]

    for _ in range(3):
        to_p, to_c = slivers(child, parent), slivers(parent, child)
        if not to_p and not to_c:
            break
        moved_c = _areal(shapely.union_all(to_p, grid_size=GRID)) if to_p else Polygon()
        moved_p = _areal(shapely.union_all(to_c, grid_size=GRID)) if to_c else Polygon()
        child = _areal(shapely.union_all([_areal(child.difference(moved_c, grid_size=GRID)), moved_p], grid_size=GRID))
        parent = _areal(shapely.union_all([_areal(parent.difference(moved_p, grid_size=GRID)), moved_c], grid_size=GRID))
    def one(g):
        """A single polygon when g is one piece (snap-rounding may return a one-part
        collection); holes of no area dropped."""
        ps = polys(g)
        if len(ps) == 1 or (ps and sum(p.area for p in ps[1:]) < 1e-6):
            p = ps[0]
            return Polygon(p.exterior, [h for h in p.interiors if Polygon(h).area > 1e-6])
        return g
    return one(child), one(parent), conv


def river_side(land, rivers: list, seeds: list, gap: float = 1.5, cuts: list = ()):
    """The child's side when its border is a river or another barrier (a spec's `river`:
    area polygons, e.g. river areas of `all` or a mountain sub-area, and `seeds` inside the
    child): the parent's land less the barriers, short land gaps between consecutive barrier
    pieces bridged, extra `cuts` (polylines) added, keeping the pieces holding a seed. The
    barriers must reach the parent's outline (or each other) for the side to close."""
    from shapely.ops import nearest_points
    rs = [g for r in rivers for g in getattr(r, "geoms", [r]) if g.geom_type == "Polygon"]
    extra_cuts, cuts = list(cuts), []
    for i in range(len(rs)):
        for j in range(i + 1, len(rs)):
            if 0 < rs[i].distance(rs[j]) < gap:
                a, b = nearest_points(rs[i], rs[j])
                cuts.append(LineString([a, b]).buffer(0.002))
    cuts += [LineString(c).buffer(0.002) for c in extra_cuts]
    rest = land.difference(unary_union(rs + cuts))
    keep = [g for g in getattr(rest, "geoms", [rest]) if g.geom_type == "Polygon"
            and any(g.covers(Point(s)) for s in seeds)]
    assert keep, "no piece of the parent's land holds a seed"
    return unary_union(keep).buffer(0.005, join_style=2)


def sub_area_to_child(a, side, child, parent) -> bool:
    """A sub-area (mountain zone beside the main ring) goes to the child when the drawn side
    covers most of it, else to the nearer piece (a zone touching both is not a tie)."""
    cov = a.intersection(side).area / a.area if a.area else 0.0
    if cov > 0.5:
        return True
    if cov > 0.05:
        return False
    return child.distance(a) < parent.distance(a)


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
    # the mainland: the largest area (not always area 0: Anatolia's first areas are islands)
    mi = max(range(len(p_areas)), key=lambda k: m.area_polygon(pi, k).area)
    main = p_areas[mi]
    assert main[2].value, f"parent area {mi} is not a main area"
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

    # the child's side of the drawn line; a region polygon is grown a little, so an edge drawn
    # along an earlier child's border lies inside that (already split off) territory
    if "region" in spec:
        side = Polygon(spec["region"]).buffer(REGION_GROW, join_style=2)
    elif "seeds" in spec:
        # a border that is a river or a mountain zone: the parent's land less the barriers
        barriers = [m.area_polygon(riv, a) for a in spec.get("river_areas", [])]
        for pt in spec.get("barrier_points", []):
            k = next(k for k in range(len(p_areas)) if m.area_polygon(pi, k).covers(Point(pt)))
            barriers.append(m.area_polygon(pi, k))
        side = river_side(ring_poly, barriers, spec["seeds"], cuts=spec.get("cuts", ()))
    else:
        side = side_polygon(spec["cut"], spec["child_side"])
    # a sub-area INSIDE the main ring (Syria's inland mountain zone: the main area has open
    # outlines along its edge, as along an interior river) moves whole, and a ring edge may
    # not double as such an outline: the line keeps INNER_MARGIN off it, on the side that
    # covers most of it
    for k in range(len(p_areas)):
        a = m.area_polygon(pi, k)
        if k == mi or a.is_empty or ring_poly.intersection(a).area <= 0.5 * a.area:
            continue
        cov = side.intersection(a).area / a.area
        if 1e-6 < cov < 1 - 1e-6 or side.boundary.distance(a) < INNER_MARGIN:
            grown = a.buffer(INNER_MARGIN, join_style=2)
            side = side.union(grown) if cov >= 0.5 else side.difference(grown)
            print(f"  inner sub-area {k} ({a.area:.1f}) kept whole on the {'child' if cov >= 0.5 else 'parent'}'s side")
    near_r =unary_union([rp for rp in (m.area_polygon(riv, ai) for ai in range(len(m.areas(riv))))
                          if not rp.is_empty and rp.intersects(ring_poly.buffer(1.0))])
    child, parent, conv = plan_split(ring_poly, rivers, near_r, side, spec.get("snap", SLIVER_W))
    for nm, g in (("child", child), ("parent", parent)):
        parts = [(round(x.area, 6), tuple(round(c, 3) for c in x.representative_point().coords[0]),
                  [round(Polygon(h).area, 6) for h in getattr(x, "interiors", [])]) for x in getattr(g, "geoms", [g])]
        assert g.geom_type == "Polygon" and not g.interiors, \
            f"{spec['child']}: the {nm} is not one piece without holes (a river right across it?): parts {parts}"

    # rings over three vertex sources: the parent's ring, the converted rivers' rings, new
    # points. A new point on an existing edge (the coast where the line meets it, a river
    # bank where a land border meets the water) is spliced into every outline with that edge.
    conv_ols = [ol for ai in conv for ol in m.areas(riv)[ai][7].children]
    known = {}
    for v in R + [v for ol in conv_ols for v in ol[3].value]:
        known.setdefault(m.V(v), v)
    seg_ids = [(R[k], R[(k + 1) % len(R)]) for k in range(len(R))]
    seg_ids += [e for ol in conv_ols for e in Mesh.edges(list(ol[3].value), ol[0].value)]
    seg_tree = STRtree([LineString([m.V(a), m.V(b)]) for a, b in seg_ids])
    splices: dict[frozenset, list] = {}               # undirected edge -> [new vertex]
    new_vs: set[int] = set()

    def lookup(pt):
        """The existing vertex within 1.5 GRID of pt (the nearest), else pt itself."""
        pt = (float(pt[0]), float(pt[1]))
        if pt in known:
            return known[pt]
        near = [(math.dist(q, pt), v) for q, v in known.items()
                if abs(q[0] - pt[0]) < 1.5 * GRID and abs(q[1] - pt[1]) < 1.5 * GRID]
        return min(near)[1] if near else pt

    def vid(key):
        if isinstance(key, int):
            return key
        v = m.add_vertex(*key)
        known[key] = known[m.V(v)] = v
        new_vs.add(v)
        P = Point(key)
        on = [k for k in seg_tree.query(P.buffer(2 * GRID)) if seg_tree.geometries[k].distance(P) < 2 * GRID]
        if on:
            k = min(on, key=lambda k: seg_tree.geometries[k].distance(P))
            splices.setdefault(frozenset(seg_ids[k]), []).append(v)
        return v

    r_ccw = LinearRing([m.V(i) for i in R]).is_ccw

    def ring_of(g):
        g = orient(g, 1.0 if r_ccw else -1.0)
        keys = []
        for pt in list(g.exterior.coords)[:-1]:
            k = lookup(pt)
            if not keys or keys[-1] != k:
                keys.append(k)
        while len(keys) > 1 and keys[0] == keys[-1]:
            keys.pop()
        changed = True
        while changed:                       # zero-width spikes: a -> b -> a
            changed = False
            for i in range(len(keys)):
                if len(keys) > 3 and keys[i - 1] == keys[(i + 1) % len(keys)]:
                    del keys[i]
                    del keys[i % len(keys)]
                    changed = True
                    break
        return [vid(k) for k in keys]

    old_edges = {frozenset(e) for _, _, ol in m.outlines() for e in Mesh.edges(list(ol[3].value), ol[0].value)}
    child_ring, parent_ring = ring_of(child), ring_of(parent)
    lost = set(R) - set(child_ring) - set(parent_ring)
    assert not lost, (f"{spec['child']}: {len(lost)} vertices of the parent's ring are in neither new ring "
                      f"(snapped together?): {[tuple(round(c, 4) for c in m.V(v)) for v in list(lost)[:4]]}")
    # splice: walk every outline once, inserting along each spliced edge in order from its start
    spliced = 0
    for ri, ai, ol in m.outlines():
        vs = list(ol[3].value)
        if not any(frozenset(e) in splices for e in Mesh.edges(vs, ol[0].value)):
            continue
        out = []
        for a_, b_ in Mesh.edges(vs, ol[0].value):
            out.append(a_)
            ins = splices.get(frozenset((a_, b_)))
            if ins:
                A = m.V(a_)
                out += sorted(ins, key=lambda v: math.dist(A, m.V(v)))
                spliced += 1
        if not ol[0].value:
            out.append(vs[-1])
        m.set_outline(ol, out)
    for nm, ring in (("child", child_ring), ("parent", parent_ring)):
        rp_ = Polygon([m.V(i) for i in ring])
        if not rp_.is_valid:
            from shapely.validation import explain_validity
            raise AssertionError(f"{spec['child']}: new {nm} ring is not a valid polygon: {explain_validity(rp_)}")
    # the child's border with the parent: its ring away from the old parent ring (one stretch)
    # the child's border with the parent: the run of its ring edges that are not the old
    # ring's (or pieces of them, where a point was spliced in); one run, as a straight cut
    # between two spliced points (Trebizond) has no new vertex at all
    old_r = set()
    for k in range(len(R)):
        e = frozenset((R[k], R[(k + 1) % len(R)]))
        if e in splices:
            a_, b_ = R[k], R[(k + 1) % len(R)]
            seq = [a_] + sorted(splices[e], key=lambda v: math.dist(m.V(a_), m.V(v))) + [b_]
            old_r |= {frozenset(x) for x in zip(seq, seq[1:])}
        else:
            old_r.add(e)
    n = len(child_ring)
    new_e = [frozenset((child_ring[k], child_ring[(k + 1) % n])) not in old_r for k in range(n)]
    starts = [k for k in range(n) if new_e[k] and not new_e[k - 1]]
    assert len(starts) == 1, (f"{spec['child']}: the border meets the parent's ring {len(starts)} times; "
                              f"stretches start at {[tuple(round(c, 2) for c in m.V(child_ring[k])) for k in starts]}")
    k = starts[0]
    border_v = [child_ring[k]]
    while new_e[k]:
        k = (k + 1) % n
        border_v.append(child_ring[k])
    river_edges = {frozenset(e) for ol in conv_ols for e in Mesh.edges(list(ol[3].value), ol[0].value)}
    land_lines, cur = [], []
    for a_, b_ in zip(border_v, border_v[1:]):
        if frozenset((a_, b_)) in river_edges:
            if len(cur) > 1:
                land_lines.append(cur)
            cur = []
        else:
            cur = (cur or [m.V(a_)]) + [m.V(b_)]
    if len(cur) > 1:
        land_lines.append(cur)
    conv_banks = [ol for ol in banks if any(frozenset(e) in river_edges for e in Mesh.edges(list(ol[3].value), False))]
    banks = [ol for ol in banks if ol not in conv_banks]

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
    # batch 2 decides by how much of a zone the drawn side covers (a zone touching both
    # pieces is no tie: Trebizond's Pontic range, Karaman's Taurus); batch 1 keeps the
    # nearer-piece rule its in-game build was tested with
    if spec.get("sub_areas") == "cover":
        moved = [k for k in range(len(p_areas))
                 if k != mi and sub_area_to_child(m.area_polygon(pi, k), side, cpoly, ppoly)]
    else:
        moved = [k for k in range(len(p_areas))
                 if k != mi and cpoly.distance(m.area_polygon(pi, k)) < ppoly.distance(m.area_polygon(pi, k))]
    keep = [k for k in range(len(p_areas)) if k not in moved]
    pm = keep.index(mi)                                       # the parent's main area, renumbered
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
        touches = ri in (pi, ci) or any(r[0] in old_parent for r in stored) or bool(new_vs & set(vs))
        if not touches:
            continue
        if id(ol) in odd:
            assert not new_vs & set(vs), "an outline the split cuts does not regenerate exactly"
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
          f"vertices ({sum(len(x) for x in land_lines)} on land); edges spliced {spliced}; river banks {len(c_banks)} "
          f"to child, {len(p_banks)} kept, {len(conv_banks)} dropped; rivers made border rivers {conv}; "
          f"connectivity regenerated on {regen} outlines, patched on {fallback}")

    # ── quadtree ──
    split_edges, pieces_set = {}, set()
    for e, ins in splices.items():
        a_, b_ = tuple(e)
        seq = [a_] + sorted(ins, key=lambda v: math.dist(m.V(a_), m.V(v))) + [b_]
        split_edges[(a_, b_)] = list(zip(seq, seq[1:]))
        pieces_set |= {frozenset(x) for x in split_edges[(a_, b_)]}
    border_edges, seen_e = [], set()
    for ring in (child_ring, parent_ring):
        for e in Mesh.edges(ring, True):
            f = frozenset(e)
            if f not in old_edges and f not in pieces_set and f not in seen_e:
                border_edges.append(e)
                seen_e.add(f)
    child_areas_poly = {0: cpoly} | {i: m.area_polygon(ci, i) for i in range(1, 1 + len(moved))}
    parent_areas_poly = {i: m.area_polygon(pi, i) for i in range(len(keep))}

    def locate_old(pt, old_tag):
        """New tag for a point that belonged to one of the parent's old areas."""
        if old_tag in remap:
            return remap[old_tag]
        if old_tag == packed(pi, mi):
            return packed(ci, 0) if cpoly.contains(Point(pt)) else packed(pi, pm)
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
            if d == packed(pi, mi) or d in remap:
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
    main_group = [k for k in range(len(old_areas)) if old_face[k] == old_face[mi]]
    c_group = [0] + [moved.index(k) + 1 for k in main_group if k in moved]
    p_group = [keep.index(k) for k in main_group if k in keep]
    c_rivers = [rp for ai, rp in rivers.items() if ai not in conv and cpoly.contains(rp.representative_point())]
    p_rivers = [rp for ai, rp in rivers.items() if ai not in conv and not cpoly.contains(rp.representative_point())]
    tc = group_faces(ci, c_group, c_rivers)
    tp = group_faces(pi, p_group, p_rivers)
    # the border rivers' own faces (their rings gained the spliced vertices); areas sharing
    # one face list are triangulated together
    r_face = {k: tuple(a[6].children[0].value) for k, a in enumerate(m.areas(riv))}
    tr = 0
    for ai in conv:
        tr += group_faces(riv, [k for k in r_face if r_face[k] == r_face[ai]], [])
    print(f"  faces: {spec['child']} areas {c_group} {tc} triangles; {spec['parent']} areas {p_group} {tp} triangles; "
          f"border rivers {conv} {tr}")
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
            "side": side, "border_land": land_lines, "main_old": mi, "parent_main": pm,
            # where pathfinding records count as the child's: its side of the line, less the
            # parent's land (its bank of a river), plus the child's land
            "child_half": unary_union([side.difference(ppoly), cpoly]),
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
    from etwpc.compiler.coastal import (CellView, commit_plan, check_cells, check_nodes, CELL, ring_of,
                                        restore_fields, stored_fields)
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
    child_half = info["child_half"]
    # the strip runs only where the two regions' land meets: along a border river each
    # region's land touches the river's own records, as vanilla's (deep_dive 10.15)
    land = [LineString(x) for x in info["border_land"]]
    strip = unary_union(land).buffer(STRIP_HALF) if land else Polygon()
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
    # cells beside the carve get their fields recomputed; vanilla's few cells the rules do
    # not reproduce (r105 c95 by the Seine) keep their stored values
    odd_cells = stored_fields(view, [rc for rc in around if (rc[0], rc[1]) not in plan])
    # The invariant check fails only on problems the carve adds: vanilla itself has
    # T-junctions (r105 c95 by the Seine, once under a saved unit zone).
    import re
    norm = lambda msgs: {re.sub(r" rec \d+", "", m) for m in msgs}
    carved = [rc for rc, out in plan.items() if any(o is None for *_, o in out)]
    before_inv = norm(check_cells(view, around) + check_nodes(view, carved))
    if run_specs:
        g._apply(run_specs)
        view.cache.clear()
        # the plan's original Rec objects came from the old cache: re-read nothing, they stay valid
    res = commit_plan(view, plan, extra)
    odd_skipped = restore_fields(view, odd_cells)
    assert not odd_skipped, f"odd vanilla cells beside the border changed shape: {odd_skipped}"
    view.cache.clear()
    bad = norm(check_cells(view, around) + check_nodes(view, carved)) - before_inv
    assert not bad, f"carved cells break a vanilla invariant: {sorted(bad)[:3]}"
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
    new_main = clone_item(hl_node, hl[p_h[info["main_old"]]], ids.cai())
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
        if a == info["main_old"]:
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
                (take_p if cpoly.buffer(0.8).contains(Point(x, y)) and not info["parent_areas_poly"][info["parent_main"]].contains(Point(x, y)) else keep_p).append(pt)
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
                for lst, face, poly in ((c_list, p_ai, cpoly), (p_list, new_ai, info["parent_areas_poly"][info["parent_main"]])):
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
                    and not info["parent_areas_poly"][info["parent_main"]].buffer(0.5).contains(Point(pt.children[0].value / FIXED, pt.children[1].value / FIXED)):
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
    ap.add_argument("--split", required=True, nargs="+", choices=sorted(SPLITS) + ["batch1", "batch2"],
                    help="splits to apply in order (batch1 = brittany normandy provence lyonnais burgundy andorra)")
    ap.add_argument("--regions-esf", type=Path, required=True)
    ap.add_argument("--startpos-esf", type=Path, required=True)
    ap.add_argument("--pathfinding-esf", type=Path,
                    help="give the child its own path id and border strip (S2); without it pathfinding is untouched")
    ap.add_argument("--copy-from", type=Path, help="copy pathfinding.esf (without S2) / *.pack from this build dir")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    names = [x for n in a.split for x in {"batch1": BATCH1, "batch2": BATCH2}.get(n, [n])]
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
