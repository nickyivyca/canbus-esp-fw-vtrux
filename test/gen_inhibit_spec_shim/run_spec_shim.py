#!/usr/bin/env python3
"""Run the spec 12.4 shim cases as evidence: the product source they were built
from, the stimulus checked against the DBCs, the judge's own tests, then every
case, each in its own process. Since port step 0c (2026-10-25) also
build_id_test, the rows "0x7F3 carries the build id" (host half) and "the page
names the binary", into build_id.txt; its RESULT lines join the summary.

    python3 -B run_spec_shim.py --local --out <run folder> --dbc-dir <reverse-it>/projects/vtrux
        [--base e3d9437]                       (on madhouse-debian: the run that counts)
    py -3.14 -B run_spec_shim.py --out ... --dbc-dir ...   (from Windows, builds in WSL)

Only a run built on madhouse-debian with its gcc 14.2 counts for VERIFICATION.md;
a WSL build on NICKY-XPS is a self-check (user, 2026-10-24, through the reviewer).
Writes into --out (which must not exist): identity.json, payloads.txt,
judge_test.txt, cases.txt, build_id.txt and summary.txt. identity.json and the summary record
the host and compiler the build shell reported (host_env.py). Exit 0 only when
both were recorded, main/ is the --base commit's tree, nothing it builds is
modified, every payload decodes as the cases say, the judge's tests all pass and
every RESULT line is PASS. The binaries go in this run's own folder in the
worktree's scratch/. Gen-inhibit tester, 2026-10-20; host, compiler, --local and
the scratch folder 2026-10-24.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import host_env  # noqa: E402
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
BUILT = ["main/gen_inhibit.c", "main/gen_inhibit_core.c", "main/gi_sched.c", "main/can.c",
         "test/gen_inhibit_shim/fake_twai.c", "test/gen_inhibit_shim/fake_can.c"]


def git(*a):
    return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True, text=True).stdout.strip()


def payloads():
    """The interlock set as nonode_test.c declares it, read from the source so
    the check cannot drift from what the cases send."""
    src = open(os.path.join(HERE, "nonode_test.c"), encoding="utf-8").read()
    out = {}
    for name, body in re.findall(r"static const uint8_t (\w+)\[\d\]\s*=\s*\{([^}]*)\}", src):
        vals = []
        for tok in body.split(","):
            tok = tok.strip()
            if tok:
                vals.append(eval(tok, {"__builtins__": {}}) & 0xFF)   # literals and shifts only
        out[name] = vals
    return out


def check_payloads(dbc_dir, lines):
    import cantools
    pt = cantools.database.load_file(os.path.join(dbc_dir, "epri-pt-bus.dbc"), strict=False)
    ex = cantools.database.load_file(os.path.join(dbc_dir, "vtrux-powertrain-experimental.dbc"), strict=False)
    p = payloads()

    def dec(db, fid, name):
        m = db.get_message_by_frame_id(fid)
        data = (bytes(p[name]) + bytes(8))[:m.length]
        return db.decode_message(fid, data, decode_choices=False)
    checks = [
        ("KEY_ON: IgnitionKeyState 1", dec(pt, 0x592, "KEY_ON").get("IgnitionKeyState") == 1),
        ("KEY_OFF: IgnitionKeyState 0", dec(pt, 0x592, "KEY_OFF").get("IgnitionKeyState") == 0),
        ("CONT: bcm_mainc_stat 11 (closed)", dec(pt, 0x440, "CONT").get("bcm_mainc_stat") == 11),
        ("SOC: bms_soc_hires 50", dec(ex, 0x411, "SOC").get("bms_soc_hires") == 50.0),
        ("SHF: shift_lever_pos set", dec(ex, 0x639, "SHF").get("shift_lever_pos") not in (None, 0)),
        ("VCM: gen_rpm_ref < 0 (engine-off null)", dec(ex, 0x051, "VCM").get("gen_rpm_ref", 0) < 0),
        ("VCM: gen_torque_cmd 0", dec(ex, 0x051, "VCM").get("gen_torque_cmd") == 0),
        ("FLT: 0x617 B7 not 0xCA (raw byte; no DBC signal)", len(p["FLT"]) == 8 and p["FLT"][7] != 0xCA),
    ]
    for name, ok in checks:
        lines.append("%s %s" % ("ok  " if ok else "FAIL", name))
    return all(ok for _, ok in checks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dbc-dir", required=True)
    ap.add_argument("--base", default="e3d9437")
    ap.add_argument("--local", action="store_true", help="build in the local bash (a Linux host with gcc), not WSL")
    a = ap.parse_args()
    os.makedirs(a.out)
    fails = []

    def build(cmd):
        rc, out, err = host_env.sh(cmd, a.local, timeout=1800)
        return rc, out + err

    ident = {"head": git("rev-parse", "HEAD"), "main_tree": git("rev-parse", "HEAD:main"),
             "base": a.base, "base_main_tree": git("rev-parse", "%s:main" % a.base),
             "modified": git("status", "--porcelain", "--", "main", "test/gen_inhibit_shim"),
             "spec_shim_modified": git("status", "--porcelain", "--", "test/gen_inhibit_spec_shim"),
             "md5": {f: hashlib.md5(open(os.path.join(REPO, f), "rb").read()).hexdigest() for f in BUILT},
             "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ident.update(host_env.host_identity(a.local))
    if host_env.missing(ident):
        fails.append("not recorded: %s" % ", ".join(host_env.missing(ident)))
    ident["main_is_base"] = bool(ident["main_tree"]) and ident["main_tree"] == ident["base_main_tree"]
    if not ident["main_is_base"]:
        fails.append("main/ is not %s's tree" % a.base)
    if ident["modified"]:
        fails.append("main/ or the fakes are modified")

    lines = []
    if not check_payloads(a.dbc_dir, lines):
        fails.append("a payload does not decode as the cases say")
    open(os.path.join(a.out, "payloads.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")

    here = host_env.build_path(HERE, a.local)
    work = host_env.scratch_dir("spec_shim_run")
    jt = host_env.build_path(os.path.join(work, "judge_test"), a.local)
    nn = host_env.build_path(os.path.join(work, "nonode_test"), a.local)
    rc, out = build('cd "%s" && gcc -std=c11 -Wall -Wextra -I. -o "%s" judge_test.c nonode_judge.c '
                    '&& "%s"; r=$?; rm -f "%s"; exit $r' % (here, jt, jt, jt))
    open(os.path.join(a.out, "judge_test.txt"), "w", encoding="utf-8", newline="\n").write(out)
    if rc != 0 or " 0 FAIL" not in out:
        fails.append("judge_test failed (rc %s)" % rc)

    rc, out = build('cd "%s" && bash build.sh "%s" 2>&1 | grep -ci error; for c in $("%s"); '
                    'do echo "=== $c"; timeout 600 "%s" $c; echo "=== rc $?"; done; rm -f "%s"'
                    % (here, nn, nn, nn, nn))
    open(os.path.join(a.out, "cases.txt"), "w", encoding="utf-8", newline="\n").write(out)
    res = [l.split(" ", 4) for l in out.splitlines() if l.startswith("RESULT ")]
    if not res:
        fails.append("no RESULT line: the build or the run failed")

    bid = host_env.build_path(os.path.join(work, "build_id_test"), a.local)
    rc, out = build('cd "%s" && NN_TEST=build_id_test.c bash build.sh "%s" 2>&1 | grep -ci error; '
                    'timeout 600 "%s" build_id; echo "=== rc $?"; rm -f "%s"' % (here, bid, bid, bid))
    try:
        os.rmdir(work)
    except OSError:
        pass
    open(os.path.join(a.out, "build_id.txt"), "w", encoding="utf-8", newline="\n").write(out)
    bres = [l.split(" ", 4) for l in out.splitlines() if l.startswith("RESULT ")]
    if not bres:
        fails.append("no RESULT line from build_id_test: the build or the run failed")
    res += bres
    bad = [r for r in res if len(r) < 4 or r[3] != "PASS"]
    if bad:
        fails.append("%d RESULT line(s) not PASS" % len(bad))
    cases = sorted({r[1] for r in res})

    summary = ["spec 12.4 'a bus with no other node, emulated', and '0x7F3 carries the build id' (host half) "
               "with 'the page names the binary', run %s" % ident["started"],
               "head %s; main/ tree %s; %s's %s; main_is_base %s; modified: %s" % (
                   ident["head"][:12], ident["main_tree"][:12], a.base, ident["base_main_tree"][:12],
                   ident["main_is_base"], ident["modified"] or "nothing"),
               "harness modified: %s" % (ident["spec_shim_modified"] or "nothing"),
               host_env.describe(ident),
               "cases: %s" % ", ".join(cases), ""]
    for r in res:
        summary.append("%-12s %-6s %-17s %s" % (r[1], r[2], r[3], r[4] if len(r) > 4 else ""))
    summary += ["", "VERDICT: %s" % ("PASS" if not fails else "FAIL: " + "; ".join(fails))]
    ident["fails"] = fails
    json.dump(ident, open(os.path.join(a.out, "identity.json"), "w"), indent=1)
    open(os.path.join(a.out, "summary.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(summary) + "\n")
    print("\n".join(summary))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
