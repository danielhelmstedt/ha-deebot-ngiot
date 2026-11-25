"""Select entities for the NGIOT integration."""

from dataclasses import dataclass
from typing import Any, override

from deebot_client.capabilities import CapabilitySet
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ConfigEntry
from .commands import StatusEvent
from .entity import (
    CapabilityEntityDescription,
    DescriptionEntity,
    get_supported_entities,
)


@dataclass(kw_only=True, frozen=True)
class SelectDescription(
    SelectEntityDescription,
    CapabilityEntityDescription,
):
    """Class describing a select entity."""


def _water_mode_capability(
    capabilities: Any,
) -> CapabilitySet[StatusEvent, [str]] | None:
    return getattr(capabilities, "water_mode", None)


ENTITY_DESCRIPTIONS: tuple[SelectDescription, ...] = (
    SelectDescription(
        capability_fn=_water_mode_capability,
        key="water_mode",
        translation_key="water_mode",
        entity_category=EntityCategory.CONFIG,
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
            config_entry.runtime_data, WaterModeSelect, ENTITY_DESCRIPTIONS
        )
    )


class WaterModeSelect(
    DescriptionEntity[CapabilitySet[StatusEvent, [str]]],
    SelectEntity,
):
    """Select the mop water level."""

    entity_description: SelectDescription
    _attr_options = ["low", "medium", "high"]

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_status(event: StatusEvent) -> None:
            mode = event.data.get("waterMode")
            if mode == "mid":
                self._attr_current_option = "medium"
            elif mode in self._attr_options:
                self._attr_current_option = mode
            else:
                return
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_status)

    @override
    async def async_select_option(self, option: str) -> None:
        """Set the water level."""
        await self._device.execute_command(self._capability.set(option))
