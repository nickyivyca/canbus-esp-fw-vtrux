/*
 * gen-inhibit's app_main (spec 3.3, shared/wican W4; port step 1a).
 *
 * The stock WiCAN start-up is the shared component vtrux/wican's, as two
 * calls, so the order stays visible here: the hooks, then stock start-up up to
 * the CAN driver's init, then the inhibitor, then the rest of stock start-up.
 */
#include <stdint.h>

#include "can.h"
#include "config_server.h"
#include "wican.h"
#include "gen_inhibit.h"
#include "gi_wican.h"

void app_main(void)
{
	/* The bus-owner callbacks and the URI hook, before wican_init(). */
	gi_wican_register();

	wican_init();

	/*
	 * SPEC 3.2, "THE BOOT-TIME CONFIGURATION STAYS, AND ARMING COMES AFTER IT"
	 * (user, 2026-10-16). The configured bitrate and can_mode used to be
	 * stored here AFTER gen_inhibit_init(), whose auto-arm had already
	 * installed the driver with can.c's defaults; both setters then did
	 * nothing (ON_BUS). gen_inhibit_init() now stores them first and only then
	 * starts the worker and arms.
	 */
	const int8_t can_datarate = config_server_get_can_rate();
	gen_inhibit_init((can_datarate != -1) ? (uint8_t)can_datarate : CAN_500K,
	                 (config_server_get_can_mode() == CAN_NORMAL) ? 0 : 1);

	wican_start();
}
