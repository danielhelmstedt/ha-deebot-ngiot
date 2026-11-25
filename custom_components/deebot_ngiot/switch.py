"""Switch module."""

from dataclasses import dataclass
from typing import Any, override

from deebot_client.capabilities import CapabilitySet
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
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
class SwitchDescription(
    SwitchEntityDescription,
    CapabilityEntityDescription,
):
    """Class describing switch entity."""

    status_field: str


def _switch_description(
    key: str,
    translation_key: str,
    *,
    status_field: str,
    entity_category: EntityCategory | None,
) -> SwitchDescription:
    def capability(caps: Any) -> CapabilitySet[StatusEvent, [bool]] | None:
        return getattr(caps, "switches", {}).get(status_field)

    return SwitchDescription(
        capability_fn=capability,
        key=key,
        translation_key=translation_key,
        status_field=status_field,
        entity_category=entity_category,
    )


ENTITY_DESCRIPTIONS: tuple[SwitchDescription, ...] = (
    _switch_description(
        "child_lock", 
        translation_key="child_lock",
        status_field="childLock",
        entity_category=EntityCategory.CONFIG
    ),
    _switch_description(
        "do_not_disturb",
        translation_key="do_not_disturb",
        status_field="disturbSwitch",
        entity_category=EntityCategory.CONFIG
    ),
    _switch_description(
        "break_clean",
        translation_key="auto_resume",
        status_field="breakCleanSwitch",
        entity_category=EntityCategory.CONFIG
    ),
    _switch_description(
        "auto_boost",
        translation_key="carpet_boost",
        status_field="autoBoost",
        entity_category=EntityCategory.CONFIG
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    async_add_entities(
        get_supported_entities(config_entry.runtime_data, Switch, ENTITY_DESCRIPTIONS)
    )


class Switch(
    DescriptionEntity[CapabilitySet[StatusEvent, [bool]]],
    SwitchEntity,
):
    """Switch for an on/off setting."""

    entity_description: SwitchDescription

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_event(event: StatusEvent) -> None:
            if (value := event.data.get(self.entity_description.status_field)) is None:
                return
            self._attr_is_on = bool(value)
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

    async def _set(self, enabled: bool) -> None:
        await self._device.execute_command(self._capability.set(enabled))

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self._set(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self._set(False)
