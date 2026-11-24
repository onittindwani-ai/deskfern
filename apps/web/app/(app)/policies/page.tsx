"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Field, Stat, notify } from "@sendora/ui-kit";
import { LoadError, PageHead } from "@/components/Parts";
import { get, post, put } from "@/lib/api";
import { useRole } from "@/lib/hooks";
import type { RefundRule, SimRow } from "@/lib/types";

const num = (v: string, lo: number, hi: number) => (/^\d+$/.test(v) && +v >= lo && +v <= hi ? null : `Use a whole number from ${lo} to ${hi}.`);

export default function PoliciesPage() {
  const qc = useQueryClient();
  const { owner } = useRole();
  const q = useQuery({ queryKey: ["rule"], queryFn: () => get<RefundRule>("/policies/refund") });
  const [m, setM] = useState("7"), [a, setA] = useState("14"), [x, setX] = useState("1");
  const [dup, setDup] = useState(true), [canc, setCanc] = useState(true);
  const [sim, setSim] = useState<{ rows: SimRow[]; eligible: number; total: number } | null>(null);
  useEffect(() => { if (q.data) { setM(String(q.data.monthly_days)); setA(String(q.data.annual_days)); setX(String(q.data.max_per_12m)); setDup(q.data.always_full.includes("duplicate_charge")); setCanc(q.data.always_full.includes("charged_after_cancel")); } }, [q.data]);
  const valid = !num(m, 0, 365) && !num(a, 0, 365) && !num(x, 0, 12);
  const body = () => ({ monthly_days: +m, annual_days: +a, max_per_12m: +x, always_full: [dup ? "duplicate_charge" : "", canc ? "charged_after_cancel" : ""].filter(Boolean) });
  const run = useMutation({ mutationFn: () => post<{ rows: SimRow[]; eligible: number; total: number }>("/policies/refund/simulate", body()), onSuccess: setSim, onError: (e: Error) => notify.error("Could not simulate", e.message) });
  useEffect(() => { if (valid) { const t = setTimeout(() => run.mutate(), 300); return () => clearTimeout(t); } }, [m, a, x, dup, canc]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({ mutationFn: () => put("/policies/refund", body()), onSuccess: () => { notify.success("Refund rule saved."); qc.invalidateQueries(); }, onError: (e: Error) => notify.error("The rule was not saved", e.message) });
  if (q.isError) return <div className="lh-page"><PageHead title="Policies" /><LoadError error={q.error} onRetry={() => q.refetch()} what="the refund rule" /></div>;
  return (
    <div className="lh-page">
      <PageHead title="Policies" sub="The refund rule is plain numbers. The desk applies it and then asks a person to approve. It never pays on its own. These numbers are placeholders until you set your real policy." />
      <div className="lh-two">
        <Card title="Refund rule">
          <div className="lh-stack">
            <Field label="Monthly plan, days after the first payment" value={m} onChange={setM} validate={(v) => num(v, 0, 365)} inputMode="numeric" />
            <Field label="Annual plan, days after the first or a renewal payment" value={a} onChange={setA} validate={(v) => num(v, 0, 365)} inputMode="numeric" />
            <Field label="Refunds allowed per customer in 12 months" value={x} onChange={setX} validate={(v) => num(v, 0, 12)} inputMode="numeric" />
            <label className="sx-row"><input type="checkbox" checked={dup} onChange={(e) => setDup(e.target.checked)} /> A duplicate charge is always refunded in full</label>
            <label className="sx-row"><input type="checkbox" checked={canc} onChange={(e) => setCanc(e.target.checked)} /> A charge after a confirmed cancellation is refunded in full</label>
            <div><Button variant="primary" disabled={!owner || !valid} disabledReason={!owner ? "Only the owner can save the rule" : "Fix the numbers first"} onClick={async () => { await save.mutateAsync(); }}>Save the rule</Button></div>
          </div>
        </Card>
        <Card title="Simulator: what this rule would do today">
          {sim ? <><div className="lh-cols"><Stat label="Would qualify" value={sim.eligible} /><Stat label="Paying customers" value={sim.total} /></div>
            <div className="lh-sim" aria-live="polite">{sim.rows.map((r) => <div key={r.key} className="lh-sim__row"><span><strong>{r.customer}</strong> <span className="lh-mini">{r.plan}, paid {r.paid_on || "never"}<br />{r.why}</span></span><Badge tone={r.eligible ? "success" : "neutral"}>{r.eligible ? `Refund ${(r.amount_minor / 100).toFixed(2)}` : "No refund"}</Badge></div>)}</div></> : <p className="lh-mini">Change a number to see the result for every paying customer. Nothing is changed by the simulator.</p>}
        </Card>
      </div>
    </div>
  );
}
