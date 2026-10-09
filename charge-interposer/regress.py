"""Offline regression: drive machine.py straight from real captures.

No CAN, no simulators, no timing -- just the truck's own bytes fed into the
state machine as fast as the file reads. This is the ground-truth half of the
bench: the live three-way sim shows what the interposer does to a MODEL, this
shows what it does to the vehicle.

The three captures cover the three terminations:

  vtrux_charge_M1                            balanced pack, genuine 100 % stop
  vtruxchargeafterexportchargetest-80pctcv   the evap 80 % ceiling
  vtrux_partstruck_charge                    badly imbalanced, genuine stop
                                             after a 6 h CV balancing tail
                                             (also contains a low-power-mode
                                             transient 31 s into the session)

The strongest assertion here is byte-exactness: on both genuine-stop captures
the interposer must forward every single frame unchanged. Any modification at
all is a failure, because the truck was already doing the right thing.

    python3.13 projects/vtrux/tools/interposer/regress.py --list
    python3.13 projects/vtrux/tools/interposer/regress.py --all

Large captures are filtered first with notes/artifacts/charge_cmd_filter.sh
(seconds, versus minutes to parse the raw multi-GB file). The filtered copy is
deleted afterwards unless --keep-filtered; it is fully regenerable.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths                                                # noqa: E402

VTRUX = Path(paths.data_dir())          # SeaDrive projects/vtrux (paths.py)

import machine as M
import replay

ARTIFACTS = VTRUX / "notes" / "artifacts"
FILTER_SH = ARTIFACTS / "charge_cmd_filter.sh"
LOGS = VTRUX / "logs"


class Case(object):
    def __init__(self, name, why, sources, expect, order_by_header=False):
        self.name = name
        self.why = why
        self.sources = sources          # list of glob-ish paths under logs/
        self.expect = expect
        self.order_by_header = order_by_header


CASES = [
    Case("charge_M1",
         "Balanced pack, genuine top-of-charge stop (vmax 3594 mV, chg_max "
         "2.75 A). Must not be touched. Since 2026-09-30 this is also the "
         "real-capture case for the spec 3 boot fallback: the capture opens "
         "99 minutes into the session (the first "
         "BELINV_chargePilotOnlineTime the core reads is 99), so the core "
         "must stay in PASSTHROUGH for the whole of it and never reach the "
         "accept. Before the fallback existed it armed and logged "
         "'LOW_POWER accepted' at the genuine stop, which is what this "
         "expectation used to assert.",
         ["vtrux_charge_M1_T*.log", "vtrux_M1_T20.log"],
         dict(modified=0, synthesized=0,
              must_event=r"booted into a session already \d+ min old",
              must_not_event=r"LOW_POWER (?:overridden|accepted)",
              final_state=("PASSTHROUGH",)),
         order_by_header=True),

    Case("evap_80pct",
         "The evap 80 % ceiling: the VCU stopped at SoC 80 with bcm_chg_max "
         "still at 300 A. This is the one that must be overridden.",
         ["vtruxchargeafterexportchargetest-80pctcv.log"],
         # Note the limit of replay: once the interposer overrides the stop,
         # the captured charger still shuts down, because in reality it was
         # never told to keep going. Replay proves the DECISION is right; only
         # the closed-loop bench (run_scenario.py evap-override) can show what
         # happens after it.
         dict(min_modified=1, must_event=r"LOW_POWER overridden",
              must_not_event=r"LOW_POWER accepted: ")),

    Case("partstruck",
         "Badly imbalanced pack: a low-power-mode transient 31 s in (must be "
         "ignored as a startup blip) and a genuine stop at vmax 3590 mV "
         "followed by a 6 h balancing tail (must pass through).",
         ["big-logs/vtrux_partstruck_charge.log"],
         dict(modified=0, synthesized=0,
              must_event=r"LOW_POWER accepted",
              must_not_event=r"LOW_POWER overridden")),

    Case("above_ceiling_start",
         "Plugged in at 83 % with the evap flag up (spec 7): CHARGER with a "
         "0 A setpoint, Low Power 3 s after it, the pack draining through the "
         "hold, handle pulled at 58 s. The core must arm without a net "
         "charging current, see the hold with the gate satisfied, wait out "
         "the 75 s arm delay (which the handle pull cuts short), go "
         "transparent on the charger's plug-out report at 57.9 s and to "
         "PASSTHROUGH on the real STAND_BY at 59 s -- nothing modified "
         "(spec 9, user 2026-10-08).",
         ["unidentified-bus/vtrux_partstruck_chargeagain3.log"],
         dict(modified=0, synthesized=0,
              must_event=r"waiting out the arm delay",
              must_not_event=r"LOW_POWER (?:overridden|accepted)",
              # spec 9 (2026-10-08): the two transitions, in order, each
              # within +/- 0.5 s of the spec's time (capture time)
              state_events=[("TERMINATED", r"plug out", 57.4, 58.4),
                            ("PASSTHROUGH", r"session over", 58.5, 59.5)],
              final_state=("PASSTHROUGH",))),
]

BY_NAME = {c.name: c for c in CASES}


def _order_by_header(paths):
    """Sort BUSMASTER chunks by their START DATE header, not by filename.

    vtrux_charge_M1's chunk numbering wraps: T21 is the first chunk and T20 the
    last, so sorting by the T-number puts the end of the charge at the start.
    """
    def key(p):
        with open(p, errors="replace") as f:
            for line in f:
                if line.startswith("***START DATE"):
                    m = re.search(r"TIME (\d+):(\d+):(\d+) (\d+):(\d+):(\d+)", line)
                    if m:
                        d, mo, y, H, Mi, S = map(int, m.groups())
                        return (y, mo, d, H, Mi, S)
                if not line.startswith("***"):
                    break
        return (9999,)
    return sorted(paths, key=key)


def prepare(case, keep, verbose=True):
    """Filter the case's raw captures down to the charge-control IDs."""
    paths = []
    for pat in case.sources:
        paths.extend(sorted(LOGS.glob(pat)))
    missing = [p for p in case.sources if not list(LOGS.glob(p))]
    if missing:
        return None, "missing capture(s): %s" % ", ".join(missing)
    if case.order_by_header:
        paths = _order_by_header(paths)
    out = ARTIFACTS / ("filt_%s.log" % case.name)
    if out.exists() and keep:
        return out, None
    if verbose:
        total = sum(p.stat().st_size for p in paths)
        print("   filtering %d file(s), %.1f MB ..." % (len(paths), total / 1e6))
    t0 = time.time()
    # On Windows a bare "bash" from subprocess resolves through the
    # CreateProcess search order to System32's bash.exe (WSL), which cannot
    # read C:/ paths (exit 127, "/bin/bash: ... No such file or directory")
    # even though shutil.which() reports Git's. Name Git's bash explicitly
    # and pass forward-slash paths, which it reads.
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.exists() else "bash"
    subprocess.run([bash, FILTER_SH.as_posix(), out.as_posix()]
                   + [p.as_posix() for p in paths],
                   check=True, stdout=subprocess.DEVNULL)
    if verbose:
        print("   filtered to %.1f MB in %.0f s"
              % (out.stat().st_size / 1e6, time.time() - t0))
    return out, None


def check_state_events(events, want):
    """-> (ok, why). `events` is the replay's [(t_ms, state, text)]; `want`
    a list of (state, regex, t_lo_s, t_hi_s) that must occur IN ORDER: for
    each, the first event after the previous match whose state is `state`
    and whose text matches must fall inside [t_lo_s, t_hi_s]."""
    i = 0
    got = []
    for st, rx, lo, hi in want:
        while i < len(events) and not (events[i][1] == st
                                       and re.search(rx, events[i][2])):
            i += 1
        if i >= len(events):
            return (False, "no %s event matching /%s/ after %s"
                    % (st, rx, "; ".join(got) or "the start"))
        t = events[i][0] / 1000.0
        if not lo <= t <= hi:
            return (False, "%s /%s/ at %.1f s, outside %.1f-%.1f s"
                    % (st, rx, t, lo, hi))
        got.append("%s at %.1f s" % (st, t))
        i += 1
    return (True, "in order: " + "; ".join(got))


def run_case(case, args):
    path, err = prepare(case, args.keep_filtered)
    if err:
        return [(False, err)], None
    core = M.InterposerCore(M.Config(), now_ms=0)
    t0 = time.time()
    try:
        res = replay.replay_offline(str(path), core,
                                    progress=args.progress or None)
    finally:
        if not args.keep_filtered:
            try:
                os.remove(path)
            except OSError:
                pass

    evtext = "\n".join("%8.1fs [%s] %s" % (t / 1000.0, st, tx)
                       for t, st, tx in res["events"])
    checks = []
    e = case.expect
    if "modified" in e:
        checks.append((res["modified"] == e["modified"],
                       "forwarded stream byte-identical to the capture "
                       "(%d modified)" % res["modified"]))
    if "min_modified" in e:
        checks.append((res["modified"] >= e["min_modified"],
                       "modified at least %d frame(s) (%d)"
                       % (e["min_modified"], res["modified"])))
    if "synthesized" in e:
        checks.append((res["synthesized"] == e["synthesized"],
                       "synthesised no frames (%d)" % res["synthesized"]))
    if "must_event" in e:
        checks.append((re.search(e["must_event"], evtext) is not None,
                       "event matches /%s/" % e["must_event"]))
    if "must_not_event" in e:
        checks.append((re.search(e["must_not_event"], evtext) is None,
                       "no event matches /%s/" % e["must_not_event"]))
    if "state_events" in e:
        ok, why = check_state_events(res["events"], e["state_events"])
        checks.append((ok, "state transitions: %s" % why))
    if "final_state" in e:
        want = e["final_state"]
        got = M.STATE_NAMES[core.state]
        checks.append((got in want, "final state in %s (was %s)" % (want, got)))

    return checks, dict(res=res, events=evtext, secs=time.time() - t0,
                        core=core)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--keep-filtered", action="store_true",
                    help="keep the filtered captures in notes/artifacts/ "
                         "(they are large; regenerating takes seconds)")
    ap.add_argument("--progress", type=int, default=0,
                    help="print a line every N frames")
    args = ap.parse_args()

    if args.list:
        for c in CASES:
            print("%-14s %s" % (c.name, c.why))
        return 0
    names = args.case or ([c.name for c in CASES] if args.all else [])
    if not names:
        ap.error("give a case name, --all, or --list")

    allok = True
    for n in names:
        c = BY_NAME[n]
        print("\n=== %s ===" % n)
        print(c.why)
        checks, info = run_case(c, args)
        if info:
            r = info["res"]
            print("   %d frames in, %d->charger, %d->vehicle, %.0f s"
                  % (r["frames_in"], r["to_charger"], r["to_vehicle"], info["secs"]))
            for line in info["events"].splitlines():
                print("   " + line)
            if r["first_modified"]:
                t, a, b = r["first_modified"]
                print("   first modification at t=%.1fs: %s -> %s" % (t, a, b))
        ok = all(x[0] for x in checks)
        for good, text in checks:
            print("   [%s] %s" % ("PASS" if good else "FAIL", text))
        allok = allok and ok
    print("\n%s" % ("ALL REGRESSIONS PASSED" if allok else "SOME REGRESSIONS FAILED"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
