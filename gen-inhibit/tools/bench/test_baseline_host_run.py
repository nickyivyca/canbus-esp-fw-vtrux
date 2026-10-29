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
madhouse-debian.

The archive (2026-10-25): the fake ssh saves what the copy's tar would unpack. A
commit with the bench's gi_diag.py ships it, pins.json and the diag_schemas/ export beside
main/ and test/; the old commit ships main/ and test/ only. The test_cfg_build_id step
runs where the archive has the test, and not where it does not.

Each commit at its own layout (port step 0d, 2026-10-26): a commit before the move
(GI_TEST_ROOT_COMMIT, default e7263bb) gives a copy with main/, test/ and
tools/gen_inhibit/bench/; a commit from the move on (GI_TEST_DECODE_COMMIT, default this
tree's HEAD, so run it at a merge of both halves of the move) gives firmware/main/, test/
and tools/bench/, and no main/ at the root; a commit with only the tester's half of the
move (c6c7fe4) is refused. The remote script takes the WiCAN sources from firmware/main/
where the copy has it, else main/. Needs a gen-inhibit worktree (FW, default this project
folder, gi_paths.project_root()) for `git archive`.

canre and the stock headers (2026-10-29, found on the step 1a acceptance's first emulated
run, where every replay regen, the long-arm fixture and the shims failed on them): a commit
with tools/bench/canre_pin.py ships it, and the remote script runs a canre_install step
before replays_regen; a commit with the shim's wican_ref.py (port step 1a on,
GI_TEST_DECODE_COMMIT) ships shared/wican at the tag its idf_component.yml pins, byte for
byte, at shared-wican/, and the remote script exports WICANDIR to it. A project-layout
commit before 1a (GI_TEST_PRE1A_COMMIT, default 0b03841) has canre_pin.py and ships no
headers; the root-layout commit has neither.

    py -3.14 -B gen-inhibit/tools/bench/test_baseline_host_run.py
    py -3.14 -B gen-inhibit/tools/bench/test_baseline_host_run.py --mutants

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
FW = os.environ.get("FW") or gi_paths.project_root()
COMMIT = os.environ.get("GI_TEST_COMMIT", "2a1a0f1")
DECODE_COMMIT = os.environ.get("GI_TEST_DECODE_COMMIT", "HEAD")    # the project layout, with gi_diag.py
ROOT_COMMIT = os.environ.get("GI_TEST_ROOT_COMMIT", "e7263bb")      # the root layout, with gi_diag.py
PART_COMMIT = "c6c7fe4"                                             # only the tester's half of the move
PRE1A_COMMIT = os.environ.get("GI_TEST_PRE1A_COMMIT", "0b03841")    # the project layout, canre pinned, before 1a
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
  *"tar xf"*) if [ -n "$ARCHIVE" ]; then cat > "$ARCHIVE"; else cat > /dev/null; fi ;;
  *) cat > /dev/null ;;
esac
exit 0
"""


def remote_script(script_dir, tmp, rroot=RROOT, tag="plain", commit=None):
    bin_ = os.path.join(tmp, "bin")
    if not os.path.isdir(bin_):
        os.makedirs(bin_)
        with open(os.path.join(bin_, "ssh"), "w", newline="\n") as fh:
            fh.write(FAKE_SSH)
    cap = os.path.join(tmp, "remote_%s.sh" % tag)
    arc = os.path.join(tmp, "archive_%s.tar" % tag)
    e = dict(os.environ)
    e.update({"PATH": fw(bin_) + os.pathsep + e.get("PATH", ""), "CAPTURE": fw(cap), "ARCHIVE": fw(arc), "FW": fw(FW),
              "RROOT": rroot, "PY": "PYTHON", "GI_OUT": fw(os.path.join(tmp, "out_" + tag)), "REMOTE": "nowhere",
              "MSYS_NO_PATHCONV": "1"})
    r = subprocess.run([BASH, fw(os.path.join(script_dir, SCRIPT)), commit or COMMIT], capture_output=True, text=True,
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


def archive_names(tmp, tag):
    """The members of the archive the fake ssh saved for `tag`, without the leading ./"""
    import tarfile
    p = os.path.join(tmp, "archive_%s.tar" % tag)
    if not os.path.exists(p) or not os.path.getsize(p):
        return []
    with tarfile.open(p) as t:
        return [n[2:] if n.startswith("./") else n for n in t.getnames()]


def cfg_step(s, tree):
    """The test_cfg_build_id lines of the remote script, run in `tree` with `step` printing."""
    keep = [ln for ln in s.splitlines() if "test_cfg_build_id" in ln and not ln.startswith("#")]
    body = "\n".join(["step() { printf 'STEP %s\\n' \"$1\"; }"] + keep)
    r = subprocess.run([BASH, "-c", body], cwd=tree, capture_output=True, text=True, timeout=60)
    return r.stdout + r.stderr, len(keep)


def guarded(s, word, tree, home=None, extra=()):
    """The remote script's one-line `if [ ... ]; then ...; fi` lines naming `word`, run in
    `tree` with `step` printing its name (HOME set to `home` when given)."""
    keep = [ln for ln in s.splitlines() if ln.startswith("if [ ") and word in ln]
    body = "\n".join(["step() { printf 'STEP %s\\n' \"$1\"; }"] + keep + list(extra))
    e = dict(os.environ)
    e["MSYS_NO_PATHCONV"] = "1"
    if home is not None:
        e["HOME"] = fw(home)
    r = subprocess.run([BASH, "-c", body], cwd=tree, capture_output=True, text=True, env=e, timeout=60)
    return r.stdout + r.stderr, len(keep)


def git_show(ref):
    r = subprocess.run(["git", "-C", FW, "show", ref], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def tar_member(tmp, tag, name):
    import tarfile
    p = os.path.join(tmp, "archive_%s.tar" % tag)
    if not os.path.exists(p) or not os.path.getsize(p):
        return None
    with tarfile.open(p) as t:
        for m in t.getmembers():
            if m.name in (name, "./" + name):
                return t.extractfile(m).read()
    return None


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
        names = archive_names(tmp, "plain")
        check("an old commit (%s): the copy holds main/ and test/, and no tools/ or diag_schemas/" % COMMIT,
              any(n.startswith("main/") for n in names) and any(n.startswith("test/") for n in names)
              and not any(n.startswith(("tools/", "diag_schemas")) for n in names), sorted(set(n.split("/")[0] for n in names)))
        rc5, s5, out5 = remote_script(script_dir, tmp, tag="rootdecode", commit=ROOT_COMMIT)
        names = archive_names(tmp, "rootdecode")
        check("a root-layout commit with gi_diag.py (%s): the script runs to the end" % ROOT_COMMIT,
              rc5 == 0 and s5, (rc5, out5[-300:]))
        check("a root-layout commit with gi_diag.py: the copy holds gi_diag.py and pins.json in tools/gen_inhibit/bench/",
              "tools/gen_inhibit/bench/gi_diag.py" in names and "tools/gen_inhibit/bench/pins.json" in names,
              [n for n in names if n.startswith("tools/")])
        check("a root-layout commit with gi_diag.py: the diag_schemas export at the copy's root, main/ and test/ too",
              "diag_schemas/manifest.json" in names and any(n.startswith("main/") for n in names)
              and any(n.startswith("test/") for n in names) and not any(n.startswith("firmware/") for n in names),
              sorted(set(n.split("/")[0] for n in names)))
        rc4, s4, out4 = remote_script(script_dir, tmp, tag="decode", commit=DECODE_COMMIT)
        names = archive_names(tmp, "decode")
        check("a project-layout commit (%s): the script runs to the end" % DECODE_COMMIT, rc4 == 0 and s4, (rc4, out4[-300:]))
        check("a project-layout commit: the copy holds gi_diag.py and pins.json in tools/bench/",
              "tools/bench/gi_diag.py" in names and "tools/bench/pins.json" in names,
              [n for n in names if n.startswith("tools/")])
        check("a project-layout commit: the copy holds the diag_schemas export at its root",
              "diag_schemas/manifest.json" in names, [n for n in names if n.startswith("diag")])
        check("a project-layout commit: the copy holds firmware/main/ and test/, and no main/ at its root",
              "firmware/main/gen_inhibit_core.c" in names and "test/gen_inhibit_host/Makefile" in names
              and "test/gen_inhibit_shim/Makefile" in names and not any(n.startswith("main/") for n in names),
              sorted(set("/".join(n.split("/")[:2]) for n in names)))
        # canre at the pinned commit, and vtrux/wican's headers (2026-10-29)
        check("a project-layout commit: the copy holds tools/bench/canre_pin.py",
              "tools/bench/canre_pin.py" in names, [n for n in names if n.startswith("tools/")])
        check("the canre_install step comes before replays_regen and the long-arm fixture",
              0 <= s4.find("step canre_install") < s4.find("step replays_regen") < s4.find("step longarm_fixture"),
              (s4.find("step canre_install"), s4.find("step replays_regen")))
        empty = os.path.join(tmp, "guard_tree")
        os.makedirs(os.path.join(empty, "shared-wican"))
        open(os.path.join(empty, "shared-wican", "can.h"), "w").close()
        out_c, nc = guarded(s4, "canre_install", empty)
        check("a commit with canre_pin.py: the canre_install step runs, from the copy's tools/bench/",
              nc == 1 and out_c.strip() == "STEP canre_install"
              and "$PY -B tools/bench/canre_pin.py install" in s4.replace("PYTHON", "$PY"), (nc, out_c, s4[-200:]))
        sys.path.insert(0, os.path.join(FW, "test", "gen_inhibit_shim"))
        import wican_ref
        man = git_show("%s:gen-inhibit/firmware/main/idf_component.yml" % DECODE_COMMIT)
        wtag = wican_ref.pinned_tag(man.decode()) if man else None
        for h in ("can.h", "comm_server.h"):
            want_h = git_show("%s:shared/wican/%s" % (wtag, h)) if wtag else None
            got_h = tar_member(tmp, "decode", "shared-wican/" + h)
            check("a commit from 1a on: the copy holds shared/wican's %s at the pinned tag (%s), byte for byte" % (h, wtag),
                  want_h is not None and got_h == want_h, (wtag, got_h is None))
        home = os.path.join(tmp, "wican home")
        out_w, nw = guarded(s4, "WICANDIR", empty, home, ['echo "WICANDIR IS $WICANDIR"'])
        seen_w = [ln[len("WICANDIR IS "):] for ln in out_w.splitlines() if ln.startswith("WICANDIR IS ")]
        check("a commit from 1a on: WICANDIR is exported as $HOME/<copy>/shared-wican",
              nw == 1 and seen_w and seen_w[0].endswith("/gi_baseline_%s/shared-wican" % DECODE_COMMIT)
              and os.path.basename(home) in seen_w[0], (nw, out_w))
        check("... before the first step", 0 <= s4.find("export WICANDIR") < s4.find("\nstep make "),
              (s4.find("export WICANDIR"), s4.find("\nstep make ")))
        rc7, s7, out7 = remote_script(script_dir, tmp, tag="pre1a", commit=PRE1A_COMMIT)
        names7 = archive_names(tmp, "pre1a")
        out7w, _ = guarded(s7, "WICANDIR", empty, home, ['echo "WICANDIR IS [$WICANDIR]"'])
        out7c, _ = guarded(s7, "canre_install", empty)
        check("a project-layout commit before 1a (%s): runs to the end, ships canre_pin.py and no headers" % PRE1A_COMMIT,
              rc7 == 0 and s7 and "tools/bench/canre_pin.py" in names7
              and not any(n.startswith("shared-wican") for n in names7), (rc7, out7[-300:]))
        check("... the canre_install step runs, and WICANDIR is not set",
              out7c.strip() == "STEP canre_install" and "WICANDIR IS []" in out7w, (out7c, out7w))
        names5 = archive_names(tmp, "rootdecode")
        out5w, _ = guarded(s5, "WICANDIR", empty, home, ['echo "WICANDIR IS [$WICANDIR]"'])
        out5c, _ = guarded(s5, "canre_install", empty)
        check("a root-layout commit: no canre_pin.py and no headers shipped, no canre_install step, no WICANDIR",
              not any(n.endswith("canre_pin.py") or n.startswith("shared-wican") for n in names5)
              and out5c.strip() == "" and "WICANDIR IS []" in out5w, (out5c, out5w))
        rc6, s6, out6 = remote_script(script_dir, tmp, tag="part", commit=PART_COMMIT)
        check("a commit with only part of the move (%s) is refused, before anything is sent" % PART_COMMIT,
              rc6 == 2 and not s6 and not archive_names(tmp, "part") and "neither layout" in out6, (rc6, out6[-300:]))
        # the remote script takes the WiCAN sources from the copy's own layout
        for label, sub, want in (("a project-layout copy", "firmware/main", "firmware/main"), ("a root-layout copy", "main", "main")):
            tree = os.path.join(tmp, "M_" + sub.replace("/", "_"))
            os.makedirs(os.path.join(tree, *sub.split("/")))
            keep = [ln for ln in s4.splitlines() if ln.startswith("M=")]
            r = subprocess.run([BASH, "-c", "\n".join(keep + ['echo "M IS $M"'])], cwd=tree, capture_output=True,
                               text=True, timeout=60)
            check("%s: the WiCAN sources are read from %s/" % (label, want),
                  len(keep) == 1 and r.stdout.strip() == "M IS %s" % want, (keep, r.stdout, r.stderr[-200:]))
        check("the source hashes read the WiCAN sources through $M",
              "sha256sum $M/gen_inhibit_core.c $M/gen_inhibit.c $M/gi_sched.c $M/can.c" in s4, "")
        has = os.path.join(tmp, "cfg_has")
        hasnt = os.path.join(tmp, "cfg_hasnt")
        os.makedirs(has)
        os.makedirs(hasnt)
        open(os.path.join(has, "test_cfg_build_id.py"), "w").close()
        out_has, nk = cfg_step(s4, has)
        out_hasnt, _ = cfg_step(s4, hasnt)
        check("test_cfg_build_id: one line in the remote script", nk == 1, nk)
        check("test_cfg_build_id: a step where the archive has the test", out_has.strip() == "STEP test_cfg_build_id",
              out_has)
        check("test_cfg_build_id: no step where it does not", out_hasnt.strip() == "", out_hasnt)
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
    # the archive and the build-id step (2026-10-25)
    ("gi_diag.py and pins.json not shipped",
     '  for f in gi_diag.py pins.json; do git -C "$TOP" show "$C:$BENCH/$f" > "$STAGE/$BENCH_AT/$f" || exit 2; done\n', ""),
    ("the export not made", '  ${LOCAL_PY:-py -3.14} -B "$TOP/gen-inhibit/tools/bench/gi_diag.py" export "$STAGE/diag_schemas" >/dev/null || exit 2\n', ""),
    ("the decode files shipped for every commit", 'if tree "$BENCH/gi_diag.py"; then', 'if true; then'),
    # each commit at its own layout (port step 0d, 2026-10-26)
    ("the project layout never chosen", "if tree gen-inhibit/firmware/main && tree gen-inhibit/test; then", "if false; then"),
    ("a project commit's main/ at the copy's root", "put gen-inhibit/firmware/main firmware/main", "put gen-inhibit/firmware/main main"),
    ("a project commit's bench in the old place", "BENCH_AT=tools/bench\n", "BENCH_AT=tools/gen_inhibit/bench\n"),
    ("a part-moved commit taken as the root layout", "elif tree main && tree test && ! tree gen-inhibit/test; then",
     "elif tree main && tree test; then"),
    ("the remote script always reads main/", "M=main; [ -d firmware/main ] && M=firmware/main", "M=main"),
    ("the source hashes read main/ whatever the layout", "sha256sum \\$M/gen_inhibit_core.c", "sha256sum main/gen_inhibit_core.c"),
    ("the build-id step not guarded", "if [ -e test_cfg_build_id.py ]; then step test_cfg_build_id $PY test_cfg_build_id.py; fi",
     "step test_cfg_build_id $PY test_cfg_build_id.py"),
    ("the build-id step dropped", "if [ -e test_cfg_build_id.py ]; then step test_cfg_build_id $PY test_cfg_build_id.py; fi\n", ""),
    # canre and the stock headers (2026-10-29)
    ("canre_pin.py not shipped",
     '  git -C "$TOP" show "$C:$BENCH/canre_pin.py" > "$STAGE/$BENCH_AT/canre_pin.py" || exit 2\n', ""),
    ("the canre_install step dropped",
     "if [ $CANRE = 1 ]; then step canre_install $PY -B $BENCH_AT/canre_pin.py install; fi\n", ""),
    ("the canre_install step for every commit", "if [ $CANRE = 1 ]; then step canre_install",
     "if [ 1 = 1 ]; then step canre_install"),
    ("the headers shipped for every commit", 'if [ -e "$STAGE/test/gen_inhibit_shim/wican_ref.py" ]; then', "if true; then"),
    ("the headers from another folder", 'git -C "$TOP" archive "$WTAG:shared/wican" |',
     'git -C "$TOP" archive "$WTAG:shared/debug_logs" |'),
    ("WICANDIR not exported", 'export WICANDIR="\\$HOME/$DEST/shared-wican"; ', ""),
    ("WICANDIR names the copy's root", 'export WICANDIR="\\$HOME/$DEST/shared-wican";',
     'export WICANDIR="\\$HOME/$DEST";'),
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
