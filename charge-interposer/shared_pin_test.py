#!/usr/bin/env python3
"""Tests shared_pin.py: the harness's shared code is the tag the project
pins, from its read-only checkout ../shared-<tag>/, or it is refused.

Charge-interposer tester, port step 2a (the reviewer, 2026-11-03; port plan
goal 20 item 8). The fixture is made in a scratch_dir run folder: a stand-in
shared repo `src` with two tagged commits (T1, and T2 with a changed
vtrux_bench), a project worktree `wt` beside it, and checkouts
`shared-<tag>` beside `wt`, as on the bench host. The stand-in's
shared/tools/vtrux_build/digest.py is a salted hash of its own, so a digest
taken by anything but the checkout's own module gives another value.

  1. read(): a good entry reads; refused are no file, bad JSON, no entry, no
     tag, a tag that is a commit id, a tag with a slash, a short commit, a
     bad digest, and a component the harness does not pin.
  2. folder(): the pinned shared/bench and shared/twai_port pass; refused
     are a missing checkout (the refusal prints the create command), a
     checkout at another commit, a tag the repository lacks, a tag marking
     another commit than the pin's, a file changed, added or removed in the
     component, and no worktree. No __pycache__ is left in the checkout.
  3. The digest is the checkout's own: a pin carrying a plain sha256 of the
     same files is refused.
  4. use_bench(), each in a fresh interpreter: vtrux_bench loads from the
     checked folder, first on sys.path, once; a vtrux_bench imported earlier
     from elsewhere is refused; a failing pin is refused before sys.path
     changes.
  5. entry(): gives the values folder() then accepts; a checkout whose HEAD
     is not the tag is refused.
  6. main(): check/where exit 0 on the pin, 1 refused, 2 on a bad call.
  7. The real pin file: once it has the tester's entries, both pass
     folder() on this host; until then reported as not checked.

    py -3.14 -B charge-interposer/shared_pin_test.py
    py -3.14 -B charge-interposer/shared_pin_test.py --can-fail

--can-fail runs the test on copies of shared_pin.py with one edit each
(SHARED_PIN_SRC names the copy's folder), and passes only if every one is
caught.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("SHARED_PIN_SRC") or HERE
sys.path.insert(0, SRC_DIR)
import shared_pin as P  # noqa: E402
sys.path.insert(0, HERE)
import scratch_dir  # noqa: E402

FAILS = []
SALT = "stand-in R7"
DIGEST_PY = '''import hashlib, os
def component_digest(root):
    h = hashlib.sha256(b"%s")
    for d, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for f in sorted(files):
            if f.endswith(".md"):
                continue
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, root).replace(os.sep, "/").encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()
''' % SALT.encode().decode()


def check(name, ok, detail=""):
    print("%s %s%s" % ("ok      " if ok else "FAIL    ", name,
                       ("  -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def refused(name, fn, *args, has="", **kw):
    try:
        fn(*args, **kw)
    except P.SharedPinError as e:
        check(name, has in str(e), "refused, but without %r: %s" % (has, e))
        return str(e)
    except Exception as e:  # noqa: BLE001
        check(name, False, "raised %s: %s, not SharedPinError" % (type(e).__name__, e))
        return None
    check(name, False, "not refused")
    return None


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "core.autocrlf=false", "-C", cwd] + list(args),
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def write(base, files):
    for rel, text in files.items():
        p = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def plain_digest(root):
    h = hashlib.sha256()
    for d, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for f in sorted(files):
            h.update(open(os.path.join(d, f), "rb").read())
    return h.hexdigest()


def fixture(base):
    """{src, wt, a, b, pins}: the stand-in shared repo with tags T1 (a) and
    T2 (b), the project worktree, and its pin file path."""
    src, wt = os.path.join(base, "src"), os.path.join(base, "wt")
    os.makedirs(src)
    git(src, "init", "-q", "-b", "m")
    write(src, {"shared/tools/vtrux_build/digest.py": DIGEST_PY,
                "shared/bench/vtrux_bench/__init__.py": "MARK = 'T1'\n",
                "shared/bench/tests/test_x.py": "X = 1\n",
                "shared/bench/README.md": "readme\n",
                "shared/twai_port/test/twai_fake.c": "int fake;\n"})
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "a")
    git(src, "tag", "-a", "T1", "-m", "T1")
    a = git(src, "rev-parse", "HEAD")
    write(src, {"shared/bench/vtrux_bench/__init__.py": "MARK = 'T2'\n"})
    git(src, "commit", "-q", "-am", "b")
    git(src, "tag", "T2")
    b = git(src, "rev-parse", "HEAD")
    os.makedirs(os.path.join(wt, "charge-interposer", "firmware"))
    git(wt, "init", "-q")
    git(src, "worktree", "add", "-q", "--detach", os.path.join(base, "shared-T1"), "T1")
    pins = os.path.join(wt, "charge-interposer", "firmware", "shared_pins.json")
    return {"src": src, "wt": wt, "a": a, "b": b, "pins": pins,
            "start": os.path.join(wt, "charge-interposer")}


def pin_values(fx, tag="T1", commit=None):
    co = os.path.join(os.path.dirname(fx["wt"]), "shared-" + tag)
    out = {}
    for name in P.OURS:
        d = os.path.join(co, *name.split("/"))
        out[name] = {"tag": tag, "commit": commit or fx["a"],
                     "digest": P.digest_of(co, d)}
    return out


def set_pins(fx, entries, extra=None):
    data = {"shared/tools": {"tag": "T1", "commit": fx["a"], "digest": "0" * 64}}
    data.update(entries)
    data.update(extra or {})
    write(os.path.dirname(fx["pins"]), {"shared_pins.json": json.dumps(data, indent=2)})


def child(fx, code):
    """Run code in a fresh interpreter with the module under test."""
    pre = ("import sys; sys.path.insert(0, %r); import shared_pin as P\n"
           "P.START = %r; P.PIN_FILE = %r\n" % (SRC_DIR, fx["start"], fx["pins"]))
    return subprocess.run([sys.executable, "-B", "-c", pre + code],
                          capture_output=True, text=True, timeout=120)


def checks():
    base = scratch_dir.make("shared-pin-test")
    try:
        fx = fixture(base)
        good = pin_values(fx)
        co1 = os.path.join(base, "shared-T1")

        # 1. read()
        set_pins(fx, good)
        r = P.read("shared/bench", fx["pins"])
        check("1 a good entry reads", r == good["shared/bench"], repr(r))
        refused("1 no pin file", P.read, "shared/bench", fx["pins"] + ".x", has="no pin file")
        write(os.path.dirname(fx["pins"]), {"bad.json": "{nope"})
        refused("1 bad JSON", P.read, "shared/bench",
                os.path.join(os.path.dirname(fx["pins"]), "bad.json"), has="not valid JSON")
        set_pins(fx, {"shared/twai_port": good["shared/twai_port"]})
        refused("1 no entry", P.read, "shared/bench", fx["pins"], has="no 'shared/bench' entry")
        for label, change, has in (
                ("no tag", {"tag": ""}, "names no tag"),
                ("a commit id as the tag", {"tag": fx["a"][:12]}, "commit id"),
                ("a tag with a slash", {"tag": "x/y"}, "cannot be a checkout"),
                ("a short commit", {"commit": fx["a"][:12]}, "40 lower-case hex"),
                ("a bad digest", {"digest": "ab"}, "64 lower-case hex")):
            e = dict(good["shared/bench"], **change)
            set_pins(fx, {"shared/bench": e})
            refused("1 " + label, P.read, "shared/bench", fx["pins"], has=has)
        set_pins(fx, good)
        refused("1 a component the harness does not pin", P.read, "shared/tools",
                fx["pins"], has="not a component this harness pins")

        # 2. folder(), with bytecode allowed in this interpreter (run -B), so
        # only the module's own setting keeps it out of the checkout
        sys.dont_write_bytecode = False
        for name in P.OURS:
            try:
                d = P.folder(name, fx["pins"], fx["start"])
                check("2 the pinned %s passes" % name,
                      os.path.normcase(d) == os.path.normcase(
                          os.path.join(co1, *name.split("/"))), d)
            except P.SharedPinError as e:
                check("2 the pinned %s passes" % name, False, str(e))
        sys.dont_write_bytecode = True
        check("2 no __pycache__ left in the checkout",
              not any("__pycache__" in dirs for _d, dirs, _f in os.walk(co1)))
        set_pins(fx, {"shared/bench": dict(good["shared/bench"], tag="T2")})
        e = refused("2 a missing checkout", P.folder, "shared/bench", fx["pins"],
                    fx["start"], has="does not exist")
        check("2 the refusal prints the create command",
              bool(e) and "worktree add --detach ../shared-T2 T2" in e, e or "")
        os.rename(co1, os.path.join(base, "shared-T2"))
        refused("2 a tag marking another commit than the pin's", P.folder, "shared/bench",
                fx["pins"], fx["start"], has="the tag T2 marks")
        os.rename(os.path.join(base, "shared-T2"), os.path.join(base, "shared-T9"))
        set_pins(fx, {"shared/bench": dict(good["shared/bench"], tag="T9")})
        refused("2 a tag the repository lacks", P.folder, "shared/bench",
                fx["pins"], fx["start"], has="no tag of that name")
        os.rename(os.path.join(base, "shared-T9"), co1)
        set_pins(fx, good)
        git(co1, "checkout", "-q", "--detach", "T2")
        refused("2 a checkout at another commit", P.folder, "shared/bench",
                fx["pins"], fx["start"], has="is at %s" % fx["b"])
        git(co1, "checkout", "-q", "--detach", "T1")
        bench = os.path.join(co1, "shared", "bench")
        for label, act, undo in (
                ("changed", lambda: write(bench, {"tests/test_x.py": "X = 2\n"}),
                 lambda: git(co1, "checkout", "-q", "--", ".")),
                ("added", lambda: write(bench, {"tests/new.py": ""}),
                 lambda: os.remove(os.path.join(bench, "tests", "new.py"))),
                ("removed", lambda: os.remove(os.path.join(bench, "tests", "test_x.py")),
                 lambda: git(co1, "checkout", "-q", "--", "."))):
            act()
            refused("2 a file %s in the component" % label, P.folder, "shared/bench",
                    fx["pins"], fx["start"], has="has the digest")
            undo()
        write(bench, {"NOTES.md": "a .md the digest leaves out\n"})
        try:
            P.folder("shared/bench", fx["pins"], fx["start"])
            check("2 a .md added (left out by R7) still passes", True)
        except P.SharedPinError as e:
            check("2 a .md added (left out by R7) still passes", False, str(e))
        os.remove(os.path.join(bench, "NOTES.md"))
        # the run folder is inside this worktree's scratch/: git is kept
        # from looking above it, as on a host with no git above the code
        os.environ["GIT_CEILING_DIRECTORIES"] = os.path.dirname(base)
        try:
            refused("2 no worktree", P.folder, "shared/bench", fx["pins"], base,
                    has="not in a git worktree")
        finally:
            del os.environ["GIT_CEILING_DIRECTORIES"]

        # 3. the digest is the checkout's own
        set_pins(fx, {"shared/bench": dict(good["shared/bench"],
                                           digest=plain_digest(bench))})
        refused("3 a plain sha256 of the files is not the pin's digest", P.folder,
                "shared/bench", fx["pins"], fx["start"], has="has the digest")
        set_pins(fx, good)

        # 4. use_bench(), in fresh interpreters
        r = child(fx, "d = P.use_bench(); d2 = P.use_bench()\n"
                      "import vtrux_bench\n"
                      "print(vtrux_bench.MARK, sys.path[0] == d, sys.path.count(d))\n")
        check("4 vtrux_bench loads from the checked folder, first, once",
              r.returncode == 0 and r.stdout.split() == ["T1", "True", "1"],
              r.stdout + r.stderr[-400:])
        other = os.path.join(base, "elsewhere")
        write(other, {"vtrux_bench/__init__.py": "MARK = 'other'\n"})
        r = child(fx, "sys.path.insert(0, %r); import vtrux_bench\n"
                      "try:\n    P.use_bench()\n    print('NOT REFUSED')\n"
                      "except P.SharedPinError as e:\n    print('REFUSED', e)\n" % other)
        check("4 a vtrux_bench imported from elsewhere is refused",
              r.stdout.startswith("REFUSED") and "already imported" in r.stdout,
              r.stdout + r.stderr[-400:])
        set_pins(fx, {"shared/bench": dict(good["shared/bench"], digest="1" * 64)})
        r = child(fx, "before = list(sys.path)\n"
                      "try:\n    P.use_bench()\n    print('NOT REFUSED')\n"
                      "except P.SharedPinError:\n    print('REFUSED', sys.path == before)\n")
        check("4 a failing pin is refused before sys.path changes",
              r.stdout.split() == ["REFUSED", "True"], r.stdout + r.stderr[-400:])
        set_pins(fx, good)

        # 5. entry()
        try:
            got = {n: P.entry("T1", n, fx["start"]) for n in P.OURS}
        except P.SharedPinError as e:
            got = "refused: %s" % e
        check("5 entry() gives the values folder() accepts", got == good, repr(got))
        git(co1, "checkout", "-q", "--detach", "T2")
        refused("5 entry() refuses a checkout whose HEAD is not the tag", P.entry,
                "T1", "shared/bench", fx["start"], has="the tag T1 marks")
        git(co1, "checkout", "-q", "--detach", "T1")

        # 6. main()
        r = child(fx, "sys.exit(P.main(['where', 'shared/bench']))")
        check("6 where exits 0 and prints the folder", r.returncode == 0 and
              os.path.normcase(r.stdout.strip()) == os.path.normcase(bench),
              r.stdout + r.stderr[-300:])
        r = child(fx, "sys.exit(P.main(['check', 'shared/tools']))")
        check("6 a refusal exits 1", r.returncode == 1 and "REFUSED" in r.stderr, r.stderr[-300:])
        r = child(fx, "sys.exit(P.main(['frob']))")
        check("6 a bad call exits 2", r.returncode == 2, str(r.returncode))
    finally:
        scratch_dir.remove(base)

    # 7. the real pin file
    try:
        with open(P.PIN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not all(n in data for n in P.OURS):
        print("NOTE     7 the real pin file has no tester entries yet: not checked")
    else:
        for name in P.OURS:
            try:
                P.folder(name)
                check("7 the real %s pin passes on this host" % name, True)
            except P.SharedPinError as e:
                check("7 the real %s pin passes on this host" % name, False, str(e))
    return FAILS


MUTANTS = [
    ("no worktree check of the tag's commit",
     "    if tagged != pin[\"commit\"]:\n        raise SharedPinError(\"the tag %s marks",
     "    if False:\n        raise SharedPinError(\"the tag %s marks"),
    ("HEAD not compared with the pin",
     "if rc != 0 or head != pin[\"commit\"]:", "if rc != 0:"),
    ("the tag's existence not checked",
     "    if rc != 0 or not tagged:\n        raise SharedPinError(\"the %r pin names",
     "    if False:\n        raise SharedPinError(\"the %r pin names"),
    ("the digest not compared", "    if got != pin[\"digest\"]:", "    if False:"),
    ("the digest taken by its own sha256, not the checkout's",
     "    return mod.component_digest(folder)",
     "    import hashlib\n    h = hashlib.sha256()\n"
     "    for d, ds, fs in sorted(os.walk(folder)):\n        ds.sort()\n"
     "        for f in sorted(fs):\n            h.update(open(os.path.join(d, f), 'rb').read())\n"
     "    return h.hexdigest()"),
    ("bytecode written into the checkout", "    sys.dont_write_bytecode = True\n", ""),
    ("a missing checkout without the create command",
     "\"    %s\" % (checkout, create_command(top, tag)))",
     "\"%s\" % (checkout, \"\"))"),
    ("a commit-like tag accepted", "    if _LOOKS_LIKE_COMMIT.match(tag):", "    if False:"),
    ("a short commit accepted", '_COMMIT = re.compile(r"^[0-9a-f]{40}$")',
     '_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")'),
    ("any component accepted", "    if name not in OURS:", "    if False:"),
    ("an earlier vtrux_bench from elsewhere kept",
     "        if not getattr(mod, \"__file__\", None) or not _same(where, d):",
     "        if False:"),
    ("the folder appended, not first", "        sys.path.insert(0, d)", "        sys.path.append(d)"),
    ("the folder added every call",
     "    if not any(_same(p, d) for p in sys.path if p):\n        sys.path.insert(0, d)",
     "    sys.path.insert(0, d)"),
    ("the checkout beside the worktree's parent",
     "os.path.join(top, os.pardir, \"shared-\" + tag)",
     "os.path.join(top, os.pardir, os.pardir, \"shared-\" + tag)"),
    ("a refusal exits 0 in main", "        return 1\n    print(__doc__", "        return 0\n    print(__doc__"),
]


def main():
    if "--can-fail" not in sys.argv[1:]:
        got = checks()
        print("\nshared_pin_test: %s" % ("all checks OK" if not got else
                                         "%d FAILED: %s" % (len(got), "; ".join(got))))
        return 1 if got else 0
    base = scratch_dir.make("shared-pin-mutants")
    src = open(os.path.join(HERE, "shared_pin.py"), encoding="utf-8", newline="").read()
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate([("control: unmutated", "", "")] + MUTANTS):
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            if n and src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            with open(os.path.join(d, "shared_pin.py"), "w", encoding="utf-8",
                      newline="") as f:
                f.write(src.replace(old, new) if n else src)
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                               capture_output=True, text=True, timeout=900,
                               env=dict(os.environ, SHARED_PIN_SRC=d))
            if n == 0:
                ok = r.returncode == 0 and "all checks OK" in r.stdout
                print("%s m0 the unmutated copy passes" % ("ok      " if ok else "FAIL    "))
                if not ok:
                    print(r.stdout[-1500:] + r.stderr[-800:])
                    errors += 1
                continue
            fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
            dead = bool(fails) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "".join("\n           " + f[:130] for f in fails[:2])))
    finally:
        scratch_dir.remove(base)
    print("\nshared_pin_test --can-fail: %d of %d killed by a check, %d errors"
          % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
