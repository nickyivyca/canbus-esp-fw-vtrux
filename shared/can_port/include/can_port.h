/*
 * vtrux/can_port: the CAN port interface both projects use (SPEC.md C1).
 *
 * A project registers, at start-up, a backend for each port index it routes
 * through here. A backend is a table of operations (C2); vtrux/twai_port is
 * one. A port the project's own code drives directly needs no backend until
 * one exists for it (C1).
 *
 * The stock WiCAN code's CAN calls, declared in can.h, are this component's,
 * on port 0 (C3), with the owner's refusal of C4 and the teardown of C5. They
 * are in can_stock.c.
 */
#ifndef CAN_PORT_H
#define CAN_PORT_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "driver/twai.h"

#ifdef __cplusplus
extern "C" {
#endif

/* How many port indices a project can register: a board's CAN ports. */
#define CAN_PORT_MAX    2

/*
 * The controller modes a backend may offer (twai_port T2). A backend that
 * cannot do one refuses it with ESP_ERR_NOT_SUPPORTED.
 *   NORMAL            on the bus: acknowledges, transmits, can send errors;
 *   LISTEN_ONLY       receives only: no ACK, no error frames, no transmit;
 *   NO_ACK_SELF_TEST  transmits without needing an ACK and receives its own
 *                     frames: a node alone on a bench bus.
 */
typedef enum {
    CAN_PORT_MODE_NORMAL = 0,
    CAN_PORT_MODE_LISTEN_ONLY,
    CAN_PORT_MODE_NO_ACK_SELF_TEST,
} can_port_mode_t;

/* The controller's state as a status read gives it. */
typedef enum {
    CAN_PORT_STATE_DOWN = 0,    /* not brought up, or torn down */
    CAN_PORT_STATE_STOPPED,     /* up, not on the bus */
    CAN_PORT_STATE_RUNNING,
    CAN_PORT_STATE_BUS_OFF,
    CAN_PORT_STATE_RECOVERING,
} can_port_state_t;

/*
 * One status read (twai_port T7, C2). The levels are as the read found them;
 * every count is monotonic for the life of the program, carried across a
 * teardown and a bring-up as deltas, so none goes backwards. A project that
 * reports a figure per bring-up subtracts the counts it took at its own
 * bring-up.
 */
typedef struct {
    /* Levels. */
    can_port_state_t state;
    uint32_t tx_pending;        /* frames to transmit, the one in flight included */
    uint32_t rx_waiting;        /* frames received and not yet taken */
    uint32_t tec;               /* transmit error counter (0 in listen-only) */
    uint32_t rec;               /* receive error counter (0 in listen-only) */
    uint32_t rx_queue_len;      /* the receive queue's depth the port brings up with */
    bool bus_off;               /* inside a bus-off episode */
    /* Counts. */
    uint32_t rx_frames;         /* frames handed to a receiver */
    uint32_t tx_frames;         /* sends the port queued */
    /*
     * Frames lost on receive, however they were lost: rx_lost is the total,
     * and the two below are its parts for a backend that can tell them apart.
     * rx_lost_is_bound: the backend cannot count a loss per frame, so rx_lost
     * is a lower bound (one per occurrence), not a tally (C2).
     */
    uint32_t rx_lost;
    uint32_t rx_lost_queue_full;    /* the receive queue was full */
    uint32_t rx_lost_overrun;       /* the controller's FIFO overran */
    bool rx_lost_is_bound;
    uint32_t tx_dropped;        /* sends refused with the TX queue full */
    uint32_t tx_refused;        /* sends refused for any other reason (C2) */
    uint32_t tx_failed;         /* transmissions the controller reported failed */
    uint32_t bus_errors;
    uint32_t arb_lost;          /* arbitrations lost */
    uint32_t bus_off_entries;   /* once per episode */
    uint32_t tx_discarded_bus_off;  /* queued frames bus-off recovery discarded */
    uint32_t tx_discarded_teardown; /* TX queue at a teardown (twai_port T4) */
    uint32_t rx_discarded_teardown; /* RX queue at a teardown */
    uint32_t rx_high_water;     /* the most frames seen waiting at once */
    uint32_t bring_ups;         /* successful bring-ups so far */
} can_port_status_t;

/*
 * The settings the stock calls (C3) give port 0 at each can_enable(): the
 * timing from the stock rate-code table, the mode from can_set_silent(), the
 * filter from can_set_filter() and can_set_mask() as one single filter. A
 * backend applies them at its next bring-up.
 */
typedef struct {
    twai_timing_config_t timing;
    can_port_mode_t mode;
    uint32_t acceptance_code;
    uint32_t acceptance_mask;
    bool single_filter;
} can_port_settings_t;

/*
 * A backend (C1, C2). Every operation takes the context it was registered
 * with. All but receive and send must be safe to call while the port is down.
 */
typedef struct {
    const char *name;
    /* Bring up and tear down (twai_port T3, T4). Teardown runs the backend's
     * pre-teardown hook first, every time (C5). */
    esp_err_t (*up)(void *ctx);
    esp_err_t (*down)(void *ctx);
    bool (*is_up)(void *ctx);
    esp_err_t (*set_mode)(void *ctx, can_port_mode_t mode);
    /* Apply the stock settings at the next bring-up. Refused while up. */
    esp_err_t (*configure)(void *ctx, const can_port_settings_t *s);
    /* Take one frame, waiting up to `wait` for one (0: only if waiting). */
    esp_err_t (*receive)(void *ctx, twai_message_t *out, TickType_t wait);
    /* Enqueue only, never wait for the wire; wait up to `wait` for queue
     * space (0: enqueue or fail). Every refusal is counted. */
    esp_err_t (*send)(void *ctx, const twai_message_t *m, TickType_t wait);
    /* Non-blocking, on every pass of the project's loop. */
    void (*service)(void *ctx);
    esp_err_t (*status)(void *ctx, can_port_status_t *out);
    /* Discard the frames received and not yet taken. */
    void (*flush_rx)(void *ctx);
} can_port_ops_t;

/*
 * Register `ops` for `port`, once, at start-up, before anything calls the
 * port. ESP_ERR_INVALID_ARG for a bad index or table, ESP_ERR_INVALID_STATE
 * if the port already has one.
 */
esp_err_t can_port_register(int port, const can_port_ops_t *ops, void *ctx);

/* True once `port` has a backend. */
bool can_port_registered(int port);

/*
 * The operations of C2, on a port by index. A port with no backend refuses
 * with ESP_ERR_INVALID_STATE (send and receive) or does nothing.
 */
esp_err_t can_port_up(int port);
esp_err_t can_port_down(int port);
bool can_port_is_up(int port);
esp_err_t can_port_set_mode(int port, can_port_mode_t mode);
esp_err_t can_port_receive(int port, twai_message_t *out, TickType_t wait);
esp_err_t can_port_send(int port, const twai_message_t *m, TickType_t wait);
void can_port_service(int port);
esp_err_t can_port_status(int port, can_port_status_t *out);
const char *can_port_name(int port);

/*
 * THE OWNER'S REFUSAL (C4). While `owns_bus` returns true, the stock
 * can_send() refuses: it calls `refused` once per refused send, logs at most
 * once every 5 s and returns ESP_ERR_INVALID_STATE. can_port_send(), the
 * owner's own path, is not touched. Register once, at start-up; NULL for
 * either removes it.
 */
void can_port_register_owner(bool (*owns_bus)(void), void (*refused)(void));

#ifdef __cplusplus
}
#endif

#endif
