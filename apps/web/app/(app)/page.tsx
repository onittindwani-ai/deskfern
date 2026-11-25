"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Badge, Button, Card, DataTable, EmptyState, Field, SegmentedControl, Stat, TableSkeleton, type Column } from "@sendora/ui-kit";
import { LoadError, PageHead } from "@/components/Parts";
import { Sla } from "@/components/Sla";
import { get } from "@/lib/api";
import { flagLabel } from "@/lib/labels";
import type { Overview, TicketRow } from "@/lib/types";

type View = "open" | "escalated" | "awaiting_approval" | "answered" | "all";
const OUT: Record<string, { label: string; tone: "success" | "warning" | "danger" | "neutral" | "accent" }> = {
  answer: { label: "Cited answer", tone: "success" }, refund_ok: { label: "Refund to approve", tone: "accent" }, refund_denied: { label: "Refund refused by rule", tone: "neutral" }, refuse: { label: "Politely refused", tone: "neutral" },
  escalate: { label: "Needs a person", tone: "warning" }, conflict_escalate: { label: "Pages disagree", tone: "warning" }, duplicate: { label: "Duplicate", tone: "neutral" },
};

export default function QueuePage() {
  const router = useRouter();
  const [view, setView] = useState<View>("open");
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["tickets", "all"], queryFn: async () => ({ at: Date.now(), rows: await get<TicketRow[]>("/tickets?limit=5000") }), refetchInterval: 30_000 });
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => get<Overview>("/overview") });
  const rows = useMemo(() => {
    const all = list.data?.rows ?? [];
    const f = all.filter((r) => (view === "all" ? true : view === "open" ? ["new", "drafted", "awaiting_approval", "escalated"].includes(r.status) : r.status === view));
    return q ? f.filter((r) => `${r.subject} ${r.customer}`.toLowerCase().includes(q.toLowerCase())) : f;
  }, [list.data, view, q]);
  const at = list.data?.at ?? Date.now();
  const columns: Column<TicketRow>[] = [
    { key: "sla", header: "First reply due", width: "150px", render: (r) => <Sla minutes={r.minutes_left} fetchedAt={at} />, sortValue: (r) => r.minutes_left ?? 1e9 },
    { key: "subject", header: "Ticket", width: "1.6fr", render: (r) => <span><Link href={`/tickets/${r.id}`} className="lh-linkbtn"><strong>{r.subject}</strong></Link><br /><span className="lh-mini">{r.customer}{r.plan ? `, ${r.plan}` : ""}</span></span>, sortValue: (r) => r.subject },
    { key: "urgency", header: "Urgency", width: "100px", render: (r) => <Badge tone={r.urgency === "urgent" ? "danger" : r.urgency === "high" ? "warning" : "neutral"}>{r.urgency}</Badge>, sortValue: (r) => r.urgency },
    { key: "outcome", header: "Draft", width: "190px", render: (r) => { const o = OUT[r.outcome]; return o ? <Badge tone={o.tone}>{o.label}</Badge> : <Badge>New</Badge>; }, sortValue: (r) => r.outcome },
    { key: "conf", header: "Confidence", width: "110px", align: "right", render: (r) => (r.confidence === null ? "" : `${Math.round(r.confidence * 100)}`), sortValue: (r) => r.confidence ?? -1 },
    { key: "flags", header: "Flags", width: "1fr", render: (r) => <span className="sx-row">{r.flags.filter((f) => ["injection", "angry", "other_customer_data", "needs_person", "unverified_sender"].includes(f)).map((f) => <Badge key={f} tone="warning">{flagLabel(f)}</Badge>)}</span> },
  ];
  const o = ov.data;
  const num = (v: number | undefined) => v ?? 0;
  return (
    <div className="lh-page">
      <PageHead title="Queue" sub="Every ticket with a first reply timer. Open one to see the drafted answer, the passages it cites and how sure the desk is." />
      {ov.isError ? <Card><p className="lh-mini" role="alert">The headline numbers could not be loaded. <Button size="sm" variant="ghost" onClick={async () => { await ov.refetch(); }}>Try again</Button></p></Card> : (
        <div className="lh-cols4">
          <Card><Stat label="Open tickets" value={num(o?.open)} /></Card>
          <Card><Stat label="Past the reply target" value={num(o?.breached)} note="late now" /></Card>
          <Card><Stat label="Answered with citations" value={num(o?.deflection_pct)} format={(n) => `${Math.round(n)}%`} note="of all tickets" /></Card>
          <Card><Stat label="Waiting for approval" value={num(o?.pending_approvals)} /></Card>
        </div>)}
      <div className="lh-toolbar">
        <SegmentedControl<View> label="Show" value={view} onChange={setView} options={[{ value: "open", label: "Open" }, { value: "escalated", label: "Needs a person" }, { value: "awaiting_approval", label: "Refunds" }, { value: "answered", label: "Answered" }, { value: "all", label: "All" }]} />
        <Field label="Search tickets" value={q} onChange={setQ} clearable placeholder="Subject or customer" />
      </div>
      {list.isError ? <LoadError error={list.error} onRetry={() => list.refetch()} what="the queue" /> : !list.data ? <TableSkeleton rows={8} columns={5} /> : rows.length === 0 ? <EmptyState title="Nothing here" body="No tickets match this view. Try All, or clear the search." actionLabel="Show all" onAction={() => { setView("all"); setQ(""); }} /> :
        <DataTable label="Tickets" rows={rows} rowKey={(r) => String(r.id)} columns={columns} height={520} onRowOpen={(r) => router.push(`/tickets/${r.id}`)} />}
    </div>
  );
}
