#include "vehicle_port.h"

#include "twai_port.h"

namespace interposer {

bool vehicleSetup(can_port_mode_t install_mode, uint32_t rx_queue,
                  uint32_t tx_queue) {
  twai_port_config_t c;
  // The defaults (twai_port.h): 500 kbit/s and accept-all, which spec 2.4
  // asks for; the rest is set below whatever the defaults say.
  twai_port_config_defaults(&c);
  c.board_port = VEHICLE_PORT;
  c.mode = install_mode;
  c.filter.acceptance_code = 0;
  c.filter.acceptance_mask = 0xFFFFFFFFu;  // every identifier
  c.filter.single_filter = true;
  c.single_shot = false;  // retries continue (spec 2.2's reasoning)
  c.rx_queue_len = rx_queue;
  c.tx_queue_len = tx_queue;
  c.recovery = TWAI_PORT_RECOVERY_AUTO;
  c.drop_rtr = true;
  c.on_install_fail = TWAI_PORT_INSTALL_FAIL_REPORT;
  c.pre_teardown = nullptr;
  c.install_hook = nullptr;
  c.hook_ctx = nullptr;
  if (twai_port_setup(&c) != ESP_OK) return false;
  return can_port_register(VEHICLE_PORT, &twai_port_ops, nullptr) == ESP_OK;
}

}  // namespace interposer
