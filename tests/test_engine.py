import copy
import unittest

from trazo import config as cfg
from trazo import evdev as ev
from trazo import keys
from trazo.engine import Engine, PenState
from trazo.mapping import Desktop, Monitor

ABSINFO = {
    ev.ABS_X: ev.AbsInfo(0, 0, 15200, 0, 0, 100),
    ev.ABS_Y: ev.AbsInfo(0, 0, 9500, 0, 0, 100),
    ev.ABS_PRESSURE: ev.AbsInfo(0, 0, 2047, 0, 0, 0),
    ev.ABS_DISTANCE: ev.AbsInfo(0, 0, 63, 0, 0, 0),
}
ADVANCED = cfg.DEFAULT_CONFIG["advanced"]


class FakeSink:
    def __init__(self):
        self.calls = []
        self.last_pen = PenState()

    def pen(self, state):
        self.last_pen = state
        self.calls.append(("pen", state))

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name,) + args)
        return record

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.sink = FakeSink()
        self.clock = Clock()
        self.engine = Engine(self.sink, ABSINFO, clock=self.clock)
        self.profile = copy.deepcopy(cfg.DEFAULT_PROFILE)
        self.profile["smoothing"] = 0.0
        self.apply()

    def apply(self):
        self.engine.set_profile(self.profile, ADVANCED, 400)

    def frame(self, x=0.5, y=0.5, p=0.0, lower=False, upper=False, prox=True):
        events = [(ev.EV_KEY, ev.BTN_TOOL_PEN, int(prox)),
                  (ev.EV_ABS, ev.ABS_X, round(x * 15200)),
                  (ev.EV_ABS, ev.ABS_Y, round(y * 9500)),
                  (ev.EV_ABS, ev.ABS_PRESSURE, round(p * 2047)),
                  (ev.EV_KEY, ev.BTN_STYLUS, int(lower)),
                  (ev.EV_KEY, ev.BTN_STYLUS2, int(upper)),
                  (ev.EV_SYN, ev.SYN_REPORT, 0)]
        self.engine.handle(events)
        self.clock.now += 0.005
        return self.sink.last_pen

    def test_hover_maps_full_desktop(self):
        state = self.frame(0.25, 0.75)
        self.assertTrue(state.prox)
        self.assertFalse(state.touch)
        self.assertAlmostEqual(state.u, 0.25, places=3)
        self.assertAlmostEqual(state.v, 0.75, places=3)

    def test_tip_threshold_and_floor(self):
        self.profile["tip"]["threshold"] = 0.10
        self.apply()
        self.assertFalse(self.frame(p=0.05).touch)
        state = self.frame(p=0.11)
        self.assertTrue(state.touch)
        self.assertGreaterEqual(state.pressure, ADVANCED["contact_floor"])
        self.assertAlmostEqual(self.frame(p=1.0).pressure, 1.0, places=3)
        # Hysteresis: still touching slightly below the threshold.
        self.assertTrue(self.frame(p=0.09).touch)
        self.assertFalse(self.frame(p=0.0).touch)

    def test_soft_curve_gives_more_pressure(self):
        self.frame(p=0.3)
        linear = self.sink.last_pen.pressure
        self.frame(p=0.0)
        self.profile["tip"]["curve"] = [0.0, 0.8, 0.2, 1.0]
        self.apply()
        self.frame(p=0.3)
        self.assertGreater(self.sink.last_pen.pressure, linear + 0.1)

    def test_prox_out_releases(self):
        self.frame(p=0.5)
        state = self.frame(prox=False)
        self.assertFalse(state.prox)

    def test_without_shell_pointer_moves_only_when_pen_leaves(self):
        # Without the GNOME extension the duplicate cursor cannot be hidden: the
        # pointer is only placed where the pen was when it leaves.
        self.frame(0.3, 0.6)
        self.frame(0.4, 0.6, p=0.5)
        self.assertFalse(self.sink.named("pointer_move"))
        self.frame(0.4, 0.6, prox=False)
        moves = self.sink.named("pointer_move")
        self.assertEqual(len(moves), 1)
        self.assertAlmostEqual(moves[0][1][0], 0.4, places=3)

    def test_unified_cursor_follows_hover_not_strokes(self):
        self.engine.hover_pointer = True
        self.frame(0.3, 0.6)
        self.frame(0.4, 0.6)
        moves = self.sink.named("pointer_move")
        self.assertAlmostEqual(moves[-1][1][0], 0.4, places=3)
        count = len(moves)
        self.frame(0.5, 0.6, p=0.5)  # drawing: the pointer does not move
        self.frame(0.6, 0.6, p=0.5)
        self.assertEqual(len(self.sink.named("pointer_move")), count)
        self.frame(0.6, 0.6, prox=False)  # on leaving, the pointer stays where the pen was
        self.assertAlmostEqual(self.sink.named("pointer_move")[-1][1][0], 0.6, places=3)

    def test_unified_cursor_can_be_disabled(self):
        self.engine.set_profile(self.profile, dict(ADVANCED, unified_cursor=False), 400)
        self.frame(0.3, 0.6)
        self.frame(prox=False)
        self.assertFalse(self.sink.named("pointer_move"))

    def touch_profile(self):
        self.profile["buttons"]["tip"] = {"type": "touch"}
        self.apply()

    def test_touch_tap_clicks(self):
        self.touch_profile()
        self.frame(0.5, 0.5)
        state = self.frame(0.5, 0.5, p=0.5)
        self.assertFalse(state.touch)  # not yet known whether it is a tap or a drag
        self.frame(0.5, 0.5, p=0.0)
        self.assertEqual(self.sink.named("pen_pulse")[-1], ("pen_pulse", "touch", 1))
        self.assertIn(("feedback", "tap", (0.5, 0.5)), self.sink.calls)

    def test_touch_drag_scrolls_with_momentum(self):
        self.touch_profile()
        self.frame(0.5, 0.3)
        for i in range(8):
            self.frame(0.5, 0.3 + i * 0.03, p=0.5)
        self.assertFalse(self.sink.named("pen_pulse"))
        scrolled = sum(c[1] for c in self.sink.named("scroll"))
        self.assertGreater(scrolled, 0)
        self.frame(0.5, 0.51, p=0.0)
        self.assertTrue(self.engine.needs_tick)
        before = len(self.sink.named("scroll"))
        self.engine.tick(self.clock.now + 0.02)
        self.assertGreater(len(self.sink.named("scroll")), before)

    def test_touch_hold_right_clicks(self):
        self.touch_profile()
        self.frame(0.5, 0.5)
        self.frame(0.5, 0.5, p=0.5)
        self.assertTrue(self.engine.needs_tick)
        self.engine.tick(self.clock.now + 0.7)
        self.assertEqual(self.sink.named("pen_pulse")[-1], ("pen_pulse", ev.BTN_STYLUS2, 1))
        self.frame(0.5, 0.5, p=0.0)
        self.assertEqual(len(self.sink.named("pen_pulse")), 1)  # lifting does not click

    def test_touch_toggle(self):
        self.profile["buttons"]["upper"] = {"type": "touch_toggle"}
        self.apply()
        self.frame()
        self.frame(upper=True)
        self.assertTrue(self.engine.touch_mode)
        self.frame(upper=False)
        self.frame(upper=True)
        self.assertFalse(self.engine.touch_mode)

    def test_smoothing_reduces_jitter(self):
        self.profile["smoothing"] = 0.6
        self.apply()
        xs = []
        for i in range(60):
            jitter = 0.004 if i % 2 else -0.004
            xs.append(self.frame(0.5 + jitter, 0.5).u)
        spread = max(xs[30:]) - min(xs[30:])
        self.assertLess(spread, 0.004)

    def test_radial_menu_and_trigger(self):
        self.profile["buttons"]["upper"] = {"type": "radial_menu"}
        self.apply()
        self.frame(0.2, 0.3)
        self.frame(0.2, 0.3, upper=True)
        self.assertEqual(self.sink.named("radial")[0][0], "radial")
        self.engine.trigger({"type": "copy"})
        self.assertIn(("keys", [keys.KEY_LEFTCTRL, keys.KEY_C], True), self.sink.calls)

    def test_precision_overlay(self):
        self.profile["buttons"]["lower"] = {"type": "precision_mode"}
        self.apply()
        self.frame(0.5, 0.5)
        self.frame(0.5, 0.5, lower=True)
        shown = self.sink.named("overlay")[-1]
        self.assertTrue(shown[1])
        self.assertAlmostEqual(shown[2][2], ADVANCED["precision_factor"], places=4)
        self.frame(0.5, 0.5)
        self.assertEqual(self.sink.named("overlay")[-1], ("overlay", False, None))

    def test_right_click_hover(self):
        self.frame()
        state = self.frame(lower=True)
        self.assertIn(ev.BTN_STYLUS2, state.buttons)
        state = self.frame(lower=False)
        self.assertNotIn(ev.BTN_STYLUS2, state.buttons)

    def test_click_and_tap_mode(self):
        self.profile["button_mode"] = "click_tap"
        self.apply()
        self.frame()
        state = self.frame(lower=True)
        self.assertNotIn(ev.BTN_STYLUS2, state.buttons)
        state = self.frame(lower=True, p=0.5)
        self.assertIn(ev.BTN_STYLUS2, state.buttons)
        self.assertFalse(state.touch)  # the tip does not left-click
        state = self.frame(lower=True, p=0.0)
        self.assertNotIn(ev.BTN_STYLUS2, state.buttons)

    def test_keystroke_hold(self):
        self.profile["buttons"]["upper"] = keys.combo(keys.KEY_LEFTCTRL, keys.KEY_Z) | {
            "type": "keystroke"}
        self.apply()
        self.frame()
        self.frame(upper=True)
        self.frame(upper=False)
        presses = self.sink.named("keys")
        self.assertEqual(presses[0], ("keys", [keys.KEY_LEFTCTRL, keys.KEY_Z], True))
        self.assertEqual(presses[1], ("keys", [keys.KEY_Z, keys.KEY_LEFTCTRL], False))

    def test_undo_fixed_combo(self):
        self.profile["buttons"]["lower"] = {"type": "undo"}
        self.apply()
        self.frame()
        self.frame(lower=True)
        self.assertEqual(self.sink.named("keys")[0][1], [keys.KEY_LEFTCTRL, keys.KEY_Z])

    def test_pan_scroll(self):
        self.frame(y=0.5)
        self.frame(y=0.5, upper=True)
        start = self.frame(y=0.5, upper=True, p=0.5)
        self.assertFalse(start.touch)
        moved = self.frame(y=0.6, upper=True, p=0.5)  # 9.5 mm down
        scrolls = self.sink.named("scroll")
        self.assertTrue(scrolls)
        self.assertGreater(sum(c[1] for c in scrolls), 0)  # content follows the pen
        self.assertAlmostEqual(moved.v, start.v, places=4)  # cursor stays put

    def test_click_lock(self):
        self.profile["buttons"]["lower"] = {"type": "click_lock"}
        self.apply()
        self.frame()
        self.frame(lower=True)
        self.assertTrue(self.frame(lower=False).touch)
        self.frame(lower=True)
        self.assertFalse(self.frame(lower=False).touch)

    def test_double_click_pulse(self):
        self.profile["buttons"]["lower"] = {"type": "double_click"}
        self.apply()
        self.frame()
        self.frame(lower=True)
        self.assertEqual(self.sink.named("pen_pulse")[0], ("pen_pulse", "touch", 2))

    def test_precision_mode(self):
        self.profile["buttons"]["lower"] = {"type": "precision_mode"}
        self.apply()
        self.frame(x=0.5)
        self.frame(x=0.5, lower=True)
        state = self.frame(x=0.7, lower=True)
        self.assertAlmostEqual(state.u, 0.5 + 0.2 * ADVANCED["precision_factor"], places=3)
        self.assertAlmostEqual(self.frame(x=0.7).u, 0.7, places=3)

    def test_double_click_distance_snaps(self):
        self.profile["double_click_distance"] = 3.0
        self.apply()
        self.frame(x=0.5, y=0.5, p=0.5)
        self.frame(x=0.5, y=0.5, p=0.0)
        second = self.frame(x=0.51, y=0.5, p=0.5)  # 1.5 mm apart
        self.assertAlmostEqual(second.u, 0.5, places=4)
        self.frame(x=0.5, p=0.0)
        self.clock.now += 1.0
        third = self.frame(x=0.51, y=0.5, p=0.5)  # the double-click time has passed
        self.assertAlmostEqual(third.u, 0.51, places=3)

    def test_mouse_mode_relative(self):
        self.profile["mapping"]["mode"] = "mouse"
        self.apply()
        self.frame(x=0.5)
        self.frame(x=0.6)
        moves = self.sink.named("mouse_move")
        self.assertTrue(moves)
        self.assertGreater(sum(m[1] for m in moves), 0)
        self.frame(x=0.6, p=0.5)
        self.assertIn(("mouse_button", ev.BTN_LEFT, True), self.sink.calls)

    def test_mode_toggle(self):
        self.profile["buttons"]["upper"] = {"type": "mode_toggle"}
        self.apply()
        self.frame()
        self.frame(upper=True)
        self.assertEqual(self.engine.mode, "mouse")
        self.frame(upper=False)
        self.assertFalse(self.sink.last_pen.prox)

    def test_monitor_mapping_and_display_toggle(self):
        desktop = Desktop([Monitor("eDP-1", "A", 0, 0, 1536, 864, True),
                           Monitor("HDMI-1", "B", 1536, 0, 1920, 1080)])
        self.engine.set_desktop(desktop)
        self.profile["mapping"]["screen"] = {"type": "monitor", "monitor": "HDMI-1"}
        self.profile["buttons"]["upper"] = {"type": "display_toggle"}
        self.apply()
        state = self.frame(x=0.0, y=0.0)
        self.assertAlmostEqual(state.u, 1536 / 3456, places=4)
        self.frame(upper=True, x=0.0, y=0.0)
        self.frame(upper=False, x=0.0, y=0.0)
        # After HDMI-1 the cycle goes back to "all".
        self.assertEqual(self.engine.target_override, {"type": "all"})

    def test_force_proportions_crops_tablet(self):
        desktop = Desktop([Monitor("eDP-1", "A", 0, 0, 1000, 1000, True)])
        self.engine.set_desktop(desktop)
        self.profile["mapping"]["force_proportions"] = True
        self.apply()
        # 152x95 mm tablet -> centered 95x95 square area.
        left_edge = (152 - 95) / 2 / 152
        self.assertAlmostEqual(self.frame(x=left_edge, y=0.0).u, 0.0, places=3)
        self.assertAlmostEqual(self.frame(x=0.5, y=0.5).u, 0.5, places=3)

    def test_orientation_left_handed(self):
        self.profile["mapping"]["orientation"] = 180
        self.apply()
        state = self.frame(x=0.2, y=0.3)
        self.assertAlmostEqual(state.u, 0.8, places=3)
        self.assertAlmostEqual(state.v, 0.7, places=3)

    def test_capture_tablet_area(self):
        captured = []
        self.engine.on_capture = lambda kind, rect: captured.append((kind, rect))
        self.engine.start_capture("tablet")
        self.frame(x=0.1, y=0.2)
        self.assertFalse(self.frame(x=0.1, y=0.2, p=0.5).touch)
        self.frame(x=0.1, y=0.2, p=0.0)
        self.frame(x=0.6, y=0.7, p=0.5)
        self.assertEqual(captured[0][0], "tablet")
        for got, want in zip(captured[0][1], [0.1, 0.2, 0.5, 0.5]):
            self.assertAlmostEqual(got, want, places=3)

    def test_pressure_hold(self):
        self.profile["buttons"]["lower"] = {"type": "pressure_hold"}
        self.apply()
        self.frame(p=0.4)
        held = self.frame(p=0.4, lower=True).pressure
        self.assertAlmostEqual(self.frame(p=0.9, lower=True).pressure, held, places=4)
        self.assertGreater(self.frame(p=0.9).pressure, held)


if __name__ == "__main__":
    unittest.main()
