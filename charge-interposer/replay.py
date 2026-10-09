"""Replay real captures into the bench, at accelerated rates.

Three ways to use a log, cheapest first:

  offline   `replay_offline()` drives machine.py directly with no CAN at all.
            Deterministic, as fast as the file reads, and the right tool for
            regression: it can assert that every forwarded byte is identical to
            what the truck actually sent.

  half      `vehicle_sim.py --mode replay` puts the captured VCU + BMS frames on
            the vehicle segment. The interposer and charger_sim run live and
            react to genuine truck traffic. Use `--speed` to accelerate.

  tap       `replay_to_bus()` with BOTH id sets pushes both sides onto one
            segment so a listener sees the whole original conversation.

Bus selection is by CONTENT, never by channel number -- channel numbers vary
per capture with dongle insertion order. The powertrain bus is identified by its
anchor IDs (0x051, the BMS 0x4xx cluster and the J1939 charger family).

Large captures: run charge_cmd_filter.sh (in
projects/vtrux/notes/artifacts/) over the raw log first. It strips a multi-GB
capture down to the charge-control IDs in seconds, and the result replays here
unchanged.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
def _repo_root():
    """Walk up until we find the directory that holds `canre`.

    Counting `.parents[N]` is how this breaks -- AGENTS.md flags it, and it
    broke here anyway: from a file inside projects/vtrux/tools/interposer/,
    parents[3] is projects/, not the repo root. Searching for the marker cannot
    be off by one.
    """
    from pathlib import Path as _P
    here = _P(__file__).resolve()
    for d in here.parents:
        if (d / "canre").is_dir():
            return d
    return here.parents[-1]

REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import can

import machine as M

CMD_ID = 0x18EFC000

VEHICLE_IDS = {CMD_ID, 0x410, 0x411, 0x412, 0x420, 0x430, 0x440,
               0x050, 0x596, 0x600, 0x644, 0x649}
CHARGER_IDS = {0x18FFD4C0, 0x18FFD5C0, 0x18FFD6C0, 0x18FFD7C0,
               0x18FFD8C0, 0x18FFD9C0, 0x18FFDAC0, 0x18FFDBC0}

# content-based powertrain-bus anchors (see AGENTS.md: never use channel number)
PT_ANCHORS = {0x051, CMD_ID, 0x410, 0x420, 0x430, 0x440,
              0x18FFD4C0, 0x18FFD7C0}


def _frames(path):
    from canre.parsers import parse_file
    return parse_file(path)


def detect_pt_bus(path, sample=400000):
    """Pick the capture's powertrain bus by anchor-ID content."""
    counts = {}
    n = 0
    for fr in _frames(path):
        if fr.arbitration_id in PT_ANCHORS:
            counts[fr.bus] = counts.get(fr.bus, 0) + 1
        n += 1
        if n >= sample:
            break
    if not counts:
        return None
    return max(counts, key=counts.get)


def iter_pt(path, bus_filter=None, ids=None):
    """Yield (t_s, arb_id, is_extended, data) for the powertrain bus only."""
    if bus_filter is None:
        bus_filter = detect_pt_bus(path)
    t0 = None
    for fr in _frames(path):
        if bus_filter is not None and fr.bus != bus_filter:
            continue
        if ids is not None and fr.arbitration_id not in ids:
            continue
        if t0 is None:
            t0 = fr.timestamp
        yield (fr.timestamp - t0, fr.arbitration_id, fr.is_extended,
               bytes(fr.data))


def replay_to_bus(path, bus, ids, speed=1.0, bus_filter=None, logger=None,
                  start_s=0.0, end_s=None):
    """Push captured frames onto a live bus, preserving relative timing.

    speed = 1.0 real time, 60.0 sixty-times faster, 0 = as fast as possible.
    """
    if bus_filter is None:
        bus_filter = detect_pt_bus(path)
        if logger:
            logger.info("replay: powertrain bus detected as channel %s "
                        "(by anchor ID, not channel number)", bus_filter)
    wall0 = time.time()
    sent = 0
    for t, arb, ext, data in iter_pt(path, bus_filter, ids):
        if t < start_s:
            continue
        if end_s is not None and t > end_s:
            break
        if speed:
            due = wall0 + (t - start_s) / speed
            lag = due - time.time()
            if lag > 0:
                time.sleep(lag)
        bus.send(can.Message(arbitration_id=arb, is_extended_id=ext, data=data))
        sent += 1
        if logger and sent % 200000 == 0:
            logger.info("replay: %d frames, t=%.0fs", sent, t)
    if logger:
        logger.info("replay complete: %d frames, %.0f s of capture in %.1f s wall",
                    sent, t if sent else 0.0, time.time() - wall0)
    return sent


def replay_offline(path, core, bus_filter=None, progress=None,
                   start_s=0.0, end_s=None):
    """Drive an InterposerCore straight from a capture. No CAN, no timing.

    Returns a dict with the forwarded-stream comparison, which is what the
    regression scenarios assert on:

        frames_in        frames fed to the core
        to_charger       frames the core emitted toward the charger
        to_vehicle       frames the core emitted toward the vehicle
        modified         forwarded frames whose bytes differ from the input
        synthesized      frames the core originated (the release burst)
        first_modified   (t_s, hex_in, hex_out) of the first modification
        events           the core's own event log
    """
    if bus_filter is None:
        bus_filter = detect_pt_bus(path)
    known = VEHICLE_IDS | CHARGER_IDS
    res = dict(frames_in=0, to_charger=0, to_vehicle=0, modified=0,
               synthesized=0, first_modified=None, events=None,
               bus=bus_filter)
    last_tick_ms = -1
    for t, arb, ext, data in iter_pt(path, bus_filter, known):
        if t < start_s:
            continue
        if end_s is not None and t > end_s:
            break
        t_ms = int(t * 1000)
        res["frames_in"] += 1
        if arb in CHARGER_IDS:
            out = core.on_charger_frame(arb, ext, data, t_ms)
        else:
            out = core.on_vehicle_frame(arb, ext, data, t_ms)
        for side, oid, oext, odata in out:
            if side == M.TO_CHARGER:
                res["to_charger"] += 1
            else:
                res["to_vehicle"] += 1
            if oid == arb and odata != data:
                res["modified"] += 1
                if res["first_modified"] is None:
                    res["first_modified"] = (t, data.hex(" "), odata.hex(" "))
        # tick at most once per simulated 10 ms
        if t_ms - last_tick_ms >= 10:
            last_tick_ms = t_ms
            for side, oid, oext, odata in core.tick(t_ms):
                res["synthesized"] += 1
        if progress and res["frames_in"] % progress == 0:
            print("  ...%d frames, t=%.0fs, state=%s"
                  % (res["frames_in"], t, M.STATE_NAMES[core.state]))
    res["events"] = list(core.events)
    return res
