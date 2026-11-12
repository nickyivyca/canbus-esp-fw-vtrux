"""x2_hold.py's enable-inputs step, run under esptool's own python (GI_ESPTOOL_ARGV[0]).

Gen-inhibit tester, 2026-11-11 (the gen-inhibit reviewer's ruling of that day: x2_hold's
API step as a subprocess under the settings' esptool python, no ~/.platformio). Set
FUN_IE (bit 9) on the IO_MUX registers named by --enable, then read every register in
--regs, in ONE esptool session: a separate `write_mem` reported success and the next
connection read the bit clear again (2026-10-10). Input enable changes no output; the
reset at release clears it.

    <esptool python> -B x2_hold_inputs.py --port P --regs '{"NAME": addr, ...}'
        --enable NAME,NAME --pin 4.13.0

Prints one JSON line, {"version", "regs": {name: value}, "writes": [[name, before,
after], ...]}, and exits 0; or {"refused": reason} and exits 2 when esptool's version is
not --pin. Imports only esptool and the standard library, so it runs in esptool's own
environment. Nothing else is printed on stdout.
"""
import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--regs", required=True)
    ap.add_argument("--enable", required=True)
    ap.add_argument("--pin", required=True)
    a = ap.parse_args(argv)
    regs = {k: int(v) for k, v in json.loads(a.regs).items()}
    enable = [x for x in a.enable.split(",") if x]
    import esptool
    from esptool.cmds import detect_chip
    if esptool.__version__ != a.pin:
        print(json.dumps({"refused": "esptool %s here, not the pinned %s"
                          % (esptool.__version__, a.pin)}))
        return 2
    esp = detect_chip(a.port, 115200, "no_reset").run_stub()
    writes = []
    try:
        for name in enable:
            before = esp.read_reg(regs[name])
            esp.write_reg(regs[name], 0x200, mask=0x200)
            writes.append([name, "0x%08x" % before, "0x%08x" % esp.read_reg(regs[name])])
        got = {k: esp.read_reg(v) for k, v in regs.items()}
    finally:
        esp._port.close()
    print(json.dumps({"version": esptool.__version__, "regs": got, "writes": writes}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
