"""Support for AuraMon energy monitor sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_HOST,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AuraMonConfigEntry, AuraMonDataUpdateCoordinator, AuraMonDeviceData

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class AuraMonSensorEntityDescription(SensorEntityDescription):
    """Describes an AuraMon per-device sensor entity."""

    value_fn: Callable[[AuraMonDeviceData], float]


DEVICE_SENSOR_DESCRIPTIONS: tuple[AuraMonSensorEntityDescription, ...] = (
    AuraMonSensorEntityDescription(
        key="voltage",
        translation_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.voltage,
    ),
    AuraMonSensorEntityDescription(
        key="current",
        translation_key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.current,
    ),
    AuraMonSensorEntityDescription(
        key="power_factor",
        translation_key="power_factor",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.power_factor * 100,
    ),
    AuraMonSensorEntityDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.power,
    ),
)

FREQUENCY_DESCRIPTION = SensorEntityDescription(
    key="frequency",
    translation_key="frequency",
    native_unit_of_measurement=UnitOfFrequency.HERTZ,
    device_class=SensorDeviceClass.FREQUENCY,
    state_class=SensorStateClass.MEASUREMENT,
    entity_registry_enabled_default=False,
)

ENERGY_DESCRIPTION = AuraMonSensorEntityDescription(
    key="energy",
    translation_key="energy",
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    # native_value is overridden on AuraMonEnergySensor, so this is never called.
    value_fn=lambda data: data.energy_total,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AuraMonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for the passed config entry."""
    coordinator = config_entry.runtime_data
    created: set[str] = set()

    @callback
    def _create_entities(device_name: str) -> list[SensorEntity]:
        created.add(device_name)
        entities: list[SensorEntity] = [
            AuraMonSensor(coordinator, device_name, description)
            for description in DEVICE_SENSOR_DESCRIPTIONS
        ]
        entities.append(AuraMonEnergySensor(coordinator, device_name, ENERGY_DESCRIPTION))
        return entities

    entities: list[SensorEntity] = [AuraMonFrequencySensor(coordinator, FREQUENCY_DESCRIPTION)]
    for device_name in coordinator.data.devices:
        entities.extend(_create_entities(device_name))
    async_add_entities(entities)

    @callback
    def _new_data_received() -> None:
        """Add entities for any devices that appeared after initial setup."""
        new_entities: list[SensorEntity] = []
        for device_name in coordinator.data.devices:
            if device_name not in created:
                new_entities.extend(_create_entities(device_name))
        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_listener(_new_data_received)


def _device_info(coordinator: AuraMonDataUpdateCoordinator) -> DeviceInfo:
    """Return the (single) HA device representing the AuraMon controller."""
    return DeviceInfo(
        connections={(dr.CONNECTION_NETWORK_MAC, coordinator.data.mac)}
        if coordinator.data.mac
        else set(),
        identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
        name=coordinator.config_entry.title,
        manufacturer="AuraMon",
        model="AuraMon",
        sw_version=coordinator.data.version,
        configuration_url=f"http://{coordinator.config_entry.data.get(CONF_HOST, '')}",
    )


class AuraMonSensor(CoordinatorEntity[AuraMonDataUpdateCoordinator], SensorEntity):
    """Representation of a single AuraMon per-device measurement."""

    entity_description: AuraMonSensorEntityDescription

    def __init__(
        self,
        coordinator: AuraMonDataUpdateCoordinator,
        device_name: str,
        entity_description: AuraMonSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_name = device_name
        self.entity_description = entity_description
        mac = coordinator.data.mac or coordinator.config_entry.entry_id
        self._attr_unique_id = f"{mac}_{device_name}_{entity_description.key}"
        self._attr_name = device_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return _device_info(self.coordinator)

    @property
    def available(self) -> bool:
        """Return whether the underlying device data is still present."""
        return super().available and self._device_name in self.coordinator.data.devices

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Removes this entity if its device has disappeared from /status (e.g. disabled or
        unassigned in the device's configuration), rather than leaving it unavailable
        forever.
        """
        if self._device_name not in self.coordinator.data.devices:
            er.async_get(self.hass).async_remove(self.entity_id)
            return
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        data = self.coordinator.data.devices.get(self._device_name)
        if data is None:
            return None
        return self.entity_description.value_fn(data)


class AuraMonEnergySensor(AuraMonSensor, RestoreEntity):
    """Energy sensor that accumulates the device's per-interval Wh deltas.

    The AuraMon /energy endpoint only reports energy consumed during each interval, not a
    running total, so the coordinator accumulates deltas itself. This entity restores the
    last known total on startup so the running total survives Home Assistant restarts.
    """

    async def async_added_to_hass(self) -> None:
        """Restore the last known energy total into the coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (None, "unknown", "unavailable"):
            try:
                restored = float(last_state.state)
            except ValueError:
                restored = 0.0
            self.coordinator.seed_energy_total(self._device_name, restored)

    @property
    def native_value(self) -> float | None:
        """Return the accumulated energy total, bypassing the generic value_fn lookup."""
        data = self.coordinator.data.devices.get(self._device_name)
        if data is None:
            return None
        return data.energy_total


class AuraMonFrequencySensor(CoordinatorEntity[AuraMonDataUpdateCoordinator], SensorEntity):
    """Controller-level line frequency sensor."""

    def __init__(
        self,
        coordinator: AuraMonDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        mac = coordinator.data.mac or coordinator.config_entry.entry_id
        self._attr_unique_id = f"{mac}_{entity_description.key}"
        self._attr_name = "Frequency"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return _device_info(self.coordinator)

    @property
    def native_value(self) -> float:
        """Return the sensor value."""
        return self.coordinator.data.hz
