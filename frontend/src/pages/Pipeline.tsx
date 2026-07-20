import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, inr, Rfp, Stats } from "../api";
import { Chip, DaysLeft, Tile } from "../components";

const VERDICT_LABEL: Record<string, string> = {
  proceed: "Ready to bid",
  proceed_with_deviations: "Bid with deviations",
  recommend_no_bid: "Advised: no bid",
};

export default function Pipeline() {
  const nav = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [rfps, setRfps] = useState<Rfp[]>([]);
  const [windowed, setWindowed] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = () => {
    api.stats().then(setStats).catch(() => {});
    api.rfps(windowed ? "3m" : undefined).then(setRfps).catch(() => {});
  };
  useEffect(load, [windowed]);
  useEffect(() => {
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [windowed]);

  const trigger = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try {
      await fn();
      setToast(`${name} finished.`);
      load();
    } catch (e) {
      setToast(`${name} failed: ${e}`);
    } finally {
      setBusy(null);
      setTimeout(() => setToast(null), 5000);
    }
  };

  const queue = stats?.review_queue ?? [];

  return (
    <>
      <h1 className="page-title">Tender desk</h1>
      <p className="page-sub">
        Tenders arrive by email and portal on their own; each one is analyzed automatically.
        Your job starts below — review, decide, submit.
      </p>

      {stats && (
        <div className="tiles">
          <Tile label="Awaiting your decision" value={queue.filter((q) => !q.running).length}
                alert={queue.some((q) => !q.running)} />
          <Tile label="Live tenders in window" value={stats.rfps_in_window} />
          <Tile label="Submitted bids" value={stats.bids_submitted} />
          <Tile label="Open issues" value={stats.open_escalations} alert={stats.open_escalations > 0} />
          <Tile label="Catalog products" value={stats.skus} />
        </div>
      )}

      {/* ---------------- the work queue ---------------- */}
      <div className="card queue-card">
        <h3>Needs your decision {queue.length > 0 && <span className="counter">{queue.length}</span>}</h3>
        {queue.length === 0 && (
          <p className="muted small" style={{ margin: 0 }}>
            Nothing waiting. New tenders are analyzed automatically and will appear here.
          </p>
        )}
        <div className="queue">
          {queue.map((q) => (
            <button key={q.run_id} className="queue-row" onClick={() => nav(`/runs/${q.run_id}`)}>
              <div className="q-main">
                <div className="q-title">{q.title || "(untitled tender)"}</div>
                <div className="q-meta">{q.issuer || "unknown issuer"}
                  {q.due_date && <> · due {q.due_date}</>}{" "}
                  <DaysLeft days={q.days_left} unknown={q.due_date === null} />
                </div>
              </div>
              <div className="q-right">
                {q.running ? (
                  <span className="chip drafting">analyzing…</span>
                ) : (
                  <>
                    <div className="q-amount">{inr(q.grand_total)}</div>
                    <div className="q-flags">
                      {q.verdict && <Chip status={q.verdict} />}
                      <span className="small muted">
                        {q.items_total - q.items_flagged}/{q.items_total} items matched
                      </span>
                    </div>
                  </>
                )}
              </div>
              <div className="q-go">Review →</div>
            </button>
          ))}
        </div>
      </div>

      {/* ---------------- registry ---------------- */}
      <div className="card">
        <div className="btn-row" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>All tenders ({rfps.length})</h3>
          <div className="spacer" />
          <label className="small muted" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={windowed} onChange={(e) => setWindowed(e.target.checked)} />
            due within 92 days
          </label>
          <button disabled={busy !== null} onClick={() => trigger("Inbox scan", api.scanEmail)}>
            {busy === "Inbox scan" ? "Scanning…" : "📬 Scan now"}
          </button>
          <button disabled={busy !== null} onClick={() => trigger("Portal scan", api.scanWeb)}>🌐 Portals</button>
          <button disabled={busy !== null} onClick={() => trigger("Catalog rebuild", api.rebuildCatalog)}>⚙ Catalog</button>
        </div>
        <table>
          <thead>
            <tr><th>Tender</th><th>Issuer</th><th>Ref</th><th>Due</th><th>Status</th></tr>
          </thead>
          <tbody>
            {rfps.map((r) => (
              <tr key={r.rfp_id} className="clickable" onClick={() => nav(`/rfps/${r.rfp_id}`)}>
                <td>{r.title || <span className="muted">(untitled)</span>}</td>
                <td>{r.issuer || "—"}</td>
                <td className="mono small">{r.reference_no || "—"}</td>
                <td>{r.due_date ?? ""} <DaysLeft days={r.days_left} unknown={r.due_unknown} /></td>
                <td><Chip status={r.status} /></td>
              </tr>
            ))}
            {rfps.length === 0 && (
              <tr><td colSpan={5} className="muted">Nothing yet — the inbox is polled every 5 minutes, or press Scan now.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {toast && <div className="toast small">{toast}</div>}
    </>
  );
}
