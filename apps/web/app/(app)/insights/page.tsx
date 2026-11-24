"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Card, EmptyState, Gauge, Stat } from "@sendora/ui-kit";
import { ListSkeleton, LoadError, PageHead } from "@/components/Parts";
import { get } from "@/lib/api";
import { categoryLabel } from "@/lib/labels";
import type { Insights } from "@/lib/types";

export default function InsightsPage() {
  const q = useQuery({ queryKey: ["insights"], queryFn: () => get<Insights>("/insights") });
  if (q.isError) return <div className="lh-page"><PageHead title="Insights" /><LoadError error={q.error} onRetry={() => q.refetch()} what="insights" /></div>;
  if (!q.data) return <div className="lh-page"><PageHead title="Insights" /><ListSkeleton /></div>;
  const d = q.data;
  const max = Math.max(1, ...Object.values(d.categories));
  return (
    <div className="lh-page">
      <PageHead title="Insights" sub="What people ask about, how much the desk answers with a citation, and what it hands to a person. Numbers are from the tickets in this install." />
      <div className="lh-cols4 lh-cols4--hero">
        <Card><div className="lh-hero"><Gauge value={d.deflection_pct / 100} size={110} label="Answered with citations" display={`${d.deflection_pct}%`} /><div><strong>Answered with a cited source</strong><p className="lh-mini">Cited answers and refund decisions count. Polite refusals, duplicates and hand overs do not. A person still sends every reply.</p></div></div></Card>
        <Card><Stat label="Citations used" value={d.citations} /></Card><Card><Stat label="Handed to a person" value={d.escalated} /></Card><Card><Stat label="Past reply target" value={d.breached} /></Card>
      </div>
      <div className="lh-two--rev lh-two">
        <Card title="Topics">
          <div className="lh-funnel">{Object.entries(d.categories).sort((a, b) => b[1] - a[1]).map(([k, v]) => <div key={k} className="lh-funnel__row"><span>{categoryLabel(k)}</span><div className="lh-funnel__bar"><div className="lh-funnel__fill" style={{ width: `${(v / max) * 100}%` }} /></div><strong>{v}</strong></div>)}</div>
        </Card>
        <Card title="Most cited pages">{d.top_sources.length === 0 ? <EmptyState title="No citations yet" body="Run the jobs to draft answers." /> : <ol className="lh-steps">{d.top_sources.map((s) => <li key={s.title}>{s.title} <span className="lh-mini">used {s.uses} times</span></li>)}</ol>}</Card>
      </div>
      <Card title="Why tickets went to a person">{d.escalations.length === 0 ? <p className="lh-mini">None.</p> : <div className="lh-stack">{d.escalations.slice(0, 10).map((e) => <p key={`${e.ticket_id}-${e.reason}`} className="lh-mini"><Link href={`/tickets/${e.ticket_id}`}><strong>{e.subject}</strong></Link> goes to {e.owner}: {e.reason}</p>)}</div>}</Card>
    </div>
  );
}
