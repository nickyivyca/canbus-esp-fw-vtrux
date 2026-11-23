"""Test of build_output.py's spec 12.3 item 3 diag rows (stage (b) batch 2, 2026-11-23).

  check_diag_table()   the committed tables (firmware/main/gi_diag_table.c/.h,
                       tools/gi_diag_table.py) regenerated from the DBC byte for byte
                       with the pinned shared/diag generator; the DBC's DiagSchemaHash its
                       own layout hash (shared/diag D3-D5)
  _decode_emitted()    its schema 9 row: 0x7F1 B1 and 0x7F3 B4-B5, the retired
                       revision's bytes, sent 0 in the frames the host build emitted
                       (spec 10)

It replaces test_fw_version_row.py, whose row schema 9 retired.

The real case runs the row on this worktree: HEAD's DBC from git (refs_from_git) and
the committed tables, which must pass. The rest run it on a fixture, the tables
generated here from that DBC: identical passes; one byte changed in each of the three
files fails naming that file; a DBC whose layout moved (a signal one bit shorter, its
hash restated) fails the table row, its hash row still passing; a DBC with a stale
DiagSchemaHash fails the hash row; a missing committed file fails; no generator is NO
REF, never a pass. The reserved row: a schema 9 golden with both bytes 0 passes, either
byte not 0 fails, and goldens with no schema 9 page are NO REF.

Run:  python3.13 -B gen-inhibit/test/build_checks/test_diag_table_row.py   (exit 0 = all pass)
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_output as B                                        # noqa: E402

sys.path.insert(0, os.path.join(B.PROJECT, "tools", "bench"))
import gi_diag  # noqa: E402
import gi_paths  # noqa: E402  remove_tree (AGENTS.md "Removing scratch")

TMP = os.path.join(B.WORKTREE, "scratch", "build-checks", "test_diag_table_row-%d" % os.getpid())
FAILS = []


def mark(r):
    _name, ok, _detail, ref_missing = r
    if ok is None or ref_missing:
        return "NO REF"
    return "ok" if ok else "FAIL"


def rows_of(fn, *a, **kw):
    del B.rows[:]
    fn(*a, **kw)
    return {r[0]: (mark(r), r[2]) for r in B.rows}


def check(label, got, want):
    ok = got == want
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", label, "" if ok else "  -- want %s, got %s" % (want, got)))
    if not ok:
        FAILS.append(label)


def generate(gen, dbc, out):
    os.makedirs(out, exist_ok=True)
    r = subprocess.run([sys.executable, "-B", gen, dbc, "--prefix", B.TABLE_PREFIX, "--c-header",
                        "gi_diag_table.h", "--c-source", "gi_diag_table.c", "--python", "gi_diag_table.py"],
                       cwd=out, capture_output=True, text=True,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if r.returncode:
        raise SystemExit("fixture generation failed: %s" % r.stderr)
    return {n: os.path.join(out, n) for n in ("gi_diag_table.h", "gi_diag_table.c", "gi_diag_table.py")}


def restate_hash(text):
    D = gi_diag.core()
    v, h = D.schema_identity(D.load(text=text))
    return text.replace('BA_ "DiagSchemaHash" "%s";' % h,
                        'BA_ "DiagSchemaHash" "%s";' % D.layout_hash(D.load(text=text)))


def table_cases(dbc, gen):
    T, H = B.TABLE_ROW, B.HASH_ROW
    r = rows_of(B.check_diag_table, dbc)
    check("the real tables against HEAD's DBC, the pinned generator: both rows pass",
          (r[T][0], r[H][0]), ("ok", "ok"))
    print("       %s" % r[T][1])

    base = generate(gen, dbc, os.path.join(TMP, "base"))
    r = rows_of(B.check_diag_table, dbc, committed=base, gen=gen)
    check("fixture tables generated from the DBC: pass", r[T][0], "ok")

    for name in sorted(base):
        d = os.path.join(TMP, "one-byte-" + name.replace(".", "_"))
        shutil.copytree(os.path.join(TMP, "base"), d)
        p = os.path.join(d, name)
        b = bytearray(open(p, "rb").read())
        k = b.rindex(b"8")              # a digit in the last signal's row or constant
        b[k:k + 1] = b"9"
        open(p, "wb").write(bytes(b))
        r = rows_of(B.check_diag_table, dbc, committed={n: os.path.join(d, n) for n in base}, gen=gen)
        check("one byte changed in %s: FAIL naming it" % name,
              (r[T][0], name in r[T][1]), ("FAIL", True))

    d = os.path.join(TMP, "crlf")
    shutil.copytree(os.path.join(TMP, "base"), d)
    p = os.path.join(d, "gi_diag_table.c")
    crlf = open(p, "rb").read().replace(b"\n", b"\r\n")     # read before the "wb" truncates
    open(p, "wb").write(crlf)
    r = rows_of(B.check_diag_table, dbc, committed={n: os.path.join(d, n) for n in base}, gen=gen)
    check("the C table with CRLF line ends: FAIL (byte for byte)", r[T][0], "FAIL")

    text = open(dbc, encoding="utf-8").read()
    moved = os.path.join(TMP, "moved", os.path.basename(dbc))
    os.makedirs(os.path.dirname(moved))
    narrowed = text.replace(" SG_ diag_ctr_bad : 40|8@1+", " SG_ diag_ctr_bad : 40|7@1+")
    if narrowed == text:
        raise SystemExit("fixture: diag_ctr_bad's line not found in the DBC")
    open(moved, "w", encoding="utf-8", newline="").write(restate_hash(narrowed))
    r = rows_of(B.check_diag_table, moved, committed=base, gen=gen)
    check("the DBC's layout moved (diag_ctr_bad 8 -> 7 bits, hash restated): table FAIL, hash ok",
          (r[T][0], r[H][0]), ("FAIL", "ok"))

    stale = os.path.join(TMP, "stale", os.path.basename(dbc))
    os.makedirs(os.path.dirname(stale))
    open(stale, "w", encoding="utf-8", newline="").write(narrowed)
    r = rows_of(B.check_diag_table, stale, committed=base, gen=gen)
    check("a DBC whose DiagSchemaHash is not its layout hash: hash FAIL", r[H][0], "FAIL")

    r = rows_of(B.check_diag_table, dbc, committed=dict(base, **{"gi_diag_table.py": os.path.join(TMP, "none.py")}),
                gen=gen)
    check("a committed table missing: FAIL", r[T][0], "FAIL")

    r = rows_of(B.check_diag_table, dbc, committed=base, gen=os.path.join(TMP, "no_generator.py"))
    check("no generator: NO REF, not a pass", r[T][0], "NO REF")

    r = rows_of(B.check_diag_table, os.path.join(TMP, "no.dbc"), committed=base, gen=gen)
    check("no DBC: both rows NO REF", (r[T][0], r[H][0]), ("NO REF", "NO REF"))


def reserved_cases():
    """_decode_emitted's schema 9 row, on goldens written here (B.PROJECT pointed at them)."""
    R = B.RESERVED_ROW

    def run(traces):
        proj = os.path.join(TMP, "proj-%d" % len(os.listdir(TMP)))
        gold = os.path.join(proj, "test", "gen_inhibit_host", "golden")
        os.makedirs(gold)
        for i, t in enumerate(traces):
            open(os.path.join(gold, "t%d.trace" % i), "w").write(t)
        saved = B.PROJECT
        B.PROJECT = proj
        try:
            return rows_of(B._decode_emitted, B.DIAG_REF["dbc"])
        finally:
            B.PROJECT = saved

    def tr(st, bd):
        return ("0 TX DIAG id=7F1 dlc=8 data=%s ok=1\n240000 TX DIAG id=7F3 dlc=8 data=%s ok=1\n" % (st, bd))

    good = tr("0900030600FF0000", "0000000000000900")
    check("schema 9, both bytes 0: ok", run([good])[R][0], "ok")
    check("schema 9, 0x7F1 B1 = 7: FAIL", run([tr("0907030600FF0000", "0000000000000900")])[R][0], "FAIL")
    check("schema 9, 0x7F3 B4 = 7: FAIL", run([tr("0900030600FF0000", "0000000007000900")])[R][0], "FAIL")
    check("schema 8 only (the revision there): NO REF", run([tr("0807030600FF0000", "0000000007000800")])[R][0],
          "NO REF")


def main():
    gi_paths.remove_tree(TMP)
    os.makedirs(TMP)
    try:
        del B.rows[:]
        dbc, _doc = B.refs_from_git(B.WORKTREE)
        if not dbc:
            print("no DBC from git: %s" % B.rows)
            return 2
        gen = os.path.join(B._diag_host().diag_dir(), "tools", "diag_table_gen.py")
        table_cases(dbc, gen)
        reserved_cases()
    finally:
        gi_paths.remove_tree(TMP)
    print("\n=== %s ===" % ("PASS" if not FAILS else "FAIL: %d" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
