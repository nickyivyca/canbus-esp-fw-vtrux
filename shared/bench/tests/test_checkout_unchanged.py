"""shared/bench's tests leave the checkout they run from as they found it: the
component digest (shared/tools R7) of shared/bench is the same before and
after every other test file in tests/ runs. No hardware.

    python -B shared/bench/tests/test_checkout_unchanged.py            every test
    python -B shared/bench/tests/test_checkout_unchanged.py --can-fail  the control
        first (every test unchanged, which must pass), then each mutant: a
        rule of this file's switched off, or a test's -B taken out again;
        every one must make its test fail
    python -B shared/bench/tests/test_checkout_unchanged.py test_x.py...  the
        whole-folder test over those test files only

Why (the charge-interposer reviewer, 2026-11-16, on the charge-interposer
tester's finding): test_run_core's bench-lock checks ran `python -c "from
vtrux_bench import bench_lock"` with the settings' python, which had no -B, so
vtrux_bench/__pycache__/ was written into the checkout the tests ran from. A
project runs shared/bench from a read-only checkout whose digest its pin
holds, so the project's next use of the pin refused. Every Python a test
starts now runs with -B. A survey of every test file, plain and with
--can-fail, from a fresh copy (2026-11-16, madhouse-debian) found
test_run_core the only one that wrote into it (vtrux_bench/__pycache__/,
through the settings' python), so its -B is the one with a mutant here; the
rest are there so that a check added later inherits it. test_accept_compile's
settings keep a bare python: the fake adapter adds -B to it, and
test_compile_shape checks that argv exactly.
test_accept_run's test_runner_stays_clean still runs a runner without -B on
purpose, in a clone under scratch/, not in this checkout.

The whole run takes about 17 minutes on madhouse-debian (test_scratch_helper's
--can-fail alone about 5).

How. The tracked files of shared/bench (`git ls-files`, as they are on disk)
are copied into this run's own folder under the worktree's scratch/
(tests/_scratch.py), so no bytecode an earlier run left can hide a write:
Python does not rewrite a cache that is current. Each test file runs there
twice, plain and with --can-fail (projects run both), each with -B as its
docstring says, with PYTHONDONTWRITEBYTECODE and
PYTHONPYCACHEPREFIX taken out of its environment, so that whatever it starts
without -B writes where it would from a pinned checkout. The digest is taken
after each file; a change fails the test, naming each file added, removed or
changed and the test file after which it appeared, and the copy is made
again before the next file. A test file's own exit code is printed, not
judged: its own run judges it. The digest is this checkout's
shared/tools/vtrux_build/digest.py, loaded by file with no bytecode written;
a checkout without it, or with no tracked files under shared/bench, is
refused.

The two plant tests check the checker: a test file planted in the copy that
starts `python -c "import vtrux_bench.decode"` from the copy's shared/bench
without -B must be caught, naming vtrux_bench/__pycache__/; the same with -B
must not. Under --can-fail PYTHONDONTWRITEBYTECODE=1 is set in this process
for the control and every mutant, so the environment rule has something to
take out.
"""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
SHARED = os.path.dirname(BENCH)
sys.path.insert(0, HERE)

import _scratch                                      # noqa: E402

PY = sys.executable
ME = os.path.basename(os.path.abspath(__file__))
SCRATCH = _scratch.run_dir("checkout_unchanged")
N = [0]
TEST_TIMEOUT_S = 3600
NO_BYTECODE_ENV = ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")

# What the checker relies on; --can-fail switches each off in turn.
RULES = {
    "env_cleared": True,      # the bytecode variables are taken out of each test's environment
    "whole_folder": True,     # the digest covers every file on disk, not only the tracked ones
    "fresh_copy": True,       # the copy is made again after a file that changed it
}


class Refused(Exception):
    pass


def load_digest():
    """This checkout's shared/tools R7 digest module, loaded by file."""
    src = os.path.join(SHARED, "tools", "vtrux_build", "digest.py")
    if not os.path.isfile(src):
        raise Refused("%s is missing, so the checkout's digest cannot be taken" % src)
    spec = importlib.util.spec_from_file_location("_checkout_digest", src)
    mod = importlib.util.module_from_spec(spec)
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


DG = load_digest() if os.path.isfile(
    os.path.join(SHARED, "tools", "vtrux_build", "digest.py")) else None


def tracked():
    """shared/bench's tracked files, relative to it, with "/"."""
    r = subprocess.run(["git", "-C", BENCH, "ls-files", "-z", "."], capture_output=True)
    if r.returncode != 0:
        raise Refused("git ls-files failed in %s: %s" % (BENCH, r.stderr.decode(errors="replace")))
    files = sorted(p for p in r.stdout.decode("utf-8").split("\0") if p)
    if not files:
        raise Refused("no tracked files under %s" % BENCH)
    return files


def make_copy(dst, mutate=None, plant=None):
    """The tracked files into dst/shared/bench; a mutant's edit (rel, old, new)
    applied, which must find old exactly once; a planted test file (name,
    text) written. Returns the copy's shared/bench."""
    bench = os.path.join(dst, "shared", "bench")
    if os.path.exists(dst):
        _scratch.remove_tree(dst)
    for rel in tracked():
        out = os.path.join(bench, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(os.path.join(BENCH, *rel.split("/")), out)
    if mutate:
        rel, old, new = mutate
        p = os.path.join(bench, *rel.split("/"))
        with open(p, encoding="utf-8", newline="") as f:
            text = f.read()
        if text.count(old) != 1:
            raise Refused("mutant edit found %d times in %s: %r" % (text.count(old), rel, old))
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new))
    if plant:
        name, text = plant
        with open(os.path.join(bench, "tests", name), "w", encoding="utf-8") as f:
            f.write(text)
    return bench


def files_of(bench, listed):
    """{relative path: sha256} the digest covers: every file under bench (the
    R7 file set), or with the whole_folder rule off only the listed ones."""
    if RULES["whole_folder"]:
        pairs = DG.component_files(bench)
    else:
        pairs = [(rel, os.path.join(bench, *rel.split("/"))) for rel in listed
                 if os.path.isfile(os.path.join(bench, *rel.split("/")))]
    out = {}
    for rel, full in pairs:
        with open(full, "rb") as f:
            out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def digest_of(bench, listed):
    if RULES["whole_folder"]:
        return DG.component_digest(bench)
    h = hashlib.sha256()
    for rel, d in sorted(files_of(bench, listed).items()):
        h.update(("%s\0%s\0" % (rel, d)).encode("utf-8"))
    return h.hexdigest()


def child_env():
    env = dict(os.environ)
    if RULES["env_cleared"]:
        for k in NO_BYTECODE_ENV:
            env.pop(k, None)
    return env


def changes(before, after):
    out = ["added %s" % p for p in sorted(set(after) - set(before))]
    out += ["removed %s" % p for p in sorted(set(before) - set(after))]
    out += ["changed %s" % p for p in sorted(set(before) & set(after)) if before[p] != after[p]]
    return out


def run_tests(names, mutate=None, plant=None, modes=((),)):
    """Each named test file run in a fresh copy, once per mode (its extra
    arguments): [(name and mode, exit code, [changes to the copy after it])]."""
    N[0] += 1
    dst = os.path.join(SCRATCH, "c%d" % N[0])
    bench = make_copy(dst, mutate, plant)
    listed = sorted(files_of_listing(bench))
    d0, f0 = digest_of(bench, listed), files_of(bench, listed)
    d, f = d0, f0                    # the copy as the next test file finds it
    out = []
    try:
        for n, args in [(n, a) for n in names for a in modes]:
            label = " ".join([n] + list(args))
            r = subprocess.run([PY, "-B", os.path.join("tests", n)] + list(args), cwd=bench,
                               env=child_env(), capture_output=True, text=True,
                               timeout=TEST_TIMEOUT_S)
            d1 = digest_of(bench, listed)
            if d1 == d:
                out.append((label, r.returncode, []))
                continue
            f1 = files_of(bench, listed)
            out.append((label, r.returncode, changes(f, f1) or ["the digest changed"]))
            if RULES["fresh_copy"]:
                bench = make_copy(dst, mutate, plant)
            else:
                d, f = d1, f1
    finally:
        _scratch.remove_tree(dst)
    return out


def files_of_listing(bench):
    """The copy's files as made (the tracked ones and a plant), relative."""
    out = []
    for root, dirs, files in os.walk(bench):
        for fn in files:
            out.append(os.path.relpath(os.path.join(root, fn), bench).replace(os.sep, "/"))
    return out


def all_tests():
    return sorted(n for n in os.listdir(HERE)
                  if n.startswith("test_") and n.endswith(".py") and n != ME)


def report(results):
    bad = []
    for n, rc, ch in results:
        print("      %-44s exit %-3s %s" % (n, rc, "; ".join(ch) if ch else "unchanged"),
              flush=True)
        if ch:
            bad.append("%s: %s" % (n, "; ".join(ch)))
    return bad


BOTH_MODES = ((), ("--can-fail",))


def test_tests_leave_checkout(names=None, mutate=None, modes=BOTH_MODES):
    """Every other test file in tests/, run from a fresh copy plain and with
    --can-fail (projects run both), leaves the copy's digest unchanged."""
    bad = report(run_tests(names or all_tests(), mutate=mutate, modes=modes))
    if bad:
        raise AssertionError("the checkout changed: " + " | ".join(bad))


PLANT = '''import os, subprocess, sys
bench = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
subprocess.run([sys.executable%s, "-c", "import vtrux_bench.decode"], cwd=bench, check=True)
'''


def test_plant_caught():
    """A test file that starts Python without -B in the checkout is caught,
    naming vtrux_bench/__pycache__/."""
    (n, rc, ch), = run_tests(["test_zz_plant.py"], plant=("test_zz_plant.py", PLANT % ""))
    assert rc == 0, "the plant failed (exit %s)" % rc
    assert ch and any("vtrux_bench/__pycache__/" in c for c in ch), \
        "a write into vtrux_bench/__pycache__ was not caught: %r" % ch


def test_plant_clean():
    """The same plant with -B leaves the copy unchanged."""
    (n, rc, ch), = run_tests(["test_zz_plant.py"], plant=("test_zz_plant.py", PLANT % ', "-B"'))
    assert rc == 0 and not ch, "exit %s, %r" % (rc, ch)


def test_plants_fresh():
    """Two leaking files in one run: each is named, since the copy is made
    again after the first (with fresh_copy off the second writes nothing new:
    the first one's cache is current)."""
    res = run_tests(["test_zz_plant.py", "test_zz_plant.py"],
                    plant=("test_zz_plant.py", PLANT % ""))
    assert [bool(ch) for _, _, ch in res] == [True, True], res


TESTS = [test_plant_caught, test_plant_clean, test_plants_fresh, test_tests_leave_checkout]


def _rule(name):
    return (lambda: RULES.__setitem__(name, False),
            lambda: RULES.__setitem__(name, True), name)


def _undo_b(label, rel, old, new):
    """A test file with its -B taken out again, run alone and plain."""
    return ((lambda: None), (lambda: None), label), \
        (lambda: test_tests_leave_checkout([rel.split("/")[-1]], mutate=(rel, old, new),
                                           modes=((),)))


MUTANTS = [
    (_rule("env_cleared"), test_plant_caught),
    (_rule("whole_folder"), test_plant_caught),
    (_rule("fresh_copy"), test_plants_fresh),
    _undo_b("test_run_core: settings python without -B", "tests/test_run_core.py",
            'hosts={"local": {"python": [PY, "-B"]}},',
            'hosts={"local": {"python": [PY]}},'),
]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    if DG is None:
        print("REFUSED: %s has no shared/tools/vtrux_build/digest.py" % SHARED)
        return 2
    try:
        tracked()
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    can_fail = "--can-fail" in argv
    names = [a for a in argv if not a.startswith("--")]
    if names:
        o = outcome_of(lambda: test_tests_leave_checkout(names))
        print("%s  test_tests_leave_checkout %s" % ("ok  " if o is None else "FAIL", o or ""))
        return 1 if o else 0
    if can_fail:
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    bad = []
    for t in TESTS:
        s = time.monotonic()
        o = outcome_of(t)
        print("%s  %-26s %.0fs %s" % ("ok  " if o is None else "FAIL", t.__name__,
                                     time.monotonic() - s, o or ""), flush=True)
        if o:
            bad.append(t.__name__)
    if not can_fail:
        print("\n%d of %d passed" % (len(TESTS) - len(bad), len(TESTS)))
        return 1 if bad else 0
    if bad:
        print("\nREFUSED: the control fails (%s); no mutant is counted" % ", ".join(bad))
        return 1
    caught = 0
    for (on, off, name), t in MUTANTS:
        on()
        try:
            o = outcome_of(t)
        finally:
            off()
        caught += o is not None
        print("%s  %-44s off: %-26s %s" % ("ok  " if o else "FAIL", name,
                                           getattr(t, "__name__", "its test file"),
                                           ("fails (%s)" % o[:120]) if o else
                                           "STILL PASSES -- nothing checks it"), flush=True)
    print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
