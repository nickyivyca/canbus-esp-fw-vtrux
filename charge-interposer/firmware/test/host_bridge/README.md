# host_bridge/ — spec 9.1 L2

The firmware's I/O layer, compiled for a PC against fake peripherals. What
L2 covers that nothing else can: the diagnostics cadence and fields, the
spec 2.2 counters and the 100 ms age-out, a charger segment with nothing
acknowledging, bus-off, and the bridge-failed status frame.

Run: `wsl bash ".../firmware/test/host_bridge/run.sh"` (plain `bash` on
Linux). Builds with `g++ -std=c++11`, globbing the sources rather than
listing them — a hand-kept source list going stale is how the
generator-inhibit session built against old headers three times.

| file | what |
|---|---|
| `mcp2515_fake.h` / `.cpp` | Constants, the status table, and the bit-modifiable register set. **The status table is the source of truth for what every L2 result rests on**, and the run-time marking is generated from it |
| `mcp2515_fake_chip.h` / `.cpp` | The chip: register file, SPI instruction decode, transmit engine, error counters, virtual clock |
| `test_mcp2515_fake.cpp` | The model checked against the datasheet behaviours it claims |
| `conformance.h` / `.cpp` | **The conformance sequence: one script, two subjects.** Eleven steps (C1-C11) driven entirely over raw SPI, emitting a diffable transcript |
| `test_conformance.cpp` | Runs the sequence against the fake, prints the transcript, and asserts the DATASHEET-grade lines |
| `driver/twai.h` | The ESP-IDF TWAI API, host side, so `port_twai.cpp` compiles unmodified. Types and documented return values taken from the toolchain's own header |
| `twai_fake.h` / `.cpp` | The TWAI driver model: four states, `ESP_ERR_INVALID_STATE` on every wrong-state call, config-sized RX queue, injectable FIFO overruns, bus errors and bus-off |
| `test_twai_port.cpp` | `TwaiPort` against that model -- the first executed test of `rxDropped()` and `busOffEvents()` |
| `test_mcp2515_busoff.cpp` | Bus-off on the charger port: the error-mode walk against the chip, and `Mcp2515Port::busOffEvents()` through the driver |
| `provenance.h` / `.cpp` | The status scale, shared by both fakes so they cannot drift apart |
| `driver/twai.h`, `esp_app_desc.h` / `.cpp` | Shims that let `main.cpp` itself compile on the host |
| `test_l2_bridge.cpp` | **The real bridge loop**: `src/main.cpp`'s `setup()`/`loop()` driven against both fakes |

## L2 proper: the whole bridge loop

`test_l2_bridge` compiles **`src/main.cpp` itself** -- its `setup()`, its
`loop()`, its file-static ports, its dispatch and diagnostics -- and drives
it on the virtual clock. Nothing is reimplemented, which is the point: the
diagnostic cadence, the spec 2.2 counter and spec 8.2's `bridge_ok` are
properties of that file, and a test that reproduced them would be checking
its own copy.

Built with **`-DINTP_NO_SERIAL=1`**, so it exercises the **truck image's**
code path -- the one that actually goes in the vehicle. A phase asserts
`intp_serial` reads 0, which is how a truck image is identified on the
wire.

`main.cpp` keeps its state in file statics with no way to reset them, so
this is ONE bridge lifetime read in chronological order rather than
independent cases. The phases are numbered and each says what it relies on
from the one before; phase 1 asserts the bridge actually came up, because
every later phase is vacuous if it did not.

### It immediately found that the RX path had never run

`Mcp2515Port::service()` drains `while (digitalRead(int_) == LOW)`, and
**the fake never drove the INT pin**, so the host pin map returned HIGH
forever and the driver's entire receive path had never executed. The
existing tests did not notice because the EFLG/overrun handling sits
*after* that loop, so overruns were still counted and
`test_canport_contract` passed.

Fixed by modelling INT as what it is: open-drain, active low, and
LEVEL-triggered on `CANINTF & CANINTE` (p24 s7.0). With it, the bridge
forwards 79 frames in the run instead of 0.

### Three faults in the harness itself, all the same shape

Worth listing because each produced a green or plausible result:

- **`bridge_ok` read from the wrong bit.** Guessed B4 bit 1, which is
  `chg_seen_charging` and is never set here, so "an overrun clears
  bridge_ok" passed on every frame ever sent. The schema says **bit 6**.
  Now written as a three-state sequence -- set, then cleared, then set
  again -- which no constant bit can satisfy whichever bit it is.
- **The build id decoded big-endian**, reading `0xE0A55ED1` for
  `0xD15EA5E0`. A byte reversal reads as a wrong value rather than a wrong
  decode.
- **`run.sh` built the `defs` array and never passed it**, so
  `INTP_NO_SERIAL` was missing and the test did not build at all -- while
  building fine by hand, which is the confusing direction.

## Bus-off on the charger port

Added 2026-10-04, completing spec 9.1's bus-off coverage -- the TWAI side
had it and the MCP2515 side was declared `NOT_MODELLED`.

**The error source is the whole point, and it is a second one, not the
existing one.** Per ISO 11898 an error-passive transmitter stops counting
*acknowledge* errors, which is why the no-ACK path pins TEC at 128 and can
never reach bus-off -- the `DEFERRED` item, and the reason the 100 ms
age-out exists at all. A **form, bit or stuff** error (p47 s6.3-6.5)
carries no such exception, so `setBusErrors(true)` drives TEC up by 8 per
attempt until it exceeds 255. The two sources stay separate and a test
asserts the no-ACK one still cannot reach bus-off.

Datasheet, confirmed by text extraction **and** the rendered page:

| fact | cite |
|---|---|
| error-passive at TEC >= 128; bus-off when TEC **exceeds** 255 | p47 s6.7 |
| recovery is 128 occurrences of 11 consecutive recessive bits (2816 us at 500 kbit) | p47 s6.7, p48 Fig 6-1 |
| recovery needs **no MCU intervention** -- a real difference from the TWAI port, which `service()` must restart | p47 note box |
| the edge out of bus-off returns to **error-active**, so both counters clear | p48 Fig 6-1 |
| nothing is transmitted or received while bus-off | p47 s6.6 |

Because bus-off needs TEC to *exceed* 255, the counter cannot live in the
8-bit TEC register; the model keeps it wider and the register carries
`min(tec, 255)`.

Figure 6-1 labels the error-passive edges `REC > 127 or TEC > 127` and
s6.7's prose says "equals or exceeds 128". These are **the same
threshold** -- the counters are integers -- so the figure and the prose
agree. Noted explicitly because an earlier draft of this README recorded
it as a discrepancy with the prose overriding the figure, which would
have sent the next reader looking for a conflict that is not there.

### Mutation-tested

`$VTRUX_DATA/notes/artifacts/interposer-firmware/busoff_mutations.sh` (SeaDrive).
B (transmitting while bus-off), C (recovery not clearing TEC), D (a 128-bit
instead of 128x11 window) and E (removing the no-ACK pin, so the two error
sources collapse into one) are each caught on their own check.

**A is an equivalent mutation and is asserted to survive.** `>= 255` and
`> 255` fire on the same attempt because TEC moves by 8 from 0 and never
takes the value 255. The predicate stays faithful to the datasheet wording
rather than being loosened to whatever a test can defend.

Two faults the script found in the test itself, both the usual shape:
offering **one** frame meant bus-off unloaded the only pending buffer, so
"nothing is transmitted while bus-off" passed with the guard deleted; and
the first version sampled TEC at fixed times computed by hand, all of
which landed after the ~11 ms entry-and-recovery cycle had finished and
read `TEC=0` as though nothing had happened. It now watches for the event
instead of predicting when it occurs.

## The TWAI fake

Written 2026-10-04, and it existed to answer a specific embarrassment:
**`TwaiPort::rxDropped()` and `busOffEvents()` had no executed test at
all.** `busOffEvents()` was added to the `CanPort` interface with nothing
calling it, and the `rxDropped()` delta-accumulation fix was
compile-verified only. Both carried a defect.

Derived from the toolchain's own
`framework-arduinoespressif32-libs/esp32s3/.../driver/twai.h`, never from
`port_twai.cpp` -- the same rule as the MCP2515 fake, for the same reason.

**The detail that makes the test real:** per that header,
`twai_initiate_recovery()` ends in **STOPPED**, not RUNNING. That is why
`TwaiPort::service()` calls `twai_start()` on seeing `TWAI_STATE_STOPPED`.
A fake that recovered straight to RUNNING would make that branch dead code
and it would never be tested.

Losses are produced by the real mechanism rather than injected: the RX
queue is sized from the port's own `rx_queue_len`, so delivering more
frames than the port drains fills it and the overflow is booked to
`rx_missed_count` exactly as the controller would.

### The defect it found

`bus_off_events_` **under-counted**. `st` is a snapshot, so the pass that
restarts a recovered controller reads `STOPPED` and never reaches the
`state == RUNNING` test that clears `bus_off_`. The flag then cleared only
on some later pass, and because the increment is gated on `!bus_off_`, a
genuine second bus-off arriving before that pass was swallowed. It reaches
spec 8.2: `bridge_ok` would read fine across a bus-off it never booked.
Fixed by ending the episode on a successful restart.

### Mutation-tested, including one mutation that must SURVIVE

`$VTRUX_DATA/notes/artifacts/interposer-firmware/twai_mutations.sh` (SeaDrive) puts
each defect back. A and B (the max-form, and the unclosed bus-off episode)
are caught on their own checks, as is D (dropping `rx_overrun_count` from
the loss figure).

**C is asserted to survive, and that is deliberate.** Removing the
`if (!bus_off_)` guard changes nothing any honest test can see, because
`TWAI_ALERT_BUS_OFF` is documented as "Bus-off condition **occurred**" --
an edge raised on the transition and consumed by `twai_read_alerts` -- so
a correct driver never raises it twice in one episode. The guard stays as
defence rather than being deleted on the strength of a surviving mutation.
Asserting the survival turns it into a tripwire on the **fake**: if it ever
starts being caught, the model has begun re-raising BUS_OFF within an
episode, which contradicts the header.

## The conformance sequence

Written 2026-10-04. The same sequence is meant to run against the fake
(anywhere) and against the real MCP2515 (on the bench), and the two
transcripts diffed. The status on each line says how to read a difference:
a **DATASHEET** line that differs means the model or the reading is wrong;
an **UNKNOWN** or **DEFERRED** line that differs is the bench answering
what the document would not.

The fake's transcript is kept at
`$VTRUX_DATA/notes/artifacts/interposer-firmware/mcp2515_conformance_fake.txt` (SeaDrive).

**It does not name buffer numbers on the wire, and that is deliberate.** A
dongle cannot see which of TXB0-2 a frame came from, so a sequence that
asserted on buffer numbers would be unrunnable against the only subject
that can settle the ordering questions it exists for. Every frame carries
a buffer tag in `data[0]` and the observable is the order of tags.

| step | what it settles |
|---|---|
| C1 | RESET defaults and that RESET enters Configuration mode |
| C2 | BIT MODIFY both ways, including the **silent widening** of the mask to FFh on a non-modifiable register, and that TEC is not host-writable |
| C3 | The READ STATUS bit map -- the orientation anchor the driver's TXnIF reads depend on |
| C4 | When ABTF/MLOA/TXERR clear (**UNKNOWN**, not asserted) |
| C5 | The p15 s3.2 tie-break under **one** RTS. A clean datasheet test |
| C6 | The driver's one-RTS-per-buffer pattern. C5 and C6 must **differ** -- that difference is the finding. But C6's line is **UNKNOWN, not DATASHEET**: on the fake the order is determinate, while on silicon it depends on how long three RTS instructions take against the frame already on the wire, so **the bench measures a rate there, not an order**. It was marked DATASHEET until the review session caught it, which would have made a different order on the real chip read as a model or datasheet error when it is neither |
| C7 | TXP beats buffer number. Its expected order is the exact reverse of C5's, so it cannot pass by accident if C5 does |
| C8 | Same identifier, varying DLC (**UNKNOWN** -- spec 2) |
| C9 | A segment with nothing acknowledging: the TEC trajectory and whether bus-off is reached (**DEFERRED**, the highest-value bench item) |
| C10 | Aborting a buffer that has not started |
| C11 | Receive-overrun flags and clearing them |

**The assertions say what the FAKE must do, and never predict silicon.**
Freezing a guess about the real chip into a test means the bench can no
longer contradict it without "breaking" something. Most assertions are
therefore datasheet-grade; C6 is the exception and is asserted only
because the fake's order is determinate and the assertion guards the
model's RTS handling -- its transcript line stays UNKNOWN.

### It was mutation-tested before being trusted

`$VTRUX_DATA/notes/artifacts/interposer-firmware/conformance_mutations.sh` (SeaDrive)
breaks one DATASHEET behaviour at a time and requires the sequence to fail
**on the line that behaviour belongs to** -- a failure somewhere else is
incidental coverage and the script says so. Five mutations, all caught:
inverted tie-break, BIT MODIFY honouring the mask everywhere, TXP ignored,
the no-ACK limit removed, and TXREQ left set after a successful send. Each
mutation also verifies that it applied, because a `sed` that matches
nothing produces a clean run that reads exactly like the test passing.

Inverting the tie-break (A) is the one worth knowing: it collapses C5 and
C6 onto the same order, which is precisely the distinction the TX-ordering
question turns on.

### Two defects this sequence found in itself

Both are the recurring shape -- a check that passes while testing nothing:

- the no-ACK checkpoints were **absolute**, and the earlier steps had
  already advanced the clock past the first few, so all five TEC samples
  landed at the same instant and still printed as a trajectory;
- the forced-mask step used **TEC**, which is *also* read-only, so the
  write was dropped before the mask mattered and the step returned the
  same value a chip honouring the mask would give. Two properties, one
  observation, ambiguous between them. It now uses `TXB0SIDH`.

## Where the model comes from

Microchip **DS20001801J**, in reverse-it's `projects/vtrux/tools/interposer/firmware/datasheets/`
(not moved to this repo), never from
`port_mcp2515.cpp`. A fake that borrows the driver's opcode map agrees with
the driver's mistakes, and a conformance sequence written from the same
borrowed map then agrees with both.

Constants were derived from the document first and diffed against the
driver afterwards: **0 mismatches in 31**. That is a result about the
driver — it is talking to the chip the document describes — and not a
reason to have copied it.

Every citation is confirmed **both** by text extraction **and** by the
rendered page (user's rule, 2026-10-04). Neither alone is enough, and this
document shows why in two different ways:

- **table shading is invisible to text extraction.** Which registers accept
  BIT MODIFY is marked only by shading in p63 Table 11-1; the dump carries
  the note explaining what shading means and not one bit of which cells
  have it;
- **figure labels do not dump in bit order.** The p70 READ STATUS labels
  come out of the text dump in a different order from their actual bit
  positions. Taken alone it gives a wrong bit map.

A table with explicit `Bit 7 … Bit 0` headers, like p63 Table 11-2, *is*
safe to read from the dump. A figure is not.

## The status table

Six statuses, in `mcp2515_fake.h`, descending by how much weight a result
resting on them can bear: **MEASURED** (our bench) → **DATASHEET** →
**DEFERRED** → **RECALLED** → **UNKNOWN** → **ASSUMED**.

`DEFERRED` is its own status because "the document explicitly declines to
say" is different from "nobody has looked", and the bench should prioritise
it accordingly.

### Not yet resolved

| status | item |
|---|---|
| **DEFERRED** | A transmitter with nothing acknowledging stays error-passive and never reaches bus-off. p47 §6.7 gives the thresholds and hands every increment rule to "the CAN bus specification". **Modelled that way.** If it is wrong, a silent charger segment cycles bus-off every ~2.8 ms, `busOffEvents()` climbs all through every drive, and spec 8.2's `bridge_ok` reads clear on every status frame of every journey — a flag that is always false says nothing. The 100 ms age-out exists for this case, so it is the most valuable item to settle |
| **UNKNOWN** | When ABTF/MLOA/TXERR are cleared: p15 §3.3 says "when TXREQ is set", the p17 flowchart clears them at the top of every attempt. Decides whether TXERR can read 1 on a frame that eventually went out. Modelled as the flowchart |
| **UNKNOWN** | Reloading a buffer whose aborted frame is still on the wire. p15 says only to write when TXREQ is clear, and our abort clears it early |
| **UNKNOWN** | Which overflow flag a BUKT rollover sets |
| **UNKNOWN** | Whether frames sharing an identifier keep their order (spec 2, corrected 2026-10-04) |
| **RECALLED** | CNF1-3 writable only in Configuration mode; mode-change latency; CS high aborting an instruction mid-way |
| **ASSUMED** | SPI byte time; arbitration against other nodes — the fake has no shared medium, so **any load-dependent result needs the bench whatever this shows** |

## What the model found about our own driver

With **one RTS instruction** requesting all three buffers, the p15 §3.2
tie-break sends TXB2, TXB1, TXB0 — newest-first, as the generator-inhibit
session predicted.

But `loadAndSend()` issues **one RTS per buffer**, so TXB0's request
reaches an idle controller and starts transmitting before TXB1 and TXB2 are
requested at all. The tie-break then only orders what queued behind it.

That refines the hypothesis rather than contradicting it, and it fits the
bench data better than the original form did: the transposition is rare,
always by one position, and needs varying frame lengths — because the
length of the frame already on the wire decides whether the next buffer is
still waiting when the one after it is requested.

**The fake hid this from itself at first.** Its RTS handler applied the
instruction's three bits one at a time and started the engine after each,
so a single RTS behaved like three and the tie-break could never be
observed. Fixed; both cases are now tested separately.

## Standing rule for this folder

**Every DATASHEET behaviour gets a test that asserts it against the chip
model DIRECTLY, not through the driver.**

The RTS defect above is why. A model exercised only through
`port_mcp2515.cpp` is a model whose observable rules are chosen by the
driver's usage pattern: the driver issues one RTS per buffer, so a bug that
made a multi-buffer RTS behave like three separate ones was invisible from
that direction, and the rule it broke was the one the whole TX-ordering
investigation turns on.

A fake wrong in the same direction as a defect confirms the defect. A fake
wrong in the direction that makes a rule unobservable is worse, because
everything stays green and the rule simply never gets tested.
