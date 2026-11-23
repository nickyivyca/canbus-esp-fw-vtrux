#!/usr/bin/env python3
"""verify_diag.py's can-fails: one-line defects planted in a copy of shared/diag, each of
which verify_diag.py must fail (gen-inhibit tester, 2026-11-23).

Each mutant copies this shared/diag (its code, include/, python/, tools/) to the worktree's
scratch/diag-mutants/<pid>/m<n>/, applies one edit, and runs verify_diag.py --diag on the
copy, the fixture only. KILLED is exit 1 with a FAIL line; anything else SURVIVED. First, an
unedited copy must pass (the control). The product's text is read only to plant the edits.

    python3.13 -B shared/diag/test/mutate.py [--n N] [--only K]

Exit 0 when the control passes and every mutant is killed, else 1.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIAG = os.path.dirname(HERE)
WORKTREE = os.path.dirname(os.path.dirname(DIAG))

# (file under shared/diag, what it breaks, old text, new text)
MUTANTS = [
    # the C packer (D3)
    ("diag_pack.c", "a value at its maximum taken as a misfit",
     "if (raw < diag_signal_min(s) || raw > diag_signal_max(s))",
     "if (raw < diag_signal_min(s) || raw >= diag_signal_max(s))"),
    ("diag_pack.c", "no misfit check: a value that does not fit is truncated into the frame",
     "    if (raw < diag_signal_min(s) || raw > diag_signal_max(s))\n    {\n        return DIAG_PACK_MISFIT;\n    }\n",
     ""),
    ("diag_pack.c", "Motorola written LSB first",
     "const unsigned bit = s->big_endian ? (s->length - 1 - n) : n;", "const unsigned bit = n;"),
    ("diag_pack.c", "Motorola's next byte one bit off",
     "return (pos % 8 == 0) ? pos + 15 : pos - 1;", "return (pos % 8 == 0) ? pos + 14 : pos - 1;"),
    ("diag_pack.c", "a signed signal's minimum one too high",
     "return -((int64_t)1 << (s->length - 1));", "return -((int64_t)1 << (s->length - 1)) + 1;"),
    ("diag_pack.c", "an unsigned signal's maximum one too high",
     "((int64_t)1 << s->length) - 1;", "((int64_t)1 << s->length);"),
    ("diag_pack.c", "the frame not zeroed first",
     "    memset(out, 0, m->dlc <= DIAG_MAX_DLC ? m->dlc : DIAG_MAX_DLC);\n", ""),
    ("diag_pack.c", "the misfit mask's bit is the count, not the signal",
     "*misfits |= (uint64_t)1 << i;", "*misfits |= (uint64_t)1 << n;"),
    ("diag_pack.c", "a misfit not counted", "            n++;\n", ""),
    ("diag_pack.c", "every multiplexed page packed whatever the selector",
     "(s->mux >= 0 && s->mux != sel_value)", "(s->mux >= 0 && 0)"),
    ("diag_pack.c", "a selector that does not fit packs on as page 0",
     "            memset(out, 0, m->dlc);\n            return -1;", "            sel_value = 0;"),
    # the Python twin (D4)
    ("python/diag_pack.py", "the twin writes Motorola LSB first",
     'bit = (n - 1 - i) if s["big_endian"] else i', "bit = i"),
    ("python/diag_pack.py", "the twin takes a value at its maximum as a misfit",
     "if raw < signal_min(s) or raw > signal_max(s):", "if raw < signal_min(s) or raw >= signal_max(s):"),
    ("python/diag_pack.py", "the twin's mask bit is the count", "misfits |= 1 << i", "misfits |= 1 << n"),
    ("python/diag_pack.py", "the twin packs every page",
     'if i == sel or (s["mux"] >= 0 and s["mux"] != sel_value):', "if i == sel:"),
    ("python/diag_pack.py", "the twin's signed minimum one too high",
     'return -(1 << (s["length"] - 1))', 'return -(1 << (s["length"] - 1)) + 1'),
    # the generator (D3, D4)
    ("tools/diag_table_gen.py", "byte order inverted",
     'be = s.byte_order == "big_endian"', 'be = s.byte_order != "big_endian"'),
    ("tools/diag_table_gen.py", "signedness dropped",
     '"is_signed": bool(s.is_signed)', '"is_signed": False'),
    ("tools/diag_table_gen.py", "a multiplexed signal under the next page",
     "mux = int(s.multiplexer_ids[0])", "mux = int(s.multiplexer_ids[0]) + 1"),
    ("tools/diag_table_gen.py", "__BITS one too many",
     '"#define %s_%s__%s__BITS %d\\n" % (prefix, m["name"], s["name"], s["length"])',
     '"#define %s_%s__%s__BITS %d\\n" % (prefix, m["name"], s["name"], s["length"] + 1)'),
    ("tools/diag_table_gen.py", "__SIGNED always 0", '1 if s["is_signed"] else 0))', "0))"),
    ("tools/diag_table_gen.py", "the C table's start bit one off",
     's["name"], s["start"], s["length"], "true" if s["big_endian"] else "false",',
     's["name"], s["start"] + 1, s["length"], "true" if s["big_endian"] else "false",'),
    ("tools/diag_table_gen.py", "the Python table's signedness dropped",
     '% (s["name"], s["start"], s["length"], s["big_endian"], s["is_signed"], s["mux"]))',
     '% (s["name"], s["start"], s["length"], s["big_endian"], False, s["mux"]))'),
    ("tools/diag_table_gen.py", "__ID one off",
     '"#define %s_%s__ID 0x%Xu\\n" % (prefix, m["name"], m["id"])',
     '"#define %s_%s__ID 0x%Xu\\n" % (prefix, m["name"], m["id"] + 1)'),
    ("tools/diag_table_gen.py", "the C table's extended flag always false",
     'm["name"], m["id"], "true" if m["extended"] else "false", m["dlc"],',
     'm["name"], m["id"], "false", m["dlc"],'),
    # the build id (D2)
    ("include/diag_build_id.h", "the build id's bytes reversed",
     "return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) |\n           ((uint32_t)b[2] << 8)  |  (uint32_t)b[3];",
     "return ((uint32_t)b[3] << 24) | ((uint32_t)b[2] << 16) |\n           ((uint32_t)b[1] << 8)  |  (uint32_t)b[0];"),
    ("diag_build_id.c", "diag_build_id() reads from the hash's second byte",
     "esp_app_get_description()->app_elf_sha256)", "esp_app_get_description()->app_elf_sha256 + 1)"),
]


def copy_diag(dst):
    shutil.copytree(DIAG, dst, ignore=shutil.ignore_patterns("test", "unit", "__pycache__", "*.pyc"))
    for d, _s, fs in os.walk(dst):      # the source may be a read-only checkout
        os.chmod(d, 0o755)
        for f in fs:
            os.chmod(os.path.join(d, f), 0o644)


def run(diag, n):
    r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "verify_diag.py"), "--diag", diag, "--no-git",
                        "--n", str(n)], capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL ")]
    return r.returncode, fails, r.stdout + r.stderr


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--only", type=int, default=0)
    a = ap.parse_args(argv)
    base = os.path.join(WORKTREE, "scratch", "diag-mutants", str(os.getpid()))
    if os.path.exists(base):
        shutil.rmtree(base)
    os.makedirs(base)
    bad = 0
    try:
        c = os.path.join(base, "control", "diag")
        copy_diag(c)
        rc, fails, out = run(c, a.n)
        ok = rc == 0 and not fails
        print("%s control: an unedited copy passes%s" % ("ok      " if ok else "FAILED  ", "" if ok else "\n" + out[-800:]))
        bad += not ok
        for k, (f, name, old, new) in enumerate(MUTANTS, 1):
            if a.only and k != a.only:
                continue
            d = os.path.join(base, "m%d" % k, "diag")
            copy_diag(d)
            p = os.path.join(d, *f.split("/"))
            text = open(p, encoding="utf-8").read()
            if text.count(old) != 1:
                print("ERROR    m%d %s: the edit matched %d times" % (k, name, text.count(old)))
                bad += 1
                continue
            open(p, "w", encoding="utf-8", newline="").write(text.replace(old, new))
            rc, fails, out = run(d, a.n)
            killed = rc == 1 and bool(fails)
            bad += not killed
            print("%s m%d %s (%s)%s" % ("KILLED  " if killed else "SURVIVED", k, name, f,
                                        "".join("\n           " + x[:150] for x in fails[:2])
                                        if killed else "\n           exit %d: %s" % (rc, out[-300:])))
    finally:
        shutil.rmtree(base)
        parent = os.path.dirname(base)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    n = len(MUTANTS) if not a.only else 1
    print("\n%s" % ("PASS: the control passes and %d of %d mutants are killed" % (n, n) if not bad
                    else "FAIL: %d problem(s)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
