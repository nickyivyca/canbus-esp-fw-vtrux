#!/usr/bin/env python3
"""Per-arm summary of one B6-investigation session on the _ecc build (b6_ecc_run.sh).

Reads the session folder's x2_jam_threshold_*.json records and prints, per arm: the B6
verdict on the first 10 pulses, the witness's error frames, /gi_ecc's device groups
against the witness's, where trip 6 latched (pulse number), the over-counts, the ecc,
type, dir and seg values seen in the isr rows, and the caveats. Observations only; no
judgement beyond what the records already carry. Gen-inhibit tester, 2026-10-20.

    py -3.14 -B tools/gen_inhibit/bench/b6_ecc_session_summary.py <session folder>
"""
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gi_ecc as GE      # noqa: E402


def main(folder):
    recs = sorted(glob.glob(os.path.join(folder, "x2_jam_threshold_*[0-9].json")))
    tot = collections.Counter()
    for path in recs:
        r = json.load(open(path))
        lead = r["args"].get("lead_s")
        print("== %s  lead %s s, %d arms, verdict %s" % (os.path.basename(path), lead, len(r.get("arms", [])),
                                                         r.get("verdict")))
        for k, a in enumerate(r.get("arms", [])):
            j = a.get("judge") or {}
            e = a.get("ecc") or {}
            page = a.get("ecc_page") or {}
            rows = [GE.decode_isr(x) for x in page.get("isr") or []]
            vals = {f: sorted(collections.Counter(getattr(x, f, None) if not isinstance(x, dict) else x.get(f)
                                                  for x in rows).items()) for f in ("ecc", "type", "dir", "seg")}
            latch = None
            for p in e.get("pulses") or []:
                if p.get("phase") == "post-latch":
                    latch = p.get("pulse")
                    break
            tot[j.get("verdict")] += 1
            tot["overcounts"] += len(e.get("overcounts") or [])
            print("arm %d: %-12s judged groups %s/10 | witness errs %s | device/witness groups %s/%s aligned %s | "
                  "first post-latch pulse %s | over-counts %s | isr_total %s overwritten %s | ticks_overwritten %s"
                  % (k, j.get("verdict"), (j.get("per_pulse") or {}).get("groups"),
                     len(a.get("witness_error_times") or []), e.get("device_groups"), e.get("witness_groups"),
                     e.get("aligned"), latch, e.get("overcounts"), page.get("isr_total"),
                     page.get("isr_overwritten"), page.get("ticks_overwritten")))
            print("        isr values %s" % vals)
            for c in e.get("caveats") or []:
                print("        caveat: %s" % c)
            for p in e.get("problems") or []:
                print("        PROBLEM: %s" % p)
    print("== totals: %s" % dict(tot))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
