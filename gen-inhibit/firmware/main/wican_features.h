/*
 * STOCK FEATURES, OFF IN THIS PRODUCT (spec 3.2 and 12.3 item 3, user
 * 2026-10-16).
 *
 * main/CMakeLists.txt defines each switch to 1, the shared code's default, or
 * to 0, on every file of the component; the top-level CMakeLists.txt sets all
 * four to 0 for gen-inhibit. With a switch at 0 its feature's source file is
 * not in the build, and its call sites elsewhere are under #if on the same
 * switch, so none of its code is compiled.
 *
 * A file that tests a switch includes this header, so a build that left a
 * definition out fails here. Without it `#if WICAN_FEATURE_MQTT` would read an
 * undefined switch as 0 and turn the feature off without a word.
 */
#ifndef WICAN_FEATURES_H
#define WICAN_FEATURES_H

#if !defined(WICAN_FEATURE_MQTT) || !defined(WICAN_FEATURE_ELM327) \
    || !defined(WICAN_FEATURE_AUTOPID) || !defined(WICAN_FEATURE_REALDASH)
#error "WICAN_FEATURE_MQTT, _ELM327, _AUTOPID and _REALDASH must each be defined to 0 or 1 (main/CMakeLists.txt)"
#endif

/*
 * AutoPID drives the ELM327 command path (elm327_process_cmd(), its ATSP
 * commands included) and publishes through mqtt_publish(), so it cannot be
 * built without either.
 */
#if WICAN_FEATURE_AUTOPID && !(WICAN_FEATURE_ELM327 && WICAN_FEATURE_MQTT)
#error "WICAN_FEATURE_AUTOPID needs WICAN_FEATURE_ELM327 and WICAN_FEATURE_MQTT"
#endif

#endif
