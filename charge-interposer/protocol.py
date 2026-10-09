"""Wire protocol for the Vtrux charge-interposer bench.

Two halves:

1. `0x18EFC000` -- the VCU -> Bel charger command frame. J1939 PDU1 proprietary-A
   (PF=0xEF, DA=0xC0 = charger, SA=0x00 = VCU). It is defined in no project DBC;
   the page map below was derived by correlating its bytes against the BMS and
   charger signals across three full charge captures. Treat every field name as
   a HYPOTHESIS until bench/vehicle testing confirms it -- nothing here has been
   promoted into a DBC.

     cyclic pages (~3.3 Hz each, ~20 Hz aggregate)
       01     01 [Vlim LE16] [Ilim LE16] 00 00 00     0.05 V / 0.05 A per count
       03.02  03 02 00 <00|75> 00 00 00 00
       03.04  03 04 03 61 00 00 00 00                 constant
       03.06  03 06 00 64 00 32 80 53                 constant
       03.07  03 07 <02|01> 64 04 32 00 00            2 = charging, 1 = complete
       09     09 <SoC%> 00 00 00 00 00 00

     event pages (burst of ~20 frames at 20 Hz on change)
       00     00 <cmd_Enable> <cmd_Mode> 00 00 00 00 00
       03.00  03 00 <0|1> 00 00 00 00 00
       03.01  03 01 01 00 00 00 00 00                 end of session
       03.05  03 05 <0|1> 00 00 00 00 00

   Observed constants: Vlim = 8584 counts = 429.20 V in every session
   (116 cells x 3.700 V) and never approached. Ilim is a live regulator output
   that dithers +/-0.5 A, not a table lookup.

2. DBC-backed encode/decode for the A123 BMS frames (epri-pt-bus.dbc) and the
   Bel charger frames (BelInverter-v2.dbc), so the simulators put exactly the
   bytes on the wire that `canre`/cantools reads back out.
"""

import os

import cantools

# --------------------------------------------------------------------------
# 0x18EFC000 -- VCU -> charger command
# --------------------------------------------------------------------------

CMD_ID = 0x18EFC000

# page keys: (B0,) for B0 != 0x03, (0x03, B1) otherwise
PAGE_SETPOINT = (0x01,)
PAGE_SOC = (0x09,)
PAGE_MASTER = (0x00,)
PAGE_03_00 = (0x03, 0x00)
PAGE_03_01 = (0x03, 0x01)
PAGE_03_02 = (0x03, 0x02)
PAGE_03_04 = (0x03, 0x04)
PAGE_03_05 = (0x03, 0x05)
PAGE_03_06 = (0x03, 0x06)
PAGE_03_07 = (0x03, 0x07)

# the six pages the VCU transmits cyclically, in the observed rotation order
CYCLIC_ROTATION = (PAGE_03_04, PAGE_03_07, PAGE_03_06, PAGE_SETPOINT,
                   PAGE_SOC, PAGE_03_02)

CONST_03_04 = bytes((0x03, 0x04, 0x03, 0x61, 0, 0, 0, 0))
CONST_03_06 = bytes((0x03, 0x06, 0x00, 0x64, 0x00, 0x32, 0x80, 0x53))

# page 00 B2 -- master mode
# cmd_Mode. CONFIRMED by two independent methods: the EPRI app binary's literal
# pool, and log correlation on this vehicle. Note that mode 3 is a LOW POWER
# mode, not a completion state -- that is why the charger holds ~2.2-2.9 A in it
# indefinitely instead of stopping, and why 3 -> 1 restarts full current.
MODE_EXPORT = 0
MODE_CHARGER = 1
MODE_STANDBY = 2
MODE_LOW_POWER = 3
MODE_INVALID = 4

MODE_NAMES = {0: "EXPORT_POWER", 1: "CHARGER", 2: "STAND_BY",
              3: "CHARGER_LOW_POWER", 4: "INVALID"}

UNIT = 0.05          # V and A per count on page 01
VLIM_DEFAULT_COUNTS = 8584   # 429.20 V == 116 cells x 3.700 V


def page_key(data):
    """Page identity of a 0x18EFC000 payload."""
    return (0x03, data[1]) if data[0] == 0x03 else (data[0],)


def page_name(key):
    return ".".join("%02X" % b for b in key)


def enc_setpoint(vlim_counts, ilim_counts):
    """Page 01 -- CC/CV setpoint. Both fields little-endian uint16, 0.05 units."""
    v = int(vlim_counts) & 0xFFFF
    i = max(0, min(0xFFFF, int(ilim_counts)))
    return bytes((0x01, v & 0xFF, v >> 8, i & 0xFF, i >> 8, 0, 0, 0))


def dec_setpoint(data):
    """-> (vlim_volts, ilim_amps). Raises ValueError if not page 01."""
    if data[0] != 0x01:
        raise ValueError("not page 01")
    v = (data[2] << 8) | data[1]
    i = (data[4] << 8) | data[3]
    return v * UNIT, i * UNIT


def setpoint_counts(data):
    """-> (vlim_counts, ilim_counts) without scaling. Page 01 only."""
    return ((data[2] << 8) | data[1], (data[4] << 8) | data[3])


def enc_soc(soc_pct):
    return bytes((0x09, max(0, min(255, int(soc_pct))), 0, 0, 0, 0, 0, 0))


def dec_soc(data):
    return data[1]


def enc_master(flow_enable, mode):
    return bytes((0x00, int(flow_enable) & 0xFF, int(mode) & 0xFF, 0, 0, 0, 0, 0))


def dec_master(data):
    """-> (flow_enable, mode)."""
    return data[1], data[2]


def enc_03(sub, b2, b3=0, b4=0, b5=0):
    return bytes((0x03, sub, b2, b3, b4, b5, 0, 0))


def enc_03_02(complete):
    return bytes((0x03, 0x02, 0x00, 0x75 if complete else 0x00, 0, 0, 0, 0))


def enc_03_07(complete):
    return bytes((0x03, 0x07, 0x01 if complete else 0x02, 0x64, 0x04, 0x32, 0, 0))


# --------------------------------------------------------------------------
# DBC-backed frames
# --------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_VTRUX = os.path.normpath(os.path.join(_HERE, "..", ".."))

EPRI_PT_DBC = os.path.join(_VTRUX, "epri-pt-bus.dbc")
BEL_DBC = os.path.join(_VTRUX, "BelInverter-v2.dbc")

EPRI = cantools.database.load_file(EPRI_PT_DBC)
BEL = cantools.database.load_file(BEL_DBC)

# BMS frames the bench emulates, with their observed broadcast periods (s)
BMS_FRAMES = {
    0x410: 0.100,   # EPRI_BCM_Status_0410
    0x411: 0.100,   # hi-res SoC
    0x420: 0.100,   # EPRI_BCM_Limits_0420
    0x430: 0.100,   # EPRI_BCM_Data1_0430
    0x440: 0.100,   # EPRI_BCM_Data2_0440
}

# Bel charger frames, with their observed rates (from tools/survey.py on a
# charge capture: D4C0 10.1 Hz, D5C0 1.1, D7C0 10.1, D9C0 9.9, DAC0/DBC0 1.1)
CHARGER_FRAMES = {
    0x18FFD4C0: 0.100,   # BINV_status (multiplexed, mux rotates 0..3)
    0x18FFD5C0: 0.900,   # overall temperature
    0x18FFD7C0: 0.100,   # hvBatteryAndCharger  (V / I)
    0x18FFD8C0: 0.900,   # j1772 info
    0x18FFD9C0: 0.100,   # chargeInfo (maxAvailableChargingCurrent, vehicleState)
    0x18FFDAC0: 0.900,   # inverter temperature
    0x18FFDBC0: 0.900,   # buck-boost temperature
}

# charger BELINV_state values seen in the corpus
CHG_STATE_OFF = 10
CHG_STATE_READY = 11
CHG_STATE_CHARGING = 12
CHG_STATE_FAULT = 15
CHG_STATE_TERMINATED = 33

# BELINV_vehicleState == J1772 pilot state (A=1, B=2, C=3): 1 no vehicle,
# 2 connected-not-ready, 3 charging. HYPOTHESIS -- fits every observed session
# (2 during the 980charge startup failures, 3 throughout charging, 3->1 at
# handle pull) but is not independently confirmed.
VEH_STATE_A = 1
VEH_STATE_B = 2
VEH_STATE_C = 3


def _signal_names(msg, mux_value=None):
    names = []
    for sig in msg.signals:
        if sig.multiplexer_ids is None:
            names.append(sig.name)
        elif mux_value is not None and mux_value in sig.multiplexer_ids:
            names.append(sig.name)
    return names


def encode(db, frame_id, values, mux_signal=None, mux_value=None):
    """Encode with zero-filled defaults for every signal not supplied.

    cantools requires a complete signal dict; the simulators only care about a
    handful per frame, so everything else defaults to 0 (which is the resting
    value for all the status bits involved).
    """
    msg = db.get_message_by_frame_id(frame_id)
    full = {}
    for name in _signal_names(msg, mux_value):
        full[name] = 0
    if mux_signal is not None:
        full[mux_signal] = mux_value
    full.update(values)
    # drop anything not in this mux page so a caller can pass a superset
    valid = set(_signal_names(msg, mux_value))
    if mux_signal is not None:
        valid.add(mux_signal)
    full = {k: v for k, v in full.items() if k in valid}
    return msg.encode(full, strict=False)


def decode(db, frame_id, data):
    try:
        return db.decode_message(frame_id, data, decode_choices=False,
                                 allow_truncated=True)
    except Exception:
        return {}
