# WiCAN gen-inhibit diagnostic frames — schema (source of truth)

The WiCAN broadcasts its own state on CAN so any Android/laptop log is
self-documenting: which build ran, what mode it was in, whether/why the inhibit
was disabled, and how much it transmitted.

Modelled on the 914 telemetry approach (reverse-it's `projects/914/README.md`) but a level
simpler: no generator pipeline, and three plain fixed-ID frames rather than one
multiplexed ID. This doc is the source of truth; the DBC
(`vtrux-wican-diag.dbc`, beside this file) and the firmware (`gen_inhibit.c`,
`send_diag()`) are hand-kept in sync, guarded by an on-wire `diag_schema_ver` —
a decoder with a mismatched version flags it rather than trusting the fields.

## Frames

Standard 11-bit IDs, DLC 8, transmitted by the WiCAN, only while armed
(mode != OFF). `0x7F1-0x7F3` are in the 0x7Fx range nothing on the truck
consumes (same rationale as the 0x7F0 probe). All little-endian.

**FIVE pages since schema 5, round-robin at 240 ms each, so each page still
repeats every 1.2 s** (spec 10). It was four pages at 300 ms (also 1.2 s) at
schema 4, and three at ~1 Hz each before that. A decoder that measures a page's
cadence still has to read `diag_schema_ver` to know which pages to expect.
(Corrected 2026-10-04: this said 300 ms and 1.5 s, contradicting spec 10 and
the firmware's `diag_period_ms` of 240.)

### 0x7F1 — WICAN_Diag_Status
| Byte | Field | Meaning |
|---|---|---|
| B0 | diag_schema_ver | on-wire schema version (currently **7**) |
| B1 | diag_fw_ver | behaviour/capability revision, hand-bumped (see the rule and table below; the name is historical) |
| B2 | diag_mode | 0 OFF, 1 OBSERVE, 2 RESPOND, 3 INHIBIT, **4 PASSIVE** |
| B3 | diag_flags | bit0 disabled, bit1 bus_on (**controller running**, not driver installed -- meaning changed at schema 6), bit2 bus_ours, bit3 autoarm_build, bit4 shutdown_suppressed, bit5 inhibit_live, bit6 key_on, bit7 abort_latched |
| B4 | diag_disable_reason | 0 none, 1 m_mode, 2 low_soc |
| B5 | diag_shift_pos | last 0x639 shift_lever_pos (4 = M, 255 = unseen) |
| B6-B7 | diag_soc_x100 | last SoC, u16, scale 0.01 % (0 = not yet seen) |

### 0x7F2 — WICAN_Diag_Counters
| Byte | Field | Meaning |
|---|---|---|
| B0-B3 | diag_tx_ok | inhibit frames transmitted (u32) |
| B4 | diag_tx_fail | transmit failures (u8, saturates at 255) |
| B5 | diag_ctr_bad | 0x051 counter non-+1 steps (u8, saturates) |
| B6 | diag_rx_errors | non-timeout receive errors (u8, saturates) |
| B7 | — | reserved |

### 0x7F8 — WICAN_Diag_Status2  *(schema 4)*
| Byte | Field | Meaning |
|---|---|---|
| B0 | diag_abort_reason | which section 7 trip latched (0 none; see table below) |
| B1 | diag_flags2 | bit0 soc_valid, bit1 soc_since_valid, bit2 tx_pending, bit3 emit_refused |
| B2 | diag_arm_block | why the inhibit is not live (0 = it is; see table below) |
| B3-B6 | diag_would_tx | PASSIVE would-transmit count since the last arm (u32; since boot through schema 6) |
| B7 | diag_tx_queued_behind | inhibit frames queued behind an unfinished frame (u8, saturates) |

**Why a fourth frame rather than repacking the first three.** `0x7F1` is full
to B7 and `diag_flags` has no spare bit. Moving a field would mean a recorded
log could not be read without first consulting its schema version, and the
whole point of the version byte is to catch a *decoder* mismatch, not to make
every historical log conditional. Appending leaves `0x7F1`-`0x7F3`
byte-identical, so a schema-3 decoder reads everything it always did and simply
does not see this page.

> **`0x7F8`, and not `0x7F4`, which this first shipped as.** `0x7F4` is the
> charge interposer's `INTP_Status`. Both devices transmit on the powertrain
> bus and may be fitted at once, so that would have put two transmitters on one
> ID with differing data: arbitration with bit errors, error frames feeding the
> inhibitor's own error-rate trip, and a log in which no `0x7F4` frame could be
> attributed to a device. Caught in review on 2026-09-25, before any hardware
> run.
>
> **The check that missed it was run against the wrong artifact.** It asked
> `tools/interposer/bus.py` — the *virtual bench's* physics link — when the
> authoritative list is the interposer's diag DBC and schema doc (now
> `charge-interposer/diag/` on `charge-interposer/main`). The allocation agreement
> is stated further down **this file**. Checking a real artifact that does not
> answer the question produces exactly the same confident wording as checking
> the right one.

**The would-transmit count is its own field, not `tx_ok` reinterpreted.** Spec
10 allowed either. A separate field is chosen because a decoder that ignores
`diag_mode` would otherwise read a PASSIVE dry run as a drive in which the
device transmitted thousands of times. The two counters are mutually exclusive
by mode, and that is a property worth being able to **check** rather than one
every reader has to remember to apply.

### 0x7F9 — WICAN_Diag_Skips  *(schema 5)*
| Byte | Field | Meaning |
|---|---|---|
| B0-B3 | diag_skips_withdrawn | inhibit frames withdrawn at their deadline, never on the wire (u32) |
| B4-B7 | diag_skips_late | inhibit frames that went out late, or may have (u32) |

Both are **since arming** and reset to 0 on a re-arm, so differencing across an
arm boundary can step negative.

**Why skips get a CAN page at all** (spec 5.2 item 5, section 10; user
2026-09-27, confirmed 2026-09-29). A skipped inhibit means the generator inverter
acted on the VCM's real torque command for one cycle. Before schema 5 the only
trace of that in a CAN log was `diag_abort_reason` 15 — the trip that *three*
skips within one second causes. An isolated skip left no trace whatever, and
nobody is connected to the HTTP status page during a drive.

**Why the two kinds are never summed.** `withdrawn` means the frame was removed
before it reached the wire, so the inverter certainly never saw ours. `late`
means it went out after the VCM's next command, *or may have* — the device cannot
tell those apart, because the TWAI driver reports an aborted frame and a real
transmission identically, as TX_SUCCESS with `msgs_to_tx` falling to 0 (169 of
169 measured). A reader deciding whether our frame reached the bus needs the
split; a total would destroy exactly that.

**Why u32 and not u16 saturating.** A completely stuck transmit path skips every
command, which is ~360,000 in an hour at 100 Hz. Saturating fields would report
the worst case and the tenth-worst case identically. Two u32 fields fill the page
exactly, so nothing is reserved and nothing is wasted.

**The rest of the scheduler's counters are deliberately NOT here** (spec section
11, user 2026-09-29): the per-class telemetry counts, item 6's `carried_in` and
`held`, the on-time verified/unverified counts and the abort-loop figures are on
the HTTP status page only. They check the scheduler on the bench; on the truck,
lost or delayed telemetry already shows in a CAN log as a gap in these very
pages. This replaces the 2026-09-27 wording, which put all of them on one page.

### 0x7F3 — WICAN_Diag_Build
| Byte | Field | Meaning |
|---|---|---|
| B0-B3 | diag_build_id | the first four bytes of the running image's image hash: the ELF SHA-256 ESP-IDF embeds in the app descriptor (`esp_app_get_description()->app_elf_sha256`, shared/tools R1), read at run time. **Names the binary.** Packed as the charge interposer's `0x7F7` build id: a u32 with hash byte 0 most significant, sent little-endian, so the raw bytes B0..B3 are hash bytes 3, 2, 1, 0, and the DBC's unsigned value printed as `%08x` reads as the hash's first four bytes in order (schema 8, spec 10; user, 2026-10-19 and 2026-10-25). Through schema 7 these bytes were `diag_git_hash`, an FNV-1a hash of the build's name |
| B4-B5 | diag_fw_ver16 | the same behaviour/capability revision as `diag_fw_ver`, as u16 |
| B6 | diag_schema_ver_build | schema version (echoed for a build-only decoder) |
| B7 | — | reserved |

## Versions

`diag_schema_ver` = **8**. Bump on any layout change, and update the DBC and this
doc together. The DBC carries the shared decoder's identity attributes
(`shared/bench`, `vtrux_bench.decode`): `DiagSchemaVersion`, the schema byte its
frames carry, and `DiagSchemaHash`, its own layout hash (`00401e953c405902` at
schema 8, `7af6adef1d948197` at schema 7); a bump changes the version, and the
hash whenever a name, position or value table changes. The schema byte sits at `0x7F1` B0 and `0x7F3`
B6. **A change of meaning counts too**, even when no field moves --
see version 6.

**Version 2** (2026-09-19) added two `diag_flags` bits and changed no field
positions, so a version-1 decoder reads every other field correctly and simply
does not know about the new bits.

- **bit4 `shutdown_suppressed`** -- the VCM is commanding generator shutdown
  (`0x051` B0 = `0x10`) and the inhibitor is holding its peace. **Live and
  non-latching**, which is why it is a flag rather than a `diag_disable_reason`
  code: spec 6.3 and 6.4. A reason code that appeared and vanished as the truck
  parked and woke could not be told apart from a past one when reading a log.
- **bit5 `inhibit_live`** -- the spec 7 arm interlocks are satisfied and the
  inhibitor is transmitting. Deliberately distinct from `diag_mode` = 3, which
  is only what was *requested*: the two differ for the whole time the gate is
  waiting on the bus, and on a device that may auto-arm at truck-wake with
  nobody watching, that gap is the interesting thing to see in a log.

**Version 3** (2026-09-20) added two more `diag_flags` bits and again changed no
field positions, so a version-2 decoder reads everything else correctly. Both
come from **spec section 7.1**, the key-state rules.

- **bit6 `key_on`** -- `0x592` B0 bit 4 reads on AND the frame is fresh. This
  is the term that gates transmission, so with bit5 set and this clear the
  device is **armed but silent**, which is the normal state of a parked truck
  whose VCM is still talking. It reads 0 both for a real key-off and for a bus
  carrying no `0x592` at all; `bit1`/`bit2` tell those apart.
- **bit7 `abort_latched`** -- a section 7 abort has stood the device down.
  Before rev 3 an abort set the mode to OFF, which stopped the diag heartbeat,
  so a latched device and a switched-off device looked identical in a log --
  both silent. The device now stays armed, keeps transmitting diag, and says
  which state it is in. `diag_mode` therefore no longer drops to 0 on an abort;
  read this bit instead.


`diag_fw_ver` is a **behaviour/capability revision**, not a build identity;
`0x7F3`'s `diag_build_id` names the binary (spec 10).
- **When to bump:** bump `DIAG_FW_VERSION` in `gen_inhibit.c`, and add a row
  here, in either case:
  - correctly parsed values must be **interpreted** differently (rev 2: the
    same `diag_flags` bits meant a live inhibit on rev 1 and not on rev 2);
  - the firmware gains or changes a behaviour someone would gate on
    (`wican_diag_check.py` gates on `fw_ver >= 3` for the spec 7.1 key
    rules).
- **Not this field's job:** where the bytes are, and which pages exist,
  belong to `diag_schema_ver`. So **`diag_fw_ver` does not predict page
  presence**; read `diag_schema_ver` for that.
- **The name is historical, and stays.** The hand-kept number retires in
  stage (b) of the shared-code port, in the one diag layout both projects
  share, so it is not renamed first (user, 2026-10-25: no churn for a field
  that is going). This withdraws the standing instruction of 2026-10-12 to
  rename it at the next schema bump after 7.

The table:

**Version 8** (2026-10-25, user; port step 0c) changes what **`0x7F3` B0-B3**
mean and moves no field. They are now `diag_build_id`, the first four bytes of
the running image's image hash, packed as the interposer's `0x7F7` build id
(see the page above). Through schema 7 they were `diag_git_hash`, an FNV-1a
hash of the build's name, which named the source and not the binary. A
schema-7 decoder reads every other field correctly but would read the build id
as a name hash, so the field takes a new name and no `RenamedFrom`: the meaning
changed, not only the name. `diag_fw_ver` stays 7: the schema bump guards the
decoder, and the hand-kept number retires in stage (b) (user, 2026-10-25).

**Version 7** (2026-10-10, user) adds `diag_arm_block` value **15**, "receive queue not drained" (spec 7 gate condition 9), and moves no field. A schema-6 decoder reads every field correctly but has no name for 15. It also changes what **`diag_would_tx`** means: it resets on every arm, as `diag_tx_ok` does, where through schema 6 it counted since boot across every arm (spec 3.1, user, 2026-10-12). A reader holding the old meaning would difference a per-arm count. The bump follows this doc's rule that an appended value bumps the schema.

**Version 6** (2026-10-07) changes what **bit1 `bus_on`** means and moves no
field. It now reports the controller's own state: set only while
`twai_get_status_info()` reports `TWAI_STATE_RUNNING`, clear when the controller
is stopped, bus-off or recovering, and clear when the driver is not installed.
Through schema 5 it reported only that the TWAI driver was installed. The
status page's `bus_on` field changed with it (spec 11), and "driver installed"
stays readable there as `drv` being non-null.

On the wire the change is not visible in practice: a controller that is not
running transmits nothing, so a bus-off shows in a CAN log as a gap in the diag
pages under either meaning (spec 10). The difference shows on the status page,
where on 2026-10-06 `bus_on` read true through 42.1 s of bus-off. The schema
bump exists so that a decoder does not attach the new meaning to a schema-5 log.

**Version 5** (2026-09-29, user) adds a fifth page, `0x7F9` SKIPS. No field
moved, so a schema-4 decoder reads `0x7F1`-`0x7F3` and `0x7F8` exactly as before
and simply does not see the new page. It did not change cadence: the period
moved to 240 ms with the fifth page (spec 10), so each page still repeats every
1.2 s. (Corrected 2026-10-04: this said five pages at 300 ms and 1.5 s.)

**Version 4** (2026-09-25, review B1) adds the **PASSIVE** mode and a fourth
page, `0x7F8`. No field moved again, so a version-3 decoder reads
`0x7F1`-`0x7F3` exactly as before and does not see the new page.

- **`diag_mode` gains 4 = PASSIVE** (spec 3.1): the complete INHIBIT decision
  path with no `0x051` on the wire. A log of a PASSIVE drive is meant to read
  as a dry run of an INHIBIT drive, which is why the page below exists.
- **`diag_abort_reason`** -- `diag_flags` bit7 says *that* a section 7 trip
  latched; with the trips now numbering eight, a log has to say *which*, and a
  dry run that reports only "something tripped" is not a dry run of anything.
- **`diag_arm_block`** -- and symmetrically, why the inhibit is *not* live,
  which is the other half of reading a dry run.
- **`diag_would_tx`** -- see the note on the page above for why it is its own
  field rather than `tx_ok` reinterpreted by mode.
- **`diag_flags2`** -- the spec 6.2 SoC-valid marker, so a log shows when SoC
  started counting; `tx_pending`, so a frame outstanding at the moment of
  sampling is visible; and `emit_refused`, which must always read 0 and is a
  bug in the firmware if it does not.
- **`diag_tx_queued_behind`** -- spec 5's hazard, counted: inhibit frames that
  went into the controller behind a frame that had not finished.

### `diag_abort_reason` values

| value | meaning |
|---|---|
| 0 | none |
| 1 | transmit failed (controller reported it) |
| 2 | `0x617` B7 = `0xCA`, VCM fault active |
| 3 | bus lost -- no `0x051` |
| 4 | `0x471` stopped while `0x051` still live -- inverter lost |
| 5 | engine turning while armed |
| 6 | error-frame rate exceeded |
| 7 | `0x411` went stale while live |
| 8 | `0x440` went stale while live |
| 9 | `0x617` went stale while live |
| 10 | `0x639` went stale while live |
| 11 | `0x054` stopped while live |
| 12 | `0x051` arrived with DLC < 6 |
| 13 | inhibit frame could not be queued |
| 14 | **RETIRED 2026-09-27** -- inhibit frame still unsent when the VCM's next `0x051` arrived. Nothing sets it any more (spec 7 trip 7, amended: a late or withdrawn frame is a counted skip). Kept because captures from before that date carry it. |
| 15 | 3 inhibit frames skipped within 1 s |

**These are `gi_abort_t` in `gen_inhibit_core.h`, in order.** Appending is safe;
reordering or inserting silently changes what every recorded log means, so add
to the end and bump the schema.

### `diag_arm_block` values

| value | meaning |
|---|---|
| 0 | none -- the gate is satisfied |
| 1 | not armed |
| 2 | gate not yet evaluated |
| 3 | no fresh `0x051` |
| 4 | VCM fault active |
| 5 | generator running |
| 6 | VCM requesting engine |
| 7 | VCM commanding torque |
| 8 | VCM commanding `0x10` |
| 9 | SoC not yet valid (contactors) |
| 10 | main contactors not closed |
| 11 | no fresh `0x617` |
| 12 | no fresh `0x639` |
| 13 | latched disable (section 6) |
| 14 | latched abort (section 7) |
| 15 | receive queue not drained (spec 7 condition 9; added 2026-10-10, user) |

`gi_block_t` in `gen_inhibit_core.h`, same rule: append only.

| diag_fw_ver | git rev | notes |
|---|---|---|
| 1 | `gen-inhibit/rollback-fix` @ first diag build | rollback gate + OTA fix + inhibitor + strips + disable conditions + this diag frame |
| 2 | `gen-inhibit/rollback-fix` @ `dc2682d` or later | decision logic split into the host-testable pure core (`gen_inhibit_core.c`, behaviour-neutral), **and** a latched section 6 release now clears `inhibit_live`. On the wire that is `diag_flags` byte3 `0x27` -> `0x07`: rev 1 reports a live inhibit on a device that is latched off. First flashed to the WiCAN 2026-09-20 as `834af07`. |
| 3 | `gen-inhibit/rollback-fix` @ the spec 7.1 build | key-state rules. Transmission of the real `0x051` is gated on `0x592` B0 bit4 reading on and fresh; a key-off -> key-on transition clears every latch and re-enters the arm gate; a section 7 abort latches in place instead of setting mode OFF. On the wire: `diag_schema_ver` `2` -> `3`, `diag_flags` gains bits 6 and 7, and `diag_mode` **stays 3 through an abort** where rev 2 dropped it to 0 and went silent. |
| 4 | `gen-inhibit/rollback-fix` @ the review B1 build | **PASSIVE** (`diag_mode` 4) and the `0x7F8` page. Also, from the commits between 3 and 4: the spec 6.2 SoC-valid marker gates going live on the main contactors (A1), a stale interlock signal while live is a latched abort subject to the evidence rule (B3), a short `0x051` aborts (C2b), and a transmit counts only once the controller reports it completed (C1). On the wire: `diag_schema_ver` `3` -> `4`, `diag_mode` may read 4, and `0x7F8` appears. (The page shipped briefly as `0x7F4` in development and was moved before any hardware run -- see the allocation table.) |
| 5 | `gen-inhibit/rollback-fix` @ the commit after `6771edf` that bumps `DIAG_FW_VERSION` (`6771edf` itself already has the new meaning but still reports 4, so for that one commit only `diag_schema_ver` 6 identifies it) | **`diag_flags` bit1 `bus_on` now means the CONTROLLER is running** (spec 11, changed 2026-10-06, user), not that the TWAI driver is installed. False when the controller is stopped, bus-off or recovering. The status page's `bus_on` changed with it; "driver installed" stays readable there as `drv` being non-null. **No field moves, which is why this rev exists:** a rev 4 log and a rev 5 log are byte-identical in layout, and a reader holding the old meaning would read the bit correctly and interpret it wrongly. On the wire the bit reads 1 under both meanings in practice, since a controller that is not running sends no pages. The difference shows on the status page, where on 2026-10-06 the old meaning read true through 42.1 s of bus-off. Paired with `diag_schema_ver` `5` -> `6`: the schema version guards a decoder, this guards the human reading the rev table. |
| 6 | `gen-inhibit/rollback-fix` @ the commit that adds spec 7's recency rule to trip 6 and bumps `DIAG_FW_VERSION` (`477171a` has the sliding window without the recency rule and still reports 5) | **When abort reason 6 (error-frame rate) fires changed** (spec 7, "How the window moves", user, 2026-10-08). Through rev 5 the window was fixed 10 s blocks that re-based their count, so 10 error frames spread over ~9 s latched from only about 1 start phase in 10. From rev 6 the window slides and is inclusive at 10.0 s. The history is recorded whenever the device is armed and cleared only by a key-on, and a burst trips only while its newest error frame is within 10 s of the live check. No field moves and reason 6 still means "error-frame rate exceeded", but two logs of the same bus can now differ on whether and when it fires. `diag_schema_ver` stays `6`. |
| 7 | `gen-inhibit/rollback-fix` @ the commit that implements the 2026-10-10 go-live batch and bumps `DIAG_FW_VERSION` | spec 7 gate condition 9 (the receive queue is drained before going live; `diag_arm_block` 15 while it is not); trip 6 checked before going live, so a recent burst latches with no inhibit frame sent; spec 3 wake on a mode change; `diag_would_tx` resets on every arm (spec 3.1, user, 2026-10-12). On the wire: `diag_schema_ver` `6` -> `7`, and `diag_arm_block` may read 15. `099e9da` reports fw_ver 7 and schema 7 without the `would_tx` reset; it was never flashed, so no capture carries that combination. |

Unchanged at 4 across `4a0a796` (the `!aborting` guard): no diag field's
meaning changed. The counters the fix affects are on the status page only (spec
section 11).

`diag_build_id` names the binary (spec 10). Look it up in the release build
records and the bench run records, which name each image by its full hash: a
build id that matches no record is unknown, and one that matches two is
ambiguous, never guessed. `diag_fw_ver` is the human-readable handle; for a
build id with no record, decode `diag_fw_ver` and cross-check the rev table.
On a schema 7 or older log, B0-B3 are `diag_git_hash`, the hash of the build's
name.

## `0x7F0`: the RESPOND timing probe

*Published 2026-10-12 (user).* Until then the layout existed only in the code
(`build_probe()` in `gen_inhibit_core.c`). It is now a published interface: a
change to it is a change to this file and the DBC first. It is not a diag frame
and carries no schema version, so publishing it bumped nothing.

In RESPOND (spec 3) the device sends one `0x7F0` for each `0x051` it receives,
due `offset_us` after that `0x051`. DLC 8, little-endian:

| Bytes | Signal | Content |
|---|---|---|
| B0-B3 | `probe_rx_time_us` | when the device received the `0x051` it answers: its own microsecond clock (`esp_timer`), low 32 bits, so it wraps about every 71.6 min |
| B4-B5 | `probe_offset_us` | the response delay setting, us |
| B6 | `probe_vcm_ctr` | the rolling counter (0-15, `0x051` B5 low nibble) of the most recent `0x051` received with DLC 6 or more, which is the one it answers. Upper nibble 0 |
| B7 | `probe_marker` | `0x5A`, constant |

**What B6 is for.** It ties each probe to the command it answers, so a witness
can tell a late probe without assuming that a probe answers the `0x051` just
before it on the wire. A probe is late when B6 is older than the newest
`0x051` counter on the wire when the probe appears.

## ID allocation on this truck

Both this component and the charge interposer invent self-telemetry IDs, both
transmit on the powertrain bus, and both may be fitted at the same time. The
ranges are therefore disjoint **by agreement**, and the agreement is only worth
anything if the next page on either side is taken from this table.

| range | owner | source of truth |
|---|---|---|
| `0x7F0` | WiCAN RESPOND timing probe (not a diag frame) | layout: this file, "`0x7F0`: the RESPOND timing probe"; behaviour: spec 3 |
| `0x7F1`-`0x7F3` | WiCAN diag STATUS / COUNTERS / BUILD | this file |
| `0x7F4`-`0x7F7` | **charge interposer** diag STATUS / OBSERVED / COUNTERS / BUILD | `charge-interposer/diag/interposer_diag_schema.md` on `charge-interposer/main` |
| `0x7F8` | WiCAN diag STATUS2 (schema 4) | this file |
| `0x7F9` | WiCAN diag SKIPS (schema 5) | this file |
| `0x7FA`-`0x7FF` | unallocated | take the next page from here |

`0x7DF` and `0x7E0`-`0x7EF` are the OBD request/response range and are not
available -- and that is measured, not assumed: `0x7DF` carries 88,385 frames in
40 captures, `0x7E0` 462,993 in 300 and `0x7E8` 667,187 in 316.

**Nothing transmits in `0x7F0`-`0x7FF`, and as of 2026-09-25 that is a
measurement rather than a habit.** `diag_id_allocation.py --corpus` over
**2,204 captures** finds **zero frames** anywhere in `0x7F0`-`0x7FF`, against 58
distinct IDs seen elsewhere in `0x700`-`0x7FF`. Output in reverse-it's
`diag_id_allocation.txt`.

That check was prompted by the interposer session, which went looking for the
evidence behind the claim and found none: *"nothing on the truck consumes the
`0x7Fx` range"* appeared **six times** across the two components' specs, schema
docs and firmware logs, with no measurement behind any instance. It had become
established by repetition. It happens to be true, but it was not known to be,
and it is the assumption that makes it safe for either device to transmit here
at all.

"Nothing on the truck" is still not the same as "nothing on the bus": the two
aftermarket devices are the whole hazard, and that is what the table above is
for.

**Check before allocating, do not remember.**
`diag_id_allocation.py` (reverse-it's `projects/vtrux/notes/artifacts/gen-inhibit/`) reports every ID in `0x700`-`0x7FF`
declared by any Vtrux DBC, flags any declared twice, and with `--corpus` reports
what the captures actually carry. `diag_decode_check.py` asserts the two DBCs
are disjoint on every run, so this cannot be repeated silently.

## `0x7F0` and `0x7F1` mean something else on the interposer bench

Recorded 2026-09-22, from the interposer session. Two of the four IDs this
component puts on the desk segment are also used by the charge interposer's
**virtual bench physics link** (`bus.py`, then at reverse-it's `projects/vtrux/tools/interposer/bus.py`):

| id | this component | `bus.py` |
|---|---|---|
| `0x7F0` | gen_inhibit timing probe (`GI_PROBE_ID`) | `PHYS_CURRENT_ID`, charger -> pack delivered DC current |
| `0x7F1` | `WICAN_Diag_Status` | `PHYS_VOLTAGE_ID`, pack -> charger terminal voltage |

**There is no frame collision.** The physics link is UDP multicast on its own
segment (port 43215, `hop_limit=0`) and never reaches a real wire, and these
ids are absent from the truck. The hazard is entirely in **interpretation**:
a tool that scans a dongle for `0x7F0` or `0x7F1` and concludes "a virtual
bench is leaking onto hardware" will be wrong on this desk almost every time,
because both are this device working normally -- `wican_timing_bench.py` alone
puts 2000 `0x7F0` frames on the wire per RESPOND arm.

The "diagnostic ranges are disjoint" agreement between the two components
covers `0x7F1`-`0x7F3` and `0x7F8`-`0x7F9` against the interposer's
`0x7F4`-`0x7F7`
(see the allocation table above). It says nothing about `bus.py`'s physics ids,
which predate it and overlap both of ours. If the virtual bench and a real
segment ever run together, these two are the ids to watch.

**Two different questions, and confusing them cost a shipped ID clash.**
`bus.py` answers "what does the interposer's *simulation* put on a virtual
segment"; the DBC and the interposer schema doc answer "what does the
interposer *board* put on the truck's bus". The second is the one that matters
for allocation.

## Note on OBSERVE

**Changed 2026-09-19.** `gen_inhibit` now forces `TWAI_MODE_LISTEN_ONLY` for
OBSERVE itself, rather than leaving it to the `can_mode` configuration (spec 3
and spec 13 item 1: OBSERVE must perturb nothing, and in normal mode the
controller ACKs every frame and can emit error frames).

The consequence for this schema is that **no diag frames go out in OBSERVE**:
`twai_transmit` fails in listen-only, so 0x7F1-0x7F3 are absent for the whole
run. That is the price of a genuinely passive tap and it is intended — an
OBSERVE capture is expected to show nothing from the WiCAN at all, and a
capture that *does* contain 0x7F1-0x7F3 during OBSERVE means the controller is
not listen-only and the tap was not passive.

RESPOND and INHIBIT are unaffected and still transmit all three frames.
