"""Harness test for can2_ack_probe.py: parse_link, err_kinds, judge_probe, check, and probe()
on a fake `can`, a fake canctl and fake link readings. No hardware, no root, no lock.

Every verdict has a case that must come out the other way. Gen-inhibit tester, 2026-11-13.

    py -3.13 -B gen-inhibit/tools/bench/test_can2_ack_probe.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import can2_ack_probe as AP                                    # noqa: E402

bad = []


def check(group, label, ok, got=""):
    print("  %-4s %-7s %-62s %s" % ("ok" if ok else "FAIL", group, label[:62], got))
    if not ok:
        bad.append(label)


def ip_json(state="ERROR-ACTIVE", tx=0, warn=0, passive=0, oper="UP"):
    """`ip -d -s -j link show canchg` as madhouse's gs_usb gives it (fields read 2026-11-13)."""
    return [{"operstate": oper, "stats64": {"rx": {"packets": 0}, "tx": {"packets": tx}},
             "linkinfo": {"info_kind": "can",
                          "info_data": {"ctrlmode_supported": ["LOOPBACK", "LISTEN-ONLY", "CC-LEN8-DLC"],
                                        "state": state},
                          "info_xstats": {"restarts": 0, "bus_error": 0, "arbitration_lost": 0,
                                          "error_warning": warn, "error_passive": passive,
                                          "bus_off": 0}}}]


def L(**kw):
    return AP.parse_link(ip_json(**kw))


ACK_ERR = (AP.CAN_ERR_FLAG | AP.CAN_ERR_ACK, bytes(8))
TX_PASSIVE = (AP.CAN_ERR_FLAG | AP.CAN_ERR_CRTL, bytes([0, AP.CRTL_TX_PASSIVE, 0, 0, 0, 0, 0, 0]))
RX_WARN = (AP.CAN_ERR_FLAG | AP.CAN_ERR_CRTL, bytes([0, 0x04, 0, 0, 0, 0, 0, 0]))


def main():
    got = L(tx=7, warn=1, state="ERROR-PASSIVE", oper="DOWN")
    check("parse", "parse_link reads every field", got == {
        "operstate": "DOWN", "state": "ERROR-PASSIVE", "tx_packets": 7, "error_warning": 1,
        "error_passive": 0, "bus_error": 0}, got)
    got = AP.parse_link([{}])
    check("parse", "parse_link on nothing gives None fields", got["tx_packets"] is None and got["state"] is None, got)
    got = AP.err_kinds([ACK_ERR, TX_PASSIVE, RX_WARN])
    check("kinds", "an ACK error, a TX passive, an RX warning", got == {"ack": 1, "tx_warn_or_passive": 1, "other": 1}, got)
    for label, before, after, errs, want in (
            ("confirmed, nothing else -> ACKED", L(tx=3), L(tx=4), [], "ACKED"),
            ("confirmed with an ACK error -> not ACKED", L(tx=3), L(tx=4), [ACK_ERR], "UNCLEAR"),
            ("not confirmed, ACK-error frames -> NO_ACK", L(), L(), [ACK_ERR, ACK_ERR], "NO_ACK"),
            ("not confirmed, TX passive frame -> NO_ACK", L(), L(), [TX_PASSIVE], "NO_ACK"),
            ("not confirmed, error_passive rose -> NO_ACK", L(), L(passive=1), [], "NO_ACK"),
            ("not confirmed, error_warning rose -> NO_ACK", L(), L(warn=1), [], "NO_ACK"),
            ("not confirmed, state ERROR-PASSIVE -> NO_ACK", L(), L(state="ERROR-PASSIVE"), [], "NO_ACK"),
            ("not confirmed, an RX warning only -> UNCLEAR", L(), L(), [RX_WARN], "UNCLEAR"),
            ("not confirmed, nothing counted -> UNCLEAR", L(), L(), [], "UNCLEAR"),
            ("no tx counter -> UNCLEAR", AP.parse_link([{}]), AP.parse_link([{}]), [ACK_ERR], "UNCLEAR")):
        got = AP.judge_probe(before, after, errs)
        check("judge", label, got["verdict"] == want, "%s %s" % (got["verdict"], got["why"]))
    for expect, verdict, want in (("no_ack", "NO_ACK", "PASS"), ("no_ack", "ACKED", "FAIL"),
                                  ("acked", "ACKED", "PASS"), ("acked", "NO_ACK", "FAIL"),
                                  ("no_ack", "UNCLEAR", "INCONCLUSIVE"), ("acked", "UNCLEAR", "INCONCLUSIVE")):
        got = AP.check(expect, verdict)
        check("check", "expect %s, probe %s" % (expect, verdict), got == want, got)
    probe_cases()
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL: " + "; ".join(bad)))
    return 1 if bad else 0


class FakeBus:
    """A socketcan bus: send() records, recv() hands out `rx` once, then None."""
    made = []

    def __init__(self, interface=None, channel=None, **kw):
        self.interface, self.channel, self.sent, self.closed = interface, channel, [], False
        self.rx = list(FakeBus.rx_next)
        FakeBus.made.append(self)

    def send(self, m):
        if FakeBus.send_raises:
            raise OSError("fake send failure")
        self.sent.append(m)

    def recv(self, timeout=None):
        # what the link says about the probe frame comes only after it was sent
        return self.rx.pop(0) if self.rx and self.sent else None

    def shutdown(self):
        self.closed = True


class Msg:
    def __init__(self, arbitration_id=0, is_extended_id=False, data=b"", is_error_frame=False):
        self.arbitration_id, self.data, self.is_error_frame = arbitration_id, data, is_error_frame


def run_probe(links, rx=(), up_rc=0, send_raises=False):
    """probe() with the fakes. links: what link() returns, in order. -> (rec, canctl calls, bus)."""
    calls, seq = [], list(links)
    fake = types.ModuleType("can")
    fake.Bus, fake.Message = FakeBus, Msg
    FakeBus.made, FakeBus.rx_next, FakeBus.send_raises = [], list(rx), send_raises
    saved = (sys.modules.get("can"), AP.canctl, AP.link)
    sys.modules["can"] = fake
    AP.canctl = lambda *a: calls.append(" ".join(a)) or {"argv": "canctl " + " ".join(a),
                                                        "rc": up_rc if a[0] == "up" else 0, "out": ""}
    AP.link = lambda role: seq.pop(0) if seq else None
    try:
        rec = AP.probe("canchg", observe_s=0.05)
    except Exception as e:      # noqa: BLE001  reported by the case
        rec = {"raised": repr(e)}
    finally:
        if saved[0] is None:
            sys.modules.pop("can", None)
        else:
            sys.modules["can"] = saved[0]
        AP.canctl, AP.link = saved[1], saved[2]
    return rec, calls, (FakeBus.made[0] if FakeBus.made else None)


def probe_cases():
    down = L(oper="DOWN", state="STOPPED")
    rec, calls, bus = run_probe([L(oper="UP")])
    check("probe", "a link already up is refused, canctl never called",
          "refused" in rec and calls == [] and bus is None, (rec.get("refused"), calls))
    rec, calls, bus = run_probe([down, L(), L(state="ERROR-PASSIVE", passive=1), down],
                                rx=[Msg(AP.CAN_ERR_ACK, data=bytes(8), is_error_frame=True)])
    check("probe", "no ACK: up 500000 normal, one 0x7A1, down; NO_ACK",
          calls == ["up canchg 500000", "down canchg"] and bus and len(bus.sent) == 1
          and bus.sent[0].arbitration_id == AP.PROBE_ID and bus.closed
          and rec.get("judge", {}).get("verdict") == "NO_ACK", (calls, rec.get("judge")))
    rec, calls, bus = run_probe([down, L(), L(), down],
                                rx=[Msg(AP.CAN_ERR_ACK, data=bytes(8), is_error_frame=True)])
    check("probe", "no ACK seen only as an ACK-error frame -> NO_ACK",
          rec.get("judge", {}).get("kinds", {}).get("ack") == 1
          and rec.get("judge", {}).get("verdict") == "NO_ACK", rec.get("judge"))
    rec, calls, bus = run_probe([down, L(), L(), down], rx=[Msg(0x7A5, data=bytes(8), is_error_frame=False)])
    check("probe", "a data frame heard is counted, not an error -> UNCLEAR",
          rec.get("frames_seen") == 1 and rec.get("judge", {}).get("verdict") == "UNCLEAR",
          (rec.get("frames_seen"), rec.get("judge")))
    check("probe", "the up is normal mode: no listen-only, no berr", "listen-only" not in calls[0]
          and "berr" not in calls[0], calls[:1])
    rec, calls, bus = run_probe([down, L(tx=0), L(tx=1), down])
    check("probe", "ACKed: confirmed -> ACKED", rec.get("judge", {}).get("verdict") == "ACKED",
          rec.get("judge"))
    rec, calls, bus = run_probe([down, L(), L(), down], send_raises=True)
    check("probe", "a send that raises still brings the link down",
          calls == ["up canchg 500000", "down canchg"] and bus.closed and "raised" in rec, (calls, rec))
    rec, calls, bus = run_probe([down, down], up_rc=2)
    check("probe", "a failed up is refused and the link is brought down",
          rec.get("refused") == "canctl up failed" and calls == ["up canchg 500000", "down canchg"]
          and bus is None, (rec.get("refused"), calls))


if __name__ == "__main__":
    sys.exit(main())
