"""Verify the WiCAN diagnostic frames decode against the diag DBC.

The DBC is read from git by gi_diag.py (toolkit item 10, 2026-10-24): HEAD's, refused
if the working tree's differs, and every page is decoded in the order received, on the
schema its latest schema page states, which must be HEAD's (the record keeps the run
identity). A page on another schema is refused, never decoded.
"""
import os
import sys, time

# Reviews A1 and B3 added 0x440, 0x411, 0x617 and 0x639 to the arm gate, on
# top of spec 7.1's 0x592. The whole set lives in one place so the next
# addition does not have to be remembered five times.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_interlocks import send_interlocks, prime_interlocks   # noqa: E402

# The pass/fail rules live in their own module so they can be exercised with
# no bus -- see diag_check_rules.py for why, and for the TypeError that made
# it necessary. diag_check_rules_test.py runs THESE rules, not a copy.
from diag_check_rules import build_checks                        # noqa: E402

import can, urllib.request, json
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)


# --- dongle backlog ------------------------------------------------------
#
# Reported by the interposer session and measured on BOTH dongles
# (2026-09-22): after the bus has been idle, the first second of a freshly
# opened handle delivers a backlog -- 37 frames against a true rate of 4/s --
# and it is then exact. Counting from the instant of the open therefore
# inflates a witness count, which reads as a better pass rather than a
# failure. A non-blocking drain at t=0 does not fix it: the backlog arrives
# over about a second, so there is nothing to drain yet.
#
# Sleep, then drain. Two seconds costs nothing next to a bench run.
SETTLE_S = 2.0


def settle(*buses, seconds=SETTLE_S):
    """Let any backlog arrive, then throw it away. Returns what was dropped."""
    time.sleep(seconds)
    dropped = 0
    for b in buses:
        while b.recv(timeout=0.0) is not None:
            dropped += 1
    return dropped


SCHEMAS = gi_diag.SchemaSet.from_git()
IDENT = gi_diag.run_identity(schemas=SCHEMAS)
db = SCHEMAS.head().db

#
# THE PAGE SET COMES FROM THE DBC, NOT FROM A LITERAL (2026-10-04, reviewing
# session). This read
#
#     DIAG_IDS = {0x7F1: "Status", 0x7F2: "Counters", 0x7F3: "Build"}
#
# and so never captured, decoded or asserted on 0x7F8 (Status2) or 0x7F9
# (Skips), both of which the device has emitted since schema 4/5. The script
# reported PASS on three of five pages with nothing saying two were absent --
# and the two it skipped are the ones carrying the abort reason and the skip
# counters, i.e. exactly what spec 10 added them for.
#
# A literal list cannot notice a page it does not know about, so the schema
# bump that added them could not have been caught here. Deriving the set from
# the DBC means the NEXT page is required the moment the DBC describes it:
# this check now goes red on a schema bump rather than quietly narrowing.
#
# THE VACUITY GUARD IS THE POINT OF THE SECOND HALF. "Require every page in
# the DBC" is satisfied trivially by a DBC with no messages, or one that
# failed to parse the way expected -- the requirement would be empty and the
# run would pass having checked nothing. That is the filter-returns-empty
# failure from AGENTS.md, and here it would read as a clean bench. So the
# three pages that have existed since schema 1 are also required by id.
#
DIAG_IDS = {m.frame_id: m.name.replace("WICAN_Diag_", "")
            for m in db.messages}
CORE_PAGES = (0x7F1, 0x7F2, 0x7F3)
_missing_core = [p for p in CORE_PAGES if p not in DIAG_IDS]
if _missing_core:
    sys.exit("DBC does not define %s -- refusing to run. Deriving the page "
             "set from a DBC that is missing the pages this check was built "
             "around would make every page requirement vacuous and the run "
             "would pass having verified nothing."
             % ", ".join("0x%X" % p for p in _missing_core))
print("diag pages required (from the DBC): %s"
      % ", ".join("0x%X %s" % (i, DIAG_IDS[i]) for i in sorted(DIAG_IDS)))

# The schema HEAD's DBC states (its DiagSchemaVersion; until 2026-10-24 parsed from
# its VERSION line). The schema doc is the source of truth and the DBC is kept in
# sync with it, so reading N here means this check follows a schema bump instead of
# going stale behind one.
DBC_SCHEMA = IDENT["dbc_version"]

# Spec 7.1: without 0x592 the device reads the key OFF and transmits nothing,
# so the 0x051 stimulus below would produce a device that looks armed and
# silent. 0x592 EPRI_HCU_Sensor_0592, IgnitionKeyState = B0 bit 4.
KEY_ID = 0x592
KEY_ON = bytes([0x10, 0, 0, 0, 0, 0, 0, 0])

# How many of EACH diag page must arrive before the last one is treated as a
# settled reading. Three, so the one asserted on is neither the first nor
# immediately after it. See the break condition for what one page per ID cost.
MIN_PAGES = 3

#
# PACE THE STIMULUS. THE LOOP USED TO SEND AS FAST AS THE PCAN WOULD ACCEPT,
# AND THAT BROKE THE MEASUREMENT (found 2026-10-04, first hardware run after
# the 0x7F8/0x7F9 pages were added).
#
# Unpaced, the loop puts 0x051 plus eight interlock IDs on a 500 kbit bus with
# no gaps, which saturates it. The diag pages are 0x7F1-0x7F9 -- numerically
# the HIGHEST ids on this bus and therefore the LOWEST priority -- so under
# saturation they lose every arbitration and essentially never get through. The
# device's own inhibit frames fail the same way, which trips spec 7 and latches
# it: the run that exposed this showed diag_flags 0xC6, abort_latched set,
# live 0, one page of each id and 0x7F2 missing entirely.
#
# It was invisible before because the loop broke as soon as it had one page of
# each, so it stopped before saturation had built up -- which is why a harness
# whose exit condition is "I have enough data" can hide the thing that limits
# how much data there is.
#
# 10 ms is the truck's measured 0x051 cadence (spec 5: ~100 Hz, median
# 9.4-10.5 ms), so this is also simply the right stimulus.
CMD_PERIOD_S = 0.010

def post(p):
    r = urllib.request.Request("http://192.168.80.1"+p, data=b"", method="POST")
    urllib.request.urlopen(r, timeout=8).read()

found = {}
for cfg in can.detect_available_configs(["pcan", "kvaser"]):
    if "Virtual" not in cfg.get("device_name",""):
        found.setdefault(cfg["interface"], cfg)
# OPEN ORDER IS LOAD-BEARING: PCAN FIRST, THEN KVASER.
# Resolved by the interposer session 2026-09-22. `can.Bus()` on the PCAN
# takes seconds to return, and a bus already open while another is still
# opening receives and buffers that whole time, then delivers the backlog in
# a burst when reading starts. Open the Kvaser first and it accumulates ~33
# frames through the PCAN's slow open; open the PCAN first and nothing waits
# on anything. Measured over the same 20 s window, varying only the order:
# kvaser-then-pcan gave 113 vs 80 frames and 21 "duplicates"; pcan-then-kvaser
# gave 80 vs 80 and zero. There is no receiver duplication -- that conclusion
# is withdrawn, and this was its whole cause.
# Swapping these two lines looks harmless and is not. The settle() below is
# the belt to this braces.
pcan = can.Bus(interface="pcan", channel=found["pcan"]["channel"], bitrate=500000)
kvaser = can.Bus(interface="kvaser", channel=found["kvaser"]["channel"], bitrate=500000)
print("settling dongles (%.1fs) ... dropped %d backlog frames"
      % (SETTLE_S, settle(pcan, kvaser)))

try:
    post("/gen_inhibit_set?mode=3")
    time.sleep(0.4)
    # Keep EVERY status frame, not just the last, and run a fixed window with
    # no early break.
    #
    # This used to stop at `len(seen)==3 and elapsed>2` and assert on whatever
    # 0x7F1 it happened to hold. On 2026-09-22 that sampled the frame emitted
    # at the instant of arming -- flags 0x06, before any stimulus had arrived
    # -- and reported bit6 key_on FAIL while the device's own JSON showed
    # tx_ok=399, i.e. transmission had plainly been permitted. diag_flags is a
    # snapshot taken when the frame is built, so WHICH frame you sample is the
    # whole measurement. `diag_flags_trace.py` prints all of them and was what
    # settled it: 0x66 in six of seven, 0x06 only at t=0.
    #
    # Assert on the LAST one, and print the series so a future mismatch shows
    # its own history instead of needing a second script.
    seen = {}
    all_pages = {}
    stream = []         # every diag page in the order received, for one decoder
    status_series = []
    t0 = time.time()
    for i in range(600):
        t_send = time.perf_counter()
        pcan.send(can.Message(arbitration_id=0x051,
                  data=bytes([0x08,0x00,0x80,0xFF,0x7F,i&0x0F]), is_extended_id=False))
        send_interlocks(pcan, i)
        m = kvaser.recv(timeout=0.01)
        while m is not None:
            if m.arbitration_id in DIAG_IDS:
                seen[m.arbitration_id] = bytes(m.data)
                #
                # KEEP EVERY PAGE, NOT JUST THE LAST, FOR ALL FIVE IDS
                # (2026-10-04). This file already records the 2026-09-22
                # lesson for 0x7F1 -- diag_flags is a snapshot taken when the
                # frame is built, so WHICH frame you sample is the whole
                # measurement, and sampling the arming-instant frame reported
                # key_on FAIL on a device that was plainly transmitting.
                #
                # The first hardware run after 0x7F8 was added hit the same
                # thing from the other side: arm_block read 'no_fresh_cmd'
                # while 0x7F1 from a DIFFERENT instant showed inhibit_live set
                # and tx_ok was 339. The lesson had been learned for one page
                # and the new page inherited the weakness, because `seen` keeps
                # one frame per ID and the five are not simultaneous.
                #
                all_pages.setdefault(m.arbitration_id, []).append(
                    (round(time.time() - t0, 3), bytes(m.data)))
                stream.append((round(time.time() - t0, 3), m.arbitration_id, bytes(m.data)))
                if m.arbitration_id == 0x7F1 and len(m.data) >= 4:
                    status_series.append((time.time()-t0, m.data[3]))
            m = kvaser.recv(timeout=0.0)
        #
        # Was `len(seen) == 3` -- a literal that would have cut the loop short
        # the moment the three original pages arrived, so adding 0x7F8/0x7F9
        # to DIAG_IDS alone would NOT have been enough: the break could fire
        # before either showed up, and they would have been reported missing
        # on a device that emits them perfectly well.
        #
        #
        # ENOUGH PAGES OF EACH TO ASSERT ON A SETTLED ONE, not merely one of
        # each. `len(seen) == len(DIAG_IDS)` was satisfied by a single page per
        # ID, which on the first hardware run meant asserting on the
        # arming-instant 0x7F8 -- arm_block 'no_fresh_cmd' from before the
        # device went live, against an 0x7F1 from after. Requiring MIN_PAGES of
        # every ID means the last of each is a steady-state reading.
        #
        if (len(seen) == len(DIAG_IDS)
                and all(len(all_pages.get(i, ())) >= MIN_PAGES
                        for i in DIAG_IDS)
                and time.time() - t0 > 4):
            break

        # Hold the 10 ms cadence. Without this the loop saturates the bus and
        # starves the very pages it is trying to read -- see CMD_PERIOD_S.
        slack = CMD_PERIOD_S - (time.perf_counter() - t_send)
        if slack > 0:
            time.sleep(slack)
    post("/gen_inhibit_set?mode=0")
finally:
    for b in (pcan, kvaser):
        try: b.shutdown()
        except Exception: pass

#
# A RUN RECORD, because until 2026-10-04 this script wrote NONE and its first
# hardware pass existed only in a chat message. That is precisely the failure
# just fixed in wican_autoarm_bench.py -- the 2026-09-26 gate/live result
# survives only as prose because nothing durable was written -- and it would
# have recurred here for the same reason: a check whose output is a terminal
# printout cannot be compared with a later run, tied to a build, or quoted
# with evidence behind it.
#
# The device's build is recorded from the 0x7F3 BUILD page on the WIRE as well
# as from HTTP, so the record names what it ran against by the same two
# independent paths diag_build_page_check.py uses.
#
# Decode every page once, in the order received (gi_diag.Decoder): SEQ holds each id's
# decoded pages with their times, LAST the last of each. An unknown schema, or one that
# is not HEAD's, raises here: the run is refused, not judged.
DEC = gi_diag.Decoder(SCHEMAS, IDENT)
SEQ, LAST = {}, {}
for _t, _fid, _data in stream:
    _d = DEC.feed(_fid, _data, t=_t)
    if _d is None:
        continue
    SEQ.setdefault(_fid, []).append((_t, _d))
    LAST[_fid] = _d

record = {
    "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "decoder": dict(IDENT, **DEC.summary()),
    "dbc_schema": DBC_SCHEMA,
    "pages_required": {"0x%X" % i: DIAG_IDS[i] for i in sorted(DIAG_IDS)},
    "pages_seen": {"0x%X" % i: len(all_pages.get(i, ()))
                   for i in sorted(DIAG_IDS)},
    "min_pages": MIN_PAGES,
    "cmd_period_s": CMD_PERIOD_S,
    "status_flag_series": [[round(t, 3), "0x%02X" % f]
                           for t, f in status_series],
}
try:
    _cs = urllib.request.urlopen(
        "http://192.168.80.1/check_status", timeout=5).read()
    record["device_http_git_version"] = (
        json.JSONDecoder().raw_decode(
            _cs.decode("utf-8", "replace").lstrip())[0].get("git_version"))
except Exception as _e:
    record["device_http_git_version"] = None
    record["device_http_error"] = "%s: %s" % (type(_e).__name__, _e)

print("diag IDs captured: %s" % sorted("0x%X"%k for k in seen))
print("0x7F1 status frames seen: %d%s" % (
    len(status_series),
    "  flags " + " ".join("%.2fs:0x%02X" % r for r in status_series)
    if status_series else ""))
if len(status_series) < 2:
    print("  WARNING: fewer than two status frames -- the one asserted on may"
          " be the arming-instant frame, which is not a measurement")

#
# THE SEQUENCE OF THE STATE PAGES, printed because the assertions below read
# only the LAST of each and a reader needs to see whether that last one was
# settled. 0x7F1 and 0x7F8 are separate frames built at separate instants, so
# the cross-page row can only be trusted where both have stopped changing.
#
print("\nPAGE SEQUENCES (the assertions use the LAST of each)")
for fid in (0x7F1, 0x7F8):
    rows = SEQ.get(fid, [])
    print("  0x%X %-8s %d pages%s"
          % (fid, DIAG_IDS.get(fid, "?"), len(rows),
             "" if len(rows) >= MIN_PAGES
             else "   <-- fewer than MIN_PAGES=%d, not settled" % MIN_PAGES))
    for t, d in rows:
        if isinstance(d, gi_diag.Undecodable):
            print("      t=%6.2fs UNDECODABLE: %s" % (t, d.reason))
            continue
        if fid == 0x7F1:
            print("      t=%6.2fs flags=0x%02X live=%d key=%d block_src=0x7F1"
                  % (t, int(d["diag_flags"]), 1 if int(d["diag_flags"]) & 0x20
                     else 0, 1 if int(d["diag_flags"]) & 0x40 else 0))
        else:
            print("      t=%6.2fs abort=%-6s arm_block=%-20s would_tx=%s"
                  % (t, str(d["diag_abort_reason"]), str(d["diag_arm_block"]),
                     d["diag_would_tx"]))
ok = True
for fid in sorted(DIAG_IDS):
    if fid not in seen:
        print("  MISSING 0x%X %s" % (fid, DIAG_IDS[fid])); ok = False; continue
    dec = LAST[fid]
    if isinstance(dec, gi_diag.Undecodable):
        # A page the run needs and could not decode is an ERROR, never an absence.
        print("  UNDECODABLE 0x%X %s: %s" % (fid, DIAG_IDS[fid], dec.reason)); ok = False; continue
    print("  0x%X %-9s %s" % (fid, DIAG_IDS[fid], {k: (str(v)) for k,v in dec.items()}))
if ok:
    st = LAST[0x7F1]
    bd = LAST[0x7F3]
    s2 = LAST[0x7F8]
    sk = LAST[0x7F9]
    checks = build_checks(st, bd, s2, sk, DBC_SCHEMA)
    for k,v in checks.items(): print("  [%s] %s" % ("PASS" if v else "FAIL", k))
    ok = all(checks.values())
    record["checks"] = dict(checks)

    # Reported, not asserted -- see the 0x7F9 note above for why. Printed so a
    # nonzero value is visible to whoever reads the run rather than silently
    # discarded, and labelled so nobody mistakes the line for a passed check.
    print("  [report, not asserted] skips withdrawn=%d late=%d | "
          "would_tx=%d tx_queued_behind=%d"
          % (int(sk["diag_skips_withdrawn"]), int(sk["diag_skips_late"]),
             int(s2["diag_would_tx"]), int(s2["diag_tx_queued_behind"])))
    if int(sk["diag_skips_withdrawn"]) or int(sk["diag_skips_late"]):
        print("       NOTE: a skip was counted. Not a failure here -- this "
              "harness does not pace to a truck rate -- but if you were "
              "expecting a quiet bus, find out why before using this run.")
print("=== %s ===" % ("PASS" if ok else "FAIL"))

# Decoded pages, including the 0x7F9 report line's figures, so the record
# carries the numbers and not just the verdicts.
record["decoded"] = {}
for fid in sorted(DIAG_IDS):
    if fid in seen and LAST.get(fid):
        record["decoded"]["0x%X" % fid] = {k: str(v) for k, v in LAST[fid].items()}
if LAST.get(0x7F3):
    _b = LAST[0x7F3]
    if "diag_build_id" in _b:      # schema 8 on (port step 0c): the image hash's first four bytes
        record["device_wire_build_id"] = "%08x" % int(_b["diag_build_id"])
    else:
        record["device_wire_git_hash"] = "0x%08X" % int(_b["diag_git_hash"])
if LAST.get(0x7F9) and LAST.get(0x7F8):
    _sk = LAST[0x7F9]
    _s2 = LAST[0x7F8]
    record["reported_not_asserted"] = {
        "skips_withdrawn": int(_sk["diag_skips_withdrawn"]),
        "skips_late": int(_sk["diag_skips_late"]),
        "would_tx": int(_s2["diag_would_tx"]),
        "tx_queued_behind": int(_s2["diag_tx_queued_behind"])}
record["ok"] = bool(ok)

_runs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
if not os.path.isdir(_runs):
    os.makedirs(_runs)
_stamp = record["when"].replace(":", "").replace("-", "")
for _p in (os.path.join(_runs, "diag_check.json"),
           os.path.join(_runs, "diag_check_%s.json" % _stamp)):
    with open(_p, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(json.dumps(record, indent=1) + "\n")
print("wrote runs/diag_check.json and runs/diag_check_%s.json" % _stamp)

sys.exit(0 if ok else 2)
