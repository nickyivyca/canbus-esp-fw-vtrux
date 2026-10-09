#include "port_twai.h"

#include <Arduino.h>
#include <driver/twai.h>

namespace interposer {

TwaiPort::TwaiPort(int tx_gpio, int rx_gpio, uint16_t rx_queue,
                   uint16_t tx_queue)
    : tx_gpio_(tx_gpio),
      rx_gpio_(rx_gpio),
      rx_queue_(rx_queue),
      tx_queue_(tx_queue),
      rx_count_(0),
      tx_count_(0),
      rx_dropped_(0),
      rx_lost_raw_(0),
      tx_dropped_(0),
      bus_errors_(0),
      rx_peak_(0),
      last_ms_(0),
      bus_off_(false),
      bus_off_events_(0),
      tx_discarded_bus_off_(0),
      ready_(false),
      selftest_(false),
      listen_only_(false) {}

bool TwaiPort::begin(uint32_t bitrate) {
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
      (gpio_num_t)tx_gpio_, (gpio_num_t)rx_gpio_,
      listen_only_ ? TWAI_MODE_LISTEN_ONLY
                   : (selftest_ ? TWAI_MODE_NO_ACK : TWAI_MODE_NORMAL));
  g.rx_queue_len = rx_queue_;
  g.tx_queue_len = tx_queue_;

  twai_timing_config_t t;
  switch (bitrate) {
    case 1000000UL: t = TWAI_TIMING_CONFIG_1MBITS(); break;
    case 500000UL:  t = TWAI_TIMING_CONFIG_500KBITS(); break;
    case 250000UL:  t = TWAI_TIMING_CONFIG_250KBITS(); break;
    case 125000UL:  t = TWAI_TIMING_CONFIG_125KBITS(); break;
    default: return false;
  }

  // Accept everything. A bridge that filters in hardware cannot forward what it
  // filtered, and we do not yet know the full ID set the Bel cares about.
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();

  if (twai_driver_install(&g, &t, &f) != ESP_OK) return false;
  if (twai_start() != ESP_OK) {
    twai_driver_uninstall();
    return false;
  }

  twai_reconfigure_alerts(
      TWAI_ALERT_RX_QUEUE_FULL | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_ERROR |
          TWAI_ALERT_BUS_OFF | TWAI_ALERT_TX_FAILED,
      NULL);

  ready_ = true;
  return true;
}

bool TwaiPort::receive(RxFrame& out) {
  if (!ready_) return false;
  twai_message_t m;
  if (twai_receive(&m, 0) != ESP_OK) return false;  // non-blocking
  if (m.rtr) return false;  // nothing on this bus uses remote frames
  out.id = m.identifier;
  out.ext = m.extd != 0;
  out.len = m.data_length_code > 8 ? 8 : m.data_length_code;
  for (uint8_t i = 0; i < out.len; i++) out.data[i] = m.data[i];
  out.t_ms = last_ms_;
  rx_count_++;
  return true;
}

bool TwaiPort::send(uint32_t id, bool ext, const uint8_t* data, uint8_t len) {
  if (!ready_) return false;
  twai_message_t m = {};
  m.identifier = id;
  m.extd = ext ? 1 : 0;
  m.rtr = 0;
  m.data_length_code = len > 8 ? 8 : len;
  m.self = selftest_ ? 1 : 0;  // bench only; see setSelfTest()
  for (uint8_t i = 0; i < m.data_length_code; i++) m.data[i] = data[i];

  // Ticks-to-wait of 0: enqueue or fail, never block. The driver owns a real
  // TX queue, so a failure here means we have genuinely fallen behind and the
  // frame is lost -- which the bridge reports rather than hides.
  if (twai_transmit(&m, 0) != ESP_OK) {
    tx_dropped_++;
    return false;
  }
  tx_count_++;
  return true;
}

void TwaiPort::service(uint32_t t_ms) {
  if (!ready_) return;
  last_ms_ = t_ms;

  uint32_t alerts = 0;
  if (twai_read_alerts(&alerts, 0) == ESP_OK && alerts) {
    // NOT rx_dropped_++ here. The alert says "the queue filled at least
    // once since you last asked", not how many frames that cost, so
    // counting it was one per alert and can_port.h asks for frames lost.
    // The authoritative per-frame figures come from the status read below.
    if (alerts & TWAI_ALERT_BUS_ERROR) bus_errors_++;
    if (alerts & TWAI_ALERT_BUS_OFF) {
      if (!bus_off_) bus_off_events_++;
      bus_off_ = true;

      // Count what recovery is about to throw away, BEFORE throwing it.
      // `twai_initiate_recovery` is documented to "reset the TX queue,
      // clearing any messages pending transmission" (esp32s3
      // driver/twai.h), and those frames never reach the wire. Nothing
      // else reports them: they were accepted by `send()`, so they are
      // not in tx_dropped_, and they never failed a transmission, so
      // they are not in the driver's tx_failed_count either. Without
      // this they are simply gone.
      //
      // The read has to happen here rather than in the status block
      // below, because by then the queue is already empty.
      //
      // WHAT THIS COUNT CANNOT DO: say which of them were bridged
      // frames and which were the diagnostic sends. That distinction
      // lives in `main.cpp`'s dispatch(), above this layer, so the port
      // reports the total and the caller decides. Spec 8.2 is satisfied
      // regardless -- `bridge_ok` already clears for the interval
      // containing the bus-off entry.
      twai_status_info_t pre;
      if (twai_get_status_info(&pre) == ESP_OK)
        tx_discarded_bus_off_ += pre.msgs_to_tx;

      // Recovery is deliberately automatic. A bridge that stays bus-off after a
      // transient is a bridge that has silently partitioned the bus, which is
      // the failure this whole design is trying not to have.
      twai_initiate_recovery();
    }
  }

  twai_status_info_t st;
  if (twai_get_status_info(&st) == ESP_OK) {
    if (st.msgs_to_rx > rx_peak_) rx_peak_ = st.msgs_to_rx;

    // can_port.h: rxDropped() is "controller overrun, frames lost". The
    // driver keeps both kinds as running per-frame totals since install:
    //   rx_missed_count  -- lost to a full RX queue
    //   rx_overrun_count -- lost to an RX FIFO overrun
    // so take them rather than counting alerts. Guarded against going
    // backwards because the contract says these counters are monotonic
    // and a driver reinstall would reset the chip's.
    // Accumulate the delta, not the maximum. These are cumulative
    // since the driver was installed and `twai_start` does not reset
    // them -- only a fresh install does, and then they restart at 0.
    // Tracking the maximum would swallow every loss after a reinstall
    // until the new count overtook the old peak.
    const uint32_t raw = st.rx_missed_count + st.rx_overrun_count;
    rx_dropped_ += (raw >= rx_lost_raw_) ? (raw - rx_lost_raw_) : raw;
    rx_lost_raw_ = raw;
    // `bus_off_` means "inside a bus-off episode that has already been
    // counted", and it is what stops `bus_off_events_` incrementing once
    // per poll instead of once per episode. So it has to be cleared the
    // moment the episode actually ends.
    //
    // `st` is a SNAPSHOT, and that is the trap: the pass that performs
    // the restart below reads STOPPED, so it never reached the RUNNING
    // test above and left the flag set. The flag then cleared only on
    // some later pass that happened to read RUNNING -- and a genuine
    // second bus-off arriving before that pass was swallowed, because
    // the increment is gated on `!bus_off_`. Under-counting here reaches
    // spec 8.2: `bridge_ok` would read fine across a bus-off it never
    // booked. Found 2026-10-04 by `test_twai_port`, the first test ever
    // to execute this counter.
    if (st.state == TWAI_STATE_RUNNING) bus_off_ = false;
    if (st.state == TWAI_STATE_STOPPED) {
      if (twai_start() == ESP_OK) bus_off_ = false;   // the episode is over
    }
  }
}

bool TwaiPort::end() {
  if (!ready_) return true;   // already down; saying so is not a failure

  // Teardown used to be `twai_stop(); twai_driver_uninstall();` with both
  // return values dropped, which is wrong from three of the four states
  // and said nothing about it:
  //   RUNNING     -- stop is valid, uninstall then valid
  //   STOPPED     -- stop is REJECTED (it needs running); uninstall fine
  //   BUS_OFF     -- stop is REJECTED; uninstall IS permitted from here
  //                  ("not in stopped/bus-off state" is the error case)
  //   RECOVERING  -- both rejected, and there is no way to hurry the 128
  //                  bus-free occurrences along
  // The last one is the dangerous one: it left the driver installed while
  // the port claimed to be down, so a later begin() would hit "already
  // installed" and a test could reuse a half-installed driver without
  // anything saying so.
  twai_status_info_t st;
  if (twai_get_status_info(&st) == ESP_OK) {
    if (st.state == TWAI_STATE_RECOVERING) {
      // Not an error we can clear here, and pretending otherwise is worse
      // than reporting it. The caller can retry once recovery completes.
      return false;
    }
    if (st.state == TWAI_STATE_RUNNING && twai_stop() != ESP_OK) return false;
  }

  if (twai_driver_uninstall() != ESP_OK) return false;
  ready_ = false;
  return true;
}

uint32_t TwaiPort::txErrCounter() const {
  twai_status_info_t st;
  if (twai_get_status_info(&st) != ESP_OK) return 0;
  return st.tx_error_counter;
}

uint32_t TwaiPort::rxErrCounter() const {
  twai_status_info_t st;
  if (twai_get_status_info(&st) != ESP_OK) return 0;
  return st.rx_error_counter;
}

}  // namespace interposer
