/*
 * can_frame.h -- slcan_spi's CAN frame, shared by the CAN side and the host
 * protocol. NOT FOR VEHICLE (x2_test_images SPEC T14).
 *
 * Neither side knows the other (SPEC T15): the CAN side (can_side.c,
 * mcp2515.c) takes and gives frames in this form, and the host side
 * (slcan_proto.c, host_usb.c) turns them into text. A later network variant
 * reuses the CAN side with another host side.
 */
#ifndef CAN_FRAME_H
#define CAN_FRAME_H

#include <stdint.h>

typedef struct {
    uint32_t id;        /* 11 or 29 bits */
    uint8_t  ext;       /* 1: 29-bit identifier */
    uint8_t  rtr;       /* 1: remote frame, no data bytes */
    uint8_t  dlc;       /* 0..8 */
    uint8_t  data[8];
} can_frame_t;

#endif
