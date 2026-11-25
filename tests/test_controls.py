"""Tests for volume and water-mode entities."""

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


def load_control(name):
    """Load a control platform with Home Assistant interfaces stubbed."""
    package_name = f"_test_{name}_deebot_ngiot"
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
    events = types.ModuleType("deebot_client.events")
    events.VolumeEvent = type("VolumeEvent", (), {})
    events_base = types.ModuleType("deebot_client.events.base")
    events_base.Event = type("Event", (), {})
    component = types.ModuleType(f"homeassistant.components.{name}")
    entity_name = "Select" if name == "select" else "Number"
    setattr(component, f"{entity_name}Entity", type(f"{entity_name}Entity", (), {}))

    @dataclass(kw_only=True, frozen=True)
    class EntityDescription:
        key: str
        translation_key: str
        entity_category: object
        native_min_value: float | None = None
        native_max_value: float | None = None
        native_step: float | None = None

    setattr(component, f"{entity_name}EntityDescription", EntityDescription)
    const = types.ModuleType("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(CONFIG="config")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddConfigEntryEntitiesCallback = object
    commands = types.ModuleType(f"{package_name}.commands")
    commands.StatusEvent = type("StatusEvent", (events_base.Event,), {})
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
        "deebot_client.events": events,
        "deebot_client.events.base": events_base,
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        component.__name__: component,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.entity_platform": platform,
        commands.__name__: commands,
        entity.__name__: entity,
    }
    module_name = f"{package_name}.{name}"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(package.__path__[0]) / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    modules[module_name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_water_mode_updates_and_executes_selection(self):
        module = load_control("select")
        entity = module.WaterModeSelect()
        entity._capability = types.SimpleNamespace(event=object(), set=Mock(return_value="command"))
        entity._device = types.SimpleNamespace(execute_command=AsyncMock())
        entity._subscribe = Mock()
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()
        callback = entity._subscribe.call_args.args[1]
        await callback(types.SimpleNamespace(data={"waterMode": "mid"}))
        self.assertEqual(entity._attr_current_option, "medium")
        await callback(types.SimpleNamespace(data={"waterMode": "high"}))
        self.assertEqual(entity._attr_current_option, "high")
        await callback(types.SimpleNamespace(data={"waterMode": "invalid"}))
        self.assertEqual(entity.async_write_ha_state.call_count, 2)
        await entity.async_select_option("low")
        entity._capability.set.assert_called_once_with("low")
        entity._device.execute_command.assert_awaited_once_with("command")

    async def test_volume_updates_and_executes_integer_value(self):
        module = load_control("number")
        entity = module.Number()
        entity.entity_description = module.ENTITY_DESCRIPTIONS[0]
        entity._capability = types.SimpleNamespace(event=object(), set=Mock(return_value="command"))
        entity._device = types.SimpleNamespace(execute_command=AsyncMock())
        entity._subscribe = Mock()
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()
        callback = entity._subscribe.call_args.args[1]
        await callback(types.SimpleNamespace(volume=7, maximum=15))
        self.assertEqual(entity._attr_native_value, 7)
        self.assertEqual(entity._attr_native_max_value, 15)
        await callback(types.SimpleNamespace(volume=3, maximum=None))
        self.assertEqual(entity._attr_native_max_value, 15)
        await entity.async_set_native_value(4.0)
        entity._capability.set.assert_called_once_with(4)
        entity._device.execute_command.assert_awaited_once_with("command")
