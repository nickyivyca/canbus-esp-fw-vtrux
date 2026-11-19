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
  anything opens, printing the --role form; --role with an --interface or a
  --channel is refused the same way; udp_multicast (the virtual segment) is
  allowed; --role opens through canbench.bench_roles.open_role, after the
  pinned canbench is loaded, with the run's bitrate and
  receive_own_messages=False (and auto_reset for a PCAN role), and a
  RoleError ends the run with its reason.

canbench.bench_roles and can.Bus are stand-ins, so nothing opens a real
adapter and no canctl runs.
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


def fake_bench_roles(interface="socketcan", mode="socketcan", error=None,
                     events=None):
    """A stand-in canbench package with bench_roles: resolve() gives a role
    of that interface and mode, open_role() records its call and returns a
    marker bus; error, if given, is raised by resolve() as a RoleError."""
    events = events if events is not None else []
    pkg = types.ModuleType("canbench")
    br = types.ModuleType("canbench.bench_roles")

    class RoleError(RuntimeError):
        pass

    def resolve(role):
        events.append(("resolve", role))
        if error:
            raise RoleError(error)
        return types.SimpleNamespace(interface=interface, mode=mode)

    def open_role(role, **kw):
        events.append(("open_role", role, kw))
        return "BUS:" + role

    br.RoleError, br.resolve, br.open_role = RoleError, resolve, open_role
    pkg.bench_roles = br
    return {"canbench": pkg, "canbench.bench_roles": br}, events


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
                self.assertIs(vr.check_bench_open(a), False)
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
        self.assertIs(vr.check_bench_open(a), False)
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
                            "udp_multicast")
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
                self.assertIs(got, False)
                self.assertEqual(a.channel, ch or "can0")

    def test_role_goes_through_open_by_role_and_nothing_else(self):
        def fake(*a):
            self.events.append(("open_by_role",) + a)
            raise Opened("open_by_role")
        with mock.patch("vehicle_runner.open_by_role", side_effect=fake):
            self.assertIsNone(self.open(args(role="kvaser")))
        self.assertEqual(self.events, [("open_by_role", "kvaser", 500000)])

    def test_check_says_open_by_role(self):
        a = args(role="canchg")
        self.assertIs(vr.check_bench_open(a), True)


class OpenByRole(Base):
    """open_by_role on the stand-in bench_roles."""

    def setUp(self):
        super().setUp()
        self.bench(True)

    def call(self, role, **fake):
        mods, ev = fake_bench_roles(events=self.events, **fake)
        with mock.patch.dict(sys.modules, mods):
            return vr.open_by_role(role, 250000)

    def test_opens_through_open_role_after_loading_the_pin(self):
        bus, desc = self.call("kvaser", interface="socketcan", mode="socketcan")
        self.assertEqual(bus, "BUS:kvaser")
        self.assertIn("role kvaser", desc)
        self.assertIn("socketcan", desc)
        names = [e[0] for e in self.events]
        self.assertEqual(names, ["import_canbench", "resolve", "open_role"])
        self.assertEqual(self.events[2], ("open_role", "kvaser",
                                          {"bitrate": 250000,
                                           "receive_own_messages": False}))
        self.assertIn("--role", self.events[0][1])

    def test_a_pcan_role_gets_auto_reset(self):
        self.call("pcanfd", interface="pcan", mode="pcan")
        self.assertEqual(self.events[-1][2],
                         {"bitrate": 250000, "receive_own_messages": False,
                          "auto_reset": True})

    def test_a_gs_usb_role_does_not(self):
        self.call("canmain", interface="gs_usb", mode="libusb")
        self.assertNotIn("auto_reset", self.events[-1][2])

    def test_a_role_error_ends_the_run_with_its_reason(self):
        try:
            self.call("kvaser", error="netdev kvaser is down")
        except SystemExit as e:
            self.assertIn("--role kvaser: netdev kvaser is down", str(e))
        except Exception as e:      # escaped as a traceback, not a one-line exit
            self.fail("a RoleError was not turned into an exit: %r" % e)
        else:
            self.fail("a RoleError did not end the run")
        self.assertNotIn("open_role", [e[0] for e in self.events])


class CanctlPath(unittest.TestCase):

    def test_canbench_default_and_setting(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CANBENCH_CANCTL", None)
            self.assertEqual(vr.canctl_path(), "/usr/local/sbin/canctl")
        with mock.patch.dict(os.environ, {"CANBENCH_CANCTL": "/x/canctl"}):
            self.assertEqual(vr.canctl_path(), "/x/canctl")


if __name__ == "__main__":
    unittest.main()
