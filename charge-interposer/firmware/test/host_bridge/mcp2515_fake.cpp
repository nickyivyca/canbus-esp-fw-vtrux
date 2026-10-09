#include "mcp2515_fake.h"

#include "mcp2515_fake_chip.h"

namespace mcpfake {

// --------------------------------------------------------------------------
// p63, Table 11-1: the shaded cells. Confirmed from the rendered page and,
// independently, from the page's non-white fill rectangles. Text extraction
// cannot see shading at all.
// --------------------------------------------------------------------------
bool isBitModifiable(uint8_t addr) {
  switch (addr) {
    case R_BFPCTRL:
    case R_TXRTSCTRL:
    case R_CNF1:
    case R_CNF2:
    case R_CNF3:
    case R_CANINTE:
    case R_CANINTF:
    case R_EFLG:
    case R_TXB0CTRL:
    case R_TXB0CTRL + 0x10:
    case R_TXB0CTRL + 0x20:
    case R_RXB0CTRL:
    case R_RXB1CTRL:
      return true;
    default:
      // CANSTAT and CANCTRL are mirrored at every xE / xF (p63 Table 11-1).
      // CANCTRL is shaded, CANSTAT is not.
      if ((addr & 0x0F) == 0x0F) return true;   // CANCTRL
      return false;                             // incl. CANSTAT, TEC, REC
  }
}


// --------------------------------------------------------------------------
// Everything this model does, and what each rests on.
//
// A test prints the entries its own run depended on, straight from here, so
// the marking cannot drift from the behaviour. The gen-inhibit session lost
// three rounds to a hand-kept list going stale against the code.
// --------------------------------------------------------------------------
static const Assumption kAssumptions[] = {
    {DATASHEET, "p67 Table 12-1",
     "instruction set: RESET C0, READ 03, WRITE 02, RTS 80|nnn, READ STATUS "
     "A0, BIT MODIFY 05, READ RX 90|0nm0, LOAD TX 40|0abc"},
    {DATASHEET, "p63 Table 11-2",
     "register addresses and every bit position used here"},
    {DATASHEET, "p63 Table 11-1 (shading)",
     "which registers accept BIT MODIFY; on the rest the mask is forced to "
     "FFh and it becomes a byte write"},
    {DATASHEET, "p70 Fig 12-8, oriented by ST_TX0REQ=0x04 and RX0IF=0x01 "
                "across the 88,000-frame Stage 1 run",
     "READ STATUS byte layout, including TX0IF/TX1IF/TX2IF at 0x08/0x20/0x80"},
    {DATASHEET, "p15 s3.2",
     "equal TXP: the higher-numbered buffer transmits first"},
    {DATASHEET, "p16 s3.6 note 1, p17 flowchart",
     "clearing TXREQ aborts only a message that has NOT started; one already "
     "transmitting completes"},
    {DATASHEET, "p16 s3.6 note 2",
     "ABTF is set by ABAT or one-shot, NOT by clearing TXREQ"},
    {DATASHEET, "p15 s3.3, p17 flowchart, p18 Register 3-1",
     "TXREQ is cleared by the chip only on successful transmission, which "
     "also sets TXnIF regardless of TXnIE"},
    {DATASHEET, "p47 s6.7, p48 Fig 6-1",
     "the error-state THRESHOLDS: error-warning at 96, error-passive at "
     "128, bus-off above TEC 255, recovery after 128 x 11 recessive bits"},
    {DATASHEET, "p47 s6.7 + p48 Fig 6-1, text AND rendered page",
     "BUS-OFF, via a FORM/BIT/STUFF error source (setBusErrors) that is "
     "separate from the no-ACK one: error-passive at TEC >= 128, bus-off "
     "when TEC EXCEEDS 255 (so at 256 -- which is why the counter is kept "
     "wider than the 8-bit TEC register), and recovery after 128 "
     "occurrences of 11 consecutive recessive bits, 2816 us at 500 kbit. "
     "This entry read NOT_MODELLED until 2026-10-04 and before that "
     "wrongly read DATASHEET while the code had nothing"},
    {DATASHEET, "p47 s6.7 note box",
     "recovery from bus-off needs NO MCU intervention -- the chip returns "
     "on its own if the bus stays idle for 128 x 11 bit times. A real "
     "difference from the TWAI port, which service() must restart"},
    {DATASHEET, "p48 Fig 6-1, agreeing with p47 s6.7",
     "the edge out of bus-off returns to ERROR-ACTIVE, so both counters "
     "clear rather than TEC merely dropping below the bus-off limit. The "
     "figure's 'TEC > 127' and the prose's 'equals or exceeds 128' are "
     "the SAME threshold -- the counters are integers -- so the two "
     "agree and there is no conflict here to go looking for"},
    {NOT_MODELLED, "nothing here drives REC",
     "the RECEIVE error counter and the error-passive RECEIVE state. "
     "Only transmit errors are modelled, which is enough for bus-off "
     "(p47 s6.6: 'only transmitters can go bus-off') but means no result "
     "may rest on REC or EFLG_RXEP"},
    {DEFERRED, "p47 s6.7 defers increments AND decrements to the CAN "
               "specification",
     "TEC decrements by 1 on a successful transmission. Modelled as the "
     "usual ISO rule"},
    {DEFERRED, "p47 s6.7 defers increments to 'the CAN bus specification'",
     "a transmitter with nothing acknowledging stays error-passive and never "
     "reaches bus-off. MODELLED THAT WAY. If it is wrong, a silent charger "
     "segment cycles bus-off every ~2.8 ms and spec 8.2's bridge_ok is clear "
     "on every status frame of every drive"},
    {UNKNOWN, "p15 s3.3 says 'when TXREQ is set'; p17 flowchart clears them "
              "at the top of every attempt",
     "when ABTF/MLOA/TXERR are cleared. Decides whether TXERR can read 1 on "
     "a frame that eventually went out. MODELLED as the flowchart"},
    {UNKNOWN, "not in the document",
     "reloading a buffer whose aborted frame is still on the wire (p15 says "
     "only to write when TXREQ is clear, and our abort clears it early)"},
    {UNKNOWN, "not established",
     "which overflow flag a BUKT rollover sets"},
    {MEASURED, "firmware/README.md, Stage 1 2026-09-11",
     "egress transposes adjacent frames of DIFFERENT ids, ~1 in 10^4 at "
     "2,250 fps, always by one position; uniform DLC never reproduces it"},
    {UNKNOWN, "spec 2, corrected 2026-10-04",
     "whether frames sharing an identifier keep their order: the archived "
     "run is 10,000 frames toward the charger at an unrecorded rate"},
    {ASSUMED, "no evidence either way",
     "SPI byte time is 8 bits at the configured clock, with no inter-byte "
     "gap. Kept in NANOSECONDS: in microseconds it was integer zero at the "
     "driver's 10 MHz default and the clock did not move during SPI at all"},
    {ASSUMED, "worst case on purpose",
     "bit stuffing adds one bit per four of the stuffable span. Overstating "
     "a frame's time on the wire is the safe direction for reasoning about "
     "how long an abort window stays open"},
    {ASSUMED, "the fake has no shared medium",
     "arbitration against other nodes. Any load-dependent result needs the "
     "bench, whatever this model shows"},
};

const Assumption* assumptions(int* count) {
  *count = (int)(sizeof(kAssumptions) / sizeof(kAssumptions[0]));
  return kAssumptions;
}

}  // namespace mcpfake
