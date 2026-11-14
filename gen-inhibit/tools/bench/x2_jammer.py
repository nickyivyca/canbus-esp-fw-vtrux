"""Host side of the X2 jammer (x2_jammer/, test equipment, NOT FOR VEHICLE).

Gen-inhibit tester, 2026-10-08. Talks to the jammer image on the
ESP32-CAN-X2's USB-Serial-JTAG console (named by its USB IDs in GI_X2_DEVICE and found
through gi_serial and the core's serial_dev, DEVICES-SPEC S1, 2026-11-12), watches the
WiCAN's segment with an independent witness (Kvaser, NORMAL mode -- a silent
Kvaser reports 0 error frames whatever happens), and judges what was
DELIVERED, never what was commanded.

Pure functions (tested by test_x2_jammer.py, no hardware):
  parse_line(text)          one console line -> dict, or None if not ours
  judge_stop(...)           did delivery stop by the deadline? (timeout checks)
  clusters(ts, gap_s)       error-frame times -> groups, one per pulse
  per_pulse(...)            witness error frames and WiCAN bus_error per pulse
  judge_identity(...)       the ESP-IDF port's banner against its archive row
  after_done(...)           witness error frames from DONE to the next command
  judge_pulses(...)         PULSES' verdict: n groups of one, quiet before and after DONE
  judge_boot_silence(...)   boot-silence's verdict, with its closing pulse as the control
  judge_done(...)         DONE's stop_by= / end= against the variant's stop
  fold_timeout(...)         the timeout verdict with those two folded in
  judge_can2_isolation(...) CAN2 isolation without the WiCAN
  args_why(a)               the argument refusals
(2026-11-13, for the ESP-IDF port's bench pass on madhouse, x2_test_images SPEC T8: the
identity verdict, the PULSES verdict, the count from DONE to the next command (the
gen-inhibit reviewer's bench note: jam_pulse's close_channel deletes the RMT channel and then
drives the pin again, so TXD may float for a moment), the DONE-line checks for stop (a) alone
and stop (b) alone, --no-wican and can2-isolation.)

Bench subcommands (each needs the bench lock; each writes
runs/x2_jammer_<sub>_<stamp>.json and never overwrites):
  id                 ask the board for its banner (no reset); with --expect-name and
                     --expect-elf (the row's project_name and app_elf_sha256), a verdict
  boot-silence       reset the X2 N times; witness must see 0 error frames
                     from each reset to banner + 5 s, and then exactly 1 for a closing
                     PULSES w 1 (the positive control, else INCONCLUSIVE; 2026-11-13)

A witness whose read raised, or whose thread ended before the close, makes the run
NOT-RUN (WitnessDown): its record is cut short, and a cut-short record reads as a quiet
bus (2026-11-13). pulses and timeout (and their after-DONE count) are VOID (LagVoid) unless
the witness's median receipt lag is under TIMING_LAG_S, 0.25 s, fixed before any run and
below the judges' 0.3 s margin (gen-inhibit reviewer, 2026-11-13); the other subs keep
witness_clock's 2 s sanity limit.
  pulses             PULSES w n interval_ms with the WiCAN in --wican-mode,
                     page polled ~10 Hz: port check (n=1) and calibration. Verdict
                     (judge_pulses): n groups of exactly one, none in the 4 s before,
                     none from DONE + 0.3 s over --post-s (default 3 s)
  timeout            TRAIN w p ms; judge_stop against ms (and a negative
                     control against --control-ms, which must FAIL);
                     --host-quiet stops reading the console during the train;
                     --expect-stop-by / --expect-end judge the DONE line, and an
                     error frame after DONE FAILs
  noduration         "TRAIN w p" with no ms: must be refused, witness quiet
                     from the send (its reply is the DONE) for 3 s
  can2-check         WiCAN in mode 2 + n pulses; the Kvaser (WiCAN segment)
                     must see pages and pulses, the Innomaker (X2 CAN2, libusb) neither
  can2-isolation     the form without the WiCAN, for madhouse (gen-inhibit reviewer,
                     2026-11-13, option (i)); needs --no-wican: the Kvaser and canchg up
                     through canctl (SocketCAN, normal, ACKing) while the PCAN sends 50 0x7A0
                     on CAN1 and then the jammer sends n pulses; the Kvaser must see
                     every 0x7A0 and one group per pulse, canchg nothing, then hear its own
                     unACKed frame; then can2_ack_probe's probe on canchg must come back
                     NO_ACK. Refused while another process holds the PCAN, and with
                     --interval-ms under 500 (the groups' fixed 0.1 s gap)

  session            (2026-11-13, the gen-inhibit reviewer's ruling (C)) the PCAN as the timing
                     witness for a list of checks, `--check "<sub and its args>"` repeated
                     (boot-silence, pulses, timeout, noduration; never can2-isolation, whose 0x7A0
                     sender the PCAN is; id needs no witness). One console; PCAN opens
                     (bench_witness.PcanWitness, refused unless the named PCAN-USB on
                     PCAN_USBBUS1 with ts_mode 3), every check driven, then each judged at the end
                     on its OPEN's anchor (witness_anchor: offset = min(receipt - device), slope,
                     spread under 0.25 s, the per-command bracket); each check only on the frames
                     of its open mapped into its own span.
                     PER-OPEN ANCHORS (gen-inhibit reviewer's ruling of 2026-11-13, after session 1
                     at e61f4030: the PCAN in normal mode stops reporting bus errors once error
                     passive, after ~128 pulses of one open, and listen-only reports none):
                     consecutive boot-silence, pulses and noduration checks share one open, each
                     timeout check (a train) gets its own, opened afresh LEAD_S (fixed, at most
                     LEAD_MAX_S) before its expected stop, the effective ms from the host's send,
                     and kept through its closing pulse; a timeout whose period puts more than
                     LEAD_PULSES_MAX pulses in the lead is refused. A train judged from a late
                     open sees its end, not its start: judge_stop with start_observed False drops
                     the quiet-before-start rule and keeps the rest (frames up to the deadline,
                     none after it plus the margin, watched quiet_s beyond). One anchor per open
                     (a second open within one still refuses); a controller-state frame on an
                     open is WitnessDown from its receipt, and every check of that open not over
                     by then is NOT-RUN. Each check names its open and the open's anchor.
                     The record keeps every open (anchor, method, bounds, frames), every
                     bracket's margins and each check's resource-log window (P1 (5): a check
                     counts only with a clean record there; gen-inhibit's slice marks are not yet
                     set, so that is read by hand). One file, runs/x2_jammer_session_<stamp>.json.

WITNESSES since the Kvaser moved to kvaser_usb (2026-11-13, ruling (C)): the session's checks on
the PCAN; can2-isolation on the Kvaser through SocketCAN (bench_witness.SocketcanWitness: `kvaser`
only, software stamps used only for the gaps in its groups, counts only, berr off: kvaser_usb_flex
refuses it); id on none; the single-check subs as before on the Kvaser through CANlib (kvaser_bus),
which no longer opens while the Kvaser is on kvaser_usb. Every record says which.

EVERY JUDGED SILENCE ENDS WITH A FRAME THE SAME WITNESS MUST COUNT (gen-inhibit reviewer,
2026-11-13, after CAN replay's degraded-USB advisory): boot silence, pulses' and the timeout's
window after DONE, the timeout's silence after its stop and no duration's quiet each end at a
closing PULSES w 1, counted once from its send to a second past its DONE, else a PASS is
INCONCLUSIVE; can2-isolation's "canchg nothing" ends with a frame canchg sends itself on the same
socket, whose ACK errors its reader must hear (nothing else on CAN2 can send). A failed read on
any witness socket, canchg's included, is WitnessDown (NOT-RUN).

THE USB RECORD (same ruling): the hubs are bus-powered, and the GL850G at 1-3.2 holds the Kvaser,
the PCAN, canmain and the WiCAN console. `journalctl -k` is read over the run (the session's
whole span, or a single sub's), and an EPROTO, -71, reset or disconnect line naming a 1-3.2.x
device VOIDs every check whose window holds it, the line kept in the record; a kernel log that
cannot be read VOIDs too.

--no-wican: no HTTP to the WiCAN at all, the record says "not read: --no-wican" where its
page would be. madhouse has no Wi-Fi adapter, and there the WiCAN's 192.168.80.1 routes out
the default gateway, off the house LAN (2026-11-13). Refused with --wican-mode and can2-check.

The console is opened by serial_noreset.open_console, so opening and closing do
not reset the S3, on Linux too (2026-11-11: the old form, DTR and RTS pre-set
low, resets it there); `boot-silence` resets it deliberately with esptool's
USB-JTAG ordering (console_capture.pulse_rts).

Error-frame times are the Kvaser's hardware stamps, on the epoch clock. When they
are not on it, the run is VOID and nothing more is judged: the witness never
judges on receipt time (witness_clock.py; the gen-inhibit reviewer's A2 ruling
(b), 2026-11-11). Witness.times() raises witness_clock.ReceiptClock, which main
records as the VOID; Witness.recorded() still gives the times on either clock.

  py -3.14 gen-inhibit/tools/bench/x2_jammer.py <sub> [...]
"""
import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import witness_clock as WC  # noqa: E402  never judged on receipt time (A2 (b), 2026-11-11)
import witness_anchor as WA  # noqa: E402  the PCAN session's anchor (ruling (C), 2026-11-13)
from bench_witness import GROUP_GAP_S, MIN_INTERVAL_MS  # noqa: E402  can2-isolation's grouping

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
BITRATE = 500000
KINDS = ("ACK", "DONE", "PULSE", "ERR", "CAPPED", "STATUS")
LINE_RE = re.compile(r"^JAMMER (\S+)(.*)$")
KV_RE = re.compile(r"(\w+)=(\S+)")
INT_RE = re.compile(r"-?\d+")


# ---------------------------------------------------------------- pure parts

def parse_line(text):
    """'JAMMER <KIND> ...k=v...' -> {'kind', 'what', k: v...}; None if not ours.

    Banner lines (no KIND word) come back as kind 'INFO'. Integers are ints.
    """
    m = LINE_RE.match(text.strip())
    if not m:
        return None
    head, rest = m.group(1), m.group(2)
    if head in KINDS:
        d = {"kind": head}
        words = rest.split()
        d["what"] = words[0] if words and "=" not in words[0] else ""
        body = rest
    else:
        d = {"kind": "INFO", "what": ""}
        body = head + rest
    for k, v in KV_RE.findall(body):
        d[k] = int(v) if INT_RE.fullmatch(v) else v
    d["raw"] = text.strip()
    return d


def judge_stop(err_ts, t_start, t_deadline, obs_start, obs_end,
               margin_s=0.3, quiet_s=5.0, pre_s=2.0, seen_s=0.5, start_observed=True):
    """Did the witness's error frames stop by the deadline?

    err_ts: witness error-frame times, same clock as the other arguments.
    PASS needs: the bus observed quiet for pre_s before the start; at least one
      error frame between start and deadline (else there was nothing to stop);
      none after deadline + margin, observed for quiet_s beyond that.
    FAIL: any error frame after deadline + margin.
    INCONCLUSIVE: not observed long enough, the bus not quiet before,
      nothing delivered, or the last frame seen more than seen_s before the
      deadline (the witness may have stopped seeing -- the Kvaser reported
      nothing for 1.5 s mid-train on 2026-10-08 01:24 -- so "none after" would
      be vacuous).
    start_observed False (a train's fresh PCAN open LEAD_S before its stop, the gen-inhibit
      reviewer, 2026-11-13): the witness saw the train's end, not its start, so the quiet before
      the start is not judged; every other rule stands.
    """
    pre = [t for t in err_ts if t_start - pre_s <= t < t_start - margin_s]
    inside = [t for t in err_ts if t_start - margin_s <= t <= t_deadline + margin_s]
    after = [t for t in err_ts if t > t_deadline + margin_s]
    out = {"n_pre": len(pre), "n_in": len(inside), "n_after": len(after),
           "margin_s": margin_s, "start_observed": start_observed,
           "last_in_rel_deadline_s": (round(max(inside) - t_deadline, 4)
                                      if inside else None),
           "first_after_rel_deadline_s": (round(min(after) - t_deadline, 4)
                                          if after else None)}
    if after:
        out["verdict"], out["why"] = "FAIL", "error frames after the deadline"
    elif (start_observed and obs_start > t_start - pre_s) or obs_end < t_deadline + margin_s + quiet_s:
        out["verdict"], out["why"] = "INCONCLUSIVE", "not observed long enough"
    elif pre:
        out["verdict"], out["why"] = "INCONCLUSIVE", "bus not quiet before start"
    elif not inside:
        out["verdict"], out["why"] = "INCONCLUSIVE", "nothing delivered"
    elif max(inside) < t_deadline - seen_s:
        out["verdict"], out["why"] = ("INCONCLUSIVE",
                                      "witness stopped seeing before the deadline")
    else:
        out["verdict"], out["why"] = "PASS", ""
    return out


def judge_counter_stop(samples, t_start, t_deadline, margin_s=0.3, quiet_s=5.0,
                       max_gap_s=1.0, seen_s=0.5):
    """Second witness of the stop: a cumulative error counter read off a page.

    samples: [(t, value or None)], same clock. An increase is placed in the
    interval (t_prev, t]. FAIL if one starts at or after deadline + margin.
    INCONCLUSIVE if the counter never rose during the train, the readings do
    not reach deadline + margin + quiet_s, or any gap after the start exceeds
    max_gap_s (a rise could hide in it), or the last rise ended more than
    seen_s before the deadline (e.g. the device went bus-off and stopped
    counting, so "no late rise" would be vacuous). PASS otherwise.
    """
    good = [(t, v) for t, v in samples if v is not None]
    rises = [(a[0], b[0], b[1] - a[1]) for a, b in zip(good, good[1:])
             if b[1] > a[1]]
    late = [r for r in rises if r[0] >= t_deadline + margin_s]
    during = [r for r in rises if r[1] >= t_start and r[0] < t_deadline + margin_s]
    after_start = [t for t, _ in good if t >= t_start]
    gaps = [b - a for a, b in zip(after_start, after_start[1:])]
    out = {"n_readings": len(good), "rises_during": sum(r[2] for r in during),
           "rises_late": sum(r[2] for r in late),
           "last_rise_end_rel_deadline_s": (round(max(r[1] for r in during) - t_deadline, 3)
                                            if during else None),
           "max_gap_s": round(max(gaps), 3) if gaps else None}
    if late:
        out["verdict"], out["why"] = "FAIL", "counter rose after the deadline"
    elif not during:
        out["verdict"], out["why"] = "INCONCLUSIVE", "counter never rose"
    elif not good or good[-1][0] < t_deadline + margin_s + quiet_s:
        out["verdict"], out["why"] = "INCONCLUSIVE", "not observed long enough"
    elif gaps and max(gaps) > max_gap_s:
        out["verdict"], out["why"] = "INCONCLUSIVE", "gap in readings"
    elif max(r[1] for r in during) < t_deadline - seen_s:
        out["verdict"], out["why"] = ("INCONCLUSIVE",
                                      "counter stopped rising before the deadline")
    else:
        out["verdict"], out["why"] = "PASS", ""
    return out


def combine(mains, controls):
    """Overall timeout verdict from per-witness verdicts.

    mains / controls: {witness: verdict}. A witness's negative control must
    FAIL only where that witness's own verdict is PASS: a witness that went
    blind (INCONCLUSIVE) cannot fail its control either, and demanding it did
    turned the 2026-10-08 01:25 run into a FAIL although the Kvaser witness
    passed and its control failed. FAIL if any witness FAILs or a passing
    witness's control does not FAIL; PASS if at least one witness PASSes;
    otherwise INCONCLUSIVE.
    """
    bad_controls = [w for w, v in mains.items()
                    if v == "PASS" and w in controls and controls[w] != "FAIL"]
    if "FAIL" in mains.values():
        return "FAIL", "a witness saw delivery after the deadline"
    if bad_controls:
        return "FAIL", "negative control did not fail: %s" % ",".join(bad_controls)
    if "PASS" in mains.values():
        return "PASS", ""
    return "INCONCLUSIVE", "no witness could show the stop"


def judge_can2(kv_pages, kv_groups, n_pulses, in_frames, in_err):
    """Does the jammer reach ONLY the WiCAN's segment (reviewer, 2026-10-08)?

    The Kvaser on the WiCAN's segment is the positive control: it must see the
    WiCAN's pages and one error-frame group per pulse. The Innomaker, on the
    X2's CAN2 (user), ACKing in normal mode, must see neither frames nor error
    frames. With the jammer image there is no bridge, so any WiCAN page on the
    Innomaker means it shares the segment.
    Note the limit: whether gs_usb reports error frames at all is not shown
    here, so "0 error frames" on it rests on the frame half of the check.
    """
    if kv_pages < 1 or kv_groups != n_pulses:
        return "INCONCLUSIVE", "positive control failed on the Kvaser"
    if in_frames or in_err:
        return "FAIL", "the Innomaker saw traffic from the WiCAN's segment"
    return "PASS", ""


BANNER_NAME = "JAMMER %s -- TEST EQUIPMENT, NEVER ON THE TRUCK"


def judge_identity(lines, expect_name, expect_elf):
    """The ESP-IDF port's banner against the archive row it was flashed from (2026-11-13,
    the port's bench pass, x2_test_images SPEC T8 "identity").

    lines: parsed console lines (parse_line) after ID was sent. PASS needs the name line
    "JAMMER <expect_name> -- TEST EQUIPMENT, NEVER ON THE TRUCK" with NOT_FOR_VEHICLE in
    the name, the build line's elf= equal to expect_elf (the row's app_elf_sha256), the tx
    line's twai=never_started and the mcp2515 line's config_mode=yes. Any of them missing
    or different is a FAIL, with every one that failed named."""
    info = [p for p in lines if p and p.get("kind") == "INFO"]
    name = next((p["raw"] for p in info if p["raw"].endswith(" -- TEST EQUIPMENT, NEVER ON THE TRUCK")),
                None)
    elf = next((str(p["elf"]) for p in info if "elf" in p and "version" in p), None)
    twai = next((p.get("twai") for p in info if "twai" in p), None)
    cfg = next((p.get("config_mode") for p in info if "mcp2515" in p["raw"]), None)
    stops = next((p.get("stops") for p in info if "stops" in p), None)
    why = []
    if not expect_name or "NOT_FOR_VEHICLE" not in expect_name or name != BANNER_NAME % expect_name:
        why.append("name line %r, expected %r" % (name, BANNER_NAME % expect_name))
    if not expect_elf or elf != expect_elf:
        why.append("elf %s, expected %s" % (elf, expect_elf))
    if twai != "never_started":
        why.append("twai=%s" % twai)
    if cfg != "yes":
        why.append("mcp2515 config_mode=%s" % cfg)
    return {"verdict": "FAIL" if why else "PASS", "why": "; ".join(why), "name_line": name,
            "elf": elf, "twai": twai, "config_mode": cfg, "stops": stops}


def after_done(err_ts, t_done, obs_end, margin_s=0.3, post_s=3.0):
    """Witness error frames from a command's DONE to the next command (the gen-inhibit
    reviewer's bench note, 2026-11-13: jam_pulse's close_channel deletes the RMT channel and
    then drives the pin again, so TXD may float for a moment; the judge counts error frames
    from DONE until the next command, not only during trains).

    The window is (t_done + margin_s, obs_end]; the run's next command is the STOP it sends
    as it closes, after obs_end. n counts the frames in it; observed_s is its length, and a
    window shorter than post_s is not long enough to show anything (enough False)."""
    if t_done is None:
        return {"n": None, "observed_s": None, "enough": False}
    lo = t_done + margin_s
    return {"n": sum(1 for t in err_ts if lo < t <= obs_end),
            "observed_s": round(obs_end - lo, 3), "enough": obs_end - lo >= post_s}


def judge_pulses(err_ts, n_cmd, interval_s, t_send, ack, t_done, obs_end,
                 margin_s=0.3, pre_s=4.0, post_s=3.0):
    """Verdict for PULSES: the port check (n=1) and calibration (2026-11-13; before, the
    record was read by hand, as for the 2026-10-08 commissioning).

    INCONCLUSIVE: no ACK, no DONE, error frames in the pre_s before the send (the bus was
    not quiet), or the window after DONE shorter than post_s. FAIL: the frames from the send
    to DONE are not n_cmd groups of exactly one (per_pulse), or any frame after DONE
    (after_done). PASS otherwise."""
    pre = [t for t in err_ts if t_send - pre_s <= t < t_send - margin_s]
    hi = t_done + margin_s if t_done is not None else obs_end
    pp = per_pulse([t for t in err_ts if t_send - margin_s <= t <= hi], n_cmd, interval_s)
    post = after_done(err_ts, t_done, obs_end, margin_s, post_s)
    out = {"n_pre": len(pre), "per_pulse": pp, "after_done": post}
    if not ack or t_done is None:
        out["verdict"], out["why"] = "INCONCLUSIVE", "no ACK or no DONE"
    elif pre:
        out["verdict"], out["why"] = "INCONCLUSIVE", "bus not quiet before the send"
    elif post["n"]:
        out["verdict"], out["why"] = "FAIL", "error frames after DONE"
    elif not pp["exactly_one_each"]:
        out["verdict"], out["why"] = ("FAIL", "%d groups %s for %d pulses"
                                      % (pp["groups"], pp["per_pulse"], n_cmd))
    elif not post["enough"]:
        out["verdict"], out["why"] = "INCONCLUSIVE", "after DONE not observed long enough"
    else:
        out["verdict"], out["why"] = "PASS", ""
    return out


def judge_boot_silence(resets, control):
    """boot-silence's verdict -> (verdict, why). resets: one dict per reset (banner_after_s,
    witness_err_frames, config_mode); control: the closing pulse (witness_err_frames counted
    from its send to a second past its DONE, done). FAIL: a reset's window held an error
    frame, or a reset showed no banner or no MCP2515 configuration mode. INCONCLUSIVE: the
    closing pulse was not counted exactly once, so the witness may not have been hearing
    the windows (a deaf witness reports none; 2026-11-13). PASS otherwise."""
    if not resets:
        return "INCONCLUSIVE", "no reset made"
    bad = [r.get("n") for r in resets if r.get("witness_err_frames") != 0]
    if bad:
        return "FAIL", "error frames after reset(s) %s" % bad
    bad = [r.get("n") for r in resets
           if r.get("banner_after_s") is None or r.get("config_mode") != "yes"]
    if bad:
        return "FAIL", "no banner or MCP2515 not in configuration mode after reset(s) %s" % bad
    if not control or control.get("witness_err_frames") != 1:
        return "INCONCLUSIVE", "positive control: the witness counted %s for the closing pulse, not 1" % (
            (control or {}).get("witness_err_frames"))
    if not control.get("done"):
        return "INCONCLUSIVE", "positive control: no DONE for the closing pulse"
    return "PASS", "%d resets silent, closing pulse seen once" % len(resets)


def judge_done(done, expect_stop_by=None, expect_end=None):
    """The DONE line's stop_by= and end= against what the variant must show (2026-11-13:
    stop (a) alone on stop_a_only, stop_by=deadline_timer; stop (b) alone on stop_b_only,
    end=deadline_layer_b and stop_by=none). (True, "") when nothing is expected or all
    match; (None, why) with no DONE line; (False, why) on a mismatch."""
    want = {k: v for k, v in (("stop_by", expect_stop_by), ("end", expect_end)) if v}
    if not want:
        return True, ""
    if not done:
        return None, "no DONE line to judge %s" % ",".join(sorted(want))
    bad = ["%s=%s, expected %s" % (k, done.get(k), v) for k, v in sorted(want.items())
           if done.get(k) != v]
    return (False, "; ".join(bad)) if bad else (True, "")


def fold_timeout(verdict, why, post, done_check):
    """The timeout verdict with the window after DONE and the DONE line folded in
    (2026-11-13). Error frames after DONE, or a DONE line that does not match, FAIL; no DONE
    line to judge, when one was expected, makes a PASS INCONCLUSIVE. A FAIL stays a FAIL.
    The window after DONE is not required to be long here: judge_stop already needs the bus
    watched quiet to deadline + margin + 5 s."""
    ok, dwhy = done_check
    if verdict == "FAIL":
        return verdict, why
    if post.get("n"):
        return "FAIL", "error frames after DONE"
    if ok is False:
        return "FAIL", dwhy
    if ok is None and verdict == "PASS":
        return "INCONCLUSIVE", dwhy
    return verdict, why


def clusters(ts, gap_s):
    """Sorted times split wherever the gap exceeds gap_s."""
    out = []
    for t in sorted(ts):
        if out and t - out[-1][-1] <= gap_s:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def per_pulse(err_ts, n_cmd, interval_s, samples=None, slack_s=0.05):
    """Witness error frames per pulse, and the WiCAN's bus_error delta per pulse.

    Clock-free attribution: error frames are grouped by gaps > interval/2, one
    group per delivered pulse. samples: [(t, bus_error or None)] or
    [(t, bus_error or None, req_start or None)] from the WiCAN page, same
    clock as err_ts, t when the response arrived and req_start when the
    request was sent. The page was built somewhere between the two, so a
    reading is "before" a group only if it RETURNED before the group's first
    error frame, and "after" it only if it was SENT after the group's last
    one. A pulse's delta is the first reading after its group minus the last
    reading before it, and is None if either reading is missing or another
    group falls between them: the "before" reading sent before the previous
    group, or the "after" reading returned after the next one.
    A reading with no req_start is taken as sent at t, the rule before
    2026-11-05. Then a stalled poll returning after a group showed a page
    built before it: step 1d's a61f n9_lead5 arm 0, a poll sent 0.64 s
    before pulse 2's error frame returned 0.50 s after it, and that pulse's
    delta came out 0 though the device counted it (gen-inhibit reviewer,
    2026-11-05).
    slack_s: readings within slack_s of a group are not used. The page is
    stamped on the host clock and error frames on the Kvaser's; on
    2026-10-08 01:45 a reading 1.4 ms BEFORE a pulse's stamped error frame
    already counted it, and the delta came out 0 with no slack.
    """
    cl = clusters(err_ts, interval_s / 2.0)
    counts = [len(c) for c in cl]
    deltas = []
    good = []    # (returned, bus_error, sent)
    for s in samples or []:
        if s[1] is not None:
            good.append((s[0], s[1], s[2] if len(s) > 2 and s[2] is not None else s[0]))
    for i, c in enumerate(cl):
        before = sorted((s for s in good if s[0] < c[0] - slack_s), key=lambda s: s[0])
        after = sorted((s for s in good if s[2] > c[-1] + slack_s), key=lambda s: s[2])
        if not before or not after:
            deltas.append(None)
            continue
        b, a = before[-1], after[0]
        lo = cl[i - 1][-1] if i > 0 else float("-inf")
        hi = cl[i + 1][0] if i + 1 < len(cl) else float("inf")
        if b[2] <= lo or a[0] >= hi:
            deltas.append(None)
            continue
        deltas.append(a[1] - b[1])
    return {"commanded": n_cmd, "groups": len(cl), "per_pulse": counts,
            "total": sum(counts), "bus_error_delta_per_pulse": deltas,
            "exactly_one_each": len(cl) == n_cmd and all(c == 1 for c in counts)}


# ------------------------------------------------------------- bench parts

class Console(threading.Thread):
    """The X2's console reader; survives a reset (reopens) and can be paused. port: the
    by-id path gi_serial resolved (no default: never a tty name, DEVICES-SPEC S1)."""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port
        self.stop_flag = threading.Event()
        self.paused = threading.Event()
        self.lines = []      # (epoch, text, parsed)
        self.events = []     # (epoch, what)
        self.lock = threading.Lock()
        self.ser = None

    def _open(self):
        # Without a reset on Linux too (serial_noreset.py, 2026-11-11). Imported here, so
        # the copies of this file other tests make need nothing beside them.
        from serial_noreset import open_console
        return open_console(self.port, 115200, 0.05)

    def run(self):
        pending, first = b"", True
        while not self.stop_flag.is_set():
            if self.paused.is_set():
                time.sleep(0.05)
                continue
            if self.ser is None:
                try:
                    self.ser = self._open()
                    self.events.append((time.time(), "open" if first else "reopened"))
                    first = False
                except Exception:
                    time.sleep(0.05)
                    continue
            try:
                chunk = self.ser.read(4096)
            except Exception as e:
                self.events.append((time.time(), "lost: %s" % type(e).__name__))
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                continue
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                txt = raw.decode("latin-1").rstrip("\r")
                with self.lock:
                    self.lines.append((time.time(), txt, parse_line(txt)))
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass

    def send(self, line):
        self.ser.write((line + "\n").encode("ascii"))
        self.events.append((time.time(), "sent: %s" % line))

    def wait(self, pred, timeout, since):
        """First parsed line after epoch `since` satisfying pred, or None."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for t, _txt, p in self.lines:
                    if t >= since and p is not None and pred(p):
                        return t, p
            time.sleep(0.02)
        return None

    def pulse_reset(self):
        """esptool's USB-JTAG hard reset (see console_capture.pulse_rts)."""
        s = self.ser
        s.dtr = False
        s.rts = True
        s.dtr = False
        s.rts = True
        time.sleep(0.2)
        s.rts = False
        s.dtr = False
        s.rts = False
        self.events.append((time.time(), "reset pulsed"))


class WitnessDown(RuntimeError):
    """The witness stopped reading before the check was judged: what it recorded is cut
    short, and a cut-short record reads as a quiet bus."""


# The timing subs' lag bound (gen-inhibit reviewer, 2026-11-13). A median receipt lag of L
# may be a stamp offset rather than delivery delay, and an offset moves every frame by L
# across a window's edge. So pulses, timeout and their after-DONE count are VOID unless the
# lag is under this bound, fixed before any run and below the smallest judging margin
# (judge_stop's and after_done's 0.3 s). It is never set from the run it judges: lags near
# or over it go to the reviewer. witness_clock's MAX_LAG_S (2 s) stays the sanity limit for
# the other subs (id, boot silence's counts).
TIMING_LAG_S = 0.25


class LagVoid(RuntimeError):
    def __init__(self, lag):
        super().__init__("median receipt lag %.3f s is not under the timing bound %.2f s"
                         % (lag, TIMING_LAG_S))
        self.lag = lag


def require_timing_lag(lag):
    """Raises LagVoid unless lag (s; None when nothing was heard) is under TIMING_LAG_S
    either way."""
    if lag is not None and not abs(lag) < TIMING_LAG_S:
        raise LagVoid(lag)


class Witness(threading.Thread):
    """Kvaser in NORMAL mode: records every error frame and the 0x051 count.

    A read that raises ends the thread; run() keeps the exception in .failed (with its
    time), and times() then raises WitnessDown, as it does when the thread has ended
    before close(), so no check is judged on a record cut short (2026-11-13, found in the
    review of the charge-interposer tester's slcan_spi witness: a dead reader thread
    would have passed boot silence and the "none after" of the stop checks)."""

    def __init__(self):
        super().__init__(daemon=True)
        import can
        from kvaser_bus import kvaser_bus  # noqa: E402  the pinned can-tools' Linux fixes (2026-11-11)
        self.bus = kvaser_bus(channel=0, bitrate=BITRATE)
        self._state()

    def _state(self):
        self.stop_flag = threading.Event()
        self.err = []        # (hw_ts, receipt_epoch)
        self.frames = 0
        self.ids = {}
        self.t_open = time.time()
        self.lock = threading.Lock()
        self.failed = None   # (epoch, "Type: message") when a read raised

    def run(self):
        try:
            self._read()
        except Exception as e:      # noqa: BLE001  kept, and every judgement refuses on it
            self.failed = (time.time(), "%s: %s" % (type(e).__name__, e))

    def _read(self):
        while not self.stop_flag.is_set():
            m = self.bus.recv(timeout=0.05)
            while m is not None:
                now = time.time()
                with self.lock:
                    if m.is_error_frame:
                        self.err.append((m.timestamp, now))
                    else:
                        self.frames += 1
                        k = "0x%03X" % m.arbitration_id
                        self.ids[k] = self.ids.get(k, 0) + 1
                m = self.bus.recv(timeout=0.0)

    def down(self):
        """Why the record cannot be judged, or None: a read raised, or the thread ended
        before close()."""
        if self.failed:
            return "witness read failed at %.3f: %s" % self.failed
        if not self.stop_flag.is_set() and not self.is_alive():
            return "witness thread not running"
        return None

    def close(self):
        self.stop_flag.set()
        self.join(timeout=2.0)
        self.bus.shutdown()

    def recorded(self):
        """Error-frame times, and which clock was used: the hardware stamps when they
        are on the epoch clock, else receipt time. For the record only."""
        with self.lock:
            e = list(self.err)
        if not e:
            return [], WC.NONE
        off = statistics.median(r - h for h, r in e)
        if abs(off) < WC.MAX_LAG_S:
            return [h for h, _ in e], WC.hw_note(off, 3)
        return [r for _, r in e], "%s: offset %.1f s" % (WC.RECEIPT, off)

    def lag(self):
        """The median receipt lag behind the hardware stamps (s), or None with nothing heard."""
        with self.lock:
            e = list(self.err)
        return statistics.median(r - h for h, r in e) if e else None

    def times(self, timing=False):
        """recorded(), for a judgement: raises WC.ReceiptClock on receipt time, so the
        run is VOID and never judged on it (A2 (b), 2026-11-11). Raises WitnessDown when
        down() says the record is cut short (2026-11-13). timing=True (the subs whose
        judges have margins: pulses, timeout and their after-DONE count) also raises
        LagVoid unless the median lag is under TIMING_LAG_S (2026-11-13)."""
        why = self.down()
        if why:
            raise WitnessDown(why)
        ts, note = self.recorded()
        WC.require(note)
        if timing:
            require_timing_lag(self.lag())
        return ts, note


def wican_snap():
    sys.path.insert(0, HERE)
    import lowid_flood_skip_bench as LF
    t0 = time.time()
    p = LF.page()
    if p is None:
        return None
    d = p.get("drv") or {}
    return {"t": time.time(), "req_start": t0, "mode": p.get("mode"), "drv_null": p.get("drv") is None,
            "bus_error": d.get("bus_error"), "tec": d.get("tec"), "rec": d.get("rec"),
            "state": d.get("state"), "bus_on": p.get("bus_on"),
            "tx_ok": p.get("tx_ok")}


def wican_mode(n):
    import lowid_flood_skip_bench as LF
    LF.dev_post("/gen_inhibit_set?mode=%d" % n)


def write_rec(sub, rec):
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, "x2_jammer_%s_%s.json" % (sub, rec["when"]))
    with open(path, "x", encoding="ascii", errors="replace") as fh:
        json.dump(rec, fh, indent=1)
    print("wrote", path)


def _console_lines(con):
    with con.lock:
        return list(con.lines)


def _console_dump(con, since=0.0):
    with con.lock:
        return [(round(t, 4), x) for t, x, _ in con.lines if t >= since]


def reset_since_open(events, up):
    """Whether the X2 reset after its console was opened (2026-11-11). events: the
    console's (time, what); up: the reply's uptime line as (time, parsed), or None.
    True when the port was lost or reopened (the USB device drops with the chip), or when
    the uptime is shorter than the time from the open to the line (the board booted
    after the open). None when there is no uptime line or no open to judge by. Else
    False. The jammer's boot banner and its reply to ID are the same lines, so only the
    uptime tells a reply from a boot."""
    if any(e.startswith("lost") or e == "reopened" for _t, e in events):
        return True
    opened = [t for t, e in events if e == "open"]
    if up is None or not opened or not isinstance(up[1].get("uptime_us"), int):
        return None
    return up[1]["uptime_us"] / 1e6 < up[0] - opened[0]


def sub_id(a, rec, con, wit):
    # An empty line first. The jammer runs a line only on LF and drops CR, so input an
    # earlier probe left unterminated (bench_identify's slcan "V\r" "N\r", 2026-10-19)
    # would join the ID line and come back unknown_command. The empty line runs whatever
    # is pending (an empty buffer is ignored) before ID is sent.
    con.send("")
    time.sleep(0.3)
    t = time.time()
    con.send("ID")
    got = con.wait(lambda p: p["kind"] == "INFO" and "mcp2515" in p["raw"], 3.0, t)
    # The banner's src_digest line, printed before the mcp2515 line, carries the uptime.
    up = con.wait(lambda p: p["kind"] == "INFO" and "uptime_us" in p, 0.5, t) if got else None
    rec["uptime_s"] = up[1]["uptime_us"] / 1e6 if up and isinstance(up[1].get("uptime_us"), int) else None
    # A banner only from a board that has not reset since the open: a reset at the open
    # (the old open form on Linux, 2026-11-11) would make a boot banner look like a reply.
    rec["reset_since_open"] = reset_since_open(list(con.events), up)
    rec["banner_ok"] = got is not None and rec["reset_since_open"] is False
    time.sleep(0.3)
    # The port's identity against its archive row (2026-11-13), when asked for: the banner's
    # lines after the ID, and a banner only from a board that has not reset since the open.
    if getattr(a, "expect_name", None) or getattr(a, "expect_elf", None):
        lines = [p for tt, _x, p in _console_lines(con) if tt >= t]
        j = judge_identity(lines, a.expect_name, a.expect_elf)
        if j["verdict"] == "PASS" and not rec["banner_ok"]:
            j["verdict"], j["why"] = "FAIL", "banner_ok false (no banner, or a reset since the open)"
        rec["identity"] = j
        rec["verdict"] = j["verdict"]
        print("identity %s %s" % (j["verdict"], j["why"]))


# Each witnessed sub is a drive part and a judge part (2026-11-13, the gen-inhibit reviewer's
# ruling (C)): drive_<sub>(a, con, t_open) works the jammer and returns what the judge needs,
# host times only; judge_<sub>(a, inp, ts, clock) judges the witness's error-frame times `ts`
# (host epoch clock). The per-process subs (sub_<sub>, the Kvaser through CANlib) judge at once
# on wit.times(); the PCAN session judges every check at its end, on the session's anchor. inp
# carries t_begin and t_end (the check's span: the session judges only the frames mapped into
# it) and "commands": [(host send, host receipt of its DONE or None)], the commands that put
# judged frames on the wire (the session's per-command bracket, witness_anchor.bracket).

# Every silence a check judges ends with a frame the same witness must count (the gen-inhibit
# reviewer, 2026-11-13, after CAN replay's degraded-USB advisory: a deaf witness reports a quiet
# bus, and only where silence is the pass does that matter). After boot silence's resets, pulses'
# and the timeout's window after DONE (and the timeout's silence after its stop) and no
# duration's quiet: one closing PULSES w 1, counted exactly once from its send to a second past
# its DONE, else a PASS is INCONCLUSIVE. The silence a check judges ends at the closing send.

def drive_closing(a, con, pre_s=1.0):
    time.sleep(pre_s)
    t_c = time.time()
    con.send("PULSES %d 1 1000" % a.w)
    done = con.wait(lambda p: p["kind"] == "DONE", 5.0, t_c)
    time.sleep(1.3)
    return {"t_send": t_c, "t_done": done[0] if done else None, "done": done[1] if done else None}


def closing_count(ts, c):
    end = (c["t_done"] if c["t_done"] is not None else c["t_send"] + 5.0) + 1.0
    return {"done": c["done"], "witness_err_frames": sum(1 for t in ts if c["t_send"] - 0.05 <= t <= end)}


def fold_closing(verdict, why, ctl):
    """A PASS stands only with the closing pulse counted once and its DONE; FAIL and
    INCONCLUSIVE stay (missed frames there are the safe direction)."""
    if verdict != "PASS":
        return verdict, why
    if not ctl or ctl.get("witness_err_frames") != 1:
        return "INCONCLUSIVE", ("closing pulse: the witness counted %s, not 1 (a deaf witness reports a "
                                "quiet bus)" % (ctl or {}).get("witness_err_frames"))
    if not ctl.get("done"):
        return "INCONCLUSIVE", "closing pulse: no DONE"
    return verdict, why


def drive_boot_silence(a, con, t_open=None):
    inp = {"t_begin": time.time(), "resets": []}
    for k in range(a.resets):
        time.sleep(1.0)
        t0 = time.time()
        con.pulse_reset()
        got = con.wait(lambda p: p["kind"] == "INFO" and "mcp2515" in p["raw"],
                       15.0, t0)
        t_banner = got[0] if got else None
        time.sleep(5.0)
        inp["resets"].append({"n": k, "t0": t0, "t_banner": t_banner,
                              "canstat": got[1].get("canstat") if got else None,
                              "config_mode": got[1].get("config_mode") if got else None})
        print("reset %d: banner after %s s, canstat %s"
              % (k, round(t_banner - t0, 3) if t_banner else None, inp["resets"][-1]["canstat"]))
    # the positive control: one closing pulse after the last window, which the witness must
    # count exactly once up to a second past its DONE (2026-11-13)
    inp["control"] = drive_closing(a, con, 1.0)
    inp["commands"] = [(inp["control"]["t_send"], inp["control"]["t_done"])]
    inp["t_end"] = time.time()
    return inp


def judge_boot_silence_run(a, inp, ts, clock):
    resets = []
    for r in inp["resets"]:
        t0, tb = r["t0"], r["t_banner"]
        end = (tb or t0) + 5.0
        resets.append({"n": r["n"], "banner_after_s": round(tb - t0, 3) if tb else None,
                       "canstat": r["canstat"], "config_mode": r["config_mode"],
                       "witness_err_frames": sum(1 for t in ts if t0 - 0.3 <= t <= end), "clock": clock})
    control = dict(closing_count(ts, inp["control"]), clock=clock)
    v, why = judge_boot_silence(resets, control)
    print("verdict %s %s | err frames per reset %s, closing pulse %s"
          % (v, why, [r["witness_err_frames"] for r in resets], control["witness_err_frames"]))
    return {"resets": resets, "control": control, "verdict": v, "why": why}


def sub_boot_silence(a, rec, con, wit):
    inp = drive_boot_silence(a, con)
    ts, clock = wit.times()
    rec.update(judge_boot_silence_run(a, inp, ts, clock))


def drive_pulses(a, con, t_open=None):
    samples, stop = [], threading.Event()
    th = None
    t_begin = time.time()
    if not a.no_wican:
        wican_mode(a.wican_mode)
        time.sleep(2.0)

        def poll():
            while not stop.is_set():
                s = wican_snap()
                samples.append(s if s else {"t": time.time(), "unanswered": True})
                time.sleep(0.1)
        th = threading.Thread(target=poll, daemon=True)
        th.start()
    time.sleep(2.0 if th is not None else 4.5)  # the judge's pre_s (4 s) observed quiet
    t0 = time.time()
    con.send("PULSES %d %d %d" % (a.w, a.n, a.interval_ms))
    ack = con.wait(lambda p: p["kind"] in ("ACK", "ERR"), 3.0, t0)
    done = con.wait(lambda p: p["kind"] == "DONE", a.n * a.interval_ms / 1000.0 + 5.0, t0)
    # From DONE to the next command (the closing STOP): watched post_s past DONE + the 0.3 s
    # margin, plus a second for the witness's delivery lag and 0.2 s to spare (2026-11-13).
    time.sleep(a.post_s + 0.3 + 1.0 + 0.2)
    stop.set()
    if th is not None:
        th.join(timeout=2.0)
        wican_mode(0)
    # the window after DONE ends at the closing pulse, which the witness must count (2026-11-13)
    closing = drive_closing(a, con, 0.0)
    return {"t_begin": t_begin, "t_send": t0, "ack": ack, "done": done, "t_end": time.time(),
            "obs_end": closing["t_send"] - 0.05, "closing": closing, "samples": samples,
            "wican_read": th is not None,
            "jammer_pulses": [p for t, _x, p in _console_lines(con)
                              if p and p["kind"] == "PULSE" and t0 <= t < closing["t_send"]],
            "commands": [(t0, done[0] if done else None), (closing["t_send"], closing["t_done"])]}


def judge_pulses_run(a, inp, ts, clock):
    t0, ack, done, obs_end = inp["t_send"], inp["ack"], inp["done"], inp["obs_end"]
    ok_ack = ack is not None and ack[1]["kind"] == "ACK"
    j = judge_pulses(ts, a.n, a.interval_ms / 1000.0, t0, ok_ack, done[0] if done else None,
                     obs_end, post_s=a.post_s)
    win = [t for t in ts if t0 - 0.3 <= t <= (done[0] if done else inp["t_end"]) + 1.0]
    pre = [t for t in ts if t0 - 4.0 <= t < t0 - 0.3]
    samples = inp["samples"]
    be = [(s["t"], s.get("bus_error"), s.get("req_start")) for s in samples if not s.get("unanswered")]
    out = {"ack": ack[1] if ack else None, "done": done[1] if done else None,
           "clock": clock, "witness_err_frames_pre_4s": len(pre),
           "per_pulse": per_pulse(win, a.n, a.interval_ms / 1000.0, be),
           "judge": j, "verdict": j["verdict"], "why": j["why"],
           "jammer_pulses": inp["jammer_pulses"],
           "wican_samples": samples if inp["wican_read"] else "not read: --no-wican",
           "err_ts_rel_t0": [round(t - t0, 4) for t in ts if t >= t0 - 4.0]}
    out["closing"] = closing_count(ts, inp["closing"])
    out["verdict"], out["why"] = fold_closing(out["verdict"], out["why"], out["closing"])
    pp = out["per_pulse"]
    print("verdict %s %s | commanded %d, groups %d, per pulse %s, bus_error deltas %s, pre %d, "
          "after DONE %s in %s s, closing pulse %s" % (
              out["verdict"], out["why"], a.n, pp["groups"], pp["per_pulse"], pp["bus_error_delta_per_pulse"],
              len(pre), j["after_done"]["n"], j["after_done"]["observed_s"], out["closing"]["witness_err_frames"]))
    return out


def sub_pulses(a, rec, con, wit):
    rec["wican_mode"] = a.wican_mode
    inp = drive_pulses(a, con)
    ts, clock = wit.times(timing=True)   # the PULSES judge's margins (TIMING_LAG_S)
    rec.update(judge_pulses_run(a, inp, ts, clock))


def _poller(samples, stop):
    while not stop.is_set():
        x = wican_snap()
        samples.append(x if x else {"t": time.time(), "unanswered": True})
        time.sleep(0.1)


def drive_timeout(a, con, t_open, open_witness=None):
    """t_open: when the witness opened (judge_stop's observation start). open_witness: the
    session's per-train open (2026-11-13): called at train_open_at(), it returns the started
    witness, whose t_open is then the observation start and the train judged with
    start_observed False; inp["witness"] holds it for the caller to judge and close."""
    samples, stop = [], threading.Event()
    th = None
    t_begin = time.time()
    if a.wican_mode:
        wican_mode(a.wican_mode)
        time.sleep(2.0)
        th = threading.Thread(target=_poller, args=(samples, stop), daemon=True)
        th.start()
    time.sleep(2.5)  # quiet observation before the start (judge pre_s = 2 s)
    t_send = time.time()
    con.send("TRAIN %d %d %d" % (a.w, a.p, a.ms))
    ack = con.wait(lambda p: p["kind"] in ("ACK", "ERR"), 3.0, t_send)
    capped = con.wait(lambda p: p["kind"] == "CAPPED", 0.5, t_send)
    if ack is None or ack[1]["kind"] != "ACK":
        stop.set()
        return {"t_begin": t_begin, "t_end": time.time(), "no_ack": ack[1] if ack else None,
                "commands": [(t_send, None)]}
    inp = {"t_begin": t_begin, "t_send": t_send}
    t_start = ack[0]
    eff_ms = ack[1].get("ms", a.ms)
    if a.host_quiet:
        con.paused.set()
        inp["host_quiet_from"] = round(time.time() - t_start, 3)
    t_deadline = t_start + (a.expect_ms or eff_ms) / 1000.0
    if open_witness is not None:
        inp["open_at"] = train_open_at(t_send, eff_ms)
        while time.time() < inp["open_at"]:
            time.sleep(min(0.01, max(0.0, inp["open_at"] - time.time())))
        t_call = time.time()
        wit = open_witness()
        inp.update(witness=wit, open_called=t_call, open_lag_s=round(wit.t_open - inp["open_at"], 4),
                   start_observed=False)
        t_open = wit.t_open
    # Watch past deadline + margin + quiet (judge_stop) with 2 s to spare
    # for the witness's delivery lag. The 01:24 run stopped ~0.5 s short.
    end = t_start + eff_ms / 1000.0 + 0.3 + 5.0 + 2.0
    while time.time() < end:
        time.sleep(0.1)
    con.paused.clear()
    stop.set()
    if th is not None:
        th.join(timeout=2.0)
        wican_mode(0)
    done = con.wait(lambda p: p["kind"] == "DONE", 3.0, t_start)
    # the silence after the stop (and after DONE) ends at the closing pulse (2026-11-13)
    closing = drive_closing(a, con, 0.5)
    inp.update({"ack": ack, "capped": capped, "t_start": t_start, "eff_ms": eff_ms,
                "t_deadline": t_deadline, "done": done, "t_end": time.time(),
                "obs_end": closing["t_send"] - 0.05, "closing": closing,
                "obs_start": t_open, "samples": samples,
                "commands": [(t_send, done[0] if done else None), (closing["t_send"], closing["t_done"])]})
    return inp


def judge_timeout_run(a, inp, ts, clock):
    rec = {}
    if "no_ack" in inp:
        rec["verdict"] = "INCONCLUSIVE"
        rec["why"] = "no ACK: %s" % (inp["no_ack"],)
        return rec
    if "host_quiet_from" in inp:
        rec["host_quiet_from"] = inp["host_quiet_from"]
    ack, capped, done, samples = inp["ack"], inp["capped"], inp["done"], inp["samples"]
    t_start, t_deadline, eff_ms, obs_end = inp["t_start"], inp["t_deadline"], inp["eff_ms"], inp["obs_end"]
    closing = closing_count(ts, inp["closing"])
    ts = [t for t in ts if t <= obs_end]     # the silence judged ends at the closing pulse
    seen_start = inp.get("start_observed", True)
    j = judge_stop(ts, t_start, t_deadline, inp["obs_start"], obs_end, start_observed=seen_start)
    be = [(x["t"], x.get("bus_error")) for x in samples if not x.get("unanswered")]
    rec.update({"ack": ack[1], "capped": capped[1] if capped else None,
                "effective_ms": eff_ms, "done": done[1] if done else None,
                "clock": clock, "judge": j,
                "err_ts_rel_start": [round(t - t_start, 4) for t in ts
                                     if t >= t_start - 2.5],
                "wican_samples": samples})
    if a.wican_mode:
        rec["judge_wican_bus_error"] = judge_counter_stop(be, t_start, t_deadline)
    if a.control_ms:
        c = judge_stop(ts, t_start, t_start + a.control_ms / 1000.0, inp["obs_start"],
                       obs_end, start_observed=seen_start)
        rec["negative_control"] = {"expect_ms": a.control_ms, "judge": c,
                                   "ok": c["verdict"] == "FAIL"}
        if a.wican_mode:
            cw = judge_counter_stop(be, t_start, t_start + a.control_ms / 1000.0)
            rec["negative_control"]["judge_wican_bus_error"] = cw
            rec["negative_control"]["ok"] = (rec["negative_control"]["ok"]
                                             and cw["verdict"] == "FAIL")
    mains = {"kvaser": j["verdict"]}
    controls = {}
    if a.wican_mode:
        mains["wican_bus_error"] = rec["judge_wican_bus_error"]["verdict"]
    if a.control_ms:
        controls["kvaser"] = rec["negative_control"]["judge"]["verdict"]
        if a.wican_mode:
            controls["wican_bus_error"] = (
                rec["negative_control"]["judge_wican_bus_error"]["verdict"])
        rec["negative_control"]["ok"] = all(
            controls[w] == "FAIL" for w, v in mains.items()
            if v == "PASS" and w in controls)
    rec["verdict"], rec["why"] = combine(mains, controls)
    # From DONE to the next command (2026-11-13), and the DONE line against the variant's
    # stop (stop (a) alone: stop_by=deadline_timer; stop (b) alone: end=deadline_layer_b).
    rec["after_done"] = after_done(ts, done[0] if done else None, obs_end)
    rec["done_check"] = judge_done(done[1] if done else None, a.expect_stop_by, a.expect_end)
    rec["verdict"], rec["why"] = fold_timeout(rec["verdict"], rec["why"], rec["after_done"],
                                              rec["done_check"])
    rec["closing"] = closing
    rec["verdict"], rec["why"] = fold_closing(rec["verdict"], rec["why"], closing)
    print("verdict %s | kvaser %s n_in %d n_after %d last_in %s | wican %s | control ok %s | "
          "after DONE %s | done check %s | %s"
          % (rec["verdict"], j["verdict"], j["n_in"], j["n_after"],
             j["last_in_rel_deadline_s"],
             rec.get("judge_wican_bus_error"),
             rec.get("negative_control", {}).get("ok"), rec["after_done"]["n"],
             rec["done_check"], (done[1]["raw"] if done else None)))
    return rec


def sub_timeout(a, rec, con, wit):
    rec["wican_mode"] = a.wican_mode
    inp = drive_timeout(a, con, wit.t_open)
    if "no_ack" in inp:
        rec.update(judge_timeout_run(a, inp, [], None))
        return
    ts, clock = wit.times(timing=True)   # judge_stop's 0.3 s margin (TIMING_LAG_S)
    rec.update(judge_timeout_run(a, inp, ts, clock))


def drive_noduration(a, con, t_open=None):
    t_begin = time.time()
    time.sleep(2.0)
    t0 = time.time()
    con.send("TRAIN %d %d" % (a.w, a.p))
    got = con.wait(lambda p: p["kind"] in ("ACK", "ERR"), 3.0, t0)
    time.sleep(3.0)
    # the quiet ends at the closing pulse (2026-11-13), the only command here that should put a
    # frame on the wire
    closing = drive_closing(a, con, 0.0)
    return {"t_begin": t_begin, "t_send": t0, "reply": got, "t_end": time.time(), "closing": closing,
            "commands": [(closing["t_send"], closing["t_done"])]}


def judge_noduration_run(a, inp, ts, clock):
    got = inp["reply"]
    n = sum(1 for t in ts if inp["t_send"] - 0.3 <= t < inp["closing"]["t_send"] - 0.05)
    out = {"reply": got[1] if got else None, "witness_err_frames": n, "clock": clock,
           "closing": closing_count(ts, inp["closing"])}
    out["verdict"] = ("PASS" if got and got[1]["kind"] == "ERR" and n == 0
                      else "FAIL")
    out["verdict"], out["why"] = fold_closing(out["verdict"], "", out["closing"])
    print("reply %s, err frames %d, closing pulse %s -> %s %s" % (
        got[1]["raw"] if got else None, n, out["closing"]["witness_err_frames"], out["verdict"], out["why"]))
    return out


def sub_noduration(a, rec, con, wit):
    inp = drive_noduration(a, con)
    ts, clock = wit.times()
    rec.update(judge_noduration_run(a, inp, ts, clock))


def sub_can2_check(a, rec, con, wit):
    # the pinned can-tools' Linux fixes (2026-11-11); close_gs_usb also frees the pyusb handle
    from kvaser_bus import gs_usb_bus, close_gs_usb
    try:
        import libusb_package
        import usb.backend.libusb1 as lb
        lb.get_backend(find_library=libusb_package.find_library)
    except Exception:
        pass
    inno = gs_usb_bus(channel="can0", index=0, bitrate=BITRATE)
    # Every Innomaker frame is logged with host and adapter time, so a frame
    # left in its buffer from before the run can be told from a live one: the
    # 01:28 run logged one 0x051 with nothing else to place it (2026-10-08).
    log = []           # (phase, epoch, adapter_ts, id, err, data)
    phase = ["drain"]
    stop = threading.Event()

    def rd():
        while not stop.is_set():
            m = inno.recv(timeout=0.05)
            if m is None:
                continue
            log.append((phase[0], round(time.time(), 4), m.timestamp,
                        "0x%03X" % m.arbitration_id, bool(m.is_error_frame),
                        bytes(m.data).hex()))
    th = threading.Thread(target=rd, daemon=True)
    th.start()
    try:
        time.sleep(2.0)  # drain: anything already buffered lands in phase "drain"
        phase[0] = "test"
        f0 = wit.frames
        ids0 = dict(wit.ids)
        wican_mode(2)
        time.sleep(3.0)
        t0 = time.time()
        con.send("PULSES %d %d %d" % (a.w, a.n, a.interval_ms))
        done = con.wait(lambda p: p["kind"] == "DONE",
                        a.n * a.interval_ms / 1000.0 + 5.0, t0)
        time.sleep(2.0)
        wican_mode(0)
        time.sleep(1.0)
    finally:
        stop.set()
        th.join(timeout=2.0)
        close_gs_usb(inno)
    ts, clock = wit.times()
    groups = clusters([t for t in ts if t >= t0 - 0.3], a.interval_ms / 2000.0)
    kv_pages = wit.frames - f0
    test = [x for x in log if x[0] == "test"]
    in_frames = sum(1 for x in test if not x[4])
    in_err = sum(1 for x in test if x[4])
    v, why = judge_can2(kv_pages, len(groups), a.n, in_frames, in_err)
    kv_ids = {k: wit.ids.get(k, 0) - ids0.get(k, 0) for k in wit.ids
              if wit.ids.get(k, 0) - ids0.get(k, 0)}
    rec.update({"done": done[1] if done else None, "clock": clock,
                "kvaser_frames_during": kv_pages, "kvaser_ids_during": kv_ids,
                "kvaser_err_groups": [len(g) for g in groups],
                "innomaker_log": log, "innomaker_mode": "ACKING (normal mode)",
                "verdict": v, "why": why})
    print("kvaser frames %d %s, err groups %s | innomaker drain %s | test %s -> %s %s"
          % (kv_pages, kv_ids, [len(g) for g in groups],
             [x[1:] for x in log if x[0] == "drain"], [x[1:] for x in test], v, why))


def judge_can2_isolation(sent, kv_seen, kv_groups, n_pulses, in_frames, in_err, probe_verdict,
                         canchg_alive):
    """CAN2 isolation without the WiCAN (2026-11-13, the gen-inhibit reviewer's option (i)
    for madhouse, which has no Wi-Fi). The positive control on CAN1: the Kvaser saw every one
    of the `sent` 0x7A0 frames the PCAN sent there, and one error-frame group per pulse. On
    CAN2, canchg (ACKing, normal mode) saw neither frames nor error frames, and its no-ACK
    probe (can2_ack_probe) came back NO_ACK: the MCP2515 neither transmits nor ACKs.
    INCONCLUSIVE when the positive control failed or the probe was UNCLEAR (or did not run);
    FAIL when canchg saw traffic or its frame was ACKed; PASS otherwise. As on 2026-10-08,
    whether gs_usb reports a bus error as an error frame is not shown (canchg has no
    BERR-REPORTING), so "0 error frames" there rests on the frame half and the probe.
    canchg_alive (2026-11-13, the reviewer's rule that every judged silence ends with a frame the
    same witness must count): the error frames canchg's reader heard after the test, for one
    frame canchg sent itself on the same socket. Nothing else on CAN2 can send (the MCP2515 is
    held in configuration mode), so canchg's own unACKed frame is the only thing it can be shown
    hearing. None or 0 makes a PASS INCONCLUSIVE: its reader may have been deaf through the test."""
    if sent < 1 or kv_seen != sent or kv_groups != n_pulses:
        return "INCONCLUSIVE", "positive control failed on the Kvaser (0x7A0 %s of %s, groups %s of %s)" % (
            kv_seen, sent, kv_groups, n_pulses)
    if in_frames or in_err:
        return "FAIL", "canchg saw traffic from CAN1 (%d frames, %d error frames)" % (in_frames, in_err)
    if not canchg_alive:
        return "INCONCLUSIVE", ("canchg's reader heard %s error frames for its own unACKed frame after the "
                                "test: it may not have been hearing" % canchg_alive)
    if probe_verdict == "ACKED":
        return "FAIL", "canchg's probe frame was ACKed on CAN2"
    if probe_verdict != "NO_ACK":
        return "INCONCLUSIVE", "the no-ACK probe was %s" % probe_verdict
    return "PASS", ""


PCAN_FRAMES = 50          # 0x7A0 frames the PCAN sends on CAN1 as the positive control
PCAN_GAP_S = 0.02


def can2_counts(wit_frames, n_pulses):
    """What the can2-isolation judge takes from the Kvaser's SocketCAN log (2026-11-13, ruling
    (C)): COUNTS only. wit_frames: SocketcanWitness.frames(("pcan", "pulses")), entries
    (phase, receipt, sw, can_id, data hex), each tagged with the phase the sub was in when the
    witness read it. kv_seen: the 0x7A0 data frames read in either phase. groups: the bus-error
    frames read in the pulses phase, grouped by the gaps between their software stamps
    (bench_witness.groups, GROUP_GAP_S fixed there). No window or margin is judged on a Kvaser
    stamp: which frames count is decided by phase, and a stamp is only ever compared with
    another stamp. A bus-error frame without a software stamp cannot be grouped, so `unstamped`
    makes the positive control fail."""
    import bench_witness as BW
    kv_seen = sum(1 for e in wit_frames if not e[3] & BW.CAN_ERR_FLAG
                  and (e[3] & BW.CAN_ERR_MASK) == 0x7A0)
    errs = [e for e in wit_frames if e[0] == "pulses" and e[3] & BW.CAN_ERR_FLAG and BW.is_bus_error(e[3])]
    unstamped = sum(1 for e in errs if e[2] is None)
    gs = BW.groups([e[2] for e in errs if e[2] is not None])
    other = [("%s 0x%08X %s" % (e[0], e[3], e[4])) for e in wit_frames
             if e[3] & BW.CAN_ERR_FLAG and not (e[0] == "pulses" and BW.is_bus_error(e[3]))]
    return {"kv_seen": kv_seen, "groups": [len(g) for g in gs], "unstamped": unstamped,
            "other_error_frames": other[:20], "n_other_error_frames": len(other),
            "n_groups": len(gs) if not unstamped else -1, "n_pulses": n_pulses}


def sub_can2_isolation(a, rec, con, wit):
    """CAN2 isolation on madhouse (judge_can2_isolation), witnessed by the Kvaser on SocketCAN
    (2026-11-13, ruling (C); `wit` is unused: no CANlib witness is opened for this sub). The
    Kvaser up through canctl (normal, no berr), its witness opened (bench_witness's named-netdev
    refusal), canchg up through canctl and listened to, while the PCAN sends PCAN_FRAMES 0x7A0 on
    CAN1 (the Kvaser ACKs them) and then while the jammer sends n pulses; canchg and the Kvaser
    down; then can2_ack_probe's no-ACK probe on canchg. The PCAN sends before the pulses, never
    during them, so it is not transmitting under the jammer; and it is never this process's
    witness, so the sub refuses while another process holds it (a session's witness)."""
    import can
    import can2_ack_probe as AP
    import bench_witness as BW
    rec["witness"] = WITNESS_KVASER_SC
    rec["canchg_link_at_start"] = AP.link("canchg")
    rec["kvaser_link_at_start"] = AP.link(BW.KVASER_IFACE)
    for name in ("canchg", "kvaser"):
        lk = rec["%s_link_at_start" % name]
        if not lk or lk.get("operstate") != "DOWN":
            rec["verdict"], rec["why"] = "INCONCLUSIVE", "%s not down at the start: %s" % (name, lk)
            return
    holders = BW.pcan_holders()
    if holders:
        rec["verdict"], rec["why"] = "NOT-RUN", ("the PCAN is open in pid %s (a session's witness?): it is "
                                                 "this check's 0x7A0 sender, never at once" % holders)
        return
    rec["canctl"] = [AP.canctl("up", BW.KVASER_IFACE, str(BITRATE))]
    log, phase, stop, canchg_failed = [], ["drain"], threading.Event(), []
    inno = kw = th = None
    t0, done, sent = None, None, 0
    try:
        if rec["canctl"][0]["rc"] != 0:
            rec["verdict"], rec["why"] = "INCONCLUSIVE", "canctl up kvaser failed"
            return
        rec["kvaser_link_up"] = AP.link(BW.KVASER_IFACE)
        try:
            kw = BW.SocketcanWitness(BW.KVASER_IFACE)
        except BW.WitnessRefused as e:
            rec["verdict"], rec["why"] = "NOT-RUN", "witness refused: %s" % e
            return
        rec["witness_describe"] = kw.describe()
        kw.phase = "drain"
        kw.start()
        rec["canctl"].append(AP.canctl("up", "canchg", str(BITRATE)))
        if rec["canctl"][-1]["rc"] != 0:
            rec["verdict"], rec["why"] = "INCONCLUSIVE", "canctl up canchg failed"
            return
        inno = can.Bus(interface="socketcan", channel="canchg")

        def rd():
            try:
                while not stop.is_set():
                    m = inno.recv(timeout=0.05)
                    if m is not None:
                        log.append((phase[0], round(time.time(), 4), "0x%03X" % m.arbitration_id,
                                    bool(m.is_error_frame), bytes(m.data).hex()))
            except Exception as e:      # noqa: BLE001  canchg is a witness too: a failed read is WitnessDown
                canchg_failed.append("canchg read failed at %.3f: %s: %s" % (time.time(), type(e).__name__, e))
        th = threading.Thread(target=rd, daemon=True)
        th.start()
        time.sleep(2.0)
        phase[0] = "test"
        kw.phase = "pcan"
        pcan = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE)
        try:
            for k in range(PCAN_FRAMES):
                pcan.send(can.Message(arbitration_id=0x7A0, is_extended_id=False,
                                      data=bytes([0x7A, k & 0xFF, 0, 0, 0, 0, 0, 0])))
                sent += 1
                time.sleep(PCAN_GAP_S)
        finally:
            pcan.shutdown()
        time.sleep(1.0)
        kw.phase = "pulses"
        t0 = time.time()
        con.send("PULSES %d %d %d" % (a.w, a.n, a.interval_ms))
        done = con.wait(lambda p: p["kind"] == "DONE", a.n * a.interval_ms / 1000.0 + 5.0, t0)
        time.sleep(2.0)
        kw.phase = "end"
        # canchg's liveness (2026-11-13): one frame of its own on the same socket, which nothing
        # on CAN2 ACKs, so its reader must hear the ACK errors; canctl down then ends the retries
        phase[0] = "liveness"
        inno.send(can.Message(arbitration_id=AP.PROBE_ID, is_extended_id=False,
                              data=bytes([0xA2, 0, 0, 0, 0, 0, 0, 0])))
        time.sleep(1.0)
        phase[0] = "end"
    finally:
        stop.set()
        if inno is not None:
            if th is not None:
                th.join(timeout=1.0)
            inno.shutdown()
            rec["canctl"].append(AP.canctl("down", "canchg"))
        if kw is not None:
            rec["witness_down_at_end"] = kw.down()
            kw.close()
        if len(rec.get("canctl", [])) and rec["canctl"][0]["rc"] == 0:
            rec["canctl"].append(AP.canctl("down", BW.KVASER_IFACE))
            rec["kvaser_link_at_end"] = AP.link(BW.KVASER_IFACE)
    if rec.get("witness_down_at_end"):
        raise WitnessDown(rec["witness_down_at_end"])
    if canchg_failed:
        rec["canchg_down"] = canchg_failed[0]
        raise WitnessDown(canchg_failed[0])
    cnt = can2_counts(kw.frames(("pcan", "pulses")), a.n)
    test = [x for x in log if x[0] == "test"]
    in_frames = sum(1 for x in test if not x[3])
    in_err = sum(1 for x in test if x[3])
    alive = sum(1 for x in log if x[0] == "liveness" and x[3])
    rec["canchg_alive_error_frames"] = alive
    rec["probe"] = AP.probe("canchg")
    pv = "NOT RUN" if "refused" in rec["probe"] else rec["probe"]["judge"]["verdict"]
    v, why = judge_can2_isolation(sent, cnt["kv_seen"], cnt["n_groups"], a.n, in_frames, in_err, pv, alive)
    rec.update({"done": done[1] if done else None, "pcan_sent_0x7A0": sent,
                "kvaser_counts": cnt, "kvaser_0x7A0_seen": cnt["kv_seen"],
                "kvaser_err_groups": cnt["groups"], "kvaser_log": kw.frames(("drain", "pcan", "pulses", "end")),
                "canchg_log": log, "canchg_mode": "socketcan, normal (ACKing), no berr",
                "verdict": v, "why": why})
    print("pcan sent %d, kvaser saw %d, err groups %s (unstamped %d, other err %d) | canchg drain %s test %s "
          "| probe %s -> %s %s" % (sent, cnt["kv_seen"], cnt["groups"], cnt["unstamped"],
                                   cnt["n_other_error_frames"], [x[1:] for x in log if x[0] == "drain"],
                                   [x[1:] for x in test], pv, v, why))


SUBS = {"id": sub_id, "boot-silence": sub_boot_silence, "pulses": sub_pulses,
        "timeout": sub_timeout, "noduration": sub_noduration,
        "can2-check": sub_can2_check, "can2-isolation": sub_can2_isolation}


# ------------------------------------------------------------- the PCAN session (ruling (C))

WITNESS_PCAN = ("pcan: PCAN-USB FD on PCAN_USBBUS1, normal mode, device stamps placed by its open's "
                "anchor (witness_anchor)")
# A train's open (the gen-inhibit reviewer, 2026-11-13): LEAD_S before its expected stop, at most
# LEAD_MAX_S, and never more than LEAD_PULSES_MAX pulses inside it (the PCAN reported warning at
# +0.46 s and passive at +0.62 s of a 5 ms train in session 1, about 96 and 128 pulses).
LEAD_S = 0.4
LEAD_MAX_S = 0.4
LEAD_PULSES_MAX = 90
SHARED_SETTLE_S = 1.0       # a shared open is read this long before its first check


def lead_why(ns):
    """Why a timeout check's train cannot be judged from a fresh open LEAD_S before its stop, or
    None."""
    if LEAD_S > LEAD_MAX_S:
        return "the train's open lead %g s is over the bound %g s" % (LEAD_S, LEAD_MAX_S)
    if ns.p <= 0 or LEAD_S * 1e6 / ns.p > LEAD_PULSES_MAX:
        return ("--p %d puts %.0f pulses in the %g s lead before the stop, over %d: the PCAN would go "
                "error passive inside it" % (ns.p, LEAD_S * 1e6 / max(ns.p, 1), LEAD_S, LEAD_PULSES_MAX))
    return None


def train_open_at(t_send, eff_ms):
    """When a train's witness opens: LEAD_S before its expected stop, the effective ms counted
    from the host's send of TRAIN; at the send when the train is shorter than the lead."""
    return max(t_send, t_send + eff_ms / 1000.0 - LEAD_S)
WITNESS_KVASER_SC = ("kvaser: kvaser_usb on SocketCAN (netdev kvaser), normal mode, software stamps, "
                     "counts only, berr off")
WITNESS_KVASER_CANLIB = "kvaser: CANlib (kvaser_bus), hardware stamps on the epoch clock"
WITNESS_NONE = "none: id needs no witness"
SESSION_SUBS = ("boot-silence", "pulses", "timeout", "noduration")
DRIVE = {"boot-silence": drive_boot_silence, "pulses": drive_pulses, "timeout": drive_timeout,
         "noduration": drive_noduration}
JUDGE = {"boot-silence": judge_boot_silence_run, "pulses": judge_pulses_run, "timeout": judge_timeout_run,
         "noduration": judge_noduration_run}
RESOURCE_SETTLE_S = 6.0     # resource-log samples every 5 s: wait for the row after a window
COUNTS = ("only with a clean resource-log record over its window (ruling (C), P1 (5); AGENTS.md "
          "'Parallel work', point 2): see resource_log")


def parse_checks(ap, specs, no_wican):
    """The session's --check strings -> [(spec, Namespace)], each parsed by the tool's own parser
    and refused like a command line (args_why). Raises ValueError with the reason."""
    import shlex
    out = []
    for s in specs:
        words = shlex.split(s)
        if words and words[0] == "can2-isolation":
            raise ValueError("check %r: can2-isolation needs the PCAN as its 0x7A0 sender, so it never "
                             "runs in the PCAN's session (ruling (C)): run it on its own" % s)
        if not words or words[0] not in SESSION_SUBS:
            raise ValueError("check %r: a session runs only %s" % (s, ", ".join(SESSION_SUBS)))
        if no_wican and "--no-wican" not in words:
            words.append("--no-wican")
        try:
            ns = ap.parse_args(words)
        except SystemExit:
            raise ValueError("check %r: not arguments of %s" % (s, words[0]))
        if ns.check:
            raise ValueError("check %r: no --check inside a check" % s)
        why = args_why(ns) or (lead_why(ns) if ns.sub == "timeout" else None)
        if why:
            raise ValueError("check %r: %s" % (s, why))
        out.append((s, ns))
    if not out:
        raise ValueError("a session needs at least one --check")
    return out


def resource_window(t0, t1, run=None):
    """`resource-log window` over one check's span (P1 (5)), kept raw with the logger's longest
    gap. The harness can flag only the rule AGENTS.md fixes (a gap over 10 s: the logger itself
    starved, UNCLEAR); gen-inhibit's marks for the slices are not yet set with the user's
    approval, so the rest of the triage is read by hand in the run README."""
    import math
    import subprocess
    run = run or subprocess.run
    out = {"window": [round(t0, 3), round(t1, 3)]}
    try:
        p = run(["resource-log", "window", "%d" % math.floor(t0), "%d" % math.ceil(t1)],
                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        out.update(error="%s: %s" % (type(e).__name__, e),
                   triage="UNCLEAR: no resource-log record over the window")
        return out
    text = p.stdout or ""
    m = re.search(r"longest gap ([\d.]+) s", text)
    gap = float(m.group(1)) if m else None
    out.update(rc=p.returncode, text=text[-4000:], longest_gap_s=gap,
               bench_row=next((ln for ln in text.splitlines() if ln.startswith("bench ")), None))
    if p.returncode != 0 or gap is None:
        out["triage"] = "UNCLEAR: no resource-log record over the window"
    elif gap > 10.0:
        out["triage"] = "UNCLEAR: the logger's own gap %.1f s is over 10 s (it was starved or down)" % gap
    else:
        out["triage"] = ("by hand: the record over the window is read in the run README (gen-inhibit's "
                         "slice marks are not yet set with the user's approval)")
    return out


# The USB record (the gen-inhibit reviewer, 2026-11-13, after CAN replay's degraded-USB
# advisory): the bench's hubs are bus-powered, and the GL850G at 1-3.2 holds the Kvaser, the
# PCAN, canmain and the WiCAN console. The kernel log over the run's whole window is read, and
# any EPROTO, -71, reset or disconnect line naming a 1-3.2.x device VOIDs every check whose
# window holds it, with the line kept in the record. A kernel log that cannot be read VOIDs too:
# nothing then shows the hub was clean.
USB_DEV_RE = re.compile(r"(?<![\d.])1-3\.2(?:\.\d+)*(?!\d)")
USB_BAD_RE = re.compile(r"EPROTO|-71\b|\breset\b|disconnect", re.I)


def usb_record(t0, t1, run=None):
    """`journalctl -k` over [t0, t1] (epochs): the 1-3.2.x lines that VOID, as (epoch, line)."""
    import math
    import subprocess
    run = run or subprocess.run
    out = {"window": [round(t0, 3), round(t1, 3)], "hub": "1-3.2 (GL850G, bus-powered)"}
    try:
        p = run(["journalctl", "-k", "--since", "@%d" % math.floor(t0), "--until", "@%d" % math.ceil(t1),
                 "-o", "short-unix", "--no-pager"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    out["rc"] = p.returncode
    if p.returncode != 0:
        out["error"] = "journalctl exit %d: %s" % (p.returncode, (p.stderr or "").strip()[-300:])
        return out
    hits = []
    for ln in (p.stdout or "").splitlines():
        head = ln.split(" ", 1)[0]
        try:
            t = float(head)
        except ValueError:
            continue
        if USB_DEV_RE.search(ln) and USB_BAD_RE.search(ln):
            hits.append((t, ln.strip()[:300]))
    out["hits"] = hits
    return out


def usb_fold(rec, record, lo, hi):
    """VOID rec (a check's record) if the USB record holds a line in [lo, hi], or could not be
    read; a NOT-RUN or a REFUSED stays. The lines go in rec["usb"]."""
    if "error" in record:
        lines = ["kernel log not read: %s" % record["error"]]
    else:
        lines = [ln for t, ln in record.get("hits", []) if lo <= t <= hi]
    rec["usb"] = {"window": [round(lo, 3), round(hi, 3)], "lines": lines}
    v = str(rec.get("verdict"))
    if lines and v != "NOT-RUN" and not v.startswith("REFUSED"):
        rec["verdict_before_usb"] = rec.get("verdict")
        rec["verdict"] = "VOID: USB on the 1-3.2 hub: %d kernel line(s) in the check's window" % len(lines)
        rec["why"] = lines[0]
    return rec


def fold_bracket(c, br):
    """The brackets into a judged check (P1 (2), widened by the gen-inhibit reviewer,
    2026-11-13). Every command in `commands` puts frames on the wire (a PULSES, a TRAIN, a
    closing pulse), so a command with a DONE and no frame at all means the witness missed it.
    That, or a frame outside its bracket, VOIDs the check, but a FAIL stays a FAIL with the
    breach kept in the record: a real frame after DONE (close_channel letting TXD float) is a
    finding, not a clock fault. (A FAIL beside an empty command stays too: the frames that FAILed
    it were heard; the tester's reading, put to the reviewer.)"""
    out = sum(b["n_outside"] for b in br)
    empty = [b["t_send"] for b in br if b["t_done"] is not None and b["n"] == 0]
    if not out and not empty:
        return c
    why = []
    if out:
        why.append("%d frames outside their command's bracket (host send < mapped <= min(own receipt, "
                   "the DONE's receipt))" % out)
    if empty:
        why.append("%d command(s) with a DONE and no frame (sent at %s)" % (len(empty), empty))
    c["bracket_breach"] = "; ".join(why)
    if c.get("verdict") != "FAIL":
        c["verdict_before_bracket"] = c.get("verdict")
        c["verdict"] = "VOID: bracket: %s" % c["bracket_breach"]
        c["why"] = "P1 (2), the gen-inhibit reviewer's ruling (C), widened 2026-11-13"
    return c


def judge_session(checks, points, anc, clock, open_id=0):
    """Every driven check of one open judged on that open's anchor (P1 (1)-(3), one anchor per
    open since 2026-11-13). checks: dicts with "sub", "ns" (its Namespace) and "inp" (its drive's
    result), or already a "verdict" (NOT-RUN). The anchor VOID makes every check VOID; otherwise
    the check is judged on the open's frames mapped into its span, and then its brackets folded
    in (fold_bracket).
    -> the frames mapped into no check's span (anything there is in the record for the reader)."""
    spans = []
    for c in checks:
        if "inp" not in c:
            continue
        inp = c["inp"]
        spans.append((inp["t_begin"], inp["t_end"]))
        if anc["void"]:
            c["verdict"] = "VOID: anchor of open %s: %s" % (open_id, anc["void"])
            c["why"] = "witness_anchor (P1, gen-inhibit reviewer's ruling (C), 2026-11-13)"
            continue
        cmds = inp["commands"]
        br = [WA.bracket(anc, points, t_send, t_done, cmds[i + 1][0] if i + 1 < len(cmds) else inp["t_end"])
              for i, (t_send, t_done) in enumerate(cmds)]
        c["bracket"] = br
        ts = [WA.mapped(anc, d) for d, _r in points if inp["t_begin"] <= WA.mapped(anc, d) <= inp["t_end"]]
        c.update(JUDGE[c["sub"]](c["ns"], inp, ts, clock))
        fold_bracket(c, br)
    if anc["offset_s"] is None:
        return []
    return [round(WA.mapped(anc, d), 4) for d, _r in points
            if not any(lo <= WA.mapped(anc, d) <= hi for lo, hi in spans)]


def _close_open(o):
    """Close one open, keeping why it was down (if it was) and when, before the close."""
    w = o["wit"]
    o["down"] = w.down()
    o["t_fail"] = getattr(w, "t_fail", None) if o["down"] else None
    w.close()
    o["t_close"] = time.time()


def run_session(checks, con, open_witness, settle=time.sleep, resource=resource_window, usb=usb_record):
    """Drive each (spec, Namespace) in order on one console, each on a PCAN open (module doc,
    PER-OPEN ANCHORS), then judge every check on its own open's anchor. open_witness(): opens and
    starts a bench_witness.PcanWitness (or a fake with t_open, down(), t_fail, close(), points(),
    opens, same_device()). -> (check records, opens, frames outside the checks); each open as
    {"id", "kind" (shared | train), "wit", "checks", "anchor", "down", "t_fail", ...}."""
    out, opens = [], []
    cur = None

    def new_open(kind):
        o = {"id": len(opens), "kind": kind, "wit": open_witness(), "checks": []}
        o["t_open"] = o["wit"].t_open
        opens.append(o)
        return o

    for spec, ns in checks:
        c = {"spec": spec, "sub": ns.sub, "ns": ns, "witness": WITNESS_PCAN}
        out.append(c)
        if ns.sub == "timeout":
            if cur is not None:
                _close_open(cur)
                cur = None
            got = []
            print("== check: %s (its own open, %g s before its stop)" % (spec, LEAD_S))
            try:
                c["inp"] = DRIVE[ns.sub](ns, con, None, open_witness=lambda: got.append(new_open("train"))
                                         or got[-1]["wit"])
            except Exception as e:     # noqa: BLE001  the open refused or failed: nothing judged
                c["verdict"], c["why"] = "NOT-RUN", "witness not opened: %s: %s" % (type(e).__name__, e)
                c.pop("inp", None)
            if got:
                got[0]["checks"].append(c)
                c["open"] = got[0]["id"]
                c.get("inp", {}).pop("witness", None)
                _close_open(got[0])
            continue
        if cur is None:
            try:
                cur = new_open("shared")
            except Exception as e:     # noqa: BLE001
                c["verdict"], c["why"] = "NOT-RUN", "witness not opened: %s: %s" % (type(e).__name__, e)
                continue
            settle(SHARED_SETTLE_S)
        why = cur["wit"].down()
        if why:
            c["verdict"], c["why"] = "NOT-RUN", "witness down: %s" % why
            continue
        print("== check: %s (open %d)" % (spec, cur["id"]))
        c["inp"] = DRIVE[ns.sub](ns, con, cur["wit"].t_open)
        cur["checks"].append(c)
        c["open"] = cur["id"]
    if cur is not None:
        _close_open(cur)
    outside = []
    for o in opens:
        w = o["wit"]
        pts = w.points()
        o["anchor"] = anc = WA.anchor(pts, opens=w.opens, same_device=w.same_device())
        if o["down"]:
            # down from t_fail (None: no time known, so from the open): every check of this open
            # not over by then is NOT-RUN
            for c in o["checks"]:
                if "inp" in c and (o["t_fail"] is None or c["inp"]["t_end"] >= o["t_fail"]):
                    c["verdict"], c["why"] = "NOT-RUN", "witness down (open %d): %s" % (o["id"], o["down"])
                    # never judged, but its drive stays in the record (a train's send, ACKed ms,
                    # open_at and open lag: the run README states them, 2026-11-13)
                    c["inp_unjudged"] = c.pop("inp")
        clock = ("pcan_anchor of open %d (offset %s s, slope %s ppm, spread p99 %s s)"
                 % (o["id"], anc.get("offset_s"), anc.get("slope_ppm"), anc.get("spread_p99_s")))
        for c in o["checks"]:
            c["anchor"] = "open %d" % o["id"]
        outside += judge_session(o["checks"], pts, anc, clock, o["id"])
    for c in out:
        if "inp" in c and "open" not in c:
            # a train that never opened its witness (no ACK, so no stop to watch)
            c.update(JUDGE[c["sub"]](c["ns"], c["inp"], [], None))
    def drv(c):
        return c.get("inp") or c.get("inp_unjudged")

    driven = [c for c in out if drv(c)]
    if driven:
        settle(max(0.0, drv(driven[-1])["t_end"] + RESOURCE_SETTLE_S - time.time()))
        rec_usb = usb(drv(driven[0])["t_begin"], drv(driven[-1])["t_end"])
    for c in driven:
        c["resource_log"] = resource(drv(c)["t_begin"], drv(c)["t_end"])
        c["counts"] = COUNTS
        usb_fold(c, rec_usb, drv(c)["t_begin"], drv(c)["t_end"])
    for c in out:
        c["args"] = vars(c.pop("ns"))
        if "inp" in c:
            c["drive"] = c.pop("inp")
        elif "inp_unjudged" in c:
            c["drive"] = c.pop("inp_unjudged")
            c["drive_judged"] = False
    for o in opens:
        o["checks"] = [c["spec"] for c in o["checks"]]
    return out, opens, outside


def main_session(a, ap, dev):
    """`session`: the PCAN as the timing witness for a list of checks (ruling (C))."""
    try:
        checks = parse_checks(ap, a.check, a.no_wican)
    except ValueError as e:
        print("REFUSED: %s (nothing run, no record)" % e)
        return 2
    import bench_witness as BW
    rec = {"when": time.strftime("%Y%m%dT%H%M%S"), "sub": "session", "args": vars(a),
           "port": dev["port"], "serial_device": dev, "witness": WITNESS_PCAN,
           "anchor_method": WA.METHOD, "wican_before": NO_WICAN if a.no_wican else wican_snap()}
    con = Console(dev["port"])
    con.start()
    made = []

    def open_witness():
        # WitnessRefused, BenchNotHeld or a failed open raise here; run_session makes the check
        # NOT-RUN with the reason
        w = BW.PcanWitness()
        made.append(w)
        w.start()
        return w

    opens = []
    try:
        try:
            rec["checks"], opens, rec["frames_outside_checks"] = run_session(checks, con, open_witness)
        finally:
            for w in made:
                if not w.stop_flag.is_set():
                    w.close()
            rec["opens"] = []
            for k, w in enumerate(made):
                o = dict(opens[k]) if k < len(opens) else {"id": k}
                o.pop("wit", None)
                o.update(witness_describe=w.describe(), witness_frames=w.frames, witness_ids=w.ids,
                         witness_err_total=len(w.err),
                         witness_other_err=[(round(d, 6), round(r, 4), x) for d, r, x in w.other_err][:200],
                         witness_points=[(round(d, 6), round(r, 6)) for d, r in w.points()])
                rec["opens"].append(o)
        rec["verdicts"] = [(c["spec"], c.get("verdict")) for c in rec["checks"]]
        for spec, v in rec["verdicts"]:
            print("%-60s %s" % (spec, v))
        for o in rec["opens"]:
            an = o.get("anchor") or {}
            print("open %s (%s, checks %s, down %s): anchor n %s, offset %s s, slope %s ppm, drift %s s, "
                  "spread p50/p99/max %s/%s/%s s, void %s"
                  % (o["id"], o.get("kind"), o.get("checks"), o.get("down"), an.get("n"), an.get("offset_s"),
                     an.get("slope_ppm"), an.get("drift_s"), an.get("spread_p50_s"), an.get("spread_p99_s"),
                     an.get("spread_max_s"), an.get("void")))
    finally:
        try:
            if con.ser is not None:
                con.send("STOP")
                time.sleep(0.3)
        except Exception:
            pass
        rec["console"] = _console_dump(con)
        rec["console_events"] = [(round(t, 4), e) for t, e in con.events]
        con.stop_flag.set()
        con.join(timeout=2.0)
        rec["wican_after"] = NO_WICAN if a.no_wican else wican_snap()
        write_rec("session", rec)
    return 0


NO_WICAN = "not read: --no-wican"
WITNESSLESS = ("id", "can2-isolation")   # main() opens no CANlib witness for these
NEEDS_WICAN = ("can2-check",)       # its positive control is the WiCAN's pages (mode 2)
NO_WICAN_ONLY = ("can2-isolation",)  # the WiCAN-free form, for madhouse


def args_why(a):
    """Why these arguments are refused before anything opens, or None (2026-11-13).
    --no-wican: no HTTP to the WiCAN anywhere, so not with --wican-mode or can2-check;
    can2-isolation only with it. --expect-name/--expect-elf go with id, together;
    --expect-stop-by/--expect-end with timeout."""
    if a.no_wican and a.wican_mode:
        return "--wican-mode needs the WiCAN, and --no-wican never reaches it"
    if a.no_wican and a.sub in NEEDS_WICAN:
        return "%s needs the WiCAN's pages; with --no-wican use can2-isolation" % a.sub
    if a.sub in NO_WICAN_ONLY and not a.no_wican:
        return "%s is the form without the WiCAN: give --no-wican" % a.sub
    if (a.expect_name or a.expect_elf) and a.sub != "id":
        return "--expect-name/--expect-elf go only with id"
    if a.sub == "id" and bool(a.expect_name) != bool(a.expect_elf):
        return "id needs both --expect-name and --expect-elf, or neither"
    if (a.expect_stop_by or a.expect_end) and a.sub != "timeout":
        return "--expect-stop-by/--expect-end go only with timeout"
    # 2026-11-13, ruling (C)
    if a.sub == "session" and not a.check:
        return "session needs at least one --check"
    if a.check and a.sub != "session":
        return "--check goes only with session"
    if a.sub == "can2-isolation" and a.interval_ms < MIN_INTERVAL_MS:
        return ("can2-isolation groups by a fixed %g s gap, so --interval-ms must be at least %d"
                % (GROUP_GAP_S, MIN_INTERVAL_MS))
    return None


def run_guarded(sub, a, rec, con, wit):
    """Run one subcommand. If the witness is on receipt time when a judgement asks for
    its times (Witness.times() raises WC.ReceiptClock), the run is VOID, with the note."""
    try:
        sub(a, rec, con, wit)
    except WC.ReceiptClock as e:
        WC.void(rec, e.note)
        print(rec["verdict"])
    except WitnessDown as e:
        rec["verdict"], rec["why"] = "NOT-RUN", "witness down: %s" % e
        print(rec["verdict"], rec["why"])
    except LagVoid as e:
        rec["verdict"], rec["why"] = "VOID: %s" % e, "timing lag bound (gen-inhibit reviewer, 2026-11-13)"
        rec["timing_lag_void"] = e.lag
        print(rec["verdict"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", choices=sorted(list(SUBS) + ["session"]))
    ap.add_argument("--resets", type=int, default=3)
    ap.add_argument("--w", type=int, default=20)
    ap.add_argument("--p", type=int, default=200)
    ap.add_argument("--ms", type=int, default=2000)
    ap.add_argument("--expect-ms", type=int, default=0,
                    help="deadline to judge against (default: the ACKed ms)")
    ap.add_argument("--control-ms", type=int, default=0,
                    help="negative control: judge again against this; must FAIL")
    ap.add_argument("--host-quiet", action="store_true")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--interval-ms", type=int, default=1000)
    ap.add_argument("--wican-mode", type=int, default=0)
    ap.add_argument("--no-wican", action="store_true",
                    help="never reach the WiCAN over HTTP (madhouse has no Wi-Fi, 2026-11-13)")
    ap.add_argument("--expect-name", default=None, help="id: the row's project_name")
    ap.add_argument("--expect-elf", default=None, help="id: the row's app_elf_sha256")
    ap.add_argument("--expect-stop-by", default=None, help="timeout: DONE's stop_by= must be this")
    ap.add_argument("--expect-end", default=None, help="timeout: DONE's end= must be this")
    ap.add_argument("--post-s", type=float, default=3.0,
                    help="pulses: watch this long after DONE (error frames there FAIL)")
    ap.add_argument("--check", action="append", default=[],
                    help="session: one check, as its own arguments (e.g. \"pulses --n 1\"); repeat")
    a = ap.parse_args()
    why = args_why(a)
    if why:
        print("REFUSED: %s (nothing run, no record)" % why)
        return 2
    sys.path.insert(0, HERE)
    # the X2's port by its USB IDs (DEVICES-SPEC S1; gen-inhibit reviewer, 2026-11-12): no
    # device, or not exactly one, refuses before anything opens (exit 2, no record)
    import gi_serial as GS
    try:
        dev = GS.resolve("x2")
    except GS.Refused as e:
        print("REFUSED: %s (nothing run, no record)" % e)
        return 2
    if a.sub == "session":
        return main_session(a, ap, dev)
    rec = {"when": time.strftime("%Y%m%dT%H%M%S"), "sub": a.sub, "args": vars(a),
           "port": dev["port"], "serial_device": dev,
           "wican_before": NO_WICAN if a.no_wican else wican_snap()}
    con = Console(dev["port"])
    con.start()
    wit = None
    try:
        # id needs no witness; can2-isolation opens its own (the Kvaser on SocketCAN); the rest
        # keep the Kvaser through CANlib (2026-11-13, ruling (C))
        rec["witness"] = {"id": WITNESS_NONE, "can2-isolation": WITNESS_KVASER_SC}.get(
            a.sub, WITNESS_KVASER_CANLIB)
        t_run0 = time.time()
        if a.sub not in WITNESSLESS:
            wit = Witness()
            wit.start()
        time.sleep(1.0)
        run_guarded(SUBS[a.sub], a, rec, con, wit)
        # the USB record over the run (2026-11-13): a 1-3.2.x error line in it VOIDs the check
        time.sleep(1.0)
        usb_fold(rec, usb_record(t_run0, time.time()), t_run0, time.time())
        if "usb" in rec and rec["usb"]["lines"]:
            print(rec["verdict"], "|", rec["why"])
    finally:
        try:
            if con.ser is not None:
                con.send("STOP")
                time.sleep(0.3)
        except Exception:
            pass
        rec["console"] = _console_dump(con)
        rec["console_events"] = [(round(t, 4), e) for t, e in con.events]
        con.stop_flag.set()
        con.join(timeout=2.0)
        if wit is not None:
            rec["witness_frames"] = wit.frames
            rec["witness_err_total"] = len(wit.err)
            rec["witness_down_at_end"] = wit.down()
            wit.close()
        rec["wican_after"] = NO_WICAN if a.no_wican else wican_snap()
        write_rec(a.sub, rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
