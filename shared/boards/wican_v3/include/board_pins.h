/*
 * board_pins.h -- wican_v3's pins (shared/boards/SPEC.md B6 and section 2):
 * MeatPi WiCAN OBD, hardware v3.
 *
 * The names are upstream WiCAN's, from main/hw_config.h's non-Pro block
 * (WICAN_V300), kept the same so that stock code builds for any WiCAN.
 *
 * board.c includes this file, and so may test equipment. A project file may
 * include it only if it is on that project's list of stock files that still
 * use pins (shared/tools SPEC R30). Everything else asks board.h for a port.
 */
#ifndef BOARD_PINS_H
#define BOARD_PINS_H

/* CAN port 0: the C3's TWAI, and its MCP2561/2's STBY (high is standby). */
#define TX_GPIO_NUM                 0
#define RX_GPIO_NUM                 3
#define CAN_STDBY_GPIO_NUM          6

/* Offered: the three LEDs and the Bluetooth-enable input (low is enabled). */
#define CONNECTED_LED_GPIO_NUM      8
#define ACTIVE_LED_GPIO_NUM         9
#define PWR_LED_GPIO_NUM            7
#define BLE_EN_PIN_NUM              5

#endif
