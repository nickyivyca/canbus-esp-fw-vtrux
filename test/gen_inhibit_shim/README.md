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
make && ./shim_test       # or: make run
make asan && ./shim_test
```

Builds `main/gen_inhibit.c` and `main/gen_inhibit_core.c` against mock
platform headers in `mock/`. Needs pthreads; no ESP-IDF.

## What the model has to get right

`fake_twai.c` is not the ESP32's peripheral and a green run here is **not**
evidence about hardware. It reproduces the three properties the defects turned
on, and nothing else:

- **alerts are latched bits shared by every frame** — `TWAI_ALERT_TX_SUCCESS`
  says *the previous* transmission succeeded, with no indication of which, and
  reading clears it;
- **one transmit buffer, FIFO** — so a frame at the head blocks everything
  behind it, and `msgs_to_tx` is the only attributable completion signal;
- **completion takes time**, settable per case.

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
seven mutations one at a time to a scratch copy and reports `rc`:

```sh
BASE=~/gi_review python3 mutate.py
```

Six must come back `rc=1`. **M7 is expected to survive** and `gen_inhibit.c`
says why: with completion decided by `msgs_to_tx == 0`, the pre-queue drain
only clears a stale `TX_FAILED`, itself unreachable at `ss = 0`.

**Read `rc`, not the text.** The original scraped lines containing `FAIL`,
which also matches the harness's own `twai_receive failed: ESP_FAIL` logging —
so a surviving mutation was reported as caught.

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
