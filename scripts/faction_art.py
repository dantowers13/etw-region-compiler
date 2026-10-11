"""
Make a faction's whole art set from one master flag (and optionally a coin emblem), in the
formats add_faction.py ships (deep_dive 11.7):

  ui_flags/large.tga 512x256, flag_132.tga 132x66, mini_flag.tga 44x22   the flag, flat
  ui_flags/flag_animation0000-0011.tga 200x100                            waving: a vanilla frame's
                                                                          outline, a moving fold shade
  ui_flags/hud_left.tga, hud_right.tga 40x79, small.tga 19x24,            hanging pennants: a vanilla
  portrait_flags.tga 140x116                                              shape, its gold finials kept
  flag_<key>.dds 512x256 (3D campaign flag), ensign_<key>_diffuse.dds     DDS (add_faction compresses)
  1024x512 (ships), agent_id_<key>_diffuse.tga 128x128 (agent coin)

Vanilla shapes come from a reference faction's UI flags in ui.pack (georgia: red crosses on white,
so its flag pixels are easy to tell from the gold finials). With --cloth (default) the flat images
take the brightness variation of the atlas's own cloth flag (flag_default), so they sit with the
vanilla flags instead of looking flat.

    python scripts/faction_art.py --key moldavia --master master.png [--emblem coin.png]
    python scripts/faction_art.py --key wallachia --master out/faction_assets/wallachia/ui_flags/large.tga \\
        --emblem out/faction_assets/wallachia/wallachia_option2_1024.png --only-cloth
"""

from __future__ import annotations

import argparse
import io
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
from reactivate_region import pack_file  # noqa: E402

UI = ["flag_132.tga", "mini_flag.tga", "hud_left.tga", "hud_right.tga", "portrait_flags.tga", "small.tga"]


def vanilla(game_data: Path, ref: str, name: str) -> Image.Image:
    return Image.open(io.BytesIO(pack_file(game_data / "ui.pack", f"ui\\flags\\{ref}\\{name}"))).convert("RGBA")


def cloth_shade(game_data: Path, size: tuple[int, int]) -> np.ndarray:
    """Brightness of the atlas's flag_default cloth (flags1.dds, u 0.75 v 0.375, 512 x 256),
    as a multiplier around 1."""
    d = pack_file(game_data / "models.pack", "rigidmodels\\flags\\textures\\flags1.dds")
    a = Image.open(io.BytesIO(d))
    a.load()
    c = a.convert("L").crop((1536, 768, 2048, 1024)).resize(size, Image.LANCZOS)
    lum = np.asarray(c, dtype=np.float32)
    return np.clip(1 + 0.9 * (lum / lum.mean() - 1), 0.75, 1.2)[..., None]


def shaded(img: Image.Image, shade: np.ndarray | None) -> Image.Image:
    if shade is None:
        return img
    a = np.asarray(img.convert("RGBA"), dtype=np.float32)
    a[..., :3] = np.clip(a[..., :3] * shade, 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def flag_pixels(ref: Image.Image) -> np.ndarray:
    """Which pixels of a georgia pennant are flag cloth (white or red), not gold finial / frame."""
    a = np.asarray(ref, dtype=np.int32)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    white = (r > 150) & (g > 150) & (b > 150) & (abs(r - b) < 40)
    gold = (r > 90) & (r >= g) & (g > b + 20) & (g > 0.6 * r)       # finials, gilt edging, their highlights
    dark = np.maximum(np.maximum(r, g), b) < 60                       # outlines
    return (al > 0) & ~gold & ~dark                                   # all else is cloth (incl. pink edges)


def pennant(master: Image.Image, ref: Image.Image) -> Image.Image:
    """The master laid over a vanilla pennant's cloth pixels (stripes run down the hanging cloth),
    keeping the reference's alpha, finials and fold shading."""
    w, h = ref.size
    m = np.asarray(master.convert("RGB").rotate(90, expand=True).resize((w, h), Image.LANCZOS), dtype=np.float32)
    a = np.asarray(ref, dtype=np.float32)
    mask = flag_pixels(ref)
    # the folds, without the reference's own pattern (georgia's crosses): its brightness with
    # the red parts lifted to the white's level, then blurred
    lum = a[..., :3].max(axis=2)
    ref_lum = np.median(lum[mask]) if mask.any() else 1
    blur = np.asarray(Image.fromarray(np.clip(lum, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(max(1, w / 20))), dtype=np.float32)
    fold = np.clip(blur / max(ref_lum, 1), 0.7, 1.15)[..., None]
    out = a.copy()
    out[..., :3] = np.where(mask[..., None], np.clip(m * fold, 0, 255), a[..., :3])
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def wave_frame(master: Image.Image, ref: Image.Image, i: int) -> Image.Image:
    """Frame i of 12: the master in the vanilla frame's outline, a fold shade moving along it."""
    w, h = ref.size
    m = np.asarray(master.convert("RGB").resize((w, h), Image.LANCZOS), dtype=np.float32)
    x = np.arange(w, dtype=np.float32)[None, :, None]
    y = np.arange(h, dtype=np.float32)[:, None, None]
    phase = i / 12 * 2 * math.pi
    shade = 1 + 0.10 * np.sin(2 * math.pi * (x / w * 1.6) - phase + y / h * 0.6)
    out = np.dstack([np.clip(m * shade, 0, 255), np.asarray(ref, dtype=np.float32)[..., 3:]])
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def coin(master: Image.Image, emblem: Image.Image | None) -> Image.Image:
    """128 x 128: the emblem as given, else a disc of the flag's centre on black."""
    if emblem is not None:
        return emblem.convert("RGBA").resize((128, 128), Image.LANCZOS)
    m = master.convert("RGBA")
    s = min(m.size)
    disc = m.crop(((m.width - s) // 2, 0, (m.width + s) // 2, s)).resize((128, 128), Image.LANCZOS)
    out = Image.new("RGBA", (128, 128), (0, 0, 0, 255))
    mask = Image.new("L", (128, 128), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, 125, 125), fill=255)
    out.paste(disc, (0, 0), mask)
    ImageDraw.Draw(out).ellipse((2, 2, 125, 125), outline=(200, 160, 60, 255), width=4)
    return out


def save_dds(img: Image.Image, path: Path) -> None:
    img.convert("RGBA").save(path, "DDS")       # uncompressed; add_faction makes it DXT + mips


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", required=True)
    ap.add_argument("--master", type=Path, required=True, help="the flag, any size (2:1 is best)")
    ap.add_argument("--emblem", type=Path, help="the agent coin emblem, square, any size")
    ap.add_argument("--out", type=Path, help="default out/faction_assets/<key>")
    ap.add_argument("--ref", default="georgia", help="vanilla faction whose shapes are used")
    ap.add_argument("--no-cloth", action="store_true", help="leave the flat images flat")
    ap.add_argument("--only-cloth", action="store_true",
                    help="only re-make the flat flags (large, 132, mini, campaign flag) with cloth")
    ap.add_argument("--game-data", type=Path, default=Path("../data"))
    a = ap.parse_args()

    out = a.out or Path("out/faction_assets") / a.key
    (out / "ui_flags").mkdir(parents=True, exist_ok=True)
    master = Image.open(a.master).convert("RGBA")
    emblem = Image.open(a.emblem) if a.emblem else None
    shade = None if a.no_cloth else cloth_shade(a.game_data, (512, 256))

    flat = shaded(master.resize((512, 256), Image.LANCZOS), shade)
    flat.save(out / "ui_flags" / "large.tga")
    flat.convert("RGB").resize((132, 66), Image.LANCZOS).save(out / "ui_flags" / "flag_132.tga")
    flat.convert("RGB").resize((44, 22), Image.LANCZOS).save(out / "ui_flags" / "mini_flag.tga")
    save_dds(flat, out / f"flag_{a.key}.dds")
    made = ["large", "flag_132", "mini_flag", f"flag_{a.key}.dds"]
    if not a.only_cloth:
        for i in range(12):
            wave_frame(flat, vanilla(a.game_data, a.ref, f"flag_animation{i:04d}.tga"), i).save(
                out / "ui_flags" / f"flag_animation{i:04d}.tga")
        for n in ("hud_left.tga", "hud_right.tga", "small.tga", "portrait_flags.tga"):
            pennant(flat, vanilla(a.game_data, a.ref, n)).save(out / "ui_flags" / n)
        ens = Image.new("RGBA", (1024, 512))
        ens.paste(shaded(master.resize((896, 512), Image.LANCZOS), None if shade is None else
                         np.asarray(Image.fromarray((shade[..., 0] * 100).astype(np.uint8)).resize((896, 512)),
                                    dtype=np.float32)[..., None] / 100), (128, 0))
        ens.paste(master.rotate(90, expand=True).resize((128, 512), Image.LANCZOS), (0, 0))
        save_dds(ens, out / f"ensign_{a.key}_diffuse.dds")
        coin(flat, emblem).save(out / f"agent_id_{a.key}_diffuse.tga")
        made += ["12 waving frames", "hud_left", "hud_right", "small", "portrait_flags",
                 f"ensign_{a.key}_diffuse.dds", f"agent_id_{a.key}_diffuse.tga"]
    print(f"{a.key}: wrote {', '.join(made)} to {out}" + ("" if shade is not None else " (no cloth)"))


if __name__ == "__main__":
    main()
