"""Spec 5.2 item 6: does the per-class balance close on every recorded reading?

    queued + carried_in = sent + dropped + withdrawn + depth + held

`carried_in` and `held` do not exist in any firmware recorded so far, so for old
readings they are unknown and this reports the RESIDUAL

    residual = queued - (sent + dropped + depth)

which is what those two terms have to account for: +1 means one more frame left
than was queued in this arm (a frame carried in across gs_rearm), -1 means one
fewer (a frame in the controller at the read).

IT ALSO CHECKS THE INHIBIT CLASS AGAINST THE SKIPS, which is a different
question and the reason this script exists. Every inhibit frame withdrawn at its
deadline is popped from its queue and counted only as a skip -- there is no term
for it on the right-hand side -- so on any arm with skipped > 0 the inhibit class
cannot balance no matter what carried_in and held do. The `+skipped` column below
is the same residual with the skips added back:

    residual_with_skips = queued - (sent + dropped + depth + skipped - dropped)

The `- dropped` is not a typo. A dropped inhibit is ALSO counted as a skip
(gs_queue_frame, the D3 path), so it would otherwise be counted twice.

Usage:
    py -3.11 gen-inhibit/tools/bench/sched_identity_check.py
    py -3.11 ... sched_identity_check.py runs/e4_case3_final.json
"""

import glob
import io
import json
import os
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

CLASSES = ("inhibit", "probe", "telemetry")


def readings(doc, path):
    """Yield (label, status_dict) for every scheduler status in a run file.

    THE SHAPES ARE NOT UNIFORM and assuming one is how this first reported
    "every class balances" off 8 readings out of several hundred -- the empty
    filter this project's notes warn about. A bare `.json` from a status read is
    itself the page; an e4 run file has `arms` as a DICT keyed by arm name, and
    each arm's end-of-arm status page is under `device`. Anything carrying a
    `sched` object is a reading, whatever it is called, so the walk below looks
    for that rather than for a key name.
    """
    base = os.path.basename(path)
    if "sched" in doc:
        yield base, doc
        return

    arms = doc.get("arms")
    if isinstance(arms, dict):
        items = sorted(arms.items())
    elif isinstance(arms, list):
        items = [(str(i), a) for i, a in enumerate(arms)]
    else:
        items = []
    for name, arm in items:
        if not isinstance(arm, dict):
            continue
        for key in sorted(arm.keys()):
            st = arm[key]
            if isinstance(st, dict) and "sched" in st:
                yield "%s:%s:%s" % (base, name, key), st

    for key in sorted(doc.keys()):
        st = doc[key]
        if isinstance(st, dict) and "sched" in st:
            yield "%s:%s" % (base, key), st


def check(label, st, rows):
    sched = st.get("sched") or {}
    skipped = int(sched.get("skipped", 0))
    # The per-class objects are under "cls", not at the top of "sched".
    cls = sched.get("cls")
    if not isinstance(cls, dict):
        cls = sched
    for cname in CLASSES:
        c = cls.get(cname)
        if not isinstance(c, dict):
            continue
        q = int(c.get("queued", 0))
        sent = int(c.get("sent", 0))
        dropped = int(c.get("dropped", 0))
        depth = int(c.get("depth", 0))
        #
        # USE THE REAL TERMS WHERE THE FIRMWARE HAS THEM. carried_in and held
        # arrived with schema 5 (spec 5.2 item 6, 2026-09-29); readings taken
        # before that have neither, and for those the residual below is exactly
        # what the two terms would have accounted for. A reading that HAS them and
        # still does not balance is a real finding, not a missing-field artefact,
        # so the two cases must not be collapsed -- the `terms` column says which
        # kind of row you are looking at.
        #
        has_new = ("carried_in" in c) and ("held" in c)
        carried = int(c.get("carried_in", 0))
        held = int(c.get("held", 0))
        # `withdrawn` arrived after carried_in/held, in the same ruling; a reading
        # from between the two builds has the first pair and not this one, and for
        # the inhibit class that is exactly the residual the +skips column shows.
        withdrawn = int(c.get("withdrawn", 0))
        resid = (q + carried) - (sent + dropped + withdrawn + depth + held)
        resid_skip = resid
        if cname == "inhibit":
            resid_skip = q - (sent + dropped + depth + skipped - dropped)
        if resid == 0 and resid_skip == 0:
            continue
        rows.append((label, cname, "5" if has_new else "pre-5",
                     q, sent, dropped, depth, skipped, resid, resid_skip))


def main(argv):
    paths = argv[1:] or sorted(glob.glob(os.path.join(RUNS, "*.json")))
    rows = []
    n_read = 0
    for p in paths:
        if not os.path.isabs(p):
            p = gi_paths.data_dir(p)
        try:
            doc = json.load(io.open(p, encoding="utf-8"))
        except Exception as e:
            print("  skip %s (%s)" % (os.path.basename(p), e))
            continue
        if not isinstance(doc, dict):
            continue
        for label, st in readings(doc, p):
            n_read += 1
            check(label, st, rows)

    print("readings examined: %d" % n_read)
    print("")
    if not rows:
        print("every class balances on every reading")
        return 0
    print("%-40s %-9s %-5s %6s %6s %5s %5s %5s %7s %8s"
          % ("reading", "class", "terms", "queued", "sent", "drop", "depth",
             "skip", "resid", "+skips"))
    for r in rows:
        print("%-40s %-9s %-5s %6d %6d %5d %5d %5d %+7d %+8d" % r)
    print("")
    print("resid  = (queued + carried_in) - (sent + dropped + depth + held);")
    print("         carried_in and held are 0 for pre-5 readings that lack them")
    print("+skips = the same with withdrawn inhibits added back")
    print("%d unbalanced class readings" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
