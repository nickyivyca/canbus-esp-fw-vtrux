"""Two cases for wait_for_wire_quiet, run on a fake clock in milliseconds.

They exist because this check was written twice and wrong twice, and neither flaw was
a syntax error, so compiling proved nothing. The first version compared the newest
witnessed frame's timestamp against the newest 0x051's, which CANNOT detect quiet: on
a silent bus nothing new is appended, so the difference stops growing, the wait runs to
its cap, and it reports possible mid-traffic on an idle wire. Case 1 below fails
against that version and passes against the current one.

    py -3.11 test_wait_for_wire_quiet.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e4_device_calibration as e4   # noqa: E402

CMD = e4.CMD_ID
OTHER = 0x400


class FakeClock(object):
    """A clock that only advances when sleep() is called, so tests are instant."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.t += d


class FakeWitness(object):
    def __init__(self, frames=None):
        self.frames = list(frames or [])


def case_silent_bus_is_quiet():
    """A bus that stops entirely must satisfy quiet.

    This is the case the first implementation got wrong: with no new frames the
    witness's own timestamps stop advancing, so any test comparing two of them
    freezes below the threshold for ever.
    """
    clk = FakeClock()
    # Traffic up to witness time 90.0, then nothing at all.
    w = FakeWitness([(89.8, OTHER, b"", False, False),
                     (89.9, CMD, b"", False, False),
                     (90.0, OTHER, b"", False, False)])
    quiet, last_ts, last_host, t0 = e4.wait_for_wire_quiet(
        w, quiet_s=0.2, cap_s=6.0, clock=clk, sleep=clk.sleep)
    assert quiet, "a silent bus must read as quiet (it did not: the old bug)"
    waited = clk() - t0
    assert waited < 1.0, "should settle in about quiet_s, waited %.2f" % waited
    assert last_ts == 90.0, last_ts
    print("  ok  silent bus reads quiet after %.2f s" % waited)


def case_busy_bus_is_not_quiet():
    """A bus still carrying 0x051 must NOT read as quiet, and must hit the cap."""
    clk = FakeClock()
    w = FakeWitness([(90.0, CMD, b"", False, False)])
    state = {"ts": 90.0}

    def sleep_and_append(d):
        clk.sleep(d)
        # A new command arrives on every tick, so quiet is never reached.
        state["ts"] += d
        w.frames.append((state["ts"], CMD, b"", False, False))

    quiet, _, _, t0 = e4.wait_for_wire_quiet(
        w, quiet_s=0.2, cap_s=1.0, clock=clk, sleep=sleep_and_append)
    assert not quiet, "a bus still carrying 0x051 must not read as quiet"
    assert clk() - t0 >= 1.0, "should have run to the cap"
    print("  ok  busy bus does not read quiet, ran to the cap")


def case_no_commands_at_all():
    """No 0x051 ever seen: must not claim quiet, because it never saw the thing it
    is waiting to fall silent. Claiming quiet here would pass an arm whose witness
    recorded nothing."""
    clk = FakeClock()
    w = FakeWitness([(90.0, OTHER, b"", False, False)])
    quiet, _, _, _ = e4.wait_for_wire_quiet(
        w, quiet_s=0.2, cap_s=0.5, clock=clk, sleep=clk.sleep)
    assert not quiet, "must not claim quiet without having seen a single 0x051"
    print("  ok  no 0x051 ever seen does not read as quiet")


def main():
    print("wait_for_wire_quiet:")
    case_silent_bus_is_quiet()
    case_busy_bus_is_not_quiet()
    case_no_commands_at_all()
    print("3 cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
