#include "l3_replay.h"

#include "mcp2515_fake_chip.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace l3 {
namespace {

uint8_t nyb(char c) {
  if (c >= '0' && c <= '9') return (uint8_t)(c - '0');
  if (c >= 'A' && c <= 'F') return (uint8_t)(c - 'A' + 10);
  if (c >= 'a' && c <= 'f') return (uint8_t)(c - 'a' + 10);
  return 0xFF;
}

std::string fnv1a64(const std::string& s) {
  uint64_t h = 0xCBF29CE484222325ull;
  for (size_t i = 0; i < s.size(); i++) {
    h ^= (uint8_t)s[i];
    h *= 0x100000001B3ull;
  }
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%016llX", (unsigned long long)h);
  return std::string(buf);
}

}  // namespace

Stimulus load(const char* path) {
  Stimulus st;

  // Try the caller's path, then the places the file actually is relative
  // to plausible working directories. run.sh executes each test binary
  // from the CALLER's cwd, not from host_bridge, so a single relative
  // path works by hand and fails under the runner -- which is how this
  // first shipped.
  const char* candidates[] = {
      path,
      "../../../../../notes/artifacts/interposer-firmware/l3_stimulus.txt",
      "projects/vtrux/notes/artifacts/interposer-firmware/l3_stimulus.txt",
      "../notes/artifacts/interposer-firmware/l3_stimulus.txt",
  };
  const char* env = std::getenv("L3_STIMULUS");
  std::FILE* fh = NULL;
  std::string used;
  if (env && *env) {
    fh = std::fopen(env, "rb");
    if (fh) used = env;
  }
  for (size_t ci = 0; !fh && ci < sizeof(candidates) / sizeof(*candidates);
       ci++) {
    if (!candidates[ci]) continue;
    fh = std::fopen(candidates[ci], "rb");
    if (fh) used = candidates[ci];
  }
  if (!fh) {
    st.why = std::string("cannot open the stimulus. Tried $L3_STIMULUS and ")
             + "the relative candidates in l3_replay.cpp; run from the "
               "project root or set L3_STIMULUS.";
    return st;
  }
  st.source_line = "(" + used + ")";
  std::string all;
  char buf[65536];
  size_t n;
  while ((n = std::fread(buf, 1, sizeof(buf), fh)) > 0) all.append(buf, n);
  std::fclose(fh);

  // Split header comments from the body. The body is everything after
  // the last '#' line, and the pin covers exactly that -- the same span
  // the generator hashed.
  std::string body;
  size_t pos = 0;
  while (pos < all.size()) {
    size_t eol = all.find('\n', pos);
    if (eol == std::string::npos) eol = all.size();
    const std::string line = all.substr(pos, eol - pos);
    if (!line.empty() && line[0] == '#') {
      const char* P = "# fnv1a64 ";
      const char* S = "# source ";
      if (line.compare(0, strlen(P), P) == 0)
        st.pin_expected = line.substr(strlen(P));
      else if (line.compare(0, strlen(S), S) == 0)
        st.source_line = line.substr(2);
    } else if (!line.empty()) {
      body = all.substr(pos);
      break;
    }
    pos = eol + 1;
  }

  st.pin_actual = fnv1a64(body);
  if (st.pin_expected.empty()) {
    st.why = "no '# fnv1a64' line in the header";
    return st;
  }
  if (st.pin_actual != st.pin_expected) {
    st.why = "pin mismatch: header says " + st.pin_expected +
             ", body hashes to " + st.pin_actual +
             " -- the stimulus changed since it was generated";
    return st;
  }

  // fields: t_us segment id_hex ext dlc data_hex
  pos = 0;
  while (pos < body.size()) {
    size_t eol = body.find('\n', pos);
    if (eol == std::string::npos) eol = body.size();
    if (eol > pos) {
      const std::string line = body.substr(pos, eol - pos);
      Row r;
      std::memset(&r, 0, sizeof(r));
      char seg = 'V';
      char idhex[16] = {0};
      char datahex[32] = {0};
      unsigned long long t = 0;
      int ext = 0, dlc = 0;
      if (std::sscanf(line.c_str(), "%llu %c %15s %d %d %31s",
                      &t, &seg, idhex, &ext, &dlc, datahex) >= 5) {
        r.t_us = t;
        r.charger = (seg == 'C');
        r.id = (uint32_t)std::strtoul(idhex, NULL, 16);
        r.ext = ext != 0;
        r.len = (uint8_t)(dlc > 8 ? 8 : dlc);
        for (uint8_t i = 0; i < r.len; i++) {
          const uint8_t hi = nyb(datahex[i * 2]);
          const uint8_t lo = nyb(datahex[i * 2 + 1]);
          if (hi == 0xFF || lo == 0xFF) break;
          r.data[i] = (uint8_t)((hi << 4) | lo);
        }
        st.rows.push_back(r);
      }
    }
    pos = eol + 1;
  }

  if (st.rows.empty()) {
    st.why = "the body parsed to zero frames";
    return st;
  }
  st.loaded = true;
  return st;
}

Stimulus serialise(const Stimulus& in, uint32_t bitrate) {
  Stimulus out = in;
  // One cursor per segment: they are separate wires and do not
  // contend with each other.
  uint64_t free_us[2] = {0, 0};
  for (size_t i = 0; i < out.rows.size(); i++) {
    Row& r = out.rows[i];
    mcpfake::CanFrame f;
    f.id = r.id;
    f.ext = r.ext;
    f.rtr = false;
    f.len = r.len;
    for (uint8_t b = 0; b < 8; b++) f.data[b] = r.data[b];
    const uint64_t dur_us =
        ((uint64_t)mcpfake::canFrameBits(f) * 1000000ull + bitrate - 1) /
        bitrate;
    const int seg = r.charger ? 1 : 0;
    // DELAYED, NEVER DROPPED. An earlier attempt at this skipped a
    // frame when the wire was busy and silently lost 65% of the
    // stimulus -- which reported a beautifully clean result that was
    // simply an empty bus.
    if (r.t_us < free_us[seg]) r.t_us = free_us[seg];
    free_us[seg] = r.t_us + dur_us;
  }
  return out;
}

}  // namespace l3
