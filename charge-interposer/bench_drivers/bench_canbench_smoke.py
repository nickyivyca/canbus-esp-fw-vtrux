"""Bench smoke for the can-tools pin: the kvaser and gs_usb segments opened
through bus.open_bus, with canbench from the pinned can-tools commit
(can_tools_pin.py), not from the clone through can-tools.pth.

Charge-interposer tester, 2026-11-01. The reviewer's acceptance for moving
the pin (2026-10-30 at the cutover, again 2026-11-01 for 1cd3942) asks for
this smoke on the bench. It was a scratch script at the cutover; it is
committed harness now, because a check a VERIFICATION.md entry rests on is
(AGENTS.md "Spec, validation and verification", 2026-11-01).

  1. refusal: a child with CAN_TOOLS_DIR unset gets paths.PathsError naming
     CAN_TOOLS_DIR from open_bus(kvaser), and imports no canbench;
  2. the sys.path entries holding a canbench/ (the .pth's clone) are taken
     off, then open_bus(kvaser) as the vehicle side: canbench must be loaded
     from scratch/can-tools/<pin>/canbench after the pin's check, and no
     sys.path entry may hold a canbench/ afterwards;
  3. open_bus(gs_usb) as the charger side;
  4. quiet: both listen 3 s (counted by ID, not judged: the board is
     listen-only while the charger is silent, spec 2.3);
  5. charger up: gs_usb sends the charger status frame (0x18FFD4C0, spec
     2.1/2.3) at 10 Hz for 4 s, with no send errors; kvaser must hear all
     four diag pages 0x7F4-0x7F7, the board's vehicle-port frames
     (bench_topology.py's discriminator). The 0x7F7 frames it heard give
     the X2's build id, decoded with the git diag DBC (paths.diag_dbc(), as
     bench_identify.py does); they must agree, and equal --expect-x2 when
     given;
  6. fall-back: both listen 3 s more (not judged).

Observations go to <out>/canbench_smoke.json and the judged lines to
<out>/canbench_smoke.txt. The judge works on the observations alone, so the
self-test runs it on made-up ones, and the refusal of step 1 for real (it
needs no adapter).

    CAN_TOOLS_DIR=<clone> py -3.14 -B bench_canbench_smoke.py --out DIR [--expect-x2 ID]
    py -3.14 -B bench_canbench_smoke.py --self-test
    py -3.14 -B bench_canbench_smoke.py --can-fail
Exit 0 PASS, 1 FAIL.
"""

import argparse
import collections
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths                                                 # noqa: E402

# charge-interposer/, found from paths.py rather than from this file, so a
# copy run by script_can_fail.py from its scratch folder finds it too
CODE = os.path.dirname(os.path.abspath(paths.__file__))
DIAG_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)
CHARGER_ID = 0x18FFD4C0
BITRATE = 500000

REFUSAL_CHILD = r'''
import argparse, os, sys
sys.path[:] = [p for p in sys.path
               if not os.path.isdir(os.path.join(p or ".", "canbench"))]
sys.path.insert(0, %r)
import bus, paths
ns = argparse.Namespace(transport="kvaser", channel=None, port=0, bitrate=500000)
try:
    b = bus.open_bus(ns)
except paths.PathsError as e:
    print("REFUSED", e)
    print("canbench imported:", "canbench" in sys.modules)
    sys.exit(0)
b.shutdown()
print("OPENED")
sys.exit(1)
'''


def refusal():
    """Step 1, for real: no adapter is reached, the refusal comes first."""
    env = dict(os.environ)
    env.pop("CAN_TOOLS_DIR", None)
    r = subprocess.run([sys.executable, "-B", "-c", REFUSAL_CHILD % CODE],
                       env=env, capture_output=True, text=True, timeout=60)
    return {"rc": r.returncode, "out": (r.stdout + r.stderr).strip()}


def x2_build_ids(frames):
    """The build ids in the 0x7F7 payloads (hex strings) heard on kvaser."""
    import cantools
    m = cantools.database.load_file(paths.diag_dbc()).get_message_by_frame_id(0x7F7)
    return ["%08x" % m.decode(bytes.fromhex(h))["intp_build_id"] for h in frames]


def judge(obs, expect_x2=None):
    """(lines, ok) from the observations; every judged line says ok or FAIL."""
    lines = []
    ok = True

    def say(good, text):
        nonlocal ok
        ok = ok and good
        lines.append("%s %s" % ("ok  " if good else "FAIL", text))

    ref = obs["refusal"]
    say(ref["rc"] == 0 and "REFUSED" in ref["out"],
        "1 open_bus(kvaser) with CAN_TOOLS_DIR unset is refused: %s"
        % ref["out"].replace("\n", " | "))
    say("CAN_TOOLS_DIR" in ref["out"], "  the refusal names CAN_TOOLS_DIR")
    say("canbench imported: False" in ref["out"],
        "  and the refused child imported no canbench")
    pin = obs["pin"]
    say(os.path.normcase(pin["canbench_file"]) == os.path.normcase(pin["want_file"]),
        "2 kvaser opened (%s); canbench loaded from %s; the pin %s says %s"
        % (obs["kvaser"]["type"], pin["canbench_file"], pin["commit"],
           pin["want_file"]))
    say(not pin["on_path_after"],
        "  no sys.path entry holds a canbench/ after the open (%s)"
        % (pin["on_path_after"] or "none"))
    lines.append("3 --   gs_usb opened (%s)" % obs["gs_usb"]["type"])
    lines.append("4 --   quiet 3 s: kvaser heard %s; gs_usb heard %s"
                 % (fmt(obs["quiet"]["kvaser"]), fmt(obs["quiet"]["gs_usb"])))
    up = obs["up"]
    heard = [i for i in DIAG_IDS if up["kvaser"].get("0x%X" % i)]
    say(len(heard) == len(DIAG_IDS),
        "5 kvaser heard the diag pages %s with the charger up; kvaser heard %s; "
        "gs_usb heard %s" % (["0x%X" % i for i in heard], fmt(up["kvaser"]),
                             fmt(up["gs_usb"])))
    say(up["sent"] > 0, "  gs_usb sent 0x%X x%d" % (CHARGER_ID, up["sent"]))
    say(up["errs"] == 0, "  with %d send errors" % up["errs"])
    ids = sorted(set(x2_build_ids(up["x7f7"])))
    say(len(ids) == 1, "  the X2's build id from %d 0x7F7 frames: %s"
        % (len(up["x7f7"]), ids or "none"))
    if expect_x2 is not None:
        say(ids == [expect_x2.lower()], "  expected %s" % expect_x2)
    lines.append("6 --   after 3 s: kvaser heard %s; gs_usb heard %s"
                 % (fmt(obs["after"]["kvaser"]), fmt(obs["after"]["gs_usb"])))
    lines.append("canbench smoke: %s" % ("PASS" if ok else "FAIL"))
    return lines, ok


def fmt(c):
    return ", ".join("%s x%d" % (k, v) for k, v in sorted(c.items())) or "nothing"


class Drain:
    def __init__(self, b):
        self.b = b
        self.ids = collections.Counter()
        self.data = collections.defaultdict(list)
        self.lock = threading.Lock()
        self.stop = False
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        while not self.stop:
            try:
                m = self.b.recv(0.1)
            except Exception:
                continue
            if m is not None and not m.is_error_frame:
                with self.lock:
                    self.ids["0x%X" % m.arbitration_id] += 1
                    if m.arbitration_id == 0x7F7:
                        self.data[0x7F7].append(bytes(m.data).hex())

    def take(self):
        with self.lock:
            ids, data = dict(self.ids), self.data
            self.ids, self.data = collections.Counter(), collections.defaultdict(list)
        return ids, data


def observe():
    """Steps 1-6 on the bench. Returns the observations."""
    import argparse as _a
    obs = {"refusal": refusal()}
    held = [p for p in sys.path if os.path.isdir(os.path.join(p or ".", "canbench"))]
    sys.path[:] = [p for p in sys.path if p not in held]
    for m in [m for m in sys.modules if m == "canbench" or m.startswith("canbench.")]:
        del sys.modules[m]
    import bus
    import can_tools_pin
    p = can_tools_pin.pin()
    folder = can_tools_pin.verify(can_tools_pin.folder(p), p, can_tools_pin.clone())

    def ns(t):
        return _a.Namespace(transport=t, channel=None, port=0, bitrate=BITRATE)
    t0 = time.monotonic()
    vb = bus.open_bus(ns("kvaser"))
    obs["kvaser"] = {"type": type(vb).__name__, "s": round(time.monotonic() - t0, 2)}
    obs["pin"] = {
        "commit": p["commit"], "taken_off_path": len(held),
        "canbench_file": os.path.abspath(sys.modules["canbench"].__file__),
        "want_file": os.path.join(folder, "canbench", "__init__.py"),
        "on_path_after": [q for q in sys.path
                          if os.path.isdir(os.path.join(q or ".", "canbench"))]}
    cb = bus.open_bus(ns("gs_usb"))
    obs["gs_usb"] = {"type": type(cb).__name__}
    dv, dc = Drain(vb), Drain(cb)
    try:
        time.sleep(3.0)
        obs["quiet"] = {"kvaser": dv.take()[0], "gs_usb": dc.take()[0]}
        import can
        msg = can.Message(arbitration_id=CHARGER_ID, is_extended_id=True,
                          data=bytes(8))
        sent = errs = 0
        t_end = time.monotonic() + 4.0
        while time.monotonic() < t_end:
            try:
                cb.send(msg, timeout=0.2)
                sent += 1
            except Exception:
                errs += 1
            time.sleep(0.1)
        (kv, kd), (gc, _gd) = dv.take(), dc.take()
        obs["up"] = {"sent": sent, "errs": errs, "kvaser": kv, "gs_usb": gc,
                     "x7f7": kd.get(0x7F7, [])}
        time.sleep(3.0)
        obs["after"] = {"kvaser": dv.take()[0], "gs_usb": dc.take()[0]}
    finally:
        dv.stop = dc.stop = True
        dv.t.join(1)
        dc.t.join(1)
        vb.shutdown()
        cb.shutdown()
    return obs


# ---- self-test ------------------------------------------------------------
def _frame_7f7(build_id):
    import cantools
    m = cantools.database.load_file(paths.diag_dbc()).get_message_by_frame_id(0x7F7)
    d = m.decode(bytes(m.length), decode_choices=False)
    d["intp_build_id"] = build_id
    return m.encode(d).hex()


def _good():
    pages = {"0x7F4": 4, "0x7F5": 4, "0x7F6": 4, "0x7F7": 4, "0x18FFD4C0": 40}
    f = os.path.join("scratch", "can-tools", "c" * 40, "canbench", "__init__.py")
    return {
        "refusal": {"rc": 0, "out": "REFUSED CAN_TOOLS_DIR is not set. ...\n"
                                    "canbench imported: False"},
        "pin": {"commit": "c" * 40, "taken_off_path": 1, "canbench_file": f,
                "want_file": f, "on_path_after": []},
        "kvaser": {"type": "KvaserBus", "s": 0.1},
        "gs_usb": {"type": "GsUsbNoEcho"},
        "quiet": {"kvaser": {}, "gs_usb": {}},
        "up": {"sent": 40, "errs": 0, "kvaser": pages, "gs_usb": {},
               "x7f7": [_frame_7f7(0x756d6714)] * 4},
        "after": {"kvaser": {"0x7F4": 2}, "gs_usb": {}},
    }


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    print("the judge on made-up observations")
    _lines, ok = judge(_good())
    check(ok, "a good smoke passes")
    _lines, ok = judge(_good(), expect_x2="756d6714")
    check(ok, "... and with --expect-x2 756d6714")
    bad = [
        # the other two refusal lines hold here, so only the first can
        # fail it (can-fail, 2026-11-01: with a bare "OPENED" the other two
        # failed it and the first went unchecked)
        ("the refused child opened the adapter",
         lambda o: o["refusal"].update(
             rc=1, out="OPENED with CAN_TOOLS_DIR unset\n"
                       "canbench imported: False")),
        ("the refusal does not name CAN_TOOLS_DIR",
         lambda o: o["refusal"].update(out="REFUSED something else\n"
                                           "canbench imported: False")),
        ("the refused child imported canbench",
         lambda o: o["refusal"].update(out="REFUSED CAN_TOOLS_DIR is not set\n"
                                           "canbench imported: True")),
        ("canbench loaded from the clone",
         lambda o: o["pin"].update(canbench_file=os.path.join(
             "Git", "can-tools", "canbench", "__init__.py"))),
        ("a sys.path entry holds a canbench/ after the open",
         lambda o: o["pin"].update(on_path_after=["Git/can-tools"])),
        ("kvaser heard three diag pages",
         lambda o: o["up"]["kvaser"].pop("0x7F6")),
        ("gs_usb had send errors", lambda o: o["up"].update(errs=2)),
        ("gs_usb sent nothing", lambda o: o["up"].update(sent=0)),
        ("no 0x7F7 frame was kept", lambda o: o["up"].update(x7f7=[])),
        ("two build ids in the 0x7F7 frames",
         lambda o: o["up"]["x7f7"].append(_frame_7f7(0x1234abcd))),
    ]
    for what, spoil in bad:
        o = _good()
        spoil(o)
        _lines, ok = judge(o)
        check(not ok, "FAIL when %s" % what)
    _lines, ok = judge(_good(), expect_x2="deadbeef")
    check(not ok, "FAIL when the build id is not --expect-x2")

    print("step 1 for real (no adapter is reached)")
    r = refusal()
    check(r["rc"] == 0 and "REFUSED" in r["out"] and "CAN_TOOLS_DIR" in r["out"]
          and "canbench imported: False" in r["out"],
          "open_bus(kvaser) with CAN_TOOLS_DIR unset is refused, naming it, "
          "with no canbench imported -> %s" % r["out"].replace("\n", " | ")[:200])
    print("\nbench_canbench_smoke --self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out")
    g.add_argument("--self-test", action="store_true")
    g.add_argument("--can-fail", action="store_true")
    ap.add_argument("--expect-x2", help="the X2's build id, 8 hex digits")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    os.makedirs(a.out, exist_ok=True)
    head = ["canbench smoke, %s" % time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "worktree commit %s" % subprocess.run(
                ["git", "-C", CODE, "rev-parse", "HEAD"], capture_output=True,
                text=True).stdout.strip()]
    obs = observe()
    with open(os.path.join(a.out, "canbench_smoke.json"), "w",
              newline="\n") as f:
        json.dump(obs, f, indent=1, sort_keys=True)
        f.write("\n")
    lines, ok = judge(obs, a.expect_x2)
    text = "\n".join(head + lines) + "\n"
    print(text, end="")
    with open(os.path.join(a.out, "canbench_smoke.txt"), "w", newline="\n") as f:
        f.write(text)
    return 0 if ok else 1


MUTANTS = [
    ("an opened adapter counts as refused",
     'say(ref["rc"] == 0 and "REFUSED" in ref["out"],',
     'say(True,'),
    ("the refusal need not name CAN_TOOLS_DIR",
     'say("CAN_TOOLS_DIR" in ref["out"],',
     'say(True,'),
    ("canbench imported by the refused child is not looked at",
     'say("canbench imported: False" in ref["out"],',
     'say(True,'),
    ("canbench from anywhere passes",
     'say(os.path.normcase(pin["canbench_file"]) == os.path.normcase(pin["want_file"]),',
     'say(True,'),
    ("a canbench/ left on sys.path passes",
     'say(not pin["on_path_after"],',
     'say(True,'),
    ("one diag page is enough",
     'say(len(heard) == len(DIAG_IDS),',
     'say(len(heard) >= 1,'),
    ("send errors are not counted",
     'say(up["errs"] == 0,',
     'say(True,'),
    ("nothing sent passes",
     'say(up["sent"] > 0,',
     'say(True,'),
    ("any number of build ids passes",
     'say(len(ids) == 1,',
     'say(True,'),
    ("--expect-x2 is not compared",
     'say(ids == [expect_x2.lower()],',
     'say(True,'),
]

if __name__ == "__main__":
    sys.exit(main())
