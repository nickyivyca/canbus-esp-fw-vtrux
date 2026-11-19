#!/usr/bin/env python3
"""Test diag_build_page_check.open_buses (B18): where it finds the Kvaser. With --mutants, its
mutants.

    python3.13 -B gen-inhibit/tools/bench/test_diag_build_open.py [--mutants]

The Kvaser on Linux is netdev kvaser on kvaser_usb, opened through SocketCAN (gen-inhibit
reviewer, 2026-11-18, ruling (a)), and CANlib's detection (can.detect_available_configs)
does not find it there, so B18 takes it from kvaser_bus.detect. Against a fake can and a
fake kvaser_bus: detect's config is opened, PCAN first, then the Kvaser on the channel detect
gave, through kvaser_bus.bus; CANlib's detection is not asked for the Kvaser (a Kvaser it
listed, virtual or not, opens nothing); no Kvaser from detect refuses before anything opens.
DIAG_BUILD_SCRIPT overrides the script under test. Gen-inhibit tester, 2026-11-18.
"""
import importlib.util
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders
SCRIPT = os.environ.get("DIAG_BUILD_SCRIPT") or os.path.join(HERE, "diag_build_page_check.py")
FAILS = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def load(kv_cfg, canlib_lists, pcan_refused=None):
    """The script with a fake can (detect lists the PCAN, plus the Kvaser through CANlib when
    canlib_lists) and a fake kvaser_bus (detect gives kv_cfg; bus logs; pcan_role gives role pcanfd,
    device id 0, or raises RoleError(pcan_refused)). -> (module, log)."""
    log = []
    can = types.ModuleType("can")

    def detect_available_configs(ifaces):
        log.append(("detect", tuple(ifaces)))
        out = [{"interface": "pcan", "channel": "PCAN_USBBUS1"}]
        if canlib_lists and "kvaser" in ifaces:
            out.append({"interface": "kvaser", "channel": 0, "device_name": "Kvaser Leaf Light v2"})
        return [c for c in out if c["interface"] in ifaces]
    can.detect_available_configs = detect_available_configs

    class FakeBus:          # not named Bus: test_kvaser_bus's scan reads any Bus(**kw) as an open
        def __init__(self, kw):
            self.kw = kw

        def reset(self):
            pass
    kb = types.ModuleType("kvaser_bus")
    kb.detect = lambda: (log.append(("kb.detect",)), kv_cfg)[1]

    class RoleError(RuntimeError):
        pass
    kb.RoleError = RoleError

    def pcan_role():
        log.append(("kb.pcan_role",))
        if pcan_refused:
            raise RoleError(pcan_refused)
        return {"role": "pcanfd", "device_id": 0, "chardev": "pcanusbfd32"}
    kb.pcan_role = pcan_role

    def bus(**kw):
        log.append(("open", kw.get("interface"), kw.get("channel")))
        return FakeBus(kw)
    kb.bus = bus
    saved = {k: sys.modules.get(k) for k in ("can", "kvaser_bus")}
    sys.modules["can"], sys.modules["kvaser_bus"] = can, kb
    try:
        spec = importlib.util.spec_from_file_location("diag_build_under_test", SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    m.can = can
    return m, log, kb


def opens(m, kb):
    saved = sys.modules.get("kvaser_bus")
    sys.modules["kvaser_bus"] = kb
    try:
        return m.open_buses(False), None
    except (SystemExit, Exception) as e:      # noqa: BLE001  any other error is a FAIL, not a crash
        return None, e
    finally:
        if saved is None:
            sys.modules.pop("kvaser_bus", None)
        else:
            sys.modules["kvaser_bus"] = saved


def tests():
    sock = {"interface": "kvaser", "channel": "kvaser"}
    m, log, kb = load(sock, canlib_lists=False)
    buses, err = opens(m, kb)
    o = [e for e in log if e[0] == "open"]
    check("the Kvaser from kvaser_bus.detect (netdev kvaser) is opened, after the PCAN, which is opened by its "
          "role (no channel: kvaser_bus.bus -> pcan_bus) once kvaser_bus.pcan_role resolved it",
          err is None and o == [("open", "pcan", None), ("open", "kvaser", "kvaser")]
          and ("kb.pcan_role",) in log, (err, log))
    check("detection is not asked for the PCAN (2026-11-18)",
          all("pcan" not in e[1] for e in log if e[0] == "detect"), log)
    check("CANlib's detection is not asked for the Kvaser",
          all("kvaser" not in e[1] for e in log if e[0] == "detect") and ("kb.detect",) in log, log)

    m, log, kb = load(sock, canlib_lists=False, pcan_refused="role pcanfd: 2 PCAN devices attached")
    buses, err = opens(m, kb)
    check("the PCAN's role refused (two attached) refuses the run, and nothing opens",
          isinstance(err, SystemExit) and "pcan" in str(err) and not [e for e in log if e[0] == "open"], (err, log))
    m, log, kb = load(None, canlib_lists=True)
    buses, err = opens(m, kb)
    check("no Kvaser from kvaser_bus.detect refuses, though CANlib lists one, and nothing opens",
          err is not None and "kvaser" in str(err) and not [e for e in log if e[0] == "open"], (err, log))
    print("\n%s" % ("PASS" if not FAILS else "FAIL: %d checks" % len(FAILS)))
    return 1 if FAILS else 0


MUTANTS = [
    ("CANlib's detection kept for the Kvaser", 'can.detect_available_configs(["ixxat"])',
     'can.detect_available_configs(["kvaser", "ixxat"])'),
    ("the PCAN by detection's channel", '            kw = {"auto_reset": True}          # by role',
     '            kw = {"auto_reset": True, "channel": "PCAN_USBBUS1"}          # by role'),
    ("the PCAN found by detection", 'can.detect_available_configs(["ixxat"])', 'can.detect_available_configs(["pcan", "ixxat"])'),
    ("a refused role ignored", '        found["pcan"] = {"interface": "pcan", "role": r["role"], "device_id": r["device_id"]}\n'
     '    except RoleError as e:\n        print("  PCAN: %s" % e)\n',
     '        found["pcan"] = {"interface": "pcan", "role": r["role"], "device_id": r["device_id"]}\n'
     '    except RoleError as e:\n        found["pcan"] = {"interface": "pcan", "role": "pcanfd", "device_id": None}\n'),
    ("kvaser_bus.detect not used", '    if kv is not None:\n        found["kvaser"] = kv\n', ""),
    ("the Kvaser's channel not detect's",
     '            kw = {"channel": found[name]["channel"]}\n',
     '            kw = {"channel": 0 if name == "kvaser" else found[name]["channel"]}\n'),
]


def mutants():
    src = open(os.path.join(HERE, "diag_build_page_check.py"), encoding="utf-8", newline="").read()
    base = gi_paths.scratch("mutants-diag_build_open")
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate([(None, None, None)] + MUTANTS):
            if old is not None and src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            gi_paths.remove_tree(d)
            os.makedirs(d)
            path = os.path.join(d, "diag_build_page_check.py")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(src if old is None else src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, DIAG_BUILD_SCRIPT=path, PYTHONDONTWRITEBYTECODE="1"),
                               timeout=300)
            fl = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
            crashed = "Traceback" in r.stderr
            if old is None:
                if r.returncode != 0 or fl or crashed:
                    print("FAILED   control (exit %d)\n%s%s" % (r.returncode, r.stdout[-800:], r.stderr[-800:]))
                    return 1
                print("PASS     control: the unmutated copy")
                continue
            if crashed:
                errors += 1
                print("ERROR    m%d %s: crashed\n%s" % (n, name, r.stderr[-400:]))
            elif fl:
                killed += 1
                print("KILLED   m%d %s  (%s)" % (n, name, fl[0][:100]))
            else:
                print("SURVIVED m%d %s" % (n, name))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else tests())
