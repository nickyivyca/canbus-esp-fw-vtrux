#!/usr/bin/env python3
"""Tests build_output.py's source rows over the shipping source, on throwaway projects.

Gen-inhibit tester, 2026-10-29, port step 1a's rest. The stock code moved into the
shared component vtrux/wican, compiled from the build's managed_components/, and the
rows that read it had looked only in main/:

  "one transmit owner (spec 5.2 item 1)" and "the private TWAI abort is confined"
      scan the shipping source: main/ whole, and every file the build's compiles read
      (its record, `ninja -t deps`) from a shared component vtrux_build.json names
      (gen-inhibit reviewer, 2026-10-29: the spec's "shipping source" includes the
      stock code compiled from managed_components/). No record, or a record with no
      file of a shared component, is NO REF, never a pass on main/ alone.
  "WiFi storage is RAM-only"
      reads vtrux/wican's wifi_network.c in the build's copy, not main/'s.

Each case builds a project in scratch/: main/, managed_components/vtrux__wican/ and
espressif__mdns/ (not this project's source), vtrux_build.json, and a build directory
whose deps record is a fake `ninja -t deps`.

    python3 test_source_rows.py
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_output as B                                        # noqa: E402

TMP = os.path.join(B.WORKTREE, "scratch", "build-checks", "test_source_rows")
BUILD = os.path.join(TMP, "build")
WICAN = os.path.join(TMP, "managed_components", "vtrux__wican")
MDNS = os.path.join(TMP, "managed_components", "espressif__mdns")
BOARD = os.path.join(TMP, "managed_components", "vtrux__wican_v3")
LOGS = os.path.join(TMP, "managed_components", "vtrux__debug_logs")

MAIN = {
    "can.c": "int can_send(void) { return twai_transmit(0, 0); }\n",
    "gen_inhibit.c": '#include "gi_sched.h"\nvoid hal(void) { twai_transmit(0, 0); twai_ll_set_cmd_abort_tx(0); }\n',
    "main.c": "void app_main(void) { wican_init(); wican_start(); }\n",
    "gi_txabort_probe.c": "void probe(void) { twai_transmit(0, 0); twai_ll_set_cmd_abort_tx(0); }\n",
}
WIFI_LIVE = "void wifi(void) {\n    esp_wifi_set_storage(WIFI_STORAGE_RAM);\n}\n"
STOCK = {
    "wican.c": "void wican_start(void) { }\n",
    "slcan.c": "void slcan(void) {\n    // twai_transmit(0, 0);\n}\n",
    "wifi_network.c": WIFI_LIVE,
    "wican.h": "void wican_start(void);\n",
}


def project(main=None, stock=None, mdns_call=False, names=("vtrux/wican_v3", "vtrux/wican", "vtrux/debug_logs"),
            build_file=True):
    shutil.rmtree(TMP, ignore_errors=True)
    for d in (os.path.join(TMP, "main"), WICAN, MDNS, BUILD, BOARD, LOGS):
        os.makedirs(d)
    for p, text in ((os.path.join(BOARD, "board.h"), "#define BOARD 1\n"),
                    (os.path.join(LOGS, "debug_logs.c"), "void dl(void) { }\n")):
        with open(p, "w") as fh:
            fh.write(text)
    for folder, files in ((os.path.join(TMP, "main"), dict(MAIN, **(main or {}))), (WICAN, dict(STOCK, **(stock or {})))):
        for f, text in files.items():
            if text is not None:
                with open(os.path.join(folder, f), "w") as fh:
                    fh.write(text)
    with open(os.path.join(MDNS, "mdns.c"), "w") as fh:
        fh.write("void m(void) { %s }\n" % ("twai_transmit(0, 0);" if mdns_call else ""))
    if build_file:
        with open(os.path.join(TMP, "vtrux_build.json"), "w") as fh:
            json.dump({"project": "gen-inhibit", "shared_components": list(names)}, fh)
    with open(os.path.join(BUILD, "project_description.json"), "w") as fh:
        json.dump({"project_path": TMP}, fh)
    with open(os.path.join(BUILD, "CMakeCache.txt"), "w") as fh:
        fh.write("CMAKE_MAKE_PROGRAM:FILEPATH=ninja\n")
    open(os.path.join(BUILD, "build.ninja"), "w").close()


def deps(compiled, stale=(), rc=0, fail_in=None):
    """A fake subprocess.run for `ninja -t deps`: one VALID record per compiled file
    (absolute paths), one STALE record per stale one. fail_in: a folder where ninja
    fails, whatever rc says elsewhere."""
    lines = []
    for p in compiled:
        lines += ["%s.obj: #deps 1, deps mtime 1 (VALID)" % os.path.basename(p), "    " + p, ""]
    for p in stale:
        lines += ["%s.obj: #deps 1, deps mtime 1 (STALE)" % os.path.basename(p), "    " + p, ""]

    class R:
        returncode = rc
        stdout = "\n".join(lines)
        stderr = "" if rc == 0 else "ninja: error"

    def run(cmd, **kw):
        if cmd[1:] != ["-t", "deps"]:
            raise AssertionError(cmd)
        if fail_in is not None and os.path.normpath(kw.get("cwd", "")) == os.path.normpath(fail_in):
            class F(R):
                returncode, stdout, stderr = 1, "", "ninja: error"
            return F()
        return R()
    return run


def product_record(extra=()):
    """The files a product build's compiles read: main/'s compiled files, vtrux/wican's,
    and mdns's."""
    m = [os.path.join(TMP, "main", f) for f in ("can.c", "gen_inhibit.c", "main.c")]
    w = [os.path.join(WICAN, f) for f in ("wican.c", "slcan.c", "wifi_network.c", "wican.h")]
    return m + w + [os.path.join(MDNS, "mdns.c"), os.path.join(BOARD, "board.h"),
                    os.path.join(LOGS, "debug_logs.c")] + list(extra)


def rows_of(fn, run):
    B.rows.clear()
    fn(BUILD, run)
    out = {n: (ok, d, ref) for n, ok, d, ref in B.rows}
    B.rows.clear()
    return out


OWNER = "one transmit owner (spec 5.2 item 1)"
ABORT = "the private TWAI abort is confined"
WIFI = "WiFi storage is RAM-only"


def both(run):
    r = rows_of(B.check_one_transmit_owner, run)
    r.update(rows_of(B.check_private_abort_confined, run))
    return r


def wifi_row():
    B.rows.clear()
    B.check_source(BUILD)
    out = {n: (ok, d, ref) for n, ok, d, ref in B.rows}
    B.rows.clear()
    return out[WIFI]


def main():
    checks = []

    def case(label, good, detail=""):
        checks.append((label, bool(good), detail))

    project()
    r = both(deps(product_record()))
    case("product shape: one transmit owner passes", r[OWNER][0] is True and not r[OWNER][2], r[OWNER][1])
    case("product shape: the private abort is confined", r[ABORT][0] is True, r[ABORT][1])
    case("product shape: the shared components' files are in the scan (10: main/ 4, vtrux/wican 4, board 1, logs 1)",
         "in 10 files" in r[OWNER][1], r[OWNER][1])

    project(stock={"slcan.c": "void slcan(void) { twai_transmit(0, 0); }\n"})
    r = both(deps(product_record()))
    case("twai_transmit in a compiled vtrux/wican file fails the owner row, naming it",
         r[OWNER][0] is False and "managed_components/vtrux__wican/slcan.c" in r[OWNER][1], r[OWNER][1])
    project(stock={"slcan.c": "void slcan(void) { twai_transmit(0, 0); }\n"})
    rec = [p for p in product_record() if not p.endswith("slcan.c")]
    r = both(deps(rec))
    case("... but not when the build did not compile that file", r[OWNER][0] is True, r[OWNER][1])
    r = both(deps(rec, stale=[os.path.join(WICAN, "slcan.c")]))
    case("... nor when its record is STALE", r[OWNER][0] is True, r[OWNER][1])

    project(stock={"wican.h": "static inline void tx(void) { twai_transmit(0, 0); }\n"})
    r = both(deps(product_record()))
    case("twai_transmit in a vtrux/wican header a compile read fails the owner row",
         r[OWNER][0] is False and "vtrux__wican/wican.h" in r[OWNER][1], r[OWNER][1])

    project(mdns_call=True)
    r = both(deps(product_record()))
    case("twai_transmit in a managed component that is not a shared one (mdns) is not this project's source",
         r[OWNER][0] is True, r[OWNER][1])

    project(main={"extra.c": "void x(void) { twai_transmit(0, 0); }\n"})
    r = both(deps(product_record()))
    case("twai_transmit in a main/ file the build did not compile still fails (main/ is scanned whole)",
         r[OWNER][0] is False and "main/extra.c" in r[OWNER][1], r[OWNER][1])

    project(main={"can.c": "int can_send(void) { twai_transmit(0, 0); return twai_transmit(1, 0); }\n"})
    r = both(deps(product_record()))
    case("a second call in main/can.c fails the owner row", r[OWNER][0] is False and "main/can.c has 2" in r[OWNER][1],
         r[OWNER][1])
    project(main={"can.c": None})
    r = both(deps([p for p in product_record() if not p.endswith("can.c")]))
    case("no main/can.c: the owner row is NO REF", r[OWNER][0] is None and r[OWNER][2], r[OWNER][1])

    project(stock={"wican.c": "void w(void) { twai_ll_set_cmd_abort_tx(0); }\n"})
    r = both(deps(product_record()))
    case("the private abort in a compiled vtrux/wican file fails the abort row, naming it",
         r[ABORT][0] is False and "managed_components/vtrux__wican/wican.c" in r[ABORT][1], r[ABORT][1])
    project(main={"gen_inhibit.c": '#include "gi_sched.h"\nvoid hal(void) { twai_transmit(0, 0); }\n'})
    r = both(deps(product_record()))
    case("the scheduler wired in with no abort site fails the abort row (lost)", r[ABORT][0] is False and
         "lost" in r[ABORT][1], r[ABORT][1])

    project()
    r = both(deps(product_record(), rc=1))
    case("the record cannot be read: both rows NO REF",
         r[OWNER][0] is None and r[OWNER][2] and r[ABORT][0] is None and r[ABORT][2], r[OWNER][1])
    boot = os.path.join(BUILD, "bootloader")         # a CMake build folder below the build directory
    os.makedirs(boot)
    for f in ("CMakeCache.txt", "build.ninja"):
        open(os.path.join(boot, f), "w").close()
    r = both(deps(product_record(), fail_in=boot))
    case("the record cannot be read in one build folder (the bootloader's): both rows NO REF",
         r[OWNER][0] is None and r[OWNER][2] and r[ABORT][0] is None and r[ABORT][2], r[OWNER][1])
    r = both(deps(product_record()))
    case("... and with it readable, both rows pass", r[OWNER][0] is True and r[ABORT][0] is True, r[OWNER][1])
    r = both(deps([p for p in product_record() if not p.startswith(WICAN)]))
    case("a record with no vtrux/wican file: both rows NO REF, naming it",
         r[OWNER][0] is None and "vtrux/wican" in r[OWNER][1] and r[ABORT][0] is None, r[OWNER][1])
    project(build_file=False)
    r = both(deps(product_record()))
    case("no vtrux_build.json: both rows NO REF", r[OWNER][0] is None and r[ABORT][0] is None, r[OWNER][1])

    project(stock={"slcan.c": "void slcan(void) { twai_transmit(0, 0); }\n"}, names=("vtrux/wican_v3",))
    r = both(deps(product_record()))
    case("a component vtrux_build.json does not name is not scanned (the list is the project's)",
         r[OWNER][0] is True, r[OWNER][1])
    project()
    r = both(deps([p for p in product_record() if not p.startswith(LOGS)]))
    case("a record with no file of one shared component (debug_logs): NO REF, naming it",
         r[OWNER][0] is None and "vtrux/debug_logs" in r[OWNER][1], r[OWNER][1])

    project()
    ok, d, ref = wifi_row()
    case("WiFi row: vtrux/wican's wifi_network.c with a live call passes, naming it",
         ok is True and not ref and "vtrux/wican" in d, d)
    project(stock={"wifi_network.c": "void wifi(void) {\n    // esp_wifi_set_storage(WIFI_STORAGE_RAM);\n}\n"})
    ok, d, ref = wifi_row()
    case("WiFi row: a commented-out call fails", ok is False, d)
    project(stock={"wifi_network.c": None}, main={"wifi_network.c": WIFI_LIVE})
    ok, d, ref = wifi_row()
    case("WiFi row: no file in vtrux/wican is NO REF, and main/'s old copy does not rescue it",
         ok is None and ref, d)
    project(names=("vtrux/wican_v3", "vtrux/debug_logs"))
    ok, d, ref = wifi_row()
    case("WiFi row: vtrux/wican not a shared component is NO REF", ok is None and ref, d)

    shutil.rmtree(TMP, ignore_errors=True)
    fails = 0
    for label, good, detail in checks:
        print("  %-4s %s%s" % ("ok" if good else "FAIL", label, "" if good else "  -- " + str(detail)[:140]))
        fails += not good
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
