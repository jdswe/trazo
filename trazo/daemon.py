"""trazo-daemon: the user-space "driver".

Takes exclusive control of the physical pen, applies the active profile with
the engine and emits the result through virtual devices (uinput). It talks to
the UI over D-Bus (io.github.jdswe.Trazo1)."""

import json
import os
import shlex
import signal
import sys
import time

from gi.repository import Gio, GLib

from . import config as cfg
from . import devices, gnome
from . import evdev as ev
from . import keys
from .engine import Engine, PenState

BUS_NAME = "io.github.jdswe.Trazo1"
OBJECT_PATH = "/io/github/jdswe/Trazo1"
INTERFACE_XML = """
<node>
  <interface name="io.github.jdswe.Trazo1">
    <method name="Reload"/>
    <method name="GetStatus"><arg type="s" direction="out" name="status"/></method>
    <method name="StartCapture"><arg type="s" direction="in" name="kind"/></method>
    <method name="CancelCapture"/>
    <method name="SetDiagnostics"><arg type="b" direction="in" name="enabled"/></method>
    <signal name="StatusChanged"><arg type="s" name="status"/></signal>
    <signal name="Diagnostics"><arg type="s" name="data"/></signal>
    <signal name="Button"><arg type="s" name="button"/><arg type="b" name="pressed"/></signal>
    <signal name="Captured"><arg type="s" name="kind"/><arg type="ad" name="rect"/></signal>
  </interface>
</node>
"""
POINTER_MAX = 65535
MOUSE_BUTTONS = [ev.BTN_LEFT, ev.BTN_RIGHT, ev.BTN_MIDDLE, ev.BTN_SIDE, ev.BTN_EXTRA]
WHEELS = [ev.REL_WHEEL, ev.REL_HWHEEL, ev.REL_WHEEL_HI_RES, ev.REL_HWHEEL_HI_RES]
TICK_MS = 16
DIAGNOSTICS_INTERVAL = 0.05


def log(*args):
    print("trazo:", *args, file=sys.stderr, flush=True)


class ShellClient:
    """On-screen effects (ripples, precision overlay, radial menu) through the
    GNOME Shell extension. Does nothing when it is not active."""

    def __init__(self, bus, desktop_getter):
        self.bus = bus
        self.desktop = desktop_getter

    def _px(self, u, v):
        bx, by, bw, bh = self.desktop().bbox
        return bx + u * bw, by + v * bh

    def _call(self, method, params):
        def done(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error:
                pass
        self.bus.call(gnome.SHELL_BUS, gnome.SHELL_PATH, gnome.SHELL_IFACE, method, params,
                      None, Gio.DBusCallFlags.NO_AUTO_START, 500, None, done)

    def feedback(self, kind, uv):
        x, y = self._px(*uv)
        self._call("Feedback", GLib.Variant("(sdd)", (kind, x, y)))

    def overlay(self, visible, rect):
        if visible and rect:
            x, y = self._px(rect[0], rect[1])
            _bx, _by, bw, bh = self.desktop().bbox
            self._call("ShowOverlay", GLib.Variant("(dddd)", (x, y, rect[2] * bw,
                                                              rect[3] * bh)))
        else:
            self._call("HideOverlay", None)

    def radial(self, uv, items):
        x, y = self._px(*uv)
        self._call("ShowRadialMenu", GLib.Variant("(dds)", (x, y, json.dumps(items))))


class VirtualDevices:
    """Engine output: virtual pen, mouse, absolute pointer and keyboard, plus
    the extension's on-screen effects."""

    def __init__(self, source, launcher, shell=None, radial_items=None):
        self.launcher = launcher
        self.shell = shell
        self.radial_items = radial_items or (lambda: [])
        absinfo = {code: ev.AbsInfo(0, info.minimum, info.maximum, 0, 0, info.resolution)
                   for code, info in source.absinfo.items()
                   if code in (ev.ABS_X, ev.ABS_Y, ev.ABS_PRESSURE, ev.ABS_DISTANCE)}
        self.out_abs = absinfo
        pen_keys = [ev.BTN_TOOL_PEN, ev.BTN_TOUCH, ev.BTN_STYLUS, ev.BTN_STYLUS2, ev.BTN_STYLUS3]
        self.has_rubber = ev.BTN_TOOL_RUBBER in source.keys
        if self.has_rubber:
            pen_keys.append(ev.BTN_TOOL_RUBBER)
        # Same vendor/model as the real tablet, so GNOME and libinput treat it
        # as the same Wacom tablet (libwacom).
        self.pen_dev = ev.UInput(f"{devices.VIRTUAL_PREFIX} {source.name}",
                                 vendor=source.vendor, product=source.product,
                                 version=source.version, bustype=source.bustype,
                                 keys=pen_keys, absinfo=absinfo,
                                 props=[ev.INPUT_PROP_POINTER])
        self.mouse_dev = ev.UInput(f"{devices.VIRTUAL_PREFIX} Mouse",
                                   keys=MOUSE_BUTTONS, rels=[ev.REL_X, ev.REL_Y] + WHEELS)
        pointer_abs = {ev.ABS_X: ev.AbsInfo(maximum=POINTER_MAX),
                       ev.ABS_Y: ev.AbsInfo(maximum=POINTER_MAX)}
        self.pointer_dev = ev.UInput(f"{devices.VIRTUAL_PREFIX} Pointer",
                                     keys=MOUSE_BUTTONS, rels=WHEELS, absinfo=pointer_abs)
        self.keyboard_dev = ev.UInput(f"{devices.VIRTUAL_PREFIX} Keyboard",
                                      keys=keys.ALL_KEYS)
        self.last = PenState()
        self.last_abs = {}
        self.wheel_acc = {ev.REL_WHEEL: 0, ev.REL_HWHEEL: 0}

    def close(self):
        if self.last.prox:
            self.pen(PenState(prox=False))
        for dev in (self.pen_dev, self.mouse_dev, self.pointer_dev, self.keyboard_dev):
            dev.close()

    # --- Pen ---------------------------------------------------------------
    def _abs(self, code, fraction):
        info = self.out_abs.get(code)
        if info is None:
            return
        value = info.denormalize(fraction)
        if self.last_abs.get(code) != value:
            self.last_abs[code] = value
            self.pen_dev.emit(ev.EV_ABS, code, value)

    def _tool_code(self, tool):
        if tool == "rubber" and self.has_rubber:
            return ev.BTN_TOOL_RUBBER
        return ev.BTN_TOOL_PEN

    def pen(self, state):
        last, dev = self.last, self.pen_dev
        if not state.prox or (last.prox and last.tool != state.tool):
            if last.prox:
                if last.touch or last.buttons:
                    if last.touch:
                        dev.emit(ev.EV_KEY, ev.BTN_TOUCH, 0)
                    for code in last.buttons:
                        dev.emit(ev.EV_KEY, code, 0)
                    self._abs(ev.ABS_PRESSURE, 0.0)
                    dev.syn()
                self._abs(ev.ABS_PRESSURE, 0.0)
                dev.emit(ev.EV_KEY, self._tool_code(last.tool), 0)
                dev.syn()
            self.last = last = PenState()
            if not state.prox:
                return
        if not last.prox:
            self._abs(ev.ABS_X, state.u)
            self._abs(ev.ABS_Y, state.v)
            self._abs(ev.ABS_DISTANCE, state.distance)
            self._abs(ev.ABS_PRESSURE, 0.0)
            dev.emit(ev.EV_KEY, self._tool_code(state.tool), 1)
            dev.syn()
            last = PenState(prox=True, tool=state.tool, u=state.u, v=state.v,
                            distance=state.distance)
        self._abs(ev.ABS_X, state.u)
        self._abs(ev.ABS_Y, state.v)
        self._abs(ev.ABS_DISTANCE, state.distance)
        self._abs(ev.ABS_PRESSURE, state.pressure)
        if state.touch != last.touch:
            dev.emit(ev.EV_KEY, ev.BTN_TOUCH, int(state.touch))
        for code in sorted(state.buttons ^ last.buttons):
            dev.emit(ev.EV_KEY, code, int(code in state.buttons))
        dev.syn()
        self.last = state

    def pen_pulse(self, kind, count):
        if not self.last.prox or self.last.touch:
            return
        dev = self.pen_dev
        for _ in range(count):
            if kind == "touch":
                self._abs(ev.ABS_PRESSURE, 0.5)
                dev.emit(ev.EV_KEY, ev.BTN_TOUCH, 1)
                dev.syn()
                self._abs(ev.ABS_PRESSURE, 0.0)
                dev.emit(ev.EV_KEY, ev.BTN_TOUCH, 0)
                dev.syn()
            else:
                dev.emit(ev.EV_KEY, kind, 1)
                dev.syn()
                dev.emit(ev.EV_KEY, kind, 0)
                dev.syn()

    # --- Mouse, pointer and keyboard --------------------------------------
    def mouse_move(self, dx, dy):
        if dx:
            self.mouse_dev.emit(ev.EV_REL, ev.REL_X, dx)
        if dy:
            self.mouse_dev.emit(ev.EV_REL, ev.REL_Y, dy)
        self.mouse_dev.syn()

    def mouse_button(self, code, pressed):
        self.mouse_dev.emit(ev.EV_KEY, code, int(pressed))
        self.mouse_dev.syn()

    def mouse_pulse(self, code, count):
        for _ in range(count):
            self.mouse_button(code, True)
            self.mouse_button(code, False)

    def _move_pointer(self, at):
        self.pointer_dev.emit(ev.EV_ABS, ev.ABS_X, round(at[0] * POINTER_MAX))
        self.pointer_dev.emit(ev.EV_ABS, ev.ABS_Y, round(at[1] * POINTER_MAX))

    def pointer_move(self, at):
        self._move_pointer(at)
        self.pointer_dev.syn()

    def feedback(self, kind, uv):
        if self.shell:
            self.shell.feedback(kind, uv)

    def overlay(self, visible, rect):
        if self.shell:
            self.shell.overlay(visible, rect)

    def radial(self, uv):
        if self.shell:
            self.shell.radial(uv, self.radial_items())

    def pointer_button(self, code, pressed, at):
        if at:
            self._move_pointer(at)
            self.pointer_dev.syn()
        self.pointer_dev.emit(ev.EV_KEY, code, int(pressed))
        self.pointer_dev.syn()

    def scroll(self, vertical, horizontal, at=None):
        dev = self.pointer_dev if at else self.mouse_dev
        if at:
            self._move_pointer(at)
        for hi_res, legacy, amount in ((ev.REL_WHEEL_HI_RES, ev.REL_WHEEL, vertical),
                                       (ev.REL_HWHEEL_HI_RES, ev.REL_HWHEEL, horizontal)):
            if not amount:
                continue
            dev.emit(ev.EV_REL, hi_res, amount)
            self.wheel_acc[legacy] += amount
            notches = int(self.wheel_acc[legacy] / 120)
            if notches:
                self.wheel_acc[legacy] -= notches * 120
                dev.emit(ev.EV_REL, legacy, notches)
        dev.syn()

    def keys(self, codes, pressed):
        for code in codes:
            self.keyboard_dev.emit(ev.EV_KEY, code, int(pressed))
            self.keyboard_dev.syn()

    def launch(self, action):
        self.launcher(action)


class Daemon:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.config = cfg.load()
        self.config_mtime = self._config_mtime()
        self.desktop = gnome.read_desktop(self.bus)
        self.focus_app = gnome.focused_app(self.bus)
        self.device = None
        self.virtual = None
        self.engine = None
        self.watch_id = None
        self.usb_id = None
        self.error = ""
        self.took_over = set()
        self.registration = None
        self.shell = ShellClient(self.bus, lambda: self.desktop)
        self.gnome_watch = None
        self.tick_id = None
        self.diagnostics_owner = None
        self.diagnostics_watch = None
        self.last_diagnostics = 0.0
        self.shell_present = False
        self.frames = 0
        self.rate = 0
        self.rate_since = time.monotonic()

    # --- Lifecycle ---------------------------------------------------------
    def run(self):
        Gio.bus_own_name_on_connection(self.bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                       None, self._name_lost)
        node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        self.registration = self.bus.register_object(OBJECT_PATH, node.interfaces[0],
                                                     self._method_call, None, None)
        gnome.watch_monitors(self._monitors_changed, self.bus)
        gnome.watch_focus(self._focus_changed, self.bus)
        Gio.bus_watch_name_on_connection(self.bus, gnome.SHELL_BUS,
                                         Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self._shell_changed(True),
                                         lambda *_: self._shell_changed(False))
        self.bus.signal_subscribe(gnome.SHELL_BUS, gnome.SHELL_IFACE, "RadialActivated",
                                  gnome.SHELL_PATH, None, Gio.DBusSignalFlags.NONE,
                                  self._radial_activated)
        self.bus.signal_subscribe(gnome.SHELL_BUS, gnome.SHELL_IFACE, "MouseActive",
                                  gnome.SHELL_PATH, None, Gio.DBusSignalFlags.NONE,
                                  self._mouse_active)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._quit)
        self._connect()
        GLib.timeout_add_seconds(2, self._tick)
        log("service started")
        self.loop.run()
        self._disconnect()
        for usb_id in self.took_over:
            gnome.restore(usb_id)
        log("service stopped")

    def _quit(self):
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _name_lost(self, *_args):
        log("another trazo-daemon is already running")
        self.loop.quit()

    def _tick(self):
        mtime = self._config_mtime()
        if mtime != self.config_mtime:
            self.config_mtime = mtime
            self.reload()
        elif self.device is None and self.config.get("enabled", True):
            self._connect()
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _config_mtime():
        try:
            return os.stat(cfg.CONFIG_PATH).st_mtime_ns
        except OSError:
            return None

    def reload(self):
        self.config = cfg.load()
        self.config_mtime = self._config_mtime()
        if not self.config.get("enabled", True):
            self._disconnect()
            for usb_id in list(self.took_over):
                gnome.restore(usb_id)
                self.took_over.discard(usb_id)
            self._set_error("Trazo is turned off")
            return
        if self.engine:
            self.engine.reset_overrides()
            self._apply_profile()
        else:
            self._connect()
        self._emit_status()

    # --- Tablet ------------------------------------------------------------
    def _set_error(self, message):
        if message != self.error:
            self.error = message
            if message:
                log(message)
            self._emit_status()

    def _connect(self):
        if self.device is not None or not self.config.get("enabled", True):
            return
        pens = devices.find_pens()
        if not pens:
            self._set_error("No Wacom tablet is connected")
            return
        pen = pens[0]
        if not pen.readable or not devices.uinput_writable():
            self._set_error("Missing permissions: run install.sh once")
            return
        try:
            device = ev.InputDevice(pen.path)
            virtual = VirtualDevices(device, self.launch, self.shell, self._radial_items)
        except OSError as error:
            self._set_error(f"Could not open the tablet: {error}")
            return
        self.device, self.virtual, self.usb_id = device, virtual, pen.usb_id
        self.engine = Engine(virtual, device.absinfo)
        self.engine.on_button = self._on_button
        self.engine.on_capture = self._on_capture
        self.engine.hover_pointer = self.shell_present
        self.engine.set_desktop(self.desktop)
        self._apply_profile()
        try:
            gnome.take_over(self.usb_id)
            self.took_over.add(self.usb_id)
            self.gnome_watch = gnome.watch(self.usb_id, self._gnome_changed)
        except (GLib.Error, OSError) as error:
            log("could not adjust the GNOME settings:", error)
        try:
            device.grab()
        except OSError as error:
            self._disconnect()
            self._set_error(f"Another application controls the tablet: {error}")
            return
        self.watch_id = GLib.io_add_watch(
            device.fd, GLib.PRIORITY_HIGH,
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._on_input)
        self.error = ""
        log(f"connected to {device.name} ({pen.path})")
        self._emit_status()

    def _disconnect(self):
        gnome.unwatch(self.gnome_watch)
        self.gnome_watch = None
        if self.tick_id is not None:
            GLib.source_remove(self.tick_id)
            self.tick_id = None
        if self.watch_id is not None:
            GLib.source_remove(self.watch_id)
            self.watch_id = None
        if self.engine:
            self.engine.device_lost()
        if self.virtual:
            self.virtual.close()
        if self.device:
            self.device.close()
        self.device = self.virtual = self.engine = None

    def _on_input(self, _fd, condition):
        try:
            if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
                raise OSError("device disconnected")
            events = self.device.read()
        except OSError:
            self.watch_id = None
            self._disconnect()
            self._set_error("The tablet was disconnected")
            return GLib.SOURCE_REMOVE
        self.engine.handle(events)
        self._schedule_tick()
        self.frames += sum(1 for t, c, _v in events if t == ev.EV_SYN and c == ev.SYN_REPORT)
        if self.diagnostics_owner:
            self._emit_diagnostics()
        return GLib.SOURCE_CONTINUE

    # --- Timing (momentum and press-and-hold) ------------------------------
    def _schedule_tick(self):
        if self.tick_id is None and self.engine and self.engine.needs_tick:
            self.tick_id = GLib.timeout_add(TICK_MS, self._on_tick)

    def _on_tick(self):
        if self.engine:
            self.engine.tick()
            if self.engine.needs_tick:
                return GLib.SOURCE_CONTINUE
        self.tick_id = None
        return GLib.SOURCE_REMOVE

    def _shell_changed(self, present):
        self.shell_present = present
        if self.engine:
            self.engine.hover_pointer = present
        log("GNOME extension " + ("active" if present else "not available"))
        self._emit_status()

    # --- GNOME settings ----------------------------------------------------
    def _gnome_changed(self):
        # Something changed the tablet settings in GNOME: Trazo is in charge.
        GLib.idle_add(self._reapply_gnome)

    def _reapply_gnome(self):
        if self.usb_id and self.usb_id in self.took_over:
            gnome.take_over(self.usb_id)
        return GLib.SOURCE_REMOVE

    def _mouse_active(self, *_args):
        # The real mouse was used: it takes the cursor back from the pen.
        if self.engine:
            self.engine.mouse_active()

    # --- Radial menu -------------------------------------------------------
    def _radial_items(self):
        return [{"label": cfg.action_label(a).rstrip("…"),
                 "icon": cfg.ACTION_ICONS.get(a.get("type"), "input-mouse-symbolic")}
                for a in self.config["radial_menu"]]

    def _radial_activated(self, _bus, _sender, _path, _iface, _signal, params):
        index = params.unpack()[0]
        actions = self.config["radial_menu"]
        if self.engine and 0 <= index < len(actions):
            action = actions[index]
            # Let the menu close and focus return to the application.
            GLib.timeout_add(80, lambda: (self.engine and self.engine.trigger(action),
                                          GLib.SOURCE_REMOVE)[1])

    # --- Diagnostics -------------------------------------------------------
    def _emit_diagnostics(self, force=False):
        now = time.monotonic()
        if now - self.rate_since >= 1.0:
            self.rate = round(self.frames / (now - self.rate_since))
            self.frames = 0
            self.rate_since = now
        if not force and now - self.last_diagnostics < DIAGNOSTICS_INTERVAL:
            return
        self.last_diagnostics = now
        data = self.engine.diagnostics() if self.engine else {}
        data.update(rate=self.rate, profile=self.engine.profile_id if self.engine else "",
                    app=self.focus_app)
        self._emit("Diagnostics", GLib.Variant("(s)", (json.dumps(data),)))

    def _set_diagnostics(self, sender, enabled):
        if self.diagnostics_watch is not None:
            Gio.bus_unwatch_name(self.diagnostics_watch)
            self.diagnostics_watch = None
        self.diagnostics_owner = sender if enabled else None
        if enabled:
            # If the UI closes without telling us, stop sending.
            self.diagnostics_watch = Gio.bus_watch_name_on_connection(
                self.bus, sender, Gio.BusNameWatcherFlags.NONE, None,
                lambda *_: self._set_diagnostics(None, False))
            self._emit_diagnostics(force=True)

    def _apply_profile(self):
        if not self.engine:
            return
        profile_id, profile = cfg.profile_for(self.config, self.focus_app)
        self.engine.set_profile(profile, self.config["advanced"], gnome.double_click_ms(),
                                profile_id)

    def _monitors_changed(self):
        self.desktop = gnome.read_desktop(self.bus)
        if self.engine:
            self.engine.set_desktop(self.desktop)
        self._emit_status()

    def _focus_changed(self, app_id):
        self.focus_app = app_id
        if self.engine:
            previous = self.engine.profile_id
            self._apply_profile()
            if self.engine.profile_id != previous:
                log(f"profile: {self.engine.profile_id} ({app_id})")
        self._emit_status()

    def launch(self, action):
        try:
            app_id = action.get("app_id")
            if app_id:
                info = Gio.DesktopAppInfo.new(app_id)
                if info:
                    info.launch([], None)
                    return
            command = action.get("command")
            if command:
                Gio.Subprocess.new(shlex.split(command), Gio.SubprocessFlags.NONE)
        except (GLib.Error, ValueError) as error:
            log("could not open:", error)

    # --- D-Bus -------------------------------------------------------------
    def status(self):
        engine = self.engine
        return {
            "connected": engine is not None,
            "device": self.device.name if self.device else "",
            "usb_id": self.usb_id or "",
            "error": self.error,
            "app": self.focus_app,
            "profile": engine.profile_id if engine else "",
            "mode": engine.mode if engine else "",
            "tablet_mm": list(engine.size_mm) if engine else [],
            "focus_extension": bool(self.bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (gnome.SHELL_BUS,)), None,
                Gio.DBusCallFlags.NONE, 500, None).unpack()[0]),
        }

    def _emit_status(self):
        self._emit("StatusChanged", GLib.Variant("(s)", (json.dumps(self.status()),)))

    def _emit(self, name, params):
        try:
            self.bus.emit_signal(None, OBJECT_PATH, BUS_NAME, name, params)
        except GLib.Error:
            pass

    def _on_button(self, name, pressed):
        self._emit("Button", GLib.Variant("(sb)", (name, pressed)))

    def _on_capture(self, kind, rect):
        self._emit("Captured", GLib.Variant("(sad)", (kind, [float(v) for v in rect])))

    def _method_call(self, _conn, sender, _path, _iface, method, params, invocation):
        if method == "SetDiagnostics":
            self._set_diagnostics(sender, params.unpack()[0])
            invocation.return_value(None)
        elif method == "Reload":
            self.reload()
            invocation.return_value(None)
        elif method == "GetStatus":
            invocation.return_value(GLib.Variant("(s)", (json.dumps(self.status()),)))
        elif method == "StartCapture":
            kind = params.unpack()[0]
            if self.engine and kind in ("screen", "tablet"):
                self.engine.start_capture(kind)
            invocation.return_value(None)
        elif method == "CancelCapture":
            if self.engine:
                self.engine.cancel_capture()
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)


def main():
    Daemon().run()


if __name__ == "__main__":
    main()
