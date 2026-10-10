# Legacy

Scripts and tests from the March 2026 experiments (South France / Occitania by hand,
the slot-74 repurpose, CAI fix-ups, phase-0 recon) that the current build no longer uses.
Kept for reference; `docs/reverse_eng/deep_dive.md` still describes them under their old
`scripts/` paths. They are not maintained and may not run from here (their `sys.path`
setup assumes `scripts/`).

The current build chain is `split_region.py` / `add_region.py` / `clear_zones.py` ->
`reactivate_region.py` -> `unlock_factions.py` -> `paint_supertexture.py`, deployed
with `deploy_mod.py`; see deep_dive 10.23 for the order.
