"""The background interlock traffic every bench stimulus has to carry.

WHY THIS IS SHARED AND NOT COPIED INTO FIVE HARNESSES. The set of signals the
device needs before it will transmit has now grown three times -- 0x592 for
spec 7.1, then 0x440/0x411 for review A1, then 0x617/0x639 for review B3 -- and
each time, a harness that missed one produced a device that arms, reports
`inhibit_live`, transmits nothing, and reports no error anywhere. That failure
looks exactly like a broken inhibitor and has cost a bench session before. One
definition, imported by every harness, is the only way the next addition does
not cost another.

THE RATES ARE THE MEASURED ONES, not round numbers
(`review_signal_cadence.txt`, four clean captures):

    0x051   100 Hz    the anchor the harnesses replay
    0x639   100 Hz    sent at 20 Hz here; anything inside 0.5 s will do
    0x440   ~20 Hz    16.85-20.0 Hz measured
    0x411   ~20 Hz    16.9-20.0 Hz measured
    0x592    10 Hz    9.97-9.99 Hz measured
    0x617     4 Hz    the tightest against the 0.5 s window, worst gap 308 ms

A harness that drives one of these itself -- wican_disable_bench.py drives
0x411, wican_trip_bench.py drives several -- passes it in `skip`, exactly as
make_scenarios.py's injector does for the host suite. Sending both would fight:
the low-SoC release counts CONSECUTIVE sub-threshold readings, so a healthy
20 Hz train resets the count every 50 ms and the release never latches.

Usage inside a 100 Hz replay loop:

    from bench_interlocks import send_interlocks
    for i, data in enumerate(frames):
        pcan.send(vcm_frame(data))
        send_interlocks(pcan, i)                      # everything
        send_interlocks(pcan, i, skip=(0x411,))       # harness drives SoC
"""

import can

# --- frame builders, each matching the DBC the core's extractor was checked
# --- against (see test/gen_inhibit_host/test_signals.py)

KEY_ID = 0x592
CONTACTOR_ID = 0x440
SOC_ID = 0x411
FAULT_ID = 0x617
SHIFT_ID = 0x639

MAINC_CLOSED_DRIVE = 11     # MAIN_PN_CLOSED_DRIVE
MAINC_CLOSED_CHARGE = 12    # MAIN_P_CLOSED_CHARGE

FAULT_CLEAR = 0xC8
FAULT_ACTIVE = 0xCA

HEALTHY_SOC_RAW = 5000      # 50.00 %, well clear of the 21.00 % threshold
DRIVE_SHIFT_POS = 2         # not 4, which is Manual_generator_mode


def key_frame(on=True):
    """0x592 B0 bit 4 (IgnitionKeyState, spec 7.1)."""
    return bytes([0x10 if on else 0x00, 0, 0, 0, 0, 0, 0, 0])


def contactor_frame(stat=MAINC_CLOSED_DRIVE):
    """0x440 bcm_mainc_stat: 4-bit enum at bit 2 of B0 (review A1)."""
    return bytes([(stat & 0x0F) << 2, 0, 0, 0, 0, 0, 0, 0])


def soc_frame(raw=HEALTHY_SOC_RAW):
    """0x411 bms_soc_hires: 14-bit BIG-endian at bit 7, scale 0.01 %.

    Big-endian. A little-endian version of this cost an afternoon on
    2026-09-24 before cantools was asked.
    """
    return bytes([(raw >> 6) & 0xFF, (raw & 0x3F) << 2, 0, 0, 0, 0, 0, 0])


def fault_frame(val=FAULT_CLEAR):
    """0x617 B7: 0xCA = VCM fault active, 0xC8 = clear."""
    return bytes([0, 0, 0, 0, 0, 0, 0, val])


def shift_frame(pos=DRIVE_SHIFT_POS):
    """0x639 shift_lever_pos, B6 bits 6:4. 4 = Manual_generator_mode."""
    d = bytearray(8)
    d[6] = (pos & 0x07) << 4
    return bytes(d)


# (id, send on every Nth 0x051, builder) at a 100 Hz 0x051 replay rate.
SCHEDULE = (
    (SHIFT_ID,      5,  shift_frame),
    (CONTACTOR_ID,  5,  contactor_frame),
    (SOC_ID,        5,  soc_frame),
    (KEY_ID,       10,  key_frame),
    (FAULT_ID,     25,  fault_frame),
)


def send_interlocks(bus, i, skip=(), timeout=0.5):
    """Send whichever background frames are due alongside 0x051 number `i`.

    `i` counts 0x051 frames at ~100 Hz. Returns how many frames were sent, so a
    harness can account for what it put on the wire.
    """
    n = 0
    for ident, every, build in SCHEDULE:
        if ident in skip:
            continue
        if i % every == 0:
            bus.send(can.Message(arbitration_id=ident, data=build(),
                                 is_extended_id=False), timeout=timeout)
            n += 1
    return n


def prime_interlocks(bus, cycles=30, period=0.01, skip=(), timeout=0.5):
    """Put the interlocks on the wire BEFORE the run starts.

    The arm gate needs 0x440 closed with an 0x411 after it, plus fresh 0x617
    and 0x639, so a harness that starts counting from its first 0x051 spends
    its first frames blocked on conditions the truck would have satisfied long
    before. Priming costs 0.3 s and removes that from every measurement.
    """
    n = 0
    for i in range(cycles):
        n += send_interlocks(bus, i, skip=skip, timeout=timeout)
        _sleep(period)
    return n


def _sleep(seconds):
    import time
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass
