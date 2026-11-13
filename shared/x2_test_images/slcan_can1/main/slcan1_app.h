/*
 * slcan1_app.h -- slcan_can1's image, split so the host test of T8a
 * (../test/test_app.c) can run it on fakes of ESP-IDF and the shared ports:
 * app_main() is slcan1_app_boot(), then a task calling slcan1_app_step()
 * forever.
 */
#ifndef SLCAN1_APP_H
#define SLCAN1_APP_H

/* The USB link up, CAN1's TWAI port set up and registered with can_port,
 * never brought up (SPEC T8a), the protocol closed. Once per boot. */
void slcan1_app_boot(void);

/* One pass: take what the host sent, waiting at most a tick, through the
 * protocol; then, only while open, the port's service and the received
 * frames drained and discarded. */
void slcan1_app_step(void);

#endif
