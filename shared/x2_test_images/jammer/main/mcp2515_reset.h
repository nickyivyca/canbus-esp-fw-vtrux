/*
 * mcp2515_reset.h -- puts the X2's MCP2515 (CAN2) into configuration mode
 * and reads it back. TEST EQUIPMENT, NOT FOR VEHICLE (x2_test_images SPEC
 * T8). In configuration mode the MCP2515 neither transmits nor ACKs.
 */
#ifndef MCP2515_RESET_H
#define MCP2515_RESET_H

#include <stdint.h>
#include "esp_err.h"

/* CANSTAT's operating-mode bits (OPMOD, 7:5) in configuration mode. */
#define MCP2515_OPMOD_MASK   0xE0
#define MCP2515_OPMOD_CONFIG 0x80

/* Sends the SPI RESET instruction to the board's SPI CAN port `port`, waits
 * 10 ms, then reads CANSTAT and CANCTRL. Only RESET (0xC0) and READ (0x03)
 * are ever sent: nothing writes a register, loads a buffer or requests a
 * transmission. The SPI bus and device stay attached, so the chip select
 * stays driven high (deselected). */
esp_err_t mcp2515_reset_config(int port, uint8_t *canstat, uint8_t *canctrl);

#endif
