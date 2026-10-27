# Item 3 debug: the rx-order keep-alive (0x18FF5AC0, SA 0xC0) was counted by
# the board (c2v +1) but never seen by the Kvaser. This sends keep-alives
# from the Innomaker every 0.5 s for 6 s and records EVERYTHING the Kvaser
# (normal mode) and the PCAN (listen-only) receive, and the board's serial
# lines, so it shows whether the vehicle port went normal and what reached
# the vehicle segment. COM3 is opened with DTR/RTS low (a close then does not
# reset the X2). Usage: py -3.14 keepalive_probe.py > keepalive_probe.txt
import collections
import os
import sys
import threading
import time

sys.path.insert(0, ".")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

import can
import serial
import bench_charger_witness as bcw

KA = 0x18FF5AC0
lines = []
ser = serial.Serial()
ser.port, ser.baudrate, ser.timeout = "COM3", 115200, 0.2
ser.dtr, ser.rts = False, False
ser.open()
stop = threading.Event()


def tap():
    buf = b""
    while not stop.is_set():
        buf += ser.read(512)
        while b"\n" in buf:
            ln, _, buf = buf.partition(b"\n")
            lines.append((time.perf_counter(),
                          ln.decode("ascii", "replace").rstrip()))


def drain(bus, name, out):
    while not stop.is_set():
        m = bus.recv(timeout=0.05)
        if m is not None:
            out.append((time.perf_counter(), name, m))


tx = bcw.open_gs_usb()
kv = can.Bus(interface="kvaser", channel=0, bitrate=500000)
pc = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000,
             state=can.BusState.PASSIVE)
got = []
ths = [threading.Thread(target=tap, daemon=True),
       threading.Thread(target=drain, args=(kv, "kvaser", got), daemon=True),
       threading.Thread(target=drain, args=(pc, "pcan", got), daemon=True),
       threading.Thread(target=drain, args=(tx, "innomaker", got), daemon=True)]
for t in ths:
    t.start()
time.sleep(2.0)
t0 = time.perf_counter()
print("t0 %.6f; sending 0x%X every 0.5 s for 6 s" % (t0, KA))
sent = 0
while time.perf_counter() - t0 < 6.0:
    tx.send(can.Message(arbitration_id=KA, is_extended_id=True,
                        data=bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])), timeout=0.2)
    sent += 1
    time.sleep(0.5)
time.sleep(3.0)
stop.set()
time.sleep(0.3)
print("sent %d" % sent)
cnt = collections.Counter()
for h, name, m in got:
    cnt[(name, "%X" % m.arbitration_id, m.is_extended_id,
         getattr(m, "is_rx", None))] += 1
for k, v in sorted(cnt.items()):
    print("  %-9s id %-9s ext %-5s is_rx %-5s x%d" % (k + (v,)))
print("first frames per adapter (host t - t0):")
seen = set()
for h, name, m in got:
    if name not in seen:
        seen.add(name)
        print("  %s %.3f %X %s" % (name, h - t0, m.arbitration_id, m.data.hex()))
print("serial lines from t0-1 s:")
for h, ln in lines:
    if h >= t0 - 1.0 and ("vehicle port" in ln or "MODE" in ln or "LOSS" in ln
                          or ln.startswith("t=") or ln.startswith("[")):
        print("  %+.3f %s" % (h - t0, ln[:200]))
for b in (tx, kv, pc):
    b.shutdown()
ser.close()
