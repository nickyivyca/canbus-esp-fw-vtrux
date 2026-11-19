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
         1. sudo -n /usr/local/sbin/canctl mode canchg libusb
         2. the re-check: role canchg resolved from canctl's role table
            (adapter_roles.resolve) in libusb mode, so not on the kernel's
            gs_usb driver, with a USB bus and address
         It fails only if one of the two fails, and says which. A failed
         start hook stops the bench: no group that needs the lock runs.
  end    (on_end, after the last case and the end pass, on every run however
         it ended, unless the lock is no longer the run's)
         1. sudo -n /usr/local/sbin/canctl mode canchg socketcan --wait N
            (N = BIND_WAIT_S: canctl also waits until canchg's netdev exists)
         2. the check: role canchg resolved again, in socketcan mode, on the
            kernel's gs_usb driver, with a netdev, polled for up to
            BIND_WAIT_S (udev names the netdev a moment after the bind)

Which device canchg is, and its driver, netdev, bus and address, come from
canctl's role table through the pinned canbench (shared/bench SPEC section
8's adapter roles, 2026-11-18): the harness names the role and never a
serial, an index or a netdev of its own. Before 2026-11-18 both checks found
canchg in sysfs by the USB serial in VTRUX_CANCHG_DEVICE (the local
settings' canchg_device), which is gone.
         A failure is the runner's "RELEASE NOT CONFIRMED" line. canctl's
         mode is kept across replugs, so without this step a failed or
         killed run would leave canchg in libusb mode for the next holder.
         A start step that failed still left the libusb mode saved (canctl
         saves the mode even when it does not take), and the runner still
         calls on_end, so this step puts it back.

canctl's exit codes (its header; installed and checked on canchg by the CAN
replay tooling session, 2026-11-12): 0 done, 2 refused (bad arguments or
state; `--wait` with `mode ... libusb` is refused, so the start step does
not pass it), 3 did not take (a bind left no interface on gs_usb, an unbind
left one there, or `--wait` ran out). Any non-zero exit is a failure,
naming canctl, its rc and what the rc means. Each step still reads the
result back itself as well (the reviewer's batch: the re-check, and the
bound check), from canctl's role table, which reads the driver and netdev
off the system each time. Before 2026-11-12 the installed canctl exited 0
even when a bind did not take, and the read-backs are what caught that.

A process that opened the Innomaker through pyusb holds it until it exits,
and the bind fails until then (canctl exit 3). The end step runs in the
runner's on_end, after every check's process has exited; the runner itself
never opens the Innomaker (the canctl owner, relayed by the reviewer,
2026-11-12).

Off Linux both steps do nothing and say so: the Windows bench has no canctl
and no kernel driver to take canchg from.

canctl, the role table (ROLES) and the clock are module globals, read at
each call, so the tests (canchg_mode_test.py, bench_suite_test.py) point
them at a fake canctl and a stand-in role table.

    hooks(on_start, on_end) -> (on_start, on_end)
        the bench suite's board hooks: on the X2, this module's step and
        then (start) or before it (end) the hook given; on any other board
        the hook given alone
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]
ROLE = "canchg"
ROLES = None                # the role table: adapter_roles, or a stand-in (tests)
GS_USB_DRIVER = "gs_usb"
X2_BOARD = "x2"
CANCTL_TIMEOUT_S = 60
BIND_WAIT_S = 10.0
POLL_S = 0.5
RC_MEANING = {2: "refused", 3: "did not take"}         # canctl's header
sleep = time.sleep
monotonic = time.monotonic


def linux(platform=None):
    return (platform or sys.platform).startswith("linux")


def _resolve():
    """canbench's Resolved for role canchg, from canctl's role table."""
    roles = ROLES
    if roles is None:
        import adapter_roles as roles
    return roles.resolve(ROLE)


def _rc_text(rc):
    return "rc %s%s" % (rc, (", %s" % RC_MEANING[rc]) if rc in RC_MEANING else "")


def _canctl(mode, logdir, step, extra=()):
    """Run canctl mode canchg <mode> [extra]; its output to a log in logdir.
    -> (rc or None if it could not run, the last line of its output)."""
    argv = list(CANCTL) + ["mode", ROLE, mode] + list(extra)
    log = os.path.join(logdir, "canchg_%s_%s.txt" % (step, time.strftime("%Y%m%dT%H%M%S")))
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=CANCTL_TIMEOUT_S)
        out = p.stdout.decode("utf-8", "replace")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "timed out after %d s" % CANCTL_TIMEOUT_S, None
    except OSError as e:
        out, rc = "could not run: %s" % e, None
    try:
        with open(log, "w", encoding="utf-8") as f:
            f.write("$ %s\n%s\nrc %s\n" % (" ".join(argv), out, rc))
    except OSError:
        pass
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return rc, (lines[-1] if lines else "no output")


def bound_state():
    """-> (bound, netdev, why), from canctl's role table: whether role
    canchg is in socketcan mode on the kernel's gs_usb driver, whether it
    has a netdev, and a line saying what was found. Raises what the role
    table raises when canchg does not resolve (not attached, two devices,
    socketcan mode with no netdev yet)."""
    # canbench resolves a gs_usb role in socketcan mode only with a netdev,
    # and refuses one in libusb mode still on gs_usb, so the driver says it
    res = _resolve()
    r = res.role
    bound = r.driver == GS_USB_DRIVER
    netdev = bool(res.netdev)
    why = "role %s: mode %s, driver %s, netdev %s" % (
        ROLE, res.mode, r.driver or "none", res.netdev or "absent")
    return bound, netdev, why


def start(logdir, platform=None):
    """canctl mode canchg libusb, then the re-check. -> hook result."""
    if not linux(platform):
        return dict(ok=True, detail="canchg: no mode step off Linux")
    rc, last = _canctl("libusb", logdir, "start")
    if rc != 0:
        return dict(ok=False, detail="canchg: canctl mode canchg libusb failed (%s): %s"
                    % (_rc_text(rc), last), found={"canctl_rc": rc})
    try:
        res = _resolve()
        if res.mode != "libusb":
            raise RuntimeError("role %s resolves in mode %s, not libusb" % (ROLE, res.mode))
    except Exception as e:                              # noqa: BLE001
        return dict(ok=False, detail="canchg: the re-check after canctl mode canchg libusb "
                    "failed: %s" % e, found={"canctl_rc": rc})
    return dict(ok=True, detail="canchg: libusb mode (canctl rc 0; re-check by role: USB bus "
                "%s address %s, not on gs_usb)" % (res.busnum, res.devnum),
                found={"canctl_rc": rc, "bus": res.busnum, "address": res.devnum})


def end(logdir, platform=None):
    """canctl mode canchg socketcan, then the check that canchg is bound
    again (polled for up to BIND_WAIT_S). -> hook result."""
    if not linux(platform):
        return dict(ok=True, detail="canchg: no mode step off Linux")
    rc, last = _canctl("socketcan", logdir, "end", ["--wait", "%d" % BIND_WAIT_S])
    t_end = monotonic() + BIND_WAIT_S
    while True:
        try:
            bound, netdev, why = bound_state()
        except Exception as e:                          # noqa: BLE001
            bound, netdev, why = False, False, "not checked: %s" % e
        if (bound and netdev) or monotonic() >= t_end:
            break
        sleep(POLL_S)
    found = {"canctl_rc": rc, "bound": bound, "netdev": netdev}
    if rc != 0:
        return dict(ok=False, detail="canchg: canctl mode canchg socketcan failed (%s): "
                    "%s; %s" % (_rc_text(rc), last, why), found=found)
    if not (bound and netdev):
        return dict(ok=False, detail="canchg: not bound again after canctl mode canchg "
                    "socketcan (rc 0): %s" % why, found=found)
    return dict(ok=True, detail="canchg: back on gs_usb (%s)" % why, found=found)


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
