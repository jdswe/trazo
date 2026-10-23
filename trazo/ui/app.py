"""Entry point of the graphical interface."""

import os
import sys

import gi

# Trazo is an English app: GTK's own strings (file dialogs, About…) follow it
# even when the desktop uses another language.
os.environ["LANGUAGE"] = "en"

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

APP_ID = "io.github.jdswe.Trazo"

# macOS 26/27 "Liquid Glass" style on top of the system theme (Macify /
# WhiteSur): a background with soft color glows, translucent glass panels with
# a highlight on the edge, very rounded corners and capsule buttons. It is
# loaded above ~/.config/gtk-4.0/gtk.css so the theme does not cover it.
CSS = """
window.trazo {
  background-color: #f1eff6;
  background-image:
    radial-gradient(circle at 12% 8%, rgba(192, 97, 203, 0.30), transparent 42%),
    radial-gradient(circle at 88% 14%, rgba(98, 160, 234, 0.28), transparent 46%),
    radial-gradient(circle at 72% 92%, rgba(255, 163, 72, 0.18), transparent 48%),
    radial-gradient(circle at 25% 85%, rgba(87, 227, 137, 0.12), transparent 40%),
    linear-gradient(to bottom, #f7f5fa, #ebe7f1);
}
window.trazo navigation-split-view,
window.trazo toolbarview,
window.trazo .content-pane,
window.trazo stack,
window.trazo preferencespage,
window.trazo scrolledwindow,
window.trazo viewport {
  background-color: transparent;
  background-image: none;
}
window.trazo headerbar {
  background-color: transparent;
  background-image: none;
  box-shadow: none;
  border: none;
}

/* Floating glass sidebar */
window.trazo .sidebar-pane {
  margin: 8px 0 8px 8px;
  border-radius: 22px;
  background-color: rgba(255, 255, 255, 0.46);
  background-image: linear-gradient(to bottom, rgba(255, 255, 255, 0.35), rgba(255, 255, 255, 0.05));
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.95),
              inset 0 0 0 1px rgba(255, 255, 255, 0.55),
              0 12px 34px rgba(70, 40, 100, 0.12);
  border: none;
}
window.trazo .navigation-sidebar,
window.trazo .navigation-sidebar row { background-color: transparent; }
window.trazo .navigation-sidebar row { border-radius: 11px; margin: 1px 0; }
window.trazo .navigation-sidebar row:hover { background-color: rgba(255, 255, 255, 0.45); }
window.trazo .navigation-sidebar row:selected {
  color: white;
  background-image: linear-gradient(to bottom, rgba(192, 97, 203, 0.95), rgba(129, 61, 156, 0.95));
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.40), 0 4px 12px rgba(129, 61, 156, 0.30);
}

/* Content layer (not glass, per Apple): solid, light surfaces.
   Concentric corners: inner radius = outer radius - padding. */
window.trazo list.boxed-list,
window.trazo .hero {
  border: none;
  border-radius: 22px;
  background-color: rgba(255, 255, 255, 0.86);
  background-image: none;
  box-shadow: 0 0 0 1px rgba(80, 60, 110, 0.06), 0 6px 20px rgba(70, 40, 100, 0.06);
}
window.trazo list.boxed-list > row { background-color: transparent; border-radius: 0; }
window.trazo list.boxed-list > row:first-child { border-top-left-radius: 22px; border-top-right-radius: 22px; }
window.trazo list.boxed-list > row:last-child { border-bottom-left-radius: 22px; border-bottom-right-radius: 22px; }
window.trazo list.boxed-list > row:hover { background-color: rgba(120, 90, 160, 0.05); }
window.trazo .hero { padding: 12px; }
window.trazo .device-card {
  padding: 10px 12px;
  border-radius: 14px;
  background-color: rgba(255, 255, 255, 0.5);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.9);
}

/* Toolbar controls: glass capsules */
window.trazo headerbar menubutton > button {
  border-radius: 999px;
  min-height: 30px;
  min-width: 30px;
  background-color: rgba(255, 255, 255, 0.55);
  background-image: linear-gradient(to bottom, rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0.1));
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.5),
              0 2px 8px rgba(70, 40, 100, 0.10);
}

window.trazo entry.search,
window.trazo searchbar entry,
window.trazo .sidebar-pane entry {
  border-radius: 11px;
  background-color: rgba(255, 255, 255, 0.55);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.9), inset 0 0 0 1px rgba(120, 100, 140, 0.12);
}
window.trazo dropdown > button {
  border-radius: 11px;
  background-color: rgba(255, 255, 255, 0.55);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.9), inset 0 0 0 1px rgba(120, 100, 140, 0.12);
}

/* Glossy capsule buttons */
window.trazo button.suggested-action,
dialog button.suggested-action {
  border-radius: 999px;
  padding-left: 16px;
  padding-right: 16px;
  color: white;
  background-image: linear-gradient(to bottom, #c061cb, #813d9c);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.45), 0 3px 10px rgba(129, 61, 156, 0.35);
}
window.trazo scale trough highlight { background-image: linear-gradient(to right, #c061cb, #9141ac); }

/* Section icons (glossy colored squares) */
.mac-tile {
  color: white;
  border-radius: 8px;
  padding: 4px;
  -gtk-icon-size: 16px;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.45), 0 1px 3px rgba(0, 0, 0, 0.20);
}
.mac-tile.big {
  border-radius: 18px;
  padding: 12px;
  -gtk-icon-size: 34px;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.5), 0 8px 20px rgba(70, 40, 100, 0.22);
}
.tile-purple { background-image: linear-gradient(to bottom, #d27ddb, #813d9c); }
.tile-blue { background-image: linear-gradient(to bottom, #7fb4f0, #1c71d8); }
.tile-orange { background-image: linear-gradient(to bottom, #ffb366, #e66100); }
.tile-green { background-image: linear-gradient(to bottom, #6fe89a, #26a269); }
.tile-gray { background-image: linear-gradient(to bottom, #a8a7a4, #5e5c64); }
.tile-red { background-image: linear-gradient(to bottom, #f77b6d, #c01c28); }

.page-header { margin-top: 6px; }
.page-header .title-1 { font-weight: 800; }
.status-text.ok { color: #26a269; }
.status-text.off { color: #e66100; }

/* Pen button cards (inside a 22 px panel with 12 px padding: 10 px radius,
   concentric) */
.action-card {
  padding: 10px 14px;
  border-radius: 10px;
  border: none;
  background-color: rgba(120, 90, 160, 0.06);
  background-image: none;
  box-shadow: none;
  transition: all 160ms ease-out;
}
.action-card:hover { background-color: rgba(120, 90, 160, 0.11); }
.action-card .action-icon {
  padding: 7px;
  border-radius: 10px;
  color: white;
  background-image: linear-gradient(to bottom, #d27ddb, #813d9c);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.45);
}
.action-card.pressed {
  background-color: rgba(192, 97, 203, 0.22);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.8), 0 0 0 2px #9141ac,
              0 6px 18px rgba(129, 61, 156, 0.30);
}

/* Dark mode */
@media (prefers-color-scheme: dark) {
  window.trazo {
    background-color: #1d1a22;
    background-image:
      radial-gradient(circle at 12% 8%, rgba(145, 65, 172, 0.40), transparent 45%),
      radial-gradient(circle at 88% 14%, rgba(28, 113, 216, 0.30), transparent 48%),
      radial-gradient(circle at 72% 92%, rgba(230, 97, 0, 0.18), transparent 50%),
      linear-gradient(to bottom, #221e28, #17151b);
  }
  window.trazo .sidebar-pane,
  window.trazo headerbar menubutton > button {
    background-color: rgba(255, 255, 255, 0.07);
    background-image: linear-gradient(to bottom, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.0));
    box-shadow: inset 0 1px rgba(255, 255, 255, 0.18),
                inset 0 0 0 1px rgba(255, 255, 255, 0.08),
                0 10px 30px rgba(0, 0, 0, 0.35);
  }
  window.trazo list.boxed-list,
  window.trazo .hero {
    background-color: rgba(44, 40, 52, 0.92);
    box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.06), 0 8px 24px rgba(0, 0, 0, 0.30);
  }
  window.trazo .device-card { background-color: rgba(255, 255, 255, 0.07); }
  .action-card { background-color: rgba(255, 255, 255, 0.06); }
  window.trazo .navigation-sidebar row:hover,
  window.trazo list.boxed-list > row:hover,
  .action-card:hover { background-color: rgba(255, 255, 255, 0.10); }
  window.trazo entry.search,
  window.trazo .sidebar-pane entry,
  window.trazo dropdown > button {
    background-color: rgba(255, 255, 255, 0.08);
    box-shadow: inset 0 1px rgba(255, 255, 255, 0.15);
  }
}
"""


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
                         | Gio.ApplicationFlags.CAN_OVERRIDE_APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    def do_activate(self):
        from .window import Window
        window = self.props.active_window or Window(self)
        window.present()


def main():
    return Application().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
