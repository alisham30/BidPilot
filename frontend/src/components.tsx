import { ReactNode } from "react";

export function Chip({ status }: { status: string }) {
  return <span className={`chip ${status}`}>{status.replaceAll("_", " ")}</span>;
}

export function Severity({ level }: { level: "low" | "medium" | "high" }) {
  const icon = level === "high" ? "▲" : level === "medium" ? "◆" : "●";
  return <span className={`sev ${level}`}>{icon} {level}</span>;
}

export function MatchBar({ pct, threshold = 80 }: { pct: number; threshold?: number }) {
  return (
    <div className="matchbar" title={`Spec match ${pct.toFixed(1)}% (deterministic, reproducible)`}>
      <div className="track">
        <div className={`fill ${pct < threshold ? "low" : ""}`} style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <span className="val">{pct.toFixed(1)}%</span>
    </div>
  );
}

export function Tile({ label, value, alert }: { label: string; value: ReactNode; alert?: boolean }) {
  return (
    <div className={`tile ${alert ? "alert" : ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

export function DaysLeft({ days, unknown }: { days: number | null; unknown: boolean }) {
  if (unknown) return <span className="chip">due date unknown ⚑</span>;
  if (days === null) return null;
  const cls = days <= 3 ? "urgent" : days <= 10 ? "soon" : "";
  return <span className={`pill-days ${cls}`}>{days}d left</span>;
}
