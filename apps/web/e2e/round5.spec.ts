import { expect, test, type Browser, type Page } from "@playwright/test";
import { call, login, resetDemo, tickets, watch } from "./helpers";

/** Round 5: defects found by walking every screen as a first time owner. One test per fix. */

const BASE = `http://localhost:${process.env.WEB_PORT ?? "3000"}`;
const toasts = (page: Page) => page.locator("[data-sonner-toast]");
const settle = async (page: Page) => { await page.waitForLoadState("networkidle"); await page.waitForTimeout(500); };
async function as(browser: Browser, email: string, viewport = { width: 1440, height: 900 }) {
  const ctx = await browser.newContext({ baseURL: BASE, viewport });
  const page = await ctx.newPage();
  await login(page, email);
  return { ctx, page };
}

test.describe.serial("round 5: the second walk", () => {
  let page: Page;
  test.beforeAll(async ({ browser }) => {
    page = await (await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 } })).newPage();
    await login(page);
    await resetDemo(page);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("the demo bar (Run now, +1 day) is there in draft mode, which is how the demo starts", async () => {
    await call(page, "/settings", { force_draft: true });
    await page.goto("/"); await settle(page);
    await expect(page.getByText("Draft mode.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Run now" })).toBeVisible();
    const before = (await (await page.request.get("/api/demo/state")).json()).now;
    await page.getByRole("button", { name: "+1 day" }).click();
    await expect(toasts(page).filter({ hasText: "Moved the demo clock forward" })).toBeVisible();
    expect((await (await page.request.get("/api/demo/state")).json()).now).not.toBe(before);
    await call(page, "/settings", { force_draft: false });
  });

  test("a page asked for before signing in is the page you land on after signing in", async ({ browser }) => {
    const ctx = await browser.newContext({ baseURL: BASE });
    const p = await ctx.newPage();
    await p.goto("/approvals");
    await expect(p).toHaveURL(/\/login\?next=%2Fapprovals/);
    await p.getByLabel("Email").fill("owner@example.test");
    await p.getByLabel("Password").fill("demo-password-1");
    await p.getByRole("button", { name: "Sign in" }).click();
    await expect(p.getByRole("heading", { name: "Approvals" })).toBeVisible();
    expect(new URL(p.url()).pathname).toBe("/approvals");
    // an address on another site is ignored
    await p.context().clearCookies();
    await p.goto("/login?next=//evil.example.test");
    await p.getByLabel("Email").fill("owner@example.test");
    await p.getByLabel("Password").fill("demo-password-1");
    await p.getByRole("button", { name: "Sign in" }).click();
    await expect(p.getByRole("heading", { name: "Queue" })).toBeVisible();
    await ctx.close();
  });

  test("on a phone Pause all and Sign out are in the top bar, and the kill switch button says what it does", async ({ browser }) => {
    const { ctx, page: p } = await as(browser, "owner@example.test", { width: 390, height: 844 });
    await expect(p.getByRole("button", { name: "Pause all" })).toBeVisible();
    await expect(p.getByRole("button", { name: "Sign out" })).toBeVisible();
    await p.getByRole("button", { name: "Pause all" }).click();
    await p.getByRole("dialog").getByRole("button", { name: "Pause all" }).click();
    await expect(p.getByText("Kill switch is on.")).toBeVisible();
    await expect(p.getByRole("button", { name: "Turn it off (kill switch)" })).toBeVisible();
    await expect(p.getByRole("button", { name: "Pause all", exact: true })).toHaveCount(0);
    await p.keyboard.press("Meta+k");
    await expect(p.getByRole("option", { name: /Turn the kill switch off/ })).toBeVisible();
    await expect(p.getByRole("option", { name: /Pause everything/ })).toHaveCount(0);
    await p.keyboard.press("Escape");
    await p.getByRole("button", { name: "Turn it off (kill switch)" }).click();
    await expect(p.getByText("Kill switch is on.")).toHaveCount(0);
    await p.getByRole("button", { name: "Sign out" }).click();
    await expect(p).toHaveURL(/\/login/);
    await ctx.close();
  });

  test("G then a number goes to the page the palette lists, and typing a g in a field does not", async () => {
    await page.goto("/"); await settle(page);
    await page.keyboard.press("g"); await page.keyboard.press("3");
    await expect(page).toHaveURL(/\/policies$/);
    await page.keyboard.press("g"); await page.keyboard.press("8");
    await expect(page).toHaveURL(/\/approvals$/);
    await page.goto("/"); await settle(page);
    await page.getByRole("textbox", { name: "Search tickets" }).fill("");
    await page.getByRole("textbox", { name: "Search tickets" }).focus();
    await page.keyboard.type("g3");
    await expect(page).toHaveURL(`${BASE}/`);
  });

  test("typing reset in the palette puts the reset first, not the dark theme", async () => {
    await page.goto("/"); await settle(page);
    await page.keyboard.press("Meta+k");
    await page.keyboard.type("reset");
    await expect(page.getByRole("option").first()).toContainText("Reset the demo data");
    await page.keyboard.press("Escape");
  });

  test("a viewer reads everything: write buttons are off with a reason, and no page answers 403", async ({ browser }) => {
    const { ctx, page: v } = await as(browser, "viewer@example.test");
    const errors = watch(v);
    const ts = await tickets(v);
    const t = ts.find((x) => x.outcome === "refund_ok")!;
    await v.goto(`/tickets/${t.id}`); await settle(v);
    for (const n of ["Send reply", "Draft again", "Ask for a refund", "Hand to a person"]) await expect(v.getByRole("button", { name: n })).toBeDisabled();
    await v.goto("/approvals"); await settle(v);
    await expect(v.getByRole("button", { name: "Approve" }).first()).toBeDisabled();
    await expect(v.getByRole("button", { name: "Reject" }).first()).toBeDisabled();
    await v.goto("/policies"); await settle(v);
    await expect(v.getByRole("button", { name: "Save the rule" })).toBeDisabled();
    await expect(v.getByText("Would qualify")).toBeVisible(); // the simulator works for a viewer
    await v.goto("/testcases"); await settle(v);
    await expect(v.getByRole("button", { name: "Run all cases" })).toBeDisabled();
    await v.goto("/health"); await settle(v);
    await expect(v.getByRole("button", { name: "Recompute" })).toBeDisabled();
    await v.goto("/integrations"); await settle(v);
    await expect(v.getByRole("button", { name: "Test the one in use" }).first()).toBeDisabled();
    await v.goto("/knowledge"); await settle(v);
    await v.getByRole("tab", { name: /Gaps/ }).click();
    await expect(v.getByRole("button", { name: "Covered now" }).first()).toBeDisabled();
    await v.goto("/"); await settle(v);
    await expect(v.getByRole("button", { name: "Run now" })).toBeDisabled();
    await v.keyboard.press("Meta+k");
    await expect(v.getByRole("option", { name: /Run every job now/ })).toHaveCount(0);
    expect(errors.filter((e) => !e.includes("ERR_ABORTED"))).toEqual([]);
    await ctx.close();
  });

  test("a ticket opens by tapping its name, not only by double click or Enter", async () => {
    await page.goto("/"); await settle(page);
    await page.getByRole("link", { name: "Phone support" }).click();
    await expect(page).toHaveURL(/\/tickets\/\d+$/);
    await expect(page.getByRole("heading", { name: "Phone support" })).toBeVisible();
  });

  test("a refused request shows no success tick on the button", async () => {
    const ts = await tickets(page);
    const denied = ts.find((t) => t.outcome === "refund_denied")!;
    await page.goto(`/tickets/${denied.id}`); await settle(page);
    const btn = page.getByRole("button", { name: "Ask for a refund" });
    await btn.click();
    await expect(toasts(page).filter({ hasText: "The refund rule says no" })).toBeVisible();
    await page.waitForTimeout(900);
    await expect(btn).not.toHaveAttribute("data-state", "success");
    // the same for a crawl that is refused
    await page.goto("/knowledge"); await settle(page);
    await page.getByRole("tab", { name: "Add a source" }).click();
    await page.getByRole("radio", { name: "Crawl a site" }).click();
    await page.getByLabel("Site address").fill("http://127.0.0.1:8000/admin");
    const crawl = page.getByRole("button", { name: "Start the crawl" });
    await crawl.click();
    await expect(toasts(page).filter({ hasText: "The crawl did not run" })).toBeVisible();
    await page.waitForTimeout(900);
    await expect(crawl).not.toHaveAttribute("data-state", "success");
  });

  test("a ticket that does not exist says so plainly, not 'Could not load'", async () => {
    await page.goto("/tickets/99999"); await settle(page);
    await expect(page.getByText("No such ticket")).toBeVisible();
    await expect(page.getByText("Check that the API is running")).toHaveCount(0);
    await page.goto("/tickets/abc"); await settle(page);
    await expect(page.getByText("No such ticket")).toBeVisible();
    await expect(page.getByText(/Input should be a valid integer/)).toHaveCount(0);
    await page.goto("/no-such-page");
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });

  test("gaps carry the customer's own words and a dismissed gap stays dismissed after a recompute", async () => {
    await resetDemo(page);
    await page.goto("/knowledge"); await settle(page);
    await page.getByRole("tab", { name: /Gaps/ }).click();
    const first = page.locator(".lh-field").first();
    await expect(first).not.toContainText(/colleagu|fieldnot|charg exact/);
    const label = (await first.locator("strong").first().innerText()).trim();
    const open = async () => (await (await page.request.get("/api/gaps")).json()).filter((g: { status: string }) => g.status === "open").length;
    const n = await open();
    await first.getByRole("button", { name: "Dismiss" }).click();
    await expect(toasts(page).filter({ hasText: "Dismissed" })).toBeVisible();
    await call(page, "/insights/refresh");
    expect(await open()).toBe(n - 1);
    await page.reload(); await settle(page);
    await page.getByRole("tab", { name: /Gaps/ }).click();
    await expect(page.getByText(label, { exact: true })).toHaveCount(0);
  });

  test("approving says what happened, links the ticket, and the ticket leaves the Refunds list", async () => {
    await resetDemo(page);
    await call(page, "/settings", { force_draft: true });
    await page.goto("/approvals"); await settle(page);
    const row = page.locator(".lh-field").filter({ hasText: "Refund" }).first();
    await expect(row.getByRole("link", { name: "open the ticket" })).toBeVisible();
    const href = await row.getByRole("link", { name: "open the ticket" }).getAttribute("href");
    await row.getByRole("button", { name: "Approve" }).click();
    await expect(toasts(page).filter({ hasText: "Nothing was paid" })).toBeVisible();
    const id = Number(href!.split("/").pop());
    const t = await (await page.request.get(`/api/tickets/${id}`)).json();
    expect(t.status).toBe("drafted");
    await page.goto(`/tickets/${id}`); await settle(page);
    await expect(page.getByText("Approved, not paid yet (draft mode or Pause all)")).toBeVisible();
    await call(page, "/settings", { force_draft: false });
  });

  test("handing a ticket over twice says so and keeps one record; asking for a refund twice says nothing new was sent", async () => {
    await resetDemo(page);
    const ts = await tickets(page);
    const t = ts.find((x) => x.outcome === "answer")!;
    await page.goto(`/tickets/${t.id}`); await settle(page);
    for (let i = 0; i < 2; i++) {
      await page.getByRole("button", { name: "Hand to a person" }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Hand over" }).click();
      await expect(toasts(page).filter({ hasText: i ? "already with a person" : "Handed to a person" }).first()).toBeVisible();
      await page.waitForTimeout(500);
    }
    await expect(page.getByRole("heading", { name: "Handed to a person" })).toHaveCount(1);
    expect((await (await page.request.get(`/api/tickets/${t.id}`)).json()).escalations).toHaveLength(1);
    const r = ts.find((x) => x.outcome === "refund_ok")!;
    await page.goto(`/tickets/${r.id}`); await settle(page);
    await page.getByRole("button", { name: "Ask for a refund" }).click();
    await expect(toasts(page).filter({ hasText: "already waiting" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Handed to a person" })).toHaveCount(0); // a refund waiting for approval is not a hand over
  });

  test("the gauges show the real share: a confidence of 20 is not a full ring", async () => {
    await resetDemo(page);
    const ts = await tickets(page);
    const low = ts.find((x) => x.outcome === "escalate")!;
    await page.goto(`/tickets/${low.id}`); await settle(page);
    const ring = page.locator(".lh-meter .sx-gauge");
    const now = Number(await ring.getAttribute("aria-valuenow"));
    const offset = await ring.locator(".sx-gauge__bar").evaluate((e) => ({ off: Number(e.getAttribute("stroke-dashoffset")), len: Number(e.getAttribute("stroke-dasharray")) }));
    expect(now).toBeLessThan(60);
    expect(offset.off / offset.len).toBeGreaterThan(0.3);
    await page.goto("/insights"); await settle(page);
    const ov = await (await page.request.get("/api/overview")).json();
    const g = page.locator(".sx-gauge").first();
    expect(Number(await g.getAttribute("aria-valuenow"))).toBe(ov.deflection_pct);
    await expect(page.getByText(/Polite refusals, duplicates and hand overs do not/)).toBeVisible();
    await expect(page.getByText(/polite refusals count/i)).toHaveCount(0);
  });

  test("a reset in the middle of page loads never answers 500", async () => {
    await page.goto("/"); await settle(page);
    const res = await page.evaluate(async () => {
      const h = { "x-deskfern": "1", "content-type": "application/json" };
      let stop = false; const bad: string[] = [];
      const loop = (async () => { while (!stop) for (const p of ["/api/tickets?limit=50", "/api/overview", "/api/approvals", "/api/knowledge"]) { const r = await fetch(p, { headers: h }); if (r.status >= 500) bad.push(`${p} ${r.status}`); } })();
      await new Promise((r) => setTimeout(r, 200));
      for (let i = 0; i < 3; i++) await fetch("/api/demo/reset", { method: "POST", headers: h, body: "{}" });
      stop = true; await loop; return bad;
    });
    expect(res).toEqual([]);
  });

  test("count up: an animated number that ends whole never shows a decimal on the way", async () => {
    const sample = async () => page.evaluate(() => new Promise<string[]>((done) => {
      const seen = new Set<string>(); const t0 = performance.now();
      const tick = () => { document.querySelectorAll(".sx-stat__value, .sx-gauge__center .sx-num").forEach((e) => seen.add((e as HTMLElement).innerText.trim())); if (performance.now() - t0 < 2200) setTimeout(tick, 30); else done([...seen]); };
      tick();
    }));
    for (const path of ["/", "/insights", "/knowledge"]) {
      await page.goto(path);
      const seen = await sample();
      expect(seen.length, `${path} counted`).toBeGreaterThan(3);
      expect(seen.filter((s) => /\d\.\d/.test(s)), `${path} decimals on the way`).toEqual([]);
    }
    await page.goto("/"); await settle(page);
    await page.keyboard.press("Meta+k"); await page.keyboard.type("Reset the demo"); await page.keyboard.press("Enter");
    await page.getByRole("dialog").getByRole("button", { name: "Reset" }).click();
    const after = await sample();
    expect(after.filter((s) => /\d\.\d/.test(s)), "after a reset").toEqual([]);
  });

  test("a failed load looks like a failure, not like zero or an endless wait", async () => {
    await page.route("**/api/overview", (r) => r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { message: "Internal Server Error" } }) }));
    await page.goto("/"); await page.waitForTimeout(3500);
    await expect(page.getByText("The headline numbers could not be loaded")).toBeVisible();
    await expect(page.getByText("Open tickets")).toHaveCount(0);
    await page.unroute("**/api/overview");
    await page.route("**/api/gaps", (r) => r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { message: "Internal Server Error" } }) }));
    await page.goto("/knowledge"); await page.getByRole("tab", { name: "Gaps" }).click();
    await expect(page.getByText("Could not load the gaps")).toBeVisible({ timeout: 8000 });
    await page.unroute("**/api/gaps");
    await page.route("**/api/audit/verify", (r) => r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { message: "Internal Server Error" } }) }));
    await page.goto("/audit"); await expect(page.getByText("The chain could not be checked")).toBeVisible({ timeout: 8000 });
    await page.unroute("**/api/audit/verify");
  });

  test("a dropped file does not make the browser leave the app", async () => {
    await page.goto("/knowledge"); await settle(page);
    const prevented = await page.evaluate(() => {
      const dt = new DataTransfer(); dt.items.add(new File(["a,b"], "leads.csv", { type: "text/csv" }));
      const over = new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true });
      const drop = new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true });
      document.body.dispatchEvent(over); document.body.dispatchEvent(drop);
      return over.defaultPrevented && drop.defaultPrevented;
    });
    expect(prevented).toBe(true);
    await expect(page).toHaveURL(/\/knowledge$/);
  });

  test("integrations: knowledge sources in use are the ones with pages, and they have no useless test button", async () => {
    await page.goto("/integrations"); await settle(page);
    const card = page.locator(".sx-card").filter({ has: page.getByRole("heading", { name: "Knowledge sources" }) });
    await expect(card.locator(".lh-field").filter({ hasText: "In use" })).toHaveCount(1);
    await expect(card.locator(".lh-field").filter({ hasText: "folder" }).getByText("In use")).toBeVisible();
    await expect(card.getByRole("button", { name: "Test the one in use" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Test the one in use" })).toHaveCount(4);
  });

  test("no internal names on screen: audit actions, ticket badges and categories are in plain words", async () => {
    await page.goto("/audit"); await settle(page);
    const text = await page.locator("main").innerText();
    expect(text).not.toMatch(/approval\.requested|ticket\.duplicate|ticket\.reply|\bticket:\d+|\bapproval:\d+/);
    await expect(page.getByText("Asked for an approval").first()).toBeVisible();
    await page.goto("/insights"); await settle(page);
    expect(await page.locator("main").innerText()).not.toMatch(/security_privacy|how_to/);
    const ts = await tickets(page);
    const inj = ts.find((t) => t.flags.includes("injection_only"))!;
    await page.goto(`/tickets/${inj.id}`); await settle(page);
    const badges = await page.locator("[aria-label='Triage']").innerText();
    expect(badges).not.toMatch(/injection only|no product terms|_/);
    // a table that cannot open a row does not say "enter opens"
    for (const p of ["/audit", "/health", "/knowledge"]) {
      await page.goto(p); await settle(page);
      const hint = await page.locator(".lh-noopen .sx-table__footer > span").last().evaluate((e) => getComputedStyle(e, "::after").content);
      expect(hint, p).toContain("j k move");
    }
  });

  test("a duplicate ticket names the other ticket by its number and links it", async () => {
    await resetDemo(page);
    const ts = await tickets(page);
    const d = ts.find((t) => t.status === "duplicate")!;
    await page.goto(`/tickets/${d.id}`); await settle(page);
    const card = page.locator(".sx-card").filter({ hasText: "Looks like the same question as ticket" });
    await expect(card.getByRole("link", { name: /^T\d{3}$/ })).toBeVisible();
  });

  test("the source viewer shows headings as headings, and a refund draft does not repeat itself", async () => {
    const ts = await tickets(page);
    const t = ts.find((x) => x.subject === "Charged twice")!;
    await page.goto(`/tickets/${t.id}`); await settle(page);
    const src = page.locator(".lh-source__body");
    expect(await src.innerText()).not.toMatch(/^#|\n#/);
    await expect(src.locator("strong.lh-source__h").first()).toBeVisible();
    const draft = await page.getByLabel("Drafted answer").innerText();
    expect((draft.toLowerCase().match(/refunded in full/g) ?? []).length).toBe(1);
  });

  test("Run now does something you can see: a new ticket arrives and is drafted, until the demo inbox is empty", async () => {
    await resetDemo(page);
    await call(page, "/settings", { force_draft: true });
    await page.goto("/"); await settle(page);
    const open = async () => (await (await page.request.get("/api/overview")).json()).open as number;
    const before = await open();
    await page.getByRole("button", { name: "Run now" }).click();
    await expect(toasts(page).filter({ hasText: 'A new ticket arrived: "More guests"' })).toBeVisible();
    expect(await open()).toBe(before + 1);
    await page.getByRole("textbox", { name: "Search tickets" }).fill("More guests");
    await page.getByRole("link", { name: "More guests" }).click();
    await expect(page.getByText("up to 5 guests", { exact: false }).first()).toBeVisible(); // a cited answer, drafted in the same press
    await page.goto("/"); await settle(page);
    for (let i = 0; i < 3; i++) { await page.getByRole("button", { name: "Run now" }).click(); await page.waitForTimeout(1400); }
    await expect(toasts(page).filter({ hasText: "No new tickets are left in the demo inbox" })).toBeVisible();
    await call(page, "/settings", { force_draft: false });
  });

  test("the reasons for hand overs name the ticket and link to it", async () => {
    await page.goto("/insights"); await settle(page);
    const card = page.locator(".sx-card").filter({ has: page.getByRole("heading", { name: "Why tickets went to a person" }) });
    const first = card.getByRole("link").first();
    await expect(first).toBeVisible();
    const href = await first.getAttribute("href");
    await first.click();
    await expect(page).toHaveURL(new RegExp(`${href}$`));
  });
});
