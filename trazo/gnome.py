"""GNOME integration: displays (Mutter), tablet settings and the focused
application (trazo-focus extension)."""

import json
import os

from gi.repository import Gio, GLib

from . import config as cfg
from .mapping import Desktop, Monitor

TABLET_SCHEMA = "org.gnome.desktop.peripherals.tablet"
STYLUS_SCHEMA = "org.gnome.desktop.peripherals.tablet.stylus"
BACKUP_PATH = os.path.join(cfg.CONFIG_DIR, "gnome-backup.json")

# While Trazo controls the tablet, GNOME must not apply anything on top: the
# virtual pen covers the whole desktop and Trazo does the rest.
NEUTRAL_TABLET = {
    "mapping": GLib.Variant("s", "absolute"),
    "keep-aspect": GLib.Variant("b", False),
    "left-handed": GLib.Variant("b", False),
    "area": GLib.Variant("ad", [0.0, 0.0, 0.0, 0.0]),
    "output": None,  # unset = "all displays"
}
NEUTRAL_STYLUS = {
    "pressure-curve": GLib.Variant("ai", [0, 0, 100, 100]),
    "pressure-range": GLib.Variant("ai", [0, 100]),
    "eraser-pressure-curve": GLib.Variant("ai", [0, 0, 100, 100]),
    "button-action": GLib.Variant("s", "middle"),           # BTN_STYLUS
    "secondary-button-action": GLib.Variant("s", "right"),  # BTN_STYLUS2
    "tertiary-button-action": GLib.Variant("s", "back"),    # BTN_STYLUS3
}


def _schema_keys(schema_id):
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source else None
    return set(schema.list_keys()) if schema else set()


def _settings(usb_id):
    return (
        (Gio.Settings.new_with_path(
            TABLET_SCHEMA, f"/org/gnome/desktop/peripherals/tablets/{usb_id}/"),
         NEUTRAL_TABLET, _schema_keys(TABLET_SCHEMA)),
        (Gio.Settings.new_with_path(
            STYLUS_SCHEMA, f"/org/gnome/desktop/peripherals/stylus/default-{usb_id}/"),
         NEUTRAL_STYLUS, _schema_keys(STYLUS_SCHEMA)),
    )


def take_over(usb_id):
    """Saves the user's GNOME settings and applies neutral ones."""
    backup = {}
    if os.path.exists(BACKUP_PATH):
        try:
            with open(BACKUP_PATH) as f:
                backup = json.load(f)
        except (OSError, ValueError):
            backup = {}
    entry = backup.get(usb_id)
    first_time = entry is None
    entry = entry or {}
    for settings, neutral, available in _settings(usb_id):
        path = settings.props.path
        saved = entry.setdefault(path, {})
        for key, value in neutral.items():
            if key not in available:
                continue
            if first_time:
                user = settings.get_user_value(key)
                saved[key] = user.print_(True) if user is not None else None
            # Only write when changed, to avoid notification loops.
            if value is None:
                if settings.get_user_value(key) is not None:
                    settings.reset(key)
            elif not settings.get_value(key).equal(value):
                settings.set_value(key, value)
    backup[usb_id] = entry
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    with open(BACKUP_PATH, "w") as f:
        json.dump(backup, f, indent=2)
    Gio.Settings.sync()


def watch(usb_id, callback):
    """Calls callback when something (e.g. GNOME Settings) changes the tablet
    settings while Trazo controls the tablet. Returns what unwatch() needs."""
    handles = []
    for settings, neutral, available in _settings(usb_id):
        for key in neutral:
            if key in available:
                settings.get_value(key)  # GSettings only notifies keys that were read
        handles.append((settings, settings.connect("changed", lambda *_: callback())))
    return handles


def unwatch(handles):
    for settings, handler in handles or []:
        settings.disconnect(handler)


def restore(usb_id):
    """Gives GNOME back the settings the user had before Trazo."""
    try:
        with open(BACKUP_PATH) as f:
            backup = json.load(f)
    except (OSError, ValueError):
        return
    entry = backup.pop(usb_id, None)
    if not entry:
        return
    for settings, _neutral, available in _settings(usb_id):
        for key, text in entry.get(settings.props.path, {}).items():
            if key not in available:
                continue
            if text is None:
                settings.reset(key)
            else:
                settings.set_value(key, GLib.Variant.parse(None, text, None, None))
    with open(BACKUP_PATH, "w") as f:
        json.dump(backup, f, indent=2)
    Gio.Settings.sync()


def double_click_ms():
    try:
        return Gio.Settings.new("org.gnome.desktop.peripherals.mouse").get_int("double-click")
    except Exception:
        return 400


# --- Displays --------------------------------------------------------------
_DISPLAY_BUS = "org.gnome.Mutter.DisplayConfig"
_DISPLAY_PATH = "/org/gnome/Mutter/DisplayConfig"


def read_desktop(connection=None):
    """Reads the monitor layout from Mutter (in logical pixels)."""
    connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION)
    try:
        reply = connection.call_sync(_DISPLAY_BUS, _DISPLAY_PATH, _DISPLAY_BUS,
                                     "GetCurrentState", None, None,
                                     Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return Desktop()
    _serial, monitors, logical_monitors, properties = reply.unpack()
    layout_logical = properties.get("layout-mode", 1) == 1
    by_spec = {}
    for spec, modes, props in monitors:
        current = next((m for m in modes if m[6].get("is-current")), None)
        name = props.get("display-name") or spec[0]
        by_spec[tuple(spec)] = (current, name)
    result = []
    for x, y, scale, transform, primary, specs, _props in logical_monitors:
        if not specs:
            continue
        current, name = by_spec.get(tuple(specs[0]), (None, specs[0][0]))
        if not current:
            continue
        width, height = current[1], current[2]
        if transform % 2 == 1:  # rotated 90° or 270°
            width, height = height, width
        if layout_logical and scale > 0:
            width, height = round(width / scale), round(height / scale)
        result.append(Monitor(specs[0][0], name, x, y, width, height, bool(primary)))
    return Desktop(result)


def watch_monitors(callback, connection=None):
    connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION)
    return connection.signal_subscribe(
        _DISPLAY_BUS, _DISPLAY_BUS, "MonitorsChanged", _DISPLAY_PATH, None,
        Gio.DBusSignalFlags.NONE, lambda *args: callback(), )


# --- GNOME Shell extension (focused app, effects and radial menu) ----------
SHELL_BUS = "io.github.jdswe.TrazoShell"
SHELL_PATH = "/io/github/jdswe/TrazoShell"
SHELL_IFACE = "io.github.jdswe.TrazoShell"
EXTENSION_UUID = "trazo@jdswe.github.io"


def enable_extension():
    """Marks the Trazo extension as enabled. GNOME on Wayland loads it at the
    next login."""
    settings = Gio.Settings.new("org.gnome.shell")
    enabled = list(settings.get_strv("enabled-extensions"))
    if EXTENSION_UUID not in enabled:
        settings.set_strv("enabled-extensions", enabled + [EXTENSION_UUID])
    disabled = list(settings.get_strv("disabled-extensions"))
    if EXTENSION_UUID in disabled:
        settings.set_strv("disabled-extensions", [u for u in disabled if u != EXTENSION_UUID])
    Gio.Settings.sync()


def focused_app(connection=None):
    connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION)
    try:
        reply = connection.call_sync(SHELL_BUS, SHELL_PATH, SHELL_IFACE, "GetFocus",
                                     None, GLib.VariantType("(s)"),
                                     Gio.DBusCallFlags.NONE, 500, None)
        return reply.unpack()[0]
    except GLib.Error:
        return ""


def watch_focus(callback, connection=None):
    connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION)
    return connection.signal_subscribe(
        SHELL_BUS, SHELL_IFACE, "FocusChanged", SHELL_PATH, None,
        Gio.DBusSignalFlags.NONE,
        lambda _c, _s, _p, _i, _n, params: callback(params.unpack()[0]))
