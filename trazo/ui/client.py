"""D-Bus client for the trazo-daemon service."""

import json

from gi.repository import Gio, GLib, GObject

from ..daemon import BUS_NAME, OBJECT_PATH

SERVICE = "trazo-daemon.service"


class DaemonClient(GObject.Object):
    __gsignals__ = {
        "status": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "button": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        "captured": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        "diagnostics": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.running = False
        self.known = False  # True once we know whether the service is running
        self.status = {}
        for name, handler in (("StatusChanged", self._on_status),
                              ("Button", self._on_button),
                              ("Captured", self._on_captured),
                              ("Diagnostics", self._on_diagnostics)):
            self.bus.signal_subscribe(BUS_NAME, BUS_NAME, name, OBJECT_PATH, None,
                                      Gio.DBusSignalFlags.NONE, handler)
        Gio.bus_watch_name_on_connection(self.bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
                                         self._appeared, self._vanished)

    def _appeared(self, *_args):
        self.known = True
        self.running = True
        self.refresh()

    def _vanished(self, *_args):
        self.known = True
        self.running = False
        self.known = False  # True once we know whether the service is running
        self.status = {}
        self.emit("status", self.status)

    def _call(self, method, params=None, callback=None):
        def done(bus, result):
            try:
                reply = bus.call_finish(result)
            except GLib.Error:
                return
            if callback:
                callback(reply)
        self.bus.call(BUS_NAME, OBJECT_PATH, BUS_NAME, method, params, None,
                      Gio.DBusCallFlags.NONE, 3000, None, done)

    def refresh(self):
        self._call("GetStatus", callback=lambda reply: self._set_status(reply.unpack()[0]))

    def reload(self):
        self._call("Reload")

    def start_capture(self, kind):
        self._call("StartCapture", GLib.Variant("(s)", (kind,)))

    def cancel_capture(self):
        self._call("CancelCapture")

    def set_diagnostics(self, enabled):
        self._call("SetDiagnostics", GLib.Variant("(b)", (enabled,)))

    def _set_status(self, text):
        try:
            self.status = json.loads(text)
        except ValueError:
            self.status = {}
        self.emit("status", self.status)

    def _on_status(self, _bus, _sender, _path, _iface, _signal, params):
        self.running = True
        self._set_status(params.unpack()[0])

    def _on_button(self, _bus, _sender, _path, _iface, _signal, params):
        name, pressed = params.unpack()
        self.emit("button", name, pressed)

    def _on_diagnostics(self, _bus, _sender, _path, _iface, _signal, params):
        try:
            self.emit("diagnostics", json.loads(params.unpack()[0]))
        except ValueError:
            pass

    def _on_captured(self, _bus, _sender, _path, _iface, _signal, params):
        kind, rect = params.unpack()
        self.emit("captured", kind, list(rect))


def systemctl(*args, on_done=None):
    """Runs systemctl --user asynchronously."""
    try:
        process = Gio.Subprocess.new(["systemctl", "--user", *args],
                                     Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error:
        return

    def finished(proc, result):
        try:
            proc.wait_finish(result)
        except GLib.Error:
            pass
        if on_done:
            on_done(proc.get_successful())
    process.wait_async(None, finished)
