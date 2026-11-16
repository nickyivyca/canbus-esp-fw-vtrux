#!/usr/bin/env python3
"""The kernel's USB and CAN lines over every judged check's window of a run
record, with each PASS or FAIL beside a -71, an EPROTO, a USB disconnect, a
port disabled by its hub or an error -32 FLAGGED.

Charge-interposer tester, 2026-11-13, from the charge-interposer reviewer's
request the same day (after CAN replay's bench advisory: both bench USB
hubs are bus-powered, and the GL850G holding the Kvaser, the PCAN and
canmain, usb 1-3.2, has shown -71/EPROTO): "the bench run records
journalctl -k USB and CAN errors on 1-3.2.x over every judged check's
window, not only VOIDs. A PASS or FAIL alongside a -71 or a disconnect is
flagged in the record and in your entry." The reviewer accepted an
entry-time scan, committed and re-runnable; this is it. madhouse's journal
is persistent, so it can be run again after the run.

Only checks with hardware in their loop are scanned: the results of a run
whose start event names a suite in HARDWARE_SUITES (the bench suite: the vp
runs and the bench run), and a chain record's row for a whole suite run
whose run (<record>-<group>) is one of those, since its window holds that
run's board start hooks (run e79d's WiCAN failure was in vp1/run's). The
emulated and harness suites' results, a chain's rows for them or for the
builds, and a run whose start event names no suite, are left out and
counted on a "not scanned" line (the reviewer, 2026-11-16, at the user's correction: the
dongles are not in those suites' loop, so a line beside them means
nothing).

A check's window is its result's end time ("t") less its duration_s, with
SLACK_S either side; a result with no duration has no window and is listed
as such. Judged means a verdict other than NOT-RUN. The lines are
kvaser_link.KLOG_RE's (since the hub swap of 2026-11-16, at the reviewer's
ruling: every device under the bench hub 1-3 by any driver, the bench's
devices by ID, gs_usb, kvaser_usb, peak_usb, pcan, -71, EPROTO,
disconnects, "disabled by hub", "error -32"); a window is FLAGGED when one
of its lines matches FLAG_RE (-71, EPROTO, a disconnect, and from
2026-11-16 "disabled by hub" and "error -32", run e79d's WiCAN failure
lines). It judges nothing: the flags go into the record (--out) and the
VERIFICATION.md entry.

    python3.13 -B charge-interposer/bench_drivers/klog_windows.py RUN_DIR [RUN_DIR ...] [--out FILE]
        every progress.jsonl under each RUN_DIR (nested runs included)
"""

import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ first, then charge-interposer/ (kvaser_link, scratch_dir),
# both found from this file
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import kvaser_link                                      # noqa: E402

SLACK_S = 1.0
FLAG_RE = re.compile(r"-71\b|EPROTO|disconnect|disabled by hub|error -32\b", re.I)
FLAG_TEXT = "-71/EPROTO/disconnect/disabled by hub/error -32"
# the suites whose checks have the boards and the USB adapters in their loop:
# the bench suite (the vp runs and the bench run). The emulated and harness
# suites have no dongle in theirs, so their results are not scanned (the
# reviewer, 2026-11-16, at the user's correction).
HARDWARE_SUITES = ("charge-interposer-bench",)
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?[+-]\d\d:\d\d)\s")


def epoch(iso):
    return datetime.datetime.fromisoformat(iso).timestamp()


def results(run_dirs):
    """-> ([(record, id, verdict, t0, t1 or None)] of every judged result in
    every progress.jsonl under run_dirs whose run is a HARDWARE_SUITES run,
    {(record, suite): n} of the judged results left out, suite None when
    the run's start event names none)."""
    runs = []                                   # (record, suite, [judged result rows])
    for d in run_dirs:
        for root, _dirs, files in os.walk(d):
            if "progress.jsonl" not in files:
                continue
            rec = os.path.basename(root)        # the run's own folder name
            suite, rows = None, []
            with open(os.path.join(root, "progress.jsonl"), encoding="utf-8") as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if r.get("type") == "event" and r.get("kind") == "start":
                        suite = r.get("suite")
                    if r.get("type") != "result" or r.get("verdict") == "NOT-RUN":
                        continue
                    rows.append(r)
            runs.append((rec, suite, rows))
    hw = [rec for rec, suite, _rows in runs if suite in HARDWARE_SUITES]
    out, skipped = [], {}
    for rec, suite, rows in runs:
        for r in rows:
            # a chain record's row for a whole suite run ("vp1/run", group
            # vp1) is hardware when that run, <record>-<group>, is: its
            # window holds the run's board start hooks
            if suite not in HARDWARE_SUITES and not any(
                    h.endswith("-%s" % r.get("group")) for h in hw if r.get("group")):
                skipped[(rec, suite)] = skipped.get((rec, suite), 0) + 1
                continue
            t1 = epoch(r["t"])
            dur = r.get("duration_s")
            t0 = t1 - float(dur) if isinstance(dur, (int, float)) else None
            out.append((rec, r["id"], r["verdict"], t0, t1))
    return out, skipped


def kernel_rows(t0, t1, journal=None):
    """-> [(epoch, line)] of the kernel's USB/CAN lines in [t0, t1]; a line
    whose time cannot be read is kept at None. An unreadable log raises."""
    rows = []
    for ln in kvaser_link.kernel_lines(t0, t1, journal):
        if ln.startswith("(the kernel log could not be read"):
            raise RuntimeError(ln)
        m = TS_RE.match(ln)
        rows.append((epoch(m.group(1)) if m else None, ln))
    return rows


def scan(run_dirs, journal=None):
    """-> (lines, n flagged). One kernel read over the whole span of the
    hardware runs' results."""
    res, skipped = results(run_dirs)
    lines, flagged = [], 0
    for (rec, suite), n in sorted(skipped.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        lines.append("not scanned, no hardware in the loop: %s, %d judged result(s) (%s)"
                     % (rec, n, ("suite %s" % suite) if suite else "its start event names no suite"))
    timed = [r for r in res if r[3] is not None]
    if not timed:
        return lines + ["no judged hardware result with a window in %s" % ", ".join(run_dirs)], 0
    lo = min(r[3] for r in timed) - SLACK_S
    hi = max(r[4] for r in timed) + SLACK_S
    rows = kernel_rows(lo, hi, journal)
    lines.append("kernel USB/CAN lines over %d judged result(s), %s .. %s: %d line(s) in "
                 "the span" % (len(res), kvaser_link.clock_text(lo),
                               kvaser_link.clock_text(hi), len(rows)))
    for rec, cid, verdict, t0, t1 in res:
        if t0 is None:
            lines.append("  %s %s %s: no duration, no window" % (rec, cid, verdict))
            continue
        inw = [ln for t, ln in rows if t is not None and t0 - SLACK_S <= t <= t1 + SLACK_S]
        bad = [ln for ln in inw if FLAG_RE.search(ln)]
        if bad:
            flagged += 1
            lines.append("  FLAGGED %s %s %s (%s..%s): %d kernel line(s), %d of them "
                         "%s: %s"
                         % (rec, cid, verdict, kvaser_link.clock_text(t0),
                            kvaser_link.clock_text(t1), len(inw), len(bad), FLAG_TEXT,
                            " | ".join(bad[:6]) + (" | ..." if len(bad) > 6 else "")))
        elif inw:
            lines.append("  %s %s %s: %d kernel line(s), none %s"
                         % (rec, cid, verdict, len(inw), FLAG_TEXT))
    lines.append("%d judged result(s) flagged" % flagged)
    return lines, flagged


# --- self-test ---------------------------------------------------------------------

def self_test():
    import scratch_dir
    fails = []

    def check(ok, what):
        print("%s  %s" % ("ok  " if ok else "FAIL", what))
        if not ok:
            fails.append(what)
    with scratch_dir.run("klog-windows") as d:
        _self_test(d, check)
    print("\nklog_windows self-test: %s" % ("all checks OK" if not fails
                                           else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def _self_test(d, check):
    if True:
        nested = os.path.join(d, "runs", "x-bench")
        os.makedirs(nested)
        rows = [
            {"type": "event", "kind": "start", "t": "2026-11-13T10:00:00-08:00",
             "suite": "charge-interposer-bench"},
            {"type": "result", "t": "2026-11-13T10:00:10-08:00", "id": "b/clean", "verdict": "PASS",
             "duration_s": 5.0},
            {"type": "result", "t": "2026-11-13T10:00:30-08:00", "id": "b/hit", "verdict": "PASS",
             "duration_s": 10.0},
            {"type": "result", "t": "2026-11-13T10:00:50-08:00", "id": "b/fail", "verdict": "FAIL",
             "duration_s": 5.0},
            {"type": "result", "t": "2026-11-13T10:01:00-08:00", "id": "b/skip", "verdict": "NOT-RUN"},
            {"type": "result", "t": "2026-11-13T10:01:10-08:00", "id": "b/nodur", "verdict": "PASS"},
            {"type": "event", "kind": "check-end", "t": "2026-11-13T10:00:10-08:00"},
            # after the hub swap (the reviewer, 2026-11-16): run e79d's WiCAN
            # failure lines are flags; a WiCAN line on 1-3.1 is read
            {"type": "result", "t": "2026-11-13T10:01:30-08:00", "id": "b/emi", "verdict": "PASS",
             "duration_s": 5.0},
            {"type": "result", "t": "2026-11-13T10:01:50-08:00", "id": "b/epipe", "verdict": "FAIL",
             "duration_s": 5.0},
            {"type": "result", "t": "2026-11-13T10:02:10-08:00", "id": "b/wican", "verdict": "PASS",
             "duration_s": 5.0},
        ]
        with open(os.path.join(nested, "progress.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        # no hardware in the loop (the reviewer, 2026-11-16): an emulated
        # run's PASS and a suite-less run's FAIL, each beside a -71 line
        for name, start, cid, verdict, t in (
                ("x-emulated", {"type": "event", "kind": "start", "t": "2026-11-13T10:02:30-08:00",
                                "suite": "charge-interposer-emulated"},
                 "m/sim", "PASS", "2026-11-13T10:02:40-08:00"),
                ("x-nosuite", None, "h/x", "FAIL", "2026-11-13T10:03:00-08:00"),
                # a chain record: its row for the bench run (group "bench",
                # the run x-bench) is hardware, its row for the emulated run
                # (group "emulated", x-emulated) is not
                ("x-chain", {"type": "event", "kind": "start", "t": "2026-11-13T10:00:00-08:00",
                             "suite": "accept-x"},
                 "emulated/run", "FAIL", "2026-11-13T10:02:40-08:00")):
            p = os.path.join(d, "runs", name)
            os.makedirs(p)
            with open(os.path.join(p, "progress.jsonl"), "w", encoding="utf-8") as f:
                if start:
                    f.write(json.dumps(start) + "\n")
                f.write(json.dumps({"type": "result", "t": t, "id": cid, "verdict": verdict,
                                    "group": cid.split("/")[0], "duration_s": 5.0}) + "\n")
                if name == "x-chain":
                    f.write(json.dumps({"type": "result", "t": "2026-11-13T10:03:00-08:00",
                                        "id": "bench/run", "group": "bench", "verdict": "FAIL",
                                        "duration_s": 5.0}) + "\n")
        log = ("2026-11-13T10:00:07.000001-08:00 madhouse-debian kernel: gs_usb 1-3.3:1.0 canchg: "
               "renamed from can0\n"
               "2026-11-13T10:00:25.500000-08:00 madhouse-debian kernel: usb 1-3.2.1: device "
               "descriptor read/64, error -71\n"
               "2026-11-13T10:00:47.000000-08:00 madhouse-debian kernel: usb 1-3.2: USB "
               "disconnect, device number 4\n"
               "2026-11-13T10:00:40.000000-08:00 madhouse-debian kernel: e1000e eno1 link up\n"
               "2026-11-13T10:01:27.000000-08:00 madhouse-debian kernel: usb 1-3.2-port4: "
               "disabled by hub (EMI?), re-enabling...\n"
               "2026-11-13T10:01:47.000000-08:00 madhouse-debian kernel: usb 1-3.4: device "
               "descriptor read/64, error -32\n"
               "2026-11-13T10:02:07.000000-08:00 madhouse-debian kernel: cdc_acm 1-3.1:1.0: "
               "ttyACM0: USB ACM device\n"
               "2026-11-13T10:02:37.000000-08:00 madhouse-debian kernel: usb 1-3.2.1: device "
               "descriptor read/64, error -71\n"
               "2026-11-13T10:02:57.000000-08:00 madhouse-debian kernel: usb 1-3.2.1: device "
               "descriptor read/64, error -71\n")
        lines, n = scan([d], lambda s, u: log)
        text = "\n".join(lines)
        check(n == 5, "five judged results flagged (a -71, a disconnect, disabled by hub, "
              "error -32, a chain's bench run beside a -71): %d" % n)
        check(any(x.startswith("  FLAGGED x-chain bench/run FAIL") for x in lines),
              "a chain record's row for a hardware run is scanned and FLAGGED")
        check("emulated/run" not in text and any(
            x.startswith("not scanned, no hardware in the loop: x-chain, 1 judged") for x in lines),
              "a chain record's row for the emulated run is not scanned")
        check(any(x.startswith("  FLAGGED x-bench b/emi PASS") and "disabled by hub" in x
                  for x in lines), "a PASS beside a port disabled by its hub is FLAGGED")
        check(any(x.startswith("  FLAGGED x-bench b/epipe FAIL") and "error -32" in x
                  for x in lines), "a FAIL beside an error -32 is FLAGGED")
        check(any("b/wican PASS: 1 kernel line(s), none" in x for x in lines),
              "a WiCAN line on 1-3.1 (cdc_acm) is read, and listed, not flagged")
        check("m/sim" not in text and any(
            x.startswith("not scanned, no hardware in the loop: x-emulated, 1 judged")
            and "charge-interposer-emulated" in x for x in lines),
              "an emulated run's PASS beside a -71 is not scanned, and counted as not scanned")
        check("h/x" not in text and any(
            x.startswith("not scanned, no hardware in the loop: x-nosuite, 1 judged")
            and "names no suite" in x for x in lines),
              "a run whose start event names no suite is not scanned, and says so")
        only = scan([os.path.join(d, "runs", "x-emulated")], lambda s, u: log)
        check(only[1] == 0 and any(x.startswith("no judged hardware result") for x in only[0]),
              "a record of only non-hardware runs reads no kernel window: %s" % only[0][-1])
        check(any(x.startswith("  FLAGGED x-bench b/hit PASS") and "error -71" in x
                  for x in lines), "a PASS beside a -71 is FLAGGED, its line quoted")
        check(any(x.startswith("  FLAGGED x-bench b/fail FAIL") and "USB disconnect" in x
                  for x in lines), "a FAIL beside a disconnect is FLAGGED")
        check(any("b/clean PASS: 1 kernel line(s), none" in x for x in lines),
              "a window with only an unflagged USB/CAN line is listed, not flagged")
        check("b/skip" not in text, "a NOT-RUN result is not judged, so not listed")
        check(any("b/nodur PASS: no duration, no window" in x for x in lines),
              "a result with no duration says it has no window")
        check("eno1" not in text, "a line that is not USB/CAN is not read")
        try:
            scan([d], lambda s, u: (_ for _ in ()).throw(OSError("no journal")))
            check(False, "an unreadable kernel log read as no lines")
        except RuntimeError as e:
            check("could not be read" in str(e), "an unreadable kernel log raises, never 'none'")


MUTANTS = [
    ("no flag at all", "        if bad:\n            flagged += 1", "        if False:\n            flagged += 1"),
    ("non-hardware runs scanned", "            if suite not in HARDWARE_SUITES and not any(",
     "            if False and not any("),
    ("a run with no suite taken for hardware", "            if suite not in HARDWARE_SUITES and not any(",
     "            if suite is not None and suite not in HARDWARE_SUITES and not any("),
    ("a chain's rows for hardware runs not scanned",
     "                    h.endswith(\"-%s\" % r.get(\"group\")) for h in hw if r.get(\"group\")):",
     "                    False for h in hw):"),
    ("a chain's rows scanned whatever run they name",
     "                    h.endswith(\"-%s\" % r.get(\"group\")) for h in hw if r.get(\"group\")):",
     "                    True for h in hw):"),
    ("the emulated suite counted as hardware", "HARDWARE_SUITES = (\"charge-interposer-bench\",)",
     "HARDWARE_SUITES = (\"charge-interposer-bench\", \"charge-interposer-emulated\")"),
    ("NOT-RUN judged", "r.get(\"verdict\") == \"NOT-RUN\":", "False:"),
    ("the window ignores the duration",
     "            t0 = t1 - float(dur) if isinstance(dur, (int, float)) else None",
     "            t0 = t1 - 0.0 if isinstance(dur, (int, float)) else None"),
    ("-71 not flagged", "FLAG_RE = re.compile(r\"-71\\b|EPROTO|", "FLAG_RE = re.compile(r\"EPROTO|"),
    ("disabled by hub not flagged", "|disconnect|disabled by hub|error -32\\b\", re.I)",
     "|disconnect|error -32\\b\", re.I)"),
    ("error -32 not flagged", "|disconnect|disabled by hub|error -32\\b\", re.I)",
     "|disconnect|disabled by hub\", re.I)"),
    ("an unreadable log read as none",
     "        if ln.startswith(\"(the kernel log could not be read\"):\n            raise RuntimeError(ln)",
     "        if ln.startswith(\"(the kernel log could not be read\"):\n            continue"),
    ("nested runs not read", "            if \"progress.jsonl\" not in files:\n                continue",
     "            if \"progress.jsonl\" not in files or root != d:\n                continue"),
]


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if argv == ["--can-fail"]:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    lines, n = scan(a.run_dir)
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
