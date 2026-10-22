#!/usr/bin/env python3
"""B2's X2 presence count (baseline_run_list.md): for each e4 run given, the frames
on its idle-arm witness log and how many carry the X2's own diag ids 0x7F4-0x7F7
(bench-holder.md rule 6: the other board's ids, by its DBC). Gen-inhibit tester;
the c65c45e count was made inline, this is the same count as a script.

    py -3.14 x2_presence_count.py runs/.../B2_e4_presence_*.json ...
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provenance_classifier as P   # noqa: E402

X2_IDS = (0x7F4, 0x7F5, 0x7F6, 0x7F7)

for run in sys.argv[1:]:
    w = run[:-5] + "_idle_witness.log.gz"
    if not os.path.exists(w):
        print("%s  NO WITNESS LOG (%s)" % (os.path.basename(run)[:-5], os.path.basename(w)))
        continue
    n, by = 0, dict.fromkeys(X2_IDS, 0)
    for _t, i, _b in P.read_witness(w):
        n += 1
        if i in by:
            by[i] += 1
    print("%s frames %d  x2 %s" % (os.path.basename(run)[:-5], n, " ".join("%X=%d" % (i, by[i]) for i in X2_IDS)))
