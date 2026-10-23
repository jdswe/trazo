#!/bin/bash
# Installs Trazo for the current user. Run it without sudo: it only asks for
# your password to grant access to the tablet.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="$HOME/.local/share/trazo"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
UNITS="$HOME/.config/systemd/user"
EXT_UUID="trazo@jdswe.github.io"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run ./install.sh as your normal user (without sudo)."
  exit 1
fi

echo "==> 1/4 Checking dependencies"
if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' \
    2>/dev/null; then
  sudo apt-get install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
fi

# Compares the rules ignoring comments, so a reworded comment needs no sudo.
same_rules() {
  [ -f "$2" ] && cmp -s <(grep -v '^#' "$1") <(grep -v '^#' "$2")
}

if same_rules "$SRC/data/70-trazo.rules" /etc/udev/rules.d/70-trazo.rules &&
   same_rules "$SRC/data/trazo-uinput.conf" /etc/modules-load.d/trazo-uinput.conf; then
  echo "==> 2/4 Tablet access permissions: already installed"
else
  echo "==> 2/4 Tablet access permissions (asks for your password)"
  sudo install -m 644 "$SRC/data/70-trazo.rules" /etc/udev/rules.d/70-trazo.rules
  sudo install -m 644 "$SRC/data/trazo-uinput.conf" /etc/modules-load.d/trazo-uinput.conf
  sudo modprobe uinput 2>/dev/null || true
  sudo udevadm control --reload-rules
  sudo udevadm trigger --action=change --subsystem-match=input --subsystem-match=misc
  sudo udevadm settle || true
fi

echo "==> 3/4 Copying Trazo to $PREFIX"
rm -rf "$PREFIX"
mkdir -p "$PREFIX" "$BIN" "$APPS" "$ICONS" "$UNITS" "$EXT_DIR"
cp -r "$SRC/trazo" "$SRC/bin" "$PREFIX/"
find "$PREFIX" -name __pycache__ -prune -exec rm -rf {} +
ln -sf "$PREFIX/bin/trazo" "$BIN/trazo"
ln -sf "$PREFIX/bin/trazo-daemon" "$BIN/trazo-daemon"
sed "s|^Exec=trazo|Exec=$PREFIX/bin/trazo|" "$SRC/data/io.github.jdswe.Trazo.desktop" \
  > "$APPS/io.github.jdswe.Trazo.desktop"
install -m 644 "$SRC/data/io.github.jdswe.Trazo.svg" "$ICONS/"
install -m 644 "$SRC/data/trazo-daemon.service" "$UNITS/"
cp "$SRC/extension/$EXT_UUID/"* "$EXT_DIR/"
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "==> 4/4 Enabling the service and the extension"
systemctl --user daemon-reload
systemctl --user enable trazo-daemon.service
systemctl --user restart trazo-daemon.service
relogin=0
if ! gnome-extensions enable "$EXT_UUID" 2>/dev/null; then
  # GNOME on Wayland only picks up new extensions after logging back in; mark
  # it as enabled so it turns on by itself then.
  python3 - "$EXT_UUID" <<'PY'
import sys
from gi.repository import Gio
settings = Gio.Settings.new("org.gnome.shell")
enabled = list(settings.get_strv("enabled-extensions"))
if sys.argv[1] not in enabled:
    settings.set_strv("enabled-extensions", enabled + [sys.argv[1]])
Gio.Settings.sync()
PY
  relogin=1
fi

echo
echo "Trazo is installed. Open it from the app grid or run: trazo"
if [ "$relogin" = 1 ] || [ "${XDG_SESSION_TYPE:-}" = wayland ]; then
  echo "To turn on the GNOME extension (single cursor, ripples, radial menu and per-app"
  echo "profiles), log out and back in."
fi
