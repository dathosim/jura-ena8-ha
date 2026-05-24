"""Constants for the JURA ENA 8 integration."""

DOMAIN = "jura_ena8"

DEFAULT_PORT = 51515
DEFAULT_SCAN_INTERVAL = 30  # seconds

# Connection modes
CONNECTION_MODE_POLLING    = "polling"
CONNECTION_MODE_PERSISTENT = "persistent"
DEFAULT_CONNECTION_MODE    = CONNECTION_MODE_POLLING
CONF_CONNECTION_MODE       = "connection_mode"

# Products: (product_code, grinder_byte, strength, water_ml, temperature_code)
# temperature: "00"=Low, "01"=Normal, "02"=High
PRODUCTS: dict[str, tuple[str, str, str, int, str]] = {
    "espresso":           ("02", "04", "08",  45, "02"),
    "ristretto":          ("01", "04", "08",  25, "02"),
    "espresso_doppio":    ("30", "04", "08",  90, "01"),
    "coffee":             ("03", "04", "05", 100, "01"),
    "cappuccino":         ("04", "04", "08",  60, "01"),
    "flat_white":         ("2E", "04", "05",  60, "01"),
    "latte_macchiato":    ("07", "04", "08",  45, "02"),
    "espresso_macchiato": ("06", "04", "08",  25, "01"),
    "hot_water":          ("0D", "00", "00", 220, "01"),
    "milk_foam":          ("08", "00", "00",   0, "00"),
}

# Coffee strength: default level (1-10) per product; None = not applicable
# Source: EF555 XML COFFEE_STRENGTH ITEM list (values 01→0A, argument F3)
PRODUCT_STRENGTH_DEFAULTS: dict[str, int | None] = {
    "espresso":           8,
    "ristretto":          8,
    "espresso_doppio":    8,
    "coffee":             5,
    "cappuccino":         8,
    "flat_white":         5,
    "latte_macchiato":    8,
    "espresso_macchiato": 8,
    "hot_water":          None,   # no grinder, no strength
    "milk_foam":          None,   # no grinder, no strength
}

STRENGTH_MIN = 1
STRENGTH_MAX = 10

# Water quantity limits (min_ml, max_ml) per product — step is always 5 ml
# Used by the number entity; default comes from PRODUCTS[key][3]
PRODUCT_WATER_LIMITS: dict[str, tuple[int, int]] = {
    "espresso":           ( 25,  80),
    "ristretto":          ( 15,  50),
    "espresso_doppio":    ( 50, 150),
    "coffee":             ( 60, 220),
    "cappuccino":         ( 40, 120),
    "flat_white":         ( 40, 120),
    "latte_macchiato":    ( 30,  90),
    "espresso_macchiato": ( 15,  50),
    "hot_water":          ( 50, 400),
    "milk_foam":          (  0,   0),  # no water for milk foam
}

PRODUCT_NAMES: dict[str, str] = {
    "espresso":           "Espresso",
    "ristretto":          "Ristretto",
    "espresso_doppio":    "Espresso Doppio",
    "coffee":             "Coffee",
    "cappuccino":         "Cappuccino",
    "flat_white":         "Flat White",
    "latte_macchiato":    "Latte Macchiato",
    "espresso_macchiato": "Espresso Macchiato",
    "hot_water":          "Hot Water",
    "milk_foam":          "Milk Foam",
}

# ─────────────────────────────────────────────────────────────────────────────
# Machine states — byte 0 of the @TF: push frame (PROGRESS_STATE_INTAKE)
# Source: reverse-engineered from J.O.E. APK / EF555 XML
# ─────────────────────────────────────────────────────────────────────────────
MACHINE_STATES: dict[str, str] = {
    # ── Normal operation ─────────────────────────────────────────────────────
    "00": "ready",
    "11": "stand_by",
    "15": "please_wait",
    "20": "empty_grounds",  # ENA 8 confirmed: byte_0=20 = "vider marc"
    "21": "heating_up",
    "24": "coffee_ready",
    "25": "shutting_down",
    "40": "fill_water",       # ENA 8: water tank empty (confirmed from @TF: frame observation)
    # ── Maintenance needed ────────────────────────────────────────────────────
    "01": "insert_tray",
    "02": "fill_water",
    "03": "empty_grounds",
    "04": "empty_tray",
    "05": "needs_cleaning",
    "07": "descaling_needed",
    "0A": "fill_beans",
    "0E": "change_water_filter",
    "10": "add_ground_coffee",
    "13": "add_beans",
    "2D": "no_milk",
    # ── Rinse / Clean cycles ──────────────────────────────────────────────────
    "06": "cleaning",
    "08": "descaling",
    "09": "rinsing",
    "0B": "pre_rinsing",
    "0C": "rinsing",          # post-brew rinse / pre-heat rinse (ENA 8)
    "0D": "calc_clean",
    "88": "ejecting_grounds",  # ENA 8 confirmed: appears during grounds ejection cycle (NOT dispensing)
    "A8": "ejecting_grounds",  # ENA 8 confirmed: transition state during grounds ejection
    # ── Special modes ─────────────────────────────────────────────────────────
    "29": "program_mode",
}

# Human-readable labels for each machine state key
MACHINE_STATE_LABELS: dict[str, str] = {
    "ready":               "Ready",
    "stand_by":            "Stand-by",
    "please_wait":         "Please wait…",
    "brewing":             "Brewing",
    "heating_up":          "Heating up",
    "coffee_ready":        "Coffee ready",
    "shutting_down":       "Shutting down",
    "warming_up":          "Warming up…",        # kept for backwards compat
    "insert_tray":         "Insert drip tray",
    "fill_water":          "Fill water tank",
    "empty_grounds":       "Empty grounds container",
    "empty_tray":          "Empty drip tray",
    "needs_cleaning":      "Cleaning required",
    "descaling_needed":    "Descaling required",
    "fill_beans":          "Fill bean hopper",
    "change_water_filter": "Change water filter",
    "add_ground_coffee":   "Add ground coffee",
    "add_beans":           "Add beans",
    "no_milk":             "No milk",
    "cleaning":            "Cleaning…",
    "descaling":           "Descaling…",
    "rinsing":             "Rinsing…",
    "pre_rinsing":         "Pre-rinsing…",
    "calc_clean":          "Calc-clean cycle",
    "dispensing":          "Dispensing…",
    "ejecting_grounds":    "Ejecting grounds…",
    "program_mode":        "Program mode",
    "unavailable":         "Unavailable",
}

STATE_UNAVAILABLE = "unavailable"
STATE_READY = "ready"

# Storage
TOKEN_STORAGE_KEY = "jura_ena8_token"
TOKEN_STORAGE_FILE = "jura_ena8_token.json"

# TCP
TCP_CONNECT_TIMEOUT = 5.0   # seconds
TCP_RECV_TIMEOUT = 3.0      # seconds
TCP_PUSH_WAIT = 1.5         # seconds to wait for push frame after auth
TCP_BUFFER_SIZE = 1024
