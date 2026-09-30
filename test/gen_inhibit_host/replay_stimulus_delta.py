"""What changes in the eight replay scenarios when they are regenerated, and why.

WHY THIS EXISTS. `scenarios/` is not tracked in git, so a clean checkout has to
rebuild it -- `make_scenarios.py` for the synthetic cases and the README's
`from_capture.py` commands for the eight replays. canre's BUSMASTER parser was
fixed on 2026-09-27 (`_DATA_RE` could not match a negative timestamp component, so
lines the Android logger wrote with a pre-origin offset were dropped silently: 4.8 M
lines, 0.203 %, across 1,715 corpus files, worst single file 44 %). Rebuilding the
replays with the corrected parser therefore produces a different stimulus from the
same log, and six goldens no longer match.

A GOLDEN IS NOT EVIDENCE OF ANYTHING UNTIL THE DELTA IS UNDERSTOOD. Re-blessing to
make a suite green is the failure this directory's README opens with, so this script
reports the delta instead of hiding it: the stimulus difference, whether the frame
ORDER is affected, and the behaviour difference in the emitted trace. Run it before
re-blessing, and read the per-scenario output.

    python3 replay_stimulus_delta.py            # uses ./scenarios and ./golden
    python3 replay_stimulus_delta.py --regen    # rebuild the replays first

`--regen` moves the existing replay scenarios aside to scenarios/_pre_regen/ so the
old stimulus is still readable afterwards; without it the script compares whatever
is on disk against the goldens.
"""

import argparse
import collections
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCN = os.path.join(HERE, "scenarios")
OLD = os.path.join(SCN, "_pre_regen")
GOLD = os.path.join(HERE, "golden")
RUNNER = os.path.join(HERE, "host_runner")

# The README's table, one entry per replay: (name, log, channel, at, dur).
REPLAYS = [
    ("replay-genrun-stop", "vtrux_20260719_190019_T4.log", 2, 0, 220),
    ("replay-healthy-engine-off", "vtrux_20260323_220148_T0.log", 1, 0, 300),
    ("replay-mmode-genstart", "vtrux_20260322_165622_T1.log", 1, 0, 64),
    ("replay-shutdown-at-keyon", "vtrux_20260714_112312_T2.log", 2, 0, 225),
    ("replay-bus-sleeps", "vtrux_20260802_123318_T0.log", 2, 0, 161),
    ("replay-rekey-short", "vtrux_20260403_194203_T2.log", 2, 0, 176),
    ("replay-rekey-long", "vtrux_20260513_174225_T4.log", 57, 0, 224),
    ("replay-inverter-lost-keyon", "vtrux_20260714_112312_T2.log", 2, 20.5, 7.7),
]

FRAME_RE = re.compile(r"^\s*(\d+)\s+(?:RX|rx)?\s*0?x?([0-9A-Fa-f]{3,8})\b")


def logs_dir():
    """The project repo's Vtrux logs, per the README. Never guessed silently."""
    for cand in (os.path.expanduser("~/Seafile/NotGit/reverse-it/projects/vtrux/logs"),
                 os.path.expanduser("~/seadrive_root/nickivyc_1/My Libraries/NotGit/"
                                    "reverse-it/projects/vtrux/logs")):
        if os.path.isdir(cand):
            return cand
    return None


def read_scn(path):
    """Return (header_lines, [(us, id)]) from a scenario file."""
    head, rows = [], []
    if not os.path.exists(path):
        return head, rows
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if ln.startswith("#"):
                head.append(ln.rstrip())
                continue
            # The format is `f <us> <id> <dlc> <hex>`; `mode` and blank lines are
            # not frames. The first version indexed parts[0] as the offset, which
            # is the literal "f", so every file read as empty -- and the script
            # then reported "no regenerated scenario on disk" for all eight, which
            # is a false negative that looks like a missing file.
            parts = ln.split()
            if len(parts) < 3 or parts[0] != "f":
                continue
            try:
                us = int(parts[1])
            except ValueError:
                continue
            rows.append((us, parts[2].lower()))
    return head, rows


def regen(name, log, chan, at, dur, ldir):
    out = os.path.join(SCN, name + ".scn")
    cmd = [sys.executable, os.path.join(HERE, "from_capture.py"),
           os.path.join(ldir, log), "--channel", str(chan),
           "--at", str(at), "--for", str(dur), "--out", out]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    return r.returncode, (r.stdout + r.stderr)[-400:]


def trace_of(name):
    p = os.path.join(SCN, name + ".scn")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        r = subprocess.run([RUNNER], stdin=fh, capture_output=True, text=True,
                           cwd=HERE)
    if r.returncode != 0:
        return None
    # The suite strips TX lines from replay goldens; match that.
    return [ln for ln in r.stdout.splitlines() if " TX " not in ln]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--regen", action="store_true")
    args = ap.parse_args(argv[1:])

    if args.regen:
        ldir = logs_dir()
        if ldir is None:
            print("NO REFERENCE: the project repo's projects/vtrux/logs was not "
                  "found, so the replays cannot be rebuilt. Refusing to guess a "
                  "path.")
            return 2
        os.makedirs(OLD, exist_ok=True)
        for name, _l, _c, _a, _d in REPLAYS:
            src = os.path.join(SCN, name + ".scn")
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(OLD, name + ".scn"))
        print("existing replay scenarios copied to %s" % OLD)
        for name, log, chan, at, dur in REPLAYS:
            rc, tail = regen(name, log, chan, at, dur, ldir)
            print("  regen %-30s %s" % (name, "ok" if rc == 0 else "FAILED " + tail))
        print("")

    for name, _log, _chan, _at, _dur in REPLAYS:
        newp = os.path.join(SCN, name + ".scn")
        oldp = os.path.join(OLD, name + ".scn")
        _nh, new_rows = read_scn(newp)
        _oh, old_rows = read_scn(oldp)

        print("=" * 78)
        print(name)
        if not new_rows:
            print("  no regenerated scenario on disk")
            continue

        # --- the stimulus itself -------------------------------------------
        print("  frames      %d%s" % (len(new_rows),
              ("  (was %d, delta %+d)" % (len(old_rows), len(new_rows) - len(old_rows)))
              if old_rows else ""))
        print("  first us    %d%s" % (new_rows[0][0],
              ("  (was %d, shift %+d us)" % (old_rows[0][0],
                                             new_rows[0][0] - old_rows[0][0]))
              if old_rows else ""))
        print("  last us     %d%s" % (new_rows[-1][0],
              ("  (was %d, shift %+d us)" % (old_rows[-1][0],
                                             new_rows[-1][0] - old_rows[-1][0]))
              if old_rows else ""))

        # ORDER, which matters more than the shift: from_capture.py does not sort,
        # and a replay fed non-monotonic offsets is replaying frames out of time
        # order. Pre-existing if it was already true; a REGRESSION if new.
        def nonmono(rows):
            return sum(1 for a, b in zip(rows, rows[1:]) if b[0] < a[0])
        nm_new, nm_old = nonmono(new_rows), nonmono(old_rows) if old_rows else None
        print("  out-of-order offsets  %d%s" % (nm_new,
              ("  (was %d)" % nm_old) if nm_old is not None else ""))

        if old_rows:
            cn = collections.Counter(i for _u, i in new_rows)
            co = collections.Counter(i for _u, i in old_rows)
            diffs = {k: cn.get(k, 0) - co.get(k, 0)
                     for k in set(cn) | set(co) if cn.get(k, 0) != co.get(k, 0)}
            print("  per-id count changes  %s" % (diffs or "none"))

        # --- the behaviour -------------------------------------------------
        gpath = os.path.join(GOLD, name + ".trace")
        tr = trace_of(name)
        if tr is None:
            print("  runner FAILED on the regenerated scenario")
            continue
        if not os.path.exists(gpath):
            print("  no golden to compare against")
            continue
        with open(gpath, encoding="utf-8") as fh:
            gold = fh.read().splitlines()
        if tr == gold:
            print("  BEHAVIOUR: identical to the golden")
            continue

        # Decision lines only -- EV/LOG/FINAL. That is what a reviewer reads.
        #
        # STATE LINES COUNT, and leaving them out made this tool under-report. A
        # replay trace is MOSTLY STATE -- 11 STATE against 6 EV in genrun-stop,
        # 105 against 38 in rekey-short -- so a verdict of "timestamps only"
        # computed over EV alone covered a third of the record. Caught by the
        # reviewing session, 2026-09-30: genrun-stop and shutdown-at-keyon each
        # have 2 STATE lines whose CONTENT changes, inside the first 6 ms, where
        # the gate reason reported first swaps with the arrival order. Benign --
        # and a tool whose output justifies a re-bless must not be what decides
        # that by not looking.
        #
        def keyline(ln):
            return (" EV " in ln or " LOG " in ln
                    or " STATE " in ln
                    or ln.startswith("FINAL") or " FINAL " in ln)
        gk = [l for l in gold if keyline(l)]
        tk = [l for l in tr if keyline(l)]
        print("  BEHAVIOUR DIFFERS. decision lines %d -> %d" % (len(gk), len(tk)))
        # First divergence, and the shape of it.
        for i, (a, b) in enumerate(zip(gk, tk)):
            if a != b:
                print("    first differing decision line (#%d):" % i)
                print("      golden: %s" % a)
                print("      now   : %s" % b)
                break
        else:
            if len(gk) != len(tk):
                extra = tk[len(gk):] or gk[len(tk):]
                print("    identical up to the shorter, then %d extra line(s):"
                      % len(extra))
                for l in extra[:4]:
                    print("      %s" % l)
        #
        # THREE DIFFERENT KINDS OF CHANGE, and lumping them together is useless to
        # a reviewer. A FINAL line carries counters, which MUST move when the
        # stimulus gains a frame; that is not a behaviour change. What matters is
        # whether the EV/LOG sequence -- the decisions -- is the same sequence with
        # different timestamps, or a different sequence.
        #
        # Every decision line; see keyline() for why STATE belongs here.
        def ev_only(ls):
            return [l for l in ls
                    if " EV " in l or " LOG " in l or " STATE " in l]

        def by_class(ls, k):
            return [l for l in ls if (" %s " % k) in l]

        def detime(ls):
            return [re.sub(r"^\s*-?\d+\s+", "", l) for l in ls]

        ge, te = ev_only(gk), ev_only(tk)
        if detime(ge) == detime(te):
            print("    decisions: SAME SEQUENCE, timestamps only (%d events)"
                  % len(te))
        else:
            print("    decisions: SEQUENCE CHANGED (%d -> %d events)"
                  % (len(ge), len(te)))
            dg, dt = detime(ge), detime(te)
            for i, (a, b) in enumerate(zip(dg, dt)):
                if a != b:
                    print("      first change at event #%d:" % i)
                    print("        golden: %s" % a)
                    print("        now   : %s" % b)
                    break
            else:
                extra = dt[len(dg):] or dg[len(dt):]
                print("      same up to the shorter, then: %s" % (extra[:3]))
            # Is the SET of events the same, only reordered?
            if sorted(dg) == sorted(dt):
                print("      ...but the same events, REORDERED -- no event gained "
                      "or lost")
            #
            # PER CLASS, AND WHERE. "2 of 22 STATE lines, both inside 6 ms" is a
            # different finding from "the run diverges at 40 s", and one verdict
            # over the pooled lines cannot tell them apart.
            #
            for k in ("STATE", "EV", "LOG"):
                ka, kb = by_class(ge, k), by_class(te, k)
                if not ka and not kb:
                    continue
                da, db = detime(ka), detime(kb)
                if ka == kb:
                    verdict = "identical"
                elif da == db:
                    verdict = "timestamps only"
                elif sorted(da) == sorted(db):
                    verdict = "same lines reordered, none gained or lost"
                else:
                    n = sum(1 for x, y in zip(da, db) if x != y)
                    ts = []
                    for x, y in zip(ka, kb):
                        if detime(x) == detime(y):
                            continue
                        mm = re.match(r"^\s*(-?\d+)", y)
                        if mm:
                            ts.append(int(mm.group(1)))
                    span = ("  spanning %d..%d us" % (min(ts), max(ts))) if ts else ""
                    verdict = ("CONTENT CHANGED in %d of %d lines%s"
                               % (n, len(kb), span))
                print("      %-5s %4d -> %4d  %s" % (k, len(ka), len(kb), verdict))

        gf = [l for l in gold if l.startswith("FINAL")]
        tf = [l for l in tr if l.startswith("FINAL")]
        if gf and tf and gf[0] != tf[0]:
            gw = dict(w.split("=", 1) for w in gf[0].split() if "=" in w)
            tw = dict(w.split("=", 1) for w in tf[0].split() if "=" in w)
            moved = {k: "%s->%s" % (gw[k], tw[k])
                     for k in gw if k in tw and gw[k] != tw[k]}
            print("    FINAL fields that moved: %s" % (moved or "none"))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
