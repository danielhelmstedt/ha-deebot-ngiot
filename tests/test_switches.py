"""Tests for switch capability and status-field mapping."""

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_switch():
    """Load switches without requiring Home Assistant."""
    package_name = "_test_switch_deebot_ngiot"
    package = types.ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "deebot_ngiot")
    ]
    package.ConfigEntry = object
    capabilities = types.ModuleType("deebot_client.capabilities")

    class CapabilitySet:
        def __class_getitem__(cls, item):
            return cls

    capabilities.CapabilitySet = CapabilitySet
    switch = types.ModuleType("homeassistant.components.switch")
    switch.SwitchEntity = type("SwitchEntity", (), {})

    @dataclass(kw_only=True, frozen=True)
    class SwitchEntityDescription:
        key: str
        translation_key: str
        entity_category: object

    switch.SwitchEntityDescription = SwitchEntityDescription
    const = types.ModuleType("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(CONFIG="config")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddConfigEntryEntitiesCallback = object
    commands = types.ModuleType(f"{package_name}.commands")
    commands.StatusEvent = type("StatusEvent", (), {})
    entity = types.ModuleType(f"{package_name}.entity")

    @dataclass(kw_only=True, frozen=True)
    class CapabilityEntityDescription:
        capability_fn: object

    class DescriptionEntity:
        def __class_getitem__(cls, item):
            return cls

        async def async_added_to_hass(self):
            pass

    entity.CapabilityEntityDescription = CapabilityEntityDescription
    entity.DescriptionEntity = DescriptionEntity
    entity.get_supported_entities = lambda *args: []
    modules = {
        package_name: package,
        "deebot_client": types.ModuleType("deebot_client"),
        "deebot_client.capabilities": capabilities,
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        "homeassistant.components.switch": switch,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.entity_platform": platform,
        commands.__name__: commands,
        entity.__name__: entity,
    }
    name = f"{package_name}.switch"
    spec = importlib.util.spec_from_file_location(
        name, Path(package.__path__[0]) / "switch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    modules[name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class SwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_capabilities_and_status_updates_use_device_fields(self):
        module = load_switch()
        fields = {
            "child_lock": "childLock",
            "do_not_disturb": "disturbSwitch",
            "break_clean": "breakCleanSwitch",
            "auto_boost": "autoBoost",
        }
        capabilities = types.SimpleNamespace(
            switches={field: object() for field in fields.values()}
        )
        self.assertEqual({d.key for d in module.ENTITY_DESCRIPTIONS}, set(fields))
        for description in module.ENTITY_DESCRIPTIONS:
            with self.subTest(key=description.key):
                field = fields[description.key]
                self.assertIs(
                    description.capability_fn(capabilities), capabilities.switches[field]
                )
                entity = module.Switch()
                entity.entity_description = description
                entity._capability = types.SimpleNamespace(event=module.StatusEvent)
                entity._subscribe = Mock()
                entity.async_write_ha_state = Mock()
                await entity.async_added_to_hass()
                callback = entity._subscribe.call_args.args[1]
                await callback(types.SimpleNamespace(data={field: True}))
                self.assertIs(entity._attr_is_on, True)
                await callback(types.SimpleNamespace(data={field: False}))
                self.assertIs(entity._attr_is_on, False)
                await callback(types.SimpleNamespace(data={}))
                self.assertEqual(entity.async_write_ha_state.call_count, 2)
