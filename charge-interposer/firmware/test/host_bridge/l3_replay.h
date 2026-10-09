// Spec 9.1 L3: real captured traffic as stimulus for the host bridge.
//
// Loads the pinned stimulus written by
// `notes/artifacts/interposer-firmware/make_l3_stimulus.py` and hands it
// to the L2 bridge at the cadence it was captured at, each burst of
// frames sharing a timestamp delivered whole.
//
// WHAT L3 IS NOT, in the spec's own words: "A replay gives stimulus, not
// bus load: the fakes have no shared medium ... an acceptance criterion
// that depends on bus load needs a bench run."
//
// So the replay must NOT assert "nothing was lost". Whether the charger
// port keeps up with 2,263 fps is exactly a bus-load question, and the
// fakes cannot answer it. What it asserts instead is that the ACCOUNTING
// CLOSES: every frame offered is either handed to the bridge or counted
// as lost, and whatever did cross came through byte-identical and in
// order. Those hold at any load, and a frame vanishing with nothing
// counting it is a real defect at any load.

#pragma once

#include <stdint.h>

#include <string>
#include <vector>

namespace l3 {

struct Row {
  uint64_t t_us;
  bool charger;        // true: charger segment; false: vehicle segment
  uint32_t id;
  bool ext;
  uint8_t len;
  uint8_t data[8];
};

struct Stimulus {
  std::vector<Row> rows;
  std::string pin_expected;   // from the header
  std::string pin_actual;     // computed over the body
  std::string source_line;
  bool loaded = false;
  std::string why;            // why it failed to load, if it did
};

// Read and verify. The pin is a 64-bit FNV-1a over the body, which the
// generator writes alongside a SHA-256.
//
// FNV rather than SHA here because the threat is ACCIDENT -- a parser
// change, a stray edit -- not forgery, and a test should not carry a
// crypto implementation to defend against an adversary it does not have.
// The SHA-256 stays in the file as the record-keeping pin.
Stimulus load(const char* path);

// Push timestamps apart so no two frames on the SAME segment overlap
// on the wire. Returns a copy; the input is untouched.
//
// WHY EVERY REPLAY NEEDS THIS. The capture carries the logger's 1 ms
// timestamp quantisation, so several frames share a timestamp and a
// naive replay hands them all over in the same instant. No CAN
// segment can deliver them that way -- each frame occupies the wire
// for its own duration. Injecting a physically impossible burst fills
// the driver's ring and loads two or three hardware buffers at once,
// which is contention that cannot happen in life.
//
// Measured on 2026-10-05: unserialised, the host model produced 5,555
// completions out of load order over this capture and put two other
// buffers busy at 51% of loads. The BOARD, on the same capture at
// truck rate, gave 5 and 0.65%. Serialising the injection moves the
// model onto silicon's shape. The chip model and the p15 s3.2
// tie-break were never wrong; the harness was.
//
// Each segment is serialised independently -- they are separate wires.
Stimulus serialise(const Stimulus& in, uint32_t bitrate = 500000);

}  // namespace l3
