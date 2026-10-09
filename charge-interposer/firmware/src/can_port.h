// One CAN segment, as the bridge sees it.
//
// Two implementations: TwaiPort (the S3's built-in controller, vehicle side)
// and Mcp2515Port (SPI, charger side). The core never sees either -- it is
// handed frames and hands back frames, and main.cpp does the moving.
//
// The interface deliberately separates "queued" from "on the wire". Both of the
// failure modes we care about come from conflating them: coryjfowler's
// MCP_CAN::sendMsg() busy-waits for completion and then silently drops the
// frame on timeout, and a bridge that believes a dropped frame was delivered
// has no way to notice it is corrupting the stream. send() here only enqueues.
// txDropped() is how you find out it did not make it.

#pragma once

#include <stdint.h>

namespace interposer {

struct RxFrame {
  uint32_t id;
  uint32_t t_ms;
  uint8_t data[8];
  uint8_t len;
  bool ext;
};

class CanPort {
 public:
  virtual ~CanPort() {}

  // Bring the controller up at `bitrate`. False means the segment is unusable
  // and the bridge must not claim to be bridging.
  virtual bool begin(uint32_t bitrate) = 0;

  // Pull one frame if the driver has one. Non-blocking. False if none.
  virtual bool receive(RxFrame& out) = 0;

  // Enqueue for transmission. Non-blocking, and NEVER waits for the wire.
  // False means the queue is full -- the caller has fallen behind and the frame
  // is lost, which is a fact the bridge must surface rather than hide.
  virtual bool send(uint32_t id, bool ext, const uint8_t* data, uint8_t len) = 0;

  // Move queued frames toward the wire and drain the controller's receive
  // buffers. Call every loop iteration. Must not block.
  virtual void service(uint32_t t_ms) = 0;

  // Counters. All monotonic; a bridge that is keeping up shows zeros in the
  // last three.
  virtual uint32_t rxCount() const = 0;
  virtual uint32_t txCount() const = 0;
  // Frames LOST on receive, however they were lost: our software ring
  // overflowing, the controller's receive buffers overrunning, or its
  // queue filling. A port that books any of those somewhere else still
  // compiles and quietly breaks every caller written to this line --
  // spec 8.2's `bridge_ok` is fed from these, so a frame lost at the
  // chip must appear here or the flag reports a healthy bridge that
  // dropped a frame. Checked for every port by
  // test/host_bridge/test_canport_contract.cpp, through this interface
  // rather than through any concrete class.
  //
  // Per FRAME where the hardware offers it. The MCP2515 gives one flag
  // per receive buffer and not a count, so its contribution is at best
  // one per service() pass -- a bound, not a tally. TWAI does better:
  // `rx_missed_count` and `rx_overrun_count` are real per-frame totals.
  virtual uint32_t rxDropped() const = 0;
  virtual uint32_t txDropped() const = 0;  // our queue was full
  virtual uint32_t busErrors() const = 0;

  // True while the controller is in a state where it cannot transmit
  // (bus-off, or error-passive on the parts that report it).
  virtual bool busOff() const = 0;

  // Monotonic count of ENTRIES into bus-off. busOff() alone cannot answer
  // "was this segment bus-off during the last second", because recovery is
  // automatic (spec 2, B-7c) and a transient can begin and end between two
  // reads. spec 8.2's `bridge_ok` is health since the previous status frame,
  // so it needs the edge, not the level.
  virtual uint32_t busOffEvents() const = 0;

  virtual const char* name() const = 0;
};

// A fixed-capacity frame ring. No allocation, single-producer/single-consumer
// when the producer is an ISR and the consumer is the main loop.
template <uint16_t N>
class FrameRing {
 public:
  FrameRing() : head_(0), tail_(0), dropped_(0) {}

  bool push(uint32_t id, bool ext, const uint8_t* d, uint8_t len,
            uint32_t t_ms) {
    const uint16_t next = (uint16_t)((head_ + 1) % N);
    if (next == tail_) {
      dropped_++;
      return false;
    }
    RxFrame& f = buf_[head_];
    f.id = id;
    f.ext = ext;
    f.t_ms = t_ms;
    f.len = len > 8 ? 8 : len;
    for (uint8_t i = 0; i < f.len; i++) f.data[i] = d[i];
    head_ = next;
    return true;
  }

  bool pop(RxFrame& out) {
    if (tail_ == head_) return false;
    out = buf_[tail_];
    tail_ = (uint16_t)((tail_ + 1) % N);
    return true;
  }

  bool empty() const { return head_ == tail_; }

  // The frame at the head WITHOUT removing it, or null if empty.
  //
  // Spec 2's same-identifier hold needs to ask "may I send this one
  // yet?" before committing to taking it. Popping and pushing back
  // would put it at the TAIL, which is precisely the reordering the
  // hold exists to prevent, so the question has to be asked in place.
  const RxFrame* peek() const {
    if (head_ == tail_) return 0;
    return &buf_[tail_];
  }
  uint32_t dropped() const { return dropped_; }

  // Bench only: drop everything queued. Used when a diagnostic re-initialises
  // a port into a different mode and stale frames would muddy the next result.
  void clear() {
    tail_ = head_;
    dropped_ = 0;
  }

  // Peak occupancy is the number worth watching on the bench: it says how much
  // margin the ring actually had, which a drop count of zero does not.
  uint16_t used() const {
    return (uint16_t)((head_ + N - tail_) % N);
  }

 private:
  RxFrame buf_[N];
  volatile uint16_t head_;
  volatile uint16_t tail_;
  volatile uint32_t dropped_;
};

}  // namespace interposer
