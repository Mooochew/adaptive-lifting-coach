import { expect, test as base, type Page } from "@playwright/test";

/**
 * Every test fails if the page throws or logs a console error, unless the test
 * expects it (`test.use({ allowedErrors: [...] })`, e.g. requests to a backend that's down).
 */
export const test = base.extend<{ allowedErrors: RegExp[]; consoleGuard: void }>({
  allowedErrors: [[], { option: true }],
  consoleGuard: [
    async ({ page, allowedErrors }, use) => {
      const errors: string[] = [];
      page.on("pageerror", (err) => errors.push(`Uncaught: ${err.message}`));
      page.on("console", (msg) => {
        if (msg.type() !== "error") return;
        const text = msg.text();
        if (!allowedErrors.some((re) => re.test(text))) errors.push(`console.error: ${text}`);
      });
      await use();
      expect(errors, "page errors").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** Fresh visit (Playwright gives every test empty storage) and load the demo athlete. */
export async function loadDemo(page: Page) {
  await page.goto("/");
  await expect(page.getByText("Backend connected")).toBeVisible();
  await page.getByRole("button", { name: "Load demo athlete" }).click();
  await waitForBoard(page);
}

/** Board shown and the backend's metrics back (the estimated-max strip needs them). */
export async function waitForBoard(page: Page) {
  await expect(page.getByRole("heading", { name: "The Board" })).toBeVisible();
  await expect(page.getByText("Estimated max", { exact: true })).toBeVisible();
}

export async function openDataMenu(page: Page) {
  await page.getByLabel("Data menu").click();
}

/** Lifter data as saved in the browser, minus the save timestamp. */
export async function savedData(page: Page) {
  const text = await page.evaluate(() => localStorage.getItem("alc:data"));
  if (!text) return null;
  const data = JSON.parse(text);
  delete data.savedAt;
  return data;
}
