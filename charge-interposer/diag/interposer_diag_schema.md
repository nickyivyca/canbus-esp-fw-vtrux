# Charge interposer diagnostic frames -- schema (source of truth)

The interposer broadcasts its own state on the **vehicle side** of the bridge so
any Android or laptop log of a run is self-documenting: which build ran, what
state the core was in, what it believed about the pack and the charger, and
exactly what it sent the charger. Modelled on the WiCAN generator inhibitor's
scheme (`wican_diag_schema.md`): fixed-ID frames, a
hand-maintained schema, an on-wire schema version as the decoder's guard.

This doc is the source of truth. The DBC (`vtrux-interposer-diag.dbc`, beside
this file), the Python bench (`interposer_sim.py`) and the firmware (`main.cpp`)
are hand-kept in sync with it. Behavioural spec: the charge interposer spec,
section 8.

Everything here is best-effort: a frame that cannot be queued is dropped and
counted (`diag_tx_fail`); bridged traffic is never delayed or reordered for it.
Nothing diagnostic is ever transmitted toward the charger.

## 1. Mirror frames -- what the charger actually received

Every frame the core **modifies or repeats** is rebroadcast with the J1939
source-address byte (the low byte of the 29-bit ID) replaced by **0xE1**. The
payload is byte-for-byte the frame the other segment saw, so the original
message's DBC layout decodes it under a cloned definition.

| original | mirror | mirror carries |
|---|---|---|
| `0x18EFC000` VCU_ChargerCmd (all pages) | `0x18EFC0E1` | the frame **as sent to the charger** -- the rewritten page 00/01/03 frames of an override, the rewritten page-01 frames of the hold (spec 5.2), and the repeats of the VCU's standing page-00 command (spec 4.1) |
| `0x18FFD7C0` BELINV_hvBatteryAndCharger | `0x18FFD7E1` | the frame **as received from the charger**, only when `mask_charger_telemetry` is on (default off) |

Rules:
- unmodified frames are never mirrored; outside an override, its repeat
  bursts and the hold (spec 5.2) the mirror IDs are silent;
- the mirror of `0x18EFC000` keeps destination byte C0, so the VCU (SA 00)
  ignores it and the charger, on the other port, never sees it;
- 0xE1 is unused on this truck. Source addresses seen in the corpus:
  00, 20, 40, 47, B3, C0, FE (`SHOW TAG VALUES ... can_id`, 2026-09-16).

## 2. Status frames

Four standard 11-bit IDs, DLC 8, little-endian. Each of the four is sent at
~1 Hz -- all four together once per second, back to back (~0.3 ms apart),
not one of the four per second in rotation. Plus an
immediate `0x7F4` on every core state change. `0x7F4-0x7F7` sit beside the
WiCAN's `0x7F0-0x7F3`; nothing on the truck consumes the `0x7Fx` range.

### 0x7F4 -- INTP_Status
| Byte | Field | Meaning |
|---|---|---|
| B0 | intp_schema_ver | on-wire schema version (currently **4**) |
| B1 | intp_fw_ver | firmware build number (table in section 4) |
| B2 | intp_state | 0 PASSTHROUGH, 1 MONITOR, 2 OVERRIDE, 4 TERMINATED, 5 SAFE, 6 HOLD (`machine.py` STATE_NAMES); 3 reserved (was RELEASING, removed in schema 2). HOLD (schema 4, spec 5.2): after the release, page 00 forwarded, our page-01 setpoint; TERMINATED stays transparent |
| B3 | intp_trip_reason | 0 none; otherwise the `TripReason` enum of `machine.h`: 1 vmax over hard ceiling, 2 cell_overvolt, 3 **retired** (was cell_undervolt; never emitted from fw 4 -- spec 6, B-7a), 4 BMS EPO, 5 BMS alarm TYPE3, 6 HVIL open, 7 contactors not closed, 8 charger fault, 9 charger out of state 12, 10 BMS frames stale -- *which* message is in the event log, not in this byte, 11 charger frames stale, 12 override exceeded the 6 h cap |
| B4 | intp_flags | bit0 bms_seen, bit1 chg_seen_charging, bit2 mainc_closed, bit3 evse_cap_known, bit4 vcu_flow, bit5 override_active (1 while the core is rewriting the VCU's commands: OVERRIDE, pages 00/01/03, and HOLD, page 01; schema 4), bit6 bridge_ok (see below), bit7 serial_attached |
| B5 | intp_our_ilim_x10 | our commanded current on page 01, u8, 0.1 A (255 = not commanding): the override command in OVERRIDE, the hold setpoint in HOLD (schema 4) |
| B6-B7 | intp_t_in_state_s | seconds since the current state was entered, u16 (saturates) |

**`bridge_ok` (B4 bit 6) is health since the PREVIOUS status frame**, not
since boot (spec 8.2, B-3). It is set when, over that interval, all of these
held:

- no RX overflow on either port, and no emit-list overflow;
- neither controller **entered** bus-off. The edge, not the level: recovery
  is automatic (spec 2, B-7c), so a transient that begins and ends between
  two status frames is invisible to `busOff()` alone. Each port carries a
  monotonic `busOffEvents()` and the flag compares counts across the
  interval;
- **no bridged frame failed to reach the vehicle** (added 2026-10-04). Once
  the 2.2 counter became charger-only these were in no CAN field at all, and
  a charger frame that never reaches the VCU is a transparency failure.

...and neither controller is bus-off right now, and both started.

**The board's own diagnostic frames do not count** toward that third item:
neither the 8.2 status frames (tallied separately, and a status frame that
cannot be sent is already visible by its absence) nor the 8.1 mirrors, which
are not bridged traffic. Mirrors travel the same path toward the vehicle, so
the firmware tells them apart by their 0xE1 source address -- the same
premise the mirror scheme itself rests on.

**No schema bump for this.** The bit and its meaning -- "the bridge was
healthy over the last interval" -- are unchanged; only the set of things
that count as unhealthy is wider. A decoder reading schema 3 reads it
correctly either way.

The rule is `firmware/src/bridge_health.h`, kept out of `main.cpp` so it can
be compiled and tested on a host; `firmware/test/host_unit/` does that.

**A board whose controllers did not both start still sends `0x7F4`** (spec
2.1 / 8.2, B-7d), once a second, as long as the vehicle-side controller came
up -- with `bridge_ok` clear and the state reading PASSTHROUGH, because the
core has seen no frames. The other three frames are **not** sent: they carry
observations and counters that mean nothing on a board that is bridging
nothing. Without this a board that failed to start and a board that is not
fitted look identical in a truck log, and the two call for different repairs.

### 0x7F5 -- INTP_Observed
| Byte | Field | Meaning |
|---|---|---|
| B0-B1 | intp_vmax_mv | last `bcm_cell_vmax`, u16 mV |
| B2-B3 | intp_chg_max_ca | last `bcm_chg_max`, u16 centi-amps |
| B4 | intp_soc_half | last `bcm_soc`, u8, 0.5 % |
| B5 | intp_chg_state | last `BELINV_state` |
| B6 | intp_evse_cap_x10 | EVSE DC cap derived from the pilot, u8, 0.1 A (255 = unknown) |
| B7 | intp_vcu_mode (bits 0-3), intp_chg_silent_expected (bit 6), intp_evap_active (bit 7) | low nibble: last page-00 `cmd_Mode` from the VCU; bit 6: **charger silent while expected** (spec 2.2) -- the VCU's flow bit is 1 and no `0x18FFD4C0` has arrived for 500 ms; bit 7: last `vcm_evap_active` (0x649), the override gate's input (spec 3). Bits 4-5 reserved, sent 0. Bit 6 added at schema 3 |

### 0x7F6 -- INTP_Counters
| Byte | Field | Meaning |
|---|---|---|
| B0-B3 | intp_modified | frames rewritten toward the charger, u32 |
| B4-B5 | intp_synth | repeats of the VCU's standing page-00 command sent toward the charger (spec 4.1), u16 (saturates); 20 per state change |
| B6 | intp_rx_overflow | RX queue overflows on either port since boot, u8 (saturates) |
| B7 | intp_tx_fail_expected | **charger not acknowledging while expected** (spec 2.2): failed transmissions **toward the charger** while the VCU's flow bit is 1, since boot, u8 (saturates). Counts `txDropped` + `txFailed` + `txAged` on the charger port only, each failure once. It reads 0 while driving however many frames bounce off an absent charger, so any non-zero value is a problem **during a session**. A board that boots mid-session counts nothing until the VCU's next page 00 tells it the flow bit. Vehicle-side and diagnostic transmit failures are **not** in this byte; they are on the serial `LOSS` line. Redefined at schema 3 -- fw 1-3 counted every failure on both ports |

## The shared 0x7Fx allocation

Two components put diagnostics in this range and neither owns it. Mirrored
here 2026-09-25 from `wican_diag_schema.md` so the agreement
does not live in one file that only one of us reads.

| ID | Owner | What |
|---|---|---|
| `0x7F0` | WiCAN inhibitor | RESPOND timing probe -- **not** a diag frame, and in RESPOND it is one per received `0x051`, so it can be the highest-rate id on the segment |
| `0x7F1`-`0x7F3` | WiCAN inhibitor | diag STATUS / COUNTERS / BUILD |
| **`0x7F4`-`0x7F7`** | **charge interposer (this document)** | STATUS / OBSERVED / COUNTERS / BUILD |
| `0x7F8` | WiCAN inhibitor | diag STATUS2, added at their schema v4 |
| `0x7F9` | WiCAN inhibitor | diag SKIPS, added at their schema v5, 2026-09-29 |
| `0x7FA`-`0x7FF` | **free** | take from the top and tell the other component |

**This table has changed twice since it was written; treat it as a snapshot,
not a settled allocation.** `0x7F9` was listed as free here until 2026-09-29,
when the generator-inhibit session took it for a SKIPS page and told us.
Re-derive from the DBCs rather than trusting this copy -- `verify_diag_decode.py`
does exactly that, so the gate check needed no edit when `0x7F9` landed.

Verified independently 2026-09-25 by listing every `BO_` in `0x700`-`0x7FF`
across all of `projects/vtrux/*.dbc`: no id is declared twice. The only
other traffic in the wider range is `0x772`, `0x77A`, `0x77F` in
`GMW8762_PPEI_GMLAN.dbc` (generic GMLAN diagnostic IDs), well clear of
`0x7F0`.

### "Nothing on the truck consumes the 0x7Fx range" -- now MEASURED

**Zero frames in `0x7F0`-`0x7FF` across 2,204 captures**, both log trees.
Scanned 2026-09-25 by the generator-inhibit session with
`../gen-inhibit/diag_id_allocation.py` on `madhouse-debian`; output at
`artifacts/gen-inhibit/diag_id_allocation.txt` **on that machine**. Not
independently re-run here, and it cannot be: the Vtrux logs on `NICKY-XPS`
are SeaDrive placeholders (~1 MB local against ~70 GB apparent), so
scanning from this machine would hydrate the corpus.

The scan was demonstrably looking -- 58 distinct ids ARE present elsewhere
in `0x700`-`0x7FF`:

| id | frames | captures |
|---|---|---|
| `0x7DF` | 88,385 | 40 |
| `0x7E0` | 462,993 | 300 |
| `0x7E8` | 667,187 | 316 |
| `0x7E3` | 3 | 2 |
| `0x7B2`-`0x7CB` | 4 each | all in one capture |

The first four are the OBD request/response range, which also measures the
other half of the constraint: that part of `0x7xx` is busy and unavailable.
The `0x7B2`-`0x7CB` row -- 28 consecutive ids at exactly 4 frames each in a
single capture -- is **left uninterpreted deliberately**. It looks like
something sweeping a range, but which tool and when is not known, and
guessing is how the unmeasured claim became established in the first place.
It is well clear of both components' ranges; the capture name is in the
output file.

**What the null result does not cover.** It is the logged corpus -- what
the Android and BUSMASTER loggers captured on the buses they were attached
to. It cannot speak for a bus nobody logged, or for an ECU that transmits
only in a state the corpus does not contain. For the powertrain bus over
2,204 captures it is as strong as the data gets.

**Why this was checked at all.** The claim appeared six times across the two
components' specs, schema docs and firmware logs with no measurement behind
any instance -- established by repetition, and read as settled because it
was repeated. It happens to be true. It was not known to be, and it is the
assumption that makes it safe for either board to transmit in this range.
A measured claim and an inherited one read identically once written down,
which is the reason this paragraph names its source.

**The interposer's `bus.py` (`charge-interposer/bus.py` in `canbus-esp-fw-vtrux`; `tools/interposer/bus.py` until 2026-10-08) is NOT an allocation claim and must not be read
as one.** It uses `0x7F0` (delivered DC current) and `0x7F1` (pack terminal
voltage) for the *virtual bench's* physics link, which runs on its own UDP
segment and never reaches a CAN wire. Those ids predate this agreement and
overlap the WiCAN's. Checking `bus.py` answers "what does the simulation
put on a virtual segment", not "what does a board put on the truck's bus" --
and the WiCAN session did exactly that in September 2026, found `0x7F4`
apparently free, and put a new page on it. It was caught in review before
anything was flashed or transmitted.

**The authoritative sources are `vtrux-interposer-diag.dbc` and this file**
for `0x7F4`-`0x7F7`, and `vtrux-wican-diag.dbc` with
`../gen-inhibit/wican_diag_schema.md` for theirs. Check those, not a
simulator.

The transport is what separates the `0x7F0`/`0x7F1` collision in practice:
on a real dongle those are always the WiCAN; on the physics UDP port they
are always the bench. `vehicle_sim` already respects this -- it reads
`PHYS_CURRENT_ID` from `phys.recv()` and never from the CAN bus.

### 0x7F7 -- INTP_Build
| Byte | Field | Meaning |
|---|---|---|
| B0-B3 | intp_build_id | first 4 bytes of the application ELF's SHA-256, from the ESP-IDF app descriptor. The same sources give the same id on the same host OS; a different host OS gives a different id for the same flashed code (spec 8.2, 2026-10-08). **BYTE ORDER, added 2026-10-06 because this row did not state it and a decoder got it backwards:** decode as the Intel u32 the DBC declares (`0|32@1+`) and print `%08x` to get the hash's first four bytes the same way round as `elf_sha256` -- e.g. `7f197bfd`. **The raw payload is the reverse**: the firmware packs `sha[0]` as the u32's most significant byte (`main.cpp buildId()`) and then writes it little-endian (`machine.cpp put32()`), so B0-B3 carry `sha[3],sha[2],sha[1],sha[0]` = `fd 7b 19 7f`. Joining the payload bytes in order gives `fd7b197f`, which matches no manifest row and surfaces as an unknown build on a healthy board -- live in `build_lookup.build_id_from_payload` until 2026-10-06, where its own docstring warned of exactly that outcome while causing it. **Look it up in `charge-interposer/firmware/builds/manifest.json`** (repo `canbus-esp-fw-vtrux`, branch `charge-interposer/main`; `tools/interposer/firmware/builds/` until 2026-10-08), which every build updates and which maps this back to an image, environment and toolchain; `charge-interposer/build_lookup.py` does that from the wire and refuses rather than guessing. The manifest's `elf_sha256` is itself read from `esptool image-info <bin>` -> `ELF file SHA256`, which remains the way to check a file by hand before flashing. **fw_ver 1-2 carried `intp_git_hash` here instead**, which was always `fnv1a("unknown")` = 2608177081 |
| B4-B5 | intp_fw_ver16 | firmware build number, u16 |
| B6 | intp_schema_ver_build | schema version (echoed for a build-only decoder) |
| B7 | intp_uptime_min | minutes since boot, u8 (saturates) |

## 3. USB serial

The event lines (`[t_ms] STATE event a= b= c=`) and the periodic status line
are unchanged. One line per second is added:

```
diag st=<state> trip=<n> flags=0x<xx> ilim=<A> vmax=<mV> chgmax=<cA> soc=<%> evap=<0|1> chg=<n> mod=<n> synth=<n> ovf=<n> txf=<n> silent=<0|1> fw=<n> schema=<n>
```

with the same values as the four frames, so a bench run can be judged without
a CAN tap. `run_scenario.py` reads either.

## 4. Versions

`intp_schema_ver` = **4**. Bump on any layout change, and update the DBC, the
bench and this doc together. Every layout keeps the schema byte at `0x7F4` B0
and `0x7F7` B6 (user, 2026-10-19), so a decoder can choose the DBC by it. The
DBC states its schema as the `DiagSchemaVersion` attribute, with
`DiagSchemaHash` = `layout_hash` of the DBC (`shared/bench/vtrux_bench/decode.py`);
message, signal and choice names are in that hash, so a rename is a layout
change too, and a renamed signal carries `RenamedFrom`, its earlier names,
oldest first (user, 2026-10-19). Git history holds the DBC for every schema
from 4 on; no per-schema copies are kept. **A byte that keeps its position but changes
what it counts is a layout change for this purpose.** That is precisely the
case a version guard exists for: a decoder reading the old name off the new
frame gets a plausible number and no warning.

| schema | change |
|---|---|
| 1 | first layout |
| 2 | 2026-09-19: state 3 (RELEASING) removed; trip codes renumbered (cell temperature trips removed, spec 6); OBSERVED B7 bit 7 carries the evap flag; `synth` counts the spec 4.1 repeats |
| 3 | 2026-10-03 (spec 2.2; B-2, B-3): OBSERVED B7 bit 6 = charger silent while expected; COUNTERS B7 redefined from "every transmit failure on both ports since boot" to "failures toward the charger while the VCU's flow bit is 1"; STATUS B4 bit 6 `bridge_ok` now covers the interval since the previous status frame and counts bus-off entries. **Trip codes are deliberately NOT renumbered**: code 3 (cell_undervolt) is retired in place rather than removed, so every other code means the same thing at schema 2 and 3 and a log from either decodes identically |
| 4 | 2026-10-08 (spec 5.2, user): `intp_state` 6 = HOLD, the hold after the release (page 00 forwarded, our page-01 setpoint); its page-01 frames are mirrored; STATUS B5 in HOLD carries the hold setpoint; STATUS B4 bit 5 `override_active` is also set in HOLD (user, 2026-10-09). No byte moved |

`intp_fw_ver` (build number). Bump **`DIAG_FW_VER` in `machine.h`** -- and
the matching `DIAG_FW_VER` in `machine.py`, which `check_port.py` compares --
per meaningful firmware rev, and add a row here. (This paragraph named
`DIAG_FW_VERSION` in `main.cpp` until 2026-10-03. No such constant exists in
`main.cpp` or anywhere else, so the instruction could not be followed, and
the two cores drifted to 2 and 3 with only `check_port.py` to notice. B-2.)

| intp_fw_ver | build | notes |
|---|---|---|
| 1 | first build carrying diagnostics | release-by-charger-stop core (spec 5 as of 2026-09-16), mirror frames, status frames |
| 2 | spec of 2026-09-19 | evap-flag/SoC gate (spec 3), release and trip as transparency plus the repeat of the VCU's standing command (spec 4.1, 5, 6), teardown not a fault, 75 s arm delay, schema 2 |
| 3 | 2026-09-20, **C++ only** | `0x7F7` B0-B3 changed meaning from `fnv1a(GIT_SHA)` -- permanently `fnv1a("unknown")`, because `-DGIT_SHA` was never passed -- to the first four bytes of the application ELF's SHA-256. The wire layout is unchanged, so this version byte is the only thing that tells the two apart. `machine.py` was never moved off 2, so the two cores reported different firmware versions from 2026-09-20 to 2026-10-03 (B-2) |
| 4 | spec of 2026-10-03, review sections A and B | section A (teardown window removed, the charger's plug-out report ends the override, the 5 s charger-state debounce, the boot lock, session boundaries) **and** section B: a separate 500 ms staleness timer per BMS message (B-6), no `cell_undervolt` trip (B-7a), 20 repeat frames from every path including a tick-raised trip (B-1), the flow-gated charger transmit-failure counter and the charger-silence flag (spec 2.2), interval-scoped `bridge_ok` (B-3), a status frame from a board whose controllers did not start (B-7d). Schema 3 |
| 5 | spec of 2026-10-08 (5.2), and the spec 2 receive-order fix | the hold after the release (`intp_state` 6 HOLD): page 00 forwarded, our page-01 setpoint stepped 0.1 A on `bcm_ibat` against `bcm_chg_max`; section 6 trips and BMS staleness apply in HOLD, the 6 h cap does not, and a CHARGER command with the flow bit on ends it to PASSTHROUGH; STATUS B5 reports the hold setpoint and B4 bit 5 is set in HOLD. The MCP2515 port re-reads its status after taking RXB0 so that charger-to-vehicle frames keep their order across a rollover (spec 2). Schema 4 |
| 6 | spec of 2026-10-09 (3, 5.2) | arming, re-arming included, requires the charger's latest status to be state 12 with the contactors closed, not a state 12 seen earlier in the session. Schema 4 |
| 7 | spec of 2026-10-10 (4, 5.1, 5.2) | the override's command is not below a 6.10 A floor once `bcm_soc` reads 98 % or more; the release fires on `bcm_cell_vmax` >= 3585 mV for 1 s instead of `bcm_chg_max` <= 3.0 A; the hold steps down at `bcm_cell_vmax` >= 3600 mV and up only below 3595 mV, and moves to the EVSE cap at once whenever the cap is below it. Schema 4 |
| 8 | spec of 2026-10-13 (5.2) | the hold steps at most once per 0.28 s instead of 0.3 s, so a step lands on each of the VCU's page-01 frames (median gap 0.300 s on the truck) instead of on every second one; the clamp's cadence rules are unchanged apart from the figure. No frame differs in layout; only the rate at which the hold follows `bcm_chg_max` shows it. Schema 4 |
| 9 | spec of 2026-10-13 (2.3, 2.1, 8, 8.2) | the vehicle port is listen-only while the charger is silent: normal mode from the first charger data frame with source address 0xC0 on the charger port until 2 s after the last; at power-up one round of all four status frames, then listen-only; no diagnostic frames in listen-only, so a capture of a drive carries none; a board whose controllers did not both start sends that round with the bridge reported failed and then stays listen-only, and no longer repeats `0x7F4` at 1 Hz. No byte moved. Schema 4 |
