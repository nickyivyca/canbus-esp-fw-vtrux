"""Harness test for x2_hold: the read_mem parser, the TX pin verdict, and its esptool.

    python3.13 -B gen-inhibit/tools/bench/test_x2_hold.py

The esptool part (gen-inhibit reviewer, 2026-11-11; gi_esptool.py) runs x2_hold.py as a
process against a stand-in esptool package (_fake_esptool.py) named by GI_ESPTOOL_ARGV:
the underscore command name, the chip and resets each command uses, the argv and the
version in every record, a refusal with no record and nothing run when GI_ESPTOOL_ARGV is
unset, of another form or another version, and enable-inputs run under that argv's own
python (x2_hold_inputs.py). No port is opened. X2_HOLD_DIR names a folder holding a mutant
x2_hold.py (test_x2_hold_mutants.py), run in place of this folder's.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOLD_DIR = os.environ.get("X2_HOLD_DIR") or HERE
sys.path.insert(0, HERE)
if HOLD_DIR != HERE:
    sys.path.insert(0, HOLD_DIR)
import x2_hold as H                                              # noqa: E402
import gi_paths                                                  # noqa: E402
import _fake_esptool as FE                                       # noqa: E402

BASE = {"GPIO_ENABLE": 0, "GPIO_IN": (1 << 7) | (1 << 6), "GPIO7_OUT_SEL": 0x100,
        "IO_MUX_GPIO7": (1 << 9) | (1 << 8) | (1 << 12), "IO_MUX_GPIO6": (1 << 9) | (1 << 12)}


def R(**kw):
    d = dict(BASE)
    d.update(kw)
    return d


def static_checks():
    out = "Connecting....\n0x60004020 = 0x00000080\nStaying in bootloader.\n"
    return [
        ("parse: value for the asked address", H.parse_read_mem(out, 0x60004020) == 0x80),
        ("parse: another address is not taken", H.parse_read_mem(out, 0x6000403C) is None),
        ("parse: upper-case hex", H.parse_read_mem("0x6000403C = 0x000000C0", 0x6000403C) == 0xC0),
        ("not driven, reads 1 -> recessive", H.decode(R())["verdict"] == "recessive"),
        ("output enabled on GPIO7 -> driven",
         H.decode(R(GPIO_ENABLE=1 << 7))["verdict"] == "driven"),
        ("output enabled on another GPIO only -> still recessive",
         H.decode(R(GPIO_ENABLE=1 << 8))["verdict"] == "recessive"),
        ("not driven, reads 0 -> dominant", H.decode(R(GPIO_IN=1 << 6))["verdict"] == "dominant"),
        ("bit 6 (RX) is not read as TX", H.decode(R(GPIO_IN=1 << 6))["tx_level"] == 0
         and H.decode(R(GPIO_IN=1 << 7))["rx_level"] == 0),
        ("pull-up bit 8 decoded", H.decode(R())["tx_iomux"]["pullup"] is True
         and H.decode(R(IO_MUX_GPIO7=0))["tx_iomux"]["pullup"] is False),
        ("input disabled (the ROM's state, 2026-10-10 read) -> level unreadable, never dominant",
         (lambda d: d["verdict"] == "not driven, level unreadable" and d["tx_level"] is None
          and d["rx_level"] is None)(H.decode(R(GPIO_IN=0x3c00000d, IO_MUX_GPIO7=0x800,
                                                IO_MUX_GPIO6=0x800)))),
        ("input disabled but output enabled -> driven",
         H.decode(R(GPIO_ENABLE=1 << 7, IO_MUX_GPIO7=0x800))["verdict"] == "driven"),
        ("a register not read -> unknown, never recessive",
         H.decode(R(GPIO_IN=None))["verdict"] == "unknown"),
        ("addresses are the IDF v5.4.1 ones",
         H.REGS == {"GPIO_ENABLE": 0x60004020, "GPIO_IN": 0x6000403C,
                    "GPIO7_OUT_SEL": 0x60004570, "IO_MUX_GPIO7": 0x60009020,
                    "IO_MUX_GPIO6": 0x6000901C} and (H.TX, H.RX) == (7, 6)),
    ]


class Bench:
    """x2_hold.py run as a process with the stand-in esptool; each run in its own folder."""

    def __init__(self, work):
        self.work = work
        self.fake = FE.make(os.path.join(work, "fake"))
        self.n = 0
        # the esptool python by another name than this one (a link to it, where links can be
        # made), so the test can tell which python ran enable-inputs
        self.py = os.path.join(work, "esptool-python")
        try:
            os.symlink(sys.executable, self.py)
        except (OSError, NotImplementedError):
            self.py = None
        self.argv = [self.py or sys.executable, "-m", "esptool"]

    def run(self, args, argv="good", version="4.13.0", regs=None):
        """(rc, stdout, [records], [fake log lines]) of `x2_hold.py <args> --port FAKE`."""
        self.n += 1
        d = os.path.join(self.work, "r%d" % self.n)
        os.makedirs(d)
        log = os.path.join(d, "esptool.log")
        env = {k: v for k, v in os.environ.items() if k not in ("GI_ESPTOOL_ARGV",)}
        env.update(self.fake, GI_RUNS_DIR=d, FAKE_ESPTOOL_LOG=log, FAKE_ESPTOOL_VERSION=version,
                   FAKE_ESPTOOL_REGS=json.dumps({"0x%08x" % a: v for a, v in
                                                 (regs or {}).items()}))
        if argv == "good":
            env["GI_ESPTOOL_ARGV"] = json.dumps(self.argv)
        elif argv is not None:
            env["GI_ESPTOOL_ARGV"] = json.dumps(argv)
        p = subprocess.run([sys.executable, "-B", os.path.join(HOLD_DIR, "x2_hold.py")] + args
                           + ["--port", "FAKE"], capture_output=True, text=True, env=env, timeout=120)
        recs = []
        for n in sorted(os.listdir(d)):
            if n.startswith("x2_hold_") and n.endswith(".json"):
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    recs.append(json.load(f))
        lines = open(log).read().splitlines() if os.path.exists(log) else []
        return p.returncode, p.stdout + p.stderr, recs, lines


# the X2 held with its inputs enabled: TX not driven and reading 1, RX reading 1
HELD = {H.REGS["GPIO_IN"]: (1 << 7) | (1 << 6), H.REGS["IO_MUX_GPIO7"]: 0x200 | 0x1000,
        H.REGS["IO_MUX_GPIO6"]: 0x200 | 0x1000}
# the ROM's state: inputs disabled (FUN_IE clear)
ROM = {H.REGS["GPIO_IN"]: (1 << 7) | (1 << 6), H.REGS["IO_MUX_GPIO7"]: 0x1000,
       H.REGS["IO_MUX_GPIO6"]: 0x1000}


def esptool_checks(b):
    out = []

    def cli(lines):
        return [ln[4:] for ln in lines if ln.startswith("cli ")]

    rc, txt, recs, lines = b.run(["pins"], regs=HELD)
    reads = [c for c in cli(lines) if "read_mem" in c]
    out += [
        ("pins: reads the five registers by read_mem and passes (recessive)",
         rc == 0 and len(reads) == 5 and len(recs) == 1 and recs[0]["pins"]["verdict"] == "recessive",
         (rc, txt[-300:], cli(lines))),
        ("pins: no dashed command name, each on --chip esp32s3 with no reset before or after",
         bool(reads) and all(" --chip esp32s3 --port FAKE --before no_reset --after no_reset read_mem 0x"
                             in " " + c for c in reads)
         and not any("read-mem" in c for c in cli(lines)), reads),
        ("pins: the version is read first, by `version`", cli(lines)[:1] == ["version"], cli(lines)[:2]),
        ("pins: the record holds esptool's argv and its version",
         bool(recs) and recs[0].get("esptool_argv") == b.argv and recs[0].get("esptool_version") == "4.13.0",
         recs and {k: recs[0].get(k) for k in ("esptool_argv", "esptool_version")}),
    ]
    rc, txt, recs, lines = b.run(["hold"], regs=HELD)
    reads = [c for c in cli(lines) if "read_mem" in c]
    out.append(("hold: the first read resets into download mode, the rest do not",
                rc == 0 and len(reads) == 5 and "--before default_reset --after no_reset" in reads[0]
                and all("--before no_reset --after no_reset" in c for c in reads[1:]), reads))
    rc, txt, recs, lines = b.run(["release"], regs=HELD)
    reads = [c for c in cli(lines) if "read_mem" in c]
    out.append(("release: one read_mem with a hard reset after, recorded with the version",
                rc == 0 and len(reads) == 1 and "--before no_reset --after hard_reset read_mem" in reads[0]
                and bool(recs) and recs[0].get("esptool_version") == "4.13.0", (rc, reads, txt[-200:])))
    for label, kw in (("GI_ESPTOOL_ARGV unset", {"argv": None}),
                      ("an argv of another form", {"argv": [sys.executable, "esptool.py"]}),
                      ("esptool 5.0.2", {"version": "5.0.2"})):
        rc, txt, recs, lines = b.run(["pins"], regs=HELD, **kw)
        out.append(("%s: refused (exit 2), no record, no board command" % label,
                    rc == 2 and "REFUSED" in txt and not recs
                    and not [c for c in cli(lines) if c != "version"], (rc, txt[-200:], recs, lines)))
    rc, txt, recs, lines = b.run(["enable-inputs"], regs=ROM)
    api = [ln[4:] for ln in lines if ln.startswith("api ")]
    rec = recs[0] if recs else {}
    out += [
        ("enable-inputs: FUN_IE set on GPIO7 and GPIO6 in one session, then read: recessive",
         rc == 0 and rec.get("pins", {}).get("verdict") == "recessive"
         and [w[0] for w in rec.get("writes", [])] == ["IO_MUX_GPIO7", "IO_MUX_GPIO6"]
         and all(int(w[2], 16) & 0x200 and not int(w[1], 16) & 0x200 for w in rec.get("writes", []))
         and [a.split()[0] for a in api] == ["detect_chip", "run_stub", "write_reg", "write_reg", "close"],
         (rc, txt[-300:], rec.get("writes"), api)),
        ("enable-inputs: the record holds esptool's argv and its version",
         rec.get("esptool_argv") == b.argv and rec.get("esptool_version") == "4.13.0",
         {k: rec.get(k) for k in ("esptool_argv", "esptool_version")}),
    ]
    if b.py:
        out.append(("enable-inputs: run under GI_ESPTOOL_ARGV's python, not x2_hold's",
                    bool(api) and api[0].endswith("python=%s" % b.py), api[:1]))
    else:
        print("  NOT RUN enable-inputs under GI_ESPTOOL_ARGV's python: no link could be made here")
    # x2_hold_inputs.py checks the pin itself
    env = dict(os.environ, **b.fake)
    env["FAKE_ESPTOOL_VERSION"] = "4.13.0"
    p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "x2_hold_inputs.py"), "--port", "FAKE",
                        "--regs", json.dumps(H.REGS), "--enable", "IO_MUX_GPIO7", "--pin", "9.9.9"],
                       capture_output=True, text=True, env=env, timeout=60)
    out.append(("x2_hold_inputs: another esptool version than --pin refuses (exit 2), touching nothing",
                p.returncode == 2 and "refused" in p.stdout, (p.returncode, p.stdout[-200:])))
    return out


def main():
    work = gi_paths.scratch("x2_hold")
    checks = [(label, ok, None) for label, ok in static_checks()]
    checks += esptool_checks(Bench(work))
    fails = []
    for label, ok, got in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            if got is not None:
                print("       got %s" % (got,))
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
