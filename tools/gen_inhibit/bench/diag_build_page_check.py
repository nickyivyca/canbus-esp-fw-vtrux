#!/usr/bin/env python3
"""Read the 0x7F3 BUILD page off the WIRE and check it names the running firmware.

WHY THE WIRE AND NOT HTTP. `GET /check_status` already returns `git_version`, and after
the 2026-10-03 flash it returned `4a0a796`. That is the device answering about itself
through the web server -- one instrument. The 0x7F3 BUILD page is the same fact arriving
by a completely different path: built by `gen_inhibit`'s own diag packer from
`fnv1a32(GIT_SHA)`, handed to the TWAI controller, put on the wire, and decoded by a
separate dongle against the DBC. If the two agree, the running image's identity does not
rest on either one alone.

It is also the check that matters for every LOG. A capture carries no HTTP, so 0x7F3 is
the only way a log can be matched to a firmware rev afterwards -- which is the whole
reason the page exists. A page that is wrong, stale or absent would make every future
capture unattributable, and nothing else would reveal it.

HOW THE EXPECTED VALUE IS DERIVED. `diag_git_hash` is FNV-1a 32-bit over the GIT_SHA
string, and GIT_SHA comes from `git describe --tags --always --dirty` at CMake configure
time. So the expectation is computed here from the SHA, with the same FNV-1a constants
the firmware uses, rather than from a number recorded by an earlier session. Decoding is
via cantools against the diag DBC (gi_diag.py: from git, by schema byte), never by hand
-- a field at the wrong offset or a width that truncates then shows up as a wrong value
rather than as nothing. The record keeps the decoder's run identity (toolkit item 10).

TWO BENCH RULES THIS OBEYS, BOTH LEARNED THE HARD WAY HERE.

  1. THE PCAN IS OPENED FIRST. A bus already open while another is still opening receives
     and buffers throughout, then delivers the backlog in a burst -- which is what the
     withdrawn "the Kvaser duplicates frames" finding actually was. Two sessions
     reproduced it faithfully because both opened the Kvaser first. The open order below
     is pinned deliberately; swapping two adjacent lines looks harmless and is not.
     (`diag_duplicate_crosscheck.py`, the tool that DIAGNOSED this, still opens
     kvaser-then-pcan itself. Its result is withdrawn so nothing rests on it, but do not
     copy its loop.)
  2. A CHECK WHOSE FAILURE LOOKS LIKE ITS SUCCESS IS NOT A CHECK. Seeing zero 0x7F3
     frames would otherwise read as "nothing wrong". So: zero pages is a hard FAIL with
     its own message, and the per-ID counts for the other diag pages are printed
     alongside, which establishes that the population is non-empty before any conclusion
     is drawn about 0x7F3 in particular.

THE DEVICE MUST BE OUT OF OFF TO EMIT DIAG AT ALL. After a calibration arm it is left in
mode 0 with the bus released, so this sets mode 3 and restores mode 0 in a finally. With
no 0x051 on the segment the arm gate stays blocked and the device transmits only diag,
which is exactly what is wanted. The PCAN must be open for the device's frames to be
ACKed -- a lone transmitter with no other node on the segment never completes a frame.

Bench only. Needs PCAN and Kvaser on the desk segment and the WiCAN at 192.168.80.1.

Usage:
    py -3.11 diag_build_page_check.py --expect-sha 4a0a796
    py -3.11 diag_build_page_check.py            # takes the SHA from /check_status
"""

import argparse
import io
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict


import can                                                          # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import gi_diag  # noqa: E402  the diag DBC from git, by schema byte (toolkit item 10)

HERE = os.path.dirname(os.path.abspath(__file__))

WICAN = "http://192.168.80.1"

BUILD_ID = 0x7F3
STATUS_ID = 0x7F1
#
# 0x7F1 diag_flags bit3 == autoarm_build. Firmware chain, all three links
# checked 2026-10-04: gen_inhibit.c:1587 `cfg.autoarm_configured =
# (GEN_INHIBIT_AUTOARM_MODE != GEN_INHIBIT_OFF)`, gen_inhibit_core.c:988
# `| (st->cfg.autoarm_configured ? 0x08 : 0)`, and the DBC CM_ on diag_flags
# documents "bit3 autoarm_build".
#
AUTOARM_BIT = 0x08
# Every page the WiCAN owns at schema 5. Counted so that "no 0x7F3" can be
# distinguished from "no diag at all", which are different faults.
DIAG_IDS = (0x7F1, 0x7F2, 0x7F3, 0x7F8, 0x7F9)


def fnv1a32(s):
    """The firmware's hash, reproduced exactly (gen_inhibit.c fnv1a32)."""
    h = 2166136261
    for ch in s.encode("ascii"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def get_json(path):
    return json.loads(
        urllib.request.urlopen(WICAN + path, timeout=8).read().decode("utf-8", "replace"))


def post(path):
    req = urllib.request.Request(WICAN + path, data=b"")
    return urllib.request.urlopen(req, timeout=8).read()


def open_buses(want_ixxat):
    """PCAN FIRST. See rule 1 in the docstring before touching this order."""
    found = {}
    for cfg in can.detect_available_configs(["pcan", "kvaser", "ixxat"]):
        if "Virtual" in str(cfg.get("device_name", "")):
            continue
        found.setdefault(cfg["interface"], cfg)

    order = ["pcan", "kvaser"] + (["ixxat"] if want_ixxat else [])

    #
    # AN EXPLICITLY REQUESTED WITNESS THAT IS ABSENT IS A HARD FAILURE, NOT A
    # FALLBACK. The earlier version skipped a missing adapter and ran on
    # whatever was left, so `--ixxat` on a bench with no IXXAT produced a
    # complete, confident result from one fewer witness than was asked for --
    # a failure indistinguishable from success.
    #
    # This stopped being hypothetical on 2026-10-04: the user swapped the
    # IXXAT out for the Innomaker, so `--ixxat` now matches no hardware. The
    # Innomaker is a gs_usb device needing the libusb_package DLL bootstrap and
    # is NOT reachable from `py -3.11` here (module not installed), so it is
    # deliberately not offered as a substitute -- a second witness on a
    # different segment would not be one.
    #
    missing = [n for n in order if n not in found]
    if missing:
        raise SystemExit(
            "requested transport(s) not present: %s\n"
            "  detected: %s\n"
            "  Refusing to run with fewer witnesses than were asked for. If the\n"
            "  bench changed, say so explicitly rather than letting the run\n"
            "  silently narrow." % (", ".join(missing),
                                    ", ".join(sorted(found)) or "nothing"))

    buses = {}
    for name in order:
        buses[name] = can.Bus(interface=name,
                              channel=found[name]["channel"],
                              bitrate=500000,
                              **({"auto_reset": True} if name == "pcan" else {}))
        if name == "pcan":
            # A previous run that ended bus-off otherwise leaves the channel
            # unusable. Tolerate failure: a fresh channel has nothing to reset.
            try:
                buses[name].reset()
            except Exception:
                pass
        print("  opened %s (channel %r)" % (name, found[name]["channel"]))
    return buses


def settle(buses, seconds=2.0):
    """Drain whatever each bus buffered while the others were opening.

    On a silent segment this drops 0, which is the expected reading and NOT evidence
    the settle is unnecessary -- on a live segment it has measured 40 frames in one
    open order and 16 in the other.
    """
    t_end = time.time() + seconds
    dropped = defaultdict(int)
    while time.time() < t_end:
        for name, bus in buses.items():
            while True:
                m = bus.recv(timeout=0.0)
                if m is None:
                    break
                dropped[name] += 1
    return dict(dropped)


def autoarm_verdict(status_decoded, expect):
    """Decide the auto-arm discriminator from decoded 0x7F1 pages.

    THE ONE FUNCTION THAT MAKES THIS DECISION. `autoarm_bit_offline_test.py` imports
    and calls it, so the bench path and the tested path are the same code. It used to
    hold a duplicate of this logic and would have passed with the gate below deleted.

    Returns `(ok, reason, detail)`. `reason` is one of a closed set --
    "ok", "no_pages", "varied", "mismatch" -- so a test can assert on the reason
    rather than on the boolean, and a flipped comparison shows up as the wrong
    reason instead of merely the wrong verdict. `detail` carries what the caller
    needs to print and to record, and is never interpreted here.

    THE POPULATION GATE IS NOT OPTIONAL AND THIS IS WHY. `expect == 0` is the
    SHIPPING expectation, so bit3 clear is the pass. A device that is silent --
    wrong mode, wrong segment, nothing to ACK it, dead -- emits no 0x7F1 at all,
    which shows no bits set and would satisfy `expect == 0` while proving nothing
    whatever. So an empty population FAILS both expectations. `expect == 1` would
    fail safe on silence by accident; relying on that would leave the check sound
    for one of its two arguments and invite being read as sound for both.
    """
    flags = [d.get("diag_flags") for d in status_decoded
             if isinstance(d.get("diag_flags"), int)]
    detail = {"pages": len(flags),
              "flag_bytes": ["0x%02X" % f for f in sorted(set(flags))],
              "expected": expect}
    if not flags:
        return False, "no_pages", detail
    bits = sorted(set(1 if (f & AUTOARM_BIT) else 0 for f in flags))
    detail["bit3"] = bits[0] if len(bits) == 1 else bits
    if len(bits) > 1:
        return False, "varied", detail
    if bits[0] != expect:
        return False, "mismatch", detail
    return True, "ok", detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0,
                    help="capture window. The page repeats at diag_period_ms "
                         "240, so 6 s is ~25 pages -- enough that one corrupt "
                         "frame cannot decide the result.")
    ap.add_argument("--expect-sha", default=None,
                    help="the GIT_SHA the page should name. Default: whatever "
                         "/check_status reports, which still cross-checks the "
                         "wire against HTTP.")
    ap.add_argument("--expect-autoarm", type=int, default=None,
                    choices=(0, 1),
                    help="assert 0x7F1 diag_flags bit3 (autoarm_build): 0 "
                         "for the shipping image, 1 for a build-autoarm "
                         "image. This is the ONLY discriminator between the "
                         "two after a flash -- E3 passes identically on both "
                         "and at HEAD they even share a filename. Zero "
                         "decoded 0x7F1 pages is a hard FAIL whichever value "
                         "is asked for, because a silent device would "
                         "otherwise satisfy --expect-autoarm 0 by showing "
                         "nothing at all.")
    ap.add_argument("--ixxat", action="store_true",
                    help="open the IXXAT as a second witness. It never "
                         "transmits and is never the sole witness.")
    ap.add_argument("--out", default="runs/diag_build_page_check.json")
    args = ap.parse_args()

    out = args.out
    if not os.path.isabs(out) and os.path.dirname(out) in ("", "runs"):
        out = gi_paths.runs(os.path.basename(out))

    # The decoder's identity first: a working-tree DBC that is not HEAD's is refused
    # here, before the device is touched, and every page must be on this DBC's schema.
    schemas = gi_diag.SchemaSet.from_git()
    ident = gi_diag.run_identity(schemas=schemas)
    dec = gi_diag.Decoder(schemas, ident)
    db = schemas.head().db
    msg = db.get_message_by_frame_id(BUILD_ID)
    #
    # Not fatal if 0x7F1 is missing from the DBC: this tool is about 0x7F3,
    # and build_output.py's "DBC defines every diag page" row is where a
    # missing page is supposed to fail. But --expect-autoarm cannot then be
    # honoured, so refuse rather than silently skip the assertion the caller
    # asked for -- a skipped assertion reports as a pass.
    #
    try:
        status_msg = db.get_message_by_frame_id(STATUS_ID)
    except KeyError:
        status_msg = None
    if args.expect_autoarm is not None and status_msg is None:
        raise SystemExit(
            "--expect-autoarm needs 0x%03X in %s and it is not there.\n"
            "  Refusing to run: skipping the assertion would report a pass "
            "for a check that never happened."
            % (STATUS_ID, gi_diag.DBC_PATH))
    print("0x7F3 BUILD page check")
    print("  DBC %s at %s (schema %d) -> message %s, DLC %d, signals %s"
          % (gi_diag.DBC_PATH, ident["dbc_commit"][:10], ident["dbc_version"], msg.name, msg.length,
             ", ".join(s.name for s in msg.signals)))

    # --- what the device says about itself over HTTP, before anything else
    try:
        cs = get_json("/check_status")
    except Exception as exc:
        print("device did not answer /check_status: %r" % exc)
        print("Join its AP first.")
        return 2
    http_sha = cs.get("git_version")
    expect_sha = args.expect_sha or http_sha
    expect_hash = fnv1a32(expect_sha)
    print("  HTTP /check_status git_version = %s" % http_sha)
    print("  expecting diag_git_hash = fnv1a32(%r) = 0x%08X" % (expect_sha, expect_hash))
    if args.expect_sha and args.expect_sha != http_sha:
        print("  NOTE: --expect-sha differs from what HTTP reports. The wire is "
              "being checked against the ARGUMENT, and the HTTP disagreement is "
              "itself a finding.")

    print("  opening buses (PCAN first -- see rule 1):")
    buses = open_buses(args.ixxat)
    if "pcan" not in buses:
        print("FAIL: no PCAN. Without a second node the device's frames are never "
              "ACKed and it cannot complete a diag transmission.")
        return 1
    if "kvaser" not in buses:
        print("FAIL: no Kvaser witness.")
        return 1

    drained = settle(buses)
    print("  settled 2 s, drained %s" % (drained or "0 frames"))

    st_before = get_json("/gen_inhibit")
    print("  device before: mode=%s bus_on=%s abort_latched=%s"
          % (st_before.get("mode"), st_before.get("bus_on"),
             st_before.get("abort_latched")))

    counts = defaultdict(lambda: defaultdict(int))
    decoded = []
    raw_build = []
    status_decoded = []
    armed = False
    try:
        post("/gen_inhibit_set?mode=3")
        armed = True
        time.sleep(0.4)

        t_end = time.time() + args.seconds
        while time.time() < t_end:
            for bname, bus in buses.items():
                while True:
                    m = bus.recv(timeout=0.002)
                    if m is None:
                        break
                    if m.arbitration_id in DIAG_IDS:
                        counts[bname][m.arbitration_id] += 1
                    # Every diag page off the witness goes through one decoder, in
                    # order, so a page is read on the schema the device last stated.
                    page = None
                    if bname == "kvaser" and m.arbitration_id in dec.ids:
                        try:
                            page = dec.feed(m.arbitration_id, bytes(m.data), t=m.timestamp)
                        except gi_diag.DiagError:
                            raise
                        except Exception as exc:
                            page = {"_decode_error": repr(exc)}
                        if isinstance(page, gi_diag.Undecodable):
                            page = {"_decode_error": page.reason}
                    if m.arbitration_id == BUILD_ID and bname == "kvaser":
                        raw_build.append(bytes(m.data))
                        decoded.append(page)
                    #
                    # Same witness as 0x7F3 deliberately. Reading the two
                    # pages off different adapters would mean a disagreement
                    # could be either the device or the rig, and this check
                    # exists to attribute a flashed image, not to debug the
                    # bench.
                    #
                    if (m.arbitration_id == STATUS_ID and bname == "kvaser"
                            and status_msg is not None):
                        status_decoded.append(page)
    finally:
        if armed:
            try:
                post("/gen_inhibit_set?mode=0")
            except Exception as exc:
                print("WARNING: could not return device to mode 0: %r" % exc)
        for bus in buses.values():
            try:
                bus.shutdown()
            except Exception:
                pass

    st_after = get_json("/gen_inhibit")
    print("  device after:  mode=%s bus_on=%s tx_ok=%s"
          % (st_after.get("mode"), st_after.get("bus_on"), st_after.get("tx_ok")))

    print("\nDIAG PAGES SEEN (establishes the population before judging 0x7F3)")
    for bname in sorted(counts):
        per = counts[bname]
        print("  %-7s %s" % (bname,
                             "  ".join("0x%03X=%d" % (i, per.get(i, 0))
                                       for i in DIAG_IDS)))
    if not counts:
        print("  none on any bus")

    result = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "http_git_version": http_sha,
        "expect_sha": expect_sha,
        "expect_git_hash": "0x%08X" % expect_hash,
        "seconds": args.seconds,
        "buses_opened": sorted(buses.keys()),
        "open_order": "pcan first, then kvaser" + (", then ixxat" if args.ixxat else ""),
        "settle_drained": drained,
        "counts": {b: {"0x%03X" % i: c for i, c in per.items()}
                   for b, per in counts.items()},
        "mode_before": st_before.get("mode"),
        "mode_after": st_after.get("mode"),
        "decoder": dict(ident, **dec.summary()),
    }

    # --- the hard failure: no page at all must NOT read as "nothing wrong"
    if not decoded:
        other = sum(sum(per.values()) for per in counts.values())
        print("\nFAIL: zero 0x7F3 pages decoded in %.1f s." % args.seconds)
        if other:
            print("  But %d other diag frames were seen, so the device WAS "
                  "transmitting -- the fault is specific to 0x7F3." % other)
        else:
            print("  And no diag frames of any ID were seen, so the device was "
                  "not transmitting at all. Check mode took effect and that the "
                  "PCAN is on the same segment to ACK.")
        result["verdict"] = "FAIL: no 0x7F3 page"
        io.open(out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(result, indent=1) + "\n")
        print("wrote %s" % out)
        return 1

    # --- every page must agree with itself, then with the expectation
    uniq = sorted(set(raw_build))
    print("\n0x7F3 DECODED (%d pages, %d distinct payload%s)"
          % (len(decoded), len(uniq), "" if len(uniq) == 1 else "s"))
    for pl in uniq:
        print("  payload %s" % pl.hex(" "))
    for k in sorted(decoded[0].keys()):
        vals = sorted(set(repr(d.get(k)) for d in decoded))
        if len(vals) == 1:
            print("  %-24s %s" % (k, vals[0]))
        else:
            print("  %-24s VARIES across pages: %s" % (k, ", ".join(vals)))

    got_hash = decoded[0].get("diag_git_hash")
    fw_ver = decoded[0].get("diag_fw_ver16")
    schema = decoded[0].get("diag_schema_ver_build")
    hash_ok = (got_hash == expect_hash)
    stable = (len(uniq) == 1)

    result.update({
        "pages_decoded": len(decoded),
        "distinct_payloads": [p.hex(" ") for p in uniq],
        "diag_git_hash": got_hash,
        "diag_git_hash_hex": ("0x%08X" % got_hash
                              if isinstance(got_hash, int) else None),
        "diag_fw_ver16": fw_ver,
        "diag_schema_ver_build": schema,
        "hash_matches_expected": hash_ok,
        "payload_stable": stable,
    })

    print("\nVERDICT")
    print("  diag_git_hash on the wire   0x%08X"
          % got_hash if isinstance(got_hash, int) else "  diag_git_hash NOT AN INT")
    print("  expected from %-13s 0x%08X" % (expect_sha, expect_hash))
    print("  %s" % ("MATCH -- the wire names the same build HTTP reports"
                    if hash_ok else
                    "MISMATCH -- the wire does NOT name the expected build"))
    print("  diag_fw_ver16 = %s, diag_schema_ver_build = %s" % (fw_ver, schema))
    if not stable:
        print("  WARNING: the payload varied between pages. A build page must be "
              "constant for the life of the image.")

    # --- the auto-arm discriminator. The DECISION is autoarm_verdict(); this
    #     block only prints and records it, so the offline test can import the
    #     decision and exercise the same code the bench runs.
    autoarm_ok = True
    if args.expect_autoarm is not None:
        autoarm_ok, reason, detail = autoarm_verdict(status_decoded,
                                                     args.expect_autoarm)
        print("\nAUTO-ARM DISCRIMINATOR (0x%03X diag_flags bit3)" % STATUS_ID)
        if reason == "no_pages":
            seen = sum(per.get(STATUS_ID, 0) for per in counts.values())
            print("  FAIL: zero 0x%03X pages decoded in %.1f s."
                  % (STATUS_ID, args.seconds))
            if seen:
                print("  %d raw 0x%03X frame(s) were counted but none "
                      "decoded, so the fault is the payload, not the "
                      "silence." % (seen, STATUS_ID))
            else:
                print("  No 0x%03X frames at all. THIS IS NOT 'bit3 is "
                      "clear': a silent device shows no bits set and would "
                      "satisfy --expect-autoarm 0 while proving nothing."
                      % STATUS_ID)
        else:
            got = detail["bit3"]
            print("  pages decoded        %d" % detail["pages"])
            print("  distinct flag bytes  %s" % ", ".join(detail["flag_bytes"]))
            print("  bit3 (autoarm_build) %s" % got)
            print("  expected             %d" % args.expect_autoarm)
            if reason == "varied":
                print("  FAIL: bit3 VARIED across pages. It is compiled in, "
                      "so it cannot change within one image.")
            elif reason == "mismatch":
                print("  MISMATCH -- the running image is the %s build, not "
                      "the %s one."
                      % ("auto-arm" if got else "shipping",
                         "auto-arm" if args.expect_autoarm else "shipping"))
            else:
                print("  MATCH -- the running image is the %s build."
                      % ("auto-arm" if got else "shipping"))
        detail["reason"] = reason
        detail["verdict"] = "ok" if autoarm_ok else "FAIL"
        result["autoarm"] = detail

    ok = hash_ok and stable and autoarm_ok
    result["verdict"] = ("ok" if ok else "FAIL")
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(result, indent=1) + "\n")
    print("wrote %s" % out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
