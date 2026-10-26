# E1 — the shim's own tests, against a fake TWAI driver

`../gen_inhibit_host` compiles the pure **core** and none of `gen_inhibit.c`.
That is not a gap in coverage, it is a whole component — and in the four days
to 2026-09-25 **four defects in a row lived exactly there**, every one of them
invisible to a suite that was green throughout:

| | |
|---|---|
| `dc571cb` | alerts read only after an inhibit frame, crediting whatever was pending |
| `632af32` | the fix closed the inhibit-first ordering and left diag-first open |
| A3 | `can_send()` and the three SLCAN channels — no core involvement at all |
| spec 11 | a self-imposed OFF that could not say why |

## Running it

```sh
export REVERSE_IT_DIR=<the reverse-it project folder>   # required, no default
make && ./shim_test && ./shim_test_autoarm    # or: make run
make asan && ./shim_test
```

Builds `main/gen_inhibit.c` and `main/gen_inhibit_core.c` against mock
platform headers in `mock/`. Needs pthreads and Python 3; no ESP-IDF.
`make` also builds the long-arm stimulus first, from the project folder (see
"Building the long-arm stimulus" below), and stops if `REVERSE_IT_DIR` is not
set.

**Exit codes:** 0 all cases pass, 1 a case failed, 2 a case was still running
at its wall-clock budget (20 s, or what the case declares with
`case_budget()`). A `SIGALRM` armed in `case_begin()` enforces the budget
while the case runs, and the line before the exit names the case. Until
2026-10-20 the budget was judged only in `case_end()`, which a hung case never
reaches: the tester's run of `b341e0d` built with WSL gcc 9.4 sat in case 5
for 17 minutes with the budget silent.

`shim_test_autoarm` is the same sources with the boot auto-arm compiled in
(`GEN_INHIBIT_AUTOARM_MODE=GEN_INHIBIT_PASSIVE`). It runs the one case that
needs it (spec 3.2, "arming comes after it"); every other case arms by hand.

## What the model has to get right

`fake_twai.c` is not the ESP32's peripheral and a green run here is **not**
evidence about hardware. It reproduces the three properties the defects turned
on, and nothing else:

- **alerts are latched bits shared by every frame** — `TWAI_ALERT_TX_SUCCESS`
  says *the previous* transmission succeeded, with no indication of which, and
  reading clears it;
- **one transmit buffer, FIFO** — so a frame at the head blocks everything
  behind it, and `msgs_to_tx` is the only attributable completion signal;
- **completion takes time**, settable per case;
- **a controller that is not running receives nothing** -- with no driver
  installed, stopped, or bus-off, a delivered frame is dropped and counted
  (`ft_rx_dropped_off()`), and install and start each begin with an empty
  receive queue, as `components/driver/twai/twai.c` does (IDF 5.4.1). Until
  2026-10-16 only bus-off dropped, so frames delivered after a mode 0 queued
  with nothing to take them and `replay_scn()`'s drain never returned.

Determinism: virtual time, and the worker thread never runs concurrently with
the test. Two runs of the same script produce the same trace. **It therefore
cannot find a race** — that is out of reach here and belongs on hardware.

## The invariant every case is checked against

`tx_ok` means "frames that completed on the wire", so it can never exceed the
number of `0x051` frames the driver actually completed. Every mis-crediting
bug this component has had violates exactly that, whatever the mechanism —
which is why the check is stated as a property rather than as a replay of any
one defect's shape.

## Three things this file got wrong before it worked

Worth keeping, because each produced a **passing** test that proved nothing:

1. **Case 1 fed one frame and then ran 1.6 s of silence.** The device aborted
   on bus-loss at 1.015 s, stopped transmitting, and "tx_ok did not move"
   passed for a reason unrelated to the case. Fixed by `keep_alive()`.
2. **`ft_stall_next()` stalled whichever frame was queued next** — and
   `gi_tick()` may emit a diag page first, so the stall landed on the diag.
   Fixed by `ft_stall_id()`.
3. **An earlier case demanded that a diag frame complete while a stalled
   inhibit sat at the head of the queue.** It cannot: one buffer, FIFO. The
   case was describing something the hardware cannot do, and its guard caught
   it.

And the model itself was wrong twice in the same direction as the defect it
was meant to catch — `ft_run()` capped virtual time instead of advancing it,
and the head-of-queue clock restarted on every call — so case 3 reported a
false `TX_LATE` that looked exactly like the firmware bug. **A model that errs
the same way as the defect will confirm it.**

## Replaying a real capture

Spec 5.2 item 10 (user, 2026-10-03): **emulation replays recorded traffic for its
stimulus, not for bus load.** A replay gives real command timing and jitter, real
arrival ordering, and the arm and key transitions the truck actually produced. It
gives *no* bus load: `controller_advance()` completes our frames from our own queue
head plus `air_time_for(id)`, and a delivered frame enters that calculation nowhere.
A load-dependent criterion needs the bench.

`replay_scn(name, &result, window_us, on_window)` reads a scenario and delivers it at
the recorded timestamps. It looks in this harness's `scenarios/` first and falls back
to `../gen_inhibit_host/scenarios/`, so the eight host replays are reusable here
without being copied. Neither directory is in git.

**Building the long-arm stimulus** (cases 33 and 53). `make` builds
`scenarios/replay-longarm-300s-all.scn` before `shim_test`, through
`build_longarm.py`, from a capture in the project folder:
`projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log`,
every id, channel 0, the first 300 s. It is never committed: the capture is not
published, so nothing derived from it is either, and `scenarios/` is in
`.gitignore`. **23,241,533 bytes with LF line endings**, and 706,283 frames
(gen-inhibit tester's measurement of their own regeneration, 2026-10-09; the
implementor's madhouse build of `38fd103` came out the same size. The "~12 MB"
this said until then was wrong by a factor of two and so could not have been used
to notice a truncated rebuild).

- **Where the project folder comes from:** `REVERSE_IT_DIR`, the absolute path of
  the reverse-it folder on this machine (methodology, 2026-10-20). There is no
  default; unset stops the build with a message naming the variable, and
  `shim_test` is not run. `build_longarm.py` passes the same folder to
  `from_capture.py` as `--project-dir`, so the capture and the `canre` parser that reads
  it come from the same folder. Under WSL, pass a Windows value through with
  `WSLENV=REVERSE_IT_DIR/p`.
- **When it rebuilds:** when the file is missing, or older than the capture,
  `../gen_inhibit_host/from_capture.py` or `build_longarm.py`, so a changed
  converter cannot leave a stale file passing. Otherwise the step is a no-op. It
  writes to a temporary name and renames it when complete, so an interrupted build
  does not leave a truncated file that looks current.
- **Which Python:** `python3`, or `make PYTHON=<interpreter>`. The suite builds
  with gcc on Linux or WSL; on Windows a bare `python3` opens the Microsoft Store,
  and the project's `python-executables-reference.md` is the per-machine table.

`stimulus.fnv` is what keeps a rebuilt file honest: each replay case checks the
stimulus it ran on against the committed checksum (spec 12.4), so a rebuild that
differs fails the case rather than quietly measuring something else.

Until 2026-10-20 this was a manual `from_capture.py` line run before the suite. A
clean checkout without it failed cases 33 and 53 on a missing file, and the
project folder came from `--repo` or `$GEN_INHIBIT_REPO`, which defaulted to a
sync-client path that does not exist on every machine.

**Pass `--all-ids`** — case 33 replays the whole capture. Do not generate it into the
host harness's `scenarios/`: `run_tests.py` enumerates everything there, and
`host_runner` exits 2 on the length of the resulting per-ID comment line.

**The pin.** `stimulus.fnv` records two FNV-1a 64 checksums per scenario — the
stimulus lines and the comment lines — because a replay-driven case has no golden to
hang a checksum on (spec 12.4, widened 2026-10-03). A stimulus mismatch **fails** the
case; a comment-only change is a **notice**. `./shim_test --pin-stimulus` writes the
file; run it only after checking the scenario is the one you mean.

**The recipe above is known to reproduce the pin.** The 2026-10-09 regeneration
came back with both of `stimulus.fnv`'s recorded checksums for this scenario --
`0ec984c308821e55` for the stimulus lines and `d2bf76277d4595d4` for the
comments (gen-inhibit tester; the two hashes are the ones in the file, checked
here). So a rebuilt fixture that *fails* the pin is a changed recipe, a changed
capture or a truncated write, not drift in the generator.

**Every case declares its transmit timing** (spec 12.4), printed as a `timing:` line:
default air time, per-ID overrides, stalls armed. It is a *high-water* record over the
whole case, not the state at the end, so a case that sets an override and clears it
still reports having used it — case 31 does exactly that.

### Replaying the full capture: what it took, and the one thing still short

**The full capture replays.** All 706,283 frames, every ID, over 299 s, with the
recorded timing reproduced to **63 us** and no excursion over a millisecond. Mock loss
is **zero**: `ctr_ok` 29,991, `ctr_bad` 0. `tx_ok` 29,977 of the capture's 29,992
commands.

**The drain condition is the whole trick, and three of my diagnoses were wrong before
it.** The worker consumes **one frame per turn**, and the capture delivers 706,283
frames at 242,977 distinct timestamps — bursts of up to 12. One turn per timestamp
therefore lost all but the first frame of each burst, which is why the core first saw
242,974 frames: one per timestamp, to within three.

But draining to **empty** is just as wrong, and worse to diagnose. `fake_twai`'s blocked
receive advances `g_now` to *its own* deadline "if nothing else does", and the worker's
is `GEN_INHIBIT_RX_TIMEOUT_MS` = 200 ms. So emptying the queue let the worker re-enter a
blocking receive, the clock jumped 200 ms, the next ~470 frames of capture had targets
already in the past and arrived as one clump of ~20 commands inside 50 us, and the device
tripped 7 on four phantom skips. The `rx_gap` histogram said it plainly: 28,495 gaps
under 50 us against 1,496 over 12.8 ms, with a perfectly correct mean of 10,003 us, and
1,496 x 200 ms = 299 s.

So the reader leaves **one frame pending**. The worker never re-enters a blocking
receive, and at 2354 frames/s that is the more faithful model anyway — the device's
receive queue is essentially never empty.

**A figure this file previously stated, and it was wrong.** It said the mock worker
"costs ~1235 us per frame", and concluded the clock model could not sustain truck rate.
That number was 300 s divided by the frames that survived — a *consequence* of the loss
read as its cause. Measured properly, by summing the turns rather than reading a maximum
as an average: **3.5 us per turn**, 1.62 s across 463,307 turns of a 300 s replay. The
clock is cheap. A spec requirement to rebuild it was raised and then withdrawn on this
correction; the wrong figure is kept here so nobody re-derives it.

**The verified-on-time criterion holds, and it took a fix to the mock rather than to
the firmware.** (It is the spec's **third** acceptance criterion for the emulation half,
not the fifth — the order is every ID, mock loss 0, verified-on-time, every case
passes, the mutation rounds still catch. An earlier revision of this section and the
commit that landed it both called it the fifth; names are used here instead of numbers
for that reason.) `ontime` is credited only when the receive queue is empty at the
completion. The drain used to leave one frame pending deliberately — the only way to
stop the 200 ms clock jump — so `rx_backlog >= 1` at most completions *by
construction*, and the verified-on-time proportion sat at **7,011 of 29,977** against
the bench's **8,976 of 8,983**. A blocked receive now advances virtual time no further
than the turn the test granted it (`g_turn_grant` in `fake_twai.c`), so the drain
empties the queue and the proportion is **29,977 of 29,977**.

**That 100 % is constructed the opposite way, and is not a better result than the
bench's 99.92 %.** The drain empties the queue before each completion is judged, so
`rx_backlog` is now 0 by construction and this case **cannot produce an unverified
completion at all**. The bench produces 7, and the device's unverified accounting — the
count and the largest backlog behind one — therefore has **no cover in case 33 any
more**, where the old construction gave it 22,966 samples. One question replaced
another. **Case 34 builds the non-empty-backlog condition instead**, and catches a
backlog forced to zero at `gs_tick`'s call site or forced to one at both.

**Those counters are status-page telemetry and no trip depends on them.** `rx_backlog`'s
only effect is the `ontime` / `ontime_unverified` / `unverified_max_backlog` split in
`note_left_controller`; trip 7 reads skips, `TX_FAILED` and refusal. An earlier revision
of this section said trip 7 read the unverified counts, and that was wrong — the lost
cover matters for telemetry, not for any latch.

**What the fix cost elsewhere, measured:** the replay now takes one worker turn per
frame rather than per timestamp — 706,284 turns against 463,307, 2.46 s of virtual time
against 1.62 s, the per-turn cost unchanged at 3.5 us — and the worst lateness moved
from 63 us to **72 us**, still with **0 excursions over 1 ms**. Every other figure is
unchanged: 706,283 frames, `tx_ok` 29,977, `ctr_ok` 29,991, `ctr_bad` 0, diag 1,210.
All 33 cases pass, and no timing assertion in the suite moved — checked by landing the
mock fix alone first, with the drain untouched, which reproduced every case 33 figure
byte for byte.

**Stretching the timeline does not help** and is worth knowing before anyone tries it:
at 2x, 4x and 8x, `tx_ok` collapses to 558, 25 and 7. The interlock freshness windows
are in *absolute* time, so a slowed stimulus makes every signal stale and the device
latches off. The gates cannot be slowed down.

## What E1 does NOT cover

- **`main.c`'s SLCAN dispatch.** A3 disables the command parser on three
  channels, and that code lives in `can_tx_task`, which this build does not
  compile. Nothing here would notice if it came back. Bench or code review only.
- **Races.** One runnable thread at a time by design; a race between the worker
  and an HTTP handler is out of reach.
- **Real timing.** Virtual clock. The trail measurement is the bench's job.

`main/can.c` and `main/gen_inhibit.c` ARE compiled as shipped, which is the
point of `fake_can.c` being platform stubs rather than a reimplementation.

## The negative control

`mutate.py` is the standing one, adopted from the reviewing session. It applies
seven mutations one at a time to a scratch copy and reports `rc`. `BASE` is a
copy of the project folder, `gen-inhibit/`, which holds `firmware/main/` and
`test/`:

```sh
BASE=~/gi_review python3 mutate.py
```

Six must come back `rc=1`. **M7 is expected to survive** and `gen_inhibit.c`
says why: with completion decided by `msgs_to_tx == 0`, the pre-queue drain
only clears a stale `TX_FAILED`, itself unreachable at `ss = 0`.

**Read `rc`, not the text.** The original scraped lines containing `FAIL`,
which also matches the harness's own `twai_receive failed: ESP_FAIL` logging —
so a surviving mutation was reported as caught.

### A sixth round found the layer every suite was watching the wrong side of

V01-V07, against `accdb18`. **Five of seven survived** — and not because the
suites were weak about the rules, but because all four of them watch the same
thing: the **core's emit list**. Every one of these mutations lives in the four
lines of `dispatch_emits()` that copy a `gi_frame_t` into a `twai_message_t`,
which is *downstream* of that list. The core decided correctly in all five
cases. The copy then got it wrong, and nothing anywhere looked.

- **V04** (`tx.extd = 1`) is the worst defect this component has had on paper.
  The inhibit goes out as a 29-bit identifier; the GENE inverter filters on the
  11-bit `0x051` and never sees it. The VCM's torque command stands — while
  `tx_ok` counts up, the completion logic reports success, and every diag page
  says the inhibit is healthy. **Silently ineffective, with the instrumentation
  agreeing.**
- **V05** (`tx.self = 1`) makes the controller deliver our own frame back to
  us. The worker reads its own `0x051`, cannot distinguish it from the VCM's,
  and answers it with counter + 1 — which is received, and answered. A
  self-sustaining transmit loop on a live powertrain bus.
- **V02** (DLC 8 not 6), **V03** (`memcpy` 6 bytes of 8) and **V06** (the probe
  ignores `offset_us`) are quieter. V06 is the one worth noting anyway: the
  offset was configured, range-checked by case 12, and reported in the diag
  JSON — and simply not obeyed. A value can be validated everywhere and used
  nowhere.

**The fix was one invariant, not five cases.** `fake_twai.c` now checks every
frame handed to `twai_transmit()`: standard data frame, not extended, not
self-receiving, not single-shot, DLC 6 for `0x051` and 8 for the diag and probe
IDs, `B0` at `0x08`, torque bytes `00 80`, and the ID in the set the scenario
declared with `ft_allow_ids()`. It is a property of the *fake*, in the shape of
`ft_set_step_hook()` one layer lower, so it covers the paths no case walks.

**Where the invariant is checked turned out to matter as much as what it
checks.** The first version ran the whole thing before `twai_transmit()`'s
early returns and immediately reported OBSERVE as broken. It is not:
`gen_inhibit.c:495` says outright that the section 10 diag pages are handed to
`twai_transmit()` in OBSERVE and that the listen-only controller refuses them,
"the documented cost of a genuinely passive tap". So the check is split — the
frame *flags and payload* are judged on what the firmware **built**, because a
frame constructed with `extd` set is wrong whether or not the driver took it;
the *ID rules* are judged on what the controller **accepted**, because a
refused call never reaches the bus.

**And that split exposed a model-fidelity gap of exactly the recorded kind.**
The fake enqueued whatever it was handed, ignoring the installed mode. Case 11
("OBSERVE queues nothing") had been passing because the driver happened not to
be running at the instant the diag page was built — an accident of timing, not
the mechanism the firmware depends on. It would have gone on passing if that
mechanism were removed. `twai_transmit()` now returns `ESP_ERR_NOT_SUPPORTED`
in listen-only, as ESP-IDF documents, so OBSERVE's silence rests on the real
reason.

**V05 also gets a behavioural check, not only a flag check.** The fake now
*honours* `self`: a frame queued with it set is delivered back to the receive
path, which needed the single receive slot to become a real queue — a 1-deep
slot would have dropped the self-frame and swallowed the very evidence it
exists to produce. `teardown()` then asserts that no more inhibit frames went
out than commands came in, counting only frames the **test** delivered, so the
loop cannot raise its own ceiling. That catches the runaway whatever set the
bit.

**Where it ended up.** 32 mutations, 28 caught, 4 survivors — and all three
survivors are **equivalent mutants with the reason written down**, not gaps:

| Survivor | Why it cannot be killed |
|---|---|
| `K21` | `disable_monitor()` returns early once a release has latched, so the branch K21 edits cannot run. Spec 6.2's "unconditional on SoC recovering" is enforced one level above it. |
| `M7` | Completion is decided by `msgs_to_tx == 0`, which no leftover alert affects; and `poll_tx_completion()` runs immediately before `dispatch_emits()` in both worker paths, leaving no window for a stale `TX_FAILED`. At `ss = 0` the bit is never set on the device anyway. |
| `V08`, `V09` | Each edits an outer guard standing in front of an inner one — `gi_on_tx_result()` returns early for `GI_TX_DIAG` on its own (`gen_inhibit_core.c:1753`), and the worker never calls `gi_tick()` in OFF on its own (`gen_inhibit.c:670`). |

The four `W` mutations were added with the 2026-09-25 rulings and all four are
caught: `0x617`'s window back on `fresh_us`, the evidence test left on the old
window while staleness moved to the new one, the GENE ordering reverted, and the
SoC debounce at 4. A ruling that lands without a mutation showing something
would notice it going away is a ruling nobody is checking.

All four are kept in the set as **tripwires on the structure that makes them
harmless**. The day an inner guard moves, its outer one stops being redundant
and the mutation starts failing — which is precisely when someone needs to
know.

**One real gap is visible inside V09 and should not hide behind the
equivalence.** `gi_tick()`'s OFF guard is unreachable from the driver, but the
host harness calls the pure core directly and *could* reach it. No scenario
there ticks in OFF, so nothing tests what the core does when asked to. That is
a host-suite gap, small but real.

**Two of the new checks fired on their first run, and both times the check was
wrong rather than the firmware** — worth recording, because a new invariant's
first failures are the ones most likely to be believed:

- The schema-version check was applied to the whole diag family. Only the
  STATUS page carries the version in byte 0; STATUS2's byte 0 is the abort
  reason code.
- Case 16 ran 360 ms and caught exactly one status page — emitted *before* the
  interlocks established a SoC at 65 ms. It reported `soc_x100 = 0` against the
  JSON's 5000, which reads exactly like the truncation defect the case exists to
  catch. The case was simply too short to observe what it compares.

### A second round found five more, after all seven were being caught

N1-N7, against `bea8d51`. **N2, N3, N4, N5 and N6 survived.** That is the
pattern worth internalising: every round of "all mutations caught" has been
followed by a round that found more. **A green mutation run means "nothing in
THIS set survives", never "the suite is adequate."**

- **N5** was the one to fix first: the spec 8 quiesce handshake -- the
  use-after-free guard -- could be deleted freely. Closing it needed a
  **model-fidelity fix, not a new case**: the fake's `twai_receive()` returned
  as soon as it got the CPU back, so the worker left the driver on any test
  step. On the device it stays blocked until a frame arrives or the timeout
  expires, and nothing a lower-priority task does shortens that. With the
  eager model, `can_disable()`'s own short delay let the worker slip out and
  park, so the teardown never saw anyone inside. Same class as the
  task-handle finding: **a mock that is more forgiving than the hardware hides
  exactly the bugs the hardware would punish.**
- **N4** (OBSERVE not listen-only) and **N3** (an alerts-config failure ignored
  at arm) needed the fake to record the install mode and to be able to fail
  `twai_reconfigure_alerts()`.
- **N2** (`behind` forced false) killed spec 5's `tx_queued_behind` reading
  silently — a zero there reads as "hazard absent", which is the worst way for
  a measurement to fail.
- **N6** was simply untested: the 4000 us offset limit, listed in spec 12.4.

### And once, all fourteen were "caught" while the baseline was red

Fixing N5's fidelity broke case 4's timing, so the baseline failed two
assertions — and every mutant then failed the same two. `mutate.py` now refuses
to run on a red baseline. **A mutation score computed against a failing
baseline is meaningless and looks perfect.**

Case 4 was consequently rebuilt to be **timing-independent**: the inhibit is
stalled so it can never complete, and only the diag's air time matters. Four
earlier versions each depended on three durations lining up, and each broke.

### Four of the seven survived the first version of this suite

Including **M1, the exact bug E1 was written for**. Each reason is worth
keeping, because every one produced a green run:

1. **Case 5 tested the fake.** `fake_can.c` had its own copy of the spec 3.2
   refusal, so deleting the real one in `main/can.c` changed nothing. Fixed by
   compiling `main/can.c` as shipped.
2. **Case 6 drove only one of the two self-off paths.** The quiesce reason could
   be deleted freely. Fixed by case 10.
3. **Nothing asserted the diag deferral at all.** Fixed by case 9.
4. **Case 4 never actually created the ordering it describes.** It set an air
   time and hoped a diag page was in the buffer. Making it deterministic took
   three further attempts, each of which *looked* like a firmware defect:
   - one air time for everything, so the diag and the inhibit could not be
     separated — either both completed or neither;
   - a diag fast enough (1 ms) that it completed before the `0x051` arrived, so
     the per-iteration poll consumed its alert while nothing was outstanding;
   - a detection loop that fed five interlock signals per pass, letting up to
     100 ms elapse between the diag being queued and the `0x051` — longer than
     the diag's own air time.

The invariant is checked as a **step hook** for the same reason: the early
credit opens a window that closes again, and an end-of-case assertion sees
nothing wrong.

### Round 14: six cases for the switchover, and two of them could not fail

Cases 21-26, added 2026-09-27 for the mutants that survived the reviewing
session's round 13 against the scheduler switchover. Every one is a form of
section 7 trip 7 that the switchover left unpinned: three skips inside a second
trip and one does not (I2), the controller reporting a failed transmit trips at
once (I3, I8), a refused inhibit trips and is not retried afterwards (I7, and
defect D7), a withdrawn skip is reported as withdrawn (I9), and the HAL issues no
abort command while the buffer is transmitting (I6). Case 26 is defect D10 and is
not anyone's mutant.

**Mutation I9 could not have been caught by any test, because the output it
corrupts did not exist.** It reports every skip under the maybe-late kind. The
kind reached the core, the core put it in a `GI_EV_SKIP` event -- and
`report_events()` had no case for that event, so the `default: break;` swallowed
it with no `-Wswitch` warning. Nothing on the device could tell a withdrawn
inhibit from one that may have gone out late, or either from nothing happening.
Fixing that (defect D9) came first; the case came second. **When a mutation
survives, ask whether anything reads the value before writing a test that claims
to.**

**Case 23's D7 half was written twice before it could fail.** D7 is the defect
where a refused inhibit stays in the scheduler's queue, the core latches off on
the refusal, and the retry puts an inhibit frame on the wire after the trip.

* v1 compared `ft_wire_count_id(0x051)` across a 120 ms window. The scheduler
  retries on the very next received frame, so the retry was already inside the
  "before" sample.
* v2 moved that into a step hook armed on first seeing `abort_latched`. The trip
  and the retry fall inside a single worker iteration, so the hook's baseline
  again contained the frame it was watching for.

Both passed with D7 put back. What settled it was running both builds and
printing the counters:

| | correct | D7 removed |
|---|---|---|
| inhibit `queued` | 12 | 12 |
| inhibit `sent` | 11 | 12 |
| `0x051` on the wire | 11 | 12 |
| `skipped_withdrawn` | 1 | 0 |

(Since 2026-10-16 the withdrawn frame is a **stand-down**, not a skip -- spec 5.2
item 5 -- so the case now reads `withdrawn` and `standdown` where this table has
`skipped_withdrawn`. The D7 question is the same: withdrawn, or left queued for a
retry.)

So the case asserts what happened to the frame rather than when it went out --
from two independent places, the fake's wire log and the scheduler's own
counters. Neither needs to resolve the instant of the trip, which is the part the
harness cannot do.

**`stats()` was reading a truncated page.** Its buffer was 1600 bytes and the
page is 1761, so every `json_u32()` of a key in the scheduler block returned
`0xFFFFFFFF` for "absent" -- which satisfies every `>=` assertion written against
it. `json_need()` now fails on absence, and the window is 4096. The same
arithmetic on the device side is defect D10, which case 26 pins.

**`ft_refuse_id()` exists for the reason `ft_stall_id()` does.**
`ft_refuse_next()` marks whichever frame is queued next, and `gi_tick()` may emit
a telemetry page first, so a case meaning "the inhibit was refused" has to say
so or it passes and fails by timing.

### Round 15: D8, and a case that asserted everything except the defect

Case 27 pins that `gen_inhibit.c` calls `gs_rearm()` and not `gs_init()` at a
mode change. The reviewing session's round 13 left that swap as its only
survivor: it passed every suite, here and in `gen_inhibit_sched`.

**The first version of case 27 survived it too, and the reason is worth keeping.**
It asserted that frames still went out, that nothing was refused, that no frame
was dropped, and that there were no wire violations. Every one of those stays
true through the defect, because **`fake_twai` models the driver's real FIFO
(depth 16), not a single slot** -- so a frame handed over while the controller
still holds one is simply *accepted*, and queues behind it. That is precisely
the device's behaviour, and precisely the priority inversion item 3 forbids, and
nothing in the fake was counting it.

`ft_max_inflight()` now does: the high-water mark of **our** frames in the driver
at once. Item 3 says at most one, so the assertion is `<= 1` and the mutation
takes it to 2. Foreign frames are excluded, because `ft_foreign_transmit()`
models another task's `can_send()`, which spec 3.2 refuses while armed, and
counting them would conflate "the scheduler double-submitted" with "another task transmitted" --
which case 5 already covers.

The case is also **driven to its precondition rather than timed into it**: it
feeds in 10 ms steps until `ft_sent_count_id() > ft_wire_count_id()` for a diag
page, which is exactly "a page is in the driver, not yet on the wire", and only
then changes mode. A fixed `keep_alive()` would have made it pass or fail on
scheduling luck.

**`gi_state_t.skips` is gone** (user, 2026-09-27: "leave in only 'skipped'").
The scheduler's `skipped`, with its per-kind split, is the reported number. The
core's own tally reached no JSON at all. All 77 goldens lost the ` skips=N` token
from their FINAL line and were re-blessed; the re-bless was verified mechanically
-- stripping that token from the old goldens reproduces the new ones **exactly**,
all 77, so nothing else changed. The skip count stays pinned in the goldens by
the `GI_EV_SKIP` event lines, one per skip.

### 2026-10-16: stand-downs, trip 8, and a receive queue with no driver

Cases 49-52, for the reviewer's pre-port batch.

* **Cases 49 and 50, a stand-down with an answer pending** (spec 12.4 row of that
  name). An M-mode release locked to a `0x051`, with the answer in the controller
  (49, `ft_stall_id`) or queued behind a diag page on the wire (50, 30 ms page air
  time, the command fed only once a page is found in flight). Each requires one
  `standdown`, skips unchanged, the identity `withdrawn + dropped == skipped +
  standdown`, at least 4 diag pages in the next 1.2 s, and the answer never on the
  wire. Against `c65c45e` product code, case 49 shows the bench B20 silence (0
  pages) and case 50 a skip where there should be none; both lack `standdown`.
  **The frame after the release is not a `0x051`.** A `0x051` there also runs
  its deadline mid-withdrawal, which on `c65c45e` booked a phantom skip that
  ended the core's wait and hid the silence.
* **Case 51, a short `0x051`** (spec 7 trip 8): aborts, and `tx_fail` does not
  move. It moved by 1 on `c65c45e`.
* **Case 52, the receive model above**: frames delivered after a mode 0 are
  dropped and counted, and a drain bounded at 100 turns takes none. It tests the
  fake, not the firmware, so it passes on `c65c45e` too.
* **Case 23 changed** with the spec: see the note under its table.

### 2026-10-16: the worker makes every mode change

Spec 3 as changed 2026-10-16: `gen_inhibit_set_mode()` posts the change and the
worker applies it at the top of its loop, so the test thread -- which plays
httpd -- now WAITS in it while the worker runs. Two model changes follow:

* `vTaskDelay()` on the test thread before the worker exists only passes time;
  yielding would wait for a thread that does not exist.
* `ft_hold_worker()` lets time pass without giving the worker a turn, so a case
  can make the worker miss the 1 s bound. `ft_installs_off_worker()` counts
  driver installs from any thread but the worker.

And the cases:

* **Case 39** posts its arm before starting the worker and awaits it after
  (`gen_inhibit_post_mode()` / `gen_inhibit_await_mode()`, the two halves of the
  setter). That is still the boot ordering it reproduces.
* **Case 53** replays the 300 s capture and posts a mode change every 10 s
  (PASSIVE, INHIBIT, OBSERVE, RESPOND in a cycle) without pausing the traffic.
  Every change must come back `ESP_OK`, every status reading must balance (case
  28's hook), the stand-down identity must hold at each window with nothing
  held, every INHIBIT arm must transmit, and no install may come from outside the
  worker. It cannot show a race; nothing here can.
* **Case 54** puts the 1 s deadline and the worker's take in each order: never
  taken (refused, and never applied afterwards), taken at 950 ms (applied), and
  taken at 999 ms but still applying at the second, because OBSERVE tears the
  driver down and `can_block()` yields (the poster waits; applied). Then with
  the slot held by another poster, since the 1 s runs from the setter's call
  and covers its wait for the slot: held all second (refused with
  `ESP_ERR_NOT_FINISHED` at 1 s, and the worker applies neither request, the
  other one being past its deadline too), and freed at 900 ms (posted then,
  withdrawn at the same 1 s with `ESP_ERR_TIMEOUT`, never applied).

**Why `apply_mode()` drives the scheduler first.** With the change applied at the
top of the loop, before the periodic tick, a frame that had completed since the
last pass was still `held`, so the re-arm carried it in and the new arm timed it
as on time with no `tx_ok` behind it. Case 33 read ontime 29978 against tx_ok
29977 that way, the extra frame being case 32's last. `apply_mode()` now collects
the old arm's completions before it touches anything.

### 2026-10-16: the worker state machine (spec 8.1)

The worker is IDLE, ARMED or UPDATING, and every pending request is taken at
one update point per pass. The status page now carries `worker` and
`bus_violations`. Model changes:

* `ft_as_task(3)` makes the test thread a task other than the default (2). Spec
  8.1 tells the OTA handler's own teardown from anybody else's by the task.
* `ft_autostart_worker()` starts the worker inside `xTaskCreate()`, for the
  auto-arm build, where `gen_inhibit_init()` waits for its own arm.
* `ft_installed_brp()` records the bitrate an install was handed.
* `gen_inhibit_init()` takes the boot configuration; `setup()` passes can.c's
  defaults (500K, normal), which is what every case ran with before.

And the cases:

* **Case 6** also checks row 7: after the receive-error self-off the worker is
  idle and the bus is given back.
* **Case 55** is row 9, the backstop: a `can_disable()` from the test thread with
  the worker held off the CPU must not move the mode until the worker runs, and
  leaves it idle with one violation. Part B does it against a blocked receive,
  with no traffic.
* **Case 56** is the OTA rows, each from its own state: refused while armed with
  nothing touched (8), handed over from idle (4), the handler's own teardown
  counted as nothing and another caller's as one (13), a mode request refused
  (12), and a failed upload back to idle with a later arm working (11).
* **Case 57** is the mode rows from their own states (1, 2, 3, 5 and its
  refusal, 6) and row 13 from idle: a teardown of a bus that was down counts
  nothing, one that was up counts one and forces nothing.
* **Case 58** is the exit action: an answer held in the controller is given up
  as a stand-down when the worker leaves ARMED by row 6, 5 or 9.
* **Case 59** is spec 10's `0x7F3` build id, the host half of the 12.4 row
  "0x7F3 carries the build id" (port step 0c): the first `0x7F3` on the wire
  in PASSIVE is `ff f0 0f 00 07 00 08 00`, held as a literal. The mock's
  image hash starts `00 0f f0 ff`, so the build id is `0x000FF0FF`, hash
  byte 0 most significant as the interposer's `0x7F7`, sent little-endian;
  then `diag_fw_ver16` 7 and schema 8.
* **`shim_test_autoarm`**: `gen_inhibit_init(CAN_250K, listen-only)` with the
  auto-arm compiled in must install at 250K and listen-only, not can.c's
  defaults. **It has no wall-clock budget**: its `main()` does not go through
  `case_begin()`, so the watchdog below never arms. Whoever gates it wraps it
  in an outer timeout.
