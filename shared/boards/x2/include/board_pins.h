/*
 * board_pins.h -- x2's pins (shared/boards/SPEC.md B6 and section 3):
 * Autosport Labs ESP32-CAN-X2.
 *
 * board.c includes this file, and so may test equipment. A project file may
 * include it only if it is on that project's list of stock files that still
 * use pins (shared/tools SPEC R30). Everything else asks board.h for a port.
 */
#ifndef BOARD_PINS_H
#define BOARD_PINS_H

/* CAN port 0: the S3's TWAI. Its MCP2562 has STBY hardwired: no standby. */
#define TWAI_TX_GPIO_NUM            7
#define TWAI_RX_GPIO_NUM            6

/* CAN port 1: an MCP2515 on SPI3, through the GPIO matrix. INT is active low.
 * Its MCP2562 has STBY hardwired too. */
#define MCP2515_CS_GPIO_NUM         10
#define MCP2515_SCK_GPIO_NUM        12
#define MCP2515_MISO_GPIO_NUM       13
#define MCP2515_MOSI_GPIO_NUM       11
#define MCP2515_INT_GPIO_NUM        3
#define MCP2515_CRYSTAL_HZ          16000000

/* Offered: led1. */
#define LED1_GPIO_NUM               2

#endif
