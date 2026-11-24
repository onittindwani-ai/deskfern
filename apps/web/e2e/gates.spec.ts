import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { PAGES, call, login, resetDemo, tickets, watch } from "./helpers";

const SHOTS = resolve(__dirname, "../../../docs/screenshots");
mkdirSync(SHOTS, { recursive: true });
const VIEWS = [{ name: "desktop", width: 1440, height: 900 }, { name: "phone", width: 390, height: 844 }];
const slug = (p: string) => (p === "/" ? "queue" : p.slice(1));

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(900); // count up and chart tween finish
}
async function layoutShift(page: Page) {
  return page.evaluate(() => new Promise<number>((done) => {
    let total = 0;
    const o = new PerformanceObserver((l) => { for (const e of l.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) if (!e.hadRecentInput) total += e.value; });
    o.observe({ type: "layout-shift", buffered: true });
    setTimeout(() => { o.disconnect(); done(total); }, 1200);
  }));
}

test.describe.serial("quality gates", () => {
  let page: Page;
  let errors: string[];
  test.beforeAll(async ({ browser }) => {
    const ctx = await browser.newContext({ baseURL: `http://localhost:${process.env.WEB_PORT ?? "3000"}`, viewport: { width: 1440, height: 900 } });
    page = await ctx.newPage();
    errors = watch(page);
    await login(page);
    await resetDemo(page);
  });
  test.afterAll(async () => { await page.close(); });

  for (const v of VIEWS) {
    test(`screenshot of every page, ${v.name}`, async () => {
      await page.setViewportSize({ width: v.width, height: v.height });
      for (const p of PAGES) {
        await page.goto(p);
        await settle(page);
        await page.screenshot({ path: `${SHOTS}/page-${slug(p)}-${v.name}.png` });
      }
    });

    test(`eight states, ${v.name}`, async () => {
      await page.setViewportSize({ width: v.width, height: v.height });
      const shot = (n: string) => page.screenshot({ path: `${SHOTS}/state-${n}-${v.name}.png` });
      const ts = await tickets(page);
      // 6 empty: no test cases exist right after a reset
      await page.goto("/testcases"); await settle(page);
      await expect(page.getByText("No cases yet")).toBeVisible();
      await shot("6-empty-state");
      // 1 default: the queue
      await page.goto("/"); await settle(page);
      await shot("1-default-queue");
      // 2 detail: a cited answer with one sentence hovered and its source highlighted
      const ans = ts.find((t) => t.outcome === "answer" && t.status === "drafted")!;
      await page.goto(`/tickets/${ans.id}`); await settle(page);
      await page.locator("button.lh-sent").first().focus(); await page.waitForTimeout(500);
      await expect(page.locator("mark.lh-mark")).toBeVisible();
      await shot("2-detail-cited-answer");
      // 3 warning: two help pages that disagree, handed to a person
      const warn = ts.find((t) => t.outcome === "conflict_escalate")!;
      await page.goto(`/tickets/${warn.id}`); await settle(page);
      await shot("3-warning-pages-disagree");
      // 4 button mid processing
      await page.goto("/"); await settle(page);
      await page.route("**/api/demo/tick", async (route) => { await new Promise((r) => setTimeout(r, 2500)); await route.continue(); });
      await page.getByRole("button", { name: "Run now" }).click(); await page.waitForTimeout(500);
      await shot("4-button-processing");
      await page.waitForTimeout(2600);
      await page.unroute("**/api/demo/tick");
      // 5 toast: redraft a ticket
      await page.goto(`/tickets/${ans.id}`); await settle(page);
      await page.getByRole("button", { name: "Draft again" }).click();
      await expect(page.getByText("Drafted again from the current knowledge.")).toBeVisible();
      await page.waitForTimeout(400);
      await shot("5-toast");
      await page.waitForTimeout(3500);
      // 7 error: the queue call is cut off
      const before = errors.length;
      await page.route("**/api/tickets?**", (route) => route.abort());
      await page.goto("/"); await page.waitForTimeout(1500);
      await expect(page.getByText(/Could not|try again/i).first()).toBeVisible();
      await shot("7-error-state");
      await page.unroute("**/api/tickets?**");
      errors.splice(before);
      // 8 dark
      await page.goto(`/tickets/${ans.id}`); await settle(page);
      await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
      await page.waitForTimeout(300);
      await shot("8-dark");
      await page.evaluate(() => document.documentElement.removeAttribute("data-theme"));
    });
  }

  for (const theme of ["light", "dark"]) {
    test(`axe finds no serious or critical problems on any page, ${theme}`, async () => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.emulateMedia({ colorScheme: theme as "light" | "dark" });
      const bad: string[] = [];
      for (const p of PAGES) {
        await page.goto(p); await settle(page);
        if (theme === "dark") await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
        await page.waitForTimeout(300);
        const res = await new AxeBuilder({ page }).analyze();
        for (const v of res.violations.filter((x) => x.impact === "serious" || x.impact === "critical")) bad.push(`${theme} ${p} ${v.id} ${v.impact}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`);
      }
      await page.emulateMedia({ colorScheme: "light" });
      expect(bad).toEqual([]);
    });
  }

  test("keyboard path: every stop shows focus, and a ticket opens and a sentence shows its source with the keyboard", async () => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/"); await settle(page);
    const seen: string[] = [];
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press("Tab");
      const info = await page.evaluate(() => {
        const e = document.activeElement as HTMLElement | null;
        if (!e || e === document.body) return null;
        const cs = getComputedStyle(e);
        const r = e.getBoundingClientRect();
        return { name: (e.getAttribute("aria-label") || e.textContent || e.tagName).trim().slice(0, 30), ring: (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none" || e.matches("[data-selected]"), visible: r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight };
      });
      if (info) { seen.push(info.name); expect(info.visible, `${info.name} is on screen`).toBeTruthy(); expect(info.ring, `${info.name} shows focus`).toBeTruthy(); }
    }
    expect(seen.length).toBeGreaterThan(10);
    const ts = await tickets(page);
    const t = ts.find((x) => x.outcome === "answer" && x.status === "drafted")!;
    await page.goto(`/tickets/${t.id}`); await settle(page);
    const s = page.locator("button.lh-sent").nth(0);
    await s.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("mark.lh-mark")).toBeVisible();
  });

  test("reduced motion: nothing animates", async () => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/"); await page.waitForLoadState("networkidle"); await page.waitForTimeout(1200);
    const running = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running" && (a.effect?.getComputedTiming().iterations ?? 1) === Infinity).length);
    expect(running).toBe(0);
    await page.emulateMedia({ reducedMotion: "no-preference" });
  });

  test("no layout shift on any page", async () => {
    const bad: string[] = [];
    for (const p of PAGES) {
      await page.goto(p);
      const cls = await layoutShift(page);
      if (cls > 0.1) bad.push(`${p} ${cls.toFixed(3)}`);
    }
    expect(bad).toEqual([]);
  });

  test("5,000 rows: the queue opens fast and stays responsive", async () => {
    const add = await call(page, "/demo/stress-tickets", { n: 5000 });
    expect(add.ok()).toBeTruthy();
    const t0 = Date.now();
    await page.goto("/");
    await page.getByRole("radio", { name: "All" }).click();
    await expect(page.locator('[role="row"]').first()).toBeVisible({ timeout: 15000 });
    await settle(page);
    expect(Date.now() - t0, "queue with 5,000 tickets is ready").toBeLessThan(9000);
    expect(await page.locator('[role="row"]').count(), "only visible rows are in the page").toBeLessThan(200);
    await page.screenshot({ path: `${SHOTS}/state-13-five-thousand-rows-desktop.png` });
  });

  test("no console errors or failed requests during the whole run", async () => {
    expect(errors).toEqual([]);
  });
});
