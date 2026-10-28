// Unit tests for the extension's cursor-ownership rule. Run with:
//   gjs -m tests/js/ownership_test.js
// (tests/test_extension.py runs it as part of the Python suite.)

import {CursorOwnership, NOTIFY_INTERVAL_MS, RECLAIM_DISTANCE} from
    '../../extension/trazo@jdswe.github.io/ownership.js';

let failures = 0;

function check(name, condition) {
    if (condition) {
        print(`ok - ${name}`);
    } else {
        failures += 1;
        print(`FAIL - ${name}`);
    }
}

function restingPen(ownership, frames = 50, x = 500, y = 400) {
    // A pen lying on the tablet: in proximity, jittering by a pixel.
    for (let i = 0; i < frames; i++)
        ownership.penEvent({x: x + (i % 2 ? 1 : -1), y});
}

{
    // Regression: every pen report used to hide the mouse cursor again,
    // milliseconds after the mouse showed it, so the mouse looked dead.
    const o = new CursorOwnership();
    o.penEvent({x: 100, y: 100});
    o.penEvent({x: 300, y: 100});  // the pen was used
    check('pen in use hides the mouse cursor', o.hideMouseCursor);
    o.mouseEvent(1000);
    restingPen(o);
    check('resting pen does not take the cursor back from the mouse', !o.hideMouseCursor);
}

{
    const o = new CursorOwnership();
    restingPen(o);
    check('a pen that was never moved does not hide the mouse cursor', !o.hideMouseCursor);
}

{
    const o = new CursorOwnership();
    o.mouseEvent(0);
    o.penEvent({x: 500, y: 400});
    o.penEvent({x: 500 + RECLAIM_DISTANCE + 5, y: 400});
    check('moving the pen on purpose takes the cursor back', o.hideMouseCursor);
}

{
    const o = new CursorOwnership();
    o.mouseEvent(0);
    o.penEvent({x: 500, y: 400});
    o.penEvent({x: 501, y: 400, pressed: true});
    check('a tap or pen button takes the cursor back', o.hideMouseCursor);
}

{
    const o = new CursorOwnership();
    o.penEvent({x: 100, y: 100});
    o.penEvent({x: 300, y: 100});
    o.penEvent({leaving: true});
    check('the mouse cursor shows again when the pen leaves', !o.hideMouseCursor);
    o.penEvent({x: 300, y: 100});
    check('a pen that owned the cursor hides it again when it comes back', o.hideMouseCursor);
}

{
    const o = new CursorOwnership();
    check('mouse use without pen reports does not notify Trazo', !o.mouseEvent(0));
    restingPen(o, 2);
    check('mouse use after pen reports notifies Trazo', o.mouseEvent(1000));
    restingPen(o, 2);
    check('notifications are rate-limited',
        !o.mouseEvent(1000 + NOTIFY_INTERVAL_MS - 1) && o.mouseEvent(1000 + NOTIFY_INTERVAL_MS));
}

if (failures > 0) {
    print(`${failures} failure(s)`);
    imports.system.exit(1);
}
print('all ownership tests passed');
