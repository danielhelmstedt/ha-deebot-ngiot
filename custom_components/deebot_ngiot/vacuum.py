"""Support for Ecovacs vacuums using the NGIOT endpoint."""

import asyncio
import logging
from collections.abc import Mapping
from time import monotonic
from typing import TYPE_CHECKING, Any, cast, override

from deebot_client.capabilities import Capabilities as ClientCapabilities
from deebot_client.capabilities import DeviceType
from deebot_client.device import Device
from deebot_client.events import (
    CachedMapInfoEvent,
    FanSpeedEvent,
    RoomsEvent,
    StateEvent,
)
from deebot_client.events.map import Map
from deebot_client.models import CleanAction, CleanMode, State
from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    StateVacuumEntityDescription,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify
from probatio import In, Required

from . import ConfigEntry
from .const import CONSUMABLES, FAN_SPEED_TO_FAN_MODE
from .controller import Capabilities
from .entity import Entity

_LOGGER = logging.getLogger(__name__)
_SEGMENTS_SEPARATOR = "_"

_STATE_TO_VACUUM_STATE = {
    State.IDLE: VacuumActivity.IDLE,
    State.CLEANING: VacuumActivity.CLEANING,
    State.RETURNING: VacuumActivity.RETURNING,
    State.DOCKED: VacuumActivity.DOCKED,
    State.ERROR: VacuumActivity.ERROR,
    State.PAUSED: VacuumActivity.PAUSED,
}

_ATTR_ROOMS = "rooms"
_ATTR_CONSUMABLE = "consumable"
SERVICE_RESET_CONSUMABLE = "reset_consumable"

_RETURN_TO_DOCK_REFRESH_INTERVAL = 30
_RETURN_TO_DOCK_FALLBACK_TIMEOUT = 20 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacuums."""
    controller = config_entry.runtime_data
    async_add_entities(
        Vacuum(device)
        for device in controller.devices
        if device.capabilities.device_type is DeviceType.VACUUM
    )

    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_RESET_CONSUMABLE,
        {
            Required(_ATTR_CONSUMABLE): In(
                list(CONSUMABLES)
                + [
                    api_value
                    for api_value, _ in CONSUMABLES.values()
                    if api_value not in CONSUMABLES
                ]
            )
        },
        "async_reset_consumable",
    )


class Vacuum(
    Entity[ClientCapabilities],
    StateVacuumEntity,
):
    """Vacuum."""

    _unrecorded_attributes = frozenset({_ATTR_ROOMS})

    _attr_supported_features = (
        VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.START
        | VacuumEntityFeature.STOP
    )

    entity_description = StateVacuumEntityDescription(
        key="vacuum", translation_key="vacuum", name=None, entity_category=None
    )

    def __init__(self, device: Device) -> None:
        """Initialize the vacuum."""
        super().__init__(device, device.capabilities)

        self._room_event: RoomsEvent | None = None
        self._maps: dict[str, Map] = {}
        self._return_to_dock_task: asyncio.Task[None] | None = None

        if fan_speed := self._capability.fan_speed:
            self._attr_supported_features |= VacuumEntityFeature.FAN_SPEED
            self._attr_fan_speed_list = [
                FAN_SPEED_TO_FAN_MODE[level] for level in fan_speed.types
            ]

        if self._capability.map and self._capability.clean.action.area:
            self._attr_supported_features |= VacuumEntityFeature.CLEAN_AREA

        if self._capability.play_sound:
            self._attr_supported_features |= VacuumEntityFeature.LOCATE

        if self._capability.custom:
            self._attr_supported_features |= VacuumEntityFeature.SEND_COMMAND

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_status(event: StateEvent) -> None:
            self._attr_activity = _STATE_TO_VACUUM_STATE[event.state]
            if self._attr_activity != VacuumActivity.RETURNING:
                self._cancel_return_to_dock_monitor()
            self.async_write_ha_state()

        self._subscribe(self._capability.state.event, on_status)

        if self._capability.fan_speed:

            async def on_fan_speed(event: FanSpeedEvent) -> None:
                self._attr_fan_speed = FAN_SPEED_TO_FAN_MODE[event.speed]
                self.async_write_ha_state()

            self._subscribe(self._capability.fan_speed.event, on_fan_speed)

        if map_caps := self._capability.map:

            async def on_rooms(event: RoomsEvent) -> None:
                self._room_event = event
                self._check_segments_changed()
                self.async_write_ha_state()

            self._subscribe(map_caps.rooms.event, on_rooms)

            async def on_map_info(event: CachedMapInfoEvent) -> None:
                self._maps = {map_obj.id: map_obj for map_obj in event.maps}
                self._check_segments_changed()

            self._subscribe(map_caps.cached_info.event, on_map_info)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Cancel pending work before removing the entity."""
        self._cancel_return_to_dock_monitor()
        await super().async_will_remove_from_hass()

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return entity specific state attributes."""
        rooms: dict[str, Any] = {}
        if self._room_event is None:
            return rooms

        for room in self._room_event.rooms:
            room_name = slugify(room.name)
            room_values = rooms.get(room_name)
            if room_values is None:
                rooms[room_name] = room.id
            elif isinstance(room_values, list):
                room_values.append(room.id)
            else:
                rooms[room_name] = [room_values, room.id]

        return {
            _ATTR_ROOMS: rooms,
        }

    @override
    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set fan speed."""
        if TYPE_CHECKING:
            assert self._capability.fan_speed
        speed = next(
            (
                level
                for level, mode in FAN_SPEED_TO_FAN_MODE.items()
                if mode == fan_speed
            ),
            None,
        )
        if speed is None:
            raise ValueError(f"Unsupported fan speed: {fan_speed}")
        await self._device.execute_command(self._capability.fan_speed.set(speed))

    @override
    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Set the vacuum cleaner to return to the dock."""
        await self._device.execute_command(self._capability.charge.execute())
        self._attr_activity = VacuumActivity.RETURNING
        self.async_write_ha_state()
        self._start_return_to_dock_monitor()

    @override
    async def async_pause(self) -> None:
        """Pause the vacuum cleaner."""
        await self._clean_command(CleanAction.PAUSE)

    @override
    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum cleaner."""
        self._cancel_return_to_dock_monitor()
        await self._clean_command(CleanAction.STOP)

    async def async_reset_consumable(self, consumable: str) -> None:
        """Reset the usage counter of a consumable."""
        if consumable in CONSUMABLES:
            consumable = CONSUMABLES[consumable][0]
        elif consumable not in (api_value for api_value, _ in CONSUMABLES.values()):
            raise ValueError(f"Unsupported consumable: {consumable}")
        capability = cast(Capabilities, self._capability)
        await self._device.execute_command(
            capability.reset_consumable.execute(consumable)
        )

    @override
    async def async_locate(self, **kwargs: Any) -> None:
        """Locate the vacuum cleaner."""
        if TYPE_CHECKING:
            assert self._capability.play_sound
        await self._device.execute_command(self._capability.play_sound.execute())

    @override
    async def async_start(self) -> None:
        """Start the vacuum cleaner."""
        await self._clean_command(CleanAction.START)

    async def _clean_command(self, action: CleanAction) -> None:
        await self._device.execute_command(
            self._capability.clean.action.command(action)
        )

    def _start_return_to_dock_monitor(self) -> None:
        """Poll the state while returning, as the dock event may not be pushed."""
        self._cancel_return_to_dock_monitor()
        self._return_to_dock_task = self.hass.async_create_background_task(
            self._monitor_return_to_dock(), f"{self.entity_id}_return_to_dock"
        )

    def _cancel_return_to_dock_monitor(self) -> None:
        if self._return_to_dock_task is not None:
            self._return_to_dock_task.cancel()
            self._return_to_dock_task = None

    async def _monitor_return_to_dock(self) -> None:
        started = monotonic()
        try:
            while self._attr_activity == VacuumActivity.RETURNING:
                await asyncio.sleep(_RETURN_TO_DOCK_REFRESH_INTERVAL)
                self._device.events.request_refresh(StateEvent)
                if self._attr_activity != VacuumActivity.RETURNING:
                    return

                if monotonic() - started >= _RETURN_TO_DOCK_FALLBACK_TIMEOUT:
                    _LOGGER.debug("Return-to-dock fallback marked docked")
                    self._attr_activity = VacuumActivity.DOCKED
                    self.async_write_ha_state()
                    return
        finally:
            if self._return_to_dock_task is asyncio.current_task():
                self._return_to_dock_task = None

    @callback
    def _check_segments_changed(self) -> None:
        """Check if segments have changed and create repair issue."""
        last_seen = self.last_seen_segments
        if last_seen is None:
            return

        last_seen_ids = {seg.id for seg in last_seen}
        current_ids = {seg.id for seg in self._get_segments()}

        if current_ids != last_seen_ids:
            self.async_create_segments_issue()

    def _get_segments(self) -> list[Segment]:
        """Get the segments that can be cleaned."""
        last_seen = self.last_seen_segments or []
        if self._room_event is None or not self._maps:
            # Keep the last seen segments until the map information is complete
            return last_seen

        map_id = self._room_event.map_id
        if (map_obj := self._maps.get(map_id)) is None:
            _LOGGER.warning("Map ID %s not found in available maps", map_id)
            return []

        id_prefix = f"{map_id}{_SEGMENTS_SEPARATOR}"
        other_map_ids = {
            map_obj.id
            for map_obj in self._maps.values()
            if map_obj.id != self._room_event.map_id
        }
        segments = [
            seg for seg in last_seen if _split_composite_id(seg.id)[0] in other_map_ids
        ]
        segments.extend(
            Segment(
                id=f"{id_prefix}{room.id}",
                name=room.name,
                group=map_obj.name,
            )
            for room in self._room_event.rooms
        )
        return segments

    @override
    async def async_get_segments(self) -> list[Segment]:
        """Get the segments that can be cleaned."""
        return self._get_segments()

    @override
    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Perform an area clean.

        Only cleans segments from the currently selected map.
        """
        if not self._maps:
            _LOGGER.warning("No map information available, cannot clean segments")
            return

        valid_room_ids: list[int | float] = []
        for composite_id in segment_ids:
            map_id, segment_id = _split_composite_id(composite_id)
            if (map_obj := self._maps.get(map_id)) is None:
                _LOGGER.warning("Map ID %s not found in available maps", map_id)
                continue

            if not map_obj.using:
                room_name = next(
                    (
                        segment.name
                        for segment in self.last_seen_segments or []
                        if segment.id == composite_id
                    ),
                    "",
                )
                _LOGGER.warning(
                    'Map "%s" is not currently selected, skipping segment "%s" (%s)',
                    map_obj.name,
                    room_name,
                    segment_id,
                )
                continue

            valid_room_ids.append(int(segment_id))

        if not valid_room_ids:
            _LOGGER.warning(
                "No valid segments to clean after validation,"
                " skipping clean segments command"
            )
            return

        if TYPE_CHECKING:
            assert self._capability.clean.action.area is not None

        await self._device.execute_command(
            self._capability.clean.action.area(
                CleanMode.SPOT_AREA,
                valid_room_ids,
                1,
            )
        )


@callback
def _split_composite_id(composite_id: str) -> tuple[str, str]:
    """Split a composite ID into its components."""
    map_id, _, segment_id = composite_id.partition(_SEGMENTS_SEPARATOR)
    return map_id, segment_id
