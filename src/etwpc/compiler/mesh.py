"""
regions.esf geometry: vertex table, area outlines, connectivity and the query_info
quadtree (deep_dive 10.2-10.3), with the rules a region split regenerates them by.

* vertices: one float32 (x, y) table; outlines and faces index into it.
* outline item: [closed, bbox min, bbox max, vertex indices, connectivity]. Closed rings are
  coasts / borders, open polylines are river banks. connectivity = runs [neighbour
  region<<16|area, first, last] along the outline; the neighbour of edge i (v_i -> v_i+1)
  is the area whose own outline has the reversed edge; none = 0xFFFFFFFF, and a gap
  between two runs of the same neighbour joins them (1,341 of 1,352 vanilla outlines).
* faces: clockwise triangles over vertex indices (outlines run counter-clockwise); areas
  that form one connected landmass share one list verbatim (river polygons excluded).
* quadtree leaf cell: [point, default, (a, b, A, B) * n]. default = area containing the
  point; a leaf lists every edge overlapping its box with positive length; for (a, b, A,
  B), B owns a->b and A owns b->a.
"""

from __future__ import annotations

import struct

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_U4

NONE = 0xFFFFFFFF


def packed(region: int, area: int) -> int:
    return region << 16 | area


def _set_list(p: ESFPrimitive, vals) -> None:
    p.value, p.raw = list(vals), b""


def _set_v2(p: ESFPrimitive, xy) -> None:
    p.value, p.raw = (float(xy[0]), float(xy[1])), b""


class Mesh:
    def __init__(self, root):
        self.root = root
        rd = root.children[3]
        self.vprim: ESFPrimitive = rd.children[0].children[0]
        self.xy = list(struct.unpack(f"<{len(self.vprim.raw) // 4}f", self.vprim.raw))
        self.regs = rd.children[3].children

    # ── vertices ─────────────────────────────────────────────────────────
    def V(self, i: int) -> tuple[float, float]:
        return self.xy[2 * i], self.xy[2 * i + 1]

    def add_vertex(self, x: float, y: float) -> int:
        fx, fy = struct.unpack("<2f", struct.pack("<2f", x, y))     # float32, as stored
        self.xy += [fx, fy]
        return len(self.xy) // 2 - 1

    def flush_vertices(self) -> None:
        self.vprim.raw = struct.pack(f"<{len(self.xy)}f", *self.xy)
        self.vprim.value = None

    # ── outlines ─────────────────────────────────────────────────────────
    def name(self, ri: int) -> str:
        return self.regs[ri][0].value

    def areas(self, ri: int) -> list:
        node = next((c for c in self.regs[ri] if isinstance(c, ESFNode) and c.tag == "areas"), None)
        return node.children if node is not None else []

    def outlines(self):
        """(region, area, outline item) for every outline."""
        for ri in range(len(self.regs)):
            for ai, a in enumerate(self.areas(ri)):
                for ol in a[7].children:
                    yield ri, ai, ol

    @staticmethod
    def edges(vs, closed):
        n = len(vs)
        return [(vs[i], vs[(i + 1) % n]) for i in range(n if closed and n > 2 else n - 1)]

    def owner_map(self) -> dict[tuple[int, int], int]:
        own = {}
        for ri, ai, ol in self.outlines():
            for e in self.edges(list(ol[3].value), ol[0].value):
                own.setdefault(e, packed(ri, ai))
        return own

    def runs(self, vs, closed, own) -> list[list[int]]:
        nb = [own.get((b, a), NONE) for a, b in self.edges(vs, closed)]
        n = len(nb)
        for _ in range(n):                     # join gaps between runs of the same neighbour
            changed = False
            i = 0
            while i < n:
                if nb[i] != NONE:
                    i += 1
                    continue
                j = i
                while j < n and nb[j] == NONE:
                    j += 1
                prev = nb[i - 1] if i > 0 else (nb[-1] if closed else None)
                nxt = nb[j] if j < n else (nb[0] if closed else None)
                if prev not in (None, NONE) and prev == nxt and j - i < n:
                    nb[i:j] = [prev] * (j - i)
                    changed = True
                i = j
            if not changed:
                break
        out = []
        for i, x in enumerate(nb):
            if out and out[-1][0] == x:
                out[-1][2] = i
            else:
                out.append([x, i, i])
        if not closed and out:
            out[-1][2] = len(vs) - 1
        return out

    @staticmethod
    def stored_runs(ol) -> list[list[int]]:
        return [[c[0].value, c[1].value, c[2].value] for c in ol[4].children]

    @staticmethod
    def write_runs(ol, runs) -> None:
        con = ol[4]
        proto = con.children[0]
        items = []
        for nb, a, b in runs:
            it = [ESFPrimitive(type_tag=x.type_tag, value=x.value, raw=b"") for x in proto]
            for p, v in zip(it, (nb, a, b)):
                p.value, p.raw = v, b""
            items.append(it)
        con.children = items

    def set_outline(self, ol, vs, closed=None) -> None:
        if closed is not None:
            ol[0].value = closed
        _set_list(ol[3], vs)
        pts = [self.V(i) for i in vs]
        _set_v2(ol[1], (min(p[0] for p in pts), min(p[1] for p in pts)))
        _set_v2(ol[2], (max(p[0] for p in pts), max(p[1] for p in pts)))

    def area_polygon(self, ri: int, ai: int):
        a = self.areas(ri)[ai]
        ps = [Polygon([self.V(i) for i in ol[3].value]).buffer(0) for ol in a[7].children
              if ol[0].value and len(ol[3].value) > 2]
        return unary_union(ps)

    def set_area_bbox(self, a) -> None:
        los = [ol[1].value for ol in a[7].children]
        his = [ol[2].value for ol in a[7].children]
        _set_v2(a[3], (min(p[0] for p in los), min(p[1] for p in los)))
        _set_v2(a[4], (max(p[0] for p in his), max(p[1] for p in his)))

    def set_region_bbox(self, ri: int) -> None:
        ar = self.areas(ri)
        _set_v2(self.regs[ri][2], (min(a[3].value[0] for a in ar), min(a[3].value[1] for a in ar)))
        _set_v2(self.regs[ri][3], (max(a[4].value[0] for a in ar), max(a[4].value[1] for a in ar)))

    # ── faces ────────────────────────────────────────────────────────────
    def triangulate(self, poly) -> list[int]:
        """Constrained Delaunay triangles of `poly` (holes allowed) over existing vertex
        indices; every polygon vertex must already be in the table."""
        import shapely
        index = {}
        for i in range(len(self.xy) // 2):
            index.setdefault((round(self.xy[2 * i], 4), round(self.xy[2 * i + 1], 4)), i)
        out = []
        for t in shapely.constrained_delaunay_triangles(poly).geoms:
            cs = list(t.exterior.coords)[:3]
            if Polygon(cs).area < 1e-9:
                continue
            ids = [index[(round(x, 4), round(y, 4))] for x, y in cs]
            if Polygon(cs).exterior.is_ccw:            # vanilla triangles are clockwise
                ids = [ids[0], ids[2], ids[1]]
            out += ids
        return out

    # ── quadtree ─────────────────────────────────────────────────────────
    def leaves(self):
        """(lo, hi, cell node) for every quadtree leaf."""
        out = []

        def walk(n):
            for c in n.children:
                if isinstance(c, ESFNode):
                    if c.tag == "cell":
                        out.append((tuple(n.children[0].value), tuple(n.children[1].value), c))
                    else:
                        walk(c)
        walk(self.root.children[6].children[2])
        return out

    def overlaps(self, a: int, b: int, lo, hi) -> bool:
        seg = LineString([self.V(a), self.V(b)])
        return seg.intersection(box(lo[0], lo[1], hi[0], hi[1])).length > 0
