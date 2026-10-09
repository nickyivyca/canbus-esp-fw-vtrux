"""Rules the core must never break, checked over every trace and over
randomised traffic -- with no golden file anywhere.

Review item C1. The Python/C++ differential is the strongest test here and it
has a structural blind spot: it compares the two cores against each other, so
a mistake both of them make is invisible to it. B-7a was in both. The silent
session boundary was in both. The goldens have the same shape -- they record
what the core did, so a wrong behaviour becomes the expected behaviour as
soon as it is regenerated.

These checks do not know what the core did last time. They state what it must
never do, from spec 2, 4, 4.1 and 5.3, and are evaluated against whatever it
does now:

  1. every frame in is forwarded to the other port exactly once, unchanged
     except for the section 4 rewrites;
  2. nothing is rewritten unless the core is overriding;
  3. no repeat burst exceeds 20 frames, and a burst that is not cut short by
     the VCU's own page 00 (or a session boundary) delivers exactly 20;
  4. the core never originates a command of its own -- no STAND_BY, no
     EXPORT, nothing the VCU has not commanded or that section 4 would not
     rewrite it to (spec 5.3);
  5. while overriding, every page 01 toward the charger carries the BMS's
     permission capped by the EVSE (spec 4) -- never above either -- with the
     VCU's other bytes untouched.

Rev 2 (charge-interposer tester, 2026-10-07). Rev 1 judged rule 5 against the
core's OWN beliefs (core.chg_max, core._evse_cap_ca()), so a core that kept a
stale permission agreed with itself, and it only looked at page 01 when the
core had changed it, so an override that forwarded the VCU's 19.8 A untouched
was never checked. The limits now come from an Oracle that decodes the bus the
core was handed, nothing else; the reading of spec 4 / 9 behind it is the
reviewer's (tracker section F, item 6): the latest 0x420 permission and the
latest EVSE cap received before each page 01, within one page-01 count, and
during an override every page 01 is rewritten. Also from rev 2:
  - the burst length is the spec's literal 20, not the core's Config;
  - only a VCU *page 00* cuts a repeat short (spec 4.1), not any 0x18EFC000;
  - each repeat's mode follows the 4.1 table's direction, and its bytes are
    the VCU's standing page 00 with only the mode changed (reading 3);
  - spec 8 / 8.1 mirrors: vehicle side only, one per modified or repeated
    command frame and carrying exactly what the charger was sent, none for
    unmodified frames, and no telemetry mirror (mask_charger_telemetry is off
    by default and outside the verified design).
test_checker_catches_each_mutation proves each of those can fail.

Rev 3 (tester, 2026-10-08, plan 1b), for spec text written 2026-10-07:
  - spec 4: during an override, pages 03.02 / 03.07 reach the charger as
    the fixed held values (HELD), whatever the VCU sent;
  - spec 4.1: each gap between a repeat's frames is 50 +/- 10 ms, judged
    only where the steps were dense enough to let a compliant core comply
    (Checker._check_gaps).
Neither can be judged on the traces as recorded (100 ms ticks, no 03.02 /
03.07 in any override), so densified() replays them with 2-10 ms ticks and
injected page 03 frames; test_hold_and_gaps_over_densified_traces guards the
population and test_checker_catches_hold_and_gap_mutations proves both can
fail. The randomised traffic also carries 03.02 / 03.07 now, from its own
generator so the main stream -- and the path the core walks -- is unchanged.

The randomised sequence matters as much as the traces. Every trace here was
generated to exercise something, so all of them are well-formed charges; the
random one is not, and the invariants hold on malformed traffic too or they
are not invariants.

Run:  py -3.14 projects/vtrux/tools/interposer/test_invariants.py
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import machine as M
import protocol as P

import paths                                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES = paths.fixtures()               # SeaDrive, via VTRUX_DATA

FAILS = []


def ok(cond, name):
    if not cond:
        FAILS.append(name)


# Modes the core is allowed to put on the wire toward the charger. Spec 5.3:
# the only frames it transmits that the VCU did not just send are repeats of
# the VCU's standing page-00 command, and section 4 rewrites Low Power to
# CHARGER. STAND_BY and EXPORT are never ours to send.
ALLOWED_REPEAT_MODES = (P.MODE_CHARGER, P.MODE_LOW_POWER)

# Spec literals. Taken from the spec, never from machine.Config, so a core
# whose Config drifted cannot move the goalposts it is measured against.
BURST = 20                       # spec 4.1: "20 frames at 50 ms"
CC_MIN_A = 5.0                   # spec 4: a CC command is a setpoint >= 5 A
PAGE01_COUNT_A = P.UNIT          # one page-01 count, the tolerance (item 6)
CHG_SILENCE_MS = 20000           # spec 6.1 charger-silence boundary
MIRROR_CMD = 0x18EFC0E1          # spec 8.1: SA replaced by 0xE1
MIRROR_HV = 0x18FFD7E1
REPEAT_GAP_MS = (40, 60)         # spec 4.1: "each gap ... 50 ms +/- 10 ms"
GAP_STEP_MS = 10                 # step density at which a gap is judged
# spec 4: pages 03.02 / 03.07 held at these during an override (user,
# 2026-10-07), whatever the VCU sends
HELD = {0x02: bytes.fromhex("0302000000000000"),
        0x07: bytes.fromhex("0307026404320000")}


class Oracle(object):
    """What the bus has told the core, decoded independently of the core.

    Tracks the inputs spec 4 builds the override's command from -- the BMS's
    permission (0x420 bcm_chg_max), the charger's DC-side
    maxAvailableChargingCurrent, and the VCU's learned CC command -- plus the
    VCU's standing page 00 and the spec 6.1 session boundaries that reset the
    learned values. Nothing here reads the core.
    """

    def __init__(self):
        self.perm_a = None
        self.cap_raw_a = None
        self.cc_a = None            # learned CC, latest setpoint >= 5 A
        self.cc_prev_a = None       # the value before the current frame
        self.cc_first_a = None      # first CC since the last boundary
        self.cc_max_a = None        # largest CC since the last boundary
        self.p00 = None             # the VCU's standing page 00, raw bytes
        self.pilot = None
        self.chg_t = None
        self.silent = False
        self.boundary = False       # a 6.1 boundary on THIS step

    def observe(self, kind, t, fid, data):
        self.boundary = False
        self.cc_prev_a = self.cc_a
        if kind == "V":
            if fid == M.BMS_LIMITS:
                d = P.decode(P.EPRI, 0x420, data)
                if "bcm_chg_max" in d:
                    self.perm_a = float(d["bcm_chg_max"])
            elif fid == M.CMD_ID and len(data) >= 3 and data[0] == 0x00:
                if data[2] in (P.MODE_STANDBY, P.MODE_EXPORT):
                    self._reset()
                self.p00 = bytes(data)
            elif fid == M.CMD_ID and len(data) >= 5 and data[0] == 0x01:
                _v, amps = P.dec_setpoint(data)
                if amps >= CC_MIN_A:
                    self.cc_a = amps
                    if self.cc_first_a is None:
                        self.cc_first_a = amps
                    if self.cc_max_a is None or amps > self.cc_max_a:
                        self.cc_max_a = amps
        elif kind == "C":
            if fid == M.CHG_INFO:
                d = P.decode(P.BEL, 0x18FFD9C0, data)
                if "BELINV_maxAvailableChargingCurrent" in d:
                    self.cap_raw_a = float(
                        d["BELINV_maxAvailableChargingCurrent"])
            elif fid == M.CHG_PILOT and len(data) >= 8:
                m = data[5] | (data[6] << 8) | (data[7] << 16)
                if self.pilot is not None and m < self.pilot:
                    self._reset()
                self.pilot = m
            elif fid == M.CHG_STATUS:
                self.chg_t = t
                self.silent = False
        else:
            if (self.chg_t is not None and not self.silent
                    and t - self.chg_t >= CHG_SILENCE_MS):
                self.silent = True
                self._reset()

    def _reset(self):
        self.boundary = True
        self.cc_a = None
        self.cc_prev_a = None
        self.cc_first_a = None
        self.cc_max_a = None
        self.cap_raw_a = None

    def caps(self):
        """Every EVSE cap the spec text allows for this frame.

        The cap itself is the latest maxAvailableChargingCurrent (item 6).
        WHICH CC command is "the VCU's own learned CC command" (spec 4) is
        not stated -- the latest before this frame, the latest including it
        if it is itself >= 5 A, the first since the boundary, or the largest
        -- and is with the reviewer (raised 2026-10-07). Until it is ruled
        every one of those readings is accepted; a value matching none of
        them still fails, and so does anything above the permission or above
        the latest maxAvailableChargingCurrent."""
        if self.cap_raw_a is None:
            return []
        out = []
        for cc in (self.cc_prev_a, self.cc_a, self.cc_first_a,
                   self.cc_max_a):
            c = self.cap_raw_a if cc is None else min(self.cap_raw_a, cc)
            if c not in out:
                out.append(c)
        return out


class Checker(object):
    """Replays steps through a core and checks the five rules as it goes.

    `mutate`, when given, is called as mutate(checker, kind, arb_id, data,
    out) on every step and returns the output list to judge instead of the
    core's. It exists for test_checker_catches_each_mutation only: a wrong
    core, built without touching machine.py, that each rule must reject.
    """

    def __init__(self, label, cfg=None, mutate=None):
        self.label = label
        self.cfg = cfg or M.Config()
        self.core = M.InterposerCore(self.cfg, 0)
        self.bus = Oracle()
        self.mutate = mutate
        self.problems = []
        self.n_steps = 0
        self.n_repeat_frames = 0
        self.n_overrode = 0
        self.n_page01_checked = 0
        self.n_mirrors = 0
        self.n_held = 0             # override 03.02 / 03.07 judged
        self.n_held_differed = 0    # ... of which the VCU sent other bytes
        self.n_gaps_judged = 0      # repeat gaps judged against 50 +/- 10
        self.n_gaps_unjudged = 0    # ... and not: no step offered a send
        self.step_t = []            # every step's time, for the gap rule
        self._t = 0
        self.bursts = []            # {"mode", "sent", "closed", "cut"}
        self._new_bursts = []
        self._step_kind = None
        self._step_before = None
        # Every step in make_golden.py's trace format, so the same sequence
        # can be replayed through machine.cpp. These rules are checked
        # against machine.py; recording lets host_diff/make_unit_traces.py
        # put the randomised traffic in front of the port as well.
        self.trace = []
        # The hook below only SEGMENTS the repeat frames into bursts (where
        # one starts); core._burst_left says when the core has nothing left
        # to send. Whether a burst was legitimately cut short is decided from
        # the bus (a VCU page 00, a new burst, or an Oracle boundary), not
        # from the core. Wrapping beats reading core.events: the event list
        # is capped at MAX_EVENTS, so on a long trace the early bursts fall
        # off the end and a check built on it would silently stop checking.
        self._orig_repeat = self.core._repeat_master
        self.core._repeat_master = self._on_repeat

    def _on_repeat(self, t_ms, mode):
        self._close_burst(cut=True)        # replaced (spec 4.1)
        # the core's own mode is kept for messages only; the direction rule
        # is judged on the mode the burst's frames carry on the wire
        self.bursts.append({"mode": mode, "sent": 0, "closed": False,
                            "cut": False, "kind": self._step_kind,
                            "before": self._step_before})
        self._new_bursts.append(self.bursts[-1])
        self._orig_repeat(t_ms, mode)

    def _close_burst(self, cut):
        if self.bursts and not self.bursts[-1]["closed"]:
            self.bursts[-1]["closed"] = True
            self.bursts[-1]["cut"] = cut

    def fail(self, msg):
        if len(self.problems) < 12:
            self.problems.append("%s: %s" % (self.label, msg))

    # -- the step ----------------------------------------------------------
    def step(self, kind, t_ms, arb_id=None, ext=None, data=None):
        self.n_steps += 1
        self._new_bursts = []
        self._t = t_ms
        self.step_t.append(t_ms)
        if kind == "T":
            self.trace.append("T %d" % t_ms)
        else:
            self.trace.append("%s %d %X %d %s"
                              % (kind, t_ms, arb_id, 1 if ext else 0,
                                 bytes(data).hex()))
        before = self.core.state
        self._step_kind = kind
        self._step_before = before
        self.bus.observe(kind, t_ms, arb_id, data)

        if kind == "T":
            out = self.core.tick(t_ms)
        elif kind == "V":
            out = self.core.on_vehicle_frame(arb_id, ext, data, t_ms)
        else:
            out = self.core.on_charger_frame(arb_id, ext, data, t_ms)
        if self.mutate is not None:
            out = self.mutate(self, kind, arb_id, data, list(out))
        after = self.core.state
        if after == M.S_OVERRIDE:
            self.n_overrode += 1
        for b in self._new_bursts:
            b["after"] = after

        want_port = (M.TO_CHARGER if kind == "V" else M.TO_VEHICLE)
        fwd = []
        repeats = []
        modified_cmd = []          # 0x18EFC000 to the charger, modified or
        mirrors = []               # repeated: each needs one mirror (8.1)
        for side, fid, _e, payload in out:
            if fid in (MIRROR_CMD, MIRROR_HV):
                mirrors.append((side, fid, bytes(payload)))
                continue
            if kind != "T" and side == want_port and fid == arb_id:
                fwd.append(payload)
                if (len(fwd) == 1 and side == M.TO_CHARGER
                        and fid == M.CMD_ID
                        and bytes(payload) != bytes(data)):
                    modified_cmd.append(bytes(payload))
            elif side == M.TO_CHARGER and fid == M.CMD_ID:
                repeats.append(payload)
                modified_cmd.append(bytes(payload))
            else:
                self.fail("unexpected emission port=%d id=0x%X on a %s step"
                          % (side, fid, kind))

        # --- 1. forwarded exactly once ------------------------------------
        if kind != "T":
            if len(fwd) != 1:
                self.fail("id 0x%X forwarded %d times, not once"
                          % (arb_id, len(fwd)))
            elif fwd[0] != data:
                # --- 2. and only rewritten while overriding ---------------
                if after != M.S_OVERRIDE:
                    self.fail("id 0x%X rewritten in %s, not OVERRIDE"
                              % (arb_id, M.STATE_NAMES[after]))
                elif arb_id != M.CMD_ID:
                    self.fail("id 0x%X rewritten -- only 0x%X may be"
                              % (arb_id, M.CMD_ID))
                else:
                    self._check_rewrite(data, fwd[0])
            # --- 5. during an override every page 01 is ours (item 6) -----
            if (len(fwd) == 1 and kind == "V" and arb_id == M.CMD_ID
                    and len(data) >= 5 and data[0] == 0x01
                    and after == M.S_OVERRIDE):
                self._check_page01(data, fwd[0])
            # --- 4. pages 03.02 / 03.07 held during an override -----------
            if (len(fwd) == 1 and kind == "V" and arb_id == M.CMD_ID
                    and len(data) >= 2 and data[0] == 0x03
                    and data[1] in HELD and after == M.S_OVERRIDE):
                want = HELD[data[1]]
                self.n_held += 1
                if bytes(data) != want:
                    self.n_held_differed += 1
                if bytes(fwd[0]) != want:
                    self.fail("page 03.%02X sent as %s during an override; "
                              "spec 4 holds it at %s (VCU sent %s)"
                              % (data[1], bytes(fwd[0]).hex(), want.hex(),
                                 bytes(data).hex()))

        # --- 3 and 4. the repeat burst ------------------------------------
        for payload in repeats:
            self.n_repeat_frames += 1
            if not self.bursts:
                self.fail("a repeat frame with no burst started")
                break
            self.bursts[-1]["sent"] += 1
            self.bursts[-1].setdefault("times", []).append(t_ms)
            if payload[0] != 0x00:
                self.fail("a repeat frame that is not page 00: %s"
                          % bytes(payload).hex())
                continue
            _flow, mode = P.dec_master(payload)
            b = self.bursts[-1]
            if "wire_mode" not in b:
                b["wire_mode"] = mode
            elif mode != b["wire_mode"]:
                self.fail("mode %d inside a burst that began with mode %d "
                          "-- one burst repeats one command (spec 4.1)"
                          % (mode, b["wire_mode"]))
            if mode not in ALLOWED_REPEAT_MODES:
                self.fail("repeated mode %d -- the core originated a command "
                          "of its own (spec 5.3)" % mode)
                continue
            # 4.1 / reading 3: the VCU's standing page 00 with only the mode
            # changed -- so the flow bit is the VCU's last.
            std = self.bus.p00
            if std is None:
                self.fail("a repeat with no VCU page 00 ever heard -- there "
                          "is no standing command to repeat (spec 4.1)")
            else:
                want = std[:2] + bytes([mode]) + std[3:]
                if bytes(payload) != want:
                    self.fail("repeat %s is not the VCU's standing page 00 "
                              "%s with only the mode changed (spec 4.1)"
                              % (bytes(payload).hex(), std.hex()))
        for b in self.bursts:
            if (not b.get("judged") and "wire_mode" in b
                    and "after" in b):
                b["judged"] = True
                self._check_direction(b["wire_mode"], b["kind"],
                                      b["before"], b["after"])

        # --- 8 / 8.1 mirrors ----------------------------------------------
        want_mirrors = list(modified_cmd)
        for side, fid, payload in mirrors:
            self.n_mirrors += 1
            if side != M.TO_VEHICLE:
                self.fail("mirror 0x%X sent toward the charger -- nothing "
                          "diagnostic is ever sent that way (spec 8)" % fid)
                continue
            if fid == MIRROR_HV:
                self.fail("telemetry mirror 0x%X with mask_charger_telemetry "
                          "off by default (spec 4, 8.1)" % fid)
                continue
            if payload in want_mirrors:
                want_mirrors.remove(payload)
            else:
                self.fail("mirror %s matches no modified or repeated frame "
                          "sent to the charger on this step (spec 8.1)"
                          % payload.hex())
        for payload in want_mirrors:
            self.fail("frame %s modified or repeated toward the charger "
                      "with no mirror (spec 8.1)" % payload.hex())

        # --- burst close-out ----------------------------------------------
        if self.bursts and not self.bursts[-1]["closed"]:
            vcu_p00 = (kind == "V" and arb_id == M.CMD_ID
                       and len(data) >= 1 and data[0] == 0x00)
            if vcu_p00 or self.bus.boundary:
                self.bursts[-1]["cutter"] = True
            if self.core._burst_left == 0:
                self._close_burst(cut=bool(self.bursts[-1].get("cutter")))

    def _check_direction(self, mode, kind, before, after):
        """Spec 4.1 table: CHARGER only for an override decided from a tick,
        Low Power only for a release or a trip out of an override, and in
        both cases the VCU's standing mode is Low Power."""
        std = self.bus.p00
        if std is not None and len(std) >= 3 and std[2] != P.MODE_LOW_POWER:
            self.fail("a mode-%d repeat while the VCU's standing mode is %d, "
                      "not Low Power (spec 4.1)" % (mode, std[2]))
        if mode == P.MODE_CHARGER:
            if not (kind == "T" and before != M.S_OVERRIDE
                    and after == M.S_OVERRIDE):
                self.fail("a CHARGER repeat on a %s step, %s -> %s: only an "
                          "override decided from a tick repeats CHARGER "
                          "(spec 4.1)" % (kind, M.STATE_NAMES[before],
                                          M.STATE_NAMES[after]))
        elif mode == P.MODE_LOW_POWER:
            if not (before == M.S_OVERRIDE
                    and after in (M.S_TERMINATED, M.S_SAFE)):
                self.fail("a Low Power repeat %s -> %s: only a release or a "
                          "trip out of an override repeats Low Power "
                          "(spec 4.1)" % (M.STATE_NAMES[before],
                                          M.STATE_NAMES[after]))

    def _check_rewrite(self, orig, sent):
        """Section 4: pages 00, 01 and 03.02 / 03.07, and nothing else."""
        page = orig[0]
        if page not in (0x00, 0x01, 0x03):
            self.fail("page %02X rewritten; section 4 allows 00, 01, 03 only"
                      % page)
            return
        if page == 0x03 and orig[1] not in (0x02, 0x07):
            self.fail("page 03.%02X rewritten; only 03.02 and 03.07 may be"
                      % orig[1])
            return
        if page == 0x00:
            of, om = P.dec_master(orig)
            sf, sm = P.dec_master(sent)
            if sf != of:
                self.fail("the flow bit was changed: %d -> %d" % (of, sf))
            if om != P.MODE_LOW_POWER:
                self.fail("page 00 mode %d rewritten -- section 4 rewrites "
                          "only Low Power" % om)
            if sm != P.MODE_CHARGER:
                self.fail("page 00 rewritten to mode %d, not CHARGER" % sm)
            if bytes(sent[3:]) != bytes(orig[3:]):
                self.fail("page 00 bytes other than the mode changed: %s -> "
                          "%s" % (bytes(orig).hex(), bytes(sent).hex()))

    def _check_page01(self, orig, sent):
        """Spec 4: page 01's current is ours -- the BMS's permission capped
        by the EVSE -- and only the current changes (item 6 reading)."""
        self.n_page01_checked += 1
        if bytes(sent[:3]) != bytes(orig[:3]) or \
                bytes(sent[5:]) != bytes(orig[5:]):
            self.fail("page 01 bytes other than the current changed: %s -> "
                      "%s" % (bytes(orig).hex(), bytes(sent).hex()))
        perm = self.bus.perm_a
        caps = self.bus.caps()
        _v, amps = P.dec_setpoint(sent)
        if perm is None:
            self.fail("overriding with no 0x420 permission ever received")
            return
        if not caps:
            self.fail("overriding with no EVSE cap known (spec 3)")
            return
        tol = PAGE01_COUNT_A + 1e-6
        wants = [min(perm, c) for c in caps]
        if amps > perm + tol:
            self.fail("commanded %.2f A, above the BMS's permission %.2f A"
                      % (amps, perm))
        elif all(amps > c + tol for c in caps):
            self.fail("commanded %.2f A, above the EVSE cap %s A"
                      % (amps, "/".join("%.2f" % c for c in caps)))
        elif not any(abs(amps - w) <= tol for w in wants):
            self.fail("commanded %.2f A, not the permission %.2f A capped by "
                      "the EVSE %s A (spec 4)"
                      % (amps, perm, "/".join("%.2f" % c for c in caps)))

    def _check_gaps(self):
        """Spec 4.1: each gap between a repeat's frames is 50 +/- 10 ms.

        The core can only send on a step it is given, so a gap is judged
        only where the steps were dense enough that any compliant core
        lands in tolerance: no two consecutive steps more than
        GAP_STEP_MS apart from just before the earlier frame to 60 ms after
        it. Then a core that sends 50 ms after the previous frame, and one
        that sends on a fixed 50 ms grid, are each at most 10 ms late on
        either frame, so the gap is in [40, 60]. Where steps are sparser
        the stimulus decides the gap, not the core: a first version judged
        on "a step was offered in the window" and failed a grid-scheduled
        core at 37-39 ms on steps 15 ms apart. Unjudged gaps are counted,
        not failed, and the judged population is guarded in the tests.
        """
        import bisect
        lo, hi = REPEAT_GAP_MS
        st = sorted(self.step_t)

        def dense(a):
            i = bisect.bisect_left(st, a)
            if i == 0 or a - st[i - 1] > GAP_STEP_MS:
                return False
            k = i
            while st[k] < a + hi:
                if k + 1 >= len(st) or st[k + 1] - st[k] > GAP_STEP_MS:
                    return False
                k += 1
            return True

        for i, b in enumerate(self.bursts):
            ts = b.get("times", [])
            for a, z in zip(ts, ts[1:]):
                if not dense(a):
                    self.n_gaps_unjudged += 1
                    continue
                self.n_gaps_judged += 1
                gap = z - a
                if not lo <= gap <= hi:
                    self.fail("burst %d: repeat frames %d ms apart, outside "
                              "%d-%d with steps every <= %d ms (spec 4.1: "
                              "50 +/- 10 ms)" % (i, gap, lo, hi, GAP_STEP_MS))

    def finish(self):
        self._close_burst(cut=True)       # the run ended; do not judge it
        self._check_gaps()
        for i, b in enumerate(self.bursts):
            if b["sent"] > BURST:
                self.fail("burst %d sent %d frames, more than %d"
                          % (i, b["sent"], BURST))
            elif not b["cut"] and b["sent"] != BURST:
                self.fail("burst %d ran to completion with %d frames, not %d"
                          % (i, b["sent"], BURST))
        return self.problems


def _read_trace(path):
    """-> (Config, [(kind, t_ms, arb_id, ext, data)]) from a trace file."""
    # `K <field> <value>` preamble lines carry the Config the trace was
    # recorded under (host_diff/make_unit_traces.py). Replaying a trace
    # recorded with arm_delay_ms=0 against the 75 s default would simply
    # never arm, so the rules about overriding would go unexercised -- the
    # quiet kind of non-coverage this file exists to avoid.
    cfg = M.Config()
    steps = []
    with open(path, "r") as f:
        for line in f:
            k = line[:1]
            if k == "K":
                name, value = line[1:].split()
                if name not in M.Config.__slots__:
                    raise SystemExit("unknown Config field %r in %s"
                                     % (name, path))
                setattr(cfg, name, int(value))
            elif k == "T":
                steps.append(("T", int(line[1:].strip()), None, None, None))
            elif k in ("V", "C"):
                parts = line[1:].split()
                steps.append((k, int(parts[0]), int(parts[1], 16),
                              parts[2] != "0", bytes.fromhex(parts[3])))
    return cfg, steps


def replay_trace(path):
    cfg, steps = _read_trace(path)
    c = Checker(os.path.basename(path), cfg)
    for st in steps:
        c.step(*st)
    return c


# Pages 03.02 / 03.07 the densified replay injects, in turn: the held bytes,
# the "complete" bytes the VCU flips to at a real top of charge (spec 4,
# charge_M1), and a page 03 the hold does not cover.
PAGE03_CYCLE = (HELD[0x02], bytes.fromhex("0302007500000000"),
                HELD[0x07], bytes.fromhex("0307010000000000"),
                bytes.fromhex("0300010000000000"))


def densified(path, seed=0, mutate=None):
    """Replays a trace with extra ticks and injected page 03 frames.

    Two spec rules cannot be judged on the traces as recorded. They tick
    the core about every 100 ms, so the 4.1 gap rule (50 +/- 10 ms) is
    unjudgeable there: no step is offered inside the window. And no trace
    sends 03.02 or 03.07 during an override, so the spec 4 hold would pass
    by never running. This replay adds ticks 2-10 ms apart in every gap of
    a second or less between the trace's own steps, and a page 03 from
    PAGE03_CYCLE every 150-350 ms. Page 03 is not a mode command, so it
    changes no state that spec 3-6 key on. Longer gaps are left alone so
    that hours-long traces do not become millions of steps.
    """
    rng = random.Random(seed)
    cfg, steps = _read_trace(path)
    c = Checker("dense/" + os.path.basename(path), cfg, mutate=mutate)
    last = None
    next03 = 0
    k = 0
    for st in steps:
        t = st[1]
        if last is not None and 0 < t - last <= 1000:
            u = last + rng.randint(2, GAP_STEP_MS)
            while u < t:
                if u >= next03:
                    c.step("V", u, M.CMD_ID, True,
                           PAGE03_CYCLE[k % len(PAGE03_CYCLE)])
                    k += 1
                    next03 = u + rng.randint(150, 350)
                c.step("T", u)
                u += rng.randint(2, GAP_STEP_MS)
        c.step(*st)
        last = t
    return c


# --- the randomised sequence ----------------------------------------------

RAND_IDS = [M.BMS_STATUS, M.BMS_LIMITS, M.BMS_DATA1, M.BMS_DATA2,
            M.VCM_EVAP, M.CMD_ID]
RAND_CHG_IDS = [M.CHG_STATUS, M.CHG_INFO, M.CHG_PILOT, M.CHG_HV_STATUS]


def _rand_bms(rng, fault_rate):
    """The four BMS messages with randomised but in-range values.

    `fault_rate` is per message, per period. It has to be able to go LOW:
    the core arms only after 75 s of an observed charge with all four
    messages fresh, so at a fault rate of a few percent it is in SAFE
    essentially always and the override half of these rules is never
    reached. A first version used one rate for everything and spent 36,000
    of 54,000 steps latched in SAFE, having never rewritten a frame.
    """
    def f():
        return rng.random() < fault_rate
    return [
        (M.BMS_DATA1, P.encode(P.EPRI, 0x430, {
            "bcm_cell_vmax": rng.uniform(3.20, 3.65) if f()
            else rng.uniform(3.30, 3.58),
            "bcm_cell_vmin": rng.uniform(3.00, 3.40),
            "bcm_cell_tmax": rng.uniform(-10, 50),
            "bcm_cell_tmin": rng.uniform(-10, 40)})),
        (M.BMS_LIMITS, P.encode(P.EPRI, 0x420, {
            "bcm_chg_max": rng.uniform(0, 320) if f()
            else rng.uniform(20, 320),
            "bcm_dis_max": 612.0,
            "bcm_cell_overvolt": f(),
            "bcm_cell_undervolt": rng.random() < 0.05})),
        (M.BMS_DATA2, P.encode(P.EPRI, 0x440, {
            "bcm_mainc_stat": rng.choice((0, 11)) if f() else 12})),
        (M.BMS_STATUS, P.encode(P.EPRI, 0x410, {
            "bcm_soc": rng.uniform(78, 100),
            "bcm_ibat": rng.uniform(-10, 40),
            "bcm_hvil_mon": 0 if f() else 1,
            "bcm_alarm": 3 if f() else rng.choice((0, 0, 0, 2)),
            "bcm_epo": 1 if f() else 0})),
    ]


def random_sequence(seed, steps=120000, fault_rate=0.0004,
                    garbage=0.0, mutate=None):
    """A bus that keeps its cadence but not its sense.

    Not fuzzing for crashes. The point is that the five rules are
    UNCONDITIONAL, and every trace in the corpus is a well-formed charge --
    so all of them walk the core down the paths it was designed for. A rule
    that only holds on a well-formed charge is not an invariant, and the
    place a safety core has to keep its promises is where the bus stops
    making sense.

    The frames arrive on the real periods with randomised values, plus
    outright garbage mixed in. The cadence is deliberate: with purely random
    frame selection the core never armed at all -- the arm needs 75 s of an
    observed charge with four BMS messages all staying fresh -- so every
    rule about overriding, rewriting and repeating went unexercised and the
    test passed on a core that had done nothing. The "reached the repeat
    path" assertion at the end of the test is there because that is exactly
    what happened on the first version.
    """
    rng = random.Random(seed)
    rng03 = random.Random(seed + 1000)
    c = Checker("random/%d f=%g g=%g" % (seed, fault_rate, garbage),
                mutate=mutate)
    t = 0
    next_bms = 0
    next_chg = 0
    next_cmd = 0
    for _ in range(steps):
        t += rng.randint(5, 15)

        if t >= next_bms:
            next_bms = t + 100
            for fid, data in _rand_bms(rng, fault_rate):
                c.step("V", t, fid, False, data)

        if t >= next_chg:
            next_chg = t + 100
            c.step("C", t, M.CHG_STATUS, True, P.encode(
                P.BEL, 0x18FFD4C0, {
                    "BELINV_state": (rng.choice((10, 11, 15, 33))
                                     if rng.random() < fault_rate * 8
                                     else 12),
                    "BELINV_shutdownSource": rng.choice((0, 1, 3, 4, 11, 14)),
                    "BELINV_vehicleConnected":
                        0 if rng.random() < fault_rate else 1,
                    "BELINV_evseConnected": 1},
                "BELINV_statusMultiplexer", 0))
            c.step("C", t, M.CHG_INFO, True, P.encode(
                P.BEL, 0x18FFD9C0, {
                    "BELINV_maxAvailableChargingCurrent": rng.uniform(6, 32),
                    "BELINV_vehicleState": 3}))
            # The pilot timer counts minutes and only ever steps BACK at a
            # handle pull -- which is a session boundary that resets the arm
            # (spec 6.1). A first version jittered it at 2 % of periods, so
            # the boundary fired every few seconds and the 75 s arm delay
            # could never elapse: that alone kept the core out of OVERRIDE
            # for the whole run.
            c.step("C", t, M.CHG_PILOT, True,
                   _pilot(rng.randint(0, 90) if rng.random() < fault_rate
                          else t // 60000))

        if t >= next_cmd:
            next_cmd = t + rng.randint(300, 3000)
            page = rng.choice((0x00, 0x00, 0x01, 0x03, 0x09))
            if page == 0x00:
                # Same trap as the pilot: STAND_BY and EXPORT are session
                # boundaries, so making them common means the core restarts
                # its arm delay every few seconds and never gets to override.
                if rng.random() < fault_rate * 20:
                    mode = rng.choice((P.MODE_STANDBY, P.MODE_EXPORT))
                    flow = 0
                else:
                    mode = rng.choice((P.MODE_CHARGER, P.MODE_LOW_POWER,
                                       P.MODE_LOW_POWER, P.MODE_LOW_POWER))
                    flow = 0 if rng.random() < fault_rate * 10 else 1
                data = P.enc_master(flow, mode)
            elif page == 0x01:
                data = P.enc_setpoint(P.VLIM_DEFAULT_COUNTS,
                                      rng.randint(0, 400))
            elif page == 0x03:
                # 03.02 / 03.07 often enough that the spec 4 hold is judged
                # during overrides (a uniform second byte gave ~1 per run),
                # with their held bytes, the "complete" bytes and noise.
                # The main stream makes the same draws as before and the
                # contents come from rng03, so the core walks the path it
                # always did: page 03 changes no state. Redrawing from rng
                # itself cost seeds 2 and 3 every repeat burst they had.
                noise = bytes([page, rng.getrandbits(8)]) + bytes(
                    rng.getrandbits(8) for _ in range(6))
                sub = rng03.choice((0x00, 0x02, 0x07, noise[1]))
                if sub in HELD and rng03.random() < 0.3:
                    data = HELD[sub]
                elif sub == 0x02 and rng03.random() < 0.5:
                    data = bytes.fromhex("0302007500000000")
                elif sub == 0x07 and rng03.random() < 0.5:
                    data = bytes.fromhex("0307010000000000")
                else:
                    data = bytes([page, sub]) + noise[2:]
            else:
                data = bytes([page, rng.getrandbits(8)]) + bytes(
                    rng.getrandbits(8) for _ in range(6))
            c.step("V", t, M.CMD_ID, True, data)

        c.step("V", t, M.VCM_EVAP, False,
               bytes([rng.getrandbits(8)] * 4
                     + [0x80 if rng.random() < 0.6 else 0x00]
                     + [rng.getrandbits(8)] * 3))

        if garbage and rng.random() < garbage:
            # Outright garbage, structurally valid only in its length. It is
            # its own dial, and OFF in the calm runs, because mixing the two
            # does not give a bit of both -- it gives the chaotic regime.
            # At a flat 5 % this block alone kept the core out of OVERRIDE
            # for every seed: a junk 0x18FFD8C0 decodes as a pilot timer of
            # several million minutes, the next real one steps back from it,
            # and a backward step is a session boundary that resets the arm
            # (spec 6.1); a junk 0x430 reads 5,659 mV and trips the hard
            # ceiling within seconds of every re-arm. The core was behaving
            # correctly throughout. The stimulus simply never let it arm, and
            # every rule about overriding went unchecked while the run
            # reported no problems.
            fid = rng.choice(RAND_IDS + RAND_CHG_IDS)
            c.step("C" if fid in RAND_CHG_IDS else "V", t, fid, fid > 0x7FF,
                   bytes(rng.getrandbits(8) for _ in range(8)))

        c.step("T", t)
    return c


def _pilot(minutes):
    d = bytearray(8)
    d[5] = minutes & 0xFF
    d[6] = (minutes >> 8) & 0xFF
    d[7] = (minutes >> 16) & 0xFF
    return bytes(d)


# --- tests -----------------------------------------------------------------

def test_invariants_hold_over_every_trace():
    names = sorted(n for n in os.listdir(TRACES) if n.endswith(".trace"))
    ok(len(names) >= 20,
       "found %d traces in %s -- the glob is looking in the right place"
       % (len(names), TRACES))
    total = p01 = mirrors = full = cut = 0
    for name in names:
        c = replay_trace(os.path.join(TRACES, name))
        total += c.n_steps
        p01 += c.n_page01_checked
        mirrors += c.n_mirrors
        # Per trace, only that it was read at all -- the unit traces are
        # deliberately a few dozen steps each. The population check is the
        # total, which no individual trace can satisfy on its own.
        ok(c.n_steps > 0, "%s: replayed %d steps" % (name, c.n_steps))
        for p in c.finish():
            FAILS.append(p)
        full += sum(1 for b in c.bursts if not b["cut"])
        cut += sum(1 for b in c.bursts if b["cut"])
    ok(total > 500000,
       "the trace corpus is substantial (%d steps over %d traces)"
       % (total, len(names)))
    # Rev 2 population guards: each rule must actually have been exercised,
    # or it passes by never running (the shape AGENTS.md calls Mode 1).
    ok(p01 >= 1000, "rule 5 judged %d override page-01 frames" % p01)
    ok(mirrors >= 1000, "rule 8.1 judged %d mirror frames" % mirrors)
    ok(full >= 10, "%d repeat bursts ran to completion and were judged "
       "at exactly 20" % full)
    ok(cut >= 2, "%d cut-short bursts were seen (replaced, superseded)"
       % cut)


def test_invariants_hold_over_randomised_traffic():
    total_repeats = 0
    overrode = 0
    # Two regimes. The calm one is the only one that reaches OVERRIDE at all,
    # so it is the only one that exercises the rewrite and repeat rules; the
    # chaotic one is where the core spends its time tripping, re-arming and
    # being handed nonsense, which is where rules 1 and 4 have to hold.
    for seed, rate, junk in ((1, 0.00005, 0.0), (2, 0.0001, 0.0),
                             (3, 0.0002, 0.0),
                             (4, 0.02, 0.03), (5, 0.08, 0.08)):
        c = random_sequence(seed, fault_rate=rate, garbage=junk)
        total_repeats += c.n_repeat_frames
        overrode += c.n_overrode
        for p in c.finish():
            FAILS.append(p)
    ok(overrode > 0,
       "the random sequences reached OVERRIDE at least once (%d steps)"
       % overrode)
    # Without this the whole random test passes on a core that never does
    # anything at all, which is the shape most of these checks have.
    ok(total_repeats > 0,
       "the random sequences reached the repeat path at least once (%d "
       "frames)" % total_repeats)


# --- the checker's own test ------------------------------------------------
#
# A check that cannot fail is not a check. Each mutation below is a wrong
# core, made by rewriting the REAL core's outputs on the way to the checker
# (machine.py is untouched), and each must be rejected for its own reason --
# the failure text has to name it, so a mutation caught by some unrelated
# rule does not count. Each must also have actually APPLIED at least once:
# a mutation that never fires is a test that cannot fail.

MUTATION_TRACES = ("syn_override_release.trace", "syn_above_ceiling_start.trace",
                   "syn_repeat_replaced.trace", "syn_trip_during_override.trace")


def _rewrite(out, pred, fn):
    hit = 0
    new = []
    for item in out:
        if pred(item):
            hit += 1
            r = fn(item)
            if r is None:
                continue
            if isinstance(r, list):
                new.extend(r)
                continue
            item = r
        new.append(item)
    return new, hit


def _is_rep(c, kind, arb_id):
    def pred(item):
        side, fid, _e, payload = item
        fwd = kind != "T" and side == (M.TO_CHARGER if kind == "V"
                                       else M.TO_VEHICLE) and fid == arb_id
        return side == M.TO_CHARGER and fid == M.CMD_ID and not fwd
    return pred


def _mutations():
    """name -> (mutate(c, kind, arb_id, data, out) -> (out, hits), text)."""
    def fwd_cmd(kind, arb_id, page):
        def pred(item):
            side, fid, _e, payload = item
            return (kind == "V" and arb_id == M.CMD_ID and side == M.TO_CHARGER
                    and fid == M.CMD_ID and payload[0] == page)
        return pred

    def setb(payload, i, v):
        b = bytearray(payload)
        b[i] = v & 0xFF
        return bytes(b)

    def m_p01_unrewritten(c, kind, arb_id, data, out):
        if c.core.state != M.S_OVERRIDE:
            return out, 0
        return _rewrite(out, fwd_cmd(kind, arb_id, 0x01),
                        lambda it: (it[0], it[1], it[2], bytes(data))
                        if bytes(it[3]) != bytes(data) else None)

    def m_p01_over(c, kind, arb_id, data, out):
        if c.core.state != M.S_OVERRIDE:
            return out, 0

        def up(it):
            v, i = P.setpoint_counts(it[3])
            return (it[0], it[1], it[2], P.enc_setpoint(v, i + 40))
        return _rewrite(out, fwd_cmd(kind, arb_id, 0x01), up)

    def m_p01_vlim(c, kind, arb_id, data, out):
        if c.core.state != M.S_OVERRIDE:
            return out, 0
        return _rewrite(out, fwd_cmd(kind, arb_id, 0x01),
                        lambda it: (it[0], it[1], it[2],
                                    setb(it[3], 1, it[3][1] ^ 0x01)))

    def m_p00_bytes(c, kind, arb_id, data, out):
        def pred(it):
            return (fwd_cmd(kind, arb_id, 0x00)(it)
                    and bytes(it[3]) != bytes(data))
        return _rewrite(out, pred, lambda it: (it[0], it[1], it[2],
                                               setb(it[3], 5, 0x5A)))

    def m_rep_standby(c, kind, arb_id, data, out):
        return _rewrite(out, _is_rep(c, kind, arb_id),
                        lambda it: (it[0], it[1], it[2],
                                    setb(it[3], 2, P.MODE_STANDBY)))

    def m_rep_flow(c, kind, arb_id, data, out):
        return _rewrite(out, _is_rep(c, kind, arb_id),
                        lambda it: (it[0], it[1], it[2],
                                    setb(it[3], 1, it[3][1] ^ 0x01)))

    def m_rep_short(c, kind, arb_id, data, out):
        # drop the 20th frame of every burst: 19 delivered, nothing cut it
        if not c.bursts or c.bursts[-1]["sent"] != BURST - 1:
            return out, 0
        return _rewrite(out, _is_rep(c, kind, arb_id), lambda it: None)

    def m_rep_long(c, kind, arb_id, data, out):
        if not c.bursts or c.bursts[-1]["sent"] != BURST - 1:
            return out, 0
        return _rewrite(out, _is_rep(c, kind, arb_id), lambda it: [it, it])

    def m_rep_truncated(c, kind, arb_id, data, out):
        # stop every burst after 9 frames. VCU page 01 / 03 frames arrive
        # during it, and rev 1 counted any 0x18EFC000 as cutting a burst
        # short -- only a page 00 does (spec 4.1)
        if not c.bursts or c.bursts[-1]["sent"] < 9:
            return out, 0
        return _rewrite(out, _is_rep(c, kind, arb_id), lambda it: None)

    def m_charger_on_release(c, kind, arb_id, data, out):
        def pred(it):
            return _is_rep(c, kind, arb_id)(it) and it[3][2] == P.MODE_LOW_POWER
        return _rewrite(out, pred, lambda it: (it[0], it[1], it[2],
                                               setb(it[3], 2, P.MODE_CHARGER)))

    def m_mirror_side(c, kind, arb_id, data, out):
        return _rewrite(out, lambda it: it[1] == MIRROR_CMD,
                        lambda it: (M.TO_CHARGER, it[1], it[2], it[3]))

    def m_mirror_missing(c, kind, arb_id, data, out):
        return _rewrite(out, lambda it: it[1] == MIRROR_CMD, lambda it: None)

    def m_mirror_content(c, kind, arb_id, data, out):
        return _rewrite(out, lambda it: it[1] == MIRROR_CMD,
                        lambda it: (it[0], it[1], it[2],
                                    setb(it[3], 7, it[3][7] ^ 0xFF)))

    def m_mirror_hv(c, kind, arb_id, data, out):
        # any charger frame will do as the carrier: these traces hold no
        # 0x18FFD7C0 of their own
        if kind == "C":
            return out + [(M.TO_VEHICLE, MIRROR_HV, True, bytes(data))], 1
        return out, 0

    def m_dup_bms(c, kind, arb_id, data, out):
        return _rewrite(out, lambda it: kind == "V" and arb_id == M.BMS_LIMITS
                        and it[1] == M.BMS_LIMITS, lambda it: [it, it])

    def m_drop_bms(c, kind, arb_id, data, out):
        return _rewrite(out, lambda it: kind == "V" and arb_id == M.BMS_LIMITS
                        and it[1] == M.BMS_LIMITS, lambda it: None)

    return {
        "page 01 forwarded unrewritten in an override":
            (m_p01_unrewritten, "commanded"),
        "page 01 two amps above ours": (m_p01_over, "commanded"),
        "page 01 Vlim byte changed": (m_p01_vlim, "other than the current"),
        "page 00 byte other than the mode changed":
            (m_p00_bytes, "other than the mode"),
        "repeat of STAND_BY": (m_rep_standby, "originated a command"),
        "repeat with the flow bit flipped": (m_rep_flow, "standing page 00"),
        "burst of 19 with nothing cutting it": (m_rep_short, "with 19"),
        "burst of 21": (m_rep_long, "more than 20"),
        "burst stopped at 9 with only non-page-00 frames around it":
            (m_rep_truncated, "with 9"),
        "CHARGER repeat on a release or trip":
            (m_charger_on_release, "CHARGER repeat"),
        "mirror sent toward the charger": (m_mirror_side, "toward the charger"),
        "mirror missing": (m_mirror_missing, "with no mirror"),
        "mirror carrying other bytes": (m_mirror_content, "matches no"),
        "telemetry mirror with masking off": (m_mirror_hv, "telemetry mirror"),
        "0x420 forwarded twice": (m_dup_bms, "forwarded 2 times"),
        "0x420 dropped": (m_drop_bms, "forwarded 0 times"),
    }


def _replay_mutated(path, mutate, dense=False):
    hits = [0]

    def wrap(c, kind, arb_id, data, out):
        new, h = mutate(c, kind, arb_id, data, out)
        hits[0] += h
        return new

    if dense:
        c = densified(path, mutate=wrap)
    else:
        cfg, steps = _read_trace(path)
        c = Checker(os.path.basename(path), cfg, mutate=wrap)
        for st in steps:
            c.step(*st)
    return c.finish(), hits[0]


def test_checker_catches_each_mutation():
    paths = [os.path.join(TRACES, n) for n in MUTATION_TRACES]
    for p in paths:
        ok(os.path.exists(p), "mutation trace %s exists" % p)
    # control: the unmutated core passes every one of these traces, through
    # the same mutate path, so a failure below is the mutation's doing
    for p in paths:
        probs, _h = _replay_mutated(p, lambda c, k, a, d, out: (out, 0))
        ok(not probs, "control %s passes unmutated: %s"
           % (os.path.basename(p), probs[:2]))
    for name, (mut, text) in sorted(_mutations().items()):
        hits = 0
        probs = []
        for p in paths:
            pr, h = _replay_mutated(p, mut)
            hits += h
            probs += pr
        ok(hits > 0, "mutation '%s' applied (%d times)" % (name, hits))
        ok(any(text in q for q in probs),
           "mutation '%s' rejected for its own reason ('%s'); got %s"
           % (name, text, probs[:2]))


def test_hold_and_gaps_over_densified_traces():
    """Spec 4 (03.02 / 03.07 held) and 4.1 (50 +/- 10 ms) on the traces
    replayed densely, the only place both rules are judged (densified()).
    unit_dense_* are already densified replays, recorded for the port; they
    are judged as they stand in test_invariants_hold_over_every_trace."""
    names = sorted(n for n in os.listdir(TRACES) if n.endswith(".trace")
                   and not n.startswith("unit_dense_"))
    held = differed = judged = unjudged = 0
    for name in names:
        c = densified(os.path.join(TRACES, name))
        for p in c.finish():
            FAILS.append(p)
        held += c.n_held
        differed += c.n_held_differed
        judged += c.n_gaps_judged
        unjudged += c.n_gaps_unjudged
    # Printed so the run file carries the figures the evidence quotes
    # (reviewer, 2026-10-08); the floors below are guards, not the result.
    print("  --  densified: hold judged on %d override 03.02 / 03.07 frames "
          "(%d with VCU bytes differing); %d repeat gaps judged, %d "
          "unjudged; %d traces" % (held, differed, judged, unjudged,
                                   len(names)))
    # Population guards: measured 2026-10-08 at 718 / 358 / 467.
    ok(differed >= 200, "the hold was judged on %d override 03.02 / 03.07 "
       "frames whose VCU bytes differed from the held ones (%d in all)"
       % (differed, held))
    ok(judged >= 300, "the 4.1 gap rule judged %d repeat gaps" % judged)


def _dense_mutations():
    """Wrong cores for the two rules only the densified replay judges."""
    def pred_03(kind, arb_id, data):
        def pred(it):
            return (kind == "V" and arb_id == M.CMD_ID and len(data) >= 2
                    and data[0] == 0x03 and data[1] in HELD
                    and it[0] == M.TO_CHARGER and it[1] == M.CMD_ID)
        return pred

    def m_hold_dropped(c, kind, arb_id, data, out):
        # the VCU's own 03.02 / 03.07 forwarded during an override
        if (kind != "V" or c.core.state != M.S_OVERRIDE
                or bytes(data) in HELD.values()):
            return out, 0
        return _rewrite(out, pred_03(kind, arb_id, data),
                        lambda it: (it[0], it[1], it[2], bytes(data)))

    def m_hold_wrong(c, kind, arb_id, data, out):
        # 03.07 held with one byte off (64 -> 65)
        if (kind != "V" or c.core.state != M.S_OVERRIDE or len(data) < 2
                or data[1] != 0x07):
            return out, 0

        def f(it):
            b = bytearray(it[3])
            b[3] = 0x65
            return (it[0], it[1], it[2], bytes(b))
        return _rewrite(out, pred_03(kind, arb_id, data), f)

    def split(c, kind, arb_id, out):
        rep = [it for it in out if _is_rep(c, kind, arb_id)(it)]
        mir = [it for it in out if it[1] == MIRROR_CMD]
        rest = [it for it in out if it not in rep and it not in mir]
        return rep, mir, rest

    def m_gap_late(c, kind, arb_id, data, out):
        # each burst's 10th frame, and its mirror, held back >= 25 ms and
        # released on a tick: gaps of ~75 and ~25 ms either side of it
        held = c.__dict__.setdefault("_m_late", [])
        if held and kind == "T" and c._t >= held[0][0]:
            out = out + [it for _t, it in held]
            del held[:]
        if c.bursts and c.bursts[-1]["sent"] == 9                 and not c.bursts[-1].get("_m"):
            rep, mir, rest = split(c, kind, arb_id, out)
            if rep:
                c.bursts[-1]["_m"] = True
                held[:] = [(c._t + 25, it) for it in rep + mir]
                return rest, 1
        return out, 0

    def m_gap_early(c, kind, arb_id, data, out):
        # each burst's 10th frame sent on the same step as its 9th, and the
        # core's own 10th dropped: still 20 frames, one gap of 0 ms
        if not c.bursts:
            return out, 0
        b = c.bursts[-1]
        rep, mir, rest = split(c, kind, arb_id, out)
        if not rep:
            return out, 0
        if b["sent"] == 8 and not b.get("_m"):
            b["_m"] = "drop"
            return out + rep + mir, 1
        if b.get("_m") == "drop":
            b["_m"] = True
            return rest, 0
        return out, 0

    return {
        "03.02 / 03.07 forwarded as the VCU sent them in an override":
            (m_hold_dropped, "spec 4 holds it"),
        "03.07 held with one byte wrong": (m_hold_wrong, "spec 4 holds it"),
        "a repeat frame sent 25 ms late": (m_gap_late, "repeat frames"),
        "two repeat frames on one step": (m_gap_early, "repeat frames"),
    }


def test_checker_catches_hold_and_gap_mutations():
    paths = [os.path.join(TRACES, n) for n in MUTATION_TRACES]
    for p in paths:
        probs, _h = _replay_mutated(p, lambda c, k, a, d, out: (out, 0),
                                    dense=True)
        ok(not probs, "control dense/%s passes unmutated: %s"
           % (os.path.basename(p), probs[:2]))
    for name, (mut, text) in sorted(_dense_mutations().items()):
        hits = 0
        probs = []
        for p in paths:
            pr, h = _replay_mutated(p, mut, dense=True)
            hits += h
            probs += pr
        ok(hits > 0, "mutation '%s' applied (%d times)" % (name, hits))
        ok(any(text in q for q in probs),
           "mutation '%s' rejected for its own reason ('%s'); got %s"
           % (name, text, probs[:2]))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        before = len(FAILS)
        try:
            t()
        except Exception as e:                            # noqa: BLE001
            FAILS.append("%s raised %r" % (t.__name__, e))
        print(("  ok  " if len(FAILS) == before else "FAIL  ") + t.__name__)
    if FAILS:
        print("\n%d failure(s):" % len(FAILS))
        for f in FAILS[:40]:
            print("  - " + f)
        return 1
    print("\ntest_invariants: %d tests OK" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
