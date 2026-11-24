"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, ConfirmDialog, EmptyState, SplitView, notify } from "@sendora/ui-kit";
import { Meter } from "@/components/Meter";
import { ListSkeleton, LoadError, PageHead, Textarea } from "@/components/Parts";
import { SourceViewer } from "@/components/SourceViewer";
import { ApiError, get, post } from "@/lib/api";
import { useRole } from "@/lib/hooks";
import { actionStatusLabel, categoryLabel, flagLabel, statusLabel } from "@/lib/labels";
import type { TicketDetail } from "@/lib/types";

export default function TicketPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { canWrite } = useRole();
  const idOk = /^\d+$/.test(id);
  const q = useQuery({ queryKey: ["ticket", id], queryFn: () => get<TicketDetail>(`/tickets/${id}`), enabled: idOk, retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2 });
  const [active, setActive] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [confirmEsc, setConfirmEsc] = useState(false);
  const ans = q.data?.answer ?? null;
  useEffect(() => { if (ans) setText(ans.edited_text || ans.text); setActive(null); }, [ans?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => qc.invalidateQueries();
  const send = useMutation({ mutationFn: () => post<{ sent: boolean; edited: boolean }>(`/tickets/${id}/reply`, { text }), onSuccess: (r) => { notify.success((r.sent ? "Reply sent." : "Reply recorded. Nothing left this app, because draft mode or the kill switch is on.") + (r.edited ? " Your edit was saved as a test case." : "")); refresh(); }, onError: (e: Error) => notify.error("Could not send", e.message) });
  const redraft = useMutation({ mutationFn: () => post(`/tickets/${id}/redraft`), onSuccess: () => { notify.info("Drafted again from the current knowledge."); refresh(); }, onError: (e: Error) => notify.error("Could not draft again", e.message) });
  const refund = useMutation({ mutationFn: () => post<{ already: boolean }>(`/tickets/${id}/refund`), onSuccess: (r) => { notify.success(r.already ? "A refund request is already waiting for this ticket. Nothing new was sent." : "Refund request made. Another person must approve it."); refresh(); }, onError: (e: Error) => notify.error("No refund request", e.message) });
  const esc = useMutation({ mutationFn: () => post<{ already: boolean }>(`/tickets/${id}/escalate`, { reason: "Passed to a person by an agent.", owner: "support lead" }), onSuccess: (r) => { notify.info(r.already ? "This ticket is already with a person." : "Handed to a person."); refresh(); }, onError: (e: Error) => notify.error("Could not hand it over", e.message) });
  const cite = useMemo(() => new Map((ans?.citations ?? []).map((c) => [c.sentence_index, c])), [ans]);
  const nums = useMemo(() => { const m = new Map<number, number>(); let n = 0; (ans?.sentences ?? []).forEach((s, i) => { if (cite.has(i)) m.set(i, ++n); }); return m; }, [ans, cite]);
  const missing = !idOk || (q.error instanceof ApiError && q.error.status === 404);
  if (missing) return <div className="lh-page"><PageHead title="Ticket" actions={<Button variant="ghost" feedback={false} onClick={() => router.push("/")}>Back to the queue</Button>} /><EmptyState title="No such ticket" body="This ticket does not exist, or it was removed when the demo data was reset." actionLabel="Back to the queue" onAction={() => router.push("/")} /></div>;
  if (q.isError) return <div className="lh-page"><PageHead title="Ticket" /><LoadError error={q.error} onRetry={() => q.refetch()} what="the ticket" /></div>;
  if (!q.data) return <div className="lh-page"><PageHead title="Ticket" /><ListSkeleton rows={6} /></div>;
  const t = q.data;
  const shown = active !== null ? cite.get(active) : ans?.citations[0];
  const rf = t.actions.find((a) => a.kind === "refund");
  return (
    <div className="lh-page">
      <PageHead title={t.subject} sub={`${t.customer}${t.plan ? `, ${t.plan}` : ""} , ${t.channel} , ${t.external_id}`} actions={<Button variant="ghost" feedback={false} onClick={() => router.push("/")}>Back to the queue</Button>} />
      <div className="lh-toolbar" aria-label="Triage">
        <Badge tone={t.urgency === "urgent" ? "danger" : t.urgency === "high" ? "warning" : "neutral"}>{t.urgency}</Badge><Badge>{categoryLabel(t.category)}</Badge><Badge tone={t.sentiment === "angry" ? "danger" : "neutral"}>{t.sentiment}</Badge><Badge>{t.language}</Badge><Badge tone={t.status === "escalated" ? "warning" : t.status === "answered" ? "success" : "accent"}>{statusLabel(t.status)}</Badge>
        {t.flags.map((f) => flagLabel(f)).filter((l): l is string => !!l).map((l) => <Badge key={l} tone="warning">{l}</Badge>)}
        {t.breached ? <Badge tone="danger">past the reply target</Badge> : null}
      </div>
      <SplitView list={
        <div className="lh-stack">
          <Card title="Conversation">
            <div className="lh-stack">
              {t.messages.map((m) => (
                <div key={m.id} className={`lh-msg ${m.direction === "out" ? "lh-msg--out" : m.direction === "note" ? "lh-msg--note" : ""}`}>
                  <span className="lh-mini">{m.direction === "note" ? "Note from the desk" : m.direction === "out" ? `Reply from ${m.author}` : m.author}</span>
                  <p>{m.body}</p>
                </div>
              ))}
            </div>
          </Card>
          {t.customer_detail ? <Card title="Customer" headingLevel={3}><p className="lh-mini">{t.customer_detail.name}, {t.customer_detail.plan} {t.customer_detail.interval}, {t.customer_detail.seats} {t.customer_detail.seats === 1 ? "seat" : "seats"}, last paid {t.customer_detail.last_paid ?? "never"}, refunds in 12 months: {t.customer_detail.refunds_12m}</p></Card> : null}
          {t.escalations.length ? <Card title="Handed to a person" headingLevel={3}>{t.escalations.map((e) => <p key={e.id} className="lh-mini"><strong>{e.owner}</strong>: {e.reason}</p>)}</Card> : null}
          {t.duplicate_of ? <Card title="Duplicate" headingLevel={3}><p className="lh-mini">Looks like the same question as ticket <Link href={`/tickets/${t.duplicate_of}`}>{t.duplicate_of_external ?? t.duplicate_of}</Link>. No second answer was drafted.</p></Card> : null}
        </div>
      } detail={
        <div className="lh-stack">
          {ans ? (
            <>
              <Card title="Answer draft">
                <p className="lh-mini">Hover or tab to a sentence to see the passage that backs it. {ans.writer === "offline rules, not a language model" ? "Written by rules that copy sentences from the sources, not by a language model." : ""}</p>
                <p className="lh-draft" aria-label="Drafted answer">
                  {ans.sentences.map((s, i) => s.removed ? null : s.kind === "fact" ? (
                    <button key={i} className="lh-sent" aria-pressed={active === i} onMouseEnter={() => setActive(i)} onFocus={() => setActive(i)} onClick={() => setActive(i)} aria-label={`${s.text} Source ${nums.get(i) ?? ""}`}>{s.text}<sup>{nums.get(i)}</sup></button>
                  ) : <span key={i} className={`lh-sent lh-sent--plain ${s.kind === "decision" ? "lh-sent--decision" : ""}`} title={s.kind === "decision" ? "Worked out by code from the account and the refund rule" : "Fixed wording, makes no claim"}> {s.text} </span>)}
                </p>
                {ans.sentences.some((s) => s.removed) ? <div className="lh-stack"><strong>Removed by the check</strong>{ans.sentences.filter((s) => s.removed).map((s, i) => <p key={i} className="lh-mini"><span className="lh-sent lh-sent--removed">{s.text}</span> {s.reason}</p>)}</div> : null}
                {ans.reason ? <p className="lh-mini" role="status">{ans.reason}</p> : null}
              </Card>
              {shown ? <SourceViewer title={shown.title + (shown.section ? `, ${shown.section}` : "")} text={shown.document_text} highlight={shown.highlight} updatedOn={shown.updated_on} outdated={shown.outdated} /> : <Card><p className="lh-mini">No source is cited. This draft makes no claim from the help pages.</p></Card>}
              <Card title="How sure is the desk?"><Meter meter={ans.meter} /></Card>
              {rf ? <Card title="Refund request" headingLevel={3}><p><Badge tone={rf.status === "executed" ? "success" : rf.status === "rejected" ? "neutral" : "accent"}>{actionStatusLabel(rf.status)}</Badge> {(rf.amount_minor / 100).toFixed(2)} USD. {String(rf.rationale.explanation ?? "")}</p><p className="lh-mini">A refund only runs after another person approves it on the Approvals page. In draft mode the intent is recorded and nothing is paid.</p></Card> : null}
              <Card title="Your reply">
                <Textarea label="Reply to the customer" value={text} onChange={(e) => setText(e.target.value)} hint="If you change the words, the change is saved as a test case so the desk learns from it." />
                <div className="lh-toolbar">
                  <Button variant="primary" disabled={!canWrite || !text.trim() || t.status === "answered"} disabledReason={!canWrite ? "Viewers cannot send replies" : t.status === "answered" ? "This ticket already has a reply" : "Write a reply first"} onClick={async () => { await send.mutateAsync(); }}>{t.status === "answered" ? "Replied" : "Send reply"}</Button>
                  <Button disabled={!canWrite} disabledReason="Viewers cannot change tickets" onClick={async () => { await redraft.mutateAsync(); }}>Draft again</Button>
                  <Button disabled={!canWrite} disabledReason="Viewers cannot ask for refunds" onClick={async () => { await refund.mutateAsync(); }}>Ask for a refund</Button>
                  <Button variant="ghost" feedback={false} disabled={!canWrite} disabledReason="Viewers cannot hand tickets over" onClick={() => setConfirmEsc(true)}>Hand to a person</Button>
                </div>
              </Card>
            </>
          ) : <Card><p>{t.status === "duplicate" ? "This ticket is a duplicate, so no answer was drafted." : "No draft yet. Run the jobs from the demo bar."}</p></Card>}
        </div>
      } />
      <ConfirmDialog open={confirmEsc} onOpenChange={setConfirmEsc} title="Hand this ticket to a person?" description="It moves to Needs a person and the support lead is named on it." confirmLabel="Hand over" onConfirm={() => void esc.mutateAsync()} />
    </div>
  );
}
