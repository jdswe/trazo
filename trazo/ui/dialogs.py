"""Dialogs: capture a keyboard shortcut, pick modifiers, pick an application
and pick an action."""

from gi.repository import Adw, Gdk, Gio, Gtk

from .. import config as cfg
from .. import keys
from .widgets import ACTION_ICONS

_MASKS = [(Gdk.ModifierType.CONTROL_MASK, keys.KEY_LEFTCTRL),
          (Gdk.ModifierType.SHIFT_MASK, keys.KEY_LEFTSHIFT),
          (Gdk.ModifierType.ALT_MASK, keys.KEY_LEFTALT),
          (Gdk.ModifierType.SUPER_MASK, keys.KEY_LEFTMETA)]
_MODIFIER_MASK = {keys.KEY_LEFTCTRL: Gdk.ModifierType.CONTROL_MASK,
                  keys.KEY_RIGHTCTRL: Gdk.ModifierType.CONTROL_MASK,
                  keys.KEY_LEFTSHIFT: Gdk.ModifierType.SHIFT_MASK,
                  keys.KEY_RIGHTSHIFT: Gdk.ModifierType.SHIFT_MASK,
                  keys.KEY_LEFTALT: Gdk.ModifierType.ALT_MASK,
                  keys.KEY_RIGHTALT: Gdk.ModifierType.ALT_MASK,
                  keys.KEY_LEFTMETA: Gdk.ModifierType.SUPER_MASK,
                  keys.KEY_RIGHTMETA: Gdk.ModifierType.SUPER_MASK}
_KEYVAL_NAMES = {
    "Return": "Enter", "KP_Enter": "Num Enter", "BackSpace": "Backspace", "space": "Space",
    "Escape": "Esc", "Prior": "Page Up", "Next": "Page Down", "Up": "↑", "Down": "↓",
    "Left": "←", "Right": "→", "Print": "Print Screen", "KP_Add": "Num +",
    "KP_Subtract": "Num −", "KP_Multiply": "Num *", "KP_Divide": "Num /",
}


def _mods_to_codes(state):
    return [code for mask, code in _MASKS if state & mask]


def _key_label(keyval):
    """English name of the key as the user's layout prints it (so "Ñ" stays
    "Ñ" on a Spanish keyboard)."""
    keyval = Gdk.keyval_to_lower(keyval)
    name = Gdk.keyval_name(keyval) or ""
    if name in _KEYVAL_NAMES:
        return _KEYVAL_NAMES[name]
    codepoint = Gdk.keyval_to_unicode(keyval)
    char = chr(codepoint) if codepoint else ""
    if char.isprintable() and not char.isspace():
        return char.upper()
    return name.replace("_", " ")


def _dialog_frame(dialog, title, content, on_apply, apply_label="Apply"):
    dialog.set_title(title)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: dialog.close())
    header.pack_start(cancel)
    apply = None
    if on_apply:
        apply = Gtk.Button(label=apply_label)
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: on_apply())
        header.pack_end(apply)
    toolbar.add_top_bar(header)
    toolbar.set_content(content)
    dialog.set_child(toolbar)
    return apply


class KeyCaptureDialog(Adw.Dialog):
    """Press a combination; it is stored as physical key codes."""

    def __init__(self, on_done, current=None):
        super().__init__(content_width=420)
        self.on_done = on_done
        self.result = current if current and current.get("keys") else None
        self._modifier_only = False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        icon = Gtk.Image.new_from_icon_name("input-keyboard-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("dim-label")
        box.append(icon)
        hint = Gtk.Label(label="Press the key combination you want to assign.",
                         wrap=True, justify=Gtk.Justification.CENTER)
        box.append(hint)
        self.label = Gtk.Label(label=self._label_text())
        self.label.add_css_class("title-2")
        box.append(self.label)
        note = Gtk.Label(label="The keys stay pressed while you hold the pen button "
                               "(handy for Space, Ctrl and so on).",
                         wrap=True, justify=Gtk.Justification.CENTER)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        box.append(note)
        self.apply = _dialog_frame(self, "Keystroke", box, self._apply)
        self.apply.set_sensitive(self.result is not None)
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._pressed)
        controller.connect("key-released", self._released)
        self.add_controller(controller)

    def _label_text(self):
        return self.result["label"] if self.result else "…"

    def _set(self, codes, label):
        self.result = {"keys": keys.sort_combo(codes), "label": label}
        self.label.set_label(label)
        self.apply.set_sensitive(True)

    def _pressed(self, _ctrl, keyval, keycode, state):
        code = keycode - 8
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Escape and not mods:
            self.close()
            return True
        if code in keys.MODIFIERS:
            self._modifier_only = True
            codes = _mods_to_codes(mods | _MODIFIER_MASK[code])
            self.label.set_label(keys.combo_label(codes) + "+…")
            return True
        self._modifier_only = False
        mod_codes = _mods_to_codes(mods)
        label = "+".join([keys.key_name(c) for c in keys.sort_combo(mod_codes)]
                         + [_key_label(keyval)])
        self._set(mod_codes + [code], label)
        return True

    def _released(self, _ctrl, keyval, keycode, state):
        code = keycode - 8
        if code in keys.MODIFIERS and self._modifier_only:
            self._modifier_only = False
            codes = _mods_to_codes(state | _MODIFIER_MASK[code])
            self._set(codes, keys.combo_label(codes))
        return True

    def _apply(self):
        if self.result:
            self.on_done(self.result)
        self.close()


class ModifierDialog(Adw.Dialog):
    def __init__(self, on_done, current=None):
        super().__init__(content_width=380)
        self.on_done = on_done
        selected = set((current or {}).get("keys") or [])
        group = Adw.PreferencesGroup(
            description="The selected keys stay pressed while you hold the pen button.")
        self.rows = []
        for name, code in (("Ctrl", keys.KEY_LEFTCTRL), ("Shift", keys.KEY_LEFTSHIFT),
                           ("Alt", keys.KEY_LEFTALT), ("Super", keys.KEY_LEFTMETA)):
            row = Adw.SwitchRow(title=name, active=code in selected)
            group.add(row)
            self.rows.append((row, code))
        page = Adw.PreferencesPage()
        page.add(group)
        _dialog_frame(self, "Modifier", page, self._apply)

    def _apply(self):
        codes = [code for row, code in self.rows if row.get_active()]
        if codes:
            self.on_done({"keys": codes, "label": keys.combo_label(codes)})
        self.close()


def installed_apps():
    apps = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id()]
    return sorted(apps, key=lambda a: a.get_display_name().lower())


class AppChooserDialog(Adw.Dialog):
    """Searchable list of installed applications (and an optional command)."""

    def __init__(self, title, on_done, allow_command=False, exclude=()):
        super().__init__(content_width=440, content_height=560)
        self.on_done = on_done
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.search = Gtk.SearchEntry(placeholder_text="Search applications")
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        box.append(self.search)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_filter_func(self._filter)
        for app in installed_apps():
            if app.get_id() in exclude:
                continue
            row = Adw.ActionRow(title=app.get_display_name(), activatable=True)
            row.set_subtitle(app.get_id())
            icon = app.get_icon()
            image = Gtk.Image.new_from_gicon(icon) if icon else Gtk.Image.new_from_icon_name(
                "application-x-executable-symbolic")
            image.set_pixel_size(32)
            row.add_prefix(image)
            row.app = app
            row.connect("activated", self._chosen)
            self.listbox.append(row)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.listbox)
        box.append(scroller)
        if allow_command:
            group = Adw.PreferencesGroup(title="Or a command")
            self.command = Adw.EntryRow(title="Command, e.g. “gnome-calculator”",
                                        show_apply_button=True)
            self.command.connect("apply", self._command)
            group.add(self.command)
            box.append(group)
        _dialog_frame(self, title, box, None)
        self.set_focus(self.search)

    def _filter(self, row):
        text = self.search.get_text().strip().lower()
        return not text or text in row.get_title().lower() or text in row.app.get_id().lower()

    def _chosen(self, row):
        self.on_done({"app_id": row.app.get_id(), "name": row.app.get_display_name()})
        self.close()

    def _command(self, entry):
        text = entry.get_text().strip()
        if text:
            self.on_done({"command": text, "name": text})
            self.close()


CATEGORIES = [("basic", "General"), ("click", "Mouse Clicks"), ("keyboard", "Keyboard"),
              ("navigation", "Scrolling"), ("pen", "Pen and Display"),
              ("desktop", "Desktop and Applications")]


class ActionPickerDialog(Adw.Dialog):
    """Actions grouped by category (like the Wacom Center actions panel)."""

    def __init__(self, title, current_kind, on_pick, exclude=()):
        super().__init__(content_width=460, content_height=640)
        self.on_pick = on_pick
        page = Adw.PreferencesPage()
        for category, category_title in CATEGORIES:
            group = Adw.PreferencesGroup(title=category_title)
            count = 0
            for kind, (label, kind_category) in cfg.ACTIONS.items():
                if kind_category != category or kind in exclude:
                    continue
                row = Adw.ActionRow(title=label, activatable=True)
                row.add_prefix(Gtk.Image.new_from_icon_name(ACTION_ICONS.get(kind)))
                if kind in cfg.FIXED_COMBOS:
                    row.set_subtitle(cfg.FIXED_COMBOS[kind]["label"])
                if kind == current_kind:
                    check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                    check.add_css_class("accent")
                    row.add_suffix(check)
                row.connect("activated", self._picked, kind)
                group.add(row)
                count += 1
            if count:
                page.add(group)
        _dialog_frame(self, title, page, None)

    def _picked(self, _row, kind):
        self.close()
        self.on_pick(kind)
