"""Tests for account validation and device discovery."""

import importlib.util
from pathlib import Path
import string
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_status_mqtt import load_controller


def load_config_flow():
    """Load config flow with Home Assistant interfaces stubbed."""
    controller = load_controller()
    package_name = "_test_config_deebot_ngiot"
    package = types.ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "deebot_ngiot")
    ]
    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    config_entries.ConfigFlow = ConfigFlow
    config_entries.ConfigFlowResult = dict
    config_entries.SOURCE_REAUTH = "reauth"
    const = types.ModuleType("homeassistant.const")
    for name in ("CONF_COUNTRY", "CONF_DEVICE_ID", "CONF_PASSWORD", "CONF_USERNAME"):
        setattr(const, name, name.lower())
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    helpers.selector = types.ModuleType("homeassistant.helpers.selector")
    local_const = types.ModuleType(f"{package_name}.const")
    local_const.CONF_DEVICE_DID = "device_did"
    local_const.CONF_VERIFICATION_CODE = "verification_code"
    local_const.DOMAIN = "deebot_ngiot"
    local_controller = types.ModuleType(f"{package_name}.controller")
    local_controller.is_ngiot_candidate = controller.is_ngiot_candidate
    modules = {
        package_name: package,
        "probatio": types.ModuleType("probatio"),
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": helpers.aiohttp_client,
        "homeassistant.helpers.selector": helpers.selector,
        local_const.__name__: local_const,
        local_controller.__name__: local_controller,
    }
    name = f"{package_name}.config_flow"
    spec = importlib.util.spec_from_file_location(
        name, Path(package.__path__[0]) / "config_flow.py"
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
class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow = load_config_flow()

    def test_device_id_and_label_fallbacks(self):
        self.assertEqual(self.flow.get_client_device_id({"conf_device_id": "abc"}), "abc")
        generated = self.flow.get_client_device_id({})
        self.assertEqual(len(generated), 8)
        self.assertTrue(all(char in string.ascii_uppercase + string.digits for char in generated))
        self.assertEqual(self.flow._device_label({"did": "1", "nick": "Robot"}), "Robot")
        self.assertEqual(self.flow._device_label({"did": "1"}), "1")

    async def test_device_discovery_filters_and_combines_sources(self):
        devices = types.SimpleNamespace(
            mqtt=[types.SimpleNamespace(api={"did": "1", "company": "eco-ng"})],
            not_supported=[
                {"did": "2", "company": "eco-ng"},
                {"did": "3", "company": "eco"},
            ],
        )
        client = types.SimpleNamespace(get_devices=AsyncMock(return_value=devices))
        with patch.object(self.flow, "ApiClient", return_value=client):
            found, errors = await self.flow._find_devices(object())
        self.assertEqual([device["did"] for device in found], ["1", "2"])
        self.assertEqual(errors, {})

        client.get_devices.return_value = types.SimpleNamespace(mqtt=[], not_supported=[])
        with patch.object(self.flow, "ApiClient", return_value=client):
            found, errors = await self.flow._find_devices(object())
        self.assertEqual(found, [])
        self.assertEqual(errors, {"base": "no_supported_vacuums"})

    async def test_invalid_authentication_stops_validation(self):
        auth = types.SimpleNamespace(
            authenticate=AsyncMock(side_effect=self.flow.InvalidAuthenticationError())
        )
        hass = types.SimpleNamespace(async_add_executor_job=AsyncMock())
        self.assertEqual(
            await self.flow._validate_input(hass, {}, "device", auth),
            {"base": "invalid_auth"},
        )
        hass.async_add_executor_job.assert_not_awaited()

    async def test_successful_authentication_verifies_mqtt(self):
        auth = types.SimpleNamespace(authenticate=AsyncMock())
        hass = types.SimpleNamespace(async_add_executor_job=AsyncMock(return_value="config"))
        mqtt = types.SimpleNamespace(verify_config=AsyncMock())
        with patch.object(self.flow, "MqttClient", return_value=mqtt) as factory:
            errors = await self.flow._validate_input(
                hass, {"conf_country": "US"}, "device", auth
            )
        self.assertEqual(errors, {})
        factory.assert_called_once_with("config", auth)
        mqtt.verify_config.assert_awaited_once()
