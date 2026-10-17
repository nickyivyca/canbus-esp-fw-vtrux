#include "twai_fake.h"

#include <cstring>

#include "host_clock.h"

namespace twaifake {

TwaiFake& fake() {
  static TwaiFake f;
  return f;
}

void TwaiFake::hardReset() {
  installed_ = false;
  state_ = TWAI_STATE_STOPPED;
  acked_ = true;
  cfg_rx_queue_len_ = 5;
  cfg_tx_queue_len_ = 5;
  alerts_enabled_ = 0;
  alerts_ = 0;
  rx_.clear();
  tx_pending_.clear();
  sent_.clear();
  auto_flush_ = true;
  tx_discarded_by_recovery_ = 0;
  wire_time_ = false;
  busoff_after_sent_ = -1;
  sent_at_arm_ = 0;
  auto_recovery_ = false;
  busoff_inflight_fails_ = false;
  inflight_failed_ = 0;
  recovery_done_us_ = 0;
  misreport_queue_empty_ = false;
  fail_ids_.clear();
  refuse_ids_.clear();
  failed_by_id_ = 0;
  refused_by_id_ = 0;
  head_start_us_ = 0;
  tx_discarded_by_stop_ = 0;
  tx_cut_by_stop_ = 0;
  rx_missed_count_ = 0;
  rx_overrun_count_ = 0;
  tx_failed_count_ = 0;
  bus_error_count_ = 0;
  invalid_state_calls_ = 0;
  transmit_calls_ = 0;
  frames_handed_out_ = 0;
  install_fails_ = false;
  mode_ = TWAI_MODE_NORMAL;
  install_modes_.clear();
  install_times_us_.clear();
  tx_queued_times_us_.clear();
  listen_only_tx_attempts_ = 0;
}

void TwaiFake::deliver(const Frame& f) {
  // A stopped or bus-off controller is not on the bus at all, so a frame
  // on the wire does not reach it and is NOT a loss it should report.
  // Counting it as a loss would make every bus-off look like a flood of
  // dropped frames.
  if (!installed_ || state_ != TWAI_STATE_RUNNING) return;

  if ((uint32_t)rx_.size() >= cfg_rx_queue_len_) {
    rx_missed_count_++;              // lost to a full RX queue
    alerts_ |= TWAI_ALERT_RX_QUEUE_FULL;
    return;
  }
  rx_.push_back(f);
}

void TwaiFake::deliver(uint32_t id, bool ext, const uint8_t* data,
                       uint8_t len) {
  Frame f;
  std::memset(&f, 0, sizeof(f));
  f.id = id;
  f.ext = ext;
  f.rtr = false;
  f.len = len > 8 ? 8 : len;
  for (uint8_t i = 0; i < f.len; i++) f.data[i] = data[i];
  deliver(f);
}

void TwaiFake::enterBusOff() {
  if (!installed_) return;
  if (state_ == TWAI_STATE_BUS_OFF) return;   // already there; not an event
  state_ = TWAI_STATE_BUS_OFF;
  alerts_ |= TWAI_ALERT_BUS_OFF;
  // The controller is off the bus. Whatever is queued to send stays
  // queued -- it is `twai_initiate_recovery()` that throws it away, per
  // the header -- so tx_pending_ is deliberately NOT cleared here.
  rx_.clear();
}

void TwaiFake::completeRecovery() {
  if (state_ != TWAI_STATE_RECOVERING) return;
  // Header: "wait for 128 occurrences of the bus-free signal ... before
  // returning to the STOPPED state."
  state_ = TWAI_STATE_STOPPED;
  alerts_ |= TWAI_ALERT_BUS_RECOVERED;
}

esp_err_t TwaiFake::install(const twai_general_config_t* g) {
  if (install_fails_) return ESP_ERR_NO_MEM;   // the controller did not start
  if (installed_) return ESP_ERR_INVALID_STATE;   // "already installed"
  installed_ = true;
  state_ = TWAI_STATE_STOPPED;
  cfg_rx_queue_len_ = g && g->rx_queue_len ? g->rx_queue_len : 5;
  cfg_tx_queue_len_ = g && g->tx_queue_len ? g->tx_queue_len : 5;
  alerts_enabled_ = g ? g->alerts_enabled : 0;
  mode_ = g ? g->mode : TWAI_MODE_NORMAL;
  install_modes_.push_back(mode_);
  install_times_us_.push_back(hostclock::micros());
  alerts_ = 0;
  rx_.clear();
  // A fresh install resets the controller's cumulative counters. THIS is
  // the case the driver's delta accumulation exists for: they restart at
  // zero while the port's own totals must not go backwards or stall.
  rx_missed_count_ = 0;
  rx_overrun_count_ = 0;
  tx_failed_count_ = 0;
  bus_error_count_ = 0;
  return ESP_OK;
}

esp_err_t TwaiFake::uninstall() {
  // "Driver is not in stopped/bus-off state, or is not installed."
  if (!installed_) return ESP_ERR_INVALID_STATE;
  if (state_ != TWAI_STATE_STOPPED && state_ != TWAI_STATE_BUS_OFF)
    return ESP_ERR_INVALID_STATE;
  installed_ = false;
  rx_.clear();
  // A bus-off driver can still hold queued frames (initiateRecovery is
  // what clears them). Uninstalling frees the queue, so they are gone.
  dropQueuedAtStop();
  return ESP_OK;
}

esp_err_t TwaiFake::start() {
  tickRecovery();   // recovery completes on its own, read or not
  // "Driver is not in stopped state, or is not installed."
  if (!installed_ || state_ != TWAI_STATE_STOPPED)
    return ESP_ERR_INVALID_STATE;
  // Header: "The TX and RX queue are reset in this function, clearing any
  // messages that are unread or pending transmission."
  rx_.clear();
  dropQueuedAtStop();
  state_ = TWAI_STATE_RUNNING;
  return ESP_OK;
}

esp_err_t TwaiFake::stop() {
  // "Driver is not in running state, or is not installed."
  if (!installed_ || state_ != TWAI_STATE_RUNNING)
    return ESP_ERR_INVALID_STATE;
  // Header: "Any messages in the TX queue are cleared", and "A message
  // currently being transmitted ... will be ceased immediately". Frames
  // whose airtime has already ended reached the wire first.
  advanceWire();
  dropQueuedAtStop();
  state_ = TWAI_STATE_STOPPED;
  return ESP_OK;
}

void TwaiFake::dropQueuedAtStop() {
  if (tx_pending_.empty()) return;
  if (wire_time_ && state_ == TWAI_STATE_RUNNING) {
    tx_cut_by_stop_++;                       // the head was on the wire
    tx_discarded_by_stop_ += (uint32_t)tx_pending_.size() - 1;
  } else {
    tx_discarded_by_stop_ += (uint32_t)tx_pending_.size();
  }
  tx_pending_.clear();
}

uint32_t TwaiFake::frameUs(const Frame& f) {
  // Unstuffed frame bits incl. ACK, EOF and the 3-bit intermission: 47
  // for a standard data frame, 67 for an extended one, plus 8 per data
  // byte (none for an RTR). 2 us per bit at 500 kbit/s.
  const uint32_t bits = (f.ext ? 67u : 47u) + (f.rtr ? 0u : 8u * f.len);
  return bits * 2u;
}

void TwaiFake::tickRecovery() {
  if (auto_recovery_ && state_ == TWAI_STATE_RECOVERING &&
      hostclock::micros() >= recovery_done_us_)
    completeRecovery();
}

void TwaiFake::advanceWire() {
  tickRecovery();
  if (!wire_time_ || state_ != TWAI_STATE_RUNNING) return;
  // Nothing ACKs: the head is retransmitted and never completes.
  if (!acked_) return;
  const uint64_t now = hostclock::micros();
  bool any = false;
  while (!tx_pending_.empty()) {
    const uint64_t done = head_start_us_ + frameUs(tx_pending_.front());
    if (done > now) break;
    sent_.push_back(tx_pending_.front());
    tx_pending_.pop_front();
    head_start_us_ = done;     // the next frame follows back to back
    any = true;
    if (busoff_after_sent_ >= 0 &&
        sent_.size() - sent_at_arm_ >= (uint32_t)busoff_after_sent_) {
      busoff_after_sent_ = -1;       // once
      alerts_ |= TWAI_ALERT_TX_SUCCESS;
      if (busoff_inflight_fails_ && !tx_pending_.empty()) {
        // branch a: the halted frame is booked failed, out of the queue
        tx_failed_count_++;
        inflight_failed_++;
        alerts_ |= TWAI_ALERT_TX_FAILED;
        tx_pending_.pop_front();
      }
      enterBusOff();                 // what is still queued stays queued
      return;
    }
  }
  if (any) {
    // Header: TX_SUCCESS "The previous transmission was successful",
    // TX_IDLE "No more messages to transmit".
    alerts_ |= TWAI_ALERT_TX_SUCCESS;
    if (tx_pending_.empty()) alerts_ |= TWAI_ALERT_TX_IDLE;
  }
}

void TwaiFake::setAcknowledged(bool yes) {
  // An ACKer arriving: the retransmission of the head that it ACKs starts
  // now, not when the frame was first queued.
  if (wire_time_ && yes && !acked_) {
    advanceWire();
    head_start_us_ = hostclock::micros();
  }
  acked_ = yes;
}

esp_err_t TwaiFake::transmit(const twai_message_t* m) {
  transmit_calls_++;            // counted before any state check
  tickRecovery();
  // "TWAI driver is not in running state, or is not installed."
  if (!installed_ || state_ != TWAI_STATE_RUNNING)
    return ESP_ERR_INVALID_STATE;
  if (!m) return ESP_ERR_INVALID_ARG;
  // "ESP_ERR_NOT_SUPPORTED: Listen Only Mode does not support
  // transmissions" -- nothing reaches the wire, and the attempt is counted
  if (mode_ == TWAI_MODE_LISTEN_ONLY) {
    listen_only_tx_attempts_++;
    return ESP_ERR_NOT_SUPPORTED;
  }

  // The by-id knobs (twai_fake.h, setFailIds / setRefuseIds).
  for (uint32_t id : refuse_ids_)
    if (id == m->identifier) {
      refused_by_id_++;
      return ESP_ERR_TIMEOUT;
    }
  for (uint32_t id : fail_ids_)
    if (id == m->identifier) {
      failed_by_id_++;
      tx_failed_count_++;
      alerts_ |= TWAI_ALERT_TX_FAILED;
      return ESP_OK;   // accepted into the queue; the failure is later
    }

  if (!acked_ && !wire_time_) {
    tx_failed_count_++;
    alerts_ |= TWAI_ALERT_TX_FAILED;
    return ESP_OK;   // accepted into the queue; the failure is later
  }
  Frame f;
  std::memset(&f, 0, sizeof(f));
  f.id = m->identifier;
  f.ext = m->extd != 0;
  f.rtr = m->rtr != 0;
  f.len = m->data_length_code > 8 ? 8 : m->data_length_code;
  for (uint8_t i = 0; i < f.len; i++) f.data[i] = m->data[i];
  tx_queued_times_us_.push_back(hostclock::micros());
  if (wire_time_) {
    advanceWire();
    // "Transmission will start immediately if no other messages are
    // queued"; otherwise the frame waits behind the ones ahead of it.
    if (tx_pending_.empty()) head_start_us_ = hostclock::micros();
    tx_pending_.push_back(f);
    return ESP_OK;
  }
  tx_pending_.push_back(f);
  if (auto_flush_) flushTx();
  return ESP_OK;
}

void TwaiFake::flushTx() {
  if (tx_pending_.empty()) return;
  while (!tx_pending_.empty()) {
    sent_.push_back(tx_pending_.front());
    tx_pending_.pop_front();
  }
  alerts_ |= TWAI_ALERT_TX_SUCCESS | TWAI_ALERT_TX_IDLE;
}

esp_err_t TwaiFake::receive(twai_message_t* m) {
  // "TWAI driver is not installed." -- note this one does NOT require
  // running, so a read while stopped returns TIMEOUT rather than an error.
  if (!installed_) return ESP_ERR_INVALID_STATE;
  if (!m) return ESP_ERR_INVALID_ARG;
  if (rx_.empty()) return ESP_ERR_TIMEOUT;

  const Frame f = rx_.front();
  rx_.pop_front();
  frames_handed_out_++;
  std::memset(m, 0, sizeof(*m));
  m->identifier = f.id;
  m->extd = f.ext ? 1 : 0;
  m->rtr = f.rtr ? 1 : 0;
  m->data_length_code = f.len;
  for (uint8_t i = 0; i < f.len; i++) m->data[i] = f.data[i];
  return ESP_OK;
}

esp_err_t TwaiFake::readAlerts(uint32_t* alerts) {
  if (!installed_) return ESP_ERR_INVALID_STATE;
  if (!alerts) return ESP_ERR_INVALID_ARG;
  advanceWire();
  // Only the enabled ones are reported, and reading clears them.
  const uint32_t reportable = alerts_ & alerts_enabled_;
  *alerts = reportable;
  alerts_ &= ~reportable;
  return reportable ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t TwaiFake::reconfigureAlerts(uint32_t enabled, uint32_t* current) {
  if (!installed_) return ESP_ERR_INVALID_STATE;
  if (current) *current = alerts_enabled_;
  alerts_enabled_ = enabled;
  return ESP_OK;
}

esp_err_t TwaiFake::status(twai_status_info_t* s) {
  if (!installed_) return ESP_ERR_INVALID_STATE;
  if (!s) return ESP_ERR_INVALID_ARG;
  std::memset(s, 0, sizeof(*s));
  advanceWire();
  s->state = state_;
  s->msgs_to_tx = misreport_queue_empty_ ? 0u : (uint32_t)tx_pending_.size();
  s->msgs_to_rx = (uint32_t)rx_.size();
  s->tx_failed_count = tx_failed_count_;
  s->rx_missed_count = rx_missed_count_;
  s->rx_overrun_count = rx_overrun_count_;
  s->bus_error_count = bus_error_count_;
  return ESP_OK;
}

esp_err_t TwaiFake::initiateRecovery() {
  // "TWAI driver is not in the bus-off state, or is not installed."
  if (!installed_ || state_ != TWAI_STATE_BUS_OFF)
    return ESP_ERR_INVALID_STATE;
  state_ = TWAI_STATE_RECOVERING;
  recovery_done_us_ = hostclock::micros() + 1408u * 2u;
  // Header, verbatim: "This function will reset the TX queue, clearing any
  // messages pending transmission." Those frames never reach the wire and
  // nothing else reports them, so the count is kept for the test.
  tx_discarded_by_recovery_ += (uint32_t)tx_pending_.size();
  tx_pending_.clear();
  return ESP_OK;
}

// --------------------------------------------------------------------------
// What this model rests on.
// --------------------------------------------------------------------------
static const prov::Assumption kAssumptions[] = {
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, twai_initiate_recovery",
     "recovery waits for 128 bus-free occurrences and returns to the "
     "STOPPED state, not RUNNING. The port's service() is what starts it "
     "again, so that branch is real and is exercised"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, twai_initiate_recovery",
     "initiating recovery RESETS THE TX QUEUE, clearing any messages "
     "pending transmission. Modelled; still to be confirmed on silicon"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, TWAI_ALERT_BUS_OFF",
     "the bus-off alert is an EDGE -- 'bus-off condition occurred' -- "
     "consumed by twai_read_alerts, so it is not re-raised within one "
     "episode"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, return-value docs on every call",
     "ESP_ERR_INVALID_STATE for each documented wrong state: install when "
     "installed, start when not stopped, transmit when not running, "
     "uninstall when neither stopped nor bus-off, recover when not bus-off"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, twai_status_info_t",
     "rx_missed_count (full RX queue) and rx_overrun_count (RX FIFO "
     "overrun) are DIFFERENT losses, both cumulative since install"},
    {prov::ASSUMED,
     "no evidence either way",
     "a fresh driver_install zeroes the cumulative counters. This is the "
     "case the port's delta accumulation exists for, so the model takes "
     "the reading that makes that code matter"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, twai_transmit return values",
     "ESP_ERR_NOT_SUPPORTED in TWAI_MODE_LISTEN_ONLY: nothing is queued or "
     "sent, and the attempt is counted. The mode is the install's (a mode "
     "change is an uninstall / install). That a listen-only controller "
     "also sends no ACK and no error flag is the controller's documented "
     "behaviour, not something this model can show: it has no medium"},
    {prov::ASSUMED,
     "the fake has no shared medium",
     "arbitration, bit timing and how quickly TEC climbs. Any "
     "load-dependent or timing-dependent result needs the bench"},
    {prov::NOT_MODELLED,
     "nothing here raises it",
     "TWAI_ALERT_ERR_PASS and the error counters behind it. tx_error_counter "
     "and rx_error_counter are reported as zero, so no result may rest on "
     "them"},
    {prov::NOT_MODELLED,
     "nothing here raises it",
     "the errata workaround that also feeds rx_missed_count on some "
     "silicon revisions"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, twai_stop",
     "'Any messages in the TX queue are cleared', and a message being "
     "transmitted is 'ceased immediately'. Modelled always; it bites only "
     "with setWireTime(true), since otherwise nothing is ever queued"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, twai_start",
     "the TX and RX queues are reset, clearing anything unread or pending"},
    {prov::ASSUMED,
     "frame length arithmetic; implementor's bench boot 2026-10-13",
     "setWireTime(true): one frame on the wire at a time, back to back, "
     "each its UNSTUFFED length at 500 kbit/s, the first starting at the "
     "transmit that found the queue empty. A lower bound on airtime. With "
     "an ACKer the bench put 1 of the 4 power-up frames on the wire"},
    {prov::DATASHEET,
     "esp32s3 driver/twai.h, TWAI_ALERT_TX_SUCCESS / TWAI_ALERT_TX_IDLE",
     "a completed transmission raises TX_SUCCESS, and an emptied queue "
     "TX_IDLE (both modes; with airtime, when the airtime ends)"},
    {prov::ASSUMED,
     "normal-mode CAN retransmission; TX_FAILED documented single-shot",
     "with airtime and nothing ACKing, the head is retransmitted forever: "
     "nothing completes, no TX_SUCCESS, no TX_FAILED, tx_failed_count not "
     "raised, and twai_stop cuts it. The error counters this drives (TEC "
     "to error passive) are NOT modelled"},
    {prov::ASSUMED,
     "test-driven bus-off; header: recovery waits 128 bus-free signals",
     "setBusOffAfterSent strikes bus-off right after the n-th frame "
     "completes, leaving the rest queued (initiate_recovery is what clears "
     "them); setAutoRecovery completes recovery 1408 bit times (2816 us) "
     "later. What causes the bus-off is not modelled"},
};

const prov::Assumption* assumptions(int* count) {
  if (count) *count = (int)(sizeof(kAssumptions) / sizeof(kAssumptions[0]));
  return kAssumptions;
}

}  // namespace twaifake

// --------------------------------------------------------------------------
// The C entry points `port_twai.cpp` links against.
//
// Each one records a wrong-state rejection, so a test can assert that a
// healthy run made none. The driver ignores most of these return values,
// which is exactly why the count has to be observable from outside: a
// rejected call and a successful one look identical from inside the driver.
// --------------------------------------------------------------------------

using twaifake::fake;

static esp_err_t note(esp_err_t rc) {
  if (rc == ESP_ERR_INVALID_STATE) fake().noteInvalidState();
  return rc;
}

extern "C" {

esp_err_t twai_driver_install(const twai_general_config_t* g_config,
                              const twai_timing_config_t*,
                              const twai_filter_config_t*) {
  return note(fake().install(g_config));
}

esp_err_t twai_driver_uninstall(void) { return note(fake().uninstall()); }
esp_err_t twai_start(void) { return note(fake().start()); }
esp_err_t twai_stop(void) { return note(fake().stop()); }

esp_err_t twai_transmit(const twai_message_t* message, int) {
  return note(fake().transmit(message));
}

esp_err_t twai_receive(twai_message_t* message, int) {
  return note(fake().receive(message));
}

esp_err_t twai_read_alerts(uint32_t* alerts, int) {
  return note(fake().readAlerts(alerts));
}

esp_err_t twai_reconfigure_alerts(uint32_t alerts_enabled,
                                  uint32_t* current_alerts) {
  return note(fake().reconfigureAlerts(alerts_enabled, current_alerts));
}

esp_err_t twai_get_status_info(twai_status_info_t* status_info) {
  return note(fake().status(status_info));
}

esp_err_t twai_initiate_recovery(void) {
  return note(fake().initiateRecovery());
}

}  // extern "C"
