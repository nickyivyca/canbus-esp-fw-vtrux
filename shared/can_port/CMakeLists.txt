# vtrux/can_port: the CAN port interface both projects use (SPEC.md C1): a
# backend per port index (can_port.c) and the stock WiCAN code's CAN calls on
# port 0 (can_stock.c, C3 to C5). It names no backend: a project registers
# one, e.g. vtrux/twai_port, at start-up. can.h and can_port.h are public;
# both include driver/twai.h for twai_message_t and the timing type.
idf_component_register(SRCS "can_port.c" "can_stock.c"
                       INCLUDE_DIRS "include"
                       REQUIRES driver freertos esp_common
                       PRIV_REQUIRES esp_timer log)
