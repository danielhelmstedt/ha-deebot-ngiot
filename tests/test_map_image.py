"""Tests for map image path tracking."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from test_status_mqtt import load_controller


def load_image():
    """Load the image platform with Home Assistant interfaces stubbed."""
    controller = load_controller()
    package_name = "_test_image_deebot_ngiot"
    package = types.ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "deebot_ngiot")
    ]
    package.ConfigEntry = object
    component = types.ModuleType("homeassistant.components.image")
    component.ImageEntity = type("ImageEntity", (), {})
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    entity_module = types.ModuleType("homeassistant.helpers.entity")
    entity_module.EntityDescription = lambda **kwargs: types.SimpleNamespace(**kwargs)
    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddConfigEntryEntitiesCallback = object
    util = types.ModuleType("homeassistant.util")
    util.dt = types.SimpleNamespace(utcnow=lambda: "now")
    entity = types.ModuleType(f"{package_name}.entity")

    class Entity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, device, capability, **kwargs):
            self._device = device
            self._capability = capability

        async def async_added_to_hass(self):
            pass

    entity.Entity = Entity
    map_module = types.ModuleType(f"{package_name}.map")
    map_globals = controller.decode_map_raster.__globals__
    for symbol in (
        "MapRasterEvent", "MapTracePointsEvent", "RobotPositionEvent",
        "render_svg", "room_id_at",
    ):
        setattr(map_module, symbol, map_globals[symbol])
    modules = {
        package_name: package,
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        component.__name__: component,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity": entity_module,
        "homeassistant.helpers.entity_platform": platform,
        "homeassistant.util": util,
        entity.__name__: entity,
        map_module.__name__: map_module,
    }
    name = f"{package_name}.image"
    spec = importlib.util.spec_from_file_location(
        name, Path(package.__path__[0]) / "image.py"
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
class MapImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracks_positions_only_while_cleaning_and_clears_on_dock(self):
        module = load_image()
        capabilities = types.SimpleNamespace(
            cached_info=types.SimpleNamespace(event=module.CachedMapInfoEvent),
            rooms=types.SimpleNamespace(event=module.RoomsEvent),
            changed=types.SimpleNamespace(event=module.MapChangedEvent),
        )
        entity = module.Map(types.SimpleNamespace(map=object()), capabilities, object())
        callbacks = {}
        entity._subscribe = lambda event, callback: callbacks.update({event: callback})
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()
        position = module.RobotPositionEvent("1", 1, 2, 0)
        await callbacks[module.RobotPositionEvent](position)
        self.assertEqual(entity._path, [])

        await callbacks[module.StateEvent](types.SimpleNamespace(state=module.State.CLEANING))
        await callbacks[module.RobotPositionEvent](position)
        await callbacks[module.RobotPositionEvent](position)
        self.assertEqual(entity._path, [position])
        self.assertEqual(entity._attr_extra_state_attributes["path_length"], 1)

        await callbacks[module.StateEvent](types.SimpleNamespace(state=module.State.DOCKED))
        self.assertEqual(entity._path, [])
        self.assertEqual(entity._attr_extra_state_attributes["path_length"], 0)

    async def test_trace_gaps_create_separate_path_segments(self):
        module = load_image()
        capabilities = types.SimpleNamespace(
            cached_info=types.SimpleNamespace(event=module.CachedMapInfoEvent),
            rooms=types.SimpleNamespace(event=module.RoomsEvent),
            changed=types.SimpleNamespace(event=module.MapChangedEvent),
        )
        entity = module.Map(types.SimpleNamespace(map=object()), capabilities, object())
        callbacks = {}
        entity._subscribe = lambda event, callback: callbacks.update({event: callback})
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()
        await callbacks[module.StateEvent](types.SimpleNamespace(state=module.State.CLEANING))
        first = module.RobotPositionEvent("1", 1, 1, 0)
        last = module.RobotPositionEvent("1", 3, 3, 0)
        await callbacks[module.MapTracePointsEvent](
            module.MapTracePointsEvent("1", "trace", 0, 3, (first,))
        )
        await callbacks[module.MapTracePointsEvent](
            module.MapTracePointsEvent("1", "trace", 2, 3, (last,))
        )
        self.assertEqual(entity._path, [first, None, last])
