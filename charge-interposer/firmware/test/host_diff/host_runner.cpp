// Drives machine.cpp over a trace file produced by make_golden.py and prints
// the same canonical format that script writes to <name>.golden.
//
// The two outputs must be byte-identical. Any difference is a divergence
// between the C++ port and the Python reference, which is the only thing this
// program exists to find.
//
// Build (any host compiler, no dependencies beyond the C++ standard library):
//
//     g++ -std=c++11 -O2 -I ../../src -o host_runner \
//         host_runner.cpp ../../src/machine.cpp
//
// Run:
//
//     ./host_runner golden/<name>.trace > out.txt
//     diff out.txt golden/<name>.golden
//
// compare.py does both steps and reports the first divergence in context.

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "machine.h"

using namespace interposer;

namespace {

int hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// Parses an even-length hex string into bytes. Returns the byte count, or -1.
int parseHex(const char* s, uint8_t* out, int cap) {
  int n = 0;
  while (s[0] && s[0] != '\n' && s[0] != '\r') {
    const int hi = hexNibble(s[0]);
    const int lo = hexNibble(s[1]);
    if (hi < 0 || lo < 0) return -1;
    if (n >= cap) return -1;
    out[n++] = (uint8_t)((hi << 4) | lo);
    s += 2;
  }
  return n;
}

// Every Config field a trace may override. Named one by one rather than
// reflected, so adding a field to Config does not silently become
// overridable without anyone deciding it should be.
bool setConfigField(Config& c, const char* n, int32_t v) {
  if (std::strcmp(n, "arm_delay_ms") == 0) { c.arm_delay_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "override_max_ms") == 0) { c.override_max_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "full_debounce_ms") == 0) { c.full_debounce_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "chg_state_debounce_ms") == 0) { c.chg_state_debounce_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "bms_stale_ms") == 0) { c.bms_stale_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "chg_stale_ms") == 0) { c.chg_stale_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "chg_silence_ms") == 0) { c.chg_silence_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "chgmax_full_ca") == 0) { c.chgmax_full_ca = v; return true; }
  if (std::strcmp(n, "soc_arm_half") == 0) { c.soc_arm_half = v; return true; }
  if (std::strcmp(n, "vmax_hard_mv") == 0) { c.vmax_hard_mv = v; return true; }
  if (std::strcmp(n, "learned_ilim_min_ca") == 0) { c.learned_ilim_min_ca = v; return true; }
  if (std::strcmp(n, "burst_frames") == 0) { c.burst_frames = (uint8_t)v; return true; }
  if (std::strcmp(n, "burst_period_ms") == 0) { c.burst_period_ms = (uint32_t)v; return true; }
  if (std::strcmp(n, "mask_charger_telemetry") == 0) { c.mask_charger_telemetry = v != 0; return true; }
  if (std::strcmp(n, "revert_aux_pages") == 0) { c.revert_aux_pages = v != 0; return true; }
  if (std::strcmp(n, "diag_mirror") == 0) { c.diag_mirror = v != 0; return true; }
  return false;
}

}  // namespace

int main(int argc, char** argv) {
  // --diag replaces the frame/state output with the four spec 8.2 status
  // frames, so diag_stream.py can be diffed against it. The I/O-supplied
  // arguments are fixed constants in both, because the point is the PACKING
  // -- what the core puts in the bytes from its own state.
  bool diag_mode = false;
  // --events prints the core's EVENT LOG instead of its emissions, as
  // `X <t_ms> <a> <b> <c> <name>`. Added 2026-10-05 so EV_REPEAT_END can be
  // compared against machine.py's. It is NOT part of the L1 gate: the two
  // cores word their logs differently by design -- prose on the Python side,
  // a code and three numbers here -- so only the numbers are comparable.
  bool events_mode = false;
  int argi = 1;
  while (argc > argi) {
    if (std::strcmp(argv[argi], "--diag") == 0) { diag_mode = true; argi++; }
    else if (std::strcmp(argv[argi], "--events") == 0) {
      events_mode = true; argi++;
    } else break;
  }
  if (argc < argi + 1) {
    std::fprintf(stderr,
                 "usage: host_runner [--diag] [--events] <trace file>\n");
    return 2;
  }
  std::FILE* f = std::fopen(argv[argi], "rb");
  if (!f) {
    std::fprintf(stderr, "cannot open %s\n", argv[argi]);
    return 2;
  }

  Config cfg;
  configDefaults(cfg);

  // `K <field> <value>` lines carry Config overrides and must come before
  // any frame. An unknown field is a hard error: these readers skip
  // anything that is not V/C/T, so silently ignoring a K line the Python
  // side applied would produce a divergence that looks like a port bug.
  {
    char kline[256];
    long pos = std::ftell(f);
    while (std::fgets(kline, sizeof(kline), f)) {
      if (kline[0] != 'K') {
        std::fseek(f, pos, SEEK_SET);
        break;
      }
      char name[64];
      long value = 0;
      if (std::sscanf(kline + 1, " %63s %ld", name, &value) != 2) {
        std::fprintf(stderr, "bad K line: %s", kline);
        return 2;
      }
      if (!setConfigField(cfg, name, (int32_t)value)) {
        std::fprintf(stderr, "unknown Config field in K line: %s\n", name);
        return 2;
      }
      pos = std::ftell(f);
    }
  }

  InterposerCore core(cfg, 0);

  uint8_t last_state = core.state();
  EmitList out;
  char line[256];
  unsigned long lineno = 0;
  unsigned long last_diag_ms = 0;
  bool diag_started = false;
  static const uint32_t kDiagIds[4] = {DIAG_STATUS_ID, DIAG_OBSERVED_ID,
                                       DIAG_COUNTERS_ID, DIAG_BUILD_ID};

  while (std::fgets(line, sizeof(line), f)) {
    lineno++;
    const char kind = line[0];
    if (kind != 'V' && kind != 'C' && kind != 'T') continue;

    out.clear();
    unsigned long t_ms = 0;

    if (kind == 'T') {
      if (std::sscanf(line + 1, " %lu", &t_ms) != 1) {
        std::fprintf(stderr, "bad tick line %lu\n", lineno);
        return 2;
      }
      core.tick((uint32_t)t_ms, out);
    } else {
      unsigned long id = 0;
      unsigned ext = 0;
      char hex[64];
      if (std::sscanf(line + 1, " %lu %lx %u %63s", &t_ms, &id, &ext, hex) != 4) {
        std::fprintf(stderr, "bad frame line %lu\n", lineno);
        return 2;
      }
      uint8_t data[8];
      const int n = parseHex(hex, data, 8);
      if (n < 0) {
        std::fprintf(stderr, "bad hex on line %lu\n", lineno);
        return 2;
      }
      if (kind == 'C') {
        core.onChargerFrame((uint32_t)id, ext != 0, data, (uint8_t)n,
                            (uint32_t)t_ms, out);
      } else {
        core.onVehicleFrame((uint32_t)id, ext != 0, data, (uint8_t)n,
                            (uint32_t)t_ms, out);
      }
    }

    if (!diag_mode) {
      for (uint8_t i = 0; i < out.n; i++) {
        const Emit& e = out.items[i];
        if (events_mode) continue;
        std::printf("E %lu %u %lX %u ", t_ms, (unsigned)e.port,
                    (unsigned long)e.id, e.ext ? 1u : 0u);
        for (uint8_t b = 0; b < e.len; b++) std::printf("%02x", e.data[b]);
        std::printf("\n");
      }
    }
    if (out.dropped) {
      std::fprintf(stderr, "EMIT_MAX overflow on line %lu (%u dropped)\n",
                   lineno, (unsigned)out.dropped);
      return 3;
    }
    const bool changed = (core.state() != last_state);
    if (changed) {
      if (!diag_mode && !events_mode)
        std::printf("S %lu %s\n", t_ms, stateName(core.state()));
      last_state = core.state();
    }
    // Drained every pass, so a long trace cannot silently saturate
    // MAX_EVENTS (64) and lose everything after it -- log() returns early
    // when the array is full and reports nothing about having done so.
    if (events_mode) {
      for (uint8_t i = 0; i < core.eventCount(); i++) {
        const Event& e = core.event(i);
        std::printf("X %lu %ld %ld %ld %s\n", (unsigned long)e.t_ms,
                    (long)e.a, (long)e.b, (long)e.c, eventName(e.code));
      }
      core.clearEvents();
    }
    if (diag_mode &&
        (changed || !diag_started || t_ms - last_diag_ms >= 1000)) {
      diag_started = true;
      last_diag_ms = t_ms;
      uint8_t fr[4][8];
      core.diagFrames((uint32_t)t_ms, 0, 0, (uint32_t)t_ms, true, false, 0,
                      fr);
      for (uint8_t i = 0; i < 4; i++) {
        std::printf("D %lu %lX ", t_ms, (unsigned long)kDiagIds[i]);
        for (uint8_t b = 0; b < 8; b++) std::printf("%02x", fr[i][b]);
        std::printf("\n");
      }
    }
  }

  std::fclose(f);
  return 0;
}
