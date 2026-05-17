"""Select platform for JURA ENA 8 — choose the beverage type."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PRODUCT_STRENGTH_DEFAULTS, PRODUCTS
from .coordinator import JuraCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the JURA ENA 8 beverage select entity."""
    coordinator: JuraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([JuraCoffeeSelect(coordinator, entry)])


class JuraCoffeeSelect(CoordinatorEntity[JuraCoordinator], SelectEntity):
    """Select entity for choosing the beverage to brew."""

    _attr_has_entity_name = True
    _attr_translation_key = "coffee_type"
    _attr_icon = "mdi:coffee"
    _attr_options = list(PRODUCTS.keys())

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_coffee_select"
        self._attr_device_info = _device_info(entry)

    @property
    def current_option(self) -> str:
        return self.coordinator.selected_product

    async def async_select_option(self, option: str) -> None:
        """Update the selected beverage; reset water and strength to product defaults."""
        self.coordinator.selected_product = option
        self.coordinator.current_water_ml = PRODUCTS[option][3]
        self.coordinator.current_strength = PRODUCT_STRENGTH_DEFAULTS[option]
        # Notify all CoordinatorEntity listeners (number entities, buttons…)
        self.coordinator.async_set_updated_data(self.coordinator.data)

    @property
    def available(self) -> bool:
        return True
