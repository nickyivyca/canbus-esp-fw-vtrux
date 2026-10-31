#!/usr/bin/env python3
"""Mutants of bench_flash_wire.py; test_bench_flash_wire.py must fail on each BY A
CHECK. A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 test_bench_flash_wire_mutants.py

Each mutant copies bench_flash_wire.py and the test into a fresh folder in the
worktree's scratch/, applies one edit, and runs the test there. Gen-inhibit
tester, 2026-10-17.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1. Until then a mutant that crashed after a FAIL line was also
counted as killed.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
SRC = "bench_flash_wire.py"
TEST = "test_bench_flash_wire.py"

MUTANTS = [
    ("the bytes are the relative ack itself", '"bytes_acked": hi - 1', '"bytes_acked": hi'),
    ("a window of one segment is closed", '"window_closed": last["win"] < seg', '"window_closed": last["win"] <= seg'),
    ("only a zero window is closed", '"window_closed": last["win"] < seg', '"window_closed": last["win"] == 0'),
    ("one segment is always 1440", "seg = max((r[\"len\"] for r in conn if r[\"src\"] != host), default=0) or SEGMENT_FALLBACK",
     "seg = SEGMENT_FALLBACK"),
    ("a bare FIN is an ACK", 'and not (r["syn"] or r["fin"] or r["rst"])', 'and not (r["syn"] or r["rst"])'),
    ("an RST is an ACK", 'and not (r["syn"] or r["fin"] or r["rst"])', 'and not (r["syn"] or r["fin"])'),
    ("reply data is an ACK", 'r["ackf"] and r["len"] == 0', 'r["ackf"]'),
    ("the upload is the stream with most packets", 'sent[r["stream"]] = sent.get(r["stream"], 0) + r["len"]',
     'sent[r["stream"]] = sent.get(r["stream"], 0) + 1'),
    ("times always from the upload's start", "t0 = syn if syn is not None else t_start", "t0 = t_start"),
    ("the shortest gap, not the longest", 'gap = max(((acks[i + 1]["t"]', 'gap = min(((acks[i + 1]["t"]'),
    ("closed and open swapped in the line", 'elif s["window_closed"]:', 'elif not s["window_closed"]:'),
    ("RST blamed on the other side", '"device" if r["src"] == host else "laptop"', '"laptop" if r["src"] == host else "device"'),
    ("flags read from 1 only", 'return v.strip() in ("1", "True", "true")', 'return v.strip() in ("1",)'),
    ("an interface matched by prefix", 'if s.endswith("(%s)" % name) and ". " in s:', 'if ("(%s" % name) in s and ". " in s:'),
    ("no dumpcap is not checked", '        if not dumpcap:\n            return False, "no dumpcap (Wireshark not installed)"\n', ""),
    ("dumpcap -D failing raises", "        except (OSError, subprocess.SubprocessError) as e:\n            return False, \"dumpcap -D failed: %s\" % e\n",
     "        except subprocess.SubprocessError as e:\n            return False, \"dumpcap -D failed: %s\" % e\n"),
    # The reviewer's 2026-10-17 rulings: a third label, and the silence after the last ACK.
    ("the whole request needs one byte more", '"whole_acked": req is not None and hi - 1 >= req,',
     '"whole_acked": req is not None and hi - 1 > req,'),
    ("any upload is the whole one", '"whole_acked": req is not None and hi - 1 >= req,', '"whole_acked": True,'),
    ("the request is its Content-Length only", "return end + 4 + int(v.strip())", "return int(v.strip())"),
    ("the whole-upload label never printed", 'if s.get("whole_acked"):', "if False:"),
    ("no reply status read", 'if len(parts) >= 2 and parts[0].startswith(b"HTTP/") and parts[1].isdigit():',
     "if False:"),
    ("silence counted from the first ACK", 'round(max(0.0, t_end - last["t"]), 3)', 'round(max(0.0, t_end - acks[0]["t"]), 3)'),
    ("a colon-separated payload is not read", '.replace(":", "")', ""),
]


def main():
    base = gi_paths.scratch("mutants-bench_flash_wire")
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed; and a crash is not a kill
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:80]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
