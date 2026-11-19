"""Implementor self-test of vehicle_runner.py's open on a bench host. Not
evidence.

    python3.13 -B gen-inhibit/tools/test_vehicle_runner_roles.py

shared/bench SPEC section 8 (user, 2026-11-18): a tool asks for a bench
adapter by its canctl role, never by an enumeration index, a channel number
or the first one of its kind it finds; the reviewer ruled on 2026-11-18 that
it covers this runner on a bench host. A bench host is one with canctl at
canbench's path (CANBENCH_CANCTL, default /usr/local/sbin/canctl). Here a
fake canctl, an empty executable in this run's own scratch folder
(gi_paths.scratch("vehicle-runner-roles-test"), removed when the run ends),
is present or absent:

- absent (the truck): auto, an explicit --interface and --channel behave as
  before (the channel default can0 filled in when none is given), and --role
  is refused before anything opens;
- present: --interface auto, and any other --interface but udp_multicast,
  with or without --channel, are refused before canbench is loaded or
  anything opens, printing the --role form and that --role needs the shared
  bench guard; --role with an --interface or a --channel is refused the same
  way; udp_multicast (the virtual segment) is allowed; and --role itself is
  refused with a one-line reason, before canbench is loaded or anything
  opens, until gen-inhibit pins the shared bench guard (shared/bench
  bench_lock.guard; reviewer, 2026-11-19).

can.Bus and import_canbench are stand-ins, so nothing opens a real adapter
and no canctl runs.
"""

import os
import stat
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vehicle_runner as vr  # noqa: E402
from test_can_tools_pin import run_folder  # noqa: E402


class Opened(Exception):
    """A stand-in open was reached; stops open_and_identify there."""


def args(**kw):
    a = dict(interface="auto", bitrate=500000, channel=None, role=None)
    a.update(kw)
    return types.SimpleNamespace(**a)


class Base(unittest.TestCase):

    def setUp(self):
        d = os.path.join(run_folder("vehicle-runner-roles-test"),
                         self.id().rsplit(".", 1)[-1])
        os.makedirs(d, exist_ok=True)
        self.canctl = os.path.join(d, "canctl")
        self.events = []
        # Nothing may reach python-can or load canbench unless a test says so.
        for target, what in (("can.Bus", "can.Bus"),
                             ("vehicle_runner.import_canbench", "import_canbench")):
            p = mock.patch(target, side_effect=self.record(what))
            p.start()
            self.addCleanup(p.stop)

    def record(self, what):
        def call(*a, **kw):
            self.events.append((what,) + a)
            if what == "can.Bus":
                raise Opened(what)
        return call

    def bench(self, present):
        if present:
            with open(self.canctl, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(self.canctl, os.stat(self.canctl).st_mode | stat.S_IXUSR)
        elif os.path.exists(self.canctl):
            os.remove(self.canctl)
        p = mock.patch.dict(os.environ, {"CANBENCH_CANCTL": self.canctl})
        p.start()
        self.addCleanup(p.stop)

    def open(self, a):
        """Runner.open_and_identify on a stand-in self; returns the
        SystemExit text, or None if it reached an open."""
        me = types.SimpleNamespace(args=a)
        try:
            vr.Runner.open_and_identify(me)
        except SystemExit as e:
            return str(e)
        except Opened:
            return None
        self.fail("open_and_identify returned without opening or refusing")

    def refused(self, a, *words):
        text = self.open(a)
        self.assertIsNotNone(text, "opened instead of refusing")
        for w in words:
            self.assertIn(w, text)
        # refused before anything: no canbench load, no open
        self.assertEqual(self.events, [])
        return text


class OffBench(Base):
    """No canctl, as on the truck: nothing changes, and --role is refused."""

    def setUp(self):
        super().setUp()
        self.bench(False)

    def test_check_passes_auto_and_explicit_and_fills_the_channel(self):
        for iface, ch, want in (("auto", None, "can0"),
                                ("socketcan", None, "can0"),
                                ("kvaser", "0", "0"),
                                ("udp_multicast", None, "can0")):
            with self.subTest(iface=iface, ch=ch):
                a = args(interface=iface, channel=ch)
                self.assertIsNone(vr.check_bench_open(a))
                self.assertEqual(a.channel, want)

    def test_explicit_socketcan_opens_on_can0_by_default(self):
        self.assertIsNone(self.open(args(interface="socketcan")))
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], "can.Bus")

    def test_auto_loads_canbench_as_before(self):
        with mock.patch("vehicle_runner.import_canbench",
                        side_effect=Opened("import_canbench")):
            self.assertIsNone(self.open(args()))

    def test_role_is_refused_before_anything_opens(self):
        text = self.refused(args(role="kvaser"), "--role kvaser",
                            "not a bench host", self.canctl)
        self.assertIn("--interface", text)

    def test_stand_ins_without_role_or_channel_attributes_still_open(self):
        # The root test's stand-in args carry neither.
        a = types.SimpleNamespace(interface="socketcan", bitrate=500000)
        self.assertIsNone(vr.check_bench_open(a))
        self.assertEqual(a.channel, "can0")


class OnBench(Base):
    """canctl present: only --role opens a bench adapter."""

    def setUp(self):
        super().setUp()
        self.bench(True)

    def test_auto_is_refused_with_the_role_form(self):
        text = self.refused(args(), "bench host", self.canctl,
                            "SPEC section 8", "first adapter detected",
                            "--role <role>", "canctl roles",
                            "needs the shared bench guard", "udp_multicast")
        self.assertNotIn("takes an adapter by its channel", text)

    def test_explicit_interfaces_are_refused_with_or_without_a_channel(self):
        for iface in ("socketcan", "kvaser", "pcan", "gs_usb", "ixxat"):
            for ch in (None, "0"):
                with self.subTest(iface=iface, ch=ch):
                    self.events.clear()
                    self.refused(args(interface=iface, channel=ch),
                                 "--interface %s takes an adapter by its "
                                 "channel" % iface, "--role <role>",
                                 "SPEC section 8")

    def test_role_with_an_interface_or_a_channel_is_refused(self):
        for kw in (dict(interface="socketcan"), dict(interface="kvaser"),
                   dict(interface="udp_multicast"), dict(channel="can1"),
                   dict(interface="pcan", channel="PCAN_USBBUS1")):
            with self.subTest(**kw):
                self.events.clear()
                self.refused(args(role="kvaser", **kw), "--role kvaser",
                             "drop --interface and --channel",
                             "--role <role>")

    def test_the_virtual_segment_is_allowed(self):
        for ch in (None, "gi"):
            with self.subTest(ch=ch):
                a = args(interface="udp_multicast", channel=ch)
                try:
                    got = vr.check_bench_open(a)
                except SystemExit as e:
                    self.fail("udp_multicast refused on a bench host: %s" % e)
                self.assertIsNone(got)
                self.assertEqual(a.channel, ch or "can0")

    def test_role_is_refused_until_the_shared_guard_is_pinned(self):
        for role in ("kvaser", "canmain", "canchg", "pcanfd", "canx2"):
            with self.subTest(role=role):
                self.events.clear()
                before = {m for m in sys.modules if m.split(".")[0] == "canbench"}
                text = self.refused(args(role=role),
                                    "vehicle_runner: --role %s: needs the "
                                    "shared bench guard" % role,
                                    "bench_lock.guard")
                self.assertNotIn("\n", text, "not a one-line reason")
                after = {m for m in sys.modules if m.split(".")[0] == "canbench"}
                self.assertEqual(after, before, "canbench loaded")

    def test_the_check_refuses_role_itself(self):
        # Not only open_and_identify: the check alone refuses, so no later
        # branch can open a role.
        a = args(role="kvaser")
        try:
            vr.check_bench_open(a)
        except SystemExit as e:
            self.assertIn("needs the shared bench guard", str(e))
        else:
            self.fail("check_bench_open let --role through on a bench host")
        self.assertIsNone(a.channel)


class CanctlPath(unittest.TestCase):

    def test_canbench_default_and_setting(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CANBENCH_CANCTL", None)
            self.assertEqual(vr.canctl_path(), "/usr/local/sbin/canctl")
        with mock.patch.dict(os.environ, {"CANBENCH_CANCTL": "/x/canctl"}):
            self.assertEqual(vr.canctl_path(), "/x/canctl")


if __name__ == "__main__":
    unittest.main()
