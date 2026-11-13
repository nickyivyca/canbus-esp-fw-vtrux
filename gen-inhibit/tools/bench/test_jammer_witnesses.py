#!/usr/bin/env python3
"""Harness test for the jammer pass's two witnesses on madhouse (the gen-inhibit reviewer's
ruling (C), 2026-11-13): witness_anchor.py, bench_witness.py, and x2_jammer.py's session,
can2_counts, parse_checks and the new argument refusals.

Synthetic inputs, no hardware, no lock: the PCAN and the Kvaser are fakes, sysfs and /proc are
folders under the worktree's scratch/, removed at the end. Every verdict has a case that must
come out the other way.

  anchor     a clean session anchors (offset = min(receipt - device), no frame mapped after its
             receipt); each VOID: spread p99 at and over 0.25 s, drift over 0.05 s, fewer than
             10 frames, stamps on the host epoch clock (COOKED), the device clock going back, two
             opens, a different device at the close; the bracket holds for a clean anchor and
             fails for the offset 0.3 s late and 0.3 s early
  pcan       PcanWitness's refusals (not a PCAN-USB adapter, a bus that is not a PCAN bus, another
             channel, ts_mode not 3, nom_bitrate not 500000), each with the bus it opened shut;
             its counting (bus-error frames apart from state frames and data frames); a read that
             raises is down(); same_device() after a replug
  socketcan  netdev_why: only `kvaser`, a CAN netdev with a kvaser_usb device, up; a vcan, another
             driver, another name, a link down, a non-CAN netdev refused. SocketcanWitness tags
             each frame with the phase it was read in and keeps the software stamp; ENETDOWN is
             down(). groups() and the gap's bounds; pcan_holders()
  can2       can2_counts on stamps of an arbitrary base (so nothing can judge a window on them):
             every 0x7A0, one group per pulse, PASS through judge_can2_isolation; a missing
             pulse, an unstamped bus error, a 0x7A0 lost -> INCONCLUSIVE; state frames and
             error frames in the PCAN phase are not groups
  usb        usb_record keeps EPROTO, -71, reset and disconnect lines on 1-3.2.x and not on
             1-3.4, 1-3.21 or without an error word; usb_fold VOIDs a check with a line in its
             window (a FAIL too), keeps NOT-RUN, and VOIDs when the kernel log is unreadable
  session    parse_checks and args_why; run_session on a fake console and a fake PCAN whose
             stamps are device time (an uptime base, drift, delivery lag): boot silence, pulses
             x3, the timeout with its control (which must FAIL) and no duration all PASS, each
             named with the PCAN witness, with a resource-log window; a stale frame inside a
             command's window VOIDs that check by the bracket; a made-up -71 journal line in one
             check's window VOIDs that check alone, an unreadable journal all; slow deliveries,
             the device clock going back and too few frames VOID them all; a witness down before
             a check is NOT-RUN, down at the end NOT-RUN for all

  py -3.13 -B gen-inhibit/tools/bench/test_jammer_witnesses.py
Gen-inhibit tester, 2026-11-13.
"""
import errno
import os
import socket
import struct
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import witness_anchor as WA            # noqa: E402
import bench_witness as BW             # noqa: E402
import x2_jammer as X                  # noqa: E402

BAD = []
UPTIME = 5000.0          # the fake device clock's base: device = wire - (epoch - UPTIME)


def check(group, label, ok, got=""):
    print("  %-4s %-9s %-66s %s" % ("ok" if ok else "FAIL", group, label[:66], str(got)[:200]))
    if not ok:
        BAD.append(label)


def lag(k):
    return 0.0005 + (k * 7 % 25) / 10000.0      # 0.5 .. 2.9 ms, deterministic


def pts_from_wire(wire, drift_ppm=0.0, epoch0=None, lags=None):
    """(device, receipt) pairs in receipt order for wire times (epoch)."""
    e0 = wire[0] if epoch0 is None else epoch0
    out = []
    for k, w in enumerate(wire):
        d = UPTIME + (w - e0) * (1.0 + drift_ppm * 1e-6)
        out.append((d, w + (lags[k] if lags else lag(k))))
    return sorted(out, key=lambda p: p[1])


# ------------------------------------------------------------------------ anchor

def anchor_cases():
    base = 1.7946e9
    wire = [base + k * 9.0 for k in range(100)]     # ~15 min
    anc = WA.anchor(pts_from_wire(wire))
    check("anchor", "clean session: anchored, no VOID", anc["void"] is None, anc["void"])
    pts = pts_from_wire(wire)
    check("anchor", "offset = min(receipt - device)",
          anc["offset_s"] == min(r - d for d, r in pts), anc["offset_s"])
    check("anchor", "no frame maps after its own receipt",
          all(WA.mapped(anc, d) <= r for d, r in pts), anc.get("spread_p50_s"))
    check("anchor", "mapped within 3 ms of the wire",
          all(abs(WA.mapped(anc, d) - w) < 0.003 for (d, _r), w in zip(pts_from_wire(wire), wire)), "")
    check("anchor", "the record states method, offset, slope, drift and spread",
          all(k in anc for k in ("method", "offset_s", "slope_ppm", "drift_s", "spread_p99_s", "bounds"))
          and "min(receipt - device)" in anc["method"], sorted(anc))
    check("anchor", "the bounds are fixed: spread 0.25 s, drift 0.05 s, 10 frames",
          (WA.SPREAD_P99_MAX_S, WA.DRIFT_MAX_S, WA.MIN_POINTS) == (0.25, 0.05, 10),
          (WA.SPREAD_P99_MAX_S, WA.DRIFT_MAX_S, WA.MIN_POINTS))
    # spread: p99 of (receipt - mapped)
    lags = [0.001] * 97 + [0.30] * 3
    a2 = WA.anchor(pts_from_wire(wire, lags=lags))
    check("anchor", "3 of 100 frames 0.3 s late: spread p99 over 0.25 s -> VOID",
          a2["void"] and "spread" in a2["void"], a2["void"])
    lags = [0.001] * 99 + [0.30]
    a2 = WA.anchor(pts_from_wire(wire, lags=lags))
    check("anchor", "control: 1 of 100 late, p99 not reached -> anchored", a2["void"] is None, a2["void"])
    lags = [0.001] * 92 + [0.2] * 8
    a2 = WA.anchor(pts_from_wire(wire, lags=lags))
    check("anchor", "8 slow deliveries at the session's end do not tilt the slope (envelope)",
          a2["void"] is None and abs(a2["slope_ppm"]) < 1.0, (a2["void"], a2.get("slope_ppm")))
    lags = [0.0] * 98 + [0.25, 0.25]
    a2 = WA.anchor(pts_from_wire([float(k * 9) + 1e5 for k in range(100)], lags=lags))
    check("anchor", "spread p99 exactly 0.25 s -> VOID (not under)",
          a2["void"] and "spread" in a2["void"], (a2["void"], a2.get("spread_p99_s")))
    # drift
    a2 = WA.anchor(pts_from_wire(wire, drift_ppm=100.0))
    check("anchor", "100 ppm over 15 min (0.09 s) -> VOID", a2["void"] and "drift" in a2["void"], a2["void"])
    a2 = WA.anchor(pts_from_wire(wire, drift_ppm=-20.0))
    check("anchor", "control: -20 ppm over 15 min (0.018 s) -> anchored", a2["void"] is None,
          (a2["void"], a2.get("drift_s")))
    # points
    check("anchor", "9 frames -> VOID", WA.anchor(pts_from_wire(wire[:9]))["void"] is not None, "")
    check("anchor", "control: 10 frames -> anchored", WA.anchor(pts_from_wire(wire[:10]))["void"] is None,
          WA.anchor(pts_from_wire(wire[:10]))["void"])
    # COOKED: device stamps on the host epoch clock
    cooked = [(w - 0.001, w + lag(k)) for k, w in enumerate(wire)]
    a2 = WA.anchor(cooked)
    check("anchor", "stamps on the epoch clock (COOKED) -> VOID", a2["void"] and "epoch" in a2["void"], a2["void"])
    a2 = WA.anchor([(w - 2.5, w + lag(k)) for k, w in enumerate(wire)])
    check("anchor", "control: stamps 2.5 s behind the epoch are a device clock", a2["void"] is None, a2["void"])
    # refusals
    pts = pts_from_wire(wire)
    pts[50] = (pts[50][0] - 3000.0, pts[50][1])
    a2 = WA.anchor(pts)
    check("anchor", "the device clock went back -> REFUSED", a2["void"] and a2["void"].startswith("REFUSED")
          and "back" in a2["void"], a2["void"])
    a2 = WA.anchor(pts_from_wire(wire), opens=2)
    check("anchor", "two opens in the session -> REFUSED", a2["void"] and "opened" in a2["void"], a2["void"])
    a2 = WA.anchor(pts_from_wire(wire), same_device=False)
    check("anchor", "another device at the close (replug) -> REFUSED",
          a2["void"] and "replugged" in a2["void"], a2["void"])
    # bracket
    send, done = base + 100.0, base + 103.0
    cmd = [base + 100.05 + k * 0.5 for k in range(5)]
    allw = wire + cmd
    pts = pts_from_wire(sorted(allw), epoch0=base)
    anc = WA.anchor(pts)
    nxt = base + 104.0
    br = WA.bracket(anc, pts, send, done, nxt)
    check("anchor", "bracket: the command's 5 frames inside", br["ok"] and br["n"] == 5, br)
    check("anchor", "bracket: margins recorded",
          br["margin_after_send_min_s"] > 0.04 and br["margin_before_receipt_min_s"] >= 0, br)
    late = dict(anc, offset_s=anc["offset_s"] + 0.3)
    check("anchor", "bracket: offset 0.3 s late -> frames outside",
          not WA.bracket(late, pts, send, done, nxt)["ok"], WA.bracket(late, pts, send, done, nxt)["n_outside"])
    early = dict(anc, offset_s=anc["offset_s"] - 0.3)
    check("anchor", "bracket: offset 0.3 s early -> the first frame before the send",
          not WA.bracket(early, pts, send, done, nxt)["ok"], WA.bracket(early, pts, send, done, nxt)["n_outside"])
    br = WA.bracket(anc, pts, base + 200.0, base + 201.0, base + 202.0)
    check("anchor", "bracket: frames received outside the window are not the command's", br["n"] == 0, br)
    # widened (reviewer, 2026-11-13): a frame received after the DONE, before the next send, is
    # the command's; on the wire after the DONE it is outside
    d_done = done - anc["offset_s"]
    extra = sorted(pts + [(d_done + 0.2, done + 0.2005)], key=lambda p: p[1])
    br = WA.bracket(anc, extra, send, done, nxt)
    check("anchor", "bracket: a frame received after the DONE, before the next send, counts and breaches",
          br["n"] == 6 and br["n_outside"] == 1, br)
    br = WA.bracket(anc, extra, send, None, nxt)
    check("anchor", "bracket: with no DONE only its own receipt bounds it", br["n"] == 6 and br["ok"]
          and br["margin_before_done_min_s"] is None, br)


# ------------------------------------------------------------------------ sysfs / proc fakes

SCRATCH = None


def scratch():
    global SCRATCH
    if SCRATCH is None:
        import gi_paths
        SCRATCH = gi_paths.scratch("test-jammer-witnesses")
        gi_paths.remove_tree(SCRATCH)
        os.makedirs(SCRATCH)
    return SCRATCH


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as f:
        f.write(text + "\n")


def pcan_sysfs(name, adapter="PCAN-USB FD", ts_mode="3", bitrate="500000", usb="1-3.2.2"):
    d = os.path.join(scratch(), "pcan", name)
    for k, v in (("adapter_name", adapter), ("dev", "506:32"), ("ts_mode", ts_mode), ("nom_bitrate", bitrate)):
        if v is not None:
            write(os.path.join(d, k), v)
    usbd = os.path.join(scratch(), "usb", usb)
    os.makedirs(usbd, exist_ok=True)
    os.symlink(usbd, os.path.join(d, "device"))
    return d


class Msg:
    def __init__(self, ts, aid, err=False):
        self.timestamp, self.arbitration_id, self.is_error_frame = ts, aid, err


class FakePcanBus:
    def __init__(self, msgs=(), channel_info="PCAN_USBBUS1", raise_after=False):
        self.msgs, self.channel_info, self.raise_after, self.shut = list(msgs), channel_info, raise_after, False

    def recv(self, timeout=None):
        if self.msgs:
            return self.msgs.pop(0)
        if self.raise_after:
            raise RuntimeError("PCAN read: bus hardware error")
        time.sleep(0.002)
        return None

    def shutdown(self):
        self.shut = True


class VirtualBus(FakePcanBus):
    pass


def pcan_cases():
    for label, kw, bus_cls, chan, want in (
            ("the bench's PCAN-USB FD, ts_mode 3", {}, FakePcanBus, "PCAN_USBBUS1", None),
            ("not a PCAN-USB adapter", {"adapter": "PCAN-PCI"}, FakePcanBus, "PCAN_USBBUS1", "PCAN-USB"),
            ("no adapter_name", {"adapter": None}, FakePcanBus, "PCAN_USBBUS1", "PCAN-USB"),
            ("a virtual bus class", {}, VirtualBus, "PCAN_USBBUS1", "not a PCAN bus"),
            ("another channel", {}, FakePcanBus, "PCAN_USBBUS2", "not PCAN_USBBUS1"),
            ("ts_mode 2 (host time)", {"ts_mode": "2"}, FakePcanBus, "PCAN_USBBUS1", "ts_mode"),
            ("nom_bitrate 250000", {"bitrate": "250000"}, FakePcanBus, "PCAN_USBBUS1", "nom_bitrate")):
        sysfs = pcan_sysfs("p_%s" % abs(hash(label)), **kw)
        bus = bus_cls(channel_info=chan)
        got = None
        try:
            w = BW.PcanWitness(opener=lambda **kw_: bus, sysfs=sysfs)
        except BW.WitnessRefused as e:
            got = str(e)
        if want is None:
            check("pcan", "opens: %s" % label, got is None and not bus.shut, got)
        else:
            check("pcan", "refused: %s, the bus shut" % label, got is not None and want in got and bus.shut, got)
    # counting
    sysfs = pcan_sysfs("count")
    msgs = [Msg(10.0, 0x088 | 0x200, True), Msg(10.5, 0x004, True), Msg(11.0, 0x200, True),
            Msg(11.5, 0x7A8), Msg(12.0, 0x088, True)]
    bus = FakePcanBus(msgs)
    w = BW.PcanWitness(opener=lambda **kw: bus, sysfs=sysfs)
    w.start()
    time.sleep(0.1)
    w.close()
    check("pcan", "bus-error frames counted, state/counter frames apart, data by id",
          [d for d, _r in w.points()] == [10.0, 12.0] and len(w.other_err) == 2 and w.ids == {"0x7A8": 1},
          (w.points(), w.other_err, w.ids))
    check("pcan", "closed cleanly: not down, same device", w.down() is None and w.same_device(), w.down())
    sysfs = pcan_sysfs("dying")
    w = BW.PcanWitness(opener=lambda **kw: FakePcanBus([Msg(1.0, 0x088, True)], raise_after=True), sysfs=sysfs)
    w.start()
    w.join(1.0)
    check("pcan", "a read that raised: down() says so", (w.down() or "").startswith("witness read failed"),
          w.down())
    sysfs = pcan_sysfs("replug")
    w = BW.PcanWitness(opener=lambda **kw: FakePcanBus(), sysfs=sysfs)
    w.start()
    os.unlink(os.path.join(sysfs, "device"))
    other = os.path.join(scratch(), "usb", "1-3.2.4")
    os.makedirs(other, exist_ok=True)
    os.symlink(other, os.path.join(sysfs, "device"))
    w.close()
    check("pcan", "replugged on another port between open and close: not the same device",
          not w.same_device(), (w.identity_open.get("usb_path"), (w.identity_close or {}).get("usb_path")))


def net_sysfs(name, iface="kvaser", typ="280", driver="kvaser_usb", oper="up"):
    root = os.path.join(scratch(), "net_%s" % name)
    d = os.path.join(root, iface)
    write(os.path.join(d, "type"), typ)
    write(os.path.join(d, "operstate"), oper)
    if driver:
        drv = os.path.join(scratch(), "drivers", driver)
        os.makedirs(drv, exist_ok=True)
        os.makedirs(os.path.join(d, "device"), exist_ok=True)
        os.symlink(drv, os.path.join(d, "device", "driver"))
    return root


class FakeSock:
    def __init__(self, frames, then=None):
        self.frames, self.then, self.closed = list(frames), then, False

    def recvmsg(self, n, anc):
        if self.frames:
            cid, data, sw = self.frames.pop(0)
            raw = struct.pack(BW.FRAME, cid, len(data), data.ljust(8, b"\x00"))
            ts = struct.pack("=6q", int(sw), int(round((sw % 1) * 1e9)), 0, 0, 0, 0) if sw else struct.pack("=6q", 0, 0, 0, 0, 0, 0)
            return raw, [(socket.SOL_SOCKET, BW.SCM_TIMESTAMPING, ts)], 0, None
        if self.then:
            raise self.then
        time.sleep(0.002)
        raise socket.timeout()

    def close(self):
        self.closed = True


def socketcan_cases():
    for label, kw, want in (
            ("kvaser on kvaser_usb, up", {}, None),
            ("another name (can0)", {"iface": "can0"}, "only the bench's Kvaser"),
            ("a vcan named kvaser (no device)", {"driver": None}, "virtual"),
            ("driven by gs_usb", {"driver": "gs_usb"}, "not kvaser_usb"),
            ("link down", {"oper": "down"}, "not up"),
            ("not a CAN netdev", {"typ": "1"}, "not a CAN netdev")):
        root = net_sysfs("n_%s" % abs(hash(label)), **kw)
        why = BW.netdev_why(kw.get("iface", "kvaser"), root)
        if want is None:
            check("socketcan", "accepted: %s" % label, why is None, why)
        else:
            check("socketcan", "refused: %s" % label, why is not None and want in why, why)
    try:
        BW.SocketcanWitness("kvaser", sysfs_net=net_sysfs("vc", driver=None), sock=FakeSock([]))
        got = None
    except BW.WitnessRefused as e:
        got = str(e)
    check("socketcan", "the witness refuses to open on a vcan", got and "virtual" in got, got)
    sock = FakeSock([(0x7A0, b"\x7a\x00", 100.25), (BW.CAN_ERR_FLAG | 0x288, b"\x00\x00\x04\x19", 101.5),
                     (BW.CAN_ERR_FLAG | 0x204, b"\x00\x40", None)])
    w = BW.SocketcanWitness("kvaser", sysfs_net=net_sysfs("ok"), sock=sock)
    w.phase = "pcan"
    w.start()
    time.sleep(0.1)
    w.phase = "end"
    w.close()
    fr = w.frames(("pcan",))
    check("socketcan", "frames tagged with the phase, software stamps kept (None when absent)",
          len(fr) == 3 and abs(fr[0][2] - 100.25) < 1e-6 and fr[2][2] is None and fr[1][3] == BW.CAN_ERR_FLAG | 0x288,
          fr)
    check("socketcan", "closed cleanly: not down, socket closed", w.down() is None and sock.closed, w.down())
    w = BW.SocketcanWitness("kvaser", sysfs_net=net_sysfs("down"),
                            sock=FakeSock([], then=OSError(errno.ENETDOWN, "Network is down")))
    w.start()
    w.join(1.0)
    check("socketcan", "ENETDOWN mid-read: down() says so", (w.down() or "").startswith("witness read failed")
          and "ENETDOWN" in w.down(), w.down())
    d = w.describe()
    check("socketcan", "the record says berr was off", "off" in d["berr"] and d["group_gap_s"] == BW.GROUP_GAP_S, d)
    # groups and the gap's bounds
    check("socketcan", "the gap is at least 10x the stamps' jitter (1.08 ms)",
          BW.GROUP_GAP_S >= 10 * BW.SW_JITTER_MAX_S and BW.SW_JITTER_MAX_S == 0.00108,
          (BW.GROUP_GAP_S, BW.SW_JITTER_MAX_S))
    check("socketcan", "the gap is well under the shortest pulse spacing allowed (5x)",
          BW.MIN_INTERVAL_MS / 1000.0 >= 5 * BW.GROUP_GAP_S, (BW.MIN_INTERVAL_MS, BW.GROUP_GAP_S))
    g = BW.groups([5.0, 5.0004, 6.0, 7.0, 7.09, 7.3])
    check("socketcan", "groups split at gaps over 0.1 s", [len(x) for x in g] == [2, 1, 2, 1], g)
    # pcan_holders
    proc = os.path.join(scratch(), "proc")
    for pid, tgt in ((4242, "/dev/pcanusbfd32"), (4243, "/dev/null"), (os.getpid(), "/dev/pcan32")):
        os.makedirs(os.path.join(proc, str(pid), "fd"), exist_ok=True)
        os.symlink(tgt, os.path.join(proc, str(pid), "fd", "3"))
    h = BW.pcan_holders(proc)
    check("socketcan", "pcan_holders: another pid with /dev/pcan* open, not this process",
          h == [4242], h)


# ------------------------------------------------------------------------ can2_counts

def can2_frames(n=3, sent=50, base=12345.0, drop_7a0=0, unstamped=False, drop_pulse=False, extra=()):
    """A Kvaser log for can2-isolation. Stamps on an arbitrary base: receipt times are epoch,
    software stamps 1.79e9 - base away, so a check judging a window on a stamp finds nothing."""
    log, t = [], 1.7946e9
    for k in range(sent - drop_7a0):
        log.append(("pcan", t + 0.02 * k, base + 0.02 * k, 0x7A0, "7a%02x" % k))
    for k in range(n):
        if drop_pulse and k == 1:
            continue
        log.append(("pulses", t + 5.0 + k + 0.0003, base + 5.0 + k, BW.CAN_ERR_FLAG | 0x288, "00000419"))
        if unstamped and k == 0:    # a second bus error in the first pulse, with no stamp
            log.append(("pulses", t + 5.0 + k + 0.0006, None, BW.CAN_ERR_FLAG | 0x288, "00000419"))
    log.extend(extra)
    return log


def can2_cases():
    for label, kw, want in (
            ("every 0x7A0, one group per pulse", {}, "PASS"),
            ("a pulse missing", {"drop_pulse": True}, "INCONCLUSIVE"),
            ("a bus error with no software stamp", {"unstamped": True}, "INCONCLUSIVE"),
            ("a 0x7A0 lost", {"drop_7a0": 1}, "INCONCLUSIVE"),
            ("a state frame in the pulses phase is not a group",
             {"extra": [("pulses", 1.7946e9 + 9.5, 12345.0 + 9.5, BW.CAN_ERR_FLAG | 0x204, "0040")]}, "PASS"),
            ("an error frame in the PCAN phase is not a group",
             {"extra": [("pcan", 1.7946e9 + 0.5, 12345.0 + 0.5, BW.CAN_ERR_FLAG | 0x288, "00000419")]}, "PASS")):
        cnt = X.can2_counts(can2_frames(**kw), 3)
        v, why = X.judge_can2_isolation(50, cnt["kv_seen"], cnt["n_groups"], 3, 0, 0, "NO_ACK", 2)
        check("can2", "%s -> %s" % (label, want), v == want, (v, why, cnt["groups"], cnt["kv_seen"]))
    cnt = X.can2_counts(can2_frames(extra=[("pcan", 1.7946e9 + 0.5, 12345.5, BW.CAN_ERR_FLAG | 0x288, "")]), 3)
    check("can2", "the other error frames are in the record", cnt["n_other_error_frames"] == 1, cnt)
    cnt = X.can2_counts(can2_frames(base=-5e8), 3)
    check("can2", "stamps on any base count the same (no window on a stamp)",
          cnt["kv_seen"] == 50 and cnt["n_groups"] == 3, cnt)


# ------------------------------------------------------------------------ session

def session_args(spec_list, no_wican=True):
    """x2_jammer's own parser: main() builds it; rebuilt here by running main()'s argparse part."""
    captured = {}
    real = X.argparse.ArgumentParser.parse_args

    def grab(self, args=None, namespace=None):
        captured["ap"] = self
        raise SystemExit(0)
    X.argparse.ArgumentParser.parse_args = grab
    try:
        try:
            X.main()
        except SystemExit:
            pass
    finally:
        X.argparse.ArgumentParser.parse_args = real
    ap = captured["ap"]
    return ap, X.parse_checks(ap, spec_list, no_wican)


def parse_cases():
    ap, checks = session_args(["boot-silence --resets 1", "pulses --n 3", "timeout --p 5000 --ms 2000 --control-ms 1000",
                               "noduration"])
    check("session", "four checks parsed, --no-wican inherited",
          [c[1].sub for c in checks] == ["boot-silence", "pulses", "timeout", "noduration"]
          and all(c[1].no_wican for c in checks) and checks[2][1].control_ms == 1000, [c[0] for c in checks])
    for label, specs, want in (
            ("can2-isolation refused in the PCAN's session", ["can2-isolation --n 3"], "0x7A0 sender"),
            ("id refused (no witness)", ["id"], "a session runs only"),
            ("a check inside a check", ["pulses --check noduration"], "no --check"),
            ("a check's own refusal", ["timeout --expect-name x"], "go only with id"),
            ("not arguments", ["pulses --bogus"], "not arguments"),
            ("no checks", [], "at least one")):
        try:
            X.parse_checks(ap, specs, True)
            got = None
        except ValueError as e:
            got = str(e)
        check("session", "refused: %s" % label, got is not None and want in got, got)

    class A:
        def __init__(self, **kw):
            d = dict(sub="session", no_wican=True, wican_mode=0, expect_name=None, expect_elf=None,
                     expect_stop_by=None, expect_end=None, check=[], interval_ms=1000)
            d.update(kw)
            self.__dict__.update(d)
    for label, kw, want in (
            ("session without a check", {}, "at least one"),
            ("--check on pulses", {"sub": "pulses", "check": ["noduration"]}, "only with session"),
            ("can2-isolation at 400 ms", {"sub": "can2-isolation", "interval_ms": 400}, "at least 500"),
            ("control: can2-isolation at 1000 ms", {"sub": "can2-isolation"}, None),
            ("control: a session with a check", {"check": ["noduration"]}, None)):
        why = X.args_why(A(**kw))
        check("session", "args_why: %s" % label, (why is None) if want is None else (why and want in why), why)


class SessionCon:
    """The jammer's console for a session: PULSES -> ACK, PULSE per pulse, DONE; TRAIN w p ms ->
    ACK, DONE at +ms; TRAIN w p -> ERR; a reset prints the mcp2515 line 0.4 s later. `wire`
    collects the error frames' wire times the fake PCAN will have seen."""

    def __init__(self, stale_at=None, after_done=None):
        self.lock = threading.Lock()
        self.lines, self.sent, self.events, self.wire = [], [], [], []
        self.paused = threading.Event()
        self.stale_at = stale_at    # (index of the PULSES command, s before its send): a stale frame
        self.after_done = after_done    # (index of the PULSES command, s after its DONE): a real frame

    def add(self, t, txt):
        self.lines.append((t, txt, X.parse_line(txt)))

    def send(self, line):
        t = time.time()
        self.sent.append(line)
        w = line.split()
        if w and w[0] == "PULSES":
            n, iv = int(w[2]), int(w[3]) / 1000.0
            self.add(t + 0.01, "JAMMER ACK PULSES w_us=20 n=%d interval_ms=%d" % (n, int(w[3])))
            for k in range(n):
                self.add(t + 0.05 + k * iv, "JAMMER PULSE k=%d t_us=1 rel_us=%d" % (k, k * iv * 1e6))
                self.wire.append((t + 0.049 + k * iv, None))
            self.add(t + 0.2 + (n - 1) * iv, "JAMMER DONE PULSES end=complete stop_by=none pulses=%d" % n)
            if self.after_done is not None and sum(1 for s in self.sent if s.startswith("PULSES")) == self.after_done[0]:
                # on the wire after the DONE: TXD floating at close_channel, say
                self.wire.append((t + 0.2 + (n - 1) * iv + self.after_done[1], None))
            if self.stale_at is not None and sum(1 for s in self.sent if s.startswith("PULSES")) == self.stale_at[0]:
                # delivered inside this command's window (before its first pulse's frame, so the
                # queue stays in order), stamped well before its send
                self.wire.append((t - self.stale_at[1], t + 0.03))
        elif w and w[0] == "TRAIN" and len(w) == 4:
            ms = int(w[3])
            self.add(t + 0.01, "JAMMER ACK TRAIN w_us=20 p_us=5000 ms=%d t0_us=1" % ms)
            self.add(t + ms / 1000.0, "JAMMER DONE TRAIN end=stopped stop_by=deadline_timer pulses=1 chunks=1")
            k = 0
            while 0.01 + 0.005 * k < ms / 1000.0 - 0.003:
                self.wire.append((t + 0.01 + 0.005 * k, None))
                k += 1
        elif w and w[0] == "TRAIN":
            self.add(t + 0.01, "JAMMER ERR no_duration")
        self.events.append((t, "sent: %s" % line))

    def wait(self, pred, timeout, since):
        """As the real console: returns when the line has arrived (the fake clock moves on to
        it), or after the timeout."""
        for t, _x, p in self.lines:
            if t >= since and p and pred(p):
                if t - time.time() <= timeout:
                    time.sleep(max(0.0, t - time.time()))
                    return t, p
        time.sleep(timeout)
        return None

    def pulse_reset(self):
        self.add(time.time() + 0.4, "JAMMER mcp2515 canstat=0x80 canctrl=0x87 config_mode=yes")


class SessionPcan:
    """A PCAN witness: device stamps on an uptime base with drift, received after a delivery lag."""

    def __init__(self, con, drift_ppm=-6.0, back_at=None, down_after=None, down_at_end=False, slow_every=0,
                 extra_lag=0.0, deaf_last=False):
        self.con, self.drift, self.back_at, self.slow_every = con, drift_ppm, back_at, slow_every
        self.extra_lag, self.deaf_last = extra_lag, deaf_last
        self.t_open = time.time() - 100.0
        self.opens, self.closed, self.calls = 1, False, 0
        self.down_after, self.down_at_end = down_after, down_at_end

    def down(self):
        self.calls += 1
        if self.down_after is not None and self.calls > self.down_after:
            return "witness read failed at 1.000: RuntimeError: planted"
        if self.down_at_end and self.closed is False and self.calls > 4:
            return "witness read failed at 2.000: RuntimeError: planted at the end"
        return None

    def close(self):
        self.closed = True

    def same_device(self):
        return True

    def points(self):
        e0 = self.t_open
        out, last = [], 0.0
        wire = sorted(self.con.wire)
        if self.deaf_last:
            wire = wire[:-1]
        for k, (w, rx) in enumerate(wire):
            d = UPTIME + (w - e0) * (1.0 + self.drift * 1e-6)
            if self.back_at is not None and k >= self.back_at:
                d -= 4000.0
            if rx is None:
                slow = 0.3 if self.slow_every and k % self.slow_every == 0 else 0.0
                rx = last = max(last, w + lag(k) + slow + self.extra_lag)   # one FIFO queue, in wire order
            out.append((d, rx))
        return sorted(out, key=lambda p: p[1])


def fake_journal(lines_at=(), rc=0):
    """A journalctl for usb_record: lines_at [(offset from the run's start, text)]."""
    calls = []

    def run(argv, **kw):
        t0 = float(argv[argv.index("--since") + 1].lstrip("@"))
        calls.append(argv)
        out = "".join("%.6f madhouse-debian kernel: %s\n" % (t0 + dt, txt) for dt, txt in lines_at)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="" if rc == 0 else "no access")
    run.calls = calls
    return run


def fake_session(specs, con=None, wit_kw=None, journal=None):
    ap, checks = session_args(specs)
    con = con or SessionCon()
    clock, lk = [time.time()], threading.Lock()
    real_sleep = time.sleep
    saved = (X.time.sleep, X.time.time)

    def fsleep(s):
        with lk:
            clock[0] += s
        real_sleep(0.0002)
    X.time.sleep, X.time.time = fsleep, lambda: clock[0]
    windows = []
    try:
        wit = SessionPcan(con, **(wit_kw or {}))
        jr = journal or fake_journal()
        out, anc, outside = X.run_session(checks, con, wit, settle=lambda s: None,
                                          resource=lambda a, b: windows.append((a, b)) or {"window": [a, b]},
                                          usb=lambda a, b: X.usb_record(a, b, run=jr))
    finally:
        X.time.sleep, X.time.time = saved
    return out, anc, outside, windows, con


# The timeout before two pulse checks: its judge counts every frame after the deadline, so a
# check judged on frames outside its own span would FAIL there.
SPECS = ["boot-silence --resets 2", "pulses --n 1", "timeout --p 5000 --ms 2000 --control-ms 1000",
         "pulses --n 10 --interval-ms 1000", "pulses --n 9 --interval-ms 1100", "noduration"]


def session_cases():
    out, anc, outside, windows, con = fake_session(SPECS)
    v = [c.get("verdict") for c in out]
    check("session", "six checks on the PCAN: all PASS", v == ["PASS"] * 6, [(c["spec"], c.get("verdict"), c.get("why"))
                                                                         for c in out])
    check("session", "the anchor holds (drift -6 ppm, lag 0.5-2.9 ms)", anc["void"] is None and anc["n"] > 300,
          (anc["void"], anc["n"], anc.get("spread_p99_s")))
    tm = out[2]
    check("session", "timeout's control (1 s against a 2 s train) FAILs",
          tm.get("negative_control", {}).get("ok") is True
          and tm["negative_control"]["judge"]["verdict"] == "FAIL", tm.get("negative_control"))
    check("session", "every check names the PCAN witness", all(c["witness"] == X.WITNESS_PCAN for c in out), "")
    check("session", "a resource-log window per check, its own span",
          len(windows) == 6 and all(c["resource_log"]["window"][0] == c["drive"]["t_begin"] for c in out)
          and all("resource-log" in c["counts"] for c in out), windows)
    check("session", "brackets recorded with margins", all(b["ok"] and b["margin_after_send_min_s"] is not None
                                                           for c in out[1:5] for b in c["bracket"]),
          [c.get("bracket") for c in out[1:2]])
    check("session", "pulses 10 @ 1000: ten groups of one",
          out[3]["per_pulse"]["exactly_one_each"] and out[3]["per_pulse"]["groups"] == 10, out[3].get("per_pulse"))
    check("session", "boot silence: closing pulse counted once, resets silent",
          out[0]["control"]["witness_err_frames"] == 1 and all(r["witness_err_frames"] == 0 for r in out[0]["resets"]),
          out[0].get("control"))
    check("session", "no frame outside the checks", outside == [], outside[:5])
    check("session", "the timed checks' args are in the record, not a Namespace",
          all(isinstance(c["args"], dict) for c in out), "")
    # a stale frame inside the 2nd PULSES command's window, stamped 0.5 s before its send
    out, anc, _o, _w, _c = fake_session(SPECS, con=SessionCon(stale_at=(2, 0.5)))
    v = [c.get("verdict") for c in out]
    check("session", "a stale frame inside a command's window -> that check VOID by the bracket",
          str(v[1]).startswith("VOID") and "bracket" in v[1] and v[0] == "PASS" and v[2] == "PASS", v)
    check("session", "the USB record read once, over the whole session, nothing in it",
          all(c.get("usb", {}).get("lines") == [] for c in out), [c.get("usb") for c in out][:1])
    # the USB record (2026-11-13): a made-up -71 line on the 1-3.2 hub in the timeout's window
    t_to = out[2]["drive"]["t_begin"] - out[0]["drive"]["t_begin"] + 3.0
    out2, _a, _o, _w, _c = fake_session(SPECS, journal=fake_journal(
        [(t_to, "usb 1-3.2.3: can't set config #1, error -71")]))
    v = [c.get("verdict") for c in out2]
    check("session", "can-fail: a -71 line on 1-3.2.3 in a check's window -> that check VOID, kept",
          str(v[2]).startswith("VOID: USB") and "error -71" in (out2[2].get("usb", {}).get("lines") or [""])[0]
          and v[:2] == ["PASS", "PASS"] and v[3:] == ["PASS"] * 3, v)
    out2, _a, _o, _w, _c = fake_session(SPECS, journal=fake_journal(rc=1))
    check("session", "the kernel log unreadable -> every check VOID",
          all(str(c.get("verdict")).startswith("VOID: USB") for c in out2), [c.get("verdict") for c in out2][:2])
    # the bracket widened (reviewer, 2026-11-13): every delivery 0.2 s slower than the DONE line,
    # so every frame is received after its DONE; the anchor holds (a constant lag) and maps every
    # frame 0.2 s late, past its DONE's receipt
    out2, anc2, _o, _w, _c = fake_session(SPECS, wit_kw={"extra_lag": 0.2})
    v = [c.get("verdict") for c in out2]
    # (by frames outside: the window before the widening saw no frame at all here, and only the
    # empty-command rule would have VOIDed it)
    check("session", "deliveries 0.2 s slower than DONE: the anchor holds, every check VOID, frames outside",
          anc2["void"] is None and all(str(x).startswith("VOID: bracket") and "outside" in str(x) for x in v),
          (anc2["void"], v))
    # a real frame on the wire 0.5 s after the pulses' DONE: a FAIL, and it stays a FAIL
    out2, _a, _o, _w, _c = fake_session(SPECS, con=SessionCon(after_done=(2, 0.5)))
    v = [c.get("verdict") for c in out2]
    check("session", "a real frame after DONE: FAIL stays FAIL, the breach kept in the record",
          v[1] == "FAIL" and "bracket" in out2[1].get("bracket_breach", "") and v[0] == "PASS" and v[2] == "PASS",
          (v, out2[1].get("bracket_breach")))
    # the witness misses the last command's only frame (noduration's closing pulse)
    out2, _a, _o, _w, _c = fake_session(SPECS, wit_kw={"deaf_last": True})
    v = [c.get("verdict") for c in out2]
    check("session", "a command with a DONE and no frame -> that check VOID",
          str(v[5]).startswith("VOID: bracket") and "no frame" in v[5] and v[:5] == ["PASS"] * 5, v)
    # every 20th frame delivered 0.3 s late, and those behind it in the queue: the anchor VOIDs
    # (spread or drift) with an offset, so nothing but the VOID keeps the checks from a judgement
    out, anc, _o, _w, _c = fake_session(SPECS, wit_kw={"slow_every": 20})
    check("session", "slow deliveries -> the anchor VOID (with an offset), every check VOID",
          anc["offset_s"] is not None and anc["void"]
          and all(str(c.get("verdict")).startswith("VOID: session anchor: ") for c in out),
          (anc["void"], [c.get("verdict") for c in out][:2]))
    # the device clock went back
    out, anc, _o, _w, _c = fake_session(SPECS, wit_kw={"back_at": 200})
    check("session", "the device clock went back -> every check VOID (REFUSED)",
          all(str(c.get("verdict")).startswith("VOID: session anchor: REFUSED") for c in out),
          [c.get("verdict") for c in out][:2])
    # a session with too few frames (boot silence only: one closing pulse)
    out, anc, _o, _w, _c = fake_session(["boot-silence --resets 1"])
    check("session", "too few frames to anchor -> VOID, not PASS", str(out[0].get("verdict")).startswith("VOID"),
          out[0].get("verdict"))
    # witness down before the 3rd check
    out, anc, _o, _w, _c = fake_session(SPECS, wit_kw={"down_after": 2})
    v = [c.get("verdict") for c in out]
    check("session", "witness down before a check -> NOT-RUN for every check",
          all(x == "NOT-RUN" for x in v), v)
    out, anc, _o, _w, _c = fake_session(SPECS, wit_kw={"down_at_end": True, "down_after": None})
    v = [c.get("verdict") for c in out]
    check("session", "witness down at the end -> NOT-RUN for every driven check", all(x == "NOT-RUN" for x in v), v)


def usb_cases():
    """usb_record's matcher and usb_fold (2026-11-13, the reviewer's USB record)."""
    lines = [(1.0, "gs_usb 1-3.2.3:1.0 canmain: failed to xmit URB 0: -EPROTO"),
             (2.0, "usb 1-3.2.1: USB disconnect, device number 12"),
             (3.0, "usb 1-3.2.2: reset high-speed USB device number 9 using xhci_hcd"),
             (4.0, "usb 1-3.2: Error -71 while reading timestamp"),
             (5.0, "usb 1-3.4: USB disconnect, device number 7"),          # the X2's port, not the hub
             (6.0, "usb 1-3.21: reset full-speed USB device"),             # another port
             (7.0, "usb 1-3.2.1: new full-speed USB device number 13"),    # no error word
             (8.0, "kvaser_usb 1-3.2.1:1.0 kvaser: error -71")]
    rec = X.usb_record(1000.0, 1010.0, run=fake_journal(lines))
    got = [round(t - 1000.0, 1) for t, _ln in rec["hits"]]
    check("usb", "EPROTO, disconnect, reset, -71 on 1-3.2.x kept; 1-3.4, 1-3.21, no error word not",
          got == [1.0, 2.0, 3.0, 4.0, 8.0], got)
    r = {"verdict": "PASS"}
    X.usb_fold(r, rec, 1000.5, 1001.5)
    check("usb", "fold: a line in the window -> VOID, the line kept", r["verdict"].startswith("VOID: USB")
          and "EPROTO" in r["usb"]["lines"][0] and r["verdict_before_usb"] == "PASS", r)
    r = {"verdict": "FAIL"}
    X.usb_fold(r, rec, 1000.5, 1001.5)
    check("usb", "fold: a FAIL in the window is VOID too", r["verdict"].startswith("VOID: USB"), r)
    r = {"verdict": "PASS"}
    X.usb_fold(r, rec, 1004.5, 1007.5)
    check("usb", "fold: control, only lines that do not VOID in the window -> PASS stays",
          r["verdict"] == "PASS" and r["usb"]["lines"] == [], r)
    r = {"verdict": "NOT-RUN"}
    X.usb_fold(r, rec, 1000.5, 1001.5)
    check("usb", "fold: a NOT-RUN stays NOT-RUN", r["verdict"] == "NOT-RUN", r)
    r = {"verdict": "PASS"}
    X.usb_fold(r, X.usb_record(1000.0, 1010.0, run=fake_journal(rc=1)), 1000.5, 1001.5)
    check("usb", "fold: the kernel log unreadable -> VOID", r["verdict"].startswith("VOID: USB")
          and "not read" in r["usb"]["lines"][0], r)


def main():
    import gi_paths
    try:
        usb_cases()
        anchor_cases()
        pcan_cases()
        socketcan_cases()
        can2_cases()
        parse_cases()
        session_cases()
    finally:
        if SCRATCH is not None:
            gi_paths.remove_tree(SCRATCH)
    print("\n=== %s ===" % ("PASS" if not BAD else "FAIL: %d" % len(BAD)))
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
