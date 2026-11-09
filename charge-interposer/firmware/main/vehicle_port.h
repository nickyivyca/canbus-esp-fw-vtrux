// The vehicle port: vtrux/twai_port on board port 0, registered with
// vtrux/can_port for that index (spec 2.4, port step 2b). It replaced this
// firmware's own TwaiPort (port_twai.cpp).
//
// The interposer's settings (spec 2.4), the same in every variant except
// the mode it installs in and the queue depths, which a bench variant may
// choose:
//   - installed in the mode given; the bridge installs in normal mode and
//     reaches listen-only by a switch (2.3), and a switch is a teardown and
//     a reinstall (twai_port T4);
//   - bus-off recovery automatic (spec 2, B-7c);
//   - remote (RTR) frames dropped (spec 2);
//   - every identifier accepted in hardware;
//   - 500 kbit/s;
//   - an install that fails at any step, its alert setup included, is
//     reported, never a restart, and the caller treats it as a failed start
//     (2.1, B-7d);
//   - no pre-teardown or install hook.

#pragma once

#include <stdint.h>

#include "can_port.h"   // the shared component's: vtrux/can_port

namespace interposer {

// The vehicle side's can_port index and board port (board_ports.h).
static const int VEHICLE_PORT = 0;

// Spec 2.4's queue depths for the bridge.
static const uint32_t VEHICLE_RX_QUEUE = 64;
static const uint32_t VEHICLE_TX_QUEUE = 32;

// twai_port_setup() with the settings above, then twai_port registered as
// can_port's backend for VEHICLE_PORT. Nothing is installed: the caller
// brings the port up (PortView::begin, can_port_up). Once per boot. False
// if either step refused.
bool vehicleSetup(can_port_mode_t install_mode,
                  uint32_t rx_queue = VEHICLE_RX_QUEUE,
                  uint32_t tx_queue = VEHICLE_TX_QUEUE);

}  // namespace interposer
