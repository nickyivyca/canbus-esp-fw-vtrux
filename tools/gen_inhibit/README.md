# projects/vtrux/tools/gen_inhibit/

A prototype of the generator-inhibit idea: transmit 0x051 frames carrying zero
generator torque, timed against the VCM's own 0x051 slot, so the GENE MCU never
acts on a command to crank the engine.

**Nothing here has been run against the truck.** It is a bench: a pure core, an
offline replay against a real capture, and a live three-node virtual bus.

---

## Why 0x051 is a soft target

Measured on `projects/vtrux/logs/generator-on-then-off.log` (6423 frames of
0x051, one generator start and one stop) by
`../../notes/artifacts/gen-inhibit/gen_inhibit_timing.py`:

| | finding |
|---|---|
| Rate / DLC | 100.01 Hz, DLC 6, on the PT bus |
| `B0` | **two values only** -- `0x08` idle, `0x0B` active. Not a checksum: sum, negated sum, XOR, and both with the CAN ID folded in, all matched at or below chance (best 5.4 %). |
| `B1-B2` | torque command, LE, centred at 0x8000 |
| `B3-B4` | engine RPM reference |
| `B5` | rolling counter, **6422 of 6422 steps were exactly +1**, high nibble always 0 |
| idle payload | `08 00 80 FF 7F <ctr>`, 5543 of 5544 idle frames |

No checksum to forge and a counter that never once misbehaved. Every field we
would need to reproduce is either constant, copyable from the VCM's own last
frame, or trivially predictable.

The GENE MCU's torque feedback (0x471) moves within 0 to 20 ms of a command
step, so it acts on commands promptly rather than filtering them over hundreds
of milliseconds.

## What the capture cannot tell us

The timing. BUSMASTER timestamps these captures host-side in batches -- **up to
96 frames share a single 100 us tick**, which at 500 kbit/s is physically
impossible for even two. So the recorded inter-frame times are the logger's,
not the bus's. Two independent messages, 0x051 and 0x052, have jitter
distributions identical to four significant figures (sigma 2.8186 ms vs
2.8268 ms; p5/p95 both 5.90/14.60 ms), which is a common-mode capture artefact,
not two ECUs behaving identically. The residual against an ideal 10 ms clock
has lag-1 autocorrelation **-0.61** -- alternating, i.e. quantisation noise; a
genuinely drifting clock would sit near +1.

So: the VCM's 10 ms period is stable and its counter is perfect, and the true
transmit jitter is **unmeasured**. That is why the bench sweeps jitter as a
parameter instead of assuming a value, and why the guard band sizes itself from
error observed at run time rather than from a constant.

---

## Files

| File | What |
|---|---|
| `sync.py` | **The synchroniser.** Phase-locked loop on the VCM's slot. Pure, no imports. Uses the rolling counter as the slot count, so a dropped frame cannot slip a cycle. |
| `inhibit.py` | **The core.** Pure, no imports. Decides whether it is safe to transmit, when, and with what payload. |
| `models.py` | Hypotheses about how the GENE MCU consumes 0x051, and the scorer. |
| `replay_inhibit.py` | Offline bench: real command content from a capture, modelled bus timing, swept. |
| `live_bench.py` | Three nodes on a virtual CAN segment, real sockets and real clocks. |
| `vehicle_runner.py` | **The on-vehicle tool.** One dongle, plugged in alongside the VCM. Observe-only by default. Arm/disarm, engage interlock, health monitor, verdict. |
| `bench_vehicle.py` | A truck-shaped rig to rehearse the runner against, including a mode that simulates the inverter latching a bad CAN state. |
| `test_core.py` | 43 unit tests for the core. ~1 s, no CAN, no captures. |
| `HANDOFF.md` | **Field procedure.** What to do on the laptop before leaving, the run sequence at the truck, and how to read the verdict. Start there for an actual test. |
| `bench_ota_guard.py` | **Spec 12.4 row 17, bench half.** `POST /upload/ota.bin` must return 403 in every non-OFF mode. Refuses to run unless the device is in `wifi_mode = AP` (the configuration that flies -- spec 8 makes it permanent) and this machine still has a route to the internet, checked by connecting rather than by reading a routing table. The "accepted when OFF" half needs `--allow-ota-write`, because it posts to the OTA endpoint on a device with no *external* USB port and no factory reset -- recovery is bootloader rollback, or taking the case off to reach the C3's native USB (user, 2026-09-27; this row said "no USB port" until then). **Run against hardware 2026-09-26 on `5ceedf2`: 403 in all four non-OFF modes.** Its `gi_status()` now takes `strict=` -- lenient callers tolerate a trailing fragment, because a device whose status page is malformed is exactly the device someone needs to reflash, and refusing there leaves only the cable. |
| `bench_flash_guard.py` | **Spec 5.1 item 3, bench half.** The five HTTP handlers that write SPIFFS (`store_config`, `store_canflt`, `store_auto_data`, `store_car_data`, `upload_car_data`) must each return 403 while `gen_inhibit` owns the bus, because `CONFIG_TWAI_ISR_IN_IRAM` is unset and a flash erase stops the TWAI ISR for up to 20 ms, leaving only the hardware FIFO. **Run 2026-09-26 on `5ceedf2`: 20 of 20 refused, each 403 naming the reason.** The OFF half uses only probes that provably write nothing (empty bodies on the three that check length before `fopen`) and SKIPS `store_car_data` and `upload_car_data`, which `fopen(..., "w")` before validating anything. **That skip is the lesson of the first version**, which posted an invalid body on the assumption a handler would reject it: `store_config` writes the body verbatim and reboots, so it overwrote the device's `config.json`, dropped it to firmware defaults, and took bench access away until the config was restored from `wican-config/backup/`. |
| `gi_vbus.py` | **The host-confinement guard for the virtual bus.** A trimmed copy of the interposer's `bus.py`, made when this folder moved into the firmware repo and could no longer import it. Opens only the `virtual` transport, sets `IP_MULTICAST_TTL` to 0 and **verifies it by reading it back**. Note what it does on a non-zero hop limit: it *corrects* it rather than refusing, and refuses only if the set fails, if the read-back disagrees, or if it cannot find the socket to check at all. That last case used to return silently -- fail-open exactly where a python-can internal rename would land -- and now raises. Why any of this matters: `python-can`'s `udp_multicast` defaults to `hop_limit=1` and `239.0.0.0/8` routes out the default interface, which on 2026-09-01 put 66,450 packets/s on the house LAN for twenty minutes. |
| `test_gi_vbus.py` | Tests for the guard, including that each bench entry point actually routes through it. |
| `test_publish_scan.py` | Checks this folder is safe to publish: no credentials, SSIDs, MACs, private addresses or absolute paths committed. |
| `requirements.txt` | `python-can`. The core, the offline bench and the tests need nothing. |

### Settings that have no committed default

Three things that used to be hard-coded are now settings, because this folder
is published in a public repo and they are either secrets or specific to one
machine. Each refuses or falls back loudly rather than guessing.

| Setting | Used by | What it is |
|---|---|---|
| `--ssid` or `$GI_WICAN_SSID` | `bench_flash.py` | The WiCAN's SoftAP SSID, which embeds the device MAC. The script refuses to run without one. |
| `$GI_RUNS_DIR` | `bench_flash_guard.py`, `bench_ota_guard.py` | Where run records are written. |
| `$REVERSE_IT_ROOT` | `replay_inhibit.py` | The `reverse-it` checkout, for `canre` and the capture logs. Never a committed absolute path. |

## Quick start

```bash
python3.13 projects/vtrux/tools/gen_inhibit/test_core.py
python3.13 projects/vtrux/tools/gen_inhibit/replay_inhibit.py --run
python3.13 projects/vtrux/tools/gen_inhibit/replay_inhibit.py --sweep
python3.13 projects/vtrux/tools/gen_inhibit/live_bench.py

# rehearse the on-vehicle tool against a simulated truck, two terminals
python3.13 projects/vtrux/tools/gen_inhibit/bench_vehicle.py --receiver counter
python3.13 projects/vtrux/tools/gen_inhibit/vehicle_runner.py \
    --interface udp_multicast --channel 239.0.2.4 --port 43217 --live

# on the truck: observe first, always
python3.13 projects/vtrux/tools/gen_inhibit/vehicle_runner.py --channel can0
```

Saved output is in `../../notes/artifacts/gen-inhibit/`.

---

## The three polarities

You cannot erase a frame from a CAN bus by transmitting one; the VCM's frame
always reaches the GENE MCU. Inhibition works one layer up -- by being the
frame it acts on, or by making the genuine frame the one it discards. Which
applies depends on how it consumes 0x051, which is not observable from the bus.

| | what it does | wins against | loses against |
|---|---|---|---|
| `lead` | transmits in the gap **before** the VCM's predicted slot, carrying the counter value the VCM is about to use | a receiver that validates the rolling counter -- the genuine frame then looks like a repeat and is discarded, so it is never acted on **at all** | a receiver that just obeys the newest frame: ours is immediately superseded |
| `trail` | transmits a short fixed delay **after** each genuine frame, carrying the next counter | both -- it steals the next counter value *and* is the most recent frame for most of each slot. Needs no forward prediction. | nothing outright, but it cannot stop the genuine frame being briefly the freshest |
| `bracket` | both | everything considered | -- |

`bracket` is the default. It costs about 4.5 % extra bus load (0x051 at 100 Hz
is roughly 2.2 % of a 500 kbit/s bus).

Note that the "just before" polarity on its own is the *weaker* half: it is the
only one that can reduce the genuine frames acted on to zero, but only if the
GENE MCU validates the counter, and it does nothing at all if it does not.

---

## The thing that can break the truck

Two nodes transmitting the **same CAN ID** at overlapping times do not
arbitrate. The identifier fields are identical, so both survive arbitration and
run on into the data field, where the first differing bit makes one of them
detect a bit error. That destroys the frame and raises both nodes' transmit
error counters; repeated at 100 Hz it walks the VCM toward error-passive and
then bus-off. A VCM that has gone bus-off is a truck that has stopped.

So every transmission is gated. The core refuses to transmit unless it can
place the frame wholly clear of the VCM's predicted slot by more than three
times the synchroniser's own recent 99th-percentile phase error. If it is not
locked, if the prediction has gone loose, or if the VCM has gone quiet, it
sends nothing. **Silence is the safe state** -- the truck then behaves exactly
as it does today.

In every bench configuration run so far, including deliberately hostile ones,
the collision count is **zero**, and where the guard cannot be met the frames
transmitted drops to zero rather than the frames colliding.

---

## Results

Against the real crank sequence, all figures as a percentage of the torque the
GENE MCU would have been commanded with no inhibitor:

| receiver model | torque still delivered |
|---|---|
| validates the rolling counter (four variants) | **0.00 %** -- 574 of 600 genuine frames rejected |
| acts on every frame as it arrives | 13.8 % |
| obeys the newest frame on its own control tick | 0.0 % at three of four tick phases, 14.1 % at the fourth |

Jitter is the binding constraint. Suppression holds to about **+/- 0.75 ms** of
VCM transmit jitter; past +/- 1 ms the guard band starts refusing, and by
+/- 2 ms the inhibitor transmits nothing at all. Feeding the raw batched logger
timestamps in, which look like several ms of jitter, it never locks and sends
zero frames -- the intended failure.

### The one case that beats it

A receiver that reads a mailbox on its own control tick, where that tick falls
in the short window between the VCM's frame landing and our reply landing. Then
neither polarity helps and that tick obeys the real command.

That window is exactly our own interrupt-to-wire latency. Sweeping the tick
phase across a slot at 0.8 ms reply latency, 7 of 50 phases leak more than a
quarter of the command and the worst leaks all of it; at 0.15 ms reply latency
that falls to 4 of 50. It is not a permanent condition -- the VCM's transmit
clock and the GENE's control clock are independent, so the phase drifts through
the vulnerable window rather than sitting in it -- which matches what the
offline bench sees: occasional isolated 10-30 ms leaks rather than a sustained
one. **Reply latency is the number to minimise in the firmware.**

---

## Open questions before this goes near the truck

1. **Real transmit jitter of the VCM's 0x051.** Unmeasured, and it decides
   everything. Needs a hardware-timestamping interface or a scope on the bus,
   not another look at these captures.
2. **Does the GENE MCU validate B5?** Not observable from the bus. It is the
   difference between total suppression and roughly 86 %.
3. **What the VCM does when the generator does not respond.** It has 0x471
   torque feedback, so it will see a generator that was commanded to crank and
   did not. Expect diagnostic trouble codes at minimum; whether it retries
   harder, or enters a limp mode, is unknown.
4. **`mirror` versus `idle` payload.** `mirror` copies the VCM's frame and
   overwrites only the torque field, so the RPM reference the GENE inverter
   cross-checks against measured RPM stays consistent. `idle` sends the
   byte-exact engine-off command, which is more emphatically "off" but
   null-references an engine that may be turning. `mirror` is the default for
   that reason.
5. **Hardware failsafe.** As with the charge interposer, a hung micro must
   degrade to silence on this ID, not to a stuck transmitter. A watchdog that
   leaves a queued 0x051 repeating is the worst possible outcome.

---

## Can this replace the MITM bridge?

`../../CANsmog/generator_runner.py` cuts the powertrain bus and sits in it, so
it rewrites every 0x051 on its way past. That is a strictly stronger position
than this one. Nothing it writes can collide with the VCM, no frame escapes it,
and it needs no timing argument at all. If you are willing to break into the
harness, it remains the better tool.

This is the version that plugs in. It trades determinism for not needing to cut
anything, and the price is set out above: a small residual leak against some
receiver models, a dependence on transmit jitter that is still unmeasured, and
a same-ID collision risk that has to be actively guarded against.

**And yes -- the same mechanism would drive the generator, not only stop it.**
`--target <counts>` is the value held once armed; zero is the inhibit case and
the default, but the machinery does not care what the number is. Holding +342
to backdrive the engine for a smog readiness run is the same transaction as
holding 0 to stop it cranking, and `test_core.py` covers both.

One asymmetry is worth stating before anyone relies on that. When an inhibit
leaks a frame, the generator briefly gets asked for torque it was going to get
anyway -- benign. When a *backdrive* leaks a frame, the command drops to the
VCM's value for a tick and the engine gets a chopped torque command while it is
running. `notes/vtrux-smog-pass.md` already documents a backdrive/HCU limit
cycle and a stall failure mode on that truck. A leak rate that is harmless for
inhibiting is not automatically harmless for driving.

---

## On the truck

**The step-by-step field procedure is in `HANDOFF.md`.** What follows is the
reasoning behind it.

**`vehicle_runner.py` transmits nothing unless you pass `--live`.** The default
mode locks onto the VCM's slot, measures how well it can predict it on your
hardware, and prints whether a live run would work -- before anything reaches
the bus. Run that first, every time.

It identifies the bus by content, never by channel number: 0x051 at 80-120 Hz,
extended 0x18xxxxxx frames present, no 0x0C1 or 0x1F5, 0x471 present. It
refuses to run if the fingerprint does not match.

**Arm and disarm** follow `generator_runner.py`: Enter arms, Enter disarms,
Ctrl-C exits. `--auto-arm-after` does the same on a timer if you would rather
watch the engine than a keyboard.

**It refuses to arm while the generator is running.** Taking over a loaded
generator and commanding zero sheds the engine's whole load in one frame. The
only transition it will engage on is the one worth testing: engine off, VCM
tries to start it, we stop it. `--allow-running` overrides that and should not
be used on the truck without a reason.

**It disarms itself** on `0x617 B7 = 0xCA`, on a CAN error frame, on a failed
send, if 0x471 stops arriving, and if its own transmit rate ceiling trips.

### Telling a working inhibit from a broken inverter

That distinction is the whole point of the verdict block, and it is checked in
this order:

| Verdict | Means |
|---|---|
| `TRANSMIT STARVED` | We did not actually get on the bus. **Nothing else in the report is a result about the truck** -- it is a result about the host's timing. |
| `INHIBIT HELD` | The VCM commanded torque while we were armed and the inverter's own 0x471 feedback never followed it. |
| `INHIBIT LEAKED` | It commanded, and the inverter produced real torque. |
| `NOT TESTED` | The VCM never asked for torque while armed. No win claimed. |
| `RECOVERY GOOD` | After disarm, the VCM is commanding and the inverter is tracking it again. |
| `RECOVERY LATCHED / DEAD` | 0x471 has stopped. The inverter has reset or dropped off the bus. **This is not an inhibit result, it is damage to the CAN state.** |
| `RECOVERY LATCHED` | 0x471 is still there, the VCM is asking, and the inverter is not answering. |

Rehearse all of these before the truck -- `bench_vehicle.py --latch-on-dup`
simulates an inverter that decides a duplicate rolling counter means the bus is
misbehaving, latches a fault and stops transmitting, so you can confirm the
runner reports LATCHED rather than success. Recorded results are in
`../../notes/artifacts/gen-inhibit/rehearsal/`.

### What Python can and cannot do here

Measured against the virtual bench, on the two machines this has run on. These
are properties of the host, not of the truck or of the method, which is why
`--preflight` re-measures them and prints a go/no-go rather than trusting this
table:

| | madhouse-debian (CPython 3.13) | the Windows laptop (CPython 3.12) |
|---|---|---|
| reply latency, frame received -> our frame queued | median **1.3-1.6 ms**, p99 **1.8 ms** | median **1.7-1.9 ms**, p99 **1.9-2.2 ms** |
| `bus.send()` call itself | p99 **0.13 ms** | p99 **0.16-0.20 ms** |
| phase-prediction error p99, listening only | 0.15 ms | 0.62 ms |
| phase-prediction error p99, while transmitting | ~0.9 ms | 0.60-0.76 ms |
| preflight verdict | GO | GO, **trail only** |

So: **`trail` is viable from Python, `lead` is marginal at best.** `lead` has to
place a frame at a predicted future instant, and a 0.9 ms p99 phase error
against a 2 ms lead leaves very little. `trail` only answers a frame it has
already received and does not care. The cost of running trail-only is the
1.3-1.6 ms shadow -- against a receiver that simply obeys the newest frame,
that is the window the VCM can still be obeyed in, and it is ten times what a
microcontroller would give you.

On the laptop `lead` is not merely marginal, it is **refused**: the runner
computes a guard band of 1.8-3.1 ms from the measured phase error and reports
`lead viable here : False (lead 2.0000 ms < needed 2.6-3.1 ms)`. The laptop is
15-25% slower on reply latency and 4x worse on listening phase error, both
comfortably inside what `trail` needs. Run trail there and do not override it.

### Releasing the dongle

`run()` shuts the bus down on its own way out, but two exits raise `SystemExit`
before reaching it: `no CAN interface detected`, and the fingerprint refusal.
A Kvaser channel left open that way stays claimed and the next invocation cannot
have it -- at the truck that reads as a dead dongle. `main()` therefore shuts the
bus down in a `finally`, which is idempotent. Symptom if this ever regresses:
`KvaserBus was not properly shut down` on the refusal path.

### The clock, on Windows

`time.monotonic()` is `GetTickCount64()` on Windows -- **15.625 ms resolution**,
coarser than the whole 10 ms slot this tool has to hit. Every interval measured
through it quantises to a tick, which surfaces as a phase error of about half a
tick and a reply latency that reads as exactly `0.000 ms`. The first laptop
preflight failed this way: `phase error p99 8.4958 ms`, `locked: False`, NO-GO.

`vehicle_runner.py`, `bench_vehicle.py` and `live_bench.py` therefore take every
timestamp from `_now = time.perf_counter` -- `QueryPerformanceCounter()` on
Windows, `clock_gettime(MONOTONIC)` on Linux, monotonic and sub-microsecond on
both. `inhibit.py`, `sync.py` and `models.py` receive timestamps as arguments
and never read a clock, so they were unaffected.

**Any timing measured on a Windows host before 2026-09-06 is void.** A run that
reports a reply latency of exactly `0.000 ms` is reporting a broken clock, not a
fast one.

The runner reports `lead viable here` / `trail viable here` from measured
numbers rather than assuming, and the core refuses either polarity it cannot
justify. If the truck needs `lead`, it needs a microcontroller, not a laptop.
