"""Constants for the Ecovacs NGIOT integration."""

from deebot_client.events import FanSpeedLevel, LifeSpan

DOMAIN = "deebot_ngiot"

CONF_DEVICE_DID = "device_did"
CONF_VERIFICATION_CODE = "verification_code"

NGIOT_COMPANY = "eco-ng"

STATUS_PRIME_ATTEMPTS = 5

TOPICS = {
    "status_family": "10000",
    "status_response": "10001",
    "map_family": "30000",
    "map_request": "30001",
    "start_clean": "40001",
    "clean_area": "40007",
    "pause_clean": "40009",
    "resume_clean": "40011",
    "charge": "40013",
    "stop_clean": "40015",
    "play_sound": "40019",
    "set_fan_speed": "50011",
    "set_water_mode": "50013",
    "reset_consumable": "50017",
    "set_volume": "50023",
    "child_lock": "50038",
    "do_not_disturb": "50019",
    "break_clean": "50025",
    "auto_boost": "50036",
}

FAN_SPEED_TO_FAN_MODE = {
    FanSpeedLevel.QUIET: "quiet",
    FanSpeedLevel.NORMAL: "auto",
    FanSpeedLevel.MAX: "strong",
    FanSpeedLevel.MAX_PLUS: "max",
}

STATUS_FIELDS = (
    "battery",
    "chargeStatus",
    "chargeState",
    "charging",
    "isCharging",
    "isDocked",
    "pauseSwitch",
    "status",
    "workMode",
    "stationStatus",
    "stationType",
    "fanMode",
    "waterMode",
    "mopState",
    "cleanTime",
    "cleanArea",
    "cleanCount",
    "cleanAreaTotal",
    "cleanCountTotal",
    "cleanTimeTotal",
    "volume",
    "childLock",
    "disturbSwitch",
    "disturbTimeSet",
    "breakCleanStatus",
    "breakCleanSwitch",
    "autoBoost",
    "carpetCleanPrefer",
    "safeModeSwitch",
    "relocateSwitch",
    "dormant",
    "unitSet",
    "timeZone",
    "consumables",
    "deviceInfo",
    "otaData",
    "voiceData",
    "error",
)

CONSUMABLES = {
    "side_brush": ("sideBrush", LifeSpan.SIDE_BRUSH),
    "roll_brush": ("rollBrush", LifeSpan.BRUSH),
    "filter": ("filter", LifeSpan.FILTER),
    "unit_care": ("unitCare", LifeSpan.UNIT_CARE),
}

