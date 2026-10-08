"""
Campaign-map region border lines (deep_dive 10.13).

The lines the game draws along region borders are not derived from regions.esf at run time:
each region has precomputed spline models in models.pack,
``rigidmodels\\campaignborders\\<region>.rigid_spline`` or ``<region>_1 .. _n.rigid_spline``,
one polyline per land-border run of the region's outline (coasts, impassable land and
river stretches break a run; a landlocked region is one closed ring). Each region draws
its own runs in its owner's colour, which is why a border shows two parallel lines.
patch2.pack ships regenerated splines for the regions CA reshaped. The loader takes
``<region>.rigid_spline`` alone if it exists, else ``_1``, ``_2`` ... while they exist.

File: b"SPLN", u32 1, u32 1, u16 n + UTF-16 name ("border:France:1"), u32 1, u32 count,
count x (x, height, z) float32, cubic Bezier control points (count = 3k + 1). All 1,211
vanilla files have that header and one spline.
Map -> spline coordinates: x' = (x - TX) / SCALE, z' = (y - TY) / SCALE, height constant;
fitted on France, Spain and Alsace (mean distance 0.1 map units, about what vanilla's own
splines deviate from regions.esf).
"""

from __future__ import annotations

import math
import struct

from shapely import voronoi_polygons
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

SCALE = 39.462
TX, TY = -0.04, -0.75
HEIGHT = struct.unpack("<f", bytes.fromhex("783385b9"))[0]      # every vanilla point
PACK_DIR = "rigidmodels\\campaignborders\\"
# vanilla draws each region's line this far inside it: two neighbours' lines are 0.20 apart
# on every shared border measured (France/Spain, /Alsace, /Savoy, /Flanders, Spain/Portugal)
INSET = 0.1
MAX_GAP = 1.0          # longest edge between anchors (vanilla: median 0.5-0.8, max ~2.7)
MIN_GAP = 0.02         # closer outline vertices are merged
HANDLE_CAP = 0.5       # Bezier handle at most this fraction of its chord


def read_spln(data: bytes) -> dict:
    """Name and on-curve points (every third control point)."""
    assert data[:4] == b"SPLN", "not a rigid_spline"
    p = 12
    n = struct.unpack_from("<H", data, p)[0]
    p += 2
    name = data[p:p + 2 * n].decode("utf-16-le")
    p += 2 * n
    _, count = struct.unpack_from("<2I", data, p)
    p += 8
    pts = [struct.unpack_from("<3f", data, p + 12 * i) for i in range(0, count, 3)]
    return {"name": name, "points": [((x * SCALE) + TX, (z * SCALE) + TY) for x, _, z in pts]}


def bezier_controls(points: list[tuple[float, float]], closed: bool = False) -> list[tuple[float, float]]:
    """Catmull-Rom through the points as cubic Bezier controls (anchor, control, control,
    anchor ...), as vanilla's (tangent-continuous, handles a third of the chord). Each handle
    is capped at HANDLE_CAP of its own chord, so a short edge beside a long one cannot loop."""
    P = points
    n = len(P)
    if closed:                       # P[0] == P[-1]
        prev = lambda i: P[i - 1] if i > 0 else P[-2]
        nxt = lambda i: P[i + 1] if i < n - 1 else P[1]
    else:
        prev = lambda i: P[i - 1] if i > 0 else (2 * P[0][0] - P[1][0], 2 * P[0][1] - P[1][1])
        nxt = lambda i: P[i + 1] if i < n - 1 else (2 * P[-1][0] - P[-2][0], 2 * P[-1][1] - P[-2][1])
    tan = [((nxt(i)[0] - prev(i)[0]) / 2, (nxt(i)[1] - prev(i)[1]) / 2) for i in range(n)]

    def handle(t, chord):
        tl = math.hypot(*t)
        k = min(1 / 3, HANDLE_CAP * chord / tl) if tl > 1e-12 else 0.0
        return t[0] * k, t[1] * k

    ctrl = [P[0]]
    for i in range(n - 1):
        chord = math.dist(P[i], P[i + 1])
        h0, h1 = handle(tan[i], chord), handle(tan[i + 1], chord)
        ctrl += [(P[i][0] + h0[0], P[i][1] + h0[1]), (P[i + 1][0] - h1[0], P[i + 1][1] - h1[1]), P[i + 1]]
    return ctrl


def write_spln(name: str, points: list[tuple[float, float]], closed: bool = False) -> bytes:
    """The file holds cubic Bezier control points: Empire.exe takes (count - 1) / 3
    segments (+0xc93b72) and loops ~2^32 times on a count below 4, reading past the file
    (dump 7768). count = 3 (len(points) - 1) + 1, like every vanilla file (3k + 1)."""
    assert len(points) >= 2, "a border spline needs at least one edge"
    ctrl = bezier_controls(points, closed)
    out = bytearray(b"SPLN") + struct.pack("<2I", 1, 1)
    out += struct.pack("<H", len(name)) + name.encode("utf-16-le")
    out += struct.pack("<2I", 1, len(ctrl))
    for x, y in ctrl:
        out += struct.pack("<3f", (x - TX) / SCALE, HEIGHT, (y - TY) / SCALE)
    return bytes(out)


def region_polygon(mesh, ri: int):
    """Union of a region's areas (closed outlines) from regions.esf."""
    parts = []
    for a in mesh.areas(ri):
        for ol in a[7].children:
            if ol[0].value:
                parts.append(Polygon([mesh.V(v) for v in ol[3].value]).buffer(0))
    return unary_union(parts)


LOOP_GAP = 0.6         # a river loop's two banks meet within this (map units)
LOOP_RATIO = 4.0       # ... after a path at least this many times longer
# A split keeps a river it runs along whole, with a margin each side, in one region
# (split_region RIVER_MARGIN): a strip ~1 unit wide between the neighbours. Vanilla has none:
# a river on a border lies in one region and the other's outline runs along its bank (the
# Rhone between France and Savoy). virtual_polygons redraws the map that way for the lines
# only. A strip is what an opening of CORRIDOR_W removes, with a river over at least
# CORRIDOR_RIVER of it.
CORRIDOR_W = 0.6
CORRIDOR_RIVER = 0.15


def _skip_loops(c, n, k, j, skip) -> None:
    """Within ring edges k..j-1 (a river stretch), mark edges to step over where the path
    returns within LOOP_GAP of an earlier point after a much longer detour."""
    idx = [t % n for t in range(k, j + 1)]
    pts = [c[t] for t in idx]
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + Point(a).distance(Point(b)))
    i = 0
    while i < len(pts) - 2:
        far = None
        for q in range(len(pts) - 1, i + 1, -1):
            d = Point(pts[i]).distance(Point(pts[q]))
            if d < LOOP_GAP and cum[q] - cum[i] > LOOP_RATIO * max(d, 1e-6):
                far = q
                break
        if far is None:
            i += 1
            continue
        for e in range(i, far - 1):           # edges i .. far-2 add no vertex; far-1 adds pts[far]
            skip[idx[e]] = True
        i = far


def _nearest_split(comp, sites: list, step: float = 0.05) -> list:
    """[(label, part of comp nearer that label's outline than any other)] for sites
    [(label, outline)], earlier sites winning ties (a Voronoi of the outlines' points)."""
    if len(sites) == 1:
        return [(sites[0][0], comp)]
    seen = {}
    zone = comp.buffer(2.0)
    for label, outline in sites:
        b = outline.intersection(zone).segmentize(step)
        for g in getattr(b, "geoms", [b]):
            for c in getattr(g, "coords", []):
                seen.setdefault((round(c[0], 6), round(c[1], 6)), label)
    pts = list(seen)
    if len(pts) < 2:
        return [(sites[0][0], comp)]
    cells = voronoi_polygons(MultiPoint(pts), extend_to=zone, ordered=True)
    owner = {}
    for c, cell in zip(pts, cells.geoms):
        owner.setdefault(seen[c], []).append(cell)
    out = []
    for label, cs in owner.items():
        piece = comp.intersection(unary_union(cs))
        piece = unary_union([g for g in getattr(piece, "geoms", [piece]) if g.geom_type == "Polygon"])
        if not piece.is_empty and piece.area > 1e-4:
            out.append((label, piece))
    return out


def _sharing(comp, x: int, ids: list[int], polys: dict) -> list[int]:
    """Regions in ids (not x) sharing more than 0.05 units of outline with comp."""
    out = []
    for y in ids:
        if y != x and not polys[y].is_empty and polys[y].distance(comp) < 1e-3:
            if polys[y].boundary.intersection(comp.buffer(1e-4)).length > 0.05:
                out.append(y)
    return out


def _give(polys: dict, y: int, piece) -> None:
    polys[y] = unary_union([polys[y], piece]).buffer(1e-6).buffer(-1e-6)


def virtual_polygons(mesh, ids: list[int], rivers: set[int], polys: dict, families: list[set[int]]) -> list[str]:
    """Fill `polys` (the cache border_runs takes) with the regions `ids` as the lines should
    see them: a strip a split left in one region (see CORRIDOR_W) is dissolved, the margin
    on each bank going to the region on that side and the river to neither, so each bank
    shows its neighbour's line. (A margin on one bank only stays: see deep_dive 10.14.)
    Ownership is unchanged; only the drawn lines move (by up to RIVER_MARGIN). Only borders
    inside a split family (a parent and its children, `families`) are touched: a vanilla
    border may well run beside a river. Returns a report line per change."""
    kin = {}
    for fam in families:
        for i in fam:
            kin.setdefault(i, set()).update(fam - {i})
    for i in set(ids) | set(rivers):
        if i not in polys:
            polys[i] = region_polygon(mesh, i)
    rv = unary_union([polys[i] for i in rivers if not polys[i].is_empty])
    report = []
    for x in ids:                                           # (a) strips
        X = polys[x]
        if X.is_empty:
            continue
        thin = X.difference(X.buffer(-CORRIDOR_W, join_style=2).buffer(CORRIDOR_W, join_style=2))
        for s in getattr(thin, "geoms", [thin]):
            if s.area <= 0.05 or s.intersection(rv).area <= CORRIDOR_RIVER * s.area:
                continue
            given = []
            rest = s.difference(rv)
            for comp in getattr(rest, "geoms", [rest]):
                if comp.geom_type != "Polygon" or comp.area < 1e-4:
                    continue
                ys = _sharing(comp, x, sorted(kin.get(x, ())), polys)
                if not ys:
                    continue
                for y, piece in _nearest_split(comp, [(y, polys[y].boundary) for y in ys]):
                    _give(polys, y, piece)
                    X = X.difference(piece.buffer(1e-6))
                    given.append(mesh.name(y))
            if given:
                X = X.difference(s.intersection(rv).buffer(1e-6))
            report.append(f"{mesh.name(x)} strip {s.area:.1f} -> {', '.join(sorted(set(given))) or 'kept'}")
        polys[x] = X.buffer(0)
    return report


def border_runs(mesh, ri: int, land: set[int], rivers: set[int] = frozenset(), polys: dict | None = None,
                eps: float = 1e-3):
    """[(points, closed)] for region ri: the stretches of its outline (oriented with the
    interior on the left, as vanilla: France's Pyrenees run goes west to east) that face a
    land region (`land`: every land-type region, impassable and void ones included, as
    vanilla's Alsace ring runs past the Alps) or a river (`rivers`) lying between two such
    stretches; seas and lakes break a run, and so do river mouths on a coast. A ring facing
    land all the way round is one closed run (first point repeated)."""
    polys = polys if polys is not None else {}

    def poly(i):
        if i not in polys:
            polys[i] = region_polygon(mesh, i)
        return polys[i]

    R = poly(ri)
    if R.is_empty:
        return []
    probe = R.buffer(0.01)
    near = [poly(i) for i in land if i != ri and not poly(i).is_empty and poly(i).intersects(probe)]
    near_r = [poly(i) for i in rivers if not poly(i).is_empty and poly(i).intersects(probe)]
    if not near:
        # island regions draw nothing: vanilla gives Corsica, Ireland, Cuba ... a 4-point
        # placeholder far off the map, and a region with no file is tolerated
        return []
    N = unary_union(near).boundary
    NR = unary_union(near_r).boundary if near_r else None
    out = []
    for part in getattr(R, "geoms", [R]):
        part = orient(part, 1.0)
        for ring in [part.exterior] + list(part.interiors):
            c = list(ring.coords)[:-1]
            n = len(c)
            mids = [Point((c[k][0] + c[(k + 1) % n][0]) / 2, (c[k][1] + c[(k + 1) % n][1]) / 2) for k in range(n)]
            kind = ["L" if N.distance(p) < eps else "R" if NR is not None and NR.distance(p) < eps else "S"
                    for p in mids]
            # a river stretch continues a run when land lies on either side of it (a river
            # mouth between two coast stretches does not); where the outline runs up a river
            # into the region and back down the other bank (the Rhone entering France from
            # Savoy), the line steps straight across, as vanilla does
            flag = [k == "L" for k in kind]
            skip = [False] * n
            if "R" in kind and any(flag):
                k0 = next(k for k in range(n) if kind[k] != "R")
                k = k0
                for _ in range(n):
                    if kind[k] == "R" and kind[k - 1] != "R":
                        j = k
                        while kind[j % n] == "R":
                            j += 1
                        if kind[(k - 1) % n] == "L" or kind[j % n] == "L":
                            for t in range(k, j):
                                flag[t % n] = True
                            _skip_loops(c, n, k, j, skip)
                    k = (k + 1) % n
            if all(flag):
                ring_pts = [c[k] for k in range(n) if not skip[k - 1]]
                out.append((ring_pts + [ring_pts[0]], True))
                continue
            if not any(flag):
                continue
            start = next(k for k in range(n) if flag[k] and not flag[k - 1])
            k = start
            run = None
            for _ in range(n):
                if flag[k]:
                    if run is None:
                        run = [c[k]]
                    if not skip[k]:
                        run.append(c[(k + 1) % n])
                elif run is not None:
                    out.append((run, False))
                    run = None
                k = (k + 1) % n
            if run is not None:
                out.append((run, False))
    return out


def split_to(runs: list, k: int) -> list:
    """Split the longest open runs until there are at least k (a region must replace every
    vanilla _n file, or the loader still finds the old ones)."""
    runs = list(runs)
    while len(runs) < k:
        i = max(range(len(runs)), key=lambda j: LineString(runs[j][0]).length if len(runs[j][0]) > 1 else 0)
        pts, closed = runs[i]
        if len(pts) < 4:
            break
        mid = len(pts) // 2
        runs[i:i + 1] = [(pts[:mid + 1], False), (pts[mid:], False)]
    return runs


def line_anchors(pts: list, closed: bool, inset: float = INSET) -> list:
    """A run's on-curve points as vanilla places them: offset `inset` into the region (runs
    have the interior on their left), vertices closer than MIN_GAP merged, edges longer than
    MAX_GAP split evenly (so the curve stays straight along them and only rounds corners)."""
    if inset:
        line = LineString(pts)
        off = line.offset_curve(inset, join_style="mitre", mitre_limit=2.0)
        if off.geom_type == "MultiLineString":
            off = max(off.geoms, key=lambda g: g.length)
        # a run that doubles back within 2 x inset (a river stepped across) offsets badly
        if not off.is_empty and off.length > 0.5 * line.length:
            pts = list(off.coords)
            if closed and pts[0] != pts[-1]:
                pts.append(pts[0])
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) >= MIN_GAP:
            out.append(p)
    if closed:
        if math.dist(out[-1], out[0]) < MIN_GAP:
            out[-1] = out[0]
        else:
            out.append(out[0])
    if len(out) < 2:
        out = [pts[0], pts[-1]]
    dense = [out[0]]
    for a, b in zip(out, out[1:]):
        k = max(1, math.ceil(math.dist(a, b) / MAX_GAP))
        dense += [(a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k) for j in range(1, k + 1)]
    return dense


def spline_files(region: str, runs: list, display: str | None = None) -> dict[str, bytes]:
    """{pack path: file bytes}; one run -> <region>.rigid_spline, several -> _1 .. _n."""
    label = display or region
    if len(runs) == 1:
        pts, closed = runs[0]
        return {f"{PACK_DIR}{region}.rigid_spline": write_spln(f"border:{label}", line_anchors(pts, closed), closed)}
    return {f"{PACK_DIR}{region}_{i}.rigid_spline": write_spln(f"border:{label}:{i}", line_anchors(pts, closed), closed)
            for i, (pts, closed) in enumerate(runs, 1)}
