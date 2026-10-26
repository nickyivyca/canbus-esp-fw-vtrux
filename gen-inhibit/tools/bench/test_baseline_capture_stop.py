#!/usr/bin/env python3
"""Test the 2026-10-20 batch in the baseline scripts, with fakes for everything that
would touch the bench (reviewer, 2026-10-20):

  1. B21 (baseline_b21.sh, probe step): each capture runs under capture_window.py and is
     stopped TAIL s after its probe exits, cleanly; the capture's start and stop are
     recorded beside the probe's; the join gets the capture.
  2. B11raw (baseline_b_noX2.sh): the same per arm.
  3. baseline_b_jam.sh custody with GI_RUNS_DIR holding a space: flash-jammer gets the
     dump record; with no dump record it stops before flash-jammer.
  4. baseline_b_jam.sh restore and x2_release_identify.sh with a spaced GI_RUNS_DIR:
     the hold and release records are moved into the output folder.
  5. baseline_b_otagroup.sh: no rt_self OTA; the rt step ends with page_state.py, after
     test_only_inert, writing B12_rt_end_page.json into GI_OUT (reviewer, 2026-10-20).

    py -3.14 -B tools/gen_inhibit/bench/test_baseline_capture_stop.py

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
    echo '{"rows": []}' > "$GI_RUNS_DIR/ota_guard_raw_probe_$(arg --body "$@")_$(date +%s%N).json" ;;
  ota_guard_pcap_join.py) echo "JOIN $*" >> "$FAKE_LOG"; echo "joined" ;;
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


def env_for(tmp, runs, out, **extra):
    e = dict(os.environ)
    e["PATH"] = fw(os.path.join(tmp, "bin")) + os.pathsep + e.get("PATH", "")
    e.update({"FAKE_LOG": fw(os.path.join(tmp, "calls.log")), "FAKE_DUMPCAP_LOG": fw(os.path.join(tmp, "dumpcap.log")),
              "REAL_PY": fw(sys.executable), "GI_DUMPCAP_PRE": "%s -B %s" % (fw(sys.executable), fw(os.path.join(tmp, "fake_dumpcap.py"))),
              "GI_CAPTURE_TAIL_S": str(TAIL), "GI_CAPTURE_MAX_S": "60", "GI_COMMIT": "e3d9437",
              "GI_WICAN_SSID": "WiCAN_testssid", "INTERPOSER_CODE": "x",
              "GI_RUNS_DIR": fw(runs), "GI_OUT": fw(out), "MSYS_NO_PATHCONV": "1"})
    e.update(extra)
    return e


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


def windows_ok(name, cap_recs, win_recs, start_key, end_key):
    check("%s: one capture record per window (%d, %d)" % (name, len(cap_recs), len(win_recs)),
          len(cap_recs) == len(win_recs) and cap_recs, (cap_recs, win_recs))
    for c, w in zip(cap_recs, win_recs):
        cr, wr = json.load(open(c)), json.load(open(w))
        tag = os.path.basename(c)
        check("%s: %s stopped by its stop file, cleanly, rc 0" % (name, tag),
              cr["how"] == "stopfile" and cr["stopped_cleanly"] is True and cr["rc"] == 0, cr)
        check("%s: %s started before and stopped %d s after the window" % (name, tag, TAIL),
              cr["t_start"] < wr[start_key] and TAIL - 0.5 <= cr["t_stop_requested"] - wr[end_key] <= TAIL + 3,
              (cr["t_start"], cr["t_stop_requested"], wr))
        check("%s: %s's stop file is gone" % (name, tag), not os.path.exists(cr["stopfile"]), cr["stopfile"])


def main():
    tmp = gi_paths.scratch("capture-stop-test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "bin"))
    with open(os.path.join(tmp, "bin", "py"), "w", newline="\n") as f:
        f.write(FAKE_PY)
    open(os.path.join(tmp, "fake_dumpcap.py"), "w").write(FAKE_DUMPCAP)
    try:
        # 1. B21's probe step
        runs, out = os.path.join(tmp, "b21runs"), os.path.join(tmp, "b21out")
        os.makedirs(runs)
        rc, text = bash("baseline_b21.sh", ["img.bin"], env_for(tmp, runs, out, GI_B21_STEPS="probe"))
        check("B21: the probe step ran (two probes, two captures)",
              text.count("probe ") >= 2 and text.count("capture ") >= 2, text[-600:])
        windows_ok("B21", sorted(glob.glob(os.path.join(out, "B21_capture_*.json"))),
                   sorted(glob.glob(os.path.join(out, "B21_probe_window_*.json"))), "probe_start", "probe_end")
        dc = [json.loads(l) for l in open(os.path.join(tmp, "dumpcap.log"))] if os.path.exists(os.path.join(tmp, "dumpcap.log")) else []
        begins = [d for d in dc if "t_begin" in d]
        check("B21: dumpcap wrote to each capture file the join reads",
              len(begins) == 2 and all(os.path.basename(d["argv"][d["argv"].index("-w") + 1]) in calls(tmp) for d in begins),
              (begins, calls(tmp)[-400:]))
        check("B21: dumpcap keeps its own bound (-a duration)",
              begins and all("duration:60" in d["argv"] for d in begins), begins)
        # 2. B11raw
        open(os.path.join(tmp, "dumpcap.log"), "w").close()
        runs, out = os.path.join(tmp, "b11runs"), os.path.join(tmp, "b11out")
        os.makedirs(runs)
        rc, text = bash("baseline_b_noX2.sh", ["B11raw"], env_for(tmp, runs, out))
        windows_ok("B11raw", sorted(glob.glob(os.path.join(out, "capture_B11raw_*.json"))),
                   sorted(glob.glob(os.path.join(out, "window_B11raw_*.json"))), "arm_start", "arm_end")
        check("B11raw: six arms", len(glob.glob(os.path.join(out, "capture_B11raw_*.json"))) == 6, text[-400:])
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
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
