"""Binary sensors for JURA ENA 8 — maintenance and operational alerts."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JuraCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class JuraBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a JURA binary sensor derived from the machine state_key."""

    # state_keys that make this sensor True
    active_states: frozenset[str] = frozenset()
    icon_on: str = "mdi:alert-circle"
    icon_off: str = "mdi:check-circle"
    # False = detection not yet verified on real ENA 8 hardware.
    # When False, the sensor returns None (Unknown) instead of False (OK)
    # so the user knows the reading is not reliable.
    confirmed_on_ena8: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Sensor definitions — mapped from EF555 XML ALERTS + state_key
# The `key` doubles as the translation_key → entity.binary_sensor.<key>.name
# ─────────────────────────────────────────────────────────────────────────────
BINARY_SENSORS: tuple[JuraBinarySensorDescription, ...] = (
    # ── Service alerts (scheduled maintenance) ────────────────────────────────
    JuraBinarySensorDescription(
        key="cleaning_needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:washing-machine-alert",
        icon_off="mdi:washing-machine",
        active_states=frozenset({"needs_cleaning", "cleaning"}),
        confirmed_on_ena8=False,  # byte_0=05 not yet observed on real ENA 8
    ),
    JuraBinarySensorDescription(
        key="descaling_needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:water-alert",
        icon_off="mdi:water-check",
        active_states=frozenset({"descaling_needed", "descaling", "calc_clean"}),
        confirmed_on_ena8=False,  # byte_0=07 not yet observed on real ENA 8
    ),
    JuraBinarySensorDescription(
        key="filter_needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:air-filter",
        icon_off="mdi:air-filter",
        active_states=frozenset({"change_water_filter"}),
        confirmed_on_ena8=False,  # byte_0=0E not yet observed on real ENA 8
    ),
    # ── Operational alerts (need user action before brewing) ─────────────────
    JuraBinarySensorDescription(
        key="fill_water",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:water-alert",
        icon_off="mdi:water-check",
        active_states=frozenset({"fill_water"}),
    ),
    JuraBinarySensorDescription(
        key="empty_grounds",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:delete-alert",
        icon_off="mdi:delete-empty",
        active_states=frozenset({"empty_grounds"}),
    ),
    JuraBinarySensorDescription(
        key="empty_tray",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:tray-alert",
        icon_off="mdi:tray",
        active_states=frozenset({"empty_tray"}),
    ),
    JuraBinarySensorDescription(
        key="add_beans",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:seed-off",
        icon_off="mdi:seed",
        active_states=frozenset({"add_beans", "fill_beans"}),
    ),
    # ── Aggregated sensor ─────────────────────────────────────────────────────
    JuraBinarySensorDescription(
        key="maintenance_needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon_on="mdi:coffee-maker-check-outline",
        icon_off="mdi:coffee-maker-check",
        active_states=frozenset({
            "needs_cleaning", "cleaning",
            "descaling_needed", "descaling", "calc_clean",
            "change_water_filter",
            "fill_water", "empty_grounds", "empty_tray",
            "add_beans", "fill_beans", "add_ground_coffee",
            "insert_tray", "no_milk",
        }),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up JURA ENA 8 binary sensors from a config entry."""
    coordinator: JuraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        JuraMaintenanceSensor(coordinator, entry, desc)
        for desc in BINARY_SENSORS
    )


class JuraMaintenanceSensor(CoordinatorEntity[JuraCoordinator], BinarySensorEntity):
    """Binary sensor derived from the machine state_key."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
        description: JuraBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_translation_key = description.key   # → strings.json / fr.json
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """True when the machine state matches an active state.

        Returns None (Unknown) for unconfirmed sensors when the machine is not
        actively in that state — we can't guarantee the alert would be detected.
        """
        if not self.coordinator.data:
            return None
        state_key = self.coordinator.data.get("state", "")
        if state_key in self._description.active_states:
            return True
        # Unconfirmed sensor + machine not in alert state → Unknown (not OK)
        if not self._description.confirmed_on_ena8:
            return None
        return False

    @property
    def icon(self) -> str:
        return self._description.icon_on if self.is_on else self._description.icon_off

    @property
    def extra_state_attributes(self) -> dict:
        if not self._description.confirmed_on_ena8:
            return {"detection_confirmed": False}
        return {}

    @property
    def available(self) -> bool:
        return True
