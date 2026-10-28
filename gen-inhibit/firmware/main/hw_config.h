 /* This file is part of the WiCAN project.
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
#ifndef GW_CONFIG_h
#define GW_CONFIG_h

#include "esp_err.h"

/* THE BOARD IS THE DEPENDENCY (shared/boards SPEC B1, B2 and B5; port step
 * 1a). Stock WiCAN chose its pins and per-model values here by HARDWARE_VER,
 * set in the top-level CMakeLists.txt. The project now names its board by its
 * one dependency under shared/boards/, so HARDWARE_VER is gone:
 *   - the pins are in the board's board_pins.h, which only the stock files on
 *     the project's R30 list include (shared/tools SPEC R30);
 *   - the per-model values, FS_MOUNT_POINT and the hotspot's address, are
 *     macros in the board's board.h, included here so that stock code that
 *     reads FS_MOUNT_POINT through this file builds unchanged. */
#include "board.h"

/* Stock's model numbers, which project_hardware_rev (ver.h) holds at run time.
 * They were -D definitions from the top-level CMakeLists.txt. */
#define WICAN_V210                  1
#define WICAN_V300                  2
#define WICAN_USB_V100              3
#define WICAN_PRO                   4

esp_err_t hw_config_get_device_id(char *uid);

#endif
