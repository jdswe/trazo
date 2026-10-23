"""Driver engine: takes the raw pen events and produces the output (virtual
pen, pointer, mouse, keyboard and on-screen effects) by applying the active
profile.

It never touches devices: everything goes out through a "sink" (see
daemon.VirtualDevices), so it can be tested without hardware."""

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import config as cfg
from . import evdev as ev
from . import keys
from .curve import PressureMapper
from .mapping import Desktop, Mapper, Rect, to_user_view

CLICK_ACTIONS = {name for name, (_label, kind) in cfg.ACTIONS.items() if kind == "click"}
LOCK_ACTIONS = {"click_lock": "click", "right_click_lock": "right", "middle_click_lock": "middle"}
DEFAULT_ACTIONS = {"tip": "click", "lower": "right_click", "upper": "middle_click"}
PAN_UNITS_PER_MM = 30      # 120 units = 1 wheel "notch" (~4 mm of pen)
WHEEL_NOTCH = 120
SIDE_CLICK_PRESSURE = 0.5  # pressure used when a side button "clicks"
TOUCH_SLOP_MM = 2.5        # touch mode: moving more than this = scroll
HOLD_TIME = 0.6            # touch mode: holding still = right click
MOMENTUM_TAU = 0.35        # s, how long momentum lasts after a flick
MOMENTUM_MIN = 90.0        # wheel units/s below which momentum stops
POINTER_EPSILON = 0.0002   # minimum (normalized) motion to move the pointer

# Virtual pen codes for each click. The service sets GNOME up so BTN_STYLUS is
# middle click, BTN_STYLUS2 right click and BTN_STYLUS3 back.
PEN_CODES = {"middle": ev.BTN_STYLUS, "right": ev.BTN_STYLUS2, "back": ev.BTN_STYLUS3}
MOUSE_CODES = {"left": ev.BTN_LEFT, "middle": ev.BTN_MIDDLE, "right": ev.BTN_RIGHT,
               "back": ev.BTN_SIDE, "forward": ev.BTN_EXTRA}


@dataclass(frozen=True)
class PenState:
    prox: bool = False
    tool: str = "pen"
    u: float = 0.0
    v: float = 0.0
    pressure: float = 0.0
    distance: float = 0.0
    touch: bool = False
    buttons: frozenset = field(default_factory=frozenset)


class OneEuroFilter:
    """"1 €" filter (Casiez et al.): removes jitter when the pen moves slowly
    without adding lag when it moves fast."""

    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.value = None
        self.deriv = 0.0
        self.t = None

    @staticmethod
    def _alpha(dt, cutoff):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, value, t):
        if self.value is None:
            self.value, self.t = value, t
            return value
        dt = max(t - self.t, 1e-4)
        self.t = t
        a_d = self._alpha(dt, self.d_cutoff)
        self.deriv = a_d * (value - self.value) / dt + (1 - a_d) * self.deriv
        a = self._alpha(dt, self.min_cutoff + self.beta * abs(self.deriv))
        self.value = a * value + (1 - a) * self.value
        return self.value


def make_smoother(strength):
    """strength 0..1 (0 = no smoothing)."""
    if strength <= 0.01:
        return None
    min_cutoff = 0.6 + (1.0 - strength) * 6.0
    beta = 30.0 * (1.0 - 0.6 * strength)
    return OneEuroFilter(min_cutoff, beta), OneEuroFilter(min_cutoff, beta)


class Engine:
    def __init__(self, sink, absinfo, clock=time.monotonic):
        self.sink = sink
        self.clock = clock
        self.absx = absinfo.get(ev.ABS_X) or ev.AbsInfo(maximum=15200, resolution=100)
        self.absy = absinfo.get(ev.ABS_Y) or ev.AbsInfo(maximum=9500, resolution=100)
        self.absp = absinfo.get(ev.ABS_PRESSURE)
        self.absd = absinfo.get(ev.ABS_DISTANCE)
        self.size_mm = (self._mm(self.absx, 152.0), self._mm(self.absy, 95.0))
        self.desktop = Desktop()
        self.on_button = None    # callback(name, pressed) for the UI
        self.on_capture = None   # callback(kind, rect) for "click to define"
        self.profile_id = cfg.DEFAULT_PROFILE_ID
        self.mode_override = None
        self.target_override = None
        self.touch_override = None
        self.capture = None
        self.smoothing = None
        self.smoother = None
        # Move the system pointer while the pen hovers only if the GNOME
        # extension can hide the duplicate cursor; otherwise GNOME would show
        # two cursors.
        self.hover_pointer = False
        self._raw = {"x": 0, "y": 0, "p": 0, "d": 0, "tool": None,
                     "lower": False, "upper": False}
        self._dropped = False
        self._emitted_mode = None
        self._reset_runtime()
        self.set_profile(cfg.DEFAULT_PROFILE, cfg.DEFAULT_CONFIG["advanced"])

    @staticmethod
    def _mm(info, fallback):
        if info.resolution > 0:
            return (info.maximum - info.minimum) / info.resolution
        return fallback

    def _reset_runtime(self):
        self.prox = False
        self.tool = "pen"
        self.touching = False
        self.phys = {"lower": False, "upper": False}
        self.active = {}            # origin -> (action, mode) being pressed
        self.armed = {}             # "click & tap": button -> pending action
        self.contact = set()        # reasons for the virtual pen to be in contact
        self.pen_holds = defaultdict(set)  # BTN_STYLUS* code -> origins
        self.locks = set()
        self.pan = set()
        self.pan_last = None
        self.pan_uv = None
        self.pan_acc = [0.0, 0.0]
        self.scroll_samples = deque(maxlen=24)
        self.momentum = None
        self.touch_state = None
        self.precision = None
        self.pressure_hold = None
        self.x = self.y = 0.0
        self.mapped = (0.0, 0.0)
        self.u = self.v = 0.0
        self.p_curve = 0.0
        self.snap = None
        self.dc_last_up = -math.inf
        self.dc_down = None
        self.mouse_last = None
        self.mouse_acc = [0.0, 0.0]
        self.last_pen_uv = None
        self.last_pointer_uv = None

    # --- Configuration -----------------------------------------------------
    def set_profile(self, profile, advanced, double_click_ms=400, profile_id=None):
        tip = profile["tip"]
        self.pressure = PressureMapper(tip["threshold"], tip["maximum"], tip["curve"],
                                       tip.get("tip_up_assist", False))
        self.buttons_cfg = profile["buttons"]
        self.button_mode = profile.get("button_mode", "hover")
        self.dcd_mm = float(profile.get("double_click_distance", 0.0))
        self.mapping_cfg = profile["mapping"]
        self.advanced = advanced
        self.dc_time = double_click_ms / 1000.0
        smoothing = float(profile.get("smoothing", 0.0))
        if smoothing != self.smoothing:
            self.smoothing = smoothing
            self.smoother = make_smoother(smoothing)
        if profile_id is not None:
            self.profile_id = profile_id
        self._rebuild_mapper()

    def set_desktop(self, desktop):
        self.desktop = desktop
        self._rebuild_mapper()

    def reset_overrides(self):
        self.mode_override = None
        self.target_override = None
        self.touch_override = None
        self._rebuild_mapper()

    def _rebuild_mapper(self):
        m = self.mapping_cfg
        screen = self.target_override or m.get("screen")
        target, aspect = self.desktop.target(screen)
        area_cfg = m.get("tablet_area") or {}
        area = Rect()
        if area_cfg.get("type") == "portion":
            area = Rect.from_list(area_cfg.get("rect"))
        self.mapper = Mapper(m.get("orientation", 0), area, self.size_mm, target, aspect,
                             bool(m.get("force_proportions")))

    @property
    def mode(self):
        return self.mode_override or self.mapping_cfg.get("mode", "pen")

    @property
    def touch_mode(self):
        return self._resolve("tip").get("type") == "touch"

    @property
    def needs_tick(self):
        """The service must call tick() often (momentum, press-and-hold)."""
        pending = self.touch_state is not None and self.touch_state["state"] == "pending"
        return self.momentum is not None or pending

    # --- Input -------------------------------------------------------------
    def handle(self, events):
        raw = self._raw
        for ev_type, code, value in events:
            if ev_type == ev.EV_SYN:
                if code == ev.SYN_DROPPED:
                    self._dropped = True
                elif code == ev.SYN_REPORT:
                    if self._dropped:
                        self._dropped = False
                    else:
                        self._frame()
            elif self._dropped:
                continue
            elif ev_type == ev.EV_ABS:
                if code == ev.ABS_X:
                    raw["x"] = value
                elif code == ev.ABS_Y:
                    raw["y"] = value
                elif code == ev.ABS_PRESSURE:
                    raw["p"] = value
                elif code == ev.ABS_DISTANCE:
                    raw["d"] = value
            elif ev_type == ev.EV_KEY:
                if code in (ev.BTN_TOOL_PEN, ev.BTN_TOOL_RUBBER):
                    tool = "pen" if code == ev.BTN_TOOL_PEN else "rubber"
                    if value:
                        raw["tool"] = tool
                    elif raw["tool"] == tool:
                        raw["tool"] = None
                elif code == ev.BTN_STYLUS:
                    raw["lower"] = bool(value)
                elif code == ev.BTN_STYLUS2:
                    raw["upper"] = bool(value)

    def device_lost(self):
        """The tablet was unplugged: release everything."""
        self._raw.update(tool=None, lower=False, upper=False, p=0)
        if self.prox:
            self._prox_out()

    def _frame(self):
        raw = self._raw
        now = self.clock()
        if self._emitted_mode is not None and self._emitted_mode != self.mode:
            if self._emitted_mode == "pen":
                self.sink.pen(PenState(prox=False))
            self.mouse_last = None
        self._emitted_mode = self.mode

        if raw["tool"] is None:
            if self.prox:
                self._prox_out()
            return
        if not self.prox:
            self.prox = True
            self.mouse_last = None
            if self.smoother:
                for f in self.smoother:
                    f.reset()
        self.tool = raw["tool"]

        x, y = self.absx.normalize(raw["x"]), self.absy.normalize(raw["y"])
        if self.smoother:
            x, y = self.smoother[0](x, now), self.smoother[1](y, now)
        self.x, self.y = x, y
        pressure = self.absp.normalize(raw["p"]) if self.absp else 0.0
        distance = self.absd.normalize(raw["d"]) if self.absd else 0.0
        touching, self.p_curve = self.pressure.map(pressure, self.touching)
        self._update_position()

        for name in ("lower", "upper"):
            if raw[name] != self.phys[name]:
                self.phys[name] = raw[name]
                if self.on_button:
                    self.on_button(name, raw[name])
                if raw[name]:
                    self._button_down(name)
                else:
                    self._button_up(name)

        if touching != self.touching:
            self.touching = touching
            if self.on_button:
                self.on_button("tip", touching)
            if touching:
                self._tip_down(now)
            else:
                self._tip_up(now)

        if self.touch_state and self.touching:
            self._touch_motion(now)
        elif self.pan and self.touching:
            self._scroll_motion(now)
        else:
            self.pan_last = None

        if self.mode == "mouse":
            self._mouse_motion(now)
        else:
            self._emit_pen(distance)

    def tick(self, now=None):
        """Advances time-based behavior: press-and-hold and momentum."""
        now = self.clock() if now is None else now
        state = self.touch_state
        if (state and state["state"] == "pending" and self.touching
                and now - state["t0"] >= HOLD_TIME):
            self._touch_hold()
        momentum = self.momentum
        if momentum:
            dt = now - momentum["t"]
            if dt <= 0:
                return
            momentum["t"] = now
            decay = math.exp(-dt / MOMENTUM_TAU)
            momentum["v"] *= decay
            momentum["h"] *= decay
            if max(abs(momentum["v"]), abs(momentum["h"])) < MOMENTUM_MIN:
                self.momentum = None
                return
            self._emit_scroll(momentum["v"] * dt, momentum["h"] * dt, momentum["at"])

    def _update_position(self):
        u, v = self.mapper.map(self.x, self.y)
        self.mapped = (u, v)
        if self.precision:
            factor = float(self.advanced.get("precision_factor", 0.35))
            u0, v0 = self.precision
            u, v = u0 + (u - u0) * factor, v0 + (v - v0) * factor
        self.u, self.v = u, v

    def _dist_mm(self, a, b):
        dx, dy = self.mapper.mm_delta(a[0] - b[0], a[1] - b[1])
        return math.hypot(dx, dy)

    def _feedback(self, kind, uv):
        if self.advanced.get("visual_feedback", True):
            self.sink.feedback(kind, uv)

    # --- Tip and buttons ---------------------------------------------------
    def _resolve(self, origin):
        action = self.buttons_cfg.get(origin) or {"type": "default"}
        if action.get("type", "default") == "default":
            action = {"type": DEFAULT_ACTIONS[origin]}
        if origin == "tip" and self.touch_override is not None:
            if self.touch_override:
                return {"type": "touch"}
            if action["type"] == "touch":
                return {"type": "click"}
        return action

    def _tip_down(self, now):
        self.momentum = None
        if self.capture:
            self._capture_point()
            return
        if self.pan:
            return
        for name in ("lower", "upper"):
            if name in self.armed:
                self._press("tip", self.armed[name])
                return
        action = self._resolve("tip")
        if action["type"] == "touch" and self.mode == "pen":
            uv = (self.u, self.v)
            self.touch_state = {"xy": (self.x, self.y), "uv": uv, "t0": now,
                                "state": "pending"}
            self._feedback("hold-start", uv)
            return
        if self.dcd_mm > 0 and self.mode == "pen":
            point = (self.x, self.y)
            if (now - self.dc_last_up <= self.dc_time and self.dc_down
                    and self._dist_mm(point, self.dc_down[0]) <= self.dcd_mm):
                self.snap = self.dc_down
            else:
                self.dc_down = (point, (self.u, self.v))
        self._press("tip", action)
        if action["type"] in ("click", "touch") and self.mode == "pen":
            self._feedback("tap", self.snap[1] if self.snap else (self.u, self.v))

    def _tip_up(self, now):
        self.dc_last_up = now
        self.snap = None
        state = self.touch_state
        if state:
            self.touch_state = None
            if state["state"] == "pending":
                self._feedback("hold-cancel", state["uv"])
                self.sink.pen_pulse("touch", 1)
                self._feedback("tap", state["uv"])
            elif state["state"] == "scroll":
                self._start_momentum(now, state["uv"])
            return
        self._release("tip")

    def _touch_motion(self, now):
        state = self.touch_state
        if state["state"] == "pending":
            if self._dist_mm((self.x, self.y), state["xy"]) > TOUCH_SLOP_MM:
                state["state"] = "scroll"
                self._feedback("hold-cancel", state["uv"])
                self.pan_last = state["xy"]
                self.pan_uv = state["uv"]
                self.pan_acc = [0.0, 0.0]
                self.scroll_samples.clear()
            elif now - state["t0"] >= HOLD_TIME:
                self._touch_hold()
        if state["state"] == "scroll":
            self._scroll_motion(now)

    def _touch_hold(self):
        state = self.touch_state
        state["state"] = "held"
        self.sink.pen_pulse(PEN_CODES["right"], 1)
        self._feedback("right", state["uv"])

    def _button_down(self, name):
        if self.capture:
            return
        action = self._resolve(name)
        if self.button_mode == "click_tap" and action["type"] in CLICK_ACTIONS:
            self.armed[name] = action
            return
        self._press(name, action)

    def _button_up(self, name):
        if self.armed.pop(name, None) is not None:
            return
        self._release(name)

    def _press(self, origin, action):
        mode = self.mode
        self.active[origin] = (action, mode)
        self._dispatch(origin, action, mode, True)

    def _release(self, origin):
        entry = self.active.pop(origin, None)
        if entry:
            action, mode = entry
            self._dispatch(origin, action, mode, False)

    def trigger(self, action):
        """Runs a whole action (press and release), e.g. from the radial menu."""
        mode = self.mode
        self._dispatch("radial", action, mode, True)
        self._dispatch("radial", action, mode, False)

    # --- Actions -----------------------------------------------------------
    def _dispatch(self, origin, action, mode, pressed):
        kind = action.get("type")
        if kind == "touch":
            kind = "click"   # outside pen mode, "touch" is a plain click
        pen = mode == "pen"
        at = (self.u, self.v) if pen else None

        if kind == "click":
            if pen:
                self._set(self.contact, origin, pressed)
                if pressed and origin != "tip":
                    self._feedback("tap", at)
            else:
                self.sink.mouse_button(ev.BTN_LEFT, pressed)
        elif kind in ("right_click", "middle_click", "back"):
            button = {"right_click": "right", "middle_click": "middle", "back": "back"}[kind]
            if pen:
                self._set(self.pen_holds[PEN_CODES[button]], origin, pressed)
                if pressed and button == "right":
                    self._feedback("right", at)
            else:
                self.sink.mouse_button(MOUSE_CODES[button], pressed)
        elif kind == "forward":
            if pen:
                self.sink.pointer_button(ev.BTN_EXTRA, pressed, at)
            else:
                self.sink.mouse_button(ev.BTN_EXTRA, pressed)
        elif kind in ("double_click", "middle_double_click"):
            if pressed:
                middle = kind == "middle_double_click"
                if pen:
                    self.sink.pen_pulse(PEN_CODES["middle"] if middle else "touch", 2)
                    self._feedback("double", at)
                else:
                    self.sink.mouse_pulse(ev.BTN_MIDDLE if middle else ev.BTN_LEFT, 2)
        elif kind in LOCK_ACTIONS:
            if pressed:
                self._toggle_lock(LOCK_ACTIONS[kind], pen)
        elif kind in ("keystroke", "modifier") or kind in cfg.FIXED_COMBOS:
            combo = cfg.FIXED_COMBOS.get(kind) or action
            codes = keys.sort_combo(list(combo.get("keys") or []))
            if codes:
                self.sink.keys(codes if pressed else list(reversed(codes)), pressed)
        elif kind == "switch_app":
            if pressed:
                self.sink.keys([keys.KEY_LEFTALT], True)
                self.sink.keys([keys.KEY_TAB], True)
                self.sink.keys([keys.KEY_TAB], False)
            else:
                self.sink.keys([keys.KEY_LEFTALT], False)
        elif kind == "pan_scroll":
            self._set(self.pan, origin, pressed)
            self.pan_last = None
        elif kind in ("scroll_up", "scroll_down"):
            if pressed:
                self.sink.scroll(WHEEL_NOTCH if kind == "scroll_up" else -WHEEL_NOTCH, 0, at)
        elif kind == "precision_mode":
            self.precision = self.mapped if pressed else None
            self._update_position()
            if self.advanced.get("precision_overlay", True):
                self.sink.overlay(pressed, self._precision_rect() if pressed else None)
        elif kind == "pressure_hold":
            if pressed:
                self.pressure_hold = self.p_curve if self.touching else "pending"
            else:
                self.pressure_hold = None
        elif kind == "mode_toggle":
            if pressed:
                self.mode_override = "mouse" if self.mode == "pen" else "pen"
        elif kind == "touch_toggle":
            if pressed:
                self.touch_override = not self.touch_mode
        elif kind == "display_toggle":
            if pressed:
                self._toggle_display()
        elif kind == "radial_menu":
            if pressed:
                self.sink.radial((self.u, self.v))
        elif kind == "run":
            if pressed:
                self.sink.launch(action)
        elif kind == "settings":
            if pressed:
                self.sink.launch({"type": "run", "app_id": "io.github.jdswe.Trazo.desktop",
                                  "command": "trazo"})

    @staticmethod
    def _set(container, item, present):
        if present:
            container.add(item)
        else:
            container.discard(item)

    def _toggle_lock(self, which, pen):
        on = which not in self.locks
        self._set(self.locks, which, on)
        if pen:
            if which == "click":
                self._set(self.contact, "lock", on)
            else:
                self._set(self.pen_holds[PEN_CODES[which]], "lock", on)
        else:
            self.sink.mouse_button(MOUSE_CODES["left" if which == "click" else which], on)

    def _toggle_display(self):
        targets = self.desktop.toggle_targets()
        current = self.target_override or self.mapping_cfg.get("screen") or {"type": "all"}
        try:
            index = targets.index({k: current[k] for k in ("type", "monitor") if k in current})
        except ValueError:
            index = -1
        self.target_override = targets[(index + 1) % len(targets)]
        self._rebuild_mapper()
        self._update_position()

    def _precision_rect(self):
        """Screen region the tablet covers in precision mode."""
        target = self.mapper.target
        factor = float(self.advanced.get("precision_factor", 0.35))
        u0, v0 = self.precision
        return [u0 + (target.x - u0) * factor, v0 + (target.y - v0) * factor,
                target.w * factor, target.h * factor]

    # --- Motion ------------------------------------------------------------
    def _scroll_motion(self, now):
        """Scrolls the content following the pen (pan and touch mode)."""
        point = (self.x, self.y)
        if self.pan_last is None:
            self.pan_last = point
            self.pan_uv = (self.u, self.v)
            self.pan_acc = [0.0, 0.0]
            self.scroll_samples.clear()
            return
        dx_mm, dy_mm = self.mapper.mm_delta(point[0] - self.pan_last[0],
                                            point[1] - self.pan_last[1])
        self.pan_last = point
        k = PAN_UNITS_PER_MM * float(self.advanced.get("pan_speed", 1.0))
        # Content follows the pen: moving the pen down scrolls the view up.
        vertical, horizontal = dy_mm * k, -dx_mm * k
        self.scroll_samples.append((now, vertical, horizontal))
        self._emit_scroll(vertical, horizontal, self.pan_uv if self.mode == "pen" else None)

    def _emit_scroll(self, vertical, horizontal, at):
        self.pan_acc[0] += vertical
        self.pan_acc[1] += horizontal
        v, h = int(self.pan_acc[0]), int(self.pan_acc[1])
        self.pan_acc[0] -= v
        self.pan_acc[1] -= h
        if v or h:
            self.sink.scroll(v, h, at)

    def _start_momentum(self, now, at):
        recent = [s for s in self.scroll_samples if now - s[0] <= 0.1]
        if len(recent) < 2:
            return
        span = max(now - recent[0][0], 1 / 120)
        vertical = sum(s[1] for s in recent) / span
        horizontal = sum(s[2] for s in recent) / span
        if max(abs(vertical), abs(horizontal)) >= 2 * MOMENTUM_MIN:
            self.momentum = {"v": vertical, "h": horizontal, "t": now,
                             "at": at if self.mode == "pen" else None}

    def _mouse_motion(self, now):
        point = (self.x, self.y)
        if self.mouse_last is None or (self.pan and self.touching):
            self.mouse_last = (point, now)
            return
        (lx, ly), last_t = self.mouse_last
        self.mouse_last = (point, now)
        dx_mm, dy_mm = self.mapper.mm_delta(point[0] - lx, point[1] - ly)
        dt = max(now - last_t, 1e-3)
        speed = float(self.mapping_cfg.get("mouse_speed", 0.0))
        accel = float(self.mapping_cfg.get("mouse_acceleration", 0.0))
        velocity = math.hypot(dx_mm, dy_mm) / dt
        factor = 4.0 * 2 ** (2 * speed) * (1 + accel * min(velocity / 50.0, 4.0))
        self.mouse_acc[0] += dx_mm * factor
        self.mouse_acc[1] += dy_mm * factor
        ix, iy = int(self.mouse_acc[0]), int(self.mouse_acc[1])
        self.mouse_acc[0] -= ix
        self.mouse_acc[1] -= iy
        if ix or iy:
            self.sink.mouse_move(ix, iy)

    def _emit_pen(self, distance):
        u, v = self.u, self.v
        if self.snap:
            if self._dist_mm((self.x, self.y), self.snap[0]) > self.dcd_mm:
                self.snap = None
            else:
                u, v = self.snap[1]
        if self.touch_state:
            u, v = self.touch_state["uv"]
        elif self.pan and self.touching and self.pan_uv:
            u, v = self.pan_uv
        touch = bool(self.contact)
        pressure = 0.0
        if touch:
            if "tip" in self.contact:
                p = self.p_curve
                if self.pressure_hold == "pending":
                    self.pressure_hold = p
                if self.pressure_hold is not None:
                    p = self.pressure_hold
            else:
                p = SIDE_CLICK_PRESSURE
            floor = float(self.advanced.get("contact_floor", 0.06))
            pressure = floor + p * (1.0 - floor)
        buttons = frozenset(code for code, origins in self.pen_holds.items() if origins)
        self.last_pen_uv = (u, v)
        self.sink.pen(PenState(True, self.tool, u, v, pressure, distance, touch, buttons))
        # Like on Windows, the pen also moves the system pointer while it
        # hovers, so the dock, hot corners and GNOME menus react to it. It does
        # not move while drawing.
        if self.hover_pointer and not touch and not self.touching:
            self._sync_pointer((u, v))

    def _sync_pointer(self, uv, force=False):
        if not self.advanced.get("unified_cursor", True):
            return
        last = self.last_pointer_uv
        if force or last is None or max(abs(uv[0] - last[0]),
                                        abs(uv[1] - last[1])) > POINTER_EPSILON:
            self.last_pointer_uv = uv
            self.sink.pointer_move(uv)

    def _prox_out(self):
        if self.touch_state and self.touch_state["state"] == "pending":
            self._feedback("hold-cancel", self.touch_state["uv"])
        self.touch_state = None
        for origin in list(self.active):
            self._release(origin)
        self.armed.clear()
        if self.mode == "mouse":
            for which in self.locks:
                self.sink.mouse_button(MOUSE_CODES["left" if which == "click" else which], False)
        self.locks.clear()
        self.contact.clear()
        self.pen_holds.clear()
        self.pan.clear()
        self.precision = None
        self.pressure_hold = None
        self.snap = None
        self.touching = False
        self.phys = {"lower": False, "upper": False}
        self.prox = False
        if self.mode == "pen":
            self.sink.pen(PenState(prox=False))
            if self.last_pen_uv:
                self._sync_pointer(self.last_pen_uv, force=True)

    # --- "Click to define" ------------------------------------------------
    def start_capture(self, kind):
        self.capture = {"kind": kind, "points": []}
        if kind == "screen":
            self.capture["saved"] = self.target_override
            self.target_override = {"type": "all"}
            self._rebuild_mapper()

    def cancel_capture(self):
        if self.capture and self.capture["kind"] == "screen":
            self.target_override = self.capture.get("saved")
            self._rebuild_mapper()
        self.capture = None

    def _capture_point(self):
        cap = self.capture
        if cap["kind"] == "screen":
            cap["points"].append(self.mapped)
        else:
            cap["points"].append(to_user_view(self.x, self.y, self.mapper.orientation))
        self._feedback("tap", (self.u, self.v))
        if len(cap["points"]) < 2:
            return
        (a0, b0), (a1, b1) = cap["points"]
        x0, x1 = sorted((a0, a1))
        y0, y1 = sorted((b0, b1))
        kind = cap["kind"]
        self.cancel_capture()
        if kind == "screen":
            bx, by, bw, bh = self.desktop.bbox
            rect = [round(bx + x0 * bw), round(by + y0 * bh),
                    round((x1 - x0) * bw), round((y1 - y0) * bh)]
        else:
            rect = [x0, y0, x1 - x0, y1 - y0]
        if self.on_capture and rect[2] > 0 and rect[3] > 0:
            self.on_capture(kind, rect)

    # --- Diagnostics -------------------------------------------------------
    def diagnostics(self):
        raw = self._raw
        return {
            "prox": self.prox, "tool": raw["tool"] or "",
            "x": raw["x"], "y": raw["y"], "pressure": raw["p"], "distance": raw["d"],
            "x_max": self.absx.maximum, "y_max": self.absy.maximum,
            "pressure_max": self.absp.maximum if self.absp else 0,
            "distance_max": self.absd.maximum if self.absd else 0,
            "lower": raw["lower"], "upper": raw["upper"], "tip": self.touching,
            "output_pressure": round(self.p_curve, 3) if self.touching else 0.0,
            "u": round(self.u, 4), "v": round(self.v, 4),
            "mode": self.mode, "touch_mode": self.touch_mode,
        }
