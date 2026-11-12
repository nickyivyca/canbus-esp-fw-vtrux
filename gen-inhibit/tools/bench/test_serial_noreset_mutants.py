#!/usr/bin/env python3
"""Mutants of serial_noreset.py and of the tools that open a console through it;
test_serial_noreset.py must fail on each BY A CHECK. A crash is not a kill, and an edit
that does not apply is an error.

    python3.13 -B gen-inhibit/tools/bench/test_serial_noreset_mutants.py

Each mutant is a copy of the files in the worktree's scratch/, one edit applied to one of
them. The unmutated copy runs first, as the control: if it fails, no mutant is judged and
the run exits 1. Among the mutants, each tool put back on the old form (DTR and RTS pre-set
low, then open), which is what the test must catch on Linux. Gen-inhibit tester,
2026-11-11.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = "test_serial_noreset.py"
FILES = ("serial_noreset.py", "x2_jammer.py", "x2_pins_noreset_check.py", "console_capture.py",
         "serial_boot_log.py", "gi_paths.py", "bench_held.py", "redact_secrets.py",
         "witness_clock.py")      # x2_jammer imports it since 2026-11-11 (A2 (b))
OLD = ("        s.dtr = False\n        s.rts = False\n        s.open()\n        s.dtr = False\n"
       "        s.rts = False\n")
NEW = ("        s.open()            # the kernel asserts both lines: no reset\n"
       "        s.rts = False       # RTS first: DTR asserted with RTS clear is no reset either\n"
       "        s.dtr = False\n")


def old_inline(indent, port, baud, timeout, ret):
    """The tools' open as it was until 2026-11-11, at the given indent."""
    lines = ["import serial", "s = serial.Serial()", "s.port, s.baudrate, s.timeout = %s, %s, %s" % (port, baud, timeout),
             "s.dtr = False", "s.rts = False", "s.open()", "s.dtr = False", "s.rts = False"] + ([ret] if ret else [])
    return "".join(indent + ln + "\n" for ln in lines)


MUTANTS = [
    ("Linux opens with both pre-set low (the old form)", "serial_noreset.py", NEW, OLD),
    ("Linux clears DTR before RTS", "serial_noreset.py", NEW,
     "        s.open()\n        s.dtr = False\n        s.rts = False\n"),
    ("Linux leaves both lines asserted", "serial_noreset.py", NEW, "        s.open()\n"),
    ("Windows takes the Linux form", "serial_noreset.py", '    if os_name() == "nt":\n', "    if False:\n"),
    ("every system takes the Windows form", "serial_noreset.py", '    if os_name() == "nt":\n', "    if True:\n"),
    ("a serial_mod given is ignored", "serial_noreset.py",
     "    if serial_mod is None:\n        import serial as serial_mod\n", "    import serial as serial_mod\n"),
    ("x2_jammer's console back on the old form", "x2_jammer.py",
     "        from serial_noreset import open_console\n        return open_console(self.port, 115200, 0.05)\n",
     old_inline("        ", "self.port", "115200", "0.05", "return s")),
    ("x2_jammer's console on another port", "x2_jammer.py",
     "        return open_console(self.port, 115200, 0.05)\n", '        return open_console("COM3", 115200, 0.05)\n'),
    ("x2_pins' serial read back on the old form", "x2_pins_noreset_check.py",
     "    from serial_noreset import open_console\n    s = open_console(port, 115200, 0.2)\n",
     old_inline("    ", "port", "115200", "0.2", None)),
    ("x2_pins' serial read never closes", "x2_pins_noreset_check.py",
     "    finally:\n        s.close()\n", "    finally:\n        pass\n"),
    ("console_capture back on the old form", "console_capture.py",
     "        return open_console(self.port, self.baud, 0.1)\n",
     old_inline("        ", "self.port", "self.baud", "0.1", "return s")),
    ("serial_boot_log back on the old form", "serial_boot_log.py",
     "    return open_console(port, baud, 0.2)\n", old_inline("    ", "port", "baud", "0.2", "return s")),
]


def main():
    base = gi_paths.scratch("mutants-serial_noreset")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in FILES}
    killed = errors = 0
    for n, (name, target, old, new) in enumerate([("the control: the unmutated copy", None, None, None)] + MUTANTS):
        if old is not None and srcs[target].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[target].count(old), target))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        for f, s in srcs.items():
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(s.replace(old, new) if f == target else s)
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "CRASH" in r.stdout
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % (
                "PASS    " if ok else "FAILED  ", name, r.returncode, len(fails),
                ", CRASHED: " + (r.stderr.strip().splitlines() or ["CRASH in a check"])[-1][:120] if crashed else "",
                ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED: " + (r.stderr.strip().splitlines() or ["CRASH in a check"])[-1][:120] if crashed else "",
            ("; " + fails[0][:100]) if fails else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
