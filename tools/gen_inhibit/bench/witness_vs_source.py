"""Compare what the witness saw against what the replay was asked to send.

WHY THIS EXISTS, and it is the lesson of the 2026-09-29 re-paced saturation arm.
That arm reported `refused 0` -- the adapter accepted every frame -- and the
stimulus was still incomplete: 8998 0x051 commands went into the replay and only
about 8613 reached the wire. **`refused 0` does not mean the stimulus arrived.**
An adapter can take a frame into its queue and never get it onto a bus that is
already carrying 97.6 %, and nothing raises an exception when that happens.

The previous arm's loss was loud (4242 refusals). This one was silent, and the
only thing that showed it was counting the witness against the source. So that
comparison has to be a check rather than something a reader might think to do.

THE DEVICE IS THE SECOND INSTRUMENT. It reports how many 0x051 frames it received
(`ctr_ok + ctr_bad`). When that agrees with the witness and both fall short of the
source, the frames genuinely never reached the wire -- it is not the witness
dropping them. When the witness falls short and the device does not, suspect the
witness. Reporting both is what makes the two cases distinguishable.

    py -3.11 witness_vs_source.py --run runs/e4_saturation_5364648.json

Reads the run JSON for the capture name, window and witness log, so it cannot be
pointed at a mismatched pair by accident.
"""

import argparse
import gzip
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e4_device_calibration as e4  # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

CMD_ID = e4.CMD_ID
KEY_ID = 0x592          # the freshness gate for key-on (spec 7.1 rule 4)
DIAG_IDS = (0x7F0, 0x7F1, 0x7F2, 0x7F3, 0x7F8)


def read_witness(path):
    """Yield (ts, ident) from a candump-style witness log, skipping error frames."""
    op = gzip.open if path.endswith(".gz") else io.open
    with op(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line or "ERRORFRAME" in line:
                continue
            try:
                ts = float(line[1:line.index(")")])
                rest = line[line.index(")") + 1:].split()
                ident = int(rest[1].split("#")[0], 16)
            except (ValueError, IndexError):
                continue
            yield ts, ident


def gaps(times, period_s):
    """Return the gaps exceeding 3x the nominal period, longest first."""
    out = []
    for a, b in zip(times, times[1:]):
        if b - a > 3.0 * period_s:
            out.append(b - a)
    out.sort(reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="an e4 run JSON")
    ap.add_argument("--arm", default=None, help="arm name (default: the only one)")
    args = ap.parse_args()

    run_path = args.run
    if not os.path.isabs(run_path):
        run_path = gi_paths.data_dir(run_path) if not os.path.exists(run_path) \
            else run_path
    doc = json.load(open(run_path))

    arms = doc["arms"]
    name = args.arm or (list(arms)[0] if len(arms) == 1 else None)
    if name is None:
        print("several arms; pass --arm one of %s" % list(arms))
        return 2
    arm = arms[name]

    raw = arm.get("witness_raw")
    if not raw:
        print("this arm has no witness log recorded -- it was VOID for that reason")
        return 2
    wpath = os.path.join(os.path.dirname(run_path), raw)

    seconds = doc["seconds"]
    print("run %s, arm %s, device %s, %s s"
          % (os.path.basename(run_path), name, doc.get("device"), seconds))

    # ---- the source, through the harness's own loader so the window matches ----
    #
    # Its loader reads module globals rather than taking the capture as an
    # argument, so they are set from the run's OWN record of what it replayed.
    # Re-deriving the window here instead would let this check and the run it
    # audits disagree about what the stimulus was, which is the one thing an
    # audit must not do.
    #
    e4.CAPTURE = gi_paths.runs(doc["capture_used"])
    e4.WINDOW_START = doc.get("window_start") or 0.0
    src = e4.load_window(seconds)
    src_cmd = [off for off, ident, _, _ in src if ident == CMD_ID]
    src_key = [off for off, ident, _, _ in src if ident == KEY_ID]

    # ---- the witness ----------------------------------------------------------
    w_cmd, w_key, w_diag, total = [], [], {}, 0
    for ts, ident in read_witness(wpath):
        total += 1
        if ident == CMD_ID:
            w_cmd.append(ts)
        elif ident == KEY_ID:
            w_key.append(ts)
        if ident in DIAG_IDS:
            w_diag[ident] = w_diag.get(ident, 0) + 1

    dev = arm.get("device") or {}
    recv = (dev.get("ctr_ok") or 0) + (dev.get("ctr_bad") or 0)
    tx_ok = dev.get("tx_ok")
    # Every 0x051 on the wire is either the VCM's or our answer to one; the device
    # transmits at most one answer per command, so its own tx_ok is the honest way
    # to split the witnessed total without re-running the trail classifier.
    vcm_on_wire = len(w_cmd) - (tx_ok or 0)

    print("\n0x051 COMMANDS")
    print("  source window           %6d" % len(src_cmd))
    print("  on the wire (witness)   %6d   [%d total 0x051 less tx_ok %s]"
          % (vcm_on_wire, len(w_cmd), tx_ok))
    print("  received by the device  %6d   [ctr_ok+ctr_bad]" % recv)
    lost = len(src_cmd) - vcm_on_wire
    print("  NEVER REACHED THE WIRE  %6d   (%.2f %% of the source)"
          % (lost, 100.0 * lost / max(1, len(src_cmd))))
    print("  device vs wire          %6d missed by the device (drv.rx_missed %s)"
          % (vcm_on_wire - recv, (dev.get("drv") or {}).get("rx_missed")))

    print("\n0x592 KEY GATE (a stale key silently withholds transmission)")
    print("  source %d, witness %d" % (len(src_key), len(w_key)))
    if src_key:
        per = seconds / float(len(src_key))
        sg, wg = gaps(src_key, per), gaps(w_key, per)
        print("  nominal period %.1f ms" % (per * 1e3))
        print("  source gaps > 3x period:  %d%s"
              % (len(sg), (", longest %.0f ms" % (sg[0] * 1e3)) if sg else ""))
        print("  witness gaps > 3x period: %d%s"
              % (len(wg), (", longest %.0f ms" % (wg[0] * 1e3)) if wg else ""))
        if len(wg) > len(sg):
            print("  ^ the HARNESS starved the gate; any unanswered command in "
                  "those windows is spec-conformant gating, not a device fault")

    print("\nDIAG FRAMES ON THE WIRE (telemetry criterion, item 10 case 3)")
    if w_diag:
        for i in sorted(w_diag):
            print("  0x%03X  %d" % (i, w_diag[i]))
    else:
        print("  NONE on %s -- telemetry was withheld for the whole arm"
              % ", ".join("0x%03X" % i for i in DIAG_IDS))
    print("\nwitness total %d frames" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
