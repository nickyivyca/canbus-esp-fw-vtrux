#!/usr/bin/env python3
"""Mutants of e4_device_calibration.py's replay start (gen-inhibit reviewer's answer to the
run (C) report, item 4(iv), 2026-11-20: the base after the --flood-slcan filler, the start-lag
guard, each refusal kept); test_e4_replay_start.py must fail on each BY A CHECK. A crash is not
a kill, and an edit that does not apply is an error. The unmutated copy runs first as the
control.

    python3.13 -B gen-inhibit/tools/bench/test_e4_replay_start_mutants.py

Each mutant is a copy of e4_device_calibration.py in the worktree's scratch/, one edit
applied, run through E4_DIR. A few seconds a mutant. Gen-inhibit tester, 2026-11-20.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

TEST = os.path.join(HERE, "test_e4_replay_start.py")
TARGET = "e4_device_calibration.py"
NEW_BASE = ("    base = perf()\n"
            "    # The replay's clock against the Kvaser's (epoch): placing a POST against the\n"
            "    # device's first frame on the wire needs both (reviewer, 2026-10-14).\n"
            "    base_epoch = time.time() - (perf() - base)\n"
            "    for k in range(len(msgs)):\n")
MUTANTS = [
    (None, None, None),
    ("the base taken before the filler opens again, as in run C",
     [(NEW_BASE, "    for k in range(len(msgs)):\n"),
      ("    fails = 0\n    ledger = ReplayLedger()\n",
       "    base = perf()\n    base_epoch = time.time() - (perf() - base)\n"
       "    fails = 0\n    ledger = ReplayLedger()\n")]),
    ("the base taken after the filler opens but before it starts",
     [(NEW_BASE, "    for k in range(len(msgs)):\n"),
      ("    if sl is not None:\n        sl_filler = SlcanFiller(sl, SLCAN_FILLER_WIRE_S)\n",
       "    base = perf()\n    base_epoch = time.time() - (perf() - base)\n"
       "    if sl is not None:\n        sl_filler = SlcanFiller(sl, SLCAN_FILLER_WIRE_S)\n")]),
    ("the start bound loosened to 5 s", "START_LAG_MAX_S = 0.005\n", "START_LAG_MAX_S = 5.0\n"),
    ("the bound itself voids", "    if first_lag_s > bound:\n", "    if first_lag_s >= bound:\n"),
    ("a late start voids nothing", "    if first_lag_s > bound:\n", "    if False:\n"),
    ("no frame sent voids nothing",
     "    if first_lag_s is None:\n        return \"the replay sent",
     "    if first_lag_s is None:\n        return None\n        return \"the replay sent"),
    ("every frame's lag overwrites the first", "        if self.first_lag_s is None:\n",
     "        if True:\n"),
    ("the worst lag is never updated after the first",
     "        if self.max_lag_s is None or lag > self.max_lag_s:\n",
     "        if self.max_lag_s is None:\n"),
    ("the lag taken with the sign reversed", "        lag = now - due\n", "        lag = due - now\n"),
    ("refusals past the bound are not counted",
     "        self.refused += 1\n        if len(self.refusals) < self.keep:\n",
     "        if len(self.refusals) < self.keep:\n            self.refused += 1\n"),
    ("refusals kept without bound", "        if len(self.refusals) < self.keep:\n", "        if True:\n"),
    ("the refusal's error type is dropped",
     "\"error\": \"%s: %s\" % (type(exc).__name__, exc)}", "\"error\": \"%s\" % exc}"),
    ("the refusal's index is dropped", "self.refusals.append({\"index\": index,",
     "self.refusals.append({\"index\": None,"),
    ("only 5 refusals kept by default", "REFUSALS_KEPT = 50\n", "REFUSALS_KEPT = 5\n"),
    ("the start lag is not recorded",
     "        return {\"start_lag_s\": None if self.first_lag_s is None else round(self.first_lag_s, 6),\n",
     "        return {\"start_lag_s\": None,\n"),
    ("run_arm does not void on the start lag",
     "    if lag_why:\n        void_reasons.append(lag_why)\n", "    if lag_why:\n        pass\n"),
    ("run_arm does not feed the ledger its lags", "        ledger.sent(due, perf())\n", ""),
    ("run_arm's refusals are counted but not kept", "            ledger.refuse(k, perf() - base, e)\n", ""),
    ("the ledger's fields are left out of the record", "        **ledger.fields(),\n", ""),
]


def apply(src, edits):
    for old, new in edits:
        if src.count(old) != 1:
            return None, src.count(old)
        src = src.replace(old, new)
    return src, 1


def main():
    base = gi_paths.scratch("mutants-e4_replay_start")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    for n, m in enumerate(MUTANTS):
        name, old, new = (m + (None,))[:3]
        if old is None:
            text = src
        else:
            text, matched = apply(src, old if isinstance(old, list) else [(old, new)])
            if text is None:
                print("ERROR    m%d %s: an edit matched %d times" % (n, name, matched))
                errors += 1
                continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(text)
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, E4_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s control: the unmutated copy  (exit %d, %d FAIL)" % (
                "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
