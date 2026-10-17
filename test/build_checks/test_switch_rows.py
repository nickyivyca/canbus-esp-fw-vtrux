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
  run did not produce fails it; and the same for "every stock feature off in
  this product has a refusal row" (2026-10-16);
- the stock-feature rows (spec 3.2, 12.3 item 3; 2026-10-16): call_sites on
  synthetic objdump text in both line shapes, and stock_rows on synthetic symbol
  sets and call sites, each feature and each bus-state breach reaching its own
  row. The real switch-on/switch-off builds are test_refusal_pairs.py's.
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

    real_scan, real_table, real_triggers, real_record = BS.scan, BS.SWITCHES, BS.TRIGGERS, BS.read_record
    ok_t = {"A": (BS.SHIP, None, "why", None), "B": (BS.TEST, None, "why", "row b")}
    try:
        def drive(found, table):
            # classify() reads TRIGGERS too, so a substituted table needs triggers
            # that match it: one per test-only entry, as the real tables have
            BS.read_record = lambda repo, build_dir, run=None: None
            BS.scan = lambda repo=None, record=None: found
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
        def no_record(repo, build_dir, run=None):
            raise BS.RecordError("no build.ninja")
        BS.read_record = no_record
        BO.rows.clear()
        BO.check_switches_classified()
        got = {n: ok for n, ok, _d, _r in BO.rows}
        checks.append(("classified: an unusable build record FAILS the row, never a fallback",
                       got.get("every build-time switch classified") is False))

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
        SR = "every stock feature off in this product has a refusal row"
        BS.SWITCHES = {"S": (BS.STOCK, None, "why", "row s")}
        BO.rows.clear()
        BO.row("row s", True, "")
        BO.check_test_only_refused()
        got = {n: ok for n, ok, _d, _r in BO.rows}
        checks.append(("refused: a stock feature whose row ran passes", got.get(SR) is True))
        BO.rows.clear()
        BO.row("some other row", True, "")
        BO.check_test_only_refused()
        got = {n: ok for n, ok, _d, _r in BO.rows}
        checks.append(("refused: a stock feature whose row did not run fails", got.get(SR) is False))
    finally:
        BS.scan, BS.SWITCHES, BS.TRIGGERS, BS.read_record = real_scan, real_table, real_triggers, real_record
        BO.rows.clear()

    checks.extend(stock_checks())

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


DISASM = """
42000100 <apply_mode>:
42000104:\tjal\tra,42000900 <can_enable>
42000108:\t0e2080ef          \tjal\tra,42000980 <can_disable>
42000200 <gen_inhibit_task>:
42000204:\tj\t42000980 <can_disable>
42000300 <upload_post_handler>:
42000304:\tjalr\tra,-12(ra) # 42000980 <can_disable>
42000308:\taddi\ta0,a0,256 # 42000900 <can_enable>
42000900 <can_enable>:
42000904:\tbeqz\ta0,42000910 <can_enable+0x10>
42000908:\tj\t42000900 <can_enable>
42000980 <can_disable>:
42000984:\tret
"""
PRODUCT_SITES = {"can_enable": {"apply_mode": 1},
                 "can_disable": {"apply_mode": 1, "gen_inhibit_task": 1, "upload_post_handler": 1},
                 "can_flush_rx": {}, "can_send": {}}


def stock_checks():
    out = []
    s = BO.call_sites(DISASM, ("can_enable", "can_disable", "can_send"))
    out.append(("call_sites: jal, j and a commented jalr counted, by caller, in both line shapes",
                s["can_disable"] == {"apply_mode": 1, "gen_inhibit_task": 1, "upload_post_handler": 1}))
    out.append(("call_sites: a branch to <can_enable+0x10>, its own self-jump and an address taken (addi) are not call sites",
                s["can_enable"] == {"apply_mode": 1}))
    out.append(("call_sites: a target never called is empty", s["can_send"] == {}))
    nm = "42000100 T apply_mode\n42000200 t gen_inhibit_task\n         U printf\n"
    out.append(("defined_symbols: defined names only", BO.defined_symbols(nm) == {"apply_mode", "gen_inhibit_task"}))

    base = {"apply_mode", "gen_inhibit_task", "upload_post_handler", "can_enable", "can_disable", "app_main"}

    def rows(defined=(), sites=None):
        return {n: (ok, det) for n, ok, det in BO.stock_rows(base | set(defined), sites or PRODUCT_SITES)}

    r = rows()
    out.append(("stock_rows: the product shape passes every row", all(ok for ok, _ in r.values())
                and len(r) == len(BO.STOCK_FEATURES) + 2))
    for flag, name, syms, prefixes in BO.STOCK_FEATURES:
        for sym in syms:
            r = rows({sym})
            out.append(("stock_rows: %s alone fails %r, naming %s" % (sym, name, flag),
                        r[name][0] is False and flag in r[name][1]
                        and sum(1 for ok, _ in r.values() if not ok) == 1))
        for p in prefixes:
            r = rows({p + "init"})
            out.append(("stock_rows: a %s* name fails %r" % (p, name), r[name][0] is False))
    sites = dict(PRODUCT_SITES, can_enable={"apply_mode": 1, "app_main": 1})
    r = rows(sites=sites)
    out.append(("stock_rows: app_main calling can_enable fails the boot-enable row", r[BO.REALDASH_ROW][0] is False))
    out.append(("stock_rows: ... and the bus-state row", r[BO.BUS_STATE_ROW][0] is False))
    for fn, caller in (("can_disable", "elm327_set_protocol"), ("can_enable", "mqtt_parse_data"),
                       ("can_enable", "upload_post_handler")):
        sites = dict(PRODUCT_SITES, **{fn: dict(PRODUCT_SITES[fn], **{caller: 1})})
        r = rows(sites=sites)
        out.append(("stock_rows: %s from %s fails the bus-state row, naming it" % (fn, caller),
                    r[BO.BUS_STATE_ROW][0] is False and caller in r[BO.BUS_STATE_ROW][1]))
    for absent in BO.BUS_STATE_ABSENT:
        r = rows({absent})
        out.append(("stock_rows: %s in the image fails the bus-state row" % absent,
                        r[BO.BUS_STATE_ROW][0] is False and absent in r[BO.BUS_STATE_ROW][1]))

    img = bytes(1) + b"product image" + bytes(1) + b"gen_inhibit" + bytes(1)
    r = {n: ok for n, ok, _d in BO.stock_image_rows(img)}
    out.append(("stock_image_rows: an image with no marker passes every row",
                all(r.values()) and len(r) == len(BO.STOCK_IMAGE_MARKERS)))
    for flag, name, markers in BO.STOCK_IMAGE_MARKERS:
        for m in markers:
            r = {n: (ok, d) for n, ok, d in BO.stock_image_rows(img + m + bytes(1))}
            out.append(("stock_image_rows: %r alone fails %r, naming %s" % (m.decode(), name, flag),
                        r[name][0] is False and flag in r[name][1]
                        and sum(1 for ok, _ in r.values() if not ok) == 1))
    return out


if __name__ == "__main__":
    sys.exit(main())
