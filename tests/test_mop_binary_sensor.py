"""Tests for the mop installation binary sensor."""

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def load_binary_sensor():
    """Load binary sensors without requiring Home Assistant."""
    package_name = "_test_mop_deebot_ngiot"
    package = types.ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "deebot_ngiot")
    ]
    package.ConfigEntry = object
    binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorEntity = type("BinarySensorEntity", (), {})

    @dataclass(kw_only=True, frozen=True)
    class BinarySensorEntityDescription:
        key: str
        translation_key: str
        entity_category: object

    binary_sensor.BinarySensorEntityDescription = BinarySensorEntityDescription
    const = types.ModuleType("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddConfigEntryEntitiesCallback = object
    commands = types.ModuleType(f"{package_name}.commands")
    commands.StatusEvent = type("StatusEvent", (), {})
    controller = types.ModuleType(f"{package_name}.controller")
    controller.status_capability = lambda caps: None
    entity = types.ModuleType(f"{package_name}.entity")

    @dataclass(kw_only=True, frozen=True)
    class CapabilityEntityDescription:
        capability_fn: object

    entity.CapabilityEntityDescription = CapabilityEntityDescription
    entity.DescriptionEntity = type("DescriptionEntity", (), {})
    entity.get_supported_entities = lambda *args: []
    modules = {
        package_name: package,
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        "homeassistant.components.binary_sensor": binary_sensor,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.entity_platform": platform,
        commands.__name__: commands,
        controller.__name__: controller,
        entity.__name__: entity,
    }
    name = f"{package_name}.binary_sensor"
    path = Path(package.__path__[0]) / "binary_sensor.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    modules[name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class MopBinarySensorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_binary_sensor()
        cls.description = next(
            description
            for description in cls.module.ENTITY_DESCRIPTIONS
            if description.key == "mopState"
        )

    def test_reported_values(self):
        for state, expected in (
            ("installed", True),
            ("none", False),
            ("other", False),
        ):
            with self.subTest(state=state):
                event = types.SimpleNamespace(data={"mopState": state})
                self.assertIs(self.description.value_fn(event), expected)

    def test_missing_and_null_values_do_not_change_state(self):
        self.assertIsNone(self.description.value_fn(types.SimpleNamespace(data={})))
        self.assertIsNone(
            self.description.value_fn(types.SimpleNamespace(data={"mopState": None}))
        )
