#!/usr/bin/env python3
"""Test b6_ecc_run.sh with fakes for everything that would touch the bench.

A temporary tree holds a copy of the script, a fake baseline_b_jam.sh (custody writes a
dump record and a jammer id record; restore logs its argument) and a bench-holder.md
with a made-up SSID. A fake `py` first on PATH logs its argv and prints the SSID, so the
redaction can be seen. Checks the three threshold calls, the custody/restore order, the
refusals and the redaction.

    py -3.14 -B gen-inhibit/tools/bench/test_b6_ecc_run.py
    py -3.14 -B gen-inhibit/tools/bench/test_b6_ecc_run.py --mutants

Gen-inhibit tester, 2026-10-20.
"""
import os
import re
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "b6_ecc_run.sh"
BASH = shutil.which("bash") or r"C:/Program Files/Git/usr/bin/bash.exe"
A = "projects/vtrux/notes/artifacts/gen-inhibit"
SHA = "0123456789abcdef" * 4
SSID = "WiCAN_" + "0a1b2c3d4e5f"   # made up
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


FAKE_PY = r"""#!/usr/bin/env bash
echo "py $*" >> "$LOG"
echo "fake run for $GI_WICAN_SSID"
echo "arm 0 (replay 100.0/s): PASS"
exit "${FAKE_PY_RC:-0}"
"""

FAKE_JAM = r"""#!/usr/bin/env bash
echo "jam $*" >> "$LOG"
case "$1" in
  custody)
    mkdir -p "$GI_RUNS_DIR/x2"
    echo '{}' > "$GI_RUNS_DIR/x2_flash_custody_dump_20261020T000000.json"
    if [ "${FAKE_BANNER:-1}" = 1 ]; then echo '{"banner_ok": true}' > "$GI_RUNS_DIR/x2_jammer_id_20261020T000001.json"
    else echo '{"banner_ok": false}' > "$GI_RUNS_DIR/x2_jammer_id_20261020T000001.json"; fi
    echo "custody for $GI_WICAN_SSID" > "$GI_RUNS_DIR/x2/custody_dump.txt"
    exit "${FAKE_CUSTODY_RC:-0}" ;;
  restore) exit 0 ;;
esac
"""


def required():
    """The variables the REAL baseline_b_jam.sh refuses without (its ${NAME:?...}). The
    fake requires the same ones, so a variable the run script forgets fails here as it
    would on the bench (2026-10-20: GI_COMMIT was missed while the fake asked nothing)."""
    src = open(os.path.join(HERE, "baseline_b_jam.sh"), encoding="utf-8").read()
    return sorted(set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?", src)))


def fake_jam():
    head = "".join(': "${%s:?the real baseline_b_jam.sh requires %s}"\n' % (n, n) for n in required())
    # After the log line, so a custody call that then fails on a missing variable is seen.
    line = 'echo "jam $*" >> "$LOG"\n'
    return FAKE_JAM.replace(line, line + head, 1)


def fw(p):
    return p.replace("\\", "/")


def tree(tmp, script_src):
    root = os.path.join(tmp, "root")
    a = os.path.join(root, *A.split("/"))
    os.makedirs(a)
    os.makedirs(os.path.join(root, "firmware"))
    with open(os.path.join(a, SCRIPT), "w", newline="\n") as fh:
        fh.write(script_src)
    with open(os.path.join(a, "baseline_b_jam.sh"), "w", newline="\n") as fh:
        fh.write(fake_jam())
    with open(os.path.join(root, "firmware", "bench-holder.md"), "w") as fh:
        fh.write("The WiCAN's SoftAP `%s` (test).\n" % SSID)
    b = os.path.join(tmp, "bin")
    os.makedirs(b)
    with open(os.path.join(b, "py"), "w", newline="\n") as fh:
        fh.write(FAKE_PY)
    return root, b


def run(script_src, **env_over):
    tmp = tempfile.mkdtemp(prefix="gi-b6ecc-run-", dir=gi_paths.scratch("tmp", run=False))
    try:
        root, b = tree(tmp, script_src)
        log = os.path.join(tmp, "log.txt")
        e = {k: v for k, v in os.environ.items() if k not in ("GI_WICAN_SSID", "GI_OUT", "GI_RUNS_DIR")}
        e.update({"PATH": fw(b) + os.pathsep + e.get("PATH", ""), "LOG": fw(log), "GI_ECC_SHA": SHA,
                  "INTERPOSER_CODE": "fake", "GI_COMMIT": "8abf16b", "REVERSE_IT_DIR": fw(root), "GI_OUT": A + "/runs/b6_ecc_test", "MSYS_NO_PATHCONV": "1"})
        for k, v in env_over.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        r = subprocess.run([BASH, "%s/%s" % (A, SCRIPT)], cwd=root, capture_output=True, text=True, env=e, timeout=120)
        lines = open(log).read().splitlines() if os.path.exists(log) else []
        out_dir = os.path.join(root, *A.split("/"), "runs", "b6_ecc_test")
        texts = {}
        for dp, _dn, fns in os.walk(out_dir):
            for f in fns:
                if f.endswith(".txt"):
                    texts[os.path.relpath(os.path.join(dp, f), out_dir)] = open(os.path.join(dp, f)).read()
        return r.returncode, r.stdout + r.stderr, lines, texts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def checks(src):
    check("the real baseline_b_jam.sh's required variables are read, GI_COMMIT among them",
          "GI_COMMIT" in required(), required())
    rc, out, log, texts = run(src)
    calls = [l for l in log if l.startswith("py ")]
    check("the session runs to the end", rc == 0 and "done" in out, (rc, out[-300:]))
    check("custody first, restore last, with the dump record",
          log[:1] == ["jam custody"] and log[-1:] and log[-1].startswith("jam restore ") and
          log[-1].endswith("x2_flash_custody_dump_20261020T000000.json"), log)
    check("three threshold calls, between custody and restore", len(calls) == 3 and log[1:4] == calls, log)
    want = [("7", "1.5"), ("7", "5"), ("6", "8.5")]
    got = []
    for c in calls:
        w = c.split()
        got.append((w[w.index("--arms") + 1] if "--arms" in w else None, w[w.index("--lead-s") + 1] if "--lead-s" in w else None))
    check("arms 7, 7, 6 at leads 1.5, 5, 8.5 s (20 arms)", got == want, got)
    need = ["threshold", "--n 29", "--judge-first 10", "--interval-ms 1000", "--reset-wican", "--ecc",
            "--want-sha %s" % SHA, "--no-wican-console"]
    check("each call: 29 pulses at 1000 ms, judged on the first 10, /gi_ecc, a reset per arm, the _ecc sha, "
          "no WiCAN console",
          calls and all(all((" %s " % n) in (c + " ") for n in need) for c in calls), calls)
    check("each call runs x2_jam_cases.py", calls and all("/%s/x2_jam_cases.py " % A in c for c in calls), calls)
    check("the SSID is redacted in the outputs, and kept nowhere",
          texts and all(SSID not in t for t in texts.values()) and
          any("<GI_WICAN_SSID>" in t for k, t in texts.items() if k.startswith("B6_ecc_")) and
          any("<GI_WICAN_SSID>" in t for k, t in texts.items() if k.startswith("x2")), sorted(texts))
    check("three outputs, named by lead", sorted(k for k in texts if k.startswith("B6_ecc_")) ==
          ["B6_ecc_n29_lead1.5.txt", "B6_ecc_n29_lead5.txt", "B6_ecc_n29_lead8.5.txt"], sorted(texts))

    for name, env in (("GI_ECC_SHA unset", {"GI_ECC_SHA": None}),
                      ("GI_ECC_SHA not hex", {"GI_ECC_SHA": "z" * 64}),
                      ("GI_ECC_SHA too short", {"GI_ECC_SHA": SHA[:63]}),
                      ("INTERPOSER_CODE unset", {"INTERPOSER_CODE": None}),
                      ("GI_COMMIT unset", {"GI_COMMIT": None})):
        rc, out, log, _ = run(src, **env)
        check("%s: refused before custody" % name, rc != 0 and log == [], (rc, log, out[-200:]))
    rc, out, log, _ = run(src, FAKE_BANNER="0")
    check("no jammer banner: no arms, and the X2 is restored",
          rc != 0 and not any(l.startswith("py ") for l in log) and log[-1:] and log[-1].startswith("jam restore"), log)
    rc, out, log, _ = run(src, FAKE_CUSTODY_RC="1")
    check("a failed custody stops before any arm", rc != 0 and not any(l.startswith("py ") for l in log), log)
    rc, out, log, _ = run(src, GI_OUT=A + "/runs/b6 ecc")
    check("an output folder with a space is refused", rc != 0 and log == [], (rc, log))
    # ---- the liveness gate on the first arm (reviewer, 2026-10-20)
    rc, out, log, _ = run(src)
    calls = [l for l in log if l.startswith("py ")]
    check("the first call carries --ecc-gate, the other two do not",
          len(calls) == 3 and " --ecc-gate " in calls[0] + " " and all("--ecc-gate" not in c for c in calls[1:]), calls)
    rc, out, log, _ = run(src, FAKE_PY_RC="3")
    calls = [l for l in log if l.startswith("py ")]
    check("exit 3 from the first call (the gate): no more calls, the X2 restored, the session exits 3",
          len(calls) == 1 and log[-1:] and log[-1].startswith("jam restore") and rc == 3 and "STOPPED" in out,
          (rc, log, out[-300:]))
    rc, out, log, _ = run(src, FAKE_PY_RC="1")
    calls = [l for l in log if l.startswith("py ")]
    check("another failing exit does not stop the session", len(calls) == 3 and rc == 0, (rc, len(calls)))


MUTANTS = [
    # Not "drop the export": a variable from the environment stays exported (equivalent).
    ("GI_COMMIT not required", 'export GI_COMMIT=${GI_COMMIT:?set GI_COMMIT (the _ecc build commit, e.g. 8abf16b)}\n', ''),
    ("no --ecc", " --reset-wican --ecc $gate --want-sha", " --reset-wican $gate --want-sha"),
    ("no --judge-first", "--n 29 --judge-first 10 ", "--n 29 "),
    ("30 pulses", "--n 29 --judge-first 10", "--n 30 --judge-first 10"),
    ("no reset per arm", " --reset-wican --ecc $gate", " --ecc $gate"),
    ("the sha is not passed", ' --want-sha "$GI_ECC_SHA" --no-wican-console \\', ' --no-wican-console \\'),
    ("the WiCAN console is captured", ' --want-sha "$GI_ECC_SHA" --no-wican-console \\', ' --want-sha "$GI_ECC_SHA" \\'),
    ("the sha is not checked as hex", 'case "$GI_ECC_SHA" in *[!0-9a-fA-F]*|"") echo', 'case "$GI_ECC_SHA" in "") echo'),
    ("the sha length is not checked", '[ ${#GI_ECC_SHA} = 64 ] || { echo "GI_ECC_SHA is not 64 hex digits"; exit 2; }', ':'),
    ("19 arms", "for spec in 1.5:7 5:7 8.5:6; do", "for spec in 1.5:7 5:7 8.5:5; do"),
    ("no restore at the end", 'echo "== $(date +%H:%M:%S) restore"\nbash $A/baseline_b_jam.sh restore "$rec"\n',
     'echo "== $(date +%H:%M:%S) restore"\n'),
    ("no restore when the banner is missing", '  bash $A/baseline_b_jam.sh restore "$rec"\n  exit 1\n', '  exit 1\n'),
    ("arms run without a banner", """if [ -z "$idrec" ] || ! grep -q '"banner_ok": true' "$idrec"; then""", "if false; then"),
    ("no redaction", 'sed -i "s/$GI_WICAN_SSID/<GI_WICAN_SSID>/g" "$GI_OUT"/B6_ecc_*.txt "$GI_OUT"/x2/*.txt 2>/dev/null\n', '\n'),
    ("no gate flag", "gate=--ecc-gate\n", "gate=\n"),
    ("the gate on every call", "  gate=\ndone\n", "done\n"),
    ("exit 3 does not stop", "    break\n  fi\n", "  fi\n"),
    ("a stopped session exits 0", '[ -z "$stopped" ] || exit 3\n', ""),
    ("any failing call stops", '  if [ -n "$gate" ] && [ $rc = 3 ]; then', '  if [ -n "$gate" ] && [ $rc != 0 ]; then'),
    ("a spaced output folder runs", 'case "$GI_RUNS_DIR" in *" "*) echo "GI_RUNS_DIR has a space: $GI_RUNS_DIR"; exit 2;; esac\n', '\n'),
]


def main():
    src = open(os.path.join(HERE, SCRIPT), encoding="utf-8", newline="").read()
    if "--mutants" not in sys.argv[1:]:
        checks(src)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = 0
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: the substitution matches %d times" % (i, name, src.count(old)))
            continue
        del FAILS[:]
        try:
            checks(src.replace(old, new))
            crashed = False
        except Exception as e:
            crashed = "%s: %s" % (type(e).__name__, e)
        if FAILS and not crashed:
            killed += 1
            print("KILLED   m%d %s  (%d FAIL; %s)" % (i, name, len(FAILS), FAILS[0][:80]))
        else:
            print("%s m%d %s%s" % ("CRASHED " if crashed else "SURVIVED", i, name, ("  " + crashed) if crashed else ""))
    del FAILS[:]
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
