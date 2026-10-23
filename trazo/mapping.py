"""Mapping geometry: orientation, tablet area, screen area and force
proportions."""

from dataclasses import dataclass, field

ORIENTATIONS = (0, 90, 180, 270)


def _clamp(value, low=0.0, high=1.0):
    return min(high, max(low, value))


@dataclass(frozen=True)
class Rect:
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0

    @classmethod
    def from_list(cls, values, default=None):
        try:
            x, y, w, h = (float(v) for v in values)
        except (TypeError, ValueError):
            return default if default is not None else cls()
        if w <= 0 or h <= 0:
            return default if default is not None else cls()
        return cls(x, y, w, h)

    def to_list(self):
        return [self.x, self.y, self.w, self.h]

    def clipped(self):
        x0, y0 = _clamp(self.x), _clamp(self.y)
        x1, y1 = _clamp(self.x + self.w), _clamp(self.y + self.h)
        if x1 - x0 < 0.01 or y1 - y0 < 0.01:
            return Rect()
        return Rect(x0, y0, x1 - x0, y1 - y0)


def to_user_view(x, y, orientation):
    """Normalized physical coordinates -> as the user sees them with the
    tablet rotated (0 = normal, 180 = left-handed, 90/270 = portrait)."""
    if orientation == 90:
        return 1.0 - y, x
    if orientation == 180:
        return 1.0 - x, 1.0 - y
    if orientation == 270:
        return y, 1.0 - x
    return x, y


def from_user_view(a, b, orientation):
    if orientation == 90:
        return b, 1.0 - a
    if orientation == 180:
        return 1.0 - a, 1.0 - b
    if orientation == 270:
        return 1.0 - b, a
    return a, b


def user_view_size(width_mm, height_mm, orientation):
    if orientation in (90, 270):
        return height_mm, width_mm
    return width_mm, height_mm


def proportional_area(area, size_mm, target_aspect):
    """Crops the area (centered) so its physical aspect ratio matches the
    screen's, like Wacom's "Force proportions"."""
    if target_aspect <= 0:
        return area
    width_mm, height_mm = size_mm
    area_w_mm, area_h_mm = area.w * width_mm, area.h * height_mm
    if area_h_mm <= 0 or area_w_mm <= 0:
        return area
    if area_w_mm / area_h_mm > target_aspect:
        new_w = area_h_mm * target_aspect / width_mm
        return Rect(area.x + (area.w - new_w) / 2, area.y, new_w, area.h)
    new_h = area_w_mm / target_aspect / height_mm
    return Rect(area.x, area.y + (area.h - new_h) / 2, area.w, new_h)


class Mapper:
    """Turns physical tablet positions into normalized positions on the whole
    desktop (0..1 over the rectangle that covers every screen)."""

    def __init__(self, orientation=0, tablet_area=None, tablet_size_mm=(152.0, 95.0),
                 target=None, target_aspect=16 / 9, force_proportions=False):
        self.orientation = orientation if orientation in ORIENTATIONS else 0
        self.size_mm = user_view_size(*tablet_size_mm, self.orientation)
        area = (tablet_area or Rect()).clipped()
        if force_proportions:
            area = proportional_area(area, self.size_mm, target_aspect)
        self.area = area
        self.target = target or Rect()

    def tablet_fraction(self, x, y):
        a, b = to_user_view(x, y, self.orientation)
        return (_clamp((a - self.area.x) / self.area.w),
                _clamp((b - self.area.y) / self.area.h))

    def map(self, x, y):
        s, t = self.tablet_fraction(x, y)
        return self.target.x + s * self.target.w, self.target.y + t * self.target.h

    def mm_delta(self, dx, dy):
        """Physical (normalized) movement -> millimeters in the user's view."""
        if self.orientation == 90:
            da = -dy
            db = dx
        elif self.orientation == 180:
            da, db = -dx, -dy
        elif self.orientation == 270:
            da, db = dy, -dx
        else:
            da, db = dx, dy
        width_mm, height_mm = self.size_mm
        return da * width_mm, db * height_mm


@dataclass(frozen=True)
class Monitor:
    connector: str
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False


@dataclass
class Desktop:
    monitors: list = field(default_factory=list)

    @property
    def bbox(self):
        if not self.monitors:
            return 0, 0, 1920, 1080
        x0 = min(m.x for m in self.monitors)
        y0 = min(m.y for m in self.monitors)
        x1 = max(m.x + m.width for m in self.monitors)
        y1 = max(m.y + m.height for m in self.monitors)
        return x0, y0, x1 - x0, y1 - y0

    def monitor(self, connector):
        for m in self.monitors:
            if m.connector == connector:
                return m
        return None

    def normalize(self, x, y, w, h):
        bx, by, bw, bh = self.bbox
        return Rect((x - bx) / bw, (y - by) / bh, w / bw, h / bh)

    def target(self, screen):
        """Returns (normalized rectangle, pixel aspect ratio) for the screen
        setting: all displays, one monitor or a portion."""
        kind = (screen or {}).get("type", "all")
        bx, by, bw, bh = self.bbox
        if kind == "monitor":
            m = self.monitor(screen.get("monitor"))
            if m:
                return self.normalize(m.x, m.y, m.width, m.height), m.width / m.height
        elif kind == "portion":
            rect = screen.get("rect")
            if rect and len(rect) == 4 and rect[2] > 0 and rect[3] > 0:
                x, y, w, h = rect
                norm = self.normalize(x, y, w, h).clipped()
                return norm, (norm.w * bw) / (norm.h * bh)
        return Rect(), bw / bh

    def toggle_targets(self):
        """"Display toggle" cycle: all -> each monitor -> all."""
        targets = [{"type": "all"}]
        if len(self.monitors) > 1:
            for m in sorted(self.monitors, key=lambda m: (m.x, m.y)):
                targets.append({"type": "monitor", "monitor": m.connector})
        return targets
