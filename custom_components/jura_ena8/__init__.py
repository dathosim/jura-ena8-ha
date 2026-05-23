"""JURA ENA 8 integration for Home Assistant."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_PERSISTENT,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    TOKEN_STORAGE_FILE,
)
from .coordinator import JuraCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON, Platform.BINARY_SENSOR, Platform.SELECT, Platform.NUMBER]

CONF_DEVICE_NAME = "device_name"


# ──────────────────────────────────────────────────────────────────────────────
# Token persistence helpers (run in executor)
# ──────────────────────────────────────────────────────────────────────────────

def _load_token(path: str) -> str:
    """Read token from JSON storage file; return empty string on any error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return data.get("token", "")
    except FileNotFoundError:
        return ""
    except Exception as exc:
        _LOGGER.warning("JURA: could not read token from %s: %s", path, exc)
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# Entry setup / teardown
# ──────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up JURA ENA 8 from a config entry."""
    host: str = entry.data[CONF_HOST]
    port: int = int(entry.data[CONF_PORT])
    device_name: str = entry.data[CONF_DEVICE_NAME]
    scan_interval: int = int(
        entry.options.get(CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    )
    connection_mode: str = entry.options.get(
        CONF_CONNECTION_MODE,
        entry.data.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE),
    )

    # Load persisted token (survives HA restarts)
    token_path = hass.config.path(f".storage/{TOKEN_STORAGE_FILE}")
    persisted_token: str = await hass.async_add_executor_job(_load_token, token_path)

    # Prefer the token stored on disk over the one saved in the config entry,
    # because the machine may have refreshed it between HA restarts.
    token: str = persisted_token or entry.data.get("token", "")

    coordinator = JuraCoordinator(
        hass=hass,
        host=host,
        port=port,
        device_name=device_name,
        token=token,
        scan_interval=scan_interval,
        connection_mode=connection_mode,
    )

    # Perform the first refresh.  If the machine is offline we still set up
    # the integration — entities will show "unavailable" state rather than
    # blocking HA startup.
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as exc:
        # The coordinator's _async_update_data never raises UpdateFailed, but
        # guard here just in case.
        _LOGGER.warning(
            "JURA ENA 8 first refresh failed (machine may be offline): %s", exc
        )
    except Exception as exc:
        _LOGGER.error("JURA ENA 8 unexpected error during setup: %s", exc)
        raise ConfigEntryNotReady(f"Unexpected error: {exc}") from exc

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    # Also keep a global token reference used by the coordinator for change detection
    hass.data[DOMAIN]["token"] = coordinator.token

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start persistent connection loop if needed (after platforms are set up)
    if connection_mode == CONNECTION_MODE_PERSISTENT:
        await coordinator.async_start_persistent()

    _LOGGER.info(
        "JURA ENA 8 integration set up: host=%s port=%d device=%s",
        host,
        port,
        device_name,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: JuraCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_stop_persistent()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Clean up the global dict if no more entries remain
        if not any(
            k != "token" for k in hass.data.get(DOMAIN, {})
        ):
            hass.data.pop(DOMAIN, None)

    return unload_ok
