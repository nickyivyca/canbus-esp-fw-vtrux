#!/usr/bin/env python3
"""Test that gi_host_run.sh's --shim and --signals give the remote its REVERSE_IT_DIR.

The shim's Makefile (build_longarm.py) and test_signals.py (project_dir.py) take the
reverse-it folder from $REVERSE_IT_DIR with no default, and the shell an ssh command runs
in on madhouse-debian does not have it, so both modes stopped there unless it was set by
hand (port step 0d finding 5, 2026-10-26). RROOT names the folder on the remote.

gi_host_run.sh is run in a real bash with a fake ssh first on PATH, which keeps each
command it is given and runs nothing; LOCAL_PY is a fake that makes the export folder.
Then the mode's remote command is run in a bash with REVERSE_IT_DIR removed, HOME a
folder of this run, a fake make, and a shim_test or $PY that write down the
REVERSE_IT_DIR they see:

  1. --shim and --signals, RROOT with a space in it: the remote command sees exactly it.
  2. The same with RROOT ~/<folder with a space>: it sees the remote's $HOME/<folder>.
  3. --shim and --signals with RROOT unset: exit non-zero naming RROOT, before the sync
     (the fake ssh never called).
  4. Control: --sched with RROOT unset still runs (only the two modes need it), and a
     remote command without the export sees no REVERSE_IT_DIR (the check can fail).
  5. canre at the pinned commit (the canre-pin step, 2026-10-26): --shim's remote
     command runs $PY -B ~/gi_host/tools/bench/canre_pin.py install before make, and
     when that install fails, nothing after it runs.
  6. --canre, RROOT unset: exit 0, and its remote command runs that install and nothing
     else.
  7. The shared components (port steps 1a and 1b, 2026-10-31): the sync's remote command
     replaces ~/gi_host/shared/ before unpacking, and --shim gives the shim's make
     WICANDIR=$HOME/gi_host/shared/wican (the pinned vtrux/wican, with can_port and
     twai_port beside it). LOCAL_PY's fake answers wican_ref.py with a fake checkout of
     this run holding the three folders.

The sync is the real one, from this project folder, into the fake ssh; the export goes
to the worktree's scratch/gi-host-export, which every gi_host_run.sh run replaces.

    py -3.14 -B gen-inhibit/tools/bench/test_gi_host_run_env.py
    py -3.14 -B gen-inhibit/tools/bench/test_gi_host_run_env.py --mutants

--mutants runs the test on copies of gi_host_run.sh with one edit (GI_HOST_RUN_SH names
the copy, with host_identity.sh beside it). Gen-inhibit tester, 2026-10-26.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("GI_HOST_RUN_SH") or os.path.join(HERE, "gi_host_run.sh")
GIT_BASH = r"C:/Program Files/Git/usr/bin/bash.exe"
BASH = GIT_BASH if os.name == "nt" and os.path.exists(GIT_BASH) else shutil.which("bash")
FAILS = []
RUNS = []


def check(name, ok, detail=""):
    print("  %-4s %-70s %s" % ("ok" if ok else "FAIL", name, "" if ok else repr(detail)[:200]))
    if not ok:
        FAILS.append(name)


def fwd(p):
    return p.replace("\\", "/")


def script(path, body):
    with open(path, "w", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\n" + body)
    os.chmod(path, 0o755)


def setup(base):
    """The fakes, and a remote home with the two folders the modes cd into."""
    b = os.path.join(base, "bin")
    os.makedirs(b)
    # ssh: keep the last argument (the remote command) of each call in calls/<n>, drop stdin
    script(os.path.join(b, "ssh"), 'd="$FAKE_CALLS"; n=$(ls "$d" | wc -l); printf %s "${@: -1}" > "$d/$n"; '
                                   'cat > /dev/null; exit 0\n')
    # LOCAL_PY: wican_ref.py answers the fake shared checkout; anything else is the gi_diag export
    script(os.path.join(b, "fakepy"), 'case "${@: -1}" in wican_ref.py) printf "%s\\n" "$FAKE_WICAN" ;; '
                                      '*) mkdir -p "${@: -1}" ;; esac\n')
    script(os.path.join(b, "make"), 'echo "make $*" >> "$FAKE_LOG"\n')
    for comp in ("wican", "can_port", "twai_port"):
        os.makedirs(os.path.join(base, "sharedco", "shared", comp))
        open(os.path.join(base, "sharedco", "shared", comp, "README.md"), "w").close()
    # $PY: write down REVERSE_IT_DIR and its first argument, log its command line, and
    # fail when FAKE_PY_FAIL is set
    script(os.path.join(b, "sigpy"), 'printf "%s|%s" "${REVERSE_IT_DIR-UNSET}" "$1" > "$FAKE_SEEN"\n'
                                     'echo "py $*" >> "$FAKE_LOG"\n[ -z "${FAKE_PY_FAIL:-}" ]\n')
    home = os.path.join(base, "home")
    os.makedirs(os.path.join(home, "gi_host", "test", "gen_inhibit_host"))
    os.makedirs(os.path.join(home, "gi_host", "test", "gen_inhibit_shim"))
    script(os.path.join(home, "gi_host", "test", "gen_inhibit_shim", "shim_test"),
           'printf "%s|shim" "${REVERSE_IT_DIR-UNSET}" > "$FAKE_SEEN"\necho shim_test >> "$FAKE_LOG"\n')
    return b, home


def env_for(base, bindir, extra):
    e = {k: v for k, v in os.environ.items() if k not in ("REVERSE_IT_DIR", "RROOT", "FW", "REMOTE", "DEST", "PY")}
    e.update(PATH=fwd(bindir) + os.pathsep + os.environ.get("PATH", ""), MSYS_NO_PATHCONV="1",
             LOCAL_PY="fakepy", PY="sigpy", FW=fwd(gi_paths.project_root()),
             FAKE_WICAN=fwd(os.path.join(base, "sharedco", "shared", "wican")))
    e.update(extra)
    return e


def run_mode(base, bindir, mode, rroot):
    """(exit, stderr, [remote commands]) of gi_host_run.sh <mode>, RROOT unset if rroot is None."""
    RUNS.append(mode)
    calls = os.path.join(base, "calls_%d_%s" % (len(RUNS), mode.strip("-")))
    os.makedirs(calls)
    extra = {"FAKE_CALLS": fwd(calls)}
    if rroot is not None:
        extra["RROOT"] = rroot
    r = subprocess.run([BASH, fwd(SCRIPT), mode], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env=env_for(base, bindir, extra), timeout=300)
    cmds = [open(os.path.join(calls, n), encoding="utf-8").read() for n in sorted(os.listdir(calls), key=int)]
    return r.returncode, r.stderr, cmds


def remote(base, bindir, home, cmd, py_fails=False, log=None):
    """What the remote command's shim_test or $PY saw as REVERSE_IT_DIR, and the remote's $HOME;
    with log a list, the lines $PY, make and shim_test logged, in order, are put in it."""
    seen, logf = os.path.join(base, "seen.txt"), os.path.join(base, "log.txt")
    for f in (seen, logf):
        if os.path.exists(f):
            os.remove(f)
    extra = {"HOME": fwd(home), "FAKE_SEEN": fwd(seen), "FAKE_LOG": fwd(logf)}
    if py_fails:
        extra["FAKE_PY_FAIL"] = "1"
    e = env_for(base, bindir, extra)
    rhome = subprocess.run([BASH, "-c", 'printf %s "$HOME"'], capture_output=True, text=True, env=e).stdout
    subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, stdin=subprocess.DEVNULL, env=e, timeout=60)
    got = open(seen, encoding="utf-8").read() if os.path.exists(seen) else None
    if log is not None:
        log[:] = open(logf, encoding="utf-8").read().splitlines() if os.path.exists(logf) else []
    return got, rhome


def checks():
    base = gi_paths.scratch("test_gi_host_run_env")
    try:
        bindir, home = setup(base)
        tag = {"--shim": "shim", "--signals": "test_signals.py"}
        for mode in ("--shim", "--signals"):
            rc, err, cmds = run_mode(base, bindir, mode, "/srv/rev it/root")
            check("1 %s with RROOT: exit 0, the mode's command sent last" % mode, rc == 0 and len(cmds) >= 2,
                  (rc, err[-300:], len(cmds)))
            got, _ = remote(base, bindir, home, cmds[-1]) if cmds else (None, None)
            check("1 %s: the remote command sees REVERSE_IT_DIR = RROOT (with a space)" % mode,
                  got == "/srv/rev it/root|" + tag[mode], got)
            rc, err, cmds = run_mode(base, bindir, mode, "~/rev it")
            got, rhome = remote(base, bindir, home, cmds[-1]) if cmds else (None, "")
            check("2 %s with RROOT ~/rev it: the remote sees its $HOME/rev it" % mode,
                  rc == 0 and got == rhome + "/rev it|" + tag[mode], (rc, got, rhome))
            rc, err, cmds = run_mode(base, bindir, mode, None)
            check("3 %s with RROOT unset: exit non-zero naming RROOT, nothing sent" % mode,
                  rc != 0 and "RROOT" in err and cmds == [], (rc, err[-200:], len(cmds)))
        rc, err, cmds = run_mode(base, bindir, "--sched", None)
        check("4 control: --sched with RROOT unset runs (exit 0, its command sent)",
              rc == 0 and cmds and "gen_inhibit_sched" in cmds[-1], (rc, err[-200:], cmds[-1:]))
        got, _ = remote(base, bindir, home, "cd ~/gi_host/test/gen_inhibit_shim && ./shim_test")
        check("4 control: a remote command without the export sees no REVERSE_IT_DIR", got == "UNSET|shim", got)

        rc, err, cmds = run_mode(base, bindir, "--shim", "/srv/rit")
        log = []
        _, rhome = remote(base, bindir, home, cmds[-1] if cmds else "", log=log)
        install = "py -B %s/gi_host/tools/bench/canre_pin.py install" % rhome
        check("5 --shim: the pinned canre is installed and checked before make, then the shim runs",
              rc == 0 and log[:1] == [install] and [l.split()[0] for l in log[1:]] == ["make", "make", "shim_test"],
              (rc, log))
        check("7 --shim: make is given WICANDIR, the synced pinned vtrux/wican",
              log[1:3] == ["make -s clean", "make -s WICANDIR=%s/gi_host/shared/wican" % rhome], log)
        check("7 the sync replaces ~/gi_host/shared/ before unpacking",
              bool(cmds) and "rm -rf ~/gi_host/diag_schemas ~/gi_host/shared && tar xzf" in cmds[0], cmds[:1])
        remote(base, bindir, home, cmds[-1] if cmds else "", py_fails=True, log=log)
        check("5 --shim: when the canre install fails, nothing after it runs", log == [install], log)
        rc, err, cmds = run_mode(base, bindir, "--canre", None)
        check("6 --canre with RROOT unset: exit 0, its command sent", rc == 0 and len(cmds) >= 2, (rc, err[-200:], len(cmds)))
        remote(base, bindir, home, cmds[-1] if cmds else "", log=log)
        check("6 --canre: the remote command installs and checks the pinned canre, and does nothing else",
              log == [install], log)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return FAILS


MUTANTS = [
    ("--shim sends no export", '"${RX}$PY -B ~/$DEST/tools/bench/canre_pin.py install && cd ~/$DEST/test/gen_inhibit_shim',
     '"$PY -B ~/$DEST/tools/bench/canre_pin.py install && cd ~/$DEST/test/gen_inhibit_shim'),
    ("--signals sends no export", '"${RX}cd ~/$DEST/test/gen_inhibit_host', '"cd ~/$DEST/test/gen_inhibit_host'),
    ("RROOT not quoted", '      *)     RQ=$(printf %q "$RROOT") ;;\n    esac\n    RX=',
     '      *)     RQ=$RROOT ;;\n    esac\n    RX='),
    ("a leading ~/ quoted with the rest", '      "~/"*) RQ=\'"$HOME"/\'$(printf %q "${RROOT#"~/"}") ;;',
     '      "~/"*) RQ=$(printf %q "$RROOT") ;;'),
    ("RROOT given a default", 'RROOT="${RROOT:?set RROOT', 'RROOT="${RROOT:-/srv/default}"; : "${RROOT:?set RROOT'),
    ("the export only for --shim", "  shim|signals)\n    RROOT=", "  shim)\n    RROOT="),
    ("--shim without the canre install", '"${RX}$PY -B ~/$DEST/tools/bench/canre_pin.py install && cd ~/$DEST/test/gen_inhibit_shim',
     '"${RX}cd ~/$DEST/test/gen_inhibit_shim'),
    ("--shim builds even when the canre install fails", "canre_pin.py install && cd ~/$DEST/test/gen_inhibit_shim",
     "canre_pin.py install; cd ~/$DEST/test/gen_inhibit_shim"),
    ("--canre not a mode", "  --canre)         mode=canre;     shift ;;\n", ""),
    ("--shim make without WICANDIR", "make -s WICANDIR=\\$HOME/$DEST/shared/wican && ./shim_test", "make -s && ./shim_test"),
    ("the last shared export not replaced", "rm -rf ~/$DEST/diag_schemas ~/$DEST/shared && tar", "rm -rf ~/$DEST/diag_schemas && tar"),
    ("--canre checks instead of installing", '    ssh "$REMOTE" "$PY -B ~/$DEST/tools/bench/canre_pin.py install"\n',
     '    ssh "$REMOTE" "$PY -B ~/$DEST/tools/bench/canre_pin.py check"\n'),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        got = checks()
        print("\n%s" % ("all ok" if not got else "%d FAIL: %s" % (len(got), "; ".join(got))))
        return 1 if got else 0
    base = gi_paths.scratch("test_gi_host_run_env_mutants")
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            shutil.copy(os.path.join(HERE, "host_identity.sh"), d)
            p = os.path.join(d, "gi_host_run.sh")
            open(p, "w", encoding="utf-8", newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, GI_HOST_RUN_SH=p))
            fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            dead = bool(fails) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name,
                                   "".join("\n           " + f[:130] for f in fails[:2])))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
