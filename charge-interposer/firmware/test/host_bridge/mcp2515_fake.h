// A model of the MCP2515, driven through the SPI byte stream (spec 9.1 L2).
//
// Written from Microchip DS20001801J,
// `MCP2515-Stand-Alone-CAN-Controller-with-SPI-20001801J.pdf` in
// reverse-it's projects/vtrux/tools/interposer/firmware/datasheets/
// (not moved to this repo),
// NOT from `port_mcp2515.cpp`. A fake that borrows the driver's opcode map
// agrees with the driver's mistakes, and then the conformance run -- written
// from the same borrowed map -- agrees with both. The constants below were
// derived from the document first and diffed against the driver afterwards;
// that diff found 0 mismatches in 31, which is a result about the driver and
// not a reason to have copied it.
//
// The boundary is the SPI byte stream, not the Mcp2515Port object. Faking
// the port would test nothing about the driver, and the driver is where the
// 100 ms age-out, the no-ACK counting and the transmit-buffer ordering live.
//
// EVERY CITATION IS "p<n>" = the PDF page number printed by
// `$VTRUX_DATA/notes/artifacts/interposer-firmware/mcp2515_datasheet_extract.py`
// (SeaDrive),
// which is 1-based over the file and runs one ahead of the page number
// printed in the document's own footer for most of the book. Each one below
// was confirmed BOTH by text extraction AND by looking at the rendered page
// (user's rule, 2026-10-04), because neither alone is reliable: the text
// dump cannot see table shading at all, and it lists figure labels in an
// order that is NOT their bit order.

#pragma once

#include <stdint.h>
#include <string.h>

#include "provenance.h"

namespace mcpfake {

// --------------------------------------------------------------------------
// Status of every behaviour this model implements.
//
// The run-time marking is generated from this table (see `assumptions()`),
// so what a test prints about its own foundations cannot drift from what the
// model actually does. Six statuses, in descending order of how much weight
// a result resting on them can bear.
// --------------------------------------------------------------------------
// The status scale now lives in `provenance.h`, shared with the TWAI
// fake. These using-declarations keep every existing `mcpfake::DATASHEET`
// spelling working unchanged, so moving it cost no call sites.
using prov::Status;
using prov::Assumption;
using prov::MEASURED;
using prov::DATASHEET;
using prov::DEFERRED;
using prov::RECALLED;
using prov::UNKNOWN;
using prov::ASSUMED;
using prov::NOT_MODELLED;

// --------------------------------------------------------------------------
// Instruction set -- p67, Table 12-1 (text + render).
// --------------------------------------------------------------------------
static const uint8_t OP_RESET = 0xC0;        // 1100 0000
static const uint8_t OP_READ = 0x03;         // 0000 0011
static const uint8_t OP_WRITE = 0x02;        // 0000 0010
static const uint8_t OP_RTS = 0x80;          // 1000 0nnn, nnn = buffer mask
static const uint8_t OP_READ_STATUS = 0xA0;  // 1010 0000
static const uint8_t OP_RX_STATUS = 0xB0;    // 1011 0000
static const uint8_t OP_BIT_MODIFY = 0x05;   // 0000 0101
static const uint8_t OP_READ_RX = 0x90;      // 1001 0nm0
static const uint8_t OP_LOAD_TX = 0x40;      // 0100 0abc

// --------------------------------------------------------------------------
// Registers -- p63, Table 11-2 (text + render).
// --------------------------------------------------------------------------
static const uint8_t R_BFPCTRL = 0x0C;
static const uint8_t R_TXRTSCTRL = 0x0D;
static const uint8_t R_CANSTAT = 0x0E;   // mirrored at every xE
static const uint8_t R_CANCTRL = 0x0F;   // mirrored at every xF
static const uint8_t R_TEC = 0x1C;
static const uint8_t R_REC = 0x1D;
static const uint8_t R_CNF3 = 0x28;
static const uint8_t R_CNF2 = 0x29;
static const uint8_t R_CNF1 = 0x2A;
static const uint8_t R_CANINTE = 0x2B;
static const uint8_t R_CANINTF = 0x2C;
static const uint8_t R_EFLG = 0x2D;
static const uint8_t R_TXB0CTRL = 0x30;  // +0x10 per buffer
static const uint8_t R_RXB0CTRL = 0x60;
static const uint8_t R_RXB1CTRL = 0x70;

// TXBnCTRL bits -- p63, Table 11-2.
static const uint8_t TXB_ABTF = 0x40;
static const uint8_t TXB_MLOA = 0x20;
static const uint8_t TXB_TXERR = 0x10;
static const uint8_t TXB_TXREQ = 0x08;
static const uint8_t TXB_TXP_MASK = 0x03;

// CANINTF bits -- p63, Table 11-2.
static const uint8_t INTF_RX0IF = 0x01;
static const uint8_t INTF_RX1IF = 0x02;
static const uint8_t INTF_TX0IF = 0x04;
static const uint8_t INTF_TX1IF = 0x08;
static const uint8_t INTF_TX2IF = 0x10;
static const uint8_t INTF_ERRIF = 0x20;
static const uint8_t INTF_WAKIF = 0x40;
static const uint8_t INTF_MERRF = 0x80;

// EFLG bits -- p63, Table 11-2.
static const uint8_t EFLG_EWARN = 0x01;
static const uint8_t EFLG_RXWAR = 0x02;
static const uint8_t EFLG_TXWAR = 0x04;
static const uint8_t EFLG_RXEP = 0x08;
static const uint8_t EFLG_TXEP = 0x10;
static const uint8_t EFLG_TXBO = 0x20;
static const uint8_t EFLG_RX0OVR = 0x40;
static const uint8_t EFLG_RX1OVR = 0x80;

// CANCTRL / CANSTAT mode field -- p63, Table 11-2 (REQOP at bits 7-5).
static const uint8_t MODE_MASK = 0xE0;
static const uint8_t MODE_NORMAL = 0x00;
static const uint8_t MODE_SLEEP = 0x20;
static const uint8_t MODE_LOOPBACK = 0x40;
static const uint8_t MODE_LISTEN = 0x60;
static const uint8_t MODE_CONFIG = 0x80;
static const uint8_t CANCTRL_ABAT = 0x10;

// --------------------------------------------------------------------------
// READ STATUS byte -- p70, Figure 12-8.
//
// The bit ORDER here does not rest on the figure, and that distinction is
// the point. Four readings of Figure 12-8 were taken (two sessions, each by
// rendered page and by word coordinates) and all four read the same layout,
// so a shared misreading of its orientation would have survived every one of
// them -- one of those readings did invert it and had to be corrected.
//
// What fixes the orientation is silicon: the driver polls TXB0's TXREQ with
// 0x04 and reads RXB0's flag with 0x01, and both ran through the 88,000-frame
// Stage 1 transparency pass of 2026-09-11 where the board's own counters
// reconciled exactly against an external count
// (`../../README.md`, "Stage 1"). TXREQ(TXB0) is third from the top of the
// figure; at 0x04 it is bit 2, which is only consistent with top-to-bottom
// being bit 0 upward. RX0IF pins the same direction from the other end.
static const uint8_t ST_RX0IF = 0x01;    // bit 0
static const uint8_t ST_RX1IF = 0x02;    // bit 1
static const uint8_t ST_TX0REQ = 0x04;   // bit 2  <- anchor
static const uint8_t ST_TX0IF = 0x08;    // bit 3
static const uint8_t ST_TX1REQ = 0x10;   // bit 4
static const uint8_t ST_TX1IF = 0x20;    // bit 5
static const uint8_t ST_TX2REQ = 0x40;   // bit 6
static const uint8_t ST_TX2IF = 0x80;    // bit 7

// Registers the BIT MODIFY instruction may address -- p63, Table 11-1,
// where they are SHADED. Shading is invisible to text extraction: the dump
// carries the note explaining what it means and not one bit of which cells
// have it. Confirmed from the rendered page and, independently, from the
// page's non-white fill rectangles intersected with word positions.
//
// On any other register the chip forces the mask to FFh and the instruction
// becomes a plain byte write (p66 section 12.10 note; p67 Table 12-1 note) --
// silently. That is modelled, because a driver that bit-modifies TEC or
// CANSTAT would wipe it and nothing would complain.
bool isBitModifiable(uint8_t addr);

struct CanFrame {
  uint32_t id;
  uint8_t data[8];
  uint8_t len;
  bool ext;
  bool rtr;
};

// What the model did with a frame, so a test can assert on the WIRE and not
// only on the driver's counters. The gen-inhibit session's lesson: a count
// can balance with a term skipped on both sides.
struct WireEvent {
  uint64_t t_us;
  uint8_t buffer;        // 0-2
  CanFrame frame;
  bool delivered;        // reached the wire and was acknowledged
  bool aborted_in_time;  // TXREQ cleared BEFORE transmission started
};

const Assumption* assumptions(int* count);
using prov::statusName;

}  // namespace mcpfake
