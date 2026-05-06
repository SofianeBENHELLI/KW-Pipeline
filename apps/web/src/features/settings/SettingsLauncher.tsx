/**
 * Tiny floating-button launcher for ``SettingsModal``.
 *
 * Extracted into its own module so ``SettingsModal`` (which pulls in
 * the shared DemoToggle and the admin-config fetch path) can be
 * lazy-loaded without dragging the launcher onto the initial-chunk
 * critical path. App.tsx imports the launcher eagerly and the modal
 * via ``React.lazy``; the modal chunk is only fetched the first time
 * the user clicks the gear.
 */

export function SettingsLauncher({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Open settings"
      data-testid="settings-launcher"
      style={{
        position: "fixed",
        top: 12,
        right: 12,
        zIndex: 999,
        width: 32,
        height: 32,
        borderRadius: "50%",
        border: "1px solid #C8CDD4",
        background: "white",
        boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
        cursor: "pointer",
        fontSize: 14,
      }}
      title="Settings"
    >
      ⚙
    </button>
  );
}
