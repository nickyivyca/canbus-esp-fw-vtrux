"""Hold the X2 in its ROM download mode (off the CAN bus) and read its CAN1 pins.

Gen-inhibit tester, 2026-10-10. Approved by the reviewer the same day for the
OBSERVE ACK-error case and the interposer-presence arms, as a way to take the
X2 off the WiCAN's segment with no cable move and no reflash. Conditions: the
X2 is identified on the wire before and after, its silence is shown on the wire
before anything relies on it, it is back in its app at the end, and the TX
pin's state while held is recorded. The X2 is shared with the charge-interposer
sessions, so a hold is noted in firmware/bench-holder.md.

In download mode the app is not running, so its TWAI controller is not either:
the X2 cannot ACK, cannot send and cannot bridge. What then decides the wire is
its CAN1 transceiver's TXD input, on GPIO7 (RXD is GPIO6;
charge-interposer firmware `CAN1_TX_GPIO = 7`). The hold reads, by esptool
read-mem in the ROM (ESP32-S3 addresses from ESP-IDF v5.4.1
components/soc/esp32s3/register/soc/{reg_base,gpio_reg,io_mux_reg}.h):

  GPIO_ENABLE_REG   0x60004020  bit 7: GPIO7 output driver enabled
  GPIO_IN_REG       0x6000403C  bits 7 / 6: GPIO7 / GPIO6 input levels
  GPIO_FUNC7_OUT_SEL_CFG_REG 0x60004570  what drives GPIO7's output, if enabled
  IO_MUX_GPIO7_REG  0x60009020  FUN_PD bit 7, FUN_PU bit 8, FUN_IE bit 9,
                                MCU_SEL bits 12-14
  IO_MUX_GPIO6_REG  0x6000901C  (the same fields, for RXD)

A TXD that is NOT driven and reads 1 is recessive. A TXD driven, or reading 0,
is not, and `decode` says so. GPIO6 reads the bus as the transceiver sees it
(1 = recessive), a spot sample only.

    py -3.14 x2_hold.py hold      # reset into download mode, read the pins
    py -3.14 x2_hold.py pins      # read the pins again, no reset
    py -3.14 x2_hold.py release   # hard reset into the app
Each writes runs/x2_hold_<cmd>_<stamp>.json. Identify the app afterwards with
interposer-firmware/bench_identify_board.py --require-witness.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
# GI_RUNS_DIR: the chain runner points a check's records at its own folder (2026-10-18).
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()
ESPTOOL = os.path.join(os.path.expanduser("~"), ".platformio", "packages",
                       "tool-esptoolpy", "esptool.py")

REGS = {
    "GPIO_ENABLE": 0x60004020,
    "GPIO_IN": 0x6000403C,
    "GPIO7_OUT_SEL": 0x60004570,
    "IO_MUX_GPIO7": 0x60009020,
    "IO_MUX_GPIO6": 0x6000901C,
}
TX, RX = 7, 6


def parse_read_mem(text, addr):
    """The value esptool printed for addr ('0x60004020 = 0x00000000'), or None."""
    m = re.search(r"0x%08x\s*=\s*0x([0-9a-fA-F]{1,8})" % addr, text, re.I)
    return int(m.group(1), 16) if m else None


def _mux(v):
    return {"pulldown": bool(v >> 7 & 1), "pullup": bool(v >> 8 & 1),
            "input_enabled": bool(v >> 9 & 1), "mcu_sel": v >> 12 & 7}


def decode(regs):
    """TX/RX pin state from the register values. Missing values -> 'unknown'."""
    if any(regs.get(k) is None for k in REGS):
        return {"verdict": "unknown", "why": "a register was not read",
                "missing": [k for k in REGS if regs.get(k) is None]}
    tx_driven = bool(regs["GPIO_ENABLE"] >> TX & 1)
    tx_level = regs["GPIO_IN"] >> TX & 1
    rx_level = regs["GPIO_IN"] >> RX & 1
    out = {"tx_gpio": TX, "tx_driven": tx_driven, "tx_level": tx_level,
           "tx_out_sel": regs["GPIO7_OUT_SEL"] & 0x1FF,
           "tx_iomux": _mux(regs["IO_MUX_GPIO7"]),
           "rx_gpio": RX, "rx_level": rx_level, "rx_iomux": _mux(regs["IO_MUX_GPIO6"])}
    # GPIO_IN reads 0 for a pin whose input is disabled, whatever the pin's real level:
    # the ROM leaves GPIO6/7 with FUN_IE clear (read 2026-10-10), and the first version
    # called that "dominant". `enable-inputs` sets FUN_IE so the levels can be read.
    if not out["rx_iomux"]["input_enabled"]:
        out["rx_level"] = None
    if not out["tx_iomux"]["input_enabled"]:
        out["tx_level"] = None
        out["verdict"] = "driven" if tx_driven else "not driven, level unreadable"
        out["why"] = ("GPIO7 input disabled (FUN_IE 0), so GPIO_IN says nothing about its level"
                      + ("; its output driver is enabled" if tx_driven else ""))
    elif not tx_driven and tx_level == 1:
        out["verdict"] = "recessive"
        out["why"] = "GPIO7 output not enabled and it reads 1: TXD idles recessive"
    elif tx_driven:
        out["verdict"] = "driven"
        out["why"] = "GPIO7's output driver is enabled (out_sel %d)" % out["tx_out_sel"]
    else:
        out["verdict"] = "dominant"
        out["why"] = "GPIO7 is not driven but reads 0"
    return out


def esptool(port, before, after, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, ESPTOOL, "--chip", "esp32s3", "--port", port,
           "--before", before, "--after", after] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return p.returncode, (p.stdout + p.stderr)


def read_regs(port, first_before):
    regs, raw = {}, []
    before = first_before
    for name, addr in REGS.items():
        rc, txt = esptool(port, before, "no_reset", "read-mem", "0x%08x" % addr)
        regs[name] = parse_read_mem(txt, addr) if rc == 0 else None
        raw.append([name, rc, [ln for ln in txt.splitlines() if "0x%08x" % addr in ln.lower()
                               or "error" in ln.lower()][-2:]])
        before = "no_reset"
    return regs, raw


def read_with_inputs(port):
    """Set FUN_IE (bit 9) on GPIO7 and GPIO6 and read every register, in ONE esptool
    session. Input enable changes no output; the reset at release clears it.

    One session, because a separate `esptool write-mem` reported success and the next
    connection read the bit clear again (2026-10-10): something in each new connection
    resets it. So this uses esptool's Python API from PlatformIO's package."""
    sys.path.insert(0, os.path.dirname(ESPTOOL))
    from esptool.cmds import detect_chip
    esp = detect_chip(port, 115200, "no_reset").run_stub()
    writes = []
    try:
        for name in ("IO_MUX_GPIO7", "IO_MUX_GPIO6"):
            before = esp.read_reg(REGS[name])
            esp.write_reg(REGS[name], 0x200, mask=0x200)
            writes.append([name, "0x%08x" % before, "0x%08x" % esp.read_reg(REGS[name])])
        regs = {k: esp.read_reg(v) for k, v in REGS.items()}
    finally:
        esp._port.close()
    return regs, writes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["hold", "pins", "enable-inputs", "release"])
    ap.add_argument("--port", default="COM3")
    a = ap.parse_args()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    rec = {"when": stamp, "cmd": a.cmd, "port": a.port}
    if a.cmd == "enable-inputs":
        regs, rec["writes"] = read_with_inputs(a.port)
        rec.update({"regs": {k: (None if v is None else "0x%08x" % v) for k, v in regs.items()},
                    "pins": decode(regs)})
        print("%s: TX (GPIO%d) %s -- %s; RX (GPIO%d) level %s"
              % (a.cmd, TX, rec["pins"]["verdict"], rec["pins"].get("why"), RX,
                 rec["pins"].get("rx_level")))
    elif a.cmd in ("hold", "pins"):
        regs, raw = read_regs(a.port, "default_reset" if a.cmd == "hold" else "no_reset")
        rec.update({"regs": {k: (None if v is None else "0x%08x" % v) for k, v in regs.items()},
                    "esptool": raw, "pins": decode(regs)})
        print("%s: TX (GPIO%d) %s -- %s; RX (GPIO%d) level %s"
              % (a.cmd, TX, rec["pins"]["verdict"], rec["pins"].get("why"), RX,
                 rec["pins"].get("rx_level")))
    else:
        rc, txt = esptool(a.port, "no_reset", "hard_reset", "read-mem", "0x%08x" % REGS["GPIO_IN"])
        rec.update({"rc": rc, "tail": [ln for ln in txt.splitlines()
                                       if "reset" in ln.lower() or "error" in ln.lower()][-4:]})
        print("release: esptool rc %d, %s" % (rc, rec["tail"]))
    path = os.path.join(RUNS, "x2_hold_%s_%s.json" % (a.cmd, stamp))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    print("wrote", path)
    return 0 if (a.cmd == "release" and rec["rc"] == 0) or \
        (a.cmd != "release" and rec["pins"]["verdict"] != "unknown") else 1


if __name__ == "__main__":
    sys.exit(main())
