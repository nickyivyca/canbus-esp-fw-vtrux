"""Cross-check machine.py's hand-rolled bit extraction against cantools.

machine.py must stay free of cantools (it ports to the micro), so it decodes
the BMS and charger frames with explicit byte arithmetic. This test is what
makes that safe: it encodes random values through the real DBCs and asserts the
hand-rolled extractors return the same numbers cantools does.

Run:  python3.13 projects/vtrux/tools/interposer/test_signals.py
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import machine as M
import protocol as P

FAILS = []


def check(name, got, want, tol=0):
    if isinstance(got, tuple) or isinstance(want, tuple):
        if tuple(got) != tuple(want):
            FAILS.append("%s: got %r want %r" % (name, got, want))
    elif abs(got - want) > tol:
        FAILS.append("%s: got %r want %r" % (name, got, want))


def main():
    rng = random.Random(20260901)

    for _ in range(2000):
        vmax = rng.uniform(2.0, 3.799)
        vmin = rng.uniform(2.0, vmax)
        tmax = rng.randrange(-40, 80)
        tmin = rng.randrange(-40, tmax + 1)
        d = P.encode(P.EPRI, 0x430, {
            "bcm_cell_vmax": vmax, "bcm_cell_vmin": vmin,
            "bcm_cell_tmax": tmax, "bcm_cell_tmin": tmin})
        ref = P.decode(P.EPRI, 0x430, d)
        check("vmax_mv", M.vmax_mv(d), round(ref["bcm_cell_vmax"] * 1000), 1)
        check("vmin_mv", M.vmin_mv(d), round(ref["bcm_cell_vmin"] * 1000), 1)
        check("tmax_ddegc", M.tmax_ddegc(d), round(ref["bcm_cell_tmax"] * 10), 1)
        check("tmin_ddegc", M.tmin_ddegc(d), round(ref["bcm_cell_tmin"] * 10), 1)

        chg = rng.uniform(0, 1023.75)
        ov, uv = rng.randrange(2), rng.randrange(2)
        d = P.encode(P.EPRI, 0x420, {
            "bcm_chg_max": chg, "bcm_dis_max": rng.uniform(0, 1023.75),
            "bcm_cell_overvolt": ov, "bcm_cell_undervolt": uv})
        ref = P.decode(P.EPRI, 0x420, d)
        check("chg_max_ca", M.chg_max_ca(d), round(ref["bcm_chg_max"] * 100), 1)
        check("cell_overvolt", M.cell_overvolt(d), ref["bcm_cell_overvolt"])
        check("cell_undervolt", M.cell_undervolt(d), ref["bcm_cell_undervolt"])

        soc = rng.randrange(0, 201) * 0.5
        epo, hvil, done = rng.randrange(2), rng.randrange(2), rng.randrange(2)
        alarm = rng.randrange(4)
        d = P.encode(P.EPRI, 0x410, {
            "bcm_soc": soc, "bcm_epo": epo, "bcm_hvil_mon": hvil,
            "bcm_alarm": alarm, "bcm_chg_done": done,
            "bcm_ibat": rng.uniform(-900, 600), "bcm_vbat": rng.uniform(0, 500)})
        ref = P.decode(P.EPRI, 0x410, d)
        check("soc_half", M.soc_half_pct(d) * 0.5, ref["bcm_soc"], 0.01)
        check("bms_epo", M.bms_epo(d), ref["bcm_epo"])
        check("bms_hvil_mon", M.bms_hvil_mon(d), ref["bcm_hvil_mon"])
        check("bms_alarm", M.bms_alarm(d), ref["bcm_alarm"])
        check("bms_chg_done", M.bms_chg_done(d), ref["bcm_chg_done"])
        check("ibat_ca", M.ibat_ca(d), round(ref["bcm_ibat"] * 100), 1)

        ms = rng.randrange(16)
        d = P.encode(P.EPRI, 0x440, {
            "bcm_mainc_stat": ms, "bcm_balancing_cnt": rng.randrange(254),
            "bcm_vbus_pos": rng.uniform(0, 500)})
        ref = P.decode(P.EPRI, 0x440, d)
        check("mainc_stat_4", M.mainc_stat_4(d), ref["bcm_mainc_stat"])

        st = rng.randrange(256)
        d = P.encode(P.BEL, 0x18FFD4C0, {"BELINV_state": st},
                     "BELINV_statusMultiplexer", 0)
        ref = P.decode(P.BEL, 0x18FFD4C0, d)
        check("chg_state", M.chg_state(d), ref["BELINV_state"])
        check("chg_mux", M.chg_mux(d), 0)

        inv, bb, ov2, ot = (rng.randrange(2) for _ in range(4))
        d = P.encode(P.BEL, 0x18FFD4C0, {
            "BELINV_inverterFault": inv, "BELINV_buckBoostFault": bb,
            "BELINV_hvBatteryOverVoltage": ov2, "BELINV_overTemperature": ot},
            "BELINV_statusMultiplexer", 3)
        ref = P.decode(P.BEL, 0x18FFD4C0, d)
        check("chg_fault_bits", M.chg_fault_bits(d),
              (ref["BELINV_inverterFault"], ref["BELINV_buckBoostFault"],
               ref["BELINV_hvBatteryOverVoltage"], ref["BELINV_overTemperature"]))

    # 0x649 vcm_evap_active lives in the experimental DBC, which protocol.py
    # does not load (the sims never needed it); load it here for the check.
    import cantools
    vcm = cantools.database.load_file(os.path.join(
        P._VTRUX, "vtrux-powertrain-experimental.dbc"), strict=False)
    msg649 = vcm.get_message_by_frame_id(0x649)
    for _ in range(500):
        flag = rng.randrange(2)
        d = msg649.encode({"vcm_evap_active": flag,
                           "vcm_evap_counter": rng.randrange(4096)})
        d = bytes(d[i] if i in (2, 3, 4) else rng.randrange(256) for i in range(8))
        ref = msg649.decode(d, decode_choices=False)
        check("evap_active", M.evap_active(d), ref["vcm_evap_active"])

    for _ in range(500):
        a = rng.uniform(0, 400)
        d = P.encode(P.BEL, 0x18FFD9C0, {
            "BELINV_maxAvailableChargingCurrent": a, "BELINV_vehicleState": 3})
        ref = P.decode(P.BEL, 0x18FFD9C0, d)
        check("max_avail_ca", M.max_avail_ca(d),
              round(ref["BELINV_maxAvailableChargingCurrent"] * 100), 1)

    # page-01 setpoint round trip
    for _ in range(500):
        counts = rng.randrange(0, 0x10000)
        d = P.enc_setpoint(P.VLIM_DEFAULT_COUNTS, counts)
        check("ilim counts", M.setpoint_ilim_counts(d), counts)
        rewritten = M.enc_setpoint_ilim(d, 123)
        check("rewrite keeps vlim", P.setpoint_counts(rewritten)[0],
              P.VLIM_DEFAULT_COUNTS)
        check("rewrite sets ilim", M.setpoint_ilim_counts(rewritten), 123)

    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS[:20]:
            print("  " + f)
        return 1
    print("test_signals: OK -- hand-rolled extraction matches cantools "
          "over 2000 randomised frames per message")
    return 0


if __name__ == "__main__":
    sys.exit(main())
