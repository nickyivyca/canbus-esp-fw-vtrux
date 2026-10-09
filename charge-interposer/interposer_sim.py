"""The interposer board, as a process.

Bridges the vehicle segment and the charger segment, running machine.py over
every frame. This is the stand-in for the real board: when the hardware exists,
kill this process, wire the board in its place, and the other two simulators do
not change.

    vehicle_sim.py  ==[ vehicle segment ]== interposer_sim.py ==[ charger segment ]== charger_sim.py

Nothing here makes decisions. All of the logic lives in machine.py, which is
pure and has no imports; this file only does I/O, echo suppression and logging.
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import can

import bus as busmod
import machine as M

log = logging.getLogger("intp")

DRAIN_LIMIT = 512       # frames retired per port per loop pass


def main():
    busmod.install_break_handler()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    busmod.add_bus_args(ap, default_channel=busmod.VEHICLE_SEGMENT,
                        prefix="vehicle-", help_role="the vehicle segment")
    busmod.add_bus_args(ap, default_channel=busmod.CHARGER_SEGMENT,
                        prefix="charger-", help_role="the charger segment")
    ap.add_argument("--time-scale", type=float, default=20.0,
                    help="must match the simulators. The core is clocked in "
                         "SIMULATED time, so its thresholds stay in vehicle "
                         "time no matter how fast the bench runs.")
    ap.add_argument("--bypass", action="store_true",
                    help="forward everything unmodified -- the control run. "
                         "Proves the bench works without the state machine.")
    ap.add_argument("--stealth", action="store_true",
                    help="also mask charger->vehicle telemetry while "
                         "overriding (UNVERIFIED: only enable if the bench "
                         "shows the VCU misbehaves without it)")
    ap.add_argument("--chgmax-full-a", type=float, default=None,
                    help="release threshold on bcm_chg_max (spec 5.1); "
                         "negative disables the release, for the hard-ceiling "
                         "negative test")
    ap.add_argument("--tick-interval-ms", type=float, default=10.0,
                    help="simulated ms between core.tick() calls. "
                         "Default 10 matches main.cpp's "
                         "TICK_INTERVAL_MS, so the bench resolves "
                         "debounces and the repeat burst on the same "
                         "cadence the board does. 0 ticks every loop "
                         "iteration, which is what this did before "
                         "2026-10-04; kept for comparing the two.")
    ap.add_argument("--trace-out", default=None,
                    help="record every frame and tick the core processed, "
                         "in the differential trace format, so the run can "
                         "be replayed through machine.cpp (spec 9.1 L1). "
                         "Ignored with --bypass, where the core does not "
                         "run. These files are large -- run_scenario.py "
                         "deletes them after a clean diff.")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s intp %(message)s", datefmt="%H:%M:%S")

    cfg = M.Config()
    if args.chgmax_full_a is not None:
        cfg.chgmax_full_ca = int(args.chgmax_full_a * 100)
    cfg.mask_charger_telemetry = args.stealth

    busmod.assert_segments_isolated(args)

    veh = busmod.open_bus(args, prefix="vehicle-",
                          default_channel=busmod.VEHICLE_SEGMENT, logger=log)
    chg = busmod.open_bus(args, prefix="charger-",
                          default_channel=busmod.CHARGER_SEGMENT, logger=log)

    # udp_multicast loops a socket's own frames back to it; the interposer
    # forwards everything, so without suppression each frame it emits echoes
    # back and is re-forwarded -- an infinite bridge loop. Same fix as the Coda
    # flash proxy. Real dongles honour receive_own_messages, so this is inert
    # on hardware.
    virtual = (args.vehicle_transport == "virtual"
               or args.charger_transport == "virtual")
    echo_v = busmod.EchoSuppressor() if virtual else None
    echo_c = busmod.EchoSuppressor() if virtual else None

    core = M.InterposerCore(cfg, now_ms=0)
    t0 = time.time()
    seen_events = 0

    # Spec 9.1 L1. `K` lines carry the Config overrides, because
    # host_runner builds with configDefaults() and would otherwise diverge
    # on a threshold rather than on a port bug.
    last_tick_ms = -1e12      # tick on the first pass
    trace = None
    if args.trace_out and not args.bypass:
        trace = open(args.trace_out, "w", newline="\n")
        if args.chgmax_full_a is not None:
            trace.write("K chgmax_full_ca %d\n" % cfg.chgmax_full_ca)
        if args.stealth:
            trace.write("K mask_charger_telemetry 1\n")

    def rec_frame(kind, msg, t_ms):
        if trace is not None:
            trace.write("%s %d %X %d %s\n"
                        % (kind, t_ms, msg.arbitration_id,
                           1 if msg.is_extended_id else 0,
                           bytes(msg.data).hex()))
    # Baseline for the spec 8.2 bridge_ok interval; a list so the nested
    # scope can move it, like `saturated`.
    prev_saturated = [0]
    last_diag_ms = -1000000
    last_diag_state = None
    saturated = [0]
    running = [True]

    def stop(_s, _f):
        running[0] = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    log.info("interposer up%s (vehicle=%s charger=%s)",
             "  [BYPASS]" if args.bypass else "",
             args.vehicle_channel or busmod.VEHICLE_SEGMENT,
             args.charger_channel or busmod.CHARGER_SEGMENT)

    def emit(side, arb, ext, data):
        msg = can.Message(arbitration_id=arb, is_extended_id=ext, data=data)
        if side == M.TO_CHARGER:
            chg.send(msg)
            if echo_c:
                echo_c.sent(msg)
        else:
            veh.send(msg)
            if echo_v:
                echo_v.sent(msg)

    last_nv, last_nc = [0], [0]
    frozen_s = 0.0
    # Bypass forwards frames straight through without driving the core,
    # so the core's own counters cannot describe it. [v->c, c->v].
    bypass_fwd = [0, 0]

    try:
        last_loop = time.perf_counter()
        while running[0]:
            # SIMULATED time. Every threshold in machine.py is expressed in
            # vehicle time (a 60 s arming delay means 60 s of charging; a 500 ms
            # staleness window means ten missed command frames), so an
            # accelerated bench has to scale the clock too. Feeding wall time
            # here silently divides every one of those thresholds by
            # --time-scale, which is how a 60 s arming guard became 3 s.
            # Freeze accounting BEFORE the clock is read: any loop gap beyond
            # bus.FREEZE_TOLERANCE_S is wall time the whole bench spent
            # suspended (see bus.py) and is subtracted from the clock, so a
            # frozen 60 ms does not become 600 simulated ms of "silence".
            _now = time.perf_counter()
            _gap = _now - last_loop
            last_loop = _now
            frozen_s += _gap - busmod.unfreeze(_gap)
            now_ms = int((time.time() - t0 - frozen_s) * 1000 * args.time_scale)
            did = False
            if _gap > 0.040:
                log.warning("STALL %.0f ms wall at sim t=%.0fs (loop gap; "
                            "%.0f ms subtracted from the clock; last pass "
                            "drained v=%d c=%d, saturated=%d)",
                            _gap * 1000.0, now_ms / 1000.0,
                            (_gap - busmod.unfreeze(_gap)) * 1000.0,
                            last_nv[0], last_nc[0], saturated[0])

            # Drain both ports, BOUNDED. An unbounded drain starves the other
            # port and the tick: under an accelerated --time-scale the inbound
            # rate can exceed what one Python loop retires, recv() then never
            # returns None, and the state machine stops being serviced at all.
            n_v = 0
            msg = veh.recv(timeout=0.0)
            while msg is not None and n_v < DRAIN_LIMIT:
                n_v += 1
                if not (echo_v and echo_v.is_echo(msg)):
                    did = True
                    if args.bypass:
                        # Counted HERE because the core is not driven in
                        # bypass: core.stats stays at zero and the final
                        # line would read "nothing crossed" while every
                        # frame crossed.
                        bypass_fwd[0] += 1
                        emit(M.TO_CHARGER, msg.arbitration_id,
                             msg.is_extended_id, bytes(msg.data))
                    else:
                        rec_frame("V", msg, now_ms)
                        for out in core.on_vehicle_frame(
                                msg.arbitration_id, msg.is_extended_id,
                                bytes(msg.data), now_ms):
                            emit(*out)
                msg = veh.recv(timeout=0.0)

            n_c = 0
            msg = chg.recv(timeout=0.0)
            while msg is not None and n_c < DRAIN_LIMIT:
                n_c += 1
                if not (echo_c and echo_c.is_echo(msg)):
                    did = True
                    if args.bypass:
                        bypass_fwd[1] += 1
                        emit(M.TO_VEHICLE, msg.arbitration_id,
                             msg.is_extended_id, bytes(msg.data))
                    else:
                        rec_frame("C", msg, now_ms)
                        for out in core.on_charger_frame(
                                msg.arbitration_id, msg.is_extended_id,
                                bytes(msg.data), now_ms):
                            emit(*out)
                msg = chg.recv(timeout=0.0)

            last_nv[0], last_nc[0] = n_v, n_c
            if n_v >= DRAIN_LIMIT or n_c >= DRAIN_LIMIT:
                saturated[0] += 1
                log.warning("DRAIN LIMIT hit at sim t=%.0fs: v=%d c=%d (limit %d)",
                            now_ms / 1000.0, n_v, n_c, DRAIN_LIMIT)
                if saturated[0] % 200 == 1:
                    log.warning("input saturated (v=%d c=%d per pass) -- the "
                                "bench cannot keep up at this --time-scale; "
                                "lower it or use the offline harness",
                                n_v, n_c)

            # Tick at the FIRMWARE's cadence (C3 gap 4). main.cpp ticks
            # every TICK_INTERVAL_MS = 10 ms; this used to tick once per
            # loop pass. The L1 differential cannot see the difference --
            # both cores replay the same recorded steps -- but the
            # closed-loop behaviour the scenarios judge can: the hold
            # evaluation, every debounce and the 50 ms repeat spacing are
            # resolved on ticks.
            if (not args.bypass
                    and now_ms - last_tick_ms >= args.tick_interval_ms):
                last_tick_ms = now_ms
                if trace is not None:
                    trace.write("T %d\n" % now_ms)
                for out in core.tick(now_ms):
                    emit(*out)

            if not args.bypass:
                while seen_events < len(core.events):
                    t_ms, state, text = core.events[seen_events]
                    log.info("[%s] %s", state, text)
                    seen_events += 1
                # Diagnostics (spec 8.2 / 8.3): all four status frames on
                # the vehicle side at 1 Hz of simulated time, plus 0x7F4
                # ALONE the moment the state changes, and the serial-style
                # `diag` line with the 1 Hz burst.
                #
                # B-4: this used to send all four on a state change too. The
                # firmware sends one, so the bench and the board disagreed on
                # the frame a scenario reads to find a state change -- the
                # bench is the thing that is supposed to stand in for the
                # board, so it follows the board.
                periodic = now_ms - last_diag_ms >= 1000
                changed = core.state != last_diag_state
                if periodic or changed:
                    last_diag_state = core.state
                    ovf = min(255, saturated[0])
                    # Spec 8.2: bridge_ok is health SINCE THE PREVIOUS STATUS
                    # FRAME. This passed `saturated[0] == 0`, which is "since
                    # start" -- one saturated pass in the first minute left
                    # the flag clear for the rest of the run, so the bench
                    # and the board disagreed about the one flag a log is
                    # read for. The sim follows the board, as with B-4.
                    bridge_ok = (saturated[0] == prev_saturated[0])
                    prev_saturated[0] = saturated[0]
                    frames = core.diag_frames(
                        now_ms, ovf, 0, now_ms, bridge_ok, False, 0)
                    if periodic:
                        last_diag_ms = now_ms
                    else:
                        frames = frames[:1]      # 0x7F4 only
                    for fid, data in frames:
                        emit(M.TO_VEHICLE, fid, False, data)
                    if periodic:
                        log.info(core.diag_line(now_ms, ovf, 0))

            if not did:
                time.sleep(0.0005)
    finally:
        if args.bypass:
            # LABELLED, and from the harness's own counters. The previous
            # line printed core.stats here, which in bypass are an idle
            # core's zeros: `fwd v->c=0 c->v=0 modified=0 synth=0` read as
            # "nothing crossed" on runs where everything crossed
            # untouched. Review item, 2026-10-05.
            log.info("final state=%s  BYPASS, harness fwd v->c=%d c->v=%d "
                     "(the core is not driven in bypass, so it has no "
                     "modified/synth to report)",
                     M.STATE_NAMES[core.state], bypass_fwd[0], bypass_fwd[1])
        else:
            log.info("final state=%s  fwd v->c=%d c->v=%d modified=%d "
                     "synth=%d%s",
                     M.STATE_NAMES[core.state], core.stats["fwd_v2c"],
                     core.stats["fwd_c2v"], core.stats["modified"],
                     core.stats["synth"],
                     ("  trip=%s" % core.trip_reason)
                     if core.trip_reason else "")
        if trace is not None:
            trace.close()
            log.info("trace written: %s", args.trace_out)
        veh.shutdown()
        chg.shutdown()


if __name__ == "__main__":
    main()
