"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Badge, Button, Card, EmptyState, notify } from "@sendora/ui-kit";
import { ListSkeleton, LoadError, PageHead } from "@/components/Parts";
import { get, post } from "@/lib/api";
import { useMe, useRole } from "@/lib/hooks";
import { riskLabel } from "@/lib/labels";
import type { ApprovalRow } from "@/lib/types";

export default function ApprovalsPage() {
  const qc = useQueryClient();
  const { canWrite } = useRole();
  const me = useMe();
  const q = useQuery({ queryKey: ["approvals"], queryFn: () => get<ApprovalRow[]>("/approvals") });
  const decide = useMutation({
    mutationFn: (v: { id: number; approve: boolean }) => post<{ status: string; refund: string | null }>(`/approvals/${v.id}/decide`, { approve: v.approve, note: "" }),
    onSuccess: (r, v) => { notify.success(!v.approve ? "Rejected. No refund will be made." : r.refund === "executed" ? "Approved. The refund was sent." : r.refund === "recorded_draft" ? "Approved. Nothing was paid: draft mode or the kill switch is on, so the refund was only recorded." : "Approved."); qc.invalidateQueries(); },
    onError: (e: Error) => notify.error("Could not decide", e.message),
  });
  if (q.isError) return <div className="lh-page"><PageHead title="Approvals" /><LoadError error={q.error} onRetry={() => q.refetch()} what="approvals" /></div>;
  if (!q.data) return <div className="lh-page"><PageHead title="Approvals" /><ListSkeleton /></div>;
  const pending = q.data.filter((a) => a.status === "pending");
  const past = q.data.filter((a) => a.status !== "pending");
  return (
    <div className="lh-page">
      <PageHead title="Approvals" sub="Anything that reaches outside this app waits here for a person. The person who asked cannot approve their own request." />
      <Card title={`Waiting (${pending.length})`}>{pending.length === 0 ? <EmptyState title="Nothing waiting" body="Risky actions such as sending a reminder show up here." /> : (
        <div className="lh-stack">{pending.map((a) => (
          <div key={a.id} className="lh-field" style={{ gridTemplateColumns: "1fr auto" }}>
            <span><strong>{a.summary}</strong> <Badge tone={a.risk === "high" ? "danger" : "warning"}>{riskLabel(a.risk)}</Badge><br /><span className="lh-mini" style={{ fontSize: "var(--sx-text-caption)", color: "var(--sx-ink-2)" }}>Asked by {a.requested_by} on {a.created_at.slice(0, 10)}{a.payload?.ticket_id ? <> , <Link href={`/tickets/${String(a.payload.ticket_id)}`}>open the ticket</Link></> : null}</span></span>
            <span className="sx-row"><Button size="sm" variant="primary" disabled={!canWrite || a.requested_by === me.data?.user} disabledReason={!canWrite ? "Viewers cannot decide" : "You asked for this one, so someone else must decide it"} onClick={async () => { await decide.mutateAsync({ id: a.id, approve: true }); }}>Approve</Button><Button size="sm" disabled={!canWrite || a.requested_by === me.data?.user} disabledReason={!canWrite ? "Viewers cannot decide" : "You asked for this one, so someone else must decide it"} onClick={async () => { await decide.mutateAsync({ id: a.id, approve: false }); }}>Reject</Button></span>
          </div>))}</div>)}</Card>
      <Card title="Decided">{past.length === 0 ? <EmptyState title="No history yet" body="Decisions appear here." /> : <div className="lh-stack">{past.map((a) => <div key={a.id} className="lh-field" style={{ gridTemplateColumns: "1fr auto" }}><span>{a.summary}</span><Badge tone={a.status === "approved" ? "success" : "neutral"}>{a.status === "approved" ? "Approved" : a.status === "rejected" ? "Rejected" : a.status} by {a.decided_by}</Badge></div>)}</div>}</Card>
    </div>
  );
}
