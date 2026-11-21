#!/usr/bin/env python3
"""Tests that e4_device_calibration.py and wican_txabort_bench.py name the running image
from /gen_inhibit and never ask for /check_status (reviewer, 2026-10-20: it answers with
the device's config, credentials included). Both asked for it until 2026-10-25: e4 as its
first request (git_version, fw_version, wifi_mode), and B11raw's Wi-Fi capture of port 80
kept the answer; the TX-abort bench for git_version and batt_voltage before and after.

The device is faked. Its /check_status answers 200 with a made-up value, so a request for
it shows in the paths asked for, in what is printed and in the record.

  e4      device_identity() on a good page, a page that does not answer, one that does
          not parse, one with no elf_sha256 and one whose elf_sha256 is not 64 hex; main()
          refusing on a page that does not answer, and main() through to its record (the
          replay, the arms and the provenance pass stubbed) with "device" the page's
          elf_sha256
  txabort main() up to its mode-0 refusal: the page read, the image printed, nothing else
          asked; device_id() keeps elf_sha256 only
  source  no string constant outside a docstring names /check_status in either file
  provenance  both tools were loaded from DEVICE_READS_DIR's own files (2026-11-20), with a
          can-fail: a module from another folder is not

    python3.13 -B gen-inhibit/tools/bench/test_device_page_reads.py    (py -3.14 -B on Windows)
Mutants: test_device_page_reads_mutants.py. DEVICE_READS_DIR runs another copy of the two
tools (the mutants use it); each is loaded from that folder's file, not by the import path
(2026-11-20: from 602206f3 e4_rig_calibration put this folder first on sys.path, so the
txabort mutants ran on the unmutated tool and survived). Gen-inhibit tester, 2026-10-25.
"""
import ast
import contextlib
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("DEVICE_READS_DIR") or HERE
sys.path.insert(0, SRC)
sys.path.insert(1, HERE)
import gi_paths                    # noqa: E402


def load_tool(name):
    """The tool `name` from SRC's own file, never by the import path: a module a tool imports
    may put its folder first on sys.path (e4_rig_calibration does, from 602206f3), and an
    import by name after it found this folder's unmutated wican_txabort_bench, not the copy
    in SRC, so the mutants of it survived (the whole tools test set, 2026-11-20)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def from_src(mod):
    """True when the module was loaded from SRC's own file."""
    return os.path.dirname(os.path.abspath(getattr(mod, "__file__", "") or "")) == os.path.abspath(SRC)


E4 = load_tool("e4_device_calibration")
TX = load_tool("wican_txabort_bench")

FAILS = []
MADE_UP = "made-up-value-not-a-credential"
SHA = "c0" * 32


def check(label, cond, detail=""):
    print("%s  %s  %s" % ("ok  " if cond else "FAIL", label, " | ".join(str(detail).splitlines())[:150]))
    if not cond:
        FAILS.append(label)


class Device:
    """A WiCAN. /gen_inhibit answers `page` (a dict, a raw string, or None for no answer);
    /check_status answers 200 with the made-up value. Records every path."""

    def __init__(self, page):
        self.page, self.paths = page, []

    def answer(self, path):
        self.paths.append(path.split("?")[0])
        if path.startswith("/check_status"):
            return 200, json.dumps({"git_version": MADE_UP, "sta_pass": MADE_UP, "wifi_mode": "AP"})
        if path.startswith("/gen_inhibit"):
            if self.page is None:
                return None, "TimeoutError: timed out"
            return 200, self.page if isinstance(self.page, str) else json.dumps(self.page)
        return 404, "not found"

    def gi(self, path, method="GET", timeout=6.0):         # e4's gi()
        return self.answer(path)

    def http(self, path, method="GET", timeout=120.0):      # the TX-abort bench's http()
        st, body = self.answer(path)
        if st is None:
            raise OSError(body)
        return st, body.encode()


def run(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = fn()
        except SystemExit as e:
            rc = "SystemExit: %s" % (e.code,)
    return rc, buf.getvalue()


def e4_identity():
    for page, want, label in [
            ({"elf_sha256": SHA, "mode": 0}, SHA, "a good page names its elf_sha256"),
            (None, None, "a page that does not answer names nothing"),
            ("{cut", None, "a page that does not parse names nothing"),
            ({"mode": 0}, None, "a page with no elf_sha256 names nothing"),
            ({"elf_sha256": "abc", "mode": 0}, None, "an elf_sha256 that is not 64 hex names nothing"),
            ({"elf_sha256": SHA.upper(), "mode": 0}, None, "an upper-case elf_sha256 is not the page's form")]:
        dev = Device(page)
        E4.gi = dev.gi
        sha, why = E4.device_identity()
        check("e4 identity: %s" % label, sha == want and (why is None) == (want is not None), (sha, why))
        check("e4 identity: only /gen_inhibit asked (%s)" % label, dev.paths == ["/gen_inhibit"], dev.paths)


def e4_main():
    real = (E4.load_window, E4.run_arm, E4.attach_provenance, E4.time.sleep)
    out = os.path.join(gi_paths.scratch("device_page_reads"), "e4_record.json")
    argv = sys.argv
    try:
        E4.load_window = lambda seconds: [(0.0, 0x051, b"\x08" + bytes(7), False), (1.0, 0x100, b"\x00", False)]
        E4.run_arm = lambda *a, **k: None
        E4.attach_provenance = lambda path, rec: None
        E4.time.sleep = lambda s: None
        sys.argv = ["e4", "--arm", "idle", "--mode", "respond", "--seconds", "1", "--out", out]

        dev = Device(None)
        E4.gi = dev.gi
        rc, text = run(E4.main)
        check("e4 main: refuses when the page does not answer", rc == 2, (rc, text[-120:]))
        check("e4 main: the refusal asked only /gen_inhibit", dev.paths == ["/gen_inhibit"], dev.paths)
        check("e4 main: no record on a refusal", not os.path.exists(out))

        dev = Device({"elf_sha256": SHA, "mode": 0})
        E4.gi = dev.gi
        rc, text = run(E4.main)
        rec = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
        check("e4 main: runs through to its record", rc == 0, (rc, text[-160:]))
        check("e4 main: the record's device is the page's elf_sha256", rec.get("device") == SHA, rec.get("device"))
        check("e4 main: the image is printed", SHA in text, text[:200])
        check("e4 main: /check_status never asked", "/check_status" not in dev.paths, dev.paths)
        check("e4 main: the made-up value printed nowhere", MADE_UP not in text)
        check("e4 main: the made-up value recorded nowhere", MADE_UP not in json.dumps(rec))
    finally:
        E4.load_window, E4.run_arm, E4.attach_provenance, E4.time.sleep = real
        sys.argv = argv
        gi_paths.remove_tree(os.path.dirname(out))


def tx_main():
    argv = sys.argv
    try:
        sys.argv = ["wican_txabort_bench"]
        dev = Device({"elf_sha256": SHA, "mode": 3})
        TX.http = dev.http
        rc, text = run(TX.main)
        check("txabort main: refuses on mode 3 before anything else", str(rc).startswith("SystemExit"), (rc, text[-120:]))
        check("txabort main: asked only /gen_inhibit", dev.paths == ["/gen_inhibit"], dev.paths)
        check("txabort main: the image is printed", SHA in text, text[:200])
        check("txabort main: the made-up value printed nowhere", MADE_UP not in text)
    finally:
        sys.argv = argv
    page = {"elf_sha256": SHA, "mode": 0, "uptime_ms": 5, "sta_pass": MADE_UP}
    check("txabort device_id: elf_sha256 and nothing else", TX.device_id(page) == {"elf_sha256": SHA}, TX.device_id(page))


def named_in_code(path):
    """String constants naming /check_status, docstrings left out (comments are not in the AST)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "check_status" in n.value
            and id(n) not in docs]


def source():
    for name in ("e4_device_calibration.py", "wican_txabort_bench.py"):
        lines = named_in_code(os.path.join(SRC, name))
        check("source: %s names /check_status in no code string" % name, not lines, lines)
    probe = os.path.join(gi_paths.scratch("device_page_reads"), "probe.py")
    open(probe, "w", encoding="utf-8").write('"""/check_status in a docstring"""\nx = gi("/check_status")\n')
    check("source: the check finds a request in code (control)", named_in_code(probe) == [2], named_in_code(probe))
    gi_paths.remove_tree(os.path.dirname(probe))


def provenance():
    for mod in (E4, TX):
        check("provenance: %s is SRC's own file" % mod.__name__, from_src(mod), mod.__file__)
    other = type(sys)("elsewhere")
    other.__file__ = os.path.join(os.path.dirname(os.path.abspath(SRC)), "elsewhere", "wican_txabort_bench.py")
    check("provenance can-fail: a module from another folder is not SRC's", not from_src(other), other.__file__)


def main():
    for part in (provenance, e4_identity, e4_main, tx_main, source):
        try:
            part()
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
