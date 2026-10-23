#!/bin/bash
# Uninstalls Trazo. The tablet goes back to GNOME's normal settings. Your
# settings stay in ~/.config/trazo in case you install it again.
set -uo pipefail

EXT_UUID="trazo@jdswe.github.io"

systemctl --user disable --now trazo-daemon.service 2>/dev/null
gnome-extensions disable "$EXT_UUID" 2>/dev/null
rm -f "$HOME/.config/systemd/user/trazo-daemon.service"
systemctl --user daemon-reload
rm -rf "$HOME/.local/share/trazo" "$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
rm -f "$HOME/.local/bin/trazo" "$HOME/.local/bin/trazo-daemon" \
  "$HOME/.local/share/applications/io.github.jdswe.Trazo.desktop" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.jdswe.Trazo.svg"

echo "Removing the tablet permissions (asks for your password)"
sudo rm -f /etc/udev/rules.d/70-trazo.rules /etc/modules-load.d/trazo-uinput.conf
sudo udevadm control --reload-rules
sudo udevadm trigger --action=change --subsystem-match=input --subsystem-match=misc

echo "Trazo was uninstalled. Your settings are still in ~/.config/trazo"
