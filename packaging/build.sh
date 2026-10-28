#!/bin/bash
# Builds the release files in dist/:
#   trazo_VERSION_all.deb   Debian/Ubuntu package (system-wide install)
#   trazo-VERSION.tar.gz    source archive with install.sh (per-user install)
#   SHA256SUMS
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import trazo; print(trazo.__version__)' "$SRC")"
DIST="$SRC/dist"
EXT_UUID="trazo@jdswe.github.io"
APP_ID="io.github.jdswe.Trazo"
MAINTAINER="Jose David Carranza Angarita <134252125+jdswe@users.noreply.github.com>"

rm -rf "$DIST"
mkdir -p "$DIST"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

# --- .deb ---------------------------------------------------------------------
ROOT="$BUILD/trazo_${VERSION}_all"
install -d "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/share/trazo" \
  "$ROOT/usr/share/applications" "$ROOT/usr/share/icons/hicolor/scalable/apps" \
  "$ROOT/usr/lib/systemd/user" "$ROOT/usr/lib/udev/rules.d" "$ROOT/usr/lib/modules-load.d" \
  "$ROOT/usr/share/gnome-shell/extensions/$EXT_UUID" "$ROOT/usr/share/doc/trazo"

cp -r "$SRC/trazo" "$SRC/bin" "$ROOT/usr/share/trazo/"
find "$ROOT/usr/share/trazo" -name __pycache__ -prune -exec rm -rf {} +
ln -s ../share/trazo/bin/trazo "$ROOT/usr/bin/trazo"
ln -s ../share/trazo/bin/trazo-daemon "$ROOT/usr/bin/trazo-daemon"
install -m 644 "$SRC/data/$APP_ID.desktop" "$ROOT/usr/share/applications/"
install -m 644 "$SRC/data/$APP_ID.svg" "$ROOT/usr/share/icons/hicolor/scalable/apps/"
sed 's|^ExecStart=.*|ExecStart=/usr/bin/trazo-daemon|' "$SRC/data/trazo-daemon.service" \
  > "$ROOT/usr/lib/systemd/user/trazo-daemon.service"
install -m 644 "$SRC/data/70-trazo.rules" "$ROOT/usr/lib/udev/rules.d/"
install -m 644 "$SRC/data/trazo-uinput.conf" "$ROOT/usr/lib/modules-load.d/"
install -m 644 "$SRC/extension/$EXT_UUID/"* "$ROOT/usr/share/gnome-shell/extensions/$EXT_UUID/"
install -m 644 "$SRC/README.md" "$ROOT/usr/share/doc/trazo/"
install -m 644 "$SRC/LICENSE" "$ROOT/usr/share/doc/trazo/copyright"

cat > "$ROOT/DEBIAN/control" <<EOF
Package: trazo
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, systemd, udev
Recommends: gnome-shell
Maintainer: $MAINTAINER
Homepage: https://jdswe.github.io/trazo/
Description: Complete Wacom tablet settings for Linux
 Trazo brings the features of Wacom Center to Linux: tip feel and pressure
 curve, pen buttons with any action, mapping to any display or portion,
 per-application profiles, touch mode like Windows Ink, a radial menu and
 live diagnostics, with a macOS-style interface.
EOF

cat > "$ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  modprobe uinput 2>/dev/null || true
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger --action=change --subsystem-match=input --subsystem-match=misc 2>/dev/null || true
  systemctl --global enable trazo-daemon.service 2>/dev/null || true
  gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
EOF

cat > "$ROOT/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ]; then
  systemctl --global disable trazo-daemon.service 2>/dev/null || true
fi
EOF
chmod -R u=rwX,go=rX "$ROOT"
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/prerm" "$ROOT/usr/share/trazo/bin/"*
dpkg-deb --root-owner-group --build "$ROOT" "$DIST/trazo_${VERSION}_all.deb" >/dev/null

# --- .tar.gz ------------------------------------------------------------------
NAME="trazo-$VERSION"
mkdir -p "$BUILD/$NAME"
cp -r "$SRC/trazo" "$SRC/bin" "$SRC/data" "$SRC/extension" "$SRC/tests" \
  "$SRC/install.sh" "$SRC/uninstall.sh" "$SRC/README.md" "$SRC/CHANGELOG.md" "$SRC/LICENSE" "$BUILD/$NAME/"
find "$BUILD/$NAME" -name __pycache__ -prune -exec rm -rf {} +
tar -C "$BUILD" --owner=0 --group=0 -czf "$DIST/$NAME.tar.gz" "$NAME"

(cd "$DIST" && sha256sum ./*.deb ./*.tar.gz | sed 's| \./| |' > SHA256SUMS)
echo "Built Trazo $VERSION:"
ls -l "$DIST"
