"""
Settlement footprints in coastal pathfinding cells (deep_dive 9.6-9.11).

footprint.py copies a vanilla footprint into a block of interior cells. Small islands
(the Canaries) have no interior cells: every land cell is a header cell whose records
partition it into land / sea / impassable polygons. Here the footprint is carved into
those records in place, and every derived field is recomputed from geometry.

What a vanilla footprint is (deep_dive 9.11, every vanilla settlement and slot):

* a settlement's slot outline (path_type 7) is exactly its regions.esf
  settlement_and_slots[1] polygon (137/137), and a town / resource / port slot's is
  exactly its slot[6] polygon (725/725), so the outline is carved from the same
  polygon patch_regions_esf writes;
* the outline replaces every land and impassable record under it, split per cell, and
  never borders a sea record (0 of 862 do);
* every vertex on a cell edge is also a vertex of the neighbouring cell's records
  (76,691 of 76,692 in grid 2; the exception is on the map border).

Cell record encoding (decoded 2026-10-04 on vanilla grids 1 and 2):

* a record is a closed polygon in cell-local units (cell = [0,2] x [0,2]); entries are
  vertex-table indices or corner markers E0 (0,0), E1 (0,2), E2 (2,0), E3 (2,2); vanilla
  rings run counter-clockwise.
* path_type classes: land 0/6/7, sea 1/4/5 (+7), dead 2/3 (pass 0, unknown2 0). A slot
  outline (7) belongs to both land and sea classes.
* passable_part = 8-neighbour connectivity: bit0 right edge, bit1 corner E1, bit2 top
  edge, bit3 corner E3, bit4 corner E0, bit5 bottom edge, bit6 corner E2, bit7 left edge.
* unknown2 = five nibbles [bottom, left, inner, right, top]. inner = bitmask over the
  ranks of the cell's records of the polygon's class (all live records for type 7) that
  share a boundary stretch with it. An edge nibble = the same kind of mask over the
  neighbour cell's records along the shared edge whose stretch overlaps this polygon's;
  1 when the neighbour is a run cell; 0 when the polygon does not touch the edge. Masks
  keep 4 bits. Reproduces 99.25% of grid 2's 31,126 live records (the misses are mostly
  types 4/5/6, which a footprint does not touch).
* the 8-byte cell header is terrain (run cells carry the same patterns, and the Pensa
  footprint cells keep their neighbours' 1f1f...), so an edited cell keeps its header.
"""

from __future__ import annotations

from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from etwpc.compiler.footprint import AreaGrid, CellSpec
from etwpc.io.esf_types import BoundaryEntry

CORNERS = {0: (0.0, 0.0), 1: (0.0, 2.0), 2: (2.0, 0.0), 3: (2.0, 2.0)}
DEAD = {2, 3}
SEA = {1, 4, 5}
LAND = {0, 6}
EPS = 1e-4
FIX = 1 << 20
CELL = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])

# edge nibble index -> (neighbour dr, dc, own edge, neighbour's matching edge, axis along the edge)
EDGES = {0: (-1, 0, ((0, 0), (2, 0)), ((0, 2), (2, 2)), 0),     # bottom
         1: (0, -1, ((0, 0), (0, 2)), ((2, 0), (2, 2)), 1),     # left
         3: (0, 1, ((2, 0), (2, 2)), ((0, 0), (0, 2)), 1),      # right
         4: (1, 0, ((0, 2), (2, 2)), ((0, 0), (2, 0)), 0)}      # top
PASS_EDGE_BITS = {3: 0, 4: 2, 0: 5, 1: 7}                      # edge nibble index -> pass bit
PASS_CORNER_BITS = {1: 1, 3: 3, 0: 4, 2: 6}                    # corner marker -> pass bit


def rank_class(t: int) -> set[int]:
    if t == 7:
        return {0, 1, 4, 5, 6, 7}
    if t in (1, 4, 5):
        return {1, 4, 5, 7}
    return {0, 6, 7}


class Rec:
    def __init__(self, be: BoundaryEntry, ents: list[int], poly: Polygon):
        self.be, self.ents, self.poly = be, ents, poly

    @property
    def t(self):
        return self.be.path_type


class CellView:
    """Decoded records of the grid's header cells, editable."""

    def __init__(self, g: AreaGrid):
        self.g = g
        self.cache: dict[tuple[int, int], object] = {}

    def origin(self, r, c):
        return self.g.ox + c * self.g.cs, self.g.oy + r * self.g.cs

    def get(self, r, c):
        """list[Rec] for a header cell, ('run', pid) otherwise."""
        if (r, c) in self.cache:
            return self.cache[(r, c)]
        g = self.g
        k, j = g.cell_item[r * g.cols + c]
        it = g.items[k]
        if j or not it.bounds:
            out = ("run", it.pid)
        else:
            x0, y0 = self.origin(r, c)
            out = []
            for a, b in it.bounds:
                be = BoundaryEntry.from_packed(a, b)
                n = g.vlist[be.vertex_index]
                ents = g.vlist[be.vertex_index + 1: be.vertex_index + 1 + n]
                pts = [CORNERS[e] if e in CORNERS else (g.vx[e] - x0, g.vy[e] - y0) for e in ents]
                out.append(Rec(be, list(ents), Polygon(pts) if len(pts) >= 3 else Polygon()))
        self.cache[(r, c)] = out
        return out


def _intervals(poly: Polygon, seg, axis):
    x = poly.boundary.intersection(LineString(seg))
    geoms = [x] if x.geom_type == "LineString" else list(getattr(x, "geoms", []))
    return [(min(p[axis] for p in gm.coords), max(p[axis] for p in gm.coords))
            for gm in geoms if gm.geom_type == "LineString" and gm.length > EPS]


def compute_fields(view: CellView, r: int, c: int) -> list[tuple[int, int]]:
    """(passable_part, unknown2) for every record of header cell (r, c)."""
    recs = view.get(r, c)
    out = []
    for i, rec in enumerate(recs):
        if rec.t in DEAD:
            out.append((0, 0))
            continue
        p, cls = rec.poly, rank_class(rec.t)
        pas = 0
        for e, bit in PASS_EDGE_BITS.items():
            if _intervals(p, EDGES[e][2], EDGES[e][4]):
                pas |= 1 << bit
        for corner, bit in PASS_CORNER_BITS.items():
            if p.buffer(EPS).contains(Point(CORNERS[corner])):
                pas |= 1 << bit
        ranks = {jj: q for q, jj in enumerate(jj for jj, o in enumerate(recs) if o.t in cls)}
        inner = 0
        for jj, q in ranks.items():
            if jj != i and p.boundary.intersection(recs[jj].poly.boundary).length > EPS:
                inner |= 1 << q
        nib = {2: inner & 15}
        for e, (dr, dc, seg, nseg, ax) in EDGES.items():
            mine = _intervals(p, seg, ax)
            if not mine:
                nib[e] = 0
                continue
            nb = view.get(r + dr, c + dc)
            if isinstance(nb, tuple):
                nib[e] = 1
                continue
            mask = 0
            for q, jj in enumerate(jj for jj, o in enumerate(nb) if o.t in cls):
                for a0, a1 in _intervals(nb[jj].poly, nseg, ax):
                    if any(min(a1, m1) - max(a0, m0) > EPS for m0, m1 in mine):
                        mask |= 1 << q
            nib[e] = mask & 15
        out.append((pas, sum(nib[k] << (4 * k) for k in range(5))))
    return out


def self_test(view: CellView, cells) -> list[str]:
    """Cells whose stored fields the rules do not reproduce."""
    bad = []
    for r, c in cells:
        recs = view.get(r, c)
        if isinstance(recs, tuple):
            continue
        for i, (rec, (pas, u2)) in enumerate(zip(recs, compute_fields(view, r, c))):
            if (rec.be.passable_part, rec.be.unknown2) != (pas, u2):
                bad.append(f"r{r} c{c} rec {i} t{rec.t}: stored pass {rec.be.passable_part:08b} "
                           f"u2 {rec.be.unknown2:#07x}, rule pass {pas:08b} u2 {u2:#07x}")
    return bad


def refresh_fields(view: CellView, cells, types=(0, 7)) -> int:
    """Rewrite pass / unknown2 of `types` records in `cells` where the rules disagree with
    the stored value (a transplanted footprint keeps its template's links to neighbours
    that may differ here). Types 4/5/6 are left alone: the rules miss some vanilla ones."""
    g = view.g
    new_specs = {}
    fixed = 0
    for r, c in cells:
        recs = view.get(r, c)
        if isinstance(recs, tuple):
            continue
        bounds, dirty = [], False
        for rec, (pas, u2) in zip(recs, compute_fields(view, r, c)):
            if rec.t in types and (rec.be.passable_part, rec.be.unknown2) != (pas, u2):
                rec.be.passable_part, rec.be.unknown2 = pas, u2
                dirty = True
                fixed += 1
            bounds.append(rec.be.to_packed())
        if dirty:
            k, _ = g.cell_item[r * g.cols + c]
            new_specs[r * g.cols + c] = CellSpec(g.items[k].hdr, bounds, None, None)
    if new_specs:
        g._apply(new_specs)
    view.cache.clear()
    return fixed


def check_cells(view: CellView, cells) -> list[str]:
    """Vanilla invariants of header cells: valid counter-clockwise polygons that partition
    the cell, and every vertex on a cell edge shared with the neighbour across it."""
    g = view.g
    bad = []
    for r, c in cells:
        recs = view.get(r, c)
        if isinstance(recs, tuple):
            continue
        if any(not q.poly.is_valid or q.poly.area <= 0 or not q.poly.exterior.is_ccw for q in recs):
            bad.append(f"r{r} c{c}: invalid or clockwise polygon")
        if abs(sum(q.poly.area for q in recs) - 4.0) > 1e-4 or abs(unary_union([q.poly for q in recs]).area - 4.0) > 1e-4:
            bad.append(f"r{r} c{c}: records do not partition the cell")
        x0, y0 = view.origin(r, c)
        for q in recs:
            for e in q.ents:
                if e in CORNERS:
                    continue
                lx, ly = g.vx[e] - x0, g.vy[e] - y0
                side = ((0, -1) if abs(lx) < 1e-6 else (0, 1) if abs(lx - 2) < 1e-6 else
                        (-1, 0) if abs(ly) < 1e-6 else (1, 0) if abs(ly - 2) < 1e-6 else None)
                if side is None:
                    continue
                nb = view.get(r + side[0], c + side[1])
                if not isinstance(nb, tuple) and not any(e in o.ents for o in nb):
                    bad.append(f"r{r} c{c}: edge vertex {e} at ({lx:.3f},{ly:.3f}) missing from the neighbour")
    return bad


def outline_cells(g: AreaGrid, outline: Polygon) -> list[tuple[int, int]]:
    """Cells the outline overlaps (by area)."""
    x0, y0, x1, y1 = outline.bounds
    r0, c0 = g.cell_of(x0, y0)
    r1, c1 = g.cell_of(x1, y1)
    out = []
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            ox, oy = g.ox + c * g.cs, g.oy + r * g.cs
            if outline.intersection(box(ox, oy, ox + g.cs, oy + g.cs)).area > 1e-9:
                out.append((r, c))
    return out


def ring_of(cells) -> set[tuple[int, int]]:
    cs = set(cells)
    return {(r + dr, c + dc) for r, c in cs for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))} - cs


class Terrain:
    """World-coordinate unions of the records around a point, for fast site search."""

    def __init__(self, view: CellView, near, land_pid: int, reach: int):
        g = view.g
        r0, c0 = g.cell_of(*near)
        sea, land, other, slot = [], [], [], []
        self.run_cells = set()
        for r in range(r0 - reach, r0 + reach + 1):
            for c in range(c0 - reach, c0 + reach + 1):
                recs = view.get(r, c)
                ox, oy = view.origin(r, c)
                if isinstance(recs, tuple):
                    self.run_cells.add((r, c))
                    sq = box(ox, oy, ox + g.cs, oy + g.cs)
                    (sea if recs[1] == len(g.i2) else land if recs[1] == land_pid else other).append(sq)
                    continue
                for q in recs:
                    wp = translate(q.poly, ox, oy)
                    if q.t in SEA:
                        sea.append(wp)
                    elif q.t == 7:
                        slot.append(wp)
                    elif q.t in LAND:
                        (land if q.be.path_id == land_pid else other).append(wp)
        self.sea, self.land = unary_union(sea), unary_union(land)
        self.other, self.slot = unary_union(other), unary_union(slot)


def find_outline_site(view: CellView, make, near, land_pid: int, inside, *, radius=4.0, step=0.05,
                      sea_margin=0.15, min_land=0.7, gap=0.1, taken=(), avoid=frozenset()):
    """Nearest lattice point to `near` where the outline make(x, y) is a vanilla-like
    footprint: inside the region, over header cells only (none of them or their
    4-neighbours in `avoid`), at least `sea_margin` from every sea record, overlapping no
    other region's land and no slot outline, `gap` clear of the outlines in `taken`, at
    least `min_land` of it on land of `land_pid` (the rest impassable), and with no vertex
    on a cell edge. Returns (x, y, outline)."""
    g = view.g
    reach = int(radius / g.cs) + 3
    ter = Terrain(view, near, land_pid, reach)
    taken_u = unary_union(list(taken)) if taken else None
    n = int(radius / step)
    cands = sorted(((i * i + j * j), near[0] + i * step, near[1] + j * step)
                   for i in range(-n, n + 1) for j in range(-n, n + 1) if i * i + j * j <= n * n)
    for _, x, y in cands:
        if not inside(x, y):
            continue
        poly = make(x, y)
        if poly.distance(ter.sea) < sea_margin:
            continue
        if poly.intersection(ter.land).area < min_land * poly.area:
            continue
        if poly.intersection(ter.other).area > 1e-9 or poly.intersection(ter.slot).area > 1e-9:
            continue
        if taken_u is not None and poly.distance(taken_u) < gap:
            continue
        cells = outline_cells(g, poly)
        if any(rc in ter.run_cells or rc in avoid for rc in cells) or any(rc in avoid for rc in ring_of(cells)):
            continue
        if any(min(abs((px - g.ox) / g.cs - round((px - g.ox) / g.cs)),
                   abs((py - g.oy) / g.cs - round((py - g.oy) / g.cs))) < 1e-3
               for px, py in poly.exterior.coords):
            continue
        if plan_carve(view, poly, land_pid) is None:
            continue
        return x, y, poly
    raise RuntimeError(f"no footprint site within {radius} of {near} (min land {min_land}, sea margin {sea_margin})")


def _parts(geom):
    return [p for p in getattr(geom, "geoms", [geom]) if p.geom_type == "Polygon" and not p.is_empty]


def _node(poly: Polygon, pool) -> Polygon:
    """Insert into the ring every pool point that lies inside one of its edges. Records of a
    cell form a planar subdivision whose neighbours share vertex ids along a common edge;
    the outline piece must carry the points where other records' boundaries meet it."""
    coords = list(poly.exterior.coords)[:-1]
    out = []
    for k, a in enumerate(coords):
        b = coords[(k + 1) % len(coords)]
        out.append(a)
        seg = LineString([a, b])
        mids = []
        for q in pool:
            if (abs(q[0] - a[0]) < 1e-9 and abs(q[1] - a[1]) < 1e-9) or (abs(q[0] - b[0]) < 1e-9 and abs(q[1] - b[1]) < 1e-9):
                continue
            if seg.distance(Point(q)) < 1e-7:
                t = seg.project(Point(q))
                if 1e-9 < t < seg.length - 1e-9:
                    mids.append((t, q))
        out.extend(q for _, q in sorted(mids))
    return Polygon(out)


def check_nodes(view: CellView, cells) -> list[str]:
    """Within each cell, a vertex lying inside another record's edge (a T-junction)."""
    g = view.g
    bad = []
    for r, c in cells:
        recs = view.get(r, c)
        if isinstance(recs, tuple):
            continue
        ox, oy = view.origin(r, c)
        pts = {e: CORNERS[e] if e in CORNERS else (g.vx[e] - ox, g.vy[e] - oy) for q in recs for e in q.ents}
        for i, q in enumerate(recs):
            for k, a in enumerate(q.ents):
                z = q.ents[(k + 1) % len(q.ents)]
                seg = LineString([pts[a], pts[z]])
                for e, p in pts.items():
                    if e not in (a, z) and seg.distance(Point(p)) < 1e-6:
                        bad.append(f"r{r} c{c} rec {i} t{q.t}: vertex {e} lies on edge {a}-{z}")
    return bad


def plan_carve(view: CellView, outline: Polygon, land_pid: int):
    """{cell: [(path_type, path_id, local polygon, original Rec or None)]} with the
    outline cut out of every land / impassable record and appended as a type-7 record,
    or None if the cut would leave a hole, a sliver, or touch a sea / slot record."""
    plan = {}
    for r, c in outline_cells(view.g, outline):
        recs = view.get(r, c)
        if isinstance(recs, tuple):
            return None
        ox, oy = view.origin(r, c)
        piece = translate(outline, -ox, -oy).intersection(CELL)
        if piece.geom_type != "Polygon" or piece.area < 1e-4:
            return None
        out = []
        for q in recs:
            if q.poly.intersection(piece).area < 1e-9:
                if q.t in SEA and q.poly.boundary.intersection(piece.boundary).length > EPS:
                    return None
                out.append((q.t, q.be.path_id, q.poly, q))
                continue
            if q.t not in LAND | DEAD or (q.t in LAND and q.be.path_id != land_pid):
                return None
            parts = [p for p in _parts(q.poly.difference(piece)) if p.area >= 1e-9]
            for p in parts:
                if p.interiors or p.area < 1e-4 or p.area / p.length < 0.005:     # hole, or a sliver
                    return None
                # a land piece split off by the outline must border it, or nothing reaches it
                if q.t in LAND and len(parts) > 1 and p.boundary.intersection(piece.boundary).length <= EPS:
                    return None
                out.append((q.t, q.be.path_id, orient(p, 1.0), None))
        out.append((7, land_pid, orient(piece, 1.0), None))
        if abs(sum(p.area for _, _, p, _ in out) - 4.0) > 1e-6:
            return None
        plan[(r, c)] = out
    return plan


def carve_footprint(view: CellView, outline: Polygon, land_pid: int) -> dict:
    """Carve `outline` (world coordinates) into the grid as a slot outline of `land_pid`:
    every land / impassable record under it loses that part, the per-cell pieces are
    appended to their cells as type-7 records, vertices on cell edges are shared by both
    cells, and pass / unknown2 are recomputed for the edited cells and their 4-neighbours.
    Records outside the outline keep their vertex lists. Writes back to the grid."""
    g = view.g
    plan = plan_carve(view, outline, land_pid)
    if plan is None:
        raise RuntimeError("outline cannot be carved here")
    ring = ring_of(plan)

    def fixed(v):
        return int(round(v * FIX))

    known: dict[tuple[int, int], int] = {}            # fixed-point world xy -> vertex id
    for rc in list(plan) + sorted(ring):
        recs = view.get(*rc)
        if isinstance(recs, tuple):
            continue
        for q in recs:
            for e in q.ents:
                if e not in CORNERS:
                    known.setdefault((fixed(g.vx[e]), fixed(g.vy[e])), e)
    added = 0

    def vertex(wx, wy):
        nonlocal added
        kx, ky = fixed(wx), fixed(wy)
        for dx in (0, -1, 1, -2, 2):
            for dy in (0, -1, 1, -2, 2):
                if (kx + dx, ky + dy) in known:
                    return known[(kx + dx, ky + dy)]
        known[(kx, ky)] = g._add_vertex(kx / FIX, ky / FIX)
        added += 1
        return known[(kx, ky)]

    def encode(poly: Polygon, ox, oy) -> list[int]:
        ents = []
        for px, py in list(poly.exterior.coords)[:-1]:
            corner = next((k for k, (cx_, cy_) in CORNERS.items() if abs(px - cx_) < 1e-6 and abs(py - cy_) < 1e-6), None)
            e = corner if corner is not None else vertex(ox + px, oy + py)
            if not ents or ents[-1] != e:
                ents.append(e)
        while len(ents) > 1 and ents[0] == ents[-1]:
            ents.pop()
        return ents

    new_ids = set()
    for (r, c), recs in plan.items():
        ox, oy = view.origin(r, c)
        pool = [xy for _, _, poly, _ in recs for xy in list(poly.exterior.coords)[:-1]]
        out = []
        for t, pid, poly, orig in recs:
            if orig is not None:
                out.append(orig)
                continue
            ents = encode(_node(poly, pool), ox, oy)
            pts = [CORNERS[e] if e in CORNERS else (g.vx[e] - ox, g.vy[e] - oy) for e in ents]
            rec = Rec(BoundaryEntry(pid, 0, 0, 0, t), ents, Polygon(pts))
            new_ids.add(id(rec))
            out.append(rec)
        view.cache[(r, c)] = out

    touched = set(plan) | ring
    new_specs = {}
    n_changed = 0
    for r, c in sorted(touched):
        recs = view.get(r, c)
        if isinstance(recs, tuple):
            continue
        k, _ = g.cell_item[r * g.cols + c]
        bounds = []
        for rec, (pas, u2) in zip(recs, compute_fields(view, r, c)):
            be = rec.be
            fresh = id(rec) in new_ids
            n_changed += fresh or (be.passable_part, be.unknown2) != (pas, u2)
            be.passable_part, be.unknown2 = pas, u2
            if fresh:
                vi = len(g.vlist)
                g.vlist.append(len(rec.ents))
                g.vlist.extend(rec.ents)
                be.vertex_index = vi
            bounds.append(be.to_packed())
        new_specs[r * g.cols + c] = CellSpec(g.items[k].hdr, bounds, None, None)
    g._apply(new_specs)
    view.cache.clear()
    return {"cells": sorted(plan), "rewritten_cells": len(new_specs), "records_changed": n_changed,
            "vertices_added": added, "area": outline.area}
