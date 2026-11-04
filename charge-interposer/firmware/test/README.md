# firmware/test/

The firmware's own C++ unit tests, `host_unit/`. Everything else that was
here, the tester's harness (`host_bridge/`, `host_diff/`, `c12_bench/`,
`vport_bench/`, `hw_mechanics.py`), moved to `../../fw_harness/` at port step
2a (reviewer, 2026-11-03, Q3), and the PlatformIO build checks
(`build_check*.py`, `digest_recheck_check.py`) retired with the PlatformIO
build. The tester's harness files and their checks are described in
`../../fw_harness/README.md`.

## `host_unit/`

```
wsl bash ".../charge-interposer/firmware/test/host_unit/run.sh"
```

Builds and runs every `test_*.cpp` in the folder with plain `g++ -std=c++11`,
and needs no environment set (unlike `host_bridge/run.sh`, which wants
`REVERSE_IT_DIR`). The binaries go in a run folder of its own under the
tree's `scratch/` (`../../scratch_run.sh`, tool `host-unit`), never the
system temp folder, removed when it exits (2026-11-02). Both rules below were kept out of `main.cpp` so they could be
tested here; the rest of `main.cpp` needs the fake ports of spec 9.1 L2,
which live in `../../fw_harness/host_bridge/` rather than in this folder.

| test | covers |
|---|---|
| `test_bridge_health.cpp` | spec 8.2's `bridge_ok`: health **since the previous status frame**, not since boot. Both halves, because each is a different defect -- a failure inside one interval must clear the flag for **that frame**, and a clean interval afterwards must set it again. Shown failing against both: ignoring the vehicle-side input (the pre-2026-10-04 behaviour) fails 2 checks, and never advancing the baselines (the "since boot" defect of review item B-3) fails 7. 20 checks |
| `test_charger_tx_failures.cpp` | spec 2.2's `0x7F6` B7, "charger not acknowledging while expected": the SUM of the charger port's three disjoint tallies -- queue full, controller failure, and the 100 ms age-out that discards a frame at the head without ever loading it (user, 2026-10-13) -- accrued only while the VCU's flow bit is 1, with a delta that arrives outside a session DISCARDED rather than banked for the next one. 18 checks: one per tally, the three together in one pass, both sides of the gate, the mid-session boot of 2.2's last sentence, and the unsigned subtraction that makes a counter wrap harmless |

(Check counts measured by the tester, 2026-10-13, at 9c42964: 20 and 18,
all OK.)

**`test_charger_tx_failures.cpp` is not a smaller copy of `test_l2_bridge`'s
`tx-fail` arm.** That arm drives the same field end to end through
`main.cpp` and is much the stronger test of the wiring -- the frames in it
fail by aging out, so it reaches the 2026-10-13 clause too. What it cannot
discriminate is the shape of the number: its flow-1 assertion is
`gated > 0`, which an implementation that banks failures from outside the
session and adds them to the next one also satisfies, reporting a bigger
number rather than a wrong-looking one. The unit case "and they are NOT
banked: the session starts at 0 even though the tallies stand at 1,000" is
an equality. *The implementor reports (2026-10-13, not re-run by the
tester) that six mutations of the header were run against these cases and
none survived, the baseline-only-when-counting mutation failing exactly
that case first.*
