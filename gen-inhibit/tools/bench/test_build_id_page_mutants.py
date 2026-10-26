#!/usr/bin/env python3
"""Mutants of lowid_flood_skip_bench.build_id() and its callers' image reads;
test_build_id_page.py must fail on each BY A CHECK. A crash is not a kill, and an edit
that does not apply is an error. The unmutated copy runs first as the control.

    py -3.14 -B gen-inhibit/tools/bench/test_build_id_page_mutants.py

Each mutant is a copy of every non-test .py in this folder but gi_paths.py, in the
worktree's scratch/, one edit applied to one file, run through BUILD_ID_DIR. Gen-inhibit tester, 2026-10-25.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "test_build_id_page.py")
LF, RU, RM = "lowid_flood_skip_bench.py", "restore_unarmed_check.py", "reset_matrix.py"
PO, IB = "page_only_baseline.py", "interlock_bench.py"
BODY = '    p = page()\n    sha = p.get("elf_sha256") if isinstance(p, dict) else None\n'
MUTANTS = [
    (None, None, None, None),     # the control: no edit
    ("build_id reads /check_status, as before", LF, BODY,
     '    p, _ = json.JSONDecoder().raw_decode(dev_get("/check_status").lstrip())\n'
     '    sha = p.get("elf_sha256") if isinstance(p, dict) else None\n'),
    ("build_id asks /check_status first, by a built path", LF, BODY, '    dev_get("/check_" + "status")\n' + BODY),
    ("build_id takes any elf_sha256", LF,
     '    if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):',
     "    if sha:"),
    ("build_id takes upper-case hex", LF, 'all(c in "0123456789abcdef" for c in sha)',
     'all(c in "0123456789abcdefABCDEF" for c in sha)'),
    ("build_id takes a page that is not an object", LF,
     '    sha = p.get("elf_sha256") if isinstance(p, dict) else None\n', '    sha = p.get("elf_sha256") if p else None\n'),
    ("build_id returns the whole page", LF, '        return {"elf_sha256": sha}', "        return dict(p)"),
    ("restore page_state keeps build_id's git_version", RU, '"elf_sha256": p.get("elf_sha256"),',
     '"elf_sha256": (LF.build_id() or {}).get("git_version"),'),
    ("restore judge takes a short expectation", RU, "        if not (len(expect) >= 8 and sha.startswith(expect.lower())):",
     "        if not sha.startswith(expect.lower()):"),
    ("reset_matrix matches as --expect-git did", RM, "    return len(expect) >= 8 and sha.startswith(expect)",
     "    return expect in json.dumps(build)"),
    ("reset_matrix does not fold case", RM, "    expect = expect.lower()\n", ""),
    ("page_only_baseline reads /check_status again", PO,
     '        rec["build_start"] = {"wall": stamp, "elf_sha256": (LF.build_id() or {}).get("elf_sha256")}',
     '        rec["build_start"] = {"wall": stamp, "git_version": LF.dev_get("/check_status")}'),
    ("interlock_bench records /check_status", IB, '"build": LF.build_id(),', '"build": LF.dev_get("/check_status"),'),
]


def main():
    base = gi_paths.scratch("mutants-build_id_page")
    # gi_paths stays this folder's: a copy would look for pins.json beside itself
    files = [f for f in os.listdir(HERE) if f.endswith(".py") and not f.startswith("test_") and f != "gi_paths.py"]
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in files}
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
        r = subprocess.run([sys.executable, "-B", TEST], env=dict(os.environ, BUILD_ID_DIR=d),
                           capture_output=True, text=True, timeout=300)
        fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
        if target is None:
            control_ok = r.returncode == 0 and not fails and not crashed
            print("%s control: the unmutated copy  (exit %d, %d FAIL)" % ("PASS    " if control_ok else "FAILED  ",
                                                                          r.returncode, len(fails)))
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
