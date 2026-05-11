import { useEffect, useState } from "react";

import { CatalogScreen } from "./CatalogScreen";
import { Workspace } from "./Workspace";

import "./tokens.css";
import "./styles.css";

/**
 * Top-level Orbital entry mounted at `/orb`.
 *
 * Two screens, exactly matching the mockup's two artboards:
 *   - no document selected → `CatalogScreen` (BannersAndCatalog artboard)
 *   - document selected    → `Workspace` (ReviewWorkspaceA artboard)
 *
 * The `?document=<id>` query param deep-links straight to the workspace
 * for Forge's "open in Orbital" handoff.
 */
export function OrbitalApp() {
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const params = new URLSearchParams(window.location.search);
    return params.get("document");
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (selectedId) {
      const url = new URL(window.location.href);
      url.searchParams.set("document", selectedId);
      window.history.replaceState(null, "", url.toString());
    } else {
      const url = new URL(window.location.href);
      url.searchParams.delete("document");
      window.history.replaceState(null, "", url.toString());
    }
  }, [selectedId]);

  if (selectedId) {
    return <Workspace initialDocumentId={selectedId} onBackToCatalog={() => setSelectedId(null)} />;
  }
  return <CatalogScreen onOpenDocument={setSelectedId} />;
}
