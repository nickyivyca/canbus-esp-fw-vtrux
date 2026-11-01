// cpp/'s own tests: where a TwaiFake member does more than pass its
// arguments to ../twai_fake.c, it keeps the meaning the interposer's own
// twai_fake.h gave it at charge-interposer/main 5426202.
//
//   make -C shared/twai_port/test cxx-test

#include <stdio.h>
#include <string.h>

#include "twai_fake.h"

// The interposer's virtual clock, which hardReset() connects; here a
// counter the test moves.
static uint64_t g_now_us = 0;
namespace hostclock {
uint64_t micros() { return g_now_us; }
}

using twaifake::fake;

static int g_fails = 0;

static void check(const char* name, bool ok) {
  printf("  %-4s %s\n", ok ? "ok" : "FAIL", name);
  if (!ok) g_fails++;
}

static void running() {
  fake().hardReset();
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT((gpio_num_t)21, (gpio_num_t)20,
                                                        TWAI_MODE_NORMAL);
  fake().install(&g);
  fake().start();
}

static esp_err_t send(uint32_t id) {
  twai_message_t m;
  memset(&m, 0, sizeof(m));
  m.identifier = id;
  m.data_length_code = 1;
  esp_err_t e = fake().transmit(&m);
  g_now_us += 1000;
  return e;
}

int main() {
  printf("setBusOffAfterSent:\n");

  // 0: ours struck with no frame sent (0 frames sent >= 0), so at once.
  running();
  fake().setBusOffAfterSent(0);
  check("0 strikes at once, with no frame sent",
        fake().state() == TWAI_STATE_BUS_OFF && fake().sent().empty());

  // n > 0: the moment the n-th frame from now has completed.
  running();
  fake().setBusOffAfterSent(1);
  bool before = fake().state() == TWAI_STATE_RUNNING;
  send(0x101);
  check("1 strikes after the first frame, not before",
        before && fake().state() == TWAI_STATE_BUS_OFF && fake().sent().size() == 1);

  running();
  fake().setBusOffAfterSent(2);
  send(0x101);
  bool after_one = fake().state() == TWAI_STATE_RUNNING;
  send(0x102);
  check("2 strikes after the second frame",
        after_one && fake().state() == TWAI_STATE_BUS_OFF && fake().sent().size() == 2);

  // -1: off, and it cancels a count already set.
  running();
  fake().setBusOffAfterSent(1);
  fake().setBusOffAfterSent(-1);
  send(0x101);
  send(0x102);
  check("-1 cancels a count already set",
        fake().state() == TWAI_STATE_RUNNING && fake().sent().size() == 2);

  // 0 replaces a count already set: after the strike and a recovery, the
  // old count does not strike again.
  running();
  fake().setBusOffAfterSent(2);
  fake().setBusOffAfterSent(0);
  bool struck = fake().state() == TWAI_STATE_BUS_OFF;
  fake().initiateRecovery();
  fake().completeRecovery();
  fake().start();
  send(0x101);
  send(0x102);
  check("0 replaces a count already set, which does not strike after the recovery",
        struck && fake().state() == TWAI_STATE_RUNNING && fake().sent().size() == 2);

  printf("\n%s\n", g_fails ? "FAILED" : "all ok");
  return g_fails ? 1 : 0;
}
