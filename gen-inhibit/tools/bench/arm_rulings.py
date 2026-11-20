"""The gen-inhibit reviewer's rulings on the arm reasons on no spec 12.1 entry (answer to
ba92acf0, 2026-11-19), for the checks whose arms record them: B5, B6, B7 and B14.

THE PRINCIPLE (the reviewer's words): a reason the bench caused is an incomplete stimulus
or reading, so it is void. A reason that is the device's own behaviour is a result, never
void. Where the record can't tell which, it stays ERROR and goes to the reviewer with the
arm. So every reason maps to one of three kinds:

  BENCH   -> the arm is VOID
  DEVICE  -> the arm FAILs
  UNCLEAR -> ERROR, for the reviewer

and two reasons need the record read first:

THE RESET RULE. A device reset seen in an arm is a result: read its reason. A panic, a
watchdog, or any software reset the bench did not command (in these arms the bench
commands none) is the device's: FAIL. A brownout or a power-on is the bench's power: VOID.
Any other reason, or a reset with no new boot on the page, can't be told: ERROR. Spec 11
says how the page shows a reset: `uptime_ms` falls, and `reset_reason` is the cause of the
reset that started this boot (an IDF name without ESP_RST_, or its number as a string).
The reason is recorded either way, in the arm's text: "reset reason PANIC", or
"reset reason none" when no reading after it showed a new boot.

THE GATE ON THE WIRE. "Never went live" is the bench's (VOID) only if the witness shows the
replay never put the go-live conditions on the wire; if it did, it is the device's (FAIL).
The conditions read from the wire are spec 7's arm gate 1-6, at each frame the witness
holds from the arm to the end of the wait for live: 0x051 fresh (0.5 s), reading
gen_rpm_ref < 0 and gen_torque_cmd 0 (6); 0x440 fresh reading bcm_mainc_stat 11 or 12,
with a 0x411 since it closed (2); 0x617 fresh (1.0 s) and its B7 not 0xCA (3); 0x639
fresh (4); if 0x054 is fresh, GENE_RotSpd below 300 rpm (5). Conditions 7 (the 6.3
shutdown suppression), 8 (no release or latch) and 9 (the receive queue drained) are the
device's own state and are not read from the wire. The conditions count as on the wire
when they held, frame after frame, for at least GATE_HELD_S; never holding is VOID;
holding only for less is ERROR (the record can't tell). Held long enough is FAIL only when
the page's arm_block, read while it held, names a wire condition or nothing; naming 7, 8
or 9 is ERROR (the reviewer's refinement, answer to 0cfd6689, 2026-11-19; held_block).
The arm's text carries "gate on the wire: held 4.2 s at most; arm_block while held: wire"
or "gate on the wire: never held"; a record without it (one made before the witness kept
the gate's IDs, 2026-11-19) is ERROR, and so is a held line without the page's class.

GATE_HELD_S is a harness bound, accepted by the gen-inhibit reviewer (answer to 0cfd6689,
2026-11-19) and recorded in the madhouse bench test plan: twice the slowest arm-to-live
the harness has recorded on these images (0.24-0.58 s, the livetrain arms and the SW
boot-to-live, autoarm_boot_to_live.py).

Gen-inhibit tester, 2026-11-19.
"""
import re

BENCH, DEVICE, UNCLEAR = "bench", "device", "unclear"
VERDICT = {BENCH: "VOID", DEVICE: "FAIL", UNCLEAR: "ERROR"}

# ---------------------------------------------------------------- the reset rule

RESET_DEVICE = ("PANIC", "INT_WDT", "TASK_WDT", "WDT", "SW")
RESET_BENCH = ("BROWNOUT", "POWERON")


def new_boots(samples, t0, t1):
    """[(t, reset_reason)] for each answered reading in [t0, t1) whose uptime_ms is below
    the answered reading's before it, at any time (spec 11: a reboot, seen directly).
    samples: [(t, snap or None)]."""
    out, last = [], None
    for t, s in sorted(samples, key=lambda x: x[0]):
        if not s or s.get("uptime_ms") is None:
            continue
        u = s["uptime_ms"]
        if last is not None and u < last and t0 <= t < t1:
            out.append((t, s.get("reset_reason")))
        last = u
    return out


def reset_text(boots):
    """The arm's record of the reasons: "reset reason PANIC,SW", or "reset reason none"."""
    return "reset reason " + (",".join(str(r) for _, r in boots) if boots else "none")


RESET_RE = re.compile(r"reset reason (\S+)")


def reset_kind(line):
    """The kind of the reset an arm's line records (reset_text), UNCLEAR without one."""
    m = RESET_RE.search(line)
    if not m or m.group(1) == "none":
        return UNCLEAR
    rs = m.group(1).rstrip(");,").split(",")
    if any(r in RESET_DEVICE for r in rs):
        return DEVICE
    if all(r in RESET_BENCH for r in rs):
        return BENCH
    return UNCLEAR


# ---------------------------------------------------------------- the gate on the wire

GATE_HELD_S = 1.0
GATE_IDS = (0x051, 0x054, 0x411, 0x440, 0x617, 0x639)
FRESH_S = {0x051: 0.5, 0x054: 0.5, 0x440: 0.5, 0x617: 1.0, 0x639: 0.5}
CLOSED = (11, 12)
FAULT_ACTIVE = 0xCA


def decoder():
    """decode(arb, data bytes) -> the signals the gate reads, from the vehicle DBCs
    (interlock_judge.db: REVERSE_IT_DIR's projects/vtrux), or None if it does not decode."""
    import interlock_judge as IJ

    def decode(arb, data):
        try:
            if arb == 0x051:
                return IJ.db(IJ.PTX).decode_message(0x051, data, decode_choices=False)
            if arb in (0x054, 0x440):
                return IJ.db(IJ.PT).decode_message(arb, data, decode_choices=False, allow_truncated=True)
        except Exception:
            return None
        return {}
    return decode


def gate_spans(frames, t0, t1, decode):
    """[(start, end)]: the runs of consecutive witness frames in [t0, t1] at each of which
    spec 7's gate conditions 1-6 held on the wire (the module docstring). frames:
    [(t, arb, hex)] in any order, including those before t0 (freshness, the close)."""
    last, closed_since, soc_t = {}, None, None
    spans, start, prev = [], None, None
    for t, arb, h in sorted(frames, key=lambda x: x[0]):
        if arb not in GATE_IDS:
            continue
        data = bytes.fromhex(h) if isinstance(h, str) else bytes(h)
        if arb == 0x411:
            soc_t = t
        else:
            sig = decode(arb, data)
            if arb == 0x617:
                sig = {"b7": data[7]} if len(data) >= 8 else None
            last[arb] = (t, sig)
            if arb == 0x440:
                c = bool(sig) and sig.get("bcm_mainc_stat") in CLOSED
                closed_since = (closed_since if closed_since is not None else t) if c else None
        if t < t0 or t > t1:
            continue

        def fresh(i):
            v = last.get(i)
            return v is not None and v[1] is not None and t - v[0] <= FRESH_S[i]
        ok = (fresh(0x051) and last[0x051][1].get("gen_rpm_ref", 0) < 0
              and last[0x051][1].get("gen_torque_cmd", 1) == 0
              and fresh(0x440) and closed_since is not None and soc_t is not None and soc_t >= closed_since
              and fresh(0x617) and last[0x617][1]["b7"] != FAULT_ACTIVE
              and fresh(0x639)
              and (not fresh(0x054) or last[0x054][1].get("GENE_RotSpd", 300) < 300))
        if ok and start is None:
            start = t
        if not ok and start is not None:
            spans.append((start, prev))
            start = None
        prev = t
    if start is not None:
        spans.append((start, prev))
    return spans


# THE PAGE WHILE THE GATE HELD (the gen-inhibit reviewer, answer to 0cfd6689, 2026-11-19): a
# gate held for at least GATE_HELD_S is the device's only when the page's arm_block, read
# while it held, names a wire condition (1-6) or names nothing. Naming 7, 8 or 9 (the
# shutdown suppression, a latched release or abort, the queue not drained) is ERROR: spec 7
# says a device that can't drain doesn't go live, so FAIL there would be wrong. The page
# gives arm_block as text: the wican_diag_schema.md "diag_arm_block values" meanings (a
# number is read by that table too); it gives a latched abort as the abort's reason, so
# abort_latched, disabled and shutdown_suppressed true are read as 8, 8 and 7 whatever the
# text. Classes: "none" (the gate satisfied, ""), "wire" (1-6), "state 7-9", "other" (not
# armed, gate not yet evaluated, or a text the schema does not name: the record can't
# tell), "unread" (no answered page read while it held).
BLOCK_NONE, BLOCK_WIRE, BLOCK_STATE, BLOCK_OTHER, BLOCK_UNREAD = "none", "wire", "state 7-9", "other", "unread"
BLOCK_DEVICE = (BLOCK_NONE, BLOCK_WIRE)
WIRE_BLOCKS = ("no fresh 0x051", "vcm fault active", "generator running", "vcm requesting engine",
               "vcm commanding torque", "soc not yet valid", "main contactors not closed",
               "no fresh 0x617", "no fresh 0x639")
STATE_BLOCKS = ("vcm commanding 0x10", "latched disable", "latched abort", "receive queue not drained")
WIRE_NUMBERS = (3, 4, 5, 6, 7, 9, 10, 11, 12)
STATE_NUMBERS = (8, 13, 14, 15)


def block_class(snap):
    """One answered page read's arm_block class (the comment above)."""
    if snap.get("abort_latched") or snap.get("disabled") or snap.get("shutdown_suppressed"):
        return BLOCK_STATE
    b = snap.get("arm_block")
    if isinstance(b, bool) or b is None:
        return BLOCK_OTHER
    if isinstance(b, int):
        return (BLOCK_NONE if b == 0 else BLOCK_WIRE if b in WIRE_NUMBERS
                else BLOCK_STATE if b in STATE_NUMBERS else BLOCK_OTHER)
    b = str(b).strip().lower()
    if b in ("", "none"):
        return BLOCK_NONE
    if b.startswith(STATE_BLOCKS):
        return BLOCK_STATE
    if b.startswith(WIRE_BLOCKS):
        return BLOCK_WIRE
    return BLOCK_OTHER


def held_block(samples, spans):
    """The page's arm_block class while the gate held at least GATE_HELD_S: the gravest of
    the answered reads stamped inside such a span (state 7-9, then other, then wire, then
    none), "unread" with none answered there, None when no span is that long.
    samples: [(t, snap or None)]."""
    held = [(a, b) for a, b in spans if b - a >= GATE_HELD_S]
    if not held:
        return None
    seen = {block_class(s) for t, s in samples if s and any(a <= t <= b for a, b in held)}
    for c in (BLOCK_STATE, BLOCK_OTHER, BLOCK_WIRE, BLOCK_NONE):
        if c in seen:
            return c
    return BLOCK_UNREAD


def gate_text(spans, block=None):
    """The arm's record of the gate: "gate on the wire: held 4.2 s at most; arm_block while
    held: wire" (held_block's class, when given) or "gate on the wire: never held"."""
    if not spans:
        return "gate on the wire: never held"
    return "gate on the wire: held %.1f s at most%s" % (
        max(b - a for a, b in spans), "" if block is None else "; arm_block while held: %s" % block)


GATE_RE = re.compile(r"gate on the wire: (?:(never held)|held ([\d.]+) s at most"
                     r"(?:; arm_block while held: (none|wire|state 7-9|other|unread))?)")


def gate_kind(line):
    """The kind of a "never went live" an arm's line records (gate_text), UNCLEAR without one:
    never held BENCH; held at least GATE_HELD_S DEVICE when the page named a wire condition
    or nothing while it held, else UNCLEAR (7-9, other, unread, or a line without the page's
    class: one made before 2026-11-19's refinement); held less UNCLEAR."""
    m = GATE_RE.search(line)
    if not m:
        return UNCLEAR
    if m.group(1):
        return BENCH
    if float(m.group(2)) < GATE_HELD_S:
        return UNCLEAR
    return DEVICE if m.group(3) in BLOCK_DEVICE else UNCLEAR


# ---------------------------------------------------------------- the reasons, per check

def b5_arm(line):
    """B5's INCONCLUSIVE livetrain arm (live_jam_arm.judge's summary): its kind."""
    if "never went live" in line:
        return gate_kind(line)
    if "the device was not live at the start of the jam" in line:
        return reset_kind(line) if "device reset seen" in line else BENCH
    return UNCLEAR


B6_BENCH = ("not live at the start of the pulses", "abort already latched before the pulses",
            "error frames between the arm and the pulses", "not armed: the reset before it was not shown")
B6_SHORT = "delivery was not one error frame per pulse"


def b6_arm(why):
    """B6's INCONCLUSIVE threshold arm (judge_threshold's why): its kind, or "inconclusive"
    for a delivery other than one error frame per pulse, which stays INCONCLUSIVE (12.1
    lists a stimulus that fell short, not one that went over: the reviewer, not widened)."""
    if B6_SHORT in why:
        return "inconclusive"
    if why.startswith("device reset while armed"):
        return reset_kind(why)
    if "never went live" in why:
        return gate_kind(why)
    if any(r in why for r in B6_BENCH):
        return BENCH
    return UNCLEAR


B7_BENCH = ("too few answered readings", "the replay was not running after the re-arm")
B7_BUSOFF = "the page never read bus_off after the jam"
B7_ANSWERED_RE = re.compile(r"\((\d+) answered readings from the jam to the re-arm\)")


def b7_arm(verdict):
    """B7's INCONCLUSIVE busoff-rearm arm (judge_busoff_rearm's verdict): its kind. The page
    never reading bus_off is the device's, unless no page read was answered (device silent);
    "never went live before the jam" is read as B5's "never went live" (the tester's
    reading, put to the reviewer with these rulings)."""
    if any(r in verdict for r in B7_BENCH):
        return BENCH
    if B7_BUSOFF in verdict:
        m = B7_ANSWERED_RE.search(verdict)
        if not m:
            return UNCLEAR
        return BENCH if int(m.group(1)) == 0 else DEVICE
    if "never went live" in verdict:
        return gate_kind(verdict)
    return UNCLEAR


B14_BENCH_POST = "the mode POST got no answer on the bench path"
B14_CUT_SHORT = "the probe was cut short"


def b14_row(result):
    """B14's row (observe_ack_probe.judge): None when read fairly, else its kind. INVALID
    (the page reads another mode than the one asked) is the device's, unless its mode POST
    got no answer on the bench path."""
    if not result.startswith("INVALID"):
        return None
    return BENCH if B14_BENCH_POST in result else DEVICE


def b14_verdict(verdict):
    """B14's probe verdict: None when read, else its kind. Both VOID reasons ("no valid
    mode-0 control", "the mode-0 control was ACKed") are the bench's; INCOMPLETE is the
    bench's when the probe was cut short, otherwise the reviewer's."""
    if verdict.startswith("VOID"):
        return BENCH
    if verdict.startswith("INCOMPLETE"):
        return BENCH if B14_CUT_SHORT in verdict else UNCLEAR
    return None
