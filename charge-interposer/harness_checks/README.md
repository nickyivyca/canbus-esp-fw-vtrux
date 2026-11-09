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
  `scratch/`, `scratch/cih/<tool>/` (short names since 2026-11-05), from
  `../scratch_run.sh`, never the system temp folder, removed when the
  script exits (tester, 2026-11-02, the reviewer's item (B)). Until then
  they wrote to `$TMPDIR` or `/tmp`, and `busoff_mutations.sh` and
  `twai_mutations.sh` to one fixed folder each, which two runs at once
  shared. Tested by `../scratch_run_test.py`.
- Since port step 2b (tester, 2026-11-08) the scripts that build
  `test_l2_bridge` from a copy (`l2_mutations.sh`,
  `rx_order_prefix_control.sh`, `tx_fail_banking_mutation.sh`,
  `switch_discard_witness.py`) build it as `host_bridge/run.sh` does, by
  its `l2_build.sh`: on the shared TWAI driver fake, with the real
  `twai_port` and `can_port` compiled as C, from `TWAI_FAKE_DIR` or the
  pinned checkout. They mutate only the interposer's own code.
  `twai_mutations.sh` retired with `port_twai.cpp` and `test_twai_port`;
  its mutants' sites moved into `l2_mutations.sh` (D, H, K, L).

| File | What |
|---|---|
| `l2_mutations.sh` | Mutation check for `test_l2_bridge`. Since port step 2b also M (frames lost at the restart after bus-off recovery left out of the RX overflow count, spec 2.4), N (the half-open bridge against `failed-start-vehicle`, now caught by the vehicle port's `tx_refused`), O and P (frames toward the vehicle, and the status frames, sent in listen-only); and Q-X, one mutant per line of spec 2.4's counter mapping in `port_view.h` (`rxDropped`'s three parts, `txDropped`'s two, `busErrors`, `busOff`, `busOffEvents`), each caught by `test_canport_contract`'s vehicle half on its own check (tester, 2026-11-09, the reviewer's ruling) |
| `hold_mutations.sh` | Mutation check for `test_sameid_hold` |
| `busoff_mutations.sh` | Mutation check for `test_mcp2515_busoff` |
| `rx_hook_mutations.sh` | Mutation check for `Mcp2515Fake::deliverWhenFreed()` |
| `conformance_mutations.sh` | Mutation check for the MCP2515 conformance sequence |
| `tx_fail_banking_mutation.sh` | Can-fail for `test_l2_bridge`'s tx-fail case |
| `hold_cadence_gate_mutation.sh` | Can-fail for the two hold-cadence unit traces |
| `rx_order_prefix_control.sh` | Can-fail control for spec 2's receive-order cases |
| `shared_fake_canfail.py` | Can-fail for host_bridge on the shared TWAI fake: host_bridge on copies of the fake's checkout (`twai_port` and `can_port`, `TWAI_FAKE_DIR` naming each copy's fake), compared with the acceptance adapter's masked-log comparer; two unchanged copies must compare the same, and each of three mutants from the fake's own `mutate.py` must compare different from the first. Until port step 2b the reference was our own fake (`TWAI_FAKE=shared`, port step 1b's acceptance E). Run as `run.sh --can-fail`, on madhouse (gcc, g++). Its test: `../twai_fake_option_test.py` (tester, 2026-11-02, the reviewer's order; 2026-11-08 for 2b) |
| `t10_header_check.py` | twai_port T10's fake against ESP-IDF v5.4.2: its `driver/twai.h` declares what ESP-IDF's `driver/twai.h` and `hal/twai_types.h` declare, with the same values, for the S3 and the C3, each side read with that chip's `#if` branches; every remaining difference is a named allowance that must still be needed. Reads the fake at the pin (or `TWAI_FAKE_DIR`) and the install by `IDF_PATH`, refused unless v5.4.2; local, in a shell exported for that IDF. `--can-fail`: nine edited copies (a field, a define, a prototype, an enum item, the other crystal's timing branch, a missing function, BRP_MAX, a new ESP-IDF define, a wrong version) and each rule shown to matter (port step 2b; the reviewer's ruling, 2026-11-08) |
| `switch_discard_witness.py` | Whether the switch-discard case can PASS (since port step 2b reading the vehicle port's pending count from `twai_port_status()`) |
| `monitor_6h_diff.py` | MONITOR held past 6 h, on `machine.cpp` (spec 6); `--can-fail`, `--corrupt-pin` |
| `clause1_fall_mutations.py` | Can-fail for clause 1's exemptions in `run_scenario._hold_follows_loads` |
| `bypass_diag_mutations.py` | Can-fail for spec 9's declared `--bypass` exemption from the diag check |
| `e5_arming_controls.py` | E5's arming checks (spec 3), shown able to fail |
| `clamp_frame_cases.py` | Every HOLD page-01 frame that owes a clamp in a trace, listed |
| `cutover_verify.py` | Verifies the 2026-10-08 git cutover, file by file (historical). Its test, `cutover_verify_test.py`, is retired (reviewer, 2026-10-31): the full mode copied SeaDrive's `tools/interposer/`, emptied after the move it checked, and its `--guard-test` had nothing left to guard; it is in git history |
