"""The Neo Smart Blinds Component"""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, DATA_NEOSMARTBLINDS

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["cover"]

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Neo Smart Blinds component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Neo Smart Blinds from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        if not hass.config_entries.async_entries(DOMAIN):
            domain_data = hass.data.get(DATA_NEOSMARTBLINDS, {})
            session = domain_data.get("session")
            if session is not None:
                await session.close()
            hass.data.pop(DATA_NEOSMARTBLINDS, None)
    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

