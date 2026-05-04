/**
 * Ambient typings for the dashboard `widget` runtime, scoped to the calls
 * this Knowledge Explorer makes. Mirrors apps/widget/src/widget.d.ts
 * intentionally — keep them in sync if the upstream surface grows.
 */

declare module "@widget-lab/3ddashboard-utils" {
  /** Disable the dashboard's default UWA stylesheet. */
  export function disableDefaultCSS(disabled: boolean): void;

  /** Dashboard widget runtime API. Only the subset used by this widget. */
  export const widget: {
    setTitle(title: string): void;
    addEvent(event: "onLoad", handler: () => void): void;
    /** Persisted-per-tile string store. Returns null if the key is unset. */
    getValue(key: string): string | null;
    setValue(key: string, value: string): void;
    /** Absolute URL of this widget's index.html, injected by the host. */
    uwaUrl?: string;
  };
}
