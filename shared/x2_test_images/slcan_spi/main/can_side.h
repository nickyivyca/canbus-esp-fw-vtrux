/*
 * can_side.h -- slcan_spi's CAN side: the MCP2515 on CAN2, with its own
 * task, knowing nothing of the host link (x2_test_images SPEC T15).
 * NOT FOR VEHICLE (SPEC T14).
 *
 * Silent until opened (SPEC T8a): can_side_init() resets the MCP2515 into
 * configuration mode, where it neither transmits nor ACKs; can_side_close()
 * aborts what is pending, waits for the frame on the wire to end, and
 * resets the chip again.
 */
#ifndef CAN_SIDE_H
#define CAN_SIDE_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "can_frame.h"

/* Called from the CAN side's task. */
typedef struct {
    void (*on_frame)(const can_frame_t *f);
    /* state: 'a' error-active, 'w' warning, 'p' error-passive, 'b' bus-off */
    void (*on_state)(char state, uint8_t rec, uint8_t tec);
    /* A receive overrun in the MCP2515: at least one frame lost. */
    void (*on_rx_overrun)(void);
} can_side_cb_t;

typedef struct {
    uint32_t tx_ok;         /* frames the MCP2515 sent (TX0IF) */
    uint32_t tx_queued;     /* frames accepted into the transmit queue */
    uint32_t tx_full;       /* frames refused: transmit queue full */
    uint32_t tx_flushed;    /* frames still queued at a close */
    uint32_t rx;            /* frames received */
    uint32_t rx_overrun;    /* MCP2515 receive overruns */
} can_side_stats_t;

/* Attaches the MCP2515 on the board's port, resets it into configuration
 * mode and starts the task. *canstat gets CANSTAT read after the reset
 * (0x80: configuration mode). */
esp_err_t can_side_init(int port, const can_side_cb_t *cb, uint8_t *canstat);

/* 500 kbit/s only (SPEC T12). Returns 0 once the MCP2515 reports the
 * mode. */
int  can_side_open(bool listen_only);
void can_side_close(void);

/* Queues a frame; 0 if queued, -1 if the queue is full or the side is not
 * open in normal mode. */
int  can_side_send(const can_frame_t *f);

void can_side_get_stats(can_side_stats_t *out);

#endif
