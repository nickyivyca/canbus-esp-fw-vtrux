"""Spec 12.1: when an e4 arm is void, and why (gen-inhibit tester, 2026-11-19).

Spec 12.1 (c5849685, the user's ruling of 2026-11-19, Q-VOID option C): "A void arm is
never judged as a normal arm." An arm is void when the bench could not give it a fair
reading, for one of the reasons in LIST, and a check made of arms reads VOID when any arm
it asked for is void, never PASS while one is void or unjudged; a FAIL in an arm read
fairly stands.

An e4 arm's run records why it was void (`void_reasons`, e4_device_calibration.run_arm).
`kind` puts each recorded reason under its LIST entry. A reason that fits none is not
mapped (`reasons` returns it apart): it goes to the gen-inhibit reviewer, and a desk tool
gives the arm an error, never a verdict.

ATTRIBUTION, COUNTED OVER THE SAME WINDOW. The device's counters reset on every arm
(spec, PASSIVE goal 2: "The count resets on every arm, as tx_ok does"), so the device's
count and the witness's count are compared from the arm on (spec 12.1's attribution
entry). The arm is the last mode POST the arm sent: the last scheduled POST
(`post_schedule`, e.g. --post-schedule 20:3), else the --arm-after POST, else the start
of the replay (an arm armed before it). Each POST is placed on the wire by the replay
frame the run was at when it started it (its `index`), on the provenance classifier's
wire times, as quiesce_analysis places them; a frame the device sent once that POST was
on its way counts, so a frame sent between the request and the reply counts on the new
side. What the witness counts is what the device's tx_ok counts in the mode that POST
set: the 0x7F0 probes in RESPOND, the device's own 0x051 in INHIBIT, by the provenance
classifier. OFF, OBSERVE and PASSIVE put nothing on the wire that tx_ok counts, so there
is nothing to compare (a device 0x051 there is behaviour, judged as before). The old check
counted the whole arm, so an arm re-armed mid-replay (the e3d9437 baseline's B3 RESPOND
arms, POSTed into INHIBIT at 20 s) disagreed by construction: its recorded reason
("attribution disagrees with the device: ...") is dropped and the count made again here.
"""
import re

LIST = (
    ("clock", "the witness clock fit fails its bounds"),
    ("witness_log", "there is no witness log, or the witness left it short of the arm's window"),
    ("replay_refused", "the replay was refused"),
    ("stimulus_short", "the stimulus fell short of what the arm asked for"),
    ("device_silent", "the device went silent, so its own figures could not be read"),
    ("attribution", "the device's count and the witness's count disagree over the same window, from the arm"),
)
KINDS = tuple(k for k, _ in LIST)

CLOCK_PREFIX = "witness clock "            # witness_clock.CLOCK_VOID_PREFIX
OLD_ATTRIBUTION = "attribution disagrees with the device:"   # the whole-arm count, before 2026-11-19
ATTRIBUTION = "attribution over the same window from the arm disagrees: "

# Each reason e4_device_calibration.run_arm records, and witness_clock.window_void's, by its
# opening words. test_arm_void scans run_arm's appends so a new one cannot go unmapped.
PATTERNS = (
    (re.compile(re.escape(CLOCK_PREFIX)), "clock"),
    (re.compile(r"the witness process: "), "witness_log"),
    (re.compile(r"witness log could not be written: "), "witness_log"),
    (re.compile(r"no witness log directory was configured\Z"), "witness_log"),
    (re.compile(r"the witness holds \d+ of \d+ replayed frames in order"), "witness_log"),
    (re.compile(r"\d+ replay frames were refused by the adapter"), "replay_refused"),
    (re.compile(r"\d+ of \d+ commands \([\d.]+ %\) never reached the wire"), "stimulus_short"),
    (re.compile(r"the device did not answer at the end of the arm\Z"), "device_silent"),
    (re.compile(re.escape(ATTRIBUTION)), "attribution"),
)

MODE_NAMES = {0: "off", 1: "observe", 2: "respond", 3: "inhibit", 4: "passive"}
PROBE_ID = 0x7F0


def kind(reason):
    """The LIST entry a recorded void reason falls under, or None (not on the list)."""
    s = str(reason)
    for rx, k in PATTERNS:
        if rx.match(s):
            return k
    return None


def arm_post(d, a):
    """The arm, by the run's record: (what it was, its replay frame index or None, the mode
    it set). d: the e4 run record; a: its arm."""
    posts = [e for e in (a.get("post_schedule") or []) if e.get("index") is not None]
    if posts:
        e = posts[-1]
        return ("scheduled POST %d (at %s s, mode %s)" % (len(posts), e.get("at_s"), e.get("mode")),
                e["index"], MODE_NAMES.get(e.get("mode"), str(e.get("mode"))))
    if a.get("post_index") is not None:
        return "the --arm-after POST", a["post_index"], d.get("mode")
    return "the start of the replay", None, d.get("mode")


def window(d, a, rep_t):
    """Where the device's counters last reset: {"from", "index", "start_s", "mode"}.
    rep_t: the wire time of each replayed frame."""
    frm, idx, mode = arm_post(d, a)
    if idx is None:
        return {"from": frm, "index": None, "start_s": None, "mode": mode}
    if not 0 <= idx < len(rep_t):
        raise ValueError("%s at replay frame %d, but the witness places %d replayed frames" % (frm, idx, len(rep_t)))
    return {"from": frm, "index": idx, "start_s": rep_t[idx], "mode": mode}


def attribution(d, a, wire, device_times, rep_t):
    """The device's count against the witness's, from the arm on: a dict with "agree", or
    None when the device gave no count (tx_ok missing: its own reason, device silent).
    wire: the witness [(t, id, data)]; device_times: the provenance classifier's device
    0x051 times; rep_t: its wire time of each replayed frame."""
    tx_ok = (a.get("device") or {}).get("tx_ok")
    if not isinstance(tx_ok, int):
        return None
    w = window(d, a, rep_t)
    t0 = w["start_s"]
    after = (lambda t: True) if t0 is None else (lambda t: t >= t0)
    if w["mode"] == "respond":
        counted_as = "probe 0x7F0"
        n = sum(1 for t, i, _d in wire if i == PROBE_ID and after(t))
    elif w["mode"] == "inhibit":
        counted_as = "device 0x051 (provenance)"
        n = sum(1 for t in device_times if after(t))
    else:
        # OFF, OBSERVE and PASSIVE put nothing on the wire that tx_ok counts: nothing to
        # compare, and a device 0x051 there is judged as behaviour, not attribution
        return dict(w, counted_as=None, witness=None, device_tx_ok=tx_ok, agree=None)
    return dict(w, counted_as=counted_as, witness=n, device_tx_ok=tx_ok, agree=n == tx_ok)


def attribution_reason(att):
    """The void reason for a same-window disagreement, or None."""
    if att is None or att["agree"] is not False:
        return None
    return "%sthe device reports tx_ok=%d, the witness counts %d %s from %s" % (
        ATTRIBUTION, att["device_tx_ok"], att["witness"], att["counted_as"], att["from"])


def reasons(a, extra=(), recounted=False):
    """(listed, unlisted): the arm's recorded void reasons under LIST, the old whole-arm
    attribution reason dropped, with `extra` (window_void's, a count made again) added;
    unlisted: those on no entry. recounted: the caller counted attribution again and gives
    it in `extra`, so a recorded same-window reason is dropped too (provenance_classifier.rejudge)."""
    listed, unlisted = [], []
    rec = [r for r in (a.get("void_reasons") or [])
           if not str(r).startswith(OLD_ATTRIBUTION) and not (recounted and kind(r) == "attribution")]
    for r in rec + [r for r in extra if r]:
        (listed if kind(r) else unlisted).append(str(r))
    return listed, unlisted
