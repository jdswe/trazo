"""Setup Assistant (first run), like Wacom Center's and the macOS Setup
Assistant: a few steps with simple questions."""

from gi.repository import Adw, GLib, Gtk

from .. import config as cfg
from .. import gnome
from .pages import add_touch_profiles, browsers, tile
from .widgets import TabletIllustration

BUTTON_PRESETS = [
    ("Recommended", "Lower: right click · Upper: pan/scroll",
     {"lower": {"type": "right_click"}, "upper": {"type": "pan_scroll"}}),
    ("Classic", "Lower: right click · Upper: middle click",
     {"lower": {"type": "right_click"}, "upper": {"type": "middle_click"}}),
    ("Drawing", "Lower: undo · Upper: radial menu",
     {"lower": {"type": "undo"}, "upper": {"type": "radial_menu"}}),
    ("Browsing", "Lower: right click · Upper: radial menu",
     {"lower": {"type": "right_click"}, "upper": {"type": "radial_menu"}}),
]


def _step(icon, color, title, text, *children):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER,
                  margin_start=36, margin_end=36, margin_top=12, margin_bottom=12)
    if icon:
        box.append(tile(icon, color, big=True))
    heading = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
    heading.add_css_class("title-1")
    box.append(heading)
    if text:
        body = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER,
                         max_width_chars=48)
        body.add_css_class("dim-label")
        box.append(body)
    for child in children:
        box.append(child)
    return box


def _choices(options, selected=0):
    """Mutually exclusive options (like macOS radio buttons)."""
    group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    group.add_css_class("boxed-list")
    checks = []
    first = None
    for index, (title, subtitle) in enumerate(options):
        row = Adw.ActionRow(title=title, subtitle=subtitle or "", activatable=True)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=index == selected)
        if first is None:
            first = check
        else:
            check.set_group(first)
        row.add_prefix(check)
        row.set_activatable_widget(check)
        group.append(row)
        checks.append(check)
    return group, checks


class SetupWizard(Adw.Dialog):
    def __init__(self, window):
        super().__init__(content_width=560, content_height=620, title="Set Up Trazo")
        self.window = window
        self.set_can_close(True)
        desktop = window.desktop
        device = window.client.status.get("device", "").removesuffix(" Pen") or "your tablet"

        illustration = TabletIllustration()
        illustration.set_size_request(260, 150)
        welcome = _step(None, None, "Welcome to Trazo",
                        f"Let's set up your {device} in a few steps. You can change "
                        "everything later.", illustration)

        hands, self.hand_checks = _choices([("Right-Handed", "I hold the pen in my right hand"),
                                            ("Left-Handed", "I hold the pen in my left hand: "
                                                            "the tablet is rotated 180°")])
        hand = _step("input-tablet-symbolic", "purple", "Which hand do you draw with?", None,
                     hands)

        presets, self.preset_checks = _choices([(t, s) for t, s, _ in BUTTON_PRESETS])
        buttons = _step("input-mouse-symbolic", "blue", "Pen Buttons",
                        "Choose what the two side buttons do.", presets)

        screens = [("All Displays", "The tablet covers your whole desktop")]
        self.screen_values = [{"type": "all"}]
        for index, monitor in enumerate(desktop.monitors, 1):
            screens.append((f"Display {index}", monitor.name))
            self.screen_values.append({"type": "monitor", "monitor": monitor.connector})
        screen_list, self.screen_checks = _choices(screens)
        self.force = Adw.SwitchRow(title="Force Proportions",
                                   subtitle="Circles stay round on screen", active=True)
        force_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        force_box.add_css_class("boxed-list")
        force_box.append(self.force)
        screen = _step("video-display-symbolic", "blue", "Where do you want to use the pen?",
                       None, screen_list, force_box)

        names = ", ".join(a.get_display_name() for a in browsers()[:3]) or "your web browsers"
        self.touch = Adw.SwitchRow(title="Touch Mode in Web Browsers",
                                   subtitle=f"For {names}: drag to scroll, tap to click and "
                                            "hold for a right click",
                                   active=True)
        self.feedback = Adw.SwitchRow(title="Tap Ripples",
                                      subtitle="An animated circle confirms every tap",
                                      active=True)
        self.unified = Adw.SwitchRow(title="The Pen Is Also the Mouse",
                                     subtitle="One cursor; the dock and menus react when the "
                                              "pen hovers over them",
                                     active=True)
        ink_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        ink_list.add_css_class("boxed-list")
        for row in (self.unified, self.touch, self.feedback):
            ink_list.append(row)
        ink = _step("input-touchscreen-symbolic", "orange", "Just Like Windows and macOS",
                    "Features that make the pen feel natural across the whole system.",
                    ink_list)

        done = _step("object-select-symbolic", "green", "You're All Set!",
                     "Try your pen in the Pen section: draw in the test area and adjust the "
                     "tip feel to your liking.")

        self.carousel = Adw.Carousel(allow_scroll_wheel=False, allow_mouse_drag=False,
                                     vexpand=True)
        for page in (welcome, hand, buttons, screen, ink, done):
            self.carousel.append(page)
        self.carousel.connect("page-changed", lambda *_: self._sync())

        dots = Adw.CarouselIndicatorDots(carousel=self.carousel)
        self.back = Gtk.Button(label="Back")
        self.back.connect("clicked", lambda *_: self._go(-1))
        self.next = Gtk.Button(label="Continue")
        self.next.add_css_class("suggested-action")
        self.next.add_css_class("pill")
        self.next.connect("clicked", lambda *_: self._go(1))
        footer = Gtk.CenterBox(margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        footer.set_start_widget(self.back)
        footer.set_center_widget(dots)
        footer.set_end_widget(self.next)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        toolbar.add_top_bar(header)
        toolbar.set_content(self.carousel)
        toolbar.add_bottom_bar(footer)
        self.set_child(toolbar)
        self.connect("closed", self._closed)
        self._sync()

    def _closed(self, *_args):
        # Closed without finishing: do not show it again on its own (it stays
        # available from the menu).
        if not self.window.config.get("first_run_done"):
            self.window.config["first_run_done"] = True
            self.window.changed()

    def _index(self):
        return round(self.carousel.get_position())

    def _sync(self):
        index = self._index()
        last = self.carousel.get_n_pages() - 1
        self.back.set_visible(0 < index < last)
        self.next.set_label("Done" if index == last else
                            "Get Started" if index == 0 else "Continue")

    def _go(self, delta):
        index = self._index() + delta
        if index >= self.carousel.get_n_pages():
            self._finish()
            return
        index = max(0, index)
        self.carousel.scroll_to(self.carousel.get_nth_page(index), True)

    @staticmethod
    def _selected(checks):
        return next((i for i, c in enumerate(checks) if c.get_active()), 0)

    def _finish(self):
        config = self.window.config
        profile = config["profiles"][cfg.DEFAULT_PROFILE_ID]
        mapping = profile["mapping"]
        mapping["orientation"] = 180 if self._selected(self.hand_checks) == 1 else 0
        mapping["screen"] = dict(self.screen_values[self._selected(self.screen_checks)])
        mapping["force_proportions"] = self.force.get_active()
        profile["buttons"].update(BUTTON_PRESETS[self._selected(self.preset_checks)][2])
        config["advanced"]["visual_feedback"] = self.feedback.get_active()
        config["advanced"]["unified_cursor"] = self.unified.get_active()
        if self.touch.get_active():
            add_touch_profiles(config)
        config["first_run_done"] = True
        try:
            gnome.enable_extension()
        except GLib.Error:
            pass
        self.window.changed()
        self.window.reload_pages()
        self.close()
