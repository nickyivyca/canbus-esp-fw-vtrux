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

Port step 1b (2026-10-31): main/can.c is gone and the CAN port is shared/can_port on
shared/twai_port (spec 3.3, 5.1 item 1, 5.2 item 1; twai_port T6, T8). So:

  the owner row allows one twai_transmit, in vtrux/twai_port's twai_port.c, and no
      other anywhere -- gen_inhibit.c included, which now sends through the port;
  the abort row wants the private abort once, in twai_port's C3 register file, and,
      with the scheduler wired in, gen_inhibit.c calling twai_port_abort_if_awaiting;
  the RX rows read GEN_INHIBIT_RX_QUEUE_LEN and its live assignment to rx_queue_len
      in gen_inhibit.c, of the project the build belongs to.

Each case builds a project in scratch/: main/, managed_components/vtrux__wican/,
vtrux__twai_port/, vtrux__can_port/ and espressif__mdns/ (not this project's source),
vtrux_build.json, and a build directory whose deps record is a fake `ninja -t deps`.

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
TPORT = os.path.join(TMP, "managed_components", "vtrux__twai_port")
CPORT = os.path.join(TMP, "managed_components", "vtrux__can_port")

GI_C = ('#include "gi_sched.h"\n'
        "#define GEN_INHIBIT_RX_QUEUE_LEN    64\n"
        "void hal(void) { twai_port_send(0, 0); }\n"
        "int abort_hal(void) { return twai_port_abort_if_awaiting(); }\n"
        "void setup(void) {\n    c.rx_queue_len    = GEN_INHIBIT_RX_QUEUE_LEN;\n}\n")
MAIN = {
    "gen_inhibit.c": GI_C,
    "main.c": "void app_main(void) { wican_init(); wican_start(); }\n",
    "gi_txabort_probe.c": "void probe(void) { twai_transmit(0, 0); twai_ll_set_cmd_abort_tx(0); }\n",
}
PORT = {
    "twai_port.c": "int twai_port_send(void) {\n    /* the only twai_transmit (T6) */\n    return twai_transmit(0, 0);\n}\n",
    "twai_port_ll_esp32c3.c": "int twai_port_abort_if_awaiting(void) { twai_ll_set_cmd_abort_tx(0); return 0; }\n",
    "twai_port.h": "int twai_port_send(void);\n",
}
CAN_PORT = {"can_port.c": "int can_port_send(void) { return twai_port_send(0, 0); }\n"}
WIFI_LIVE = "void wifi(void) {\n    esp_wifi_set_storage(WIFI_STORAGE_RAM);\n}\n"
STOCK = {
    "wican.c": "void wican_start(void) { }\n",
    "slcan.c": "void slcan(void) {\n    // twai_transmit(0, 0);\n}\n",
    "wifi_network.c": WIFI_LIVE,
    "wican.h": "void wican_start(void);\n",
}


NAMES = ("vtrux/wican_v3", "vtrux/wican", "vtrux/debug_logs", "vtrux/can_port", "vtrux/twai_port")


def project(main=None, stock=None, port=None, can_port=None, mdns_call=False, names=NAMES,
            build_file=True):
    shutil.rmtree(TMP, ignore_errors=True)
    for d in (os.path.join(TMP, "main"), WICAN, MDNS, BUILD, BOARD, LOGS, TPORT, CPORT):
        os.makedirs(d)
    for p, text in ((os.path.join(BOARD, "board.h"), "#define BOARD 1\n"),
                    (os.path.join(LOGS, "debug_logs.c"), "void dl(void) { }\n")):
        with open(p, "w") as fh:
            fh.write(text)
    for folder, files in ((os.path.join(TMP, "main"), dict(MAIN, **(main or {}))), (WICAN, dict(STOCK, **(stock or {}))),
                          (TPORT, dict(PORT, **(port or {}))), (CPORT, dict(CAN_PORT, **(can_port or {})))):
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
    the two ports', and mdns's."""
    m = [os.path.join(TMP, "main", f) for f in ("gen_inhibit.c", "main.c")]
    w = [os.path.join(WICAN, f) for f in ("wican.c", "slcan.c", "wifi_network.c", "wican.h")]
    p = [os.path.join(TPORT, f) for f in sorted(PORT)] + [os.path.join(CPORT, f) for f in sorted(CAN_PORT)]
    return m + w + p + [os.path.join(MDNS, "mdns.c"), os.path.join(BOARD, "board.h"),
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


APPLIED = "rx_queue_len override applied"
DEPTH = B.RXQ_ROW
E4 = "E4 RX_DEPTH matches firmware"


def source_rows():
    B.rows.clear()
    B.check_source(BUILD)
    out = {n: (ok, d, ref) for n, ok, d, ref in B.rows}
    B.rows.clear()
    return out


def wifi_row():
    return source_rows()[WIFI]


def main():
    checks = []

    def case(label, good, detail=""):
        checks.append((label, bool(good), detail))

    project()
    r = both(deps(product_record()))
    case("product shape: one transmit owner passes", r[OWNER][0] is True and not r[OWNER][2], r[OWNER][1])
    case("product shape: the private abort is confined", r[ABORT][0] is True, r[ABORT][1])
    case("product shape: the shared components' files are in the scan "
         "(13: main/ 3, vtrux/wican 4, twai_port 3, can_port 1, board 1, logs 1)",
         "in 13 files" in r[OWNER][1], r[OWNER][1])
    case("product shape: the abort row names the port's C3 file and gen_inhibit.c's port call",
         "C3 register file" in r[ABORT][1] and "twai_port_abort_if_awaiting (x1)" in r[ABORT][1], r[ABORT][1])

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

    # port step 1b: the one caller is twai_port's send (T6); gen_inhibit.c sends through it
    project(main={"gen_inhibit.c": GI_C + "void direct(void) { twai_transmit(0, 0); }\n"})
    r = both(deps(product_record()))
    case("twai_transmit called directly in gen_inhibit.c fails the owner row (1b: only the port's send)",
         r[OWNER][0] is False and "main/gen_inhibit.c calls it 1" in r[OWNER][1], r[OWNER][1])
    project(main={"can.c": "int can_send(void) { return twai_transmit(0, 0); }\n"})
    r = both(deps(product_record(extra=[os.path.join(TMP, "main", "can.c")])))
    case("a main/can.c calling twai_transmit is no longer allowed: fails, naming it",
         r[OWNER][0] is False and "main/can.c calls it 1" in r[OWNER][1], r[OWNER][1])
    project(can_port={"can_port.c": "int can_port_send(void) { return twai_transmit(0, 0); }\n"})
    r = both(deps(product_record()))
    case("twai_transmit in vtrux/can_port (bypassing twai_port) fails the owner row, naming it",
         r[OWNER][0] is False and "managed_components/vtrux__can_port/can_port.c" in r[OWNER][1], r[OWNER][1])
    project(port={"twai_port.c": "int s(void) { twai_transmit(0, 0); return twai_transmit(1, 0); }\n"})
    r = both(deps(product_record()))
    case("a second call in twai_port.c fails the owner row",
         r[OWNER][0] is False and "vtrux__twai_port/twai_port.c has 2" in r[OWNER][1], r[OWNER][1])
    project(port={"twai_port.c": None})
    r = both(deps([p for p in product_record() if not p.endswith("twai_port.c")]))
    case("no twai_port.c (the rest of vtrux/twai_port compiled): the owner row is NO REF, naming it",
         r[OWNER][0] is None and r[OWNER][2] and "twai_port.c is missing" in r[OWNER][1], r[OWNER][1])
    project()
    r = both(deps([p for p in product_record() if not p.startswith(TPORT)]))
    case("a record with no vtrux/twai_port file: both rows NO REF, naming it",
         r[OWNER][0] is None and "vtrux/twai_port" in r[OWNER][1] and r[ABORT][0] is None, r[OWNER][1])

    project(stock={"wican.c": "void w(void) { twai_ll_set_cmd_abort_tx(0); }\n"})
    r = both(deps(product_record()))
    case("the private abort in a compiled vtrux/wican file fails the abort row, naming it",
         r[ABORT][0] is False and "managed_components/vtrux__wican/wican.c" in r[ABORT][1], r[ABORT][1])
    project(main={"gen_inhibit.c": GI_C + "void old(void) { twai_ll_set_cmd_abort_tx(0); }\n"})
    r = both(deps(product_record()))
    case("the private abort at its pre-1b site, gen_inhibit.c, fails the abort row, naming it",
         r[ABORT][0] is False and "main/gen_inhibit.c x1" in r[ABORT][1], r[ABORT][1])
    project(main={"gen_inhibit.c": GI_C + "void old(void) { twai_ll_set_cmd_abort_tx(0); }\n"},
            port={"twai_port_ll_esp32c3.c": "int twai_port_abort_if_awaiting(void) { return 0; }\n"})
    r = both(deps(product_record()))
    case("... and so does the one abort moved out of the port into gen_inhibit.c (one site, the wrong file)",
         r[ABORT][0] is False and "main/gen_inhibit.c x1" in r[ABORT][1], r[ABORT][1])
    project(port={"twai_port_ll_esp32c3.c": "int twai_port_abort_if_awaiting(void) { return 0; }\n"})
    r = both(deps(product_record()))
    case("no abort in twai_port's C3 file fails the abort row (lost from the port)",
         r[ABORT][0] is False and "has lost it" in r[ABORT][1], r[ABORT][1])
    project(port={"twai_port_ll_esp32c3.c": "void a(void) { twai_ll_set_cmd_abort_tx(0);\n twai_ll_set_cmd_abort_tx(0); }\n"})
    r = both(deps(product_record()))
    case("two aborts in twai_port's C3 file fail the abort row", r[ABORT][0] is False and "x2" in r[ABORT][1],
         r[ABORT][1])
    project(main={"gen_inhibit.c": GI_C.replace("return twai_port_abort_if_awaiting();", "return 0;")})
    r = both(deps(product_record()))
    case("the scheduler wired in, gen_inhibit.c never calling the port's abort: fails (lost)",
         r[ABORT][0] is False and "lost" in r[ABORT][1] and "gen_inhibit.c never calls" in r[ABORT][1], r[ABORT][1])
    project(main={"gen_inhibit.c": GI_C.replace("int abort_hal(void) { return twai_port_abort_if_awaiting(); }",
                                                "// int abort_hal(void) { return twai_port_abort_if_awaiting(); }")})
    r = both(deps(product_record()))
    case("... and a commented-out port call does not count", r[ABORT][0] is False and "lost" in r[ABORT][1],
         r[ABORT][1])
    project(main={"gen_inhibit.c": GI_C.replace('#include "gi_sched.h"\n', "").replace(
        "return twai_port_abort_if_awaiting();", "return 0;")})
    r = both(deps(product_record()))
    case("the scheduler not wired in, no port call: the abort row passes on the port's one site",
         r[ABORT][0] is True and "not wired in" in r[ABORT][1], r[ABORT][1])

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

    project(stock={"slcan.c": "void slcan(void) { twai_transmit(0, 0); }\n"},
            names=("vtrux/wican_v3", "vtrux/twai_port"))
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

    # port step 1b: the RX depth is gen_inhibit.c's (spec 3.3, 5.1 item 1). make_scenarios.RX_DEPTH
    # is the real file's (64), so E4's row is read against it.
    project()
    r = source_rows()
    case("RX rows, product shape: 64, assigned live, E4 equal, all pass and name gen_inhibit.c",
         r[APPLIED][0] is True and r[DEPTH][0] is True and r[E4][0] is True
         and "main/gen_inhibit.c assigns c.rx_queue_len" in r[APPLIED][1], r[APPLIED][1])
    project(main={"gen_inhibit.c": GI_C.replace("RX_QUEUE_LEN    64", "RX_QUEUE_LEN    32")})
    r = source_rows()
    case("RX rows: a depth of 32 fails the depth row and E4's", r[DEPTH][0] is False and r[E4][0] is False
         and "is 32" in r[DEPTH][1], r[DEPTH][1])
    project(main={"gen_inhibit.c": GI_C.replace("    c.rx_queue_len", "    // c.rx_queue_len")})
    r = source_rows()
    case("RX rows: a commented-out assignment fails the applied row", r[APPLIED][0] is False, r[APPLIED][1])
    project(main={"gen_inhibit.c": GI_C.replace("c.rx_queue_len    = GEN_INHIBIT_RX_QUEUE_LEN;",
                                                "c.rx_queue_len    = 32;")})
    r = source_rows()
    case("RX rows: rx_queue_len assigned from something else fails the applied row", r[APPLIED][0] is False,
         r[APPLIED][1])
    project(main={"gen_inhibit.c": GI_C.replace("c.rx_queue_len    = GEN_INHIBIT_RX_QUEUE_LEN;",
                                                "c.rx_queue_len    = GEN_INHIBIT_RX_QUEUE_LEN_MIN;")})
    r = source_rows()
    case("RX rows: ... including a longer name that starts with the constant's", r[APPLIED][0] is False,
         r[APPLIED][1])
    project(main={"gen_inhibit.c": None,
                  "can.c": "#define CAN_RX_QUEUE_LEN 64\nvoid c(void) { g_config.rx_queue_len = CAN_RX_QUEUE_LEN; }\n"})
    r = source_rows()
    case("RX rows: no gen_inhibit.c is NO REF for all three, and an old can.c does not rescue them",
         all(r[k][0] is None and r[k][2] for k in (APPLIED, DEPTH, E4)), r[APPLIED][1])

    shutil.rmtree(TMP, ignore_errors=True)
    fails = 0
    for label, good, detail in checks:
        print("  %-4s %s%s" % ("ok" if good else "FAIL", label, "" if good else "  -- " + str(detail)[:140]))
        fails += not good
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
