"""Finds connected tablets by reading /sys (no permissions needed)."""

import glob
import os
import struct
from dataclasses import dataclass

from . import evdev

WACOM_VENDOR = 0x056A
VIRTUAL_PREFIX = "Trazo"

_WORD_BITS = struct.calcsize("l") * 8


@dataclass(frozen=True)
class PenNode:
    path: str
    name: str
    vendor: int
    product: int
    readable: bool

    @property
    def usb_id(self):
        return f"{self.vendor:04x}:{self.product:04x}"


def parse_sysfs_bitmap(text):
    """Parses a /sys bitmap (hex words, most significant first)."""
    bits = set()
    for index, word in enumerate(reversed(text.split())):
        value = int(word, 16)
        for bit in range(_WORD_BITS):
            if value >> bit & 1:
                bits.add(index * _WORD_BITS + bit)
    return bits


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def find_pens(sysfs_root="/sys/class/input", dev_root="/dev/input"):
    """Lists physical Wacom pens (virtual devices are excluded)."""
    pens = []
    for node in sorted(glob.glob(os.path.join(sysfs_root, "event*")),
                       key=lambda p: int(p.rsplit("event", 1)[1])):
        device = os.path.join(node, "device")
        if "/virtual/" in os.path.realpath(device):
            continue
        name = _read(os.path.join(device, "name"))
        if name.startswith(VIRTUAL_PREFIX):
            continue
        try:
            vendor = int(_read(os.path.join(device, "id/vendor")) or "0", 16)
            product = int(_read(os.path.join(device, "id/product")) or "0", 16)
        except ValueError:
            continue
        if vendor != WACOM_VENDOR:
            continue
        keys = parse_sysfs_bitmap(_read(os.path.join(device, "capabilities/key")) or "0")
        if evdev.BTN_TOOL_PEN not in keys:
            continue
        path = os.path.join(dev_root, os.path.basename(node))
        pens.append(PenNode(path, name, vendor, product, os.access(path, os.R_OK)))
    return pens


def uinput_writable():
    return os.access("/dev/uinput", os.W_OK)
