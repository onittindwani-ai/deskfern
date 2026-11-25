export interface Me { user: string; role: "owner" | "approver" | "viewer"; demo_mode: boolean }
export interface Settings { mode: string; effective_mode: string; kill_switch: boolean; demo_mode: boolean; problems: string[]; company: string; writer: string; search: string; sla_hours: Record<string, number>; sla_clock: string; business_hours: string }
export interface Overview { tickets: number; open: number; breached: number; outcomes: Record<string, number>; deflection_pct: number; escalated: number; pending_approvals: number; open_gaps: number }
export interface TicketRow {
  id: number; external_id: string; subject: string; customer: string; plan: string; status: string; outcome: string; category: string; urgency: string; sentiment: string; language: string; flags: string[];
  created_at: string; due_at: string | null; minutes_left: number | null; breached: boolean; confidence: number | null; channel: string;
}
export interface Citation { chunk_id: number; document_id: number; title: string; slug: string; section: string; updated_on: string | null; outdated: boolean; chunk_text: string; quote: string; support: number; document_text: string; highlight: [number, number]; highlight_exact?: boolean; sentence_index: number }
export interface SentenceRow { text: string; kind: "fact" | "template" | "decision"; chunk_id: number | null; support: number; quote: string; removed: boolean; reason: string }
export interface AnswerRow { id: number; decision: string; status: string; text: string; edited_text: string; sentences: SentenceRow[]; citations: Citation[]; meter: { retrieval: number; groundedness: number; policy: number; confidence: number }; reason: string; writer: string; created_at: string }
export interface TicketDetail extends TicketRow {
  customer_detail: { id: number; name: string; email: string; plan: string; interval: string; seats: number; last_paid: string | null; refunds_12m: number } | null;
  messages: { id: number; direction: string; author: string; body: string; at: string }[]; answer: AnswerRow | null; escalations: { id: number; reason: string; owner: string; status: string }[];
  actions: { id: number; kind: string; amount_minor: number; currency: string; status: string; approval_id: number | null; rationale: Record<string, unknown> }[]; duplicate_of: number | null; duplicate_of_external: string | null;
}
export interface ActionRow { id: number; ticket_id: number; kind: string; amount_minor: number; status: string; approval_id: number | null }
export interface Knowledge { sources: { id: number; kind: string; name: string; uri: string; status: string; last_synced_at: string | null; error: string; documents: number }[]; documents: { id: number; title: string; slug: string; kind: string; source_id: number; updated_on: string | null; outdated: boolean; chunks: number; words: number }[]; chunks: number; coverage_pct: number; outdated: number }
export interface Gap { id: number; label: string; count: number; samples: string[]; ticket_ids: number[]; status: string }
export interface RefundRule { monthly_days: number; annual_days: number; max_per_12m: number; always_full: string[] }
export interface SimRow { customer: string; key: string; plan: string; paid_on: string; eligible: boolean; code: string; amount_minor: number; why: string }
export interface HealthRow { customer: string; plan: string; email: string; score: number; risk: string; reasons: string[]; customer_id: number }
export interface Insights { tickets: number; open: number; breached: number; outcomes: Record<string, number>; deflection_pct: number; escalated: number; categories: Record<string, number>; citations: number; top_sources: { title: string; uses: number }[]; escalations: { ticket_id: number; subject: string; reason: string; owner: string }[] }
export interface TestCaseRow { id: number; question: string; expected_text: string; expected_docs: string[]; expected_facts: string[]; origin: string; ticket_id: number | null; last_result: string; last_detail: string }
export interface ApprovalRow { id: number; action: string; summary: string; risk: string; status: string; requested_by: string; decided_by: string | null; note: string | null; created_at: string; payload: Record<string, unknown> }
export interface AuditRow { id: number; actor: string; action: string; target: string | null; detail: Record<string, unknown>; at: string }
export interface Integrations { categories: string[]; demo_mode: boolean; adapters: { category: string; name: string; tier: string; live_verified: boolean; notes: string; selected: boolean; env_vars: string[]; configured: boolean }[] }
