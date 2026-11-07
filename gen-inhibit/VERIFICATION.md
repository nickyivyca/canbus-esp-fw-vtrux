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

