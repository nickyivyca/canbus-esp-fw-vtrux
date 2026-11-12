"""A made-up bench of serial devices for the harness's tests: the X2 and the WiCAN, each an
Espressif USB-Serial-JTAG (303a:1001, three interfaces, the serial port on interface 0),
with made-up serials, in a made-up sysfs and /dev/serial/by-id/ folder. No hardware.

The tree is the pinned core's own test fixture (shared/bench tests/_serial_fixture.py,
FakeSerial), loaded by file from beside the vtrux_bench on PYTHONPATH, so it is laid out as
the core's lookup expects (seen on madhouse-debian, 2026-11-11).

    env, ports = fake_bench(folder)
    subprocess.run([... "x2_hold.py", "pins"], env=dict(os.environ, **env))
    # the tool resolves GI_X2_DEVICE through gi_serial to ports["x2"], a by-id link

env sets GI_X2_DEVICE and GI_WICAN_DEVICE, and gi_serial's test-only GI_SERIAL_SYS_ROOT and
GI_SERIAL_BY_ID. Gen-inhibit tester, 2026-11-12.
"""
import importlib.util
import json
import os

X2_SERIAL, WICAN_SERIAL = "MADEUPX2SERIAL01", "MADEUPWICAN01"
ESP = ("303a", "1001")


def fixture():
    """The core's FakeSerial class, from tests/ beside the importable vtrux_bench."""
    import vtrux_bench
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(vtrux_bench.__file__))),
                        "tests", "_serial_fixture.py")
    spec = importlib.util.spec_from_file_location("_core_serial_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FakeSerial


def device(serial):
    return {"vid": ESP[0], "pid": ESP[1], "serial": serial, "interface": 0}


def fake_bench(folder, x2=True, wican=True):
    """(env, ports): a made-up bench in folder with the boards asked for plugged in."""
    fs = fixture()(folder)
    ports = {}
    if x2:
        ports["x2"] = fs.add("ttyACM1", *ESP, serial=X2_SERIAL, interface=0, interfaces=3)
    if wican:
        ports["wican"] = fs.add("ttyACM2", *ESP, serial=WICAN_SERIAL, interface=0, interfaces=3)
    env = {"GI_X2_DEVICE": json.dumps(device(X2_SERIAL)),
           "GI_WICAN_DEVICE": json.dumps(device(WICAN_SERIAL)),
           "GI_SERIAL_SYS_ROOT": fs.root, "GI_SERIAL_BY_ID": fs.by_id}
    return env, ports
