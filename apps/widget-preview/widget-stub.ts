/**
 * Stand-in for `@widget-lab/3ddashboard-utils`. The real package only
 * exists inside the 3DEXPERIENCE host runtime; for the standalone
 * browser preview we provide a no-op shim that satisfies the same
 * import surface the widget uses (`widget.getValue`, `widget.setValue`,
 * `widget.setTitle`, `widget.addEvent`, `disableDefaultCSS`).
 *
 * `addEvent("onLoad", cb)` immediately invokes `cb` so the React tree
 * mounts at boot — same effect as the dashboard firing its lifecycle
 * hook.
 */

type ValueStore = Record<string, string>;
const VALUES: ValueStore = {};

export const widget = {
  getValue(key: string): string | null {
    return Object.prototype.hasOwnProperty.call(VALUES, key) ? VALUES[key] : null;
  },
  setValue(key: string, value: string): void {
    VALUES[key] = value;
  },
  setTitle(_title: string): void {
    /* no-op outside the dashboard host */
  },
  addEvent(event: string, callback: () => void): void {
    if (event === "onLoad") {
      // Fire on next tick so the host React tree has time to mount.
      setTimeout(callback, 0);
    }
  },
};

export function disableDefaultCSS(_disabled: boolean): void {
  /* no-op outside the dashboard host */
}
