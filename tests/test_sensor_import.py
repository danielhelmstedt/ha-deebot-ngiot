"""Tests for sensor platform initialization."""

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def load_sensor():
    """Load sensor descriptions using Home Assistant-compatible stubs."""
    package_name = "_test_sensor_deebot_ngiot"
    package = types.ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "deebot_ngiot")
    ]
    package.ConfigEntry = object
    capabilities = types.ModuleType("deebot_client.capabilities")

    class CapabilityEvent:
        def __class_getitem__(cls, item):
            return cls

    capabilities.CapabilityEvent = CapabilityEvent
    events = types.ModuleType("deebot_client.events")
    events.BatteryEvent = type("BatteryEvent", (), {})
    events_base = types.ModuleType("deebot_client.events.base")
    events_base.Event = type("Event", (), {})
    sensor = types.ModuleType("homeassistant.components.sensor")
    sensor.SensorEntity = type("SensorEntity", (), {})

    @dataclass(kw_only=True, frozen=True)
    class SensorEntityDescription:
        key: str
        translation_key: str | None = None
        native_unit_of_measurement: str | None = None
        suggested_unit_of_measurement: str | None = None
        suggested_display_precision: int | None = None
        device_class: object = None
        state_class: object = None
        entity_category: object = None

    sensor.SensorEntityDescription = SensorEntityDescription
    sensor.SensorDeviceClass = types.SimpleNamespace(
        BATTERY="battery", AREA="area", DURATION="duration"
    )
    sensor.SensorStateClass = types.SimpleNamespace(
        MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing"
    )
    const = types.ModuleType("homeassistant.const")
    const.PERCENTAGE = "%"
    const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic")
    const.UnitOfArea = types.SimpleNamespace(SQUARE_METERS="m²")
    const.UnitOfTime = types.SimpleNamespace(MINUTES="min", HOURS="h")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddConfigEntryEntitiesCallback = object
    typing_module = types.ModuleType("homeassistant.helpers.typing")
    typing_module.StateType = object
    commands = types.ModuleType(f"{package_name}.commands")
    commands.StatusEvent = type("StatusEvent", (events_base.Event,), {})
    controller = types.ModuleType(f"{package_name}.controller")
    controller.status_capability = lambda caps: None
    entity = types.ModuleType(f"{package_name}.entity")

    @dataclass(kw_only=True, frozen=True)
    class CapabilityEntityDescription:
        capability_fn: object

    class DescriptionEntity:
        def __class_getitem__(cls, item):
            return cls

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
        "homeassistant.components.sensor": sensor,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.entity_platform": platform,
        "homeassistant.helpers.typing": typing_module,
        commands.__name__: commands,
        controller.__name__: controller,
        entity.__name__: entity,
    }
    name = f"{package_name}.sensor"
    spec = importlib.util.spec_from_file_location(
        name, Path(package.__path__[0]) / "sensor.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    modules[name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class SensorImportTests(unittest.TestCase):
    def test_clean_area_suggests_integer_display_without_rounding_value(self):
        module = load_sensor()
        description = next(
            description
            for description in module.ENTITY_DESCRIPTIONS
            if description.key == "cleanArea"
        )
        self.assertEqual(description.suggested_display_precision, 0)
        self.assertEqual(
            description.value_fn(types.SimpleNamespace(data={"cleanArea": 1.75})),
            1.75,
        )

    def test_consumable_descriptions_initialize(self):
        module = load_sensor()
        descriptions = [
            description
            for description in module.ENTITY_DESCRIPTIONS
            if description.key.startswith("consumable_")
        ]
        self.assertEqual(len(descriptions), 4)
        for description in descriptions:
            self.assertEqual(description.native_unit_of_measurement, "min")
            self.assertEqual(description.suggested_unit_of_measurement, "h")

    def test_consumable_reads_matching_raw_minutes_and_total(self):
        module = load_sensor()
        description = next(
            description
            for description in module.ENTITY_DESCRIPTIONS
            if description.key == "consumable_filter"
        )
        event = types.SimpleNamespace(data={"consumables": [
            {"type": "sideBrush", "left": 10, "total": 20},
            {"type": "filter", "left": 8520, "total": 9000},
        ]})
        self.assertEqual(description.value_fn(event), 8520)
        self.assertEqual(description.attributes_fn(event), {"total": 9000})
        event.data = {"consumables": [{"type": "filter", "left": None}]}
        self.assertIsNone(description.value_fn(event))
        self.assertIsNone(description.attributes_fn(event))
