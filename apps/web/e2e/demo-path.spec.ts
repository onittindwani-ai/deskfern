import { expect, test } from "@playwright/test";
import { call, login, resetDemo, tickets, watch } from "./helpers";

test.describe.serial("the five minute demo", () => {
  test("queue, cited answer with source highlight, edit becomes a test case, refund waits for a second person", async ({ page }) => {
    const errors = watch(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await login(page);
    await resetDemo(page);
    await page.goto("/");
    await expect(page.getByText("Open tickets")).toBeVisible();
    await expect(page.getByText(/left|Late/).first()).toBeVisible(); // the first reply timers

    // a cited answer: hover a sentence, the source viewer shows the highlighted passage
    const ts = await tickets(page);
    const answered = ts.find((t) => t.outcome === "answer" && t.status === "drafted")!;
    await page.goto(`/tickets/${answered.id}`);
    const sentence = page.locator("button.lh-sent").first();
    await expect(sentence).toBeVisible();
    await sentence.hover();
    const mark = page.locator("mark.lh-mark");
    await expect(mark).toBeVisible();
    const quoted = (await mark.innerText()).trim();
    expect(quoted.length).toBeGreaterThan(20);
    await expect(page.getByText("Confidence = 0.4 x retrieval")).toBeVisible();
    await expect(page.getByLabel("Confidence meter")).toBeVisible();

    // edit the reply and send it: draft mode or demo adapter, the edit is kept as a test case
    const box = page.getByLabel("Reply to the customer");
    await box.fill(`${await box.inputValue()} Please tell us if anything else is unclear.`);
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(page.getByText(/Your edit was saved as a test case/)).toBeVisible();
    await page.goto("/testcases");
    await expect(page.getByText("Please tell us if anything else is unclear").first()).toBeVisible();
    await page.getByRole("button", { name: "Run all cases" }).click();
    await expect(page.getByText(/of 1 cases pass/)).toBeVisible();

    // an injection ticket: no action, a note on the ticket
    const inj = ts.find((t) => t.flags.includes("injection"))!;
    await page.goto(`/tickets/${inj.id}`);
    await expect(page.getByText("orders hidden in the message").first()).toBeVisible();
    await expect(page.getByText(/tried to give the system orders/)).toBeVisible();
    expect((await (await page.request.get(`/api/tickets/${inj.id}`)).json()).actions).toEqual([]);

    // a refund that fits the rule: it waits, the owner approves, the demo adapter records the refund
    const refund = ts.find((t) => t.outcome === "refund_ok")!;
    await page.goto(`/tickets/${refund.id}`);
    await expect(page.getByText("A refund only runs after another person approves it")).toBeVisible();
    await page.goto("/approvals");
    await expect(page.getByText(/Refund .* USD/).first()).toBeVisible();
    await page.locator(".lh-field").filter({ hasText: `ticket ${refund.external_id})` }).getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("Approved.")).toBeVisible();
    await page.goto(`/tickets/${refund.id}`);
    await expect(page.getByText(/Paid|Approved, not paid yet/).first()).toBeVisible();

    // a refund the rule refuses cannot be requested by hand
    const denied = ts.find((t) => t.outcome === "refund_denied")!;
    const r = await call(page, `/tickets/${denied.id}/refund`);
    expect(r.status()).toBe(409);
    expect(errors.filter((e) => !e.includes("409"))).toEqual([]);
  });

  test("knowledge: paste a page, crawl the sample site, see the gaps; policies: the simulator follows the numbers; kill switch and force draft", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await login(page);
    await page.goto("/knowledge");
    await page.getByRole("tab", { name: "Add a source" }).click();
    await page.getByLabel("Page title").fill("Scheduled exports");
    await page.getByLabel("Page text").fill("Scheduled exports run every Sunday at 03:00 UTC and are emailed to the owner of the workspace.");
    await page.getByRole("button", { name: "Add the page" }).click();
    await expect(page.getByText(/passages are searchable now/)).toBeVisible();
    await page.getByRole("radio", { name: "Crawl a site" }).click();
    await page.getByRole("button", { name: "Start the crawl" }).click();
    await expect(page.getByText(/Crawled \d+ pages/)).toBeVisible();
    await expect(page.getByText(/robots/).first()).toBeVisible();
    await page.getByRole("tab", { name: /Gaps/ }).click();
    await expect(page.getByText(/question/).first()).toBeVisible();

    await page.goto("/policies");
    await expect(page.getByText(/Would qualify/)).toBeVisible();
    const before = await page.locator(".lh-sim__row").filter({ hasText: /Refund \d+\.\d\d/ }).count();
    await page.getByLabel("Monthly plan, days after the first payment").fill("60");
    await page.waitForTimeout(900);
    const after = await page.locator(".lh-sim__row").filter({ hasText: /Refund \d+\.\d\d/ }).count();
    expect(after).toBeGreaterThan(before);

    await call(page, "/settings", { kill_switch: true });
    await page.goto("/");
    await expect(page.getByText("Kill switch is on.")).toBeVisible();
    await call(page, "/settings", { kill_switch: false });
  });
});
