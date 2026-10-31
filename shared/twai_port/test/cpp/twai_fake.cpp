#include "twai_fake.h"

#include <cstring>

// The interposer's virtual clock (its host_bridge/host_clock.h). Declared
// here rather than included, so this file needs nothing else from there.
namespace hostclock {
uint64_t micros();
}

namespace twaifake {

static int64_t host_now(void*) { return (int64_t)hostclock::micros(); }

TwaiFake& fake() {
  static TwaiFake f;
  static bool wired = false;
  if (!wired) {
    wired = true;
    f.hardReset();
  }
  return f;
}

void TwaiFake::hardReset() {
  twai_fake_set_clock(host_now, nullptr);
  twai_fake_reset();
  twai_fake_set_auto_recovery(false);
  auto_flush_ = true;
  wire_time_ = false;
  noted_ = 0;
  applyTiming();
}

void TwaiFake::applyTiming() {
  twai_fake_set_tx_timing(wire_time_ ? TWAI_FAKE_TX_TIMED
                          : auto_flush_ ? TWAI_FAKE_TX_INSTANT
                                        : TWAI_FAKE_TX_HELD);
}

void TwaiFake::deliver(const Frame& f) {
  twai_message_t m;
  std::memset(&m, 0, sizeof(m));
  m.identifier = f.id;
  m.extd = f.ext ? 1 : 0;
  m.rtr = f.rtr ? 1 : 0;
  m.data_length_code = f.len > 8 ? 8 : f.len;
  std::memcpy(m.data, f.data, m.data_length_code);
  twai_fake_deliver(&m);
}

void TwaiFake::deliver(uint32_t id, bool ext, const uint8_t* data, uint8_t len) {
  Frame f;
  std::memset(&f, 0, sizeof(f));
  f.id = id;
  f.ext = ext;
  f.len = len > 8 ? 8 : len;
  for (uint8_t i = 0; i < f.len; i++) f.data[i] = data[i];
  deliver(f);
}

void TwaiFake::setFailIds(const std::vector<uint32_t>& ids) {
  twai_fake_clear_fail_rules();
  for (uint32_t id : ids) twai_fake_fail(id, -1);
}

void TwaiFake::setRefuseIds(const std::vector<uint32_t>& ids) {
  twai_fake_clear_refuse_rules();
  for (uint32_t id : ids) twai_fake_refuse(id, -1, ESP_ERR_TIMEOUT);
}

const std::vector<Frame>& TwaiFake::sent() {
  const int n = twai_fake_wire_count();
  sent_.clear();
  for (int i = 0; i < n; i++) {
    const twai_fake_frame_t* w = twai_fake_wire(i);
    Frame f;
    std::memset(&f, 0, sizeof(f));
    f.id = w->id;
    f.len = w->dlc;
    std::memcpy(f.data, w->data, 8);
    f.ext = w->extd;
    f.rtr = w->rtr;
    sent_.push_back(f);
  }
  return sent_;
}

const std::vector<twai_mode_t>& TwaiFake::installModes() {
  install_modes_.clear();
  for (int i = 0; i < twai_fake_install_log_count(); i++)
    install_modes_.push_back(twai_fake_install_log_mode(i));
  return install_modes_;
}

const std::vector<uint64_t>& TwaiFake::installTimesUs() {
  install_times_us_.clear();
  for (int i = 0; i < twai_fake_install_log_count(); i++)
    install_times_us_.push_back((uint64_t)twai_fake_install_log_time(i));
  return install_times_us_;
}

const std::vector<uint64_t>& TwaiFake::txQueuedTimesUs() {
  tx_queued_times_us_.clear();
  for (int i = 0; i < twai_fake_sent_count(); i++)
    tx_queued_times_us_.push_back((uint64_t)twai_fake_sent(i)->t_queued);
  return tx_queued_times_us_;
}

const std::vector<uint32_t>& TwaiFake::droppedAtStopIds() {
  dropped_ids_.clear();
  for (int i = 0; i < twai_fake_dropped_at_stop_count(); i++)
    dropped_ids_.push_back(twai_fake_dropped_at_stop_id(i));
  return dropped_ids_;
}

uint32_t TwaiFake::rxMissed() const {
  twai_status_info_t s;
  twai_fake_driver_status(&s);
  return s.rx_missed_count;
}

uint32_t TwaiFake::rxOverrun() const {
  twai_status_info_t s;
  twai_fake_driver_status(&s);
  return s.rx_overrun_count;
}

uint32_t TwaiFake::configuredRxQueueLen() const {
  return twai_fake_install_count() ? twai_fake_installed_general()->rx_queue_len : 5;
}

uint32_t TwaiFake::configuredTxQueueLen() const {
  return twai_fake_install_count() ? twai_fake_installed_general()->tx_queue_len : 5;
}

esp_err_t TwaiFake::install(const twai_general_config_t* g) {
  const twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
  const twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  return twai_driver_install(g, &t, &f);
}

const twai_fake_assumption_t* assumptions(int* count) {
  return twai_fake_assumptions(count);
}

}  // namespace twaifake
