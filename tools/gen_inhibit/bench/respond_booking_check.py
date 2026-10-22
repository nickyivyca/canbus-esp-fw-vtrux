"""Spec 11 (l): in RESPOND the on-time counts are booked for probe completions.

Gen-inhibit tester, 2026-10-12. Spec 11, "Transmit scheduler counters" (added
2026-10-12, user): "In RESPOND the on-time counts and the largest unverified backlog
are booked for probe completions, against the same deadline (the VCM's next 0x051)
... In every other mode they count inhibit completions only, and in OBSERVE and
PASSIVE they read 0."

Judged from the status page an e4 run records per arm (arms.<name>.device.sched), with
the mode the PAGE reports (device.mode), falling back to the run's `mode`:
  OBSERVE / PASSIVE  ontime, ontime_unverified and unverified_max_backlog all 0;
  RESPOND            ontime + ontime_unverified >= 1 when probes were sent, and never
                     more than cls.probe.sent; unverified_max_backlog >= 1 exactly
                     when ontime_unverified >= 1;
  INHIBIT            not judged here (the existing inhibit criteria apply).
`unbooked` = probes sent minus the two counts. Reviewer's reading (2026-10-12): a probe
completing after the VCM's next 0x051 is neither on time nor unverified, and never a
skip (skips and trip 7 are for inhibit frames only), so unbooked should equal the late
probes the witness sees, provided no probe is withdrawn. balance() makes that
comparison and judge_balance() JUDGES it (since 2026-10-12, when the user published the
0x7F0 layout: wican_diag_schema.md, "0x7F0: the RESPOND timing probe", and the DBC's
WICAN_Probe). Until then it was recorded only, because B6's meaning came from the code.

    py -3.14 respond_booking_check.py runs/<e4 run>.json [...]
"""
import json
import os
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = gi_paths.reverse_it_dir()
DBC = gi_paths.diag_dbc()
_PROBE = None


def probe_message():
    """WICAN_Probe (0x7F0) from vtrux-wican-diag.dbc, loaded once."""
    global _PROBE
    if _PROBE is None:
        import cantools
        _PROBE = cantools.database.load_file(DBC).get_message_by_frame_id(0x7F0)
    return _PROBE

MODES = {1: "observe", 2: "respond", 3: "inhibit", 4: "passive"}


def judge(mode, sched):
    """(verdict, detail) for one arm's sched block."""
    if sched is None or any(k not in sched for k in ("ontime", "ontime_unverified", "unverified_max_backlog")):
        return "UNRESOLVED", "no sched on-time fields"
    on, unv, mx = sched["ontime"], sched["ontime_unverified"], sched["unverified_max_backlog"]
    if mode in ("observe", "passive"):
        if on or unv or mx:
            return "FAIL", "%s reads ontime %s, unverified %s, max backlog %s (must be 0)" % (mode, on, unv, mx)
        return "PASS", "all 0"
    if mode != "respond":
        return "NOT JUDGED", "mode %s" % mode
    sent = ((sched.get("cls") or {}).get("probe") or {}).get("sent")
    if sent is None:
        return "UNRESOLVED", "no cls.probe.sent"
    bad = []
    if sent and on + unv < 1:
        bad.append("%d probes sent, none booked on time (ontime %d, unverified %d)" % (sent, on, unv))
    if on + unv > sent:
        bad.append("booked %d > %d probes sent" % (on + unv, sent))
    if (mx >= 1) != (unv >= 1):
        bad.append("max backlog %d with %d unverified" % (mx, unv))
    detail = "ontime %d, unverified %d, max backlog %d, probes sent %d, unbooked %d" % (
        on, unv, mx, sent, sent - on - unv)
    return ("FAIL", "; ".join(bad) + " | " + detail) if bad else ("PASS", detail)


def balance(sched, late_on_wire):
    """Recorded, not judged: probes sent minus booked against the witness's late probes.
    Applies only with no probe withdrawn (cls.probe.withdrawn 0)."""
    probe = ((sched or {}).get("cls") or {}).get("probe") or {}
    if not sched or "sent" not in probe or late_on_wire is None:
        return {"balanced": None, "why": "no sched, no cls.probe.sent or no witness count"}
    unbooked = probe["sent"] - sched.get("ontime", 0) - sched.get("ontime_unverified", 0)
    if probe.get("withdrawn"):
        return {"balanced": None, "why": "%d probe(s) withdrawn" % probe["withdrawn"],
                "unbooked": unbooked, "late_on_wire": late_on_wire}
    return {"balanced": unbooked == late_on_wire, "unbooked": unbooked, "late_on_wire": late_on_wire}


def judge_balance(b):
    """balance() -> (verdict, detail). PASS when unbooked equals the late probes on the
    wire, FAIL when it does not, UNRESOLVED when it cannot be compared (no counts, or a
    probe withdrawn)."""
    if b.get("balanced") is None:
        return "UNRESOLVED", b.get("why", "not comparable")
    v = "PASS" if b["balanced"] else "FAIL"
    return v, "unbooked %d, late on the wire %d" % (b["unbooked"], b["late_on_wire"])


def late_probes(frames):
    """(late, judged, malformed) from the witness: [(t, can_id, data bytes)] in any order.

    The published layout (wican_diag_schema.md, "0x7F0: the RESPOND timing probe"; the
    DBC's WICAN_Probe, user 2026-10-12): probe_vcm_ctr (B6) is the counter (0x051 B5 low
    nibble) of the most recent 0x051 the device received, the one the probe answers, and
    probe_marker (B7) is 0x5A. A probe is LATE when its probe_vcm_ctr differs from the
    newest 0x051 counter on the wire before it: the VCM's next command arrived first.
    B6 and B7 are decoded with cantools from the DBC. A probe with no 0x051 before it is
    not judged. A probe that is not 8 bytes, whose marker is not 0x5A or whose counter is
    outside 0-15 is MALFORMED: counted, never judged, so it cannot pass as on time. The
    0x051 counter has no DBC signal, so its low nibble is read directly."""
    msg = probe_message()
    late = judged = malformed = 0
    newest = None
    for _t, cid, data in sorted(frames, key=lambda f: f[0]):
        if cid == 0x051 and len(data) >= 6:
            newest = data[5] & 0x0F
        elif cid == 0x7F0:
            if len(data) != 8:
                malformed += 1
                continue
            sig = msg.decode(bytes(data), decode_choices=False)
            ctr, marker = int(sig["probe_vcm_ctr"]), int(sig["probe_marker"])
            if marker != 0x5A or not 0 <= ctr <= 15:
                malformed += 1
                continue
            if newest is None:
                continue
            judged += 1
            if ctr != newest:
                late += 1
    return late, judged, malformed


def read_witness(path):
    """A candump-style witness log (.log or .log.gz) -> [(t, id, data)]."""
    import gzip
    op = gzip.open if path.endswith(".gz") else open
    out = []
    with op(path, "rt", errors="replace") as fh:
        for ln in fh:
            parts = ln.split()
            if len(parts) < 3 or "#" not in parts[2]:
                continue
            cid, _, hexd = parts[2].partition("#")
            try:
                out.append((float(parts[0].strip("()")), int(cid, 16), bytes.fromhex(hexd)))
            except ValueError:
                continue
    return out


def arm_mode(run_mode, dev):
    """The mode the page reports, else the run's."""
    return MODES.get(dev.get("mode"), run_mode) if "mode" in dev else run_mode


def main():
    rc = 0
    for path in sys.argv[1:]:
        d = json.load(open(path, encoding="utf-8"))
        mode = d.get("mode")
        mode = MODES.get(mode, mode)
        for name, arm in (d.get("arms") or {}).items():
            dev = arm.get("device") or {}
            # The page's own mode, not the run's: a re-arm run names its SOURCE mode, and
            # its final page belongs to the arm it re-armed into (2026-10-12: the
            # e4_rearm2_observe runs were first judged as OBSERVE on an INHIBIT page).
            m = arm_mode(mode, dev)
            v, det = judge(m, dev.get("sched"))
            print("[%-12s] %s %s/%s: %s" % (v, path.replace("\\", "/").split("/")[-1], m, name, det))
            wl = arm.get("witness_raw")
            if m == "respond" and wl:
                import os
                wp = os.path.join(os.path.dirname(path), wl)
                if os.path.exists(wp):
                    late, judged, malformed = late_probes(read_witness(wp))
                    bv, bdet = judge_balance(balance(dev.get("sched"), late))
                    if malformed:
                        bv, bdet = "UNRESOLVED", "%d malformed probe(s); %s" % (malformed, bdet)
                    print("[%-12s]   balance: %s; late probes %d of %d judged, %d malformed"
                          % (bv, bdet, late, judged, malformed))
                    rc |= bv != "PASS"
            rc |= v not in ("PASS", "NOT JUDGED")
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
