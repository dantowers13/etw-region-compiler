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
import unicodedata
import sys
from dataclasses import dataclass, field
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
    # M1, a living faction: regions handed over and a government cloned from a living donor
    donor: str | None = None       # living faction whose government / court is cloned (georgia)
    regions: tuple = ()            # regions handed over; the first is the capital
    protector: str | None = None   # starts as this faction's protectorate (as Crimea / Ottomans)
    people: dict = field(default_factory=dict)       # post -> (first, surname, born)
    candidates: list = field(default_factory=list)   # unposted court, in order: (first, surname, born)


# Ministers and court as of 1700 (birth years approximate where unknown). ETW has no heir or
# family tree in the startpos: Brancoveanu's sons are court candidates with their real ages.
FACTIONS = {
    "wallachia": FactionSpec(
        "wallachia", "Wallachia", "Wallachian", "Principality of Wallachia",
        donor="georgia", regions=("wallachia",), protector="ottomans",
        people={
            "faction_leader": ("Constantin", "Brâncoveanu", 1654),
            "head_of_government": ("Constantin", "Cantacuzino", 1639),
            "finance": ("Ianache", "Văcărescu", 1654),
            "army": ("Toma", "Cantacuzino", 1665),
            "justice": ("Mihai", "Cantacuzino", 1640),
            "navy": ("Radu", "Greceanu", 1655),
            "accident": ("Cornea", "Brăiloiu", 1650),
            "governor_europe": ("Pârvu", "Cantacuzino", 1667),
        },
        candidates=[("Constantin", "Brâncoveanu", 1683), ("Ștefan", "Brâncoveanu", 1685),
                    ("Radu", "Brâncoveanu", 1690), ("Matei", "Brâncoveanu", 1698),
                    ("Dumitrașcu", "Corbea", 1665)],
    ),
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


def ascii_key(s: str) -> str:
    """'Brâncoveanu' -> 'Brancoveanu', for localisation keys (the text keeps the diacritics)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if c.isalnum() and ord(c) < 128)


def fac_fields(F) -> dict[str, int]:
    """Positions of FACTION fields found by their landmarks (unlock_factions inserts a
    CAMPAIGN_VICTORY_CONDITIONS record into selectable factions, which shifts the rest):
    the governor-post list follows MORGUE, the two capital-region ids follow the second
    FORT_UPGRADE_MANAGER, the alive/emergent flag follows PRESTIGE."""
    tags = [c.tag if isinstance(c, ESFNode) else None for c in F.children]
    fu = [i for i, t in enumerate(tags) if t == "FORT_UPGRADE_MANAGER"][-1]
    return {"govposts": tags.index("MORGUE") + 1, "cap1": fu + 1, "cap2": fu + 2,
            "flag": tags.index("PRESTIGE") + 1}


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
        self.text = {}             # localisation the startpos now names (character names)
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
        restring(F, {spec.template: spec.key})     # CAMPAIGN_PLAYER_SETUP names the faction too
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

    # ── M1: a living faction ─────────────────────────────────────────────────

    def make_living(self, spec: FactionSpec, fid: int, c_id: int) -> None:
        """Hand the spec's regions to the new faction and give it a government and court cloned
        from a living donor (georgia: an absolute monarchy with 7 ministers, a governor and 5
        unposted candidates). The donor's general and agents belong with its army (M1b)."""
        D_item, D, d_fid = self.faction(spec.donor)
        N_item, N, _ = self.faction(spec.key)
        regs = {}
        for it in find_fast(self.root, "REGIONS_ARRAY").children:
            R = sub(it, "REGION")
            regs[R.children[0].value] = R
        cwr = {sub(it, "CAI_REGION").children[10].value: it for it in find_fast(self.root, "CAI_WORLD_REGIONS").children}
        cap = regs[spec.regions[0]]
        dfx, nfx = fac_fields(D), fac_fields(N)
        cap_rid, d_cap_rid = cap.children[4].value, D.children[dfx["cap1"]].value

        # the donor's court: ministers only (king, office holders, candidates)
        d_gov, d_fam, d_chars = (sub(D.children, t) for t in ("GOVERNMENT", "FAMILY", "CHARACTER_ARRAY"))
        def ctype(c):
            return next(p.value for p in sub(c, "CHARACTER").children if isinstance(p, ESFPrimitive) and isinstance(p.value, str))
        keep = [c for c in d_chars.children if ctype(c) == "minister"]
        posts = [p[0] for p in d_gov.children[3].children]
        post_type = {p.children[0].value: p.children[1].value for p in posts}
        gship = next(sub(p.children, "GOVERNORSHIP") for p in posts if sub(p.children, "GOVERNORSHIP") is not None)
        donor_ids = ([sub(c, "CHARACTER").children[2].value for c in d_chars.children]
                     + list(post_type) + [d_gov.children[0].value, gship.children[1].value])
        idmap = {v: self.ids.obj() for v in donor_ids}
        idmap.update({d_fid: fid, d_cap_rid: cap_rid})
        new_gpost = idmap[gship.children[1].value]

        gov, fam = deep_copy(d_gov), deep_copy(d_fam)
        chars = deep_copy(d_chars)
        chars.children = [deep_copy(c) for c in keep]
        for x in (gov, fam, chars):
            remap(x, idmap)
            restring(x, {spec.donor: spec.key})
        for i, c in enumerate(N.children):
            if isinstance(c, ESFNode) and c.tag in ("GOVERNMENT", "FAMILY", "CHARACTER_ARRAY"):
                N.children[i] = {"GOVERNMENT": gov, "FAMILY": fam, "CHARACTER_ARRAY": chars}[c.tag]
        set_list(N.children[nfx["govposts"]], [new_gpost])
        set_int(N.children[nfx["cap1"]], cap_rid)
        set_int(N.children[nfx["cap2"]], cap_rid)
        N.children[nfx["flag"]] = deep_copy(D.children[dfx["flag"]])

        # names and birth years
        new_posts = {p[0].children[2].value: p[0].children[1].value for p in gov.children[3].children}
        cands = list(spec.candidates)
        for c in chars.children:
            ch = sub(c, "CHARACTER")
            ptype = new_posts.get(ch.children[2].value)
            who = spec.people.get(ptype) if ptype else (cands.pop(0) if cands else None)
            if who is None:
                continue
            first, surname, born = who
            det = sub(ch.children, "CHARACTER_DETAILS")
            locs = [x for x in det.children if isinstance(x, ESFNode) and x.tag == "CAMPAIGN_LOCALISATION"]
            set_str(locs[0].children[0], self.name_key(spec.key, first))
            set_str(locs[1].children[0], self.name_key(spec.key, surname) if surname else "")
            set_str(det.children[4], "")
            set_int(next(x for x in det.children if isinstance(x, ESFNode) and x.tag == "DATE").children[0], born)
        king = spec.people["faction_leader"][0]
        mon = fam.children[0]
        rkey = f"names_royalty_name_{spec.key}{ascii_key(king)}"
        set_str(mon.children[0].children[0], rkey)
        self.text[rkey] = king
        set_int(mon.children[6], 1)

        # the governorship governs the handed-over regions
        g = next(sub(p[0].children, "GOVERNORSHIP") for p in gov.children[3].children
                 if sub(p[0].children, "GOVERNORSHIP") is not None)
        set_list(g.children[2], [regs[r].children[4].value for r in spec.regions])

        # AI: characters, governorship, faction lists
        cwc = find_fast(self.root, "CAI_WORLD_CHARACTERS")
        cwg = find_fast(self.root, "CAI_WORLD_GOVERNORSHIPS")
        d_cwf = next(it for it in self.cwf.children
                     if sub(it, "CAI_FACTION") is not None and sub(it, "CAI_FACTION").children[6].value == d_fid)
        n_cwf = next(it for it in self.cwf.children
                     if sub(it, "CAI_FACTION") is not None and sub(it, "CAI_FACTION").children[6].value == fid)
        d_cai = d_cwf[2].value
        d_cap_cai = cwr[next(k for k, R in regs.items() if R.children[4].value == d_cap_rid)][2].value
        cap_cai = cwr[spec.regions[0]][2].value
        kept_ids = {sub(c, "CHARACTER").children[2].value for c in keep}
        d_cchars = [it for it in cwc.children if sub(it, "CAI_CHARACTER") is not None
                    and sub(it, "CAI_CHARACTER").children[3].value in kept_ids]
        d_cgov = next(it for it in cwg.children if sub(it, "CAI_GOVERNORSHIP").children[0].value == gship.children[1].value)
        aimap = {it[2].value: self.ids.cai() for it in d_cchars}
        aimap[d_cgov[1].value] = self.ids.cai()
        aimap[d_cai] = c_id
        full = idmap | aimap
        for it in d_cchars:
            x = deep_copy(it)
            remap(x, full)
            clear_bdi(x)
            cwc.children.append(x)
        xg = deep_copy(d_cgov)
        remap(xg, full)
        clear_bdi(xg)
        set_list(sub(xg, "CAI_GOVERNORSHIP").children[3], [cwr[r][2].value for r in spec.regions])
        cwg.children.append(xg)
        gov_ai = xg[1].value
        n_cf, d_cf = sub(n_cwf, "CAI_FACTION"), sub(d_cwf, "CAI_FACTION")
        set_list(n_cf.children[4], [aimap[v] for v in d_cf.children[4].value if v in aimap])
        for i, c in enumerate(d_cf.children):          # the donor's AI capital field(s)
            if isinstance(c, ESFPrimitive) and not isinstance(c.value, bool) and c.value == d_cap_cai:
                set_int(n_cf.children[i], cap_cai)

        # hand over the regions (as reactivate_region.transfer_region)
        for r in spec.regions:
            R = regs[r]
            old_fid, old_gov, rid = R.children[19].value, R.children[21].value, R.children[4].value
            old_key = self.key_of(old_fid)
            old_cwf = next(it for it in self.cwf.children
                           if sub(it, "CAI_FACTION") is not None and sub(it, "CAI_FACTION").children[6].value == old_fid)
            old_cai = old_cwf[2].value
            OF = self.faction(old_key)[1]
            ofx = fac_fields(OF)
            assert rid not in (OF.children[ofx["cap1"]].value, OF.children[ofx["cap2"]].value), f"{r} is {old_key}'s capital"
            for gr in find_all_fast(R, {"GARRISON_RESIDENCE"})["GARRISON_RESIDENCE"]:
                if gr.children[0].value == old_fid:
                    set_int(gr.children[0], fid)
            for f in R.children[35].children:
                if f[0].value == old_fid:
                    set_int(f[0], fid)
            restring(R, {old_key: spec.key})                 # buildings name their owner
            set_int(R.children[19], fid)
            set_int(R.children[21], new_gpost)
            for gg in find_all_fast(self.root, {"GOVERNORSHIP"})["GOVERNORSHIP"]:
                if gg.children[1].value == old_gov:
                    set_list(gg.children[2], [v for v in gg.children[2].value if v != rid])
            cai_rid = cwr[r][2].value
            set_int(sub(cwr[r], "CAI_REGION").children[12], gov_ai)
            for it in cwg.children:
                cg = sub(it, "CAI_GOVERNORSHIP")
                if cg.children[0].value == old_gov:
                    set_list(cg.children[3], [v for v in cg.children[3].value if v != cai_rid])
            ocf = sub(old_cwf, "CAI_FACTION")
            set_list(ocf.children[0], [v for v in ocf.children[0].value if v != cai_rid])
            set_list(n_cf.children[0], list(n_cf.children[0].value) + [cai_rid])
            sett_id = R.children[5].children[4].value
            for it in find_fast(self.root, "CAI_WORLD_SETTLEMENTS").children:
                cs = sub(it, "CAI_SETTLEMENT")
                if cs.children[2].value == sett_id:
                    set_int(sub(it, "OWNED_DIRECT").children[0], c_id)
                    set_int(cs.children[1], c_id if r == spec.regions[0] else 0)   # capital marker
            print(f"    {r}: {old_key} -> {spec.key}")

        # protectorate: copy an existing protector/protectorate pair (Crimea and the Ottomans)
        if spec.protector:
            P_item, P, p_fid = self.faction(spec.protector)
            def rel(F, other):
                return next(x for x in find_fast(F, "DIPLOMACY_RELATIONSHIPS_ARRAY").children
                            if sub(x, "DIPLOMACY_RELATIONSHIP").children[0].value == other)
            vassal = next(x for x in find_fast(P, "DIPLOMACY_RELATIONSHIPS_ARRAY").children
                          if sub(x, "DIPLOMACY_RELATIONSHIP").children[4].value == "patron")
            v_fid = sub(vassal, "DIPLOMACY_RELATIONSHIP").children[0].value
            back = rel(self.faction(self.key_of(v_fid))[1], p_fid)
            for F, other, src in ((P, fid, vassal), (N, p_fid, back)):
                dra = find_fast(F, "DIPLOMACY_RELATIONSHIPS_ARRAY")
                i = dra.children.index(rel(F, other))
                x = deep_copy(src)
                set_int(sub(x, "DIPLOMACY_RELATIONSHIP").children[0], other)
                dra.children[i] = x
            print(f"    protectorate of {spec.protector} (as {self.key_of(v_fid)})")

        # nothing of the donor's court may be left in the copies
        left = set(donor_ids) | set(aimap) - {d_cai}
        leak = {v for x in (gov, fam, chars, xg) for v in int_counts(x) if v in left}
        assert not leak, f"{spec.key}: donor ids left in the copies: {sorted(leak)[:10]}"
        print(f"    court of {len(keep)} from {spec.donor}, governorship {new_gpost:#x} (AI {gov_ai}), "
              f"{len(d_cchars)} AI characters")

    def key_of(self, fid: int) -> str:
        for it in self.fa.children:
            F = sub(it, "FACTION")
            k = faction_key_index(F)
            if F.children[k - 1].value == fid:
                return F.children[k].value
        raise KeyError(fid)

    def name_key(self, faction: str, name: str) -> str:
        key = f"names_name_names_{faction}{ascii_key(name)}"
        self.text[key] = name
        return key


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
             region_files: list[tuple[str, bytes]], extra_text: dict) -> list[tuple[str, bytes]]:
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
    text.update(extra_text)
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
        new = sp.add(s)
        if s.donor:
            sp.make_living(s, new["fid"], new["cai"])
    data = ESFWriter(root, r.tag_names, r.timestamp).to_bytes()
    (a.out / "startpos.esf").write_bytes(data)
    r2 = ESFReader(a.out / "startpos.esf")
    assert ESFWriter(r2.read_root(), r2.tag_names, r2.timestamp).to_bytes() == data, "startpos round-trip failed"
    print(f"wrote {a.out / 'startpos.esf'} ({len(data):,} B), round-trip OK")

    for f in a.mod.iterdir():
        if f.name not in ("startpos.esf", "new_regions.pack") and f.is_file():
            shutil.copy2(f, a.out / f.name)
    files = build_db(specs, a.game_data, a.assets, read_pack(a.mod / "new_regions.pack"), sp.text)
    build_pack(a.out / "new_regions.pack", files)
    print(f"wrote {a.out / 'new_regions.pack'}")
    with open(a.out / "MANIFEST.txt", "a", encoding="utf-8") as m:
        m.write(f"factions={' '.join(s.key for s in specs)}\n")


if __name__ == "__main__":
    main()
