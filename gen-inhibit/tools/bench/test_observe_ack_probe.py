"""Harness test for observe_ack_probe: the verdict, the PCAN status reading, and an arm that
reads on past a bus state python-can's pcan raises from recv (run cb08's B14 died of "error
passive" at its mode-0 control, 2026-11-19).

    python3.13 -B gen-inhibit/tools/bench/test_observe_ack_probe.py [--mutants]

--mutants applies each edit in MUTANTS to a copy of observe_ack_probe.py in the worktree's
scratch/ and requires a FAIL line from this test on each (a crash is not a kill).
"""
import importlib.util
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

SCRIPT = os.environ.get("OBSERVE_ACK_PROBE") or os.path.join(HERE, "observe_ack_probe.py")
_spec = importlib.util.spec_from_file_location("observe_ack_probe", SCRIPT)
O = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(O)


def A(mode, err, read=None):
    return {"mode": mode, "mode_read": mode if read is None else read, "ack_errors": err}


class FakeLF(object):
    """The WiCAN's page and POST, as lowid_flood_skip_bench gives them."""

    def __init__(self, mode):
        self.mode = mode

    def dev_post(self, path):
        return True

    def page(self):
        return {"mode": self.mode, "bus_on": True, "drv": {"state": "running", "rec": 0, "tec": 0}}


class FakePcan(object):
    """recv raises a bus state (as python-can's pcan does at error passive) on the reads in
    `raise_at`, gives a frame on the others, and the status reads BUSPASSIVE after the send."""

    def __init__(self, raise_at=(1, 2)):
        self.raise_at, self.reads, self.sent, self.closed = set(raise_at), 0, [], False

    def status(self):
        from can.interfaces.pcan import basic as B
        return B.PCAN_ERROR_BUSPASSIVE if self.sent else B.PCAN_ERROR_OK

    def send(self, msg):
        self.sent.append(msg)

    def recv(self, timeout=None):
        self.reads += 1
        if self.reads in self.raise_at:
            raise RuntimeError("Bus error: The CAN controller is error passive")
        import can
        return can.Message(arbitration_id=0x100, is_error_frame=(self.reads == 3))

    def shutdown(self):
        self.closed = True


def run_arm(bus):
    import can
    saved = sys.modules.get("kvaser_bus")
    sys.modules["kvaser_bus"] = types.SimpleNamespace(pcan_bus=lambda bitrate: bus)
    try:
        a = O.build_parser().parse_args(["--power", "x", "--x2", "held", "--hold-s", "0.05", "--settle-s", "0"])
        try:
            return O.one_arm(FakeLF(0), can, 0, a), None
        except Exception as e:      # noqa: BLE001 -- an arm that raises is what the check is about
            return None, e
    finally:
        if saved is None:
            sys.modules.pop("kvaser_bus", None)
        else:
            sys.modules["kvaser_bus"] = saved


def main():
    from can.interfaces.pcan import basic as B
    good = [A(0, True), A(1, True), A(2, False), A(1, True), A(0, True)]
    J = O.judge
    checks = [
        ("spec shape: OFF/OBSERVE errors, RESPOND none -> as specified",
         J(good)["verdict"] == "as specified"),
        ("OBSERVE ACKed -> NOT as specified",
         J([A(0, True), A(1, False), A(2, False)])["verdict"] == "NOT as specified"),
        ("RESPOND with ACK errors -> NOT as specified",
         J([A(0, True), A(1, True), A(2, True)])["verdict"] == "NOT as specified"),
        ("mode-0 control ACKed -> VOID, whatever OBSERVE did",
         J([A(0, False), A(1, True), A(2, False)])["verdict"].startswith("VOID")),
        ("no mode-0 control -> VOID", J([A(1, True), A(2, False)])["verdict"].startswith("VOID")),
        ("page not in the requested mode -> arm INVALID, and RESPOND untested -> INCOMPLETE",
         J([A(0, True), A(1, True), A(2, False, read=0)])["rows"][2]["result"].startswith("INVALID")
         and J([A(0, True), A(1, True), A(2, False, read=0)])["verdict"].startswith("INCOMPLETE")),
        ("status OK -> no ack errors", O.status_name(B.PCAN_ERROR_OK) == "OK"
         and not O.ack_errors(O.status_name(B.PCAN_ERROR_OK))),
        ("status BUSHEAVY -> ack errors", O.ack_errors(O.status_name(B.PCAN_ERROR_BUSHEAVY))),
        ("status BUSLIGHT -> ack errors", O.ack_errors(O.status_name(B.PCAN_ERROR_BUSLIGHT))),
        ("status QRCVEMPTY alone is not a bus error",
         not O.ack_errors(O.status_name(B.PCAN_ERROR_QRCVEMPTY))),
    ]
    for args, label in ((["--power", "x"], "--x2 required"), (["--x2", "held"], "--power required")):
        try:
            O.build_parser().parse_args(args)
            checks.append((label, False))
        except SystemExit:
            checks.append((label, True))

    # An arm reads on past a bus state raised from recv, records it, and judges on the status.
    bus = FakePcan(raise_at=(1, 2))
    rec, err = run_arm(bus)
    checks += [
        ("arm: a bus state raised from recv does not end the arm", err is None and rec is not None),
        ("arm: each bus state raised is recorded with its text",
         rec is not None and len(rec.get("pcan_states") or []) == 2
         and all("error passive" in s[1] for s in rec["pcan_states"])),
        ("arm: the reads after the bus states are counted",
         rec is not None and rec["pcan_rx"] >= 1 and rec["pcan_rx"] == bus.reads - 2),
        ("arm: the status after the send is read and judged (BUSPASSIVE -> ack errors)",
         rec is not None and rec["ack_errors"] is True and "BUSPASSIVE" in rec["pcan_status"]),
        ("arm: the PCAN is closed", bus.closed),
    ]
    bus2 = FakePcan(raise_at=())
    rec2, err2 = run_arm(bus2)
    checks.append(("arm: no bus state, none recorded", err2 is None and rec2 is not None
                   and rec2.get("pcan_states") == []))
    bus3 = FakePcan(raise_at=())
    import time as _t
    bus3.vtrux_states = [(_t.perf_counter() + 0.01, "PcanCanOperationError: Bus error: The CAN controller is error passive")]
    rec3, err3 = run_arm(bus3)
    checks.append(("arm: the states kvaser_bus.pcan_bus read past are recorded too, on the arm's clock",
                   err3 is None and rec3 is not None and len(rec3.get("pcan_states") or []) == 1
                   and "error passive" in rec3["pcan_states"][0][1] and -1.0 < rec3["pcan_states"][0][0] < 1.0,
                   ))

    fails = []
    for label, ok in checks:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


MUTANTS = [
    ("a bus state raised from recv ends the arm, as in run cb08",
     "            except Exception as e:      # noqa: BLE001  a bus state, not an end: recorded, read on\n",
     "            except ImportError as e:\n"),
    ("the bus states not recorded",
     '                states.append([round(time.perf_counter() - t0, 4), "%s: %s" % (type(e).__name__, e)])\n',
     "                pass\n"),
    ("the bus states not in the record", '"pcan_error_frames": n_err, "pcan_states": states}',
     '"pcan_error_frames": n_err, "pcan_states": []}'),
    ("the states pcan_bus read past not recorded",
     '        states += [[round(p - t0, 4), s] for p, s in (getattr(bus, "vtrux_states", None) or [])]\n', ""),
    ("a bus state counted as a frame","                time.sleep(0.001)\n                continue\n",
     "                time.sleep(0.001)\n                n_rx += 1\n                continue\n"),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    base = gi_paths.scratch("mutants-observe_ack_probe")
    killed = errors = 0
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("ERROR    control: the unmutated probe fails this test (exit %d)" % r.returncode)
        return 1
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, "observe_ack_probe.py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, OBSERVE_ACK_PROBE=path), timeout=120)
        fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
        dead = bool(fl) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fl),
                                                   ("; " + fl[0][:110]) if fl else (r.stderr.strip()[-120:] if r.stderr else "")))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
