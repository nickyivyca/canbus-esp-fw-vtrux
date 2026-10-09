// A model of the ESP-IDF TWAI driver, for spec 9.1 L2.
//
// What this exists for: `TwaiPort::rxDropped()` and `busOffEvents()` had
// NO executed test at all. The delta-accumulation fix of 2026-10-04 -- it
// books `raw - previous` rather than the maximum, so losses after a driver
// reinstall are not swallowed -- was compile-verified only. A counter whose
// arithmetic nothing has ever run is a counter nobody should believe.
//
// Written from the toolchain's own `driver/twai.h` (see the shim header),
// never from `port_twai.cpp`.
//
// The four states are modelled because the driver's recovery path walks
// all of them, and because every TWAI call documents ESP_ERR_INVALID_STATE
// for a specific wrong state. A fake that ignored state would let
// `port_twai.cpp` make calls the real driver would reject, and the bench
// would then be the first thing to find out.

#pragma once

#include <stdint.h>

#include <deque>
#include <vector>

#include "driver/twai.h"
#include "provenance.h"

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

  // A frame arrives from the wire. If the RX queue is full it is lost and
  // booked to rx_missed_count, which is the real mechanism rather than an
  // injected number -- `g_config.rx_queue_len` sizes the queue, so a test
  // can fill it by sending more than the driver was configured to hold.
  void deliver(const Frame& f);
  void deliver(uint32_t id, bool ext, const uint8_t* data, uint8_t len);

  // Frames lost inside the controller before they ever reached the queue.
  // A different counter from rx_missed_count in the real driver, and the
  // bridge must treat both as lost frames.
  void injectRxFifoOverrun(uint32_t n) { rx_overrun_count_ += n; }

  void injectBusError(uint32_t n) {
    bus_error_count_ += n;
    if (n) alerts_ |= TWAI_ALERT_BUS_ERROR;
  }

  // Drive the controller bus-off. Idempotent while already bus-off: the
  // real controller does not re-enter a state it is in, and a fake that
  // re-raised the alert every call would make `busOffEvents()` count
  // polls instead of events -- hiding exactly the bug the counter has.
  void enterBusOff();

  // The 128 bus-free occurrences have happened. Per the header, recovery
  // ends in STOPPED -- NOT running.
  void completeRecovery();

  // Whether a transmitted frame is acknowledged. Unacknowledged frames
  // land in tx_failed_count.
  void setAcknowledged(bool yes) { acked_ = yes; }

  // Hold transmitted frames in the TX queue instead of putting them
  // straight on the wire, so a test can have frames PENDING when bus-off
  // strikes. Default true, which keeps every test that does not care
  // about the queue behaving as before.
  void setAutoFlush(bool yes) { auto_flush_ = yes; }
  void flushTx();
  uint32_t pendingTx() const { return (uint32_t)tx_pending_.size(); }

  // Frames the recovery threw away, counted so a test can check the
  // driver noticed the same number.
  uint32_t txDiscardedByRecovery() const { return tx_discarded_by_recovery_; }

  // ---- inspection ------------------------------------------------------
  twai_state_t state() const { return state_; }
  bool installed() const { return installed_; }
  const std::vector<Frame>& sent() const { return sent_; }
  // Frames actually handed to the driver by receive(). The coverage
  // measure for the vehicle side, same reasoning as the chip's
  // rxReads(): a run where this is zero never exercised the path.
  uint32_t framesHandedOut() const { return frames_handed_out_; }

  // Make driver_install() fail, so TwaiPort::begin() returns false.
  void setInstallFails(bool yes) { install_fails_ = yes; }

  uint32_t rxMissed() const { return rx_missed_count_; }
  uint32_t rxOverrun() const { return rx_overrun_count_; }
  uint32_t rxQueueLen() const { return (uint32_t)rx_.size(); }
  uint32_t configuredRxQueueLen() const { return cfg_rx_queue_len_; }

  // How many times a call was rejected for being made in the wrong state.
  // A test asserts this is zero for a healthy run: the driver quietly
  // ignoring a rejected call looks identical to the call succeeding.
  uint32_t invalidStateCalls() const { return invalid_state_calls_; }
  void noteInvalidState() { invalid_state_calls_++; }
  // Every twai_transmit() call, in ANY state, accepted or rejected. A
  // board whose vehicle controller never started cannot put a frame on
  // the wire, so wire silence cannot show that it tried; this can
  // (failed-start-vehicle, 2026-10-09, reviewer's suggestion).
  uint32_t transmitCalls() const { return transmit_calls_; }

  // Everything the ESP-IDF entry points need.
  esp_err_t install(const twai_general_config_t* g);
  esp_err_t uninstall();
  esp_err_t start();
  esp_err_t stop();
  esp_err_t transmit(const twai_message_t* m);
  esp_err_t receive(twai_message_t* m);
  esp_err_t readAlerts(uint32_t* alerts);
  esp_err_t reconfigureAlerts(uint32_t enabled, uint32_t* current);
  esp_err_t status(twai_status_info_t* s);
  esp_err_t initiateRecovery();

  // Wipe everything, as a power cycle would. Distinct from uninstall():
  // a test that wants a genuinely fresh driver calls this.
  void hardReset();

 private:
  bool installed_ = false;
  twai_state_t state_ = TWAI_STATE_STOPPED;
  bool acked_ = true;

  uint32_t cfg_rx_queue_len_ = 5;
  uint32_t cfg_tx_queue_len_ = 5;
  uint32_t alerts_enabled_ = 0;
  uint32_t alerts_ = 0;

  std::deque<Frame> rx_;
  std::deque<Frame> tx_pending_;
  std::vector<Frame> sent_;
  bool auto_flush_ = true;
  uint32_t tx_discarded_by_recovery_ = 0;

  // Cumulative since install, exactly as the real driver documents. An
  // uninstall/install pair resets them to zero, which is the case the
  // delta accumulation exists for.
  uint32_t rx_missed_count_ = 0;
  uint32_t rx_overrun_count_ = 0;
  uint32_t tx_failed_count_ = 0;
  uint32_t bus_error_count_ = 0;
  uint32_t invalid_state_calls_ = 0;
  uint32_t transmit_calls_ = 0;
  uint32_t frames_handed_out_ = 0;
  bool install_fails_ = false;
};

// The single instance the C entry points drive. A test gets at it to
// inject and inspect.
TwaiFake& fake();

// What this model does, and what each behaviour rests on. Same contract as
// the MCP2515 fake's table: a test can print the footing of its own result,
// and a behaviour the code does NOT implement gets NOT_MODELLED rather than
// being left out.
const prov::Assumption* assumptions(int* count);

}  // namespace twaifake
