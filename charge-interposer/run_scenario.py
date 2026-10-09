"""Orchestrate the three-way bench: vehicle_sim | interposer_sim | charger_sim.

Starts all three processes on isolated virtual segments, runs a named scenario
to completion, and checks what happened against what should have happened.

    python3.13 projects/vtrux/tools/interposer/run_scenario.py --list
    python3.13 projects/vtrux/tools/interposer/run_scenario.py evap-override
    python3.13 projects/vtrux/tools/interposer/run_scenario.py --all

Each scenario pairs a vehicle/charger configuration with assertions over the
interposer's event log and the vehicle's final pack state. `evap-bypass` is the
control: identical to `evap-override` but with the state machine disabled, so
the pair shows the intervention is what moved the outcome and not the bench.
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

sys.path.insert(0, str(HERE))

# The interposer trips if either side goes quiet for bms_stale_ms/chg_stale_ms
# of SIMULATED time, so --time-scale divides the wall-clock budget the bench has
# to keep the frames flowing: 500 ms of simulated silence is only 500/S ms of
# real time. At S=20 that is 25 ms, and a Python simulator sharing a machine
# with anything else will miss 25 ms -- both emulated runs on 2026-09-12 tripped
# "charger frames stale" within seconds while a hardware scenario was running on
# the same box. The sims make it worse: MAX_CATCHUP bounds how many missed
# periods they replay, so past 8 periods behind they SKIP frames outright,
# manufacturing exactly the gap that trips the watchdog.
_STALE_MS = 500             # spec 6: 500 ms, both sides (a literal, not Config)
TIME_SCALE_WARN_MS = 50.0    # wall-clock jitter the bench should tolerate


class Scenario(object):
    def __init__(self, name, why, vehicle, charger=(), interposer=(),
                 timeout=600, expect=()):
        self.name = name
        self.why = why
        self.vehicle = list(vehicle)
        self.charger = list(charger)
        self.interposer = list(interposer)
        self.timeout = timeout
        self.expect = list(expect)


# --- expectation helpers ---------------------------------------------------

def ev(pattern):
    """An interposer event log line must match."""
    return ("intp_event", pattern)


def no_ev(pattern):
    return ("intp_no_event", pattern)


def ev_after(first, second):
    """`second` must appear in the interposer log AFTER `first`.

    Written for the restart scenarios (spec 9, A5), where a plain ev() is
    not an assertion at all: the run contains two charge sessions, so
    /LOW_POWER overridden/ matches the FIRST one and passes whether or not
    the second session ever armed -- which is the entire point of the test.
    The first version of restart-after-standby reported PASS on that line
    while the second session had not happened, because the vehicle sim had
    already exited."""
    return ("intp_event_after", (first, second))


def final_state(name):
    return ("intp_final_state", name)


def soc_at_least(v):
    return ("veh_soc_min", v)


def soc_at_most(v):
    return ("veh_soc_max", v)


def vmax_at_most(v):
    return ("veh_vmax_max", v)


def current_at_most(v):
    """Peak charge current the pack ever saw, from the vehicle sim's log.

    THE PLANT, NOT THE CORE. charger_sim clamps delivered current to its EVSE
    figure whatever it is commanded, so this cannot fail on a core that
    commands too much. What the core commands is checked frame by frame by
    the trace invariants (rule 5 of test_invariants.py), which run on every
    scenario that records a trace.
    """
    return ("veh_current_max", v)


def veh_ev(pattern):
    """A VEHICLE-sim log line must match.

    Needed once the VCU stopped being a thing that just quits: the hold, the
    flow-bit drop and the STAND_BY close-out are all VCU decisions, and they are
    only visible in the vehicle log.
    """
    return ("veh_event", pattern)


def spread_at_release_at_least(mv):
    """Cell spread (mV) AT THE RELEASE, from the core's own "top of charge
    reached" event.

    The truck reports one clamped pack SoC, so "terminated below full" is
    only observable through the spread and the weakest cell (spec 9). These
    two checks guard the SCENARIO, not the core: they prove the simulated
    pack really was imbalanced and really did end on the BMS's permission,
    so a change to the pack model cannot quietly turn this into a balanced
    run that still passes.

    Read at the release, not at the unplug (C2). The unplug is the end of
    the session -- minutes of hold later, with the pack relaxing the whole
    time -- so a number taken there describes something else. The core is
    the only thing that knows when the release happened AND is looking at
    the pack when it does, which is why it reports vmin alongside vmax.
    """
    return ("intp_spread_at_release_min", mv)


def vmin_at_release_at_most(v):
    """Weakest cell (V) at the release, same source."""
    return ("intp_vmin_at_release_max", v)


def chg_ev(pattern):
    """A CHARGER-sim log line must match. The harness ignored this log
    entirely until 2026-10-03 (C2), so nothing checked what the charger
    actually did -- which is the only place a release can be seen landing."""
    return ("chg_event", pattern)


def released_into_hold(lo_a=1.0, hi_a=4.0):
    """Spec 5.2 / 9: the release put the charger into the VCU's own hold.

    ANCHORED TO THE CORE'S RELEASE (rev 2, 2026-10-05). Take the
    interposer log's [TERMINATED] RELEASE; from the CHARGER's log, at or
    after that second and before the handle pull, there must be exactly
    ONE entry into mode 3, from mode CHARGER, in state 12, at the hold
    current, with no further mode change until the pull.

    Entries BEFORE the release are legal, and are reported in the
    why-string rather than skipped. Above the ceiling (spec 7, the 83 %
    start) the VCU commands Low Power during the 75 s arm delay and spec
    3 requires it to reach the charger untouched, so a correct run enters
    mode 3 twice.

    Rev 1 latched the first entry anywhere in the log, so on that
    scenario it read the override's return to CHARGER as the charger
    leaving the hold, and failed a correct run -- while passing the eight
    79 % scenarios, where Low Power only arrives at the ceiling with the
    override already in place and there is just one entry. Anchoring to
    the LAST entry instead would have been the easy fix and the wrong
    one: it would pass a charger that left the hold after the release and
    came back, which is close to the defect below.

    Both logs carry the host wall clock at 1 s resolution, so a
    transition in the same second as the release counts as at the
    release. See _stamped() for the midnight case.

    Checking that the core reached TERMINATED cannot see this fail, and this
    failing is precisely the defect spec 4.1 was written after: the charger
    never heard the VCU's Low Power, because we had rewritten its only
    burst and page 00 is sent only on change, so it sat in CHARGER mode
    after the release with no second chance to learn otherwise.

    Note what is NOT checked: the override's own mode phase. The charger
    sees mode CHARGER continuously from plug-in to release -- our rewrite is
    not a transition -- so there is nothing in its log to mark the override
    beginning. A first version looked for one and failed on a correct run.

    test_released_into_hold.py mutates a real charger log once per clause
    and requires each to be rejected for its own reason.
    """
    return ("chg_released_into_hold", (lo_a, hi_a))


def repeat_bursts_of_20():
    """Spec 4.1: every repeat is the VCU's burst shape -- 20 frames announced,
    and 20 sent unless the VCU's own page 00 superseded the burst.

    Two halves, because either alone passes while the other is broken: each
    REPEAT event must say 20, and the final `synth` must account for 20 x the
    number of REPEAT events. A core that logged 20 and sent 5 passes the
    first; a core that logged 5 and sent 5 passes the second.

    The one exception, and it is the spec's: "A page-00 frame from the VCU
    during a repeat ends the repeat; the VCU's frame is the truth." A burst
    the VCU superseded may end at any count, witnessed from the VEHICLE log
    rather than from the core's own account of itself. See _repeat_bursts()
    for why the tolerance cannot be a threshold, and
    test_repeat_bursts.py for the mutations that pin what must still fail.

    What this CANNOT see is a frame built, counted and then dropped before
    it reached the bus -- B-1, where `synth` read 20 and 19 frames went out.
    `synth` is incremented by the core, and the scenario reads the core's
    own tally. test_machine.py counts the emitted frames instead, which is
    the half that caught B-1.
    """
    return ("intp_repeat_20", None)


def diag_frames_ok():
    """Spec 8.2: the diagnostics, decoded from the CAN FRAMES.

    Replaces the old serial-text check (C2), which read `diag ... schema=N`
    out of interposer_sim's stdout. Three things were wrong with that: the
    board under --external-interposer prints the line but a truck build does
    not print at all (spec 8.3), it never looked at the firmware version, and
    it tested the formatter rather than the frames anyone would decode a real
    log with.

    The frames are read from the VEHICLE sim's log, which taps the segment
    they are transmitted on, so this check is identical for the bench and for
    a real board.
    """
    return ("diag_frames_ok", None)




def no_rewrite_after_release():
    """Spec 5.2: from the release on, nothing is rewritten.

    Read off `0x7F6` (the running `modified` count) in the vehicle log's
    diagnostic frames, from the first `0x7F4` that reports TERMINATED. Both
    numbers come from the same CAN stream, so there is no clock to align
    between two logs -- the previous version matched the RELEASE line in the
    interposer's wall-clock log against serial `diag` lines in the same log,
    which worked only because they were the same file.
    """
    return ("diag_no_rewrite_after_release", None)


# Endings (spec 5, 7, 9): the simulated VCU never ends a session on its own,
# and since 2026-09-19 neither does the interposer -- a release is
# transparency, after which the VCU's Low Power hold reaches the charger and
# lasts until the handle is pulled. charger_sim's --unplug-after-hold-s is
# that handle pull, timed from the charger entering mode 3 (which, in an
# override scenario, is the release). The VCU reacts to the charger leaving
# state 12 with flow 0 then STAND_BY, the BMS asserts EPO and opens the
# contactors, so every terminating scenario ends in PASSTHROUGH ("VCU mode 2,
# session over") and --stop-on-done ends the run shortly after that STAND_BY.
SCENARIOS = [
    Scenario(
        "evap-bypass",
        "Control run. Evap ceiling, state machine disabled: the truck must "
        "stop at 80 % exactly as it does today and sit in the hold until the "
        "handle is pulled.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--stop-on-done", "--max-sim-s", "9000"],
        charger=["--unplug-after-hold-s", "600"],
        interposer=["--bypass"],
        timeout=600,
        expect=[soc_at_most(82.5), no_ev(r"LOW_POWER overridden"),
                no_ev(r"TRIP"),
                veh_ev(r"commanding STAND_BY")]),

    Scenario(
        "evap-override",
        "The point of the whole exercise: evap ceiling overridden, charge "
        "continues to a real top of charge, the interposer goes transparent "
        "on the BMS's permission collapsing (spec 5), the VCU's own hold "
        "reaches the charger, and the handle pull closes the session out.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--stop-on-done", "--max-sim-s", "9000"],
        charger=["--unplug-after-hold-s", "600"],
        timeout=600,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"top of charge reached"),
                ev(r"RELEASE"),
                no_ev(r"TRIP"),
                no_rewrite_after_release(),
                released_into_hold(),
                repeat_bursts_of_20(),
                chg_ev(r"HANDLE PULLED"),
                veh_ev(r"charger left state 12"),
                veh_ev(r"commanding STAND_BY"),
                ev(r"session over"),
                final_state("PASSTHROUGH"),
                diag_frames_ok(),
                soc_at_least(99.5),
                vmax_at_most(3.610)]),

    Scenario(
        "evap-override-above-ceiling",
        "Plugged in at 83 % with evap pending (spec 7, the 83 % start): the "
        "VCU commands CHARGER with a 0 A setpoint and Low Power seconds later. "
        "The core must arm without a net charging current, wait out the 75 s "
        "arm delay without touching the (possible) startup transient, then "
        "override at 75 s and run to a real top of charge.",
        vehicle=["--evap", "--soc", "83", "--imbalance", "0.4",
                 "--stop-on-done", "--max-sim-s", "9000"],
        charger=["--unplug-after-hold-s", "600"],
        timeout=600,
        expect=[ev(r"waiting out the arm delay"),
                ev(r"LOW_POWER overridden"),
                ev(r"top of charge reached"),
                ev(r"RELEASE"),
                no_ev(r"TRIP"),
                no_rewrite_after_release(),
                released_into_hold(),
                repeat_bursts_of_20(),
                chg_ev(r"HANDLE PULLED"),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH"),
                diag_frames_ok(),
                soc_at_least(99.5),
                vmax_at_most(3.610)]),

    Scenario(
        "evap-override-early-bms",
        "Obey the BMS. Its chg_max taper is shifted +50 mV, so it calls the "
        "pack full at a ~3.54 V leader cell. The release must land there. A "
        "release line reading vmax=35xx proves it was the BMS's number.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--bms-taper-shift-mv", "50",
                 "--stop-on-done", "--max-sim-s", "7000"],
        charger=["--unplug-after-hold-s", "600"],
        timeout=600,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"top of charge reached.*(?:vmax|a)=35[0-6]\d(?!\d)"),
                ev(r"RELEASE"),
                no_ev(r"TRIP"),
                released_into_hold(),
                repeat_bursts_of_20(),
                chg_ev(r"HANDLE PULLED"),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH"),
                soc_at_least(85.0),
                vmax_at_most(3.570)]),

    Scenario(
        "evap-override-imbalanced",
        "The parts-truck pack under our override: 1.0 % spread with one cell "
        "2.5 % low. The leaders reach the top while the weak cell is behind, so "
        "the BMS tapers on a pack that is not full. We must release there: the "
        "lower than expected termination point, visible as the spread and the "
        "weakest cell AT THE RELEASE (the reported SoC is clamped and cannot "
        "show it), with vmax never past 3.610 V.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "1.0",
                 "--outlier-cell", "7", "--outlier-pct", "2.5",
                 "--stop-on-done", "--max-sim-s", "9000"],
        charger=["--unplug-after-hold-s", "600"],
        timeout=600,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"top of charge reached"),
                ev(r"RELEASE"),
                no_ev(r"TRIP"),
                released_into_hold(),
                repeat_bursts_of_20(),
                chg_ev(r"HANDLE PULLED"),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH"),
                spread_at_release_at_least(50.0),
                vmin_at_release_at_most(3.500),
                vmax_at_most(3.610)]),

    Scenario(
        "evap-override-16a",
        "The override on a 16 A EVSE (26 % pilot). Must still work, and must "
        "never command past the EVSE's 8 A DC ceiling -- J1772 compliance is "
        "the interposer's responsibility once it takes over the setpoint.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--stop-on-done", "--max-sim-s", "14000"],
        charger=["--pilot-duty", "26", "--unplug-after-hold-s", "600"],
        timeout=750,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"top of charge reached"),
                ev(r"RELEASE"),
                no_ev(r"TRIP"),
                released_into_hold(),
                repeat_bursts_of_20(),
                chg_ev(r"HANDLE PULLED"),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH"),
                soc_at_least(99.0),
                vmax_at_most(3.610)]),

    Scenario(
        "evap-override-unplug",
        "The handle is pulled mid-override -- the truck's real ending. The "
        "charger goes to 15 with shutdownSource 11, the VCU drops flow and "
        "sends STAND_BY, and the interposer must simply get out of the way: "
        "no trip, no release, PASSTHROUGH.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--stop-on-done", "--max-sim-s", "9000"],
        charger=["--unplug-at", "2500"],
        timeout=600,
        expect=[ev(r"LOW_POWER overridden"),
                veh_ev(r"charger left state 12"),
                veh_ev(r"commanding STAND_BY"),
                ev(r"session over"),
                no_ev(r"TRIP"),
                no_ev(r"RELEASE"),
                final_state("PASSTHROUGH")]),

    Scenario(
        "evap-override-bms-fault",
        "The BMS asserts cell_overvolt mid-override (spec 6). Trip straight to "
        "SAFE, transparent: the VCU's hold reaches the charger, and the handle "
        "pull closes the session out, which clears SAFE.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--bms-fault-at", "2500", "--bms-fault-kind", "overvolt",
                 "--stop-on-done", "--max-sim-s", "9000"],
        charger=["--unplug-after-hold-s", "300"],
        timeout=600,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"TRIP: BMS cell_overvolt"),
                no_ev(r"RELEASE"),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH")]),

    Scenario(
        "normal-full",
        "A balanced pack charging normally to 100 %. The stop is genuine (evap "
        "flag clear), so the interposer must not touch a single byte; the "
        "hold ends at the handle pull.",
        vehicle=["--soc", "88", "--imbalance", "0.3",
                 "--stop-on-done", "--max-sim-s", "30000"],
        charger=["--unplug-after-hold-s", "180"],
        timeout=750,
        expect=[ev(r"LOW_POWER accepted"),
                no_ev(r"LOW_POWER overridden"),
                no_ev(r"TRIP"),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH"),
                soc_at_least(99.5)]),

    Scenario(
        "imbalanced-full",
        "The parts-truck case: badly imbalanced pack with one low cell. The "
        "BMS drives the taper, so again no intervention -- and the long CV "
        "balancing tail must pass through untouched.",
        vehicle=["--soc", "85", "--imbalance", "1.0", "--outlier-cell", "7",
                 "--outlier-pct", "2.5", "--stop-on-done",
                 "--max-sim-s", "40000"],
        charger=["--unplug-after-hold-s", "180"],
        timeout=850,
        expect=[ev(r"LOW_POWER accepted"),
                no_ev(r"LOW_POWER overridden"),
                no_ev(r"TRIP"),
                vmax_at_most(3.610)]),

    Scenario(
        "balance-hold",
        "The balance hold, ended the way every captured hold ended: the handle "
        "is pulled. Mode 3 parks the charger at the VCU's ~2.4 A hold while "
        "the pack balances; nothing in the VCU ends it. The interposer must "
        "sit through all of it untouched, must not read the hold as an evap "
        "ceiling (the flag is clear), and must go transparent on the VCU's "
        "STAND_BY.",
        vehicle=["--soc", "90", "--imbalance", "0.4", "--outlier-cell", "7",
                 "--outlier-pct", "0.5", "--stop-on-done",
                 "--max-sim-s", "20000"],
        charger=["--unplug-at", "5000"],
        timeout=1200,
        expect=[ev(r"LOW_POWER accepted"),
                no_ev(r"LOW_POWER overridden"),
                no_ev(r"TRIP"),
                veh_ev(r"commanding CHARGER_LOW_POWER"),
                veh_ev(r"charger left state 12"),
                veh_ev(r"commanding STAND_BY"),
                veh_ev(r"contactors opening"),
                final_state("PASSTHROUGH"),
                vmax_at_most(3.610)]),

    Scenario(
        "charger-fault-stop",
        "The corpus's majority ending, now known for what it is: a charger "
        "internal fault (shutdownSource 3) mid-charge, with no override in "
        "play. The interposer trips from MONITOR to SAFE, the VCU drops flow "
        "with the mode left at CHARGER and sends STAND_BY, and that clears SAFE.",
        vehicle=["--soc", "88", "--imbalance", "0.3", "--stop-on-done",
                 "--max-sim-s", "30000"],
        charger=["--fault-at", "600"],
        timeout=750,
        expect=[no_ev(r"LOW_POWER overridden"),
                no_ev(r"LOW_POWER accepted"),
                ev(r"TRIP: charger fault"),
                veh_ev(r"dropping energy flow, mode left at CHARGER"),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH"),
                diag_frames_ok()]),

    Scenario(
        "fault-during-override",
        "An inverterFault while we are driving the charge. Must trip to SAFE "
        "(transparent, nothing originated) and then obey the VCU's close-out.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--stop-on-done", "--max-sim-s", "6000"],
        charger=["--fault-at", "2500"],
        timeout=420,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"TRIP: charger fault"),
                no_ev(r"RELEASE"),
                # Spec 4.1: a trip DURING an override repeats the VCU's
                # standing command too, so the charger hears the Low Power
                # we had been rewriting away.
                repeat_bursts_of_20(),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH")]),

    Scenario(
        "hard-ceiling-failsafe",
        "Negative test with BOTH normal stops disabled: the release threshold "
        "is disabled AND the BMS's charge-current permission is pinned at "
        "300 A so it never tapers. Only the interposer's own 3.610 V cell "
        "ceiling is left to stop the override; the trip is transparency, so "
        "what follows is the VCU's own hold, ended by the handle pull. Note "
        "what this took to set up -- while the BMS behaves, bcm_chg_max "
        "collapsing already releases. Must start BELOW the 80 % ceiling so "
        "the VCU's stop comes after a charge is established.",
        vehicle=["--evap", "--soc", "79", "--imbalance", "0.4",
                 "--bms-stuck-chg-max", "300", "--stop-on-done",
                 "--max-sim-s", "16000"],
        charger=["--unplug-after-hold-s", "300"],
        interposer=["--chgmax-full-a", "-1"],
        timeout=800,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"TRIP: vmax \d+ mV over hard ceiling"),
                no_ev(r"RELEASE"),
                repeat_bursts_of_20(),
                veh_ev(r"commanding STAND_BY"),
                final_state("PASSTHROUGH"),
                vmax_at_most(3.660)]),
    # --- spec 9 additions, review items A3 / A5 / 6.1 --------------------

    Scenario(
        "restart-after-standby",
        "Spec 9 (A5): a charge that fails, then a NEW session on the same "
        "run, across the VCU's STAND_BY. The realistic case is a truck "
        "plugged into one handle where the charge fails, moved to another, "
        "and charged fine. The second session must arm and override "
        "normally rather than stay latched from the first -- before 6.1 the "
        "core cleared its session state only on STAND_BY and carried SAFE "
        "and the charger-reached-12 flag into the next session.",
        vehicle=["--evap", "--soc", "79", "--restart-on-replug",
                 "--max-sim-s", "1200"],
        charger=["--fault-at", "200", "--unplug-at", "260",
                 "--replug-at", "900"],
        timeout=400,
        # NOT --stop-on-done: it ends the run 30 s after the FIRST STAND_BY,
        # which is before the replug, so the second session never happens --
        # and the plain ev(/LOW_POWER overridden/) this used to assert still
        # passed, on the first session's override. Hence ev_after.
        expect=[ev(r"TRIP: charger fault"),
                ev(r"pilot timer stepped back"),
                ev_after(r"pilot timer stepped back",
                         r"LOW_POWER overridden"),
                diag_frames_ok()]),

    Scenario(
        "restart-no-standby",
        "Spec 9 (A5): the same restart, but with NO VCU STAND_BY in "
        "between -- the boundary is the pilot timer stepping back to 0 on "
        "its own. 11 of the 49 terminations in the survey do not end on a "
        "STAND_BY, so this is the path that mattered: without the "
        "pilot-timer boundary the core would still be latched.",
        vehicle=["--evap", "--soc", "79", "--restart-on-replug",
                 "--max-sim-s", "900", "--no-standby-on-unplug"],
        charger=["--unplug-at", "200", "--replug-at", "600"],
        timeout=350,
        expect=[ev(r"pilot timer stepped back"),
                ev_after(r"pilot timer stepped back",
                         r"LOW_POWER overridden"),
                no_ev(r"TRIP"),
                diag_frames_ok()]),

    Scenario(
        "boot-mid-session",
        "Spec 9 (A3): the board powers up into a session already running. "
        "The pilot timer reads well past 2 minutes from the first frame, so "
        "the core stays in PASSTHROUGH for the whole of it and never "
        "overrides -- a freshly booted core cannot know the VCU's standing "
        "page-00 command, because page 00 is sent only on change.",
        vehicle=["--evap", "--soc", "79", "--stop-on-done",
                 "--max-sim-s", "8000"],
        charger=["--pilot-start-min", "30", "--unplug-after-hold-s", "120"],
        timeout=600,
        expect=[ev(r"booted into a session already \d+ min old"),
                no_ev(r"LOW_POWER overridden"),
                no_ev(r"charge established"),
                no_ev(r"TRIP"),
                final_state("PASSTHROUGH"),
                diag_frames_ok()]),

    Scenario(
        "charger-silence-after-trip",
        "Spec 9 (6.1): the charger goes off the bus during an override. "
        "Staleness trips at 500 ms, and 20 s later the silence boundary "
        "resets the working state -- but NOT SAFE. Silence must not undo "
        "the latch that silence set.",
        vehicle=["--evap", "--soc", "79", "--stop-on-done",
                 "--max-sim-s", "8000"],
        charger=["--silent-at", "2600"],
        timeout=600,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"TRIP: charger frames stale"),
                ev(r"no charger status frame"),
                final_state("SAFE"),
                diag_frames_ok()]),

    Scenario(
        "hvil-before-flow-drop",
        "Spec 9 (2026-10-03): the handle is pulled and HVIL opens 20 ms "
        "BEFORE the VCU drops the flow bit, the ordering in 5 of the 13 "
        "source-11 pulls in the corpus. The charger's plug-out report has "
        "already made the core transparent about a second earlier, so the "
        "early HVIL must NOT trip. Under the teardown window this replaced "
        "it logged a false 'HVIL open' fault at every such unplug.",
        vehicle=["--evap", "--soc", "79", "--stop-on-done",
                 "--hvil-before-flow-drop", "--max-sim-s", "8000"],
        # --unplug-at, NOT --unplug-after-hold-s. The latter is timed from
        # the charger entering mode 3, and during an override the core holds
        # the charger in mode 1, so mode 3 only arrives at the RELEASE at top
        # of charge. The pull then lands in TERMINATED, where the plug-out
        # rule is correctly gated off -- so the scenario ran the early HVIL
        # against a core that was never in the state the rule protects, and
        # reported three green assertions while testing nothing.
        charger=["--unplug-at", "600"],
        timeout=600,
        expect=[ev(r"LOW_POWER overridden"),
                ev(r"charger reports the plug out"),
                no_ev(r"TRIP"),
                final_state("PASSTHROUGH"),
                diag_frames_ok()]),

]

BY_NAME = {s.name: s for s in SCENARIOS}


class SerialTap(object):
    """Capture the board's serial output into the interposer log.

    Exists so `--external-interposer` runs can be judged by the same
    expectations as simulated ones. The board's event lines already read
    "LOW_POWER overridden", "top of charge reached" and so on, because
    machine.cpp's eventName() returns the same strings machine.py logs -- so
    ev()/no_ev() match unchanged.

    The one thing the board cannot produce is interposer_sim's parting
    "final state=X" line: it has no idea the scenario ended. So on stop() we
    synthesise it from the last status line seen, which is what
    final_state() reads.
    """

    STATE_RE = re.compile(r"state=(\w+)")

    def __init__(self, port, baud, path):
        if not port:
            raise SystemExit(
                "--external-interposer needs --serial-port (e.g. COM7). "
                "Without it the run produces no interposer log and every "
                "event expectation fails for the wrong reason.")
        self.port = port
        self.baud = baud
        self.path = path
        self._stop = threading.Event()
        self._thread = None
        self._last_state = None
        self._fh = None

    def start(self):
        try:
            import serial  # noqa: F401  (lazy: only --external-interposer needs it)
        except ImportError:
            raise SystemExit("pyserial is required for --external-interposer: "
                             "pip install pyserial")
        self._fh = open(self.path, "w", encoding="utf-8", errors="replace")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        import serial
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.5)
        except Exception as exc:                       # noqa: BLE001
            self._fh.write("SERIAL OPEN FAILED: %s\n" % exc)
            self._fh.flush()
            return
        with ser:
            while not self._stop.is_set():
                try:
                    raw = ser.readline()
                except Exception as exc:               # noqa: BLE001
                    self._fh.write("SERIAL READ FAILED: %s\n" % exc)
                    break
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                m = self.STATE_RE.search(line)
                if m:
                    self._last_state = m.group(1)
                self._fh.write(line + "\n")
                self._fh.flush()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._fh is not None:
            if self._last_state:
                self._fh.write("final state=%s\n" % self._last_state)
            else:
                self._fh.write("final state=UNKNOWN "
                               "(no status line seen on serial)\n")
            self._fh.close()


# Windows has no way to send SIGINT to another process: send_signal(SIGINT)
# raises ValueError("Unsupported signal: 2"). That used to escape from run_one's
# finally block, which meant the scenario was never evaluated AND the charger
# sim was left running -- still holding its dongle and still transmitting on the
# physics bus. Starting children in their own process group lets CTRL_BREAK_EVENT
# reach them. Python does NOT turn that into KeyboardInterrupt by itself -- it
# installs no SIGBREAK handler, so the default action just terminates the child
# and its `finally` never runs. bus.install_break_handler(), called by every
# sim, maps SIGBREAK to KeyboardInterrupt so the existing shutdown paths run.
if os.name == "nt":
    POPEN_KW = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
else:
    POPEN_KW = {}


# Spec 9: "Every scenario decodes the diagnostic CAN frames -- not the serial
# text -- and checks state and firmware version". Rev 1 asked for it in 8 of
# 19 scenarios. Applied here to every one, so a scenario added later cannot
# leave it out (tester, 2026-10-07). Likewise every termination scenario --
# every one that asks for released_into_hold() -- checks that nothing was
# rewritten after the release (spec 5.2); 3 of the 5 did not.
for _sc in SCENARIOS:
    _kinds = [k for k, _v in _sc.expect]
    if "diag_frames_ok" not in _kinds:
        _sc.expect.append(diag_frames_ok())
    if ("chg_released_into_hold" in _kinds
            and "diag_no_rewrite_after_release" not in _kinds):
        _sc.expect.append(no_rewrite_after_release())
del _sc, _kinds


def _interrupt(p):
    """Ask a simulator to shut down cleanly, on either platform."""
    try:
        if os.name == "nt":
            p.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            p.send_signal(signal.SIGINT)
    except Exception:                                  # noqa: BLE001
        # Never let cleanup raise: a failure here strands simulators.
        try:
            p.terminate()
        except Exception:                              # noqa: BLE001
            pass


def _transport_args(args, side):
    """Dongle selection for one segment, empty for the virtual transport."""
    out = []
    brand = getattr(args, "%s_transport" % side)
    chan = getattr(args, "%s_channel" % side)
    if brand:
        out += ["--transport", brand]
    if chan:
        out += ["--channel", chan]
    return out


def run_one(sc, args, outdir):
    veh_log = outdir / ("%s.vehicle.log" % sc.name)
    chg_log = outdir / ("%s.charger.log" % sc.name)
    int_log = outdir / ("%s.interposer.log" % sc.name)
    trace_path = outdir / ("%s.l1.trace" % sc.name)

    ts = ["--time-scale", str(args.time_scale)]
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    procs = []
    tap = None
    try:
        with open(chg_log, "w") as f:
            procs.append(subprocess.Popen(
                [PY, str(HERE / "charger_sim.py"),
                 "--port", str(args.charger_port),
                 "--phys-port", str(args.phys_port)]
                + _transport_args(args, "charger")
                + ts + sc.charger, stdout=f, stderr=subprocess.STDOUT, env=env,
                **POPEN_KW))
        if args.external_interposer:
            # The board is the interposer. Its serial output stands in for
            # interposer_sim's log, so the ev()/no_ev()/final_state()
            # expectations evaluate against the real thing unchanged.
            if sc.interposer:
                print("   note: scenario passes %s to the interposer, which an "
                      "external board cannot accept -- flash a matching build "
                      "or expect this scenario to fail" % (sc.interposer,))
            tap = SerialTap(args.serial_port, args.serial_baud, int_log)
            tap.start()
        else:
            with open(int_log, "w") as f:
                procs.append(subprocess.Popen(
                    [PY, str(HERE / "interposer_sim.py"),
                     "--vehicle-port", str(args.vehicle_port),
                     "--charger-port", str(args.charger_port)]
                    + ([] if args.no_l1
                       else ["--trace-out", str(trace_path)])
                    + ts + sc.interposer, stdout=f,
                    stderr=subprocess.STDOUT, env=env, **POPEN_KW))
        time.sleep(1.5)
        with open(veh_log, "w") as f:
            veh = subprocess.Popen(
                [PY, str(HERE / "vehicle_sim.py"),
                 "--port", str(args.vehicle_port),
                 "--phys-port", str(args.phys_port),
                 # SIMULATED seconds, and only for a human reading the
                 # log -- the assertions below read vehicle_sim's `peaks`
                 # line, not these rows. 60 rather than 1200 so a short
                 # scenario still has a readable trace of itself.
                 "--report-every", "60"]
                + _transport_args(args, "vehicle") + ts + sc.vehicle,
                stdout=f, stderr=subprocess.STDOUT, env=env,
                **POPEN_KW)
        try:
            veh.wait(timeout=sc.timeout * args.timeout_scale)
            timed_out = False
        except subprocess.TimeoutExpired:
            veh.kill()
            veh.wait()
            timed_out = True
    finally:
        if tap is not None:
            tap.stop()
        for p in procs:
            _interrupt(p)
        deadline = time.time() + 5
        for p in procs:
            try:
                p.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()

    l1 = None
    inv = None
    if args.external_interposer or args.no_l1:
        # Said out loud (tester, 2026-10-07): rev 1 appended nothing here,
        # so a run with the flight core unchecked printed the same PASS as
        # one with it checked.
        l1 = (True, "NOT RUN (%s) -- this result does not cover the flight "
                    "core or the trace invariants"
              % ("--external-interposer" if args.external_interposer
                 else "--no-l1"))
    elif "--bypass" not in sc.interposer:
        inv = check_invariants(trace_path)
    if not args.external_interposer and not args.no_l1:
        if "--bypass" in sc.interposer:
            # The core is switched OFF in this scenario, so interposer_sim
            # writes no trace (its --trace-out is documented as ignored
            # under --bypass) and there are no core decisions to replay
            # through machine.cpp.
            #
            # This is keyed off the SCENARIO DECLARING --bypass, never off
            # the trace merely being missing. That distinction is the whole
            # point: check_flight_core() fails on an absent trace precisely
            # so a machine without a C++ compiler cannot quietly pass, and
            # widening the exemption to "no trace appeared" would hand that
            # silent pass back to every scenario.
            #
            # Nor is the answer to make bypass write a trace anyway. The
            # differential would then compare a core that did nothing
            # against a core that did nothing -- green, and testing
            # nothing at all.
            l1 = (True, "not applicable: the scenario runs the core in "
                        "bypass, so there are no core decisions to replay")
        else:
            l1 = check_flight_core(trace_path)
    return evaluate(sc, veh_log, chg_log, int_log, timed_out, l1, inv)


VEH_ROW = re.compile(
    r"t=\s*(\d+)s soc=\s*([\d.]+) vmax=\s*([\d.]+) vmin=\s*([\d.]+)")
VEH_CUR = re.compile(r"I=\s*(-?[\d.]+)A")
# vehicle_sim's true maxima over every model step, printed once at exit.
VEH_PEAKS = re.compile(
    r"peaks over (\d+) model steps: I=(-?[\d.]+)A vmax=([\d.]+)V "
    r"soc=([\d.]+)%")
# Spec 8.2 frames as the vehicle sim taps them off the segment.
DIAG_FRAME = re.compile(
    r"diagframe t=([\d.]+) id=0x([0-9A-Fa-f]{3}) data=([0-9a-f]{16})")
# The release, from the core's own event. Both renderings: machine.py writes
# the names, the board writes machine.cpp's a=/b=/c= fields.
TOC_NAMED = re.compile(
    r"top of charge reached: vmax=(\d+) mV vmin=(\d+) mV")
TOC_BOARD = re.compile(r"top of charge reached a=(\d+) b=-?\d+ c=(\d+)")
REPEAT_EV = re.compile(r"REPEAT: .*?, (\d+) frames")
FINAL_SYNTH = re.compile(r"final state=\w+.*synth=(\d+)")
CHG_MODE = re.compile(r"charger: mode (\w+) -> (\w+) \(setpoint ([\d.]+) A\)")
CHG_STATE = re.compile(r"charger: state (\d+) -> (\d+) ")
CHG_ANY = re.compile(
    r"charger: (?:mode (?P<mf>\w+) -> (?P<mt>\w+) \(setpoint (?P<sp>[\d.]+) A\)"
    r"|state (?P<sf>\d+) -> (?P<st>\d+) )")

# machine.py STATE_NAMES, by the byte 0x7F4 B2 carries.
DIAG_STATES = {0: "PASSTHROUGH", 1: "MONITOR", 2: "OVERRIDE",
               4: "TERMINATED", 5: "SAFE"}


def parse_diag_frames(veh_text):
    """-> list of (sim_t, id, payload bytes), in order."""
    out = []
    for t, fid, body in DIAG_FRAME.findall(veh_text):
        out.append((float(t), int(fid, 16), bytes.fromhex(body)))
    return out


def release_vmax_vmin(intp_text):
    """(vmax_mV, vmin_mV) at the release, or (None, None)."""
    m = TOC_NAMED.search(intp_text) or TOC_BOARD.search(intp_text)
    if m is None:
        return (None, None)
    return (int(m.group(1)), int(m.group(2)))


# The check below reads TWO logs, so it needs one clock. Both sims print
# the host wall clock at 1 s resolution and are started about 1.5 s apart
# on the same day, so seconds-of-day compares directly -- with one
# wrinkle: a scenario can cross midnight, and 00:00:01 is not later than
# 23:59:59. _stamped() unwraps that per log.
LOG_TS = re.compile(r"^(\d\d):(\d\d):(\d\d) ")

# Bumped whenever the RULE changes, so a recorded verdict can be quoted
# with the check that produced it.
#   1  the first entry into mode 3 anywhere in the log
#   2  (2026-10-05) anchored to the core's RELEASE
RELEASED_INTO_HOLD_REV = 2


def _stamped(text):
    """[(abs_s, line)] -- seconds of day, midnight unwrapped.

    A line with no timestamp inherits the last one seen, so a
    continuation line sorts with the line it belongs to instead of
    being silently dropped out of the ordering.
    """
    out = []
    prev = None
    day = 0
    for line in text.splitlines():
        m = LOG_TS.match(line)
        if m:
            s = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                 + int(m.group(3)))
            # Only a step backwards of more than an hour is midnight.
            # Anything smaller is two lines sharing a second, written
            # out of order, which must not shift the whole log a day.
            if prev is not None and s < prev - 3600:
                day += 86400
            prev = s
        out.append((None if prev is None else prev + day, line))
    return out


def _release_time(intp_text):
    """Seconds of day at the core's RELEASE, or None if it never came."""
    for t, line in _stamped(intp_text):
        if "[TERMINATED] RELEASE" in line:
            return t
    return None


def _released_into_hold(chg_text, intp_text, lo_a, hi_a):
    """-> (ok, why). See released_into_hold()."""
    rel = _release_time(intp_text)
    if rel is None:
        return (False, "the core never logged a RELEASE, so there is no "
                       "release for the charger's behaviour to be measured "
                       "against. This is a DIFFERENT failure from the "
                       "charger mishandling a release, and it is not "
                       "evidence about the charger at all")

    state = None
    before = []          # entries into mode 3 BEFORE the release
    entry = None         # (t, setpoint, state) at the post-release entry
    for t, line in _stamped(chg_text):
        if "HANDLE PULLED" in line or "FAULT" in line:
            break        # everything after this is teardown
        m = CHG_STATE.search(line)
        if m:
            state = int(m.group(2))
            continue
        m = CHG_MODE.search(line)
        if not m:
            continue
        frm, to, sp = m.group(1), m.group(2), float(m.group(3))
        # Stamped to the second, so a transition in the SAME second as
        # the release IS the release, not something preceding it.
        if t is not None and t < rel:
            if to == "CHARGER_LOW_POWER":
                before.append((line.split(" ", 1)[0], sp))
            continue
        if entry is None:
            if to != "CHARGER_LOW_POWER":
                return (False, "the first mode change at or after the "
                               "release was -> %s, not into the VCU's hold"
                        % to)
            if frm != "CHARGER":
                return (False, "the charger entered mode 3 from %s, not "
                               "CHARGER" % frm)
            entry = (t, sp, state)
        else:
            return (False, "the charger left mode 3 again (-> %s) before "
                           "the handle pull" % to)

    # Entries before the release are LEGAL and are reported rather than
    # skipped. Above the ceiling (spec 7) the VCU commands Low Power
    # during the arm delay, and spec 3 requires that to reach the charger
    # untouched -- so a correct run has two entries, and rev 1 of this
    # check read the second one as the charger leaving the hold.
    earlier = ("" if not before else
               "; %d earlier entry into mode 3 (%s), before the release, "
               "which spec 3 requires to pass through untouched"
               % (len(before),
                  ", ".join("%s at %.2f A" % b for b in before)))

    if entry is None:
        return (False, "the charger never entered mode 3 after the release "
                       "-- it sat in CHARGER, which is the spec 4.1 defect "
                       "this check exists for" + earlier)
    _t, sp, st = entry
    if st != 12:
        return (False, "the VCU's Low Power reached the charger after the "
                       "release, but it was in state %s, not 12"
                       % st + earlier)
    if not (lo_a <= sp <= hi_a):
        return (False, "charger in mode 3 in state 12 after the release, "
                       "but at %.2f A, outside the hold band %.1f-%.1f A"
                % (sp, lo_a, hi_a) + earlier)
    return (True, "mode 3 in state 12 at %.2f A, entered at or after the "
                  "release and held to the handle pull%s" % (sp, earlier))


# Spec 4.1 bursts: "20 frames at 50 ms". The spec's literals, NOT the
# core's Config (tester, 2026-10-07): taking them from the product meant a
# core whose Config drifted to 10 frames would have been scored against 10
# and passed.
BURST_FRAMES = 20
BURST_MS = BURST_FRAMES * 50

# Spec 8.2 and the schema doc's version table: firmware version 4 carries
# the section A and B behaviour, schema 3. Literals for the same reason.
SPEC_FW_VER = 4
SPEC_SCHEMA_VER = 3

# A timestamped REPEAT, and the vehicle-side markers of the VCU changing
# page 00: a mode command, or the energy-flow bit going to 0. "VCU: charger
# left state ..." is NOT one of these -- it is the VCU noticing something,
# and it says itself that the command follows in REACT_HOLD_S.
REPEAT_EV_T = re.compile(r"^(\d\d:\d\d:\d\d) .*?REPEAT: .*?, (\d+) frames",
                         re.M)
# How a burst ENDED, which machine.py and machine.cpp both log from
# 2026-10-05. Two forms for one event, as with TOC_NAMED / TOC_BOARD: the
# sim writes prose, the board writes the event name plus a/b/c.
REPEAT_END_NAMED = re.compile(
    r"^(\d\d:\d\d:\d\d) .*?REPEAT ended: (\d+) of (\d+), (.+?)\s*$", re.M)
REPEAT_END_BOARD = re.compile(r"REPEAT ended a=(\d+) b=(\d+) c=(\d+)")
# machine.cpp's RepeatEnd, and the prose machine.py writes for each.
REPEAT_END_REASONS = {0: "complete",
                      1: "superseded by the VCU's page 00",
                      2: "abandoned on session reset",
                      3: "replaced by a new repeat"}

VCU_PAGE00 = re.compile(
    r"^(\d\d:\d\d:\d\d) .*?VCU: (?:commanding|dropping energy flow)", re.M)

# Spec 6.1 session boundaries, as the sims record them -- the witnesses for a
# burst that ends "abandoned on session reset" (spec 4.1: a repeat ends early
# on "a session boundary (6.1), which resets any repeat"):
#  - the VCU commands STAND_BY or EXPORT (vehicle log);
#  - the pilot timer steps back: charger_sim logs PILOT TIMER BACK TO 0
#    after the VCU's STAND_BY that follows a handle pull (spec 9,
#    2026-10-08; until then it zeroed the timer at the pull itself, so a
#    HANDLE PULLED line no longer witnesses a step-back), and a replug
#    restarts it from 0 (charger log);
#  - no charger status for 20 s: the boundary falls 20 s after the charger
#    log's GOING SILENT line. Like _burst_window, this assumes 1x, where a
#    log stamp is sim time.
VCU_BOUNDARY = re.compile(
    r"^(\d\d:\d\d:\d\d) .*?VCU: commanding (?:STAND_BY|EXPORT)", re.M)
CHG_PILOT_BACK = re.compile(
    r"^(\d\d:\d\d:\d\d) .*?charger: (?:PILOT TIMER BACK TO 0|HANDLE REPLUGGED)",
    re.M)
CHG_SILENT = re.compile(r"^(\d\d:\d\d:\d\d) .*?charger: GOING SILENT", re.M)
CHG_SILENCE_S = 20                 # spec 6.1


def _secs(hhmmss):
    h, m, s = hhmmss.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _page00_in_burst(veh_text, start_hhmmss):
    """Did the VCU change page 00 inside this burst? -> [stamps].

    Both logs are stamped to the second and a burst is BURST_MS long, so
    the window is widened by one second at the top: at this resolution a
    frame in the second after the burst's nominal end cannot be told from
    one just inside it. That is deliberately the permissive direction --
    it can only ever EXCUSE a short burst, never condemn a complete one,
    and the mutation tests pin the direction that must still fail.
    """
    t0, t1 = _burst_window(start_hhmmss)
    return [t for t in VCU_PAGE00.findall(veh_text)
            if t0 <= _secs(t) <= t1]


def _boundary_in_burst(veh_text, chg_text, start_hhmmss):
    """-> [(stamp, what)] spec 6.1 boundaries inside this burst's window."""
    t0, t1 = _burst_window(start_hhmmss)
    hits = []
    for t in VCU_BOUNDARY.findall(veh_text):
        if t0 <= _secs(t) <= t1:
            hits.append((t, "VCU STAND_BY/EXPORT"))
    for t in CHG_PILOT_BACK.findall(chg_text or ""):
        if t0 <= _secs(t) <= t1:
            hits.append((t, "pilot timer back to 0"))
    for t in CHG_SILENT.findall(chg_text or ""):
        if t0 <= _secs(t) + CHG_SILENCE_S <= t1:
            hits.append((t, "charger silent since %s, 20 s" % t))
    return hits


def _burst_window(start_hhmmss):
    """-> (t0, t1) seconds, the window a burst started at `start` occupies.

    One definition for both the superseding check and the spec 4.1
    replaced check, so the two cannot drift apart. The +1 second is the
    log resolution: both logs are stamped to the second, so an event in
    the second after the burst's nominal end cannot be distinguished from
    one just inside it. Permissive by choice -- it can only ever EXCUSE a
    short burst, never condemn a complete one -- and the mutation tests
    pin the direction that must still fail.
    """
    t0 = _secs(start_hhmmss)
    return (t0, t0 + (BURST_MS + 999) // 1000 + 1)


def _burst_ends(intp_text):
    """-> [(sent, announced, reason_text)] in log order, or None.

    None means this log predates the REPEAT-ended line entirely, which is
    the only case allowed to fall back to the aggregate score. A log with
    SOME ends and not others is not a fallback case: it is a log that lost
    one, and that has to fail.
    """
    named = REPEAT_END_NAMED.findall(intp_text)
    if named:
        return [(int(sent), int(ann), why.strip())
                for _t, sent, ann, why in named]
    board = REPEAT_END_BOARD.findall(intp_text)
    if board:
        return [(int(sent), int(ann),
                 REPEAT_END_REASONS.get(int(c), "reason %s" % c))
                for sent, ann, c in board]
    return None


def _score_per_burst(starts, ends, synth, veh_text, frames, chg_text=""):
    """-> (ok, why). Every burst answered for on its own evidence.

    The aggregate score cannot attribute a shortfall. With two bursts,
    `synth 21 of 40` plus one superseding frame inside the FIRST burst's
    window passes -- while the same logs fit "burst 1 complete, burst 2 sent
    1 with nothing superseding it", which is a defect. Found by the review
    session on 2026-10-05 by mutating a real two-burst log: the aggregate
    bound, want - frames x superseded, is simply too loose to see it.

    The vehicle log stays the witness. The core's own reason says WHICH
    burst to look at; it never excuses one on its own authority, so a core
    that mislabelled a dropped burst as superseded still fails.
    """
    if len(ends) != len(starts):
        return (False, "%d REPEAT(s) but %d end line(s) -- a burst never "
                       "reported how it ended, so its count is unaccounted "
                       "for" % (len(starts), len(ends)))

    total = 0
    notes = []
    for i, ((t, ann_s), (sent, ann_e, why)) in enumerate(zip(starts, ends)):
        if int(ann_s) != ann_e:
            return (False, "burst %d announced %s at the start and %d at the "
                           "end" % (i + 1, ann_s, ann_e))
        total += sent
        if sent > ann_e:
            return (False, "burst %d sent %d of %d -- more than it announced"
                    % (i + 1, sent, ann_e))
        if why.startswith("complete"):
            if sent != ann_e:
                return (False, "burst %d reports COMPLETE but sent %d of %d"
                        % (i + 1, sent, ann_e))
            notes.append("burst %d at %s %d/%d complete"
                         % (i + 1, t, sent, ann_e))
            continue
        if why.startswith("superseded"):
            hits = _page00_in_burst(veh_text, t)
            if not hits:
                return (False, "burst %d at %s reports SUPERSEDED at %d of "
                               "%d, but the vehicle log shows no VCU page 00 "
                               "inside its window -- the core's own reason "
                               "does not get to excuse it"
                        % (i + 1, t, sent, ann_e))
            notes.append("burst %d at %s %d/%d superseded, VCU page 00 at %s"
                         % (i + 1, t, sent, ann_e, hits[0]))
            continue
        if why.startswith("replaced"):
            # SPEC 4.1, 2026-10-05: "A repeat that starts while another is
            # still being sent replaces it: the earlier one stops there,
            # the frames it already sent stand, and only the new command
            # continues."
            #
            # The replacement is the evidence, and it has to be IN THIS
            # BURST'S WINDOW. A REPLACED end with no later REPEAT, or one
            # whose next REPEAT starts after this burst could still have
            # been running, is a burst that stopped for some other reason
            # wearing this label -- and the core's own reason never
            # excuses a count on its own authority. Same rule as
            # SUPERSEDED, different witness: there the vehicle log is the
            # witness, here the next REPEAT line is.
            if i + 1 >= len(starts):
                return (False, "burst %d at %s reports REPLACED at %d of %d, "
                               "but no later REPEAT exists -- nothing "
                               "replaced it, so its frames are unaccounted "
                               "for" % (i + 1, t, sent, ann_e))
            t_next = starts[i + 1][0]
            w0, w1 = _burst_window(t)
            if not (w0 <= _secs(t_next) <= w1):
                return (False, "burst %d at %s reports REPLACED at %d of %d, "
                               "but the next REPEAT starts at %s, outside "
                               "its window %d..%d s -- this burst had "
                               "already finished or been abandoned, so "
                               "REPLACED is the wrong reason"
                        % (i + 1, t, sent, ann_e, t_next, w0, w1))
            # The hook only fires with frames still owed, so a REPLACED
            # that claims it sent everything it announced contradicts the
            # code that writes it.
            if sent == ann_e:
                return (False, "burst %d at %s reports REPLACED at %d of %d "
                               "-- a burst with nothing left to send cannot "
                               "be replaced; it would have reported COMPLETE"
                        % (i + 1, t, sent, ann_e))
            notes.append("burst %d at %s %d/%d replaced, next REPEAT at %s"
                         % (i + 1, t, sent, ann_e, t_next))
            continue
        if why.startswith("abandoned"):
            # SPEC 4.1, 2026-10-07: a repeat ends early on "a session
            # boundary (6.1), which resets any repeat", and "a repeat that
            # sends fewer than 20 must be explained" by it. The witness is
            # the boundary in the sims' own logs, inside this burst's
            # window -- never the core's label (tester, 2026-10-08; until
            # the spec text landed, every ABANDONED end failed).
            hits = _boundary_in_burst(veh_text, chg_text, t)
            if not hits:
                return (False, "burst %d at %s reports ABANDONED at %d of "
                               "%d, but neither log shows a spec 6.1 "
                               "session boundary inside its window -- the "
                               "core's own reason does not get to excuse it"
                        % (i + 1, t, sent, ann_e))
            if sent == ann_e:
                return (False, "burst %d at %s reports ABANDONED at %d of "
                               "%d -- a burst with nothing left to send "
                               "cannot be abandoned; it would have reported "
                               "COMPLETE" % (i + 1, t, sent, ann_e))
            notes.append("burst %d at %s %d/%d abandoned, boundary %s at %s"
                         % (i + 1, t, sent, ann_e, hits[0][1], hits[0][0]))
            continue
        # Any other reason -- a code the harness does not know -- FAILS
        # (tester, 2026-10-07). Rev 1's comment said it was "reported rather
        # than waved through" and then returned True.
        return (False, "burst %d at %s ended %d of %d '%s' -- a reason the "
                       "spec does not account for, and nothing here "
                       "witnesses it" % (i + 1, t, sent, ann_e, why))

    if synth is not None and total != synth:
        return (False, "the per-burst counts sum to %d but the core's final "
                       "synth is %d -- a run whose own two numbers disagree "
                       "is not a pass" % (total, synth))
    return (True, "per-burst: " + "; ".join(notes))


def _repeat_bursts(intp_text, veh_text, frames=None, chg_text=""):
    """-> (ok, why). Spec 4.1 repeats, and what may legitimately cut one short.

    Two halves, because either alone passes while the other is broken: every
    REPEAT event must announce `frames`, and the `synth` counter must account
    for them. A core that logged 20 and sent 5 fails the second.

    A SHORT BURST IS LEGITIMATE WHEN THE VCU SUPERSEDED IT. Spec 4.1: "A
    page-00 frame from the VCU during a repeat ends the repeat; the VCU's
    frame is the truth." Any achieved count therefore passes when the vehicle
    log shows the VCU changing page 00 inside the burst window -- and a short
    burst with NO such frame still fails, which is what this check is for.

    WHY THE TOLERANCE IS UNCONDITIONAL AND NOT "20 MINUS A FEW". A burst is
    20 frames x 50 ms = 1,000 ms (spec 4.1), and the VCU drops energy flow
    REACT_HOLD_S = 1.0 s after the charger leaves state 12. They are a dead
    heat by construction, so the achieved count is scheduling jitter, not a
    property of the core. Measured on the same code: `fault-during-override`
    sent 13 at 1x, 19 at 5x and 7 on the board, while `restart-after-standby`
    won the identical race and sent all 20. Any threshold inside that range
    would be a coin toss dressed up as a limit -- and rev 1, which demanded
    exactly 20, was a check that could only FAIL on this scenario.

    THE WITNESS IS THE VEHICLE LOG, not the core's own REPEAT line. Using the
    core's account of why it stopped to excuse the core's own short burst is
    partly self-certifying; the VCU's close-out is recorded at the source
    that caused it.

    The achieved count stays in the why-string either way, so a drift from 13
    to 3 is visible in the output even while both legitimately pass.
    """
    if frames is None:
        frames = BURST_FRAMES
    counts = [int(n) for n in REPEAT_EV.findall(intp_text)]
    if not counts:
        return (False, "no REPEAT event at all, so there is nothing here to "
                       "be right about")
    bad = [c for c in counts if c != frames]
    if bad:
        return (False, "a REPEAT announced %s frames, not %d"
                % (bad, frames))

    m = FINAL_SYNTH.search(intp_text)
    if m is None:
        return (False, "%d burst(s) announced, but the core printed no final "
                       "synth counter, so what it actually sent is unknown "
                       "-- which is not the same as having sent it"
                % len(counts))
    synth = int(m.group(1))
    want = frames * len(counts)

    # PREFER PER-BURST. The aggregate below cannot attribute a shortfall to
    # a particular burst, so it passes logs that are equally consistent with
    # a burst dropped for no reason at all. Only a log with NO end lines --
    # recorded before the two cores began reporting them -- is scored in
    # aggregate, and it says so in the why-string.
    ends = _burst_ends(intp_text)
    if ends is not None:
        starts = REPEAT_EV_T.findall(intp_text)
        if len(starts) == len(counts):
            return _score_per_burst(starts, ends, synth, veh_text, frames,
                                    chg_text)
        return (False, "%d end line(s) present but only %d of %d REPEAT "
                       "lines carry a wall-clock stamp, so no burst can be "
                       "placed in time"
                % (len(ends), len(starts), len(counts)))

    if synth == want:
        return (True, "aggregate (no end lines): %d burst(s) %s, synth %d, every one complete"
                % (len(counts), counts, synth))
    if synth > want:
        return (False, "synth %d exceeds the %d announced across %d burst(s) "
                       "-- more frames went out than any REPEAT claimed"
                % (synth, want, len(counts)))

    # Short. Spec 4.1 permits exactly one cause, so find it or fail.
    starts = REPEAT_EV_T.findall(intp_text)
    if len(starts) != len(counts):
        return (False, "synth %d is short of %d, and %d of %d REPEAT lines "
                       "carry no wall-clock stamp, so the cause cannot be "
                       "established from these logs. A short burst whose "
                       "cause cannot be shown is a failure, not a pass"
                % (synth, want, len(counts) - len(starts), len(counts)))

    superseded = []
    for t, _n in starts:
        hits = _page00_in_burst(veh_text, t)
        if hits:
            superseded.append((t, hits[0]))
    if not superseded:
        return (False, "synth %d of %d announced, and the VCU changed page 00 "
                       "inside NONE of the %d burst window(s) -- nothing "
                       "superseded the repeat, so frames the core announced "
                       "were never sent"
                % (synth, want, len(counts)))

    floor = want - frames * len(superseded)
    if synth < floor:
        return (False, "synth %d is below %d, the least the %d burst(s) with "
                       "no superseding frame could have sent between them, "
                       "so the shortfall is larger than spec 4.1 accounts for"
                % (synth, floor, len(counts) - len(superseded)))
    return (True, "aggregate (no end lines): %d burst(s) %s, synth %d of %d; %d cut short by the VCU's "
                  "own page 00 (%s), which spec 4.1 makes the truth"
            % (len(counts), counts, synth, want, len(superseded),
               ", ".join("burst at %s superseded at %s" % s
                         for s in superseded)))


def _diag_frames_ok(diag, fstate):
    """-> (ok, why). Every frame carries the schema and firmware version the
    harness was built against, and the last 0x7F4 agrees with the final
    state. A wrong version is the one error a decoder cannot otherwise see:
    the fields are all in range and all mean something else."""
    if not diag:
        return (False, "none seen on the vehicle segment")
    by_id = {}
    for _t, fid, body in diag:
        by_id.setdefault(fid, []).append(body)
    missing = [i for i in (0x7F4, 0x7F5, 0x7F6, 0x7F7) if i not in by_id]
    if missing:
        return (False, "missing %s" % ", ".join("0x%03X" % i for i in missing))

    for body in by_id[0x7F4]:
        if body[0] != SPEC_SCHEMA_VER:
            return (False, "0x7F4 schema %d, spec %d"
                    % (body[0], SPEC_SCHEMA_VER))
        if body[1] != SPEC_FW_VER:
            return (False, "0x7F4 firmware %d, spec %d"
                    % (body[1], SPEC_FW_VER))
    # Reading 15 (tracker F): the version in EVERY decoded 0x7F7, not the
    # last one only.
    for b in by_id[0x7F7]:
        fw16 = b[4] | (b[5] << 8)
        if fw16 != SPEC_FW_VER or b[6] != SPEC_SCHEMA_VER:
            return (False, "0x7F7 reports fw %d schema %d, spec fw %d "
                           "schema %d" % (fw16, b[6], SPEC_FW_VER,
                                          SPEC_SCHEMA_VER))

    last_state = DIAG_STATES.get(by_id[0x7F4][-1][2], "?%d"
                                 % by_id[0x7F4][-1][2])
    if last_state != fstate:
        return (False, "last 0x7F4 says %s, the run ended %s"
                % (last_state, fstate))
    return (True, "%d frames, fw %d schema %d, last 0x7F4 %s"
            % (len(diag), SPEC_FW_VER, SPEC_SCHEMA_VER, last_state))


def _no_rewrite_after_terminated(diag):
    """-> (ok, why). Spec 5.2: `modified` (0x7F6 B0-B3) stops moving once
    0x7F4 reports TERMINATED."""
    t_term = None
    for t, fid, body in diag:
        if fid == 0x7F4 and DIAG_STATES.get(body[2]) == "TERMINATED":
            t_term = t
            break
    if t_term is None:
        return (False, "the core never reported TERMINATED")
    mods = [int.from_bytes(body[0:4], "little")
            for t, fid, body in diag if fid == 0x7F6 and t >= t_term]
    if len(mods) < 2:
        return (False, "only %d counter frame(s) after TERMINATED at t=%.0fs "
                       "-- not enough to show the count stopped"
                % (len(mods), t_term))
    if len(set(mods)) != 1:
        return (False, "modified moved after TERMINATED: %s"
                % sorted(set(mods)))
    return (True, "%d counter frames after TERMINATED at t=%.0fs, all "
                  "modified=%d" % (len(mods), t_term, mods[0]))


def check_invariants(trace_path):
    """-> (ok, why). test_invariants.py's rules over THIS scenario's trace.

    Added by the tester, 2026-10-07. The trace is every frame and tick the
    core saw in the run; machine.py is pure, so replaying it reproduces what
    the core emitted. Over it, frame by frame and from the bus rather than
    from the core's own beliefs: every frame forwarded once and unmodified
    outside an override; page 01 during an override equal to the BMS's
    permission capped by the EVSE (spec 4) -- which is what the 16 A
    scenario's delivered-current check could never see; every repeat the
    VCU's standing page 00 in the 4.1 direction, 20 frames unless cut; and
    the 8.1 mirrors. Must run BEFORE check_flight_core(), which deletes a
    trace that diffs clean.
    """
    if not trace_path.exists():
        return (False, "no trace to check -- not the same as passing")
    sys.path.insert(0, str(HERE))
    import test_invariants as TI
    c = TI.replay_trace(str(trace_path))
    probs = c.finish()
    if probs:
        return (False, "%d problem(s): %s" % (len(probs),
                                             "; ".join(probs[:4])))
    return (True, "%d steps, %d override page-01 frames judged, %d repeat "
                  "frames in %d burst(s), %d mirrors"
            % (c.n_steps, c.n_page01_checked, c.n_repeat_frames,
               len(c.bursts), c.n_mirrors))


def check_flight_core(trace_path):
    """-> (ok, why). Replay the recorded trace through machine.cpp.

    `diff_one.sh` builds the runner (cached) and diffs both sides, which it
    generates now -- there is no golden on disk, because a recording is not
    reproducible between runs and a stored one would be stale.

    The trace is removed after a clean diff and KEPT when it diverged.
    """
    script = (HERE / "firmware" / "test" / "host_diff" / "diff_one.sh")
    if not trace_path.exists():
        return (False, "interposer_sim wrote no trace -- the L1 replay did "
                       "not happen, which is not the same as passing")
    size_mb = trace_path.stat().st_size / (1024.0 * 1024.0)
    if os.name == "nt":
        cmd = [_wsl_exe(), "bash", _wsl_path(script), _wsl_path(trace_path)]
    else:
        cmd = ["bash", str(script), str(trace_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except Exception as exc:                            # noqa: BLE001
        return (False, "could not run the L1 replay (%s). A replay that "
                       "cannot run is a failed check, not a skipped one; "
                       "pass --no-l1 to say so deliberately." % exc)
    if r.returncode == 0:
        try:
            trace_path.unlink()
        except OSError:
            pass
        return (True, "%.0f MB replayed through machine.cpp, identical to "
                      "machine.py (trace deleted)" % size_mb)
    if r.returncode == 2:
        return (False, "the L1 replay could not be built or run: %s"
                % (r.stderr.strip() or r.stdout.strip())[:300])
    return (False, "machine.cpp DIVERGES from machine.py on this scenario. "
                   "Trace kept at %s\n      %s"
            % (trace_path, (r.stdout.strip() or "")[:400]))


def _wsl_exe():
    r"""Where wsl.exe actually is, from THIS interpreter.

    A 32-bit process asking for C:\Windows\System32 is redirected by WOW64
    to SysWOW64, which has no wsl.exe. A bare "wsl" therefore fails with
    WinError 2 -- "the system cannot find the file specified" -- which
    reads like WSL not being installed when it is. Sysnative is the alias
    that bypasses the redirector; it exists only for 32-bit processes,
    hence the fallback.

    NICKY-XPS moved to a 64-bit `py -3.14` on 2026-10-04
    (python-executables-reference.md), so the Sysnative branch no longer
    fires there -- but `py -3.11` is still installed and still 32-bit, so
    the fallback stays. Both literals are raw: "C:\Windows" contains the
    invalid escape \W, which Python 3.13 warns about and a later version
    will reject.
    """
    for cand in (os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "Sysnative", "wsl.exe"),
                 os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "wsl.exe")):
        if os.path.exists(cand):
            return cand
    return "wsl"


def _wsl_path(p):
    """C:\\x\\y -> /mnt/c/x/y, for calling into WSL from Windows."""
    sp = str(p).replace("\\", "/")
    if len(sp) > 1 and sp[1] == ":":
        sp = "/mnt/" + sp[0].lower() + sp[2:]
    return sp


def evaluate(sc, veh_log, chg_log, int_log, timed_out, l1=None, inv=None):
    veh = veh_log.read_text(errors="replace")
    chg = chg_log.read_text(errors="replace")
    intp = int_log.read_text(errors="replace")
    diag = parse_diag_frames(veh)

    rows = VEH_ROW.findall(veh)
    soc_end = float(rows[-1][1]) if rows else None
    # The peaks come from the sim's own per-step tracking, not from the
    # status rows. A maximum over rows is a sampling artefact: at the old
    # 1200 s cadence a 764 s session had ONE row, at t=0, so every one of
    # these assertions was comparing against the starting value and could
    # not fail. `evap-bypass` reported "SoC stayed <= 82.5 % (peak 79.0)"
    # on a run that reached 80.3 %.
    pm = VEH_PEAKS.search(veh)
    peak_n = int(pm.group(1)) if pm else 0
    cur_max = float(pm.group(2)) if pm else None
    vmax_max = float(pm.group(3)) if pm else None
    soc_max = float(pm.group(4)) if pm else None
    m = re.search(r"final state=(\w+)", intp)
    fstate = m.group(1) if m else None
    # The release, from the core's own event: vmax and vmin at the instant
    # it let go (spec 9, C2). VEH_STOP -- the vehicle log's flow-drop line --
    # used to be the source for these, but that is the END OF THE SESSION,
    # minutes of hold after the release, with the pack relaxing throughout.
    rel_vmax, rel_vmin = release_vmax_vmin(intp)

    results = []
    for kind, val in sc.expect:
        if kind == "intp_event":
            ok = re.search(val, intp) is not None
            results.append((ok, "event matches /%s/" % val))
        elif kind == "intp_event_after":
            first, second = val
            mf = re.search(first, intp)
            ms = None
            if mf is not None:
                ms = re.search(second, intp[mf.end():])
            ok = ms is not None
            results.append((ok, "/%s/ appears after /%s/%s"
                            % (second, first,
                               "" if mf is not None
                               else " -- the first never matched")))
        elif kind == "intp_no_event":
            ok = re.search(val, intp) is None
            results.append((ok, "no event matches /%s/" % val))
        elif kind == "veh_event":
            ok = re.search(val, veh) is not None
            results.append((ok, "vehicle log matches /%s/" % val))
        elif kind == "intp_final_state":
            ok = fstate == val
            results.append((ok, "final state == %s (was %s)" % (val, fstate)))
        elif kind in ("veh_soc_min", "veh_soc_max", "veh_vmax_max",
                      "veh_current_max"):
            if pm is None:
                # Not "no data, so pass". The sim prints this line on every
                # exit path, so its absence means the run did not finish
                # cleanly and the number would be a guess.
                results.append((False, "%s: the vehicle sim printed no "
                                "`peaks` line, so there is nothing to "
                                "assert on" % kind))
            elif kind == "veh_soc_min":
                ok = soc_max >= val
                results.append((ok, "SoC reached >= %.1f%% (peak %.1f over "
                                "%d steps)" % (val, soc_max, peak_n)))
            elif kind == "veh_soc_max":
                ok = soc_max <= val
                results.append((ok, "SoC stayed <= %.1f%% (peak %.1f over "
                                "%d steps)" % (val, soc_max, peak_n)))
            elif kind == "veh_vmax_max":
                ok = vmax_max <= val
                results.append((ok, "vmax stayed <= %.3f V (peak %.3f over "
                                "%d steps)" % (val, vmax_max, peak_n)))
            else:
                ok = cur_max <= val + 0.05
                results.append((ok, "charge current stayed <= %.1f A (peak "
                                "%.2f over %d steps)"
                                % (val, cur_max, peak_n)))
        elif kind == "intp_spread_at_release_min":
            spread = (None if rel_vmax is None
                      else float(rel_vmax - rel_vmin))
            ok = spread is not None and spread >= val
            results.append((ok, "spread AT THE RELEASE >= %.0f mV (%s)"
                            % (val, "no release event" if spread is None
                               else "%.0f mV, vmax %d vmin %d"
                               % (spread, rel_vmax, rel_vmin))))
        elif kind == "intp_vmin_at_release_max":
            v = None if rel_vmin is None else rel_vmin / 1000.0
            ok = v is not None and v <= val
            results.append((ok, "vmin AT THE RELEASE <= %.3f V (%s)"
                            % (val, "no release event" if v is None
                               else "%.3f V" % v)))
        elif kind == "chg_event":
            ok = re.search(val, chg) is not None
            results.append((ok, "charger log matches /%s/" % val))
        elif kind == "chg_released_into_hold":
            ok, why = _released_into_hold(chg, intp, val[0], val[1])
            results.append((ok, "the release landed in the VCU's hold: %s"
                            % why))
        elif kind == "intp_repeat_20":
            ok, why = _repeat_bursts(intp, veh, chg_text=chg)
            results.append((ok, "spec 4.1 repeats: %s" % why))
        elif kind == "diag_frames_ok":
            ok, why = _diag_frames_ok(diag, fstate)
            results.append((ok, "diag frames: %s" % why))
        elif kind == "diag_no_rewrite_after_release":
            ok, why = _no_rewrite_after_terminated(diag)
            results.append((ok, "no frame rewritten after the release: %s"
                            % why))

        else:
            # A kind with no branch used to append nothing, so the check
            # simply disappeared and the scenario still passed. Two branches
            # were replaced in this batch; without this, the scenarios still
            # asking for the old ones would have gone quietly unchecked.
            results.append((False, "UNKNOWN expectation %r -- the harness has "
                            "no branch for it, so nothing was checked" % kind))

    if l1 is not None:
        results.append((l1[0], "spec 9.1 L1, the flight core: %s" % l1[1]))
    if inv is not None:
        results.append((inv[0], "spec 2/4/4.1/8.1 invariants over the trace: "
                        "%s" % inv[1]))

    if "Traceback" in veh or "Traceback" in intp or "Traceback" in chg:
        results.append((False, "no simulator crashed"))
    if timed_out:
        results.append((False, "vehicle_sim finished within its timeout"))

    return dict(name=sc.name, results=results, soc_end=soc_end, soc_max=soc_max,
                vmax_max=vmax_max, final_state=fstate,
                events=[l for l in intp.splitlines() if "] " in l and "intp [" in l],
                logs=(veh_log, int_log))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="*", help="scenario name(s)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--time-scale", type=float, default=5.0,
                    help="simulated seconds per wall second (default 5: 100 ms "
                         "of wall clock per 500 ms staleness window, which "
                         "absorbs this machine's 40-90 ms process freezes -- "
                         "spec 9). 10 is marginal here, 20 trips. FORCED TO "
                         "1.0 by --external-interposer -- see its help.")

    # --- real hardware in the middle ---------------------------------------
    ap.add_argument("--external-interposer", action="store_true",
                    help="do not spawn interposer_sim.py; the real board is "
                         "wired between the two segments instead. Implies "
                         "--time-scale 1.0, because the board runs on its own "
                         "millis() and cannot be sped up: at any other scale "
                         "its arm delay, debounces, staleness windows and "
                         "burst period all disagree with the sims' clock, and "
                         "the failures look like logic bugs.")
    ap.add_argument("--serial-port", default=None,
                    help="board's serial port (e.g. COM7, /dev/ttyACM0). Its "
                         "output is captured to <scenario>.interposer.log so "
                         "the existing event expectations evaluate unchanged.")
    ap.add_argument("--serial-baud", type=int, default=115200)
    ap.add_argument("--no-l1", action="store_true",
                    help="skip the spec 9.1 L1 replay of the scenario "
                         "through machine.cpp. The replay needs a C++ "
                         "compiler (WSL on Windows); without this flag a "
                         "machine that cannot run it FAILS the scenario "
                         "rather than quietly passing it.")
    ap.add_argument("--timeout-scale", type=float, default=None,
                    help="multiply every scenario timeout by this. Each "
                         "scenario's timeout was written against the default "
                         "--time-scale 20, so running at 1.0 needs roughly 20x "
                         "the wall time; --external-interposer therefore "
                         "defaults this to 20.0. Set it explicitly to cut a "
                         "run short or to give a slow scenario more room.")
    for side in ("vehicle", "charger"):
        ap.add_argument("--%s-transport" % side, default=None,
                        help="dongle brand for the %s segment (kvaser, "
                             "gs_usb, ixxat, pcan, socketcan). Omit for the "
                             "virtual transport." % side)
        ap.add_argument("--%s-channel" % side, default=None,
                        help="dongle channel for the %s segment; "
                             "auto-detected if omitted" % side)
    ap.add_argument("--vehicle-port", type=int, default=43213)
    ap.add_argument("--charger-port", type=int, default=43214)
    ap.add_argument("--phys-port", type=int, default=43215,
                    help="UDP port for the HV physics link (see bus.py)")
    ap.add_argument("--outdir", default=None,
                    help="default: interposer-runs/ under VTRUX_DATA "
                         "(paths.py)")
    args = ap.parse_args()

    if args.external_interposer and args.time_scale != 1.0:
        # Not a warning. The board's clock is real, so a scaled run silently
        # produces wrong answers rather than failing: at scale 20 the sims
        # reach the evap ceiling in 4.5 s of wall time while the board is still
        # 55 s short of its 60 s arm delay, so it correctly refuses to override
        # and the scenario "fails" for a reason that has nothing to do with the
        # firmware.
        print("--external-interposer: forcing --time-scale 1.0 (was %g); "
              "the board cannot be sped up." % args.time_scale)
        args.time_scale = 1.0

    if args.timeout_scale is None:
        # Scenario timeouts were all written against the default --time-scale
        # 20. At 1.0 the same charge takes about twenty times as long in wall
        # clock, so the unscaled timeouts kill every hardware run partway
        # through -- evap-override reaches only ~81 % SoC in its 600 s, and
        # then fails on soc_at_least(99.5) for a reason that has nothing to do
        # with the board.
        # Timeouts were written against 20x; scale them to whatever this run
        # uses (the board runs at 1x, so 20x the budget).
        args.timeout_scale = 20.0 / args.time_scale
    budget_ms = _STALE_MS / args.time_scale
    print("--time-scale %g: %.1f ms of wall clock per %d ms staleness window%s"
          % (args.time_scale, budget_ms, _STALE_MS,
             "" if budget_ms >= TIME_SCALE_WARN_MS else
             " -- TIGHT. Expect spurious 'frames stale' trips if anything else "
             "is running on this machine; %g or below keeps it above %.0f ms."
             % (_STALE_MS / TIME_SCALE_WARN_MS, TIME_SCALE_WARN_MS)))

    if args.timeout_scale != 1.0:
        print("scenario timeouts scaled by %gx (wall-clock budget, not "
              "simulated time)" % args.timeout_scale)

    if args.list:
        for s in SCENARIOS:
            print("%-24s %s" % (s.name, s.why))
        return 0

    names = args.scenario or ([s.name for s in SCENARIOS] if args.all else [])
    if not names:
        ap.error("give a scenario name, --all, or --list")
    for n in names:
        if n not in BY_NAME:
            ap.error("unknown scenario %r (see --list)" % n)

    if args.outdir is None:
        import paths
        args.outdir = paths.runs_dir()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    allok = True
    for n in names:
        sc = BY_NAME[n]
        print("\n=== %s ===" % n)
        print(sc.why)
        t0 = time.time()
        res = run_one(sc, args, outdir)
        for line in res["events"]:
            print("   " + line.split("intp ", 1)[-1])
        ok = all(r[0] for r in res["results"])
        for good, text in res["results"]:
            print("   [%s] %s" % ("PASS" if good else "FAIL", text))
        print("   -> %s  (%.0f s wall, logs in %s)"
              % ("PASS" if ok else "FAIL", time.time() - t0, outdir))
        allok = allok and ok

    print("\n%s" % ("ALL SCENARIOS PASSED" if allok else "SOME SCENARIOS FAILED"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
