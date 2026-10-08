"""
The campaign map's painted texture (deep_dive 10.20).

data/supertexture.pack holds ``world_.stpi`` (index) and ``world_.stpd`` (tiles). Land and sea
colour on the campaign map come straight from it (fx\\supertexturetile.fx); diffuse alpha is
the sea mask (255 water, 0 land). It is a hand-painted parchment atlas: one framed panel per
theatre, with bare parchment and drawn coast lines where CA did not paint (Arabia south of
Jawf, the southern Red Sea: grey, flat and "land" in game).

stpi: u32 7, 65536, 32768 (pixels at mip 0), 512 (tile), 262272 (tile bytes), 7 (mips); then
per mip u32 tiles_x, tiles_y and tiles_x * tiles_y row-major records (u32 stpd offset,
compressed size, decompressed size, content hash, flags 0x67f200 stored / 0x67f201 a reuse of
an earlier tile). stpd: zlib streams of 512 x 512 DXT5 DDS files. Mip m tile (i, j) is the 2x2
box of mip m-1 tiles (2i.., 2j..). The hash is a dedup key (not crc32 / adler); new tiles get
random ones, which the game accepts. A movie pack holding both files overrides the vanilla pack.

Map (regions.esf x, y) -> mip-0 pixel: ((x + 1280) * 25.6, (640 - y) * 25.6).
"""

from __future__ import annotations

import io
import random
import struct
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

K0 = 25.6               # mip-0 pixels per map unit
TILE = 512
STPI, STPD = "world_.stpi", "world_.stpd"
STORED = 0x67F200


def map_to_px(x, y, mip: int = 0):
    k = K0 / (1 << mip)
    return (x + 1280) * k, (640 - y) * k


def px_to_map(px, py, mip: int = 0):
    k = K0 / (1 << mip)
    return px / k - 1280, 640 - py / k


def _pack_entries(path: Path) -> dict[str, tuple[int, int]]:
    with open(path, "rb") as f:
        _, _, _, repsz, n, isz = struct.unpack("<4sIIIII", f.read(24))
        f.seek(24 + repsz)
        idx = f.read(isz)
    o, off, out = 0, 24 + repsz + isz, {}
    for _ in range(n):
        sz = struct.unpack_from("<I", idx, o)[0]
        e = idx.index(b"\0", o + 4)
        out[idx[o + 4:e].decode("latin1")] = (off, sz)
        off, o = off + sz, e + 1
    return out


def downsample(big: np.ndarray) -> np.ndarray:
    """2x2 box average per channel. Not Pillow: it resizes RGBA premultiplied, and land is
    alpha 0 here, so land came out black in every rebuilt mip (zoomed-out land black)."""
    h, w = big.shape[0] // 2, big.shape[1] // 2
    return np.round(big.reshape(h, 2, w, 2, -1).astype(np.float32).mean((1, 3))).astype(np.uint8)


class Supertexture:
    def __init__(self, pack: Path):
        self.pack = Path(pack)
        ents = _pack_entries(self.pack)
        self.f = open(self.pack, "rb")
        self.f.seek(ents[STPI][0])
        self.stpi = self.f.read(ents[STPI][1])
        self.stpd_off, self.stpd_size = ents[STPD]
        self.hdr = struct.unpack_from("<6I", self.stpi, 0)
        o, self.mips = 24, []
        for _ in range(self.hdr[5]):
            tx, ty = struct.unpack_from("<II", self.stpi, o)
            o += 8
            self.mips.append((tx, ty, [list(struct.unpack_from("<5I", self.stpi, o + 20 * k))
                                       for k in range(tx * ty)]))
            o += 20 * tx * ty
        assert o == len(self.stpi), "stpi not fully parsed"
        self.dds_header = None
        self._cache: dict[tuple[int, int, int], np.ndarray] = {}

    def tile(self, m: int, i: int, j: int) -> np.ndarray:
        """Vanilla RGBA (512, 512, 4) uint8."""
        key = (m, i, j)
        if key not in self._cache:
            rec = self.mips[m][2][j * self.mips[m][0] + i]
            self.f.seek(self.stpd_off + rec[0])
            d = zlib.decompress(self.f.read(rec[1]))
            self.dds_header = self.dds_header or d[:128]
            self._cache[key] = np.asarray(Image.open(io.BytesIO(d)).convert("RGBA")).copy()
        return self._cache[key]

    def canvas(self, i0: int, i1: int, j0: int, j1: int) -> np.ndarray:
        """Mip-0 tiles [i0, i1) x [j0, j1) stitched into one RGBA array."""
        out = np.zeros(((j1 - j0) * TILE, (i1 - i0) * TILE, 4), np.uint8)
        for j in range(j0, j1):
            for i in range(i0, i1):
                out[(j - j0) * TILE:(j - j0 + 1) * TILE, (i - i0) * TILE:(i - i0 + 1) * TILE] = self.tile(0, i, j)
        return out

    def box(self, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
        """Mip-0 RGBA pixels of a map box (x0 < x1, y0 < y1), from vanilla tiles."""
        px0, py1 = map_to_px(x0, y0)
        px1, py0 = map_to_px(x1, y1)
        i0, j0 = int(px0) // TILE, int(py0) // TILE
        c = self.canvas(i0, int(px1) // TILE + 1, j0, int(py1) // TILE + 1)
        return c[int(py0) - j0 * TILE:int(py1) - j0 * TILE, int(px0) - i0 * TILE:int(px1) - i0 * TILE]

    def write(self, new0: dict[tuple[int, int], np.ndarray]) -> tuple[bytes, bytes, list[int]]:
        """New mip-0 tiles {(i, j): RGBA} -> (stpi, stpd, tiles written per mip). Every mip above
        is rebuilt where a child changed; new tiles are appended to a copy of vanilla's stpd, so
        vanilla tiles keep their offsets."""
        new = {(0, i, j): a for (i, j), a in new0.items()}
        dirty = set(new0)
        for m in range(1, len(self.mips)):
            dirty = {(i // 2, j // 2) for i, j in dirty}
            for i, j in dirty:
                big = np.zeros((2 * TILE, 2 * TILE, 4), np.uint8)
                for dj in (0, 1):
                    for di in (0, 1):
                        c = (m - 1, 2 * i + di, 2 * j + dj)
                        big[dj * TILE:(dj + 1) * TILE, di * TILE:(di + 1) * TILE] = \
                            new[c] if c in new else self.tile(*c)
                new[(m, i, j)] = downsample(big)
        self.f.seek(self.stpd_off)
        stpd = bytearray(self.f.read(self.stpd_size))
        recs = [[list(r) for r in rs] for _, _, rs in self.mips]
        for (m, i, j), a in sorted(new.items()):
            b = io.BytesIO()
            Image.fromarray(a, "RGBA").save(b, "DDS", pixel_format="DXT5")
            d = b.getvalue()
            assert len(d) == self.hdr[4], f"DXT5 tile is {len(d)} bytes"
            d = (self.dds_header or d[:128]) + d[128:]
            c = zlib.compress(d, 9)
            recs[m][j * self.mips[m][0] + i] = [len(stpd), len(c), len(d), random.getrandbits(32), STORED]
            stpd += c
        out = bytearray(struct.pack("<6I", *self.hdr))
        for (tx, ty, _), rs in zip(self.mips, recs):
            out += struct.pack("<II", tx, ty)
            for r in rs:
                out += struct.pack("<5I", *r)
        assert len(out) == len(self.stpi)
        return bytes(out), bytes(stpd), [sum(1 for k in new if k[0] == m) for m in range(len(self.mips))]
