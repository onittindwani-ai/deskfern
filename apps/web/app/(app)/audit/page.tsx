"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, Card, DataTable, TableSkeleton, type Column } from "@sendora/ui-kit";
import { LoadError, PageHead } from "@/components/Parts";
import { get } from "@/lib/api";
import { auditLabel, auditTarget } from "@/lib/labels";
import type { AuditRow } from "@/lib/types";

export default function AuditPage() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => get<AuditRow[]>("/audit?limit=500") });
  const v = useQuery({ queryKey: ["audit-verify"], queryFn: () => get<{ ok: boolean; first_bad_id: number | null }>("/audit/verify") });
  const columns: Column<AuditRow>[] = [
    { key: "at", header: "When", width: "190px", render: (r) => r.at.replace("T", " ").slice(0, 19), sortValue: (r) => r.at },
    { key: "actor", header: "Who", width: "130px", sortValue: (r) => r.actor },
    { key: "action", header: "What", width: "1.2fr", render: (r) => auditLabel(r.action), sortValue: (r) => auditLabel(r.action) },
    { key: "target", header: "On", width: "1fr", render: (r) => { const t = auditTarget(r.target); return t.href ? <Link href={t.href}>{t.text}</Link> : t.text; }, sortValue: (r) => auditTarget(r.target).text },
  ];
  return (
    <div className="lh-page">
      <PageHead title="Audit log" sub="Every change, who made it and when. Each line is chained to the one before, so an edit after the fact shows." actions={<Button onClick={async () => { await v.refetch(); }}>Check the chain</Button>} />
      <Card>{v.isError ? <p className="lh-mini" role="alert">The chain could not be checked. <Button size="sm" variant="ghost" onClick={async () => { await v.refetch(); }}>Try again</Button></p> : v.data ? <Badge tone={v.data.ok ? "success" : "danger"}>{v.data.ok ? "Chain intact" : `Chain broken at entry ${v.data.first_bad_id}`}</Badge> : <Badge>Checking</Badge>}</Card>
      {q.isError ? <LoadError error={q.error} onRetry={() => q.refetch()} what="the audit log" /> : !q.data ? <TableSkeleton rows={8} columns={4} /> : <div className="lh-noopen"><DataTable label="Audit log" rows={q.data} rowKey={(r) => String(r.id)} columns={columns} height={520} /></div>}
    </div>
  );
}
