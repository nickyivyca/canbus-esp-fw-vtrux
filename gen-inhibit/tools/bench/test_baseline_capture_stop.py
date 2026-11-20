#!/usr/bin/env python3
"""Test the 2026-10-20 batch in the baseline scripts, with fakes for everything that
would touch the bench (reviewer, 2026-10-20):

  1. B21 (baseline_b21.sh, probe step): since topic (E) (2026-11-19, spec 9.3) no capture
     and no pcap join, with no capture interface named: each probe's record, by the path
     its "wrote" line names, is judged at the client into B21_client_<body>_<commit>.txt;
     a probe that wrote no record leaves its client file saying ERROR. (Until then: each
     capture under capture_window.py, stopped TAIL s after its probe.)
  2. B11raw (baseline_b_noX2.sh): six arms with raw POSTs and no capture (topic (E)).
  3. baseline_b_jam.sh custody with GI_RUNS_DIR holding a space: flash-jammer gets the
     dump record; with no dump record it stops before flash-jammer.
  4. baseline_b_jam.sh restore and x2_release_identify.sh with a spaced GI_RUNS_DIR:
     the hold and release records are moved into the output folder.
  5. baseline_b_otagroup.sh: no rt_self OTA; the rt step ends with page_state.py, after
     test_only_inert, writing B12_rt_end_page.json into GI_OUT (reviewer, 2026-10-20).

    py -3.14 -B gen-inhibit/tools/bench/test_baseline_capture_stop.py

Run from the reverse-it root (the scripts use paths relative to it). A fake `py` goes
first on PATH: it runs capture_window.py for real and fakes every other tool by name; a
fake dumpcap (GI_DUMPCAP_PRE) writes its -w file and exits 0 on CTRL_BREAK. BATCH_DIR
names the folder of the scripts under test (default: this folder; the mutant runner sets
it). About 2 minutes. Gen-inhibit tester.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = os.environ.get("BATCH_DIR") or HERE
BASH = os.environ.get("GI_BASH") or shutil.which("bash")
TAIL = 2
fails = []
oks = 0

FAKE_PY = r'''#!/usr/bin/env bash
# fake py for test_baseline_capture_stop.py
echo "CALL $*" >> "$FAKE_LOG"
if [ "${1:-}" = "-3.14" ]; then shift; fi
if [ "${1:-}" = "-c" ]; then exec "$REAL_PY" "$@"; fi
[ "${1:-}" = "-B" ] && shift
script=${1:-}; [ $# -gt 0 ] && shift
arg() { local k=$1; shift; while [ $# -gt 0 ]; do [ "$1" = "$k" ] && { echo "$2"; return; }; shift; done; }
case "$(basename "$script")" in
  capture_window.py) exec "$REAL_PY" -B "$script" "$@" ;;
  -|"") cat > /dev/null; echo "1000 SW" ;;
  ota_guard_raw_probe.py) sleep 1
    rec="$GI_RUNS_DIR/ota_guard_raw_probe_$(arg --body "$@")_$(date +%Y%m%dT%H%M%S).json"
    [ -n "${FAKE_NO_PROBE_RECORD:-}" ] || echo '{"rows": []}' > "$rec"
    echo "uptime 1 -> 2 ms, mode after 0; wrote $rec" ;;
  ota_guard_client_judge.py) echo "CLIENT $*" >> "$FAKE_LOG"; echo "verdict PASS: 0 reps (fake)" ;;
  e4_device_calibration.py) sleep 1; echo '{}' > "$(arg --out "$@")" ;;
  x2_flash_custody.py)
    case "$1" in
      dump) [ -n "${FAKE_NO_DUMP:-}" ] || echo '{"verdict": "PASS"}' > "$GI_RUNS_DIR/x2_flash_custody_dump_20261020T000000.json"
            echo "-> PASS" ;;
      flash-jammer) echo "FLASHJAMMER dump-record=[$(arg --dump-record "$@")]" >> "$FAKE_LOG" ;;
      restore) echo "Verification successful (digest matched)" ;;
    esac ;;
  x2_jammer.py) echo "banner ok" ;;
  x2_hold.py) echo '{}' > "$GI_RUNS_DIR/x2_hold_$1_20261020T000000.json" ;;
  bench_identify_board.py) sleep 3; echo "build 756d6714" ;;
  restart_error_frames.py) echo "restart ok" ;;
  test_only_inert.py) echo "INERT" >> "$FAKE_LOG"; echo "inert ok" ;;
  page_state.py) echo "PAGESTATE out=[$(arg --out "$@")]" >> "$FAKE_LOG"; echo '{}' > "$(arg --out "$@")" ;;
  *) echo "fake py: unknown $script" >&2; exit 9 ;;
esac
'''

FAKE_DUMPCAP = r'''
import json, os, signal, sys, time
a = sys.argv[1:]
w = a[a.index("-w") + 1]
open(w, "wb").write(b"\x0a\x0d\x0d\x0a")
log = os.environ["FAKE_DUMPCAP_LOG"]
def done(*_):
    with open(log, "a") as f:
        f.write(json.dumps({"argv": a, "t_end": time.time()}) + "\n")
    sys.exit(0)
signal.signal(getattr(signal, "SIGBREAK", signal.SIGINT), done)
with open(log, "a") as f:
    f.write(json.dumps({"argv": a, "t_begin": time.time()}) + "\n")
while True:
    time.sleep(0.05)
'''


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, str(detail)[:300]))
    sys.stdout.flush()


def fw(p):
    return p.replace("\\", "/")


# The scripts call the host's Python through bench_host.sh's $PY (2026-11-12): python3.13
# on Linux, `py -3.14` in Git Bash. The fake goes first on PATH under that name.
PY_CMD = ["py", "-3.14"] if os.name == "nt" else ["python3.13"]


def put_fake_py(bindir, text):
    """Writes the fake interpreter as bindir/<the host's Python name>, executable."""
    p = os.path.join(bindir, PY_CMD[0])
    with open(p, "w", newline="\n") as f:
        f.write(text)
    os.chmod(p, 0o755)
    return p


def host_py():
    """bench_host.sh's $PY, as this host's bash reads it, split into words."""
    r = subprocess.run([BASH, "-c", '. "$1/bench_host.sh" && printf "%s\\n" $PY', "x", fw(BATCH)],
                       capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else ["exit %d: %s" % (r.returncode, r.stderr.strip())]


POWER = "12 V test supply; USB plugged at test-port"   # GI_WICAN_POWER, as the settings give it (2026-11-16)


def env_for(tmp, runs, out, **extra):
    e = dict(os.environ)
    e["PATH"] = fw(os.path.join(tmp, "bin")) + os.pathsep + e.get("PATH", "")
    e.update({"FAKE_LOG": fw(os.path.join(tmp, "calls.log")), "FAKE_DUMPCAP_LOG": fw(os.path.join(tmp, "dumpcap.log")),
              "REAL_PY": fw(sys.executable), "GI_DUMPCAP_PRE": "%s -B %s" % (fw(sys.executable), fw(os.path.join(tmp, "fake_dumpcap.py"))),
              "GI_CAPTURE_TAIL_S": str(TAIL), "GI_CAPTURE_MAX_S": "60", "GI_COMMIT": "e3d9437",
              "GI_WICAN_SSID": "WiCAN_testssid", "INTERPOSER_CODE": "x", "GI_CAPTURE_IFACE": "test-iface",
              "GI_RUNS_DIR": fw(runs), "GI_OUT": fw(out), "MSYS_NO_PATHCONV": "1",
              "GI_WICAN_POWER": POWER})
    e.update(extra)
    return {k: v for k, v in e.items() if v is not None}     # extra NAME=None unsets NAME


def bash(script, args, env, timeout=600):
    p = subprocess.Popen([BASH, fw(os.path.join(BATCH, script))] + args, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # the whole tree, so no fake dumpcap or capture_window is left running
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.kill()
        out, _ = p.communicate()
        return None, "TIMED OUT " + out
    return p.returncode, out


def calls(tmp):
    p = os.path.join(tmp, "calls.log")
    return open(p).read() if os.path.exists(p) else ""


def main():
    tmp = gi_paths.scratch("capture-stop-test")
    gi_paths.remove_tree(tmp)
    os.makedirs(os.path.join(tmp, "bin"))
    put_fake_py(os.path.join(tmp, "bin"), FAKE_PY)
    open(os.path.join(tmp, "fake_dumpcap.py"), "w").write(FAKE_DUMPCAP)
    try:
        # 0. bench_host.sh: the scripts call the host's Python, under its own name
        check("bench_host.sh: the scripts' Python is %s here" % " ".join(PY_CMD), host_py() == PY_CMD,
              host_py())
        # 1. B21's probe step, judged at the client since topic (E) (spec 9.3, 2026-11-19): no capture
        #    and no pcap join, with no capture interface named; each probe's record goes to the client
        #    judge by the path its "wrote" line names, into B21_client_<body>_<commit>.txt
        open(os.path.join(tmp, "calls.log"), "w").close()
        runs, out = os.path.join(tmp, "b21runs"), os.path.join(tmp, "b21out")
        os.makedirs(runs)
        # a stray record newer than any the probes write: the probe's own record is judged, by its
        # "wrote" line, never the newest in runs/
        stray = os.path.join(runs, "ota_guard_raw_probe_image_29991231T000000.json")
        open(stray, "w").write('{"rows": []}')
        os.utime(stray, (4102444800, 4102444800))
        e = env_for(tmp, runs, out, GI_B21_STEPS="probe", GI_CAPTURE_IFACE=None)
        rc, text = bash("baseline_b21.sh", ["img.bin"], e)
        recs = sorted(r for r in glob.glob(os.path.join(runs, "ota_guard_raw_probe_*.json")) if r != stray)
        judged = [l for l in calls(tmp).splitlines() if l.startswith("CLIENT ")]
        check("B21: two probes, each its own record judged at the client, with no capture interface named",
              rc == 0 and len(recs) == 2 and len(judged) == 2 and not any("29991231" in l for l in judged)
              and all(any(fw(r) in l for l in judged) for r in recs), (rc, recs, judged, text[-400:]))
        check("B21: the two client judgements written, B21_client_<body>_e3d9437.txt",
              sorted(n for n in os.listdir(out) if n.startswith("B21_client_"))
              == ["B21_client_image_e3d9437.txt", "B21_client_multipart_e3d9437.txt"], sorted(os.listdir(out)))
        check("B21: no dumpcap, no capture_window, no pcap join",
              not os.path.exists(os.path.join(tmp, "dumpcap.log")) and "capture_window" not in calls(tmp)
              and "pcap_join" not in calls(tmp) and not glob.glob(os.path.join(out, "*.pcapng")), calls(tmp)[-400:])
        open(os.path.join(tmp, "calls.log"), "w").close()
        runs, out = os.path.join(tmp, "b21norec runs"), os.path.join(tmp, "b21norec out")
        os.makedirs(runs)
        rc, text = bash("baseline_b21.sh", ["img.bin"], env_for(tmp, runs, out, GI_B21_STEPS="probe",
                                                                FAKE_NO_PROBE_RECORD="1"))
        got = [open(os.path.join(out, "B21_client_%s_e3d9437.txt" % k)).read() if os.path.exists(
            os.path.join(out, "B21_client_%s_e3d9437.txt" % k)) else "" for k in ("multipart", "image")]
        check("B21: a probe that wrote no record -> its client file says ERROR, nothing judged",
              all(g.startswith("verdict ERROR: the probe wrote no record") for g in got)
              and "CLIENT " not in calls(tmp), (got, calls(tmp)[-300:]))
        # 2. B11raw: six arms with raw POSTs and no capture (topic (E))
        runs, out = os.path.join(tmp, "b11runs"), os.path.join(tmp, "b11out")
        os.makedirs(runs)
        rc, text = bash("baseline_b_noX2.sh", ["B11raw"], env_for(tmp, runs, out, GI_CAPTURE_IFACE=None))
        e4 = [l for l in calls(tmp).splitlines() if "e4_device_calibration.py" in l and "B11raw_e4_quiesce_" in l]
        check("B11raw: six arms with raw POSTs, no capture interface named, exit 0",
              rc == 0 and len(e4) == 6 and all("--post-raw-timeout 3" in l for l in e4), (rc, len(e4), text[-400:]))
        check("B11raw: no dumpcap, no capture_window, no capture files",
              not os.path.exists(os.path.join(tmp, "dumpcap.log")) and "capture_window" not in calls(tmp)
              and not glob.glob(os.path.join(out, "*.pcapng")) and not glob.glob(os.path.join(out, "capture_*.json")),
              calls(tmp)[-300:])
        # baseline_b_desk.sh B11raw takes "$dir"/B11raw_e4_quiesce_*.json as the e4 records
        desk = glob.glob(os.path.join(out, "B11raw_e4_quiesce_*.json"))
        check("B11raw: the desk step's glob finds the six e4 records and nothing else",
              len(desk) == 6 and all(json.load(open(f)) == {} for f in desk), desk)
        # 3. jam custody with a space in GI_RUNS_DIR
        open(os.path.join(tmp, "calls.log"), "w").close()
        runs, out = os.path.join(tmp, "jam runs"), os.path.join(tmp, "jamout")
        os.makedirs(runs)
        rc, text = bash("baseline_b_jam.sh", ["custody"], env_for(tmp, runs, out))
        want = "dump-record=[%s]" % fw(os.path.join(runs, "x2_flash_custody_dump_20261020T000000.json"))
        check("jam custody, spaced GI_RUNS_DIR: flash-jammer gets the dump record", want in calls(tmp),
              (want, calls(tmp)[-500:]))
        open(os.path.join(tmp, "calls.log"), "w").close()
        runs2 = os.path.join(tmp, "jam runs 2")
        os.makedirs(runs2)
        rc, text = bash("baseline_b_jam.sh", ["custody"], env_for(tmp, runs2, out, FAKE_NO_DUMP="1"))
        check("jam custody, no dump record: stops before flash-jammer",
              rc != 0 and "FLASHJAMMER" not in calls(tmp), (rc, calls(tmp)[-300:]))
        # 4. restore and release with a space in GI_RUNS_DIR
        runs3, out3 = os.path.join(tmp, "rel runs"), os.path.join(tmp, "relout")
        os.makedirs(runs3)
        rc, text = bash("baseline_b_jam.sh", ["restore", "rec.json"], env_for(tmp, runs3, out3))
        check("jam restore, spaced GI_RUNS_DIR: the hold record is moved into x2/",
              glob.glob(os.path.join(out3, "x2", "x2_hold_hold_*.json")) and not glob.glob(os.path.join(runs3, "x2_hold_hold_*.json")),
              os.listdir(runs3))
        runs4, out4 = os.path.join(tmp, "id runs"), os.path.join(tmp, "idout")
        os.makedirs(runs4)
        rc, text = bash("x2_release_identify.sh", [fw(out4), "pcan"], env_for(tmp, runs4, out4))
        check("release identify, spaced GI_RUNS_DIR: the release record is moved",
              glob.glob(os.path.join(out4, "x2_hold_release_*.json")) and not glob.glob(os.path.join(runs4, "x2_hold_release_*.json")),
              (os.listdir(runs4), text[-300:]))
        # 5. no rt_self
        src = open(os.path.join(BATCH, "baseline_b_otagroup.sh"), encoding="utf-8").read()
        check("otagroup: no OTA of _man_rt over itself", "ota rt_self" not in src and src.count('ota rt "$(img rt)"') == 1)
        # 6. the rt step ends with the page read (run under the fakes, the runner's GI_NO_OTA)
        out6 = os.path.join(tmp, "ota out")
        os.makedirs(out6)
        before = len(calls(tmp))
        rc, text = bash("baseline_b_otagroup.sh", [fw(tmp), fw(tmp), "rt"], env_for(tmp, os.path.join(tmp, "runs6"), out6,
                                                                                    GI_NO_OTA="1"))
        log = calls(tmp)[before:].splitlines()
        ps = [i for i, l in enumerate(log) if l.startswith("PAGESTATE ")]
        inert = [i for i, l in enumerate(log) if l == "INERT"]
        want = fw(os.path.join(out6, "B12_rt_end_page.json"))
        check("otagroup rt: one page read, after test_only_inert, into GI_OUT",
              rc == 0 and len(ps) == 1 and inert and ps[0] > max(inert) and log[ps[0]] == "PAGESTATE out=[%s]" % want
              and os.path.exists(want), (rc, log[-6:], text[-300:]))
        # 7. the WiCAN's power as given, never a fixed "USB unplugged" (gen-inhibit reviewer, 2026-11-16)
        inert_calls = [l for l in log if l.startswith("CALL") and "test_only_inert.py" in l]
        check("otagroup rt: test_only_inert gets GI_WICAN_POWER as given, no console capture, no 'USB unplugged'",
              inert_calls and all("--power %s; no WiCAN console capture; X2 756d6714 not held; image" % POWER in l
                                  for l in inert_calls) and not any("USB unplugged" in l for l in log),
              inert_calls[:1])
        for script, args, extra in (("baseline_b_otagroup.sh", [fw(tmp), fw(tmp), "rt"], {"GI_NO_OTA": "1"}),
                                    ("baseline_b_hand.sh", ["B1"], {"GI_PW_IMAGE": "image test"}),
                                    ("b6_rate_run.sh", [], {}),
                                    ("b6_ecc_run.sh", [], {"GI_ECC_SHA": "ab" * 32})):
            for unset in (None, ""):
                o = os.path.join(tmp, "nopw %s %r" % (script, unset))
                os.makedirs(o)
                before = len(calls(tmp))
                rc, text = bash(script, args, env_for(tmp, os.path.join(tmp, "runs7"), o, GI_WICAN_POWER=unset,
                                                      **extra))
                new = [l for l in calls(tmp)[before:].splitlines() if l.startswith("CALL")]
                check("%s: GI_WICAN_POWER %s refuses, exit 2, nothing run" % (script, "unset" if unset is None else "empty"),
                      rc == 2 and new == [] and "GI_WICAN_POWER" in text, (rc, new[:2], text[-200:]))
        o = os.path.join(tmp, "hand B1")
        os.makedirs(o)
        before = len(calls(tmp))
        rc, text = bash("baseline_b_hand.sh", ["B1"], env_for(tmp, os.path.join(tmp, "runs8"), o, GI_PW_IMAGE="image test"))
        b1 = [l for l in calls(tmp)[before:].splitlines() if l.startswith("CALL") and "slcan_refusal_probe.py" in l]
        check("hand B1: the probe gets GI_WICAN_POWER as given, no console capture, the image, the X2 not held",
              len(b1) == 1 and "--power %s; no WiCAN console capture; image test; X2 756d6714 not held" % POWER in b1[0]
              and "USB unplugged" not in b1[0], (b1, text[-200:]))
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        gi_paths.remove_tree(tmp)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
