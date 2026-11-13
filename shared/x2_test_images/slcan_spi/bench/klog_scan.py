"""The kernel's USB and CAN lines over every judged check's window in
slcan_spi_bench.py's runs, and a PASS or FAIL beside a -71, an EPROTO or a
disconnect flagged (the charge-interposer reviewer, 2026-11-13: the bench's
USB hubs are bus-powered, so such lines may be power faults). NOT FOR
VEHICLE (SPEC T14). An entry-time scan, re-runnable from the run folders.

Each check's window runs from its "check <name>" log line to its verdict
line (result.json's log, whole seconds on the run's own date and UTC offset),
widened by PAD_S on each side. The lines are journalctl -k's, matching
KLOG_RE (as charge-interposer/kvaser_link.py's).

    python3.13 -B klog_scan.py <run folder> [<run folder> ...]
        each folder, or any folder under it, holding a result.json

Prints one row per check and writes <folder>/klog_scan.json beside each
result.json. Exit 0 scanned (flags are rows, not failures), 2 a kernel log
that could not be read or no result.json found. --self-test runs its tests
on a stand-in journal; --can-fail runs its mutants, each of which the tests
must catch (exit 0 only if all are caught).
"""

import datetime
import json
import os
import re
import subprocess
import sys

KLOG_RE = re.compile(r"usb 1-3\.2|gs_usb|kvaser_usb|peak_usb|pcan|-71\b|EPROTO|disconnect", re.I)
FLAG_RE = re.compile(r"-71\b|EPROTO|disconnect", re.I)
JOURNALCTL = ["journalctl", "-k", "-o", "short-iso-precise", "--no-pager"]
PAD_S = 1.0
CHECK_RE = re.compile(r"^(\d\d:\d\d:\d\d) check (\S+)")
VERDICT_RE = re.compile(r"^(\d\d:\d\d:\d\d)\s+(PASS|FAIL|NOT-RUN)\b")
JUDGED = ("PASS", "FAIL")


def windows(result):
    """result.json's content -> [(check, t0, t1, verdict)], epoch seconds,
    from its log's check and verdict lines on its started date and offset."""
    started = datetime.datetime.strptime(result["started"], "%Y-%m-%dT%H:%M:%S%z")
    out, cur = [], None

    def at(hms):
        t = datetime.datetime.strptime(hms, "%H:%M:%S").time()
        d = datetime.datetime.combine(started.date(), t, started.tzinfo)
        if d < started - datetime.timedelta(seconds=1):
            d += datetime.timedelta(days=1)          # the run crossed midnight
        return d.timestamp()

    for ln in result.get("log", []):
        m = CHECK_RE.match(ln)
        if m:
            cur = (m.group(2), at(m.group(1)))
            continue
        m = VERDICT_RE.match(ln)
        if m and cur:
            out.append((cur[0], cur[1], at(m.group(1)), m.group(2)))
            cur = None
    return out


def journal_text(since, until):
    p = subprocess.run(JOURNALCTL + ["--since", "@%d" % since, "--until", "@%d" % until],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError("journalctl rc %d: %s" % (p.returncode, (p.stderr or p.stdout).strip()[-200:]))
    return p.stdout


def scan(wins, journal=journal_text):
    """[(check, t0, t1, verdict)] -> rows {check, verdict, window, lines,
    flagged}: flagged when a judged verdict has a FLAG_RE line in its window.
    journal(since, until) -> text; it raises if the log cannot be read."""
    rows = []
    for check, t0, t1, verdict in wins:
        since, until = int(t0 - PAD_S), int(t1 + PAD_S) + 1
        lines = [ln.strip() for ln in journal(since, until).splitlines() if KLOG_RE.search(ln)]
        rows.append({"check": check, "verdict": verdict, "window": [since, until],
                     "lines": lines,
                     "flagged": verdict in JUDGED and any(FLAG_RE.search(ln) for ln in lines)})
    return rows


def results_under(folders):
    found = []
    for d in folders:
        for root, _dirs, files in os.walk(d):
            if "result.json" in files:
                found.append(os.path.join(root, "result.json"))
    return sorted(found)


def main(argv):
    paths = results_under(argv)
    if not paths:
        print("no result.json under %s" % argv)
        return 2
    for p in paths:
        with open(p) as f:
            result = json.load(f)
        try:
            rows = scan(windows(result))
        except Exception as e:                      # noqa: BLE001
            print("REFUSED: the kernel log could not be read for %s: %s" % (p, e))
            return 2
        with open(os.path.join(os.path.dirname(p), "klog_scan.json"), "w") as f:
            json.dump({"pattern": KLOG_RE.pattern, "flag": FLAG_RE.pattern, "pad_s": PAD_S,
                       "rows": rows}, f, indent=1)
        for r in rows:
            print("%-44s %-17s %-7s lines %d%s" % (
                os.path.basename(os.path.dirname(p)), r["check"], r["verdict"], len(r["lines"]),
                "  FLAGGED" if r["flagged"] else ""))
            for ln in r["lines"]:
                print("    " + ln)
    return 0


# ---------------------------------------------------------------- self-test

def run_tests(m):
    fails = []

    def check(cond, what):
        if not cond:
            fails.append(what)

    res = {"started": "2026-11-13T23:59:58-0800",
           "log": ["23:59:58 check jam-port", "23:59:59   PASS ",
                   "00:00:05 check jam-stop", "00:00:09   FAIL witness FAIL",
                   "00:00:15 check jam-cap", "00:00:18   NOT-RUN INCONCLUSIVE",
                   "00:00:18 result: x exit 1", "00:00:19   PASS (no check before it)"]}
    w = m.windows(res)
    base = datetime.datetime(2026, 11, 13, 23, 59, 58,
                             tzinfo=datetime.timezone(datetime.timedelta(hours=-8))).timestamp()
    check([x[0] for x in w] == ["jam-port", "jam-stop", "jam-cap"], "checks %r" % (w,))
    check(w and w[0][1] == base and w[0][2] == base + 1, "first window %r" % (w[:1],))
    check(len(w) > 1 and w[1][2] == base + 11, "window across midnight %r" % (w[1:2],))
    check([x[3] for x in w] == ["PASS", "FAIL", "NOT-RUN"], "verdicts %r" % (w,))

    def journal_for(lines_at):
        def j(since, until):
            return "\n".join(ln for t, ln in lines_at if since <= t <= until)
        return j
    planted = [(base + 0.5, "2026-11-13T23:59:58.5 madhouse-debian kernel: usb 1-3.2.1: "
                            "device descriptor read/64, error -71"),
               (base + 8, "2026-11-14T00:00:06 madhouse-debian kernel: kvaser_usb 1-3.2.3: "
                          "Unknown state"),
               (base + 8, "2026-11-14T00:00:06 madhouse-debian kernel: e1000e eno1: link up"),
               (base + 21.5, "2026-11-14T00:00:19.5 madhouse-debian kernel: usb 1-3.2: USB "
                             "disconnect, device number 9")]
    rows = m.scan(w, journal_for(planted))
    check(rows[0]["flagged"], "a PASS beside -71 not flagged: %r" % rows[0])
    check(len(rows[1]["lines"]) == 1 and not rows[1]["flagged"],
          "the kvaser_usb line not kept, an unrelated line kept, or flagged without -71: %r" % rows[1])
    check(rows[2]["lines"] and not rows[2]["flagged"], "a NOT-RUN flagged, or its window's "
          "disconnect (inside the pad) not kept: %r" % rows[2])

    def broken(since, until):
        raise RuntimeError("no journal")
    try:
        m.scan(w, broken)
        check(False, "an unreadable kernel log read as no lines")
    except RuntimeError:
        pass
    return fails


MUTANTS = [
    ("-71 not flagged", 'FLAG_RE = re.compile(r"-71\\b|EPROTO|disconnect", re.I)',
     'FLAG_RE = re.compile(r"EPROTO|disconnect", re.I)'),
    ("NOT-RUN flagged", '"flagged": verdict in JUDGED and any(', '"flagged": any('),
    ("no pad", "since, until = int(t0 - PAD_S), int(t1 + PAD_S) + 1", "since, until = int(t0), int(t1)"),
    ("midnight ignored", "            d += datetime.timedelta(days=1)", "            pass"),
    ("all kernel lines", "if KLOG_RE.search(ln)]", "if ln.strip()]"),
    ("verdict not paired", "        if m and cur:\n", "        if m:\n"),
]


def self_test(can_fail):
    import types
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    cut = src.index("# " + "-" * 64 + " self-test")
    code, rest = src[:cut], src[cut:]       # mutants touch the code, not this test

    def load(text):
        mod = types.ModuleType("klog_scan_under_test")
        exec(compile(text, __file__, "exec"), mod.__dict__)
        return mod
    fails = run_tests(load(src))
    if not can_fail:
        for x in fails:
            print("FAIL", x)
        print("%d FAILED" % len(fails) if fails else "ALL PASS")
        return 1 if fails else 0
    if fails:
        print("ORIGINAL FAILS:", fails)
        return 1
    bad = 0
    for name, old, new in MUTANTS:
        if code.count(old) != 1:
            print("%-22s BROKEN MUTANT (matches %d times)" % (name, code.count(old)))
            bad += 1
            continue
        try:
            f = run_tests(load(code.replace(old, new) + rest))
        except Exception as e:                      # noqa: BLE001
            f = ["raised %r" % e]
        print("%-22s %s" % (name, "caught" if f else "SURVIVED"))
        bad += 0 if f else 1
    print("%d of %d mutants survived or broken" % (bad, len(MUTANTS)) if bad
          else "all %d mutants caught" % len(MUTANTS))
    return 1 if bad else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv or "--can-fail" in sys.argv:
        sys.exit(self_test("--can-fail" in sys.argv))
    sys.exit(main(sys.argv[1:]))
