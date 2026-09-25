# gen_inhibit host harness

Runs `main/gen_inhibit_core.c` — the same file the ESP32 runs — on a PC, with
no hardware, and diffs its behaviour against recorded goldens.

Written 2026-09-19, when the decision logic was split out of `gen_inhibit.c`
into a pure core plus a driver shim.

## Read this before trusting a green run

**This is a regression harness, not a differential one.** It compiles the same
core the target runs, so green means *behaviour has not changed since the
goldens were blessed*. It cannot catch a rule that is wrong here and wrong in
the golden too.

That trade was made deliberately with the user on 2026-09-19. The alternative
— maintaining an independent Python reference and diffing two implementations,
the way `projects/vtrux/tools/interposer/firmware/test/host_diff/` does with
`machine.py` against `machine.cpp` — was considered and declined. We have no
1:1 reference for this logic: `tools/gen_inhibit/inhibit.py` is proactive and
scheduled where this is a reactive trail, so it is related, not an oracle.

Why it is still worth having: every bug found in this component so far has
been a **state-sequence** bug, reachable only from a particular order of
received frames and invisible to a compiler. That is exactly what a replay
catches. Three were found by hand on 2026-09-19; two more were found by this
harness on its first run (see *Findings*).

**`--bless` is not a way to make a failing test pass.** Read the diff, decide
whether the change is intended, and check it against what the scenario's own
header says it is meant to demonstrate. A wrong rule blessed into a golden
stays green forever.

**A green run does not clear a spec §13 item.** Those clear on bench proof.

## Layout

| File | What it is |
|---|---|
| `host_runner.c` | Emulates the worker loop, replays a scenario, prints a deterministic trace |
| `extract_probe.c` | Exposes the core's four hand-rolled extractors for the cantools cross-check |
| `Makefile` | `make` builds both; `make asan` rebuilds under ASan/UBSan |
| `make_scenarios.py` | Generates the synthetic scenarios into `scenarios/` |
| `from_capture.py` | Turns a real capture into a scenario; `--scan` finds key-on points |
| `run_tests.py` | Builds, replays every scenario, diffs against `golden/` |
| `test_signals.py` | 2000 randomised frames per signal, C extractors vs cantools |
| `scenarios/` | Generated inputs. Not hand-edited |
| `golden/` | Blessed traces |

## Running it

```sh
make
python3 make_scenarios.py      # writes scenarios/
python3 run_tests.py           # build, replay, diff
python3 run_tests.py --only keyon
python3 run_tests.py --bless   # after reading the diff
python3 test_signals.py        # needs cantools and the project repo
```

`test_signals.py` and `from_capture.py` reach into the **project** repo
(`reverse-it`), which is a separate tree — for the DBCs and for `canre`'s
parsers. Point them at it with `--repo` or `$GEN_INHIBIT_REPO`; the default is
the `madhouse-debian` layout, `~/Seafile/NotGit/reverse-it`.

Prefer running all of this on `madhouse-debian`: the log corpus is on local
disk there, so no SeaDrive hydration is involved, and it keeps the load off
whichever machine is driving the bench.

## What is emulated, and what is therefore untested

`host_runner` reproduces the worker loop closely enough for frame ordering and
for the interaction between the periodic tick and frame arrival — which is
where the bugs live. It does **not** emulate:

- the TWAI driver, `can_enable()`/`can_disable()`, or the listen-only forcing
  for OBSERVE. Those are in the shim and are hardware-only;
- **latency**. Every transmit is instantaneous, so the response histogram is
  all zeros. Timing is what the bench ESP-to-ESP test measures; a host replay
  cannot speak to it;
- preemption, and the races between the worker task and the HTTP handlers.

## Findings

Both were produced by this harness on its first run. Both are **pre-refactor
behaviour, faithfully preserved** — neither is damage from the core split —
and both are recorded as goldens rather than fixed, because the spec is the
source of truth and a behaviour change belongs there first.

**1. The arm gate's "generator already running" check is arrival-order
dependent.** `arm-gate-order-rpm-first` and `arm-gate-order-cmd-first` differ
by one microsecond in whether 0x051 or 0x054 lands first. Rpm-first blocks
correctly and never transmits. Cmd-first finds `have_rpm` still false, skips
the check, goes live, and transmits ~16 zero-torque frames at a generator
turning 800 rpm before the 0.3 s runtime debounce ends the run. The check
exists because "taking over a loaded generator and commanding zero sheds the
engine's whole load in one frame".

**Severity is lower than that reads, measured after the fact.** In 21,925 real
frames with the generator at >=300 rpm, `gen_rpm_ref` was never the engine-off
null and torque was never zero — so the gate's two `0x051`-based checks, which
can never be skipped, already cover the loaded case. The synthesised
combination occurs 8 times in 39,718 co-observed frames across 150 logs, over
a 73 ms engine coast-down where the VCM has already commanded zero, and going
live there is correct. Real gap, no observed hazard.

**2. A latched section 6 disable freezes `inhibit_live` at true.**
`disable-freezes-live-flag`. Transmission stops correctly, but the interlock
block is guarded by `mode == INHIBIT && !disabled`, so once disabled nothing
clears the flag and the worker never reaches its OFF branch. `diag_flags` bit5
and the JSON then claim a live inhibit on a device that is latched off. Same
defect class as the disarm bug fixed by hand earlier the same day.

## Regenerating the real-capture replays

`scenarios/` is gitignored — the replays are 25k–127k lines each. The goldens
are committed (summary only; TX lines are filtered, since `FINAL` already
carries `tx_ok`/`tx_fail`/`ctr_ok`/`ctr_bad`). To rebuild the inputs:

All five captures are copied into the project repo so this suite has one
stable source instead of reaching into the Android auto-capture store. They
are described in `projects/vtrux/logs/README.md` there. The copy is not
ceremony: the original `replay-T20-drive` fixture broke precisely because its
source moved out from under the suite.

```sh
L=~/Seafile/NotGit/reverse-it/projects/vtrux/logs
python3 from_capture.py $L/vtrux_20260719_190019_T4.log --channel 2 --at 0 --for 220 \
        --out scenarios/replay-genrun-stop.scn
python3 from_capture.py $L/vtrux_20260323_220148_T0.log --channel 1 --at 0 --for 300 \
        --out scenarios/replay-healthy-engine-off.scn
python3 from_capture.py $L/vtrux_20260322_165622_T1.log --channel 1 --at 0 --for 64 \
        --out scenarios/replay-mmode-genstart.scn
python3 from_capture.py $L/vtrux_20260714_112312_T2.log --channel 2 --at 0 --for 225 \
        --out scenarios/replay-shutdown-at-keyon.scn
python3 from_capture.py $L/vtrux_20260802_123318_T0.log --channel 2 --at 0 --for 161 \
        --out scenarios/replay-bus-sleeps.scn
python3 from_capture.py $L/vtrux_20260403_194203_T2.log --channel 2 --at 0 --for 176 \
        --out scenarios/replay-rekey-short.scn
python3 from_capture.py $L/vtrux_20260513_174225_T4.log --channel 57 --at 0 --for 224 \
        --out scenarios/replay-rekey-long.scn
python3 from_capture.py $L/vtrux_20260714_112312_T2.log --channel 2 --at 20.5 --for 7.7 \
        --out scenarios/replay-inverter-lost-keyon.scn
```

**`--channel` is not optional, and every window above is one epoch.** Both
were added 2026-09-20 with spec 7.1, and two of the five original commands
were wrong:

- `replay-bus-sleeps` ran `--for 297` over a capture with **three** epochs and
  a channel shuffle — the powertrain bus moves from channel 2 to channel 7
  mid-file — so it spliced separate recording windows into one stream.
  Windowing it to epoch 1 changes no decision (it aborts at 11.67 s either
  way, long before the first epoch break), but the old fixture was built the
  exact way the section above warns against.
- all five took every channel, which on a merged capture means taking frames
  the device could never have seen on one wire.

Channel numbers come from `bus_epochs.py` and are **per capture** — note that
`replay-rekey-long`'s powertrain bus is channel **57**. Never carry a channel
number from one capture to another.

`--scan` finds key-on candidates in a capture you want to add. Two
corpus-wide selectors live in
`projects/vtrux/notes/artifacts/gen-inhibit/`: `shutdown_and_wake_scan.py`
found `replay-shutdown-at-keyon` and `replay-bus-sleeps`, and
`replay_candidate_scan.py` found `replay-genrun-stop`.

**Check a candidate's provenance before you adopt it.** A capture recorded
with test equipment inline on the bus looks exactly like an ordinary drive,
and one such capture was in this suite for a day before it was caught. The
known rig dates are listed in the repo's `data-sources.md`.

**Check its epoch structure too, and never feed a whole multi-epoch file to
`from_capture.py`.** An Android `.log` can hold several *disjoint* recording
windows: the logger drops, reconnects, and the same physical wire comes back
under a **different channel number**. One capture in the corpus reconnects
seven times in 300 s. Splicing those windows into one stream injects
bus-losses the truck never had and invents inverter silences that were only
the logger being away -- a 255 s "inverter lost" gap was manufactured exactly
this way, and two proposed fixtures were sourced from it before it was caught.

Run `projects/vtrux/notes/artifacts/gen-inhibit/bus_epochs.py <capture>`
first. It prints each epoch, names each channel's physical bus from anchor
content, and flags both channel shuffles and channels carrying more than one
bus. Then set `--at`/`--for` to sit **inside a single epoch**. Worked example:
the planned `replay-rekey-short` uses `vtrux_20260403_194203_T2` epoch 1 only
(0-176.44 s), because that file has a 7.7 s total-bus dropout at
176.44-184.22 s which would otherwise replay as a bus-loss.

## The key train, and why every scenario has one (spec 7.1)

Since 2026-09-20 the core will not transmit unless `0x592` B0 bit 4 reads on
and is fresh, and **never-seen reads off**. A scenario with no `0x592` in it
therefore transmits nothing — it stops testing what it was written for and
starts testing the key gate.

So `make_scenarios.py` injects a 10 Hz key-ON train spanning each scenario's
traffic, unless the scenario passes `autokey=False` and builds its own. The
generated `.scn` says which it got, in a `# KEY:` header line. Two synthetics
opt out because the key must go away *with* the bus: `bus-loss-latches` and
`bus-glitch-short`. A key train that outlives the VCM's own `0x051` is a bus
the truck cannot produce — `0x592` comes from the VCM, and the VCM is the
thing that just went away.

## The contactor train, and why every scenario has one too (review A1)

The same trap, one rule later. Since 2026-09-24 the core will not go live until
`0x440 bcm_mainc_stat` has read 11 `MAIN_PN_CLOSED_DRIVE` or 12
`MAIN_P_CLOSED_CHARGE` **and** an `0x411` has arrived since (spec 6.2's
SoC-valid marker, spec 7 condition 2). A scenario with no `0x440` therefore
transmits nothing.

So `make_scenarios.py` also injects a 20 Hz `0x440` closed train spanning each
scenario's traffic, plus a 20 Hz healthy `0x411` train, unless the scenario
passes `autobms=False`. The generated `.scn` records it in a `# BMS:` header
line. `bus-loss-latches` and `bus-glitch-short` opt out for the same reason they
opt out of the key train — a pulled connector takes the BMS with it — and the
four `soc-*` scenarios opt out because the marker is the thing under test.

**The `0x411` train is withheld from any scenario that sends `0x411` itself**,
and that is not a nicety: `low-soc-debounce` counts *consecutive* sub-threshold
readings, so a competing healthy train at 20 Hz would reset the count every
50 ms and the release could never latch. A scenario that owns `0x411` is then
responsible for keeping it fresh for as long as it wants the marker to hold,
which its trace shows.

Two things worth knowing about the injection's side effects:

- **Every scenario now shows one extra gate line** — `block=SoC not yet valid
  (contactors)` for the loop iteration before going live, because `0x051`
  arrives before `0x440`/`0x411`. 18 of the 39 pre-A1 goldens changed by exactly
  that and nothing else.
- **The added frames re-time the tick grid**, since `host_runner` ticks once per
  loop iteration. That is what surfaced `arm-gate-generator-running`'s wrong
  golden (below) and is the same hazard `_frame_span()` documents.

The four `soc-*` scenarios are review A1's assertions:

| scenario | what it pins |
|---|---|
| `soc-wake-transient-ignored` | a BMS wake reading of 18.77 % against a real 24.41 % latches **nothing**, because the contactors have not closed |
| `soc-low-after-close` | the same readings *after* the close **do** latch — the release is still reachable |
| `soc-never-closed-never-live` | contactors never closed: never live, `tx_ok=0`, no abort. "Not ready", not a fault |
| `soc-marker-clears-on-bms-sleep` | the staleness half, and that the marker clearing does **not** end a live inhibit (that is trip 5) |

### `arm-gate-generator-running` had the wrong behaviour blessed into it

Found 2026-09-24, by A1 rather than by anyone looking. The scenario's header
says *"blocked on generator running — a block, not an abort"*; its golden showed
it going **live** and then aborting on engine-turning, with 16 transmits. The
gate's `0x054` check is skipped when `0x051` arrives first (finding 1,
`arm-gate-order-cmd-first`), and at a shared timestamp that is what happened.
A1's extra gate condition delays the gate past `0x054`'s arrival, so the
intended behaviour now appears.

**The fragility is unchanged** — A1 moved this scenario to the other side of it,
it did not fix it. And the lesson is the one `run_tests.py`'s own header states:
a wrong rule blessed into a golden stays green forever. This is the second
instance in two days, and it is the argument for the golden-independent
invariant checker (review D9/E2): **a scenario's header and its golden
disagreed, and only a rule change unrelated to either of them noticed.**

The six `key-*` scenarios are the spec 7.1 assertions themselves:

| scenario | what it pins |
|---|---|
| `key-arms-while-off` | arming is **not** gated — `live=1` with `tx_ok=0` |
| `key-never-seen` | no `0x592` at all: arms, transmits nothing (fail-closed) |
| `key-gates-tx` | transmission stops and resumes on the key, no abort, no latch clear |
| `key-on-clears-abort` | the end-of-drive story: latch, key-on, `fb_ever` reset, live again |
| `key-on-clears-disable` | a key-on clears the **section 6** latch too |
| `key-stale-not-keyoff` | a stale key gates transmission but clears nothing |

## Real-capture replays

Eight captures. The five below were the original set; `replay-rekey-short`,
`replay-rekey-long` and `replay-inverter-lost-keyon` were added for spec 7.1
and are described after them.

**`replay-genrun-stop`** — 220 s, 93,150 frames, SoC 77.2 %, and the only
replay that shows the arm gate both holding and releasing:

| t | event | device |
|---|---|---|
| 0.007 s | generator already turning at 735 rpm | blocked, `generator running` |
| 0.007–92.058 s | generator runs, 1,676 rpm peak | **transmits nothing** |
| **92.058 s** | generator stops | goes live in the same tick |
| 92.058–220.476 s | engine off, SoC 77 % | 12,779 transmits, 0 failures |
| 220.476 s | capture ends | bus-loss abort on the trailing silence |

21,982 counter steals, `ctr_bad=0`. This is the fixture that says the gate is
not one-way: it holds the device off for 92 s of real generator operation and
then arms itself on the real stop, with no synthetic directive anywhere in
the trace but the initial mode set.

**It replaces `replay-T20-drive`, removed 2026-09-20.** That fixture's source
was `vtrux_20260617_193900_T20`, from the session where the `generator_runner`
laptop bridge was inline on the bus; the user ruled those captures out as test
data. Its role was *generator running plus SoC below the 21 % floor*, and
**that combination does not exist anywhere else in the corpus** — all 26
captures with both properties are from the 2026-06-17/18 rig window
(`projects/vtrux/notes/artifacts/gen-inhibit/replay_candidate_scan.py`). So
the replacement keeps the generator-running block against real traffic and
gives up the low-SoC latch, which `low-soc-debounce` already asserts
synthetically. The low-SoC latch is **not exercised against real traffic at
all**, and the one apparent exception was withdrawn on 2026-09-24. When
`replay-rekey-long` was added for spec 7.1 it appeared to restore the cover by
carrying a pack at 18.77 %, latching `low_soc` at 202.47 s. Review A1 showed
that reading was the BMS wake transient -- the pack was really at 24.41 % --
so the fixture was pinning a false release, and under the spec 6.2 SoC-valid
marker it correctly latches nothing. The cover it seemed to give was never
real. `low-soc-debounce` and `soc-low-after-close` assert the latch
synthetically; nothing asserts it on real traffic.

**`replay-healthy-engine-off`** — 300 s, 127,082 frames, engine off at SoC
84.6 %. Live at 7 ms, **held the whole capture**, 29,990 transmits, zero
aborts. This is the one that says the §7 trip set is not hair-triggered
against real traffic.

**`replay-mmode-genstart`** — 64 s, 24,809 frames, and the most complete
end-to-end story in the corpus. The driver engages M mode and the generator
actually starts:

| t | event | device |
|---|---|---|
| 0.006 s | engine off, SoC 43.3 % | goes live |
| 0.006–39.057 s | normal driving, shifter P/R/N/D | 3,905 transmits |
| **39.057 s** | `shift_lever_pos` -> 4 (**M mode**) | latches `m_mode`, `inhibit_live` clears, transmission stops |
| 46.4 s | **generator cranks and runs** | silent |
| 53.2 s | M mode released | latch **holds** |
| 54.4 s | engine coasts down | silent |

**Zero transmits after the latch** — last at 39.054 s, latch at 39.057 s.
This exercises §6.1's entire purpose against a real event rather than a
synthetic one: the driver demanded the generator, the inhibitor stood down,
and the generator started. SoC rises 43.3 % -> 54.9 % across the capture,
which is the generator doing its job with the inhibitor out of the way.

## The three spec 7.1 replays

**`replay-rekey-long`** — `vtrux_20260513_174225_T4`, channel 57, 224 s,
88,551 frames, and the fixture that carries spec 7.1 end to end against real
traffic:

| t | event | device |
|---|---|---|
| 0.004 s | engine off, bus alive | live |
| 0.040 s | first `0x592`, key on | transmitting |
| **167.230 s** | **key off** | transmission stops in the same frame |
| **168.071 s** | `0x471` stops, 0.84 s later | `inverter lost`, **latched** |
| 202.254 s | **key on**, 34.2 s later | `KEY_CLEAR`, `fb_ever` reset, gate re-entered |
| 202.289–203.3 s | `0x411` returns reading **18.77 %** — the BMS wake transient, against a real 24.41 % | **ignored**: `0x440` still walking its close sequence, so the SoC-valid marker is clear |
| **203.459 s** | `bcm_mainc_stat` -> 11 `MAIN_PN_CLOSED_DRIVE` | marker set; first `0x411` after it 12 ms later |
| 203.471 s | — | **live again**, on the real 24.41 % |
| 220.279 s | key off again | transmission stops |
| 220.985 s | `0x471` stops | `inverter lost` latched, the ordinary end of a drive |

18,397 transmits, `ctr_bad=0`. Three things worth naming.

The inverter-lost trip firing 0.84 s after a key-off, on a healthy truck with
nothing wrong, **is** finding 3 — and the key-on clearing it is the fix,
measured rather than argued.

**This capture is the regression test for review A1**, and the rows above are
the change. Until 2026-09-24 it went live the instant the key came back and
latched `low_soc` 217 ms later on the 18.77 % wake reading, losing the rest of
the drive; the golden blessed that. The new behaviour was predicted from the
capture before the rule was written —
`projects/vtrux/notes/artifacts/gen-inhibit/contactor_state_check.py` replays
the marker rule over it — so the golden is not a reading of the
implementation's own output.

**It no longer provides real-traffic cover for the low-SoC latch**, and it
never really did: what it was pinning was a false release. See the withdrawal
above.

**`replay-inverter-lost-keyon`** — `vtrux_20260714_112312_T2`, channel 2,
20.5–28.2 s, 2,673 frames. The **key-ON** inverter loss: the population spec
7.1 says the trip is actually for, and the fixture that would catch a key rule
written so loosely it swallowed the real fault too.

**Since review A1 (2026-09-24) it does not go live, and so does not trip.**
`0x440 bcm_mainc_stat` reads **14 `ALL_OPEN_SHUTDOWN` on all 154 frames** of
this window — and before it — so the spec 6.2 SoC-valid marker is never set and
arm-gate condition 2 never passes. It now transmits nothing (`tx_ok=0`) and
blocks on `SoC not yet valid (contactors)`. That is the rule working: with the
main contactors open the HV inverter cannot crank the engine, so there is
nothing to inhibit. It used to show 41 transmits and then a latched
`inverter lost`.

**The consequence is a real loss of coverage, not a formality.** Checked across
the whole clean corpus rather than inferred from this one window
(`projects/vtrux/notes/artifacts/gen-inhibit/inverter_lost_contactor_sweep.py`,
`keyon_inverter_loss_contactors.py`): all five key-ON events in this capture
have the contactors open throughout, and of the 27 inverter-lost events
`inverter_lost_prevalence.py` found in clean captures, **0 have the contactors
closed inside the gap**. So no clean capture offers a replacement, and **the
key-ON case of section 7 trip 3 now has only synthetic cover**
(`inverter-lost`).

Read that sweep carefully in one respect: contactors open during the gap does
**not** mean the trip is dead. Liveness is not retracted when the contactors
open, so every key-OFF end-of-drive event still fires it — which is exactly
what `replay-rekey-long` shows at 220.985 s. What is gone is the key-ON case.

Why every one of those events has the pack disconnected is **an open question
put to the user on 2026-09-24 and not answered**; nothing here should be read as
a claim about its cause.

**`replay-rekey-short`** — `vtrux_20260403_194203_T2`, channel 2, epoch 1 only
(0–176.44 s), 41,114 frames. Three key cycles, each about 10.3 s down, each
one clearing a latch and re-arming. It is the only fixture that exercises rule
2 repeatedly.

**It is also the suite's only lossy capture, and that has to be read
correctly.** `ctr_bad=4363` against `ctr_ok=6121` — 42 % of the `0x051`
counter steps are not +1, `0x051` arrives at 59.6 Hz instead of 100, and
`0x592` at 5.5 Hz instead of 10. Both IDs are down by the same fraction, so
this is the **logger** dropping frames, not the truck. The visible consequence
is the key gate flapping several times a minute in the trace, from freshness
lapses with no change in the key's value.

Do not read that as truck behaviour. Measured across the fixture captures
(`projects/vtrux/notes/artifacts/gen-inhibit/key_cadence.py`), `0x592` runs at
**9.97–9.99 Hz with a maximum gap of 0.117 s** on every clean epoch — four
times inside the 0.5 s freshness window, with zero gaps over it. The flapping
is this one capture. Keeping it is deliberate: it is the only fixture that
says what the gate does under frame loss, and the answer is that it degrades
by transmitting less, never by transmitting wrongly.

Across the replays the rolling counter stepped exactly +1 on every transition
in seven of the eight goldens; `replay-rekey-short` is the exception, for the
reason above.
