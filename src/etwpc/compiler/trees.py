"""
Campaign-map trees (deep_dive 10.19).

Every tree on the campaign map is an instance in one file,
``rigidmodels\\campaigntrees\\campaign.rigid_trees`` (models.pack, replaced by patch2.pack).
Nothing else knows about them: movement and region data ignore trees, so a new settlement
carved out of a forest keeps the forest standing on it.

File: b"G@M=", u32 1, f32 1024.0, u32 6, u32 type count, then per type: u16 n + UTF-16 model
path ("RigidModels/CampaignTrees/Campaign_tree_coniferous01.rigid_model"), u32 1, u32 count,
6 x f32 model bounds, count x (x, rotation?, y, ~35..38) float32 in map coordinates (the same
x/y as regions.esf). Vanilla: 174 types, 27,587 instances, each a cluster model.

Vanilla keeps ground clear round what it places (nearest tree, 5th percentile): capitals 1.9,
settlement building slots 1.5..2.3, ports 1.7, towns 1.45, resource slots 1.2.
"""

from __future__ import annotations

import math
import struct

PACK_PATH = "rigidmodels\\campaigntrees\\campaign.rigid_trees"
# clear radius by slot kind, after vanilla's clearances
CLEAR_CAPITAL = 2.0
CLEAR = {"town": 1.5, "port": 1.5, "resource": 1.2}


def read_trees(data: bytes) -> tuple[bytes, list[list]]:
    """(header bytes, [[name bytes (u16 + UTF-16), one/count/bounds bytes, [records]], ...])."""
    ntypes = struct.unpack_from("<I", data, 16)[0]
    o, types = 20, []
    for _ in range(ntypes):
        n = struct.unpack_from("<H", data, o)[0]
        name = data[o:o + 2 + 2 * n]
        o += 2 + 2 * n
        one, count = struct.unpack_from("<II", data, o)
        bounds = data[o + 8:o + 32]
        o += 32
        recs = [data[o + 16 * k:o + 16 * k + 16] for k in range(count)]
        o += 16 * count
        types.append([name, one, bounds, recs])
    assert o == len(data), f"rigid_trees: parsed {o} of {len(data)} bytes"
    return data[:20], types


def write_trees(header: bytes, types: list[list]) -> bytes:
    out = [header[:16], struct.pack("<I", len(types))]
    for name, one, bounds, recs in types:
        out += [name, struct.pack("<II", one, len(recs)), bounds, *recs]
    return b"".join(out)


def tree_xy(rec: bytes) -> tuple[float, float]:
    x, _, y, _ = struct.unpack("<4f", rec)
    return x, y


def clear_trees(data: bytes, sites: list[tuple[float, float, float]]) -> tuple[bytes, int]:
    """Drop every tree within r of a site (x, y, r); returns (file, trees removed)."""
    header, types = read_trees(data)
    removed = 0
    for t in types:
        keep = [r for r in t[3]
                if not any(math.dist(tree_xy(r), (x, y)) < rad for x, y, rad in sites)]
        removed += len(t[3]) - len(keep)
        t[3] = keep
    return write_trees(header, types), removed
