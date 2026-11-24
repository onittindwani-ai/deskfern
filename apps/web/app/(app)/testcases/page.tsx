"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, EmptyState, TableSkeleton, notify } from "@sendora/ui-kit";
import { LoadError, PageHead } from "@/components/Parts";
import { del, get, post } from "@/lib/api";
import { useRole } from "@/lib/hooks";
import type { TestCaseRow } from "@/lib/types";

export default function TestCasesPage() {
  const qc = useQueryClient();
  const { canWrite } = useRole();
  const q = useQuery({ queryKey: ["testcases"], queryFn: () => get<TestCaseRow[]>("/testcases") });
  const run = useMutation({ mutationFn: () => post<{ total: number; passed: number; failed: number }>("/testcases/run"), onSuccess: (r) => { (r.failed ? notify.error : notify.success)(`${r.passed} of ${r.total} cases pass.`, r.failed ? "Open a failed case to see what is missing." : undefined); qc.invalidateQueries(); }, onError: (e: Error) => notify.error("Could not run the cases", e.message) });
  const remove = useMutation({ mutationFn: (id: number) => del(`/testcases/${id}`), onSuccess: () => { notify.info("Case removed."); qc.invalidateQueries(); }, onError: (e: Error) => notify.error("Could not remove the case", e.message) });
  return (
    <div className="lh-page">
      <PageHead title="Test cases" sub="When an agent edits a draft before sending, the edit becomes a test case. Run them after changing the knowledge or the rules to see that nothing got worse." actions={<Button variant="primary" disabled={!canWrite || (q.data?.length ?? 0) === 0} disabledReason={!canWrite ? "Viewers cannot run cases" : "There are no cases to run yet"} onClick={async () => { await run.mutateAsync(); }}>Run all cases</Button>} />
      {q.isError ? <LoadError error={q.error} onRetry={() => q.refetch()} what="test cases" /> : !q.data ? <TableSkeleton rows={4} columns={3} /> : q.data.length === 0 ? <EmptyState title="No cases yet" body="Edit a drafted reply on a ticket and send it. Your edit is saved here as a case." /> : (
        <div className="lh-stack">{q.data.map((c) => (
          <Card key={c.id} title={c.question.slice(0, 90)} headingLevel={3}>
            <p className="lh-mini">Agent wrote: {c.expected_text}</p>
            <div className="lh-toolbar">{c.expected_facts.map((f) => <Badge key={f}>{f}</Badge>)}{c.expected_docs.map((d) => <Badge key={d} tone="accent">{d}</Badge>)}{c.last_result ? <Badge tone={c.last_result === "pass" ? "success" : "danger"}>{c.last_result}</Badge> : <Badge>not run</Badge>}<Button size="sm" variant="ghost" disabled={!canWrite} disabledReason="Viewers cannot remove cases" onClick={async () => { await remove.mutateAsync(c.id); }}>Remove</Button></div>
            {c.last_detail ? <p className="lh-mini">{c.last_detail}</p> : null}
          </Card>))}</div>)}
    </div>
  );
}
