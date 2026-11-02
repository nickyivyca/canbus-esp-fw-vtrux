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
- The `.sh` scripts work in a run folder of their own under the tree's
  `scratch/`, `scratch/charge-interposer-harness/<tool>/`, from
  `../scratch_run.sh`, never the system temp folder, removed when the
  script exits (tester, 2026-11-02, the reviewer's item (B)). Until then
  they wrote to `$TMPDIR` or `/tmp`, and `busoff_mutations.sh` and
  `twai_mutations.sh` to one fixed folder each, which two runs at once
  shared. Tested by `../scratch_run_test.py`.

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
| `shared_fake_canfail.py` | Can-fail for host_bridge on the shared TWAI fake (`TWAI_FAKE=shared`, port step 1b's acceptance E): host_bridge on our own fake against copies of the shared fake in `TWAI_FAKE_DIR`, compared with the acceptance adapter's masked-log comparer; the unchanged copy must compare the same, and each of three mutants from the shared fake's own `mutate.py` must compare different. Run as `run.sh --can-fail`, on madhouse (g++). Its test: `../twai_fake_option_test.py` (tester, 2026-11-02, the reviewer's order) |
| `switch_discard_witness.py` | Whether the switch-discard case can PASS |
| `monitor_6h_diff.py` | MONITOR held past 6 h, on `machine.cpp` (spec 6); `--can-fail`, `--corrupt-pin` |
| `clause1_fall_mutations.py` | Can-fail for clause 1's exemptions in `run_scenario._hold_follows_loads` |
| `bypass_diag_mutations.py` | Can-fail for spec 9's declared `--bypass` exemption from the diag check |
| `e5_arming_controls.py` | E5's arming checks (spec 3), shown able to fail |
| `clamp_frame_cases.py` | Every HOLD page-01 frame that owes a clamp in a trace, listed |
| `cutover_verify.py` | Verifies the 2026-10-08 git cutover, file by file (historical). Its test, `cutover_verify_test.py`, is retired (reviewer, 2026-10-31): the full mode copied SeaDrive's `tools/interposer/`, emptied after the move it checked, and its `--guard-test` had nothing left to guard; it is in git history |
