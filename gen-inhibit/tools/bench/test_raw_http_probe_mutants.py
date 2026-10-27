#!/usr/bin/env python3
"""Mutants of raw_http_probe.py and of e4_device_calibration.py's raw-POST path:
the matching test must fail on every one.

    py -3.14 test_raw_http_probe_mutants.py <scratch dir> [target.py ...]

Each mutant is one source substitution. The target, its test and the files they
import are copied into <scratch dir>/<target>_<n>/ with the mutation applied, and
the test is run there. A kill is a FAIL line from a check. A mutant the test
passes is a check that cannot fail (SURVIVED); one that crashes the test without
a FAIL line is BROKEN. Both count against the run.

Each suite first runs its unmodified copy, laid out the same way, as the control. If
the control fails a check or crashes, the suite's kills mean nothing: none counts, and
the run fails. Only the tests in TAKES_SCRATCH get the copy's scratch/ folder as their
argument; test_image_elf_sha.py's optional argument is a real image, and passing it a
folder crashed its unmodified copy after every synthetic check had passed (gen-inhibit
tester, 2026-10-25).
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

PROBE_MUTANTS = [
    ("RST read as FIN", 'rec["ending"] = "rst"', 'rec["ending"] = "fin"'),
    ("FIN read as timeout", 'rec["ending"] = "fin"\n                break',
     'rec["ending"] = "timeout"\n                break'),
    ("first byte time wrong", 'rec["t_first_byte"] = round(t - base, 4)',
     'rec["t_first_byte"] = round(clock() + 5 - base, 4)'),
    ("verdict always ok", 'r.begin()\n        r.read()\n        return {"result": "ok"',
     'return {"result": "ok"'),
    ("replay without buffering", "return io.BufferedReader(self.raw)", "return self.raw"),
    ("cl_matches always True", "else len(body) == cl)", "else True)"),
    ("JSON always ok", 'json.loads(payload.decode("utf-8"))', "pass"),
    ("no wait cap after complete", "left = min(deadline, t_complete_abs + after_complete_s) - now",
     "left = deadline - now"),
    ("t_complete never set", 'rec["t_complete"] = round(t - base, 4)', "pass"),
    ("header order changed", '"Accept-Encoding: identity"]', '"Accept-Encoding: Identity"]'),
    ("chunked not decoded", "dechunk(body) if chunked else (body, None)", "(body, None)"),
    ("caught_by_gi inverted", '"caught_by_gi": isinstance(e, OSError)',
     '"caught_by_gi": not isinstance(e, OSError)'),
    ("connect error swallowed", 'rec.update(ending="connect_error"', 'rec.update(ending="fin"'),
    ("received bytes dropped", "raw += d", "raw += d[:0]"),
]

# The raw-POST path in e4_device_calibration.py, against test_e4_rawpost.py.
E4_MUTANTS = [
    ("reply_failed ignores JSON", 'or rec.get("body_json_ok") is not True)', ")"),
    ("reply_failed ignores ending", 'rec.get("ending") not in ("fin", "complete_open")', "False"),
    ("reply_failed ignores status", 'or " 200 " not in st + " "', ""),
    ("no follow-up GET", "if reply_failed(self.raw):", "if False:"),
    ("chain ignored", "not (POST_CHAIN and si > 0", "not (False and si > 0"),
    ("exception re-raised", "self.exc = traceback.format_exc()", "raise"),
    ("netstat by prefix", 'f[2].rsplit(":", 1)[0] == host', "f[2].startswith(host)"),
    ("raw path off", "if POST_RAW_TIMEOUT is None:\n                self.t_start",
     "if True:\n                self.t_start"),
]

# reply_golive_report.py, against test_reply_golive_report.py (which takes a scratch dir).
REPORT_MUTANTS = [
    ("trail test inverted", "(t - prev) * 1e6 < TRAIL_MAX_US", "(t - prev) * 1e6 >= TRAIL_MAX_US"),
    ("VCM count off by one", "        vcm_between += 1", "        vcm_between += 2"),
    ("dwell from t_end", 'last_byte = prev_raw["chunks"][-1][0] if prev_raw["chunks"] else prev_raw["t_end"]',
     'last_byte = prev_raw["t_end"]'),
    ("reboot test inverted", 'dev["uptime_ms"] < pre["uptime_ms"]', 'dev["uptime_ms"] > pre["uptime_ms"]'),
    ("FIN not seen", 'first([e for e in d if e["fin"]])', "None"),
    ("own port counted as other", "if x != r[\"local_port\"]]", "]"),
    ("SYN window ignores time", 'close - 2.0 <= x["t"] <= close', "True"),
    ("X2 build read big-endian", 'int.from_bytes(b, "little")', 'int.from_bytes(b, "big")'),
    ("X2 IDs not filtered", "if i not in X2_IDS:", "if False:"),
]

# image_elf_sha.py, against test_image_elf_sha.py (synthetic images only).
IMAGE_MUTANTS = [
    ("hash read one byte early", "sha = d[SHA_OFF_IN_DESC:SHA_OFF_IN_DESC + 32]",
     "sha = d[SHA_OFF_IN_DESC - 1:SHA_OFF_IN_DESC + 31]"),
    ("hash read one byte late", "sha = d[SHA_OFF_IN_DESC:SHA_OFF_IN_DESC + 32]",
     "sha = d[SHA_OFF_IN_DESC + 1:SHA_OFF_IN_DESC + 33]"),
    ("image magic unchecked", "if data[0] != IMAGE_MAGIC:", "if False:"),
    ("descriptor magic unchecked", "if magic != DESC_MAGIC:", "if False:"),
    ("descriptor at 0x18", "DESC_OFF = 0x20", "DESC_OFF = 0x18"),
    ("short file not refused", "if len(data) < DESC_OFF + SHA_OFF_IN_DESC + 32:", "if False:"),
]

SUITES = [
    ("raw_http_probe.py", "test_raw_http_probe.py", [], PROBE_MUTANTS),
    ("e4_device_calibration.py", "test_e4_rawpost.py",
     ["raw_http_probe.py", "test_raw_http_probe.py", "e4_rig_calibration.py"], E4_MUTANTS),
    ("reply_golive_report.py", "test_reply_golive_report.py", [], REPORT_MUTANTS),
    ("image_elf_sha.py", "test_image_elf_sha.py", [], IMAGE_MUTANTS),
    ("x2_jam_cases.py", "test_busoff_rearm_judge.py", [], [
        ("device copy never booked", "mine = i + 1 < len(fr) and fr[i + 1][1] == h", "mine = False"),
        ("trail rule back in the split", "mine = i + 1 < len(fr) and fr[i + 1][1] == h",
         "mine = i > 0 and t - fr[i - 1][0] < TRAIL_S"),
        ("split ignores the payload", "mine = i + 1 < len(fr) and fr[i + 1][1] == h", "mine = i + 1 < len(fr)"),
        ("trail count reads nothing", "if t0 <= t < t1 and t - p < TRAIL_S)", "if t0 <= t < t1 and t - p < 0)"),
        ("witness file drops frames", 'json.dump({"clock": clock, "frames": frames, "errs": errs}, fh)',
         'json.dump({"clock": clock, "frames": frames[:1], "errs": errs}, fh)'),
        ("no-arm recovery ignored", 'elif any(x[1] != "bus_off" for x in noarm) or dev_n:', "elif False:"),
        ("device 0x051 with no arm ignored", 'any(x[1] != "bus_off" for x in noarm) or dev_n:',
         'any(x[1] != "bus_off" for x in noarm):'),
        ("replay check off", 'out["vcm_0x051_per_s_after"] < MIN_VCM_PER_S:', "False:"),
        ("reply status ignored", "elif reply_status != 200:", "elif False:"),
        ("old build: device 0x051 ignored", "if run_after is None and not dev_a and all(",
         "if run_after is None and all("),
        ("new build: live not required", "if run_after is not None and live_after is not None and dev_a:",
         "if run_after is not None:"),
        ("bus_off precondition off", "    if boff is None:\n        out[\"verdict\"] = \"INCONCLUSIVE: the page never",
         "    if False:\n        out[\"verdict\"] = \"INCONCLUSIVE: the page never"),
        ("reading count unchecked", "elif len(noarm) < 20 or len(after) < 20:", "elif False:"),
        ("reboot unchecked", "    elif drop is not None:", "    elif False:"),
        ("400 not named", "    elif reply_status == 400:", "    elif False:"),
        ("reboot read on any uptime", "if up_before and u < max(up_before)", "if up_before"),
    ]),
    ("interlock_judge.py", "test_interlock_judge.py", ["interlock_bench.py"], [
        ("rekey window from the POST return", "c.first_sent(3) if k == 0 else s", "s"),
        ("wake window from the POST return", '_wake_gate(c, v, c.first_sent(3), float("inf"), "wake")',
         '_wake_gate(c, v, t_arm, float("inf"), "wake")'),
        ("first_sent is the return", 'return p.get("t_sent", p["t_ret"])', 'return p["t_ret"]'),
        ("device copy never booked", "mine = k + 1 < len(c) and alias.get(c[k + 1][1], c[k + 1][1]) == h",
         "mine = False"),
        ("answers the next VCM frame", "k = bisect.bisect_left(self.vcm_t, td) - 1",
         "k = bisect.bisect_left(self.vcm_t, td)"),
        ("silence never checked", 'bad = c.dev_answering(t, t_end if t_end is not None else float("inf"))',
         "bad = []"),
        ("any 0x440 sets the marker", "            last_c = t\n            if val in CLOSED:",
         "            last_c = t\n            if True:"),
        ("debounce one short", "if len(run) == DEBOUNCE_N:\n                return run[3], run[4]",
         "if len(run) == DEBOUNCE_N - 1:\n                return run[2], run[3]"),
        ("raw 0 counts", "if raw == 0:\n            continue", "if False:\n            continue"),
        ("healthy reading does not restart", "        else:\n            run = []", "        else:\n            pass"),
        ("live flag unchecked", "lit = [round(t - t_latch, 2) for t, d in after if flag(d, 5)]", "lit = []"),
        ("latch window runs past the disarm", "t_stop = min([t_end] + posts)", "t_stop = t_end"),
        ("alias ignored", "mine = k + 1 < len(c) and alias.get(c[k + 1][1], c[k + 1][1]) == h",
         "mine = k + 1 < len(c) and c[k + 1][1] == h"),
        ("DLC 8 answer unchecked", "len(mine) == 1,", "True,"),
        ("arm_block unchecked", 'if block_name(d) != "disabled"]', 'if False]'),
        ("live before the close unchecked", "early = c.dev_answering(t_from, since if since else t_to)",
         "early = []"),
        ("marker precondition off", 'unset = [round(t - t_re, 2) for t, d in f2 if not flag(d, 0, "diag_flags2")]',
         "unset = []"),
        ("0x10 leak unchecked", "inside = [round(t - a, 4) for t, x in b0 if a < t < b and x != 0x10]",
         "inside = []"),
        ("no evidence rule", "lo, hi = c.vcm_after(exp - EDGE_S), c.vcm_after(exp + EDGE_S)",
         "lo, hi = exp, exp"),
        ("0x617 window 0.5 s", "0x617: 1.0, 0x639", "0x617: 0.5, 0x639"),
        ("once-heard rule off", "heard = [t for t in ts if t >= t_arm] if ident in ONCE_HEARD else ts",
         "heard = ts"),
        ("stale_rpm with 0x471 stale", "if other is None or other[0] >= t_fire - EDGE_S:", "if True:"),
        ("trip reason unchecked", 'name == case["expect"],\n            "first 0x7F8 abort reason',
         'name is not None,\n            "first 0x7F8 abort reason'),
        ("event-trip reason unchecked", 'name == case["expect"], "first abort %r" % name)',
         'name is not None, "first abort %r" % name)'),
        ("cut proof off", "v.need(\"cut 0x%03X proved on the wire\" % ident, before and not after,",
         "v.need(\"cut 0x%03X proved on the wire\" % ident, True,"),
        ("other signals unchecked", "gone = [(\"0x%03X\" % i) for i in heard if", "gone = [] and [(\"0x%03X\" % i) for i in heard if"),
        ("tx_fail unchecked", "max(post) == pre_fail[-1],", "True,"),
        ("key order unchecked", "ta is not None and ta > t_off and (t_fire is None or t_fire > t_off),",
         "ta is not None,"),
        ("emit_refused unchecked", 'if flag(d, 3, "diag_flags2")]', "if False]"),
        ("0xCA proof off", 'v.need("0x617 B7 changed to 0xCA on the wire", before and bad and',
         'v.need("0x617 B7 changed to 0xCA on the wire", True or before and bad and'),
        ("trip 4 debounce ignored", "lo = hi_t[0] + 0.3 - 0.012", "lo = hi_t[0] - 0.012"),
        ("bus-stop latch unchecked", 'silent_from(c, v, stop, "no device 0x051 after the stop, before or after the bus returns")',
         "pass"),
        ("bus-stop gap unproved", "before and after and not gap,", "before and after,"),
        ("gate block unchecked", "if block_name(d) != blk]", "if False]"),
        ("gate never-live unchecked", 'c.times(0x639), "")\n    v.check("never live", not c.dev,',
         'c.times(0x639), "")\n    v.check("never live", True,'),
        ("never-closed never-live unchecked", 'else t_arm\n    v.check("never live", not c.dev,',
         'else t_arm\n    v.check("never live", True,'),
        ("gate stimulus unchecked", "rpm and min(rpm) >= 300", "True"),
    ]),
    ("interlock_bench.py", "test_interlock_judge.py", ["interlock_judge.py"], [
        ("premise counts nothing", "return sum(1 for a, b in zip(c, c[1:]) if a == b)", "return 0"),
        ("DLC mismatch accepted", "if len(hexs) != 2 * dlc:", "if False:"),
        ("host directive accepted", "        else:\n            out[\"refused\"].append(s)\n    if out",
         "        else:\n            pass\n    if out"),
        ("pin mismatch accepted", 'if info.get("pin_match") is False:', "if False:"),
        ("never armed accepted", 'if not any(m == 3 for _, m, _ in info["modes"]):', "if False:"),
        ("original payload not kept", "info.update(injected_hex=fr[k][2], original_hex=h)",
         "info.update(injected_hex=fr[k][2], original_hex=fr[k][2])"),
        ("encode_sig grows the frame", 'return out.to_bytes(m.length, "big")[:len(raw)].hex()',
         'return out.to_bytes(m.length, "big").hex()'),
        ("encode_sig drops other bits", "out = (int.from_bytes(pad[:m.length], \"big\") & ~mask) | val",
         "out = val"),
        ("close_all leaves 0x440 as it was", "encode_mainc(h, 11) if i == 0x440 else h", "h"),
        ("sender stall never voids", "return worst, [k for k, v in worst.items() if v > limit]",
         "return worst, []"),
        ("sender stall ignores the planned gap", "x = (ts - p_ts) - (t_us - p_us) / 1e6", "x = ts - p_ts"),
        ("sender stall keeps the first excess", "worst[k] = max(worst.get(k, x), x)", "worst.setdefault(k, x)"),
        ("witness stall never voids", "return worst, bad + [k for k, v in worst.items() if v > limit]",
         "return worst, bad"),
        ("witness stall keeps 0x051", "if ident != 0x051:", "if True:"),
        ("witness stall ignores the plan", "(w[j] - w[j - 1]) - (p[j] - p[j - 1])", "(w[j] - w[j - 1])"),
        ("witness stall hides an unpaired ID", 'bad.append("%s planned %d seen %d" % (k, len(p), len(w)))',
         "pass"),
        ("priority not raised", "k.SetPriorityClass(ctypes.c_void_p(h), ABOVE_NORMAL)", "pass"),
        ("lock ignores the loan", 'if loan.strip("- ") == "":', "if True:"),
        ("lock accepts any loan", "return loan.startswith(ME),", "return True,"),
    ]),
    ("spec11_page_check.py", "test_spec11_page_check.py", ["image_elf_sha.py"], [
        ("no VOID on equal builds", "if mine == other:", "if False:"),
        ("hex form unchecked", 'not re.fullmatch(r"[0-9a-f]{64}", v)', "False"),
        ("compare to the other build", "elif v != mine:", "elif v != other:"),
        ("absent page value passes", "elif v is None:", "elif False:"),
    ]),
]

# The tests whose first argument is a scratch folder; the others get none.
TAKES_SCRATCH = {"test_reply_golive_report.py", "test_busoff_rearm_judge.py", "test_interlock_judge.py"}


def run_copy(scratch, tag, target, text, test, extra):
    """Copy target (as text), its test and extra into scratch/tag and run the test there.
    Returns (exit code, FAIL lines, last stderr line)."""
    d = os.path.join(scratch, tag)
    os.makedirs(d, exist_ok=True)
    for x in extra:
        shutil.copy(os.path.join(HERE, x), d)
    open(os.path.join(d, target), "w", encoding="utf-8").write(text)
    shutil.copy(os.path.join(HERE, test), d)
    # NOT -I: isolated mode drops the script's own directory from sys.path, the
    # test then cannot import the mutated target, and every mutant "dies" on a
    # ModuleNotFoundError -- which is how the first run of this file read 14 of
    # 14 killed with no check having run (2026-10-14).
    # This folder (the target's sibling modules) comes after the script's own
    # directory, so the mutated copy still wins. The reverse-it root is no longer
    # on it: it was there for canre, which comes from the pin since the canre-pin
    # step (2026-10-26; gi_paths.use_canre, canre_pin.py).
    env = dict(os.environ, PYTHONPATH=HERE)
    args = [os.path.join(d, "scratch")] if test in TAKES_SCRATCH else []
    r = subprocess.run([sys.executable, os.path.join(d, test)] + args,
                       capture_output=True, text=True, cwd=d, timeout=900, env=env)
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
    return r.returncode, fails, (r.stderr.strip().splitlines() or [""])[-1]


def run_suite(scratch, target, test, extra, muts):
    """Returns (killed, not killed, whether the control passed)."""
    src = open(os.path.join(HERE, target), encoding="utf-8").read()
    killed, survived = 0, []
    print("== %s against %s" % (target, test))
    tag = "%s_control" % os.path.splitext(target)[0]
    rc, fails, err = run_copy(scratch, tag, target, src, test, extra)
    control_ok = rc == 0 and not fails
    print("%-9s %-26s %-30s %s" % ("PASS" if control_ok else "FAILED", tag, "control: the unmodified copy",
                                   "" if control_ok else (fails[0][:90] if fails else err[:90])))
    for n, (label, old, new) in enumerate(muts):
        tag = "%s_%d" % (os.path.splitext(target)[0], n)
        if src.count(old) != 1:
            print("BAD MUTANT %s %s: pattern found %d times" % (tag, label, src.count(old)))
            survived.append(label + " (bad pattern)")
            continue
        rc, fails, err = run_copy(scratch, tag, target, src.replace(old, new), test, extra)
        dead = rc != 0 and bool(fails)
        verdict = "KILLED" if dead else ("BROKEN" if rc != 0 else "SURVIVED")
        why = fails[0][:90] if fails else err[:90]
        print("%-9s %-26s %-30s %s" % (verdict, tag, label, why))
        if dead and control_ok:
            killed += 1
        elif not dead:
            survived.append("%s (%s)" % (label, verdict))
    return killed, survived, control_ok


def main():
    scratch = sys.argv[1]
    killed, survived, total, bad_controls = 0, [], 0, []
    only = sys.argv[2:]              # optional: run only these targets
    for target, test, extra, muts in SUITES:
        if only and target not in only:
            continue
        k, s, ok = run_suite(scratch, target, test, extra, muts)
        killed += k
        survived += s
        total += len(muts)
        if not ok:
            bad_controls.append(target)
    for x in survived:
        print("NOT KILLED: %s" % x)
    for x in bad_controls:
        print("CONTROL FAILED: %s" % x)
    print("\n%d of %d killed; %s" % (killed, total, "every control passes" if not bad_controls
                                      else "%d control(s) FAIL" % len(bad_controls)))
    return 1 if survived or bad_controls else 0


if __name__ == "__main__":
    sys.exit(main())
