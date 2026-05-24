"""DataUpdateCoordinator for JURA ENA 8."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_PERSISTENT,
    CONNECTION_MODE_POLLING,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MACHINE_STATES,
    PRODUCT_STRENGTH_DEFAULTS,
    PRODUCTS,
    STATE_READY,
    STATE_UNAVAILABLE,
    TCP_BUFFER_SIZE,
    TCP_CONNECT_TIMEOUT,
    TCP_PUSH_WAIT,
    TCP_RECV_TIMEOUT,
    TOKEN_STORAGE_FILE,
)

_LOGGER = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Crypto helpers (nibble-based substitution cipher, reverse-engineered from
# the J.O.E. APK / JURA Smart Connect V2 protocol)
# ──────────────────────────────────────────────────────────────────────────────

TABLE_A = [1, 0, 3, 2, 15, 14, 8, 10, 6, 13, 7, 12, 11, 9, 5, 4]
TABLE_B = [9, 12, 6, 11, 10, 15, 2, 14, 13, 0, 4, 3, 1, 8, 7, 5]
ESCAPE_BYTES: frozenset[int] = frozenset({0, 10, 13, 38, 27})


def _b(n: int) -> int:
    """Clamp to unsigned byte."""
    return n % 256


def _encrypt_nibble(i5: int, i8: int, i9: int, i10: int) -> int:
    iB = _b(i5 + i8 + i9) % 16
    bi11 = _b(i8 >> 4)
    inner = _b(bi11 + TABLE_A[iB] + i10 - i8 - i9) % 16
    outer = _b(TABLE_B[inner] + i9 + i8 - i10 - bi11) % 16
    return _b(TABLE_A[outer] - i8 - i9) % 16


def _make_frame(cmd: str) -> bytes:
    """Encode a plaintext command into a JURA wire frame."""
    while True:
        key = random.randint(0, 255)
        if (key & 0xF) not in (14, 15):
            break

    out = bytearray()
    khi = _b(key >> 4)
    klo = _b(key)

    # Encode key byte
    if key in ESCAPE_BYTES:
        out += bytes([0x1B, key ^ 0x80])
    else:
        out.append(key)

    counter = 0
    for raw in cmd.encode("ascii"):
        enc_hi = _encrypt_nibble(_b(raw >> 4), counter, khi, klo)
        enc_lo = _encrypt_nibble(_b(raw & 0xF), counter + 1, khi, klo)
        enc = _b(enc_lo | _b(enc_hi << 4))
        if enc in ESCAPE_BYTES:
            out += bytes([0x1B, enc ^ 0x80])
        else:
            out.append(enc)
        counter += 2

    return b"\x2A" + bytes(out) + b"\x0D\x0A"


def _parse_frame(raw: bytes) -> str | None:
    """Decode a JURA wire frame into a plaintext string, or None on error."""
    if not raw or raw[0] != 0x2A or len(raw) < 3:
        return None

    i = 1
    if raw[i] == 0x1B:
        i += 1
        key = raw[i] ^ 0x80
        i += 1
    else:
        key = raw[i]
        i += 1

    khi = (key >> 4) & 0xF
    klo = key & 0xF

    enc: list[int] = []
    while i < len(raw):
        v = raw[i]
        if v == 0x0D:
            break
        if v == 0x1B:
            i += 1
            enc.append(raw[i] ^ 0x80)
        else:
            enc.append(v)
        i += 1

    out = bytearray()
    counter = 0
    for byte in enc:
        out.append(
            (_encrypt_nibble(byte >> 4, counter, khi, klo) << 4)
            | _encrypt_nibble(byte & 0xF, counter + 1, khi, klo)
        )
        counter += 2

    try:
        return out.decode("ascii").strip()
    except Exception:
        return None


def _hex_encode(s: str) -> str:
    """Encode each character of a string as 2 uppercase hex digits."""
    return "".join(f"{ord(c):02X}" for c in s)


# ──────────────────────────────────────────────────────────────────────────────
# @TP payload builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_tp_payload(
    product_key: str,
    water_ml_override: int | None = None,
    strength_override: int | None = None,
) -> str:
    """Build the 32-hex-char payload for an @TP: brew command."""
    code, grinder, default_strength_hex, default_water_ml, temp = PRODUCTS[product_key]
    water_ml = water_ml_override if water_ml_override is not None else default_water_ml
    water_hex = format(water_ml // 5, "02X")
    # Strength: user override (int 1-10) → hex, or keep product XML default
    strength = format(strength_override, "02X") if strength_override is not None else default_strength_hex

    # 16-byte payload (32 hex chars), positions as per EF555 XML:
    # pos 0  (byte 0): product code
    # pos 2  (byte 1 hi nibble / F3): strength
    # pos 3  (byte 1 lo nibble / F4): water_ml // 5
    # pos 6  (byte 3 hi nibble / F7): temperature
    # pos 8  (byte 4 hi nibble): always 01
    # pos 15 (byte 7 lo nibble): grinder byte
    payload = ["00"] * 16  # 16 hex-char pairs → 32 chars

    payload[0] = code                       # F1 – product code (byte 0)
    payload[2] = strength                   # F3 – strength (nibble at pos 2)
    payload[3] = water_hex                  # F4 – water (nibble at pos 3, 1 byte)
    payload[6] = temp                       # F7 – temperature (nibble at pos 6)
    payload[8] = "01"                       # pos 8 – always 01
    payload[15] = grinder                   # pos 15 – grinder byte

    # Flatten: each element is already 2 hex chars
    return "".join(payload)


# ──────────────────────────────────────────────────────────────────────────────
# Low-level TCP session (runs in executor)
# ──────────────────────────────────────────────────────────────────────────────

class _JuraSession:
    """A single synchronous TCP session with the machine."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TCP_CONNECT_TIMEOUT)
        sock.connect((self._host, self._port))
        sock.settimeout(TCP_RECV_TIMEOUT)
        self._sock = sock

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ── I/O primitives ───────────────────────────────────────────────────────

    def send(self, cmd: str) -> None:
        assert self._sock is not None
        frame = _make_frame(cmd)
        _LOGGER.debug("JURA TX: %s", cmd)
        self._sock.sendall(frame)

    def recv_line(self, timeout: float = TCP_RECV_TIMEOUT) -> str | None:
        """Receive one CRLF-terminated frame and decode it."""
        assert self._sock is not None
        self._sock.settimeout(timeout)
        buf = bytearray()
        try:
            while True:
                chunk = self._sock.recv(TCP_BUFFER_SIZE)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\x0D\x0A" in buf:
                    break
        except socket.timeout:
            pass

        if not buf:
            return None

        decoded = _parse_frame(bytes(buf))
        _LOGGER.debug("JURA RX: %s", decoded)
        return decoded

    # ── auth ─────────────────────────────────────────────────────────────────

    def authenticate(
        self, device_name: str, token: str
    ) -> tuple[bool, str]:
        """
        Send @HP: handshake.

        Returns (success, new_token).  new_token may be the same as the input
        token or a freshly issued one from an @hp4:TOKEN response.
        """
        # Device name must be hex-encoded: "iPhone 12 Damien" → "6950686F6E652031322044616D69656E"
        device_id = _hex_encode(device_name)
        cmd = f"@HP:,{device_id},{token}" if token else f"@HP:,{device_id},"
        self.send(cmd)
        resp = self.recv_line()
        if resp is None:
            _LOGGER.warning("JURA auth: no response")
            return False, token

        if resp.startswith("@hp4"):
            # Extract new token if present
            parts = resp.split(":")
            new_token = parts[1].strip() if len(parts) > 1 and parts[1].strip() else token
            _LOGGER.debug("JURA auth OK, token=%s", new_token)
            return True, new_token

        if resp.startswith("@hp5"):
            _LOGGER.warning("JURA auth failed (wrong token): %s", resp)
            return False, ""

        _LOGGER.warning("JURA auth unexpected response: %s", resp)
        return False, token

    # ── status ───────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """
        Try to get the machine state.

        Wait TCP_PUSH_WAIT seconds for a spontaneous @TF: push frame.
        If none arrives, fall back to @AN:00 query.
        Returns dict with 'state' and 'raw'.
        """
        # 1. Wait for push frame
        resp = self.recv_line(timeout=TCP_PUSH_WAIT)

        if resp is None:
            # 2. Fall back: query the machine
            self.send("@AN:00")
            resp = self.recv_line()

        return _parse_tf_response(resp)

    # ── brew ─────────────────────────────────────────────────────────────────

    def brew(self, product_key: str, water_ml: int | None = None, strength: int | None = None) -> bool:
        """Send a brew command. Returns True if machine acknowledged."""
        payload = _build_tp_payload(product_key, water_ml_override=water_ml, strength_override=strength)
        cmd = f"@TP:{payload}"
        self.send(cmd)
        resp = self.recv_line()
        if resp is None:
            _LOGGER.warning("JURA brew: no response")
            return False
        if resp.startswith("@tp"):
            _LOGGER.info("JURA brew accepted: %s", product_key)
            return True
        _LOGGER.warning("JURA brew unexpected response: %s", resp)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Helper: parse @TF: status frames
# ──────────────────────────────────────────────────────────────────────────────

def _parse_tf_response(resp: str | None) -> dict[str, Any]:
    """Extract machine state from an @TF: or @TV: frame string."""
    if resp is None:
        return {"state": STATE_READY, "raw": None}

    if resp.startswith("@TF:"):
        data = resp[4:]  # hex chars after "@TF:"
        # Parse all bytes for state detection and debugging
        bytes_list = [
            data[i*2:(i*2)+2].upper()
            for i in range(len(data) // 2)
            if len(data[i*2:(i*2)+2]) == 2
        ]
        bytes_debug = {f"byte_{i}": b for i, b in enumerate(bytes_list)}

        if not bytes_list:
            return {"state": STATE_READY, "raw": resp, "bytes": bytes_debug}

        # Byte 0: primary operational state (ready, brewing, heating, maintenance…)
        byte_0 = bytes_list[0]
        state = MACHINE_STATES.get(byte_0, f"unknown_{byte_0}")

        # Byte 1: maintenance alert bitmask (observed on ENA 8)
        # Normal idle value = 0x04. Extra bits signal alerts:
        #   bit 5 (0x20) = add_beans (no coffee beans in hopper)
        # More bits TBD as observed.
        if byte_0 == "00" and len(bytes_list) >= 2:
            try:
                b1 = int(bytes_list[1], 16)
                b1_alerts = b1 & ~0x04  # mask out the always-on base bit
                if b1_alerts & 0x20:
                    state = "add_beans"
            except ValueError:
                pass

        return {"state": state, "raw": resp, "bytes": bytes_debug}

    if resp.startswith("@TV:"):
        data = resp[4:]
        bytes_list = [data[i*2:(i*2)+2].upper() for i in range(len(data) // 2) if len(data[i*2:(i*2)+2]) == 2]
        bytes_debug = {f"byte_{i}": b for i, b in enumerate(bytes_list)}

        # byte_4 = water flow counter: increments while coffee is being dispensed
        # When > 0 and not FF (no sensor) → machine is actively dispensing
        if len(bytes_list) >= 5:
            flow_hex = bytes_list[4]
            if flow_hex != "FF":
                try:
                    flow = int(flow_hex, 16)
                    if flow > 0:
                        return {"state": "dispensing", "raw": resp, "bytes": bytes_debug}
                except ValueError:
                    pass

        # @TV: without flow = machine alive (heating, keeping warm, or idle)
        return {"state": STATE_READY, "raw": resp, "bytes": bytes_debug}

    # Unknown frame type → machine alive
    return {"state": STATE_READY, "raw": resp, "bytes": {}}


# ──────────────────────────────────────────────────────────────────────────────
# Async helpers for persistent connection mode
# ──────────────────────────────────────────────────────────────────────────────

async def _async_recv_frame(
    reader: asyncio.StreamReader, timeout: float = 60.0
) -> str | None:
    """Read one CRLF-terminated JURA frame asynchronously."""
    try:
        data = await asyncio.wait_for(reader.readuntil(b"\x0D\x0A"), timeout=timeout)
        return _parse_frame(bytes(data))
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        return None


async def _async_send_frame(writer: asyncio.StreamWriter, cmd: str) -> None:
    """Send an encrypted JURA frame asynchronously."""
    writer.write(_make_frame(cmd))
    await writer.drain()


async def _async_authenticate(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    device_name: str,
    token: str,
) -> tuple[bool, str]:
    """Async version of the @HP: handshake."""
    device_id = _hex_encode(device_name)
    cmd = f"@HP:,{device_id},{token}" if token else f"@HP:,{device_id},"
    await _async_send_frame(writer, cmd)
    resp = await _async_recv_frame(reader, timeout=TCP_RECV_TIMEOUT)
    if resp is None:
        return False, token
    if resp.startswith("@hp4"):
        parts = resp.split(":")
        new_token = parts[1].strip() if len(parts) > 1 and parts[1].strip() else token
        return True, new_token
    if resp.startswith("@hp5"):
        return False, ""
    return False, token


# ──────────────────────────────────────────────────────────────────────────────
# Coordinator
# ──────────────────────────────────────────────────────────────────────────────

class JuraCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages polling and command execution for one JURA ENA 8 machine."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        device_name: str,
        token: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        connection_mode: str = DEFAULT_CONNECTION_MODE,
    ) -> None:
        # Persistent mode disables the built-in polling timer (update_interval=None)
        update_interval = (
            None
            if connection_mode == CONNECTION_MODE_PERSISTENT
            else timedelta(seconds=scan_interval)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self._host = host
        self._port = port
        self._device_name = device_name
        self.token = token
        self._connection_mode = connection_mode
        self._lock = asyncio.Lock()
        # Currently selected product (used by JuraCoffeeSelect + JuraMakeCoffeeButton)
        self.selected_product: str = "espresso"
        # Water quantity for the next brew (ml); reset to product default on product change
        self.current_water_ml: int = PRODUCTS["espresso"][3]
        # Strength level 1-10; None for products without grinder (hot_water, milk_foam)
        self.current_strength: int | None = PRODUCT_STRENGTH_DEFAULTS["espresso"]
        # Consecutive TCP failures — show "unavailable" only after several failures
        # (during brewing the machine refuses connections → we keep the last known state)
        self._consecutive_failures: int = 0
        self._MAX_FAILURES_BEFORE_UNAVAILABLE: int = 3  # 3 × scan_interval ≈ 90 s
        # Persistent connection mode internals
        self._persistent_task: asyncio.Task | None = None
        self._persistent_writer: asyncio.StreamWriter | None = None
        self._persistent_reader: asyncio.StreamReader | None = None
        self._persistent_stop = asyncio.Event()

    # ── token persistence ────────────────────────────────────────────────────

    def _token_path(self) -> str:
        return self.hass.config.path(f".storage/{TOKEN_STORAGE_FILE}")

    async def _async_save_token(self, token: str) -> None:
        """Persist the token to disk so it survives HA restarts."""
        path = self._token_path()
        data = {"token": token}
        try:
            await self.hass.async_add_executor_job(
                _write_json, path, data
            )
        except Exception as exc:
            _LOGGER.error("JURA: failed to save token: %s", exc)

    # ── internal executor helpers ────────────────────────────────────────────

    def _do_status(self) -> dict[str, Any]:
        """Blocking: connect → auth → status → disconnect."""
        # If no token, skip polling — machine would need screen validation
        # which can't happen automatically. Avoid spamming the machine.
        if not self.token:
            _LOGGER.debug("JURA: no token, skipping poll (validate on machine screen first)")
            return {"state": STATE_UNAVAILABLE, "raw": None}

        session = _JuraSession(self._host, self._port)
        try:
            session.connect()
        except (ConnectionRefusedError, OSError, socket.timeout) as exc:
            _LOGGER.debug("JURA offline: %s", exc)
            return {"state": STATE_UNAVAILABLE, "raw": None}

        try:
            ok, new_token = session.authenticate(self._device_name, self.token)
            if not ok:
                _LOGGER.warning("JURA auth failed (token may have expired)")
                return {"state": STATE_UNAVAILABLE, "raw": None}
            self.token = new_token
            return session.get_status()
        except Exception as exc:
            _LOGGER.warning("JURA status error: %s", exc)
            return {"state": STATE_UNAVAILABLE, "raw": None}
        finally:
            session.close()

    def _do_brew(self, product_key: str, water_ml: int, strength: int | None) -> bool:
        """Blocking: connect → auth → brew → disconnect."""
        session = _JuraSession(self._host, self._port)
        try:
            session.connect()
        except (ConnectionRefusedError, OSError, socket.timeout) as exc:
            _LOGGER.error("JURA brew: cannot connect: %s", exc)
            return False

        try:
            ok, new_token = session.authenticate(self._device_name, self.token)
            if not ok:
                _LOGGER.error("JURA brew: auth failed")
                return False
            self.token = new_token
            return session.brew(product_key, water_ml=water_ml, strength=strength)
        except Exception as exc:
            _LOGGER.error("JURA brew: error: %s", exc)
            return False
        finally:
            session.close()

    # ── DataUpdateCoordinator interface ──────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch machine status. Never raises UpdateFailed — returns unavailable instead."""
        async with self._lock:
            try:
                data = await self.hass.async_add_executor_job(self._do_status)
            except Exception as exc:
                _LOGGER.error("JURA unexpected error in update: %s", exc)
                data = {"state": STATE_UNAVAILABLE, "raw": None}

        if data["state"] == STATE_UNAVAILABLE:
            self._consecutive_failures += 1
            if (
                self._consecutive_failures < self._MAX_FAILURES_BEFORE_UNAVAILABLE
                and self.data is not None
            ):
                # Machine is temporarily unreachable (busy brewing, single TCP slot)
                # → keep the last known state rather than flashing "Unavailable"
                _LOGGER.debug(
                    "JURA: connection failed (%d/%d), keeping last state: %s",
                    self._consecutive_failures,
                    self._MAX_FAILURES_BEFORE_UNAVAILABLE,
                    self.data.get("state"),
                )
                return self.data
        else:
            self._consecutive_failures = 0

        # Persist token if it changed
        if self.token != self.hass.data.get(DOMAIN, {}).get("token", ""):
            self.hass.data.setdefault(DOMAIN, {})["token"] = self.token
            await self._async_save_token(self.token)

        return data

    # ── persistent connection management ──────────────────────────────────────

    async def async_start_persistent(self) -> None:
        """Start the persistent TCP connection background task."""
        if self._connection_mode != CONNECTION_MODE_PERSISTENT:
            return
        self._persistent_stop.clear()
        self._persistent_task = self.hass.loop.create_task(
            self._persistent_loop(), name="jura_ena8_persistent"
        )
        _LOGGER.info("JURA: persistent connection mode started")

    async def async_stop_persistent(self) -> None:
        """Stop the persistent TCP connection background task."""
        self._persistent_stop.set()
        if self._persistent_writer:
            try:
                self._persistent_writer.close()
                await self._persistent_writer.wait_closed()
            except Exception:
                pass
            self._persistent_writer = None
            self._persistent_reader = None
        if self._persistent_task and not self._persistent_task.done():
            self._persistent_task.cancel()
            try:
                await self._persistent_task
            except asyncio.CancelledError:
                pass
        _LOGGER.info("JURA: persistent connection mode stopped")

    async def _persistent_loop(self) -> None:
        """Background task: keep a live TCP connection for real-time push frames."""
        backoff = 5.0
        while not self._persistent_stop.is_set():
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=TCP_CONNECT_TIMEOUT,
                )
                backoff = 5.0  # reset on successful connect

                ok, new_token = await _async_authenticate(
                    reader, writer, self._device_name, self.token
                )
                if not ok:
                    _LOGGER.warning("JURA persistent: auth failed, retrying in %.0fs", backoff)
                    writer.close()
                    await asyncio.sleep(backoff)
                    continue

                self.token = new_token
                self._persistent_reader = reader
                self._persistent_writer = writer
                _LOGGER.debug("JURA persistent: connected and authenticated")

                # Get initial state
                resp = await _async_recv_frame(reader, timeout=TCP_PUSH_WAIT)
                if resp is None:
                    await _async_send_frame(writer, "@AN:00")
                    resp = await _async_recv_frame(reader, timeout=TCP_RECV_TIMEOUT)
                if resp:
                    self.async_set_updated_data(_parse_tf_response(resp))

                # Real-time push frame reading loop.
                # We use a short timeout (20 s) so that if the machine goes
                # quiet (e.g. after clearing an alert like "add_beans"), we
                # actively poll its current state rather than waiting up to 90 s.
                while not self._persistent_stop.is_set():
                    frame = await _async_recv_frame(reader, timeout=20.0)
                    if frame is None:
                        # No spontaneous push frame — query the machine explicitly
                        _LOGGER.debug("JURA persistent: no push frame, querying @AN:00")
                        try:
                            await _async_send_frame(writer, "@AN:00")
                            frame = await _async_recv_frame(reader, timeout=TCP_RECV_TIMEOUT)
                        except Exception:
                            frame = None
                        if frame is None:
                            _LOGGER.debug("JURA persistent: connection lost, reconnecting…")
                            break
                    data = _parse_tf_response(frame)
                    _LOGGER.debug("JURA persistent RX: %s → %s", frame, data.get("state"))
                    self.async_set_updated_data(data)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                _LOGGER.warning("JURA persistent: error: %s", exc)
            finally:
                self._persistent_reader = None
                self._persistent_writer = None
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

            if self._persistent_stop.is_set():
                return
            _LOGGER.debug("JURA persistent: reconnecting in %.0fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60.0)

    async def _async_drop_persistent_connection(self) -> None:
        """Close the persistent connection so a brew can use the TCP slot."""
        if self._persistent_writer:
            try:
                self._persistent_writer.close()
                await self._persistent_writer.wait_closed()
            except Exception:
                pass
            self._persistent_writer = None
            self._persistent_reader = None

    # ── public command API ────────────────────────────────────────────────────

    async def async_brew(self, product_key: str, water_ml: int | None = None, strength: int | None = None) -> None:
        """Trigger a brew cycle for the given product key.

        water_ml: override in ml; if None uses coordinator.current_water_ml.
        strength: override level 1-10; if None uses coordinator.current_strength.
        """
        if product_key not in PRODUCTS:
            raise ValueError(f"Unknown product: {product_key}")

        actual_water = water_ml if water_ml is not None else self.current_water_ml
        actual_strength = strength if strength is not None else self.current_strength
        _LOGGER.info(
            "JURA: brewing '%s' — water=%d ml, strength=%s",
            product_key, actual_water, actual_strength,
        )

        # In persistent mode, release the TCP slot so the brew can connect
        if self._connection_mode == CONNECTION_MODE_PERSISTENT:
            await self._async_drop_persistent_connection()
            await asyncio.sleep(0.3)  # brief pause for TCP to fully close

        async with self._lock:
            try:
                success = await self.hass.async_add_executor_job(
                    self._do_brew, product_key, actual_water, actual_strength
                )
            except Exception as exc:
                _LOGGER.error("JURA async_brew error: %s", exc)
                return

        if not success:
            _LOGGER.warning("JURA brew command was not acknowledged by machine")
            return

        # Persist token after successful brew (auth may have refreshed it)
        await self._async_save_token(self.token)

        # Immediately show "brewing" state
        self._consecutive_failures = 0
        self.async_set_updated_data({"state": "brewing", "raw": None})

        if self._connection_mode == CONNECTION_MODE_POLLING:
            # Polling: request a status refresh after the brew
            await self.async_request_refresh()
        # Persistent: the loop will reconnect automatically and receive push frames

    def get_token(self) -> str:
        """Return the current authentication token."""
        return self.token


# ──────────────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────────────

def _write_json(path: str, data: dict) -> None:
    """Write a dict as JSON to a file (runs in executor)."""
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
