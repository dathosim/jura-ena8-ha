"""Config flow for JURA ENA 8 integration."""
from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_PERSISTENT,
    CONNECTION_MODE_POLLING,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_NAME = "device_name"


def _schema_for_mode(mode: str) -> vol.Schema:
    """Return the config schema — scan_interval only shown in polling mode."""
    fields: dict = {
        vol.Required(CONF_HOST, default="192.168.1.66"): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_DEVICE_NAME): str,
        vol.Required(
            CONF_CONNECTION_MODE, default=DEFAULT_CONNECTION_MODE
        ): vol.In([CONNECTION_MODE_POLLING, CONNECTION_MODE_PERSISTENT]),
    }
    if mode == CONNECTION_MODE_POLLING:
        fields[
            vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL)
        ] = vol.All(vol.Coerce(int), vol.Range(min=3, max=3600))
    return vol.Schema(fields)


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

    def __init__(self) -> None:
        self._selected_mode: str = DEFAULT_CONNECTION_MODE

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_mode = user_input.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE)

            # If the user changed the mode, re-render the form with the right fields
            if selected_mode != self._selected_mode:
                self._selected_mode = selected_mode
                return self.async_show_form(
                    step_id="user",
                    data_schema=_schema_for_mode(self._selected_mode),
                    errors={},
                )

            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            device_name = user_input[CONF_DEVICE_NAME].strip()
            connection_mode = selected_mode
            scan_interval = int(
                user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            )

            if not device_name:
                errors[CONF_DEVICE_NAME] = "invalid_device_name"
            else:
                error_key = await self.hass.async_add_executor_job(
                    _try_connect, host, port
                )
                if error_key:
                    errors["base"] = error_key
                else:
                    await self.async_set_unique_id(f"{host}:{port}")
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"JURA ENA 8 ({host})",
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_DEVICE_NAME: device_name,
                            CONF_CONNECTION_MODE: connection_mode,
                            CONF_SCAN_INTERVAL: scan_interval,
                            "token": "",
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema_for_mode(self._selected_mode),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "JuraEna8OptionsFlow":
        return JuraEna8OptionsFlow(config_entry)


class JuraEna8OptionsFlow(config_entries.OptionsFlow):
    """Allow changing connection mode and scan interval after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        # Read from options first (saved by previous Configure), then fall back to data
        self._selected_mode: str = config_entry.options.get(
            CONF_CONNECTION_MODE,
            config_entry.data.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE),
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_mode = user_input.get(CONF_CONNECTION_MODE, self._selected_mode)

            if selected_mode != self._selected_mode:
                self._selected_mode = selected_mode
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._options_schema(),
                    errors={},
                )

            # Save options — HA will reload the integration automatically
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self._options_schema(),
            errors=errors,
        )

    def _options_schema(self) -> vol.Schema:
        current_mode = self._config_entry.options.get(
            CONF_CONNECTION_MODE,
            self._config_entry.data.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE),
        )
        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self._config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        fields: dict = {
            vol.Required(
                CONF_CONNECTION_MODE, default=self._selected_mode
            ): vol.In([CONNECTION_MODE_POLLING, CONNECTION_MODE_PERSISTENT]),
        }
        if self._selected_mode == CONNECTION_MODE_POLLING:
            fields[
                vol.Optional(CONF_SCAN_INTERVAL, default=current_interval)
            ] = vol.All(vol.Coerce(int), vol.Range(min=3, max=3600))
        return vol.Schema(fields)
