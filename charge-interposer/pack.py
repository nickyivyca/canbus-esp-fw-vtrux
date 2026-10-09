"""A123 LFP pack model for the charge-interposer bench.

116 cells in series (4 x 26s3p + 1 x 12s3p), ~60 Ah per series element
(3 x AMP20). The model exists to close the loop that the real captures cannot:
what the pack does between the VCU's premature 80 % cutoff and a genuine
top-of-charge. Everything above 80 % SoC in evap mode is territory no log
covers, because the truck has never been allowed to go there.

FITTED, NOT DERIVED. The OCV curve, the internal resistance, the balancing
threshold and the BMS charge-current taper are all fitted to three real
captures (vtrux_charge_M1, vtruxchargeafterexportchargetest-80pctcv,
vtrux_partstruck_charge). They reproduce those sessions' observable behaviour;
they are not a cell datasheet. Anything the interposer decides must therefore be
justified by the BMS *signals*, never by trusting this model's absolute numbers.

State of charge convention
--------------------------
Each cell carries `q`, its charge as a fraction of Q100 (the charge at which the
BMS reports 100 %). q can exceed 1.0: the real BMS clamps its reported SoC at
100 while the cells keep climbing the LFP knee toward the 3.600 V ceiling --
that is exactly the region where the top-of-charge decision happens.
"""

import bisect
import random

N_CELLS = 116
Q100_AH = 60.0          # Ah at BMS-reported 100 %
R_CELL_OHM = 0.0006     # per series element (3 cells in parallel)

# --- fitted charge-direction OCV curve: q (fraction of Q100) -> volts --------
# The knee above 1.00 was re-fitted 2026-09-16 against the two captured
# balance holds (artifacts/interposer-firmware/knee_charge_to_voltage.txt):
# integrating bcm_ibat while the weakest cell climbed at 0.35-0.43 A, the
# truck needed 0.134 Ah for 3.480 -> 3.520 V, 0.092 Ah for 3.520 -> 3.560 V
# and 0.040 Ah for 3.560 -> 3.580 V (parts truck; the 2026-05-03 hold agrees
# within 30 %). The previous table needed 2-5x that charge per band, which
# made every modelled hold 2-4x longer than the truck's. Below 3.48 V the
# hold data is contaminated by the charger still tapering and is not used.
_OCV_X = [0.00, 0.10, 0.20, 0.30, 0.40, 0.50, 0.59, 0.73, 0.80, 0.87,
          0.92, 0.95, 0.96, 0.98, 0.99, 0.995, 1.00,
          1.0024, 1.0046, 1.0061, 1.0068, 1.0075, 1.012, 1.030]
_OCV_V = [3.090, 3.190, 3.250, 3.280, 3.292, 3.307, 3.327, 3.346, 3.358, 3.394,
          3.407, 3.418, 3.424, 3.435, 3.438, 3.454, 3.461,
          3.480, 3.520, 3.560, 3.580, 3.598, 3.700, 3.900]

# --- fitted BMS charge-current permission: max cell volts -> amps ------------
# This is the signal the interposer actually keys off (bcm_chg_max, 0x420).
# Fitted to the observed (vmax, bcm_chg_max) pairs:
#   vtrux_charge_M1         (3.422, 88.5) (3.439, 12) (3.508, 5.75)
#                           (3.594, 2.75) (3.598, 0)
#   vtrux_partstruck_charge (3.400, 271) (3.410, 247) (3.426, 140) (3.435, 86)
#                           (3.443, 58) (3.446, 7) (3.469, 1.0) (3.590, 0)
# The two sessions genuinely disagree through 3.44-3.47 -- different packs and
# temperatures -- so no single curve matches both. This follows charge_M1 in the
# tail because that is the balanced/typical case, and because a tail that
# collapses too early ends the bench's charge at vmax ~3.47 instead of the
# ~3.59 the truck actually reaches.
_CHGMAX_V = [3.000, 3.350, 3.390, 3.410, 3.420, 3.430, 3.440, 3.450,
             3.470, 3.510, 3.560, 3.594, 3.600, 3.700]
_CHGMAX_A = [300.0, 300.0, 280.0, 240.0, 130.0, 90.0, 40.0, 20.0,
             12.0, 6.0, 4.0, 2.75, 0.0, 0.0]

# Balancing. The real BMS rule is NOT established -- the project's own notes
# leave it open (balancing-investigation.md "Open Questions": charge_9pm showed
# 25 mV of spread at 99 % SoC and triggered no balancing at all). Two facts have
# to be reproduced simultaneously and no single simple rule does both:
#
#   * `bcm_balancing_cnt` reaches 116 (every cell) both as a brief transient at
#     vmax ~3400-3420 mV and as a sustained phase at ~3595-3600 mV -- which
#     implies an ABSOLUTE voltage threshold around 3.40 V.
#   * In vtrux_partstruck_charge the spread collapsed 152 mV -> 2 mV in ~1.8 h
#     with vmin climbing 3445 -> 3597 mV while vmax held at 3598 -- which a
#     uniform bleed under a uniform series current cannot produce, and which
#     needs a RELATIVE (bleed-the-leaders) rule.
#
# So the model does both, deliberately and visibly: it BLEEDS on the relative
# rule (so convergence matches the captures) and REPORTS `balancing_cnt` on the
# absolute rule (so the count matches the captures). This is a fitted stand-in
# chosen to make the bench behave like the logs, not a claim about what the
# A123 BMS does. `--balance-model absolute` selects a pure absolute rule if you
# want to see the difference.
#
# None of the interposer's decisions depend on this: it keys off bcm_chg_max
# and bcm_cell_vmax, both of which the model reproduces from the fitted curves
# above.
BALANCE_THRESHOLD_V = 3.395   # observed onset: vmax 3390-3406 mV across 5 sessions
BALANCE_DELTA_V = 0.003       # bleed a cell this far above vmin (settled spread 2-3 mV)
BLEED_A = 0.30                # bleed-resistor current per balancing cell


def _interp(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x) - 1
    f = (x - xs[i]) / (xs[i + 1] - xs[i])
    return ys[i] + f * (ys[i + 1] - ys[i])


def ocv(q):
    return _interp(_OCV_X, _OCV_V, q)


def bms_chg_max(vmax):
    """The BMS charge-current permission the real pack broadcasts on 0x420."""
    return _interp(_CHGMAX_V, _CHGMAX_A, vmax)


class Pack:
    """116-cell LFP pack with per-cell charge, IR drop and passive balancing."""

    def __init__(self, soc_pct=50.0, imbalance_pct=0.4, *, seed=1,
                 outlier_cell=None, outlier_pct=0.0,
                 balance_threshold_v=BALANCE_THRESHOLD_V,
                 balance_delta_v=BALANCE_DELTA_V,
                 balance_model="hybrid", bleed_a=BLEED_A,
                 q100_ah=Q100_AH, r_cell=R_CELL_OHM):
        """Imbalance is specified as CHARGE, not voltage, and for good reason.

        On the LFP plateau a 3 % charge spread shows up as ~4 mV; the same
        spread at the top of the knee shows up as ~150 mV. Specifying volts
        would mean a different (and at low SoC, absurd) charge imbalance
        depending on where the session happens to start. Specifying charge
        reproduces the real observation directly: vtrux_partstruck_charge began
        at 59 % SoC with a 4-5 mV spread and opened to 152 mV at the top -- one
        constant charge spread of roughly 3 %, seen through two very different
        parts of the curve.

        `imbalance_pct` is the +/-2-sigma cell-to-cell charge spread, in percent
        of Q100. `outlier_cell` / `outlier_pct` inject a single persistently-low
        cell, which is what the parts truck's pack actually has: cnt=115 was its
        dominant balancing count (26 % of the session) -- one cell below the
        threshold while the other 115 were above it.
        """
        rng = random.Random(seed)
        q0 = soc_pct / 100.0
        sigma_q = (imbalance_pct / 100.0) / 4.0
        self.q = [q0 + rng.gauss(0.0, sigma_q) for _ in range(N_CELLS)]
        if outlier_cell is not None and outlier_pct:
            self.q[outlier_cell % N_CELLS] -= outlier_pct / 100.0
        self.q100_ah = q100_ah
        self.r_cell = r_cell
        self.balance_threshold_v = balance_threshold_v
        self.balance_delta_v = balance_delta_v
        self.balance_model = balance_model
        self.bleed_a = bleed_a
        self.current_a = 0.0        # + = charging into the pack
        self.temp_c = 22.0
        self._balancing = [False] * N_CELLS

    # ---- observables -----------------------------------------------------
    def cell_voltages(self):
        ir = self.current_a * self.r_cell
        return [ocv(q) + ir for q in self.q]

    @property
    def vmax(self):
        return max(self.cell_voltages())

    @property
    def vmin(self):
        return min(self.cell_voltages())

    @property
    def pack_v(self):
        return sum(self.cell_voltages())

    @property
    def soc_pct(self):
        """What the BMS reports: mean cell charge, clamped at 100."""
        return min(100.0, 100.0 * sum(self.q) / len(self.q))

    @property
    def soc_true_pct(self):
        """Unclamped, for bench instrumentation only -- not on the wire."""
        return 100.0 * sum(self.q) / len(self.q)

    @property
    def balancing_cnt(self):
        return sum(1 for b in self._balancing if b)

    @property
    def spread_mv(self):
        v = self.cell_voltages()
        return (max(v) - min(v)) * 1000.0

    # ---- integration -----------------------------------------------------
    def step(self, dt_s, current_a):
        """Advance by dt_s with `current_a` flowing in (+ = charge)."""
        self.current_a = current_a
        per_cell_ah = current_a * dt_s / 3600.0 / self.q100_ah

        # Passive balancing. Bleed responds to the spread, it does not cause it.
        # See the module docstring above for why bleeding and reporting use
        # different rules under the default "hybrid" model.
        volts = self.cell_voltages()
        vmin = min(volts)
        bleed_ah = self.bleed_a * dt_s / 3600.0 / self.q100_ah
        for i in range(N_CELLS):
            above_abs = volts[i] > self.balance_threshold_v
            above_rel = volts[i] > vmin + self.balance_delta_v
            if self.balance_model == "absolute":
                bleeding = above_abs
                reported = above_abs
            else:                       # "hybrid" (default)
                bleeding = above_abs and above_rel
                reported = above_abs
            self._balancing[i] = reported
            self.q[i] += per_cell_ah - (bleed_ah if bleeding else 0.0)
