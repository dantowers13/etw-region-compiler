"""
Paint the campaign map's bare parchment south of the North African coast and of Jawf (the
Sahara from Mauritania to Egypt, the Red Sea coasts, Arabia, the Gulf of Oman) into the
supertexture and give it relief, so the land can be opened up (deep_dive 10.20-10.22).

  * parchment: low-chroma light pixels near the void regions (arabia, sahara, central_africa),
    measured over land, grid lines closed over; small parchment islands next to repainted sea
    count too. The Atlantic panel keeps its left margin: water is painted down to the frame east
    of a new torn edge at x ~-177.5, shadowed like vanilla's
  * the torn-paper shadow on the painted side: undone on land by a local brightness gain;
    water within 6 units of the old edge is repainted (its shadow is deepest in the Persian Gulf)
  * desert: colour pulled in from the painted land round it (push-pull), fading inland to gravel
    or sand (the painted Syrian desert / Nafud) in Arabia and, in Africa, to the painted desert
    along the edge at that longitude; sand seas (the Rub' al Khali, the Dahna, the ergs) are sand.
    Detail splatted from painted deserts
  * mountains: Hejaz / Asir / Yemen, the Red Sea Hills and the Hajar as bands at set distances
    from the Red Sea and the Gulf of Oman, the Saharan massifs as ellipses, all ridged with
    patches of the painted Zagros
  * water: colour and alpha by distance from land, sampled from the painted Persian Gulf (east)
    or Atlantic (west); painted down to the frame bar and across the strip between the Europe
    and India panels
  * relief: heightmaps\\default.tga (8192 x 4096, 8 bit, 3.2 px per map unit, the same grid as
    supertexture mip 3) is 0 south of the old torn edge. New land gets a plateau (vanilla's heights
    carried in; Arabia tilting from the Hejaz down to the Gulf, the Sahara raised round its
    massifs), coast ramps, desert micro-relief from vanilla's Nafud heights and, under the painted
    mountains, the Zagros heights from the very patches the ridges were painted from
  * palms at oases and palm-lined shores (campaign trees, with an olive tint under them)
  * mips rebuilt, tiles appended, shipped with the heightmap as new_regions_map.pack (movie pack,
    overrides supertexture.pack and main.pack)

The canvas (16,896 x 3,072 px) is too big to paint at once (~240 bytes a pixel): the cheap,
long-range fields (masks, the desert colour membrane, water tint, mountains, sand, palms, heights)
are worked out once on the heightmap grid, then the full-resolution work runs in strips of
CHUNK tiles with HALO tiles of overlap. Noise and splat patches are laid out in canvas
coordinates, so neighbouring strips agree where they meet.

Run from the game-dir clone:
    python scripts/paint_supertexture.py --regions-esf out/<build>/regions.esf \\
        --region-pack out/<build>/new_regions.pack --out out/map_<name>
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from etwpc.io.esf_reader import ESFReader  # noqa: E402
from etwpc.compiler.mesh import Mesh  # noqa: E402
from etwpc.compiler.supertexture import K0, TILE, STPD, STPI, Supertexture, map_to_px, px_to_map  # noqa: E402
from etwpc.compiler.trees import PACK_PATH as TREES_PATH, read_trees, tree_height, write_trees  # noqa: E402
from reactivate_region import build_pack, pack_file, sas_of  # noqa: E402

I0, I1, J0, J1 = 54, 87, 20, 26          # mip-0 tiles painted: map x -200..460, y 120..240
CHUNK, HALO = 6, 1                       # tiles painted per strip, tiles of overlap each side
DEBUG = None                             # a dict to collect each strip's masks (1/4 scale), for inspection
FRAME_Y = 133.35                         # top of the Europe panel's bottom frame bar
ATL_EDGE_X = -177.5                      # the Atlantic panel's left torn edge, carried down to the frame
ATL_TOP = 152.0                          # its margin north of here (x < -150) stays as it is
# vanilla's void land, and the regions carved out of it (split_region.py void_parent): painted
# as void, or Hejaz's coast, over 6 units from what is left of `arabia`, stayed parchment
VOID_LAND = {"arabia", "sahara", "central_africa", "hejaz", "najd", "al_hasa", "oman"}
DESERT_SRC = ((278, 199, 318, 212), (262, 207, 300, 219))   # painted Nafud / Syrian desert
MOUNTAIN_SRC = (352, 212, 378, 226)                          # painted Zagros folds
GULF_REF = (320, 196, 400, 240)                              # painted Persian Gulf (water ramps)
WATER_SRC = (330, 192, 375, 215)                             # open water of the northern gulf (texture)
ATL_REF = (-176, 156, -110, 240)                             # painted Atlantic off Morocco (water ramps)
ATL_WATER_SRC = (-172, 158, -135, 196)                       # its open water (texture)
ATL_X = 0.0                                                  # water west of here takes the Atlantic's
CREAM_X = 60.0                                               # cream parchment (Morocco, Algeria) lies west of here
HEIGHTMAP = "heightmaps\\default.tga"
HK = K0 / 8                                                  # heightmap pixels per map unit (3.2)
SNOW_SAFE = 135                                              # the shader whitens ground above ~144
VANILLA_TREE_PACKS = (Path("../data/models.pack"), Path("../data/patch2.pack"))
SITE_CLEAR = 1.6                                             # palms keep this far from settlements and slots
PALMS = [f"RigidModels/CampaignTrees/Campaign_tree_palm0{k}.rigid_model" for k in (1, 2, 3)]
# Map (x, y) of places below: Arabia from a lat/lon fit to eight coastal landmarks (within ~2
# units), Africa from a thin-plate spline through the Canaries, Cape Blanc, southern Tunisia's
# tip, the Gulf of Sidra, Suez, Cairo and Wadi Halfa (the painted Nile's end).
# oases and palm-lined towns: x, y, palm clusters, spread (map units)
OASES = [
    ("medina", 282, 174.5, 10, 1.0), ("khaybar", 280, 183, 5, 0.7), ("tayma", 274, 196, 4, 0.6),
    ("al_ula", 270, 189, 4, 0.6), ("hail", 296.5, 195, 4, 0.7), ("qassim", 313, 187, 7, 1.0),
    ("diriyah", 332.5, 176, 6, 0.8), ("al_kharj", 337, 172, 4, 0.7), ("hofuf", 353, 181, 12, 1.2),
    ("qatif", 355.6, 189, 6, 0.6), ("jeddah", 280, 154, 3, 0.5), ("yanbu", 268, 172, 3, 0.5),
    ("doha", 366.5, 180.4, 2, 0.4), ("al_ain", 397, 173, 5, 0.6), ("liwa", 383, 165.5, 6, 1.2),
    ("ibri", 402.4, 166, 3, 0.5), ("nizwa", 409.5, 164, 5, 0.6), ("muscat", 416.5, 169, 3, 0.4),
    ("kharga", 214, 180, 6, 0.9), ("dakhla", 203, 180, 6, 1.0), ("farafra", 196, 191, 3, 0.5),
    ("bahariya", 202, 199, 4, 0.6), ("suakin", 266, 138.5, 3, 0.5),
    ("siwa", 181, 202.7, 5, 0.6), ("kufra", 168, 171, 6, 0.8), ("jalu", 156, 207.5, 3, 0.5),
    ("sebha", 107.5, 188.7, 4, 0.6), ("murzuq", 104, 180, 4, 0.6), ("ghat", 77.5, 169, 3, 0.5),
    ("ghadames", 70, 202, 4, 0.6), ("ouargla", 39, 215, 5, 0.7), ("touggourt", 44, 223.5, 4, 0.6),
    ("el_golea", 23, 207, 3, 0.5), ("timimoun", 5, 200.4, 4, 0.7), ("adrar", 2.6, 191, 4, 0.7),
    ("in_salah", 22.4, 183.7, 3, 0.5), ("tamanrasset", 45.6, 153.3, 2, 0.4), ("djanet", 73, 166, 2, 0.4),
    ("tafilalt", -27.6, 220.3, 5, 0.8), ("tindouf", -52.7, 197.8, 2, 0.4), ("atar", -87.7, 146, 3, 0.5),
    ("chinguetti", -82.2, 145.7, 2, 0.4), ("ouadane", -76.2, 148.8, 2, 0.4),
]
# palm-lined shores: box (x0, y0, x1, y1), how many, distance from the water (map units)
PALM_SHORES = [
    ("batinah", (403, 167, 418, 179), 14, (0.2, 1.0)),       # the Gulf of Oman coast below the Hajar
    ("tihama", (268, 136, 290, 165), 10, (0.3, 1.4)),        # the Red Sea plain below the Asir
    ("nubian_nile", (219, 157, 236, 173), 10, (0.15, 0.7)),  # the Nile above vanilla's last palms
]
# Saharan massifs: x, y, radii, rotation (degrees), height (1 = the Hejaz)
MASSIFS = [
    ("ahaggar", 46, 156.5, 14, 11, 0, 1.0), ("tassili", 65.4, 171, 16, 5, -35, 0.6),
    ("tibesti", 133.3, 148.2, 15, 12, 20, 1.1), ("uweinat", 179.6, 154.8, 4, 4, 0, 0.7),
    ("gilf_kebir", 185.7, 165, 7, 6, 0, 0.5), ("adrar_mauritania", -87.3, 146, 10, 7, 30, 0.5),
]
# sand seas: x, y, radii, rotation (degrees)
SAND_SEAS = [
    ("great_sand_sea", 181.6, 184.7, 7, 14, 0), ("calanshio", 162.6, 198.9, 7, 5, 0),
    ("rebiana", 156.4, 174.1, 6, 5, 0), ("ubari", 90, 184.7, 10, 4, 0), ("murzuq", 94.4, 168.4, 7, 5, 0),
    ("erg_oriental", 55.5, 204.1, 12, 7, 0), ("erg_occidental", 2.9, 209.1, 13, 6, 0),
    ("erg_chech", -11.1, 173.1, 10, 12, 30), ("erg_iguidi", -33.1, 186.7, 12, 4, 20),
    ("majabat", -41.5, 144.3, 14, 8, 0), ("ouarane", -63.9, 149.2, 10, 5, 0), ("akchar", -99.5, 146, 6, 6, 30),
]


def push_pull(img, w, levels=9):
    """Fill where w == 0 from the weighted neighbourhood, coarse to fine (a smooth membrane)."""
    if levels == 0 or min(img.shape[:2]) < 4:
        tot = (img * w[..., None]).sum((0, 1)) / max(w.sum(), 1e-6)
        return np.where(w[..., None] > 0, img, tot)
    h2, w2 = (img.shape[0] + 1) // 2, (img.shape[1] + 1) // 2
    pi = np.zeros((h2 * 2, w2 * 2, img.shape[2]))
    pw = np.zeros((h2 * 2, w2 * 2))
    pi[:img.shape[0], :img.shape[1]] = img * w[..., None]
    pw[:img.shape[0], :img.shape[1]] = w
    si = pi.reshape(h2, 2, w2, 2, -1).sum((1, 3))
    sw = pw.reshape(h2, 2, w2, 2).sum((1, 3))
    coarse = push_pull(si / np.maximum(sw, 1e-6)[..., None], np.minimum(sw, 1.0), levels - 1)
    up = ndimage.uniform_filter(np.kron(coarse, np.ones((2, 2, 1)))[:img.shape[0], :img.shape[1]], size=(3, 3, 1))
    ww = np.clip(w, 0, 1)[..., None]
    return img * ww + up * (1 - ww)


def up(a, f, shape):
    """Nearest-neighbour upsample by f (float32), cropped to shape."""
    return np.repeat(np.repeat(a.astype(np.float32), f, 0), f, 1)[:shape[0], :shape[1]]


def smooth_fill(img, known, small=8, sigma=6):
    """push_pull at 1/small resolution, blurred and scaled back up."""
    base = push_pull(img[::small, ::small], known[::small, ::small].astype(float))
    base = up(ndimage.gaussian_filter(base, (sigma, sigma, 0)), small, known.shape)
    return ndimage.uniform_filter(base, size=(small * 2, small * 2, 1))


class Splat:
    """Overlapping random patches of the sources (raised-cosine weights) laid on a grid of the
    whole canvas, each patch's choice seeded by its grid cell, so any window of columns renders
    the same as the whole would. Patches are zero-mean (a patch's own offset showed as a grid of
    blotches) and jittered by at most S / 4 (neighbours still overlap when P >= 1.5 S)."""

    def __init__(self, sources, shape, seed, key, P=256, S=160):
        self.src, (self.H, self.W), self.seed, self.key, self.P, self.S = sources, shape, seed, key, P, S
        self.C = sources[0].shape[2]
        win1 = np.sin(np.linspace(0, np.pi, P)) ** 2 + 1e-3
        self.win = (win1[:, None] * win1[None, :]).astype(np.float32)
        self.scale = np.ones(self.C, np.float32)
        # blending overlapping patches flattens their contrast: measure by how much, once
        cal = self.render(0, min(self.W, 6 * P), min(self.H, 6 * P))
        src_std = np.mean([s.reshape(-1, self.C).std(0) for s in sources], 0)
        self.scale = (src_std / np.maximum(cal.reshape(-1, self.C).std(0), 1e-6)).astype(np.float32)

    def render(self, x0, x1, h=None):
        h, w, P, S = h or self.H, x1 - x0, self.P, self.S
        acc = np.zeros((h + 4 * P, w + 4 * P, self.C), np.float32)
        wacc = np.zeros((h + 4 * P, w + 4 * P), np.float32)
        for iy, y0 in enumerate(range(-P // 2, h, S)):
            for ix, gx0 in enumerate(range(-P // 2, self.W, S)):
                if gx0 + P + S // 4 <= x0 or gx0 - S // 4 >= x1:
                    continue
                r = np.random.default_rng([self.seed, self.key, iy, ix])
                src = self.src[r.integers(len(self.src))]
                a, b = r.integers(0, src.shape[0] - P + 1), r.integers(0, src.shape[1] - P + 1)
                patch = src[a:a + P, b:b + P]
                patch = patch - patch.mean((0, 1))
                if r.random() < 0.5:
                    patch = patch[:, ::-1]
                jy, jx = r.integers(-S // 4, S // 4 + 1, size=2)
                py, px = y0 + 2 * P + jy, gx0 - x0 + 2 * P + jx
                acc[py:py + P, px:px + P] += patch * self.win[..., None]
                wacc[py:py + P, px:px + P] += self.win
        out = acc[2 * P:2 * P + h, 2 * P:2 * P + w] / np.maximum(wacc[2 * P:2 * P + h, 2 * P:2 * P + w, None], 1e-6)
        return out * self.scale


class Noise:
    """Smooth noise in [-1, 1] over the whole canvas at 1/cell resolution; .at() gives any
    window of columns (x0 a multiple of cell) at full resolution."""

    def __init__(self, shape, rng, cell, sigma):
        self.H, self.cell = shape[0], cell
        n = ndimage.gaussian_filter(rng.standard_normal((shape[0] // cell + 1, shape[1] // cell + 1)), sigma)
        self.n = (n / (np.abs(n).max() + 1e-6)).astype(np.float32)

    def at(self, x0, x1):
        # bilinear: nearest-neighbour cells of up to 10 units showed as rectangles
        def lerp_axis(a, n_out, start, axis):
            t = (np.arange(start, start + n_out) + 0.5) / self.cell - 0.5
            i = np.clip(np.floor(t).astype(int), 0, a.shape[axis] - 2)
            f = np.clip(t - i, 0, 1).astype(np.float32)
            lo, hi = np.take(a, i, axis), np.take(a, i + 1, axis)
            f = f[:, None] if axis == 0 else f[None, :]
            return lo * (1 - f) + hi * f
        cols = lerp_axis(self.n, x1 - x0, x0, 1)
        return lerp_axis(cols, self.H, 0, 0).astype(np.float32)


def band(d, lo, hi):
    """1 in the middle of [lo, hi] (map units), falling to 0 at both ends."""
    t = np.clip((d - lo) / np.maximum(hi - lo, 1e-6), 0, 1)
    return np.sin(np.pi * t) ** 0.7


def ellipse_r(xs, ys, x, y, rx, ry, angle):
    """Normalised radius of an ellipse (1 on its edge)."""
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    u, v = (xs - x) * c + (ys - y) * s, -(xs - x) * s + (ys - y) * c
    return np.sqrt((u / rx) ** 2 + (v / ry) ** 2)


def edt(mask):
    """Distance to the nearest False pixel; inf when there is none (scipy then measures from
    outside the top-left corner)."""
    if mask.all():
        return np.full(mask.shape, np.inf, np.float32)
    return ndimage.distance_transform_edt(mask).astype(np.float32)


def grow_parchment(parch, ok, chroma, lum, k, west):
    """Parchment the light grey test missed, next to what it found (k px per map unit): warmer
    cream parchment (chroma 60-75, only west of CREAM_X) out to 6 units, stopping at the dark shadow
    band the torn edge has there; then that band where it lies on the parchment side (dark grey,
    within 4 units). Missed, Morocco's edge was measured from ~6 units too far south and its shadow
    carried in as land. Further east painted land (the Nile valley, Cyrenaica) fades into the
    parchment with no dark band between, and the growth swallowed it."""
    light = ok & west & (lum > 150) & (chroma < 90) & (edt(~parch) < 6 * k)
    parch = ndimage.binary_propagation(parch, mask=light | parch)
    return parch | (ok & (chroma < 45) & (lum <= 135) & (edt(~parch) < 4 * k))


def pack_names(pack: Path) -> list[str]:
    with open(pack, "rb") as f:
        _, _, _, repsz, n, isz = struct.unpack("<4sIIIII", f.read(24))
        f.seek(24 + repsz)
        idx = f.read(isz)
    o, names = 0, []
    for _ in range(n):
        e = idx.index(b"\0", o + 4)
        names.append(idx[o + 4:e].decode("latin1"))
        o = e + 1
    return names


def over_land(seed, land, steps):
    """Steps (chessboard px) from seed to every pixel walking over land only, inf past `steps`.
    Musandam is 3 units of water from Iran: straight-line distances reached across the strait."""
    d = np.full(seed.shape, np.inf, np.float32)
    cur = seed & land
    d[cur] = 0
    for k in range(1, steps + 1):
        nxt = ndimage.binary_dilation(cur, np.ones((3, 3), bool)) & land
        d[nxt & ~cur] = k
        cur = nxt
    return d


def mip_canvas(st, m, i0, i1, j0, j1):
    """Mip-m pixels of the mip-0 tile range [i0, i1) x [j0, j1) (tile edges a multiple of 2**m)."""
    f = 1 << m
    px0, px1, py0, py1 = i0 * TILE // f, i1 * TILE // f, j0 * TILE // f, j1 * TILE // f
    out = np.zeros((py1 - py0, px1 - px0, 4), np.uint8)
    for j in range(py0 // TILE, (py1 - 1) // TILE + 1):
        for i in range(px0 // TILE, (px1 - 1) // TILE + 1):
            t = st.tile(m, i, j)
            ya, yb = max(j * TILE, py0), min((j + 1) * TILE, py1)
            xa, xb = max(i * TILE, px0), min((i + 1) * TILE, px1)
            out[ya - py0:yb - py0, xa - px0:xb - px0] = t[ya - j * TILE:yb - j * TILE, xa - i * TILE:xb - i * TILE]
    return out


def water_ramps(st, polys, box):
    """Water colour and alpha by signed distance from land (px, -20..299), from the open water of
    a painted sea clear of any parchment (and of the torn edge's shadow)."""
    x0, y0, x1, y1 = box
    b = st.box(x0, y0, x1, y1)
    rgb, alpha = b[..., :3].astype(np.float32), b[..., 3].astype(np.float32)
    px0, py0 = map_to_px(x0, y1)
    land = raster(polys, lambda r: r[1] == "land" and r[0] != "lakes", int(px0), int(py0), b.shape[1], b.shape[0], K0)
    sea = ~land
    clear = edt(~(sea & (alpha < 128))) / K0 > 6           # off water drawn as parchment (and its shadow)
    signed = np.where(sea, edt(sea), -edt(land))
    sb = np.clip(np.round(signed), -20, 299).astype(np.int16)
    bins = np.arange(-20, 300)
    ok = sea & (alpha > 200) & clear
    col, al = np.zeros((len(bins), 3)), np.zeros(len(bins))
    for k, v in enumerate(bins):
        sel = ok & (sb == v)
        col[k] = rgb[sel].mean(0) if sel.sum() > 20 else col[k - 1]
        sel_a = clear & (sb == v)
        al[k] = alpha[sel_a].mean() if sel_a.sum() > 20 else (al[k - 1] if k else 0)
    first = 20 + int(np.argmax(col[20:].any(1)))              # the shore bins may hold no open water
    col[:first] = col[first]
    col = ndimage.uniform_filter1d(col, 9, axis=0).astype(np.float32)
    ref_mean = rgb[ok & (signed > 15)].mean(0)
    return col, al, ref_mean


def water_patches(st, box, rng, n=40, Pw=120):
    """High-passed windows of a painted sea that are open water 20 px off any shore."""
    wbox = st.box(*box)
    wsrc = wbox[..., :3].astype(np.float32)
    wres = wsrc - ndimage.gaussian_filter(wsrc, (25, 25, 0))
    open_w = ndimage.binary_erosion(wbox[..., 3] == 255, np.ones((41, 41)))
    patches = []
    wy, wx = np.nonzero(open_w[:-Pw, :-Pw])
    for _ in range(4000):
        k = rng.integers(len(wy))
        py, px = wy[k], wx[k]
        if open_w[py:py + Pw, px:px + Pw].all():
            patches.append(wres[py:py + Pw, px:px + Pw])
            if len(patches) == n:
                break
    assert patches, f"no open-water patch in {box}"
    return patches


def raster(polys, pred, px0, py0, w, h, k):
    """Regions picked by pred((name, kind)) filled into a w x h window whose top-left pixel is
    (px0, py0) on the grid of k px per map unit."""
    im = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(im)
    for key, geoms in polys:
        if not pred(key):
            continue
        for g in geoms:
            dr.polygon([((x + 1280) * k - px0, (640 - y) * k - py0) for x, y in g.exterior.coords], fill=255)
            for hole in g.interiors:
                dr.polygon([((x + 1280) * k - px0, (640 - y) * k - py0) for x, y in hole.coords], fill=0)
    return np.asarray(im) > 127


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions-esf", type=Path, required=True)
    ap.add_argument("--supertexture", type=Path, default=Path("../data/supertexture.pack"))
    ap.add_argument("--main-pack", type=Path, default=Path("../data/main.pack"),
                    help="pack holding the vanilla heightmaps\\default.tga")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-mountains", action="store_true")
    ap.add_argument("--no-relief", action="store_true", help="leave the heightmap vanilla")
    ap.add_argument("--region-pack", type=Path,
                    help="the region build's new_regions.pack: palms are added to its campaign trees (else "
                         "vanilla's) and a copy carrying the same trees file is written next to the map pack")
    ap.add_argument("--no-palms", action="store_true")
    ap.add_argument("--seed", type=int, default=1700)
    a = ap.parse_args()
    t_start = time.time()
    rng = np.random.default_rng(a.seed)
    st = Supertexture(a.supertexture)
    W, H = (I1 - I0) * TILE, (J1 - J0) * TILE
    Wh, Hh = W // 8, H // 8
    PX0, PY0 = I0 * TILE, J0 * TILE                    # canvas origin, mip-0 px

    def cx(x): return map_to_px(x, 0)[0] - PX0
    def cy(y): return map_to_px(0, y)[1] - PY0
    xh, yh = px_to_map(np.arange(Wh)[None, :] + 0.5 + PX0 // 8, np.arange(Hh)[:, None] + 0.5 + PY0 // 8, 3)
    xh, yh = np.broadcast_to(xh, (Hh, Wh)), np.broadcast_to(yh, (Hh, Wh))

    # ── regions ──
    regions_root = ESFReader(a.regions_esf).read_root()
    m = Mesh(regions_root)
    # every settlement and slot: palms keep clear of them (Medina's oasis is also its town)
    sites = []
    for r_ in regions_root.children[3].children[3].children:
        sas = sas_of(r_)
        if sas is None or not sas.children:
            continue
        sites.append(tuple(sas.children[0].value))
        sites += [tuple(sl[2].value) for sl in sas.children[2].children]
    cx0, cy0 = px_to_map(PX0, PY0 + H)
    cx1, cy1 = px_to_map(PX0 + W, PY0)
    polys = []
    for ri in range(len(m.regs)):
        geoms = []
        for ai in range(len(m.areas(ri))):
            try:
                p = m.area_polygon(ri, ai)
            except Exception:
                continue
            for g in getattr(p, "geoms", [p]):
                if not g.is_empty and g.bounds[2] > cx0 and g.bounds[0] < cx1 and g.bounds[3] > cy0 and g.bounds[1] < cy1:
                    geoms.append(g)
        if geoms:
            polys.append(((m.name(ri), m.regs[ri][1].value), geoms))
    is_land = lambda r: r[1] == "land" and r[0] != "lakes"                    # noqa: E731
    land0 = raster(polys, is_land, PX0, PY0, W, H, K0)
    void0 = raster(polys, lambda r: r[0] in VOID_LAND, PX0, PY0, W, H, K0)
    river0 = raster(polys, lambda r: r[1] == "river", PX0, PY0, W, H, K0)

    # ════ the whole canvas on the heightmap grid ════
    def hgrid(pred):
        return raster(polys, pred, PX0 // 8, PY0 // 8, Wh, Hh, HK)
    land_h, void_h = hgrid(is_land), hgrid(lambda r: r[0] in VOID_LAND)
    red_h = hgrid(lambda r: r[0] == "red_sea") & ~land_h
    sea_h = ~land_h
    # near the void regions over land (straight-line distance crossed the Strait of Hormuz); 15
    # units, since Morocco's region runs up to 10 units south of the torn edge
    d_void_h = over_land(void_h & land_h, land_h, 50)
    near_void_h = d_void_h <= 15 * HK
    # the light-grey parchment test keeps to 6 units: at 15 it took Egypt's pale painted desert
    # (and the Nile valley with it) for parchment
    seed_void_h = d_void_h <= 6 * HK
    _, (_, nxh) = ndimage.distance_transform_edt(~red_h, return_indices=True)
    east_h = nxh < np.arange(Wh)[None, :]                    # nearest Red Sea water lies west
    del nxh
    d_red_h = edt(~red_h) / HK
    d_gulf_h = edt(~(sea_h & ~red_h & (xh > 300) & (yh < 215))) / HK
    tilt = d_gulf_h / np.maximum(d_gulf_h + d_red_h, 1e-3)  # 1 by the Red Sea, 0 by the Gulf
    d_sea_h = edt(land_h) / HK
    hnoise = lambda cell, sigma: Noise((Hh, Wh), rng, cell, sigma).n[:Hh, :Wh] if cell == 1 else \
        Noise((Hh, Wh), rng, cell, sigma).at(0, Wh)                         # noqa: E731
    nz = hnoise(8, 2)

    # sand: the Rub' al Khali and the Dahna arc (Arabia), the ergs and sand seas (Sahara)
    rub = (east_h * np.clip((166 - yh + 6 * nz) / 8, 0, 1) * np.clip((0.72 - tilt + 0.1 * nz) / 0.12, 0, 1)
           * np.clip((d_sea_h - 6) / 6, 0, 1) * (xh > 300))
    dahna = (east_h * np.clip(1 - np.abs(tilt - 0.47 + 0.05 * nz) / 0.035, 0, 1)
             * np.clip((200 - yh) / 3, 0, 1) * np.clip((yh - 152) / 6, 0, 1) * np.clip((d_sea_h - 3) / 3, 0, 1))
    ergs = np.zeros((Hh, Wh), np.float32)
    nz2 = hnoise(4, 2)
    nz3 = hnoise(2, 1.5)
    for _, x, y, rx, ry, ang in SAND_SEAS:
        r = ellipse_r(xh, yh, x, y, 1.3 * rx, 1.3 * ry, ang) + 0.45 * nz2 + 0.2 * nz3
        ergs = np.maximum(ergs, np.clip((1 - r) / 0.6, 0, 1) ** 1.5)
    # ragged, not a flat blob: thinned in patches, at most 0.7 sand
    ergs *= 0.7 * np.clip(0.75 + 0.5 * nz3, 0, 1) * ~east_h * np.clip((d_sea_h - 1) / 2, 0, 1)
    sand_h = ndimage.gaussian_filter(np.maximum.reduce([rub, dahna, ergs]), 3).astype(np.float32)

    # mountains: bands by the Red Sea and the Gulf of Oman, the Saharan massifs
    mw_h = np.zeros((Hh, Wh), np.float32)
    dark_h = np.zeros((Hh, Wh), np.float32)                  # the massifs are dark volcanic rock
    lift_h = np.ones((Hh, Wh), np.float32)
    uplift_h = np.zeros((Hh, Wh), np.float32)
    if not a.no_mountains:
        wander = 1.2 * hnoise(2, 3)                           # ranges' edges wander, not ribbons
        d_rs = d_red_h + wander
        yemen = np.clip((150 - yh) / 12, 0, 1)                # the highlands widen southward
        hejaz = east_h * (yh < 196) * band(d_rs, 1.2, 6.5 + 5.5 * yemen)
        sudan = ~east_h * (yh < 192) * (yh > 140) * band(d_rs, 0.8, 5.0)
        d_om = edt(~(sea_h & (xh > 401) & (yh < 192))) / HK + 0.8 * wander     # the Gulf of Oman
        hajar = (xh > 396) * (yh > 158) * (yh < 193) * band(d_om, 0.6, 5.0)
        massif, m_lift = np.zeros((Hh, Wh), np.float32), np.zeros((Hh, Wh), np.float32)
        big_warp = hnoise(16, 2)
        for _, x, y, rx, ry, ang, height in MASSIFS:
            # outlines warped at two scales and soft: plain ellipses read as dark ovals
            r = ellipse_r(xh, yh, x, y, rx, ry, ang) + 0.45 * big_warp + 0.25 * nz2 + 0.15 * wander
            core = np.clip((1 - r) / 0.6, 0, 1) ** 1.3
            m_lift = np.where(core > massif, height, m_lift)
            massif = np.maximum(massif, core)
            uplift_h = np.maximum(uplift_h, 14 * height * np.clip((1.9 - r) / 0.9, 0, 1))
        dark_h = ndimage.gaussian_filter(massif * land_h, 3).astype(np.float32)
        rng_mask = np.maximum.reduce([hejaz, sudan, hajar, massif]) * land_h * near_void_h   # not Iran's coast
        lift_h = np.where(massif >= np.maximum.reduce([hejaz, sudan, hajar]), np.maximum(m_lift, 0.5),
                          np.where(east_h, 1.0, 0.75))                              # the Red Sea Hills are lower
        patchy = np.clip(0.8 + 0.6 * hnoise(2, 3), 0, 1)
        mw_h = np.clip(1.25 * ndimage.gaussian_filter(rng_mask * patchy, 1.5), 0, 1).astype(np.float32)
        print(f"mountains: {(mw_h > 0.3).sum() / HK ** 2:,.0f} square map units")

    # parchment and painted land, roughly, on the mip-3 picture (the same grid)
    t3 = mip_canvas(st, 3, I0, I1, J0, J1)
    rgb3, a3 = t3[..., :3].astype(np.float32), t3[..., 3].astype(np.float32)
    del t3
    chroma3, lum3 = rgb3.max(2) - rgb3.min(2), rgb3.mean(2)
    parch3 = land_h & ndimage.binary_dilation(seed_void_h, iterations=2) & (chroma3 < 50) & (lum3 > 135) & (a3 < 128)
    parch3 = ndimage.binary_closing(parch3, iterations=2) | (void_h & land_h)
    # (as in the strips; else the edge's grey shadow is carried in as land colour)
    parch3 = grow_parchment(parch3, land_h & near_void_h & (a3 < 128), chroma3, lum3, HK, xh < CREAM_X)
    d_par3 = edt(~parch3) / HK
    # the desert colour: a membrane of the painted land clear of the edge's shadow, fading over
    # 15 units to gravel / sand in Arabia, to the painted desert along the edge at that longitude
    # in Africa (Morocco's, Algeria's and Libya's carried south), sand over the sand seas
    known_h = land_h & ~parch3 & (a3 < 128) & (d_par3 > 7) & (yh > FRAME_Y + 2)
    base_h = ndimage.gaussian_filter(push_pull(rgb3, known_h.astype(float)), (4, 4, 0))
    srcs = [st.box(*b)[..., :3].astype(np.float32) for b in DESERT_SRC]
    sand_ref = srcs[0].reshape(-1, 3).mean(0) * np.array([1.03, 0.97, 0.88])   # redder, like the Rub' al Khali
    gravel_ref = srcs[1].reshape(-1, 3).mean(0)
    edge_band = known_h & (d_par3 < 20) & ~east_h
    bins = np.arange(0, Wh + 32, 32)
    prof = np.full((len(bins) - 1, 3), np.nan)
    for k in range(len(bins) - 1):
        sel = edge_band[:, bins[k]:bins[k + 1]]
        if sel.sum() > 30:
            prof[k] = np.median(rgb3[:, bins[k]:bins[k + 1]][sel], 0)
    good = ~np.isnan(prof[:, 0])
    prof = np.stack([np.interp(np.arange(len(prof)), np.nonzero(good)[0], prof[good, c]) for c in range(3)], 1)
    # interpolated between bin centres and smoothed (stepped bins showed as vertical bands), and
    # half desert gravel: Morocco's green carried south turned the western Sahara olive
    cols = np.stack([np.interp(np.arange(Wh), (bins[:-1] + 16), prof[:, c]) for c in range(3)], 1)
    cols = ndimage.gaussian_filter1d(cols, 48, axis=0)
    africa_far = (0.5 * cols + 0.5 * gravel_ref)[None, :, :]
    s3 = sand_h[..., None]
    # broad darker gravel plains (hammada) break up the Sahara's one tone
    ham = (np.clip((hnoise(24, 2) - 0.05) / 0.35, 0, 1) * 0.55 * (1 - sand_h))[..., None]
    africa_far = africa_far * (1 - ham) + gravel_ref * np.array([0.8, 0.76, 0.72]) * ham
    far = np.where(east_h[..., None], gravel_ref * (1 - s3) + sand_ref * s3,
                   africa_far * (1 - s3) + sand_ref * s3).astype(np.float32)
    del ham
    w_int = np.clip(edt(~known_h) / HK / 15, 0, 1)[..., None]
    base_col_h = (base_h * (1 - w_int) + far * w_int).astype(np.float32)
    if DEBUG is not None:
        DEBUG["global"] = dict(base_col_h=base_col_h, rgb3=rgb3, known_h=known_h, parch3=parch3, d_par3=d_par3,
                               near_void_h=near_void_h, land_h=land_h)
    del base_h, far, w_int
    print(f"desert refs: sand {sand_ref.round()}, gravel {gravel_ref.round()}, "
          f"africa {np.round(prof.min(0))}..{np.round(prof.max(0))}")

    # water: ramps from the painted gulf (east) and Atlantic (west); a tint membrane of open water
    gulf_col, gulf_al, gulf_mean = water_ramps(st, polys, GULF_REF)
    atl_col, atl_al, atl_mean = water_ramps(st, polys, ATL_REF)
    known_w_h = sea_h & (a3 > 200) & (edt(~(parch3 | (sea_h & (a3 < 128)))) / HK > 6) & (edt(sea_h) > 2)
    tint_src = ndimage.gaussian_filter(push_pull(rgb3, known_w_h.astype(float)), (3, 3, 0))
    ref_mean = np.where((xh < ATL_X)[..., None], atl_mean, gulf_mean)
    tint_h = np.clip(tint_src / ref_mean, 0.6, 1.6).astype(np.float32)
    del tint_src, ref_mean, chroma3, lum3

    # the Atlantic margin's new torn edge: ragged at three scales, like vanilla's
    rag = [Noise((H, W), rng, 64, 3), Noise((H, W), rng, 8, 1.5), Noise((H, W), rng, 2, 1)]
    n_feather, n_var1, n_var2 = Noise((H, W), rng, 64, 3), Noise((H, W), rng, 32, 4), Noise((H, W), rng, 8, 2)
    n_var0 = Noise((H, W), rng, 256, 2)

    # ── palms ──
    palms = []
    if not a.no_palms:
        def fits(x, y, max_mw):
            px, py = int(cx(x)), int(cy(y))
            return (0 <= px < W and 0 <= py < H and land0[py, px] and y > FRAME_Y + 1 and x > ATL_EDGE_X + 3
                    and mw_h[py // 8, px // 8] <= max_mw and all(math.dist((x, y), q) >= 0.45 for q in palms)
                    and all(math.dist((x, y), q) >= SITE_CLEAR for q in sites))
        for _, ox, oy, n, spread in OASES:
            placed = 0
            for _ in range(300):
                if placed == n:
                    break
                x, y = ox + rng.normal(0, spread), oy + rng.normal(0, spread)
                if fits(x, y, 0.6):
                    palms.append((x, y))
                    placed += 1
        for _, (bx0, by0, bx1, by1), n, (lo, hi) in PALM_SHORES:
            px0, px1, py0, py1 = int(cx(bx0)), int(cx(bx1)), int(cy(by1)), int(cy(by0))
            lb = land0[py0:py1, px0:px1]
            dl = edt(lb) / K0
            cand = np.argwhere(lb & (dl >= lo) & (dl <= hi))
            if bx0 >= 260:                                   # Arabia's shore, not Africa's
                cand = cand[east_h[(cand[:, 0] + py0) // 8, (cand[:, 1] + px0) // 8]]
            placed = 0
            for k in rng.permutation(len(cand))[:600]:
                if placed == n:
                    break
                x, y = px_to_map(cand[k][1] + px0 + PX0 + 0.5, cand[k][0] + py0 + PY0 + 0.5)
                if fits(x, y, 0.85):
                    palms.append((x, y))
                    placed += 1
        nile = st.box(217, 185, 227, 205)[..., :3].reshape(-1, 3).astype(np.float32)
        green = nile[(nile[:, 1] >= 0.95 * nile[:, 0]) & (nile[:, 1] > 1.2 * nile[:, 2])]    # not the river
        oasis_col = np.median(green, 0) if len(green) > 500 else np.array([118, 120, 72], np.float32)
        print(f"palms: {len(palms)} clusters; oasis ground {oasis_col.round()}")

    # splats, laid on the whole canvas
    fine = [s - ndimage.gaussian_filter(s, (4, 4, 0)) for s in srcs]
    broad = [s - ndimage.gaussian_filter(s, (48, 48, 0)) for s in srcs]
    sp_sand_f, sp_grav_f = Splat([fine[0]], (H, W), a.seed, 1), Splat([fine[1]], (H, W), a.seed, 2)
    sp_sand_b = Splat([broad[0]], (H, W), a.seed, 3, P=288, S=180)
    sp_grav_b = Splat([broad[1]], (H, W), a.seed, 4, P=288, S=180)
    msrc = st.box(*MOUNTAIN_SRC)[..., :3].astype(np.float32)
    hm = np.frombuffer(pack_file(a.main_pack, HEIGHTMAP), np.uint8)
    tga_header, hm = bytes(hm[:18]), hm[18:].reshape(4096, 8192)[::-1]    # bottom-up rows
    assert tga_header[2] == 3 and tga_header[12:17] == bytes([0, 32, 0, 16, 8]), "not an 8192x4096 8-bit TGA"

    def hbox(x0, y0, x1, y1):
        return hm[int((640 - y1) * HK):int((640 - y0) * HK), int((x0 + 1280) * HK):int((x1 + 1280) * HK)].astype(np.float32)

    # the Zagros heights ride along with its painted folds, so ridges and relief line up
    zh = hbox(*MOUNTAIN_SRC)
    zh = np.asarray(Image.fromarray(zh, "F").resize((msrc.shape[1], msrc.shape[0]), Image.BILINEAR))
    msrc4 = np.dstack([msrc - ndimage.gaussian_filter(msrc, (10, 10, 0)), zh - ndimage.gaussian_filter(zh, 2 * K0)])
    sp_ridge = Splat([msrc4], (H, W), a.seed, 5)
    sp_wgulf = Splat(water_patches(st, WATER_SRC, rng), (H, W), a.seed, 6, P=100, S=64)
    sp_watl = Splat(water_patches(st, ATL_WATER_SRC, rng), (H, W), a.seed, 7, P=100, S=64)
    print(f"global fields ready ({time.time() - t_start:.0f} s)")

    # ════ full resolution, strip by strip ════
    orig = st.canvas(I0, I1, J0, J1)
    out = orig.copy()
    weight_core_h = np.zeros((Hh, Wh), np.float32)
    ridge_h = np.zeros((Hh, Wh), np.float32)
    parch_h = np.zeros((Hh, Wh), np.float32)
    parch_paint_h = np.zeros((Hh, Wh), np.float32)
    bare_h = np.zeros((Hh, Wh), np.float32)
    nt = I1 - I0
    nv_dil_h = ndimage.binary_dilation(near_void_h, iterations=2)
    seed_dil_h = ndimage.binary_dilation(seed_void_h, iterations=2)

    def down(m_):
        return m_.reshape(m_.shape[0] // 8, 8, m_.shape[1] // 8, 8).mean((1, 3), dtype=np.float32)

    for c0 in range(0, nt, CHUNK):
        if DEBUG is not None and c0 not in DEBUG.get("only", [c0]):
            continue
        c1 = min(c0 + CHUNK, nt)
        w0, w1 = max(c0 - HALO, 0) * TILE, min(c1 + HALO, nt) * TILE      # window, canvas px
        k0, k1 = c0 * TILE - w0, c1 * TILE - w0                           # its core, window px
        ww = w1 - w0
        hs = slice(w0 // 8, w1 // 8)
        rgb = orig[:, w0:w1, :3].astype(np.float32)
        alpha = orig[:, w0:w1, 3].astype(np.float32)
        xs, ys = px_to_map(np.arange(ww)[None, :] + 0.5 + PX0 + w0, np.arange(H)[:, None] + 0.5 + PY0)
        xs, ys = np.broadcast_to(xs, (H, ww)), np.broadcast_to(ys, (H, ww))
        land, void = land0[:, w0:w1], void0[:, w0:w1]
        near_river = edt(~river0[:, w0:w1]) / K0 < 4
        sea = ~land
        frame_w = np.clip((ys - FRAME_Y) / 0.25, 0, 1)
        near_void = up(nv_dil_h[:, hs], 8, (H, ww)) > 0.5
        seed_void = up(seed_dil_h[:, hs], 8, (H, ww)) > 0.5
        east = ndimage.gaussian_filter(up(east_h[:, hs], 8, (H, ww)), 8) > 0.5
        sand = ndimage.gaussian_filter(up(sand_h[:, hs], 8, (H, ww)), 8)

        # ── parchment ──
        chroma, lum = rgb.max(2) - rgb.min(2), rgb.mean(2)
        parch = land & seed_void & (chroma < 50) & (lum > 135) & (alpha < 128)
        parch = ndimage.binary_fill_holes(ndimage.binary_closing(parch, np.ones((7, 7)), iterations=2) | (void & land))
        lab, n = ndimage.label(parch)
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        # water by other coasts (Iran's) stays vanilla: its painted beach lies outside the region
        # polygon, so it is 'sea' drawn as land and was repainted as water, darkening the shore
        d_other = edt(~(land & ~near_void)) / K0
        by_other = (d_other < edt(~(land & near_void)) / K0) * np.clip(1.5 - d_other, 0, 1)
        del d_other
        # the Atlantic's margin west of the new edge and north of ATL_TOP stays parchment
        bare = ((xs < ATL_EDGE_X + 2.0 * rag[0].at(w0, w1) + 0.5 * rag[1].at(w0, w1) + 0.25 * rag[2].at(w0, w1))
                | ((xs < -150) & (ys > ATL_TOP)))
        paint_sea = sea & (alpha < 128) & (ys > FRAME_Y - 1) & (by_other == 0) & ~bare   # water drawn as parchment
        touch = ndimage.binary_dilation(paint_sea, np.ones((5, 5)))
        islands = [k + 1 for k in range(n) if sizes[k] <= 20000 and touch[lab == k + 1].any()] if n < 5000 else []
        parch = np.isin(lab, list(1 + np.nonzero(sizes > 20000)[0]) + islands)
        del lab
        parch |= (land & seed_void & (chroma < 50) & (lum > 120) & (alpha < 128)
                  & ndimage.binary_dilation(paint_sea, np.ones((25, 25))))
        parch = grow_parchment(parch, land & near_void & (alpha < 128), chroma, lum, K0, xs < CREAM_X)
        del chroma
        bare_parch = (parch | (sea & (alpha < 128))) & bare & (ys > FRAME_Y)
        parch_paint = parch & ~bare
        paint_land = parch_paint & land & (ys > FRAME_Y - 1)
        target = paint_sea | paint_land

        # ── the torn-paper shadow (painted on the painted side): local brightness gain ──
        d_edge = edt(~parch_paint)
        d_west = edt(~bare_parch)
        lum_s = ndimage.gaussian_filter(lum, 10)
        del lum
        # 8 units, refs at 7-9: Morocco's shadow runs deeper than the 4-5 units elsewhere
        taper = np.clip((7 * K0 - d_edge) / (1.5 * K0), 0, 1)
        kind = land & (alpha < 128)
        # only where the nearest parchment gets painted; vanilla keeps its shadow by bare parchment
        shadow = kind & ~parch & near_void & (d_edge < 8 * K0) & (ys > FRAME_Y + 2) & (d_edge < d_west)
        ref_px = kind & ~parch & (d_edge > 7 * K0) & (d_edge < 9 * K0)
        ref_local = smooth_fill(lum_s[..., None], ref_px, sigma=4)[..., 0]
        gain = 1 + (np.clip(ref_local / np.maximum(lum_s, 1), 1.0, 2.2) - 1) * taper
        del ref_local, lum_s, taper, kind, ref_px
        rgb = np.where(shadow[..., None], np.clip(rgb * gain[..., None], 0, 255), rgb)
        del gain
        grown = target | (land & (d_edge <= 5) & (ys > FRAME_Y))     # the paper edge itself
        weight = np.clip((10 - ndimage.distance_transform_edt(~grown)) / 10, 0, 1).astype(np.float32) * frame_w
        weight_core = weight.copy()
        # feather the old torn edge ~3 units into vanilla land, wobbling: a 10 px blend kept its
        # ragged outline as a crisp seam along Jawf's southern border. The Nile's green is left alone
        d_g = edt(~grown) / K0 + 0.8 * n_feather.at(w0, w1)
        # (green by a river only: Morocco's greenish steppe kept its dark ragged edge)
        veg = ndimage.gaussian_filter(np.clip((rgb[..., 1] / np.maximum(rgb[..., 0], 1) - 0.92) / 0.08, 0, 1)
                                      * near_river, 8)
        feather = np.clip((3.5 - d_g) / 3.0, 0, 1) * land * near_void * (d_edge < d_west) * (1 - veg)
        weight = np.maximum(weight, feather.astype(np.float32) * frame_w)
        del d_g, veg, feather
        # painted water carries the torn edge's shadow too (darkest where the edge crosses the
        # Persian Gulf): repaint water within 6 units of any parchment, land or sea, fading 4 -> 6
        d_any = edt(~(parch_paint | paint_sea)) / K0
        shadow_w = sea & (alpha > 200) & (d_any < 6) & (ys > FRAME_Y)
        weight = np.maximum(weight, np.where(shadow_w, np.clip((6 - d_any) / 2, 0, 1) * (1 - by_other), 0) * frame_w)
        del by_other, shadow_w, d_any, grown

        # ── desert ──
        base_col = ndimage.uniform_filter(up(base_col_h[:, hs], 8, (H, ww)), size=(16, 16, 1))
        s3 = sand[..., None]
        # the broad patches at 0.35: at 0.6 their dune crescents repeated across the Sahara
        detail = ((sp_sand_f.render(w0, w1) + 0.35 * sp_sand_b.render(w0, w1)) * s3
                  + (sp_grav_f.render(w0, w1) + 0.35 * sp_grav_b.render(w0, w1)) * (1 - s3))
        var = 1 + 0.06 * n_var0.at(w0, w1) + 0.05 * n_var1.at(w0, w1) + 0.03 * n_var2.at(w0, w1)
        desert = np.clip(base_col * var[..., None] + detail, 0, 255)
        del base_col, detail, var, s3

        # ── mountains ──
        mw = np.clip(ndimage.gaussian_filter(up(mw_h[:, hs], 8, (H, ww)), 6), 0, 1)
        if not a.no_mountains and mw.max() > 0:
            ridges4 = sp_ridge.render(w0, w1)
            ridges = ridges4[..., :3]
            ridge_h[:, (w0 + k0) // 8:(w0 + k1) // 8] = down(ridges4[:, k0:k1, 3])
            del ridges4
            # the desert round them, shaded by the ridges: a separate dark tone stood out red-brown
            dark = 1 - 0.12 * ndimage.gaussian_filter(up(dark_h[:, hs], 8, (H, ww)), 6)[..., None]
            mountain = np.clip(desert * np.array([0.92, 0.89, 0.86], np.float32) * dark
                               + 1.3 * ridges.mean(2, keepdims=True) + 0.3 * ridges, 0, 255)
            del dark
            del ridges
            land_rgb = desert * (1 - mw[..., None]) + mountain * mw[..., None]
            del mountain
        else:
            land_rgb = desert
        del desert

        # ── water ──
        d_land = edt(land)
        signed = np.where(sea, edt(sea), -d_land)
        sb = np.clip(np.round(signed), -20, 299).astype(np.int16) + 20
        atl = xs < ATL_X
        water = np.where(atl[..., None], atl_col[sb], gulf_col[sb])
        new_alpha = np.where(atl, np.interp(signed, np.arange(-20, 300), atl_al),
                             np.interp(signed, np.arange(-20, 300), gulf_al)).astype(np.float32)
        del sb, signed
        if (sea & (weight > 0)).any():
            wtex = (sp_watl.render(w0, w1) if atl.any() else 0) * atl[..., None] + \
                   (sp_wgulf.render(w0, w1) if (~atl).any() else 0) * ~atl[..., None]
            water = np.clip(water + 0.8 * ndimage.gaussian_filter(wtex, (2, 2, 0)), 0, 255)
            del wtex
        # tint by the nearest painted water (the Red Sea is bluer than the gulf the ramp came from)
        tint = ndimage.uniform_filter(up(tint_h[:, hs], 8, (H, ww)), size=(16, 16, 1))
        water = np.clip(water * tint, 0, 255)
        del tint, atl

        # ── composite ──
        shore = np.where(xs[..., None] < ATL_X, atl_col[20], gulf_col[20])
        paint_rgb = np.where(sea[..., None], water, land_rgb)
        del water, land_rgb
        fringe = (np.clip(1 - d_land / 6, 0, 1) * land)[..., None]      # coast edge leans to the shore colour
        paint_rgb = paint_rgb * (1 - 0.35 * fringe) + shore * 0.35 * fringe
        del fringe, shore, d_land
        w3 = weight[..., None]
        out_rgb = rgb * (1 - w3) + paint_rgb * w3
        del paint_rgb, w3
        # the new torn edge's shadow, on what we painted or brightened (vanilla's: 0.55 over ~4 units)
        mine = (weight > 0) | shadow
        shade = 1 - 0.45 * np.clip(1 - d_west / K0 / 4, 0, 1) ** 1.6
        out_rgb = np.where(mine[..., None], out_rgb * shade[..., None], out_rgb)
        del shade, mine
        # palms: a darker green on the ground under them
        if palms:
            tw = np.zeros((H, ww), np.float32)
            r = int(1.5 * K0)
            g1 = np.arange(-r, r + 1)
            blob = np.exp(-((g1[:, None] ** 2 + g1[None, :] ** 2) / (2 * (0.55 * K0) ** 2)))
            for x, y in palms:
                px, py = int(cx(x)) - w0, int(cy(y))
                if -r <= px < ww + r:
                    ya, yb, xa, xb = max(py - r, 0), min(py + r + 1, H), max(px - r, 0), min(px + r + 1, ww)
                    tw[ya:yb, xa:xb] = np.maximum(tw[ya:yb, xa:xb], blob[ya - py + r:yb - py + r, xa - px + r:xb - px + r])
            tw *= 0.3 * land
            out_rgb = out_rgb * (1 - tw[..., None]) + oasis_col * tw[..., None]
            del tw
        if DEBUG is not None:
            DEBUG[c0] = {k_: v_[::4, ::4].copy() for k_, v_ in dict(
                shadow=shadow, weight=weight, d_edge=d_edge, d_west=d_west, near_void=near_void, parch=parch,
                land=land, alpha=alpha).items()}
            DEBUG[c0]["w0"] = w0
        res = np.dstack([np.clip(out_rgb, 0, 255),
                         np.clip(alpha * (1 - weight) + new_alpha * weight, 0, 255)]).astype(np.uint8)
        out[:, w0 + k0:w0 + k1] = res[:, k0:k1]
        hk = slice((w0 + k0) // 8, (w0 + k1) // 8)
        weight_core_h[:, hk] = down(weight_core[:, k0:k1])
        parch_h[:, hk] = down(parch[:, k0:k1].astype(np.float32))
        parch_paint_h[:, hk] = down(parch_paint[:, k0:k1].astype(np.float32))
        bare_h[:, hk] = down(bare_parch[:, k0:k1].astype(np.float32))
        print(f"strip x {px_to_map(PX0 + w0 + k0, 0)[0]:.0f}..{px_to_map(PX0 + w0 + k1, 0)[0]:.0f}: "
              f"parchment land {paint_land[:, k0:k1].sum():,} px, sea {paint_sea[:, k0:k1].sum():,} px "
              f"({time.time() - t_start:.0f} s)")
        del (res, out_rgb, rgb, alpha, weight, weight_core, parch, parch_paint, paint_land, paint_sea, target,
             bare, bare_parch, shadow, d_edge, d_west, new_alpha, mw, near_void, east, sand, frame_w, xs, ys,
             near_river, seed_void)

    # ── relief ──
    files = []
    r0, c0_ = PY0 // 8, PX0 // 8
    vh = hm[r0:r0 + Hh, c0_:c0_ + Wh].astype(np.float32)
    out_h = vh.astype(np.uint8)
    if not a.no_relief:
        par_h = parch_paint_h > 0.3
        d_par_h = over_land(par_h, land_h, 17) / HK       # over land: not across to Iran
        d_anypar_h = edt(~(parch_h > 0.3)) / HK
        d_west_h = edt(~(bare_h > 0.5)) / HK
        # vanilla heights clear of its torn-edge drop (~3 units), carried in as a membrane
        trusted = land_h & (d_anypar_h > 4) & (vh > 0)
        base = push_pull(ndimage.gaussian_filter(vh, 3)[..., None], trusted.astype(float))[..., 0]
        base = ndimage.gaussian_filter(base, 6)
        big = hnoise(32, 2)
        level = np.where(east_h, 15 + 38 * tilt - 12 * sand_h,                      # Arabia tilts down to the Gulf
                         26 + 8 * big - 7 * sand_h + uplift_h)                      # the Sahara, raised round its massifs
        w_far = np.clip(edt(~trusted) / HK / 15, 0, 1)
        plain = base * (1 - w_far) + level * w_far
        ramp = np.clip(d_sea_h / 5, 0, 1) ** 0.8
        plain = 3 + (plain - 3) * ramp
        dsrc = [hbox(*b) for b in DESERT_SRC]
        micro = Splat([(d - ndimage.gaussian_filter(d, 4))[..., None] for d in dsrc], (Hh, Wh), a.seed, 8,
                      P=32, S=20).render(0, Wh)[..., 0]
        micro *= 1 + 0.6 * sand_h                                      # dune fields are rougher
        mtn = mw_h * lift_h * (32 + 0.75 * np.maximum(ridge_h, -27)) * np.clip(d_sea_h / 2, 0, 1)
        new_h = plain + micro * ramp + mtn
        # below the snow line (world y 1.4 = height ~144: peaks of 190 came out white)
        new_h = np.where(new_h > SNOW_SAFE - 30, SNOW_SAFE - 30 + (new_h - SNOW_SAFE + 30) * 0.35, new_h)
        new_h = np.where(land_h, np.clip(new_h, 1, SNOW_SAFE), 0)
        new_h *= np.clip((yh - FRAME_Y) / 2.5, 0, 1) * np.clip(d_west_h / 2.5, 0, 1)
        # painted ground takes the new heights; vanilla's drop at the old edge blends into them
        w_h = np.maximum(weight_core_h * near_void_h, land_h * np.clip((5 - d_par_h) / 2, 0, 1) * (d_par_h < d_west_h))
        out_h = np.round(vh * (1 - w_h) + new_h * w_h).clip(0, 255).astype(np.uint8)
        sel = land_h & (w_h > 0.5)
        print(f"relief: {(w_h > 0).sum():,} heightmap px changed; new land p50 {np.median(new_h[sel]):.0f}, "
              f"p95 {np.percentile(new_h[sel], 95):.0f}, max {new_h.max():.0f}")
        hm_new = hm.copy()
        hm_new[r0:r0 + Hh, c0_:c0_ + Wh] = out_h
        files.append((HEIGHTMAP, tga_header + np.ascontiguousarray(hm_new[::-1]).tobytes()))

    # ── palm instances in the campaign trees (deep_dive 10.19) ──
    a.out.mkdir(parents=True, exist_ok=True)
    if palms:
        src = [pk for pk in ([a.region_pack] if a.region_pack else []) + list(VANILLA_TREE_PACKS[::-1]) if pk.exists()]
        data = None
        for pk in src:
            try:
                data = pack_file(pk, TREES_PATH)
                break
            except KeyError:
                pass
        assert data is not None, f"{TREES_PATH} in none of {src}"
        header, types = read_trees(data)
        by_name = {t[0][2:].decode("utf-16-le"): t for t in types}
        for x, y in palms:
            hv = float(out_h[int((640 - y) * HK) - r0, int((x + 1280) * HK) - c0_])
            by_name[PALMS[rng.integers(len(PALMS))]][3].append(
                struct.pack("<4f", x, tree_height(hv), y, rng.uniform(35.0, 38.5)))
        trees = write_trees(header, types)
        files.append((TREES_PATH, trees))
        print(f"trees: {len(palms)} palms added to {pk}'s {TREES_PATH}")
        if a.region_pack:
            # the same trees file in the region pack too, so neither pack's copy can win wrongly
            with open(a.region_pack, "rb") as f:
                ptype = struct.unpack("<4sI", f.read(8))[1]
            ents = [(n, trees if n.lower() == TREES_PATH.lower() else pack_file(a.region_pack, n))
                    for n in pack_names(a.region_pack)]
            if not any(n.lower() == TREES_PATH.lower() for n, _ in ents):
                ents.append((TREES_PATH, trees))
            build_pack(a.out / "new_regions.pack", ents, ptype)
            print(f"wrote {a.out / 'new_regions.pack'} (= {a.region_pack} with these trees) -> data/")

    Image.fromarray(out[::4, ::4, :3]).save(a.out / "preview.png")
    Image.fromarray(orig[::4, ::4, :3]).save(a.out / "preview_orig.png")
    Image.fromarray((out_h.astype(np.float32) * 1.25).clip(0, 255).astype(np.uint8)).save(a.out / "preview_height.png")
    new0 = {}
    for j in range(J0, J1):
        for i in range(I0, I1):
            sl = (slice((j - J0) * TILE, (j - J0 + 1) * TILE), slice((i - I0) * TILE, (i - I0 + 1) * TILE))
            if not np.array_equal(out[sl], orig[sl]):
                new0[(i, j)] = out[sl]
    stpi, stpd, per_mip = st.write(new0)
    build_pack(a.out / "new_regions_map.pack", [(STPI, stpi), (STPD, stpd)] + files)
    print(f"tiles written per mip {per_mip}; stpd +{len(stpd) - st.stpd_size:,} B; "
          f"wrote {a.out / 'new_regions_map.pack'} -> data/ (movie pack, auto-loads) ({time.time() - t_start:.0f} s)")


if __name__ == "__main__":
    main()
