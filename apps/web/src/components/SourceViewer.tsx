"use client";
import { useEffect, useRef } from "react";

/** The page text with Markdown heading marks turned into bold lines, so "## Short version" reads as a heading. Still plain text, never HTML. */
function Plain({ text }: { text: string }) {
  const lines = text.split("\n");
  return <>{lines.map((l, i) => { const nl = i < lines.length - 1 ? "\n" : ""; return /^#{1,6}\s/.test(l) ? <strong key={i} className="lh-source__h">{l.replace(/^#{1,6}\s+/, "")}{nl}</strong> : <span key={i}>{l}{nl}</span>; })}</>;
}

/** Shows a whole source page with the cited passage highlighted. The page text is plain data, shown as text, never as HTML. */
export function SourceViewer({ title, text, highlight, updatedOn, outdated }: { title: string; text: string; highlight: [number, number] | null; updatedOn: string | null; outdated: boolean }) {
  const ref = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const reduce = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    ref.current?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [highlight?.[0], highlight?.[1], title]);
  const [a, b] = highlight ?? [0, 0];
  return (
    <div className="lh-source" role="region" aria-label={`Source: ${title}`} tabIndex={0}>
      <div className="lh-source__head"><strong>{title}</strong><span className="lh-mini">{updatedOn ? `updated ${updatedOn}` : "no date"}{outdated ? " , over 18 months old" : ""}</span></div>
      <div className="lh-source__body">
        <Plain text={text.slice(0, a)} />
        {b > a ? <mark ref={ref} className="lh-mark">{text.slice(a, b)}</mark> : null}
        <Plain text={text.slice(b)} />
      </div>
    </div>
  );
}
