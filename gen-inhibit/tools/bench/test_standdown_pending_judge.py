#!/usr/bin/env python3
"""Tests standdown_pending_judge on synthetic runs built with test_interlock_judge's
Sim and synthetic full-page rows; no device. --mutants runs its mutants; each must
make a check fail.

    py -3.14 tools/gen_inhibit/bench/test_standdown_pending_judge.py [--mutants]
Run from the reverse-it root.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import standdown_pending_judge as K   # noqa: E402
import test_interlock_judge as T      # noqa: E402

FAILS = []
LATCH = 3.0002          # 200 us after the VCM 0x051 at 3.0, before the answer at 3.0005


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def wire(latch=LATCH, pages_stop=False, no_7f9=False, answer_before=False):
    """Armed at 0; VCM 0x051 every 20 ms from 1 s; position 2 then 4 from the latch;
    the device answers 500 us after each VCM frame before 3.0 s (and the one at 3.0
    too with answer_before); diag pages 0x7F1/0x7F8/0x7F2 each second plus 0x7F3 and
    0x7F9, stopping at the latch with pages_stop."""
    s = T.Sim()
    s.arm(0.0)
    s.vcm(1.0, 9.0)
    s.mainc(1.0, 9.0, 11)
    s.soc(1.0, 9.0, 5000)
    s.key(1.0, 9.0, True)
    s.shift(1.0, latch, 2)
    s.shift(latch, 9.0, 4)
    s.device(lambda t: t >= 1.05 and (t < 2.999 or (answer_before and t < 3.001)))
    end = latch if pages_stop else 9.0
    s.pages(0.3, end, lambda t: {"live": t < 3.0, "disabled": t >= 3.0,
                                 "dis": "m_mode" if t >= 3.0 else "none",
                                 "block": "none" if t < 3.0 else "disabled"})
    s.train(0x7F3, 0.33, end, 1.0, lambda t: T.enc(T.J.DIAG, 0x7F3))   # HEAD's schema (gi_diag)
    if not no_7f9:
        s.train(0x7F9, 0.34, end, 1.0, lambda t: T.enc(T.J.DIAG, 0x7F9))
    return s


def page(dis, sd, sk=0, w=0, d=0, skw=0, skl=0, top_w=0, top_l=0, no_sd=False):
    p = {"mode": 3, "disabled": dis, "disable_reason": "m_mode" if dis else "",
         "skips_withdrawn": top_w, "skips_late": top_l,
         "sched": {"skipped": sk, "skipped_withdrawn": skw, "skipped_late": skl,
                   "standdown": sd, "aborts": w,
                   "cls": {"inhibit": {"withdrawn": w, "dropped": d}}}}
    if no_sd:
        del p["sched"]["standdown"]
    return p


def rows(after, mid=None, no_after=False, latch=LATCH):
    """A read every 0.5 s from 0.5 to 8.5 s, each taking 20 ms: page(False, 0) before
    the latch, `mid` inside the settle window, `after` from SETTLE_S on."""
    out = []
    t = 0.5
    while t < 8.6:
        if t + 0.02 < latch:
            p = page(False, 0)
        elif t < latch + K.SETTLE_S:
            p = mid if mid is not None else after
        else:
            p = None if no_after else after
        if p is not None:
            out.append({"t_req": t, "t_resp": t + 0.02, "page": p})
        t += 0.5
    return out


def run(s, r):
    return K.judge(s.f, s.posts, r)


def failed(r, part):
    return [c["name"] for c in r["checks"] if not c["ok"] and part in c["name"]]


def main():
    good = page(True, 1, w=1)
    r = run(wire(), rows(good))
    check("the right device (standdown +1, skips unchanged, cadence, identity): AS SPECIFIED",
          r["verdict"] == "AS SPECIFIED", r["checks"] + r["preconditions"])
    r = run(wire(), rows(good, mid=page(True, 1, w=0)))
    check("one short mid-withdrawal (standdown booked, withdrawn not yet) is allowed",
          r["verdict"] == "AS SPECIFIED", r["checks"])
    r = run(wire(pages_stop=True), rows(page(True, 0, w=1, no_sd=True)))
    check("c65c45e's shape (no standdown field, pages stop): NOT AS SPECIFIED on +1, cadence and identity",
          r["verdict"] == "NOT AS SPECIFIED" and failed(r, "standdown +1") and failed(r, "cadence")
          and failed(r, "withdrawn + dropped"), r["checks"])
    r = run(wire(), rows(page(True, 0, w=1, no_sd=True, sk=1)))
    check("no sched.standdown on the page fails the +1 even with the identity's other terms equal",
          failed(r, "standdown +1"), r["checks"])
    r = run(wire(), rows(page(True, 0, sk=1, skw=1, top_w=1, w=1)))
    check("the stand-down booked as a skip fails +1 and the skips",
          failed(r, "standdown +1") and failed(r, "skips unchanged"), r["checks"])
    r = run(wire(), rows(page(True, 2, w=2)))
    check("standdown +2 fails the +1", failed(r, "standdown +1") and not failed(r, "withdrawn + dropped"),
          r["checks"])
    r = run(wire(), rows(page(True, 1, w=1, skl=1, sk=1, d=1)))
    check("sched.skipped_late changing fails the skips", failed(r, "skips unchanged"), r["checks"])
    r = run(wire(), rows(page(True, 1, w=1, top_l=1)))
    check("top-level skips_late changing fails the skips", failed(r, "skips unchanged"), r["checks"])
    r = run(wire(), rows(page(True, 1, w=1, d=1)))
    check("a dropped inhibit with no matching skip breaks the identity", failed(r, "withdrawn + dropped")
          and not failed(r, "standdown +1"), r["checks"])
    r = run(wire(pages_stop=True), rows(good))
    check("pages silent after the latch fail the cadence alone",
          r["verdict"] == "NOT AS SPECIFIED" and failed(r, "cadence") and len(failed(r, "")) == 1, r["checks"])
    r = run(wire(no_7f9=True), rows(good))
    check("a diag id never seen fails the cadence", failed(r, "cadence"), r["checks"])
    r = run(wire(latch=3.0008, answer_before=True), rows(good, latch=3.0008))
    check("an answer already on the wire before the latch: INCONCLUSIVE",
          r["verdict"] == "INCONCLUSIVE", r["preconditions"])
    r = run(wire(), rows(good, no_after=True))
    check("no page after the settle: INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE", r["preconditions"])
    r = run(wire(), rows(page(False, 1, w=1)))
    check("the page after not disabled: INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE", r["preconditions"])
    check("residual: balanced 0, one short -1, a missing term None",
          K.residual(page(True, 1, w=1)) == 0 and K.residual(page(True, 1, w=0)) == -1
          and K.residual(page(True, 0, no_sd=True)) is None)
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("settle window not excluded", 'and (r["t_resp"] < tl or r["t_req"] >= tl + settle)]', "]"),
    ("standdown +N accepted", "sa - sb == 1,", "sa - sb >= 1,"),
    ("missing standdown passes", "sb is not None and sa is not None and sa - sb == 1,",
     "(sb is None or sa is None) or sa - sb == 1,"),
    ("only sched.skipped compared", 'SKIP_FIELDS = ("sched.skipped", "sched.skipped_withdrawn", "sched.skipped_late",\n'
     '               "skips_withdrawn", "skips_late")', 'SKIP_FIELDS = ("sched.skipped",)'),
    ("identity without dropped", "return (w + d) - (s + sd)", "return w - (s + sd)"),
    ("pending not required", "tv is not None and not between,", "True,"),
    ("cadence threshold ignored", "spans and not over and not missing,", "spans and not missing,"),
    ("missing diag id accepted", "spans and not over and not missing,", "spans and not over,"),
]


def mutants():
    src = open(os.path.join(HERE, "standdown_pending_judge.py"), encoding="utf-8").read()
    base = gi_paths.scratch("mutants-standdown_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, "standdown_pending_judge.py"), "w", encoding="utf-8").write(src.replace(old, new))
        shutil.copy(os.path.abspath(__file__), d)
        r = subprocess.run([sys.executable, os.path.join(d, os.path.basename(__file__))],
                           capture_output=True, text=True, cwd=os.getcwd(),
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + os.getcwd()))
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               ("; " + fails[0][:80]) if fails else (", CRASHED" if "Traceback" in r.stderr else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
