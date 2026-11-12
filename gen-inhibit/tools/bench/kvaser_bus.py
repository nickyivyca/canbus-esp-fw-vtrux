"""The one way the bench harness opens the Kvaser and the Innomaker (gs_usb).

Gen-inhibit tester, 2026-11-11, the gen-inhibit reviewer's opener item of that day. On Linux
no Kvaser channel opens through python-can as it is: linuxcan's CANlib refuses python-can's
1-byte canIOCTL_SET_LOCAL_TXACK buffer ("Error in parameter"). And gs_usb's start() begins
with a USB reset, which re-enumerates the device on Linux and loses the handle. can-tools'
canbench.live.linux_fixes (can-tools fa6c0aea) works around both, and is a no-op elsewhere.
canbench comes from gen-inhibit's can-tools pin (tools/can_tools_pin.json, the implementor's),
loaded by file from scratch/can-tools/<commit>/ by can_tools_pin.use(), which refuses a
canbench imported from anywhere else (AGENTS.md "can-tools").

  kvaser_bus(**kw)     linux_fixes.apply(), then can.Bus(interface="kvaser", **kw)
  gs_usb_bus(**kw)     linux_fixes.apply(), then can.Bus(interface="gs_usb", **kw)
  close_gs_usb(bus)    bus.shutdown(), then canbench's release_gs_usb(bus). python-can's
                       GsUsbBus.shutdown() leaves the pyusb handle open, so a second open in
                       the same process, or canctl handing the Innomaker back to socketcan
                       from it, fails without a word (reviewer, 2026-11-11). Call it, not
                       bus.shutdown(), for every gs_usb bus.
  bus(**kw)            for a tool that opens an interface named at run time: kvaser and
                       gs_usb through the two above, anything else straight to can.Bus
  fixes()              the fixes' result, {"kvaser": bool, "gs_usb": bool}, applied once per
                       process (canbench's own apply() is idempotent too)

Every harness file opens these adapters through this module; test_kvaser_bus.py scans for a
can.Bus that opens either directly, or with an interface named at run time. The tools import
it where they open the bus, not at their top, so a tool's copy in a mutant runner's scratch
folder imports without it.
"""
import importlib
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_TOOL = os.path.join(os.path.dirname(HERE), "can_tools_pin.py")
_fixes = None


def _pin_tool():
    spec = importlib.util.spec_from_file_location("_kvaser_bus_can_tools_pin", PIN_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def linux_fixes():
    """canbench.live.linux_fixes from the pinned canbench, loaded by file (can_tools_pin.use)."""
    _pin_tool().use()
    return importlib.import_module("canbench.live.linux_fixes")


def fixes():
    global _fixes
    if _fixes is None:
        _fixes = linux_fixes().apply()
    return _fixes


def kvaser_bus(**kw):
    fixes()
    import can
    return can.Bus(interface="kvaser", **kw)


def gs_usb_bus(**kw):
    fixes()
    import can
    return can.Bus(interface="gs_usb", **kw)


def close_gs_usb(b):
    try:
        b.shutdown()
    finally:
        _pin_tool().use()
        importlib.import_module("canbench.live.receiver").release_gs_usb(b)


def bus(**kw):
    iface = kw.pop("interface")
    if iface == "kvaser":
        return kvaser_bus(**kw)
    if iface == "gs_usb":
        return gs_usb_bus(**kw)
    import can
    return can.Bus(interface=iface, **kw)
