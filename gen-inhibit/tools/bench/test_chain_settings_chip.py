#!/usr/bin/env python3
"""Test that the chain settings name the X2's chip as the shared core's mandatory chip
check (core e37995a, flash.py) needs, using the core's own functions.

    python3.13 -B gen-inhibit/tools/bench/test_chain_settings_chip.py SETTINGS
    python3.13 -B gen-inhibit/tools/bench/test_chain_settings_chip.py --can-fail SETTINGS
                 # copies with the core back on PYTHONPATH and with an empty
                 # wallclock_patterns: each check must fail (exit 0 only if both do)

SETTINGS, or GI_CHAIN_SETTINGS, is this machine's chain settings file in reverse-it
(local, unpublished: it holds the X2's MAC, which no published file may); with neither
it refuses. The core is the checkout gen-inhibit pins (gi_core_pin; port step 4 item 1,
2026-11-25): the settings' hosts.local PYTHONPATH must put no vtrux_bench on the path
(split on either separator: until 2026-11-12 a split on ";" alone hid the entries on
Linux), the settings give no wallclock_patterns (the core's default list is used), and
the core imported is the pinned checkout's. The chip-id text is the X2's own, from the
e3d9437 chain run's auto-reset check (runs/chain/gen-inhibit-bench_20261020T004332_59b0/
_bench/man_slcan/autoreset_after.txt). Controls: the same text with another MAC, and
settings with no MAC or another chip type, must each be refused. Gen-inhibit tester,
2026-10-20.
"""
import copy
import json
import os
import re
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
CAN_FAIL = "--can-fail" in sys.argv[1:]
ARGS = [a for a in sys.argv[1:] if a != "--can-fail"]
SETTINGS = ARGS[0] if ARGS else os.environ.get("GI_CHAIN_SETTINGS") or sys.exit(
    "give the chain settings file as the argument or in GI_CHAIN_SETTINGS (it is local, never committed)")
CHIPID = gi_paths.runs("chain", "gen-inhibit-bench_20261020T004332_59b0", "_bench", "man_slcan",
                      "autoreset_after.txt")
fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def core_entries(s):
    """The entries of the settings' hosts.local PYTHONPATH, split on either separator, that
    hold a vtrux_bench package: a core put on the path by the settings."""
    pp = s.get("hosts", {}).get("local", {}).get("env", {}).get("PYTHONPATH") or ""
    return [p for p in re.split(r"[;:]", pp) if p and os.path.isdir(os.path.join(p, "vtrux_bench"))]


def main():
    s = json.load(open(SETTINGS, encoding="utf-8"))
    # The core is the checkout gen-inhibit pins (port step 4 item 1, 2026-11-25): the
    # settings put none on the path, and give no wallclock_patterns of their own (the core's
    # procs.DEFAULT_WALLCLOCK_PATTERNS, which this suite's list moved into, is used).
    check("the settings' hosts.local PYTHONPATH puts no core (vtrux_bench) on the path",
          core_entries(s) == [], core_entries(s))
    check("the settings give no wallclock_patterns (the core's default list)",
          "wallclock_patterns" not in s, "wallclock_patterns" in s)
    try:
        sys.path.insert(0, HERE)
        import bench_held
        folder, why = bench_held.use_core()
        if folder is None:
            raise RuntimeError(why)
        from vtrux_bench.run import flash as F
    except Exception as e:
        check("the pinned core imports", False, repr(e))
        print("\n%d ok, %d FAIL" % (oks, len(fails)))
        return 1
    src = os.path.realpath(F.__file__)
    print("core: %s" % src)
    check("the core imported is the pinned checkout's (gi_core_pin)",
          src.startswith(os.path.realpath(folder) + os.sep), (src, folder))
    check("the core has the mandatory chip check", F.RULES.get("chip_required") is True, F.RULES)
    chip = (s.get("esptool") or {}).get("chip")
    check("settings.esptool names a chip", isinstance(chip, dict), chip)
    check("the chip is named in full", F.chip_unnamed(chip) is None, F.chip_unnamed(chip))
    text = open(CHIPID, encoding="utf-8", errors="replace").read()
    check("the X2's own chip-id output matches it", F.chip_mismatch(text, chip) is None, F.chip_mismatch(text, chip))
    other = re.sub(r"(?m)^(MAC:\s*)[0-9a-fA-F:]{17}", r"\g<1>00:00:5e:00:53:01", text)
    check("control: another MAC on the port is refused", F.chip_mismatch(other, chip) is not None, other[-200:])
    no_mac = copy.deepcopy(chip or {})
    no_mac.pop("mac", None)
    check("control: a chip with no MAC is refused as unnamed", F.chip_unnamed(no_mac) is not None, no_mac)
    wrong = dict(chip or {}, type="ESP32-C3")
    check("control: another chip type is refused", F.chip_mismatch(text, wrong) is not None, wrong)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


def can_fail():
    """Two copies of the settings in this run's scratch/ folder, each of which must FAIL
    its check (and the test exit 1): one with the pinned core put back on hosts.local's
    PYTHONPATH (joined with the other platform's separator, which the check must still
    see), one with an empty wallclock_patterns."""
    import subprocess
    sys.path.insert(0, HERE)
    import bench_held
    folder, why = bench_held.use_core()
    if folder is None:
        print("NOT JUDGED: the pinned core is refused: %s" % why)
        return 1
    d = gi_paths.scratch("chain_settings_chip")
    ok_all = True
    for label, edit, want in (
            ("the core back on PYTHONPATH",
             lambda s: s["hosts"]["local"]["env"].__setitem__(
                 "PYTHONPATH", (";" if os.pathsep == ":" else ":").join(
                     [folder] + [p for p in [s["hosts"]["local"]["env"].get("PYTHONPATH")] if p])),
             "FAIL the settings' hosts.local PYTHONPATH puts no core"),
            ("an empty wallclock_patterns", lambda s: s.__setitem__("wallclock_patterns", []),
             "FAIL the settings give no wallclock_patterns")):
        s = json.load(open(SETTINGS, encoding="utf-8"))
        s.setdefault("hosts", {}).setdefault("local", {}).setdefault("env", {})
        edit(s)
        copy_path = os.path.join(d, "settings_%d.json" % len(label))
        with open(copy_path, "w", encoding="utf-8") as fh:
            json.dump(s, fh)
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), copy_path], capture_output=True,
                           text=True, timeout=120)
        hit = [ln for ln in r.stdout.splitlines() if ln.startswith(want)]
        ok = r.returncode == 1 and bool(hit)
        ok_all &= ok
        print("%s  %s: its check fails, exit %d" % ("FAILED as it must" if ok else "DID NOT FAIL", label,
                                                   r.returncode))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(can_fail() if CAN_FAIL else main())
