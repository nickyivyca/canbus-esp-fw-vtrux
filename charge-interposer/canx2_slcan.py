#!/usr/bin/env python3
"""Before a bench run identifies or flashes the X2, it detaches canx2 from
the kernel's SLCAN if it finds it attached, through canctl, and records it.

Charge-interposer tester, 2026-11-13 (the charge-interposer reviewer's
run-start batch, from shared/bench SPEC section 8 at shared/main 9342bf8b,
the lock as a mutex: "If canx2 is attached as SLCAN, it is detached before
a flash"; the holder before may have left it attached, and nothing is put
back at a release). canx2 is canctl's slcan role on the X2's USB serial
port: the same USB device, and the same tty, as the run's serial_port, the
port bench_identify reads the X2 on and the esptool flasher writes it
through. While slcand holds that tty as netdev canx2, the run's own reads
and writes on it would fight the kernel's.

So on Linux, in the bench suite's start hook on the X2 (before the
start pass identifies or flashes anything, and again at a resume, when
another role's take in between may have attached it):

  1. the state: netdev canx2 in /sys/class/net, and any slcand process
     whose command line ends in the role (`slcand <tty> canx2`, as canctl
     starts it), read from /proc
  2. neither: nothing to do, ok, and the detail says it was not attached
  3. either: `sudo -n /usr/local/sbin/canctl slcan detach canx2` (canctl
     takes the link down and stops slcand), then the state polled for up
     to DETACH_WAIT_S until both are gone (slcand exits on its signal a
     moment after canctl returns)
  4. not ok when canctl exits non-zero (2: refused, such as a netdev with
     no slcand behind it) or the state still shows it attached at the
     end; the detail says which, with the state before and after

A failed start hook stops the bench: no group that needs the lock runs.
There is no end step: nothing is put back at a release (the mutex ruling),
so canx2 stays detached, and the run's record says so.

Off Linux the step does nothing and says so: the Windows bench has no
canctl and no kernel SLCAN.

canctl, the sysfs and proc roots and the clock are module globals, read at
each call, so the tests (canx2_slcan_test.py, bench_suite_test.py) point
them at a fake canctl and made-up trees.

    hooks(on_start, on_end) -> (on_start, on_end)
        the bench suite's board hooks: on the X2, this module's step and
        then the start hook given; the end hook given unchanged; on any
        other board the start hook given alone
"""

import os
import subprocess
import sys
import time

CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]
ROLE = "canx2"
SYSFS_NET = "/sys/class/net"
PROC = "/proc"
SLCAND = "slcand"
X2_BOARD = "x2"
CANCTL_TIMEOUT_S = 60
DETACH_WAIT_S = 5.0
POLL_S = 0.25
RC_MEANING = {2: "refused", 3: "did not take"}         # canctl's header
sleep = time.sleep
monotonic = time.monotonic


def linux(platform=None):
    return (platform or sys.platform).startswith("linux")


def _rc_text(rc):
    return "rc %s%s" % (rc, (", %s" % RC_MEANING[rc]) if rc in RC_MEANING else "")


def slcand_pids(proc_root=None):
    """The pids of the slcand processes attached as ROLE: argv[0]'s name
    is slcand and its last argument is the role."""
    proc_root = proc_root or PROC
    out = []
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, name, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue                    # exited while we looked
        argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        if len(argv) >= 2 and os.path.basename(argv[0]) == SLCAND and argv[-1] == ROLE:
            out.append(int(name))
    return sorted(out)


def state(net_root=None, proc_root=None):
    """-> (netdev, pids, why): whether netdev canx2 exists, the slcand pids
    attached as canx2, and a line saying what was found."""
    net_root = net_root or SYSFS_NET
    netdev = os.path.lexists(os.path.join(net_root, ROLE))
    pids = slcand_pids(proc_root)
    why = "netdev %s %s, %s" % (ROLE, "present" if netdev else "absent",
                                ("slcand pid %s" % ", ".join(str(p) for p in pids))
                                if pids else "no slcand for %s" % ROLE)
    return netdev, pids, why


def _canctl(logdir):
    """Run canctl slcan detach canx2; its output to a log in logdir.
    -> (rc or None if it could not run, the last line of its output)."""
    argv = list(CANCTL) + ["slcan", "detach", ROLE]
    log = os.path.join(logdir, "canx2_detach_%s.txt" % time.strftime("%Y%m%dT%H%M%S"))
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


def start(logdir, platform=None):
    """Detach canx2 if it is attached as SLCAN. -> hook result."""
    if not linux(platform):
        return dict(ok=True, detail="canx2: no slcan step off Linux")
    try:
        netdev, pids, before = state()
    except OSError as e:
        return dict(ok=False, detail="canx2: its state could not be read: %s" % e)
    if not netdev and not pids:
        return dict(ok=True, detail="canx2: not attached as SLCAN (%s)" % before,
                    found={"attached": False, "before": before})
    rc, last = _canctl(logdir)
    t_end = monotonic() + DETACH_WAIT_S
    while True:
        try:
            netdev, pids, after = state()
        except OSError as e:
            netdev, pids, after = True, [], "not read: %s" % e
        if not (netdev or pids) or monotonic() >= t_end:
            break
        sleep(POLL_S)
    found = {"attached": True, "before": before, "canctl_rc": rc, "after": after}
    if rc != 0:
        return dict(ok=False, detail="canx2: attached as SLCAN (%s), and canctl slcan "
                    "detach canx2 failed (%s): %s; now %s" % (before, _rc_text(rc), last,
                                                             after), found=found)
    if netdev or pids:
        return dict(ok=False, detail="canx2: still attached after canctl slcan detach "
                    "canx2 (rc 0): before %s; after %s" % (before, after), found=found)
    return dict(ok=True, detail="canx2: was attached as SLCAN (%s); detached by canctl "
                "slcan detach canx2 (rc 0); now %s" % (before, after), found=found)


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
    found["canx2"] = mine.get("found")
    return dict(ok=bool(mine.get("ok")) and bool(theirs.get("ok")),
                detail="%s; %s" % (mine.get("detail"), theirs.get("detail")), found=found)


def hooks(on_start, on_end, board=X2_BOARD, platform=None):
    """The bench suite's on_start / on_end: on `board`, start() and then
    on_start; on_end unchanged (no end step); on any other board the start
    hook given alone."""
    def start_hook(b, img, ctx, logdir):
        if b != board:
            return on_start(b, img, ctx, logdir)
        mine = start(logdir, platform)
        return _both(mine, _call(on_start, b, img, ctx, logdir))

    return start_hook, on_end
