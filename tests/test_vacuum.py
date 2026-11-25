"""Tests for vacuum controls and room selection."""

from dataclasses import dataclass
from enum import IntFlag, auto
import importlib.util
import json
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_status_mqtt import load_controller


def load_vacuum():
    """Load vacuum entities with Home Assistant interfaces stubbed."""
    controller = load_controller()
    package_name = "_test_vacuum_deebot_ngiot"
    package = types.ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "deebot_ngiot")
    ]
    package.ConfigEntry = object
    component = types.ModuleType("homeassistant.components.vacuum")

    class VacuumEntityFeature(IntFlag):
        PAUSE = auto()
        RETURN_HOME = auto()
        STATE = auto()
        START = auto()
        STOP = auto()
        FAN_SPEED = auto()
        CLEAN_AREA = auto()
        LOCATE = auto()
        SEND_COMMAND = auto()

    @dataclass
    class Segment:
        id: str
        name: str
        group: str

    component.Segment = Segment
    component.StateVacuumEntity = type("StateVacuumEntity", (), {})
    component.StateVacuumEntityDescription = lambda **kwargs: types.SimpleNamespace(**kwargs)
    component.VacuumActivity = types.SimpleNamespace(
        IDLE="idle", CLEANING="cleaning", RETURNING="returning",
        DOCKED="docked", ERROR="error", PAUSED="paused",
    )
    component.VacuumEntityFeature = VacuumEntityFeature
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func
    helpers = types.ModuleType("homeassistant.helpers")
    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddConfigEntryEntitiesCallback = object
    helpers.entity_platform = platform
    util = types.ModuleType("homeassistant.util")
    util.slugify = lambda value: value.lower().replace(" ", "_")
    probatio = types.ModuleType("probatio")
    probatio.In = lambda value: value
    probatio.Required = lambda value: value
    const = types.ModuleType(f"{package_name}.const")
    const.CONSUMABLES = controller.CONSUMABLES
    const.FAN_SPEED_TO_FAN_MODE = controller.GetStatus.__init__.__globals__["FAN_SPEED_TO_FAN_MODE"]
    local_controller = types.ModuleType(f"{package_name}.controller")
    local_controller.Capabilities = controller.Capabilities
    entity = types.ModuleType(f"{package_name}.entity")

    class Entity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, device, capability):
            self._device = device
            self._capability = capability

    entity.Entity = Entity
    modules = {
        package_name: package,
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        component.__name__: component,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_platform": platform,
        "homeassistant.util": util,
        "probatio": probatio,
        const.__name__: const,
        local_controller.__name__: local_controller,
        entity.__name__: entity,
    }
    name = f"{package_name}.vacuum"
    spec = importlib.util.spec_from_file_location(
        name, Path(package.__path__[0]) / "vacuum.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    modules[name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    importlib.util.find_spec("deebot_client"), "deebot-client is not installed"
)
class VacuumTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_vacuum()

    def make_vacuum(self):
        fan = types.SimpleNamespace(
            types=[load_controller().FanSpeedLevel.NORMAL], set=Mock(return_value="fan")
        )
        action = types.SimpleNamespace(
            area=Mock(return_value="area"), command=Mock(return_value="clean")
        )
        caps = types.SimpleNamespace(
            fan_speed=fan, map=object(), clean=types.SimpleNamespace(action=action),
            play_sound=None, custom=None,
        )
        device = types.SimpleNamespace(capabilities=caps, execute_command=AsyncMock())
        vacuum = self.module.Vacuum(device)
        vacuum.last_seen_segments = []
        return vacuum

    async def test_fan_speed_validation_and_clean_actions(self):
        vacuum = self.make_vacuum()
        await vacuum.async_set_fan_speed("auto")
        vacuum._capability.fan_speed.set.assert_called_once_with(
            load_controller().FanSpeedLevel.NORMAL
        )
        vacuum._device.execute_command.assert_awaited_with("fan")
        with self.assertRaises(ValueError):
            await vacuum.async_set_fan_speed("invalid")
        await vacuum.async_start()
        vacuum._capability.clean.action.command.assert_called_once_with(
            self.module.CleanAction.START
        )
        vacuum._device.execute_command.assert_awaited_with("clean")

    async def test_clean_segments_only_sends_rooms_from_active_map(self):
        vacuum = self.make_vacuum()
        vacuum._maps = {
            "1": types.SimpleNamespace(id="1", name="Current", using=True),
            "2": types.SimpleNamespace(id="2", name="Other", using=False),
        }
        with self.assertLogs(self.module.__name__, level="WARNING"):
            await vacuum.async_clean_segments(["1_4", "2_5", "3_9"])
        vacuum._capability.clean.action.area.assert_called_once_with(
            self.module.CleanMode.SPOT_AREA, [4], 1
        )
        vacuum._device.execute_command.assert_awaited_once_with("area")

    def test_room_attributes_preserve_duplicate_names(self):
        vacuum = self.make_vacuum()
        vacuum._room_event = types.SimpleNamespace(rooms=[
            types.SimpleNamespace(name="Living Room", id=1),
            types.SimpleNamespace(name="Living Room", id=2),
        ])
        self.assertEqual(vacuum.extra_state_attributes, {"rooms": {"living_room": [1, 2]}})

    async def test_reset_consumable_accepts_selector_and_legacy_values(self):
        vacuum = self.make_vacuum()
        reset = Mock(return_value="reset")
        vacuum._capability.reset_consumable = types.SimpleNamespace(execute=reset)
        for value, expected in (
            ("side_brush", "sideBrush"),
            ("roll_brush", "rollBrush"),
            ("unit_care", "unitCare"),
            ("filter", "filter"),
            ("sideBrush", "sideBrush"),
            ("rollBrush", "rollBrush"),
            ("unitCare", "unitCare"),
        ):
            with self.subTest(value=value):
                await vacuum.async_reset_consumable(value)
                reset.assert_called_with(expected)
                vacuum._device.execute_command.assert_awaited_with("reset")
        with self.assertRaises(ValueError):
            await vacuum.async_reset_consumable("invalid")

    async def test_service_schema_accepts_selector_and_legacy_values(self):
        platform = Mock()
        self.module.entity_platform.async_get_current_platform = Mock(return_value=platform)
        entry = types.SimpleNamespace(runtime_data=types.SimpleNamespace(devices=[]))
        await self.module.async_setup_entry(object(), entry, Mock())
        schema = platform.async_register_entity_service.call_args.args[1]
        self.assertEqual(
            set(schema["consumable"]),
            {"side_brush", "roll_brush", "filter", "unit_care",
             "sideBrush", "rollBrush", "unitCare"},
        )

    def test_selector_options_have_valid_matching_translation_keys(self):
        directory = Path(__file__).parents[1] / "custom_components" / "deebot_ngiot"
        translations = json.loads((directory / "translations" / "en.json").read_text())
        translated_options = translations["selector"]["consumable"]["options"]
        options_section = (directory / "services.yaml").read_text().split("options:", 1)[1]
        options = re.findall(r"^\s*-\s+(\S+)\s*$", options_section, re.MULTILINE)
        self.assertEqual(set(options), set(translated_options))
        self.assertEqual(set(options), set(self.module.CONSUMABLES))
        for option in options:
            self.assertRegex(option, r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
