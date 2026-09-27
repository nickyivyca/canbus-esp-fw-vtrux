# See the build system documentation in IDF programming guide
# for more information about component CMakeLists.txt files.
set(srcs "main.c" "comm_server.c" "config_server.c" "slcan.c" "can.c" "wifi_network.c" "wc_uart.c" "elm327.c" "mqtt.c" "sleep_mode.c" "autopid.c" "expression_parser.c" "wc_mdns.c" "wc_timer.c" "dev_status.c" "ota_health.c" "gen_inhibit.c" "gen_inhibit_core.c")
set(requires   esp_timer esp_wifi nvs_flash fatfs vfs driver esp-tls esp_adc esp_eth log app_update esp_http_server spiffs freertos mqtt json debug_logs)
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
