"""Capabilities, device and controller for the NGIOT endpoint."""

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.capabilities import Capabilities as ClientCapabilities
from deebot_client.capabilities import (
    CapabilityClean,
    CapabilityCleanAction,
    CapabilityEvent,
    CapabilityExecute,
    CapabilityLifeSpan,
    CapabilityMap,
    CapabilitySet,
    CapabilitySettings,
    CapabilitySetTypes,
    CapabilityStats,
    DeviceType,
)
from deebot_client.command import Command
from deebot_client.const import DataType
from deebot_client.device import Device
from deebot_client.events import (
    AvailabilityEvent,
    BatteryEvent,
    ErrorEvent,
    FanSpeedEvent,
    FanSpeedLevel,
    LifeSpan,
    LifeSpanEvent,
    NetworkInfoEvent,
    ReportStatsEvent,
    RoomsEvent,
    StateEvent,
    StatsEvent,
    TotalStatsEvent,
    VolumeEvent,
)
from deebot_client.events.map import (
    CachedMapInfoEvent,
    MapChangedEvent,
    MapTraceEvent,
    PositionsEvent,
)
from deebot_client.exceptions import (
    DeebotError,
    DeviceVerificationRequiredError,
    InvalidAuthenticationError,
)
from deebot_client.message import MessagePayloadType
from deebot_client.models import ApiDeviceInfo, DeviceInfo, StaticDeviceInfo
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client

from .commands import (
    Charge,
    Clean,
    CleanArea,
    GetBattery,
    GetCachedMapInfo,
    GetFanSpeed,
    GetMapInfo,
    GetStatus,
    PlaySound,
    ResetConsumable,
    SetFanSpeed,
    SetSwitch,
    SetVolume,
    SetWaterMode,
    StatusEvent,
    notify_status,
    state_from_status,
)
from .const import (
    CONF_DEVICE_DID,
    CONSUMABLES,
    DOMAIN,
    NGIOT_COMPANY,
    STATUS_PRIME_ATTEMPTS,
    TOPICS,
)
from .map import (
    decode_map_raster,
    decode_map_trace_data,
    parse_robot_position,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Capabilities(ClientCapabilities):
    """Capabilities extended with the NGIOT status snapshot."""

    status: CapabilityEvent[StatusEvent]
    reset_consumable: CapabilityExecute[ResetConsumable]
    switches: dict[str, CapabilitySet[StatusEvent, [bool]]]
    water_mode: CapabilitySet[StatusEvent, [str]]


def status_capability(
    capabilities: ClientCapabilities,
) -> CapabilityEvent[StatusEvent] | None:
    """Return the status capability if the device has one."""
    return getattr(capabilities, "status", None)


def _reset_life_span(life_span: LifeSpan) -> Command:
    for consumable, name in CONSUMABLES.items():
        if name == life_span.name:
            return ResetConsumable(consumable)
    raise ValueError(f"Unsupported consumable {life_span.name}")


def _switch_capability(field: str, apn: str) -> CapabilitySet[StatusEvent, [bool]]:
    return CapabilitySet(
        StatusEvent,
        [GetStatus()],
        lambda enabled: SetSwitch(apn, field, enabled),
    )


def get_static_device_info() -> StaticDeviceInfo:
    """Return the static device info for NGIOT vacuums."""
    capabilities = Capabilities(
        DeviceType.VACUUM,
        availability=CapabilityEvent(AvailabilityEvent, []),
        battery=CapabilityEvent(BatteryEvent, [GetBattery()]),
        charge=CapabilityExecute(Charge),
        clean=CapabilityClean(
            action=CapabilityCleanAction(command=Clean, area=CleanArea),
        ),
        custom=None,  # type: ignore[arg-type]
        error=CapabilityEvent(ErrorEvent, []),
        fan_speed=CapabilitySetTypes(
            event=FanSpeedEvent,
            get=[GetFanSpeed()],
            set=SetFanSpeed,
            types=(
                FanSpeedLevel.QUIET,
                FanSpeedLevel.NORMAL,
                FanSpeedLevel.MAX,
                FanSpeedLevel.MAX_PLUS,
            ),
        ),
        life_span=CapabilityLifeSpan(
            event=LifeSpanEvent, get=[], types=(), reset=_reset_life_span
        ),
        map=CapabilityMap(
            cached_info=CapabilityEvent(CachedMapInfoEvent, [GetCachedMapInfo()]),
            changed=CapabilityEvent(MapChangedEvent, []),
            info=CapabilityExecute(GetMapInfo),
            position=CapabilityEvent(PositionsEvent, []),
            rooms=CapabilityEvent(RoomsEvent, [GetCachedMapInfo()]),
            set=CapabilityExecute(GetMapInfo),
            trace=CapabilityEvent(MapTraceEvent, []),
        ),
        network=CapabilityEvent(NetworkInfoEvent, []),
        play_sound=CapabilityExecute(PlaySound),
        settings=CapabilitySettings(
            volume=CapabilitySet(VolumeEvent, [GetStatus()], SetVolume)
        ),
        water_mode=CapabilitySet(StatusEvent, [GetStatus()], SetWaterMode),
        state=CapabilityEvent(StateEvent, [GetStatus()]),
        stats=CapabilityStats(
            clean=CapabilityEvent(StatsEvent, []),
            report=CapabilityEvent(ReportStatsEvent, []),
            total=CapabilityEvent(TotalStatsEvent, []),
        ),
        status=CapabilityEvent(StatusEvent, [GetStatus()]),
        reset_consumable=CapabilityExecute(ResetConsumable),
        switches={
            field: _switch_capability(field, TOPICS[topic])
            for field, topic in (
                ("childLock", "child_lock"),
                ("disturbSwitch", "do_not_disturb"),
                ("breakCleanSwitch", "break_clean"),
                ("autoBoost", "auto_boost"),
            )
        },
    )
    return StaticDeviceInfo(DataType.JSON, capabilities)


class DeviceController(Device):
    """Device that handles the numeric NGIOT live message families."""

    async def initialize(self, client: MqttClient) -> None:
        """Initialize the device and prime it with a status snapshot."""
        await super().initialize(client)

        # The first request often races the MQTT subscription and returns nothing
        for _ in range(STATUS_PRIME_ATTEMPTS):
            response = await self.execute_command(GetStatus())
            if response.get("body", {}).get("data"):
                return
            await asyncio.sleep(1)
        _LOGGER.warning("Could not fetch an initial status snapshot")

    def _handle_message(
        self, message_name: str, message_data: MessagePayloadType
    ) -> None:
        super()._handle_message(message_name, message_data)
        if message_name in (
            TOPICS["status_family"],
            TOPICS["map_family"],
        ):
            self._handle_live_payload(message_name, message_data)

    def _handle_live_payload(
        self, topic_family: str, payload: MessagePayloadType
    ) -> None:
        if isinstance(payload, dict):
            data = payload
        else:
            try:
                data = json.loads(payload)
            except TypeError, json.JSONDecodeError:
                _LOGGER.debug("Ignoring invalid live payload for %s", topic_family)
                return
        body = data.get("body") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            _LOGGER.warning("Ignoring invalid body for topic %s", topic_family)
            return
        body_data = body.get("data", {})
        if not isinstance(body_data, dict):
            _LOGGER.warning("Ignoring invalid data for topic %s", topic_family)
            return

        if topic_family == TOPICS["status_family"]:
            notify_status(self.events, body_data)
            if state_from_status(body_data) is None:
                # Partial pushes (e.g. scheduled cleans) can't tell the state
                self.events.request_refresh(StateEvent)
            return

        if isinstance(map_data := body_data.get("mapData"), dict) and (
            raster := decode_map_raster(map_data)
        ):
            self.events.notify(raster)
        map_id = str(body_data.get("mapId", ""))
        if isinstance(trace_data := body_data.get("mapTraceData"), dict) and (
            trace := decode_map_trace_data(trace_data, map_id)
        ):
            self.events.notify(trace)
        if position := parse_robot_position(body_data.get("pos"), map_id):
            self.events.notify(position)


def is_ngiot_candidate(device: ApiDeviceInfo) -> bool:
    """Return True for next-gen devices that the Ecovacs core integration skips."""
    return device.get("company") == NGIOT_COMPANY


class AccountController:
    """Controller of the devices of one Ecovacs account."""

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize controller."""
        self._hass = hass
        self._devices: list[DeviceController] = []
        self._device_did: str | None = config.get(CONF_DEVICE_DID)
        self._device_id: str = config[CONF_DEVICE_ID]
        self._country: str = config[CONF_COUNTRY]

        self._authenticator = Authenticator(
            create_rest_config(
                aiohttp_client.async_get_clientsession(self._hass),
                device_id=self._device_id,
                alpha_2_country=self._country,
            ),
            config[CONF_USERNAME],
            md5(config[CONF_PASSWORD]),
        )
        self._api_client = ApiClient(self._authenticator)
        self._mqtt_client: MqttClient | None = None

    async def initialize(self) -> None:
        """Init controller."""
        try:
            devices = await self._api_client.get_devices()
            api_infos = [
                *(info.api for info in devices.mqtt),
                *devices.not_supported,
            ]
            candidates = [
                info
                for info in api_infos
                if is_ngiot_candidate(info)
                and (self._device_did is None or info["did"] == self._device_did)
            ]
            if not candidates:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN, translation_key="no_matching_device"
                )

            mqtt = await self._get_mqtt_client()
            for api_info in candidates:
                device = DeviceController(
                    DeviceInfo(api_info, get_static_device_info()),
                    self._authenticator,
                )
                await device.initialize(mqtt)
                self._devices.append(device)
        except DeviceVerificationRequiredError as ex:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="device_verification_required"
            ) from ex
        except InvalidAuthenticationError as ex:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from ex
        except DeebotError as ex:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="setup_error"
            ) from ex

        _LOGGER.debug("Controller initialize complete")

    async def teardown(self) -> None:
        """Disconnect controller."""
        for device in self._devices:
            await device.teardown()
        if self._mqtt_client is not None:
            await self._mqtt_client.disconnect()
        await self._authenticator.teardown()

    async def _get_mqtt_client(self) -> MqttClient:
        """Return validated MQTT client."""
        if self._mqtt_client is None:
            config = await self._hass.async_add_executor_job(
                lambda: create_mqtt_config(
                    device_id=self._device_id, country=self._country
                )
            )
            mqtt = MqttClient(config, self._authenticator)
            await mqtt.verify_config()
            self._mqtt_client = mqtt

        return self._mqtt_client

    @property
    def devices(self) -> list[DeviceController]:
        """Return devices."""
        return self._devices
