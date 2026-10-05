"""Push-driven DataUpdateCoordinator for the AuraMon integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuraMonClient, StatusResponse
from .const import CONNECTION_ERRORS, STALE_CHECK_INTERVAL, STALE_THRESHOLD, STATUS_REFRESH_INTERVAL

_LOGGER = logging.getLogger(__name__)

type AuraMonConfigEntry = ConfigEntry[AuraMonDataUpdateCoordinator]


@dataclass
class _Reading:
    """Latest known electrical reading for a single device, from a webhook payload."""

    voltage: float
    current: float
    power: float
    power_factor: float


@dataclass
class AuraMonDeviceData:
    """Latest known values for a single AuraMon device."""

    name: str
    voltage: float
    current: float
    power_factor: float
    power: float
    energy_total: float


@dataclass
class AuraMonData:
    """Snapshot of coordinator data used by entities."""

    version: str
    mac: str
    hz: float
    devices: dict[str, AuraMonDeviceData] = field(default_factory=dict)
    # True once no webhook payload has landed for STALE_THRESHOLD seconds - entities should
    # treat this as "no longer available" regardless of their last known values.
    stale: bool = False


class AuraMonDataUpdateCoordinator(DataUpdateCoordinator[AuraMonData]):
    """Coordinator fed by a push webhook rather than polling.

    Live readings arrive exclusively via `handle_payload()`, called from the webhook
    handler. `GET /status` (device identity: version/mac/network/device list) is only
    fetched:

    - Once, during `async_config_entry_first_refresh()` at setup.
    - On-demand (debounced) when a webhook payload names a device not currently known.
    - Unconditionally every `STATUS_REFRESH_INTERVAL` seconds, to catch firmware/version
      changes even while payloads keep flowing normally for an unchanged device set.
    - On-demand when no webhook payload has landed for `STALE_THRESHOLD` seconds, both as a
      reachability check and to re-sync the device list once data resumes.
    """

    config_entry: AuraMonConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AuraMonConfigEntry, client: AuraMonClient
    ) -> None:
        """Initialize the coordinator."""
        self.client = client

        self._status: StatusResponse | None = None
        self._known_devices: set[str] = set()
        self._status_refresh_inflight = False

        # Running per-device energy totals (Wh). The webhook payload's `wh` field is a
        # per-interval delta, not a running total (see WEBHOOK_INGESTION.md §2), so it's
        # accumulated here exactly like the old polling coordinator did with `/energy`.
        self._energy_totals: dict[str, float] = {}
        self._seeded_energy: set[str] = set()
        self._latest_readings: dict[str, _Reading] = {}
        self._latest_hz: float = 0.0

        self._last_webhook_at: float | None = None
        self._stale = False

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            config_entry=entry,
            name=entry.title,
            # Push-driven: there is no polling update_interval. Data is only published via
            # async_set_updated_data(), from handle_payload() or a status refresh.
            update_interval=None,
        )

        entry.async_on_unload(
            async_track_time_interval(
                hass, self._async_periodic_status_refresh, timedelta(seconds=STATUS_REFRESH_INTERVAL)
            )
        )
        entry.async_on_unload(
            async_track_time_interval(
                hass, self._async_check_stale, timedelta(seconds=STALE_CHECK_INTERVAL)
            )
        )

    def seed_energy_total(self, device_name: str, restored_total: float) -> None:
        """Seed a device's running energy total from a restored sensor state.

        Called once by the energy sensor on startup (from RestoreEntity) so the running
        total continues from where it left off, rather than resetting to zero. Any energy
        already accumulated this session (e.g. from a webhook payload that landed before
        entities finished restoring) is preserved on top of the restored baseline.
        """
        if device_name in self._seeded_energy:
            return
        self._seeded_energy.add(device_name)
        total = restored_total + self._energy_totals.get(device_name, 0.0)
        self._energy_totals[device_name] = total

        if self.data is not None and device_name in self.data.devices:
            self.data.devices[device_name].energy_total = total

    async def _async_update_data(self) -> AuraMonData:
        """Fetch the initial /status snapshot. Only ever called once, at setup."""
        try:
            status = await self.client.get_status()
        except CONNECTION_ERRORS as err:
            raise UpdateFailed(f"Error communicating with device: {err}") from err

        self._status = status
        self._known_devices = {dev.name for dev in status.devices}
        # Treat setup as "contact", so the staleness watchdog's 5-minute window starts now
        # rather than firing immediately if the firmware takes a moment to start pushing.
        self._last_webhook_at = time.monotonic()

        return self._build_snapshot()

    def handle_payload(self, body: dict) -> None:
        """Handle a parsed webhook payload (see WEBHOOK_INGESTION.md §2)."""
        self._last_webhook_at = time.monotonic()
        self._stale = False

        if "hz" in body:
            try:
                self._latest_hz = float(body["hz"])
            except (TypeError, ValueError):
                _LOGGER.debug("Ignoring non-numeric hz in webhook payload: %r", body.get("hz"))

        unknown_device = False
        for device in body.get("devices") or []:
            name = device.get("name")
            if not name:
                continue
            try:
                reading = _Reading(
                    voltage=float(device["volts"]),
                    current=float(device["amps"]),
                    power=float(device["watts"]),
                    power_factor=float(device["pf"]),
                )
                wh = float(device["wh"])
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Ignoring malformed device entry in webhook payload: %r", device)
                continue

            self._latest_readings[name] = reading
            self._energy_totals[name] = self._energy_totals.get(name, 0.0) + wh

            if name not in self._known_devices:
                unknown_device = True
                # Show the new device immediately rather than waiting for the debounced
                # /status refresh below to complete; a later refresh will prune it again if
                # it turns out not to be a real configured device.
                self._known_devices.add(name)

        self.async_set_updated_data(self._build_snapshot())

        if unknown_device:
            self._schedule_status_refresh("unknown device in webhook payload")

    def _build_snapshot(self) -> AuraMonData:
        """Build a snapshot from the current /status cache and latest webhook readings."""
        devices: dict[str, AuraMonDeviceData] = {}
        for name in self._known_devices | set(self._latest_readings):
            reading = self._latest_readings.get(name)
            devices[name] = AuraMonDeviceData(
                name=name,
                voltage=reading.voltage if reading else 0.0,
                current=reading.current if reading else 0.0,
                power_factor=reading.power_factor if reading else 0.0,
                power=reading.power if reading else 0.0,
                energy_total=self._energy_totals.get(name, 0.0),
            )

        return AuraMonData(
            version=self._status.version if self._status else "",
            mac=self._status.network.mac if self._status and self._status.network else "",
            hz=self._latest_hz,
            devices=devices,
            stale=self._stale,
        )

    def _schedule_status_refresh(self, reason: str) -> None:
        """Kick off a debounced, non-blocking /status refresh."""
        if self._status_refresh_inflight:
            return
        self._status_refresh_inflight = True

        async def _run() -> None:
            try:
                await self._async_refresh_status(reason)
            finally:
                self._status_refresh_inflight = False

        self.config_entry.async_create_task(self.hass, _run(), f"aura_mon status refresh ({reason})")

    async def _async_refresh_status(self, reason: str) -> None:
        """Refresh /status and merge the result into the published snapshot."""
        try:
            status = await self.client.get_status()
        except CONNECTION_ERRORS as err:
            _LOGGER.warning("Failed to refresh AuraMon status (%s): %s", reason, err)
            return

        self._status = status
        new_known = {dev.name for dev in status.devices}

        # /status is authoritative for which devices exist. Prune any device that's no
        # longer configured on the firmware, so its entities get removed (mirrors the old
        # polling coordinator's behavior, where `devices` was always built from /status).
        for name in list(self._latest_readings):
            if name not in new_known:
                self._latest_readings.pop(name, None)
                self._energy_totals.pop(name, None)
        self._known_devices = new_known

        self.async_set_updated_data(self._build_snapshot())

    async def _async_periodic_status_refresh(self, _now) -> None:
        """Timer callback: unconditional periodic /status refresh."""
        self._schedule_status_refresh("periodic refresh")

    async def _async_check_stale(self, _now) -> None:
        """Timer callback: mark unavailable if no webhook payload has landed recently."""
        if self._last_webhook_at is None or self._stale:
            return
        if time.monotonic() - self._last_webhook_at < STALE_THRESHOLD:
            return

        self._stale = True
        if self.data is not None:
            self.async_set_updated_data(self._build_snapshot())
        # Also check reachability/device-list changes while we're at it - the outcome
        # doesn't affect `stale` (no push = stale, regardless of reachability).
        self._schedule_status_refresh("no webhook payload received recently")
