/**
 * Ambient typings for the dashboard `widget` runtime, scoped to the calls
 * this project actually makes. The full surface lives in the upstream
 * `@widget-lab/3ddashboard-utils` package; this declaration just sharpens
 * the parts we depend on so TypeScript stops complaining at the call sites.
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
