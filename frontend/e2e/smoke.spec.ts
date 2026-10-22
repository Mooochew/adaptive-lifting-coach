import { readFile } from "node:fs/promises";
import { API_URL } from "./env";
import { expect, loadDemo, openDataMenu, savedData, test, waitForBoard } from "./fixtures";

test("demo: log a set and it survives a reload", async ({ page }) => {
  await loadDemo(page);

  // The first session nobody has started yet (tile label: "Week 3 Lower A, Planned. Open session.").
  const tile = page.getByRole("button", { name: /, Planned\. Open session\.$/ }).first();
  const session = (await tile.getAttribute("aria-label"))!.split(", Planned")[0];
  await tile.click();
  await expect(page.getByRole("heading", { name: session.replace(/^Week \d+ /, "") })).toBeVisible();
  await expect(page.getByRole("group", { name: /^Sets done: 0 \// })).toBeVisible();

  // First exercise is open; its weight is prefilled with the target load.
  await expect(page.getByLabel("Set 1 weight", { exact: true })).not.toHaveValue("");
  await page.getByLabel("Set 1 reps", { exact: true }).fill("5");
  await page.getByRole("button", { name: "Save set 1 as done" }).click();
  await expect(page.getByRole("button", { name: "Mark set 1 not done" })).toBeVisible();
  await expect(page.getByRole("group", { name: /^Sets done: 1 \// })).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: `${session}, In progress` }).click();
  await expect(page.getByRole("button", { name: "Mark set 1 not done" })).toBeVisible();
  await expect(page.getByLabel("Set 1 reps", { exact: true })).toHaveValue("5");
});

test("onboarding: a new lifter gets a program", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Build my program" }).click();
  await expect(page.getByRole("heading", { name: "Build your program" })).toBeVisible();

  await page.getByText("Strength", { exact: true }).click();
  await page.getByLabel("Days per week").selectOption("3");
  await page.getByLabel("Bench press weight in lb").fill("185");
  await page.getByLabel("Bench press reps").fill("5");
  await page.getByLabel(/Training background/).fill("3 years lifting, recover well");
  await page.getByRole("button", { name: "Build my program" }).click();

  // The (scripted, e2e) AI coach reviewed the standard plan and its review is shown, labelled.
  const coach = page.getByRole("region", { name: "Your coach's adjustments" });
  await expect(coach).toContainText("This plan already fits you well");
  await expect(coach).toContainText("AI-written");

  const weekOne = page.getByRole("button", { name: /^Week 1 .*Open session\.$/ });
  await expect(page.getByRole("heading", { name: "The Board" })).toBeVisible();
  await expect(page.getByText("Block 1 · Strength · 3 days · lb")).toBeVisible();
  await expect(weekOne).toHaveCount(3);

  // The program survives a reload, and the recent set drives the starting weight:
  // bench (185 x 5 entered) is prefilled; squat (left blank) is picked on the day.
  await page.reload();
  await weekOne.first().click();
  await expect(page.getByLabel("Set 1 weight", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: /Barbell Bench Press/ }).click();
  await expect(page.getByLabel("Set 1 weight", { exact: true })).toHaveValue("185");
});

test("onboarding: bodyweight only asks for equipment instead of failing", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Build my program" }).click();
  await page.getByRole("button", { name: "Full gym (everything)" }).click();       // turns everything off

  await expect(page.getByRole("button", { name: "Build my program" })).toBeDisabled();
  await expect(page.getByRole("status").filter({ hasText: "Pick at least one piece of equipment" })).toBeVisible();

  await page.getByRole("button", { name: "Dumbbells" }).click();
  await expect(page.getByRole("button", { name: "Build my program" })).toBeEnabled();
});

test("progress panel shows volume and estimated maxes", async ({ page }) => {
  await loadDemo(page);
  await page.getByRole("button", { name: "Progress", exact: true }).click();

  const panel = page.getByRole("complementary", { name: "Progress" });
  await expect(panel.getByRole("heading", { name: /^Hard sets · week \d+$/ })).toBeVisible();
  // The demo athlete benches every week, so chest has real (non-zero) volume.
  const muscles = panel.getByRole("list", { name: "Hard sets per muscle" });
  await expect(muscles.getByRole("listitem", { name: /^chest: [1-9][\d.]* hard sets$/})).toBeVisible();

  // Estimated maxes: real numbers, not just an empty table.
  await expect(panel.getByRole("heading", { name: "Main lifts · estimated max" })).toBeVisible();
  const table = panel.getByRole("table");
  await expect(table.getByRole("rowheader").first()).toBeVisible();
  await expect(table.getByRole("cell").filter({ hasText: /^[1-9]\d*$/ }).first()).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
});

test.describe("backend down", () => {
  // The blocked requests show up as console errors; that's the point of this test.
  test.use({ allowedErrors: [/Failed to load resource/] });

  test("shows a clear message instead of crashing", async ({ page }) => {
    await page.route(`${API_URL}/**`, (route) => route.abort());
    await page.goto("/");

    await expect(page.getByText("Backend not reachable")).toBeVisible();
    await page.getByRole("button", { name: "Load demo athlete" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Can't reach the backend" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "The Board" })).toHaveCount(0);
  });
});

test("export, reset and import round-trips all data", async ({ page }) => {
  await loadDemo(page);
  const before = await savedData(page);
  expect(before?.logs.length).toBeGreaterThan(0);

  await openDataMenu(page);
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Export data (.json)" }).click(),
  ]);
  const file = await download.path();

  page.once("dialog", (dialog) => dialog.accept());
  await openDataMenu(page);
  await page.getByRole("button", { name: "Reset everything" }).click();
  await expect(page.getByRole("button", { name: "Load demo athlete" })).toBeVisible();
  expect(await savedData(page)).toBeNull();

  await openDataMenu(page);
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByRole("button", { name: "Import data…" }).click(),
  ]);
  await chooser.setFiles(file);

  await waitForBoard(page);
  expect(await savedData(page)).toEqual(before);
  expect(JSON.parse(await readFile(file, "utf-8")).logs).toHaveLength(before.logs.length);
});
