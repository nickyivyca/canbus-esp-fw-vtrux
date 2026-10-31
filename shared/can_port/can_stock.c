/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * THE STOCK CAN CALLS, ON PORT 0 (can_port SPEC.md C3, C4, C5; shared/wican
 * W3). Made at port step 1b from gen-inhibit's main/can.c (gen-inhibit/main
 * 4020bc8, last changed at c48e05b), with today's behaviour: what the stock
 * code sees through can.h is unchanged. What moved out:
 *
 *   - the driver calls, into port 0's backend (vtrux/twai_port for both
 *     projects): the install, start and teardown, the board's pins and its
 *     standby line, and the receive queue's depth, which is now the project's
 *     setting (twai_port T2);
 *   - gen-inhibit's three calls into its own code, which became hooks: the
 *     owner's refusal here (C4), the worker quiesce as the backend's
 *     pre-teardown hook (C5, twai_port T2) and the accept-all forcing as its
 *     install hook (twai_port T2).
 *
 * The rate-code table stays here: the stock code passes CAN_5K to CAN_1000K,
 * and the table that turns them into bit timing is the stock layer's (C3,
 * user, 2026-10-31).
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/timers.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_timer.h"
#include <string.h>
#include "can.h"
#include "can_port.h"
#include "can_port_priv.h"

/* The stock calls are port 0's (C3). */
#define CAN_STOCK_PORT	0

static EventGroupHandle_t s_can_event_group = NULL;
#define CAN_ENABLE_BIT 		BIT0

#define TAG 		__func__
enum bus_state
{
    OFF_BUS = 0,
    ON_BUS = 1,
	END_BUS
};
static const twai_timing_config_t twai_timing_config[] = {
	{.brp = 800, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 400, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 200, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 128, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false},
	{.brp = 80, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 40, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 32, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 16, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 8, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false},
	{.brp = 4, .tseg_1 = 16, .tseg_2 = 8, .sjw = 3, .triple_sampling = false},
	{.brp = 4, .tseg_1 = 15, .tseg_2 = 4, .sjw = 3, .triple_sampling = false}
};

static TimerHandle_t xCAN_EN_Timer;
static uint8_t datarate = CAN_500K;
static can_cfg_t can_cfg = {.bus_state = END_BUS, .auto_bitrate = 0, .mask = 0xFFFFFFFF, .filter = 0};

//block tx/rx
void can_block(void)
{
	xEventGroupClearBits(s_can_event_group, CAN_ENABLE_BIT);

	if( xTimerIsTimerActive( xCAN_EN_Timer ) != pdFALSE )
	{
		xTimerReset( xCAN_EN_Timer, 0 );
		xTimerStop(xCAN_EN_Timer, 0);
	}
	vTaskDelay(pdMS_TO_TICKS(1));//wait for rx to finish
}
//unblock tx/rx
void can_unblock(void)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	if( xTimerIsTimerActive( xCAN_EN_Timer ) == pdFALSE )
	{
		xTimerStart( xCAN_EN_Timer, 0 );
	}
	else
	{
		xTimerReset( xCAN_EN_Timer, 0 );
	}
}


/*
 * THE REPORTING FORM OF THE STOCK ENABLE (C3): can_enable() without the
 * restart. A failed configure or bring-up returns its error and leaves the bus
 * off, whatever the backend's install-failure choice (twai_port T2); ON_BUS
 * only on success. For a project's own bring-up, which refuses rather than
 * restarts (gen-inhibit spec 3.3). ESP_OK if already on.
 */
esp_err_t can_port_stock_enable(void)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return ESP_OK;
	}

	/*
	 * The stock settings, applied by the backend at the bring-up below. The
	 * filter is the stock one as configured: a project that must not run on a
	 * narrowed filter replaces it in its install hook (twai_port T2;
	 * gen-inhibit spec 5.1 item 2), which can.c did here until 1b. Loopback
	 * is recorded by can_set_loopback() and, as before, not applied.
	 */
	can_port_settings_t s = {
		.timing = twai_timing_config[datarate],
		.mode = can_cfg.silent ? CAN_PORT_MODE_LISTEN_ONLY : CAN_PORT_MODE_NORMAL,
		.acceptance_code = can_cfg.filter,
		.acceptance_mask = can_cfg.mask,
		.single_filter = true,
	};
	esp_err_t err = can_port_configure(CAN_STOCK_PORT, &s);
	if(err == ESP_OK)
	{
		/*
		 * The backend's bring-up: the install, the start, the alerts and the
		 * transceiver out of standby. A backend set to abort on an install
		 * failure (twai_port T2) does not return from a failed one.
		 */
		err = can_port_up(CAN_STOCK_PORT);
	}
	if(err != ESP_OK)
	{
		ESP_LOGE(TAG, "port 0 would not come up: %s", esp_err_to_name(err));
		return err;
	}

	can_port_flush_rx(CAN_STOCK_PORT);
	can_unblock();
	can_cfg.bus_state = ON_BUS;
	return ESP_OK;
}

/*
 * The stock enable restarts on any failed configure or bring-up, as can.c's
 * ESP_ERROR_CHECK on the install and the start did (C3, today's behaviour),
 * whatever the backend's install-failure choice: a backend set to report
 * returns here, and the restart is this one.
 */
void can_enable(void)
{
	ESP_ERROR_CHECK(can_port_stock_enable());
}

void can_disable(void)
{
	if(can_cfg.bus_state == OFF_BUS)
	{
		return;
	}

	const uint8_t was = can_cfg.bus_state;
	if(was == ON_BUS)
	{
		can_block();
	}
	/*
	 * C5: the teardown is the backend's, and runs its pre-teardown hook first,
	 * every time, so a project's worker blocked in a receive is out of the
	 * driver before it is uninstalled. With the port already down only the
	 * hook runs.
	 *
	 * The port reports itself down only once the uninstall has succeeded
	 * (twai_port T3), and so does this. Refused -- the controller is
	 * recovering from bus-off -- the bus stays on and the stock tasks are let
	 * through again. can.c marked it off whatever happened.
	 */
	const esp_err_t err = can_port_down(CAN_STOCK_PORT);
	if(was == ON_BUS)
	{
		if(err == ESP_OK)
		{
			can_cfg.bus_state = OFF_BUS;
		}
		else
		{
			ESP_LOGE(TAG, "port 0 teardown refused: %s", esp_err_to_name(err));
			can_cfg.bus_state = OFF_BUS;	/* lets can_unblock() through */
			can_unblock();
			can_cfg.bus_state = ON_BUS;
		}
	}
}

void can_set_silent(uint8_t flag)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	can_cfg.silent = flag;
}
void can_set_loopback(uint8_t flag)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	can_cfg.loopback = flag;
}

uint8_t can_is_silent(void)
{
	return can_cfg.silent;
}
void can_set_auto_retransmit(uint8_t flag)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}
}

void can_set_filter(uint32_t f)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}

	can_cfg.filter = f;
}

void can_set_mask(uint32_t m)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}
	can_cfg.mask = m;
}

/*
 * Is the configured acceptance filter anything other than accept-all? Mask
 * 0xFFFFFFFF with code 0 is accept-all: the TWAI mask is dont-care-bits-set.
 */
bool can_filter_narrowed(void)
{
	return can_cfg.filter != 0 || can_cfg.mask != 0xFFFFFFFF;
}

/* The receive queue's depth port 0 brings up with: the project's setting. */
uint32_t can_rx_queue_len(void)
{
	can_port_status_t st;
	if(can_port_status(CAN_STOCK_PORT, &st) != ESP_OK)
	{
		return 0;
	}
	return st.rx_queue_len;
}

void can_set_bitrate(uint8_t rate)
{
	if(can_cfg.bus_state == ON_BUS || can_cfg.auto_bitrate)
	{
		return;
	}
	datarate = rate;
}
uint8_t can_get_bitrate(void)
{
	return datarate;
}
static void vCAN_EN_Callback( TimerHandle_t xTimer )
{
	xEventGroupSetBits(s_can_event_group, CAN_ENABLE_BIT);
}
void can_init(uint8_t bitrate)
{
	if(s_can_event_group == NULL)
	{
		s_can_event_group = xEventGroupCreate();
		xCAN_EN_Timer= xTimerCreate
						   ( /* Just a text name, not used by the RTOS
							 kernel. */
							 "CANTimer",
							 /* The timer period in ticks, must be
							 greater than 0. */
							 pdMS_TO_TICKS(10),
							 /* The timers will auto-reload themselves
							 when they expire. */
							 pdFALSE,
							 /* The ID is used to store a count of the
							 number of times the timer has expired, which
							 is initialised to 0. */
							 ( void * ) 0,
							 /* Each timer calls the same callback when
							 it expires. */
							 vCAN_EN_Callback
						   );
	}
	if( xTimerIsTimerActive( xCAN_EN_Timer ) != pdFALSE )
	{
		xTimerStop( xCAN_EN_Timer, 0 );
	}

	can_cfg.auto_bitrate = 0;

	if(bitrate == CAN_AUTO)
	{
		bitrate = CAN_500K;
	}
}


esp_err_t can_receive(twai_message_t *message, TickType_t ticks_to_wait)
{
	xEventGroupWaitBits(s_can_event_group,
							CAN_ENABLE_BIT,
							pdFALSE,
							pdFALSE,
							portMAX_DELAY);

	return can_port_receive(CAN_STOCK_PORT, message, ticks_to_wait);
}

esp_err_t can_send(twai_message_t *message, TickType_t ticks_to_wait)
{
	/*
	 * C4, THE OWNER'S REFUSAL. While the project's owner hook says the bus is
	 * owned, no stock path transmits: the refusal is counted through the
	 * project's hook, logged at most once every 5 s, and returns an error. The
	 * owner's own frames go through can_port_send(), which this does not touch.
	 *
	 * For gen-inhibit this is also what its spec 7 trip 7 rests on: its
	 * inhibit frame is judged complete when the controller's queue is empty,
	 * which proves its own frame went out only because nothing else can be
	 * queued behind it (gen-inhibit spec 3.2; can_port VALIDATION.md C4).
	 */
	if(can_port_owner_says_owned())
	{
		can_port_owner_refused();

		static int64_t s_last_refusal_log;
		const int64_t now_us = esp_timer_get_time();
		if(now_us - s_last_refusal_log > 5000000 || s_last_refusal_log == 0)
		{
			s_last_refusal_log = now_us;
			ESP_LOGW(TAG, "can_send refused: the bus is owned (can_port C4)");
		}
		return ESP_ERR_INVALID_STATE;
	}

	EventBits_t uxBits = xEventGroupGetBits(s_can_event_group);

	if(uxBits & CAN_ENABLE_BIT)
	{
		return can_port_send(CAN_STOCK_PORT, message, ticks_to_wait);
	}
	else return ESP_ERR_INVALID_STATE;
}

bool can_is_enabled(void)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return true;
	}
	else return false;
}

void can_flush_rx(void)
{
    if (can_cfg.bus_state == ON_BUS)
	{
        can_port_flush_rx(CAN_STOCK_PORT);
    }
}


/* 0 when the port will not say, where can.c returned an unset value. */
uint32_t can_msgs_to_rx(void)
{
	can_port_status_t st;

	if(can_port_status(CAN_STOCK_PORT, &st) != ESP_OK)
	{
		return 0;
	}

	return st.rx_waiting;
}
