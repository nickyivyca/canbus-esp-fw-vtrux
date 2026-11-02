"""Tests of the acceptance record's start (vtrux_bench/accept/record.py) and
of the runner's Python guard: ACCEPT-SPEC A13 and the reviewer's condition of
2026-11-01 (the runner runs on Python >= 3.11; WSL is a check host only).
No hardware. Each test names what it checks.

    python -B shared/bench/tests/test_accept_record.py            every test
    python -B shared/bench/tests/test_accept_record.py --can-fail  the control
        first (every test unchanged, which must pass), then each test again
        with a rule it guards switched off (record.RULES; every rule has one)

Work goes in this run's own folder under the worktree's scratch/
(tests/_scratch.py), removed at the end with vtrux_bench/scratch.py.
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
sys.path.insert(0, BENCH)
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402
from vtrux_bench import scratch as S                 # noqa: E402
from vtrux_bench.accept import record as R           # noqa: E402

BASE = _scratch.run_dir("accept_record")
N = [0]


def fresh(name):
    N[0] += 1
    d = os.path.join(BASE, "%s%d" % (name, N[0]))
    os.makedirs(d)
    return d


def outside_git():
    """A runs root in no git worktree: BASE is inside this worktree, so the
    tests make one beside the worktree's scratch/ that git does not see as
    a worktree of its own -- a folder with GIT_CEILING_DIRECTORIES set at
    BASE stops git's search there."""
    return fresh("runs")


# ---- the tests --------------------------------------------------------------
def test_named_first():
    """A13: start() makes the record folder before anything else,
    <runs>/accept_<step>_<YYYYmmddTHHMMSS>_<hex4>/, names it as the first
    console line, and from then on every console line is in its
    console.txt too."""
    runs = outside_git()
    said = []
    rec = R.start(runs, "s1", out=said.append, ceiling=BASE)
    try:
        assert os.path.isdir(rec.path), rec.path
        assert os.path.dirname(rec.path) == runs, rec.path
        assert re.match(r"^accept_s1_\d{8}T\d{6}_[0-9a-f]{4}$", rec.id), rec.id
        assert said and rec.path in said[0] and said[0].startswith("acceptance record:"), said
        rec.say("a line from the runner")
        rec.say("another")
    finally:
        rec.close()
    with open(os.path.join(rec.path, "console.txt"), encoding="utf-8") as f:
        text = f.read()
    for ln in (said[0], "a line from the runner", "another"):
        assert ln in text, (ln, text)
    assert said[-2:] == ["a line from the runner", "another"], said


def test_never_reused():
    """A13: a record folder is never reused: two starts in the same second
    get two folders, and an id that is already there is refused."""
    runs = outside_git()
    a = R.start(runs, "s1", out=lambda s: None, ceiling=BASE)
    b = R.start(runs, "s1", out=lambda s: None, ceiling=BASE)
    a.close()
    b.close()
    assert a.path != b.path, a.path
    try:
        R.start(runs, "s1", out=lambda s: None, ceiling=BASE, record_id=a.id)
    except R.RecordError:
        return
    raise AssertionError("an existing record folder was taken again")


def test_off_publish_paths():
    """A13: run records are never published: a runs folder inside a git
    worktree (this repo is public) is refused, before any folder is made."""
    runs = os.path.join(BASE, "in-worktree-runs")
    try:
        R.start(runs, "s1", out=lambda s: None)
    except R.RecordError as e:
        assert "git" in str(e), e
        assert not os.path.exists(runs), "a folder was made before the refusal"
        return
    raise AssertionError("a runs folder inside a git worktree was taken")


def test_step_id():
    """A13: the step id goes into a folder name, so it must be a plain id."""
    runs = outside_git()
    for bad in ("a b", "..", "a/b", ""):        # "a b" first: it would make a folder
        try:
            R.start(runs, bad, out=lambda s: None, ceiling=BASE)
        except R.RecordError:
            continue
        raise AssertionError("step id %r taken" % bad)


def test_python_guard():
    """The reviewer's condition (2026-11-01): accept refuses to start on
    Python < 3.11 with a message naming 3.11 and saying WSL is a check
    host; importing vtrux_bench.accept on an older Python stops there,
    before tomllib is reached."""
    msg = R.python_refusal((3, 10, 7))
    assert msg and "3.11" in msg and "WSL" in msg, msg
    assert R.python_refusal((3, 11, 0)) is None and R.python_refusal((3, 14, 6)) is None
    code = ("import sys; sys.version_info = (3, 10, 7, 'final', 0)\n"
            "try:\n    import vtrux_bench.accept\n"
            "except ImportError as e:\n    print('REFUSED', e)\n"
            "else:\n    print('IMPORTED')\n")
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=BENCH), cwd=BENCH, timeout=60)
    assert r.stdout.startswith("REFUSED") and "3.11" in r.stdout, r.stdout + r.stderr


TESTS = [test_named_first, test_never_reused, test_off_publish_paths, test_step_id,
         test_python_guard]

MUTANTS = [
    ("folder_first", test_named_first),
    ("console_tee", test_named_first),
    ("exclusive", test_never_reused),
    ("off_git", test_off_publish_paths),
    ("step_id", test_step_id),
    ("python_guard", test_python_guard),
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
                print("%s  %-24s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(R.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            R.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                R.RULES[k] = True
            print("%s  %-14s off: %-24s %s" % (
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
