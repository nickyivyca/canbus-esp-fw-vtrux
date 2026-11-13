"""A canmain check that passes with no hardware, for tests that start the
runner (shared/bench SPEC section 8: the runner refuses a run whose settings
carry no canmain_check, and probes canmain after its take).

The check runs for real, on made-up inputs: a sysfs tree with one USB root hub
and no canmain, and a Plug and Play listing with one root hub, so the probe
"absent" passes in either environment. The same inputs as test_run_core's.
Probe "absent" never runs canctl (the bring-down after the take is probe
link's only); canctl is pointed at a command that exits 9 all the same, so
no test can reach the real one.

    import _canmain_fixture
    CANMAIN = _canmain_fixture.install(SCRATCH)   # after sys.path names the package
    settings = dict(..., canmain_check=dict(CANMAIN))
"""
import os
import sys

SERIAL = "TESTCANMAIN01"
PNP_IDS = ["USB\\ROOT_HUB30\\4&2D0A1B&0&0"]


def install(scratch):
    """Make the made-up tree under `scratch`, point vtrux_bench's canmain
    module at it (the one this process imported), and return the setting."""
    from vtrux_bench.run import canmain as CM
    root = os.path.join(scratch, "_canmain_sys")
    hub = os.path.join(root, "bus", "usb", "devices", "usb1")
    os.makedirs(hub)
    os.makedirs(os.path.join(root, "class", "net"))
    with open(os.path.join(hub, "serial"), "w") as f:
        f.write("0000:00:14.0\n")
    serial_file = os.path.join(scratch, "_canmain_serial.md")
    with open(serial_file, "w") as f:
        f.write("# made up\n\n- canmain serial: `%s`\n" % SERIAL)
    CM.SYS_ROOT = root
    CM.pnp_ids = lambda: list(PNP_IDS)
    CM.CANCTL = [sys.executable, "-c", "import sys; sys.exit(9)"]
    return {"probe": "absent", "reason": "made-up bench, no canmain",
            "iface": "canmain", "serial_file": serial_file}
