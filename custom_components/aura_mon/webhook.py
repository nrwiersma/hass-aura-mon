"""Webhook handler for push-based ingestion of AuraMon live readings.

See WEBHOOK_INGESTION.md for the full payload shape and design rationale.
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.core import HomeAssistant

from .const import CONF_WEBHOOK_ID
from .coordinator import AuraMonConfigEntry

_LOGGER = logging.getLogger(__name__)


async def handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: web.Request
) -> web.Response:
    """Handle an incoming webhook POST from the AuraMon firmware.

    Never raises into the webhook response: malformed bodies are logged and ignored so a
    single bad payload can't break the firmware's retry loop or crash the handler.
    """
    try:
        body = await request.json()
    except ValueError:
        _LOGGER.warning("Ignoring webhook payload that isn't valid JSON")
        return web.Response(status=200)

    if not isinstance(body, dict):
        _LOGGER.warning("Ignoring webhook payload that isn't a JSON object: %r", body)
        return web.Response(status=200)

    entry: AuraMonConfigEntry | None = next(
        (
            e
            for e in hass.config_entries.async_entries()
            if e.data.get(CONF_WEBHOOK_ID) == webhook_id
        ),
        None,
    )
    if entry is None or entry.runtime_data is None:
        _LOGGER.warning("Received webhook for unknown/unset-up config entry: %s", webhook_id)
        return web.Response(status=200)

    entry.runtime_data.handle_payload(body)
    return web.Response(status=200)
