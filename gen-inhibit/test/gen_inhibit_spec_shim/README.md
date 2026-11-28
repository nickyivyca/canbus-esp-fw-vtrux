# Spec 12.4 requirement tests on the mock-HAL shim (tester)

The tester's tests of the product on the host, for the spec 12.4 rows that
judge product code (reviewer, 2026-10-20: *a test that judges product code is
not written by the implementor*). They build `main/gen_inhibit.c`,
`gen_inhibit_core.c` and `gi_sched.c` as shipped, and since port step 1b
(2026-10-31) the shared `vtrux/can_port` and `vtrux/twai_port` that replaced
`main/can.c`, from the pinned shared checkout, against the implementor's
harness in `../gen_inhibit_shim/` (`shim_harness.c`, `fake_can.c`,
`fake_port.c`) on twai_port's one host fake (`shared/twai_port/test/`, T10).
Neither the harness nor the fake is **edited**: the tester's fault behaviour
sits on top, through link-time wraps (`-Wl,--wrap=twai_...`) and their own
public controls. Expected values come from the spec and from what the layer
injected and observed, never from the product source.

Gen-inhibit tester, 2026-10-20. First row: **"a bus with no other node,
emulated"**.

## Files

| File | What |
|---|---|
| `spec_layer.h`, `spec_layer.c` | The fault layer. With no other node: every frame never completes (the fake's ACK taken away, `twai_fake_set_acked(false)`, so a normal-mode frame is retransmitted for ever and the head reads as transmitting, as a frame retrying unacknowledged does); one error frame per retry period while a frame waits (`bus_error_count` through the harness's `ft_set_bus_errors`, the fake latching `TWAI_ALERT_BUS_ERROR`); TEC 8 per error to 128 (error passive, `ERR_PASS`, which the layer raises), REC 0, in the fake's counters; on request bus-off (the fake's, TEC 256), ended by the next install. Alerts latch only if enabled when they occur, as the driver's do. Logs every `twai_receive()` return with its virtual time (the device's check points) and every injected error; counts `twai_initiate_recovery()` calls through a wrap, then passes each to the fake. **The retry period and alert pattern are a guess** until the bench row measures them; then the layer replays the measured pattern (spec 12.4). **Ported to twai_port's host fake at port step 1b** (2026-10-31): before, it had its own counter, alert and bus-off model and wrapped `twai_driver_install`, which `shim_harness.c` now wraps; it sees an install by the fake's count. On WSL gcc 9.4 the ported cases gave the 1a2 run's 83 RESULT lines (`runs/step1a2_17ec04c/spec_shim/`) word for word, every figure included. **2026-11-26:** the withdrawn frame stuck in the controller (`sl_retry_stuck`, below "The cases"), through two more wraps, of `twai_ll_get_status` and `twai_ll_set_cmd_abort_tx`; with no frame armed they pass every call through, and every earlier case gave only PASS lines with them on main 54e1ea13. **2026-11-27:** TS between the stuck frame's retries follows the C3's measurement (spec 12.4 row, user): set throughout while error active, clear for 16 us before each retry from TEC 128 (`sl_stuck_passive_idle()` for another value); `sl_stuck_first_delay()` moves the retries against the device's reads (the phase variants), and `sl_retry_started_at()` reads back when they started. Every case without a stuck frame gave only PASS lines on main e9bfa300. |
| `nonode_judge.h`, `nonode_judge.c` | The row's verdicts as pure functions of what a case observed (R1-R7 below, and two checks on the layer). |
| `judge_test.c` | The judge on made-up observations: each verdict shown to pass and to fail. No fakes, no product code. |
| `build_id_test.c` | **Spec 12.4 "0x7F3 carries the build id" (host half) and "the page names the binary"** (port step 0c, diag schema 8; 2026-10-25). Built by `build.sh` with `NN_TEST=build_id_test.c`; one case, `build_id`. The stimulus is the fakes' app descriptor hash (`MOCK_ELF_SHA256`). Spec 10's rule gives B0-B3 = hash[3], hash[2], hash[1], hash[0] (a u32 with the hash's first byte most significant, sent little-endian), worked once more by hand for this descriptor (`fff00f00`). Over 4 s in INHIBIT, every 0x7F3 on the wire must carry those bytes, 8 in B6 (schema 8) and, printed `%08x`, the hash's first four bytes; the page's `elf_sha256` must be the descriptor's hash and start with that build id. 7 RESULT lines. WSL self-check on `ef553fa` merged with the tester's branch: 7 PASS; 6 of 6 mutants of the product's build id and page hash, in a scratch copy, caught (`scratch/step0c/mutate_build_id_test.py`). **2026-11-23 (stage (b) batch 2, schema 9):** B6 must read 9, and a new result, `reserved`, needs B4-B5 0 (spec 10: the retired revision, sent 0); 8 PASS on gen-inhibit/main db1bfeae. |
| `probe_test.c` | **Spec 7 trip 7, "What the device can see of lateness", and spec 11's RESPOND probe on the same path** (both changed 2026-11-23, user; f1961dcf, ab74c3da). Built by `build.sh` with `NN_TEST=probe_test.c` and `host_env.PROBE_BUILD_ARGS` (its judge, and the implementor's `../gen_inhibit_shim/deliver_at_count.c`, not edited, with the three wraps that file names). Ten cases, one per process; see "The trip 7 and probe cases" below. |
| `probe_judge.h`, `probe_judge.c` | `probe_test`'s verdicts as pure functions of three page readings and the wire count. The header states what correct looks like, from the spec only. |
| `probe_judge_test.c` | That judge on made-up observations: each case's correct observation passes every verdict, and each wrong one fails the verdict it should (108 rows). No fakes, no product code. |
| `product_mutants.py` | **The probe cases' can-fail against the product.** It reads the T-series mutants (spec 7 trip 7 and spec 11) from the implementor's `../gen_inhibit_shim/mutate.py` with `ast`, never importing it, applies each to a copy of the parts `build.sh` compiles, in this run's own folder in `scratch/`, and runs every probe case. A mutant is caught by a RESULT line that is not PASS. The unmutated copy runs first and must give only PASS lines. Linux only. |
| `nonode_test.c` | The cases. One case per process (`nonode_test <case>`; no argument lists them): the firmware's statics outlive `ft_reset()`, and trip 6's error history is one of them. Since port step 1b each process sets up port 0 once, before anything arms, as `app_main` does (`gen_inhibit_port_register()`, spec 3.3); so does `build_id_test.c`. |
| `build.sh` | Builds `nonode_test` (`bash build.sh <out>`). `SPEC_LAYER`, `NN_TEST`, `NN_JUDGE` substitute a source (the mutants). The stock headers are vtrux/wican's (port step 1a), and since port step 1b the CAN code is the shared `can_port` and `twai_port` on twai_port's host fake, all from the pinned shared checkout, found by the implementor's `../gen_inhibit_shim/wican_ref.py`, or `WICANDIR` where it is elsewhere (`test_host_env.py` sets it for its copy). Sources, include order and the `twai_driver_install` wrap follow the shim's `Makefile`, with `shim_test.c` replaced by this folder's cases, judge and layer; the layer adds five wraps. (From its first refusal on 2026-10-31 until this port, it refused with exit 2.) **Since stage (b) batch 2** (2026-11-23) it adds the generated diag table, the pinned shared/diag's `diag_pack.c` and `diag_build_id.c` (on the shim's mock `esp_app_desc.h`) and the misfit wrapper over `diag_pack_message()`, from `../diag_host/diag_host.py` (`DIAGDIR` overrides the folder): a misfit no case takes ends the run with exit 3 (spec 12.3 item 2). |
| `mutate.py` | Mutants of the judge (caught by `judge_test`), the layer and the cases (caught by a case's RESULT line; since 2026-11-26 a RESULT line whose verdict differs from the unmutated build's, built and run first as the control and printed with its lines that are not PASS, because the withdrawn-frame case fails by design on a product without its fix; with an all-PASS control that is any line not PASS, as before). Since 2026-11-23 also of `probe_judge.c` (caught by `probe_judge_test`) and `probe_test.c` (caught by a probe case's RESULT line). A crash, a build failure or no RESULT line is not a catch. Each run works in its own folder in the worktree's `scratch/gen-inhibit-tests/mutants-spec_shim/`, each mutant's binary in that mutant's folder (2026-10-24; before, a fixed system temp folder and a fixed `/tmp` binary name, shared by two runs at once). Its second output line is the build's host and compiler; a run with either missing stops there with an error (2026-10-24). |
| `run_spec_shim.py` | The evidence run (since 2026-10-25 also `build_id_test`, into `build_id.txt`, its RESULT lines in the summary; since 2026-11-23 also `probe_judge_test` and `probe_test`, into `probe_judge_test.txt` and `probe.txt`, with `deliver_at_count.c`'s md5 in `identity.json`): the product identity (HEAD, `main/`'s tree against `--base`, nothing modified, each built file's md5; since port step 1b also the shared checkout it built from: the tag the project pins, the folder, its git HEAD where it is a checkout, and each shared file's md5, a FAIL if the checkout or a file is missing or the checkout modified, and a `shared:` line in the summary), the build's host and compiler, the stimulus decoded with the DBCs, `judge_test`, every case. Writes `identity.json`, `payloads.txt`, `judge_test.txt`, `cases.txt`, `summary.txt` into `--out`. `--local` builds in the local bash, for madhouse-debian; without it, in WSL. Since 2026-10-24: `identity.json` has `host`, `compiler` and `build_env`, and the summary a line for them, which says "a self-check only" for a WSL build. A run with either value missing FAILs. The binaries go in the run's own folder in the worktree's `scratch/gen-inhibit-tests/spec_shim_run/` (before, fixed `/tmp` names). |
| `host_env.py` | Since 2026-11-23 also `PROBE_BUILD_ARGS`, `probe_test`'s extra `build.sh` arguments, and `remove()`, `gi_paths.remove_tree`. What both runners share: the build shell (WSL or `--local`), paths as it sees them, the per-run scratch folder, and `host_identity()`. That asks the build shell for `uname -n` and the first line of `gcc --version`, and `describe()` makes the summary line (2026-10-24). |
| `test_host_env.py` | Its test, with `--dbc-dir`. The pure checks run anywhere. The real-shell checks run on Linux only (on Windows a bare `bash` is WSL's): the identity command in a real bash with a fake gcc on PATH and with none, `run_spec_shim.py --local` with each (`identity.json`, the summary line, a FAIL naming the missing compiler), and `mutate.py --local` with no gcc. The pure checks also cover the shared checkout's record (an md5 for each shared file `build.sh` compiles, the host fake among them, none when there is no checkout), and a real-shell run with no checkout must FAIL naming it. `--mutants`: 10 of 10 killed on madhouse-debian (2026-10-31, port step 1b), each in a copy of the repo's layout, and the unmutated copy must pass. **2026-11-26:** the pure checks also cover `mutate.py`'s catch rule against its control (`verdicts()`, `differs()`), and the copy now holds `../diag_host/`: without it the unmutated copy's `run_spec_shim.py` could not record `misfit_wrap.c` and the control failed, from stage (b) batch 2 (2026-11-23) until then. `--mutants` 13 of 13 killed (three more: the control ignored, a line the control lacks never judged, one line kept per requirement), the control passing. |

## The row, as judged

| | Requirement (spec 12.4 row, 7 trips 2 and 6, 7.1) |
|---|---|
| R1 | no `0x051` from the device after its answer to the last VCM command |
| R2 | trips are evaluated once per received frame or per 200 ms receive timeout: the latch lands at a check point (the tick right after a receive return); a latch at any other time is NOT AS SPECIFIED (reviewer, 2026-10-20) |
| R3 | trip 6 if its threshold (10 error frames within 10 s) is met at a check **strictly earlier** than the one that finds `0x051` stale; otherwise trip 2, 0.5-0.7 s after the last `0x051` (a tie is trip 2; reviewer, 2026-10-20) |
| R4 | a stuck diag frame does not block the worker, the status page or the latch |
| R5 | traffic back without a key-on: still latched, the stuck frame completes, diag pages resume, no `0x051` |
| R6 | a key-on then clears the latch |
| R7 | bus-off: no recovery; off the bus through traffic and a key cycle (latched, frames dropped, no reinstall) until a re-arm, which comes more than 10 s after the last error (spec 7: a re-arm goes live only once the bus has been free of a qualifying burst for 10 s) |
| layer | the device's `err_n` moves with the layer's count (reviewer, 2026-10-20: otherwise every case could fall through to trip 2); no error before a frame waits |

## The cases

| case | injected | the 10th error | expected |
|---|---|---|---|
| `fast` | a retry every 300 us | just after the first frame waits | trip 6 at the first check after it |
| `before` | placed | +300 ms after the last `0x051`, before the +400 ms check | trip 6 at that check |
| `tie` | placed | +550 ms, after the check before the stale one | trip 2 at the stale check (the tie) |
| `slow` | placed | +1.5 s | trip 2 at the stale check |
| `busoff_few` | a retry every 1 ms, bus-off after 5 | none | trip 2; R7 |
| `busoff_many` | a retry every 300 us, bus-off after 12 | before bus-off | trip 6; R7 |
| `answer_stuck` | the nodes go before the answer to the last command completes; a retry every 150 ms | +1.5 s | trip 2 at the stale check; A1-A5 below |
| `layer` | the layer's own mechanics on a live device | | |
| `withdrawn_262`, `withdrawn_274` | as `answer_stuck`, at the bench's pattern (246 then 262 us; 258 then 274 us), the answer retried with TS set during each attempt (`sl_retry_stuck`) | about +2.5 ms after the answer is handed over | trip 6 at the first check after it; W1-W4 below |
| `withdrawn_262_15` | as `withdrawn_262`, with a 15 us idle time from TEC 128 (the C3's low end, 15.41 us, in whole us) | as `withdrawn_262` | as `withdrawn_262` |
| `withdrawn_274_q1`, `_q2`, `_q3` | as `withdrawn_274`, with the first retry delayed by a quarter, a half and three quarters of the 274 us period (68, 137 and 205 us, `sl_stuck_first_delay`), so the device's reads land elsewhere in the period (reviewer, 2026-11-27: the fix must remove the frame whatever the phase of its reads) | as `withdrawn_274`, plus the delay | as `withdrawn_274`; one more `layer` line: the first error is the delay plus one active period after the retries start |
| `layer_stuck`, `layer_stuck_15`, `layer_stuck_back` | the layer's stuck-frame model on its own (an abort while error active; TS read every 1 us over 6 ms, because the 262 us period is even and reads every 2 us cannot tell 15 us of TS clear from 16 us; an abort in an attempt and in the idle time; the nodes back mid-attempt); `layer_stuck_15` with the 15 us idle time | | |

**The withdrawn frame stuck in the controller** (spec 12.4 row, added
2026-11-26, user; spec 5.2 item 5, "A withdrawn frame held past the bound";
emulated only, no bench run). The trip lands while the device's inhibit frame
is in the controller and unacknowledged; the fake retries it at the bench
row's measured pattern and reports it transmitting during each retry. **The
retries are the layer's model** (`spec_layer.h`, `sl_retry_stuck`, through
wraps of `twai_ll_get_status` and `twai_ll_set_cmd_abort_tx`): TS set during
each attempt, where an abort does nothing (the fake's abort of a transmitting
frame, twai_port T8). Between attempts TS follows the C3's measurement (spec
12.4 row, added 2026-11-27, user; F5 bench run
`gen-inhibit-bench_20261127T171351_403a`, `VERIFICATION.md` 12.4). While error
active (TEC < 128), TS stays set from one attempt to the next, so an abort
does nothing at any time. From TEC 128 (error passive), TS reads clear for
16 us before each attempt (the C3 measured 15.41-16.44 us;
`sl_stuck_passive_idle()` sets another value, in whole us), and an abort then
removes the frame. The retry period stays the bench row's pattern. Before
2026-11-27 the layer used a model from the CAN protocol: 6 us of TS clear
before each active attempt and 22 us before each passive one.

**The test's step while the frame is held** (2026-11-27). The shim runs the
worker only in the turns the test grants, and a wait of the device's
(`esp_rom_delay_us()`, through the fake's wait hook) lasts until the end of the
turn. At the case's 100 us step, a re-read "every 2 us" came 103 us apart. So
while the stuck frame is held, the withdrawn cases grant 3 us turns (`FINE_US`,
the device's estimated spacing; reviewer, 2026-11-27), and their waits last
what they ask, to within one turn.

**The phase.** The emulation is deterministic, so where the device's reads fall
in the period is fixed by the case's timing. The phase variants move the
retries against the reads. On gen-inhibit/main e9bfa300, which reads once or
twice per pass, the 3 us turns moved its reads too: each pass now starts at
+199.984 ms, +399.988 ms and so on from the latch (at the 100 us step,
+200.026 ms and so on), and some passes read twice, 3 us apart. With that,
the read at +1400.011 ms in `withdrawn_262`, `withdrawn_262_15` and
`withdrawn_274_q2`, and the read at +599.993 ms in `withdrawn_274_q3`, land in
the 16 us idle time, and those cases pass W1-W3 there. At the 100 us step,
`withdrawn_262` failed them on e9bfa300 and `withdrawn_274` passed. With the
3 us turns, `withdrawn_274` and `withdrawn_274_q1` fail them (the can-fail,
reviewer, 2026-11-27), and W5 and W6 fail in all six.

A frame still held when the nodes return completes at the end of the first
attempt that starts after they return. Each withdrawn case prints a `NOTE`
line, recorded and not judged: the status reads while the frame was held, per
pass (the pass's start from the latch, its reads, their span and the widest
gap). Judged, besides R1-R4, R6, A2-A4 and the layer's three checks:

| | |
|---|---|
| W1 | the scheduler aborts it again on later passes until the buffer is free: an abort of the device's removed it before traffic returned (the detail gives the abort commands, how many fell in an attempt, the passes, and the status reads while it was held, with how many read TS clear) |
| W2 | it never completes, traffic back included |
| W3 | no `0x051` reaches the wire when traffic returns |
| W4 | the queued telemetry then goes out: every telemetry frame waiting when traffic returned (`depth + held`) is sent, and that many diag pages reach the wire |
| W5 | (spec 5.2 item 5's last sentence, added 2026-11-27, user; judged from 2026-11-27, reviewer) each pass that starts 1 ms or more after the withdrawal, while the frame is held, re-reads the controller about every 2 us until a read finds TS = 0 or 1 ms has passed. "The 1 ms limit is reached" is a time, 1 ms after the withdrawal (reviewer, 2026-11-27). The reads are the status reads the layer saw while the frame was held; reads more than 1 ms apart are separate passes. A pass fails with two reads more than 8 us apart (the device's 2-3 us, plus one 3 us turn, plus 2 us), or with no TS = 0 read and less than 992 us of reads. The withdrawal's time is the latch as the case's poll saw it. PASS with no such pass |
| W6 | (spec 5.2 item 5, "a trip that lands ... withdraws it": the withdrawal comes on the latch's own pass; reviewer's ruling, 2026-11-27) the withdrawal's first read of the controller comes on the pass on which the latch lands: a status read while the frame is held, from the last poll that saw no latch to 1 ms after the first poll that saw it. VOID with no latch, or with no read and the frame no longer held at the latch |

**The answer-stuck case** (reviewer, 2026-10-20; spec 5.2 items 5 and 6): the
device's answer to the last VCM command is the frame stuck in the controller
when the latch lands, so the trip must withdraw it as a stand-down. How the
controller's abort removes a frame that is retrying is the layer's model
(`spec_layer.h`). Judged, besides R1-R6:

| | |
|---|---|
| A1 | the answer never completes on the wire, traffic back included, and no `0x051` is on the wire after traffic returns |
| A2 | counted as `standdown` +1, `skipped` +0 |
| A3 | `queued + carried_in = sent + dropped + withdrawn + depth + held`, every class, on every page read |
| A4 | `withdrawn + dropped = skipped + standdown` (inhibit) on every read with no inhibit frame held (not mid-withdrawal, which 5.2 item 6 excludes) |
| A5 | the withdrawal ends the wait: a diag page is handed to the driver after the latch, becomes the stuck frame, and completes when traffic returns |

## The trip 7 and probe cases (`probe_test.c`, 2026-11-23)

Spec 7 trip 7: when the worker sees our frame complete, it counts the frames in
the receive queue, after the completion. Queue empty: on time. Frames waiting with
the VCM's next `0x051` among them: late, a skip for an inhibit frame. Frames waiting
without it: on time, with frames waiting. A frame whose completion is seen only after
the next `0x051` was dequeued is late too. Spec 11: the RESPOND probe takes the same
path; a probe found late is in neither on-time count, is never a skip and is never
withdrawn.

Each case sends one VCM command N. Our frame for it (the probe, or the inhibit frame)
completes while the worker waits on an empty queue, so the device has not seen it
complete. Then a filler wakes the worker, with the case's frames behind it:

| case (`respond_`, `inhibit_`) | behind the filler | expected decision for N |
|---|---|---|
| `late` | another frame and N+1 | late: neither on-time count; an inhibit frame is a skip |
| `between` | nothing; N+1 arrives between the completion read and the count (`ft_deliver_at_count()`) | late, as `late` |
| `waiting` | two frames, neither an `0x051` | on time, with frames waiting; max backlog 2 |
| `empty` | nothing | on time |
| `queued` | no filler: N+1 is waiting behind N when the worker takes N | late, as `late` (the completion is seen after N+1 was dequeued) |

Where N+1 came, its frame is booked on time once a last filler lets the device
see it complete with nothing waiting (`next`). Every frame of ours must be on the
wire, the late one too; in RESPOND each probe must be credited in
`cls.probe.sent` and none withdrawn (`sent`). A probe is never a skip; an
inhibit frame found late is one skip, and no other frame is (`skip`). The max
backlog moves only for an on-time-with-frames-waiting booking (`backlog`). A
stimulus that did not land where the case meant is VOID (`setup`), never a
pass. The verdicts are `probe_judge.c`'s.

A case whose check points or injected errors did not land where it meant
(a check within 20 ms of the 0.5 s freshness edge, a frame received in the
silence, the 10th error outside the case's region) is VOID, never a pass.

## Running

**Only a run built on madhouse-debian with its gcc 14.2 counts for
VERIFICATION.md.** A WSL build on NICKY-XPS (gcc 9.4) is a self-check only
(user, 2026-10-24, through the reviewer). On madhouse-debian, in a clone at the
commit under test, from the clone's root (before port step 0d, without `gen-inhibit/`):

```
python3 -B gen-inhibit/test/gen_inhibit_spec_shim/run_spec_shim.py --local --out <run folder> --dbc-dir <reverse-it>/projects/vtrux
python3 -B gen-inhibit/test/gen_inhibit_spec_shim/mutate.py --local
python3 -B gen-inhibit/test/gen_inhibit_spec_shim/product_mutants.py
python3 -B gen-inhibit/test/gen_inhibit_spec_shim/test_host_env.py --dbc-dir <reverse-it>/projects/vtrux [--mutants]
```

From Windows, as a self-check (WSL's gcc):

```
py -3.14 -B gen-inhibit/test/gen_inhibit_spec_shim/run_spec_shim.py --out <run folder> --dbc-dir <reverse-it>/projects/vtrux
py -3.14 -B gen-inhibit/test/gen_inhibit_spec_shim/mutate.py
```

Run files go to the gen-inhibit harness folder in reverse-it
(`projects/vtrux/notes/artifacts/gen-inhibit/runs/`).
