"""evdev key codes and keyboard shortcuts.

Shortcuts are stored as physical (evdev) key codes, so they work with any
keyboard layout: the compositor applies the user's layout."""

KEY_ESC, KEY_TAB, KEY_ENTER, KEY_SPACE = 1, 15, 28, 57
KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFTALT, KEY_LEFTMETA = 29, 42, 56, 125
KEY_RIGHTCTRL, KEY_RIGHTSHIFT, KEY_RIGHTALT, KEY_RIGHTMETA = 97, 54, 100, 126
KEY_Z, KEY_Y, KEY_D = 44, 21, 32
KEY_C, KEY_V, KEY_X, KEY_S = 46, 47, 45, 31
KEY_LEFT, KEY_RIGHT = 105, 106
KEY_KPMINUS, KEY_KPPLUS = 74, 78

MODIFIERS = {
    KEY_LEFTCTRL: "Ctrl", KEY_RIGHTCTRL: "Ctrl",
    KEY_LEFTSHIFT: "Shift", KEY_RIGHTSHIFT: "Shift",
    KEY_LEFTALT: "Alt", KEY_RIGHTALT: "AltGr",
    KEY_LEFTMETA: "Super", KEY_RIGHTMETA: "Super",
}

# Canonical order for showing and pressing modifiers.
MODIFIER_ORDER = [KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_LEFTSHIFT, KEY_RIGHTSHIFT,
                  KEY_LEFTALT, KEY_RIGHTALT, KEY_LEFTMETA, KEY_RIGHTMETA]

_NAMES = {
    1: "Esc", 14: "Backspace", 15: "Tab", 28: "Enter", 57: "Space",
    58: "Caps Lock", 69: "Num Lock", 70: "Scroll Lock", 99: "Print Screen", 119: "Pause",
    102: "Home", 103: "↑", 104: "Page Up", 105: "←", 106: "→", 107: "End",
    108: "↓", 109: "Page Down", 110: "Insert", 111: "Delete", 127: "Menu",
    113: "Mute", 114: "Volume Down", 115: "Volume Up",
    12: "-", 13: "=", 26: "[", 27: "]", 39: ";", 40: "'", 41: "`", 43: "\\",
    51: ",", 52: ".", 53: "/", 55: "Num *", 74: "Num −", 78: "Num +",
    96: "Num Enter", 98: "Num /", 83: "Num .",
}
_NAMES.update({2 + i: str((i + 1) % 10) for i in range(10)})  # 1..9, 0
for row, start in (("QWERTYUIOP", 16), ("ASDFGHJKL", 30), ("ZXCVBNM", 44)):
    _NAMES.update({start + i: ch for i, ch in enumerate(row)})
_NAMES.update({59 + i: f"F{i + 1}" for i in range(10)})
_NAMES.update({87: "F11", 88: "F12"})
_NAMES.update({71: "Num 7", 72: "Num 8", 73: "Num 9", 75: "Num 4", 76: "Num 5",
               77: "Num 6", 79: "Num 1", 80: "Num 2", 81: "Num 3", 82: "Num 0"})

# Every key the virtual keyboard can press.
ALL_KEYS = list(range(1, 249))


def key_name(code):
    return MODIFIERS.get(code) or _NAMES.get(code) or f"Key {code}"


def sort_combo(codes):
    """Modifiers first (in canonical order), then the rest."""
    mods = [c for c in MODIFIER_ORDER if c in codes]
    rest = [c for c in codes if c not in MODIFIERS]
    return mods + rest


def combo_label(codes):
    return "+".join(key_name(c) for c in sort_combo(codes)) or "None"


def combo(*codes, label=None):
    codes = sort_combo(list(codes))
    return {"keys": codes, "label": label or combo_label(codes)}
