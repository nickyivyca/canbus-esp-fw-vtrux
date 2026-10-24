"""Test of where build_output.py looks for the diag DBC and the schema doc by default.

Both live in the reverse-it project folder, not in this repo, and that folder is
somewhere else on every machine. So the defaults come from $REVERSE_IT_DIR
(projects/vtrux/ under it), and with it unset there is no default: the rows report
NO REFERENCE and say how to give one, never a pass and never a guessed path
(harness-defects row, 2026-10-24; home-path ruling B, user 2026-10-23: no literal
home path in the repo). Each case imports build_output.py afresh in a subprocess
with the variable set, unset or empty, so the module-level defaults are read as a
real run reads them.

Run:  python test/build_checks/test_reference_paths.py   (exit 0 = all pass)
      python test/build_checks/test_reference_paths.py --target <copy of build_output.py>
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []

PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import build_output as B
del B.rows[:]
B.check_diag(B.DEFAULT_DBC)
B.check_schema_doc(B.DEFAULT_SCHEMA_DOC)
B.check_fw_version(B.DEFAULT_SCHEMA_DOC)
B.check_abort_reason_parity(B.DEFAULT_DBC)
print(json.dumps({"dbc": B.DEFAULT_DBC, "doc": B.DEFAULT_SCHEMA_DOC,
                  "rows": [[r[0], r[1], r[2], r[3]] for r in B.rows]}))
"""


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def probe(target_dir, rit):
    env = {k: v for k, v in os.environ.items() if k != "REVERSE_IT_DIR"}
    if rit is not None:
        env["REVERSE_IT_DIR"] = rit
    r = subprocess.run([sys.executable, "-B", "-c", PROBE, target_dir], capture_output=True, text=True,
                       env=env, cwd=target_dir, timeout=120)
    if r.returncode != 0:
        return {"crash": (r.stdout + r.stderr)[-400:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def row(res, name):
    for r in res.get("rows", []):
        if r[0] == name:
            return r
    return None


def main():
    target = os.path.join(HERE, "build_output.py")
    if "--target" in sys.argv[1:]:
        target = os.path.abspath(sys.argv[sys.argv.index("--target") + 1])
    tdir = os.path.dirname(target)
    fake = os.path.join(os.path.abspath(os.sep), "no-such-rit", "reverse it")   # a space, as SeaDrive's has
    want_dbc = os.path.join(fake, "projects", "vtrux", "vtrux-wican-diag.dbc")
    want_doc = os.path.join(fake, "projects", "vtrux", "notes", "artifacts", "gen-inhibit", "wican_diag_schema.md")

    res = probe(tdir, fake)
    check("set: the module imports", "crash" not in res, res.get("crash"))
    check("set: the DBC default is projects/vtrux/vtrux-wican-diag.dbc under $REVERSE_IT_DIR",
          os.path.normcase(os.path.normpath(res.get("dbc") or "")) == os.path.normcase(want_dbc), res.get("dbc"))
    check("set: the schema doc default is projects/vtrux/notes/artifacts/gen-inhibit/wican_diag_schema.md under it",
          os.path.normcase(os.path.normpath(res.get("doc") or "")) == os.path.normcase(want_doc), res.get("doc"))
    r = row(res, "DBC defines every diag ID")
    check("set, but no DBC there: the DBC row is NO REFERENCE naming the path",
          r is not None and r[1] is None and r[3] and want_dbc in r[2], r)

    for label, val in (("unset", None), ("empty", ""), ("blank", "  ")):
        res = probe(tdir, val)
        check("%s: the module imports" % label, "crash" not in res, res.get("crash"))
        check("%s: no DBC default (None), not a guessed path" % label, res.get("dbc", "x") is None, res.get("dbc"))
        check("%s: no schema doc default (None)" % label, res.get("doc", "x") is None, res.get("doc"))
        for name, flag in (("DBC defines every diag ID", "--dbc"), ("abort reasons are all in the DBC", "--dbc"),
                           ("schema doc version matches", "--schema-doc"),
                           ("DIAG_FW_VERSION is the schema doc's latest rev", "--schema-doc")):
            r = row(res, name)
            check("%s: '%s' is NO REFERENCE, naming %s and REVERSE_IT_DIR" % (label, name, flag),
                  r is not None and r[1] is None and r[3] and flag in r[2] and "REVERSE_IT_DIR" in r[2], r)

    src = open(target, encoding="utf-8").read()
    bad = [m.group(0) for m in re.finditer(r"seadrive_root|nickivyc|My Libraries|NotGit|Seafile|C:[\\/]+Users", src)]
    check("no machine-specific path in build_output.py", not bad, bad)

    print("%d FAIL" % len(FAILS) if FAILS else "all ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
