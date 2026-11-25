"""Map image entity."""

from typing import Any, cast, override

from deebot_client.capabilities import CapabilityMap
from deebot_client.device import Device
from deebot_client.events import RoomsEvent, StateEvent
from deebot_client.events.map import CachedMapInfoEvent, MapChangedEvent
from deebot_client.map import Map as ClientMap
from deebot_client.models import Room, State
from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ConfigEntry
from .entity import Entity
from .map import (
    MapRasterEvent,
    MapTracePointsEvent,
    RobotPositionEvent,
    render_svg,
    room_id_at,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    controller = config_entry.runtime_data
    entities = [
        Map(device, caps, hass)
        for device in controller.devices
        if (caps := device.capabilities.map)
    ]

    if entities:
        async_add_entities(entities)


class Map(
    Entity[CapabilityMap],
    ImageEntity,
):
    """Map."""

    _attr_content_type = "image/svg+xml"

    def __init__(
        self,
        device: Device,
        capability: CapabilityMap,
        hass: HomeAssistant,
    ) -> None:
        """Initialize entity."""
        super().__init__(device, capability, hass=hass)
        self._attr_extra_state_attributes = {}
        self._map = cast(ClientMap, self._device.map)
        self._map_name: str | None = None
        self._rooms: list[Room] = []
        self._raster: MapRasterEvent | None = None
        self._position: RobotPositionEvent | None = None
        self._path: list[RobotPositionEvent | None] = []
        self._trace_id: str | None = None
        self._trace_points: dict[int, RobotPositionEvent] = {}
        self._last_state: State | None = None
        self._tracking_clean = False

    entity_description = EntityDescription(
        key="map",
        translation_key="map",
        entity_category=None,
    )

    @override
    def image(self) -> bytes | None:
        """Return bytes of image or None."""
        if self._raster is None:
            return None
        return render_svg(self._raster, self._position, self._path)

    def _update_attributes(self) -> None:
        """Rebuild the state attributes from the latest map data."""
        attributes: dict[str, Any] = {}
        if self._map_name is not None:
            attributes["map_name"] = self._map_name
        if (raster := self._raster) is not None:
            attributes |= {
                "map_id": raster.map_id,
                "width": raster.width,
                "height": raster.height,
                "resolution": raster.resolution,
            }
            if raster.charge_pos is not None:
                attributes["charge_pos"] = raster.charge_pos
        if self._rooms:
            attributes["rooms"] = {room.id: room.name for room in self._rooms}
        if (position := self._position) is not None:
            attributes["robot_position"] = {
                "x": position.x,
                "y": position.y,
                "angle": position.angle,
            }
            if (
                raster is not None
                and (room_id := room_id_at(raster, position)) is not None
            ):
                attributes["current_room_id"] = room_id
                if room := next((r for r in self._rooms if r.id == room_id), None):
                    attributes["current_room"] = room.name
        attributes["path_length"] = sum(point is not None for point in self._path)
        self._attr_extra_state_attributes = attributes

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_info(event: CachedMapInfoEvent) -> None:
            for map_obj in event.maps:
                if map_obj.using:
                    self._map_name = map_obj.name
            self._update_attributes()

        async def on_rooms(event: RoomsEvent) -> None:
            self._rooms = list(event.rooms)
            self._update_attributes()
            self.async_write_ha_state()

        async def on_changed(event: MapChangedEvent) -> None:
            self._attr_image_last_updated = event.when
            self.async_write_ha_state()

        async def on_raster(event: MapRasterEvent) -> None:
            self._raster = event
            self._update_attributes()
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()

        async def on_position(event: RobotPositionEvent) -> None:
            self._position = event
            if self._tracking_clean and self._trace_id is None:
                last_point = next(
                    (point for point in reversed(self._path) if point is not None),
                    None,
                )
                if last_point and last_point.map_id != event.map_id:
                    self._path.clear()
                if not self._path or (
                    self._path[-1] is None
                    or self._path[-1].x != event.x
                    or self._path[-1].y != event.y
                ):
                    self._path.append(event)
            self._update_attributes()
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()

        async def on_trace(event: MapTracePointsEvent) -> None:
            if not self._tracking_clean:
                return
            if event.trace_id != self._trace_id:
                self._trace_id = event.trace_id
                self._trace_points.clear()
                self._path.clear()
            if event.start == 0:
                self._trace_points.clear()
                self._path.clear()
            for index, point in enumerate(event.points, start=event.start):
                self._trace_points[index] = point
            self._path.clear()
            previous_index = None
            previous_point = None
            for index in sorted(self._trace_points):
                point = self._trace_points[index]
                break_before = (
                    (previous_index is not None and index > previous_index + 1)
                    or point.break_before
                    or (previous_point and previous_point.map_id != point.map_id)
                )
                if break_before and self._path and self._path[-1] is not None:
                    self._path.append(None)
                last_point = self._path[-1] if self._path else None
                if last_point is None or (
                    last_point.map_id != point.map_id
                    or last_point.x != point.x
                    or last_point.y != point.y
                ):
                    self._path.append(point)
                previous_index = index
                previous_point = point
            self._update_attributes()
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()

        async def on_state(event: StateEvent) -> None:
            if event.state == State.CLEANING:
                if not self._tracking_clean or self._last_state not in {
                    State.CLEANING,
                    State.PAUSED,
                    State.RETURNING,
                }:
                    self._path.clear()
                    self._trace_id = None
                    self._trace_points.clear()
                self._tracking_clean = True
            elif event.state == State.DOCKED:
                if self._path:
                    self._path.clear()
                    self._trace_id = None
                    self._trace_points.clear()
                    self._update_attributes()
                    self._attr_image_last_updated = dt_util.utcnow()
                    self.async_write_ha_state()
                self._tracking_clean = False
            self._last_state = event.state

        self._subscribe(self._capability.cached_info.event, on_info)
        self._subscribe(self._capability.rooms.event, on_rooms)
        self._subscribe(self._capability.changed.event, on_changed)
        self._subscribe(MapRasterEvent, on_raster)
        self._subscribe(RobotPositionEvent, on_position)
        self._subscribe(MapTracePointsEvent, on_trace)
        self._subscribe(StateEvent, on_state)

    @override
    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        await super().async_update()
        self._map.refresh()
