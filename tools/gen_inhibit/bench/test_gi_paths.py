#!/usr/bin/env python3
"""Tests gi_paths.py, where the bench tools find what is not in this repo.

Gen-inhibit tester, 2026-10-22 (the goal-20 move). On a fake reverse-it root in this
run's scratch/ folder (the machine's temp folder until 2026-10-24), with a fake pins file:

  1. REVERSE_IT_DIR: unset, empty, or a folder without canre/ and projects/ is refused;
     a real root resolves, and every data path (runs, captures, DBCs, the bench lock,
     the jammer builds) lands under it, never under the repo.
  2. The retired settings (GI_FW_TOOLS, GEN_INHIBIT_REPO, VTRUX_DATA, REVERSE_IT_ROOT)
     are each refused, by check_retired and on import.
  3. Pins: a pinned file that matches passes, one changed is refused, one spelt in
     another case is still checked, an unpinned file passes. A pinned folder that
     matches passes; a changed, renamed, added or removed file is refused; a change to an
     excluded file is not, and an excluded file is never opened; a folder with no pin
     is refused.
  4. repo_root and fw_tools name this repo; use_canre puts the root on sys.path.

    py -3.14 -B test_gi_paths.py           # exit 0 only if every check passes
    py -3.14 -B test_gi_paths.py --real    # also: every entry of the real pins.json
                                           # holds today (reads ~2.3 GB; REVERSE_IT_DIR set)

Its mutants: test_gi_paths_mutants.py. GI_PATHS_DIR runs another copy of gi_paths.py.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.environ.get("GI_PATHS_DIR") or HERE
FAILS = []


def check(label, fn, detail=""):
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001  a crash is this check's FAIL
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def raises(fn, text=""):
    """True if fn raises an error whose message holds text."""
    try:
        fn()
    except BaseException as e:      # noqa: BLE001  SystemExit included
        return text in str(e)
    return False


def load(env):
    """A fresh import of gi_paths (SRC_DIR's) under env; (module, None) or (None, error)."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        sys.modules.pop("gi_paths", None)
        sys.path.insert(0, SRC_DIR)
        try:
            import gi_paths
        finally:
            sys.path.remove(SRC_DIR)
        return gi_paths, None
    except BaseException as e:      # noqa: BLE001
        return None, e
    finally:
        os.environ.clear()
        os.environ.update(saved)


def fake_root(base):
    root = os.path.join(base, "reverse it")          # a space, as SeaDrive's has
    for d in ("canre", "projects/vtrux/logs", "projects/vtrux/notes/artifacts/gen-inhibit/runs/fix",
              "projects/vtrux/notes/artifacts/gen-inhibit/x2_jammer/builds/abc", "firmware"):
        os.makedirs(os.path.join(root, d))
    files = {"projects/vtrux/logs/cap.log": b"capture\n",
             "projects/vtrux/epri-pt-bus.dbc": b"VERSION \"\"\n",
             "projects/vtrux/vtrux-wican-diag.dbc": b"VERSION \"diag\"\n",
             "projects/vtrux/notes/artifacts/gen-inhibit/runs/fix/a.json": b"{}\n",
             "projects/vtrux/notes/artifacts/gen-inhibit/runs/fix/b.txt": b"b\n",
             "projects/vtrux/notes/artifacts/gen-inhibit/runs/fix/secret.pcapng": b"NOT-TO-BE-READ\n",
             "projects/vtrux/notes/artifacts/gen-inhibit/x2_jammer/builds/abc/app.bin": b"\x00jam",
             "projects/vtrux/logs/unpinned.log": b"x\n",
             "firmware/bench-holder.md": b"lock\n"}
    for rel, b in files.items():
        with open(os.path.join(root, rel), "wb") as fh:
            fh.write(b)
    return root


def write_pins(gp, root):
    sha = lambda rel: hashlib.sha256(open(os.path.join(root, rel), "rb").read()).hexdigest()   # noqa: E731
    G = "projects/vtrux/notes/artifacts/gen-inhibit"
    tree = G + "/runs/fix/"
    pins = {"projects/vtrux/logs/cap.log": sha("projects/vtrux/logs/cap.log"),
            "projects/vtrux/epri-pt-bus.dbc": sha("projects/vtrux/epri-pt-bus.dbc"),
            "projects/vtrux/vtrux-wican-diag.dbc": sha("projects/vtrux/vtrux-wican-diag.dbc"),
            G + "/x2_jammer/builds/abc/app.bin": sha(G + "/x2_jammer/builds/abc/app.bin"),
            tree: {"sha256": gp.tree_digest(os.path.join(root, tree), {"secret.pcapng"}), "exclude": ["secret.pcapng"]}}
    p = os.path.join(os.path.dirname(root), "pins.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"files": pins}, fh)
    return p


def retired_usage(top, retired):
    """[(file name, line number, setting)]: lines under top, test_gi_paths*.py aside (they
    plant them), that assign a retired setting (NAME=...), as a usage line would."""
    rx = re.compile(r"\b(%s)=" % "|".join(sorted(retired)))
    out = []
    for d, dirs, files in os.walk(top):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", ".pio")]
        for n in sorted(files):
            if not n.endswith((".py", ".sh", ".ps1", ".md", ".ini", ".json")) or n.startswith("test_gi_paths"):
                continue
            with open(os.path.join(d, n), encoding="utf-8", errors="replace") as fh:
                for k, ln in enumerate(fh, 1):
                    for m in rx.finditer(ln):
                        out.append((n, k, m.group(1)))
    return out


def main():
    work = gi_paths.scratch("gi-paths")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    base_env = {k: v for k, v in os.environ.items()
                if k not in ("REVERSE_IT_DIR", "GI_FW_TOOLS", "GEN_INHIBIT_REPO", "VTRUX_DATA", "REVERSE_IT_ROOT")}
    root = fake_root(work)

    print("=== REVERSE_IT_DIR ===")
    gp, err = load(base_env)
    check("imports with no setting (every function resolves when called)", lambda: gp is not None, err)
    os.environ.pop("REVERSE_IT_DIR", None)
    check("unset is refused, naming it", lambda: raises(gp.reverse_it_dir, "REVERSE_IT_DIR is not set"))
    os.environ["REVERSE_IT_DIR"] = "  "
    check("empty is refused", lambda: raises(gp.reverse_it_dir, "REVERSE_IT_DIR is not set"))
    os.environ["REVERSE_IT_DIR"] = work
    check("a folder without canre/ and projects/ is refused", lambda: raises(gp.reverse_it_dir, "does not hold"))
    os.environ["REVERSE_IT_DIR"] = root
    check("a real root resolves", lambda: os.path.samefile(gp.reverse_it_dir(), root), gp.reverse_it_dir())
    G = os.path.join(root, "projects", "vtrux", "notes", "artifacts", "gen-inhibit")
    check("runs() is SeaDrive's runs/", lambda: os.path.normcase(gp.runs()) == os.path.normcase(os.path.join(G, "runs")),
          gp.runs())
    check("runs(x) joins under it", lambda: os.path.normcase(gp.runs("a", "b")) ==
          os.path.normcase(os.path.join(G, "runs", "a", "b")))
    check("data_dir() is the SeaDrive half", lambda: os.path.normcase(gp.data_dir()) == os.path.normcase(G))
    check("bench_holder() is firmware/bench-holder.md",
          lambda: os.path.normcase(gp.bench_holder()) == os.path.normcase(os.path.join(root, "firmware", "bench-holder.md")))
    # Each data path is under the reverse-it root, so none lands elsewhere in the repo.
    # (The fake root is in the worktree's scratch/ since 2026-10-24, not the temp folder,
    # so "not under the repo" no longer says it.)
    data = [gp.runs(), gp.data_dir(), gp.bench_holder(), gp.jammer_builds(), gp.project("projects")]
    check("no data path lands in the repo: each is under the reverse-it root",
          lambda: all(os.path.normcase(p).startswith(os.path.normcase(root) + os.sep) for p in data), data)
    check("repo_root holds tools/gen_inhibit/bench/gi_paths.py",
          lambda: os.path.isfile(os.path.join(gp.repo_root(), "tools", "gen_inhibit", "bench", "gi_paths.py")))
    check("fw_tools holds suite_gen_inhibit.py",
          lambda: os.path.isfile(os.path.join(gp.fw_tools(), "suite_gen_inhibit.py")))
    sys.path[:] = [p for p in sys.path if os.path.normcase(p) != os.path.normcase(root)]
    gp.use_canre()
    check("use_canre puts the root on sys.path", lambda: root in sys.path)
    sys.path.remove(root)

    print("\n=== scratch folders (harness-defects row, 2026-10-24) ===")
    wt = os.path.join(work, "wt")
    tool = os.path.join(wt, "a", "b", "tool.py")
    os.makedirs(os.path.dirname(tool))
    with open(os.path.join(wt, ".git"), "w") as fh:
        fh.write("gitdir: elsewhere\n")      # a worktree's .git is a file
    check("worktree_of walks up to the nearest .git", lambda: os.path.samefile(gp.worktree_of(tool), wt))
    check("a path with no .git above it is refused",
          lambda: raises(lambda: gp.worktree_of(os.path.abspath(os.sep)), "no git worktree"))
    s1 = gp.scratch("t", start=tool)
    check("scratch is <worktree>/scratch/gen-inhibit-tests/<name>/<time>_<pid>, and made",
          lambda: os.path.isdir(s1) and os.path.normcase(os.path.dirname(s1)) == os.path.normcase(
              os.path.join(wt, "scratch", "gen-inhibit-tests", "t"))
          and re.fullmatch(r"\d{8}T\d{6}_%d" % os.getpid(), os.path.basename(s1)), s1)
    check("scratch without run is the name's own folder",
          lambda: os.path.normcase(gp.scratch("t", run=False, start=tool))
          == os.path.normcase(os.path.join(wt, "scratch", "gen-inhibit-tests", "t")))
    child = subprocess.run([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import gi_paths; "
                            "print(gi_paths.scratch('t', start=sys.argv[2]))", SRC_DIR, tool],
                           capture_output=True, text=True, env=base_env)
    check("another run (process) gets another folder",
          lambda: child.returncode == 0 and os.path.isdir(child.stdout.strip())
          and os.path.normcase(child.stdout.strip()) != os.path.normcase(s1), (child.stdout, child.stderr[-200:]))
    own = gp.scratch("t", run=False)
    check("by default, the worktree that holds gi_paths.py itself, never the system temp folder",
          lambda: os.path.normcase(own) == os.path.normcase(os.path.join(gp.worktree_of(gp.HERE), "scratch",
                                                                         "gen-inhibit-tests", "t"))
          and not os.path.normcase(own).startswith(os.path.normcase(os.environ.get("TEMP") or "/tmp")), own)

    print("\n=== retired settings ===")
    for k in ("GI_FW_TOOLS", "GEN_INHIBIT_REPO", "VTRUX_DATA", "REVERSE_IT_ROOT"):
        check("check_retired refuses %s" % k, lambda: raises(lambda: gp.check_retired({k: "x"}), k))
        m, e = load(dict(base_env, **{k: "somewhere"}))
        check("import refuses %s" % k, lambda: m is None and k in str(e), e)
    check("check_retired passes with none set", lambda: gp.check_retired({"PATH": "x"}) is None)
    check("an empty retired value is not 'set'", lambda: gp.check_retired({"GI_FW_TOOLS": ""}) is None)
    # No usage line or docstring tells a reader to set a retired setting, which the tool
    # would then refuse (harness-defects row, 2026-10-24). baseline_host_run.sh exports
    # GEN_INHIBIT_REPO on the host, for the host suite there, not for these tools.
    tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    allowed = {("baseline_host_run.sh", "GEN_INHIBIT_REPO"), ("test_baseline_host_run.py", "GEN_INHIBIT_REPO")}
    hits = [h for h in retired_usage(tools, gp.RETIRED) if (h[0], h[2]) not in allowed]
    check("no file under tools/gen_inhibit sets a retired setting in a usage line", lambda: not hits, hits[:5])
    with open(os.path.join(root, "usage.py"), "w", encoding="utf-8") as fh:
        fh.write('"""\n    GI_FW_TOOLS=<firmware repo>/tools/gen_inhibit py -3.14 x.py\n"""\n')
    check("control: such a usage line is found",
          lambda: [h[0] for h in retired_usage(root, gp.RETIRED)] == ["usage.py"])
    os.remove(os.path.join(root, "usage.py"))

    print("\n=== pins ===")
    pins = write_pins(gp, root)
    gp.PINS_FILE = pins
    gp._checked.clear()
    cap = os.path.join(root, "projects", "vtrux", "logs", "cap.log")
    check("a pinned capture that matches passes", lambda: gp.capture("cap.log") == cap)
    check("a pinned DBC that matches passes", lambda: gp.vehicle_dbc("epri-pt-bus.dbc") and gp.diag_dbc())
    check("a pinned jammer build that matches passes", lambda: gp.jammer_builds("abc", "app.bin"))
    check("an unpinned file passes unchanged", lambda: gp.verify(os.path.join(root, "projects", "vtrux", "logs",
                                                                             "unpinned.log")))
    check("a path outside the root passes unchanged", lambda: gp.verify(__file__) == __file__)
    with open(cap, "ab") as fh:
        fh.write(b"one more line\n")
    check("a changed pinned capture is refused, naming it", lambda: raises(lambda: gp.capture("cap.log"),
                                                                           "projects/vtrux/logs/cap.log"))
    other_case = os.path.join(root, "projects", "VTRUX", "logs", "Cap.log")
    if os.path.normcase("A") == os.path.normcase("a"):
        check("... and spelt in another case, still refused", lambda: raises(lambda: gp.verify(other_case), "pinned"))
    tree = os.path.join(G, "runs", "fix")
    opened = []
    real_sha = gp.sha256_file

    def spy(p):
        opened.append(os.path.basename(p))
        return real_sha(p)
    gp.sha256_file = spy
    check("a pinned folder that matches passes", lambda: gp.pinned_tree(tree) == tree)
    check("... and its excluded file was never opened", lambda: "secret.pcapng" not in opened and opened, opened)
    with open(os.path.join(tree, "secret.pcapng"), "ab") as fh:
        fh.write(b"changed")
    check("a change to an excluded file is not refused", lambda: gp.pinned_tree(tree) == tree)
    with open(os.path.join(tree, "b.txt"), "ab") as fh:
        fh.write(b"changed")
    check("a changed file in a pinned folder is refused", lambda: raises(lambda: gp.pinned_tree(tree), "changed"))
    with open(os.path.join(tree, "b.txt"), "wb") as fh:
        fh.write(b"b\n")
    check("... restored, it passes again", lambda: gp.pinned_tree(tree) == tree)
    os.rename(os.path.join(tree, "b.txt"), os.path.join(tree, "c.txt"))
    check("a renamed file in a pinned folder is refused", lambda: raises(lambda: gp.pinned_tree(tree), "changed"))
    os.rename(os.path.join(tree, "c.txt"), os.path.join(tree, "b.txt"))
    with open(os.path.join(tree, "added.json"), "wb") as fh:
        fh.write(b"{}")
    check("an added file in a pinned folder is refused", lambda: raises(lambda: gp.pinned_tree(tree), "changed"))
    os.remove(os.path.join(tree, "added.json"))
    os.remove(os.path.join(tree, "a.json"))
    check("a removed file in a pinned folder is refused", lambda: raises(lambda: gp.pinned_tree(tree), "changed"))
    gp.sha256_file = real_sha
    check("a folder with no pin is refused", lambda: raises(lambda: gp.pinned_tree(os.path.join(G, "runs")), "no tree pin"))

    if "--real" in sys.argv:
        print("\n=== the real pins.json holds today ===")
        gp2, err = load(dict(base_env, REVERSE_IT_DIR=os.environ.get("REAL_REVERSE_IT_DIR", "")))
        os.environ["REVERSE_IT_DIR"] = os.environ.get("REAL_REVERSE_IT_DIR", "")
        real = json.load(open(os.path.join(SRC_DIR, "pins.json"), encoding="utf-8"))["files"]
        for rel, pin in sorted(real.items()):
            p = gp2.project(rel)
            if rel.endswith("/"):
                check("pin holds: %s" % rel, lambda: gp2.pinned_tree(p) == p)
            else:
                check("pin holds: %s" % rel, lambda: gp2.verify(p) == p)

    shutil.rmtree(work, ignore_errors=True)
    print("\nFAIL: %d check(s) failed" % len(FAILS) if FAILS else "\nPASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    if "--real" in sys.argv and not os.environ.get("REAL_REVERSE_IT_DIR"):
        os.environ["REAL_REVERSE_IT_DIR"] = os.environ.get("REVERSE_IT_DIR", "")
    sys.exit(main())
