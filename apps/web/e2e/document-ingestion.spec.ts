import { test, expect } from "@playwright/test";

test.describe.skip("Orbital document ingestion flow", () => {
  test("uploads a document and shows hash, status, and catalog entry", async ({ page }) => {
    await page.goto("/documents/upload");
    await page.getByLabel("Document").setInputFiles({
      name: "policy.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Policy title\nReview required"),
    });
    await page.getByRole("button", { name: /upload/i }).click();

    await expect(page.getByText("policy.txt")).toBeVisible();
    await expect(page.getByText(/STORED|HASHED|UPLOADED/)).toBeVisible();
    await expect(page.getByText(/[a-f0-9]{64}/)).toBeVisible();
  });

  test("shows duplicate/version indicator for repeated binary upload", async ({ page }) => {
    await page.goto("/documents/upload");
    await page.getByLabel("Document").setInputFiles({
      name: "first.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("same bytes"),
    });
    await page.getByRole("button", { name: /upload/i }).click();

    await page.goto("/documents/upload");
    await page.getByLabel("Document").setInputFiles({
      name: "second-name.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("same bytes"),
    });
    await page.getByRole("button", { name: /upload/i }).click();

    await expect(page.getByText(/duplicate/i)).toBeVisible();
  });

  test("previews semantic Markdown and source lineage after extraction", async ({ page }) => {
    await page.goto("/documents/catalog");
    await page.getByRole("link", { name: /policy.txt/i }).click();
    await page.getByRole("button", { name: /extract/i }).click();
    await page.getByRole("link", { name: /markdown preview/i }).click();

    await expect(page.getByRole("heading", { name: /source lineage/i })).toBeVisible();
    await expect(page.getByText(/needs_review/i)).toBeVisible();
  });
});

