"""
Install / restore a region mod built by reactivate_region.py, with exact backups.

The game reads regions.esf and startpos.esf as loose files, so a mod has to
replace them. Your current save games are NOT touched, but a save made with the
vanilla files should only be loaded with the vanilla files in place: run
`restore` before going back to an existing campaign, `install` before starting a
new one with the extra region.

    python scripts/deploy_mod.py status  --game-dir "X:/Games/Steam/steamapps/common/Empire Total War" --mod out/wilderness_arabia
    python scripts/deploy_mod.py install --game-dir ... --mod out/wilderness_arabia
    python scripts/deploy_mod.py restore --game-dir ... --mod out/wilderness_arabia

install: copies each current game file to data/_region_mod_backup/<name> (only if
no backup exists yet, so the first install's originals are what restore brings
back), writes the mod files and drops the .pack into data/. The pack is a
"movie" pack (PFH0 type 4), which the game loads with no script entry at all.
With --script (or for a type-3 mod pack) it also adds `mod <pack>;` to
user.script.txt, written as UTF-16LE with BOM like every CA script file; a
UTF-8 file is silently ignored, which is exactly how the first test failed
(regions_tables had no row for the new region -> crash on the campaign button).
restore reverses all of that.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import sys
from pathlib import Path

TARGETS = {
    "regions.esf": "data/campaign_maps/global_map/regions.esf",
    "startpos.esf": "data/campaigns/main/startpos.esf",
    "pathfinding.esf": "data/campaign_maps/global_map/pathfinding.esf",
}
OPTIONAL = {"pathfinding.esf"}      # only builds made with --footprints ship one


def sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p.exists() else "missing"


def user_script() -> Path:
    return Path(os.environ["APPDATA"]) / "The Creative Assembly" / "Empire" / "scripts" / "user.script.txt"


def read_script(p: Path) -> str:
    if not p.exists():
        return ""
    raw = p.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def write_script(p: Path, text: str) -> None:
    p.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))


def pack_type(p: Path) -> int:
    return struct.unpack_from("<I", p.read_bytes(), 4)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "install", "restore"])
    ap.add_argument("--game-dir", type=Path, required=True)
    ap.add_argument("--mod", type=Path, required=True, help="output dir of reactivate_region.py")
    ap.add_argument("--script", action="store_true", help="also register the pack in user.script.txt (UTF-16)")
    a = ap.parse_args()
    game, mod = a.game_dir, a.mod
    backup = game / "data" / "_region_mod_backup"
    packs = sorted(mod.glob("*.pack"))
    if not packs:
        sys.exit(f"no .pack in {mod}")
    pack = packs[0]
    ptype = pack_type(pack)
    line = f"mod {pack.name};"
    us = user_script()

    if a.action == "status":
        for name, rel in TARGETS.items():
            print(f"{name:14} game={sha(game / rel)}  mod={sha(mod / name)}  backup={sha(backup / name)}")
        print(f"{pack.name:14} in data/: {(game / 'data' / pack.name).exists()}  type={ptype} "
              f"({'movie, auto-loads' if ptype == 4 else 'mod, needs a script line'})")
        print(f"user.script.txt: {us} exists={us.exists()} has_line={line in read_script(us)}")
        return

    if a.action == "install":
        backup.mkdir(parents=True, exist_ok=True)
        for name, rel in TARGETS.items():
            src, dst, bak = mod / name, game / rel, backup / name
            if not src.exists():
                if name in OPTIONAL:
                    continue
                sys.exit(f"mod file missing: {src}")
            if not bak.exists():
                shutil.copy2(dst, bak)
                print(f"backed up {dst.name} -> {bak}")
            shutil.copy2(src, dst)
            print(f"installed {name} -> {dst}")
        data = bytearray(pack.read_bytes())
        if a.script:
            struct.pack_into("<I", data, 4, 3)     # register via script => make it a real mod pack (type 3)
        (game / "data" / pack.name).write_bytes(data)
        ptype = struct.unpack_from("<I", data, 4)[0]
        print(f"installed {pack.name} -> data/ (type {ptype}, {'script-registered mod pack' if ptype == 3 else 'auto-loading movie pack'})")
        if a.script or ptype != 4:
            us.parent.mkdir(parents=True, exist_ok=True)
            txt = read_script(us)
            if line not in txt:
                write_script(us, (txt.rstrip("\r\n") + "\r\n" if txt.strip() else "") + line + "\r\n")
                print(f"added '{line}' to {us} (UTF-16LE with BOM)")
        print("\nStart a NEW campaign to test. Run `restore` before loading an older save.")
        return

    if a.action == "restore":
        for name, rel in TARGETS.items():
            bak, dst = backup / name, game / rel
            if bak.exists():
                shutil.copy2(bak, dst)
                print(f"restored {dst} from backup")
            else:
                print(f"no backup for {name}; left as is")
        p = game / "data" / pack.name
        if p.exists():
            p.unlink()
            print(f"removed data/{pack.name}")
        if us.exists():
            txt = read_script(us)
            if line in txt:
                txt = txt.replace(line + "\r\n", "").replace(line + "\n", "").replace(line, "")
                if txt.strip():
                    write_script(us, txt)
                else:
                    us.unlink()
                print(f"removed '{line}' from {us}")


if __name__ == "__main__":
    main()
