#!/usr/bin/env python3
"""Test that the chain settings name the X2's chip as the shared core's mandatory chip
check (core e37995a, flash.py) needs, using the core's own functions.

    py -3.14 -B tools/gen_inhibit/bench/test_chain_settings_chip.py [SETTINGS]

SETTINGS defaults to chain_settings_NICKY-XPS.json beside this file (local, unpublished:
it holds the X2's MAC, which no published file may). The core is imported from the
PYTHONPATH the settings give hosts.local. The chip-id text is the X2's own, from the
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
SETTINGS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GI_CHAIN_SETTINGS") or sys.exit(
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
    for p in (s.get("hosts", {}).get("local", {}).get("env", {}).get("PYTHONPATH") or "").split(";"):
        if p:
            sys.path.insert(0, p)
    try:
        from vtrux_bench.run import flash as F
    except Exception as e:
        check("the core named by the settings imports", False, repr(e))
        print("\n%d ok, %d FAIL" % (oks, len(fails)))
        return 1
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


if __name__ == "__main__":
    sys.exit(main())
