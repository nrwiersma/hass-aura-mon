"""The AuraMon integration."""
from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.components import webhook as ha_webhook
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuraMonClient
from .const import CONF_WEBHOOK_ID, DOMAIN
from .coordinator import AuraMonConfigEntry, AuraMonDataUpdateCoordinator
from .webhook import handle_webhook

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: AuraMonConfigEntry) -> bool:
    """Set up AuraMon from a config entry."""
    client = AuraMonClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    coordinator = AuraMonDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if webhook_id is None:
        # Entries created before push support was added don't have a webhook_id yet (the
        # config flow only generates one for new entries; re-adding an already-configured
        # device just aborts/updates the host, it never reaches that step - see
        # config_flow.py's async_step_user). Generate one now and let the user know.
        webhook_id = ha_webhook.async_generate_id()
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_WEBHOOK_ID: webhook_id}
        )
        webhook_url = ha_webhook.async_generate_url(hass, webhook_id)
        _LOGGER.warning("Generated new AuraMon webhook URL for %s: %s", entry.title, webhook_url)
        persistent_notification.async_create(
            hass,
            (
                f"This Aura Mon device ({entry.title}) needs a webhook configured on its "
                "firmware to start receiving live readings. Add the following URL to the "
                "device's Home Assistant uploader settings (`url`/`webhook_id`):\n\n"
                f"`{webhook_url}`"
            ),
            title="Aura Mon: webhook URL needed",
            notification_id=f"{DOMAIN}_{entry.entry_id}_webhook",
        )

    ha_webhook.async_register(hass, DOMAIN, entry.title, webhook_id, handle_webhook)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AuraMonConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        webhook_id = entry.data.get(CONF_WEBHOOK_ID)
        if webhook_id is not None:
            ha_webhook.async_unregister(hass, webhook_id)
    return unload_ok


