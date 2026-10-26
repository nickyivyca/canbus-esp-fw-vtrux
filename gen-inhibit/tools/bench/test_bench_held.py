#!/usr/bin/env python3
"""Tests for bench_held.py, on bench logs written in scratch/ by shared/bench's own
bench_lock (init, take, release), so they are logs the real writer makes. And that the
two tools that check the lock themselves ask bench_held, not bench-holder.md. No
hardware. Needs the pinned core's shared/bench on PYTHONPATH, as bench_held does.

    py -3.14 -B gen-inhibit/tools/bench/test_bench_held.py

Gen-inhibit tester, 2026-10-25.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402
import bench_held as H  # noqa: E402
from vtrux_bench import bench_lock as L  # noqa: E402

fails = []
oks = 0
BOARDS = {"wican": "not read: test", "x2": "not read: test"}


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def new_log(d, name, *events):
    """A log made by bench_lock: init, then each (kind, who) in order."""
    p = os.path.join(d, name)
    L.init(p, "tester (gen-inhibit)")
    for n, (kind, who) in enumerate(events):
        if kind == "take":   # a run id is used once per log
            L.take(p, who, os.getpid(), run="test-%s-%d" % (name.replace(".", "-"), n))
        else:
            L.release(p, who, BOARDS)
    return p


def main():
    d = gi_paths.scratch("bench-held-test")
    us, other, impl = "tester (gen-inhibit)", "tester (charge-interposer)", "implementor (gen-inhibit)"
    for name, events, want, who in (
            ("free.jsonl", (), False, "free"),
            ("ours.jsonl", (("take", us),), True, us),
            ("other.jsonl", (("take", other),), False, other),
            ("loan.jsonl", (("take", impl),), False, impl),
            ("released.jsonl", (("take", us), ("release", us)), False, "free"),
            ("retaken.jsonl", (("take", other), ("release", other), ("take", us)), True, us),
    ):
        p = new_log(d, name, *events)
        got = H.held_by_us(p)
        check("log %s -> %s, %s" % (name, want, who), got == (want, who), got)

    got = H.held_by_us(os.path.join(d, "none.jsonl"))
    check("no log -> not ours, the reason given", got[0] is False and "could not be read" in got[1], got)

    p = new_log(d, "damaged.jsonl", ("take", us))
    with open(p, "ab") as f:
        f.write(b"not json\n")
    got = H.held_by_us(p)
    check("a log that does not replay, our take in it -> not ours", got[0] is False and "could not be read" in got[1], got)

    p = new_log(d, "host.jsonl", ("take", us))
    lines = open(p, "rb").read().split(b"\n")
    lines[0] = re.sub(rb'"host":"[^"]*"', b'"host":"NOT-THE-BENCH"', lines[0])
    open(p, "wb").write(b"\n".join(lines))
    got = H.held_by_us(p)
    check("a log whose bench is another host, our take in it -> not ours",
          got[0] is False and "NOT-THE-BENCH" in got[1], got)

    # The default is gi_paths.bench_log() under REVERSE_IT_DIR: a fake root, with our take
    # in firmware/bench-log.jsonl and a bench-holder.md naming us, so only the log decides.
    root = os.path.join(d, "root")
    for sub in ("canre", "projects", "firmware"):
        os.makedirs(os.path.join(root, sub))
    with open(os.path.join(root, "firmware", "bench-holder.md"), "w", encoding="utf-8") as f:
        f.write("## Lock\n\n**Holder:** tester (gen-inhibit)\n**Loaned to:** --\n")
    old = os.environ.get("REVERSE_IT_DIR")
    os.environ["REVERSE_IT_DIR"] = root
    try:
        got = H.held_by_us()
        check("default: no bench log, bench-holder.md naming us -> not ours", got[0] is False, got)
        shutil.copy(os.path.join(d, "other.jsonl"), os.path.join(root, "firmware", "bench-log.jsonl"))
        got = H.held_by_us()
        check("default: the bench log's holder decides (the other tester)", got == (False, other), got)
        shutil.copy(os.path.join(d, "ours.jsonl"), os.path.join(root, "firmware", "bench-log.jsonl"))
        got = H.held_by_us()
        check("default: the bench log's holder decides (us)", got == (True, us), got)
    finally:
        if old is None:
            del os.environ["REVERSE_IT_DIR"]
        else:
            os.environ["REVERSE_IT_DIR"] = old

    # Without vtrux_bench the answer is "not ours", never an import error.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "-B", "-c",
                        "import bench_held; ok, who = bench_held.held_by_us(%r); print(ok, who)"
                        % os.path.join(d, "ours.jsonl")],
                       cwd=HERE, env=env, capture_output=True, text=True)
    check("no vtrux_bench -> not ours, with the reason",
          r.returncode == 0 and r.stdout.startswith("False vtrux_bench is not importable"), r.stdout + r.stderr)

    # The tools that check the lock themselves ask bench_held, and nothing reads the
    # frozen Holder line. Read as text: interlock_bench needs can and the host goldens.
    for tool in ("interlock_bench.py", "x2_pins_noreset_check.py"):
        src = open(os.path.join(HERE, tool), encoding="utf-8").read()
        gate = re.search(r"^    ok, who = bench_held\.held_by_us\(\)\n    if not ok:\n.*\n        return 2\n", src, re.M)
        check("%s: refuses unless bench_held says ours" % tool, gate is not None)
        check("%s: does not read bench-holder.md's Holder line" % tool,
              "**Holder:**" not in src and "Holder:" not in src and "bench_holder()" not in src)
        imp = [m.start() for m in re.finditer(r"^\s*import can\b", src, re.M)]
        check("%s: gates before it imports can (its one import of it)" % tool,
              gate is not None and len(imp) == 1 and gate.end() <= imp[0], "%s %s" % (gate and gate.end(), imp))

    shutil.rmtree(d, ignore_errors=True)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
