"""Tests of the acceptance runner's comparisons, premises and verdict
(vtrux_bench/accept/judge.py): ACCEPT-SPEC A10 (comparisons), A11, A12. No
hardware. Each test names what it checks.

    python -B shared/bench/tests/test_accept_judge.py            every test
    python -B shared/bench/tests/test_accept_judge.py --can-fail  the control
        first (every test unchanged, which must pass), then each test again
        with a rule it guards switched off (judge.RULES; every rule has one)

The comparisons and the verdict are fed made-up records and logs; the
premises read a small git repo built in this run's own folder under the
worktree's scratch/ (tests/_scratch.py), removed at the end with
vtrux_bench/scratch.py.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
import fake_adapter as FA                            # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import judge as J            # noqa: E402

BASE = _scratch.run_dir("accept_judge")
N = [0]


def res(i, verdict, **figs):
    return {"id": i, "check": i, "verdict": verdict,
            "figures": {k: {"value": v, "unit": ""} for k, v in figs.items()},
            "tolerances": {}}


def run_side(*results):
    return {"kind": "run", "record": {"results": list(results)}}


def log_side(text):
    N[0] += 1
    p = os.path.join(BASE, "log%d.txt" % N[0])
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return {"kind": "check", "log": p}


def cmp(comparer, a, b, expect, **kw):
    spec = dict(id="c", comparer=comparer, expect=expect)
    spec.update(kw)
    return J.run_compare(spec, a, b, FA)


# ---- comparisons (A10) ------------------------------------------------------
def test_compare_identical():
    """A10: "identical" holds only with no difference at all: the same
    verdicts and the same figures; the difference is named."""
    a = run_side(res("g/a", "PASS", t=1.0), res("g/b", "PASS"))
    assert cmp("run-records", a, run_side(res("g/a", "PASS", t=1.0), res("g/b", "PASS")),
               "identical")["held"]
    r = cmp("run-records", a, run_side(res("g/a", "PASS", t=1.0), res("g/b", "FAIL")),
            "identical")
    assert not r["held"] and any("g/b" in d for d in r["counted"]), r
    r = cmp("run-records", a, run_side(res("g/a", "PASS", t=2.0), res("g/b", "PASS")),
            "identical")
    assert not r["held"], r


def test_compare_same_verdicts():
    """A10: "same-verdicts" counts verdicts (and cases missing or new) and
    not figures."""
    a = run_side(res("g/a", "PASS", t=1.0))
    assert cmp("run-records", a, run_side(res("g/a", "PASS", t=9.0)), "same-verdicts")["held"]
    assert not cmp("run-records", a, run_side(res("g/a", "FAIL", t=1.0)),
                   "same-verdicts")["held"]
    assert not cmp("run-records", a, run_side(res("g/a", "PASS"), res("g/new", "PASS")),
                   "same-verdicts")["held"]


def test_compare_within():
    """A10: "within" holds when the verdicts are the same and every figure
    is within the list's tolerance for it (by name, or "*")."""
    a = run_side(res("g/a", "PASS", t=1.0, n=5))
    b = run_side(res("g/a", "PASS", t=1.5, n=5))
    assert cmp("run-records", a, b, "within", tolerance={"t": {"abs": 1.0}})["held"]
    assert not cmp("run-records", a, b, "within", tolerance={"t": {"abs": 0.1}})["held"]
    assert cmp("run-records", a, b, "within", tolerance={"*": {"rel": 0.6}})["held"]
    assert not cmp("run-records", a, run_side(res("g/a", "FAIL", t=1.0, n=5)), "within",
                   tolerance={"*": {"rel": 1.0}})["held"]


def test_compare_listed():
    """A10: "listed" holds when the differences are exactly the list's, one
    by one: each listed one is there and nothing else is."""
    a = run_side(res("g/a", "PASS"), res("g/b", "PASS"))
    b = run_side(res("g/a", "FAIL"), res("g/b", "PASS"))
    d = cmp("run-records", a, b, "identical")["counted"]
    assert len(d) == 1, d
    assert cmp("run-records", a, b, "listed", differences=d)["held"]
    assert not cmp("run-records", a, b, "listed", differences=[])["held"]
    assert not cmp("run-records", a, b, "listed", differences=d + ["VERDICT g/b: PASS -> FAIL"])["held"]
    c = run_side(res("g/a", "FAIL"), res("g/b", "FAIL"))
    assert not cmp("run-records", a, c, "listed", differences=d)["held"]


def test_transcript():
    """A10: the transcript comparer compares two logs line by line after
    the masks (a regex, "re:...", or the adapter's named mask), so two runs
    in two trees compare as identical when only their tree roots differ."""
    a = log_side("start\nroot C:/x/tree-base-111/project\nok 3\n")
    b = log_side("start\nroot C:/x/tree-new-222/project\nok 3\n")
    assert not cmp("transcript", a, b, "identical")["held"]
    assert cmp("transcript", a, b, "identical", mask=["re:tree-[a-z]+-[0-9]+"])["held"]
    assert cmp("transcript", a, b, "identical", mask=["tree-root"])["held"]
    c = log_side("start\nroot C:/x/tree-new-222/project\nok 4\n")
    r = cmp("transcript", a, c, "identical", mask=["tree-root"])
    assert not r["held"] and any("ok 4" in d for d in r["counted"]), r


def test_adapter_comparer():
    """A10, A15: a comparer named by the adapter is the adapter's code."""
    r = cmp("fake-count", run_side(res("g/a", "PASS")), run_side(res("g/a", "PASS"),
                                                                 res("g/b", "PASS")), "identical")
    assert not r["held"] and r["counted"] == ["count 1 -> 2"], r


# ---- premises (A11) ---------------------------------------------------------
def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def repo_with_two_commits():
    """proj/check.py and proj/suite.py (the fake product files) at C1; at C2
    proj/notes.md changed (not product); at C3 proj/check.py changed."""
    N[0] += 1
    repo = os.path.join(BASE, "repo%d" % N[0])
    os.makedirs(os.path.join(repo, "proj"))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    cs = []
    for files in ({"check.py": "a\n", "suite.py": "s\n", "notes.md": "1\n"},
                  {"notes.md": "2\n"}, {"check.py": "b\n"}):
        for n, t in files.items():
            with open(os.path.join(repo, "proj", n), "w", newline="\n") as f:
                f.write(t)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "c")
        cs.append(git(repo, "rev-parse", "HEAD"))
    return repo, cs


def test_premise_digest():
    """A11: a premise on the product digest holds or not: equal to a value,
    or the same as the digest at a commit, worked out from git objects."""
    repo, (c1, c2, c3) = repo_with_two_commits()
    made = S.Made()
    parent = os.path.join(BASE, "digests%d" % N[0])
    d2 = J.digest_at(repo, c2, FA, parent, made)
    trees = {"base": {"record": {"product_digest": d2}}}
    ps = [dict(id="p1", kind="product_digest_same_as", tree="base", commit=c1),
          dict(id="p2", kind="product_digest_same_as", tree="base", commit=c3),
          dict(id="p3", kind="product_digest_equal", tree="base", value=d2),
          dict(id="p4", kind="product_digest_equal", tree="base", value="0" * 64)]
    got = {p["id"]: p["held"] for p in J.premises(ps, trees, repo, FA, parent, made)}
    assert got == {"p1": True, "p2": False, "p3": True, "p4": False}, got
    assert made.remove_all(out=lambda s: None) == []


def test_not_exercised():
    """A11: a premise that fails makes its items NOT EXERCISED, never PASS
    or FAIL, whatever their results; with no items named, every item; an
    item it does not name is judged as usual. A12: then no ACCEPTANCE
    PASS."""
    items = [J.item("check", "a", "PASS", "PASS"), J.item("check", "b", "PASS", "FAIL"),
             J.item("check", "c", "PASS", "PASS")]
    v = J.verdict(items, [dict(id="p", held=False, why="digest differs", items=["a", "b"])])
    st = {i["id"]: i["status"] for i in v["items"]}
    assert st == {"a": "NOT EXERCISED", "b": "NOT EXERCISED", "c": "ok"}, st
    assert not v["pass"], v
    v = J.verdict(items[:1] + items[2:], [dict(id="p", held=False, why="x", items=[])])
    assert {i["status"] for i in v["items"]} == {"NOT EXERCISED"}, v["items"]


def test_verdict():
    """A12: ACCEPTANCE PASS only if every premise held, every check gave
    its expected verdict (a check expected to FAIL that fails is right),
    every comparison held and every can-fail behaved; otherwise each item
    that did not is listed."""
    ok = [J.item("check", "a", "PASS", "PASS"), J.item("check", "neg", "FAIL", "FAIL"),
          J.item("compare", "cmp", "held", "held"), J.item("suite_run", "emu", "PASS", "PASS"),
          J.item("build", "fw", "PASS", "PASS"), J.item("canfail", "cf", "as stated", "as stated")]
    v = J.verdict(ok, [dict(id="p", held=True, why="", items=[])])
    assert v["pass"] and v["lines"][0] == "ACCEPTANCE PASS", v["lines"]
    for bad in (J.item("check", "x", "PASS", "FAIL"), J.item("check", "y", "FAIL", "PASS"),
                J.item("compare", "c2", "held", "differs"), J.item("build", "b2", "PASS", "FAIL"),
                J.item("canfail", "cf2", "as stated", "STILL PASSES")):
        v = J.verdict(ok + [bad], [])
        assert not v["pass"] and v["lines"][0] == "ACCEPTANCE FAIL", (bad, v["lines"])
        assert any(bad["id"] in ln for ln in v["lines"][1:]), (bad, v["lines"])


TESTS = [test_compare_identical, test_compare_same_verdicts, test_compare_within,
         test_compare_listed, test_transcript, test_adapter_comparer, test_premise_digest,
         test_not_exercised, test_verdict]

MUTANTS = [
    ("count_verdicts", test_compare_identical),
    ("count_figures", test_compare_identical),
    ("verdicts_only", test_compare_same_verdicts),
    ("missing_new", test_compare_same_verdicts),
    ("tolerance", test_compare_within),
    ("listed_exact", test_compare_listed),
    ("listed_seen", test_compare_listed),
    ("masks", test_transcript),
    ("adapter_comparer", test_adapter_comparer),
    ("premise_equal", test_premise_digest),
    ("premise_same_as", test_premise_digest),
    ("not_exercised", test_not_exercised),
    ("all_items", test_not_exercised),
    ("expected_verdict", test_verdict),
    ("every_item", test_verdict),
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
                print("%s  %-28s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(J.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            J.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                J.RULES[k] = True
            print("%s  %-16s off: %-28s %s" % (
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
