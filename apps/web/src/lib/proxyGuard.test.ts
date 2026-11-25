import assert from "node:assert/strict";
import { test } from "node:test";
import { bodyAllowed, readLimited, safePath } from "./proxyGuard.ts";

test("ordinary paths pass and the private ones never do", () => {
  assert.equal(safePath(["tickets", "12"]), "tickets/12");
  assert.equal(safePath(["knowledge", "documents"]), "knowledge/documents");
  assert.equal(safePath(["auth", "me"]), "auth/me");
  for (const bad of [["auth", "login"], ["Auth", "login"], ["auth", "login", "x"], ["webhooks", "chatwoot"], ["tools", "x"]]) assert.equal(safePath(bad), null, bad.join("/"));
});

test("dot segments and encoded tricks are refused", () => {
  for (const bad of [[".", "auth", "login"], ["..", "health"], ["tickets", ".."], ["a/b"], ["a\\b"], ["%2e"], ["%2e%2e"], ["tickets", "1%2f2"], ["a\u0000b"], [""], []]) {
    assert.equal(safePath(bad), null, JSON.stringify(bad));
  }
});

test("a segment with a query mark, fragment, semicolon or other reserved character is refused, so auth/login cannot be reached in disguise", () => {
  for (const bad of [["auth", "login?"], ["auth", "login?x=1"], ["auth", "login#"], ["auth", "login;"], ["auth", "login;x"], ["auth", "login%3F"], ["auth", "login%2e"], ["auth", "login "], ["auth", "login\\"], ["auth", "log\u00efn"], ["auth", "login\t"], ["tickets", "1?x"], ["tickets", "1&x"], ["tickets", "a,b"], ["tickets", "a:b"], ["tickets", "a@b"], ["tickets", "a=b"]]) {
    assert.equal(safePath(bad), null, JSON.stringify(bad));
  }
});

test("every path the web app really uses still passes", () => {
  for (const ok of [["tickets", "12"], ["tickets", "12", "reply", "resolve"], ["knowledge", "crawl", "3"], ["policies", "refund", "simulate"], ["actions", "pay-approved"], ["insights", "explain"], ["audit", "verify"], ["approvals", "3", "decide"], ["a.b_c~d-e"]]) {
    assert.equal(safePath(ok), ok.join("/"), ok.join("/"));
  }
});

test("a declared length over the limit is refused before reading", () => {
  assert.equal(bodyAllowed(null, 10), true);
  assert.equal(bodyAllowed("10", 10), true);
  assert.equal(bodyAllowed("11", 10), false);
  assert.equal(bodyAllowed("abc", 10), false);
  assert.equal(bodyAllowed("-5", 10), false);
});

test("a body is cut off at the limit even when it did not say how long it is", async () => {
  const big = new Request("http://x.test/", { method: "POST", body: "x".repeat(50) });
  big.headers.delete("content-length");
  assert.equal(await readLimited(big, 10), null);
  const small = new Request("http://x.test/", { method: "POST", body: "hello" });
  const got = await readLimited(small, 10);
  assert.equal(new TextDecoder().decode(got!), "hello");
});
