"""Tests for NGIOT status MQTT routing."""

import importlib
import importlib.util
from functools import lru_cache
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, call, patch


@lru_cache(maxsize=1)
def load_controller():
    """Load the controller with the client, without requiring Home Assistant."""
    package = types.ModuleType("_test_deebot_ngiot")
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "deebot_ngiot")
    ]
    homeassistant = types.ModuleType("homeassistant")
    const = types.ModuleType("homeassistant.const")
    for name in ("CONF_COUNTRY", "CONF_DEVICE_ID", "CONF_PASSWORD", "CONF_USERNAME"):
        setattr(const, name, name.lower())
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    modules = {
        package.__name__: package,
        "homeassistant": homeassistant,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": helpers.aiohttp_client,
    }
    with patch.dict(sys.modules, modules):
        controller = importlib.import_module("_test_deebot_ngiot.controller")
        client_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "deebot_client" or name.startswith("deebot_client.")
        }
    for name, module in client_modules.items():
        sys.modules.setdefault(name, module)
    return controller


@unittest.skipUnless(
    importlib.util.find_spec("deebot_client"), "deebot-client is not installed"
)
class StatusMqttTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = load_controller()

    def setUp(self):
        self.device = self.controller.DeviceController.__new__(
            self.controller.DeviceController
        )
        self.device.events = Mock()
        self.device._device_info = types.SimpleNamespace(
            static=types.SimpleNamespace(
                data_type=self.controller.DataType.JSON,
                capabilities=types.SimpleNamespace(map=None),
            )
        )
    def test_status_push_delivers_status_and_derived_events(self):
        self.device._handle_live_payload(
            "10000", b'{"body":{"data":{"battery":100,"status":"idle"}}}'
        )
        events = [call.args[0] for call in self.device.events.notify.call_args_list]
        self.assertEqual(events[0].data, {"battery": 100, "status": "idle"})
        self.assertEqual(events[1].value, 100)
        self.device.events.request_refresh.assert_not_called()

    def test_invalid_live_body_does_not_publish(self):
        with self.assertLogs(self.controller.__name__, level="WARNING"):
            self.device._handle_live_payload("10000", b'{"body":[]}')
        self.device.events.notify.assert_not_called()
        self.device.events.request_refresh.assert_not_called()

    def test_only_status_and_map_families_use_live_handler(self):
        payload = b'{"body":{"data":{"battery":2}}}'
        with (
            patch.object(self.controller.Device, "_handle_message") as parent,
            patch.object(self.device, "_handle_live_payload") as live,
        ):
            for topic in ("10000", "10001", "30000"):
                self.device._handle_message(topic, payload)
            self.assertEqual(parent.call_count, 3)
            self.assertEqual(
                live.call_args_list,
                [call("10000", payload), call("30000", payload)],
            )

    def test_partial_push_still_requests_state_refresh(self):
        self.device._handle_live_payload("10000", b'{"body":{"data":{"dormant":true}}}')
        self.assertEqual(
            self.device.events.notify.call_args.args[0].data, {"dormant": True}
        )
        self.device.events.request_refresh.assert_called_once_with(
            self.controller.StateEvent
        )

    def test_decoded_status_push_is_published(self):
        self.device._handle_live_payload(
            "10000", {"body": {"data": {"waterMode": "mid", "status": "idle"}}}
        )
        self.assertEqual(
            self.device.events.notify.call_args_list[0].args[0].data,
            {"waterMode": "mid", "status": "idle"},
        )
        self.device.events.request_refresh.assert_not_called()


@unittest.skipUnless(
    importlib.util.find_spec("deebot_client"), "deebot-client is not installed"
)
class StandardMqttClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_uses_standard_mqtt_client(self):
        controller = load_controller()
        account = controller.AccountController.__new__(controller.AccountController)
        account._mqtt_client = None
        account._device_id = "device"
        account._country = "US"
        account._authenticator = object()
        account._hass = types.SimpleNamespace(async_add_executor_job=AsyncMock())
        config = object()
        account._hass.async_add_executor_job.return_value = config
        client = types.SimpleNamespace(verify_config=AsyncMock())
        with patch.object(controller, "MqttClient", return_value=client) as factory:
            self.assertIs(await account._get_mqtt_client(), client)
        factory.assert_called_once_with(config, account._authenticator)
        client.verify_config.assert_awaited_once()
