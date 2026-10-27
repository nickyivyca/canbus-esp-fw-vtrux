# Step 4 item 5 (plan 4a): verify boot-mid-session's board reset on the bench
# BEFORE any scored run uses it. The reset is run_scenario's own:
# SerialTap.reset_board() (esptool HardReset over USB-Serial/JTAG, RTS 0.2 s).
# Answers the plan's four questions: does the port re-enumerate (SerialTap
# marks SERIAL LOST / BACK), how long is the serial gap, does the boot banner
# survive (a boot line on serial after the reset), does 0x7F7's uptime drop
# (>= 2 min before, 0 after).
#
# Bench: the PCAN in NORMAL mode on CAN1 reads 0x7F4-0x7F7 and acknowledges
# (so the power-up round after the reset completes); the Innomaker sends the
# 2.3 keep-alive 0x18FF5AC0 (extended, SA 0xC0, no DBC defines it) every
# 100 ms so the vehicle port is normal and the 1 Hz status frames flow.
# Usage: py -3.14 reset_check.py [--serial-log FILE] > reset_check.txt
# Moved from reverse-it's runs/step4_nohands_20261014T1730/ into git with
# the bench suite's scripts (port step 0, 2026-10-26). The diag DBC is
# the git copy (paths.diag_dbc(); the SeaDrive copy it read before, schema
# 3, differs from it in the version line, comments and attributes, with
# every signal's layout and scaling the same), and the serial log goes to
# --serial-log, by default where it went before (that run folder).
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# bench_drivers/ (bench_charger_witness) first, then charge-interposer/
# (paths, run_scenario): both found from this file.
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import can
import cantools
import bench_charger_witness as bcw
from run_scenario import SerialTap, BOOT_LINE
import paths

DBC = paths.diag_dbc()
_ap = argparse.ArgumentParser()
_ap.add_argument("--serial-log", default=None,
                 help="the board's serial log; default where it went before "
                      "the move, reverse-it's runs/step4_nohands_20261014T1730/")
SERIAL_LOG = _ap.parse_args().serial_log or os.path.join(
    paths.fixtures(), "runs", "step4_nohands_20261014T1730",
    "reset_check.serial.log")
KA = 0x18FF5AC0
db = cantools.database.load_file(DBC)
stop = threading.Event()
frames = []                       # (host t, id, data)

tap = SerialTap("COM3", 115200, SERIAL_LOG)
tap.start()
pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
tx = bcw.open_gs_usb()


def keepalive():
    while not stop.is_set():
        try:
            tx.send(can.Message(arbitration_id=KA, is_extended_id=True,
                                data=bytes([0xA5] + [0] * 7)), timeout=0.05)
        except Exception:                                  # noqa: BLE001
            pass
        m = tx.recv(timeout=0.0)
        while m is not None:
            m = tx.recv(timeout=0.0)
        time.sleep(0.1)


def pcan_rx():
    while not stop.is_set():
        m = pc.recv(timeout=0.1)
        if m is not None and 0x7F4 <= m.arbitration_id <= 0x7F7:
            frames.append((time.perf_counter(), m.arbitration_id,
                           bytes(m.data)))


ths = [threading.Thread(target=f, daemon=True) for f in (keepalive, pcan_rx)]
for t in ths:
    t.start()


def uptime():
    u = [db.decode_message(0x7F7, d)["intp_uptime_min"]
         for _h, i, d in frames if i == 0x7F7]
    return u[-1] if u else None


t0 = time.perf_counter()
print("waiting for 0x7F7 uptime >= 2 min (up to 200 s)")
while time.perf_counter() - t0 < 200 and (uptime() or 0) < 2:
    time.sleep(1.0)
pre_up = uptime()
print("0x7F7 uptime before the reset: %s min (%.0f s waited)"
      % (pre_up, time.perf_counter() - t0))
tap.reset_board()
time.sleep(25.0)
stop.set()
for t in ths:
    t.join(timeout=2.0)
tap.stop()
pc.shutdown()
tx.shutdown()

rh = tap.reset_host_t
print("reset sent at host %s" % ("%.6f" % rh if rh else "NEVER"))
rows = []
with open(tap.stamped_path, encoding="utf-8", errors="replace") as f:
    for ln in f:
        h, _, text = ln.rstrip("\n").partition("\t")
        rows.append((float(h), text))
after = [(h, x) for h, x in rows if rh and h >= rh]
marks = [(h - rh, x) for h, x in after if x.startswith("# ")]
boots = [(h - rh, x) for h, x in after if BOOT_LINE.search(x)]
print("serial markers after the reset:")
for dt, x in marks:
    print("  %+.3f s  %s" % (dt, x))
print("boot lines after the reset: %d" % len(boots))
for dt, x in boots[:4]:
    print("  %+.3f s  %s" % (dt, x[:100]))
first_status = next(((h - rh, x) for h, x in after
                     if x.startswith("t=")), None)
print("first status line after it: %s" % (first_status,))
post = [(h - rh, db.decode_message(0x7F7, d)["intp_uptime_min"])
        for h, i, d in frames if i == 0x7F7 and rh and h > rh]
print("0x7F7 after the reset (s, uptime min): %s" % post[:5])
rnd = sorted((h - rh, i) for h, i, _d in frames if rh and h > rh)[:8]
print("first status frames after the reset (s, id): %s"
      % ", ".join("%.3f:%X" % r for r in rnd))
gap = [x for _dt, x in marks if x.startswith("# SERIAL BACK")]
ok = (rh is not None and bool(boots) and pre_up is not None and pre_up >= 2
      and bool(post) and post[0][1] == 0 and not any(
          "NOT BACK" in x for _d, x in marks))
print("RESET PROVEN: %s (boot line %s; uptime %s -> %s; serial %s)"
      % ("YES" if ok else "NO", "yes" if boots else "NO", pre_up,
         post[0][1] if post else "unseen",
         gap[0][2:] if gap else "never lost"))
