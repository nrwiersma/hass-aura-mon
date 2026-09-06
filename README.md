# hass-aura-mon

A [Home Assistant](https://www.home-assistant.io/) custom integration for
[AuraMon](https://github.com/nrwiersma/aura-mon) energy monitors, installable via
[HACS](https://hacs.xyz/) as a custom repository.

It polls the AuraMon device's local HTTP API (no cloud, no account) to expose per-circuit
electrical sensors in Home Assistant, including energy sensors compatible with the
Home Assistant Energy dashboard.

## Installation

### HACS (recommended)

1. In HACS, go to **Integrations** > the **⋮** menu > **Custom repositories**.
2. Add `https://github.com/nrwiersma/hass-aura-mon` as an **Integration**.
3. Install **Aura Mon** from HACS, then restart Home Assistant.

### Manual

Copy `custom_components/aura_mon` into your Home Assistant `config/custom_components`
directory and restart Home Assistant.

## Configuration

1. In Home Assistant, go to **Settings > Devices & Services > Add Integration**.
2. Search for **Aura Mon**.
3. Enter the IP address or hostname of your AuraMon device (e.g. `aura-mon.local` or
   `192.168.1.50`). No authentication is required.

## Entities

The integration creates one Home Assistant device (identified by the AuraMon's MAC address)
with the following entities:

- **Frequency** (Hz) — line frequency, disabled by default.

For each configured/enabled circuit on the device:

- **Power** (W) — enabled by default.
- **Energy** (Wh) — enabled by default. Compatible with the Energy dashboard. The AuraMon API
  only reports the energy consumed during each logging interval, not a running total, so this
  integration accumulates those deltas into a persistent running total that survives Home
  Assistant restarts.
- **Voltage** (V) — disabled by default.
- **Current** (A) — disabled by default.
- **Power factor** (%) — disabled by default.

Disabled-by-default entities can be enabled from the entity's settings in Home Assistant if
you want them.

## Polling

The integration polls the device's `/status` and `/energy` endpoints at the device's own
datalog interval (bounded between 5 and 60 seconds), so updates stay in sync with what the
AuraMon firmware is actually logging.

## See also

- [AuraMon firmware HTTP API](https://github.com/nrwiersma/aura-mon/blob/main/firmware/API.md)

## Development / releases

- CI runs `hassfest` + HACS validation (`.github/workflows/validate.yml`) and `ruff` lint
  (`.github/workflows/lint.yml`) on every push/PR to `main`, plus a daily scheduled validation
  run.
- Dependabot keeps GitHub Actions and Python lint/dev dependencies up to date weekly. It
  deliberately does **not** bump the `homeassistant` package pin in `requirements.txt` — that
  must be updated by hand together with the `homeassistant` minimum version in `hacs.json` so
  the two stay in sync.
- Publishing a GitHub release (tag, e.g. `1.2.3`, without a `v` prefix) automatically sets
  `custom_components/aura_mon/manifest.json`'s `version` to the release tag, zips the
  integration folder, and attaches it to the release.

