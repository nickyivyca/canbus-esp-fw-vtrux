# Acceptance lists

**Owner:** the charge-interposer reviewer (shared/bench ACCEPT-SPEC A2).

One TOML list per port step, `<step>.toml`. The acceptance runner reads a
list from git at a named commit (`<commit>:charge-interposer/acceptance/<step>.toml`),
never from a working tree, and records the commit and the list's sha256.
The format is in shared/bench's `vtrux_bench/accept/listfile.py`, at the
shared/bench pin of the tree the runner runs from.

A list says what a step's acceptance runs. It is not evidence: the run's
record and the tester's `VERIFICATION.md` entries are. The runner's smoke
lists (`../accept_smoke.toml`, `../accept_build_smoke.toml`) are test
fixtures, not acceptance lists, and stay outside this folder.

| File | Step |
|---|---|
| `2a.toml` | Port step 2a: items (a)-(j) of the review tracker's 2a row, with the check forms ruled 2026-11-04 |
| `2a-rerun.toml` | Port step 2a's re-run after run 0d0d: the items its harness fixes touch, on the same product, in a record that names 0d0d (ACCEPT-SPEC A13; reviewer, 2026-11-07) |
| `2b.toml` | Port step 2b: the vehicle port on `vtrux/twai_port` and `vtrux/can_port`, the charger port behind `can_port`; every build, the whole suites and bench, compared against 6976 and 0d0d (user, 2026-11-09) |
