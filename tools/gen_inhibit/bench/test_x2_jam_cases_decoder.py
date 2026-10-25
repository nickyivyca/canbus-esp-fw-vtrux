#!/usr/bin/env python3
"""Test that x2_jam_cases.py hands its record's decoder identity to the judges it calls
during a live run (2026-10-25, toolkit item 10, reading Q1): the busoff arm's
tec_busoff_sweep.judge_bus_on and the livetrain arm's live_jam_arm.judge. Both read
rec["decoder"] from the dict they are given, so an identity left out of that dict is
never checked: the record would name a DBC its own judgement was not held to.
case_busoff and case_livetrain run end to end with the bench stubbed (the device,
the jammer console, the poller and the witness). Synthetic, no hardware.

    py -3.14 -B tools/gen_inhibit/bench/test_x2_jam_cases_decoder.py
    py -3.14 -B tools/gen_inhibit/bench/test_x2_jam_cases_decoder.py --mutants

Gen-inhibit tester.
"""
import argparse
import importlib.util
import itertools
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)

FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def load(path):
    spec = importlib.util.spec_from_file_location("x2_jam_cases_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def tried(fn):
    """fn's value, or the exception's name, so a crash FAILs a check rather than the run."""
    try:
        return fn()
    except Exception as e:      # noqa: BLE001
        return "raised %s" % type(e).__name__


class FakeWit:
    def __init__(self, frames):
        self.frames, self.lock, self.status, self.status_error = frames, threading.Lock(), [], None

    def clocked(self):
        return list(self.frames), [], {"stub": True}


class FakePoller:
    def __init__(self):
        self.samples = [(101.0 + 0.1 * k, {"mode": 2, "bus_on": True, "drv_present": True, "drv_state": "running"})
                        for k in range(20)]

        class Flag:
            def set(flag):
                pass
        self.stop_flag = Flag()

    def start(self):
        pass

    def join(self, timeout=None):
        pass


def stub_bench(C):
    C.LF.dev_post = lambda path: None
    C.LF.page = lambda: {}
    C.snapx = lambda p: {}
    C.Poller = FakePoller
    C.train = lambda con, w, p, ms: (102.0, {"raw": "ACK"})
    C.sleep_until = lambda t: None
    C.attach_console = lambda arm, t0, t1: None
    C.replay_rate_device_off = lambda frames, ph: 100.0
    C.witness_state_summary = lambda st, lo, hi: {"bus_off": False, "bus_off_from": None, "error_passive": False,
                                                  "tec_max": 0, "rec_max": 0, "flags_seen": []}
    C.time.sleep = lambda s: None
    clock = itertools.count(100.0, 1.0)         # each call a second on: arm windows are not empty
    C.time.time = lambda: next(clock)


def page_7f1(schema):
    return "%02X01030600028813" % schema      # diag_schema_ver, fw, mode 3, diag_flags 0x06 (bus_on)


def checks(C):
    real_judge_bus_on = C.TB.judge_bus_on      # the module is shared: keep it before stubbing
    stub_bench(C)
    head = gi_diag.schemas_for().head_version
    ident = {"harness_commit": "0" * 40, "dbc_path": "gen-inhibit/diag/vtrux-wican-diag.dbc",
             "dbc_commit": "1" * 40, "dbc_version": head, "dbc_hash": "feedfacefeedface"}
    wire = [(101.5, 0x7F1, page_7f1(head))]

    # 1. busoff: judge_bus_on is handed the record's identity.
    seen = []
    C.TB.judge_bus_on = lambda rec: seen.append(rec) or {"verdict": "PASS", "summary": "", "page_mismatch_count": 0,
                                                          "page_states": {"running": 1}}
    a = argparse.Namespace(w=20, p=2000, ms=5000, arms=1, control=False)
    r = tried(lambda: C.case_busoff(a, {"decoder": ident}, None, FakeWit(wire)))
    check("busoff: the case runs with the bench stubbed", not isinstance(r, str), r)
    check("busoff: judge_bus_on is called once per arm", len(seen) == 1, len(seen))
    check("busoff: judge_bus_on is handed the record's decoder identity",
          bool(seen) and seen[0].get("decoder") == ident, seen[0].get("decoder") if seen else None)
    check("busoff: ... and still the arm's 0x7F1s", bool(seen) and seen[0].get("wire_0x7F1") == [(101.5, page_7f1(head))],
          seen[0].get("wire_0x7F1") if seen else None)

    # 2. livetrain: live_jam_arm.judge is handed the record's identity.
    seen_lj = []
    C.LJ.judge = lambda rec: seen_lj.append(rec) or {"verdict": "PASS", "summary": ""}
    ph = {"replay_start": 95.0, "arm_posted": 100.0, "jam_start": 102.0, "jam_stop": 107.0, "mode0_posted": 122.0}
    C.arm_live = lambda a_, con, wit, k, body: {"n": k, "phases": dict(ph), "pcan_status_changes": [],
                                                "samples": [(103.0, {"drv_state": "running"})]}
    a = argparse.Namespace(w=20, p=2000, ms=5000, arms=1)
    r = tried(lambda: C.case_livetrain(a, {"decoder": ident}, None, FakeWit(wire)))
    check("livetrain: the case runs with the bench stubbed", not isinstance(r, str), r)
    check("livetrain: live_jam_arm.judge is called once per arm", len(seen_lj) == 1, len(seen_lj))
    check("livetrain: live_jam_arm.judge is handed the record's decoder identity",
          bool(seen_lj) and seen_lj[0].get("decoder") == ident, seen_lj[0].get("decoder") if seen_lj else None)

    # 3. What the identity is for: the real judge_bus_on refuses a 0x7F1 whose schema's DBC
    # is not the identity's (here a made-up hash), and decodes it when there is no identity.
    shaped = {"baseline": {"samples": [{"pt": t, "snap": s} for t, s in FakePoller().samples]},
              "wire_0x7F1": [(101.5, page_7f1(head))]}
    got = tried(lambda: real_judge_bus_on(dict(shaped, decoder=ident)))
    check("the real judge_bus_on refuses a 0x7F1 on another DBC than the identity's",
          got == "raised SchemaMismatch", got if isinstance(got, str) else got.get("verdict"))
    got = tried(lambda: real_judge_bus_on(dict(shaped)))
    check("... and judges it with no identity", isinstance(got, dict), got)


MUTANTS = [
    ("the busoff arm drops the identity", '"wire_0x7F1": w7f1, "decoder": decoder}', '"wire_0x7F1": w7f1}'),
    ("case_busoff does not pass the identity", 'arm_busoff(a, con, wit, k, tr, rec.get("decoder"))',
     "arm_busoff(a, con, wit, k, tr)"),
    ("the livetrain arm drops the identity", '"witness_sees_replay": True, "decoder": rec.get("decoder")}',
     '"witness_sees_replay": True}'),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        checks(load(os.path.join(HERE, "x2_jam_cases.py")))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = 0
    src = open(os.path.join(HERE, "x2_jam_cases.py"), encoding="utf-8", newline="").read()
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            continue
        with tempfile.TemporaryDirectory(prefix="gi-jam-decoder-m%d-" % i, dir=gi_paths.scratch("tmp", run=False)) as tmp:
            mp = os.path.join(tmp, "x2_jam_cases.py")
            open(mp, "w", encoding="utf-8", newline="").write(src.replace(old, new))
            env = dict(os.environ, PYTHONPATH=HERE + os.pathsep + os.environ.get("PYTHONPATH", ""))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--module", mp],
                               capture_output=True, text=True, timeout=300, env=env)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
        crashed = r.returncode not in (0, 1) or "Traceback" in r.stderr
        if fails and not crashed:
            killed += 1
            print("KILLED   m%d %s  (%d FAIL; %s)" % (i, name, len(fails), fails[0][6:90]))
        else:
            print("%s m%d %s  (rc %d)%s" % ("CRASHED " if crashed else "SURVIVED", i, name, r.returncode,
                                            ("  " + r.stderr.strip().splitlines()[-1]) if crashed and r.stderr.strip() else ""))
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--module"]:
        checks(load(sys.argv[2]))
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
