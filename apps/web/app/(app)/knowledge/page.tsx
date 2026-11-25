"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, DataTable, EmptyState, Field, SegmentedControl, Stat, TableSkeleton, TabsView, notify, type Column } from "@sendora/ui-kit";
import { LoadError, PageHead, Textarea } from "@/components/Parts";
import { get, post } from "@/lib/api";
import { useRole } from "@/lib/hooks";
import type { Gap, Knowledge } from "@/lib/types";

type Doc = Knowledge["documents"][number];

export default function KnowledgePage() {
  const qc = useQueryClient();
  const { canWrite } = useRole();
  const k = useQuery({ queryKey: ["knowledge"], queryFn: () => get<Knowledge>("/knowledge") });
  const g = useQuery({ queryKey: ["gaps"], queryFn: () => get<Gap[]>("/gaps") });
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [kind, setKind] = useState<"paste" | "site">("paste");
  const [url, setUrl] = useState("demo://sample-help-site");
  const [crawl, setCrawl] = useState<{ status: string; fetched: number; skipped: number; log: string[] } | null>(null);
  const add = useMutation({ mutationFn: () => post<{ chunks: number }>("/knowledge/documents", { title, text: body }), onSuccess: (r) => { notify.success(`Added. ${r.chunks} passages are searchable now.`); setTitle(""); setBody(""); qc.invalidateQueries(); }, onError: (e: Error) => notify.error("Could not add", e.message) });
  const start = useMutation({
    mutationFn: async () => {
      const r = await post<{ crawl_job_id: number }>("/knowledge/sources", { kind: "site", name: url.replace(/^\w+:\/\//, "").slice(0, 60), uri: url });
      const j = await get<{ status: string; fetched: number; skipped: number; log: string[] }>(`/knowledge/crawl/${r.crawl_job_id}`);
      setCrawl(j);
      if (j.status !== "done") throw new Error(j.log[0] ?? "The crawl failed."); // a failed crawl is an error, so the button shows no success tick
      return j;
    },
    onSuccess: (j) => { notify.success(`Crawled ${j.fetched} pages, skipped ${j.skipped}.`); qc.invalidateQueries(); },
    onError: (e: Error) => { notify.error("The crawl did not run", e.message); qc.invalidateQueries(); },
  });
  const syncFolder = useMutation({ mutationFn: () => post<{ new: number; changed: number; same: number; skipped: number }>("/knowledge/folder/sync"), onSuccess: (r) => { notify.success(`Read the folder: ${r.new} new, ${r.changed} changed, ${r.same} unchanged, ${r.skipped} skipped.`); qc.invalidateQueries(); }, onError: (e: Error) => notify.error("Could not read the folder", e.message) });
  const gapStatus = useMutation({ mutationFn: (v: { id: number; status: string }) => post(`/gaps/${v.id}/status`, { status: v.status }), onSuccess: (_r, v) => { notify.info(v.status === "covered" ? "Marked as covered. It will not come back." : "Dismissed. It will not come back."); qc.invalidateQueries(); }, onError: (e: Error) => notify.error("Could not change the gap", e.message) });
  const cols: Column<Doc>[] = [
    { key: "title", header: "Page", width: "1.5fr", render: (r) => <strong>{r.title}</strong>, sortValue: (r) => r.title },
    { key: "kind", header: "Type", width: "80px", sortValue: (r) => r.kind },
    { key: "updated", header: "Updated", width: "120px", render: (r) => r.updated_on ?? "no date", sortValue: (r) => r.updated_on ?? "" },
    { key: "chunks", header: "Passages", width: "100px", align: "right", sortValue: (r) => r.chunks },
    { key: "state", header: "Health", width: "150px", render: (r) => (r.outdated ? <Badge tone="warning">Over 18 months old</Badge> : <Badge tone="success">Current</Badge>), sortValue: (r) => Number(r.outdated) },
  ];
  if (k.isError) return <div className="lh-page"><PageHead title="Knowledge" /><LoadError error={k.error} onRetry={() => k.refetch()} what="the knowledge base" /></div>;
  const d = k.data;
  return (
    <div className="lh-page">
      <PageHead title="Knowledge" sub="What the desk is allowed to say comes only from here. Everything it reads is treated as plain text, never as orders." />
      <div className="lh-cols4">
        <Card><Stat label="Pages" value={d?.documents.length ?? 0} /></Card><Card><Stat label="Searchable passages" value={d?.chunks ?? 0} /></Card>
        <Card><Stat label="Tickets with a source" value={d?.coverage_pct ?? 0} format={(n) => `${Math.round(n)}%`} /></Card><Card><Stat label="Old pages" value={d?.outdated ?? 0} /></Card>
      </div>
      <TabsView defaultValue="pages" items={[
        { value: "pages", label: "Pages", content: !d ? <TableSkeleton rows={6} columns={5} /> : <div className="lh-noopen"><DataTable label="Knowledge pages" rows={d.documents} rowKey={(r) => String(r.id)} columns={cols} height={420} /></div> },
        { value: "gaps", label: g.data ? `Gaps (${g.data.filter((x) => x.status === "open").length})` : "Gaps", content: g.isError ? <LoadError error={g.error} onRetry={() => g.refetch()} what="the gaps" /> : !g.data ? <TableSkeleton rows={4} columns={3} /> : g.data.filter((x) => x.status === "open").length === 0 ? <EmptyState title="No gaps" body="Every question had a source." /> :
          <div className="lh-stack">{g.data.filter((x) => x.status === "open").map((x) => (
            <div key={x.id} className="lh-field" style={{ gridTemplateColumns: "1fr auto" }}>
              <span><strong>{x.label}</strong> <Badge tone="warning">{x.count} question{x.count === 1 ? "" : "s"}</Badge>{x.samples.length > 1 ? <><br /><span className="lh-mini">Also asked as: {x.samples.slice(1).join(", ")}</span></> : null}</span>
              <span className="lh-toolbar"><Button size="sm" disabled={!canWrite} disabledReason="Viewers cannot change gaps" onClick={async () => { await gapStatus.mutateAsync({ id: x.id, status: "covered" }); }}>Covered now</Button><Button size="sm" variant="ghost" disabled={!canWrite} disabledReason="Viewers cannot change gaps" onClick={async () => { await gapStatus.mutateAsync({ id: x.id, status: "dismissed" }); }}>Dismiss</Button></span>
            </div>))}</div> },
        { value: "add", label: "Add a source", content: (
          <div className="lh-stack"><Card title="Add knowledge">
            <SegmentedControl<"paste" | "site"> label="How" value={kind} onChange={setKind} options={[{ value: "paste", label: "Paste a page" }, { value: "site", label: "Crawl a site" }]} />
            {kind === "paste" ? (
              <div className="lh-stack"><Field label="Page title" value={title} onChange={setTitle} validate={(v) => (v.trim().length < 2 ? "Give the page a title." : null)} maxLength={200} />
                <Textarea label="Page text" value={body} onChange={(e) => setBody(e.target.value)} hint="Plain text or Markdown. It is split into searchable passages." />
                <div><Button variant="primary" disabled={!canWrite || title.trim().length < 2 || body.trim().length < 20} disabledReason={!canWrite ? "Viewers cannot add pages" : "Give the page a title and at least 20 characters of text"} onClick={async () => { await add.mutateAsync(); }}>Add the page</Button></div></div>
            ) : (
              <div className="lh-stack"><Field label="Site address" value={url} onChange={setUrl} hint="In the demo only the sample site works. On a real install, private and local addresses are always refused, robots.txt is followed, and the crawl is capped." />
                <div><Button variant="primary" disabled={!canWrite} disabledReason="Viewers cannot start a crawl" onClick={async () => { await start.mutateAsync(); }}>Start the crawl</Button></div>
                {crawl ? <div role="status" className="lh-stack"><Badge tone={crawl.status === "done" ? "success" : "danger"}>{crawl.status}</Badge><span className="lh-mini">{crawl.fetched} pages read, {crawl.skipped} skipped</span>{crawl.log.slice(0, 6).map((l) => <span key={l} className="lh-mini">{l}</span>)}</div> : null}</div>
            )}
          </Card>
          <Card title="Knowledge folder" headingLevel={3}>
            <p className="lh-mini">Reads the markdown, text and PDF files in the folder named by KNOWLEDGE_DIR. Press it again after you change a file; unchanged files stay as they are.</p>
            <div><Button disabled={!canWrite} disabledReason="Viewers cannot change knowledge" onClick={async () => { await syncFolder.mutateAsync(); }}>Read the folder now</Button></div>
          </Card></div>) },
      ]} />
    </div>
  );
}
