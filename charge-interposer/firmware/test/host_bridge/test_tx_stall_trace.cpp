// WHAT EACH HARDWARE BUFFER IS DOING DURING THE L3 CHARGER-PORT STALL.
//
// WHY THIS EXISTS. `test_tx_throughput` establishes the shortfall and
// localises it: driving `Mcp2515Port` directly with the capture, with no
// `main.cpp`, no core and no bridge loop, delivers 540 of 22,314 frames.
// It also rules out everything above and below the driver -- the chip
// starts every frame it is given, the harness's worst pass offers 12
// frames into a 64-deep ring, synthetic bursts of 16 at the same mean
// rate pass at 100%, and the accounting closes to within 40 of a
// structural bound of 67.
//
// What it CANNOT say is why. `aged`, `dropped` and `failed` read exactly
// the same in two situations with opposite fixes:
//
//   (a) the buffers are churning flat out and the offered rate genuinely
//       exceeds what one 500 kbit wire can carry -- in which case the
//       ring policy is the thing to change, and the port is behaving;
//   (b) the buffers are sitting unavailable while the ring overflows
//       behind them -- in which case the port has a stall and no ring
//       policy will help.
//
// Totals over a 10 s window average the difference away, which is how
// four earlier experiments came back inconclusive. This takes the
// per-pass record instead: for every `drainTx()` call, what state each of
// the three buffers was in, how deep the ring was, and how many frames
// the refill loop managed to load. Then it finds the longest stretch
// where the refill loop loaded NOTHING while frames were waiting, and
// prints it pass by pass.
//
// IT IS A MODEL, NOT THE BENCH. Spec 9.1: "a replay gives stimulus, not
// bus load... an acceptance criterion that depends on bus load needs a
// bench run." Nothing here is an acceptance criterion. The two checks at
// the bottom are structural -- they hold at any load and would hold on
// silicon -- and everything else is printed for reading, not asserted.
// The hardware counterpart is the instrumented-board replay in the bench
// plan.

#include <cstdio>
#include <cstring>

#include "l3_replay.h"
#include "mcp2515_fake_chip.h"
#include "port_mcp2515.h"

using namespace interposer;
using mcpfake::Mcp2515Fake;

static int failures = 0;

static void check(bool cond, const char* what) {
  std::printf(cond ? "  ok  %s\n" : "FAIL  %s\n", what);
  if (!cond) failures++;
}

namespace {

const uint8_t CS_PIN = 10;
const uint8_t INT_PIN = 3;

struct Rig {
  Mcp2515Fake chip;
  SPIClass spi;
  Mcp2515Port mcp;

  Rig() : spi(0), mcp(spi, CS_PIN, INT_PIN) {
    hostclock::attach(&chip, CS_PIN);
    hostclock::attachInt(INT_PIN);
    spi.attach(&chip);
    mcp.begin(500000);
  }
  ~Rig() { hostclock::detach(); }
};

// Every state character drainTx() can record, in the order they are
// worth reading. Listed explicitly rather than derived from the trace,
// so a state that NEVER occurs still prints as 0 -- an absent row and a
// zero row mean very different things here, and an absent one is the
// easier to misread as "not a factor".
const char kStates[] = "BXWLFCU.";

void summarise(const Mcp2515Port::TxPass* tr, uint32_t n) {
  std::printf("\n  PER-BUFFER STATE, over all %u stored passes:\n", n);
  std::printf("      state                                "
              "buf0      buf1      buf2\n");
  for (const char* s = kStates; *s; s++) {
    uint32_t cnt[3] = {0, 0, 0};
    for (uint32_t i = 0; i < n; i++)
      for (int b = 0; b < 3; b++)
        if (tr[i].state[b] == *s) cnt[b]++;
    const char* label = "";
    switch (*s) {
      case 'B': label = "busy, within age budget"; break;
      case 'X': label = "over age: TXREQ cleared this pass"; break;
      case 'W': label = "abort placed, inside 400 us wait"; break;
      case 'L': label = "abort resolved: went out late"; break;
      case 'F': label = "abort resolved: never reached wire"; break;
      case 'C': label = "completed normally"; break;
      case 'U': label = "UNEXPLAINED (datasheet violated)"; break;
      case '.': label = "idle and free to load"; break;
    }
    std::printf("      %c  %-32s %7u %9u %9u\n", *s, label,
                cnt[0], cnt[1], cnt[2]);
  }
}

// The longest run of consecutive passes that loaded nothing while frames
// were waiting. That is the stall, by definition: the ring is non-empty
// and the refill loop is achieving nothing.
//
// A run is scored by DURATION, not pass count. Passes are not evenly
// spaced -- the harness advances the clock in 20 us steps but services
// only every 200 us -- so counting passes would rank a dense stretch
// above a longer-lasting one.
struct Run {
  uint32_t begin, end;     // indices, [begin, end)
  uint32_t us;             // wall duration
};

Run longestStall(const Mcp2515Port::TxPass* tr, uint32_t n) {
  Run best = {0, 0, 0};
  uint32_t i = 0;
  while (i < n) {
    if (tr[i].loaded != 0 || tr[i].ring == 0) { i++; continue; }
    uint32_t j = i;
    while (j < n && tr[j].loaded == 0 && tr[j].ring != 0) j++;
    const uint32_t dur = tr[j - 1].us - tr[i].us;
    if (dur > best.us) { best.begin = i; best.end = j; best.us = dur; }
    i = j;
  }
  return best;
}

void dump(const Mcp2515Port::TxPass* tr, uint32_t n, uint32_t from,
          uint32_t to, uint32_t t0) {
  if (to > n) to = n;
  std::printf("      %10s %7s %5s %4s %4s  %s\n",
              "t_us", "age_ms", "ring", "load", "aged", "buf0/1/2");
  for (uint32_t i = from; i < to; i++) {
    const Mcp2515Port::TxPass& p = tr[i];
    std::printf("      %10u %7u %5u %4u %4u   %c %c %c\n",
                p.us - t0, p.ms, p.ring, p.loaded, p.aged,
                p.state[0], p.state[1], p.state[2]);
  }
}

}  // namespace

int main() {
  std::printf("--- Mcp2515Port TX stall, traced pass by pass ---\n");
  std::printf("    (diagnostic for the L3 2.4%% shortfall; the two checks\n");
  std::printf("     at the end are structural, the rest is for reading)\n");

  l3::Stimulus st = l3::load("l3_stimulus.txt");
  // SERIALISED: the capture's 1 ms timestamp quantisation lets
  // several frames share an instant, which no wire can deliver.
  // See l3::serialise() for what that did to the model.
  st = l3::serialise(st);
    // EVERY ROW OFFERED, none skipped -- see test_l2_bridge for why
    // this one line is here.
    uint32_t rows_v_ = 0;
    for (size_t q_ = 0; q_ < st.rows.size(); q_++)
      if (!st.rows[q_].charger) rows_v_++;
  if (!st.loaded) {
    // NOT a silent skip. A stimulus that will not load means this probe
    // measured nothing, and a green run that measured nothing is the
    // failure mode this file is meant to avoid elsewhere.
    std::printf("\nFAIL  stimulus not loadable: %s\n", st.why.c_str());
    return 1;
  }

  Rig r;
  Mcp2515Port::traceReset();

  // Chip-side samples, one per service() call, static for the same
  // reason the driver's trace is: these are large and the rig is a local.
  static const uint32_t kChipMax = Mcp2515Port::TRACE_N;
  static int8_t chip_sending[kChipMax];
  static uint32_t chip_wire[kChipMax];
  static char chip_buf[kChipMax][3];
  uint32_t chip_n = 0;

  size_t i = 0;
  uint32_t offered = 0;
  const uint64_t t0 = r.chip.micros();
  const uint64_t span = st.rows.back().t_us;
  uint64_t next_service = t0;
  // `|| i < rows.size()`: the clock advances in 20 us steps, so
  // elapsed time can step PAST the last row's timestamp without
  // ever equalling it, and the loop would end one frame short.
  // Serialising the stimulus pushed that last timestamp later and
  // made it visible; the offered == rows check below is what
  // caught it.
  while (r.chip.micros() - t0 <= span || i < st.rows.size()) {
    const uint64_t el = r.chip.micros() - t0;
    while (i < st.rows.size() && st.rows[i].t_us <= el) {
      const l3::Row& w = st.rows[i++];
      if (!w.charger) {            // vehicle -> charger, as the bridge does
        r.mcp.send(w.id, w.ext, w.data, w.len);
        offered++;
      }
    }
    if (r.chip.micros() >= next_service) {
      r.mcp.service(r.chip.millis());
      // THE CHIP'S SIDE OF THE SAME PASS. service() calls drainTx()
      // exactly once, so sample k here lines up with trace pass k --
      // an index correspondence, not a timestamp match, so it cannot
      // drift. Taken AFTER service() for the same reason the driver
      // stamps its abort after the clear: the pass's effect is what
      // matters, not the state it walked into.
      if (chip_n < kChipMax) {
        chip_sending[chip_n] = (int8_t)r.chip.sendingBuf();
        chip_wire[chip_n] = r.chip.wireEvents();
        for (int b = 0; b < 3; b++) {
          // 'R' eligible (TXREQ and a frame behind it), 'r' TXREQ set
          // with NO frame -- invisible to both the driver and the
          // engine -- 'l' a frame with TXREQ clear, '.' neither.
          const bool q = r.chip.txReq(b), ld = r.chip.txLoaded(b);
          chip_buf[chip_n][b] = q ? (ld ? 'R' : 'r') : (ld ? 'l' : '.');
        }
        chip_n++;
      }
      next_service += 200;
    }
    r.chip.advance(20);
  }
  r.mcp.service(r.chip.millis());

  const Mcp2515Port::TxPass* tr = Mcp2515Port::trace();
  const uint32_t n = Mcp2515Port::traceCount();
  const uint32_t total = Mcp2515Port::tracePasses();

  std::printf("\n  offered %u, delivered %u (%.1f%%)   "
              "aged %u dropped %u failed %u late %u\n",
              offered, r.chip.delivered(),
              100.0 * r.chip.delivered() / (offered ? offered : 1),
              r.mcp.txAged(), r.mcp.txDropped(), r.mcp.txFailed(),
              r.mcp.txLate());
  std::printf("  drainTx passes: %u (%u stored%s)\n", total, n,
              total > n ? ", TRACE TRUNCATED" : "");

  summarise(tr, n);

  // How much of the run was productive at all. A pass that loads nothing
  // with an empty ring is correct and idle; a pass that loads nothing
  // with frames waiting is the stall.
  uint32_t loads = 0, stalled = 0, idle = 0;
  for (uint32_t k = 0; k < n; k++) {
    loads += tr[k].loaded;
    if (tr[k].ring == 0) idle++;
    else if (tr[k].loaded == 0) stalled++;
  }
  std::printf("\n  PASS OUTCOMES: %u loaded %u frame(s) total "
              "(%.3f per pass)\n", n, loads, (double)loads / (n ? n : 1));
  std::printf("      ring empty, nothing to do : %6u (%4.1f%%)\n",
              idle, 100.0 * idle / (n ? n : 1));
  std::printf("      frames waiting, LOADED 0  : %6u (%4.1f%%)  <-- stall\n",
              stalled, 100.0 * stalled / (n ? n : 1));

  const Run s = longestStall(tr, n);
  std::printf("\n  LONGEST UNBROKEN STALL: %u us over %u passes, "
              "starting at t=%u us\n",
              s.us, s.end - s.begin, s.begin < n ? tr[s.begin].us - tr[0].us : 0);

  if (s.end > s.begin) {
    // A window around the stall, not the whole thing: enough passes
    // before it to show how the buffers got into that state, and enough
    // after to show what broke it.
    const uint32_t lead = s.begin > 8 ? 8 : s.begin;
    uint32_t to = s.end + 8;
    // Cap the dump so one pathological stall cannot print megabytes.
    if (to - (s.begin - lead) > 240) to = s.begin - lead + 240;
    std::printf("\n  THE STALL, PASS BY PASS "
                "(8 passes of lead-in, then the stall):\n");
    dump(tr, n, s.begin - lead, to, tr[0].us);

    // THE CHIP'S SIDE OF THE SAME PASSES. This is the question the
    // driver's trace cannot answer: three buffers reading busy is
    // perfectly correct if the engine is working through them, and a
    // defect if the engine is idle. `eng` is the buffer the chip has on
    // the wire, '-' for none; `wire` is the cumulative count of transmit
    // attempts, so a flat column means nothing started.
    const uint32_t cfrom = s.begin - lead;
    uint32_t cto = to < chip_n ? to : chip_n;
    if (cfrom < cto) {
      std::printf("\n  THE CHIP OVER THE SAME PASSES:\n");
      std::printf("      %10s %4s %9s  %-8s %s\n", "t_us", "eng",
                  "attempts", "REQ/load", "(R eligible, r TXREQ-no-frame)");
      for (uint32_t k = cfrom; k < cto; k++) {
        const int8_t e = chip_sending[k];
        std::printf("      %10u %4c %9u  %c %c %c   %s\n",
                    tr[k].us - tr[0].us, e < 0 ? '-' : (char)('0' + e),
                    chip_wire[k], chip_buf[k][0], chip_buf[k][1],
                    chip_buf[k][2], e < 0 ? "<-- ENGINE IDLE" : "");
      }
    }

    // And the summary form, which is the number that actually decides it.
    uint32_t idle_engine = 0, busy_engine = 0;
    for (uint32_t k = cfrom; k < cto; k++)
      (chip_sending[k] < 0 ? idle_engine : busy_engine)++;
    std::printf("\n  ACROSS THE STALL: engine busy on %u pass(es), "
                "idle on %u; attempts went %u -> %u\n",
                busy_engine, idle_engine,
                cfrom < cto ? chip_wire[cfrom] : 0,
                cfrom < cto ? chip_wire[cto - 1] : 0);
  }

  // ---- THE STRUCTURAL CHECKS ----------------------------------------
  //
  // Two, and only two. Everything above is a measurement under a modelled
  // load, which spec 9.1 puts on the bench; these are invariants of the
  // driver that hold at any load, so they are assertable here.

  // 1. 'U' is the datasheet-violation state: a buffer found idle that we
  //    never aborted, with TXnIF clear. DS20001801J p15 s3.3 says the
  //    chip clears TXREQ only on success and success sets TXnIF, so this
  //    is unreachable against a conforming chip. The fake IS conforming
  //    by construction, so a 'U' here means the DRIVER's bookkeeping has
  //    lost track of a buffer -- which would be a real defect and would
  //    also corrupt every other number on this page.
  uint32_t unexplained = 0;
  for (uint32_t k = 0; k < n; k++)
    for (int b = 0; b < 3; b++)
      if (tr[k].state[b] == 'U') unexplained++;
  check(offered == rows_v_,
        "the capture replay offered EVERY vehicle row -- a dropping "
        "injector would otherwise report a clean result on a stimulus it "
        "had mostly thrown away");
  check(unexplained == 0,
        "no buffer is ever found idle-and-unaccounted: the driver's view "
        "of the three hardware buffers stays in step with the chip's");

  // 2. The trace must actually cover the run. A truncated trace would
  //    silently turn the stall search into a search over a prefix, and
  //    the summary percentages into percentages of something else --
  //    with nothing in the output marking it.
  check(total == n,
        "the trace covers every drainTx pass, so the stall search and "
        "the percentages above are over the whole run and not a prefix");

  if (failures) {
    std::printf("\n%d failure(s)\n", failures);
    return 1;
  }
  std::printf("\ntest_tx_stall_trace: all checks OK\n");
  return 0;
}
