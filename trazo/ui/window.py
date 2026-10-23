"""Trazo main window, structured like macOS System Settings: a sidebar with
search, the tablet on top (like the account card) and sections with colored
icons; the selected section's content on the right."""

from gi.repository import Adw, Gio, GLib, Gtk

from .. import __version__
from .. import config as cfg
from .. import gnome
from . import client
from .pages import (AppsPage, DiagnosticsPage, GeneralPage, MappingPage, PenPage, RadialPage,
                    tile)

SAVE_DELAY_MS = 250
SECTIONS = ((PenPage, MappingPage, RadialPage), (AppsPage,), (DiagnosticsPage, GeneralPage))
KEYWORDS = {
    "pen": "pressure tip buttons curve smoothing double click sensitivity feel",
    "mapping": "screen display monitor area orientation left-handed mouse proportions tablet",
    "radial": "radial menu on-screen shortcuts",
    "apps": "applications profiles browser touch windows ink",
    "diagnostics": "diagnostics data report rate pressure status",
    "general": "backup reset assistant setup cursor ripples service",
}


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Trazo", default_width=1080,
                         default_height=820)
        self.set_size_request(360, 480)
        self.add_css_class("trazo")
        self.config = cfg.load()
        self.profile_id = cfg.DEFAULT_PROFILE_ID
        self.desktop = gnome.read_desktop()
        self.client = client.DaemonClient()
        self._save_source = None
        self._capture_toast = None
        self._updating_dropdown = False

        self.pages = [cls(self) for section in SECTIONS for cls in section]
        self.split = Adw.NavigationSplitView(min_sidebar_width=250, max_sidebar_width=300,
                                             sidebar_width_fraction=0.27)
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.set_content(self.split)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)

        self.client.connect("status", self._status_changed)
        self.client.connect("captured", self._captured)
        gnome.watch_monitors(self._monitors_changed)
        self.connect("close-request", self._close_request)
        self.reload_pages()
        self.nav.select_row(self.nav.get_row_at_index(0))
        self._status_changed(self.client, self.client.status)
        if not self.config.get("first_run_done"):
            GLib.timeout_add(400, lambda: (self.show_wizard(), GLib.SOURCE_REMOVE)[1])

    # --- Layout --------------------------------------------------------------
    def _build_sidebar(self):
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        menu = Gio.Menu()
        menu.append("Setup Assistant", "win.wizard")
        menu.append("About Trazo", "win.about")
        for name, callback in (("about", self._about), ("wizard", self.show_wizard)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, c=callback: c())
            self.add_action(action)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main Menu"))
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=2,
                      margin_bottom=12, margin_start=10, margin_end=10)
        self.search = Gtk.SearchEntry(placeholder_text="Search")
        self.search.connect("search-changed", lambda *_: self.nav.invalidate_filter())
        box.append(self.search)

        account = Gtk.Box(spacing=12)
        account.add_css_class("device-card")
        account.append(tile("input-tablet-symbolic", "purple", big=True))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       valign=Gtk.Align.CENTER)
        self.device_name = Gtk.Label(label="Wacom Tablet", xalign=0, wrap=True)
        self.device_name.add_css_class("heading")
        text.append(self.device_name)
        self.device_status = Gtk.Label(xalign=0)
        self.device_status.add_css_class("caption")
        self.device_status.add_css_class("status-text")
        text.append(self.device_status)
        account.append(text)
        box.append(account)

        profile_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label="Settings For", xalign=0, margin_start=4)
        label.add_css_class("caption-heading")
        label.add_css_class("dim-label")
        profile_box.append(label)
        self.profile_list = Gtk.StringList()
        self.profile_ids = []
        self.profile_dropdown = Gtk.DropDown(model=self.profile_list,
                                             tooltip_text="Application whose settings you edit")
        self.profile_dropdown.connect("notify::selected", self._profile_selected)
        profile_box.append(self.profile_dropdown)
        box.append(profile_box)

        self.nav = Gtk.ListBox()
        self.nav.add_css_class("navigation-sidebar")
        firsts = set()
        index = 0
        for section in SECTIONS:
            firsts.add(index)
            for _cls in section:
                page = self.pages[index]
                row = Gtk.ListBoxRow()
                row.page_name = page.get_name()
                row.search_text = f"{page.get_title()} {KEYWORDS[page.get_name()]}".lower()
                line = Gtk.Box(spacing=10, margin_top=3, margin_bottom=3, margin_start=2)
                line.append(tile(page.ICON, page.COLOR))
                line.append(Gtk.Label(label=page.get_title(), xalign=0))
                row.set_child(line)
                self.nav.append(row)
                index += 1
        self.nav.set_header_func(lambda row, _before: row.set_header(
            Gtk.Box(height_request=10) if row.get_index() in firsts and row.get_index() > 0
            else None))
        self.nav.set_filter_func(self._nav_filter)
        self.nav.connect("row-selected", self._nav_selected)
        box.append(self.nav)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(box)
        toolbar.set_content(scroller)
        return Adw.NavigationPage(title="Trazo", child=toolbar)

    def _build_content(self):
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.content_title = Adw.WindowTitle()
        header.set_title_widget(self.content_title)
        toolbar.add_top_bar(header)
        self.banner = Adw.Banner(button_label="Start Service")
        self.banner.connect("button-clicked", self._start_service)
        toolbar.add_top_bar(self.banner)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=150)
        for page in self.pages:
            self.stack.add_named(page, page.get_name())
        self.toasts = Adw.ToastOverlay(child=self.stack)
        toolbar.set_content(self.toasts)
        self.content_page = Adw.NavigationPage(title="Pen", child=toolbar)
        return self.content_page

    def _nav_filter(self, row):
        text = self.search.get_text().strip().lower()
        return not text or all(word in row.search_text for word in text.split())

    def _nav_selected(self, _listbox, row):
        if row is None:
            return
        self.stack.set_visible_child_name(row.page_name)
        self._update_title()
        self.split.set_show_content(True)

    def _update_title(self):
        page = self.stack.get_visible_child()
        title = page.get_title() if page else "Trazo"
        self.content_page.set_title(title)
        self.content_title.set_title(title)
        self.content_title.set_subtitle(f"Settings for: {self.profile.get('name')}")

    def show_wizard(self):
        from .wizard import SetupWizard
        SetupWizard(self).present(self)

    # --- Profiles ------------------------------------------------------------
    @property
    def profile(self):
        profiles = self.config["profiles"]
        if self.profile_id not in profiles:
            self.profile_id = cfg.DEFAULT_PROFILE_ID
        return profiles[self.profile_id]

    def ordered_profiles(self):
        profiles = self.config["profiles"]
        others = sorted((p for p in profiles if p != cfg.DEFAULT_PROFILE_ID),
                        key=lambda p: (profiles[p].get("name") or p).lower())
        return [(pid, profiles[pid]) for pid in [cfg.DEFAULT_PROFILE_ID] + others]

    def reload_pages(self):
        ordered = self.ordered_profiles()
        self.profile_ids = [pid for pid, _ in ordered]
        self._updating_dropdown = True
        self.profile_list.splice(0, self.profile_list.get_n_items(),
                                 [p.get("name") or pid for pid, p in ordered])
        self.profile_dropdown.set_selected(self.profile_ids.index(self.profile_id)
                                           if self.profile_id in self.profile_ids else 0)
        self._updating_dropdown = False
        for page in self.pages:
            page.load()
        self._update_title()

    def select_profile(self, profile_id, page=None):
        self.profile_id = profile_id
        self.reload_pages()
        if page:
            for index, p in enumerate(self.pages):
                if p.get_name() == page:
                    self.nav.select_row(self.nav.get_row_at_index(index))

    def _profile_selected(self, dropdown, _pspec):
        if self._updating_dropdown:
            return
        index = dropdown.get_selected()
        if 0 <= index < len(self.profile_ids) and self.profile_ids[index] != self.profile_id:
            self.profile_id = self.profile_ids[index]
            self.reload_pages()

    def replace_config(self, config):
        self.config = config
        self.profile_id = cfg.DEFAULT_PROFILE_ID
        self.changed()
        self.reload_pages()

    # --- Saving --------------------------------------------------------------
    def changed(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._save)

    def _save(self):
        self._save_source = None
        cfg.save(self.config)
        self.client.reload()
        if len(self.profile_ids) != len(self.config["profiles"]):
            self.reload_pages()
        return GLib.SOURCE_REMOVE

    def _close_request(self, *_args):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save()
        if self._capture_toast:
            self.client.cancel_capture()
        return False

    # --- Status and notifications -------------------------------------------
    def toast(self, text, button=None, on_button=None, timeout=4):
        toast = Adw.Toast(title=text, timeout=timeout)
        if button:
            toast.set_button_label(button)
            toast.connect("button-clicked", lambda *_: on_button())
        self.toasts.add_toast(toast)
        return toast

    def _set_status(self, text, ok):
        self.device_status.set_label(f"● {text}")
        for css in ("ok", "off"):
            self.device_status.remove_css_class(css)
        self.device_status.add_css_class("ok" if ok else "off")

    def _status_changed(self, client_, status):
        if status.get("device"):
            self.device_name.set_label(
                status["device"].removeprefix("Wacom ").removesuffix(" Pen"))
        if not client_.known:
            self._set_status("Checking…", True)
        elif not client_.running:
            self._set_status("Service stopped", False)
            self.banner.set_title("The Trazo service is not running: the tablet is using "
                                  "GNOME's basic settings.")
            self.banner.set_button_label("Start Service")
            self.banner.set_revealed(True)
        elif not status.get("connected"):
            self._set_status("Disconnected", False)
            self.banner.set_title(status.get("error") or "Tablet not available")
            self.banner.set_button_label("Retry")
            self.banner.set_revealed(True)
        else:
            self._set_status("Connected", True)
            self.banner.set_revealed(False)

    def _start_service(self, *_args):
        if self.client.running:
            self.client.reload()
        else:
            client.systemctl("start", client.SERVICE,
                             on_done=lambda ok: None if ok else self.toast(
                                 "Could not start the service. Did you run install.sh?"))

    def _monitors_changed(self):
        self.desktop = gnome.read_desktop()
        self.reload_pages()

    # --- "Define with the pen" -----------------------------------------------
    def start_capture(self, kind):
        if not self.client.running:
            self.toast("The Trazo service must be running")
            return
        where = "screen" if kind == "screen" else "tablet"
        self.client.start_capture(kind)
        self._capture_toast = self.toast(
            f"With the pen, tap the top-left corner and then the bottom-right corner of "
            f"the {where}", button="Cancel", on_button=self._cancel_capture, timeout=0)

    def _cancel_capture(self):
        self.client.cancel_capture()
        self._capture_toast = None

    def _captured(self, _client, kind, rect):
        if self._capture_toast:
            self._capture_toast.dismiss()
            self._capture_toast = None
        for page in self.pages:
            if isinstance(page, MappingPage):
                page.apply_capture(kind, rect)
        self.toast("Area defined")

    def _about(self, *_args):
        about = Adw.AboutDialog(
            application_name="Trazo", application_icon="io.github.jdswe.Trazo",
            version=__version__, developer_name="Jose David Carranza Angarita",
            comments="Complete Wacom tablet settings for Linux: tip feel, buttons, mapping "
                     "and per-application profiles.",
            license_type=Gtk.License.MIT_X11)
        about.present(self)
