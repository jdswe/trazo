"""Window pages, in the style of macOS System Settings: Pen, Mapping, Radial
Menu, Applications, Diagnostics and General."""

import copy
import json

from gi.repository import Adw, Gio, GLib, Gtk

from .. import config as cfg
from ..curve import curve_from_softness, softness_from_curve
from .. import gnome
from ..gnome import EXTENSION_UUID
from ..mapping import Rect
from . import client
from .dialogs import ActionPickerDialog, AppChooserDialog, KeyCaptureDialog, ModifierDialog
from .widgets import (ActionCard, CurveEditor, PenHero, PressureTestArea, RadialPreview,
                      ScreenPreview, TabletPreview)

PARAM_ACTIONS = {"keystroke", "modifier", "run"}
DEFAULT_TABLET_MM = (152.0, 95.0)


def scale_row(add, title, subtitle, low, high, step, on_changed, fmt, marks=None):
    """Row with a slider. `add` is group.add or expander.add_row."""
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
    scale.set_hexpand(True)
    scale.set_size_request(240, -1)
    scale.set_valign(Gtk.Align.CENTER)
    scale.set_draw_value(True)
    scale.set_value_pos(Gtk.PositionType.RIGHT)
    scale.set_format_value_func(lambda _s, v: fmt(v))
    for value, label in (marks or []):
        scale.add_mark(value, Gtk.PositionType.BOTTOM, label)
    scale.connect("value-changed", lambda s: on_changed(s.get_value()))
    scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
    row.add_suffix(scale)
    add(row)
    return row, scale


def switch_row(group, title, subtitle, on_changed):
    row = Adw.SwitchRow(title=title, subtitle=subtitle)
    row.connect("notify::active", lambda r, _p: on_changed(r.get_active()))
    group.add(row)
    return row


def percent(value):
    return f"{round(value * 100)}%"


def tile(icon_name, color, big=False):
    """White icon on a rounded colored square, like macOS."""
    image = Gtk.Image.new_from_icon_name(icon_name)
    image.add_css_class("mac-tile")
    image.add_css_class(f"tile-{color}")
    if big:
        image.add_css_class("big")
    image.set_valign(Gtk.Align.CENTER)
    image.set_halign(Gtk.Align.CENTER)
    return image


def hero_box(*children, spacing=12):
    box = Gtk.Box(spacing=spacing)
    box.add_css_class("hero")
    for child in children:
        box.append(child)
    return box


class Page(Adw.PreferencesPage):
    ICON = "emblem-system-symbolic"
    COLOR = "gray"
    DESCRIPTION = ""

    def __init__(self, window, **kwargs):
        super().__init__(**kwargs)
        self.window = window
        self.loading = False
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         halign=Gtk.Align.CENTER, margin_bottom=6)
        header.add_css_class("page-header")
        header.append(tile(self.ICON, self.COLOR, big=True))
        title = Gtk.Label(label=self.get_title())
        title.add_css_class("title-1")
        header.append(title)
        if self.DESCRIPTION:
            description = Gtk.Label(label=self.DESCRIPTION, wrap=True, max_width_chars=60,
                                    justify=Gtk.Justification.CENTER)
            description.add_css_class("dim-label")
            header.append(description)
        group = Adw.PreferencesGroup()
        group.add(header)
        self.add(group)

    @property
    def profile(self):
        return self.window.profile

    def changed(self):
        if not self.loading:
            self.window.changed()

    def load(self):
        self.loading = True
        try:
            self.populate()
        finally:
            self.loading = False

    def populate(self):
        pass


BUTTONS = (("upper", "Upper Button"), ("lower", "Lower Button"), ("tip", "Tip"))


def configure_action(window, kind, current, done):
    """Opens the dialog for an action's parameters (shortcut, app…)."""
    if kind == "keystroke":
        dialog = KeyCaptureDialog(done, current)
    elif kind == "modifier":
        dialog = ModifierDialog(done, current)
    else:
        dialog = AppChooserDialog("Open Application or Command", done, allow_command=True)
    dialog.present(window)


class PenPage(Page):
    ICON, COLOR = "input-tablet-symbolic", "purple"
    DESCRIPTION = "Adjust how the tip feels, what the pen buttons do and how strokes respond."

    def __init__(self, window):
        super().__init__(window, title="Pen", icon_name="input-tablet-symbolic", name="pen")
        hero_group = Adw.PreferencesGroup(
            title="Pen Buttons",
            description="Click a card to change its action. Press a pen button and it "
                        "lights up here.")
        self.pen = PenHero()
        cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, homogeneous=True,
                        hexpand=True, valign=Gtk.Align.FILL)
        self.cards = {}
        for key, caption in BUTTONS:
            card = ActionCard(caption, lambda _b, k=key: self._pick(k))
            cards.append(card)
            self.cards[key] = card
        hero_group.add(hero_box(self.pen, cards))
        self.add(hero_group)

        mode_group = Adw.PreferencesGroup()
        self.button_mode = Adw.ComboRow(
            title="Side Button Mode",
            subtitle="Hover click: the click happens when you press the button. Click and tap: "
                     "it happens when the tip touches the tablet while you hold the button.",
            model=Gtk.StringList.new(["Hover Click", "Click & Tap"]))
        self.button_mode.connect("notify::selected", self._button_mode_changed)
        mode_group.add(self.button_mode)
        self.add(mode_group)

        tip = Adw.PreferencesGroup(
            title="Tip Feel",
            description="Drag the points on the curve and try the result by drawing on "
                        "the right.")
        header = Gtk.Box(spacing=6)
        clear = Gtk.Button(label="Clear Drawing", valign=Gtk.Align.CENTER)
        clear.add_css_class("flat")
        header.append(clear)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                           tooltip_text="Reset Tip Feel")
        reset.add_css_class("flat")
        reset.connect("clicked", self._reset_tip)
        header.append(reset)
        tip.set_header_suffix(header)
        self.curve = CurveEditor(self._curve_changed)
        self.test = PressureTestArea(self.curve.set_level)
        clear.connect("clicked", lambda *_: self.test.clear())
        curve_box = hero_box(self.curve, self.test)
        curve_box.set_homogeneous(True)
        tip.add(curve_box)
        self.add(tip)

        feel = Adw.PreferencesGroup()
        _, self.softness = scale_row(
            feel.add, "Feel", "From soft (less force) to firm (more force)", -1, 1, 0.05,
            self._softness_changed,
            lambda v: "Linear" if abs(v) < 0.025 else ("Soft" if v < 0 else "Firm")
            + f" {round(abs(v) * 100)}%",
            marks=[(0, None)])
        custom = Adw.ExpanderRow(title="Customize",
                                 subtitle="Click threshold, maximum pressure and tip-up assist")
        _, self.threshold = scale_row(custom.add_row, "Click Threshold",
                                      "Minimum force for the tip to click",
                                      0, 0.3, 0.005, self._threshold_changed, percent)
        _, self.maximum = scale_row(custom.add_row, "Maximum Pressure",
                                    "Force that already reaches 100%",
                                    0.3, 1.0, 0.01, self._maximum_changed, percent)
        self.tip_up = Adw.SwitchRow(
            title="Tip-Up Assist",
            subtitle="Keeps strokes from breaking when pressure drops for a moment")
        self.tip_up.connect("notify::active", self._tip_up_changed)
        custom.add_row(self.tip_up)
        feel.add(custom)
        self.add(feel)

        response = Adw.PreferencesGroup(title="Response")
        _, self.smoothing = scale_row(
            response.add, "Smoothing",
            "Removes cursor and stroke jitter without adding lag when you move fast",
            0, 1, 0.05, self._smoothing_changed,
            lambda v: "Off" if v < 0.02 else ("Low" if v < 0.35 else
                                              "Medium" if v < 0.7 else "High"))
        _, self.dcd = scale_row(
            response.add, "Double-Click Distance",
            "When the second tap lands this close to the first, both count at the same spot",
            0, 8, 0.5, self._dcd_changed,
            lambda v: "Off" if v < 0.25 else f"{v:.1f} mm")
        self.add(response)

        window.client.connect("button", self._button_event)

    def populate(self):
        tip = self.profile["tip"]
        self.curve.set_values(tip["threshold"], tip["maximum"], tip["curve"])
        self.softness.set_value(softness_from_curve(tip["curve"]))
        self.threshold.set_value(tip["threshold"])
        self.maximum.set_value(tip["maximum"])
        self.tip_up.set_active(tip.get("tip_up_assist", False))
        for key, card in self.cards.items():
            card.set_action(self.profile["buttons"].get(key) or {"type": "default"})
        self.button_mode.set_selected(1 if self.profile["button_mode"] == "click_tap" else 0)
        self.smoothing.set_value(self.profile["smoothing"])
        self.dcd.set_value(self.profile["double_click_distance"])

    # --- Buttons -----------------------------------------------------------
    def _pick(self, key):
        caption = dict(BUTTONS)[key]
        current = self.profile["buttons"].get(key) or {"type": "default"}
        exclude = () if key == "tip" else cfg.TIP_ONLY_ACTIONS

        def picked(kind):
            if kind in PARAM_ACTIONS:
                configure_action(self.window, kind,
                                 current if current.get("type") == kind else None,
                                 lambda result: self._set_action(key, {"type": kind,
                                                                       **result}))
            else:
                self._set_action(key, {"type": kind})

        ActionPickerDialog(f"Action: {caption}", current.get("type"), picked,
                           exclude).present(self.window)

    def _set_action(self, key, action):
        self.profile["buttons"][key] = action
        self.load()
        self.changed()

    def _button_event(self, _client, name, pressed):
        self.pen.set_pressed(name, pressed)
        if name in self.cards:
            self.cards[name].set_pressed(pressed)

    # --- Tip -----------------------------------------------------------------
    def _sync_tip(self):
        self.load()
        self.changed()

    def _curve_changed(self, threshold, maximum, curve, final):
        self.profile["tip"].update(threshold=threshold, maximum=maximum, curve=curve)
        if final:
            self._sync_tip()

    def _softness_changed(self, value):
        if self.loading:
            return
        self.profile["tip"]["curve"] = curve_from_softness(value)
        self._sync_tip()

    def _threshold_changed(self, value):
        if self.loading:
            return
        tip = self.profile["tip"]
        tip["threshold"] = value
        tip["maximum"] = max(tip["maximum"], value + 0.05)
        self._sync_tip()

    def _maximum_changed(self, value):
        if self.loading:
            return
        tip = self.profile["tip"]
        tip["maximum"] = max(value, tip["threshold"] + 0.05)
        self._sync_tip()

    def _tip_up_changed(self, row, _pspec):
        if not self.loading:
            self.profile["tip"]["tip_up_assist"] = row.get_active()
            self.changed()

    def _reset_tip(self, *_args):
        self.profile["tip"] = copy.deepcopy(cfg.DEFAULT_PROFILE["tip"])
        self._sync_tip()

    def _button_mode_changed(self, row, _pspec):
        if not self.loading:
            self.profile["button_mode"] = "click_tap" if row.get_selected() == 1 else "hover"
            self.changed()

    def _smoothing_changed(self, value):
        if not self.loading:
            self.profile["smoothing"] = 0.0 if value < 0.02 else round(value, 2)
            self.changed()

    def _dcd_changed(self, value):
        if not self.loading:
            self.profile["double_click_distance"] = 0.0 if value < 0.25 else value
            self.changed()


ORIENTATIONS = [(0, "Landscape"), (180, "Landscape, Flipped (Left-Handed)"),
                (90, "Portrait, Rotated Right"), (270, "Portrait, Rotated Left")]


def _captioned(widget, caption):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
    box.append(widget)
    label = Gtk.Label(label=caption)
    label.add_css_class("caption-heading")
    label.add_css_class("dim-label")
    box.append(label)
    return box


class MappingPage(Page):
    ICON, COLOR = "video-display-symbolic", "blue"
    DESCRIPTION = "Choose which part of the tablet matches which part of your displays."

    def __init__(self, window):
        super().__init__(window, title="Mapping", icon_name="video-display-symbolic",
                         name="mapping")
        hero_group = Adw.PreferencesGroup(
            title="Tablet → Displays",
            description="The highlighted tablet area matches the highlighted area of your "
                        "displays. In portion mode, drag to choose it.")
        self.tablet_preview = TabletPreview(self._area_changed)
        self.screen_preview = ScreenPreview(self._portion_changed)
        arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
        arrow.set_pixel_size(28)
        arrow.add_css_class("dim-label")
        screen_box = _captioned(self.screen_preview, "Displays")
        screen_box.set_size_request(300, -1)
        hero_group.add(hero_box(_captioned(self.tablet_preview, "Tablet"), arrow, screen_box,
                                spacing=6))
        self.add(hero_group)

        general = Adw.PreferencesGroup(title="Orientation and Mode")
        self.orientation = Adw.ComboRow(
            title="Orientation",
            model=Gtk.StringList.new([label for _, label in ORIENTATIONS]))
        self.orientation.connect("notify::selected", self._orientation_changed)
        general.add(self.orientation)
        self.mode = Adw.ComboRow(
            title="Mode",
            subtitle="Pen: each point on the tablet is a point on the screen. "
                     "Mouse: the pen moves the cursor like a mouse.",
            model=Gtk.StringList.new(["Pen", "Mouse"]))
        self.mode.connect("notify::selected", self._mode_changed)
        general.add(self.mode)
        self.speed_row, self.speed = scale_row(
            general.add, "Mouse Mode Speed", None, -1, 1, 0.05,
            lambda v: self._set_mapping("mouse_speed", v), lambda v: f"{v:+.2f}")
        self.accel_row, self.accel = scale_row(
            general.add, "Mouse Mode Acceleration", None, 0, 1, 0.05,
            lambda v: self._set_mapping("mouse_acceleration", v), percent)
        self.add(general)

        screen = Adw.PreferencesGroup(title="Screen Area")
        self.screen_row = Adw.ComboRow(title="Map To")
        self.screen_row.connect("notify::selected", self._screen_changed)
        screen.add(self.screen_row)
        screen.add(self._capture_row("screen", "Define with the Pen",
                                     "Tap two corners of the screen with the pen"))
        self.add(screen)

        tablet = Adw.PreferencesGroup(title="Tablet Area")
        self.full = Adw.SwitchRow(title="Use the Whole Tablet",
                                  subtitle="Turn off to choose a portion")
        self.full.connect("notify::active", self._full_changed)
        tablet.add(self.full)
        self.force = Adw.SwitchRow(
            title="Force Proportions",
            subtitle="Crops the area so a circle you draw stays round on screen")
        self.force.connect("notify::active", self._force_changed)
        tablet.add(self.force)
        tablet.add(self._capture_row("tablet", "Define with the Pen",
                                     "Tap two corners of the tablet with the pen"))
        self.add(tablet)
        self.screen_options = []

    def _capture_row(self, kind, title, subtitle):
        row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
        row.add_prefix(Gtk.Image.new_from_icon_name("find-location-symbolic"))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda *_: self.window.start_capture(kind))
        return row

    @property
    def mapping(self):
        return self.profile["mapping"]

    def populate(self):
        m = self.mapping
        values = [o for o, _ in ORIENTATIONS]
        self.orientation.set_selected(values.index(m["orientation"])
                                      if m["orientation"] in values else 0)
        mouse = m["mode"] == "mouse"
        self.mode.set_selected(1 if mouse else 0)
        self.speed.set_value(m["mouse_speed"])
        self.accel.set_value(m["mouse_acceleration"])
        self.speed_row.set_visible(mouse)
        self.accel_row.set_visible(mouse)

        desktop = self.window.desktop
        self.screen_options = [{"type": "all"}]
        labels = ["All Displays"]
        for index, monitor in enumerate(desktop.monitors, 1):
            self.screen_options.append({"type": "monitor", "monitor": monitor.connector})
            labels.append(f"Display {index}: {monitor.name}")
        self.screen_options.append({"type": "portion"})
        labels.append("Portion of the Screen")
        self.screen_row.set_model(Gtk.StringList.new(labels))
        screen = m["screen"]
        selected = 0
        for index, option in enumerate(self.screen_options):
            if option["type"] == screen.get("type") and option.get("monitor") == screen.get(
                    "monitor"):
                selected = index
        self.screen_row.set_selected(selected)
        self.screen_preview.set_state(desktop, screen)

        area = m["tablet_area"]
        portion = area.get("type") == "portion"
        self.full.set_active(not portion)
        self.force.set_active(m["force_proportions"])
        _, aspect = desktop.target(screen)
        size = tuple(self.window.client.status.get("tablet_mm") or DEFAULT_TABLET_MM)
        self.tablet_preview.set_state(size, m["orientation"],
                                      Rect.from_list(area.get("rect")), portion,
                                      m["force_proportions"], aspect)

    def _set_mapping(self, key, value):
        if self.loading:
            return
        self.mapping[key] = value
        self.load()
        self.changed()

    def _orientation_changed(self, row, _pspec):
        self._set_mapping("orientation", ORIENTATIONS[row.get_selected()][0])

    def _mode_changed(self, row, _pspec):
        self._set_mapping("mode", "mouse" if row.get_selected() == 1 else "pen")

    def _force_changed(self, row, _pspec):
        self._set_mapping("force_proportions", row.get_active())

    def _screen_changed(self, row, _pspec):
        if self.loading or row.get_selected() >= len(self.screen_options):
            return
        option = dict(self.screen_options[row.get_selected()])
        if option["type"] == "portion":
            current = self.mapping["screen"]
            if current.get("type") == "portion" and current.get("rect"):
                option["rect"] = current["rect"]
            else:
                bx, by, bw, bh = self.window.desktop.bbox
                option["rect"] = [bx + bw // 4, by + bh // 4, bw // 2, bh // 2]
        self._set_mapping("screen", option)

    def _portion_changed(self, _rect):
        self._set_mapping("screen", {"type": "portion",
                                     "rect": self.screen_preview.portion_pixels()})

    def _full_changed(self, row, _pspec):
        area = dict(self.mapping["tablet_area"])
        area["type"] = "full" if row.get_active() else "portion"
        if area["type"] == "portion" and Rect.from_list(area.get("rect")) == Rect():
            area["rect"] = [0.1, 0.1, 0.8, 0.8]
        self._set_mapping("tablet_area", area)

    def _area_changed(self, rect):
        self._set_mapping("tablet_area", {"type": "portion", "rect": rect.to_list()})

    def apply_capture(self, kind, rect):
        if kind == "screen":
            self._set_mapping("screen", {"type": "portion", "rect": [round(v) for v in rect]})
        else:
            self._set_mapping("tablet_area", {"type": "portion", "rect": list(rect)})


SLOT_NAMES = ["Top", "Top Right", "Right", "Bottom Right", "Bottom", "Bottom Left", "Left",
              "Top Left"]


class RadialPage(Page):
    ICON, COLOR = "view-app-grid-symbolic", "orange"
    DESCRIPTION = ("A circular menu that opens at the pen tip, like Wacom's on-screen "
                   "shortcuts. Assign “Radial Menu” to a pen button to open it.")

    def __init__(self, window):
        super().__init__(window, title="Radial Menu", icon_name="view-app-grid-symbolic",
                         name="radial")
        preview_group = Adw.PreferencesGroup()
        self.preview = RadialPreview(self._pick)
        preview_group.add(hero_box(self.preview))
        self.add(preview_group)

        assign = Adw.PreferencesGroup()
        self.assign_row = Adw.ActionRow(
            title="Open with the Upper Pen Button",
            subtitle="Assigns the radial menu to the upper button in the profile you are "
                     "editing")
        self.assign_button = Gtk.Button(label="Assign", valign=Gtk.Align.CENTER)
        self.assign_button.add_css_class("suggested-action")
        self.assign_button.connect("clicked", self._assign)
        self.assign_row.add_suffix(self.assign_button)
        assign.add(self.assign_row)
        self.add(assign)

        self.slots_group = Adw.PreferencesGroup(title="Actions",
                                                description="Click an action to change it.")
        self.slot_rows = []
        for index, name in enumerate(SLOT_NAMES):
            row = Adw.ActionRow(title=name, activatable=True)
            row.icon = Gtk.Image()
            row.add_prefix(row.icon)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, i=index: self._pick(i))
            self.slots_group.add(row)
            self.slot_rows.append(row)
        self.add(self.slots_group)

    @property
    def items(self):
        return self.window.config["radial_menu"]

    def populate(self):
        for row, action in zip(self.slot_rows, self.items, strict=True):
            row.set_subtitle(cfg.action_label(action))
            row.icon.set_from_icon_name(cfg.ACTION_ICONS.get(action.get("type")))
        self.preview.set_items(self.items)
        assigned = any(a.get("type") == "radial_menu"
                       for a in self.profile["buttons"].values())
        self.assign_button.set_sensitive(not assigned)
        self.assign_button.set_label("Assigned" if assigned else "Assign")

    def _pick(self, index):
        current = self.items[index]

        def done(action):
            self.items[index] = action
            self.load()
            self.changed()

        def picked(kind):
            if kind in PARAM_ACTIONS:
                configure_action(self.window, kind,
                                 current if current.get("type") == kind else None,
                                 lambda result: done({"type": kind, **result}))
            else:
                done({"type": kind})

        ActionPickerDialog(f"Radial Menu: {SLOT_NAMES[index]}", current.get("type"),
                           picked, cfg.NOT_IN_RADIAL).present(self.window)

    def _assign(self, *_args):
        self.profile["buttons"]["upper"] = {"type": "radial_menu"}
        self.window.reload_pages()
        self.changed()


def browsers():
    return [a for a in Gio.AppInfo.get_all()
            if a.should_show() and a.get_id() and "WebBrowser" in (a.get_categories() or "")]


def add_touch_profiles(config):
    """Creates profiles with the tip in touch mode for web browsers."""
    added = 0
    for app in browsers():
        if app.get_id() in config["profiles"]:
            continue
        profile = copy.deepcopy(config["profiles"][cfg.DEFAULT_PROFILE_ID])
        profile["name"] = app.get_display_name()
        profile["buttons"]["tip"] = {"type": "touch"}
        config["profiles"][app.get_id()] = profile
        added += 1
    return added


class AppsPage(Page):
    ICON, COLOR = "application-x-executable-symbolic", "green"
    DESCRIPTION = ("Each application can have its own settings. When it comes to the "
                   "front, Trazo switches profiles on its own.")

    def __init__(self, window):
        super().__init__(window, title="Applications", icon_name="view-app-grid-symbolic",
                         name="apps")
        info = Adw.PreferencesGroup()
        self.extension_row = Adw.ActionRow(title="GNOME Shell Extension")
        self.extension_button = Gtk.Button(label="Turn On", valign=Gtk.Align.CENTER)
        self.extension_button.add_css_class("suggested-action")
        self.extension_button.connect("clicked", self._enable_extension)
        self.extension_row.add_suffix(self.extension_button)
        info.add(self.extension_row)
        touch = Adw.ActionRow(
            title="Touch Mode in Web Browsers",
            subtitle="Like Windows Ink: drag with the tip to scroll, tap to click and hold "
                     "for a right click", activatable=True)
        touch.add_prefix(tile("input-touchscreen-symbolic", "blue"))
        touch.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        touch.connect("activated", self._add_browsers)
        info.add(touch)
        self.add(info)
        self.list_group = Adw.PreferencesGroup(title="Profiles")
        add = Gtk.Button(valign=Gtk.Align.CENTER)
        add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic",
                                        label="Add Application"))
        add.add_css_class("flat")
        add.connect("clicked", self._add)
        self.list_group.set_header_suffix(add)
        self.add(self.list_group)
        self.profile_rows = []
        window.client.connect("status", lambda *_: self.load())

    def populate(self):
        status = self.window.client.status
        if status.get("focus_extension"):
            self.extension_row.set_subtitle("On: per-app profiles, single cursor, tap "
                                            "ripples and the radial menu")
            self.extension_button.set_visible(False)
        else:
            self.extension_row.set_subtitle(
                "Needed for per-app profiles, the single cursor, ripples and the radial "
                "menu. After turning it on, log out and back in.")
            self.extension_button.set_visible(True)
        for row in self.profile_rows:
            self.list_group.remove(row)
        self.profile_rows = []
        active = status.get("profile")
        for pid, profile in self.window.ordered_profiles():
            row = Adw.ActionRow(title=profile.get("name") or pid)
            subtitles = []
            if pid == active:
                subtitles.append("In use now")
            if pid == self.window.profile_id:
                subtitles.append("Editing")
            if (profile["buttons"].get("tip") or {}).get("type") == "touch":
                subtitles.append("Touch mode")
            row.set_subtitle(" · ".join(subtitles))
            row.add_prefix(self._icon(pid))
            edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text="Edit This Profile")
            edit.add_css_class("flat")
            edit.connect("clicked", lambda _b, p=pid: self.window.select_profile(p, "pen"))
            row.add_suffix(edit)
            if pid != cfg.DEFAULT_PROFILE_ID:
                delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text="Delete Profile")
                delete.add_css_class("flat")
                delete.connect("clicked", lambda _b, p=pid: self._delete(p))
                row.add_suffix(delete)
            self.list_group.add(row)
            self.profile_rows.append(row)

    @staticmethod
    def _icon(pid):
        if pid != cfg.DEFAULT_PROFILE_ID:
            try:
                info = Gio.DesktopAppInfo.new(pid)
                if info and info.get_icon():
                    image = Gtk.Image.new_from_gicon(info.get_icon())
                    image.set_pixel_size(32)
                    return image
            except TypeError:
                pass
        return tile("view-app-grid-symbolic", "gray")

    def _add(self, *_args):
        def done(result):
            app_id = result.get("app_id")
            if not app_id:
                return
            profile = copy.deepcopy(self.window.config["profiles"][cfg.DEFAULT_PROFILE_ID])
            profile["name"] = result["name"]
            self.window.config["profiles"][app_id] = profile
            self.window.changed()
            self.window.select_profile(app_id, "pen")

        dialog = AppChooserDialog("Add Application", done,
                                  exclude=set(self.window.config["profiles"]))
        dialog.present(self.window)

    def _add_browsers(self, *_args):
        added = add_touch_profiles(self.window.config)
        self.window.changed()
        self.window.reload_pages()
        self.window.toast(f"Touch mode turned on for {added} browser(s)" if added else
                          "Your browsers already have profiles")

    def _delete(self, pid):
        self.window.config["profiles"].pop(pid, None)
        if self.window.profile_id == pid:
            self.window.profile_id = cfg.DEFAULT_PROFILE_ID
        self.window.changed()
        self.window.reload_pages()

    def _enable_extension(self, *_args):
        try:
            gnome.enable_extension()
            # Loads it right away when GNOME already knows the extension.
            Gio.Subprocess.new(["gnome-extensions", "enable", EXTENSION_UUID],
                               Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as error:
            self.window.toast(f"Could not turn on the extension: {error.message}")
            return
        self.window.toast("Extension turned on. Log out and back in to load it.")


class DiagnosticsPage(Page):
    ICON, COLOR = "dialog-information-symbolic", "gray"
    DESCRIPTION = "Live data from the tablet and pen, like in Wacom Center."

    FIELDS = (
        ("Pen", (("prox", "Proximity"), ("tool", "Tool"), ("tip", "Tip"),
                 ("lower", "Lower Button"), ("upper", "Upper Button"))),
        ("Position and Pressure", (("xy", "Position on Tablet"), ("uv", "Position on Screen"),
                                   ("pressure", "Physical Pressure"),
                                   ("output_pressure", "Pressure Sent"),
                                   ("distance", "Distance"))),
        ("Service", (("rate", "Report Rate"), ("app", "Active Application"),
                     ("profile", "Profile"), ("mode", "Mode"), ("touch_mode", "Touch Mode"))),
    )

    def __init__(self, window):
        super().__init__(window, title="Diagnostics", icon_name="dialog-information-symbolic",
                         name="diagnostics")
        self.rows = {}
        self.level = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER,
                                  width_request=140)
        for title, fields in self.FIELDS:
            group = Adw.PreferencesGroup(title=title)
            for key, label in fields:
                row = Adw.ActionRow(title=label)
                if key == "pressure":
                    row.add_suffix(self.level)
                value = Gtk.Label(label="—", css_classes=["dim-label", "numeric"])
                row.add_suffix(value)
                group.add(row)
                self.rows[key] = value
            self.add(group)
        window.client.connect("diagnostics", self._update)
        self.connect("map", lambda *_: window.client.set_diagnostics(True))
        self.connect("unmap", lambda *_: window.client.set_diagnostics(False))

    def _update(self, _client, data):
        pressure_max = data.get("pressure_max") or 1
        values = {
            "prox": "Near the tablet" if data.get("prox") else "Out of range",
            "tool": {"pen": "Pen", "rubber": "Eraser"}.get(data.get("tool"), "—"),
            "tip": "Touching" if data.get("tip") else "Lifted",
            "lower": "Pressed" if data.get("lower") else "—",
            "upper": "Pressed" if data.get("upper") else "—",
            "xy": f"{data.get('x', 0)} × {data.get('y', 0)}",
            "uv": f"{data.get('u', 0) * 100:.1f}% × {data.get('v', 0) * 100:.1f}%",
            "pressure": f"{data.get('pressure', 0)} / {pressure_max}",
            "output_pressure": percent(data.get("output_pressure", 0)),
            "distance": f"{data.get('distance', 0)} / {data.get('distance_max', 0)}",
            "rate": f"{data.get('rate', 0)} Hz",
            "app": data.get("app") or "—",
            "profile": cfg.DEFAULT_PROFILE_NAME if data.get("profile") == "*" else
                       (data.get("profile") or "—"),
            "mode": "Mouse" if data.get("mode") == "mouse" else "Pen",
            "touch_mode": "On" if data.get("touch_mode") else "Off",
        }
        for key, text in values.items():
            self.rows[key].set_label(text)
        self.level.set_value(min(1.0, data.get("pressure", 0) / pressure_max))


class GeneralPage(Page):
    ICON, COLOR = "emblem-system-symbolic", "gray"
    DESCRIPTION = "Service status, general behavior and backups."

    def __init__(self, window):
        super().__init__(window, title="General", icon_name="emblem-system-symbolic",
                         name="general")
        state = Adw.PreferencesGroup(title="Status")
        self.device_row = Adw.ActionRow(title="Tablet")
        self.device_row.add_prefix(tile("input-tablet-symbolic", "purple"))
        state.add(self.device_row)
        self.service_row = Adw.ActionRow(title="Trazo Service")
        self.service_row.add_prefix(tile("system-run-symbolic", "gray"))
        restart = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER)
        restart.add_css_class("flat")
        restart.connect("clicked", lambda *_: client.systemctl("restart", client.SERVICE))
        self.service_row.add_suffix(restart)
        state.add(self.service_row)
        self.enabled = switch_row(
            state, "Trazo Controls the Tablet",
            "When off, the tablet goes back to GNOME's basic settings",
            lambda v: self._set_config("enabled", v))
        self.add(state)

        behaviour = Adw.PreferencesGroup(title="Behavior")
        self.unified = switch_row(
            behaviour, "The Pen Is Also the Mouse",
            "Like Windows and macOS: one cursor, and the dock, hot corners and menus react "
            "when the pen hovers over them (the latter needs the GNOME extension)",
            lambda v: self._set_advanced("unified_cursor", v))
        self.feedback = switch_row(
            behaviour, "Tap Ripples",
            "Shows an animated circle when you click or press and hold with the pen",
            lambda v: self._set_advanced("visual_feedback", v))
        self.overlay = switch_row(
            behaviour, "Precision Mode Overlay",
            "Shows the screen region the tablet covers in precision mode",
            lambda v: self._set_advanced("precision_overlay", v))
        _, self.precision = scale_row(
            behaviour.add, "Precision Mode", "Fine ↔ ultra fine",
            0.1, 0.9, 0.05, lambda v: self._set_advanced("precision_factor", v), percent)
        self.add(behaviour)

        backup = Adw.PreferencesGroup(title="Settings")
        for title, subtitle, icon, handler in (
                ("Setup Assistant", "Set up the tablet again step by step",
                 "starred-symbolic", lambda *_: window.show_wizard()),
                ("Export Settings…", "Save every profile to a file",
                 "document-save-symbolic", self._export),
                ("Import Settings…", "Load profiles from a file",
                 "document-open-symbolic", self._import),
                ("Reset Everything", "Go back to the factory settings",
                 "edit-undo-symbolic", self._reset)):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", handler)
            backup.add(row)
        self.add(backup)

        advanced = Adw.PreferencesGroup(title="Advanced")
        _, self.pan_speed = scale_row(
            advanced.add, "Scrolling Speed", "For pan/scroll and touch mode",
            0.3, 3.0, 0.1, lambda v: self._set_advanced("pan_speed", v),
            lambda v: f"×{v:.1f}")
        _, self.floor = scale_row(
            advanced.add, "Minimum Touch Pressure",
            "Pressure applications receive on the lightest touch", 0.0, 0.2, 0.01,
            lambda v: self._set_advanced("contact_floor", v), percent)
        self.add(advanced)
        window.client.connect("status", lambda *_: self.load())

    def populate(self):
        c = self.window.client
        status = c.status
        self.service_row.set_subtitle("Running" if c.running else "Stopped")
        if status.get("connected"):
            self.device_row.set_subtitle(f"{status.get('device')} ({status.get('usb_id')})")
        else:
            self.device_row.set_subtitle(status.get("error") or "Not connected to the service")
        config = self.window.config
        advanced = config["advanced"]
        self.enabled.set_active(config.get("enabled", True))
        self.unified.set_active(advanced["unified_cursor"])
        self.feedback.set_active(advanced["visual_feedback"])
        self.overlay.set_active(advanced["precision_overlay"])
        self.precision.set_value(advanced["precision_factor"])
        self.pan_speed.set_value(advanced["pan_speed"])
        self.floor.set_value(advanced["contact_floor"])

    def _set_advanced(self, key, value):
        if not self.loading:
            self.window.config["advanced"][key] = value
            self.changed()

    def _set_config(self, key, value):
        if not self.loading:
            self.window.config[key] = value
            self.changed()

    def _export(self, *_args):
        dialog = Gtk.FileDialog(title="Export Settings", initial_name="trazo-settings.json")

        def done(d, result):
            try:
                file = d.save_finish(result)
            except GLib.Error:
                return
            cfg.save(self.window.config, file.get_path())
            self.window.toast("Settings exported")
        dialog.save(self.window, None, done)

    def _import(self, *_args):
        dialog = Gtk.FileDialog(title="Import Settings")

        def done(d, result):
            try:
                file = d.open_finish(result)
                with open(file.get_path()) as f:
                    data = json.load(f)
            except (GLib.Error, OSError, ValueError):
                self.window.toast("Could not read the file")
                return
            self.window.replace_config(cfg.normalize(data))
            self.window.toast("Settings imported")
        dialog.open(self.window, None, done)

    def _reset(self, *_args):
        alert = Adw.AlertDialog(heading="Reset Everything?",
                                body="Every Trazo profile and setting will be deleted.")
        alert.add_response("cancel", "Cancel")
        alert.add_response("reset", "Reset")
        alert.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_dialog, answer):
            if answer == "reset":
                config = cfg.normalize({})
                config["first_run_done"] = True
                self.window.replace_config(config)
                self.window.toast("Settings reset")
        alert.connect("response", response)
        alert.present(self.window)
