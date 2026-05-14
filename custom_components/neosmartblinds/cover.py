"""Support for NeoSmartBlinds covers."""
import asyncio
import aiohttp
import logging
import time

from .neo_smart_blind import NeoSmartBlind
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
import functools as ft
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import device_registry

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION
)

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
)

from .const import (
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
    DATA_NEOSMARTBLINDS,
    LEGACY_POSITIONING,
    EXPLICIT_POSITIONING,
    IMPLICIT_POSITIONING,
    ACTION_STOPPED,
    ACTION_OPENING,
    ACTION_CLOSING
)

PARALLEL_UPDATES = 0

SUPPORT_NEOSMARTBLINDS = (
    CoverEntityFeature.OPEN
    | CoverEntityFeature.CLOSE
    | CoverEntityFeature.SET_POSITION
    | CoverEntityFeature.OPEN_TILT
    | CoverEntityFeature.CLOSE_TILT
    | CoverEntityFeature.SET_TILT_POSITION
    | CoverEntityFeature.STOP
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up NeoSmartBlinds cover from a config entry."""
    # Combine data and options
    config = {**config_entry.data, **config_entry.options}
    
    cover = NeoSmartBlindsCover(
        hass,
        config.get(CONF_NAME),
        config.get(CONF_HOST),
        config.get(CONF_ID),
        config.get(CONF_DEVICE),
        int(config.get(CONF_CLOSE_TIME, 20)),
        config.get(CONF_PROTOCOL, "http"),
        int(config.get(CONF_PORT, 8838)),
        int(config.get(CONF_RAIL, 1)),
        int(config.get(CONF_PERCENT_SUPPORT, 0)),
        config.get(CONF_MOTOR_CODE, ""),
        int(config.get(CONF_START_POSITION, 50)),
        config.get(CONF_PARENT, ""),
        bool(config.get(CONF_TILT_SUPPORT, True)),
        int(config.get(CONF_IO_TIMEOUT, DEFAULT_IO_TIMEOUT)),
        float(config.get(CONF_COMMAND_BACKOFF, DEFAULT_COMMAND_BACKOFF)),
        float(config.get(CONF_COMMAND_AGGREGATION, DEFAULT_COMMAND_AGGREGATION_PERIOD)),
        int(config.get(CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT)),
        float(config.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)),
        bool(config.get(CONF_DEBUG, False)),
        config_entry.entry_id,
        )
    async_add_entities([cover])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_favorite",
        {vol.Required("favorite"): vol.In([1, 2])},
        "async_set_favorite_service",
    )
    platform.async_register_entity_service(
        "sync_position",
        {vol.Required("position"): vol.In([0, 100])},
        "async_sync_position",
    )

def compute_wait_time(larger, smaller, close_time):
    """
    Helper function to estimate how long to wait for a blind move to complete.

    The caller must determine direction and specify the larger value. The positions
    provided are %.

    make sure we are returning a positive number, otherwise the time is instant.
    """
    return abs(((larger - smaller) * close_time) / 100)

class PositioningRequest(object):
    """
    Helper class for monitoring and reacting to a blind position change
    """
    def __init__(self, target_position, starting_position, needs_stop):
        self._target_position = target_position
        self._starting_position = starting_position
        # Event to interrupt pending positioning attempts and either cancel or recalculate the delay
        self._interrupt = asyncio.Event()
        # Time at which the positioning attempt began
        self._start = time.time()
        # Active wait (will be None until the wait coroutine begins)
        self._active_wait = None
        # If interrupted to adjust the wait time, this is the new wait time
        self._adjusted_wait = None
        # Indicates whether the pending positioning attempt requires the entity to call stop once complete
        self._needs_stop = needs_stop

    @property
    def needs_stop(self):
        return self._needs_stop

    @property
    def target_position(self):
        return self._target_position

    @property
    def starting_position(self):
        return self._starting_position

    async def async_wait(self, reason, cover):
        """
        Wait on the positioning request to complete.

        Can be interrupted by adjust() or interrupt().
        """
        elapsed = 0
        while True:
            _LOGGER.info(
                '{} sleeping for {} to allow for {} to {}, elapsed={}'.format(cover.name, self._active_wait, reason,
                                                                            self._target_position, elapsed))
            await asyncio.wait_for(
                asyncio.create_task(self._interrupt.wait()), self._active_wait - elapsed
            )
            elapsed = time.time() - self._start
            if self._adjusted_wait is not None:
                # compute adjusted target position given interrupt
                self._active_wait = self._adjusted_wait
                self._adjusted_wait = None
                self._interrupt.clear()
            else:
                break
        return elapsed

    async def async_wait_for_move_up(self, cover):
        """
        Wait for the blind to move up to the target position

        Returns whether the request was interrupted and a new target position was computed.
        """
        was_interrupted = False

        self._active_wait = compute_wait_time(self._target_position, self._starting_position, cover.close_time)
        try:
            elapsed = await self.async_wait('open', cover)
            if elapsed < self._active_wait:
                self._target_position = int(
                    self._starting_position + (
                        self._target_position - self._starting_position) * elapsed / self._active_wait
                    )
                was_interrupted = True
        except asyncio.TimeoutError:
            # all done
            pass 

        return was_interrupted

    async def async_wait_for_move_down(self, cover):
        """
        Wait for the blind to move down to the target position

        Returns whether the request was interrupted and a new target position was computed.
        """
        was_interrupted = False

        self._active_wait = compute_wait_time(self._starting_position, self._target_position, cover.close_time)
        try:
            elapsed = await self.async_wait('close', cover)
            if elapsed < self._active_wait:
                self._target_position = int(
                    self._starting_position - (
                        self._starting_position - self._target_position) * elapsed / self._active_wait
                    )
                was_interrupted = True
        except asyncio.TimeoutError:
            # all done
            pass 

        return was_interrupted

    def is_moving_up(self):
        """
        Indicates whether the blind is moving up
        """
        return self._target_position > self._starting_position

    def estimate_current_position(self):
        """
        Compute an estimated position of the ongoing request based on elapsed time
        """
        # If the wait coro hasn't been awaited, this will be None. The position is simply the start.
        if not self._active_wait:
            return self._starting_position
            
        elapsed = time.time() - self._start
        if self.is_moving_up():
            return int(
                self._starting_position + (
                    self._target_position - self._starting_position) * elapsed / self._active_wait
                )            
        else:
            return int(
                self._starting_position - (
                    self._starting_position - self._target_position) * elapsed / self._active_wait
                )

    def adjust(self, target_position, cover):
        """
        Attempt to adjust the ongoing request to a new target_position.
        Return estimated current position if the ongoing request can't be adjusted, None otherwise
        """
        cur = self.estimate_current_position()
        if self.is_moving_up():
            if cur <= target_position:
                self._target_position = target_position
                self._adjusted_wait = compute_wait_time(target_position, self._starting_position, cover.close_time)
                self.interrupt()
                return
        else:
            if cur >= target_position:
                self._target_position = target_position
                self._adjusted_wait = compute_wait_time(self._starting_position, target_position, cover.close_time)
                self.interrupt()
                return
        _LOGGER.info('{} estimated position is {}, force direction change'.format(cover.name, cur))
        return cur

    def interrupt(self):
        """
        Interrupt the ongoing request
        """
        self._interrupt.set()


class NeoSmartBlindsCover(CoverEntity, RestoreEntity):
    """Representation of a NeoSmartBlinds cover."""

    def __init__(self, home_assistant, name, host, the_id, device, close_time, protocol, port, rail, percent_support,
                 motor_code, starting_position, parent_code, tilt_support, io_timeout, command_backoff,
                 command_aggregation, retry_count, retry_delay, debug_logging, entry_id):
        """Initialize the cover."""
        self.home_assistant = home_assistant
        self._name = name
        self._host = host
        self._hub_id = the_id
        self._device_code = device
        self._rail = rail
        self._debug_logging = debug_logging
        self._tilt_support = tilt_support
        self._entry_id = entry_id
        # This isn't ideal but there is no feedback from the blind / hub about position.
        self._percent_support = percent_support
        if self._percent_support > 0:
            self._current_position = starting_position
        else:
            self._current_position = 50
        self._close_time = int(close_time)
        # Used to advertise state to ha
        self._current_action = ACTION_STOPPED
        # Pending positioning request
        self._pending_positioning_command = None
        # Event used to cleanly cancel a positioning command and stop the blind
        self._stopped = None
        
        def http_session_factory(timeout):
            """
            Closure used to give the client a HTTP session that is shared by all covers.
            """
            domain_data = self.home_assistant.data.setdefault(DATA_NEOSMARTBLINDS, {})
            if "session" not in domain_data:
                t = aiohttp.ClientTimeout(total=timeout)
                domain_data["session"] = aiohttp.ClientSession(timeout=t)

            return domain_data["session"]

        self._client = NeoSmartBlind(host,
                                    the_id,
                                    device,    
                                    port,
                                    protocol,
                                    rail,
                                    motor_code,
                                    parent_code,
                                    http_session_factory,
                                    io_timeout,
                                    command_backoff,
                                    command_aggregation,
                                    retry_count,
                                    retry_delay,
                                    debug_logging)

    @property
    def close_time(self):
        """Return the close time"""
        return self._close_time

    @property
    def pending_positioning_command(self):
        """Return the pending position command"""
        return self._pending_positioning_command

    @property
    def name(self):
        """Return the name of the NeoSmartBlinds device."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique id for the entity"""
        return self._client.unique_id(DATA_NEOSMARTBLINDS)

    @property
    def device_info(self):
        """Return device information for the cover."""
        hub_identifier = f"hub_{self._hub_id}"
        blind_identifier = f"{self._hub_id}:{self._device_code}:{self._rail}"

        return DeviceInfo(
            identifiers={(DATA_NEOSMARTBLINDS, blind_identifier)},
            name=self._name,
            manufacturer="Neo Smart Blinds",
            model="Blind",
            via_device=(DATA_NEOSMARTBLINDS, hub_identifier),
            configuration_url=f"http://{self._host}",
        )

    @property
    def should_poll(self):
        """No polling needed within NeoSmartBlinds."""
        return False

    @property
    def supported_features(self):
        """Flag supported features."""
        if self._tilt_support:
            return SUPPORT_NEOSMARTBLINDS
        return SUPPORT_NEOSMARTBLINDS & ~(
            CoverEntityFeature.OPEN_TILT
            | CoverEntityFeature.CLOSE_TILT
            | CoverEntityFeature.SET_TILT_POSITION
        )

    @property
    def device_class(self):
        """Define this cover as either window/blind/awning/shutter."""
        return "blind"
        
    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self._current_position == 0

    @property
    def is_closing(self):
        """Return if the cover is closing."""
        return self._current_action == ACTION_CLOSING

    @property
    def is_opening(self):
        """Return if the cover is opening."""
        return self._current_action == ACTION_OPENING

    @property
    def current_cover_position(self):
        """Return current position of cover."""
        return self._current_position

    @property
    def current_cover_tilt_position(self):
        """Return current position of cover tilt."""
        return 50

    async def async_added_to_hass(self):
        """Complete the initialization."""
        await super().async_added_to_hass()
        if self._entry_id and self._hub_id:
            hub_identifier = f"hub_{self._hub_id}"
            dr = device_registry.async_get(self.hass)
            dr.async_get_or_create(
                config_entry_id=self._entry_id,
                identifiers={(DATA_NEOSMARTBLINDS, hub_identifier)},
                name=f"NeoSmartBlinds Hub {self._host}",
                manufacturer="Neo Smart Blinds",
                model="Controller",
                configuration_url=f"http://{self._host}",
            )
        last_state = await self.async_get_last_state()
        if self._current_position is None:
            if last_state is not None and ATTR_CURRENT_POSITION in last_state.attributes:
                self._current_position = last_state.attributes[ATTR_CURRENT_POSITION]
            else:
                self._current_position = 50

    async def async_close_cover(self, **kwargs):
        """Fully close the cover."""
        # Be pessimistic and ensure that a command is always issued. To do this, ensure
        # any pending request is stopped first
        if self._pending_positioning_command is not None:
            await self.async_stop_cover_partially()
            
        await self.async_close_cover_to(0)
        
    async def async_close_cover_to(self, target_position, move_command=None):
        """
        Close the cover to the target_position specified.
        The caller is responsible for determining that getting to the target_position requires the
        blind to close!
        If specified, move_command is a coroutine used to move the blind else a down command is issued.
        """
        # Create an event to manage clean stop for this positioning attempt
        self._stopped = asyncio.Event()
        self._pending_positioning_command = PositioningRequest(target_position, self._current_position, 
                                                               not (target_position == 0 or move_command))

        # Set the current position of the entity to the target
        self._current_position = target_position
        self._current_action = ACTION_CLOSING

        # Issue the move command
        if await self._client.async_down_command() if move_command is None else await move_command():
            _LOGGER.info('{} closing to {}'.format(self._name, target_position))
            # Put the positioning request on the ha queue to run in parallel but don't await it here (we want to continue)
            self.hass.async_create_task(self.async_cover_closed_to_position())
            # Finally, update the state to reflect that the command is in flight
            self.async_write_ha_state()
        else:
            # The request failed, just go through the motions of completion
            self.cover_change_complete(False)

    async def async_open_cover(self, **kwargs):
        """Fully open the cover."""
        # Be pessimistic and ensure that a command is always issued. To do this, ensure
        # any pending request is stopped first
        if self._pending_positioning_command is not None:
            await self.async_stop_cover_partially()

        await self.async_open_cover_to(100)

    async def async_open_cover_to(self, target_position, move_command=None):
        """
        Close the cover to the target_position specified.
        The caller is responsible for determining that getting to the target_position requires the
        blind to close!
        If specified, move_command is a coroutine used to move the blind else a down command is issued.
        """
        # Create an event to manage clean stop for this positioning attempt
        self._stopped = asyncio.Event()
        self._pending_positioning_command = PositioningRequest(target_position, self._current_position, 
                                                               not (target_position == 100 or move_command))

        # Set the current position of the entity to the target
        self._current_position = target_position
        self._current_action = ACTION_OPENING

        # Issue the move command
        if await self._client.async_up_command() if move_command is None else await move_command():
            _LOGGER.info('{} opening to {}'.format(self._name, target_position))
            # Put the positioning request on the ha queue to run in parallel but don't await it here (we want to continue)
            self.hass.async_create_task(self.async_cover_opened_to_position())
            # Finally, update the state to reflect that the command is in flight
            self.async_write_ha_state()
        else:
            # The request failed, just go through the motions of completion
            self.cover_change_complete(False)

    async def async_cover_closed_to_position(self):
        """
        Coroutine to deal with completion of positioning request down.
        """
        # Wait for the request to run to its completion
        if not await self.pending_positioning_command.async_wait_for_move_down(self):
            # If the request completed fully but needs an explicit stop to finish off, trigger it now
            if self.pending_positioning_command.needs_stop:
                await self._client.async_stop_command()
        self.cover_change_complete()

    async def async_cover_opened_to_position(self):
        # Wait for the request to run to its completion
        if not await self.pending_positioning_command.async_wait_for_move_up(self):
            # If the request completed fully but needs an explicit stop to finish off, trigger it now
            if self.pending_positioning_command.needs_stop:
                await self._client.async_stop_command()
        self.cover_change_complete()

    def cover_change_complete(self, result=True):
        """
        Manage completion of a positioning request by cleaning everything up.
        """
        # If the completion issued and awaited a stop command, defend against a situation
        # that the command was cleaned up in parallel (NB. this might be a hangover from
        # the initial async conversion where the IO itself was sync and moved to the worker
        # pool so this defence may not strictly be necessary).
        if self.pending_positioning_command is not None:
            # Update the entity state
            self._current_action = ACTION_STOPPED
            if result:
                self._current_position = self.pending_positioning_command.target_position
                _LOGGER.info('{} move done {}'.format(self._name, self._current_position))
            else:
                self._current_position = self.pending_positioning_command.starting_position
            self._pending_positioning_command = None
            # Signal to any other awaiting coroutines that the stop has completed fully
            if self._stopped is None:
                if result:
                    _LOGGER.error('{} move done but state broken'.format(self._name))
            else:
                self._stopped.set()
            # Finally, notify ha of the state change
            self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        await self.async_stop_cover_partially()
        if self.pending_positioning_command is not None:
            _LOGGER.info('{} stopped and cleaning up'.format(self._name))
            self._pending_positioning_command = None
            self._stopped = None

    async def async_stop_cover_partially(self):
        """Stop the cover."""
        _LOGGER.info('{} stop'.format(self._name))
        await self._client.async_stop_command()
        if self.pending_positioning_command is not None:
            # Interrupt any pending positioning requests
            self.pending_positioning_command.interrupt()
            # Wait for the command to stop completely
            await self._stopped.wait()
            # NB. EVERYTHING must be happening on the main event thread to guarantee that it 
            # is safe to do this here
            self._stopped = None
        else:
            # Just make sure the state is correct (though there's a consistency issue here if 
            # this isn't already the case)
            self._current_action = ACTION_STOPPED
        
    async def async_open_cover_tilt(self, **kwargs):
        await self._client.async_open_cover_tilt()
        """Open the cover tilt."""
        
    async def async_close_cover_tilt(self, **kwargs):
        await self._client.async_close_cover_tilt()
        """Close the cover tilt."""

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        pos = kwargs.get(ATTR_POSITION, kwargs.get("position"))
        if pos is None:
            _LOGGER.warning("%s missing position payload", self._name)
            return
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            _LOGGER.warning("%s invalid position payload: %s", self._name, pos)
            return

        await self.async_adjust_blind(pos)

    async def async_set_fav_position(self, pos):
        # Position doesn't resemble reality so the state is likely to get out of step
        position = await self._client.async_set_fav_position(pos)
        if isinstance(position, int):
            self._current_position = position
            self.async_write_ha_state()

    async def async_set_favorite_service(self, favorite):
        """Set favorite position using a service call."""
        await self.async_set_fav_position(50 if favorite == 1 else 51)

    async def async_sync_position(self, position):
        """Sync the assumed position without issuing a command."""
        self._current_action = ACTION_STOPPED
        self._current_position = position
        self.async_write_ha_state()

    async def async_set_cover_tilt_position(self, **kwargs):
        tilt = kwargs.get(ATTR_TILT_POSITION, kwargs.get("tilt_position"))
        if tilt is None:
            _LOGGER.warning("%s missing tilt payload", self._name)
            return
        try:
            tilt = int(tilt)
        except (TypeError, ValueError):
            _LOGGER.warning("%s invalid tilt payload: %s", self._name, tilt)
            return

        await self.async_set_fav_position(tilt)

    """Adjust the blind based on the pos value send"""
    async def async_adjust_blind(self, pos):

        """Legacy support for using position to set favorites"""
        if self._percent_support == LEGACY_POSITIONING:
            if pos == 50 or pos == 51:
                await self.async_set_fav_position(pos)
            elif pos >= 100:
                await self.async_open_cover()
            elif pos <= 0:
                await self.async_close_cover()
        else:
            """Always allow full open / close commands to get through"""

            if pos > 98:
                """
                Unable to send 100 to the API so assume anything greater then 98 is just an open command.
                Use the same logic irrespective of mode for consistency.            
                """
                pos = 100
            if pos < 2:
                """Assume anything greater less than 2 is just a close command"""
                pos = 0

            """Check for any change in position, only act if it has changed"""
            delta = 0

            """
            Work out whether the blind is already moving.
            If yes, work out whether it is moving in the right direction.
                If yes, just adjust the pending timeout.
                If no, cancel the existing timer and issue a fresh positioning command
            if not, issue a positioning command
            """
            if self._pending_positioning_command is not None:
                estimated_position = self._pending_positioning_command.adjust(pos, self)
                # The estimated position will be returned if the cover is moving in the wrong direction
                if estimated_position is not None:
                    # STOP then issue new command
                    await self.async_stop_cover_partially()
                    delta = pos - estimated_position
                elif self._percent_support == EXPLICIT_POSITIONING:
                    # just issue the new position, the wait is adjusted already
                    await self._client.async_set_position_by_percent(pos)
                # else: adjustment handled silently, leave delta at zero so no command is sent
            else:
                # New command, nothing in-flight -- compute the delta
                delta = pos - self._current_position

            if delta > 0:
                if self._percent_support == IMPLICIT_POSITIONING or pos == 100:
                    await self.async_open_cover_to(pos)
                elif self._percent_support == EXPLICIT_POSITIONING:
                    await self.async_open_cover_to(
                        pos, 
                        ft.partial(self._client.async_set_position_by_percent, pos)
                    )

            if delta < 0:
                if self._percent_support == IMPLICIT_POSITIONING or pos == 0:
                    await self.async_close_cover_to(pos)
                elif self._percent_support == EXPLICIT_POSITIONING:
                    await self.async_close_cover_to(
                        pos, 
                        ft.partial(self._client.async_set_position_by_percent, pos)
                    )
