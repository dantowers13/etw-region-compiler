"""
Paint the campaign map's bare parchment south of Jawf (Arabia, the Red Sea coasts, the Gulf of
Oman, southern Egypt) into the supertexture and give it relief, so the land can be opened up
(deep_dive 10.20, 10.21).

  * parchment: low-chroma light pixels near the void regions (arabia, sahara, central_africa),
    grid lines closed over; small parchment islands next to repainted sea count too. West of
    --west-edge (map x) it stays parchment behind a new torn-paper edge, shadowed like vanilla's
  * the torn-paper shadow on the painted side: undone on land by a local brightness gain;
    water within 6 units of the old edge is repainted (its shadow is deepest in the Persian Gulf)
  * desert: colour pulled in from the painted land round it (push-pull), fading inland to gravel
    (the painted Syrian desert) or sand (the painted Nafud: the Rub' al Khali and the Dahna arc),
    or to the painted Egyptian desert west of the Red Sea, plus detail splatted from them
  * mountains: Hejaz / Asir / Yemen, the Red Sea Hills and the Hajar as bands at set distances
    from the Red Sea and the Gulf of Oman, ridged with patches of the painted Zagros
  * water: colour and alpha by distance from land, sampled from the painted Persian Gulf; painted
    down to the frame bar and across the strip between the Europe and India panels
  * relief: heightmaps\\default.tga (8192 x 4096, 8 bit, 3.2 px per map unit, the same grid as
    supertexture mip 3) is 0 south of the old torn edge. New land gets a plateau (vanilla's heights
    carried in, tilting from the Hejaz down to the Gulf), coast ramps, desert micro-relief from
    vanilla's Nafud heights and, under the painted mountains, the Zagros heights from the very
    patches the ridges were painted from; vanilla's drop at the old torn edge is smoothed over
  * mips rebuilt, tiles appended, shipped with the heightmap as new_regions_map.pack (movie pack,
    overrides supertexture.pack and main.pack)

Run from the game-dir clone:
    python scripts/paint_supertexture.py --regions-esf out/<build>/regions.esf --out out/map_<name>
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
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
from reactivate_region import build_pack, pack_file  # noqa: E402

I0, I1, J0, J1 = 70, 87, 21, 26          # mip-0 tiles painted: map x 120..460, y 120..220
FRAME_Y = 133.35                         # top of the Europe panel's bottom frame bar
VOID_LAND = {"arabia", "sahara", "central_africa"}
DESERT_SRC = ((278, 199, 318, 212), (262, 207, 300, 219))   # painted Nafud / Syrian desert
MOUNTAIN_SRC = (352, 212, 378, 226)                          # painted Zagros folds
GULF_REF = (320, 188, 400, 640)                              # painted Persian Gulf (water ramps)
WATER_SRC = (330, 192, 375, 215)                             # open water of the northern gulf (texture)
HEIGHTMAP = "heightmaps\\default.tga"
HK = K0 / 8                                                  # heightmap pixels per map unit (3.2)
SNOW_SAFE = 135                                              # the shader whitens ground above ~144
VANILLA_TREE_PACKS = (Path("../data/models.pack"), Path("../data/patch2.pack"))
PALMS = [f"RigidModels/CampaignTrees/Campaign_tree_palm0{k}.rigid_model" for k in (1, 2, 3)]
# oases and palm-lined towns: map (x, y) from a lat/lon fit to eight coastal landmarks (within
# ~2 units; Egypt's placed from the painted Nile), palm clusters, spread in map units
OASES = [
    ("medina", 282, 174.5, 10, 1.0), ("khaybar", 280, 183, 5, 0.7), ("tayma", 274, 196, 4, 0.6),
    ("al_ula", 270, 189, 4, 0.6), ("hail", 296.5, 195, 4, 0.7), ("qassim", 313, 187, 7, 1.0),
    ("diriyah", 332.5, 176, 6, 0.8), ("al_kharj", 337, 172, 4, 0.7), ("hofuf", 353, 181, 12, 1.2),
    ("qatif", 355.6, 189, 6, 0.6), ("jeddah", 280, 154, 3, 0.5), ("yanbu", 268, 172, 3, 0.5),
    ("doha", 366.5, 180.4, 2, 0.4), ("al_ain", 397, 173, 5, 0.6), ("liwa", 383, 165.5, 6, 1.2),
    ("ibri", 402.4, 166, 3, 0.5), ("nizwa", 409.5, 164, 5, 0.6), ("muscat", 416.5, 169, 3, 0.4),
    ("kharga", 214, 180, 6, 0.9), ("dakhla", 203, 180, 6, 1.0), ("farafra", 196, 191, 3, 0.5),
    ("bahariya", 202, 199, 4, 0.6), ("suakin", 266, 138.5, 3, 0.5),
]
# palm-lined shores: box (x0, y0, x1, y1), how many, distance from the water (map units)
PALM_SHORES = [
    ("batinah", (403, 167, 418, 179), 14, (0.2, 1.0)),       # the Gulf of Oman coast below the Hajar
    ("tihama", (268, 136, 290, 165), 10, (0.3, 1.4)),        # the Red Sea plain below the Asir
    ("nubian_nile", (219, 157, 236, 173), 10, (0.15, 0.7)),  # the Nile above vanilla's last palms
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


def splat(sources, shape, rng, P=256, S=160):
    """Cover `shape` with overlapping random patches of the sources (raised-cosine weights)."""
    h, w = shape
    C = sources[0].shape[2]
    win1 = np.sin(np.linspace(0, np.pi, P)) ** 2 + 1e-3
    win = (win1[:, None] * win1[None, :]).astype(np.float32)
    acc = np.zeros((h + 3 * P, w + 3 * P, C), np.float32)
    wacc = np.zeros((h + 3 * P, w + 3 * P), np.float32)
    for y0 in range(-P // 2, h, S):
        for x0 in range(-P // 2, w, S):
            src = sources[rng.integers(len(sources))]
            a, b = rng.integers(0, src.shape[0] - P + 1), rng.integers(0, src.shape[1] - P + 1)
            # zero-mean patches: a patch's own offset showed as a grid of blotches
            patch = src[a:a + P, b:b + P] - src[a:a + P, b:b + P].mean((0, 1))
            if rng.random() < 0.5:
                patch = patch[:, ::-1]
            # jittered (by at most S / 4, so neighbours still overlap when P >= 1.5 S), or the grid of
            # patch centres shows
            py, px = y0 + P + rng.integers(-S // 4, S // 4 + 1), x0 + P + rng.integers(-S // 4, S // 4 + 1)
            acc[py:py + P, px:px + P] += patch * win[..., None]
            wacc[py:py + P, px:px + P] += win
    out = acc[P:P + h, P:P + w] / np.maximum(wacc[P:P + h, P:P + w, None], 1e-6)
    # blending overlapping patches flattens their contrast; restore each channel to the sources'
    src_std = np.mean([s.reshape(-1, C).std(0) for s in sources], 0)
    return out * (src_std / np.maximum(out.reshape(-1, C).std(0), 1e-6)).astype(np.float32)


def noise_field(shape, rng, cell, sigma):
    h, w = shape
    n = ndimage.gaussian_filter(rng.standard_normal((h // cell + 1, w // cell + 1)), sigma)
    return up(n / (np.abs(n).max() + 1e-6), cell, shape)


def band(d, lo, hi):
    """1 in the middle of [lo, hi] (map units), falling to 0 at both ends."""
    t = np.clip((d - lo) / np.maximum(hi - lo, 1e-6), 0, 1)
    return np.sin(np.pi * t) ** 0.7


def edt(mask):
    return ndimage.distance_transform_edt(mask).astype(np.float32)


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

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions-esf", type=Path, required=True)
    ap.add_argument("--supertexture", type=Path, default=Path("../data/supertexture.pack"))
    ap.add_argument("--main-pack", type=Path, default=Path("../data/main.pack"),
                    help="pack holding the vanilla heightmaps\\default.tga")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--west-edge", type=float, default=140.0,
                    help="map x of the new torn edge: parchment west of it stays bare")
    ap.add_argument("--no-mountains", action="store_true")
    ap.add_argument("--no-relief", action="store_true", help="leave the heightmap vanilla")
    ap.add_argument("--region-pack", type=Path,
                    help="the region build's new_regions.pack: palms are added to its campaign trees (else "
                         "vanilla's) and a copy carrying the same trees file is written next to the map pack")
    ap.add_argument("--no-palms", action="store_true")
    ap.add_argument("--seed", type=int, default=1700)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    st = Supertexture(a.supertexture)
    W, H = (I1 - I0) * TILE, (J1 - J0) * TILE
    Wh, Hh = W // 8, H // 8
    orig = st.canvas(I0, I1, J0, J1)
    rgb = orig[..., :3].astype(np.float32)
    alpha = orig[..., 3].astype(np.float32)

    def cx(x): return map_to_px(x, 0)[0] - I0 * TILE
    def cy(y): return map_to_px(0, y)[1] - J0 * TILE
    xs, ys = px_to_map(np.arange(W)[None, :] + 0.5 + I0 * TILE, np.arange(H)[:, None] + 0.5 + J0 * TILE)
    xs, ys = np.broadcast_to(xs, (H, W)), np.broadcast_to(ys, (H, W))
    xh, yh = px_to_map(np.arange(Wh)[None, :] + 0.5 + I0 * TILE // 8, np.arange(Hh)[:, None] + 0.5 + J0 * TILE // 8, 3)
    xh, yh = np.broadcast_to(xh, (Hh, Wh)), np.broadcast_to(yh, (Hh, Wh))

    def down(m):
        return m.reshape(Hh, 8, Wh, 8).mean((1, 3), dtype=np.float32)

    # ── masks from regions.esf ──
    m = Mesh(ESFReader(a.regions_esf).read_root())
    names = [m.name(ri) for ri in range(len(m.regs))]

    def raster(pred):
        im = Image.new("L", (W, H), 0)
        dr = ImageDraw.Draw(im)
        for ri in range(len(m.regs)):
            if not pred(ri):
                continue
            for ai in range(len(m.areas(ri))):
                try:
                    p = m.area_polygon(ri, ai)
                except Exception:
                    continue
                for g in getattr(p, "geoms", [p]):
                    if g.is_empty:
                        continue
                    dr.polygon([(cx(x), cy(y)) for x, y in g.exterior.coords], fill=255)
                    for hole in g.interiors:
                        dr.polygon([(cx(x), cy(y)) for x, y in hole.coords], fill=0)
        return np.asarray(im) > 127

    land = raster(lambda ri: m.regs[ri][1].value == "land" and names[ri] != "lakes")
    void = raster(lambda ri: names[ri] in VOID_LAND)
    red_sea = raster(lambda ri: names[ri] == "red_sea") & ~land
    sea = ~land
    frame_w = np.clip((ys - FRAME_Y) / 0.25, 0, 1)

    # ── which side of the Red Sea, and how far between it and the Gulf (heightmap grid) ──
    land_h, red_h = down(land) > 0.5, down(red_sea) > 0.5
    sea_h = ~land_h
    _, (_, nxh) = ndimage.distance_transform_edt(~red_h, return_indices=True)
    east_h = nxh < np.arange(Wh)[None, :]                    # nearest Red Sea water lies west
    del nxh
    d_red_h = edt(~red_h) / HK
    d_gulf_h = edt(~(sea_h & ~red_h & (xh > 300) & (yh < 215))) / HK
    tilt = d_gulf_h / np.maximum(d_gulf_h + d_red_h, 1e-3)  # 1 by the Red Sea, 0 by the Gulf
    d_sea_h = edt(land_h) / HK
    # sand: the Rub' al Khali in the south-east, the Dahna arc between the Nafud and it
    nz = noise_field((Hh, Wh), rng, 8, 2)
    rub = (east_h * np.clip((166 - yh + 6 * nz) / 8, 0, 1) * np.clip((0.72 - tilt + 0.1 * nz) / 0.12, 0, 1)
           * np.clip((d_sea_h - 6) / 6, 0, 1) * (xh > 300))
    dahna = (east_h * np.clip(1 - np.abs(tilt - 0.47 + 0.05 * nz) / 0.035, 0, 1)
             * np.clip((200 - yh) / 3, 0, 1) * np.clip((yh - 152) / 6, 0, 1) * np.clip((d_sea_h - 3) / 3, 0, 1))
    sand_h = ndimage.gaussian_filter(np.maximum(rub, dahna), 3).astype(np.float32)
    sand = ndimage.gaussian_filter(up(sand_h, 8, (H, W)), 8)
    east = ndimage.gaussian_filter(up(east_h, 8, (H, W)), 8) > 0.5

    # ── parchment ──
    chroma, lum = rgb.max(2) - rgb.min(2), rgb.mean(2)
    # near the void regions OVER LAND: straight-line distance crossed the Strait of Hormuz and
    # painted Iran's pale coast by Bandar Abbas as parchment
    near_void_h = over_land(down(void & land) > 0.5, land_h, 20) <= 6 * HK
    near_void = up(ndimage.binary_dilation(near_void_h, iterations=2), 8, (H, W)) > 0.5
    parch = land & near_void & (chroma < 50) & (lum > 135) & (alpha < 128)
    parch = ndimage.binary_fill_holes(ndimage.binary_closing(parch, np.ones((7, 7)), iterations=2) | (void & land))
    lab, n = ndimage.label(parch)
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    # water by other coasts (Iran's) stays vanilla: its painted beach lies outside the region
    # polygon, so it is 'sea' drawn as land and was repainted as water, darkening the shore
    d_other = edt(~(land & ~near_void)) / K0
    by_other = (d_other < edt(~(land & near_void)) / K0) * np.clip(1.5 - d_other, 0, 1)
    del d_other
    paint_sea = sea & (alpha < 128) & (ys > FRAME_Y - 1) & (by_other == 0)   # water drawn as parchment
    touch = ndimage.binary_dilation(paint_sea, np.ones((5, 5)))
    islands = [k + 1 for k in range(n) if sizes[k] <= 20000 and touch[lab == k + 1].any()] if n < 5000 else []
    parch = np.isin(lab, list(1 + np.nonzero(sizes > 20000)[0]) + islands)
    del lab
    parch |= (land & near_void & (chroma < 50) & (lum > 120) & (alpha < 128)
              & ndimage.binary_dilation(paint_sea, np.ones((25, 25))))
    del chroma
    # the new torn edge: ragged at three scales, like vanilla's
    ragged = (2.0 * noise_field((H, W), rng, 64, 3) + 0.5 * noise_field((H, W), rng, 8, 1.5)
              + 0.25 * noise_field((H, W), rng, 2, 1))
    west = xs < a.west_edge + ragged
    del ragged
    parch_west = parch & west
    parch_paint = parch & ~west
    paint_land = parch_paint & land & (ys > FRAME_Y - 1)
    target = paint_sea | paint_land

    # ── the torn-paper shadow (painted on the painted side): local brightness gain ──
    d_edge = edt(~parch_paint)
    d_west = edt(~parch_west)
    lum_s = ndimage.gaussian_filter(lum, 10)
    del lum
    taper = np.clip((5 * K0 - d_edge) / K0, 0, 1)
    gain = np.ones((H, W), np.float32)
    shadow = np.zeros((H, W), bool)
    for kind in (land & (alpha < 128),):
        # only where the nearest parchment gets painted; vanilla keeps its shadow by bare parchment
        sh = kind & ~parch & near_void & (d_edge < 6 * K0) & (ys > FRAME_Y + 2) & (d_edge < d_west)
        ref_px = kind & ~parch & (d_edge > 5 * K0) & (d_edge < 7 * K0)
        ref_local = smooth_fill(lum_s[..., None], ref_px, sigma=4)[..., 0]
        g = 1 + (np.clip(ref_local / np.maximum(lum_s, 1), 1.0, 2.2) - 1) * taper
        gain = np.where(sh, g, gain)
        shadow |= sh
        del ref_local, g
    del lum_s, taper
    rgb = np.where(shadow[..., None], np.clip(rgb * gain[..., None], 0, 255), rgb)
    grown = target | (land & (d_edge <= 5) & (ys > FRAME_Y))     # the paper edge itself
    weight = np.clip((10 - ndimage.distance_transform_edt(~grown)) / 10, 0, 1).astype(np.float32) * frame_w
    weight_core = weight.copy()
    # feather the old torn edge ~3 units into vanilla land, wobbling: a 10 px blend kept its ragged
    # outline as a crisp seam along Jawf's southern border. The Nile's green is left alone.
    d_g = edt(~grown) / K0 + 0.8 * noise_field((H, W), rng, 64, 3)
    veg = ndimage.gaussian_filter(np.clip((rgb[..., 1] / np.maximum(rgb[..., 0], 1) - 0.92) / 0.08, 0, 1), 8)
    feather = np.clip((3.5 - d_g) / 3.0, 0, 1) * land * near_void * (d_edge < d_west) * (1 - veg)
    weight = np.maximum(weight, feather.astype(np.float32) * frame_w)
    del d_g, veg, feather
    # painted water carries the torn edge's shadow too (darkest where the edge crosses the
    # Persian Gulf): repaint water within 6 units of any parchment, land or sea, fading 4 -> 6
    d_any = edt(~(parch_paint | paint_sea)) / K0
    shadow_w = sea & (alpha > 200) & (d_any < 6) & (ys > FRAME_Y)
    weight = np.maximum(weight, np.where(shadow_w, np.clip((6 - d_any) / 2, 0, 1) * (1 - by_other), 0) * frame_w)
    del by_other
    print(f"parchment: land {paint_land.sum():,} px, sea {paint_sea.sum():,} px, left bare {parch_west.sum():,} px; "
          f"shadow gain median {np.median(gain[shadow]):.2f}, max {gain[shadow].max():.2f}")
    del gain

    # ── desert ──
    known = land & ~grown & ~parch & (alpha < 128) & (ys > FRAME_Y + 2) & (d_edge > 3)
    base_col = smooth_fill(rgb, known)
    srcs = [st.box(*b)[..., :3].astype(np.float32) for b in DESERT_SRC]
    sand_ref = srcs[0].reshape(-1, 3).mean(0) * np.array([1.03, 0.97, 0.88])   # redder, like the Rub' al Khali
    gravel_ref = srcs[1].reshape(-1, 3).mean(0)
    africa = known & ~east & (xs < 245)
    africa_ref = np.median(rgb[africa], 0) if africa.sum() > 1000 else gravel_ref
    print(f"desert refs: sand {sand_ref.round()}, gravel {gravel_ref.round()}, africa {np.round(africa_ref)}")
    s3 = sand[..., None]
    far = np.where(east[..., None], gravel_ref * (1 - s3) + sand_ref * s3, africa_ref).astype(np.float32)
    w_int = np.clip(edt(~known) / (12 * K0), 0, 1)[..., None]
    base_col = base_col * (1 - w_int) + far * w_int
    del far, w_int
    fine = [s - ndimage.gaussian_filter(s, (4, 4, 0)) for s in srcs]
    broad = [s - ndimage.gaussian_filter(s, (48, 48, 0)) for s in srcs]
    sand_d = splat([fine[0]], (H, W), rng) + 0.6 * splat([broad[0]], (H, W), rng, P=288, S=180)
    gravel_d = splat([fine[1]], (H, W), rng) + 0.6 * splat([broad[1]], (H, W), rng, P=288, S=180)
    detail = sand_d * s3 + gravel_d * (1 - s3)
    del sand_d, gravel_d, fine, broad
    var = 1 + 0.05 * noise_field((H, W), rng, 32, 4) + 0.03 * noise_field((H, W), rng, 8, 2)
    desert = np.clip(base_col * var[..., None] + detail, 0, 255)
    del base_col, detail, var

    # ── mountains ──
    mw = np.zeros((H, W), np.float32)
    ridge_h = np.zeros((Hh, Wh), np.float32)
    hm = np.frombuffer(pack_file(a.main_pack, HEIGHTMAP), np.uint8)
    tga_header, hm = bytes(hm[:18]), hm[18:].reshape(4096, 8192)[::-1]    # bottom-up rows
    assert tga_header[2] == 3 and tga_header[12:17] == bytes([0, 32, 0, 16, 8]), "not an 8192x4096 8-bit TGA"

    def hbox(x0, y0, x1, y1):
        return hm[int((640 - y1) * HK):int((640 - y0) * HK), int((x0 + 1280) * HK):int((x1 + 1280) * HK)].astype(np.float32)

    if not a.no_mountains:
        d_rs, (ny, nx) = ndimage.distance_transform_edt(~red_sea, return_indices=True)
        del ny
        wander = 1.2 * noise_field((H, W), rng, 16, 3)       # ranges' edges wander, not ribbons
        d_rs = d_rs.astype(np.float32) / K0 + wander
        east_px = nx < np.arange(W)[None, :]
        del nx
        yemen = np.clip((150 - ys) / 12, 0, 1)                # the highlands widen southward
        hejaz = east_px * (ys < 196) * band(d_rs, 1.2, 6.5 + 5.5 * yemen)
        sudan = ~east_px * (ys < 192) * (ys > 140) * band(d_rs, 0.8, 5.0)
        del d_rs, yemen
        oman_sea = sea & (xs > 401) & (ys < 192)              # the Gulf of Oman, east of Musandam
        d_om = edt(~oman_sea) / K0 + 0.8 * wander
        hajar = (xs > 396) * (ys > 158) * (ys < 193) * band(d_om, 0.6, 5.0)
        del d_om, wander
        rng_mask = np.maximum.reduce([hejaz, sudan, hajar]) * land * near_void    # not Iran's coast
        del hejaz, sudan, hajar
        patchy = np.clip(0.8 + 0.6 * noise_field((H, W), rng, 16, 3), 0, 1)
        mw = np.clip(1.25 * ndimage.gaussian_filter(rng_mask * patchy, 12), 0, 1).astype(np.float32)
        del rng_mask, patchy
        # the Zagros heights ride along with its painted folds, so ridges and relief line up
        msrc = st.box(*MOUNTAIN_SRC)[..., :3].astype(np.float32)
        zh = hbox(*MOUNTAIN_SRC)
        zh = np.asarray(Image.fromarray(zh, "F").resize((msrc.shape[1], msrc.shape[0]), Image.BILINEAR))
        msrc4 = np.dstack([msrc - ndimage.gaussian_filter(msrc, (10, 10, 0)), zh - ndimage.gaussian_filter(zh, 2 * K0)])
        ridges4 = splat([msrc4], (H, W), rng)
        ridges, ridge_h = ridges4[..., :3], down(ridges4[..., 3])
        del ridges4
        # the desert round them, shaded by the ridges: a separate dark tone stood out red-brown
        mountain = np.clip(desert * np.array([0.92, 0.89, 0.86], np.float32)
                           + 1.3 * ridges.mean(2, keepdims=True) + 0.3 * ridges, 0, 255)
        del ridges
        print(f"mountains: {(mw > 0.3).sum() / K0 ** 2:,.0f} square map units")
    land_rgb = desert * (1 - mw[..., None]) + mountain * mw[..., None] if not a.no_mountains else desert
    del desert

    # ── water ──
    d_sea = edt(sea)
    d_land = edt(land)
    signed = np.where(sea, d_sea, -d_land)
    gx0, gy0, gx1, gy1 = GULF_REF
    in_ref = (xs > gx0) & (xs < gx1) & (ys > gy0) & (ys < gy1)
    gulf = sea & (alpha > 200) & in_ref & (d_any > 6)          # unshadowed open water only
    bins = np.arange(-20, 300)
    sb = np.clip(np.round(signed), -20, 299).astype(np.int16)
    col_ramp, a_ramp = np.zeros((len(bins), 3)), np.zeros(len(bins))
    for k, b in enumerate(bins):
        sel = gulf & (sb == b)
        col_ramp[k] = rgb[sel].mean(0) if sel.sum() > 20 else col_ramp[k - 1]
        sel_a = in_ref & ~target & (sb == b)
        a_ramp[k] = alpha[sel_a].mean() if sel_a.sum() > 20 else (a_ramp[k - 1] if k else 0)
    col_ramp[:20] = col_ramp[20]
    col_ramp = ndimage.uniform_filter1d(col_ramp, 9, axis=0).astype(np.float32)
    water = col_ramp[sb + 20]
    del sb
    wbox = st.box(*WATER_SRC)
    wsrc = wbox[..., :3].astype(np.float32)
    wres = wsrc - ndimage.gaussian_filter(wsrc, (25, 25, 0))
    open_w = ndimage.binary_erosion(wbox[..., 3] == 255, np.ones((41, 41)))   # 20 px off any shore
    patches, Pw = [], 120
    wy, wx = np.nonzero(open_w[:-Pw, :-Pw])
    for _ in range(4000):
        k = rng.integers(len(wy))
        py, px = wy[k], wx[k]
        if open_w[py:py + Pw, px:px + Pw].all():
            patches.append(wres[py:py + Pw, px:px + Pw])
            if len(patches) == 40:
                break
    assert patches, "no open-water patch in WATER_SRC"
    wtex = splat(patches, (H, W), rng, P=100, S=64)
    water = np.clip(water + 0.8 * ndimage.gaussian_filter(wtex, (2, 2, 0)), 0, 255)
    del wtex
    # tint by the nearest painted water (the Red Sea is bluer than the gulf the ramp came from)
    known_w = sea & (alpha > 200) & ~target & (d_sea > 15) & (d_any > 6)
    local = smooth_fill(rgb, known_w)
    tint = np.clip(local / rgb[gulf & (d_sea > 15)].mean(0), 0.6, 1.6)
    del local
    water = np.clip(water * tint, 0, 255)
    del tint
    new_alpha = np.interp(signed, bins, a_ramp).astype(np.float32)
    del signed

    # ── composite ──
    paint_rgb = np.where(sea[..., None], water, land_rgb)
    del water, land_rgb
    fringe = (np.clip(1 - d_land / 6, 0, 1) * land)[..., None]      # coast edge leans to the shore colour
    paint_rgb = paint_rgb * (1 - 0.35 * fringe) + col_ramp[20] * 0.35 * fringe
    del fringe
    w3 = weight[..., None]
    out_rgb = rgb * (1 - w3) + paint_rgb * w3
    del paint_rgb
    # the new torn edge's shadow, on what we painted or brightened (vanilla's: 0.55 over ~4 units)
    mine = (weight > 0) | shadow
    shade = 1 - 0.45 * np.clip(1 - d_west / K0 / 4, 0, 1) ** 1.6
    out_rgb = np.where(mine[..., None], out_rgb * shade[..., None], out_rgb)
    del shade

    # ── palms: oases and palm-lined shores, a darker green on the ground under them ──
    palms = []
    if not a.no_palms:
        def fits(x, y, max_mw):
            px, py = int(cx(x)), int(cy(y))
            return (0 <= px < W and 0 <= py < H and land[py, px] and y > FRAME_Y + 1 and mw[py, px] <= max_mw
                    and d_west[py, px] > 2 * K0 and all(math.dist((x, y), q) >= 0.45 for q in palms))
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
            dl = d_land[py0:py1, px0:px1] / K0
            cand = np.argwhere(land[py0:py1, px0:px1] & (dl >= lo) & (dl <= hi) & (bx0 < 260 or east[py0:py1, px0:px1]))
            placed = 0
            for k in rng.permutation(len(cand))[:600]:
                if placed == n:
                    break
                x, y = px_to_map(cand[k][1] + px0 + I0 * TILE + 0.5, cand[k][0] + py0 + J0 * TILE + 0.5)
                if fits(x, y, 0.85):
                    palms.append((x, y))
                    placed += 1
        nile = rgb[int(cy(205)):int(cy(185)), int(cx(217)):int(cx(227))].reshape(-1, 3)
        green = nile[(nile[:, 1] >= 0.95 * nile[:, 0]) & (nile[:, 1] > 1.2 * nile[:, 2])]    # not the river
        oasis_col = np.median(green, 0) if len(green) > 500 else np.array([118, 120, 72], np.float32)
        tw = np.zeros((H, W), np.float32)
        r = int(1.5 * K0)
        blob = np.exp(-((np.arange(-r, r + 1)[:, None] ** 2 + np.arange(-r, r + 1)[None, :] ** 2) / (2 * (0.55 * K0) ** 2)))
        for x, y in palms:
            px, py = int(cx(x)), int(cy(y))
            ys0, ys1, xs0, xs1 = max(py - r, 0), min(py + r + 1, H), max(px - r, 0), min(px + r + 1, W)
            tw[ys0:ys1, xs0:xs1] = np.maximum(tw[ys0:ys1, xs0:xs1], blob[ys0 - py + r:ys1 - py + r, xs0 - px + r:xs1 - px + r])
        tw *= 0.3 * land
        out_rgb = out_rgb * (1 - tw[..., None]) + oasis_col * tw[..., None]
        del tw
        print(f"palms: {len(palms)} clusters; oasis ground {oasis_col.round()}")

    out = np.dstack([np.clip(out_rgb, 0, 255),
                     np.clip(alpha * (1 - weight) + new_alpha * weight, 0, 255)]).astype(np.uint8)
    del out_rgb

    # ── relief ──
    files = []
    if not a.no_relief:
        r0, c0 = J0 * TILE // 8, I0 * TILE // 8
        vh = hm[r0:r0 + Hh, c0:c0 + Wh].astype(np.float32)
        par_h = down(parch_paint) > 0.3
        d_par_h = over_land(par_h, land_h, 17) / HK       # over land: not across to Iran
        d_anypar_h = edt(~(down(parch) > 0.3)) / HK
        d_west_h = edt(~(down(parch_west) > 0.5)) / HK
        # vanilla heights clear of its torn-edge drop (~3 units), carried in as a membrane
        trusted = land_h & (d_anypar_h > 4) & (vh > 0)
        base = push_pull(ndimage.gaussian_filter(vh, 3)[..., None], trusted.astype(float))[..., 0]
        base = ndimage.gaussian_filter(base, 6)
        level = np.where(east_h, 15 + 38 * tilt - 12 * sand_h, 30)      # Arabia tilts down to the Gulf
        w_far = np.clip(edt(~trusted) / HK / 15, 0, 1)
        plain = base * (1 - w_far) + level * w_far
        ramp = np.clip(d_sea_h / 5, 0, 1) ** 0.8
        plain = 3 + (plain - 3) * ramp
        dsrc = [hbox(*b) for b in DESERT_SRC]
        micro = splat([(d - ndimage.gaussian_filter(d, 4))[..., None] for d in dsrc], (Hh, Wh), rng, P=32, S=20)[..., 0]
        micro *= 1 + 0.6 * sand_h                                      # dune fields are rougher
        mw_h = down(mw)
        lift = np.where(east_h, 1.0, 0.75)                             # the Red Sea Hills are lower
        mtn = mw_h * lift * (32 + 0.75 * np.maximum(ridge_h, -27)) * np.clip(d_sea_h / 2, 0, 1)
        new_h = plain + micro * ramp + mtn
        # below the snow line (world y 1.4 = height ~144: peaks of 190 came out white)
        new_h = np.where(new_h > SNOW_SAFE - 30, SNOW_SAFE - 30 + (new_h - SNOW_SAFE + 30) * 0.35, new_h)
        new_h = np.where(land_h, np.clip(new_h, 1, SNOW_SAFE), 0)
        new_h *= np.clip((yh - FRAME_Y) / 2.5, 0, 1) * np.clip(d_west_h / 2.5, 0, 1)
        # painted ground takes the new heights; vanilla's drop at the old edge blends into them
        w_h = np.maximum(down(weight_core) * near_void_h, land_h * np.clip((5 - d_par_h) / 2, 0, 1) * (d_par_h < d_west_h))
        out_h = np.round(vh * (1 - w_h) + new_h * w_h).clip(0, 255).astype(np.uint8)
        print(f"relief: {(w_h > 0).sum():,} heightmap px changed; new land p50 {np.median(new_h[land_h & (w_h > 0.5)]):.0f}, "
              f"p95 {np.percentile(new_h[land_h & (w_h > 0.5)], 95):.0f}, max {new_h.max():.0f}")
        hm_new = hm.copy()
        hm_new[r0:r0 + Hh, c0:c0 + Wh] = out_h
        files.append((HEIGHTMAP, tga_header + np.ascontiguousarray(hm_new[::-1]).tobytes()))

    # ── palm instances in the campaign trees (deep_dive 10.19) ──
    a.out.mkdir(parents=True, exist_ok=True)
    if palms:
        hcan = out_h if not a.no_relief else hm[J0 * TILE // 8:J0 * TILE // 8 + Hh, I0 * TILE // 8:I0 * TILE // 8 + Wh]
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
            hv = float(hcan[int((640 - y) * HK) - J0 * TILE // 8, int((x + 1280) * HK) - I0 * TILE // 8])
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
    Image.fromarray(out[..., :3]).resize((W // 4, H // 4)).save(a.out / "preview.png")
    Image.fromarray(orig[..., :3]).resize((W // 4, H // 4)).save(a.out / "preview_orig.png")
    if files:
        Image.fromarray((out_h.astype(np.float32) * 1.25).clip(0, 255).astype(np.uint8)).save(a.out / "preview_height.png")
        Image.fromarray((vh * 1.25).clip(0, 255).astype(np.uint8)).save(a.out / "preview_height_orig.png")
    new0 = {}
    for j in range(J0, J1):
        for i in range(I0, I1):
            sl = (slice((j - J0) * TILE, (j - J0 + 1) * TILE), slice((i - I0) * TILE, (i - I0 + 1) * TILE))
            if not np.array_equal(out[sl], orig[sl]):
                new0[(i, j)] = out[sl]
    stpi, stpd, per_mip = st.write(new0)
    build_pack(a.out / "new_regions_map.pack", [(STPI, stpi), (STPD, stpd)] + files)
    print(f"tiles written per mip {per_mip}; stpd +{len(stpd) - st.stpd_size:,} B; "
          f"wrote {a.out / 'new_regions_map.pack'} -> data/ (movie pack, auto-loads)")


if __name__ == "__main__":
    main()
