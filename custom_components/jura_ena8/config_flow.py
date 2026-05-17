"""Config flow for JURA ENA 8 integration."""
from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResult

from .const import DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_NAME = "device_name"

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default="192.168.1.66"): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_DEVICE_NAME): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=3600)
        ),
    }
)


def _try_connect(host: str, port: int, timeout: float = 5.0) -> str | None:
    """
    Attempt a TCP connection to host:port.

    Returns None on success, or an error key string on failure.
    Runs in executor (blocking).
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return None
    except ConnectionRefusedError:
        return "cannot_connect"
    except socket.timeout:
        return "timeout"
    except OSError as exc:
        _LOGGER.debug("JURA config flow connect error: %s", exc)
        return "cannot_connect"


class JuraEna8ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the JURA ENA 8 configuration flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            device_name = user_input[CONF_DEVICE_NAME].strip()
            scan_interval = int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

            # Validate device_name is not empty
            if not device_name:
                errors[CONF_DEVICE_NAME] = "invalid_device_name"
            else:
                # Validate TCP reachability (just a connect — no auth required)
                error_key = await self.hass.async_add_executor_job(
                    _try_connect, host, port
                )
                if error_key:
                    errors["base"] = error_key
                else:
                    # Prevent duplicate entries for the same host:port
                    await self.async_set_unique_id(f"{host}:{port}")
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"JURA ENA 8 ({host})",
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_DEVICE_NAME: device_name,
                            CONF_SCAN_INTERVAL: scan_interval,
                            "token": "",
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
