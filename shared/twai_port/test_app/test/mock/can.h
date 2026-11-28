/* Host stand-in, compile only (test_stack.py): shared/can_port's can.h, the
 * calls the probe makes. */
#ifndef MOCK_CAN_H
#define MOCK_CAN_H
#include <stdbool.h>
#include <stdint.h>
#include "driver/twai.h"
#define CAN_500K 8
void can_enable(void);
void can_disable(void);
void can_set_silent(uint8_t flag);
void can_set_bitrate(uint8_t rate);
bool can_filter_narrowed(void);
uint32_t can_rx_queue_len(void);
uint8_t can_is_silent(void);
bool can_is_enabled(void);
void can_flush_rx(void);
#endif
