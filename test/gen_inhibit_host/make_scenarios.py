#!/usr/bin/env python3
"""Generate the synthetic scenario files the host runner replays.

These are SYNTHETIC. They exist for the states the real corpus does not
contain -- a failed transmit, an error-frame storm, an inverter that drops off
a live bus -- and for the three bugs found by hand on 2026-09-19, which are
what this harness exists to keep fixed:

  bug 1  the arm gate required fresh 0x471/0x054, so the inhibit would have
         gone live ~28 s after key-on or never       -> keyon-gene-late
  bug 2  the 0x471 trip fired without ever having seen 0x471, so it aborted
         instantly on every normal key-on            -> keyon-normal
  bug 3  inhibit_live survived an ordinary disarm    -> disarm-clears-live

A scenario is a plain text file of directives; see host_runner.c for the
grammar. Frame rates here are chosen to keep traces small and are NOT claims
about the truck -- the real rates come from replaying real captures, which is
what from_capture.py is for.

Nothing here asserts. The runner prints a trace, run_tests.py diffs it against
a golden. What each scenario is meant to demonstrate is in its header comment,
which is carried into the scenario file so a failing diff explains itself.
"""

import argparse
import os

US = 1
MS = 1000
S = 1000000


# --------------------------------------------------------------- frames --

def _f(t, ident, data, dlc=None):
    if dlc is None:
        dlc = len(data)
    hexs = "".join("%02X" % b for b in data)
    return "f %d %03X %d %s" % (t, ident, dlc, hexs)


def cmd(t, ctr, b0=0x08, torque=0, rpm_ref=-1):
    """0x051 VCM generator command, DLC 6.

    B1-B2 gen_torque_cmd LE offset -32768; B3-B4 gen_rpm_ref same;
    B5 low nibble is the rolling counter.
    """
    tq = torque + 32768
    rr = rpm_ref + 32768
    return _f(t, 0x051, [b0, tq & 0xFF, (tq >> 8) & 0xFF,
                         rr & 0xFF, (rr >> 8) & 0xFF, ctr & 0x0F])


def gene(t, rpm):
    """0x054 GENE_RotSpd, LE, zero at 32767 per the DBC (not 32768)."""
    v = rpm + 32767
    return _f(t, 0x054, [v & 0xFF, (v >> 8) & 0xFF, 0, 0, 0, 0, 0, 0])


def fb(t):
    """0x471 inverter feedback. Liveness only; the payload is not read."""
    return _f(t, 0x471, [0] * 8)


def soc(t, raw):
    """0x411 BMS_SoC_HiRes: 14-bit big-endian at bit 7, scale 0.01%."""
    return _f(t, 0x411, [(raw >> 6) & 0xFF, (raw & 0x3F) << 2, 0, 0, 0, 0, 0, 0])


def contactor(t, stat=11):
    """0x440 EPRI_BCM_Data2_0440; bcm_mainc_stat is 4 bits at bit 2 of B0.

    Spec 6.2 / 7 condition 2 (review A1). 11 = MAIN_PN_CLOSED_DRIVE and
    12 = MAIN_P_CLOSED_CHARGE are the two closed states; the names and the
    layout are from epri-pt-bus.dbc and were cross-checked against cantools
    over 15,188 real frames. The close sequence the truck actually walks is
    0 -> 2 -> 5 -> 7 -> 8 -> 9 -> 11, and 13 -> 14 on the way down.
    """
    d = [0] * 8
    d[0] = (stat & 0x0F) << 2
    return _f(t, 0x440, d)


def contactor_train(t0, t1, stat=11, period=50 * MS):
    """0x440 at its measured ~20 Hz (16.85-20.0 Hz on four clean captures)."""
    return periodic(t0, t1, period, lambda t: contactor(t, stat))


def shift(t, pos):
    """0x639 shift_lever_pos in B6 bits 6:4. 4 = Manual_generator_mode."""
    d = [0] * 8
    d[6] = (pos & 0x07) << 4
    return _f(t, 0x639, d)


def fault(t, val):
    """0x617 B7: 0xCA = VCM fault active, 0xC8 = clear."""
    d = [0] * 8
    d[7] = val
    return _f(t, 0x617, d)


def key(t, on):
    """0x592 EPRI_HCU_Sensor_0592; IgnitionKeyState is B0 bit 4.

    The DBC says `SG_ IgnitionKeyState : 4|1@1+` -- Intel start bit 4, width
    1, so B0 & 0x10. Spec 7.1. The other bits of B0 are EVSESwitchState and
    ExportPowerState and are left zero; nothing in the core reads them.
    """
    d = [0] * 8
    d[0] = 0x10 if on else 0x00
    return _f(t, 0x592, d)


def key_train(t0, t1, on=True, period=100 * MS):
    return periodic(t0, t1, period, lambda t: key(t, on))


def cmd_train(t0, t1, period, ctr0=0, **kw):
    """A run of 0x051 at a fixed period, counter incrementing mod 16."""
    out = []
    ctr = ctr0
    t = t0
    while t < t1:
        out.append(cmd(t, ctr, **kw))
        ctr = (ctr + 1) & 0x0F
        t += period
    return out


def periodic(t0, t1, period, fn, phase=0):
    out = []
    t = t0 + phase
    while t < t1:
        out.append(fn(t))
        t += period
    return out


# ------------------------------------------------------------ scenarios --

SCEN = {}


def scenario(name, why, autokey=True, autobms=True):
    """Register a scenario.

    autokey=True (the default) injects a 10 Hz 0x592 key-ON train spanning the
    whole scenario, and records that it did in the generated file's header.

    WHY IT IS A DEFAULT RATHER THAN WRITTEN OUT 25 TIMES. Spec 7.1 gates
    transmission on the key reading on, and "never seen" reads off -- so
    without a key train every scenario written before 2026-09-20 would
    transmit nothing and would be testing the key gate instead of whatever it
    was written for. A key-on train is also what the truck actually presents:
    0x592 was found in 1045 of 1045 powertrain epochs across the corpus.

    Scenarios that are ABOUT the key pass autokey=False and build their own.

    autobms=True (the default) does the same job for the interlock signals that
    gate going live: 0x440 closed, 0x411 healthy, 0x617 no-fault and 0x639,
    each skipped if the scenario sends that ID itself (see add_autobms()).

    WHY THIS IS ALSO A DEFAULT, and it is the same argument as autokey. Since
    reviews A1 and B3 the arm gate will not let the inhibit go live until the
    main contactors have reported closed with an 0x411 since (condition 2) and
    0x617 and 0x639 are fresh (conditions 3 and 4) -- so a scenario missing any
    of them never transmits, and would be testing the new gate conditions
    instead of whatever it was written for. The truck presents the
    contactors closed for the whole of any drive in which the engine could be
    started, because the engine is cranked by the HV inverter and that cannot
    run with them open.

    Each train is withheld from a scenario that sends that ID itself, because
    low-soc-debounce counts CONSECUTIVE sub-threshold readings and a competing
    healthy train would reset the count every 50 ms so the release could never
    latch -- and vcm-fault would never see 0xCA, and m-mode-latch never see
    position 4.

    Scenarios about the bus going away pass autobms=False and build their own,
    for the reason _frame_span() documents: background frames arriving during
    a deliberate silence re-time every trailing abort.
    """
    def deco(fn):
        SCEN[name] = (why, fn, autokey, autobms)
        return fn
    return deco


@scenario("keyon-normal", """
Ordinary key-on with an auto-arm build. The bus is silent, the device arms
INHIBIT with nothing on the wire, then the VCM starts talking.

EXPECT: blocked on "no fresh 0x051" while silent; goes live within a frame or
two of the bus waking; transmits a held 0x08 zero-torque 0x051 per received
frame; NEVER aborts. Pins bug 2 -- the 0x471 trip must not fire on a bus whose
inverter has not powered up, because never-seen is not the same as stopped.
""")
def s_keyon_normal():
    L = ["mode 0 3 500"]              # auto-arm INHIBIT at t=0, bus silent
    L += cmd_train(3 * S, 8 * S, 20 * MS)
    L += ["end %d" % (9 * S)]
    return L


@scenario("keyon-gene-late", """
Key-on where the GENE family lags the VCM by 28 s, which is what the corpus
actually shows (0x051 at +0.17 s, 0x471/0x054 at +28 s or never).

EXPECT: live within a frame or two of 0x051 starting -- NOT 28 s later. Pins
bug 1. Once 0x471 does appear, fb_ever latches and the inverter-lost trip
becomes armed for the rest of the run; the trace must show that transition and
still no abort while 0x471 keeps arriving.
""")
def s_keyon_gene_late():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 40 * S, 50 * MS)
    L += periodic(29 * S, 40 * S, 100 * MS, fb)
    L += periodic(29 * S, 40 * S, 100 * MS, lambda t: gene(t, 0))
    L += ["end %d" % (41 * S)]
    return sorted_directives(L)


@scenario("disarm-clears-live", """
Arm, go live, then an ordinary disarm (mode 0) -- not an abort.

EXPECT: inhibit_live returns to 0 and arm_block becomes "not armed" the moment
the worker next reaches the OFF branch. Pins bug 3: leaving the flag set made
diag_flags bit5 and the JSON claim a live inhibit on a disarmed device, which
is exactly the reassuring-but-wrong reading those flags exist to prevent.
""")
def s_disarm_clears_live():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS)
    L += ["mode %d 0 500" % (3 * S)]
    L += cmd_train(3 * S + 100 * MS, 4 * S, 20 * MS)   # dropped while OFF
    L += ["end %d" % (5 * S)]
    return sorted_directives(L)


@scenario("b0-held-not-mirrored", """
The VCM asks for a start: B0 goes 0x08 -> 0x0B with nonzero torque.

EXPECT: every transmitted frame keeps B0 = 0x08 and B1-B2 = 00 80. Spec 4.1,
changed 2026-09-19 -- mirroring 0x0B made the inverter draw ~10x its
engine-off power for the whole inhibit. B3-B4 still mirror gen_rpm_ref and B5
is still the stolen counter+1, so the frame stays byte-identical to the VCM's
next genuine frame whenever the VCM is also in 0x08.
""")
def s_b0_held():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 2 * S, 20 * MS)
    # VCM requests a start: B0=0x0B, torque nonzero, rpm_ref positive.
    L += cmd_train(2 * S, 3 * S, 20 * MS, ctr0=0, b0=0x0B,
                   torque=400, rpm_ref=1200)
    L += ["end %d" % (4 * S)]
    return sorted_directives(L)


@scenario("shutdown-suppress", """
The VCM parks the generator: 0x051 B0 = 0x10 for a spell, then back to 0x08.

EXPECT: transmission stops for the whole 0x10 episode and resumes by itself
afterwards with no clearing step. NOT latched, deliberately -- 0x10 is a state
the truck passes through and drives out of again (40,697 frames of it, 100%
with the shifter in Park, up to four episodes in one capture), and latching
would stand the inhibitor down for the drive it exists for.
""")
def s_shutdown_suppress():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 2 * S, 20 * MS)
    L += cmd_train(2 * S, 4 * S, 20 * MS, b0=0x10)
    L += cmd_train(4 * S, 5 * S, 20 * MS)
    L += ["end %d" % (6 * S)]
    return sorted_directives(L)


@scenario("shutdown-at-keyon", """
0x10 is the normal state at key-on, so the device arms straight into it.

EXPECT: the gate blocks on "VCM commanding 0x10" rather than going live and
sitting silent, and it clears by itself when the VCM leaves 0x10. This is why
the suppression needs no startup guard.
""")
def s_shutdown_at_keyon():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS, b0=0x10)
    L += cmd_train(3 * S, 4 * S, 20 * MS)
    L += ["end %d" % (5 * S)]
    return sorted_directives(L)


@scenario("m-mode-latch", """
Driver engages M mode (0x639 shift_lever_pos == 4) mid-run, then leaves it.

EXPECT: disable latches immediately and NEVER clears -- no transmission for
the rest of the run even after the shifter moves away from 4. Cleared only by
a reboot, which the relay dropping at truck sleep provides.
""")
def s_m_mode_latch():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += periodic(1 * S, 3 * S, 200 * MS, lambda t: shift(t, 2))
    L += periodic(3 * S, 4 * S, 200 * MS, lambda t: shift(t, 4))
    L += periodic(4 * S, 6 * S, 200 * MS, lambda t: shift(t, 2))
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("low-soc-debounce", """
SoC crosses the 21% floor. Three separate things are being pinned.

EXPECT: (a) raw 0 is the startup sentinel and never counts as 0% -- no latch
however many arrive; (b) four consecutive sub-threshold samples do not latch;
(c) a sample above the floor resets the count, so 4-high-4 does not latch
either; (d) the fifth consecutive sub-threshold sample latches low_soc.
""")
def s_low_soc():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 12 * S, 20 * MS)
    t = 1 * S
    for _ in range(6):                      # (a) sentinel zeros
        L.append(soc(t, 0)); t += 200 * MS
    for _ in range(4):                      # (b) four low, no latch
        L.append(soc(t, 2050)); t += 200 * MS
    L.append(soc(t, 2500)); t += 200 * MS   # (c) one high resets the count
    for _ in range(4):
        L.append(soc(t, 2050)); t += 200 * MS
    L.append(soc(t, 2500)); t += 200 * MS
    for _ in range(5):                      # (d) five consecutive -> latch
        L.append(soc(t, 2050)); t += 200 * MS
    L += periodic(t, 12 * S, 200 * MS, lambda tt: soc(tt, 2050))
    L += ["end %d" % (13 * S)]
    return sorted_directives(L)


@scenario("bus-loss-latches", """
A live inhibit loses the CAN link: 0x051 stops for longer than the freshness
window, then comes back.

EXPECT: abort "bus lost -- no 0x051 (latched; CAN link down)", latched=1, and
-- the point of the scenario -- it does NOT re-arm when the frames return.

The mode STAYS 3 (changed 2026-09-20, spec 7.1). It used to drop to OFF, and
nothing is less latched for that having changed: the gate does not run while
latched=1. What it buys is that the device keeps receiving, which is what
makes a key-on able to clear the latch at all -- the worker's OFF branch does
not call twai_receive(). The key here stays ON throughout, so no transition
occurs and the latch correctly survives the frames returning.

Latching is deliberate (2026-09-19): a bus that drops and returns is
an unreliable environment, and this device steals the VCM's rolling counter
and transmits a real 0x051 onto a live powertrain bus. Resuming across a link
we already have evidence is unsound would mean doing that repeatedly, through
a gate whose freshness checks a flapping bus can satisfy.
""", autokey=False, autobms=False)
def s_bus_loss():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS)
    # 1.5 s of nothing -- three freshness windows.
    L += cmd_train(4500 * MS, 8 * S, 20 * MS)
    # The BMS goes with the bus too, for the same reason the key does: a
    # pulled connector takes 0x440 and 0x411 with 0x051. Leaving them running
    # through the silence would also put frames into it, which re-times the
    # trailing abort (see _frame_span).
    L += contactor_train(1 * S, 3 * S)
    L += contactor_train(4500 * MS, 8 * S)
    L += periodic(1 * S, 3 * S, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(4500 * MS, 8 * S, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(1 * S, 3 * S, 250 * MS, lambda t: fault(t, 0xC8))
    L += periodic(4500 * MS, 8 * S, 250 * MS, lambda t: fault(t, 0xC8))
    L += periodic(1 * S, 3 * S, 50 * MS, lambda t: shift(t, 2))
    L += periodic(4500 * MS, 8 * S, 50 * MS, lambda t: shift(t, 2))
    # The key train goes with the bus and comes back with it. The default
    # injection would have run 0x592 straight through the silence, which is a
    # bus the truck cannot produce -- 0x592 is the VCM's, and the VCM is the
    # thing that just went away. It also matters to what this scenario proves:
    # the key returns reading ON without ever having been read OFF, so no
    # transition occurs and the latch is shown surviving rather than assumed
    # to.
    L += key_train(1 * S, 3 * S, on=True)
    L += key_train(4500 * MS, 8 * S, on=True)
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("bus-glitch-short", """
The same shape as bus-loss-latches but the gap is 0.3 s -- inside the 0.5 s
freshness window for 0x051.

EXPECT, SINCE REVIEW B3 (2026-09-24): a latched abort naming 0x617, at the
moment the bus returns. This scenario used to expect no abort at all, and the
change is not a regression -- it is the arithmetic of trip 5 meeting a 4 Hz
signal, and it is worth understanding before anyone "fixes" it.

0x617 arrives every 250 ms. A whole-bus interruption therefore leaves it
absent for the glitch PLUS up to a further 250 ms, so a 0.3 s glitch produces
an absence of up to 0.55 s -- past the 0.5 s window. The bus then returns,
0x051 arrives after 0x617's window closed, the spec 7 evidence rule is
satisfied, and trip 5 fires. Here the last 0x617 before the gap is at 2.75 s
and the bus returns at 3.3 s: 0.55 s.

SO THE DEVICE'S TOLERANCE OF A BRIEF DROPOUT IS NOW SET BY THE SLOWEST
INTERLOCK, NOT BY fresh_us. A whole-bus glitch survives if it is shorter than
0.5 s minus however long ago 0x617 last spoke -- between 0.25 s and 0.5 s
depending on phase. bus-glitch-very-short pins the surviving end of that
range. Flagged to the user 2026-09-25; the rules producing it are all ones
they have already ruled on.

The key goes away with the bus and comes back with it, for the same reason as
in bus-loss-latches, and a 0.3 s absence is inside the key's own freshness
window -- so the key gate is not what stops transmission here.
""", autokey=False, autobms=False)
def s_bus_glitch():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS)
    L += cmd_train(3300 * MS, 6 * S, 20 * MS)
    L += contactor_train(1 * S, 3 * S)
    L += contactor_train(3300 * MS, 6 * S)
    L += periodic(1 * S, 3 * S, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(3300 * MS, 6 * S, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(1 * S, 3 * S, 250 * MS, lambda t: fault(t, 0xC8))
    L += periodic(3300 * MS, 6 * S, 250 * MS, lambda t: fault(t, 0xC8))
    L += periodic(1 * S, 3 * S, 50 * MS, lambda t: shift(t, 2))
    L += periodic(3300 * MS, 6 * S, 50 * MS, lambda t: shift(t, 2))
    L += key_train(1 * S, 3 * S, on=True)
    L += key_train(3300 * MS, 6 * S, on=True)
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("inverter-lost", """
0x471 has been arriving, then stops while 0x051 keeps running at full rate.

EXPECT: abort "0x471 stopped while 0x051 still live -- inverter lost". This is
the case the trip is actually for, and it is only reachable once 0x471 has
been heard -- the distinction the laptop tool could not make, and the one that
bug 2 got wrong in the other direction.
""")
def s_inverter_lost():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += periodic(1 * S, 4 * S, 100 * MS, fb)
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("rpm-debounce", """
GENE rpm crosses the 300 threshold twice: once for 0.2 s, once for 0.5 s.

EXPECT: the 0.2 s excursion does NOT trip; the 0.5 s one does, with
"engine turning while armed -- the inhibit did not hold". The debounce is not
optional -- a momentary crank blip is exactly what a WORKING inhibit produces,
and aborting on it would end the run the inhibit just won.
""")
def s_rpm_debounce():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 10 * S, 20 * MS)
    L += periodic(1 * S, 3 * S, 100 * MS, lambda t: gene(t, 0))
    L += periodic(3 * S, 3200 * MS, 50 * MS, lambda t: gene(t, 450))
    L += periodic(3200 * MS, 6 * S, 100 * MS, lambda t: gene(t, 0))
    L += periodic(6 * S, 6500 * MS, 50 * MS, lambda t: gene(t, 450))
    L += periodic(6500 * MS, 10 * S, 100 * MS, lambda t: gene(t, 0))
    L += ["end %d" % (11 * S)]
    return sorted_directives(L)


@scenario("arm-gate-generator-running", """
The device arms while the generator is already turning at 800 rpm.

EXPECT: blocked on "generator running" -- a block, not an abort, so it can
still go live later if the generator stops. Taking over a loaded generator and
commanding zero would shed the engine's whole load in one frame.
""")
def s_gate_gen_running():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += periodic(1 * S, 4 * S, 100 * MS, lambda t: gene(t, 800))
    L += periodic(4 * S, 8 * S, 100 * MS, lambda t: gene(t, 0))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("arm-gate-order-rpm-first", """
FINDING (2026-09-19, found by this harness). Pairs with
arm-gate-order-cmd-first: the two differ by ONE MICROSECOND in arrival order
and produce opposite outcomes.

Here 0x054 (800 rpm, generator running) arrives just BEFORE the first 0x051.

EXPECT, and currently observed: the gate sees a fresh 0x054 above the
threshold and blocks on "generator running". Nothing is ever transmitted.
This is the intended behaviour.
""")
def s_gate_order_rpm_first():
    L = ["mode 0 3 500"]
    L += periodic(1 * S, 4 * S, 100 * MS, lambda t: gene(t, 800))
    L += cmd_train(1 * S + 1, 4 * S, 20 * MS)
    L += ["end %d" % (5 * S)]
    return sorted_directives(L)


@scenario("arm-gate-order-cmd-first", """
FINDING (2026-09-19, found by this harness). The same situation as
arm-gate-order-rpm-first with the first two frames swapped: 0x051 arrives one
microsecond BEFORE the first 0x054.

CURRENTLY OBSERVED: the gate runs before any 0x054 has been seen, so
gi_fresh(have_rpm) is false, the generator-running check is SKIPPED, and the
inhibit goes live. It then transmits ~16 zero-torque 0x051 frames at a
generator turning 800 rpm before the runtime rpm trip's 0.3 s debounce ends
the run.

This is pre-refactor behaviour, faithfully preserved -- it is NOT a
regression introduced by the core split. It is recorded here as a golden so
that the behaviour is visible and any change to it shows up as a diff.

WHY IT MATTERS, and how much -- measured 2026-09-19, AFTER this scenario was
written, and the answer is "less than this scenario implies":

  * Across 21,925 real frames with the generator at >=300 rpm, gen_rpm_ref was
    never the engine-off null and torque was never zero. A loaded running
    generator always coincides with the VCM commanding it, and the gate's
    vcm_rpm_ref/vcm_torque checks read 0x051, which is always fresh when the
    gate runs. The load-dump case is independently covered.
  * The combination this scenario synthesises occurs 8 times in 39,718
    co-observed frames across 150 logs, spanning 73 ms, and it is an engine
    coasting down after the VCM already commanded it off -- unloaded, so
    commanding zero there is harmless and going live is correct.

Keep the scenario: the skipped-check path is real and this pins it. But do not
read it as a vehicle hazard. Raised with the user; a fix belongs in the spec
before the code.
""")
def s_gate_order_cmd_first():
    L = ["mode 0 3 500"]
    L += periodic(1 * S + 1, 4 * S, 100 * MS, lambda t: gene(t, 800))
    L += cmd_train(1 * S, 4 * S, 20 * MS)
    L += ["end %d" % (5 * S)]
    return sorted_directives(L)


@scenario("disable-clears-live-flag", """
A latched section 6 disable (here M mode) arrives while the inhibit is live.

EXPECT: transmission stops, inhibit_live goes false, and arm_block reads
"latched disable". Spec 6.4, added 2026-09-19.

It then DISARMS and RE-ARMS while still latched, which is the second route
into the same state and the one m-mode-latch does not cover: the arm-cycle
reset sets arm_block to "gate not yet evaluated", and without the guard in
gi_tick that would hide the reason the gate is never going to run. Expect
arm_block back to "latched disable", inhibit_live still false, and still no
transmission.

READING THE FINAL LINE: tx_ok is 0 and the frame counts look far too low for
the scenario length. That is correct, not a bug. Arming calls the arm-cycle
reset, so the re-arm at 6 s zeroes tx_ok, other_frames, ctr_steps and the
histograms -- FINAL therefore describes only the post-6 s window, in which
the device is latched off and rightly transmits nothing. The ~100 frames
transmitted between going live at 1 s and the latch at 3 s are real; they
are simply no longer counted. The EV and STATE lines above are the record of
that earlier activity.

HISTORY, because this scenario exists to pin a fix rather than a feature.
As found by this harness, inhibit_live stayed 1 for the rest of the run: the
whole interlock block is guarded by `mode == INHIBIT && !disabled`, so once
disabled neither the runtime trips nor the arm gate ran again and nothing
cleared the flag, and the worker never reached its OFF branch because the
mode was still INHIBIT.

Why that mattered enough to change the spec: inhibit_live is NOT just
telemetry. It is one of the terms authorising a transmit in gi_on_frame(),
so a frozen-true flag meant a control flag reading "authorised" while
transmission was forbidden, with only the `!disabled` term beside it in the
same && chain preventing a transmit. A one-term margin that depends on the
order of a boolean expression. The fix clears the flag at the instant of the
latch, so the dispatch does not rely on `!disabled` at all.
""")
def s_disable_freezes_live():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 9 * S, 20 * MS)
    L += periodic(1 * S, 3 * S, 200 * MS, lambda t: shift(t, 2))
    L += periodic(3 * S, 9 * S, 200 * MS, lambda t: shift(t, 4))
    # Disarm and re-arm while the disable is still latched.
    L += ["mode %d 0 500" % (5 * S)]
    L += ["mode %d 3 500" % (6 * S)]
    L += ["end %d" % (10 * S)]
    return sorted_directives(L)


@scenario("arm-gate-vcm-wants-engine", """
The VCM is requesting the engine when the device arms (rpm_ref >= 0, nonzero
torque), then stops requesting.

EXPECT: blocked on "VCM requesting engine", then on nothing, then live. The
only transition the gate arms into is "engine off, VCM tries to start it, we
stop it".
""")
def s_gate_vcm_wants():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS, b0=0x0B, torque=300, rpm_ref=900)
    L += cmd_train(3 * S, 5 * S, 20 * MS)
    L += ["end %d" % (6 * S)]
    return sorted_directives(L)


@scenario("vcm-fault", """
0x617 B7 goes to 0xCA (fault active) while live, then clears.

EXPECT: abort "0x617 B7 = 0xCA (VCM fault active)". Once aborted the device is
latched and the clearing frame does not bring it back -- an abort is not a
block. (Before spec 7.1 the latch was expressed as mode OFF; it is latched=1
now, and the key never transitions here, so nothing clears it.)
""")
def s_vcm_fault():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += periodic(1 * S, 3 * S, 200 * MS, lambda t: fault(t, 0xC8))
    L += periodic(3 * S, 5 * S, 200 * MS, lambda t: fault(t, 0xCA))
    L += periodic(5 * S, 8 * S, 200 * MS, lambda t: fault(t, 0xC8))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("tx-fail-aborts", """
Transmits start failing while the inhibit is live -- REFUSED BY THE QUEUE,
which is only one of spec 7 trip 7's three forms (review C1).

EXPECT: abort "transmit failed" on the FIRST failure, no averaging. Unlike an
error frame, which is a property of the bus and is judged on a rate, a failed
transmit is unambiguously ours -- our frame did not go out, so the VCM's
command stands and we are not inhibiting anything.
""")
def s_tx_fail():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += ["txfail %d %d" % (3 * S, 6 * S)]
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("err-rate-trip", """
The controller's bus_error_count climbs by 12 inside one 10 s window.

EXPECT: abort "error-frame rate exceeded" once the delta reaches 10. Rate
based, never first-strike: aborting on the first error frame ended three
consecutive armed runs, and the running-generator regime legitimately bursts
to 4 in any 10 s.
""")
def s_err_rate():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    for i in range(1, 13):
        L.append("bus %d 1 1 1 %d" % (2 * S + i * 100 * MS, i))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("err-rate-under", """
The same climb but stopping at 9 within the window.

EXPECT: no abort. Pins the threshold from the other side -- 10 in 10 s keeps
2.5x headroom over the worst observed burst, and this is the scenario that
fails if someone lowers it.
""")
def s_err_under():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    for i in range(1, 10):
        L.append("bus %d 1 1 1 %d" % (2 * S + i * 100 * MS, i))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("observe-never-transmits", """
Mode OBSERVE with a fully live bus.

EXPECT: no PROBE and no INHIBIT frames. Diag frames ARE emitted by the core --
on the device they then fail in listen-only, which is the documented cost of a
genuinely passive tap, and is a shim behaviour this harness cannot see.
""")
def s_observe():
    L = ["mode 0 1 500"]
    L += cmd_train(1 * S, 4 * S, 20 * MS)
    L += periodic(1 * S, 4 * S, 100 * MS, fb)
    L += ["end %d" % (5 * S)]
    return sorted_directives(L)


@scenario("respond-probe-offset", """
Mode RESPOND with a 500 us probe offset.

EXPECT: one 0x7F0 per received 0x051, due 500 us after the receive, echoing
the RX timestamp in B0-B3 and the offset in B4-B5. Never 0x051.
""")
def s_respond():
    L = ["mode 0 2 500"]
    L += cmd_train(1 * S, 2 * S, 20 * MS)
    L += ["end %d" % (3 * S)]
    return sorted_directives(L)


@scenario("rearm-after-disarm", """
Arm, go live, disarm, arm again on a still-live bus.

EXPECT: the second arm goes live again promptly -- signal freshness is
deliberately NOT cleared by the arm-cycle reset, because it is a property of
the bus and not of the run, and clearing it would make every arm wait a fresh
round. Statistics DO reset. fb_ever resets, so the inverter-lost trip re-arms
from scratch.
""")
def s_rearm():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS)
    L += periodic(1 * S, 6 * S, 100 * MS, fb)
    L += ["mode %d 0 500" % (3 * S)]
    L += ["mode %d 3 500" % (3500 * MS)]
    L += cmd_train(3500 * MS, 6 * S, 20 * MS)
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("short-dlc-0x051", """
A 0x051 arrives with DLC 4 -- too short to rebuild an inhibit frame from,
because B5 carries the rolling counter this device exists to steal.

EXPECT, SINCE REVIEW C2b (spec 7 trip 8): a LATCHED abort on the first short
frame, naming it. tx_fail still increments, but it is no longer the only
record -- and note the counter's name is misleading here, since no transmit was
ever attempted: this is a fault in what the VCM SENT.

Before 2026-09-24 the device skipped the frame and carried on inhibiting, with
no abort and no event. A VCM emitting short 0x051 on a bus we are actively
transmitting onto is a state nobody has observed and nobody can explain, and
continuing while not understanding what the truck is doing is the wrong
default.

Also confirms the interlock monitor's own DLC guards hold: torque and rpm_ref
need DLC >= 5 and must keep their previous values rather than read past the
end.
""")
def s_short_dlc():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 2 * S, 20 * MS)
    L += [_f(2 * S + i * 20 * MS, 0x051, [0x08, 0x00, 0x80, 0x00], 4)
          for i in range(25)]
    L += cmd_train(2500 * MS, 3500 * MS, 20 * MS)
    L += ["end %d" % (4 * S)]
    return sorted_directives(L)


# ------------------------------------------------- spec 7.1: key state --
#
# Six scenarios, all autokey=False because they build their own 0x592. Between
# them they pin both of spec 7.1's rules and the two things that are easy to
# get wrong about them: that arming is NOT gated, and that a stale key is not
# a key-off.


@scenario("key-arms-while-off", """
0x051 flows with the engine off and torque zero, and the key reads OFF for the
whole scenario.

EXPECT: the inhibit GOES LIVE -- arming is not gated on the key -- and
transmits NOTHING. live=1 with tx_ok=0 is the whole assertion.

This is spec 7.1's "armed but silent", and it is the half of the design that
answers the user's requirement to be armed before the VCU asserts the enable
flag and torque, rather than racing it. Every arm-gate condition is satisfied
during a key-off on the real truck: 0x051 outlives the key by a median 77 s,
the generator is stopped, gen_rpm_ref reads its engine-off null (raw 0x7FFF ->
-1, which passes the >= 0 test) and torque is zero.
""", autokey=False)
def s_key_arms_while_off():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += key_train(1 * S, 6 * S, on=False)
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("key-gates-tx", """
Live and transmitting, then the key goes off for 2 s and comes back, with
0x051 and 0x471 both running at full rate throughout.

EXPECT: transmission stops at the key-off frame and resumes at the key-on
frame. No abort, because nothing went quiet. No KEY_CLEAR either -- there was
no latch to clear, and a key-on with nothing latched must be a no-op rather
than a re-arm.

This is rule 1 on its own, with every other input held still.
""", autokey=False)
def s_key_gates_tx():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 10 * S, 20 * MS)
    L += periodic(1 * S, 10 * S, 100 * MS, fb)
    L += key_train(1 * S, 4 * S, on=True)
    L += key_train(4 * S, 6 * S, on=False)
    L += key_train(6 * S, 10 * S, on=True)
    L += ["end %d" % (11 * S)]
    return sorted_directives(L)


@scenario("key-on-clears-abort", """
The end of a drive and the start of the next one, which is the case finding 3
measured: 100 of 100 key-offs in the corpus would trip the section 7 inverter
trip as it stood.

  1 s  live, transmitting, 0x471 arriving
  4 s  key off. Transmission stops at once (rule 1). 0x471 stops with it --
       the inverter sleeps, leading the key by a median 0.302 s on the truck.
       0x051 keeps running, because the VCM outlives the key.
 ~4.5 s GI_ABORT_INVERTER_LOST latches. Correct: the inverter DID go quiet on
       a live bus. Before 7.1 this was the end of the story and the device
       stayed latched for the rest of the drive and the next one.
  8 s  key on. KEY_CLEAR: the latch goes, fb_ever resets, the gate re-runs.
  9 s  0x471 comes back, a second after we went live again.

EXPECT: transmits, latches, clears on the key-on, transmits again. The gap
between 8 s and 9 s is load-bearing -- going live with fb_ever still true
would abort instantly on an inverter that has not powered up yet, which is
bug 2 from 2026-09-19 reintroduced through a different door.
""", autokey=False)
def s_key_on_clears_abort():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 14 * S, 20 * MS)
    L += periodic(1 * S, 4 * S, 100 * MS, fb)
    L += key_train(1 * S, 4 * S, on=True)
    L += key_train(4 * S, 8 * S, on=False)
    L += key_train(8 * S, 14 * S, on=True)
    L += periodic(9 * S, 14 * S, 100 * MS, fb)
    L += ["end %d" % (15 * S)]
    return sorted_directives(L)


@scenario("key-on-clears-disable", """
M mode latches the section 6 release, then the driver keys off and on.

EXPECT: disabled at 3 s, transmission stops; KEY_CLEAR at the 6 s key-on;
the device re-arms and transmits again.

THIS IS THE LITERAL READING OF THE USER'S RULING ("clears any latch"), and it
is worth being explicit that it extends to section 6. The justification is
that a key cycle is the reboot these latches were always documented to wait
for -- section 6 says cleared "only by a reboot, which the relay dropping at
truck sleep provides", and a re-key quick enough that the relay never opens is
that same event with the power never interrupted. Nothing survives on
evidence either way: M mode has to be re-selected before disable_monitor()
can see it again, and a low SoC re-latches after five valid 0x411 frames. The
shift lever is back at P here, so nothing re-latches.
""", autokey=False)
def s_key_on_clears_disable():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 10 * S, 20 * MS)
    L += key_train(1 * S, 5 * S, on=True)
    L += key_train(5 * S, 6 * S, on=False)
    L += key_train(6 * S, 10 * S, on=True)
    L += periodic(1 * S, 3 * S, 200 * MS, lambda t: shift(t, 0))
    L += periodic(3 * S, 4 * S, 200 * MS, lambda t: shift(t, 4))
    L += periodic(4 * S, 10 * S, 200 * MS, lambda t: shift(t, 0))
    L += ["end %d" % (11 * S)]
    return sorted_directives(L)


@scenario("key-stale-not-keyoff", """
0x592 simply stops for 2 s while the key is ON, then resumes still reading ON.
0x051 and 0x471 run throughout.

EXPECT: transmission stops when the key goes STALE (rule 1 is freshness as
well as value) and resumes when 0x592 comes back. No KEY event, because the
value never changed. Crucially NO KEY_CLEAR: staleness is not a key-off.

That distinction is what keeps the bus-loss latch meaningful. A key-off takes
0x592 with it, but so does a dropped connector -- and if a stale key counted
as an off, every flapping link would clear the very latch that exists because
the link is flapping. Only an actual 0 followed by an actual 1 clears.
""", autokey=False)
def s_key_stale():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 10 * S, 20 * MS)
    L += periodic(1 * S, 10 * S, 100 * MS, fb)
    L += key_train(1 * S, 3 * S, on=True)
    L += key_train(5 * S, 10 * S, on=True)
    L += ["end %d" % (11 * S)]
    return sorted_directives(L)


@scenario("key-never-seen", """
A bus with no 0x592 on it at all -- a partial bench harness, or a capture
taken from the wrong segment.

EXPECT: arms and goes live, and transmits NOTHING. Never-seen reads OFF.

Fail-closed is the literal spec ("transmit only while the key reads on") and
it is the safe direction, but it has a cost that this scenario exists to make
visible: a bench that does not present 0x592 will see a device that looks
armed and does nothing, with no error anywhere. That is the reason every
other scenario in this file carries an injected key train, and the reason the
bench harness has to send 0x592 too.
""", autokey=False)
def s_key_never_seen():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += periodic(1 * S, 6 * S, 100 * MS, fb)
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


# ------------------------------------------------------------- plumbing --


# --------------------------------------------------------------------------
# Spec 6.2 / 7 condition 2 -- the SoC-valid marker (review A1, 2026-09-24).
#
# All four build their own 0x440/0x411 (autobms=False), because the marker is
# the thing under test and an injected closed train would decide the answer.
# The numbers are the corpus wake behaviour, not invented: the wake reading is
# LOW by a median 13.8 % and corrects 0.32-1.15 s later, and in all 103 scanned
# wakes where the contactors closed, SoC had already corrected before the first
# closed state.


@scenario("soc-wake-transient-ignored", """
The BMS wake transient, which is what this rule exists for. The contactors are
open, 0x411 comes up reading 18.77 % -- a real value from
vtrux_20260513_174225_T4 against a real pack of 24.41 % -- holds it for 1.0 s,
corrects, and only then do the contactors close.

EXPECT: no low_soc latch, ever. Every sub-threshold reading arrives while the
SoC-valid marker is clear and must not count toward the debounce; the trace
shows socv=0 across them, then SOC_VALID a=1 at the close, and live only
after that. Before A1 this capture latched low_soc for the whole drive and the
golden blessed it.
""", autobms=False)
def s_soc_wake_transient():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    # 0x617/0x639 are arm-gate conditions since review B3; the BMS half
    # of the background is what this scenario drives itself.
    L += _healthy_bg(1 * S, 8 * S, skip=(0x440, 0x411))
    # BMS wakes with the bus, contactors still open (0 = ALL_OPEN).
    L += contactor_train(1 * S, 3 * S, stat=0)
    L += periodic(1 * S, 2 * S, 50 * MS, lambda t: soc(t, 1877))
    # ... corrects, still open. 24 readings of the false value have gone by.
    L += periodic(2 * S, 3 * S, 50 * MS, lambda t: soc(t, 2441))
    # ... and now the contactors walk their real sequence and close.
    L += [contactor(3 * S, 2), contactor(3050 * MS, 5),
          contactor(3100 * MS, 7), contactor(3150 * MS, 8),
          contactor(3200 * MS, 9)]
    L += contactor_train(3250 * MS, 8 * S, stat=11)
    L += periodic(3 * S, 8 * S, 50 * MS, lambda t: soc(t, 2441))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("soc-low-after-close", """
A genuinely low pack, read after the contactors closed. The other half of A1:
the rule must not have made the low-SoC release unreachable.

EXPECT: live first, then low_soc latches after soc_debounce readings and
transmission stops for good. Same readings as soc-wake-transient-ignored, in
the other order relative to the close.
""", autobms=False)
def s_soc_low_after_close():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += _healthy_bg(1 * S, 8 * S, skip=(0x440, 0x411))
    L += contactor_train(1 * S, 8 * S, stat=11)
    L += periodic(1 * S, 3 * S, 50 * MS, lambda t: soc(t, 2441))
    L += periodic(3 * S, 8 * S, 50 * MS, lambda t: soc(t, 1877))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("soc-never-closed-never-live", """
The contactors never report closed. A charging-only or LV-only wake, or a
capture that starts after the drive.

EXPECT: the inhibit NEVER goes live, and the block reads "SoC not yet valid
(contactors)" rather than something that sounds like a fault. Nothing is
transmitted and nothing aborts -- this is "not ready", not a failure. It costs
no coverage: the engine is cranked by the HV inverter, which cannot run with
the contactors open.
""", autobms=False)
def s_soc_never_closed():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += _healthy_bg(1 * S, 6 * S, skip=(0x440, 0x411))
    L += contactor_train(1 * S, 6 * S, stat=7)    # WAIT_PRECHARGE, forever
    L += periodic(1 * S, 6 * S, 50 * MS, lambda t: soc(t, 5000))
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("soc-marker-clears-on-bms-sleep", """
The marker's staleness half, which is the end of every drive: the key goes
off, the BMS stops, and the next thing it says is a wake reading.

EXPECT: SOC_VALID a=0 within one freshness window of 0x440/0x411 stopping,
then -- when they come back with the contactors still walking their sequence --
the false low reading is ignored exactly as in soc-wake-transient-ignored. The
0x051 and the key keep running throughout, so this isolates the BMS going away
from the bus going away (which is bus-loss-latches).

Two rules meet here and it is worth keeping them apart. The marker clearing
does not end a live inhibit BY ITSELF -- it governs whether the next 0x411 can
be believed. What ends it is spec 7 trip 5, which fires on the same staleness
because 0x051 keeps arriving and so satisfies the evidence rule. Before B3
this scenario transmitted straight through the BMS's absence (449 frames);
now it stands down at it.
""", autobms=False)
def s_soc_marker_clears():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 10 * S, 20 * MS)
    L += _healthy_bg(1 * S, 10 * S, skip=(0x440, 0x411))
    L += contactor_train(1 * S, 4 * S, stat=11)
    L += periodic(1 * S, 4 * S, 50 * MS, lambda t: soc(t, 5000))
    # BMS asleep 4.0 -> 6.0 s. One freshness window in, the marker clears.
    L += contactor_train(6 * S, 7 * S, stat=0)
    L += periodic(6 * S, 7 * S, 50 * MS, lambda t: soc(t, 1500))
    L += contactor_train(7 * S, 10 * S, stat=11)
    L += periodic(7 * S, 10 * S, 50 * MS, lambda t: soc(t, 5000))
    L += ["end %d" % (11 * S)]
    return sorted_directives(L)



# --------------------------------------------------------------------------
# Spec 7 trip 5 and arm-gate conditions 3-4 (review B3, decided 2026-09-24).
#
# A stale interlock signal while LIVE is a latched abort. These build their own
# background (autobms=False) because the signal under test is exactly the one
# the injector would keep healthy.


def _healthy_bg(t0, t1, skip=()):
    """Healthy 0x440/0x411/0x617/0x639 over [t0, t1), minus `skip`."""
    out = []
    if 0x440 not in skip:
        out += contactor_train(t0, t1)
    if 0x411 not in skip:
        out += periodic(t0, t1, 50 * MS, lambda t: soc(t, 5000))
    if 0x617 not in skip:
        out += periodic(t0, t1, 250 * MS, lambda t: fault(t, 0xC8))
    if 0x639 not in skip:
        out += periodic(t0, t1, 50 * MS, lambda t: shift(t, 2))
    return out


def _stale_one(ident, fn, period):
    """Live on a healthy bus, then ONE signal stops while 0x051 keeps running."""
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += _healthy_bg(1 * S, 8 * S, skip=(ident,))
    L += periodic(1 * S, 4 * S, period, fn)      # the signal under test, then gone
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("stale-soc-aborts", """
0x411 stops while the inhibit is live and 0x051 keeps arriving. The BMS has
dropped off a bus that is otherwise healthy -- which the LV-connector
disconnect test shows really happens, and which the pre-B3 text claimed had
"no plausible cause".

EXPECT: a LATCHED abort naming 0x411, on the first 0x051 after its freshness
window expires. Not a block, not a pause: spec 7 trip 5 is option (c), because
if the BMS is gone the truck is either stopping or we have lost sight of it.

This is also the half of the spec 7 EVIDENCE RULE that must keep working. Its
companion bus-stop-reports-link-loss pins the other half -- everything stopping
at once must read as the link. If this scenario ever stops aborting, the
evidence rule has been written too strictly and trip 5 is dead.
""", autobms=False)
def s_stale_soc():
    return _stale_one(0x411, lambda t: soc(t, 5000), 50 * MS)


@scenario("stale-contactor-aborts", """
0x440 stops while live. Same shape as stale-soc-aborts, different signal, and
worth its own scenario because 0x440 also drives the spec 6.2 SoC-valid marker
-- so the trace shows SOC_VALID clearing and the abort landing together.

EXPECT: a latched abort naming 0x440.
""", autobms=False)
def s_stale_contactor():
    return _stale_one(0x440, lambda t: contactor(t, 11), 50 * MS)


@scenario("stale-fault-aborts", """
0x617 stops while live. This is the tightest signal against the 0.5 s window
-- 4 Hz, worst measured gap 308 ms -- so it is the one a careless tightening of
fresh_us would break first.

EXPECT: a latched abort naming 0x617. Note what this is NOT: the VCM-fault trip
(0x617 B7 = 0xCA) is trip 1 and fires on a value. This fires on absence, which
before B3 read as "no fault" for ever.
""", autobms=False)
def s_stale_fault():
    return _stale_one(0x617, lambda t: fault(t, 0xC8), 250 * MS)


@scenario("stale-shift-aborts", """
0x639 stops while live. Without it the M-mode release of section 6.1 is
evaluating last_shift_pos, a last-known value that a silent bus can no longer
contradict.

EXPECT: a latched abort naming 0x639.
""", autobms=False)
def s_stale_shift():
    return _stale_one(0x639, lambda t: shift(t, 2), 50 * MS)


@scenario("stale-rpm-once-heard", """
0x054 is heard, then stops. It carries the same once-heard qualification as
0x471, so this pins the ARMED half of it; keyon-normal and keyon-gene-late pin
the other half, where 0x054 never arrives and must not trip anything.

EXPECT: live at ~1 s with no 0x054 anywhere (never-seen is not stale), no abort
when 0x054 starts at 3 s, and a latched abort naming 0x054 one freshness window
after it stops at 5 s. If this aborts before 3 s, bug 2 has been reintroduced
through trip 5.
""", autobms=False)
def s_stale_rpm():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += _healthy_bg(1 * S, 8 * S)
    L += periodic(3 * S, 5 * S, 100 * MS, lambda t: gene(t, 0))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("gate-needs-fresh-fault", """
0x617 never arrives at all. Before B3 the gate passed: vcm_fault is initialised
to 0xC8, so a bus that had never mentioned the fault flag read as "no fault" --
a silent bus answering a safety question in the reassuring direction.

EXPECT: never live, tx_ok=0, blocked on "no fresh 0x617". A block, not an
abort: this is "not ready", and it clears by itself if the VCM starts talking.
""", autobms=False)
def s_gate_needs_fault():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += _healthy_bg(1 * S, 6 * S, skip=(0x617,))
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("gate-needs-fresh-shift", """
0x639 never arrives. The same argument for the M-mode release: last_shift_pos
reads its 0xFF never-seen sentinel, and the gate used to pass anyway.

EXPECT: never live, tx_ok=0, blocked on "no fresh 0x639".
""", autobms=False)
def s_gate_needs_shift():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += _healthy_bg(1 * S, 6 * S, skip=(0x639,))
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("stale-at-keyoff-then-keyon", """
THE ORDERING SPEC 7 REQUIRES A TEST FOR. At the end of a drive the key reads 0
and the BMS stops about a quarter-second later, so trip 5 fires AFTER the
key-off -- and the next key-on must clear it, exactly as it clears the
inverter-lost trip. Measured on vtrux_20260513_174225_T4: key 0 at 167.230 s,
last 0x411 at 167.692 s, last 0x440 at 167.680 s.

EXPECT, in this order: transmission stops the moment the key reads 0 (rule 1,
no abort); a latched trip-5 abort one freshness window after the BMS stops; and
on the key-on at 8 s, KEY_CLEAR, the gate re-entered, and live again once the
contactors have closed and an 0x411 has followed. If the abort came BEFORE the
key-off this scenario would be pinning the wrong thing, so the timestamps in
the trace are the assertion.
""", autokey=False, autobms=False)
def s_stale_at_keyoff():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 12 * S, 20 * MS)        # the VCM never stops talking
    L += key_train(1 * S, 5 * S, on=True)
    L += key_train(5 * S, 8 * S, on=False)        # key off at 5 s
    L += key_train(8 * S, 12 * S, on=True)        # back on at 8 s
    L += _healthy_bg(1 * S, 5300 * MS)            # BMS stops 0.3 s after the key
    L += _healthy_bg(8 * S, 12 * S)
    L += ["end %d" % (13 * S)]
    return sorted_directives(L)


@scenario("gate-contactors-open-now", """
Spec 7 condition 2 as the user resolved it on 2026-09-25: the contactors must
read closed NOW, not merely have done.

The bus is fully healthy and the SoC-valid marker is SET -- 0x440 reads 11 for
the first two seconds with 0x411 alongside it, so the readings are valid and
stay valid, because spec 6.2 says the contactors merely opening while the BMS
stays awake does not invalidate them. Then 0x440 goes to 13 SHUTDOWN_REQUEST
and 14 ALL_OPEN_SHUTDOWN and keeps arriving. This is an arm during a key-off
with the BMS still talking.

EXPECT: live while 0x440 reads 11; the moment it reads 13 the device is no
longer eligible -- but note it is ALREADY live by then, and going open does not
retract liveness, so what this scenario pins is the GATE. The re-arm at 5 s is
the assertion: with the marker still set and 0x440 reading 14, the gate must
refuse with "main contactors not closed" and never go live again.

Without the 2026-09-25 ruling the re-arm would pass the gate on soc_valid
alone and transmit onto a bus with no HV behind it.
""", autobms=False)
def s_gate_contactors_open_now():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 9 * S, 20 * MS)
    L += _healthy_bg(1 * S, 9 * S, skip=(0x440,))
    L += contactor_train(1 * S, 3 * S, stat=11)
    L += contactor_train(3 * S, 3500 * MS, stat=13)
    L += contactor_train(3500 * MS, 9 * S, stat=14)
    # The disarm/re-arm window is deliberately SHORT (100 ms). host_runner
    # drops frames while OFF, so a longer one would let 0x411/0x440 go stale,
    # clear the SoC-valid marker, and leave the re-arm blocking on
    # "SoC not yet valid" -- testing the marker instead of the new condition.
    # The whole point here is a re-arm with the marker still SET.
    L += ["mode %d 0 500" % (5 * S)]          # disarm
    L += ["mode %d 3 500" % (5100 * MS)]      # and re-arm, contactors open
    L += ["end %d" % (10 * S)]
    return sorted_directives(L)


@scenario("bus-stop-reports-link-loss", """
The evidence rule of spec 7 (decided 2026-09-25), and the case that prompted
it. EVERYTHING stops at once -- the pulled connector.

0x617 runs at 4 Hz, so its last frame precedes 0x051's by up to 250 ms and its
freshness window closes that much earlier. Before the rule, this aborted
naming 0x617 about 100 ms before trip 2 would have fired, so a pulled connector
reported "VCM fault flag lost" and would have sent someone after the VCM.

EXPECT: exactly one abort, and it names the LINK -- "bus lost -- no 0x051".
No trip-5 abort at any point, because once 0x051 has stopped no 0x051 can
arrive after any other signal's expiry, which is precisely what the evidence
rule tests.

This is a reporting rule, not a safety one: the device stands down identically
either way. It matters because the reason goes on the wire.
""", autokey=False, autobms=False)
def s_bus_stop_link_loss():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += key_train(1 * S, 6 * S, on=True)
    L += _healthy_bg(1 * S, 6 * S)
    L += ["end %d" % (8 * S)]
    return sorted_directives(L)




@scenario("bus-glitch-very-short", """
A 0.15 s whole-bus glitch -- short enough that even 0x617, the slowest
interlock at 4 Hz, stays inside its window.

EXPECT: no abort at all, and transmission resuming when the bus does. This is
now the scenario that says the freshness window is doing its job rather than
the trips being hair-triggered; bus-glitch-short stopped being able to say it
when review B3 gave trip 5 teeth, because a 0.3 s glitch can leave 0x617
absent for 0.55 s.

Pairs with bus-glitch-short as the two ends of the tolerance range: the device
survives a whole-bus interruption shorter than 0.5 s minus 0x617's phase, and
latches for one longer than that.
""", autokey=False, autobms=False)
def s_bus_glitch_very_short():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS)
    L += cmd_train(3150 * MS, 6 * S, 20 * MS)
    L += key_train(1 * S, 3 * S, on=True)
    L += key_train(3150 * MS, 6 * S, on=True)
    L += _healthy_bg(1 * S, 3 * S)
    L += _healthy_bg(3150 * MS, 6 * S)
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("tx-late-loses-counter-race", """
Spec 7 trip 7, third form (review C1), and the case "could not queue" never
caught: the inhibit frame IS accepted by the driver, but the controller has not
sent it by the time the VCM's next 0x051 arrives.

This is worse than a frame that never goes out. It may still go out -- late --
and a frame landing after the VCM's next one loses the counter race, so the
GENE inverter accepted the VCM's torque for that slot while the device believed
it was inhibiting.

EXPECT: transmission normally until 3 s; then the controller stops answering,
and on the VERY NEXT 0x051 a latched abort naming the race. tx_ok must stop
climbing at the stall, not at the abort -- since C1 a frame counts only when
the controller confirms it, so the queued-but-unconfirmed frame is never
counted.

Before C1 this ran to the end of the scenario transmitting happily, because
twai_transmit() returning ESP_OK was recorded as a successful transmit.
""", autobms=False)
def s_tx_late():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += _healthy_bg(1 * S, 6 * S)
    L += ["txstall %d %d" % (3 * S, 9 * S)]
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("tx-controller-reports-failed", """
Spec 7 trip 7, second form (review C1): the frame is queued, and the controller
comes back and says the attempt FAILED -- arbitration lost repeatedly, no ACK,
or bus-off.

EXPECT: a latched abort naming the transmit, at the instant of the verdict
rather than at the next 0x051. Distinct from tx-fail-aborts, where the driver
refused the frame outright, and from tx-late-loses-counter-race, where nobody
ever answered. All three end the inhibit; they differ in what a log says the
truck did, which is the whole point of C1.
""", autobms=False)
def s_tx_controller_failed():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 6 * S, 20 * MS)
    L += _healthy_bg(1 * S, 6 * S)
    L += ["txdone %d %d" % (3 * S, 9 * S)]
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


@scenario("diag-defers-to-pending-tx", """
The core half of the 2026-09-25 alert-attribution fix: while an inhibit frame
is outstanding, the diag page waits.

Two reasons, both load-bearing. TIMING (spec 10): the diag IDs are the lowest
priority on the bus and share the controller's single TX buffer with the
inhibit frame. ATTRIBUTION (review C1): the driver's TX alerts are latched bits
shared by every frame and TWAI_ALERT_TX_SUCCESS does not say WHICH frame
completed, so if a diag frame can complete while an inhibit is in flight the
shim credits the inhibit with the diag frame's success -- masking the very case
trip 7 exists to catch. Keeping one frame of ours in flight is what makes the
alert attributable.

Here the controller stops answering at 3 s and 0x051 pauses for 0.4 s, inside
the freshness window, so several ticks run with a frame outstanding.

EXPECT: no TX DIAG line between the stalled transmit and the abort, and then a
TX_LATE abort on the first 0x051 after the pause. The host harness cannot test
the shim half of this -- that is an E1 mock-HAL case (latched shared alert
bits, a diag frame completing ahead of an inhibit, a completion at 1.2 ms).
""", autobms=False)
def s_diag_defers():
    L = ["mode 0 3 500"]
    # 0x051 stops 40 ms after the stall begins, so the outstanding frame
    # survives several ticks instead of being caught by the next 0x051 20 ms
    # later -- that gap is the whole observation window. The pause is 0.4 s,
    # inside the 0.5 s freshness window, so nothing trips on absence.
    L += cmd_train(1 * S, 2940 * MS, 20 * MS)
    L += cmd_train(3340 * MS, 5 * S, 20 * MS)
    L += _healthy_bg(1 * S, 5 * S)
    L += ["txstall %d %d" % (2900 * MS, 9 * S)]
    L += ["end %d" % (6 * S)]
    return sorted_directives(L)


@scenario("passive-dry-run", """
PASSIVE (spec 3.1, review B1) on an ordinary drive: arm in mode 4, the bus
wakes, the gate passes, and the device counts would-transmits instead of
transmitting.

EXPECT: the same trace as keyon-normal in every decision -- live at the same
instant, the same trailing bus-loss abort -- with tx_ok=0, would_tx equal to
the number of 0x051 frames that arrived while live, and NOT ONE `TX INHIBIT`
line. emit_refused must read 0: it is a tripwire on the choke point in emit(),
and a non-zero value means a code path tried to put a real 0x051 on the wire
from a mode that must not.

This golden pins the mode-4 trace format. The PROPERTY -- that PASSIVE decides
exactly as INHIBIT does -- is not pinned here and cannot be, because a golden
can only agree with itself; passive_diff.py replays every scenario in both
modes and compares them, which is a check that has no golden to be blessed
into.
""")
def s_passive_dry_run():
    L = ["mode 0 4 500"]
    L += cmd_train(3 * S, 8 * S, 20 * MS)
    L += ["end %d" % (9 * S)]
    return L


@scenario("diag-cadence-inhibit-to-passive", """
Switching a LIVE INHIBIT to PASSIVE must not hold the diagnostics, and before
2026-09-26 it held them for up to a second.

Spec 5.1 item 4 schedules a due diag page to the moment just after an inhibit
frame completes, and names the cases where the normal cadence applies instead:
"not live, key off, PASSIVE, RESPOND". The implementation tested
`inhibit_live && key_on && !suppressed` with NO MODE TERM, and `inhibit_live` is
set in PASSIVE exactly as in INHIBIT -- that is the whole point of spec 3.1, one
decision path, differing only at the moment of transmission. So on the switch
the core went on waiting for a completion that by construction could never
arrive, and diag stopped until the 1 s fallback rescued it.

WHY NO EXISTING SCENARIO CAUGHT IT: none switches a live INHIBIT to PASSIVE.
`passive-dry-run` arms in PASSIVE from t=0 and never transmits, so
`have_tx_done` stays false, `completion_recent` is false, and the normal cadence
applies for a different reason. The defect needed a device that HAD completed a
frame and then stopped being able to.

EXPECT: diag pages at their ordinary cadence across the 4 s switch, with no gap
around it -- and tx_ok stops advancing while would_tx starts, because PASSIVE
decides everything and transmits nothing.
""")
def s_diag_cadence_inhibit_to_passive():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += _healthy_bg(1 * S, 8 * S)
    # The switch lands mid-stream, 20 ms after a 0x051 and so within a
    # millisecond or two of that frame's completion -- the worst moment for the
    # defect, because the completion was freshly on record.
    L += ["mode %d 4 500" % (4 * S)]
    L += ["end %d" % (8 * S)]
    return sorted_directives(L)


@scenario("diag-cadence-after-keycycle", """
A stale completion record must not gate the next transmitting episode.

Spec 5.1 item 4 (amended 2026-09-26, user) clears the device's record of its
last inhibit completion whenever it stops being able to transmit, so a device
that has just gone live has none on record and takes the normal diag cadence
until its first one. Until that change the record was set in gi_on_tx_done() and
cleared NOWHERE -- not on a disarm, not on an abort, not on a mode change -- so a
device carried a completion timestamp across a trip or a key cycle.

WHY THE CLEARING NEEDS ITS OWN SCENARIO. The change is invisible in every other
trace: it only bites where a stale record exists AND a diag page falls due before
the first new completion. Adding a guard that no test distinguishes is how the 1 s
fallback this replaces came to sit unexercised through 74 goldens and 200
randomised sequences, so this scenario exists to make the difference observable.

HOW IT IS MADE OBSERVABLE. Live and transmitting to 3 s, so completions are on
record. The key goes off for a second -- spec 7.1 keeps the device armed and
live, it just stops transmitting -- and comes back at 4 s. `0x051` then pauses
for 0.4 s, inside the 0.5 s freshness window so nothing trips (the same trick
diag-defers-to-pending-tx uses), which means no new completion can arrive until
4.4 s. That 0.4 s is the window in which the two behaviours differ, and it is
long enough to hold a page at the 300 ms cadence rather than relying on one
landing in a 10 ms gap.

EXPECT: diag pages continue at their ordinary cadence across the key cycle and
through the 0x051 pause. With the clearing removed, the stale record makes
`in_window` false and the pages are held until the first completion at 4.4 s.
""", autokey=False)
def s_diag_cadence_after_keycycle():
    L = ["mode 0 3 500"]
    # The VCM never stops talking through a key-off; only the 0.4 s pause after
    # the key returns is a gap, and that is the measurement window.
    L += cmd_train(1 * S, 4 * S, 20 * MS)
    L += cmd_train(4400 * MS, 8 * S, 20 * MS)
    L += key_train(1 * S, 3 * S, on=True)
    L += key_train(3 * S, 4 * S, on=False)
    L += key_train(4 * S, 8 * S, on=True)
    # Healthy throughout: this scenario is about the diag record, and a stale
    # trip on a supporting signal would end the episode before the window.
    L += _healthy_bg(1 * S, 8 * S)
    L += ["end %d" % (8 * S)]
    return sorted_directives(L)


# --------------------------------------------------------------------------
# Round-3 mutation survivors (reviewing session, 2026-09-25). Each of these
# rules was in the code with nothing asserting it.


@scenario("keyon-clear-then-gene-late", """
BUG 2 THROUGH A THIRD DOOR, and the one the mutation round found unguarded.

The key-on clear resets fb_ever AND rpm_ever. If either survived the clear, the
device would go live on the way back in -- correctly, since the gate does not
require the GENE family -- and then immediately abort, because the GENE signal
is stale and every arriving 0x051 satisfies the spec 7 evidence rule. Trip 3
would fire on 0x471 and trip 5 on 0x054, on a healthy truck, every re-key,
about 28 s before the inverter wakes.

That is bug 2 of 2026-09-19 in a new place. It was fixed in the code from the
start and the comment says so; nothing tested it. replay-rekey-long cannot: its
inverter is already awake (fb_ever=1 at 202.995 s) before the device goes live
at 203.471 s, so the flags are legitimately set either way.

Here the GENE family runs for the first drive, stops at the key-off, and NEVER
COMES BACK -- which is what the corpus shows for a drive that does not use the
generator.

EXPECT: a latched abort in the first drive (inverter lost, at the key-off), the
key-on clearing it, live again -- and then NO abort for the rest of the run
despite 0x054 and 0x471 never arriving again. If either _ever flag survives the
clear, this aborts within a frame or two of going live.
""", autokey=False, autobms=False)
def s_keyon_clear_then_gene_late():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 20 * S, 20 * MS)
    L += key_train(1 * S, 6 * S, on=True)
    L += key_train(6 * S, 8 * S, on=False)      # key off at 6 s
    L += key_train(8 * S, 20 * S, on=True)      # back on at 8 s
    L += _healthy_bg(1 * S, 20 * S)
    # The inverter is awake for the first drive only.
    L += periodic(1 * S, 6 * S, 100 * MS, fb)
    L += periodic(1 * S, 6 * S, 100 * MS, lambda t: gene(t, 0))
    L += ["end %d" % (21 * S)]
    return sorted_directives(L)


@scenario("rearm-after-abort", """
Spec 7.1 names TWO exits from a section 7 latch: a key-on, and an explicit
re-arm. The key-on half is covered by key-on-clears-abort. The re-arm half was
not covered at all -- rearm-after-disarm re-arms after an ORDINARY disarm, with
no latch set.

EXPECT: the bus-loss trip latches on the silence, and the re-arm at 7.1 s
clears it -- the device goes live again on the returning traffic.

NOTHING ELSE MAY BE LATCHED HERE, and that is the point of it being its own
scenario. An earlier version also drove a low-SoC release first, to assert in
one place that a re-arm clears the section 7 latch and NOT the section 6 one.
That masked the thing it was testing: with `disabled` set, the gate reports
"latched disable" whether or not the abort was cleared, so removing
`abort_latched = false` from gi_reset_stats() passed. The section 6 half is
rearm-keeps-section6-latch, below.
""", autokey=False, autobms=False)
def s_rearm_after_abort():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 3 * S, 20 * MS)
    # 1.5 s of silence -> bus-loss latches.
    L += cmd_train(6 * S, 12 * S, 20 * MS)
    L += key_train(1 * S, 3 * S, on=True)
    L += key_train(6 * S, 12 * S, on=True)
    L += _healthy_bg(1 * S, 3 * S)
    L += _healthy_bg(6 * S, 12 * S)
    L += ["mode %d 0 500" % (7 * S)]            # disarm...
    L += ["mode %d 3 500" % (7100 * MS)]        # ...and re-arm with the latch set
    L += ["end %d" % (13 * S)]
    return sorted_directives(L)


@scenario("soc-threshold-boundary", """
Spec 12.4 / review D2: the 21.00 % boundary itself. Every other SoC scenario
uses values far from it -- 2050, 2500, 1877, 1500 -- so `raw < soc_min_raw`
becoming `<=` changes nothing anywhere in the suite.

2100 is 21.00 %, which must KEEP INHIBITING: spec 6.2 releases "below 21.00 %".
2099 is 20.99 %, which must release.

EXPECT: live through the 2100 run, then low_soc latches after soc_debounce
readings of 2099. If the boundary moves by one count, the first half latches.
""", autobms=False)
def s_soc_threshold_boundary():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 10 * S, 20 * MS)
    L += _healthy_bg(1 * S, 10 * S, skip=(0x411,))
    L += periodic(1 * S, 5 * S, 50 * MS, lambda t: soc(t, 2100))
    L += periodic(5 * S, 10 * S, 50 * MS, lambda t: soc(t, 2099))
    L += ["end %d" % (11 * S)]
    return sorted_directives(L)


@scenario("soc-recovers-stays-latched", """
Spec 6.2 and 6.4, review D1: "the latch is unconditional on SoC recovering."
The spec spells the case out -- fall to 19 %, the engine runs, SoC climbs back
to 22 %, and the inhibit does NOT resume -- and nothing tested it. Making the
latch conditional on the current reading survived the whole suite.

EXPECT: low_soc latches on the 19 % run and transmission stops; SoC returning
to 22 % for five seconds changes nothing -- still disabled, still low_soc,
tx_ok frozen at the value it had when the latch closed.
""", autobms=False)
def s_soc_recovers_stays_latched():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 12 * S, 20 * MS)
    L += _healthy_bg(1 * S, 12 * S, skip=(0x411,))
    L += periodic(1 * S, 3 * S, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(3 * S, 6 * S, 50 * MS, lambda t: soc(t, 1900))   # 19.00 %
    L += periodic(6 * S, 12 * S, 50 * MS, lambda t: soc(t, 2200))  # back to 22 %
    L += ["end %d" % (13 * S)]
    return sorted_directives(L)


@scenario("rearm-keeps-section6-latch", """
The other half of rearm-after-abort, kept apart from it on purpose.

gi_reset_stats() clears the section 7 abort latch and DELIBERATELY leaves the
section 6 `disabled` latch alone: spec 6 gives that one only a key-on or a power
cycle as exits, and spec 7.1 adds the re-arm to the section 7 one alone. The two
are adjacent lines in the same function, which is exactly how they would come to
be conflated by someone tidying it.

EXPECT: low_soc latches, the re-arm at 7.1 s changes nothing, and the device
never transmits again -- block "latched disable", dcode low_soc, tx_ok frozen.
""", autokey=False, autobms=False)
def s_rearm_keeps_section6():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 12 * S, 20 * MS)
    L += key_train(1 * S, 12 * S, on=True)
    L += _healthy_bg(1 * S, 12 * S, skip=(0x411,))
    L += periodic(1 * S, 2 * S, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(2 * S, 12 * S, 50 * MS, lambda t: soc(t, 1500))   # low_soc
    L += ["mode %d 0 500" % (7 * S)]
    L += ["mode %d 3 500" % (7100 * MS)]
    L += ["end %d" % (13 * S)]
    return sorted_directives(L)


@scenario("arm-gate-torque-only", """
Spec 7 condition 6 requires BOTH halves: gen_rpm_ref at its engine-off null AND
gen_torque_cmd zero. Nothing presented the second half on its own, so the
rpm_ref check always blocked first and removing the torque check entirely
changed no trace in the suite (round-4 mutation R09).

Not hypothetical. Spec 4.1 records real frames with B0 = 0x08 and non-zero
torque, and spec 7.1's unresolved note is about exactly this state -- B0 0x0B
spanning motoring and generating, separated by the SIGN of the torque, with the
present test blocking both.

EXPECT: rpm_ref at the null (-1) with torque +200 blocks on "VCM commanding
torque" -- NOT on "VCM requesting engine", which would mean the other half
caught it -- and the device goes live once torque returns to 0.
""", autobms=False)
def s_arm_gate_torque_only():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 4 * S, 20 * MS, torque=200, rpm_ref=-1)
    L += cmd_train(4 * S, 7 * S, 20 * MS, torque=0, rpm_ref=-1)
    L += _healthy_bg(1 * S, 7 * S)
    L += ["end %d" % (8 * S)]
    return sorted_directives(L)


@scenario("arm-gate-torque-negative", """
The other sign, which spec 7.1 says is the one the interlock was written for:
negative torque is a LOADED, generating machine, and taking that over with a
zero command sheds the engine's whole load in one frame.

EXPECT: identical blocking to arm-gate-torque-only. The pair exists because
spec 7.1 records that the sign distinction is unresolved -- if the test is ever
made sign-aware, these two scenarios are what will show which way.
""", autobms=False)
def s_arm_gate_torque_negative():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 4 * S, 20 * MS, torque=-200, rpm_ref=-1)
    L += cmd_train(4 * S, 7 * S, 20 * MS, torque=0, rpm_ref=-1)
    L += _healthy_bg(1 * S, 7 * S)
    L += ["end %d" % (8 * S)]
    return sorted_directives(L)

def sorted_directives(lines):
    """Stable-sort directive lines by their timestamp field.

    Scenarios are built by concatenating independent frame trains, so they
    arrive interleaved out of order. The runner requires ascending time. Sort
    is stable, so frames written at the same timestamp keep the order the
    scenario declared -- which is itself part of what a trace pins.
    """
    def key(item):
        i, ln = item
        p = ln.split()
        # EVERY directive keyword must be listed here. One that is not gets
        # the -1 key below and sorts to the FRONT of the file, ahead of the
        # opening `mode` line -- and host_runner applies directives in file
        # order, so everything behind it waits for its timestamp. A txstall at
        # 3 s silently delayed arming from 0 s to 3 s before this was fixed,
        # and the scenario still "worked", just not as written.
        if p[0] in ("f", "mode", "bus", "txfail", "txstall", "txdone", "end"):
            return (int(p[1]), i)
        return (-1, i)
    return [ln for _, ln in sorted(enumerate(lines), key=key)]


def _frame_span(lines):
    """(first, last) timestamp across the scenario's FRAMES.

    Frames, not directives, and that distinction is load-bearing. The key
    train must not outlive the rest of the traffic:

      - physically, 0x592 comes from the VCM, which is the same module as the
        HCU and the same transmitter as 0x051, so the key stops when the
        command stops. A key train running into the trailing silence is a bus
        the truck cannot produce.
      - mechanically, the host runner consumes one frame per loop iteration
        and ticks once per iteration, so frames arriving during a silence
        make the interlocks evaluate on a finer grid than the 200 ms receive
        timeout gives. That silently re-times every trailing abort, and in
        one case (arm-gate-order-rpm-first) let the gate pass in a window
        where 0x054 had gone stale but 0x051 had not -- a real behaviour of
        the unchanged rules, but not the behaviour the scenario was written
        to pin.
    """
    ts = [int(ln.split()[1]) for ln in lines if ln.split()[0] == "f"]
    return (min(ts), max(ts)) if ts else (0, 0)


# The background interlock signals, and the cadence each is injected at. The
# rates are the measured ones (review B3/B4, four clean captures) rather than
# round numbers, because 0x617 at 4 Hz has the least margin of any of them
# against the 0.5 s freshness window and a scenario should not be quietly
# kinder to the core than the truck is.
BACKGROUND = (
    (0x440, 50 * MS,  lambda t: contactor(t, 11)),   # ~20 Hz measured
    (0x411, 50 * MS,  lambda t: soc(t, 5000)),       # ~20 Hz measured
    (0x617, 250 * MS, lambda t: fault(t, 0xC8)),     # 4 Hz measured
    (0x639, 50 * MS,  lambda t: shift(t, 2)),        # 100 Hz; 20 Hz keeps
)                                                    # traces small


def add_autobms(lines):
    """Span the scenario's traffic with healthy interlock signals.

    Both signals get a TRAIN, not a single frame, and that is not a detail: the
    spec 6.2 marker clears when EITHER 0x411 or 0x440 stops being fresh, so one
    0x411 at the start goes stale after 0.5 s and the marker then clears on
    every tick and re-sets on every 0x440 -- a 20 Hz flap through the whole
    trace. The first version of this function did exactly that.

EACH signal is SKIPPED when the scenario sends that ID of its own, which is
    the only workable rule: a scenario that drives a signal owns it, and an
    injected healthy train would fight it. low-soc-debounce counts CONSECUTIVE
    sub-threshold 0x411 readings, so a healthy train at 20 Hz would reset the
    count every 50 ms and the release could never latch; vcm-fault would never
    see 0xCA; m-mode-latch would never see position 4. Such a scenario is then
    responsible for keeping that signal fresh for as long as it wants the
    inhibit live, which is itself worth having visible in its trace.

    0x617 and 0x639 joined the list on 2026-09-24: review B3 made them arm-gate
    conditions 3 and 4, so a scenario without them never goes live, exactly as
    with 0x592 and 0x440 before them. The scenarios that already drove them
    happen to do so at 200 ms, inside the window, so none of them needed
    changing.

    The 0x411 frames are appended AFTER the 0x440 train so that, at a shared
    timestamp, the stable sort puts the contactor frame first. That is the order
    the marker needs: soc_valid is set by the 0x440, and only then does an
    0x411 set soc_since_valid. 5000 raw = 50.00 %, well above the 21.00 %
    threshold and not a value any scenario drives to.
    """
    t0, t1 = _frame_span(lines)
    own = {p[2] for p in (ln.split() for ln in lines)
           if len(p) > 2 and p[0] == "f"}
    out = list(lines)
    for ident, period, fn in BACKGROUND:
        if ("%03X" % ident) in own:
            continue
        out += periodic(t0, t1 + 1, period, fn)
    return sorted_directives(out)


def add_autokey(lines):
    """Span the scenario's traffic with a key-ON train. See scenario().

    Appended, then re-sorted: the sort is stable, so a key frame sharing a
    timestamp with an existing line lands AFTER it, which makes the result
    deterministic.
    """
    t0, t1 = _frame_span(lines)
    return sorted_directives(lines + key_train(t0, t1 + 1, on=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="scenarios")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name in sorted(SCEN):
            why = SCEN[name][0].strip().splitlines()[0]
            print("%-28s %s" % (name, why))
        return

    os.makedirs(args.out, exist_ok=True)
    for name in sorted(SCEN):
        why, fn, autokey, autobms = SCEN[name]
        lines = fn()
        if autobms:
            lines = add_autobms(lines)
        if autokey:
            lines = add_autokey(lines)
        path = os.path.join(args.out, name + ".scn")
        with open(path, "w", newline="\n") as fh:
            fh.write("# %s\n#\n" % name)
            for ln in why.strip().splitlines():
                fh.write("# %s\n" % ln)
            fh.write("#\n")
            if autokey:
                fh.write("# KEY: a 10 Hz 0x592 key-ON train spanning the whole"
                         " scenario was injected\n"
                         "# automatically (spec 7.1 gates transmission on the"
                         " key, and never-seen\n"
                         "# reads off). Nothing below asserts on the key.\n#\n")
            else:
                fh.write("# KEY: this scenario builds its own 0x592 traffic.\n#\n")
            if autobms:
                fh.write("# BACKGROUND: healthy 0x440 (closed), 0x411"
                         " (50.00%), 0x617 (no fault) and\n"
                         "# 0x639 (position 2) trains spanning the whole"
                         " scenario were injected\n"
                         "# automatically, EXCEPT any of those IDs that appear"
                         " below -- a scenario\n"
                         "# that drives a signal owns it. All four gate going"
                         " live (spec 6.2, spec 7\n"
                         "# conditions 2-4). Nothing below asserts on the"
                         " injected ones.\n#\n")
            else:
                fh.write("# BACKGROUND: this scenario builds its own"
                         " 0x440/0x411/0x617/0x639 traffic.\n#\n")
            for ln in lines:
                fh.write(ln + "\n")
        print("wrote %-40s %5d lines" % (path, len(lines)))


# ---------------------------------------------------------------------------
# Spec 12.4: the whole-bus dropout row, and 0x617's own freshness window.
# ---------------------------------------------------------------------------

def _dropout(gap_us, fault_lead_us, fault_resume_us):
    """Every signal stops for `gap_us` at t = 3 s, then all resume.

    The two `fault_*` arguments place 0x617's PHASE either side of the gap,
    which is the whole point of this family. 0x617 runs at 4 Hz, so its total
    absence is the gap plus however long before it the last frame fell plus
    however long after the bus returns the next one takes -- up to 250 ms at
    each end. A window that only just covers the gap itself is therefore not
    enough, and the failure depends on nothing the device can see or control.

      fault_lead_us    the last pre-gap 0x617 lands this long BEFORE the stop
      fault_resume_us  the first post-gap 0x617 lands this long AFTER the
                       bus returns
    """
    T = 3 * S                       # the bus stops
    T2 = T + gap_us                 # the bus returns
    END = T2 + 3 * S
    L = ["mode 0 3 500"]

    L += cmd_train(1 * S, T, 20 * MS)
    L += cmd_train(T2, END, 20 * MS)
    L += contactor_train(1 * S, T)
    L += contactor_train(T2, END)
    L += periodic(1 * S, T, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(T2, END, 50 * MS, lambda t: soc(t, 5000))
    L += periodic(1 * S, T, 50 * MS, lambda t: shift(t, 2))
    L += periodic(T2, END, 50 * MS, lambda t: shift(t, 2))
    L += key_train(1 * S, T, on=True)
    L += key_train(T2, END, on=True)

    # 0x617 backwards from its placed last pre-gap frame, so the phase is
    # exact rather than whatever a forward train happens to land on.
    t = T - fault_lead_us
    while t >= 1 * S:
        L.append(fault(t, 0xC8))
        t -= 250 * MS
    L += periodic(T2, END, 250 * MS, lambda t: fault(t, 0xC8),
                  phase=fault_resume_us)

    L += ["end %d" % (END + 1 * S)]
    return sorted_directives(L)


@scenario("bus-dropout-450-fault-aligned", """
A 450 ms whole-bus dropout with 0x617 arriving right up to the stop and again
right after the return -- the kindest phase for it.

EXPECT: no abort at all. 0x051's gap is under its own 0.5 s window, so the link
never reads lost, and every other signal is fresh again before the first 0x051
comes back. The easy case, kept as the control for the two below: if this one
ever aborts, the dropout family is measuring something other than phase.
""", autokey=False, autobms=False)
def s_bus_dropout_450_aligned():
    return _dropout(450 * MS, 0, 0)


@scenario("bus-dropout-450-fault-worst-phase", """
The same 450 ms dropout with 0x617's phase against us at BOTH ends: its last
frame 240 ms before the stop, its first 240 ms after the return. Total absence
~930 ms.

EXPECT: still no abort. This is the case that decided 0x617's window (user,
2026-09-25). On the old single 0.5 s window a dropout as short as 300 ms
latched the inhibit off for the rest of the key cycle AND blamed 0x617 -- the
old bus-glitch-short golden recorded exactly that, aborting with "0x617 went
stale while live". The evidence rule did not save it, because on the return
0x051 comes back within 20 ms while 0x617 can take another 250, and in that
window 0x617 is genuinely stale with a live bus to prove it.

930 ms against a 1000 ms window is a 70 ms margin, and that is the design
margin rather than slack: spec 7 puts the ride-through bound at 0x051's own
0.5 s window, so this is meant to be tight. If a future change moves either
number this scenario is the one that should fail.
""", autokey=False, autobms=False)
def s_bus_dropout_450_worst():
    return _dropout(450 * MS, 240 * MS, 240 * MS)


@scenario("bus-dropout-600-reports-link", """
A 600 ms dropout -- past 0x051's own 0.5 s window -- with 0x617's phase still
against us.

EXPECT: a latched abort naming the LINK (trip 2, "bus lost"), never a stale
0x617. This is the other half of the ruling: the wider window is not there to
hide a real bus loss, it is there so the device blames the right thing. 0x617's
total absence here is ~1080 ms, so it IS stale by its own window -- and trip 2
must still win, because 0x051 expired first and a link that has gone down is
the honest report.
""", autokey=False, autobms=False)
def s_bus_dropout_600_link():
    return _dropout(600 * MS, 240 * MS, 240 * MS)


# ---------------------------------------------------------------------------
# Spec 12.4: the GENE family, trips 3 and 5.
# ---------------------------------------------------------------------------

@scenario("gene-family-quiet-together", """
0x054 and 0x471 both arrive, then BOTH stop while 0x051 keeps running.

EXPECT: a latched abort naming the INVERTER (trip 3), not a stale 0x054.

WHY THIS SCENARIO HAD TO BE WRITTEN. Neither existing scenario constrained the
order, and that is why the bug was invisible: inverter-lost sends 0x471 and no
0x054 at all, so trip 5 cannot fire in it; stale-rpm-once-heard sends 0x054 and
no 0x471, so trip 3 cannot fire in it. Each tested one trip with the other
structurally unreachable, and both passed under either ordering.

On the truck the two always go together -- same module, both ~100 Hz -- so they
expire within a tick of each other and whichever test ran first won. The
reported reason therefore depended on nothing but which frame happened to
arrive last, and the two identical end-of-drive events in replay-rekey-long
reported one each, from the same capture, for the same physical event.
""", autobms=False)
def s_gene_family_together():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += _healthy_bg(1 * S, 8 * S)
    L += periodic(2 * S, 5 * S, 100 * MS, fb)
    L += periodic(2 * S, 5 * S, 100 * MS, lambda t: gene(t, 0))
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


@scenario("gene-rpm-stale-while-fb-fresh", """
0x054 and 0x471 both arrive, then 0x054 alone stops -- 0x471 keeps running at
full rate to the end.

EXPECT: a latched abort naming 0x054 (trip 5, "generator speed lost").

This is the other half of the rule and the reason it is an ordering and not
simply "always report trip 3". The inverter is demonstrably still there, so
saying it was lost would be wrong; what has gone is the speed signal alone.
Together with gene-family-quiet-together this pins both directions, which is
what the single-signal scenarios could not do.
""", autobms=False)
def s_gene_rpm_stale_fb_fresh():
    L = ["mode 0 3 500"]
    L += cmd_train(1 * S, 8 * S, 20 * MS)
    L += _healthy_bg(1 * S, 8 * S)
    L += periodic(2 * S, 8 * S, 100 * MS, fb)                   # 0x471 stays
    L += periodic(2 * S, 5 * S, 100 * MS, lambda t: gene(t, 0))  # 0x054 stops
    L += ["end %d" % (9 * S)]
    return sorted_directives(L)


# ---------------------------------------------------------------------------
# E4 / spec 5: the loaded bus. The model is OFF unless a scenario says `load`,
# so these are the only scenarios whose timing it touches.
# ---------------------------------------------------------------------------

# The RX depth the scenarios model is the depth the FIRMWARE installs: spec 5.1
# item 1, can.c's CAN_RX_QUEUE_LEN. They said 5 while the firmware said 5, and
# they have to move together or the model stops describing the device. The
# depth-5 behaviour is not lost -- load_margin.py sweeps depths, which is where
# a comparison between them belongs.
RX_DEPTH = 32


def filler(t, n=8):
    """Traffic the core reads as nothing, to occupy the wire and the RX queue.

    0x3FF is in none of the sets the inhibitor reads, so these frames cost
    exactly what the truck's other ~2000 fps costs: air time on the wire and a
    slot in a 5-deep RX queue. That is the whole mechanism of spec 5's receive
    hazard, and it does not need the frames to mean anything.
    """
    return _f(t, 0x3FF, [0] * n)


def cmd_train_jittered(t0, t1, ctr0=0):
    """0x051 at the truck's MEASURED spread, not a fixed period.

    Spec 5: the inter-arrival range is 4.69-16.4 ms, median 9.4-10.5 ms. A fixed
    period is the wrong model for this harness in a specific and costly way --
    it PHASE-LOCKS the command train against everything else. With diag and
    0x051 both on 10 ms, the gap between them is constant for the whole run, so
    a collision between them either never happens or always does. Raising the
    diag rate to "sample it more often" (the first attempt here) makes it worse,
    because equal periods lock exactly.

    On the truck the relative phase drifts continuously, so our diag page lands
    inside an 0x051's air time about 3 % of the time. The gap pattern below is
    fixed and repeating -- no RNG, so the scenario stays reproducible -- but its
    period is incommensurate with every other train, which reproduces the drift.
    """
    gaps = [4690, 9400, 16400, 10500, 6300, 13100, 8200, 11900]
    L, t, ctr, i = [], t0, ctr0, 0
    while t < t1:
        L.append(cmd(t, ctr & 0x0F))
        ctr += 1
        t += gaps[i % len(gaps)]
        i += 1
    return L


def _at_truck_rate(t0, t1, cmd_period=10 * MS):
    """0x051 + interlocks + filler, totalling ~2250 fps (section 9.3).

    EVERY TRAIN IS PHASED, and the first version was not. Starting them all on
    exact multiples put NINE frames on the same microsecond at t0 and eight at
    every 100 ms after it, which overflowed a 5-deep queue on its own: the
    control scenario reported 102 drops with no preemption at all.

    That is a model wrong in the same direction as the defect -- the trap this
    suite keeps rediscovering. Had only the preemption scenario been read, spec
    5's receive hazard would have looked confirmed by a mechanism the truck does
    not have. Real ECUs are independent and unsynchronised; the offsets below
    are deliberately not multiples of each other, so the trains drift against
    one another the way measured traffic does.
    """
    L = []
    L += cmd_train_jittered(t0, t1)
    L += periodic(t0, t1, 50 * MS, lambda t: contactor(t, 11), phase=3 * MS)
    L += periodic(t0, t1, 50 * MS, lambda t: soc(t, 5000), phase=7 * MS)
    L += periodic(t0, t1, 50 * MS, lambda t: shift(t, 2), phase=11 * MS)
    L += periodic(t0, t1, 250 * MS, lambda t: fault(t, 0xC8), phase=17 * MS)
    L += periodic(t0, t1, 100 * MS, fb, phase=23 * MS)
    L += periodic(t0, t1, 100 * MS, lambda t: gene(t, 0), phase=29 * MS)
    L += periodic(t0, t1, 100 * MS, lambda t: key(t, True), phase=31 * MS)
    # Everything above is ~250 fps. Fill to ~2250.
    L += periodic(t0, t1, 500, filler, phase=211)
    return L


@scenario("load-truck-rate-holds", """
E4 / spec 5. ~2250 fps -- the truck's measured powertrain load (section 9.3) --
through a 5-deep RX queue and a single-buffer FIFO TX, with diag interleaved.
No preemption.

EXPECT: rx_dropped=0 and tx_late_past_next=0. Every inhibit frame answers its
0x051 inside the VCM's next-frame deadline even with the wire ~56 % occupied.

THIS IS THE CONTROL, and it is the more important half. Spec 5's numbers were
all taken on a near-idle bus (~100 fps, VCM-only) and review A4 flagged that as
not the truck. If this scenario ever starts dropping or running late, the
question "does the reactive trail hold at load" has changed its answer -- and
the E4 line in the output is where it says so.

A green run here is NOT proof about the truck. The model has no arbitration, no
ISR, and no error frames; review A4 is explicit that the full-replay bench run
is what calibrates it.
""", autokey=False, autobms=False)
def s_load_truck_rate():
    L = ["mode 0 3 500", "load 0 %d 500" % RX_DEPTH]
    L += _at_truck_rate(1 * S, 4 * S)
    L += ["end %d" % (5 * S)]
    return sorted_directives(L)


@scenario("load-preempt-rides-through", """
E4 / spec 5, the receive hazard made to happen. The same truck load, with the
worker off-CPU for 2.4 ms -- the measured worst WiFi-preemption delay is
2.39 ms (spec 5).

EXPECT: nothing dropped, nothing late, at a queue high-water mark of about 6
of the 32 slots spec 5.1 item 1 installs.

THIS SCENARIO HAS BEEN RENAMED TWICE, and both renames were the point. It began
as "loses-command" and did not reliably lose one; it became
"overflows-queue" and did overflow, at the shipped depth of 5; with the spec 5.1
fix it overflows nothing, so it is now named for riding through. A golden that
passes while demonstrating something other than its title is the failure mode
this suite keeps rediscovering, and a name is the cheapest place to keep honest.

THE ARITHMETIC IS THE POINT, and it is spec 5's own: the RX queue is 5 frames
deep, which at 2250 fps is ~2.2 ms of buffer, against a 2.39 ms worst
preemption. The margin is negative. A dropped 0x051 is one the device never
answered, so the inverter acted on the VCM's torque for that slot -- and
nothing in the device's own counters distinguishes that from a slot where the
VCM was not commanding torque at all.

WHAT IT MEASURED BEFORE THE FIX, kept because it is the reason the fix exists.
At ~2250 fps five slots fill in ~2.5 ms of surrounding traffic, so a 2.39 ms
preemption overflowed by well under a single frame: exactly one lost, and WHICH
one a phase lottery across ~2000 fps of other traffic and ~100 fps of 0x051 --
roughly one time in twenty the command itself. Intermittent is worse news than
deterministic, not better: an occasional unanswered frame is the kind that gets
attributed to anything else.

At depth 32 the same preemption is absorbed with 26 slots to spare. This
scenario now exists to fail loudly if the depth is ever reduced, and
load_margin.py exists because "clean at 2.39 ms" is one point and not a margin.
""", autokey=False, autobms=False)
def s_load_preempt():
    L = ["mode 0 3 500", "load 0 %d 500" % RX_DEPTH]
    frames = _at_truck_rate(1 * S, 4 * S)
    L += frames

    """
    THE WINDOW IS PLACED AGAINST A REAL 0x051 ARRIVAL, and the phase is chosen
    deliberately rather than left to luck. Both halves of that need saying.

    The arithmetic is MARGINAL by design of the hardware, not of this test: five
    queue slots at ~2250 fps is ~2.2 ms of buffer against a measured worst
    preemption of 2.39 ms. That is an overflow of roughly a third of one frame.
    So whether anything is lost at all -- and whether the lost frame is the
    0x051 or one of the ~2000 fps of traffic around it -- depends entirely on
    where in the phase the preemption falls. An earlier version of this scenario
    put the window at a round 2.5 s and reported zero drops, which would have
    read as "the buffer is sufficient". It is not; it is marginal.

    So the window starts 2.2 ms before a command and ends just after it: the
    queue fills on the surrounding traffic, and the command arrives to a full
    queue. That is a real phase the truck will hit, not a manufactured one, and
    pinning it makes the scenario deterministic instead of a coin toss.

    What this does NOT claim is a rate. How often the truck lands on this phase
    is a question for the bench run (review A4), not for a model.
    """
    cmd_t = None
    for line in frames:
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "f" and parts[2].upper() == "051":
            t = int(parts[1])
            if t >= 2500 * MS:
                cmd_t = t
                break
    assert cmd_t is not None, "no 0x051 after 2.5 s to place the preemption against"

    L += ["preempt %d %d" % (cmd_t - 2200, cmd_t + 190)]
    L += ["end %d" % (5 * S)]
    return sorted_directives(L)


@scenario("load-diag-delays-inhibit", """
E4 / spec 5, the transmit-ordering hazard. Truck load with the model on, run
long enough for the 300 ms diag round-robin to put a page in the TX buffer just
as an inhibit is queued behind it.

EXPECT: tx_behind > 0 on the E4 line -- our own diag frame delaying our own
inhibit, which is exactly what spec 5 describes and what `tx_queued_behind`
counts on the device.

WHY IT MATTERS THAT THIS IS COUNTED AND NOT ABORTED: being behind is a timing
hazard, not a failure. Whether it cost anything shows up as tx_ok not rising or
as a trip-7 abort. A zero here would read as "hazard absent", which is the worst
way for a measurement to fail -- and before E4 this harness hard-coded `behind`
to false, so it read zero always.
""", autokey=False, autobms=False)
def s_load_diag_behind():
    # 20 ms diag, not the shipped 300 ms. At 300 ms a page lands inside one
    # frame's air time of an 0x051 roughly 3 % of the time, so a 5 s run expects
    # well under one occurrence and would pass by observing nothing. The hazard
    # is not rate-dependent -- only how often it is SAMPLED -- so the rate is
    # raised to sample it. 20 ms rather than 10: with the command train
    # jittered, an equal period no longer locks, but a period that is a clean
    # divisor of the median gap still under-samples the drift.
    L = ["mode 0 3 500", "load 0 %d 500" % RX_DEPTH, "cfg diag_period_ms 20"]
    L += _at_truck_rate(1 * S, 6 * S)
    L += ["end %d" % (7 * S)]
    return sorted_directives(L)


if __name__ == "__main__":
    main()
