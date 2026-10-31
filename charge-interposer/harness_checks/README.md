# harness_checks/

The charge interposer's checks of the checks: mutation scripts, can-fails
and controls that show a test can fail, run by the harness suite
(`../bench_suite.py:harness`). The `.sh` scripts and `monitor_6h_diff.py`
and `switch_discard_witness.py` run on madhouse-debian (bash and g++), from
the code the runner ships there; the rest run locally. Owner: the
charge-interposer tester (harness).

They moved here from reverse-it's
`projects/vtrux/notes/artifacts/interposer-firmware/` folder in port step 0
(reviewer, 2026-10-26). The SeaDrive copies stay there, with a pointer,
until nothing reads them. Since the move:

- Each script tests the tree it is in: `charge-interposer/` is found from
  its own location (`paths.code_dir()` for the Python ones). INTERPOSER_CODE
  is not needed; if it is set to another tree, the script refuses.
- Fixtures still come from SeaDrive: `REVERSE_IT_DIR` for the traces and
  goldens, and `L3_STIMULUS` (else the copy under `REVERSE_IT_DIR`) for the
  pinned L3 stimulus, which until the move was looked for beside the script.

| File | What |
|---|---|
| `l2_mutations.sh` | Mutation check for `test_l2_bridge` |
| `twai_mutations.sh` | Mutation check for `test_twai_port` |
| `hold_mutations.sh` | Mutation check for `test_sameid_hold` |
| `busoff_mutations.sh` | Mutation check for `test_mcp2515_busoff` |
| `rx_hook_mutations.sh` | Mutation check for `Mcp2515Fake::deliverWhenFreed()` |
| `conformance_mutations.sh` | Mutation check for the MCP2515 conformance sequence |
| `tx_fail_banking_mutation.sh` | Can-fail for `test_l2_bridge`'s tx-fail case |
| `hold_cadence_gate_mutation.sh` | Can-fail for the two hold-cadence unit traces |
| `rx_order_prefix_control.sh` | Can-fail control for spec 2's receive-order cases |
| `switch_discard_witness.py` | Whether the switch-discard case can PASS |
| `monitor_6h_diff.py` | MONITOR held past 6 h, on `machine.cpp` (spec 6); `--can-fail`, `--corrupt-pin` |
| `clause1_fall_mutations.py` | Can-fail for clause 1's exemptions in `run_scenario._hold_follows_loads` |
| `bypass_diag_mutations.py` | Can-fail for spec 9's declared `--bypass` exemption from the diag check |
| `e5_arming_controls.py` | E5's arming checks (spec 3), shown able to fail |
| `clamp_frame_cases.py` | Every HOLD page-01 frame that owes a clamp in a trace, listed |
| `cutover_verify.py` | Verifies the 2026-10-08 git cutover, file by file (historical) |
| `cutover_verify_test.py` | Its test: only `--guard-test`, which the suite runs, and which refuses a scratch folder in SeaDrive or in this tree outside its `scratch/`. The full mode is retired (2026-10-31): it copied SeaDrive's `tools/interposer/`, emptied after the 2026-10-08 move it checked, so it has failed since; it refuses (exit 2) |
