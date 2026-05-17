"""Number platform for JURA ENA 8 — water quantity control."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PRODUCT_STRENGTH_DEFAULTS, PRODUCT_WATER_LIMITS, PRODUCTS, STRENGTH_MAX, STRENGTH_MIN
from .coordinator import JuraCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)

WATER_STEP_ML = 5  # JURA encodes water as ml // 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up JURA ENA 8 number entities."""
    coordinator: JuraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        JuraWaterAmountNumber(coordinator, entry),
        JuraCoffeeStrengthNumber(coordinator, entry),
    ])


class JuraWaterAmountNumber(CoordinatorEntity[JuraCoordinator], NumberEntity):
    """Number entity to control the water quantity for the next brew.

    - Default value resets to the product default when the beverage select changes.
    - Min/max adapt to the currently selected product.
    - Step is 5 ml (JURA protocol granularity).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "water_amount"
    _attr_icon = "mdi:water"
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_native_step = float(WATER_STEP_ML)
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_water_amount"
        self._attr_device_info = _device_info(entry)

    # ── dynamic min / max based on selected product ───────────────────────────

    @property
    def native_min_value(self) -> float:
        lo, _ = PRODUCT_WATER_LIMITS.get(self.coordinator.selected_product, (0, 400))
        return float(lo)

    @property
    def native_max_value(self) -> float:
        _, hi = PRODUCT_WATER_LIMITS.get(self.coordinator.selected_product, (0, 400))
        return float(hi)

    # ── current value ─────────────────────────────────────────────────────────

    @property
    def native_value(self) -> int:
        return self.coordinator.current_water_ml

    # ── user sets a new value ─────────────────────────────────────────────────

    async def async_set_native_value(self, value: float) -> None:
        """Store the user-chosen water quantity in the coordinator."""
        # Round to nearest 5 ml step
        rounded = round(value / WATER_STEP_ML) * WATER_STEP_ML
        self.coordinator.current_water_ml = int(rounded)
        self.async_write_ha_state()

    # ── unavailable for milk_foam (no water) ──────────────────────────────────

    @property
    def available(self) -> bool:
        lo, hi = PRODUCT_WATER_LIMITS.get(self.coordinator.selected_product, (0, 400))
        return lo != hi  # milk_foam has lo == hi == 0


# ─────────────────────────────────────────────────────────────────────────────
# Coffee strength — level 1 (mild) to 10 (strong)
# ─────────────────────────────────────────────────────────────────────────────

class JuraCoffeeStrengthNumber(CoordinatorEntity[JuraCoordinator], NumberEntity):
    """Number entity to control the coffee strength (1–10).

    - Resets to the product default when the beverage select changes.
    - Unavailable for products without a grinder (hot_water, milk_foam).
    - Source: EF555 XML COFFEE_STRENGTH — 10 discrete levels (0x01–0x0A).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "coffee_strength"
    _attr_icon = "mdi:speedometer"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = float(STRENGTH_MIN)
    _attr_native_max_value = float(STRENGTH_MAX)
    _attr_native_step = 1.0
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_coffee_strength"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int | None:
        return self.coordinator.current_strength

    async def async_set_native_value(self, value: float) -> None:
        """Store the user-chosen strength level."""
        self.coordinator.current_strength = int(value)
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable for products without a grinder."""
        return PRODUCT_STRENGTH_DEFAULTS.get(self.coordinator.selected_product) is not None
