# Generator inhibitor (WiCAN) -- test results and evidence

**Status:** the user approved the evidence move on 2026-10-05 and asked for
superseded results to be cleared out; this is the cleared-out version. The
spec text that had been holding test results was moved out of the spec the
same day.

## What this document is

- **The spec states requirements; this document holds the results.**
  `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-spec.md` is written as if
  every test of it passes (user, 2026-10-04: *"we shouldn't be keeping notes
  of when tests passed in the spec ... evidence can and should live in a
  testing results and evidence doc, but not in the spec"*).
- **Organised by spec section,** so the evidence for a requirement sits under
  the section that states it.
- **Each entry names** what was tested, on which build, the result, and the
  run files. Run files are in `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/runs/` unless stated
  otherwise.
- **The X2's state is a condition of every bench entry** (reviewer,
  2026-10-13): its image md5, held in ROM, or the jammer. Frame counts on CAN1
  leave the X2's IDs out by DBC, and a run that needs a quiet bus holds the X2
  in ROM: `$REVERSE_IT_DIR/firmware/bench-holder.md` rule 6. Why: with no node on CAN1
  ACKing, `7f197bfd` repeats `0x7F4` at ~3,700 frames/s (measured
  2026-10-13, `runs/bench_census_20261013T232011`; who ACKs is in that
  file's X2 section). Entries before 2026-10-13 that ran with no ACKing
  adapter carry a dated note saying so.
- **Current results only** (user, 2026-10-05). When a newer run supersedes an
  entry, the entry is replaced, not added to. History lives in three places:
  the run files; the working log
  `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-wican-firmware.md`, which
  narrates how each result was obtained; and the review tracker
  `$REVERSE_IT_DIR/projects/vtrux/notes/gen-inhibit-review-2026-09-22.md`,
  which holds the reviewer's verification of each result. The tracker also
  keeps a verbatim archive of the spec text that was moved out on
  2026-10-05.
- **A record, not a status board.** An entry describes the build it was
  measured on. Under spec 13, a build that is code-identical to a tested one
  carries that build's results. Any other change means re-running the test.
- **The unit.** Every hardware result here is from one WiCAN OBD (ESP32-C3)
  with base MAC recorded in `$REVERSE_IT_DIR/firmware/bench-holder.md` (bench identifiers are never in git), which serves as its serial number.
  Over WiFi it identifies itself by SSID, recorded there too, and by
  `device_id` on `/check_status`; both use the SoftAP MAC, which is the base
  MAC + 1. It has been the only WiCAN on the bench since the bench was set up
  in its current configuration (user, 2026-10-05), and its MAC is on record
  from the 2026-09-19 bench-state table in the implementation plan. Earlier
  testing on a WiCAN Pro is not evidence here. A result from any other unit
  must name that unit.
- **Implementor self-test versus tester evidence** (user, 2026-10-07). Every hardware result produced by the earlier implementing session, while it was testing its own work, is marked *Implementor self-test, not tester evidence* where it appears. Those entries close nothing. Each is replaced, not appended to, when the tester produces its own result for that section. Entries without the mark are the tester's, or are host/emulation results verified by the reviewer.
- **Harnesses and run files** are described in the harness README,
  `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/README.md`.

---

## 3.2 No other transmitter

**SLCAN over TCP and the websocket is refused and counted, in OFF and while
armed -- tester, 2026-10-12 17:05, on `dace947` `_man`. Result: AS
SPECIFIED, 20 of 20 commands, each counted exactly once.** Spec 3.2,
including the clause changed 2026-10-12 (user): "A refused attempt is
counted, never silently dropped: the status page carries `refused_slcan` and
`refused_can_send`, both since boot". Listed in spec 13 as "bench, no
harness".
- **Image:** `dace947` `_man`, as in the 12.4 entry "No stale answers on going
  live, bench half".
- **Channels** (reviewer's ruling):
  - the TCP port, because the device's protocol is `slcan`. Read from
    `/load_config`: `protocol` `slcan`, `port_type` `tcp`, `port` 3333; only
    those three fields were kept, since the config carries credentials;
  - the web UI websocket `/ws`, whatever the protocol;
  - UART needs USB and is parked.
- **Commands, on each channel:** O (open), Y (loopback), S6 (bitrate), `t`
  (one frame on `0x6A6`, an id nothing on the bench sends), C (close).
- **Conditions:**
  - OFF (mode 0);
  - armed (mode 3), live on the interlock slice that the PCAN loops (as in
    `skip_method_bench.py`);
  - Kvaser witness, X2 app running, 12 V only.
- **Harness:** `slcan_refusal_probe.py`, tested by
  `test_slcan_refusal_probe.py` (22 checks, 7 mutants all caught;
  `runs/test_slcan_refusal_probe_20261012.txt`). Around each command it reads
  the status page before and 0.6 s after, the channel's reply, and the ids the
  witness saw in between.
- **A command fails if any of these happens:**
  - a bus-state field on the page changes (mode, `bus_on`, `drv` state,
    `inhibit_live`, `self_off`, `disabled`, `arm_block`, `abort_reason`);
  - `0x6A6` reaches the wire;
  - in OFF, a WiCAN frame appears (`0x7F0`-`0x7F3`, `0x7F8`, `0x7F9`);
  - while armed, the device stops answering (`tx_ok` does not rise);
  - the counters do not move by exactly `refused_slcan` +1 and
    `refused_can_send` +0. A decrease fails as "went down". Reviewer's
    reading (2026-10-12): every refused SLCAN command counts once in
    `refused_slcan`, `t` and OFF included, and none counts in
    `refused_can_send`, which only `can_send()` callers book.

| Condition | Channel | O | Y | S6 | t (`0x6A6`) | C |
|---|---|---|---|---|---|---|
| OFF | TCP 3333 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 |
| OFF | `/ws` | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 |
| armed | TCP 3333 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 |
| armed | `/ws` | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 | PASS, +1/+0 |

(+1/+0: `refused_slcan` +1, `refused_can_send` +0.)
- **Bus:** in OFF, `drv` stayed null through every command, O included.
  Armed, nothing on the page changed, C included. `0x6A6` was never on the
  wire. Neither channel replied to any of the 20 commands, and no other page
  key appeared.
- **Gap: `refused_can_send` was only seen at 0.** Moving it needs a client
  that calls `can_send()` while armed. The device's protocol is `slcan`, and
  changing it means `store_config`, which rewrites the whole config,
  credentials included. The tester declined that. So the counter is shown not
  to count SLCAN refusals; that it counts its own refusals is not shown.
- **Run file:** `runs/slcan_refusal_20261012T170526.json`. It replaces the
  `cc34c80` run of 2026-10-10 (`runs/slcan_refusal_20261010T191450.json`),
  which passed on the bus and found that nothing observable recorded a
  refusal. That finding led to the spec change above.

## 4.1 B0 held at the engine-off state

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**Bench, 2026-09-20** (`wican_crank_request_bench.py`; build not recorded).
The bench drove B0 through `0x08` / `0x0B` / `0x10` / `0x08` at 100 Hz:

- **Active generator request (`0x0B`):** the device transmitted 600 frames,
  every one carrying B0 `0x08`, with zero torque, the counter stolen and
  `ctr_bad` 0.
- **`0x10`:** it transmitted nothing, and resumed afterwards without
  latching. `0x10` never appeared in a transmitted frame.

## 5 Transmit strategy and timing

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**The IRAM decision:** across three runs, the flash build fell inside the IRAM
build's own spread. No build or run file is recorded for this.

### Full-bus replay, current firmware (bench, 2026-09-27 to 09-29)

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

The reviewer recounted every arm from the raw witness log. A frame counts as
"ours" if it trails the frame in front, reads that frame's counter +1, and is
not a bunched VCM command; a late answer would read +0.

| INHIBIT | full-bus replay, WiFi idle | same, WiFi flood (outside the design case) | 300 s long arm |
|---|---|---|---|
| firmware | `976eadb` | `976eadb` | `422293a` |
| stimulus | `truck_passive2.log`, 30 s, ~2377 frames/s | the same + 1165 status requests in 30 s | `scottsvalley_armable_300s.log`, 300 s, ~2454 frames/s, key on, contactors closed, engine off |
| answers on the wire = `tx_ok` | 670 = 670 | 670 = 670 | 29977 = 29977 |
| late / doubled at the wire | 0 / 0 | 0 / 0 | 0 / 0 |
| trail at the wire, p50 / p95 / p99 / max | 420 / 430 / 510 / 520 us | 430 / 1111 / 1530 / 1790 us | 420 / 431 / 510 / 1030 us |
| device RX -> observed completion, mean / max | 544 / 708 us | 704 / 2475 us | 572 / 1508 us |
| inhibit longest queue / longest hold | 15 / 692 us | 682 / 2212 us | 100 / 1481 us |
| on time, verified / unverified (largest backlog) | 670 / 0 | 582 / 88 (5) | 29962 / 15 (2) |
| skipped / abort-loop bound reached | 0 / 0 | 0 / 0 | 0 / 0 |
| `ctr_bad` / driver `rx_missed` | 0 / 0 | 1 / 15 | 0 / 0 |
| error frames / `tx_queued_behind` | 0 / 0 | 0 / 0 | 0 / 0 |

| RESPOND (`976eadb`, same 30 s replay) | WiFi idle | WiFi flood (1035 requests in 30 s) |
|---|---|---|
| probes on the wire / sent | 2999 / 2999 | 2994 / 2999: 5 dropped, counted |
| device RX -> queue, max | 1523 us | 3246 us |
| at the wire (Kvaser), p50 / p95 / p99 / max | 940 / 1360 / 1960 / 3500 us | 1020 / 1800 / 2300 / 3500 us |

Run files: `e4_fullbus_{inhibit,respond}_976eadb` and `e4_long_arm_422293a`,
each a `.json` with its witness log. The recounts are in
`saturation_recount*.out`.

- **The scheduler adds no measurable latency at the wire.** The
  pre-scheduler build `5ceedf2`, on the same 300 s replay (`e4_long_arm2`),
  trails at 420 / 431 / 510 / 1030 us, the same as `422293a`. It latched
  trip 7 at ~21,560 frames on a late frame; the scheduler build ran the full
  300 s with none late.
- **Every non-zero figure is in the WiFi flood, which is outside the design
  case.** All 670 answers were on time at the wire, but the device could not
  verify 88 of them, because its receive backlog reached 5. The driver
  missed 15 frames, one of them a VCM command (`ctr_bad` 1).
- **The charge interposer's bench image was live on the segment** for all
  three columns above and for the RESPOND pair
  (`witness_hidden_load_census.json`). Its cost on these arms measured as
  negligible:
  - the board's only traffic was its own four diag pages, at exactly
    1.00/s each. In 1208 frames none was delayed more than 4.5 ms, where its
    ~225 errors/s would need ~7 ms of retransmission per frame. The same
    measure does detect delay: it shows delays to ~109 ms on a saturated
    arm.
  - Every ID on the wire is accounted for
    (`witness_foreign_id_census.json`).
  - Receive-side errors from an error-passive node cost no wire time.

  A re-run with the interposer's presence controlled is required (spec 12).

**Interposer presence controlled -- tester, 2026-10-10, on `cc34c80`
`_man`.** The re-run this section required.
- **Arms:** each arm was run twice, back to back, changing only the X2's
  state:
  - "held": in ROM download mode, so not running, not ACKing, not
    bridging;
  - "app": running `7f197bfd`.
- **The arms:**
  - the INHIBIT full-bus replay (`truck_passive2.log`, 30 s);
  - the RESPOND one;
  - the 300 s long arm (`scottsvalley_armable_300s.log`).
- **Rig:** `e4_device_calibration.py`, WiFi idle, Kvaser witness, PCAN
  replay. Supply 12 V only, USB unplugged.
- **Presence, read off the wire:** the X2's `0x7F4`-`0x7F7` pages appear 0
  times in every held arm, and about 1/s each in every app arm.
- **Run files:** `runs/e4_presence_{inhibit,respond,longarm}_cc34c80_{x2held,x2app}_20261010*.json`,
  each with its witness log. The RESPOND held arm is `..._x2held_20261010b`;
  the first, `..._x2held_20261010`, was voided by a harness defect (below).

| | INHIBIT 30 s, held / app | RESPOND 30 s, held / app | long arm 300 s, held / app |
|---|---|---|---|
| answers on the wire = `tx_ok` | 670 = 670 / 670 = 670 | 2996 = 2996 / 2998 = 2998 probes | 29977 = 29977 / 29977 = 29977 |
| late at the wire / error frames | 0 / 0, both | 0 / 0, both | 0 / 0, both |
| `ctr_bad` / `tx_fail` | 0 / 0, both | 1 / 2 held; 1 / 0 app | 0 / 0, both |
| device response max | 4,789 / 1,003 us | 2,887 / 3,805 us | 25,185 / 1,498 us |
| inhibit `max_hold_us` | -- | -- | 25,170 / 1,480 |
| skipped / on-time unverified | -- | -- | 0 / 2 held; 0 / 6 app |
| probe latency at the wire, p50 / p99 / max | -- | 940 / 2130 / 6140 held; 940 / 2140 / 4090 app | -- |

- **On the wire, presence changes nothing measured.** Every answer is on
  time in all six arms, with 0 error frames, and the answer counts equal the
  2026-09 figures for the same stimuli (670; 29,977).
- **Reported, not interpreted: one long held-arm reading of `max_hold_us`
  25,170 us** (the device's longest inhibit hold).
  - In the same arm the device counted 0 skips and 0 late.
  - The witness shows all 29,977 answers trailing their command by about
    0.4 ms.
  - The app arm read 1,480 us, close to `422293a`'s 1,508 us.
  - One arm per condition cannot attribute it to the X2's absence.
  - Whether a 25 ms hold with 0 skips is consistent with spec 5.2 item 5 is
    for the reviewer.
- **The held long arm's "3 double answers / 3 misattributed VCM" are
  classifier artefacts.**
  - The trail window is min(3 ms, `max_hold_us` + `max_queue_us` +
    300 us), so the 25 ms hold set it to its 3 ms cap.
  - At that width, three VCM commands that the replay sent only 2.39-2.9 ms
    after our answer were taken for ours.
  - The raw sequence (`presence_arm_doubles.py`) is VCM *k*, ours *k*+1,
    VCM *k*+1 throughout.
  - The counts leave no room for an extra answer: 59,969 `0x051` = 29,992
    VCM + 29,977 `tx_ok`.
  - The app arm's window was 1,925 us and flagged none.
- **`ontime`, `ontime_unverified` and `unverified_max_backlog` are not
  measurements in RESPOND.** They are booked only for inhibit-class frames
  (implementor, from the source, checked by the reviewer), so they read 0 in
  every RESPOND arm because that code never runs, not because there was no
  backlog.
- **`ctr_bad` 1 in both RESPOND arms, held and app,** so it does not follow
  presence. The cause is in the data: a `0x051` dropped from the driver's
  software receive queue.
  - Those two arms read `drv.rx_missed` 13 (app) and 16 (held `b`), with
    `rx_overrun` 0 and `ctr_ok` 2,996 of 2,998.
  - The voided first held RESPOND arm read 0.
  - Every INHIBIT and long arm read 0.
  - The 2026-09 `976eadb` RESPOND arms read 0, idle and under WiFi load.
  - The only earlier non-zero is that build's INHIBIT arm under WiFi load (15).
  - `ctr_bad` counts one step per run of consecutive drops, so it undercounts.
    `rx_missed` is the measure.
  - Every arm here used `offset` 500. The offset sweep is in a separate entry.
- **What the device's timing fields measure** (implementor, from the source,
  relayed by the reviewer 2026-10-10): `max_hold_us` and `rx_gap` are
  stamped when the worker credits or dequeues a frame, not when it is on the
  wire.
  - The held long arm's 25,170 us hold sits in a stimulus gap (`rx_gap` max
    167 ms), not a stall.
  - The cause is with the implementor as a code question.
- **Both INHIBIT arms latched "engine turning while armed -- the inhibit did
  not hold". That is the replay's own engine start, and expected.** The
  window is a recording, so it cannot respond to our inhibit. Read off the
  witness:
  - the VCM's B0 turns to 0x0B (generator requested) at +5.68 s;
  - the device keeps answering with B0 held at 0x08 for 1.23 s more (124
    answers);
  - its last answer is at +6.91 s, and then it latches.
  - Held and app arms agree to the millisecond, and the 2026-09 `976eadb`
    idle and loaded arms show the same: 124 after the request, last at
    +6.909 s, the same latch.
- **Harness defect, found and fixed:** the stimulus check subtracted
  `tx_ok` from the `0x051` count in RESPOND too, where `tx_ok` counts
  `0x7F0` probes. It voided a good arm ("2993 of 2999 commands never reached
  the wire" while the witness had all 2,999). This is the same mode mix-up
  the attribution split fixed on 2026-09-28. It is now
  `vcm_commands_on_wire()`, tested by `test_e4_stimulus_split.py` (4 checks;
  the 2026-09-29 form is a caught mutant).
- **How the X2 was taken off the bus** (reviewer-approved 2026-10-10):
  `x2_hold.py`, through COM3.
  - It was held in ROM download mode 16:58-17:13, with no flash write, so
    custody of its image is untouched. The only writes were the input-enable
    bits of GPIO7/GPIO6, in volatile IO_MUX registers in ROM mode, cleared
    by the reset at release.
  - Before the hold it read `7f197bfd` on the wire (16:59), and again after
    the release (17:13:46): `--require-witness`, env `esp32-can-x2-witness`,
    fw 4 (`runs/x2_identify_{before_hold_20261010T1659,after_hold_20261010T1714}.txt`).
  - **TX pin while held:**
    - GPIO_ENABLE bit 7 was 0, so GPIO7 (CAN1 TXD) was not driven, and the
      IO_MUX showed no pulls.
    - The ROM leaves input-enable clear on GPIO6/7, so GPIO_IN read 0
      meaninglessly at first (`runs/x2_hold_hold_20261010T165839.json`).
      - **Correction (tester, 2026-11-07): that file is in the baseline
        folder now,** `runs/baseline_c65c45e/bench/x2/x2_hold_hold_20261010T165839.json`
        (sha256 `0f0f10c6...f472`), not in `runs/`. Found when the hash
        manifest could not resolve the citation.
    - With input-enable set (one esptool session), GPIO_IN `0x3c0000cd` put
      GPIO7 = 1 and GPIO6 = 1: TXD idles recessive and the transceiver sees
      a recessive bus (`runs/x2_hold_enable-inputs_20261010T170115.json`).
  - `test_x2_hold.py`: 13 checks, 5 mutants caught.

**RESPOND receive loss: offset, WiFi load and polling -- tester, 2026-10-10,
on `cc34c80` `_man`. Result: drops do not follow the probe offset, the WiFi
load or light polling. The implementor's pinned prediction is falsified.**
- **Question (reviewer):** RESPOND arms showed `drv.rx_missed` 13-16 per 30 s,
  where INHIBIT and the 2026-09 `976eadb` RESPOND arms showed 0.
- **Implementor's prediction, pinned before the run:** the probe's busy-wait
  at worker priority is the cause, so:
  - offset 100: 0 or low single digits;
  - offset 500: about 10-20 per 30 s;
  - offset 1000: clearly worse;
  - falsified if roughly flat, or if 100 still drops.
  The implementor withdrew it before the result, since `976eadb` had the same
  busy-wait and dropped nothing.
- **Rig:** `respond_offset_sweep.py` driving `e4_device_calibration.py`.
  - The RESPOND window is `truck_passive2.log` 20-50 s at 2,364.6 frames/s;
    INHIBIT uses 0-30 s.
  - PCAN replay, Kvaser witness, X2 running its app, 12 V only.
  - Idle arms make no HTTP request during the replay: the mode POST before,
    one status read after the wire is quiet.
  - "loaded" is e4's status-page flood (about 1,100 requests in 30 s).
  - "poll 1 Hz" is the new `--poll-hz 1` (31 requests), and records
    `drv.msgs_to_rx` from each answer.
  - Judged on `rx_missed`, never `ctr_bad`.
- **Comparability:** every arm read back its offset and replayed at
  2,354-2,365 frames/s. Burstiness: the back-to-back fraction (gap < 300 us)
  was 0.72-0.77 in every arm below (bound 0.79, chosen from today's clean
  arms).

| Plan, time | Arms (offset, condition) -> `rx_missed` |
|---|---|
| offsets, 17:44-17:49 | 100: 0, 16, 0; 500: 31, 16, 0; 1000: 0, 0, 0 |
| WiFi pair (re-run), 17:59-18:04 | RESPOND 500 idle: 0, 0; loaded: 0, 1. INHIBIT 500 idle: 0, 0; loaded: 0, 0 |
| poll, 18:04-18:08 | RESPOND 500 unpolled: 15, 0, 0; polled 1 Hz: 0, 0, 0 |

- **The prediction is falsified on both of its own tests.** Offset 100
  dropped (16 in one arm of three), and 1000 dropped nothing in three. Across
  the 12 unpolled RESPOND idle arms of the day at offset 500 (presence 3,
  offsets 3, WiFi re-run 2, poll 3, fidelity check 1), the drops were
  intermittent: 31, 16, 16, 15, 13 and seven arms of 0.
- **The drops come in lumps of about 16:** 13, 15, 16, 16, 16 and 31 (about
  2 x 16). Each arm had at most one `ctr_bad` step. This is observed, not
  interpreted.
- **WiFi load:**
  - With the flood: 0 and 1, against 0 and 0 idle.
  - INHIBIT, idle or flooded: 0 in all four. INHIBIT read 0 in every arm of the
    day (twelve).
  - Light polling: 0, 0, 0, against 15, 0, 0 unpolled; three arms each cannot
    separate those.
- **Queue depth (the polled and flooded arms):** `msgs_to_rx` read 0 on all
  but 3 of about 4,600 samples.
  - The one drop observed with samples (RESPOND flooded, `rx_missed` 1) came in
    the first 44 ms of the arm. The first sample, taken as the replay started,
    read 54 of 64 queued; every later sample read 0.
  - That is one event. Whether the RESPOND drops are a start-of-arm transient
    is a hypothesis, not established.
  - **Located afterwards, by position** (`respond_drop_location.py`,
    `runs/respond_drop_location_20261010.txt`; reviewer's method). Every
    RESPOND arm replays the same window, so a command's index is comparable
    across all 21 RESPOND idle arms of the day (offsets 9, poll 6, WiFi re-run 2,
    presence 3, fidelity check 1):
    - **In all six arms with receive misses, the first two replayed commands**
      (index 0 and 1, within the first 9 ms) went unanswered. Only 1 of the 15
      arms without misses shows both, and 2 more show index 0 alone.
    - **Each arm with misses queued 1 fewer probe than commands replayed**
      (2,998 of 2,999), and the 31-drop arm 2 fewer. Every arm without misses
      queued 2,999. So each lost 1-2 commands, and with the index-0/1 pattern,
      at the start.
    - **The replay is not burstier at the start:** the back-to-back fraction in
      the first 100 ms was 0.68-0.76, against 0.74-0.77 for the rest of each
      arm.
    - **So the drops cluster at the start of the arm, and the start of the
      replay is not where the load peaks.** That is consistent with a device-side
      transient at mode entry, and with the flooded arm whose first sample read
      54 of 64 queued. It is not proven.
    - The 31-drop arm is the only one with unanswered commands unique to it
      later on (2.44-2.48 s and 25.78-25.84 s). The 16-drop held arm has three
      unique ones at 28.61 s.
    - **What this cannot do:** separate receive misses from probe drops by
      count. The rule (no probe before the next command) also catches probes
      that were merely late, so arms with no misses show more unanswered
      commands than dropped probes, by up to 10.
- **Still open (reviewer):** the 2026-09 `976eadb` RESPOND arms dropped 0 at the
  same offset with the same zero polling. That difference by build is not
  explained by anything here.
- **The first WiFi-pair attempt (17:49-17:55) is NOT COMPARABLE and is kept
  only as a rig record.**
  - The replay came out bunched: 0.81-0.91 back to back, with unbroken runs of
    up to 199 frames, against 0.72-0.77 and runs of 33-120 in every other
    arm.
  - Its mean rate was normal, so the comparability check of the time (mean
    rate only) passed it.
  - In its four RESPOND arms the device's probe class dropped 351, 965, 1,034
    and 1,062 probes (`tx_fail` the same), with `arb_lost` 15,047-39,231
    (sweep arms: 1,026-3,754). `rx_missed` was 0 throughout, and INHIBIT was
    unaffected (670/670).
  - A fidelity check at 17:56 was back to 0.752.
  - The cause was not established. Host Python work (harness tests) was
    running on the same laptop during those arms; since then nothing else runs
    on it during a replay, and burstiness is a comparability criterion.
- **Run files:**
  - `runs/respond_offset_sweep_{offsets_20261010T174402,wifi_20261010T175941,poll_20261010T180439}.json`;
  - the per-arm `e4_sweep_*` files with their witness logs;
  - the spoiled attempt: `respond_offset_sweep_wifi_20261010T174940.json`;
  - the fidelity check: `e4_fidelity_check_respond_20261010T1756.json`.
- **Harness:**
  - `respond_offset_sweep.py`, tested by `test_respond_offset_sweep.py` (15
    checks, 6 mutants);
  - `e4_device_calibration.py --offset-us`, `--poll-hz`, and the queue series,
    tested by `test_e4_stimulus_split.py` (9 checks, 5 mutants).

**Which 0x051 is ours: re-judged by provenance -- tester, 2026-10-10. Result:
every arm of the day is as specified; five arms lose their "double answers".**
- **Why** (reviewer, 2026-10-10): e4's classifier took its time bound from
  the device (`max_hold_us` + `max_queue_us` + 300 us, capped at 3 ms), and
  `max_hold_us` is stamped when the worker credits a frame, not when it is on
  the wire. On this capture our answer to command *k* is byte-identical to the
  VCM's *k*+1, so only the bound separated them.
- **Harness:** `provenance_classifier.py`, tested by
  `test_provenance_classifier.py` (10 checks, 7 mutants).
- **Method: queue order.**
  - The replay's frames leave the PCAN in queue order, so they appear on the
    wire in capture order.
  - A walk with a pointer into the replayed sequence assigns each wire frame
    to the replay or to another node. A `0x051` can be the replay's *k*+1 only
    once every frame queued before *k*+1 has been seen.
  - It is ambiguous only where two identical frames arrive with no replayed
    frame between them; that makes the arm UNRESOLVED, and every such case is
    listed.
  - **Count invariant:** every replayed frame must be found in order, and the
    device's `0x051` must equal `tx_ok` (0 in RESPOND). A mismatch makes the
    arm UNRESOLVED.
- **Timing was tried first and refused, correctly.**
  - Scheduled replay times were fitted to the witness clock on the non-`0x051`
    ids.
  - The residuals ran p99 0.24-34 ms, max 3-154 ms: a frame waits behind
    whatever the adapter has queued.
  - So the bound covered whole command periods, and all 11 INHIBIT arms came
    out UNRESOLVED (`runs/provenance_rejudge_timebased_20261010.txt`).
  - The fit and its residuals are still reported in each output. The e4 runs
    record no PCAN TX timestamp; queue order needs none.
- **Rejudge of all 38 arms** (the seven presence arms and every `e4_sweep_*`
  arm, including the not-comparable bunched ones). Each writes
  `runs/<run>_<arm>_provenance.json`; the original run files are unchanged.
  Summary: `runs/provenance_rejudge_20261010.txt`.
  - Every replayed frame found in order in all 38 arms (706,283 in each long
    arm).
  - The device's `0x051` equal its `tx_ok` in every INHIBIT arm, and 0 in
    every RESPOND arm.
  - 0 ambiguous, 0 late, 0 double, 0 unexplained: as specified in all 38.
- **Verdicts that change against e4's classifier** (frames in
  `runs/provenance_changed_frames_20261010.txt`):
  - the X2-held long arm, 3 "double answers" -> 0;
  - the four INHIBIT arms of the bunched WiFi attempt, 1, 3, 2 and 1 -> 0.
    Each of these had its trail window at the 3 ms cap.
  - The frames file re-runs the old rule exactly (`provenance_changed_frames.py`;
    it reproduces the old counts) and marks each double.
  - Every "double" is one swap of roles. The frame booked as our second answer
    is the replay's own command, arriving 1.36-2.65 ms after our answer to the
    previous command, with every frame the replay queued between them seen
    first. The frame right after it, booked as VCM, is the device's answer to
    that command (counter +1, about 0.43 ms later).
  - No arm changes in the other direction.
- **Limit:** this relies on the adapter sending in queue order and the witness
  missing nothing. A lost or reordered replayed frame breaks the walk and shows
  as "replayed frames not found", never as a quiet verdict. That is tested.

**Mode entry into live traffic -- tester, 2026-10-10 18:24-18:29, on `cc34c80`
`_man`. Result: INHIBIT entered into a running full-bus replay loses nothing and
answers every command once live; RESPOND drops frames in both entry orders.**
- **Question (reviewer):** every earlier arm POSTed the mode on a quiet bus and
  then started the replay. On the truck the device enters INHIBIT with the bus
  already at about 2,400 frames/s. The RESPOND receive losses clustered at the
  start of the arm.
- **Rig:**
  - `e4_device_calibration.py --arm-after 5` (new). The replay runs from
    t = 0, and the mode is POSTed 5 s in, from a thread.
  - Capture: `scottsvalley_armable_300s.log` (B0 0x08 throughout), 35 s,
    about 2,358 frames/s. INHIBIT uses capture 0-35 s and RESPOND 20-55 s; e4
    fixes RESPOND's window start. Same rate, both armable.
  - Offset 500, unpolled apart from the arming POST, X2 app running.
  - Interleaved INHIBIT / RESPOND / RESPOND / INHIBIT / INHIBIT / RESPOND.
  - All six replays kept their fidelity: back-to-back 0.75-0.79 in the
    200 ms after the POST, against 0.77-0.78 for the rest.
- **The POST on the wire** (`mode_entry_analysis.py`):
  - the run records the replay index at the POST;
  - the replay leaves the adapter in queue order, so provenance gives that
    frame's wire time;
  - "unanswered" means no answer before the next command (INHIBIT: a device
    `0x051` by provenance; RESPOND: a `0x7F0`, which also counts late probes);
  - the POST itself took 20-39 ms (HTTP).

| Arm | `rx_missed` / `ctr_bad` | First answer after the POST | Unanswered before it / after it | Class queued, `tx_ok` |
|---|---|---|---|---|
| INHIBIT 1 | 0 / 0 | +150.9 ms (going live) | 15 / 0 | 2,985, 2,985 |
| INHIBIT 4 | 0 / 0 | +150.9 ms | 15 / 0 | 2,985, 2,985 |
| INHIBIT 5 | 0 / 0 | +150.9 ms | 15 / 0 | 2,985, 2,985 |
| RESPOND 2 | 5 / 0 | +66.0 ms | 5 / 15 | 2,996, 2,984 (`tx_fail` 12) |
| RESPOND 3 | 2 / 0 | +46.5 ms | 4 / 6 | 2,997, 2,993 (`tx_fail` 4) |
| RESPOND 6 | 0 / 0 | +27.6 ms | 2 / 9 | 2,997, 2,991 (`tx_fail` 6) |

- **INHIBIT, the truck's condition:**
  - No receive loss in any of three arms (`rx_missed` 0, `ctr_bad` 0).
  - Live 150.9 ms after the POST in all three. That is the 15 commands before
    the first answer, at 10 ms each; it matches to the 0.1 ms.
  - From then on every command was answered: 2,985 = queued = `tx_ok`, with
    provenance as specified (0 late, 0 double, every replayed frame found).
  - This is the manual-arm-into-traffic figure, not the boot-to-live one that
    spec 13 asks for.
- **RESPOND:**
  - `rx_missed` 5, 2, 0 (7 in all), against 13, 16, 0 (29) in the three
    presence RESPOND arms, which entered on a quiet bus. Those used a
    different capture window (`truck_passive2.log` 20-50 s), at the same rate.
    So RESPOND drops frames in both entry orders; INHIBIT dropped none.
  - **Whether a command was lost is not established.** `ctr_bad` read 0 in all
    three, but at 7 dropped frames with `0x051` about 4 % of frames, the
    expected number of commands hit is about 0.3 for the three arms together.
    So 0 is the likely reading either way (implementor's arithmetic, checked
    by the reviewer).
  - The probe queued count cannot settle it. The device's moment of entry is
    not visible to one command: one arm queued 2,996 probes against 2,994
    commands from its first probe on.
  - The first probe came 27.6-66 ms after the POST.
  - After that, the unanswered commands come in clusters mid-arm (10.9-23.8 s
    after the POST). They track the probe drops (`tx_fail` 12, 4, 6), plus
    probes that were merely late.
  - Where the 5 and 2 lost frames fall cannot be seen: they are not commands.
- **What it says about the start transient:**
  - INHIBIT entered into steady traffic lost no frame in three arms.
  - RESPOND dropped fewer frames entering into traffic (7 across three arms)
    than at quiet-bus entry (29 across the three presence arms), but it
    dropped in both orders.
  - Three arms per mode on different windows, so a direction only. The
    reviewer is recommending to the user that the RESPOND transient be parked
    as a known RESPOND-only characteristic.
- **Run files:**
  - `runs/e4_mode_entry_{inhibit,respond}_20261010T182419_arm{1..6}.json`,
    each with its witness log and live provenance;
  - the analysis: `runs/mode_entry_analysis_20261010T182419.jsonl`.
- **Harness:**
  - `e4 --arm-after` (`ModePoster`) and `mode_entry_analysis.py`, tested by
    `test_mode_entry_analysis.py` (6 checks, 2 mutants);
  - provenance now also returns each replayed frame's wire time.

### Receive backlog, measured directly (bench, 2026-09-26)

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

A measurement-only build (`GI_INSTRUMENT_RXQ`, never flashed to the truck)
read the driver's receive backlog at every dequeue (`msgs_to_rx` + 1). It ran
at the shipping depth of 64, on the `truck_passive2.log` replay at ~2356
frames/s (~424 us per queued frame), with 82,604 dequeues per arm.

| arm | max backlog | ~stall | dequeues with backlog >= 2 / >= 4 / >= 8 |
|---|---|---|---|
| WiFi idle (design case), run 1 | 4 | ~1.7 ms | 63 / 1 / 0 |
| WiFi idle (design case), run 2 | 3 | ~1.3 ms | 19 / 0 / 0 |
| WiFi flood (533 requests in 35 s) | 9 | ~3.8 ms | 3654 / 467 / 2 |

- **The design-case worst is ~1.7 ms,** inside the 2.39 ms design figure
  (`WORST_PREEMPT_US`).
- **The depth of 64** is ~16x the design-case worst and ~7x the flood's.
  The E4 sweep puts the first loss at depth 64 at 30 ms, which is 12.6x
  2.39 ms; 5.1 item 5 requires at least 3x.
- **The instrument perturbs the device:** `tx_queued_behind` was 1 in both
  instrument runs and 0 in every shipping-build run.

### Case 3, sustained saturation (5.2 item 10)

Accepted by the user (2026-10-02 and 2026-10-03). Closed on the flashed
firmware `4a0a796` (user, 2026-10-04).

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**Bench half: two arms on `4a0a796`** (`e4_case3_4a0a796.json` and `_b`, each
with its witness log). 90 s per arm at 92.5 % carried load, with the filler
on a separate adapter. Neither arm is VOID.

- **Commands:** 8998 of 8998 on the wire.
- **Inhibit:** 8983 queued = 8983 sent; 0 dropped, withdrawn or aborted.
- **On time:** `ontime_unverified` read 4 and 1, and the wire shows 0 late.
- **Telemetry:** 363 queued, 11 dropped and counted, 1 held. Arm `_b` also
  carried one frame in from the previous arm, so it reads 352 sent and
  balances at 364.
- **Zero:** skipped, late, doubled and error frames.

The reviewer's recount of both witness logs agrees, and every class
balances. The 0x7F3 page names the build on the wire (fnv1a32("4a0a796") =
0x4F28F92D).

**Emulation half:** `case_saturation_sustained` and
`case_saturation_skip_is_counted` in `test/gen_inhibit_sched/sched_test.c`.
Both assert the case's three clauses, and the second drives the skip path.
They pass in the sched suite at `1017bbf`, verified by the reviewer.

**Limits:**
- 92.5 % is a lower bound on carried load, because stuff bits cannot be
  recovered from a logged frame.
- The load is filler, not truck traffic at this density, so it is a stress
  condition (spec 12.2).
- The emulation's contention is a calibrated dial (`fb_set_contended`),
  traced to the 5.2 item 9 measurement; it is not a bus simulation.
- No bench arm has reached the skip path (`skipped` is 0 in every recorded
  bench reading).
- `4a0a796` added the `!aborting` guard in `gs_command_received` (5.2 item
  6). The bench arms never open that guard's window: every abort resolved
  through gs_tick's step 3, and the abort bound was never reached. The
  guard's evidence is the emulation case
  `case_departure_during_abort_is_not_sent`, plus a mutation that removes
  the guard, which the suite catches.
- `build/wican-fw_obd_4a0a796.bin` on disk is a rebuild, not the image that
  was flashed.

## 5.1 The fix

**Item 4:** withdrawing the 1 s diag fallback changed none of the 74 host
goldens and none of 200 randomised sequences. The build is not recorded.

## 5.2 Transmit scheduler

### Skipped inhibit frames and trip 7, spec 12.4 method (tester, 2026-10-07, `aaab89a` `_man`)

Spec 12.4 row "skipped inhibit frames" (5.2 item 5, section 7 trip 7), bench
method of 2026-10-04. Harness `skip_method_bench.py`; classifier and judge
tested by `test_skip_method_judge.py` (29/29). Run file
`runs/skip_method_20261007T234718.json` (dry run `..._234708`).

**Rig.** PCAN replays the interlock slice of `vtrux_20260719_190019_T4.log`
and makes each event: a marker frame (`0x6A5`, event number in B0-B3), VCM
N, the stimulus's next `0x051` (N+1) queued straight behind it, then in the
filler arms 9 x `0x050` (~2 ms). Kvaser is an independent witness (transmits
nothing). Each event is classified from the wire: after the marker, N, then
the VCM's N+1 (back to back: 0.17-0.26 ms in every event), then any further
`0x051` with N+1's counter is the device's answer to N, i.e. late. 0 such
frames = withdrawn. In every filler event the 9 filler frames sat between N+1
and the next `0x051`. Witness error frames 0; no event displaced.

| Arm | Events | Witness (never on wire / late) | Device `skips_withdrawn` / `skips_late` delta | Abort latched | Trip 7 expected | Judge (ruled) |
|---|---|---|---|---|---|---|
| w3, filler | 0 / 0.4 / 0.8 s | 3 / 0 | 0 / 3 | "3 inhibit frames skipped within 1 s" | yes | **PASS** (12.4 original: FAIL) |
| w2, filler | 0 / 0.4 s | 2 / 0 | 0 / 2 | none | no | **PASS** (12.4 original: FAIL) |
| w3s, filler | 0 / 0.55 / 1.1 s | 3 / 0 | 0 / 3 | none | no | **PASS** (12.4 original: FAIL) |
| l3, no filler | 0 / 0.4 / 0.8 s | 0 / 3 | 0 / 3 | "3 inhibit frames skipped within 1 s" | yes | **PASS** |
| l2, no filler | 0 / 0.4 s | 0 / 2 | 0 / 2 | none | no | **PASS** |
| l3s, no filler | 0 / 0.55 / 1.1 s | 0 / 3 | 0 / 3 | none | no | **PASS** |

- **Trip 7 latched in exactly the two arms where it should**, both with three
  events inside 1 s, and in none of the four others (two events; or a third at
  1.1 s). No other abort latched in any arm.
- **Late frames:** every one the witness saw, the device counted (8 of 8). No
  frame went out late undetected.
- **How the filler arms are judged** (reviewer ruling, 2026-10-07; **pass
  pending the user's approval of the corrected 12.4 wording**). In all 8
  filler events the witness saw the device's answer to N never reach the
  wire, and the device booked all 8 as **late**. Spec 7 (trip 7) and 5.2 item
  6 govern the split: a frame still in the controller at the deadline "may
  have reached the wire late" and is booked late, because the device cannot
  tell a late completion from a successful abort; the device's "withdrawn"
  kind is a frame purged from the queue before it reached the controller. The
  12.4 row's demand that the device's split agree with the witness's cannot
  hold under spec 7, so the arms are judged by: (1) device total skips =
  witness never-on-wire + witness late; (2) device late >= witness late;
  (3) device withdrawn <= witness never-on-wire; (4) trip 7 exactly where 3
  skips fall within 1 s. All six arms pass. The run file keeps the original
  12.4 judge output (`judge_12_4_original` in future runs; `judge` in this
  one, FAIL for the filler arms) and carries a dated `rejudged` block.
- **Coverage:** this method reaches only the late kind. A queue-purged
  withdrawal needs the controller busy with another frame when the answer is
  queued; that kind is covered by the host suite only, not the bench.
- Not yet checked: the skip counts on the `0x7F9` diag page (the witness did
  not keep its frames in this run).

**Re-run on `cc34c80` `_man` (tester, 2026-10-10 19:08-19:10). Result: all six
arms PASS under the ruled totals rule.** The build changed since `aaab89a`
(trip 6, page fields), so the 10-07 result did not carry over.
- **Harness:** `skip_method_bench.py` unchanged in method: the same interlock
  slice, the same filler (`0x050` x 9), marker `0x6A5`.
- **Two additions, both tested:**
  - the witness's error frames counted per event (marker to next marker),
    `errors_per_event()`;
  - the raw `0x7F9` frames kept per arm, decoded with the DBC.
  `test_skip_method_judge.py`: 33/33 (29 + 4 new), and two new mutants
  caught (`runs/test_skip_method_judge_20261010.txt`).
- **Run file:** `runs/skip_method_20261010T190908.json`; dry run
  `..._190857`.

| Arm | Events | Witness (never on wire / late) | Device withdrawn / late (page) | `0x7F9` last (withdrawn / late) | Abort latched | Witness error frames per event |
|---|---|---|---|---|---|---|
| w3, filler | 0 / 0.4 / 0.8 s | 3 / 0 | 0 / 3 | 0 / 3 | "3 inhibit frames skipped within 1 s" | 0, 0, 0 |
| w2, filler | 0 / 0.4 s | 2 / 0 | 0 / 2 | 0 / 2 | none | 0, 0 |
| w3s, filler | 0 / 0.55 / 1.1 s | 3 / 0 | 0 / 3 | 0 / 3 | none | 0, 0, 0 |
| l3, no filler | 0 / 0.4 / 0.8 s | 0 / 3 | 0 / 3 | 0 / 3 | "3 inhibit frames skipped within 1 s" | 0, 0, 0 |
| l2, no filler | 0 / 0.4 s | 0 / 2 | 0 / 2 | 0 / 2 | none | 0, 0 |
| l3s, no filler | 0 / 0.55 / 1.1 s | 0 / 3 | 0 / 3 | 0 / 3 | none | 0, 0, 0 |

- **Trip 7 latched in exactly the two arms with three events inside 1 s**
  (w3, l3), and in none of the other four. No other abort latched.
- **Every late frame the witness saw, the device counted** (8 of 8).
- **The filler arms** are judged by totals, as ruled 2026-10-07. In all 8
  filler events the answer to N never reached the wire, and the device booked
  each as late. Device total = witness total; device late >= witness late
  (8 >= 0 in the filler arms); device withdrawn 0 <= witness never-on-wire.
- **`0x7F9` agrees with the status page** in every arm. Its last frame in each
  arm carries the same withdrawn/late counts as the page read after the
  events. This closes the "not yet checked" item above.
- **Error frames:** 0 at the witness in every one of the 16 events, and 0 in
  every arm.
- Every event classified, none displaced: N to N+1 was 0.17-0.26 ms, and in
  the filler arms 9 filler frames sat between N+1 and the next `0x051`.
- The pass still awaits the user's approval of the corrected 12.4 skip-row
  wording (reviewer's decision item (a)); this run is evidence under that
  wording.


> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**Items 4 and 9, abort behaviour and the abort-and-recheck loop** (bench C3,
2026-09-27).
- **Build and run:** `f6eb9ec`, measurement build `GI_INSTRUMENT_TXABORT`,
  all phases in one session. Run file `txabort_full.json`, recounted
  independently by the reviewer.
- **Load:** a flood of two witnesses' ~3978 fps of higher-priority IDs, a
  stress condition rather than a truck rate.
- **Result:** the loop freed 78 of 78 under contention, with at most 2 abort
  commands and at most 36 us from the first abort. The full characterisation
  table is in spec 5.2 item 9, which makes it the emulator's calibration.

**Item 10, emulation half of the full-replay long arm:** accepted against the
five criteria in item 10 (user, 2026-10-03). Verified by the reviewer at
`1017bbf` (test only) on clean archives on madhouse-debian. Mutation outputs
are in `artifacts/gen-inhibit/review_mutations/*_1017bbf.out`.

| Criterion | Result |
|---|---|
| (1) the full capture replays with every ID | case 33: 706,283 frames |
| (2) commands lost inside the mock: 0 | `ctr_bad` 0 |
| (3) verified-on-time comparable to the bench | case 33 asserts `ontime == tx_ok` and unverified == 0: 29,977 of 29,977 |
| (4) every existing case still passes | goldens 77/77, sched pass, shim 34 cases |
| (5) the latest mutation rounds still catch what they caught | rounds 12 (12/12), 13 (every live anchor), 14 (13/13) |

The whole 300 s capture costs about 12 s of wall clock on madhouse-debian.

### Case 3 saturation, the criterion as changed on 2026-11-23 (tester, 2026-11-25, `cca06f36` `_man`)

**B22, both arms -- tester, 2026-11-25 10:53-10:58, on `cca06f36`. Result: AS SPECIFIED.
In each arm `ontime + ontime_unverified == queued`, with 0 skips, and the witness saw no
inhibit frame late.** Spec 5.2's saturation criterion as changed on 2026-11-23 (user,
`f1961dcf`): `ontime_unverified` now means "on time, with frames waiting", so the two
on-time counts together must equal the frames queued. The status page field names did not
change. Run: `runs/chain/gen-inhibit-bench_20261125T082838_8047/man_slcan/B22/`, part of the
full run in the 12.4 entry.
- **Method.** `baseline_b22.sh`: 90 s per arm with the X2 on `slcan` and the filler on a
  separate adapter, carried load >= 92.7 % (a lower bound: stuff bits cannot be recovered).
  The Kvaser witness on its hardware clock (fit slope about 54 ppm, residual p99 2.7 ms).
- **The device's page, per arm** (each arm's JSON, `arms.idle.device.sched`):

  | Arm | `ontime` | `ontime_unverified` | sum | inhibit `queued` / `sent` | `skipped` | `unverified_max_backlog` |
  |---|---|---|---|---|---|---|
  | a | 8979 | 4 | 8983 | 8983 / 8983 | 0 | 3 |
  | b | 8978 | 5 | 8983 | 8983 / 8983 | 0 | 2 |

- **The witness, per arm:** provenance as specified, replayed 212124 of 212124 found, our
  `0x051` 8983 (the device says 8983), late 0, double 0, ambiguous 0. 0 error frames,
  `tx_fail` 0, `ctr_bad` 0.
- **The judge reads the criterion as the build message words it.**
  `suite_judges.b22_saturation` (harness `aa947dec`) fails an arm unless `ontime +
  ontime_unverified` equals the inhibit class's `queued` with `skipped` 0, and fails an arm
  whose JSON has no on-time counts. The witness half is provenance's late 0. Can-fail:
  - `test_suite_judges.py` edits the `e3d9437` arms' JSONs. It has 4 new rows that must
    FAIL: the sum one short, one over, one skip, and a count missing. It has 1 row that
    must PASS: the same 8983 split another way between the two counts.
  - `test_suite_judges_mutants.py`: 169 of 169 mutants killed, the 4 new ones included
    (2026-11-23). These remove the read, leave `ontime_unverified` out of the sum, ignore
    skips, and pass a JSON with no counts.
- Recorded, not judged: response time mean 580 us in both arms, max 1099 us and 1386 us.

## 7 Arming

### Trip 6 on a live INHIBIT arm under a jam

**Arm (i), a steady jam on a live arm, with the X2 jammer -- tester,
2026-10-12 18:49-18:56, on `dace947` `_man`. Result: (A), (B) and (C) as
specified in all six arms. 200/s decides (B) and (C); 500/s adds the
error-passive regime for (A), with (B) and (C) there counting only until the
replayer went bus-off. In one 500/s arm, the reviewer ruled the judge's (B)
FAIL to be the VCM's frame received twice. Trip 6 came first in every arm;
trip 2 and failed transmits were not reached.** The reviewer's item 2, arm (i),
re-run as the user ruled on 2026-10-12: properly, with an independent witness,
not accepting the 2026-10-07 deviation (the witness was the replayer).
This replaces the 2026-10-07 `aaab89a` entry. Its run files remain
(`runs/live_jam_arm_20261007T234117.json`, `..._233822`, `..._233908`).
- **Judge** (the reviewer's 2026-10-07 (A)/(B)/(C)): (A) trip 6 latches; (B)
  after any abort the inhibit is not live and the device sends no `0x051`;
  (C) it never resumes without a re-arm. `live_jam_arm.judge()`, as in arm
  (ii). It finds device `0x051` as the first of two adjacent `0x051` with
  the same counter byte (B5): the device's answer to command k carries k+1,
  as the VCM's next command does. Also recorded, as the reviewer asked: which
  trip came first, and whether trip 2 or a failed transmit was reached (the
  page's `drv.tx_failed` before and after the jam).
- **Rig:**
  - **Jammer:** the X2 with `x2_jammer` `a89ece8c...` (TEST EQUIPMENT),
    20 us dominant pulses on CAN1 at a steady period (`TRAIN 20 <p> 5000`,
    5 s).
  - **Replay and witness:** the PCAN replays the interlock slice in normal
    mode (99.8-100.1 `0x051`/s with the device off), and the Kvaser is an
    independent witness in normal mode, as in arm (ii).
  - **Per arm:** 3 s replay with the device OFF (negative control), mode 3,
    3 s live, the train, 15 s more, then mode 0. The next arm comes about
    22 s after the train, past the 10 s error window.
  - Supply 12 V only, USB unplugged. The WiCAN was not reset or reflashed;
    uptime was continuous from the 18:17 OTA to 18:58.
- **X2 custody:**
  - 18:46: read `7f197bfd` off the wire
    (`runs/x2_identify_before_jammer_20261012T1846.txt`).
  - Dump PASS (`runs/x2_flash_custody_dump_20261012T184452.json`: two reads
    with matching sha256; app0 carries `7f197bfd` and is the boot slot).
  - Jammer flashed (`..._flash-jammer_20261012T184853.json`); banner read
    (`runs/x2_jammer_id_20261012T184901.json`).
  - Restored from the dump with verify-flash
    (`..._restore_20261012T185656.json`, digest matched).
  - 18:58: read `7f197bfd` off the wire again
    (`runs/x2_identify_after_restore_20261012T1858.txt`).
- **Harness:** `x2_jam_cases.py livetrain --w 20 --p <p> --ms 5000`. Changes
  for this run:
  - `--power` is now required and recorded as given; it was a fixed "USB
    only (PC)".
  - Each arm records `drv_summary`: `tx_failed` before and after the jam,
    worst TEC/REC, the driver states, and the abort reasons in order.
  - Tested by `test_x2_jam_cases.py` (all pass; 6 mutants, all caught;
    `runs/test_x2_jam_cases_20261012.txt`).
  - Summaries: `armi_calibration_summary.py`
    (`runs/armi_summary_20261012.jsonl`).
- **Choosing the rate.** The reviewer: steady, from calibration, so the
  WiCAN is error-active or error-passive but not bus-off for the jam's
  length. One live arm at each of 200, 500 and 1,000 pulses/s
  (`runs/x2_jam_armi_calibration_20261012.txt`). The rule, fixed before the
  ladder: the highest rate at which the WiCAN never reads `bus_off` and the
  witness has no gap over 0.5 s in the train.

| Rate | Witness error frames in the train | Longest witness gap | WiCAN worst TEC / REC | Replaying PCAN | Run |
|---|---|---|---|---|---|
| 200/s | 994 of 1,000 pulses | 0.029 s | 9 / 9 | never bus-off; replay to the end | `x2_jam_livetrain_20261012T184930` |
| 500/s | 2,327 of 2,500 | 0.294 s | 28 / 128 | bus-off at +1.29 s; no `0x051` after +1.27 s | `..._185011` |
| 1,000/s | 611 of 5,000 | 4.389 s (blind) | 21 / 128 | bus-off at +1.13 s | `..._185052` |

  - The rule picks 500/s.
  - It did not foresee the replaying PCAN going bus-off. From then on there
    is no VCM traffic, so "sends no `0x051`" and "never resumes" are tested
    with nothing to answer.
  - At 200/s the VCM keeps commanding through the train and after it, but
    the WiCAN stays error-active.
  - So three arms were run at each rate, rather than choosing after seeing
    the ladder.
  - **Reviewer's ruling (2026-10-12): both sets answer arm (i), each for a
    different part.** 200/s decides (B) and (C): the VCM commands through the
    train and after it, so "no device `0x051`" and "never resumes" are tested
    with something to answer the whole time. 500/s adds the error-passive
    regime (REC 128-134) for (A). There, (B) and (C) count only while the
    replayer was still sending (the window column below).

| Run, arm | Rate | Live before the train | Trip 6 first read (s from the ACK) | PCAN bus-off | (B)/(C) window with VCM traffic | WiCAN worst TEC / REC | `tx_failed` before -> after | Abort reasons seen | (A) | (B) | (C) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `..._185212` 0 | 500/s | yes | +0.008 | +1.839 s | +0.008 to +1.839 s only | 0 / 128 | 0 -> 0 | trip 6 only | PASS | PASS | as specified: never live again |
| `..._185212` 1 | 500/s | yes | +0.117 | +2.975 s | +0.117 to +2.975 s only | 7 / 128 | 0 -> 0 | trip 6 only | PASS | PASS | as specified |
| `..._185212` 2 | 500/s | yes | +0.091 | +4.433 s | +0.091 to +4.433 s only | 7 / 134 | 0 -> 0 | trip 6 only | PASS | PASS by the reviewer's ruling (raw judge FAIL, see below) | as specified |
| `..._185359` 0 | 200/s | yes | +0.047 | never | the whole arm, train and 15 s after | 12 / 29 | 0 -> 0 | trip 6 only | PASS | PASS | as specified |
| `..._185359` 1 | 200/s | yes | +0.069 | never | the whole arm | 0 / 11 | 0 -> 0 | trip 6 only | PASS | PASS | as specified |
| `..._185359` 2 | 200/s | yes | +0.052 | never | the whole arm | 6 / 14 | 0 -> 0 | trip 6 only | PASS | PASS | as specified |

  - **The 500/s window.** The window runs from the latch to the PCAN's
    bus-off. The replay never came back: this harness opens the PCAN without
    `auto_reset`, and the last `0x051` on the wire was at +1.83, +2.95 and
    +4.41 s. So the 500/s (B) and (C) do not cover the rest of the train or
    the 15 s after it; only the 200/s arms do.

  - "As specified" for (C): no page reading had `inhibit_live` after the
    train began, through the 15 s after it. The judge's own (C) label reads
    "NOT REACHED (no bus_off sample)", because its (C) was written for a
    bus-off, and the WiCAN never reached one.
  - **Trip 6 first, in all six arms and the three ladder arms.** The latch
    was first read 0.04-0.11 s after the witness's 10th error frame of the
    train. The jammer's pulses begin 12-50 ms before the host stamps its
    ACK, which is why +0.008 s is not early.
  - **Trip 2 and failed transmits: not reached.** Every abort reason read
    "error-frame rate exceeded". The driver stayed `running`, with
    `drv.tx_failed` 0 before and after in all nine arms. The WiCAN's TEC
    never passed 28, while REC reached 128-134 at 500/s (error-passive as a
    receiver).
  - The device's `tx_ok` did not move from the latch to the end of the arm,
    in all nine arms.
- **`..._185212` arm 2: the VCM's frame duplicated, not a device answer
  (reviewer's ruling, 2026-10-12; (B) passes for that arm).** The raw judge
  output in the run file stays "FAIL: 1 device `0x051` after the latch". It
  counted two `080080ff7f00` 0.25 ms apart at +1.2698 s, after VCM command
  `0f` at +1.2667 s. The reviewer's reasons:
  - `tx_ok` was flat at 321 from the latch (+0.09 s) to the end of the arm,
    so the device completed nothing.
  - TEC never passed 7 in that arm. A device frame held and retried for the
    1.18 s from the latch to +1.27 s would have driven TEC up by 8 per
    failure, so nothing of the device's was pending.
  - The spacing is the retransmit signature. `080080ff7f00` on `0x051` is
    about 92 bits plus stuffing. An error at the last EOF bit costs the
    transmitter an error flag and delimiter (about 14-20 bits) and the 3-bit
    intermission, then the whole frame again: about 115-125 bits, roughly
    0.23-0.25 ms at 500 kbit. That matches 0.25 ms, below the 0.34 ms
    minimum of the device's 321 answers in that arm.
  - The missing witness error frame (a 2 ms pulse was due at about
    +1.2698 s) is consistent with this, though not proven by it. A dominant
    bit at the last EOF bit is an error to the transmitter but an overload
    to receivers, so a receiver may not report it as an error frame.
- **Judge change, from this ruling:** `live_jam_arm.judge()` now has a
  duplicate class, `vcm_duplicates()`. A same-B5 pair reads "VCM duplicate,
  not judged" (listed in `vcm_duplicates_not_judged`), not a device frame,
  only when all three hold:
  - the payloads are identical;
  - they are at most `DUP_MAX_S` = 0.30 ms apart, which covers the
    retransmit: about 0.27 ms with worst-case stuffing and a superposed error
    flag (reviewer);
  - the page's `tx_ok` reads the same on the readings either side of the
    pair.

  Anything else is still a device frame.
  - **`tx_ok` is the discriminator, not the spacing** (reviewer,
    2026-10-12). The 0.34 ms was the closest device answer observed in that
    arm, not a firmware bound. A late device answer can land back to back with
    the VCM's next frame, about one frame time apart, inside the spacing
    bound. What tells it from a duplicate is that the device's own frame
    moves `tx_ok`. The spacing bound only narrows the class to what a
    retransmit can produce.
  - Tested by `test_live_jam_arm_judge.py`, with five synthetic cases: a
    duplicate, too wide, payloads differ, `tx_ok` moved, and no `tx_ok`.
    The real `185212` arm 2 pair is a fixture, both as it is (PASS) and with
    `tx_ok` raised after it (FAIL). 5 mutants, all caught
    (`runs/test_live_jam_arm_judge_20261012.txt`).
  - Rejudging every `livetrain` arm on file with it changes only this pair.
    No other arm, 2026-10-08 included, has a duplicate.
- **Run files:** `runs/x2_jam_livetrain_20261012T185212.json` (500/s) and
  `..._185359` (200/s); ladder `..._184930`, `..._185011`, `..._185052`;
  logs `runs/x2_jam_armi_{calibration,runs}_20261012.txt`.
  The five `x2_jam_livetrain_20261012T*.json` have since moved into
  `runs/baseline_c65c45e/bench/` (tester, 2026-10-24; `185212` sha256
  `d4f9c7c5...`); the two logs are still in `runs/`.

**Not a result, recorded because it happened:** in
`live_jam_arm_20261007T233908.json` the device stopped answering HTTP 3.8 s
after going live, **before the jam started**; its `0x051` answers stopped at
the same moment. It answered again in mode 0 with `tx_ok` 0 and `drv` null,
reporting `aaab89a`. That is consistent with a device reset during a live arm
with no jam; it was not reproduced in the next arm, and nothing in the run
identifies the cause. The first version of the judge scored this run as an
(A) FAIL; it now requires a live reading within 1 s of the jam start and
treats any reset as INCONCLUSIVE. The run file keeps its original `judge`
verdict (FAIL) and carries a dated `rejudged` block beside it
(INCONCLUSIVE). How often such a reset happens is to be measured by repeated
dry live arms (reviewer, 2026-10-07); the cause is for the implementor.

**Reset rate, dry live arms (tester, 2026-10-08, `aaab89a` `_man`).**
`reset_rate_arms.py`, run file `runs/reset_rate_20261008T001008.json`; reset
detection tested by `test_reset_rate_find_reset.py` (6/6).
- **Result: 0 resets in 10 arms, 603.4 s live in total.** Every arm went
  live within 0.30 s of being armed and was held 60 s; 0 unanswered pages;
  ~12,060 `0x051` on the wire per arm (the VCM replay plus the device's
  answers, ~100/s each).
- **Conditions:** PCAN replaying the same interlock slice, Kvaser an
  independent witness, page polled at ~2 Hz, console captured on COM4
  throughout. It printed 0 lines, and that says nothing: this firmware
  turns all log output off at the end of startup (`esp_log_level_set("*",
  ESP_LOG_NONE)` as `app_main`'s last statement; implementor, verified by the
  reviewer, 2026-10-08), so after boot only ROM-path output (a reset banner, a
  panic dump) can reach COM4. The no-reset finding rests on the page polls
  alone (a reset reads as mode 0 that nobody posted and `drv` null). COM4 not
  dropping is no evidence either: a USB-JTAG reset that printed `rst:0x15`
  did not drop the port (console self-test below). In `..._233908` the
  Kvaser was the replayer, the page was polled at ~10 Hz, and the PCAN was not
  yet open. Recorded as a difference, not as a cause.
- **Power:** USB only from NICKY-XPS (user, 2026-10-08); no rail to meter on
  this rig, so a brownout cannot be confirmed or ruled out by measurement.
- **Console coverage** (`console_capture.py --selftest`): a reset initiated
  through USB-JTAG printed `rst:0x15 (USB_UART_CHIP_RESET)` and the capture
  caught it 68 ms later (`console_selftest_20261008T000900.json`); the port did
  not drop in that reset, so whether a reset that takes USB down would be
  caught is not shown. A quiet console is no evidence either way.
- So the 2026-10-07 reset is one event in about 13 minutes of live arming
  today (the arms of 2026-10-07 plus these 10), not reproduced under these
  conditions.

**Reset rate, set 2, under the `..._233908` conditions (tester, 2026-10-08,
`aaab89a` `_man`).** Requested by the reviewer. Same harness with
`--poll-sleep 0.1 --replayer kvaser`; run file
`runs/reset_rate_20261008T002146.json`, console
`runs/reset_rate_console_20261008_set2.txt`.
- **Result: 0 resets in 10 arms, 600.4 s live in total.** Every arm went
  live within 0.49 s and was held 60 s. 4,774 page requests (472-485 per arm,
  ~8 Hz achieved against ~10 Hz requested), 0 unanswered. Each arm ended in
  mode 0 with `drv` null, and so did the run (`tx_ok` 6,014).
- **Conditions:**
  - The Kvaser was both replayer and witness, as in `..._233908`. It does not
    receive its own frames, so its `0x051` count is the device's answers
    only: 6,002-6,013 per 60 s arm, ~100/s.
  - The PCAN was never opened. In `..._233908` it was opened only at the jam,
    which came after the device had gone quiet.
  - No jam. Witness error frames: 0.
  - The console was captured on COM4 throughout: the port opened once and did
    not drop. It printed 0 lines, which carries no information, because log
    output is off after startup on this firmware (set 1 above). The no-reset
    finding rests on the page polls alone; a port that does not drop is no
    evidence of no reset (set 1 above).
  - Power: USB only, as in set 1.
  - The record's `replay_stats` is empty because the replay thread was still
    running when the record was written. That leaves a gap in the record; it
    does not change the result.
- With sets 1 and 2 together, the 2026-10-07 reset is one event in about 23
  minutes of live arming. It has not been reproduced under either set's
  conditions. The cause is for the implementor.

**Arm (ii), a burst that fails every device transmit:** *2026-10-07: not
run. These adapters cannot make every device transmit fail (above). The
reviewer has put a switched CANH/CANL short (spec 13) to the user.* Run on
2026-10-08 with the X2 jammer instead; the result follows.

**Arm (ii) with the X2 jammer (tester, 2026-10-08, `aaab89a` `_man`).
Result: (A), (B) and (C) PASS in 3 of 3 arms, with (B) rejudged under the
reviewer's 2026-10-08 rule for a latch followed by bus-off.**
This is case B of the reviewer's step-9 plan, approved 2026-10-08.
- **Harness:** `x2_jam_cases.py livetrain --arms 3`, run file
  `runs/x2_jam_livetrain_20261008T015033.json`. Judged per arm with
  `live_jam_arm.judge()`, the same (A)/(B)/(C) as arm (i).
- **Conditions:**
  - The PCAN replayed the interlock slice in normal mode, at 99.9-100.2
    `0x051`/s with the device off. The Kvaser was an independent witness in
    normal mode, so **the arm (i) witness deviation does not apply**.
  - Per arm: 3 s of replay with the device OFF (the discriminator's negative
    control: 0 equal pairs in every arm), mode 3, 3 s live, `TRAIN 20 200
    3000`, then 15 s more.
- **Delivered error counts during the train are not used.** Under a dense
  train the Kvaser reports error frames in bursts of 430 and goes blind for
  up to 1.5 s (`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/x2_jammer/README.md`).
- **The train also destroys the VCM's `0x051`.** The device loses fresh
  `0x051` whatever its own state, so which trip latches first is confounded
  by design (reviewer, 2026-10-08).

| Arm | Live after | Replayer bus-off | Last `0x051` on wire | Trip seen latched | `bus_error` delta at that reading | WiCAN `bus_off` (page) | Device `0x051` after latch | Live again |
|---|---|---|---|---|---|---|---|---|
| 0 | 0.25 s | +0.009 s | -0.020 s | +0.206 s, error-frame rate exceeded | 1,146 | +0.446 s | 0 | never |
| 1 | 0.24 s | -0.006 s | -0.038 s | +0.253 s, error-frame rate exceeded | 1,489 | +0.381 s | 0 | never |
| 2 | 0.33 s | +0.029 s | -0.034 s | +0.206 s, error-frame rate exceeded | 1,191 | +0.490 s | 0 | never |

Times are relative to the jammer's ACK of the train.
- **"Replayer bus-off"** is the PCAN's own status reaching 0x18 (bus-off
  plus bus-heavy).
- **"Last `0x051` on wire"** covers every `0x051`: the VCM's and the
  device's.
- **(B), rejudged** under the reviewer's 2026-10-08 rule for a latch followed
  by bus-off. `x2_jam_cases.judge_b_busoff()`, tested by
  `test_x2_jam_cases.py`. Each run file keeps its original judge beside a
  `rejudged_b` block.
  - **The rule:** (B) PASSES when the page shows the abort latched and not
    live, and no device `0x051` follows the latch. The wire-flag half is
    "not observable" once the controller is bus-off. (B) still FAILS if any
    `0x7F1` that does reach the wire after the latch has bit5 set or bit7
    clear.
  - **Result: PASS in all 3 arms.** Every page reading after the latch was
    latched and not live, and the witness saw 0 device `0x051` after the
    latch.
  - **The wire half was not observable:** the controller was bus-off from
    +0.240 / +0.128 / +0.285 s after the latch reading, so no frame could
    reach the wire.
  - The original judge read "FAIL: no 0x7F1 on the wire after the latch".
  - The valid arm 0s of `..._013935` and `..._014241` were rejudged the same
    way: PASS, bus-off from +0.247 and +0.242 s.
- **Earlier case B runs** (`..._013836`, `..._013935`, `..._014241`): see the
  harness defect below. Their arm 0s read the same as the table above; arm 0
  of `..._013935` has a rejudged block for a timestamp artifact.

**Trip 6 at the trip threshold, 12.4 error-frame row (tester, 2026-10-08,
`aaab89a` `_man`). Result: FAIL, 3 of 3 valid arms.** Observed: 10 error
frames delivered within 9.0 s while the inhibit was live, every one counted
by the device, and trip 6 did not latch. Spec 7: trip 6 latches on "10 or
more error frames within a 10 s window". **The control (9 pulses) passed in
3 of 3 valid arms.**
- **Harness:** `x2_jam_cases.py threshold`. Judged by `judge_threshold()`,
  tested by `test_x2_jam_cases.py`, which fails when the "latched before the
  10th" rule is removed.
- **Conditions:**
  - PCAN replaying (~100 `0x051`/s with the device off), Kvaser witness in
    normal mode.
  - Per arm: mode 3, 5 s live, then `PULSES 20 n interval`, then 12 s more.
  - The jammer was calibrated the same night at one error frame per pulse,
    and the device's `bus_error` +1 per pulse (`x2_jammer/README.md`).

| Run / arm | Pulses | Witness error frames | Device `bus_error` | 10th error frame | Trip 6 | Verdict |
|---|---|---|---|---|---|---|
| `..._014532` / 0 | 10 at 1000 ms | 1 per pulse | +1 per pulse (rejudged, see below) | +8.99 s | none; live until mode 0 at +21.07 s | FAIL |
| `..._015220` / 0 | 10 at 1000 ms | 1 per pulse | +1 per pulse, 0 -> 10 | +8.98 s | none; live until mode 0 at +21.06 s | FAIL |
| `..._015220` / 1 | 10 at 1000 ms | 1 per pulse | +1 per pulse, 0 -> 10 | +8.96 s | none; live until mode 0 at +21.03 s | FAIL |
| `..._015501` / 1 | 9 at 1100 ms | 1 per pulse | +1 per pulse | -- (max 9 in any window) | none | PASS |
| `..._015501` / 2 | 9 at 1100 ms | 1 per pulse | +1 per pulse | -- | none | PASS |
| `..._015731` / 0 | 9 at 1100 ms | 1 per pulse | +1 per pulse | -- | none | PASS |

Times are relative to the jammer's ACK of the pulses. The first pulse fires
1 ms after it.
- **The window is now ruled** (spec 7, "How the window moves", user,
  2026-10-08): sliding, inclusive at 10.0 s, recorded whenever armed,
  cleared only by a key-on, and only a recent burst trips. The bench rows
  above are `aaab89a`'s; the bench rerun on `3b8117c` is pending.
  - The boundary arms (10 at 1100 / 1150 ms) are still to be run.
  - The inclusive edge (exactly 10.0 s) cannot be placed on the bench, so it
    is a host-suite row below.
- **Host trip 6 cases on `a71b90a` (tester, 2026-10-08). Result: PASS, 21 of
  21 rows.** Every row latched at its due time to the microsecond, or did not
  latch where none was due. On `3b8117c` the key-on row FAILED (trip 6 at
  11.5 s, one error after a key-on); `a71b90a` changes only
  `main/gen_inhibit_core.c` and that row now passes.
  - **Harness:** `wican-fw-vtrux` branch `tester/trip6-ruled`, commit
    `5c5ba0c`. Run on `madhouse-debian` from `git archive a71b90a` with
    `test/gen_inhibit_host/` from `git archive 5c5ba0c` laid over it
    (the merge was not yet pushed): `spec_expect_check_a71b90a_20261008.txt`.
    The whole host suite on the same tree, with the eight replays generated
    per its README: 78 passed, 0 failed, 19 without goldens
    (`host_run_tests_a71b90a_20261008.txt`).
  - **How it judges.** `spec_expect_check.py` models the five bullets in one
    function, `due()`. It reads errors, arming and the key from the `.scn`,
    and live intervals and latches from the trace. Each row also carries its
    expectation worked out by hand, and a row whose model and hand answers
    differ is an ERROR, never a pass. Its self-test is 18/18: 12 model cases
    (both 10.0 s edges, recency on both sides, disarmed errors not recorded,
    key-on clear, re-arm carry) and 6 judge cases (a latch early, late, with
    none due or missing must each fail).
  - **"The first live check" has no time bound in the spec.** The test
    tolerance is `TICK_SLACK_US` = 100 ms, after the due time and never
    before it (reviewer, 2026-10-08). No PASS used it.
  - **Preconditions, checked from the trace, else INCONCLUSIVE:** live before
    the first error; for the armed-not-live rows, the gate blocked on `0x617`
    at every error, because the key gates transmission, not liveness
    (spec 7.1); for the key row, the key reading 0 then 1 between the ninth
    and tenth errors.

| Scenario | Spec requires | Core (`a71b90a`) | Verdict |
|---|---|---|---|
| controls: 12 in 1.2 s / 9 in 0.9 s / 9 at 1.1 s | 3.0 s / none / none | as required | PASS |
| 10 at 1/s from 1.5, 2.5, 5.0, 9.0, 11.5 s | at the 10th: 10.5, 11.5, 14.0, 18.0, 20.5 s | each at its 10th | PASS (5 rows) |
| 10 over 9.5 s / exactly 10.0 s / 10.5 s, from 5 s | 14.5 s / 15.0 s (inclusive) / none | 14.5 s / 15.0 s / none | PASS (3 rows) |
| 9 at 1/s from 2 s, 10 at 1/s from 33.5 s | 42.5 s | 42.5 s | PASS |
| 4 in every 10 s for 60 s | none | none | PASS |
| +12 in one reading at 5 s | 5.0 s | 5.0 s | PASS |
| 9, disarm 10.5 s, re-arm 11.0 s, 1 more at 11.5 s (`err-rate-rearm-carries`) | none in the first live interval; 11.5 s after the re-arm (the history carries) | as required | PASS |
| 9, key off 10.2 s, key on 10.8 s, 1 more at 11.5 s (`err-rate-keyon-clears`) | none: one error since the key-on | none, live to 18 s | PASS (FAIL on `3b8117c`) |
| control: the same with the key on throughout (`err-rate-keyon-control`) | 11.5 s | 11.5 s | PASS |
| 10 at 0.5 s from 2 s while armed, gate held on `0x617` until 8 s (`err-rate-armed-not-live`) | at go-live (newest 1.5 s old) | live 8.00 s, trip 6 at 8.00 s | PASS |
| the same, held until 18 s (`err-rate-stale-burst`) | none (newest 11.5 s old) | live from 18.0 s, no trip 6 | PASS |
| trip 6 at 3.0 s, disarm 5 s, re-arm 14 s (`-rearm-after-trip-expired`) | 3.0 s; then live, no re-trip (newest 10.8 s old) | 3.0 s; live 14.05 s to the end | PASS |
| the same, re-arm 8 s (`-rearm-after-trip-recent`) | 3.0 s; then at go-live (newest 4.8 s old) | 3.0 s; live and trip 6 at 8.05 s | PASS |

  - **Renamed:** `err-rate-rearm-clears` is now `err-rate-rearm-carries`.
    It was written to ask whether a re-arm clears the count, and the ruling
    says it does not.
  - All 21 scenarios have no goldens on purpose, so blessing cannot pin a
    failure.
- **Host trip 6 cases on `099e9da`, "Checked before going live" (tester,
  2026-10-12). Result: PASS, 21 of 21 rows. On `fb61892` the same check
  FAILs the two rows the rule changed.**
  - **The rule** (spec 7, "Checked before going live", user, 2026-10-10):
    each time the arm gate passes, trip 6 is checked first. A recent burst
    latches there, the device never goes live, and no inhibit frame is sent.
  - **Harness:** `spec_expect_check.py` now judges per gate pass, not per
    live interval.
    - A gate pass is the first live STATE of an arm cycle, or a trip 6 latch
      reached from armed, not live and not latched.
    - An `INHIBIT_LIVE` event or an inhibit frame printed in the latching
      call counts as going live.
    - A latch at the gate must fall within `TICK_SLACK_US` of the stimulus
      event that releases the gate (the first `0x617` on a held row, else
      the arming). Otherwise it FAILs as a latch while held.
    - The two rows' hand expectation is now "gate".
    - Self-test 35 checks; 13 mutants of the new logic, all caught
      (`runs/test_spec_expect_gate_20261012.txt`).
  - **Run:** `wican-fw-vtrux` branch `tester/golive-host`, commit `7d9c20c`
    on `099e9da`, on `madhouse-debian`, with the control in the same file
    (`runs/host_suite_099e9da_20261012.txt`).

| Scenario | Spec requires | `099e9da` | `fb61892` (control) |
|---|---|---|---|
| `err-rate-armed-not-live` | latch when the gate passes at 8.0 s (newest error 1.5 s old), never live, no inhibit frame | trip 6 at 8.00 s; no `INHIBIT_LIVE`, no inhibit frame | `INHIBIT_LIVE` and trip 6 in the same call at 8.00 s: FAIL |
| `err-rate-rearm-after-trip-recent` | 3.0 s; then latch when the gate passes after the re-arm (newest 4.8 s old), never live | 3.0 s; trip 6 at 8.05 s; no `INHIBIT_LIVE`, no inhibit frame after the re-arm | `INHIBIT_LIVE` and trip 6 in the same call at 8.05 s: FAIL |

  - On the host the `fb61892` control sends no inhibit frame before the
    latch; it is caught by the `INHIBIT_LIVE` event. The one frame before
    the latch was seen on the bench (A3) and in shim case 38.
  - The other 19 rows are as in the `a71b90a` table above, and pass.
- **Bench case C on `a71b90a`: NOT RUN (tester, 2026-10-08 21:07-21:16).**
  With the jammer on the X2, every WiCAN arm reset the WiCAN, before any
  pulse was sent. Observed, not interpreted:

| Time | X2 image | WiCAN image | Kvaser ACKing | COM4 open | Arm (mode 3) | Run file |
|---|---|---|---|---|---|---|
| 21:02 | `7f197bfd` | `aaab89a` | no | no | held (segment probe) | `wican_segment_probe_20261008T210216.json` |
| 21:10 | jammer | `a71b90a` | no | no | page read mode 0 after the post | `wican_segment_probe_20261008T211014.json` |
| 21:11 | jammer | `a71b90a` | no | yes | reset at +0.1 s | `arm_holds_probe_20261008T211156.json` |
| 21:12 | jammer | `a71b90a` | yes | yes | reset at +0.5 s | `arm_holds_probe_20261008T211227.json` |
| 21:13 | jammer | `aaab89a` | yes | yes | reset at +0.5 s | `arm_holds_probe_20261008T211328.json` |
| 21:13 | jammer | `aaab89a` | no | yes | reset at +0.2 s | `arm_holds_probe_20261008T211339.json` |
| 21:14 | jammer | `aaab89a` | no | no | reset (HTTP gone ~3 s, back in mode 0) | by hand with curl, no file |
| 21:15 | `7f197bfd` | `aaab89a` | no | yes | held 8 s | `arm_holds_probe_20261008T211543.json` |
| 21:16 | `7f197bfd` | `a71b90a` | no | yes | held 8 s | `arm_holds_probe_20261008T211634.json` |
| 21:16 | `7f197bfd` | `a71b90a` | no | no | held (segment probe, fw_ver read) | `wican_segment_probe_20261008T211648.json` |

  - **Corrected 2026-10-09: the "Kvaser ACKing" column.** The Kvaser Leaf
    Light v2 has no silent mode (below, "Who ACKs"). In the three segment-probe
    rows (21:02, 21:10, 21:16) the census opened it "silent" for 6 s after the
    arm, so it ACKed during that window; "no" holds only for the arm before it
    opened. In the `arm_holds_probe` rows marked "no" it was not opened at
    all, and "no" stands.
  - A reset is read from the post-state: mode 0 that nobody posted, `drv`
    null, HTTP unreachable for ~6 s; and COM4 dropping and re-enumerating
    (`lost` at +0.10..0.57 s, `reopened` ~0.7 s later). No ROM banner was
    caught, which means nothing: log output is off after startup (set 1
    above), and the banner would have been printed while the port was gone.
    For comparison, the USB-JTAG reset of the console self-test did not drop
    the port (`console_selftest_20261008T000900.json`).
  - The jammer was idle throughout: no `TRAIN` or `PULSES` was sent. Its
    banner reported the MCP2515 held in config mode.
  - Both WiCAN images reset with the jammer on the X2 and held with
    `7f197bfd` on it, so the WiCAN build is not what differs.
  - The charge-interposer tester reports no hardware change on the bench
    since 02:00 (2026-10-08, 21:17).
  - The 00:49-01:58 session ran the jammer on the X2 with arms that held
    (resets in 2 of 15), so this is not how that session behaved.
  - Harness: `arm_holds_probe.py` (new), which posts mode 3, polls the page
    every 200 ms and captures COM4 throughout, optionally holding the
    Kvaser in normal mode to ACK.
- **Reset matrix on the diag image `b7c65a7`: STOPPED at its baseline
  (tester, 2026-10-09 00:01-00:07). Observation only; the reviewer has asked
  that no interpretation be recorded until the WiCAN's power has been
  changed.** All WiCAN hardware work, this matrix and bench case C included,
  is on hold until then (reviewer, 2026-10-09).
  - **Conditions:** WiCAN only, mode 0 throughout, no arm posted, no CAN
    adapter opened, X2 untouched on `7f197bfd`. COM4 captured from before the
    first OTA for 6 min; the page polled every 2 s. Power: USB only, as above.
    Run files `runs/reset_matrix_baseline_20261009T000120.json`, `..._com4.txt`
    and the boot list `..._reread.txt`.
    - *Condition added 2026-10-13, corrected 2026-10-13 (reviewer):* CAN1 also
      carried the X2's unacknowledged `0x7F4` retries: 3,697 frames/s on the
      wire, all `0x7F4`, 0 error frames (tester,
      `runs/bench_census_20261013T232011`, X2 on `7f197bfd`). The X2 counts
      them as transmit errors (~3.6k/s, interposer report 2026-10-13). The X2
      was read off the wire as `7f197bfd` before and after
      (`runs/x2_identify_after_restore_20261008T21.txt`,
      `runs/x2_identify_before_jammer_20261009T04.txt`), and the bench lock
      records no X2 change between; no CAN adapter was open. The WiCAN's CAN
      driver was not running: `drv` null on 144 of 144 answered polls. The two
      baselines after the power change (below) had the same X2 condition and 0
      further boots. *First written as "`0x7F4`-`0x7F7` retries (~3.6k error
      frames/s)", from the interposer report alone; the wire reading showed
      one ID and no error frames.*
  - **The diag image did not install.** Both OTAs of `b7c65a7` (image md5
    `8e2744dc29e355505f01e42e36100d0a`; 00:01 and 00:02) were accepted, and
    the device came back on `a71b90a` (`runs/bench_flash_b7c65a7_diag_20261009.txt`,
    `..._diag_2_20261009.txt`). The retry rule (retry once, report a second
    failure) stopped the run there.
  - **Both diag boots died before 1 s.** Each printed `GI_DIAG: DIAG BUILD, NOT
    FOR VEHICLE. reset reason ESP_RST_SW (3)` after ROM `rst:0xc
    (RTC_SW_CPU_RST)`, and `OTA_HEALTH: gate armed: running ota_1, state
    PENDING_VERIFY`, then printed no uptime line at all. Their last log ticks
    were 571 ms (`phy_init`) and 714 ms (wifi init). The next boot followed
    1.2-1.3 s after each; a few boots later the device ran `a71b90a` ("image
    already VALID"). No reason line was captured for those resets: the boot
    after each lost its first lines while COM4 was down, so which image it was
    is not known, and the boot that survived was `a71b90a`, which prints none.
  - **At least 15 boots in the 6 min** (a lower bound: most boots lost their
    ROM banner while COM4 was down). Each boot's highest log tick was
    571-751 ms, at `phy_init`, softAP start, `wifi_init` or `read_ss_adc_voltage`.
    - After each OTA restart: the diag boot, then 2 more boots (first OTA) or
      4 more (second OTA) ~1 s apart, before one survived.
    - **Idle, no OTA: 7 boots ~0.9-1.0 s apart at 226-233 s into the capture**
      (about 00:05:06-00:05:13), on `a71b90a` in mode 0, 160 s after its last
      boot, with only the 2 s page poll running. COM4 dropped and came back 7
      times in that window. The last boot survived; the page answered mode 0
      at 241 s.
    - No panic dump or halt anywhere in the capture.
  - **How boots are counted:** any one of four console signs -- a ROM banner,
    the `GI_DIAG` reason line, the uptime counter restarting, or the ESP-IDF
    log tick going backwards. Port drops are recorded, not counted. The
    log-tick sign was added after this capture: the first reading of the same
    transcript, without it, counted 2 boots. Harness `reset_matrix.py`; its test
    `test_reset_matrix.py` is 19/19, with a mutant per detection rule
    (`runs/test_reset_matrix_20261009.txt`).
  - **Next, once the power has changed** (reviewer): the same 6 min idle
    baseline on `a71b90a` with the same counting; if it is quiet, the diag OTA
    and the matrix follow.
- **Idle baseline after the power change (tester, 2026-10-09 04:03-04:09).
  Observation only.** The user swapped the WiCAN's USB cable and moved it to
  another laptop port before this run.
  - **Port, read 04:03 from PnP location paths:** root-hub port `HS02` of the
    laptop's own XHCI controller (was `HS01`), not behind the dock; console
    still COM4 (`runs/usb_topology_wican_20261009T0403.txt`).
  - **Conditions:** `a71b90a` (`/check_status` git_version, read 04:02), mode 0
    throughout, no arm, no CAN adapter opened, X2 untouched on `7f197bfd`.
    COM4 captured for 360 s; one USB-JTAG reset (`pulse_rts`) at 3.0 s to start
    from; page polled every 2 s. Run files
    `runs/reset_matrix_baseline_20261009T040315.json`, `..._com4.txt` and
    `..._reread.txt`.
    - *Condition added 2026-10-13, corrected 2026-10-13 (reviewer):* CAN1 also
      carried the X2's unacknowledged `0x7F4` retries: 3,697 frames/s on the
      wire, all `0x7F4`, 0 error frames (tester,
      `runs/bench_census_20261013T232011`, X2 on `7f197bfd`). The X2 counts
      them as transmit errors (~3.6k/s, interposer report 2026-10-13). The X2
      was read off the wire as `7f197bfd` before and after
      (`runs/x2_identify_after_restore_20261008T21.txt`,
      `runs/x2_identify_before_jammer_20261009T04.txt`), and the bench lock
      records no X2 change between; no CAN adapter was open. The WiCAN's CAN
      driver was not running: `drv` null on every answered poll (173 of 173),
      so the result does not depend on the bus traffic. *First written as
      "`0x7F4`-`0x7F7` retries (~3.6k error frames/s)", from the interposer
      report alone; the wire reading showed one ID and no error frames.*
  - **Boots after the reset I started: 1.** ROM `rst:0x15
    (USB_UART_CHIP_RESET)`, project `wican-fw_obd_a71b90a_man`, "image already
    VALID", log output through `read_ss_adc_voltage` at log tick 655 ms.
  - **Boots in the following 357 s idle: 0.** COM4 never dropped; 0 of 173
    page polls went unanswered, every one reading mode 0 with `drv` null.
  - Against the same counting before the change (entry above): 3 and 5 boots
    after each OTA restart, and 7 boots in ~6 s while idle.
  - **A stale line:** the first read of the capture, in the same chunk as the
    reset's ROM banner and before it, held `GI_DIAG: up 26 s, uptime 26002 ms,
    last reset ESP_RST_USB (11)`. `a71b90a` prints no `GI_DIAG` lines. The
    harness first counted it as a boot (2 boots); it now treats a first sign
    that is an uptime line past "up 1 s" as an image it joined mid-run, not a
    boot it saw, and re-reads this run as 1 boot and the 00:01 run still as
    15 (`test_reset_matrix.py` 22/22, with a mutant for that rule).
  - **Not covered by anything of mine:** the implementor reports that at
    01:00, during its loan, an OTA of `b7c65a7` died mid-upload and the device
    then served no HTTP with a silent console and no COM4 drop for 40 s, until
    a USB-JTAG reset recovered it. No capture of mine ran between 00:47 and
    01:09, so I have no record of it.
- **Reset reproducer with the jammer on the X2, on `a71b90a`: 0 resets in
  15 arms (tester, 2026-10-09 04:20-04:42). Observation only.**
  - **X2 custody:** read off the wire as `7f197bfd` at 04:13. Read back at
    04:15, twice and identical, sha256 `33f0fb58...`, the same as the
    2026-10-08 21:03 and 00:45 read-backs. A first attempt failed at 68.7%
    with esptool "Corrupt data" and left no file. The jammer `a89ece8c` went on
    at 04:19; its banner names source `bb20e4a37b64` with the MCP2515 in config
    mode. It was restored at 04:43 (verify-flash digest matched) and read off
    the wire as `7f197bfd`, witness env, fw 4, at 04:44
    (`runs/x2_flash_custody_{dump_20261009T041519,flash-jammer_20261009T041908,restore_20261009T044342}.json`,
    `runs/x2_identify_{before_jammer,after_restore}_20261009T04.txt`,
    `runs/x2_jammer_id_20261009T041924.json`).
  - **Which nodes were on the WiCAN's segment and powered:** the WiCAN; the
    X2's CAN1, driven by the jammer through RMT on GPIO7, with no CAN
    controller on it, so it cannot ACK; the Kvaser and the PCAN, each opened
    or closed as below. The Innomaker is on the X2's CAN2, not this segment.
    The jammer sent no `TRAIN` or `PULSES` during the reproducer.

| Arms | Mode | Adapters open | Who ACKed (TEC) | Held in mode | Boots on COM4 | Run file |
|---|---|---|---|---|---|---|
| 6 | 3 | Kvaser, opened "silent" | the Kvaser (TEC 0) | 6 of 6, 10 s each | 0 | `reset_matrix_A_20261009T042000` |
| 3 | 2 | Kvaser, opened "silent" | the Kvaser (TEC 0) | 3 of 3 | 0 | `reset_matrix_B_20261009T042311` |
| 6 | 3 | none | nobody (TEC 128, error passive, `tx_ok` 0) | 6 of 6, 10 s each | not captured | `ack_source_probe_20261009T043900` to `..._044146` (6 files) |

  - Each held arm read every page poll in its armed mode, controller state
    `running`, no reset by the page rule. The six no-adapter arms are the
    2026-10-08 21:11 condition: no node ACKs, which here held too. COM4 was
    not open for them.
  - **Who ACKs: the Kvaser cannot be silent.** Its driver reports channel
    caps `0x000000C1` (extended CAN, TX request, TX acknowledge) and no
    silent-mode capability. Opened with python-can `driver_mode=False` it ACKed
    (TEC 0), and it still did with `single_handle=True`. The PCAN opened
    `state=PASSIVE` (listen-only) did not ACK: TEC reached 128, and it saw
    36,253 frames in 10 s, the WiCAN's unacknowledged retransmissions. So
    every earlier run that opened the Kvaser "silent" had an ACKing node on
    the segment. `reset_matrix.py` now uses the PCAN in listen-only as the
    witness where no node may ACK (cases A, B, E), and `bench_census.py`
    (and so `wican_segment_probe.py`) opens the Kvaser in normal mode and
    labels it ACKING (tests `test_reset_matrix.py` 27/27,
    `test_bench_census_labels.py`).
- **Bench case C on `a71b90a`: PASS (tester, 2026-10-09 04:26-04:37).** Spec
  7 trip 6 at the threshold, on a live arm: the PCAN replays the interlock
  slice, the Kvaser witnesses (normal mode), COM4 is captured throughout.
  Single 20 us pulses from the jammer, one delivered error frame per pulse in
  every arm (Kvaser) and one `drv.bus_error` count per pulse (page).
  - **Main arms, 10 pulses at 1.0 s:** trip 6 must latch at the 10th.
  - **Controls, 9 pulses at 1.1 s:** never 10 in any window, so no trip 6.
  - **Start phases:** the pulses began 1.5 s, 5 s or 8.5 s after the arm
    went live (`--lead-s`).
  - **A USB-JTAG reset of the WiCAN before each arm.** A trip 6 latch carries
    across a disarm and re-arm until a key-on (spec 7.1), and the first run
    showed it: its second arm stayed blocked on the latch and never went live
    (INCONCLUSIVE, `x2_jam_threshold_20261009T042608` arm 1). Every later arm
    started from a reset device reading mode 0, nothing latched.

| Run | Pulses | Lead | Arm | 10th error frame (s after the 1st pulse) | Latch first read (s) | Verdict |
|---|---|---|---|---|---|---|
| `..._042608` | 10 @ 1.0 s | 5 s | 0 | 8.959 | 9.054 | PASS |
| `..._042804` | 10 @ 1.0 s | 1.5 s | 0 | 8.961 | 9.067 | PASS |
| `..._042804` | 10 @ 1.0 s | 1.5 s | 1 | 8.974 | 9.026 | PASS |
| `..._042928` | 10 @ 1.0 s | 5 s | 0 | 8.992 | 9.001 | PASS |
| `..._042928` | 10 @ 1.0 s | 5 s | 1 | 8.946 | 9.006 | PASS |
| `..._043059` | 10 @ 1.0 s | 8.5 s | 0 | 8.960 | 8.936 | **INCONCLUSIVE** (rejudged; see below) |
| `..._043059` | 10 @ 1.0 s | 8.5 s | 1 | 8.985 | 9.050 | PASS |
| `..._043343` | 10 @ 1.0 s | 8.5 s | 0 | 8.952 | 9.012 | PASS |
| `..._043343` | 10 @ 1.0 s | 8.5 s | 1 | 8.952 | 8.987 | PASS |
| `..._043521` | 9 @ 1.1 s | 1.5 s | 0, 1 | (max 9 in any window) | none | PASS, PASS |
| `..._043645` | 9 @ 1.1 s | 5 s | 0, 1 | (max 9 in any window) | none | PASS, PASS |

  - Run files are `runs/x2_jam_threshold_<stamp>.json`. "Latch first read" is
    the first page reading showing trip 6 latched, so it trails the latch by
    up to one poll (~0.12 s). Through `..._043059` a reading was stamped
    when its request STARTED; from `..._043343` on, when its response
    arrived.
  - **Rejudged: `..._043059` arm 0** was judged FAIL, "trip 6 latched before
    the 10th delivered error frame". The page reading that first showed the
    latch was stamped 8.936 s, 24 ms before the Kvaser's 10th error frame
    (8.960 s), and already showed `drv.bus_error` at 10. The stamp was taken
    before the HTTP request was sent, so the reading describes state from
    some time within that request, and the request's end was not recorded.
    The reading cannot place the latch before the 10th error frame, and it
    cannot place it after. Harness defect, fixed (the poller now stamps at
    the response; `test_x2_jam_cases.py`, 36 checks); the same lead was
    re-run as `..._043343`, both PASS. The original verdict stays in the
    record beside this note.
  - **No reset during case C** other than the deliberate pre-arm resets:
    every judged arm read its armed mode throughout, and the judge's
    reset-while-armed rule found none.
  - Harness for this entry: `x2_jam_cases.py` gained `--lead-s`, a per-arm
    `--reset-wican` for case C, the response-time stamp, and the window's
    inclusive 10.0 s edge per the spec 7 ruling (it was still the strict
    edge, which no arm here depends on).
- **The WiCAN on 12 V: idle baseline and the reset reproducer, on `a71b90a`.
  0 resets (tester, 2026-10-09 09:27-09:49). Observation only.**
  - **Supply.** Per the user, 12 V goes into OBD pin 16 with ground on pins
    4-5. The source type, set voltage and current limit were not stated.
    **USB was still connected** (laptop port HS02, COM4). The device's own
    battery-sense reading (`/check_status` `batt_voltage`) was 14.1 V before
    the baseline and before and after every run below, and 14.0 V at the end
    (`runs/batt_voltage_log_20261009.jsonl`). It reads 14.x V, not 12 V.
    These are spot readings in 0.1 V steps taken between runs, so they
    cannot show a sag during a run. None read "0.0V", the page's
    no-reading value.
  - **Which rail fed the board is NOT settled.** `batt_voltage` shows the
    voltage that reaches OBD pin 16. It does not show whether the 3.3 V rail
    drew from it or from USB 5 V. With USB connected, nothing here separates
    the two; a run with USB unplugged (page polls only) would.
  - **The sleep settings are stored but unused in this build, so they are not a
    reset mechanism.** The status page shows the stock sleep settings
    (`sleep_volt` 13.1 V, `wakeup_volt` 13.5 V). Per the reviewer, at
    `a71b90a` the sleep state machine is removed (`main/sleep_mode.c:377-382`,
    spec 8) and the ADC task only samples the voltage, so a sag cannot put
    the board to sleep. The user ruled that sleep stays as it is.
    *Corrected 2026-10-09: this first read as an open observation about a
    possible sleep below 13.1 V.*
  - **Idle baseline, 09:27-09:33.** Counted exactly as
    `reset_matrix_baseline_20261009T040315`: one USB-JTAG reset at 3 s, then
    357 s idle in mode 0, no CAN adapter opened, X2 untouched.
    - Boots on COM4: 1, the reset's own (`rst:0x15 USB_UART_CHIP_RESET` at
      3.0 s). 0 further boots, 0 panic dumps.
    - COM4 drops: 0.
    - Page polls: 0 of 173 unanswered, all mode 0 with `drv` null.
    - Run files: `runs/reset_matrix_baseline_20261009T092717{.json,_com4.txt,_reread.txt}`.
    - *Condition added 2026-10-13, corrected 2026-10-13 (reviewer):* CAN1 also
      carried the X2's unacknowledged `0x7F4` retries: 3,697 frames/s on the
      wire, all `0x7F4`, 0 error frames (tester,
      `runs/bench_census_20261013T232011`, X2 on `7f197bfd`). The X2 counts
      them as transmit errors (~3.6k/s, interposer report 2026-10-13). The X2
      was read off the wire as `7f197bfd` before and after
      (`runs/x2_identify_after_restore_20261009T04.txt`,
      `runs/x2_identify_before_jammer_20261009T09.txt`), and the bench lock
      records no X2 change between; no CAN adapter was open. The WiCAN's CAN
      driver was not running: `drv` null on every answered poll (173 of 173),
      so the result does not depend on the bus traffic. *First written as
      "`0x7F4`-`0x7F7` retries (~3.6k error frames/s)", from the interposer
      report alone; the wire reading showed one ID and no error frames.*
  - **X2 custody.** Read off the wire as `7f197bfd` at 09:35. Read back
    09:35-09:39, twice and identical, sha256 `33f0fb58...`, the same as the
    04:15 read-back. The jammer `a89ece8c` went on at 09:39; its banner names
    source `bb20e4a37b64` with the MCP2515 in config mode. It was restored at
    09:48 (verify-flash digest matched) and read off the wire as `7f197bfd`,
    witness env, fw 4, at 09:49
    (`runs/x2_flash_custody_{dump_20261009T093530,flash-jammer_20261009T093919,restore_20261009T094807}.json`,
    `runs/x2_identify_{before_jammer,after_restore}_20261009T09.txt`,
    `runs/x2_jammer_id_20261009T093923.json`).
  - **Reproducer, 09:40-09:48**, the same 15 arms as the 04:20 run. The same
    nodes were on the segment, and the jammer sent no `TRAIN` or `PULSES`.

| Arms | Mode | Adapters open | Who ACKed (TEC) | Held in mode | Boots on COM4 | Run file |
|---|---|---|---|---|---|---|
| 6 | 3 | Kvaser, normal mode | the Kvaser (0 error frames seen) | 6 of 6, 10 s each | 0 | `reset_matrix_A_20261009T094024` |
| 3 | 2 | Kvaser, normal mode | the Kvaser (0 error frames seen) | 3 of 3 | 0 | `reset_matrix_B_20261009T094336` |
| 6 | 3 | none | nobody (TEC 128, `tx_ok` 0) | 6 of 6, 10 s each, every poll answered | not captured | `ack_source_probe_20261009T0945*`-`..._094750` |

  - The first two rows repeat the 04:20 run as it actually was: the Kvaser
    open and ACKing (`reset_matrix.py --kvaser-acks`, new; the 04:20 run
    opened it "silent", which ACKs the same way). Every page poll was
    answered in the armed mode, with no reset by the page rule. The WiCAN
    read mode 0 with `drv` null afterwards.
  - Harness for this entry: `reset_matrix.py` now requires `--power`, which
    the run file records (it was hard-coded "USB only (PC)"), and gained
    `--kvaser-acks` (`test_reset_matrix.py`, 36 checks).
    `batt_voltage_log.py` records `batt_voltage` and `git_version` from the
    status page and nothing else (`test_batt_voltage_log.py`).
- **The WiCAN on 12 V with USB unplugged: it kept running through the unplug,
  then idled 551 s with no outage, on `a71b90a` (tester, 2026-10-09
  11:25-11:40). Observation only.**
  - **Supply.** 12 V on OBD pin 16 with ground on pins 4-5 (user). USB was
    connected at the start and the user pulled it during the run. The run
    stamps the unplug as the first poll at which COM4 was absent: 33.0 s in.
    `batt_voltage` read 14.1 V before and after
    (`runs/batt_voltage_log_20261009.jsonl`).
  - **No console, so reboots are read from the page and the WiFi link**
    (`page_only_baseline.py`, new). Neither `/gen_inhibit` nor `/check_status`
    carries an uptime. Every poll (1 s) records whether the page answered, how
    long it took, its mode and `drv`, and whether the laptop's WLAN was on the
    WiCAN's SSID. An OUTAGE is a run of polls that went unanswered, were off
    the WLAN, or took longer than 3 s. **The detector cannot tell a reboot
    from a SoftAP or WiFi dropout**; 0 outages means no reboot by this test.
  - **Positive control first, with USB plugged back in by the user for it.**
    One USB-JTAG reset at 30 s:
    - The first version (no stall sign) saw NO outage. The page never went
      unanswered and the WLAN never dropped: the request in flight blocked
      7.4 s, then answered (`runs/page_only_baseline_20261009T112608.json`).
      Detector defect, fixed: a poll slower than 3 s is now an outage sign
      (`test_page_only_baseline.py`, 16 checks; the mutant without the stall
      sign fails).
    - The fixed version saw exactly 1 outage, 30.0-37.1 s, one poll of
      7.0 s, and COM4 stayed present (`runs/page_only_baseline_20261009T112840.json`).
  - **How the run started:** with no reset. The WiCAN had been up since the
    11:28 control reset, in mode 0, and the run started polling it at 11:30:20.
  - **The unplug (which rail feeds the board):** no outage at it. The polls at
    32.0 s (COM4 present) and 33.0 s (COM4 gone) answered in 22 ms and 29 ms,
    and every poll before and after answered too. So when USB 5 V went away
    the board kept running on the 12 V supply, without a reboot that this
    detector can see.
  - **Idle after the unplug:** 551.4 s, 552 polls. 0 outages, 0 unanswered,
    0 off the WLAN, 0 stalled (slowest poll of the run 0.151 s), every poll
    in mode 0 with `drv` null.
  - **The 3 s stall limit is a chosen parameter, and this result does not
    depend on it.** The slowest poll of the run took 0.151 s and the control's
    reboot stalled a poll for 7.0-7.4 s, so any limit between those reads this
    run the same way. A reboot that fell wholly between two polls would escape
    it, but the control's reboot took ~7 s against a 1 s poll period. The
    detector reads a reboot only indirectly: neither page reports an uptime or
    a reset reason. It was validated only with USB connected, since the
    control reset needs USB. A chip that stays wedged would read as one
    outage that never ends.
  - Run file: `runs/page_only_baseline_20261009T113020.json`.
  - *Condition added 2026-10-13, corrected 2026-10-13 (reviewer):* CAN1 also
    carried the X2's unacknowledged `0x7F4` retries: 3,697 frames/s on the
    wire, all `0x7F4`, 0 error frames (tester,
    `runs/bench_census_20261013T232011`, X2 on `7f197bfd`). The X2 counts them
    as transmit errors (~3.6k/s, interposer report 2026-10-13). The X2 was
    read off the wire as `7f197bfd` before and after
    (`runs/x2_identify_after_restore_20261009T09.txt`, 09:49, and
    `runs/x2_identify_before_hold_20261010T1659.txt`), and the bench lock
    records no X2 change between; no CAN adapter was open. The WiCAN's CAN
    driver was not running: `drv` null on every answered poll (600 of 600), so
    the result does not depend on the bus traffic. *First written as
    "`0x7F4`-`0x7F7` retries (~3.6k error frames/s)", from the interposer
    report alone; the wire reading showed one ID and no error frames.*
- **Rejudged:** `..._014532` arm 0 first read the device as missing pulses 0
  and 2. Host and Kvaser clocks differ by a few ms, and a page reading
  stamped 1.4 ms before pulse 0's error frame already counted it. The
  attribution now ignores readings within 50 ms of a pulse (tested). The
  record keeps its original verdict beside the rejudged block. Both read
  FAIL.
- **INCONCLUSIVE arms:**
  - `..._015220` arm 2 and `..._015501` arm 0: a device reset while armed,
    see below.
  - The arms >= 1 of `..._014532`: the harness defect below.

**Two device resets during the threshold arms (tester, 2026-10-08), by the
post-state rule.** In each case the page read mode 0, `tx_ok` 0, `drv` null
and nothing latched, without the harness posting mode 0. Readings around it
were 7.12 s apart, a page request that blocked for about 7 s.
- `..._015220` arm 2: 4.64 s after the arm was posted, **before any pulse**,
  with the jammer idle.
- `..._015501` arm 0: 16.62 s after the arm was posted, 2.5 s after the last
  pulse.
- The second arm also read `disabled` false, `disable_reason` "", `self_off`
  "" and key flags false. Those fields are captured since that run.
- COM4 was not captured during these cases, so there is no console
  evidence.
- **Compared by arm, not by second** (reviewer, 2026-10-08), because the
  resets come 4-5 s after arming: **2 of 15 step-9 live arms, against 0 of 20
  dry arms** in the two reset-rate sets (1,203.8 s live).
- **Conditions the step-9 arms had and the dry sets did not.** These are
  conditions, not causes:
  - the X2 carried the jammer image instead of the interposer build, so
    nothing was bridged and no `0x7F4-0x7F7` reached the WiCAN's segment;
  - the PCAN's status was polled every 20 ms during each arm;
  - the jammer's COM3 console was open;
  - the page was polled at ~10 Hz by one thread while another polled for
    live. Set 2 also polled at ~10 Hz, but from one thread.
  *Corrected 2026-10-08:* this list first also named "the Kvaser in normal
  mode with the PCAN replaying". That was not a difference: set 1 ran the
  same way.
- **Rate tonight:** 2 resets in 15 valid live arms with 201.6 s live in
  total. These are the step-9 live arms (cases B and C, plus one dry re-arm),
  counted from the run files with `x2_jam_cases.live_totals()`, tested.
  - An arm's live time runs from its first live page reading to its last, so
    case B arms count about 3 s each: they latched under the train.
  - The 4 invalid arms (harness defect below) add 0.5 s and are not counted.
  - With 233908 that makes three resets, two of them 3.96 s and 4.64 s after
    arming with the jammer idle (reviewer, 2026-10-08).
- **From now on** `x2_jam_cases.py` captures COM4 for the whole session and
  attaches banner lines and port events to each arm. It also records
  `live_totals` per session (reviewer, 2026-10-08). A quiet console is still
  no evidence: after startup this firmware sends no log output at all, so only
  a reset banner or a panic dump can appear (set 1 above).
- Recorded as observed; the cause is for the implementor.

**Harness defect, found and fixed 2026-10-08: stacked replays.**
- **What happened:** `x2_jam_cases.py` shut the PCAN down after each arm but
  did not stop the replay thread. PCAN-Basic addresses the channel by a
  global handle, so when the next arm re-opened it, the old thread's sends
  worked again. The replay rate with the device off was 100, 200 and
  300 `0x051`/s in arms 0, 1 and 2.
- **Arms invalidated** (marked in each run file as `harness_defect` /
  `invalid_arms`): arms >= 1 of `x2_jam_livetrain_20261008T013935` and
  `..._014241`, `x2_jam_rearm_20261008T014443` and
  `x2_jam_threshold_20261008T014532`.
- **What those arms showed:** trip 7 latching 0.3-0.5 s after going live,
  and bursts of 16 error frames 0.06-0.18 s after arming with the device's
  TEC at 126. **These are not device findings.** With the fix, every arm
  replays at ~100/s and neither shape has recurred in the 10 arms since the
  fix (case B `..._015033`, case C `..._015220`, `..._015501`, `..._015731`).
- **Fix:** `StoppableSender`, tested on the real `LF.play` with a fake bus,
  with an unstopped control. Each arm now records its own replay rate.


> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**Images on `f363674`** (2026-10-04, verified by the reviewer). `f363674`
changes `test/` only; `main/` is unchanged since `5a4aea1`.

| Image | md5 | Code identity |
|---|---|---|
| `build/wican-fw_obd_f363674_man.bin` | `87bba6466cc46985c2518e5f1859798c` | Against `5a4aea1_man`: 92 bytes differ, all in the accepted regions (version, project name, build time/date, ELF sha256, `git_version` rodata, checksum and appended sha256). `5a4aea1_man` was compared the same way against `build/wican-fw_obd_4a0a796.bin`, the build of the case 3 arms. That file is a rebuild, so carrying the `4a0a796` results over assumes the clean rebuild is code-identical to the image that was flashed. |
| `build-autoarm/wican-fw_obd_f363674_auto.bin` | `04577b85333314f52681b905a9ce9a63` | Carries no earlier results; it is evidenced by its own runs below. |

**Auto-arm build** (2026-10-04, on `f363674`):

| Check | Run file | Result |
|---|---|---|
| Arm-state discriminator, manual image | `diag_build_page_check.json` (20:45) | `0x7F1` `diag_flags` bit3 = 0, expected 0: match |
| Arm-state discriminator, auto-arm image | `diag_build_page_check_auto.json` (20:49) | bit3 = 1, expected 1: match |
| Negative control | `diag_build_page_check_negctl.json` (20:49) | auto-arm image checked against expectation 0: `verdict` FAIL, `reason` mismatch |
| Manual image back on the device | `diag_build_page_check_endstate.json` (21:00) | bit3 = 0, mode 0 |
| Build on the wire | all four | `0x7F3` hash `0x60EFB786` = fnv1a32("f363674"), agreeing with HTTP `git_version` |
| Comes up armed, not live on a silent bus | `autoarm_gate_20261004T211130.json` | mode 3, not live, blocked "no fresh 0x051" |
| Goes live once traffic arrives | `autoarm_live_20261004T211130.json` | live 0.266 s after traffic. The witness saw 192 frames against `tx_ok` 205: `tx_ok` counts since driver install, and the witness window was 2.486 s |
| Reset into a running generator does not go live (condition 5) | `autoarm_reset_engine_running_20261004T211130.json` | 32 readings over 20 s: never live, blocked "generator running", `tx_ok` 0, `gene_rpm` 800 fresh. On the wire, 2022 `0x051` sent by the replay and 2022 seen by the witness, excess 0 |

**Which image ran the 21:11:30 phases.** The `_auto` image was re-flashed
after the 21:05 `diag_check` run on `_man`, and no record was taken of that.
The image is identified by the harness's own check: it reboots the device and
refuses to run unless the device comes up in a mode that matches
`--expect-autoarm 1` (a manual build comes up in mode 0), with `git_version`
`f363674`. That identifies an auto-arm build of `f363674`, and `_auto` is the
only one on disk. No md5 or bit3 reading ties the flashed file to it.

**Boot-to-live time** (spec 12.4 auto-arm row): **not established.** The
latest reading, `autoarm_boot_20261004T211130.json`, gives 186 ms on the
host clock against 821 ms on the driver clock, and the harness refuses to
quote either.

**One pulse counted 10-12 times: the B6 threshold arms on `e3d9437` and the
multi-count pulses on record -- tester, 2026-10-20.** Spec 7, trip 6: 10 or
more error frames within a 10 s window.

- **What prompted it:** in the e3d9437 acceptance run's B6 re-run (`runs/chain/gen-inhibit-bench_20261020T004332_59b0`), arm 0 of n10 lead 8.5 latched trip 6 1.108 s after its first pulse, with 2 error frames delivered.
  - The witness saw one error frame per pulse.
  - The device's `bus_error` rose by 11 over pulse 2, with TEC 0.
  - The B6 entry under 12.4 has the run.
- **More arms on `e3d9437` `_man`** (reviewer, 2026-10-20), `b6_rate_run.sh`, 2026-10-20 01:37-01:57:
  - 20 threshold arms of n10: 7 at lead 1.5 s, 7 at 5 s, 6 at 8.5 s.
  - The WiCAN was reset before each arm (POST /system_reboot, recorded in each arm).
  - The X2 ran the jammer `a89ece8c` under custody (dump of `756d6714` first, restored and read off the wire after).
  - PCAN replaying ~100 `0x051`/s, Kvaser witness in normal mode.
  - Run: `runs/b6_rate_e3d9437_20261020T013658`.
  - **Result: 20 of 20 arms PASS; 200 of 200 pulses counted once.** One witness error frame and a device `bus_error` delta of 1 per pulse. Every arm latched "error-frame rate exceeded" 9.03-9.12 s after its first pulse, at the 10th. TEC read 0 and REC at most 1, from page samples a median 0.127 s apart (95th percentile 0.145 s).
  - A first attempt, `runs/b6_rate_e3d9437_20261020T013136`, ran no arms (the jammer was never written; see the README row for `b6_rate_run.sh`).
- **Every multi-count pulse on record** (`b6_multicount_landing.py` over every `x2_jam_threshold` record with a witness file: 36 earlier and the 3 above). The 11 records of 2026-10-08/09 have no witness file and cannot be placed. Times are the Kvaser's hardware clock, relative to the pulse's witness error frame. The device state is the last page before that error frame (30-128 ms before).

| Record / arm / pulse | Device `bus_error` delta | Last frame before | Next frame after | Device state before |
|---|---|---|---|---|
| c65c45e `014347_VOID_noreset` / 1 / 1 | 12 | `0x051`, 6.65 ms | `0x051`, +260 us | mode 3, live, not latched |
| c65c45e `014347_VOID_noreset` / 1 / 6 | 10 | `0x051`, 6.74 ms | `0x051`, +260 us | mode 3, latched "error-frame rate exceeded" (the arm's own latch, after pulse 1) |
| c65c45e `014739_VOID_noreset` / 0 / 7 | 10 | `0x7F3`, 7.0 ms | `0x051`, +5.8 ms | mode 3, live, not latched |
| e3d9437 59b0 n10 lead 8.5 / 0 / 2 | 11 | `0x051`, 9.55 ms | `0x051`, +260 us | mode 3, live, not latched |

  - TEC and REC read 0 around all four.
  - The c65c45e runs are VOID for the missing WiCAN reset; the device's counts in them are still data.
- **Landing against count**, over all 679 placed pulses:
  - with an `0x051` 200-320 us after the error frame: 3 multi-count of 23;
  - any other landing: 1 multi-count of 656.
  - **Correction (tester, 2026-10-21): 3 of 20 and 1 of 659.** The 23 added the 3 multi-count pulses twice: 3, plus "16 earlier single", plus 4 today. But the 16 earlier class pulses already included the 3. Re-judged over the same 679 pulses (`b6_tie_rejudge.py`, `runs/b6_tie_rejudge_20261021_before_fix.txt` and `..._after_fix.txt`): class 20, of which 3 multi-count; other landings 659, of which 1. The tie rule below changes neither count.
  - Today's 200 include 4 in the first class, each counted once.
- **Not concluded here:** why one pulse is sometimes counted 10-12 times. It is with the reviewer and the user (2026-10-20). No product code was changed.
- **B6 investigation, session 1, on the `_ecc` measurement build** (user: investigate first; reviewer's plan, 2026-10-20, option 1), 2026-10-20 21:41-22:12:
  - **Build:** the tester's throttled build of `8abf16b` with `GI_INSTRUMENT_ECC` (elf `637e1af1...`, md5 `b6c6e86c...`; `runs/b6_ecc_build_8abf16b/`). OTA'd with `bench_flash.py` at `dbea60d` and `--no-capture`: TOOK. Restored to `e3d9437` `_man` (`25f50ac0...`) after, TOOK; release reboot PASS.
  - **Stimulus as B6:** the PCAN replaying ~100 `0x051`/s, the Kvaser witnessing, the X2 on the jammer `a89ece8c` under custody (dump of `756d6714`, restored and read off the wire after). 29 pulses at 1000 ms in one PULSES command, trip 6 judged on the first 10. The WiCAN was reset before each arm by `POST /system_reboot` (no COM4 console). `/gi_ecc` was read after each arm.
  - **Arms:** 20, at leads 7 x 1.5 s, 7 x 5 s, 6 x 8.5 s (`b6_ecc_run.sh`). Run: `runs/b6_ecc_20261020T214017/`; per-arm table `runs/b6_ecc_20261020T214017/session_summary.txt`; tool versions `runs/b6_ecc_20261020T214017/tool_versions.txt`.
  - **Result: 18 PASS, 2 INCONCLUSIVE, 0 over-counts.**
    - Every PASS arm latched "error-frame rate exceeded" 9.0-9.1 s after its first pulse; the first post-latch pulse was the 11th.
    - Lead 1.5 arm 1, INCONCLUSIVE: the witness has no error frame for pulse 10 (28 in 29 pulses). `/gi_ecc` has 29 isr rows, so its rows were not aligned with the witness. The device latched 9.1 s after the first pulse.
    - Lead 1.5 arm 2, INCONCLUSIVE: neither the witness nor `/gi_ecc` has an error frame for pulse 5 (28 each, aligned). The device latched 13.0 s after the first pulse. Its own rings (`x2_jam_threshold_20261020T214653.json`, `arms[2].ecc_page`; reviewer's reading, checked by the tester) put the latch at the 13th delivered error frame, not the 10th:
      - the first ticks row with `latched` 1 has `err_n` 13 (t_us 24,769,459);
      - E1 to E10 are 9.9999 s apart on the isr clock, and 10.0029 s apart on the ticks rows that pushed them (`pushed` 1; E1 11.7704 s, E10 21.7734 s); each tick follows its isr row by 0.3-6.4 ms;
      - on the ticks, E2-E11 span 10.0028 s, E3-E12 10.0040 s and E4-E13 10.0000 s. The latch came at E13, the first span of 10.0 s or less.
      - So on the isr clock ten error frames fell within 10 s at E10 and the device did not latch until E13. Observed, not judged: the boundary case is with the reviewer and the user.
    - Lead 8.5 arm 2: the witness has 28 error frames, the missing one after the 10th; PASS.
  - **Per pulse** (`b6_multicount_landing.py`, `runs/b6_ecc_20261020T214017/landing.txt`): 577 pulses placed. The device's `bus_error` delta was 1 for 576 and 0 for one (lead 1.5 arm 6 pulse 27, post-latch). None was counted more than once.
  - **Landing class** (`b6_class_count.py`, `runs/b6_ecc_20261020T214017/class_count.txt`): 9 pulses had an `0x051` 200-320 us after the error frame, all counted once. The next frame after the error frame was an `0x051` for 567.
  - **`/gi_ecc`:** no ring was overwritten in any arm, and no read failed. **All 578 isr rows read the same: ecc `0x00`, type `bit`, dir TX, segment 0.** The reader flags this on every arm: the build's assumption that the probe reads the captured code may be wrong. Reported as observed, not judged.
  - **The stop rule** (the 3rd over-count, with at least 10 single-count pulses in the class; cap 80 arms) is not met: 0 over-counts, 9 class pulses, 20 arms. Session 2 is held (reviewer, 2026-10-20): whether it runs, and on which build, is with the user, with the boundary case and the `/gi_ecc` rows above.
  - **Desk points on session 1** (reviewer, 2026-10-20), from the records only. Tool `b6_session1_desk.py`; output `runs/b6_ecc_20261020T214017/desk_points.txt`. Error-frame and frame times are on the Kvaser's hardware clock, and lags on the device's clock. Observed, not judged.
    - **The undelivered pulses.** In all three, the jammer printed its PULSE line on schedule, 1,000,000 us after the one before, and its DONE line reads `end=complete ... pulses=29`. The PCAN status stayed OK throughout. The "predicted instant" is a fit of the arm's witness error times on the jammer's `t_us` (residuals within +/-55 us).
      - Lead 1.5 arm 1, pulse 10: **a witness miss.** The device has an isr row for it. Put on the witness clock (a fit of the other pulses, residuals within +/-44 us), the row falls 8.6 us from the predicted instant. The witness has no error frame. On the wire, an `0x051` with data `080080ff7f09` is stamped 38.9 us before the predicted instant, and the same data again 249.9 us after that. That pair is the only `0x051` repeated with the same data less than 400 us apart in the session's three witness files.
      - Lead 1.5 arm 2, pulse 5: missing on both the witness and the device. The last frame, an `0x051`, is 555 us before the predicted instant, and the next 16.2 ms after it.
      - Lead 8.5 arm 2, pulse 14: missing on both (the device has 28 isr rows, like the witness). The last frame is 10.4 ms before the predicted instant, and the next, an `0x051`, is 606.5 us after it.
      - The records do not say where in a frame the Kvaser stamps it, so idle against mid-frame is open for pulse 10. Pulses 5 and 14 are more than 500 us from any frame stamp. For comparison, the 577 delivered error frames came 80 us to 18.9 ms after the last frame stamp (median 5.2 ms).
    - **The pulse with `bus_error` delta 0** (lead 1.5 arm 6 pulse 27, post-latch) has a witness error frame and an isr row (seq 27: TEC 3, REC 1, status 44). It also has a ticks row with `pushed` 1 and `bus_error_count` 27, 1.83 ms after the isr row.
      - The delta comes from the page. The judge's reading after the pulse was sent at +0.000 s from the error frame and returned at +0.052 s (host clock), and read 26. The next reading, at +0.172 s, read 27.
    - **578 isr rows against 577 placed pulses:** the extra row is lead 1.5 arm 1 pulse 10, the witness miss (29 isr rows, 28 witness error frames). Every other arm has as many isr rows as witness pulses. Pushed ticks rows equal isr rows in every arm (578). Pulse 27 above is one of the 577.
    - **Against the earlier rate.** Every record with a witness file; an over-count is a delta above 1, and the class is the stop rule's:

| Build | Placed pulses | Over-counts | Class pulses | Class over-counts |
|---|---|---|---|---|
| `c65c45e` (10 records, 5 of them VOID) | 192 (96 VOID) | 3 (all VOID) | 11 (6 VOID) | 2 |
| `e3d9437`, the 20 rate arms included (23 records) | 583 (200 rate) | 1 | 14 (4 rate) | 1 |
| `_ecc`, session 1 | 577 | 0 | 9 | 0 |

      - The earlier entry's 679 placed and 23 class pulses are the same records without the chain run `..._5880`, which ran after that count (96 placed, 2 class). One `e3d9437` pulse could not be placed.
      - At each earlier rate, the chance of 0 over-counts in 577 pulses and of 0 in 9 class pulses:
        - `e3d9437`, 1/583: 0.371 and, at 1/14, 0.513;
        - `c65c45e`, 3/192: 0.0001 and, at 2/11, 0.164;
        - both pooled, 4/775: 0.051 and, at 3/25, 0.317.
    - **Recording lag**, the ticks row's `t_us` minus its isr row's, over all 578 rows. Each isr row is paired with the `pushed` 1 ticks row whose `bus_error_count` equals its seq; none was left unpaired.
      - Lag: min 16 us, median 1,536 us, p95 5,191 us, p99 7,147 us, max 10,009 us.
      - The gap from the error frame to the next frame the device received, from the witness: min 170 us, median 4,690 us, p95 11,690 us, p99 14,080 us, max 17,150 us. The WiCAN's own ids (0x7F0-0x7F3, 0x7F8-0x7F9, from its diag DBC) are excluded. The witness miss uses the device's row put on the witness clock.
      - Lag minus that gap: at most +1,520 us. Rows above the gap by more than 0 us: 125; 250 us: 11; 500 us: 7; 1 ms: 3; 2 ms: 0. The margin was set at 1 ms in the tool before it ran.
      - **The 3 rows above the gap + 1 ms:** lead 1.5 arm 1 pulses 2 and 10, and arm 6 pulse 6. Each has a next frame 220-260 us after the error and a lag of 1.6-1.7 ms.
      - The largest lag, 10,009 us (lead 5 arm 5 pulse 7), had its next received frame 11.95 ms after the error.
    - **TEC, REC and status by row.** 565 of 578 rows read TEC 0, REC 1, status 44 (0x2C: tx buffer free, tx complete, transmitting), and every row has events 128. The other 13:
      - **TEC 8, REC 0, status 32** (0x20: transmitting), 7 rows:
        - lead 1.5 arm 1: pulses 2 (pre-latch, class), 10 (the witness miss) and 28 (post-latch);
        - lead 1.5 arm 6: pulses 3 and 6 (pre-latch, class) and 26 (post-latch);
        - lead 5 arm 4: pulse 10 (pre-latch).
      - **TEC 0, REC 0, status 32**, 1 row: lead 1.5 arm 6 pulse 9 (pre-latch, class).
      - **TEC 3, REC 1, status 44**, 4 rows: lead 1.5 arm 1 pulses 11 and 29, arm 6 pulse 27 (the delta-0 pulse) and lead 5 arm 4 pulse 11. Each is the pulse after a TEC 8 row in the same arm.
      - **TEC 0, REC 9, status 44**, 1 row: lead 5 arm 3 pulse 4 (pre-latch).
      - **The status-32 rows and the next frame.** All 8 status-32 rows had a frame 200-320 us after the error frame: `0x051` for 5, `0x7F8` for 2, `0x7F1` for 1. 13 rows had such a frame, and the other 5 read TEC 0, REC 1, status 44.
      - **The 9 class pulses:** 4 read status 32 and 5 the common values.
      - **The undelivered pulses:** the two missing on both have no row.
- **B6 investigation, session 2, on the readout-fixed `_ecc` build** (user: fix the readout, then session 2 with the same stimulus and stop rule; reviewer, 2026-10-20), 2026-10-20 23:21-23:51. Observed, not judged.
  - **Build:** the tester's throttled clean build of `a55d52c` with `GI_INSTRUMENT_ECC` (elf `cec8433d...`, md5 `9f68d818...`; `runs/b6_ecc_build_a55d52c/`). The implementor's image (md5 `9cc4120d...`) was not used. `prod_build.py --check-only` on the tree refuses it: HEAD detached. Its build-cache refusal reads only `build/`, so it did not see this build's `build-tester-ecc/`, though `GI_INSTRUMENT_ECC` is in its forbidden list.
  - **The OTAs:** `bench_flash.py` at `dbea60d` with `--no-capture`, to `_ecc` and back to `e3d9437` `_man`, both TOOK. Start and release reboots PASS. The fresh page carried `isr_distinct_ecc` and `isr_undefined_seg` (both 0).
  - **Stimulus, arm plan and stop rule as session 1** (`b6_ecc_run.sh`): 20 arms at leads 7 x 1.5 s, 7 x 5 s and 6 x 8.5 s; 29 pulses at 1000 ms, judged on the first 10; no COM4 console. Run: `runs/b6_ecc_20261020T231941/`; per-arm table `runs/b6_ecc_20261020T231941/session_summary.txt`; tool versions `runs/b6_ecc_20261020T231941/tool_versions.txt`.
  - **Harness changes since session 1, each with its own test** (README rows):
    - `/gi_ecc` is decoded by the TRM table (v1.4 Table 31.4-12: SOF 3; 0, 1, 16, 20, 21 and 29-31 undefined).
    - The first-arm liveness gate (`--ecc-gate`): one distinct ecc over 20 or more rows, or more than half the rows undefined.
    - The dir check: a TEC rise expects TX; otherwise REC above 0 expects RX.
    - The trip 6 judge's latch bound (0.2 s + 0.02 s after the 10th witness error frame).
    - The band refusal.
  - **The gate:** arm 0 read 3 distinct codes, 0 undefined segments and 0 dir disagreements, so the session ran on.
    - 5 other arms read one code (`0xa2`) on all 29 rows: lead 1.5 arms 3 and 5, lead 5 arm 0, lead 8.5 arms 3 and 5. Any of them would have stopped the gate as a first arm.
  - **Result: 18 PASS, 2 FAIL; 2 over-counts** (`b6_multicount_landing.py`, `runs/b6_ecc_20261020T231941/landing.txt`; `b6_ecc_codes.py`, `runs/b6_ecc_20261020T231941/ecc_codes.txt`).
    - **Lead 8.5 arm 1, FAIL, "trip 6 latched before the 10th delivered error frame":**
      - Pulse 7 (pre-latch) has one witness error frame, a `bus_error` delta of 8 and 8 isr rows, spaced 4-6 us apart over 34 us.
      - Their codes: `0xf8` other RX CRC delimiter x2, `0xf1` other RX active error flag x3, `0xf7` other RX error delimiter x3.
      - The rows read TEC 0 throughout, REC 0, 1 and then 9, and status 16 on the first row, 32 on the rest.
      - One ticks row pushed all 8 (`bus_error_count` 14, `err_n` 14) and latched, 307 us after the first.
      - The last frame stamp before the pulse is an `0x051` 5.2 ms earlier; the next is an `0x051` 474 us after the predicted instant.
    - **Lead 5 arm 3, PASS:**
      - Pulse 19 (post-latch) has one witness error frame, a delta of 7 and 7 rows over 30 us.
      - Their codes: `0xf8` x2, `0xf1` x2, `0xf7` x3.
      - The rows read TEC 0, REC 0 then 1, and status 28 then 44.
      - One ticks row pushed all 7.
      - There is no frame within 2 ms of the predicted instant.
    - **Lead 8.5 arm 2, FAIL by the new latch bound:**
      - Pulse 4 has a witness error frame but no isr row, no ticks push and a `bus_error` delta of 0.
      - The device's 10th error was at pulse 11. It latched there, seen 1.077 s after the witness's 10th. A page requested 0.254 s after the witness's 10th (answered at 0.269 s) still showed none.
      - At pulse 4 the last frame stamp is 1.7 ms before the predicted instant, and the next 9.0 ms after.
    - **One pulse missing on both the witness and the device:** lead 5 arm 3 pulse 21 (post-latch). The jammer printed its PULSE line on schedule. The last frame stamp is 4.9 ms before, the next 4.0 ms after.
    - No `0x051` was repeated with the same data less than 400 us apart.
  - **Codes, all 591 isr rows**, decoded from the raw ecc. The page's type, dir and seg agreed with the decode on every row.
    - `0xa2` stuff RX ID28-21: 535;
    - `0xaa` stuff RX data: 22;
    - `0xf7` other RX error delimiter: 6;
    - `0xa8` stuff RX CRC sequence: 5;
    - `0xf1` other RX active error flag: 5;
    - `0xab` stuff RX DLC: 4;
    - `0x0a` bit TX data: 4;
    - `0xf8` other RX CRC delimiter: 4;
    - `0x7a` form RX EOF: 2;
    - `0xa5` stuff RX IDE: 2;
    - `0xa6` stuff RX ID20-18: 1;
    - `0x78` form RX CRC delimiter: 1.
    - No segment was undefined, and the dir check found no disagreement in any arm.
    - **Pre-latch** rows: 204, of which `0xa2` 174 (lead 8.5 arm 1 pulse 7's 8 among them). **Post-latch**: 387, of which `0xa2` 361 (lead 5 arm 3 pulse 19's 7 among them).
    - **The 15 over-count rows** are the `0xf8`, `0xf1` and `0xf7` above, and only they.
  - **The rows that session 1 left open, now with codes** (reviewer's request):
    - **Status 32**, 14 rows. Each `0x0a` row read TEC 8, REC 0; each `0xa5` and `0xaa` row read TEC 0, REC 1.
      - `0x0a` bit TX data: 4, at lead 1.5 arm 1 pulse 25, arm 2 pulse 2 (class), arm 6 pulse 7, and lead 5 arm 3 pulse 1.
      - `0xa5` stuff RX IDE: 2, at lead 1.5 arm 4 pulses 6 and 7 (class).
      - `0xaa` stuff RX data: 1, at lead 8.5 arm 0 pulse 16.
      - Lead 8.5 arm 1 pulse 7's 7 rows after its first.
    - **The 7 landing-class pulses**, all counted once:
      - `0xaa` stuff RX data: 3;
      - `0xa2`: 2;
      - `0x0a` bit TX data: 1, the TEC 8 / status 32 row above;
      - `0xa5` stuff RX IDE: 1.
  - **Per pulse** (`runs/b6_ecc_20261020T231941/landing.txt`, `runs/b6_ecc_20261020T231941/class_count.txt`):
    - 579 pulses placed: a delta of 1 on 576, 7 on one, 8 on one, 0 on one.
    - Class: 7 pulses, all single-count. Neither over-count is in the class: their next frames were 3.93 ms and 0.51 ms after the error frame.
  - **Recording lag** (`runs/b6_ecc_20261020T231941/desk_points.txt`, phase 4; 591 rows, a ticks row that pushed p rows paired with all p):
    - Lag: min 12 us, median 1,816 us, p95 5,357 us, p99 8,342 us, max 13,605 us.
    - Lag minus the gap to the next received frame: at most +2,665 us.
      - Rows over the gap by 0 us: 111; by 1 ms: 2; by 2 ms: 1.
      - The 2 rows over 1 ms (lead 1.5 arm 2 pulse 2, arm 4 pulse 7) each had a next frame 260 us after the error.
  - **The stop rule** (the 3rd over-count, with at least 10 single-count pulses in the class; cap 80 arms) is not met.
    - Over the two sessions: 2 over-counts, both in session 2; 16 single-count class pulses (9 + 7); 40 arms.
    - Whether session 3 runs is with the reviewer.
- **B6 investigation, session 3, on the `_ecc` build of `a55d52c`** (the user's B6 ruling, relayed by the reviewer 2026-10-21: same stimulus, build and gate; stop at the end of the arm where the 3rd over-count lands, or at 80 arms; a device miss is a FAIL, reported beside the over-counts), 2026-10-21 01:46-02:01. Observed, not judged.
  - **How it ran:** by hand with `b6_ecc_run.sh`'s steps, not the script (the reviewer's simpler variant).
    - The steps: custody; `x2_jam_cases.py threshold` calls in session 2's order and arguments, each with `--stop-over-counts 1` added; restore; the SSID redaction.
    - Before custody, before the call and at release, the lock and the paused interposer runner were checked (`runs/b6_ecc_20261021T014654/between_call_check.sh`; outputs in `runs/b6_ecc_20261021T014654/session.txt`). Each time, `bench-holder.md` named tester (gen-inhibit), and the runner (PID 10096) had no child and no other interposer process existed.
    - The first check's helper counted one process: the runner's own `py.exe` launcher (PID 1460, the parent of 10096, created with it at 01:11:21). The later checks exclude it.
    - The one-arm-per-call runner was parked.
  - **Build and OTAs:** the build is session 2's (elf `cec8433d...`). `bench_flash.py` at `3bec89d` with `--no-capture`: to `_ecc` at 01:47 and back to `_man` at 01:59, both TOOK. Start and release reboots PASS. The fresh `/gi_ecc` page had 0 rows and no problems. Tool versions: `runs/b6_ecc_20261021T014654/tool_versions.txt`.
  - **Harness changes since session 2, each with its own test** (README rows):
    - The gate's one-distinct-ecc criterion is dropped; more than half the rows undefined is the only stop.
    - `--stop-over-counts`.
    - `b6_ecc_codes.py`'s per-pulse report.
  - **The stop rule was met after 2 arms** (lead 1.5 s arms 0 and 1, 01:55-01:57).
    - The 3rd over-count overall landed in arm 1, and the call stopped at that arm's end (exit 4, verdict STOPPED). The other 18 planned arms did not run.
    - Over the three sessions: 3 over-counts in 1,212 placed pulses, over 42 arms (`runs/b6_ecc_20261021T014654/desk_points.txt`, phase 3).
  - **The gate:** arm 0 read 3 distinct codes, with 0 undefined segments and 0 dir disagreements, so the session ran on.
  - **Both arms PASS** on the judged first 10 pulses (`runs/b6_ecc_20261021T014654/B6_ecc_n29_lead1.5.txt`).
    - Arm 0: 29 pulses, a delta of 1 on each, 29 isr rows.
    - Arm 1: 27 delivered pulses, 33 isr rows, a delta of 1 on each except pulse 18's 7.
  - **The over-count: lead 1.5 arm 1 pulse 18**, post-latch, one witness error frame (`runs/b6_ecc_20261021T014654/ecc_codes.txt`).
    - **Pattern: fits.** 7 rows by seq: `0xf8` other RX CRC delimiter x2, then `0xf1` other RX active error flag x2, then `0xf7` other RX error delimiter x3. Span 31 us, with rows at +0, 8, 13, 17, 22, 27 and 31 us.
    - **No frame stamp within 0.47 ms of the predicted instant.**
      - Nearest before: an `0x7F3` at -5,722.5 us. Nearest after: an `0x051` at +1,957.7 us.
      - Measured from the witness error frame (-2.4 us from the instant): -5,720.1 and +1,960.0 us.
    - The rows read TEC 0; REC 0, 1, 1, 9, 9, 9, 9; status 28 on the first row and 44 on the rest.
    - One ticks row pushed all 7 (`bus_error_count` 17 to 24).
  - **Session 2's over-counts by the same questions** (`runs/b6_ecc_20261020T231941/ecc_codes_report_20261021.txt`):
    - Lead 5 arm 3 pulse 19: fits, span 30 us. Nearest data frames to the predicted instant: -6,007.0 and +3,892.9 us; none within 0.47 ms.
    - Lead 8.5 arm 1 pulse 7: fits, span 34 us. Nearest: -5,245.9 and +474.0 us (`0x051`); none within 0.47 ms.
  - **Device misses: none in session 3.**
    - Session 2's (lead 8.5 arm 2 pulse 4, a FAIL as recorded) by the new report: no isr row.
    - Nearest data frames to its predicted instant: -1,715.7 and +8,954.3 us (`0x051`).
    - Pulses 3 and 5 each have one `0xa2` stuff RX ID28-21 row (TEC 0, REC 1, status 44).
    - The ticks rows either side: `bus_error_count` 3 (pushed 1) and 4 (pushed 1).
  - **Missing on both the witness and the device:** lead 1.5 arm 1 pulses 22 and 27. The jammer printed both PULSE lines on schedule (`runs/b6_ecc_20261021T014654/desk_points.txt`, phase 1).
    - Pulse 22: last frame 1.9 ms before, next 9.0 ms after.
    - Pulse 27: last frame 0.9 ms before, next 9.0 ms after.
  - **A repeated frame:** one `0x051` was repeated with the same data 250.1 us apart (`080080ff7f02`), 22.2 s before pulse 22's predicted instant.
  - **Codes, all 62 isr rows.** The page's type, dir and seg agreed with the decode on every row.
    - `0xa2` stuff RX ID28-21: 48;
    - `0xf7` other RX error delimiter: 3;
    - `0xa8` stuff RX CRC sequence: 2;
    - `0xaa` stuff RX data: 2;
    - `0x7a` form RX EOF: 2;
    - `0xf8` other RX CRC delimiter: 2;
    - `0xf1` other RX active error flag: 2;
    - `0xa6` stuff RX ID20-18: 1.
    - Pre-latch: 20 rows, all `0xa2`. No row read status 32.
  - **Landing class: two of my desk tools disagree on session 3.**
    - `b6_ecc_codes.py`, via `b6_session1_desk.arm_table`, puts arm 1 pulses 9 and 12 in the class.
    - `b6_session1_desk.py` phase 3, via `b6_multicount_landing.arm_rows`, counts only pulse 12.
    - At pulse 9 a witness `0x051` is stamped at the error frame's own time (+0.0 us), and the next at +250.1 us. One tool takes the tie as the next frame; the other takes the frame strictly after it.
    - Session 2's two counts agreed (7 each). Neither of session 3's class pulses over-counted, and the class condition was met before session 3 (16 pulses).
    - **Resolved 2026-10-21** (reviewer: "the next frame after the error frame" means strictly after). `b6_multicount_landing.near` now takes the first frame strictly after, so `b6_class_count.py` and `b6_session1_desk.py` phase 3 follow it. The change has its own tie fixture and a mutant back to `>=` (README row).
      - Re-judged over every B6 record (`b6_tie_rejudge.py`; 51 records, 1,987 placed pulses): 7 pulses have a frame at the error frame's own stamp, all single-count.
      - Only session 3 arm 1 pulse 9 changes class: it is in under the strict reading (next frame `0x051` +250.1 us). The other 6 have their strict next frame 6.6-16.7 ms later, out of the class either way.
      - No over-count changes class. Per build: `_ecc` 18 class pulses (session 3: 2), none over-counted. `c65c45e` VOID 6 (2 over), `c65c45e` 5 (0 over) and `e3d9437` 14 (1 over), all unchanged.
      - All records: class 43, of which 3 over-counted (`runs/b6_class_count_20261021_strict.txt`). Session 3's desk points again: `runs/b6_ecc_20261021T014654/desk_points_strict_20261021.txt`.
  - **Recording lag** (`runs/b6_ecc_20261021T014654/desk_points.txt`, phase 4; 62 rows):
    - Lag: min 16 us, median 1,158 us, p95 5,075 us, max 6,880 us.
    - Lag minus the gap to the next received frame: at most +258 us. No row is over the gap by 500 us.

### Trip 7 at bus-off (bench, 2026-10-06)

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**Run:** `tec_busoff_sweep.json`, verified by the reviewer from the raw
samples.
- **Build and mode:** `6a515fd-dirty` (the `state`/`tec`/`rec` change before
  its commit as `3373f49`), PASSIVE, so no arm.
- **Stimulus:** a 250 kbit adapter on the 500 kbit segment. Its occupancy is
  the offered rate minus queue-full exceptions, because the witness cannot
  decode it.

| Rung | Offered occupancy | Result |
|---|---|---|
| 0 | 19.9 % | TEC 0 until t=22.17 s, then 103 in one sample with no frame completing across it; fell to 94 while still jammed |
| 1 | 45.6 % | TEC 201, then `bus_off` from t=0.88 s |

**In bus-off,** over 42.1 s (122 samples, through the end of the run):
- `msgs_to_tx` stayed at 1 and `sent_telemetry` stayed at 102;
- `tx_failed`, `tx_refused`, every per-class `refused` and `skipped` all
  stayed 0;
- `bus_error` froze at 656,784;
- `bus_on` read `true`.

The witness saw WiCAN diag 0 against the interposer board's 111 in that
rung, so the silence is the device's.

**What it shows:**
- Neither form of trip 7 occurs at bus-off.
- The controller did not leave bus-off by itself; nothing in `main/` calls
  `twai_initiate_recovery()`.
- A mode change reinstalled the driver: `post_run_recovery_check`, OBSERVE,
  `running`, TEC/REC 0.

**Not shown:** whether trip 2 (`GI_ABORT_BUS_LOST`) catches bus-off on a
live arm. The armed run is approved and not yet done.

**Auto-arm build: boot-to-live on SW boots -- tester, 2026-10-12
17:29-17:58, on `dace947_auto`. Result: live 0.44-0.63 s after an SW boot,
in five boots with every replayed frame found; the first device `0x051`
after each answers the VCM's newest command. Condition 5 is from `f0d25e5`
(2026-10-10) and was not re-run.** The 12.4 auto-arm row; the spec 13 line
"auto-arm: the boot-to-live time is not established". These are SW-boot
figures only (reviewer's ruling). The power-on figure needs the 12 V supply
cut, is parked for the user, and is not carried over from this. A restart
timed to a key-on is the 12.4 entry "Auto-arm, restart timed to a key-on".
- **Image:** `wican-fw_obd_dace947_auto.bin`, 1,277,344 bytes, md5
  `a3f14e8cd77ca7ddbb108b0c4b7a9f0c`.
  - Built by the tester from a clean tree at `dace947` (`git status` empty)
    with `-DGEN_INHIBIT_AUTOARM=GEN_INHIBIT_INHIBIT`. CMake printed
    "gen_inhibit: AUTO-ARM build, mode GEN_INHIBIT_INHIBIT"
    (`runs/build_autoarm_dace947_20261012.txt`).
  - It reports `git_version` `dace947`, like the manual image. It showed
    itself by reading mode 3 at the first read after each boot, with nobody
    arming it.
  - It was on the WiCAN from about 17:30 to 18:17 only, under the bench lock
    with the hazard noted in `bench-holder.md`.
  - Restored to `dace947` `_man` (md5 `babaa13e...`) at 18:17 by OTA, after
    a deliberate mode 0 (`runs/bench_flash_restore_dace947_20261012T1817.txt`).
    Confirmed on the wire: it booted OFF (mode 0, `drv` null at uptime 22 s),
    and in mode 0 it sent nothing to 1,500 replayed `0x051`
    (`runs/restore_unarmed_check_20261012T181806.json`, PASS; harness
    `restore_unarmed_check.py`, tested by `test_restore_unarmed_check.py`,
    13 checks, 8 mutants).
- **Harness:** `autoarm_boot_to_live.py`, tested by
  `test_autoarm_boot_to_live.py` (38 checks, 23 mutants all caught;
  `runs/test_autoarm_boot_to_live_20261012.txt`). Each cycle:
  - the PCAN replays a window and the Kvaser witnesses;
  - the device restarts (reset reason SW): either by OTA (`bench_flash.py
    --flash`, mode 0 first, since OTA is refused while armed), or by a
    `/system_reboot` POST timed so the restart lands at a set replay time
    (`--reboot-at`);
  - boot = the median of (status read time - `uptime_ms`) over the reads
    after the restart;
  - live = the first device `0x051` by provenance (queue order), judged by
    `golive_stale_check.py` against the VCM's newest command;
  - the witness clock against the host epoch is measured from the replayed
    frames' send times against their wire times. Boot-to-live is good to
    about +/-20 ms.
  - **Changed 2026-10-12, after the PCAN went bus-off at a restart** (see
    the key-on entry): the PCAN is opened with `auto_reset`, send failures
    are recorded, provenance is judged against the frames whose send
    succeeded, and the witness's error frames are recorded.
- **Boot-to-live, SW boot** (`scottsvalley_armable_300s.log`):

| Run | Restart | Boot -> first device `0x051` | Device `0x051` after boot | Provenance, first frame |
|---|---|---|---|---|
| `autoarm_20261012T172934` | OTA from `dace947` `_man`, mode 0 | 0.583 s | 3,128 | as specified |
| `..._173119` | `/system_reboot`, 20 s in | 0.625 s | 2,404 | as specified |
| `..._173209` | `/system_reboot`, 20 s in | 0.629 s | 2,404 | as specified |
| `..._173349` | `/system_reboot`, 20 s in | 0.436 s | 2,421 | as specified |
| `..._175702` | `/system_reboot`, 20 s in | 0.570 s | 2,404 | as specified |
| `..._173300` | `/system_reboot`, 20 s in | -- | -- | UNRESOLVED: 47,435 of 106,016 replayed frames found; the replay stopped at the restart (PCAN bus-off, before the change above) |

- **No stale frame at any boot.** "As specified": every replayed frame was
  found in order, and no device `0x051` after the boot carries a counter the
  VCM had already passed. On `f0d25e5` (2026-10-10) one of three OTA boots
  went live with a stale inhibit frame (`runs/autoarm_20261010T193344.json`).
  The same signature has not appeared on `dace947`, here or in the key-on
  runs.
- **Condition 5, on `f0d25e5` (2026-10-10; not re-run on `dace947`)**
  (`runs/autoarm_20261010T193540.json`):
  - `truck_passive2.log` 0-70 s, in which the VCM requests the generator (B0
    0x0B) from 5.7 s to the end. The OTA restart landed inside the request
    (boot at about 29 s in).
  - The device came up armed (mode 3, reset reason SW), never went live (no
    read with `inhibit_live`), and sent **0** device `0x051` after the boot,
    by provenance with every replayed frame found. **As specified.**
  - The block reason during the request was not recorded: the harness kept
    only the first and last reads. It now keeps every read.
  - On `dace947` the key-on restart at +9 s landed after the request and
    never went live. The +7 s restart landed 0.39-0.41 s before the request,
    still booting when it came, and also never went live (12.4 key-on
    entry).
- **Not covered:** power-on straight into the gate, and the wait for the
  contactors (a cold, relay-switched power-on). Both are parked with the
  POWERON figure for the user.

**Auto-arm deadline: how soon the truck asks for the engine after a key-on --
tester, 2026-10-10, corpus sweep (no hardware). Reviewed by the reviewer
before this entry; the margin against the device's boot-to-live is left to
the reviewer and the user.** Reviewer's request, approved by the user. The
worst case in question: the truck fully off (the WiCAN's relay off), keyed on
without unlocking, so the WiCAN boots at key-on.
- **Method:** `autoarm_deadline_sweep.py`, run on `madhouse-debian` over 2,231
  captures (`projects/vtrux/logs/*.log` and `CANLogs/VtruxLogs/*.log`, the
  rig sessions of `data-sources.md` excluded); 1 parse error. Outputs:
  `autoarm_deadline_sweep.txt`, `.json`, `.csv` (one row per wake) and
  `_keyons.csv` (one row per key-on edge).
  - **PT bus by content**, per channel and per capture: `0x051` at about
    100 Hz, at least 2 PT-exclusive ids (`0x592`-`0x5A2`, `0x639`-`0x64B`),
    and no Vehicle or OBD anchor. Found in 1,205 captures. Every count below
    is on the PT bus.
  - **Decoded with cantools:**
    - key: `EPRI_HCU_Sensor_0592` `IgnitionKeyState`;
    - engine request: `VCM_GenControl_0051` `gen_rpm_ref` >= 0 (leaving its
      engine-off null) or `gen_torque_cmd` != 0
      (`vtrux-powertrain-experimental.dbc`);
    - engine turning: `EPRI_MCU_Cmd2_Response_0054` `GENE_RotSpd` >= 300 rpm
      (`epri-pt-bus.dbc`);
    - SoC-valid marker (spec 6.2): `0x440` `bcm_mainc_stat` 11/12.
  - Read raw, because no DBC defines them: `0x617` B7 and `0x051` B0, as the
    spec names them.
  - **Gate on values** = spec 7 arm gate 1-6 plus key on.
  - Each series is sorted per channel before gaps are taken. A wake is the
    start of a PT activity run after a gap of 2 s (200 periods of `0x051`).
  - **"From key-on" times** are to the first event at or after the run's
    first `0x592` 0 -> 1 edge.
- **Population.** 299 PT runs measured, 282 after removing duplicates (the
  same drive under several names: `debuglog_M0`, `debuglog_M0_T0`,
  `debuglog_M0_T0_half` and `oghighwaydrive` are one wake).
  - Excluded and counted:
    - 868 `middrive`: no logger evidence, and the first `0x440` reads closed;
    - 220 `running`: GENE >= 300 rpm within 1 s of the "wake", i.e. a logger
      resume;
    - 5 `merged`: buses on one channel;
    - 1 `shuffle`: a channel renumbered.
  - Wake classes:
    - **A, logger up before the wake: 12.** Times are from the true wake.
    - **B, wake by content, key 0 at the first `0x592`: 263.** The first
      `0x440` reads open, or there is no BMS. Times are from the first PT
      frame the logger saw, so they are lower bounds on the time from the
      wake. The Android logger re-enumerates at every wake, so the stricter
      evidence (A) is rare.
    - **C, the bus woke with the key already on: 1.** No engine request.
    - **D, no `0x592` in the run: 8.**
  - Key-on edges: 91, 81 after removing duplicates. **20 reached an engine
    request after the edge.**
  - Possible duplicate not removed: `generator-on-then-off.log` and
    `vtrux_20260322_165622_T1.log`, both 37.248 s.
  - **Correction (tester, 2026-10-31): the duplicate is real, and it is not truck
    data.** `vtrux_20260322_165622_T1.log` is the `:vtrux` app's recording of a
    desktop replay of `generator-on-then-off.log`'s powertrain bus
    (`$REVERSE_IT_DIR/android-app/notes/artifacts/vtrux_bench_replay_captures/README.md`,
    quarantined 2026-10-30). Aligned by payload, it is the source's channel 3 frame
    for frame, offset -0.005 s
    (`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/replay_fixture_review/replay_vs_source.txt`). The sweep
    read it twice, from its `CANLogs` copy and its `projects/vtrux/logs` copy (one
    wake after removing duplicates). It also read the 23 other bench-replay captures
    of 2026-03-22 and 2026-04-03: none gave a PT run, and they are all 5 `merged`
    exclusions (`194203_T0`-`T3`, `T2` under both paths). Recomputed from the
    sweep's own CSVs without them, with its percentile method (the published
    figures reproduce exactly from the same CSVs):
    - captures 2,231 -> 2,206; with a PT bus 1,205 -> 1,203;
    - wakes 282 -> 281 after removing duplicates; class B 263 -> 262;
    - key-on edges 91 -> 89, and 81 -> 80 after removing duplicates; `merged` 5 -> 0;
    - **engine request from the key-on edge: n 20 -> 19; min 7.741 s and p5 7.742 s
      unchanged; median 12.256 s -> 7.801 s.** Ten edges at 7.741-7.801 s, and nine
      (not ten) at 16.7-172.7 s;
    - GENE >= 300 rpm from the key-on edge: n 20 -> 19, median 18.36 s -> 17.543 s.
    The five shortest are unchanged. `generator-on-then-off.log` itself stays: it is
    a desktop Kvaser capture of the truck, key on at 8.337 s, request 37.248 s later.
- **Engine request, from the key-on edge (n 20): min 7.741 s, p5 7.742 s,
  median 12.256 s.**
  - Every first request was `gen_torque_cmd` 289-328 with `gen_rpm_ref` still
    -1.
  - Ten edges fall at 7.741-7.801 s; the other ten at 16.7-172.7 s.
  - The five shortest:

| Capture | Request (s from key-on) | Gate on values (s from key-on) | GENE >= 300 rpm (s from key-on) |
|---|---|---|---|
| `vtruxdrivetrainoverheattest.log` | 7.741 | 1.243 | 8.576 |
| `vtruxtrailerpokinglog.log` | 7.742 | 1.240 | 8.538 |
| `vtruxanotherevaplogpart2.log` | 7.753 | 1.250 | 8.541 |
| `vtrux_20260906_173324_T14.log` | 7.761 | 1.266 | 66.087 |
| `vtrux_20260912_171013_T0.log` | 7.783 | 1.284 | 8.813 |

  - `173324_T14`'s 7.761 s matches `keyon_arm_window.py`'s figure for the
    same edge (the sweep's spot check).
- **`0x051` B0 on the ten-edge cluster** (`autoarm_deadline_b0_check.py` /
  `.txt`):
  - In all ten, B0 changes `0x08` -> `0x0B` on the same frame as the first
    non-zero `gen_torque_cmd`. B0 = `0x0B` came before the first non-zero
    torque in **0 of 10**.
  - Before that, B0 steps `0x10` -> `0x08` between -0.07 s and +0.50 s around
    the key-on edge.
- **Engine turning (GENE >= 300 rpm), from key-on (n 20):** min 8.538 s,
  p5 8.541 s, median 18.36 s.
- **From the wake** (class B, lower bounds, n 20): engine request min
  8.233 s, p5 8.235 s, median 29.02 s; turning min 9.036 s. The key-on edge
  came 0.315 s (min) to 3.49 s (median) after the first PT frame (n 76).
- **Gate timing:**
  - Gate on values from key-on (n 71): min 0.0 s (a re-key with the
    contactors already closed), p5 1.214 s, median 1.266 s.
  - Class B, from the first PT frame: `0x051` median 0.138 s; `0x411` and
    `0x440` median 3.2 s, when the BMS appears; contactors closed min
    1.44 s, median 6.39 s.
  - Class A (n 11; contactors already closed at these wakes): all gate
    signals present median 0.158 s.
- **A request before the key-on edge:** `engine_req_before_keyon` is true
  when the run's first engine request, from the start of the run, precedes
  the run's first `0x592` 0 -> 1 edge. One key-on row has it:
  `vtrux_partstruck_testdrive_part2.log`, a `middrive` run with a request at
  85.6 s and a key-on edge at 135.5 s, and no request after that edge. It
  belongs to an earlier key cycle.
  - The summary line "0 wakes" counted measured wakes only, from which
    `middrive` runs are excluded.
  - From-key-on times use events after the edge, so this row adds nothing to
    (4).
- **Caveats** (reviewer):
  - The user's worst case, the bus waking with the key already on and the
    WiCAN's relay off, has **n = 1 and no request**: it is unrepresented.
  - **7.741 s is the minimum observed over 20 edges, mostly curated
    experiment captures, not a bound.**
  - The gate cannot pass before about 1.2 s after key-on (the contactors),
    whatever the WiCAN's boot time.
- **Harness corrections made during the sweep:**
  - From-key-on times first took the run's first event, giving -49.9 s on a
    re-keyed run. They now take the first event after the edge.
  - Duplicate captures are now removed.
  - A first wake test ("another channel had a frame within 0.5 s") admitted a
    logger resume with the engine turning at +12 ms. It was replaced by
    continuous coverage from 1 s before, plus the content classes and the
    `running` exclusion.

**A bus-off ends the device's part until a re-arm or a reboot -- tester,
2026-10-14 08:31-08:40, on `e998385` `_man` and `5c21b5f` `_man`. Result:
AS SPECIFIED on `5c21b5f` (it contains 334c831), 3 of 3 arms. A direct re-arm
after a bus-off brought the controller back, and the device was live at the
first page reading after it. On `e998385`, the build before the change, the
same re-arm left the controller `bus_off` in 3 of 3 arms, as expected for that
build. In no arm of either build did the controller recover without a
re-arm.** Spec 7, by the reviewer's method (2026-10-14).
- **Builds:** the tester's clean-worktree builds, each OTA'd for its run:
  `e998385` `_man` (md5 `34e1b53f...`) and `5c21b5f` `_man` (md5
  `3aced07ed55f97ab5d840280a843a9c3`, `elf_sha256` `72c42a18...`;
  `runs/build_5c21b5f_20261014.json`).
- **Rig:** the PCAN replays the interlock slice with `auto_reset` on, so the
  replay comes back after the jam. Kvaser witness in normal mode. WiCAN on
  12 V only. **X2: the jammer `a89ece8c`** (rule 6). It was flashed after a
  PASS dump and restored from that dump afterwards (bench-holder, X2
  section).
- **Method:** `x2_jam_cases.py busoff-rearm`. Each arm runs:
  1. replay, then mode 3, then wait for live;
  2. the jammer's TRAIN (20 us every 200 us for 3 s) puts the controller
     bus-off;
  3. 30 s with no arm;
  4. a direct POST mode=3, its reply kept whole by `raw_http_probe`;
  5. 30 s;
  6. restore.

  The page is read at 1 Hz, and the arm, abort and latch fields are read on
  either side of the re-arm. The judge is `judge_busoff_rearm`, tested by
  `test_busoff_rearm_judge.py` (31 checks; 16 of 16 mutants killed).
- **A device `0x051`, by content:** a run of n adjacent identical `0x051`
  payloads holds n - 1 device frames. The device's frame copies the VCM's next
  command, and the replayed slice never repeats a payload in adjacent `0x051`.
  Positive control on the `e998385` run's saved frames: 298-304 device frames
  in the ~3 s live before each jam.

| Build | Arm | Page `bus_off` after the jam starts | No-arm window, 30 s | Re-arm reply | After the re-arm, 30 s | Verdict |
|---|---|---|---|---|---|---|
| `e998385` | 0-2 | 0.84-0.90 s | `bus_off` at all 29 readings; 0 device `0x051` | 200 in 0.3-5.8 ms | `bus_off` at all 29 readings; 0 device `0x051`; replay at 100 VCM `0x051`/s | FAILS AS EXPECTED |
| `5c21b5f` | 0 | 0.84 s | `bus_off` x29; 0 | 200 in 97 ms | `running`, `bus_on` and live at every reading from the first (0.52 s); 2,978 device `0x051`, the first 0.25 s after the POST | AS SPECIFIED |
| `5c21b5f` | 1 | 0.84 s | `bus_off` x29; 0 | 200 in 132 ms | the same from 0.60 s; 2,977; first at 0.24 s | AS SPECIFIED |
| `5c21b5f` | 2 | 0.91 s | `bus_off` x29; 0 | 200 in 109 ms | the same from 0.66 s; 2,980; first at 0.20 s | AS SPECIFIED |

- **Every arm, both builds:**
  - Before the re-arm, the page read `drv` `bus_off`, `bus_on` false, and the
    trip 6 abort latched ("error-frame rate exceeded"). The re-arm cleared the
    latch on both builds.
  - On `e998385` the page then read `arm_block` "no fresh 0x051" with `drv`
    still `bus_off`.
  - Uptime was continuous (no reboot). Each re-arm reply was a complete 200
    with valid JSON.
  - The witness saw 860-870 error frames per arm (the jam).
  - The restore (mode 0, then an arm) brought the controller back to
    `running` on both builds.
- **Void run, 08:23 on `e998385`** (`runs/x2_jam_busoff-rearm_20261014T082307.json`).
  It read NOT AS SPECIFIED, "recovered with no arm", in all three arms, while
  the page read `bus_off` at every poll. The harness's first rule booked any
  `0x051` under 3 ms behind the last as the device's, which gave 11, 87 and
  25 per no-arm window. Harness defect:
  - the slice holds a `0x051` pair 1.9 ms apart in every 12 s loop;
  - the replay thread bunches frames after a stall.

  Its frames were not saved, so it cannot be judged again. The re-runs save
  them. In their no-arm and after windows with the controller `bus_off`, the
  old rule still counts 9-184 (`trail_0x051_*`) where content counts 0.
- **Run files:**
  - `runs/x2_jam_busoff-rearm_20261014T083136.json` (`e998385`) and
    `runs/x2_jam_busoff-rearm_20261014T083635.json` (`5c21b5f`), each with
    `..._witness.json.gz` and a console file
    `runs/x2_jam_busoff_rearm_{e998385_console_rerun,5c21b5f_console}_20261014.txt`;
  - X2 custody: `runs/x2_flash_custody_{dump_20261014T081841,flash-jammer_20261014T082229,restore_20261014T084044}.json`,
    `runs/x2_jammer_id_20261014T082240.json` and
    `runs/x2_identify_{before_jammer_20261014T0818,after_restore_20261014T0841}.txt`.

### Trip 8 counts no transmit failure (host, pre-port batch 1)

**A short `0x051` (DLC < 6) is an abort with no `tx_fail` -- tester,
2026-10-16, on `c6fdab5` (pre-port batch 1). Result: AS SPECIFIED on the host
golden.** Spec 7 trip 8.
- **Method:** the host suite's `short-dlc-0x051` scenario, run from a `git
  archive` of the merge `5575b3b` (`c65c45e`'s golden against `c6fdab5`'s core)
  on madhouse-debian (`runs/baseline_5575b3b/host/host_run.log`).
- **Result:** the only diff in the suite was this scenario, and exactly the two
  lines the change names: the `0x7F2` page at 2.960 s (`3100000001010000` ->
  `3100000000010000`) and FINAL `tx_fail=1` -> `tx_fail=0`. Every other line,
  the abort reason included, is unchanged.
- **Re-blessed** on its own in tester commit `fc1cb4b`
  (`golden/stimulus.sha256` unchanged); the suite then ran 80 passed, 0 failed
  (`runs/baseline_fc1cb4b/host/host_run.log`). Published on `gen-inhibit/main`
  at `65e9446`.
- **Bench half:** B20's `trip8-dlc5` repeats in the full before-port rerun.

### Trip 7, what the device can see of lateness (spec shim, 2026-11-23)

**The decision with frames waiting, and spec 11's probe on the same path -- tester,
2026-11-23, on `cca06f36`. Result: AS SPECIFIED, 46 of 46 verdicts, in RESPOND and in
INHIBIT.** Spec 7 trip 7, "What the device can see of lateness" (changed 2026-11-23,
user, `f1961dcf`), and spec 11, "The probe must use the same code path as the inhibit
frame" (`ab74c3da`). The reviewer's build message asked for RESPOND's probe on the shared
path. Run files: `runs/verdict_hold_cca06f36/spec_shim/` (`probe.txt`,
`probe_judge_test.txt`, `product_mutants.txt`, `mutate.txt`, `summary.txt`).
- **Method:** `probe_test.c` on the mock-HAL shim (`test/gen_inhibit_spec_shim`, harness
  `687a135f`). It builds `main/` as shipped against the implementor's harness, unedited.
  `run_spec_shim.py --local --base cca06f36` ran it on madhouse-debian, gcc 14.2.0, from
  the clean worktree `gen-inhibit-tester-tools-687a135f`: main/'s tree is `cca06f36`'s,
  nothing modified, shared checkout `2026.11.23.3` unmodified.
- **The stimulus.** Each case sends one VCM command N. Our frame for it, the probe or the
  inhibit frame, completes while the worker waits on an empty receive queue. Then a filler
  wakes the worker, with the case's frames behind it:
  - `late`: another frame and N+1;
  - `between`: nothing, and N+1 arrives between the completion read and the count (the
    harness's `ft_deliver_at_count()`);
  - `waiting`: two frames, neither an `0x051`;
  - `empty`: nothing;
  - `queued`: no filler. N+1 is already waiting when the worker takes N, so the
    completion is seen after N+1 is dequeued.
- **Result, RESPOND (the probe):**
  - `late`, `between` and `queued`: the late probe is in neither on-time count (`ontime`
    +0, `ontime_unverified` +0). It is on the wire, credited in `cls.probe.sent`, not
    withdrawn and not a skip. `unverified_max_backlog` does not move. The probe to N+1,
    seen with nothing waiting, is booked on time.
  - `waiting`: on time, with frames waiting (`ontime_unverified` +1), max backlog 2.
  - `empty`: on time (`ontime` +1).
- **Result, INHIBIT:** the same bookings. A frame found late is one skip (`skipped` +1)
  and still went out on the wire. No other case has a skip.
- **Can-fail.**
  - The judge's own test: 108 of 108 rows, each verdict shown to pass and to fail.
  - `mutate.py --local`: 73 of 73 mutants caught, the 22 new judge and case mutants
    included.
  - `product_mutants.py`: the implementor's six trip 7 mutants (T1 to T6, read from
    `test/gen_inhibit_shim/mutate.py`), 6 of 6 caught. The unmutated copy gave 46 PASS.
    T1 and T2 (path (ii)) are caught by `inhibit_queued`. T3 (the count read before the
    completion) is caught as VOID: the arrival between the two reads never happens.
- **Bench half: AS SPECIFIED, B4 and B13 in the full run on `cca06f36`** (bench run `8047`,
  2026-11-25; the 12.4 entry).
  - B4, spec 12.4's row "skipped inhibit frames": 6 of 6 cases PASS. The judge reads the
    row on totals: device withdrawn + late == witness never-on-wire + witness late, and
    device late >= witness late (`skip_method_bench.judge_arm_v2`).
  - B13, RESPOND's probe: the late probes are in neither on-time count, and the witness
    saw each one on the wire (section 11, "The probe on the inhibit frame's path").

## 7.1 Key state

The key-state scenarios pass in the host suite on `f363674` (the 78/78 run
under 12.4 below):

- the synthetic `key-*` scenarios: `key-arms-while-off`, `key-gates-tx`,
  `key-never-seen`, `key-on-clears-abort`, `key-on-clears-disable` and
  `key-stale-not-keyoff`;
- the replays `replay-rekey-short`, `replay-rekey-long` and
  `replay-bus-sleeps`.

The key-on reset of `fb_ever` is covered by `replay-rekey-long` and
`key-on-clears-abort`.

`replay-rekey-short` is the frame-loss fixture. Its source capture lost frames
at the logger: 4,363 non-+1 counter steps in 10,484. When it was first built,
at `eea8f75`, the gate degraded by transmitting less, never by transmitting
wrongly.

**Correction (tester, 2026-10-31): `replay-rekey-short` is not a truck capture, and
its key cycles are not the truck's.** Its source, `vtrux_20260403_194203_T2`, is the
`:vtrux` app's recording of a desktop replayer playing `generator-on-then-off.log`
with `--loop`, all three buses merged onto one channel
(`android-app/notes/artifacts/vtrux_bench_replay_captures/README.md`). The frames
were lost at the replayer (its TX overflowed), not at a logger on the truck. The
source log has one key-on (8.336 s) and one key-off (62.312 s) in its 64.23 s. The
fixture's three key cycles are the loop wrapping: each down time (10.50, 10.26 and
10.25 s) is the source's key-off to its end (1.92 s) plus its start to key-on
(8.34 s), and they recur every 64.24-64.26 s, the source's length
(`artifacts/gen-inhibit/replay_fixture_review/replay_vs_source.txt`). The pass on
`f363674` above stands as a host-suite result on that stimulus. It is not evidence
of rule 2 against real key cycles. The user ruled on 2026-10-30, through the
reviewer, to retire the fixture. On 2026-10-31 it was instead kept as the
frame-loss fixture, labelled a bench replay (user, 2026-10-31).

## 9.1 The rollback gate

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**Rollback rehearsal on the WiCAN itself, 2026-10-04, on `b492dee`.** Run by
the implementing session with `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/ota_rollback_rehearsal.py`,
and verified by the reviewer from the run files.

| Phase | Run file | Result |
|---|---|---|
| Images | `rollback_images.json` | good `build/wican-fw_obd_b492dee_man.bin`, md5 `789c7bafd71f3d947bdaa4dff6acd639`. Fault `build-otafault/wican-fw_obd_b492dee_man_flt.bin`, md5 `37077ef0d8931bb5e717030367d2dea0`, which carries the fault string and the `_flt` name |
| Code identity | -- | `b492dee_man` against `f363674_man`: 91 bytes differ, all in the accepted regions. `b492dee`'s change to `ota_health.c` is comments only, and its build plumbing is inert at mask 0 |
| Control | `rollback_control.json` | 12/12. The good image was OTA'd to ota_1, where the gate armed on `PENDING_VERIFY` and marked it valid. otadata (`otadata_post_control.bin`): ota_1 seq 2 VALID. **This PASS is a re-judgement of the 23:24 capture.** The first judgement (`rollback_control_20261004_232453.json`) failed four rows asking for OTA-handler log lines, which the firmware cannot print because logging is off after startup. The replacement evidence is the boot slot and the otadata bytes, and neither depends on the log |
| Fault | `rollback_fault.json` | 14/14. The fault image was OTA'd to ota_0. On its first boot the SoftAP's health report (0x01) was masked, while HTTPD (0x02) reported and the console shows the WiFi driver starting in AP mode. Resets came at 15.44 s and 76.02 s (60.6 s apart), and the second boot loaded ota_1 (0x1D0000) as VALID. otadata (`otadata_post_fault.bin`): ota_0 seq 3 INVALID |
| Restore | `rollback_restore.json` | 12/12. The good image is on ota_0, marked valid. otadata (`otadata_final.bin`): ota_0 seq 3 VALID |
| Verify | `rollback_verify.json` | `git_version` b492dee, mode 0, nothing latched. The good image is in both slots, running ota_0 |
| Bootloader on the device | `rollback_bootloader.json`, dump `bootloader_on_device.bin` (2026-10-05) | Byte-identical to this project's `build/bootloader/bootloader.bin`: 21,952 bytes, md5 `532fc5ed12210045be6d20d421181fb6`, with the rest of the region to 0x8000 erased. Every boot in the rehearsal logs announces `ESP-IDF v5.4.1 2nd stage bootloader, compile time Sep 27 2026 14:14:55`, which is that build. `idf.py flash` writes the bootloader at 0x0 (`flasher_args.json`), and this unit has been serially flashed. **So the rehearsal exercised the project's bootloader, not MeatPi's** (checked by the reviewer: dump md5 and erased tail) |
| E3 refuses the rehearsal image | `ba0e644` | Run by the reviewer: the good image passes "no OTA fault injection" and "not a rehearsal build", and the `_flt` image fails both |

**No log line marks a rollback.** `main/main.c:623` (stock, meatpiHQ
`c941591e`) turns all logging off at the end of `app_main`, before the gate
resolves. `console_liveness.json` and `console_liveness_dtr.json` show zero
console lines after boot. The user ruled this a bench nuisance, not a truck
risk (2026-10-05). The after-update check is bring-up row 1.5b.

## 9.2 The OTA upload path

**Items 4-6 (shared/wican W12-W14): tester, 2026-11-23, on `0a740ce6`
`_man`, whose shared/wican is tag `2026.11.23` (`8e16cd74`). Result: AS
SPECIFIED. All three pass on the host, and W13 and W14 also pass on the
bench. W12's bench cases and the trailing-boundary stall are recorded, not
judged.** This is stage (b) batch 1 (the gen-inhibit reviewer's batch, the
user's go 2026-11-23). Run files are in `runs/stage_b1_0a740ce6/`, listed in
its `README.md`.
- **Image:** built by the tester from a clean worktree at `0a740ce6`
  (`host/build_man_0a740ce6.txt`): 1,013,776 bytes, version `0a740ce6`,
  elf_sha256 `e1975c09...`, md5 `04b26838116089f7d0c388639ea7cfc9`.
- **X2:** `c4a553bb`, as the interposer tester read it off the wire at the
  bench release before this one. It was not touched, and no CAN traffic is
  part of these tests.
- **Harness:** `gen-inhibit/tester/stage-b1` at `9f315a13`.
  `test_ota_failpaths_bench.py` is at `d5ed88c4`, which fixes a fault in
  that test's own server.
- **On the host** (`gen-inhibit/test/wican_ota_host/run_ota_host.py`):
  - **How it works:** the upload handler, the one function in the pinned
    `config_server.c` that calls `esp_ota_begin()`, is compiled against a
    fake ESP-IDF with ASan and UBSan. The fake's `httpd_req_recv` is
    scripted. The harness runs 17 cases, each in its own process.
  - **Disclosure:** the fake was plumbed from a reading of the handler's
    source, as told to the reviewer. The cases come from the spec's
    wording.
  - **Why W12 is judged here:** a leaked update session is about a 40-byte
    heap entry, which no client can see.
  - Results (`host/ota_host_2026.11.23.txt`): 36 rows, 0 failing, 1
    recorded.
    - **W14:** `esp_ota_begin` gets `OTA_WITH_SEQUENTIAL_WRITES` in all 17
      cases.
    - **W12:** no session is open at return on any exit except the restart.
      That covers 13 failing cases: reset, close or an invalid socket
      mid-body, first read or trailer; write, end or set-boot refusing;
      stalls; and begin refused.
    - **W13:** a mid-body stall and a first-read stall each return after 4
      timeouts in a row (4 in all). Spaced timeouts (3 in a row; 3 in a row
      four times over; 2 in a row twice) complete with the image written
      exactly.
    - **Recorded, not judged** (reviewer's ruling, 2026-11-23): the stall at
      the trailing boundary drops after 1 timeout with a 500, and no session
      is left open.
  - **Can-fail** (`host/ota_host_2026.11.04_canfail.txt`): the same harness
    on the code before the fix (tag `2026.11.04`) FAILs W12, W13 and W14,
    24 rows. Its begin size is `OTA_SIZE_UNKNOWN`. It leaves 5 sessions open
    (receive failures). It drops a stall only after 21 timeouts in a row.
  - The harness's own test (`host/test_run_ota_host.txt`) passes (47
    checks). Of its 8 source mutants, 7 each fail only their own
    requirement. The eighth, a parked write pointer (item 2), is VOID by
    the control. 5 judge mutants each change a verdict.
- **On the bench** (`gen-inhibit/tools/bench/ota_failpaths_bench.py`,
  `bench_20261123T181229/`):
  - **Setup:** a plain client POST over the phone's route to the hotspot.
    TCP_INFO is sampled during the upload: bytes acknowledged, the window
    the device offers, and the time since the last ACK.
  - **Limits:** the W13 band is 18-23 s and the W14 limit is 2 s. Both are
    the tester's, accepted by the reviewer 2026-11-23.

  | Kind | W13 | W14: longest ACK stall with bytes outstanding | After |
  |---|---|---|---|
  | `stall` (stops mid-body) | PASS: dropped 20.22 s after the last chunk was ACKed, by `408` | 0.161 s | same image, uptime continuous, mode 0, worker idle |
  | `restart` (3 gaps of 2 timeouts, a chunk after each, then a stall) | PASS: no end in the gaps; then dropped 19.80 s after the last chunk, by `408` | 0.262 s | the same |
  | `fin` (client closes mid-body) | -- | 0.161 s | the same; W12 recorded: the device replied `500` |
  | `rst` (client resets mid-body) | -- | 0.121 s | the same; W12 recorded |
  | `full` (the whole image) | -- | 0.182 s | `303`, restarted on the same image (uptime 7,462 ms) |

  - Every kind saw the device offer a zero window at some point. This is
    recorded, not judged: W14 is judged on ACK stalls.
  - W12's consequence on the bench: after the `fin` and `rst` uploads
    failed, the `full` upload of the whole image took.
  - The probe's own test (`host/test_ota_failpaths_bench.txt`, at
    `d5ed88c4`) runs on loopback with time scaled. Each kind passes on a
    server that behaves as the spec says and fails where it should. 7 of 7
    mutants are killed.
  - **The fault fixed by `d5ed88c4`:** that test's server missed a
    multipart trailer split across two reads, so its `full` case failed now
    and then. The files from before the fix are kept in `host/`. The test
    with the old matching fails the new split-trailer check, which is the
    can-fail.

**shared/wican W16, the multipart start read within what was received:
tester, 2026-11-23, on `db998aca`, whose shared/wican is tag
`2026.11.23.3`. Result: AS SPECIFIED on the host, for both upload handlers.
The code before the fix fails it (the can-fail).** Spec 9.2 makes the stock
upload the interface and item 1 keeps every read inside the receive buffer;
W16 adds that the multipart start is looked for only within the bytes
received. This is stage (b) batch 2 (the gen-inhibit reviewer's batch, the
user's go 2026-11-23). Run files are in `runs/stage_b2_db998aca/host/`, listed
in its `README.md`.
- **Harness:** `gen-inhibit/test/wican_upload_host/` (merged in
  `gen-inhibit/main` `e6d818ef`), run at `gen-inhibit/tester/stage-b2`
  `4126f144`, clean. Both handlers, the OTA upload's `upload_post_body()` and
  `/upload/car_data.json`'s `upload_car_data_handler()`, are taken from the
  pinned `config_server.c` and compiled against a fake ESP-IDF with ASan and
  UBSan. The fake's receive is scripted and poisons the buffer past each
  receive, so a read past the bytes received is an ASan error. 13 cases a
  handler: the start split at five points (in and after the boundary line,
  in the `Content-Type` header, before and in the blank line), trickled 1
  and 3 bytes at a time, no boundary line, no `Content-Type`, no blank line,
  a body that ends early, and a long start, plain and trickled.
- **On the pinned code** (`upload_host_pinned.txt`, the default, the checkout
  of `2026.11.23.3`): W16 PASS, W12 PASS, 38 rows, 0 failing. Every split
  and trickled upload is accepted with the image or file written exactly;
  each start lacking a part is refused with 400, nothing opened or written,
  and the OTA refusal leaves no update session open (W12). `unlink` calls on
  `car_data.json`'s refusals are recorded, not judged.
- **Can-fail** (`upload_host_canfail.txt`, tag `2026.11.23`, before the
  fix): W16 FAIL, 26 of 28 rows. Reads past the received bytes (ASan) on
  both handlers, and `car_data.json` accepting a split start with the file 1
  or 2 bytes too long.
- **The harness's own test** (`upload_host_test.txt`, on its default tags
  `2026.11.23.2` and `2026.11.23`; `upload_host_test_3.txt`, the same with
  `--wican` the `2026.11.23.3` checkout): 41 checks ok, 0 failed, each. It
  plants 9 handler defects: 8 are caught (stock's 200-byte `memchr` put
  back fails 10 or more rows, each a read past the received bytes), and the
  ninth, the data offset one short, is the control that must come out VOID;
  6 judge mutants each change a verdict; the handler extraction refuses none
  or two registrations. The test's labels name `2026.11.23.2` whatever
  `--wican` is given; the second file ran on `2026.11.23.3`, as its command
  line shows.

## 9.4 Image budget

**The regression set after the Bluetooth strip: tester, 2026-11-23, on
`0a740ce6` `_man`. Result: the clean boot, the health gate reaching its
decision and the end-to-end OTA PASS. The inhibitor bench is NOT RUN; it
runs with batch 2's full run (user, 2026-11-23).** The strip counts as kept
only once that run passes (reviewer, 2026-11-23). This is stage (b) batch 1.
The image, the X2's state and the harness are as in the 9.2 entry above.
Bluetooth off in the build is in 12.3, "Bluetooth off". Run files are in
`runs/stage_b1_0a740ce6/bench_20261123T181229/`, from
`gen-inhibit/tools/bench/stage_b1_bench.py`, whose own test passes with 5 of
5 mutants killed.
- **End-to-end OTA: PASS, twice.**
  - **First, `gi_ota.py` from `2aac016d`** (`load.txt`, `load.json`): the
    image took, and the device ran it after a software restart. Verdict
    `(a2+c) NO STATUS, RESTARTED + TOOK`.
    - **Observed:** curl sent the whole body (1,014,004 bytes) and got no
      HTTP status. It timed out after 180 s. The device had booted the image
      (reset SW) 14.2 s after the request started; a whole upload takes
      about 15 s (`full` below).
    - In the run files, every earlier real load by this flasher got its
      status (`(c) TOOK`).
    - Spec 9.2 makes a plain curl POST the interface and asks for no
      particular reply, so this is recorded, not judged.
    - The resource record over the load (`resource-log window 1795457550
      1795457745`) is clean: bench.slice at 0.4 cores, no pressure.
  - **Second, `ota_failpaths_bench.py --kind full`, a plain client:**
    `303 See Other`, then restarted on the same image.
- **Clean boot: PASS.** The page answered in 0.07 s after the load: the
  image, mode 0, worker idle, reset SW. This was the image's first boot
  after the OTA, by `load.json`'s boot list.
- **The health gate reaching its decision: PASS.** Spec 9.1 requires a
  decision within the gate's 60 s timeout. It is shown from the client with
  no console:
  - **No rollback restart:** the device was on the image at uptime 171,593
    ms, past 75 s, in its first boot after the OTA (`gate_reads.json`). A
    rollback would have restarted it into `2aac016d`.
  - **Marked valid:** `wican_reboot.py --want` (`gate_reboot.json`). After a
    software restart (uptime 7,047 ms, reset SW) it is still on the image,
    mode 0, nothing latched or disabled. An image left `PENDING_VERIFY`
    would have rolled back at that boot.
  - **Only one read:** the gate's watch got a single read, because the
    load's 180 s curl wait used up the time before 75 s. Uptime is
    continuous from the boot to that read, so no restart happened in
    between.
- **The inhibitor bench: NOT RUN.** It runs with batch 2's full run (user,
  2026-11-23; reviewer's ruling, 2026-11-23). The chain runner's bench
  suite needs an emulated run on the same clean code first, which batch 1
  does not have. *Ran 2026-11-23 with batch 2's full run, on `db998aca`
  (tester): the next entry.*

**The inhibitor bench after the Bluetooth strip, and the OTA-reply count:
tester, 2026-11-23, on `db998aca`. Result: the inhibitor bench rows PASS
(B1, B3, B11, B17a, B18, B21). The full bench run they are part of has two
FAILs, B4 and B6 (12.4, stage (b) batch 2's full run). Every OTA load in the
run got its HTTP status.** The image carries the strip (`CONFIG_BT_ENABLED`
not set, `build_output.py`'s row on the image, 12.3 below). Under the
reviewer's ruling of 2026-11-23, the strip counts as kept only once the full
run passes; this run has B4 and B6 not passing, so the ruling is the
reviewer's to apply. Run: `runs/chain/gen-inhibit-bench_20261123T171226_4b15`,
described in the 12.4 entry.
- **The inhibitor bench** (the rows the reviewer named for 9.4):
  - B1 (3.2, SLCAN refused and counted): PASS, 20 of 20.
  - B3arms and B3 (the go-live batch): PASS, 20 arms, stale 0.
  - B11arms and B11 (quiesce under traffic): PASS, 6 arms as specified,
    `tx_ok` agrees with every INHIBIT span.
  - B17a (the page names the binary): PASS, `elf_sha256` `cd46e234`.
  - B18 (`0x7F3` carries the build id): PASS, "MATCH, 7 rows ok",
    `diag_build_id` `cd46e234`.
  - B21 (9.3, the OTA refusal at the client): PASS, both guards hold; 20 of
    20 multipart and 20 of 20 image requests got the whole 403 with its
    reason.
- **The OTA-reply count** (recorded, not judged; the reviewer's carry-over
  from batch 1, where `gi_ota.py`'s load got no HTTP status): the run made
  six WiCAN OTA loads through the shared flasher, each from its record
  `_bench/<group>/ota_attempt1_*.json`. All six got HTTP `303` with "File
  uploaded successfully" (curl exit 0), and each took at attempt 1 (`(c)
  TOOK`, the new image read back after a software restart). The no-status
  case did not repeat. `bench/ota_reply_count_4b15.txt`.

  | Load (group) | Image | HTTP status | curl `time_total` |
  |---|---|---|---|
  | start (`_man`) | `cd46e234...` | 303 | 12.46 s |
  | rt | `343464da...` | 303 | 18.36 s |
  | txabort | `51ab1fc7...` | 303 | 19.37 s |
  | abortwin | `ad12c4fc...` | 303 | 17.97 s |
  | rxq | `bccf8159...` | 303 | 17.78 s |
  | prod (the stand-in) | `cd46e234...` | 303 | 15.42 s |

## 10 Diagnostics

**Tester, 2026-10-08, on `a71b90a` `_man`** (image md5
`6c76f96f55ce7b928eae1a4a68df4a0a`, built fresh by the tester from a clean
tree at `a71b90a`; E3 `runs/build_output_a71b90a_man_20261008.txt`: 33 ok,
prod record NO REF as spec 12.3 item 3 requires for a bench image, and the
partition-table row compared by hand, byte-identical to stock, because the
check looks only in `build/`). Run file
`runs/wican_segment_probe_20261008T211648.json`; device in mode 3 with no
`0x051` on the bus, so diag only. Read by three adapters independently
(Kvaser, PCAN listen-only, Innomaker), all agreeing. *Corrected 2026-10-09:
this said "Kvaser silent"; the Leaf Light v2 has no silent mode and was ACKing
(section 7, "Who ACKs").*
**Correction (tester, 2026-11-18): the Innomaker read was not independent.**
It was the bench's only Innomaker then, on the X2's CAN2 (today's `canchg`),
and saw CAN1 only through the X2's bridge, not as an adapter on CAN1. The
values rest on the Kvaser and PCAN reads, which decoded every one. Results
unchanged (reviewer ruling, 2026-11-18).

| Check (spec 10; schema doc rev table row 6) | Expected | Observed | Result |
|---|---|---|---|
| `0x7F3` `diag_git_hash` names the build | fnv1a32("a71b90a") = `0x991D4542` | `0x991D4542` (2,568,832,322) | pass |
| `diag_schema_ver` on `0x7F1` and `0x7F3` | 6 | 6 and 6 | pass |
| `diag_fw_ver` on `0x7F1` B1 and `0x7F3` B4-B5 | 6 | 6 and 6 | pass |
| all five pages present | `0x7F1/2/3/8/9` | 3 of each in 6 s | pass |
| `0x7F1` `bus_on` bit1 while the controller runs | 1 | flags `0x06` | pass |

The `bus_on` bit through bus-off is under section 11 below.

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**All five pages on hardware, 2026-10-04, on `f363674` `_man`**
(`diag_check.json`, 21:05:48).
- **Pages:** `0x7F1`/`0x7F2`/`0x7F3`/`0x7F8`/`0x7F9`, seen 3/4/3/3/3 times.
- **Build:** named by HTTP (`f363674`) and on the wire (`0x60EFB786`).
- **Result:** all 10 checks pass, with the stimulus paced at 10 ms. `0x7F9`
  read 0 withdrawn / 0 late, which is reported but not asserted.

**Schema 9 on `db998aca`: the frame table from the DBC, the compile-time
width check, the retired revision, and the tools moved to the schema byte --
tester, 2026-11-23. Result: AS SPECIFIED.** This is stage (b) batch 2
(`gen-inhibit/main` `db1bfeae`, the diag encoder through shared/diag at tag
`2026.11.23.3`; `db998aca` changes only the file map), on the schema of
`d2902fbf` (`DiagSchemaVersion` 9, `DiagSchemaHash` `0c2c4b82529f74e1`). Run
files are in `runs/stage_b2_db998aca/host/`. The image is `cd46e234...`, the
bench `_man` image and the release stand-in alike.
- **The frame table is generated from the DBC:** the committed C and Python
  tables regenerated from the DBC byte for byte with the pinned generator,
  and the emitted frames decoding with cantools, on the bench image and the
  stand-in (12.3, "Item 2, no diag misfit ... and item 3's diag rows").
  shared/diag's packer and generator against cantools: `shared/diag/VERIFICATION.md`.
- **No diag value can overflow its signal: the build checks it, and the
  check can fail** (`host/width_check.txt`, `gen-inhibit/test/diag_host/width_check.py`).
  It compiles `gen_inhibit_core.c` against tables generated from planted
  DBCs, and with the core's own field types planted:
  - the control (the DBC as published) builds;
  - `diag_tx_ok` 32 -> 16 bits, `diag_flags` 8 -> 7 bits, `diag_mode` 8 -> 2
    bits (PASSIVE, 4, no longer fits), `diag_flags` made signed, and the core
    putting `diag_flags` as a `uint16_t` or as an `int8_t`: each fails the
    build on a static assertion naming the signal;
  - `diag_tx_fail` 8 -> 4 bits builds: the core caps that field, as spec 10
    allows.
  A compile error other than the expected assertion is VOID; the judge's
  self-test passes 6 of 6 (`host/width_check_selftest.txt`).
- **The device does nothing of its own about a misfit, and the packer sends
  the field as 0:** the field left 0 and reported is shared/diag D3
  (`shared/diag/VERIFICATION.md`). A misfit fails any host or emulated run
  (12.3 item 2, the entry named above).
- **The hand-kept revision is retired:** `0x7F1` B1 and `0x7F3` B4-B5 sent 0
  in every schema-9 page the host build emitted (1063 judged), and on the
  bench B18 reads the build id at schema 9, "MATCH, 7 rows ok" (12.4, stage
  (b) batch 2's full run). B15's frames on the segment decode against the
  DBC.
- **Tools that gated on `diag_fw_ver` gate on `diag_schema_ver`:**
  - `diag_check_rules.py` (`host/diag_check_rules_test.txt`, 16 checks ok):
    the rule is `schema_ver >= 3` (a schema 2 page fails exactly that row),
    and at schema 9 the rows "`0x7F1` B1 reserved, 0" and "`0x7F3` B4-B5
    reserved, 0" are added, each failing on a nonzero byte; schema 7 and 8
    pages get no reserved rows.
  - `autoarm_bit_offline_test.py` (`host/autoarm_bit_offline_test.txt`): 8 of
    8 cases as intended, on schema-9 fixtures.
  - `gi_diag.py` decodes schema 8 and schema 9 by the schema byte on the
    wire (`host/whole_set_test_gi_diag.txt`, 79 checks ok): the repo's DBC
    history gives schema 7, 8 and 9, with 9 at HEAD (`d2902fb`, hash
    `0c2c4b82529f74e1`); schema 8 and 9 pages in one trace are each decoded
    on their own schema byte's DBC (`diag_fw_ver` at 8,
    `diag_reserved_b1` at 9); the host suite's and B15host's exported copy
    decodes by schema with no git and refuses an unknown schema. Mutants:
    25 of 25 killed, the control passing (`host/gi_diag_mutants.txt`).
  - The build-id tools (`host/whole_set_test_build_id_verdict.txt`, 19
    checks ok; `host/whole_set_test_build_id_page.txt`, 54 ok): the DBC set's
    head is schema 9, which decodes `0x7F3` B0-B3 as `diag_build_id`, the
    same value as schema 8, and names B4-B5 `diag_reserved16`. Mutants of the
    page checks: 12 of 12 (`host/build_id_page_mutants.txt`).
  - `golden_schema_bump_check.py`, with which the 72 goldens were moved from
    schema 8 to 9 (only the schema byte and the retired bytes changed): its
    self-test's mutants 8 of 8 (`host/golden_bump_mutants.txt`).
  - The whole tools test set at `4126f144`
    (`host/whole_set_summary.txt`): 137 tests, each exit 0 except
    `test_only_inert.py` (exit 2: a bench tool, as in every earlier set) and
    `test_gi_host_run_list.py`. That one failed on the test's own reading:
    the shim compiles `shared/diag` since the implementor's `fe8c58e5`, and
    the test looked for it only in the sync's `$SHARED_ROOT` section, while
    `gi_host_run.sh` ships it from its `$DIAG_ROOT` section. Fixed in
    `a830d59a` (the test reads both); it passes there, 11 of 11
    mutants (`host/test_gi_host_run_list_fixed.txt`). The set ran in the
    stage-b2 worktree while these entries were being written, uncommitted.
    Of its tests, `test_verification_manifest.py` and
    `test_verification_move.py` read this document; run again from the clean
    `4126f144` worktree they pass, 33 and 38 checks ok
    (`host/clean_test_verification_*.txt`).

## 11 Status page

**`bus_on` reports whether the controller is running -- tester, 2026-10-07,
on `aaab89a` `_man`.** Judged as the reviewer ruled 2026-10-07: pass/fail on
the page (`bus_on` true exactly when `drv.state` is `running`, false in every
other state and when `drv` is null); on the wire every `0x7F1` is expected to
carry bit1 set, and a bit-1-clear frame would be reported, not failed.
Judge: `tec_busoff_sweep.judge_bus_on()`, tested by
`test_busoff_bus_on_judge.py` (7/7, including four FAIL and one INCONCLUSIVE
case).

| Run | States read on the page | Page vs `drv.state` | Wire `0x7F1` | Verdict |
|---|---|---|---|---|
| `wican_segment_probe_20261007T231958.json` (mode 3, then 0) | running; drv null | agree (2 of 2) | 3 seen, bit1 set on all | pass for running and OFF |
| `tec_busoff_sweep_20261007T232320.json` (PASSIVE, 250 kbit jammer at 20/45/70 % offered) | running 367, drv null 9 | agree on all 376 | 59, bit1 set on all | **INCONCLUSIVE**: bus-off not reached |
| `tec_busoff_sweep_20261007T232533.json` (same, 90/98 % offered) | running 336, drv null 9 | agree on all 345 | 44, bit1 set on all | **INCONCLUSIVE**: bus-off not reached |

- **Not yet shown:** `bus_on` false while `drv.state` reads `bus_off` or
  `recovering`. Neither sweep reached bus-off: TEC peaked at 159 in the first
  (past error-passive, then pinned) and 7 in the second.
- **The jammer did not deliver the rate it was offered.** The witness counted
  8,200-8,620 error frames per 25 s rung at every rung from 20 % to 98 %
  offered (10,840 to 53,039 frames offered). The 2026-10-06 implementor run
  reached bus-off at the 45 % rung with the same script; today's did not.
  Reported as observed; the cause is not established.
- **Arm state in both sweeps:** never live, `arm_block` "no fresh 0x051"
  (no `0x051` on the bus), no abort latched. So trip 6 was not expected to
  latch, and these runs say nothing about trip 6 in PASSIVE (spec 3.1).

**The bus-off half, with the X2 jammer -- tester, 2026-10-08, on `aaab89a`
`_man`. Result: PASS, 3 of 3 arms; control clean.** This is case A of the
reviewer's step-9 plan, approved 2026-10-08.
- **Harness:** `x2_jam_cases.py busoff --arms 3 --control`; run file
  `runs/x2_jam_busoff_20261008T013627.json`. Judged per arm with
  `tec_busoff_sweep.judge_bus_on()`, the same judge and rules as the table
  above. The case judge is tested by `test_x2_jam_cases.py`.
- **Jammer:** the X2 jammer on CAN1 (`x2_jammer/`, test equipment, image
  md5 `a89ece8c...`), commissioned the same night (`x2_jammer/README.md`).
- **Conditions:**
  - WiCAN in RESPOND (mode 2), no replay and the PCAN closed, so the
    device's only transmissions were its diag pages.
  - Kvaser witness in normal mode.
  - Per arm: 5 s baseline, then `TRAIN 20 200 3000` (a 20 us pulse every
    200 us for 3 s), then 15 s more. The page was polled at ~10 Hz and
    `0x7F1` captured off the wire.
  - **2026-11-20, tester: B8's baseline from harness branch
    gen-inhibit/tester/runc-rulings on.** At least 5 s, and until the Kvaser
    witness's stamp check (SocketCAN, canbench's StampCheck) has left pending
    with at least 20 data frames judged, at most 15 s; past 15 s the run is
    VOID on its witness clock and no train is sent (the gen-inhibit
    reviewer's ruling of 2026-11-20, item 4(iii); `x2_jam_cases.wait_baseline`,
    the 15 s bound in the madhouse test plan, section 4b). The arms above,
    and every B8 run before this, had the fixed 5 s.

| Arm | Page states | Page vs `drv.state` | Reached bus-off | At first `bus_off` reading |
|---|---|---|---|---|
| 0 | running 41, bus_off 145 | agree on all 186 | +0.075 s after the train started | TEC 128, REC 0, `bus_error` 222 |
| 1 | running 40, bus_off 145 | agree on all 185 | +0.023 s | TEC 128, REC 0, `bus_error` 133 |
| 2 | running 40, bus_off 144 | agree on all 184 | +0.049 s | TEC 128, REC 0, `bus_error` 124 |
| control (no train) | running 182 | agree on all 182 | never | -- |

- **`bus_on` read false on every `bus_off` reading**, and true on every
  `running` one. The control arm read only `running`, with `bus_on` true,
  throughout.
- **Wire:** 3 `0x7F1` per arm, all before bus-off, bit1 set on all. There
  was no `0x7F1` after bus-off, because a bus-off controller transmits
  nothing.
- **Recovery, recorded and not judged** (no spec section governs it; it is
  with the user, reviewer 2026-10-08). In all 3 arms the device read
  `bus_off` for the whole 15 s after the train ended, until the harness
  posted mode 0. Then `drv` was null.
- The "Not yet shown" bullet above is now shown.

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**`drv.state`, `tec` and `rec`** (2026-10-06, `tecrec_status_page.json`):
- **Build:** measured on `6a515fd-dirty` (image md5 `6d72c0e1...`). The same
  diff was committed as `3373f49`. No byte comparison ties `3373f49`'s
  image to the measured one.
- **In OBSERVE:** the page read `state` `running`, TEC 0, REC 0.
- **Size:** 1,994 bytes live; 2,793 bytes with every integer at u32 width,
  against a 3,072-byte cap. That leaves **278 bytes of headroom**, about
  three more u32 fields.

**Uptime and reset reason are reported -- tester, 2026-10-09/10, on
`cc34c80` `_man`. Result: as specified on every point tested.**
- **Image:** `wican-fw_obd_cc34c80_man.bin`, 1,275,920 bytes, md5
  `4233f387a13460af43bbf454a4064b39` (built by the implementor; the md5 was
  checked by the reviewer and by `bench_flash.py`'s preflight). OTA by
  `bench_flash.py --flash`, 2026-10-09 19:18; it reported the device running
  `cc34c80`, status page 51 fields (`runs/bench_flash_cc34c80_20261009T1919.txt`).
- **Supply:** 12 V on OBD pin 16 with ground on pins 4-5, and USB unplugged,
  except where the forced resets below say otherwise.
- *Condition added 2026-10-13, corrected 2026-10-13 (reviewer):* CAN1 also
  carried the X2's unacknowledged `0x7F4` retries: 3,697 frames/s on the wire,
  all `0x7F4`, 0 error frames (tester, `runs/bench_census_20261013T232011`, X2
  on `7f197bfd`). The X2 counts them as transmit errors (~3.6k/s, interposer
  report 2026-10-13). The X2 was read off the wire as `7f197bfd` before and
  after (`runs/x2_identify_after_restore_20261009T09.txt`, 09:49, and
  `runs/x2_identify_before_hold_20261010T1659.txt`), and the bench lock
  records no X2 change between; no CAN adapter was open. The WiCAN's CAN
  driver was not running: `drv` null on every answered poll of every run here
  (20, 120, 5 and 1,476), and the bench lock records only page-only runs in
  the 21 h between them, so the results do not depend on the bus traffic.
  *First written as "`0x7F4`-`0x7F7` retries (~3.6k error frames/s)", from the
  interposer report alone; the wire reading showed one ID and no error
  frames.*
- **Harness:** `page_only_baseline.py` polls `/gen_inhibit` once a second.
  - A reboot is an `uptime_ms` drop.
  - BEHIND/AHEAD flag uptime moving off the wall clock by more than the two
    requests' durations allow.
  - It checks the form of both fields, and that the reason does not change
    within a boot.
  - The rate against the wall clock is taken from short requests only.
  - It compares the stall detector with the uptime.
  - `--build-check` reads `git_version` at the start and the end.
  - Tested by `test_page_only_baseline.py`: 35 checks, 8 mutants caught
    (`runs/test_page_only_baseline_uptime_20261010.txt`).

| Run | What it shows |
|---|---|
| `page_only_baseline_20261009T191750` (`a71b90a`, before the OTA) | control: the fields are MISSING on 20 of 20 answered polls |
| `page_only_baseline_20261009T191859` (from 14.7 s after the OTA boot, 120 s) | `git_version` `cc34c80` at the start and at uptime 133.8 s, with no reboot between: **bring-up 1.5b**. Both fields on every poll, `uptime_ms` a JSON integer and `reset_reason` a string; reason **SW** after the OTA restart; rate 1.000 over 119 s |
| `page_only_baseline_20261010T162721` (21 h later) | `uptime_ms` 76,116,389 at 16:27:21 on 10-10, which is the 19:18:44 boot: **no reboot in the 21 h**, reason still SW. Uptime is cumulative, so this one reading covers the whole window with no gap between polls (reviewer: no separate long idle baseline is needed). The laptop's WLAN had dropped off the SoftAP meanwhile (`..._162649`); the device's uptime says it stayed up |
| `page_only_baseline_20261010T162757` (25 min) | the two forced resets below; afterwards 1,375 polls with no reboot, reason POWERON throughout |

- **Forced resets.** The user did the cable steps at the bench at 16:2x on
  2026-10-10; no unattended process did.
  - **USB-JTAG reset**, with USB plugged back in for it:
    - pulse at 16:28:26 (t = 29.0 s); `uptime_ms` 76,180,097 -> 7,073,
      `reset_reason` **USB** (was SW);
    - the ROM banner on COM4 read `rst:0x15 (USB_UART_CHIP_RESET)`, one boot,
      52 ms after the pulse (`runs/usb_jtag_reset_cc34c80_20261010.json`).
    - USB reads USB, as recorded in `firmware/bench-holder.md` from the diag
      work.
  - **12 V cut**, about 5 s, with USB unplugged:
    - `uptime_ms` 77,257 -> 9,681 at t = 124.7 s, `reset_reason` **POWERON**
      (was USB).
    - The boot was at about 16:29:52, inside the cut.
  - **The unplug** (COM4 gone at 75.2 s) had no outage at it.
- **Stall detector against uptime:** they agree. Each reboot falls inside the
  stall detector's one outage (a 7.0 s poll at the USB reset; 7 polls at the
  power cut), and there is no outage that was not a reboot.
- **Rate against the wall clock**, per boot (`..._162757_rejudge.json`): SW
  1.00011, USB 0.99998, POWERON 0.99999.
- **Page size:** the live page was 1,876 bytes in mode 0 (`drv` null) on
  2026-10-10 16:57. That is a spot reading only. The worst-case size against
  the 3,072-byte cap was not measured here.
- **Not tested:** an unrecognised `esp_reset_reason()` value (reported as its
  number written as a string, e.g. `"99"`; reviewer's reading, which the
  harness checks the form of) cannot be produced on the hardware. `PANIC`
  cannot be reached on this configuration either, because a panic halts the
  chip (implementor).
- **Harness defects found on the bench, both fixed with a test:**
  - With no answered poll, the verdict read "no reboot". That came from a run
    where the laptop was off the SoftAP. It now reads NO UPTIME READINGS.
  - The rate dated a reading to the middle of its request. The first reading
    after a reset comes from a 7 s stalled request that was answered at its
    end, so the USB boot's rate read 0.952. The run files keep the first
    version's figures; the `_rejudge.json` files beside them hold the fixed
    ones.


**Status page fields added 2026-10-12 -- tester, 2026-10-12, on `dace947`
`_man`. Result: AS SPECIFIED for `reset_reason`'s form, the on-time counts
in RESPOND and PASSIVE (the RESPOND late-probe balance included), and
`would_tx` restarting on every arm. Two parts
are not shown: OBSERVE's on-time counts and an unrecognised reset reason.**
Spec 11 (`reset_reason` always a string; "Transmit scheduler counters";
"Refused transmits are counted"; "Trip 6's error history is reported") and
spec 3.1 (`would_tx` resets on every arm, `0x7F8` B3-B6, `diag_schema_ver`
7).
- **`reset_reason` is a string.** All 120 polls of the 1.5b baseline
  (`runs/page_only_baseline_20261012T170310.json`) read the JSON string
  `"SW"`, and so did all 7,041 status reads in the 30 auto-arm runs, across
  their OTA and `/system_reboot` restarts. **Not tested:** an unrecognised `esp_reset_reason()`
  value, which spec 11 reports as its number in a string (`"17"`). The bench
  cannot produce one on demand.
- **`refused_slcan`, `refused_can_send`:** spec 3.2 entry.
- **`err_n`, `err_age_ms`, `err_burst`:** 12.4 entry "Trip 6 before going
  live, after a re-arm".
- **On-time counts by mode.** `respond_booking_check.py`, tested by
  `test_respond_booking_check.py` (29 checks, 25 mutants all caught;
  `runs/test_respond_booking_check_20261012.txt`). It judges the status page
  each e4 run records, by the mode the page reports
  (`runs/respond_booking_check_dace947_20261012.txt`):
  - **RESPOND** (`runs/e4_respond_dace947_20261012T170807.json`, Scotts
    Valley 0-35 s, offset 500 us): `ontime` 3,494, `ontime_unverified` 0,
    `unverified_max_backlog` 0, against 3,496 probes sent. **PASS.**
  - **The balance: PASS** (judged since the user published the `0x7F0`
    layout, 2026-10-12). Reviewer's reading (2026-10-12): a probe completing
    after the VCM's next `0x051` is neither on time nor unverified, so the
    unbooked probes should equal the late probes on the wire. Here 2 probes
    are unbooked, and the witness shows 2 late probes, out of 3,496 judged,
    with none malformed.
    - "Late" is from `wican_diag_schema.md`, "`0x7F0`: the RESPOND timing
      probe": `probe_vcm_ctr` (B6) is the counter of the `0x051` the probe
      answers. A probe is late when that is older than the newest `0x051`
      counter on the wire when it appears.
    - B6 and the `0x5A` marker are decoded with cantools from the DBC's
      `WICAN_Probe`. A probe that is not 8 bytes, has another marker, or has
      a counter outside 0-15 is counted as malformed and never judged.
    - `respond_booking_check.py`, `judge_balance()` and the DBC-decoded
      `late_probes()`; 7 mutants, all caught
      (`runs/test_respond_booking_check_20261012b.txt`). Result in
      `runs/respond_booking_check_dace947_20261012b.txt`.
  - **PASSIVE** (both arms below): `ontime`, `ontime_unverified` and
    `unverified_max_backlog` all 0. **PASS.**
  - **OBSERVE: not shown on `dace947`.** No run today ended with the page in
    OBSERVE (the go-live entries all end in INHIBIT).
  - Observed, not judged: the RESPOND run's page read `tx_fail` 3, with
    `tx_ok` 3,496.
- **`would_tx` restarts on every arm.** `passive_analysis.py` `from_zero`,
  tested by `test_passive_analysis.py` (14 checks, 7 mutants all caught;
  `runs/test_passive_analysis_20261012.txt`). The control is in the same
  file: the `cc34c80` PASSIVE runs, where the count was cumulative, fail it.
  Two PASSIVE arms in one boot (`runs/e4_passive_dace947_20261012T170624_arm{1,2}.json`,
  Scotts Valley 0-35 s, 3,500 `0x051` each, armed before the replay;
  `runs/passive_analysis_dace947_20261012.jsonl`):

| Arm | First `diag_would_tx` page after the arm | Last page | Status page at the end | Verdict |
|---|---|---|---|---|
| 1 | 0, after 0 commands since the arm | 3,467, after 3,482 commands | 3,485 | PASS |
| 2 (re-arm, same boot) | 55, after 70 commands since the arm | 3,417, after 3,433 commands | 3,485 | PASS |

  - A count carried over from arm 1 would have read at least 3,467 at arm 2's
    first page, and about 7,000 at its end.
  - Both arms: 0 device `0x051` by provenance, 82,555 of 82,555 replayed
    frames found, `diag_mode` 4 throughout, and the would-transmit count rose
    by the commands seen in every full interval.
  - Host, at `dace947` (`runs/host_suite_dace947_20261012.txt`): shim case 44
    ("the would-transmit count restarts on every arm") passes.
    `passive_diff.py` now compares `would_tx` with the INHIBIT transmits since
    the last arm: 96 identical. The `099e9da` core differs on exactly the 8
    re-arm scenarios, as required.

**The page names the binary it runs -- tester, 2026-10-14 08:36-08:43, on
two builds of `88021c4` `_man` and on `5c21b5f` `_man`. Result: AS SPECIFIED
on all three images.** The 12.4 row: "`elf_sha256` on the page equals the
value in the flashed image file's descriptor, and differs between two builds
of one commit".
- **Images:** two clean builds of `88021c4` from one worktree. Their
  descriptors differ, and each equals its ELF's SHA-256
  (`runs/spec11_images_88021c4_20261014.json`). Third point: the `5c21b5f`
  build (`runs/build_5c21b5f_20261014.json`).
- **Method:** each image is OTA'd (`bench_flash.py`, which verifies the
  device's `git_version`), then `/gen_inhibit` is read. The page's
  `elf_sha256` is compared with the value in the image file's app descriptor
  at file offset 0xB0. That value is read by `image_elf_sha.py`, which is
  checked against esptool, not by the device's code.
- **Control:** the page must not equal the control image's value. If the two
  builds carried the same value the control could not fail, and the run
  would be VOID. The judge is `spec11_page_check.py`, tested by
  `test_spec11_page_check.py` (8 checks; 4 of 4 mutants killed).

| Image flashed | Page `elf_sha256` | Image descriptor | Control image | Verdict |
|---|---|---|---|---|
| `88021c4` build 1 | `e64b3f90...` | `e64b3f90...` | build 2, `f0a0c65f...` | AS SPECIFIED |
| `88021c4` build 2 | `f0a0c65f...` | `f0a0c65f...` | build 1, `e64b3f90...` | AS SPECIFIED |
| `5c21b5f` | `72c42a18...` | `72c42a18...` | the `e998385` image, `1ec3325c...` (another commit; `5c21b5f` has one build) | AS SPECIFIED |

- **Conditions:** no CAN traffic is involved. The X2 was on `7f197bfd` for
  the `88021c4` reads and the jammer for the `5c21b5f` read.
- **Run files:** `runs/spec11_page_{88021c4_build1_20261014T0843,88021c4_build2_20261014T0842,5c21b5f_20261014T0836}.json`
  and `runs/bench_flash_{spec11_88021c4_build1,spec11_88021c4_build2}_20261014T0842.txt`.

### A software reboot, confirmed by uptime (tester, 2026-10-20, `e3d9437` `_man`)

**What was tested.** Section 11's "a reboot is confirmed by uptime, not by the reply", on
the real device, by the bench's reboot step `wican_reboot.py` run once by hand in its
release form (`--want wican_man`) before its first use in a suite run (the gen-inhibit
reviewer's request, 2026-10-20). The script reads `/gen_inhibit` before, POSTs
`/system_reboot`, reads until a new boot shows, and checks the reset reason, the mode,
the latch and disable fields and `elf_sha256`. It reads nothing else.

**Build.** `e3d9437` `_man`, `elf_sha256` `25f50ac0...` (that image file's descriptor).
X2 on `756d6714`, not touched (as released 2026-10-20 04:13); no CAN adapter open.

**Result: PASS** (09:15, NICKY-XPS clock). `/system_reboot` replied 200. The next page
read `uptime_ms` 7,756 against 18,193,038 before, reset reason `SW`, mode 0,
`abort_latched` false, not disabled, `self_off` empty, and the same `elf_sha256`. The
boot before this one was also a software restart (the 04:12 reboot), so here the reset
reason alone would not have shown the restart; the falling uptime does, and the script
requires both.

**Run files.** `runs/wican_reboot_manual_release_20261020T091530.json` and `.txt`. The
script and its test against a fake device: `wican_reboot.py`, `test_wican_reboot.py`
(37 ok; 17 of 17 mutants killed by a check, `runs/test_wican_reboot_mutants_20261020.txt`).

### shared/wican W15: the image's version in mDNS and on the status page (tester, 2026-11-23, `0a740ce6` `_man`)

**Result: W15 is AS SPECIFIED. The mDNS half PASSES on the device; the
status-page half PASSES on the host.** This is stage (b) batch 1. The image,
the X2's state and the harness are as in the 9.2 entry. The page half was
first NOT RUN because it reads `fw_version` from `/check_status`, which has
been barred since 2026-10-20 until the factory reset or the user's ruling.
The bar stands. On the reviewer's instruction (2026-11-23), the page half is
tested on the host instead, as W12-W14 were, and `/check_status` is never
read from the device.

**The status page, on the host**
(`gen-inhibit/test/wican_status_host/run_status_host.py`, harness at
`e89b9fad`, on the pinned shared/wican `2026.11.23`, `8e16cd74`):
- **How it works:** the handler is the one the pinned `config_server.c`
  registers for the URI `/check_status`, here `check_status_handler()`. It
  is compiled against a fake ESP-IDF with ASan and UBSan. The JSON comes
  from ESP-IDF v5.4.2's own cJSON. Each case writes a known version string
  into the app descriptor and runs the handler once. The body must parse as
  one JSON object with one `fw_version` key, and that key must hold the
  whole string. A prefix, `0.00`, an empty value, a missing key or a second
  key fails.
- **Disclosure:** the fake was plumbed from a reading of the handler's
  source. The cases come from the spec's wording and from the forms
  shared/tools R4's `git describe --always --tags --dirty` can put in the
  32-byte field.
- **Results** (`host/status_host_2026.11.23.txt`): 4 of 4 cases PASS.
  `fw_version` was the descriptor's version, the whole string, in each:
  - `0a740ce6`, the abbreviated commit, as on the bench image;
  - `0a740ce6-dirty`;
  - `v4.20-1234-g0a740ce6ab-dirty`, a describe from a stock-style tag. The
    stock parse read 4.20 from this form;
  - `0a740ce6ab12cd34ef56ab78cd90ef1`, 31 characters, the most the field
    holds.
- **Recorded, not judged:** `git_version` was the same string in each case.
  The content type was `application/json`, with one send, returning
  `ESP_OK`.
- **Can-fail** (`host/status_host_2026.11.04_canfail.txt`): shared/main
  `2026.11.04`, from before W15, FAILS. `fw_version` is `0.00` on the
  commit, `-dirty` and 31-character cases. Its stock-tag case is VOID, not a
  pass: on a 64-bit host the stock parse's `%ld` writes 8 bytes into a
  `uint32_t`, and ASan stops it. This crash happens only on the host.
- **The harness's test** (`host/test_run_status_host.txt`, 32 checks ok):
  - the pinned code passes and the 2026.11.04 code fails as above;
  - six planted defects in the pinned handler each fail exactly the cases
    they show in: a renamed key, an empty value, a value cut to 8
    characters, a leading `v` dropped, the stock parse put back, and a
    second `fw_version` key;
  - five mutants of the judge each change a verdict;
  - the extraction is not misled by a registration in a comment, a
    commented-out `.handler`, or another URI. No registration, or two, is
    refused.

**mDNS, on the device**
- **Check:** `gen-inhibit/tools/bench/wican_fw_version_check.py`. It sends a
  legacy unicast DNS-SD query to UDP 5353 at the hotspot (RFC 6762 6.7) and
  reads the TXT entry under the stock key `fimrware`; the typo is upstream's,
  named in shared/main `8e16cd74`. It judges that entry against the version
  in the image's app descriptor, and it must be the whole string. Its own
  test runs against a local responder; a truncated version fails it. 5 of 5
  mutants are killed.
- **Observed** (`runs/stage_b1_0a740ce6/bench_20261123T181229/w15.json`):
  - 2 replies, 1 instance, `WiCAN-WebServer._http._tcp.local`;
  - TXT `path=/`, `hardware=WiCAN-OBD`, `fimrware=0a740ce6`;
  - the descriptor's version is `0a740ce6`.
- **Recorded, not judged:** `/gen_inhibit`'s `git_version` is null on this
  image. Only its `elf_sha256` and `git_version` fields are read.

### The probe on the inhibit frame's path (tester, 2026-11-25, `cca06f36` `_man`)

**RESPOND's probe, on the bench and in the spec shim -- tester, 2026-11-23 and 2026-11-25, on
`cca06f36`. Result: AS SPECIFIED. A probe found late is in neither on-time count, and it is
still sent.** Spec 11 as changed on 2026-11-23 (user, `ab74c3da`): the probe uses the same
code path as the inhibit frame. Only its frame and its due time differ, and it is never
withdrawn or a skip. A late probe is in neither on-time count.
- **The shared path, case by case:** the spec shim entry in section 7, "Trip 7, what the
  device can see of lateness". Its `late` case puts the VCM's next `0x051` among the frames
  waiting when the probe's completion is seen. The probe is then in neither on-time count,
  in `cls.probe.sent`, and not withdrawn or a skip. The same case in INHIBIT books one skip.
- **On the bench: B13's booking check, in B2's two RESPOND arms** (bench run `8047`,
  `man_held/B13/B13_respond_booking_check.txt`; the 12.4 entry). The RESPOND arm runs under
  replayed traffic, once with the X2 on its app and once with it held.

  | Arm | probes sent | `ontime` | `ontime_unverified` | unbooked | late on the wire | `unverified_max_backlog` |
  |---|---|---|---|---|---|---|
  | X2 app | 2998 | 2992 | 2 | 4 | 4 | 3 |
  | X2 held | 2999 | 2997 | 1 | 1 | 1 | 2 |

  - Unbooked is probes sent minus both on-time counts. A probe is late on the wire when the
    witness saw its VCM's next `0x051` before it. The witness judged every probe, 0
    malformed.
  - In both arms unbooked equals late on the wire, and no probe on time on the wire was
    booked late. Every late probe was sent: the witness saw it.
- **How the judge reads it.** `respond_booking_check.judge_balance` (harness `aa947dec`)
  passes when unbooked >= late on the wire, and fails when it is less. The reason: trip 7's
  decision can book an on-time probe late (the next `0x051` dequeued before the completion
  is seen, path (ii)), but never a late one on time. Before 2026-11-23 it asked for equality.
  Can-fail: `test_respond_booking_check.py`, with rows that must FAIL (fewer unbooked than
  late, 13 against 14 and 0 against 1) and a row that must PASS with one booked late but on
  time on the wire (13 against 12).
- **Field names:** the page's `ontime`, `ontime_unverified` and `unverified_max_backlog` read
  as before. Only what `ontime_unverified` counts changed: on time, with frames waiting.

## 12.1 Reading the result

> **Implementor self-test, not tester evidence.** Produced by the implementing session while it was testing its own work, before the tester role existed (user, 2026-10-07). It closes nothing and is expected to go stale; the tester replaces it with its own result.

**RESPOND baseline after the scheduler switchover, 2026-09-27, on `e011b01`.**
Measured on the 100 Hz timing bench (`3a_respond_baseline_e011b01.json`, with
the Kvaser raw logs beside it). It is compared with three pre-scheduler runs
of the same tool **by means**. Medians are not comparable here: the latencies
sit on a ladder of values ~85 us apart, so a mean shift of tens of us moves
the median a whole step.

| | this run | earlier three runs |
|---|---|---|
| wire, WiFi idle | 756 us | 746-748 us |
| wire, WiFi load | 897 us | 862-882 us |
| device, WiFi idle | 503 us | 504 us |
| device, WiFi load | 553 us | 548-567 us |

**Comparable.** The scheduler costs about +9 us at the wire with WiFi idle,
which is outside the earlier runs' 2.6 us spread. Under WiFi load the
difference is within their ~20 us run-to-run variation.

## 12.3 Build-output checks (E3) and prod builds

**Every build-time switch is classified -- tester, 2026-10-13, source at
`a3394ca`, refusal rows on images of `42a860d` and `a3394ca`. Result: built;
it FAILS today on one unclassified symbol, `GEN_INHIBIT_SOC_MIN_PCT`, held
open on purpose until the user decides its class or deletion (reviewer).**
Spec 12.3 item 3, paragraph "Every build-time switch is classified" (added
2026-10-13, user, with the "not a switch" class and the `main/CMakeLists.txt`
scope added the same day). Scope readings by the reviewer: product source is
`main/` and `components/`, not `managed_components/` or `test/`. Whether the
top-level `CMakeLists.txt` is in scope is with the user; only
`main/CMakeLists.txt` is scanned for now.
- **Check:** `test/build_checks/build_switches.py` (firmware repo, branch
  `tester/build-switches`, `ee813c3`, local).
  - It scans every symbol an `#if`/`#ifdef`/`#ifndef`/`#elif` tests (`CONFIG_*`
    and header guards excluded) and every variable an `if()`/`elseif()` in
    `main/CMakeLists.txt` tests.
  - It compares them with a table: shipping variant, test-only (naming its
    refusal row), or not a switch (default value, injected value, library
    macro), each with a reason.
  - `build_output.py` reports four rows: unlisted, stale entries, malformed
    entries, and "every test-only switch has a refusal row" (the named row
    must be one the run produced).
- **The table at `a3394ca`:** 25 symbols, classes as the reviewer ruled.
  - Shipping variant: `GEN_INHIBIT_AUTOARM`, `GEN_INHIBIT_AUTOARM_MODE`,
    `GEN_INHIBIT_AUTOARM_OFFSET_US`, `HARDWARE_VER`, `WICAN_PRO`.
  - Test-only: `GI_INSTRUMENT_RXQ`, `_TXABORT`, `_ABORTWIN` (row "no
    measurement instrument in the image"), `OTA_HEALTH_FAULT_INJECT` (row "no
    OTA fault injection in the image"), `GI_TEST_RESTART_QUIESCE` (new row "no
    restart test feature in the image").
  - Not a switch: `GIT_SHA`, `DIAG_FW_VERSION`, `OTA_HEALTH_TIMEOUT_MS`,
    `DEBUG_LOGS_*` (6), `MIN`, `MAX`, `__cplusplus`,
    `ADC_CALI_SCHEME_{CURVE,LINE}_FITTING_SUPPORTED`.
  - Unlisted, so FAIL: `GEN_INHIBIT_SOC_MIN_PCT` (`main/gen_inhibit.c:2075`).
- **The new refusal rows, for `GI_TEST_RESTART_QUIESCE`:**
  - image: any of `/gi_quiesce`, `{"build":"restart-test"`,
    `expected ?crash=illegal|abort`;
  - ELF: `gi_restart_test_register`, `gi_restart_test_pre_install` or
    `gi_restart_record_get` defined;
  - name: `_rt` as a whole token in `project_name` (from `a3394ca`).
  - The markers were picked by comparing images of `42a860d` built by the
    tester from a clean worktree with and without the switch: 41 strings and
    19 symbols are only in the switch-on image, and none only in the
    switch-off image.
- **Shown failing on the switch-on image and passing without it** (spec's
  requirement), tester's clean-worktree builds:
  - `42a860d` (image and ELF rows; both images were named
    `wican-fw_obd_42a860d_man.bin`): on md5 `11cae294...`, off md5 `4210fdfb...`;
  - `a3394ca` (image, ELF and name rows): `..._man_rt.bin` md5 `644cabc7...`,
    `..._man.bin` md5 `9ada877f...`
    (`runs/test_switch_rows_pair_a3394ca_20261013.txt`).
  - `63eb50f`, every test-only switch (reviewer, 2026-10-13: the four older
    rows judge markers the code can drift away from). The tester's six
    clean-worktree builds: off `360f1719...`, restart test `87b3662f...`,
    RXQ `b74948e5...`, TXABORT `b0508bed...`, ABORTWIN `bb951803...`,
    `OTA_HEALTH_FAULT_INJECT=1` `327d9655...`.
    - `test_refusal_pairs.py` (`cb25931`): each switch's rows fail on its
      own image, and the instrument rows name that switch. Every refusal
      row passes on the off image. PASS, 20 checks
      (`runs/test_refusal_pairs_63eb50f_20261013.txt`).
    - Negative controls, all caught: TXABORT given the RXQ image, the
      restart test given the off image, the RXQ or fault-injection image as
      the off image, ABORTWIN left out, an RXQ image without its ELF
      (`runs/test_refusal_pairs_negative_controls_63eb50f_20261013.txt`).
- **Tests of the check:**
  - `test_build_switches.py` (synthetic trees): a default value shaped like a
    guard, a guard after an `#include`, a guard symbol tested again later,
    `defined()`, `#elif`, continuations, comments and string literals, CMake
    keywords, comments, `${}`, `elseif` and case, scope, and the table rules.
    25 mutants, all caught (`runs/test_build_switches_mutants_20261013.txt`).
    One defect in the first version was found this way: an `#ifndef` after an
    `#include` was taken for a header guard.
  - `test_switch_rows.py`: each restart-test marker alone fails the row, the
    `_rt` token is whole-token only, and each scan finding reaches its own row
    only. 14 mutants on the `a3394ca` pair, all caught; 2 dropped as equivalent
    (`runs/test_switch_rows_mutants_a3394ca_20261013.txt`).
- **Observed vs spec, from the same `build_output.py` run on `42a860d`:** the
  12.4 row "the private TWAI abort is confined" (`twai_ll_set_cmd_abort_tx` at
  exactly one site, the scheduler's device HAL) FAILS. It is called in
  `gen_inhibit.c` x1 and `gi_restart_quiesce.c` x1. Reported to the reviewer.
  On `63eb50f` (the fix) the row passes: exactly one site, in `gen_inhibit.c`.
- **The 12.4 inert row** is recorded under 12.4 ("Test-only features are inert in the build that will fly").
- **Re-run on `3ee47b7`, after the user's rulings:** the next entry (PASS).

**Every build-time switch is classified, on `3ee47b7` -- tester, 2026-10-13.
Result: PASS. 27 symbols tested, all listed; nothing unlisted, stale or
malformed.**
Spec 12.3 item 3 as amended 2026-10-13 (user): "every build option the
project's CMakeLists.txt files test", so the top-level `CMakeLists.txt` is in
scope. `GEN_INHIBIT_SOC_MIN_PCT` is deleted (user's ruling; implementor,
`3ee47b7`).
- **Tree:** `3ee47b7` (`gen-inhibit/main`, local) with `tester/build-switches`
  `7fb7525` merged. The merge changes `test/build_checks/` only.
  `GEN_INHIBIT_SOC_MIN_PCT` has no match left in `main/`, `components/` or the
  top-level `CMakeLists.txt`.
- **Changes to the check** (`tester/build-switches`, local):
  - `becc186`: the scan also reads the top-level `CMakeLists.txt` and every
    `CMakeLists.txt` under `main/` and `components/`. `managed_components/`,
    `test/` and build directories stay out. Only `components/debug_logs`
    has one, and it has no `if()`.
  - `7fb7525`: the three names the wider scan found, classed by the reviewer.
    - `WICAN_USB_V100`, `WICAN_V210`: shipping variant (board ids
      `HARDWARE_VER` is compared with, as `WICAN_PRO`).
    - `PRJ_BIN_LEN`: not a switch, "computed by the build from the project
      name, used only to refuse a name over 31 characters".
    - The reviewer reads the spec's three "not a switch" kinds as examples,
      not a closed list. The check now requires a kind but no longer limits
      it to those three.
  - `8a76cc7`, a harness fix: `test_switch_rows.py` failed 3 checks from
    `31f59b8` (the trigger table) until this commit, because its substituted
    table did not bring matching triggers. No reported result rested on
    them. The refusal-pair PASS above came from `test_refusal_pairs.py`.
- **Result** (`runs/build_switches_3ee47b7_20261013.txt`): 27 symbols, 15 not
  a switch, 7 shipping variant, 5 test-only; unlisted 0, stale 0, malformed 0.
- **The rows, on the implementor's image** `wican-fw_obd_3ee47b7_man.bin`, md5
  `c5fb63ba...` (`runs/build_output_3ee47b7_20261013.txt`):
  - These pass: the three switch rows, "every test-only switch has a refusal
    row", and the three restart-test refusal rows.
  - "Image built from HEAD" FAILS and "sources older than the image" has NO
    REF. Both come from the run's context, not the image: HEAD is the merge
    commit, not `3ee47b7`. An addendum in the run file shows the diff.
  - "Prod-build record present" has NO REF: this is not a prod build.
- **Tests of the check:**
  - `test_build_switches.py`: 35 mutants, all caught. Five are new, on the
    CMake scope, and two on the kind check
    (`runs/test_build_switches_mutants_7fb7525_20261013.txt`).
  - `test_switch_rows.py`: 14 of 14 on the `a3394ca` pair
    (`runs/test_switch_rows_mutants_7fb7525_20261013.txt`).
- **The host suite on the same commit** (`runs/host_suite_3ee47b7_20261013.txt`,
  `madhouse-debian`, `<madhouse-debian home>/gi_host_tester_3ee47b7`):
  - 78 goldens pass, 22 scenarios without goldens;
  - `invariants` (100 hold), `passive_diff` (96 identical, 0 differing) and
    `fuzz_sequences` (200 sequences, 0 violations);
  - `load_margin` PASS, `spec_expect_check` 21 of 21, `bus_on_check` and
    `drained_check` PASS;
  - the shim and scheduler suites: every case passes.
  - **The tree also merges `tester/golive-host-dace947`** (`a4806b2`, on
    origin), which was never merged into `gen-inhibit/main`. It carries
    `drained_check.py`, the 10-12 goldens and the `passive_diff` per-arm fix.
    Without it, `gi_host_run.sh` stops at the sync on the missing
    `drained_check.py`. With `45375b9` but not the fix, `passive_diff`
    flags the 8 re-arm scenarios, as it did on 10-12
    (`runs/host_suite_3ee47b7_attempts_20261013.txt`).
  - **Generated inputs (not in git)** were copied from the 10-12 `dace947`
    run's directory: the eight replays, which are the stimulus the goldens
    are pinned to, and the shim's `replay-longarm-300s-all.scn` (sha256
    `43459c48...`). In `<madhouse-debian home>/gi_host`, six replays failed as `[STIM]`, a
    stimulus that is not the one the goldens were blessed from, not a
    firmware result (same attempts file).

**The switch scan takes its CMake files from the build's record -- tester,
2026-10-13, real configures on `NICKY-XPS`. Result: PASS, shown both ways,
on `d74777d` and on `e998385`.**
Spec 12.3 item 3 as changed 2026-10-13 (user): "every build option tested by a
CMake file the build reads from the repo, taken from the build's own record of
the files it read", with a build option defined as "a variable that can be set
from outside the file that tests it". Spec 13 "Checks, not built". Readings
(reviewer, 2026-10-13):
- the record is the inputs of `build.ninja`'s regeneration rule
  (`ninja -t query build.ninja`), minus the IDF install and the build
  directory;
- `managed_components/` is excluded by that exact directory, not by git
  tracking. The scan prints what it scanned and what it excluded, and fails
  if the scanned list is empty or lacks the top-level `CMakeLists.txt`;
- "sets itself" is read narrowly, so the scan errs towards listing: only a
  file-scope `set(VAR <value>)` with no CACHE or PARENT_SCOPE, before the
  test, from built-ins or the file's own variables;
- built-ins are `cmake --help-variable-list` of the recorded cmake.
  `<PROJECT-NAME>` expands only to the declared project. Other placeholders
  count only in a pattern with a literal prefix.
- **The check:** `test/build_checks/build_switches.py` (firmware repo, branch
  `tester/build-switches`, `2021b3e`, then `46d0863` for `e998385`'s table,
  local).
  - It needs a configured build directory (`--repo`, `--build`). It refuses,
    and fails, when there is no `build.ninja`, when ninja or cmake cannot
    answer, or when the record is empty or not this tree's. It also refuses
    when a scanned file is newer than `build.ninja`.
  - `build_output.py`'s "every build-time switch classified" row reads the
    record of the image's own build directory. A record it cannot use fails
    the row, with no fallback.
- **The record, on both commits** (`runs/build_switches_record_d74777d_20261013.txt`,
  `runs/build_switches_record_e998385_20261013.txt`):
  - scanned: `CMakeLists.txt`, `cmake/git_tree_name.cmake`,
    `components/debug_logs/CMakeLists.txt`, `main/CMakeLists.txt`;
  - excluded: `managed_components/espressif__mdns/CMakeLists.txt` and seven
    files under `build/`;
  - outside the repo: 259 (the IDF install and CMake's modules).
- **Both ways, on real configures** (`scan_scope_check.py`; `runs/scan_scope_d74777d_20261013T194230.{json,log}`,
  `runs/scan_scope_e998385_20261013T195848.{json,log}`). Throwaway
  worktree, `idf.py reconfigure` only. All three steps PASS on each commit:
  - baseline: the scan is clean (0 unlisted, 0 stale, 0 malformed);
  - planted: a NEW `cmake/gi_planted_option.cmake`, included from the
    top-level `CMakeLists.txt`, tests `GI_PLANTED_OPTION`, configured with
    `-DGI_PLANTED_OPTION=ON`. Its `message()` printed, so the `-D` reached
    the test: it is a build option. The scan FAILED, naming it unlisted at
    `cmake/gi_planted_option.cmake:2`. The file was in the record;
  - set-first: the same file with `set(GI_PLANTED_OPTION OFF)` before the
    test, the same `-D`. The `message()` did not print, so the file's own
    set won. The file was still in the record. The scan PASSED and did not
    report the name.
- **Classification changes the new definition brought** (the table is the
  tester's; flagged here for the reviewer):
  - **dropped:** `WICAN_USB_V100` and `WICAN_V210`. The top-level
    `CMakeLists.txt` sets both unconditionally before testing them, so they
    are not build options, and no C source tests them. `HARDWARE_VER` and
    `WICAN_PRO` stay, tested in C.
  - **added, "not a switch", kind "module internal"**, each with its reason:
    ten names `cmake/git_tree_name.cmake` tests, which the narrow reading
    lists. They are `GTN_DESCRIBE`, `GTN_STAMP`, `_gtn_now`, `_gtn_was`,
    `res`, `name`, `called`, `git`, `stamp` and `was`: function locals,
    out-parameters, a find_program result, and the script-mode inputs of
    the module's own check.
  - **on `e998385`** (`46d0863`):
    - `GIT_SHA` dropped as stale: it is no longer a compile define;
    - `PROJECT_VER` added: "not a switch", kind "guard", the reviewer's
      pre-ruled reason;
    - `len` added: a module internal.
    - With the `d74777d` table, the `e998385` scan reported exactly those
      three, 2 unlisted and 1 stale.
- **Tests of the check:** `test_build_switches.py` (the record, every refusal,
  the narrow reading case by case), `test_switch_rows.py` (an unusable record
  fails the row), `test_scan_scope_check.py` (every judgement). 66 mutants
  across `build_switches.py`, `build_output.py`'s row and
  `scan_scope_check.py`, all caught
  (`runs/test_build_switches_mutants_2021b3e_20261013.txt`).
- **No bench use.**

**Prod builds are built on a clean tree -- tester, refusals 2026-10-12 on
`ad80037`, positive build 2026-10-10 on `ddddfea`. Result: as specified,
the branch rule (added 2026-10-12, user) included.**
- **Refusals:** `test/build_checks/test_prod_build_refusals.py` (firmware
  repo, branch `tester/prod-build-refusals`, `62e641f`).
  - It runs `tools/prod_build.py --check-only` as committed, in a throwaway
    tree, with a copy of the verified `managed_components/`.
  - Since 2026-10-12 the throwaway tree is an isolated local clone with its
    own `gen-inhibit/main` at the commit, not a `git worktree`. The real
    branch is checked out elsewhere, and `--self-test` commits fakes, which
    in a worktree sharing refs would move it. After the runs, the real
    `gen-inhibit/main` was still at `ad80037`, with no probe branch and no
    clone left.
  - The clean tree on `gen-inhibit/main` passes.
  - Each case must be refused for its own cause (cause phrase, not exact
    text) and pass again once undone:
    - tracked: edited, staged, `README.md` deleted, `main/can.c` deleted,
      `main/gen_inhibit.c` deleted;
    - untracked file;
    - component file edited, missing or added;
    - `.component_hash` off the lock;
    - **the branch rule** (spec 12.3 item 3, "refuses to build unless HEAD is
      on `gen-inhibit/main`"): another branch at the same commit; a detached
      HEAD at the branch's tip; `gen-inhibit/main-old` and
      `probe/gen-inhibit/main`, against a match on part of the name. Each
      refusal must name `gen-inhibit/main`.
    - a dirty tree on another branch, which must name both rules;
    - the script run from a directory that is not the repo, both as a sibling
      outside any repo and nested inside a git tree (identity cause).
  - `--self-test` catches 6 fakes: always passes, always refuses, exits 1
    silently, refuses a dirty tree by the wrong rule, accepts a branch whose
    name contains `gen-inhibit/main`, accepts a detached HEAD at the tip
    (`runs/prod_build_refusals_selftest_ad80037_20261012.txt`).
  - **`ad80037`: 34 of 34** (`runs/prod_build_refusals_ad80037_20261012.txt`).
    Each branch refusal names what it found ("HEAD is on
    tester-probe-other", "HEAD is detached at ad80037") and the rule.
  - **Control, `dace947`** (before the rule): the five branch cases FAIL, as
    required. Every other case passes
    (`runs/prod_build_refusals_dace947_control_20261012.txt`).
  - Earlier runs: `..._13709ab_20261010.txt` (22/22, with the nested identity
    case red at `ddddfea`), `..._ddddfea_...` and `..._fab2713_...`.
- **Positive build:** `prod_build.py` at `ddddfea`, in a throwaway worktree
  (`runs/prod_build_ddddfea_20261010.txt`). Not re-run at `ad80037`; at
  `ddddfea` there was no branch rule to pass.
  - The log opens with "Executing action: reconfigure".
  - It shows the tree clean, 96 component files verified, exit 0.
  - Record: commit `ddddfea`, 1,275,920 bytes, md5
    `e6048490cbc97d01f39b737e830a8dd8`, which matches the image's own md5.
    `git status` was empty after the build.
  - Python's buffered prints land after the build output, so the log's line
    order says nothing about whether the refusal checks ran before the build.
- **E3 on that image** (`runs/build_output_ddddfea_prod_20261010.txt`):
  36 ok, "every check with a reference passed". The prod-record row reads
  "present: built from ddddfea on a clean tree, components verified"; a bench
  image reads NO REFERENCE there.

**The prod build records the image the build named -- tester, 2026-10-21, on
`95aa77a`. Result: AS SPECIFIED.** Spec 12.3 item 3 (a prod build "records the
commit, the image's md5 and its size beside the image"), with the reviewer's
reading of 2026-10-21: the image is the one `build/project_description.json`
names as `app_bin`, not the newest `.bin` in `build/`, and a missing name or
file is a refusal. `95aa77a` changes `tools/prod_build.py` only; the product
code is still `e3d9437`'s.
- **Check:** `test/build_checks/test_prod_build_image_real.py` (firmware repo,
  tester clone, branch `tester/range-20261020b`, `de142aa`, local; run through
  `prod_image_real_run.ps1`, which logs the harness's sha256).
  - It runs the clone's own `tools/prod_build.py` as committed: `main()` with
    every check and the real `idf.py reconfigure` and `idf.py build`.
  - A hook changes `build/` just after `idf.py build` returns, so the change is
    in place when the script judges the image. A change made before the run
    would be undone, because the reconfigure rewrites
    `project_description.json`.
  - A spy notes which image the real image judge (`check_image_symbols`) was
    given. Nothing is replaced.
  - Cases:
    - stray: a newer `wican-fw_obd_*.bin` of another name, mtime an hour ahead;
    - no-desc: `project_description.json` moved out of `build/`;
    - no-key: `project_description.json` without `app_bin`;
    - no-file: app_bin's file moved out, with the stray beside it;
    - plain: no change, run last so the clone keeps its record.
  - stray and plain must exit 0, judge app_bin's image and write one record for
    it, whose md5, size and commit match that file and HEAD; the stray gets no
    record. The other three must exit non-zero, with nothing judged and no
    record.
  - Every case must fire the hook exactly once and leave `git status` empty.
- **Changed from the plan agreed with the reviewer:** the plan ran cases (a)-(d)
  through `--check-only`. `--check-only` builds nothing ("run the refusals and
  stop; build nothing"), so it never judges an image. The cases run through a
  real build instead.
- **`95aa77a`: 5 of 5** (`runs/prod_image_real_95aa77a_20261021.txt`). A fresh
  clone of `gen-inhibit/main` at `95aa77a`, clean; run at Idle priority.
  - Each refusal names its cause:
    - "build/project_description.json cannot be read ... so which image this
      build made is unknown";
    - "has no "app_bin"";
    - "names wican-fw_obd_95aa77a_man.bin as this build's image, and build/ has
      no such file".
  - The record: `wican-fw_obd_95aa77a_man.bin`, 1,113,456 bytes, md5
    `9db11ebdcbba8a3deba7b56158307c08`, commit `95aa77a`. `git status` was empty
    after every case.
  - Run again after the mutants: 5 of 5 again, the record's md5 the same (`..._95aa77a_rerun_20261021.txt`).
- **Control, the parent `3bec89d`** (before the fix): stray, no-desc, no-key and
  no-file FAIL, as required; plain passes
  (`..._3bec89d_control_20261021.txt`, a clone whose `gen-inhibit/main` is
  `3bec89d`).
  - stray and no-file: the old script judged the stray. It refused only because
    no ELF sat beside it.
  - no-desc and no-key: it built and recorded app_bin's image.
- **The harness's own checks** (`test_prod_build_image_real_mutants.py`,
  `..._mutants_95aa77a_20261021.txt`): 3 of 3 mutants killed by a check, each
  on one real case against `95aa77a`:
  - the hook fires but changes nothing;
  - the hook is never installed (killed by "the hook fired 0 time(s)");
  - a case leaves a file in the tree (killed by "git status empty").
- **Also at `95aa77a`:**
  - `test_prod_build_refusals.py`: PASS, 28 checks, and its self-test PASS, 5
    fakes caught. These are the same checks as the `2df3c17` run
    (`runs/prod_build_refusals_95aa77a_20261021.txt`,
    `..._selftest_95aa77a_20261021.txt`).
  - The implementor's `tools/test_prod_build_image.py` (4 of 4) and
    `test_prod_build_image_mutants.py` (1 of 1), run unedited. These are
    implementor self-tests, not evidence (`runs/test_prod_build_image_95aa77a_20261021.txt`,
    `..._mutants_95aa77a_20261021.txt`).
- **Not done:** `build_output.py` on this prod image, which is not flashed.

**The E1 shim suite's hang, and its fix -- tester, 2026-10-20, on
`implementor/shim-hang` `38fd103` (from `b341e0d`). Result: the hang is gone,
and with its generated scenario built the whole suite passes, 3 of 3.** Spec
12.3 item 1 (the mock-HAL host build).
- **The hang.** `test/gen_inhibit_shim/shim_test` at `b341e0d`, built with WSL
  gcc 9.4 on NICKY-XPS, sat in its case 5 for 17 minutes with its 20 s per-case
  budget silent, and was stopped by hand.
- **The fix** (implementor): `51cfd51` enforces the per-case budget while a
  case runs (exit 2, naming the case); `38fd103` stops the worker without
  `pthread_cancel()`.
- **Method.** `shim_hang_verify.py` builds `38fd103` from a `git archive` (so
  nothing untracked is present) and runs `shim_test` and `shim_test_autoarm`
  10 times each; then the same tree with `pthread_cancel()` put back in
  `ft_stop_worker()`, 10 times, as the check that can fail. **WSL only;
  madhouse-debian was not run** (reviewer, 2026-10-20).
- **Result.**
  - No fix run hung: `shim_test` finished in ~1.5 s each time,
    `shim_test_autoarm` passed 10 of 10.
  - `shim_test` exited 1 every time, on cases 33 and 53 only: both report
    `scenarios/replay-longarm-300s-all.scn` missing, a generated file not in
    git (the harness README gives the line that builds it).
  - With the cancel back, 10 of 10 runs hung and the new budget stopped each
    at ~20 s (exit 2, naming the case).
- **The whole suite** (reviewer, 2026-10-20: cases 33 and 53 must run before
  the push). In the archive copy, `replay-longarm-300s-all.scn` built with the
  line `test/gen_inhibit_shim/README.md` gives (`from_capture.py` from
  `runs/scottsvalley_armable_300s.log`, channel 0, 300 s, `--all-ids`; exit 0,
  23,241,533 bytes, md5 `51b0a55c...`); then `shim_test` whole 3 times: exit 0
  each time (77, 78 and 74 s), no FAIL line
  (`runs/shim_hang_verify_38fd103_whole_20261020T102726/`).
- **Run files.** `runs/shim_hang_verify_38fd103_20261020T102101/`
  (`summary.json`, `fix.log`, `cancel.log`); the first run,
  `..._20261020T101555_tailonly/`, kept only each run's last lines.

**Known anomaly (1) closed: gen-inhibit's release builds pass the options
check (R18) and the reproducible test (R19) -- tester, 2026-10-25, `0c707e9`,
shared/tools `2026.10.24`. Result: PASS, both.** The review tracker's known
anomaly (1): gen-inhibit release builds failed shared/tools R18 and R19
because `CONFIG_APP_REPRODUCIBLE_BUILD` was off, to be fixed by port step 0b
(the implementor's `4f65e81`, merged in `a996894`). Spec 12.3 item 3 (a prod
build is the shared tooling's release build at the pinned version; user,
2026-10-25) and shared/tools SPEC R18 (options against the project's release
profile) and R19 (one clean commit, built twice in two folders, gives one
image hash).
- **Build:** gen-inhibit/main `0c707e9`, cloned twice into new folders of
  different length and depth (`scratch/a1/a/gi`, `scratch/a1/b_longer/deeper/gi`),
  both clean. Each built once by its own launcher, `tools/vtrux_tools.py
  release`, with the tooling its `shared_pins.json` names: shared/tools
  `2026.10.24` (`e40f5cb`, digest `2dcbfd89...`), from a checkout
  `../shared-2026.10.24/` made beside each clone with the command the tooling's
  README gives. Each record's tooling field names that tag, commit and digest,
  dirty false. `REVERSE_IT_DIR` was a stand-in folder per clone, so no record
  reached the real `firmware/builds/` (no prod build during the port). Both
  builds exit 0 (329 and 321 s). NICKY-XPS, IDF v5.4.1, its Python 3.11.9;
  full jobs, Idle priority, no wall-clock test running, bench lock free.
  gen-inhibit/main has since moved to `200a6e4`, which changes only the pin
  (to `2026.10.25`); these builds were already running.
- **R19: PASS.** The image hash, read three ways in each build (the record,
  the pinned library through `tools/vtrux_tools.py image`, and the tester's own
  reader `independent.py`), is `e7e2282a...0630f` in both; the `.bin` files are
  byte-identical (sha256 `3ccc7a57...c427`) and the code hash is the same,
  `7341ad32...ed08`. The premise was checked first: both clones clean at the
  same commit, no `build/` before, `CONFIG_APP_REPRODUCIBLE_BUILD` on in the
  config each build used. Judged by the tester's `repro_cases.py` premise and
  judge.
- **R18: PASS on both builds.** The pinned `vtrux_checks options` (from the
  `2026.10.24` checkout) exits 0 on each, reading `build` and
  `build/bootloader`: all 9 options of `0c707e9`'s release profile
  (settings 1 to 5 of spec 12.3 item 3, and R12's
  `CONFIG_APP_REPRODUCIBLE_BUILD`, now true in both folders) at the profile's
  value. The tester read the same options independently from each folder's
  `config/sdkconfig.h`: none differs from the profile. Judged by the tester's
  `options_cases.py` judge.
- **Not part of the anomaly: the profile's setting 6.** Spec 12.3 item 3's new
  sixth setting, `CONFIG_FREERTOS_HZ` 1000 (shared/tools R27), is added to
  `vtrux_checks.json` on `gen-inhibit/tester/freertos-hz` `db58c35`. On build
  a, the options check of shared/tools `2026.10.25` (`8dd056e`) passes with it
  (1000 in both folders), and fails naming it with the value 100 or the string
  `"1000"`. With the entry removed it also passes (exit 0): see the report to
  the reviewer.
- **Run files.** `runs/anomaly1_0c707e9/` (`README.md`; `SHA256SUMS`
  `cf2ab1b1...b3244` hashes every file: `results.json` `b215b8e8...1ea33`,
  `options_a.log` `6da9ef9f...ff655`, `options_b.log` `21522e98...18b84`,
  `driver.log` `61eaeccc...383e8`, the build logs, the driver and the setting 6
  check), and `runs/anomaly1_0c707e9.console.txt`.

**Known anomaly (2) closed, and port step 0c's release-build acceptance: the
commit enters the image once (shared/tools R4) -- tester, 2026-10-25, `ef553fa`,
shared/tools `2026.10.25`. Result: PASS, all eight results.** The review
tracker's known anomaly (2): the commit also sat in `project_name`, so R4 was
not met, until port step 0c (the implementor's `a17706f`, `ef553fa`). Spec 12.3
item 3 (a prod build is the shared tooling's release build at the pinned
version; user, 2026-10-25) and the reviewer's step 0c acceptance: a release
build at the tip has the version = the bare commit, a `project_name` with no
commit, and the code hash equal across two worktrees. shared/tools SPEC R4 (the
version field holds `git describe --always --tags --dirty` run at configure;
`project_name` carries no commit), with R18 and R19 as for known anomaly (1).
- **Build:** gen-inhibit/implementor/build-id `ef553fa`, cloned twice into new
  folders of different length and depth (`scratch/a2/a/gi`,
  `scratch/a2/b_longer/deeper/gi`), both clean. Each built once by its own
  launcher, `tools/vtrux_tools.py release`, with the tooling its
  `shared_pins.json` names: shared/tools `2026.10.25` (`8dd056e`, digest
  `e16aec27...`), from a checkout `../shared-2026.10.25/` made beside each
  clone. Each record's tooling field names that tag, commit and digest, dirty
  false. `REVERSE_IT_DIR` was a stand-in folder per clone, so no record reached
  the real `firmware/builds/` (no prod build during the port). Both builds exit
  0 (293 and 287 s). NICKY-XPS, IDF v5.4.1, its Python 3.11.9; full jobs, Idle
  priority, no wall-clock test running, bench lock free.
- **R4: PASS on both builds.** Read from each image's app descriptor by the
  tester's reader (`independent.py`): the version is `ef553fa`, which is what
  `git describe --always --tags --dirty` gives in that clone (no tag is
  reachable from the commit) and a bare commit (hex, a prefix of `ef553fa...`,
  no `-dirty`). `project_name` is `wican-fw_obd_man`: it starts `wican-fw` and
  has no run of 7 or more hex digits (the reviewer's reading of the project-name
  row, 2026-10-25). **Control:** the same driver on `0c707e9` (before step 0c)
  FAILS the project-name row on both clones, `wican-fw_obd_0c707e9_man`
  (`0c707e9` names a commit). Its version, `0c707e9`, also equals describe and
  is bare, so the control shows the project-name row failing, not the version
  row.
- **Code hash equal across the two clones: PASS.** `f7112b30...93ae` in both,
  computed by the tester (SPEC R2's blanking) and as each record states it.
- **R19: PASS.** The image hash, read three ways in each build, is
  `c7c3a226...d821` in both; the `.bin` files are byte-identical (sha256
  `e2bb51c4...8d89`).
- **R18: PASS on both builds.** The pinned `vtrux_checks options` (from the
  `2026.10.25` checkout) exits 0 on each, reading `build` and
  `build/bootloader`: all 10 options of `ef553fa`'s release profile (setting 6,
  `CONFIG_FREERTOS_HZ` 1000, included) at the profile's value; none differs in
  either folder's `config/sdkconfig.h`.
- **The image file's name changes with `project_name`:** `wican-fw_obd_man.bin`,
  with no commit. The bench scripts that named images by the commit are changed
  on `gen-inhibit/tester/step0c` (`4c8201c`: found with or without the commit in the name).
- **Run files.** `runs/anomaly2_ef553fa/` (`README.md`; `SHA256SUMS`
  `da0783fa...6ac6fa` hashes every file: `results.json`, `driver.log`, the
  build and options logs, the driver, its launcher and maker, the console, and
  `control_0c707e9/`), and `runs/anomaly2_ef553fa.console.txt`.

**Port step 0d's release-build acceptance: the move into `gen-inhibit/` leaves
the code hash unchanged (shared/tools R2) -- tester, 2026-10-26, `e7263bb` and
`60d52f3`, shared/tools `2026.10.25`. Result: PASS, all seven results; R21
recorded, PASS on both builds.** Port step 0d
(port plan text, user, 2026-10-26; AGENTS.md "Project layout", `cd63a9d`)
moves gen-inhibit's tree under `gen-inhibit/`: the WiCAN tree, the IDF build
root, to `gen-inhibit/firmware/`, the tooling flat to `gen-inhibit/tools/`, the
tests to `gen-inhibit/test/`, the launcher to `gen-inhibit/vtrux_tools.py`. The
reviewer's acceptance (revised batch, 2026-10-26): release builds of the commit
before the move and of the reviewer's merge, on one host, throttled, after the
bench check, with equal code hashes. No spec section changes and no suite runs
(the bench and the chain take the new paths at the next run). Spec 12.3 item 3
(a prod build is the shared tooling's release build at the pinned version;
user, 2026-10-25); shared/tools SPEC R2 (the code hash), with R4, R18 and R21
recorded.
- **Builds.** Before: `gen-inhibit/main` `e7263bb`, the WiCAN tree at the
  clone's root, built by `py -3.14 -B tools/vtrux_tools.py release --jobs 1`.
  After: the reviewer's local merge `60d52f3` (parents `69d2c57` = `e7263bb`
  plus the methodology picks `cd63a9d`, `ba1599c`, `c17f94f`; the implementor's
  `e7263bb..43f0ae2`; the tester's `e7263bb..143ed89`), not yet pushed, built by
  `py -3.14 -B gen-inhibit/vtrux_tools.py release --jobs 1` (its `--project`
  default is `gen-inhibit/firmware/`). Each commit cloned into a new folder of
  different length and depth (`scratch/m0f/a/gi`, `scratch/m0f/b_longer/deeper/gi`),
  both clean, each with the tooling its `shared_pins.json` names (at the root
  before, in `gen-inhibit/firmware/` after; both shared/tools `2026.10.25`,
  `8dd056e`, digest `e16aec27...`), from a checkout `../shared-2026.10.25/` made
  beside each clone; each checkout's HEAD is `8dd056e`, and each record's
  tooling field names that tag, commit and digest, dirty false. `REVERSE_IT_DIR`
  was a stand-in folder per clone, so no record reached the real
  `firmware/builds/` (no prod build during the port). Both builds exit 0 (841
  and 689 s); in each, the image hash read three ways (the record, the
  library, the tester's reader) agrees. NICKY-XPS, IDF v5.4.1, its Python
  3.11.9 for the driver; Idle priority and `--jobs 1` (AGENTS.md's throttled
  form); bench lock free, "No builds" clear, no simulator or build process
  running.
- **Code hash equal before and after: PASS.** `f7112b30...93ae` on `e7263bb`,
  `f7112b30...93ae` on `60d52f3`, computed by the tester (SPEC R2's blanking)
  and as each record states it. The image hashes differ (`e6ac661c...9831`
  before, `9287a792...de9b` after) and are not compared: the app descriptor's
  version holds the commit (R4), which differs by design. Both images are
  1113360 bytes.
- **R4: PASS on both builds.** Read from each image's app descriptor by the
  tester's reader (`independent.py`): the version is `e7263bb` and `60d52f3`,
  each what `git describe --always --tags --dirty` gives in its clone and a
  bare commit (hex, a prefix of the commit, no `-dirty`). `project_name` is
  `wican-fw_obd_man` in both: it starts `wican-fw` and has no run of 7 or more
  hex digits.
- **R18: PASS on both builds.** The pinned `vtrux_checks options` (from the
  `2026.10.25` checkout) exits 0 on each, reading `build` and
  `build/bootloader`: all 10 options of the release profile at the profile's
  value; none differs in either folder's `config/sdkconfig.h`.
- **R21 (recorded, not part of the acceptance): PASS on both builds.** The pinned board
  check reads `vtrux_checks.json`'s `board.partition_table` relative to the
  WiCAN tree; since the move it is
  `../test/build_checks/reference/stock-v4.13-partition-table.bin`.
  On each build it exits 0: target `esp32c3`, the partition table identical to
  the reference, the smaller OTA slot 1781760 bytes, the image 1113360 bytes
  (668400 free), flash size 4MB in the app and bootloader headers, tool prefix
  `riscv32-esp-elf-`.
- **The harness at the move** (tester's range `e7263bb..143ed89`: `c6c7fe4`
  420 renames, all R100; `41868db` the paths; `143ed89` two faults the sweep
  found in `41868db`, each with a test). Run at a local merge of the tester's
  and the implementor's ranges (tree equal to `60d52f3`'s `gen-inhibit/`):
  - host suite on madhouse-debian (gcc 14.2) through `gi_host_run.sh`: 80 pass,
    0 fail (23 scenarios without goldens); `--checks` PASS, `--sched` PASS; the
    shim (E1) all cases pass and `test_signals` exit 0, both with
    `REVERSE_IT_DIR` set by hand (`gi_host_run.sh` does not export it for
    `--shim` and `--signals`: a harness defect from before the move, to be
    fixed on its own topic);
  - spec shim on madhouse-debian: `run_spec_shim --local --base e7263bb`
    VERDICT PASS (its `main/` tree equal to `e7263bb`'s), `mutate` 49 of 49
    caught, `test_host_env --mutants` 8 of 8;
  - the 49 bench, tools and build_checks unit tests and mutant runs the move
    touches, all PASS, among them `gi_paths` mutants 23 of 23, `gi_diag` 25 of
    25, `baseline_host_run` 23 of 23, `gi_host_run_list` 3 of 3,
    `suite_gen_inhibit` 302 ok and mutants 76 of 76, `baseline_b_desk` 45 ok and
    9 of 9, `reference_paths` 8 of 8 with the control passing;
  - `golden_schema_bump_check` reads all 80 goldens on both sides of the move.
- **Old run records keep the paths they were written with** (the harness at the
  repo root, `tools/gen_inhibit/...`, in a chain record's settings and argv).
  They are history and stay as written. Nothing reads a harness path back from
  a record: a re-judge runs the current tree's tools on the record's data, found
  through `REVERSE_IT_DIR`, and a git path a record names (`gi_diag`'s
  `dbc_path`) is read at the record's own commit, whose tree the move did not
  change.
- **Run files.** `runs/move0d_fw_60d52f3/` (`README.md`; `SHA256SUMS`
  `8feaeca0...3c2f` hashes every file: `results.json`, `driver.log`, the build,
  options and board logs, the driver, its launcher and maker, and the console),
  and `runs/move0d_fw_60d52f3.console.txt`.

**Port step 1a C: the board from its component -- tester, 2026-10-28,
`gen-inhibit/tester/board-1a` `b6bee3f` (`gen-inhibit/main` `7a91650`, the
implementor's C, plus the tester's harness), shared/tools `2026.10.27`
(`77e114f`). Result: the build checks PASS on the bench build; the release
build's R18, R21, R29 and R30 PASS; R17 FAILS on a clean build (F1, F2,
observed vs spec below).** The implementor's C (`8919fb3..3e73f65`, merged as
`7a91650`) takes the board as `vtrux/wican_v3` from git at tag `2026.10.27`
(lock commit `77e114f`), its partition table from the component, and the image
name's hardware label `obd` (spec 12.3 item 3); `shared_pins.json` names
`2026.10.27`. Sections: shared/boards B1, B2, B5, B6 and section 2; shared/tools
R16, R21, R29, R30, with R17's git clause and R18 by folder (ruled
2026-10-27); spec 12.3 item 3. Spec 3's restart and standby behaviour is
hardware: it is not tested here, and goes to the bench suite at step 1a's end.
- **The harness for C** (`8919fb3..b6bee3f`): `5981e60` takes the board from
  its component: `vtrux_checks.json` drops its `board` key (R21 reads the
  board component), and its release profile names the board's required
  settings (the console on the USB-Serial/JTAG, no secondary console,
  `CONFIG_BT_ENABLED=y`; spec 12.3 item 3 settings 6 and 7, `CONFIG_FREERTOS_HZ`
  now setting 8) and R30's `stock_files_using_pins` (`main/main.c`,
  `main/gi_restart_test.c`, the implementor's list); `board_ref.py` finds the
  checkout of the tag `main/idf_component.yml` pins the board at, from which
  `test_board_data.py` reads `board.json`; the stock partition table has one
  copy, the board's, which `build_output.py`'s partition row reads from the
  build's `managed_components/`. `f447156` adds two mutants they missed.
  `3487cab` drops R20's `HARDWARE_VER` and `WICAN_PRO` rows (gen-inhibit
  reviewer, 2026-10-27). `b6bee3f` merges `gen-inhibit/main` `7a91650`.
- **Build checks on the bench build of `b6bee3f`: PASS.** `idf.py build` in
  `gen-inhibit/firmware/` (exit 0, `wican-fw_obd_man.bin`, 1114464 bytes).
  The 10 `build_checks` unit tests each exit 0 with the real pin
  (`test_board_data`, `test_partition_row`, `test_image_rows`,
  `test_prod_record`, `test_flt_row`, `test_fw_version_row`,
  `test_reference_paths`, `test_build_switches`, `test_switch_rows`,
  `test_scratch_only`); 13 mutants of `board_ref.py`, `test_board_data.py` and
  `build_output.py`'s partition row, all caught. `build_output.py` on the
  image: every row with a reference passes, among them the partition table
  equal to the board's (sha256 `98d193a6...`), the image in the OTA slot
  (62.5 %), `project_name` `wican-fw_obd_man`, and every build-time switch
  classified (24 symbols, no stale entry); the prod-build record row is NO REF,
  as this is a bench build, not a pass. R20's rows: the committed table is
  clean, and with `8919fb3`'s two rows put back the scan names both stale
  (`r20_rows_back`). `elf_checks.py`: no forbidden symbol linked.
- **Release builds of C (shared/tools `VERIFICATION.md`, entry "Real builds of
  gen-inhibit's step 1a C", on `shared/tools/fixture-layout` `52d7a9a`).**
  `release_cases.py`'s fixture made from `b6bee3f`, built by the
  `2026.10.27` tooling, three times, code hash `ee41cace...` each time. R18 by
  folder, R29 (the board's fixed and required settings, folder by folder), R30
  and R21 on the board the build downloaded pass, and each planted case fails
  naming what was planted. R28 fails on the three log levels, known anomaly 5,
  unchanged.
- **Observed vs spec, shared/tools R17**, on the fixture's clean release build
  and on the tester's bench build of `b6bee3f` alike, `--release` and
  `--bench`:
  - **F1.** The build reads the repository's `.git/HEAD` and the checked-out
    branch's ref, outside the project root (`gen-inhibit/firmware/` since port
    step 0d), IDF and `managed_components/`, and the check fails on them. R17:
    every file the build read comes from the project root, IDF or
    `managed_components/`. Not seen on a minimal project whose `.git` is in its
    project root.
  - **F2.** The check reports `vtrux/wican_v3` taken from git and pinned
    nowhere. `main/idf_component.yml` names it with `git`, `path:
    shared/boards/wican_v3` and `version: "2026.10.27"`; that tag resolves
    under `refs/tags/` to `77e114f`, the lock's commit; the downloaded
    component's manifest has no `version`. R17's git clause: the lock's commit
    is the one the tag the project pins names, and the manifest has no
    `version`. The release tooling reports the same component pinned (R16).
- **Run files**, under `runs/`: `c_board-1a_b6bee3f_20261028/` (`SHA256SUMS`
  `68593758...e7ec`: the bench build log, the 10 tests, the mutants with their
  script, `build_output`, `elf_checks`, `r20_rows_back` with its script);
  `c1a_fixture_80e32a4_20261028/` (`6080e21b...89fd`, the fixture's first
  release build); `c1a_realbuild_0df0757_20261028/` (`cb3ea1c0...3f49`),
  `c1a_realbuild_e839370_20261028/` (`343a398c...69f4`) and
  `c1a_realbuild_ccdd525_20261028/` (`f531f3c0...63e3`, with
  `realtree_scope_b6bee3f.txt` and `realtree_pin_b6bee3f.txt`). Host
  NICKY-XPS, IDF v5.4.1; no wall-clock test running, bench check clear.

**Port step 1a, the rest: the stock WiCAN code from `vtrux/wican`, build
checks and release stand-in -- tester, 2026-10-29, `gen-inhibit/main`
`bc7d086` (product code the implementor's `7ad8c14`), shared/tools
`2026.10.29` (`828ed93`). Result: PASS, but for R28, known anomaly 5
until port step 1a2.** The implementor's C (`0b03841..7ad8c14`, merged as
`a287e1c`) takes the stock code from `vtrux/wican` and `vtrux/debug_logs` and
the board `vtrux/wican_v3`, all at tag `2026.10.29` (spec 3.3; shared/wican
W1-W11). The tester's C (`23118ac`) moves the build checks with it; the
reviewer merged both as `bc7d086` (tree = `23118ac`'s). Sections: spec 12.3
item 3 (the `WICAN_SLCAN_COMMANDS` switch, "stock feature off in this
product", with the same image-refusal row as the four feature switches);
shared/tools R16, R20 (each shared component's own table), R21, R30 (a shared
component's own `pins.json`), R28 recorded.
- **The harness for C, `23118ac`:**
  - `vtrux_checks.json`: the entries the stock code tests leave the project's
    table (`WICAN_FEATURE_*` x4, `OTA_HEALTH_TIMEOUT_MS`, `DEBUG_LOGS_*` x6,
    `ADC_CALI_*` x2), now in `vtrux/wican`'s and `vtrux/debug_logs`' own
    tables at `2026.10.29`; `OTA_HEALTH_FAULT_INJECT` stays for `_flt`.
    `stock_files_using_pins` is `main/gi_restart_test.c` only.
  - `build_output.py`: "one transmit owner" and "the private TWAI abort is
    confined" scan `main/` and every file the build compiled from a shared
    component `vtrux_build.json` names (gen-inhibit reviewer, 2026-10-29), NO
    REF when that record can't be read; "WiFi storage is RAM-only" reads
    `vtrux/wican`'s `wifi_network.c`; the RealDash row is "no boot-time bus
    enable at start-up", counting `can_enable` in `app_main`, `wican_init` and
    `wican_start`.
  - `test_refusal_pairs.py` takes its switches from the project's and every
    shared component's table.
  - The spec shim's `build.sh` puts `vtrux/wican`'s headers on the include
    path.
  - Tests, re-run at `8829425` (`runs/step1a_bc7d086/harness_tests/`):
    `test_source_rows` (new) 24 ok, `test_refusal_tables` (new) 8 ok,
    `test_switch_rows` 68 ok, `test_build_switches` 78 ok;
    `source_rows_mutants.py` 20 of 20 killed, 4 controls passing.
- **Build checks on the release stand-in's build folder (below), with
  `bc7d086`'s own `build_output.py` and the `2026.10.29` `vtrux_checks`:
  PASS but for R28.** NICKY-XPS, Idle priority, bench lock free, no
  simulator running.
  - `build_output.py` (`--records` the stand-in's record folder): every row
    `[ ok ]`, 49 rows, no FAIL, no NO REF. Among them: "WiFi storage is
    RAM-only" from `vtrux/wican`'s `wifi_network.c`; "one transmit owner"
    (2 call sites in 55 files) and "the private TWAI abort is confined"
    (one site, `gen_inhibit.c`) over the shipping source; "no boot-time bus
    enable at start-up" (none of `app_main`, `wican_init`, `wican_start`
    calls `can_enable`); every build-time switch classified (11 tested, all
    listed), no stale entry, every test-only switch's refusal rows passing
    on the image (5); the prod-build record present (`71f70ab2...`, built
    from `bc7d086` by shared/tools `2026.10.29`, the image beside it
    identical).
  - **R20: PASS.** The project's table 11 tested, 11 listed; `vtrux/debug_logs`
    7 and 7; `vtrux/wican` 13 and 13 (`WICAN_SLCAN_COMMANDS` and the four
    `WICAN_FEATURE_*` as "stock feature off in this product"); the board
    `vtrux/wican_v3` 1 and 1; `espressif__mdns` 14 tested, no table, not
    judged.
  - **R30: PASS.** The project's list `main/gi_restart_test.c` (reads no
    pins); `vtrux/wican`'s `pins.json` 1 file, `wican.c`, which the build read
    it for; `vtrux/debug_logs` 0.
  - **R21: PASS** (partition table identical to the board's reference, slot
    1781760 bytes, image 1115088, 666672 free, 4MB, esp32c3). **R17
    (`scope --release`): PASS**, the three shared components from git at
    lock commit `828ed934...`, pinned `2026.10.29`. **R18, R26, R27, R29:
    PASS.**
  - **R28: FAIL**, the three log levels (`CONFIG_BOOTLOADER_LOG_LEVEL` 3,
    want 1; `CONFIG_LOG_DEFAULT_LEVEL` and `CONFIG_LOG_MAXIMUM_LEVEL` 3, want
    0, in `build` and `build/bootloader`): known anomaly 5, unchanged, until
    port step 1a2 (reviewer, 2026-10-29).
- **The images and the release stand-in.** Built at `bc7d086` in a detached
  worktree, clean, NICKY-XPS, IDF v5.4.1, Idle priority, no wall-clock test
  running: `_man` `71f70ab2...`, `_rt` `4506bc58...`, `_txabort`
  `dcfc20b0...`, `_abortwin` `0ca48018...`, `_rxq` `9939e408...`, each
  version `bc7d086`, each exit 0. **Prod: a release build standing in for
  prod** (as at step 0c): the `2026.10.29` tooling's release build of
  `bc7d086` in a second clean worktree, exit 0, every row `[ ok ]`: tooling pin
  (R23, R16) `2026.10.29` at `828ed934f19b`; worktree clean at the start,
  after the reconfigure and at the end (R8); compiler cache off, no launcher,
  the toolchain's compilers (R9); `vtrux__debug_logs`, `vtrux__wican` and
  `vtrux__wican_v3` each matching the lock (R16, pinned); one board (R21);
  image named by the build (R11). Image hash `71f70ab2...`, the same as the
  bench `_man` (builds reproducible); code hash `4f9ab365...`. Its record
  went to a stand-in reverse-it folder, not `firmware/builds/`. A test image,
  never flashed to the truck, and not a prod build in spec 12.3 item 3's
  sense.
- **Run files:** `runs/step1a_bc7d086/` (`README.md`; `SHA256SUMS`
  `9a97ecf4...f811`): `builds/` (the five image logs, the release log
  `release_prodstandin.txt`, `driver.log`), `build_checks/`
  (`build_output_prod1a.txt`, `vtrux_checks_{switches,pins,board,options,scope}.txt`)
  and `harness_tests/`.

**Port step 1a2: release log levels (R28) and the fault rows -- tester,
2026-10-30, `gen-inhibit/main` `17ec04c`, shared/tools `2026.10.30`
(`92364d6`). Result: PASS; R28 PASS, so known anomaly 5 closes.** The
implementor's `c48e05b` puts gen-inhibit's release and bench builds at
shared/tools R28's log levels (app none, bootloader error), and test-only
builds keep info through `sdkconfig.log_info`; `7e9e2a6` re-pins
`vtrux/wican`, `vtrux/debug_logs`, `vtrux/wican_v3` and shared/tools to
`2026.10.30`. The tester's harness for it is `fbb3aa7`, `a2bfe92` and
`fc0274b`, on `gen-inhibit/tester/r28-levels`. The reviewer merged all of
it as `17ec04c`. Sections: spec 12.3 item 3 (release log levels) and
shared/tools R28, plus R16, R17, R18, R20, R21, R26, R27, R29 and R30 re-run.
- **The harness for 1a2:**
  - `fbb3aa7`: the rollback rehearsal at log level none (gen-inhibit
    reviewer, 2026-10-30, rulings 1, 2 and 5). It finds boots from the ROM
    banner; the control is judged by its effect: the page names the good
    image past the gate's timeout and again after a reboot. The fault phase
    reads two ROM banners 55-75 s apart. The bootloader phase records the
    bootloader's sha256 and the log level its strings show. `reset_matrix`
    notes that a ROM-banner-only capture gives a lower bound on the boot
    count.
  - `a2bfe92`: the rehearsal's otadata and bootloader phases compare
    esptool's MAC value-blind, and keep only `mac_matches`.
  - `fc0274b`: the fault rows judge the hook's symbol, not its log string
    (ruling 4). At R28's levels the string "FAULT INJECT: suppressing report
    of" is not compiled in even with `OTA_HEALTH_FAULT_INJECT` on, so the
    old rows passed on a fault build. `vtrux_checks.json`'s
    `OTA_HEALTH_FAULT_INJECT` row is now `elf_symbols_absent` on
    `ota_health_fault_inject_suppress` (shared/wican W7), keeping
    `project_name_token_absent` `_flt`. The row in `build_output.py` ("no OTA
    fault injection in the image") reads the same symbol, and an empty nm
    listing reads NO REF. `judge_images` in the rehearsal now has "good ELF
    does NOT define the fault hook" and "faulty ELF defines the fault hook".
  - **The proof on real builds** (`runs/step1a2_17ec04c/r28_fault_rows/`):
    three images built at `fc0274b` in a scratch worktree, Idle priority:
    `_man` (none/none), `_flt` at info, and `_flt` at none (the fault switch
    on at release levels, with `sdkconfig.log_info` swapped for `sdkconfig`
    in that build only, restored after; version `fc0274b-dirty`). Each build
    exited 0.
    - `build_output.py`'s row: `_man` ok; both `_flt` builds FAIL by the
      symbol and the `_flt` name. The old string is absent from the `_flt`
      build at none, so the old row would have passed it.
    - `judge_images` (`_man`, `_flt` at info): all 10 rows pass. Each
      swapped pair fails the hook rows.
    - The `vtrux_checks.json` rule through shared/tools: on FAILS for both
      `_flt` builds; off passes.
  - Tests, re-run at `17ec04c` (`runs/step1a2_17ec04c/harness_tests/`):
    `test_fault_hook_row` 12 ok, `--mutants` 9 of 9, the control passing; `test_flt_row` 6 ok;
    `test_ota_rehearsal_images` 14 ok, `--mutants` 9 of 9 (this runner has no control run; the unmutated test passes at the same tip); `test_ota_rehearsal_none` 72
    ok, its mutants 24 of 24, the control passing;
    `test_ota_rehearsal_identity` 51 ok, its mutants 12 of 12, the control
    passing;
    `test_reset_matrix` 46 ok. The build-check tests again:
    `test_source_rows` 24 ok, `test_refusal_tables` 8, `test_switch_rows`
    69, `test_build_switches` 79; `source_rows_mutants.py` 20 of 20 killed,
    4 controls passing.
- **Build checks on the release stand-in's build folder (below), with
  `17ec04c`'s own `build_output.py` and the `2026.10.30` `vtrux_checks`:
  PASS.** NICKY-XPS, Idle priority, bench lock free, no simulator running.
  - `build_output.py` (`--records` the stand-in's record folder): every row
    `[ ok ]`, 49 rows, no FAIL, no NO REF. They include "no OTA fault
    injection in the image" (the hook not defined in the ELF) and the
    prod-build record (`1a07454e...`, built from `17ec04c` by shared/tools
    `2026.10.30`, the image beside it identical). Also every build-time
    switch classified (11 tested, all listed) and every test-only switch's
    refusal rows passing on the image (5).
  - **R20: PASS.** The project's table 11 tested, 11 listed;
    `vtrux/debug_logs` 7 and 7; `vtrux/wican` 13 and 13; the board 1 and 1;
    `espressif__mdns` 14 tested, no table, not judged.
  - **R30: PASS.** The project's list `main/gi_restart_test.c` (reads no
    pins); `vtrux/wican`'s `pins.json` 1 file, `wican.c`; `vtrux/debug_logs`
    0.
  - **R21: PASS** (partition table identical to the board's reference, slot
    1781760 bytes, image 1016208, 765552 free). **R17 (`scope --release`):
    PASS**, the three shared components from git at lock commit
    `92364d6a3466...`, pinned `2026.10.30`. **R18, R26, R27, R29: PASS.**
  - **R28: PASS.** `CONFIG_BOOTLOADER_LOG_LEVEL` 1, want 1;
    `CONFIG_LOG_DEFAULT_LEVEL` and `CONFIG_LOG_MAXIMUM_LEVEL` 0, want 0; in
    both `build` and `build/bootloader`. The same check failed at `bc7d086`
    (12.3, "Port step 1a, the rest"), so it can fail. **Known anomaly 5
    closes.**
- **The images and the release stand-in.** Built at `17ec04c` in a detached
  worktree, clean, NICKY-XPS, IDF v5.4.1, Idle priority, no wall-clock test
  running: `_man` `1a07454e...`, `_rt` `0802da33...`, `_txabort`
  `58eed9ba...`, `_abortwin` `26e2f547...`, `_rxq` `ee9b9132...`, each
  version `17ec04c`, each exit 0. **Prod: a release build standing in for
  prod** (as at step 1a): the `2026.10.30` tooling's release build of
  `17ec04c` in a second clean worktree, exit 0, every row `[ ok ]`:
  - tooling pin (R23, R16) `2026.10.30` at `92364d6a3466`;
  - worktree clean at the start, after the reconfigure and at the end (R8);
  - compiler cache off, no launcher, the toolchain's compilers (R9);
  - `vtrux__debug_logs`, `vtrux__wican` and `vtrux__wican_v3` each matching
    the lock (R16, pinned);
  - one board (R21);
  - image named by the build (R11).

  Image hash `1a07454e...`, the same as the bench `_man` (builds
  reproducible); code hash `2ab1a707...`. Its record went to a stand-in
  reverse-it folder, not `firmware/builds/`. This is a test image, never
  flashed to the truck, and not a prod build in spec 12.3 item 3's sense.
- **Run files:** `runs/step1a2_17ec04c/` (`README.md`; `SHA256SUMS`
  `99db7428...4452`): `builds/`, `build_checks/`, `r28_fault_rows/` and
  `harness_tests/`.

**Known anomaly 8: the refusal-pair proof on post-port images, with
`WICAN_SLCAN_COMMANDS` -- tester, 2026-10-30/31, `gen-inhibit/main`
`683502e` (contains `dd8e57f`). Result: PASS.** The reviewer's post-merge
assignment (2026-10-30). Three parts:
- an image set with an SLCAN image;
- the SLCAN entry in the refusal-pair runner;
- every rule of the project's and the pinned components' tables shown failing
  on its own switch's image.

Then the refusal-pair run and the `build_checks_stock_mutants.py` reading on
that set. The harness is on `gen-inhibit/tester/refusal-pairs-postport`,
`683502e..5d11983`. Sections: spec 12.3 item 3 (test-only builds and the
stock features off in this product are refused). Also shared/tools R20, whose
table rules the proof now judges on real images.
- **The harness:**
  - `848a149`: `baseline_builds.ps1` gains `fslcan`
    (`-DWICAN_SLCAN_COMMANDS=ON`), and `refusal_pairs_runs.py`'s `ON` maps
    the switch to it. New `test_refusal_pairs_runs.py` (11 ok) checks that
    every test-only switch and stock feature, in the project's table and in
    each pinned component's table, has an `ON` name and a build that sets it,
    that every `CONTROL` build leaves its switch off, and that no `ON` entry
    is stale. Each check is also run on a broken input it must fail. It
    failed before the `ON` entry was added.
  - `e99236e`: `test_refusal_pairs.py` also judges each switch's own table
    rules, from every table that lists it, as shared/tools R20 reads them:
    - each rule FAILs on the switch's ON image and PASSes on the OFF image
      and on its control;
    - a `project_row` rule must name a row the run produced, failing on the
      ON image;
    - a switch with no rules fails.

    Until then `WICAN_SLCAN_COMMANDS` had no rows there. No row of
    `build_output.py` names it; its only refusal rule is vtrux/wican's
    `elf_symbols_absent slcan_parse_str`. It is in `ROWS` with no row
    function. Tested by:
    - `test_refusal_tables.py`, on made-up tables and facts;
    - `test_refusal_pairs_mutants.py`, with the unmutated copy first as the
      control;
    - five more negatives in `refusal_pairs_runs.py`, 16 in all.
  - `8933e8a`: **a harness defect the first run found.** That run (harness
    `676db6d`, 2026-10-31) crashed in `test_refusal_pairs.py`'s `main()`
    before any check: it called SLCAN's row function, which is None. The
    positive and all 16 negatives died on the same `TypeError`, so nothing
    was judged. `run()` now gives no rows for a switch with no row function.
    A new check in `test_refusal_tables.py` covers it, and a ninth mutant
    that removes the guard is killed by that check.
  - `9474b60`: `source_rows_mutants.py`'s r06 is re-pointed at
    `tables()`. `e99236e` had moved the line it edited, so r06 matched
    nothing at `8933e8a`.
  - Tests (`harness_tests/`, at `8933e8a`):
    - `test_refusal_tables` 24 ok;
    - `test_refusal_pairs_mutants` 9 of 9 killed, the control passing;
    - `test_refusal_pairs_runs` 11 ok;
    - `test_switch_rows` 69, `test_build_switches` 79 and `test_source_rows`
      24 ok;
    - `test_scratch_only` 44 ok;
    - `source_rows_mutants` (at `9474b60`) 20 of 20 killed, 4 controls
      passing.
- **The images.** 12 builds by `baseline_builds.ps1`, made 2026-10-30 22:13
  to 23:58:
  - from a detached worktree of `683502e` made from the anchor, clean;
  - NICKY-XPS, IDF v5.4.1, Idle priority;
  - the bench lock free, "No builds" cleared, no simulator running.

  Each build exited 0 with one app image, version `683502e`, and the
  descriptor's ELF SHA-256 equal to the ELF's (`baseline_images_record.py`).
  File sha256:
  - `_man` `8948ca35...`, `_rt` `1f4672e1...`, `_rxq` `7d26d9af...`;
  - `_txabort` `8ee457d5...`, `_abortwin` `cbb114a6...`, `_flt`
    `205c2f63...`;
  - `fmqtt` `de5a9349...`, `felm` `bae874fd...`, `fmqttelm` `42ae937f...`;
  - `fautopid` `c38a0227...`, `frealdash` `53fa499d...`, `fslcan`
    `d9612078...`.

  These are test images, never flashed.
- **The refusal-pair run (`refusal_pairs_runs.py --tree`, harness `8933e8a`):
  PASS.** The positive run passes 86 checks. 44 of them are the tables' own
  rules: 21 rules over 11 switches, each failing on its switch's ON image and
  passing on the OFF image, plus AutoPID's two passing on its control
  (`fmqttelm`).
  - The project's table: `GI_INSTRUMENT_ABORTWIN` 2, `GI_INSTRUMENT_RXQ` 2,
    `GI_INSTRUMENT_TXABORT` 2, `GI_TEST_RESTART_QUIESCE` 3,
    `OTA_HEALTH_FAULT_INJECT` 2.
  - vtrux/wican's table: `OTA_HEALTH_FAULT_INJECT` 1,
    `WICAN_FEATURE_AUTOPID` 2, `WICAN_FEATURE_ELM327` 2,
    `WICAN_FEATURE_MQTT` 3, `WICAN_FEATURE_REALDASH` 1 (`project_row` "no
    boot-time bus enable at start-up"), `WICAN_SLCAN_COMMANDS` 1
    (`slcan_parse_str` defined in `fslcan`'s ELF, not in `_man`'s).
  - vtrux/debug_logs' and the board's tables list no test-only switch or
    stock feature.

  **16 of 16 negatives caught on their own check.** Among them:
  - the product image as SLCAN's ON;
  - the SLCAN image as OFF;
  - the product image as RXQ's ON (a project-table rule);
  - the product image as RealDash's ON (a `project_row` rule);
  - AutoPID's own image as its control.
- **`build_checks_stock_mutants.py --tree` on the same images (harness
  `8933e8a`): the control passes, 16 of 16 killed.** This is its first reading
  at this harness: on the `e3d9437` images its control failed, and no image
  set had an SLCAN image.
- **Run files:** `runs/refusal_pairs_683502e/` (`README.md`; `SHA256SUMS`
  `83dedcbb...f6af`):
  - `builds/`, `images_683502e.json`;
  - `refusal_pairs_683502e{,.console}.txt`;
  - the crashed first run, `refusal_pairs_683502e_harness676db6d_crash{,.console}.txt`;
  - `build_checks_stock_mutants_683502e.txt`;
  - `harness_tests/`.

**Port step 1b: build checks and the release stand-in on `488ef9f` --
tester, 2026-10-31, `gen-inhibit/main` `488ef9f`, shared/tools and the CAN
components at `2026.10.31.2` (`1fda69d`). Result: PASS.** At 1b,
`firmware/main/can.c` is gone. The CAN code is the shared `vtrux/can_port`
and `vtrux/twai_port`. Sections: spec 12.3 (build-output checks, E3) and
shared/tools R16, R17, R18, R20, R21, R26, R27, R28, R29 and R30, re-run.
- **The images and the release stand-in.** Built at `488ef9f` in a detached
  worktree, clean, NICKY-XPS, IDF v5.4.1, Idle priority, no wall-clock test
  running. Each exited 0. ELF sha256 (what the page reports): `_man`
  `cbfc683a...`, `_rt` `786d808a...`, `_txabort` `008f26ff...`,
  `_abortwin` `1bd5a67f...`, `_rxq` `a6c5365e...`. **Prod: a release build
  standing in for prod** (as at step 1a2): the `2026.10.31.2` tooling's
  release build of `488ef9f` in a second clean worktree, exit 0, 27 rows,
  every row `[ ok ]`. Image hash `cbfc683a...`, the same as the bench
  `_man` (builds reproducible); code hash `92411a20...`. Its record went
  to a stand-in reverse-it folder, not `firmware/builds/`. This is a test
  image, never flashed to the truck, and not a prod build in spec 12.3
  item 3's sense.
- **Build checks on the release stand-in's build folder, with `488ef9f`'s
  own `build_output.py` and the `2026.10.31.2` `vtrux_checks`: PASS.**
  NICKY-XPS, Idle priority, bench lock free, no simulator running.
  - `build_output.py` (`--records` the stand-in's record folder): 49 rows,
    every row `[ ok ]`, no FAIL, no NO REF. The rows are 1a2's, with the
    renames the port makes: `CAN_RX_QUEUE_LEN` is now
    `GEN_INHIBIT_RX_QUEUE_LEN`, and its override is no longer in
    `main/can.c`. The head and the image size changed too.
  - `vtrux_checks` (checkout at `1fda69d`, clean): `switches`, `pins`,
    `board`, `options` and `scope`, all with `--release`, each exit 0.
    R20, R30, R21, R18, R26, R27, R28, R29 and R17 (5775 files): PASS.
- **Harness:** `gen-inhibit/tester/accept-1b` `8bc82f1`. There are no
  build-check harness changes for 1b. Its tests, at `8bc82f1`, are in the
  acceptance entry (12.4, "Port step 1b's acceptance").
- **Run files:** `runs/step1b_488ef9f/` (`README.md`; `SHA256SUMS`
  `21580d1c...c0dd`): `builds/` (with the stand-in's record,
  `prod_standin_ri/`) and `build_checks/`.

**Port step 1c: build checks, the release stand-in and the R20/R30
can-fails on `5f2fe2c` -- tester, 2026-11-02, `gen-inhibit/main`
`5f2fe2c`, shared/tools and the six shared dependencies at `2026.11.01.2`
(`b33bce5`). Result: PASS, with switches `release_pass` VOID from a harness
name lag (below).** At 1c, gen-inhibit takes shared/main `2026.11.01.2`,
with the switch names `RESTART_GUARD_TEST` and `TWAI_PORT_TXABORT_PROBE`.
Sections: spec 12.3 (build-output checks, E3) and shared/tools R16, R17,
R18, R20, R21, R26, R27, R28, R29 and R30, re-run.
- **The images and the release stand-in.** Built at `5f2fe2c` in a detached
  worktree, clean at both ends, NICKY-XPS, IDF v5.4.1, Idle priority, no
  wall-clock test running. Each exited 0. ELF sha256 (what the page
  reports): `_man` `47e805f1...`, `_rt` `7a00c4e6...`, `_txabort`
  `cc18cf02...`, `_abortwin` `e7148cba...`, `_rxq` `7aac1135...`; and the
  ON images `test_refusal_pairs` proves over: `flt` `e02bf3d1...`, `fmqtt`
  `8e7c6b8d...`, `felm` `e326ebdd...`, `fmqttelm` `1109f644...`,
  `fautopid` `8ca28075...`, `frealdash` `51d99d38...`, `fslcan`
  `8d39119c...`. **Prod: a release build standing in for prod** (as at
  steps 1a2 and 1b): the `2026.11.01.2` tooling's release build of
  `5f2fe2c` in a second clean worktree, exit 0, 28 rows, every row
  `[ ok ]`: 1b's 27 and `vtrux__restart_guard`'s component hash. Image hash
  `47e805f1...`, the same as the bench `_man` (builds reproducible); code
  hash `17d448ba...`. Its record went to a stand-in reverse-it folder, not
  `firmware/builds/`. This is a test image, never flashed to the truck, and
  not a prod build in spec 12.3 item 3's sense.
- **Build checks on the release stand-in's build folder, with `5f2fe2c`'s
  own `build_output.py` and the `2026.11.01.2` `vtrux_checks`: PASS.**
  NICKY-XPS, Idle priority, bench lock free, no simulator running.
  - `build_output.py` (`--records` the stand-in's record folder): exit 0,
    every row `[ ok ]`, "every check with a reference passed".
  - `vtrux_checks` (checkout at `b33bce5`, clean): `switches`, `pins`,
    `board`, `options` and `scope`, all with `--release`, each exit 0.
    R20, R30, R21, R18, R26, R27, R28, R29 and R17: PASS. This is the
    positive R20.
  - On the bench build folders: `switches` on `_rt` and on `_txabort`
    exit 1, FAIL R20, each naming the switch's refusal rules in the project
    table and in `vtrux__restart_guard`'s or `vtrux__twai_port`'s table.
    `pins` on each: PASS R30 (on `_rt`, `restart_guard_test.c` reads the
    pins through `vtrux/restart_guard`). Neither is a committed can-fail
    mode; they are the checks on real images. The committed can-fails are
    below.
- **R20 and R30 can-fails, `2026.11.01.2`'s committed modes**
  (`shared/tools/verification/`, checkout `b33bce5`, clean at both ends,
  `--base 5f2fe2c`, under the IDF venv's Python 3.11.9):
  - **`switches_cases`:** self-test exit 0. `on_restart` PASS and
    `on_txabort` PASS: exit 1, naming the project's `RESTART_GUARD_TEST`
    refusal and `vtrux__restart_guard`'s, and the project's
    `TWAI_PORT_TXABORT_PROBE` refusal and `vtrux__twai_port`'s. These are
    R20's can-fails.
  - **`switches_cases` `release_pass`: VOID**, "the project's rows file
    lacks a passing RealDash row: None". `switches_cases.py` (l.270 at
    `b33bce5`) looks up the row "no boot-time bus enable in app_main".
    `build_output.py` has emitted it as "no boot-time bus enable at
    start-up" since `23118ac` (port step 1a part C), and the self-test's
    fake uses the old name, so it can't catch the rename. A harness name
    lag, not a product result (the reviewer, 2026-11-02). The positive R20
    is the release check above. A shared/tools fix follows as its own
    range.
  - **The IDF venv.** `5f2fe2c`'s `build_output.py --bin` imports
    `cantools` (since `5c8c5e1`), and `switches_cases` runs it under the
    venv's Python. The first switches run was VOID because the venv lacked
    it (`verification/`). By the user's ruling (2026-11-02), `cantools`
    went into `idf5.4_py3.11_env` to stay, at py -3.14's versions:
    `cantools` 44.1.0, `argparse-addons` 0.12.0, `bitstruct` 8.23.0,
    `crccheck` 1.3.1, `python-can` 4.6.1, `textparser` 0.26.2, `wrapt`
    1.17.3; `packaging` 26.3 and `typing_extensions` 4.16.0 were there
    already. The switches cases then ran again on the same fixture
    (`verification_switches_cantools/`).
  - **`pins_cases`:** self-test exit 0. `release_pass` PASS (PASS R30);
    `comp_unlisted` and `comp_no_list` PASS (exit 1, naming
    `managed_components/vtrux__wican/wican.c`). `unlisted`: n/a to
    gen-inhibit, VOID "no file on the project's list reads the board's
    pins".
  - An earlier attempt from the shell that had run the builds, with the
    global 3.11 and no `esp_idf_monitor`, half-made the fixture and counts
    for nothing (`verification.attempt0_idf_env/`).
- **`test_refusal_pairs`** (`26acc04`'s `refusal_pairs_runs.py`, `--sha
  5f2fe2c`, every ON image): "positive PASS; 16 of 16 negatives caught on
  their own check".
- **Harness:** `gen-inhibit/tester/accept-1c` `26acc04`. Its one change,
  to `bench_compare.py`, is in the acceptance entry (12.4, "Port step 1c's
  acceptance"). No build-check harness changes.
- **Run files:** `runs/step1c_5f2fe2c/` (`README.md`; `SHA256SUMS`
  `ef7307e0...39e8`): `builds/` (with the stand-in's record,
  `prod_standin_ri/`), `build_checks/`, `verification.attempt0_idf_env/`,
  `verification/` and `verification_switches_cantools/`.

**Port step 1d: R19 on ESP-IDF v5.4.2, the release stand-in, the build
checks and the R8 sdkconfig cases on `7f2d503` -- tester, 2026-11-04,
`gen-inhibit/main` `7f2d5032`, shared/tools and the shared dependencies at
`2026.11.04` (`181cec94`), the verification at shared/main `a3b0f250` and
its two fixes (below). Result: PASS.** At 1d gen-inhibit moves to ESP-IDF
v5.4.2, its own install and tools folder, with the venv's Python 3.11.9.
Plan: `$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/step1d_test_plan.md` items 1, 2 and 6
(reviewer-confirmed, 2026-11-04). Sections: spec 12.3 (build-output checks,
E3) and shared/tools R8, R16, R17, R18, R19, R20, R21, R26, R27, R28, R29
and R30. Every build ran on NICKY-XPS at Idle priority with no wall-clock
test running.
- **R19, gen-inhibit's own run (item 1a): PASS.** `7f2d5032` cloned twice
  under the tester worktree's `scratch/`, into folders of different length
  and depth, each with its own `../shared-2026.11.04/` beside it, each built
  once by `py -3.14 -B gen-inhibit/vtrux_tools.py release`, with its records
  to a stand-in reverse-it folder. Both exit 0, clean before and after, no
  `build/` before, reproducible build on. Image hash `f515a383...151b` the
  same three ways (the record, `vtrux_tools.py image`, `a3b0f250`'s
  `independent.py`) in each build and across the two; the `.bin` files
  byte-identical (sha256 `68787728...`, 1021520 bytes); code hash
  `bc49dce0...` in both.
- **R18 on both builds: PASS.** The pinned `vtrux_checks options` exit 0 on
  each, judged by `a3b0f250`'s `options_cases`: R18, R26, R27 and R28 PASS,
  no expected failure, in both build folders (`build`, `build/bootloader`).
  A first judgement called `options_cases.expected()` in its old form and
  means nothing (`r19_own/`, kept); `r18_rejudge/` is the judgement.
- **R19, the tooling's rerun (item 1b): PASS.** `repro_cases.py` from a
  clean detached checkout of shared/main `a3b0f250` (the `2026.11.04` pin
  plus R32's padded-path refusal and R33's configured-IDF check), under the
  v5.4.2 venv, `--base 7f2d5032`: `fixture`, `mini` and `time_macro` PASS
  (R19 met); `build_path` PASS (R19 not met, the can-fail).
  - `repro_off` was FAIL at `a3b0f250`: R19 not met, where the case wanted
    it met. The two `.bin` files differ only in the descriptor's
    `elf_sha256`, with 0 bytes outside it and R2's blanks, as at `3543457`
    under v5.4.1. The case's premise, gen-inhibit building with
    reproducible build off (until its port step 0b), no longer held.
  - By the gen-inhibit reviewer's ruling (2026-11-04), the case keeps its
    id and wants R19 not met, a second can-fail beside `build_path` (R5's
    basis in shared/tools `VALIDATION.md`): `repro_cases.py` at `f231b334`
    (shared/main `569d0031`). `repro_off` was rerun alone from `1cbe0da5`,
    whose code is the same as `f231b334`'s (`f231b334` changes only the
    docstring's and the README's wording). The self-test exit 0, and a
    mutant with `repro_off` back to wanting R19 met was caught. Then
    **PASS**: R19 not met, image hashes `c127406e...` and `a56ccff5...`,
    the `.bin` files differing only in the descriptor's `elf_sha256`.
  - 1b is judged on `fixture`, `mini`, `time_macro`, `build_path` and this
    rerun. The first `repro_off` FAIL stays in its folder as a stale
    premise.
- **The release stand-in** is 1a's first clone's release build (image hash
  `f515a383...`), its record in a stand-in folder, not `firmware/builds/`.
  It is a test image, not a prod build in spec 12.3 item 3's sense. It went
  on the bench WiCAN for the acceptance (12.4, "Port step 1d's
  acceptance"), never on the truck.
- **The 12 images (item 2).** Built at `7f2d5032` by the tester branch's
  `baseline_builds.ps1` (`check-env` first, exit 0 each) in a clean
  detached worktree, `gen-inhibit-tester-images1d`, clean at both ends,
  each exit 0. ELF sha256: `_man` `f515a383...` (the stand-in's image hash),
  `_rt` `6ab7bc79...`, `_txabort` `ca8fd54a...`, `_abortwin` `6c4047d3...`,
  `_rxq` `ea3c0c05...`; the ON images `flt` `851837ce...`, `fmqtt`
  `9e7b4919...`, `felm` `f1fa8829...`, `fmqttelm` `203b556e...`,
  `fautopid` `04d9c0d7...`, `frealdash` `2ebc0881...`, `fslcan`
  `2b30f8ac...`. A first attempt built nothing and counts for nothing
  (`builds/`, kept): its driver exported v5.4.2 and then ran
  `baseline_builds.ps1`, which exports again, and the second export put the
  base Python ahead of the venv's, so `idf.py` stopped at once ("No module
  named esp_idf_monitor").
- **Build checks on the release stand-in's build folder, with `7f2d5032`'s
  own `build_output.py` and the `2026.11.04` `vtrux_checks`: PASS.**
  - `build_output.py` (`--records` the stand-in's record folder): exit 0,
    "every check with a reference passed".
  - `vtrux_checks` (checkout at `181cec94`, clean): `switches` PASS R20,
    `pins` PASS R30, `board` PASS R21, `options` PASS R18, R26, R27, R28
    and R29, each with `--build build` as at 1c; `scope --release` PASS
    R17. At `2026.11.04` only `scope` takes `--release` (the plan's "all
    `--release`" was a slip). The first run of the other four with it
    stopped on the argument error and counts for nothing (`build_checks/`,
    kept); `build_checks2/` is the run.
  - On the bench build folders: `switches` on `_rt` and on `_txabort` exit
    1, refusing `RESTART_GUARD_TEST` (the project's row and
    `vtrux__restart_guard`'s) and `TWAI_PORT_TXABORT_PROBE` (the project's
    and `vtrux__twai_port`'s), plus the `WICAN_FEATURE_REALDASH`
    project-row line a bench build cannot answer (no
    `vtrux_project_rows.json`), as at 1c. `pins` on each: PASS R30.
- **R20, R30 and R8 cases, shared/main `a3b0f250`'s verification**
  (`shared/tools/verification/`, clean detached checkout, the venv's
  Python, `--base 7f2d5032`, each script's `--self-test` exit 0 first):
  - **`switches_cases`:** `release_pass`, `on_restart` and `on_txabort`
    PASS (`release_pass` was VOID at 1c, from a row-name lag).
  - **`pins_cases`:** `release_pass`, `comp_unlisted` and `comp_no_list`
    PASS; `unlisted` n/a to gen-inhibit, VOID "no file on the project's
    list reads the board's pins", as at 1c.
  - **`release_cases`, the `r8_sdkconfig_*` cases (item 6)**, on the fixture
    made from `7f2d5032` (`idf ==5.4.2`, so R33 lets it build): `_ignored`,
    `_untracked`, `_elsewhere`, `_generated` and `_outside` PASS.
    `r8_sdkconfig_excluded` was VOID at `a3b0f250`, "the exclude rule did
    not take": the case wrote `/sdkconfig` to `.git/info/exclude`, where a
    leading `/` anchors at the repository's top, so with the project in
    `gen-inhibit/firmware/` it matched nothing. A defect in the case, fixed
    on `shared/tools/r8-exclude-prefix` `88035115` (shared/main
    `181ef8eb`): the line carries git's `--show-prefix`. The fix's
    self-test exit 0, and a mutant writing the old line was caught. Then,
    from `88035115` on a fresh fixture, `r8_sdkconfig_excluded` **PASS**:
    refused (exit 1), no record, matched by the prefixed exclude line.
- **`test_refusal_pairs`** (`77143534`'s `refusal_pairs_runs.py`, `--sha
  7f2d5032`, every ON image): "positive PASS; 16 of 16 negatives caught on
  their own check".
- **v5.4.2's new defaults (item 2): PASS.** `git diff 115f907c 7f2d5032 --
  gen-inhibit/firmware/sdkconfig` (38+/6-) adds as `y` the BLE 4.2
  controller options (`BT_BLE_42_DTM_TEST_EN`, `BT_BLE_42_ADV_EN`,
  `BT_BLE_42_SCAN_EN`, `BT_CTRL_DTM_ENABLE`, `BT_CTRL_BLE_MASTER`,
  `BT_CTRL_BLE_SCAN`, `BT_CTRL_BLE_SECURITY_ENABLE`, `BT_CTRL_BLE_ADV`),
  `GPTIMER_OBJ_CACHE_SAFE`, `GDMA_ISR_HANDLER_IN_IRAM`,
  `GDMA_OBJ_DRAM_SAFE` and `MBEDTLS_SHA1_C`; as `0` the two FATFS
  free-space options (`FATFS_DONT_TRUST_FREE_CLUSTER_CNT`,
  `FATFS_DONT_TRUST_LAST_ALLOC`); and, not set, `BT_CTRL_BLE_TEST`,
  `I2C_SKIP_LEGACY_CONFLICT_CHECK`, `FATFS_USE_DYN_BUFFERS`,
  `MBEDTLS_ALLOW_WEAK_CERTIFICATE_VERIFICATION` and
  `SPI_FLASH_FORCE_ENABLE_C6_H2_SUSPEND`. It removes
  `BT_BLE_HOST_QUEUE_CONG_CHECK` and its legacy name, both not set, and
  moves `BT_BLE_ESTAB_LINK_CONN_TOUT=30` and `GDMA_ISR_IRAM_SAFE`
  unchanged. None is one of the release profile's 13 options, the board's
  9 `sdkconfig.board` options or its 5 required settings, and the options
  check passes on the stand-in.
- **Harness:** `gen-inhibit/tester/port-1d-bench` `77143534`, clean
  throughout. Its build-check change, `5339c16d` (`baseline_builds.ps1`
  runs R32's `check-env` before each build), and its tests are in the
  acceptance entry (12.4, "Port step 1d's acceptance").
- **Run files:** `runs/step1d_7f2d503/` (`README.md`; `SHA256SUMS`
  `f5a8c380...fe37`): `r19_own/`, `r18_rejudge/`, `standin_ri_a/`,
  `standin_ri_b/`, `builds/`, `builds2/`, `build_checks/`,
  `build_checks2/`, `verification/`, `verification_r8x/`.
  `runs/step1d_7f2d503_repro_off/` (`README.md`; `SHA256SUMS`
  `74264a65...f5fb`): the `repro_off` rerun.

**Bluetooth off (item 3, setting 7; spec 9.4): tester, 2026-11-23, on
`0a740ce6` `_man`. Result: AS SPECIFIED in the build. shared/tools' options
check (R18) on `0a740ce6`'s own release profile FAILs on this option alone,
because that profile still says Bluetooth on. The release profile on
`gen-inhibit/tester/stage-b1` (`603835b3`) passes.** This is stage (b) batch
1. The image is as in the 9.2 entry. Run files are in
`runs/stage_b1_0a740ce6/host/`.
- **`build_output.py`'s new row "Bluetooth off"** (`603835b3`): it passes
  when `CONFIG_BT_ENABLED` is absent or false in the build's
  `config/sdkconfig.json`, and fails otherwise, naming the value.
  - On the image: `ok  Bluetooth off  CONFIG_BT_ENABLED=not set`
    (`build_output_0a740ce6_man.txt`). 46 rows ok.
  - Two rows are not ok, and neither is about Bluetooth:
    - The switch row FAILs because the build's CMake record could not be
      used from the stage-b1 worktree, where the check ran. Run with
      `--repo` on the image's own tree, `build_switches.py` gives unlisted
      0, stale 0, malformed 0 (`build_switches_0a740ce6.txt`).
    - The prod-build record is NO REF: this is not a prod build.
  - The row's test (`test_bt_row.txt`) covers absent, false, `y`, `n`,
    a config not shown to apply to the image (NO REF, not a pass), and the
    sdkconfig text forms. It also covers the
    release profile's entry and the committed `sdkconfig`. 4 of 4 mutants
    are killed.
- **The release profile** (`gen-inhibit/firmware/vtrux_checks.json`, the
  tester's): `CONFIG_BT_ENABLED` changed from `true` to `"unset"` in
  `603835b3`, citing 12.3 item 3 setting 7 and section 9.4.
- **shared/tools `2026.11.16.3` `vtrux_checks`:**
  - On the image, with `0a740ce6`'s profile
    (`shared_tools_options_0a740ce6.txt`): R18 FAILs, with "build-tester-man:
    CONFIG_BT_ENABLED is false, the profile says true", and nothing else.
    R26-R29 PASS.
  - On a build of the same firmware with stage-b1's profile
    (`shared_tools_options_stage_b1_profile.txt`): R18 PASSes.
  - On the image (`shared_tools_scopes_0a740ce6.txt`): R17, R20, R21 and R30
    each PASS. R17 accepts `dependencies.lock` naming the annotated tag
    object `b5f8a292`, which it resolves to commit `8e16cd74`.

**Item 2, no diag misfit on any trace, and item 3's diag rows, on
`db998aca`: tester, 2026-11-23. Result: AS SPECIFIED. No host or emulated
trace carries a misfit, and the misfit report can fail a trace (the
can-fail). The committed C and Python tables are the DBC's, regenerated byte
for byte, and the DBC's `DiagSchemaHash` is its layout hash, on the bench
image and on the release stand-in.** This is stage (b) batch 2, where the
core began packing every diag page, and the `0x7F0` probe, through
shared/diag's packer (`vtrux/diag`, tag `2026.11.23.3`) and the table
generated from the DBC (`gen-inhibit/main` `db1bfeae`; `db998aca` changes
only the file map). Run files are in `runs/stage_b2_db998aca/host/`, and the
emulated runs in `runs/chain/`.
- **How a host build takes the misfit report.** The core discards the
  packer's report, as spec 10 says the device does, so the host build takes
  it at the packer: `gen-inhibit/test/diag_host/misfit_wrap.c`, linked with
  `-Wl,--wrap=diag_pack_message`, records every call that reported a misfit
  or refused its table entry and passes the result on unchanged.
  `host_runner` prints each record as a `!! DIAG MISFIT` line, and
  `invariants.py` fails any trace that has one. A build whose diag frames
  went out without passing the wrapper prints `!! DIAG PACKER NOT WRAPPED`
  and exits 2.
- **No misfit on any trace: PASS** (`gen-inhibit-emulated_20261123T170423_d82f`,
  H, on `db998aca` clean, `gi_tools` `4126f144` clean). 25 steps exit 0. Its
  log names the shared/diag it built with, `2026.11.23.3`, `diag_pack.c`
  sha256 `e44f16f9...`. Invariants: 104 scenarios hold, 0 violate (128,609
  inhibit frames checked); none of the 294 traces in its
  `traces_scenarios_golden.tgz` has a `DIAG MISFIT`, `UNPACKABLE` or `NOT
  WRAPPED` line. Goldens 81 passed, 0 failed, 23 without goldens; 9 replays
  identical; PASSIVE differential 100 identical, 0 differing; 200 fuzz
  sequences, 0 with violations.
- **The can-fail** (`host/diag_host_test.txt`, 19 checks ok): `host_runner`
  on a copy of the core with `0x7F3`'s `diag_reserved16` planted at 65536
  (written into the raw array, past spec 10's width check) prints a MISFIT
  line naming it at each `0x7F3`, and `invariants.py` fails that trace; the
  unplanted copy prints none and passes. Built without the wrapper, the
  runner refuses with `DIAG PACKER NOT WRAPPED`, and `invariants.py` fails
  that trace too. The wrapper's own cases: a fit is not recorded; a misfit is
  recorded with id, count, mask, name and value; a refused entry as -1; a
  record nobody takes ends the process with exit 3.
- **The first emulated run on `db998aca` failed, from a harness gap:**
  `gen-inhibit-emulated_20261123T162408_aa4c` (`gi_tools` `db998aca`), H FAIL
  at `make`, because `baseline_host_run.sh` shipped the host copy of the tree
  without shared/diag, so the build found no `shared/diag`; B15host FAILed
  after it, on the missing `host_runner`. Fixed in `4126f144`
  (`baseline_host_run.sh` ships `shared/diag` from the tag the tree pins as
  `vtrux/diag`, and logs it; its test 68 checks ok, 44 of 44 mutants killed
  with the control passing, `host/test_baseline_host_run_mutants.txt`).
  `aa4c` stays as the record; `d82f` is the run counted.
- **Item 3's diag rows: PASS** (`build_output.py` at `db998aca`, run from
  each image's own tree; `build_checks/` is the same at `4126f144`):
  - On the bench `_man` image (`host/build_output_man_owntree.txt`): 51 rows
    ok, and NO REF on the prod record, as for any bench image. On the release
    stand-in (`host/build_output_standin_owntree.txt`, its record in the
    stand-in's records folder): 52 rows ok, none failing. The two are the
    same image, `cd46e234...`, 1,015,728 bytes.
  - The diag rows on both: the DBC from git at `d2902fbf`, schema 9, hash
    `0c2c4b82529f74e1`; `GI_DIAG_SCHEMA_VER` 9 and the schema doc's 9; the
    DBC's `DiagSchemaHash` is its layout hash; `gi_diag_table.c`,
    `gi_diag_table.h` and `gi_diag_table.py` regenerated from the DBC with
    the pinned generator, identical; 2600 frames emitted by the host build on
    `0x7F1`, `0x7F2`, `0x7F3`, `0x7F8` and `0x7F9` decoded clean with
    cantools; the retired revision bytes 0 in all 1063 pages judged.
  - Bluetooth off: `CONFIG_BT_ENABLED=not set`, ok on both.
  - The rows' own test (`host/diag_table_row_test.txt`, 15 checks ok): one
    byte changed in each committed table, CRLF line ends, a DBC whose layout
    moved, a DBC whose `DiagSchemaHash` is not its layout hash, and a missing
    table each FAIL; no generator, or no DBC, is NO REF, not a pass; at
    schema 9 a retired byte that is not 0 FAILs; schema 8 alone is NO REF.
  - Run from the stage-b2 worktree rather than the image's tree, the switch
    row FAILs on the build's CMake record and the two switch-table rows after
    it are not reached, as in batch 1 (`host/build_output_man.txt`,
    `host/build_output_standin.txt`); every other row is the same.
- **shared/diag's own requirements** (D2-D4) are in
  `shared/diag/VERIFICATION.md`, from the run in
  `runs/stage_b2_db998aca/shared_diag/`.

## 12.4 Required tests

**B11raw and B21 under the wording of 2026-11-19 -- tester, 2026-11-19, harness topic (E) on
`gen-inhibit/tester/arm-void-12.1`. No device run: the harness change, and the `e3d9437`
baseline re-judged from its records. Result: no B11raw or B21 pass stands under the new wording;
the baseline reads VOID for both.** Spec 3 and 9.3 as the user changed them (`f7865461`): a mode
change judged in the request-to-reply window, with no backlog on the witness or the receive queue,
not on the exact frame (B11raw); the OTA refusal judged by the whole 403 with its reason reaching
the client, the connection's end recorded, not judged (B21). The gen-inhibit reviewer's readings
(m)-(w) (answer to b07f7984) set how each is judged (`bench/README.md`, `b11_rawpost_judge.py`,
`ota_guard_client_judge.py`).
- **Earlier passes are not carried forward.** Every B11raw and B21 result in this document before
  this entry was judged under the old wording: B11raw by the request's arrival on the air in a
  Wi-Fi capture, B21 by the 403 and the device's end of the connection on the wire. They are not
  passes under the new wording; the full re-run (C) gives the first results. (2026-11-20: both
  PASS on `743cf7cd` in run (C), its entry at the end of this section.)
- **The `e3d9437` baseline, re-judged from its records** (written into
  `runs/baseline_e3d9437/bench/` beside the old files, which stay as they were):
  - B21 (`B21_client_multipart_e3d9437.txt`, `B21_client_image_e3d9437.txt`): **VOID**. All 40
    reps got the whole 403 with its reason at the client ("gen_inhibit is armed; POST
    /gen_inhibit_set?mode=0 first", Content-Length 56, read from each row's raw_head, which holds
    the whole 127-byte reply). The records hold no page read around any request, a reading the
    harness never sent, so nothing-touched cannot be judged.
  - B11raw (`B11raw_window_judge.txt`): **VOID**. No page read follows any reply, so the mode after
    and the queue cannot be judged. On the witness, 9 of the 12 POSTs show the change in the window
    (no device `0x051` after the reply into OBSERVE, no stale answer after the POST back), and 3
    (OBSERVE from PASSIVE) have nothing to show. Windows 8.3-37.5 ms; the clock margins 0.35-0.46
    ms.
  - 2026-11-19, the topic (E) follow-up (the gen-inhibit reviewer's answer to 8e2ed20c): a stale
    answer is judged from the request's send, not from the reply, so an answer inside the window to
    a command from before the request now FAILs. Re-judged with that rule, the baseline's
    `B11raw_window_judge.txt` and both B21 client files come out byte for byte the same: no POST
    back has such an answer. B21 also prints a spec 3 finding where the mode setter replied 200
    and the page showed another mode; the baseline has none.
- **B21's limit** (reading (v)): the image check reads `elf_sha256` before and after each refused
  request, and the uptime its continuity. A write to the other OTA slot without a reboot is not
  visible to this check.
- **B11raw's queue half has no bound yet.** `drv.msgs_to_rx` on the page after each reply must
  stay within a bound set from quiet data on the same replay with no mode change (reading (q)),
  which the test plan will carry beside `GATE_HELD_S` once measured. Until then the queue half
  reads ERROR, so B11raw cannot PASS.
  - **2026-11-20, tester: the bound is set, 0.** `baseline_b_noX2.sh B11rawquiet` on `743cf7cd`
    `_man` (`483bb940...`, loaded by OTA under the hold), 11:15-11:20: B11raw's six arms
    (I P P I I P, `scottsvalley_armable_300s.log` 0-35 s) with no mode change, the page polled
    at 10 Hz; `b11_queue_quiet.py` over the six records: 2069 answered reads, all in the arm's
    own mode (344, 346, 344, 345, 344 and 346), `drv.msgs_to_rx` 0 in every one (median, p99
    and largest 0 in each arm), `rx_missed` 0. `b11_rawpost_judge.QUEUE_BOUND` = 0, the largest
    read (the gen-inhibit reviewer's answer to 8e2ed20c, item 1); test plan section 4a. Run
    files: `runs/b11rawquiet_743cf7c_20261120T111518/` (the records, their stdout, and
    `B11rawquiet_queue_quiet.txt`). B11raw itself has not been run on this code; this sets the
    bound only.

**Quiesce under traffic: INHIBIT / PASSIVE -> OBSERVE -> back -- tester,
2026-10-10 18:43-18:48, on `cc34c80` `_man`. Result: OBSERVE in effect, no
panic, PASSIVE re-arms cleanly. The re-arm into INHIBIT is NOT AS SPECIFIED:
it sends stale inhibit frames, some late, and in 2 of 3 arms uncounted.**
Spec 8, and the 12.4 row "quiesce under traffic".
- **Rig:**
  - `e4_device_calibration.py --post-schedule` (new). The arm's mode is POSTed
    before the replay; OBSERVE is POSTed 10 s in and the arm's mode again at
    20 s, each from a thread, with the replay index recorded.
  - `scottsvalley_armable_300s.log` 35 s, offset 500, unpolled, interleaved
    I, P, P, I, I, P. PCAN replay, Kvaser witness, X2 app running, 12 V only.
- **Analysis:** `quiesce_analysis.py`, tested by `test_quiesce_analysis.py`
  (7 checks, 3 mutants); results in `runs/quiesce_analysis_20261010T184409.jsonl`.
  - Each POST is placed on the wire by its replay index (provenance).
  - Per span: the device's `0x051` and other frames.
  - The driver read uptime and reset reason before and after each arm
    (`runs/quiesce_uptime_20261010T184409.json`).
- **No panic:** in all six arms uptime advanced by the wall time (gap within
  0.03 s) and the reset reason stayed POWERON.
- **OBSERVE in effect:**
  - The INHIBIT arms' last frames came 3.9 and 14.3 ms after the OBSERVE POST.
    After that the device sent nothing until the back-POST: no `0x051`, no
    diag page.
  - The PASSIVE arms sent nothing from the OBSERVE POST on.
  - Listen-only itself is proven by the OBSERVE ACK probe below.
- **Back to PASSIVE (clean):**
  - diag resumed 182-214 ms after the POST;
  - the first `inhibit_live` page came 0.45-1.17 s after it, at page
    resolution;
  - 0 device `0x051` by provenance.
- **Back to INHIBIT: live again 97.7, 65.5 and 90.4 ms after the POST, but
  the first transmission was a burst of stale frames in all three arms:**

| Arm | Back to live | Burst at re-arm (device frame, behind which VCM command, delay) | Late on the wire | Device: `skips_late` / `late_on_wire`; `rx_missed`; `unverified_max_backlog` |
|---|---|---|---|---|
| 1 | +97.7 ms | 06 and 07 behind VCM 08, at +1.45 and +1.96 ms | (stale, not delta 0) | 0 / 0; 35; 37 |
| 4 | +65.5 ms | 04 behind VCM 04 at +12.2 ms, then 05 | 1 | 1 / 1; 0; 7 |
| 5 | +90.4 ms | 05, 06 and 07 behind VCM 07; the 07 at +2.99 ms | 1 | 0 / 0; 32; 43 |

  - **Observed against spec 5.2 item 5 and trip 7.** The spec says a frame not
    on the wire by the VCM's next `0x051` is withdrawn and never sent late, and
    a frame that went out late, or may have, counts as a skip. The 12.4 row
    asks the witness to confirm that no frame went out late undetected.
  - At every re-arm the device answered commands that arrived during the
    re-arm, after the VCM had moved on.
  - Arm 4 counted its one late frame. Arms 1 and 5 counted nothing. Those two
    had a receive-queue overflow at the re-arm (`rx_missed` 35, 32) and a
    backlog of 37-43; arm 4 had neither.
  - Entering INHIBIT from OFF into the same kind of traffic (mode-entry arms,
    section 5) was clean in 3 of 3, so this is particular to leaving OBSERVE.
  - Not diagnosed; with the implementor through the reviewer. Provenance
    verdict for arms 1, 4 and 5: NOT as specified (frames in each run file's
    `provenance`).
- **`tx_ok` against the wire, per span:** `tx_ok` resets on every arming, so
  it counts the re-armed span only. Arms 1 and 5 match (1,492; 1,494). Arm 4
  has 1,496 on the wire against `tx_ok` 1,495.
- **Not observed:** `self_off` read "" at each arm's end, after the re-arm had
  cleared it. Whether the setter's own quiesce reports "forced quiesce" while
  in OBSERVE needs a read inside the OBSERVE span; the arms were unpolled.
- **Harness note:** provenance's whole-arm count invariant cannot apply
  across a re-arm. For schedule arms it is skipped there and done per span by
  `quiesce_analysis.py`.
- **Run files:** `runs/e4_quiesce_{inhibit,passive}_20261010T184409_arm{1..6}.json`
  and their witness logs.

**Error frames from an adapter at the wrong bit rate -- tester, 2026-10-10
19:18-19:21, on `cc34c80` `_man`. Result: only the error-passive regime is
reachable this way on this bench. Trip 6 latched; the device stayed
`running`.** The 12.4 error-frame row, with the method it names.
- **Rig:** `wrong_bitrate_errors.py`, tested by `test_wrong_bitrate_errors.py`
  (7 checks, 3 mutants).
  - Only two adapters reach the WiCAN segment, so the PCAN plays the
    interlock slice (the device armed and live) and also witnesses error
    frames.
  - The Kvaser at 250 kbit is the error source.
  - The X2 was held in ROM download mode (19:18-19:21; `7f197bfd` on the wire
    before and after).
  - The status page was polled at 5 Hz: state, TEC, REC, `bus_error`, live,
    abort.
- **First calibration** (`runs/wrong_bitrate_calibrate_20261010T191825.json`)
  was a harness defect.
  - The burst was ended with `flush_tx_buffer()`, which does not stop a frame
    the controller is already retransmitting.
  - The first "1 ms" burst became about 30,000 error frames on the segment.
    It drove the PCAN (the interlock player and witness) bus-off, so the later
    bursts read 0.
  - The bus was quiet again at 19:19 (3 s listen-only: 0 frames, 0 error
    frames).
  - Fixed: each burst now opens the Kvaser channel, sends one frame, and closes
    the channel after the burst length.
- **Second calibration** (`..._192003`), nine bursts of 1, 5 and 20 ms, 3 s
  apart:
  - **2,594-2,907 witness error frames per burst, whatever its length.** The
    floor is the wrong-rate controller's own on/off episode, not the burst
    length. With traffic flowing, it destroys frames just by being on the bus.
  - The WiCAN, read throughout: REC peaked at 128-135 after every burst (error
    passive), TEC 0, state `running` throughout (never `bus_off`).
    `bus_error` rose by 22,731 against 33,218 error frames at the witness.
    Trip 6 latched ("error-frame rate exceeded").
- **Regimes:**
  - The method reaches the error-passive regime only: about 270 times the
    trip threshold in a single episode.
  - Error-active and at-threshold are not reachable this way with these
    adapters.
  - The threshold regime stands on the X2 jammer (bench case C, 2026-10-09,
    below), as the reviewer ruled; the jammer is also the way to an
    error-active regime.
  - A wrong-rate adapter on a bus with no other traffic might yield fewer
    error frames per episode; not measured.
- **Note (tester, 2026-10-26; wording approved, user, 2026-10-25): B9's trip 6
  was never latched by the bursts on a live arm.**
  - **What every wrong-bit-rate record on file shows:** `live_before` is false,
    no page sample reads live, and the trip had already latched at the first
    page sample, about 4 s before the first burst. The records are
    `wrong_bitrate_calibrate_*.json`: 2026-10-10 on `cc34c80`, the `e3d9437`
    baseline, `0bfe` and `ec06`, and `cfcd` and `6204` on `6926fb0`.
  - **Why it read PASS:** B9's judge passed on "error-frame rate exceeded" being
    among the aborts, so a latch made before the stimulus counted.
  - **What this changes:** these B9 PASS verdicts are not evidence that the
    wrong-bit-rate bursts trip a live arm. Under the judge as changed on
    `gen-inhibit/tester/gi-page-reads` (`6081665`), each reads INCONCLUSIVE.
  - **What still stands:** the counter readings stand as readings of the
    device's counters with the arm latched: REC up to 128-135 after each burst,
    TEC, state `running`, and the witness's error frames per burst.
  - **Where trip 6 on a live arm rests instead:** the jammer cases (B5
    livetrain, B6 threshold, B7 busoff-rearm) and B10's flood arms. Their
    records show the device live with no abort just before the stimulus, no
    witness error frame before it, and the latch after it.
  - The "Trip 6 latched" in this entry's result is one of these.

**Trip 6 after a re-arm: the first re-arm trips on an expired burst --
tester, 2026-10-10 19:22, on `cc34c80` `_man`. Result: NOT AS SPECIFIED.**
- **Spec 7, "How the window moves"** (decided 2026-10-08, user): "Only a
  recent burst trips ... After a trip 6 latch, a re-arm can therefore go live
  once the bus has been free of a qualifying burst for 10 s."
- **Observed** (`runs/trip6_rearm_check_20261010T192221.json`; PCAN interlock
  slice, status page every 1 s):
  - Arm 1 was about 100 s after the last error frame (the second calibration
    ended about 19:20:41). The re-arm cleared the latch: `abort_reason` "" at
    t = 0.
  - At +1.04 s, while `inhibit_live` was still false, trip 6 latched again:
    "error-frame rate exceeded", with `bus_error` 0 and 0 witness error frames
    since arming. Spec 7 evaluates the trip only while live, so this latch came
    before the device was ever live. It never went live in 14 s.
  - Disarmed and re-armed 2 s later (arm 2): live normally, `tx_ok` 1,420 in
    14 s, no abort.
  - The second calibration's own arm shows the same. It was latched at arming,
    4 s before its first burst, about 75 s after the first calibration's flood
    had ended.
- So the first re-arm after a trip 6 latch trips once on a burst that has
  expired, and the following re-arm goes live. Not diagnosed; with the
  implementor through the reviewer.

**Trip 6 before going live, after a re-arm -- tester, 2026-10-12 17:25-17:26,
on `dace947` `_man`. Result: AS SPECIFIED in all three judged arms. A3
latched at the gate and never went live. The error history on the page read
as specified before each re-arm, and `err_burst` predicted each arm's
outcome.** Spec 7, "How the window moves" and "Checked before going live"
(added 2026-10-10, user), and spec 11, "Trip 6's error history is reported"
(added 2026-10-12, user). This replaces the `fb61892` entry, which the
reviewer verified and closed on 2026-10-10 against the earlier text
("checked once live"). Its run files remain
(`runs/trip6_rearm_check_20261010T200922.json`, `..._201038`).
- **Image:** `dace947` `_man`, as in the entry "No stale answers on going
  live, bench half".
- **Harness:** `trip6_rearm_check.py`, tested by `test_trip6_rearm_check.py`
  (34 checks, 14 mutants all caught; `runs/test_trip6_rearm_check_20261012.txt`).
  - The PCAN plays the interlock slice (key on throughout, so no key-off ->
    key-on edge can clear the history) only while the WiCAN is armed, and
    witnesses. The Kvaser at 250 kbit is the error source. Only typed PCAN
    error frames count as wire errors (the 2026-10-10 fixes). The X2 was held
    in ROM download mode 17:24:45-17:26:14 and read off the wire as
    `7f197bfd` before and after
    (`runs/x2_identify_{before_hold_20261012T1724,after_hold_20261012T1726}.txt`).
  - Sequence:
    - A0: arm, go live, burst (trip 6 latches), mode 0.
    - A1: re-arm 15 s after A0's last error frame. Must go live with no trip.
    - A2: arm, go live, burst, mode 0.
    - A3: re-arm 2 s after A2's last error frame. Must latch trip 6 when the
      gate passes and never go live.
    - A4: re-arm 15 s after A2's last error frame. Must go live.
  - The status page is polled at 5 Hz, and read once just before each
    re-arm.
  - **History**, from that read: in A1 and A4, `err_burst` false and `err_n`
    0; in A3, `err_burst` true and `err_n` >= 10. In every arm, `err_age_ms`
    must be within 0.5 s of the time since the witness's last error frame.
  - **Prediction:** `err_burst` before the arm must match whether trip 6
    latched in it.

| Arm | Re-armed after the last error | Before the arm: `err_n`, `err_age_ms`, `err_burst` | Outcome | Arm, history, prediction |
|---|---|---|---|---|
| A1 | 15.078 s | 0, 15,121, false | live, no abort; 1,195 device `0x051` | as specified (all three) |
| A3 | 2.047 s | 16, 2,044, true | trip 6 latched; never live in 54 reads; 0 device `0x051` | as specified (all three) |
| A4 | 15.043 s | 0, 15,040, false | live, no abort; 1,202 device `0x051` | as specified (all three) |

  - A0 and A2: each burst latched trip 6 on a live arm (2,692 and 2,673 wire
    error frames). Before A0, with no error since boot, `err_age_ms` read
    `null`, as spec 11 states.
  - In every judged arm: 0 wire error frames since arming.
  - Run file: `runs/trip6_rearm_check_20261012T172500.json`.
- **A3 compared with `fb61892`:** on `fb61892` one A3 of two sent one inhibit
  frame before the latch, as the then-current text allowed. On `dace947` A3
  sent none and never read `inhibit_live`.
- **Observed, not judged: `err_n` read 16 two seconds after a burst of 2,673
  wire error frames.** Spec 11 defines `err_n` as "the number of error frames
  recorded in the 10 s before the reading". Whether "recorded" can be fewer
  than the frames on the wire is put to the reviewer, not read here.
- **Positive control on `cc34c80`** (2026-10-10,
  `runs/trip6_rearm_check_20261010T200306.json`, `..._200520`): A1 latched
  trip 6 on an expired burst and never went live, NOT as specified, in both
  runs. The check fails on the defect.

**No stale answers on going live, host half -- tester, 2026-10-12, on
`099e9da`. Result: AS SPECIFIED on the host. The bench half waits for the
follow-up commit (reviewer, 2026-10-11: do not run `099e9da` on the
bench).** Spec 7 gate condition 9, and the 12.4 row "no stale answers on
going live" (host: "a case with frames waiting at go-live").
- **Harness** (`wican-fw-vtrux` branch `tester/golive-host`, commit
  `7d9c20c`):
  - `host_runner`, with `load` on, sets `gi_bus_t.rx_waiting` at each
    dequeue to the frames still queued behind the one taken, and prints
    `RXW n=<k>` when it changes. Before this the runner never set the field,
    so condition 9 could only read "drained" on the host.
  - Scenario `golive-backlog-held`: truck load (~2250 fps, RX depth 64).
    `0x617`, the last gate condition, is withheld until 2.767 s. The worker
    is preempted from 2.7665 to 2.7758 s (9.3 ms), so the `0x617` comes out
    of the preemption with two VCM `0x051`s queued behind it.
  - **The backlog is constructed.** 9.3 ms is longer than the 2.39 ms worst
    WiFi preemption measured (spec 5). It takes two waiting commands to tell
    a stale answer from a fresh one, because a single waiting command is
    still the newest when it is answered.
  - `drained_check.py` judges it against the spec, with no golden:
    - the first inhibit frame after going live must carry the counter of the
      newest `0x051` presented by then, plus one. "Presented" means arrived
      in the device's receive queue (the scenario's own frame times), not
      dequeued: judged against dequeued frames, a stale answer would pass by
      construction. The `fb61892` control failing shows the check uses arrival
      (reviewer, 2026-10-12);
    - the `rx_waiting` in force when the device went live must be 0;
    - with no "receive queue not drained" hold, the case is INCONCLUSIVE.
    - Self-test 16 cases; 14 mutants, all caught
      (`runs/test_drained_check_20261012.txt`).
- **Result on `099e9da`: PASS.** Held on "receive queue not drained" from
  2.775810 s for 0.80 ms while 21 frames drained. Live at 2.776611 s with
  `rx_waiting` 0. The first inhibit frame answers the newest `0x051`.
- **Control, the `fb61892` core** (runner built without the field): **FAIL,
  as required.** Live on the `0x617` with no hold. The first inhibit frame
  (2.776170 s) carries counter 1, the counter of the newest `0x051`
  (2.775470 s), so it answers the older command, which the VCM had already
  passed.
- **Shim at `099e9da`** (`runs/shim_099e9da_20261011.txt`): all cases pass,
  among them case 40 ("the inhibit does not go live while frames are
  queued"), case 39 ("an arm before the worker's first instruction still has
  a worker to wake") and case 38 ("a real burst before going live still
  trips (not over-corrected)").
- **The rest of the host suite at `099e9da`**
  (`runs/host_suite_099e9da_20261012.txt`):
  - 78 goldens pass; 22 scenarios are judged without goldens.
  - `invariants`, `passive_diff`, `fuzz_sequences` (200 sequences),
    `load_margin` and `bus_on_check` pass.
  - The 70 goldens that moved change only `diag_schema_ver`, 6 -> 7. The one
    exception is `load-preempt-rides-through`, which also gains six `RXW`
    lines (`runs/golden_schema_bump_099e9da_20261012.txt`).
- **Re-run on `dace947`, the follow-up commit** (branch
  `tester/golive-host-dace947`; `runs/host_suite_dace947_20261012.txt`,
  `madhouse-debian`): the same result. `drained_check` PASS (held 0.801 ms
  while 21 frames drained, live with `rx_waiting` 0, the first inhibit frame
  answers the newest `0x051`), and the `fb61892` control FAILs as required.
  78 goldens pass, `spec_expect_check` passes 21 of 21 rows, and the shim
  passes every case, 40-45 among them. `passive_diff.py` needed one fix for
  `dace947` (the 11 status page entry).
- **The bench half:** the next entry.

**No stale answers on going live, bench half -- tester, 2026-10-12
17:09-17:24 (entries) and 17:29-18:09 (auto-arm boots), on `dace947`.
Result: AS SPECIFIED. 20 of 20 entries into INHIBIT from OFF, OBSERVE,
RESPOND and PASSIVE, and 18 auto-arm boots that went live: 0 stale device
`0x051`.** Spec 7 condition 9, and the 12.4 row "no stale answers on going
live" (bench).
- **Image:** `wican-fw_obd_dace947_man.bin`, 1,277,232 bytes, md5
  `babaa13ea7365fcfccbd3ea07a31190c`.
  - Built by the tester from a clean tree at `dace947` (`git status` empty),
    default configuration: no AUTO-ARM line in the build output
    (`runs/build_default_dace947_20261012.txt`).
  - OTA'd at 17:02 (`runs/bench_flash_dace947_20261012T1702.txt`: the device
    reports `dace947`, and the image embeds `dace947`).
  - **Bring-up 1.5b passed** (`runs/page_only_baseline_20261012T170310.json`):
    `git_version` `dace947` at uptime 16.3 s and again at 135.4 s, 120 polls,
    no outage and no reboot, reset reason `"SW"`, rate 1.00008.
    - *Condition added 2026-10-13, corrected 2026-10-13 (reviewer):* CAN1 also
      carried the X2's unacknowledged `0x7F4` retries: 3,697 frames/s on the
      wire, all `0x7F4`, 0 error frames (tester,
      `runs/bench_census_20261013T232011`, X2 on `7f197bfd`). The X2 counts
      them as transmit errors (~3.6k/s, interposer report 2026-10-13). The X2
      was read off the wire as `7f197bfd` before and after
      (`runs/x2_identify_after_hold_20261010T2012.txt` and
      `runs/x2_identify_before_hold_20261012T1724.txt`), and the bench lock
      records no X2 change between; no CAN adapter was open. The WiCAN's CAN
      driver was not running: `drv` null on every answered poll (120 of 120),
      so the result does not depend on the bus traffic. *First written as
      "`0x7F4`-`0x7F7` retries (~3.6k error frames/s)", from the interposer
      report alone; the wire reading showed one ID and no error frames.*
- **Rig:** `e4_device_calibration.py`, full-bus replay of
  `scottsvalley_armable_300s.log` 0-35 s, offset 500 us, PCAN replay, Kvaser
  witness, 12 V only.
  - From OFF: armed into INHIBIT 5 s in.
  - From OBSERVE, RESPOND and PASSIVE: the run starts in that mode, and mode 3
    is POSTed 20 s in.
  - Five rounds, the modes interleaved, so a drift over the batch does not
    land on one mode (`runs/golive_batch_dace947_20261012T1709.txt`).
- **Judge:** `golive_stale_check.py`, tested by `test_golive_stale_check.py`
  (15 checks). A device `0x051` is stale when its counter is one the VCM has
  already passed: delta 0 or 9-15 against the command in front of it. Only
  frames after the entry are judged
  (`runs/golive_stale_check_dace947_20261012.txt`).

| Entry from | Arms | Device `0x051` per arm | Stale | Verdict |
|---|---|---|---|---|
| OFF | 5 | 2,985 | 0 | as specified |
| OBSERVE | 5 | 1,496-1,498 | 0 | as specified |
| RESPOND | 5 | 1,496-1,499 | 0 | as specified |
| PASSIVE | 5 | 1,498-1,499 | 0 | as specified |

- In all 20 arms: live in INHIBIT at the end with no abort, `tx_fail` 0, 0
  witness error frames, and uptime continuous across the batch (no reboot).
- **Auto-arm boots:** 18 boots went live with provenance resolved: the OTA
  boot, four `/system_reboot` boots under Scotts Valley (section 7 auto-arm
  entry), and 13 key-on restarts (next entry). In each, the first device
  `0x051` answered the VCM's newest command, and no later one was stale.
- **Observed, not judged: two INHIBIT POSTs left no reply on record.** This
  was OBSERVE arms 1 and 5, and the device still changed mode (live in
  INHIBIT, page mode 3). The other 13 POSTs from OBSERVE, RESPOND and PASSIVE
  were answered 200 in 25-55 ms, and the five arms from OFF in 18-60 ms.
  *Corrected 2026-10-14: this said "the reply never arrived". The record
  cannot say that.* The POST ran in a thread that caught OSError only, so a
  cut or malformed reply would have raised an http.client error (IncompleteRead,
  BadStatusLine, LineTooLong). That error would have ended the thread with no
  reply recorded, and the batch's output filter dropped the exception line.
  The follow-up below records each reply whole.
- **Desk check of the 10-12 POST client, 2026-10-14 (reviewer's request after
  the user's ruling (b); no bench time). What the code and the record show.**
  The script that ran is kept as `runs/e4_device_calibration_golive_20261012.py`
  (sha256 `2453f0cb...`). Line numbers below are in that file. It differs from
  today's `e4_device_calibration.py` only by the raw-POST patch of 2026-10-14
  05:30. No other edit was made between the batch and that patch.
  - **Thread, join, record.** Each scheduled POST is a `ModePoster`
    (`daemon=True`, 543-560). The replay loop starts it at the first frame at or
    after 20 s (718-723). `run()` sets `t_start`, calls `gi(..., method="POST")`
    and sets `t_end` and `status` only when `gi` returns (557-560). The thread is
    joined only after the 35 s replay has ended, with `join(timeout=10.0)` (753).
    The join's result is not checked, and `is_alive()` is not recorded. The
    arm's `post_schedule` entry copies `t_end` and `status` later still, after
    the quiet wait and the end read of the page (959-961). So the code allows a
    record to be written while the thread is still waiting: `t_end` and `http`
    are then `null`, and a later reply is never seen. For that, the exchange
    must outlast about 25 s (15 s of replay plus the 10 s join) without any one
    socket operation going 6 s without data.
  - **Timeouts.** urllib: 6.0 s, `gi`'s default, which `ModePoster` does not
    override (266, 559). It applies to each socket operation (connect, each
    read), not to the whole exchange. When it fires, it raises `TimeoutError`
    (as `URLError` if it fires at connect). Both are OSError, so `gi` catches
    them and returns `(None, text)`, and `run()` goes on to set `t_end`. The
    record then reads `http: null` with `t_end` set. The join timeout is 10.0 s
    (753), and urllib's is the shorter. When the join times out it returns
    silently: the thread keeps running and nothing is recorded.
  - **By exception class** (Python 3.14.6, checked on this host).
    `urllib.error.HTTPError` is caught, and its status is recorded (273-274).
    Every other OSError is caught and recorded as `http: null` with `t_end` set
    (275-276); the exception text is discarded (`self.status, _ = ...`). That
    covers `TimeoutError`, `URLError`, `ConnectionResetError` and
    `RemoteDisconnected` (a `ConnectionResetError` and a `BadStatusLine` both).
    `IncompleteRead`, `BadStatusLine`, `LineTooLong` and any other
    `HTTPException` are not OSError. urllib re-raises an exception from
    `getresponse()` unwrapped, so these escape `gi` and `run()`, and the thread
    ends. So does anything else not caught (`ValueError` and so on). On those
    paths `t_end` and `status` stay `null`, and the only trace is the
    traceback on stderr. That record is identical to "still running when
    recorded".
  - **Arms 1 and 5 against the other 13 scheduled POSTs and the 5 OFF arms**
    (`golive_1012_reply_desk_check.py`, `runs/golive_1012_reply_desk_check_20261014.txt`):
    - The record differs only in `post_s[1]` and `http`, both `null`.
    - The sends started at 19.9999 and 19.9997 s, at replay frame 47,182.
      That is the same frame as every OBSERVE and PASSIVE entry, and inside
      the batch's 19.9997-20.0005 s spread.
    - The device's first `0x051` came 47.1 ms after the send in arm 1 (after 5
      VCM commands), and 29.6 ms in arm 5 (after 3). In the other 13 scheduled
      POSTs it came 14.9-40.7 ms after the send, after 1-4 commands. That is
      the send placed on the witness clock by the provenance fit (residual p50
      about 280 us), with device frames told by content. On these files the
      content rule's premise holds: no adjacent identical `0x051` before any
      of the 20 sends. Its count is one under provenance's in every arm, the
      last device frame having no command after it.
    - Nothing else stands out. Arms 1 and 5 had 1,495 and 1,497 device
      `0x051` and 0 witness error frames. Their end pages read mode 3 and live.
      Uptime rises through all 20 arms (`reset_reason` SW, no reboot). Neither
      arm is VOID. No hammer or filler thread ran (`hammer_requests` 0, no
      flood).
    - The batch log has a `Traceback (most recent call last):` line under
      these two arms' headers and under no other arm's
      (`runs/golive_batch_dace947_20261012T1709.txt` lines 8 and 89). The
      batch's filter (`grep -i "...\|Traceback\|Error"`) kept only that line.
      So no other line of either traceback contained "error" in any case,
      including its last line, which names the exception. That excludes every
      exception whose name or message holds it: all OSError subclasses,
      `URLError`, `HTTPError` and `ValueError`. It leaves `IncompleteRead`,
      `BadStatusLine`, `LineTooLong` and the bare `HTTPException`.
  - **Reading, as far as the code and the record go.** The thread never came
    back from `gi`. The code offers two ways for that.
    - An escaped non-OSError exception. This fits the `Traceback` line seen in
      exactly these two arms.
    - Still waiting when the record was written. This fits nothing in the
      record: a daemon thread still running at exit prints no traceback.
    - No other thread could have printed it. The main thread completed and
      wrote the file, the witness counted a normal number of frames, and no
      hammer or filler ran. Still, the line's header and last line were
      filtered out, so the record does not tie it to the POST thread by name.
    - Every exception left by the filter is http.client rejecting the bytes it
      received: the connection ended before `Content-Length`, or a status line
      or header that is not HTTP. None of them is raised on a complete,
      well-formed reply.
    - So client-side loss of a complete, well-formed reply finds no route in
      this code that matches the record. What the record cannot decide is
      which of those exceptions it was, and so how the received reply was
      short or malformed.
    - The 2026-10-14 follow-up below reproduced none of this in 30 POSTs (12
      OBSERVE -> INHIBIT).
- **Run files:** `runs/e4_golive_{off,observe,respond,passive}_dace947_20261012T1709_arm{1..5}.json`.

**The missing INHIBIT reply, characterised -- tester, 2026-10-14 07:58-08:17,
on `e998385` `_man`. Result: not reproduced. All 30 POSTs got a complete
reply: 200, the body matching `Content-Length`, valid JSON, and parsed by
http.client. That includes 12 OBSERVE -> INHIBIT entries, the transition that
failed twice in five on 2026-10-12. No RST, no retransmission and no reboot
were seen.** Reviewer's request, relayed as the user's bench request of
2026-10-14.
- **Closed 2026-10-14 by the reviewer, on the user's ruling (a): not reproduced,
  test-only.** The record is the desk check under the 10-12 go-live bullet above.
- **Rig:** as the go-live entry above. The full-bus replay is Scotts Valley
  0-35 s at offset 500 us on the PCAN, with the Kvaser as witness and the
  WiCAN on 12 V only. **X2 on `7f197bfd`**, its four pages ~1/s each (35-36
  per entry; ACKed).
- **Method:** `e4_device_calibration.py --post-raw-timeout 30`. Each
  scheduled POST goes over a raw socket with urllib's exact request bytes
  (`raw_http_probe.py`). It records, against the send:
  - connect, first byte, completion and end;
  - every received chunk, and how the connection ended;
  - the headers, `Content-Length` against the body, and whether the JSON
    parses;
  - whether http.client would have parsed the reply, and whether the 10-12
    `except OSError` would have caught its error;
  - the host's sockets to the device before and after (netstat), and a
    follow-up GET if a reply fails.

  tshark captured all host traffic to the device on port 80
  (`runs/reply_golive_e998385_20261014T0758.pcapng`), and
  `reply_golive_report.py` joins each POST to its TCP stream and the witness
  log. The harness is tested by `test_raw_http_probe.py` (95 checks),
  `test_e4_rawpost.py` (39) and `test_reply_golive_report.py` (25), with 37 of
  37 mutants killed.
- **Batch:** four interleaved rounds (`runs/reply_golive_batch_e998385_20261014T0758.txt`,
  the whole console kept).

| Entry | Arms | POSTs | First byte after the send | Complete after the send | Failed |
|---|---|---|---|---|---|
| OBSERVE -> INHIBIT | 12 | 12 | 15.1-42.7 ms (median 21.4) | <= 52.8 ms | 0 |
| RESPOND -> INHIBIT (control) | 6 | 6 | 4.1-18.1 ms (median 7.6) | <= 22.1 ms | 0 |
| OBSERVE -> RESPOND (driver reinstall, no go-live) | 4 | 4 | 15.8-20.0 ms | <= 52.9 ms | 0 |
| INHIBIT -> OFF -> INHIBIT (chained, 2 s apart) | 4 | 8 | 5.6-18.1 ms | <= 24.9 ms | 0 |

- **On the TCP side, from the capture, for all 30 POSTs:**
  - No RST and 0 retransmissions.
  - No other stream to the device was open at the send.
  - The device kept the connection open after the reply. The host closed it
    after its 2 s wait, and the device's FIN followed 4-22 ms later.
- **Order against the wire:** in OBSERVE -> INHIBIT the reply's first byte
  came before the first device `0x051` in 8 of 12 entries and after it in 4.
  The first device `0x051` came 14.6-45.5 ms after the send.
- **Observed, not judged:**
  - `e4`'s own per-arm verdict was VOID on every RESPOND, OBSERVE -> RESPOND
    and chained entry. In each, its frame attribution disagreed with the
    device's `tx_ok`. That calibration figure is not used here.
  - All four OBSERVE -> RESPOND entries show `tx_fail` 3-7 on the page at the
    end, and every other entry shows 0. **Every count is `sched.cls.probe.dropped`**
    (desk read 2026-10-14 at the reviewer's request, from the run files, no
    re-run). Per the implementor's source reading, spot-checked by the
    reviewer, RESPOND's `tx_fail` is the arm's probe `dropped` plus `refused`.
    It is reset on every arm. `dropped` counts a probe queue (depth 1) that
    was still full when the next `0x051` came.

| Entry | `tx_fail` | probe `dropped` | probe `refused` | probe `max_hold_us` | telemetry `max_hold_us` | `tx_refused` | probe `carried_in` | `tx_ok` |
|---|---|---|---|---|---|---|---|---|
| arm 1 | 7 | 7 | 0 | 27,811 | 4,125 | 0 | 0 | 1,487 |
| arm 2 | 4 | 4 | 0 | 27,948 | 199,956 | 0 | 0 | 1,491 |
| arm 3 | 3 | 3 | 0 | 23,911 | 6,024 | 0 | 0 | 1,491 |
| arm 4 | 4 | 4 | 0 | 27,115 | 8,664 | 0 | 0 | 1,490 |

- The values are from the last page read, about 15 s after the mode 2
  POST, with the device still in RESPOND. Telemetry and inhibit
  `dropped`/`refused` are 0 in all four.
- **When it moved:** the only earlier page in these files is the mode 2
  POST's own reply body, read as RESPOND began. In all four it reads every
  one of these counters 0. So the drops happened within the ~15 s of
  RESPOND before the last read. The runs polled no page between those two
  reads (`poll_hz` 0), so no finer time is on record.
- Arm 2's telemetry `max_hold_us`, 199,956 (about 200 ms), is the outlier
      against 4-9 ms in the others.
- **Run files:** `runs/reply_golive_{observe3,respond3,observe2,off3}_e998385_20261014T0758_arm*.json`
  (each with a witness log) and `runs/reply_golive_report_e998385_20261014T0758.{txt,json}`.

**Auto-arm, restart timed to a key-on -- tester, 2026-10-12 17:34-18:08, on
`dace947_auto`. Result: AS SPECIFIED at all eight offsets, two or more
resolved runs each. Live 1.25 s after key-on for a restart up to the key-on
edge, and 1.85-6.86 s for later restarts. The first engine request was held
in every live run. A restart at +9 s, after the request, never went live,
and nor did one at +7 s, 0.39-0.41 s before it, while the device was still
booting.** The 12.4 auto-arm row, "a restart timed to a key-on" (added
2026-10-10, user), and the spec 13 line "auto-arm, restart at key-on: the
time from key-on to live is not measured".
- **Image:** `dace947_auto`, as in section 7's entry "Auto-arm build:
  boot-to-live on SW boots", which also covers its removal and the
  confirmation on the wire.
- **Capture:** `runs/keyon_drivetrainoverheat_70s.log`
  (`keyon_replay_prep.py`), from the 7.7 s cluster of the deadline sweep
  (section 7). It starts at its key-on edge, the bus silent for 2 s before
  it. The VCM first asks for the engine at 7.741 s, and the gate is closed
  from there to the end (69.999 s). Its contactors close about 1.24 s after
  the key-on edge.
- **Method:** `autoarm_boot_to_live.py --reboot-at <offset>`. `/system_reboot`
  is POSTed so the restart lands at the offset from the key-on edge.
  Reviewer-approved offsets: -3, -1, 0, +1, +3, +6, +7 and +9 s, one cycle
  each, then repeated. Offsets 0 and +1 were run again for a second resolved
  run.
  - A live run is as specified when it never goes live inside a gate-closed
    window, and holds the first request if it is live before it.
  - A run that never goes live is as specified when no gate-open span longer
    than 1.0 s follows the boot.
  - **Two caveats (reviewer, 2026-10-10).** First, the gate-closed windows
    are computed from the capture for conditions 5 and 6 only. So an open
    span that starts before the contactors close is overstated by up to
    1.24 s. Second, the 1.0 s threshold is a chosen bound, taken from the
    0.41-0.58 s SW boot-to-live measured on `f0d25e5`, not a figure from
    the spec.

| Offset | Restart measured (s from key-on) | Boot -> live | Key-on -> live | First request (7.741 s) | Runs |
|---|---|---|---|---|---|
| -3 s | -2.632, -2.634 | 3.884, 3.886 s | 1.2512 s | held | `autoarm_20261012T173440`, `..._174458` |
| -1 s | -0.620, -0.637 | 1.871, 1.888 s | 1.2512 s | held | `..._173601`, `..._174619` |
| **0 s** | 0.354, 0.356, 0.355 | 0.897, 0.895, 0.897 s | **1.2512-1.2513 s** | held | `..._174739`, `..._180432`, `..._180707` |
| +1 s | 1.357, 1.364 | 0.495, 0.488 s | 1.8517 s | held | `..._174857`, `..._180550` |
| +3 s | 3.359, 3.327 | 0.494, 0.526 s | 3.853 s | held | `..._175014`, `..._180026` |
| +6 s | 6.307, 6.346 | 0.552, 0.513 s | 6.859 s | held | `..._174109`, `..._175130` |
| +7 s | 7.348, 7.330 | -- | never live, as specified | not held: the restart came 0.39-0.41 s before it, and the device was still booting when it came | `..._174225`, `..._175247` |
| +9 s | 9.328, 9.327 | -- | never live, as specified | not held: the restart came after it | `..._174342`, `..._180142` |

- **The 0 s point: a restart exactly at the key-on edge costs nothing.** The
  device was live 1.2512 s after key-on, the same as when it was already up
  (-3 s and -1 s). It restarted 0.35 s after key-on and went live 0.90 s
  later, against 0.49-0.55 s for the restarts from +1 s on, where the boot is
  what sets the time.
- **Finding: a restart that lands within about one boot-to-live (~0.5 s)
  before an engine request misses that request.** At +7 s the restart was at
  7.330-7.348 s, 0.39-0.41 s before the 7.741 s request. The device was still
  booting (boot-to-live 0.49-0.55 s for the restarts from +1 s on) when the
  request closed the gate, and it never went live. As specified under the
  1.0 s open-span bound: the open span after the boot was 0.39-0.41 s
  (reviewer, 2026-10-12).
- **Every live run held the first request.** The VCM asks at 7.741 s; the
  latest live time was 6.86 s (+6 s), 0.88 s before it.
- The restart landed 0.31-0.38 s after the nominal offset (the harness times
  the POST; the device times the restart). The table gives the measured
  instant.
- **UNRESOLVED runs, not counted** (8 of 30 auto-arm runs today, the Scotts
  Valley ones included):
  - **Seven, before the rig change:** `..._173300`, `..._173721`,
    `..._173838`, `..._173954`, `..._175403`, `..._175753` and `..._175910`.
    Each stalled at the restart: provenance found only 199-47,435 of the
    replayed frames and refused to judge. The two of them that recorded
    sends (`..._175753`, `..._175910`) had 164,004 and 161,985 failed sends
    from the restart on: the PCAN went bus-off at the restart's error frames
    and stayed off, since python-can's PCAN backend does not reset after a
    bus-off by default. Fixed in the
    harness as described in section 7: `auto_reset`, recorded send failures,
    and provenance judged over the frames sent.
  - **One after it, `..._180826` (+1 s):** one send was reported failed at
    1.1251 s. Four more replayed frames before it (capture frames 2229-2232:
    `0x55C`, `0x052`, `0x400`, `0x596`) never reached the wire, without a
    reported failure. Their payloads recur later, so the in-order walk
    matched them to later copies and stopped at frame 2,234. The run's
    "7,584 device `0x051`" and "0.0044 s boot-to-live" come from that
    misalignment.
    - Counting `0x051` as a multiset instead: 7,720 on the wire against 6,998
      in the capture, so 722 from the device, the same as both resolved +1 s
      runs.
    - None of the lost frames was a `0x051`.
    - Inspected with `autoarm_180826_inspect.py`.
    - **Harness limitation, recorded:** a recorded send failure does not
      account for every frame lost at a restart.
- **Observed, not judged: every restart puts error frames on the bus.** The
  witness saw 131-138 error frames around each of the seven restarts
  recorded after the rig change, the first 0.24-0.25 s before the computed
  boot instant. In the two runs where the PCAN went bus-off it saw 32 and 78.

**Test-only features are inert in the build that will fly -- tester,
2026-10-13 03:51-04:08, on `63eb50f`. Result: PASS on the flight candidate
for all five test-only switches. Each probe has a positive control on a
switch-on image, four of them fresh on the same commit. Auto-arm was shown
both ways. One probe was wrong at first and was caught by its positive
control (below).**
Spec 12.4 row "test-only features are inert in the build that will fly" (added
2026-10-13, user). The design and its two conditions are the reviewer's:
- the fault-injection control comes from the 2026-10-04 record, with no
  deliberate rollback while USB is unplugged;
- after every negative probe the device must be shown to have kept running.
- **The flight candidate:** the tester's clean-worktree build of `63eb50f`
  `_man`, md5 `360f1719...`. No prod build of `63eb50f` exists yet; the result
  carries to one by byte comparison (section 13).
- **The trigger table:** `TRIGGERS` in the firmware repo's
  `test/build_checks/build_switches.py` (`tester/build-switches`, `31f59b8`).
  - For each test-only switch it gives the request, the device state it is
    sent in, the effect, and the observable.
  - The switch check fails a test-only switch with no trigger, and a trigger
    for anything else. 29 mutants, all caught.
  - `restart_error_frames.py --trigger` and the inert rig both read it.
- **The rig:** `test_only_inert.py`. The same code runs both ways: `--expect
  absent` on the flight image, `--expect present` on a switch-on image.
  - Any undetermined observable fails in either direction: a deaf witness,
    a state not reached, or no answer.
  - Absent also needs the device to have kept running across the probe:
    uptime advanced by the elapsed time within 1.5 s, and the same reset
    reason and build.
  - Tested by `test_test_only_inert.py`: 29 checks, 17 mutants, all caught
    (`runs/test_test_only_inert_mutants_20261013.txt`).

| Switch | Request, state | Flight `63eb50f` (absent) | Positive control |
|---|---|---|---|
| `GI_TEST_RESTART_QUIESCE` | `POST /gi_quiesce?crash=illegal` live in INHIBIT under replay (`restart_error_frames.py --trigger`), then `GET /gi_quiesce` | POST 404, GET 404; device live at the POST; no reset, 0 witness error frames; uptime continuous over 80.8 s, reset reason SW; PASS (`..._absent_20261013T035441.json`, crash run `restart_error_frames_live_crash_20261013T035555.json`) | Today's `63eb50f` `_man_rt` acceptance: the same request in the same state reset the device (PANIC) three times (`restart_error_frames_live_crash_20261013T03{3135,3223,3448}.json`) |
| `GI_INSTRUMENT_TXABORT` | `POST /gi_txabort?phase=control&reps=2&settle=20`, mode 0, the PCAN as the ACKing node, the Kvaser witnessing | 404; 0 tagged frames, with the witness shown listening (16 frames, the X2's); kept running; PASS (`..._absent_20261013T035441.json`) | TXABORT image `b0508bed...`: 200, a 1,582-byte answer, 2 frames tagged `AB 7E`/`AB 7F` on the witness; PASS (`..._present_20261013T035923.json`) |
| `GI_INSTRUMENT_RXQ` | `GET /gen_inhibit` while live in INHIBIT under replay; the `"rxq":` token at any depth | live, mode 3, no token; kept running; PASS (`..._absent_20261013T040439.json`) | RXQ image `b74948e5...`: live, token present (top level); PASS (`..._present_20261013T040139.json`) |
| `GI_INSTRUMENT_ABORTWIN` | the same read; the `"abortwin":` token | live, mode 3, no token; kept running; PASS (`..._absent_20261013T040439.json`) | ABORTWIN image `bb951803...`: live, token present (nested, not top level); PASS (`..._present_20261013T040046.json`) |
| `OTA_HEALTH_FAULT_INJECT` | the OTA of the image, then 70 s with no request, mode 0 | started 91 s after the OTA, i.e. after the 60 s rollback point: build `63eb50f` before and after (the other slot held `dace947`), first reading uptime 91.2 s (no reboot since the OTA boot), then continuous over 72.3 s, reset reason SW; PASS (`..._absent_20261013T035441.json`) | 2026-10-04 rehearsal, `runs/rollback_fault_20261004_235125.json` (PASS); see below |

- **A probe that read nothing, caught by its positive control:**
  - The first page-key probe looked for the key at the top level of the
    page only. On the ABORTWIN image it found nothing (FAIL,
    `..._present_20261013T035957.json`), because `"abortwin"` lives inside
    the scheduler object. So the flight image's first "absent" for ABORTWIN
    (`..._absent_20261013T035441.json`) was vacuous, and it is not used.
  - The probe now looks for the exact `"<key>":` token at any depth
    (`page_has_key`, with tests, and a mutant restoring the old behaviour
    caught).
  - Both page-key switches were re-run in both directions with it. The RXQ
    run from before the fix (`..._present_20261013T035840.json`) is
    superseded by `..._040139`.
- **The fault-injection control, and what it shows:**
  - The 2026-10-04 rehearsal predates `63eb50f`: image
    `wican-fw_obd_b492dee_man_flt.bin`, md5 `37077ef0...`, mask 0x01.
  - It recorded the faulty image's first boot, then a second boot 60.47 s
    later on a different slot (`ota_0` -> `ota_1`). The device answered
    afterwards on `b492dee`, the image it had been on before. Both boots were
    observed on the console over USB.
  - This probe judges the build and uptime continuity after the wait. In
    that rehearsal the faulty and previous images had the same version
    string (`b492dee`), so the build comparison alone would not have seen
    that rollback. The reboot 60.47 s after the faulty image's boot is what
    the uptime-continuity check sees.
  - The flight image exposes no partition field, so the partition is not
    read here.
  - **What carries the flight run (reviewer, 2026-10-13):** the 70 s window
    began 91 s after the OTA, so it opened after the 60 s rollback point, and
    uptime continuity over the window does not cover that instant.
    - What covers it is the build check: the image in the other slot, the
      one a rollback would return to, was `dace947`, read before the OTA
      (`runs/autoarm_20261013T035242.json`). It differs from `63eb50f`, and
      the build read `63eb50f` throughout.
    - The window's first reading also shows no reboot since the OTA:
      uptime 91,164 ms at 03:54:41.994, reset reason SW. That puts the boot
      at 03:53:10.830, within 0.08 s of the post-OTA boot that the OTA run
      measured on its own (03:53:10.754). So there was no reboot between
      the OTA boot and the window, which covers the rollback point at about
      03:54:11.
  - No deliberate rollback was run today, as ruled.
- **Auto-arm, a shipping variant, both ways:**
  - Manual flight image: OTA'd under replay, and the device came up in mode
    0. `restore_unarmed_check.py --expect 63eb50f`: in mode 0 it sent
    nothing to 1,500 replayed `0x051`; PASS
    (`runs/restore_unarmed_check_20261013T035415.json`).
  - My first try at this used `autoarm_boot_to_live.py --expect-no-live`,
    whose verdict requires mode 3 after the boot: it is the check for an
    auto image held off by a generator request, not for a manual image. Its
    "NOT as specified" (`runs/autoarm_20261013T035242.json`) is that misfit,
    not a finding. Its data agree with the PASS above: no device `0x051`
    after the boot, 0 witness error frames.
  - Auto image `63eb50f` `_auto` (`GEN_INHIBIT_AUTOARM=GEN_INHIBIT_INHIBIT`,
    md5 `42f2c4bd...`): OTA'd under replay, it came up armed and went live
    0.476 s after the boot, with 4,441 device `0x051` after it; go-live and
    provenance as specified (`runs/autoarm_20261013T040526.json`). It was
    then disarmed by a deliberate mode 0 POST for the restore.
- **Bench incidents:**
  - 03:51: the first OTA of the flight image never ran (the image path was
    not converted for Windows). That run is marked VOID
    (`runs/autoarm_20261013T035111_VOID_path.json`).
  - 04:02: an OTA of the flight image failed with "Connection was reset",
    and the PC's WiFi dropped off the device's AP. On rejoining, the RXQ
    image was still running with uptime continuous (no reboot), and the
    retry succeeded.
- **Restored:** to `dace947` `_man` by OTA at 04:06
  (`runs/bench_flash_restore_dace947_20261013T0406.txt`). On the wire at
  04:07, in mode 0 it sent nothing to 1,500 replayed `0x051`
  (`runs/restore_unarmed_check_20261013T040718.json`, PASS).
- **Run files:** `runs/test_only_inert_{absent,present}_20261013T0*.json`;
  logs `runs/test_only_inert_{absent,present}_63eb50f_20261013.txt` and
  `runs/test_only_inert_pagekey_rerun_63eb50f_20261013.txt`; OTA logs
  `runs/bench_flash_63eb50f_*_20261013T0*.txt`;
  `runs/inert_63eb50f_{flight_ota,autoarm}_20261013.txt`.

**No error frames at a restart -- tester, 2026-10-13 03:28-03:37, on
`63eb50f` `_man_rt` (the restart test build of `gen-inhibit/main`). Result:
PASS. 3 live `/system_reboot` and 3 live forced crashes, each while live in
INHIBIT under replayed traffic, put 0 error frames on the witness, and every
restart record shows the step ran.**
Spec 12.4 row "no error frames at a restart" (added 2026-10-13, user) and spec 3
"No restart leaves the controller on the bus". The plan is the reviewer's.
- **Build:** commit `63eb50f`, built with `-DGI_TEST_RESTART_QUIESCE=1`.
  - The tester built it from a clean worktree: md5 `87b3662f...`. The
    implementor's image (`9062cf2c...`) is the same size. It differs only in
    the compile time and the ELF hash in the app descriptor, and in the image
    checksum and hash at the end.
  - The tester's switch-off build of the same commit is md5 `360f1719...`.
    On it, `build_output.py`'s "the private TWAI abort is confined" row
    passes (exactly one site, in `gen_inhibit.c`). That row failed on
    `42a860d` (entry under 12.3).
- **Control tie:** one live `/system_reboot` on `dace947` `_man` at 03:28,
  same rig and session, put 135 error frames on the witness
  (`runs/restart_error_frames_live_20261013T032846.json`). The 089cd21
  trial's six hook-off restarts gave 132-139.
- **The way back first:** OTA from `dace947` to the test build (`ota_1`),
  then a self-OTA through the same path (`ota_0`, uptime reset, record
  `entries` 1); `runs/bench_flash_63eb50f_rt_20261013T{0329,0331}.txt`.
- **Rig:** `restart_error_frames.py` as in the trial entry below, with no
  hook switch. Crashes used `POST /gi_quiesce?crash=illegal`. The record was
  read with `GET /gi_quiesce` after each restart. One cycle per run, in the
  order reboot, crash, crash, reboot, reboot, crash, then a mode 0 reboot.
  12 V only, USB unplugged; the X2 on `7f197bfd`, untouched.

| Restart (`restart_error_frames_...`) | Condition | Entries | `did_twai` / `did_stdby` | `aborts` | `wait_us` / `timed_out` | Status at entry / at reset | Reset reason | Boot after the POST | Error frames | PCAN failed sends | Lost (non-`0x051`) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `live_20261013T033047` | live, reboot | 2 | 1 / 1 | 0 | 110 / 0 | `0x01c` / `0x00d` | SW | 2.349 s | 0 | 0 | 0 |
| `live_crash_..._033135` | live, panic | 3 | 1 / 1 | 0 | 0 / 0 | `0x00f` / `0x00f` | PANIC | 0.606 s | 0 | 0 | 0 |
| `live_crash_..._033223` | live, panic | 4 | 1 / 1 | 0 | 94 / 0 | `0x01f` / `0x00f` | PANIC | 0.610 s | 0 | 0 | 0 |
| `live_..._033311` | live, reboot | 5 | 1 / 1 | 0 | 0 / 0 | `0x00c` / `0x00c` | SW | 2.359 s | 0 | 0 | 0 |
| `live_..._033359` | live, reboot | 6 | 1 / 1 | 0 | 0 / 0 | `0x00c` / `0x00c` | SW | 2.358 s | 0 | 0 | 0 |
| `live_crash_..._033448` | live, panic | 7 | 1 / 1 | 0 | 0 / 0 | `0x00f` / `0x00f` | PANIC | 0.606 s | 0 | 0 | 0 |
| `off_..._033536` | mode 0, reboot | 8 | 1 / 1 | 0 | 42 / 0 | `0x01c` / `0x00c` | SW | 2.342 s | 0 | 0 | 0 |

  - **Witness:** no error frame anywhere in the 40 s of any of the 7 runs.
    The `0x051` lower bound was 0 in all, and the PCAN's status read OK at
    the end of every run.
  - **Records:** the device read live before the POST in all 6 live runs.
    `entries` moved by exactly 1 per restart. `tec` and `rec` at entry were
    0 throughout. The build after every restart was `63eb50f`, on `ota_0`.
  - **`aborts` read 0 in every restart:** no frame of ours was awaiting the
    bus when the step ran. So the abort path the step goes through was not
    exercised in these runs.
- **"Both stay that way until the driver is installed again" (spec 3),
  recorded:**
  - `at_app_start` after every one of the 7 restarts: `stdby_out` 1,
    `stdby_oe` 1, `twai_clk` 1, `twai_rst` 0, `twai_in_reset` 1, `tec` 0.
  - `pre_install` was null after every restart in the batch. The manual
    image boots OFF and no driver was installed before the next restart, as
    the build message says.
  - To read it once: after the mode 0 reboot, mode 3 was POSTed at uptime
    49.25 s and mode 0 again 2 s later. `pre_install` (t 49.273 s,
    immediately before that boot's first driver install) read `stdby_out` 1,
    `stdby_oe` 1, `twai_in_reset` 1, status `0x01c`, `tec` 0
    (`runs/quiesce_pre_install_read_63eb50f_20261013.json`).
- **Every WiCAN frame after the POST** (reviewer's item 4;
  `runs/restart_device_frames_63eb50f_20261013.{json,txt}`). Ids are taken
  from `vtrux-wican-diag.dbc`, plus device `0x051` by the equal-B5 rule. The
  X2's `0x7F4`-`0x7F7` are not counted.
  - The last WiCAN frame before each restart was a device `0x051`, 222-276 ms
    before the computed boot: POST +2121 to +2130 ms for reboots (the
    `dace947` control included), POST +330 to +337 ms for panics.
  - No WiCAN frame in the 1 s after the computed boot in any run.
  - The mode 0 run had no WiCAN frame after the POST.
- **The self-OTA's restart, recorded:** in mode 0 with no driver installed,
  the record read status `0x05f` (ES set) and `rec` 129 at entry, with
  `did_twai` 1.
- **Restored:** to `dace947` `_man` by OTA at 03:37
  (`runs/bench_flash_restore_dace947_20261013T0337.txt`). On the wire at
  03:37, in mode 0 it sent nothing to 1,500 replayed `0x051`
  (`runs/restore_unarmed_check_20261013T033736.json`, PASS). The page reads
  `dace947`, and `/gi_quiesce` answers 404.
- **Run files:** `runs/restart_error_frames_{live,live_crash,off}_20261013T03[2-5]*.json`
  with their witness logs; batch log
  `runs/restart_quiesce_63eb50f_batch_20261013.txt`; summary
  `runs/quiesce_63eb50f_summary_20261013.jsonl`.

**Restart-quiesce trial: the hook on and off at a live restart -- tester,
2026-10-13 00:55-01:07, on the trial image `089cd21` `_man`. Result, observed
and not judged against a spec section (none governs it yet): with the hook
on, six live restarts (three `/system_reboot`, three forced panics) put 0
error frames on the wire and lost no replayed frame. With it off, six
interleaved live restarts put 132-139 error frames on the wire each, the same
burst as `dace947`.**
The user asked (2026-10-13, via the reviewer) for the trial to show this
before any spec text is written. The plan is the reviewer's.
- **Build:** commit `089cd21` on `gen-inhibit/trial-restart-quiesce`, off
  `ad80037`, built with `-DGI_TRIAL_RESTART_QUIESCE=1`.
  - The tester built it from a clean worktree: md5 `045cfc2d...`. The
    implementor's image (md5 `b3e92a7c...`) is the same size. It differs in
    69 bytes: the compile time (offset `0x73`) and the ELF hash (`0xb0`) in
    the app descriptor, and the image checksum and hash at the end.
  - Interface from the implementor's build message:
    - `POST /gi_quiesce?hook=1|0` sets the switch, and the page field
      `quiesce_hook` reports it;
    - `quiesce_entries` counts the hook's entries;
    - `POST /gi_quiesce?crash=illegal` panics 300 ms after answering;
    - `GET /gi_quiesce` returns the last restart's record and a snapshot
      taken before `app_main` (`at_app_start`).
- **The way back, proved first.** The trial went on by OTA from `dace947`
  (`runs/bench_flash_trial_089cd21_20261013T0055.txt`). It read `089cd21`,
  `ota_running` `ota_0`, mode 0, `drv` null, `quiesce_entries` 0.
  - It was then flashed onto itself through the same upload path
    (`runs/bench_flash_selfota_089cd21_20261013T0056.txt`).
  - After that it read `ota_1`, uptime 7.7 s, `089cd21`, and
    `quiesce_entries` 1. The partition change shows the write was real.
  - Reads: `runs/quiesce_read_after_{trial_ota,selfota}_20261013.json`.
- **Rig:** `restart_error_frames.py` as in the entry below (the PCAN replays
  0-40 s with `auto_reset`; the Kvaser witnesses; the page is polled at
  5 Hz; mode 3 at 5 s for live; the restart at 20 s), with the options added
  for this trial. Tested by `test_restart_error_frames.py`: 22 checks,
  15 mutants, all caught (`runs/test_restart_error_frames_hook_20261013.txt`).
  - `--hook on|off` sets the switch in mode 0 before each cycle. It refuses
    the cycle unless the page reads it back as requested.
  - Each cycle is judged by the last readback before the restart POST.
  - `--restart crash` POSTs the crash trigger instead of `/system_reboot`.
  - After each restart the cycle records `GET /gi_quiesce`, the build and the
    reset reasons.
  - One cycle per run, interleaved:
    - live reboots: on, off, off, on, on, off;
    - live crashes: on, off, off, on, on, off;
    - mode 0 reboots: on, off.
  - 12 V only, USB unplugged; the X2 on `7f197bfd`, untouched.
  - The first cycle's `GET /gi_quiesce` failed: Git Bash rewrote the path
    argument. Its record was read before any further restart
    (`runs/quiesce_read_after_live_hookon_20261013T005557.json`). The batch
    then ran with `MSYS_NO_PATHCONV=1`.

| Restart (`restart_error_frames_...`) | Condition | Hook readback | Entries | `did_twai` / `did_stdby` | `wait_us` / `timed_out` | Status at entry / at reset | Reset reason | Boot after the POST | Error frames | First / last relative to the boot | PCAN failed sends | Lost (non-`0x051`) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `live_hookon_20261013T005557` | live, reboot | on | 1 -> 2 | 1 / 1 | 64 / 0 | `0x01c` / `0x00d` | SW | 2.335 s | 0 | -- | 0 | 0 |
| `live_hookoff_..._005712` | live, reboot | off | 2 -> 3 | 0 / 0 | 0 / 0 | `0x01c` / `0x000` | SW | 2.327 s | 134 | -0.221 / -0.187 s | 0 | 0 |
| `live_hookoff_..._005801` | live, reboot | off | 3 -> 4 | 0 / 0 | 0 / 0 | `0x00c` / `0x000` | SW | 2.355 s | 135 | -0.220 / -0.192 s | 0 | 0 |
| `live_hookon_..._005850` | live, reboot | on | 4 -> 5 | 1 / 1 | 50 / 0 | `0x01c` / `0x00d` | SW | 2.342 s | 0 | -- | 0 | 0 |
| `live_hookon_..._005939` | live, reboot | on | 5 -> 6 | 1 / 1 | 58 / 0 | `0x01c` / `0x00d` | SW | 2.338 s | 0 | -- | 0 | 0 |
| `live_hookoff_..._010027` | live, reboot | off | 6 -> 7 | 0 / 0 | 0 / 0 | `0x00c` / `0x000` | SW | 2.340 s | 132 | -0.224 / -0.193 s | 0 | 0 |
| `live_hookon_crash_..._010116` | live, panic | on | 7 -> 8 | 1 / 1 | 0 / 0 | `0x00f` / `0x00f` | PANIC | 0.625 s | 0 | -- | 0 | 0 |
| `live_hookoff_crash_..._010205` | live, panic | off | 8 -> 9 | 0 / 0 | 0 / 0 | `0x00f` / `0x000` | PANIC | 0.608 s | 138 | -0.220 / -0.187 s | 0 | 0 |
| `live_hookoff_crash_..._010254` | live, panic | off | 9 -> 10 | 0 / 0 | 0 / 0 | `0x01f` / `0x000` | PANIC | 0.614 s | 132 | -0.224 / -0.197 s | 1, at +0.371 s | 6 |
| `live_hookon_crash_..._010343` | live, panic | on | 10 -> 11 | 1 / 1 | 0 / 0 | `0x00f` / `0x00f` | PANIC | 0.607 s | 0 | -- | 0 | 0 |
| `live_hookon_crash_..._010431` | live, panic | on | 11 -> 12 | 1 / 1 | 148 / 0 | `0x01f` / `0x00f` | PANIC | 0.622 s | 0 | -- | 0 | 0 |
| `live_hookoff_crash_..._010520` | live, panic | off | 12 -> 13 | 0 / 0 | 0 / 0 | `0x01f` / `0x000` | PANIC | 0.606 s | 139 | -0.224 / -0.191 s | 0 | 0 |
| `off_hookon_..._010609` | mode 0, reboot | on | 13 -> 14 | 1 / 1 | 0 / 0 | `0x00f` / `0x00f` | SW | 2.327 s | 0 | -- | 0 | 0 |
| `off_hookoff_..._010657` | mode 0, reboot | off | 14 -> 15 | 0 / 0 | 0 / 0 | `0x00c` / `0x000` | SW | 2.338 s | 0 | -- | 0 | 0 |

  - **Entries:** `quiesce_entries` moved by exactly 1 per restart in all 14,
    on the page and in the record (`entries`). The record's `hook` matched
    the readback in every cycle.
  - **Hook state:** every cycle read the hook back as requested before the
    POST. After every boot it read off: the switch did not persist, as the
    build message says.
  - **Error counters:** `tec` and `rec` at entry were 0 in all 14.
    `timed_out` was 0 in all 14; `wait_us` was 0-148.
  - **Reset reasons and partition:** SW after every `/system_reboot` and
    PANIC after every crash (page and record agree). `ota_running` was
    `ota_1` and the build `089cd21` after every restart.
  - **Live before every restart:** the device read live before the POST in
    all 12 live cycles. Every live cycle with the hook on gave no error frame
    anywhere in the 40 s.
  - **The bursts with the hook off** have the same timing relative to the
    boot as on `dace947` (first frame 0.220-0.224 s before the boot, span
    27.7-34.2 ms, a clump of 128-129 then single frames, except
    `..._010254`: clumps of 31 and 98).
  - **Boot timing:** the panics booted 0.606-0.625 s after the POST, against
    2.326-2.355 s for the reboots. The trigger answers and panics 300 ms
    later.
  - **The one loss:** `..._010254` (off, panic) had 1 PCAN failed send at
    +0.371 s on the send clock. The burst started at +0.390 s on the wire
    clock. 6 replayed frames other than `0x051` never reached the wire (one
    each of `052`, `054`, `400`, `440`, `596`, `18FFD4B3`). The `0x051`
    lower bound was 0 in all 14. `restart_loss_windows.py`'s fixed windows
    assume a 2.3 s boot, so they do not place a panic's losses, and are not
    used here. The PCAN's status read OK at the end of every run.
- **`at_app_start`, the snapshot before `app_main`:**
  - after every hook-on restart (live or mode 0): `stdby_out` 1, `stdby_oe` 1,
    `twai_clk` 1, `twai_rst` 0, `twai_in_reset` 1, `tec` 0;
  - after every live hook-off restart: `stdby_out` 0, `stdby_oe` 1,
    `twai_in_reset` 0;
  - after the mode 0 hook-off restart: `stdby_out` 1, `twai_in_reset` 1. Its
    record shows the controller already in reset at entry
    (`in_reset_at_entry` 1), which no other cycle shows;
  - after the OTA from `dace947` (no hook in the restarting image):
    `stdby_out` 1, `twai_clk` 1, `twai_in_reset` 0, status `0x04f`.
- **Observed against the reviewer's expectations, as stated in the plan:**
  - Met:
    - hook on while live gave `did_twai` 1, `did_stdby` 1, 0 error frames,
      0 failed sends, and `at_app_start` with `stdby_out` 1, `stdby_oe` 1 and
      the controller in reset mode;
    - hook off reproduced the burst, with `did_twai` 0;
    - no `timed_out`.
  - Not met: **in mode 0 with the hook on, `did_twai` read 1, not 0.** At
    entry that cycle read `twai_clk` 1, `twai_rst` 0, `in_reset_at_entry` 0,
    status `0x00f`. In mode 0 the controller's clock was on and the
    controller was out of reset at the hook. Both mode 0 restarts gave 0
    error frames, as on `dace947`.
  - `quiesce_entries` after the OTA from `dace947` read 0, not null. That
    boot was a SW reset from an image without the hook, not a power-on.
- **Not in scope, and not seen:** no failed boot. No reset reason other than
  the one triggered. No partition change other than the two OTAs.
- **Restored:** to `dace947` `_man` (md5 `babaa13e...`) by OTA at 01:08
  (`runs/bench_flash_restore_dace947_20261013T0109.txt`). Confirmed on the
  wire at 01:09: in mode 0 it sent nothing to 1,500 replayed `0x051`
  (`runs/restore_unarmed_check_20261013T010852.json`, PASS). The page reads
  `dace947`, mode 0, `drv` null, and none of the trial's fields.
- **Run files:** `runs/restart_error_frames_{live,off}_hook{on,off}[_crash]_20261013T*.json`
  with their witness logs; batch log `runs/restart_quiesce_batch_20261013.txt`;
  per-cycle summary `runs/quiesce_trial_summary_20261013.jsonl`
  (`quiesce_trial_summary.py`).

**Error frames at a WiCAN restart, characterised -- tester, 2026-10-12
19:37-19:42, on `dace947` `_man`. Result, observed and not judged: the burst
comes only when the controller is active at the restart. Three live INHIBIT
restarts gave 107-133 error frames each, from about 0.22 s before the
computed boot instant; three mode 0 restarts (driver not installed) gave 0.**
The user ruled (2026-10-12, via the reviewer) that the burst first seen in the
auto-arm runs be characterised before the truck, without changing the image.
No spec section governs it.
- **Rig:** `restart_error_frames.py`, tested by `test_restart_error_frames.py`
  (16 checks; 9 mutants, all caught; `runs/test_restart_error_frames_20261012.txt`).
  - The PCAN replays `scottsvalley_armable_300s.log` 0-40 s with
    `auto_reset`, recording every send and every failed send. The Kvaser
    witnesses, recording error frames by hardware timestamp. The page is
    polled at 5 Hz.
  - Live: mode 3 POSTed 5 s in; the device read live before the restart in
    all three.
  - `/system_reboot` is POSTed 20 s in. The manual image boots OFF, so the
    device is silent after every restart.
  - Boot instant: the median of (read time - `uptime_ms`) over the reads
    after the restart. The witness clock is tied to the host by the replay.
  - The six restarts alternated conditions (off, live, off, live, off, live);
    12 V only, USB unplugged; the X2 on `7f197bfd`, untouched.

| Restart | Condition | Error frames | First / last after the POST | First / last relative to the boot | Span | Clumps (gap > 1 ms) | Largest gap | PCAN failed sends |
|---|---|---|---|---|---|---|---|---|
| `restart_error_frames_off_20261012T193749` | mode 0, `drv` null | 0 | -- | -- | -- | -- | -- | 0 |
| `..._live_20261012T193837` | live INHIBIT | 129 | +2.107 / +2.134 s | -0.221 / -0.194 s | 26.8 ms | 5: 1, 24, 85, 18, 1 | 7.33 ms | 2, at +2.088 and +2.102 s |
| `..._off_20261012T193925` | mode 0 | 0 | -- | -- | -- | -- | -- | 0 |
| `..._live_20261012T194014` | live INHIBIT | 133 | +2.101 / +2.126 s | -0.217 / -0.193 s | 24.5 ms | 6: 128 then five single frames | 4.26 ms | 0 |
| `..._off_20261012T194102` | mode 0 | 0 | -- | -- | -- | -- | -- | 0 |
| `..._live_20261012T194150` | live INHIBIT | 107 | +2.107 / +2.136 s | -0.220 / -0.191 s | 28.8 ms | 5: 1, 24, 31, 21, 30 | 6.48 ms | 4, +2.088 to +2.114 s |

  - The mode 0 restarts had no error frame anywhere in the 40 s. The live
    ones had none outside the burst.
  - The burst begins about 2.10 s after the POST and ends about 0.19 s
    before the boot instant, so it falls in the restart, not in the boot.
    The boot came 2.318-2.328 s after the POST in five restarts.
- **Replayed frames lost, now that the PCAN has `auto_reset`:**
  - in the two live restarts where sends failed: 45 and 66 frames, other
    than `0x051`, never reached the wire (an exact count per id and
    payload);
  - none in the live restart without failed sends, and none in any mode 0
    restart;
  - by the `0x051` lower bound, 0 commands lost in every run. One of the
    third live run's failed sends was a `0x051`, so it sent 3,999 commands
    rather than 4,000;
  - per-window counts (`restart_loss_windows.py`,
    `runs/restart_loss_windows_20261012.jsonl`) put the net loss between
    1.5 and 3.5 s after the POST (49 and 72 there, against boundary noise of
    up to 19 in the clean runs). That is the restart, where the failed sends
    are. The harness's own "in restart window" field undercounts and is not
    used: its 0.1 s witness slack lets a repeated payload stand in for a
    lost one.
  - The PCAN's status read OK at the end of every run.
- **Observed, not explained:** the first mode 0 restart computed its boot
  4.457 s after the POST, against 2.318-2.328 s for the other five. Its
  reads after the restart are self-consistent (uptime rising at 1.00 s/s,
  reset reason SW). With the driver not installed there is no wire signal to
  check it against.
- **Not done, as ruled:** no stock firmware was flashed for comparison (the
  WiCAN is on 12 V with USB unplugged, and stock v4.13 has the OTA defects).
- **Run files:** `runs/restart_error_frames_{off,live}_20261012T19*.json`
  with their witness logs; batch log `runs/restart_error_frames_batch_20261012.txt`.

**Re-arm into INHIBIT from each mode, and OFF entry -- tester, 2026-10-10
18:52-19:06, on `cc34c80` `_man` (reviewer's before-and-after set for the
re-arm finding). Result: the stale burst follows leaving OBSERVE only.**
- **Rig:** as the quiesce arms (Scotts Valley, 35 s, offset 500), with the
  source mode armed before the replay.
  - Re-arm arms: the mode is POSTed into INHIBIT 20 s in, the same timing in
    every arm (`--post-schedule 20:3`, starting mode OBSERVE, PASSIVE or
    RESPOND).
  - RESPOND as the starting mode uses capture 20-55 s; e4 fixes RESPOND's
    window start.
  - OFF entries: INHIBIT POSTed 5 s into the replay (`--arm-after 5`).
  - Two INHIBIT -> OBSERVE -> INHIBIT arms polled the status page at 1 Hz
    (`--poll-hz 1`, which records `mode` and `self_off` each time), a
    separate condition.
  - Two sets, interleaved within each.
- **No panic** in any of the 18 arms: uptime continuous, POWERON unchanged
  (`runs/rearm_uptime_20261010T185231.json`, `runs/rearm2_uptime_20261010T185947.json`).

| Into INHIBIT from | Arms | Live after the POST | Stale or late device `0x051` (provenance) | `rx_missed` / `unverified_max_backlog` |
|---|---|---|---|---|
| OBSERVE (unpolled) | 5 | 61-87 ms | stale 5/5, late 3/5 | 28, 28, 2, 5, 0 / 29-39 |
| OBSERVE (I -> O -> I, polled 1 Hz) | 2 | 61, 65 ms | stale 2/2, late 1/2 | 27, 0 / 31, 8 |
| PASSIVE | 3 | 15.4-15.5 ms | none | 0 / 0-2 |
| RESPOND | 3 | 21-40 ms | none | 0 / 0 |
| OFF (POST at 5 s) | 5 | 150.6-150.9 ms | none | 0, 0, 0, 8, 0 / 0-1 |

- **The stale burst is tied to leaving OBSERVE (listen-only).**
  - Every one of the ten OBSERVE -> INHIBIT re-arms of the day sent stale
    answers: the three quiesce arms above plus these seven.
  - A late frame reached the wire in 6 of the 10, and the device counted a
    late skip for 2 of those 6.
  - PASSIVE and RESPOND re-armed cleanly, as did OFF.
- **OFF entry (implementor's prediction, pinned before the run):** "OFF
  overflows sometimes, but sends no stale answer, because it isn't live until
  about 150 ms in". Confirmed: it overflowed in 1 of 5 (`rx_missed` 8,
  `ctr_bad` 1), sent no stale or late answer in any of the 5, and went live
  150.6-150.9 ms after the POST. The mode-entry arms add 3 of 3 clean.
- **`self_off` during OBSERVE** (the two polled arms): every sample in the
  OBSERVE span read mode 1 with `self_off` "". Not a finding (reviewer,
  2026-10-10): spec 8's report is for a quiesce that forces mode OFF, and a
  requested OBSERVE is not that, so "" is correct there.
- **Analysis files:** `runs/rearm_analysis_20261010T185231.jsonl`,
  `runs/rearm2_analysis_20261010T185947.jsonl`,
  `runs/rearm2_off_entry_20261010T185947.jsonl`.
- **Run files:** `runs/e4_rearm_*_20261010T185231_arm{1..9}.json` and
  `runs/e4_rearm2_*_20261010T185947_arm{1..9}.json`, with their witness logs.

**PASSIVE on the wire -- tester, 2026-10-10 18:35-18:41, on `cc34c80` `_man`.
Result: as specified.** Spec 3.1, judged against the 12.4 wording as the
reviewer ruled. Listed in spec 13 as "bench, no harness".
- **Rig:**
  - `e4_device_calibration.py --mode passive` (new), PCAN replay, Kvaser
    witness, X2 app running, 12 V only, offset 500.
  - Six 30 s arms of `truck_passive2.log` 0-30 s, PASSIVE and INHIBIT
    interleaved (P, I, I, P, P, I). This window ends with the recorded engine
    start, so it exercises the engine-turning trip.
  - One pair on `scottsvalley_armable_300s.log` 0-30 s, which stays live
    throughout.
- **Analysis:** `passive_analysis.py`, tested by `test_passive_analysis.py`
  (6 checks, 4 mutants); results in `runs/passive_analysis_20261010T183526.jsonl`.
  - The diag pages are decoded off the wire with the DBC.
  - Within each arm, the counter's rise between consecutive pages is set
    against the replayed commands the witness saw in that interval (by
    provenance). The counter is `diag_would_tx` in PASSIVE and `diag_tx_ok` in
    INHIBIT, so both modes are judged by one method.
  - Intervals: FULL (rise = commands +/-1), IDLE, PARTIAL (allowed only at the
    edges of the live span) and OVER (never allowed).

| Arm | Device `0x051` (provenance) | Counter rise in this arm | Intervals: full / idle / partial / over; sum of (rise - commands) over full | First page with `abort_latched`: replay index, s |
|---|---|---|---|---|
| PASSIVE tp2 (1) | 0 | would_tx +670 | 4 / 19 / 2 / 0; 0 | 18,112, 7.71 |
| INHIBIT tp2 (2) | 670 (= `tx_ok`) | tx_ok 670 | 5 / 18 / 1 / 0; 0 | 17,988, 7.66 |
| INHIBIT tp2 (3) | 670 | tx_ok 670 | 5 / 18 / 1 / 0; 0 | 17,934, 7.64 |
| PASSIVE tp2 (4) | 0 | would_tx +670 | 5 / 18 / 1 / 0; 0 | 17,014, 7.25 |
| PASSIVE tp2 (5) | 0 | would_tx +670 | 5 / 18 / 1 / 0; 0 | 16,386, 6.98 |
| INHIBIT tp2 (6) | 670 | tx_ok 670 | 4 / 19 / 1 / 0; 0 | 16,283, 6.94 |
| PASSIVE sv (7) | 0 | would_tx +2,985 | 24 / 0 / 0 / 0; 0 | never latched |
| INHIBIT sv (8) | 2,985 | tx_ok 2,985 | 23 / 0 / 0 / 0; 0 | never latched |

- **Never transmits `0x051`:** 0 device `0x051` in all four PASSIVE arms by
  provenance, with every replayed frame found. `tx_ok` was 0.
- **The would-transmit count equals the commands seen while would-be live, in
  the same arm.**
  - In every full interval of every PASSIVE arm the rise equalled the
    commands exactly (sum of the differences 0), with no OVER interval, and
    partial intervals only where the live span starts or ends.
  - Each PASSIVE arm added exactly what INHIBIT transmitted on the same
    window (670; 2,985). That is a cross-arm agreement only, not the
    criterion.
- **Releases and trips as INHIBIT reports them:** the same `(arm_block,
  abort_reason)` sequence in every tp2 arm of both modes.
  - First block 0 (`GI_BLOCK_NONE`, the gate satisfied). In PASSIVE arm 1 the
    first page came early enough to catch block 2, "gate not yet evaluated",
    before it.
  - Then block 14 (`GI_BLOCK_ABORTED`) with abort 5
    (`GI_ABORT_ENGINE_TURNING`, "engine turning while armed").
  - The latch page lands at replay index 16,283-18,112 across both modes. The
    spread is within one diag page period (about 1.2 s, about 2,800 frames),
    which is the resolution this method has.
  - On Scotts Valley, both modes stayed live with no block or abort.
- **Readable from the frames alone:** `diag_mode` reads 4 in PASSIVE and 3 in
  INHIBIT on every `0x7F1`. The would-transmits are on `0x7F8`
  (`diag_would_tx`), separate from `diag_tx_ok`.
- **Observed, not judged:** `diag_would_tx` is cumulative, never reset. It read
  670, 1,340 and 2,010 at the end of the successive PASSIVE arms, and 4,995
  after the Scotts Valley one. It kept its value through the INHIBIT arms in
  between (the status page read 4,995 at 18:41 in mode 0). The skip counters
  are documented as resetting on a re-arm; neither spec 3.1 nor the DBC
  comment says whether this one should.
  - The reviewer confirmed it in the source: `gi_reset_stats()` clears
    `tx_ok` and the skips on arming, not `would_tx`. With the spec silent, it
    is a spec gap for the user.
  - The result above stands either way: the interval method uses the rise
    between pages, which a counter that never resets does not affect.
- **Run files:** `runs/e4_passive_{passive,inhibit}_{tp2,sv}_20261010T183526_arm{1..8}.json`
  and their witness logs.

**OBSERVE is listen-only in hardware -- tester, 2026-10-10, on `cc34c80`
`_man`. Result: as specified.** This is the spec 12.4 row; spec 13 lists it
as "bench, no harness".
- **Harness:** `observe_ack_probe.py`, tested by
  `test_observe_ack_probe.py` (12 checks, 5 mutants caught).
- **Run file:** `runs/observe_ack_probe_20261010T170140.json`.
- **The WiCAN as the only possible ACKer:**
  - the Kvaser CLOSED (it ACKs whenever it is open);
  - the X2 held off the bus (below);
  - the Innomaker on the X2's CAN2, not this segment;
  - the PCAN as the transmitter, opened fresh for each arm, sending one
    `0x6A5` frame and reading its own status 0.5 s later.
- **Supply:** 12 V only, USB unplugged.

| Arm | Page (mode, `drv.state`) | PCAN status | Verdict |
|---|---|---|---|
| mode 0, the control | 0, null | BUSHEAVY (ACK errors) | nothing else ACKs, so the run is valid |
| OBSERVE | 1, running | BUSHEAVY | as specified |
| RESPOND | 2, running | OK (ACKed) | as specified |
| OBSERVE | 1, running | BUSHEAVY | as specified |
| mode 0, the control | 0, null | BUSHEAVY | still valid |

- **The RESPOND arm also shows the segment was not stuck dominant** while
  the X2 was held.
- **After each OBSERVE arm the WiCAN read TEC 0** (`bus_error` 97): it never
  transmitted.
- **After the RESPOND arm it read TEC 121, `bus_error` 5,956.** The arm
  sequencing explains it:
  - in RESPOND the device sends its diag pages from the moment the mode is
    set;
  - for the 1.5 s settle before the PCAN opens nothing can ACK them, so it
    retransmits back to back and reaches error-passive (TEC 128), where the
    ACK-error exception stops it rising;
  - while the PCAN is open it ACKs a few pages, and each success takes TEC
    down by 1;
  - `bus_error` 5,956 fits about 1.5 s of continuous retransmission.
  - Not reconciled exactly: the PCAN counted 4 received frames against the
    7 implied by 128 -> 121.
  - The device ended in mode 0.
- **How the X2 was taken off the bus** (reviewer-approved 2026-10-10):
  `x2_hold.py`, through COM3.
  - It was held in ROM download mode 16:58-17:13, with no flash write, so
    custody of its image is untouched. The only writes were the input-enable
    bits of GPIO7/GPIO6, in volatile IO_MUX registers in ROM mode, cleared
    by the reset at release.
  - Before the hold it read `7f197bfd` on the wire (16:59), and again after
    the release (17:13:46): `--require-witness`, env `esp32-can-x2-witness`,
    fw 4 (`runs/x2_identify_{before_hold_20261010T1659,after_hold_20261010T1714}.txt`).
  - **TX pin while held:**
    - GPIO_ENABLE bit 7 was 0, so GPIO7 (CAN1 TXD) was not driven, and the
      IO_MUX showed no pulls.
    - The ROM leaves input-enable clear on GPIO6/7, so GPIO_IN read 0
      meaninglessly at first (`runs/x2_hold_hold_20261010T165839.json`).
      - **Correction (tester, 2026-11-07): that file is in the baseline
        folder now,** `runs/baseline_c65c45e/bench/x2/x2_hold_hold_20261010T165839.json`
        (sha256 `0f0f10c6...f472`), not in `runs/`. Found when the hash
        manifest could not resolve the citation.
    - With input-enable set (one esptool session), GPIO_IN `0x3c0000cd` put
      GPIO7 = 1 and GPIO6 = 1: TXD idles recessive and the transceiver sees
      a recessive bus (`runs/x2_hold_enable-inputs_20261010T170115.json`).
  - `test_x2_hold.py`: 13 checks, 5 mutants caught.

**Host suite, 2026-10-04, on `f363674`:** 78 of 78 scenarios pass with
freshly regenerated replay scenarios. That includes
`shutdown-suppress-twice`, the host half of the D3 row: two `0x10` episodes
in one power cycle, edges at 2/4/6/8 s, live throughout, `tx_ok` 249.
- **Run:** by the reviewer on madhouse-debian, from `git archive f363674`.
- **Record:** review tracker, 2026-10-04 ("78 passed, 0 failed, 0 without
  goldens"). There is no run file, because the run tree was removed
  afterwards.

The receive-backlog row's result is under section 5, and the
abort-and-recheck row's is under 5.2.

The error-frame row's "at the trip threshold" regime (tester, 2026-10-08,
X2 jammer): **FAIL**, see section 7, "Trip 6 at the trip threshold".

**No stale image name -- tester, 2026-10-13, real builds on `NICKY-XPS`.
Result: PASS on `d74777d` (the naming module), all eleven steps. FAIL on
`95619b7` (before it) on five steps, as required. The same script ran both
directions.**
Spec 12.4 row "no stale image name" and 12.3 item 3, "Every build names its
image from the tree it compiled" (added 2026-10-13, user). The reviewer's
readings of the 12.3 text (same day) add the `README.md` steps and the no-git
step.
- **The check:** `test/build_checks/stale_name_check.py` (firmware repo,
  branch `tester/stale-image-name`, `de324d7`, local).
  - It runs in a throwaway detached worktree of the commit, beside the repo,
    with `managed_components/` copied from the `63eb50f` tree (the same
    `dependencies.lock`). Commits land on the detached HEAD only, no branch
    moves, and the worktree is removed afterwards.
  - One build directory is rebuilt eleven times. The source edit is a
    comment appended to `main/gen_inhibit.c`.

    | Step | Change, then build | Must read |
    |---|---|---|
    | clean | fresh tree, `idf.py build` | `<C>` |
    | edit/idf | source edited, `idf.py build` | `<C>-dirty` |
    | revert/idf | reverted, `idf.py build` | `<C>` |
    | edit/ninja | the same edit, `ninja -C build` | `<C>-dirty` |
    | revert/ninja | reverted, `ninja -C build` | `<C>` |
    | commit/ninja | an edit committed (C1), `ninja` | `<C1>` |
    | commit/idf | another committed (C2), `idf.py build` | `<C2>` |
    | readme-edit/ninja | `README.md` edited, `ninja` | `<C2>-dirty` |
    | readme-revert/ninja | reverted, `ninja` | `<C2>` |
    | readme-commit/ninja | a `README.md`-only commit (C3), `ninja` | `<C3>` |
    | nogit/ninja | `.git` moved aside, a source edit, `ninja` | build fails, no image |

  - Each step judges the one image that build wrote: a top-level
    `build/wican-fw_*.bin` modified during the step. It reads the filename,
    the `esp_app_desc_t` version (0x10) and its project_name (0x30). Each
    must name HEAD (an abbreviation of its sha) and carry `-dirty` exactly
    when the step left the tree dirty, and `git status` must agree with the
    step.
  - Undetermined fails: a failed build, no image, two images, or no app
    descriptor. A step whose naming does not re-run writes no image when
    nothing compiled changed, and that FAILS ("re-runs the naming before
    the build", reviewer).
  - The no-git step first shows git cannot answer in the tree
    (`GIT_CEILING_DIRECTORIES` stops it looking above). Its source edit
    means a build that ignored git's failure would recompile and write an
    image. Then `.git` and the edit are put back.
  - Names and versions are compared, never md5s: rebuilds of one commit
    differ (compile time in the image; implementor).
- **Tests of the check:** `test_stale_name_check.py` covers the parsing, the
  prefix and dirty match, the step window, every undetermined case and the
  no-git judgement. 22 mutants, all caught
  (`runs/test_stale_name_check_mutants_de324d7_20261013.txt`).
- **Passing half, `d74777d`** (`runs/stale_name_d74777d_20261013T070558.{txt,json,log}`):
  - Every step PASSES. Each image read its tree: `d74777d`,
    `d74777d-dirty`, `d74777d`, `d74777d-dirty`, `d74777d`, then the new
    commits `b08009e`, `1979b37`, `1979b37-dirty`, `1979b37`, `5763866`.
  - The no-git build failed in 0.3 s (rc 1) and wrote no image. The log
    shows the naming check stopping it before anything was compiled.
  - The worktree was removed afterwards.
- **Failing half, `95619b7`** (`runs/stale_name_95619b7_20261013T073713.{txt,json,log}`):
  FAIL on five steps.
  - edit/idf and edit/ninja: the images read `95619b7` in filename, version
    and project_name, with no `-dirty`. This is the a3394ca trap: new code
    under the clean name, through `idf.py build` as well as bare `ninja`.
  - readme-edit/ninja: no image was written, so the tree's change never
    reached a name. readme-revert/ninja failed the same way, as a
    consequence: the previous step never wrote a dirty name to replace.
  - nogit/ninja: with git unable to answer, the build succeeded and wrote
    `wican-fw_obd_d20e264_man.bin`, an image named for the last commit seen.
  - Observed, and not a failure: the three commit steps pass on `95619b7`.
    A new commit was picked up by `idf.py build` and by bare `ninja` alike.
    Before the module, the trap was uncommitted changes and a missing git.
  - An earlier ten-step run of the same commit (no no-git step) agrees
    step for step (`runs/stale_name_95619b7_20261013T064814.txt`).
- **Voided:** the first run, `runs/stale_name_95619b7_20261013T062844_VOID_window.*`,
  was a harness defect. The step window opened 1 s before the step, so
  commit/ninja counted revert/ninja's image as its own and failed for "2
  images". Fixed in `6fba9dd`, with a test that fails on the old window.
- **No bench use.** The builds ran in throwaway worktrees only.

**The name enters the build once -- tester, 2026-10-13, real builds on
`NICKY-XPS`, the shim on `madhouse-debian`, the wire on the bench. Result:
PASS on `e998385` (the change), every part. FAIL on `d74777d` (before it) on
the README steps and the module fixture, as required.**
Spec 12.4 row "the name enters the build once" and 12.3 item 3 "The name
enters the build once" (added 2026-10-13, user). The row is "host (build); the
reported hash on the bench" (user, 2026-10-13). Readings (reviewer,
2026-10-13):
- **"same as before the change":** the formula is unchanged on both sides. On
  `d74777d`, `0x7F3` == fnv1a32 of its name. On `e998385`, `0x7F3` ==
  fnv1a32 of the descriptor version == fnv1a32 of the image name's
  describe part. fnv1a32 of the descriptor is the host cross-check.
- **the over-long name:** on the product, the build fails and writes no
  image, by any guard, recording which guard fired. On the module, a minimal
  fixture project with no project-name guard: the build must fail with an
  error that names the version field.
- **The check:** `test/build_checks/name_once_check.py` (firmware repo,
  branch `tester/name-once`, `a4b906a` for the runs below, local). It runs
  in a throwaway local clone (so the tag never reaches the repo's refs),
  with `managed_components/` copied in. The clone and the fixture are
  removed afterwards.

  | Step | Change, then `idf.py build` | Must |
  |---|---|---|
  | clean | none | one image; filename, descriptor version and project_name name HEAD |
  | readme-edit | `README.md` edited | an image named `<C>-dirty`; no object whose source is under `main/` or `components/` recompiled |
  | readme-revert | reverted | an image named `<C>`; none recompiled |
  | overlong | a 38-character tag on HEAD | the build fails, no image |
  | module | the fixture, with the commit's `cmake/git_tree_name.cmake`, tagged the same | the build fails, no image, the error names the version field |

  - A recompile is an object in `compile_commands.json` whose mtime changed
    across the build. Other recompiles are recorded, not judged.
- **Passing half, `e998385`** (`runs/name_once_e998385_20261013T201654.{json,log}`):
  every step PASSES.
  - clean: all three read `e998385`. fnv1a32 of the version and of the name
    are both `0x1AA2F834`.
  - readme-edit and readme-revert: the images read `e998385-dirty`, then
    `e998385`. **0 of 1203 objects** under `main/` or `components/`
    recompiled. Two others recompiled each time: ESP-IDF's `esp_app_desc.c`,
    and `project_elf_src_esp32c3.c` under the ELF target's new name.
    - Observed, not a spec matter: `project_elf_src` also recompiled on the
      revert, a name already built once in this build directory. The
      implementor's note said "the first time a given name is built".
  - overlong: rc 2, no image. **The version-field guard fired**:
    `git_tree_name.cmake:153`, "'gi-name-once-overlong-0123456789abcdef' is
    38 characters; esp_app_desc.version holds 31 plus a NUL".
  - module: the fixture failed the same way, at its own include, and wrote
    no image.
- **Failing half, `d74777d`** (`runs/name_once_d74777d_20261013T201202.{json,log}`):
  FAIL on three steps.
  - readme-edit and readme-revert: **22 product objects recompiled each
    time**, all 1203 objects in all. The images were named right
    (`d74777d-dirty`, then `d74777d`).
  - module: **the fixture built**, and its descriptor version was **cut to
    31 characters without a word**: `gi-name-once-overlong-012345678`.
  - overlong passes here too, as the reviewer predicted. The product's
    project-name guard fired first (`CMakeLists.txt:164`, project name "55
    characters").
  - clean passes: `d74777d`, fnv1a32 `0x7E8B4C8D`.
- **The name on compile lines** (12.3: "The name is not passed on every
  compile line"; recorded, not a row criterion; `runs/name_on_compile_lines_d74777d_e998385_20261013.txt`):
  - `d74777d`: 1203 of 1203 compile lines carry `-DGIT_SHA=\"d74777d\"`;
  - `e998385`: 1, `esp_app_desc.c`'s `-DPROJECT_VER`.
  - The runs' own JSON reads 1203 for both. That count matched any line with
    the sha anywhere, and the clone's directory carries it, so it is void.
    Fixed in `0dbcd3f`; the figures above come from that fixed match.
- **The reported hash, on the bench** (`runs/diag_build_page_nameonce_d74777d_20261013T201243.{json,txt}`,
  `runs/diag_build_page_nameonce_e998385_20261013T201344.{json,txt}`). Flashed
  by OTA from clean worktree builds: `d74777d` md5 `a1222cc2...`, `e998385`
  md5 `34e1b53f...` (`runs/bench_flash_nameonce_*_20261013T201[23].txt`).
  `diag_build_page_check.py` decoded three `0x7F3` pages, the same on the
  PCAN and the Kvaser, on each:
  - `d74777d`: `diag_git_hash` `0x7E8B4C8D` = fnv1a32("d74777d"), its name.
  - `e998385`: `0x1AA2F834` = fnv1a32("e998385"). That is the descriptor
    version (bench_flash read `e998385` embedded) and the image name's
    describe part, and it equals the host cross-check above. HTTP
    `/check_status` `git_version` also reads `e998385`.
  - On both: `diag_fw_ver16` 7, `diag_schema_ver_build` 7, shipping build
    (0x7F1 bit3 0).
  - Restored to `dace947` `_man` and confirmed unarmed on the wire
    (`runs/restore_unarmed_check_20261013T201453.json`, PASS). Bench lock
    20:11-20:15.
- **The shim's 0x7F3, before and after** (reviewer's request;
  `runs/shim_7f3_probe_d74777d_e998385_20261013.txt`):
  - The shim suite checks which diag IDs are sent, never the payload. So
    `shim_7f3_probe.sh` adds one print to a copy of `fake_twai.c` and
    lists the distinct `0x7F3` payloads.
  - Both trees: 328 frames, one payload, `b99f759b 07000700`, which is
    `0x9B759FB9` = fnv1a32("unknown"), the mock's string. Unchanged.
  - A copy whose mock says "e998385" prints `34f8a21a...` instead, so the
    probe can show a difference.
- **The host suite on `e998385`** (`runs/host_suite_e998385_20261013.txt`,
  `<madhouse-debian home>/gi_host_tester_e998385`; replays and the shim fixture, sha256
  `43459c48...`, copied from the `3ee47b7` run):
  - 78 goldens pass, 22 scenarios without goldens;
  - `passive_diff` 96 identical, 0 differing;
  - `spec_expect_check` 21 of 21;
  - the shim and scheduler suites: every case passes;
  - every mode exits 0.
- **Tests of the check:** `test_name_once_check.py`. 21 mutants, all caught
  (`runs/test_name_once_check_mutants_0dbcd3f_20261013.txt`).
- **Voided:** `runs/name_once_d74777d_20261013T195636_VOID_crash.*`. A
  harness defect: recording the overlong step passed `name=` to a helper
  that takes the step's name, and it crashed after the README steps (which
  failed the same way as above). Fixed in `a4b906a`.


**A stand-down with an answer pending -- tester, 2026-10-16. Result: NOT AS
SPECIFIED on `c65c45e` (the before-fix record); AS SPECIFIED on `c6fdab5`
(pre-port batch 1) on the bench, and on `887c000` in the host sched and shim
suites.** Spec 5.2 items 5 and 6, and the 12.4 row "a stand-down with an answer
pending". X2 on `756d6714`, untouched, in both bench runs.
- **Rig:** one `interlock_bench.py` `liveflag-mmode-hold` run (PCAN replay,
  Kvaser witness) with `full_page_recorder.py` reading the whole page at 2 Hz
  beside it. The M-mode latch (`0x639` position 4) lands 260 us after a VCM
  `0x051`, with no device answer between them, so an answer is pending.
- **Judge:** `standdown_pending_judge.py` (reviewer's check):
  - `sched.standdown` +1 across the latch;
  - the skips unchanged (`sched.skipped`, `skipped_withdrawn`,
    `skipped_late`, `skips_withdrawn`, `skips_late`);
  - every diag id on cadence (none silent > 2.5 s while armed);
  - `withdrawn + dropped == skipped + standdown` on every page outside 1 s
    after the latch.
  Tested by `test_standdown_pending_judge.py` (15 checks; 8 of 8 mutants
  killed by a check).
- **`c65c45e` `_man`** (`runs/baseline_c65c45e/bench/standdown_pending_c65c45e_050039.txt`):
  NOT AS SPECIFIED. The page has no `sched.standdown`. The diag ids go silent
  for 5.7-6.7 s after the latch. The identity cannot be formed.
- **`c6fdab5` `_man`** (`72c83f53`;
  `runs/c6fdab5_checks/standdown_pending_c6fdab5_062201.txt`): AS SPECIFIED.
  - `standdown` went 0 -> 1, with all five skip counters unchanged.
  - The largest diag gap was 1.57 s.
  - The identity held on all 105 pages outside the window, and on the 2 inside
    it.
  - `interlock_bench`'s own judge also calls the case AS SPECIFIED (on
    `c65c45e` it was INCONCLUSIVE, with no pages after the latch).
- **Host, `887c000`** (`runs/baseline_887c000/host/host_run.log`, git archive,
  madhouse-debian):
  - `test/gen_inhibit_sched`: all 35 cases passed, and again under ASan/UBSan.
  - `test/gen_inhibit_shim`: all 54 cases passed, and again under ASan/UBSan.
  - The shim's 300 s fixture is 23,241,533 bytes, sha256 `43459c48...`, the
    same as the `c65c45e` baseline's.

**The worker makes every mode change -- tester, 2026-10-16 06:55 and 06:58,
on `887c000` `_man` (`a7101836`, pre-port batch 2). Result: AS SPECIFIED, 2 of
2 runs.** Spec 3 "The worker makes every mode change", 5.2 item 6, and its
12.4 row. Readings for "each reply correct" and "every arm's balance" are the
reviewer's (2026-10-16). X2 on `756d6714`, untouched.
- **Rig:**
  - `e4_device_calibration.py` on `scottsvalley_armable_300s.log` 0-40 s,
    PCAN replay, Kvaser witness.
  - Armed with `--arm-after 1`, then 11 POSTs 3 s apart, chained, over the raw
    socket: 3->3 x5 and 3->4->3 x3 (sequence 3,3,3,3,4,3,3,4,3,3,4,3).
  - `full_page_recorder.py` at 4 Hz beside it.
- **Judge:** `mode_change_judge.py`, tested by `test_mode_change_judge.py`
  (23 checks; 14 of 14 mutants killed by a check).
  - Per POST: HTTP 200 with neither 500 text, a reply within 1 s (a later 200
    is reported as its own category until the user rules), and the mode
    applied on the next page. *Since the user's ruling on question (a)
    (2026-10-16) a 200 after 1 s passes and is listed with its timing; the
    judge was changed for it (24 checks; 15 of 15 mutants killed), and both
    runs re-judged the same (`..._rejudge_late200.txt`). Neither had a late
    reply.* A new arm: `tx_ok` out of INHIBIT, or `would_tx`
    out of PASSIVE, restarts.
  - At each old arm's end: every class balances (`queued + carried_in == sent
    + dropped + withdrawn + depth + held`) and so does the inhibit identity.
  - Over the run: the identity on every page (one short only inside a change's
    1 s window while `held` is 1; one over never), and the diag cadence across
    the whole armed time.
- **Result, both runs** (`runs/887c000_checks/mode_change_judge_887c000_065452.txt`,
  `..._065748.txt`): 24 of 24 preconditions met (live before every POST).
  - Every reply 200 and applied.
  - Every arm restarted: `tx_ok` 276-291 -> 0-16, `would_tx` 287-291 -> 0.
  - Every class and the identity balance at every arm's end.
  - The identity held on all 320 and 321 pages. No page read one short or
    one over.
  - Diag gaps at most 1.26 s for every id.
- **Reply latency** (reply complete minus sent): 10.4-26.0 ms (median 13.4)
  and 7.1-27.6 ms (median 12.2); none after 1 s.
  - The `--arm-after` POST is untimed in both runs: e4 then kept no raw record
    for it. The judge checks it for a 200 and applied only. e4 now keeps it
    (`arm_post_fields`).
- **e4's own "ARM VOID"** in both runs is its single-arm `tx_ok` comparison
  meeting a deliberately re-arming run, a harness limitation (accepted,
  reviewer). Its live provenance is as specified: 94,324 of 94,324 replayed
  frames found, 0 late, 0 double.
- **Run 2 overlap:** the tester's judge mutant run (CPU only) overlapped run 2
  unthrottled. Run 2's stimulus matched run 1's: 0 refused, the longest idle
  gap 30.4 ms against 44.5 ms, 0 error frames.
- **Host half, `887c000`:** shim cases 52-54 (mode changes posted by another
  task during a live 300 s replay, every arm balancing; the 1 s refusal) and
  the sched re-arm cases are in the passes above.

**Refusals reach the client with no RST -- tester, 2026-10-16 06:24-06:34, on
`c6fdab5` `_man` (`72c83f53`, pre-port batch 1). Result: AS SPECIFIED, 40 of
40.** Spec 5.1 item 3 and 9.3; the reviewer's check for the refusal-reply
change. B21 on the wire. HTTP only; no CAN adapter was used.
- **2026-11-19:** judged under 9.3's wording before `f7865461` (the device's end of each connection
  on the wire); not a pass under the new wording (the 12.4 entry "B11raw and B21 under the wording
  of 2026-11-19").
- **Rig:** `ota_guard_raw_probe.py`, 5 reps in each non-OFF mode, with
  unelevated tshark on the laptop's Wi-Fi. `ota_guard_pcap_join.py
  --expect=no-rst` joins each request to its connection by client port.
  Tested by `test_ota_guard_probe.py` (30 checks; 14 of 14 mutants killed by a
  check).
- **184-byte body, 20 of 20**
  (`runs/c6fdab5_checks/B21_wire_multipart_c6fdab5.txt`): the 403 on the
  wire, read by the client 8-82 ms after the request, and the device closed
  with FIN, never RST.
- **Full image, 20 of 20** (1,278,323 bytes, the running image;
  `B21_wire_image_c6fdab5.txt`): the same. The 403 arrived in 14-37 ms, and
  the device had read the whole image off by 2.74-2.84 s.
- Uptime continuous over both runs, and mode 0 after.
- **Observed; not a finding against the spec** (reviewer, 2026-10-16: no spec
  text covers connection lifetime, and the control below shows a successful
  request's connection held the same way): after the 403 the device does not
  close the connection. It holds it until the client closes, then sends FIN; the client
  closed first in all 40. The control, 5 x `POST /gen_inhibit_set?mode=0` in
  mode 0 (`connection_close_control.py`, `connclose_*`), behaved the same after
  a 200. On `c65c45e` the device ended a refused connection with RST straight
  after the 403 (B21 addendum).

**The guard benches read /gen_inhibit, never /check_status -- tester,
2026-10-21. A harness change; no device run.** Spec 5.1 item 3 and 9.3 (B21's
`bench_flash_guard.py` and `bench_ota_guard.py`, firmware repo
`tools/gen_inhibit/`). `/check_status` answers with the device's config,
credentials included (reviewer, 2026-10-20).
- **What changed** (tester clone, branch `tester/range-20261020b`, `1e4d7e8`,
  local):
  - both guards read the record's `device` version from `/check_status`. It is
    now `device_identity()`: `elf_sha256`, `git_version` and `uptime_ms` from
    `/gen_inhibit`;
  - the flash guard's liveness probe after a closed connection in its OFF half
    asked `/check_status`. It now asks `/gen_inhibit`.
- **Test:** `test_bench_guards_page.py`, 13 checks, PASS
  (`runs/test_bench_guards_page_1e4d7e8_20261021.txt`). It runs both guards'
  `main()` on a fake device whose `/check_status` answers with a made-up
  value. It checks that `/check_status` is never asked for and that the
  record's `device` is the page's identity. The flash guard's OFF rows must
  fail when `/gen_inhibit` stops answering after the probe.
- **Mutants:** 8 of 8 killed by a check, and the control on both guards at
  `95aa77a` fails by a check (`runs/test_bench_guards_page_mutants_1e4d7e8_20261021.txt`).
- **Also:** `test_bench_flash_preflight.py`, `test_bench_flash_outcome.py`,
  `test_suite_judges.py` (with `GI_BASELINE_DIR` set) and
  `test_publish_scan.py` still pass.
- **The "device is in AP mode" precondition, `d526c2a`** (user's ruling,
  2026-10-21, through the reviewer: `wifi_mode` may be read from `/load_config`
  as a fourth field, beside `protocol`, `port_type` and `port`).
  - Until then the OTA guard read it from `/load_config`, which the rule did not
    allow. The flash guard read it from `/gen_inhibit`, which spec 11 does not
    put there, and passed when the field was absent.
  - Both now read it with `bench_ota_guard.wifi_mode()`, which keeps that one
    value, and print and record only it. Anything but `AP` refuses the run
    before anything is sent, a missing or unreadable value included.
  - **Test:** `test_bench_guards_page.py` at `d526c2a`, 47 checks, PASS
    (`runs/test_bench_guards_page_d526c2a_20261021.txt`). The fake config
    carries the made-up value in credential-shaped fields.
    - With `AP`, both guards print and record only `wifi_mode='AP'`.
    - Seven cases refuse both guards, with exit 2, a FAIL line naming the
      cause, nothing sent and nothing of the config printed: `APStation`; the
      field absent; null; a config that is not JSON; not an object; a 500; and
      a page saying `AP` over a config saying `APStation`.
  - **Mutants:** 19 of 19 killed by a check, and the control on both guards at
    `95aa77a` fails by a check
    (`runs/test_bench_guards_page_mutants_d526c2a_20261021.txt`). The AP-mode
    ones:
    - an absent `wifi_mode` read as `AP` (the reviewer asked for this one);
    - a value not read passing;
    - non-JSON read as `AP`;
    - the config printed, recorded, or carried in an error;
    - either guard's precondition not enforced;
    - the flash guard back on the page, by either reading;
    - the flash guard not recording the value.
  - `test_bench_flash_preflight.py`, `test_bench_flash_outcome.py` and
    `test_publish_scan.py` still pass.
  - Neither guard has been run on the device since. B21 can run once this lands
    (reviewer). Run on the device 2026-10-22 (B21 step 1, next entry).

**B21 step 1, the guard benches on the device -- tester, 2026-10-22
00:14-00:15, on `e3d9437` `_man` (`25f50ac0...`), harness `d526c2a`. Result:
AS SPECIFIED.** Spec 5.1 item 3 and 9.3. The first device run of the guards
since they moved off `/check_status` and read `wifi_mode` from `/load_config`
(entry above). HTTP only. No CAN adapter or COM port was opened, there was no
Wi-Fi capture, and the X2 was not touched.
- **Rig:** `baseline_b21.sh` with `GI_B21_STEPS=guards`: `bench_flash_guard.py
  --check-accepted` and `bench_ota_guard.py --allow-ota-write`, firmware repo
  `tools/gen_inhibit/` at `d526c2a` (tester clone, clean tree; the two
  files' sha256 are in `run_log.txt`). The WiCAN was rebooted by software
  before and after (`wican_reboot.py --want wican_man`, PASS both).
- **Preconditions, both guards:** `wifi_mode='AP'`, read from `/load_config`
  and the only config value printed or recorded. The inhibitor build was
  present, in mode OFF.
- **Flash writers, 20 of 20 refused:** the five writers in each of OBSERVE,
  RESPOND, INHIBIT and PASSIVE, each with a 403 naming `gen_inhibit`.
- **Reached when OFF, 3 of 3:** `/store_config` and `/store_canflt` closed
  the connection with the device still answering `/gen_inhibit`, and
  `/store_auto_data` answered 400. `/store_car_data` and
  `/upload/car_data.json` were skipped by design (no probe that writes
  nothing).
- **OTA, 4 of 4 refused:** a 403 in each non-OFF mode. In OFF the
  deliberately invalid image got a 500, not a 403, so the guard is not what
  stops it. Nothing was flashed: the release reboot found the same image.
- **Records:** both name the image by `elf_sha256` `25f50ac0...` from
  `/gen_inhibit`. No `/check_status` was asked for.
- **Run files:** `runs/b21_guards_d526c2a_20261022/` (`B21_bench_flash_guard.txt`,
  `B21_bench_ota_guard.txt`, the two JSON records, `run_log.txt`, and
  `wican_reboot_start.*` and `wican_reboot_release.*`).
- **Steps 2 and 3:** run the same night, next entry. The user ruled on
  2026-10-22, through the reviewer, that OTA Wi-Fi captures may run.

**B21 steps 2 and 3, refusals on the wire -- tester, 2026-10-22 01:46-01:50,
on `e3d9437` `_man` (`25f50ac0...`). Result: AS SPECIFIED, 40 of 40.** Spec
5.1 item 3 and 9.3. The user's ruling (2026-10-22, through the reviewer) lifts
the hold on Wi-Fi captures during OTAs. HTTP only. No CAN adapter or COM port
was opened, and the X2 was not touched.
- **2026-11-19:** judged under 9.3's wording before `f7865461` (from the wire); not a pass under the
  new wording (the 12.4 entry "B11raw and B21 under the wording of 2026-11-19").
- **Rig:** `baseline_b21.sh` with `GI_B21_STEPS=probe`:
  - `ota_guard_raw_probe.py`, 5 reps in each non-OFF mode;
  - an unelevated dumpcap of the laptop's Wi-Fi (`host 192.168.80.1 and tcp
    port 80`), stopped by `capture_window.py` 10 s after the probe;
  - `ota_guard_pcap_join.py --expect=no-rst`.
  - The scripts' sha256, the image's and dumpcap's version are in
    `run_log.txt`. The WiCAN was rebooted by software before and after
    (`wican_reboot.py --want wican_man`, PASS both).
- **184-byte body, 20 of 20** (`B21_wire_multipart_e3d9437.txt`): a 403 on
  the wire for every request, and the device closed with FIN, never RST. The
  client closed first in all 20. The client read the 403 146-189 ms after
  connecting (on `c6fdab5`, 8-82 ms).
- **Full image, 20 of 20** (1,113,456 bytes, the running image; body 1,113,619
  bytes; `B21_wire_image_e3d9437.txt`): the same. The client read the 403
  156-191 ms after connecting, while it was still sending. The device read the
  whole body off by 2.52-2.62 s.
- Every request was in its capture. Both captures stopped on their stop file,
  cleanly, rc 0. The mode read before each rep matched the mode asked for.
  There were no send errors.
- **Device:** uptime continuous within each run (12.1 -> 68.1 s and 83.5 ->
  187.5 s), and mode 0 after. The release reboot found the same image, so no
  upload took.
- **Run files:** `runs/b21_probe_e3d9437_20261022/`: the two pcapng captures
  (185,556 and 24,813,772 bytes), the probe and join outputs and JSON records,
  the capture records and windows, `run_log.txt`, and
  `wican_reboot_start.*` and `wican_reboot_release.*`.

**The worker state machine -- tester, 2026-10-16, on `e3d9437` (pre-port
batch 3). Result: AS SPECIFIED, host and bench.** Spec 8.1 (rows 4, 8, 9, 11,
12, 13), 9.3, and the 12.4 row "worker state machine". X2 on `756d6714`,
untouched, in every bench run.
- **Host** (`runs/baseline_e3d9437/host/host_run.log`, git archive,
  madhouse-debian):
  - `test/gen_inhibit_shim`: all 58 cases pass, plain and under ASan/UBSan. Among them:
    - case 6: a self-imposed OFF says why;
    - case 55: an outside quiesce applied by the worker;
    - case 56: an OTA takes the bus through the worker (rows 4, 8, 11, 12, 13);
    - case 57: the mode rows from each state (rows 1, 2, 3, 5, 6, 13);
    - case 58: leaving ARMED withdraws the answer in the controller (rows 5, 6, 9).
  - `shim_test_autoarm` passes, plain and under ASan/UBSan.
  - The golden suite: 80 passed, 0 failed, no golden moved.
- **Bench image:** `e3d9437` `_man` (`elf_sha256` `25f50ac0...`, md5
  `95977df0...`), the tester's clean build, OTA'd 21:01
  (`runs/e3d9437_checks/ota_e3d9437_man_retry_20261016T210039.*`, TOOK). The
  page carries `worker` (`idle` in mode 0) and `bus_violations` (0).
- **OTA while armed** (21:08). The rig, `ota_armed_bench.py`:
  - `e4_device_calibration.py` on `scottsvalley_armable_300s.log` 0-40 s, PCAN replay, Kvaser witness;
  - INHIBIT armed after 1 s, with `full_page_recorder.py` at 4 Hz beside it;
  - once live, the whole running image uploaded over a raw socket.

  Judge `batch3_bench_judge.py armed` (`runs/e3d9437_checks/judge_ota_armed_20261016.txt`,
  `runs/ota_armed_20261016T210807.json`): **AS SPECIFIED**.
  - The 403 with "gen_inhibit is armed; POST /gen_inhibit_set?mode=0 first", 42 ms in.
  - Every page across the upload reads worker `armed`, mode 3.
  - The same arm went on (`tx_ok` 340 -> 994).
  - `elf_sha256` unchanged, uptime continuous, `bus_violations` 0 -> 0.
  - The device answered the VCM throughout, from the upload's start to 1 s
    after its end (largest gap 36 ms). No diag id was silent more than 1.27 s.
- **OTA while OFF:** `_man2` (`f9de9de9...`) TOOK, 21:09
  (`ota_off_man2_20261016T210930.*`); `_man` back TOOK, 21:11
  (`ota_back_man_20261016T211129.*`).
- **Failed uploads, row 11, both kinds.** The rig: `failed_upload_probe.py`,
  with a second connection reading the page at 4 Hz from before the upload to
  35 s after; then a 40 s INHIBIT arm under replay. Judged by
  `batch3_bench_judge.py failed` under the reviewer's two rulings (2026-10-16):
  - row 11 asks for IDLE with nothing taken, so verdict a1 or b passes either kind, and the reply is recorded as observed;
  - a read requested before the upload connected is not "inside" it.

  Both kinds are **AS SPECIFIED**
  (`runs/e3d9437_checks/judge_failed_{short,truncated}_20261016_rejudged.txt`):
  - **Short body, then a half-close** (`runs/failed_upload_short_20261016T210308.json`):
    the headers declare 1,113,619 bytes; 556,861 were sent, the connection held
    open 3 s, then FIN. Observed: "500 Failed to receive file" 16 ms after the
    FIN, verdict b.
  - **Truncated image** (`runs/failed_upload_truncated_20261016T210608.json`):
    a complete request carrying 556,728 of 1,113,456 bytes. Observed: "500
    esp_ota_end failed", verdict b. Not taken.
  - After each kind:
    - every page reads worker `idle`, mode 0;
    - the image is unchanged and the uptime continuous;
    - `bus_violations` is 0;
    - the arm after it answered the VCM 3,884 times, provenance clean
      (`runs/e3d9437_checks/failed_{short,truncated}_arm_e4_*.json`).
  - **UPDATING: not observable on the bench (reads queued behind the upload).**
    - Short: a read sent 0.25 s in was answered at 11.75 s, after the 11.74 s upload.
    - Truncated: one sent 0.24 s in was answered at 11.21 s, after the 9.17 s upload.
    - In each kind, one read already in flight at the connect (sent 13-14 ms
      before it) was answered `idle`, 0.25 s and 0.015 s in. It is listed, not
      judged.
- **Tests of the harness:**
  - `test_batch3_bench_judge.py`: 21 of 21 mutants killed by a check, after the rulings.
  - `test_failed_upload_probe.py`: 14 of 14, after the rulings
    (`runs/e3d9437_checks/batch3_rulings_mutants_20261016.txt`).
  - The probe's order check was flaky: it stamped the half-close after
    `shutdown()` returned. Fixed: 0 failures in 25 runs, and every kill is by
    its own check (`runs/baseline_e3d9437/batch3_harness_mutants_20261016.txt`).

**Stock features off in this product -- tester, 2026-10-16, on `e3d9437`
(pre-port batch 3). Result: AS SPECIFIED.** Spec 3.2 and 12.3 item 3. The
build checks are the firmware repo's `test/build_checks/` at `0ee2e49` (written
by the tester, published by the reviewer).
- **Images** (`runs/baseline_e3d9437/images_e3d9437.json`, `builds/`):
  - the tester's clean-worktree builds of `e3d9437`: `_man`, `_man2` and the five test-only builds;
  - the stock-feature builds: MQTT, ELM327, MQTT+ELM327, MQTT+ELM327+AutoPID
    (AutoPID needs both), RealDash, and all four;
  - a prod build (`tools/prod_build.py`, fresh clone of `gen-inhibit/main` at
    `e3d9437`): md5 `76250712...`, `elf_sha256` `f3957789...`, 1,113,456 bytes
    (`builds_prod.txt`).
- **The switches** (`build_switches.py` on `_man`'s and prod's own build
  records, `runs/baseline_e3d9437/tester_rows_e3d9437.txt`):
  - 40 symbols: 0 unlisted, 0 stale, 0 malformed.
  - The four `WICAN_FEATURE_*` are the class "stock feature, off in this product", each naming its refusal row.
  - With the checks as committed at `e3d9437`, the four were UNLISTED. That is
    the expected result before the tester's table change
    (`build_checks_light_e3d9437_{man,prod}_committed.txt`). Every other row
    passes on both images, and `_man` has the expected NO REF for a prod record.
- **The rows, on `_man` and prod:**
  - for MQTT, ELM327 and AutoPID: an image row (three strings found only in the
    switch-on builds) and an ELF row (their symbols);
  - RealDash: `app_main`'s `can_enable` call sites;
  - a bus-state row, from objdump call sites.

  All pass on both. `can_enable` is called from `apply_mode` only. `can_disable`
  is called from `apply_mode`, `gen_inhibit_task` and `upload_post_handler`.
  `can_flush_rx` and `can_send` are absent.
- **`app_main` -> `can_enable` call sites**
  (`runs/baseline_e3d9437/stock_feature_callsites_20261016.txt`):
  - 0 with every feature off;
  - 1 with MQTT, ELM327 or RealDash;
  - 2 with MQTT+ELM327;
  - 3 with AutoPID;
  - 4 with all on.
- **Each row shown failing and passing** (`test_refusal_pairs.py` via
  `refusal_pairs_runs.py`, `runs/baseline_e3d9437/test_refusal_pairs_e3d9437.txt`):
  - PASS on all 14 images.
  - AutoPID's control is MQTT+ELM327: its rows pass there, and MQTT's and ELM327's fail.
  - 11 negative controls, each caught on the check it targets:
    - a wrong ON image;
    - the product image as ON (AutoPID, RealDash);
    - a stock, an instrument and the fault-injection image as OFF;
    - AutoPID's own image, or the product image, as its control;
    - no control;
    - a switch left out;
    - an image without its ELF.
- **Tests of the checks:**
  - `test_switch_rows.py`, `test_build_switches.py` and the other light `test_*`: all exit 0 (`build_checks_tests_tester_clone.txt`).
  - `build_checks_stock_mutants.py --pairs`: 16 of 16 mutants killed by a check,
    one of them by the real-image pairs (`build_checks_stock_mutants_pairs_20261016.txt`).
- **The prod build's components: NOT AS SPECIFIED on `e3d9437`'s
  `prod_build.py`** (reviewer's ruling, against 12.3 item 3; fixed in
  `2df3c17`, tools only).
  - **The defect:** on a fresh clone, the component check ran before the
    script's own `idf.py reconfigure` fetched `espressif__mdns`. It printed
    "managed components: none on disk", so no row covered the component that
    went into the image.
  - **This prod image stands:** `prod_build.py --check-only` on the same clone
    afterwards verified the component: 96 files against `CHECKSUMS.json`, and
    the hash matches the lock (`prod_check_only_after_build.txt`).
  - **On `2df3c17`:**
    - the implementor's `tools/test_prod_build_fetch.py` passes all 3 cases;
      run against `0ee2e49`'s script, it fails tampered and missing
      (`runs/e3d9437_checks/test_prod_build_fetch_*.txt`);
    - the tester's `test_prod_build_refusals.py`: PASS, and its self-test PASS
      (`test_prod_build_refusals*_2df3c17.txt`).
  - **Still to run:** a real prod build on a fresh clone of `2df3c17`.

**Observation, open: an OTA upload reset at exactly 128 KiB -- tester,
2026-10-16.** The reviewer opened this as an observation, not a finding. Nothing
was changed and nothing was retried in a loop. Every OTA from now on carries an
unelevated Wi-Fi capture (`ota_pcap.sh`).
- **20:58, `887c000` `_man` -> `e3d9437` `_man`**
  (`runs/e3d9437_checks/ota_e3d9437_man_20261016T205817.*`), verdict a1:
  - curl sent 131,072 of 1,113,456 bytes and got a connection reset 17.64 s
    after the connect. That is longer than any complete upload on record
    (13.0-16.2 s).
  - Page reads 11-96 s after it got no answer.
  - At 98 s the device answered, still on `887c000`, with its uptime continuous (up about 14 h).
  - The retry 2 min later took.
- **03:09, `c65c45e` `_man2` -> prod**
  (`runs/baseline_c65c45e/bench/bench_flash_c65c45e_prod_20261016T0309.txt`):
  - curl reported the same reset; that record has no byte count.
  - The device had been up under about 51 s.
  - It did not answer for over a minute, and it did not restart.
- **2026-10-17 00:21, `e3d9437` prod -> `e3d9437` `_man`, with a capture**
  (`runs/baseline_e3d9437/bench/otagroup_man_back_20261017T002129.*`; the
  wire summary `..._wire.txt` is from `ota_pcap_stall.sh`), verdict a1. The
  device had been up about 300 s.
  - curl again reported 131,072 of 1,113,456 bytes sent and "Connection was
    reset", 16.49 s after it started.
  - On the wire the device's highest ACK was 20,391: the 230-byte request
    header and 20,160 body bytes. The laptop retransmitted three segments at
    2.79-5.18 s. At 6.10 s the device ACKed 20,391 with a 90-byte window.
    After that the capture holds nothing on the upload connection until
    112.69 s: no further data, no zero-window probe and no RST from either
    side, including at the time curl reported the reset.
  - At 112.69 s the device sent `HTTP/1.1 408 Request Timeout` and a FIN on
    the upload connection. It retransmitted them until 158.7 s, with no reply.
  - bench_flash's page reads from 30 s on opened connections: the device
    ACKed each GET and sent no reply. From 90.7 s two connections' SYNs got
    no SYN+ACK. At 112.69 s the device answered the waiting GETs.
  - The device stayed on prod (`f3957789`), mode 0, worker idle, with its
    uptime continuous (599 s at 00:31). It was not retried.
  - **The nine OTAs that took** in the same captures (`otagroup_*`) all paused
    at the same point. The device ACKed 20,391 with a 90-byte window
    1.01-1.71 s after the upload's SYN. Its window update came 7.43-7.48 s
    after the SYN in all nine, and the upload went on from there. That pause
    is the upload's longest gap between device ACKs (4.9-6.5 s).
    - In eight of the nine the laptop sent a 90-byte segment in the pause; the
      rxq capture has none.
    - In five a zero-window ACK came before the update.
    - In the failed upload the 90-byte window came at 5.94 s after the SYN,
      after the three retransmissions. The window was not reopened until the
      408 at 112.5 s.
- Every other OTA with a byte count sent the whole image: 24 records, on
  `c65c45e`, `c6fdab5`, `887c000` and `e3d9437`. The 24th is the reviewer's
  one restore to `_man` at 01:01, with a capture
  (`runs/baseline_e3d9437/bench/ota_man_restore_20261017T010044.*`).

**The chained bench run on `e3d9437`, the runner's acceptance -- tester,
2026-10-19 20:24 to 2026-10-20 01:17**

- **What:** the gen-inhibit bench suite (part B of `baseline_run_list.md`, 38
  compared checks), run by the chain runner (`bench_compare.py chain`, core
  `df5d2ec`, tester clone `tools/gen_inhibit/` at `c5ab1e0`) and compared check
  by check with the accepted `e3d9437` baseline. Code identity: commit
  `e3d9437`, clean trees. Emulated prerequisite:
  `runs/chain/gen-inhibit-emulated_20261019T195847_2838` (PASS 5, NOT-RUN 2,
  nothing flagged).
- **Boards:** the WiCAN on `e3d9437` `_man` (`25f50ac0...`) and each group's
  OTA image; the X2 per group on `756d6714` not held, held in ROM, the jammer
  `a89ece8c` (custody) or the interposer's `slcan`. Each was read off the board
  by the runner's identify before its group (each run's `progress.jsonl`).
- **Runs:**
  - `runs/chain/gen-inhibit-bench_20261019T202410_0bfe` (every group) and its
    re-run of the flagged checks, `_20261019T224502_ec06`;
  - `_20261020T001351_0068` (man_jammer and man_slcan) and its re-run,
    `_20261020T004332_59b0`.
  - The compare reports are the raw record: `0bfe/compare_index.txt` (flagged
    14 of 38) and `0068/compare_index.txt` (3 of 38).
- **Result: the runner is accepted** (reviewer, 2026-10-20).
  - Every verdict matched the baseline except B6 and B20.
    - **Correction (tester, 2026-11-18): B15's `gs_usb` row, here and in the
      baseline, was the CAN2 Innomaker** (today's `canchg`), reached through
      the X2's bridge, not an adapter on CAN1, so it does not count as
      independent. B15's pass rests on its Kvaser and PCAN rows. The verdict
      stands (reviewer ruling, 2026-11-18).
  - B5, B7, B8 and B22 are taken from 0068; in 0bfe they were NOT-RUN after
    the harness bug below.
  - B27 is the declared skip (needs the user at the bench).
  - Every OTA took at the first attempt.
- **B20: flaky, needs review.**
  - FAIL first (27 of 28 AS SPECIFIED), PASS 28 of 28 on the re-run.
  - The arm was `liveflag-mmode`, INCONCLUSIVE: its precondition "0x7F1 pages
    between the latch and any mode change" was not met (0 pages).
  - Reviewer, 2026-10-20: the case's timing, not the device. The judged window,
    from latch + 1.1 s to the mode-0 POST, is about 0.92 s wide, and 0x7F1 comes
    every 1.23-1.25 s. The page came 20 ms before the window opened in the
    first run and just inside it in the re-run. The case is to be changed so
    page phase cannot make it INCONCLUSIVE.
- **B6: flaky, needs review.**
  - PASS first (10 of 10 arms), 8 of 10 on its re-run. The re-run was triggered
    by B5's figures (B5 to B8 are one check).
  - Observed vs spec 7, trip 6 (no latch before the 10th error frame in the
    window): n10 lead 8.5 arm 0 latched 1.108 s after the first pulse, with 2
    error frames delivered. The witness saw one error frame per pulse; the
    device's `bus_error` rose by 11 over pulse 2, with TEC 0.
  - n9 lead 5 arm 0: INCONCLUSIVE (8 of 9 pulses delivered).
  - 20 more arms on 2026-10-20 counted every pulse once; every multi-count
    pulse on record is under 7, "One pulse counted 10-12 times".
- **Harness bug found in the run and fixed.**
  - What happened: in 0bfe the X2's identify after the jammer flash read
    nothing, and the runner stopped the bench and left the X2 on the jammer.
  - Cause: the interposer identify's slcan probe sends `V\r` and `N\r`. The
    jammer runs a line only on LF and drops CR, so "VN" joined the `ID` line.
  - Fix: `x2_jammer.py id` now sends an empty line first.
    `test_x2_jammer.py` 46/46, `test_x2_jammer_mutants.py` 2 of 2 killed.
    Shown on hardware in 0068 (`jammer:banner`).
- **Other flagged figures** had unchanged verdicts. Reviewer, 2026-10-20: they
  are variance. B9 `tec_max` read 94 and 93 against the baseline's 34; a run on
  `c65c45e` read 93 (corrected 2026-10-20: this said "the run before the
  baseline").
- **Note (tester, 2026-10-26; wording approved, user, 2026-10-25): B9's trip 6
  was never latched by the bursts on a live arm.**
  - **What every wrong-bit-rate record on file shows:** `live_before` is false,
    no page sample reads live, and the trip had already latched at the first
    page sample, about 4 s before the first burst. The records are
    `wrong_bitrate_calibrate_*.json`: 2026-10-10 on `cc34c80`, the `e3d9437`
    baseline, `0bfe` and `ec06`, and `cfcd` and `6204` on `6926fb0`.
  - **Why it read PASS:** B9's judge passed on "error-frame rate exceeded" being
    among the aborts, so a latch made before the stimulus counted.
  - **What this changes:** these B9 PASS verdicts are not evidence that the
    wrong-bit-rate bursts trip a live arm. Under the judge as changed on
    `gen-inhibit/tester/gi-page-reads` (`6081665`), each reads INCONCLUSIVE.
  - **What still stands:** the counter readings stand as readings of the
    device's counters with the arm latched: REC up to 128-135 after each burst,
    TEC, state `running`, and the witness's error frames per burst.
  - **Where trip 6 on a live arm rests instead:** the jammer cases (B5
    livetrain, B6 threshold, B7 busoff-rearm) and B10's flood arms. Their
    records show the device live with no abort just before the stimulus, no
    witness error frame before it, and the latch after it.

**The subset run on `e3d9437` after the duration cuts -- tester, 2026-10-20
03:15 to 04:06**

- **What:** B21, B11raw (its arms and its desk judge), B12 (the rt group) and
  B5_B8 (the jammer group), chained (`bench_compare.py chain`, core
  `e37995a`, tester clone `tools/gen_inhibit/` at `590c990`), after the
  2026-10-20 harness batch: the B21 and B11raw captures stopped by
  `capture_window.py` a tail after the probe or arm (10 s and 5 s; before, a
  fixed 600 s and 75 s), `ota rt_self` dropped from B12, `GI_RUNS_DIR` quoted
  in `baseline_b_jam.sh` and `x2_release_identify.sh`, and `page_state.py` at
  the end of the rt step. Commit `e3d9437`, clean trees.
- **Boards:** read by the runner's identify at the start: the WiCAN on
  `_man` (`25f50ac0...`), the X2 on `756d6714`. The WiCAN went to `_man_rt`
  (`b5f852ef...`) for B12 and back to `_man`; the X2 to the jammer for B5_B8
  and back to `756d6714` (04:06, read off the wire).
- **Run:** `runs/chain/gen-inhibit-bench_20261020T031518_5880`. The compare is `compare_report.txt` in that folder
  (`bench_compare.py report`; the chain wrapper was stopped before it wrote
  `compare_index.txt`, below).
- **Result: 8 of 8 PASS, every verdict as the baseline.** Reviewer,
  2026-10-20: accepted as evidence that the harness batch changes nothing.
- **Flagged, 3 of 38, figures only. Reviewed, no re-run** (reviewer,
  2026-10-20): each figure is inside what earlier `e3d9437` runs read.
  - B5: `pass_500_per_s` 9 and `inconclusive_500_per_s` 0 (baseline 8 and 1);
    `0068` and its re-run read 9 and 0.
  - B11raw: `send_to_first_byte_ms_max` 33.9 (baseline 20.1, spread
    6.1..20.1); `0bfe` read 19.8 and its re-run `ec06` 30.7. `pass_on_ack` 0
    (baseline 1); `0bfe` and `ec06` read 0.
  - B12: `panic_boot_s` 0.583..0.600 (baseline 0.592..0.599) and `sw_boot_s`
    2.301..2.307 (baseline 2.314..2.353); `0bfe` and `ec06` read
    0.582..0.600 and 2.297..2.330.
- **The stopped captures are complete, on hardware.** `capture_stop_check.py`
  on all 8 (2 B21, 6 B11raw; `capture_stop_check.json`): PASS. tshark read
  each file to the end with no "cut short"; each window's last client FIN on
  port 80 was answered by the device with a FIN; each capture stopped on its
  stop file, cleanly, rc 0, after its window. B21 took 3.5 min (03:22-03:25).
- **The state the rt step leaves** (`rt/B12/B12_rt_end_page.json`, read at the
  end of the step): mode 0, `inhibit_live` false, `disabled` false,
  `arm_block` "not armed", `abort_latched` false, `abort_reason` and
  `disable_reason` empty, `err_n` 0, worker idle; `uptime_ms` 27217,
  `reset_reason` PANIC, `elf_sha256` `b5f852ef...`. The boot it reports
  (epoch 1792492500.085, the read's time less its uptime) is the boot after
  `inert rt present`'s crash trigger: the POST `/gi_quiesce?crash=illegal` at
  1792492499.555 and the boot at 1792492500.137 by `restart_error_frames`
  (`rt/B12/restart_error_frames_live_crash_20261020T033438.json`, PANIC). No
  other reset between them.
- **The automatic re-run of the flagged checks did not finish.** It started
  04:06 (`runs/chain/gen-inhibit-bench_20261020T040615_29a5`) and was stopped
  about 04:10 by Claude Code's low-memory process reaper, in B11raw arm 4, not
  by the runner. It has no result. It left the WiCAN in mode 3, latched "bus
  lost -- no 0x051"; the tester POSTed mode 0 and rebooted it by software
  (04:12): mode 0, nothing latched, `25f50ac0...` (`page_after_kill.json`,
  `page_after_disarm.json`, `page_after_reboot.json` in that folder). No
  process was left running.
- **Harness tests for the batch:** `test_baseline_capture_stop.py` 37 ok (15
  of 15 mutants killed by a check), `test_capture_window.py` 10 ok (6 of 6),
  `test_capture_stop_check.py` 15 ok (10 of 10), `test_page_state.py` 7 ok (7
  of 7); the mutant runs in `runs/*_20261020.txt`.

**A bus with no other node, emulated -- tester, 2026-10-20, rerun on
`madhouse-debian` 2026-10-24, on the mock-HAL shim, product `main/` =
`e3d9437`'s tree. Result: as specified, in all seven cases (64 of 64 verdicts
PASS).** Spec 12.4 row "a bus with no other node,
emulated"; spec 7 trips 2 and 6, 7.1; and, for the answer-stuck case, 5.2
items 5 and 6 (added by the reviewer, 2026-10-20).
- **Build.** `main/gen_inhibit.c`, `gen_inhibit_core.c`, `gi_sched.c` and
  `can.c` as checked out at `b341e0d` (gen-inhibit/main), whose `main/` tree
  (`c7cd9d55`) is `e3d9437`'s, checked by the run, nothing modified (md5s in
  `identity.json`, the fakes' included). Built with gcc 14.2.0 on
  `madhouse-debian` against the implementor's fakes `test/gen_inhibit_shim/`
  (not edited): the 2026-10-24 rerun (user, 2026-10-24, through the reviewer:
  host C results count only from `madhouse-debian`; NICKY-XPS's WSL gcc 9.4 is
  for self-checks). The rerun is the commit's own `run_spec_shim.py` with only
  its `wsl()` and `wsl_path()` replaced by local ones, and its own
  `mutate.py --local`, from a fresh clone of origin. Its verdicts and detail
  lines are the same as the 2026-10-20 run's, row for row, and so are
  `cases.txt`, `payloads.txt`, `judge_test` and every mutant. `identity.json`
  differs only in its start time. The 2026-10-20 run was built with WSL gcc
  9.4 on NICKY-XPS; under that ruling it is a self-check.
- **Rig.** `test/gen_inhibit_spec_shim/` (tester clone, branch
  `tester/spec-shim`, `c73656f`; design approved by the reviewer
  2026-10-20). A fault layer over the fakes by link-time wraps: with no other
  node, every frame never completes (the head reads as transmitting, as a
  frame retrying unacknowledged does); one error frame per retry period while
  a frame waits (`bus_error_count` and a latched bus-error alert); TEC to 128
  (error passive); no frame received; bus-off on request. The layer logs every
  receive return (the device's check points) and counts recovery calls. **The
  retry period and alert pattern, and how the controller's abort removes a
  retrying frame (the answer-stuck case), are the tester's model, not a
  measurement**, until the bench row measures the real pattern.
- **Stimulus.** The interlock set (key on, contactors closed, SoC 50 %, VCM
  fault flag clear, shift position set, a VCM command at its engine-off null
  with zero torque), each payload decoded with `epri-pt-bus.dbc` and
  `vtrux-powertrain-experimental.dbc` by the run (`payloads.txt`; `0x617` B7
  checked as a raw byte, the DBC has no signal for it). Each case goes live,
  then the other nodes go: after the device's answer to the last `0x051` has
  completed, or (`answer_stuck`) before it can.
- **The first frame to wait in the silence**, in the six cases whose answer
  completed, was queued 200 ms after the last `0x051`, at the first receive
  timeout, and was not an `0x051` (R1); this run's record does not name its
  ID. So no error frame came before +200 ms there, and none of them could meet
  trip 6's threshold at the +200 ms check.

| Case | Injected | 10th error | Expected (spec 7, strictly earlier) | Observed |
|---|---|---|---|---|
| `fast` | a retry every 300 us | +203 ms | trip 6 at the +400 ms check | trip 6, latched at +400 ms |
| `before` | placed | +299 ms | trip 6 at the +400 ms check | trip 6, +400 ms |
| `tie` | placed | +549 ms (threshold first met at the stale check) | trip 2 at the +600 ms check | trip 2, +600 ms |
| `slow` | placed | +1.5 s | trip 2 at the +600 ms check | trip 2, +600 ms |
| `busoff_few` | a retry every 1 ms, bus-off after 5 | none | trip 2 at +600 ms | trip 2, +600 ms |
| `busoff_many` | a retry every 300 us, bus-off after 12 | +203 ms | trip 6 at +400 ms | trip 6, +400 ms |
| `answer_stuck` | a retry every 150 ms; the answer stuck | +1.5 s | trip 2 at +600 ms | trip 2, +600 ms |

- **In every case:** no device `0x051` after its answer to the last VCM
  command (R1); the latch landed at a check point, a receive return within
  the ~100 us poll bracket (R2); the receive kept returning (9 returns in 2 s)
  and the status page answered every poll while a frame was stuck (R4); the
  page's `err_n` rose with the injected errors (up to its 16 cap), so trip 6
  was reachable.
- **Traffic back without a key-on** (the five non-bus-off cases): still
  latched, the stuck frame completed, diag pages on the wire (8 in 1 s; 6 in
  `answer_stuck`), no `0x051` (R5). **A key-on** (key 0 for 0.3 s, then 1)
  cleared the latch (R6).
- **Bus-off** (two cases): no recovery call; through 1.3 s of traffic and a
  key cycle the device stayed latched, the controller dropped 876 delivered
  frames and nothing reinstalled the driver. A re-arm made more than 10 s
  after the last error (spec 7: a re-arm goes live only once the bus has been
  free of a qualifying burst for 10 s) put it back on the bus: 672 frames
  received, not latched (R7).
- **The answer stuck** (5.2 items 5 and 6): the trip withdrew it as a
  stand-down (`standdown` +1, `skipped` +0); it never reached the wire,
  traffic back included, and no `0x051` was on the wire after traffic returned;
  the balance identity held on all 19,417 page reads, every class, and
  `withdrawn + dropped = skipped + standdown` on all 9,708 reads with no
  inhibit frame held; a diag page was handed to the driver after the latch,
  was the stuck frame, and completed when traffic returned.
- **Re-run on the shim-hang fix's fakes** (gen-inhibit/main `38fd103`, merged
  into `tester/spec-shim` as `9f968e0`; reviewer, 2026-10-20): the same seven
  cases, 64 of 64 PASS, `main/` still `e3d9437`'s tree. Of the built files only
  `test/gen_inhibit_shim/fake_twai.c` changed (md5 `9d22e9f3...` to
  `54763177...`); `fake_can.c` and the four product files are byte-identical.
  Rerun on `madhouse-debian` 2026-10-24 with the rest: the same verdicts, row
  for row, as the 2026-10-20 run, `judge_test` 71 ok, and `mutate.py --local`
  45 of 45 caught (none had been run at `9f968e0` before)
  (`runs/spec_shim_madhouse_rerun_20261024/out/spec_shim_nonode_9f968e0_madhouse_20261024T041918/`,
  `runs/spec_shim_madhouse_rerun_20261024/out/spec_shim_mutants_9f968e0_madhouse_20261024T041918.txt`).
  The 2026-10-20 run (`runs/spec_shim_nonode_9f968e0_20261020T103233/`) was
  built through WSL, which its runner requires; it does not record the
  machine or the compiler.
- **Run files.** `runs/spec_shim_madhouse_rerun_20261024/out/spec_shim_nonode_c73656f_madhouse_20261024T041746/`
  (`summary.txt`, `cases.txt`, `identity.json`, `payloads.txt`,
  `judge_test.txt`, `host.txt`). The folder's `README.md` covers the three
  reruns and how they were compared. The 2026-10-20 self-check run is
  `runs/spec_shim_nonode_c73656f_20261020T094736/`.
- **Harness tests.** `judge_test.c`: every verdict passing and failing on
  made-up data, 71 ok. A `layer` case checks the layer's own mechanics on a
  live device (4 PASS). `mutate.py`: 45 of 45 mutants of the judge, the layer
  and the cases caught by a check, on `madhouse-debian`
  (`runs/spec_shim_madhouse_rerun_20261024/out/spec_shim_mutants_c73656f_madhouse_20261024T041746.txt`;
  the same 45 on 2026-10-20, `runs/spec_shim_mutants_c73656f_20261020T094736.txt`,
  build host not recorded).

**A bus with no other node, on the bench -- tester, 2026-10-20 17:04-17:05,
WiCAN on `e3d9437` `_man` (`elf_sha256` `25f50ac0...`). Result: as specified
in all three main arms (verdict PASS; no answer-stuck arm occurred).** Spec
12.4 row "a bus with no other node"; spec 7 trips 2 and 6, 7.1.
- **Rig** (`nonode_bench.py`, design approved by the reviewer 2026-10-20). On
  CAN1: the WiCAN; the Kvaser replaying the interlock slice (key on), which
  closes to make the silence, since it ACKs whenever open; the PCAN in
  listen-only mode as the witness throughout; the X2 held in ROM download mode
  (`x2_hold.py hold` 17:04; `pins` read CAN1 TX "not driven" at the run's start
  and end), released and read off the wire as `756d6714` at 17:06. The
  Innomaker (X2's CAN2) was not opened. **Termination**, as recorded: fixed
  passive resistors, no switched or relay termination on the bench, so closing
  the Kvaser changes no termination (user, 2026-10-20); locations not given.
  The WiCAN was rebooted by software before the first arm and at the end
  (`wican_reboot.py`, both PASS: `_man`, mode 0, not latched).
- **Each arm:** live for 5 s; the replay stopped just after a VCM `0x051` and
  the Kvaser closed 6.3-6.7 ms later; 3 s of silence; the Kvaser back for 3 s
  with the key on; a key-on (`0x592` key 0 for 0.5 s, then 1, encoded with
  cantools; the decoded key of every `0x592` sent read 0 then 1); the page
  read at ~20 Hz throughout.

| Arm | Stuck frame on the witness (copies, median gap) | Trip | Latched by (bracket) | Back: latched reads, diag frames | Key-on |
|---|---|---|---|---|---|
| 1 | `0x7F3`, 10,693 copies, 262 us | error-frame rate exceeded | 0.425 s (0.354-0.425 s) | 58 of 58, 24 | cleared |
| 2 | `0x7F2`, 10,226 copies, 274 us | error-frame rate exceeded | 0.490 s (0.367-0.490 s) | 59 of 59, 30 | cleared |
| 3 | `0x7F2`, 10,226 copies, 274 us | error-frame rate exceeded | 0.408 s (0.313-0.408 s) | 59 of 59, 30 | cleared |

- **In every arm:** the device's answer to the last VCM command was on the
  wire once, before the close; no other device `0x051` after it; none from the
  nodes' return to the key-on, on the witness (provenance rule, 0 VCM
  duplicates excluded) or on the page (`tx_ok` 507 on every read from the
  first latched one to the key-on, in all three arms); nothing from any other
  node in the silence; the device went live again after the key-on.
- **Recorded, not judged:** TEC 0 before the silence and 128 through the rest
  of the arm, REC 0 throughout; no bus-off; the page's `err_n` reached 16 and
  `bus_error` 10,282-10,750; the PCAN logged 16 error reports in each arm
  before the first copy of the stuck frame; arm 3's frame times are 17.6 s
  after arm 2's (the matching counts are not a copied record). The PCAN's
  status at the end read OK.
- **Harness change before the run** (2026-10-20): the X2 gate looked for
  `x2_hold.py` under `GI_FW_TOOLS` (the firmware repo, which does not hold it),
  and took any pins line containing "not driven", which a "dominant" verdict
  ("GPIO7 is not driven but reads 0") also contains and "recessive" does not.
  It now runs the `x2_hold.py` beside it and passes only the verdicts
  "recessive" and "not driven, level unreadable"; the start checks are one
  pure function (`start_refusal`). Test 50 ok; 31 of 31 mutants killed by a
  check (`runs/test_nonode_bench_20261020_x2gate.txt`,
  `runs/test_nonode_bench_mutants_20261020_x2gate.txt`).
- **Run files.** `runs/nonode_bench_20261020T170419.json` (and `_reboot_start`,
  `_reboot_release`), `runs/nonode_bench_20261020_console.txt`,
  `runs/x2_hold_hold_20261020T170408.json`,
  `runs/nonode_bench_20261020T170419_x2/` (release and identify).

**A bus with no other node, emulated, on the bench's measured pattern --
tester, 2026-10-20 17:14, rerun on `madhouse-debian` (gcc 14.2.0) 2026-10-24,
the mock-HAL shim, product `main/` = `e3d9437`'s tree. Result: as specified; the shim judges trip 6 for that timing (83 of 83
verdicts PASS).** Spec 12.4 row "a bus with no other node, emulated", its last
sentence (the layer replays the pattern the bench measured; reviewer,
2026-10-20).
- **The pattern, read from the bench run above** (`runs/nonode_bench_20261020T170419.json`):
  16 error reports on the witness, 246 us apart (arm 1) or 258 us (arms 2
  and 3), TEC 0 to 128; then the stuck frame's copies 262 us apart (arm 1) or
  274 us (arms 2 and 3), the page's `bus_error` rising with them; REC 0; no
  bus-off.
- **Rig.** `test/gen_inhibit_spec_shim/` at `e4bc4a7` (tester clone, branch
  `tester/spec-shim`): `sl_silence_measured(active_us, passive_us)` injects
  one error frame (a bus-error alert, `bus_error_count` +1) per `active_us`
  while TEC is below 128, then one per `passive_us`, TEC staying 128, REC 0,
  no bus-off. Two new cases: `bench_262` (246 then 262 us, arm 1) and
  `bench_274` (258 then 274 us, arms 2 and 3). Each also checks that the
  errors it injected follow that pattern. A new layer case, `layer_bench`,
  checks the layer alone: 77 errors in 20 ms, 15 gaps of 246 us then 262 us,
  `bus_error_count` 77, TEC 128, REC 0, still running.
- **Observed, both cases:** the first error 0.34-0.35 ms after the first frame
  waiting; the 10th error at +202 ms after the last `0x051`; expected (spec 7,
  strictly earlier) trip 6 at the +400 ms check point; got "error-frame rate
  exceeded", latched between +400.043 and +400.146 ms, at a check point. No
  `0x051` after the answer; the receive kept returning and the page answered
  every poll; traffic back without a key-on: still latched; a key-on cleared
  it. The other seven cases and the layer case are unchanged (64 verdicts PASS
  as before).
- **Run files.** `runs/spec_shim_madhouse_rerun_20261024/out/spec_shim_nonode_e4bc4a7_madhouse_20261024T042048/`
  (`identity.json`: head `e4bc4a7`, `main/` tree `c7cd9d55` = `e3d9437`'s,
  nothing modified, harness unmodified; `host.txt`: `madhouse-debian`, gcc
  14.2.0). `judge_test` 71 ok. `mutate.py --local`: 49 of 49 mutants caught by
  a check, including the four new ones: no passive period, the passive period
  from TEC 120, the measured silence without its passive period, and the bench
  cases on a fixed period
  (`runs/spec_shim_madhouse_rerun_20261024/out/spec_shim_mutants_e4bc4a7_madhouse_20261024T042048.txt`).
  The rerun is done the same way as the one in the entry above. Its verdicts
  and detail lines are the same as the 2026-10-20 run's, row for row, and so
  are `cases.txt`, `payloads.txt`, `judge_test` and every mutant. That run was
  built through WSL, which its runner requires, and does not record the
  machine or the compiler
  (`runs/spec_shim_nonode_e4bc4a7_20261020T171443/`,
  `runs/spec_shim_mutants_e4bc4a7_20261020T171443.txt`).

**The goal-20 move's bench subset: B1, B2app and B21 on harness `a24de99`,
B5_B8 on `e4d1fb2` -- tester, 2026-10-23 17:23-17:41 and 18:51-19:50, on
`e3d9437` `_man`. Result: B1, B2app, B21, B5, B7 and B8 PASS, every verdict as
the baseline; B6 FAIL in both runs (9 of 10 arms PASS, the tenth
INCONCLUSIVE), for the reviewer.** The goal-20 move's bench subset, as the
reviewer ordered it (tracker row "Tooling move into git").
- **What:** the chain runner (`bench_compare.py chain`, core `8fdcec5`), each
  check compared with the accepted `e3d9437` baseline. B1, B2app and B21 are
  kept from the `a24de99` run (reviewer's ruling); B5_B8 ran alone on
  `e4d1fb2`, with a fresh X2 dump, after the custody fault below.
- **Code identity:** product commit `e3d9437`, clean trees; harness
  (`gi_tools`) `a24de99` clean in the first two runs, `e4d1fb2` clean in the
  last two (`record.json` of each). Emulated prerequisite, every run:
  `runs/chain/gen-inhibit-emulated_20261022T201733_6b2c` (a whole run, on
  `e3d9437`).
- **Runs:**
  - `runs/chain/gen-inhibit-bench_20261023T172332_bee0` (17:23-17:41,
    `a24de99`): B1 PASS (20 of 20 refused and counted), B2app PASS, B21 PASS
    (both guards hold; raw probe 403 without RST, 20 of 20 each). B5_B8
    NOT-RUN: the jammer flash failed (below).
  - `_20261023T174109_1764`: the runner's automatic re-run of B5_B8, the same
    failure. Compare: `bee0/compare_index.txt`.
  - `_20261023T185101_a389` (18:51-19:20, `e4d1fb2`): B5_B8, and its
    automatic re-run `_20261023T192034_ce10` (19:20-19:50). Compare:
    `a389/compare_index.txt` (flagged 2 of 39, B5 and B6). Log:
    `tooling_move/bench_subset_e4d1fb2_log.txt`.

| Check | Baseline | a389 | ce10 |
|---|---|---|---|
| B5 | PASS; 200/s 3 of 3, 500/s 8 of 9, 1 inconclusive | PASS; 3 of 3, 7 of 9, 2 inconclusive | PASS; 3 of 3, 9 of 9, 0 inconclusive |
| B6 | PASS, 10 of 10 | FAIL, 9 of 10 | FAIL, 9 of 10 |
| B7 | PASS, 3 of 3 as specified | PASS, 3 of 3 | PASS, 3 of 3 |
| B8 | PASS; train 3 of 3, control INCONCLUSIVE | the same | the same |

- **B6, observed.** In both runs the arm short of PASS was in the same
  configuration, n10 lead 8.5 (a389 arm 1, ce10 arm 0): INCONCLUSIVE,
  "delivery was not one error frame per pulse". The jammer reported 10 pulses
  queued (`pulses=10`, end complete); the witness saw 9 error frames in the
  pulse window, one 1 s slot with none (a389 at +6 s, ce10 at +2 s; each
  `x2_jam_threshold_*.json`, `witness_error_times`). The device's `bus_error`
  rose by 1 for each of the 9; no latch. The other 9 arms of each run PASS,
  one error frame per pulse: each n10 arm latched "error-frame rate
  exceeded", no n9 arm latched. In a389 the witness saw one more
  error frame 21.04 s after the first pulse, outside the window, at the arm's
  mode-0 POST, 17 ms after it. The chained run of 2026-10-19 (above) records
  B6 as "flaky, needs review"; the INCONCLUSIVE of 2026-10-20 was n9 lead 5 arm 0 (8 of 9
  pulses delivered).
- **The custody fault and its fix.** In bee0 and 1764 the custody dump
  (two reads, matching sha256, app0 carrying `756d6714`) passed, and
  `x2_flash_custody.py flash-jammer` then stopped with rc 1 before writing
  anything: `FileNotFoundError` reading the dump back, the path doubled.
  `sub_dump` recorded the dump's path relative to the script's own folder
  (since the move, `tools/gen_inhibit/bench/` in the firmware repo, so the
  path climbs out of the repo with `..`), and the readers resolved it against
  another base. The X2 was not written: read off the wire as `756d6714` at 17:42 and 17:47.
  - Fix, `e4d1fb2` (tester clone `wican-fw-vtrux-tester-move`, branch
    `tester/tooling-move`; pushed by the reviewer): `sub_dump` records the
    path relative to the data folder (`gi_paths.data_dir()`), and both readers
    resolve it through one function, `dump_path`, which refuses an absolute
    path or one with a `..` part.
  - Tests: `test_x2_flash_custody.py` 23 checks PASS, among them dump ->
    record -> `_checked_dump`, `restore` and `check-dump` on a fake esptool,
    and each refusal by both readers; `test_x2_flash_custody_mutants.py` 5 of
    5 killed by a check (the old base back, an absolute path, the refusal
    dropped, either reader bypassing it). Run files
    `runs/test_x2_flash_custody_e4d1fb2.txt`,
    `runs/test_x2_flash_custody_mutants_e4d1fb2.txt`.
  - On hardware, a389 and ce10: dump PASS, `flash-jammer` `a89ece8c` done,
    `jammer:banner` read (18:57:27, 19:27:00); each custody record names its
    dump relative to the data folder.
  - In bee0 the end-of-run board state says the X2 was "flashed in this run:
    True", though nothing was written; recorded, not changed.
- **Boards:** the WiCAN on `e3d9437` `_man` (`25f50ac0...`) throughout, not
  flashed, rebooted by software at each run's start and end (`wican_reboot.py`
  PASS each; the last, 19:50: mode 0, not armed, nothing latched, reset reason
  SW). The X2 on `756d6714` read off the wire before each jammer flash and
  after each restore (the runner flashes `x2_app` back, 4 writes verified;
  read 19:20:23 and 19:49:55). The dumps are in `board-backups/` (sensitive;
  path and sha256 in each custody record).
- **Partial runs and the gate** (reviewer, 2026-10-23). The first launch of
  this subset, on `14062d1`, was refused (`tooling_move/bench_subset_14062d1_log.txt`):
  the newest emulated record was then `gen-inhibit-emulated_20261022T125137_147f`,
  the 12:51 run of the `build_checks` group alone (C5_C6_scan, for the move's
  comparison), and a bench run's `requires=` reads the newest emulated record
  on the same product code and refuses a partial one. A whole emulated run
  (`6b2c`) was needed first. **From now on, a partial emulated run is first
  checked for whether it would become the record a bench run's `requires=`
  reads.** Only the two bench suites (gen-inhibit and interposer) have
  `requires=`, and both read emulated records, so a partial bench run gates
  nothing.

**The shared-core re-pin, `8fdcec5` -> `b9896a0`: harness `1e23b4e`, its host
tests on both cores, and B1 and B2app on the bench with a manual pause --
tester, 2026-10-23 21:27-23:27, on `e3d9437` `_man`. Result: every host test
PASS on both cores, every mutant killed; B1 and B2app PASS in both bench runs,
every verdict as the baseline; the pause held until both the PAUSE file was
gone and the Holder line was gen-inhibit's again.** The re-pin of the bench
core, as the reviewer ordered it (tracker row "Tooling move into git").
- **What changed:** `1e23b4e` (branch `gen-inhibit/tester/cutover`, on
  `gen-inhibit/main` `02d6086`) changes the docstrings of
  `suite_gen_inhibit.py`, `test_suite_gen_inhibit.py` and
  `test_suite_gen_inhibit_mutants.py` only: the core they are pinned at is
  `b9896a0` on `charge-interposer/main` (which carries `5fa86c6`'s board
  steps and the bench lock, `0e6f633`, cleared 2026-10-22), not `8fdcec5`.
  No code line changed, so the same tests run against either core.
- **Cores:** each a `git archive` of `shared` and `charge-interposer` at the
  commit, into the tester worktree's `scratch/gen-inhibit-core/<commit>/`,
  put on `PYTHONPATH` (`shared/bench`, `charge-interposer`) for the host
  tests and the bench runs. The run records name the core only by path (the
  settings' `interposer_code`), not by commit.
- **Host tests,** at Idle priority, each core in turn, harness `1e23b4e`
  (run files `runs/repin_1e23b4e/<test>_core<commit>.txt`):

| Test | `b9896a0` | `8fdcec5` (control) |
|---|---|---|
| `test_suite_gen_inhibit` | PASS, 283 ok, 0 FAIL | PASS, 283 ok, 0 FAIL |
| `test_suite_gen_inhibit_mutants` | 72 of 72 killed by a check, 0 errors | 72 of 72 killed by a check, 0 errors |
| `test_suite_judges` | PASS, 158 ok, 0 failed | PASS, 158 ok, 0 failed |
| `test_suite_judges_mutants` | 33 of 33 killed by a check, 0 errors | 33 of 33 killed by a check, 0 errors |
| `test_bench_compare` | PASS, 46 ok, 0 FAIL | PASS, 46 ok, 0 FAIL |
| `test_bench_compare_mutants` | 34 of 34 killed by a check, 0 errors | 34 of 34 killed by a check, 0 errors |

  - **A void first attempt** (21:25-21:26): `test_suite_judges` on
    `b9896a0` reported 76 FAIL, every B judge "0 files match", because the old
    gen-inhibit tester's `test_suite_judges_mutants` was running at the same
    time and both use the one fixed folder
    `%TEMP%\gen-inhibit-suite-judges`, emptied at start (its log ends in a
    `shutil.Error`, files gone mid-copy). Void, kept with that attempt's
    other two outputs in `runs/repin_1e23b4e/attempt1_2133_judges_collided/`;
    every test was rerun from 21:27, the judges tests after that load had
    ended. The fixed
    temp folders are on the harness-defects row.
- **Bench, settings:** a copy of `chain_settings_NICKY-XPS.json` with the
  core paths on the `b9896a0` export and `gi_tools` at a detached worktree of
  `1e23b4e` (local only: it holds the X2's address). No `bench_lock_log`, so
  the runner stays on `bench-holder.md`'s Holder line (`lock.source`
  "holder" in each record). Subset `man_app/B1`, `man_app/B2app`.
- **Code identity, both runs:** product `e3d9437`, clean (`gi_images`,
  `gi_prod_dir`); `gi_tools` `1e23b4e`, clean. Emulated prerequisite:
  `gen-inhibit-emulated_20261022T201733_6b2c` (`requires_met`).
- **Runs:**
  - `runs/chain/gen-inhibit-bench_20261023T221639_0be3` (22:16:39-22:26:44):
    B1 PASS (20 of 20 refused and counted; commands 20, passed 20, fail 0),
    B2app PASS (rc 0). No pause in this run: the watcher meant to write
    PAUSE waited for a check-start event, and the runner writes none (a
    check's folder appears when it starts). Kept as a further B1 and B2app
    run.
  - `runs/chain/gen-inhibit-bench_20261023T222808_a18b` (22:28:08-22:39:43):
    B1 PASS (the same figures), B2app PASS (rc 0), with the pause below.
  - Compared with the baseline index (`bench_compare.py report`): each
    "flagged: 0 of 39; re-run: none", B1 PASS against PASS, the other 37
    not selected; B2app has no index entry of its own (it feeds B2, B24 and
    B13 at the desk). `runs/repin_1e23b4e/compare_0be3.txt` (sha256
    `6376619d95f8c264a25bcbcdf0f8c836d067e3f698081ce9f55f4a05ab88d46f`),
    `compare_a18b.txt`
    (`ac347e90e377fc412c5c3702e5e0b80f39decaf401e3a5bb0cae81e8c3c95d04`).
- **The pause, run `a18b`** (`progress.jsonl`; `pause_poll_s` 10 s, the
  default):
  - 22:29:45 PAUSE written into the run folder, once `man_app/B1/` existed.
  - 22:30:22 `pause` event, at B1's end: "PAUSE file present; waiting until
    it is removed and the Holder is tester (gen-inhibit)".
  - 22:30:35 Holder set to `free` by hand. At 22:31:10 still waiting, no
    `man_app/B2app/`.
  - 22:31:17 PAUSE removed, Holder still `free`. At 22:31:52 still waiting,
    no `man_app/B2app/`.
  - 22:32:00 Holder set back to `tester (gen-inhibit)`. 22:32:02 `resume`
    event, "PAUSE removed and the lock is ours"; B2app started.
  So neither condition alone ended the wait: removing PAUSE while the
  Holder was someone else's (here `free`) did not resume the run.
- **Boards:** the WiCAN on `e3d9437` `_man` (`25f50ac0...`) throughout, not
  flashed; `wican_reboot.py` PASS at each run's start and end (mode 0, not
  armed, nothing latched; the last at 22:39:32). The X2 on `756d6714`, read
  off the wire twice in each run, not flashed. Lock taken 22:16, released
  22:40 (`bench-holder.md`).
- **Load during the runs:** the host tests above ran at Idle priority through
  both bench runs (`test_suite_judges_mutants` on `b9896a0` through `0be3`;
  `test_suite_gen_inhibit_mutants` on `8fdcec5` through `a18b`, and on
  through the charge-interposer tester's DIAGNOSTIC run of 22:41-22:57, which
  judged nothing). No build ran. B1's figures are counts (commands, passed, fail) and B2app reports
  only its rc; neither has a timing figure.

**The bench lock in log mode, before the cutover: shared/bench's tests at
`b9896a0`, then two runs on the bench against a scratch log -- tester,
2026-10-23 23:41 to 2026-10-24 00:06, on `e3d9437` `_man`. Result: every test
PASS and every mutant caught; on the bench the runner took, paused, resumed
and released as `shared/bench/SPEC.md` (shared/main) section 6 says, and a
hand take of its paused run under a session's PID was a lost lock with
nothing written to the log.** The bench-lock cutover's verification, as the
reviewer ordered it (2026-10-23): verify first, then switch. Nobody was cut
over: the runs used a scratch log, and `bench-holder.md`'s Holder line, still
the lock, was held by hand for the whole session.
- **shared/bench's own tests at `b9896a0`** (the core gen-inhibit pins,
  `git archive` export in the tester worktree's `scratch/`), at Idle, each
  plain and then with `--can-fail`:
  - `test_bench_lock`: 29 of 29 passed; 130 of 130 mutants caught.
  - `test_run_core`: 57 of 57 passed; 57 of 57 caught, each with its rule
    switched off.
  - `test_decode`: 7 of 7 passed; 33 of 33 caught (cantools 44.1.0; the
    README names 41.3.1 as the version the golden test passed on).
  - Nobody else was running them (process list checked first; their scratch
    folder, a fixed `%TEMP%\vtrux-bench-tests`, was last written
    2026-10-22). Run files `runs/cutover_b9896a0/<test>_plain.txt`,
    `_can-fail.txt`.
- **Bench setup:** settings `chain_settings_scratchlog.json` (local, holds
  the X2's address): the standing `chain_settings_NICKY-XPS.json` plus
  `bench_lock_log` naming `scratch/gen-inhibit-cutover/bench-log-scratch.jsonl`
  in the tester worktree, created by `bench_lock.py init` at 23:46:57
  (`host` NICKY-XPS, `env` windows). Harness `4de33c9` (gen-inhibit/main),
  in the clean detached worktree `gen-inhibit-tester-harness`; core
  `b9896a0`. Subset `man_app/B1`, `man_app/B2app`. Holder line taken by
  hand 23:47, released 00:06, interposer tester messaged at both.
- **The standing settings moved** (reviewer, 2026-10-23): from this session
  `chain_settings_NICKY-XPS.json` names that worktree as `gi_tools` and
  `gi_checks_clone`, no longer the old clone `wican-fw-vtrux-tester`, and
  the `b9896a0` export as its core. `gi_images` is unchanged (the interposer
  reads it for `wican_man`). It names no `bench_lock_log` (that is the
  switch).
- **Run 1**, `runs/chain/gen-inhibit-bench_20261023T234744_2116`
  (23:47:44-00:00:29): PASS 2, B1 20 of 20 refused and counted, B2app rc 0.
  - 23:47:44 `take`, pid 2680 (the runner's `python.exe`, checked in the
    process table), run id = the run's.
  - 23:49:19 PAUSE written once `man_app/B1/` existed; B1 ran to its end
    (23:49:56).
  - 23:49:56-23:50:48: both boards identified (WiCAN `25f50ac0...` over
    HTTP, X2 `756d6714` off the wire 23:50:38) and the end hooks run (the
    WiCAN release reboot, PASS, mode 0, not latched); 23:50:48
    `free_paused` with those two reads. `status`: free, the run paused.
  - 23:51:50, a minute later: still waiting, no `man_app/B2app/`, the log
    unchanged. PAUSE removed.
  - 23:51:58 `take`, pid 2680, the same run id, note `resume`; 23:52:09 the
    start hooks again (WiCAN start reboot PASS), then both boards
    re-identified (`resume-identify`, 23:52:50, nothing to flash). B2app
    ran.
  - 00:00:29, after the end identify and the end hooks: `release` with both
    reads (WiCAN 23:59:37 and the end reboot, X2 00:00:19).
- **Run 2**, `runs/chain/gen-inhibit-bench_20261024T000100_b014`
  (00:01:00-00:05:05): B1 PASS (20 of 20), B2app NOT-RUN, as specified.
  - 00:01:00 `take`, pid 16768 (not checked in the process table this
    time), run id = the run's. PAUSE at 00:02:35; `free_paused` 00:04:04
    with both reads, as in run 1.
  - 00:04:16 a hand `take --run gen-inhibit-bench_20261024T000100_b014
    --pid 7804` (7804: this Claude Code session's `claude.exe`), accepted;
    `status`: held by tester (gen-inhibit), that run, pid 7804. PAUSE
    removed at once.
  - 00:04:24 the runner: `lock-lost`, "run ... is no longer paused: held by
    tester (gen-inhibit), run ..., pid 7804"; its `lost` was refused
    ("lost names run ..., which holds the lock"), so the log got nothing
    (still 8 lines). B2app NOT-RUN "LOCK LOST: ...". `index.txt` and
    `board_state.txt` open with the LOCK LOST line; both end hooks "not run:
    the lock is not ours"; no restore, no release.
  - **Observed, for the reviewer:** after the LOCK LOST the runner still
    identified the WiCAN (over HTTP, 00:04:24) and the X2 (off the wire,
    00:05:05). Reads only, nothing flashed. Section 6 says a lost lock
    skips "the end restore and the end hooks' board actions".
    **Ruled** (user, 2026-10-24, through the reviewer; shared/bench
    `SPEC.md` section 6, `9aa8924`, on shared/main by merge `d20dada`):
    board reads after a LOCK LOST are not prevented. When a run's
    record shows them, the role tells the session that held the bench at
    the time what was read, how and when. Here that holder was this
    session's own hand take, so nobody needed telling.
  - 00:05:41 the scratch lock released by hand under the session, with
    those two reads.
- **The scratch log**, 9 lines: `init`; run 1's `take`, `free_paused`,
  `take` (resume), `release`; run 2's `take`, `free_paused`; the hand
  `take`; the hand `release`. Copy `runs/cutover_b9896a0/bench-log-scratch.jsonl`
  (sha256 `9f02eb25...`). `status` at each step:
  `runs/cutover_b9896a0/<run>_status_{paused,resumed,handtake,end}.txt`.
- **Boards:** the WiCAN on `e3d9437` `_man` (`25f50ac0...`) throughout, not
  flashed, rebooted by software at each start, pause, resume and end hook
  (the last at 00:04:04, run 2's pause), mode 0, not latched each time. The
  X2 on `756d6714`, identified only, not flashed. No build ran.

**The diag decode rework (port plan toolkit item 10): the e3d9437 baseline
re-graded through `gi_diag`, the DBCs read from git at `9c54669` -- tester,
2026-10-25, harness `e9ee61e` for the re-grade and `ebe2c5a` to `7229c06` for its
own tests (branch `gen-inhibit/tester/decode-rework`).
Result: every re-gradable check keeps its baseline verdict, with every figure
computed from the diag frames exact, except B13 arm8's `from_zero` and interval
figures (accepted, reason below); four checks or tools cannot be re-graded
and are listed by id; the rest decode no diag page.** The acceptance the
gen-inhibit reviewer set in the build message of 2026-10-24: re-grade the
`e3d9437` baseline captures with the new code and get the same verdicts, on the
diag DBC and schema doc in this repo (`gen-inhibit/diag/` at `9c54669`: schema
7, `DiagSchemaHash` `7af6adef1d948197`), with the reviewer's readings Q1-Q9
(2026-10-24/25).
- **What changed:** every tool, judge and host check that decodes a WiCAN diag
  page decodes it through `tools/gen_inhibit/bench/gi_diag.py` (on
  `shared/bench`'s `vtrux_bench.decode` at the suite's pin `b9896a0`, checked
  by blob id), never from a file path:
  - the DBC for each frame is picked by its schema byte from
    `vtrux-wican-diag.dbc`'s git history (`check_history` clean); an unknown
    schema is refused (Q1);
  - a live run records `gi_diag.run_identity()` (harness commit, DBC commit,
    version and hash), and a re-judge refuses a page on another DBC (Q1);
  - a diag page before a capture's first schema page is undecodable: counted
    and reported, never decoded with a later schema (Q2). `0x7F0`
    (`WICAN_Probe`) is outside the schema and decodes anywhere; `gi_diag`
    refuses a history in which its layout is not the same in every DBC (Q8);
  - golden traces compare decoded, by signal name, after `RenamedFrom`,
    without the two schema signals (Q3);
  - the interposer's DBC is read with `git show` at the pinned
    `charge-interposer/main` `d83fc87` (Q5); `interlock_judge` raises on an
    unknown schema and keeps None for other failures (Q6);
  - the eight hand decoders moved onto `gi_diag` by signal name (Q7):
    `quiesce_analysis`, `b11_post_latency`, `x2_jam_cases`, `tec_busoff_sweep`,
    `live_jam_arm`, `diag_flags_trace`, `golden_schema_bump_check` phase 2, and
    `test/gen_inhibit_host/bus_on_check.py`;
  - a passive-analysis row's `error` is set only when a judged figure cannot be
    computed from decodable pages, and the suite's B11, B11raw and B13 judges
    give ERROR on it (Q9).
  Commits `5c8c5e1` (the rework), `8e09e73` (`x2_jam_cases` passes the record's
  identity to its judges in a live run), `e9ee61e` (Q8, Q9), and six fixes
  the harness's own tests found (below): `a790bc9`, `01db48e`, `6ee0f13`,
  `107f659`, `1d595e8` and `7229c06`, on `gen-inhibit/main` `b55dd0e`; `ebe2c5a` is the
  methodology cherry-pick of `4553dc5` and changes no harness file.
- **Method:** each re-gradable check's desk step or judge re-run over the
  baseline's stored captures (`runs/baseline_e3d9437/`), with the harness at
  `e9ee61e`, and again with the pre-rework harness `b55dd0e` (a `git archive`)
  as the control, so a change from the decode rework shows as a difference
  between the two, and a difference from anything older shows in both.
  Compared: the verdict and every figure computed from the frames, exact (Q4).
  Run folder `runs/decode_regrade_e9ee61e/` (its `README.md` lists the files).

| Check | What was re-run | Result |
|---|---|---|
| B11 | `quiesce_analysis` over the 6 arms | same; adds `diag_decode` (0-2 pages per arm before the first schema page, no figure from them) |
| B11raw | `b11_rawpost_judge`, `quiesce_analysis` | same; the judge's output byte-identical, `quiesce_analysis` as B11 |
| B13 | `passive_analysis` (8 arms), `respond_booking_check` (B13's PASSIVE arms, B2's RESPOND arms) | booking byte-identical (every probe judged, Q8); passive the same except arm8, below |
| B20 | `interlock_judge.judge` over the 28 records | 28 of 28 AS SPECIFIED; equal to the control exactly, adding `errors` [] and `diag_decode` (no page undecodable) |
| B25 | `b25_keystate_judge` on the rekey-long record | byte-identical |
| B5 | `live_jam_arm.judge` over 12 arms (4 runs) | equal to the stored judgements, 0 differences |
| B8 | `tec_busoff_sweep.judge_bus_on` over 4 arms, `0x7F1`s from the witness | equal to the stored judgements, 0 differences |
| B18 | the recorded `0x7F3` payload through `gi_diag` | `diag_git_hash` 803755636 (`0x2FE85674`), `diag_fw_ver16` 7, `diag_schema_ver_build` 7: the record's figures |
| B15host | `diag_decode_check` on madhouse-debian against the `e3d9437` host build | the same, adding the DBC identity line and two decoder lines |
| H | `run_tests` (golden compare) and `bus_on_check` on madhouse-debian, the `e3d9437` host build | the same; `run_tests` adds one line naming the export |
| B2, B3, B24 | their desk steps (no diag decode), as a check on the method | byte-identical |
| (B11, B3 support) | `b11_post_latency` over the baseline's `e3d9437` arms (6 and 15) | equal to the baseline's lines |

- **B13 arm8 (`B13_e4_passive_sv_inhibit_e3d9437_20261016T215910_arm8`):** its
  first counter page came before the witness's first schema page, so it is
  not decoded, and `from_zero` is judged from the first decodable counter page
  (reviewer, 2026-10-25, Q9: the check's purpose survives, since a count that
  had not reset on the arm would still exceed the commands since the arm).
  Before and after: `first_counter` 0 -> 108, `commands_to_first` 0 -> 123,
  `intervals` 24 -> 23, `partial` 1 -> 0; `from_zero` ok and `intervals` ok
  true in both. The control gives the baseline's figures, so the change is
  the rework's.
- **B20's stored judgements differ from both harnesses in one way:** in 7
  records (the four `liveflag-*` cases, `low-after-close`, `soc-boundary` and
  `soc-recovers`) a latch window's page count in a check or precondition detail
  reads one page more than stored, or the same count with "from +x s" added
  (for example "4 pages" stored, "5 pages, from +0.64 s" now; 26 details in
  all). That is the reviewer's 2026-10-20 "caught up" rule
  (`interlock_judge.latch_and_flag`), which came after the baseline; the
  control gives the same, so it is not a decode change. Every verdict is
  unchanged.
- **Not re-gradable:**
  - B15: `wican_segment_probe` keeps only decoded values, no raw payloads, and
    its verdict is the per-id frame counts.
  - `golden_schema_bump_check`: its runs are on `6771edf` and `099e9da`
    (schemas 5 and 6), which are not in the DBC's git history, and it has no
    `e3d9437` run.
  - `diag_flags_trace`: a live tool that keeps no capture; no baseline run.
  - `x2_jam_cases.judge_b_busoff`: no case calls it (its test only).
- **No diag decode** (each checked by its tools' imports and a search for the
  diag ids): B1, B4 (it records `0x7F9` raw and never decodes it), B6, B7
  (`judge_busoff_rearm` reads page samples and `0x051`), B9, B10, B12, B14,
  B16, B17a, B17b, B19present, B19absent, B19page, B21, B22, B23, B26,
  NONODE; B27 is the declared skip; C1C7, C3, C5, C6, C2scan.
- **Defects the harness's own tests found, all fixed but one.** None is in a
  re-grade step. The re-grade at `e9ee61e` stands for `7229c06`: since
  `e9ee61e` the only change outside the harness's own tests and READMEs is
  `build_output.py`, and no re-grade step runs it.
  - `a790bc9`: `5c8c5e1` bound a new module-level name in
    `test/build_checks/build_output.py` that shadowed the reference folder's
    path, so its partition-table row raised TypeError on every image.
    `test_partition_row` fails at `e9ee61e` and passes at `a790bc9` and at
    `b55dd0e`.
  - `01db48e`: `5c8c5e1` changed B15host's copy command, and two
    `test_suite_gen_inhibit_mutants` edits still named the old text. Neither
    applied: 71 of 73 killed, 2 errors, at `ebe2c5a`.
  - Three mutant runners killed every mutant whatever its edit, because the
    unmutated copy they test already failed checks, and none ran that copy as a
    control. Found when every mutant of `test_suite_gen_inhibit_mutants` at
    `01db48e` (73 of 73) failed the same six B15host checks. A copy of `gi_diag`
    in a bare scratch folder finds no git history for the DBC, which it reads
    from three folders up. Each runner now runs its unmutated copy first, and
    the run fails if that control fails a check:
    - `6ee0f13`, `test_suite_gen_inhibit_mutants`: since `5c8c5e1`, B15host
      exports through `gi_diag`; the control failed 6 checks. The mutants now
      run in a shared clone of the repo at HEAD, in `scratch/`.
    - `1d595e8`, `test_baseline_b_desk_mutants`: it copied the script alone,
      which runs its tools from its own folder, and since `5c8c5e1` its B11,
      B11raw and B13 tools also decode through `gi_diag`; the control failed
      B11's index check. It runs in a clone the same way.
    - `107f659`, `test_baseline_capture_stop_mutants`: it copied the five
      scripts alone, and they run `capture_window.py` from their own folder;
      the control failed 5 checks. Each mutant is now a copy of the whole of
      `bench/`.
    The suite runner's control passes at `b55dd0e`; the other two fail there as
    they do at `01db48e`, so those two defects are older than the rework
    (`runner_controls/`).
  - `7229c06`, `test_suite_gen_inhibit.py`: with its control passing, the
    suite runner showed one survivor, "B15host's copy failing is not seen"
    (no `pipefail`). Since `5c8c5e1`, the test's failing-copy case left out
    `pins.json`, which `gi_diag`'s export reads, so the export failed before
    `tar` ran, and the pipefail was never exercised. The case now leaves out
    `diag_decode_check.py`, in a shared clone so that the export passes, and a
    new check asserts that the copy reached the host, so it is `tar` that
    failed.
  - Every other mutant runner's control passes, run as its own main with one
    no-op mutant (`scripts/mutant_control.py`). One exception is not fixed:
    in `test_raw_http_probe_mutants`, `image_elf_sha`'s unmutated copy
    crashes, because the runner passes a scratch folder as
    `test_image_elf_sha.py`'s optional image path. All its synthetic checks
    pass first, so its six kills are by checks.
  - After the fixes: at `7229c06` (`tests_7229c06/`), `test_suite_gen_inhibit`
    297 ok and its mutants 73 of 73 killed by a check, `test_baseline_b_desk` 45
    ok and its mutants 9 of 9; at `107f659` (`tests_107f659/`), the capture-stop
    mutants 15 of 15. Every control passes, and the kills are spread over the checks the
    edits aim at, not one shared check.
- **The harness's own tests:** a sweep of every test file in
  `tools/gen_inhibit/`, `tools/gen_inhibit/bench/` and `test/build_checks/`
  (`sweep_rows.txt`), then, at `ebe2c5a`, one at a time with no time cap, the
  84 that matter here (`tests/`): the sweep's first 50 rows, which ran while
  the rework's files were still changing, the decode tests, every test that
  imports `build_output.py`, and three that need `INTERPOSER_CODE`.
  - In `tests/`, 83 of 84 pass. Among them: `test_gi_diag` (mutants 23 of 23),
    `test_suite_judges` (36 of 36), `test_suite_gen_inhibit` (296 ok),
    `test_interlock_judge` (175 checks; with `interlock_bench`'s, the five
    interlock mutant files kill 27 of 27),
    `test_x2_jam_cases_decoder` (3 of 3), `test_respond_booking_check`,
    `test_passive_analysis`, `test_diag_readers`,
    `test_golden_schema_bump_check_mutants` (6 of 6), `test_reference_paths`
    (6 of 6), `test_raw_http_probe_mutants` (113 of 113) and
    `test_partition_row`. The 84th is `test_suite_gen_inhibit_mutants` (71 of
    73, 2 errors), fixed in `01db48e` (above). Two of the 83, the B-desk and
    capture-stop mutant runners (9 of 9 and 15 of 15), count for nothing: their
    controls failed (above). They were re-run after their fixes, in
    `tests_107f659/` and `tests_7229c06/`.
  - The sweep's rows 51 onward ran at `e9ee61e`, which differs from
    `ebe2c5a` in no file they import: 74 of 87 pass, 5 are re-run and pass in
    `tests/`, and 8 need an argument or a local input.
  - Not run as tests, since each needs a real build tree, the bench or a local
    settings file: `build_checks_stock_mutants`, the four `prod_build` tests
    (`tools/prod_build.py` is retired on `gen-inhibit/main`, known anomaly
    (3)), `test_refusal_pairs`, `test_chain_settings_chip` and
    `test_only_inert` (a bench tool). `test_reply_golive_report`, which needs
    only a scratch folder, passes at `01db48e`.
- **No hardware:** no board was touched and the bench lock was not taken. The
  host half ran on madhouse-debian in `<madhouse-debian home>/gi_regrade_e3d9437`, a `git archive`
  of `e3d9437` with this branch's `run_tests.py`, `bus_on_check.py`,
  `gi_diag.py`, `pins.json`, `diag_decode_check.py` and `gi_diag`'s export,
  `e3d9437`'s own two scripts kept beside them as the control.

**Port step 0c, host and shim half: 0x7F3 carries the build id, the host suite
on schema 8 and its goldens -- tester, 2026-10-25, on `b9a35c3` (main/ =
`ef553fa`'s). Result: PASS. The bench half (B18 at the tip, and the rest of
part B decoded with schema 8) is not run yet.** Spec 10 (0x7F3 B0-B3 are
`diag_build_id`, diag schema 8; user, 2026-10-25), the 12.4 row "0x7F3 carries
the build id" (host shim half) with "the page names the binary", and the host
suite (part H) decoded with schema 8. The implementor's range
`564c129..ef553fa`; the harness `gen-inhibit/tester/step0c` `564c129..6ba7eff`.
- **Build:** `b9a35c3`, a local merge (on no branch) of `ef553fa` and the
  tester's branch at `f3f8562`; its `main/` tree (`cfc88b81...`) is
  `ef553fa`'s, so every result here is about `ef553fa`'s product code.
- **"0x7F3 carries the build id", host half: PASS**
  (`runs/step0c_b9a35c3/spec_shim/`, madhouse-debian, gcc 14.2.0, built in
  its own shell; `main_is_base` true against `ef553fa`, nothing modified).
  `build_id_test` builds the product as shipped against the fakes, whose app
  descriptor carries a fixed ELF SHA-256 (`000ff0ff 01234567 ...`). Spec 10's
  rule (a u32 with the hash's first byte most significant, sent
  little-endian) gives B0-B3 `ff f0 0f 00`, which the test also works by hand.
  Over 4 s in INHIBIT, all 3 0x7F3 frames on the wire carry those bytes and 8
  in B6 (schema 8), and their value printed `%08x` is `000ff0ff`, the hash's
  first four bytes. The page's `elf_sha256` is the descriptor's hash and starts
  with that build id ("the page names the binary"). 7 of 7 rows. The test's
  power: 6 of 6 mutants of the product's build id and page hash, in a scratch
  copy, are caught (the hash's bytes as a little-endian value, bytes 4-7, a
  byte dropped, always 0, the config never given it, the page's hash one byte
  off; WSL self-check). The row "a bus with no other node, emulated" in the
  same run: every RESULT line PASS.
- **The host runner's build id:** `cfg build_id <n>` reaches every 0x7F3 as n,
  decoded with the schema 8 DBC and as B0-B3 little-endian, also with the top
  bit set; unset it is 0, the goldens' value; the old key `git_hash` is
  refused (`test_cfg_build_id`, 13 checks, in the host run below; 6 of 6
  mutants of the runner's cfg line caught, WSL self-check).
- **Host suite (part H): PASS** (`runs/step0c_b9a35c3/host/host_run.log`,
  madhouse-debian, gcc 14.2.0, `baseline_host_run.sh`, which now ships
  `gi_diag` and its DBC export): make, scenarios, replays, `test_signals`,
  `invariants`, `passive_diff`, `fuzz_sequences --seed 0`, `load_margin`,
  `spec_expect_check` and `drained_check` (each with its self-test),
  `bus_on_check`, `test_cfg_build_id`, the long-arm fixture (23,241,533
  bytes), the shim (E1, ASan, auto-arm, auto-arm ASan), the scheduler (and
  ASan) and `config_vs_spec` all exit 0. `run_tests` on the old goldens: 8
  passed, 72 failed, 23 without goldens, every changed line a 0x7F3 page
  (`diag_git_hash=0` against `diag_build_id=0`): the expected 0c difference.
- **The goldens, re-blessed for schema 8** (`gen-inhibit/tester/step0c`
  `6ba7eff`; `runs/step0c_b9a35c3/rebless/`). Blessed in the host run's own
  copy; after it `run_tests` passes 80 of 80. `golden_schema_bump_check
  --old-ver 7 --new-ver 8 --same-value diag_git_hash=diag_build_id`: only the
  schema moved, in all 72 changed goldens (without `--same-value`, the
  control, it rejects all 72: the DBC has no `RenamedFrom` between the two
  names, because the meaning changed, not the bytes). Byte level: 547 0x7F1
  lines B0 07 -> 08 and 516 0x7F3 lines B6 07 -> 08, 0x7F3 B0-B3 `00000000` on
  both sides, every other line identical; the 8 unchanged goldens are replays
  with no diag lines. The bless also pinned the 23 scenarios that have no
  golden on purpose; those goldens and pin lines were dropped, so
  `stimulus.sha256` is unchanged.
- **Run files.** `runs/step0c_b9a35c3/` (`host/`, `rebless/`, `spec_shim/`).

**Port step 0c, bench half: part B on `6926fb0`, decoded with schema 8 --
tester, 2026-10-25 13:23 to 19:03, on `gen-inhibit/main` `6926fb0`. Result:
every verdict matches the `e3d9437` baseline but B9's, which is flaky, needs
review (observed vs spec below).** This followed one harness defect, fixed and
re-run (B20, B25), and one Wi-Fi outage, whose casualties were voided and
re-run (B3, B11raw, B15). Spec 10 (0x7F3 B0-B3 are `diag_build_id`, diag
schema 8), the 12.4 row "0x7F3 carries the build id" (bench half, B18), and
part B decoded with schema 8. Acceptance (reviewer, 2026-10-25): every verdict
as the baseline, with 0x7F3 B0-B3 the expected difference. Harness:
`gen-inhibit/tester/step0c-bench` `6926fb0..1f6250d`.
- **Build.** The part B images were built at `6926fb0` from a detached
  worktree, with clean trees: `_man` `3639f8b2...`, `_rt` `3c7610cf...`,
  `_txabort` `64a63148...`, `_abortwin` `f73220f9...`, `_rxq` `a5a0888c...`.
  `_man2` built to `3639f8b2...`, the same as `_man`: builds are now
  reproducible.
  - **Prod: a release build standing in for prod** (reviewer, 2026-10-25): the
    shared tooling's release build of `6926fb0`, also `3639f8b2...`.
  - Its release record went to a stand-in reverse-it folder, not
    `firmware/builds/`.
  - It is a test image, never flashed to the truck, and not a prod build in
    spec 12.3 item 3's sense.
  - Build logs: `runs/step0c_bench_6926fb0/builds/`.
- **Expected differences from the baseline,** all from those three equal
  hashes (reviewer ruling, 2026-10-25; harness `f0e29fb`):
  - the `man2` group retires;
  - B17a's other image is `_rxq`;
  - B17b runs in the rxq group (image `_rxq`, other `_man`);
  - the prod page's other image is `_rxq`.
- **Identity rows, labelled, not compared:**
  - B17a page `elf_sha256` `3639f8b2` (baseline `25f50ac0`);
  - B17b `a5a0888c` (`f9de9de9`);
  - B19page `3639f8b2` (`f3957789`);
  - B18 `diag_build_id` `3639f8b2` (baseline schema 7 `diag_git_hash`
    `0x2FE85674`).
- **"0x7F3 carries the build id", bench half (B18): PASS.** MATCH, 7 rows ok.
  `diag_build_id` read off the wire is `3639f8b2`, the first four bytes of the
  running image's `elf_sha256`.
- **Emulated prerequisite:** `runs/chain/gen-inhibit-emulated_20261025T123158_ba11`
  at `6926fb0`. PASS 3 (H, B15host, C2scan); NOT-RUN 2 (C1C7 and C3, by
  design).
- **Boards.** Each was read off the board by the runner's identify before
  every group (each run's `progress.jsonl`).
  - WiCAN: `_man` `3639f8b2...` and each group's OTA image.
  - X2: per group, on `756d6714`, held, the jammer (custody dump taken) or
    `slcan`.
  - In the first full run, every OTA took at its first attempt: rt, txabort,
    abortwin, rxq and prod.
  - The prod group did OTA, from `_rxq` to the stand-in. The ruling's "no
    OTA" assumed the board was already on that hash, but rxq runs before prod.
  - In re-run 2, rt's first attempt did not take ("(a1) NO STATUS, NO
    RESTART"); the second took.
  - Each run's lock was held by its runner (`$REVERSE_IT_DIR/firmware/bench-log.jsonl`). The
    one hand take was for the hand OTA below.
- **Runs** (`runs/chain/`):
  - `gen-inhibit-bench_20261025T132354_3d53`, at 13:23. **Refused at start:**
    the WiCAN was on `e3d9437` `_man` (`25f50ac0...`), not a suite image.
    NOT-RUN 35, nothing flashed.
    - Then a hand take (13:25:30) and `bench_flash.py --flash` to `6926fb0`
      `_man`: TOOK, release reboot PASS, released 13:28:02
      (`runs/step0c_bench_6926fb0/hand_ota_man/`).
  - `_132810_407a`, at 13:28. **Stopped at start-identify:** the X2 identify
    refused the run settings' key `interposer_dbc`, retired 2026-10-24.
    - The X2 was not touched. The key was removed from the run settings only.
  - `_132956_cfcd`, 13:29:56-15:39:37, every group, harness `f0e29fb`:
    PASS 35, FAIL 2 (B20, B9), ERROR 2 (B25 and its rc), NOT-RUN 1 (B27, the
    declared skip).
  - `_155323_6204`, 15:53:23-16:14:06, re-run 1 of B20, B25 and B9,
    harness `9b74ba0`: PASS 3.
  - `_161806_08ea`, 16:18:06-17:49:36, re-run 2 of the other flagged checks,
    harness `9b74ba0`: PASS 13, FAIL 1 (B15), ERROR 3 (B3, B11raw and its rc),
    INCONCLUSIVE 1 (NONODE).
  - `_183925_86b5`, 18:39:25-19:03:05, re-run 3 of B3, B11raw and B15,
    harness `1f6250d`: PASS 5.
  - **The comparison:** `cfcd/compare_report.txt` (`bench_compare.py report`,
    all four runs; flagged 13 of 39). The three-run report it replaces is kept
    beside it as `compare_report_cfcd_6204_08ea.txt`.
    - **Correction (tester, 2026-11-23): the folder does not hold these two files as
      written. Its tree digest changed. The cause is unknown.** Read on madhouse-debian:
      - The folder has no `compare_report.txt`.
      - `compare_report_cfcd_6204_08ea.txt` (12,962 bytes, modified 2026-10-25 19:04:37)
        holds the four-run report. Its first line is the note that adds re-run 3 (`86b5`).
        It compares all four runs and flags 13 of 39.
      - No `.txt` file under `runs/` holds the three-run report.
      - No file or folder in it was modified or changed after 2026-10-25 19:28:50.
      - The 2026-11-07 manifest, written on NICKY-XPS, gives its tree digest as
        `50c6b7b7...`. The manifest of 2026-11-23 gives `fc3f1490...` (717 files).
      - A desk probe removed each file in turn, and changed each file's line endings in
        both directions. No single change gives `50c6b7b7...`.
      - The NICKY-XPS copy that the 2026-11-07 manifest hashed cannot be read from
        madhouse-debian. A read of that copy would settle the cause.
    - **Later 2026-11-23 (tester): the NICKY-XPS copy was read. With its two report files,
      the folder gives the 2026-11-07 digest in full.** A methodology helper session on
      NICKY-XPS read the copy, read-only, and gave each file's size and SHA-256.
      - The NICKY-XPS copy has 718 files. It holds both files as this entry names them:
        - `compare_report.txt`: 12,962 bytes, modified 2026-10-25 19:04:37, SHA-256
          `9c5ddda2...`. This is the four-run report.
        - `compare_report_cfcd_6204_08ea.txt`: 11,436 bytes, modified 2026-10-25 17:56:37,
          SHA-256 `625eab84...`. This is the three-run report.
      - On madhouse-debian, `compare_report_cfcd_6204_08ea.txt` has SHA-256 `9c5ddda2...`:
        the four-run report, under the three-run report's name.
      - The madhouse-debian tree, with those two files and hashes in place of its one,
        gives 718 files and the digest `50c6b7b7984e02a1eea999e3bec8932b145df86aaecdf60d8d4e8276853648b4`.
        This is the 2026-11-07 manifest's digest in full.
      - NICKY-XPS's SeaDrive log has, at 2026-10-25 19:30:10, a failed rename of
        `compare_report.txt` to `compare_report_cfcd_6204_08ea.txt` in this folder: "UNIQUE
        constraint failed". The log does not say which machine made the rename.
- **The tester's miss.** `cfcd` was started as a plain `vtrux_bench.run run`,
  not `bench_compare.py chain`, so the automatic re-run of the flagged checks
  did not happen. Re-runs 1 and 2 were started by hand, with the check list
  `bench_compare.rerun_checks` gives, desk reads included.
- **A harness defect: B20 FAIL and B25 ERROR in `cfcd`; fixed in `9b74ba0`.**
  - `interlock_bench.py` still gated on `bench-holder.md`'s Holder line,
    frozen at "free" since the 2026-10-24 lock cutover. After building its 28
    stimuli, B20 refused ("bench-holder.md names 'free' as the holder") while
    the runner held the lock in the bench log.
  - B25 reads B20's output, and had none.
  - `x2_pins_noreset_check.py` had the same gate; it is not in the suite's
    verdicts.
  - `bench_held.py` now asks the bench log through `vtrux_bench.bench_lock`,
    and fails closed.
  - Re-run 1: B20 28 of 28 AS SPECIFIED. B25 AS SPECIFIED: `rule1_violations`
    0 in 18396 frames, gate open 203.478 s and resumed 203.481 s (baseline
    203.479 and 203.482).
- **B9: flaky, needs review. Observed vs spec.**
  - The spec 12.4 row "error-frame rate trip" (spec 7, trip 6). B9 is its
    wrong-bit-rate method.
  - `cfcd`: 9 bursts and 32773 witness error frames (2571-2762 a burst). The
    device read TEC max 0, REC max 135, `bus_error` +22718. **Trip 7 latched,
    "3 inhibit frames skipped within 1 s"; trip 6 did not.** FAIL.
  - Re-run 1: 9 bursts and 33520 frames (2600-2890 a burst); TEC max 68, REC
    134; trip 6 latched. PASS.
  - The baseline: trip 6, TEC 34, REC 128, 34031 frames. Earlier `e3d9437`
    runs: trip 6, TEC 93 and 94.
  - This is the first trip-7 latch in B9 on record. Spec 7 defines both trips
    and gives no order between them. Sent to the reviewer.
- **Note (tester, 2026-10-26; wording approved, user, 2026-10-25): B9's trip 6
  was never latched by the bursts on a live arm.**
  - **What every wrong-bit-rate record on file shows:** `live_before` is false,
    no page sample reads live, and the trip had already latched at the first
    page sample, about 4 s before the first burst. The records are
    `wrong_bitrate_calibrate_*.json`: 2026-10-10 on `cc34c80`, the `e3d9437`
    baseline, `0bfe` and `ec06`, and `cfcd` and `6204` on `6926fb0`.
  - **Why it read PASS:** B9's judge passed on "error-frame rate exceeded" being
    among the aborts, so a latch made before the stimulus counted.
  - **What this changes:** these B9 PASS verdicts are not evidence that the
    wrong-bit-rate bursts trip a live arm. Under the judge as changed on
    `gen-inhibit/tester/gi-page-reads` (`6081665`), each reads INCONCLUSIVE.
  - **What still stands:** the counter readings stand as readings of the
    device's counters with the arm latched: REC up to 128-135 after each burst,
    TEC, state `running`, and the witness's error frames per burst.
  - **Where trip 6 on a live arm rests instead:** the jammer cases (B5
    livetrain, B6 threshold, B7 busoff-rearm) and B10's flood arms. Their
    records show the device live with no abort just before the stimulus, no
    witness error frame before it, and the latch after it.
  - In `cfcd` (trip 7) and `6204` (trip 6) alike, the trip had latched before
    the first burst, so B9's FAIL-then-PASS says nothing about the device's
    response to the bursts. Which trip latches first under the pre-arm condition
    is recorded, not judged.
- **A Wi-Fi outage voids B3, B11raw and B15 in re-run 2** (reviewer,
  2026-10-25).
  - The PC's Wi-Fi link to the WiCAN's AP dropped from 16:33:19 to 16:40:15
    (Windows WLAN-AutoConfig log): event 11004 at 16:33:19.2; 8003
    "disconnected by the driver" at 16:33:29.2; 8001, reconnected, at
    16:40:15.9.
  - The WiCAN did not reboot: its uptime went from 884214 ms at 16:32:58 to
    1451308 ms at 16:42:25, 567.1 s over 567 s of wall clock. CAN identify
    worked through it (16:40:19).
  - The drop came within about 1 s of B3 observe arm 5's go-live POST.
  - Casualties:
    - B3's desk read ERROR (its judge raised on arm 5's empty fields);
    - every B3arms respond and passive arm exited 2;
    - all 6 B11raw arms exited 2, their captures empty, so B11raw's desk read
      ERROR, "no input files";
    - B15 FAIL, "mode 0 not restored" (its mode-3 POST timed out at 16:39).
  - Re-run 3: B3 PASS (20 arms, stale 0); B11raw PASS (3 PASS, 0 on ACK, 3
    not applicable); B15 PASS (3 adapters on the segment).
    - **Correction (tester, 2026-11-18): two adapters on CAN1, not three.**
      The count included the `gs_usb` row, which was the CAN2 Innomaker
      (today's `canchg`) reached through the X2's bridge, not an adapter on
      CAN1 and not independent. The pass rests on the Kvaser and PCAN rows.
      The verdict stands (reviewer ruling, 2026-11-18).
  - B3arms and B11rawarms read PASS in re-run 2 though every arm failed. Arms
    cases have no judge: they are rated on the script's exit code, which was
    0. Fixed in `1f6250d`.
- **NONODE: re-judged PASS** (reviewer, 2026-10-25).
  - In re-run 2, arms 1 and 3 latched trip 6, "error-frame rate exceeded", at
    0.427 and 0.420 s. Arm 2 latched trip 2, "bus lost", at 0.635 s. Every
    judged item read PASS in all three.
  - The record's verdict was FLAKY (the main arms disagree on the trip),
    which the suite reads as INCONCLUSIVE.
  - Spec 12.4 row "a bus with no other node": "Recorded, not judged: which
    trip latched (2 or 6) and when". The 0.7 s bound is met.
  - `79bada4` drops the trip comparison. `nonode_bench.py --rejudge` on the
    record gives PASS (`08ea/man_held/NONODE/nonode_bench_20261025T164839_rejudged.json`).
    The record is unchanged (`d3ada231...`).
  - 1 of 3 arms latched trip 2, against 6 of 6 latching trip 6 before (the
    `e3d9437` baseline and `cfcd`).
  - The comparison report still reads re-run 2's NONODE as INCONCLUSIVE,
    from the record.
- **Figures flagged, verdicts unchanged.** Each is given as first run /
  re-run (baseline):
  - B2: `respond_probes_x2app` 2992 / 2992 (2994); `respond_probes_x2held`
    2996 / 2999 (2992).
  - B3, first run / re-run 3, each a maximum: `first_answer_ms` observe
    46.3 / 46.8 (36.0); respond 40.5 / 40.5 (34.0); passive 46.3 / 28.7
    (28.6). Passive minimum 15.4 / 15.4 (15.8); off minimum 150.7 / 150.9
    (150.9).
  - B5: `pass_500_per_s` 9 / 9 (8); `inconclusive_500_per_s` 0 / 0 (1).
  - B11raw, first run / re-run 3: `pass_on_ack` 0 / 0 (1);
    `send_to_first_byte_ms` 7.6-19.0 / 6.1-28.7 (6.1-20.1).
  - B12: `sw_boot_s` 2.306-4.349 / 2.306-2.314 (2.314-2.353); `panic_boot_s`
    minimum 0.586 / 0.582 (0.592).
  - B15, first run / re-run 3: `frames_pcan` 20938 / 20926 (20930);
    `frames_gs_usb` 14789 / 14788 (14791).
  - B22: `response_us_max_a` 1467 / 826 (846); `_b` 918 / 1044 (814).
  - B23:
    - contend aborted 61 / 66 (57);
    - contend no effect 14 / 8 (21);
    - contend lost-then-aborted 3 / 4 (0);
    - retry aborted 71 / 74 (69);
    - retry first abort enough 72 / 74 (69);
    - retry lost-then-aborted 7 / 4 (9).
  - B26: `ge2_idle_a` 12 / 5 (15); `ge2_idle_b` 10 / 13 (17); `ge2_loaded`
    13012 / 12098 (11543); `max_backlog_idle_b` 3 / 3 (4).
- **Recorded, not judged: B15's non-ACK repeat id.** It was 0x7F8 in re-run
  3. It was 0x7F9 in the first run and in every earlier B15 on record. The
  comparison sets the first run's figures against the baseline, not a
  re-run's, so it does not show this.
- **`ota_rollback_rehearsal`'s verify phase: NOT RUN** (reviewer,
  2026-10-25). It reads `/check_status`, which is barred until the WiCAN's
  factory reset (reviewer, 2026-10-20). Moving it onto `/gen_inhibit` is next
  on this branch.
- **`/check_status` is off two suite tools** (`4db69fe`, reviewer,
  2026-10-25).
  - `e4_device_calibration.py`, behind the B2, B3, B11, B11raw and B13 arms,
    asked for `/check_status` first, to name the image. B11raw's Wi-Fi capture
    of port 80 kept the answer.
  - `wican_txabort_bench.py` (B23) asked for it before and after.
  - Both now name the image by `/gen_inhibit`'s `elf_sha256`. e4's record
    field `device` is now that hash; it was `git_version`.
  - `cfcd`'s six B11raw captures (`man_app/B11rawarms/*.pcapng`) each hold
    one `/check_status` request and its answer. They are left untouched, not
    opened or copied, pending the user (reviewer).
  - Re-run 3's six B11raw captures hold no `/check_status` request. This is a
    value-blind count of request lines, made by
    `check_status_request_count.py` on 2026-10-25.
- **Harness changes** on `6926fb0..1f6250d`, each with its own test:
  - `f0e29fb`, the man2 retirement: `test_suite_gen_inhibit` 302 ok, its
    mutants 76 of 76, `test_suite_judges`;
  - `4f3cea7`, the tester skill's cherry-pick of methodology `c4fd7d6`;
  - `9b74ba0`, `bench_held.py`:
    - `test_bench_held` 19 ok and its mutants 12 of 12;
    - `test_interlock_judge` 170;
    - `test_x2_pins_noreset_check` 40 and its mutants 25 of 25;
    - `test_gi_paths` and its mutants 15 of 15;
  - `79bada4`, NONODE's trip recorded, not judged, and `--rejudge`:
    `test_nonode_bench` 67 ok, its mutants 42 of 42;
  - `4db69fe`, the `/gen_inhibit` reads: `test_device_page_reads` 29 ok, its
    mutants 11 of 11, `test_e4_rawpost`;
  - `1f6250d`, the arms scripts exit 1 when an arm does: `test_arms_exit` 22
    ok, its mutants 11 of 11.
  - Every test was re-run at `1f6250d` after re-run 3, and all pass
    (`runs/step0c_bench_6926fb0/harness_tests/`).
  - The first saved run of `9b74ba0`'s mutants had the bench core missing
    from the Python path. Every mutant crashed on import, and none was killed.
    `9b74ba0`'s message says 12 of 12. The run that shows it is the one at
    `1f6250d`: `bench_held.py`, its tests and the two gated tools are
    unchanged since `9b74ba0`.
- **Run files:**
  - `runs/chain/gen-inhibit-bench_20261025T{132354_3d53,132810_407a,132956_cfcd,155323_6204,161806_08ea,183925_86b5}/`;
  - `runs/step0c_bench_6926fb0/` (`builds/`, `hand_ota_man/`,
    `harness_tests/`, with a README and SHA256SUMS).

**The canre pin: the harness takes canre from the public repo at
`ffbe029`, not from the reverse-it root -- tester, 2026-10-26 17:45-18:06,
harness `5d85dce`. Result: PASS. (1) The eight replays rebuilt with the pinned
canre match `golden/stimulus.sha256`, 8 of 8, headers too; the host suite and
the shim suite pass. (2) Every capture and stimulus is the same with either
canre, 27 of 27 items.** The canre-pin step, as the reviewer ordered it, with
check (2) as the user ruled it (2026-10-26, through the reviewer). No product
code and no spec section changes; this entry verifies the harness that builds
the spec 12.4 replay stimuli. Harness: `gen-inhibit/tester/canre-pin`
`0a2346b..5d85dce`.
- **The two canre versions:**
  - **Before, the reverse-it root's `canre/`** (SeaDrive; not a git checkout,
    so named by content hash, `canre_hash.py`): all 43 files as bytes
    `02f06e343493f948ce499a0608c769f62fd2f98d71bd01ebe1bc7ab303fb8a4f`; the
    37 `.py` files with LF line endings
    `1a5739564d8b840f5efce216d898d031043149621bb143509c03e25f617f1aab`. The
    madhouse-debian copy the host runs used has the same three hashes.
  - **After, the pin:** canre 0.1.0, `canbus-reveng-vtrux-coda` at
    `ffbe0291f7b817360f03f1082e07240804cf6084`, in `pins.json`, installed by
    `canre_pin.py install` (pip `--no-deps --target`) and checked on every use
    (version, PEP 610 commit and repo, every RECORD hash, no added file). The
    install: 33 files `b619f72595ab011ed3b83550b561b85a0942edd1c4d7cf8f0d601b669370a9cb`,
    the 32 `.py` files `bff371e0f8579c970dca51fecc1786ba3be428e250690749ddb1b3cd0bc0a1ad`,
    the same on both hosts.
  - They differ in CRLF line endings, the root's `.md` notes and `gui/`, and
    one comment in `canre/utils/merge.py` (`diff --strip-trailing-cr`).
- **Harness change, `5d85dce`:** `canre_pin.py` (new) and `pins.json`'s
  `canre` entry; `gi_paths.use_canre`, `from_capture.py` and
  `replay_inhibit.py` import canre through it; `gi_host_run.sh` ships it,
  gains `--canre` (install only), and `--shim` installs before it builds.
  Its tests, on NICKY-XPS: `test_canre_pin` ok, mutants 16 of 16 (its
  importer scan flags `from_capture.py` and `replay_inhibit.py` at
  `0a2346b`); `test_gi_paths` ok, mutants 25 of 25; `test_project_dir` ok,
  mutants 16 of 16; `test_gi_host_run_env` ok, mutants 10 of 10;
  `test_gi_host_run_list` ok, mutants 4 of 4; `test_x2_jam_cases_reset_mutants`
  14 of 14; the 26 bench tests that reach canre, 25 exit 0 and
  `test_only_inert` (a bench tool) stops at its arguments after its imports.
  `test_raw_http_probe_mutants` 111 of 113, every control passes; the two
  not killed ("lock ignores the loan", "lock accepts any loan") edit code no
  longer in `tools/` and predate this change (reported to the reviewer, not
  fixed here).
- **(1) The replays, on madhouse-debian** (`<madhouse-debian home>/gi_host` at `5d85dce`, through
  `gi_host_run.sh`, gcc 14.2):
  - `--canre`: installed and checked at `<madhouse-debian home>/gi_host/scratch/canre/ffbe029...`.
  - `replay_stimulus_delta.py --regen`: all eight rebuilt with the pinned
    canre, each "BEHAVIOUR: identical to the golden" (frames, first and last
    us, order, per-id counts).
  - Stimulus sha256 against `golden/stimulus.sha256`: 8 of 8 MATCH, header
    sha256 the same for all 8 (`3b_stimulus_vs_pin.log`). A first check,
    `3_stimulus_vs_pin.log`, is VOID: my checker looked the pins up without
    `.scn` and read none, so it reported 0 of 8. Fixed, and the regen and the
    check run again (2b, 3b).
  - Host suite: 80 passed, 0 failed, 23 without goldens, no stimulus
    mismatch.
  - Shim suite (`--shim`): the long-arm fixture rebuilt with the pinned
    canre (706,283 frames); all shim cases pass.
- **(2) Every capture and stimulus, both ways,** on madhouse-debian
  (`accept2_remote.py`; each item in its own interpreter, once with the
  harness at `0a2346b` and canre from the root, once at `5d85dce` and canre
  from the pin):
  - **(a) Frames**, compared frame by frame (timestamp, id, data, channel,
    and `is_extended`, `dlc`, `is_tx`) by a digest over every frame: the ten
    `pins.json` and replay source logs and the long-arm source
    `runs/scottsvalley_armable_300s.log`, 11 of 11 the same (144,319 to
    24,209,199 frames each). Each run recorded the canre it loaded.
  - **(b) Stimuli:** `lowid_flood_skip_bench` and `wican_trip_bench`'s
    interlock slice (5,445 rows each; lowid also equal to its stored cache,
    wican_trip has none on madhouse), `wican_crank_request_bench`'s 2,000
    payloads, `bench_link_check`'s 600 0x051 payloads and `replay_inhibit`'s
    6,424; `cut_replay_window` (Scotts Valley, 1868.34 s, 300 s) and
    `keyon_replay_prep` (channel 3, 7.741 s), each also byte-equal to the
    stored file the runs used; `from_capture.py` for the eight replays and
    the long-arm scenario, each file byte-equal both ways (their stimulus
    hashes also equal the eight pins). 16 of 16 the same, every tool rc 0.
- **Run files:** `runs/canre_pin_5d85dce/` (`README.md`, the scripts,
  `accept1/`, `accept2/`; `SHA256SUMS`
  `3198971675c7d53b0184cb89542231e26bf70019623c5ca2c310876d303062b5`).

**Port step 1a's acceptance: the spec shim, the emulated suite and part B on
`bc7d086` -- tester, 2026-10-29 14:25 to 21:49, on `gen-inhibit/main`
`bc7d086` (product code `7ad8c14`'s). Result: every verdict matches the
`e3d9437` baseline but B6's and B22's, which read INCONCLUSIVE: in each, an
arm judged nothing (observed below; no arm failed). B22, re-judged from the
records under the provenance-only rule (gen-inhibit reviewer, 2026-10-29):
PASS in all three runs (note below). B9, known anomaly 6:
PASS on the fixed tool, on a live arm, after a timestamp defect in the tool
was fixed.** The first two runs' B6 FAIL, B22 FAIL and B9 INCONCLUSIVE were
harness defects, fixed in `8829425` and re-run. The reviewer's acceptance
(2026-10-29): the full suite at the baseline's results, with B9 re-run on the
fixed tool (known anomaly 6). Spec 3.2, 3.3 and part B as a whole, the
hardware half the step 1a C entry left to this suite; the 12.4 rows "0x7F3 carries the build id" and "a
bus with no other node, emulated". Harness: `gen-inhibit/tester/wican-move`
`bc7d086..16ccecb`.
- **Build:** the images and the release stand-in in 12.3's entry "Port step
  1a, the rest".
- **Spec shim: PASS** (`runs/step1a_bc7d086/spec_shim/`, madhouse-debian, gcc
  14.2.0, from a bundle of `bc7d086`; `main/` tree `27805e64592d`, `bc7d086`'s,
  nothing modified; `vtrux/wican`'s headers from `git archive` of
  `2026.10.29`, through `WICANDIR`). Cases `answer_stuck`, `before`,
  `bench_262`, `bench_274`, `build_id`, `busoff_few`, `busoff_many`, `fast`,
  `layer`, `layer_bench`, `slow`, `tie`: every row PASS. `build_id`: the rule
  and the hand calculation agree (`fff00f00`), 3 0x7F3 frames on the wire, 0
  differ, schema 8 in B6, `diag_build_id` `000ff0ff`, the page's `elf_sha256`
  the descriptor's.
- **Emulated suite** (`runs/chain/`):
  - `gen-inhibit-emulated_20261029T151021_8e4f`: H FAIL, a harness defect:
    `baseline_host_run.sh` shipped neither `canre_pin.py` (every replay regen
    and the long-arm fixture failed on its import) nor `vtrux/wican`'s
    headers (the shims found no `2026.10.29` checkout). B15host PASS, C2scan
    PASS. Fixed in `9738f8d`.
  - `_154641_7a07`, harness `9738f8d`, clean: PASS 3 (H, 25 steps, 80
    goldens passed, 0 failed, 23 without goldens; B15host; C2scan 3 of 3),
    NOT-RUN 2 (C1C7 and C3, by design).
- **Boards.** A hand take (15:11:55) and `bench_flash.py --flash` from
  `6926fb0` `_man` (`3639f8b2...`) to `bc7d086` `_man`: TOOK, release
  reboot PASS, released 15:14:53 (`runs/step1a_bc7d086/hand_ota_man/`). In
  each run, the runner's identify read each board before every group (each
  run's `progress.jsonl`): WiCAN `_man` `71f70ab2...` and each group's OTA
  image; X2 `756d6714`, held, the jammer (custody dump taken) or `slcan`.
  Every OTA took at its first attempt, in every run. The prod group had no
  OTA (prod = `_man`'s hash). Each run's lock was held by its runner.
- **Runs** (`runs/chain/`):
  - `gen-inhibit-bench_20261029T160025_9d31` and its automatic re-run
    `_160129_387c`: **refused at start.** The X2 identify raised on the run
    settings' key `interposer_dbc`: the Python path's interposer part was a
    copy whose identify still read that key. Nothing flashed, the X2 not
    touched (pins read only); the start and end hooks rebooted the WiCAN,
    PASS. NOT-RUN 35 and 34. Re-launched with the interposer tester's
    worktree on the path, as at step 0c.
  - `_160508_9bf7`, 16:05-18:30, every group (`bench_compare.py chain`),
    harness `9738f8d`: PASS 36, FAIL 1 (B6), INCONCLUSIVE 1 (B9), NOT-RUN 1
    (B27, the declared skip).
  - `_183033_8e53`, 18:30-20:22, the chain's automatic re-run of the 12
    flagged checks (17 cases with their arms), harness `9738f8d`: PASS 17,
    FAIL 2 (B6, B22), INCONCLUSIVE 1 (B9).
  - `_211324_880c`, 21:13-21:49, a subset run of B9, the jammer group's
    `B5_B8` (B5-B8) and B22, harness `8829425`: PASS 4 (B9, B5, B7, B8),
    INCONCLUSIVE 2 (B6, B22). No OTA; the X2 held, the jammer (custody dump
    taken) and `slcan`, restored to `756d6714`.
  - **The comparison:** `9bf7/compare_report.txt` (`bench_compare.py
    report`, all three runs; flagged 12 of 39), beside the chain's own
    `compare_index.txt` (the first two). It reads each run's verdicts as the
    run recorded them, so `9bf7`'s and `8e53`'s B6 and B22 show as the
    judges then gave them; the re-judged verdicts are below.
- **Expected differences from the baseline,** as at step 0c: the `man2` group
  retired, B17a's other image `_rxq`, B17b in the rxq group, and the prod
  group with no OTA. **Identity rows, labelled, not compared:** B17a page
  `elf_sha256` `71f70ab2` (baseline `25f50ac0`), B17b `9939e408`
  (`f9de9de9`), B19page `71f70ab2` (`f3957789`), B18 `diag_build_id`
  `71f70ab2` (baseline schema 7 `diag_git_hash` `0x2FE85674`). **"0x7F3
  carries the build id", bench half (B18): PASS.**
- **B6 FAIL in `9bf7` and `8e53`: a judge defect; fixed in `8829425`.**
  - No arm failed. 1 of 10 arms in `9bf7` (`n10_lead5` arm 1, 9 pulses) and
    2 of 10 in `8e53` (`n10_lead1.5` arm 0, 9 pulses; `n9_lead5` arm 0, 8
    pulses) read INCONCLUSIVE: "delivery was not one error frame per pulse",
    the jammer delivered fewer pulses than the arm needs, so it judges
    nothing. The other arms PASS.
  - The suite's B6 judge rated any arm that was not PASS as FAIL. It now
    reads INCONCLUSIVE arms as INCONCLUSIVE, and FAIL or any other word as
    FAIL.
  - Re-judged on the records: `9bf7` INCONCLUSIVE (9 of 10 PASS, 1
    INCONCLUSIVE), `8e53` INCONCLUSIVE (8 of 10, 2).
  - **`880c`: INCONCLUSIVE, observed vs spec.** 9 of 10 arms PASS;
    `n9_lead1.5` arm 1 INCONCLUSIVE, the same way (8 pulses delivered of 9,
    one error frame and one `bus_error` each). So B6 has read INCONCLUSIVE in
    all three runs at `bc7d086`, from 1 or 2 short arms in 10, against the
    baseline's 10 of 10. No arm on record at `bc7d086` failed. The spec 12.4
    row "error-frame rate trip" (B6, the threshold) is not shown met or
    unmet by the arms that judged nothing; the 26 that did judge all PASS.
- **B22 FAIL in `8e53`: a judge defect; fixed in `8829425`.**
  - Arm b was voided by its tool: two counts of the witness's frames
    disagreed by one (the older classifier: VCM 8999, the device's 8982,
    against the device's `tx_ok` 8983; the total, 17981, the same both
    ways). The tool's live provenance check, which reads each frame the
    device sent: as specified, 8983 of 8983 found.
  - The judge read the missing "wire agrees" line as "b: a line is missing",
    FAIL. A voided arm now reads INCONCLUSIVE, unless what it does show fails
    (provenance, `tx_fail`, counter, queued or error-frame lines), which is
    still FAIL.
  - Re-judged: `9bf7` PASS, `8e53` INCONCLUSIVE.
  - **`880c`: INCONCLUSIVE, observed vs spec.** Arm a voided the same way:
    `tx_ok` 8983, the older classifier attributing 8982 to the device; the
    live provenance check as specified, 212124 of 212124 replayed frames
    found, the device's 0x051 8983 (the device says 8983), late 0, double 0,
    ambiguous 0; `tx_fail` 0, `ctr_bad` 0, 0 error frames. Arm b: the wire
    agrees, 8983 sent and 8983 seen; provenance as specified. The count that
    voids the arm is the one the tool's own output labels "old trail-window
    classifier (SUPERSEDED)"; the live provenance count agrees with the device
    in every voided arm (2 of 6 arms at `bc7d086`).
  - **Note, 2026-10-30: re-judged under the provenance-only rule, B22 reads
    PASS in `9bf7`, `8e53` and `880c`.** The gen-inhibit reviewer's ruling
    (2026-10-29, after the tracker row "E4 answer classifier", 2026-10-10):
    B22's tool voids an arm on the provenance check only; the old trail-window
    count may still be printed, labelled, but voids no arm and decides no
    verdict. The tool and the judge are changed in `a820269`. Re-judged from
    the records, with no bench re-run: in `8e53` arm b and `880c` arm a the
    only void reason was that count's disagreement (8982 against `tx_ok`
    8983), and each record holds what the rule reads: provenance as specified
    (212124 of 212124 replayed frames found, the device's 0x051 8983, the
    device says 8983, late 0, double 0, ambiguous 0), `tx_fail` 0, `ctr_bad`
    0, `tx_queued_behind` 0, 0 error frames. `suite_judges.py --only B22` at
    `8d7fb91` on each run's `man_slcan/B22/`: PASS, both arms, `ours` (now
    provenance's device count) 8983 in every arm, `response_us_max` 1041 /
    747 (`9bf7`), 1018 / 867 (`8e53`), 804 / 1245 (`880c`), carried load at
    least 92.5 % (`runs/step1a_bc7d086/b22_rejudge/`). So B22 at `bc7d086`
    is PASS on all three runs, as the baseline. The tests of `a820269`
    (`test_suite_judges` 199 ok, 3 of the new edits failing on the judge
    before; its mutants 55 of 55; `test_e4_stimulus_split` 6 new checks;
    `test_e4_attribution_mutants` 4 of 4, the control passing) and of
    `8d7fb91` (the judge mutant runner's control: it passes, 55 of 55; with
    `GI_BASELINE_DIR` an empty folder it FAILS, exit 1, where the runner
    before read every mutant killed, exit 0) are in
    `runs/step1a_bc7d086/harness_tests/b22_provenance/`.
- **B9 (known anomaly 6) INCONCLUSIVE in `9bf7` and `8e53`: a timestamp
  defect in the tool; fixed in `8829425`.**
  - The 12.4 row "error-frame rate trip" (spec 7, trip 6), wrong-bit-rate
    method, on the tool as fixed in `296b0f9`: the arm is live before the
    first burst (`live_before` true; clean live samples at -0.205 and +0.001
    s in `9bf7`, -0.183 and +0.010 s in `8e53`), and trip 6, "error-frame
    rate exceeded", latched at +0.200 and +0.206 s. 9 bursts each, 24596 and
    25572 witness error frames (2577-2966 and 2611-3349 a burst), TEC max 0
    (baseline 34, but the baseline's latch came before the stimulus: see the
    step 0c note).
  - The judge did not credit the latch: the window from the last clean live
    sample to the first burst held 36 and 43 witness error frames. All of
    them fall within 5 ms before the burst's timestamp.
  - The tool took that timestamp after its Kvaser's channel had opened at
    250 kbit on the 500 kbit segment; the open ends with the controller going
    bus-on, and the witness's error frames begin there. The burst now starts
    when the open begins, and each burst records the open's time (`open_s`).
  - Re-judged on the records: INCONCLUSIVE both (the timestamp comes from the
    tool, so the records can't change).
  - **`880c`, on `8829425`: PASS.** The arm live before the first burst
    (`live_before` true, the driver running before the slice); the last
    clean live sample at -0.173 s, **0 witness error frames from it to the
    first burst** (and 0 before the first burst); trip 6, "error-frame rate
    exceeded", latched at +0.23 s, the first latch. 9 bursts (1, 5 and 20 ms,
    three each), 24639 witness error frames, 2564-3110 a burst; each open
    took 0.017-0.036 s (`open_s`); TEC max 6. **This is the first
    wrong-bit-rate record on file whose latch the judge credits to the
    bursts on a live arm.** Every record before `9bf7` had latched before
    the stimulus (the step 0c note); `9bf7` and `8e53` were live, but with
    the bus-on's error frames counted before the burst. Known anomaly 6's
    re-run on the fixed tool.
- **Figures flagged, verdicts unchanged.** Each is given as `9bf7` /
  `8e53` (baseline):
  - B2: `respond_probes_x2app` 2991 / 2992 (2994); `respond_probes_x2held`
    2991 / 2988 (2992).
  - B3, `first_answer_ms`: observe min 15.3 / 15.4 (15.4); off min 150.6 /
    150.8 (150.9); passive max 53.9 / 28.4 (28.6), min 15.3 / 15.3 (15.8);
    respond max 33.9 / 60.4 (34.0).
  - B11raw: `pass_on_ack` 0 / 0 (1); `send_to_first_byte_ms` 4.2-24.9 /
    4.2-27.1 (6.1-20.1).
  - B12: `panic_boot_s` 0.583-0.585 / 0.583-0.593 (0.592-0.599);
    `sw_boot_s` min 2.305 / 2.295 (2.314).
  - NONODE: `latch_max_s` 0.453 / 0.668 (0.49).
  - B15: `frames_pcan` 20934 / 21114 (20930); `frames_gs_usb` 14789 / 14788
    (14791).
    - **Correction (tester, 2026-11-18): `frames_gs_usb` is the CAN2
      Innomaker** (today's `canchg`), reached through the X2's bridge, not
      an adapter on CAN1, so it does not count as independent. B15's pass
      rests on its Kvaser and PCAN rows. The verdict stands (reviewer ruling, 2026-11-18).
  - B22: `response_us_max_a` 1041 / 1018 (846); `_b` 747 / - (814), 1245
    in `880c`.
  - B23: contend aborted 68 / 65 (57); contend no effect 9 / 12 (21);
    contend lost-then-aborted 1 / 1 (0); retry aborted 72 / 72 (69); retry
    first abort enough 72 / 71 (69); retry lost-then-aborted 6 / 6 (9).
  - B25: `gate_open_s` 203.478 / 203.479 (203.479); `resumed_s` 203.481 /
    203.482 (203.482).
  - B26: `ge2_idle_a` 10 / 15 (15); `ge2_idle_b` 12 / 15 (17); `ge2_loaded`
    11856 / 14399 (11543); `max_backlog_idle_a` 4 / 4 (3); `_idle_b` 3 / 4
    (4); `_loaded` 9 / 8 (8).
  - B9: `witness_error_frames` 24596 / 25572 / 24639 in `880c` (34031);
    `tec_max` 0 / 0 / 6 (34).
- **Harness changes** on `bc7d086..16ccecb`, each with its own test, all
  re-run at the tip (`runs/step1a_bc7d086/harness_tests/`):
  - `9738f8d`, `baseline_host_run.sh` ships `canre_pin.py` (and installs the
    pinned canre before the replays) and `vtrux/wican` at the tag the
    commit pins (`WICANDIR`): `test_baseline_host_run.py` 10 new checks, 9
    failing on the script before; `--mutants` 30 of 30.
  - `8829425`, `suite_judges.py` B6 and B22: `test_suite_judges.py` 194 ok
    (the judges before fail edits 17, B6, and 66, B22); its mutants 53 of
    53. `wrong_bitrate_errors.py`'s burst start and `open_s`,
    `trip6_rearm_check.py` records `burst_open_s`:
    `test_wrong_bitrate_sequence.py` 22 ok (4 fail on the tool before), its
    mutants 11 of 11, the control passing; `test_trip6_rearm_check` PASS.
  - `16ccecb`, `test_baseline_host_run.py --mutants` runs the unmutated copy
    first as the control, and judges no mutant when it fails. Found
    re-running the tests for this entry: from a PowerShell with ESP-IDF
    exported, bash on the PATH was WSL's, all 40 checks failed, and all 30
    mutants read killed, exit 0. Its test is both environments: from Git
    Bash 42 ok, the control passing, 30 of 30; on the WSL-bash PATH the
    control FAILS, no mutant judged, exit 1 (`harness_tests/env_wsl_bash/`).
- **Run files:**
  - `runs/chain/gen-inhibit-emulated_20261029T{151021_8e4f,154641_7a07}/`;
  - `runs/chain/gen-inhibit-bench_20261029T{160025_9d31,160129_387c,160508_9bf7,183033_8e53,211324_880c}/`;
  - `runs/step1a_bc7d086/` (`README.md`; `SHA256SUMS` `9a97ecf4...f811`
    hashes every file: `builds/`, `build_checks/`, `hand_ota_man/`,
    `spec_shim/`, `spec_shim_console.txt`, `harness_tests/`).

**Port step 1a2's acceptance: the spec shim, the emulated suite and part B on
`17ec04c` -- tester, 2026-10-30 14:52 to 19:41, on `gen-inhibit/main`
`17ec04c`. Result: the first bench run matches the `e3d9437` baseline in
every verdict, B6 10 of 10 arms PASS. The chain's automatic re-run of the
flagged checks reads B6 FAIL, observed vs spec below: one arm latched
trip 6 with at most 9 error frames in any window. B9 PASS on a live arm and
B22 PASS on provenance in both runs. R28 PASS on a real release build (12.3,
"Port step 1a2"). The OTA rollback rehearsal: NOT RUN.** The reviewer's
acceptance (2026-10-30): the full suite, R28 PASS on a real release build,
and the rehearsal NOT RUN (console rule), recorded as such, with known
anomalies carried as before (anomaly 7, B6 short arms; anomaly 5, R28,
should close here). Spec 3.2, 3.3 and part B as a whole; the 12.4 rows
"0x7F3 carries the build id" and "a bus with no other node, emulated".
Harness: `17ec04c`'s (no harness change since the merge).
- **Build:** the images and the release stand-in in 12.3's entry "Port step
  1a2".
- **Spec shim: PASS** (`runs/step1a2_17ec04c/spec_shim/`, madhouse-debian,
  gcc 14.2.0, from a bundle of `17ec04c`; `main/` tree `55d4b58a0b82`,
  `17ec04c`'s, nothing modified; `vtrux/wican`'s headers from `git archive`
  of `2026.10.30`, through `WICANDIR`). Cases `answer_stuck`, `before`,
  `bench_262`, `bench_274`, `build_id`, `busoff_few`, `busoff_many`, `fast`,
  `layer`, `layer_bench`, `slow`, `tie`: every row PASS. `build_id`: the rule
  and the hand calculation agree (`fff00f00`), 3 0x7F3 frames on the wire, 0
  differ, schema 8 in B6, `diag_build_id` `000ff0ff`, the page's `elf_sha256`
  the descriptor's.
- **Emulated suite:** `runs/chain/gen-inhibit-emulated_20261030T145429_cbfb`,
  code `17ec04c`, clean: PASS 3 (H, 25 steps, 80 goldens passed, 0 failed,
  23 without goldens; B15host; C2scan 3 of 3), NOT-RUN 2 (C1C7 and C3, by
  design). The same as at step 1a.
- **Boards.** A hand take (15:14:34) and `bench_flash.py --flash` from
  `bc7d086` `_man` (`71f70ab2...`) to `17ec04c` `_man` (`1a07454e...`):
  TOOK, release reboot PASS, released 15:17:59
  (`runs/step1a2_17ec04c/hand_ota_man/`). In each run, the runner's identify
  read each board before every group (each run's `progress.jsonl`): WiCAN
  `_man` `1a07454e...` and each group's OTA image; X2 `756d6714`, held, the
  jammer (custody dump taken) or `slcan`. Each run's lock was held by its
  runner. The prod group had no OTA (prod = `_man`'s hash).
  - Every OTA in `fb52` took at its first attempt.
  - In `c68c`, the OTA to `_rxq` read "(a1) NO STATUS, NO RESTART" at its
    first attempt. The runner restored `_txabort` (TOOK), and the second
    attempt took (19:01:16). Every other OTA took at its first attempt.
- **Runs** (`runs/chain/`):
  - `gen-inhibit-bench_20261030T151900_fb52`, 15:19-17:45, every group
    (`bench_compare.py chain`): PASS 38, NOT-RUN 1 (B27, the declared skip).
    The `e3d9437` baseline's verdicts exactly.
  - `_174526_c68c`, 17:45-19:41, the chain's automatic re-run of the 12
    flagged checks (17 cases with their arms): PASS 19, FAIL 1 (B6). The
    runner's end hook passed, and it released the lock at 19:41:36.
  - **The comparison:** `fb52/compare_report.txt` (`bench_compare.py
    report`, both runs; flagged 13 of 39), beside the chain's own
    `compare_index.txt`. B6: "flaky, needs review: PASS first, FAIL on
    re-run".
- **Expected differences from the baseline,** as at step 1a: the `man2`
  group retired, B17a's other image `_rxq`, B17b in the rxq group, and the
  prod group with no OTA. **Identity rows, labelled, not compared:** B17a
  page `elf_sha256` `1a07454e` (baseline `25f50ac0`), B17b `ee9b9132`
  (`f9de9de9`), B19page `1a07454e` (`f3957789`), B18 `diag_build_id`
  `1a07454e` (baseline schema 7 `diag_git_hash` `0x2FE85674`). **"0x7F3
  carries the build id", bench half (B18): PASS.**
- **B6 (spec 7, trip 6; the 12.4 row "error-frame rate trip", the
  threshold): `fb52` PASS, `c68c` FAIL.**
  - `fb52`: 10 of 10 arms PASS over 5 runs. No arm was short.
  - `c68c`: 8 of 10 arms PASS. `n9_lead5` arm 1 INCONCLUSIVE: "delivery was
    not one error frame per pulse", 8 pulses delivered of 9 (known anomaly
    7, short arms, as at step 1a).
  - **`c68c` `n9_lead1.5` arm 1: FAIL, observed vs spec.**
    - Observed: the witness saw one error frame per pulse, 9 pulses (per
      pulse `[1, 1, 1, 1, 1, 1, 1, 1, 1]`, at most 9 in any window). The
      device's `bus_error` deltas were `[1, 1, 1, 1, 1, 1, 11, 1, 1]`, and
      trip 6, "error-frame rate exceeded", latched 6.608 s after the first
      pulse.
    - Spec 7, trip 6: 10 or more error frames within a 10 s window. The
      wire never carried more than 9 in a window.
  - Where the counted error frames fell
    (`runs/step1a2_17ec04c/b6/`, `17ec04c`'s `b6_multicount_landing.py`
    and `b6_class_count.py` over both runs' 10 threshold records, 192
    pulses): 190 pulses counted once, 1 had no delta or no error frame, and
    1 counted more than once. That one was the failing arm's pulse 7
    (`x2_jam_threshold_20261030T192401`), delta 11. The frame before it on
    the wire was a 0x051 1280 us earlier, the frame after it a 0x051 10150
    us later; TEC/REC max 0/5.
  - Precedent on file: the `e3d9437` acceptance's B6 read "flaky, needs
    review" with the same shape (one pulse counted 11 times, in an `n10`
    arm), and the open item "One pulse counted 10-12 times" (spec 7, trip
    6, above) is not concluded. Whether this FAIL is carried with them is
    for the reviewer.
- **B9 (known anomaly 6; spec 7, trip 6, wrong-bit-rate method): PASS in
  both runs, on a live arm.** The driver running before the slice, 0
  witness error frames before the first burst and since the last clean live
  sample (-0.165 s in `fb52`, -0.173 s in `c68c`). Trip 6, "error-frame
  rate exceeded", latched at +0.25 and +0.23 s. 9 bursts (1, 5 and 20 ms,
  three each), 25150 and 25345 witness error frames (2622-3210 and
  2690-3120 a burst), TEC max 0.
- **B22: PASS in both runs, both arms,** on provenance: as specified,
  212124 of 212124 replayed frames found, the device's 0x051 8983 (the
  device says 8983), late 0, double 0, ambiguous 0; `tx_fail` 0, `ctr_bad`
  0, `tx_queued_behind` 0, 0 error frames.
- **The OTA rollback rehearsal: NOT RUN.** It needs a WiCAN console capture,
  and none is taken until the WiCAN's factory reset (console rule). Its
  harness at R28's levels is tested on the host (12.3, "Port step 1a2").
- **Figures flagged, verdicts unchanged.** Each is given as `fb52` / `c68c`
  (baseline):
  - B2: `respond_probes_x2app` 2991 / 2984 (2994); `respond_probes_x2held`
    2989 / 2986 (2992).
  - B3, `first_answer_ms`: off min 149.6 / 149.3 (150.9); passive max 55.5 /
    28.4 (28.6), min 15.4 / 15.4 (15.8); respond max 40.4 / 33.9 (34.0).
  - B5: 500/s `pass` 7 / 9 (8), `inconclusive` 2 / 0 (1).
  - B9: `burst_errors_max` 3210 / 3120 (3087); `witness_error_frames` 25150
    / 25345 (34031); `tec_max` 0 / 0 (34).
  - B11raw: `pass_on_ack` 0 / 0 (1); `send_to_first_byte_ms` 4.2-21.1 /
    6.1-18.8 (6.1-20.1).
  - B12: `panic_boot_s` 0.584-0.591 / 0.583-0.595 (0.592-0.599);
    `sw_boot_s` min 2.311 / 2.303 (2.314).
  - NONODE: `latch_max_s` 0.452 / 0.455 (0.49).
  - B15: `frames_pcan` 20934 / 21906 (20930); `frames_gs_usb` 14789 / 15420
    (14791); `non_ack_repeat_id` 0x7F8 / 0x7F1 (0x7F9).
    - **Correction (tester, 2026-11-18): `frames_gs_usb` is the CAN2
      Innomaker** (today's `canchg`), reached through the X2's bridge, not
      an adapter on CAN1, so it does not count as independent. B15's pass
      rests on its Kvaser and PCAN rows. The verdict stands (reviewer ruling, 2026-11-18).
  - B22: `load_pct_lower_a` 92.3 / 92.5 (92.5); `_b` 92.4 / 92.5 (92.5);
    `response_us_max_a` 1272 / 1080 (846); `_b` 883 / 934 (814).
  - B23: contend aborted 62 / 72 (57); contend no effect 12 / 6 (21);
    contend lost-then-aborted 3 / 0 (0); retry first abort enough 70 / 70
    (69).
  - B25: `gate_open_s` 203.478 / 203.478 (203.479); `resumed_s` 203.481 /
    203.481 (203.482).
  - B26: `ge2_idle_a` 19 / 16 (15); `ge2_idle_b` 9 / 15 (17); `ge2_loaded`
    13206 / 13017 (11543); `max_backlog_idle_a` 4 / 5 (3).
- **Run files:**
  - `runs/chain/gen-inhibit-emulated_20261030T145429_cbfb/`;
  - `runs/chain/gen-inhibit-bench_20261030T{151900_fb52,174526_c68c}/`;
  - `runs/step1a2_17ec04c/` (`README.md`; `SHA256SUMS` `99db7428...4452` hashes every
    file: `builds/`, `build_checks/`, `hand_ota_man/`, `spec_shim/`,
    `spec_shim_console.txt`, `b6/`, `r28_fault_rows/`, `harness_tests/`).

**Port step 1b's acceptance: the spec shim on twai_port's host fake, the
emulated suite, `gi_host_run` and part B on `488ef9f` -- tester,
2026-10-31 19:19 to 2026-11-01 03:55, on `gen-inhibit/main` `488ef9f`.
Result: the first bench run matches the `e3d9437` baseline in every verdict
except B6, which reads INCONCLUSIVE on a short arm (known anomaly 7). The
chain's automatic re-run reads B6 10 of 10 PASS. It also reads B16 FAIL on
one stalled poll (observed below) and seven FAIL or ERROR results whose
requests never reached the device: NICKY-XPS's Wi-Fi link to the WiCAN was
logged down while they ran. The OTA rollback rehearsal: NOT RUN.** The
reviewer's 1b acceptance (2026-10-31): the spec shim ported onto the
twai_port fake, the full suite against the `e3d9437` baseline's verdicts,
B6 re-run (anomaly 7), and `gi_host_run`'s first madhouse sync with
`replay_stimulus_delta --regen`. Spec 3.2, 3.3 and part B as a whole; the
12.4 rows "0x7F3 carries the build id" and "a bus with no other node,
emulated".
- **Harness for 1b** (`gen-inhibit/tester/accept-1b`, `488ef9f..8bc82f1`,
  not yet merged):
  - `3ad41bf`: the spec shim's layer runs on twai_port's host fake.
  - `1d4cfdd`: the shim README's `test_host_env` row.
  - `226ab66` and `7ff1ed6`: `baseline_host_run` ships `can_port`,
    `twai_port` and `gi_paths.py` for a 1b commit.
  - `8bc82f1`: `gi_host_run`'s sync removes what it replaces.

  Each change came with its own test (below).
- **Build:** the images and the release stand-in in 12.3's entry "Port step
  1b".
- **Spec shim: PASS** (`runs/step1b_488ef9f/spec_shim/`, madhouse-debian,
  gcc 14.2.0, from a clone of `226ab66`; `shared-2026.10.31.2` at
  `1fda69d`, recorded as pinned, nothing modified). 90 PASS lines: 83 case
  lines and 7 build id. No FAIL or VOID. Every case line is identical to
  1a2's (`answer_stuck`, `before`, `bench_262`, `bench_274`, `build_id`,
  `busoff_few`, `busoff_many`, `fast`, `layer`, `layer_bench`, `slow`,
  `tie`). `mutate.py` killed 51 of 51 mutants. One new mutant, a pending alert
  waiting for the driver, is not observable and was dropped; the shim's
  `mutate.py` says so. `test_host_env --mutants`: 10 of 10 killed, the
  control passing (`spec_shim_console.txt` l.160-570).
- **Emulated suite:**
  - `runs/chain/gen-inhibit-emulated_20261031T201452_f825` FAILED on H from
    a harness defect: `baseline_host_run.sh` shipped `canre_pin.py` without
    `gi_paths.py`. Fixed in `7ff1ed6`; not a product result.
  - The rerun `gen-inhibit-emulated_20261031T203000_5173`, code `488ef9f`,
    clean, harness `7ff1ed6`: PASS 3, NOT-RUN 2 (C1C7 and C3, by design).
    - H: 25 steps exit 0; 81 goldens passed, 0 failed, 23 without goldens;
      9 replays identical.
    - B15host (`modes_with_pages` 2) and C2scan (3 of 3) passed.
    - Against 1a2, goldens went from 80 to 81 and replays from 8 to 9:
      `83239a8` added the replay-bench-frame-loss golden on main between
      `17ec04c` and `488ef9f`. The rest is identical.
- **`gi_host_run` on madhouse-debian: PASS** (`runs/step1b_488ef9f/gi_host/`,
  from `8bc82f1`, code `488ef9f`):
  - All 8 steps exited 0.
  - The regen (`replay_stimulus_delta --regen`): 9 replays identical to the
    golden, every delta +0.
  - Suite: 81 passed, 0 failed, 23 without goldens.
  - Checks: invariants 104/0, passive_diff 100/0 with 4 skipped, fuzz
    200/0, load_margin PASS (12.6x), spec_expect 22/22, bus_on 2/2, drained
    1/1, cfg_build_id PASS.
  - Sched and the shim: all cases pass.
  - The first run (`gi_host_run1_stale_sync/`, harness `7ff1ed6`) failed
    the shim step: the sync only added files, so `<madhouse-debian home>/gi_host` kept pre-1b
    files, including a stale `twai.h`. That was a harness defect, fixed in
    `8bc82f1`.
- **Harness tests at `8bc82f1`** (`runs/step1b_488ef9f/harness_tests/`): 80
  tests, 70 exit 0. The 10 others:
  - 7 failed from my driver's environment (WSL's bare `bash`, no `cygpath`,
    no `INTERPOSER_CODE`, no `GI_CHAIN_SETTINGS`). Rerun with them set
    (`harness_tests/rerun_env/`), all 10 runs exit 0:
    - `test_suite_gen_inhibit` 302 ok; its mutants 76 of 76, the control
      passing;
    - `bench_image_lookup` 6 of 6, `host_identity` 6 of 6, `b6_ecc_run` 20
      of 20;
    - `runs_dir` and `chain_settings_chip` PASS.
  - `test_refusal_pairs`: NOT RUN. It is the proof over a set of switch
    images, and the driver gave it none, as at 1a2.
  - `test_scratch_only --mutants` killed 3 of 5. This is a known follow-up,
    not a 1b result.
  - `test_host_env --mutants` killed 5 of 10 on Windows, where its
    real-shell checks don't run. The counted run is madhouse's 10 of 10
    (above).

  The 1b changes' own tests:
  - `test_baseline_host_run`: 57 checks; mutants 37 of 37, the control
    passing.
  - `test_gi_host_run_env`: mutants 18 of 18. `test_gi_host_run_list` all
    ok.
- **Boards.** A hand take (2026-11-01 00:31:37 PDT) and `bench_flash.py
  --flash` from `17ec04c` `_man` (`1a07454e...`) to `488ef9f` `_man`
  (`cbfc683a...`): TOOK in 43 reads. The release reboot PASS. Released
  00:34:36 (`runs/step1b_488ef9f/hand_ota_man/`). In each run, the runner's
  identify read each board before every group (each run's
  `progress.jsonl`): WiCAN `_man` `cbfc683a...` and each group's OTA image;
  X2 `756d6714`, held, the jammer (custody dump taken) or `slcan`. Each
  run's lock was held by its runner. Every OTA in both runs took at its
  first attempt.
- **Runs** (`runs/chain/`). The clocks fell back at 02:00 PDT during
  `7bc9`; `progress.jsonl` gives every time with its offset.
  - `gen-inhibit-bench_20261101T003528_1eb9` and its re-run `_003618_9fe5`
    were refused at the start, all NOT-RUN, nothing flashed. The X2
    identify step refused settings that carried `interposer_dbc`, a key
    retired on 2026-10-24. That was a settings defect, mine; the key was
    deleted and the chain started again.
  - `gen-inhibit-bench_20261101T003858_7bc9`, 00:38 PDT to 02:04 PST,
    every group (`bench_compare.py chain`): PASS 37, INCONCLUSIVE 1 (B6),
    NOT-RUN 1 (B27, the declared skip). Against the `e3d9437` baseline
    (PASS 38, NOT-RUN 1), only B6 differs.
  - `_020455_c6a0`, 02:04-03:55 PST, the chain's automatic re-run of the 13
    flagged checks (18 cases with their arms): PASS 15, FAIL 3, ERROR 4. The
    runner's end hook passed, and it released the lock at 03:55:26.
  - **The comparison:** `7bc9/compare_report.txt` (both runs; flagged 14 of
    39). "flaky, needs review": B6 (INCONCLUSIVE first, PASS on re-run);
    B2, B9 and NONODE (PASS first, ERROR on re-run); B16 and B22 (PASS
    first, FAIL on re-run).
- **The re-run's Wi-Fi drops: seven results that never reached the
  device.**
  - Windows' WLAN-AutoConfig log (event 8003, reason code 0) recorded
    NICKY-XPS's Wi-Fi link to the WiCAN's AP as "disconnected by the
    driver" twice:
    - 02:58:06, reconnected 02:59:57;
    - 03:48:37, reconnected 03:52:57.

    Every other disconnect in both runs was a rejoin by the harness
    (reason code 3), connected again within a second.
  - Both drops began during an X2 flash, to held (ending 02:58:07) and to
    `slcan` (03:48:16-03:50:10). The first run's X2 flashes had none.
  - The WiCAN did not reboot through either. Its uptime read before the
    `_rt` OTA puts its boot near 02:56, and at the end hook near 03:40.
    Which end dropped the link is not known.
  - What ran inside the drops:
    - `man_held` 02:58:07-02:59:48: B2held FAIL (each of its 3 arms:
      "device did not answer /gen_inhibit ... timed out"); B2 ERROR (the
      runner script exited 2: "4 arm records, want 6"); B9 ERROR (its first POST timed
      out); NONODE ERROR ("start reboot not confirmed", no page in 4
      tries, nothing sent).
    - B22 03:50:11-03:52:49: FAIL on both arms, with the same timeout before
      any replay.
  - None of these reached the device, so they are not observations against
    the spec. Their first-run results stand: all PASS.
- **B16 (the idle page-only baseline): `7bc9` PASS, `c6a0` FAIL by the
  check's rule.**
  - `7bc9`: 359 polls, no outage.
  - `c6a0`: 357 polls, 0 unanswered, 0 with the WLAN down, 0 not in mode 0.
    One poll stalled 4.0 s (130.4-134.4 s into the run, about 02:37), and
    the check counts any outage as FAIL. No WLAN event falls in that
    window.
  - The same run's uptime reads show no reboot: `SW`, uptime continuous
    from 1790520 to 2150091 ms. The check prints "stall vs uptime:
    DISAGREE", "outages not a reboot 1".
  - Observed: one page request took 4.0 s to answer, with no reboot and no
    mode change. Whether a stall without a reboot fails B16 is for the
    reviewer.
- **Expected differences from the baseline,** as at step 1a2. **Identity
  rows, labelled, not compared:**
  - B17a page `elf_sha256` `cbfc683a` (baseline `25f50ac0`);
  - B17b `a6c5365e` (`f9de9de9`);
  - B19page `cbfc683a` (`f3957789`);
  - B18 `diag_build_id` `cbfc683a` (baseline schema 7 `diag_git_hash`
    `0x2FE85674`).

  **"0x7F3 carries the build id", bench half (B18): PASS** ("MATCH, 7 rows
  ok").
- **B6 (spec 7, trip 6; the 12.4 row "error-frame rate trip", the
  threshold): `7bc9` INCONCLUSIVE, `c6a0` PASS.**
  - `7bc9`: 9 of 10 arms PASS over 5 runs. `n10_lead8.5` arm 0 was
    INCONCLUSIVE ("delivery was not one error frame per pulse"): 9 pulses
    were delivered of 10. The device counted each once (`bus_error` deltas
    `[1, 1, 1, 1, 1, 1, 1, 1, 1]`), at most 9 in a window, no latch. This is
    known anomaly 7, short arms, as at steps 1a and 1a2.
  - `c6a0`: 10 of 10 arms PASS over 5 runs.
  - Where the counted error frames fell (`runs/step1b_488ef9f/b6/`,
    `8bc82f1`'s `b6_multicount_landing.py` and `b6_class_count.py` over
    both runs' 10 threshold records, 186 pulses): none counted 5 or more
    times. By class: single 7, other single 179, no delta or no error frame
    5. The over-count seen at `e3d9437` and 1a2 (one pulse counted 11 times)
    did not occur in these runs.
- **B9 (known anomaly 6; spec 7, trip 6, wrong-bit-rate method): `7bc9`
  PASS on a live arm.** The driver was running before the slice, with 0
  witness error frames before the first burst and since the last clean
  live sample (-0.146 s). Trip 6, "error-frame rate exceeded", latched at
  +0.25 s. 9 bursts, 24535 witness error frames (2544-3048 a burst), TEC
  max 0. `c6a0`: never reached the device (above).
- **NONODE ("a bus with no other node", bench): `7bc9` PASS**, 3 main arms,
  trip "error-frame rate exceeded", latch max 0.462 s. `c6a0`: never
  reached the device.
- **B22: `7bc9` PASS, both arms,** on provenance: as specified, 212124 of
  212124 replayed frames found, the device's 0x051 8983 (the device says
  8983), late 0, double 0, ambiguous 0; `tx_fail` 0, `ctr_bad` 0,
  `tx_queued_behind` 0, 0 error frames. `c6a0`: never reached the device.
- **The OTA rollback rehearsal: NOT RUN.** It needs a WiCAN console capture,
  and none is taken until the WiCAN's factory reset (console rule).
- **Figures flagged, verdicts unchanged unless named above.** Each is given
  as `7bc9` / `c6a0` (baseline); "-" means not in that run:
  - B2: `respond_probes_x2app` 2996 / - (2994).
  - B3, `first_answer_ms`: observe max 46.8 / 34.7 (36.0); off min 150.8 /
    150.9 (150.9), off max 150.9 / 403.0 (151.0); passive min 4.5 / 4.5
    (15.8); respond max 41.4 / 36.9 (34.0).
  - B5: 500/s `pass` 9 / 8 (8), `inconclusive` 0 / 1 (1).
  - B9: `burst_errors` 2544-3048 / - (2565-3087); `witness_error_frames`
    24535 / - (34031); `tec_max` 0 / - (34).
  - B11raw: `pass_on_ack` 0 / 1 (1); `send_to_first_byte_ms` 7.8-24.9 /
    7.1-39.0 (6.1-20.1).
  - B12: `panic_boot_s` 0.611-0.723 / 0.584-0.602 (0.592-0.599);
    `sw_boot_s` 2.300-3.848 / 2.306-2.328 (2.314-2.353).
  - NONODE: `latch_max_s` 0.462 / - (0.49).
  - B15: `frames_pcan` 20946 / 22074 (20930); `frames_gs_usb` 14789 / 15676
    (14791); `non_ack_repeat_id` 0x7F8 / 0x7F3 (0x7F9).
    - **Correction (tester, 2026-11-18): `frames_gs_usb` is the CAN2
      Innomaker** (today's `canchg`), reached through the X2's bridge, not
      an adapter on CAN1, so it does not count as independent. B15's pass
      rests on its Kvaser and PCAN rows. The verdict stands (reviewer ruling, 2026-11-18).
  - B16: `polls` 359 / 357 (360).
  - B22: `response_us_max_a` 1279 / - (846); `_b` 981 / - (814).
  - B23: contend aborted 68 / 71 (57); contend no effect 9 / 7 (21);
    contend lost-then-aborted 1 / 0 (0); retry aborted 71 / 70 (69); retry
    first abort enough 72 / 70 (69); retry lost-then-aborted 7 / 8 (9).
  - B25: `gate_open_s` 203.478 / 203.479 (203.479).
  - B26: `ge2_idle_a` 20 / 21 (15); `ge2_loaded` 15306 / 14216 (11543);
    `max_backlog_idle_a` 4 / 3 (3), `_idle_b` 5 / 5 (4), `_loaded` 9 / 9 (8).
- **Run files:**
  - `runs/chain/gen-inhibit-emulated_20261031T{201452_f825,203000_5173}/`;
  - `runs/chain/gen-inhibit-bench_20261101T{003528_1eb9,003618_9fe5}/` (refused);
  - `runs/chain/gen-inhibit-bench_20261101T{003858_7bc9,020455_c6a0}/`;
  - `runs/step1b_488ef9f/` (`README.md`; `SHA256SUMS` `21580d1c...c0dd`
    hashes every file: `builds/`, `build_checks/`, `spec_shim/`,
    `spec_shim_console.txt`, `gi_host_run1_stale_sync/`, `gi_host/`,
    `harness_tests/`, `hand_ota_man/`, `b6/`).

**Port step 1b: targeted bench re-run of man_app/B16, man_held and
man_slcan/B22 -- tester, 2026-11-01 09:26-10:17 PST, on `gen-inhibit/main`
`488ef9f`. Its own run, not a change to the entry above. Result: PASS on
every re-run check. B16: PASS, 360 polls, no outage, no stalled poll.**
The user ruled (2026-11-01, through the reviewer) that the five checks
`c6a0` never delivered to the device (B2held, B2, B9, NONODE and B22) be
re-run, and B16 with them, whose reading of `c6a0`'s one stalled poll waits
on this re-run. Spec 7 (trip 6: B9, NONODE), the 12.4 rows "error-frame
rate trip" (B9) and "a bus with no other node" (NONODE, bench), and part B's
B2, B16 and B22.
- **Harness:** `gen-inhibit/tester/accept-1b` at `49ba306` (`9d611dc..49ba306`,
  not yet merged), clean in both runs. `49ba306`: `page_only_baseline.py`
  (B16) also logs the WLAN signal and the receive and transmit rates at each
  poll, read as numbers only. They are logged, not judged. Its test,
  `test_page_only_baseline`, has 42 checks, PASS. A parser returning
  constants fails it, and so does one whose label also matches the SSID
  line.
- **Build and settings:** the images of 12.3's entry "Port step 1b" and the
  same run settings copy.
- **Load:** "No builds" was set 09:26 and cleared 10:20. No build or other
  heavy work ran during the chain.
- **Boards.** The runner's identify read each board before every group
  (each run's `progress.jsonl`). WiCAN: `_man` `cbfc683a...` throughout,
  with no OTA. X2: `756d6714`, then held, then `slcan` in each run, restored
  and read `756d6714` at 10:17:32. Start hooks PASS (mode 0, not latched),
  end hooks PASS. Each run's lock was held by its runner.
- **Wi-Fi:** WLAN-AutoConfig logged no disconnect (event 8003) from 09:26 to
  10:19, only the one connect at 09:26:47
  (`runs/step1b_488ef9f_rerun/wlan_events.txt`). The four X2 flashes in each
  run had no drop.
- **Runs** (`runs/chain/`):
  - `gen-inhibit-bench_20261101T092639_858a`, 09:26:39-09:51:47, `bench_compare.py chain
    --check` with the six checks: SUBSET RUN, PASS 5, ERROR 2. Both ERRORs are
    B2's (`B2` and its `rc`, "B2: no input files"). B2's desk check reads the
    man_app/B2app and man_held/B2held outputs, and my `--check` list did not
    select man_app/B2app. A selection error of mine, not a device result.
  - `_095148_f98b`, 09:51:48-10:17:42, the chain's automatic re-run of the
    flagged checks, which added man_app/B2app: PASS 6.
  - **The comparison** with the `e3d9437` baseline:
    `858a/compare_report.txt` (flagged 4 of 39; every verdict PASS except
    858a's B2 ERROR above).
- **B16 (the idle page-only baseline): PASS** (858a). 360 polls, 0
  unanswered, 0 with the WLAN down, 0 stalled, 0 outages. Uptime: no reboot,
  one boot (`SW`), 94097 to 453244 ms over 359.2 s, rate 0.99995. "stall vs
  uptime: agree". WLAN signal 100 % and rx and tx 72.2 Mbit/s at every one of
  the 360 polls.
- **B2held: PASS** (858a and f98b). **B2: PASS** (f98b), 6 arms as
  specified, X2 frames 0 (inhibit 0x051 670 and 670, long arm 29977 and
  29977, respond probes 2997 and 2996, x2app and x2held).
- **B9 (known anomaly 6; spec 7, trip 6, wrong-bit-rate method): PASS** in
  both runs. 858a: 9 bursts, 25090 witness error frames (2671-3161 a burst),
  TEC max 0, trip 6 latched at +0.24 s. f98b: 25646 (2501-3222), TEC max 0,
  +0.24 s.
- **NONODE ("a bus with no other node", bench): PASS** in both runs, 3 main
  arms, trip "error-frame rate exceeded", latch max 0.46 s and 0.482 s.
- **B22: PASS** in both runs, both arms: provenance clean, `tx_fail` 0, 0
  error frames, the device's 0x051 8983.
- **Figures flagged, verdicts PASS.** Each is given as `858a` / `f98b`
  (baseline):
  - B9: `burst_errors` 2671-3161 / 2501-3222 (2565-3087);
    `witness_error_frames` 25090 / 25646 (34031); `tec_max` 0 / 0 (34).
  - NONODE: `latch_max_s` 0.46 / 0.482 (0.49).
  - B22: `response_us_max_a` 919 / 984 (846); `_b` 999 / 1358 (814).
  - B2: `respond_probes_x2app` - / 2997 (2994), `_x2held` - / 2996 (2992).

  The same B9, NONODE and B22 figures moved the same way in `7bc9` (entry
  above).
- **`c6a0`'s B2held, B2, B9, NONODE and B22 results stay as recorded above:**
  they never reached the device. This run is the device result for those
  checks at `488ef9f`.
- **Run files:**
  - `runs/chain/gen-inhibit-bench_20261101T{092639_858a,095148_f98b}/`;
  - `runs/step1b_488ef9f_rerun/` (`README.md`, which gives the sha256 of
    each run's `index.txt` and `progress.jsonl`, 858a's
    `compare_report.txt` and B16's output; `SHA256SUMS` `cdc48e4d...1d8a`
    hashes `README.md` and `wlan_events.txt`).

**Port step 1c's acceptance: the spec shim, the T12 fake cases, the
emulated suite, `gi_host_run` and part B on `5f2fe2c` -- tester,
2026-11-02 10:16 to 20:11, on `gen-inhibit/main` `5f2fe2c`. Result: the
first bench runs (`6a84` for `man_app` and `man_held`, `e25d` for the
other seven groups) match the `e3d9437` baseline in every verdict: PASS 38,
NOT-RUN 1 (B27, the declared skip). `e25d`'s automatic re-run reads B12,
B23, B26 and B22 PASS, and B5, B6, B7 and B8 FAIL with 0 arms judged; those
four never reached the device, because NICKY-XPS's Wi-Fi link to the WiCAN
was logged down while they ran. `6a84`'s automatic re-run did not run (the
X2's images were missing). The OTA rollback rehearsal: NOT RUN.** The
reviewer's 1c acceptance (2026-11-02): the builds and build checks (in
12.3's entry "Port step 1c"), the harness tests, the emulated chain,
`gi_host_run` and the spec shim on madhouse, the full bench suite against
the `e3d9437` baseline's verdicts with B19, the 12.4 restart row and the
C3-abort probe row, and twai_port's T12 fake cases and `t12-standins` at
shared/main `2418ed8`. Spec 3.2, 3.3 and part B as a whole; the 12.4 rows
"0x7F3 carries the build id", "a bus with no other node, emulated" and "no
error frames at a restart".
- **Harness for 1c** (`gen-inhibit/tester/accept-1c`, `5f2fe2c..26acc04`,
  not yet merged): `26acc04`, `bench_compare.py` compares the `e3d9437`
  index's figures under the old switch names as port step 1c's new ones
  (`RENAMED`, the inverse of `suite_judges.RENAMED`), and the report row
  names the old name. Its test: `test_bench_compare` 64 ok (6 new);
  `test_bench_compare_mutants` 51 of 51 killed, the control passing.
- **Build:** the images and the release stand-in in 12.3's entry "Port step
  1c".
- **Spec shim: PASS** (`runs/step1c_5f2fe2c/spec_shim/`, madhouse-debian,
  gcc 14.2.0, from a clone of `26acc04`; `shared-2026.11.01.2` at
  `b33bce5`, recorded as pinned, nothing modified). 90 PASS lines, no FAIL
  or VOID. The case lines are identical to 1b's. `mutate.py` killed 51 of
  51 mutants. `test_host_env --mutants`: 10 of 10 killed, the control
  passing (`madhouse_console.txt`).
- **T12 fake cases at shared/main `2418ed8`: PASS**
  (`runs/step1c_5f2fe2c/t12_fake/`, madhouse-debian, gcc 14.2.0): `make
  run` exit 0, "every case passed on C3 and S3"; `make t12-standins` exit
  0, the standin as written passing every T12 case and all 9 broken
  standins caught.
- **Emulated suite: PASS 3, NOT-RUN 2** (C1C7 and C3, by design):
  `runs/chain/gen-inhibit-emulated_20261102T111059_7c94`, code `5f2fe2c`
  and harness `26acc04`, both clean. H: 25 steps exit 0; 81 goldens
  passed, 0 failed, 23 without goldens; 9 replays identical. B15host and
  C2scan (3 of 3) passed. The same figures as 1b's `5173`.
- **`gi_host_run` on madhouse-debian: PASS** (`runs/step1c_5f2fe2c/gi_host/`,
  from `26acc04`, code `5f2fe2c`): all 8 steps exited 0. The regen: 9
  replays identical to the golden, every delta +0. Suite: 81 passed, 0
  failed, 23 without goldens. Checks: invariants 104/0, passive_diff 100/0
  with 4 skipped, fuzz 200/0, load_margin PASS (12.6x), spec_expect PASS.
  Sched and the shim: all cases pass. The same as 1b.
- **Harness tests at `26acc04`** (`runs/step1c_5f2fe2c/harness_tests/`, clean
  at both ends): 85 tests, run in one environment (Git's `usr/bin` first on
  PATH, `INTERPOSER_CODE` and `GI_CHAIN_SETTINGS` set), so none needed a
  rerun this time. 84 exit 0, every mutant set killed with its control
  passing (`test_suite_gen_inhibit` 303 ok, its mutants 76 of 76). The
  other: `test_host_env --mutants` killed 5 of 10 on Windows, where its
  real-shell checks don't run, as at 1b. The counted run is madhouse's 10
  of 10 (above). `test_refusal_pairs` over real images is in 12.3's entry.
- **Boards.** A hand take (2026-11-02 16:29 PST) and `bench_flash.py
  --flash` from `488ef9f` `_man` (`cbfc683a...`) to `5f2fe2c` `_man`
  (`47e805f1...`): TOOK in 42 reads. The release reboot PASS: SW reset,
  mode 0, not latched. Released 16:31 (`runs/step1c_5f2fe2c/hand_ota_man/`).
  In each run, the runner's identify read each board before every group
  (each run's `progress.jsonl`): WiCAN `_man` `47e805f1...` and each
  group's OTA image; X2 `756d6714`, held, the jammer or `slcan`. Each run's
  lock was held by its runner. Every OTA took. The X2's identify ran from
  the interposer-tester worktree's `charge-interposer/`, at `725b4c4`,
  clean.
- **Runs** (`runs/chain/`, settings `chain_settings_step1c_5f2fe2c.json`,
  code `5f2fe2c` and harness `26acc04`, clean):
  - `gen-inhibit-bench_20261102T163353_6a84`, 16:33-17:57, every group
    (`bench_compare.py chain`): `man_app` and `man_held` ran, PASS 25. At
    the `rt` group's start the X2's restore to `x2_app` failed before any
    flash: the image files the settings named, under the system temp
    folder, had been removed that morning by the charge-interposer tester's
    scratch cleanup, on the user's go. The other ten results NOT-RUN;
    nothing was written to the X2.
  - `_175756_e819`, 17:57-17:58, the automatic re-run of `6a84`'s 15
    flagged checks: all NOT-RUN for the same reason. So the checks `6a84`
    flagged on their figures (B2, B3, B9, B11raw, NONODE, B15 and B25; and
    B20, flagged only because `e819` did not run it) have one run each.
  - The charge-interposer tester rebuilt the X2 images from `4a59647`
    (digest `36edf3e55552`) into reverse-it's
    `interposer-firmware/images/36edf3e55552/`. Their md5s matched the
    settings, and the settings' `x2_images` were pointed there.
  - `_182409_e25d`, 18:24-19:31, the seven groups `6a84` could not run
    (`rt`, `txabort`, `abortwin`, `rxq`, `prod`, `man_jammer`,
    `man_slcan`): PASS 13, NOT-RUN 1 (B27). Its start step restored the X2
    from held to `x2_app` (read `756d6714`).
  - `_193155_85e2`, 19:31-20:11, the automatic re-run of `e25d`'s 5
    flagged checks: PASS 4, FAIL 4 (below). The runner's end hook passed,
    and it released the lock at 20:11:24.
  - **The comparisons:** `6a84/compare_report.txt` (with `e819`; flagged
    20 of 39) and `e25d/compare_report.txt` (with `85e2`; flagged 8 of
    39). Together, the two first runs give every one of the baseline's 39
    results: PASS 38 and NOT-RUN 1, as the baseline. "flaky, needs
    review": B5, B6, B7 and B8 (PASS first, FAIL on re-run).
- **The re-run's Wi-Fi drop: four results that never reached the device.**
  - Windows' WLAN-AutoConfig log (event 8003, reason code 0) recorded
    NICKY-XPS's Wi-Fi link to the WiCAN's AP as "disconnected by the
    driver" once: 20:00:42, reconnected 20:02:38
    (`runs/step1c_5f2fe2c/wlan_events_1c.txt`). Every other disconnect in
    both chains was a rejoin by the harness (reason code 3), connected
    again within a second.
  - It began during `man_jammer`'s first case (`B5_livetrain_p5000`,
    19:59:16), not during a flash. Two arms had passed (trip 6 latched);
    the next POST setting mode 0 timed out (about 20:00:59). Each later
    case, 20:01:08-20:02:21, stopped at its precondition, "device must be
    in mode 0", with 0 arms. So B5, B6, B7 and B8 read FAIL ("0 of 0
    arms").
  - Which end dropped the link is not known. The WiCAN answered its page
    again at 20:03:36 (`man_slcan`'s identify, `47e805f1...`).
  - None of these reached the device, so they are not observations against
    the spec. Their first-run results stand: all PASS (below).
- **Expected differences from the baseline,** as at step 1b. **Identity
  rows, labelled, not compared:**
  - B17a page `elf_sha256` `47e805f1` (baseline `25f50ac0`);
  - B17b `7aac1135` (`f9de9de9`);
  - B19page `47e805f1` (`f3957789`);
  - B18 `diag_build_id` `47e805f1` (baseline schema 7 `diag_git_hash`
    `0x2FE85674`).

  **"0x7F3 carries the build id", bench half (B18): PASS** ("MATCH, 7 rows
  ok").
- **B19: PASS** (`e25d`). B19present: all four switches present on their
  test builds (`RESTART_GUARD_TEST`, `TWAI_PORT_TXABORT_PROBE`,
  `GI_INSTRUMENT_ABORTWIN`, `GI_INSTRUMENT_RXQ`). B19absent: all five
  absent on the prod stand-in (those four and `OTA_HEALTH_FAULT_INJECT`).
  B19page AS SPECIFIED, page `elf_sha256` `47e805f1`.
  The baseline names two of the switches `GI_TEST_RESTART_QUIESCE` and
  `GI_INSTRUMENT_TXABORT`; `bench_compare` compares them under the new
  names and says so on the row ("same", PASS both).
- **"No error frames at a restart" (B12, the `_rt` image): PASS** in both
  `e25d` and `85e2`, 7 of 7 cycles, 0 error frames.
- **The C3-abort probe (B23, the `_txabort` image): PASS** in both runs.
  `e25d`: contend 66 aborted / 9 no effect / 3 lost-then-aborted; retry 78
  of 78 free, 0 on the wire. `85e2`: contend 60 / 14 / 4; retry 78 of 78
  free, 0 on the wire. abortwin (`_abortwin`): PASS (`e25d`, rc 0).
- **B5-B8 (the jammer), `e25d`: PASS.** B5: 200/s 3 of 3, 500/s 9 of 9, 0
  inconclusive. B6: 10 of 10 arms PASS over 5 runs. B7: 3 of 3 arms AS
  SPECIFIED. B8: train 3 of 3 PASS; control INCONCLUSIVE. `85e2`: never
  reached the device (above).
- **B9 (known anomaly 6; spec 7, trip 6, wrong-bit-rate method): `6a84`
  PASS.** 9 bursts, 25343 witness error frames (2562-3143 a burst), TEC
  max 0. Trip 6 latched at +0.06 s.
- **NONODE ("a bus with no other node", bench): `6a84` PASS**, 3 main arms,
  trip "error-frame rate exceeded", latch max 0.466 s.
- **B16 (the idle page-only baseline): `6a84` PASS**, 360 polls, 0
  unanswered, 0 stalled, no outage.
- **B22: PASS** in both `e25d` and `85e2`, both arms: provenance clean,
  `tx_fail` 0, `ctr_bad` 0, 0 error frames, the device's 0x051 8983.
- **The OTA rollback rehearsal: NOT RUN.** It needs a WiCAN console capture,
  and none is taken until the WiCAN's factory reset (console rule).
- **Figures flagged, verdicts unchanged unless named above.** For
  `man_app` and `man_held`, each figure is `6a84`'s (baseline), with no
  re-run. For the rest it is `e25d` / `85e2` (baseline):
  - B2: `respond_probes_x2held` 2990 (2992).
  - B3, `first_answer_ms`: observe max 43.7 (36.0); passive min 4.5
    (15.8); respond 12.0-95.8 (14.8-34.0).
  - B5: 500/s `pass` 9 / 0 (8), `inconclusive` 0 / 0 (1).
  - B9: `burst_errors` 2562-3143 (2565-3087); `witness_error_frames` 25343
    (34031); `tec_max` 0 (34).
  - B11raw: `pass_on_ack` 0 (1); `send_to_first_byte_ms` 7.0-24.2
    (6.1-20.1).
  - B12: `panic_boot_s` 0.583-0.586 / 0.584-0.593 (0.592-0.599);
    `sw_boot_s` 2.314-4.504 / 2.326-2.333 (2.314-2.353).
  - NONODE: `latch_max_s` 0.466 (0.49).
  - B15: `frames_pcan` 22138 (20930); `frames_gs_usb` 15665 (14791);
    `non_ack_repeat_id` 0x7F1 (0x7F9).
    - **Correction (tester, 2026-11-18): `frames_gs_usb` is the CAN2
      Innomaker** (today's `canchg`), reached through the X2's bridge, not
      an adapter on CAN1, so it does not count as independent. B15's pass
      rests on its Kvaser and PCAN rows. The verdict stands (reviewer ruling, 2026-11-18).
  - B22: `response_us_max_a` 1010 / 1052 (846); `_b` 1118 / 1001 (814).
  - B23: contend aborted 66 / 60 (57); contend no effect 9 / 14 (21);
    contend lost-then-aborted 3 / 4 (0); retry aborted 71 / 73 (69); retry
    first abort enough 71 / 74 (69); retry lost-then-aborted 7 / 5 (9).
  - B25: `gate_open_s` 203.478 (203.479); `resumed_s` 203.481 (203.482).
  - B26: `ge2_idle_b` 10 / 11 (17); `ge2_loaded` 14799 / 13186 (11543);
    `max_backlog_idle_a` 5 / 5 (3), `_idle_b` 3 / 3 (4).
- **Run files:**
  - `runs/chain/gen-inhibit-emulated_20261102T111059_7c94/`;
  - `runs/chain/gen-inhibit-bench_20261102T{163353_6a84,175756_e819}/`;
  - `runs/chain/gen-inhibit-bench_20261102T{182409_e25d,193155_85e2}/`;
  - `runs/step1c_5f2fe2c/` (`README.md`; `SHA256SUMS` `ef7307e0...39e8`
    hashes every file: `builds/`, `build_checks/`, `verification*/`,
    `spec_shim/`, `t12_fake/`, `madhouse_console.txt`, `gi_host/`,
    `harness_tests/`, `hand_ota_man/`, `wlan_events_1c.txt`).

**Port step 1c's targeted bench re-run (the user's option (b)) on
`5f2fe2c` -- tester, 2026-11-02 21:06 to 23:55. Result: the first run
PASS 16, every selected check PASS. Its automatic re-run PASS 14, FAIL 2:
B6 (one `n9` arm latched trip 6 with at most 9 error frames on the wire
in any window; observed vs spec, below) and B20 (`rekey-long`
INCONCLUSIVE, a judge precondition not met; every check passed).** The user
ruled this re-run (2026-11-02, relayed by the reviewer) after the entry
above: man_jammer B5-B8, whose re-run never reached the device, and the
checks with one run on file, man_app B3, B11raw, B15, B25 and B20 and
man_held B2, B9 and NONODE. 1c is accepted once it passes; if anything
fails, the reviewer takes it to the user before 1c closes. The pinned
record `runs/step1c_5f2fe2c/` is unchanged. Sections: part B, these nine
checks, as the entry above.
- **Run:** `bench_compare.py chain --check` (the nine checks), code
  `5f2fe2c` and harness `26acc04`, both clean, settings
  `chain_settings_step1c_5f2fe2c.json`, against the `e3d9437` index. The
  chain added the desk steps those checks read from (man_app B2app, B3arms,
  B11rawarms; man_held B2held), 13 cases a run. The runner took and
  released the lock (released 23:55:19). "No builds" was set for the
  chain and cleared after its release.
  - `runs/chain/gen-inhibit-bench_20261102T210609_20c8`, 21:06-22:30: PASS
    16.
  - `_223048_0b9a`, 22:30-23:55, the automatic re-run of `20c8`'s 13
    flagged cases: PASS 14, FAIL 2.
  - `20c8/compare_report.txt` (with `0b9a`; flagged 10 of 39). "flaky,
    needs review": B6 and B20 (PASS first, FAIL on re-run).
- **Boards.** WiCAN `5f2fe2c` `_man` `47e805f1...` throughout, no OTA; each
  run's start and end hooks PASS (found wican_man, mode 0, not latched),
  read off the wire last at 23:52:33. X2 `756d6714` at each start, held for
  `man_held`, the jammer `a89ece8c...` under custody for `man_jammer`,
  restored to `756d6714` (read 22:30:36 and 23:55:09). Each run's
  `progress.jsonl` has every identify and flash.
- **Wi-Fi.** NICKY-XPS's WLAN-AutoConfig log has no connect, disconnect or
  connecting event (8000/8001/8003) from 21:05 to 23:57 (the same query
  found 38 over the first chains, 16:29-20:13). It has 84 triplets of
  11004/11010/11005 (security stopped, started, succeeded), each within a
  second (`runs/step1c_5f2fe2c_rerun/wlan_events_1c_rerun.txt`).
- **B5, B7, B8 (the jammer): PASS in both runs.** B5: 200/s 3 of 3, 500/s
  9 of 9, 0 inconclusive. B7: 3 of 3 arms AS SPECIFIED. B8: train 3 of 3
  PASS; control INCONCLUSIVE, as in `e25d`.
- **B6 (spec 7, trip 6; the 12.4 row "error-frame rate trip", the
  threshold): `20c8` PASS, `0b9a` FAIL.**
  - `20c8`: 10 of 10 arms PASS over 5 runs.
  - `0b9a`: 9 of 10 arms PASS. **`n9_lead5` arm 1: FAIL, observed vs
    spec.**
    - Observed: the witness saw one error frame per pulse, 9 pulses (per
      pulse `[1, 1, 1, 1, 1, 1, 1, 1, 1]`, at most 9 in any window). The
      device's `bus_error` deltas were `[1, 1, 1, 1, 9, 1, 1, 1, 1]`, and
      trip 6, "error-frame rate exceeded", latched 4.381 s after the first
      pulse (`x2_jam_threshold_20261102T234504.json`).
    - Spec 7, trip 6: 10 or more error frames within a 10 s window. The
      wire never carried more than 9 in a window.
  - Where the counted error frames fell (`26acc04`'s
    `b6_multicount_landing.py` and `b6_class_count.py` over both runs' 10
    threshold records, 192 pulses; `runs/step1c_5f2fe2c_rerun/b6/`): 191
    pulses counted once, 1 counted more than once: the failing arm's pulse
    5, delta 9. The frame before its error frame on the wire was a `0x051`
    2730 us earlier, the frame after it a `0x051` 5300 us later; TEC/REC
    max 0/0. Class (next frame a `0x051` 200-320 us after): 4 pulses, all
    single-count; other landings 188, 1 over-count.
  - Precedent on file: the same shape at the `e3d9437` acceptance (one
    pulse counted 11 times, an `n10` arm) and at step 1a2 (`c68c`
    `n9_lead1.5` arm 1, one pulse counted 11 times, FAIL). The open item
    "One pulse counted 10-12 times" (spec 7, trip 6) is not concluded.
    This pulse's count, 9, is below that title's range. Whether this FAIL
    is carried with them is for the reviewer.
- **B20 (the interlock, bench): `20c8` PASS, 28 of 28 AS SPECIFIED;
  `0b9a` FAIL, 27 of 28.** `rekey-long` INCONCLUSIVE
  (`interlock_rekey-long_20261102T230503.json`): all 11 of its checks
  passed (in both segments: never live before the close, no device `0x051`
  after the key-off, key 0 before the trip, the trip one the model allows;
  `emit_refused` never set; diag cadence, largest gap 1.741 s; no `low_soc`
  release). The precondition "0x051 attribution unambiguous" was not met:
  2 of 92278 sender-log sends had two candidate wire frames within the
  residual 0.009249 s (6467 unique matches). Not an observation against
  the spec; the case has no verdict in `0b9a`. Every other case in both
  runs AS SPECIFIED.
- **B2, B3, B9, B11raw, B15, B25, NONODE: PASS in both runs.** Figures
  flagged, verdicts unchanged; each is `20c8` / `0b9a` (baseline):
  - B2: `respond_probes_x2held` 2995 / 2996 (2992).
  - B3, `first_answer_ms`: observe max 47.3 / 35.3 (36.0), observe min
    15.3 / 15.4 (15.4); passive min 4.5 / 4.5 (15.8).
  - B5: 500/s `pass` 9 / 9 (8), `inconclusive` 0 / 0 (1).
  - B9: `burst_errors` 2587-3120 / 2605-3291 (2565-3087);
    `witness_error_frames` 25648 / 25416 (34031); `tec_max` 0 / 0 (34).
    Trip 6 latched at +0.24 s in both.
  - B11raw: `pass_on_ack` 0 / 1 (1); `send_to_first_byte_ms` 8.0-39.0 /
    8.2-19.6 (6.1-20.1).
  - NONODE: `latch_max_s` 0.438 / 0.445 (0.49).
  - B15: `frames_pcan` 20994 / 22230 (20930); `frames_gs_usb` 14781 /
    16554 (14791); `non_ack_repeat_id` 0x7F8 / 0x7F3 (0x7F9).
    - **Correction (tester, 2026-11-18): `frames_gs_usb` is the CAN2
      Innomaker** (today's `canchg`), reached through the X2's bridge, not
      an adapter on CAN1, so it does not count as independent. B15's pass
      rests on its Kvaser and PCAN rows. The verdict stands (reviewer ruling, 2026-11-18).
  - B25: `gate_open_s` 203.478 / 203.478 (203.479); `resumed_s` 203.481 /
    203.481 (203.482).
- **Run files:**
  - `runs/chain/gen-inhibit-bench_20261102T{210609_20c8,223048_0b9a}/`;
  - `runs/step1c_5f2fe2c_rerun/` (`README.md`; `SHA256SUMS`
    `13a9a208...6514` hashes every file: `wlan_events_1c_rerun.txt`,
    `b6/`).

**Port step 1c's B12 folders re-judged with the restart-record judge, on
the desk -- tester, 2026-11-03, harness `4f90d13`. Result: PASS in both
`e25d` and `85e2`, 7 of 7 restart records as specified.** Follow-up (iii)
after 1c (gen-inhibit reviewer, 2026-11-03): B12 ("No error frames at a
restart", the `_rt` image) also judges the restart record each cycle reads
back. The cycle's JSON, by the name its cycle line gives and only beside
the cycle file (the reviewer's reading (a)), must hold
`record_get_after.last_restart` with `did_twai` and `did_stdby` true and
`timed_out` false, as JSON booleans; a record missing, unread or without
`last_restart` fails B12. No bench, no device. The pinned record
`runs/step1c_5f2fe2c/` and the chain run folders are unchanged.
- **Harness:** gen-inhibit/tester/b12-restart-record `4f90d13`, clean.
  `test_suite_judges.py` over the `e3d9437` baseline: PASS, 0 failed; the
  test copies the baseline's 7 records (in `runs/`, before `GI_RUNS_DIR`)
  beside its cycle files in its scratch copy and checks all 7 are found,
  and 7 new edits (each field wrong, a truthy `1`, an unread record, a
  record without `last_restart`, a record not beside its file) each move B12
  alone to FAIL. `test_suite_judges_mutants.py`: 62 of 62 killed by a
  check, 0 errors, the control passing; 7 new B12 mutants.
- **Judged:** `runs/chain/gen-inhibit-bench_20261102T182409_e25d/rt/B12`
  and `_193155_85e2/rt/B12`, with `suite_judges.judge_one("B12", <folder>)`.
  Both PASS: "7 of 7 cycles, 0 error frames; 7 restart records: did_twai,
  did_stdby true, timed_out false". Every cycle's record is beside its file
  and reads `did_twai` true, `did_stdby` true, `timed_out` false. Figures
  as in the 1c entry above.
- **Run files:** `runs/step1c_5f2fe2c_b12_rejudge/` (`README.md`;
  `SHA256SUMS` `7b01e3e9...d988` hashes every other file: both runs'
  re-judge JSON, the test and mutant outputs).

**Port step 1d's acceptance: the build-under-load trial, the WiCAN flashed
with v5.4.2's boot chain, the spec shim, the T12 fake cases, the emulated
suite, `gi_host_run` and part B on `7f2d5032` -- tester, 2026-11-04 22:10
to 2026-11-05 14:13, on `gen-inhibit/main` `7f2d5032`. Result: the trial
PASS ((a) and (b) and both references); the flash PASS; the spec shim, the
T12 fake cases, `gi_host_run` and the emulated suite PASS, as at 1c. The
bench chain's first run (`575b`) gives the `e3d9437` baseline's verdict for
29 of its 32 bench results; the other three: B27 NOT-RUN (the declared
skip, as the baseline), B20 FAIL 27 of 28 (`rekey-long` INCONCLUSIVE, a
judge precondition not met; every check passed; 28 of 28 on re-run) and
B6 INCONCLUSIVE (one arm's stimulus short, in the first run and again in
its re-run; observed below). The OTA rollback rehearsal: NOT RUN.** Plan:
`$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/gen-inhibit/step1d_test_plan.md` items 3, 4 and 5
(reviewer-confirmed); items 1, 2 and 6 are 12.3's entry "Port step 1d".
Spec 3.2, 3.3, 9.1 (the boot chain), 15 and part B as a whole; the 12.4
rows "0x7F3 carries the build id", "a bus with no other node", "no error
frames at a restart" and "error-frame rate trip".
- **Harness:** `gen-inhibit/tester/port-1d-bench`. The trial, the madhouse
  steps, the first emulated run and the first harness tests ran at
  `77143534` (`gen-inhibit/main` `7f2d5032` merged in, with `5339c16d`:
  `baseline_builds.ps1` runs R32's `check-env` through the tree's own
  launcher before each build, with its test). The emulated run counted
  here and the bench chain ran at `bb09b5f6` (below), clean.
- **Why `gi_commit` is `7f2d5032` (8 characters), and the earlier runs
  superseded.** Every 1d image's app descriptor names `7f2d5032`: git's
  automatic abbreviation in this repo is now 8 characters for every
  commit (`git rev-parse --short 5f2fe2c` gives `5f2fe2c4`). The suite
  compares the descriptor with the settings' `gi_commit` exactly (the
  trees by prefix), and the bench suite needs the newest emulated run on
  the same code string. So, in order (each in
  `runs/step1d_7f2d503_suite/consoles/`):
  - the first chain, with `gi_commit` `7f2d503`, refused at once, "this
    run's code is not identifiable" (no run folder, no lock taken).
    `gi_commit` set to `7f2d5032` in the run's settings copy
    (`chain_settings_step1d_7f2d503.json`; its `_comment` says why);
  - the second refused at once: the newest emulated run,
    `gen-inhibit-emulated_20261105T004858_f18d` (`gi_commit` `7f2d503`,
    PASS 3, NOT-RUN 2), "is on other code". `f18d` stays as the record of
    the first emulated run, superseded for the pairing;
  - the emulated suite again, `_090836_049a` (`7f2d5032`, harness
    `77143534`): H PASS, B15host and C2scan ERROR, "0 files match
    `..._[0-9a-f]{7}\.txt`" (their files are named `_7f2d5032`). The
    harness's judges took a short commit in a file name to be exactly 7
    characters. Fixed in `bb09b5f6` (below); `049a` superseded, kept as
    record;
  - `_093208_cabf` at `bb09b5f6`: the emulated run counted (below);
  - the third chain, `gen-inhibit-bench_20261105T094828_e0f8` (09:48)
    and its automatic re-run `_094932_7190`: every result NOT-RUN, "could
    not identify x2". At 1c the X2's identify ran from the
    interposer-tester worktree's `charge-interposer/` (`725b4c4`, the
    diag DBC read from git); that worktree is now on the interposer's 2a
    work with uncommitted changes, so this run's `PYTHONPATH` takes it from
    the chain core's `b9896a0` archive, whose `bench_identify.py` needs the
    settings key `interposer_dbc` (the run stopped on `KeyError`). Set to
    the archive's own `charge-interposer/diag/vtrux-interposer-diag.dbc`
    (sha256 `3c14042f...`, identical to git's at `b9896a0` and to
    `charge-interposer/main`'s; the charge-interposer tester agreed).
    `e0f8` and `7190` superseded, kept as record. Nothing was flashed in
    either; the WiCAN was read `f515a383...` at `e0f8`'s start.
- **Harness change `bb09b5f6`** ("suite_judges: a short commit in a file
  name at git's abbreviation length, 7 to 40"): `suite_judges.C7` from
  exactly 7 hex digits to 7 to 40. Its test: `test_suite_judges.py`
  section 2f, on the baseline's B15host, B17a, B25 and C2scan files: the
  baseline's own verdict PASS; each file renamed with an 8-character
  commit PASS; with a 6-character one ERROR. Two new mutants (exactly 7;
  any run of hex digits). The reviewer had no objection (2026-11-05).
  The committed tests at `bb09b5f6` that use `suite_judges`
  (`runs/step1d_7f2d503_suite/harness_tests_bb09b5f/`, clean at both
  ends, at Idle, the same environment, 7200 s each): all six exit 0.
  `test_suite_judges` PASS, 0 failed; `test_suite_judges_mutants` 64 of
  64 killed (the 62 of the rerun and the two new ones), the control
  passing (5669 s); `test_suite_gen_inhibit` PASS, 303 ok, 0 FAIL;
  `test_suite_gen_inhibit_mutants` 76 of 76; `test_bench_compare` PASS, 64
  ok, 0 FAIL; `test_bench_compare_mutants` 51 of 51, each control passing.
- **Item 3, the build-under-load trial (AGENTS.md "Builds during
  wall-clock tests", point 2; gen-inhibit's build tool changed with
  v5.4.2): PASS** (`runs/step1d_7f2d503_trial/`, 2026-11-04 22:10-23:06,
  a hand take; trial runs, not evidence of device behaviour). Stimulus
  `interlock_bench.py run rekey-long --trial` (92,278 frames, 225 s), the
  WiCAN on `5f2fe2c` `_man`. Pass marks (reviewer-confirmed, set first):
  Kvaser worst stimulus gap beyond plan under 0.100 s, and the judge AS
  SPECIFIED, in every run overlapping the build and both references.
  - References: worst 0.0013 s and 0.0018 s, AS SPECIFIED.
  - (a) full speed (`baseline_builds.ps1 -Builds man`, cold, 495 s): 2
    overlapping runs, worst 0.0124 s and 0.0102 s, both AS SPECIFIED.
  - (b) throttled (Idle, `bench_build.py --jobs 1`, 1674 s): 7
    overlapping runs, worst 0.0015-0.0053 s, all AS SPECIFIED.
  - Every run: 0 witness error frames, 0 sends failed, every ID seen as
    often as sent; the arm POST 14-26 ms (references 22 and 26 ms); the X2
    0 frames on CAN1. (b) took 1674 s, not the about 3135 s the plan
    expected, so it had 7 overlapping runs, not about 14.
- **Item 4, the WiCAN flashed by USB with v5.4.2's boot chain (spec 9.1,
  15): PASS** (`runs/step1d_7f2d503_flash/`, 2026-11-05 00:32-00:35, a
  hand take; the user plugged the USB in for it). The release stand-in's
  build folder (item 1a, `7f2d5032`, record `f515a383...`) by its
  `flash_args` with the v5.4.2 venv's esptool 4.12.0; NVS not written.
  All four writes "Hash of data verified". Bootloader and partition table
  read back identical to the build's files (sha256 `a2a167d2...`,
  `98d193a6...`). After the release reboot the page reads `elf_sha256`
  `f515a383...`: a new boot, reset_reason SW, mode 0, not latched.
- **Spec shim: PASS** (`runs/step1d_7f2d503_suite/spec_shim/`,
  madhouse-debian, gcc 14.2.0, from a clone of `77143534`;
  `shared-2026.11.04` at `181cec94`, recorded as pinned, nothing
  modified). 90 cases PASS, VERDICT PASS. `mutate.py` killed 51 of 51.
  `test_host_env --mutants`: 10 of 10 killed, the control passing
  (`madhouse_console.txt`).
- **T12 fake cases at `181cec94` (twai_port as at shared/main
  `569d0031`): PASS** (`runs/step1d_7f2d503_suite/t12_fake/`): `make run`
  exit 0, "every case passed on C3 and S3"; `make t12-standins` exit 0,
  the standin as written passing every T12 case and all 9 broken
  standins caught.
- **`gi_host_run` on madhouse-debian: PASS**
  (`runs/step1d_7f2d503_suite/gi_host/`, from `77143534`, code
  `7f2d5032`): all 8 steps exit 0. The regen: 9 replays identical to the
  golden. Suite: 81 passed, 0 failed, 23 without goldens. Checks:
  invariants 104/0, passive_diff 100/0 with 4 skipped, fuzz 200/0,
  load_margin PASS (12.6x), spec_expect PASS. Sched and the shim: all
  cases pass. The same as 1c.
- **Emulated suite: PASS 3, NOT-RUN 2** (C1C7 and C3, by design):
  `runs/chain/gen-inhibit-emulated_20261105T093208_cabf`, code `7f2d5032`
  (`gi_fw` the detached worktree `gen-inhibit-tester-images1d`, clean) and
  harness `bb09b5f6`, clean. H: 25 steps exit 0; 81 goldens passed, 0
  failed, 23 without goldens. B15host passed; C2scan 4 of 4. The same
  figures as `f18d` and as 1c's `7c94`.
- **Harness tests at `77143534`** (`runs/step1d_7f2d503_suite/harness_tests/`,
  clean at both ends, at Idle, one environment as at 1c): 90 tests (1c's
  85, plus `scan_scope_check_mutants`, `test_baseline_builds` and
  `test_bench_build` with their mutants). 85 exit 0. The other five:
  - `test_host_env --mutants` killed 5 of 10 on Windows, where its
    real-shell checks don't run, as at 1b and 1c; the counted run is
    madhouse's 10 of 10 (above).
  - `test_suite_judges_mutants` and `test_suite_gen_inhibit_mutants`: no
    result, stopped at the driver's 3600 s timeout (1c: 3225 s and 2800
    s). `test_e4_stimulus_split`: its 0.55 s wall-clock poll-rate check
    failed, and the test took 52 s (1 s at 1c);
    `test_e4_attribution_mutants`: 4 of 4 killed, its control failing the
    same check. All four ran while the charge-interposer tester's 2a
    acceptance run built at full speed on NICKY-XPS (its phase 1).
  - Those four again, on a quiet machine, at `77143534`, clean at both
    ends (`harness_tests_rerun/`, 7200 s each): all exit 0.
    `test_suite_judges_mutants` 62 of 62 killed (4846 s),
    `test_suite_gen_inhibit_mutants` 76 of 76 (3060 s), each control
    passing; `test_e4_stimulus_split` passed (1 s);
    `test_e4_attribution_mutants` 4 of 4 killed, the control passing.
  - `test_refusal_pairs` over real images is in 12.3's entry.
- **Boards.** WiCAN on 12 V only from 09:05 (the user unplugged its USB
  for the chain). In each run the runner's identify read each board
  before every group (each run's `progress.jsonl`): WiCAN `_man`
  `f515a383...` and each group's OTA image; X2 `756d6714`, held, the
  jammer `a89ece8c...` (under custody) or `slcan`, restored to
  `756d6714` (read 12:19:38 and 14:13:11). Every OTA took at attempt 1.
  Each run's start and end hooks PASS (found `wican_man`). Each run's lock
  was held by its runner; "No builds" set for the chain and cleared after
  its release.
- **Runs** (`runs/chain/`, settings `chain_settings_step1d_7f2d503.json`,
  code `7f2d5032` and harness `bb09b5f6`, clean, against the `e3d9437`
  index):
  - `gen-inhibit-bench_20261105T095243_575b`, 09:52-12:19, every group
    (`bench_compare.py chain`): PASS 29, FAIL 1 (B20), INCONCLUSIVE 1
    (B6), NOT-RUN 1 (B27) of the baseline's 32 bench results. H, B15host
    and C2scan are the emulated run's; C1C7, C3, C5 and C6 were replaced
    in stage (a) (labelled, not compared), as at 1c.
  - `_121949_a61f`, 12:19-14:13, the automatic re-run of `575b`'s 13
    flagged checks (17 cases with their desk steps): every result PASS
    but B6 INCONCLUSIVE. The runner released the lock at 14:13:21.
  - `575b/compare_report.txt` (with `a61f`; flagged 14 of 39). "flaky,
    needs review": B20 (FAIL first, PASS on re-run). B6: "a re-run's
    verdict is not the baseline's" (INCONCLUSIVE in both).
- **Wi-Fi** (`runs/step1d_7f2d503_suite/wlan_events_1d.txt`, 09:48-14:15):
  9 disconnects, each ReasonCode 3 (a new connection the harness asked
  for), connected again within the second, each just after a group ended
  and before its OTA. No disconnect by the driver.
- **Expected differences from the baseline,** as at 1c. **Identity rows,
  labelled, not compared:** B17a page `elf_sha256` `f515a383` (baseline
  `25f50ac0`); B17b `ea3c0c05` (`f9de9de9`); B19page `f515a383`
  (`f3957789`); B18 `diag_build_id` `f515a383` (baseline schema 7
  `diag_git_hash` `0x2FE85674`).

  **"0x7F3 carries the build id", bench half (B18): PASS** ("MATCH, 7 rows
  ok").
- **B19: PASS** (`575b`). B19present: all four switches present on their
  test builds (`RESTART_GUARD_TEST`, `TWAI_PORT_TXABORT_PROBE`,
  `GI_INSTRUMENT_ABORTWIN`, `GI_INSTRUMENT_RXQ`). B19absent: all five
  absent on the prod stand-in (those four and `OTA_HEALTH_FAULT_INJECT`).
  B19page AS SPECIFIED, page `elf_sha256` `f515a383`. The baseline's two
  old switch names compared under the new ones ("same", PASS both).
- **"No error frames at a restart" (B12, the `_rt` image): PASS** in both
  runs, 7 of 7 cycles, 0 error frames, 7 restart records `did_twai` and
  `did_stdby` true, `timed_out` false (the restart-record judge, as
  re-judged for 1c).
- **The C3-abort probe (B23, the `_txabort` image): PASS** in both runs.
  `575b`: contend 70 aborted / 8 no effect / 0 lost-then-aborted; retry
  78 of 78 free, 0 on the wire. `a61f`: 68 / 10 / 0; retry 78 of 78
  free, 0 on the wire. abortwin (`_abortwin`): PASS (`575b`, rc 0).
- **B5, B7, B8 (the jammer): PASS in both runs.** B5: 200/s 3 of 3; 500/s
  9 of 9 (`575b`), 8 of 9 with 1 inconclusive (`a61f`). B7: 3 of 3 arms
  AS SPECIFIED. B8: train 3 of 3 PASS; control INCONCLUSIVE, as at 1c.
- **B6 (spec 7, trip 6; the 12.4 row "error-frame rate trip", the
  threshold): INCONCLUSIVE in both runs, 9 of 10 arms PASS in each.**
  Each time one arm's stimulus came up short, and the judge gives that arm
  no verdict ("delivery was not one error frame per pulse"):
  - `575b` `n10_lead5` arm 0: the witness saw 9 pulses of 10, one error
    frame each; the device's `bus_error` deltas `[1, 1, 1, 1, 1, 1, 1, 1,
    1]`; no latch. The same shape as step 1b's known anomaly 7 (a short
    `n10` arm).
  - `a61f` `n9_lead5` arm 0: the witness saw 8 pulses of 9, one error
    frame each (at most 8 in any window). The device's `bus_error` deltas
    were `[1, 1, 0, 1, 1, 1, 10, 1]`
    (`x2_jam_threshold_20261105T135659.json`). Trip 6 latched after
    pulse 7 (corrected 2026-11-05 ~17:3x, after the reviewer's question:
    first written as "trip 6 did not latch", which was wrong). The arm:
    the WiCAN rebooted before it (uptime 43594 -> 6810 ms, nothing
    latched); arm POST 5.30 s before the first pulse ("lead5"), live 0.24
    s after it; mode 3, live, key on and fresh, the uptime unbroken from
    then through the last pulse (no re-arm, no reboot, no key change). The
    page's `drv_bus_error` read 1 to 6 across the first six witness error
    frames (pulses 0, 1, 2, 4, 5, 6; none seen on the wire for pulse 3),
    6 at every poll from +6.64 s to +7.61 s, then 16 with
    `abort_latched` true, "error-frame rate exceeded", at the first poll
    after pulse 7's error frame (+7.65 s on the wire, poll +7.72 s), and
    17 after pulse 8. 0x7F8 read `error_rate` / `aborted` from +7.69 s,
    and 0x7F1's `diag_flags` went from 0x66 to 0xC6 (bit 7
    abort_latched set, bit 5 inhibit_live clear) at +8.17 s. So the latch
    came at 16 device counts, from 7 error frames on the wire in 7.7 s.
    The sampled page fields don't include `err_n` or `err_age_ms`. The
    judge gives no latch verdict for this arm: it stops at the delivery
    check.
  - Where the counted error frames fell (`b6_multicount_landing.py` and
    `b6_class_count.py` at `bb09b5f6` over both runs' 10 threshold
    records, 190 pulses; `runs/step1d_7f2d503_suite/b6/`): 187 counted
    once, 2 not counted (a `0` delta: `a61f`'s `n9_lead5` arm 0 above,
    and `a61f` `n9_lead1.5` arm 0, which passed), 1 counted more than
    once. (Corrected 2026-11-05 ~18:0x, after the reviewer's question, from
    the records, no rerun: those 2 are error frames on the wire that the
    device did count, so of the 190 error frames on the wire, 189 were
    counted once, 1 was counted 10 times, and none went uncounted. The suite
    README's "2 not counted" is the same reading, left as written in
    the pinned folder. Each `0` is the judge's per-pulse delta
    (`x2_jammer.per_pulse`), which stamps a page poll with its return time.
    `n9_lead5` arm 0: the `0` is pulse 2's error frame, on the wire at
    +2.15 s (pulse 3 had none, and the per-pulse groups are the wire's
    error frames, so it has no delta). The poll stamped +2.654 s was sent
    at +1.509 s and, by its `uptime_ms`, built at about +1.51 s, before the
    frame. The next poll (sent +2.756 s) reads 3, one more, and the
    page's reads run 1 to 6 over the first six frames. `n9_lead1.5` arm 0:
    the `0` is pulse 5's error frame at +5.490 s. The poll stamped +5.561
    s was sent at +5.476 s and built at about +5.495 s, within about 10 ms
    of the frame. The next poll (sent +5.662 s) reads 6, and the page's
    total, 9, is the wire's 9.)
    The frame counted 10 times: `a61f` `n9_lead5` arm 0 pulse 7, delta 10. The frame before its
    error frame on the wire was a `0x051` 4700 us earlier, the frame after
    it a `0x051` 1960 us later; TEC/REC max 0/0. Class (next frame a
    `0x051` 200-320 us after): 10 pulses, all single-count.
  - Precedent on file: the open item "One pulse counted 10-12 times"
    (spec 7, trip 6), not concluded; 1c's targeted re-run (`0b9a`, one
    pulse counted 9 times, that arm latching). Whether this is carried
    with them is for the reviewer.
- **B20 (the interlock, bench): `575b` FAIL, 27 of 28 AS SPECIFIED;
  `a61f` PASS, 28 of 28.** `575b`'s `rekey-long` INCONCLUSIVE
  (`interlock_rekey-long_20261105T104557.json`): all 11 of its checks
  passed. The precondition "0x051 attribution unambiguous" was not met: 1
  of 92278 sender-log sends had two candidate wire frames within the
  residual 0.009403 s (6512 unique matches). Not an observation against
  the spec; the case has no verdict in `575b`. The same shape as 1c's
  `0b9a`, which the user ruled not a finding (2026-11-03). Every other
  case in both runs AS SPECIFIED.
- **B9 (known anomaly 6; spec 7, trip 6, wrong-bit-rate method): PASS in
  both runs.** 9 bursts; trip 6 latched at +0.25 s / +0.22 s.
- **NONODE ("a bus with no other node", bench): PASS in both runs,** 3
  main arms, trip "error-frame rate exceeded".
- **B16 (the idle page-only baseline): `575b` PASS**, 360 polls, 0
  unanswered, 0 stalled, no outage.
- **B22: PASS** in both runs, both arms: provenance clean, `tx_fail` 0, 0
  error frames, the device's 0x051 8983.
- **The rest of part B: PASS** (`575b`): B1, B4, B10, B11, B13, B14, B21,
  B24, B26, B17a, B17b; B2, B3, B11raw, B15, B25 in both runs.
- **B27: NOT-RUN,** deferred by the user, as in the baseline.
- **The OTA rollback rehearsal on v5.4.2: NOT RUN.** It needs a WiCAN
  console capture, and none is taken until the WiCAN's factory reset
  (console rule).
- **Figures flagged, verdicts unchanged unless named above.** Each is
  `575b` / `a61f` (baseline):
  - B2: `respond_probes_x2app` 2965 / 2995 (2994); `_x2held` 2995 / 2994
    (2992).
  - B3, `first_answer_ms`: observe max 41.1 / 47.6 (36.0), observe min
    15.5 / 15.4 (15.4); passive max 28.8 / 15.9 (28.6), passive min 15.9
    / 4.5 (15.8); respond max 30.2 / 15.7 (34.0); off max 150.9 / 151.0
    (151.0).
  - B5: 500/s `pass` 9 / 8 (8), `inconclusive` 0 / 1 (1).
  - B6: `passed` 9 / 9 (10).
  - B9: `burst_errors` 2549-2786 / 2553-3290 (2565-3087);
    `witness_error_frames` 24102 / 25668 (34031); `tec_max` 8 / 0 (34).
  - B11raw: `pass_on_ack` 0 / 0 (1); `send_to_first_byte_ms` 4.0-11.1 /
    7.2-24.9 (6.1-20.1).
  - B12: `panic_boot_s` 0.581-0.598 / 0.581-0.583 (0.592-0.599);
    `sw_boot_s` 2.303-2.323 / 2.292-2.337 (2.314-2.353).
  - NONODE: `latch_max_s` 0.437 / 0.451 (0.49).
  - B15: `frames_pcan` 22102 / 22114 (20930); `frames_gs_usb` 15679 /
    15672 (14791); `non_ack_repeat_id` 0x7F3 / 0x7F3 (0x7F9).
    - **Correction (tester, 2026-11-18): `frames_gs_usb` is the CAN2
      Innomaker** (today's `canchg`), reached through the X2's bridge, not
      an adapter on CAN1, so it does not count as independent. B15's pass
      rests on its Kvaser and PCAN rows. The verdict stands (reviewer ruling, 2026-11-18).
  - B20: `as_specified` 27 / 28 (28).
  - B22: `response_us_max_a` 996 / 1193 (846); `_b` 1191 / 1025 (814).
  - B23: contend aborted 70 / 68 (57); contend no effect 8 / 10 (21);
    retry aborted 70 / 71 (69); retry first abort enough 70 / 71 (69);
    retry lost-then-aborted 8 / 7 (9).
  - B25: `gate_open_s` 203.478 / 203.478 (203.479); `resumed_s` 203.481
    / 203.481 (203.482).
  - B26: `ge2_idle_a` 34 / 14 (15); `ge2_loaded` 15853 / 16786 (11543);
    `max_backlog_idle_a` 5 / 4 (3); `max_backlog_loaded` 10 / 9 (8).
- **Run files:**
  - `runs/step1d_7f2d503_trial/` (`SHA256SUMS` `ae8dfd57...1ef4`);
  - `runs/step1d_7f2d503_flash/` (`SHA256SUMS` `b4946e45...c812`);
  - `runs/chain/gen-inhibit-emulated_20261105T093208_cabf/`; superseded,
    kept: `_004858_f18d/`, `_090836_049a/`;
  - `runs/chain/gen-inhibit-bench_20261105T{095243_575b,121949_a61f}/`;
    superseded, kept: `_094828_e0f8/`, `_094932_7190/`;
  - `runs/step1d_7f2d503_suite/` (`README.md`; `SHA256SUMS` `78b03ea4...7f8f`, 144
    files, hashes every other file: `spec_shim/`, `t12_fake/`,
    `madhouse_console.txt`, `gi_host/`, `gi_host_console.txt`,
    `harness_tests/`, `harness_tests_rerun/`, `harness_tests_bb09b5f/`,
    `b6/`, `wlan_events_1d.txt`, `consoles/`, `drivers/`).

**B6 per-pulse deltas of 0 re-judged with the new poll stamp, on the desk
-- tester, 2026-11-05, harness `cc957d51`. Result: of 5 deltas of 0 in
3,508 placed pulses, 1 stands and 4 were polls stamped by their return.
The one that stands is the measurement build's (session 2), the figure
behind spec 13's "An error frame can go uncounted: 1 of 1,212".** Asked by
the gen-inhibit reviewer after step 1d's two (2026-11-05). Records only,
no bench; nothing in the runs read was changed.
- **The fix:** `gen-inhibit/tester/b6-poll-stamp` `86dcfee6`.
  `x2_jammer.per_pulse` takes a page reading as "after" a pulse only if it
  was sent after the pulse's error frame, and as "before" only if it
  returned before it. Until then each reading was stamped by its return,
  so a stalled poll sent before a pulse and returned after it, with the
  count from before it, gave that pulse a 0. Its callers pass `req_start`
  on. Tests at `86dcfee6`, clean: `test_x2_jammer.py` PASS (four
  poll-stamp cases), mutants 5 of 5; `test_x2_jam_cases.py` PASS (a judge
  case with a stalled poll), its mutants 15 of 15;
  `test_b6_multicount_landing.py` 11 ok, mutants 8 of 8; each control
  passing (`runs/test_*_20261105_pollstamp.txt`).
- **The tool:** `b6_zero_deltas.py` at `cc957d51`, clean: for every
  threshold record whose witness file `b6_multicount_landing.py` finds,
  every arm, the delta per witness pulse under the new rule and the old,
  and the readings either side, with when each page was built from its
  `uptime_ms`. Its test 12 ok, mutants 8 of 8, the control passing. Every
  record read has `req_start` and `uptime_ms` on every reading. (The same
  run at `57e9fdc8`, `runs/b6_poll_stamp_rejudge_57e9fdc/`, is
  superseded: its tool looked for a witness file only under the record's
  own name and missed the five `c65c45e` records renamed `_VOID_noreset`,
  96 placed pulses with no 0s. Its five rows are the same.)
- **Per figure** (0s old / new; "pulse" numbered from 1 by witness pulse):
  - Before the port, 775 placed: `c65c45e` (10 records, 192 placed, the
    five VOID ones included) and `e3d9437` (the 20 rate arms, the chain
    runs `0068`, `59b0`, `5880`, and the five 2026-10-16 records; 583
    placed): 1 / 0. `5880`
    `x2_jam_threshold_20261020T035309` arm 1 pulse 6: the "after" reading
    returned 0.233 s after the error frame read 6, one more than the
    "before". No earlier figure counted it as uncounted.
  - The measurement build, 1,212 placed:
    - session 1 (`b6_ecc_20261020T214017`, 577): 1 / 0. Arm 6 pulse 27,
      already put down to the page's timing in session 1's desk points
      (the reading after it sent at +0.000 s); the device's isr row is
      there.
    - **session 2 (`b6_ecc_20261020T231941`, 579): 1 / 1, stands.**
      `x2_jam_threshold_20261020T234053` arm 2 pulse 4: the page built
      0.182 s before the error frame read 3, the one built 0.099 s after
      it read 3. Session 3's report on it is above: no isr row for it, and
      the ticks rows either side read `bus_error_count` 3 and 4. So spec
      13's "1 of 1,212" is an error frame the device did not count, not
      this artifact.
    - session 3 (`b6_ecc_20261021T014654`, 56): 0 / 0.
  - Port steps 1a to 1c (`a389`, `ce10`, `cfcd`, `08ea`, `9bf7`, `8e53`,
    `880c`, `fb52`, `c68c`, `7bc9`, `c6a0`, `e25d`, `20c8`, `0b9a`; 1,331
    placed): 0 / 0.
  - Step 1d (`575b`, `a61f`; 190 placed): 2 / 0, both `a61f`, as
    corrected in the entry above.
- **Run files:** `runs/b6_poll_stamp_rejudge_cc957d5/` (`README.md`;
  `SHA256SUMS` `3fa3ad81...9502` hashes every other file: `by_folder.txt`,
  `all_runs.txt`, the tool's test and mutant outputs).

**B5-B8 with the jammer's ESP-IDF port (row `ea762c62044cb5c6`) -- tester,
2026-11-16 21:30-22:13, on `2341e73d` `_man` (`02d702d6...`), madhouse-debian.
Result: B5 PASS, B6 PASS (10 of 10 arms, no arm short), B8 PASS; B7
INCONCLUSIVE (every arm: the replay was not running after the re-arm).** The
gen-inhibit reviewer's order: B5-B8 and the re-run of step 1b's known anomaly 7
(a short `n10` arm) on the ported jammer, before the x2_pin move. These are
`baseline_b_jam.sh jam`'s cases, run by hand under a hand take of the bench
lock, in bench.slice.
- **Conditions.** WiCAN: `02d702d6...`, read off its page through the 3T at
  21:30, 21:43 and 22:12, mode 0 at the start. Power: 12 V on OBD pin 16, as
  bench-holder.md has recorded since 2026-10-09 (not re-read), and USB plugged
  at 1-3.1. No WiCAN console capture. X2 (rule 6): the jammer, archive row
  `ea762c62044cb5c6` (`vtrux_jammer_x2_NOT_FOR_VEHICLE`, image hash
  `ae2783e25ad536ab...`, pinnable), written by `x2_flash_custody.py
  flash-jammer --no-dump --row` at 21:30. `read-otadata` and `verify-row`
  passed at 21:31, and the banner was read at 21:32 and 22:12. Witness: the
  Kvaser through CANlib (`leaf`). Replayer: the PCAN (`pcan`).
- **B5 (livetrain): PASS.** 200/s 3 of 3, 500/s 9 of 9, 0 inconclusive.
  Harness `2b998fe9`.
- **B6 (threshold; spec 7, trip 6): PASS, 10 of 10 arms over the 5 runs.**
  Harness `8556f073`. Every arm had per-pulse deltas of 1 and `bus_error`
  deltas of 1, and the stimulus was never short. In the n10 arms trip 6
  latched, "error-frame rate exceeded", seen on the page 9.02-9.12 s after the
  first pulse. The
  n9 arms did not latch, with at most 9 in the window. Every reset before an
  arm was shown: a lower uptime, mode 0, nothing latched.
- **B7 (busoff-rearm, `--expect reinstalls`): INCONCLUSIVE, every arm, in two
  runs** (`2b998fe9` code at 21:44, `8556f073` at 22:06). The PCAN replaying
  `0x051` went BUSOFF at the jam's start and stayed so for the rest of each
  arm, so after the re-arm the witness saw 0.0 `0x051`/s. With no fresh
  `0x051` the case cannot show the device going live. At `8556f073` the PCAN's
  `reset()` was called once a second on BUSOFF and returned True 68 times per
  arm, and the status still read BUSOFF. The page showed the controller
  `running` 0.98 s after the re-arm, with "no fresh 0x051". The suite judge at
  `8556f073` read this as FAIL ("0 of 3 arms AS SPECIFIED"): it had no
  INCONCLUSIVE. That was fixed after the run, on this topic, and it now reads
  INCONCLUSIVE.
- **B8 (busoff; spec 11, bus-off half): PASS.** Train 3 of 3 PASS; the control
  arm is INCONCLUSIVE, as the case designs it. Harness `2b998fe9` code (tree at
  `8dcbfefc`).
- **Harness changes this run needed** (topic `gen-inhibit/tester/jam-row`):
  - `2b998fe9`: every record names the jammer and the WiCAN's power as given.
    `baseline_b_jam.sh jam` never captures the WiCAN console.
  - `8556f073`: `--reset-wican` rejoins the hotspot through `gi_wifi.select`
    while the page cannot be read, and refuses without `GI_OTA_SETTINGS`. The
    first pass's B6 case lost the page for the whole 40 s, because the 3T did
    not rejoin the hotspot after the reboot by itself, and the next arm's POST
    met "Network is unreachable". The same commit has the PCAN's reset on
    BUSOFF; on this host that did not bring it back, as the B7 bullet says.
  - The B7 judge's INCONCLUSIVE, described in the B7 bullet.
  - `x2_jammer.py id --expect-elf` with the row's whole app_elf_sha256 FAILs:
    the banner prints 9 digits. The 9-digit prefix PASSes. Not changed; the
    board's identity rests on `verify-row`.
- **Run files:** `runs/b5b8_ea762c6_20261116T213049/` (`README.md`: the runs
  and which harness each used, the drivers, `judged.txt`; superseded records
  and the first pass's stdout in `first_pass/`).

**B7 with the replay's PCAN re-opened after its bus-off -- tester, 2026-11-17
00:07-00:14, on `2341e73d` `_man` (`02d702d6...`), madhouse-debian. Result: B7
PASS, 3 of 3 arms AS SPECIFIED.** Spec 7, "A bus-off ends the device's part
until a re-arm or a reboot". This is the bench try of the gen-inhibit
reviewer's option (a), 2026-11-16, after the INCONCLUSIVE B7 in the entry
above. Harness `a34c5d5c` (`4e81b9c2`'s `pcan_reopen`, merged in `6e01cab2`).
On a BUSOFF reading, the replay's PCAN is re-opened: PCAN-Basic Uninitialize,
then Initialize at 500 kbit, then error frames on. `x2_jam_cases.py
busoff-rearm --arms 3 --expect reinstalls`, as `baseline_b_jam.sh jam` runs it,
by hand under a hand take of the bench lock, in bench.slice.
- **Conditions.** WiCAN: `02d702d6...`, read off its page through the 3T at
  00:08:27. It was not flashed or rebooted in the take, and was in mode 0 at the
  end. Power: 12 V on OBD pin 16, as bench-holder.md has recorded since
  2026-10-09 (not re-read), and USB plugged at 1-3.1. No WiCAN console capture.
  X2 (rule 6): still the jammer row `ea762c62044cb5c6`, not flashed. `verify-row`
  passed at 00:08:28. `x2_jammer.py id` passed at 00:08:30, given the row's
  whole app_elf_sha256 (the banner's 9 digits judged as its prefix,
  `2182f563`). Witness: the Kvaser through CANlib (`leaf`, clock `kvaser_hw`).
  Replayer: the PCAN (`pcan`). No kernel USB error on the 1-3 tree over the
  take.
- **Each arm.**
  - The PCAN read BUSOFF at the train's start. It was re-opened four times, 1 s
    apart, and each re-open answered OK. It read OK again about 3.35 s after
    the BUSOFF; the train is 3 s long.
  - The page showed the WiCAN bus_off 0.91-1.02 s after the jam. In the no-arm
    window it stayed bus_off, with 0 device `0x051`.
  - After the direct re-arm it was running and live after 0.19-0.94 s, and the
    first device `0x051` came 0.16-0.26 s after the re-arm. The witness saw
    100.0 `0x051`/s after the re-arm.
  - No reboot.
- **Suite judge** (`suite_judges` B7 at `a34c5d5c`): `B7 PASS | 3 of 3 arms AS
  SPECIFIED`. No timing failure.
- **Run files:** `runs/b7reopen_a34c5d5_20261117T000827/` (`README.md`: the
  per-arm table, the drivers and the pre-step outputs; the record
  `x2_jam_busoff-rearm_20261117T000852.json` and its witness file).

**Bench incident: the bench guard's can-fail ran real `canctl` with no lock held
-- tester, 2026-11-19, found in the short bench check. Harness defect, fixed in
`3f656785`.** No spec section's result rests on it. It is recorded here because it
changed the bench under other holders. Reported to the gen-inhibit reviewer, who
takes it to the user and to the charge-interposer reviewer (2026-11-19), and to
the charge-interposer tester (2026-11-19).
- **Cause.** `test_kvaser_bus.py --can-fail` runs the real bench guard in a child
  interpreter, with the real `kvaser_bus` and the real pinned canbench
  `bench_layer`. The child tripwired python-can's `Bus` entry points and CANlib,
  but not the bench's root operations. Under the mutants that break the guard,
  `kvaser_bus` reached `bench_layer.open_bench` and the real `sudo -n canctl`. It
  stopped only at the next tripwire: the Kvaser's open, or canmain's gs_usb open.
  Every can-fail run from this worktree since `kvaser_bus` took its own `canctl`
  calls (the Kvaser on SocketCAN, 2026-11-18) did this. `sudo -n` runs `canctl`
  without asking.
- **The calls.** All are from journalctl (`_COMM=sudo`, working directory in the
  tester's worktree). Each burst is one can-fail run. In each one, `canctl up` was
  `up kvaser 500000 restart-ms 100`.

  | Time | Calls | Left | Lock |
  |---|---|---|---|
  | 2026-11-18 17:13:18-43 | `up kvaser` 18, `down kvaser` 14 | Kvaser down | free |
  | 18:35:27-52 | the same | Kvaser down | free |
  | 19:01:21-47 | `up kvaser` 17, `down kvaser` 13, `mode canmain libusb` 1 (no hand-back) | Kvaser down; canmain in libusb | **the charge-interposer tester's hand take, 18:50:56-19:18:03** |
  | 19:15:01-09 | `up kvaser` 4 | **Kvaser UP** | **the same hold** |
  | 22:19:04-24, 22:37:36-57 | `up kvaser` 4 each | Kvaser up (already up: can-tools' 22:04:19) | free |
  | 2026-11-19 01:38:50 | `up kvaser` 1 | Kvaser up (already up: the charge-interposer tester's 01:18:41) | free |
  | 02:04:04-11 | `up kvaser` 1; `mode canmain libusb`, then `socketcan --wait 10` | **Kvaser UP**; canmain back in socketcan | free |
  | 02:08, 02:13, 02:17 | `mode canmain libusb`, then `socketcan --wait 10`, each within 4 s | canmain back in socketcan | free |
- **What was left, and for how long.**
  - The Kvaser link, up at 500 kbit and so ACKing on CAN1, from 2026-11-18 19:15:09
    until can-tools' `canctl down kvaser` at 22:00:58 (2 h 46 min). That covers the
    charge-interposer tester's hand takes of 19:44:14-20:17:42,
    20:30:28-20:49:43 and 21:13:29-21:22:34, and the start of can-tools' hold at
    22:00:45. Their release notes say the Kvaser was untouched, which is true of
    them. An up kvaser link ACKs with no socket open (kvaser_link's measurement,
    2026-11-13), so the X2's spec 2.3 power-up rounds on CAN1 that those holds
    record had an ACKing node they do not name; whether that matters to them is
    the charge-interposer tester's call.
  - canmain in libusb mode (unbound from gs_usb) from 2026-11-18 19:01:22 until
    can-tools' `canctl mode canmain socketcan` at 22:01:19 (3 h). The same holds.
  - The Kvaser link up again from 2026-11-19 02:04:04 until this tester's
    `canmain_burst_check` took it down at 02:34 (the lock free until its take at
    02:31:42). That record names it.
- **Gen-inhibit records taken while one of these calls had the bench in a state
  the record does not name: none.** Between 2026-11-18 17:13 and 2026-11-19 02:17,
  the only gen-inhibit record is the short bench check
  `runs/benchcheck_9950f96_20261119T015215/` (01:52-01:55).
  - Its `canctl_roles_start.txt` names the Kvaser link up and canmain in libusb.
    `canmain_burst_check` took the Kvaser down, read back, before anything was
    sent.
  - The Kvaser had been up since the charge-interposer tester's 01:18:41; this
    tester's 01:38:50 call changed nothing. canmain was in libusb since can-tools'
    00:24:37. *Correction, tester 2026-11-19: this tester first reported the
    01:52 found-up Kvaser as caused by its 01:38:50 call; the journal shows it was
    already up.*
  - Just after the window, `runs/benchcheck_069714e_20261119T023416/` (02:31-02:37)
    found the Kvaser up from the 02:04:04 call. Its `canctl_roles_start.txt` names
    it, and the check took it down first.
  - No gen-inhibit hold fell inside the window: the bench log shows no
    gen-inhibit take between 2026-11-18 15:05 and 2026-11-19 01:51.
- **The fix** (`3f656785`, accepted by the gen-inhibit reviewer 2026-11-19).
  - Before `kvaser_bus` is imported, the child now also tripwires `subprocess`
    (`run`, `Popen`, `call`, `check_call`, `check_output`) for `sudo`, `canctl` and
    `ip`, and `socket.socket` for `AF_CAN`.
  - A self-test in the child must show each one tripping and a harmless command
    still running, and the check that no tripwire was reached now covers them.
  - Checked on a copy of `kvaser_bus` with the guard removed: every open trips at
    its first `canctl`, and the journal shows no `canctl` call.
  - `--can-fail` at `9e7650fc` (the fix, with can-tools pin `0f9c85f` merged),
    2026-11-19 02:53:11-03:00:18: 99 of 99 mutants killed, control passing. Over
    that window the journal shows 0 `canctl` calls from the tester's worktree.
- **Run files:** `runs/incident_canctl_20261119/` (`README.md`: every `sudo canctl`
  call over the window from the journal, the bench log's holds, and the can-fail
  run at the fix with its canctl count).

**The full bench run on `743cf7cd`, its nine clean passes: B15, B16, B17a,
B18, B23, B26, B17b, B19absent and B19page -- tester, 2026-11-19 03:25-05:30,
madhouse-debian, harness `549d8542`. Result: all nine PASS.** Spec 10 (B15),
spec 11 (B16, and the prod page, B19page), and the 12.4 rows "the page names
the binary" (B17a, B17b), "0x7F3 carries the build id", bench half (B18),
"test-only features are inert in the build that will fly", absent half
(B19absent), "abort behaviour of the C3 TWAI" and "the abort-and-recheck loop"
(B23), and "receive backlog measured directly" (B26). The gen-inhibit reviewer
ordered these entries from this run (2026-11-19). B19 here covers the absent
half and the prod page only: B19present has no result from this run, so the
present half stays open. The run's other results are not results about the
device: the reviewer ruled them harness defects and witness-link limits
(2026-11-19), which topic `gen-inhibit/tester/run2-fixes` fixes before a full
re-run. B27 was NOT-RUN, deferred by the user, as in the baseline.
- **Run:** `gen-inhibit-bench_20261119T032517_cb08`, the whole suite, from the
  chain runner holding the lock itself. Overall: PASS 16, FAIL 14, VOID 3,
  ERROR 7, NOT-RUN 1. Code `743cf7cd` (`gi_images` and `gi_prod_dir` at
  `743cf7cd`, clean). Harness: `gi_tools` the detached worktree
  `gen-inhibit-tester-run-549d` at `549d8542`, clean. Settings
  `chain_settings_madhouse-debian.json`, against the `e3d9437` index. The run
  is paired with the emulated run `gen-inhibit-emulated_20261118T122828_eed2`
  on `743cf7cd`. Each of the nine ran in bench.slice, with no other wall-clock
  test on the host (`wallclock-others`).
- **Boards.** The runner's identify read both boards before and after every
  group (`progress.jsonl`), and every OTA took at attempt 1.
  - WiCAN, by group: `_man` `483bb940...` (man_app: B15, B16, B17a, B18);
    `_txabort` `0c7b3268...` (B23; OTA 05:01:51); `_rxq` `d9e8ae62...` (B26,
    B17b; OTA 05:09:14); the prod stand-in `483bb940...` (B19absent, B19page;
    OTA 05:15:06).
  - The run's start and end hooks PASS: found `wican_man`, mode 0, not
    latched.
  - Power: 12 V on OBD pin 16, as bench-holder.md has recorded since
    2026-10-09 (not re-read), and USB plugged at 1-3.1. No WiCAN console
    capture.
  - X2 (rule 6): `x2_app` `756d6714` for all nine checks, read before and
    after each of their groups.
  - Adapters by `canctl` role: the Kvaser (`kvaser`) on SocketCAN with
    hardware stamps; the PCAN (`pcanfd`); in B15 also `canmain`.
- **B15 (spec 10, the diag frames on the segment): PASS,** "3 adapters on
  the segment; non-ACK repeat on 0x7F9" (04:01:41-04:02:19). Each adapter
  read the device's diag frames in mode 3:
  - the Kvaser, ACKing: 20 frames;
  - the PCAN, listen-only: 20857, all `0x7F9`;
  - `canmain`, ACKing: 23.

  The Kvaser's and `canmain`'s frames decoded with schema 8, and `0x7F3`'s
  `diag_build_id` read `483bb940`. The PCAN's `0x7F9` repeat came before the
  first schema page, so it was not decoded. All three rows are adapters on CAN1, opened by role. The
  `gs_usb` row is `canmain`, CAN1's Innomaker, not the CAN2 Innomaker of the
  2026-11-18 correction under step 1d's entry. Recorded, not judged: the
  non-ACK repeat id `0x7F9` (baseline `0x7F9`; step 1d `0x7F3`).
- **B16 (spec 11, uptime and reset reason; the idle page-only baseline):
  PASS** (04:02:19-04:08:19). 360 polls, 0 unanswered, 0 wlan-down, 0
  stalled, no outage. No reboot: uptime 2219730 -> 2578786 ms, reset_reason
  SW. Baseline: 360 polls, 0 outages.
- **B17a (12.4, the page names the binary): PASS,** AS SPECIFIED. The page's
  `elf_sha256` is `_man`'s image hash `483bb940...`, and the other image is
  `_rxq` `d9e8ae62...`.
- **B18 ("0x7F3 carries the build id", bench half): PASS,** "MATCH, 7 rows
  ok". `diag_build_id` on the wire is `483bb940`, the first four bytes of the
  image hash and of the page's `elf_sha256`, which read the same before and
  after. `diag_fw_ver16` 7, `diag_schema_ver_build` 8.
- **B23 (12.4, abort behaviour of the C3 TWAI and the abort-and-recheck loop;
  the `_txabort` image): PASS** (05:02:31-05:03:11).
  - Contend: 72 aborted, 5 no effect, 1 lost-then-aborted.
  - Retry: 78 of 78 trials reached a free buffer, and the frame was on the
    wire on 0. The first abort was enough on 70.
  - Each flood phase's witness saw 3809-3978 frames/s, the full bus.
  - Baseline: contend 57 / 21 / 0; retry 78 of 78 free, 0 on the wire, the
    first abort enough on 69.
- **B26 (12.4, receive backlog measured directly; the `_rxq` image): PASS**
  (05:09:54-05:12:36). Max backlog 4 / 3 / 7 (idle a, idle b, loaded),
  against the baseline's 3 / 4 / 8. Dequeues with a backlog >= 2: 15 / 14 /
  1065 (baseline 15 / 17 / 11543). 82464 dequeues per arm, the replay's
  82464 frames.
  - The judge reads only the device's own receive samples.
  - The Kvaser witness saw 21387, 1550 and 5403 of the 82464 frames: the
    witness loss in the replay's process that this run found, which
    `run2-fixes` fixes. So the load on the wire is shown by the device's
    dequeue count, not by the witness. In the loaded arm the device also
    counted 3499 of the replay's 3500 `0x051` frames.
  - **Figure flagged, recorded, not judged** (B26 has no bound):
    `ge2_loaded` 1065. This is the first B26 with figures from madhouse-debian,
    and its sender is the PCAN under Linux. Every earlier B26 ran on
    NICKY-XPS and read 13186-16786 (`7bc9`, `c6a0`, `e25d`, `85e2`, `575b`,
    `a61f`).
  - In the loaded arm the device latched "engine turning while armed -- the
    inhibit did not hold" with `tx_ok` 670, as in `575b` (the replay is
    passive).
- **B17b (12.4, the page names the binary; `_rxq`): PASS,** AS SPECIFIED.
  The page's `elf_sha256` is `_rxq`'s image hash `d9e8ae62...`, and the other
  image is `_man`.
- **B19absent (12.4, test-only features inert in the build that will fly,
  absent half): PASS,** "all five absent on prod" (05:15:46-05:21:31):
  `RESTART_GUARD_TEST`, `GI_INSTRUMENT_ABORTWIN`, `OTA_HEALTH_FAULT_INJECT`,
  `GI_INSTRUMENT_RXQ` and `TWAI_PORT_TXABORT_PROBE`, each absent with the
  device still running.
  - `GI_INSTRUMENT_RXQ` was undetermined on the first pass ("page_key:rxq
    None"; the device had latched "error-frame rate exceeded"). It PASSed with
    the 30 s gap, the same shape as the baseline, which was undetermined
    twice.
- **B19page (spec 11, the prod page): PASS,** AS SPECIFIED. The page's
  `elf_sha256` is `483bb940`.
- **Identity rows, labelled, not compared:** B17a `483bb940` (baseline
  `25f50ac0`); B17b `d9e8ae62` (`f9de9de9`); B19page `483bb940`
  (`f3957789`); B18 `diag_build_id` `483bb940` (baseline schema 7
  `diag_git_hash` `0x2FE85674`).
- **Run files:**
  `runs/chain/gen-inhibit-bench_20261119T032517_cb08/` (`index.txt`,
  `progress.jsonl`, `board_state.txt`). The checks: `man_app/{B15,B16,B17a,B18}/`,
  `txabort/B23/`, `rxq/{B26,B17b}/` and `prod/B19absent/`.
- **2026-11-20:** all nine passed again in run (C), entry below.

**The full re-run (C) on `743cf7cd` -- tester, 2026-11-20 12:25-14:52, madhouse-debian,
harness `21753d42`, with its triage re-runs to 16:26. Result: PASS 31, FAIL 1 (B10), VOID 5
(B3, B24, B9, B8, B22), INCONCLUSIVE 1 (B6), NOT-RUN 1 (B27); after the triage re-runs, B3 and
B6 PASS, B10's arm A4 repeated (the re-run VOID on the bursts' timing), B9, B8 and B22 VOID
again, B5 ERROR on its re-run, B24 VOID again on another arm.** The gen-inhibit reviewer's answer to 78d24573
ordered it, from `gen-inhibit/main` `21753d42`, after topics (B), (D), (E), the drain topic and
queue-bound fixed run 2's harness findings. Run 2's nine passes on this code
(`gen-inhibit-bench_20261119T032517_cb08`, entry above) all passed again here. It gives the first B11raw and B21 results under the wording of 2026-11-19
(entry above).
- **Run:** `gen-inhibit-bench_20261120T122548_6846`, the whole suite, from the chain runner
  holding the lock itself. Code `743cf7cd` (`gi_images` and `gi_prod_dir` at `743cf7cd`, clean).
  Harness: `gi_tools` the detached worktree `gen-inhibit-tester-run-2175` at `21753d42`,
  clean, with its pinned can-tools `262ba7b` and canre `ffbe029` installs. Settings
  `chain_settings_madhouse-debian.json`, against the `e3d9437` index. Paired with the emulated
  run `gen-inhibit-emulated_20261118T122828_eed2` on `743cf7cd`. Every check ran in bench.slice
  (placement read back, CPUWeight 2000), with no other wall-clock test on the host
  (`wallclock-others`).
- **Boards.** The runner's identify read both boards before and after every group
  (`progress.jsonl`), and every OTA took at attempt 1.
  - WiCAN, by group: `_man` `483bb940...` (man_app, man_held, man_jammer, man_slcan; on it
    already at the start, not flashed); `_rt` `4fd1c77d...` (OTA 13:51:40); `_txabort`
    `0c7b3268...` (14:02:10); `_abortwin` `8b415a6c...` (14:06:01); `_rxq` `d9e8ae62...`
    (14:09:34); the prod stand-in `483bb940...` (14:15:29).
  - The start and end hooks PASS: found `wican_man`, mode 0; latched False at the start, True
    at the end.
  - Power: 12 V on OBD pin 16, as bench-holder.md has recorded since 2026-10-09 (not re-read),
    and USB plugged at 1-3.1. No WiCAN console capture.
  - X2 (rule 6): `x2_app` `756d6714` (flashed 12:26:42 and 13:51:46) for man_app, rt,
    txabort, abortwin, rxq and prod; held in ROM, not driving TX (`x2_hold.py`), for man_held;
    the jammer row `ea762c62044cb5c6` under custody, verify-row PASS, for man_jammer; `slcan`
    (`slcan:V0101:NF001`, flashed 14:46:33) for man_slcan, which it was left on.
- **Triage.** `resource-log window` over every non-PASS check's window was clean: bench.slice
  0.8-1.3 cores, CPU pressure (some) at most 0.5 %, no memory or IO pressure, 0 major faults,
  no swap traffic, no logger gap over 5 s (12:35:36-12:51:44 for B3/B24's arms, 13:45:33-13:47:44
  for B9 and B10, 14:24:13-14:44:43 for B5_B8, 14:47:11-14:51:06 for B22). With no marks set
  (the user's 11-16 ruling), each timing failure counts as unclear and was re-run once.
  - **Re-run 1:** `gen-inhibit-bench_20261120T150438_b840` (15:04:38-15:56:54), the same code,
    harness, settings and boards (WiCAN `_man` `483bb940...` throughout, not flashed; X2
    `x2_app` 15:05:33, held 15:24:48, the jammer row 15:27:50, `slcan` 15:50:56, each read
    back), checks B3arms, B3, B24, B9,
    B10, B5_B8 and B22. PASS 4, VOID 4, ERROR 3. B5_B8 is one check, so B5 and B7 ran again
    with B6 and B8. B24 reads B2app's arms as well as B3's, and B2app was not in this re-run's
    checks, so B24's desk step refused ("B24: no input files"), ERROR: not a result.
  - **Re-run 2:** `gen-inhibit-bench_20261120T155948_bdbe` (15:59:48-16:26:49), the same code,
    harness, settings and WiCAN (`_man` `483bb940...`, not flashed; X2 `x2_app`, read back, and
    left on it), checks B2app, B3arms and B24: B24 with both its inputs. PASS 2 (B2app,
    B3arms), VOID 1 (B24). The record was clean over its B3 arms (16:08:52-16:24:56):
    bench.slice 1.3 cores, CPU pressure (some) at most 0.3 %, 0 major faults, no logger gap
    over 5 s.
  - The record was clean over re-run 1's non-pass windows too: bench.slice 0.9-1.3 cores, CPU
    pressure (some) at most 0.3 %, no memory or IO pressure, 0 major faults in bench.slice, no
    logger gap over 5 s (15:24:49-15:25:40 for B9, 15:25:40-15:26:58 for B10, 15:28:43-15:49:07
    for B5_B8, 15:51:34-15:55:29 for B22). So each repeat stands as a result (AGENTS.md,
    "Parallel work on the shared machine", point 2).

**Passes**
- **B1 (3.2, SLCAN refused and counted): PASS,** 20 of 20 refused and counted (12:28:02-12:28:44).
  Baseline 20 of 20.
- **B2 (5, interposer presence controlled; which 0x051 is ours): PASS,** 6 arms as specified, X2
  frames 0. INHIBIT 0x051 670 / 670 (x2_app / held), RESPOND probes 2991 / 2994, long arm 29977
  / 29977 (baseline 670 / 670, 2994 / 2992, 29977 / 29977).
- **B4 (5.2, skipped inhibit frames and trip 7, bench half): PASS,** 6 of 6.
- **B11 (12.4, quiesce under traffic): PASS,** 6 arms as specified, `tx_ok` agrees with every
  INHIBIT span.
- **B11raw (spec 3, a mode change in the request-to-reply window): PASS,** 12 of 12 POSTs, the
  first B11raw pass under the wording of 2026-11-19 (12:58:22-13:03:26). Each: HTTP 200, the
  witness as specified in the window (no device `0x051` after the reply into OBSERVE; no stale
  answer, judged from the request's send, after the POST back), the page's mode after the reply
  the requested mode, and `drv.msgs_to_rx` 0 after every reply (bound 0). Windows 17.0-38.0 ms;
  clock margins 0.34-13.97 ms. The three POSTs into OBSERVE from PASSIVE have nothing to show
  on the witness (NOT APPLICABLE) and pass on the mode and the queue.
- **B13 (12.4, PASSIVE on the wire; 11, the page fields): PASS,** 8 arms ok, booking 8 of 8.
- **B14 (12.4, OBSERVE is listen-only in hardware; X2 held): PASS,** as specified, 5 mode
  checks, mode 0 at the end.
- **B15 (spec 10, the diag frames on the segment): PASS,** 3 adapters on CAN1 by role: the
  Kvaser 19 frames, the PCAN 21145, `canmain` 23 (13:09:11-13:09:48). Recorded, not judged: the
  non-ACK repeat id `0x7F8` (run 2 and baseline `0x7F9`; step 1d `0x7F3`).
- **B16 (spec 11, uptime and reset reason; the idle page-only baseline): PASS,** 360 polls, 0
  unanswered, 0 wlan-down, 0 stalled, no outage (13:09:48-13:15:49).
- **B17a / B17b (12.4, the page names the binary): PASS,** AS SPECIFIED: the page's
  `elf_sha256` is `483bb940` on `_man` and `d9e8ae62` on `_rxq`.
- **B18 ("0x7F3 carries the build id", bench half): PASS,** "MATCH, 7 rows ok",
  `diag_build_id` `483bb940`.
- **B20 (the interlock, bench): PASS,** 28 of 28 AS SPECIFIED (13:16:07-13:34:10).
- **B25 (7.1, key state, on B20's `rekey-long`): PASS,** AS SPECIFIED, 0 of 18387 frames
  against rule 1; gate open 203.483 s, resumed 203.486 s (baseline 0 of 18396; 203.479 /
  203.482 s).
- **B21 (spec 9.3, the OTA refusal at the client): PASS,** both guards hold
  (`bench_ota_guard`, `bench_flash_guard`), and the raw probe's 20 of 20 multipart and 20 of 20
  image requests (5 each in OBSERVE, RESPOND, INHIBIT and PASSIVE) got the whole 403 with its
  reason ("gen_inhibit is armed; POST /gen_inhibit_set?mode=0 first") at the client, the
  uptime unbroken across each (13:34:11-13:37:14). The first B21 pass under the wording of
  2026-11-19. Endings, recorded, not judged: all 40 open at the client's close. No spec 3
  finding.
- **NONODE (12.4, a bus with no other node; X2 held): PASS,** 3 main arms, trip "error-frame
  rate exceeded", latched by 0.456 s at most, no answer stuck.
- **B12 (12.4, no error frames at a restart; `_rt`): PASS,** 7 of 7 cycles, 0 error frames, 7
  restart records `did_twai` and `did_stdby` true, `timed_out` false; SW 2.339-2.357 s and PANIC
  0.601-0.609 s (13:52:26-13:59:37).
- **B23 (12.4, abort behaviour of the C3 TWAI; the abort-and-recheck loop; `_txabort`):
  PASS,** contend 67 aborted / 10 no effect / 1 lost-then-aborted; retry 78 of 78 free, 0 on the
  wire, the first abort enough on 70 (14:02:50-14:03:29). abortwin (`_abortwin`): PASS, rc 0.
- **B26 (12.4, receive backlog measured directly; `_rxq`): PASS,** max backlog 3 / 4 / 8 (idle
  a, idle b, loaded), 82464 dequeues per arm (14:10:14-14:12:59); baseline 3 / 4 / 8. Figure
  flagged, recorded, not judged (no bound): dequeues with a backlog >= 2, 21 / 25 / 2559 (run 2
  15 / 14 / 1065; NICKY-XPS runs 13186-16786 loaded).
- **B19 (12.4, test-only features inert in the build that will fly): PASS.** B19present: all
  four present on their test builds (`RESTART_GUARD_TEST`, `TWAI_PORT_TXABORT_PROBE`,
  `GI_INSTRUMENT_ABORTWIN`, `GI_INSTRUMENT_RXQ`), the half run 2 left open. B19absent: all five
  absent on the prod stand-in (those four and `OTA_HEALTH_FAULT_INJECT`), 14:16:09-14:21:54.
  B19page (spec 11, the prod page): AS SPECIFIED, `elf_sha256` `483bb940`.
- **B7 (7, a bus-off re-arm): PASS,** 3 of 3 arms AS SPECIFIED; re-run 1 the same, 3 of 3.
- **B3 (the go-live batch): PASS on re-run 1,** 20 arms, stale 0 (15:06:52-15:23:27). Run C
  was VOID on one of its 20 arms: observe arm 3, attribution disagrees, device `tx_ok` 1502
  against the witness's 1500 device `0x051` from scheduled POST 1 at 20.0 s, in mode 3.
  Recorded, not judged: re-run 1's first answer after the POST, OFF 149.8-150.9 ms, OBSERVE
  46.3-82.7, PASSIVE 20.6-29.4, RESPOND 40.3-60.4 (baseline OFF 150.9-151.0, OBSERVE 15.4-36.0,
  PASSIVE 15.8-28.6, RESPOND 14.8-34.0).
- **B6 (7, trip 6 at the threshold): PASS on re-run 1,** 10 of 10 arms over 5 runs. Run C was
  INCONCLUSIVE, 9 of 10: `B6_threshold_n10_lead1.5` arm 1 had 9 pulses, not 10, "delivery was
  not one error frame per pulse".

**Not passes**, observed against the spec
- **B10 (12.4, "Trip 6 before going live, after a re-arm"; X2 held): FAIL in run C
  (13:46:24-13:47:44); VOID on re-run 1 on the bursts' timing (15:25:40-15:26:58), with the
  same observation on arm A4.** In both runs A1 (expired) and A3 (recent) were as specified,
  and A0 and A2 were the flood arms (trip "error-frame rate exceeded").
  - Observed, arm A4 (expired; armed 15.064 s after the burst in run C, 15.086 s in re-run 1):
    device `0x051` on the wire 6 frames in run C, 4 in re-run 1, then the abort "0x617 went
    stale while live -- VCM fault flag lost". Run C's record: mode 3, blocked "no fresh 0x051",
    then "SoC not yet valid (contactors)", then from 62.943 s blocked and aborted on "0x617
    went stale while live"; `tx_ok` 6, the first `0x051` 0.139 s after the arm. So A4 went
    live and was aborted.
  - Spec 7, "How the window moves" and "Checked before going live": A4, re-armed 15 s after
    A2's last error frame, must go live, as A1 did in both runs. The judge's line for A4,
    "NOT as specified: never live", misreads a live-then-aborted arm as never live (the
    gen-inhibit reviewer, 2026-11-20). A4's history and prediction were as specified in both
    runs. **B10 stays open with the reviewer** until the judge tells the two apart and the
    witness settles whose the abort is (0x617 on the wire around it).
  - Re-run 1's VOID: "burst 5 ms: span 2.494 ms, under 3 ms". Run C's B9 has the same span,
    2.494 ms, on a 20 ms burst.
- **B5 (7, trip 6, arm (i), a steady jam): PASS in run C; ERROR on re-run 1**
  (15:28:43-15:49:07). 200/s 3 of 3; 500/s 8 of 9 PASS and 1 ERROR:
  `B5_livetrain_p2000_run3` arm 1, "the device was not live at the start of the jam; device
  reset seen at [-3.143] s rel jam; reset reason none". "Reset reason none" means no page
  reading after it showed a new boot, which the reset rule (`arm_rulings.py`, the reviewer's
  answer to ba92acf0) leaves ERROR, for the reviewer. Run C: 200/s 3 of 3, 500/s 9 of 9.
- **B9 (12.4, "Error frames from an adapter at the wrong bit rate"; X2 held): VOID in both
  runs on the bursts' timing,** so the stimulus was not as the check needs and the device was
  not judged. Run C (13:45:33-13:46:24): burst 1 ms span 12.246 ms, over 6 ms; burst 20 ms
  spans 9.980, 13.619 and 2.494 ms, under 18 ms. Re-run 1 (15:24:49-15:25:40): burst 1 ms span
  8.958 ms, over 6 ms; burst 20 ms span 29.834 ms, over 25 ms. Not a result about the device.
- **B8 (11, `bus_on`, the bus-off half): VOID in both runs on the witness clock,** "socketcan
  host stamps (kernel receipt time, not the Leaf's hardware clock): stamp check pending ()",
  not judged (spec 12: the timing witness stamps at the wire, in hardware). Not a result about
  the device.
- **B22 (5.2, case 3 saturation, bench half; X2 `slcan`): VOID in both runs, both arms:** the
  adapter refused replay frames, so the stimulus was incomplete, and attribution over the arm
  disagrees.

  | arm | refused | commands never on the wire (of 8998) | device `tx_ok` | provenance device `0x051` |
  |---|---|---|---|---|
  | run C a | 6677 | 287 | 8696 | 17359 |
  | run C b | 6664 | 289 | 8694 | 17355 |
  | re-run 1 a | 6677 | 282 | 8699 | 17367 |
  | re-run 1 b | 6617 | 288 | 8695 | 17359 |

  Baseline: both arms 8983 of ours sent and seen, provenance double 0. Not a result about the
  device.
- **B24 (4.1, B0 held): VOID in run C,** 21 arms AS SPECIFIED and 1 void, B3's observe arm 3
  above. **VOID again on re-run 2** (16:25:20), 21 AS SPECIFIED and 1 void, on another arm:
  `B3_e4_golive_passive_743cf7cd_20261120T162054_arm1`, attribution over the arm disagrees,
  device `tx_ok` 1501 against the witness's 1500 device `0x051` from scheduled POST 1 at
  20.0 s, in mode 3. In both void arms every command in front was `0x08` ("consistent, not
  discriminating"). The void is the arm's attribution, so this is not a result about the
  device.
- **B27: NOT-RUN,** deferred by the user (needs the user at the bench, USB plugged in), as in
  the baseline.
- **Harness finding, recorded:** on every B5 livetrain arm, in run C and in re-run 1, "witness
  after the jam: NOT SAMPLED (AttributeError: 'SocketcanHwBus' object has no attribute
  '_read_handle')". `x2_jam_cases.py`'s status sampler reads the Kvaser's canlib handle, and on
  madhouse-debian the Kvaser opens on SocketCAN.
- **Identity rows, labelled, not compared:** B17a `483bb940` (baseline `25f50ac0`); B17b
  `d9e8ae62` (`f9de9de9`); B19page `483bb940` (`f3957789`); B18 `diag_build_id` `483bb940`
  (baseline schema 7 `diag_git_hash` `0x2FE85674`).
- **Run files:** `runs/chain/gen-inhibit-bench_20261120T122548_6846/` (`index.txt`,
  `progress.jsonl`, `record.json`, `board_state.txt`, a folder per group and check);
  re-run 1 `runs/chain/gen-inhibit-bench_20261120T150438_b840/` (`man_app/{B3arms,B3,B24}`,
  `man_held/{B9,B10}`, `man_jammer/B5_B8`, `man_slcan/B22`); re-run 2
  `runs/chain/gen-inhibit-bench_20261120T155948_bdbe/` (`man_app/{B2app,B3arms,B24}`).
- **2026-11-20:** B10, B9, B8, B22 and B24 PASS in the rulings re-run on the same code, entry
  below.

**The rulings re-run on `743cf7cd` -- tester, 2026-11-20 19:04-20:05, madhouse-debian,
harness `e42c5707`. Result: PASS 10 (B2app, B3arms, B24, B9, B10, B5, B6, B7, B8, B22).** The
gen-inhibit reviewer's rulings on run (C)'s harness fixes ordered it: B10, B9, B8, B22 and B24
(with B2app) again on `743cf7cd` `_man`, from gen-inhibit/tester/runc-rulings (`69ebea39..e42c5707`).
The harness changes it runs under: B10's expired arm that went live and was then aborted other
than by trip 6 is as specified, its abort on the arm line; B24 places an arm's count window from
its POST's own send; the Kvaser witness's status is sampled on SocketCAN, with the fields
SocketCAN does not give recorded as not available; B9's and B10's runs are VOID on a burst only
when it drew no witness error frame, each burst's span and close recorded, not judged, with the
source's own state; B8's baseline lasts at least 5 s and until the stamp check leaves pending
with at least 20 data frames judged, at most 15 s, VOID past it.
- **Run:** `gen-inhibit-bench_20261120T190456_0d8c`, a subset run (B2app, B3arms, B24, B9, B10,
  B5_B8, B22), from the chain runner holding the lock itself. Code `743cf7cd` (`gi_images` and
  `gi_prod_dir` at `743cf7cd`, clean). Harness: `gi_tools` the detached worktree
  `gen-inhibit-tester-run-e42c` at `e42c5707`, clean, with its pinned can-tools `49eb777` and
  canre `ffbe029` installs. Settings, index and the paired emulated run as in run (C). Every
  check ran in bench.slice (placement read back, CPUWeight 2000), with no other wall-clock test
  on the host (`wallclock-others`).
- **Boards.** The runner's identify read both boards before and after every group
  (`progress.jsonl`).
  - WiCAN `_man` `483bb940...` throughout, not flashed. The start hook PASS (found `wican_man`,
    mode 0, latched False), the end hook PASS (mode 0, latched True). Power 12 V on OBD pin 16,
    as bench-holder.md has recorded since 2026-10-09 (not re-read), and USB plugged at 1-3.1.
    No WiCAN console capture.
  - X2 (rule 6): found on the charge-interposer's `vport_bench`; `x2_app` `756d6714` (flashed
    19:05:50) for man_app; held in ROM, not driving TX (19:31:43), for man_held; the jammer row
    `ea762c62044cb5c6` under custody, verify-row PASS (19:34:47), for man_jammer; `slcan`
    (`slcan:V0101:NF001`, flashed 19:59:26) for man_slcan, which it was left on.
- **Triage:** no check failed, VOIDed or ran out of time, so none was needed.

**Passes**
- **B10 (12.4, "Trip 6 before going live, after a re-arm"; X2 held): PASS,** 3 of 3 judged arms
  as specified (19:32:35-19:33:54). A1 (expired, armed 15.058 s after the burst) and A4 (expired,
  16.362 s): history and prediction as specified, device `0x051` on the wire from 0.317 s and
  0.378 s after the arm, 1190 and 1185 frames, no abort. A3 (recent, 2.574 s): as specified,
  device `0x051` 0, abort "error-frame rate exceeded". A0 and A2 were the flood arms (trip
  "error-frame rate exceeded"); their bursts drew 48 and 38 witness error frames. Recorded, not
  judged: the bursts' spans, 16.333 ms (A0) and 2.494 ms (A2), each asked 5 ms; the host's
  `0x617` quiet before A4's arm 1.507 s.
- **B9 (12.4, "Error frames from an adapter at the wrong bit rate"; X2 held): PASS,** 9 bursts,
  every one drew witness error frames (7-61; 199 in all), trip 6 latched at +1.40 s, TEC max 0
  on the page (REC max 118), 19:31:45-19:32:35. Recorded, not judged, each burst's span on the
  witness (first to last error frame): asked 1 ms, 6.994, 3.018 and 7.220 ms; asked 5 ms, 7.046,
  0.268 and 6.984 ms; asked 20 ms, 18.298, 24.191 and 8.261 ms. The source's own state, read
  during each hold: no frame of any kind read from `canmain` on any burst (bus_off False, 0
  restarts, 0 error frames). Whether gs_usb over libusb passes `canmain`'s own error frames to
  the host is not established on this bench, so that line shows nothing about the source's
  state.
- **B8 (11, `bus_on`, the bus-off half): PASS,** train 3 of 3 PASS: the page agrees with
  `drv.state` on all 183, 174 and 173 readings, each arm's readings running then bus_off. The
  control arm (no train) INCONCLUSIVE, "no sample read bus_off, so the bus-off half was not
  exercised". Baselines 6.608, 5.006, 5.007 and 5.006 s, each ending with the stamp check
  "hardware" on 20 data frames. Run (C) and its re-run were VOID with the stamp check pending.
- **B5 (7, trip 6, arm (i), a steady jam): PASS,** 200/s 3 of 3, 500/s 9 of 9. **B6 (7, trip 6
  at the threshold): PASS,** 10 of 10 arms over 5 runs. **B7 (7, a bus-off re-arm): PASS,** 3
  of 3 arms AS SPECIFIED. B5_B8 ran 19:35:39-19:57:36.
  - The Kvaser witness's status on SocketCAN, on every B5 livetrain arm (run (C)'s finding,
    "NOT SAMPLED", gone): 15-17 samples after the jam and 28-31 over the arm; states seen
    error-active, and error-passive in some arms; never bus-off; over each whole arm the
    link's bus_error counter rose by 986-2353 and error_passive by 0-37. TEC, REC and overruns
    read "not available on socketcan". No judge reads them.
- **B22 (5.2, case 3 saturation, bench half; X2 `slcan`): PASS,** both arms provenance clean,
  8983 of ours each, 0 `tx_fail`, 0 error frames, 0 counter faults; response mean 580 us in
  both, max 1260 and 1046 us; load at least 92.7 % (20:00:04-20:04:07). Baseline: both arms
  8983 of ours sent and seen, provenance double 0. Run (C) and its re-run were VOID.
- **B24 (4.1, B0 held): PASS,** 22 arms as specified, and 1 RESPOND arm with no device frames
  (B2's presence RESPOND arm, `x2_app`, device `0x051` 0), other 0 (19:30:22). Every command in
  front was `0x08` ("consistent, not discriminating").
- **B2app** (19:07:09-19:14:01) **and B3arms** (to 19:29:58), B24's inputs: PASS, rc 0.
- **Run files:** `runs/chain/gen-inhibit-bench_20261120T190456_0d8c/` (`index.txt`,
  `progress.jsonl`, `record.json`, `board_state.txt`, `man_app/{B2app,B3arms,B24}`,
  `man_held/{B9,B10}`, `man_jammer/B5_B8`, `man_slcan/B22`).

**Port step 3: madhouse against the Windows records -- tester, 2026-11-21, records only, no
build and no bench run. Result: of the 44 check ids, 43 have the same standing verdict on both
sides. B6 differs: PASS on madhouse, INCONCLUSIVE in both Windows runs. Figures differ on 14
checks, below.** This is port plan step 3's third acceptance bullet
(`esp-shared-code-port.md`, step 3, "Accepted by"): gen-inhibit's whole emulated and bench suites
on madhouse-debian, compared with the existing Windows records, every difference listed with
its cause. The basis was ruled by the user on 2026-11-21 (option 1), relayed by the gen-inhibit
reviewer: run C on `743cf7cd` with its triage re-runs and the rulings re-run, and the emulated
`eed2`, against step 1d's Windows runs. No new Windows run was made (user, 2026-11-11).
- **The two sides.**
  - **madhouse-debian, code `743cf7cd`.** The emulated run
    `gen-inhibit-emulated_20261118T122828_eed2` used harness `48725311`. The bench runs were run C
    `gen-inhibit-bench_20261120T122548_6846` and its re-runs `_150438_b840` and `_155948_bdbe`,
    all at harness `21753d42`, and the rulings re-run `_190456_0d8c` at harness `e42c5707`. The
    shared core was the checkout of shared/main tag `2026.11.16.5` (`d2b545c8`), on each run's
    `PYTHONPATH` in its `record.json`. The settings' `_comment` still names `2026.11.11.6`: stale
    text, not what ran. Each run's own entry, above and in section 12.4, has its conditions.
  - **NICKY-XPS, code `7f2d5032`, step 1d.** The emulated run was
    `gen-inhibit-emulated_20261105T093208_cabf`. The bench runs were
    `gen-inhibit-bench_20261105T095243_575b` and its automatic re-run `_121949_a61f`. All three
    used harness `bb09b5f6`, with the core from the `b9896a0` archive (`scratch/gen-inhibit-core/b9896a0/shared/bench`
    on `PYTHONPATH`). Step 1d's entry above has the conditions.
- **How the list was made.** `gen-inhibit/tools/run_pair.py` (committed with this entry, with its
  test `test_run_pair.py`: PASS, 0 FAIL, three can-fails failing as they must) prints both sides
  from the runs' `record.json` files, one block per check. A side's standing result is its last
  run that has the check: run C's re-runs and 0d8c over run C, and a61f over 575b. Its output is
  `runs/step3_diff_743cf7cd_vs_1d/pair.txt` (`README.md` beside it).
- **Causes.** A cause is written where the records or the commits show it: a harness change with
  its commit, a spec change, or the bench's adapters. Otherwise it is "not known", and the
  verified differences in how the check ran on the two sides are listed beside it. They are not
  claimed to explain the figure.
- **The firmware differs.** `7f2d5032` and `743cf7cd` are two gen-inhibit re-pins apart:
  - `5e28d6d3`: shared/main `2026.11.16.2`, shared/wican W12-W14 in the OTA upload (spec 9.2 items 4-6);
  - `a481019a`: `2026.11.16.3`, W7, so `/store_car_data` and `/upload/car_data.json` are not registered with AutoPID off.

  Between those pins, the only product source file that changed in the six vtrux components is
  `shared/wican/config_server.c` (`b781599d` W7, `f4414ba7` W12-W14). Beside it, shared/tools
  changed, and in `gen-inhibit/firmware/` only `idf_component.yml`, `dependencies.lock` and
  `shared_pins.json`. Rows the re-pins could touch name them as a possible cause.
- **Image identities, different by construction.** These are the descriptors' `elf_sha256`
  values, madhouse against Windows:

  | Image | madhouse | Windows |
  |---|---|---|
  | `_man` and the prod stand-in | `483bb940` | `f515a383` |
  | `_rt` | `4fd1c77d` | `6ab7bc79` |
  | `_txabort` | `0c7b3268` | `ca8fd54a` |
  | `_abortwin` | `8b415a6c` | `6c4047d3` |
  | `_rxq` | `d9e8ae62` | `ea3c0c05` |

  The same code builds to a different image hash on each host. `runs/man7f2d_madhouse` (2026-11-11)
  rebuilt `7f2d5032` `_man` on madhouse: image `2aac016d` against XPS's `f515a383`, but code hash
  (shared/tools R3) `bc49dce0...` on both, read in one call (`linux/image_xps_and_madhouse.json`).
  The images compared here also differ in code, by the re-pins above.
- **The host's path to the WiCAN's hotspot differs.**
  - madhouse-debian joins through the phone over adb: the settings in every madhouse run's
    `record.json` (6846, b840, bdbe, 0d8c) have `ota.wifi` `adb`, and the harness's joins and
    page reads go through that block (`gen-inhibit/tools/README.md`, the `suite_gen_inhibit.py`
    row).
  - NICKY-XPS used its own Wi-Fi adapter: the settings in 575b's and a61f's `record.json` have
    no `ota` block, and the laptop's WLAN-AutoConfig log over both runs
    (`runs/step1d_7f2d503_suite/wlan_events_1d.txt`) records its own WLAN connecting 9 times.

  This is a difference in how the checks ran, not a claimed cause. It bears on the rows timed
  over HTTP (the gen-inhibit reviewer, 2026-11-21): B3, B11raw, B21 and B14.

**Same standing verdict and the same figures** (madhouse | Windows)
- **H** (emulated): PASS | PASS. 25 steps, 81 goldens passed, 0 failed, 23 without, 9 replays
  identical, 0 invariant, fuzz and spec-expect failures, on both sides. madhouse alone records
  `host_mode` local, from harness `4b86ad66`: H and B15host carry the host mode, and they ran in
  local mode on madhouse.
- **B15host** (emulated): PASS | PASS, 2 modes with pages. `host_mode` as for H.
- **C2scan** (emulated): PASS | PASS, 4 of 4. Its runner was ported from PowerShell to Python in
  `5e51f80d`.
- **C1C7, C3:** NOT-RUN | NOT-RUN, their commands not yet in the suite.
- **B1:** 20 of 20 refused and counted. **B4:** 6 of 6. **B11:** 6 arms, `tx_ok` agrees with every
  INHIBIT span. **B13:** 8 arms, booking 8 of 8. **B14:** 5 mode checks, mode 0 at the end.
  **B16:** 360 polls, 0 outages. All PASS on both sides, with the same figures. B14 ran over
  each host's own path to the hotspot (above).
- **B17a, B17b, B18, B19page:** AS SPECIFIED on both sides. Only their identity figures differ (table above).
- **B19present, B19absent:** PASS on both sides, every switch the same: four present on their
  test builds, five absent on the prod stand-in.
- **B20:** 28 of 28 (run C) | FAIL 27 of 28 in 575b (`rekey-long` INCONCLUSIVE, a judge
  precondition not met; step 1d's entry), then 28 of 28 in a61f. The standing results are the same.
- **B24:** 22 arms as specified, 1 RESPOND arm with no device frames, other 0 (0d8c) | the same
  (575b).
  - On madhouse, run C was VOID on B3's void arm; b840 was ERROR, with no input files (B2app not
    selected); bdbe was VOID on another arm.
  - Cause of those: the arm's count window. From harness `259b2066` it is placed from the POST's
    own send (the reviewer's ruling on run C).
- **B7:** 3 of 3 AS SPECIFIED on both sides. It is jammer-driven, like B5, B6 and B8:
  - madhouse ran the jammer's ESP-IDF port, archive row `ea762c62044cb5c6` (the suite's jammer
    route since `6e01cab2`);
  - Windows ran the jammer image before that port;
  - from `6e01cab2`, B7 re-opens the replay's PCAN after its bus-off (`4e81b9c2`).
- **B8:** train 3 of 3 PASS, control INCONCLUSIVE (0d8c) | the same (575b, a61f).
  - On madhouse, run C and b840 were VOID on the witness clock: the Kvaser on Linux is read
    through SocketCAN (`466235e1`), and its stamp check was still pending when the baseline ended.
  - Cause: the Linux driver, and harness `e42c5707`. From it, B8's baseline lasts until the
    stamp check is settled (5-15 s).
- **B10:** 3 of 3 judged arms as specified, 2 flood arms (0d8c) | the same (575b).
  - On madhouse, run C was FAIL: A4 went live and was then aborted, and the judge read it as never
    live. b840 was VOID on the bursts' timing.
  - Cause: harness `9bc62714` (a live-then-aborted arm is as specified) and `e42c5707` (a run is
    VOID on a burst only when it drew no witness error frame), both the reviewer's rulings on run C.
  - The burst source differs: madhouse `canmain`, gs_usb over libusb (`8999d69d`, merged in
    `466235e1`); Windows the Kvaser at 250 kbit/s.
- **B2app, B2held, B3arms, B11arms, B11rawarms, B13arms, abortwin:** rc 0 on both sides.
- **B27:** NOT-RUN on both sides, deferred by the user.

**Different standing verdict**
- **B6 (spec 7, trip 6 at the threshold):** PASS 10 of 10 (b840 and 0d8c; run C INCONCLUSIVE 9 of
  10) | INCONCLUSIVE 9 of 10 in both 575b and a61f.
  - Each INCONCLUSIVE arm had a short stimulus, "delivery was not one error frame per pulse": run
    C `n10_lead1.5` arm 1; 575b `n10_lead5` arm 0; a61f `n9_lead5` arm 0.
  - **Cause: not known.** What differed:
    - the jammer image: madhouse the ESP-IDF port, row `ea762c62044cb5c6`. Its first bench run,
      2026-11-16, gave B6 10 of 10 with no arm short (entry above);
    - per-pulse poll stamps from `c81e28b9` (a poll is placed after a pulse only if it was sent
      after it);
    - the Kvaser witness on SocketCAN (`466235e1`).

**Same standing verdict, different figures** (madhouse | Windows)
- **B3:** PASS, 20 arms, stale 0, on both sides.
  - First answer after the POST (madhouse b840 | 575b and a61f), in ms:

    | Mode | madhouse (b840) | Windows (575b, a61f) |
    |---|---|---|
    | OFF | 149.8-150.9 | 150.9-151.0 |
    | OBSERVE | 46.3-82.7 | 15.4-47.6 |
    | PASSIVE | 20.6-29.4 | 4.5-28.8 |
    | RESPOND | 40.3-60.4 | 12.8-30.2 |

  - Run C was VOID on observe arm 3 (attribution: device `tx_ok` 1502 against the witness's 1500).
  - **Cause: not known.** What differed:
    - the e4 arm witness reads in a forked process (`cc8e41e6`, F1);
    - the Kvaser is read through SocketCAN (`466235e1`);
    - the host, and its path to the hotspot: the phone over adb on madhouse, the laptop's own
      Wi-Fi adapter on Windows (above);
    - firmware, possible cause: `config_server.c`, the device's HTTP server, changed in both
      re-pins (`5e28d6d3` W12-W14, `a481019a` W7).
- **B11raw:** PASS on both sides, judged differently. madhouse: 12 of 12 POSTs in the
  request-to-reply window, windows 17.0-38.0 ms, margin up to 13.97 ms, 3 with nothing on the
  witness. Windows: 3 PASS (0 on ACK), 3 not applicable, send to first byte 4.0-24.9 ms.
  - **Cause:** spec 3 changed (`f7865461`, user 2026-11-19), with harness topic (E) (`86d65e2e`)
    and `QUEUE_BOUND` 0 (`21753d42`).
  - The Windows pass was judged under the old wording. Under the new wording the `e3d9437`
    baseline re-judges VOID (entry "B11raw and B21 under the wording of 2026-11-19"). The
    figures are not comparable one for one.
  - The host's path to the hotspot also differed (above).
- **B15:** PASS on both sides, 3 adapters on the segment. Frames read (madhouse | 575b, a61f):

  | Adapter | madhouse | Windows |
  |---|---|---|
  | Kvaser | 19 | 21, 21 |
  | PCAN | 21145 | 22102, 22114 |
  | gs_usb | 23 | 15679, 15672 |

  The non-ACK repeat id, recorded, not judged: `0x7F8` on madhouse; `0x7F3` and `0x7F3` on Windows.
  - **Cause, gs_usb: the bench's adapters.** On Windows the gs_usb row was the bench's only
    Innomaker, on the X2's CAN2, reading CAN1 through the X2's bridge (correction of 2026-11-18,
    under spec 10). On madhouse it is `canmain`, CAN1's own Innomaker, ACKing.
  - The other counts and the repeat id: not known.
- **B21:** PASS on both sides, judged differently. madhouse: 20 of 20 multipart and 20 of 20 image
  requests, each with the whole 403 and its reason at the client. Windows: 20 of 20 each with a
  403, and RST 0.
  - **Cause:** spec 9.3 changed (`f7865461`), with harness topic (E) (`86d65e2e`).
  - The flash-route guard requires 404 for `/store_car_data` and `/upload/car_data.json` in every
    mode from `743cf7cd` (b21-404). It was not in the Windows harness.
  - Firmware, possible cause: `a481019a` W7 (those routes not registered) and `5e28d6d3` W12-W14
    (the OTA upload). The Windows pass is under the old wording, as for B11raw.
  - The host's path to the hotspot also differed (above).
- **B25** (on B20's `rekey-long`): PASS, 0 rule-1 violations, on both sides. madhouse | Windows:
  frames 18387 | 18396 and 18396; gate open 203.483 | 203.478 s; resumed 203.486 | 203.481 s.
  - **Cause: not known.** What differed: the Kvaser witness on SocketCAN (`466235e1`).
- **B2:** PASS, 6 arms, X2 frames 0, on both sides. Every count is the same except RESPOND probes
  with `x2_app`: 2991 on madhouse, against 2965 and 2995 on Windows.
  - **Cause: not known.** The value is inside the range the two Windows runs span.
- **B9:** PASS on both sides (0d8c | 575b, a61f). 9 bursts, trip 6 latched, on both sides.

  | | madhouse (0d8c) | Windows (575b, a61f) |
  |---|---|---|
  | witness error frames | 199 | 24102, 25668 |
  | per burst | 7-61 | 2549-3290 |
  | latched at | +1.40 s | +0.25, +0.22 s |
  | TEC max | 0 | 8, 0 |

  - Run C and b840 were VOID on the bursts' timing.
  - **Cause: the error-frame source, and harness changes.**
    - The source differs: madhouse `canmain`, gs_usb over libusb, at 250 kbit/s (`8999d69d`,
      merged in `466235e1`, the reviewer's condition 5); Windows the Kvaser at 250 kbit/s.
    - Bursts are measured on the PCAN's stamps from `12146b7a`.
    - From `e42c5707` a run is VOID on a burst only when it drew no witness error frame.

    How the source explains the counts is not established.
- **NONODE:** PASS, 3 main arms, 0 answers stuck, on both sides. Latch at most 0.456 s on madhouse,
  against 0.437 and 0.451 s on Windows.
  - **Cause: not known.** What differed: the replayer, `canmain` over libusb on madhouse
    (`8999d69d` in `466235e1`), the Kvaser on Windows. The PCAN witness is the same on both sides.
- **B12** (`_rt`): PASS on both sides, 7 of 7 cycles, 0 error frames. Boot times (madhouse | Windows):
  SW 2.339-2.357 s | 2.292-2.337 s; PANIC 0.601-0.609 s | 0.581-0.598 s.
  - **Cause: not known.** What differed:
    - B12's witness is a process witness (`e1d67472`, merged in `611cb84c`);
    - the Kvaser is read through SocketCAN;
    - the images differ by the re-pins.
- **B23** (`_txabort`): PASS on both sides; retry 78 of 78 free and 0 on the wire on both.
  madhouse | 575b, a61f:

  | Figure | madhouse | Windows |
  |---|---|---|
  | contend aborted | 67 | 70, 68 |
  | contend lost-then-aborted | 1 | 0, 0 |
  | retry aborted | 68 | 70, 71 |
  | first abort enough | 70 | 70, 71 |
  | retry lost-then-aborted | 10 | 8, 7 |

  - **Cause: not known.** The two Windows runs differ from each other by similar amounts.
- **B26** (`_rxq`): PASS on both sides, 82464 dequeues per arm.

  | Figure (idle a / idle b / loaded) | madhouse | Windows 575b | Windows a61f |
  |---|---|---|---|
  | max backlog | 3 / 4 / 8 | 5 / 4 / 10 | 4 / 2 / 9 |
  | dequeues with a backlog >= 2 | 21 / 25 / 2559 | 34 / 17 / 15853 | 14 / 3 / 16786 |

  - **Cause: not known.** What differed:
    - the replay's PCAN is opened through `pcan.ko`'s chardev on madhouse, and through
      PCAN-Basic on Windows;
    - the Kvaser witness is read through SocketCAN in a forked process (`466235e1`, `cc8e41e6`),
      against CANlib in the replay's process.
  - B26 has no bound. Run 2 on madhouse read 1065 loaded.
- **B5:** standing PASS on both sides. 200/s 3 of 3 on both. 500/s: 9 of 9 (0d8c) | 8 of 9 and 1
  inconclusive (a61f; 575b 9 of 9).
  - On madhouse, b840 was ERROR on one arm (`B5_livetrain_p2000_run3` arm 1), "reset reason
    none". **Cause: the judge, fixed in `aeab917d`** (in 0d8c's harness `e42c5707`, not in
    b840's `21753d42`). The reading at the arm's own POST could still show the previous arm's
    `tx_ok` count in mode 0, so the count's fall when the arm's own count restarted was read
    as a device reset. From `aeab917d`, a fall counts as a reset only between two answered
    readings that are both in mode 3.
  - **500/s, 9 of 9 against 8 of 9: cause not known.** What differed: the jammer image, as for
    B7.
- **B22** (X2 `slcan`): standing PASS on both sides. Both arms: 8983 of ours, provenance clean,
  0 `tx_fail`, 0 error frames, response mean 580 us. madhouse | Windows: response max
  1260 / 1046 us | 996 / 1191 and 1193 / 1025 us; load at least 92.7 % | 92.5 %.
  - On madhouse, run C and b840 were VOID: the adapter refused 6617-6677 replay frames an arm.
    0d8c refused none.
  - **Cause of the refusals: the harness, fixed in `204d48ec`** (in 0d8c's harness `e42c5707`,
    not in run C's and b840's `21753d42`).
    - `e4_device_calibration` took the replay's base before the `--flood-slcan` filler's ~3 s
      open. So each arm's replay started ~3.37 s behind its schedule, and about 8000 frames went
      to the PCAN back to back.
    - `pcan.ko`'s transmit queue holds 500 frames (`/sys/module/pcan/parameters/txqsize`).
    - From `204d48ec` the base is taken after the filler is open and started. An arm whose
      first frame goes out more than `START_LAG_MAX_S` (5 ms) late is VOID.
  - **The response max: not known.** What differed: the replay's PCAN, opened through
    `pcan.ko`'s chardev on madhouse and through PCAN-Basic on Windows; the Kvaser witness on
    SocketCAN (`466235e1`).
- **Run files:** `runs/step3_diff_743cf7cd_vs_1d/` (`pair.txt`, `README.md`). Each run's own files
  are named in its entry.

**Hypotheses for the not-known rows -- tester, 2026-11-21. Hypotheses the user has seen
(2026-11-21), not causes.** The user accepted port step 3 for gen-inhibit on this difference list
on 2026-11-21 (option 1, relayed by the gen-inhibit reviewer), with these hypotheses recorded
beside the not-known rows.
- **How they were made.** From the records and the harness code only: no bench run, no new
  measurement, no code change, no product source.
- **Re-making them.** The scripts that make the figures are in `runs/step3_diff_743cf7cd_vs_1d/`
  (`B3.py`, `B3_connect.py`, `B3_off.py`, `B5.py`, `B6.py`, `B9.py`, `B12.py`, `B15.py`,
  `B15_table.py`, `B25.py`, `B26.py`), and its `README.md` says how to run them. B2's and B22's
  figures are read straight from those runs' records, which the README names.
- **Format.** Figures are madhouse | Windows. Certainty: **H**, the records show it; **M**,
  consistent but not shown; **L**, plausible only.
- **Summary.** The phone-over-adb path to the hotspot is the common thread. No row points at the
  CAN drivers changing the device's behaviour.

- **B6.**
  1. The short pulse never reached the wire, and the device missed it as well as the witness.
     **(H)**
     - In each short arm the device's `bus_error` did not rise across the gap:
       - 575b `n10_lead5` arm 0: a miss at +5.95 s, with 6 errors over +5.09 to +6.82 s;
       - a61f `n9_lead5` arm 0: +3.25 s, with 3 over +2.78 to +4.33 s;
       - run C `n10_lead1.5` arm 1: +7.96 s, with 8 over +7.08 to +8.97 s.
     - The jammer still printed `PULSE k=` on time, and `DONE ... pulses=N end=complete`.
     - Neither jammer image checks that a pulse was sent before printing it:
       `tools/x2_jammer/src/main.cpp:236-241`, and the port's `jam_engine.c:263-267` on
       `shared/main`.
     - This rules out the SocketCAN witness and the `c81e28b9` poll stamps.
     - It is like the Windows desk points of 2026-10-20 (section 7 above). Two of the three
       undelivered pulses there were missing on both the witness and the device; the third was a
       witness miss.
     - To confirm why a pulse is dropped needs a per-pulse confirmation (the RMT done notice, or a
       scope on TXD). None is on file. (`B6.py`)
  2. The verdict difference is within run-to-run spread. **(M)**
     - madhouse went 9 of 10 in run C, then 10 of 10 in b840 at the same harness, then 10 of 10 in
       0d8c.
     - Short arms: 1 in 40 on the jammer's ESP-IDF port, against 10 in about 110 on the old jammer
       on Windows (Fisher p 0.29).
- **B5 (500/s).** The same judge defect on both sides. **(H)**
  - a61f's inconclusive arm (`B5_livetrain_p2000_run1` arm 0, "device reset seen at -3.114 s") has
    the shape of b840's ERROR arm:
    - `uptime_ms` unbroken;
    - `tx_ok` 323 in mode 0, the previous arm's count, then 1 in mode 3.
  - `aeab917d` fixed it. It is in neither `bb09b5f6` nor `21753d42`.
  - Spread: run C 9 of 9, b840 8 of 9 + 1, 0d8c 9 of 9 | 575b 9 of 9, a61f 8 of 9 + 1. (`B5.py`)
- **B9 (how the source explains the counts).**
  1. The device itself counted about 9x fewer errors on madhouse: `bus_error` delta 2588 | 22500
     and 23148. **(H)**
     - Per burst, madhouse follows the asked length: 1 ms 112-271, 5 ms 129-234, 20 ms 375-595.
       `canmain`'s hold, close minus open, was 6-26 ms.
     - Windows counted 2409-2790 per burst, whatever the length.
     - Inferred **(M)**: the old Kvaser `burst()` (`bb09b5f6`, `wrong_bitrate_errors.py:140-164`)
       sends with a 0.05 s timeout on a frame that never completes, so it stayed on the bus well
       past `--burst-ms`.
     - The Windows records keep no per-error times, so the span is not on file.
  2. madhouse's PCAN witness undercounts: 3-15 % of the device's count | 105-120 %. **(M)**
     - 0d8c's `pcan_states` shows error passive at every burst.
     - By the ruling of 2026-11-13 recorded in `x2_jammer.py`, the PCAN in normal mode under
       `pcan.ko` stops reporting bus errors once it is error passive.
  3. The +1.40 s latch is `canmain`'s libusb open inside the burst (`open_s` 1.239 s). The latch
     came 0.17 s after the errors began. **(H)**
  4. TEC max 0 | 8, 0 is within run-to-run spread. (`B9.py`)
- **B25.**
  1. The replay sat 5.8 ms later against t0 on madhouse, and the key edge, `gate_open` and
     `resumed` all move with it. **(H)**
     - Candidates for the constant **(M)**: `device_fit` maps SocketCAN stamps to the median
       receipt time (`witness_clock.py:259`), while the CANlib stamps were 6.2 ms ahead of
       receipt; or the replay started later.
  2. All 9 missing frames are at the start of span 1. **(H)**
     - The device's first `0x051` came 93 ms later: 0.168 | 0.075 s.
     - The rekey-long arm's POST took 75 ms | 26 and 39 ms, and the device went live after the
       second `0x592` rather than the first.
     - In the other 27 B20 cases the first device `0x051` is the same on both sides.
     - Inferred **(M)**: the slower POST over the phone path was applied after the first
       `0x592`, and the device went live on the next one. The case reboots the WiCAN into mode 0
       first, so the device receives nothing until the arm's POST is applied. Going live on the
       first `0x592` it received is spec 7 as written (the gen-inhibit reviewer, 2026-11-21).
       (`B25.py`)
- **NONODE.** Within run-to-run spread. **(H)**
  - The figure is the first latched page read's return minus the last VCM send
    (`nonode_bench.py:251`).
  - That read was sent at 0.400-0.425 | 0.399-0.434 s.
  - Page round trips have a median of 25-28 | 17-21 ms: the phone path adds about 8 ms.
  - The brackets overlap: 0.349-0.456 | 0.348-0.451 s.
- **B3.** The figure runs from replay frame k's wire time to the first device answer, both on the
  Kvaser's clock (`mode_entry_analysis.py:98-124`, `e4_device_calibration.py:982-993`). So it holds
  the POST's trip, any replay lateness, and the wait for the next VCM command, about 10 ms.
  1. The HTTP path, the phone over adb. **(H that it adds latency; M that it is the whole cause)**
     - POST duration medians: OFF 43-60 | 19-29 ms; OBSERVE 68-108 | 47-52; PASSIVE 51-64 |
       23-29; RESPOND 62-69 | 19-34.
     - TCP connect, from B11rawarms (12 per run): median 34.9 ms (run C) | 7.2 (575b) and 5.9
       (a61f). It is network-side, before any handler runs.
     - In RESPOND the device's answer after the command is the same, 0.34-1.02 | 0.34-1.36 ms. But
       the first answered command was scheduled 33-59 | 14.5-20.5 ms after frame k.
     - So the device went live later. It did not answer more slowly. (`B3.py`, `B3_connect.py`)
  2. A replay stall in OBSERVE. **(H for the effect, L for the mechanism)**
     - The answered command was 44-47 ms behind schedule in 4 of 5 b840 OBSERVE arms | 19-25 ms in
       the Windows arms that stalled.
     - `resource-log` over b840's B3 window is clean: bench cpu pressure at most 0.4 %.
     - Candidate mechanisms: the POST thread competing for the GIL with the busy-wait replay, or
       `pcan.ko`'s send path.
     - PASSIVE, and run C's OBSERVE, are within madhouse's own spread and overlap Windows.
  3. OFF's 1 ms is frame k's own wire timing. **(H)**
     - Against the schedule: +0.33 to +0.61 ms in 9 of 10 madhouse arms | -0.32 to -0.44 ms in all
       10 Windows arms.
     - Frame k is the first frame after `poster.start()`. (`B3_off.py`)
  4. The forked witness and the SocketCAN Kvaser: against it. **(L)** Both ends are on one clock,
     with a slope of about 53 ppm.
  5. The `config_server.c` re-pins. **(L)** They cannot be separated from the path without one
     image run over both paths, which is not on file.
- **B12.** The figure runs from host time before the restart POST to the median, over the page
  reads, of host time after the response minus uptime (`restart_error_frames.py:314-317,386-394`).
  No witness is involved.
  - The HTTP path, at both ends. **(M)**
    - SW median 2.340 | 2.320 s, partly within run-to-run spread: a61f alone spans 2.292-2.337 s.
    - PANIC median 0.607 | 0.582 s, with every madhouse cycle above every Windows one.
    - The +20 to 25 ms fits the ~29 ms longer connect.
    - Only run C ran B12 on madhouse.
    - To confirm needs per-read durations (only the first and last are kept), or the old `_rt`
      image run on madhouse. (`B12.py`)
  - The images, through the re-pins. **(L)**
- **B2.** Within run-to-run spread. **(H)**
  - The x2_app RESPOND count is 2999 minus the device's `tx_fail`: run C 8, bdbe 6, 0d8c 7 | 575b
    34, a61f 4.
  - A small excess on madhouse, 6-8 against 4-5, is **(L)** and would need more runs.
- **B22 (response max).** Within run-to-run spread. **(H)**
  - It is the device's own measurement: 913-1260 us over run C, b840 and 0d8c | 996-1193 us.
  - Responses over 800 us: 7-13 | 7-11 per arm.
  - Not asked, but on file **(H)**: the 92.7 | 92.5 % load difference is the X2's `slcan` filler.
    - Accepted: 176083 | about 161600, with queue full 56k | 66k.
    - The witness saw 397.3k | 382.8k frames, a gap matching the filler's.
    - Why Linux got more frames into the X2 is **(M)**: how it writes the serial port.
  - The device's `drv.arb_lost` was 124-184k | 46-50k per arm, with the response mean the same,
    580 us. Not judged.
- **B15.** The census opens each adapter alone for 6 s (`bench_census.py:83-128`). Once the Kvaser
  closes nothing ACKs, and the WiCAN repeats its one pending frame through the PCAN window.
  1. The PCAN count is set by which frame repeats, through its stuff bits. **(H)**
     - `0x7F8` and `0x7F9` are 140 bit times, `0x7F1` and `0x7F3` 133. Predicted ratio 0.950,
       observed 0.957.
     - Over 18 records the count groups by id: `0x7F8` Windows 20926-20994, run C 21145; `0x7F9`
       Windows 20910-20978, madhouse `cb08` 20857; `0x7F3` Windows 22074-22230.
     - A residual of about 1 % within an id has no consistent host sign. **(L)** (`B15.py`,
       `B15_table.py`)
  2. The repeat id is within run-to-run spread. **(H that it varies; M for the mechanism)**
     - Windows over 15 runs: `0x7F9` four times, `0x7F8` four, `0x7F3` four, `0x7F1` twice, `0x7F2`
       once.
     - Mechanism: the repeat id is the first page after the Kvaser stops ACKing, so it depends on
       where the close lands in the page cycle.
  3. The Kvaser count looks systematic: 21 in all 15 Windows runs, against 20 (`cb08`) and 19 (run
     C). `canmain` read 23 in its own 6 s window.
     - **(M)** that it is the Kvaser's SocketCAN open, which brings the link up inside the open
       (`kvaser_bus.py:540-582`). **(L)** for the mechanism, a late first frame.
     - Not on file: `bench_census.py` computes the first and last frame times (lines 99, 125) but
       does not write them.
- **B23.** Within run-to-run spread. **(H)**
  - Windows ranges over 15 runs, with run C in brackets: contend aborted 60-72 (67); contend
    lost-then-aborted 0-4 (1); retry aborted 69-74 (68); first abort enough 70-74 (70); retry
    lost-then-aborted 4-9 (10).
  - Run C's two figures just outside are one trial, since retry aborted plus retry
    lost-then-aborted is 78. `cb08` is inside every range.
  - Recorded, not shown to matter: the PCAN flood was 3942-3967/s | 3978/s, and `pcan.ko` refused
    340k | about 74k frames (its 500-frame queue).
- **B26.**
  1. The loaded arm's backlog follows the Wi-Fi load, which the host's path to the hotspot sets.
     **(H)**
     - The Hammer reads `/gen_inhibit` back to back (`e4_device_calibration.py:622-648`).
     - Requests in 35 s: `cb08` 114, run C 267 | 1248-1540.
     - Dequeues with a backlog of 2 or more: 1065, 2559 | 10667-16786. That is about 9.3-9.6 per
       request | 8.5-11.8, the same ratio over 16 runs. (`B26.py`)
  2. The idle arms and the loaded max backlog are within run-to-run spread. **(H)**
  3. `pcan.ko` against PCAN-Basic, and the forked Kvaser: no effect shown. **(L)**

**Stage (b) batch 2's full run on `db998aca` -- tester, 2026-11-23 16:24-19:43,
madhouse-debian. Result: the emulated suite PASS (H, B15host, C2scan; C1C7 and C3 the declared
skips). The bench suite: PASS 36, FAIL 2 (B4, B6), NOT-RUN 1 (B27, the declared skip). B4's
`l3s` case counted 2 late skips where the witness saw 3. B6's `n9_lead1.5` arm 1 latched trip
6 with at most 9 error frames on the wire in any window, one pulse counted 8 times. The
resource record is clean over both, so both count.** The gen-inhibit reviewer's batch 2
(the user's go 2026-11-23): the full emulated and bench suites on batch 2's image, with batch
1's 9.4 inhibitor bench (9.4 above), the Bluetooth keep and the OTA-reply count. `gi_commit`
is `db998aca`, `gen-inhibit/main`'s tip, as the reviewer ruled (2026-11-23); from batch 2's
firmware merge `db1bfeae` it changes only `gen-inhibit/README.md`. Run files: the chain runs
in `runs/chain/`, and the desk records in `runs/stage_b2_db998aca/bench/` (its `README.md`).
- **Code.** Built by the tester from clean worktrees detached at `db998aca`, ESP-IDF v5.4.2:
  the bench set with `baseline_builds --bench`, and the prod stand-in with the shared tooling's
  release build (every R row ok). The stand-in's image hash is `cd46e234...`, code hash
  `f9ce967d...`, the same code hash as at `db1bfeae`. The bench `_man` image is the same image,
  `cd46e234...`. The other bench images' code hashes, each the same as at `db1bfeae`: `_rt`
  `72fee584`, `_txabort` `694e6b46`, `_abortwin` `84bd041a`, `_rxq` `bd4b9b4b` (shared/tools
  `2026.11.16.3`'s R2 reading).
- **Harness.** `gi_tools` the detached worktree `gen-inhibit-tester-tools-4126f144`, at
  `gen-inhibit/tester/stage-b2` `4126f144`, clean, with its pinned can-tools `49eb777` and
  canre `ffbe029` installs; the checks' own clone `gi_checks_clone` at `db998aca`. Settings
  `chain_settings_madhouse-debian.json` (`gi_*` moved to `db998aca` and `4126f144` for this
  run), against the `e3d9437` index. The shared core was the checkout of shared/main tag
  `2026.11.16.5`.
- **The emulated suite.**
  - `gen-inhibit-emulated_20261123T162408_aa4c` (16:24, `gi_tools` at `db998aca`): H FAIL,
    B15host FAIL, C2scan PASS; a harness gap, fixed in `4126f144` (12.3, "Item 2, no diag
    misfit", above). Kept as the record.
  - `gen-inhibit-emulated_20261123T170423_d82f` (17:04-17:11, `gi_tools` `4126f144`): H PASS
    (25 steps; goldens 81 / 0 / 23 without; invariants 104 scenarios, 0 violating; the
    figures are in 12.3's entry); B15host PASS, every emitted diag frame decodes against the
    DBC; C2scan PASS, 4 of 4 steps; C1C7 and C3 NOT-RUN, their commands not yet in the suite.
    The runner exits 1 for the two skips, as before. The bench run below is paired with it.
- **Bench run:** `gen-inhibit-bench_20261123T171226_4b15` (17:12:26-19:42:57), the whole
  suite, from the chain runner holding the lock itself. `gi_images` and `gi_prod_dir` at
  `db998aca`, clean. Every check ran in bench.slice.
- **Boards.** The runner read both boards before and after every group (`progress.jsonl`).
  - WiCAN: found on an image no run names (`2aac016d...`, mode 0, nothing latched); loaded by
    OTA with `_man` `cd46e234...` (17:14:35) for man_app, man_held, man_jammer and man_slcan;
    `_rt` `343464da...` (18:40:42); `_txabort` `51ab1fc7...` (18:51:08); `_abortwin`
    `ad12c4fc...` (18:54:58); `_rxq` `bccf8159...` (18:58:28); the prod stand-in `cd46e234...`
    (19:04:22). Every OTA took at attempt 1 (the OTA-reply count is in 9.4 above). The end hook
    found `wican_man`, mode 0, latched True.
  - Power: 12 V on OBD pin 16, as bench-holder.md has recorded since 2026-10-09 (not re-read),
    and USB plugged at 1-3.1. No WiCAN console capture.
  - X2 (rule 6): `x2_app` `756d6714` (flashed 17:15:20 and 18:40:48) for man_app, rt, txabort,
    abortwin, rxq and prod; held in ROM, not driving TX (`x2_hold.py`, 18:27:32), for
    man_held; the jammer row `ea762c62044cb5c6` under custody, verify-row PASS (19:12:12),
    for man_jammer; `slcan` (`slcan:V0101:NF001`, flashed 19:36:48) for man_slcan, which it
    was left on.
- **Triage** (AGENTS.md, "Parallel work on the shared machine", point 2). `resource-log
  window` over B4 (17:41:40-17:42:40) and over B6's failing arm (19:25:35-19:27:12): CPU
  pressure (some) in bench.slice at most 0.8 % and 0.9 %, no memory or IO pressure, 0 major
  faults, no swap traffic, no logger gap over 5 s (`bench/resource_B4.txt`,
  `bench/resource_B6_n9_lead1.5.txt`). Clean, so both count as results.
  - Recorded, not judged: bench.slice ran at 4.6-5.4 cores over those windows and 4.6 on
    average over the whole run (`bench/resource_run.txt`), against 0.8-1.3 in run C of
    2026-11-20. Other sessions' wall-clock work shared the slice: the charge-interposer
    tester's simulator scenarios and the Android emulator. The run's `wallclock-others`
    events say "no other wall-clock test on local", because the settings' `wallclock_patterns`
    is empty.
  - *Later 2026-11-23:* the settings' `wallclock_patterns` is filled from the targeted B4
    re-run on (the next entry). The settings example has the same list from `ebebf4c6`.

**Passes**
- **B1 (3.2, SLCAN refused and counted): PASS,** 20 of 20 (17:16:39-17:17:22).
- **B2 (5, interposer presence controlled; which 0x051 is ours): PASS,** 6 arms as specified,
  X2 frames 0. INHIBIT 0x051 670 / 670 (x2_app / held), RESPOND probes 2989 / 2993, long arm
  29977 / 29977.
- **B3 (the go-live batch): PASS,** 20 arms, stale 0. Recorded, not judged: the first answer
  after the POST, OFF 150.0-150.9 ms, OBSERVE 25.6-90.6, PASSIVE 20.7-26.5, RESPOND 33.8-51.6.
- **B24 (4.1, B0 held): PASS,** 22 arms as specified, 1 RESPOND arm
  with no device frames, 0 other.
- **B11 (12.4, quiesce under traffic): PASS,** 6 arms as specified, `tx_ok` agrees with every
  INHIBIT span.
- **B11raw (spec 3, a mode change in the request-to-reply window): PASS,** 12 of 12 POSTs, 3
  with nothing to show on the witness (NOT APPLICABLE); windows 13.0-81.4 ms, clock margin at
  most 14.43 ms.
- **B13 (12.4, PASSIVE on the wire; 11, the page fields): PASS,** 8 arms ok, booking 8 of 8.
- **B15 (spec 10, the diag frames on the segment): PASS,** 3 adapters on CAN1 by role: the
  Kvaser 19 frames, the PCAN 20877, `canmain` 23. Recorded, not judged: the non-ACK repeat id
  `0x7F9`.
- **B16 (spec 11, uptime and reset reason): PASS,** 360 polls, 0 unanswered, no outage.
- **B17a / B17b (12.4, the page names the binary): PASS,** `elf_sha256` `cd46e234` on `_man`
  and `bccf8159` on `_rxq`.
- **B18 ("0x7F3 carries the build id", bench half): PASS,** "MATCH, 7 rows ok",
  `diag_build_id` `cd46e234`, at schema 9.
- **B20 (the interlock, bench): PASS,** 28 of 28 AS SPECIFIED (18:05:22-18:23:10).
- **B25 (7.1, key state, on B20's `rekey-long`): PASS,** 0 of 18396 frames against rule 1;
  gate open 203.483 s, resumed 203.486 s.
- **B21 (spec 9.3, the OTA refusal at the client): PASS,** both guards hold; 20 of 20
  multipart and 20 of 20 image requests got the whole 403 with its reason.
- **B9 (7, trip 6, wrong bit rate; X2 held): PASS,** 9 bursts, 228 witness error frames, TEC
  max 0, trip 6 latched at +1.42 s.
- **B10 (12.4, trip 6 before going live, after a re-arm; X2 held): PASS,** 3 of 3 judged arms
  as specified, 2 flood arms.
- **B14 (12.4, OBSERVE is listen-only in hardware; X2 held): PASS,** 5 mode checks, mode 0 at
  the end.
- **NONODE (12.4, a bus with no other node; X2 held): PASS,** 3 main arms, trips "bus lost --
  no 0x051" and "error-frame rate exceeded", latched by 0.618 s at most, no answer stuck.
- **B12 (12.4, no error frames at a restart; `_rt`): PASS,** 7 of 7 cycles, 0 error frames, 7
  restart records `did_twai` and `did_stdby` true, `timed_out` false; SW 2.311-2.403 s, PANIC
  0.595-0.611 s.
- **B23 (12.4, abort behaviour of the C3 TWAI; `_txabort`): PASS,** contend 70 aborted / 4 no
  effect / 4 lost-then-aborted; retry 78 of 78 free, 0 on the wire, the first abort enough on
  71. abortwin (`_abortwin`): PASS, rc 0.
- **B26 (12.4, receive backlog measured directly; `_rxq`): PASS,** max backlog 2 / 3 / 7 (idle
  a, idle b, loaded), 82464 dequeues per arm. Recorded, not judged: dequeues with a backlog
  >= 2, 7 / 5 / 2479.
- **B19 (12.4, test-only features inert in the build that will fly): PASS.** B19present: all
  four present on their test builds. B19absent: all five absent on the prod stand-in. B19page:
  `elf_sha256` `cd46e234`.
- **B5 (7, trip 6, a steady jam): PASS,** 200/s 3 of 3, 500/s 9 of 9.
- **B7 (7, a bus-off re-arm): PASS,** 3 of 3 arms AS SPECIFIED.
- **B8 (11, `bus_on`, the bus-off half): PASS,** train 3 of 3; the control INCONCLUSIVE, as
  in the baseline.
- **B22 (5.2, case 3 saturation, bench half; X2 `slcan`): PASS,** both arms: provenance
  clean, 0 `tx_fail`, 0 error frames, 8983 of ours each.

**Not passes**, observed against the spec
- **B4 (5.2 item 5 and 7 trip 7, skipped inhibit frames, bench half): FAIL, 5 of 6 cases**
  (17:41:45-17:42:34; `man_app/B4/skip_method_20261123T174145.json`). w3, w2, w3s, l3 and l2
  PASS.
  - Observed, `l3s` (three late events about 0.55 s apart, which must not trip 7): the witness
    classed all three as late (our answer to each marked VCM `0x051` on the wire after the
    VCM's next one); the device counted 2 late skips and 0 withdrawn; no trip.
  - Spec 12.4's row "skipped inhibit frames" judges the device's counts against the witness
    on totals: device late >= witness late. 2 < 3, so one frame that went out late was not
    counted as a skip. Every earlier full run with B4 has it 6 of 6.
  - *Later 2026-11-23:* ten more B4 runs on the same image all PASS, `l3s` included (the
    next entry).
- **B6 (7, trip 6 at the threshold; the 12.4 row "error-frame rate trip"): FAIL, 9 of 10 arms
  over 5 runs** (19:13:04-19:34:58).
  - Observed, `B6_threshold_n9_lead1.5` arm 1 (`x2_jam_threshold_20261123T192539.json`): 9
    pulses, the witness saw one error frame per pulse, at most 9 in any window. The device's
    `bus_error` deltas per pulse were `[1, 1, 1, 1, 1, 1, 8, 1, 1]`, and trip 6, "error-frame
    rate exceeded", latched 6.623 s after the first pulse.
  - Spec 7, trip 6: 10 or more error frames within a 10 s window. The wire never carried more
    than 9.
  - Where the counted error frames fell (`b6_multicount_landing.py` and `b6_class_count.py` at
    `4126f144`, over the run's 5 threshold records, 96 pulses; `bench/b6_landing_4b15.txt`,
    `bench/b6_class_4b15.txt`): 95 counted once, 1 counted 8 times. Before that pulse's error
    frame on the wire was a `0x054` 1335 us earlier, after it a `0x639` 1992 us later; TEC/REC
    max 0/0. Class (next frame a `0x051` 200-320 us after): 3 pulses, all single-count; other
    landings 93, 1 over-count.
  - Precedent on file: the same shape at the `e3d9437` acceptance (one pulse counted 11
    times), at step 1a2 (`c68c`, `n9_lead1.5` arm 1, 11 times) and at step 1c (`0b9a`, 9
    times). The open item "One pulse counted 10-12 times" (spec 7, trip 6) is not concluded.
    Whether this FAIL is carried with them is for the reviewer.
- **B27: NOT-RUN,** the declared skip (needs the user at the bench; deferred by the user
  2026-10-17), as in the baseline.

**Targeted B4 re-run on `db998aca` -- tester, 2026-11-23 20:32-21:12, madhouse-debian.
Result: B4 PASS in 10 of 10 runs, and all 60 arms PASS. `l3s` PASS 10 of 10: each time the
device counted 3 late skips, as the witness saw.** The gen-inhibit reviewer asked for this
re-run on 2026-11-23. It gives a rate for the `l3s` miss in bench run 4b15 (the entry above).
Run files: the ten chain runs in `runs/chain/`, and the desk table in `runs/b4_rerun_db998aca/`
(its `README.md`).
- **Code.** The `_man` image of the entry above, `cd46e234...`, code hash `f9ce967d...`. In
  every run the runner read the WiCAN before and after the group. It read `cd46e234...` each
  time, so no run loaded an image.
- **X2.** `x2_app` `756d6714`. The X2 was on `slcan` after 4b15. Run `d99a` flashed `x2_app`
  at 20:33:39-20:33:43, 4 writes verified. The other nine runs found it on `x2_app`.
- **Harness.**
  - `gi_tools` is the worktree `gen-inhibit-tester-tools-4126f144`, at `4126f144`, clean.
  - The suite file came from `gen-inhibit/tester/wallclock-patterns` `ebebf4c6`. From
    `4126f144` to `ebebf4c6`, the suite, the judges and `tools/bench/` change only
    `test_gi_host_run_list.py` and `tools/bench/README.md`.
  - The shared core is the checkout of shared/main tag `2026.11.16.5`.
  - Every run's record pairs it with the emulated run `d82f` (the entry above).
- **Runs.** Ten chain runs, each with the one check `man_app/B4`, back to back from 20:32:51 to
  21:11:33:
  `runs/chain/gen-inhibit-bench_20261123T{203251_d99a,203723_b488,204112_0e62,204500_cd12,204848_f00b,205236_3bce,205623_4bef,210011_142f,210359_92c3,210746_3c38}/`.
  - Each run but the last kept the lock for the tester's session (`--keep-lock`). The next
    run started from that hold (`--from-hold`). So the bench was not free between runs.
  - Every check ran in bench.slice. Each B4 took 49.2-50.9 s.
- **Result per arm.** It is the same in all ten runs. Trip 7 latched only where it must.

  | Arm | Witness | Device | Trip 7 | Verdict |
  |---|---|---|---|---|
  | `w3` | 3 withdrawn | 3 withdrawn, 0 late | latched | PASS 10 of 10 |
  | `w2` | 2 withdrawn | 2 withdrawn, 0 late | none | PASS 10 of 10 |
  | `w3s` | 3 withdrawn | 3 withdrawn, 0 late | none | PASS 10 of 10 |
  | `l3` | 3 late | 3 late, 0 withdrawn | latched | PASS 10 of 10 |
  | `l2` | 2 late | 2 late, 0 withdrawn | none | PASS 10 of 10 |
  | `l3s` | 3 late | 3 late, 0 withdrawn | none | PASS 10 of 10 |

- **The rate on `db998aca`.** B4 ran 11 times on this image: 4b15 and these ten runs.
  - `l3s` missed once: 1 of its 33 late events was not counted.
  - Over every late event (`l3`, `l2` and `l3s`, 8 a run), 1 of 88 was not counted.
  - Every withdrawn event was counted, 88 of 88.
- **Triage.** No check failed, so no window is triaged. Recorded, not judged: every run's
  `wallclock-others` event named other wall-clock work, through the settings' new
  `wallclock_patterns`. Each run named the Android emulator and a charge-interposer acceptance
  run's bench suite. Run `3c38` also named `bench_switch_errors.py`.
- **What this does not change.** 4b15's B4 FAIL stands. These runs show how often the miss
  comes on this image. The implementor's fix (`ac6621f4`, merged as `30cc9236`) is not
  tested here.

**The full run on `cca06f36`, path (ii) and path (i) with the probe -- tester, 2026-11-23 and
2026-11-25, madhouse-debian. Result: the emulated suite PASS (H, B15host, C2scan; C1C7 and C3
the declared skips). The bench suite: PASS 37, INCONCLUSIVE 1 (B6), NOT-RUN 1 (B27, the
declared skip). No FAIL. B4 PASS 6 of 6. B6 has 8 of 10 arms PASS. Its 2 other arms are each
one error frame short of the commanded 10, and the resource record is clean over both.** The
gen-inhibit reviewer's build message: verify `gen-inhibit/main` `cca06f36`, with the path (ii)
fix (`30cc9236`) and path (i) with the probe (verdict-hold), against the spec as changed on
2026-11-23 (`f1961dcf`, `ab74c3da`). The scheduler core changed, so the full emulated and
bench suites ran on new images. Run files: the chain runs in `runs/chain/`, and the desk
records in `runs/verdict_hold_cca06f36/` (its `README.md`).
- **Code.** Built by the tester from clean worktrees detached at `cca06f36`, ESP-IDF v5.4.2:
  the bench set with `baseline_builds --bench`, and the prod stand-in with the shared tooling's
  release build (32 rows ok). The stand-in's image hash is `e8e6ae76...`, code hash
  `0f405de5...`. The bench `_man` image is the same image. The other bench images' code
  hashes: `_rt` `3b862c25`, `_txabort` `5d1d385d`, `_abortwin` `7a8cb109`, `_rxq` `6f914137`.
  Every code hash differs from `db998aca`'s, so no pass carries over from it.
- **Harness.** `gi_tools` is the detached worktree `gen-inhibit-tester-tools-687a135f`, at
  `gen-inhibit/tester/verdict-hold` `687a135f`, clean: `4126f144`, the later fixes on
  `gen-inhibit/main`, and this topic's judges (`aa947dec`) and spec shim cases (`687a135f`).
  Its pinned can-tools `49eb777` and canre `ffbe029` are installed. Settings
  `chain_settings_madhouse-debian.json` (`gi_*` moved to `cca06f36` and `687a135f` for this
  run). The shared core is the checkout of shared/main tag `2026.11.16.5`.
- **The spec shim:** AS SPECIFIED, 46 of 46 (section 7, "Trip 7, what the device can see of
  lateness").
- **The emulated suite:** `gen-inhibit-emulated_20261123T231000_db8d` (2026-11-23
  23:10-23:16, build.slice). H PASS: 25 steps, goldens 81 passed / 0 failed / 23 without, 9
  replays identical, 0 invariant and 0 fuzz violations. B15host PASS: every emitted diag
  frame decodes against the DBC. C2scan PASS, 4 of 4 steps. C1C7 and C3 NOT-RUN: their
  commands are not yet in the suite. The runner exits 1 for the two skips, as before. The
  bench run below is paired with it.
- **Bench run:** `gen-inhibit-bench_20261125T082838_8047` (2026-11-25 08:28:38-10:59:16),
  the whole suite. The chain runner held the lock itself. `gi_images` and `gi_prod_dir` were
  at `cca06f36`, clean. Every check ran in bench.slice.
  - The first start, 2026-11-23 23:16, was refused: the charge-interposer tester held the
    lock (run `gen-inhibit-bench_20261123T231641_d25f`). A power failure on 2026-11-24
    ended the wait. On 2026-11-25 the tester cleared that run's lock, whose process had
    died (08:24:51), and started this run on the free lock.
- **Boards.** The runner read both boards before and after every group (`progress.jsonl`).
  - WiCAN: found on an image no run names (`2aac016d...`, mode 0, nothing latched). Loaded by
    OTA: `_man` `e8e6ae76...` (08:30:44) for man_app, man_held, man_jammer and man_slcan;
    `_rt` `49fafc1b...` (09:56:56); `_txabort` `9eeffad3...` (10:07:22); `_abortwin`
    `1b6c5e9c...` (10:11:13); `_rxq` `c950d53c...` (10:14:44); the prod stand-in `e8e6ae76...`
    (10:20:37). Every OTA took at attempt 1. The end hook found `wican_man`, mode 0, latched
    True.
  - The OTA-reply count, recorded, not judged: all six loads got HTTP `303` with "File
    uploaded successfully", curl exit 0, `time_total` 11.5-18.5 s
    (`bench/ota_reply_count_8047.txt`).
  - Power: 12 V on OBD pin 16, and USB at 1-3.1. The 12 V supply was off after the power
    failure, and the user re-enabled it on 2026-11-25 before the run. USB alone does not
    power the WiCAN (user, 2026-11-25). The WiCAN stayed on USB from 08:27:07. No WiCAN
    console capture.
  - X2 (rule 6): found on `c12_bench` (`400cf328...`). `x2_app` `756d6714` (flashed 08:31:29
    and 09:57:02) for man_app, rt, txabort, abortwin, rxq and prod. Held in ROM, not driving
    TX (`x2_hold.py`, 09:43:51), for man_held. The jammer row `ea762c62044cb5c6` under
    custody, verify-row PASS (10:28:28), for man_jammer. `slcan` (`slcan:V0101:NF001`,
    flashed 10:53:10) for man_slcan; the X2 was left on it.
- **Triage** (AGENTS.md, "Parallel work on the shared machine", point 2). No check failed.
  B6's two short arms were read against `resource-log window` (10:37:12-10:37:40 and
  10:40:27-10:41:01): bench.slice 2.3 cores, CPU pressure (some) at most 0.3 % and 0.2 %, no
  memory or IO pressure, 0 major faults, no swap traffic, no logger gap over 5 s
  (`bench/resource_B6_n10_lead1.5_arm0.txt`, `bench/resource_B6_n10_lead8.5_arm0.txt`). Clean.
  - Recorded, not judged: over the whole run bench.slice ran at 2.9 cores at most, CPU
    pressure at most 0.7 % (`bench/resource_run.txt`). The `wallclock-others` events named
    the charge-interposer tester's emulated suite and its simulator scenarios at time scale 1,
    from man_app's B4 to the end.

**Passes**
- **B1 (3.2, SLCAN refused and counted): PASS,** 20 of 20 (08:32:48-08:33:32).
- **B2 (5, interposer presence controlled; which 0x051 is ours): PASS,** 6 arms as specified,
  X2 frames 0. INHIBIT 0x051 670 / 670 (x2_app / held), RESPOND probes 2998 / 2999, long arm
  29977 / 29977.
- **B3 (the go-live batch): PASS,** 20 arms, stale 0. Recorded, not judged: the first answer
  after the POST, OFF 150.1-150.3 ms, OBSERVE 4.5-73.0, PASSIVE 4.9-88.7, RESPOND 40.4-60.5.
- **B24 (4.1, B0 held): PASS,** 22 arms as specified, 1 RESPOND arm with no device frames, 0
  other.
- **B4 (5.2 item 5 and 7 trip 7, skipped inhibit frames, bench half): PASS, 6 of 6 cases**
  (08:57:41-08:58:28; `man_app/B4/skip_method_20261125T085742.json`). The judge reads spec
  12.4's row on totals: device withdrawn + late == witness never-on-wire + witness late, and
  device late >= witness late.

  | Case | Witness never / late | Device withdrawn / late | Trip 7 |
  |---|---|---|---|
  | `w3` | 3 / 0 | 0 / 3 | latched |
  | `w2` | 2 / 0 | 0 / 2 | none |
  | `w3s` | 3 / 0 | 0 / 3 | none |
  | `l3` | 0 / 3 | 0 / 3 | latched |
  | `l2` | 0 / 2 | 0 / 2 | none |
  | `l3s` | 0 / 3 | 0 / 3 | none |

  - Recorded, not judged: in the filler cases (`w3`, `w2`, `w3s`) the device booked every
    event late, 0 withdrawn. On `db998aca` it booked them withdrawn. Both satisfy the row.
    With the filler, the VCM's next `0x051` is among the frames waiting when our frame's
    completion is seen.
  - 0 witness error frames. The player sent 19262 frames, 0 failed. The page after the
    cases: mode 0, nothing latched.
- **B11 (12.4, quiesce under traffic): PASS,** 6 arms as specified, `tx_ok` agrees with every
  INHIBIT span.
- **B11raw (spec 3, a mode change in the request-to-reply window): PASS,** 12 of 12 POSTs, 3
  with nothing to show on the witness (NOT APPLICABLE); windows 14.0-45.6 ms, clock margin at
  most 13.43 ms.
- **B13 (12.4, PASSIVE on the wire; 11, the page fields): PASS,** 8 arms ok, booking 8 of 8.
  RESPOND's probe balance in B2's two arms: unbooked 4 and 1, late on the wire 4 and 1
  (section 11, "The probe on the inhibit frame's path").
- **B15 (spec 10, the diag frames on the segment): PASS,** 3 adapters on CAN1 by role: the
  Kvaser 19 frames, the PCAN 22300, `canmain` 23. Recorded, not judged: the non-ACK repeat id
  `0x7F3`.
- **B16 (spec 11, uptime and reset reason): PASS,** 360 polls, 0 unanswered, no outage.
- **B17a / B17b (12.4, the page names the binary): PASS,** `elf_sha256` `e8e6ae76` on `_man`
  and `c950d53c` on `_rxq`.
- **B18 ("0x7F3 carries the build id", bench half): PASS,** "MATCH, 7 rows ok",
  `diag_build_id` `e8e6ae76`.
- **B20 (the interlock, bench): PASS,** 28 of 28 AS SPECIFIED (09:21:07-09:39:05).
- **B25 (7.1, key state, on B20's `rekey-long`): PASS,** 0 of 18396 frames against rule 1;
  gate open 203.484 s, resumed 203.487 s.
- **B21 (spec 9.3, the OTA refusal at the client): PASS,** both guards hold; 20 of 20
  multipart and 20 of 20 image requests got the whole 403 with its reason.
- **B9 (7, trip 6, wrong bit rate; X2 held): PASS,** 9 bursts, 178 witness error frames, TEC
  max 0, trip 6 latched at +1.63 s.
- **B10 (12.4, trip 6 before going live, after a re-arm; X2 held): PASS,** 3 of 3 judged arms
  as specified, 2 flood arms.
- **B14 (12.4, OBSERVE is listen-only in hardware; X2 held): PASS,** 5 mode checks, mode 0 at
  the end.
- **NONODE (12.4, a bus with no other node; X2 held): PASS,** 3 main arms, trip "error-frame
  rate exceeded", latched by 0.458 s at most, no answer stuck.
- **B12 (12.4, no error frames at a restart; `_rt`): PASS,** 7 of 7 cycles, 0 error frames, 7
  restart records `did_twai` and `did_stdby` true, `timed_out` false; SW 2.341-2.353 s, PANIC
  0.609-0.631 s.
- **B23 (12.4, abort behaviour of the C3 TWAI; `_txabort`): PASS,** contend 69 aborted / 6 no
  effect / 3 lost-then-aborted; retry 78 of 78 free, 0 on the wire, the first abort enough on
  70. abortwin (`_abortwin`): PASS, rc 0.
- **B26 (12.4, receive backlog measured directly; `_rxq`): PASS,** max backlog 2 / 2 / 8 (idle
  a, idle b, loaded), 82464 dequeues per arm. Recorded, not judged: dequeues with a backlog
  >= 2, 7 / 1 / 1335.
- **B19 (12.4, test-only features inert in the build that will fly): PASS.** B19present: all
  four present on their test builds. B19absent: all five absent on the prod stand-in. B19page:
  `elf_sha256` `e8e6ae76`.
- **B5 (7, trip 6, a steady jam): PASS,** 200/s 3 of 3, 500/s 9 of 9.
- **B7 (7, a bus-off re-arm): PASS,** 3 of 3 arms AS SPECIFIED.
- **B8 (11, `bus_on`, the bus-off half): PASS,** train 3 of 3; the control INCONCLUSIVE, as
  in the baseline.
- **B22 (5.2, case 3 saturation, bench half; X2 `slcan`): PASS,** both arms: provenance
  clean, 0 `tx_fail`, 0 error frames, 8983 of ours each. Spec 5.2's saturation criterion
  holds in both: 8979 + 4 and 8978 + 5 of 8983 queued, 0 skipped (section 5.2, "Case 3
  saturation, the criterion as changed on 2026-11-23").

**Not passes**, observed against the spec
- **B6 (7, trip 6 at the threshold; the 12.4 row "error-frame rate trip"): INCONCLUSIVE, 8 of
  10 arms PASS over 5 runs** (10:36:57-10:45:02). No arm FAILs.
  - Observed, `B6_threshold_n10_lead1.5` arm 0 (`x2_jam_threshold_20261125T103657.json`) and
    `B6_threshold_n10_lead8.5` arm 0 (`x2_jam_threshold_20261125T104010.json`): the jammer
    reported 10 pulses sent, "end=complete", inside its deadline. The witness saw 9 error
    frames, one per pulse for 9 pulses. The pulse missing on the wire is the tenth in the first
    arm and the seventh in the second. The device's `bus_error` deltas were 1 per pulse, 9 in
    all, and trip 6 did not latch.
  - Spec 7, trip 6: 10 or more error frames within a 10 s window. The wire carried 9, so no
    trip is right, but the arm cannot judge the threshold. The judge gives "delivery was not
    one error frame per pulse".
  - The other arm of each of those runs, and both arms of the other three runs, PASS. Each
    of the four n10 arms that got 10 error frames latched trip 6, at +9.06 s to +9.14 s. The
    four n9 arms got 9 and did not latch.
  - Precedent on file: the same short stimulus in run C, 575b and a61f (the entry "Different
    standing verdict" above). The cause is not known.
- **B27: NOT-RUN,** the declared skip (needs the user at the bench; deferred by the user
  2026-10-17), as in the baseline.

**Port step 4 item 1: the harness takes shared/bench from its pinned checkout -- tester,
2026-11-25 and 2026-11-26, madhouse-debian. Result: the same verdicts as bench run `8047`,
check by check (39 of 39), and the same emulated verdicts as `db8d`. No verdict changed.
The harness's own tests found two defects of the change in its mutant files, both fixed on
the topic (`b353c75f`, `95695442`).** The gen-inhibit reviewer's batch of 2026-11-25 (port
plan, order of work step 4): the harness takes shared/bench at tag `2026.11.23.5` (commit
`2a7ab8be`, digest `c7c0f913...`) from the checkout `../shared-2026.11.23.5/`, through
`bench/gi_core_pin.py` (shared/tools R23's checks, run by the pinned commit's own
`vtrux_build/pin.py`), and no longer from PYTHONPATH. The settings give no
`wallclock_patterns`, so the core's default list is used. The product code did not change.
Acceptance: the full emulated and bench suites, on `cca06f36`'s product images, give the same
verdicts as `8047`. On `gen-inhibit/tester/bench-pin`, from `4c3afb4d`. Desk records:
`runs/port_step4_item1/` (its `README.md`).
- **Harness.** `gi_tools` is the detached worktree `gen-inhibit-tester-tools-3d9ab7cc`, at
  `3d9ab7cc`, clean. The two later commits change only mutant files that no suite check
  runs: `b353c75f` takes out a suite mutant that `3d9ab7cc` left with no text to edit, and
  `95695442` gives `x2_hold`'s mutant copies the file they lack (below). The runner starts as
  `gi_core_pin.py module vtrux_bench.run run ...`. PYTHONPATH names only the interposer
  checkout (`interposer-code-3779f9dc`), for `bench_identify`. Every record's `code.core`
  names the tag, commit, digest and folder.
- **Code.** The images of the full run on `cca06f36` above, unchanged: `gi_images` and
  `gi_prod_dir` at `cca06f36`, clean, the same image hashes.
- **The emulated suite:** `gen-inhibit-emulated_20261125T225543_36df` (2026-11-25
  22:55-23:05, build.slice). H PASS, B15host PASS, C2scan PASS 4 of 4, C1C7 and C3 NOT-RUN
  (the declared skips): the same as `db8d`.
- **Two bench starts stopped before any check ran.**
  - `gen-inhibit-bench_20261125T230425_6582` (23:04-23:15): NOT-RUN 35, "could not identify
    x2". The X2 held the charge-interposer's image `2ffba2c6`, whose USB serial takes no
    input. `x2_hold.py pins` then went past the suite's 120 s limit. By hand, the same read
    took 205.7 s; each of its 5 esptool `read_mem` calls ended in "Write timeout". The tester
    then loaded the pinned `x2_app` by hand, under a hand-take of the lock (4 writes
    verified). `x2_hold.py` and `gi_esptool.py` are the same at `4c3afb4d` and `3d9ab7cc`, so
    this limit is not from the change.
  - `gen-inhibit-bench_20261125T232316_da19` (23:23-23:24): stopped at the X2 identify,
    `ModuleNotFoundError: bench_identify`. The tester's start command left PYTHONPATH out;
    the suite's own text names the interposer checkout there. This was an operator error.
- **Bench run:** `gen-inhibit-bench_20261125T233016_58fa` (2026-11-25 23:30:16 to
  2026-11-26 01:57:24), the whole suite, with the documented start. The chain runner held
  the lock itself. Every group's placement read back bench.slice.
  - WiCAN: found on `_man` `e8e6ae76...`, mode 0, nothing latched. Loaded by OTA, each at
    attempt 1: `_rt` (00:55:36), `_txabort` (01:05:59), `_abortwin` (01:09:47), `_rxq`
    (01:13:17), the prod stand-in `e8e6ae76...` (01:19:12). The end hook found `wican_man`,
    mode 0, latched True.
  - X2: found on `x2_app` `756d6714`. Held in ROM for man_held (00:42:28). `x2_app` flashed
    again at 00:55:42. The jammer row `ea762c62044cb5c6` under custody, verify-row PASS
    (01:27:00), for man_jammer. `slcan` flashed at 01:51:14; the X2 was left on it.
  - Recorded, not judged: the `wallclock-others` events name the charge-interposer
    tester's acceptance run and its simulator scenarios at time scale 1 (the core's default
    list).
- **Verdicts:** PASS 37, INCONCLUSIVE 1 (B6), NOT-RUN 1 (B27, the declared skip). Each of
  the 39 checks has the same verdict as in `8047`.
  - B4 PASS 6 of 6. B6: 8 of 10 arms PASS. Its 2 other arms (`B6_threshold_n10_lead1.5`
    arm 0, `B6_threshold_n10_lead5` arm 1) each got 9 error frames for the 10 commanded
    pulses ("delivery was not one error frame per pulse"), as in `8047`.
  - Triage of B6's two runs with a short arm (`resource-log window`, 01:35:14-01:36:45 and
    01:36:45-01:38:22): bench.slice 4.3-4.5 cores, CPU pressure (some) at most 1.7 %, no
    memory or IO pressure, major faults at most 0.2/s, no logger gap over 5 s
    (`resource_B6_n10_lead1.5_58fa.txt`, `resource_B6_n10_lead5_58fa.txt`). Clean. Over the
    whole run: `resource_run_58fa.txt`.
- **The harness's own tests** on `3d9ab7cc`: in the tools test set, 137 files exit 0, and
  `bench/test_only_inert.py` exits 2 without its arguments, as before. Mutant runs:
  `test_kvaser_bus` 138 of 138 with `--can-fail`, the control passing; the set's 60 mutant
  files: 58 exit 0, no mutant survives. The other two:
  - `bench/test_x2_hold_mutants.py`: the control failed (FileNotFoundError), so no mutant
    was judged. Since `3d9ab7cc`, `gi_serial` loads `gi_core_pin` from beside itself, and
    the mutant folders held no copy of it. A defect of the change, fixed in `95695442`: 10
    of 10 caught, the control passing.
  - `test_suite_gen_inhibit_mutants.py`: killed by the tester's own process cleanup (exit
    -9), so void. Run again alone on `3d9ab7cc`: 161 of 162 caught and 1 ERROR, the
    mutant with no text to edit, a defect of the change. On `b353c75f`, which takes it
    out: 161 of 161 caught, 0 errors, the control passing.

**A withdrawn inhibit frame stuck in the controller, emulated -- tester, 2026-11-26, the
mock-HAL shim on `madhouse-debian` (gcc 14.2.0). Result: NOT AS SPECIFIED on
gen-inhibit/main `54e1ea13` (the can-fail) and on the implementor's fix `8b991a45`. In
`withdrawn_262` the frame is never removed, and it reaches the wire when traffic
returns.** Spec 12.4 row "a bus with no other node, emulated", its withdrawn-frame part
(added 2026-11-26, user; emulated only, no bench run); spec 5.2 item 5, "A withdrawn frame
held past the bound" (tracker F5).
- **Harness.** `gen-inhibit/tester/withdrawn-stuck` `988c95ff`
  (`test/gen_inhibit_spec_shim/`, README "The withdrawn frame stuck in the controller").
  Two cases at the bench row's measured pattern: `withdrawn_262` (246 us apart to TEC 128,
  then 262 us) and `withdrawn_274` (258 us, then 274 us). The nodes go after the last VCM
  command and before the device's answer completes, so the answer is the inhibit frame in
  the controller. The 10th error comes about 2.5 ms after the frame is handed over. Trip 6
  latches at the first check after it (+200 ms) and withdraws the frame.
- **The retries are the layer's model** (`spec_layer.h`, `sl_retry_stuck`):
  - During each attempt, TS reads set and an abort does nothing. This is the fake's abort
    of a transmitting frame (twai_port T8, measured on the C3).
  - In the bus idle time before the next attempt, TS reads clear and the fake's abort
    removes the frame. The idle time is 6 us while error active (the intermission, 3 bit
    times) and 22 us from TEC 128 (plus the suspend transmission, 8 bit times). The bench's
    passive gaps are 16 us longer than its active ones.
  - How long TS stays clear on the C3 is not measured.
  - A frame still held when the nodes return completes at the end of the first attempt
    that starts after they return.
- **Verdicts:**
  - W1: the scheduler aborts the frame again on later passes until the buffer is free. An
    abort of the device's must remove it before traffic returns.
  - W2: the frame never completes.
  - W3: no `0x051` reaches the wire when traffic returns.
  - W4: the queued telemetry then goes out.
  - Also R1-R4, R6, A2-A4 and the layer's two checks, as for `answer_stuck`.
- **Observed on `54e1ea13`** (`run_spec_shim.py --local --base 54e1ea13` at `988c95ff`):
  - `withdrawn_262`: W1, W2 and W3 are NOT AS SPECIFIED. The device issued no abort command
    while the frame was held. It read the status register twice in that time, and both
    reads showed TS set. Traffic returned 1.8 s after the latch, and the frame then
    completed on the wire, one `0x051`. A4 is VOID, because the frame was held on every
    read.
  - The other 10 `withdrawn_262` verdicts PASS: trip 6 at the +200 ms check point,
    `standdown` +1 and `skipped` +0, the counts balanced on every read, the queued
    telemetry sent when traffic returned, and a key-on cleared the latch.
  - `withdrawn_274`: 14 PASS. One abort, 200 ms after the latch, fell in an idle time and
    removed the frame.
  - Every other case gave only PASS lines.
- **Observed on `8b991a45`** (the same run at `c94b40e6`, a local merge of `988c95ff` with
  `8b991a45`, made for this run only):
  - The verdicts are the same as on `54e1ea13`, line for line.
  - Only the W1 detail differs. While the frame was held, the device read the status
    register 14 times: once or twice per pass, every 200 ms, with the two reads 3 us apart.
    Every read showed TS set. The device issued no abort command and made no busy-wait
    call.
- **The harness's own tests** on `988c95ff`:
  - `judge_test`: 88 ok. W1-W4 are each shown to pass and to fail.
  - `layer_stuck` and `layer_stuck_back` PASS. They check TS over 6 ms against the model,
    with both idle times seen. An abort in an attempt leaves the frame held, and one in the
    idle time removes it, never on the wire. With the nodes back mid-attempt, an abort does
    nothing, and the frame completes 240 us after a 22 us idle time.
  - `mutate.py --local`: 94 of 94 caught, 21 of them new. A layer or case mutant is now
    caught only by a verdict that differs from a control run of the unmutated build,
    because the case fails on `54e1ea13` by design.
  - `test_host_env.py`: all ok. Its new checks cover that catch rule. `--mutants`: 13 of 13
    killed, the control passing. Since stage (b) batch 2 (2026-11-23), its control had
    failed: the copy lacked `test/diag_host/`. Fixed in `988c95ff`.
  - `product_mutants.py`: the probe cases' control passes, 6 of 6 caught.
  - The whole tools test set at `988c95ff`'s files:
    - 137 of the 138 test files exit 0. `test_only_inert.py` exits 2, as on earlier
      commits: it is a bench script that needs its arguments.
    - 18 of the 137 first exited 1, because the canre and can-tools pins were not yet
      installed in this new worktree. With both installed, all 18 exit 0.
    - The 60 mutant files exit 0.
- **Run files.** `runs/f5_withdrawn_988c95ff/` (`README.md` there).

## 13 Known not to be implemented

**What a cross-host IDF 5.4.1 build differs in, for the code-identical
clause -- tester, 2026-10-23, on `a24de99` `_man`. Result: same-host pairs
byte-identical, .bin and ELF; across hosts the .bin differs only in the
descriptor's ELF hash, the trailing checksum and the trailing SHA-256 (65
bytes), nothing "elsewhere".** Spec 13, "Mode 3 is bench-only ...", *What
carries over*: "Code-identical means the images differ only in version,
project name, build-time, hash (including `git_version`) and checksum bytes".
Asked for by the reviewer as the validation basis for the shared build
tooling's code hash (its requirement 2), on gen-inhibit's own IDF 5.4.1
build. Build only; no device was touched.
- **Source:** gen-inhibit/main `a24de99`, a fresh clone of a git bundle per
  build, detached; `managed_components/` from a tar of the verified
  `wican-prod-e3d9437` copy (`dependencies.lock` the same at `a24de99`),
  sha256 `2b7af917...`. The checked-out tree hashes to `3765962a...` over 685
  files on every clean build on both hosts (`tree_hash.py`: path and bytes as
  on disk, so line endings included).
- **Config, without editing the tracked tree:** the committed `sdkconfig` is
  copied beside the tree, its one line `# CONFIG_APP_REPRODUCIBLE_BUILD is not
  set` replaced by `CONFIG_APP_REPRODUCIBLE_BUILD=y`, and the build run as
  `idf.py -D SDKCONFIG=<copy> build`. Each build records, after building, `=y`
  in the copy, `#define CONFIG_APP_REPRODUCIBLE_BUILD 1` in
  `build/config/sdkconfig.h`, `git status` clean and the committed `sdkconfig`
  unchanged.
- **Builds:**
  - madhouse-debian (`repro_build.sh`; ESP-IDF v5.4.1, gcc esp-14.2.0_20241119,
    CMake 3.31.6, ninja 1.12.1, Python 3.13.5): `m1` at
    `<madhouse-debian home>/gi_repro_a24de99/a`, `m2` at `<madhouse-debian home>/gi_repro_a24de99/path_b_longer/deeper`,
    06:38-06:40 and again 06:45-06:47 (madhouse clock), the same sha256s both
    times.
  - NICKY-XPS (`repro_build.ps1`; the same IDF, gcc and ninja, CMake 3.30.2,
    Python 3.11.9): `x1` at `%TEMP%\gen-inhibit-repro\x1\a` 17:05-17:11, `x2`
    at `%TEMP%\gen-inhibit-repro\x2\path_b_longer\deeper` 17:11-17:17, after
    the interposer's bench run released NICKY-XPS for builds at 17:04.
  - Control, madhouse-debian: `mctl`, `main/main.c` line 187
    (`ESP_LOGI(TAG, "----------");`) duplicated, uncommitted.
- **Images:**

  | Build | .bin length | .bin sha256 | ELF length | ELF sha256 |
  |---|---|---|---|---|
  | m1, m2 | 1113360 | `a49ca791800b7ae3ca4370aec4125bc197e1a96d326d58d7bfb2295f7079acca` | 11008900 | `8004ed31280dd161e9e4ba9e564d8238f5954fa7dd02c308f20d953509290b3d` |
  | x1, x2 | 1113360 | `336b8415c2293b3256c50be2fe0c0c67b1b28ddf5b2c7908c715cf16014c36e7` | 11008780 | `a8915e6221fc16f019599fcebd824e427f79799a25df78840dfb3cbcc5e7a355` |
  | mctl | 1113392 | `fe72aab420676779fb3a5fef5498744fb0083b149d958ae3eb35254f7fb14512` | 11009080 | `37facd350b6c48a4eac605489240bff1b12a7ae709423871f7bcab07a9917a4a` |

- **Same host, different paths:** m1:m2 and x1:x2 byte-identical, .bin and
  ELF, no ELF section differing.
- **Across hosts** (m1:x1, m2:x2, m1:x2, m2:x1, all four alike): the same
  length, and the same six segments at the same load addresses and lengths.
  Three runs of differing bytes:
  - `0x0000b0` len 32, the descriptor's `app_elf_sha256` (descriptor ELF hash);
  - `0x10fcef` len 1, the trailing checksum;
  - `0x10fcf0` len 32, the trailing SHA-256.

  Version and project name are equal (`a24de99`, `wican-fw_obd_a24de99_man`),
  and the time and date fields are all zero bytes in every build, control
  included. Nothing differs "elsewhere". The ELFs differ in `.debug_info` (same length), `.debug_line`
  (2234734 / 2234697 bytes) and `.debug_line_str` (11228 / 11143) only. In
  `.debug_line_str` the madhouse-debian ELF has one string the NICKY-XPS ELF
  lacks, `/builds/idf/crosstool-NG/.build/riscv32-esp-elf/build/build-cc-gcc-final/gcc/include`,
  and none the other way round; x1:x2 has none either way.
- **Control** (m1:mctl): the .bin grows by 32 bytes, segment 3 (`0x42000020`,
  flash text) from 746368 to 746400; 738195 differing bytes "elsewhere", 698214
  of them in segment 3 data, across 15189 runs; `.flash.text` among the ELF
  sections differing. The descriptor's version (6 bytes) and project name (10)
  differ too: `a24de99-dirty` and `wican-fw_obd_a24de99-dirty_man`.
- **The comparison tool:** `repro_compare.py` reads the image layout (header,
  segments, `esp_app_desc_t` at `0x20`, checksum, appended SHA-256) and places
  every differing byte in its field. Tested by `test_repro_compare.py` on a
  real `e3d9437` image with bytes changed at known fields: 12 checks PASS, 8 of
  8 mutants killed by a check (field offsets shifted, date/time merged into
  version, checksum at the last byte, runs not split, runs merged,
  identity by length only, the descriptor ignored, `hash_appended` ignored).
- **Run files:** `runs/repro_a24de99/` (README): the scripts; `m1/`, `m2/`,
  `mctl/`, `x1/`, `x2/` (each the .bin, `info.txt`, `build.log`,
  `sdkconfig.repro`); `compare_madhouse.txt`/`.json`,
  `compare_cross_host.txt`/`.json`, `debug_line_str_diff.txt`. The ELFs are
  kept outside SeaDrive, in `%TEMP%/gen-inhibit-repro/builds/`, their sha256
  above.

**x1 and x2 again with ccache off -- tester, 2026-10-23 23:31-23:39, on
`a24de99` `_man`. Result: x1:x2 byte-identical, .bin and ELF, with ccache
shown off in both builds; both are also byte-identical to the first x1 and
x2, so the cross-host result above stands as written.** Asked for by the
reviewer: the first x1 and x2 (entry above) ran with ccache on
(`-DCCACHE_ENABLE=1` on idf.py's cmake line, the ESP-IDF export's
`IDF_CCACHE_ENABLE=1` on NICKY-XPS), so their identity could have been one
compile served twice from the cache. m1, m2 and mctl ran with
`-DCCACHE_ENABLE=0`. Build only; no device was touched.
- **Builds:** `repro_build.ps1 -NoCcache` (the switch added for this rerun:
  `IDF_CCACHE_ENABLE=0` after the export and `idf.py --no-ccache`; the steps
  otherwise as for the first x1 and x2), the same bundle, components tar and
  paths: `x1nc` at `%TEMP%\gen-inhibit-repro\x1\a` 23:31-23:35, `x2nc` at
  `%TEMP%\gen-inhibit-repro\x2\path_b_longer\deeper` 23:35-23:39, each
  clean (the script deletes the folder first). Unthrottled: `bench-holder.md`
  free, no simulator and no other build running (checked 23:30). `info.txt`
  of each: head `a24de99`, `git status` clean before and after, tree
  `3765962a...` (685 files), components tar `2b7af917...`, the same tool
  versions as the first x1 and x2, `=y` in the copy and in `sdkconfig.h`.
- **ccache off, by its effect** (`ccache_check.py`, written for this rerun;
  it reads what the build left): idf.py's `cmake -G Ninja` line in
  `build.log`; `CCACHE_ENABLE` in every `CMakeCache.txt` under `build/`; every
  `command =` line of every `CMakeFiles/rules.ninja` (idf puts ccache there as
  `command = ccache ${LAUNCHER}...`); and every non-empty `LAUNCHER =`
  binding in every `build.ninja`, where a launcher from the environment
  (`CMAKE_<LANG>_COMPILER_LAUNCHER`) lands per edge with `CCACHE_ENABLE`
  still 0 (the implementor's configure probes, relayed by the reviewer). The
  app's folder and every other folder with a `CMakeCache.txt` (the
  bootloader's) must each have all three files.
  - `x1nc`, `x2nc`: PASS each: cmake line `-DCCACHE_ENABLE=0`; app
    `CCACHE_ENABLE` 0, the bootloader's cache has no such entry; 0
    `command =` lines naming ccache and 0 non-empty `LAUNCHER` bindings, app
    and bootloader (`ccache_check_x1nc.txt`, `ccache_check_x2nc.txt`, run
    after the builds with the final version; the run inside each build is
    in its `info.txt`).
  - The first x2's tree (still on disk at 23:33): FAIL: cmake line `=1`, app
    `CCACHE_ENABLE` 1, 94 `command =` lines in the app's `rules.ninja`
    starting `ccache`; 0 in the bootloader's, and 0 `LAUNCHER` bindings in
    either `build.ninja` (`ccache_check_x2_orig.txt`). The first x1's tree
    gave the same FAIL before the `LAUNCHER` item was added and was rebuilt
    before it could be re-read (`ccache_check_x1_orig.txt`). So in the first
    builds the bootloader was not compiled through ccache either way; only
    the app was.
  - Its test, `test_ccache_check.py`: 20 checks PASS, a fake build folder
    that passes and each fault planted alone (each of the four items in the
    app and in the bootloader, ccache by full path, an empty `LAUNCHER`
    binding that passes, each of the three files missing from one folder
    while the other folder has it) (`test_ccache_check.txt`).
- **Images** (`repro_compare.py`, `compare_noccache.txt`/`.json`):
  `x1nc` and `x2nc` both .bin 1113360 bytes, sha256
  `336b8415c2293b3256c50be2fe0c0c67b1b28ddf5b2c7908c715cf16014c36e7`, ELF
  11008780 bytes, sha256
  `a8915e6221fc16f019599fcebd824e427f79799a25df78840dfb3cbcc5e7a355`: the
  first x1's and x2's.
  - x1nc:x2nc, x1nc:x1 and x2nc:x2: byte-identical, .bin and ELF.
  - x1nc:m1, x2nc:m2, x1nc:m2, x2nc:m1: the three runs of the entry above
    and nothing else (`0x0000b0` len 32 descriptor ELF hash, `0x10fcef` len
    1 checksum, `0x10fcf0` len 32 trailing SHA-256), the same six segments;
    ELF sections differing `.debug_info`, `.debug_line`, `.debug_line_str`
    only, as above.
- **Run files:** `runs/repro_a24de99/` (README): `x1_noccache/`,
  `x2_noccache/` (each the .bin, `info.txt`, `build.log` (UTF-16 LE),
  `sdkconfig.repro`); `ccache_check.py`, `test_ccache_check.py` and their
  outputs; `compare_noccache.txt`/`.json`. The ELFs are in
  `%TEMP%/gen-inhibit-repro/builds/x1nc`, `x2nc`, their sha256 above.

**LF line endings pinned, `4de33c9` -- tester, 2026-10-23 23:42-23:45. Result:
PASS. With `core.autocrlf=true` every tracked file checks out as its committed
bytes except `minify.bat`, which checks out CRLF; `main/homepage.html` checks
out LF. Under `core.autocrlf=input`, `41e1ec2` and `4de33c9` differ only in
`.gitattributes` and `minify.bat`.** The implementor's `1a4a67a`
(".gitattributes: pin LF line endings (eol=lf)", branch
`gen-inhibit/implementor/lf-eol`, merged into gen-inhibit/main as `4de33c9`),
on the user's ruling of 2026-10-23, option A. The root `.gitattributes` at
`4de33c9`: upstream's comment line, `* text=auto eol=lf`, `*.bat` and `*.cmd`
`text eol=crlf`, `*.pdf`, `*.png`, `*.bin`, `*.elf` `binary`. Checked as the
reviewer asked: no firmware build, the tester's own script.
- **How:** `lf_check.py` (written for this check): a fresh clone of the local
  anchor with `core.autocrlf` set at clone time (so it overrides the
  machine's `input` from Git for Windows' system gitconfig), checked out
  detached; then per tracked file the working-tree bytes against the blob's
  (`git cat-file`), with CRLF and lone-LF counts in each, or the sha256 of
  every tracked file. Four clones, each `git status` clean: `4de33c9` and
  `41e1ec2` with `autocrlf=true`, the same two with `autocrlf=input`. 697
  tracked files in each, no symlinks or submodules.
- **`4de33c9`, `autocrlf=true`:** 696 files byte-identical to their blobs.
  `minify.bat` is the one that differs: 3 CRLF in the working tree, 3 LF in
  the blob. `main/homepage.html`: 202 LF, no CRLF, identical to its blob;
  `.gitattributes` the same (8 LF). The other 29 files with a CRLF byte pair
  in the working tree are 28 `.png` and `sch/wican_obd_pro_sch_v151.pdf`,
  each identical to its blob. So no text file has a CRLF in its blob.
- **Control, `41e1ec2`, `autocrlf=true`:** 665 files differ from their
  blobs, every LF written as CRLF, `main/homepage.html` among them (202
  CRLF) and `.gitattributes` (2 CRLF). The check sees the conversion when
  there is one.
- **`41e1ec2` vs `4de33c9`, `autocrlf=input`:** 697 files each side; sha256
  differs for 2: `.gitattributes` and `minify.bat`.
- **Run files:** `runs/lf_4de33c9/`: `lf_check.py`, `clones.txt`,
  `eol_t_new.txt`/`.json` (`4de33c9`), `eol_t_old.txt`/`.json` (`41e1ec2`),
  `hashes_i_new.json`, `hashes_i_old.json`,
  `diff_input_41e1ec2_4de33c9.txt`. The clones are in the tester worktree's
  `scratch/gen-inhibit-lf/`.
