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


class PostLF(FakeLF):
    """The POST raises `exc` for the modes in `bad`; the page reads `read` after a failed one."""

    def __init__(self, exc, bad=(1,), read=0):
        FakeLF.__init__(self, 0)
        self.exc, self.bad, self.read, self.asked = exc, set(bad), read, None

    def dev_post(self, path):
        m = int(path.split("mode=")[1].split("&")[0])
        self.asked = m
        if m in self.bad:
            raise self.exc
        self.mode = m
        return True

    def page(self):
        return dict(FakeLF.page(self), mode=self.read if self.asked in self.bad else self.mode)


def http_error(code):
    import urllib.error
    return urllib.error.HTTPError("http://192.168.80.1/gen_inhibit_set", code, "refused", {}, None)


def run_main(lf, pcan_open):
    """main() with the WiCAN, the PCAN and the runs folder stubbed: (exit code, stdout, record)."""
    import io
    import json
    import contextlib
    folder = gi_paths.scratch("observe-ack-probe-main")
    saved = {k: sys.modules.get(k) for k in ("kvaser_bus", "lowid_flood_skip_bench")}
    sys.modules["kvaser_bus"] = types.SimpleNamespace(pcan_bus=lambda bitrate: pcan_open())
    sys.modules["lowid_flood_skip_bench"] = lf
    old_argv, old_runs, old_sleep = sys.argv, O.RUNS, O.time.sleep
    sys.argv = ["observe_ack_probe.py", "--power", "x", "--x2", "held", "--hold-s", "0.02", "--settle-s", "0"]
    O.RUNS, O.time.sleep = folder, (lambda s: None)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                code = O.main()
            except Exception as e:      # noqa: BLE001  a main that raises is a failed check, not a crash
                code = "raised %r" % e
        recs = [f for f in os.listdir(folder) if f.endswith(".json")]
        rec = json.load(open(os.path.join(folder, recs[0]))) if len(recs) == 1 else None
        return code, out.getvalue(), rec
    finally:
        sys.argv, O.RUNS, O.time.sleep = old_argv, old_runs, old_sleep
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        gi_paths.remove_tree(folder)


def rulings_checks():
    """The gen-inhibit reviewer's B14 rulings (2026-11-19, arm_rulings.b14_row / b14_verdict):
    the probe records what they read. An INVALID row says whether its mode POST got no answer
    on the bench path or an answer from the device; a probe cut short is INCOMPLETE, says so,
    and is judged and recorded, never a bare traceback."""
    import urllib.error
    import arm_rulings as AR
    J = O.judge
    bench = dict(A(1, True, read=0), post_failure=O.post_failure(urllib.error.URLError("timed out")))
    dev = dict(A(1, True, read=0), post_failure=O.post_failure(http_error(400)))
    out = [
        ("post_failure: an HTTP status is the device's answer",
         O.post_failure(http_error(400)) == {"by": "device", "http_status": 400}),
        ("post_failure: no connection is no answer, on the bench path",
         O.post_failure(urllib.error.URLError("refused"))["by"] == "bench path"
         and O.post_failure(TimeoutError("timed out"))["by"] == "bench path"),
        ("INVALID with no answer on the bench path says so, and reads as the bench's",
         O.BENCH_POST in J([A(0, True), bench, A(2, False)])["rows"][1]["result"]
         and AR.b14_row(J([A(0, True), bench, A(2, False)])["rows"][1]["result"]) == AR.BENCH),
        ("INVALID on the device's answer says so, and reads as the device's",
         "answered 400" in J([A(0, True), dev, A(2, False)])["rows"][1]["result"]
         and AR.b14_row(J([A(0, True), dev, A(2, False)])["rows"][1]["result"]) == AR.DEVICE),
        ("INVALID with no POST failure (the page read another mode) is the device's",
         AR.b14_row(J([A(0, True), A(1, True, read=2), A(2, False)])["rows"][1]["result"]) == AR.DEVICE),
        ("a probe cut short with every mode tested is INCOMPLETE, cut short, never as specified",
         O.CUT_SHORT in J([A(0, True), A(1, True), A(2, False)], 5, "OSError: x")["verdict"]
         and J([A(0, True), A(1, True), A(2, False)], 5, "OSError: x")["verdict"].startswith("INCOMPLETE")),
        ("a probe cut short names how many arms of how many",
         "after 2 of 5 arms (OSError: x)" in J([A(0, True), A(1, True)], 5, "OSError: x")["verdict"]),
        ("INCOMPLETE with no cut short does not say cut short (the reviewer's)",
         O.CUT_SHORT not in J([A(0, True), A(1, True)])["verdict"]
         and AR.b14_verdict(J([A(0, True), A(1, True)])["verdict"]) == AR.UNCLEAR),
        ("a fairly read arm NOT as specified stands beside a probe cut short",
         J([A(0, True), A(1, False)], 5, "OSError: x")["verdict"] == "NOT as specified"),
        ("a fairly read arm NOT as specified stands beside an untested mode",
         J([A(0, True), A(1, False), A(2, False, read=0)])["verdict"] == "NOT as specified"),
        ("a probe cut short with its control ACKed is still VOID",
         J([A(0, False), A(1, True)], 5, "OSError: x")["verdict"].startswith("VOID")),
    ]
    import can
    a = O.build_parser().parse_args(["--power", "x", "--x2", "held", "--hold-s", "0.02", "--settle-s", "0"])
    saved = sys.modules.get("kvaser_bus")
    sys.modules["kvaser_bus"] = types.SimpleNamespace(pcan_bus=lambda bitrate: FakePcan(raise_at=()))
    try:
        def arm(lf):        # an arm that raises is a failed check, not a crash
            try:
                return O.one_arm(lf, can, 1, a)
            except Exception as e:      # noqa: BLE001
                return {"raised": repr(e)}
        r1 = arm(PostLF(urllib.error.URLError("timed out")))
        r2 = arm(PostLF(http_error(400)))
    finally:
        if saved is None:
            sys.modules.pop("kvaser_bus", None)
        else:
            sys.modules["kvaser_bus"] = saved
    out += [
        ("arm: a POST with no answer is recorded on the bench path and the arm goes on",
         r1.get("post_ok") is False and (r1.get("post_failure") or {}).get("by") == "bench path"
         and r1.get("mode_read") == 0 and "ack_errors" in r1),
        ("arm: a POST the device refused is recorded with its status",
         (r2.get("post_failure") or {}) == {"by": "device", "http_status": 400}),
    ]
    opened = []

    def pcan_open():
        opened.append(1)
        if len(opened) == 3:
            raise OSError("PCAN gone")
        return FakePcan(raise_at=())
    code, text, rec = run_main(PostLF(http_error(400), bad=()), pcan_open)
    out += [
        ("main: a probe cut short prints how far it got", "STOPPED after 2 of 5 arms: OSError: PCAN gone" in text),
        ("main: and still prints its rows and a verdict line naming the cut short",
         "  mode 1: ack errors" in text and "verdict: INCOMPLETE" in text and O.CUT_SHORT in text),
        ("main: the record keeps why it stopped", rec is not None and rec.get("stopped") == "OSError: PCAN gone"),
        ("main: a probe cut short exits 1", code == 1),
    ]
    code2, text2, rec2 = run_main(PostLF(http_error(400), bad=()), lambda: FakePcan(raise_at=()))
    out.append(("main: a probe run through exits 0 with stopped None",
                code2 == 0 and rec2 is not None and rec2.get("stopped") is None and "STOPPED" not in text2))
    return out


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

    checks += rulings_checks()

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
    # the gen-inhibit reviewer's B14 rulings (2026-11-19): what the probe records for them
    ("an HTTP refusal read as no answer", '    if isinstance(code, int):\n', '    if False:\n'),
    ("the bench-path POST failure not named on its row",
     '("; %s (%s)" % (BENCH_POST, pf.get("error")) if pf.get("by") == "bench path" else',
     '("" if pf.get("by") == "bench path" else'),
    ("a probe cut short with every mode tested reads as specified", "    elif tested != {1, 2} or stopped:\n",
     "    elif tested != {1, 2}:\n"),
    ("the cut short not said", '            verdict += "; %s after %d of %s arms (%s)" % (CUT_SHORT, len(arms), asked, stopped)\n',
     "            pass\n"),
    ("a fairly read FAIL hidden behind INCOMPLETE again",
     '    elif any(r["result"] != "as specified" for r in rows if not r["result"].startswith("INVALID")):\n',
     '    elif False:\n'),
    ("a POST that raises ends the probe",
     '    except Exception as e:      # noqa: BLE001\n        rec = {"mode": mode, "post_ok": False, "post_failure": post_failure(e)}\n',
     '    except ImportError as e:\n        rec = {"mode": mode, "post_ok": False, "post_failure": post_failure(e)}\n'),
    ("a probe cut short raises again, with no verdict",
     "    except Exception as e:      # noqa: BLE001  a probe cut short is judged and recorded, never a bare traceback\n",
     "    except ImportError as e:\n"),
    ("a probe cut short exits 0", '    return 1 if rec["stopped"] else 0', "    return 0"),
    ("the judge not told the probe stopped", '    rec["judge"] = judge(rec["arms"], len(modes), rec["stopped"])',
     '    rec["judge"] = judge(rec["arms"], len(modes), None)'),
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
