#!/usr/bin/env python3
"""Mutants of can2_ack_probe.py; test_can2_ack_probe.py must fail on each BY A CHECK, with no
crash. An edit that does not apply is an error, and the control (the unmutated copy) must pass
first. Gen-inhibit tester, 2026-11-13, the shape of test_x2_jammer_mutants.py.

    py -3.13 -B gen-inhibit/tools/bench/test_can2_ack_probe_mutants.py
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ and its removal (AGENTS.md "Removing scratch")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "can2_ack_probe.py"
TEST = "test_can2_ack_probe.py"
MUTANTS = [
    ("ACKED despite an error frame (RX warning)",
     '    elif tx is not None and tx >= 1 and not any(kinds.values()):\n',
     '    elif tx is not None and tx >= 1:\n'),
    ("ACKED with nothing counted sent",
     '    elif tx is not None and tx >= 1 and not any(kinds.values()):\n',
     '    elif not any(kinds.values()):\n'),
    ("the first judge: retries count only when tx did not move (2026-11-13)",
     '    if retried:\n        # whatever', '    if retried and tx == 0:\n        # whatever'),
    ("NO_ACK with no retries seen", '    if retried:\n        # whatever', '    if True:\n        # whatever'),
    ("the state not read", 'after.get("state") not in (None, "ERROR-ACTIVE"))', 'False)'),
    ("error_passive not read", '("error_passive rose", (delta("error_passive") or 0) > 0),',
     '("error_passive rose", False),'),
    ("error_warning not read", '("error_warning rose", (delta("error_warning") or 0) > 0),',
     '("error_warning rose", False),'),
    ("the ACK bit not read", '        if cid & CAN_ERR_ACK:\n', '        if False:\n'),
    ("any controller frame counts as TX warning/passive",
     'data[1] & (CRTL_TX_WARNING | CRTL_TX_PASSIVE):', 'data[1]:'),
    ("check: UNCLEAR passes", '    if verdict == "UNCLEAR":\n        return "INCONCLUSIVE"\n',
     '    if verdict == "UNCLEAR":\n        return "PASS"\n'),
    ("check: any verdict passes", '    return "PASS" if verdict == expect.upper() else "FAIL"\n',
     '    return "PASS"\n'),
    ("probe: a link already up is used",
     '    if not rec["link_at_start"] or rec["link_at_start"].get("operstate") != "DOWN":\n',
     '    if False:\n'),
    ("probe: the link left up", '        rec["steps"].append(canctl("down", role))\n', '        pass\n'),
    ("probe: up listen-only", '    up = canctl("up", role, str(BITRATE))\n',
     '    up = canctl("up", role, str(BITRATE), "listen-only")\n'),
    ("probe: a failed up carried on", '        if up["rc"] != 0:\n', '        if False:\n'),
    ("parse_link: tx bytes for packets",
     '            "tx_packets": ((d.get("stats64") or {}).get("tx") or {}).get("packets"),',
     '            "tx_packets": ((d.get("stats64") or {}).get("tx") or {}).get("bytes"),'),
    ("probe: data frames counted as errors",
     '                if m.is_error_frame:\n', '                if True:\n'),
]


def run(base, label, text):
    d = os.path.join(base, label)
    gi_paths.remove_tree(d)
    os.makedirs(d)
    open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(text)
    shutil.copy(os.path.join(HERE, TEST), d)
    r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
    fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
    crash = r.stderr.strip().splitlines()[-1][:120] if "Traceback" in r.stderr else ""
    return fails, crash, r.returncode


def main():
    base = gi_paths.scratch("mutants-can2_ack_probe")
    try:
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
        fails, crash, rc = run(base, "control", src)
        control_ok = rc == 0 and not fails and not crash
        print("%s control: the unmutated copy  (exit %d, %d FAIL%s)" % (
            "PASS    " if control_ok else "FAILED  ", rc, len(fails), ", CRASHED: " + crash if crash else ""))
        killed = errors = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            fails, crash, rc = run(base, "m%d" % n, src.replace(old, new))
            ok = bool(fails) and not crash
            killed += ok
            print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
                "KILLED  " if ok else "SURVIVED", n, name, rc, len(fails),
                ", CRASHED: " + crash if crash else "", ("; " + fails[0][:100]) if fails else ""))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
