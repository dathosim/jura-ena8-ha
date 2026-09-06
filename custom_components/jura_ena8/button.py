"""Button platform for JURA ENA 8.

Two kinds of buttons:
  - JuraMakeCoffeeButton : "Préparer" — brews the beverage chosen in the select
                           entity, using the water quantity from the number entity.
  - JuraBrewButton       : shortcut buttons for espresso, coffee, hot_water
                           (use their product default water quantity).
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.const import EntityCategory

from .const import DOMAIN, PRODUCTS
from .coordinator import JuraCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)

# Shortcut buttons to keep (subset of PRODUCTS keys)
SHORTCUT_PRODUCTS = ["espresso", "coffee", "hot_water"]

_PRODUCT_ICONS: dict[str, str] = {
    "hot_water": "mdi:cup-water",
    "milk_foam": "mdi:cup",
}
_DEFAULT_ICON = "mdi:coffee"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up JURA ENA 8 buttons from a config entry."""
    coordinator: JuraCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_info = _device_info(entry)

    entities: list = [
        # ── Main "Préparer" button ────────────────────────────────────────────
        JuraMakeCoffeeButton(coordinator, entry, device_info),
        # ── Shortcut buttons (espresso / coffee / hot_water) ─────────────────
        *[
            JuraBrewButton(coordinator, entry, key, device_info)
            for key in SHORTCUT_PRODUCTS
        ],
        # ── Diagnostic button ─────────────────────────────────────────────────
        JuraDiagnosticButton(coordinator, entry, device_info),
    ]
    async_add_entities(entities)


# ─────────────────────────────────────────────────────────────────────────────
# "Préparer" — brews the selected beverage with the current water quantity
# ─────────────────────────────────────────────────────────────────────────────

class JuraMakeCoffeeButton(CoordinatorEntity[JuraCoordinator], ButtonEntity):
    """Single button that brews using the select + number entities."""

    _attr_has_entity_name = True
    _attr_translation_key = "make_coffee"
    _attr_icon = "mdi:coffee-maker"

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_make_coffee"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        """Brew the currently selected product with the current water quantity."""
        product = self.coordinator.selected_product
        water_ml = self.coordinator.current_water_ml
        _LOGGER.info("JURA: make coffee — product='%s' water=%d ml", product, water_ml)
        await self.coordinator.async_brew(product, water_ml=water_ml)


# ─────────────────────────────────────────────────────────────────────────────
# Shortcut buttons — use each product's default water quantity
# ─────────────────────────────────────────────────────────────────────────────

class JuraBrewButton(CoordinatorEntity[JuraCoordinator], ButtonEntity):
    """Shortcut button for one product at its default water quantity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
        product_key: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._product_key = product_key
        self._attr_unique_id = f"{entry.entry_id}_{product_key}"
        self._attr_translation_key = product_key
        self._attr_icon = _PRODUCT_ICONS.get(product_key, _DEFAULT_ICON)
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        """Brew this product at its default water quantity."""
        default_water = PRODUCTS[self._product_key][3]
        _LOGGER.info(
            "JURA: shortcut '%s' — default water=%d ml", self._product_key, default_water
        )
        await self.coordinator.async_brew(self._product_key, water_ml=default_water)


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostic button — captures current frame as a HA persistent notification
# ─────────────────────────────────────────────────────────────────────────────

class JuraDiagnosticButton(CoordinatorEntity[JuraCoordinator], ButtonEntity):
    """Capture the current machine frame as a persistent HA notification.

    Press this when the machine display shows a maintenance alert
    (cleaning, descaling, filter change…) to capture the raw frame
    and help identify the corresponding protocol bytes.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "capture_diagnostic"
    _attr_icon = "mdi:bug"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: JuraCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_capture_diagnostic"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        """Snapshot the current frame and create a persistent HA notification."""
        data = self.coordinator.data or {}
        raw = data.get("raw", "N/A")
        state_key = data.get("state", "N/A")
        frame_bytes = data.get("bytes", {})

        bytes_str = "\n".join(
            f"  {k}: {v}" for k, v in sorted(frame_bytes.items())
        )

        message = (
            f"**State key:** `{state_key}`\n"
            f"**Raw frame:** `{raw}`\n"
            f"**Bytes:**\n```\n{bytes_str}\n```\n\n"
            f"Note what the machine display shows at this moment "
            f"and share this with your developer to map new states."
        )

        _LOGGER.warning(
            "JURA diagnostic capture — state=%s raw=%s bytes=%s",
            state_key, raw, frame_bytes,
        )

        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "JURA ENA 8 — Diagnostic Frame",
                "message": message,
                "notification_id": "jura_diagnostic",
            },
        )
