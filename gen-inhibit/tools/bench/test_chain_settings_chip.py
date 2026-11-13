#!/usr/bin/env python3
"""Test that the chain settings name the X2's chip as the shared core's mandatory chip
check (core e37995a, flash.py) needs, using the core's own functions.

    python3.13 -B gen-inhibit/tools/bench/test_chain_settings_chip.py SETTINGS
    python3.13 -B gen-inhibit/tools/bench/test_chain_settings_chip.py --can-fail SETTINGS
                 # the settings' PYTHONPATH joined with the other separator: the core
                 # check must fail (exit 0 only if it does)

SETTINGS, or GI_CHAIN_SETTINGS, is this machine's chain settings file in reverse-it
(local, unpublished: it holds the X2's MAC, which no published file may); with neither
it refuses. The core is imported from the PYTHONPATH the settings give hosts.local,
split on this platform's separator (":" on Linux), and checked to be that core: until
2026-11-12 it split on ";" alone, so on Linux the settings' entries were one bogus path
and the core came from the caller's PYTHONPATH instead (the reviewer's Linux leftovers
item 5; the evidence driver now passes SETTINGS). The chip-id text is the X2's own, from the
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


def main():
    s = json.load(open(SETTINGS, encoding="utf-8"))
    named = [p for p in (s.get("hosts", {}).get("local", {}).get("env", {}).get("PYTHONPATH") or "").split(os.pathsep)
             if p]
    for p in reversed(named):
        sys.path.insert(0, p)
    try:
        from vtrux_bench.run import flash as F
    except Exception as e:
        check("the core named by the settings imports", False, repr(e))
        print("\n%d ok, %d FAIL" % (oks, len(fails)))
        return 1
    src = os.path.realpath(F.__file__)
    print("core: %s" % src)
    check("the core imported is the one the settings' PYTHONPATH names, split on %r" % os.pathsep,
          any(src.startswith(os.path.realpath(p) + os.sep) for p in named), (src, named))
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
    """The settings' PYTHONPATH joined with the other platform's separator, in a copy in
    this run's scratch/ folder: the core check must FAIL (and the test exit 1), the core
    then coming from the caller's PYTHONPATH."""
    import subprocess
    s = json.load(open(SETTINGS, encoding="utf-8"))
    env = s.get("hosts", {}).get("local", {}).get("env", {})
    named = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]
    if len(named) < 2:
        print("NOT JUDGED: the settings' PYTHONPATH names %d entr%s; the can-fail needs two"
              % (len(named), "y" if len(named) == 1 else "ies"))
        return 1
    env["PYTHONPATH"] = (";" if os.pathsep == ":" else ":").join(named)
    copy_path = os.path.join(gi_paths.scratch("chain_settings_chip"), "settings_other_separator.json")
    with open(copy_path, "w", encoding="utf-8") as fh:
        json.dump(s, fh)
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), copy_path], capture_output=True, text=True,
                       timeout=120)
    hit = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL the core imported is the one")]
    ok = r.returncode == 1 and bool(hit)
    print("%s  the settings' PYTHONPATH joined with %r: the core check fails, exit %d"
          % ("FAILED as it must" if ok else "DID NOT FAIL", env["PYTHONPATH"][len(named[0])], r.returncode))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(can_fail() if CAN_FAIL else main())
