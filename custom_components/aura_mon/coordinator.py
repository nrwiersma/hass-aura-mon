"""DataUpdateCoordinator for the AuraMon integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuraMonClient, DeviceEnergy, EnergyRow
from .const import (
    CATCH_UP_RESYNC_OFFSET,
    CONNECTION_ERRORS,
    DEFAULT_UPDATE_INTERVAL,
    MAX_CATCH_UP_AGE,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type AuraMonConfigEntry = ConfigEntry[AuraMonDataUpdateCoordinator]

# Placeholder used when a device has no /energy reading yet (e.g. before the first
# interval has been fetched), so live sensor values default to 0.0 rather than falling
# back to /status's instantaneous snapshot.
_EMPTY_READING = DeviceEnergy(
    name="", voltage=0.0, current=0.0, power=0.0, energy_wh=0.0, power_factor=0.0
)


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


class AuraMonDataUpdateCoordinator(DataUpdateCoordinator[AuraMonData]):
    """Coordinator that polls an AuraMon device's /status and /energy endpoints."""

    config_entry: AuraMonConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AuraMonConfigEntry, client: AuraMonClient
    ) -> None:
        """Initialize the coordinator."""
        self.client = client

        # Last datalog timestamp we've folded into the accumulated energy totals.
        self._last_ts: int | None = None
        self._interval = DEFAULT_UPDATE_INTERVAL
        # Running per-device energy totals (Wh), since the /energy endpoint only reports
        # per-interval deltas rather than a cumulative value.
        self._energy_totals: dict[str, float] = {}
        self._seeded_energy: set[str] = set()
        # Last known per-device electrical readings (V/A/PF/W) and Hz, from /energy.
        # /status only gives an instantaneous snapshot that isn't what's logged, so all
        # live readings come from /energy's interval-aggregated values instead. These
        # persist across polls where no new /energy row has landed yet.
        self._latest_readings: dict[str, DeviceEnergy] = {}
        self._latest_hz: float = 0.0

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )

    def seed_energy_total(self, device_name: str, restored_total: float) -> None:
        """Seed a device's running energy total from a restored sensor state.

        Called once by the energy sensor on startup (from RestoreEntity) so the running
        total continues from where it left off, rather than resetting to zero. Any energy
        already accumulated this session (e.g. from the very first refresh) is preserved on
        top of the restored baseline.
        """
        if device_name in self._seeded_energy:
            return
        self._seeded_energy.add(device_name)
        total = restored_total + self._energy_totals.get(device_name, 0.0)
        self._energy_totals[device_name] = total

        # The coordinator's first refresh already completed (and published `self.data`)
        # before entities are added and get a chance to restore/seed their state, so patch
        # the already-published snapshot in place too. Otherwise the sensor's very first
        # written state would show the un-seeded (low) total, and only pick up the restored
        # value after the next poll.
        if self.data is not None and device_name in self.data.devices:
            self.data.devices[device_name].energy_total = total

    async def _async_update_data(self) -> AuraMonData:
        """Fetch the latest status and any new energy data from the device."""
        try:
            status = await self.client.get_status()
        except CONNECTION_ERRORS as err:
            raise UpdateFailed(f"Error communicating with device: {err}") from err

        datalog = status.datalog
        interval = datalog.interval if datalog and datalog.interval else DEFAULT_UPDATE_INTERVAL
        interval = max(MIN_UPDATE_INTERVAL, min(interval, MAX_UPDATE_INTERVAL))
        if interval != self._interval:
            self._interval = interval
            self.update_interval = timedelta(seconds=interval)

        last_ts = datalog.last_ts if datalog else 0

        # If the device's last datalog timestamp went backwards (reboot / SD card reset),
        # resync instead of trying to fetch a now-invalid range.
        if self._last_ts is not None and last_ts < self._last_ts:
            _LOGGER.debug("Datalog timestamp regressed, resyncing energy totals")
            self._last_ts = None

        # If we've fallen too far behind (e.g. HA was stopped for a while), don't bother
        # replaying the whole gap row-by-row - just resync close to the device's current
        # datalog timestamp. Losing some history is fine; we don't want long/expensive
        # catch-up reads for no reason.
        if self._last_ts is not None and last_ts - self._last_ts > MAX_CATCH_UP_AGE:
            _LOGGER.debug("Last sync is over an hour old, skipping ahead")
            self._last_ts = max(last_ts - CATCH_UP_RESYNC_OFFSET, 0)

        if self._last_ts is None:
            # First run (or resync): only fetch the most recent interval so we don't replay
            # the device's entire history into the accumulated energy totals.
            start = max(last_ts - interval, 0)
        else:
            start = self._last_ts
        end = last_ts

        rows: list[EnergyRow] = []
        if last_ts and start < end:
            try:
                rows = await self.client.get_energy(
                    start=start, end=end, interval=interval
                )
            except CONNECTION_ERRORS as err:
                raise UpdateFailed(f"Error fetching energy data: {err}") from err

        if rows:
            for row in rows:
                for name, energy in row.devices.items():
                    self._energy_totals[name] = (
                        self._energy_totals.get(name, 0.0) + energy.energy_wh
                    )
                if row.timestamp > (self._last_ts or 0):
                    self._last_ts = row.timestamp
        elif last_ts and start < end:
            # The device had nothing for the requested window (e.g. a gap in the log).
            # Move the window forward by its own size rather than re-requesting the same
            # empty range forever.
            self._last_ts = min(start + (end - start), last_ts)

        if self._last_ts is None:
            self._last_ts = last_ts

        if rows:
            # Merge rather than replace: a malformed/short CSV row (see
            # AuraMonClient._parse_energy_csv) can omit a device that's still present in
            # /status, and we don't want that device's reading to flicker to 0 because of it.
            self._latest_readings.update(rows[-1].devices)
            self._latest_hz = rows[-1].hz

        # /status is only used here for the set of configured device names; the actual
        # electrical readings come exclusively from /energy (see _latest_readings above),
        # since /energy's interval-aggregated values match what's logged, unlike /status's
        # instantaneous snapshot.
        devices: dict[str, AuraMonDeviceData] = {
            dev.name: AuraMonDeviceData(
                name=dev.name,
                voltage=self._latest_readings.get(dev.name, _EMPTY_READING).voltage,
                current=self._latest_readings.get(dev.name, _EMPTY_READING).current,
                power_factor=self._latest_readings.get(
                    dev.name, _EMPTY_READING
                ).power_factor,
                power=self._latest_readings.get(dev.name, _EMPTY_READING).power,
                energy_total=self._energy_totals.get(dev.name, 0.0),
            )
            for dev in status.devices
        }

        return AuraMonData(
            version=status.version,
            mac=status.network.mac if status.network else "",
            hz=self._latest_hz,
            devices=devices,
        )
