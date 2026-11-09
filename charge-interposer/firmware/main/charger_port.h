// The charger port behind vtrux/can_port: the interposer's own MCP2515
// driver (port_mcp2515.h) registered as can_port's backend for board port 1
// (spec 2.4, can_port C1; port step 2b). It stays this driver until port
// stage (b), when it becomes mcp2515_port.
//
// WHAT GOES THROUGH can_port AND WHAT DOES NOT. The bridge moves charger
// frames through can_port by the port's index (port_view.h), as it does the
// vehicle side's, and reads the port's general figures there too: lost on
// receive, refused sends, bus errors, bus-off. Spec 2.2's counters are read
// from the driver itself, not through can_port (spec 2.4): the three
// disjoint tallies of charger_tx_failures.h (txDropped, txFailed, txAged),
// and with them the driver's own figures on the serial LOSS line (the
// abandoned/aged/queue-full/late breakdown, the unexplained buffers, the
// rings) and in the witness dump.
//
// The backend's status read (can_port C2) gives what the driver can count:
//   rx_frames      frames handed to a receiver;
//   tx_frames      sends the driver queued;
//   rx_lost        the driver's rxDropped(): its ring full plus the chip's
//                  receive-buffer overruns, the overruns ONE PER OCCURRENCE,
//                  not per frame, so rx_lost_is_bound is set; split into
//                  rx_lost_queue_full and rx_lost_overrun;
//   tx_dropped     sends the driver refused with its ring full;
//   tx_refused     sends refused for any other reason: with the port down
//                  (the driver counts those in its txDropped() too, so
//                  tx_dropped + tx_refused is that figure), and a remote
//                  frame or no frame, refused here before the driver (it
//                  cannot send one; nothing bridged is one, spec 2);
//   tx_failed      the driver's txFailed();
//   bus_errors, bus_off, bus_off_entries   the driver's;
//   tx_pending, rx_waiting   the driver's rings (what the three hardware
//                  buffers hold is not in tx_pending);
//   rx_queue_len   63, what the 64-slot ring holds;
//   rx_flushed, bring_ups    counted here.
// Not counted, so 0: tec and rec (a status read would cost two SPI register
// reads, on every counter the bridge reads through port_view.h, for figures
// nothing reads), rx_high_water, arb_lost and the bus-off and teardown
// discards. Counts the driver resets at a teardown (its ring's drop count)
// are carried across it, so none goes backwards (can_port.h).

#pragma once

#include <stdint.h>

#include "can_port.h"   // the shared component's: vtrux/can_port
#include "port_mcp2515.h"

namespace interposer {

// The charger side's can_port index and board port (board_ports.h).
static const int CHARGER_PORT = 1;

// Registers `port` as can_port's backend for CHARGER_PORT, brought up at
// `bitrate` by can_port_up(). Once per boot, before anything calls the port.
// The driver's own bench mode, its internal loopback (setLoopback), is set on
// the driver before bringing it up. False if can_port refused the
// registration.
bool chargerRegister(Mcp2515Port& port, uint32_t bitrate);

// The time the next service pass gives the driver: its only clock, which
// spec 2.2's 100 ms age-out reads. PortView calls it with the pass's t_ms
// just before can_port_service(), so the driver gets the instant the caller
// gave, as when the bridge called the driver's service(t_ms) itself.
void chargerClock(uint32_t t_ms);

}  // namespace interposer
