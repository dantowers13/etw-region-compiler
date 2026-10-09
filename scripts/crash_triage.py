"""
Read what Windows recorded about Empire.exe crashes, so a failed test tells us
which loader broke instead of just "it crashed".

    python scripts/crash_triage.py --game-dir "X:/Games/Steam/steamapps/common/Empire Total War" [--last 3]

Prints, per minidump in %LOCALAPPDATA%\\CrashDumps: fault address as Empire.exe
offset, registers, the instructions around EIP (needs `pip install capstone`),
and which known crash site it matches. Known sites come from the March 2026
event log (see docs/reverse_eng/deep_dive.md).

WER only writes dumps when LocalDumps is enabled. If none appear after a crash:
  reg add "HKLM\\SOFTWARE\\Microsoft\\Windows\\Windows Error Reporting\\LocalDumps" /v DumpType /t REG_DWORD /d 2 /f
"""

from __future__ import annotations

import argparse
import glob
import os
import struct
from pathlib import Path

KNOWN = {
    0x00757851: "pathfinder edge with null endpoint node: startpos OBSTACLE_BASE_GRID_NODE ids are positions in the "
                "grid's flat record/empty-cell sequence and were not renumbered after inserting footprint records "
                "(Sep 6, fixed: patch_startpos_pathfinder)",
    0x00756AE0: "same as +0x757851 (other branch of the same pathfinder routine)",
    0x000BFDD0: "getter [ecx+0x18] on null+8: CAI_WORLD_REGIONS wrapper OWNED_INDIRECT was 0 for the new region "
                "(end turn after it changed hands, Sep 6). Fixed in reactivate_region.py",
    0x000BFD80: "null this-pointer in 4-instruction getter (Mar 8 x6: 206th regions entry / CAI_WORLD_REGIONS short)",
    0x00553086: "reads [obj+0x1dc]->[+0x34], sub-object null (Mar 8-11 x7; ALSO vanilla 2025-03-01)",
    0x0056917e: "vcall chain ->[+0xf8]->[+0x18c] null (Mar 11 x14, occitania v5-v8)",
    0x008ea2b3: "destructor: [this+0x8c] object vtable null (Mar 11 x3)",
    0x004e2165: "db/regions_tables lookup failed for a regions.esf region_keys name "
                "('In table %S: %S is not a valid key' then null REGION_RECORD colour read): "
                "the DB pack is not loaded (Sep 6: UTF-8 user.script.txt ignored; Mar 8/11 x6 same)",
    0x00bfec4e: "colour/byte copy from [ecx+0x24][idx] null (Mar 8 x2)",
    0x00174d48: "[esi+4] with esi null, buffer append (Mar 7 x3, first pathfinding split)",
    0x00c98d72: "WS_LOADING_SCREEN_IMP::pf_on_create null (DME launcher, not the mod)",
    0x0040317c: "vcall [eax+8] on element with null vtable while iterating an array (unlock_factions test)",
    0x004a2677: "[ecx+0x10][idx] null table (unlock_factions test)",
    0x00051470: "integer hash of a null key (shared; see the caller line below). Oct 1 new_regions hit it from the "
                "hash-map find at +0x733820 during PATHFINDING_GRID load: an out-of-range OBSTACLE_BOUNDARIES "
                "entry index (deep_dive 8.14)",
    0x006f6b00: "read of 0x3f800008 walking an obstacle cell list: two grid nodes resolve to the same cell because a "
                "node references boundary entries of ANOTHER cell (unremapped donor indices; Oct 1 "
                "arabia_manager_fix; deep_dive 8.14)",
    0x00758ff6: "pathfinder path-straightening (funnel) looped until its array doubling failed (memory/disk climbs, "
                "game freezes first): two regions' land touching with no border strip (Oct 6 batch1, Lyonnais/"
                "Burgundy; deep_dive 10.8)",
    0x00596e02: "transport-graph link lookup returned null: the AI asked for a leg between two adjacent regions "
                "with no CAMPAIGN_TRADE_MANAGER TRADE_ROUTE; the node pair is at [esp+0x58]/[esp+0x5c] (Oct 2 "
                "labrador-wilderness_canada, deep_dive 8.17; Oct 8 batch2 rumelia-hudavendigar across the "
                "Bosphorus, deep_dive 10.17)",
    0x00d96afe: "fast-fail 7 (abort) from _purecall (+0xd9ced6): the campaign AI (CAI do_cai_step; first seen on "
                "Persia's turn in a Georgia campaign, Oct 10 also in other campaigns, ~turn 10) called a method on "
                "a deleted object. +0x8a0800 per list element: elem+0x2c "
                "yields a target (+0x507a70) passed to +0x824560, whose [arg]->[+0xc] is pure: the arg's vtable "
                "+0xe5b564 is an abstract base (slots 1-5 _purecall). The live object it is compared against links "
                "to an admiral (Oct 8 x2, Oct 9 x2, Oct 10 x2; 1.2-1.5 GB dumps, game hangs first)",
    0x004f2c13: "read of a small bogus address (0x0002411e) through ecx (Oct 10, once, between two +0xd96afe "
                "crashes; not analysed yet)",
    0x0064f66f: "AI region route step: region missing from the AI region-graph map (great_plains/new_mexico, whose "
                "only land neighbours were dormant; Oct 1 new_regions_noobs; deep_dive 8.14)",
}

# Shared functions (the hash at +0x51470) are told apart by the first return address.
KNOWN_CALLERS = {
    0x005e0bfb: "stored INTERNATIONAL_TRADE_ROUTE names a region that does not hold its port/settlement node, "
                "after the loading bar (Oct 6 batch1_s3_unlocked: Le Havre moved to Normandy; deep_dive 10.7; "
                "Oct 8 batch2b: a middle hop, Basra port under Mesopotamia, 10.18). The route's 24-byte hop "
                "records (region*, v2, depart, next, by-sea) are at esi",
    0x00bf686e: "stream read (+0x174d40) of a float3; if +0xc93bb8 follows, it is a rigid_spline's points: a "
                "control-point count that is not 3k + 1 (a 3-point spline loops ~2^32 times past the file; Oct 6 "
                "batch1z border lines; deep_dive 10.14)",
}


def parse(path: str, exe: bytes | None):
    d = open(path, "rb").read()
    sig, ver, nstreams, diroff = struct.unpack_from("<4sIII", d, 0)
    assert sig == b"MDMP"
    streams = {}
    for i in range(nstreams):
        t, sz, off = struct.unpack_from("<III", d, diroff + 12 * i)
        streams.setdefault(t, []).append((sz, off))
    mods = []
    sz, off = streams[4][0]
    n = struct.unpack_from("<I", d, off)[0]
    for i in range(n):
        base, size, cs, ts, nameoff = struct.unpack_from("<QIIII", d, off + 4 + 108 * i)
        L = struct.unpack_from("<I", d, nameoff)[0]
        mods.append((base, size, d[nameoff + 4:nameoff + 4 + L].decode("utf-16-le")))
    exe_mod = next(m for m in mods if m[2].lower().endswith("empire.exe"))
    ranges = []
    if 9 in streams:
        sz, off = streams[9][0]
        n, baserva = struct.unpack_from("<QQ", d, off)
        cur = baserva
        for i in range(n):
            start, size = struct.unpack_from("<QQ", d, off + 16 + 16 * i)
            ranges.append((start, size, cur))
            cur += size
    if 5 in streams:
        sz, off = streams[5][0]
        n = struct.unpack_from("<I", d, off)[0]
        for i in range(n):
            start, size, rva = struct.unpack_from("<QII", d, off + 4 + 16 * i)
            ranges.append((start, size, rva))

    def read(addr, k):
        for s, size, rva in ranges:
            if s <= addr < s + size:
                return d[rva + (addr - s): rva + (addr - s) + k]
        return None

    sz, off = streams[6][0]
    tid, _, code, flags, rec, addr = struct.unpack_from("<IIIIQQ", d, off)
    # MINIDUMP_EXCEPTION_STREAM: ThreadId, pad, then MINIDUMP_EXCEPTION at +8, whose
    # NumberParameters sits at +32 and ExceptionInformation[15] at +40
    nparams = struct.unpack_from("<I", d, off + 32)[0]
    params = struct.unpack_from("<15Q", d, off + 40)[:nparams]
    csz, coff = struct.unpack_from("<II", d, off + 8 + 152)
    ctx = d[coff:coff + csz]
    regs = {n_: struct.unpack_from("<I", ctx, o)[0] for n_, o in
            [("eax", 0xB0), ("ebx", 0xA4), ("ecx", 0xAC), ("edx", 0xA8), ("esi", 0xA0), ("edi", 0x9C),
             ("ebp", 0xB4), ("esp", 0xC4), ("eip", 0xB8)]}
    rva = addr - exe_mod[0]
    print(f"\n### {os.path.basename(path)}  {os.path.getsize(path):,} B  {Path(path).stat().st_mtime:.0f}")
    print(f"exception {code:#010x} at Empire.exe+{rva:#x}" +
          (f"  ({'write' if params[0] == 1 else 'read'} of {params[1]:#010x})" if code == 0xC0000005 else ""))
    print("regs", {k: f"{v:#010x}" for k, v in regs.items()})
    hit = next((v for k, v in KNOWN.items() if abs(k - rva) < 0x40), None)
    print("known site:", hit or "NEW - not seen before")
    code_bytes = read(regs["eip"], 32) or b""
    try:
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        for ins in list(md.disasm(code_bytes, regs["eip"]))[:6]:
            print(f"   {ins.address - exe_mod[0]:08x}  {ins.mnemonic} {ins.op_str}")
    except ImportError:
        print("   bytes @eip:", code_bytes[:16].hex(" "), "(pip install capstone for disassembly)")
    for r in ("eax", "ecx", "edx", "esi", "edi", "ebx"):
        b = read(regs[r], 48)
        if b and any(32 <= c < 127 for c in b[:8]):
            asc = bytes(c if 32 <= c < 127 else 46 for c in b).decode()
            if asc.count(".") < 30:
                print(f"   [{r}] -> {asc}")
    stack = read(regs["esp"], 0x1000) or b""
    rets = []
    for i in range(0, len(stack) - 3, 4):
        v = struct.unpack_from("<I", stack, i)[0]
        if exe_mod[0] <= v < exe_mod[0] + exe_mod[1]:
            prev = read(v - 5, 5)
            if prev and (prev[0] == 0xE8 or 0xFF in prev[:4]):
                rets.append(f"+{v - exe_mod[0]:#x}")
    print("   probable return addresses:", rets[:10])
    if rets and int(rets[0][1:], 16) in KNOWN_CALLERS:
        print("   caller:", KNOWN_CALLERS[int(rets[0][1:], 16)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", type=Path)
    ap.add_argument("--last", type=int, default=3)
    a = ap.parse_args()
    dumps = sorted(glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "CrashDumps", "Empire.exe.*.dmp")),
                   key=os.path.getmtime)[-a.last:]
    if not dumps:
        print("no Empire.exe dumps in %LOCALAPPDATA%\\CrashDumps (see docstring to enable LocalDumps)")
    for p in dumps:
        parse(p, None)
    print("\nEvent log (PowerShell): Get-WinEvent -FilterHashtable @{LogName='Application';ProviderName='Application Error'} "
          "-MaxEvents 50 | ? Message -like '*Empire.exe*' | select TimeCreated,Message")


if __name__ == "__main__":
    main()
