#!/usr/bin/env python3
"""Test baseline_host_run.sh's project-folder lines (methodology, 2026-10-20).

The remote script sets $REVERSE_IT_DIR and $GEN_INHIBIT_REPO to $RROOT, and the
long-arm fixture step passes the folder to from_capture.py as --project-dir when
the archive has test/gen_inhibit_host/project_dir.py, else as --repo (the
commits before the rename).

RROOT reaches the remote script as one quoted word (host-run commit, 2026-10-22):
a root with spaces is one path in the exports, in from_capture's two arguments and
in the fixture-source hash, and a leading ~/ is the remote's $HOME.

A fake `ssh` goes first on PATH: it swallows the archive, saves the heredoc (the
remote script) and writes nothing else. Then the script's project-folder lines,
its long-arm step and its fixture-source hash are run in fake archive trees, one
with project_dir.py and one without, with `step` printing each argument in
brackets; then again for a root with spaces and a root under ~/. Nothing reaches
madhouse-debian. Needs the tester clone (FW, default ~/Git/wican-fw-vtrux-tester)
for `git archive`.

    py -3.14 -B tools/gen_inhibit/bench/test_baseline_host_run.py
    py -3.14 -B tools/gen_inhibit/bench/test_baseline_host_run.py --mutants

Run from the reverse-it root. Gen-inhibit tester.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "baseline_host_run.sh"
BASH = shutil.which("bash") or r"C:/Program Files/Git/usr/bin/bash.exe"
FW = os.environ.get("FW") or gi_paths.repo_root()
COMMIT = os.environ.get("GI_TEST_COMMIT", "2a1a0f1")
RROOT = "fake-rit-root"
SPACED = "fake rit/My Libraries"            # spaces, as NICKY-XPS's own root has (relative, made in the tree)
TILDE = "~/fake-rit"                        # as the settings' gi_host_root is written
CAPTURE_REL = "projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log"
CAPTURE_BODY = b"fake capture\n"
FAILS = []


def fw(p):
    return p.replace("\\", "/")


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


FAKE_SSH = r"""#!/usr/bin/env bash
# the second argument is the remote command; "bash -s" carries the script on stdin
case "$2" in
  "bash -s") cat > "$CAPTURE"; echo "=== exit 0 (fake)" ;;
  *) cat > /dev/null ;;
esac
exit 0
"""


def remote_script(script_dir, tmp, rroot=RROOT, tag="plain"):
    bin_ = os.path.join(tmp, "bin")
    if not os.path.isdir(bin_):
        os.makedirs(bin_)
        with open(os.path.join(bin_, "ssh"), "w", newline="\n") as fh:
            fh.write(FAKE_SSH)
    cap = os.path.join(tmp, "remote_%s.sh" % tag)
    e = dict(os.environ)
    e.update({"PATH": fw(bin_) + os.pathsep + e.get("PATH", ""), "CAPTURE": fw(cap), "FW": fw(FW),
              "RROOT": rroot, "PY": "PYTHON", "GI_OUT": fw(os.path.join(tmp, "out_" + tag)), "REMOTE": "nowhere",
              "MSYS_NO_PATHCONV": "1"})
    r = subprocess.run([BASH, fw(os.path.join(script_dir, SCRIPT)), COMMIT], capture_output=True, text=True,
                       env=e, timeout=300)
    s = open(cap, encoding="utf-8").read() if os.path.exists(cap) else ""
    return r.returncode, s, r.stdout + r.stderr


def run_lines(s, tree, home=None):
    """The project-folder lines, the long-arm step and the fixture-source hash of the
    remote script, in `tree` (HOME set to `home` when given)."""
    keep = []
    on = False
    for ln in s.splitlines():
        if ln.startswith("# The project folder"):
            on = True
        if on:
            keep.append(ln)
            if ln.startswith('echo "# project folder'):
                on = False
    keep += [ln for ln in s.splitlines() if ln.startswith("step longarm_fixture ")]
    keep += [ln for ln in s.splitlines() if ln.startswith('echo "fixture source sha256')]
    body = "\n".join(["step() { n=\"$1\"; shift; printf 'STEP %s:' \"$n\"; printf ' [%s]' \"$@\"; echo; }"] + keep +
                     ['echo "ENV REVERSE_IT_DIR=$REVERSE_IT_DIR GEN_INHIBIT_REPO=$GEN_INHIBIT_REPO"',
                      'echo "HOME IS $HOME"'])
    e = {k: v for k, v in os.environ.items() if k not in ("REVERSE_IT_DIR", "GEN_INHIBIT_REPO")}
    e["MSYS_NO_PATHCONV"] = "1"
    if home is not None:
        e["HOME"] = fw(home)
    r = subprocess.run([BASH, "-c", body], cwd=tree, capture_output=True, text=True, env=e, timeout=60)
    return r.stdout + r.stderr, len(keep)


def fake_root(base, rel):
    """A reverse-it root at base/rel holding the long-arm capture."""
    p = os.path.join(base, *rel.split("/"), *CAPTURE_REL.split("/"))
    os.makedirs(os.path.dirname(p))
    with open(p, "wb") as fh:
        fh.write(CAPTURE_BODY)


def checks(script_dir):
    want_sha = hashlib.sha256(CAPTURE_BODY).hexdigest()
    with tempfile.TemporaryDirectory(prefix="gi-host-run-", dir=gi_paths.scratch("tmp", run=False)) as tmp:
        rc, s, out = remote_script(script_dir, tmp)
        check("the script runs to the end with the fake ssh", rc == 0 and s, (rc, out[-300:]))
        new = os.path.join(tmp, "new", "test", "gen_inhibit_host")
        old = os.path.join(tmp, "old", "test", "gen_inhibit_host")
        os.makedirs(new)
        os.makedirs(old)
        open(os.path.join(new, "project_dir.py"), "w").close()
        out_new, n = run_lines(s, os.path.join(tmp, "new"))
        out_old, _ = run_lines(s, os.path.join(tmp, "old"))
        la_new = [ln for ln in out_new.splitlines() if ln.startswith("STEP longarm_fixture")]
        la_old = [ln for ln in out_old.splitlines() if ln.startswith("STEP longarm_fixture")]
        check("the long-arm step is in the remote script once", len(la_new) == 1 and len(la_old) == 1, (out_new, out_old))
        check("a commit with project_dir.py: from_capture gets --project-dir <RROOT>, not --repo",
              la_new and (" [--project-dir] [%s] " % RROOT) in la_new[0] and "--repo" not in la_new[0], la_new)
        check("a commit without it: from_capture gets --repo <RROOT>",
              la_old and (" [--repo] [%s] " % RROOT) in la_old[0] and "--project-dir" not in la_old[0], la_old)
        want = "ENV REVERSE_IT_DIR=%s GEN_INHIBIT_REPO=%s" % (RROOT, RROOT)
        check("both variables are exported as RROOT, for either kind of commit",
              want in out_new and want in out_old, (out_new[-200:], out_old[-200:]))
        check("the log records the folder and the flag",
              ("# project folder: %s (from_capture --project-dir)" % RROOT) in out_new and
              ("# project folder: %s (from_capture --repo)" % RROOT) in out_old, (out_new[-300:], out_old[-300:]))
        check("the exports come before the first step", 0 <= s.find("export REVERSE_IT_DIR") < s.find("\nstep make "),
              (s.find("export REVERSE_IT_DIR"), s.find("\nstep make ")))

        # a root with spaces: one path everywhere it is used
        rc, s2, out = remote_script(script_dir, tmp, SPACED, "spaced")
        check("a root with spaces: the script runs to the end", rc == 0 and s2, (rc, out[-300:]))
        sp = os.path.join(tmp, "spaced")
        os.makedirs(os.path.join(sp, "test", "gen_inhibit_host"))
        open(os.path.join(sp, "test", "gen_inhibit_host", "project_dir.py"), "w").close()
        fake_root(sp, SPACED)
        out_sp, _ = run_lines(s2, sp)
        la = [ln for ln in out_sp.splitlines() if ln.startswith("STEP longarm_fixture")]
        check("a root with spaces: from_capture gets the capture as one argument",
              la and (" [%s/%s] " % (SPACED, CAPTURE_REL)) in la[0], la or out_sp[-300:])
        check("a root with spaces: from_capture gets the folder as one argument after the flag",
              la and (" [--project-dir] [%s] [--channel] " % SPACED) in la[0], la or out_sp[-300:])
        check("a root with spaces: both variables are exported whole",
              ("ENV REVERSE_IT_DIR=%s GEN_INHIBIT_REPO=%s" % (SPACED, SPACED)) in out_sp, out_sp[-300:])
        check("a root with spaces: the fixture-source hash reads the capture",
              ("fixture source sha256: %s" % want_sha) in out_sp, out_sp[-300:])

        # a root under ~/: the remote's $HOME, not a literal ~
        rc, s3, out = remote_script(script_dir, tmp, TILDE, "tilde")
        check("a root under ~/: the script runs to the end", rc == 0 and s3, (rc, out[-300:]))
        tl = os.path.join(tmp, "tilde")
        home = os.path.join(tmp, "home dir")
        os.makedirs(os.path.join(tl, "test", "gen_inhibit_host"))
        open(os.path.join(tl, "test", "gen_inhibit_host", "project_dir.py"), "w").close()
        fake_root(home, TILDE[2:])
        out_tl, _ = run_lines(s3, tl, home)
        # bash's own spelling of HOME (Git Bash shows a temp folder as /tmp/...)
        seen = [ln[len("HOME IS "):] for ln in out_tl.splitlines() if ln.startswith("HOME IS ")]
        check("a root under ~/: the shell's HOME is the fake home",
              seen and seen[0].endswith("/" + os.path.basename(home)), seen)
        full = "%s/%s" % (seen[0] if seen else fw(home), TILDE[2:])
        la = [ln for ln in out_tl.splitlines() if ln.startswith("STEP longarm_fixture")]
        check("a root under ~/: both variables are $HOME/<rest>",
              ("ENV REVERSE_IT_DIR=%s GEN_INHIBIT_REPO=%s" % (full, full)) in out_tl, out_tl[-300:])
        check("a root under ~/: from_capture gets $HOME/<rest> for the capture and the folder",
              la and (" [%s/%s] " % (full, CAPTURE_REL)) in la[0] and (" [--project-dir] [%s] " % full) in la[0],
              la or out_tl[-300:])
        check("a root under ~/: the fixture-source hash reads the capture",
              ("fixture source sha256: %s" % want_sha) in out_tl, out_tl[-300:])


EXPORT = 'export REVERSE_IT_DIR="\\$RR" GEN_INHIBIT_REPO="\\$RR"'
LONGARM_CAP = '"\\$RR/projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log" \\$PDFLAG'
MUTANTS = [
    ("REVERSE_IT_DIR is not exported", EXPORT, 'export GEN_INHIBIT_REPO="\\$RR"'),
    ("GEN_INHIBIT_REPO is not exported (older commits)", EXPORT, 'export REVERSE_IT_DIR="\\$RR"'),
    ("the flag is always --repo", "then PDFLAG=--project-dir; else", "then PDFLAG=--repo; else"),
    ("the flag is always --project-dir", "else PDFLAG=--repo; fi", "else PDFLAG=--project-dir; fi"),
    ("the flag is tested on the wrong file", "[ -e test/gen_inhibit_host/project_dir.py ]",
     "[ -e test/gen_inhibit_host/from_capture.py ]"),
    ("the long-arm step keeps --repo", '\\$PDFLAG "\\$RR" --channel 0', '--repo "\\$RR" --channel 0'),
    ("the exports come after the steps", EXPORT + "\n", "\n"),
    # the quoting (host-run commit, 2026-10-22). No mutant unquotes the exports: bash does
    # not split an assignment's value, export's included, so that mutant is equivalent.
    ("from_capture's capture path is unquoted", LONGARM_CAP,
     '\\$RR/projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log \\$PDFLAG'),
    ("from_capture's folder is unquoted", '\\$PDFLAG "\\$RR" --channel 0', "\\$PDFLAG \\$RR --channel 0"),
    ("the fixture-source hash's path is unquoted",
     '< "\\$RR/projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log" | cut',
     "< \\$RR/projects/vtrux/notes/artifacts/gen-inhibit/runs/scottsvalley_armable_300s.log | cut"),
    ("RROOT is pasted in without printf %q", '*)     RQ=$(printf %q "$RROOT") ;;', '*)     RQ=$RROOT ;;'),
    ("a leading ~/ is quoted as a literal ~", '''  "~/"*) RQ='"$HOME"/'$(printf %q "${RROOT#"~/"}") ;;\n''', ""),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        checks(HERE)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = 0
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        with tempfile.TemporaryDirectory(prefix="gi-host-run-m%d-" % i, dir=gi_paths.scratch("tmp", run=False)) as tmp:
            src = open(os.path.join(HERE, SCRIPT), encoding="utf-8", newline="").read()
            if src.count(old) != 1:
                print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
                continue
            if name == "the exports come after the steps":
                new_src = src.replace(old, new).replace("step make make -s\n", "step make make -s\n" + old)
            else:
                new_src = src.replace(old, new)
            with open(os.path.join(tmp, SCRIPT), "w", encoding="utf-8", newline="") as fh:
                fh.write(new_src)
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--in-dir", tmp],
                               capture_output=True, text=True, timeout=600)
            fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
            crashed = r.returncode not in (0, 1) or "Traceback" in r.stderr
            if fails and not crashed:
                killed += 1
                print("KILLED   m%d %s  (%d FAIL; %s)" % (i, name, len(fails), fails[0][6:90]))
            else:
                print("%s m%d %s  (rc %d)" % ("CRASHED " if crashed else "SURVIVED", i, name, r.returncode))
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--in-dir"]:
        checks(sys.argv[2])
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
