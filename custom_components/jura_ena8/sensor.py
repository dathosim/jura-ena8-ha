"""Sensor platform for JURA ENA 8 — machine state."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MACHINE_STATE_LABELS, STATE_UNAVAILABLE
from .coordinator import JuraCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up JURA ENA 8 sensor from a config entry."""
    coordinator: JuraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([JuraMachineSensor(coordinator, entry)])


class JuraMachineSensor(CoordinatorEntity[JuraCoordinator], SensorEntity):
    """Sensor representing the current machine state of the JURA ENA 8."""

    _attr_has_entity_name = True
    _attr_translation_key = "status"
    _attr_icon = "mdi:coffee-maker"
    _attr_device_class = None  # Free-form string state

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = _device_info(entry)

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def native_value(self) -> str:
        """Return a human-readable machine state string."""
        if self.coordinator.data is None:
            return MACHINE_STATE_LABELS.get(STATE_UNAVAILABLE, STATE_UNAVAILABLE)
        raw_state = self.coordinator.data.get("state", STATE_UNAVAILABLE)
        return MACHINE_STATE_LABELS.get(raw_state, raw_state.replace("_", " ").title())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        raw = None
        state_key = STATE_UNAVAILABLE
        if self.coordinator.data:
            raw = self.coordinator.data.get("raw")
            state_key = self.coordinator.data.get("state", STATE_UNAVAILABLE)
        return {
            "state_key": state_key,
            "raw_response": raw,
        }

    @property
    def available(self) -> bool:
        """
        The sensor entity is always 'available' in HA terms so that users can
        see its state even when the machine is offline.  The state value itself
        will be 'Unavailable' when the machine cannot be reached.
        """
        return True


# ──────────────────────────────────────────────────────────────────────────────
# Shared device info
# ──────────────────────────────────────────────────────────────────────────────

def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build the shared DeviceInfo for all JURA ENA 8 entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="JURA ENA 8",
        manufacturer="JURA",
        model="ENA 8",
        configuration_url=f"http://{entry.data['host']}",
    )
