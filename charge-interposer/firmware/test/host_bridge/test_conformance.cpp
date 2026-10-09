// The conformance sequence against the fake, plus the assertions that say
// what the transcript MUST contain for the model to be the chip the
// datasheet describes.
//
// Two outputs, and they do different jobs:
//
//   - the TRANSCRIPT is the artifact. It is what gets diffed against the
//     real chip on the bench, and it carries lines this file does not
//     assert on at all -- the UNKNOWN and DEFERRED ones, which exist
//     precisely because nothing here can say what they should be.
//
//   - the ASSERTIONS say what THE FAKE must do. Most of them are
//     datasheet-grade, and the rule they follow is: never assert a
//     PREDICTION ABOUT SILICON. Freezing a guess about the real chip into
//     a test means the bench can no longer contradict it without
//     "breaking" something.
//
// C6 is the one place those two things come apart, so it is worth stating.
// Its order is asserted here because on the fake it is determinate and the
// assertion guards the model's RTS handling -- the defect that once made a
// multi-buffer RTS behave like three and hid the p15 s3.2 tie-break
// entirely. But its TRANSCRIPT line is UNKNOWN, because on silicon the
// order depends on how long three RTS instructions take against the frame
// already on the wire. The bench measures a RATE there, not an order, and
// a different order on the real chip is not a defect.
//
// Per the folder's standing rule, every one of these drives the chip model
// DIRECTLY over SPI -- no Mcp2515Port anywhere in this file. The RTS defect
// that hid the p15 s3.2 tie-break was invisible through the driver because
// the driver never issues a multi-buffer RTS.

#include <cstdio>
#include <cstring>

#include "conformance.h"
#include "mcp2515_fake_chip.h"

using namespace mcpfake;

static int failures = 0;

static void check(bool cond, const char* what) {
  std::printf(cond ? "  ok  %s\n" : "FAIL  %s\n", what);
  if (!cond) failures++;
}

namespace {

// The fake wearing the Target interface. Deliberately thin: everything it
// does is either an SPI byte or a clock step, so the bench implementation
// of the same interface differs only in how those two things happen.
struct FakeTarget : mcpconf::Target {
  Mcp2515Fake chip;
  size_t wire_mark = 0;

  void csLow() override { chip.csLow(); }
  uint8_t transfer(uint8_t out) override { return chip.transfer(out); }
  void csHigh() override { chip.csHigh(); }

  void advanceUs(uint64_t us) override { chip.advance(us); }
  uint64_t nowUs() override { return chip.micros(); }

  // DELIVERED frames only. A dongle on the bench sees frames that reached
  // the wire and were acknowledged; it cannot see a buffer that was
  // aborted before it started. Reporting anything more here would make the
  // fake's transcript unmatchable by the only other subject that exists.
  std::vector<uint8_t> wireTags() override {
    std::vector<uint8_t> out;
    const std::vector<WireEvent>& w = chip.wire();
    for (size_t i = wire_mark; i < w.size(); i++)
      if (w[i].delivered) out.push_back(w[i].frame.data[0]);
    return out;
  }
  void clearWire() override { wire_mark = chip.wire().size(); }

  bool canWithholdAck() override { return true; }
  void setAcknowledged(bool yes) override { chip.setAcknowledged(yes); }

  const char* subjectName() override { return "fake (mcp2515_fake_chip)"; }
};

// Pull one value out of the transcript. Returns empty if absent, and the
// caller treats that as a failure -- a step that silently stopped
// producing a line would otherwise pass every assertion about it.
std::string valueOf(const mcpconf::Recorder& rec, const char* step,
                    const char* key) {
  const std::vector<mcpconf::Observation>& all = rec.all();
  for (size_t i = 0; i < all.size(); i++)
    if (all[i].step == step && all[i].key == key) return all[i].value;
  return "";
}

void expect(const mcpconf::Recorder& rec, const char* step, const char* key,
            const char* want) {
  const std::string got = valueOf(rec, step, key);
  char msg[256];
  if (got.empty()) {
    std::snprintf(msg, sizeof(msg), "%s/%s MISSING from the transcript",
                  step, key);
    check(false, msg);
    return;
  }
  std::snprintf(msg, sizeof(msg), "%s/%s = %s (want %s)", step, key,
                got.c_str(), want);
  check(got == want, msg);
}

}  // namespace

int main() {
  FakeTarget t;
  mcpconf::Recorder rec;
  mcpconf::run(t, rec);

  rec.print(t.subjectName());

  std::printf("\n--- assertions (DATASHEET-grade lines only) ---\n");

  // C1. RESET enters Configuration mode and clears the transmit state.
  expect(rec, "C1-reset-defaults", "CANSTAT.mode", "0x80");
  expect(rec, "C1-reset-defaults", "TXB0CTRL", "0x00");
  expect(rec, "C1-reset-defaults", "TXB1CTRL", "0x00");
  expect(rec, "C1-reset-defaults", "TXB2CTRL", "0x00");
  expect(rec, "C1-reset-defaults", "CANINTF", "0x00");
  expect(rec, "C1-reset-defaults", "TEC", "0x00");

  // C2. BIT MODIFY, and the silent widening that matters to us.
  expect(rec, "C2-bit-modify", "CANINTF.after-set-TX0IF", "0x04");
  expect(rec, "C2-bit-modify", "CANINTF.after-clear-one", "0xFB");
  // 0x22 written under a 0x01 mask: if the mask were honoured this would
  // be 0x00 (bit 0 of 0x22 is 0). Forced to FFh it is the whole byte.
  expect(rec, "C2-bit-modify", "TXB0SIDH.after-masked-write", "0x22");
  expect(rec, "C2-bit-modify", "TEC.after-direct-write", "0x00");

  // C3. The READ STATUS bit map -- the orientation anchor.
  expect(rec, "C3-read-status-map", "status.idle", "0x00");
  expect(rec, "C3-read-status-map", "status.TXB0-requested", "0x04");
  expect(rec, "C3-read-status-map", "status.after-complete", "0x08");
  expect(rec, "C3-read-status-map", "CANINTF.after-complete", "0x04");

  // C4. Only the success half is DATASHEET; the after-RTS line is UNKNOWN
  // and is deliberately NOT asserted.
  expect(rec, "C4-txflags-on-request", "TXB0CTRL.after-complete", "0x00");

  // C5/C6. The two orderings must DIFFER. That is the finding.
  // C5 is the datasheet tie-break. C6 is the fake's determinate result for
  // the driver's pattern -- asserted to guard the model, NOT as a
  // prediction about silicon (see the header).
  expect(rec, "C5-rts-one-instruction", "wire.tag-order", "2,1,0");
  expect(rec, "C6-rts-per-buffer", "wire.tag-order", "0,2,1");
  check(valueOf(rec, "C5-rts-one-instruction", "wire.tag-order") !=
            valueOf(rec, "C6-rts-per-buffer", "wire.tag-order"),
        "C5 and C6 differ -- one RTS and three RTS are not the same thing");

  // C7. TXP beats buffer number -- and the expected order here is the
  // exact reverse of C5's, so this cannot pass by accident if C5 does.
  expect(rec, "C7-txp-priority", "wire.tag-order", "0,1,2");

  // C9. The DEFERRED item. What is asserted is only that the model does
  // what the table SAYS it does -- saturate at the error-passive limit and
  // never reach bus-off -- not that the silicon agrees. The bench answers
  // that, and if it disagrees these lines are where it shows.
  expect(rec, "C9-no-ack", "TEC.at-1000ms", "128");
  expect(rec, "C9-no-ack", "EFLG.TXBO-set", "0x00");
  expect(rec, "C9-no-ack", "wire.delivered", "(none)");

  // C10. The waiting buffer was dropped; only the one already on the wire
  // got out.
  expect(rec, "C10-abort", "wire.after-abort-of-waiting", "1");

  // --- the transcript must stay diffable --------------------------------
  //
  // A step that silently stopped emitting its lines would quietly shrink
  // the artifact the bench comparison rests on, and every assertion above
  // would still pass for the steps that remained.
  const char* steps[] = {
      "C1-reset-defaults", "C2-bit-modify", "C3-read-status-map",
      "C4-txflags-on-request", "C5-rts-one-instruction",
      "C6-rts-per-buffer", "C7-txp-priority", "C8-same-id-varying-dlc",
      "C9-no-ack", "C10-abort", "C11-rx-overrun"};
  for (size_t i = 0; i < sizeof(steps) / sizeof(steps[0]); i++) {
    size_t n = 0;
    const std::vector<mcpconf::Observation>& all = rec.all();
    for (size_t j = 0; j < all.size(); j++)
      if (all[j].step == steps[i]) n++;
    char msg[160];
    std::snprintf(msg, sizeof(msg), "%s contributed %u transcript lines",
                  steps[i], (unsigned)n);
    check(n > 0, msg);
  }

  // Every open question must still be carried as open. If someone
  // "resolves" one by asserting it above, this catches the status not
  // having moved with it.
  {
    const std::vector<mcpconf::Observation>& all = rec.all();
    size_t open = 0;
    for (size_t j = 0; j < all.size(); j++)
      if (all[j].status == UNKNOWN || all[j].status == DEFERRED) open++;
    char msg[160];
    std::snprintf(msg, sizeof(msg),
                  "%u transcript lines are still UNKNOWN or DEFERRED -- "
                  "these are what the bench is for", (unsigned)open);
    check(open > 0, msg);
  }

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_conformance: all checks OK\n");
  return 0;
}
