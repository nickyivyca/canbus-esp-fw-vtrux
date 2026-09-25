#!/usr/bin/env python3
"""The standing negative control for E1: break the firmware, expect red.

Written by the reviewing session on 2026-09-25 and adopted here, because E1's
failure mode is silent greenness and this is the only thing that measures it.
Four of the seven mutations survived the first version of E1, INCLUDING the
exact defect it was written to catch.

  M1  632af32's alert-based completion reinstated -- the shipped bug
  M2  the core's diag deferral removed
  M3  the spec 3.2 refusal removed from the REAL main/can.c
  M4  the quiesce self_off reason removed
  M5  the receive-error self_off reason removed
  M6  PASSIVE allowed to reach build_inhibit()
  M7  the pre-queue alert drain removed
  N1  completion off-by-one, msgs_to_tx == 0 -> <= 1
  N2  `behind` forced false, so spec 5's tx_queued_behind never counts
  N3  a twai_reconfigure_alerts() failure ignored at arm
  N4  OBSERVE not forced listen-only
  N5  the spec 8 quiesce handshake does not wait -- the use-after-free guard
  N6  the probe offset limit relaxed from 4000 to 40000 us
  N7  the TX_LATE test removed from the core

N1-N7 were added by the reviewing session in a SECOND round against bea8d51,
after M1-M6 were all being caught. Five of them survived: N2, N3, N4, N5 and
N6. That is the pattern worth noticing -- each round of "all mutations caught"
has been followed by a round that found more, so a green mutation run means
"nothing in THIS set survives", never "the suite is adequate".

M7 is EXPECTED to survive and that is documented in gen_inhibit.c: with
completion decided by msgs_to_tx == 0 the drain only clears a stale TX_FAILED,
which is itself unreachable at ss = 0. Every other mutation must come back
rc=1.

READ rc, NOT THE TEXT. The reviewer's original scraped lines containing "FAIL",
which also matches the harness's own "twai_receive failed: ESP_FAIL" log lines,
so a surviving mutation was reported as CAUGHT. rc is the only signal.

Usage, from a tree containing main/ and test/:
    BASE=~/gi_review python3 mutate.py
"""

import subprocess, shutil, sys, os, re
BASE = os.path.expanduser(os.environ.get("BASE", "~/gi_review"))
MUT = {
 "M1_632af32_alert_completion": ("main/gen_inhibit.c", [(
  """    uint32_t alerts = 0;
    if (twai_read_alerts(&alerts, 0) == ESP_OK
        && (alerts & TWAI_ALERT_TX_FAILED))
    {
        s_tx_failed_latched = true;
    }

    if (!s_core.tx_pending)
    {
        return;
    }""",
  """    uint32_t alerts = 0;
    if (twai_read_alerts(&alerts, 0) != ESP_OK) return;
    if (!s_core.tx_pending) return;
    if (alerts & (TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED))
    {
        gi_on_tx_done(&s_core, (alerts & TWAI_ALERT_TX_SUCCESS) != 0, esp_timer_get_time(), ev);
    }
    return;
    if (!s_core.tx_pending)
    {
        return;
    }""")]),
 "M2_no_diag_deferral": ("main/gen_inhibit_core.c", [(
  "    if (st->tx_pending)\n    {\n        /* fall through to the interlocks; the page goes out next tick */\n    }\n    else if (",
  "    if (0)\n    {\n    }\n    else if (")]),
 "M3_real_can_c_refusal_removed": ("main/can.c", [(
  "\tif(gen_inhibit_owns_bus())\n\t{\n\t\tstatic int64_t s_last_refusal_log;",
  "\tif(0)\n\t{\n\t\tstatic int64_t s_last_refusal_log;")]),
 "M4_quiesce_self_off_removed": ("main/gen_inhibit.c", [(
  "    s_core.self_off = GI_SELF_OFF_QUIESCE;", "    ")]),
 "M5_rx_error_self_off_removed": ("main/gen_inhibit_core.c", [(
  "        st->self_off = GI_SELF_OFF_RX_ERRORS;", "        ")]),
 "M6_passive_builds_frame": ("main/gen_inhibit_core.c", [(
  "            st->would_tx++;\n            return;", "            st->would_tx++;")]),
 "M7_no_predrain": ("main/gen_inhibit.c", [(
  "            (void)twai_read_alerts(&stale, 0);", "            (void)stale;")]),
 "N1_completion_off_by_one": ("main/gen_inhibit.c", [(
  "    if (info.msgs_to_tx == 0)", "    if (info.msgs_to_tx <= 1)")]),
 "N2_behind_forced_false": ("main/gen_inhibit.c", [(
  "                behind = (before.msgs_to_tx > 0);", "                behind = false;")]),
 "N3_alerts_config_failure_ignored": ("main/gen_inhibit.c", [(
  """        if (twai_reconfigure_alerts(TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED,
                                    NULL) != ESP_OK)
        {
            ESP_LOGE(TAG, "cannot arm: TX alerts would not enable");
            return ESP_ERR_INVALID_STATE;
        }""",
  """        (void)twai_reconfigure_alerts(TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_FAILED,
                                      NULL);""")]),
 "N4_observe_not_listen_only": ("main/gen_inhibit.c", [(
  "        const bool want_listen_only = (mode == GEN_INHIBIT_OBSERVE);",
  "        const bool want_listen_only = false;")]),
 "N5_quiesce_does_not_wait": ("main/gen_inhibit.c", [(
  "    for (int i = 0; i < 100 && !s_parked; i++)",
  "    for (int i = 0; i < 0 && !s_parked; i++)")]),
 "N6_offset_limit_relaxed": ("main/gen_inhibit.c", [(
  "    if (offset_us > 4000)", "    if (offset_us > 40000)")]),
 "N7_no_tx_late": ("main/gen_inhibit_core.c", [(
  "    if (st->tx_pending && st->inhibit_live)", "    if (0)")]),
}
# A GREEN MUTATION RUN MEANS NOTHING IF THE BASELINE IS RED: every mutant is
# then reported caught by a failure that was already there. That happened once,
# with all fourteen "caught" and two baseline assertions failing.
_t = os.path.join(BASE, "test/gen_inhibit_shim")
_b = subprocess.run("make -s && ./shim_test", shell=True, cwd=_t,
                    capture_output=True, text=True)
if _b.returncode:
    print("BASELINE IS RED (rc=%d) -- fix it before reading anything below"
          % _b.returncode)
    for _l in _b.stdout.splitlines():
        if _l.strip().startswith("FAIL:"):
            print("   ", _l.strip()[:140])
    sys.exit(2)
print("baseline green")

for name, (f, reps) in MUT.items():
    d = os.path.expanduser("~/gi_mut")
    shutil.rmtree(d, ignore_errors=True); shutil.copytree(BASE, d)
    p = os.path.join(d, f); s = open(p).read()
    ok = True
    for a, b in reps:
        if s.count(a) != 1: ok = False; print(name, "PATTERN NOT FOUND", s.count(a)); break
        s = s.replace(a, b)
    if not ok: continue
    open(p, "w").write(s)
    t = os.path.join(d, "test/gen_inhibit_shim")
    b = subprocess.run("make -s", shell=True, cwd=t, capture_output=True, text=True)
    if b.returncode: print(name, "BUILD FAIL", b.stderr[-300:]); continue
    r = subprocess.run("./shim_test", shell=True, cwd=t, capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    # rc, not text: "FAIL" also matches the harness's own ESP_FAIL log lines,
    # which reported surviving mutations as caught.
    fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL:")][:3]
    verdict = ("CAUGHT" if r.returncode else "*** SURVIVED ***")
    print("%-32s rc=%d  %-16s %s" % (name, r.returncode, verdict, " | ".join(fails)))
