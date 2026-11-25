"""Sensor module."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, override

from deebot_client.capabilities import CapabilityEvent
from deebot_client.events import BatteryEvent
from deebot_client.events.base import Event
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import ConfigEntry
from .commands import StatusEvent
from .controller import status_capability
from .entity import (
    CapabilityEntityDescription,
    DescriptionEntity,
    get_supported_entities,
)


@dataclass(kw_only=True, frozen=True)
class SensorDescription[EventT: Event](
    SensorEntityDescription,
    CapabilityEntityDescription,
):
    """Class describing sensor entity."""

    value_fn: Callable[[EventT], StateType]
    attributes_fn: Callable[[EventT], Mapping[str, Any] | None] | None = None


def _consumable(
    kind: str,
) -> tuple[
    Callable[[StatusEvent], StateType],
    Callable[[StatusEvent], Mapping[str, Any] | None],
]:
    def find(event: StatusEvent) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in event.data.get("consumables", [])
                if item.get("type") == kind and item.get("left") is not None
            ),
            None,
        )

    def value(event: StatusEvent) -> StateType:
        return item["left"] if (item := find(event)) else None

    def attributes(event: StatusEvent) -> Mapping[str, Any] | None:
        if not (item := find(event)):
            return None
        return {"total": item.get("total")}

    return value, attributes


def _status_description(
    key: str,
    *,
    translation_key: str,
    unit: str | None = None,
    suggested_unit_of_measurement: str | None = None,
    suggested_display_precision: int | None = None,
    device_class: SensorDeviceClass | None = None,
    state_class: SensorStateClass | None = None,
    entity_category: EntityCategory | None,
) -> SensorDescription[StatusEvent]:
    return SensorDescription[StatusEvent](
        capability_fn=status_capability,
        value_fn=lambda event: event.data.get(key),
        key=key,
        translation_key=translation_key,
        native_unit_of_measurement=unit,
        suggested_unit_of_measurement=suggested_unit_of_measurement,
        suggested_display_precision=suggested_display_precision,
        device_class=device_class,
        state_class=state_class,
        entity_category=entity_category,
    )


def _consumable_description(
    kind: str, translation_key: str,
    *, 
    unit: str | None = None,
    suggested_unit_of_measurement: str | None = None,
    device_class: SensorDeviceClass | None = None,
    state_class: SensorStateClass | None = None,
    entity_category: EntityCategory | None
) -> SensorDescription[StatusEvent]:
    value_fn, attributes_fn = _consumable(kind)
    return SensorDescription[StatusEvent](
        capability_fn=status_capability,
        value_fn=value_fn,
        attributes_fn=attributes_fn,
        key=f"consumable_{kind}",
        translation_key=translation_key,
        native_unit_of_measurement=unit,
        suggested_unit_of_measurement=suggested_unit_of_measurement,
        device_class=device_class,
        state_class=state_class,
        entity_category=entity_category,
    )


ENTITY_DESCRIPTIONS: tuple[SensorDescription, ...] = (
    SensorDescription[BatteryEvent](
        capability_fn=lambda caps: caps.battery,
        value_fn=lambda e: e.value,
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None
    ),
    _status_description(
        "cleanArea",
        translation_key="stats_area",
        unit=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC
    ),
    _status_description(
        "cleanTime",
        translation_key="stats_time",
        unit=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
    ),
    _status_description(
        "cleanCount",
        translation_key="stats_cleanings",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _status_description(
        "cleanAreaTotal",
        translation_key="total_stats_area",
        unit=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _status_description(
        "cleanCountTotal",
        translation_key="total_stats_cleanings",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _status_description(
        "cleanTimeTotal",
        translation_key="total_stats_time",
        unit=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _status_description(
        "workMode",
        translation_key="work_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _status_description(
        "unitSet",
        translation_key="unit_set",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _consumable_description(
        "sideBrush",
        translation_key="lifespan_side_brush", 
        unit=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _consumable_description(
        "rollBrush",
        translation_key="lifespan_roll_brush",
        unit=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _consumable_description(
        "filter",
        translation_key="lifespan_filter",
        unit=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _consumable_description(
        "unitCare",
        translation_key="lifespan_unit_care",
        unit=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    async_add_entities(
        get_supported_entities(
            config_entry.runtime_data, Sensor, ENTITY_DESCRIPTIONS
        )
    )


class Sensor[EventT: Event](
    DescriptionEntity[CapabilityEvent[EventT]],
    SensorEntity,
):
    """Sensor."""

    entity_description: SensorDescription

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_event(event: EventT) -> None:
            if (value := self.entity_description.value_fn(event)) is None:
                return
            self._attr_native_value = value
            if self.entity_description.attributes_fn:
                self._attr_extra_state_attributes = (
                    self.entity_description.attributes_fn(event)
                )
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)
