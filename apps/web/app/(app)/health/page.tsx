"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, DataTable, EmptyState, TableSkeleton, notify, type Column } from "@sendora/ui-kit";
import { LoadError, PageHead } from "@/components/Parts";
import { get, post } from "@/lib/api";
import { useRole } from "@/lib/hooks";
import type { HealthRow } from "@/lib/types";

export default function HealthPage() {
  const qc = useQueryClient();
  const { canWrite } = useRole();
  const q = useQuery({ queryKey: ["health"], queryFn: () => get<HealthRow[]>("/health") });
  const refresh = useMutation({ mutationFn: () => post("/insights/refresh"), onSuccess: () => { notify.success("Scores recomputed from the ticket history."); qc.invalidateQueries(); }, onError: (e: Error) => notify.error("Could not recompute", e.message) });
  const cols: Column<HealthRow>[] = [
    { key: "customer", header: "Customer", width: "1.2fr", render: (r) => <span><strong>{r.customer}</strong><br /><span className="lh-mini">{r.plan}</span></span>, sortValue: (r) => r.customer },
    { key: "score", header: "Health", width: "100px", align: "right", sortValue: (r) => r.score },
    { key: "risk", header: "Churn risk", width: "120px", render: (r) => <Badge tone={r.risk === "high" ? "danger" : r.risk === "medium" ? "warning" : "success"}>{r.risk}</Badge>, sortValue: (r) => r.score },
    { key: "why", header: "Why", width: "2fr", render: (r) => r.reasons.join(", ") || "Nothing unusual" },
  ];
  const risky = (q.data ?? []).filter((r) => r.risk !== "low");
  return (
    <div className="lh-page">
      <PageHead title="Health" sub="A customer's health score starts at 100 and drops for repeated tickets, angry messages, tickets the desk could not answer, refund requests and billing errors. It is a simple sum you can read, not a prediction." actions={<Button disabled={!canWrite} disabledReason="Viewers cannot recompute" onClick={async () => { await refresh.mutateAsync(); }}>Recompute</Button>} />
      {q.isError ? <LoadError error={q.error} onRetry={() => q.refetch()} what="health scores" /> : !q.data ? <TableSkeleton rows={8} columns={4} /> : q.data.length === 0 ? <EmptyState title="No scores yet" body="Recompute after the first tickets are in." /> : (
        <>
          <Card title={`Most at risk (${Math.min(8, risky.length)} of ${risky.length})`}>{risky.length === 0 ? <p className="lh-mini">Nobody is at risk right now.</p> : <div className="lh-stack">{risky.slice(0, 8).map((r) => <div key={r.customer_id} className="lh-field" style={{ gridTemplateColumns: "1fr auto" }}><span><strong>{r.customer}</strong> <span className="lh-mini">{r.reasons.join(", ")}</span></span><Badge tone={r.risk === "high" ? "danger" : "warning"}>{r.score}</Badge></div>)}</div>}</Card>
          <div className="lh-noopen"><DataTable label="Customer health" rows={q.data} rowKey={(r) => String(r.customer_id)} columns={cols} height={420} /></div>
        </>)}
    </div>
  );
}
