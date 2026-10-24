#!/usr/bin/env python3
"""Tests b24_frame_content_judge (frame_faults, and judge_frames over the real
provenance_classifier.classify on a synthetic replay); no device. --mutants runs
its mutants; each must make a check fail.

    py -3.14 tools/gen_inhibit/bench/test_b24_frame_content_judge.py [--mutants]
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b24_frame_content_judge as B     # noqa: E402
import provenance_classifier as P       # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:140]))
    if not ok:
        FAILS.append(label)


def cmd(b0, ctr, rpm=0x1234):
    return bytes([b0, 0x00, 0x80, rpm & 0xFF, rpm >> 8, ctr & 0x0F])


def scenario(b0s, answer):
    """A replay of 0x051 commands (B0 from b0s) with a 0x100 frame between each, and
    the device's frames from answer(front) after each but the last."""
    src, wire = [], []
    t = 0.0
    for k, b0 in enumerate(b0s):
        c = cmd(b0, k)
        src += [(t, 0x051, c), (t + 0.005, 0x100, b"\x01")]
        wire += [(t, 0x051, c), (t + 0.005, 0x100, b"\x01")]
        if k + 1 < len(b0s):
            wire.append((t + 0.0006, 0x051, answer(c)))
        t += 0.01
    wire.sort(key=lambda x: x[0])
    return src, wire


def held(front):
    return bytes([0x08, 0x00, 0x80, front[3], front[4], (front[5] + 1) & 0x0F])


def run(b0s, answer):
    src, wire = scenario(b0s, answer)
    prov = P.classify(src, wire, len(b0s) - 1, keep_frames=True)
    return B.judge_frames(wire, prov)


def main():
    f = cmd(0x08, 3)
    check("frame: the table's frame conforms", B.frame_faults(held(f), f) == [])
    check("frame: B0 mirrored from 0x0B fails", B.frame_faults(bytes([0x0B]) + held(f)[1:], cmd(0x0B, 3)) != [])
    check("frame: nonzero torque fails", B.frame_faults(bytes([8, 0x10, 0x80]) + held(f)[3:], f) != [])
    check("frame: B3-B4 not mirrored fails", B.frame_faults(held(f)[:3] + b"\x00\x00" + held(f)[5:], f) != [])
    check("frame: the VCM's own counter fails", B.frame_faults(held(f)[:5] + bytes([3]), f) != [])
    check("frame: DLC 8 fails", B.frame_faults(held(f) + b"\x00\x00", f) != [])
    r = run([0x08] * 10, held)
    check("judge: all-0x08 replay answered by the table: AS SPECIFIED, 9 frames, not discriminating",
          r["verdict"] == "AS SPECIFIED" and r["device_frames"] == 9 and not r["b0_hold_discriminating"], r)
    r = run([0x08, 0x0B, 0x0B, 0x08, 0x08], held)
    check("judge: 0x0B commands answered with 0x08: AS SPECIFIED and discriminating",
          r["verdict"] == "AS SPECIFIED" and r["b0_hold_discriminating"], r)
    mirror = lambda c: bytes([c[0]]) + held(c)[1:]
    r = run([0x08, 0x0B, 0x0B, 0x08, 0x08], mirror)
    check("judge: a device mirroring B0 fails where the VCM sent 0x0B",
          r["verdict"] == "NOT AS SPECIFIED" and r["faults_n"] == 2, r)
    r = run([0x08] * 6, lambda c: bytes([8, 0x00, 0x81]) + held(c)[3:])
    check("judge: torque not zero fails in every frame", r["verdict"] == "NOT AS SPECIFIED" and r["faults_n"] == 5, r)
    src, wire = scenario([0x08] * 5, held)
    prov = P.classify(src, wire, 99, keep_frames=True)
    check("judge: a provenance count mismatch is UNRESOLVED, not a pass",
          B.judge_frames(wire, prov)["verdict"].startswith("UNRESOLVED"))
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("B0 not checked", "    if d[0] != 0x08:\n", "    if False:\n"),
    ("torque not checked", "    if d[1] != 0x00 or d[2] != 0x80:\n", "    if False:\n"),
    ("mirror not checked", "    if d[3] != front[3] or d[4] != front[4]:\n", "    if False:\n"),
    ("counter not checked", "    if (d[5] & 0x0F) != ((front[5] + 1) & 0x0F):\n", "    if False:\n"),
    ("discrimination always claimed", 'discriminating = any(b != "0x08" for b in fronts_b0)',
     "discriminating = True"),
    ("provenance unresolved ignored", "    if prov.get(\"unresolved\"):\n", "    if False:\n"),
    ("front is the frame after", "k = bisect.bisect_left(vt, t) - 1", "k = bisect.bisect_left(vt, t)"),
]


def mutants():
    src = open(os.path.join(HERE, "b24_frame_content_judge.py"), encoding="utf-8").read()
    base = gi_paths.scratch("mutants-b24_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, "b24_frame_content_judge.py"), "w", encoding="utf-8").write(src.replace(old, new))
        shutil.copy(os.path.abspath(__file__), d)
        r = subprocess.run([sys.executable, os.path.join(d, os.path.basename(__file__))],
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=HERE))
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               ("; " + fails[0][:80]) if fails else (", CRASHED" if "Traceback" in r.stderr else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
