"""Custom-drawn widgets: pressure curve editor, test area, area editors
(screen and tablet), pen illustration and radial menu preview. They are drawn
with Gsk (GTK 4), with no extra dependencies."""

import math

import gi

gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Adw, Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import config as cfg  # noqa: E402
from ..curve import bezier_y  # noqa: E402
from ..mapping import Rect, proportional_area  # noqa: E402

PAD = 14

ACTION_ICONS = cfg.ACTION_ICONS


def _clamp(value, low=0.0, high=1.0):
    return min(high, max(low, value))


def _alpha(color, alpha):
    result = Gdk.RGBA()
    result.red, result.green, result.blue = color.red, color.green, color.blue
    result.alpha = color.alpha * alpha
    return result


class Painter:
    """Drawing shortcuts on top of a Gtk.Snapshot."""

    def __init__(self, widget, snapshot):
        self.widget = widget
        self.s = snapshot
        self.fg = widget.get_color()
        self.accent = Adw.StyleManager.get_default().get_accent_color_rgba()

    def color(self, base, alpha=1.0):
        return _alpha(self.fg if base == "fg" else self.accent, alpha)

    def fill(self, path, color):
        self.s.append_fill(path, Gsk.FillRule.WINDING, color)

    def stroke(self, path, color, width=1.0, dash=None):
        stroke = Gsk.Stroke.new(width)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        if dash:
            stroke.set_dash(dash)
        self.s.append_stroke(path, stroke, color)

    @staticmethod
    def rounded(x, y, w, h, r):
        builder = Gsk.PathBuilder.new()
        rect = Gsk.RoundedRect()
        rect.init_from_rect(Graphene.Rect().init(x, y, max(w, 0.1), max(h, 0.1)),
                            max(0.0, min(r, w / 2, h / 2)))
        builder.add_rounded_rect(rect)
        return builder.to_path()

    @staticmethod
    def polyline(points):
        builder = Gsk.PathBuilder.new()
        builder.move_to(*points[0])
        for point in points[1:]:
            builder.line_to(*point)
        return builder.to_path()

    @staticmethod
    def circle(x, y, r):
        builder = Gsk.PathBuilder.new()
        builder.add_circle(Graphene.Point().init(x, y), r)
        return builder.to_path()

    def text(self, x, y, text, size=11, alpha=0.7, align=0.0, bold=False, max_width=None):
        layout = self.widget.create_pango_layout(text)
        font = Pango.FontDescription.from_string(f"Sans {'Bold ' if bold else ''}{size}px")
        font.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(font)
        if max_width:
            layout.set_width(int(max(max_width, 1) * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
        width, height = layout.get_pixel_size()
        self.s.save()
        self.s.translate(Graphene.Point().init(x - width * align, y - height / 2))
        self.s.append_layout(layout, self.color("fg", alpha))
        self.s.restore()


class Canvas(Gtk.Widget):
    def __init__(self, label, width, height):
        super().__init__()
        self.set_size_request(width, height)
        self.set_hexpand(True)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def do_snapshot(self, snapshot):
        self.paint(Painter(self, snapshot), self.get_width(), self.get_height())

    def paint(self, p, w, h):
        pass

    def add_drag(self):
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)


class CurveEditor(Canvas):
    """Tip response: click threshold, maximum pressure and a Bézier curve with
    two draggable control points."""

    def __init__(self, on_changed):
        super().__init__("Tip pressure curve", 240, 240)
        self.on_changed = on_changed
        self.threshold, self.maximum = 0.01, 1.0
        self.curve = [1 / 3, 1 / 3, 2 / 3, 2 / 3]
        self.level = None
        self._drag = None
        self.add_drag()
        self.set_cursor_from_name("pointer")

    def set_values(self, threshold, maximum, curve):
        self.threshold, self.maximum, self.curve = threshold, maximum, list(curve)
        self.queue_draw()

    def set_level(self, level):
        self.level = level
        self.queue_draw()

    def _frame(self):
        return PAD, PAD, self.get_width() - 2 * PAD, self.get_height() - 2 * PAD

    def _to_screen(self, x, y):
        fx, fy, fw, fh = self._frame()
        return fx + x * fw, fy + (1 - y) * fh

    def _from_screen(self, sx, sy):
        fx, fy, fw, fh = self._frame()
        return _clamp((sx - fx) / fw), _clamp(1 - (sy - fy) / fh)

    def _handles(self):
        th, mx = self.threshold, self.maximum
        span = mx - th
        x1, y1, x2, y2 = self.curve
        return {"threshold": (th, 0.0), "maximum": (mx, 1.0),
                "p1": (th + x1 * span, y1), "p2": (th + x2 * span, y2)}

    def _begin(self, _gesture, sx, sy):
        best, best_d = None, 18.0
        for name, (x, y) in self._handles().items():
            hx, hy = self._to_screen(x, y)
            d = math.hypot(hx - sx, hy - sy)
            if d < best_d:
                best, best_d = name, d
        self._drag = (best, sx, sy) if best else None

    def _update(self, _gesture, dx, dy):
        if not self._drag:
            return
        name, sx, sy = self._drag
        x, y = self._from_screen(sx + dx, sy + dy)
        if name == "threshold":
            self.threshold = round(_clamp(x, 0.0, min(0.5, self.maximum - 0.05)), 3)
        elif name == "maximum":
            self.maximum = round(_clamp(x, self.threshold + 0.05, 1.0), 3)
        else:
            span = self.maximum - self.threshold
            index = 0 if name == "p1" else 2
            self.curve[index] = round(_clamp((x - self.threshold) / span), 3)
            self.curve[index + 1] = round(y, 3)
        self.queue_draw()
        self.on_changed(self.threshold, self.maximum, list(self.curve), False)

    def _end(self, *_args):
        if self._drag:
            self.on_changed(self.threshold, self.maximum, list(self.curve), True)
        self._drag = None

    def paint(self, p, w, h):
        fx, fy, fw, fh = self._frame()
        p.fill(p.rounded(fx, fy, fw, fh, 10), p.color("fg", 0.05))
        for i in range(1, 4):
            p.stroke(p.polyline([(fx + fw * i / 4, fy), (fx + fw * i / 4, fy + fh)]),
                     p.color("fg", 0.08))
            p.stroke(p.polyline([(fx, fy + fh * i / 4), (fx + fw, fy + fh * i / 4)]),
                     p.color("fg", 0.08))
        p.stroke(p.polyline([self._to_screen(0, 0), self._to_screen(1, 1)]),
                 p.color("fg", 0.2), dash=[4.0, 4.0])

        th, mx = self.threshold, self.maximum
        points = [self._to_screen(0, 0), self._to_screen(th, 0)]
        for i in range(1, 65):
            t = i / 64
            points.append(self._to_screen(th + t * (mx - th), bezier_y(t, self.curve)))
        points.append(self._to_screen(1, 1))
        p.stroke(p.polyline(points), p.color("accent"), 3.0)

        handles = self._handles()
        for a, b in (("threshold", "p1"), ("maximum", "p2")):
            p.stroke(p.polyline([self._to_screen(*handles[a]), self._to_screen(*handles[b])]),
                     p.color("fg", 0.35))
        for name, (x, y) in handles.items():
            sx, sy = self._to_screen(x, y)
            if name in ("p1", "p2"):
                p.fill(p.circle(sx, sy, 6), p.color("accent"))
            else:
                p.fill(p.rounded(sx - 5, sy - 5, 10, 10, 2), p.color("fg", 0.8))

        if self.level is not None:
            _, ly = self._to_screen(0, self.level)
            p.stroke(p.polyline([(fx, ly), (fx + fw, ly)]), p.color("accent", 0.45), 2.0)
        p.text(fx + fw - 8, fy + fh - 12, "force →", align=1.0, alpha=0.5)
        p.text(fx + 8, fy + 12, "↑ pressure", alpha=0.5)


class PressureTestArea(Canvas):
    """Area to try strokes: the width follows the pressure applications
    receive (with the curve already applied)."""

    def __init__(self, on_pressure):
        super().__init__("Pen test area", 260, 240)
        self.on_pressure = on_pressure
        self.strokes = []
        self.current = None
        self.pressure = 0.0
        stylus = Gtk.GestureStylus()
        stylus.connect("down", self._down)
        stylus.connect("motion", self._motion)
        stylus.connect("up", self._up)
        self.add_controller(stylus)
        self.set_cursor_from_name("crosshair")

    def clear(self):
        self.strokes = []
        self.queue_draw()

    @staticmethod
    def _pressure(gesture):
        ok, value = gesture.get_axis(Gdk.AxisUse.PRESSURE)
        return value if ok else 0.0

    def _down(self, gesture, x, y):
        self.pressure = self._pressure(gesture)
        self.current = [(x, y, self.pressure)]
        self.strokes.append(self.current)
        self.on_pressure(self.pressure)
        self.queue_draw()

    def _motion(self, gesture, x, y):
        pressure = self._pressure(gesture)
        if self.current is not None:
            self.current.append((x, y, pressure))
        if self.current is not None or pressure != self.pressure:
            self.pressure = pressure
            self.on_pressure(pressure)
            self.queue_draw()

    def _up(self, *_args):
        self.current = None
        self.pressure = 0.0
        self.on_pressure(None)
        self.queue_draw()

    def paint(self, p, w, h):
        p.fill(p.rounded(PAD, PAD, w - 2 * PAD, h - 2 * PAD, 10), p.color("fg", 0.05))
        if not self.strokes:
            p.text(w / 2, h / 2, "Draw here with your pen", size=14, align=0.5, alpha=0.45)
        ink = p.color("fg", 0.9)
        for stroke in self.strokes:
            for (x0, y0, p0), (x1, y1, p1) in zip(stroke, stroke[1:], strict=False):
                p.stroke(p.polyline([(x0, y0), (x1, y1)]), ink, 1 + 14 * (p0 + p1) / 2)
        track = w - 2 * PAD - 20
        p.fill(p.rounded(PAD + 10, h - PAD - 16, track, 6, 3), p.color("fg", 0.1))
        if self.pressure > 0:
            p.fill(p.rounded(PAD + 10, h - PAD - 16, track * self.pressure, 6, 3),
                   p.color("accent"))
        p.text(w - PAD - 10, h - PAD - 28, f"Pressure: {round(self.pressure * 100)}%",
               align=1.0)


class RectEditor(Canvas):
    """Fixed-aspect canvas with a (normalized 0..1) rectangle that can be
    moved, resized or redrawn by dragging."""

    MIN = 0.05

    def __init__(self, label, on_changed):
        super().__init__(label, 200, 220)
        self.on_changed = on_changed
        self.aspect = 16 / 9
        self.rect = Rect()
        self.editable = False
        self._drag = None
        self.add_drag()

    def set_editable(self, editable):
        self.editable = editable
        self.set_cursor_from_name("crosshair" if editable else None)
        self.queue_draw()

    def canvas(self):
        w, h = self.get_width() - 2 * PAD, self.get_height() - 2 * PAD
        if w <= 0 or h <= 0:
            return PAD, PAD, 1, 1
        if w / h > self.aspect:
            cw, ch = h * self.aspect, h
        else:
            cw, ch = w, w / self.aspect
        return PAD + (w - cw) / 2, PAD + (h - ch) / 2, cw, ch

    def to_screen(self, rect):
        cx, cy, cw, ch = self.canvas()
        return cx + rect.x * cw, cy + rect.y * ch, rect.w * cw, rect.h * ch

    def _norm(self, sx, sy):
        cx, cy, cw, ch = self.canvas()
        return _clamp((sx - cx) / cw), _clamp((sy - cy) / ch)

    def _begin(self, _gesture, sx, sy):
        if not self.editable:
            self._drag = None
            return
        x, y = self._norm(sx, sy)
        r = self.rect
        rx, ry, rw, rh = self.to_screen(r)
        corners = {"tl": (rx, ry), "tr": (rx + rw, ry), "bl": (rx, ry + rh),
                   "br": (rx + rw, ry + rh)}
        for name, (px, py) in corners.items():
            if math.hypot(px - sx, py - sy) < 14:
                self._drag = (name, x, y, r)
                return
        inside = r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h
        self._drag = ("move" if inside else "new", x, y, r)

    def _update(self, _gesture, dx, dy):
        if not self._drag:
            return
        mode, x0, y0, r = self._drag
        cx, cy, cw, ch = self.canvas()
        x1, y1 = _clamp(x0 + dx / cw), _clamp(y0 + dy / ch)
        if mode == "move":
            self.rect = Rect(_clamp(r.x + (x1 - x0), 0, 1 - r.w),
                             _clamp(r.y + (y1 - y0), 0, 1 - r.h), r.w, r.h)
        else:
            if mode == "new":
                ax, ay = x0, y0
            else:
                ax = r.x + r.w if mode[1] == "l" else r.x
                ay = r.y + r.h if mode[0] == "t" else r.y
            left, right = sorted((ax, x1))
            top, bottom = sorted((ay, y1))
            self.rect = Rect(left, top, max(self.MIN, right - left),
                             max(self.MIN, bottom - top)).clipped()
        self.queue_draw()

    def _end(self, *_args):
        if self._drag:
            self._drag = None
            self.on_changed(self.rect)

    def paint_selection(self, p, rect, fill_alpha, handles):
        x, y, w, h = self.to_screen(rect)
        path = p.rounded(x, y, w, h, 3)
        p.fill(path, p.color("accent", fill_alpha))
        p.stroke(path, p.color("accent"), 2.0)
        if handles:
            for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                p.fill(p.circle(px, py, 5), p.color("accent"))


class ScreenPreview(RectEditor):
    """Monitor layout and the chosen screen area."""

    def __init__(self, on_changed):
        super().__init__("Screen area", on_changed)
        self.desktop = None
        self.screen = {"type": "all"}

    def set_state(self, desktop, screen):
        self.desktop, self.screen = desktop, screen or {"type": "all"}
        _bx, _by, bw, bh = desktop.bbox
        self.aspect = bw / bh
        if self.screen.get("type") == "portion":
            self.rect, _ = desktop.target(self.screen)
        self.set_editable(self.screen.get("type") == "portion")

    def portion_pixels(self):
        bx, by, bw, bh = self.desktop.bbox
        r = self.rect
        return [round(bx + r.x * bw), round(by + r.y * bh), round(r.w * bw), round(r.h * bh)]

    def paint(self, p, w, h):
        if not self.desktop:
            return
        bx, by, bw, bh = self.desktop.bbox
        kind = self.screen.get("type", "all")
        for index, m in enumerate(self.desktop.monitors, 1):
            x, y, mw, mh = self.to_screen(Rect((m.x - bx) / bw, (m.y - by) / bh,
                                               m.width / bw, m.height / bh))
            path = p.rounded(x + 3, y + 3, mw - 6, mh - 6, 6)
            active = kind == "all" or (kind == "monitor"
                                        and self.screen.get("monitor") == m.connector)
            p.fill(path, p.color("accent", 0.22) if active else p.color("fg", 0.07))
            p.stroke(path, p.color("accent") if active else p.color("fg", 0.3), 2.0)
            p.text(x + mw / 2, y + mh / 2 - 8, str(index), size=22, align=0.5, alpha=0.6,
                   bold=True)
            p.text(x + mw / 2, y + mh / 2 + 16, m.name, size=11, align=0.5, alpha=0.6,
                   max_width=mw - 12)
        if kind == "portion":
            self.paint_selection(p, self.rect, 0.3, True)


class TabletPreview(RectEditor):
    """The tablet (rotated by orientation) with its active area."""

    def __init__(self, on_changed):
        super().__init__("Tablet area", on_changed)
        self.size_mm = (152.0, 95.0)
        self.orientation = 0
        self.portion = False
        self.effective = Rect()

    def set_state(self, size_mm, orientation, area, portion, force, target_aspect):
        self.orientation = orientation
        width, height = size_mm
        if orientation in (90, 270):
            width, height = height, width
        self.size_mm = (width, height)
        self.aspect = width / height
        self.portion = portion
        self.rect = area if portion else Rect()
        self.effective = (proportional_area(self.rect, self.size_mm, target_aspect)
                          if force else self.rect)
        self.set_editable(portion)

    def paint(self, p, w, h):
        cx, cy, cw, ch = self.canvas()
        p.fill(p.rounded(cx - 8, cy - 8, cw + 16, ch + 16, 12), p.color("fg", 0.12))
        p.fill(p.rounded(cx, cy, cw, ch, 4), p.color("fg", 0.06))
        # Marks the tablet's physical top edge.
        edge = {0: [(cx + cw * 0.4, cy - 4), (cx + cw * 0.6, cy - 4)],
                180: [(cx + cw * 0.4, cy + ch + 4), (cx + cw * 0.6, cy + ch + 4)],
                90: [(cx + cw + 4, cy + ch * 0.4), (cx + cw + 4, cy + ch * 0.6)],
                270: [(cx - 4, cy + ch * 0.4), (cx - 4, cy + ch * 0.6)]}[self.orientation]
        p.stroke(p.polyline(edge), p.color("fg", 0.45), 4.0)
        if self.effective != self.rect:
            x, y, ew, eh = self.to_screen(self.effective)
            p.fill(p.rounded(x, y, ew, eh, 3), p.color("accent", 0.3))
        if self.portion:
            self.paint_selection(p, self.rect, 0.12, True)
        else:
            self.paint_selection(p, self.effective,
                                 0.18 if self.effective == self.rect else 0.0, False)
        width, height = self.size_mm
        p.text(cx + cw / 2, cy + ch - 14,
               f"{self.effective.w * width:.0f} × {self.effective.h * height:.0f} mm",
               align=0.5)


class PenHero(Canvas):
    """Pen illustration with leader lines to its button cards. The cards fill
    three equal rows on the right (upper, lower, tip) and the pen parts light
    up when pressed."""

    PARTS = ("upper", "lower", "tip")

    def __init__(self):
        super().__init__("Pen and its buttons", 170, 330)
        self.set_hexpand(False)
        self.pressed = set()

    def set_pressed(self, part, pressed):
        if pressed:
            self.pressed.add(part)
        else:
            self.pressed.discard(part)
        self.queue_draw()

    def paint(self, p, w, h):
        cx, body = w * 0.32, 26.0
        top, grip, cone, tip = h * 0.03, h * 0.40, h * 0.72, h * 0.95
        buttons = {"upper": (h * 0.45, h * 0.53), "lower": (h * 0.56, h * 0.64)}
        anchors = {name: (cx + body / 2 - 1, (y0 + y1) / 2)
                   for name, (y0, y1) in buttons.items()}
        anchors["tip"] = (cx + 3, tip - 2)

        for index, part in enumerate(self.PARTS):
            ax, ay = anchors[part]
            target_y = (index + 0.5) / 3 * h
            elbow = w * 0.62
            active = part in self.pressed
            color = p.color("accent") if active else p.color("fg", 0.3)
            p.stroke(p.polyline([(ax + 4, ay), (elbow, ay), (w - 2, target_y)]), color,
                     2.0 if active else 1.5)
            p.fill(p.circle(ax + 4, ay, 3), color)

        p.fill(p.rounded(cx - body / 2, top, body, cone - top, body / 2),
               p.color("fg", 0.82))
        p.fill(p.rounded(cx - body / 2 + 2, grip, body - 4, cone - grip, 6),
               p.color("fg", 0.62))
        p.fill(p.rounded(cx - body / 2 + 5, top + 14, 4, grip - top - 30, 2),
               p.color("fg", 0.25))
        for name, (y0, y1) in buttons.items():
            active = name in self.pressed
            p.fill(p.rounded(cx + body / 2 - 6, y0, 7, y1 - y0, 3),
                   p.color("accent") if active else p.color("fg", 0.35))
        cone_path = Gsk.PathBuilder.new()
        cone_path.move_to(cx - body / 2 + 2, cone)
        cone_path.line_to(cx + body / 2 - 2, cone)
        cone_path.line_to(cx + 3, tip - 6)
        cone_path.line_to(cx - 3, tip - 6)
        cone_path.close()
        p.fill(cone_path.to_path(), p.color("fg", 0.72))
        p.fill(p.rounded(cx - 2, tip - 7, 4, 9, 2),
               p.color("accent") if "tip" in self.pressed else p.color("fg", 0.9))


class TabletIllustration(Canvas):
    """Small drawing of the tablet and pen."""

    def __init__(self):
        super().__init__("Tablet", 200, 118)

    def paint(self, p, w, h):
        tw = min(w - 24, (h - 12) * 1.75)
        th = tw / 1.75
        x, y = (w - tw) / 2, (h - th) / 2
        p.fill(p.rounded(x, y, tw, th, 12), p.color("fg", 0.86))
        inner = p.rounded(x + 12, y + 10, tw - 24, th - 20, 5)
        p.fill(inner, p.color("fg", 0.74))
        for mx, my in ((x + 12, y + 10), (x + tw - 12, y + 10), (x + 12, y + th - 10),
                       (x + tw - 12, y + th - 10)):
            p.fill(p.circle(mx, my, 2.2), p.color("accent"))
        sx, sy = x + tw * 0.86, y + th * 0.98
        ex, ey = x + tw * 0.56, y + th * 0.42
        p.stroke(p.polyline([(sx, sy), (ex, ey)]), p.color("accent"), 7.0)
        p.stroke(p.polyline([(ex, ey), (ex - 6, ey - 9)]), p.color("accent"), 3.0)
        p.stroke(p.polyline([(x + tw * 0.25, y + th * 0.62), (x + tw * 0.35, y + th * 0.48),
                             (x + tw * 0.45, y + th * 0.56), (ex - 6, ey - 9)]),
                 p.color("accent", 0.6), 2.0)


class ActionCard(Gtk.Button):
    """Card with the action assigned to a pen button."""

    def __init__(self, caption, on_click):
        super().__init__()
        self.add_css_class("card")
        self.add_css_class("action-card")
        box = Gtk.Box(spacing=12)
        self.icon = Gtk.Image(pixel_size=22, valign=Gtk.Align.CENTER)
        self.icon.add_css_class("action-icon")
        box.append(self.icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       valign=Gtk.Align.CENTER, hexpand=True)
        caption_label = Gtk.Label(label=caption, xalign=0)
        caption_label.add_css_class("caption")
        caption_label.add_css_class("dim-label")
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("heading")
        self.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.detail.add_css_class("caption")
        text.append(caption_label)
        text.append(self.title)
        text.append(self.detail)
        box.append(text)
        chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chevron.add_css_class("dim-label")
        box.append(chevron)
        self.set_child(box)
        self.caption = caption
        self.connect("clicked", on_click)

    def set_action(self, action):
        kind = action.get("type", "default")
        self.icon.set_from_icon_name(ACTION_ICONS.get(kind, "input-mouse-symbolic"))
        self.title.set_label(cfg.ACTIONS.get(kind, ("?",))[0].rstrip("…"))
        detail = ""
        if kind in ("keystroke", "modifier"):
            detail = action.get("label", "")
        elif kind == "run":
            detail = action.get("name", "")
        elif kind in cfg.FIXED_COMBOS:
            detail = cfg.FIXED_COMBOS[kind]["label"]
        elif kind == "touch":
            detail = "Tap = click · drag = scroll · hold = right click"
        self.detail.set_label(detail)
        self.detail.set_visible(bool(detail))
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{self.caption}: {cfg.action_label(action)}"])

    def set_pressed(self, pressed):
        if pressed:
            self.add_css_class("pressed")
        else:
            self.remove_css_class("pressed")


class RadialPreview(Canvas):
    """Preview of the radial menu (as it appears on screen). Clicking a slice
    changes its action."""

    def __init__(self, on_pick):
        super().__init__("Radial menu", 300, 300)
        self.on_pick = on_pick
        self.items = []
        self.hover = None
        click = Gtk.GestureClick()
        click.connect("released", self._clicked)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: self._set_hover(self._slot_at(x, y)))
        motion.connect("leave", lambda *_: self._set_hover(None))
        self.add_controller(motion)
        self.set_cursor_from_name("pointer")

    def set_items(self, items):
        self.items = list(items)
        self.queue_draw()

    def _geometry(self):
        w, h = self.get_width(), self.get_height()
        radius = min(w, h) / 2 - 8
        return w / 2, h / 2, radius, radius * 0.36

    def _slot_at(self, x, y):
        cx, cy, outer, inner = self._geometry()
        distance = math.hypot(x - cx, y - cy)
        if not self.items or distance < inner or distance > outer:
            return None
        angle = (math.atan2(y - cy, x - cx) + math.pi / 2) % (2 * math.pi)
        step = 2 * math.pi / len(self.items)
        return int(((angle + step / 2) % (2 * math.pi)) // step)

    def _set_hover(self, slot):
        if slot != self.hover:
            self.hover = slot
            self.queue_draw()

    def _clicked(self, _gesture, _n, x, y):
        slot = self._slot_at(x, y)
        if slot is not None:
            self.on_pick(slot)

    def _icon(self, p, name, x, y, size):
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        icon = theme.lookup_icon(name or "image-missing", None, size, self.get_scale_factor(),
                                 Gtk.TextDirection.NONE, 0)
        p.s.save()
        p.s.translate(Graphene.Point().init(x - size / 2, y - size / 2))
        try:
            icon.snapshot_symbolic(p.s, size, size, [p.color("fg", 0.9)])
        except (AttributeError, TypeError):
            icon.snapshot(p.s, size, size)
        p.s.restore()

    def paint(self, p, w, h):
        cx, cy, outer, inner = self._geometry()
        p.fill(p.circle(cx, cy, outer), p.color("fg", 0.07))
        p.stroke(p.circle(cx, cy, outer), p.color("fg", 0.12))
        count = len(self.items)
        if not count:
            return
        step = 2 * math.pi / count
        for index, action in enumerate(self.items):
            start = -math.pi / 2 + index * step - step / 2
            if index == self.hover:
                builder = Gsk.PathBuilder.new()
                builder.move_to(cx + inner * math.cos(start), cy + inner * math.sin(start))
                points = 24
                for k in range(points + 1):
                    a = start + step * k / points
                    builder.line_to(cx + outer * math.cos(a), cy + outer * math.sin(a))
                for k in range(points, -1, -1):
                    a = start + step * k / points
                    builder.line_to(cx + inner * math.cos(a), cy + inner * math.sin(a))
                builder.close()
                p.fill(builder.to_path(), p.color("accent", 0.22))
            p.stroke(p.polyline([(cx + inner * math.cos(start), cy + inner * math.sin(start)),
                                 (cx + outer * math.cos(start), cy + outer * math.sin(start))]),
                     p.color("fg", 0.1))
            middle = -math.pi / 2 + index * step
            r = (outer + inner) / 2
            x, y = cx + r * math.cos(middle), cy + r * math.sin(middle)
            self._icon(p, cfg.ACTION_ICONS.get(action.get("type")), x, y - 9, 20)
            label = cfg.action_label(action).rstrip("…")
            p.text(x, y + 14, label, size=10, align=0.5, alpha=0.8,
                   max_width=outer - inner - 6)
        p.fill(p.circle(cx, cy, inner - 4), p.color("accent", 0.15))
        self._icon(p, "input-tablet-symbolic", cx, cy, 24)
