#!/usr/bin/env python3
"""The bench run puts the charger segment's Innomaker (canchg) in libusb
mode itself at its start, and back on the kernel's gs_usb driver at its end.

Charge-interposer tester, 2026-11-12 (the charge-interposer reviewer's batch
the same day, from the user's point that canctl exists so sessions configure
the bench without anyone typing commands). On madhouse-debian canchg may be
on the kernel's gs_usb driver (SocketCAN) when the run starts, and the
harness opens it only in canctl's libusb mode (adapter_roles.canchg). So on
Linux, in the bench suite's board hooks on the X2 (the board on canchg's
segment):

  start  (on_start, before the start pass identifies or flashes anything,
         and again at a resume)
         bench_layer.to_libusb("canchg"): `canctl mode canchg libusb`, then
         read back from canctl's role table until canchg resolves in libusb
         mode (off the kernel's gs_usb driver, with a USB bus and address)
         A failure, the layer's refusal or the guard's, says so. A failed
         start hook stops the bench: no group that needs the lock runs.
  end    (on_end, after the last case and the end pass, on every run however
         it ended, unless the lock is no longer the run's)
         bench_layer.to_socketcan("canchg", wait_s=BIND_WAIT_S): `canctl
         mode canchg socketcan --wait N`, then read back until canchg
         resolves in socketcan mode on the kernel's gs_usb driver with a
         netdev and its link down, for up to BIND_WAIT_S
         A failure is the runner's "RELEASE NOT CONFIRMED" line. canctl's
         mode is kept across replugs, so without this step a failed or
         killed run would leave canchg in libusb mode for the next holder.
         A start step that failed may still have left the libusb mode saved
         (canctl saves the mode even when it does not take), and the runner
         still calls on_end, so this step puts it back.

The run-long hold (the charge-interposer reviewer's bench-layer item,
2026-11-19): the mode is handed over once at the start and back once at the
end, with nothing put back in between. The checks' own opens
(adapter_roles.canchg, bench_layer.open_bench) find canchg already in libusb
mode, so they change nothing and put nothing back on shutdown.

Since 2026-11-19 both steps are can-tools' bench-role layer
(canbench.bench_layer, through adapter_roles.layer(), behind shared/bench's
bench guard): it runs canctl, names its exit (2 refused, 3 did not take),
and reads the result back from canctl's role table itself. Before, this
module ran canctl and read back by role itself (from 2026-11-12, the
reviewer's batch: the re-check and the bound check, which caught a canctl
that exited 0 when a bind did not take). Which device canchg is comes from
canctl's role table (shared/bench SPEC section 8's adapter roles,
2026-11-18); before 2026-11-18 it was found in sysfs by the USB serial in
VTRUX_CANCHG_DEVICE (the local settings' canchg_device), which is gone.

A process that opened the Innomaker through pyusb holds it until it exits,
and the bind fails until then (canctl exit 3). The end step runs in the
runner's on_end, after every check's process has exited; the runner itself
never opens the Innomaker (the canctl owner, relayed by the reviewer,
2026-11-12).

Off Linux both steps do nothing and say so: the Windows bench has no canctl
and no kernel driver to take canchg from.

The layer (LAYER) is a module global, read at each call, so the tests
(canchg_mode_test.py, bench_suite_test.py) point it at a stand-in.

Each step writes what it did and what was read back to
canchg_<step>_<time>.txt in the run's log folder.

    hooks(on_start, on_end) -> (on_start, on_end)
        the bench suite's board hooks: on the X2, this module's step and
        then (start) or before it (end) the hook given; on any other board
        the hook given alone
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ROLE = "canchg"
LAYER = None                # the bench-role layer: adapter_roles.layer(), or a stand-in
X2_BOARD = "x2"
BIND_WAIT_S = 10.0          # to_socketcan's wait for the bind and the netdev


def linux(platform=None):
    return (platform or sys.platform).startswith("linux")


def _layer():
    """The bench-role layer with the guard set (adapter_roles.layer), or
    LAYER."""
    if LAYER is not None:
        return LAYER
    import adapter_roles
    return adapter_roles.layer()


def _found(res):
    """What the layer read back, as the hook result's found."""
    r = getattr(res, "role", None)
    return {"mode": getattr(res, "mode", None), "driver": getattr(r, "driver", None),
            "netdev": getattr(res, "netdev", None), "bus": getattr(res, "busnum", None),
            "address": getattr(res, "devnum", None)}


def _log(logdir, step, text):
    try:
        with open(os.path.join(logdir, "canchg_%s_%s.txt" % (step, time.strftime(
                "%Y%m%dT%H%M%S"))), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def start(logdir, platform=None):
    """bench_layer.to_libusb(canchg): the hand-over and its read-back. ->
    hook result."""
    if not linux(platform):
        return dict(ok=True, detail="canchg: no mode step off Linux")
    try:
        res = _layer().to_libusb(ROLE)
    except Exception as e:                              # noqa: BLE001
        out = dict(ok=False, detail="canchg: bench_layer.to_libusb %s failed: %s: %s"
                   % (ROLE, type(e).__name__, e), found={"error": str(e)})
        _log(logdir, "start", out["detail"])
        return out
    f = _found(res)
    if f["mode"] != "libusb":
        out = dict(ok=False, detail="canchg: bench_layer.to_libusb %s returned mode %s, not "
                   "libusb" % (ROLE, f["mode"]), found=f)
    else:
        out = dict(ok=True, detail="canchg: libusb mode (bench_layer.to_libusb, read back by "
                   "role: USB bus %s address %s, not on gs_usb)" % (f["bus"], f["address"]),
                   found=f)
    _log(logdir, "start", out["detail"])
    return out


def end(logdir, platform=None):
    """bench_layer.to_socketcan(canchg, wait_s=BIND_WAIT_S): back on the
    kernel's gs_usb with a netdev, read back. -> hook result."""
    if not linux(platform):
        return dict(ok=True, detail="canchg: no mode step off Linux")
    try:
        res = _layer().to_socketcan(ROLE, wait_s=BIND_WAIT_S)
    except Exception as e:                              # noqa: BLE001
        out = dict(ok=False, detail="canchg: not back on gs_usb: bench_layer.to_socketcan %s "
                   "failed: %s: %s" % (ROLE, type(e).__name__, e), found={"error": str(e)})
        _log(logdir, "end", out["detail"])
        return out
    f = _found(res)
    if f["mode"] != "socketcan" or f["driver"] != "gs_usb" or not f["netdev"]:
        out = dict(ok=False, detail="canchg: not back on gs_usb: bench_layer.to_socketcan %s "
                   "returned mode %s, driver %s, netdev %s" % (ROLE, f["mode"], f["driver"],
                                                                f["netdev"]), found=f)
    else:
        out = dict(ok=True, detail="canchg: back on gs_usb (bench_layer.to_socketcan, read back "
                   "by role: netdev %s)" % f["netdev"], found=f)
    _log(logdir, "end", out["detail"])
    return out


def _call(hook, board, img, ctx, logdir):
    """The hook given, its raise turned into a failed result, so this
    module's step and its result are kept whatever the hook does."""
    try:
        out = hook(board, img, ctx, logdir)
    except Exception as e:                              # noqa: BLE001
        return dict(ok=False, detail="raised %r" % (e,))
    return dict(out)


def _both(mine, theirs):
    found = dict(theirs.get("found") or {})
    found["canchg"] = mine.get("found")
    return dict(ok=bool(mine.get("ok")) and bool(theirs.get("ok")),
                detail="%s; %s" % (mine.get("detail"), theirs.get("detail")), found=found)


def hooks(on_start, on_end, board=X2_BOARD, platform=None):
    """The bench suite's on_start / on_end: on `board`, start() and then
    on_start; on_end and then end(), end() whatever on_end returned or
    raised; on any other board the hook given alone."""
    def start_hook(b, img, ctx, logdir):
        if b != board:
            return on_start(b, img, ctx, logdir)
        mine = start(logdir, platform)
        return _both(mine, _call(on_start, b, img, ctx, logdir))

    def end_hook(b, img, ctx, logdir):
        if b != board:
            return on_end(b, img, ctx, logdir)
        theirs = _call(on_end, b, img, ctx, logdir)
        return _both(end(logdir, platform), theirs)

    return start_hook, end_hook
