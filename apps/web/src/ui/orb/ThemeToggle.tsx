import { Btn } from "./atoms";
import { Icon } from "./Icon";
import { useOrbTheme } from "./useTheme";

/**
 * Compact theme toggle for the app shell upper-right. Light ↔ dark only;
 * density lives in the Settings modal (Phase 7) rather than the chrome.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useOrbTheme();
  const label = theme === "light" ? "Switch to dark theme" : "Switch to light theme";
  return (
    <Btn
      kind="ghost"
      size="sm"
      iconOnly
      icon={<Icon name="spark" />}
      onClick={toggleTheme}
      aria-label={label}
      title={label}
      className={className}
    />
  );
}
