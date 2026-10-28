# Changelog

All notable changes to Trazo are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-28

### Fixed
- The mouse could not move the cursor while the pen was near the tablet, even lying still:
  a Wacom pen in proximity reports about 133 times a second, and every report made the
  GNOME extension hide the mouse cursor again and made the service pull the pointer back
  to the pen. Now the last device used on purpose owns the cursor, like on Windows and
  macOS: the mouse always takes over, and the pen takes the cursor back only when it really
  moves (about 2 mm), taps or clicks.
- Lifting a pen that was never used no longer moves the mouse pointer.
- Touch-mode scroll momentum stops as soon as the mouse is used, instead of pulling the
  pointer back to the pen for about a second.
- Website: the Copy buttons no longer cover the install commands.

### Added
- The GNOME extension tells the service when the real mouse is used (`MouseActive` signal).
- Tests for the extension's cursor-ownership rule, run with `gjs` from the Python suite.
- Website: icons for the GitHub, issues, releases and Macify links.

## [0.1.0] - 2026-10-22

### Added
- First public release: tip feel and pressure curve, pen buttons, mapping, per-app
  profiles, touch mode, radial menu, single cursor, smoothing, diagnostics, Setup
  Assistant, `.deb` and tarball packages, and the project website.

[0.1.1]: https://github.com/jdswe/trazo/releases/tag/v0.1.1
[0.1.0]: https://github.com/jdswe/trazo/releases/tag/v0.1.0
