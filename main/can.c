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

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <string.h>
#include "comm_server.h"
#include "esp_timer.h"
#include "lwip/sockets.h"
#include "driver/twai.h"
#include "can.h"
#include "hw_config.h"
#include "gen_inhibit.h"

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

//static EventGroupHandle_t s_can_event_group;
//
static TimerHandle_t xCAN_EN_Timer;
//static uint8_t silent = 0;
//static uint8_t auto_retransmit = 0;
static uint8_t datarate = CAN_500K;
//static uint8_t bus_state = OFF_BUS;
// static uint32_t mask = 0xFFFFFFFF;
// static uint32_t filter = 0;
static can_cfg_t can_cfg = {.bus_state = END_BUS, .auto_bitrate = 0, .mask = 0xFFFFFFFF, .filter = 0};

#define TWAI_CONFIG(tx_io_num, rx_io_num, op_mode) {.mode = op_mode, .tx_io = tx_io_num, .rx_io = rx_io_num,        \
                                                                    .clkout_io = TWAI_IO_UNUSED, .bus_off_io = TWAI_IO_UNUSED,      \
                                                                    .tx_queue_len = 100, .rx_queue_len = 100,                           \
                                                                    .alerts_enabled = TWAI_ALERT_NONE,  .clkout_divider = 0,        \
                                                                    .intr_flags = ESP_INTR_FLAG_LEVEL1}


static const twai_general_config_t g_config_normal = TWAI_GENERAL_CONFIG_DEFAULT(TX_GPIO_NUM, RX_GPIO_NUM, TWAI_MODE_NORMAL);
static const twai_general_config_t g_config_silent = TWAI_GENERAL_CONFIG_DEFAULT(TX_GPIO_NUM, RX_GPIO_NUM, TWAI_MODE_LISTEN_ONLY);
//static const twai_general_config_t g_config_no_ack = TWAI_GENERAL_CONFIG_DEFAULT(TX_GPIO_NUM, RX_GPIO_NUM, TWAI_MODE_NO_ACK);

static twai_filter_config_t f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

/*
 * SPEC 5.1 ITEM 1: the software RX queue holds at least 32 frames whenever the
 * driver is installed.
 *
 * TWAI_GENERAL_CONFIG_DEFAULT gives 5, which is ~2.2 ms of traffic at the
 * truck's measured ~2250 frames/s -- against a worst measured WiFi preemption
 * of 2.39 ms. The margin is negative, and the E4 load model shows a frame lost
 * at the shipped depth.
 *
 * 32 is ~14 ms, about 6x that preemption. THIS IS THE REMEDY THAT MAKES THE
 * MARGIN POSITIVE rather than smaller, and the reason is that WiFi/lwIP
 * preemption blocks the WORKER TASK, not the TWAI interrupt: the ISR keeps
 * moving frames from the controller into this queue while the task is off-CPU,
 * so the queue depth is the buffer that has to cover preemption x frame rate.
 * A deeper queue turns loss into lateness, and lateness past the VCM's next
 * 0x051 is already caught by section 7 trip 7.
 *
 * Measured with the E4 sweep (test/gen_inhibit_host/load_margin.py): the
 * shortest preemption that loses a frame moves from 2.5 ms at depth 5 to 20 ms
 * at depth 32 -- 8.4x the measured worst, against the spec's >= 3x requirement.
 *
 * WHAT IT DOES NOT FIX, and the reason spec 5.1 item 3 exists: while the flash
 * cache is disabled for a write, the ISR cannot run at all (the ISR is not in
 * IRAM, section 5), so nothing reaches this queue and only the controller's
 * hardware FIFO buffers. The same sweep shows the cache-stall boundary does not
 * move with depth at all.
 *
 * Cost: 32 x sizeof(twai_message_t) is well under 1 KB.
 */
#define CAN_RX_QUEUE_LEN    32

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


void can_enable(void)
{
	if(can_cfg.bus_state == ON_BUS)
	{
		return;
	}
	
	twai_timing_config_t *t_config;
	t_config = (twai_timing_config_t *)&twai_timing_config[datarate];

	f_config.acceptance_code = can_cfg.filter;
	f_config.acceptance_mask = can_cfg.mask;
	f_config.single_filter = 1;

	/*
	 * SPEC 5.1 ITEM 2: accept-all stays, and it is ASSERTED here rather than
	 * assumed.
	 *
	 * Narrowing the filter cannot close the receive-loss hazard on its own --
	 * the IDs that remain still fill a 5-deep queue in ~2.5 ms -- the C3's
	 * single/dual filter masks cannot express the core's ID set exactly, and a
	 * mask that drops one of those IDs FAILS SILENTLY: the device simply never
	 * goes live, or trips stale, for a reason no log would explain.
	 *
	 * The defaults are accept-all (mask 0xFFFFFFFF, code 0) and the only
	 * callers of can_set_filter()/can_set_mask() are in slcan.c, whose dispatch
	 * is disabled in this build -- elf_checks.py asserts slcan_parse_str is not
	 * even linked. So accept-all holds today by a chain of three other facts,
	 * any one of which could change. This makes it a property of the install
	 * instead.
	 *
	 * NOT THE PRIMARY GUARANTEE, and it never was, though this comment used to
	 * read as if it were. gen_inhibit_set_mode() calls can_enable() before
	 * gi_set_mode() records the mode, so on the arming path ownership is still
	 * false here and this block cannot fire; and an already-up driver is not
	 * reinstalled while arming, so nothing here would see the narrowing at all.
	 * gen_inhibit.c now clears it before the bus comes up and refuses to arm if
	 * it is still narrowed. What remains here covers the one case that path does
	 * not: a re-enable while already owned. Review, 2026-09-26.
	 */
	if(gen_inhibit_owns_bus() && can_filter_narrowed())
	{
		ESP_LOGE(TAG, "acceptance filter is narrowed (code 0x%08lX mask 0x%08lX) "
		              "while the inhibitor owns the bus -- forcing accept-all "
		              "per spec 5.1 item 2",
		         (unsigned long)can_cfg.filter, (unsigned long)can_cfg.mask);
		f_config.acceptance_code = 0;
		f_config.acceptance_mask = 0xFFFFFFFF;
	}

	/*
	 * A LOCAL COPY so the RX queue depth can be overridden at the point of
	 * install. The two configs stay const, and the override cannot be missed by
	 * an initialisation-order change the way a boot-time assignment could.
	 */
	twai_general_config_t g_config = can_cfg.silent ? g_config_silent
	                                                : g_config_normal;
	g_config.rx_queue_len = CAN_RX_QUEUE_LEN;

	ESP_ERROR_CHECK(twai_driver_install(&g_config, (const twai_timing_config_t *)t_config, &f_config));

	ESP_ERROR_CHECK(twai_start());
	twai_clear_receive_queue();
	can_unblock();
	can_cfg.bus_state = ON_BUS;
	gpio_set_level(CAN_STDBY_GPIO_NUM, 0);
}

void can_disable(void)
{
	if(can_cfg.bus_state == OFF_BUS)
	{
		return;
	}

	/*
	 * If gen_inhibit is armed, its worker is blocked in twai_receive(); the
	 * twai_driver_uninstall() below would free the driver under it. Force the
	 * worker out and keep it out first. No-op if gen_inhibit is idle, if this
	 * IS the worker (its own release path), or if the worker never started.
	 */
	gen_inhibit_quiesce();

	if(can_cfg.bus_state == ON_BUS)
	{
		gpio_set_level(CAN_STDBY_GPIO_NUM, 1);
		can_block();
		twai_stop();
		twai_driver_uninstall();
		can_cfg.bus_state = OFF_BUS;
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

//	auto_retransmit = flag;
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
 * Is the configured acceptance filter anything other than accept-all?
 *
 * Exists so gen_inhibit.c can clear a narrowed filter BEFORE bringing the bus
 * up, rather than relying on the install-time override below to notice. The
 * override could not fire on the arming path at all: gen_inhibit_set_mode()
 * calls can_enable() before gi_set_mode() records the mode, so
 * gen_inhibit_owns_bus() was still false inside twai_driver_install(). Found by
 * review 2026-09-26; the override's comment claimed an assertion the code did
 * not make.
 *
 * Accept-all is mask 0xFFFFFFFF with code 0 -- the TWAI mask is
 * dont-care-bits-set, so an all-ones mask accepts every ID.
 */
bool can_filter_narrowed(void)
{
	return can_cfg.filter != 0 || can_cfg.mask != 0xFFFFFFFF;
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
		// can_cfg.auto_bitrate = 1;
		// can_cfg.bus_state = OFF_BUS;
		// can_set_bitrate(CAN_100K);
	}
}


esp_err_t can_receive(twai_message_t *message, TickType_t ticks_to_wait)
{
	esp_err_t ret;
	static uint32_t rx_error;
	static uint8_t store_silent_flag = 0;
	static uint8_t bitrate_found = 1;

	xEventGroupWaitBits(s_can_event_group,
							CAN_ENABLE_BIT,
							pdFALSE,
							pdFALSE,
							portMAX_DELAY);

	// if(can_cfg.auto_bitrate)
	// {
	// 	ret = twai_receive(message, 0);

	// 	if(ret == ESP_OK)
	// 	{
	// 		rx_error = 0;
	// 		bitrate_found = 1;
	// 		if(bitrate_found == 0)
	// 		{
	// 			bitrate_found = 1;
	// 			if(store_silent_flag != can_cfg.silent)
	// 			{
	// 				can_cfg.silent = store_silent_flag;
	// 				can_disable();
	// 				can_enable();
	// 			}
	// 		}
	// 	}
	// 	else
	// 	{
	// 		rx_error++;
	// 		if(bitrate_found == 1)
	// 		{
	// 			bitrate_found = 0;
	// 			store_silent_flag = can_cfg.silent;
	// 		}

	// 		if(rx_error >=120)
	// 		{
	// 			ESP_LOGW(TAG, "try differnt baudrate");
	// 			rx_error = 0;

	// 			can_disable();
	// 			can_cfg.silent = 1;
	// 			datarate++;
	// 			datarate %= (CAN_1000K+1);
	// 			can_enable();
	// 		}
	// 	}
	// 	return ret;
	// }
	// else
	{
		return twai_receive(message, ticks_to_wait);
	}
}

esp_err_t can_send(twai_message_t *message, TickType_t ticks_to_wait)
{
	/*
	 * SPEC 3.2 (review A3): while gen_inhibit owns the bus, no other path may
	 * transmit. Every caller of this function is a client path -- MQTT, the
	 * ELM327 responder, whatever else upstream adds later. gen_inhibit does
	 * NOT come through here; its worker calls twai_transmit() directly, so
	 * refusing unconditionally here cannot starve the inhibit frame.
	 *
	 * THIS IS ALSO A CORRECTNESS REQUIREMENT FOR SPEC 7 TRIP 7, not only a
	 * bus-ownership measure (raised by the reviewing session 2026-09-25).
	 * The inhibit frame is judged complete when the controller reports
	 * msgs_to_tx == 0, which proves OUR frame went out only because nothing
	 * else can be queued behind it. A frame queued here by another task while
	 * the inhibit is in flight leaves the count above zero after our frame has
	 * gone, producing a FALSE late-transmit abort -- or credits the wrong
	 * frame if they interleave. No build without this may go to the truck.
	 *
	 * Rate-limited log: a client retrying in a loop must not become the
	 * incident in the log.
	 */
	if(gen_inhibit_owns_bus())
	{
		static int64_t s_last_refusal_log;
		const int64_t now_us = esp_timer_get_time();
		if(now_us - s_last_refusal_log > 5000000 || s_last_refusal_log == 0)
		{
			s_last_refusal_log = now_us;
			ESP_LOGW(TAG, "can_send refused: gen_inhibit owns the bus (spec 3.2)");
		}
		return ESP_ERR_INVALID_STATE;
	}

//	xEventGroupWaitBits(s_can_event_group,
//							CAN_ENABLE_BIT,
//							pdFALSE,
//							pdFALSE,
//							portMAX_DELAY);
	EventBits_t uxBits = xEventGroupGetBits(s_can_event_group);

	if(uxBits & CAN_ENABLE_BIT)
	{
		return twai_transmit(message, ticks_to_wait);
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
//	EventBits_t uxBits = xEventGroupGetBits(s_can_event_group);
//	return (uxBits & CAN_ENABLE_BIT);
}

void can_flush_rx(void)
{
    if (can_cfg.bus_state == ON_BUS) 
	{
        twai_clear_receive_queue();
    }
}


uint32_t can_msgs_to_rx(void)
{
	twai_status_info_t status_info;

	twai_get_status_info(&status_info);

	return status_info.msgs_to_rx;
}
