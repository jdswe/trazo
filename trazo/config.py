"""Configuration: profiles (one default + one per application), button
actions, and storage in ~/.config/trazo/config.json."""

import copy
import json
import os

from . import keys

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                          "trazo")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_PROFILE_ID = "*"

# Action catalog (same as Wacom Center). "kind" groups them in the picker.
ACTIONS = {
    "default": ("Default", "basic"),
    "disabled": ("Disabled", "basic"),
    "click": ("Click", "click"),
    "right_click": ("Right Click", "click"),
    "middle_click": ("Middle Click", "click"),
    "double_click": ("Double Click", "click"),
    "middle_double_click": ("Middle Double Click", "click"),
    "click_lock": ("Click Lock", "click"),
    "right_click_lock": ("Right Click Lock", "click"),
    "middle_click_lock": ("Middle Click Lock", "click"),
    "back": ("Back", "click"),
    "forward": ("Forward", "click"),
    "keystroke": ("Keystroke…", "keyboard"),
    "modifier": ("Modifier…", "keyboard"),
    "undo": ("Undo", "keyboard"),
    "redo": ("Redo", "keyboard"),
    "copy": ("Copy", "keyboard"),
    "paste": ("Paste", "keyboard"),
    "cut": ("Cut", "keyboard"),
    "save": ("Save", "keyboard"),
    "zoom_in": ("Zoom In", "keyboard"),
    "zoom_out": ("Zoom Out", "keyboard"),
    "pan_scroll": ("Pan/Scroll", "navigation"),
    "scroll_up": ("Scroll Up", "navigation"),
    "scroll_down": ("Scroll Down", "navigation"),
    "touch": ("Touch (like Windows Ink)", "pen"),
    "touch_toggle": ("Touch Mode On/Off", "pen"),
    "precision_mode": ("Precision Mode", "pen"),
    "pressure_hold": ("Pressure Hold", "pen"),
    "mode_toggle": ("Pen/Mouse Mode Toggle", "pen"),
    "display_toggle": ("Display Toggle", "pen"),
    "radial_menu": ("Radial Menu", "desktop"),
    "switch_app": ("Switch Application", "desktop"),
    "show_desktop": ("Show Desktop", "desktop"),
    "overview": ("Activities Overview", "desktop"),
    "run": ("Open/Run…", "desktop"),
    "settings": ("Open Trazo", "desktop"),
}

# Fixed keys for the predefined keyboard actions.
FIXED_COMBOS = {
    "undo": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_Z),
    "redo": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_LEFTSHIFT, keys.KEY_Z),
    "copy": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_C),
    "paste": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_V),
    "cut": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_X),
    "save": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_S),
    "zoom_in": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_KPPLUS),
    "zoom_out": keys.combo(keys.KEY_LEFTCTRL, keys.KEY_KPMINUS),
    "show_desktop": keys.combo(keys.KEY_LEFTMETA, keys.KEY_D),
    "overview": keys.combo(keys.KEY_LEFTMETA),
}

# "touch" only makes sense on the tip; these make no sense inside the radial
# menu (they need a button to be held down).
TIP_ONLY_ACTIONS = {"touch"}
NOT_IN_RADIAL = {"touch", "radial_menu", "pan_scroll", "precision_mode", "pressure_hold",
                 "modifier", "click_lock", "right_click_lock", "middle_click_lock", "default"}

ACTION_ICONS = {
    "default": "object-select-symbolic",
    "disabled": "action-unavailable-symbolic",
    "click": "input-mouse-symbolic",
    "right_click": "input-mouse-symbolic",
    "middle_click": "input-mouse-symbolic",
    "double_click": "input-mouse-symbolic",
    "middle_double_click": "input-mouse-symbolic",
    "click_lock": "changes-prevent-symbolic",
    "right_click_lock": "changes-prevent-symbolic",
    "middle_click_lock": "changes-prevent-symbolic",
    "back": "go-previous-symbolic",
    "forward": "go-next-symbolic",
    "keystroke": "input-keyboard-symbolic",
    "modifier": "input-keyboard-symbolic",
    "undo": "edit-undo-symbolic",
    "redo": "edit-redo-symbolic",
    "copy": "edit-copy-symbolic",
    "paste": "edit-paste-symbolic",
    "cut": "edit-cut-symbolic",
    "save": "document-save-symbolic",
    "zoom_in": "zoom-in-symbolic",
    "zoom_out": "zoom-out-symbolic",
    "pan_scroll": "view-fullscreen-symbolic",
    "scroll_up": "go-up-symbolic",
    "scroll_down": "go-down-symbolic",
    "touch": "input-touchscreen-symbolic",
    "touch_toggle": "input-touchscreen-symbolic",
    "precision_mode": "find-location-symbolic",
    "pressure_hold": "view-pin-symbolic",
    "mode_toggle": "input-tablet-symbolic",
    "display_toggle": "video-display-symbolic",
    "radial_menu": "view-app-grid-symbolic",
    "switch_app": "focus-windows-symbolic",
    "show_desktop": "preferences-desktop-display-symbolic",
    "overview": "view-grid-symbolic",
    "run": "system-run-symbolic",
    "settings": "emblem-system-symbolic",
}

# Buttons: "lower" = the one closest to the tip (BTN_STYLUS), "upper" = BTN_STYLUS2.
BUTTON_NAMES = {"tip": "Tip", "lower": "Lower Button", "upper": "Upper Button"}
DEFAULT_PROFILE_NAME = "All Other Applications"
_OLD_DEFAULT_NAMES = {"Todas las demás aplicaciones"}

DEFAULT_PROFILE = {
    "name": DEFAULT_PROFILE_NAME,
    "tip": {
        "threshold": 0.01,
        "maximum": 1.0,
        "curve": [1 / 3, 1 / 3, 2 / 3, 2 / 3],
        "tip_up_assist": False,
    },
    "double_click_distance": 0.0,
    "smoothing": 0.2,
    "button_mode": "hover",
    "buttons": {
        "tip": {"type": "click"},
        "lower": {"type": "right_click"},
        "upper": {"type": "pan_scroll"},
    },
    "mapping": {
        "mode": "pen",
        "orientation": 0,
        "screen": {"type": "all"},
        "tablet_area": {"type": "full", "rect": [0.0, 0.0, 1.0, 1.0]},
        "force_proportions": False,
        "mouse_speed": 0.0,
        "mouse_acceleration": 0.0,
    },
}

DEFAULT_CONFIG = {
    "version": 1,
    "enabled": True,
    "device": "auto",
    "advanced": {
        "precision_factor": 0.35,
        "contact_floor": 0.06,
        "pan_speed": 1.0,
        "unified_cursor": True,
        "visual_feedback": True,
        "precision_overlay": True,
    },
    "first_run_done": False,
    "radial_menu": [{"type": t} for t in ("undo", "redo", "copy", "paste", "zoom_in",
                                          "zoom_out", "overview", "show_desktop")],
    "profiles": {DEFAULT_PROFILE_ID: DEFAULT_PROFILE},
}
RADIAL_SLOTS = 8


def _merge(defaults, value):
    """Fills in defaults for anything missing (or of the wrong type)."""
    if isinstance(defaults, dict):
        if not isinstance(value, dict):
            return copy.deepcopy(defaults)
        merged = {k: _merge(v, value.get(k)) for k, v in defaults.items()}
        for k, v in value.items():
            if k not in merged:
                merged[k] = copy.deepcopy(v)
        return merged
    if value is None:
        return copy.deepcopy(defaults)
    if isinstance(defaults, bool) != isinstance(value, bool):
        return copy.deepcopy(defaults)
    if isinstance(defaults, (int, float)) and not isinstance(value, (int, float)):
        return copy.deepcopy(defaults)
    if isinstance(defaults, str) and not isinstance(value, str):
        return copy.deepcopy(defaults)
    if isinstance(defaults, list) and not isinstance(value, list):
        return copy.deepcopy(defaults)
    return copy.deepcopy(value)


def normalize(data):
    data = data if isinstance(data, dict) else {}
    config = _merge({k: v for k, v in DEFAULT_CONFIG.items() if k != "profiles"}, data)
    radial = [a for a in config["radial_menu"]
              if isinstance(a, dict) and a.get("type") in ACTIONS][:RADIAL_SLOTS]
    defaults = DEFAULT_CONFIG["radial_menu"]
    config["radial_menu"] = radial + copy.deepcopy(defaults[len(radial):])
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    config["profiles"] = {pid: _merge(DEFAULT_PROFILE, p) for pid, p in profiles.items()}
    if DEFAULT_PROFILE_ID not in config["profiles"]:
        config["profiles"][DEFAULT_PROFILE_ID] = copy.deepcopy(DEFAULT_PROFILE)
    default = config["profiles"][DEFAULT_PROFILE_ID]
    if default.get("name") in _OLD_DEFAULT_NAMES:
        default["name"] = DEFAULT_PROFILE_NAME
    for profile in config["profiles"].values():
        for button, action in list(profile["buttons"].items()):
            if not isinstance(action, dict) or action.get("type") not in ACTIONS:
                profile["buttons"][button] = copy.deepcopy(DEFAULT_PROFILE["buttons"].get(
                    button, {"type": "default"}))
    return config


def load(path=CONFIG_PATH):
    try:
        with open(path) as f:
            return normalize(json.load(f))
    except (OSError, ValueError):
        return normalize({})


def save(config, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def profile_for(config, app_id):
    profiles = config["profiles"]
    if app_id and app_id in profiles:
        return app_id, profiles[app_id]
    return DEFAULT_PROFILE_ID, profiles[DEFAULT_PROFILE_ID]


def action_label(action):
    kind = action.get("type", "default")
    label = ACTIONS.get(kind, ("?",))[0]
    if kind in ("keystroke", "modifier") and action.get("keys"):
        return f"{label.rstrip('…')}: {action.get('label') or keys.combo_label(action['keys'])}"
    if kind == "run" and action.get("name"):
        return f"Open: {action['name']}"
    return label
