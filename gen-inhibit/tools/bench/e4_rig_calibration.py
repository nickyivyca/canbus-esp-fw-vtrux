#!/usr/bin/env python3
"""E4 rig calibration -- can this bench produce truck-rate traffic, and what lands?

Spec section 5.1 item 5 asks the E4 load model to show a margin, and section
12.4's `full-replay bus` row then asks for it on hardware. E4 is a model, and
three of its inputs were never measured on this rig:

  1. `air_time_us(dlc)` -- 236 us DLC6 / 276 us DLC8, computed worst-case
     bit-stuffed at 500 kbit and corroborated only against a Kvaser figure.
  2. the truck frame rate, taken as ~2250 frames/s from spec 9.3.
  3. whether a general-purpose host can emit a recorded stream at its recorded
     timing at all. Spec 12.2 raises this as an open question in as many words:
     "Unverified and worth checking early: whether a transmitting USB adapter
     batches outbound frames, collapsing gaps rather than widening them."

This measures all three, WITH NO DEVICE UNDER TEST. Nothing here needs the
WiCAN, so it is the half of the calibration that can run before the device is
flashed or joined, and it is the half that gates the rest: if the rig cannot
put truck-rate traffic on the wire faithfully, the full-replay row is not
runnable as specified and E4's hardware arm does not exist.

Spec 12.2's own proposal, executed: "drive the transmitter from an *intended*
schedule and let the hardware-timestamping witness record what actually
landed, turning rig timing from an open-loop requirement into a measured
covariate."

TWO WITNESSES, AND THAT IS THE POINT. At truck rate a Python reader can fall
behind its own driver queue, and a witness that drops frames is
indistinguishable from a transmitter that never sent them -- the shortfall
reads the same either way. So the segment is witnessed twice, on two different
adapters through two different drivers. Two independent witnesses agreeing on a
count rules out one witness's queue; disagreeing localises the loss to the
witness that is short. A single witness could only report a number, not whether
to believe it.

Bench order rules, both from the 2026-09-22 withdrawal recorded in
`README.md`: open the PCAN FIRST, and settle 2 s and drain before counting. A
bus already open while another is still opening buffers throughout and delivers
the backlog in a burst, which was read as receiver duplication by two sessions
independently before the open-order experiment settled it.

Phases (`--phase`):

  quiet    Listen only. Is the segment silent before anything is claimed about
           it? A powered WiCAN in any non-OFF mode emits diag at ~400 ms, and
           an armed one answers replayed `0x051`, either of which would
           contaminate every count below. Failure here looks different from
           success: it names the IDs it saw.
  airtime  Per-DLC frame time at the wire, bracketed by payload: 0x00 (long
           identical-bit runs, maximum stuffing) against 0xAA (alternating,
           minimum). E4's constants should sit at or above the 0x00 arm.
  replay   A window of a real full-bus truck capture through the PCAN at its
           recorded deltas, witnessed twice, at several speeds. The speeds
           below 1.0 are the control: both ends are certainly capable there, so
           landed == sent at 0.25x and a shortfall at 1.0x localises the limit,
           where a single full-rate arm on its own could not.

Usage:
    py -3.11 tools/gen_inhibit/bench/e4_rig_calibration.py --phase quiet
    py -3.11 ... --phase airtime
    py -3.11 ... --phase replay --seconds 30
    py -3.11 ... --phase all
"""

import argparse
import json
import os
import statistics
import sys
import threading
import time

import can
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

# The stimulus. A listen-only truck capture (`live: false`), so it contains no
# frame the laptop tool transmitted -- replaying a capture taken while our own
# tool was transmitting would feed our own frames back in as if they were the
# truck's. Driver timestamps, not host, per `truck_sync_diagnosis.py`.
CAPTURE = os.path.join(RUNS, "truck_passive2.log")

# The loaded epoch. Measured over this capture in 10 s buckets: 0-90 s runs at
# 2333-2373 frames/s and 95-240 s at 1656-1716, so the capture holds two
# distinct bus states and only the first is the loaded bus section 5 is about.
# Starting at 20 s keeps clear of the capture's opening.
WINDOW_START = 20.0

BITRATE = 500000

# What E4 assumes, so the report can put the measurement next to it rather than
# leaving the comparison to whoever reads the output. From
# `test/gen_inhibit_host/host_runner.c` (`air_time_us`) and spec 9.3.
def e4_air_time_us(dlc):
    """E4's own formula, transcribed from `host_runner.c` air_time_us().

    NOT A TABLE OF REMEMBERED NUMBERS. A first version of this file carried
    {4: 172, 5: 196, 6: 236, 7: 252, 8: 276} written out by hand, and three of
    the five were wrong -- the model's DLC-4 figure is 196 us, not 172, so the
    comparison reported E4 as under-modelling a frame it is in fact
    conservative about. Recomputing it here from the same expression means the
    two can only disagree if the firmware's formula changes.

        const int stuffable = 34 + 8 * dlc;
        const int bits = 47 + 8 * dlc + (stuffable - 1) / 4 + 3;
        return bits * 1000 / bitrate_kbit;

    Worth noting while transcribing it: 47 + 8*dlc already carries the 3-bit
    interframe space over the 44 + 8*dlc of frame proper, so the trailing `+ 3`
    counts the IFS a second time and the model runs 6 us long for that reason
    alone, before stuffing. That is the conservative direction for a deadline
    model and is left alone here; this file measures, it does not change E4.
    """
    stuffable = 34 + 8 * dlc
    bits = 47 + 8 * dlc + (stuffable - 1) // 4 + 3
    return bits * 1000 // (BITRATE // 1000)


E4_AIR_TIME_US = dict((n, e4_air_time_us(n)) for n in range(0, 9))
E4_TRUCK_FPS = 2250.0

# Diag IDs belonging to the two boards that share this bench: 0x7F1-0x7F3 and
# 0x7F8 are the WiCAN's, 0x7F4-0x7F7 the charge interposer's. The stimulus
# capture's highest ID is 0x6A0, so nothing here can collide with a replayed
# frame and the split is unambiguous.
#
# FOREIGN TRAFFIC IS ALSO THE ONLY INDEPENDENT INSTRUMENT THIS SEGMENT HAS.
# The IXXAT turns out not to be on it (0 frames in the quiet phase against the
# PCAN's and Kvaser's 20), so there is no second witness to cross-check a
# shortfall against, and a witness dropping frames under load reads exactly like
# a transmitter that never sent them. The interposer's burst is externally
# generated at a fixed 1 Hz, which makes it a reference signal: a witness that
# keeps up sees all of it, and one whose queue is overflowing loses its share of
# these too. So the foreign count is carried through every arm and compared
# against the rate measured while the segment was otherwise idle.
FOREIGN_IDS = frozenset((0x7F1, 0x7F2, 0x7F3, 0x7F4,
                         0x7F5, 0x7F6, 0x7F7, 0x7F8))


def open_buses(need_tx):
    """Open the segment's adapters, PCAN first, and settle before counting.

    Returns (tx, witnesses) where witnesses is a list of (name, bus). `tx` is
    None when need_tx is false, and the PCAN is still opened first in that case
    so the order rule holds for every phase.
    """
    opened = []
    # PCAN FIRST -- NOT COSMETIC. Swapping this with the Kvaser below looks
    # harmless and is the 2026-09-22 artefact: whichever bus is open while
    # another is still opening accumulates a backlog and delivers it as a
    # burst. Two sessions read that as the Kvaser duplicating frames.
    pcan = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE)
    opened.append(("pcan", pcan))
    kvaser = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    opened.append(("kvaser", kvaser))
    ixxat = can.Bus(interface="ixxat", channel=0,
                    unique_hardware_id="HW630227", bitrate=BITRATE)
    opened.append(("ixxat", ixxat))

    print("  settling 2 s and draining")
    time.sleep(2.0)
    for name, bus in opened:
        n = 0
        while True:
            m = bus.recv(timeout=0.0)
            if m is None:
                break
            n += 1
        # On a silent segment this drops 0, which is the expected reading and
        # not evidence the settle is unnecessary.
        print("    %-7s drained %d" % (name, n))

    if need_tx:
        return pcan, [("kvaser", kvaser), ("ixxat", ixxat)]
    return None, opened


def close_buses(buses):
    for name, bus in buses:
        try:
            bus.shutdown()
        except Exception as exc:
            print("  warning: closing %s raised %s" % (name, exc))


class Witness(threading.Thread):
    """Drain one adapter into a list as fast as Python manages.

    Keeps the driver's timestamp and never time.time(): host receive times on a
    USB adapter show transfer batching (spec 12, `truck_sync_diagnosis.py`),
    which would be mistaken here for the very thing under test.
    """

    def __init__(self, name, bus):
        threading.Thread.__init__(self, daemon=True)
        self.name_ = name
        self.bus = bus
        self.frames = []
        self.stop = threading.Event()

    def run(self):
        append = self.frames.append
        recv = self.bus.recv
        while not self.stop.is_set():
            m = recv(timeout=0.2)
            if m is not None:
                append((m.timestamp, m.arbitration_id, bytes(m.data),
                        bool(m.is_error_frame), bool(m.is_extended_id)))


def frame_bits(dlc, extended):
    """Bit count of one frame including the 3-bit interframe space, unstuffed.

    Standard: SOF 1 + ID 11 + RTR 1 + IDE 1 + r0 1 + DLC 4 + data 8n + CRC 15 +
    delim 1 + ACK 1 + delim 1 + EOF 7 = 44 + 8n, and 47 + 8n with the IFS.
    Extended adds SRR, 18 more ID bits and r1, twenty bits in all.

    This is a floor, because stuffing only ever adds. It is not a guess: the
    airtime phase measured 221.7 us for an alternating-payload DLC-8 standard
    frame against the 222 us this gives, and 157.4 against 158 for DLC 4, on the
    IXXAT. Worst-case stuffing adds up to floor((stuffed_region - 1) / 4) bits,
    which the ceiling below uses.
    """
    base = (67 if extended else 47) + 8 * dlc
    region = (54 if extended else 34) + 8 * dlc   # SOF..CRC, the stuffed part
    return base, base + (region - 1) // 4


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    i = int(round((len(s) - 1) * p / 100.0))
    return s[i]


# ---------------------------------------------------------------- quiet ------

def phase_quiet(seconds, out):
    print("\n== phase quiet: is the segment silent? ==")
    tx, witnesses = open_buses(need_tx=False)
    ws = [Witness(n, b) for n, b in witnesses]
    for w in ws:
        w.start()
    time.sleep(seconds)
    for w in ws:
        w.stop.set()
    for w in ws:
        w.join(timeout=2.0)

    result = {"seconds": seconds, "per_witness": {}}
    unknown = {}
    for w in ws:
        ids = {}
        ext = set()
        for f in w.frames:
            ids[f[1]] = ids.get(f[1], 0) + 1
            if f[4]:
                ext.add(f[1])
        foreign = sum(v for k, v in ids.items() if k in FOREIGN_IDS)
        other = dict((k, v) for k, v in ids.items() if k not in FOREIGN_IDS)
        result["per_witness"][w.name_] = {
            "frames": len(w.frames),
            "ids": dict(("0x%03X" % k, v) for k, v in sorted(ids.items())),
            "foreign_frames": foreign,
            "foreign_fps": round(foreign / seconds, 2),
            "extended_ids": sorted("0x%X" % k for k in ext),
            #
            # NAMED FOR WHAT IT MEASURES. This is "did this adapter hear the
            # foreign traffic", nothing more. In general it CANNOT tell an
            # adapter on another segment from one that simply heard nothing -- a
            # 4 fps source over five seconds is a thin basis for either -- which
            # is why the old name `on_segment` had to go.
            #
            # BUT ITS 2026-09-26 VERDICT WAS SUBSTANTIVELY RIGHT, and an earlier
            # revision of this comment claimed the opposite. The interposer
            # ORIGINATES 0x7F4-0x7F7 on its vehicle port
            # (charge-interposer-spec.md:683) rather than forwarding them, so
            # they never cross to the charger side even with the bridge up. An
            # adapter hearing none of them while demonstrably alive is therefore
            # genuinely not vehicle-side. The flag was right by LUCK of the
            # traffic being one-sided by spec, not by design -- a right answer on
            # a wrong warrant, which is worth renaming but not worth distrusting.
            #
            # And NOT "the topology phase settles segments": it cannot. A
            # transparent bidirectional bridge gives it the same symmetric matrix
            # one wire does. See phase_topology's docstring.
            #
            "heard_foreign_traffic": len(w.frames) > 0,
        }
        unknown.update(other)
        print("  %-7s %d frames, %d distinct IDs" % (w.name_, len(w.frames), len(ids)))
        for k in sorted(ids):
            print("      0x%03X  x%d%s" % (k, ids[k],
                                           "" if k in FOREIGN_IDS else "   <-- UNKNOWN"))
    close_buses(witnesses)

    present = [n for n, r in result["per_witness"].items()
               if r["heard_foreign_traffic"]]
    absent = [n for n, r in result["per_witness"].items()
              if not r["heard_foreign_traffic"]]
    #
    # `heard_foreign` / `heard_nothing`, NOT on_segment / off_segment. Hearing
    # nothing is not evidence of being off the segment. Pre-2026-10-04 run JSONs
    # carry the old `on_segment` / `off_segment` keys for this same quantity and
    # are left as written.
    #
    result["heard_foreign"] = present
    result["heard_nothing"] = absent
    if absent:
        print("  %s heard none of the foreign traffic. That is NOT a segment"
              % ", ".join(absent))
        print("  verdict -- run the topology phase, which transmits from each")
        print("  adapter and needs no prior map, before concluding anything")
        print("  about which segment an adapter is on.")

    total = sum(r["frames"] for r in result["per_witness"].values())
    if total == 0:
        print("  SEGMENT QUIET -- nothing else is transmitting")
        print("  NOTE: with no traffic at all, this phase cannot tell a witness")
        print("  that is off the segment from one on a silent segment.")
        result["quiet"] = True
        result["usable"] = True
    elif unknown:
        print("  SEGMENT CARRIES TRAFFIC THIS SCRIPT CANNOT ACCOUNT FOR.")
        print("  Identify it before measuring; every count below would include it.")
        result["quiet"] = False
        result["usable"] = False
    else:
        fps = max(r["foreign_fps"] for r in result["per_witness"].values())
        print("  SEGMENT NOT IDLE, and the traffic is accounted for: %.2f frames/s"
              % fps)
        print("  of the two benched boards' diag IDs. Against the ~2350 frames/s")
        print("  this replays that is %.2f %% of the load, so it does not move a"
              % (100.0 * fps / 2350.0))
        print("  rate or an occupancy figure. It is excluded from the paired")
        print("  analysis by ID and counted separately as a witness reference.")
        if absent:
            print("  NOT ON THIS SEGMENT: %s -- so there is no second witness to"
                  % ", ".join(absent))
            print("  cross-check a shortfall against. The foreign count and the")
            print("  speed sweep do that job instead, less strongly.")
        result["quiet"] = False
        result["usable"] = True

    out["quiet"] = result
    return result["usable"]


# ------------------------------------------------------------- topology ------

def phase_topology(out):
    """Which adapters are actually on one wire, and is anything bridging them?

    ASKED BECAUSE THE QUIET AND AIRTIME PHASES DISAGREED. The IXXAT saw none of
    the interposer's 1 Hz burst that the PCAN and Kvaser both saw, and then saw
    every one of 300 frames the PCAN transmitted. Two topologies explain that
    equally well and they are not equivalent:

      H1  All three sit on one segment, and the IXXAT is not accepting
          0x7F4-0x7F7 -- python-can's IXXAT backend only calls
          canControlSetAccFilter when can_filters is passed, so with none it
          inherits whatever the device was last configured with.
      H2  The PCAN and Kvaser are on one segment and the IXXAT is on a second,
          with the charge interposer bridging between them. The IXXAT would then
          see the PCAN's frames only as the bridge re-transmits them, and its
          inter-frame gaps would be the bridge flushing its queue back to back
          -- which matches CAN frame theory just as exactly as a direct
          observation would, so the airtime agreement cannot separate them.

    THE AIR-TIME RESULT SURVIVES EITHER WAY: back-to-back frames of a given DLC
    occupy the same time on any 500 kbit segment, so measuring the bridge's
    output measures frame air time just as well. What does not survive is
    treating the IXXAT as a witness OF THIS SEGMENT, which is what the replay
    phase needs it for.

    REACHABILITY CANNOT SEPARATE H1 FROM H2, AND AN EARLIER VERSION OF THIS
    DOCSTRING CLAIMED IT COULD. It said "a bridge shows up as asymmetry, or as a
    segment whose traffic does not cross". A TRANSPARENT BIDIRECTIONAL BRIDGE
    shows up as NEITHER -- it forwards both ways, so the matrix is symmetric and
    the traffic does cross, which is precisely what one wire produces. All this
    phase can therefore establish is that every pair reached every other pair
    SOMEHOW, which is why its result is now called `mutually_reachable` and not
    `one_segment`.

    IT HAS ALREADY MISLED ONCE. On 2026-09-26 the S3 was running the interposer
    BRIDGE image (our slcan image went on 09-29), so every test frame crossed and
    this phase recorded all three pairs 20/20. On 2026-10-04 that reading was
    taken as settling the bench map and argued against the interposer session --
    wrongly, while the H1/H2 block above had already said the two were not
    equivalent.

    WHAT ACTUALLY SEPARATES THEM, neither of which this phase measures:
      - ORIGINATION ASYMMETRY. The interposer ORIGINATES 0x7F4-0x7F7 on its
        vehicle port (charge-interposer-spec.md:683) rather than forwarding them,
        so they never cross to the charger side even with the bridge up. An
        adapter that hears none of them WHILE DEMONSTRABLY ALIVE -- this phase
        proves liveness, which is its real contribution -- is not on the vehicle
        segment. A bridge cannot manufacture that asymmetry; that is exactly why
        it discriminates where reachability does not.
      - THE BRIDGE DOWN. Probe pair-wise with the bridging board not bridging and
        require 0 frames each way. That is what settles placement on the day.

    So read this phase as liveness plus "something connects these", and take the
    segment question to one of the two tests above.
    """
    print("\n== phase topology: which adapters share a wire? ==")
    names = ("pcan", "kvaser", "ixxat")
    # 0x7FA-0x7FC: above both boards' diag block (0x7F1-0x7F8), below the
    # airtime IDs (0x7FE/0x7FF), and far above the stimulus ceiling of 0x6A0.
    TAG = {"pcan": 0x7FA, "kvaser": 0x7FB, "ixxat": 0x7FC}
    BURST = 20

    #
    # THE IXXAT IS NO LONGER ON THIS BENCH. The user removed it on 2026-10-04 and
    # put the Innomaker in its place, so the hard-coded third adapter (channel 0,
    # unique_hardware_id HW630227) would now raise from deep inside python-can.
    # Check first and say what is wrong, because "the topology phase crashed" and
    # "there is no third adapter" are different problems.
    #
    # Deliberately NOT auto-substituting the Innomaker. It is a gs_usb device
    # needing the libusb bootstrap, and -- more to the point -- its segment is
    # unconfirmed, so using it as the third leg of a topology matrix would be
    # assuming the very thing the matrix is meant to inform.
    #
    present = set()
    for cfg in can.detect_available_configs(["pcan", "kvaser", "ixxat"]):
        if "Virtual" not in str(cfg.get("device_name", "")):
            present.add(cfg["interface"])
    absent = [n for n in names if n not in present]
    if absent:
        print("  CANNOT RUN: no %s on this bench (detected: %s)."
              % (", ".join(absent), ", ".join(sorted(present)) or "nothing"))
        print("  The IXXAT was removed 2026-10-04 and replaced by the Innomaker,")
        print("  which is a gs_usb device and whose segment is NOT yet confirmed.")
        print("  Adding it here as the third leg would assume what this phase is")
        print("  meant to inform. Bring up the Innomaker deliberately, or run the")
        print("  bridge-down 0/0 probe instead -- see the docstring.")
        out["topology"] = {"skipped": "missing adapters: %s" % ", ".join(absent)}
        return None

    buses = {}
    # PCAN FIRST, as everywhere else in this file.
    buses["pcan"] = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=BITRATE)
    buses["kvaser"] = can.Bus(interface="kvaser", channel=0, bitrate=BITRATE)
    buses["ixxat"] = can.Bus(interface="ixxat", channel=0,
                             unique_hardware_id="HW630227", bitrate=BITRATE)
    time.sleep(2.0)
    for n in names:
        while buses[n].recv(timeout=0.0) is not None:
            pass

    matrix = {}
    for tx_name in names:
        ws = [Witness(n, buses[n]) for n in names if n != tx_name]
        for w in ws:
            w.start()
        time.sleep(0.3)
        msg = can.Message(arbitration_id=TAG[tx_name], is_extended_id=False,
                          data=bytes([0x5A] * 8))
        sent = 0
        fails = 0
        for _ in range(BURST):
            try:
                buses[tx_name].send(msg, timeout=1.0)
                sent += 1
            except can.CanError:
                fails += 1
            time.sleep(0.01)
        time.sleep(0.5)
        for w in ws:
            w.stop.set()
        for w in ws:
            w.join(timeout=2.0)
        row = {"sent": sent, "refused": fails, "heard_by": {}}
        for w in ws:
            row["heard_by"][w.name_] = sum(1 for f in w.frames
                                           if f[1] == TAG[tx_name] and not f[3])
        matrix[tx_name] = row
        print("  %-7s sent %d (refused %d) -> %s"
              % (tx_name, sent, fails,
                 ", ".join("%s heard %d" % (k, v) for k, v in row["heard_by"].items())))
        time.sleep(0.3)

    close_buses(list(buses.items()))

    # A pair is on one wire when traffic crosses in BOTH directions. One-way
    # reachability is a bridge, not a shared segment.
    pairs = {}
    for a in names:
        for b in names:
            if a >= b:
                continue
            ab = matrix[a]["heard_by"].get(b, 0)
            ba = matrix[b]["heard_by"].get(a, 0)
            pairs["%s<->%s" % (a, b)] = {
                "a_to_b": ab, "b_to_a": ba,
                "both_ways": ab > 0 and ba > 0,
            }
    print("\n  pair                a->b   b->a   verdict")
    for k, v in pairs.items():
        print("  %-18s %5d  %5d   %s"
              % (k, v["a_to_b"], v["b_to_a"],
                 "same wire" if v["both_ways"]
                 else ("one way only -- A BRIDGE, NOT A SEGMENT"
                       if (v["a_to_b"] or v["b_to_a"]) else "no path")))
    all_shared = all(v["both_ways"] for v in pairs.values())
    if all_shared:
        print("\n  EVERY PAIR IS MUTUALLY REACHABLE, AND THAT DOES NOT MEAN ONE")
        print("  WIRE. A transparent bidirectional bridge produces this exact")
        print("  result, so H1 and H2 are BOTH still open on this evidence. What")
        print("  is established is that all three adapters are alive and")
        print("  something connects them -- which is what the replay phase needs")
        print("  to know about liveness, and is NOT a segment verdict.")
        print("  To settle the segment: either use origination asymmetry (an")
        print("  adapter that hears none of the interposer's vehicle-only")
        print("  0x7F4-0x7F7 while alive is not vehicle-side), or probe")
        print("  pair-wise with the bridging board NOT bridging and require 0")
        print("  frames each way.")
    else:
        print("\n  NOT MUTUALLY REACHABLE. At least one adapter is isolated or")
        print("  dead. An adapter that reaches nothing cannot witness this wire;")
        print("  treat its airtime figures as frame air time only (still valid)")
        print("  and not as an observation of the PCAN's transmissions.")
    #
    # `mutually_reachable`, NOT `one_segment`. Renamed 2026-10-04 after the old
    # name was read as a segment verdict and argued from. Pre-2026-10-04 run
    # JSONs carry `one_segment` for this same quantity and are left as written.
    #
    out["topology"] = {"matrix": matrix, "pairs": pairs,
                       "mutually_reachable": all_shared}
    return all_shared


# -------------------------------------------------------------- airtime ------

def phase_airtime(out):
    print("\n== phase airtime: frame time at the wire, per DLC ==")
    tx, witnesses = open_buses(need_tx=True)
    ws = [Witness(n, b) for n, b in witnesses]
    for w in ws:
        w.start()

    # 0x7FE/0x7FF: the two lowest-priority standard IDs on the segment, chosen
    # so this phase cannot be mistaken for any real message. The bursts are
    # tagged by ID so the two payload arms can be told apart at the witness.
    ARMS = [("max_stuff", 0x7FE, 0x00), ("min_stuff", 0x7FF, 0xAA)]
    BURST = 300
    sent = {}
    for arm, arb, fill in ARMS:
        for dlc in (4, 5, 6, 7, 8):
            msg = can.Message(arbitration_id=arb, is_extended_id=False,
                              data=bytes([fill] * dlc))
            n = 0
            fails = 0
            # No pacing at all: queue them as fast as the driver accepts, so
            # the controller transmits back to back and consecutive witness
            # gaps are frame times rather than host scheduling.
            for _ in range(BURST):
                try:
                    tx.send(msg, timeout=1.0)
                    n += 1
                except can.CanError:
                    fails += 1
            sent[(arm, dlc)] = (n, fails)
            time.sleep(0.25)   # let the burst clear before the next DLC
            print("  %-10s dlc %d: queued %d, refused %d" % (arm, dlc, n, fails))

    time.sleep(0.5)
    for w in ws:
        w.stop.set()
    for w in ws:
        w.join(timeout=2.0)

    result = {"burst": BURST, "e4_air_time_us": E4_AIR_TIME_US, "per_witness": {}}
    for w in ws:
        per = {}
        for arm, arb, fill in ARMS:
            for dlc in (4, 5, 6, 7, 8):
                ts = [f[0] for f in w.frames
                      if f[1] == arb and len(f[2]) == dlc and not f[3]]
                gaps = [(ts[k + 1] - ts[k]) * 1e6 for k in range(len(ts) - 1)]
                # Only gaps inside a burst mean anything; the 0.25 s pause
                # between DLCs shows up as one enormous gap per arm.
                gaps = [g for g in gaps if g < 5000.0]
                per["%s_dlc%d" % (arm, dlc)] = {
                    "frames": len(ts),
                    "queued": sent[(arm, dlc)][0],
                    "min_us": round(min(gaps), 1) if gaps else None,
                    "p5_us": round(pct(gaps, 5), 1) if gaps else None,
                    "p50_us": round(pct(gaps, 50), 1) if gaps else None,
                }
        result["per_witness"][w.name_] = per

        print("\n  -- %s --" % w.name_)
        print("  %-10s %-4s %7s %7s %8s %8s %9s" %
              ("arm", "dlc", "frames", "min us", "p5 us", "p50 us", "E4 says"))
        for arm, arb, fill in ARMS:
            for dlc in (4, 5, 6, 7, 8):
                r = per["%s_dlc%d" % (arm, dlc)]
                print("  %-10s %-4d %7d %7s %8s %8s %9d" %
                      (arm, dlc, r["frames"], r["min_us"], r["p5_us"],
                       r["p50_us"], E4_AIR_TIME_US[dlc]))
    close_buses([("pcan", tx)] + witnesses)
    out["airtime"] = result


# --------------------------------------------------------------- replay ------

def load_window(seconds):
    """The intended schedule: offsets, IDs and payloads from the capture itself.

    THE EXTENDED FLAG IS PART OF THE FRAME, not a detail. 4.8 % of this window
    is 29-bit -- twelve J1939 IDs such as 0x18FECAB3 -- and a 29-bit
    arbitration field is 20 bits longer than an 11-bit one, about 40 us more air
    time each at 500 kbit. A first version of this function dropped the flag and
    re-sent them as standard IDs; the filter check caught it because both
    witnesses missed exactly the same twelve IDs, which is a transmitter fault
    and not a receiver filter.
    """
    frames = []
    t0 = None
    reader = can.LogReader(CAPTURE)
    for m in reader:
        if t0 is None:
            t0 = m.timestamp
        off = m.timestamp - t0
        if off < WINDOW_START:
            continue
        if off >= WINDOW_START + seconds:
            break
        frames.append((off - WINDOW_START, m.arbitration_id, bytes(m.data),
                       bool(m.is_extended_id)))
    return frames


def phase_replay(seconds, speeds, out, foreign_fps, heard_foreign):
    print("\n== phase replay: truck-rate fidelity ==")
    frames = load_window(seconds)
    span = frames[-1][0] - frames[0][0]
    print("  stimulus %s" % os.path.basename(CAPTURE))
    print("  window %.1f-%.1f s, %d frames, %.1f frames/s intended"
          % (WINDOW_START, WINDOW_START + seconds, len(frames), len(frames) / span))
    print("  (E4 models %.0f frames/s)" % E4_TRUCK_FPS)

    # Built once, outside the timing loop. Constructing a can.Message costs
    # tens of microseconds and the mean gap at truck rate is ~425 us, so
    # building inside the loop would charge the rig for the script's own cost.
    msgs = [can.Message(arbitration_id=i, is_extended_id=x, data=d)
            for _, i, d, x in frames]
    offs = [f[0] for f in frames]
    n_ext = sum(1 for f in frames if f[3])
    print("  %d of %d frames are 29-bit extended (%.1f %%)"
          % (n_ext, len(frames), 100.0 * n_ext / len(frames)))

    tx, witnesses = open_buses(need_tx=True)

    # DOES EACH WITNESS ACCEPT EVERY ID THE STIMULUS CONTAINS?
    #
    # An inherited acceptance filter is ONE explanation for a witness missing
    # the interposer's 0x7F4-0x7F7 -- it was called "the only" one here on the
    # strength of the topology phase having "settled that the IXXAT shares this
    # wire". It had not: that phase shows mutual reachability, which a bridge
    # also produces, and the competing hypothesis (a second segment) turned out
    # to be the true one. The filter check below is worth doing regardless, so
    # only the false premise is removed. Mechanism, which still holds:
    # python-can's IXXAT backend calls
    # canControlSetAccFilter only when can_filters is passed, so an unfiltered
    # open inherits whatever the device was last set to. A filter that drops
    # some of the 86 stimulus IDs would show up below as frame loss and would be
    # indistinguishable from it, so it is established here instead -- one frame
    # of each ID, paced slowly enough that nothing can be lost to queueing, and
    # the check is on the SET of IDs seen rather than on a count.
    # Keyed on (id, extended): a 29-bit 0x123 and an 11-bit 0x123 are different
    # frames and a witness can be filtered from one and not the other.
    ids_needed = sorted(set((f[1], f[3]) for f in frames))
    print("  filter check: one frame of each of %d stimulus IDs" % len(ids_needed))
    ws = [Witness(n, b) for n, b in witnesses]
    for w in ws:
        w.start()
    time.sleep(0.3)
    for arb, ext in ids_needed:
        tx.send(can.Message(arbitration_id=arb, is_extended_id=ext,
                            data=bytes(8)), timeout=1.0)
        time.sleep(0.004)
    time.sleep(0.5)
    for w in ws:
        w.stop.set()
    for w in ws:
        w.join(timeout=2.0)

    filt = {}
    blind = {}
    on_seg = []
    for w in ws:
        seen = set((f[1], f[4]) for f in w.frames
                   if not f[3] and not (f[1] in FOREIGN_IDS and not f[4]))
        missing = [a for a in ids_needed if a not in seen]
        filt[w.name_] = {"ids_expected": len(ids_needed), "ids_seen": len(seen),
                         "ids_missing": ["0x%X%s" % (a, "x" if x else "")
                                         for a, x in missing]}
        blind[w.name_] = set(missing)
        # ON-SEGMENT IS DECIDED HERE, not from the foreign traffic in the quiet
        # phase. The IXXAT inherits an acceptance filter that excludes the
        # interposer's diag IDs, so judging presence by whether a witness hears
        # the interposer called it absent while the topology phase proved it is
        # on the wire. Hearing any of the stimulus is the direct test.
        if len(seen) > 0:
            on_seg.append(w.name_)
        print("    %-7s saw %d of %d IDs%s"
              % (w.name_, len(seen), len(ids_needed),
                 "" if not missing else "  MISSING %s"
                 % " ".join("0x%X%s" % (a, "x" if x else "") for a, x in missing[:12])))
    if any(blind.values()):
        print("    A WITNESS IS BLIND TO PART OF THE STIMULUS. Those IDs are")
        print("    excluded from its counts below -- otherwise a filter would")
        print("    be read as loss on the wire.")
    if sorted(on_seg) != sorted(heard_foreign):
        #
        # The filter check is the better evidence and this is why: it is ACTIVE,
        # against our own stimulus at full rate, where the quiet phase judged on
        # a 4 fps third-party source over five seconds.
        #
        print("    witness set revised from the filter check: %s"
              % ", ".join(on_seg))
        print("    (the quiet phase said %s, judging only by whether each"
              % (", ".join(heard_foreign) if heard_foreign else "nothing"))
        print("    adapter heard the interposer's ~4 fps traffic)")
    heard_foreign = on_seg
    on_segment = heard_foreign

    result = {"capture": os.path.basename(CAPTURE), "window_start": WINDOW_START,
              "filter_check": filt,
              "seconds": seconds, "frames": len(frames),
              "intended_fps": round(len(frames) / span, 1),
              "e4_truck_fps": E4_TRUCK_FPS, "arms": {}}

    for speed in speeds:
        print("\n  -- speed %.2fx (%.0f frames/s intended) --"
              % (speed, len(frames) / span * speed))
        ws = [Witness(n, b) for n, b in witnesses]
        for w in ws:
            w.start()
        time.sleep(0.3)

        actual = []
        fails = 0
        perf = time.perf_counter
        base = perf()
        for k in range(len(msgs)):
            due = base + offs[k] / speed
            # Spin, do not sleep. time.sleep() on Windows rounds up to the
            # system timer -- 1 ms at best, 15.6 ms by default -- and the mean
            # gap here is 425 us, so sleeping would manufacture exactly the
            # scheduling error this phase exists to measure.
            while perf() < due:
                pass
            try:
                tx.send(msgs[k], timeout=0.5)
                actual.append(perf() - base)
            except can.CanError:
                fails += 1
                actual.append(None)
        elapsed = perf() - base

        time.sleep(0.5)
        for w in ws:
            w.stop.set()
        for w in ws:
            w.join(timeout=2.0)

        arm = {"speed": speed, "queued": len(msgs) - fails, "refused": fails,
               "elapsed_s": round(elapsed, 3),
               "send_fps": round((len(msgs) - fails) / elapsed, 1),
               "witnesses": {}}

        # How far the transmitter's own send calls fell from the intended
        # schedule. This is the rig's error, before the wire.
        err = [(a - o / speed) * 1e6 for a, o in zip(actual, offs) if a is not None]
        arm["send_error_us"] = {
            "p50": round(pct(err, 50), 1), "p95": round(pct(err, 95), 1),
            "p99": round(pct(err, 99), 1), "max": round(max(err), 1),
        }
        print("     queued %d, refused %d in %.3f s -> %.1f frames/s"
              % (arm["queued"], fails, elapsed, arm["send_fps"]))
        print("     send error vs intended: p50 %.0f us, p95 %.0f, p99 %.0f, max %.0f"
              % (arm["send_error_us"]["p50"], arm["send_error_us"]["p95"],
                 arm["send_error_us"]["p99"], arm["send_error_us"]["max"]))

        for w in ws:
            errf = [f for f in w.frames if f[3]]
            # Split by ID, not by guesswork: the stimulus tops out at 0x6A0 and
            # every FOREIGN_IDS entry is above 0x7F0.
            # Both benched boards' diag IDs are 11-bit, so an extended frame is
            # never foreign however its low bits read.
            got = [f for f in w.frames
                   if not f[3] and not (f[1] in FOREIGN_IDS and not f[4])]
            fgn = [f for f in w.frames
                   if not f[3] and f[1] in FOREIGN_IDS and not f[4]]
            wr = {"frames": len(got), "error_frames": len(errf),
                  "foreign_frames": len(fgn)}
            # The witness reference. The interposer's burst is externally paced
            # at 1 Hz and does not care how loaded the segment is, so the share
            # of it this witness kept is a measure of the witness, not the wire.
            exp = foreign_fps.get(w.name_, 0.0) * elapsed
            wr["foreign_expected"] = round(exp, 1)
            wr["foreign_kept"] = (round(len(fgn) / exp, 3) if exp > 0 else None)
            if got:
                wspan = got[-1][0] - got[0][0]
                wr["landed_fps"] = round(len(got) / wspan, 1) if wspan > 0 else None
                gaps = [(got[k + 1][0] - got[k][0]) * 1e6 for k in range(len(got) - 1)]
                wr["gap_us"] = {
                    "min": round(min(gaps), 1), "p5": round(pct(gaps, 5), 1),
                    "p50": round(pct(gaps, 50), 1), "p95": round(pct(gaps, 95), 1),
                    "max": round(max(gaps), 1),
                }
                # A gap below one DLC-0 frame time is physically impossible at
                # 500 kbit and is the signature spec 12.2 asks about: frames
                # arriving collapsed together rather than spread out.
                wr["gaps_under_100us"] = sum(1 for g in gaps if g < 100.0)

                # BUS OCCUPANCY -- the E4 input this phase exists to supply.
                # Computed from the frames this witness actually saw, using the
                # bit counts the airtime phase validated, so it is a measurement
                # rather than a model of a model. Reported as a band because
                # stuffing depends on payload: the floor assumes none, the
                # ceiling the worst case.
                lo = hi = 0
                for f in got:
                    b, bmax = frame_bits(len(f[2]), f[4])
                    lo += b
                    hi += bmax
                bit_us = 1e6 / BITRATE
                wr["occupancy_pct"] = {
                    "floor": round(100.0 * lo * bit_us / (wspan * 1e6), 1),
                    "ceiling": round(100.0 * hi * bit_us / (wspan * 1e6), 1),
                }
                # What E4 implies for comparison: its own DLC-8 air time (the
                # load scenarios' frames) at the rate spec 9.3 states.
                wr["occupancy_pct"]["e4_implied"] = round(
                    100.0 * E4_TRUCK_FPS * E4_AIR_TIME_US[8] / 1e6, 1)
                # Order is preserved by a single transmitter, so the k-th
                # landed frame should be the k-th sent. Mismatches mean frames
                # were lost, reordered, or something else was transmitting.
                #
                # Compared against what THIS witness could have seen, not
                # against everything sent: an ID it is filtered from would
                # otherwise offset the whole sequence by one and report every
                # later frame as a mismatch.
                expect = [m for m in msgs
                          if (m.arbitration_id, m.is_extended_id)
                          not in blind[w.name_]]
                wr["expected"] = len(expect)
                pairs = min(len(got), len(expect))
                wr["order_mismatches"] = 0
                wr["first_mismatch"] = None
                for k in range(pairs):
                    if (got[k][1] != expect[k].arbitration_id
                            or got[k][4] != expect[k].is_extended_id
                            or got[k][2] != bytes(expect[k].data)):
                        wr["order_mismatches"] += 1
                        if wr["first_mismatch"] is None:
                            wr["first_mismatch"] = k
            arm["witnesses"][w.name_] = wr
            if w.name_ not in on_segment:
                print("     %-7s not on this segment, skipped" % w.name_)
                continue
            print("     %-7s landed %d of %d it could see (%d queued), "
                  "%s frames/s, err frames %d"
                  % (w.name_, wr["frames"], wr.get("expected", 0), arm["queued"],
                     wr.get("landed_fps"), wr["error_frames"]))
            print("             gap p50 %s us, min %s, sub-100us %s, "
                  "order mismatches %s (first at %s)"
                  % (wr.get("gap_us", {}).get("p50"), wr.get("gap_us", {}).get("min"),
                     wr.get("gaps_under_100us"), wr.get("order_mismatches"),
                     wr.get("first_mismatch")))
            occ = wr.get("occupancy_pct")
            if occ:
                print("             bus occupancy %.1f-%.1f %% of the wire "
                      "(E4 implies %.1f %%)"
                      % (occ["floor"], occ["ceiling"], occ["e4_implied"]))
            print("             foreign reference: kept %d of ~%.0f expected (%s)"
                  % (wr["foreign_frames"], wr["foreign_expected"],
                     "%.1f %%" % (100.0 * wr["foreign_kept"])
                     if wr["foreign_kept"] is not None else "no reference"))

        live = dict((n, v) for n, v in arm["witnesses"].items() if n in on_segment)
        counts = sorted(set(v["frames"] for v in live.values()))
        arm["witnesses_agree"] = len(counts) == 1
        arm["witness_count"] = len(live)
        if len(live) < 2:
            print("     ONE WITNESS ONLY. A shortfall against %d queued cannot be"
                  % arm["queued"])
            print("     localised to the wire or to this witness's queue by")
            print("     comparison; read the foreign reference and the speed")
            print("     sweep for that, and treat a single arm as suggestive.")
        elif arm["witnesses_agree"]:
            print("     witnesses AGREE at %d frames -- the shortfall against"
                  % counts[0])
            print("     %d queued is the transmitter's, not a witness queue"
                  % arm["queued"])
        else:
            print("     witnesses DISAGREE %s -- the loss is in whichever is"
                  % counts)
            print("     short, not on the wire; the higher count is the floor")
        result["arms"]["%.2fx" % speed] = arm
        time.sleep(1.0)

    close_buses([("pcan", tx)] + witnesses)
    out["replay"] = result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=("quiet", "topology", "airtime", "replay", "all"))
    ap.add_argument("--seconds", type=float, default=30.0,
                    help="length of the replay window")
    ap.add_argument("--quiet-seconds", type=float, default=5.0)
    ap.add_argument("--speeds", default="0.25,0.5,1.0")
    ap.add_argument("--force", action="store_true",
                    help="run the measuring phases even if the segment is not "
                         "quiet. Records that it was not.")
    ap.add_argument("--out", default=os.path.join(RUNS, "e4_rig_calibration.json"))
    args = ap.parse_args()

    speeds = [float(s) for s in args.speeds.split(",")]
    out = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "host": os.environ.get("COMPUTERNAME", "?"),
           "bitrate": BITRATE, "phase": args.phase}

    # The quiet phase is not optional even when only one measuring phase is
    # wanted: it is what establishes which adapters are on this segment and what
    # the foreign rate is, and both are inputs to the analysis below.
    usable = phase_quiet(args.quiet_seconds, out)
    foreign_fps = dict((n, r["foreign_fps"])
                       for n, r in out["quiet"]["per_witness"].items())
    heard_foreign = out["quiet"]["heard_foreign"]
    if not usable and not args.force:
        print("\nSTOPPING: the segment carries traffic this script cannot")
        print("account for, so nothing measured below would be about the rig")
        print("alone. Re-run with --force to measure anyway; the output records")
        print("that the segment was not clean.")
        args.phase = "quiet"
    out["segment_usable"] = usable

    if args.phase in ("topology", "all"):
        phase_topology(out)
    if args.phase in ("airtime", "all"):
        phase_airtime(out)
    if args.phase in ("replay", "all"):
        phase_replay(args.seconds, speeds, out, foreign_fps, heard_foreign)

    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
