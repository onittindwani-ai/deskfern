/** Plain words for the internal names the API uses, so no screen shows snake_case or dotted action names. */

const CATEGORY: Record<string, string> = { billing: "Billing", account: "Account", how_to: "How to", integration: "Integration", security_privacy: "Security and privacy", outage: "Outage", other: "Other" };
export const categoryLabel = (c: string | null | undefined) => (c ? CATEGORY[c] ?? c.replace(/_/g, " ") : "Other");

const STATUS: Record<string, string> = { new: "New", drafted: "Drafted", awaiting_approval: "Waiting for approval", escalated: "With a person", answered: "Replied", refused: "Politely refused", duplicate: "Duplicate", closed: "Closed" };
export const statusLabel = (s: string) => STATUS[s] ?? s.replace(/_/g, " ");

const ACTION_STATUS: Record<string, string> = { requested: "Waiting for approval", approved: "Approved", recorded_draft: "Approved, not paid yet (draft mode or Pause all)", executing: "Being paid", executed: "Paid", rejected: "Rejected" };
export const actionStatusLabel = (s: string) => ACTION_STATUS[s] ?? s.replace(/_/g, " ");

const AUDIT: Record<string, string> = {
  "approval.requested": "Asked for an approval", "approval.approved": "Approved a request", "approval.rejected": "Rejected a request",
  "knowledge.add": "Added a page", "knowledge.source.add": "Added a knowledge source", "policy.refund.changed": "Changed the refund rule",
  "refund.executed": "Paid a refund", "refund.recorded_only": "Held back an approved refund (nothing paid)", "refund.retry": "Tried to pay a held refund", "refund.pay_approved": "Pressed Pay approved refunds", "settings.force_draft": "Changed draft mode", "settings.kill_switch": "Changed the kill switch",
  "ticket.duplicate": "Marked a ticket as a duplicate", "ticket.escalate": "Handed a ticket to a person", "ticket.redraft": "Drafted a ticket again", "ticket.reply": "Replied to a ticket", "ticket.reply_unconfirmed": "A reply was not confirmed", "ticket.reply_confirmed": "Confirmed a reply was delivered", "ticket.reply_not_delivered": "Confirmed a reply was not delivered",
};
export const auditLabel = (a: string) => AUDIT[a] ?? a.replace(/[._]/g, " ");

/** "ticket:119" becomes a short name and a page to open. */
export function auditTarget(t: string | null | undefined): { text: string; href: string | null } {
  if (!t) return { text: "", href: null };
  const [kind, id] = t.split(":");
  if (kind === "ticket" && id) return { text: `Ticket ${id}`, href: `/tickets/${id}` };
  if (kind === "approval" && id) return { text: `Approval ${id}`, href: "/approvals" };
  if (kind === "document" && id) return { text: `Page ${id}`, href: "/knowledge" };
  if (kind === "policy") return { text: "Refund rule", href: "/policies" };
  if (kind === "policy_action" && id) return { text: `Refund request ${id}`, href: null };
  if (kind === "source" && id) return { text: `Source ${id}`, href: "/knowledge" };
  return { text: t.replace(/[:_]/g, " "), href: null };
}

export const riskLabel = (r: string) => (r === "high" ? "High risk" : r === "medium" ? "Medium risk" : "Low risk");
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const FLAG: Record<string, string> = {
  injection: "orders hidden in the message", angry: "angry", other_customer_data: "asks for another person's data", needs_person: "needs a person", legal: "legal", safety: "safety", security: "security", outage: "possible outage", out_of_scope: "not about the product", unverified_sender: "sender not verified", draft_failed: "could not be drafted", erased_sender: "from an erased address", too_large: "too large to read", unreadable: "could not be read", unstorable: "could not be stored",
};
/** Flags worth showing a person. The rest (refund_request, duplicate, no_product_terms, injection_only) are internal and shown elsewhere or not at all. */
export const flagLabel = (f: string): string | null => FLAG[f] ?? null;
