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
wire times, as quiesce_analysis places them. The witness counts twice: from the POST's
send (t_sent) and from its completion (t_complete, t_sent plus the POST's own
send-to-completion time on the run's clock, its post_s), and the counts agree when the
device's count lies between the two (the gen-inhibit reviewer's ruling, answer to the run
(C) report, 2026-11-20): a frame sent between the request and the reply may fall on either
side of the device's reset. What the witness counts is what the device's tx_ok counts in the mode that POST
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


def post_elapsed(a):
    """How long the arm's POST took, send to completion, from the run's record (its post_s,
    [send, complete] on the run's clock), or None when the record has no completed POST."""
    posts = [e for e in (a.get("post_schedule") or []) if e.get("index") is not None]
    ps = posts[-1].get("post_s") if posts else a.get("post_s")   # the arm's POST, as arm_post picks it
    if not (isinstance(ps, (list, tuple)) and len(ps) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in ps) and ps[1] >= ps[0]):
        return None
    return ps[1] - ps[0]


def window(d, a, rep_t):
    """Where the device's counters last reset: {"from", "index", "start_s", "complete_s",
    "mode"}. start_s is the POST's send on the wire (its replay frame's wire time); complete_s
    is its completion, start_s plus the POST's own send-to-completion time (post_elapsed), or
    None when the record has none. rep_t: the wire time of each replayed frame."""
    frm, idx, mode = arm_post(d, a)
    if idx is None:
        return {"from": frm, "index": None, "start_s": None, "complete_s": None, "mode": mode}
    if not 0 <= idx < len(rep_t):
        raise ValueError("%s at replay frame %d, but the witness places %d replayed frames" % (frm, idx, len(rep_t)))
    el = post_elapsed(a)
    return {"from": frm, "index": idx, "start_s": rep_t[idx],
            "complete_s": None if el is None else rep_t[idx] + el, "mode": mode}


def attribution(d, a, wire, device_times, rep_t):
    """The device's count against the witness's, from the arm on: a dict with "agree", or
    None when the device gave no count (tx_ok missing: its own reason, device silent).
    wire: the witness [(t, id, data)]; device_times: the provenance classifier's device
    0x051 times; rep_t: its wire time of each replayed frame.

    The witness counts from the POST's send (t_sent: "witness") and from its completion
    (t_complete: "witness_from_complete"), and the two agree when the device's count lies
    between the two counts (the gen-inhibit reviewer's ruling, answer to the run (C) report,
    2026-11-20); outside that the arm is void. With no completion on record the two counts are
    the same, the count from the send."""
    tx_ok = (a.get("device") or {}).get("tx_ok")
    if not isinstance(tx_ok, int):
        return None
    w = window(d, a, rep_t)
    t0, t1 = w["start_s"], w["complete_s"] if w["complete_s"] is not None else w["start_s"]

    def count(times, frm):
        return sum(1 for t in times if frm is None or t >= frm)
    if w["mode"] == "respond":
        counted_as = "probe 0x7F0"
        times = [t for t, i, _d in wire if i == PROBE_ID]
    elif w["mode"] == "inhibit":
        counted_as = "device 0x051 (provenance)"
        times = list(device_times)
    else:
        # OFF, OBSERVE and PASSIVE put nothing on the wire that tx_ok counts: nothing to
        # compare, and a device 0x051 there is judged as behaviour, not attribution
        return dict(w, counted_as=None, witness=None, witness_from_complete=None, device_tx_ok=tx_ok, agree=None)
    n_sent, n_complete = count(times, t0), count(times, t1)
    return dict(w, counted_as=counted_as, witness=n_sent, witness_from_complete=n_complete, device_tx_ok=tx_ok,
                agree=n_complete <= tx_ok <= n_sent)


def attribution_reason(att):
    """The void reason for a same-window disagreement, or None."""
    if att is None or att["agree"] is not False:
        return None
    n1 = att.get("witness_from_complete", att["witness"])
    if att.get("complete_s") is not None:
        counts = "%d %s from %s's send and %d from its completion" % (
            att["witness"], att["counted_as"], att["from"], n1)
    else:
        counts = "%d %s from %s" % (att["witness"], att["counted_as"], att["from"])
    return "%sthe device reports tx_ok=%d, the witness counts %s" % (ATTRIBUTION, att["device_tx_ok"], counts)


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
