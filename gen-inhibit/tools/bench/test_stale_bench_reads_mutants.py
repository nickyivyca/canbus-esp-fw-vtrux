#!/usr/bin/env python3
"""Mutants of the six moved benches' page reads; test_stale_bench_reads.py must fail on each
BY A CHECK. A crash is not a kill, and an edit that does not apply is an error. The
unmutated copy runs first as the control.

    py -3.14 -B gen-inhibit/tools/bench/test_stale_bench_reads_mutants.py

Each mutant is a copy of the seven files in the worktree's scratch/, one edit applied to one
of them, run through STALE_READS_DIR. Gen-inhibit tester, 2026-10-25.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "test_stale_bench_reads.py")
FILES = ("wican_autoarm_bench.py", "wican_keystate_bench.py", "wican_crank_request_bench.py", "wican_diag_check.py",
         "tec_busoff_sweep.py", "probe_reachability.py", "txabort_refusal_check.py")
AB, KS, CR, DC, TB, PR, TX = FILES
MUTANTS = [
    (None, None, None, None),
    ("tec_busoff_sweep.build_id reads /check_status again", TB,
     '        d = page()\n',
     '        d = json.loads(urllib.request.urlopen("http://%s/check_status" % HOST, timeout=5.0).read())\n'),
    ("tec_busoff_sweep.build_id takes any elf_sha256", TB,
     '        if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):\n'
     '            return {"elf_sha256": sha}',
     '        if sha:\n            return {"elf_sha256": sha}'),
    ("wican_autoarm_bench.build_id takes upper-case hex", AB,
     'all(c in "0123456789abcdef" for c in sha):\n        return sha',
     'all(c in "0123456789abcdefABCDEF" for c in sha):\n        return sha'),
    ("wican_autoarm_bench brackets the reset on /check_status again", AB,
     '        st, _ = gi("/gen_inhibit", timeout=2.0)', '        st, _ = gi("/check_status", timeout=2.0)'),
    ("probe_reachability probes /check_status again", PR,
     '"http://%s/gen_inhibit" % host', '"http://%s/check_status" % host'),
    ("wican_crank_request_bench prints device_id again", CR,
     'print("device elf_sha256 %s" % pre.get("elf_sha256"))', 'print("device %s" % pre.get("device_id"))'),
    ("wican_keystate_bench prints sta_mac again", KS,
     'info.get("elf_sha256", "?")', 'info.get("sta_mac", "?")'),
    ("txabort_refusal_check reads its image from /check_status again", TX,
     '        sha = get_json("/gen_inhibit").get("elf_sha256")', '        sha = get_json("/check_status").get("git_version")'),
    ("txabort_refusal_check takes any elf_sha256", TX,
     '    if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):\n        return sha',
     '    if sha:\n        return sha'),
    ("wican_diag_check reads /check_status again", DC,
     '"http://192.168.80.1/gen_inhibit"', '"http://192.168.80.1/check_status"'),
]


def main():
    base = gi_paths.scratch("mutants-stale_bench_reads")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in FILES}
    killed = errors = 0
    control_ok = False
    for n, (name, target, old, new) in enumerate(MUTANTS):
        if target and srcs[target].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[target].count(old), target))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f, s in srcs.items():
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(s.replace(old, new) if f == target else s)
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, STALE_READS_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
        if target is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s control: the unmutated copy  (exit %d, %d FAIL)" % (
                "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails)))
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    total = len(MUTANTS) - 1
    print("\n%d of %d killed by a check, %d errors; the control %s" % (
        killed, total, errors, "passes" if control_ok else "FAILS"))
    return 0 if killed == total and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
