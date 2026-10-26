#!/usr/bin/env python3
"""Test that the bench tools outside the suite that still asked for /check_status now read
/gen_inhibit instead (gen-inhibit reviewer, 2026-10-25, under the 2026-10-20 rule: that page
answers with the device's config, credentials included). Moved, each with a current use:
  wican_autoarm_bench.py      spec 12.4's auto-arm row (gate, live, condition 5): build_id()
                              and the reset-bracketing probes
  wican_keystate_bench.py     the only hardware cover of spec 7.1 (it printed sta_mac)
  wican_crank_request_bench.py spec 4.1's held 0x08 (it printed device_id, the unit's MAC)
  wican_diag_check.py         the diag pages on the bench (diag_check_rules.py's rules)
  tec_busoff_sweep.py         its build_id(); its judge_bus_on() is B8's
  probe_reachability.py       the httpd-vs-socket probe
  txabort_refusal_check.py    the over-long-argument refusal check (reviewer, 2026-10-25: its
                              only use of /check_status was the build id)
batt_voltage_log.py is parked instead (test_batt_voltage_log.py); wican_inhibit_bench.py and
wican_timing_bench.py are retired (reviewer, 2026-10-25).

urllib.request.urlopen is faked: /gen_inhibit answers a page, /check_status a made-up value,
so a request for it shows in the URLs asked for and in what comes back.
  behaviour  tec_busoff_sweep.build_id(), wican_autoarm_bench.build_id(),
             txabort_refusal_check.device_image() (a good page, no
             answer, no elf_sha256, a short or upper-case one) and probe_reachability.http_up()
             ask only /gen_inhibit and never return the made-up value
  source     no string constant outside a docstring names /check_status in any of the six
             (a control finds one in a probe file); none names "device_id" or "sta_mac"
             (seven files with txabort_refusal_check)

    py -3.14 -B tools/gen_inhibit/bench/test_stale_bench_reads.py
Mutants: test_stale_bench_reads_mutants.py (STALE_READS_DIR runs another copy of the six).
Gen-inhibit tester, 2026-10-25.
"""
import ast
import importlib.util
import io
import json
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("STALE_READS_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths      # noqa: E402

MOVED = ("wican_autoarm_bench.py", "wican_keystate_bench.py", "wican_crank_request_bench.py", "wican_diag_check.py",
         "tec_busoff_sweep.py", "probe_reachability.py", "txabort_refusal_check.py")
SCRATCH = gi_paths.scratch("stale-bench-reads")
MADE_UP = "made-up-value-not-a-credential"
SHA = "3639f8b2" + "c0" * 28
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


class Answer(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Device:
    def __init__(self, page):
        self.page, self.urls = page, []

    def urlopen(self, req, timeout=None, **kw):
        url = req.full_url if hasattr(req, "full_url") else req
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else url
        self.urls.append(path.split("?")[0])
        if path.startswith("/check_status"):
            return Answer(json.dumps({"git_version": MADE_UP, "device_id": MADE_UP, "sta_pass": MADE_UP}).encode())
        if path.startswith("/gen_inhibit"):
            if self.page is None:
                raise OSError("timed out")
            return Answer(json.dumps(self.page).encode())
        raise OSError("404")


def load(name):
    spec = importlib.util.spec_from_file_location("stale_" + name[:-3], os.path.join(SRC, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PAGES = [
    ("a good page", {"elf_sha256": SHA, "mode": 0}, SHA),
    ("no answer", None, None),
    ("no elf_sha256", {"mode": 0}, None),
    ("a short elf_sha256", {"elf_sha256": SHA[:40], "mode": 0}, None),
    ("an upper-case elf_sha256", {"elf_sha256": SHA.upper(), "mode": 0}, None)]


def behaviour():
    real = urllib.request.urlopen
    try:
        TB = load("tec_busoff_sweep.py")
        AB = load("wican_autoarm_bench.py")
        PR = load("probe_reachability.py")
        TX = load("txabort_refusal_check.py")
        for label, page, want in PAGES:
            dev = Device(page)
            urllib.request.urlopen = dev.urlopen
            got = TB.build_id()
            check("tec_busoff_sweep.build_id, %s" % label, got == ({"elf_sha256": want} if want else None), got)
            check("tec_busoff_sweep.build_id asks only /gen_inhibit (%s)" % label, set(dev.urls) == {"/gen_inhibit"},
                  dev.urls)
            dev = Device(page)
            urllib.request.urlopen = dev.urlopen
            got = AB.build_id()
            check("wican_autoarm_bench.build_id, %s" % label, got == want, got)
            check("wican_autoarm_bench.build_id asks only /gen_inhibit (%s)" % label, set(dev.urls) == {"/gen_inhibit"},
                  dev.urls)
            check("wican_autoarm_bench.build_id: the made-up value nowhere (%s)" % label, MADE_UP not in str(got))
        for label, page, want in PAGES:
            dev = Device(page)
            urllib.request.urlopen = dev.urlopen
            got = TX.device_image()
            check("txabort_refusal_check.device_image, %s" % label, got == want, got)
            check("txabort_refusal_check.device_image asks only /gen_inhibit (%s)" % label,
                  set(dev.urls) == {"/gen_inhibit"}, dev.urls)
        dev = Device({"elf_sha256": SHA})
        urllib.request.urlopen = dev.urlopen
        up = PR.http_up("192.168.80.1", 1.0)
        check("probe_reachability.http_up: up on /gen_inhibit", up[0] is True, up)
        check("probe_reachability.http_up asks only /gen_inhibit", dev.urls == ["/gen_inhibit"], dev.urls)
    finally:
        urllib.request.urlopen = real


def strings_in_code(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def source():
    for name in MOVED:
        path = os.path.join(SRC, name)
        strs = strings_in_code(path)
        hits = [ln for ln, v in strs if "check_status" in v]
        check("source: %s names /check_status in no code string" % name, not hits, hits)
        keys = [ln for ln, v in strs if v in ("device_id", "sta_mac")]
        check("source: %s reads no device_id or sta_mac key" % name, not keys, keys)
    os.makedirs(SCRATCH, exist_ok=True)
    probe = os.path.join(SCRATCH, "probe.py")
    open(probe, "w", encoding="utf-8").write('"""/check_status in a docstring"""\nx = get("/check_status")\n')
    check("source: the check finds a request in code (control)",
          [ln for ln, v in strings_in_code(probe) if "check_status" in v] == [2], strings_in_code(probe))


def main():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    for part in (behaviour, source):
        try:
            part()
        except Exception as e:      # noqa: BLE001  a crash is this part's FAIL
            check("%s ran without raising" % part.__name__, False, "%s: %s" % (type(e).__name__, e))
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n%s, %d FAIL" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
