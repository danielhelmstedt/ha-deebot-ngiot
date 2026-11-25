"""Binary sensor module."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ConfigEntry
from .commands import StatusEvent
from .controller import status_capability
from .entity import (
    CapabilityEntityDescription,
    DescriptionEntity,
    get_supported_entities,
)


@dataclass(kw_only=True, frozen=True)
class BinarySensorDescription(
    BinarySensorEntityDescription,
    CapabilityEntityDescription,
):
    """Class describing binary sensor entity."""

    value_fn: Callable[[StatusEvent], bool | None]


ENTITY_DESCRIPTIONS: tuple[BinarySensorDescription, ...] = (
    BinarySensorDescription(
        capability_fn=status_capability,
        value_fn=lambda event: (
            None if (raw := event.data.get("dormant")) is None else bool(raw)
        ),
        key="dormant",
        translation_key="dormant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorDescription(
        capability_fn=status_capability,
        value_fn=lambda event: (
            None if (raw := event.data.get("relocateSwitch")) is None else bool(raw)
        ),
        key="relocateSwitch",
        translation_key="relocation_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorDescription(
        capability_fn=status_capability,
        value_fn=lambda event: (
            None if (state := event.data.get("mopState")) is None
            else state == "installed"
        ),
        key="mopState",
        translation_key="mop_state",
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
            config_entry.runtime_data, BinarySensor, ENTITY_DESCRIPTIONS
        )
    )


class BinarySensor(
    DescriptionEntity,
    BinarySensorEntity,
):
    """Binary sensor backed by the status snapshot."""

    entity_description: BinarySensorDescription

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_event(event: StatusEvent) -> None:
            if (value := self.entity_description.value_fn(event)) is not None:
                self._attr_is_on = value
                self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)
