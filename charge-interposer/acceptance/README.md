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
| `2b-rerun.toml` | Port step 2b's re-run after run b467: the no-LED mutant build, its checkouts' self-test can-fail, the three mutation checks and the harness suite (c-harness against 6976), on the harness fixes at `305cfda0`, the same product, in a record that names b467 (ACCEPT-SPEC A13; user, 2026-11-11) |
| `3-emulated.toml` | Port step 3's Linux baseline of the emulated and harness suites on madhouse-debian, product as f57ca9d3, against b467 (emulated) and 08e8 (harness) (user, 2026-11-11) |
| `3-emulated-rerun.toml` | Port step 3's emulated baseline re-run after run 563d: both suites whole on the harness with its three Linux fixes at `2d14b2e5`, the same product and baselines, in a record that names 563d (ACCEPT-SPEC A13; reviewer, 2026-11-12) |
