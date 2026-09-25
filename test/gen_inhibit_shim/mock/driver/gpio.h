/* Mock driver/gpio.h. can.c drops the transceiver STBY line through it. */
#ifndef MOCK_GPIO_H
#define MOCK_GPIO_H
#include <stdint.h>
typedef int gpio_num_t;
int gpio_set_level(gpio_num_t pin, uint32_t level);
int gpio_get_level(gpio_num_t pin);
#endif
