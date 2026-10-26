#!/usr/bin/env python3
"""Tests the diag reads moved onto gi_diag (toolkit item 10, 2026-10-24) that no other
test reaches, with no bus and no device:

  1. tec_busoff_sweep.judge_bus_on: the wire half reads bus_on (diag_flags bit1) by name
     on each 0x7F1's schema; a clear bit is listed, a set one is not, an unknown schema
     raises, and a record's identity naming another DBC is refused.
  2. bench_census.census: an adapter's diag pages are decoded in the order received, on
     the schema the latest schema page states; a page before the first schema page is
     "undecodable", never decoded; a page on another schema than the run identity's
     refuses the adapter's decode, keeping its raw census.
  3. diag_flags_trace.flag_rows: diag_flags of each 0x7F1 by name, through the decoder.

    py -3.14 -B test_diag_readers.py      # exit 0 only if every check passes
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_diag  # noqa: E402

FAILS = []
S = gi_diag.schemas_for()
V = S.head_version


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def raises(fn, cls, text=""):
    try:
        fn()
    except cls as e:
        return text in str(e)
    except Exception:       # noqa: BLE001
        return False
    return False


def f7f1(flags, schema=None):
    return "%02x0103%02x00ff0000" % (V if schema is None else schema, flags)


def t_tec():
    import tec_busoff_sweep as TB
    snap_run = {"drv_present": True, "drv_state": "running", "bus_on": True}
    snap_off = {"drv_present": True, "drv_state": "bus_off", "bus_on": False}
    base = {"baseline": {"samples": [{"pt": 1.0, "snap": snap_run}, {"pt": 2.0, "snap": snap_off},
                                     {"pt": 3.0, "snap": snap_run}]}}
    rec = dict(base, wire_0x7F1=[(0.5, f7f1(0x06)), (2.5, f7f1(0x04)), (3.5, f7f1(0x06))])
    out = TB.judge_bus_on(rec)
    check("tec: a 0x7F1 with bus_on clear is listed, the set ones are not",
          [c["pt"] for c in out["wire_bit1_clear"]] == [2.5] and out["wire_0x7F1"] == 3, out["wire_bit1_clear"])
    check("tec: the page half still judges (PASS)", out["verdict"] == "PASS", out.get("summary"))
    rec = dict(base, wire_0x7F1=[(0.5, f7f1(0x06, 0xEE))])
    check("tec: a 0x7F1 on a schema no DBC states raises", raises(lambda: TB.judge_bus_on(rec), gi_diag.UnknownSchema))
    rec = dict(base, wire_0x7F1=[(0.5, f7f1(0x06))], decoder={"dbc_hash": "0" * 16, "dbc_commit": "0" * 40})
    check("tec: a record whose identity names another DBC is refused",
          raises(lambda: TB.judge_bus_on(rec), gi_diag.SchemaMismatch))


class Msg(object):
    def __init__(self, t, i, data, err=False):
        self.timestamp, self.arbitration_id, self.data, self.is_error_frame = t, i, bytes(data), err


class FakeBus(object):
    def __init__(self, msgs):
        self.msgs = list(msgs)

    def recv(self, timeout=None):
        return self.msgs.pop(0) if self.msgs else None

    def shutdown(self):
        pass


def t_census():
    import bench_census as BC
    msgs = [Msg(0.0, 0x7F2, bytes([9] + [0] * 7)), Msg(0.1, 0x7F1, bytes.fromhex(f7f1(0x26))),
            Msg(0.2, 0x7F2, bytes([5] + [0] * 7)), Msg(0.3, 0x051, bytes(6)),
            Msg(0.4, 0x7F8, bytes(8)), Msg(0.5, 0x7F4, bytes([1] * 8))]
    real = BC.open_adapter
    try:
        BC.open_adapter = lambda name: (FakeBus(msgs), "fake")
        r = BC.census("fake", 0.2, S)
        w = r["wican_last_decoded"]
        check("census: 0x7F2 after the 0x7F1 decodes on its schema (tx_ok 5, the last one)",
              isinstance(w.get("0x7F2"), dict) and w["0x7F2"]["diag_tx_ok"] == 5, w.get("0x7F2"))
        check("census: 0x7F1 decoded by name (diag_flags 0x26)", w.get("0x7F1", {}).get("diag_flags") == 0x26, w.get("0x7F1"))
        check("census: the 0x7F2 before the first schema page was counted undecodable",
              r["wican_decode"]["undecodable"] == 1 and r["wican_decode"]["refused"] is None, r["wican_decode"])
        check("census: the interposer page stays raw", r["x2_last_raw"].get("0x7F4") == "01" * 8, r["x2_last_raw"])
        first = [Msg(0.0, 0x7F2, bytes([9] + [0] * 7))]
        BC.open_adapter = lambda name: (FakeBus(first), "fake")
        r = BC.census("fake", 0.2, S)
        check("census: a lone page before any schema page is 'undecodable', never decoded",
              str(r["wican_last_decoded"].get("0x7F2", "")).startswith("undecodable"), r["wican_last_decoded"])
        other = {"dbc_hash": "0" * 16, "dbc_commit": "0" * 40}
        BC.open_adapter = lambda name: (FakeBus(msgs), "fake")
        r = BC.census("fake", 0.2, S, other)
        check("census: a page on another schema than the identity's refuses the decode, keeping the raw census",
              r["wican_decode"]["refused"] and "SchemaMismatch" in r["wican_decode"]["refused"]
              and r["wican_last_decoded"] == {} and r["frames"] == 6, (r["wican_decode"], r["frames"]))
    finally:
        BC.open_adapter = real


def t_trace():
    import diag_flags_trace as DT
    frames = [(0.0, 0x7F2, bytes(8)), (0.1, 0x7F1, bytes.fromhex(f7f1(0x66))), (0.2, 0x7F1, bytes.fromhex(f7f1(0x06)))]
    check("diag_flags_trace: diag_flags by name per 0x7F1",
          DT.flag_rows(frames, gi_diag.Decoder(S)) == [(0.1, 0x66), (0.2, 0x06)])
    check("diag_flags_trace: an unknown schema raises",
          raises(lambda: DT.flag_rows([(0.0, 0x7F1, bytes.fromhex(f7f1(0, 0xEE)))], gi_diag.Decoder(S)),
                 gi_diag.UnknownSchema))


def main():
    for t in (t_tec, t_census, t_trace):
        try:
            t()
        except Exception as e:      # noqa: BLE001
            check("%s ran" % t.__name__, False, "CRASH %s: %s" % (type(e).__name__, e))
    print("\n%d FAIL" % len(FAILS) if FAILS else "\nall checks pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
