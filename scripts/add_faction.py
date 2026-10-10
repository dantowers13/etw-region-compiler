"""
Add brand-new factions to a region build: startpos records, DB rows, text and flags.

M0 (deep_dive 11.3): factions start with no land. Each one is a clone of a template faction
(an empty emergent such as greece), with every per-faction structure extended:

  FACTION_ARRAY            the template's FACTION record; its private object ids (government
                           posts, technology manager) renumbered, key / name replaced
  DIPLOMACY_RELATIONSHIPS  every faction gets a relationship to the new one (a copy of its
                           relationship to the template), and the new one to every faction
  CAI_WORLD_FACTIONS       the template's AI faction record, new AI ids, belief caches cleared
  CAI_INTERFACE_MANAGERS   the template's AI manager; every pool object it defines renumbered
                           and registered in the AI world's pool-object list (CAI_WORLD [32])
  CAI_WORLD_TECHNOLOGY_TREES, SPYING_ARRAY, FACTION_INFOS, PLAYERS_ARRAY (x2),
  DOMESTIC_ / INTERNATIONAL_TRADE_ROUTES   one entry each, cloned from the template's
  global AI pool           the template's five per-faction beliefs (diplomatic, relation, owned
                           regions, absolute, recruitment analyses), listed by their analysers
  CAI_DIPLOMATIC_ANALYSIS_FACTIONINFO       an entry in every AI's diplomatic analysis

The per-pair AI beliefs (attitude analyses, relation timelines) are left for the engine to
build: Lord's new factions lack the timelines and play.

DB rows go into the region pack beside its own tables: factions (the template's row, new key,
names, flag path and row id), technology_faction_junctions (the template's rows), and text.
Flags come from out/faction_assets/<key>/ui_flags if present, else the faction shows the template's.

    python scripts/add_faction.py --mod out/batch3b_s3 --out out/m0_wallachia --factions wallachia
    python scripts/add_faction.py --mod out/batch3b_s3 --out out/m0_cap90 --dummies 34
"""

from __future__ import annotations

import argparse
import collections
import random
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from etwpc.io.esf_reader import ESFReader  # noqa: E402
from etwpc.io.esf_writer import ESFWriter  # noqa: E402
from etwpc.io.esf_types import ESFNode, ESFPrimitive, T_UNICODE, T_ASCII  # noqa: E402
from reactivate_region import (  # noqa: E402
    IdPool, build_loc, build_pack, clear_bdi, deep_copy, set_int, set_list, set_str, ws,
)


@dataclass
class FactionSpec:
    key: str
    display: str                   # factions_screen_name
    adjective: str
    description: str = "Minor Faction"
    template: str = "greece"       # an empty emergent faction to clone
    flag_key: str = ""             # ui flags folder: own art if present, else the template's (main)


FACTIONS = {
    "wallachia": FactionSpec("wallachia", "Wallachia", "Wallachian", "Principality of Wallachia"),
}

DB_PACKS = ["main.pack", "patch.pack", "patch2.pack", "patch3.pack", "patch4.pack", "patch5.pack"]


# ─── ESF helpers ──────────────────────────────────────────────────────────────

def children(x):
    return x.children if isinstance(x, ESFNode) else x


def find_fast(root, tag):
    """Breadth-first, deque-based (reactivate_region.find pops from a list head, which is
    quadratic deep in the AI pools)."""
    q = collections.deque([root])
    while q:
        x = q.popleft()
        if isinstance(x, ESFNode) and x.tag == tag:
            return x
        q.extend(c for c in children(x) if not isinstance(c, ESFPrimitive))
    return None


def find_all_fast(root, tags: set[str]) -> dict[str, list]:
    out = {t: [] for t in tags}
    st = [root]
    while st:
        x = st.pop()
        if isinstance(x, ESFNode) and x.tag in tags:
            out[x.tag].append(x)
        st.extend(c for c in children(x) if not isinstance(c, ESFPrimitive))
    return out


def sub(item, tag):
    return next((c for c in children(item) if isinstance(c, ESFNode) and c.tag == tag), None)


def prims(x):
    st = [x]
    while st:
        y = st.pop()
        if isinstance(y, ESFPrimitive):
            yield y
        else:
            st.extend(children(y))


def int_counts(x) -> collections.Counter:
    c = collections.Counter()
    for p in prims(x):
        v = p.value
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            c[v] += 1
        elif isinstance(v, (list, tuple)):
            c.update(e for e in v if isinstance(e, int) and not isinstance(e, bool))
    return c


def remap(x, m: dict[int, int]) -> None:
    for p in prims(x):
        v = p.value
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and v in m:
            set_int(p, m[v])
        elif isinstance(v, (list, tuple)) and any(isinstance(e, int) and e in m for e in v):
            set_list(p, [m.get(e, e) if isinstance(e, int) and not isinstance(e, bool) else e for e in v])


def restring(x, m: dict[str, str]) -> None:
    for p in prims(x):
        if p.type_tag in (T_UNICODE, T_ASCII) and p.value in m:
            set_str(p, m[p.value])


def belief_body(o):
    """A pool object's own record: its last child record that is not a BDI component."""
    return next((c for c in reversed(o) if isinstance(c, ESFNode)
                 and c.tag not in ("CAI_BDI_COMPONENT_PROPERTY_SET", "CAI_BDI_COMPONENT_BLOCK_OWNS")), None)


def faction_key_index(F) -> int:
    return next(i for i, c in enumerate(F.children) if isinstance(c, ESFPrimitive) and isinstance(c.value, str))


def item_key(item) -> str | None:
    for c in children(item):
        if isinstance(c, ESFPrimitive) and isinstance(c.value, str):
            return c.value
        if isinstance(c, ESFNode) and c.tag == "CAMPAIGN_PLAYER_SETUP":
            return next(p.value for p in c.children if isinstance(p, ESFPrimitive) and isinstance(p.value, str))
    return None


# ─── startpos ─────────────────────────────────────────────────────────────────

class Startpos:
    def __init__(self, root):
        self.root = root
        self.fa = find_fast(root, "FACTION_ARRAY")
        self.cwf = find_fast(root, "CAI_WORLD_FACTIONS")
        self.cim = find_fast(root, "CAI_INTERFACE_MANAGERS")
        self.ctt = find_fast(root, "CAI_WORLD_TECHNOLOGY_TREES")
        self.counts = int_counts(root)
        self.ids = IdPool(set(self.counts))
        self.rng = random.Random(1700)
        # looked up once: walking the whole tree per faction is slow at 30+ factions
        self.analyses = find_all_fast(root, {"CAI_DIPLOMATIC_ANALYSIS"})["CAI_DIPLOMATIC_ANALYSIS"]
        gpool = sub(find_fast(root, "CAI_INTERFACE").children, "CAI_BDI_POOL")
        self.g_beliefs = sub(gpool.children, "CAI_BDI_POOL_BELIEFS")
        self.g_desires = sub(gpool.children, "CAI_BDI_POOL_DESIRES")
        self.arrays = find_all_fast(root, {"SPYING_ARRAY", "FACTION_INFOS", "PLAYERS_ARRAY",
                                           "DOMESTIC_TRADE_ROUTES", "INTERNATIONAL_TRADE_ROUTES"})
        cw = find_fast(root, "CAI_WORLD")
        self.registry = max((c for c in cw.children if isinstance(c, ESFPrimitive) and isinstance(c.value, (list, tuple))),
                            key=lambda c: len(c.value))
        mgr_ids = {sub(x, "CAI_FACTION_MANAGER").children[0].value for x in self.cim.children
                   if sub(x, "CAI_FACTION_MANAGER") is not None}
        # CAI_INTERFACE [29]: (AI faction id, manager kind) pairs, kind = CAI_FACTION_MANAGER [1]
        # (11; 2 for the last). The many faction lists inside the AI pools are working memory
        # and the engine keeps them.
        self.cai_pairs = next(p for p in find_fast(root, "CAI_INTERFACE").children
                              if isinstance(p, ESFPrimitive) and isinstance(p.value, (list, tuple))
                              and len(mgr_ids.intersection(p.value)) > 30)

    def faction(self, key):
        for it in self.fa.children:
            F = sub(it, "FACTION")
            k = faction_key_index(F)
            if F.children[k].value == key:
                return it, F, F.children[k - 1].value
        raise KeyError(key)

    def faction_ids(self) -> set[int]:
        out = set()
        for it in self.fa.children:
            F = sub(it, "FACTION")
            out.add(F.children[faction_key_index(F) - 1].value)
        return out

    def add(self, spec: FactionSpec) -> dict:
        T_item, T, t_fid = self.faction(spec.template)
        new_fid = self.ids.obj()

        # diplomacy: everyone else relates to the new faction as to the template
        n_rel = 0
        for it in self.fa.children:
            F = sub(it, "FACTION")
            dra = find_fast(F, "DIPLOMACY_RELATIONSHIPS_ARRAY")
            if F is T:
                src = dra.children[0]
            else:
                src = next(r for r in dra.children if sub(r, "DIPLOMACY_RELATIONSHIP").children[0].value == t_fid)
            r = deep_copy(src)
            set_int(sub(r, "DIPLOMACY_RELATIONSHIP").children[0], new_fid)
            dra.children.append(r)
            n_rel += 1

        # AI ids of the template
        t_cwf = next(it for it in self.cwf.children
                     if sub(it, "CAI_FACTION") is not None and sub(it, "CAI_FACTION").children[6].value == t_fid)
        t_cf = sub(t_cwf, "CAI_FACTION")
        t_cai, t_tech, t_x = t_cwf[2].value, t_cf.children[9].value, t_cf.children[12].value
        cai = {t_cai: self.ids.cai(), t_x: self.ids.cai(), t_tech: self.ids.cai()}
        c_id = cai[t_cai]

        t_tree = next(it for it in self.ctt.children if it[1].value == t_tech)
        t_mgr = next(it for it in self.cim.children
                     if sub(it, "CAI_FACTION_MANAGER") is not None and sub(it, "CAI_FACTION_MANAGER").children[0].value == t_cai)

        # object ids private to the template's FACTION record (and its AI tech tree): renumber
        fids = self.faction_ids() | {new_fid}
        inside = int_counts(T) + int_counts(t_tree)
        obj = {v: self.ids.obj() for v, n in inside.items()
               if 0x10000000 <= v < 0x40000000 and v not in fids and self.counts[v] == n}
        obj[t_fid] = new_fid

        # FACTION record
        it = deep_copy(T_item)
        F = sub(it, "FACTION")
        remap(F, obj)
        k = faction_key_index(F)
        set_str(F.children[k], spec.key)
        set_str(F.children[k + 1], spec.display)
        for nad in children(F):            # name-drawing seeds, one per name list
            if isinstance(nad, ESFNode) and nad.tag == "NAME_ALLOCATION_DETAILS":
                set_int(nad.children[1], self.rng.randrange(1 << 32))
        dra = find_fast(F, "DIPLOMACY_RELATIONSHIPS_ARRAY")
        selfrel = next(r for r in dra.children if sub(r, "DIPLOMACY_RELATIONSHIP").children[0].value == new_fid)
        set_int(sub(selfrel, "DIPLOMACY_RELATIONSHIP").children[0], t_fid)
        self.fa.children.append(it)

        # AI faction record
        c = deep_copy(t_cwf)
        remap(c, cai | {t_fid: new_fid})
        clear_bdi(c)
        self.cwf.children.append(c)

        # AI manager: every belief / desire / intention it defines (id at [2]) gets a fresh id,
        # and so does anything else private to it; the AI world's registry of pool objects
        # (the long list in CAI_WORLD, 148 of greece's 163) lists the copies too
        defined = {o[2].value for sect in sub(t_mgr, "CAI_BDI_POOL").children if isinstance(sect, ESFNode)
                   for o in sect.children if len(o) > 2 and isinstance(o[2], ESFPrimitive)}
        mc = int_counts(t_mgr)
        private = {v for v, n in mc.items() if v >= 1000 and self.counts[v] == n}
        bdi = {v: self.ids.cai() for v in sorted(defined | private) if v not in cai}

        # the global pool's beliefs about one faction (diplomatic, relation, owned-regions,
        # absolute and recruitment analyses: one each per AI faction), each listed by the
        # analyser desire that owns it: [0] belief ids, [1] (faction, belief) pairs, BLOCK_OWNS.
        # They and the manager name each other's objects, so one map renumbers both.
        glob = [o for o in self.g_beliefs.children if (b := belief_body(o)) is not None and b.children
                and isinstance(b.children[0], ESFPrimitive) and b.children[0].value == t_cai]
        gmap = {o[2].value: self.ids.cai() for o in glob}
        full = cai | bdi | gmap | {t_fid: new_fid}

        remap(c, full)             # the AI faction record names some of them too
        m = deep_copy(t_mgr)
        remap(m, full)
        self.cim.children.append(m)
        add = [bdi[v] for v in self.registry.value if v in bdi]

        n_glob = 0
        for o in glob:
            g, n = o[2].value, gmap[o[2].value]
            body = belief_body(o)
            x = deep_copy(o)
            remap(x, full)
            self.g_beliefs.children.append(x)
            if body.tag == "CAI_DIPLOMATIC_ANALYSIS":
                self.analyses.append(belief_body(x))
            for d in self.g_desires.children:
                an = sub(d, "CAI_ANALYSER")
                if an is None or g not in an.children[0].value:
                    continue
                # the belief's [10] is (owner, its slot in the owner's lists): the copy's slot is
                # the new last one, not the template's (a shared slot hung the game on exit)
                slot = len(an.children[0].value)
                ol = list(x[10].value)
                for j in range(0, len(ol), 2):
                    if ol[j] == d[2].value:
                        ol[j + 1] = slot
                set_list(x[10], ol)
                set_list(an.children[0], list(an.children[0].value) + [n])
                set_list(an.children[1], list(an.children[1].value) + [c_id, n])
                owns = sub(d, "CAI_BDI_COMPONENT_BLOCK_OWNS")
                e = deep_copy(next(it for it in owns.children if it[0].value == g))
                set_int(e[0], n)
                owns.children.append(e)
            if g in self.registry.value:
                add.append(n)
            n_glob += 1
        set_list(self.registry, list(self.registry.value) + add)

        # AI technology tree
        tt = deep_copy(t_tree)
        remap(tt, full | obj)
        clear_bdi(tt)
        self.ctt.children.append(tt)

        # nothing the template owns may be left in the copies (two records naming one object
        # made the game hang on exit)
        # (the FACTION copy only against object ids: its name lists hold small numbers that
        # coincide with AI ids)
        owned = (set(full) | set(obj)) - {t_fid}
        clones = [c, m, tt] + self.g_beliefs.children[-len(glob):]
        leak = {v for x in clones for v in int_counts(x) if v in owned}
        leak |= {v for v in int_counts(it) if v in set(obj) - {t_fid}}
        assert not leak, f"{spec.key}: template ids left in the copies: {sorted(leak)[:20]}"

        # every AI's diplomatic analysis lists every other AI faction (never its owner): the new
        # faction joins each (as a copy of the template's entry); the template's own analysis and
        # the new faction's (cloned from it) each gain the other
        n_info = 0
        for da in self.analyses:
            owner, info = da.children[0].value, da.children[1]
            have = {e[0].value: e for e in info.children}
            want = t_cai if owner == c_id else c_id
            if want in have:
                continue
            e = deep_copy(have.get(t_cai) or info.children[0])
            set_int(e[0], want)
            info.children.append(e)
            n_info += 1

        # the AI interface's (faction, kind) pairs
        v = list(self.cai_pairs.value)
        i = next(j for j in range(0, len(v), 2) if v[j] == t_cai)
        set_list(self.cai_pairs, v + [c_id, v[i + 1]])

        # the simple per-faction arrays
        strs = {spec.template: spec.key}
        for tag, nodes in self.arrays.items():
            for nd in nodes:
                src = next(x for x in nd.children if item_key(x) == spec.template)
                x = deep_copy(src)
                restring(x, strs)
                if tag == "FACTION_INFOS":
                    for p in x:
                        if isinstance(p, ESFPrimitive) and isinstance(p.value, str):
                            if p.value.startswith("start_pos_factions_description_"):
                                set_str(p, f"start_pos_factions_description_{spec.key}")
                            elif p.value.lower().startswith("data\\ui\\flags\\"):
                                set_str(p, f"data\\ui\\flags\\{spec.flag_key}")
                nd.children.append(x)

        for v in list(obj.values()) + list(full.values()):
            self.counts[v] += 1
        print(f"  {spec.key}: faction id {new_fid:#x}, AI {c_id} (template {spec.template} {t_fid:#x} / {t_cai}), "
              f"{len(obj) - 1} object ids and {len(bdi)} AI ids renumbered, {n_glob} global beliefs, "
              f"{len(add)} registered, {n_rel} relationships, "
              f"{n_info} analysis entries; now {len(self.fa.children)} factions")
        return {"fid": new_fid, "cai": c_id}


# ─── DB, text, flags ──────────────────────────────────────────────────────────

def read_pack(pack: Path) -> list[tuple[str, bytes]]:
    b = pack.read_bytes()
    _, _, _, repsz, nfiles, isz = struct.unpack_from("<4sIIIII", b, 0)
    p, off, out = 24 + repsz, 24 + repsz + isz, []
    for _ in range(nfiles):
        sz = struct.unpack_from("<I", b, p)[0]
        e = b.index(b"\0", p + 4)
        out.append((b[p + 4:e].decode("latin1"), b[off:off + sz]))
        p, off = e + 1, off + sz
    return out


def latest(game_data: Path, name: str) -> bytes:
    from reactivate_region import pack_file
    found = None
    for pk in DB_PACKS:
        try:
            found = pack_file(game_data / pk, name)
        except KeyError:
            pass
    if found is None:
        raise KeyError(name)
    return found


def rstr(b: bytes, p: int) -> tuple[str, int]:
    n = struct.unpack_from("<H", b, p)[0]
    return b[p + 2:p + 2 + 2 * n].decode("utf-16-le"), p + 2 + 2 * n


def factions_rows(b: bytes) -> tuple[bytes, dict[str, bytes], set[int]]:
    """Header, rows by key, row ids. A row starts key (UTF-16), u32 row id, subculture 'sc_..'."""
    head = b[:13]
    n = struct.unpack_from("<I", b, 9)[0]
    starts = []
    for i in range(13, len(b) - 8):
        k = struct.unpack_from("<H", b, i)[0]
        if not 2 <= k <= 40:
            continue
        try:
            key = b[i + 2:i + 2 + 2 * k].decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        if not key.replace("_", "").isalnum() or not key.islower():
            continue
        j = i + 2 + 2 * k
        try:
            subc, _ = rstr(b, j + 4)
        except (UnicodeDecodeError, struct.error):
            continue
        if subc.startswith("sc_"):
            starts.append((i, key, struct.unpack_from("<I", b, j)[0]))
    assert len(starts) == n, f"factions table: found {len(starts)} rows, header says {n}"
    rows = {key: b[i:(starts[x + 1][0] if x + 1 < len(starts) else len(b))] for x, (i, key, _) in enumerate(starts)}
    return head, rows, {r for _, _, r in starts}


def faction_row(tpl: bytes, tkey: str, spec: FactionSpec, tpl_display: str, tpl_adj: str, row_id: int) -> bytes:
    r = ws(spec.key) + struct.pack("<I", row_id) + tpl[2 + 2 * len(tkey) + 4:]
    for old, new in ((tpl_display, spec.display), (tpl_adj, spec.adjective),
                     (f"data\\ui\\flags\\{tkey}", f"data\\ui\\flags\\{spec.flag_key}")):
        assert ws(old) in r, f"{old!r} not in the {tkey} row"
        r = r.replace(ws(old), ws(new), 1)
    return r


def junction_rows(b: bytes) -> list[tuple[str, str]]:
    n, p, out = struct.unpack_from("<I", b, 1)[0], 5, []
    for _ in range(n):
        a, p = rstr(b, p)
        c, p = rstr(b, p)
        out.append((a, c))
    assert p == len(b), "technology_faction_junctions is not (string, string) rows"
    return out


def loc_text(loc: bytes, key: str) -> str:
    ver, n = struct.unpack_from("<II", loc, 6)
    p = 14
    for _ in range(n):
        k, p = rstr(loc, p)
        t, p = rstr(loc, p)
        p += 1
        if k == key:
            return t
    raise KeyError(key)


def build_db(specs: list[FactionSpec], game_data: Path, assets: Path,
             region_files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    fac = latest(game_data, "db\\factions_tables\\factions")
    head, rows, row_ids = factions_rows(fac)
    tech = junction_rows(latest(game_data, "db\\technology_faction_junctions_tables\\technology_faction_junctions"))
    files = dict(region_files)
    loc = files.get("text\\localisation.loc")
    if loc is None:
        from reactivate_region import pack_file
        loc = pack_file(game_data / "patch_en.pack", "text\\localisation.loc")

    rng = random.Random(1700)
    new_rows, new_tech, text, flags = [], [], {}, []
    for s in specs:
        t = s.template
        tdisp = loc_text(loc, f"factions_screen_name_{t}")
        tadj = loc_text(loc, f"factions_screen_adjective_{t}")
        rid = rng.randrange(1000, 1 << 32)
        while rid in row_ids:
            rid = rng.randrange(1000, 1 << 32)
        row_ids.add(rid)
        new_rows.append(faction_row(rows[t], t, s, tdisp, tadj, rid))
        new_tech += [(tk, s.key) for tk, f in tech if f == t]
        text[f"factions_screen_name_{s.key}"] = s.display
        text[f"factions_screen_adjective_{s.key}"] = s.adjective
        text[f"start_pos_factions_description_{s.key}"] = s.description
        for kind, phrase in (("attack", "You are attacking a {} force."), ("defend", "You are defending against a force of {} attackers.")):
            text[f"random_localisation_strings_string_{kind}_{s.key}"] = phrase.format(s.adjective)
        if s.flag_key == s.key:
            own = assets / s.key / "ui_flags"
            flags += [(f"ui\\flags\\{s.key}\\{f.name}", f.read_bytes()) for f in sorted(own.glob("*.tga"))]

    # head[:9] = marker fcfdfeff, u32 version, 01; then the row count
    table = head[:9] + struct.pack("<I", len(new_rows)) + b"".join(new_rows)
    assert set(factions_rows(table)[1]) == {s.key for s in specs}, "new factions table does not parse"
    files["db\\factions_tables\\new_factions"] = table
    files["db\\technology_faction_junctions_tables\\new_factions"] = \
        struct.pack("<BI", 1, len(new_tech)) + b"".join(ws(a) + ws(b) for a, b in new_tech)
    files["text\\localisation.loc"] = build_loc(loc, text)
    for n, d in flags:
        files[n] = d
    print(f"DB: {len(new_rows)} factions rows, {len(new_tech)} technology rows, {len(text)} text entries, "
          f"{len(flags)} flag files")
    return list(files.items())


# ─── driver ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mod", type=Path, required=True, help="a region build (startpos.esf + new_regions.pack ...)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--factions", nargs="*", default=[], choices=sorted(FACTIONS))
    ap.add_argument("--dummies", type=int, default=0, help="also add N throwaway factions (faction-cap test)")
    ap.add_argument("--game-data", type=Path, default=Path("../data"))
    ap.add_argument("--assets", type=Path, default=Path("out/faction_assets"))
    a = ap.parse_args()

    specs = [FACTIONS[k] for k in a.factions]
    specs += [FactionSpec(f"dummy_{i:02d}", f"Dummy {i}", "Dummy", "Test faction") for i in range(1, a.dummies + 1)]
    assert specs, "nothing to add"
    for s in specs:
        s.flag_key = s.key if (a.assets / s.key / "ui_flags").is_dir() else s.template

    a.out.mkdir(parents=True, exist_ok=True)
    src = a.mod / "startpos.esf"
    r = ESFReader(src)
    root = r.read_root()
    print(f"read {src}")
    sp = Startpos(root)
    print(f"{len(sp.fa.children)} factions")
    for s in specs:
        sp.add(s)
    data = ESFWriter(root, r.tag_names, r.timestamp).to_bytes()
    (a.out / "startpos.esf").write_bytes(data)
    r2 = ESFReader(a.out / "startpos.esf")
    assert ESFWriter(r2.read_root(), r2.tag_names, r2.timestamp).to_bytes() == data, "startpos round-trip failed"
    print(f"wrote {a.out / 'startpos.esf'} ({len(data):,} B), round-trip OK")

    for f in a.mod.iterdir():
        if f.name not in ("startpos.esf", "new_regions.pack") and f.is_file():
            shutil.copy2(f, a.out / f.name)
    files = build_db(specs, a.game_data, a.assets, read_pack(a.mod / "new_regions.pack"))
    build_pack(a.out / "new_regions.pack", files)
    print(f"wrote {a.out / 'new_regions.pack'}")
    with open(a.out / "MANIFEST.txt", "a", encoding="utf-8") as m:
        m.write(f"factions={' '.join(s.key for s in specs)}\n")


if __name__ == "__main__":
    main()
