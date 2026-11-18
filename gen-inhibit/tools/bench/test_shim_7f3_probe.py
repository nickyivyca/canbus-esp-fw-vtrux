#!/usr/bin/env python3
"""Test shim_7f3_probe.sh on a real synced tree, built and run on this host.

test_host_target.py part 6 checks the commands the probe sends, through fake shells; it
never runs the script the probe sends, which is how the probe went on looking for
fake_twai.c for two weeks after port step 1b removed it (found 2026-11-12, gen-inhibit
reviewer's tracker item; fixed 2026-11-17). This runs it: the project folder this test is
in goes to a scratch tree through gi_host_run.sh --sync-only, in local mode (REMOTE this
host), and the probe runs on that tree the same way, RROOT the reverse-it root as a
~/ path when it is under $HOME. Checks on the probe's run:

  1. exit 0, and its first line is the local mode line naming this host;
  2. the print is in the copy's shim_harness.c (the shared fake's tx-accepted hook, the
     grep -n line the probe prints), and no "probe site found";
  3. shim_test ran to the end (no "shim_test exit");
  4. at least one 0x7F3 payload with its count, eight bytes each;
  5. each distinct payload decoded by name (a "7F3 <payload>  schema <n>  <name>=" line),
     and no DECODE REFUSED;
  6. the synced tree's own shim folder unchanged (its files' hashes before and after).

A tree from before port step 1b (the shim's own fake_twai.c) is not built here: that
branch of the probe is the record site it had before, unchanged but for the insertion
(awk in place of sed).

    python3.13 -B gen-inhibit/tools/bench/test_shim_7f3_probe.py
    python3.13 -B gen-inhibit/tools/bench/test_shim_7f3_probe.py --can-fail
    python3.13 -B gen-inhibit/tools/bench/test_shim_7f3_probe.py --mutants

--can-fail also runs the probe as it was at GI_TEST_OLD_PROBE (default 48725311, the fix's
parent) on the same tree, which must fail a check. --mutants runs the probe beside this
test first, as the control, then copies with one edit each, all on one synced tree; no
mutant is judged when the control fails. Each probe run builds and runs the shim (about
70 s on madhouse-debian). Run it in build.slice. Gen-inhibit tester, 2026-11-17.
"""
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys

import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_FILES = ("shim_7f3_probe.sh", "host_target.sh", "host_identity.sh")
OLD_PROBE = os.environ.get("GI_TEST_OLD_PROBE", "48725311")
BASH = shutil.which("bash")

MUTANTS = [
    ("the print on another ID", "m->identifier == 0x7F3", "m->identifier == 0x7F2"),
    ("the old record site", "  line='    check_wire_accepted(m);'",
     "  line='    if (g_nsent < SENTMAX) g_sent[g_nsent++] = *f;'"),
    ("no WICANDIR", 'mk+=("WICANDIR=$HOME/$dest/shared/wican")', "mk+=()"),
    ("REVERSE_IT_DIR not exported", 'export REVERSE_IT_DIR="$rroot"', 'REVERSE_IT_DIR="$rroot"'),
    ("a leading ~/ in RROOT not expanded", 'case "$rroot" in "~/"*) rroot="$HOME/${rroot#"~/"}" ;; esac\n', ""),
    ("the print not put in", 'mv "$site.probe" "$site"', 'rm "$site.probe"'),
    ("the synced tree probed in place", '  site="$probe/shim_harness.c"', '  site="$src/shim_harness.c"'),
]


def env_for(dest, rroot):
    e = {k: v for k, v in os.environ.items() if k not in ("REMOTE", "FW", "DEST", "PY", "RROOT", "HOST_BASH")}
    e.update(REMOTE=socket.gethostname(), DEST=dest, RROOT=rroot, FW=gi_paths.project_root(),
             PYTHONDONTWRITEBYTECODE="1")
    return e


def shim_hashes(tree):
    out = {}
    for root, _, files in os.walk(tree):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, tree)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def judge(rc, out, before, after, me):
    """The failed checks of one probe run, as (name, detail)."""
    fails = []
    lines = out.splitlines()

    def check(name, ok, detail=""):
        if not ok:
            fails.append((name, detail))
    check("1. exit 0, first line the local mode line naming this host",
          rc == 0 and bool(lines) and lines[0].startswith("local mode: REMOTE=%s names this host" % me),
          (rc, lines[:1], out[-300:]))
    check("2. the print in the copy's shim_harness.c, no 'probe site found'",
          any(re.match(r"^\d+:    if \(m->identifier == 0x7F3\) fprintf\(stderr, \"PROBE7F3 ", ln) for ln in lines)
          and "probe site found" not in out, [ln for ln in lines if "PROBE7F3" in ln or "probe site" in ln][:3])
    check("3. shim_test ran to the end", "shim_test exit" not in out,
          [ln for ln in lines if "shim_test exit" in ln])
    pay = []
    if "0x7F3 payloads (count payload):" in lines:
        for ln in lines[lines.index("0x7F3 payloads (count payload):") + 1:]:
            m = re.match(r"^\s*(\d+) PROBE7F3 ([0-9a-f]{16})$", ln)
            if not m:
                break
            pay.append(m.group(2))
    check("4. at least one 0x7F3 payload, with its count", bool(pay), out[-400:])
    dec = [ln for ln in lines if re.match(r"^7F3 [0-9a-f]{16}\s+schema \d+\s+\w+=", ln)]
    check("5. each distinct payload decoded by name, no DECODE REFUSED",
          bool(pay) and sorted(ln.split()[1] for ln in dec) == sorted(set(pay)) and "DECODE REFUSED" not in out,
          (pay, dec, [ln for ln in lines if "REFUSED" in ln]))
    check("6. the synced tree's shim folder unchanged", before == after and bool(before),
          sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))[:5])
    return fails


def run_probe(probe_dir, dest, rroot, tree_shim, me):
    before = shim_hashes(tree_shim)
    r = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "--slice=build.slice", "--", BASH,
                        os.path.join(probe_dir, "shim_7f3_probe.sh"), dest],
                       capture_output=True, text=True, env=env_for(dest, rroot), timeout=900,
                       stdin=subprocess.DEVNULL)
    out = r.stdout + r.stderr
    return judge(r.returncode, out, before, shim_hashes(tree_shim), me), out


def copy_probe(d, edit=None):
    os.makedirs(d)
    for f in PROBE_FILES:
        shutil.copy(os.path.join(HERE, f), d)
    if edit:
        old, new = edit
        p = os.path.join(d, "shim_7f3_probe.sh")
        src = open(p, encoding="utf-8", newline="").read()
        if src.count(old) != 1:
            return "edit matched %d times" % src.count(old)
        open(p, "w", encoding="utf-8", newline="").write(src.replace(old, new))
    return None


def show(label, fails, out, verbose):
    print("%-9s %s" % ("PASS" if not fails else "FAIL", label))
    for name, detail in fails:
        print("          %s: %s" % (name, repr(detail)[:300]))
    if verbose and fails:
        print("          --- output tail\n" + "\n".join("          " + x for x in out.splitlines()[-12:]))


def main():
    args = sys.argv[1:]
    home = os.path.expanduser("~")
    base = gi_paths.scratch("test_shim_7f3_probe")
    tree = os.path.join(base, "t")
    dest = os.path.relpath(tree, home)
    if dest.startswith(".."):
        print("refused: the scratch folder %s is not under $HOME, where the probe's DEST lives" % base)
        return 2
    rit = gi_paths.reverse_it_dir()
    rroot = "~/" + os.path.relpath(rit, home) if not os.path.relpath(rit, home).startswith("..") else rit
    me = socket.gethostname()
    print("sync %s -> ~/%s (local mode), RROOT %s" % (gi_paths.project_root(), dest, rroot))
    r = subprocess.run([BASH, os.path.join(HERE, "gi_host_run.sh"), "--sync-only"], capture_output=True, text=True,
                       env=env_for(dest, rroot), timeout=600, stdin=subprocess.DEVNULL)
    tree_shim = os.path.join(tree, "test", "gen_inhibit_shim")
    if r.returncode != 0 or not os.path.isfile(os.path.join(tree_shim, "shim_harness.c")):
        print("refused: the sync failed (exit %d), nothing judged\n%s" % (r.returncode, (r.stdout + r.stderr)[-800:]))
        return 2
    d0 = os.path.join(base, "probe")
    copy_probe(d0)
    fails, out = run_probe(d0, dest, rroot, tree_shim, me)
    show("the probe beside this test" + (" (the control)" if "--mutants" in args else ""), fails, out, True)
    for ln in out.splitlines():
        if re.match(r"^\s*\d+ PROBE7F3 |^7F3 ", ln):
            print("          | " + ln.strip()[:160])
    bad = bool(fails)
    if "--can-fail" in args:
        dold = os.path.join(base, "old")
        os.makedirs(dold)
        for f in PROBE_FILES:
            body = subprocess.run(["git", "-C", HERE, "show", "%s:gen-inhibit/tools/bench/%s" % (OLD_PROBE, f)],
                                  capture_output=True).stdout
            open(os.path.join(dold, f), "wb").write(body)
        ofails, oout = run_probe(dold, dest, rroot, tree_shim, me)
        print("%-9s can-fail: the probe at %s fails %d check(s)%s" % (
            "PASS" if ofails else "FAIL", OLD_PROBE, len(ofails),
            "".join("\n          " + n for n, _ in ofails) + "".join(
                "\n          | " + x for x in oout.splitlines() if "probe site" in x)))
        bad = bad or not ofails
    if "--mutants" in args:
        if fails:
            print("\nthe control FAILS: no mutant is judged")
            return 1
        killed = errors = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            d = os.path.join(base, "m%d" % n)
            err = copy_probe(d, (old, new))
            if err:
                print("ERROR     m%d %s: %s" % (n, name, err))
                errors += 1
                continue
            mfails, _ = run_probe(d, dest, rroot, tree_shim, me)
            killed += bool(mfails)
            print("%s m%d %s%s" % ("KILLED   " if mfails else "SURVIVED ", n, name,
                                   "".join("\n          " + x for x, _ in mfails)))
        print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
        bad = bad or killed != len(MUTANTS) or errors
    print("\n%s" % ("all ok" if not bad else "FAILED"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
