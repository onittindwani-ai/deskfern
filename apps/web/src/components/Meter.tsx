"use client";
import { Gauge } from "@sendora/ui-kit";
import type { AnswerRow } from "@/lib/types";

const PARTS: { key: "retrieval" | "groundedness" | "policy"; label: string; weight: number; how: string }[] = [
  { key: "retrieval", label: "Retrieval strength", weight: 40, how: "How much of what the question is about the best source covers (rare words count more)." },
  { key: "groundedness", label: "Groundedness", weight: 40, how: "Average support score of the sentences that were kept. Each sentence is checked against the cited text." },
  { key: "policy", label: "Policy match", weight: 20, how: "1.0 normally. Lower when a cited page is over 18 months old, and 0 when two pages disagree and nothing says which is current." },
];

export function Meter({ meter }: { meter: AnswerRow["meter"] }) {
  const pct = Math.round(meter.confidence * 100);
  return (
    <div className="lh-meter" aria-label="Confidence meter">
      <Gauge value={meter.confidence} size={110} label="Confidence" display={`${pct}`} />
      <ul className="lh-meter__parts">
        {PARTS.map((p) => (
          <li key={p.key} title={p.how}>
            <div className="lh-meter__row"><span>{p.label} <span className="lh-mini">x {p.weight}%</span></span><strong>{Math.round(meter[p.key] * 100)}</strong></div>
            <div className="lh-meter__bar" role="img" aria-label={`${p.label} ${Math.round(meter[p.key] * 100)} out of 100`}><span style={{ width: `${Math.round(meter[p.key] * 100)}%` }} /></div>
            <p className="lh-mini">{p.how}</p>
          </li>
        ))}
      </ul>
      <p className="lh-mini">Confidence = 0.4 x retrieval + 0.4 x groundedness + 0.2 x policy match. It is a transparent sum, not a probability, and it comes from rules, not a language model.</p>
    </div>
  );
}
