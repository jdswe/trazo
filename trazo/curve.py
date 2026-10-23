"""Pressure curve (tip sensitivity), like Wacom's "Tip feel": a click
threshold, a maximum pressure and a Bézier curve between them."""

LINEAR = [1 / 3, 1 / 3, 2 / 3, 2 / 3]
_SOFTNESS_SPAN = 0.95
_LUT_SIZE = 1024


def _clamp(value, low=0.0, high=1.0):
    return min(high, max(low, value))


def _bezier(t, a, b):
    u = 1.0 - t
    return 3 * u * u * t * a + 3 * u * t * t * b + t * t * t


def bezier_y(x, curve):
    """Value at x of a cubic Bézier from (0,0) to (1,1) with control points
    curve = [x1, y1, x2, y2]."""
    x1, y1, x2, y2 = (_clamp(v) for v in curve)
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(40):
        t = (low + high) / 2
        if _bezier(t, x1, x2) < x:
            low = t
        else:
            high = t
    return _clamp(_bezier((low + high) / 2, y1, y2))


def curve_from_softness(softness):
    """softness in [-1, 1]: negative = soft (less force), positive = firm."""
    k = abs(_clamp(softness, -1.0, 1.0)) * _SOFTNESS_SPAN
    if k < 1e-6:
        return list(LINEAR)
    if softness < 0:
        return [0.0, k, 1.0 - k, 1.0]
    return [k, 0.0, 1.0, 1.0 - k]


def softness_from_curve(curve):
    x1, y1, x2, y2 = curve
    return _clamp(((x1 - y1) + (x2 - y2)) / 2 / _SOFTNESS_SPAN, -1.0, 1.0)


class PressureMapper:
    """Turns physical pressure (0..1) into (touching?, output pressure 0..1)."""

    def __init__(self, threshold=0.01, maximum=1.0, curve=None, tip_up_assist=False):
        self.threshold = _clamp(threshold, 0.0, 0.9)
        self.maximum = _clamp(maximum, self.threshold + 0.02, 1.0)
        # Hysteresis: the tip is released below a lower threshold, so a stroke
        # is not cut by a small pressure wobble.
        self.release = self.threshold * (0.35 if tip_up_assist else 0.7)
        curve = curve or LINEAR
        self._lut = [bezier_y(i / (_LUT_SIZE - 1), curve) for i in range(_LUT_SIZE)]

    def curve_value(self, x):
        x = _clamp(x)
        pos = x * (_LUT_SIZE - 1)
        i = int(pos)
        if i >= _LUT_SIZE - 1:
            return self._lut[-1]
        frac = pos - i
        return self._lut[i] + (self._lut[i + 1] - self._lut[i]) * frac

    def map(self, raw, was_touching):
        limit = self.release if was_touching else self.threshold
        if raw <= limit or raw <= 0.0:
            return False, 0.0
        x = (raw - self.threshold) / (self.maximum - self.threshold)
        return True, self.curve_value(x)
