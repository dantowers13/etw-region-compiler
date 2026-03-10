"""
Repaint europe_lookup.tga: split France (value 230) into
  - france    (north, wy >= 332, py < 149)  → keep value 230
  - occitania (south, wy <  332, py >= 149) → new value 254

Coordinate transform (confirmed empirically):
  ORIGIN_X = -180.0, ORIGIN_Y = 140.0, CELL_SIZE = 2.0, COLS=310, ROWS=190
  world_w = 620, world_h = 380
  TGA 490×300, north-up (py=0 = highest wy)
  wx → px: px = (wx - ORIGIN_X) * (490 / 620)
  wy → py: py = (ORIGIN_Y + world_h - wy) * (300 / 380) = (520 - wy) * (300/380)
  Split at wy=332  →  py = (520-332)*300/380 ≈ 148.4  →  split_py = 149

Run from repo root:
    python scripts/update_tga.py
"""

from pathlib import Path
from PIL import Image

# ─── Config ───────────────────────────────────────────────────────────────────

INPUT_TGA  = Path("data/gc/europe_lookup.tga")
OUTPUT_TGA = Path("data/gc/europe_lookup_occitania.tga")

FRANCE_VALUE    = 230   # confirmed: Paris + Toulouse both read 230
OCCITANIA_VALUE = 254   # only unused slot in palette (105-255)

# Pathfinding split: wy < 332 → occitania
# TGA py = (520 - wy) * 300/380  →  wy=332 gives py≈148.4
# All pixels at py >= 149 that are France become Occitania
SPLIT_PY = 149

# ─── Process ──────────────────────────────────────────────────────────────────

img = Image.open(INPUT_TGA).copy()  # copy() makes it writable
assert img.mode == "P", f"Expected paletted TGA, got {img.mode}"
w, h = img.size
assert (w, h) == (490, 300), f"Unexpected TGA size {w}×{h}"

pixels = img.load()

repainted = 0
for py in range(SPLIT_PY, h):
    for px in range(w):
        if pixels[px, py] == FRANCE_VALUE:
            pixels[px, py] = OCCITANIA_VALUE
            repainted += 1

print(f"Repainted {repainted} pixels (France->Occitania, py>={SPLIT_PY})")

# Verify palette slot 254 is available (should differ from slot 230)
pal = img.getpalette()  # flat RGB list, 768 ints
r230, g230, b230 = pal[230*3], pal[230*3+1], pal[230*3+2]
r254, g254, b254 = pal[254*3], pal[254*3+1], pal[254*3+2]
print(f"Palette slot 230 (france):    RGB({r230},{g230},{b230})")
print(f"Palette slot 254 (occitania): RGB({r254},{g254},{b254})")

# Set occitania palette entry to a distinct colour so it's visible in a viewer
# Use a warm orange similar to France but slightly different
# (The game likely uses its own region colour from db tables, not the palette)
pal[254*3]   = 220
pal[254*3+1] = 140
pal[254*3+2] = 60
img.putpalette(pal)

OUTPUT_TGA.parent.mkdir(parents=True, exist_ok=True)
img.save(OUTPUT_TGA)
print(f"Saved: {OUTPUT_TGA}  ({OUTPUT_TGA.stat().st_size:,} bytes)")

# ─── Sanity checks ────────────────────────────────────────────────────────────

img2 = Image.open(OUTPUT_TGA)
pix2 = img2.load()

# Paris (px≈155, py≈137) should still be 230 (north france)
paris_val = pix2[155, 137]
# Toulouse (px≈143, py≈165) should now be 254 (occitania)
toulouse_val = pix2[143, 165]

print(f"\nSanity checks:")
print(f"  Paris    (px=155, py=137): value={paris_val}  (expected 230)")
print(f"  Toulouse (px=143, py=165): value={toulouse_val}  (expected 254)")

assert paris_val == FRANCE_VALUE,    f"Paris sanity FAIL: got {paris_val}"
assert toulouse_val == OCCITANIA_VALUE, f"Toulouse sanity FAIL: got {toulouse_val}"
print("  PASS")

# Count remaining France pixels and new Occitania pixels
france_count    = sum(1 for py in range(h) for px in range(w) if pix2[px,py] == FRANCE_VALUE)
occitania_count = sum(1 for py in range(h) for px in range(w) if pix2[px,py] == OCCITANIA_VALUE)
print(f"\n  France pixels remaining: {france_count}")
print(f"  Occitania pixels:        {occitania_count}")
print(f"  Total:                   {france_count + occitania_count}")
