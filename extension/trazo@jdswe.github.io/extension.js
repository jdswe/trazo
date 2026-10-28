// Trazo for GNOME Shell: tells Trazo which application is focused, keeps a
// single visible cursor (the one of the device in use, like on Windows and
// macOS) and draws the on-screen effects: tap ripples, the precision mode
// overlay and the radial menu.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {CursorOwnership} from './ownership.js';

const BUS_NAME = 'io.github.jdswe.TrazoShell';
const OBJECT_PATH = '/io/github/jdswe/TrazoShell';
const INTERFACE = `
<node>
  <interface name="io.github.jdswe.TrazoShell">
    <method name="GetFocus">
      <arg type="s" direction="out" name="app_id"/>
    </method>
    <method name="Feedback">
      <arg type="s" direction="in" name="kind"/>
      <arg type="d" direction="in" name="x"/>
      <arg type="d" direction="in" name="y"/>
    </method>
    <method name="ShowOverlay">
      <arg type="d" direction="in" name="x"/>
      <arg type="d" direction="in" name="y"/>
      <arg type="d" direction="in" name="width"/>
      <arg type="d" direction="in" name="height"/>
    </method>
    <method name="HideOverlay"/>
    <method name="ShowRadialMenu">
      <arg type="d" direction="in" name="x"/>
      <arg type="d" direction="in" name="y"/>
      <arg type="s" direction="in" name="items"/>
    </method>
    <signal name="FocusChanged">
      <arg type="s" name="app_id"/>
    </signal>
    <signal name="RadialActivated">
      <arg type="i" name="index"/>
    </signal>
    <signal name="MouseActive"/>
  </interface>
</node>`;

const PEN_DEVICES = [
    Clutter.InputDeviceType.TABLET_DEVICE,
    Clutter.InputDeviceType.PEN_DEVICE,
    Clutter.InputDeviceType.ERASER_DEVICE,
];
const MOUSE_DEVICES = [
    Clutter.InputDeviceType.POINTER_DEVICE,
    Clutter.InputDeviceType.TOUCHPAD_DEVICE,
];
const MOUSE_EVENTS = [
    Clutter.EventType.MOTION,
    Clutter.EventType.BUTTON_PRESS,
    Clutter.EventType.SCROLL,
];
const RADIAL_RADIUS = 104;
const RADIAL_ITEM = 76;

function verticalBox() {
    const box = new St.BoxLayout({x_align: Clutter.ActorAlign.CENTER});
    if ('orientation' in box)
        box.orientation = Clutter.Orientation.VERTICAL;
    else
        box.vertical = true;
    return box;
}

export default class TrazoShellExtension extends Extension {
    enable() {
        this._windowTracker = Shell.WindowTracker.get_default();
        this._current = this._focusedApp();
        this._ownership = new CursorOwnership();
        this._mouseHidden = false;
        this._hold = null;
        this._overlay = null;
        this._radial = null;
        this._grab = null;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
        this._focusId = global.display.connect('notify::focus-window',
            () => this._updateFocus());
        this._filterId = Clutter.Event.add_filter(global.stage,
            event => this._onEvent(event));
    }

    disable() {
        Clutter.Event.remove_filter(this._filterId);
        this._setMouseHidden(false);
        this._closeRadial(-1, false);
        this.HideOverlay();
        this._stopHold();
        global.display.disconnect(this._focusId);
        Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
        this._dbus = null;
        this._windowTracker = null;
    }

    // --- Focused application ------------------------------------------------
    GetFocus() {
        return this._current;
    }

    _focusedApp() {
        const window = global.display.focus_window;
        if (!window)
            return '';
        const app = this._windowTracker.get_window_app(window);
        return app?.get_id() ?? window.get_wm_class() ?? '';
    }

    _updateFocus() {
        const appId = this._focusedApp();
        if (appId === this._current)
            return;
        this._current = appId;
        this._dbus?.emit_signal('FocusChanged', new GLib.Variant('(s)', [appId]));
    }

    // --- Single cursor -------------------------------------------------------
    // GNOME draws one cursor for the pen and another for the mouse. The last
    // device used on purpose owns the cursor (see ownership.js): the mouse
    // cursor hides while the pen is in use, and a real mouse (not Trazo's
    // virtual pointer) always takes it back, even with the pen lying on the
    // tablet. Trazo is told so it stops moving the pointer for the pen.
    _onEvent(event) {
        try {
            const device = event.get_source_device?.() ?? event.get_device();
            const type = event.type();
            const deviceType = device?.get_device_type();
            if (PEN_DEVICES.includes(deviceType)) {
                const [x, y] = event.get_coords();
                this._ownership.penEvent({
                    leaving: type === Clutter.EventType.PROXIMITY_OUT,
                    x, y,
                    pressed: type === Clutter.EventType.BUTTON_PRESS,
                });
            } else if (MOUSE_DEVICES.includes(deviceType) && MOUSE_EVENTS.includes(type) &&
                       !(device.get_device_name() ?? '').startsWith('Trazo')) {
                if (this._ownership.mouseEvent(GLib.get_monotonic_time() / 1000))
                    this._dbus?.emit_signal('MouseActive', null);
            }
            this._setMouseHidden(this._ownership.hideMouseCursor);
        } catch (e) {
            logError(e, 'Trazo');
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _setMouseHidden(hidden) {
        if (hidden === this._mouseHidden)
            return;
        this._mouseHidden = hidden;
        const tracker = global.backend.get_cursor_tracker();
        if (tracker.inhibit_cursor_visibility) {
            if (hidden)
                tracker.inhibit_cursor_visibility();
            else
                tracker.uninhibit_cursor_visibility();
        } else {
            tracker.set_pointer_visible(!hidden);
        }
    }

    // --- Tap ripples ---------------------------------------------------------
    Feedback(kind, x, y) {
        if (kind === 'hold-start') {
            this._startHold(x, y);
            return;
        }
        this._stopHold();
        if (kind === 'hold-cancel')
            return;
        this._ripple(x, y, kind);
        if (kind === 'double') {
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 110, () => {
                this._ripple(x, y, 'tap');
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    _ripple(x, y, kind) {
        const size = kind === 'right' ? 46 : 38;
        const ripple = new St.Widget({
            style_class: `trazo-ripple trazo-ripple-${kind}`,
            width: size, height: size,
            x: Math.round(x - size / 2), y: Math.round(y - size / 2),
            reactive: false,
        });
        ripple.set_pivot_point(0.5, 0.5);
        ripple.scale_x = ripple.scale_y = 0.35;
        Main.layoutManager.uiGroup.add_child(ripple);
        ripple.ease({
            scale_x: 1.6, scale_y: 1.6, opacity: 0,
            duration: 440,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => ripple.destroy(),
        });
    }

    _startHold(x, y) {
        this._stopHold();
        const size = 56;
        const ring = new St.Widget({
            style_class: 'trazo-hold',
            width: size, height: size,
            x: Math.round(x - size / 2), y: Math.round(y - size / 2),
            reactive: false, opacity: 0,
        });
        ring.set_pivot_point(0.5, 0.5);
        ring.scale_x = ring.scale_y = 1.6;
        Main.layoutManager.uiGroup.add_child(ring);
        // Fades in slowly: a quick tap never shows it.
        ring.ease({
            scale_x: 1, scale_y: 1, opacity: 255,
            delay: 150, duration: 450,
            mode: Clutter.AnimationMode.LINEAR,
        });
        this._hold = ring;
    }

    _stopHold() {
        if (!this._hold)
            return;
        this._hold.remove_all_transitions();
        this._hold.destroy();
        this._hold = null;
    }

    // --- Precision mode overlay ---------------------------------------------
    ShowOverlay(x, y, width, height) {
        if (!this._overlay) {
            this._overlay = new St.Widget({style_class: 'trazo-precision', reactive: false,
                opacity: 0});
            Main.layoutManager.uiGroup.add_child(this._overlay);
        }
        this._overlay.set_position(Math.round(x), Math.round(y));
        this._overlay.set_size(Math.round(width), Math.round(height));
        this._overlay.ease({opacity: 255, duration: 120});
    }

    HideOverlay() {
        if (!this._overlay)
            return;
        const overlay = this._overlay;
        this._overlay = null;
        overlay.ease({opacity: 0, duration: 120, onComplete: () => overlay.destroy()});
    }

    // --- Radial menu ---------------------------------------------------------
    ShowRadialMenu(x, y, itemsJson) {
        this._closeRadial(-1, false);
        let items = [];
        try {
            items = JSON.parse(itemsJson);
        } catch (e) {
            return;
        }
        if (!items.length)
            return;

        const monitor = Main.layoutManager.monitors.find(m =>
            x >= m.x && x < m.x + m.width && y >= m.y && y < m.y + m.height) ??
            Main.layoutManager.primaryMonitor;
        const disc = 2 * RADIAL_RADIUS + RADIAL_ITEM + 20;
        const half = disc / 2;
        const cx = Math.round(Math.min(Math.max(x, monitor.x + half),
            monitor.x + monitor.width - half));
        const cy = Math.round(Math.min(Math.max(y, monitor.y + half),
            monitor.y + monitor.height - half));

        const backdrop = new St.Widget({
            reactive: true, x: 0, y: 0,
            width: global.stage.width, height: global.stage.height,
        });
        backdrop.connect('button-press-event', () => {
            this._closeRadial(-1, true);
            return Clutter.EVENT_STOP;
        });
        backdrop.connect('key-press-event', (_actor, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape)
                this._closeRadial(-1, true);
            return Clutter.EVENT_STOP;
        });

        const glass = new St.Widget({
            style_class: 'trazo-radial-disc', reactive: true,
            width: disc, height: disc, x: cx - half, y: cy - half,
        });
        glass.connect('button-press-event', () => Clutter.EVENT_STOP);
        try {
            glass.add_effect(new Shell.BlurEffect({
                radius: 30, brightness: 0.9, mode: Shell.BlurMode.BACKGROUND,
            }));
        } catch (e) {
            // No blur available: the translucent background remains.
        }
        glass.set_pivot_point(0.5, 0.5);
        glass.scale_x = glass.scale_y = 0.85;
        backdrop.add_child(glass);

        items.forEach((item, index) => {
            const angle = -Math.PI / 2 + index * 2 * Math.PI / items.length;
            const content = verticalBox();
            content.add_child(new St.Icon({
                icon_name: item.icon, icon_size: 24, style_class: 'trazo-radial-icon',
            }));
            content.add_child(new St.Label({
                text: item.label, style_class: 'trazo-radial-label',
                x_align: Clutter.ActorAlign.CENTER,
            }));
            const button = new St.Button({
                style_class: 'trazo-radial-item', child: content,
                can_focus: true, reactive: true,
                width: RADIAL_ITEM, height: RADIAL_ITEM,
                x: Math.round(cx + RADIAL_RADIUS * Math.cos(angle) - RADIAL_ITEM / 2),
                y: Math.round(cy + RADIAL_RADIUS * Math.sin(angle) - RADIAL_ITEM / 2),
            });
            button.connect('clicked', () => this._closeRadial(index, true));
            backdrop.add_child(button);
        });

        const close = new St.Button({
            style_class: 'trazo-radial-center',
            child: new St.Icon({icon_name: 'window-close-symbolic', icon_size: 20}),
            width: 48, height: 48, x: cx - 24, y: cy - 24,
        });
        close.connect('clicked', () => this._closeRadial(-1, true));
        backdrop.add_child(close);

        Main.layoutManager.uiGroup.add_child(backdrop);
        this._radial = backdrop;
        this._grab = Main.pushModal(backdrop, {actionMode: Shell.ActionMode.POPUP});
        backdrop.opacity = 0;
        backdrop.ease({opacity: 255, duration: 140});
        glass.ease({
            scale_x: 1, scale_y: 1, duration: 200,
            mode: Clutter.AnimationMode.EASE_OUT_BACK,
        });
    }

    _closeRadial(index, notify) {
        if (!this._radial)
            return;
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        const radial = this._radial;
        this._radial = null;
        radial.ease({opacity: 0, duration: 110, onComplete: () => radial.destroy()});
        if (notify)
            this._dbus?.emit_signal('RadialActivated', new GLib.Variant('(i)', [index]));
    }
}
