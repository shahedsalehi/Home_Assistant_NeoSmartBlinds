import asyncio
import logging

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_HOST, CONF_NAME

from .const import (
    DOMAIN,
    CONF_DEVICE,
    CONF_CLOSE_TIME,
    CONF_ID,
    CONF_PROTOCOL,
    CONF_PORT,
    CONF_RAIL,
    CONF_PERCENT_SUPPORT,
    CONF_MOTOR_CODE,
    CONF_START_POSITION,
    CONF_PARENT,
    CONF_TILT_SUPPORT,
    CONF_DEBUG,
    CONF_COMMAND_BACKOFF,
    CONF_COMMAND_AGGREGATION,
    CONF_IO_TIMEOUT,
    CONF_RETRY_COUNT,
    CONF_RETRY_DELAY,
    DEFAULT_IO_TIMEOUT,
    DEFAULT_COMMAND_BACKOFF,
    DEFAULT_COMMAND_AGGREGATION_PERIOD,
    DEFAULT_RETRY_COUNT,
    DEFAULT_RETRY_DELAY,
)

_LOGGER = logging.getLogger(__name__)

class NeoSmartBlindsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NeoSmartBlinds."""

    VERSION = 1

    async def _async_validate_input(self, data):
        host = data[CONF_HOST]
        protocol = data.get(CONF_PROTOCOL, "http")
        port = int(data.get(CONF_PORT, 8838))
        hub_id = data.get(CONF_ID, "")
        timeout = int(data.get(CONF_IO_TIMEOUT, DEFAULT_IO_TIMEOUT))

        try:
            if protocol == "tcp":
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=timeout
                )
                writer.close()
                await writer.wait_closed()
            else:
                url = f"http://{host}:{port}/neo/v1/transmit"
                params = {"id": hub_id, "command": "000.000-00-sp", "hash": "0000000"}
                timeout_cfg = aiohttp.ClientTimeout(total=timeout)
                async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                    async with session.get(url, params=params) as response:
                        if response.status >= 500:
                            raise RuntimeError("controller_error")
        except Exception as exc:
            _LOGGER.debug("Connection validation failed: %s", exc)
            raise

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        def get_default(key, fallback=""):
            entries = self._async_current_entries()
            if not entries:
                return fallback
            entry = entries[0]
            return entry.options.get(key, entry.data.get(key, fallback))

        if user_input is not None:
            try:
                await self._async_validate_input(user_input)
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        data_schema = vol.Schema({
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_HOST, default=get_default(CONF_HOST)): str,
            vol.Required(CONF_ID, default=get_default(CONF_ID)): str,
            vol.Required(CONF_DEVICE): str,
            vol.Optional(CONF_CLOSE_TIME, default=20): int,
            vol.Optional(CONF_PROTOCOL, default="http"): vol.In(["http", "tcp"]),
            vol.Optional(CONF_PORT, default=8838): int,
            vol.Optional(CONF_RAIL, default=1): vol.In([1, 2, 3]),
            vol.Optional(CONF_PERCENT_SUPPORT, default=0): vol.In([0, 1, 2]),
            vol.Optional(CONF_MOTOR_CODE, default=""): str,
            vol.Optional(CONF_START_POSITION, default=50): int,
            vol.Optional(CONF_PARENT, default=""): str,
            vol.Optional(CONF_TILT_SUPPORT, default=True): bool,
            vol.Optional(CONF_IO_TIMEOUT, default=DEFAULT_IO_TIMEOUT): int,
            vol.Optional(CONF_COMMAND_BACKOFF, default=DEFAULT_COMMAND_BACKOFF): vol.Coerce(float),
            vol.Optional(CONF_COMMAND_AGGREGATION, default=DEFAULT_COMMAND_AGGREGATION_PERIOD): vol.Coerce(float),
            vol.Optional(CONF_RETRY_COUNT, default=DEFAULT_RETRY_COUNT): int,
            vol.Optional(CONF_RETRY_DELAY, default=DEFAULT_RETRY_DELAY): vol.Coerce(float),
            vol.Optional(CONF_DEBUG, default=False): bool,
        })

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return NeoSmartBlindsOptionsFlowHandler()


class NeoSmartBlindsOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            try:
                await NeoSmartBlindsConfigFlow._async_validate_input(self, user_input)
            except Exception:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._build_options_schema(),
                    errors={"base": "cannot_connect"},
                )

            new_name = user_input.get(CONF_NAME)
            if new_name and new_name != self.config_entry.title:
                self.hass.config_entries.async_update_entry(self.config_entry, title=new_name)
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="init", data_schema=self._build_options_schema())

    def _build_options_schema(self):
        options = self.config_entry.options
        data = self.config_entry.data

        def safe_int(val, default):
            if val is None or val == "":
                return default
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        def safe_float(val, default):
            if val is None or val == "":
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        def safe_str(val, default=""):
            if val is None or val == "":
                return default
            return str(val)

        return vol.Schema({
            vol.Optional(CONF_NAME, default=safe_str(options.get(CONF_NAME, data.get(CONF_NAME)), "")): str,
            vol.Optional(CONF_HOST, default=safe_str(options.get(CONF_HOST, data.get(CONF_HOST)), "")): str,
            vol.Optional(CONF_ID, default=safe_str(options.get(CONF_ID, data.get(CONF_ID)), "")): str,
            vol.Optional(CONF_DEVICE, default=safe_str(options.get(CONF_DEVICE, data.get(CONF_DEVICE)), "")): str,
            vol.Optional(CONF_CLOSE_TIME, default=safe_int(options.get(CONF_CLOSE_TIME, data.get(CONF_CLOSE_TIME)), 20)): int,
            vol.Optional(CONF_PROTOCOL, default=safe_str(options.get(CONF_PROTOCOL, data.get(CONF_PROTOCOL, "http")), "http")): vol.In(["http", "tcp"]),
            vol.Optional(CONF_PORT, default=safe_int(options.get(CONF_PORT, data.get(CONF_PORT)), 8838)): int,
            vol.Optional(CONF_RAIL, default=safe_int(options.get(CONF_RAIL, data.get(CONF_RAIL)), 1)): vol.In([1, 2, 3]),
            vol.Optional(CONF_PERCENT_SUPPORT, default=safe_int(options.get(CONF_PERCENT_SUPPORT, data.get(CONF_PERCENT_SUPPORT)), 0)): vol.In([0, 1, 2]),
            vol.Optional(CONF_MOTOR_CODE, default=safe_str(options.get(CONF_MOTOR_CODE, data.get(CONF_MOTOR_CODE)), "")): str,
            vol.Optional(CONF_START_POSITION, default=safe_int(options.get(CONF_START_POSITION, data.get(CONF_START_POSITION)), 50)): int,
            vol.Optional(CONF_PARENT, default=safe_str(options.get(CONF_PARENT, data.get(CONF_PARENT)), "")): str,
            vol.Optional(CONF_TILT_SUPPORT, default=bool(options.get(CONF_TILT_SUPPORT, data.get(CONF_TILT_SUPPORT, True)))): bool,
            vol.Optional(CONF_IO_TIMEOUT, default=safe_int(options.get(CONF_IO_TIMEOUT, data.get(CONF_IO_TIMEOUT)), DEFAULT_IO_TIMEOUT)): int,
            vol.Optional(CONF_COMMAND_BACKOFF, default=safe_float(options.get(CONF_COMMAND_BACKOFF, data.get(CONF_COMMAND_BACKOFF)), DEFAULT_COMMAND_BACKOFF)): vol.Coerce(float),
            vol.Optional(CONF_COMMAND_AGGREGATION, default=safe_float(options.get(CONF_COMMAND_AGGREGATION, data.get(CONF_COMMAND_AGGREGATION)), DEFAULT_COMMAND_AGGREGATION_PERIOD)): vol.Coerce(float),
            vol.Optional(CONF_RETRY_COUNT, default=safe_int(options.get(CONF_RETRY_COUNT, data.get(CONF_RETRY_COUNT)), DEFAULT_RETRY_COUNT)): int,
            vol.Optional(CONF_RETRY_DELAY, default=safe_float(options.get(CONF_RETRY_DELAY, data.get(CONF_RETRY_DELAY)), DEFAULT_RETRY_DELAY)): vol.Coerce(float),
            vol.Optional(CONF_DEBUG, default=bool(options.get(CONF_DEBUG, data.get(CONF_DEBUG, False)))): bool,
        })
