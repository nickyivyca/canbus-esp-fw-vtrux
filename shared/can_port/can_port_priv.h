/*
 * Between can_port.c and can_stock.c only: the two operations the stock
 * calls need that the C2 interface does not give a project, and the owner
 * hooks of C4.
 */
#ifndef CAN_PORT_PRIV_H
#define CAN_PORT_PRIV_H

#include "can_port.h"

esp_err_t can_port_configure(int port, const can_port_settings_t *s);
void can_port_flush_rx(int port);

/* The owner hook says the bus is owned (false with no hook registered). */
bool can_port_owner_says_owned(void);
/* Count one refused stock send through the project's hook, if it has one. */
void can_port_owner_refused(void);

#endif
