"""Direct access to evdev (reading the tablet) and uinput (creating virtual
devices) using only the Python standard library (ioctl + struct)."""

import fcntl
import os
import struct
from dataclasses import dataclass

# --- Event types and codes (linux/input-event-codes.h) --------------------
EV_SYN, EV_KEY, EV_REL, EV_ABS, EV_MSC = 0x00, 0x01, 0x02, 0x03, 0x04
SYN_REPORT, SYN_DROPPED = 0, 3

ABS_X, ABS_Y = 0x00, 0x01
ABS_PRESSURE, ABS_DISTANCE = 0x18, 0x19
ABS_TILT_X, ABS_TILT_Y = 0x1A, 0x1B

REL_X, REL_Y = 0x00, 0x01
REL_HWHEEL, REL_WHEEL = 0x06, 0x08
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x0B, 0x0C

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_SIDE, BTN_EXTRA = 0x113, 0x114
BTN_TOOL_PEN, BTN_TOOL_RUBBER = 0x140, 0x141
BTN_STYLUS3 = 0x149
BTN_TOUCH, BTN_STYLUS, BTN_STYLUS2 = 0x14A, 0x14B, 0x14C

INPUT_PROP_POINTER, INPUT_PROP_DIRECT = 0x00, 0x01
BUS_USB, BUS_VIRTUAL = 0x03, 0x06

KEY_MAX, REL_MAX, ABS_MAX, INPUT_PROP_MAX = 0x2FF, 0x0F, 0x3F, 0x1F

# --- Kernel structures ----------------------------------------------------
_EVENT = struct.Struct("llHHi")          # struct input_event (64 bits)
_ABSINFO = struct.Struct("6i")           # struct input_absinfo
_INPUT_ID = struct.Struct("4H")          # struct input_id
_UINPUT_SETUP = struct.Struct("4H80sI")  # struct uinput_setup
_UINPUT_ABS_SETUP = struct.Struct("H2x6i")  # struct uinput_abs_setup

_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _ioc(direction, kind, nr, size):
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


EVIOCGID = _ioc(_IOC_READ, "E", 0x02, _INPUT_ID.size)
EVIOCGRAB = _ioc(_IOC_WRITE, "E", 0x90, 4)


def EVIOCGNAME(length):
    return _ioc(_IOC_READ, "E", 0x06, length)


def EVIOCGPROP(length):
    return _ioc(_IOC_READ, "E", 0x09, length)


def EVIOCGBIT(ev, length):
    return _ioc(_IOC_READ, "E", 0x20 + ev, length)


def EVIOCGABS(code):
    return _ioc(_IOC_READ, "E", 0x40 + code, _ABSINFO.size)


UI_DEV_CREATE = _ioc(_IOC_NONE, "U", 1, 0)
UI_DEV_DESTROY = _ioc(_IOC_NONE, "U", 2, 0)
UI_DEV_SETUP = _ioc(_IOC_WRITE, "U", 3, _UINPUT_SETUP.size)
UI_ABS_SETUP = _ioc(_IOC_WRITE, "U", 4, _UINPUT_ABS_SETUP.size)
UI_SET_EVBIT = _ioc(_IOC_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _ioc(_IOC_WRITE, "U", 101, 4)
UI_SET_RELBIT = _ioc(_IOC_WRITE, "U", 102, 4)
UI_SET_ABSBIT = _ioc(_IOC_WRITE, "U", 103, 4)
UI_SET_PROPBIT = _ioc(_IOC_WRITE, "U", 110, 4)


@dataclass(frozen=True)
class AbsInfo:
    value: int = 0
    minimum: int = 0
    maximum: int = 0
    fuzz: int = 0
    flat: int = 0
    resolution: int = 0

    def normalize(self, value):
        span = self.maximum - self.minimum
        if span <= 0:
            return 0.0
        return min(1.0, max(0.0, (value - self.minimum) / span))

    def denormalize(self, fraction):
        fraction = min(1.0, max(0.0, fraction))
        return int(round(self.minimum + fraction * (self.maximum - self.minimum)))


def _bits(buffer, max_code):
    return {code for code in range(max_code + 1)
            if code // 8 < len(buffer) and buffer[code // 8] >> (code % 8) & 1}


class InputDevice:
    """A /dev/input/eventN node opened in non-blocking mode."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.grabbed = False
        try:
            raw = fcntl.ioctl(self.fd, EVIOCGNAME(256), bytes(256))
            self.name = raw.split(b"\0", 1)[0].decode(errors="replace")
            self.bustype, self.vendor, self.product, self.version = _INPUT_ID.unpack(
                fcntl.ioctl(self.fd, EVIOCGID, bytes(_INPUT_ID.size)))
            self.keys = self._query_bits(EV_KEY, KEY_MAX)
            self.rels = self._query_bits(EV_REL, REL_MAX)
            abs_codes = self._query_bits(EV_ABS, ABS_MAX)
            size = INPUT_PROP_MAX // 8 + 1
            self.props = _bits(fcntl.ioctl(self.fd, EVIOCGPROP(size), bytes(size)),
                               INPUT_PROP_MAX)
            self.absinfo = {
                code: AbsInfo(*_ABSINFO.unpack(
                    fcntl.ioctl(self.fd, EVIOCGABS(code), bytes(_ABSINFO.size))))
                for code in abs_codes
            }
        except Exception:
            os.close(self.fd)
            raise

    def _query_bits(self, ev, max_code):
        size = max_code // 8 + 1
        return _bits(fcntl.ioctl(self.fd, EVIOCGBIT(ev, size), bytes(size)), max_code)

    def grab(self):
        fcntl.ioctl(self.fd, EVIOCGRAB, 1)
        self.grabbed = True

    def ungrab(self):
        if self.grabbed:
            try:
                fcntl.ioctl(self.fd, EVIOCGRAB, 0)
            except OSError:
                pass
            self.grabbed = False

    def read(self):
        """Returns pending events as (type, code, value) tuples. Raises
        OSError if the device was unplugged."""
        try:
            data = os.read(self.fd, _EVENT.size * 128)
        except BlockingIOError:
            return []
        return [(t, c, v) for _sec, _usec, t, c, v in _EVENT.iter_unpack(data)]

    def close(self):
        self.ungrab()
        try:
            os.close(self.fd)
        except OSError:
            pass


class UInput:
    """Virtual input device created through /dev/uinput.

    Events are buffered and written together on each syn() so the compositor
    receives every frame at once."""

    def __init__(self, name, *, vendor=0, product=0, version=0, bustype=BUS_VIRTUAL,
                 keys=(), rels=(), absinfo=None, props=()):
        self.name = name
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self._pending = bytearray()
        try:
            if keys:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
                for code in sorted(set(keys)):
                    fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            if rels:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
                for code in sorted(set(rels)):
                    fcntl.ioctl(self.fd, UI_SET_RELBIT, code)
            if absinfo:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
                for code, info in sorted(absinfo.items()):
                    fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                    fcntl.ioctl(self.fd, UI_ABS_SETUP, _UINPUT_ABS_SETUP.pack(
                        code, info.value, info.minimum, info.maximum,
                        info.fuzz, info.flat, info.resolution))
            for prop in props:
                fcntl.ioctl(self.fd, UI_SET_PROPBIT, prop)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, _UINPUT_SETUP.pack(
                bustype, vendor, product, version, name.encode()[:79], 0))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise

    def emit(self, ev_type, code, value):
        self._pending += _EVENT.pack(0, 0, ev_type, code, int(value))

    def syn(self):
        self.emit(EV_SYN, SYN_REPORT, 0)
        data, self._pending = bytes(self._pending), bytearray()
        os.write(self.fd, data)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
