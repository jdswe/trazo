// Decides which device owns the single visible cursor, like Windows and
// macOS: the last device used on purpose. A Wacom pen near the tablet reports
// about 133 times a second even when nobody holds it, so pen reports alone
// never take the cursor from the mouse: the pen has to really move, or be
// tapped or clicked. Pure logic (no GNOME imports) so it can be unit-tested.

/** Pen travel, in stage pixels, that counts as using the pen on purpose. */
export const RECLAIM_DISTANCE = 30;

/** Minimum time between "the mouse is in use" notifications to Trazo. */
export const NOTIFY_INTERVAL_MS = 250;

export class CursorOwnership {
    constructor() {
        this.owner = 'mouse';
        this.penInRange = false;
        this._anchor = null;
        this._penSeen = false;
        this._lastNotify = -Infinity;
    }

    /** Whether the mouse cursor must be hidden (the pen shows its own). */
    get hideMouseCursor() {
        return this.owner === 'pen' && this.penInRange;
    }

    /**
     * Records a pen event.
     *
     * @param {object} event - what the pen did
     * @param {boolean} [event.leaving] - the pen left proximity
     * @param {number} [event.x] - stage x of the pen
     * @param {number} [event.y] - stage y of the pen
     * @param {boolean} [event.pressed] - the tip touched or a pen button was pressed
     */
    penEvent({leaving = false, x = 0, y = 0, pressed = false} = {}) {
        if (leaving) {
            this.penInRange = false;
            this._anchor = null;
            return;
        }
        this.penInRange = true;
        this._penSeen = true;
        if (this.owner === 'pen')
            return;
        if (!this._anchor)
            this._anchor = [x, y];
        const moved = Math.hypot(x - this._anchor[0], y - this._anchor[1]) > RECLAIM_DISTANCE;
        if (pressed || moved) {
            this.owner = 'pen';
            this._anchor = null;
        }
    }

    /**
     * Records that the real mouse (or touchpad) was used: it takes the cursor.
     *
     * @param {number} nowMs - current time in milliseconds
     * @returns {boolean} whether Trazo must be told, so it stops making the
     *   pen claim the pointer (only when the pen reported since the last
     *   notification, and at most every NOTIFY_INTERVAL_MS)
     */
    mouseEvent(nowMs) {
        this.owner = 'mouse';
        this._anchor = null;
        if (!this._penSeen || nowMs - this._lastNotify < NOTIFY_INTERVAL_MS)
            return false;
        this._penSeen = false;
        this._lastNotify = nowMs;
        return true;
    }
}
