"""Tests for NGIOT controller initialization and capabilities."""

import importlib.util
import unittest
from unittest.mock import AsyncMock, patch

from test_status_mqtt import load_controller


@unittest.skipUnless(
    importlib.util.find_spec("deebot_client"), "deebot-client is not installed"
)
class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = load_controller()

    def test_device_selection_only_accepts_ngiot(self):
        self.assertTrue(self.controller.is_ngiot_candidate({"company": "eco-ng"}))
        self.assertFalse(self.controller.is_ngiot_candidate({"company": "eco"}))
        self.assertFalse(self.controller.is_ngiot_candidate({}))

    def test_switch_capabilities_generate_correct_commands(self):
        capabilities = self.controller.get_static_device_info().capabilities
        fields = {
            "childLock": "50038",
            "disturbSwitch": "50019",
            "breakCleanSwitch": "50025",
            "autoBoost": "50036",
        }
        self.assertEqual(set(capabilities.switches), set(fields))
        for field, topic in fields.items():
            with self.subTest(field=field):
                command = capabilities.switches[field].set(True)
                self.assertEqual(command._apn, topic)
                self.assertEqual(command._args, {field: True})

    def test_life_span_resets_use_ngiot_consumable_values(self):
        lifespan = self.controller.LifeSpan
        for member, expected in (
            (lifespan.SIDE_BRUSH, "sideBrush"),
            (lifespan.BRUSH, "rollBrush"),
            (lifespan.FILTER, "filter"),
            (lifespan.UNIT_CARE, "unitCare"),
        ):
            with self.subTest(member=member):
                command = self.controller._reset_life_span(member)
                self.assertEqual(command._args, {"resetConsumable": expected})
        with self.assertRaises(ValueError):
            self.controller._reset_life_span(lifespan.DUST_BAG)


@unittest.skipUnless(
    importlib.util.find_spec("deebot_client"), "deebot-client is not installed"
)
class DeviceInitializationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = load_controller()

    async def test_primes_until_status_is_received(self):
        device = self.controller.DeviceController.__new__(
            self.controller.DeviceController
        )
        device.execute_command = AsyncMock(side_effect=[
            {"body": {}},
            {"body": {"data": {"battery": 42}}},
        ])
        with (
            patch.object(self.controller.Device, "initialize", new_callable=AsyncMock) as parent,
            patch.object(self.controller.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            await device.initialize("client")
        parent.assert_awaited_once_with("client")
        self.assertEqual(device.execute_command.await_count, 2)
        sleep.assert_awaited_once_with(1)

    async def test_warns_when_initial_status_never_arrives(self):
        device = self.controller.DeviceController.__new__(
            self.controller.DeviceController
        )
        device.execute_command = AsyncMock(return_value={"body": {}})
        with (
            patch.object(self.controller.Device, "initialize", new_callable=AsyncMock),
            patch.object(self.controller.asyncio, "sleep", new_callable=AsyncMock),
            self.assertLogs(self.controller.__name__, level="WARNING"),
        ):
            await device.initialize("client")
        self.assertEqual(
            device.execute_command.await_count, self.controller.STATUS_PRIME_ATTEMPTS
        )
