"use client";
import { useEffect, useState } from "react";
import { Badge } from "@sendora/ui-kit";

export function fmtMinutes(m: number): string {
  const a = Math.abs(m);
  const h = Math.floor(a / 60);
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h > 0 ? `${h}h ${a % 60}m` : `${a}m`;
}

/** A live countdown. The server says how many minutes are left when the list was fetched, and this counts down from there every few seconds. */
export function Sla({ minutes, fetchedAt }: { minutes: number | null; fetchedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);
  if (minutes === null) return <span className="lh-mini">done</span>;
  const left = Math.floor(minutes - (now - fetchedAt) / 60000);
  if (left < 0) return <Badge tone="danger">Late {fmtMinutes(left)}</Badge>;
  return <Badge tone={left < 60 ? "warning" : "neutral"}>{fmtMinutes(left)} left</Badge>;
}
