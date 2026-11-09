// A port the shared vtrux/can_port has a backend for, as the bridge sees it
// (bridge_port.h's CanPort), by its port index (spec 2.4, port step 2b).
//
// The vehicle side is board port 0, with vtrux/twai_port registered as its
// backend (vehicle_port.h). Every call here goes through can_port by index,
// never to the driver directly, so the bridge reaches a port the way
// can_port says ports are reached; what is TWAI-only (the mode switch, the
// alert poll of the power-up round) main.cpp asks of twai_port itself.
//
// WHICH COUNTER FEEDS WHAT is decided here and in main.cpp, not by the port
// (spec 2.4): the port counts every frame it loses or discards, the
// interposer only bridged ones. From the port's status read (twai_port T7):
//
//   rxDropped()     rx_lost: the receive queue full, the FIFO overrun, and
//                   the frames waiting when the restart after bus-off
//                   recovery reset the queue (rx_lost_restart, D3);
//   txDropped()     tx_dropped + tx_refused: sends refused with the queue
//                   full, and for any other reason, the port down included
//                   (D2);
//   busErrors()     bus_errors, the driver's own count, one per error (D1);
//   busOff()        bus_off, the level; busOffEvents() bus_off_entries,
//                   once per episode.
//
// The frames a teardown discards (tx_discarded_teardown,
// rx_discarded_teardown) feed nothing (D6, spec 12's 2.3 entry).

#pragma once

#include <stdint.h>

#include "bridge_port.h"
#include "can_port.h"   // the shared component's: vtrux/can_port

namespace interposer {

class PortView : public CanPort {
 public:
  explicit PortView(int index) : index_(index), last_ms_(0) {}

  // Brings the port up through its backend (can_port_up). The bit rate is
  // the backend's own setting, given at its setup (vehicle_port.h: 500
  // kbit/s, spec 2.4); the only rate this firmware uses is 500000, so any
  // other is refused here rather than silently run at the backend's.
  bool begin(uint32_t bitrate) override;

  // One frame if the backend has one waiting, never waiting for one. Its
  // t_ms is the time of the last service() pass, as before port step 2b.
  // Remote frames never arrive: the backend drops them (spec 2).
  bool receive(RxFrame& out) override;

  // Enqueue only (can_port C2): false means not queued, and the backend has
  // counted it.
  bool send(uint32_t id, bool ext, const uint8_t* data, uint8_t len) override;

  // The backend's service pass (can_port_service), non-blocking; t_ms is
  // kept for the frames received until the next pass.
  void service(uint32_t t_ms) override;

  uint32_t rxCount() const override { return status().rx_frames; }
  uint32_t txCount() const override { return status().tx_frames; }
  uint32_t rxDropped() const override { return status().rx_lost; }
  uint32_t txDropped() const override {
    const can_port_status_t s = status();
    return s.tx_dropped + s.tx_refused;
  }
  uint32_t busErrors() const override { return status().bus_errors; }
  bool busOff() const override { return status().bus_off; }
  uint32_t busOffEvents() const override {
    return status().bus_off_entries;
  }
  const char* name() const override { return can_port_name(index_); }

  // The whole status read (twai_port T7). A port with no backend reads all
  // zeros, DOWN.
  can_port_status_t status() const;

  int index() const { return index_; }

 private:
  int index_;
  uint32_t last_ms_;
};

}  // namespace interposer
