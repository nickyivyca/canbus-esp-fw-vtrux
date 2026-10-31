// The charge interposer's C++ interface to the one TWAI driver fake
// (SPEC.md T10), so its host_bridge tests build on the merged fake as they
// are written: namespace twaifake, class TwaiFake, fake().
//
// Every member is a thin call into ../twai_fake.c, which is the model. The
// names and meanings are those of the interposer's own twai_fake.h at
// charge-interposer/main 5426202; where the merged model behaves
// differently from that file, ../README.md says how ("What changed from
// each source fake"). The interposer's clock adapter is
// hostclock::micros(), from its host_bridge/host_clock.h; hardReset()
// connects it.

#pragma once

#include <stdint.h>

#include <vector>

#include "driver/twai.h"
#include "../twai_fake.h"   // the C model; this file has its name

namespace twaifake {

struct Frame {
  uint32_t id;
  uint8_t data[8];
  uint8_t len;
  bool ext;
  bool rtr;
};

class TwaiFake {
 public:
  // ---- what the test does to the bus ----------------------------------
  void deliver(const Frame& f);
  void deliver(uint32_t id, bool ext, const uint8_t* data, uint8_t len);
  void injectRxFifoOverrun(uint32_t n) { twai_fake_rx_overrun(n); }
  void injectBusError(uint32_t n) { twai_fake_bus_error(n); }
  void enterBusOff() { twai_fake_bus_off(); }
  void completeRecovery() { twai_fake_complete_recovery(); }
  void setAcknowledged(bool yes) { twai_fake_set_acked(yes); }

  // Without airtime, auto-flush is INSTANT and no auto-flush is HELD; with
  // airtime, TIMED (the merged fake's default, which hardReset() undoes).
  void setAutoFlush(bool yes) { auto_flush_ = yes; applyTiming(); }
  void flushTx() { twai_fake_flush_tx(); }
  uint32_t pendingTx() { return (uint32_t)twai_fake_tx_pending(); }
  void setWireTime(bool yes) { wire_time_ = yes; applyTiming(); }
  bool wireTime() const { return wire_time_; }
  // The unstuffed length at 500 kbit/s, the interposer's bit rate.
  static uint32_t frameUs(const Frame& f) {
    return (uint32_t)twai_fake_frame_us(f.len, f.ext, f.rtr, 2000);
  }
  // Frames a stop, a start or an uninstall threw away (the head cut on the
  // wire apart), as the interposer's fake counted them.
  uint32_t txDiscardedByStop() const {
    return twai_fake_tx_discarded_by_stop() + twai_fake_tx_discarded_by_start() +
           twai_fake_tx_discarded_by_uninstall();
  }
  uint32_t txCutByStop() const { return twai_fake_tx_cut_by_stop(); }
  uint32_t txDiscardedByRecovery() const { return twai_fake_tx_discarded_by_recovery(); }
  void setBusOffAfterSent(int n) { twai_fake_bus_off_after_sent(n == 0 ? 1 : n); }
  void setAutoRecovery(bool yes) { twai_fake_set_auto_recovery(yes); }
  void setBusOffInFlightFails(bool yes) { twai_fake_set_busoff_inflight_fails(yes); }
  uint32_t txFailedInFlightAtBusOff() const { return twai_fake_tx_failed_at_busoff(); }
  void setMisreportQueueEmpty(bool yes) { twai_fake_misreport_msgs_to_tx(yes); }
  void setFailIds(const std::vector<uint32_t>& ids);
  void setRefuseIds(const std::vector<uint32_t>& ids);
  uint32_t failedById() const { return twai_fake_failed_by_rule(); }
  uint32_t refusedById() const { return twai_fake_refused_by_rule(); }

  // ---- inspection ------------------------------------------------------
  twai_state_t state() { return twai_fake_state(); }
  bool installed() const { return twai_fake_installed(); }
  // The frames that completed on the wire, in order.
  const std::vector<Frame>& sent();
  uint32_t framesHandedOut() const { return twai_fake_rx_handed_out(); }
  twai_mode_t mode() const { return twai_fake_mode(); }
  const std::vector<twai_mode_t>& installModes();
  const std::vector<uint64_t>& installTimesUs();
  const std::vector<uint64_t>& txQueuedTimesUs();
  uint32_t listenOnlyTxAttempts() const { return twai_fake_listen_only_tx_attempts(); }
  void setInstallFails(bool yes) { twai_fake_fail_installs(yes ? -1 : 0, ESP_ERR_NO_MEM); }
  uint32_t rxMissed() const;
  uint32_t rxOverrun() const;
  uint32_t rxQueueLen() const { return (uint32_t)twai_fake_rx_pending(); }
  uint32_t configuredRxQueueLen() const;
  uint32_t configuredTxQueueLen() const;
  const std::vector<uint32_t>& droppedAtStopIds();
  // No driver (ESP_ERR_INVALID_ARG) or the wrong state (INVALID_STATE).
  uint32_t invalidStateCalls() const { return twai_fake_wrong_state_calls() + noted_; }
  void noteInvalidState() { noted_++; }
  uint32_t transmitCalls() const { return twai_fake_transmit_calls(); }

  // ---- the ESP-IDF entry points ----------------------------------------
  esp_err_t install(const twai_general_config_t* g);
  esp_err_t uninstall() { return twai_driver_uninstall(); }
  esp_err_t start() { return twai_start(); }
  esp_err_t stop() { return twai_stop(); }
  esp_err_t transmit(const twai_message_t* m) { return twai_transmit(m, 0); }
  esp_err_t receive(twai_message_t* m) { return twai_receive(m, 0); }
  esp_err_t readAlerts(uint32_t* alerts) { return twai_read_alerts(alerts, 0); }
  esp_err_t reconfigureAlerts(uint32_t enabled, uint32_t* current) {
    return twai_reconfigure_alerts(enabled, current);
  }
  esp_err_t status(twai_status_info_t* s) { return twai_get_status_info(s); }
  esp_err_t initiateRecovery() { return twai_initiate_recovery(); }

  // Power-on, with the interposer's defaults: no airtime, auto-flush,
  // recovery completed by the test, the host clock.
  void hardReset();

 private:
  void applyTiming();
  bool auto_flush_ = true;
  bool wire_time_ = false;
  uint32_t noted_ = 0;
  std::vector<Frame> sent_;
  std::vector<twai_mode_t> install_modes_;
  std::vector<uint64_t> install_times_us_;
  std::vector<uint64_t> tx_queued_times_us_;
  std::vector<uint32_t> dropped_ids_;
};

TwaiFake& fake();

// What the model rests on: ../twai_fake.c's table.
const twai_fake_assumption_t* assumptions(int* count);

}  // namespace twaifake
