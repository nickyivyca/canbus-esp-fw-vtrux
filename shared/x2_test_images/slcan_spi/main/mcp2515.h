/*
 * mcp2515.h -- register-level access to the X2's MCP2515 (CAN2), written
 * from Microchip's MCP2515 datasheet (DS20001801), for slcan_spi.
 * NOT FOR VEHICLE (x2_test_images SPEC T14).
 *
 * Only can_side.c calls it, and only from its own task, so there is no
 * locking. The pins and the SPI host come from the board layer
 * (shared/boards/x2, board_spi_can(1, ...)).
 */
#ifndef MCP2515_H
#define MCP2515_H

#include <stdint.h>
#include "esp_err.h"
#include "can_frame.h"

/* Registers */
#define MCP_BFPCTRL     0x0C
#define MCP_TXRTSCTRL   0x0D
#define MCP_CANSTAT     0x0E
#define MCP_CANCTRL     0x0F
#define MCP_TEC         0x1C
#define MCP_REC         0x1D
#define MCP_CNF3        0x28
#define MCP_CNF2        0x29
#define MCP_CNF1        0x2A
#define MCP_CANINTE     0x2B
#define MCP_CANINTF     0x2C
#define MCP_EFLG        0x2D
#define MCP_TXB0CTRL    0x30
#define MCP_RXB0CTRL    0x60
#define MCP_RXB1CTRL    0x70

/* CANCTRL / CANSTAT operating modes (bits 7:5) */
#define MCP_MODE_MASK   0xE0
#define MCP_MODE_NORMAL 0x00
#define MCP_MODE_LISTEN 0x60
#define MCP_MODE_CONFIG 0x80
#define MCP_CANCTRL_ABAT 0x10

/* CANINTE / CANINTF */
#define MCP_RX0IF       0x01
#define MCP_RX1IF       0x02
#define MCP_TX0IF       0x04
#define MCP_ERRIF       0x20
#define MCP_WAKIF       0x40
#define MCP_MERRF       0x80

/* EFLG */
#define MCP_EWARN       0x01
#define MCP_RXWAR       0x02
#define MCP_TXWAR       0x04
#define MCP_RXEP        0x08
#define MCP_TXEP        0x10
#define MCP_TXBO        0x20
#define MCP_RX0OVR      0x40
#define MCP_RX1OVR      0x80

/* Sets up the SPI device on the board's SPI CAN port and the INT pin as an
 * input; no SPI traffic yet. */
esp_err_t mcp2515_attach(int port);

/* The INT pin's level: 0 while an enabled interrupt flag is set. */
int  mcp2515_int_level(void);

/* The GPIO the INT pin is on, for the caller's interrupt handler. */
int  mcp2515_int_gpio(void);

/* SPI RESET: the chip's reset, after which it is in configuration mode,
 * where it neither transmits nor ACKs. Waits for the oscillator. */
void mcp2515_reset(void);

uint8_t mcp2515_read(uint8_t reg);
void mcp2515_read_n(uint8_t reg, uint8_t *out, int n);
void mcp2515_write(uint8_t reg, uint8_t val);
void mcp2515_modify(uint8_t reg, uint8_t mask, uint8_t val);

/* Reads receive buffer 0 or 1 with READ RX BUFFER, which clears its RXnIF
 * when the transfer ends. */
void mcp2515_read_rx(int buf, can_frame_t *f);

/* Loads transmit buffer 0 and requests its transmission. */
void mcp2515_send_tx0(const can_frame_t *f);

/* Requests an operating mode and waits for CANSTAT to show it, up to
 * timeout_ms; ESP_ERR_TIMEOUT if it does not. */
esp_err_t mcp2515_set_mode(uint8_t mode, int timeout_ms);

#endif
