"""A fake python-can gs_usb bus on a libusb role, for host tests: its echoes fill unless they are read.

Gen-inhibit tester, 2026-11-19 (the gen-inhibit reviewer's ruling (C) on the echo stall). On the bench
canmain's gs_usb returned every completed frame as an echo (python-can: is_rx False) and, with 257 of them
unread, took no more frames: send 258 blocked for pyusb's 1 s write timeout and raised CanOperationError
(short bench check of 2026-11-19, gen-inhibit runs/benchcheck_9950f96_20261119T015215/). This fake does the
same with a cap of its own (no 1 s wait): each send while `acked(n)` is true queues one echo, and a send with
`cap` echoes unread raises `error` instead. recv(timeout) returns the oldest queued message or waits out its
timeout (python-can's gs_usb waits at least 1 ms). shutdown() records whether canbench's echo drain on the
bus (bench_layer.open_bench(drain=True): bus.bench_drain["running"]) was still reading, and after it recv
raises, as a freed pyusb handle would. drained(bus) starts the pinned canbench's drain on a fake, as
open_bench(drain=True) does on a real bus (from can-tools 7a27cc9, the gen-inhibit reviewer 2026-11-19).
"""
import threading
import time


class Msg:
    def __init__(self, arbitration_id=0, data=b"", is_rx=True, is_error_frame=False, timestamp=0.0):
        self.arbitration_id, self.data = arbitration_id, bytes(data)
        self.is_rx, self.is_error_frame, self.timestamp = is_rx, is_error_frame, timestamp


class StallError(Exception):
    """Stands for can.CanOperationError("The message could not be sent")."""


class StallingGs:
    def __init__(self, cap=256, acked=lambda n: True, error=StallError, on_send=None, log=None):
        self.cap, self.acked, self.error, self.on_send = cap, acked, error, on_send
        self.log = log if log is not None else []
        self.q, self.lock = [], threading.Lock()
        self.sent = self.stalls = 0
        self.closed = False
        self.drain_alive_at_shutdown = None

    def unread_echoes(self):
        with self.lock:
            return sum(1 for m in self.q if m.is_rx is False)

    def send(self, msg, timeout=None):
        if self.closed:
            raise self.error("send after shutdown")
        if self.unread_echoes() >= self.cap:
            self.stalls += 1
            self.log.append(("stall", self.sent))
            raise self.error("The message could not be sent")
        n = self.sent
        self.sent += 1
        if self.on_send is not None:
            self.on_send(msg)
        if self.acked(n):
            with self.lock:
                self.q.append(Msg(msg.arbitration_id, msg.data, is_rx=False, timestamp=time.perf_counter()))

    def put(self, msg):
        """A frame from another node (is_rx True) or an error frame, as the device would pass it up."""
        with self.lock:
            self.q.append(msg)

    def recv(self, timeout=None):
        end = time.perf_counter() + max(timeout or 0.0, 0.001)
        while True:
            if self.closed:
                raise self.error("recv after shutdown")
            with self.lock:
                if self.q:
                    return self.q.pop(0)
            if time.perf_counter() >= end:
                return None
            time.sleep(0.0002)

    def shutdown(self):
        d = getattr(self, "bench_drain", None)
        self.drain_alive_at_shutdown = bool(d is not None and d.get("running"))
        self.log.append(("shutdown", self.drain_alive_at_shutdown))
        self.closed = True


def drained(bus, role="canmain", kb=None):
    """The pinned canbench's drain on `bus`, as bench_layer.open_bench(drain=True) starts it (its _start_drain,
    reached through kvaser_bus.layer(), which loads canbench from the pin); bus.vtrux_role set as kvaser_bus's
    open sets it. Returns the bus."""
    if kb is None:
        import importlib.util
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location("kvaser_bus_fake_gs", os.path.join(here, "kvaser_bus.py"))
        kb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kb)
    bus.vtrux_role = role
    kb.layer()._start_drain(bus, role)
    return bus
