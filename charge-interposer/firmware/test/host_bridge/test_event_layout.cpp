// Spec 8.3: the event line's layout, `[t] STATE event a= b= c=`.
//
// Approved scope (reviewer, 2026-10-15): EVERY event code in the enum, not a
// subset; each printed line matches `[t] STATE name a=<int> b=<int> c=<int>`
// with STATE and name from stateName() / eventName(); one line per event;
// can-fails for a field-less line, a missing field and a name differing
// from eventName().
//
// HOW IT REACHES THE PRINTER WITHOUT A src CHANGE (the baseline's
// src_digest must not move). The event line is printed by main.cpp's
// static reportEvents(), from the static `core`, and the core's log() is
// private. This file #includes main.cpp itself, so both statics are in
// this translation unit, and opens log() with `#define private public`
// around the includes -- a test-side device; nothing under firmware/src is
// edited. main.cpp is built WITH serial (the bench images' path, not the
// truck's, which prints nothing: spec 8.3 A8b), into the capturing Serial
// that Arduino.h compiles in under HOST_SERIAL_CAPTURE.
//
// What it does: for every state the core has and every event code from
// EV_ARMED to EV_VEHICLE_MODE, log one event with extreme field values and
// run reportEvents(); the output must be exactly one line, in the form,
// carrying that t, state, name and values. Then several events in one pass
// must give that many lines, in order. EV_NONE (0) is the enum's "no
// event" placeholder, not an event, and is not logged.
//
// If the enum grows, the test must grow with it: eventName() of the code
// after EV_VEHICLE_MODE must still be the "?" fallback, so an appended
// event with a real name fails here until this file names it.

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include <algorithm>
#include <map>
#include <string>
#include <utility>
#include <vector>

#define private public
#include "../../src/main.cpp"
#undef private

namespace {

int failures = 0;

void check(bool ok, const std::string& what) {
  printf("  %s  %s\n", ok ? "ok  " : "FAIL", what.c_str());
  if (!ok) failures++;
}

std::vector<std::string> lines(const std::string& s) {
  std::vector<std::string> out;
  size_t i = 0;
  while (i < s.size()) {
    size_t j = s.find('\n', i);
    if (j == std::string::npos) j = s.size();
    std::string l = s.substr(i, j - i);
    if (!l.empty() && l[l.size() - 1] == '\r') l.erase(l.size() - 1);
    out.push_back(l);
    i = j + 1;
  }
  return out;
}

// Parse a decimal integer occupying all of `s`.
bool wholeInt(const std::string& s, long long* v) {
  if (s.empty()) return false;
  size_t i = (s[0] == '-') ? 1 : 0;
  if (i == s.size()) return false;
  for (size_t k = i; k < s.size(); k++)
    if (s[k] < '0' || s[k] > '9') return false;
  *v = strtoll(s.c_str(), nullptr, 10);
  return true;
}

// One line against the spec's form and the event it should carry.
// -> "" when it matches, else why not. The three fields are anchored at the
// END: names contain spaces and even "a=" (EV_VEHICLE_MODE's does).
std::string judgeLine(const std::string& l, uint32_t t, const char* state,
                      const char* name, int32_t a, int32_t b, int32_t c) {
  if (l.size() < 3 || l[0] != '[') return "does not start with [t]";
  const size_t close = l.find(']');
  if (close == std::string::npos) return "no ] after t";
  long long tv;
  if (!wholeInt(l.substr(1, close - 1), &tv)) return "t is not an integer";
  if (tv != (long long)t) return "t is " + l.substr(1, close - 1);
  size_t p = close + 1;
  if (p >= l.size() || l[p] != ' ') return "no space after ]";
  while (p < l.size() && l[p] == ' ') p++;
  const size_t st_end = l.find(' ', p);
  if (st_end == std::string::npos) return "nothing after STATE";
  if (l.substr(p, st_end - p) != state)
    return "STATE is '" + l.substr(p, st_end - p) + "'";
  p = st_end;
  while (p < l.size() && l[p] == ' ') p++;
  // the three fields, from the end
  const char* keys[3] = {" c=", " b=", " a="};
  long long want[3] = {c, b, a};
  size_t end = l.size();
  for (int k = 0; k < 3; k++) {
    const size_t at = l.rfind(keys[k], end);
    if (at == std::string::npos || at < p)
      return std::string("no") + keys[k] + " field";
    long long v;
    if (!wholeInt(l.substr(at + 3, end - at - 3), &v))
      return std::string(keys[k] + 1) + " is not an integer";
    if (v != want[k]) return std::string(keys[k] + 1) + " has the wrong value";
    end = at;
  }
  if (l.substr(p, end - p) != name)
    return "event is '" + l.substr(p, end - p) + "'";
  return "";
}

const uint8_t STATES[] = {S_PASSTHROUGH, S_MONITOR, S_OVERRIDE,
                          S_TERMINATED, S_SAFE, S_HOLD};

std::string printOne(uint8_t st, uint32_t t, uint8_t code, int32_t a,
                     int32_t b, int32_t c) {
  core->state_ = st;
  Serial.out.clear();
  core->log(t, code, a, b, c);
  reportEvents();
  return Serial.out;
}

}  // namespace

int main() {
  printf("spec 8.3 event-line layout ([t] STATE event a= b= c=)\n");
  Config cfg;
  configDefaults(cfg);
  static InterposerCore inst(cfg, 0);
  core = &inst;

  // the judge itself: what it must refuse (the can-fails)
  const char* N = "charge established";
  check(judgeLine("[7] MONITOR      charge established a=1 b=-2 c=3", 7,
                  "MONITOR", N, 1, -2, 3).empty(),
        "judge: a well-formed line passes");
  check(!judgeLine("[7] MONITOR      charge established", 7, "MONITOR", N,
                   0, 0, 0).empty(),
        "can-fail: a field-less line is refused");
  check(!judgeLine("[7] MONITOR      charge established a=1 c=3", 7,
                   "MONITOR", N, 1, 0, 3).empty(),
        "can-fail: a line missing b= is refused");
  check(!judgeLine("[7] MONITOR      charge establishd a=1 b=2 c=3", 7,
                   "MONITOR", N, 1, 2, 3).empty(),
        "can-fail: a name differing from eventName() is refused");
  check(!judgeLine("[7] OVERRIDE     charge established a=1 b=2 c=3", 7,
                   "MONITOR", N, 1, 2, 3).empty(),
        "can-fail: the wrong STATE is refused");
  check(!judgeLine("[7] MONITOR      charge established a=1 b=2 c=x", 7,
                   "MONITOR", N, 1, 2, 3).empty(),
        "can-fail: a non-integer field is refused");
  check(!judgeLine("7 MONITOR      charge established a=1 b=2 c=3", 7,
                   "MONITOR", N, 1, 2, 3).empty(),
        "can-fail: t without brackets is refused");

  // the enum is all here
  check(strcmp(eventName((uint8_t)(EV_VEHICLE_MODE + 1)), "?") == 0,
        "no event code after EV_VEHICLE_MODE has a name (else this test "
        "must name it)");
  for (unsigned s = 0; s < sizeof(STATES); s++)
    check(strcmp(stateName(STATES[s]), "?") != 0,
          std::string("state ") + stateName(STATES[s]) + " has a name");

  // every state x every event code: one line, in the form
  const uint32_t T = 4294967295u;
  const int32_t A = INT32_MIN, B = INT32_MAX, C = -1;
  int lines_ok = 0, cells = 0;
  for (unsigned s = 0; s < sizeof(STATES); s++) {
    for (unsigned code = EV_ARMED; code <= EV_VEHICLE_MODE; code++) {
      cells++;
      const std::string out = printOne(STATES[s], T, (uint8_t)code, A, B, C);
      std::vector<std::string> ls = lines(out);
      const char* nm = eventName((uint8_t)code);
      std::string why;
      if (strcmp(nm, "?") == 0) why = "code has no name";
      else if (ls.size() != 1) why = "printed " + std::to_string(ls.size()) + " lines";
      else why = judgeLine(ls[0], T, stateName(STATES[s]), nm, A, B, C);
      if (why.empty()) {
        lines_ok++;
      } else {
        check(false, std::string(stateName(STATES[s])) + " code " +
                         std::to_string(code) + ": " + why + " -- '" + out + "'");
      }
    }
  }
  check(lines_ok == cells && cells == 6 * (EV_VEHICLE_MODE - EV_ARMED + 1),
        "every state x every event code (" + std::to_string(cells) +
            " lines, t=4294967295, a=INT32_MIN, b=INT32_MAX, c=-1) is one "
            "line in the form: " + std::to_string(lines_ok) + " of " +
            std::to_string(cells));

  // all-zero fields still print all three (the bare-name defect of
  // 2026-10-14 that this layout pin exists to keep fixed)
  {
    const std::string out = printOne(S_TERMINATED, 604437, EV_FLOW_DROP, 0, 0, 0);
    std::vector<std::string> ls = lines(out);
    check(ls.size() == 1 && judgeLine(ls[0], 604437, "TERMINATED",
                                      eventName(EV_FLOW_DROP), 0, 0, 0).empty(),
          "an all-zero event prints a= b= c= -> '" + out + "'");
  }

  // several events in one pass: one line each, in order
  {
    core->state_ = S_MONITOR;
    Serial.out.clear();
    const uint8_t codes[] = {EV_ARMED, EV_TRIP, EV_VEHICLE_MODE, EV_REPEAT};
    for (int i = 0; i < 4; i++) core->log(100 + i, codes[i], i, -i, 7 * i);
    reportEvents();
    std::vector<std::string> ls = lines(Serial.out);
    bool ok = ls.size() == 4;
    for (int i = 0; ok && i < 4; i++)
      ok = judgeLine(ls[i], 100 + i, "MONITOR", eventName(codes[i]), i, -i,
                     7 * i).empty();
    check(ok, "4 events in one pass print 4 lines, in order (" +
                  std::to_string(ls.size()) + " printed)");
    check(core->eventCount() == 0, "reportEvents() leaves no event behind");
  }

  printf("\ntest_event_layout: %s\n",
         failures ? (std::to_string(failures) + " FAILED").c_str()
                  : "all checks OK");
  return failures ? 1 : 0;
}
