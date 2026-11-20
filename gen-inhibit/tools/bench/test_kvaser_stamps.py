"""Harness test: the Kvaser's own hardware stamps on SocketCAN, and nothing judged on host time.

Gen-inhibit tester, 2026-11-18, for the gen-inhibit reviewer's ruling of that day: the Kvaser on
kvaser_usb keeps its witness roles, the hw-time judges included, on its own SO_TIMESTAMPING raw
stamps, read by canbench's SocketcanHwBus (can-tools 5952c0c). Every open checks its stamps
(bus.stamp_check: nonzero, advancing, on the device clock); the stamps are fitted via device_fit
with the residual reported; otherwise host stamps, and the witness clock refuses, saying which.
Error frames carry no hardware stamp on kvaser_usb, so they are placed by host time only.

What is checked, with no hardware (a fake SocketcanHwBus: messages with hw_timestamp and
host_timestamp, and a stamp_stats() result):
  witness_clock   raw_stamp / receipt on a canbench message and a CANlib one; kvaser_times:
                  None off SocketCAN; the data frames mapped onto the epoch clock (not their
                  receipt times) only with the check "hardware", a clean after-check, every
                  kept frame stamped and a fit that holds; error frames on receipt time; every
                  other case receipt times and a note it refuses (pending, host_only, no
                  stamp_stats, a frame that lost its stamp or went backward after the check, a
                  kept frame with none, too few frames to fit, error frames alone)
  witnesses       interlock_bench (B20/B25) and x2_jam_cases (B5/B6) Witness.run keep the raw
                  stamp and the kernel's receipt time; recorded() maps or refuses
  B4              skip_method_bench: Witness.clocked() maps the arm's frames, keeping their
                  wire order (seq); judge_arm / judge_arm_v2 give VOID for an arm whose clock
                  was refused
  e4 (B2/B3/B24)  e4_rig_calibration.settle_clock: data frames mapped, error frames on receipt,
                  order kept; refused -> every frame on receipt time; CANlib unchanged.
                  e4_device_calibration settles before the raw log and voids the arm on a
                  refused clock (static check)
  desk            provenance_classifier.rejudge carries the arm's VOID; golive_stale_check,
                  mode_entry_analysis and b24_frame_content_judge read it as VOID

    python3.13 -B gen-inhibit/tools/bench/test_kvaser_stamps.py [--mutants]
SITE_DIR (default: this folder) is the folder the tools are loaded from; --mutants copies this
folder per mutant, applies one edit, and runs this test there. interlock_bench and x2_jam_cases
need the canre pin installed (canre_pin.py install) and REVERSE_IT_DIR, as test_witness_clock.
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SITE_DIR") or HERE
sys.path.insert(0, SITE)
import gi_paths  # noqa: E402

EPOCH = 1.79e9          # receipt times on the host's epoch clock
LEAF0 = 4000.0          # the Leaf's own clock: its uptime, nowhere near the epoch
FAILS = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def mod(name):
    m = importlib.import_module(name)
    if os.path.dirname(os.path.abspath(m.__file__)) != os.path.abspath(SITE):
        raise RuntimeError("%s loaded from %s, not SITE_DIR" % (name, m.__file__))
    return m


class Msg:
    """A received frame as canbench's SocketcanHwBus gives it (hw_timestamp, host_timestamp)."""

    def __init__(self, aid, hw, host, err=False, data=b"\x00" * 6):
        self.arbitration_id, self.hw_timestamp, self.host_timestamp = aid, hw, host
        self.timestamp, self.is_error_frame, self.data = host, err, data
        self.is_extended_id = False


class PlainMsg:
    """A received frame as python-can's CANlib backend gives it: only timestamp."""

    def __init__(self, aid, ts, err=False):
        self.arbitration_id, self.timestamp, self.is_error_frame = aid, ts, err
        self.data, self.is_extended_id = b"\x00" * 6, False


def after(**kw):
    a = {"frames": 0, "hw_zero": 0, "hw_backward": 0, "error_frames": 0}
    a.update(kw)
    return a


class SockBus:
    """A SocketcanHwBus as kvaser_bus hands it out: vtrux_backend socketcan, stamp_stats()."""
    vtrux_backend = "socketcan"

    def __init__(self, msgs=(), state="hardware", reason="test", after_check=None, owner=None):
        self.msgs = list(msgs)
        self.st = {"state": state, "reason": reason, "after_check": after_check or after()}
        self.owner = owner

    def stamp_stats(self):
        return dict(self.st)

    def recv(self, timeout=None):
        if self.msgs:
            return self.msgs.pop(0)
        if self.owner is not None:
            self.owner.stop_flag.set()
        return None

    def shutdown(self):
        pass


def jitter(k):
    """A host delivery latency that varies frame to frame (0.3 to 0.7 ms)."""
    return 0.0003 + ((k * 7) % 5) * 0.0001


def frames(n=30, step=0.5, start=0):
    """[(raw, rx, aid, hex)]: data frames every step s on the Leaf's clock, received on the
    epoch clock a varying latency later. Spread over seconds, as a witness's frames are: the
    same latency jitter over a fraction of a second reads as a slope over device_fit's bound."""
    return [(LEAF0 + (start + k) * step, EPOCH + (start + k) * step + jitter(start + k), 0x051, "00" * 6)
            for k in range(n)]


def offset(fr):
    s = sorted(rx - raw for raw, rx, *_ in fr)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---------------------------------------------------------------- witness_clock

def witness_clock():
    WC = mod("witness_clock")
    m = Msg(0x051, LEAF0 + 1.0, EPOCH + 1.0)
    check("raw_stamp: a canbench message gives its hardware stamp", WC.raw_stamp(m) == LEAF0 + 1.0)
    check("raw_stamp: a canbench message with no hardware stamp gives None, not its host time",
          WC.raw_stamp(Msg(0x051, None, EPOCH + 1.0)) is None)
    check("raw_stamp: a CANlib message gives its timestamp", WC.raw_stamp(PlainMsg(0x051, 12.5)) == 12.5)
    check("receipt: a canbench message gives the kernel's receive time", WC.receipt(m) == EPOCH + 1.0)
    r = WC.receipt(PlainMsg(0x051, 12.5))
    check("receipt: a CANlib message gives the host's time now", abs(r - __import__("time").time()) < 5, r)

    check("kvaser_times: a CANlib bus -> None (the caller's own rule)",
          WC.kvaser_times(types.SimpleNamespace(vtrux_backend="canlib"), frames(), []) is None)
    fr = frames(30)
    errs = [(None, EPOCH + 0.105), (None, EPOCH + 0.205)]
    got = WC.kvaser_times(SockBus(), fr, errs)
    off = offset(fr)
    check("kvaser_times, hardware: judgeable, a note with the fit's residual and that error frames "
          "are on receipt time", got and WC.judgeable(got[2]) and "residual p50" in got[2]
          and "error frames on host receipt time" in got[2], got and got[2])
    check("kvaser_times, hardware: each data frame on its Leaf stamp plus the fitted offset, not its "
          "receipt time", got and all(close(t, raw + off) for (t, *_), (raw, *_r) in zip(got[0], fr))
          and not close(got[0][0][0], fr[0][1], 1e-7), got and got[0][:2])   # frame 0's latency is off the median
    check("kvaser_times, hardware: the rest of each frame kept", got and got[0][0][1:] == (0x051, "00" * 6))
    check("kvaser_times, hardware: error frames on their receipt time", got and got[1] == [EPOCH + 0.105, EPOCH + 0.205],
          got and got[1])

    def refused(label, bus, fr_, er_=()):
        g = WC.kvaser_times(bus, fr_, list(er_))
        ok = (g is not None and not WC.judgeable(g[2]) and g[2].startswith(WC.SOCKETCAN)
              and [x[0] for x in g[0]] == [x[1] for x in fr_])
        check("kvaser_times refuses: %s -> receipt times, a SOCKETCAN note" % label, ok, g and g[2])
        return g
    g = refused("stamp check pending", SockBus(state="pending"), fr)
    check("kvaser_times: the note names the check's state", g and "pending" in g[2], g and g[2])
    g = refused("stamp check host_only", SockBus(state="host_only", reason="data frame 3 has no hardware stamp (0)"), fr)
    check("kvaser_times: the note carries the check's reason", g and "data frame 3" in g[2], g and g[2])
    refused("no stamp_stats on the bus", types.SimpleNamespace(vtrux_backend="socketcan"), fr)
    refused("a data frame went backward after the check", SockBus(after_check=after(frames=40, hw_backward=1)), fr)
    refused("a data frame lost its stamp after the check",
            SockBus(after_check=after(frames=40, hw_zero=3, error_frames=1)), fr)
    refused("a kept data frame has no stamp", SockBus(), fr[:10] + [(None,) + fr[10][1:]] + fr[11:])
    refused("10 data frames: too few to fit", SockBus(), frames(10))
    refused("error frames alone, nothing to fit", SockBus(), [], errs)
    g = WC.kvaser_times(SockBus(after_check=after(frames=40, hw_zero=2, error_frames=2)), fr, errs)
    check("kvaser_times: error frames without a stamp after the check do not refuse", g and WC.judgeable(g[2]),
          g and g[2])
    g = WC.kvaser_times(SockBus(), [], [])
    check("kvaser_times: nothing heard -> NONE", g == ([], [], WC.NONE), g)
    check("device_fit names the adapter it fits", "Kvaser" in str(WC.device_fit([], who="Kvaser")["void"])
          and "PCAN" in str(WC.device_fit([])["void"]))


# ---------------------------------------------------------------- the witnesses

def run_witness(w):
    """Runs w.run() on its fake bus until the bus runs dry (the bus sets w's stop flag)."""
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    t.join(10)
    return not t.is_alive()


def msgs_for(fr, errs=()):
    out = [Msg(aid, raw, rx) for raw, rx, aid, _h in fr]
    out += [Msg(0x8, None, rx, err=True) for _raw, rx in errs]
    return out


def witnesses():
    WC = mod("witness_clock")
    IB = mod("interlock_bench")
    XC = mod("x2_jam_cases")
    fr = frames(30)
    errs = [(None, EPOCH + 0.155)]
    for name, cls, extra in (("interlock_bench (B20/B25)", IB.Witness, {}),
                             ("x2_jam_cases (B5/B6)", XC.Witness,
                              {"_next_status": float("inf"), "not_bus_errors": 0})):
        w = object.__new__(cls)
        w.lock, w.stop_flag = threading.Lock(), threading.Event()
        w.frames, w.errs = [], []
        for k, v in extra.items():
            setattr(w, k, v)
        w.bus = SockBus(msgs_for(fr, errs), owner=w)
        w.bus_error = lambda m: True
        done = run_witness(w)
        check("%s Witness.run: each data frame kept with its Leaf stamp and the kernel's receipt time" % name,
              done and [(f[0], f[1]) for f in w.frames] == [(raw, rx) for raw, rx, *_ in fr], w.frames[:2])
        check("%s Witness.run: each error frame kept with no stamp and its receipt time" % name,
              done and [tuple(e) for e in w.errs] == errs, w.errs)
        got = w.recorded()
        off = offset(fr)
        check("%s recorded(), check hardware: judgeable, frames on the mapped Leaf stamps" % name,
              WC.judgeable(got[2]) and close(got[0][0][0], fr[0][0] + off), got[2])
        w.bus.st["state"], w.bus.st["reason"] = "host_only", "test"
        got = w.recorded()
        check("%s recorded(), check host_only: receipt times, refused" % name,
              not WC.judgeable(got[2]) and got[0][0][0] == fr[0][1], got[2])


def b4():
    WC = mod("witness_clock")
    SM = mod("skip_method_bench")
    fr = frames(40)
    marker = [(LEAF0 + 0.105, EPOCH + 0.1054, SM.MARKER_ID, "00" * 6)]
    allf = sorted(fr + marker)
    w = SM.Witness(None)
    w.bus = SockBus(msgs_for(allf, [(None, EPOCH + 0.2)]), owner=w)
    done = run_witness(w)
    check("B4 Witness.run: frames keep their Leaf stamp and seq, receipt times beside them",
          done and [f[1] for f in w.frames] == [x[0] for x in allf] and w.frame_rx == [x[1] for x in allf]
          and [f[0] for f in w.frames] == sorted(f[0] for f in w.frames), w.frames[:2])
    frs, ers, note = w.clocked(0, 0)
    check("B4 clocked(), check hardware: judgeable, frames mapped, seq and order kept, errors on receipt",
          WC.judgeable(note) and [f[0] for f in frs] == [f[0] for f in w.frames]
          and close(frs[0][1], allf[0][0] + offset(allf)) and ers == [EPOCH + 0.2], (note, frs[:1], ers))
    frs, ers, note = w.clocked(10, 0)
    check("B4 clocked(mark): only the frames since the mark", len(frs) == len(allf) - 10 and frs[0][0] == w.frames[10][0])
    w.bus.st["state"] = "host_only"
    frs, ers, note = w.clocked(0, 0)
    check("B4 clocked(), check host_only: receipt times, refused", not WC.judgeable(note)
          and frs[0][1] == allf[0][1], note)
    arm = {"name": "w3", "went_live": True, "witness_clock_void": note, "events_classified": [],
           "pre_events": {}, "post_events": {}}
    j1, j2 = SM.judge_arm(arm), SM.judge_arm_v2(arm)
    check("B4 judge_arm and judge_arm_v2: an arm whose witness clock was refused is VOID",
          j1["verdict"].startswith("VOID") and j2["verdict"].startswith("VOID"), (j1["verdict"], j2["verdict"]))
    arm.pop("witness_clock_void")
    check("B4 judge_arm_v2: the same arm with no clock refusal is judged (not VOID)",
          not SM.judge_arm_v2(arm)["verdict"].startswith("VOID"), SM.judge_arm_v2(arm)["verdict"])


def e4():
    WC = mod("witness_clock")
    E4 = mod("e4_rig_calibration")
    fr = frames(30)
    errs = [(None, EPOCH + 0.155)]
    # the error frame given a raw stamp here, though kvaser_usb gives none: settle_clock must
    # still put it on its receipt time
    seq = sorted([(rx, raw, aid, False) for raw, rx, aid, _h in fr] + [(rx, LEAF0 + 0.155, 0x8, True) for _r, rx in errs])

    def wit(state="hardware"):
        w = object.__new__(E4.Witness)
        w.frames = [(raw if raw is not None else rx, aid, b"\x00" * 6, err, False) for rx, raw, aid, err in seq]
        w.raw = [raw for _rx, raw, _a, _e in seq]
        w.rx = [rx for rx, _raw, _a, _e in seq]
        w.bus = SockBus(state=state)
        return w
    w = wit()
    note = E4.settle_clock(w)
    off = offset(fr)
    data = [f for f in w.frames if not f[3]]
    errf = [f for f in w.frames if f[3]]
    check("e4 settle_clock, check hardware: judgeable; data frames on the mapped Leaf stamps",
          WC.judgeable(note) and all(close(f[0], raw + off) for f, (raw, *_r) in zip(data, fr)), note)
    check("e4 settle_clock: error frames on receipt time, frame order kept",
          [f[0] for f in errf] == [errs[0][1]] and [f[1] for f in w.frames] == [x[2] for x in seq], errf)
    w = wit("host_only")
    note = E4.settle_clock(w)
    check("e4 settle_clock, check host_only: every frame on receipt time, refused",
          not WC.judgeable(note) and [f[0] for f in w.frames] == [x[0] for x in seq], note)
    w = wit()
    w.bus = types.SimpleNamespace(vtrux_backend="canlib")
    before = list(w.frames)
    note = E4.settle_clock(w)
    check("e4 settle_clock on CANlib: frames as read, a hardware note", w.frames == before and WC.judgeable(note), note)
    w = object.__new__(E4.Witness)
    w.frames, w.raw, w.rx, w.stop = [], [], [], threading.Event()
    w.bus = SockBus([Msg(0x051, LEAF0, EPOCH + 0.0004), Msg(0x8, None, EPOCH + 0.5, err=True)], owner=None)
    w.bus.recv = (lambda q: (lambda timeout=None: q.pop(0) if q else (w.stop.set(), None)[1]))(w.bus.msgs)
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    t.join(10)
    check("e4 Witness.run: raw stamps and receipt times kept beside each frame; a frame with no stamp "
          "carries its receipt time meanwhile",
          w.raw == [LEAF0, None] and w.rx == [EPOCH + 0.0004, EPOCH + 0.5]
          and [f[0] for f in w.frames] == [LEAF0, EPOCH + 0.5], (w.raw, w.rx, w.frames))
    with open(os.path.join(SITE, "e4_device_calibration.py"), encoding="utf-8") as f:
        src = f.read()
    i_settle = src.find("    witness_clock = settle_clock(w)\n")
    i_log = src.find("    raw_path = None\n")
    check("e4_device_calibration: the clock settled before the raw log, a refused clock voids the arm, "
          "the note in the arm record",
          0 < i_settle < i_log and "        void_reasons.append(WC.clock_void_reason(witness_clock))\n" in src
          and '        "witness_clock": witness_clock,\n' in src, (i_settle, i_log))
    import witness_clock as WCm  # noqa: E402
    r = WCm.clock_void_reason("SocketCAN: x")
    check("witness_clock: the clock's void reason is what arm_clock_void picks out, and nothing else is",
          WCm.arm_clock_void({"void": True, "void_reasons": ["attribution x", r]}) == [r]
          and WCm.arm_clock_void({"void": False, "void_reasons": [r]}) == []
          and WCm.arm_clock_void({"void": True, "void_reasons": ["attribution x"]}) == [], r)


# ---------------------------------------------------------------- desk judges

def desk():
    P = mod("provenance_classifier")
    G = mod("golive_stale_check")
    work = gi_paths.scratch("kvaser-stamps-desk")
    gi_paths.remove_tree(work)
    os.makedirs(work)
    try:
        rec = {"capture": os.path.join(work, "cap.log"), "window_start": 0, "seconds": 1, "mode": "inhibit",
               "arm_after_s": 5,
               "arms": {"idle": {"void": True, "void_reasons": ["witness clock 'x': not judged on host time"],
                                 "witness_raw": "w.log.gz", "provenance": {"device_frames": 3}}}}
        path = os.path.join(work, "B3_e4_golive_observe_x_arm1.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        r = G.judge_file(path)
        check("golive_stale_check: an arm VOID in its run -> VOID: arm void, with the reason",
              r["verdict"].startswith("VOID: arm void") and "witness clock" in r["verdict"], r)
        rec["arms"]["idle"]["void"] = False
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        check("golive_stale_check: the same arm not void is judged", not G.judge_file(path)["verdict"].startswith("VOID"),
              G.judge_file(path)["verdict"])
        # void for another reason only (attribution): judged as before (the accepted baseline's B3
        # arms; the reviewer asked, 2026-11-18)
        rec["arms"]["idle"]["void"] = True
        rec["arms"]["idle"]["void_reasons"] = ["attribution disagrees with the device: tx_ok=3, witness attributes 2"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        check("golive_stale_check: an arm void only on attribution is judged as before",
              not G.judge_file(path)["verdict"].startswith("VOID"), G.judge_file(path)["verdict"])
        rec["arms"]["idle"]["void_reasons"] = ["attribution disagrees with the device: tx_ok=3, witness attributes 2",
                                               "witness clock 'x': not judged on host time"]
        rec["arms"]["idle"]["void"] = True
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        # rejudge with its capture and witness reading stubbed: the arm's VOID is carried
        fake_e4 = types.ModuleType("e4_device_calibration")
        fake_e4.load_window = lambda seconds: []
        saved = (sys.modules.get("e4_device_calibration"), P.read_witness, P.classify, P.gi_paths.verify)
        sys.modules["e4_device_calibration"] = fake_e4
        P.read_witness = lambda p: []
        P.classify = lambda *a, **k: {"verdict": "as specified", "device_times": [], "vcm_times": []}
        P.gi_paths.verify = lambda p: None
        try:
            res = P.rejudge(path, "idle", keep_frames=True)
            check("provenance_classifier.rejudge: the arm's witness-clock VOID carried as arm_void, its other "
                  "reasons not", res.get("arm_void") == ["witness clock 'x': not judged on host time"], res.get("arm_void"))
            B24 = mod("b24_frame_content_judge")
            B24.P.read_witness = lambda p: []
            r = B24.judge_run(path)
            check("b24_frame_content_judge: a VOID arm reads VOID, with the reason first in its faults",
                  r["verdict"] == "VOID" and "witness clock" in str(r["faults"][0]), r)
            MEA = mod("mode_entry_analysis")
            r = MEA.analyse(path)
            check("mode_entry_analysis: a VOID arm gives {run, void}, no figures",
                  r.get("void") == ["witness clock 'x': not judged on host time"] and "first_answer_after_post_ms" not in r, r)
            rec["arms"]["idle"]["void"] = False
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            check("provenance_classifier.rejudge: an arm not void carries arm_void []",
                  P.rejudge(path, "idle")["arm_void"] == [])
            rec["arms"]["idle"]["void"] = True
            rec["arms"]["idle"]["void_reasons"] = ["attribution disagrees with the device: tx_ok=3, witness attributes 2"]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            check("provenance_classifier.rejudge: an arm void only on attribution carries arm_void [] (judged as "
                  "before)", P.rejudge(path, "idle")["arm_void"] == [])

            # The witness short of the replay, an empty or short window (run cb08, 2026-11-19): every
            # desk tool reads the arm VOID, and an analysis that raises gives an error row, never a crash.
            WCm = mod("witness_clock")
            why = "the witness holds 10 of 100 replayed frames in order: the window cannot be judged"
            check("witness_clock.window_void: short -> the reason; whole, absent or not a dict -> []",
                  WCm.window_void({"replayed_frames": 100, "replayed_found": 10}) == [why]
                  and WCm.window_void({"replayed_frames": 100, "replayed_found": 100}) == []
                  and WCm.window_void({"replayed_frames": 100}) == [] and WCm.window_void({}) == []
                  and WCm.window_void(None) == [] and WCm.window_void("UNRESOLVED") == [],
                  WCm.window_void({"replayed_frames": 100, "replayed_found": 10}))
            rec["arms"]["idle"]["void"] = False
            rec["arms"]["idle"]["void_reasons"] = []
            rec["arms"]["idle"]["provenance"] = {"device_frames": 3, "replayed_frames": 100, "replayed_found": 10,
                                                 "verdict": "UNRESOLVED", "unresolved": ["count: 10 of 100"]}
            rec["post_schedule"] = []
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            r = G.judge_file(path)
            check("golive_stale_check: the witness short of the replay -> VOID: arm void, with the reason",
                  r["verdict"] == "VOID: arm void: " + why, r)
            rec["arms"]["idle"]["provenance"] = {"device_frames": 3, "replayed_frames": 100, "replayed_found": 100}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            check("golive_stale_check: the whole replay on the witness is judged",
                  not G.judge_file(path)["verdict"].startswith("VOID: arm void"), G.judge_file(path)["verdict"])
            short = {"verdict": "UNRESOLVED", "replayed_frames": 100, "replayed_found": 10, "device_times": [],
                     "vcm_times": [], "replayed_wire_times": [float(k) for k in range(10)]}
            P.classify = lambda *a, **k: dict(short)
            r = MEA.analyse(path)
            check("mode_entry_analysis: the witness short of the replay gives {run, void}, no figures, not an error",
                  r.get("void") == [why] and "error" not in r and "first_answer_after_post_ms" not in r, r)
            QA = mod("quiesce_analysis")
            r = QA.analyse(path)
            check("quiesce_analysis: the witness short of the replay gives {run, initial_mode, void} (run cb08 "
                  "raised IndexError)", r.get("void") == [why] and r.get("initial_mode") == "inhibit", r)
            P.classify = lambda *a, **k: (_ for _ in ()).throw(IndexError("list index out of range"))
            try:
                r = MEA.analyse_row(path)
            except Exception as e:      # noqa: BLE001  the row's absence is the failure, not a crash here
                r = {"raised": repr(e)}
            check("mode_entry_analysis: an analysis that raises gives an error row naming it",
                  r.get("run") == os.path.basename(path) and "IndexError" in (r.get("error") or ""), r)
            import contextlib
            import io
            buf, argv = io.StringIO(), sys.argv
            sys.argv = ["quiesce_analysis.py", path]
            try:
                with contextlib.redirect_stdout(buf):
                    rc = QA.main()
            except Exception as e:      # noqa: BLE001
                rc = "raised %r" % e
            finally:
                sys.argv = argv
            rows = [json.loads(x) for x in buf.getvalue().splitlines() if x.startswith("{")]
            check("quiesce_analysis: an analysis that raises prints an error row and exits 0",
                  rc == 0 and len(rows) == 1 and "IndexError" in (rows[0].get("error") or ""), (rc, rows))
        finally:
            if saved[0] is None:
                sys.modules.pop("e4_device_calibration", None)
            else:
                sys.modules["e4_device_calibration"] = saved[0]
            P.read_witness, P.classify, P.gi_paths.verify = saved[1], saved[2], saved[3]
    finally:
        gi_paths.remove_tree(work)


# skip_method_bench's judges open with the dead-witness VOID (2026-11-19), before the clock VOID
PROC_VOID = ("    if arm.get(\"witness_proc_void\"):\n"
             "        return dict(out, verdict=PW.void_verdict(arm[\"witness_proc_void\"]), why=[\"witness process\"])\n")

MUTANTS = [
    ("witness_clock.py", "    return m.hw_timestamp if hasattr(m, \"hw_timestamp\") else m.timestamp\n",
     "    return m.timestamp\n"),
    ("witness_clock.py", "    return t if t is not None else time.time()\n", "    return time.time()\n"),
    ("witness_clock.py", "    if st.get(\"state\") != \"hardware\":\n", "    if st.get(\"state\") == \"host_only\":\n"),
    ("witness_clock.py", "    if lost > 0 or after.get(\"hw_backward\"):\n", "    if after.get(\"hw_backward\"):\n"),
    ("witness_clock.py", "    if lost > 0 or after.get(\"hw_backward\"):\n", "    if lost > 0:\n"),
    ("witness_clock.py", "    lost = (after.get(\"hw_zero\") or 0) - (after.get(\"error_frames\") or 0)\n",
     "    lost = after.get(\"hw_zero\") or 0\n"),
    ("witness_clock.py", "    if missing:\n", "    if False:\n"),
    ("witness_clock.py", "    if fit[\"void\"]:\n        return rx_frames", "    if False:\n        return rx_frames"),
    ("witness_clock.py", "    return [(mapped(fit, raw),) + tuple(rest) for raw, _rx, *rest in frames], rx_errs, note\n",
     "    return rx_frames, rx_errs, note\n"),
    ("witness_clock.py", "    fit = device_fit([(rx, rx, raw) for raw, rx, *_ in frames], who=\"Kvaser\")\n",
     "    fit = device_fit([(rx, rx, rx - 5.0) for raw, rx, *_ in frames], who=\"Kvaser\")\n"),
    ("witness_clock.py", "    except Exception as e:      # noqa: BLE001  no stamp check to read: never hardware\n"
     "        return rx_frames, rx_errs, \"%s: no stamp check (%s)\" % (SOCKETCAN, e)\n",
     "    except Exception as e:      # noqa: BLE001  no stamp check to read: never hardware\n"
     "        st = {\"state\": \"hardware\"}\n"),
    ("interlock_bench.py", "                        self.frames.append((WC.raw_stamp(m), rx, m.arbitration_id, bytes(m.data).hex()))",
     "                        self.frames.append((m.timestamp, rx, m.arbitration_id, bytes(m.data).hex()))"),
    ("interlock_bench.py", "                rx = WC.receipt(m)\n", "                rx = time.time()\n"),
    ("x2_jam_cases.py", "                        self.frames.append((WC.raw_stamp(m), rx, m.arbitration_id,\n",
     "                        self.frames.append((m.timestamp, rx, m.arbitration_id,\n"),
    # re-anchored 2026-11-19: the dead-witness VOID (witness_proc_void) now comes first in both judges
    ("skip_method_bench.py", "    out = {\"expected_class\": want_cls, \"expected_trip7\": want_trip}\n" + PROC_VOID
     + "    if arm.get(\"witness_clock_void\"):",
     "    out = {\"expected_class\": want_cls, \"expected_trip7\": want_trip}\n" + PROC_VOID + "    if False:"),
    ("skip_method_bench.py", "           \"expected_trip7\": want_trip}\n" + PROC_VOID + "    if arm.get(\"witness_clock_void\"):",
     "           \"expected_trip7\": want_trip}\n" + PROC_VOID + "    if False:"),
    ("skip_method_bench.py", "        if sc is None:\n            return fr, er, CANLIB_NOTE\n",
     "        if True:\n            return fr, er, CANLIB_NOTE\n"),
    ("skip_method_bench.py", "        return [(seq, t, arb, hx) for t, seq, arb, hx in mfr], mer, note\n",
     "        return fr, mer, note\n"),
    ("e4_rig_calibration.py", "    for t, k in mfr:\n        new[k] = (t,) + new[k][1:]\n", ""),
    ("e4_rig_calibration.py", "    for t, k in zip(mer, errs):\n        new[k] = (t,) + new[k][1:]\n", ""),
    ("e4_rig_calibration.py", "    if not WC.socketcan(w.bus):\n        return CANLIB_NOTE\n", ""),
    ("e4_rig_calibration.py", "                raw_append(raw)\n", ""),
    ("e4_device_calibration.py", "        void_reasons.append(WC.clock_void_reason(witness_clock))\n",
     "        pass\n"),
    ("provenance_classifier.py", "    res[\"arm_void\"] = WC.arm_clock_void(a)\n",
     "    res[\"arm_void\"] = []\n"),
    ("provenance_classifier.py", "    res[\"arm_void\"] = WC.arm_clock_void(a)\n",
     "    res[\"arm_void\"] = list(a.get(\"void_reasons\") or []) if a.get(\"void\") else []\n"),
    ("golive_stale_check.py", "        if cv:\n", "        if False:\n"),
    ("golive_stale_check.py", "        cv = WC.arm_clock_void(a)", "        cv = a.get(\"void_reasons\") if a.get(\"void\") else []"),
    ("witness_clock.py", "    if not arm.get(\"void\"):\n        return []\n", ""),
    ("witness_clock.py", "    return [r for r in (arm.get(\"void_reasons\") or []) if str(r).startswith(CLOCK_VOID_PREFIX)]\n",
     "    return list(arm.get(\"void_reasons\") or [])\n"),
    ("b24_frame_content_judge.py", "        res[\"verdict\"] = \"VOID\"\n", "        pass\n"),
    ("mode_entry_analysis.py", "    if r.get(\"arm_void\"):                          # the arm's own VOID (2026-11-18)\n",
     "    if False:\n"),
    # the witness short of the replay reads VOID; an analysis that raises gives an error row (2026-11-19)
    ("witness_clock.py", "    if not isinstance(n, int) or not isinstance(p, int) or p >= n:\n        return []\n",
     "    return []\n"),
    ("golive_stale_check.py", "        wv = WC.window_void(prov)                # the witness short of the replay (2026-11-19)\n",
     "        wv = []\n"),
    ("mode_entry_analysis.py", "    wv = WC.window_void(r)                          # the witness short of the replay (2026-11-19)\n",
     "    wv = []\n"),
    ("quiesce_analysis.py",
     "    wv = WC.window_void(r)    # the witness short of the replay (run cb08 raised IndexError here, 2026-11-19)\n",
     "    wv = []\n"),
    ("quiesce_analysis.py", "            except Exception as e:      # noqa: BLE001  a row the judge reads as ERROR",
     "            except ImportError as e:      # noqa: BLE001  a row the judge reads as ERROR"),
    ("mode_entry_analysis.py", "    except Exception as e:      # noqa: BLE001\n        return {\"run\": os.path.basename(run), \"error\": \"analysis raised",
     "    except ImportError as e:      # noqa: BLE001\n        return {\"run\": os.path.basename(run), \"error\": \"analysis raised"),
]


def mutants():
    base = gi_paths.scratch("kvaser-stamps-mutants")
    killed = errors = 0
    try:
        for n, (fn, old, new) in enumerate([(None, None, None)] + MUTANTS):
            d = os.path.join(base, "m%d" % n)
            shutil.copytree(HERE, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if fn:
                p = os.path.join(d, fn)
                with open(p, encoding="utf-8") as f:
                    s = f.read()
                if s.count(old) != 1:
                    print("ERROR    m%d %s: edit matched %d times" % (n, fn, s.count(old)))
                    errors += 1
                    gi_paths.remove_tree(d)
                    continue
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(s.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.join(d, "test_kvaser_stamps.py")],
                               capture_output=True, text=True, timeout=600,
                               env=dict(os.environ, SITE_DIR=d, PYTHONDONTWRITEBYTECODE="1"))
            fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
            if fn is None:
                if r.returncode != 0:
                    print("FAILED   control\n" + r.stdout[-2000:] + r.stderr[-1500:])
                    return 1
                print("PASS     control")
            elif fails:
                killed += 1
                print("KILLED   m%d %s: %s  (%s)" % (n, fn, new.strip()[:50], fails[0][:90]))
            else:
                print("SURVIVED m%d %s: %s  (rc %d %s)" % (n, fn, new.strip()[:50], r.returncode,
                                                         r.stderr.strip()[-120:]))
            gi_paths.remove_tree(d)
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d mutants killed, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


def main(argv):
    if "--mutants" in argv:
        return mutants()
    for part in (witness_clock, witnesses, b4, e4, desk):
        try:
            part()
        except Exception as e:      # noqa: BLE001  a part that raises is a FAIL, not a crash
            import traceback
            traceback.print_exc()
            check(part.__name__ + " ran", False, "%s: %s" % (type(e).__name__, e))
    print("PASS" if not FAILS else "FAIL: %d checks" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
