// Built-in TWAI CAN port -- CAN1 on the ESP32-CAN-X2, the vehicle segment.
//
// The vehicle side gets the built-in controller deliberately. Measured on this
// truck's powertrain bus (see notes/artifacts/interposer_hw_busload_*.txt):
//
//     charging    864 frames/s   ~21 % bus load at 500 kbps
//     generator  2250 frames/s   ~50 % bus load
//
// while the charger segment carries only what the Bel emits (~55 fps) plus the
// 20 Hz command frame. Putting the heavy side on the hardware FIFO and the
// light side on SPI is free and it is the right way round.
//
// RX queue depth is the number that matters here and it is not the default. A
// 20 ms stall -- which the ESP-IDF flash-erase yield duration can produce
// (CONFIG_SPI_FLASH_ERASE_YIELD_DURATION_MS) -- is ~45 frames at 2250 fps. The
// driver's default of 5 is short by an order of magnitude, so it is a
// parameter, defaulted to 64.

#pragma once

#include "can_port.h"

namespace interposer {

class TwaiPort : public CanPort {
 public:
  TwaiPort(int tx_gpio, int rx_gpio, uint16_t rx_queue = 64,
           uint16_t tx_queue = 32);

  bool begin(uint32_t bitrate) override;
  bool receive(RxFrame& out) override;
  bool send(uint32_t id, bool ext, const uint8_t* data, uint8_t len) override;
  void service(uint32_t t_ms) override;

  // Spec 2.3's power-up round (2026-10-14): drain the driver's alerts and
  // fold them into this port's counters. service() calls it every pass, and
  // setup()'s wait for the round calls it directly while the loop is not
  // running yet.
  //
  // THERE IS EXACTLY ONE ALERT READER, and it has to stay that way:
  // twai_read_alerts CONSUMES what it returns. A second reader anywhere
  // would take BUS_OFF out from under the recovery path below -- which
  // counts the TX queue before recovery throws it away, the only place
  // those frames are ever counted -- or would eat the TX_SUCCESS alerts the
  // round is waiting on and leave the count short. Both failures are
  // silent. So callers poll through here rather than reading alerts
  // themselves.
  void pollAlerts();

  // Frames the CONTROLLER reported transmitted, which on CAN means another
  // node acknowledged. Not the same as send() returning true, which only
  // says the driver accepted it into the queue (spec 2.3, user).
  uint32_t txSuccess() const { return tx_success_; }

  // Spec 2.3's round is COUNTED FROM THESE, not from the alert above.
  //
  //   txPending()     the driver's msgs_to_tx: "Number of messages queued
  //                   for transmission or awaiting transmission completion"
  //                   (esp32s3 driver/twai.h), so it includes the frame in
  //                   flight and only falls when one completes or fails.
  //   txFailedCount() the driver's cumulative tx_failed_count.
  //
  // A frame leaves msgs_to_tx by succeeding or by failing, so
  // queued - txPending() - (txFailedCount() delta) is the number that
  // reached the wire, exactly, however many alerts were collapsed on the
  // way. Unlike the alerts these are NOT consuming reads, so anyone may
  // call them.
  uint32_t txPending() const;
  uint32_t txFailedCount() const;

  uint32_t rxCount() const override { return rx_count_; }
  uint32_t txCount() const override { return tx_count_; }
  uint32_t rxDropped() const override { return rx_dropped_; }
  uint32_t txDropped() const override { return tx_dropped_; }
  uint32_t busErrors() const override { return bus_errors_; }
  bool busOff() const override { return bus_off_; }
  uint32_t busOffEvents() const override { return bus_off_events_; }

  // Frames the driver had queued for transmission when bus-off struck,
  // which `twai_initiate_recovery()` discards ("reset the TX queue,
  // clearing any messages pending transmission" -- esp32s3
  // driver/twai.h). They were accepted by send(), so they are not in
  // txDropped(); they never failed an attempt, so the driver does not
  // count them either. Without this they vanish silently.
  //
  // NOT attributable between bridged frames and diagnostic sends: that
  // distinction is made in main.cpp's dispatch(), above this layer.
  uint32_t txDiscardedBusOff() const { return tx_discarded_bus_off_; }
  const char* name() const override { return "TWAI"; }

  // Bench diagnostics.
  uint32_t rxQueuePeak() const { return rx_peak_; }

  // Bench diagnostics only -- the bridge never calls this.
  //
  // TWAI_MODE_NO_ACK plus a self-reception request lets the controller receive
  // its own transmission with nothing else on the bus. That isolates the
  // driver from the wire: in normal mode a lone transmitter never sees an ACK,
  // retries forever, and looks identical to a broken driver. Call before
  // begin(); it also makes send() set the self-reception flag.
  void setSelfTest(bool on) { selftest_ = on; }
  // Listen-only (TWAI_MODE_LISTEN_ONLY): receives without ever writing an ACK
  // or an error flag. Call before begin(); setMode() below is the runtime
  // form. Two uses, and the first is no longer bench-only: spec 2.3 keeps
  // the vehicle port in this mode whenever the charger is silent, so the
  // board cannot disturb the truck's bus while it drives. On the bench,
  // paired with a NO_ACK transmitter on the far side, it also gives a
  // one-way physical-path test that normal mode cannot -- there each node
  // needs the other to ACK before either succeeds.
  void setListenOnly(bool on) { listen_only_ = on; }
  bool listenOnly() const { return listen_only_; }
  // Spec 2.3: change mode on a port that is already up.
  //
  // THERE IS NO TWAI CALL FOR THIS. The mode is an argument to
  // twai_driver_install(), so this is end() followed by begin(), and the
  // two consequences belong to every caller:
  //   * IT CAN FAIL, and legitimately -- end() is refused during bus-off
  //     recovery, which cannot be hurried. False means "still in the old
  //     mode", so the caller retries rather than assuming;
  //   * IT DISCARDS THE DRIVER'S QUEUES. Frames already received but not
  //     yet read are lost with the driver, so a caller that cares drains
  //     RX first; frames arriving on the wire during the reinstall cannot
  //     be saved by anything above this layer.
  // Returns true when the port is up in the requested mode.
  bool setMode(bool listen_only);
  // Tear the driver down. Returns false if it could NOT be torn down --
  // which happens from RECOVERING, where neither stop nor uninstall is
  // permitted and the 128 bus-free occurrences cannot be hurried. The
  // port then stays up, truthfully, rather than claiming to be down
  // while the driver is still installed.
  bool end();
  uint32_t txErrCounter() const;
  uint32_t rxErrCounter() const;

 private:
  int tx_gpio_;
  int rx_gpio_;
  uint16_t rx_queue_;
  uint16_t tx_queue_;
  // Remembered by begin() so setMode() can bring the driver back up on the
  // same wire; 0 until then, which makes a setMode() before begin() fail
  // rather than install at some default rate.
  uint32_t bitrate_;

  uint32_t rx_count_;
  uint32_t tx_count_;
  uint32_t rx_dropped_;
  // The driver's own lost-frame total at the last read, so rx_dropped_
  // can accumulate the DELTA. Taking the maximum instead would hide
  // every new loss after a driver reinstall until the fresh count
  // climbed past the old peak.
  uint32_t rx_lost_raw_;
  uint32_t tx_dropped_;
  uint32_t bus_errors_;
  uint32_t tx_success_;
  uint32_t rx_peak_;
  uint32_t last_ms_;
  bool bus_off_;
  uint32_t bus_off_events_;
  uint32_t tx_discarded_bus_off_;
  bool ready_;
  bool selftest_;
  bool listen_only_;
};

}  // namespace interposer
