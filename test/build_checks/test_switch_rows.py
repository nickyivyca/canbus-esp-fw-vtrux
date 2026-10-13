#!/usr/bin/env python3
"""Tests build_output.py's switch rows (spec 12.3 item 3) without a build.

    python3 test_switch_rows.py

- "no restart test feature in the image": each marker alone fails it, a clean
  image passes it (the real switch-on/switch-off pair is the bench-side proof,
  recorded in the evidence doc);
- "every build-time switch classified" / "no stale switch entries" / "switch
  table well-formed": driven through a substituted scan and table, so each
  finding is shown to reach its own row and only that row;
- "every test-only switch has a refusal row": a table naming a row that this
  run did not produce fails it.
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_output as BO                                          # noqa: E402
import build_switches as BS                                        # noqa: E402

TMP = os.path.join(HERE, "_test_switch_rows_tmp")


def rows_of(fn, *a):
    BO.rows.clear()
    fn(*a)
    return {name: ok for name, ok, _d, _r in BO.rows}


def image(name, payload):
    p = os.path.join(TMP, name)
    with open(p, "wb") as fh:
        fh.write(b"\x00" * 64 + payload + b"\x00" * 64)
    return p


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    checks = []
    try:
        clean = rows_of(BO.check_no_restart_test, image("clean.bin", b"some ordinary image text"))
        checks.append(("restart test: a clean image passes the image row",
                       clean.get("no restart test feature in the image") is True))
        checks.append(("restart test: no ELF beside the image is NO REF, not a pass",
                       clean.get("no restart test symbols in the ELF") is None))
        for k, m in enumerate(BO.RESTART_TEST_MARKERS):
            r = rows_of(BO.check_no_restart_test, image("m%d.bin" % k, b"text " + m + b" text"))
            checks.append(("restart test: marker %r alone fails the image row" % m.decode(),
                           r.get("no restart test feature in the image") is False))
        checks.append(("restart test: _rt is a whole token, not a substring",
                       [bool(BO.RT_TOKEN.search(n)) for n in ("wican-fw_obd_a3394ca_man_rt", "x_rt_DIAG", "x_rtc",
                                                              "x_art", "wican-fw_obd_a3394ca_man")]
                       == [True, True, False, False, False]))
        missing = rows_of(BO.check_no_restart_test, os.path.join(TMP, "absent.bin"))
        checks.append(("restart test: an unreadable image is NO REF",
                       missing.get("no restart test feature in the image") is None))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    real_scan, real_table, real_triggers = BS.scan, BS.SWITCHES, BS.TRIGGERS
    ok_t = {"A": (BS.SHIP, None, "why", None), "B": (BS.TEST, None, "why", "row b")}
    try:
        def drive(found, table):
            # classify() reads TRIGGERS too, so a substituted table needs triggers
            # that match it: one per test-only entry, as the real tables have
            BS.scan = lambda repo=None: found
            BS.SWITCHES = table
            BS.TRIGGERS = {s: {"probes": (("GET", "/x"),)} for s, e in table.items() if e[0] == BS.TEST}
            return rows_of(BO.check_switches_classified)
        r = drive({"A": ["x:1"], "B": ["y:2"]}, ok_t)
        checks.append(("classified: a full, clean table passes all three rows",
                       [r.get(n) for n in ("every build-time switch classified", "no stale switch entries",
                                           "switch table well-formed")] == [True, True, True]))
        r = drive({"A": ["x:1"], "B": ["y:2"], "C": ["z:3"]}, ok_t)
        checks.append(("classified: an unlisted symbol fails 'classified' and only that",
                       [r.get(n) for n in ("every build-time switch classified", "no stale switch entries",
                                           "switch table well-formed")] == [False, True, True]))
        r = drive({"A": ["x:1"]}, ok_t)
        checks.append(("classified: a stale entry fails 'no stale' and only that",
                       [r.get(n) for n in ("every build-time switch classified", "no stale switch entries",
                                           "switch table well-formed")] == [True, False, True]))
        r = drive({"A": ["x:1"]}, {"A": (BS.TEST, None, "why", None)})
        checks.append(("classified: a test-only entry with no refusal fails 'well-formed'",
                       r.get("switch table well-formed") is False))

        BS.SWITCHES = ok_t
        BO.rows.clear()
        BO.row("row b", True, "")
        BO.check_test_only_refused()
        got = {n: ok for n, ok, _d, _r in BO.rows}
        checks.append(("refused: a test-only switch whose row ran passes",
                       got.get("every test-only switch has a refusal row") is True))
        BO.rows.clear()
        BO.row("some other row", True, "")
        BO.check_test_only_refused()
        got = {n: ok for n, ok, _d, _r in BO.rows}
        checks.append(("refused: a test-only switch whose row did not run fails",
                       got.get("every test-only switch has a refusal row") is False))
        BS.SWITCHES = {"A": (BS.SHIP, None, "why", None)}
        BO.rows.clear()
        BO.check_test_only_refused()
        got = {n: ok for n, ok, _d, _r in BO.rows}
        checks.append(("refused: a shipping variant needs no refusal row",
                       got.get("every test-only switch has a refusal row") is True))
    finally:
        BS.scan, BS.SWITCHES, BS.TRIGGERS = real_scan, real_table, real_triggers
        BO.rows.clear()

    # The real pair: images of one commit built with and without the switch, each
    # with its ELF beside it. Only this exercises the ELF half of the row.
    if "--pair" in sys.argv:
        on, off = sys.argv[sys.argv.index("--pair") + 1:sys.argv.index("--pair") + 3]
        r_on, r_off = rows_of(BO.check_no_restart_test, on), rows_of(BO.check_no_restart_test, off)
        names = ["no restart test feature in the image", "no restart test symbols in the ELF"]
        if "--with-name" in sys.argv:
            names.append("project name is not a restart test build")
        for n in names:
            checks.append(("pair: %r fails on the switch-on image" % n, r_on.get(n) is False))
            checks.append(("pair: %r passes on the switch-off image" % n, r_off.get(n) is True))
        BO.rows.clear()

    fails = 0
    for name, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", name))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
