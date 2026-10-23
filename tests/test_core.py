import json
import os
import tempfile
import unittest

from trazo import config as cfg
from trazo import devices, keys
from trazo.curve import PressureMapper, bezier_y, curve_from_softness, softness_from_curve
from trazo.mapping import Desktop, Mapper, Monitor, Rect, from_user_view, to_user_view


class CurveTest(unittest.TestCase):
    def test_linear(self):
        for x in (0.0, 0.1, 0.5, 0.9, 1.0):
            self.assertAlmostEqual(bezier_y(x, [1 / 3, 1 / 3, 2 / 3, 2 / 3]), x, places=4)

    def test_softness_roundtrip(self):
        for s in (-1.0, -0.5, 0.0, 0.4, 1.0):
            self.assertAlmostEqual(softness_from_curve(curve_from_softness(s)), s, places=6)

    def test_soft_is_above_firm(self):
        soft = bezier_y(0.3, curve_from_softness(-0.8))
        firm = bezier_y(0.3, curve_from_softness(0.8))
        self.assertGreater(soft, 0.3)
        self.assertLess(firm, 0.3)

    def test_maximum_saturates(self):
        mapper = PressureMapper(threshold=0.0, maximum=0.5)
        self.assertEqual(mapper.map(0.6, True), (True, 1.0))


class MappingTest(unittest.TestCase):
    def test_user_view_inverse(self):
        for orientation in (0, 90, 180, 270):
            a, b = to_user_view(0.2, 0.7, orientation)
            x, y = from_user_view(a, b, orientation)
            self.assertAlmostEqual(x, 0.2)
            self.assertAlmostEqual(y, 0.7)

    def test_tablet_portion(self):
        mapper = Mapper(tablet_area=Rect(0.5, 0.5, 0.5, 0.5))
        self.assertEqual(mapper.map(0.5, 0.5), (0.0, 0.0))
        self.assertEqual(mapper.map(0.0, 0.0), (0.0, 0.0))
        self.assertEqual(mapper.map(1.0, 1.0), (1.0, 1.0))

    def test_screen_portion(self):
        desktop = Desktop([Monitor("A", "A", 0, 0, 1000, 500)])
        target, aspect = desktop.target({"type": "portion", "rect": [500, 0, 500, 250]})
        self.assertEqual(target, Rect(0.5, 0.0, 0.5, 0.5))
        self.assertAlmostEqual(aspect, 2.0)

    def test_missing_monitor_falls_back_to_all(self):
        desktop = Desktop([Monitor("A", "A", 0, 0, 1000, 500)])
        target, _ = desktop.target({"type": "monitor", "monitor": "HDMI-9"})
        self.assertEqual(target, Rect())


class ConfigTest(unittest.TestCase):
    def test_roundtrip_and_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as f:
                json.dump({"profiles": {"*": {"buttons": {"lower": {"type": "nope"}},
                                              "tip": {"threshold": "x"}}}}, f)
            config = cfg.load(path)
            profile = config["profiles"]["*"]
            self.assertEqual(profile["buttons"]["lower"], {"type": "right_click"})
            self.assertEqual(profile["tip"]["threshold"], 0.01)
            cfg.save(config, path)
            self.assertEqual(cfg.load(path), config)

    def test_profile_for_app(self):
        config = cfg.normalize({"profiles": {"krita.desktop": {"name": "Krita"}}})
        self.assertEqual(cfg.profile_for(config, "krita.desktop")[0], "krita.desktop")
        self.assertEqual(cfg.profile_for(config, "otra.desktop")[0], "*")

    def test_old_default_profile_name_is_migrated(self):
        config = cfg.normalize({"profiles": {"*": {"name": "Todas las demás aplicaciones"}}})
        self.assertEqual(config["profiles"]["*"]["name"], cfg.DEFAULT_PROFILE_NAME)

    def test_action_label(self):
        action = {"type": "keystroke", **keys.combo(keys.KEY_LEFTCTRL, keys.KEY_Z)}
        self.assertEqual(cfg.action_label(action), "Keystroke: Ctrl+Z")


class DevicesTest(unittest.TestCase):
    def test_sysfs_bitmap(self):
        # Same format as /sys/class/input/event8/device/capabilities/key
        bits = devices.parse_sysfs_bitmap("1c03 0 0 0 0 0")
        self.assertEqual(bits, {0x140, 0x141, 0x14A, 0x14B, 0x14C})


if __name__ == "__main__":
    unittest.main()
