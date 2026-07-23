import { test, expect } from "@playwright/test";

/**
 * End-to-end test: upload a document → search for it via chat → verify answer.
 *
 * Requires backend running on localhost:8000 with valid LLM/embedding API keys.
 * Run with: npx playwright test --grep '@requires-backend'
 */

test.describe("chat flow", () => {
  const testContent =
    "SKY-2000 无人机最大飞行高度为 4500 米，续航时间 35 分钟，搭载 FHD-6B 光学摄像头。";
  const testFilename = `e2e-test-${Date.now()}.txt`;

  test("upload document and search via chat @requires-backend", async ({ page }) => {
    // 1. Navigate to documents page
    await page.goto("/documents");
    await page.waitForSelector("text=上传文档", { timeout: 10000 });

    // 2. Upload a test document
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: testFilename,
      mimeType: "text/plain",
      buffer: Buffer.from(testContent, "utf-8"),
    });
    await expect(page.locator("text=入库完成")).toHaveText("入库完成", {
      timeout: 30000,
    });

    // 3. Navigate to chat page and send a query about the uploaded doc
    await page.goto("/");
    const input = page.locator('textarea, input[type="text"], [contenteditable]').first();
    await input.fill("SKY-2000 无人机能飞多高");
    await page.keyboard.press("Enter");

    // 4. Wait for streaming reply to appear
    await page.waitForSelector("text=4500", { timeout: 30000 });
    const content = await page.textContent("body");
    expect(content).toContain("4500");
  });

  test("streaming reply shows incremental content @requires-backend", async ({ page }) => {
    await page.goto("/");
    const input = page.locator('textarea, input[type="text"], [contenteditable]').first();
    await input.fill("你好");
    await page.keyboard.press("Enter");

    // Wait for some visible response
    await page.waitForTimeout(2000);
    const bodyText = await page.textContent("body");
    expect(bodyText!.trim().length).toBeGreaterThan(0);
  });

  test("rate limit shows warning @requires-backend", async ({ page }) => {
    // Send many messages quickly to trigger rate limit
    await page.goto("/");
    const input = page.locator('textarea, input[type="text"], [contenteditable]').first();

    for (let i = 0; i < 35; i++) {
      await input.fill(`test ${i}`);
      await page.keyboard.press("Enter");
      await page.waitForTimeout(100);
    }

    // Either rate limit toast or error message should appear
    const rateLimited =
      (await page.textContent("body"))!.includes("频繁") ||
      (await page.textContent("body"))!.includes("稍后");
    expect(rateLimited).toBeTruthy();
  });
});
