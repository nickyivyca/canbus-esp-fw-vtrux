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

## The negative control

Reverting `gen_inhibit.c` to the `dc571cb` alert handling in full — no
per-iteration poll, no pre-queue drain, a bounded read straight after
queueing — makes cases 1 and 4 fail, with the invariant reporting `tx_ok=11
but only 10 inhibit frames completed on the wire`.

**Run that before trusting a change here.** Partially reverting is not enough:
an earlier attempt restored only the completion test and every case still
passed, which is how a suite that cannot catch its own motivating bug looks
from the outside.
