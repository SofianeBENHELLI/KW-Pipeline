/**
 * Banner tests — pin the visibility rules + dismiss behaviour.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BannerStack,
  DeepLinkErrorBanner,
  ForceAutoBanner,
} from "./Banners";

describe("<ForceAutoBanner />", () => {
  it("renders the warn copy with optional flagged count", () => {
    render(<ForceAutoBanner flaggedCount={3} />);
    expect(screen.getByTestId("kf-banner-force-auto")).toBeInTheDocument();
    expect(screen.getByText(/3 docs flagged/i)).toBeInTheDocument();
  });

  it("returns null when hidden=true", () => {
    const { container } = render(<ForceAutoBanner hidden />);
    expect(container.firstChild).toBeNull();
  });
});

describe("<DeepLinkErrorBanner />", () => {
  it("renders the failed id + dismiss button", () => {
    let dismissed = false;
    render(
      <DeepLinkErrorBanner
        documentId="doc-missing"
        onDismiss={() => {
          dismissed = true;
        }}
      />,
    );
    expect(screen.getByText("doc-missing")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Dismiss deep link error"));
    expect(dismissed).toBe(true);
  });
});

describe("<BannerStack />", () => {
  it("returns null when no banner is active", () => {
    const { container } = render(
      <BannerStack forceAutoOn={false} deepLinkErrorId={null} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the deep-link banner and lets the user dismiss it", () => {
    render(
      <BannerStack forceAutoOn={false} deepLinkErrorId="doc-x" />,
    );
    expect(screen.getByTestId("kf-banner-deep-link")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Dismiss deep link error"));
    expect(screen.queryByTestId("kf-banner-deep-link")).toBeNull();
  });

  it("stacks both banners when both flags are on", () => {
    render(
      <BannerStack
        forceAutoOn
        forceAutoFlaggedCount={2}
        deepLinkErrorId="doc-x"
      />,
    );
    expect(screen.getByTestId("kf-banner-force-auto")).toBeInTheDocument();
    expect(screen.getByTestId("kf-banner-deep-link")).toBeInTheDocument();
  });
});
