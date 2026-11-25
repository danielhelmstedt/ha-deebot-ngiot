"""Tests for status interpretation and NGIOT commands."""

import importlib.util
import types
import unittest
from unittest.mock import AsyncMock, Mock

from test_status_mqtt import load_controller


@unittest.skipUnless(
    importlib.util.find_spec("deebot_client"), "deebot-client is not installed"
)
class StatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = load_controller()
        cls.commands = cls.controller.GetStatus.__init__.__globals__

    def test_state_interpretation_and_precedence(self):
        state = self.commands["State"]
        cases = (
            ({"pauseSwitch": True, "status": "cleaning"}, state.PAUSED),
            ({"status": "cleaning", "chargeStatus": True}, state.CLEANING),
            ({"workMode": "auto_pause"}, state.PAUSED),
            ({"chargeStatus": True}, state.DOCKED),
            ({"chargeState": "charged"}, state.DOCKED),
            ({"workMode": "return_dock"}, state.RETURNING),
            ({"stationStatus": "docked"}, state.DOCKED),
            ({"status": "idle"}, state.IDLE),
            ({"dormant": True}, None),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self.controller.state_from_status(data), expected)

    def test_status_notifies_only_present_and_recognized_values(self):
        bus = Mock()
        data = {
            "battery": 42,
            "fanMode": "strong",
            "status": "cleaning",
            "volume": 8,
            "deviceInfo": {"fwVersion": "1.2", "mac": "aa", "rssi": "-55"},
        }
        self.controller.notify_status(bus, data)
        events = [entry.args[0] for entry in bus.notify.call_args_list]
        self.assertEqual([type(event).__name__ for event in events], [
            "StatusEvent", "BatteryEvent", "FanSpeedEvent", "StateEvent",
            "VolumeEvent", "FirmwareEvent", "NetworkInfoEvent",
        ])
        self.assertEqual(events[0].data, data)
        self.assertEqual(events[1].value, 42)
        self.assertEqual(events[2].speed, self.controller.FanSpeedLevel.MAX)
        self.assertEqual(events[3].state, self.commands["State"].CLEANING)

        bus.reset_mock()
        self.controller.notify_status(bus, {"fanMode": "unknown"})
        self.assertEqual(bus.notify.call_count, 1)

    def test_status_and_battery_response_failures_do_not_publish(self):
        bus = Mock()
        analyse = self.commands["HandlingState"].ANALYSE
        for command, response in (
            (self.controller.GetStatus(), {"body": {"code": 1}}),
            (self.controller.GetBattery(), {"body": {"code": 0, "data": {}}}),
            (self.controller.GetFanSpeed(), {"body": {"code": 0, "data": {"fanMode": "?"}}}),
        ):
            with self.subTest(command=type(command).__name__):
                self.assertEqual(command._handle_response(bus, response).state, analyse)
        bus.notify.assert_not_called()

    def test_successful_set_commands_publish_or_refresh(self):
        bus = Mock()
        success = {"body": {"code": 0}}
        failed = {"body": {"code": 1}}
        command = self.controller.SetFanSpeed(self.controller.FanSpeedLevel.NORMAL)
        command._handle_response(bus, success)
        self.assertEqual(bus.notify.call_args.args[0].speed, self.controller.FanSpeedLevel.NORMAL)
        bus.reset_mock()
        command._handle_response(bus, failed)
        bus.notify.assert_not_called()
        for command in (
            self.controller.SetWaterMode("medium"),
            self.controller.SetSwitch("50036", "autoBoost", True),
            self.controller.ResetConsumable("filter"),
        ):
            with self.subTest(command=type(command).__name__):
                bus.reset_mock()
                command._handle_response(bus, success)
                bus.request_refresh.assert_called_once_with(self.controller.StatusEvent)
                bus.reset_mock()
                command._handle_response(bus, failed)
                bus.request_refresh.assert_not_called()


@unittest.skipUnless(
    importlib.util.find_spec("deebot_client"), "deebot-client is not installed"
)
class CommandExecutionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = load_controller()
        cls.commands = cls.controller.GetStatus.__init__.__globals__

    async def test_clean_start_checks_pause_then_selects_correct_action(self):
        action = self.commands["CleanAction"]
        for data, expected_topic, expected_payload in (
            ({"pauseSwitch": True}, "40011", {"pauseSwitch": False}),
            ({"status": "paused"}, "40011", {"pauseSwitch": False}),
            ({"status": "idle"}, "40001", {"cleanMode": "smart", "cleanSwitch": True}),
        ):
            with self.subTest(data=data):
                command = self.controller.Clean(action.START)
                command._post_endpoint = AsyncMock(side_effect=[
                    {"body": {"code": 0, "data": data}},
                    {"body": {"code": 0}},
                ])
                await command._execute_api_request(None, {})
                self.assertEqual(command._post_endpoint.await_args_list[0].args[2], "10001")
                self.assertEqual(
                    command._post_endpoint.await_args_list[1].args[2:],
                    (expected_topic, expected_payload),
                )

    async def test_clean_actions_send_expected_commands(self):
        action = self.commands["CleanAction"]
        for selected, topic, payload in (
            (action.RESUME, "40011", {"pauseSwitch": False}),
            (action.PAUSE, "40009", {"pauseSwitch": True}),
            (action.STOP, "40015", {"chargeSwitch": False}),
        ):
            with self.subTest(action=selected):
                command = self.controller.Clean(selected)
                command._post_endpoint = AsyncMock(return_value={"body": {"code": 0}})
                await command._execute_api_request(None, {})
                self.assertEqual(command._post_endpoint.await_args.args[2:], (topic, payload))

    async def test_endpoint_request_constructs_device_and_command_payload(self):
        command = self.controller.SetSwitch("50036", "autoBoost", True)
        response = types.SimpleNamespace(
            raise_for_status=Mock(),
            json=AsyncMock(return_value={"body": {"code": 0}}),
        )
        context = Mock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        session = types.SimpleNamespace(post=Mock(return_value=context))
        auth = types.SimpleNamespace(
            authenticate=AsyncMock(return_value=types.SimpleNamespace(token="test-token")),
            _auth_client=types.SimpleNamespace(_config=types.SimpleNamespace(session=session)),
        )
        info = {"did": "robot", "class": "model", "resource": "resource"}
        result = await command._execute_api_request(auth, info)
        self.assertEqual(result, {"body": {"code": 0}})
        args, kwargs = session.post.call_args
        self.assertTrue(args[0].endswith("/api/iot/endpoint/control"))
        self.assertEqual(kwargs["params"]["apn"], "50036")
        self.assertEqual(kwargs["params"]["eid"], "robot")
        self.assertEqual(kwargs["json"]["body"]["data"], {"autoBoost": True})
        response.raise_for_status.assert_called_once()
