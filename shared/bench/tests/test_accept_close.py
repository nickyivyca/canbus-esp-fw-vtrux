"""Tests of the acceptance record's close (vtrux_bench/accept/close.py):
ACCEPT-SPEC A13 and A14. No hardware. Each test names what it checks.

    python -B shared/bench/tests/test_accept_close.py            every test
    python -B shared/bench/tests/test_accept_close.py --can-fail  the control
        first (every test unchanged, which must pass), then each test again
        with a rule it guards switched off (close.RULES; every rule has one)

Each test opens a record (record.start) in this run's own folder under the
worktree's scratch/ (tests/_scratch.py), with a made-up summary of a run,
and closes it; the folder is removed at the end with vtrux_bench/scratch.py.
"""

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import close as CL           # noqa: E402
from vtrux_bench.accept import judge as J            # noqa: E402
from vtrux_bench.accept import record as R           # noqa: E402

BASE = _scratch.run_dir("accept_close")
N = [0]
C1 = "1" * 40
C2 = "2" * 40


class Adapter:
    """What close() asks an adapter for: the identifier scan, and the
    artifacts README row."""
    PROJECT = "proj"

    def __init__(self, hits=None, scan=True, row_fails=False, scan_fails=False):
        self.hits, self.rows, self.row_fails = hits or [], [], row_fails
        self.scan_fails = scan_fails
        if not scan:
            self.identifier_scan = None

    def identifier_scan(self, folder):
        if self.scan_fails:
            raise RuntimeError("the value file is missing")
        return list(self.hits)

    def artifacts_row(self, folder, summary):
        if self.row_fails:
            raise OSError("the artifacts README is locked")
        self.rows.append((folder, summary["verdict"]["lines"][0]))
        return "row for %s" % os.path.basename(folder)


class FakeMade:
    """A Made whose removal reports one LEFTOVER, and notes whether the
    record's README was already there when it was called."""

    def __init__(self, rec_path, left=None):
        self.rec_path, self.left, self.readme_then = rec_path, left or [], None

    def remove_all(self, out=None):
        self.readme_then = os.path.exists(os.path.join(self.rec_path, "README.md"))
        for p, why in self.left:
            (out or print)("scratch: LEFTOVER %s -- %s" % (p, why))
        return list(self.left)


# the suite run's results that are not a pass: nine, past the item detail's six
NOT_PASSED = [("a/k", "KNOWN-FAIL")] + [("a/f%d" % i, "FAIL") for i in range(7)] + \
    [("b/last", "ERROR")]


def summary(rec):
    items = [J.item("check", "env", "PASS", "PASS"),
             J.item("suite_run", "emu", "PASS", "FAIL"),
             J.item("compare", "same", "held", "held"),
             J.item("canfail", "cf", "as stated", "as stated"),
             J.item("build", "fw", "PASS", "PASS")]
    items[0]["not_passed"] = []
    items[1]["not_passed"] = list(NOT_PASSED)
    v = J.verdict(items, [dict(id="digest", held=True, why="equal", items=[])])
    return {
        "step": {"id": "s1", "project": "proj", "ordered_by": "reviewer",
                 "ordered_on": "2026-11-01", "spec": ["9.1"], "verification": "9.1 Emulation"},
        "list": {"commit": C1, "path": "proj/acceptance/s1.toml", "sha256": "ab" * 32},
        "runner": {"commit": C2, "dirty": False},
        "trees": {"base": {"project": {"commit": C1, "tree_id": "3" * 40},
                           "shared": {"comp": {"commit": C2, "tree_id": "4" * 40}},
                           "product_digest": "5" * 64}},
        "builds": {"fw": {"verdict": "PASS", "variant": "man", "toolchain": "fake-cc 1.0",
                          "images": {"app": {"sha256": "6" * 64}}, "elf": {"sha256": "7" * 64}}},
        "compares": [{"id": "same", "expect": "identical", "held": True, "counted": []}],
        "premises": [{"id": "digest", "held": True, "why": "equal", "items": []}],
        "reruns": {"emu": [{"check": "a/flaky", "first": "FAIL", "rerun": "PASS"}]},
        "verdict": v,
        "earlier": None,
    }


def opened():
    N[0] += 1
    runs = os.path.join(BASE, "runs%d" % N[0])
    os.makedirs(runs)        # so git's search starts below BASE, the ceiling
    said = []
    rec = R.start(runs, "s1", out=said.append, ceiling=BASE)
    with open(os.path.join(rec.path, "note.txt"), "w") as f:
        f.write("a file the run wrote\n")
    return rec, said


def read(rec, name):
    with open(os.path.join(rec.path, name), encoding="utf-8") as f:
        return f.read()


def sums_ok(rec):
    """Every file in the folder but SHA256SUMS is listed with its sha256,
    and nothing else is."""
    listed = {}
    for ln in read(rec, "SHA256SUMS").splitlines():
        h, _, p = ln.partition("  ")
        listed[p] = h
    have = {}
    for d, _dirs, files in os.walk(rec.path):
        for n in files:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, rec.path).replace("\\", "/")
            if rel != "SHA256SUMS":
                with open(p, "rb") as f:
                    have[rel] = hashlib.sha256(f.read()).hexdigest()
    return listed == have, listed, have


# ---- the tests --------------------------------------------------------------
def test_readme():
    """A13: the README names the step, the list's commit and sha256, the
    runner's commit, the trees (commits, product digest), the builds (image
    and ELF sha256), every item with its status, the comparisons, the
    premises, and the verdict."""
    rec, _said = opened()
    s = summary(rec)
    CL.close(rec, s, S.Made(), Adapter())
    text = read(rec, "README.md")
    for want in ("s1", C1, "ab" * 32, C2, "5" * 64, "6" * 64, "7" * 64, "ACCEPTANCE FAIL",
                 "suite_run emu", "compare same", "premise digest", "9.1 Emulation"):
        assert want in text, (want, text)


def test_readme_not_passed():
    """A13 (the user, 2026-11-02, through the charge-interposer reviewer):
    the README lists every result of each check and suite run that is not a
    plain PASS, all of them, past the six of the item's detail, in order,
    and says "none" for one that has none."""
    rec, _said = opened()
    CL.close(rec, summary(rec), S.Made(), Adapter())
    text = read(rec, "README.md")
    head = "## Results that are not a pass"
    assert head in text, text
    sec = text.split(head, 1)[1].split("\n## ", 1)[0]
    want = ["- suite_run emu: %d" % len(NOT_PASSED)] + \
        ["  - %s %s" % g for g in NOT_PASSED]
    lines = [ln for ln in sec.splitlines() if ln.startswith(("- ", "  - "))]
    assert "- check env: none" in lines, sec
    i = lines.index(want[0])
    assert lines[i:i + len(want)] == want, sec


def test_sha256sums():
    """A13: SHA256SUMS lists every file in the record with its sha256 (the
    console, the README, the run's own files), and nothing else."""
    rec, _said = opened()
    CL.close(rec, summary(rec), S.Made(), Adapter())
    ok, listed, have = sums_ok(rec)
    assert ok, (listed, have)
    assert {"README.md", "console.txt", "note.txt", "verification_skeleton.md"} <= set(listed), listed


def test_closed_once():
    """A13: the record is closed once: after the close nothing more is
    written into it (the console stops), and a second close is refused."""
    rec, said = opened()
    CL.close(rec, summary(rec), S.Made(), Adapter())
    rec.say("a line after the close")
    assert said[-1] == "a line after the close", "the line was not printed"
    assert sums_ok(rec)[0], "the folder changed after the close"
    try:
        CL.close(rec, summary(rec), S.Made(), Adapter())
    except CL.CloseError:
        return
    raise AssertionError("a second close was taken")


def test_identifiers_reported():
    """A13: before the close the identifier scan runs over the folder and
    every hit is recorded, without refusing to close (identifiers may stay
    in SeaDrive run records, never in git); an adapter with no scan is
    recorded as such, never passed over."""
    rec, _said = opened()
    CL.close(rec, summary(rec), S.Made(), Adapter(hits=["note.txt:1 MAC <masked>"]))
    text = read(rec, "README.md")
    assert "note.txt:1 MAC <masked>" in text, text
    assert os.path.exists(os.path.join(rec.path, "SHA256SUMS")), "the close was refused"
    rec2, _s = opened()
    CL.close(rec2, summary(rec2), S.Made(), Adapter(scan=False))
    assert "no identifier scan" in read(rec2, "README.md"), read(rec2, "README.md")
    # a scan that fails is recorded as failed, and the close still happens
    rec3, _s = opened()
    CL.close(rec3, summary(rec3), S.Made(), Adapter(scan_fails=True))
    assert "identifier scan FAILED: RuntimeError: the value file is missing" in \
        read(rec3, "README.md"), read(rec3, "README.md")
    assert os.path.exists(os.path.join(rec3.path, "SHA256SUMS")), "the close did not happen"


def test_cleanup_first():
    """A14: the run's cleanup comes before the close, so whatever it cannot
    remove is a LEFTOVER line in the record's README."""
    rec, _said = opened()
    made = FakeMade(rec.path, left=[("C:/x/tree-base-1/.git/objects/ab/cd", "PermissionError")])
    CL.close(rec, summary(rec), made, Adapter())
    assert made.readme_then is False, "the cleanup ran after the README was written"
    text = read(rec, "README.md")
    assert "LEFTOVER C:/x/tree-base-1/.git/objects/ab/cd" in text, text


def test_late_failure():
    """A14: a failure after the close (the artifacts README row) is printed
    and reported, never written into the closed record."""
    rec, said = opened()
    res = CL.close(rec, summary(rec), S.Made(), Adapter(row_fails=True))
    assert res["late"] and "locked" in res["late"][0], res
    assert any("after the close" in s and "locked" in s for s in said), said
    assert sums_ok(rec)[0], "the late failure was written into the record"
    assert "locked" not in read(rec, "README.md")


def test_artifacts_row():
    """A13: the close adds the record's row to the project's artifacts
    README, through the adapter, after the folder is closed."""
    rec, _said = opened()
    ad = Adapter()
    res = CL.close(rec, summary(rec), S.Made(), ad)
    assert ad.rows and ad.rows[0][0] == rec.path, ad.rows
    assert res["row"] == "row for %s" % rec.id, res


def test_skeleton():
    """A13: a fact skeleton for the VERIFICATION.md entry, which the tester
    still writes: the section, the commits, the verdict, and each flagged
    case's first and re-run verdicts side by side."""
    rec, _said = opened()
    CL.close(rec, summary(rec), S.Made(), Adapter())
    sk = read(rec, "verification_skeleton.md")
    for want in ("9.1 Emulation", C1, C2, "ACCEPTANCE FAIL", "a/flaky", "first FAIL",
                 "re-run PASS", rec.id, "the tester writes the entry"):
        assert want in sk, (want, sk)


def test_dev_skeleton():
    """gen-inhibit's reviewer (2026-11-01): a --dev run's skeleton opens
    "DEV RUN, not evidence", so nothing of it is mistaken for an entry; a
    run that is not --dev has no such line."""
    rec, _said = opened()
    s = summary(rec)
    s["dev"] = True
    CL.close(rec, s, S.Made(), Adapter())
    assert read(rec, "verification_skeleton.md").startswith("DEV RUN, not evidence")
    rec2, _s = opened()
    CL.close(rec2, summary(rec2), S.Made(), Adapter())
    assert "DEV RUN" not in read(rec2, "verification_skeleton.md")


def test_readme_compare_all():
    """A13, A10 (the charge-interposer reviewer, 2026-11-04): a comparison's
    README lines are the differences that count, then every other one its
    comparer saw, both sides' values, marked as not counted; a counted one
    is not listed twice."""
    rec, _said = opened()
    s = summary(rec)
    fig = "figure 250k/idle error frames: a 352088 | b 494441"
    s["compares"] = [{"id": "vp", "expect": "same-verdicts", "held": False,
                      "counted": ["VERDICT 1M/idle: PASS -> FAIL"],
                      "all": ["VERDICT 1M/idle: PASS -> FAIL", fig]}]
    CL.close(rec, s, S.Made(), Adapter())
    sec = read(rec, "README.md").split("## Comparisons", 1)[1].split("\n## ", 1)[0]
    lines = [ln for ln in sec.splitlines() if ln.startswith(("- ", "  - "))]
    assert lines == ["- compare vp (same-verdicts): DID NOT HOLD",
                     "  - VERDICT 1M/idle: PASS -> FAIL",
                     "  - seen, not counted: " + fig], sec


TESTS = [test_readme, test_sha256sums, test_closed_once, test_identifiers_reported,
         test_cleanup_first, test_late_failure, test_artifacts_row, test_skeleton,
         test_dev_skeleton, test_readme_not_passed, test_readme_compare_all]

MUTANTS = [
    ("readme_items", test_readme),
    ("readme_not_passed", test_readme_not_passed),
    ("not_passed_all", test_readme_not_passed),
    ("readme_commits", test_readme),
    ("sums_all", test_sha256sums),
    ("console_stops", test_closed_once),
    ("close_once", test_closed_once),
    ("scan_hits", test_identifiers_reported),
    ("scan_absent", test_identifiers_reported),
    ("scan_failure", test_identifiers_reported),
    ("cleanup_first", test_cleanup_first),
    ("leftovers_in", test_cleanup_first),
    ("late_outside", test_late_failure),
    ("row", test_artifacts_row),
    ("skeleton_reruns", test_skeleton),
    ("dev_marked", test_dev_skeleton),
    ("compare_all", test_readme_compare_all),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    can_fail = "--can-fail" in argv
    bad = 0
    try:
        if not can_fail:
            for t in TESTS:
                out = outcome_of(t)
                print("%s  %-26s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(CL.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            CL.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                CL.RULES[k] = True
            print("%s  %-16s off: %-26s %s" % (
                "ok  " if out else "FAIL", k, t.__name__,
                ("fails (" + out[:110] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 1 if bad or caught < len(MUTANTS) else 0
    finally:
        S.remove(BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
