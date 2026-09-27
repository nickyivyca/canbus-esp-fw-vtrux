# See the build system documentation in IDF programming guide
# for more information about component CMakeLists.txt files.
set(srcs "main.c" "comm_server.c" "config_server.c" "slcan.c" "can.c" "wifi_network.c" "wc_uart.c" "elm327.c" "mqtt.c" "sleep_mode.c" "autopid.c" "expression_parser.c" "wc_mdns.c" "wc_timer.c" "dev_status.c" "ota_health.c" "gen_inhibit.c" "gen_inhibit_core.c")
set(requires   esp_timer esp_wifi nvs_flash fatfs vfs driver esp-tls esp_adc esp_eth log app_update esp_http_server spiffs freertos mqtt json debug_logs)

# TX-ABORT MEASUREMENT BUILD, spec 5.2 item 9 / spec 12.4's C3-abort row.
#
#   idf.py -B build-txabort -DGI_INSTRUMENT_TXABORT=1 build
#
# The source is ADDED TO THE COMPONENT here rather than compiled always and
# guarded inside with #if. A guard inside a compiled file leaves the abort
# command one #define away from a shipping image; a file that is not in the
# build cannot be linked by accident. `hal` and `soc` are added for the same
# reason -- twai_ll.h and the TWAI register struct are private headers, and the
# shipping build's component graph stays exactly as it was without them.
#
# Never flash a build configured this way to the truck. E3 refuses it.
if(GI_INSTRUMENT_TXABORT)
    list(APPEND srcs "gi_txabort_probe.c")
    list(APPEND requires "hal" "soc")
endif()

idf_component_register(
    SRCS "hw_config.c" "wc_timer.c" "autopid.c" "" "${srcs}"        # list the source files of this component
    INCLUDE_DIRS        # optional, add here public include directories
    PRIV_INCLUDE_DIRS   # optional, add here private include directories
    REQUIRES   "${requires}"         # optional, list the public requirements (component names)
    PRIV_REQUIRES       # optional, list the private requirements
    EMBED_FILES "homepage.html"
)

# MEASUREMENT-ONLY INSTRUMENT, OFF UNLESS ASKED FOR EXPLICITLY.
#
#   idf.py -B build-rxq -DGI_INSTRUMENT_RXQ=1 build
#
# Enables the RX-backlog instrument in gen_inhibit.c, which measures the one
# quantity neither device timing instrument can see: how many frames were waiting
# in the driver's queue when the worker dequeued one. Build it into a SEPARATE
# directory -- ESP-IDF names the image from the commit, so an instrument build
# and a shipping build of the same commit produce the same filename, and this
# project has already been caught once by a mislabelled image.
#
# Never flash a build configured this way to the truck.
if(GI_INSTRUMENT_RXQ)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GI_INSTRUMENT_RXQ=1)
    message(STATUS "gen_inhibit: RX backlog instrument ENABLED (measurement build)")
endif()

if(GI_INSTRUMENT_TXABORT)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GI_INSTRUMENT_TXABORT=1)
    message(STATUS "gen_inhibit: TX ABORT probe ENABLED (measurement build) -- "
                   "writes the TWAI abort command register directly")
endif()

# AUTO-ARM BUILD, for spec 12.4's auto-arm row.
#
#   idf.py -B build-autoarm -DGEN_INHIBIT_AUTOARM=GEN_INHIBIT_INHIBIT build
#
# A plain `idf.py -D` sets a CMake cache variable, not a preprocessor macro,
# which is why gen_inhibit.c's own comment says the override has to come through
# target_compile_definitions. The value is an enum name from gen_inhibit.h and is
# compared at run time, not by the preprocessor, so the name works directly.
#
# This is the DEPLOYED configuration (relay power -> boot -> inhibiting, no human
# in the loop), not a measurement hack -- but it is not the current default, so it
# gets its own build directory for the same reason build-rxq does: two
# configurations of one commit produce the same image filename.
if(GEN_INHIBIT_AUTOARM)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        GEN_INHIBIT_AUTOARM_MODE=${GEN_INHIBIT_AUTOARM})
    message(STATUS "gen_inhibit: AUTO-ARM build, mode ${GEN_INHIBIT_AUTOARM}")
endif()
