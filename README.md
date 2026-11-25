# Ecovacs NGIOT

Unofficial Home Assistant integration for Ecovacs vacuums using the NGIOT endpoint, including DEEBOT NEO 2.0 and DEEBOT NEO 2.0 PLUS.

This is a community integration. It is not affiliated with, endorsed by, or supported by Ecovacs or Home Assistant.

## Supported Devices

This integration is for Ecovacs devices using the newer "NGIOT" API.

- DEEBOT NEO 2.0 (eyfj07)
- DEEBOT NEO 2.0 PLUS (q287s6)
- Potentially others?

Models that work with Home Assistant's built-in Ecovacs integration are not supported here. 

## What Works

- Add supported vacuums from the Home Assistant UI using an Ecovacs account with email 2FA.
- Start, pause, resume, stop, and locate the vacuum.
- Send the vacuum back to its dock.
- See the vacuum state, including cleaning, paused, returning, docked, idle, mop, and error states.
- Set the fan speed.
- View the map, position and cleaning trace.
- Clean selected rooms on the currently active map.
- View battery, cleaning statistics, consumable life, and other status sensors from live updates and status responses.
- Adjust supported settings: child lock, do-not-disturb, auto resume, carpet boost, and speaker volume.
- Reset the filter, main brush, side brush, and unit-care counters with the `deebot_ngiot.reset_consumable` service (consumable: `filter`, `rollBrush`, `sideBrush` or `unitCare`).
- View dormant and relocation-mode status.

## Limitations

- Room cleaning is limited to rooms on the currently active map.
- Some sensors may show as unknown until the vacuum reports their values; available status fields can vary by device or firmware.
- No local control - still relies on Ecovacs cloud.

<!-- ha-marketplace:exclude-start -->
## Installation (Marketplace) - Recommended

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=danielhelmstedt&repository=ha-deebot-ngiot&category=integration)

1. Add `https://github.com/danielhelmstedt/ha-deebot-ngiot` to Marketplace as a
   [custom repository](https://next.home-assistant.io/integrations/marketplace/#custom-repositories).
2. In Marketplace, find **Ecovacs Deebot NGIOT** and click **Install**.
3. Complete the setup flow.

## Installation (Manual)

1. Download this repository as a ZIP and unzip the archive.
2. Copy `custom_components/deebot_ngiot` into
   `/config/custom_components/deebot_ngiot`.
   - Create the `custom_components` folder if it does not exist.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for
   **Ecovacs Deebot NGIOT**, and complete setup.
<!-- ha-marketplace:exclude-end -->

## Troubleshooting

### Vacuum Shows Unavailable

This integration avoids marking the vacuum unavailable just because unsupported NGIOT endpoints return `None`. If it still shows unavailable:

- Restart Home Assistant after installing or updating the integration.
- Confirm the vacuum is online in the Ecovacs app.
- Confirm your Ecovacs region/country matches the account.
- Enable debug logs and check for login, MQTT, or endpoint errors.

### Start, Pause, Or Return-to-Dock Does Not Work

Start, pause, and return-to-dock use `NGIOT` official-app endpoints. If a command does not reach the vacuum:

- Confirm the robot is awake and reachable in the Ecovacs app.
- Enable debug logs.
- Open an issue with the sanitized debug log around the command.

### Region Or Login Issues

Ecovacs accounts are region-sensitive.

- Choose the same country/region you use in the Ecovacs app.
- Your Ecovacs email may be case sensitive. Enter the same email address you use in the Ecovacs Home app.
- Verify your email and password by logging into the Ecovacs Home app.
- If your Ecovacs app account uses a social login, create or set an Ecovacs password first if the app allows it.

### Debug Logging

Add this to `configuration.yaml`, then restart Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.deebot_ngiot: debug
```
When opening an issue, include the relevant `custom_components.deebot_ngiot` log lines. Remove account IDs, tokens, email addresses, and home/network details before posting logs.

## Privacy

This integration stores your Ecovacs account details in Home Assistant's config entry storage, similar to other cloud integrations. Do not post `.storage` files, tokens, passwords, or full logs in GitHub issues.

## Credits

This integration is built off the work of many others. 

Thanks to @tysonjtv for his work on [deebot-neo-2-hacs](https://github.com/tysonjtv/deebot-neo-2-hacs), and the maintainers of [deebot_client](https://github.com/DeebotUniverse/client.py) and the HA Core Ecovacs Integration.

## License

Published under an [MIT LICENSE](LICENSE).
