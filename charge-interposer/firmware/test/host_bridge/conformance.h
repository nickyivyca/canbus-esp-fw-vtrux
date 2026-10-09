// The MCP2515 conformance sequence: one script, two subjects.
//
// The point of this file is that the SAME sequence runs against the fake
// (now, on any machine) and against the real chip (on the bench), and the
// two transcripts are diffed. A difference is either a defect in the model
// or a fact about the silicon, and the status attached to each observation
// says which way to read it: a DATASHEET line that differs means the model
// or the reading is wrong, an UNKNOWN line that differs is the bench
// answering a question the document would not.
//
// WHY THE SEQUENCE DOES NOT NAME BUFFER NUMBERS ON THE WIRE.
// The fake can say which buffer sent a frame. A dongle on the bench cannot
// -- a CAN frame carries no trace of which of TXB0-2 it came out of. If the
// sequence asserted on buffer numbers it would be unrunnable against the
// real chip, which is the only subject that can settle the ordering
// questions it exists for. So every frame this sequence transmits carries a
// BUFFER TAG in data[0], and the observable is the order of tags on the
// wire. Both subjects can report that.
//
// The transcript is deliberately dull: one `step/key = value` per line, in
// a fixed order, no timings unless the step is about timing. Diffing is the
// whole mechanism, so anything that varies run to run without meaning
// (wall-clock stamps, pointer values, iteration counts) must stay out.

#pragma once

#include <stdint.h>

#include <string>
#include <vector>

#include "mcp2515_fake.h"

namespace mcpconf {

// What a subject must be able to do. The fake implements this over its
// register file; the bench implementation drives a real MCP2515 over SPI
// and gets its wire order from a dongle capture.
struct Target {
  virtual ~Target() {}

  // --- SPI, the only channel to the chip -----------------------------
  virtual void csLow() = 0;
  virtual uint8_t transfer(uint8_t out) = 0;
  virtual void csHigh() = 0;

  // --- time ----------------------------------------------------------
  // The fake steps exactly; the bench busy-waits. Either way the contract
  // is "at least this long, and transmissions may complete during it".
  virtual void advanceUs(uint64_t us) = 0;
  virtual uint64_t nowUs() = 0;

  // --- the wire ------------------------------------------------------
  // The buffer tags (data[0]) of every frame seen on the wire, oldest
  // first, since the last clearWire(). On the fake this reads the model's
  // wire log; on the bench it reads the dongle capture.
  virtual std::vector<uint8_t> wireTags() = 0;
  virtual void clearWire() = 0;

  // Whether this subject can be told to withhold acknowledgement. The
  // fake has a flag; on the bench it means "the charger segment has no
  // other node on it", which is a wiring state, so the bench target
  // answers true only when the operator has confirmed it.
  virtual bool canWithholdAck() = 0;
  virtual void setAcknowledged(bool yes) = 0;

  // --- frames TO the chip (added 2026-10-08, C12) ---------------------
  // Put one frame on the segment for the chip to receive, carrying `tag`
  // in data[0], and return once it has been received. The fake delivers
  // it into the receive path at once; the bench sends it from a dongle
  // and waits at least one frame time. Every C12 action is sequenced by
  // the script, never raced, so the bench needs no timing precision for
  // it. canInject() is false where no sender is attached, and C12 then
  // records that it did not run rather than recording nothing.
  virtual bool canInject() = 0;
  virtual void injectFrame(uint32_t id, bool ext, uint8_t tag,
                           uint8_t dlc) = 0;

  virtual const char* subjectName() = 0;
};

// One line of the transcript.
struct Observation {
  std::string step;
  std::string key;
  std::string value;
  mcpfake::Status status;
  std::string cite;     // page or artifact the EXPECTATION rests on
};

class Recorder {
 public:
  void note(const char* step, const char* key, const std::string& value,
            mcpfake::Status status, const char* cite) {
    Observation o;
    o.step = step;
    o.key = key;
    o.value = value;
    o.status = status;
    o.cite = cite;
    obs_.push_back(o);
  }
  void hex8(const char* step, const char* key, uint8_t v,
            mcpfake::Status status, const char* cite);
  void dec(const char* step, const char* key, uint32_t v,
           mcpfake::Status status, const char* cite);

  const std::vector<Observation>& all() const { return obs_; }

  // The diffable artifact. One line per observation, fixed field order.
  void print(const char* subject) const;

 private:
  std::vector<Observation> obs_;
};

// Run the whole sequence. Pure: it only touches the target and the
// recorder, so a caller can run it against anything implementing Target.
void run(Target& t, Recorder& rec);

// --- helpers shared with the tests, exposed so a test can drive one step
// --- without replaying the entire sequence.
uint8_t readReg(Target& t, uint8_t addr);
void writeReg(Target& t, uint8_t addr, uint8_t value);
void bitModify(Target& t, uint8_t addr, uint8_t mask, uint8_t value);
uint8_t readStatus(Target& t);
void reset(Target& t);

// Load a frame into TXBn carrying `tag` in data[0], WITHOUT requesting
// transmission. `dlc` lets a step vary frame length, which is the
// confound the same-identifier ordering question turns on.
void loadBuffer(Target& t, int n, uint32_t id, uint8_t tag, uint8_t dlc);

// Request transmission of the buffers in `mask` (bit 0 = TXB0) with ONE
// RTS instruction, or with one instruction per buffer.
void rtsTogether(Target& t, uint8_t mask);
void rtsSeparately(Target& t, uint8_t mask);

}  // namespace mcpconf
