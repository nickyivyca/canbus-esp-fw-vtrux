/*
 * vtrux/can_port: the backend table per port index (SPEC.md C1) and the
 * operations of C2 on a port by index. The stock calls are in can_stock.c.
 */
#include "can_port.h"
#include "can_port_priv.h"

#include <stddef.h>

typedef struct {
    const can_port_ops_t *ops;
    void *ctx;
} can_port_slot_t;

static can_port_slot_t s_ports[CAN_PORT_MAX];

static bool (*s_owns_bus)(void);
static void (*s_refused)(void);

static const can_port_slot_t *slot(int port)
{
    if (port < 0 || port >= CAN_PORT_MAX || s_ports[port].ops == NULL)
    {
        return NULL;
    }
    return &s_ports[port];
}

esp_err_t can_port_register(int port, const can_port_ops_t *ops, void *ctx)
{
    if (port < 0 || port >= CAN_PORT_MAX || ops == NULL || ops->up == NULL
        || ops->down == NULL || ops->is_up == NULL || ops->set_mode == NULL
        || ops->configure == NULL || ops->receive == NULL || ops->send == NULL
        || ops->service == NULL || ops->status == NULL || ops->flush_rx == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_ports[port].ops != NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }
    s_ports[port].ctx = ctx;
    s_ports[port].ops = ops;
    return ESP_OK;
}

bool can_port_registered(int port)
{
    return slot(port) != NULL;
}

esp_err_t can_port_up(int port)
{
    const can_port_slot_t *p = slot(port);
    return p ? p->ops->up(p->ctx) : ESP_ERR_INVALID_STATE;
}

esp_err_t can_port_down(int port)
{
    const can_port_slot_t *p = slot(port);
    return p ? p->ops->down(p->ctx) : ESP_ERR_INVALID_STATE;
}

bool can_port_is_up(int port)
{
    const can_port_slot_t *p = slot(port);
    return p ? p->ops->is_up(p->ctx) : false;
}

esp_err_t can_port_set_mode(int port, can_port_mode_t mode)
{
    const can_port_slot_t *p = slot(port);
    return p ? p->ops->set_mode(p->ctx, mode) : ESP_ERR_INVALID_STATE;
}

esp_err_t can_port_receive(int port, twai_message_t *out, TickType_t wait)
{
    const can_port_slot_t *p = slot(port);
    return p ? p->ops->receive(p->ctx, out, wait) : ESP_ERR_INVALID_STATE;
}

esp_err_t can_port_send(int port, const twai_message_t *m, TickType_t wait)
{
    const can_port_slot_t *p = slot(port);
    return p ? p->ops->send(p->ctx, m, wait) : ESP_ERR_INVALID_STATE;
}

void can_port_service(int port)
{
    const can_port_slot_t *p = slot(port);
    if (p)
    {
        p->ops->service(p->ctx);
    }
}

esp_err_t can_port_status(int port, can_port_status_t *out)
{
    const can_port_slot_t *p = slot(port);
    if (out == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }
    return p ? p->ops->status(p->ctx, out) : ESP_ERR_INVALID_STATE;
}

const char *can_port_name(int port)
{
    const can_port_slot_t *p = slot(port);
    return (p && p->ops->name) ? p->ops->name : "none";
}

esp_err_t can_port_configure(int port, const can_port_settings_t *s)
{
    const can_port_slot_t *p = slot(port);
    return p ? p->ops->configure(p->ctx, s) : ESP_ERR_INVALID_STATE;
}

void can_port_flush_rx(int port)
{
    const can_port_slot_t *p = slot(port);
    if (p)
    {
        p->ops->flush_rx(p->ctx);
    }
}

void can_port_register_owner(bool (*owns_bus)(void), void (*refused)(void))
{
    s_owns_bus = owns_bus;
    s_refused = refused;
}

bool can_port_owner_says_owned(void)
{
    return s_owns_bus != NULL && s_owns_bus();
}

void can_port_owner_refused(void)
{
    if (s_refused != NULL)
    {
        s_refused();
    }
}
