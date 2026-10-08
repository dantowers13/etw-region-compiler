"""
Paint the campaign map's bare parchment south of Jawf (Arabia, the Red Sea coasts, the Gulf of
Oman) into the supertexture, so the land can be opened up (deep_dive 10.20).

  * parchment: low-chroma light pixels near the void regions (arabia, sahara, central_africa),
    grid lines closed over; small parchment islands next to repainted sea count too
  * the torn-paper shadow on the painted side: undone on land by a local brightness gain;
    water within 6 units of the old edge is repainted (its shadow is deepest in the Persian Gulf)
  * desert: colour pulled in from the painted land round it (push-pull), fading to the painted
    Nafud's mean colour inland, plus fine detail splatted from painted desert patches
  * mountains: Hejaz / Asir / Yemen, the Red Sea Hills and the Hajar as bands at set distances
    from the Red Sea and the Gulf of Oman, ridged with patches of the painted Zagros
  * water: colour and alpha by distance from land, sampled from the painted Persian Gulf
  * mips rebuilt, tiles appended, shipped as new_regions_map.pack (movie pack, overrides
    supertexture.pack)

Run from the game-dir clone:
    python scripts/paint_supertexture.py --regions-esf out/<build>/regions.esf --out out/map_<name>
"""

from __future__ import annotations

import argparse
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
from reactivate_region import build_pack  # noqa: E402

I0, I1, J0, J1 = 75, 86, 21, 26          # mip-0 tiles painted: map x 220..440, y 120..220
FRAME_Y = 134.0                          # the Europe panel's frame starts at map y ~132.5
VOID_LAND = {"arabia", "sahara", "central_africa"}
DESERT_SRC = ((278, 199, 318, 212), (262, 207, 300, 219))   # painted Nafud / Syrian desert
MOUNTAIN_SRC = (352, 212, 378, 226)                          # painted Zagros folds
GULF_REF = (320, 188, 400, 640)                              # painted Persian Gulf (water ramps)
WATER_SRC = (330, 192, 375, 215)                             # open water of the northern gulf (texture)


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


def smooth_fill(img, known, small=8, sigma=6):
    """push_pull at 1/small resolution, blurred and scaled back up."""
    h, w = known.shape
    base = push_pull(img[::small, ::small], known[::small, ::small].astype(float))
    base = np.kron(ndimage.gaussian_filter(base, (sigma, sigma, 0)), np.ones((small, small, 1)))[:h, :w]
    return ndimage.uniform_filter(base, size=(small * 2, small * 2, 1))


def splat(sources, shape, rng, P=256, S=160):
    """Cover `shape` with overlapping random patches of the sources (raised-cosine weights)."""
    h, w = shape
    win1 = np.sin(np.linspace(0, np.pi, P)) ** 2 + 1e-3
    win = win1[:, None] * win1[None, :]
    acc = np.zeros((h + 2 * P, w + 2 * P, 3), np.float32)
    wacc = np.zeros((h + 2 * P, w + 2 * P), np.float32)
    for y0 in range(-P // 2, h, S):
        for x0 in range(-P // 2, w, S):
            src = sources[rng.integers(len(sources))]
            a, b = rng.integers(0, src.shape[0] - P), rng.integers(0, src.shape[1] - P)
            patch = src[a:a + P, b:b + P]
            if rng.random() < 0.5:
                patch = patch[:, ::-1]
            acc[y0 + P // 2:y0 + P // 2 + P, x0 + P // 2:x0 + P // 2 + P] += patch * win[..., None]
            wacc[y0 + P // 2:y0 + P // 2 + P, x0 + P // 2:x0 + P // 2 + P] += win
    out = acc[P // 2:P // 2 + h, P // 2:P // 2 + w] / wacc[P // 2:P // 2 + h, P // 2:P // 2 + w, None]
    # blending overlapping patches flattens their contrast; restore it to the sources' level
    return out * (np.mean([s.std() for s in sources]) / max(out.std(), 1e-6))


def noise_field(shape, rng, cell, sigma):
    h, w = shape
    n = ndimage.gaussian_filter(rng.standard_normal((h // cell + 1, w // cell + 1)), sigma)
    return np.kron(n / (np.abs(n).max() + 1e-6), np.ones((cell, cell)))[:h, :w]


def band(d, lo, hi):
    """1 in the middle of [lo, hi] (map units), falling to 0 at both ends."""
    t = np.clip((d - lo) / np.maximum(hi - lo, 1e-6), 0, 1)
    return np.sin(np.pi * t) ** 0.7


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions-esf", type=Path, required=True)
    ap.add_argument("--supertexture", type=Path, default=Path("../data/supertexture.pack"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-mountains", action="store_true")
    ap.add_argument("--seed", type=int, default=1700)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    st = Supertexture(a.supertexture)
    W, H = (I1 - I0) * TILE, (J1 - J0) * TILE
    orig = st.canvas(I0, I1, J0, J1)
    rgb = orig[..., :3].astype(np.float32)
    alpha = orig[..., 3].astype(np.float32)

    def cx(x): return map_to_px(x, 0)[0] - I0 * TILE
    def cy(y): return map_to_px(0, y)[1] - J0 * TILE
    xs, ys = px_to_map(np.arange(W)[None, :] + 0.5 + I0 * TILE, np.arange(H)[:, None] + 0.5 + J0 * TILE)
    xs, ys = np.broadcast_to(xs, (H, W)), np.broadcast_to(ys, (H, W))

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
    frame_w = np.clip((ys - FRAME_Y) / 1.5, 0, 1)

    # ── parchment ──
    chroma, lum = rgb.max(2) - rgb.min(2), rgb.mean(2)
    near_void = ndimage.distance_transform_edt(~void) <= 6 * K0
    parch = land & near_void & (chroma < 50) & (lum > 135) & (alpha < 128)
    parch = ndimage.binary_fill_holes(ndimage.binary_closing(parch, np.ones((7, 7)), iterations=2) | (void & land))
    lab, n = ndimage.label(parch)
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    paint_sea = sea & (alpha < 128) & (ys > FRAME_Y - 1)          # water drawn as parchment
    touch = ndimage.binary_dilation(paint_sea, np.ones((5, 5)))
    islands = [k + 1 for k in range(n) if sizes[k] <= 20000 and touch[lab == k + 1].any()] if n < 5000 else []
    parch = np.isin(lab, list(1 + np.nonzero(sizes > 20000)[0]) + islands)
    parch |= land & (chroma < 50) & (lum > 120) & (alpha < 128) & ndimage.binary_dilation(paint_sea, np.ones((25, 25)))
    paint_land = parch & land & (ys > FRAME_Y - 1)
    target = paint_sea | paint_land

    # ── the torn-paper shadow (painted on the painted side): local brightness gain ──
    d_edge = ndimage.distance_transform_edt(~parch)
    lum_s = ndimage.gaussian_filter(lum, 10)
    taper = np.clip((5 * K0 - d_edge) / K0, 0, 1)
    gain = np.ones((H, W), np.float32)
    shadow = np.zeros((H, W), bool)
    for kind in (land & (alpha < 128),):
        sh = kind & ~parch & (d_edge < 6 * K0) & (ys > FRAME_Y + 2)
        ref_px = kind & ~parch & (d_edge > 5 * K0) & (d_edge < 7 * K0)
        ref_local = smooth_fill(lum_s[..., None], ref_px, sigma=4)[..., 0]
        g = 1 + (np.clip(ref_local / np.maximum(lum_s, 1), 1.0, 2.2) - 1) * taper
        gain = np.where(sh, g, gain)
        shadow |= sh
    rgb = np.where(shadow[..., None], np.clip(rgb * gain[..., None], 0, 255), rgb)
    grown = target | (land & (d_edge <= 5) & (ys > FRAME_Y))     # the paper edge itself
    weight = np.clip((10 - ndimage.distance_transform_edt(~grown)) / 10, 0, 1) * frame_w
    # painted water carries the torn edge's shadow too (darkest where the edge crosses the
    # Persian Gulf): repaint water within 6 units of any parchment, land or sea, fading 4 -> 6
    d_any = ndimage.distance_transform_edt(~(parch | paint_sea)) / K0
    shadow_w = sea & (alpha > 200) & (d_any < 6) & (ys > FRAME_Y)
    weight = np.maximum(weight, np.where(shadow_w, np.clip((6 - d_any) / 2, 0, 1), 0) * frame_w)
    print(f"parchment: land {paint_land.sum():,} px, sea {paint_sea.sum():,} px; "
          f"shadow gain median {np.median(gain[shadow]):.2f}, max {gain[shadow].max():.2f}")

    # ── desert ──
    known = land & ~grown & (alpha < 128) & (ys > FRAME_Y + 2) & (d_edge > 3)
    base_col = smooth_fill(rgb, known)
    srcs = [st.box(*b)[..., :3].astype(np.float32) for b in DESERT_SRC]
    desert_ref = np.concatenate([s.reshape(-1, 3) for s in srcs]).mean(0)
    w_int = np.clip(ndimage.distance_transform_edt(~known) / (12 * K0), 0, 1)[..., None]
    base_col = base_col * (1 - w_int) + desert_ref * w_int
    detail = splat([s - ndimage.gaussian_filter(s, (4, 4, 0)) for s in srcs], (H, W), rng)
    var = 1 + 0.05 * noise_field((H, W), rng, 32, 4) + 0.03 * noise_field((H, W), rng, 8, 2)
    desert = np.clip(base_col * var[..., None] + detail, 0, 255)

    # ── mountains ──
    mw = np.zeros((H, W), np.float32)
    if not a.no_mountains:
        d_rs, (ny, nx) = ndimage.distance_transform_edt(~red_sea, return_indices=True)
        wander = 1.2 * noise_field((H, W), rng, 16, 3)       # ranges' edges wander, not ribbons
        d_rs = d_rs / K0 + wander
        east = nx < np.arange(W)[None, :]                    # nearest Red Sea water lies west
        yemen = np.clip((150 - ys) / 12, 0, 1)                # the highlands widen southward
        hejaz = east * (ys < 196) * band(d_rs, 1.2, 6.5 + 5.5 * yemen)
        sudan = ~east * (ys < 192) * (ys > 140) * band(d_rs, 0.8, 5.0)
        oman_sea = sea & (xs > 401) & (ys < 192)              # the Gulf of Oman, east of Musandam
        d_om = ndimage.distance_transform_edt(~oman_sea) / K0 + 0.8 * wander
        hajar = (xs > 396) * (ys > 158) * (ys < 193) * band(d_om, 0.6, 5.0)
        rng_mask = np.maximum.reduce([hejaz, sudan, hajar]) * land
        patchy = np.clip(0.8 + 0.6 * noise_field((H, W), rng, 16, 3), 0, 1)
        mw = np.clip(1.25 * ndimage.gaussian_filter(rng_mask * patchy, 6), 0, 1).astype(np.float32)
        msrc = st.box(*MOUNTAIN_SRC)[..., :3].astype(np.float32)
        ridges = splat([msrc - ndimage.gaussian_filter(msrc, (10, 10, 0))], (H, W), rng)
        tone = desert_ref * np.array([0.74, 0.68, 0.62])
        mountain = np.clip(tone + 2.0 * ridges.mean(2, keepdims=True) + 0.5 * ridges, 0, 255)
        print(f"mountains: {(mw > 0.3).sum() / K0 ** 2:,.0f} square map units")
    land_rgb = desert * (1 - mw[..., None]) + mountain * mw[..., None] if not a.no_mountains else desert

    # ── water ──
    d_sea = ndimage.distance_transform_edt(sea)
    d_land = ndimage.distance_transform_edt(land)
    signed = np.where(sea, d_sea, -d_land)
    gx0, gy0, gx1, gy1 = GULF_REF
    in_ref = (xs > gx0) & (xs < gx1) & (ys > gy0) & (ys < gy1)
    gulf = sea & (alpha > 200) & in_ref & (d_any > 6)          # unshadowed open water only
    bins = np.arange(-20, 300)
    sb = np.clip(np.round(signed), -20, 299).astype(int)
    col_ramp, a_ramp = np.zeros((len(bins), 3)), np.zeros(len(bins))
    for k, b in enumerate(bins):
        sel = gulf & (sb == b)
        col_ramp[k] = rgb[sel].mean(0) if sel.sum() > 20 else col_ramp[k - 1]
        sel_a = in_ref & ~target & (sb == b)
        a_ramp[k] = alpha[sel_a].mean() if sel_a.sum() > 20 else (a_ramp[k - 1] if k else 0)
    col_ramp[:20] = col_ramp[20]
    col_ramp = ndimage.uniform_filter1d(col_ramp, 9, axis=0)
    water = col_ramp[sb + 20]
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
    # tint by the nearest painted water (the Red Sea is bluer than the gulf the ramp came from)
    known_w = sea & (alpha > 200) & ~target & (d_sea > 15) & (d_any > 6)
    local = smooth_fill(rgb, known_w)
    tint = np.clip(local / rgb[gulf & (d_sea > 15)].mean(0), 0.6, 1.6)
    water = np.clip(water * tint, 0, 255)
    new_alpha = np.interp(signed, bins, a_ramp)

    # ── composite ──
    paint_rgb = np.where(sea[..., None], water, land_rgb)
    fringe = (np.clip(1 - d_land / 6, 0, 1) * land)[..., None]      # coast edge leans to the shore colour
    paint_rgb = paint_rgb * (1 - 0.35 * fringe) + col_ramp[20] * 0.35 * fringe
    w3 = weight[..., None]
    out = np.dstack([np.clip(rgb * (1 - w3) + paint_rgb * w3, 0, 255),
                     np.clip(alpha * (1 - weight) + new_alpha * weight, 0, 255)]).astype(np.uint8)

    a.out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out[..., :3]).resize((W // 4, H // 4)).save(a.out / "preview.png")
    Image.fromarray(orig[..., :3]).resize((W // 4, H // 4)).save(a.out / "preview_orig.png")
    new0 = {}
    for j in range(J0, J1):
        for i in range(I0, I1):
            sl = (slice((j - J0) * TILE, (j - J0 + 1) * TILE), slice((i - I0) * TILE, (i - I0 + 1) * TILE))
            if not np.array_equal(out[sl], orig[sl]):
                new0[(i, j)] = out[sl]
    stpi, stpd, per_mip = st.write(new0)
    build_pack(a.out / "new_regions_map.pack", [(STPI, stpi), (STPD, stpd)])
    print(f"tiles written per mip {per_mip}; stpd +{len(stpd) - st.stpd_size:,} B; "
          f"wrote {a.out / 'new_regions_map.pack'} -> data/ (movie pack, auto-loads)")


if __name__ == "__main__":
    main()
